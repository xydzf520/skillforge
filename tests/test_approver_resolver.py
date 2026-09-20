"""动态审批人解析器测试。"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.approval.approver_resolver import resolve_approvers
from app.auth.models import User
from app.common.exceptions import AppError
from app.config import settings
from app.database import Base
from app.org.models import OrgUnit, UserOrgMembership
from app.skills.core.access import SkillMember


@pytest_asyncio.fixture
async def db():
    """独立数据库 session，每个测试自动回滚。"""
    import re
    test_url = getattr(settings, "DATABASE_URL_TEST", None) or re.sub(
        r"/skillforge$", "/skillforge_test", str(settings.DATABASE_URL)
    )
    engine = create_async_engine(test_url, echo=False)

    # 确保所有 ORM 模型注册
    import app.approval.models  # noqa: F401
    import app.auth.models  # noqa: F401
    import app.org.models  # noqa: F401
    import app.skills.models  # noqa: F401
    import app.skills.members  # noqa: F401
    import app.skills.asset_models  # noqa: F401
    import app.optimizer.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _add_active_user(db: AsyncSession, user_id: str, role: str = "ai_engineer") -> None:
    db.add(User(id=user_id, username=user_id, name=user_id, role=role, is_active=True))


# ====== static 类型（向后兼容）======


@pytest.mark.asyncio
async def test_static_approver_id_directly(db: AsyncSession):
    """向后兼容：chain_step 只有 approver_id 时直接返回。"""
    result = await resolve_approvers(db, {"approver_id": "user-1"}, {})
    assert result == ["user-1"]


@pytest.mark.asyncio
async def test_static_type_explicit(db: AsyncSession):
    """显式 type=static 时从 approver_id 取值。"""
    result = await resolve_approvers(db, {"type": "static", "approver_id": "user-2"}, {})
    assert result == ["user-2"]


@pytest.mark.asyncio
async def test_static_missing_approver_id_raises(db: AsyncSession):
    """静态类型缺少 approver_id 时报错。"""
    with pytest.raises(AppError) as exc_info:
        await resolve_approvers(db, {"type": "static"}, {})
    assert exc_info.value.code == "APPROVER_NOT_FOUND"


# ====== skill_owner 类型 ======


@pytest.mark.asyncio
async def test_skill_owner_returns_owners(db: AsyncSession):
    """skill_owner 类型返回所有 role=owner 的成员。"""
    # 准备测试数据
    _add_active_user(db, "owner-1")
    _add_active_user(db, "owner-2")
    _add_active_user(db, "editor-1")
    db.add(SkillMember(skill_id="EC-001", user_id="owner-1", role="owner"))
    db.add(SkillMember(skill_id="EC-001", user_id="owner-2", role="owner"))
    db.add(SkillMember(skill_id="EC-001", user_id="editor-1", role="editor"))
    await db.flush()

    result = await resolve_approvers(
        db,
        {"type": "skill_owner"},
        {"skill_id": "EC-001"},
    )
    assert sorted(result) == ["owner-1", "owner-2"]


@pytest.mark.asyncio
async def test_skill_owner_excludes_inactive_owners(db: AsyncSession):
    """skill_owner 只返回 active 用户，离职 owner 不应被解析出来。"""
    db.add(User(id="owner-live", username="owner-live", name="在职 owner", role="ai_engineer", is_active=True, state="active"))
    db.add(User(id="owner-off", username="owner-off", name="离职 owner", role="ai_engineer", is_active=False, state="disabled"))
    db.add(SkillMember(skill_id="EC-001A", user_id="owner-live", role="owner"))
    db.add(SkillMember(skill_id="EC-001A", user_id="owner-off", role="owner"))
    await db.flush()

    result = await resolve_approvers(
        db,
        {"type": "skill_owner"},
        {"skill_id": "EC-001A"},
    )
    assert result == ["owner-live"]


@pytest.mark.asyncio
async def test_skill_owner_prefers_chain_step_skill_id(db: AsyncSession):
    """chain_step 中的 skill_id 优先于 context 中的。"""
    _add_active_user(db, "owner-A")
    _add_active_user(db, "owner-B")
    db.add(SkillMember(skill_id="EC-002", user_id="owner-A", role="owner"))
    db.add(SkillMember(skill_id="EC-003", user_id="owner-B", role="owner"))
    await db.flush()

    result = await resolve_approvers(
        db,
        {"type": "skill_owner", "skill_id": "EC-002"},
        {"skill_id": "EC-003"},
    )
    assert result == ["owner-A"]


@pytest.mark.asyncio
async def test_skill_owner_no_owners_raises(db: AsyncSession):
    """Skill 没有 owner 时报错。"""
    db.add(SkillMember(skill_id="EC-999", user_id="editor-1", role="editor"))
    await db.flush()

    with pytest.raises(AppError) as exc_info:
        await resolve_approvers(
            db,
            {"type": "skill_owner", "skill_id": "EC-999"},
            {},
        )
    assert exc_info.value.code == "APPROVER_NOT_FOUND"


@pytest.mark.asyncio
async def test_skill_owner_missing_skill_id_raises(db: AsyncSession):
    """缺少 skill_id 时报错。"""
    with pytest.raises(AppError) as exc_info:
        await resolve_approvers(db, {"type": "skill_owner"}, {})
    assert exc_info.value.code == "PARAM_INVALID"


# ====== org_manager 类型 ======


async def _setup_org_tree(db: AsyncSession):
    """创建三层组织树：公司 → 部门 → 小组，包含关联用户。"""
    # 先创建用户（满足 FK 约束）
    db.add(User(id="ceo", username="ceo", name="CEO", role="admin", is_active=True))
    db.add(User(id="director-1", username="director1", name="总监", role="director", is_active=True))
    db.add(User(id="lead-1", username="lead1", name="组长", role="ai_engineer", is_active=True))
    db.add(User(id="staff-1", username="staff1", name="员工", role="ai_engineer", is_active=True))
    await db.flush()

    # 组织单元
    db.add(OrgUnit(id="company", name="公司", type="company", parent_id=None, manager_user_id="ceo"))
    db.add(OrgUnit(id="dept-ec", name="EC部门", type="department", parent_id="company", manager_user_id="director-1"))
    db.add(OrgUnit(id="team-ec-a", name="EC-A组", type="team", parent_id="dept-ec", manager_user_id="lead-1"))
    await db.flush()

    # 用户组织关系
    db.add(UserOrgMembership(user_id="staff-1", org_unit_id="team-ec-a"))
    await db.flush()


@pytest.mark.asyncio
async def test_org_manager_level_1(db: AsyncSession):
    """level=1 返回直属组织单元的经理。"""
    await _setup_org_tree(db)

    result = await resolve_approvers(
        db,
        {"type": "org_manager", "level": 1},
        {"requester_id": "staff-1"},
    )
    assert result == ["lead-1"]


@pytest.mark.asyncio
async def test_org_manager_level_2(db: AsyncSession):
    """level=2 沿组织树向上两层，返回部门经理。"""
    await _setup_org_tree(db)

    result = await resolve_approvers(
        db,
        {"type": "org_manager", "level": 2},
        {"requester_id": "staff-1"},
    )
    assert result == ["director-1"]


@pytest.mark.asyncio
async def test_org_manager_level_exceeds_tree_returns_top(db: AsyncSession):
    """level 超过组织树深度时，返回顶层经理。"""
    await _setup_org_tree(db)

    result = await resolve_approvers(
        db,
        {"type": "org_manager", "level": 10},
        {"requester_id": "staff-1"},
    )
    # 走到顶层（company），返回 CEO
    assert result == ["ceo"]


@pytest.mark.asyncio
async def test_org_manager_no_membership_raises(db: AsyncSession):
    """用户没有组织关系时报错。"""
    with pytest.raises(AppError) as exc_info:
        await resolve_approvers(
            db,
            {"type": "org_manager", "level": 1},
            {"requester_id": "ghost-user"},
        )
    assert exc_info.value.code == "APPROVER_NOT_FOUND"


@pytest.mark.asyncio
async def test_org_manager_no_manager_raises(db: AsyncSession):
    """组织单元没有设置 manager 时报错。"""
    db.add(User(id="lonely", username="lonely", name="孤独用户", role="ai_engineer", is_active=True))
    await db.flush()
    db.add(OrgUnit(id="dept-empty", name="空部门", type="department", parent_id=None, manager_user_id=None))
    db.add(UserOrgMembership(user_id="lonely", org_unit_id="dept-empty"))
    await db.flush()

    with pytest.raises(AppError) as exc_info:
        await resolve_approvers(
            db,
            {"type": "org_manager", "level": 1},
            {"requester_id": "lonely"},
        )
    assert exc_info.value.code == "APPROVER_NOT_FOUND"


@pytest.mark.asyncio
async def test_org_manager_missing_requester_raises(db: AsyncSession):
    """缺少 requester_id 时报错。"""
    with pytest.raises(AppError) as exc_info:
        await resolve_approvers(db, {"type": "org_manager", "level": 1}, {})
    assert exc_info.value.code == "PARAM_INVALID"


# ====== role 类型 ======


@pytest.mark.asyncio
async def test_role_returns_active_users(db: AsyncSession):
    """role 类型返回指定角色的活跃用户。"""
    db.add(User(id="admin-1", username="admin1", name="管理员1", role="admin", is_active=True))
    db.add(User(id="admin-2", username="admin2", name="管理员2", role="admin", is_active=True))
    db.add(User(id="admin-off", username="admin_off", name="离职管理员", role="admin", is_active=False))
    db.add(User(id="eng-1", username="eng1", name="工程师", role="ai_engineer", is_active=True))
    await db.flush()

    result = await resolve_approvers(db, {"type": "role", "role": "admin"}, {})
    assert sorted(result) == ["admin-1", "admin-2"]


@pytest.mark.asyncio
async def test_role_no_matching_users_raises(db: AsyncSession):
    """没有匹配角色的活跃用户时报错。"""
    with pytest.raises(AppError) as exc_info:
        await resolve_approvers(db, {"type": "role", "role": "nonexistent"}, {})
    assert exc_info.value.code == "APPROVER_NOT_FOUND"


@pytest.mark.asyncio
async def test_role_missing_role_field_raises(db: AsyncSession):
    """缺少 role 字段时报错。"""
    with pytest.raises(AppError) as exc_info:
        await resolve_approvers(db, {"type": "role"}, {})
    assert exc_info.value.code == "PARAM_INVALID"


# ====== 其他边界 ======


@pytest.mark.asyncio
async def test_unsupported_type_raises(db: AsyncSession):
    """不支持的 type 报错。"""
    with pytest.raises(AppError) as exc_info:
        await resolve_approvers(db, {"type": "magic"}, {})
    assert exc_info.value.code == "PARAM_INVALID"


@pytest.mark.asyncio
async def test_invalid_chain_step_type_raises(db: AsyncSession):
    """chain_step 不是 dict 时报错。"""
    with pytest.raises(AppError) as exc_info:
        await resolve_approvers(db, "not-a-dict", {})  # type: ignore
    assert exc_info.value.code == "PARAM_INVALID"
