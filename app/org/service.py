"""
组织架构服务：增删改查 + 树形展示 + 移动/级联路径更新。

业务规则：
- path 字段存储物化路径，格式 /{ancestor_id}/.../{self_id}
- 删除节点时子部门可上移到父节点（force=True），否则拒绝
- 移动节点时需检测循环依赖，并级联更新所有子树 path
"""

import uuid
from typing import TYPE_CHECKING
from datetime import datetime

from loguru import logger
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import audit
from app.common.cache import cache_delete_pattern
from app.common.cache_facade import NamespaceCache
from app.common.exceptions import AppError
from app.org.models import OrgUnit, UserOrgMembership
from app.common.time_utils import isoformat_bjt, now_bjt

ORG_TREE_CACHE = NamespaceCache("org:tree", ttl=300)

if TYPE_CHECKING:
    from app.auth.models import User


# ===== 已有函数 =====


async def create_org_unit(
    db: AsyncSession,
    name: str,
    parent_id: str | None = None,
    user_id: str = "system",
    manager_user_id: str | None = None,
    sort_order: int = 0,
    unit_id: str | None = None,
    unit_type: str | None = None,
    dingtalk_dept_id: str | None = None,
) -> dict:
    """创建组织单元。

    - unit_id 为 None 时自动生成；传入时用作 id（便于钉钉同步 / 测试固定 ID）
    - unit_type 默认 "department"
    - 幂等：若 unit_id 对应记录已存在，按传入字段更新而不报错
    """
    org_id = unit_id or f"org-{uuid.uuid4().hex[:8]}"
    effective_type = unit_type or "department"

    existing = await db.get(OrgUnit, org_id) if unit_id else None

    # 构建物化路径
    if parent_id:
        parent = await db.get(OrgUnit, parent_id)
        if not parent:
            raise AppError("NOT_FOUND", 404)
        path = f"{parent.path}/{org_id}"
    else:
        path = f"/{org_id}"

    if existing is not None:
        existing.name = name
        existing.type = effective_type
        existing.parent_id = parent_id
        existing.path = path
        if manager_user_id is not None:
            existing.manager_user_id = manager_user_id
        if dingtalk_dept_id is not None:
            existing.dingtalk_dept_id = dingtalk_dept_id
        existing.sort_order = sort_order
        unit = existing
    else:
        unit = OrgUnit(
            id=org_id,
            name=name,
            type=effective_type,
            parent_id=parent_id,
            path=path,
            manager_user_id=manager_user_id,
            dingtalk_dept_id=dingtalk_dept_id,
            sort_order=sort_order,
        )
        db.add(unit)
    await db.flush()

    # 清缓存 + 审计
    await _invalidate_org_cache(db)
    await audit.log(user_id, "org.create", "org_unit", org_id, {"name": name, "parent_id": parent_id})

    return _unit_to_dict(unit)


# ===== membership 管理 =====


async def list_org_unit_members(
    db: AsyncSession,
    org_unit_id: str,
) -> dict:
    """列出指定组织单元下的成员，附带用户姓名。"""
    from app.auth.models import User
    rows = (await db.execute(
        select(UserOrgMembership, User)
        .join(User, UserOrgMembership.user_id == User.id)
        .where(UserOrgMembership.org_unit_id == org_unit_id)
    )).all()
    items = []
    for m, u in rows:
        d = _membership_to_dict(m)
        d["name"] = u.name
        d["username"] = u.username
        items.append(d)
    return {"items": items}


