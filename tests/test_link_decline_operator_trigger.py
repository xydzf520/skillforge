from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select


@pytest.mark.asyncio
async def test_collector_success_triggers_operator_with_parent_run(client):
    from app.database import async_session_factory
    from app.execution.execution_service import ExecutionService
    from app.skills.core.models import Skill

    svc = ExecutionService()
    svc.execute_skill = AsyncMock(return_value={"run_id": "operator-run"})

    async with async_session_factory() as session:
        await session.merge(
            Skill(
                id=svc.OPERATOR_SKILL_ID,
                name="Link Decline Operator",
                department="EC",
                visibility="company",
                status="active",
            )
        )
        await session.commit()

    collector_output = {
        "collection_schema": "tmall_link_decline_collection_v1",
        "生意参谋_商品排行榜": [{"item_id": "1"}],
    }
    await svc._trigger_link_decline_operator_if_needed(
        collector_skill_id=svc.COLLECTOR_SKILL_ID,
        collector_run_id="collector-run-1",
        collector_decision_log_id=101,
        collector_output=collector_output,
    )

    svc.execute_skill.assert_awaited_once()
    kwargs = svc.execute_skill.await_args.kwargs
    assert kwargs["skill_id"] == svc.OPERATOR_SKILL_ID
    assert kwargs["triggered_by"] == svc.OPERATOR_TRIGGER
    assert kwargs["parent_run_id"] == "collector-run-1"
    assert kwargs["params"]["collector_run_id"] == "collector-run-1"
    assert kwargs["params"]["collector_decision_log_id"] == 101
    assert kwargs["params"]["collector_output"] == collector_output


@pytest.mark.asyncio
async def test_collector_success_does_not_duplicate_existing_operator_child(client):
    from datetime import datetime

    from app.database import async_session_factory
    from app.execution.execution_service import ExecutionService
    from app.execution.models import ExecutionRun
    from app.skills.core.models import Skill

    svc = ExecutionService()
    svc.execute_skill = AsyncMock(return_value={"run_id": "operator-run"})

    async with async_session_factory() as session:
        await session.merge(
            Skill(
                id=svc.OPERATOR_SKILL_ID,
                name="Link Decline Operator",
                department="EC",
                visibility="company",
                status="active",
            )
        )
        session.add(
            ExecutionRun(
                id="operator-existing-run",
                skill_id=svc.OPERATOR_SKILL_ID,
                parent_run_id="collector-run-2",
                trigger_type=svc.OPERATOR_TRIGGER,
                started_at=datetime.utcnow(),
                status="completed",
            )
        )
        await session.commit()

    await svc._trigger_link_decline_operator_if_needed(
        collector_skill_id=svc.COLLECTOR_SKILL_ID,
        collector_run_id="collector-run-2",
        collector_decision_log_id=102,
        collector_output={"collection_schema": "tmall_link_decline_collection_v1"},
    )

    svc.execute_skill.assert_not_awaited()


