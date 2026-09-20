"""任务树测试。"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import get_current_user
from app.common.exceptions import AppError, app_error_handler
from app.database import get_db
from app.tasktree.schemas import (
    FailureDiagnosisResponse,
    NodeDetailResponse,
    NodeStatus,
    NODE_STATUS_VALUES,
    RunChainResponse,
    RunChainStep,
    SkillValueResponse,
    SKILL_RUN_STATUS_VALUES,
    TaskTreeDashboardResponse,
    TaskTreeResponse,
    TaskTreeStatsResponse,
)
from app.tasktree.router import router as tasktree_router
from app.tasktree.service import (
    TaskTreeProjection,
    _auth_scope_hash,
    _compute_node_status,
    _compute_user_accessible_departments,
    _heartbeat_ago_text,
    _resolve_effective_department,
    _safe_metric_dept,
    invalidate_tasktree,
    projection,
)
from app.common.time_utils import now_bjt


def _load_migration_module():
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "049_tasktree_indexes.py"
    spec = importlib.util.spec_from_file_location("migration_049_tasktree_indexes", path)
    assert spec and spec.loader, f"无法加载迁移模块: {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_mock_admin():
    user = MagicMock()
    user.id = "admin"
    user.name = "管理员"
    user.username = "admin"
    user.role = "admin"
    user.department = "EC"
    user.can_view_all = True
    user.is_active = True
    user.must_change_password = False
    return user


def _make_mock_user(
    *,
    user_id: str = "u_ec",
    department: str = "EC",
    role: str = "biz_owner",
    can_view_all: bool = False,
):
    user = MagicMock()
    user.id = user_id
    user.name = "业务同学"
    user.username = user_id
    user.role = role
    user.department = department
    user.can_view_all = can_view_all
    user.is_active = True
    user.must_change_password = False
    return user


def _make_rollup_indexes():
    root_id = "demo-root"
    sales = MagicMock(
        id="lv1-sales-1",
        name="销售一部",
        parent_id=root_id,
        path=f"/{root_id}/lv1-sales-1",
        sort_order=1,
    )
    rnd = MagicMock(
        id="lv1-rnd",
        name="研发部",
        parent_id=root_id,
        path=f"/{root_id}/lv1-rnd",
        sort_order=2,
    )
    lv1_by_id = {sales.id: sales, rnd.id: rnd}
    lv1_by_name = {sales.name: sales, rnd.name: rnd}
    lv1_names = set(lv1_by_name)
    org_by_name = dict(lv1_by_name)
    return lv1_by_id, lv1_by_name, lv1_names, org_by_name


class SimpleNamespaceRows:
    """mock sqlalchemy Result：支持 .all() 和 .scalars() 无数据路径。"""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return None

    def scalar(self):
        return None

    def first(self):
        return None

    def mappings(self):
        return self

    def scalars(self):
        return self


@pytest_asyncio.fixture
async def tasktree_api_client():
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.dependency_overrides[get_current_user] = lambda: _make_mock_admin()

    async def _fake_db():
        yield object()

    app.dependency_overrides[get_db] = _fake_db
    app.include_router(tasktree_router, prefix="/api")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        setattr(client, "_tasktree_app", app)
        yield client


class TestNodeStatus:
    def test_online(self):
        inst = MagicMock(is_active=True, last_heartbeat=datetime.utcnow() - timedelta(seconds=30))
        assert _compute_node_status(inst) == NodeStatus.ONLINE

    def test_maybe_offline(self):
        inst = MagicMock(is_active=True, last_heartbeat=datetime.utcnow() - timedelta(seconds=180))
        assert _compute_node_status(inst) == NodeStatus.MAYBE_OFFLINE

    def test_offline_timeout(self):
        inst = MagicMock(is_active=True, last_heartbeat=datetime.utcnow() - timedelta(seconds=600))
        assert _compute_node_status(inst) == NodeStatus.OFFLINE

    def test_offline_inactive(self):
        inst = MagicMock(is_active=False, last_heartbeat=datetime.utcnow())
        assert _compute_node_status(inst) == NodeStatus.OFFLINE

    def test_offline_no_heartbeat(self):
        inst = MagicMock(is_active=True, last_heartbeat=None)
        assert _compute_node_status(inst) == NodeStatus.OFFLINE


class TestHeartbeatText:
    def test_seconds(self):
        now = datetime.utcnow()
        assert _heartbeat_ago_text(now - timedelta(seconds=30), now) == "30s 前"

    def test_minutes(self):
        now = datetime.utcnow()
        assert _heartbeat_ago_text(now - timedelta(minutes=5), now) == "5min 前"

    def test_hours(self):
        now = datetime.utcnow()
        assert _heartbeat_ago_text(now - timedelta(hours=2), now) == "2h 前"

    def test_none(self):
        assert _heartbeat_ago_text(None, datetime.utcnow()) == "未知"


@pytest.mark.asyncio
async def test_projection_assemble_tree_empty():
    instance_projection = TaskTreeProjection()
    tree = instance_projection._assemble_tree([], {})
    assert tree.departments == []
    assert tree.total_online == 0
    assert tree.total_offline == 0
    assert tree.total_running == 0


@pytest.mark.asyncio
async def test_query_instances_filters_inactive_nodes():
    class _Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class _Db:
        statement = None

        async def execute(self, stmt):
            self.statement = stmt
            return _Result()

    db = _Db()
    await TaskTreeProjection()._query_instances(db)

    compiled = str(db.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "openclaw_instances.is_active = true" in compiled


@pytest.mark.asyncio
async def test_query_instances_department_scope_keeps_platform_machines():
    class _Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class _Db:
        statement = None

        async def execute(self, stmt):
            self.statement = stmt
            return _Result()

    db = _Db()
    await TaskTreeProjection()._query_instances(db, departments={"研发部"})

    compiled = str(db.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "openclaw_instances.department IN ('研发部')" in compiled
    assert "openclaw_instances.is_platform_default = true" in compiled
    assert "openclaw_instances.id LIKE 'platform-%'" in compiled


@pytest.mark.asyncio
async def test_tasktree_migration_file_has_expected_indexes():
    migration = _load_migration_module()
    content = (
        Path(__file__).resolve().parents[1] / "migrations" / "versions" / "049_tasktree_indexes.py"
    ).read_text(encoding="utf-8")

    assert migration.revision == "049"
    assert migration.down_revision == "048"
    assert "ix_exec_runs_started_status" in content
    assert "ix_decision_log_created_skill" in content
    assert "ix_decision_requests_run_id" in content
    assert "WHERE run_id IS NOT NULL" in content


@pytest.mark.asyncio
async def test_get_task_tree_api(tasktree_api_client):
    tree = TaskTreeResponse(
        departments=[],
        projected_at="2026-04-13T09:00:00",
        etag="etag-1234",
        total_online=1,
        total_offline=1,
        total_running=2,
    )
    with patch.object(projection, "get_tree", AsyncMock(return_value=tree)) as mocked:
        response = await tasktree_api_client.get("/api/task-tree")

    assert response.status_code == 200
    assert response.headers["etag"] == "etag-1234"
    payload = response.json()
    assert payload["etag"] == "etag-1234"
    assert payload["total_running"] == 2
    mocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_task_tree_department_filter_and_etag(tasktree_api_client):
    tree = TaskTreeResponse(
        departments=[],
        projected_at="2026-04-13T09:00:00",
        etag="etag-ec",
        total_online=1,
        total_offline=0,
        total_running=1,
    )
    with patch.object(projection, "get_tree", AsyncMock(return_value=tree)):
        response = await tasktree_api_client.get(
            "/api/task-tree",
            params={"department": "EC"},
            headers={"If-None-Match": '"etag-ec"'},
        )

    assert response.status_code == 304
    assert response.headers["etag"] == "etag-ec"


@pytest.mark.asyncio
async def test_task_tree_stats_api(tasktree_api_client):
    stats = TaskTreeStatsResponse(
        online_nodes=1,
        total_nodes=2,
        today_executions=4,
        today_success_rate=0.667,
        today_failed=1,
    )
    with patch.object(projection, "get_stats", AsyncMock(return_value=stats)) as mocked:
        response = await tasktree_api_client.get("/api/task-tree/stats")

    assert response.status_code == 200
    assert response.json()["today_success_rate"] == 0.667
    mocked.assert_awaited_once()


class _FakeScheduleScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _FakeScheduleDb:
    def __init__(self, rows):
        self.rows = rows
        self.executed = False

    async def execute(self, stmt):
        self.executed = True
        return _FakeScheduleScalarResult(self.rows)


@pytest.mark.asyncio
async def test_task_tree_stats_counts_schedules_due_soon():
    tp = TaskTreeProjection()
    db = _FakeScheduleDb(["* * * * *", "0 16 * * *", "bad cron"])

    count = await tp._count_schedules_due_soon(
        db,
        instance_ids=["node-1"],
        now=datetime(2026, 5, 25, 14, 28, 0),
    )

    assert count == 1
    assert db.executed is True


@pytest.mark.asyncio
async def test_task_tree_stats_due_soon_skips_empty_instance_scope():
    tp = TaskTreeProjection()
    db = _FakeScheduleDb(["* * * * *"])

    count = await tp._count_schedules_due_soon(
        db,
        instance_ids=[],
        now=datetime(2026, 5, 25, 14, 28, 0),
    )

    assert count == 0
    assert db.executed is False


@pytest.mark.asyncio
async def test_task_tree_dashboard_api(tasktree_api_client):
    dashboard = TaskTreeDashboardResponse(
        department="EC",
        period_days=30,
        executions=12,
        saved_hours=18.5,
        token_cost=230.0,
        roi=4.2,
        top_skills=[{"skill_id": "skill-ec", "executions": 8}],
    )
    with patch.object(projection, "get_dashboard", AsyncMock(return_value=dashboard)) as mocked:
        response = await tasktree_api_client.get("/api/task-tree/dashboard", params={"department": "EC"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["department"] == "EC"
    assert payload["roi"] == 4.2
    mocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_task_tree_node_detail_api(tasktree_api_client):
    detail = NodeDetailResponse(
        instance_id="inst-ec-1",
        name="OpenClaw-EC-1",
        department="EC",
        agent_type="aiclaw",
        node_status=NodeStatus.ONLINE,
        active_runs=[],
        recent_completed=[],
    )
    with patch.object(projection, "get_node_detail", AsyncMock(return_value=detail)) as mocked:
        response = await tasktree_api_client.get("/api/task-tree/node/inst-ec-1")

    assert response.status_code == 200
    assert response.json()["instance_id"] == "inst-ec-1"
    mocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_task_tree_run_chain_api(tasktree_api_client):
    chain = RunChainResponse(
        run_id="run-ec-completed",
        skill_id="skill-ec",
        skill_name="EC 日报",
        chain=[
            RunChainStep(type="execution", id="run-ec-completed", status="completed"),
            RunChainStep(type="decision", id=1, status="approved"),
            RunChainStep(type="request", id="dr-1", status="approved"),
            RunChainStep(type="todo", id=1, status="approved"),
            RunChainStep(type="dispatch", id=1, status="done"),
        ],
        chain_complete=True,
    )
    with patch.object(projection, "get_run_chain", AsyncMock(return_value=chain)) as mocked:
        response = await tasktree_api_client.get("/api/task-tree/run/run-ec-completed/chain")

    assert response.status_code == 200
    payload = response.json()
    assert payload["chain_complete"] is True
    assert [item["type"] for item in payload["chain"]] == ["execution", "decision", "request", "todo", "dispatch"]
    mocked.assert_awaited_once()


@pytest.mark.asyncio
async def test_task_tree_run_chain_includes_training_job_summary(client):
    from app.database import async_session_factory
    from app.execution.models import ExecutionRun
    from app.learning.models import LearningArtifact, LearningFlowEdge
    from app.skills.core.models import Skill
    from app.training.models import TrainingJob, TrainingJobTask

    now = now_bjt()
    async with async_session_factory() as session:
        session.add(Skill(id="skill-train-chain", name="训练链路 Skill", department="EC", status="active"))
        session.add(ExecutionRun(
            id="run-train-chain",
            skill_id="skill-train-chain",
            status="completed",
            summary="已产出训练样本",
            started_at=now - timedelta(minutes=5),
            completed_at=now - timedelta(minutes=4),
            source_instance_id="node-train",
        ))
        artifact_ids = ["la-chain-1", "la-chain-2"]
        for index, artifact_id in enumerate(artifact_ids):
            session.add(LearningArtifact(
                id=artifact_id,
                event_id=f"le-chain-{index}",
                artifact_kind="training_sample",
                artifact_hash=f"hash-chain-{index}",
                target_type="skill",
                target_id="skill-train-chain",
                department="EC",
                skill_id="skill-train-chain",
                run_id="run-train-chain",
                title=f"样本 {index}",
                summary="训练样本摘要",
                content_json={},
                labels_json=[],
                quality_score=0.8,
                confidence=0.7,
                status="materialized",
                sink_type="training_sample",
                created_at=now - timedelta(days=1, minutes=index),
            ))
            session.add(LearningFlowEdge(
                from_type="learning_artifact",
                from_id=artifact_id,
                to_type="training_job",
                to_id="tj-chain-1",
                relation="trained_from",
                department="EC",
                skill_id="skill-train-chain",
            ))
        session.add(TrainingJob(
            id="tj-chain-1",
            title="每日增量训练",
            department="EC",
            created_by="learning_auto_flow",
            status="running",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="skill-train-chain",
            target_gateway_id="node-train",
            dataset_ref="learning-artifacts://skill-train-chain/training/yesterday/2026-06-23",
            approved_by="learning_training_reviewer",
            approved_at=now,
            spec_json={
                "training_mode": "daily_incremental",
                "dataset": {
                    "sample_total": 4,
                    "eval_count": 1,
                    "window": {
                        "date": "2026-06-23",
                        "start": "2026-06-23T00:00:00+08:00",
                        "end": "2026-06-24T00:00:00+08:00",
                        "timezone": "Asia/Shanghai",
                    },
                },
                "deployment": {"model_family": "skill-train-chain:learning-loop-adapter"},
                "parent_model": {
                    "model_deployment_id": "deploy-parent",
                    "model_family": "skill-train-chain:qwen3.5-4b-qlora",
                    "artifact_id": "5:0",
                },
            },
            gateway_payload_json={
                "dataset_package": {
                    "sample_count": 4,
                    "train_count": 3,
                    "eval_count": 1,
                }
            },
        ))
        session.add(TrainingJobTask(
            job_id="tj-chain-1",
            gateway_id="node-train",
            worker_id="node-train",
            status="running",
            progress=0.42,
            metrics_json={"loss": 0.1},
        ))
        await session.commit()

    response = await client.get("/api/task-tree/run/run-train-chain/chain")
    assert response.status_code == 200
    payload = response.json()
    assert payload["training_jobs"]
    item = payload["training_jobs"][0]
    assert item["job_id"] == "tj-chain-1"
    assert item["status"] == "running"
    assert item["relation"] == "trained_from"
    assert item["training_mode"] == "daily_incremental"
    assert item["dataset_window_date"] == "2026-06-23"
    assert item["sample_count"] == 4
    assert item["source_sample_count"] == 2
    assert item["latest_task_status"] == "running"
    assert item["latest_task_progress"] == 0.42
    assert item["parent_model_deployment_id"] == "deploy-parent"


@pytest.mark.asyncio
async def test_task_tree_run_chain_marks_materialized_samples_pending_daily_training(client):
    from app.database import async_session_factory
    from app.execution.models import ExecutionRun
    from app.learning.models import LearningArtifact
    from app.skills.core.models import Skill

    now = now_bjt()
    async with async_session_factory() as session:
        session.add(Skill(id="skill-pending-training", name="待训练 Skill", department="EC", status="active"))
        session.add(ExecutionRun(
            id="run-pending-training",
            skill_id="skill-pending-training",
            status="completed",
            summary="今天产出训练样本",
            started_at=now - timedelta(minutes=5),
            completed_at=now - timedelta(minutes=4),
        ))
        for index in range(2):
            session.add(LearningArtifact(
                id=f"la-pending-{index}",
                event_id=f"le-pending-{index}",
                artifact_kind="training_sample",
                artifact_hash=f"hash-pending-{index}",
                target_type="skill",
                target_id="skill-pending-training",
                department="EC",
                skill_id="skill-pending-training",
                run_id="run-pending-training",
                title=f"待训练样本 {index}",
                summary="待日批训练",
                content_json={},
                labels_json=[],
                quality_score=0.8,
                confidence=0.7,
                status="materialized",
                sink_type="training_sample",
                created_at=now,
            ))
        await session.commit()

    response = await client.get("/api/task-tree/run/run-pending-training/chain")
    assert response.status_code == 200
    item = response.json()["training_jobs"][0]
    assert item["job_id"] is None
    assert item["status"] == "pending_daily_training"
    assert item["relation"] == "pending_daily_training"
    assert item["target_skill_id"] == "skill-pending-training"
    assert item["sample_count"] == 2
    assert item["planned_training_after"] is not None


@pytest.mark.asyncio
async def test_task_tree_run_chain_hides_legacy_unreviewed_training_latest_candidates(client):
    from app.database import async_session_factory
    from app.execution.models import ExecutionRun
    from app.learning.models import LearningArtifact, LearningFlowEdge
    from app.skills.core.models import Skill
    from app.training.models import TrainingJob

    now = now_bjt()
    async with async_session_factory() as session:
        session.add(Skill(id="skill-legacy-training", name="旧候选 Skill", department="EC", status="active"))
        session.add(ExecutionRun(
            id="run-legacy-training",
            skill_id="skill-legacy-training",
            status="completed",
            summary="旧候选污染训练摘要",
            started_at=now - timedelta(minutes=8),
            completed_at=now - timedelta(minutes=7),
        ))
        for index in range(2):
            artifact = LearningArtifact(
                id=f"la-legacy-{index}",
                event_id=f"le-legacy-{index}",
                artifact_kind="training_sample",
                artifact_hash=f"hash-legacy-{index}",
                target_type="skill",
                target_id="skill-legacy-training",
                department="EC",
                skill_id="skill-legacy-training",
                run_id="run-legacy-training",
                title=f"训练样本 {index}",
                summary="训练样本",
                content_json={},
                labels_json=[],
                quality_score=0.8,
                confidence=0.7,
                status="materialized",
                sink_type="training_sample",
                created_at=now,
            )
            session.add(artifact)
            session.add(LearningFlowEdge(
                from_type="learning_artifact",
                from_id=artifact.id,
                to_type="training_job",
                to_id="tj-legacy-latest",
                relation="trained_from",
            ))
        session.add(TrainingJob(
            id="tj-legacy-latest",
            title="旧 latest 自动训练候选",
            department="EC",
            created_by="learning_auto_flow",
            status="awaiting_review",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="skill-legacy-training",
            dataset_ref="learning-artifacts://skill-legacy-training/training/latest",
            spec_json={"source": "learning_sample_threshold", "dataset": {"sample_total": 20}},
        ))
        await session.commit()

    response = await client.get("/api/task-tree/run/run-legacy-training/chain")
    assert response.status_code == 200
    training_jobs = response.json()["training_jobs"]
    assert [item["status"] for item in training_jobs] == ["pending_daily_training"]
    assert training_jobs[0]["target_skill_id"] == "skill-legacy-training"


@pytest.mark.asyncio
async def test_task_tree_diagnose_api(tasktree_api_client):
    chain = RunChainResponse(run_id="run-1", skill_id="skill-1", chain=[], chain_complete=False)
    with patch.object(projection, "get_run_chain", AsyncMock(return_value=chain)) as mocked_chain:
        with patch("app.tasktree.router.diagnose_failure", AsyncMock(return_value="诊断完成")) as mocked_diag:
            response = await tasktree_api_client.get("/api/task-tree/run/run-1/diagnose")

    assert response.status_code == 200
    assert response.json() == FailureDiagnosisResponse(run_id="run-1", diagnosis="诊断完成").model_dump(mode="json")
    mocked_chain.assert_awaited_once()
    mocked_diag.assert_awaited_once()


@pytest.mark.asyncio
async def test_task_tree_abac_rejects_cross_department(tasktree_api_client):
    """P0-6：属于 EC 的 biz_owner 请求 AM 部门 → 期望 403。"""
    app = getattr(tasktree_api_client, "_tasktree_app")
    ec_user = _make_mock_user(user_id="u_ec", department="EC", role="biz_owner")
    app.dependency_overrides[get_current_user] = lambda: ec_user

    async def _fake_db():
        # 用 AsyncMock 模拟 select(Skill.department)...join... ABAC 白名单扫描
        session = MagicMock()

        async def _execute(*_args, **_kwargs):
            return SimpleNamespaceRows([])

        session.execute = _execute
        yield session

    app.dependency_overrides[get_db] = _fake_db

    response = await tasktree_api_client.get("/api/task-tree", params={"department": "AM"})
    assert response.status_code == 403
    payload = response.json()
    # app_error_handler 返回 {"error": {"code": ..., "message": ...}}
    code = (payload.get("error") or {}).get("code") or payload.get("code")
    assert code == "AUTH_DEPARTMENT_DENIED"

    # 恢复
    app.dependency_overrides[get_current_user] = lambda: _make_mock_admin()
    app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_auth_scope_hash_differs_by_role():
    """P0-6：同部门但 role 不同 → scope_hash 必须不同，避免缓存串读。"""
    biz = _make_mock_user(role="biz_owner", department="EC")
    ops = _make_mock_user(role="operator", department="EC")
    assert _auth_scope_hash(current_user=biz, member_departments=set(), is_admin=False) != \
        _auth_scope_hash(current_user=ops, member_departments=set(), is_admin=False)


@pytest.mark.asyncio
async def test_auth_scope_hash_differs_by_member_departments():
    """成员关联部门不同 → scope_hash 不同，保证跨 skill_member 代管隔离。"""
    u1 = _make_mock_user(role="biz_owner", department="EC")
    u2 = _make_mock_user(role="biz_owner", department="EC")
    h1 = _auth_scope_hash(current_user=u1, member_departments={"AM"}, is_admin=False)
    h2 = _auth_scope_hash(current_user=u2, member_departments={"CRM"}, is_admin=False)
    assert h1 != h2


@pytest.mark.asyncio
async def test_invalidate_tasktree_department_scoped(monkeypatch):
    """P0-3：invalidate_tasktree(department) 只清该部门 + etag，不得连带其他部门。"""
    from app.tasktree import service as svc_mod

    deleted: list[str] = []

    async def fake_delete_pattern(pattern: str) -> int:
        deleted.append(pattern)
        return 0

    monkeypatch.setattr(svc_mod, "cache_delete_pattern", fake_delete_pattern)
    await invalidate_tasktree(department="EC")

    # 必须包含按部门 sharded 的 pattern，且不得是全部门的 * 通配
    assert any(p.startswith("tasktree:tree:EC:") for p in deleted)
    assert any(p.startswith("tasktree:stats:EC:") for p in deleted)
    assert any(p.startswith("tasktree:etag:EC:") for p in deleted)
    # 不应出现 tree:* 这种打穿全域的 pattern（department 传入时）
    assert not any(p == "tasktree:tree:*" for p in deleted)


@pytest.mark.asyncio
async def test_invalidate_tasktree_global_fallback(monkeypatch):
    """department=None 时仍全清，保留兜底能力。"""
    from app.tasktree import service as svc_mod

    deleted: list[str] = []

    async def fake_delete_pattern(pattern: str) -> int:
        deleted.append(pattern)
        return 0

    monkeypatch.setattr(svc_mod, "cache_delete_pattern", fake_delete_pattern)
    await invalidate_tasktree(department=None)
    assert "tasktree:tree:*" in deleted
    assert "tasktree:stats:*" in deleted


@pytest.mark.asyncio
async def test_get_tree_respects_use_tnl_read_flag(monkeypatch):
    """P0-4：USE_TNL_READ=True 和 False 返回 schema 一致。"""
    from app.tasktree import service as svc_mod

    empty = TaskTreeResponse(departments=[], projected_at="x", etag="e")
    calls = {"tnl": 0, "legacy": 0}

    async def fake_tnl(self, db, **kw):
        calls["tnl"] += 1
        return empty

    async def fake_units(self, db, deps):
        return []

    async def fake_instances(self, db, *, departments=None):
        return []

    async def fake_recent(self, db, instances, **kw):
        calls["legacy"] += 1
        return {}, {}

    async def fake_member(self, db, *, current_user, is_admin):
        return set(), set()

    async def fake_resolve(self, db, department):
        return department

    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_get_tree_from_tnl", fake_tnl)
    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_query_department_units", fake_units)
    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_query_instances", fake_instances)
    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_query_recent_runs", fake_recent)
    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_get_member_scope", fake_member)
    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_resolve_department_name", fake_resolve)
    monkeypatch.setattr(svc_mod, "_load_org_indexes", AsyncMock(return_value=_make_rollup_indexes()))

    async def fake_cache_get(key):
        return None

    async def fake_cache_set(key, value, **kw):
        return None

    monkeypatch.setattr(svc_mod, "cache_get", fake_cache_get)
    monkeypatch.setattr(svc_mod, "cache_set", fake_cache_set)

    # USE_TNL_READ=True
    monkeypatch.setattr(svc_mod.settings, "USE_TNL_READ", True)
    tp = svc_mod.TaskTreeProjection()
    tree_tnl = await tp.get_tree(db=object(), current_user=_make_mock_admin(), is_admin=True)
    assert calls["tnl"] == 1

    # USE_TNL_READ=False → 走旧路径
    monkeypatch.setattr(svc_mod.settings, "USE_TNL_READ", False)
    tree_legacy = await tp.get_tree(db=object(), current_user=_make_mock_admin(), is_admin=True)
    assert calls["legacy"] == 1

    # schema 形状一致（字段集合）
    assert set(tree_tnl.model_dump().keys()) == set(tree_legacy.model_dump().keys())


def test_enum_exports_align_with_enum_values():
    assert set(NODE_STATUS_VALUES) == {"online", "maybe_offline", "offline"}
    assert set(SKILL_RUN_STATUS_VALUES) == {"running", "completed", "failed", "idle", "queued"}


def test_resolve_effective_department_admin_pass_through():
    """[B1] admin 任何 requested 都原样放行（由 router 做审计）。"""
    assert _resolve_effective_department(
        user_department="EC",
        requested_department="SEM",
        allowed_departments=None,
        is_admin=True,
    ) == "SEM"
    assert _resolve_effective_department(
        user_department="EC",
        requested_department=None,
        allowed_departments=None,
        is_admin=True,
    ) is None


def test_resolve_effective_department_non_admin_with_requested_in_allowed():
    """[B1] 非 admin 请求自己部门/代管部门 → 保留 requested。"""
    assert _resolve_effective_department(
        user_department="EC",
        requested_department="EC",
        allowed_departments={"EC", "CRM"},
        is_admin=False,
    ) == "EC"
    assert _resolve_effective_department(
        user_department="EC",
        requested_department="CRM",
        allowed_departments={"EC", "CRM"},
        is_admin=False,
    ) == "CRM"


def test_resolve_effective_department_non_admin_requests_forbidden_dept_falls_back():
    """[B1] 非 admin 传不在白名单的部门 → service 层 fallback 到自己 user_department。"""
    # 场景：router 被绕过、或测试直接调 service，必须兜底不越权
    assert _resolve_effective_department(
        user_department="EC",
        requested_department="AM",
        allowed_departments={"EC"},  # 白名单里没有 AM
        is_admin=False,
    ) == "EC"


def test_resolve_effective_department_non_admin_without_requested_uses_user_dept():
    """[B1] 非 admin 不传 requested → 沿用 user_department。"""
    assert _resolve_effective_department(
        user_department="EC",
        requested_department=None,
        allowed_departments={"EC"},
        is_admin=False,
    ) == "EC"


def test_compute_user_accessible_departments():
    """[m3] user.department ∪ member_departments，忽略 None/空字符串。"""
    assert _compute_user_accessible_departments(
        user_department="EC",
        member_departments={"CRM", ""},
    ) == {"EC", "CRM"}
    assert _compute_user_accessible_departments(
        user_department=None,
        member_departments=None,
    ) == set()


@pytest.mark.asyncio
async def test_get_tree_non_admin_bypass_forbidden_department_falls_back(monkeypatch):
    """[B1] 非 admin 直接调 projection.get_tree 请求其他 Lv1 → 返回空树，不走越权部门缓存。

    归并后权限语义按 Lv1 收口；当请求的 Lv1 不等于用户所属 Lv1 时，
    service 层必须在 router 被绕过的情况下仍然返回空树。
    """
    from app.tasktree import service as svc_mod

    tree_payload = TaskTreeResponse(
        departments=[],
        projected_at="2026-04-14T00:00:00",
        etag="etag-ec",
        total_online=0,
        total_offline=0,
        total_running=0,
    )

    captured: dict = {}

    async def fake_tnl(self, db, **kw):
        # 抓取真实传给下游的 requested_department / accessible_departments
        captured.update(kw)
        return tree_payload

    async def fake_units(self, db, deps):
        return []

    async def fake_instances(self, db, *, departments=None):
        return []

    async def fake_recent(self, db, instances, **kw):
        return {}, {}

    async def fake_member(self, db, *, current_user, is_admin):
        # 用户没有任何跨部门代管
        return set(), set()

    async def fake_resolve(self, db, department):
        return department

    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_get_tree_from_tnl", fake_tnl)
    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_query_department_units", fake_units)
    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_query_instances", fake_instances)
    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_query_recent_runs", fake_recent)
    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_get_member_scope", fake_member)
    monkeypatch.setattr(svc_mod.TaskTreeProjection, "_resolve_department_name", fake_resolve)
    monkeypatch.setattr(svc_mod, "_load_org_indexes", AsyncMock(return_value=_make_rollup_indexes()))

    # 捕获最终写入的缓存 key，断言 dept_slot 不是"越权"的 AM
    cache_keys: list[str] = []

    async def fake_cache_get(key):
        return None

    async def fake_cache_set(key, value, **kw):
        cache_keys.append(key)

    monkeypatch.setattr(svc_mod, "cache_get", fake_cache_get)
    monkeypatch.setattr(svc_mod, "cache_set", fake_cache_set)
    monkeypatch.setattr(svc_mod.settings, "USE_TNL_READ", True)

    tp = svc_mod.TaskTreeProjection()
    sales_user = _make_mock_user(user_id="u_sales", department="销售一部", role="biz_owner")
    # 模拟攻击者传了“研发部”（不是自己的 Lv1）
    tree = await tp.get_tree(
        db=object(),
        department="研发部",
        user_department="销售一部",
        current_user=sales_user,
        is_admin=False,
    )

    assert tree.departments == []
    assert captured == {}
    assert not any(key.startswith("tasktree:tree:") for key in cache_keys)


def test_safe_metric_dept_none_and_all():
    """[M3] None / 空 → 'all'；'all' 原样保留。"""
    assert _safe_metric_dept(None) == "all"
    assert _safe_metric_dept("") == "all"
    assert _safe_metric_dept("all") == "all"


def test_safe_metric_dept_whitelist_filters_unknown(monkeypatch):
    """[M3] 白名单加载后，未命中的 dept → 'unknown'（防基数爆炸）。"""
    from app.tasktree import service as svc_mod

    monkeypatch.setattr(svc_mod, "_METRIC_DEPT_WHITELIST", {"EC", "CRM"})
    monkeypatch.setattr(svc_mod, "_METRIC_DEPT_WHITELIST_LOADED", True)

    assert _safe_metric_dept("EC") == "EC"
    assert _safe_metric_dept("CRM") == "CRM"
    assert _safe_metric_dept("random_fake_dept") == "unknown"


def test_safe_metric_dept_whitelist_not_loaded_passthrough(monkeypatch):
    """[M3] 白名单未加载 → 透传，避免开发期 metric 丢失。"""
    from app.tasktree import service as svc_mod

    monkeypatch.setattr(svc_mod, "_METRIC_DEPT_WHITELIST", set())
    monkeypatch.setattr(svc_mod, "_METRIC_DEPT_WHITELIST_LOADED", False)

    assert _safe_metric_dept("EC") == "EC"
    assert _safe_metric_dept("some_unknown_dept") == "some_unknown_dept"


@pytest.mark.asyncio
async def test_task_tree_skill_value_api(tasktree_api_client):
    fake_db = MagicMock()
    fake_db.scalar = AsyncMock(return_value="EC")

    async def _fake_db():
        yield fake_db

    app = getattr(tasktree_api_client, "_tasktree_app")
    app.dependency_overrides[get_db] = _fake_db
    with patch("app.tasktree.router.estimate_value", AsyncMock(return_value={
        "saved_hours": 9.5,
        "estimated_cost_saving": 320.0,
        "risk_events_prevented": 2,
        "recommendation": "继续投入",
    })) as mocked_value:
        response = await tasktree_api_client.get("/api/task-tree/skill/skill-1/value")

    assert response.status_code == 200
    assert response.json() == SkillValueResponse(
        skill_id="skill-1",
        period_days=30,
        saved_hours=9.5,
        estimated_cost_saving=320.0,
        risk_events_prevented=2,
        recommendation="继续投入",
    ).model_dump(mode="json")
    mocked_value.assert_awaited_once()
    app.dependency_overrides.pop(get_db, None)