async def add_membership(
    db: AsyncSession,
    *,
    user_id: str,
    org_unit_id: str,
    membership_type: str = "primary",
    is_manager: bool = False,
    actor_id: str = "svc_org",
    operator: "User | None" = None,
) -> dict:
    """新增 / 更新用户与组织单元的归属关系（幂等）。

    operator 非空时走 v2 权限检查（spec role-matrix-v2 §11）：
    - system_admin / can_view_all 通过
    - dept_admin 必须在自己的 `is_manager=True` 部门集里；否则抛 AUTH_PERMISSION_DENIED
    """
    if operator is not None:
        op_role = getattr(operator, "role", None) or ""
        if op_role not in ("system_admin", "admin") and not bool(getattr(operator, "can_view_all", False)):
            if op_role == "dept_admin":
                from app.auth.access import get_managed_departments
                managed = await get_managed_departments(db, operator)
                if org_unit_id not in managed:
                    raise AppError("AUTH_PERMISSION_DENIED", 403)
            else:
                raise AppError("AUTH_PERMISSION_DENIED", 403)
    from app.auth.access import bump_permissions_rev
    from app.auth.models import User

    user = await db.get(User, user_id)
    org = await db.get(OrgUnit, org_unit_id)
    if user is None or org is None:
        raise AppError("NOT_FOUND", 404)

    existing = (await db.execute(
        select(UserOrgMembership)
        .where(UserOrgMembership.user_id == user_id)
        .where(UserOrgMembership.org_unit_id == org_unit_id)
    )).scalar_one_or_none()
    changed = False
    if existing is not None:
        changed = (
            existing.membership_type != membership_type
            or bool(existing.is_manager) != bool(is_manager)
        )
        if changed:
            existing.membership_type = membership_type
            existing.is_manager = is_manager
        member = existing
    else:
        member = UserOrgMembership(
            user_id=user_id,
            org_unit_id=org_unit_id,
            membership_type=membership_type,
            is_manager=is_manager,
        )
        db.add(member)
        changed = True
    await db.flush()
    if changed:
        if membership_type == "primary":
            user.department = org.name or None
        await bump_permissions_rev(db, user_id)
        await _invalidate_org_cache(db)
        await audit.log(actor_id, "org.membership.add", "org_unit", org_unit_id, {
            "user_id": user_id, "membership_type": membership_type, "is_manager": is_manager,
        })
    return _membership_to_dict(member)


async def remove_membership(
    db: AsyncSession,
    *,
    user_id: str,
    org_unit_id: str,
    actor_id: str = "system",
) -> dict:
    from app.auth.access import bump_permissions_rev
    from app.auth.models import User

    membership = (
        await db.execute(
            select(UserOrgMembership)
            .where(UserOrgMembership.user_id == user_id)
            .where(UserOrgMembership.org_unit_id == org_unit_id)
        )
    ).scalar_one_or_none()
    deleted = membership is not None
    if membership is not None:
        await db.delete(membership)
    await db.flush()
    if deleted:
        user = await db.get(User, user_id)
        if user is not None and membership.membership_type == "primary":
            replacement = (
                await db.execute(
                    select(UserOrgMembership, OrgUnit)
                    .join(OrgUnit, UserOrgMembership.org_unit_id == OrgUnit.id)
                    .where(UserOrgMembership.user_id == user_id)
                    .order_by(
                        UserOrgMembership.membership_type.asc(),
                        OrgUnit.sort_order.asc(),
                        OrgUnit.id.asc(),
                    )
                    .limit(1)
                )
            ).first()
            user.department = replacement[1].name if replacement else None
        await bump_permissions_rev(db, user_id)
        await _invalidate_org_cache(db)
        await audit.log(actor_id, "org.membership.remove", "org_unit", org_unit_id, {"user_id": user_id})
    return {"deleted": deleted}


# ===== 组织合并 =====


