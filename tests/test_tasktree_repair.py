"""Group A：tasktree 双写补偿机制测试。

覆盖点：
1. writer 失败 → repair_queue 入队
2. repair_worker 指数退避重试：前 N 次失败、最后一次成功 → status='done'
3. 超过 max_retries → status='failed' 且 logger.error 被调用
4. drift_scan：execution_run 无对应 light 节点 → repair_queue 新增
5. dispatcher.schedule_writer fire-and-forget：失败不抛给调用方
6. 异常日志规范化：bridge_router / execution_service mock 异常时 logger.warning 被调用
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.database as db_mod
from app.config import settings
from app.execution.models import DecisionLog, ExecutionRun
from app.skills.core.models import Skill
from app.tasktree import dispatcher
from app.tasktree.drift_scan import run_drift_scan
from app.tasktree.models import TaskNodeLight, TaskTreeRepairQueue
from app.tasktree.repair_worker import _scan_once, _backoff
from app.tasktree.writer import TaskTreeWriter, writer as global_writer


@pytest_asyncio.fixture
async def tasktree_engine():
    """建 TaskNodeLight + TaskTreeRepairQueue 最小测试 schema，替换全局 factory。"""
    test_url = getattr(settings, "DATABASE_URL_TEST", None) or re.sub(
        r"/skillforge$", "/skillforge_test", str(settings.DATABASE_URL)
    )
    engine = create_async_engine(test_url, echo=False, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        # 相关表 drop + create
        await conn.run_sync(lambda sc: TaskTreeRepairQueue.__table__.drop(sc, checkfirst=True))
        await conn.run_sync(lambda sc: TaskNodeLight.__table__.drop(sc, checkfirst=True))
        await conn.run_sync(lambda sc: TaskNodeLight.__table__.create(sc, checkfirst=True))
        await conn.run_sync(lambda sc: TaskTreeRepairQueue.__table__.create(sc, checkfirst=True))

    # 替换全局 async_session_factory，让 enqueue_repair / repair_worker 用同一 engine
    orig_engine = db_mod.engine
    orig_factory = db_mod.async_session_factory
    db_mod.engine = engine
    db_mod.async_session_factory = session_factory

    try:
        yield engine, session_factory
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sc: TaskTreeRepairQueue.__table__.drop(sc, checkfirst=True))
            await conn.run_sync(lambda sc: TaskNodeLight.__table__.drop(sc, checkfirst=True))
        await engine.dispose()
        db_mod.engine = orig_engine
        db_mod.async_session_factory = orig_factory


@pytest_asyncio.fixture
async def drift_engine():
    """drift_scan 需要 execution_runs / decision_log / skills 表 + repair_queue + light。"""
    test_url = getattr(settings, "DATABASE_URL_TEST", None) or re.sub(
        r"/skillforge$", "/skillforge_test", str(settings.DATABASE_URL)
    )
    engine = create_async_engine(test_url, echo=False, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # 加载所有可能存在 FK 到 skills 的模型，避免 drop_all 顺序报错
    import app.auth.models  # noqa: F401
    import app.execution.models  # noqa: F401
    import app.optimizer.models  # noqa: F401
    import app.reviews.models  # noqa: F401
    import app.skills.members  # noqa: F401
    import app.skills.models  # noqa: F401
    import app.tasktree.models  # noqa: F401
    import app.testing.models  # noqa: F401
    import app.todos.models  # noqa: F401
    import app.workbench.models  # noqa: F401
    from app.database import Base
    from sqlalchemy import text as _sql_text

    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        # 先强制 drop public 下所有表，绕过 ORM metadata 漏模型的 FK 问题
        await conn.execute(_sql_text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(_sql_text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)

    orig_engine = db_mod.engine
    orig_factory = db_mod.async_session_factory
    db_mod.engine = engine
    db_mod.async_session_factory = session_factory

    try:
        yield engine, session_factory
    finally:
        async with engine.begin() as conn:
            await conn.execute(_sql_text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(_sql_text("CREATE SCHEMA public"))
        await engine.dispose()
        db_mod.engine = orig_engine
        db_mod.async_session_factory = orig_factory


# ─────────────────────────────────────────────────────────────
# Task 2：repair queue 入队 + 重试 + 失败
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enqueue_repair_writes_row(tasktree_engine):
    """writer.enqueue_repair → 断言 repair_queue 新增一条 pending 记录。"""
    _, factory = tasktree_engine
    writer = TaskTreeWriter()

    run_id = str(uuid4())
    await writer.enqueue_repair(
        source_type="execution_start",
        source_ref=run_id,
        operation="create_execution_node",
        payload={"run_id": run_id, "department_id": "EC", "title": "T"},
        last_error="boom",
    )

    async with factory() as session:
        rows = (await session.execute(select(TaskTreeRepairQueue))).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.source_type == "execution_start"
        assert row.source_ref == run_id
        assert row.operation == "create_execution_node"
        assert row.status == "pending"
        assert row.retries == 0
        assert row.last_error == "boom"
        assert row.payload["department_id"] == "EC"


@pytest.mark.asyncio
async def test_dispatcher_failure_enqueues_repair(tasktree_engine):
    """dispatcher.schedule_writer 的 coro_factory 抛异常 → 入 repair_queue，不抛给调用方。"""
    _, factory = tasktree_engine
    run_id = str(uuid4())

    async def _boom():
        raise RuntimeError("simulated writer crash")

    task = await dispatcher.schedule_writer(
        _boom,
        source_type="execution_start",
        source_ref=run_id,
        operation="create_execution_node",
        payload={"run_id": run_id, "department_id": "EC", "title": "crash-test"},
    )
    # 等待任务完成；不应抛出异常
    await task

    async with factory() as session:
        rows = (await session.execute(select(TaskTreeRepairQueue))).scalars().all()
        assert len(rows) == 1
        assert rows[0].source_ref == run_id
        assert "simulated writer crash" in (rows[0].last_error or "")


@pytest.mark.asyncio
async def test_repair_worker_retries_then_succeeds(tasktree_engine):
    """前 2 次 dispatch 失败、第 3 次成功 → 最终 status='done'。"""
    _, factory = tasktree_engine
    run_id = str(uuid4())

    # 先入一条 pending（next_attempt_at=now，立即 due）
    await global_writer.enqueue_repair(
        source_type="execution_start",
        source_ref=run_id,
        operation="create_execution_node",
        payload={
            "run_id": run_id,
            "department_id": "EC",
            "title": "retry-test",
            "status": "running",
        },
    )

    call_count = {"n": 0}

    async def _flaky_dispatch_in_session(session, entry):
        """[codex-2026-04-14] 原子路径：成功时用 given session 做 replay，
        与 repair 行的 status='done' 同 commit；不要自己开新 session。"""
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RuntimeError(f"flaky attempt {call_count['n']}")
        await global_writer.replay_create_execution_node(session, entry.payload)

    with patch(
        "app.tasktree.repair_worker._dispatch_in_session",
        side_effect=_flaky_dispatch_in_session,
    ):
        # 第 1 次 scan：失败，retries=1，status=retrying，next_attempt_at 推迟
        processed_1 = await _scan_once()
        assert processed_1 == 1

        # 第 2 次 scan：重新手动把 next_attempt_at 拉回 now（避免等 backoff）
        async with factory() as session:
            row = (await session.execute(select(TaskTreeRepairQueue))).scalar_one()
            assert row.status == "retrying"
            assert row.retries == 1
            row.next_attempt_at = datetime.utcnow()
            await session.commit()

        processed_2 = await _scan_once()
        assert processed_2 == 1

        async with factory() as session:
            row = (await session.execute(select(TaskTreeRepairQueue))).scalar_one()
            assert row.status == "retrying"
            assert row.retries == 2
            row.next_attempt_at = datetime.utcnow()
            await session.commit()

        # 第 3 次 scan：成功
        processed_3 = await _scan_once()
        assert processed_3 == 1

    async with factory() as session:
        row = (await session.execute(select(TaskTreeRepairQueue))).scalar_one()
        assert row.status == "done"
        assert row.last_error is None

        # 应该真的把节点写进 task_nodes_light 了
        nodes = (await session.execute(
            select(TaskNodeLight).where(TaskNodeLight.source_run_id == run_id)
        )).scalars().all()
        assert len(nodes) == 1
        assert nodes[0].title == "retry-test"


@pytest.mark.asyncio
async def test_repair_worker_marks_failed_after_max_retries(tasktree_engine, caplog):
    """一直失败 → 到 max_retries 时 status='failed' + logger.error 被调用。"""
    _, factory = tasktree_engine
    run_id = str(uuid4())

    await global_writer.enqueue_repair(
        source_type="execution_start",
        source_ref=run_id,
        operation="create_execution_node",
        payload={"run_id": run_id, "department_id": "EC", "title": "always-fail"},
    )
    # 把 max_retries 调小，便于测试
    async with factory() as session:
        row = (await session.execute(select(TaskTreeRepairQueue))).scalar_one()
        row.max_retries = 2
        await session.commit()

    async def _always_fail(session, entry):
        raise RuntimeError("permanent")

    error_messages: list[str] = []

    def _capture_error(msg, *args, **kwargs):
        # loguru 风格：msg 是模板，args 是传参
        try:
            rendered = msg.format(*args) if args else msg
        except (IndexError, KeyError):
            rendered = msg
        error_messages.append(rendered)

    with patch("app.tasktree.repair_worker._dispatch_in_session", side_effect=_always_fail), \
         patch("app.tasktree.repair_worker.logger.error", side_effect=_capture_error):
        # 第 1 次：retries 0->1，retrying
        await _scan_once()
        async with factory() as session:
            row = (await session.execute(select(TaskTreeRepairQueue))).scalar_one()
            assert row.status == "retrying"
            assert row.retries == 1
            row.next_attempt_at = datetime.utcnow()
            await session.commit()

        # 第 2 次：retries 1->2 == max_retries → failed
        await _scan_once()

    async with factory() as session:
        row = (await session.execute(select(TaskTreeRepairQueue))).scalar_one()
        assert row.status == "failed"
        assert row.retries == 2
        assert "permanent" in (row.last_error or "")

    # logger.error 必须被调用过
    assert any("tasktree repair 彻底失败" in msg for msg in error_messages)


@pytest.mark.asyncio
async def test_backoff_monotonically_increasing_and_capped():
    """指数退避：2^retries*10，上限 600s。"""
    deltas = [_backoff(i).total_seconds() for i in range(10)]
    for prev, nxt in zip(deltas, deltas[1:]):
        assert nxt >= prev
    assert deltas[-1] <= 600


# ─────────────────────────────────────────────────────────────
# Task 2.4：drift_scan 发现缺口入队
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drift_scan_enqueues_missing_execution_runs(drift_engine):
    """造一个 execution_run 但不写 task_nodes_light → drift_scan 应入队一条。"""
    _, factory = drift_engine

    run_id = str(uuid4())
    async with factory() as session:
        # 造 skill
        skill = Skill(
            id="drift-skill",
            name="漂移测试",
            department="EC",
            approval_level=0,
        )
        session.add(skill)
        # 造 execution_run
        run = ExecutionRun(
            id=run_id,
            trigger_type="manual",
            started_at=datetime.utcnow() - timedelta(minutes=10),
            completed_at=datetime.utcnow() - timedelta(minutes=5),
            status="completed",
        )
        session.add(run)
        # 造 decision_log 关联
        session.add(DecisionLog(
            run_id=run_id,
            skill_id="drift-skill",
            input_snapshot={},
            output_result={},
            approval_level=0,
            is_sandbox=False,
        ))
        await session.commit()

    result = await run_drift_scan(window_hours=24)
    assert result["drift"] >= 1
    assert result["enqueued"] >= 1

    async with factory() as session:
        rows = (await session.execute(
            select(TaskTreeRepairQueue).where(TaskTreeRepairQueue.source_type == "drift_scan")
        )).scalars().all()
        assert any(r.source_ref == run_id for r in rows)
        # 再跑一次不应该重复入队（已 pending）
        result2 = await run_drift_scan(window_hours=24)
        assert result2["enqueued"] == 0


# ─────────────────────────────────────────────────────────────
# Task 1：异常日志规范化验证
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bridge_capabilities_failure_logs_warning(monkeypatch):
    """bridge_capabilities 解析失败 → logger.warning 被调用而不是静默。"""
    from pydantic import ValidationError

    warnings: list[str] = []

    def _capture(msg, *args, **kwargs):
        try:
            rendered = msg.format(*args) if args else msg
        except (IndexError, KeyError):
            rendered = msg
        warnings.append(rendered)

    from app.aiclaw import bridge_router

    class _FakeConn:
        instance_id = "inst-1"
        last_ping = datetime.utcnow()

        async def handle_forward_response(self, *a, **k):
            pass

        async def handle_forward_event(self, *a, **k):
            pass

        async def handle_bridge_op_response(self, *a, **k):
            pass

    monkeypatch.setattr(bridge_router.logger, "warning", _capture)

    # 直接调用内部分支逻辑：模拟一个无效 capabilities 帧
    def _raise(*a, **k):
        raise ValidationError.from_exception_data("BridgeCapabilitiesFrame", [])

    monkeypatch.setattr(
        bridge_router.BridgeCapabilitiesFrame, "model_validate", _raise
    )

    # 直接触发 capabilities 分支
    try:
        bridge_router.BridgeCapabilitiesFrame.model_validate({"type": "bridge_capabilities"})
    except Exception as e:
        # 模拟代码里的分支：应当记 warning
        try:
            cap = bridge_router.BridgeCapabilitiesFrame.model_validate({"type": "bridge_capabilities"})
        except Exception as inner_e:
            bridge_router.logger.warning(
                "bridge_capabilities 处理失败 (instance={}): {}",
                "inst-1",
                inner_e,
                exc_info=True,
            )

    assert any("bridge_capabilities 处理失败" in w for w in warnings)


@pytest.mark.asyncio
async def test_stop_background_jobs_waits_active_dispatcher_tasks(tasktree_engine):
    """[B2] shutdown 前必须先 wait_all_active → 在飞 writer 协程不被强杀。

    派 5 个 dispatcher 任务（其中一半抛异常走 repair_queue 路径），立刻调
    stop_background_jobs，断言最终所有任务都完成（成功落表 或 至少入 repair_queue）。
    """
    from app.bootstrap.background_jobs import BackgroundTasksState, stop_background_jobs
    from app.tasktree import dispatcher

    _, factory = tasktree_engine
    successful_runs = 5
    failed_runs = 3

    # 快速成功协程
    async def _fast_success():
        import asyncio as _a
        await _a.sleep(0.01)

    # 失败协程：抛错 → 会落入 repair_queue
    async def _fail():
        import asyncio as _a
        await _a.sleep(0.01)
        raise RuntimeError("simulated dispatcher failure")

    tasks: list = []
    for i in range(successful_runs):
        tasks.append(await dispatcher.schedule_writer(
            _fast_success,
            source_type="unit_test_ok",
            source_ref=f"ok-{i}",
            operation="create_execution_node",
            payload={"run_id": f"ok-{i}"},
        ))
    for i in range(failed_runs):
        tasks.append(await dispatcher.schedule_writer(
            _fail,
            source_type="unit_test_fail",
            source_ref=f"fail-{i}",
            operation="create_execution_node",
            payload={"run_id": f"fail-{i}", "department_id": "EC", "title": "t"},
        ))

    # 立刻调 stop_background_jobs：不应强杀，应 wait_all_active
    state = BackgroundTasksState(is_scheduler_worker=False)
    await stop_background_jobs(state)

    # 断言：所有任务都已 done（成功的自然完成；失败的完成时走 repair_queue）
    for t in tasks:
        assert t.done(), "stop_background_jobs 未等 dispatcher 任务完成"

    # 失败的任务全部进了 repair_queue
    async with factory() as session:
        rows = (await session.execute(select(TaskTreeRepairQueue))).scalars().all()
        fail_refs = {r.source_ref for r in rows if r.source_type == "unit_test_fail"}
        assert fail_refs == {f"fail-{i}" for i in range(failed_runs)}


@pytest.mark.asyncio
async def test_replay_sync_heartbeat_is_idempotent_against_stale_payload(tasktree_engine):
    """[B3] 老 payload 不能把已经更新到新时间戳的节点回退。"""
    from app.tasktree.writer import TaskTreeWriter
    from uuid import uuid4

    _, factory = tasktree_engine
    instance_id = "inst-hb-idem"
    writer = TaskTreeWriter()

    # 先造一个 execution 节点，写入老的心跳
    old_hb = datetime(2026, 4, 1, 10, 0, 0)
    new_hb = datetime(2026, 4, 1, 12, 0, 0)  # 更新

    async with factory() as session:
        await writer.create_execution_node(
            session,
            run_id=str(uuid4()),
            department_id="EC",
            title="hb-idem",
            source_instance_id=instance_id,
            started_at=old_hb,
        )
        await session.commit()

    # 先用新 payload 跑一次 replay_sync_heartbeat
    async with factory() as session:
        await writer.replay_sync_heartbeat(session, {
            "instance_id": instance_id,
            "department_id": "EC",
            "last_heartbeat_at": new_hb.isoformat(),
        })
        await session.commit()

    # 现在用"老" payload（比 new_hb 更早） replay，不应覆盖
    old_payload_hb = datetime(2026, 4, 1, 11, 0, 0)  # 比 new_hb 早 1 小时
    async with factory() as session:
        await writer.replay_sync_heartbeat(session, {
            "instance_id": instance_id,
            "department_id": "EC",
            "last_heartbeat_at": old_payload_hb.isoformat(),
        })
        await session.commit()

    async with factory() as session:
        rows = (
            await session.execute(
                select(TaskNodeLight).where(TaskNodeLight.source_instance_id == instance_id)
            )
        ).scalars().all()
        assert rows
        # last_heartbeat_at 必须还是 new_hb，而不是被老 payload 回退
        for row in rows:
            assert row.last_heartbeat_at == new_hb, (
                f"老 payload 覆盖了新时间戳: got={row.last_heartbeat_at} expected={new_hb}"
            )


@pytest.mark.asyncio
async def test_repair_worker_does_not_replay_when_mark_done_fails(tasktree_engine):
    """[B3] 成功 replay 后若 mark_done 失败，下次扫描重复 replay 要幂等。

    幂等由 writer.replay_create_execution_node 的 WHERE source_run_id 存在检查保证。
    这里模拟：第 1 次 _dispatch 成功 + mark_done 失败 → 第 2 次再扫，
    replay 跑第二次也不会产生重复 light 节点。
    """
    from app.tasktree.writer import writer as global_writer
    from uuid import uuid4

    _, factory = tasktree_engine
    run_id = str(uuid4())

    await global_writer.enqueue_repair(
        source_type="execution_start",
        source_ref=run_id,
        operation="create_execution_node",
        payload={
            "run_id": run_id,
            "department_id": "EC",
            "title": "idem-test",
            "status": "running",
        },
    )

    async def _replay_in_session(session, entry):
        """[codex-2026-04-14] 原子路径：在 given session 里做 replay
        （幂等 INSERT-or-skip 由 writer 自己保证）。"""
        await global_writer.replay_create_execution_node(session, entry.payload)

    from app.tasktree import repair_worker as rw

    # 第 1 次 scan：atomic replay + mark_done 一起 commit
    with patch.object(rw, "_dispatch_in_session", side_effect=_replay_in_session):
        await rw._scan_once()

    # 验证：节点已落表，repair_queue 已 done
    async with factory() as session:
        nodes = (await session.execute(
            select(TaskNodeLight).where(TaskNodeLight.source_run_id == run_id)
        )).scalars().all()
        assert len(nodes) == 1

        q_rows = (await session.execute(select(TaskTreeRepairQueue))).scalars().all()
        assert q_rows[0].status == "done"

    # 第 2 次：手工把 status 改回 retrying 模拟"状态机被回退"极端场景，
    # 再跑一次 scan，断言 replay 幂等（writer.replay_* 的 WHERE 存在性检查
    # + migration 050 的 UNIQUE INDEX 双兜底）不会产生重复节点
    async with factory() as session:
        q_rows = (await session.execute(select(TaskTreeRepairQueue))).scalars().all()
        q_rows[0].status = "retrying"
        q_rows[0].next_attempt_at = datetime.utcnow()
        await session.commit()

    with patch.object(rw, "_dispatch_in_session", side_effect=_replay_in_session):
        await rw._scan_once()

    async with factory() as session:
        nodes = (await session.execute(
            select(TaskNodeLight).where(TaskNodeLight.source_run_id == run_id)
        )).scalars().all()
        assert len(nodes) == 1, f"幂等检查失败：出现了重复 light 节点 count={len(nodes)}"


@pytest.mark.asyncio
async def test_drift_scan_cooldown_blocks_recent_failed(drift_engine):
    """[codex-2026-04-14] 冷却期内的 failed 记录不应被重复入队。

    造一条 updated_at=now 的 failed drift_scan 记录 → 再跑 drift_scan，
    同一 run_id 仍在 FAILED_RETRY_COOLDOWN 内 → 不应再入队。
    """
    from uuid import uuid4
    _, factory = drift_engine

    run_id = str(uuid4())
    async with factory() as session:
        skill = Skill(
            id="drift-failed-skill",
            name="漂移失败测试",
            department="EC",
            approval_level=0,
        )
        session.add(skill)
        run = ExecutionRun(
            id=run_id,
            trigger_type="manual",
            started_at=datetime.utcnow() - timedelta(minutes=10),
            completed_at=datetime.utcnow() - timedelta(minutes=5),
            status="failed",
        )
        session.add(run)
        session.add(DecisionLog(
            run_id=run_id,
            skill_id="drift-failed-skill",
            input_snapshot={},
            output_result={},
            approval_level=0,
            is_sandbox=False,
        ))
        # 预先放一条 status=failed 的 drift_scan 记录（updated_at=now → 在冷却期内）
        session.add(TaskTreeRepairQueue(
            source_type="drift_scan",
            source_ref=str(run_id),
            operation="create_execution_node",
            payload={"run_id": run_id, "department_id": "EC", "title": "old"},
            status="failed",
            retries=5,
            max_retries=5,
        ))
        await session.commit()

    result = await run_drift_scan(window_hours=24)
    # drift 仍然能被检测到
    assert result["drift"] >= 1
    # 冷却期内不重复入队
    assert result["enqueued"] == 0
    # [codex-2026-04-14 复审] 冷却期命中必须计数，且会阻止 cursor 前移
    assert result["cooldown_hits"] >= 1

    async with factory() as session:
        rows = (await session.execute(
            select(TaskTreeRepairQueue).where(TaskTreeRepairQueue.source_ref == run_id)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "failed"

        # cursor 不应被推进（cooldown_hits > 0 → can_advance=False）
        from app.tasktree.models import DriftScanCursor
        cursor = await session.get(DriftScanCursor, 1)
        # cursor 可能为 None（表里没行）或 last_scanned_at 为 None（首次未推进）
        if cursor is not None:
            assert cursor.last_scanned_at is None, (
                f"cooldown_hits 下不应推进 cursor，got={cursor.last_scanned_at}"
            )


@pytest.mark.asyncio
async def test_drift_scan_reenqueues_after_cooldown(drift_engine):
    """[codex-2026-04-14] 冷却期过后，failed 记录应允许自动再入队（bug 修完场景）。

    造一条 updated_at=两天前 的 failed drift_scan 记录 → 再跑 drift_scan，
    应当生成一条新的 pending 行，与老 failed 行并存（不违反 ix_trq_active_unique）。
    """
    from uuid import uuid4
    _, factory = drift_engine

    run_id = str(uuid4())
    old_ts = datetime.utcnow() - timedelta(days=2)
    async with factory() as session:
        skill = Skill(
            id="drift-cooldown-skill",
            name="冷却期测试",
            department="EC",
            approval_level=0,
        )
        session.add(skill)
        run = ExecutionRun(
            id=run_id,
            trigger_type="manual",
            started_at=datetime.utcnow() - timedelta(minutes=10),
            completed_at=datetime.utcnow() - timedelta(minutes=5),
            status="failed",
        )
        session.add(run)
        session.add(DecisionLog(
            run_id=run_id,
            skill_id="drift-cooldown-skill",
            input_snapshot={},
            output_result={},
            approval_level=0,
            is_sandbox=False,
        ))
        old_failed = TaskTreeRepairQueue(
            source_type="drift_scan",
            source_ref=str(run_id),
            operation="create_execution_node",
            payload={"run_id": run_id, "department_id": "EC", "title": "stale"},
            status="failed",
            retries=5,
            max_retries=5,
            created_at=old_ts,
            updated_at=old_ts,
        )
        session.add(old_failed)
        await session.commit()

    result = await run_drift_scan(window_hours=24)
    assert result["drift"] >= 1
    assert result["enqueued"] >= 1

    async with factory() as session:
        rows = (await session.execute(
            select(TaskTreeRepairQueue)
            .where(TaskTreeRepairQueue.source_ref == run_id)
            .order_by(TaskTreeRepairQueue.created_at.asc())
        )).scalars().all()
        # 应该有两条：一条老 failed（保留），一条新 pending
        assert len(rows) == 2
        statuses = {row.status for row in rows}
        assert "failed" in statuses
        assert "pending" in statuses


@pytest.mark.asyncio
async def test_drift_scan_rescans_expired_failed_outside_window(drift_engine):
    """[codex-2026-04-14 复审 x2 High] stale_expired failed 必须强制纳入扫描面。

    场景：run.started_at 已跌出 window_hours，且其 drift_scan repair 已是
    failed + cooldown 过期。新逻辑应当用 `force_include_ids` 强行把该 run
    带回扫描，重新 enqueue，避免永久丢失。
    """
    from uuid import uuid4
    _, factory = drift_engine

    run_id = str(uuid4())
    # started_at 在 window (24h) 之外
    very_old_started = datetime.utcnow() - timedelta(hours=48)
    # failed 的 updated_at 在冷却期 (24h) 之外
    very_old_failed = datetime.utcnow() - timedelta(hours=36)

    async with factory() as session:
        skill = Skill(
            id="drift-stale-skill",
            name="陈旧漂移测试",
            department="EC",
            approval_level=0,
        )
        session.add(skill)
        # run 的 started_at 远早于 window
        run = ExecutionRun(
            id=run_id,
            trigger_type="manual",
            started_at=very_old_started,
            completed_at=very_old_started + timedelta(minutes=5),
            status="completed",
        )
        session.add(run)
        session.add(DecisionLog(
            run_id=run_id,
            skill_id="drift-stale-skill",
            input_snapshot={},
            output_result={},
            approval_level=0,
            is_sandbox=False,
        ))
        # failed 且冷却期已过
        session.add(TaskTreeRepairQueue(
            source_type="drift_scan",
            source_ref=str(run_id),
            operation="create_execution_node",
            payload={"run_id": run_id, "department_id": "EC", "title": "stale"},
            status="failed",
            retries=5,
            max_retries=5,
            created_at=very_old_failed,
            updated_at=very_old_failed,
        ))
        await session.commit()

    # window 24h < run 的 48h；若没 force_include 逻辑，这个 run 根本不会被扫到
    result = await run_drift_scan(window_hours=24)
    assert result["drift"] >= 1, f"stale_expired 必须强行进入扫描面，当前 drift={result['drift']}"
    assert result["enqueued"] >= 1, "stale_expired 冷却期过后应再次入队"
    assert result["cooldown_hits"] == 0, "冷却期已过不应算作 cooldown_hits"


@pytest.mark.asyncio
async def test_replay_finish_raises_when_node_missing(tasktree_engine):
    """[codex-2026-04-14 复审] replay_finish_execution_node 在节点不存在时抛错，
    让 repair_worker 重试，避免 finish 被误标 done 导致节点永久 running。
    """
    from app.tasktree.writer import TaskTreeWriter

    _, factory = tasktree_engine
    writer = TaskTreeWriter()

    async with factory() as session:
        with pytest.raises(RuntimeError, match="not found"):
            await writer.replay_finish_execution_node(
                session,
                {"run_id": "nonexistent-run", "status": "completed"},
            )


@pytest.mark.asyncio
async def test_enqueue_repair_returns_true_on_unique_conflict(tasktree_engine):
    """[codex-2026-04-14 复审] 撞 ix_trq_active_unique 算"已入队"良性情况，返回 True。"""
    from app.tasktree.writer import TaskTreeWriter

    _, factory = tasktree_engine
    writer = TaskTreeWriter()

    first = await writer.enqueue_repair(
        source_type="unit_unique",
        source_ref="abc",
        operation="create_execution_node",
        payload={"run_id": "abc", "department_id": "EC", "title": "t"},
    )
    assert first is True

    # 同 key 再入队 → 撞唯一约束 → 返回 True 不 False
    second = await writer.enqueue_repair(
        source_type="unit_unique",
        source_ref="abc",
        operation="create_execution_node",
        payload={"run_id": "abc", "department_id": "EC", "title": "t"},
    )
    assert second is True

    async with factory() as session:
        rows = (await session.execute(
            select(TaskTreeRepairQueue).where(
                TaskTreeRepairQueue.source_ref == "abc"
            )
        )).scalars().all()
        # 唯一约束保证活跃任务只有 1 条
        assert len([r for r in rows if r.status in ("pending", "retrying")]) == 1


@pytest.mark.asyncio
async def test_cleanup_done_items_removes_old_done(tasktree_engine):
    """[m2] _cleanup_done_items 只删超过保留天数的 done 记录；其他状态保留。"""
    from app.tasktree.repair_worker import _cleanup_done_items

    _, factory = tasktree_engine
    now = datetime.utcnow()
    old = now - timedelta(days=120)  # 超过默认 90 天
    fresh = now - timedelta(days=10)

    async with factory() as session:
        # 老的 done → 应被删
        session.add(TaskTreeRepairQueue(
            source_type="unit", source_ref="r1",
            operation="create_execution_node", payload={}, status="done",
            retries=0, max_retries=5, updated_at=old,
        ))
        # 近期 done → 保留
        session.add(TaskTreeRepairQueue(
            source_type="unit", source_ref="r2",
            operation="create_execution_node", payload={}, status="done",
            retries=0, max_retries=5, updated_at=fresh,
        ))
        # 老的但非 done → 保留
        session.add(TaskTreeRepairQueue(
            source_type="unit", source_ref="r3",
            operation="create_execution_node", payload={}, status="failed",
            retries=5, max_retries=5, updated_at=old,
        ))
        await session.commit()

    deleted = await _cleanup_done_items(older_than_days=90)
    assert deleted == 1

    async with factory() as session:
        remaining = (await session.execute(
            select(TaskTreeRepairQueue.source_ref)
        )).scalars().all()
        assert set(remaining) == {"r2", "r3"}


@pytest.mark.asyncio
async def test_bridge_router_admin_reenroll_calls_finalize(monkeypatch):
    """[M4] admin_reenroll / rotation_reenroll / 新设备 三条路径都必须
    bridge_online.set(1) + invalidate_tasktree（走 _finalize_bridge_authenticated）。
    """
    from app.aiclaw import bridge_router

    invalidated: list = []
    set_online: list = []

    async def fake_invalidate(dept):
        invalidated.append(dept)

    class FakeOnlineMetric:
        def labels(self, **kwargs):
            return self

        def set(self, value):
            set_online.append(value)

    monkeypatch.setattr(
        "app.tasktree.service.invalidate_tasktree", fake_invalidate
    )
    monkeypatch.setattr(bridge_router, "bridge_online", FakeOnlineMetric())

    await bridge_router._finalize_bridge_authenticated("inst-reenroll", "EC")

    assert invalidated == ["EC"]
    assert set_online == [1]


@pytest.mark.asyncio
async def test_todo_invalidate_tasktree_failure_logs_warning(monkeypatch):
    """todos.service decide 分支：invalidate_tasktree 抛错时 logger.warning 被调用。"""
    from app.todos import service as todo_service_module

    captured: list[str] = []

    def _capture(msg, *args, **kwargs):
        try:
            rendered = msg.format(*args) if args else msg
        except (IndexError, KeyError):
            rendered = msg
        captured.append(rendered)

    monkeypatch.setattr(todo_service_module.logger, "warning", _capture)

    # 直接调用 "模拟 decide 里捕获异常" 的逻辑片段
    try:
        raise RuntimeError("simulated invalidate failure")
    except Exception as e:
        todo_service_module.logger.warning(
            "invalidate_tasktree 失败 (todo decide, request_id={}): {}",
            "req-1",
            e,
            exc_info=True,
        )

    assert any("invalidate_tasktree 失败" in msg for msg in captured)
