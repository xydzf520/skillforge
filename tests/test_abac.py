"""ABAC 策略模块测试

条件引擎 / Schema 校验 / 环境收集器：纯单元测试，无 DB 依赖。
策略 CRUD + 评估：使用独立 DB 引擎，仅创建 abac_policies 表。
API 路由：使用独立 FastAPI app + mock 认证。
"""

import pytest
import pytest_asyncio
from unittest.mock import MagicMock
import re

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text


# ── 本地 DB fixture（仅建 abac_policies 表，绕过 conftest drop_all FK 问题） ──

@pytest_asyncio.fixture
async def abac_db():
    """创建独立的异步引擎 + session，仅管理 abac_policies 表。"""
    from app.config import settings
    import app.database as db_mod

    test_url = getattr(settings, "DATABASE_URL_TEST", None) or re.sub(
        r"/skillforge$", "/skillforge_test", str(settings.DATABASE_URL)
    )
    eng = create_async_engine(test_url, echo=False)

    # 只创建 abac_policies 表（删再建）
    async with eng.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS abac_policies CASCADE"))
        await conn.execute(text("""
            CREATE TABLE abac_policies (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                description TEXT,
                resource_type VARCHAR(50) NOT NULL,
                action VARCHAR(50) NOT NULL,
                effect VARCHAR(10) NOT NULL DEFAULT 'deny',
                priority INTEGER NOT NULL DEFAULT 0,
                subject_condition JSONB,
                resource_condition JSONB,
                environment_condition JSONB,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_by VARCHAR(50),
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """))

    fac = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)

    # 替换全局（ABAC 服务用 get_db → async_session_factory）
    orig_engine, orig_factory = db_mod.engine, db_mod.async_session_factory
    db_mod.engine = eng
    db_mod.async_session_factory = fac

    yield fac

    # 清理
    async with eng.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS abac_policies CASCADE"))
    await eng.dispose()
    db_mod.engine, db_mod.async_session_factory = orig_engine, orig_factory


@pytest_asyncio.fixture
async def abac_client(abac_db):
    """带 ABAC 路由的独立 FastAPI app（mock admin 认证）。"""
    from app.common.exceptions import AppError, app_error_handler
    from app.auth.dependencies import get_current_user
    from fastapi import FastAPI

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    mock_admin = MagicMock()
    mock_admin.id = "admin"
    mock_admin.name = "管理员"
    mock_admin.username = "admin"
    mock_admin.role = "admin"
    mock_admin.department = "AI小组"
    mock_admin.can_view_all = True
    mock_admin.is_active = True
    app.dependency_overrides[get_current_user] = lambda: mock_admin

    from app.auth.abac_router import router
    app.include_router(router, prefix="/api/abac")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ===== 条件引擎单元测试 =====