async def merge_org(
    db: AsyncSession,
    *,
    source_id: str,
    target_id: str,
    operator: "User | None",
) -> dict:
    """将 source 部门合并进 target 部门（spec role-matrix-v2 §11）。

    - 权限：仅 system_admin 可执行；dept_admin / aibp / observer 抛 AUTH_PERMISSION_DENIED
    - 迁移 UserOrgMembership：同 user 已在 target 则保留 target 并合并 is_manager=OR
    - 迁移 Skill.org_unit_id + Skill.department（展示名）
    - 迁移 User.department 展示名（源部门的关联用户）
    - 迁移 OpenClawInstance.department 展示名
    - bump 受影响用户的 permissions_rev
    - 删除 source OrgUnit
    - 返回受影响统计
    """
    from app.auth.access import bump_permissions_rev
    from app.execution.models import OpenClawInstance
    from app.skills.core.models import Skill

    op_role = getattr(operator, "role", None) or "" if operator else ""
    if op_role not in ("system_admin", "admin"):
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    if source_id == target_id:
        raise AppError("ORG_MERGE_SELF", 400)

    source_org = await db.get(OrgUnit, source_id)
    target_org = await db.get(OrgUnit, target_id)
    if source_org is None or target_org is None:
        raise AppError("ORG_UNIT_NOT_FOUND", 404)

    target_display = target_org.name or ""

    # 1) 先收集源部门下所有 membership，计算受影响用户集合
    source_memberships = (
        await db.execute(
            select(UserOrgMembership).where(UserOrgMembership.org_unit_id == source_id)
        )
    ).scalars().all()
    affected_user_ids: set[str] = {m.user_id for m in source_memberships}

    # 2) 处理 membership 迁移 / 去重
    for member in source_memberships:
        existing = (
            await db.execute(
                select(UserOrgMembership)
                .where(UserOrgMembership.user_id == member.user_id)
                .where(UserOrgMembership.org_unit_id == target_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            # 去重：合并 is_manager（OR）；membership_type 保留 target 现有值
            if member.is_manager:
                existing.is_manager = True
            await db.delete(member)
        else:
            # 直接改 FK
            member.org_unit_id = target_id
    await db.flush()

    # 3) 迁移 Skill
    skill_rows = (
        await db.execute(select(Skill).where(Skill.org_unit_id == source_id))
    ).scalars().all()
    affected_skills = 0
    for sk in skill_rows:
        sk.org_unit_id = target_id
        sk.department = target_display
        affected_skills += 1

    # 4) User.department 展示名迁移（关联用户）
    if affected_user_ids:
        from app.auth.models import User as _User
        from sqlalchemy import update as _sa_update
        await db.execute(
            _sa_update(_User)
            .where(_User.id.in_(list(affected_user_ids)))
            .values(department=target_display, updated_at=now_bjt())
        )

    # 5) OpenClawInstance.department 展示名迁移（仅展示字段，没有 FK）
    source_display = source_org.name or ""
    if source_display and source_display != target_display:
        await db.execute(
            text(
                "UPDATE openclaw_instances SET department = :new WHERE department = :old"
            ),
            {"new": target_display, "old": source_display},
        )

    # 6) bump 受影响用户的 permissions_rev
    # 单个用户 bump 失败不应阻断整个合并流程（其他用户仍需写入），但需记录便于排查
    for uid in affected_user_ids:
        try:
            await bump_permissions_rev(db, uid)
        except Exception as exc:
            logger.debug(f"合并组织时 bump permissions_rev 失败，跳过 user={uid}: {exc}")

    # 7) 删除 source OrgUnit
    await db.delete(source_org)
    await db.flush()

    await _invalidate_org_cache(db)
    # 收件箱权限缓存为可选优化，缺失不阻塞合并主流程；下一次缓存自然过期即可
    try:
        from app.common.cache import invalidate_inbox_permissions
        await invalidate_inbox_permissions()
    except Exception as exc:
        logger.debug(f"忽略异常: 失效收件箱权限缓存失败，等待自然过期: {exc}")

    actor_id = getattr(operator, "id", None) or "svc_org"
    await audit.log(
        actor_id, "org.merge", "org_unit", source_id,
        {"target_id": target_id, "affected_users": len(affected_user_ids),
         "affected_skills": affected_skills},
    )

    return {
        "source_id": source_id,
        "target_id": target_id,
        "affected_users": len(affected_user_ids),
        "affected_skills": affected_skills,
        "affected_playbooks": 0,  # Playbook 无 DB 模型（YAML 存储），由文件同步工具处理
        "affected_todos": 0,
    }


# ===== 钉钉同步 =====


SYNC_BATCH_COMMIT_SIZE = 50


async def sync_dingtalk_org(
    db: AsyncSession,
    *,
    actor_id: str = "system",
) -> dict:
    """从钉钉同步组织架构 + 用户归属。

    - 配置了 DINGTALK_APP_KEY / SECRET 时调用 dingtalk_client API
    - 未配置时走 fallback：根据 users.department 建扁平 OrgUnit 并补 membership

    分批提交：每 SYNC_BATCH_COMMIT_SIZE 次 create/membership 操作后 commit 一次，
    避免几百部门单事务导致 PG 锁超时 / 失败整盘回滚。commit 失败进 errors 列表
    但继续执行。
    """
    from app.auth.models import User
    from app.config import settings
    from app.dingtalk.client import dingtalk_client

    synced_units = 0
    synced_users = 0
    errors: list[str] = []
    _pending = 0

    async def _maybe_commit() -> None:
        nonlocal _pending
        if _pending >= SYNC_BATCH_COMMIT_SIZE:
            try:
                await db.commit()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"batch_commit: {exc}")
                await db.rollback()
            _pending = 0

    if settings.DINGTALK_APP_KEY and settings.DINGTALK_APP_SECRET:
        root_dept_id = settings.DINGTALK_ROOT_DEPT_ID or "1"
        try:
            tree = await dingtalk_client.get_department_tree(root_dept_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"get_department_tree: {exc}")
            tree = {"ok": False, "data": []}
        if not tree.get("ok"):
            # 失败时 data 字段可能不存在或是错误描述字符串；不继续走 _walk 避免二次异常
            err_payload = tree.get("data") or {}
            err_code = err_payload.get("errcode") if isinstance(err_payload, dict) else None
            hint = ""
            if err_code == 50004:
                hint = (
                    f"（dept_id={root_dept_id} 不在应用授权范围。解决：要么在钉钉后台"
                    f"把应用『通讯录可见范围』改为全部员工；要么在 .env 设置 "
                    f"`DINGTALK_ROOT_DEPT_ID=<应用实际授权的部门id>`）"
                )
            errors.append(
                f"钉钉部门树拉取失败 error={tree.get('error')!r} "
                f"status={tree.get('status_code')!r}{hint}"
            )
            logger.error("org.sync_dingtalk 部门树拉取失败: {}", tree)
            return {"synced_units": synced_units, "synced_users": synced_users, "errors": errors}

        async def _resolve_or_create_user(
            dingtalk_userid: str,
            item: dict,
            dept_name: str,
        ) -> User | None:
            """按钉钉 userid 查 User；查不到则自动创建（与 dingtalk_oauth 一致的 id 规则）。

            命名规则（对齐 `app/auth/dingtalk_oauth.py`）：
            - `users.id` = `dt_{钉钉userid}`，扫码登录与组织同步共用同一条记录
            - `users.username` = `dingtalk_{钉钉userid}`
            - `role` 默认 `aibp`、`state=active`，扫码后可直接使用能力大厅
            - 无密码，只能钉钉扫码登录
            """
            union_id = str(item.get("union_id") or item.get("unionid") or "").strip()

            # 按钉钉 userid 查找用户；OAuth 扫码创建的用户 id 可能是纯数字而非 dt_ 前缀
            stmt = select(User).where(User.dingtalk_user_id == dingtalk_userid)
            user = (await db.execute(stmt)).scalar_one_or_none()
            resolved_by_union = False
            if user is None and union_id:
                union_candidates = list(
                    (
                        await db.execute(
                            select(User)
                            .where(User.dingtalk_union_id == union_id)
                            .order_by(User.id.asc())
                            .limit(2)
                        )
                    ).scalars().all()
                )
                if len(union_candidates) > 1:
                    errors.append(f"resolve_user[{dingtalk_userid}]: duplicate_union_id")
                    return None
                if union_candidates:
                    user = union_candidates[0]
                    resolved_by_union = True
            if user is None:
                # 再按 id=纯数字或 id=dt_{userid} 兜底查一次（历史数据 dingtalk_user_id 可能未填）
                user = await db.get(User, dingtalk_userid)
                if user is None:
                    user = await db.get(User, f"dt_{dingtalk_userid}")
            if user is None:
                # savepoint 隔离：User 创建失败（unique 冲突等）不影响外层 batch 已做的部门
                try:
                    async with db.begin_nested():
                        user = User(
                            id=f"dt_{dingtalk_userid}",
                            username=f"dingtalk_{dingtalk_userid}",
                            name=item.get("name") or dingtalk_userid,
                            role="aibp",
                            state="active",
                            department=dept_name or None,
                            dingtalk_user_id=dingtalk_userid,
                            dingtalk_union_id=union_id or None,
                            email=item.get("email") or None,
                            phone=item.get("mobile") or None,
                            avatar_url=item.get("avatar") or None,
                            password_hash=None,
                            must_change_password=False,
                            is_active=True,
                        )
                        db.add(user)
                        await db.flush()
                except IntegrityError:
                    # 并发 OAuth 登录 / 并发同步导致的唯一键冲突 —— 用户其实已经存在，
                    # savepoint 回滚后重新查一次，继续走后续 add_membership，避免"用户存在、
                    # 部门关系缺失"的部分成功。
                    user = await db.get(User, f"dt_{dingtalk_userid}")
                    if user is None:
                        user = (await db.execute(
                            select(User).where(User.dingtalk_user_id == dingtalk_userid)
                        )).scalar_one_or_none()
                    if user is None:
                        errors.append(f"create_user[{dingtalk_userid}]: integrity_error_no_user")
                        return None
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"create_user[{dingtalk_userid}]: {exc}")
                    return None
            else:
                # 幂等更新：只改 department/name/联系方式，不覆盖 role 等敏感字段
                user.department = dept_name or user.department
                if item.get("name"):
                    user.name = item.get("name")
                if item.get("email"):
                    user.email = item.get("email")
                if item.get("mobile"):
                    user.phone = item.get("mobile")
                if item.get("avatar"):
                    user.avatar_url = item.get("avatar")
                # 确保 dingtalk_user_id 反向绑定（老数据可能没填）
                if resolved_by_union or not user.dingtalk_user_id:
                    user.dingtalk_user_id = dingtalk_userid
                if union_id and not user.dingtalk_union_id:
                    user.dingtalk_union_id = union_id
            return user

        async def _walk(nodes: list[dict], parent_id: str | None) -> None:
            nonlocal synced_units, synced_users, _pending
            for node in nodes or []:
                if not isinstance(node, dict):
                    errors.append(f"unexpected_node_type: {type(node).__name__} parent={parent_id}")
                    continue
                dept_id = str(node.get("dept_id") or node.get("id") or "").strip()
                if not dept_id:
                    continue
                await create_org_unit(
                    db,
                    unit_id=dept_id,
                    name=node.get("name") or dept_id,
                    parent_id=parent_id,
                    unit_type="department",
                    dingtalk_dept_id=dept_id,
                    sort_order=int(node.get("order", 0) or 0),
                    user_id=actor_id,
                )
                synced_units += 1
                _pending += 1
                await _maybe_commit()
                try:
                    users_resp = await dingtalk_client.list_department_users(dept_id)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"list_department_users[{dept_id}]: {exc}")
                    users_resp = {"ok": False, "data": []}
                for item in (users_resp.get("data") if users_resp.get("ok") else []) or []:
                    dingtalk_userid = item.get("user_id") or item.get("userid")
                    if not dingtalk_userid:
                        continue
                    dingtalk_userid = str(dingtalk_userid)
                    db_user = await _resolve_or_create_user(
                        dingtalk_userid, item, node.get("name") or ""
                    )
                    if db_user is None:
                        continue  # 创建失败已记 errors
                    await add_membership(
                        db,
                        user_id=db_user.id,  # SkillForge 内部 id（dt_{钉钉userid}），非钉钉原始 userid
                        org_unit_id=dept_id,
                        membership_type="primary",
                        is_manager=bool(item.get("is_manager")),
                        actor_id=actor_id,
                    )
                    synced_users += 1
                    _pending += 1
                    await _maybe_commit()
                await _walk(node.get("children") or [], parent_id=dept_id)

        await _walk(tree.get("data") or [], parent_id=None)
    else:
        # fallback：按 users.department 去重建 OrgUnit + 成员关系
        user_rows = (await db.execute(
            select(User.id, User.department).where(User.department.is_not(None))
        )).all()
        by_dept: dict[str, list[str]] = {}
        for user_id_val, dept in user_rows:
            if not dept:
                continue
            by_dept.setdefault(dept, []).append(user_id_val)
        for dept, user_ids in by_dept.items():
            await create_org_unit(
                db,
                unit_id=dept,  # 部门名作为 id，与钉钉 dept_id 语义一致
                name=dept,
                parent_id=None,
                unit_type="department",
                user_id=actor_id,
            )
            synced_units += 1
            _pending += 1
            await _maybe_commit()
            for uid in user_ids:
                await add_membership(
                    db,
                    user_id=uid,
                    org_unit_id=dept,
                    membership_type="primary",
                    is_manager=False,
                    actor_id=actor_id,
                )
                synced_users += 1
                _pending += 1
                await _maybe_commit()

    await db.flush()
    await _invalidate_org_cache(db)
    return {"synced_units": synced_units, "synced_users": synced_users, "errors": errors}


