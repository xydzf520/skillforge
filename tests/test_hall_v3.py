"""大厅 v3 · 阶段 1+2 集成测试。

覆盖：
- 数据源 visibility 三级过滤（company / department / private）
- admin / can_view_all 豁免
- Grant 过期后隐藏
- visibility 提权权限
- 申请访问流（reason minlen / 幂等 / approve / reject / owner 权限 / admin 跨审 / expire cron）
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.time_utils import now_bjt
from app.datasources import service
from app.datasources.hall_service import (
    get_data_hall_detail,
    list_data_hall,
)
from app.datasources.models import DataAccessGrant, DataAccessRequest, DataSource


def _mk_user(id="u1", dept="营销部", role="operator", can_view_all=False):
    u = MagicMock(spec=User)
    u.id = id
    u.department = dept
    u.role = role
    u.can_view_all = can_view_all
    u.is_active = True
    u.dingtalk_user_id = None
    return u


async def _mk_source(
    db: AsyncSession,
    *,
    id="ds-1",
    name="测试数据源",
    department="营销部",
    visibility="department",
    owner_contact="owner-1",
    is_active=True,
    related_skills=None,
):
    src = DataSource(
        id=id,
        name=name,
        department=department,
        source_type="csv_upload",
        config={},
        is_active=is_active,
        visibility=visibility,
        owner_contact=owner_contact,
        created_by=owner_contact,
        related_skills=related_skills or [],
    )
    db.add(src)
    await db.commit()
    await db.refresh(src)
    return src


async def _mk_grant(
    db: AsyncSession,
    *,
    source_id="ds-1",
    grantee_user_id="u1",
    expires_at=None,
):
    g = DataAccessGrant(
        source_id=source_id,
        grantee_user_id=grantee_user_id,
        permission="read",
        granted_by="admin",
        expires_at=expires_at,
    )
    db.add(g)
    await db.commit()
    return g


@pytest_asyncio.fixture
async def db(client):
    """复用 client fixture 的 DB 引擎，产出一个独立 session。"""
    import app.database as db_mod

    async with db_mod.async_session_factory() as s:
        yield s


# ═══════════════════════════════════════════════════════════
# 阶段 1 · Visibility 过滤（10 case）
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_company_visible_to_all(db):
    """visibility=company: 任何部门用户可见。"""
    await _mk_source(db, id="ds-pub", visibility="company", department="客服部", owner_contact="owner-1")
    user = _mk_user(id="u-marketing", dept="营销部")  # 不同部门
    r = await list_data_hall(db, user)
    assert any(i["id"] == "ds-pub" for i in r["items"])


@pytest.mark.asyncio
async def test_department_same_dept_visible(db):
    """visibility=department: 同部门可见。"""
    await _mk_source(db, id="ds-dept", visibility="department", department="营销部", owner_contact="owner-2")
    user = _mk_user(id="u-m1", dept="营销部")
    r = await list_data_hall(db, user)
    assert any(i["id"] == "ds-dept" for i in r["items"])


@pytest.mark.asyncio
async def test_department_cross_dept_hidden(db):
    """visibility=department: 跨部门隐藏。"""
    await _mk_source(db, id="ds-dept2", visibility="department", department="客服部", owner_contact="owner-3")
    user = _mk_user(id="u-m2", dept="营销部")
    r = await list_data_hall(db, user)
    assert not any(i["id"] == "ds-dept2" for i in r["items"])


@pytest.mark.asyncio
async def test_private_no_grant_hidden(db):
    """visibility=private 无 grant: 不可见。"""
    await _mk_source(db, id="ds-priv", visibility="private", department="客服部", owner_contact="owner-4")
    user = _mk_user(id="u-m3", dept="营销部")
    r = await list_data_hall(db, user)
    assert not any(i["id"] == "ds-priv" for i in r["items"])


@pytest.mark.asyncio
async def test_private_with_grant_visible(db):
    """visibility=private 有有效 grant: 可见 + my_access=granted。"""
    await _mk_source(db, id="ds-priv2", visibility="private", department="客服部", owner_contact="owner-5")
    await _mk_grant(db, source_id="ds-priv2", grantee_user_id="u-granted")
    user = _mk_user(id="u-granted", dept="营销部")
    r = await list_data_hall(db, user)
    item = next((i for i in r["items"] if i["id"] == "ds-priv2"), None)
    assert item is not None
    assert item["my_access"]["status"] == "granted"


@pytest.mark.asyncio
async def test_admin_sees_all(db):
    """admin 豁免，所有 visibility 都可见。"""
    await _mk_source(db, id="ds-admin-priv", visibility="private", department="客服部", owner_contact="o")
    admin = _mk_user(id="admin", role="admin", can_view_all=True, dept="AI 小组")
    r = await list_data_hall(db, admin)
    assert any(i["id"] == "ds-admin-priv" for i in r["items"])


@pytest.mark.asyncio
async def test_can_view_all_exempt(db):
    """can_view_all=True 的非 admin 用户也豁免。"""
    await _mk_source(db, id="ds-cva", visibility="department", department="客服部", owner_contact="o")
    user = _mk_user(id="u-director", role="director", can_view_all=True, dept="总经办")
    r = await list_data_hall(db, user)
    assert any(i["id"] == "ds-cva" for i in r["items"])


@pytest.mark.asyncio
async def test_expired_grant_hidden(db):
    """已过期 grant 视为无权限。"""
    past = now_bjt() - timedelta(days=1)
    await _mk_source(db, id="ds-exp", visibility="private", department="客服部", owner_contact="o")
    await _mk_grant(db, source_id="ds-exp", grantee_user_id="u-exp", expires_at=past)
    user = _mk_user(id="u-exp", dept="营销部")
    r = await list_data_hall(db, user)
    assert not any(i["id"] == "ds-exp" for i in r["items"])


@pytest.mark.asyncio
async def test_owner_sees_own_private(db):
    """owner_contact 本人可见自己的 private 数据源（即使 visibility=private）。"""
    await _mk_source(db, id="ds-owner", visibility="private", department="客服部", owner_contact="u-owner")
    user = _mk_user(id="u-owner", dept="营销部")
    r = await list_data_hall(db, user)
    assert any(i["id"] == "ds-owner" for i in r["items"])


@pytest.mark.asyncio
async def test_detail_not_found_for_private_no_grant(db):
    """private 数据源对无权限用户返回 404（不泄漏存在性）。"""
    from app.common.exceptions import AppError

    await _mk_source(db, id="ds-p404", visibility="private", department="客服部", owner_contact="o")
    user = _mk_user(id="u-other", dept="营销部")
    with pytest.raises(AppError) as exc_info:
        await get_data_hall_detail(db, user, "ds-p404")
    assert exc_info.value.code == "DATASOURCE_NOT_FOUND"


# ═══════════════════════════════════════════════════════════
# Visibility 提权权限（2 case）
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_operator_cannot_promote_to_company(db):
    """普通 owner 不能把 visibility 从 department 改到 company。"""
    from app.common.exceptions import AppError

    await _mk_source(db, id="ds-promote", visibility="department", department="营销部", owner_contact="u-owner")
    with pytest.raises(AppError) as exc_info:
        await service.update_source(
            db,
            source_id="ds-promote",
            user_id="u-owner",
            user_role="operator",
            visibility="company",
        )
    assert exc_info.value.code == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_dept_admin_can_promote_to_company(db):
    """dept_admin 可以把 visibility 提升到 company。"""
    await _mk_source(db, id="ds-promote2", visibility="department", department="营销部", owner_contact="u-owner")
    r = await service.update_source(
        db,
        source_id="ds-promote2",
        user_id="dept-admin",
        user_role="dept_admin",
        visibility="company",
    )
    assert "visibility" in r["updated"]
    src = (await db.execute(
        __import__("sqlalchemy").select(DataSource).where(DataSource.id == "ds-promote2")
    )).scalar_one()
    assert src.visibility == "company"


# ═══════════════════════════════════════════════════════════
# 阶段 2 · 申请访问流（10 case）
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_request_access_creates_pending(db):
    """申请访问：写 pending 记录 + 推送 outbox。"""
    await _mk_source(db, id="ds-req", visibility="company", department="客服部", owner_contact="u-owner")
    with patch("app.dingtalk.outbox.outbox.enqueue") as mock_enq:
        mock_enq.return_value = 999
        # mock user.dingtalk_user_id 查询：必须有 User 表里的 owner 记录
        from app.auth.models import User as UserModel

        owner = UserModel(id="u-owner", username="u-owner", name="张三", role="operator", department="客服部", dingtalk_user_id="dt-owner", password_hash="", is_active=True, state="active")
        db.add(owner)
        await db.commit()

        r = await service.request_data_access(
            db,
            source_id="ds-req",
            user_id="u-apply",
            user_department="营销部",
            reason="需要客服对话数据做质检分析，覆盖投诉定性 / 情绪识别 / 升级触发三个场景。",
        )
        assert r["status"] == "pending"
        assert r["id"] > 0
        mock_enq.assert_called_once()
        kwargs = mock_enq.call_args.kwargs
        assert kwargs["message_type"] == "work_notice"
        assert kwargs["payload"]["kind"] == "data_access_request"


@pytest.mark.asyncio
async def test_request_access_reason_too_short(db):
    """reason 少于 20 字被拒。"""
    from app.common.exceptions import AppError

    await _mk_source(db, id="ds-r-short", visibility="company", department="客服部", owner_contact="o")
    with pytest.raises(AppError) as exc:
        await service.request_data_access(
            db, source_id="ds-r-short", user_id="u", reason="测试"
        )
    assert exc.value.code == "REASON_TOO_SHORT"


@pytest.mark.asyncio
async def test_request_access_idempotent(db):
    """同 user × source 已有 pending 直接返回现有 id。"""
    await _mk_source(db, id="ds-idem", visibility="company", department="客服部", owner_contact=None)
    with patch("app.dingtalk.outbox.outbox.enqueue") as mock_enq:
        mock_enq.return_value = 1
        r1 = await service.request_data_access(
            db, source_id="ds-idem", user_id="u-x",
            reason="第一次申请。详细理由描述填充满 20 字以上。",
        )
        r2 = await service.request_data_access(
            db, source_id="ds-idem", user_id="u-x",
            reason="第二次申请，应该命中幂等直接返回现有 id。",
        )
        assert r1["id"] == r2["id"]
        assert r2["idempotent"] is True


@pytest.mark.asyncio
async def test_approve_writes_grant_and_notifies(db):
    """审批通过：写 DataAccessGrant（默认 90 天）+ 通知申请人。"""
    from app.auth.models import User as UserModel
    from sqlalchemy import select

    await _mk_source(db, id="ds-appr", visibility="private", department="客服部", owner_contact="u-owner")
    owner = UserModel(id="u-owner", username="u-owner", name="owner", role="operator", department="客服部", dingtalk_user_id="dt-owner", password_hash="", is_active=True, state="active")
    requester = UserModel(id="u-req", username="u-req", name="requester", role="operator", department="营销部", dingtalk_user_id="dt-req", password_hash="", is_active=True, state="active")
    db.add_all([owner, requester])
    await db.commit()

    with patch("app.dingtalk.outbox.outbox.enqueue") as mock_enq:
        mock_enq.return_value = 1
        req_res = await service.request_data_access(
            db, source_id="ds-appr", user_id="u-req",
            reason="需要访问客服对话进行意图识别模型训练（大于 20 字占位内容）。",
        )
        approve_res = await service.approve_data_access_request(
            db,
            request_id=req_res["id"],
            actor_user_id="u-owner",
            actor_role="operator",
            comment="OK",
        )
    assert approve_res["status"] == "approved"
    # grant 写入
    g = (await db.execute(
        select(DataAccessGrant).where(DataAccessGrant.source_id == "ds-appr", DataAccessGrant.grantee_user_id == "u-req")
    )).scalar_one()
    assert g.expires_at is not None  # 默认 90 天
    diff = (g.expires_at - now_bjt()).days
    assert 88 <= diff <= 91


@pytest.mark.asyncio
async def test_reject_requires_comment(db):
    """驳回必须填 comment。"""
    from app.common.exceptions import AppError
    from app.auth.models import User as UserModel

    await _mk_source(db, id="ds-rej", visibility="private", department="客服部", owner_contact="u-owner-r")
    owner = UserModel(id="u-owner-r", username="u-owner-r", name="o", role="operator", department="客服部", dingtalk_user_id="dt-o", password_hash="", is_active=True, state="active")
    db.add(owner)
    await db.commit()

    with patch("app.dingtalk.outbox.outbox.enqueue"):
        r = await service.request_data_access(
            db, source_id="ds-rej", user_id="u-r",
            reason="合理理由填充占位字符超过最小阈值二十字。",
        )
    with pytest.raises(AppError) as exc:
        await service.reject_data_access_request(
            db, request_id=r["id"], actor_user_id="u-owner-r", actor_role="operator", comment=""
        )
    assert exc.value.code == "COMMENT_REQUIRED"


@pytest.mark.asyncio
async def test_approve_permission_denied_non_owner(db):
    """非 owner/admin 审批被拒绝。"""
    from app.common.exceptions import AppError
    from app.auth.models import User as UserModel

    await _mk_source(db, id="ds-perm", visibility="private", department="客服部", owner_contact="u-real-owner")
    owner = UserModel(id="u-real-owner", username="u-real-owner", name="o", role="operator", department="客服部", dingtalk_user_id="dt-o", password_hash="", is_active=True, state="active")
    db.add(owner)
    await db.commit()

    with patch("app.dingtalk.outbox.outbox.enqueue"):
        r = await service.request_data_access(
            db, source_id="ds-perm", user_id="u-req",
            reason="申请理由占位字段超过 20 字以上用于测试流程。",
        )
    with pytest.raises(AppError) as exc:
        await service.approve_data_access_request(
            db, request_id=r["id"], actor_user_id="u-stranger", actor_role="operator"
        )
    assert exc.value.code == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_admin_can_approve_cross_owner(db):
    """admin 跨 owner 审批。"""
    from app.auth.models import User as UserModel

    await _mk_source(db, id="ds-adm", visibility="private", department="客服部", owner_contact="u-owner-a")
    owner = UserModel(id="u-owner-a", username="u-owner-a", name="o", role="operator", department="客服部", dingtalk_user_id="dt-o", password_hash="", is_active=True, state="active")
    req = UserModel(id="u-req-a", username="u-req-a", name="r", role="operator", department="营销部", dingtalk_user_id="dt-r", password_hash="", is_active=True, state="active")
    db.add_all([owner, req])
    await db.commit()

    with patch("app.dingtalk.outbox.outbox.enqueue"):
        r = await service.request_data_access(
            db, source_id="ds-adm", user_id="u-req-a",
            reason="合理访问理由占位字段填充至少二十字以上内容。",
        )
        res = await service.approve_data_access_request(
            db, request_id=r["id"], actor_user_id="admin-1", actor_role="admin"
        )
    assert res["status"] == "approved"


@pytest.mark.asyncio
async def test_expire_stale_access_requests(db):
    """cron: pending > 14 天置 expired。"""
    from sqlalchemy import select

    await _mk_source(db, id="ds-old", visibility="company", department="客服部", owner_contact=None)
    old_req = DataAccessRequest(
        source_id="ds-old",
        requester_id="u-old",
        reason="历史遗留申请占位内容超过 20 字以方便测试 expire 流程。",
        status="pending",
        created_at=now_bjt() - timedelta(days=15),
    )
    db.add(old_req)
    await db.commit()
    n = await service.expire_stale_access_requests(db)
    await db.commit()
    assert n >= 1
    r = (await db.execute(select(DataAccessRequest).where(DataAccessRequest.id == old_req.id))).scalar_one()
    assert r.status == "expired"


@pytest.mark.asyncio
async def test_approve_with_custom_expires(db):
    """审批人指定 expires_at 覆盖默认 90 天。"""
    from sqlalchemy import select
    from app.auth.models import User as UserModel

    await _mk_source(db, id="ds-ce", visibility="private", department="客服部", owner_contact="u-own-ce")
    owner = UserModel(id="u-own-ce", username="u-own-ce", name="o", role="operator", department="客服部", dingtalk_user_id="dt-o", password_hash="", is_active=True, state="active")
    req = UserModel(id="u-req-ce", username="u-req-ce", name="r", role="operator", department="营销部", dingtalk_user_id="dt-r", password_hash="", is_active=True, state="active")
    db.add_all([owner, req])
    await db.commit()

    custom = now_bjt() + timedelta(days=30)
    with patch("app.dingtalk.outbox.outbox.enqueue"):
        r = await service.request_data_access(
            db, source_id="ds-ce", user_id="u-req-ce",
            reason="申请理由占位字段超过二十字以便进行回归测试流程。",
        )
        await service.approve_data_access_request(
            db, request_id=r["id"], actor_user_id="u-own-ce", actor_role="operator",
            expires_at=custom,
        )
    g = (await db.execute(
        select(DataAccessGrant).where(DataAccessGrant.source_id == "ds-ce")
    )).scalar_one()
    assert abs((g.expires_at - custom).total_seconds()) < 5


@pytest.mark.asyncio
async def test_request_access_source_not_found(db):
    from app.common.exceptions import AppError

    with pytest.raises(AppError) as exc:
        await service.request_data_access(
            db, source_id="ds-nonexistent", user_id="u",
            reason="正常理由占位内容超过二十字最低限制测试失败分支。",
        )
    assert exc.value.code == "DATASOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_list_pending_requests_for_owner_returns_requester_name(db):
    from app.auth.models import User as UserModel

    await _mk_source(db, id="ds-owner-pending", visibility="private", department="客服部", owner_contact="u-owner")
    db.add_all([
        UserModel(
            id="u-owner",
            username="u-owner",
            name="负责人",
            role="operator",
            department="客服部",
            password_hash="x",
            is_active=True,
            state="active",
        ),
        UserModel(
            id="u-requester",
            username="u-requester",
            name="申请人甲",
            role="operator",
            department="营销部",
            password_hash="x",
            is_active=True,
            state="active",
        ),
    ])
    await db.commit()

    with patch("app.dingtalk.outbox.outbox.enqueue"):
        await service.request_data_access(
            db,
            source_id="ds-owner-pending",
            user_id="u-requester",
            reason="申请理由超过二十字，验证审批列表返回申请人姓名字段。",
        )

    rows = await service.list_pending_requests_for_owner(db, owner_user_id="u-owner")
    row = next(item for item in rows if item["source_id"] == "ds-owner-pending")
    assert row["requester_name"] == "申请人甲"


# ═══════════════════════════════════════════════════════════
# 阶段 4 · 团队能力聚合（3 case）
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_team_aggregation_by_department(db):
    """按部门聚合 Skill + 数据源 + AI 联系人。"""
    from app.auth.models import User as UserModel
    from app.hall.team_service import list_team_capabilities
    from app.skills.core.models import Skill

    # 营销部：3 Skill + 1 数据源 + AI 工程师
    for i in range(3):
        db.add(
            Skill(
                id=f"sk-m-{i}",
                name=f"营销 {i}",
                department="营销部",
                status="active" if i < 2 else "draft",
                category="审批" if i == 0 else "派单",
            )
        )
    db.add(
        DataSource(
            id="ds-mkt-daily", name="营销日表", department="营销部",
            source_type="csv_upload", config={}, is_active=True, visibility="department",
        )
    )
    db.add(
        UserModel(
            id="wang_ai", username="wang_ai", name="王二", role="ai_engineer",
            department="营销部", password_hash="x", is_active=True, state="active",
        )
    )
    # 客服部：无 AI 工程师，有 dept_admin
    db.add(Skill(id="sk-cs-1", name="客服 1", department="客服部", status="active", category="质检"))
    db.add(
        UserModel(
            id="lin_da", username="lin_da", name="林大", role="dept_admin",
            department="客服部", password_hash="x", is_active=True, state="active",
        )
    )
    await db.commit()

    teams = await list_team_capabilities(db, _mk_user(id="admin", role="admin", can_view_all=True))
    items = teams["items"] if isinstance(teams, dict) else teams
    marketing = next(t for t in items if t["department"] == "营销部")
    assert marketing["skill_count_total"] == 3
    assert marketing["skill_count_active"] == 2
    assert marketing["data_sources_count"] == 1
    assert marketing["ai_contact"]["user_id"] == "wang_ai"
    assert marketing["ai_contact"]["role"] == "ai_engineer"

    customer = next(t for t in items if t["department"] == "客服部")
    assert customer["ai_contact"]["user_id"] == "lin_da"
    assert customer["ai_contact"]["role"] == "dept_admin"


@pytest.mark.asyncio
async def test_team_top_categories_sorted(db):
    """top_categories 按 count 降序。"""
    from app.hall.team_service import list_team_capabilities
    from app.skills.core.models import Skill

    # 销售部：审批×5, 派单×3, 预警×1
    for i in range(5):
        db.add(Skill(id=f"sk-sa-a-{i}", name=f"审批 {i}", department="销售部", status="active", category="审批"))
    for i in range(3):
        db.add(Skill(id=f"sk-sa-b-{i}", name=f"派单 {i}", department="销售部", status="active", category="派单"))
    db.add(Skill(id="sk-sa-c-0", name="预警 0", department="销售部", status="active", category="预警"))
    await db.commit()

    teams = await list_team_capabilities(db, _mk_user(id="admin", role="admin", can_view_all=True))
    items = teams["items"] if isinstance(teams, dict) else teams
    sales = next(t for t in items if t["department"] == "销售部")
    cats = [c["category"] for c in sales["top_categories"]]
    assert cats == ["审批", "派单", "预警"]


@pytest.mark.asyncio
async def test_team_empty_department_no_contact(db):
    """部门无 active 用户 → ai_contact = None。"""
    from app.hall.team_service import list_team_capabilities
    from app.skills.core.models import Skill

    db.add(Skill(id="sk-orphan", name="孤儿", department="临时组", status="active"))
    await db.commit()

    teams = await list_team_capabilities(db, _mk_user(id="admin", role="admin", can_view_all=True))
    items = teams["items"] if isinstance(teams, dict) else teams
    orphan = next(t for t in items if t["department"] == "临时组")
    assert orphan["ai_contact"] is None


@pytest.mark.asyncio
async def test_hall_departments_come_from_org_units(db, client):
    """大厅部门筛选项来自系统组织架构一级部门，不从当前能力/数据结果反推。"""
    from app.config import settings
    from app.hall.team_service import list_departments
    from app.org.models import OrgUnit
    from app.skills.core.models import Skill

    root_id = settings.TASKTREE_TOP_LEVEL_PARENT_ID
    db.add_all([
        OrgUnit(id=root_id, name="公司", type="company", path=f"/{root_id}", sort_order=0),
        OrgUnit(id="dept-mkt", name="营销部", type="department", parent_id=root_id, path=f"/{root_id}/dept-mkt", sort_order=2),
        OrgUnit(id="dept-cs", name="客服部", type="department", parent_id=root_id, path=f"/{root_id}/dept-cs", sort_order=1),
        OrgUnit(id="dept-cs-a", name="客服一组", type="department", parent_id="dept-cs", path=f"/{root_id}/dept-cs/dept-cs-a", sort_order=1),
        OrgUnit(id="team-cs-b", name="客服二组", type="team", parent_id="dept-cs", path=f"/{root_id}/dept-cs/team-cs-b", sort_order=2),
        Skill(id="sk-legacy-dept", name="历史 Skill", department="历史部门", status="active", visibility="company"),
    ])
    await db.commit()

    payload = await list_departments(db)
    assert [item["department"] for item in payload["departments"]] == ["客服部", "营销部"]

    resp = await client.get("/api/hall/departments")
    assert resp.status_code == 200
    assert [item["department"] for item in resp.json()["departments"]] == ["客服部", "营销部"]


@pytest.mark.asyncio
async def test_list_capabilities_prefers_active_samples_and_related_sources(db):
    from app.hall.team_service import list_capabilities
    from app.skills.core.models import Skill

    now = now_bjt()
    db.add_all([
        Skill(
            id="sk-cap-active",
            name="活跃质检",
            department="客服部",
            status="active",
            visibility="company",
            category="质检",
            usage_count=5,
            created_at=now,
            updated_at=now,
            last_run_at=now,
        ),
        Skill(
            id="sk-cap-draft",
            name="草稿质检",
            department="客服部",
            status="draft",
            visibility="company",
            category="质检",
            usage_count=999,
            created_at=now,
            updated_at=now,
        ),
        Skill(
            id="sk-cap-warning",
            name="草稿预警",
            department="运营部",
            status="draft",
            visibility="company",
            category="预警",
            usage_count=12,
            created_at=now,
            updated_at=now,
        ),
        DataSource(
            id="ds-cap-quality",
            name="质检样本",
            department="客服部",
            source_type="csv_upload",
            config={},
            is_active=True,
            visibility="company",
            related_skills=["sk-cap-active", "sk-cap-draft"],
        ),
        DataSource(
            id="ds-cap-warning",
            name="预警样本",
            department="运营部",
            source_type="csv_upload",
            config={},
            is_active=True,
            visibility="company",
            related_skills=["sk-cap-warning"],
        ),
    ])
    await db.commit()

    payload = await list_capabilities(
        db,
        _mk_user(id="admin", role="admin", can_view_all=True),
    )
    quality = next(item for item in payload["items"] if item["category"] == "质检")
    assert quality["skill_count_total"] == 2
    assert quality["skill_count_active"] == 1
    assert [skill["id"] for skill in quality["sample_skills"]] == ["sk-cap-active"]
    assert [source["id"] for source in quality["data_sources"]] == ["ds-cap-quality"]

    warning = next(item for item in payload["items"] if item["category"] == "预警")
    assert [skill["id"] for skill in warning["sample_skills"]] == ["sk-cap-warning"]
    assert [source["id"] for source in warning["data_sources"]] == ["ds-cap-warning"]


@pytest.mark.asyncio
async def test_team_detail_filters_invisible_assets(db):
    """团队详情只返回当前用户可见的 Skill / 数据源。"""
    from app.auth.models import User as UserModel
    from app.hall.team_service import get_team_detail
    from app.skills.core.models import Skill

    db.add_all([
        Skill(
            id="sk-team-public",
            name="公开质检",
            department="客服部",
            status="active",
            visibility="company",
            category="质检",
        ),
        Skill(
            id="sk-team-hidden",
            name="部门内告警",
            department="客服部",
            status="active",
            visibility="department",
            category="预警",
        ),
        DataSource(
            id="ds-team-public",
            name="公开工单流",
            department="客服部",
            source_type="csv_upload",
            config={},
            is_active=True,
            visibility="company",
        ),
        DataSource(
            id="ds-team-hidden",
            name="部门私有明细",
            department="客服部",
            source_type="csv_upload",
            config={},
            is_active=True,
            visibility="department",
        ),
        UserModel(
            id="cs-owner",
            username="cs-owner",
            name="客服负责人",
            role="dept_admin",
            department="客服部",
            password_hash="x",
            is_active=True,
            state="active",
        ),
    ])
    await db.commit()

    detail = await get_team_detail(db, _mk_user(id="u-sales", dept="销售部"), "客服部")
    assert [item["id"] for item in detail["skills"]] == ["sk-team-public"]
    assert [item["id"] for item in detail["data_sources"]] == ["ds-team-public"]
    assert detail["summary"]["skill_count_total"] == 1
    assert detail["summary"]["data_sources_count"] == 1


@pytest.mark.asyncio
async def test_capability_detail_filters_invisible_assets(db):
    """能力详情不应泄漏跨部门不可见 Skill / 数据源。"""
    from app.hall.team_service import get_capability_detail
    from app.skills.core.models import Skill

    db.add_all([
        Skill(
            id="sk-cap-public",
            name="公开质检",
            department="客服部",
            status="active",
            visibility="company",
            category="质检",
        ),
        Skill(
            id="sk-cap-hidden",
            name="部门质检",
            department="客服部",
            status="active",
            visibility="department",
            category="质检",
        ),
        DataSource(
            id="ds-cap-public",
            name="公开质检样本",
            department="客服部",
            source_type="csv_upload",
            config={},
            is_active=True,
            visibility="company",
            related_skills=["sk-cap-public"],
        ),
        DataSource(
            id="ds-cap-hidden",
            name="部门质检样本",
            department="客服部",
            source_type="csv_upload",
            config={},
            is_active=True,
            visibility="department",
            related_skills=["sk-cap-hidden"],
        ),
    ])
    await db.commit()

    detail = await get_capability_detail(db, _mk_user(id="u-sales", dept="销售部"), "质检")
    assert [item["id"] for item in detail["skills"]] == ["sk-cap-public"]
    assert [item["id"] for item in detail["data_sources"]] == ["ds-cap-public"]
    assert detail["summary"]["skill_count_total"] == 1
    assert detail["summary"]["data_sources_count"] == 1