class TestConditionEngine:
    """condition_engine.evaluate_condition 纯逻辑测试（不需要数据库）"""

    def test_none_condition_matches_everything(self):
        """空条件始终匹配"""
        from app.approval.condition_engine import evaluate_condition
        assert evaluate_condition(None, {"role": "admin"}) is True

    def test_empty_dict_matches_everything(self):
        """空 dict 始终匹配"""
        from app.approval.condition_engine import evaluate_condition
        assert evaluate_condition({}, {"role": "admin"}) is True

    def test_literal_match(self):
        """字面量等值匹配"""
        from app.approval.condition_engine import evaluate_condition
        assert evaluate_condition({"role": "admin"}, {"role": "admin"}) is True
        assert evaluate_condition({"role": "admin"}, {"role": "viewer"}) is False

    def test_in_operator(self):
        """in 操作符"""
        from app.approval.condition_engine import evaluate_condition
        cond = {"role": {"in": ["admin", "ai_engineer"]}}
        assert evaluate_condition(cond, {"role": "admin"}) is True
        assert evaluate_condition(cond, {"role": "ai_engineer"}) is True
        assert evaluate_condition(cond, {"role": "operator"}) is False

    def test_not_in_operator(self):
        """not_in 操作符"""
        from app.approval.condition_engine import evaluate_condition
        cond = {"department": {"not_in": ["外部"]}}
        assert evaluate_condition(cond, {"department": "EC"}) is True
        assert evaluate_condition(cond, {"department": "外部"}) is False

    def test_comparison_operators(self):
        """gte / lte / gt / lt 操作符"""
        from app.approval.condition_engine import evaluate_condition
        cond = {"hour": {"gte": 9, "lte": 18}}
        assert evaluate_condition(cond, {"hour": 9}) is True
        assert evaluate_condition(cond, {"hour": 12}) is True
        assert evaluate_condition(cond, {"hour": 18}) is True
        assert evaluate_condition(cond, {"hour": 8}) is False
        assert evaluate_condition(cond, {"hour": 19}) is False

    def test_gt_lt_operators(self):
        """严格大于 / 小于"""
        from app.approval.condition_engine import evaluate_condition
        assert evaluate_condition({"x": {"gt": 5}}, {"x": 6}) is True
        assert evaluate_condition({"x": {"gt": 5}}, {"x": 5}) is False
        assert evaluate_condition({"x": {"lt": 5}}, {"x": 4}) is True
        assert evaluate_condition({"x": {"lt": 5}}, {"x": 5}) is False

    def test_contains_operator(self):
        """contains 操作符：list → 成员判断；string → 子串匹配"""
        from app.approval.condition_engine import evaluate_condition
        cond = {"tags": {"contains": "sensitive"}}
        assert evaluate_condition(cond, {"tags": ["sensitive", "pii"]}) is True
        assert evaluate_condition(cond, {"tags": ["public"]}) is False
        # 字符串值 → 做子串匹配
        assert evaluate_condition(cond, {"tags": "has-no-keyword"}) is False
        assert evaluate_condition(cond, {"tags": "sensitive-data"}) is True

    def test_regex_operator(self):
        """regex 操作符"""
        from app.approval.condition_engine import evaluate_condition
        cond = {"name": {"regex": "^EC-.*"}}
        assert evaluate_condition(cond, {"name": "EC-投放-01"}) is True
        assert evaluate_condition(cond, {"name": "直播-01"}) is False

    def test_eq_ne_operators(self):
        """eq / ne 操作符"""
        from app.approval.condition_engine import evaluate_condition
        assert evaluate_condition({"x": {"eq": 1}}, {"x": 1}) is True
        assert evaluate_condition({"x": {"eq": 1}}, {"x": 2}) is False
        assert evaluate_condition({"x": {"ne": 1}}, {"x": 2}) is True
        assert evaluate_condition({"x": {"ne": 1}}, {"x": 1}) is False

    def test_multi_field_and_logic(self):
        """多字段 AND 语义"""
        from app.approval.condition_engine import evaluate_condition
        cond = {"role": "admin", "department": "EC"}
        assert evaluate_condition(cond, {"role": "admin", "department": "EC"}) is True
        assert evaluate_condition(cond, {"role": "admin", "department": "直播"}) is False
        assert evaluate_condition(cond, {"role": "viewer", "department": "EC"}) is False

    def test_none_value_comparison(self):
        """目标值为 None 时比较操作符返回 False"""
        from app.approval.condition_engine import evaluate_condition
        assert evaluate_condition({"x": {"gte": 5}}, {"x": None}) is False
        assert evaluate_condition({"x": {"regex": ".*"}}, {"x": None}) is False

    def test_missing_field(self):
        """目标缺少条件字段 → 不匹配"""
        from app.approval.condition_engine import evaluate_condition
        assert evaluate_condition({"role": "admin"}, {}) is False

    def test_unknown_operator_fail_closed(self):
        """未知操作符视为不匹配（fail-closed，避免错写算子导致策略命中）"""
        from app.approval.condition_engine import evaluate_condition
        assert evaluate_condition({"x": {"future_op": 1}}, {"x": 1}) is False

    def test_depth_limit_rejected(self):
        """递归深度 > MAX_CONDITION_DEPTH 时拒绝求值，防恶意嵌套栈溢出。"""
        from app.approval.condition_engine import evaluate_condition, MAX_CONDITION_DEPTH
        deep = {"and": [{"x": 1}]}
        # 构造 MAX_CONDITION_DEPTH+5 层嵌套 or
        for _ in range(MAX_CONDITION_DEPTH + 5):
            deep = {"or": [deep]}
        assert evaluate_condition(deep, {"x": 1}) is False