async def get_org_tree(db: AsyncSession) -> list[dict]:
    """获取完整组织树（递归构建），返回嵌套 children 结构"""
    cached = await ORG_TREE_CACHE.get("default")
    if cached is not None:
        return cached

    result = await db.execute(
        select(OrgUnit).order_by(OrgUnit.sort_order)
    )
    units = result.scalars().all()

    # 一次性统计每个部门的成员数
    count_result = await db.execute(
        select(
            UserOrgMembership.org_unit_id,
            func.count(UserOrgMembership.user_id),
        ).group_by(UserOrgMembership.org_unit_id)
    )
    member_counts: dict[str, int] = dict(count_result.all())

    # 构建 id -> node 映射
    node_map: dict[str, dict] = {}
    for u in units:
        node_map[u.id] = {
            **_unit_to_dict(u),
            "member_count": member_counts.get(u.id, 0),
            "children": [],
        }

    # 组装树
    roots: list[dict] = []
    for u in units:
        node = node_map[u.id]
        if u.parent_id and u.parent_id in node_map:
            node_map[u.parent_id]["children"].append(node)
        else:
            roots.append(node)

    # 递归累加子部门成员数
    def _accumulate_total(node: dict) -> int:
        total = node.get("member_count", 0) or 0
        for child in node.get("children", []):
            total += _accumulate_total(child)
        node["total_member_count"] = total
        return total

    for root in roots:
        _accumulate_total(root)

    await ORG_TREE_CACHE.set("default", value=roots)
    return roots


