"""节点调度（Node Scheduling）端到端测试。

覆盖：
- push_schedules_to_targets（定时配置推送 + DB 记录）
- push_skill_to_targets 后自动推送定时配置
- submit-result 接口的 instance_id / run_mode
- normalize_run_mode 对 node_scheduler 触发的映射
- _execute_skill_job 节点已上报时跳过集中执行
- bridge 端 _cron_match / _field_match 纯函数
- bridge 端 handle_bridge_op sync_schedules 操作
"""

from __future__ import annotations

import json
import os
import sys
import base64
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

import app.database as db_mod
from app.execution.execution_service import normalize_run_mode, verify_run_token
from app.execution.models import (
    DecisionLog,
    ExecutionArtifact,
    ExecutionRun,
    ExecutionStep,
    NodeScheduleConfig,
    OpenClawInstance,
    RUN_MODE_MANUAL_REAL,
    RUN_MODE_SCHEDULED_REAL,
)
from app.learning.models import LearningArtifact, LearningEvent, LearningFlowEdge
from app.skills.core.models import Skill
from app.training.models import TrainingJob, TrainingModelDeployment


# ---------------------------------------------------------------------------
# 辅助：构造测试数据
# ---------------------------------------------------------------------------

def _make_skill(
    skill_id: str = "test-cron",
    *,
    trigger_type: str = "cron",
    trigger_expression: str = "0 9 * * 1-5",
    status: str = "active",
    department: str = "EC",
) -> Skill:
    """创建一条 Skill 记录（仅内存对象，需要 session.add）。"""
    return Skill(
        id=skill_id,
        name=f"测试Skill-{skill_id}",
        department=department,
        trigger_type=trigger_type,
        trigger_expression=trigger_expression,
        status=status,
        owner="admin",
    )


def _make_instance(
    instance_id: str,
    *,
    department: str = "EC",
    is_active: bool = True,
) -> OpenClawInstance:
    """创建一条 OpenClawInstance 记录。"""
    return OpenClawInstance(
        id=instance_id,
        name=f"节点-{instance_id}",
        department=department,
        gateway_url=f"http://{instance_id}:9000",
        reload_hook_url=f"http://{instance_id}:9000",
        reload_token="tok",
        is_active=is_active,
    )


def _make_active_model_deployment(
    *,
    skill_id: str = "test-cron",
    job_id: str = "job-runtime-model-1",
    deployment_id: str = "deploy-runtime-model-1",
    gateway_id: str = "node-1",
    artifact_uri: str = "/tmp/skillforge-state/training/artifacts/model.tar.gz",
) -> tuple[TrainingJob, TrainingModelDeployment]:
    job = TrainingJob(
        id=job_id,
        title="节点运行模型训练",
        department="EC",
        created_by="trainer",
        status="completed",
        target_skill_id=skill_id,
        target_gateway_id=gateway_id,
        dataset_ref="learning-artifacts:EC",
    )
    deployment = TrainingModelDeployment(
        id=deployment_id,
        job_id=job_id,
        department="EC",
        model_family="lora",
        artifact_id="artifact-runtime-model-1",
        artifact_ref_json={
            "id": "artifact-runtime-model-1",
            "uri": artifact_uri,
            "sha256": "a" * 64,
        },
        target_skill_ids_json=[skill_id],
        status="active",
        rollout_percent=100,
        requested_by="trainer",
    )
    return job, deployment


def _make_actor() -> SimpleNamespace:
    return SimpleNamespace(
        id="admin",
        role="admin",
        department="EC",
        can_view_all=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Test 1: push_schedules_to_targets — 定时配置推送到多节点
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_push_schedules_to_targets(client, monkeypatch):
    """验证 push_schedules_to_targets 对每个 active 实例调用 sync_schedules，
    并在 DB 中写入 NodeScheduleConfig 记录。"""

    # 准备 DB 数据
    async with db_mod.async_session_factory() as session:
        session.add(_make_skill("test-cron"))
        session.add(_make_instance("node-1"))
        session.add(_make_instance("node-2"))
        await session.commit()

    # mock AIClawClient.sync_schedules，记录调用参数
    sync_calls: list[dict] = []

    async def fake_sync_schedules(self, *, schedules, submit_token, submit_url, config_version):
        sync_calls.append({
            "instance_id": self.instance_id,
            "schedules": schedules,
            "config_version": config_version,
        })
        return {"accepted": len(schedules), "config_version": config_version}

    async def fake_list_local_skills(self, target_dir=None):
        return {"skills": ["test-cron"], "dir": "/tmp/skills"}

    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.sync_schedules",
        fake_sync_schedules,
    )
    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.list_local_skills",
        fake_list_local_skills,
    )
    monkeypatch.setattr("app.config.settings.BROWSER_REMOTE_TOKEN", "test-token")
    monkeypatch.setattr("app.config.settings.SCHEDULER_TIMEZONE", "Asia/Shanghai")

    from app.execution.sync_service import sync_service

    results = await sync_service.push_schedules_to_targets(
        skill_ids=["test-cron"],
    )
    assert all(item["accepted"] == 1 for item in results)
    assert all(item["config_version"] for item in results)

    # 断言：sync_schedules 对每个实例各调用一次
    assert len(sync_calls) == 2
    called_instances = {c["instance_id"] for c in sync_calls}
    assert called_instances == {"node-1", "node-2"}

    # 断言：每次调用都携带正确的 schedule entry
    for call in sync_calls:
        assert len(call["schedules"]) == 1
        assert call["config_version"]
        sched = call["schedules"][0]
        assert sched["skill_id"] == "test-cron"
        assert sched["cron"] == "0 9 * * 1-5"
        assert sched["timezone"] == "Asia/Shanghai"
        assert sched["enabled"] is True
        assert sched["runtime"]["backend"] == "bridge_script"
        assert sched["runtime"]["script_entry"] == "scripts/main.py"
        assert sched["skill_git_commit_full"] is None

    # 断言：NodeScheduleConfig 记录已写入 DB
    from sqlalchemy import select

    async with db_mod.async_session_factory() as session:
        configs = (await session.execute(select(NodeScheduleConfig))).scalars().all()
        assert len(configs) == 2
        for cfg in configs:
            assert cfg.skill_id == "test-cron"
            assert cfg.cron_expression == "0 9 * * 1-5"
            assert cfg.ack_ok is True
            assert cfg.runtime_backend == "bridge_script"
            assert cfg.runtime_config["backend"] == "bridge_script"

    # 断言：返回值正确
    assert len(results) == 2
    for r in results:
        assert r["ok"] is True


@pytest.mark.asyncio
async def test_push_schedules_includes_active_model_context_per_target_node(client, monkeypatch):
    """节点 schedule 下发应把 active 模型部署作为 Skill runtime 输入上下文。"""

    async with db_mod.async_session_factory() as session:
        node_1 = _make_instance("node-1")
        node_1.bridge_gateway_kind = "openclaw"
        node_1.bridge_capabilities_json = json.dumps({"ops": ["run_skill_script", "training.inference"]})
        node_2 = _make_instance("node-2")
        node_2.bridge_gateway_kind = "openclaw"
        node_2.bridge_capabilities_json = json.dumps({"ops": ["run_skill_script", "training.inference"]})
        job, deployment = _make_active_model_deployment(gateway_id="node-1")
        session.add(_make_skill("test-cron"))
        session.add(node_1)
        session.add(node_2)
        session.add(job)
        session.add(deployment)
        await session.commit()

    sync_calls: list[dict] = []

    async def fake_sync_schedules(self, *, schedules, submit_token, submit_url, config_version):
        sync_calls.append({
            "instance_id": self.instance_id,
            "schedules": schedules,
            "config_version": config_version,
        })
        return {"accepted": len(schedules), "config_version": config_version}

    async def fake_list_local_skills(self, target_dir=None):
        return {"skills": ["test-cron"], "dir": "/tmp/skills"}

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.sync_schedules", fake_sync_schedules)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.list_local_skills", fake_list_local_skills)

    from app.execution.sync_service import sync_service

    results = await sync_service.push_schedules_to_targets(skill_ids=["test-cron"])

    assert {item["instance_id"] for item in results} == {"node-1", "node-2"}
    by_node = {call["instance_id"]: call["schedules"][0] for call in sync_calls}
    node_1_context = by_node["node-1"]["model_context"]
    node_1_deployment = node_1_context["active_model_deployment"]
    assert node_1_context["control"]["agent_contract"] == "skill_runtime_model_context.v1"
    assert node_1_deployment["model_deployment_id"] == "deploy-runtime-model-1"
    assert node_1_deployment["artifact_sha256"] == "a" * 64
    assert node_1_deployment["artifact_uri"] == "/tmp/skillforge-state/training/artifacts/model.tar.gz"
    assert node_1_deployment["runtime_status"]["target_gateway_id"] == "node-1"
    assert node_1_deployment["runtime_status"]["inference_ready_hint"] is True

    node_2_deployment = by_node["node-2"]["model_context"]["active_model_deployment"]
    assert node_2_deployment["model_deployment_id"] == "deploy-runtime-model-1"
    assert "artifact_uri" not in node_2_deployment
    assert node_2_deployment["runtime_status"]["target_gateway_id"] == "node-1"
    assert node_2_deployment["runtime_status"]["local_artifact_available"] is False

    from sqlalchemy import select

    async with db_mod.async_session_factory() as session:
        configs = (await session.execute(select(NodeScheduleConfig))).scalars().all()
    assert len(configs) == 2
    tracked = {
        cfg.instance_id: (cfg.runtime_config or {}).get("_model_context")
        for cfg in configs
    }
    assert tracked["node-1"]["model_deployment_id"] == "deploy-runtime-model-1"
    assert tracked["node-2"]["model_deployment_id"] == "deploy-runtime-model-1"
    assert tracked["node-1"]["sha256"] != tracked["node-2"]["sha256"]


def test_normalize_runtime_config_allows_90_minute_collection_timeout():
    from app.execution.sync_service import _normalize_runtime_config

    runtime = _normalize_runtime_config({
        "runtime": {
            "backend": "bridge_script",
            "script_entry": "scripts/main.py",
            "timeout": 5400,
        }
    })

    assert runtime["timeout"] == 5400


@pytest.mark.asyncio
async def test_push_schedules_uses_node_submit_token(client, monkeypatch):
    """节点定时回传使用平台签发的节点提交 token，不依赖全局浏览器 token。"""

    async with db_mod.async_session_factory() as session:
        session.add(_make_skill("token-cron"))
        session.add(_make_instance("node-token"))
        await session.commit()

    sync_calls: list[dict] = []

    async def fake_sync_schedules(self, *, schedules, submit_token, submit_url, config_version):
        sync_calls.append({
            "instance_id": self.instance_id,
            "submit_token": submit_token,
            "schedules": schedules,
        })
        return {"accepted": len(schedules), "config_version": config_version}

    async def fake_list_local_skills(self, target_dir=None):
        return {"skills": ["token-cron"], "dir": "/tmp/skills"}

    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.sync_schedules",
        fake_sync_schedules,
    )
    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.list_local_skills",
        fake_list_local_skills,
    )
    monkeypatch.setattr("app.config.settings.BROWSER_REMOTE_TOKEN", "")

    from app.execution.router import verify_node_submit_token
    from app.execution.sync_service import sync_service

    await sync_service.push_schedules_to_targets(
        skill_ids=["token-cron"],
        target_instance_ids=["node-token"],
    )

    assert len(sync_calls) == 1
    claims = verify_node_submit_token(
        sync_calls[0]["submit_token"],
        instance_id="node-token",
    )
    assert claims["aud"] == "node_schedule_submit"