@pytest.mark.asyncio
async def test_collector_success_passes_previous_same_time_collector_output(client):
    from datetime import datetime

    from app.database import async_session_factory
    from app.execution.execution_service import ExecutionService
    from app.execution.models import DecisionLog, ExecutionRun
    from app.skills.core.models import Skill

    svc = ExecutionService()
    svc.execute_skill = AsyncMock(return_value={"run_id": "operator-run"})
    current_output = {
        "collection_schema": "tmall_link_decline_collection_v1",
        "数据采集快照": {"schedule_time": "07:50"},
        "生意参谋_商品排行榜": [{"item_id": "800737120171"}],
        "外部平台原始采集": {
            "required_flow_by_item": {
                "800737120171": {
                    "source": "tmall_item_flow_required_metrics",
                    "free_flow_basis": {
                        "search_visitor": 49,
                        "search_conversion_rate": 0.04,
                    },
                },
            },
        },
    }
    baseline_output = {
        "collection_schema": "tmall_link_decline_collection_v1",
        "数据采集快照": {
            "schema": "tmall_link_decline_snapshot_v1",
            "date": "2026-05-28",
            "schedule_time": "07:50",
            "items": {"800737120171": {"item_rank": {"visitor_count": 51}}},
        },
        "外部平台原始采集": {
            "required_flow_by_item": {
                "800737120171": {
                    "source": "tmall_item_flow_required_metrics",
                    "free_flow_basis": {
                        "search_visitor": 51,
                        "search_conversion_rate": 0.08,
                    },
                },
            },
        },
    }

    async with async_session_factory() as session:
        await session.merge(
            Skill(
                id=svc.OPERATOR_SKILL_ID,
                name="Link Decline Operator",
                department="EC",
                visibility="company",
                status="active",
            )
        )
        session.add_all([
            ExecutionRun(
                id="collector-run-current",
                skill_id=svc.COLLECTOR_SKILL_ID,
                trigger_type="node_scheduler",
                started_at=datetime(2026, 5, 29, 7, 50, 0),
                status="completed",
            ),
            ExecutionRun(
                id="collector-run-baseline",
                skill_id=svc.COLLECTOR_SKILL_ID,
                trigger_type="node_scheduler",
                started_at=datetime(2026, 5, 28, 7, 50, 0),
                status="completed",
            ),
        ])
        baseline_decision = DecisionLog(
            run_id="collector-run-baseline",
            skill_id=svc.COLLECTOR_SKILL_ID,
            input_snapshot={},
            output_result=baseline_output,
            approval_level=0,
            is_sandbox=False,
        )
        session.add(baseline_decision)
        await session.flush()
        baseline_decision_id = baseline_decision.id
        await session.commit()

    await svc._trigger_link_decline_operator_if_needed(
        collector_skill_id=svc.COLLECTOR_SKILL_ID,
        collector_run_id="collector-run-current",
        collector_decision_log_id=110,
        collector_output=current_output,
    )

    svc.execute_skill.assert_awaited_once()
    params = svc.execute_skill.await_args.kwargs["params"]
    assert params["snapshot_time"] == "07:50"
    assert params["schedule_time"] == "07:50"
    assert params["snapshot_mode"] == "on"
    assert params["collector_output"]["snapshot_time"] == "07:50"
    assert params["collector_output"]["schedule_time"] == "07:50"
    assert params["collector_output"]["baseline_snapshot"] == baseline_output["数据采集快照"]
    free_basis = params["collector_output"]["外部平台原始采集"]["required_flow_by_item"]["800737120171"]["free_flow_basis"]
    assert free_basis["search_visitor_compare"] == 51
    assert free_basis["search_visitor_change_pct"] == pytest.approx(-3.92)
    assert free_basis["search_conversion_compare_rate"] == 0.08
    assert free_basis["search_conversion_change_pct"] == pytest.approx(-50.0)
    assert params["baseline_collector_compare"]["applied_item_count"] == 1
    assert params["baseline_collector_run_id"] == "collector-run-baseline"
    assert params["baseline_collector_decision_log_id"] == baseline_decision_id
    assert params["baseline_collector_output"] == baseline_output
    assert params["baseline_snapshot"] == baseline_output["数据采集快照"]


@pytest.mark.asyncio
async def test_collector_success_does_not_trigger_draft_operator(client):
    from app.database import async_session_factory
    from app.execution.execution_service import ExecutionService
    from app.skills.core.models import Skill

    svc = ExecutionService()
    svc.execute_skill = AsyncMock(return_value={"run_id": "operator-run"})

    async with async_session_factory() as session:
        await session.merge(
            Skill(
                id=svc.OPERATOR_SKILL_ID,
                name="Link Decline Operator",
                department="EC",
                visibility="company",
                status="draft",
            )
        )
        await session.commit()

    await svc._trigger_link_decline_operator_if_needed(
        collector_skill_id=svc.COLLECTOR_SKILL_ID,
        collector_run_id="collector-run-draft",
        collector_decision_log_id=103,
        collector_output={"collection_schema": "tmall_link_decline_collection_v1"},
    )

    svc.execute_skill.assert_not_awaited()


@pytest.mark.asyncio
async def test_collector_success_enqueues_durable_operator_trigger(client):
    from app.database import async_session_factory
    from app.execution.execution_service import ExecutionService
    from app.execution.queue_models import ExecutionQueueTask
    from app.skills.core.models import Skill

    svc = ExecutionService()

    async with async_session_factory() as session:
        await session.merge(
            Skill(
                id=svc.OPERATOR_SKILL_ID,
                name="Link Decline Operator",
                department="EC",
                visibility="company",
                status="active",
            )
        )
        await session.commit()

    result = await svc.enqueue_link_decline_operator_trigger_if_needed(
        collector_skill_id=svc.COLLECTOR_SKILL_ID,
        collector_run_id="collector-run-queued",
        collector_decision_log_id=201,
        collector_output={"collection_schema": "tmall_link_decline_collection_v1", "rows": [1]},
        created_by="test",
        process_now=False,
    )

    assert result["status"] == "enqueued"
    async with async_session_factory() as session:
        task = (
            await session.execute(
                select(ExecutionQueueTask).where(
                    ExecutionQueueTask.task_type == svc.OPERATOR_TRIGGER_TASK_TYPE,
                    ExecutionQueueTask.payload["collector_run_id"].as_string() == "collector-run-queued",
                )
            )
        ).scalar_one()

    assert task.status == "pending"
    assert task.skill_id == svc.OPERATOR_SKILL_ID
    assert task.payload == {
        "collector_skill_id": svc.COLLECTOR_SKILL_ID,
        "collector_run_id": "collector-run-queued",
        "collector_decision_log_id": 201,
        "collection_schema": "tmall_link_decline_collection_v1",
    }