# ===== 新增：删除 =====


async def delete_org_unit(
    db: AsyncSession,
    org_unit_id: str,
    user_id: str,
    force: bool = False,
) -> dict:
    """删除组织单元。

    - force=False 且有子部门 → 拒绝
    - force=True → 子部门上移到被删节点的父节点，membership 迁移到父节点
    """
    unit = await db.get(OrgUnit, org_unit_id)
    if not unit:
        raise AppError("NOT_FOUND", 404)

    # 查找直接子部门
    children_result = await db.execute(
        select(OrgUnit).where(OrgUnit.parent_id == org_unit_id)
    )
    children = children_result.scalars().all()

    if children and not force:
        raise AppError("ORG_HAS_CHILDREN", 400)

    parent_id = unit.parent_id

    if children and force:
        # 计算父节点的 path（子部门上移后的新前缀）
        if parent_id:
            parent = await db.get(OrgUnit, parent_id)
            parent_path = parent.path if parent else ""
        else:
            parent_path = ""

        for child in children:
            old_child_path = child.path
            # 子部门新 path：parent_path + /child_id
            new_child_path = f"{parent_path}/{child.id}" if parent_path else f"/{child.id}"

            child.parent_id = parent_id
            child.path = new_child_path

            # 级联更新子部门的所有后代 path
            await _cascade_update_paths(db, old_child_path, new_child_path)

    # 迁移 membership 到父节点（复合主键，需删除旧记录+插入新记录）
    memberships_result = await db.execute(
        select(UserOrgMembership).where(UserOrgMembership.org_unit_id == org_unit_id)
    )
    memberships = memberships_result.scalars().all()

    if parent_id and memberships:
        for m in memberships:
            # 插入新的 membership（指向父节点），然后删除旧的
            new_membership = UserOrgMembership(
                user_id=m.user_id,
                org_unit_id=parent_id,
                membership_type=m.membership_type,
                is_manager=m.is_manager,
                joined_at=m.joined_at,
            )
            await db.delete(m)
            await db.flush()  # 先删除，避免主键冲突
            # 检查是否已存在（用户可能已属于父节点）
            existing = await db.execute(
                select(UserOrgMembership).where(
                    UserOrgMembership.user_id == new_membership.user_id,
                    UserOrgMembership.org_unit_id == parent_id,
                )
            )
            if not existing.scalars().first():
                db.add(new_membership)
    else:
        # 没有父节点 → 直接删除 membership
        for m in memberships:
            await db.delete(m)

    # 删除组织单元
    await db.delete(unit)
    await db.flush()

    # 清缓存 + 审计
    await _invalidate_org_cache(db)
    await audit.log(
        user_id, "org.delete", "org_unit", org_unit_id,
        {"name": unit.name, "force": force, "children_count": len(children)},
    )

    return {"ok": True, "deleted_id": org_unit_id, "children_moved": len(children)}