@pytest.mark.asyncio
async def test_push_schedules_removes_stale_node_configs(client, monkeypatch):
    """sync_schedules 会覆盖节点配置，DB 也必须清理该节点旧配置。"""

    async with db_mod.async_session_factory() as session:
        session.add(_make_skill("new-cron"))
        session.add(_make_skill("old-cron", status="deleted", trigger_expression="0 8 * * *"))
        session.add(_make_instance("node-stale"))
        session.add(NodeScheduleConfig(
            instance_id="node-stale",
            skill_id="old-cron",
            cron_expression="0 8 * * *",
            config_version=1,
            ack_ok=True,
        ))
        await session.commit()

    async def fake_sync_schedules(self, *, schedules, submit_token, submit_url, config_version):
        assert self.instance_id == "node-stale"
        assert [item["skill_id"] for item in schedules] == ["new-cron"]
        return {"accepted": len(schedules), "config_version": config_version}

    async def fake_list_local_skills(self, target_dir=None):
        return {"skills": ["new-cron"], "dir": "/tmp/skills"}

    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.sync_schedules",
        fake_sync_schedules,
    )
    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.list_local_skills",
        fake_list_local_skills,
    )

    from app.execution.sync_service import sync_service

    await sync_service.push_schedules_to_targets(
        skill_ids=["new-cron"],
        target_instance_ids=["node-stale"],
    )

    from sqlalchemy import select

    async with db_mod.async_session_factory() as session:
        configs = (await session.execute(
            select(NodeScheduleConfig).where(NodeScheduleConfig.instance_id == "node-stale")
        )).scalars().all()

    assert len(configs) == 1
    assert configs[0].skill_id == "new-cron"
    assert configs[0].ack_ok is True


@pytest.mark.asyncio
async def test_push_schedules_keeps_archived_cron_configs(client, monkeypatch):
    """archived 但仍有 cron 且已在节点配置中的任务，应继续下发运行。"""

    async with db_mod.async_session_factory() as session:
        session.add(_make_skill("new-cron"))
        session.add(_make_skill("archived-cron", status="archived", trigger_expression="0 8 * * *"))
        session.add(_make_instance("node-archived"))
        session.add(NodeScheduleConfig(
            instance_id="node-archived",
            skill_id="archived-cron",
            cron_expression="0 8 * * *",
            config_version=1,
            ack_ok=True,
        ))
        await session.commit()

    sync_payloads: list[list[str]] = []

    async def fake_sync_schedules(self, *, schedules, submit_token, submit_url, config_version):
        assert self.instance_id == "node-archived"
        sync_payloads.append([item["skill_id"] for item in schedules])
        return {"accepted": len(schedules), "config_version": config_version}

    async def fake_list_local_skills(self, target_dir=None):
        return {"skills": ["archived-cron", "new-cron"], "dir": "/tmp/skills"}

    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.sync_schedules",
        fake_sync_schedules,
    )
    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.list_local_skills",
        fake_list_local_skills,
    )

    from app.execution.sync_service import sync_service

    await sync_service.push_schedules_to_targets(
        skill_ids=["new-cron"],
        target_instance_ids=["node-archived"],
    )

    assert sync_payloads == [["archived-cron", "new-cron"]]

    from sqlalchemy import select

    async with db_mod.async_session_factory() as session:
        configs = (await session.execute(
            select(NodeScheduleConfig).where(NodeScheduleConfig.instance_id == "node-archived")
        )).scalars().all()

    assert {cfg.skill_id for cfg in configs} == {"archived-cron", "new-cron"}
    assert all(cfg.ack_ok is True for cfg in configs)


@pytest.mark.asyncio
async def test_push_schedules_preserves_push_marker_when_snapshot_unchanged(client, monkeypatch):
    """重连重推相同快照不能刷新 pushed_at，否则漏跑 watchdog 会误判为变更前。"""

    pushed_at = datetime(2026, 5, 15, 10, 0, 0)
    runtime = {
        "backend": "bridge_script",
        "fallback": None,
        "entry": "SKILL.md",
        "script_entry": "scripts/main.py",
        "timeout": 300,
        "tools": [],
        "output_schema": "contract.json",
        "policy_pack": "policy_pack.yaml",
    }
    async with db_mod.async_session_factory() as session:
        session.add(_make_skill("same-cron", trigger_expression="20 11 * * *"))
        session.add(_make_instance("node-same"))
        session.add(NodeScheduleConfig(
            instance_id="node-same",
            skill_id="same-cron",
            cron_expression="20 11 * * *",
            config_version=123,
            pushed_at=pushed_at,
            ack_ok=True,
            runtime_backend="bridge_script",
            runtime_config=runtime,
        ))
        await session.commit()

    async def fake_sync_schedules(self, *, schedules, submit_token, submit_url, config_version):
        assert self.instance_id == "node-same"
        assert [item["skill_id"] for item in schedules] == ["same-cron"]
        return {"accepted": len(schedules), "config_version": config_version}

    async def fake_list_local_skills(self, target_dir=None):
        return {"skills": ["same-cron"], "dir": "/tmp/skills"}

    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.sync_schedules",
        fake_sync_schedules,
    )
    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.list_local_skills",
        fake_list_local_skills,
    )

    from app.execution.sync_service import sync_service

    await sync_service.push_schedules_to_targets(target_instance_ids=["node-same"])

    from sqlalchemy import select

    async with db_mod.async_session_factory() as session:
        cfg = (await session.execute(
            select(NodeScheduleConfig).where(NodeScheduleConfig.instance_id == "node-same")
        )).scalar_one()

    assert cfg.config_version == 123
    assert cfg.pushed_at == pushed_at
    assert cfg.ack_ok is True


@pytest.mark.asyncio
async def test_push_schedules_clears_non_platform_node_on_platform_host(client, monkeypatch):
    """业务节点如果误挂到平台主机，应清空定时快照，避免在平台机执行。"""

    async with db_mod.async_session_factory() as session:
        platform = _make_instance("platform", department=None)
        platform.is_platform_default = True
        platform.bridge_fingerprint = "platform-host"
        business = _make_instance("biz-node")
        business.bridge_fingerprint = "platform-host"
        session.add(_make_skill("test-cron"))
        session.add(platform)
        session.add(business)
        session.add(NodeScheduleConfig(
            instance_id="biz-node",
            skill_id="test-cron",
            cron_expression="0 9 * * *",
            config_version=1,
            ack_ok=True,
        ))
        await session.commit()

    sync_calls: list[dict] = []

    async def fake_sync_schedules(self, *, schedules, submit_token, submit_url, config_version):
        sync_calls.append({"instance_id": self.instance_id, "schedules": schedules})
        return {"accepted": len(schedules), "config_version": config_version}

    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.sync_schedules",
        fake_sync_schedules,
    )

    from app.execution.sync_service import sync_service

    results = await sync_service.push_schedules_to_targets(
        skill_ids=["test-cron"],
        target_instance_ids=["biz-node"],
    )

    assert sync_calls == [{"instance_id": "biz-node", "schedules": []}]
    assert results[0]["ok"] is True
    assert "BRIDGE_PLACEMENT_CONFLICT" in (results[0]["error"] or "")

    from sqlalchemy import select

    async with db_mod.async_session_factory() as session:
        configs = (await session.execute(
            select(NodeScheduleConfig).where(NodeScheduleConfig.instance_id == "biz-node")
        )).scalars().all()

    assert configs == []


def test_bridge_placement_matches_structured_and_legacy_fingerprints():
    from app.execution.bridge_placement import bridge_fingerprints_match

    assert bridge_fingerprints_match("platform-host", "platform-host")
    assert bridge_fingerprints_match(
        "host:platform-host|machine:abc123",
        "host:platform-host|machine:abc123",
    )
    assert bridge_fingerprints_match(
        "platform-host",
        "host:platform-host|machine:abc123",
    )
    assert not bridge_fingerprints_match(
        "host:business-host|machine:def456",
        "host:platform-host|machine:abc123",
    )



@pytest.mark.asyncio
async def test_timeout_cleanup_allows_long_node_collection(client):
    """节点侧慢采集不应被平台 30 分钟清理任务提前标记 timeout。"""
    from uuid import uuid4

    from sqlalchemy import select

    from app.common.time_utils import now_bjt
    from app.execution.scheduler import _job_timeout_cleanup
    from app.tasktree.models import TaskNodeLight

    old_started_at = now_bjt() - timedelta(minutes=40)
    suffix = uuid4().hex[:8]
    platform_run_id = f"timeout-platform-{suffix}"
    node_run_id = f"long-node-{suffix}"
    async with db_mod.async_session_factory() as session:
        session.add_all([
            ExecutionRun(
                id=platform_run_id,
                skill_id="timeout-skill",
                trigger_type="manual",
                run_mode=RUN_MODE_MANUAL_REAL,
                started_at=old_started_at,
                status="running",
                completed_steps=0,
                source_instance_id=None,
            ),
            ExecutionRun(
                id=node_run_id,
                skill_id="timeout-skill",
                trigger_type="node_scheduler",
                run_mode=RUN_MODE_SCHEDULED_REAL,
                started_at=old_started_at,
                status="running",
                completed_steps=0,
                source_instance_id="node-a",
            ),
        ])
        session.add_all([
            TaskNodeLight(
                root_id=uuid4(),
                source_run_id=platform_run_id,
                department_id="unknown",
                node_type="execution",
                title="timeout platform",
                status="running",
                sort_key="timeout platform",
                started_at=old_started_at,
            ),
            TaskNodeLight(
                root_id=uuid4(),
                source_run_id=node_run_id,
                department_id="unknown",
                node_type="execution",
                title="long node",
                status="running",
                sort_key="long node",
                started_at=old_started_at,
            ),
        ])
        await session.commit()

    await _job_timeout_cleanup()

    async with db_mod.async_session_factory() as session:
        statuses = dict(
            (
                await session.execute(
                    select(ExecutionRun.id, ExecutionRun.status)
                    .where(ExecutionRun.id.in_([platform_run_id, node_run_id]))
                )
            ).all()
        )
        tasktree_statuses = dict(
            (
                await session.execute(
                    select(TaskNodeLight.source_run_id, TaskNodeLight.status)
                    .where(TaskNodeLight.source_run_id.in_([platform_run_id, node_run_id]))
                )
            ).all()
        )

    assert statuses[platform_run_id] == "timeout"
    assert statuses[node_run_id] == "running"
    assert tasktree_statuses[platform_run_id] == "timeout"
    assert tasktree_statuses[node_run_id] == "running"


