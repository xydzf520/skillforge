"""执行任务队列单元测试"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.config import settings


def _make_mock_admin():
    user = MagicMock()
    user.id = "admin"
    user.name = "管理员"
    user.username = "admin"
    user.role = "admin"
    user.department = "AI小组"
    user.can_view_all = True
    user.is_active = True
    user.must_change_password = False
    user.dingtalk_user_id = None
    user.avatar_url = None
    return user


def _import_all_models():
    """确保所有 ORM 模型注册到 Base.metadata（避免 FK 依赖顺序问题）"""
    import app.skills.models  # noqa: F401
    import app.auth.models  # noqa: F401
    import app.reviews.models  # noqa: F401
    import app.execution.models  # noqa: F401
    import app.execution.queue_models  # noqa: F401
    import app.datasources.models  # noqa: F401
    import app.dingtalk.models  # noqa: F401
    import app.common.models  # noqa: F401
    import app.common.audit  # noqa: F401
    import app.workbench.models  # noqa: F401
    import app.optimizer.models  # noqa: F401
    import app.testing.models  # noqa: F401
    import app.todos.models  # noqa: F401


async def _setup_test_db():
    """创建测试数据库表（使用 DROP CASCADE 解决 FK 依赖）"""
    import re
    import app.database as db_mod
    from app.database import Base

    _import_all_models()

    test_url = getattr(settings, 'DATABASE_URL_TEST', None) or re.sub(
        r'/skillforge$', '/skillforge_test', str(settings.DATABASE_URL))
    eng = create_async_engine(test_url, echo=False)
    fac = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)

    # 使用 raw SQL DROP CASCADE 避免 FK 依赖问题
    async with eng.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)

    orig_engine, orig_factory = db_mod.engine, db_mod.async_session_factory
    db_mod.engine = eng
    db_mod.async_session_factory = fac

    return eng, fac, orig_engine, orig_factory


@pytest_asyncio.fixture
async def queue_client():
    """独立的测试客户端，包含 execution_queue 表"""
    import app.database as db_mod
    from app.common.exceptions import AppError, app_error_handler
    from app.auth.dependencies import get_current_user
    from fastapi import FastAPI

    eng, fac, orig_engine, orig_factory = await _setup_test_db()

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    mock_admin = _make_mock_admin()
    app.dependency_overrides[get_current_user] = lambda: mock_admin

    from app.execution.queue_router import router as queue_router
    app.include_router(queue_router, prefix="/api/executions")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    await eng.dispose()
    db_mod.engine, db_mod.async_session_factory = orig_engine, orig_factory


# ────────────────────────────────────────
# TaskQueue 单元测试（直接调用 service 层）
# ────────────────────────────────────────

@pytest_asyncio.fixture
async def setup_db():
    """初始化测试数据库（仅 ORM 层，不启动 HTTP）"""
    import app.database as db_mod

    eng, fac, orig_engine, orig_factory = await _setup_test_db()

    yield fac

    await eng.dispose()
    db_mod.engine, db_mod.async_session_factory = orig_engine, orig_factory


@pytest.mark.asyncio
async def test_enqueue_and_claim(setup_db):
    """入队 → 领取 → 完成：完整生命周期"""
    from app.execution.task_queue import task_queue

    # 入队
    task_id = await task_queue.enqueue(
        task_type="skill_run",
        payload={"skill_id": "test-001", "params": {}},
        skill_id="test-001",
        priority=3,
    )
    assert task_id > 0

    # 领取
    claimed = await task_queue.claim(worker_id="worker-1")
    assert claimed is not None
    assert claimed["id"] == task_id
    assert claimed["task_type"] == "skill_run"
    assert claimed["payload"]["skill_id"] == "test-001"

    # 再次领取应该返回 None（已被领取）
    claimed2 = await task_queue.claim(worker_id="worker-2")
    assert claimed2 is None

    # 完成
    await task_queue.complete(task_id, result={"output": "success"})

    # 验证状态
    stats = await task_queue.get_queue_stats()
    assert stats["completed"] == 1
    assert stats["pending"] == 0


@pytest.mark.asyncio
async def test_priority_ordering(setup_db):
    """优先级排序：低优先级数字 = 高优先级"""
    from app.execution.task_queue import task_queue

    # 入队 3 个不同优先级的任务
    id_low = await task_queue.enqueue(task_type="skill_run", payload={"p": "low"}, priority=8)
    id_high = await task_queue.enqueue(task_type="skill_run", payload={"p": "high"}, priority=1)
    id_mid = await task_queue.enqueue(task_type="skill_run", payload={"p": "mid"}, priority=5)

    # 领取顺序应该是 high → mid → low
    c1 = await task_queue.claim(worker_id="w1")
    assert c1["id"] == id_high

    c2 = await task_queue.claim(worker_id="w2")
    assert c2["id"] == id_mid

    c3 = await task_queue.claim(worker_id="w3")
    assert c3["id"] == id_low


@pytest.mark.asyncio
async def test_fail_and_retry(setup_db):
    """失败后重试：未达上限时重置为 pending"""
    from app.execution.task_queue import task_queue

    task_id = await task_queue.enqueue(
        task_type="test_run",
        payload={"test": True},
        max_retries=2,
    )

    # 第一次领取 + 失败
    claimed = await task_queue.claim(worker_id="w1")
    assert claimed["id"] == task_id
    await task_queue.fail(task_id, error="连接超时")

    # 应该重新变为 pending（retry_count=1 < max_retries=2）
    stats = await task_queue.get_queue_stats()
    assert stats["pending"] == 1
    assert stats["failed"] == 0

    # 第二次领取 + 失败
    claimed2 = await task_queue.claim(worker_id="w2")
    assert claimed2 is not None
    await task_queue.fail(task_id, error="再次超时")

    # 达到 max_retries，标记为最终失败
    stats2 = await task_queue.get_queue_stats()
    assert stats2["failed"] == 1
    assert stats2["pending"] == 0


@pytest.mark.asyncio
async def test_fail_with_retry_delay_waits_until_due(setup_db):
    """延迟重试：next_attempt_at 未到时不应被 claim。"""
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.execution.queue_models import ExecutionQueueTask
    from app.execution.task_queue import task_queue

    task_id = await task_queue.enqueue(
        task_type="test_run",
        payload={"test": True},
        max_retries=3,
    )

    claimed = await task_queue.claim(worker_id="w1")
    assert claimed["id"] == task_id
    await task_queue.fail(task_id, error="bridge offline", retry_delay_seconds=600)

    assert await task_queue.claim(worker_id="w2") is None

    async with async_session_factory() as session:
        await session.execute(
            update(ExecutionQueueTask)
            .where(ExecutionQueueTask.id == task_id)
            .values(next_attempt_at=now_bjt() - timedelta(seconds=1))
        )
        await session.commit()

    claimed_again = await task_queue.claim(worker_id="w3")
    assert claimed_again is not None
    assert claimed_again["id"] == task_id


@pytest.mark.asyncio
async def test_cancel(setup_db):
    """取消 pending 和 running 状态的任务"""
    from app.execution.task_queue import task_queue

    # 取消 pending 任务
    task_id = await task_queue.enqueue(task_type="skill_run", payload={})
    await task_queue.cancel(task_id)
    stats = await task_queue.get_queue_stats()
    assert stats["cancelled"] == 1

    # 取消 running 任务
    task_id2 = await task_queue.enqueue(task_type="skill_run", payload={})
    await task_queue.claim(worker_id="w1")
    await task_queue.cancel(task_id2)
    stats2 = await task_queue.get_queue_stats()
    assert stats2["cancelled"] == 2


@pytest.mark.asyncio
async def test_queue_name_isolation(setup_db):
    """不同队列之间的任务隔离"""
    from app.execution.task_queue import task_queue

    await task_queue.enqueue(task_type="skill_run", payload={}, queue_name="default")
    await task_queue.enqueue(task_type="test_run", payload={}, queue_name="test")

    # 从 default 队列领取
    claimed = await task_queue.claim(worker_id="w1", queue_name="default")
    assert claimed is not None
    assert claimed["task_type"] == "skill_run"

    # default 队列已空
    claimed2 = await task_queue.claim(worker_id="w2", queue_name="default")
    assert claimed2 is None

    # test 队列仍有任务
    claimed3 = await task_queue.claim(worker_id="w3", queue_name="test")
    assert claimed3 is not None
    assert claimed3["task_type"] == "test_run"


@pytest.mark.asyncio
async def test_reclaim_stale(setup_db):
    """超时回收：running 超时的任务被重置"""
    from app.execution.task_queue import task_queue
    from app.execution.queue_models import ExecutionQueueTask

    task_id = await task_queue.enqueue(
        task_type="skill_run",
        payload={},
        timeout_seconds=10,  # 10 秒超时
        max_retries=2,
    )

    # 领取任务
    await task_queue.claim(worker_id="w1")

    # 手动把 started_at 改为很久之前（模拟超时）
    from app.database import async_session_factory
    async with async_session_factory() as session:
        await session.execute(
            update(ExecutionQueueTask)
            .where(ExecutionQueueTask.id == task_id)
            .values(started_at=datetime.utcnow() - timedelta(hours=1))
        )
        await session.commit()

    # 回收
    reclaimed = await task_queue.reclaim_stale(timeout_margin=0)
    assert reclaimed == 1

    # 任务应该重新变为 pending（首次超时，retry_count < max_retries）
    stats = await task_queue.get_queue_stats()
    assert stats["pending"] == 1


@pytest.mark.asyncio
async def test_list_tasks(setup_db):
    """分页查询任务列表"""
    from app.execution.task_queue import task_queue

    for i in range(5):
        await task_queue.enqueue(task_type="skill_run", payload={"i": i})

    result = await task_queue.list_tasks(page=1, page_size=3)
    assert result["total"] == 5
    assert len(result["items"]) == 3
    assert result["page"] == 1

    result2 = await task_queue.list_tasks(page=2, page_size=3)
    assert len(result2["items"]) == 2


# ────────────────────────────────────────
# ExecutionScheduler 单元测试
# ────────────────────────────────────────

@pytest.mark.asyncio
async def test_scheduler_routes_to_correct_queue(setup_db):
    """调度器按 task_type 路由到正确的队列"""
    from app.execution.exec_scheduler import exec_scheduler
    from app.execution.task_queue import task_queue

    task_id = await exec_scheduler.schedule(
        task_type="test_run",
        payload={"test": True},
        skill_id="test-skill",
    )
    assert task_id > 0

    # 应该在 test 队列
    stats = await task_queue.get_queue_stats(queue_name="test")
    assert stats["pending"] == 1

    # default 队列应为空
    stats_default = await task_queue.get_queue_stats(queue_name="default")
    assert stats_default["pending"] == 0


@pytest.mark.asyncio
async def test_scheduler_unknown_type_defaults(setup_db):
    """未知 task_type 默认路由到 default 队列"""
    from app.execution.exec_scheduler import exec_scheduler
    from app.execution.task_queue import task_queue

    task_id = await exec_scheduler.schedule(
        task_type="unknown_type",
        payload={"data": 1},
    )

    stats = await task_queue.get_queue_stats(queue_name="default")
    assert stats["pending"] == 1


# ────────────────────────────────────────
# HTTP API 测试
# ────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_queue_stats(queue_client):
    """GET /api/executions/queue/stats — 获取队列统计"""
    resp = await queue_client.get("/api/executions/queue/stats")
    assert resp.status_code == 200
    data = resp.json()
    # 全局统计返回所有队列
    assert "_global" in data


@pytest.mark.asyncio
async def test_api_queue_stats_single_queue(queue_client):
    """GET /api/executions/queue/stats?queue_name=default — 单队列统计"""
    resp = await queue_client.get("/api/executions/queue/stats?queue_name=default")
    assert resp.status_code == 200
    data = resp.json()
    assert "pending" in data
    assert "running" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_api_queue_tasks(queue_client):
    """GET /api/executions/queue/tasks — 任务列表"""
    # 先入队一个任务
    from app.execution.task_queue import task_queue
    await task_queue.enqueue(task_type="skill_run", payload={"test": True})

    resp = await queue_client.get("/api/executions/queue/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert len(data["items"]) >= 1


@pytest.mark.asyncio
async def test_api_cancel_task(queue_client):
    """POST /api/executions/queue/tasks/{id}/cancel — 取消任务"""
    from app.execution.task_queue import task_queue
    task_id = await task_queue.enqueue(task_type="skill_run", payload={})

    resp = await queue_client.post(f"/api/executions/queue/tasks/{task_id}/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_api_reclaim_stale(queue_client):
    """POST /api/executions/queue/reclaim-stale — 手动回收"""
    resp = await queue_client.post("/api/executions/queue/reclaim-stale")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "reclaimed" in data


# === M5：complete() 校验 claimed_by ===


@pytest.mark.asyncio
async def test_complete_rejects_wrong_worker(setup_db):
    """worker_id 不等于 claimed_by 时抛 ValueError，不写 completed 状态。"""
    from app.execution.task_queue import task_queue

    task_id = await task_queue.enqueue(task_type="skill_run", payload={"x": 1})
    claimed = await task_queue.claim(worker_id="real-worker")
    assert claimed is not None
    # 冒充 "other-worker" 尝试标记完成 → 应被 claimed_by WHERE 拦下
    with pytest.raises(ValueError):
        await task_queue.complete(task_id, result={"output": "spoofed"}, worker_id="other-worker")
    stats = await task_queue.get_queue_stats()
    # 任务仍在 running（未被伪造完成）
    assert stats.get("running", 0) == 1
    assert stats.get("completed", 0) == 0


@pytest.mark.asyncio
async def test_complete_accepts_correct_worker(setup_db):
    """worker_id 匹配 claimed_by 时 complete 成功。"""
    from app.execution.task_queue import task_queue

    task_id = await task_queue.enqueue(task_type="skill_run", payload={"x": 2})
    await task_queue.claim(worker_id="real-worker")
    await task_queue.complete(task_id, result={"output": "ok"}, worker_id="real-worker")
    stats = await task_queue.get_queue_stats()
    assert stats["completed"] == 1