# ===== 条件 Schema 校验测试 =====


class TestConditionSchema:
    """condition_schema.validate_condition_json 校验测试"""

    def test_none_is_valid(self):
        from app.approval.condition_schema import validate_condition_json
        assert validate_condition_json(None) == []

    def test_valid_literal(self):
        from app.approval.condition_schema import validate_condition_json
        assert validate_condition_json({"role": "admin"}) == []

    def test_valid_operators(self):
        from app.approval.condition_schema import validate_condition_json
        assert validate_condition_json({"role": {"in": ["admin"]}}) == []
        assert validate_condition_json({"x": {"gte": 5, "lte": 10}}) == []

    def test_invalid_type(self):
        from app.approval.condition_schema import validate_condition_json
        errors = validate_condition_json("not a dict")
        assert len(errors) == 1
        assert "dict" in errors[0]

    def test_unknown_operator(self):
        from app.approval.condition_schema import validate_condition_json
        errors = validate_condition_json({"x": {"bad_op": 1}})
        assert len(errors) == 1
        assert "bad_op" in errors[0]

    def test_in_not_list(self):
        from app.approval.condition_schema import validate_condition_json
        errors = validate_condition_json({"x": {"in": "not_a_list"}})
        assert any("数组" in e for e in errors)

    def test_invalid_regex(self):
        from app.approval.condition_schema import validate_condition_json
        errors = validate_condition_json({"x": {"regex": "[invalid"}})
        assert any("regex" in e for e in errors)

    def test_valid_regex(self):
        from app.approval.condition_schema import validate_condition_json
        assert validate_condition_json({"x": {"regex": "^EC-.*"}}) == []


# ===== 环境收集器测试 =====


class TestEnvironmentCollector:
    """environment.collect_environment 测试"""

    def test_collect_without_request(self):
        """无 request 时仍能收集基本环境"""
        from app.auth.environment import collect_environment
        env = collect_environment()
        assert "is_production" in env
        assert "timestamp" in env
        assert "hour" in env
        assert "weekday" in env
        assert "is_business_hours" in env
        # 无 request → 不应有 ip / method
        assert "ip" not in env
        assert "method" not in env

    def test_collect_with_mock_request(self):
        """带 mock request 收集完整环境"""
        from app.auth.environment import collect_environment

        request = MagicMock()
        request.client.host = "192.168.1.100"
        request.url.path = "/api/skills/EC-01/export"
        request.method = "GET"

        env = collect_environment(request)
        assert env["ip"] == "192.168.1.100"
        assert env["is_export"] is True
        assert env["method"] == "GET"
        assert env["path"] == "/api/skills/EC-01/export"


# ===== ABAC 策略 API 测试 =====