@pytest.mark.asyncio
async def test_get_instance_schedules_uses_node_scheduled_runs_only(client):
    """节点定时页不应混入手动执行和其它节点的定时执行。"""
    from app.tasktree.schedule_query import get_instance_schedules

    async with db_mod.async_session_factory() as session:
        session.add(_make_instance("node-sched"))
        session.add(_make_instance("other-node"))
        session.add(_make_skill("active-cron", trigger_expression="50 7 * * *"))
        session.add(_make_skill("archived-cron", status="archived", trigger_expression="0 9 * * *"))
        session.add(_make_skill("stopped-cron", trigger_type="manual", trigger_expression=None))
        session.add(NodeScheduleConfig(
            instance_id="node-sched",
            skill_id="active-cron",
            cron_expression="50 7 * * *",
            config_version=2,
            ack_ok=True,
        ))
        session.add_all([
            ExecutionRun(
                id="run-node-active",
                skill_id="active-cron",
                trigger_type="node_scheduler",
                run_mode=RUN_MODE_SCHEDULED_REAL,
                source_instance_id="node-sched",
                status="completed",
                started_at=datetime(2026, 4, 25, 0, 0, 0),
                completed_at=datetime(2026, 4, 25, 0, 1, 0),
            ),
            ExecutionRun(
                id="run-manual-active",
                skill_id="active-cron",
                trigger_type="manual:admin",
                run_mode=RUN_MODE_MANUAL_REAL,
                source_instance_id="node-sched",
                status="failed",
                started_at=datetime(2026, 4, 25, 0, 2, 0),
                completed_at=datetime(2026, 4, 25, 0, 3, 0),
            ),
            ExecutionRun(
                id="run-other-node",
                skill_id="active-cron",
                trigger_type="node_scheduler",
                run_mode=RUN_MODE_SCHEDULED_REAL,
                source_instance_id="other-node",
                status="failed",
                started_at=datetime(2026, 4, 25, 0, 4, 0),
                completed_at=datetime(2026, 4, 25, 0, 5, 0),
            ),
            ExecutionRun(
                id="run-node-archived",
                skill_id="archived-cron",
                trigger_type="node_scheduler",
                run_mode=RUN_MODE_SCHEDULED_REAL,
                source_instance_id="node-sched",
                status="completed",
                started_at=datetime(2026, 4, 24, 0, 0, 0),
                completed_at=datetime(2026, 4, 24, 0, 2, 0),
            ),
            ExecutionRun(
                id="run-node-stopped",
                skill_id="stopped-cron",
                trigger_type="node_scheduler",
                run_mode=RUN_MODE_SCHEDULED_REAL,
                source_instance_id="node-sched",
                status="failed",
                started_at=datetime(2026, 4, 23, 0, 0, 0),
                completed_at=datetime(2026, 4, 23, 0, 2, 0),
            ),
        ])
        session.add_all([
            DecisionLog(run_id="run-node-active", skill_id="active-cron", input_snapshot={}, output_result={"ok": True}),
            DecisionLog(run_id="run-manual-active", skill_id="active-cron", input_snapshot={}, output_result={"ok": False}),
            DecisionLog(run_id="run-node-archived", skill_id="archived-cron", input_snapshot={}, output_result={"ok": True}),
        ])
        await session.commit()

    async with db_mod.async_session_factory() as session:
        response = await get_instance_schedules(session, "node-sched")

    jobs = {job.skill_id: job for job in response.jobs}
    assert set(jobs) == {"active-cron", "archived-cron", "stopped-cron"}
    assert jobs["active-cron"].cron_expression == "50 7 * * *"
    assert jobs["active-cron"].status == "active"
    assert jobs["active-cron"].total_runs == 1
    assert jobs["active-cron"].success_count == 1
    assert jobs["active-cron"].failed_count == 0
    assert jobs["active-cron"].total_output_items == 1
    assert jobs["archived-cron"].status == "archived"
    assert jobs["archived-cron"].total_runs == 1
    assert jobs["stopped-cron"].status == "stopped"
    assert jobs["stopped-cron"].cron_expression is None
    assert jobs["stopped-cron"].failed_count == 1


@pytest.mark.asyncio
async def test_platform_default_schedules_hide_historic_not_scheduled_business_runs(client):
    """平台主节点不应因历史兜底/误装运行显示未下发业务任务。"""
    from app.tasktree.schedule_query import get_instance_schedules

    async with db_mod.async_session_factory() as session:
        platform = _make_instance("platform", department=None)
        platform.is_platform_default = True
        session.add(platform)
        session.add(_make_skill("business-cron", trigger_expression="50 8,12 * * *"))
        session.add(ExecutionRun(
            id="run-platform-history",
            skill_id="business-cron",
            trigger_type="node_scheduler",
            run_mode=RUN_MODE_SCHEDULED_REAL,
            source_instance_id="platform",
            status="failed",
            started_at=datetime(2026, 4, 29, 16, 50, 0),
            completed_at=datetime(2026, 4, 29, 16, 50, 1),
        ))
        await session.commit()

    async with db_mod.async_session_factory() as session:
        response = await get_instance_schedules(session, "platform")

    assert response.jobs == []



# ═══════════════════════════════════════════════════════════════════════════
#  Test 2: push_skill_to_targets 后自动推送定时配置
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_push_schedules_auto_after_file_sync(client, tmp_path, monkeypatch):
    """验证 push_skill_to_targets 在文件同步成功后自动推送定时配置。"""

    # 准备 DB 数据
    async with db_mod.async_session_factory() as session:
        session.add(_make_skill("test-cron-auto"))
        session.add(_make_instance("node-a"))
        session.add(_make_instance("node-b"))
        await session.commit()

    # 准备 Skill 文件目录
    skill_dir = tmp_path / "test-cron-auto"
    skill_dir.mkdir()
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test Cron Skill\n", encoding="utf-8")
    (scripts_dir / "main.py").write_text("def main(payload):\n    return payload\n", encoding="utf-8")

    monkeypatch.setattr("app.config.settings.SKILL_REPO_PATH", str(tmp_path))
    monkeypatch.setattr("app.config.settings.BROWSER_REMOTE_TOKEN", "test-token")
    monkeypatch.setattr("app.config.settings.SCHEDULER_TIMEZONE", "Asia/Shanghai")

    # mock bridge 操作
    install_calls: list[str] = []
    reload_calls: list[str] = []
    schedule_calls: list[str] = []

    installed_files: dict[str, list[dict]] = {}

    async def fake_install_skill(self, skill_id, files, target_dir=None, shared_files=None):
        install_calls.append(self.instance_id)
        installed_files[self.instance_id] = files
        return {"ok": True, "installed": True}

    async def fake_read_skill_from_device(self, skill_id, target_dir=None):
        return {"files": installed_files[self.instance_id]}

    async def fake_reload_skills(self):
        reload_calls.append(self.instance_id)
        return {"ok": True, "status": "ok"}

    async def fake_sync_schedules(self, *, schedules, submit_token, submit_url, config_version):
        schedule_calls.append(self.instance_id)
        return {"accepted": len(schedules), "config_version": config_version}

    async def fake_list_local_skills(self, target_dir=None):
        return {"skills": ["test-cron-auto"], "dir": "/tmp/skills"}

    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.install_skill",
        fake_install_skill,
    )
    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.reload_skills",
        fake_reload_skills,
    )
    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.read_skill_from_device",
        fake_read_skill_from_device,
    )
    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.sync_schedules",
        fake_sync_schedules,
    )
    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.list_local_skills",
        fake_list_local_skills,
    )

    from app.execution.sync_service import sync_service

    actor = _make_actor()
    results = await sync_service.push_skill_to_targets(
        "test-cron-auto",
        actor=actor,
    )

    # 断言：install_skill 对每个实例调用
    assert len(install_calls) == 2
    # 断言：sync_schedules 在 install 之后被调用
    assert len(schedule_calls) == 2


@pytest.mark.asyncio
async def test_push_skill_sync_fails_when_node_readback_differs(client, tmp_path, monkeypatch):
    """install_skill 返回成功但节点读回入口脚本不一致时，不能标记同步成功。"""

    async with db_mod.async_session_factory() as session:
        inst = _make_instance("node-verify", department="EC")
        inst.bridge_skills_dir = "/tmp/openclaw-skills"
        session.add(_make_skill("test-readback"))
        session.add(inst)
        await session.commit()

    skill_dir = tmp_path / "test-readback"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Test Readback Skill\n", encoding="utf-8")
    (scripts_dir / "main.py").write_text("def main(payload):\n    return payload\n", encoding="utf-8")

    monkeypatch.setattr("app.config.settings.SKILL_REPO_PATH", str(tmp_path))
    monkeypatch.setattr("app.config.settings.BROWSER_REMOTE_TOKEN", "test-token")

    install_calls: list[dict] = []

    async def fake_install_skill(self, skill_id, files, target_dir=None, shared_files=None):
        install_calls.append({"instance_id": self.instance_id, "target_dir": target_dir})
        return {"ok": True, "installed": True}

    async def fake_read_skill_from_device(self, skill_id, target_dir=None):
        stale = base64.b64encode(b"def main(payload):\n    return {'stale': True}\n").decode("ascii")
        return {"files": [{"path": "scripts/main.py", "content_b64": stale}]}

    async def fake_reload_skills(self):
        raise AssertionError("reload must not run when install verification fails")

    monkeypatch.setattr("app.aiclaw.client.AIClawClient.install_skill", fake_install_skill)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.read_skill_from_device", fake_read_skill_from_device)
    monkeypatch.setattr("app.aiclaw.client.AIClawClient.reload_skills", fake_reload_skills)

    from app.execution.sync_service import sync_service

    results = await sync_service.push_skill_to_targets(
        "test-readback",
        actor=_make_actor(),
        target_instance_ids=["node-verify"],
    )

    assert install_calls == [{"instance_id": "node-verify", "target_dir": "/tmp/openclaw-skills"}]
    assert len(results) == 1
    assert results[0]["ok"] is False
    assert results[0]["error"] == "INSTALL_VERIFY_FAILED"
    assert results[0]["result"]["verify"]["ok"] is False
    assert results[0]["result"]["verify"]["expected_sha256"] != results[0]["result"]["verify"]["actual_sha256"]


# ═══════════════════════════════════════════════════════════════════════════
#  Test 3: submit-result 接口 — instance_id + run_mode 验证
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_submit_result_with_source_instance_id(client, monkeypatch):
    """验证 POST /api/executions/submit-result 正确记录 source_instance_id
    和 run_mode=scheduled_real（triggered_by=node_scheduler）。"""

    monkeypatch.setattr("app.config.settings.BROWSER_REMOTE_TOKEN", "test-token")

    # 准备 Skill 数据
    async with db_mod.async_session_factory() as session:
        session.add(_make_skill("test-submit", trigger_type="cron", trigger_expression="0 9 * * *"))
        instance = _make_instance("node-1")
        instance.agent_purpose = "skill_runtime"
        instance.bridge_gateway_kind = "openclaw"
        instance.bridge_capabilities_json = json.dumps({"ops": ["run_skill_script"]})
        session.add(instance)
        await session.commit()

    # 提交结果
    resp = await client.post(
        "/api/executions/submit-result",
        params={"token": "test-token"},
        json={
            "skill_id": "test-submit",
            "output": {"conclusion": "绿灯", "reports": []},
            "params": {"date": "2026-04-21"},
            "triggered_by": "node_scheduler",
            "instance_id": "node-1",
            "remote_run_id": "node-xxx-123",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["skill_id"] == "test-submit"
    assert data["run_mode"] == "scheduled_real"
    assert data["source"]["source_instance_id"] == "node-1"
    assert data["source"]["remote_run_id"] == "node-xxx-123"

    # 验证 DB 记录
    from sqlalchemy import select

    run_id = data["run_id"]
    async with db_mod.async_session_factory() as session:
        run = (await session.execute(
            select(ExecutionRun).where(ExecutionRun.id == run_id)
        )).scalar_one()
        artifacts = (
            await session.execute(
                select(ExecutionArtifact).where(ExecutionArtifact.run_id == run_id).order_by(ExecutionArtifact.kind)
            )
        ).scalars().all()
        learning_events = (
            await session.execute(
                select(LearningEvent)
                .where(LearningEvent.source_type == "execution_artifact")
                .where(LearningEvent.run_id == run_id)
                .order_by(LearningEvent.source_id)
            )
        ).scalars().all()
        learning_artifacts = (
            await session.execute(
                select(LearningArtifact)
                .where(LearningArtifact.run_id == run_id)
                .where(LearningArtifact.artifact_kind == "execution_data_artifact")
            )
        ).scalars().all()
        flow_edges = (
            await session.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.run_id == run_id)
                .where(LearningFlowEdge.relation.in_(["produced_artifact", "captured_data", "controlled_run"]))
            )
        ).scalars().all()
        run_event = (
            await session.execute(
                select(LearningEvent)
                .where(LearningEvent.source_type == "execution_run")
                .where(LearningEvent.source_id == run_id)
            )
        ).scalar_one()
    assert run.source_instance_id == "node-1"
    assert run.run_mode == "scheduled_real"
    assert run.trigger_type == "node_scheduler"
    assert {item.kind for item in artifacts} == {"raw-input", "raw-output"}
    assert {event.source_id for event in learning_events} == {str(item.id) for item in artifacts}
    assert len(learning_artifacts) == 2
    assert all(item.content_json["source_type"] == "execution_artifact" for item in learning_artifacts)
    assert all("绿灯" not in str(item.content_json) for item in learning_artifacts)
    assert {edge.relation for edge in flow_edges} == {"produced_artifact", "captured_data", "controlled_run"}
    controlled_edge = next(edge for edge in flow_edges if edge.relation == "controlled_run")
    assert controlled_edge.from_type == "agent"
    assert controlled_edge.from_id == "node-1"
    assert controlled_edge.to_type == "run"
    assert controlled_edge.to_id == run_id
    assert controlled_edge.metadata_json["contract"]["complete"] is True
    assert controlled_edge.metadata_json["contract"]["input_channels"] == [
        "node_schedule_snapshot",
        "skill_run_request",
    ]
    assert controlled_edge.metadata_json["contract"]["control_ops"] == [
        "run_agent_skill",
        "run_skill_script",
    ]
    assert controlled_edge.metadata_json["contract"]["required_ops_any"] == [
        "run_agent_skill",
        "run_skill_script",
    ]
    assert controlled_edge.metadata_json["contract"]["available_control_ops"] == ["run_skill_script"]
    assert controlled_edge.metadata_json["contract"]["missing_ops"] == []
    assert controlled_edge.metadata_json["contract"]["output_channels"] == [
        "decision_log",
        "execution_artifact",
        "execution_run",
    ]
    assert controlled_edge.metadata_json["contract"]["lifecycle_ready"] is True
    assert run_event.metadata_json["runtime_agent"]["agent_id"] == "node-1"
    assert run_event.metadata_json["runtime_agent"]["contract"]["flow_traceable"] is True
    assert run_event.metadata_json["runtime_agent"]["contract"]["available_control_ops"] == ["run_skill_script"]


