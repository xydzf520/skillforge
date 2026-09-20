"""动态审批人解析器。

根据 approval_chain 中的步骤定义动态确定审批人。
支持类型：
- static: 直接指定 approver_id（向后兼容）
- skill_owner: 从 SkillMember 查找 role=owner
- org_manager: 从组织树查找指定级别的经理
- role: 按用户角色查找（v2 扩展：role=dept_admin 需带 department）
- dept_admin: role-matrix-v2 §6 指定部门的 dept_admin（不升 system_admin）
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.exceptions import AppError
from app.org.models import OrgUnit, UserOrgMembership
from app.skills.members import SkillMember


def _active_user_filter():
    """role-matrix-v2 §6/§10：审批候选必须是 active 用户。

    同时检查 state 与 is_active：
    - state='active' 排除 pending（钉钉首登 is_active=True 但未激活）与 disabled
    - is_active=True 为双写保险，防止 state 列未迁移的极端环境漏过 disabled 账号
    schema 由 migration 056 + main.verify_role_matrix_schema 启动时校验兜底。
    """
    return (User.state == "active") & (User.is_active == True)  # noqa: E712


async def resolve_approvers(
    db: AsyncSession,
    chain_step: dict,
    context: dict,
) -> list[str]:
    """解析单个审批步骤的审批人 ID 列表。

    chain_step 格式：
    - {"approver_id": "user-1"}  # 静态（向后兼容）
    - {"type": "skill_owner", "skill_id": "EC-001"}
    - {"type": "org_manager", "level": 1}  # 提交人的上级经理
    - {"type": "org_manager", "level": 2}  # 提交人的上上级
    - {"type": "role", "role": "admin"}  # 按角色查找

    context: {"requester_id": "user-1", "skill_id": "EC-001", "org_unit_id": "dept-ec"}

    Returns: 审批人 user_id 列表（至少一个）
    Raises: AppError 如果无法确定审批人
    """
    if not isinstance(chain_step, dict):
        raise AppError("PARAM_INVALID", 400, {"detail": "审批步骤配置格式无效"})

    # 向后兼容：直接指定 approver_id 的静态模式
    if "approver_id" in chain_step and "type" not in chain_step:
        approver_id = chain_step["approver_id"]
        if not approver_id:
            raise AppError("APPROVER_NOT_FOUND", 400, {"detail": "审批步骤缺少 approver_id"})
        return [approver_id]

    step_type = chain_step.get("type", "static")

    if step_type == "static":
        return await _resolve_static(chain_step)
    elif step_type == "skill_owner":
        return await _resolve_skill_owner(db, chain_step, context)
    elif step_type == "org_manager":
        return await _resolve_org_manager(db, chain_step, context)
    elif step_type == "role":
        return await _resolve_role(db, chain_step)
    elif step_type == "dept_admin":
        return await _resolve_dept_admin(db, chain_step, context)
    else:
        raise AppError("PARAM_INVALID", 400, {"detail": f"不支持的审批人类型: {step_type}"})


async def _resolve_static(chain_step: dict) -> list[str]:
    """静态类型：直接指定 approver_id。"""
    approver_id = chain_step.get("approver_id")
    if not approver_id:
        raise AppError("APPROVER_NOT_FOUND", 400, {"detail": "静态审批步骤缺少 approver_id"})
    return [approver_id]


async def _resolve_skill_owner(db: AsyncSession, chain_step: dict, context: dict) -> list[str]:
    """skill_owner 类型：查 SkillMember 找 role=owner 的用户。"""
    # 优先用 chain_step 中指定的 skill_id，否则取 context 中的
    skill_id = chain_step.get("skill_id") or context.get("skill_id")
    if not skill_id:
        raise AppError("PARAM_INVALID", 400, {"detail": "skill_owner 类型需要 skill_id"})

    stmt = (
        select(SkillMember.user_id)
        .join(User, User.id == SkillMember.user_id)
        .where(
            SkillMember.skill_id == skill_id,
            SkillMember.role == "owner",
            _active_user_filter(),
        )
    )
    result = (await db.execute(stmt)).scalars().all()
    if not result:
        raise AppError("APPROVER_NOT_FOUND", 400, {
            "detail": f"Skill {skill_id} 没有 owner 角色的成员",
        })
    return list(result)


async def _resolve_org_manager(db: AsyncSession, chain_step: dict, context: dict) -> list[str]:
    """org_manager 类型：沿组织树向上查找指定级别的经理。

    level=1 表示提交人的直属上级经理，level=2 表示上上级，依此类推。
    如果走到顶层还没到指定级别，返回顶层的 manager。
    """
    level = chain_step.get("level", 1)
    if not isinstance(level, int) or level < 1:
        raise AppError("PARAM_INVALID", 400, {"detail": "org_manager level 必须为正整数"})

    requester_id = context.get("requester_id")
    if not requester_id:
        raise AppError("PARAM_INVALID", 400, {"detail": "org_manager 类型需要 context.requester_id"})

    # 查找提交人所属的组织单元
    membership_stmt = select(UserOrgMembership.org_unit_id).where(
        UserOrgMembership.user_id == requester_id,
    )
    org_unit_ids = (await db.execute(membership_stmt)).scalars().all()
    if not org_unit_ids:
        raise AppError("APPROVER_NOT_FOUND", 400, {
            "detail": f"用户 {requester_id} 未加入任何组织单元",
        })

    # 取第一个组织单元（primary 优先）
    current_org_id = org_unit_ids[0]

    # 沿 parent_id 向上走 level 层
    last_manager_id: str | None = None
    for _ in range(level):
        org_unit = await db.get(OrgUnit, current_org_id)
        if not org_unit:
            break
        if org_unit.manager_user_id:
            last_manager_id = org_unit.manager_user_id
        if not org_unit.parent_id:
            # 已经到顶层，返回当前层的 manager（如果有）
            break
        current_org_id = org_unit.parent_id

    # 最后一次循环后检查目标层的 manager
    if last_manager_id is None:
        # 尝试获取最终到达的组织单元的 manager
        final_org = await db.get(OrgUnit, current_org_id)
        if final_org and final_org.manager_user_id:
            last_manager_id = final_org.manager_user_id

    if not last_manager_id:
        raise AppError("APPROVER_NOT_FOUND", 400, {
            "detail": f"无法找到用户 {requester_id} 的第 {level} 级经理",
        })
    return [last_manager_id]


async def _resolve_role(db: AsyncSession, chain_step: dict) -> list[str]:
    """role 类型：查找指定角色的活跃用户。

    role-matrix-v2 §6.2：role=dept_admin 时必须带 department，避免把所有部门
    的 dept_admin 一股脑返回；其他全局 role 保留旧语义（如 system_admin / admin）。
    """
    role = chain_step.get("role")
    if not role:
        raise AppError("PARAM_INVALID", 400, {"detail": "role 类型需要 role 字段"})

    if role == "dept_admin":
        department = chain_step.get("department")
        if not department:
            raise AppError(
                "PARAM_INVALID",
                400,
                {"detail": "dept_admin 审批需指定 department"},
            )
        stmt = (
            select(User.id)
            .join(UserOrgMembership, UserOrgMembership.user_id == User.id)
            .where(
                User.role == role,
                _active_user_filter(),
                UserOrgMembership.org_unit_id == department,
                UserOrgMembership.is_manager == True,  # noqa: E712
            )
        )
    else:
        stmt = select(User.id).where(
            User.role == role,
            _active_user_filter(),
        )

    result = (await db.execute(stmt)).scalars().all()
    if not result:
        raise AppError("APPROVER_NOT_FOUND", 400, {
            "detail": f"没有找到角色为 {role} 的活跃用户",
        })
    return list(result)


async def _resolve_dept_admin(
    db: AsyncSession,
    chain_step: dict,
    context: dict,
) -> list[str]:
    """dept_admin 类型（role-matrix-v2 §6.2）。

    用法：
        {"type": "dept_admin", "department": "<org_unit_id>"}      # L1
        {"type": "dept_admin", "department": "<org_unit_id>", "level": 2}  # L2 向上
        {"type": "dept_admin"}  # 取 context.skill_department 作为部门

    返回该部门下 is_manager=True 且 state=active 的 dept_admin。
    审批链不再升到 system_admin（§6.1 明确拒绝）。
    """
    level = chain_step.get("level", 1)
    if not isinstance(level, int) or level < 1:
        raise AppError("PARAM_INVALID", 400, {"detail": "dept_admin level 必须为正整数"})

    department = chain_step.get("department") or context.get("skill_department")
    if not department:
        raise AppError(
            "PARAM_INVALID",
            400,
            {"detail": "dept_admin 类型需要 department 或 context.skill_department"},
        )

    current = department
    for _ in range(level - 1):
        org = await db.get(OrgUnit, current)
        if not org or not org.parent_id:
            raise AppError(
                "APPROVER_NOT_FOUND",
                400,
                {"detail": f"部门 {department} 上级 level={level} 超出根部门"},
            )
        current = org.parent_id

    stmt = (
        select(User.id)
        .join(UserOrgMembership, UserOrgMembership.user_id == User.id)
        .where(
            User.role == "dept_admin",
            _active_user_filter(),
            UserOrgMembership.org_unit_id == current,
            UserOrgMembership.is_manager == True,  # noqa: E712
        )
    )
    result = (await db.execute(stmt)).scalars().all()
    if not result:
        raise AppError(
            "APPROVER_NOT_FOUND",
            400,
            {"detail": f"部门 {current} 没有活跃的 dept_admin"},
        )
    return list(result)