@pytest.mark.asyncio
async def test_policy_crud(abac_client):
    """测试策略 CRUD 全流程"""
    client = abac_client

    # 1. 创建策略
    resp = await client.post("/api/abac/policies", json={
        "name": "禁止非生产时间发布",
        "description": "工作时间外禁止发布 Skill",
        "resource_type": "skill",
        "action": "publish",
        "effect": "deny",
        "priority": 10,
        "environment_condition": {"is_business_hours": False},
    })
    assert resp.status_code == 200, f"创建失败: {resp.json()}"
    policy = resp.json()
    policy_id = policy["id"]
    assert policy["name"] == "禁止非生产时间发布"
    assert policy["effect"] == "deny"
    assert policy["priority"] == 10
    assert policy["enabled"] is True

    # 2. 查询列表
    resp = await client.get("/api/abac/policies")
    assert resp.status_code == 200
    policies = resp.json()
    assert any(p["id"] == policy_id for p in policies)

    # 3. 按资源类型过滤
    resp = await client.get("/api/abac/policies?resource_type=skill")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

    resp = await client.get("/api/abac/policies?resource_type=nonexistent")
    assert resp.status_code == 200
    assert len(resp.json()) == 0

    # 4. 获取单条
    resp = await client.get(f"/api/abac/policies/{policy_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == policy_id

    # 5. 更新
    resp = await client.put(f"/api/abac/policies/{policy_id}", json={
        "name": "更新后的策略名",
        "priority": 20,
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "更新后的策略名"
    assert resp.json()["priority"] == 20

    # 6. 删除
    resp = await client.delete(f"/api/abac/policies/{policy_id}")
    assert resp.status_code == 200

    # 7. 删除后查不到
    resp = await client.get(f"/api/abac/policies/{policy_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_policy_with_conditions(abac_client):
    """测试创建带完整条件的策略"""
    resp = await abac_client.post("/api/abac/policies", json={
        "name": "高风险 Skill 仅管理员可编辑",
        "resource_type": "skill",
        "action": "edit",
        "effect": "deny",
        "priority": 100,
        "subject_condition": {"role": {"not_in": ["admin", "ai_engineer"]}},
        "resource_condition": {"risk_level": "R3"},
        "environment_condition": {"is_production": True},
    })
    assert resp.status_code == 200
    policy = resp.json()
    assert policy["subject_condition"] == {"role": {"not_in": ["admin", "ai_engineer"]}}
    assert policy["resource_condition"] == {"risk_level": "R3"}
    assert policy["environment_condition"] == {"is_production": True}


@pytest.mark.asyncio
async def test_create_policy_invalid_condition(abac_client):
    """测试创建策略时条件格式无效"""
    resp = await abac_client.post("/api/abac/policies", json={
        "name": "无效策略",
        "resource_type": "skill",
        "action": "read",
        "subject_condition": {"role": {"bad_op": "admin"}},
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "PARAM_INVALID"


@pytest.mark.asyncio
async def test_create_policy_invalid_effect(abac_client):
    """测试创建策略时 effect 值无效"""
    resp = await abac_client.post("/api/abac/policies", json={
        "name": "无效效果",
        "resource_type": "skill",
        "action": "read",
        "effect": "maybe",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_nonexistent_policy(abac_client):
    """更新不存在的策略 → 404"""
    resp = await abac_client.put("/api/abac/policies/99999", json={"name": "x"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_policy(abac_client):
    """删除不存在的策略 → 404"""
    resp = await abac_client.delete("/api/abac/policies/99999")
    assert resp.status_code == 404


# ===== ABAC 评估逻辑测试 =====


@pytest.mark.asyncio
async def test_evaluate_abac_no_policies(abac_db):
    """无策略时 fail-closed 默认拒绝（C2 安全改动）"""
    from app.auth.abac_service import evaluate_abac

    async with abac_db() as db:
        allowed, reason = await evaluate_abac(
            db, "skill", "read",
            subject={"role": "viewer"},
            resource={"skill_id": "EC-01"},
            environment={"is_production": True},
        )
        assert allowed is False
        assert reason == "no_policy_defined"


@pytest.mark.asyncio
async def test_evaluate_abac_deny_matches(abac_db):
    """deny 策略命中 → 拒绝"""
    from app.auth.abac_models import AbacPolicy
    from app.auth.abac_service import evaluate_abac

    # 插入 deny 策略
    async with abac_db() as db:
        db.add(AbacPolicy(
            name="禁止 operator 发布",
            resource_type="skill",
            action="publish",
            effect="deny",
            priority=10,
            subject_condition={"role": {"in": ["operator"]}},
            enabled=True,
        ))
        await db.commit()

    async with abac_db() as db:
        # operator → 被拒绝
        allowed, reason = await evaluate_abac(
            db, "skill", "publish",
            subject={"role": "operator"},
            resource={},
            environment={},
        )
        assert allowed is False
        assert "policy:" in reason

        # admin → 不匹配该策略 → fail-closed 默认拒绝（C2 安全改动：需要显式 allow 策略）
        allowed2, reason2 = await evaluate_abac(
            db, "skill", "publish",
            subject={"role": "admin"},
            resource={},
            environment={},
        )
        assert allowed2 is False
        assert reason2 == "no_matching_policy"


@pytest.mark.asyncio
async def test_evaluate_abac_allow_matches(abac_db):
    """allow 策略命中 → 放行"""
    from app.auth.abac_models import AbacPolicy
    from app.auth.abac_service import evaluate_abac

    async with abac_db() as db:
        db.add(AbacPolicy(
            name="管理员可导出",
            resource_type="skill",
            action="export",
            effect="allow",
            priority=20,
            subject_condition={"role": "admin"},
            enabled=True,
        ))
        await db.commit()

    async with abac_db() as db:
        allowed, reason = await evaluate_abac(
            db, "skill", "export",
            subject={"role": "admin"},
            resource={},
            environment={},
        )
        assert allowed is True
        assert "policy:" in reason


@pytest.mark.asyncio
async def test_evaluate_abac_priority_ordering(abac_db):
    """高优先级策略优先匹配"""
    from app.auth.abac_models import AbacPolicy
    from app.auth.abac_service import evaluate_abac

    async with abac_db() as db:
        # 低优先级 allow
        db.add(AbacPolicy(
            name="低优先级放行",
            resource_type="skill",
            action="edit",
            effect="allow",
            priority=1,
            subject_condition={"role": "operator"},
            enabled=True,
        ))
        # 高优先级 deny
        db.add(AbacPolicy(
            name="高优先级拒绝",
            resource_type="skill",
            action="edit",
            effect="deny",
            priority=100,
            subject_condition={"role": "operator"},
            resource_condition={"risk_level": "R3"},
            enabled=True,
        ))
        await db.commit()

    async with abac_db() as db:
        # R3 资源 → 高优先级 deny 先命中
        allowed, reason = await evaluate_abac(
            db, "skill", "edit",
            subject={"role": "operator"},
            resource={"risk_level": "R3"},
            environment={},
        )
        assert allowed is False

        # 非 R3 资源 → 高优先级 deny 不匹配，低优先级 allow 命中
        allowed2, reason2 = await evaluate_abac(
            db, "skill", "edit",
            subject={"role": "operator"},
            resource={"risk_level": "R1"},
            environment={},
        )
        assert allowed2 is True


@pytest.mark.asyncio
async def test_evaluate_abac_disabled_policy_ignored(abac_db):
    """禁用的策略不参与评估"""
    from app.auth.abac_models import AbacPolicy
    from app.auth.abac_service import evaluate_abac

    async with abac_db() as db:
        db.add(AbacPolicy(
            name="已禁用策略",
            resource_type="datasource",
            action="read",
            effect="deny",
            priority=999,
            enabled=False,
        ))
        await db.commit()

    async with abac_db() as db:
        # 禁用策略被过滤 → 查询结果为空 → fail-closed
        allowed, reason = await evaluate_abac(
            db, "datasource", "read",
            subject={},
            resource={},
            environment={},
        )
        assert allowed is False
        assert reason == "no_policy_defined"


@pytest.mark.asyncio
async def test_evaluate_abac_environment_condition(abac_db):
    """环境条件评估（工作时间限制）"""
    from app.auth.abac_models import AbacPolicy
    from app.auth.abac_service import evaluate_abac

    async with abac_db() as db:
        db.add(AbacPolicy(
            name="非工作时间禁止执行",
            resource_type="execution",
            action="execute",
            effect="deny",
            priority=10,
            environment_condition={"is_business_hours": False},
            enabled=True,
        ))
        await db.commit()

    async with abac_db() as db:
        # 非工作时间 → deny
        allowed, reason = await evaluate_abac(
            db, "execution", "execute",
            subject={},
            resource={},
            environment={"is_business_hours": False},
        )
        assert allowed is False

        # 工作时间 → 策略不匹配 → fail-closed 默认拒绝（需显式 allow 策略才放行）
        allowed2, reason2 = await evaluate_abac(
            db, "execution", "execute",
            subject={},
            resource={},
            environment={"is_business_hours": True},
        )
        assert allowed2 is False
        assert reason2 == "no_matching_policy"


# ===== JSON Schema 导出测试 =====


def test_condition_json_schema_structure():
    """验证导出的 JSON Schema 结构完整"""
    from app.approval.condition_schema import CONDITION_JSON_SCHEMA
    assert CONDITION_JSON_SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert "oneOf" in CONDITION_JSON_SCHEMA
    assert len(CONDITION_JSON_SCHEMA["oneOf"]) == 2
    assert CONDITION_JSON_SCHEMA["oneOf"][1]["type"] == "null"
    assert "examples" in CONDITION_JSON_SCHEMA