@pytest.mark.asyncio
async def test_submit_result_running_then_completed_updates_same_node_run(client, monkeypatch):
    """节点开始时先提交 running，完成时用同一 remote_run_id 更新为 completed。"""

    monkeypatch.setattr("app.config.settings.BROWSER_REMOTE_TOKEN", "test-token")

    async with db_mod.async_session_factory() as session:
        session.add(_make_skill("test-submit-lifecycle", trigger_type="cron", trigger_expression="50 14 * * *"))
        await session.commit()

    running = await client.post(
        "/api/executions/submit-result",
        params={"token": "test-token"},
        json={
            "skill_id": "test-submit-lifecycle",
            "output": {"status": "running"},
            "triggered_by": "node_scheduler",
            "instance_id": "node-1",
            "remote_run_id": "node-lifecycle-001",
            "idempotency_key": "node-1:node-lifecycle-001",
            "status": "running",
            "started_at": "2026-05-07T14:50:00+08:00",
        },
    )

    assert running.status_code == 200
    running_data = running.json()
    assert running_data["status"] == "running"
    run_id = running_data["run_id"]
    token_claims = verify_run_token(
        running_data["run_token"],
        skill_id="test-submit-lifecycle",
        run_id=run_id,
        instance_id="node-1",
    )
    assert token_claims["department"] == "EC"
    assert token_claims["skill_department"] == "EC"
    assert token_claims["skill_git_commit_full"] is None

    completed = await client.post(
        "/api/executions/submit-result",
        params={"token": "test-token"},
        json={
            "skill_id": "test-submit-lifecycle",
            "output": {"summary": "完成", "reports": []},
            "triggered_by": "node_scheduler",
            "instance_id": "node-1",
            "remote_run_id": "node-lifecycle-001",
            "idempotency_key": "node-1:node-lifecycle-001",
            "status": "completed",
            "started_at": "2026-05-07T14:50:00+08:00",
            "completed_at": "2026-05-07T15:21:44+08:00",
        },
    )

    assert completed.status_code == 200
    completed_data = completed.json()
    assert completed_data["run_id"] == run_id
    assert completed_data["status"] == "completed"

    from sqlalchemy import func, select

    async with db_mod.async_session_factory() as session:
        run_count = await session.scalar(
            select(func.count(ExecutionRun.id)).where(ExecutionRun.skill_id == "test-submit-lifecycle")
        )
        log_count = await session.scalar(
            select(func.count(DecisionLog.id)).where(DecisionLog.skill_id == "test-submit-lifecycle")
        )
        run = await session.get(ExecutionRun, run_id)
        step = (await session.execute(
            select(ExecutionStep).where(ExecutionStep.run_id == run_id)
        )).scalar_one()

    assert run_count == 1
    assert log_count == 1
    assert run.status == "completed"
    assert run.started_at == datetime(2026, 5, 7, 14, 50)
    assert run.completed_at == datetime(2026, 5, 7, 15, 21, 44)
    assert run.metadata_json["remote_run_id"] == "node-lifecycle-001"
    assert step.status == "completed"
    assert step.duration_ms == 1904000