@pytest.mark.asyncio
async def test_operator_trigger_task_loads_output_from_decision_log(client):
    from app.database import async_session_factory
    from app.execution.execution_service import ExecutionService
    from app.execution.models import DecisionLog
    from app.execution.queue_models import ExecutionQueueTask
    from app.execution.task_queue import task_queue
    from app.skills.core.models import Skill

    svc = ExecutionService()
    svc.execute_skill = AsyncMock(return_value={"run_id": "operator-run-from-queue"})
    collector_output = {
        "collection_schema": "tmall_link_decline_collection_v1",
        "rows": [{"item_id": "1"}],
    }

    async with async_session_factory() as session:
        await session.execute(
            delete(ExecutionQueueTask).where(
                ExecutionQueueTask.task_type == svc.OPERATOR_TRIGGER_TASK_TYPE
            )
        )
        await session.merge(
            Skill(
                id=svc.OPERATOR_SKILL_ID,
                name="Link Decline Operator",
                department="EC",
                visibility="company",
                status="active",
            )
        )
        decision = DecisionLog(
            run_id="collector-run-from-log",
            skill_id=svc.COLLECTOR_SKILL_ID,
            input_snapshot={},
            output_result=collector_output,
            approval_level=0,
            is_sandbox=False,
        )
        session.add(decision)
        await session.flush()
        decision_log_id = decision.id
        await session.commit()

    await task_queue.enqueue(
        task_type=svc.OPERATOR_TRIGGER_TASK_TYPE,
        skill_id=svc.OPERATOR_SKILL_ID,
        payload={
            "collector_skill_id": svc.COLLECTOR_SKILL_ID,
            "collector_run_id": "collector-run-from-log",
            "collector_decision_log_id": decision_log_id,
            "collection_schema": "tmall_link_decline_collection_v1",
        },
    )

    result = await svc.process_link_decline_operator_queue_once(worker_id="test-worker")

    assert result["status"] == "triggered"
    svc.execute_skill.assert_awaited_once()
    kwargs = svc.execute_skill.await_args.kwargs
    assert kwargs["parent_run_id"] == "collector-run-from-log"
    assert kwargs["params"]["collector_output"] == collector_output


@pytest.mark.asyncio
async def test_operator_trigger_bridge_offline_retries_with_backoff(client):
    from app.common.exceptions import AppError
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.execution.execution_service import ExecutionService
    from app.execution.models import DecisionLog, ExecutionRun
    from app.execution.queue_models import ExecutionQueueTask
    from app.execution.task_queue import task_queue
    from app.skills.core.models import Skill

    svc = ExecutionService()
    svc.execute_skill = AsyncMock(
        side_effect=AppError("BRIDGE_OFFLINE", 503, {"detail": "实例 内容电商 的 bridge 未连接"})
    )
    collector_output = {
        "collection_schema": "tmall_link_decline_collection_v1",
        "数据采集快照": {"schedule_time": "07:50"},
    }

    async with async_session_factory() as session:
        await session.merge(
            Skill(
                id=svc.OPERATOR_SKILL_ID,
                name="Link Decline Operator",
                department="EC",
                visibility="company",
                status="active",
            )
        )
        session.add(
            ExecutionRun(
                id="collector-run-offline",
                skill_id=svc.COLLECTOR_SKILL_ID,
                trigger_type="node_scheduler",
                started_at=now_bjt(),
                status="completed",
            )
        )
        decision = DecisionLog(
            run_id="collector-run-offline",
            skill_id=svc.COLLECTOR_SKILL_ID,
            input_snapshot={},
            output_result=collector_output,
            approval_level=0,
            is_sandbox=False,
        )
        session.add(decision)
        await session.flush()
        decision_log_id = decision.id
        await session.commit()

    task_id = await task_queue.enqueue(
        task_type=svc.OPERATOR_TRIGGER_TASK_TYPE,
        skill_id=svc.OPERATOR_SKILL_ID,
        payload={
            "collector_skill_id": svc.COLLECTOR_SKILL_ID,
            "collector_run_id": "collector-run-offline",
            "collector_decision_log_id": decision_log_id,
            "collection_schema": "tmall_link_decline_collection_v1",
        },
        max_retries=3,
    )

    with pytest.raises(AppError):
        await svc.process_link_decline_operator_queue_once(worker_id="test-worker")

    async with async_session_factory() as session:
        task = await session.get(ExecutionQueueTask, task_id)
        child_runs = (
            await session.execute(
                select(ExecutionRun).where(
                    ExecutionRun.skill_id == svc.OPERATOR_SKILL_ID,
                    ExecutionRun.parent_run_id == "collector-run-offline",
                )
            )
        ).scalars().all()

    assert task.status == "pending"
    assert task.retry_count == 1
    assert task.next_attempt_at > now_bjt()
    assert "BRIDGE_OFFLINE" in task.error_message
    assert child_runs == []
