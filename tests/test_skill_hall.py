"""Skill 大厅 API 测试。

测试可见性规则、搜索、排序、统计、筛选器等核心逻辑。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


def _make_user(
    user_id: str = "user1",
    name: str = "普通用户",
    role: str = "operator",
    department: str = "EC",
    can_view_all: bool = False,
):
    """创建 mock 用户"""
    user = MagicMock()
    user.id = user_id
    user.name = name
    user.username = user_id
    user.role = role
    user.department = department
    user.can_view_all = can_view_all
    user.is_active = True
    user.must_change_password = False
    user.dingtalk_user_id = None
    user.avatar_url = None
    return user


@pytest_asyncio.fixture
async def hall_app():
    """构建包含 Skill 大厅路由的测试应用，并插入种子数据"""
    import re
    import app.database as db_mod

    # 加载 prompt registry
    try:
        from app.common.prompt_registry import init_registry, prompt_registry
        if not prompt_registry._sections:
            init_registry()
    except Exception:
        pass

    test_url = getattr(settings, "DATABASE_URL_TEST", None) or re.sub(
        r"/skillforge$", "/skillforge_test", str(settings.DATABASE_URL)
    )
    eng = create_async_engine(test_url, echo=False)
    fac = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)

    from app.database import Base

    # 确保所有 ORM 模型注册
    import app.skills.models, app.skills.members, app.auth.models  # noqa: F401
    import app.reviews.models, app.execution.models, app.datasources.models  # noqa: F401
    import app.dingtalk.models, app.common.models, app.common.audit  # noqa: F401
    import app.workbench.models, app.optimizer.models, app.testing.models  # noqa: F401
    import app.todos.models, app.org.models, app.portal.models, app.approval.models  # noqa: F401

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS optimizer_events (
                id SERIAL PRIMARY KEY,
                session_id VARCHAR(50) NOT NULL,
                candidate_id VARCHAR(50),
                event_type VARCHAR(50) NOT NULL,
                detail JSON,
                user_id VARCHAR(50),
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))

    orig_engine, orig_factory = db_mod.engine, db_mod.async_session_factory
    db_mod.engine = eng
    db_mod.async_session_factory = fac

    # 插入种子数据
    async with fac() as session:
        # 用户
        from app.auth.models import User as UserModel
        session.add(UserModel(
            id="owner1", username="owner1", name="负责人A",
            role="ai_engineer", department="EC", is_active=True, must_change_password=False,
        ))
        session.add(UserModel(
            id="user1", username="user1", name="普通用户",
            role="operator", department="EC", is_active=True, must_change_password=False,
        ))
        session.add(UserModel(
            id="user2", username="user2", name="其他部门用户",
            role="operator", department="BD", is_active=True, must_change_password=False,
        ))
        await session.flush()

        # Skills：不同可见性
        from app.skills.core.models import Skill
        from datetime import datetime, timedelta

        now = datetime.utcnow()

        # 1. company 可见
        session.add(Skill(
            id="skill-company-1", name="全公司可见Skill", department="EC",
            status="active", visibility="company", category="投放",
            owner="owner1", usage_count=100, success_rate=0.95,
            updated_at=now, created_at=now,
            last_run_at=now - timedelta(days=1),
        ))
        # 2. department 可见（EC 部门）
        session.add(Skill(
            id="skill-dept-ec", name="EC部门Skill", department="EC",
            status="active", visibility="department", category="投放",
            owner="owner1", usage_count=50, success_rate=0.8,
            updated_at=now - timedelta(days=2), created_at=now - timedelta(days=10),
        ))
        # 3. department 可见（BD 部门）
        session.add(Skill(
            id="skill-dept-bd", name="BD部门Skill", department="BD",
            status="active", visibility="department", category="商务",
            owner="user2", usage_count=30, success_rate=0.7,
            updated_at=now - timedelta(days=3), created_at=now - timedelta(days=20),
        ))
        # 4. private 可见
        session.add(Skill(
            id="skill-private-1", name="私有Skill", department="EC",
            status="draft", visibility="private", category="实验",
            owner="owner1", usage_count=5,
            updated_at=now - timedelta(days=5), created_at=now - timedelta(days=30),
        ))
        # 5. 另一个 company Skill（用于搜索测试）
        session.add(Skill(
            id="skill-company-2", name="全公司分析报告", department="BD",
            status="active", visibility="company", category="商务",
            owner="user2", usage_count=200, success_rate=0.99,
            updated_at=now - timedelta(hours=1), created_at=now - timedelta(days=5),
            last_run_at=now - timedelta(hours=2),
        ))
        # 6. forked skill（用于 most_forked 统计）
        session.add(Skill(
            id="skill-fork-1", name="Fork自公司Skill", department="EC",
            status="draft", visibility="company",
            forked_from="skill-company-1", usage_count=10,
            updated_at=now, created_at=now,
        ))
        await session.flush()

        # SkillMember：让 user1 是 skill-private-1 的成员
        from app.skills.members import SkillMember
        session.add(SkillMember(
            skill_id="skill-private-1", user_id="user1", role="editor",
            granted_by="owner1",
        ))
        await session.commit()

    yield eng, fac

    await eng.dispose()
    db_mod.engine, db_mod.async_session_factory = orig_engine, orig_factory


@pytest_asyncio.fixture
async def admin_client(hall_app):
    """admin 用户客户端"""
    eng, fac = hall_app
    from app.common.exceptions import AppError, app_error_handler
    from app.auth.dependencies import get_current_user
    from fastapi import FastAPI

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    mock_admin = _make_user("admin", "管理员", "admin", "AI小组", can_view_all=True)
    app.dependency_overrides[get_current_user] = lambda: mock_admin

    from app.skills.router import router as skills_router
    app.include_router(skills_router, prefix="/api/skills")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def ec_user_client(hall_app):
    """EC 部门普通用户客户端"""
    eng, fac = hall_app
    from app.common.exceptions import AppError, app_error_handler
    from app.auth.dependencies import get_current_user
    from fastapi import FastAPI

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    mock_user = _make_user("user1", "普通用户", "operator", "EC")
    app.dependency_overrides[get_current_user] = lambda: mock_user

    from app.skills.router import router as skills_router
    app.include_router(skills_router, prefix="/api/skills")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def bd_user_client(hall_app):
    """BD 部门普通用户客户端"""
    eng, fac = hall_app
    from app.common.exceptions import AppError, app_error_handler
    from app.auth.dependencies import get_current_user
    from fastapi import FastAPI

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    mock_user = _make_user("user2", "BD用户", "operator", "BD")
    app.dependency_overrides[get_current_user] = lambda: mock_user

    from app.skills.router import router as skills_router
    app.include_router(skills_router, prefix="/api/skills")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ═══════════════════════════════════════
# 可见性规则测试
# ═══════════════════════════════════════


@pytest.mark.asyncio
async def test_admin_sees_all(admin_client):
    """admin 用户应能看到所有 Skill"""
    resp = await admin_client.get("/api/skills/hall")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 6  # 全部 6 个 Skill


@pytest.mark.asyncio
async def test_ec_user_visibility(ec_user_client):
    """EC 部门用户应看到：company + EC department + 作为 member 的 private"""
    resp = await ec_user_client.get("/api/skills/hall")
    assert resp.status_code == 200
    data = resp.json()
    ids = {item["id"] for item in data["items"]}

    # company 可见的
    assert "skill-company-1" in ids
    assert "skill-company-2" in ids
    assert "skill-fork-1" in ids
    # EC department 可见的
    assert "skill-dept-ec" in ids
    # private 但 user1 是成员
    assert "skill-private-1" in ids
    # BD department 不可见
    assert "skill-dept-bd" not in ids

    assert data["total"] == 5


@pytest.mark.asyncio
async def test_bd_user_visibility(bd_user_client):
    """BD 部门用户应看到：company + BD department，看不到 EC department 和 private"""
    resp = await bd_user_client.get("/api/skills/hall")
    assert resp.status_code == 200
    data = resp.json()
    ids = {item["id"] for item in data["items"]}

    assert "skill-company-1" in ids
    assert "skill-company-2" in ids
    assert "skill-fork-1" in ids
    assert "skill-dept-bd" in ids
    # EC department 对 BD 用户不可见
    assert "skill-dept-ec" not in ids
    # private 且不是成员
    assert "skill-private-1" not in ids


# ═══════════════════════════════════════
# 搜索测试
# ═══════════════════════════════════════


@pytest.mark.asyncio
async def test_search_by_name(admin_client):
    """关键词搜索应匹配 name/id/description"""
    resp = await admin_client.get("/api/skills/hall", params={"q": "分析报告"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any(item["id"] == "skill-company-2" for item in data["items"])


@pytest.mark.asyncio
async def test_search_by_id(admin_client):
    """用 skill ID 搜索"""
    resp = await admin_client.get("/api/skills/hall", params={"q": "dept-ec"})
    assert resp.status_code == 200
    data = resp.json()
    assert any(item["id"] == "skill-dept-ec" for item in data["items"])


# ═══════════════════════════════════════
# 筛选测试
# ═══════════════════════════════════════


@pytest.mark.asyncio
async def test_filter_by_department(admin_client):
    """按部门筛选"""
    resp = await admin_client.get("/api/skills/hall", params={"department": "BD"})
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["department"] == "BD"


@pytest.mark.asyncio
async def test_filter_by_category(admin_client):
    """按分类筛选"""
    resp = await admin_client.get("/api/skills/hall", params={"category": "投放"})
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["category"] == "投放"


@pytest.mark.asyncio
async def test_filter_by_status(admin_client):
    """按状态筛选"""
    resp = await admin_client.get("/api/skills/hall", params={"status": "draft"})
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["status"] == "draft"


# ═══════════════════════════════════════
# 排序测试
# ═══════════════════════════════════════


@pytest.mark.asyncio
async def test_sort_by_popularity(admin_client):
    """按 usage_count 排序"""
    resp = await admin_client.get("/api/skills/hall", params={"sort_by": "popularity"})
    assert resp.status_code == 200
    data = resp.json()
    counts = [item["usage_count"] for item in data["items"]]
    assert counts == sorted(counts, reverse=True)


@pytest.mark.asyncio
async def test_sort_by_name(admin_client):
    """按名称排序"""
    resp = await admin_client.get("/api/skills/hall", params={"sort_by": "name"})
    assert resp.status_code == 200
    data = resp.json()
    names = [item["name"] for item in data["items"]]
    assert names == sorted(names)


# ═══════════════════════════════════════
# 分页测试
# ═══════════════════════════════════════


@pytest.mark.asyncio
async def test_pagination(admin_client):
    """分页返回正确的 total 和 page_size"""
    resp = await admin_client.get("/api/skills/hall", params={"page": 1, "page_size": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["total"] == 6
    assert data["page"] == 1
    assert data["page_size"] == 2


# ═══════════════════════════════════════
# 字段完整性测试
# ═══════════════════════════════════════


@pytest.mark.asyncio
async def test_item_fields(admin_client):
    """每个 item 应包含 can_fork / is_member / owner_name 额外字段"""
    resp = await admin_client.get("/api/skills/hall")
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert "can_fork" in item
        assert "is_member" in item
        assert "owner_name" in item
        assert isinstance(item["can_fork"], bool)
        assert isinstance(item["is_member"], bool)


# ═══════════════════════════════════════
# 统计端点测试
# ═══════════════════════════════════════


@pytest.mark.asyncio
async def test_stats_endpoint(admin_client):
    """统计端点返回正确结构"""
    resp = await admin_client.get("/api/skills/hall/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_visible" in data
    assert "by_department" in data
    assert "by_category" in data
    assert "recently_published" in data
    assert "most_forked" in data
    assert "trending" in data
    assert data["total_visible"] == 6


@pytest.mark.asyncio
async def test_stats_most_forked(admin_client):
    """most_forked 应返回被 fork 最多的源 Skill"""
    resp = await admin_client.get("/api/skills/hall/stats")
    assert resp.status_code == 200
    data = resp.json()
    if data["most_forked"]:
        assert data["most_forked"][0]["id"] == "skill-company-1"
        assert data["most_forked"][0]["fork_count"] >= 1


@pytest.mark.asyncio
async def test_stats_most_forked_does_not_leak_unreadable_source(hall_app, ec_user_client):
    """most_forked 不应泄露当前用户无权读取的源 Skill。"""
    _, fac = hall_app
    from datetime import datetime
    from app.skills.core.models import Skill

    now = datetime.utcnow()
    async with fac() as session:
        session.add(Skill(
            id="skill-private-source-bd",
            name="BD私有源Skill",
            department="BD",
            status="active",
            visibility="private",
            category="商务",
            owner="user2",
            usage_count=0,
            updated_at=now,
            created_at=now,
        ))
        session.add_all([
            Skill(
                id="skill-visible-fork-secret-1",
                name="可见Fork一",
                department="EC",
                status="active",
                visibility="company",
                forked_from="skill-private-source-bd",
                owner="owner1",
                usage_count=0,
                updated_at=now,
                created_at=now,
            ),
            Skill(
                id="skill-visible-fork-secret-2",
                name="可见Fork二",
                department="EC",
                status="active",
                visibility="company",
                forked_from="skill-private-source-bd",
                owner="owner1",
                usage_count=0,
                updated_at=now,
                created_at=now,
            ),
        ])
        await session.commit()

    resp = await ec_user_client.get("/api/skills/hall/stats")
    assert resp.status_code == 200
    data = resp.json()
    leaked = [
        item for item in data["most_forked"]
        if item["id"] == "skill-private-source-bd" or item["name"] == "BD私有源Skill"
    ]
    assert leaked == []


# ═══════════════════════════════════════
# 筛选器端点测试
# ═══════════════════════════════════════


@pytest.mark.asyncio
async def test_filters_endpoint(admin_client):
    """筛选器端点返回部门和分类列表"""
    resp = await admin_client.get("/api/skills/hall/filters")
    assert resp.status_code == 200
    data = resp.json()
    assert "departments" in data
    assert "categories" in data
    # 应该有至少 EC 和 BD 两个部门
    dept_names = [d["department"] for d in data["departments"]]
    assert "EC" in dept_names
    assert "BD" in dept_names
    # 每个部门有 count
    for d in data["departments"]:
        assert "count" in d
        assert d["count"] > 0


@pytest.mark.asyncio
async def test_list_hall_skills_profile_returns_only_related_datasources(hall_app):
    """include=profile 只补回当前 skill 相关的数据源。"""
    _, fac = hall_app
    from app.datasources.models import DataSource
    from app.skills import hall_service

    async with fac() as session:
        session.add_all([
            DataSource(
                id="ds-profile-related",
                name="投放画像",
                department="EC",
                source_type="csv_upload",
                config={},
                is_active=True,
                visibility="company",
                owner_contact="owner1",
                created_by="owner1",
                related_skills=["skill-company-1"],
            ),
            DataSource(
                id="ds-profile-other",
                name="无关数据源",
                department="BD",
                source_type="csv_upload",
                config={},
                is_active=True,
                visibility="company",
                owner_contact="owner1",
                created_by="owner1",
                related_skills=["skill-company-2"],
            ),
        ])
        await session.commit()

        with patch("app.common.cache.cache_get", new=AsyncMock(return_value=None)), patch(
            "app.common.cache.cache_set",
            new=AsyncMock(),
        ):
            payload = await hall_service.list_hall_skills(
                session,
                _make_user(),
                include=["profile"],
            )

    item = next(row for row in payload["items"] if row["id"] == "skill-company-1")
    assert [ds["id"] for ds in item["profile"]["data_sources"]] == ["ds-profile-related"]


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_build_skill_profiles_sqlite_fallback_filters_in_python():
    """sqlite 降级路径不使用 PG ARRAY 操作符，但仍能只保留相关数据源。"""
    from app.skills import hall_service

    fake_db = SimpleNamespace(
        bind=SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
    )

    calls = {"count": 0}

    async def _execute(_stmt):
        calls["count"] += 1
        if calls["count"] == 1:
            return _FakeResult([])
        return _FakeResult([
            ("ds-related", "关联数据源", ["skill-company-1", "other"]),
            ("ds-other", "无关数据源", ["skill-x"]),
        ])

    fake_db.execute = _execute

    with patch("app.common.cache.cache_get", new=AsyncMock(return_value=None)), patch(
        "app.common.cache.cache_set",
        new=AsyncMock(),
    ):
        profiles = await hall_service._build_skill_profiles(fake_db, ["skill-company-1"])

    assert profiles["skill-company-1"]["data_sources"] == [
        {"id": "ds-related", "name": "关联数据源"},
    ]