# ===== 新增：移动 =====


async def move_org_unit(
    db: AsyncSession,
    org_unit_id: str,
    new_parent_id: str | None,
    user_id: str,
) -> dict:
    """移动组织单元到新的父节点。

    - 循环依赖检测：new_parent 不能是 org_unit 的子孙
    - 级联更新所有子树的 path
    """
    unit = await db.get(OrgUnit, org_unit_id)
    if not unit:
        raise AppError("NOT_FOUND", 404)

    # 不能移动到自身
    if new_parent_id == org_unit_id:
        raise AppError("PARAM_INVALID", 400)

    # 验证新父节点存在 + 循环依赖检测
    new_parent_path = ""
    if new_parent_id:
        new_parent = await db.get(OrgUnit, new_parent_id)
        if not new_parent:
            raise AppError("NOT_FOUND", 404)

        # 循环检测：从 new_parent 沿 parent_id 向上遍历，如果经过 org_unit_id 则拒绝
        cursor_id = new_parent_id
        visited: set[str] = set()
        while cursor_id:
            if cursor_id == org_unit_id:
                raise AppError("PARAM_INVALID", 400)
            if cursor_id in visited:
                break  # 数据异常防护，避免死循环
            visited.add(cursor_id)
            ancestor = await db.get(OrgUnit, cursor_id)
            cursor_id = ancestor.parent_id if ancestor else None

        new_parent_path = new_parent.path

    old_path = unit.path
    new_path = f"{new_parent_path}/{org_unit_id}" if new_parent_path else f"/{org_unit_id}"
    old_parent_id = unit.parent_id

    # 更新节点本身
    unit.parent_id = new_parent_id
    unit.path = new_path

    # 级联更新所有后代 path
    await _cascade_update_paths(db, old_path, new_path)

    await db.flush()

    # 清缓存 + 审计
    await _invalidate_org_cache(db)
    await audit.log(
        user_id, "org.move", "org_unit", org_unit_id,
        {
            "name": unit.name,
            "old_parent_id": old_parent_id,
            "new_parent_id": new_parent_id,
            "old_path": old_path,
            "new_path": new_path,
        },
    )

    return _unit_to_dict(unit)