@pytest.mark.asyncio
async def test_submit_result_accepts_node_submit_token_without_browser_token(client, monkeypatch):
    """节点定时 running 上报不应依赖全局 BROWSER_REMOTE_TOKEN。"""

    monkeypatch.setattr("app.config.settings.BROWSER_REMOTE_TOKEN", "")

    async with db_mod.async_session_factory() as session:
        session.add(_make_skill("test-node-token", trigger_type="cron", trigger_expression="50 14 * * *"))
        await session.commit()

    from app.execution.router import issue_node_submit_token

    resp = await client.post(
        "/api/executions/submit-result",
        params={"token": issue_node_submit_token("node-1")},
        headers={"X-Forwarded-For": "192.0.2.13"},
        json={
            "skill_id": "test-node-token",
            "output": {"status": "running"},
            "triggered_by": "node_scheduler",
            "instance_id": "node-1",
            "remote_run_id": "node-token-001",
            "idempotency_key": "node-1:node-token-001",
            "status": "running",
            "started_at": "2026-05-07T14:50:00+08:00",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    verify_run_token(
        data["run_token"],
        skill_id="test-node-token",
        run_id=data["run_id"],
        instance_id="node-1",
    )


@pytest.mark.asyncio
async def test_submit_result_rejects_node_submit_token_instance_mismatch(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.BROWSER_REMOTE_TOKEN", "")

    async with db_mod.async_session_factory() as session:
        session.add(_make_skill("test-node-token-mismatch", trigger_type="cron", trigger_expression="50 14 * * *"))
        await session.commit()

    from app.execution.router import issue_node_submit_token

    resp = await client.post(
        "/api/executions/submit-result",
        params={"token": issue_node_submit_token("node-a")},
        headers={"X-Forwarded-For": "192.0.2.13"},
        json={
            "skill_id": "test-node-token-mismatch",
            "output": {"status": "running"},
            "triggered_by": "node_scheduler",
            "instance_id": "node-b",
            "remote_run_id": "node-token-002",
            "status": "running",
        },
    )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_submit_result_error_marks_failed_without_todo(client, monkeypatch):
    """远端提交 traceback/timeout 时应标记 failed，且不能生成审批待办。"""

    monkeypatch.setattr("app.config.settings.BROWSER_REMOTE_TOKEN", "test-token")

    from app.auth.models import User
    from app.todos.models import DecisionRequest
    from sqlalchemy import func, select

    async with db_mod.async_session_factory() as session:
        skill = _make_skill("test-submit-error", trigger_type="cron", trigger_expression="50 7 * * *")
        skill.approval_level = 1
        skill.owner = "admin"
        session.add(skill)
        session.add(User(id="admin", username="admin", name="管理员", role="admin", is_active=True))
        await session.commit()

    resp = await client.post(
        "/api/executions/submit-result",
        params={"token": "test-token"},
        json={
            "skill_id": "test-submit-error",
            "output": {
                "error": "runtime_execution_failed",
                "message": "script timeout after 600s",
            },
            "triggered_by": "node_scheduler",
            "instance_id": "node-1",
            "remote_run_id": "node-error-001",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    assert data["todo_created"] is False
    assert data["todo_count"] == 0
    assert data["failure"]["type"] == "timeout"

    async with db_mod.async_session_factory() as session:
        run = (await session.execute(
            select(ExecutionRun).where(ExecutionRun.id == data["run_id"])
        )).scalar_one()
        assert run.status == "failed"
        assert run.completed_steps == 0
        assert run.summary == "script timeout after 600s"
        step = (await session.execute(
            select(ExecutionStep).where(ExecutionStep.run_id == data["run_id"])
        )).scalar_one()
        assert step.status == "failed"
        assert step.error_message == "script timeout after 600s"
        request_count = await session.scalar(
            select(func.count(DecisionRequest.id)).where(DecisionRequest.run_id == data["run_id"])
        )
        assert request_count == 0


# ═══════════════════════════════════════════════════════════════════════════
#  Test 4: normalize_run_mode 单元测试
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_normalize_run_mode_node_scheduler():
    """验证 normalize_run_mode 正确处理 node_scheduler 触发类型。"""

    # node_scheduler → scheduled_real
    assert normalize_run_mode(triggered_by="node_scheduler") == RUN_MODE_SCHEDULED_REAL

    # node_scheduler:fallback → scheduled_real
    assert normalize_run_mode(triggered_by="node_scheduler:fallback") == RUN_MODE_SCHEDULED_REAL

    # 普通 scheduler 也是 scheduled_real
    assert normalize_run_mode(triggered_by="scheduler") == RUN_MODE_SCHEDULED_REAL
    assert normalize_run_mode(triggered_by="scheduler:cron") == RUN_MODE_SCHEDULED_REAL

    # 手动触发 → manual_real
    assert normalize_run_mode(triggered_by="manual:admin") == RUN_MODE_MANUAL_REAL

    # 直接传 run_mode 优先
    assert normalize_run_mode(run_mode="scheduled_real") == RUN_MODE_SCHEDULED_REAL
    assert normalize_run_mode(
        run_mode="scheduled_real",
        triggered_by="manual:admin",
    ) == RUN_MODE_SCHEDULED_REAL


# ═══════════════════════════════════════════════════════════════════════════
#  Test 5: _execute_skill_job 节点已上报时跳过集中执行
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_execute_skill_job_skips_when_node_reported(client, monkeypatch):
    """当节点已上报完成结果时，集中调度器应跳过执行。"""

    # 准备 Skill + 一条 node_scheduler 的完成记录
    from uuid import uuid4
    from app.common.time_utils import now_bjt

    run_id = f"node-{uuid4().hex[:8]}"
    async with db_mod.async_session_factory() as session:
        session.add(_make_skill("test-skip"))
        session.add(ExecutionRun(
            id=run_id,
            skill_id="test-skip",
            trigger_type="node_scheduler",
            run_mode="scheduled_real",
            status="completed",
            started_at=now_bjt() - timedelta(minutes=2),
            completed_at=now_bjt() - timedelta(minutes=1),
        ))
        await session.commit()

    # mock execute_skill 以记录调用
    execute_calls: list[str] = []

    async def fake_execute_skill(_self, skill_id, **kwargs):
        execute_calls.append(skill_id)
        return {"run_id": "fake", "output": {}}

    monkeypatch.setattr(
        "app.execution.execution_service.ExecutionService.execute_skill",
        fake_execute_skill,
    )

    # mock pg_advisory_lock 使其始终获取成功
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_lock(key):
        yield True

    monkeypatch.setattr(
        "app.execution.scheduler._pg_advisory_lock",
        fake_lock,
    )

    from app.execution.scheduler import _execute_skill_job

    await _execute_skill_job("test-skip")

    # 断言：execute_skill 未被调用（节点已上报）
    assert len(execute_calls) == 0


@pytest.mark.asyncio
async def test_execute_skill_job_defers_when_node_schedule_online(client, monkeypatch):
    """节点定时配置已下发且 bridge 在线时，中心 cron 不抢跑生产任务。"""
    from app.common.time_utils import now_bjt

    async with db_mod.async_session_factory() as session:
        inst = _make_instance("node-online")
        inst.bridge_connected_at = now_bjt()
        inst.last_heartbeat = now_bjt()
        session.add(_make_skill("test-node-primary"))
        session.add(inst)
        session.add(NodeScheduleConfig(
            instance_id="node-online",
            skill_id="test-node-primary",
            cron_expression="0 9 * * *",
            config_version=1,
            pushed_at=datetime.utcnow(),
            ack_ok=True,
            runtime_backend="openclaw_agent",
            runtime_config={"backend": "openclaw_agent", "fallback": "bridge_script"},
        ))
        await session.commit()

    execute_calls: list[str] = []

    async def fake_execute_skill(_self, skill_id, **kwargs):
        execute_calls.append(skill_id)
        return {"run_id": "fake", "output": {}}

    monkeypatch.setattr(
        "app.execution.execution_service.ExecutionService.execute_skill",
        fake_execute_skill,
    )

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_lock(key):
        yield True

    monkeypatch.setattr("app.execution.scheduler._pg_advisory_lock", fake_lock)
    monkeypatch.setattr(
        "app.execution.scheduler._bridge_runtime_online",
        lambda inst, **kwargs: inst.id == "node-online",
    )

    from app.execution.scheduler import _execute_skill_job

    await _execute_skill_job("test-node-primary")

    assert execute_calls == []


@pytest.mark.asyncio
async def test_execute_skill_job_skips_conflicted_node_schedule(client, monkeypatch):
    """业务节点误挂平台主机时，中心调度也不能接手在平台机跑。"""
    from app.common.time_utils import now_bjt

    async with db_mod.async_session_factory() as session:
        platform = _make_instance("platform", department=None)
        platform.is_platform_default = True
        platform.bridge_fingerprint = "platform-host"
        node = _make_instance("biz-node")
        node.bridge_fingerprint = "platform-host"
        node.bridge_connected_at = now_bjt()
        node.last_heartbeat = now_bjt()
        session.add(_make_skill("test-placement-conflict"))
        session.add(platform)
        session.add(node)
        session.add(NodeScheduleConfig(
            instance_id="biz-node",
            skill_id="test-placement-conflict",
            cron_expression="0 9 * * *",
            config_version=1,
            pushed_at=datetime.utcnow(),
            ack_ok=True,
            runtime_backend="bridge_script",
            runtime_config={"backend": "bridge_script", "fallback": "bridge_script"},
        ))
        await session.commit()

    execute_calls: list[str] = []

    async def fake_execute_skill(_self, skill_id, **kwargs):
        execute_calls.append(skill_id)
        return {"run_id": "fake", "output": {}}

    monkeypatch.setattr(
        "app.execution.execution_service.ExecutionService.execute_skill",
        fake_execute_skill,
    )

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_lock(key):
        yield True

    monkeypatch.setattr("app.execution.scheduler._pg_advisory_lock", fake_lock)
    monkeypatch.setattr(
        "app.execution.scheduler._bridge_runtime_online",
        lambda inst, **kwargs: inst.id == "biz-node",
    )

    from app.execution.scheduler import _execute_skill_job

    await _execute_skill_job("test-placement-conflict")

    assert execute_calls == []


@pytest.mark.asyncio
async def test_execute_skill_job_does_not_treat_stale_db_online_as_live_node(client, monkeypatch):
    """DB 残留在线但内存注册表无连接时，中心 cron 不能抢跑节点任务。"""
    from app.common.time_utils import now_bjt

    async with db_mod.async_session_factory() as session:
        inst = _make_instance("node-stale-online")
        inst.bridge_connected_at = now_bjt()
        inst.last_heartbeat = now_bjt()
        session.add(_make_skill("test-stale-online"))
        session.add(inst)
        session.add(NodeScheduleConfig(
            instance_id="node-stale-online",
            skill_id="test-stale-online",
            cron_expression="0 9 * * *",
            config_version=1,
            pushed_at=now_bjt(),
            ack_ok=True,
            runtime_backend="bridge_script",
            runtime_config={"backend": "bridge_script"},
        ))
        await session.commit()

    execute_calls: list[str] = []

    async def fake_execute_skill(_self, skill_id, **kwargs):
        execute_calls.append(skill_id)
        return {"run_id": "fake", "output": {}}

    monkeypatch.setattr(
        "app.execution.execution_service.ExecutionService.execute_skill",
        fake_execute_skill,
    )

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_lock(key):
        yield True

    monkeypatch.setattr("app.execution.scheduler._pg_advisory_lock", fake_lock)
    monkeypatch.setattr("app.execution.scheduler._bridge_runtime_online", lambda inst, **kwargs: False)

    from app.execution.scheduler import _execute_skill_job

    await _execute_skill_job("test-stale-online")

    assert execute_calls == []


def test_watchdog_window_uses_scheduler_timezone_for_db_bounds(monkeypatch):
    """watchdog 使用调度时区计算 cron，并用 BJT naive 与 DB 时间比较。"""
    from zoneinfo import ZoneInfo

    monkeypatch.setattr("app.execution.scheduler.settings.SCHEDULER_TIMEZONE", "Asia/Shanghai")

    from app.execution.scheduler import _watchdog_expected_window

    window = _watchdog_expected_window(
        "55 8 * * *",
        now=datetime(2026, 4, 24, 9, 6, tzinfo=ZoneInfo("Asia/Shanghai")),
        tolerance=timedelta(minutes=5),
    )

    assert window is not None
    expected, started_from, started_to = window
    assert expected.isoformat() == "2026-04-24T08:55:00+08:00"
    assert started_from == datetime(2026, 4, 24, 8, 50)
    assert started_from.tzinfo is None
    assert started_to == datetime(2026, 4, 24, 9, 6)
    assert started_to.tzinfo is None


@pytest.mark.asyncio
async def test_watchdog_remote_retriggers_online_node_schedule(client, monkeypatch):
    """节点漏跑后，watchdog 优先通过在线 bridge 远程补触发同一 runtime。"""
    from zoneinfo import ZoneInfo
    from app.common.time_utils import now_bjt

    async with db_mod.async_session_factory() as session:
        inst = _make_instance("node-agent")
        inst.bridge_connected_at = now_bjt()
        inst.last_heartbeat = now_bjt()
        session.add(_make_skill("test-watchdog-agent", trigger_expression="55 8 * * *"))
        session.add(inst)
        session.add(NodeScheduleConfig(
            instance_id="node-agent",
            skill_id="test-watchdog-agent",
            cron_expression="55 8 * * *",
            config_version=7,
            pushed_at=datetime(2026, 4, 24, 8, 0),
            ack_ok=True,
            runtime_backend="openclaw_agent",
            runtime_config={
                "backend": "openclaw_agent",
                "fallback": "bridge_script",
                "script_entry": "scripts/main.py",
                "timeout": 900,
            },
        ))
        await session.commit()

    expected = datetime(2026, 4, 24, 8, 55, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(
        "app.execution.scheduler._watchdog_expected_window",
        lambda *args, **kwargs: (
            expected,
            datetime(2026, 4, 24, 0, 50),
            datetime(2026, 4, 24, 1, 6),
        ),
    )

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_lock(key):
        yield True

    monkeypatch.setattr("app.execution.scheduler._pg_advisory_lock", fake_lock)
    monkeypatch.setattr(
        "app.execution.scheduler._bridge_runtime_online",
        lambda inst, **kwargs: inst.id == "node-agent",
    )

    calls: list[dict] = []

    async def fake_execute_skill(_self, **kwargs):
        calls.append(kwargs)
        return {"run_id": "fallback-run", "output": {}}

    async def fail_center_execute(skill_id):
        raise AssertionError("center fallback should not run while node is online")

    monkeypatch.setattr(
        "app.execution.execution_service.ExecutionService.execute_skill",
        fake_execute_skill,
    )
    monkeypatch.setattr("app.execution.scheduler._execute_skill_job", fail_center_execute)

    from app.execution.scheduler import _job_schedule_watchdog

    await _job_schedule_watchdog()

    assert len(calls) == 1
    assert calls[0]["skill_id"] == "test-watchdog-agent"
    assert calls[0]["triggered_by"] == "scheduler:fallback"
    assert calls[0]["params"]["_execution_backend"] == "openclaw_agent"
    assert calls[0]["params"]["_aiclaw_instance_id"] == "node-agent"
    assert calls[0]["params"]["_execution"]["scheduled_at"] == expected.isoformat()


@pytest.mark.asyncio
async def test_watchdog_skips_expected_time_before_schedule_push(client, monkeypatch):
    """修改 cron 后新增的已过时间点，不应被 watchdog 当作漏跑补执行。"""
    from zoneinfo import ZoneInfo

    async with db_mod.async_session_factory() as session:
        inst = _make_instance("node-after-change")
        inst.bridge_connected_at = datetime(2026, 5, 13, 13, 30)
        session.add(_make_skill("test-watchdog-after-change", trigger_expression="50 8,12 * * *"))
        session.add(inst)
        session.add(NodeScheduleConfig(
            instance_id="node-after-change",
            skill_id="test-watchdog-after-change",
            cron_expression="50 8,12 * * *",
            config_version=9,
            pushed_at=datetime(2026, 5, 13, 13, 27),
            ack_ok=True,
            runtime_backend="bridge_script",
            runtime_config={"backend": "bridge_script", "script_entry": "scripts/main.py", "timeout": 1800},
        ))
        await session.commit()

    expected = datetime(2026, 5, 13, 12, 50, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(
        "app.execution.scheduler._watchdog_expected_window",
        lambda *args, **kwargs: (
            expected,
            datetime(2026, 5, 13, 12, 45),
            datetime(2026, 5, 13, 13, 30),
        ),
    )

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_lock(key):
        yield True

    async def fail_execute(*args, **kwargs):
        raise AssertionError("watchdog must not backfill fire times before schedule push")

    monkeypatch.setattr("app.execution.scheduler._pg_advisory_lock", fake_lock)
    monkeypatch.setattr("app.execution.execution_service.ExecutionService.execute_skill", fail_execute)
    monkeypatch.setattr("app.execution.scheduler._execute_skill_job", fail_execute)

    from app.execution.scheduler import _job_schedule_watchdog

    await _job_schedule_watchdog()


@pytest.mark.asyncio
async def test_watchdog_does_not_remote_retrigger_stale_db_online(client, monkeypatch):
    """DB 心跳残留但 bridge 不在注册表时，watchdog 不应发远程补触发。"""
    from zoneinfo import ZoneInfo
    from app.common.time_utils import now_bjt

    async with db_mod.async_session_factory() as session:
        inst = _make_instance("node-stale-watchdog")
        inst.bridge_connected_at = now_bjt()
        inst.last_heartbeat = now_bjt()
        session.add(_make_skill("test-watchdog-stale", trigger_expression="55 8 * * *"))
        session.add(inst)
        session.add(NodeScheduleConfig(
            instance_id="node-stale-watchdog",
            skill_id="test-watchdog-stale",
            cron_expression="55 8 * * *",
            config_version=8,
            pushed_at=datetime(2026, 4, 24, 8, 0),
            ack_ok=True,
            runtime_backend="bridge_script",
            runtime_config={"backend": "bridge_script"},
        ))
        await session.commit()

    expected = datetime(2026, 4, 24, 8, 55, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(
        "app.execution.scheduler._watchdog_expected_window",
        lambda *args, **kwargs: (
            expected,
            datetime(2026, 4, 24, 8, 50),
            datetime(2026, 4, 24, 9, 6),
        ),
    )

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_lock(key):
        yield True

    async def fail_execute(*args, **kwargs):
        raise AssertionError("watchdog must not trigger stale-db online node")

    monkeypatch.setattr("app.execution.scheduler._pg_advisory_lock", fake_lock)
    monkeypatch.setattr("app.execution.scheduler._bridge_runtime_online", lambda inst, **kwargs: False)
    monkeypatch.setattr("app.execution.execution_service.ExecutionService.execute_skill", fail_execute)
    monkeypatch.setattr("app.execution.scheduler._execute_skill_job", fail_execute)

    from app.execution.scheduler import _job_schedule_watchdog

    await _job_schedule_watchdog()


@pytest.mark.asyncio
async def test_watchdog_does_not_retrigger_while_node_run_is_running(client, monkeypatch):
    """节点长任务仍在 running 时，watchdog 不能再次触发同一 Skill。"""
    from zoneinfo import ZoneInfo

    async with db_mod.async_session_factory() as session:
        inst = _make_instance("node-running")
        inst.bridge_connected_at = datetime.utcnow()
        session.add(_make_skill("test-watchdog-running", trigger_expression="55 8 * * *"))
        session.add(inst)
        session.add(NodeScheduleConfig(
            instance_id="node-running",
            skill_id="test-watchdog-running",
            cron_expression="55 8 * * *",
            config_version=8,
            pushed_at=datetime.utcnow(),
            ack_ok=True,
            runtime_backend="bridge_script",
            runtime_config={"backend": "bridge_script", "script_entry": "scripts/main.py", "timeout": 1800},
        ))
        session.add(ExecutionRun(
            id="running-node-run",
            skill_id="test-watchdog-running",
            trigger_type="node_scheduler",
            run_mode=RUN_MODE_SCHEDULED_REAL,
            started_at=datetime.utcnow() - timedelta(minutes=4),
            status="running",
            completed_steps=0,
            metadata_json={},
            source_instance_id="node-running",
        ))
        await session.commit()

    expected = datetime(2026, 4, 24, 8, 55, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(
        "app.execution.scheduler._watchdog_expected_window",
        lambda *args, **kwargs: (
            expected,
            datetime(2026, 4, 24, 0, 50),
            datetime(2026, 4, 24, 1, 6),
        ),
    )

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_lock(key):
        yield True

    async def fail_execute(*args, **kwargs):
        raise AssertionError("watchdog must not retrigger while node run is running")

    monkeypatch.setattr("app.execution.scheduler._pg_advisory_lock", fake_lock)
    monkeypatch.setattr("app.execution.execution_service.ExecutionService.execute_skill", fail_execute)
    monkeypatch.setattr("app.execution.scheduler._execute_skill_job", fail_execute)

    from app.execution.scheduler import _job_schedule_watchdog

    await _job_schedule_watchdog()


# ═══════════════════════════════════════════════════════════════════════════
#  Test 6: _cron_match / _field_match 全面测试
# ═══════════════════════════════════════════════════════════════════════════

# bridge 脚本不在常规 Python 路径中，需要手动加入
_bridge_dir = str(Path(__file__).resolve().parent.parent / "bridge")
if _bridge_dir not in sys.path:
    sys.path.insert(0, _bridge_dir)

from skillforgebridge import _cron_expected_minutes, _cron_match, _field_match  # noqa: E402


class TestCronMatch:
    """bridge 端 cron 匹配纯函数测试。"""

    # --- _field_match 基础 ---

    def test_field_match_wildcard(self):
        """* 匹配任意值"""
        assert _field_match("*", 0, 0, 59) is True
        assert _field_match("*", 30, 0, 59) is True
        assert _field_match("*", 59, 0, 59) is True

    def test_field_match_step(self):
        """*/N 步进匹配"""
        assert _field_match("*/15", 0, 0, 59) is True
        assert _field_match("*/15", 15, 0, 59) is True
        assert _field_match("*/15", 30, 0, 59) is True
        assert _field_match("*/15", 45, 0, 59) is True
        assert _field_match("*/15", 7, 0, 59) is False
        assert _field_match("*/15", 31, 0, 59) is False

    def test_field_match_range(self):
        """N-M 范围匹配"""
        assert _field_match("1-5", 1, 0, 6) is True
        assert _field_match("1-5", 3, 0, 6) is True
        assert _field_match("1-5", 5, 0, 6) is True
        assert _field_match("1-5", 0, 0, 6) is False
        assert _field_match("1-5", 6, 0, 6) is False

    def test_field_match_range_with_step(self):
        """N-M/S 范围+步进"""
        assert _field_match("0-30/10", 0, 0, 59) is True
        assert _field_match("0-30/10", 10, 0, 59) is True
        assert _field_match("0-30/10", 20, 0, 59) is True
        assert _field_match("0-30/10", 30, 0, 59) is True
        assert _field_match("0-30/10", 15, 0, 59) is False
        assert _field_match("0-30/10", 40, 0, 59) is False

    def test_field_match_list(self):
        """N,M,O 列表匹配"""
        assert _field_match("1,15,30", 1, 0, 59) is True
        assert _field_match("1,15,30", 15, 0, 59) is True
        assert _field_match("1,15,30", 30, 0, 59) is True
        assert _field_match("1,15,30", 2, 0, 59) is False

    def test_field_match_literal(self):
        """字面值匹配"""
        assert _field_match("9", 9, 0, 23) is True
        assert _field_match("9", 10, 0, 23) is False

    # --- _cron_match 完整表达式 ---

    def test_cron_match_workday_9am(self):
        """'0 9 * * 1-5' 工作日 9:00"""
        # 2026-04-20 是周一
        monday_9am = datetime(2026, 4, 20, 9, 0)
        assert _cron_match("0 9 * * 1-5", monday_9am) is True

        # 2026-04-25 是周六
        saturday_9am = datetime(2026, 4, 25, 9, 0)
        assert _cron_match("0 9 * * 1-5", saturday_9am) is False

    def test_cron_match_every_minute(self):
        """'* * * * *' 每分钟"""
        assert _cron_match("* * * * *", datetime(2026, 1, 1, 0, 0)) is True
        assert _cron_match("* * * * *", datetime(2026, 12, 31, 23, 59)) is True

    def test_cron_match_dow_sunday_zero(self):
        """dow 0 = Sunday"""
        # 2026-04-19 是周日
        sunday = datetime(2026, 4, 19, 10, 0)
        assert _cron_match("0 10 * * 0", sunday) is True
        # 周一不匹配
        monday = datetime(2026, 4, 20, 10, 0)
        assert _cron_match("0 10 * * 0", monday) is False

    def test_cron_match_dow_sunday_seven(self):
        """dow 7 也代表 Sunday（兼容）"""
        sunday = datetime(2026, 4, 19, 10, 0)
        assert _cron_match("0 10 * * 7", sunday) is True

    def test_cron_match_invalid_fields(self):
        """字段数不是 5 返回 False"""
        assert _cron_match("0 9 * *", datetime(2026, 1, 1, 9, 0)) is False
        assert _cron_match("0 9 * * * *", datetime(2026, 1, 1, 9, 0)) is False
        assert _cron_match("", datetime(2026, 1, 1, 9, 0)) is False

    def test_cron_match_specific_month_day(self):
        """'30 14 1 * *' 每月1号 14:30"""
        assert _cron_match("30 14 1 * *", datetime(2026, 5, 1, 14, 30)) is True
        assert _cron_match("30 14 1 * *", datetime(2026, 5, 2, 14, 30)) is False
        assert _cron_match("30 14 1 * *", datetime(2026, 5, 1, 14, 31)) is False

    def test_field_match_edge_invalid_step(self):
        """步进为 0 或非数字时不匹配"""
        assert _field_match("*/0", 0, 0, 59) is False
        assert _field_match("*/abc", 0, 0, 59) is False
        assert _field_match("1-5/0", 1, 0, 6) is False

    def test_cron_expected_minutes_skips_before_config_update(self):
        """节点收到新配置前的历史分钟不能被补跑。"""
        now = datetime(2026, 5, 15, 9, 31, 12)
        updated_at = datetime(2026, 5, 15, 9, 29, 30)

        due = _cron_expected_minutes(
            "29,30,31 9 * * *",
            now,
            300,
            after=updated_at,
        )

        assert due == [
            datetime(2026, 5, 15, 9, 30),
            datetime(2026, 5, 15, 9, 31),
        ]

    def test_cron_expected_minutes_catches_missed_tick_once_windowed(self):
        """事件循环卡过当前分钟时，小窗口内的已到分钟仍会被节点端补触发。"""
        now = datetime(2026, 5, 15, 9, 35, 12)
        previous = datetime(2026, 5, 15, 9, 29, 40)

        due = _cron_expected_minutes(
            "30,34 9 * * *",
            now,
            300,
            after=previous,
        )

        assert due == [
            datetime(2026, 5, 15, 9, 30),
            datetime(2026, 5, 15, 9, 34),
        ]


# ═══════════════════════════════════════════════════════════════════════════
#  Test 7: bridge handle_bridge_op("sync_schedules") 验证
# ═══════════════════════════════════════════════════════════════════════════

from skillforgebridge import (  # noqa: E402
    handle_bridge_op,
    _load_schedules,
    _preserved_update_header,
    _save_schedules,
    SCHEDULES_PATH,
)


@pytest.mark.asyncio
async def test_bridge_sync_schedules_op(tmp_path, monkeypatch):
    """验证 bridge 端 handle_bridge_op sync_schedules 正确写入 schedules.json。"""

    # 将 SCHEDULES_PATH / STATE_DIR 重定向到 tmp_path，避免污染真实目录
    fake_schedules_path = tmp_path / "schedules.json"
    monkeypatch.setattr("skillforgebridge.SCHEDULES_PATH", fake_schedules_path)
    monkeypatch.setattr("skillforgebridge.STATE_DIR", tmp_path)

    schedules_payload = [
        {
            "skill_id": "EC-投放-01",
            "cron": "0 9 * * 1-5",
            "timezone": "Asia/Shanghai",
            "enabled": True,
        },
        {
            "skill_id": "EC-ROI-02",
            "cron": "30 10 * * *",
            "timezone": "Asia/Shanghai",
            "enabled": True,
        },
    ]

    ok, result, error = await handle_bridge_op(
        "sync_schedules",
        {
            "schedules": schedules_payload,
            "submit_token": "test-token",
            "submit_url": "http://localhost:8000/api/executions/submit-result",
            "config_version": 42,
        },
    )

    # 断言：操作成功
    assert ok is True
    assert error is None
    assert result["accepted"] == 2
    assert result["config_version"] == 42

    # 断言：schedules.json 内容正确
    assert fake_schedules_path.exists()
    data = json.loads(fake_schedules_path.read_text(encoding="utf-8"))
    assert len(data["schedules"]) == 2
    assert data["submit_token"] == "test-token"
    assert data["config_version"] == 42
    assert "updated_at" in data

    # 验证 _load_schedules 能正确读回
    monkeypatch.setattr("skillforgebridge.SCHEDULES_PATH", fake_schedules_path)
    loaded = _load_schedules()
    assert loaded["config_version"] == 42
    assert len(loaded["schedules"]) == 2


@pytest.mark.asyncio
async def test_bridge_sync_schedules_preserves_updated_at_when_snapshot_unchanged(tmp_path, monkeypatch):
    """Bridge 重连收到相同 schedule 快照时，应保留原更新时间。"""

    fake_schedules_path = tmp_path / "schedules.json"
    monkeypatch.setattr("skillforgebridge.SCHEDULES_PATH", fake_schedules_path)
    monkeypatch.setattr("skillforgebridge.STATE_DIR", tmp_path)

    payload = {
        "schedules": [
            {
                "skill_id": "EC-ROI-02",
                "cron": "30 10 * * *",
                "timezone": "Asia/Shanghai",
                "enabled": True,
            },
        ],
        "submit_token": "test-token",
        "submit_url": "http://localhost:8000/api/executions/submit-result",
        "config_version": 42,
    }
    ok, result, error = await handle_bridge_op("sync_schedules", payload)
    assert ok is True
    assert error is None
    assert result["config_version"] == 42
    first = json.loads(fake_schedules_path.read_text(encoding="utf-8"))

    second_payload = dict(payload)
    second_payload["config_version"] = 99
    ok, result, error = await handle_bridge_op("sync_schedules", second_payload)
    assert ok is True
    assert error is None
    assert result["config_version"] == 42
    second = json.loads(fake_schedules_path.read_text(encoding="utf-8"))

    assert second["config_version"] == first["config_version"]
    assert second["updated_at"] == first["updated_at"]


@pytest.mark.asyncio
async def test_bridge_sync_schedules_op_empty(tmp_path, monkeypatch):
    """验证空 schedules 列表也能正常处理。"""

    fake_schedules_path = tmp_path / "schedules.json"
    monkeypatch.setattr("skillforgebridge.SCHEDULES_PATH", fake_schedules_path)
    monkeypatch.setattr("skillforgebridge.STATE_DIR", tmp_path)

    ok, result, error = await handle_bridge_op(
        "sync_schedules",
        {
            "schedules": [],
            "submit_token": "",
            "submit_url": "",
            "config_version": 1,
        },
    )

    assert ok is True
    assert result["accepted"] == 0
    assert result["config_version"] == 1


@pytest.mark.asyncio
async def test_bridge_scheduler_executes_main_and_submits_stdout(tmp_path, monkeypatch):
    """节点定时执行应直接跑 scripts/main.py，并把 stdout JSON 回传 submit-result。"""
    import skillforgebridge

    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "EC-node-main"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "main.py").write_text(
        "import json, sys\n"
        "import os\n"
        "payload = json.load(sys.stdin)\n"
        "print(json.dumps({\n"
        "  'reports': [{'title': '本地运行报告', 'summary': 'stdout ok'}],\n"
        "  'todos': [{'kind': 'review', 'title': '平台待办'}],\n"
        "  'payload': payload,\n"
        "  'run_token': os.environ.get('SKILLFORGE_RUN_TOKEN'),\n"
        "  'run_id': os.environ.get('SKILLFORGE_RUN_ID'),\n"
        "  'remote_run_id': os.environ.get('SKILLFORGE_REMOTE_RUN_ID'),\n"
        "  'run_mode': os.environ.get('SKILLFORGE_RUN_MODE'),\n"
        "  'skill_git_commit_full': os.environ.get('SKILLFORGE_SKILL_GIT_COMMIT_FULL'),\n"
        "}, ensure_ascii=False))\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        skillforgebridge,
        "discover_local_capabilities",
        lambda: {
            "skills_dirs": [str(skills_dir)],
            "skills_dir_default": str(skills_dir),
            "gateway_kind": "openclaw",
        },
    )
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-test-1")

    async def fail_connect_local():
        raise AssertionError("scheduler must not use chat.send/connect_local")

    monkeypatch.setattr(skillforgebridge, "connect_local", fail_connect_local)

    submissions: list[dict] = []

    def fake_post_submit_result(submit_url, submit_token, skill_id, output, run_id, **kwargs):
        submissions.append({
            "submit_url": submit_url,
            "submit_token": submit_token,
            "skill_id": skill_id,
            "output": output,
            "run_id": run_id,
            "kwargs": kwargs,
        })
        if kwargs.get("status") == "running":
            return {"run_token": "run-token-1", "run_id": "rm-platform-1"}
        return None

    monkeypatch.setattr(skillforgebridge, "_post_submit_result", fake_post_submit_result)

    await skillforgebridge._execute_scheduled_skill(
        "EC-node-main",
        "http://skillforge.test/api/executions/submit-result",
        "token-1",
        {"skill_git_commit_full": "abc1234"},
    )

    assert len(submissions) == 2
    assert submissions[0]["kwargs"]["status"] == "running"
    submission = submissions[1]
    assert submission["kwargs"]["status"] == "completed"
    assert submission["submit_url"] == "http://skillforge.test/api/executions/submit-result"
    assert submission["submit_token"] == "token-1"
    assert submission["skill_id"] == "EC-node-main"
    assert submission["run_id"].startswith("node-")
    output = submission["output"]
    assert output["reports"][0]["title"] == "本地运行报告"
    assert output["todos"][0]["title"] == "平台待办"
    assert output["payload"] == {}
    assert output["run_token"] == "run-token-1"
    assert output["run_id"] == "rm-platform-1"
    assert output["remote_run_id"] == submission["run_id"]
    assert output["run_mode"] == "scheduled_real"
    assert output["skill_git_commit_full"] == "abc1234"
    assert output["_skillforge_meta"]["execution_backend"] == "bridge_script"
    assert output["_skillforge_meta"]["instance_id"] == "node-test-1"
    assert output["_skillforge_meta"]["skillforge_run_id"] == "rm-platform-1"
    assert output["_skillforge_meta"]["remote_run_id"] == submission["run_id"]
    assert output["_skillforge_meta"]["script"] == "scripts/main.py"


@pytest.mark.asyncio
async def test_bridge_scheduler_marks_run_failed_when_final_submit_fails(tmp_path, monkeypatch):
    """本地执行已结束但最终结果提交失败时，应追加轻量 failed 状态避免平台长期 running。"""
    import skillforgebridge

    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "EC-node-submit-fail"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "main.py").write_text(
        "import json\n"
        "print(json.dumps({\n"
        "  'reports': [{'title': '完整报告', 'summary': 'ok'}],\n"
        "  'payload': 'x' * 1024,\n"
        "}, ensure_ascii=False))\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        skillforgebridge,
        "discover_local_capabilities",
        lambda: {
            "skills_dirs": [str(skills_dir)],
            "skills_dir_default": str(skills_dir),
            "gateway_kind": "openclaw",
        },
    )
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-submit-fail")

    submissions: list[dict] = []

    def fake_post_submit_result(submit_url, submit_token, skill_id, output, run_id, **kwargs):
        submissions.append({
            "output": output,
            "run_id": run_id,
            "kwargs": kwargs,
        })
        if kwargs.get("status") == "running":
            return {"run_token": "run-token-fail", "run_id": "rm-submit-fail"}
        if kwargs.get("status") == "completed":
            return {
                "submitted": False,
                "error": "Remote end closed connection without response",
            }
        return None

    monkeypatch.setattr(skillforgebridge, "_post_submit_result", fake_post_submit_result)

    await skillforgebridge._execute_scheduled_skill(
        "EC-node-submit-fail",
        "http://skillforge.test/api/executions/submit-result",
        "token-1",
        {},
    )

    assert [item["kwargs"]["status"] for item in submissions] == ["running", "completed", "failed"]
    assert submissions[1]["output"]["reports"][0]["title"] == "完整报告"
    failure = submissions[2]["output"]
    assert submissions[2]["run_id"] == submissions[1]["run_id"]
    assert submissions[2]["kwargs"]["completed_at"] == submissions[1]["kwargs"]["completed_at"]
    assert failure["error"] == "result_submit_failed"
    assert failure["submit_error"] == "Remote end closed connection without response"
    assert failure["_skillforge_meta"]["skillforge_run_id"] == "rm-submit-fail"
    assert failure["_skillforge_meta"]["original_status"] == "completed"
    assert failure["_skillforge_meta"]["result_submit_failed"] is True
    assert failure["_skillforge_meta"]["original_output_omitted"] is True


@pytest.mark.asyncio
async def test_bridge_scheduler_passes_model_context_to_script_payload_and_env(tmp_path, monkeypatch):
    """节点调度执行时应把 schedule.model_context 传给脚本输入和环境变量。"""
    import skillforgebridge

    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "EC-node-model"
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "main.py").write_text(
        "import json, os, sys\n"
        "payload = json.load(sys.stdin)\n"
        "print(json.dumps({\n"
        "  'payload_model_deployment_id': payload['model_context']['model_deployment_id'],\n"
        "  'env_model_deployment_id': os.environ.get('SKILLFORGE_MODEL_DEPLOYMENT_ID'),\n"
        "  'env_model_context': json.loads(os.environ.get('SKILLFORGE_MODEL_CONTEXT_JSON') or '{}'),\n"
        "}, ensure_ascii=False))\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        skillforgebridge,
        "discover_local_capabilities",
        lambda: {
            "skills_dirs": [str(skills_dir)],
            "skills_dir_default": str(skills_dir),
            "gateway_kind": "openclaw",
        },
    )
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-test-model")

    submissions: list[dict] = []

    def fake_post_submit_result(submit_url, submit_token, skill_id, output, run_id, **kwargs):
        submissions.append({"output": output, "run_id": run_id, "kwargs": kwargs})
        if kwargs.get("status") == "running":
            return {"run_token": "run-token-model", "run_id": "rm-model-1"}
        return None

    monkeypatch.setattr(skillforgebridge, "_post_submit_result", fake_post_submit_result)

    model_context = {
        "model_deployment_id": "deploy-runtime-model-1",
        "model_family": "lora",
        "artifact_sha256": "a" * 64,
        "active_model_deployment": {
            "model_deployment_id": "deploy-runtime-model-1",
            "model_family": "lora",
            "deployment_status": "active",
            "rollout_percent": 100,
            "artifact_id": "artifact-runtime-model-1",
            "artifact_sha256": "a" * 64,
            "target_skill_ids": ["EC-node-model"],
        },
        "control": {"agent_contract": "skill_runtime_model_context.v1"},
    }

    await skillforgebridge._execute_scheduled_skill(
        "EC-node-model",
        "http://skillforge.test/api/executions/submit-result",
        "token-1",
        {"model_context": model_context},
    )

    assert len(submissions) == 2
    output = submissions[1]["output"]
    assert output["payload_model_deployment_id"] == "deploy-runtime-model-1"
    assert output["env_model_deployment_id"] == "deploy-runtime-model-1"
    assert output["env_model_context"]["model_deployment_id"] == "deploy-runtime-model-1"
    assert output["_skillforge_meta"]["model_context"]["model_deployment_id"] == "deploy-runtime-model-1"
    assert output["_skillforge_meta"]["active_model_deployment"]["model_deployment_id"] == "deploy-runtime-model-1"
    assert submissions[1]["kwargs"]["params"]["model_context"]["model_deployment_id"] == "deploy-runtime-model-1"


@pytest.mark.asyncio
async def test_bridge_scheduler_uses_openclaw_agent_runtime(monkeypatch):
    """runtime=openclaw_agent 时，节点调度器应调用 run_agent_skill 并回传 Agent 结果。"""
    import skillforgebridge

    calls: list[dict] = []

    async def fake_handle_bridge_op(op, payload):
        calls.append({"op": op, "payload": payload})
        assert op == "run_agent_skill"
        return True, {
            "success": True,
            "duration_ms": 23,
            "output": {"reports": [{"title": "agent ok"}], "todos": []},
        }, None

    submissions: list[dict] = []

    def fake_post_submit_result(submit_url, submit_token, skill_id, output, run_id, **kwargs):
        submissions.append({
            "skill_id": skill_id,
            "output": output,
            "run_id": run_id,
            "kwargs": kwargs,
        })
        if kwargs.get("status") == "running":
            return {"run_token": "run-token-agent", "run_id": "rm-agent-1"}
        return None

    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-agent-1")
    monkeypatch.setattr(skillforgebridge, "handle_bridge_op", fake_handle_bridge_op)
    monkeypatch.setattr(skillforgebridge, "_post_submit_result", fake_post_submit_result)

    await skillforgebridge._execute_scheduled_skill(
        "agent-skill",
        "http://skillforge.test/api/executions/submit-result",
        "token-1",
        {
            "runtime": {
                "backend": "openclaw_agent",
                "fallback": "bridge_script",
                "timeout": 900,
                "entry": "SKILL.md",
            },
            "params": {"date": "2026-04-24"},
            "model_context": {
                "model_deployment_id": "deploy-agent-model-1",
                "active_model_deployment": {
                    "model_deployment_id": "deploy-agent-model-1",
                    "model_family": "lora",
                    "deployment_status": "active",
                    "artifact_sha256": "b" * 64,
                },
                "control": {"agent_contract": "skill_runtime_model_context.v1"},
            },
            "skill_git_commit_full": "agentcommit",
        },
    )

    assert calls[0]["payload"]["runtime"]["backend"] == "openclaw_agent"
    assert calls[0]["payload"]["payload"]["date"] == "2026-04-24"
    assert calls[0]["payload"]["payload"]["model_context"]["model_deployment_id"] == "deploy-agent-model-1"
    assert calls[0]["payload"]["run_id"] == "rm-agent-1"
    assert calls[0]["payload"]["remote_run_id"] == submissions[0]["run_id"]
    assert calls[0]["payload"]["run_token"] == "run-token-agent"
    assert calls[0]["payload"]["skill_git_commit_full"] == "agentcommit"
    assert len(submissions) == 2
    assert submissions[0]["kwargs"]["status"] == "running"
    output = submissions[1]["output"]
    assert submissions[1]["kwargs"]["status"] == "completed"
    assert output["reports"][0]["title"] == "agent ok"
    assert output["_skillforge_meta"]["execution_backend"] == "openclaw_agent"
    assert output["_skillforge_meta"]["instance_id"] == "node-agent-1"
    assert output["_skillforge_meta"]["skillforge_run_id"] == "rm-agent-1"
    assert output["_skillforge_meta"]["remote_run_id"] == submissions[1]["run_id"]


@pytest.mark.asyncio
async def test_bridge_register_skill_records_local_registry(tmp_path, monkeypatch):
    import skillforgebridge

    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "agent-skill"
    state_dir = tmp_path / "state"
    skill_dir.mkdir(parents=True)
    state_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Agent Skill\n", encoding="utf-8")

    calls: list[dict] = []

    async def fake_call_local_gateway(method, params, timeout=60):
        calls.append({"method": method, "params": params, "timeout": timeout})
        return True, {"registered": True}, None

    monkeypatch.setattr(
        skillforgebridge,
        "discover_local_capabilities",
        lambda: {
            "skills_dirs": [str(skills_dir)],
            "skills_dir_default": str(skills_dir),
            "gateway_kind": "openclaw",
        },
    )
    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-agent-1")
    monkeypatch.setattr(skillforgebridge, "STATE_DIR", state_dir)
    monkeypatch.setattr(skillforgebridge, "_call_local_gateway", fake_call_local_gateway)

    ok, result, error = await skillforgebridge.handle_bridge_op(
        "register_skill",
        {
            "skill_id": "agent-skill",
            "runtime": {
                "backend": "hybrid",
                "fallback": "bridge_script",
                "entry": "SKILL.md",
                "script_entry": "scripts/main.py",
            },
        },
    )

    assert ok is True
    assert error is None
    assert result["registered"] is True
    assert result["method"] == "bridge"
    assert calls == []

    registry = json.loads((state_dir / "agent_skills_registry.json").read_text(encoding="utf-8"))
    entry = registry["agent-skill"]
    assert entry["skill_dir"] == str(skill_dir)
    assert entry["runtime"]["backend"] == "hybrid"
    assert entry["status"] == "active"


@pytest.mark.asyncio
async def test_bridge_scheduler_falls_back_to_script_runtime(monkeypatch):
    """Agent runtime 不可用且声明 fallback 时，应降级 bridge_script 并标记 fallback_from。"""
    import skillforgebridge

    calls: list[str] = []

    async def fake_handle_bridge_op(op, payload):
        calls.append(op)
        if op == "run_agent_skill":
            return False, None, {"message": "unknown method: skill.run"}
        assert op == "run_skill_script"
        return True, {
            "success": True,
            "script": "scripts/main.py",
            "duration_ms": 12,
            "output": {"reports": [{"title": "fallback ok"}], "todos": []},
        }, None

    submissions: list[dict] = []

    def fake_post_submit_result(submit_url, submit_token, skill_id, output, run_id, **kwargs):
        submissions.append({"output": output, "run_id": run_id, "kwargs": kwargs})
        if kwargs.get("status") == "running":
            return {"run_token": "run-token-fallback", "run_id": "rm-fallback-1"}
        return None

    monkeypatch.setattr(skillforgebridge, "INSTANCE_ID", "node-agent-1")
    monkeypatch.setattr(skillforgebridge, "handle_bridge_op", fake_handle_bridge_op)
    monkeypatch.setattr(skillforgebridge, "_post_submit_result", fake_post_submit_result)

    await skillforgebridge._execute_scheduled_skill(
        "agent-skill",
        "http://skillforge.test/api/executions/submit-result",
        "token-1",
        {
            "runtime": {
                "backend": "openclaw_agent",
                "fallback": "bridge_script",
                "script_entry": "scripts/main.py",
            },
        },
    )

    assert calls == ["run_agent_skill", "run_skill_script"]
    assert len(submissions) == 2
    assert submissions[0]["kwargs"]["status"] == "running"
    assert submissions[0]["run_id"].startswith("node-")
    output = submissions[1]["output"]
    assert submissions[1]["kwargs"]["status"] == "completed"
    assert output["reports"][0]["title"] == "fallback ok"
    assert output["_skillforge_meta"]["execution_backend"] == "bridge_script"
    assert output["_skillforge_meta"]["skillforge_run_id"] == "rm-fallback-1"
    assert output["_skillforge_meta"]["remote_run_id"] == submissions[1]["run_id"]
    assert output["_skillforge_meta"]["fallback_from"] == "openclaw_agent"


def test_bridge_update_header_detection_ignores_source_literal():
    """自动更新只保留 render 配置块，不把源码里 marker 字符串当配置块。"""
    source_only_lines = [
        "def check_and_apply_update():\n",
        "    marker = \"# === bridge 主程序源码\"\n",
        "    return marker\n",
    ]
    assert _preserved_update_header(source_only_lines) == ""

    rendered_lines = [
        "# SkillForge bridge generated header\n",
        "_BRIDGE_CONFIG = {}\n",
        "# === bridge 主程序源码（自动更新时会被 server 最新版覆盖）===\n",
        "def body():\n",
    ]
    assert _preserved_update_header(rendered_lines) == (
        "# SkillForge bridge generated header\n"
        "_BRIDGE_CONFIG = {}\n"
        "# === bridge 主程序源码（自动更新时会被 server 最新版覆盖）===\n\n"
    )


@pytest.mark.asyncio
async def test_bridge_handle_unknown_op(tmp_path, monkeypatch):
    """验证未知操作返回 False + 错误消息。"""

    ok, result, error = await handle_bridge_op("nonexistent_op", {})

    assert ok is False
    assert result is None
    assert "unknown op" in error["message"]


# ═══════════════════════════════════════════════════════════════════════════
#  补充: push_schedules_to_targets 部分实例推送失败
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_push_schedules_partial_failure(client, monkeypatch):
    """验证部分节点推送失败时仍能记录成功节点的 NodeScheduleConfig。"""

    async with db_mod.async_session_factory() as session:
        session.add(_make_skill("test-partial"))
        session.add(_make_instance("good-node"))
        session.add(_make_instance("bad-node"))
        await session.commit()

    call_count = 0

    async def fake_sync_schedules(self, *, schedules, submit_token, submit_url, config_version):
        nonlocal call_count
        call_count += 1
        if self.instance_id == "bad-node":
            raise ConnectionError("节点离线")
        return {"accepted": len(schedules), "config_version": config_version}

    async def fake_list_local_skills(self, target_dir=None):
        return {"skills": ["test-partial"], "dir": "/tmp/skills"}

    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.sync_schedules",
        fake_sync_schedules,
    )
    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.list_local_skills",
        fake_list_local_skills,
    )
    monkeypatch.setattr("app.config.settings.BROWSER_REMOTE_TOKEN", "test-token")
    monkeypatch.setattr("app.config.settings.SCHEDULER_TIMEZONE", "Asia/Shanghai")

    from app.execution.sync_service import sync_service

    results = await sync_service.push_schedules_to_targets(
        skill_ids=["test-partial"],
    )

    # 两个节点都尝试了推送
    assert call_count == 2

    # 结果中一个成功一个失败
    ok_results = [r for r in results if r["ok"]]
    fail_results = [r for r in results if not r["ok"]]
    assert len(ok_results) == 1
    assert len(fail_results) == 1
    assert ok_results[0]["instance_id"] == "good-node"
    assert fail_results[0]["instance_id"] == "bad-node"

    # DB 中 good-node 的 ack_ok=True, bad-node 的 ack_ok=False
    from sqlalchemy import select

    async with db_mod.async_session_factory() as session:
        configs = (await session.execute(
            select(NodeScheduleConfig).order_by(NodeScheduleConfig.instance_id)
        )).scalars().all()
        config_map = {c.instance_id: c for c in configs}
        assert config_map["good-node"].ack_ok is True
    assert config_map["bad-node"].ack_ok is False


@pytest.mark.asyncio
async def test_push_schedules_skips_skill_missing_on_node(client, monkeypatch):
    """节点本地缺少 Skill 时不能继续下发会执行失败的 schedule。"""

    async with db_mod.async_session_factory() as session:
        session.add(_make_skill("missing-cron"))
        session.add(_make_instance("node-missing"))
        session.add(NodeScheduleConfig(
            instance_id="node-missing",
            skill_id="missing-cron",
            cron_expression="0 9 * * *",
            config_version=1,
            ack_ok=True,
        ))
        await session.commit()

    sync_calls: list[dict] = []

    async def fake_list_local_skills(self, target_dir=None):
        return {"skills": [], "dir": "/tmp/skills"}

    async def fake_sync_schedules(self, *, schedules, submit_token, submit_url, config_version):
        sync_calls.append({
            "instance_id": self.instance_id,
            "schedules": schedules,
        })
        return {"accepted": len(schedules), "config_version": config_version}

    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.list_local_skills",
        fake_list_local_skills,
    )
    monkeypatch.setattr(
        "app.aiclaw.client.AIClawClient.sync_schedules",
        fake_sync_schedules,
    )

    from app.execution.sync_service import sync_service

    results = await sync_service.push_schedules_to_targets(
        skill_ids=["missing-cron"],
        target_instance_ids=["node-missing"],
    )

    assert sync_calls == [{"instance_id": "node-missing", "schedules": []}]
    assert results[0]["ok"] is False
    assert results[0]["missing_local_skills"] == ["missing-cron"]
    assert "NODE_SKILL_MISSING" in (results[0]["error"] or "")

    from sqlalchemy import select

    async with db_mod.async_session_factory() as session:
        configs = (await session.execute(
            select(NodeScheduleConfig).where(NodeScheduleConfig.instance_id == "node-missing")
        )).scalars().all()

    assert configs == []


# ═══════════════════════════════════════════════════════════════════════════
#  补充: submit-result 幂等性（相同 instance_id + remote_run_id）
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_submit_result_idempotent(client, monkeypatch):
    """同一 instance_id + remote_run_id 重复提交应返回 idempotent=True。"""

    monkeypatch.setattr("app.config.settings.BROWSER_REMOTE_TOKEN", "test-token")

    async with db_mod.async_session_factory() as session:
        session.add(_make_skill("test-idempotent"))
        await session.commit()

    payload = {
        "skill_id": "test-idempotent",
        "output": {"conclusion": "通过"},
        "triggered_by": "node_scheduler",
        "instance_id": "node-1",
        "remote_run_id": "dedup-001",
    }

    # 第一次提交
    resp1 = await client.post(
        "/api/executions/submit-result",
        params={"token": "test-token"},
        json=payload,
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["idempotent"] is False

    # 第二次提交（同样的 instance_id + remote_run_id）
    resp2 = await client.post(
        "/api/executions/submit-result",
        params={"token": "test-token"},
        json=payload,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["idempotent"] is True
    # run_id 应该相同
    assert data2["run_id"] == data1["run_id"]