# ===== 内部辅助 =====


async def _cascade_update_paths(
    db: AsyncSession,
    old_path: str,
    new_path: str,
) -> None:
    """级联更新所有后代的 path 前缀。

    使用原生 SQL 的 replace() 批量更新，效率远高于逐行查询修改。
    只更新 path 以 old_path/ 开头的后代行（不含节点自身，自身已在调用方更新）。
    """
    if old_path == new_path:
        return

    # old_path + '/' 确保只匹配后代，不误匹配同名前缀的兄弟节点
    await db.execute(
        text(
            "UPDATE org_units "
            "SET path = :new_path || substr(path, length(:old_path) + 1), "
            "    updated_at = NOW() "
            "WHERE path LIKE :pattern AND path != :old_path"
        ),
        {
            "old_path": old_path,
            "new_path": new_path,
            "pattern": f"{old_path}/%",
        },
    )


async def _invalidate_org_cache(db: AsyncSession | None = None) -> None:
    """清除组织架构相关缓存。

    v2.0.15 C3+H2：
    - 在传入的 `db` session 内执行 `UPDATE system_meta SET version = version + 1`，
      与主业务写入绑定同一事务——commit 成功则版本号上升，所有 worker 下次读缓存
      时感知到 version 变化、强制 miss；rollback 则版本号不涨，避免"DB 没变但
      其它 worker 误以为变了"的短窗口
    - 本进程的 Redis / tasktree / org_indexes 本地缓存清空是"最好努力"——即使
      回滚后清了也只是多一次回源查询，不会读到错数据
    - 为兼容旧调用方（无 db 参数）保留无参形式，此时仅清本进程缓存，不 bump 版本
    """
    try:
        if db is not None:
            from app.common.system_meta import ORG_UNITS_CACHE_KEY, bump_cache_version

            await bump_cache_version(db, ORG_UNITS_CACHE_KEY)
    except Exception as exc:
        logger.debug(f"忽略异常: 组织架构版本号 bump 失败: {exc}")

    try:
        await cache_delete_pattern("org:*")
        from app.tasktree.service import invalidate_tasktree, invalidate_tasktree_org_indexes

        await invalidate_tasktree(None)
        # H13：org_indexes 是进程级 60s cache，部门变更必须显式清，否则读旧索引
        await invalidate_tasktree_org_indexes()
    except Exception as exc:
        # 缓存失效不阻塞业务（Redis 抖动 / 不可用时下游会自然 miss 后回源）
        logger.debug(f"忽略异常: 组织架构缓存失效失败: {exc}")


def _unit_to_dict(unit: OrgUnit) -> dict:
    """OrgUnit 转字典"""
    return {
        "id": unit.id,
        "name": unit.name,
        "type": unit.type,
        "parent_id": unit.parent_id,
        "path": unit.path,
        "dingtalk_dept_id": unit.dingtalk_dept_id,
        "manager_user_id": unit.manager_user_id,
        "sort_order": unit.sort_order,
        "created_at": isoformat_bjt(unit.created_at),
        "updated_at": isoformat_bjt(unit.updated_at),
    }


def _membership_to_dict(m: UserOrgMembership) -> dict:
    return {
        "user_id": m.user_id,
        "org_unit_id": m.org_unit_id,
        "membership_type": m.membership_type,
        "is_manager": bool(m.is_manager),
        "joined_at": isoformat_bjt(m.joined_at),
    }
