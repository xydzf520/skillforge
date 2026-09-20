import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from datetime import timedelta

import pytest

from app.common.time_utils import now_bjt
from app.codex.models import CodexMcpCallAudit
from app.common.models import IntelligenceAnalyzeCache, IntelligenceAnalyzeRun
from app.execution.models import DecisionLog, ExecutionRun, ExecutionStep
from app.skills.core.models import Skill


def test_project_managed_training_gate_prevents_generic_threshold_training():
    from app.learning.service import _managed_project_training_gate

    project = SimpleNamespace(metadata_json={
        "manifest": {
            "metadata": {
                "learning_loop": {
                    "managed_training": True,
                    "minimum_training_samples": 300,
                    "candidate_source": "media_workbench_learning_loop",
                }
            }
        }
    })

    assert _managed_project_training_gate(project) == {
        "managed_training": True,
        "minimum_training_samples": 300,
        "candidate_source": "media_workbench_learning_loop",
    }
    project.metadata_json["manifest"]["metadata"]["learning_loop"]["managed_training"] = False
    assert _managed_project_training_gate(project) == {}


@pytest.mark.asyncio
async def test_generic_learning_candidate_skips_project_managed_training_gate(client, monkeypatch):
    import app.database as db_mod
    from app.learning.models import LearningArtifact
    from app.learning import service as learning_service
    from app.projects.models import Project
    from app.training.models import TrainingJob
    from sqlalchemy import func, select

    project_id = "managed-training-project-test"
    target_skill_id = f"project:{project_id}"
    yesterday = now_bjt() - timedelta(days=1)
    monkeypatch.setattr(
        learning_service,
        "materialize_ecommerce_learning_flow_dataset",
        AsyncMock(return_value={"namespace": "test", "total": 0, "stats": {}}),
    )

    async with db_mod.async_session_factory() as session:
        session.add(Project(
            id=project_id,
            name="自管训练项目",
            type="internal_tool",
            department="AI小组",
            department_id="ai-team",
            owner_user_id="admin",
            visibility="department",
            status="active",
            metadata_json={
                "manifest": {
                    "metadata": {
                        "learning_loop": {
                            "managed_training": True,
                            "minimum_training_samples": 300,
                            "candidate_source": "media_workbench_learning_loop",
                        }
                    }
                }
            },
        ))
        for index in range(4):
            session.add(LearningArtifact(
                id=f"la-managed-project-{index}",
                event_id=f"le-managed-project-{index}",
                artifact_kind="training_sample",
                artifact_hash=f"{index + 1:064x}",
                target_type="project",
                target_id=project_id,
                department="AI小组",
                org_unit_id="ai-team",
                skill_id=target_skill_id,
                content_json={"sample": index},
                quality_score=0.9,
                confidence=0.9,
                status="materialized",
                sink_type="training_sample",
                created_at=yesterday,
                updated_at=yesterday,
            ))
        await session.flush()
        user = SimpleNamespace(
            id="admin",
            username="admin",
            name="管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
        )
        result = await learning_service.ensure_training_candidates_from_learning_samples(
            session,
            user,
            days=7,
            min_sample_count=4,
        )
        training_count = int((await session.execute(
            select(func.count(TrainingJob.id)).where(TrainingJob.target_skill_id == target_skill_id)
        )).scalar_one() or 0)
        await session.rollback()

    assert training_count == 0
    assert any(
        item["skill_id"] == target_skill_id
        and item["reason"] == "managed_project_training_gate"
        and item["minimum_training_samples"] == 300
        for item in result["skipped"]
    )


@pytest.fixture
def _isolate_learning_auto_flow_worker_state(tmp_path, monkeypatch):
    import app.learning.worker as worker

    monkeypatch.setattr(worker, "_STATE_PATH", tmp_path / "learning-auto-flow-state.json")
    monkeypatch.setattr(worker, "_LAST_AUTOMATION_RUN", {
        "status": "never_run",
        "started_at": None,
        "finished_at": None,
        "error": None,
        "result": None,
    })
    monkeypatch.setattr(worker, "_SERVICE_STATE", {
        "service_status": "unknown",
        "service_mode": None,
        "service_owner": None,
        "service_started_at": None,
        "service_stopped_at": None,
        "first_run_planned_at": None,
        "next_run_planned_at": None,
        "last_loop_finished_at": None,
    })


async def _seed_cli_session_token(raw_token: str, *, role: str = "admin", department: str = "AI小组") -> None:
    from app.auth.models import User
    from app.codex import service as codex_service
    from app.codex.models import CodexCliSession
    from app.database import async_session_factory

    async with async_session_factory() as db:
        user = await db.get(User, "admin")
        if user is None:
            db.add(User(
                id="admin",
                username="admin",
                name="管理员",
                role=role,
                department=department,
                can_view_all=True,
                is_active=True,
                state="active",
                permissions_rev=0,
            ))
        else:
            user.role = role
            user.department = department
            user.can_view_all = True
            user.is_active = True
            user.state = "active"
            user.permissions_rev = 0
        db.add(CodexCliSession(
            id=f"cli_{raw_token}",
            user_id="admin",
            token_hash=codex_service.token_hash(raw_token),
            device_name="pytest",
            scopes_json={"source": "pytest"},
            permissions_rev_snapshot=0,
            expires_at=now_bjt() + timedelta(days=30),
        ))
        await db.commit()


@pytest.mark.asyncio
async def test_learning_backfill_materializes_run_report_and_training_sample(client):
    import app.database as db_mod
    from app.execution.artifact_service import persist_execution_artifact

    yesterday = now_bjt() - timedelta(days=1)
    async with db_mod.async_session_factory() as session:
        skill = Skill(
            id="learn-skill",
            name="学习闭环 Skill",
            display_name="学习闭环 Skill",
            department="AI小组",
            status="active",
            visibility="department",
            approval_level=0,
        )
        run = ExecutionRun(
            id="run-learning-1",
            skill_id="learn-skill",
            trigger_type="manual",
            run_mode="manual_real",
            status="completed",
            total_steps=1,
            completed_steps=1,
            summary="本次发现知识库应自动沉淀报告结论。",
            started_at=now_bjt(),
            completed_at=now_bjt(),
        )
        decision = DecisionLog(
            run_id="run-learning-1",
            skill_id="learn-skill",
            input_snapshot={"question": "链接下滑原因"},
            output_result={
                "reports": [
                    {
                        "title": "链接下滑复盘",
                        "summary": "搜索访客下降，建议优化关键词和价格力。",
                        "tags": ["经营复盘"],
                        "metrics": [{"name": "搜索访客", "value": "-18%"}],
                    }
                ],
                "todos": [{"title": "优化关键词", "assignee": "ops"}],
            },
            user_action="completed",
            user_feedback="报告有效，已执行。",
            rating=5,
            created_at=now_bjt(),
            is_sandbox=False,
        )
        step = ExecutionStep(
            run_id="run-learning-1",
            skill_id="learn-skill",
            step_order=1,
            status="completed",
            input_data={"question": "链接下滑原因", "api_key": "raw-step-input-secret-should-not-leak"},
            output_data={"summary": "搜索访客下降，建议优化关键词。", "access_token": "raw-step-output-secret-should-not-leak"},
            duration_ms=123,
        )
        session.add_all([skill, run, decision, step])
        await session.flush()
        decision_id = decision.id
        cache = IntelligenceAnalyzeCache(
            cache_key="learn-cache-key",
            model="deepseek-v4-pro",
            prompt_hash="1" * 64,
            context_hash="2" * 64,
            output_hash="3" * 64,
            output_payload={"summary": "智能分析认为应优先修复搜索访客。"},
            prompt_tokens=12,
            completion_tokens=8,
            total_tokens=20,
            evidence_passed=True,
            first_seen_run_id="run-learning-1",
        )
        session.add(cache)
        await session.flush()
        session.add(
            IntelligenceAnalyzeRun(
                cache_key="learn-cache-key",
                cache_id=cache.id,
                cache_hit=False,
                skill_id="learn-skill",
                skill_git_commit_full="a" * 40,
                prompt_git_ref="a" * 40 + ":prompts/analysis_v1.md",
                run_id="run-learning-1",
                instance_id="node-learning-1",
                department="AI小组",
                model="deepseek-v4-pro",
                analysis_backend="platform",
                analysis_delegate_route={"mode": "platform", "scope": "platform_llm"},
                prompt_version="analysis_v1",
                prompt_hash="1" * 64,
                context_hash="2" * 64,
                prompt_tokens=12,
                completion_tokens=8,
                total_tokens=20,
                cost_usd=0,
                duration_ms=123,
                degraded=False,
                output_hash="3" * 64,
                llm_output_hash="3" * 64,
                evidence_passed=True,
            )
        )
        await persist_execution_artifact(
            session,
            run_id="run-learning-1",
            skill_id="learn-skill",
            kind="raw-input",
            payload={"question": "链接下滑原因", "secret_token": "raw-input-secret-should-not-leak"},
            decision_log_id=decision_id,
        )
        await persist_execution_artifact(
            session,
            run_id="run-learning-1",
            skill_id="learn-skill",
            kind="raw-output",
            payload={
                "collection_schema": "learning-run-output-v1",
                "reports": [{"title": "链接下滑复盘"}],
                "secret_token": "raw-output-secret-should-not-leak",
            },
            decision_log_id=decision_id,
        )
        await session.commit()

    res = await client.post("/api/learning/events/backfill", json={"days": 7, "limit": 50, "materialize": True})
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["captured"]["execution_run"] >= 1
    assert payload["captured"]["execution_artifact"] >= 2
    assert payload["captured"]["intelligence_analyze_run"] >= 1
    assert payload["captured"]["decision_log"] >= 1

    summary = (await client.get("/api/learning/summary", params={"days": 7})).json()
    assert summary["events"]["total"] >= 2
    assert summary["knowledge"]["indexed"] >= 1
    assert summary["training"]["samples"] >= 1

    home = (await client.get("/api/learning/home", params={"days": 7, "skill_id": "learn-skill"})).json()
    assert home["summary"]["events"]["total"] >= 2
    assert "stages" in home["topology"]
    assert "automation" in home["automation"]
    assert "events" not in home
    assert "artifacts" not in home

    artifacts = (await client.get("/api/learning/artifacts", params={"limit": 50})).json()["items"]
    kinds = {item["artifact_kind"] for item in artifacts}
    assert "report_summary" in kinds
    assert "execution_data_artifact" in kinds
    assert "training_sample" in kinds
    assert "raw-input-secret-should-not-leak" not in str(artifacts)
    assert "raw-output-secret-should-not-leak" not in str(artifacts)
    analysis_events = (await client.get(
        "/api/learning/events",
        params={"source_type": "intelligence_analyze_run", "skill_id": "learn-skill", "days": 7},
    )).json()["items"]
    assert analysis_events
    assert analysis_events[0]["metadata"]["output_hash"] == "3" * 64
    assert analysis_events[0]["metadata"]["analysis_delegate_route"]["mode"] == "platform"

    manifest = (await client.get("/api/learning/training-manifest", params={"skill_id": "learn-skill"})).json()
    assert manifest["sample_total"] >= 1
    assert any(
        sample["source_type"] == "intelligence_analyze_run"
        for sample in manifest["samples"]
    )
    assert manifest["governance"]["raw_payload_returned"] is False
    assert manifest["dataset_ref"] == "learning-artifacts://learn-skill/training/latest"

    graph = (await client.get("/api/learning/flow-graph", params={"days": 7})).json()
    relations = {edge["relation"] for edge in graph["edges"]}
    assert "produced" in relations
    assert "indexed_as" in relations
    assert "produced_artifact" in relations
    assert "captured_data" in relations
    assert any(
        edge["source"] == "run:run-learning-1"
        and edge["target"].startswith("execution_artifact:")
        and edge["relation"] == "produced_artifact"
        for edge in graph["edges"]
    )
    assert any(
        edge["source"] == f"decision_log:{decision_id}"
        and edge["target"].startswith("execution_artifact:")
        and edge["relation"] == "captured_data"
        for edge in graph["edges"]
    )
    assert any(
        edge["source"] == "run:run-learning-1"
        and edge["target"].startswith("intelligence_analyze:")
        and edge["relation"] == "used_analysis"
        for edge in graph["edges"]
    )
    assert "raw-input-secret-should-not-leak" not in str(graph)
    assert "raw-output-secret-should-not-leak" not in str(graph)

    from sqlalchemy import func, select
    from app.learning.models import LearningArtifact

    async with db_mod.async_session_factory() as session:
        training_samples = (
            await session.execute(
                select(LearningArtifact)
                .where(LearningArtifact.run_id == "run-learning-1")
                .where(LearningArtifact.artifact_kind == "training_sample")
            )
        ).scalars().all()
        trace_sample = next(item for item in training_samples if item.content_json.get("source_type") == "execution_trace")
        trace_content = trace_sample.content_json
    assert trace_sample.status == "materialized"
    assert trace_sample.sink_type == "training_sample"
    assert trace_content["input"]["steps"][0]["input"]["question"] == "链接下滑原因"
    assert trace_content["output"]["steps"][0]["output"]["summary"] == "搜索访客下降，建议优化关键词。"
    assert trace_content["formed_data"]["steps"][0]["duration_ms"] == 123
    encoded_trace = json.dumps(trace_content, ensure_ascii=False)
    assert "raw-step-input-secret-should-not-leak" not in encoded_trace
    assert "raw-step-output-secret-should-not-leak" not in encoded_trace

    topology = (await client.get("/api/learning/flow-topology", params={"days": 7, "skill_id": "learn-skill"})).json()
    assert [stage["key"] for stage in topology["stages"]] == ["source", "event", "artifact", "sink", "candidate", "review"]
    assert [(item["from"], item["to"]) for item in topology["connectors"]] == [
        ("source", "event"),
        ("event", "artifact"),
        ("artifact", "sink"),
        ("sink", "candidate"),
        ("candidate", "review"),
    ]
    assert topology["connectors"][0]["relation"] == "captured_as_event"
    assert topology["connectors"][1]["count"] >= 1
    assert topology["connectors"][3]["relation"] == "fed_back_to_candidate"
    assert topology["governance"]["raw_payload_returned"] is False
    artifact_nodes = next(stage for stage in topology["stages"] if stage["key"] == "artifact")["nodes"]
    assert artifact_nodes
    assert "content" not in artifact_nodes[0]["payload"]

    journeys = (await client.get("/api/learning/flow-journeys", params={"days": 7, "skill_id": "learn-skill"})).json()
    assert journeys["items"]
    first_journey = journeys["items"][0]
    assert first_journey["state"] in {"completed", "flowing", "blocked"}
    assert first_journey["progress"] > 0
    stage_names = [step["stage"] for step in first_journey["steps"]]
    assert "source" in stage_names
    assert "event" in stage_names
    assert "artifact" in stage_names
    assert "sink" in stage_names
    assert journeys["governance"]["raw_payload_returned"] is False
    assert all("content" not in step.get("payload", {}) for step in first_journey["steps"])


@pytest.mark.asyncio
async def test_learning_training_manifest_accepts_cli_token(client):
    import app.database as db_mod
    from app.auth.dependencies import get_current_user, require_state_active_web_or_cli
    from app.learning.models import LearningArtifact, LearningEvent

    raw_token = "test-learning-manifest-cli-token"
    await _seed_cli_session_token(raw_token)
    app = client._transport.app
    old_current_user = app.dependency_overrides.pop(get_current_user, None)
    old_web_or_cli = app.dependency_overrides.pop(require_state_active_web_or_cli, None)

    async with db_mod.async_session_factory() as session:
        session.add(Skill(
            id="learn-cli-skill",
            name="学习 CLI Skill",
            display_name="学习 CLI Skill",
            department="AI小组",
            status="active",
            visibility="department",
            approval_level=0,
        ))
        session.add(LearningEvent(
            id="le-cli-manifest",
            event_type="decision.completed",
            source_type="decision_log",
            source_id="decision-cli-manifest",
            source_hash="decision-cli-manifest",
            department="AI小组",
            skill_id="learn-cli-skill",
            redacted_summary="CLI 样本入口验证。",
            quality_score=0.9,
            created_at=now_bjt(),
            updated_at=now_bjt(),
        ))
        session.add(LearningArtifact(
            id="la-cli-manifest-sample",
            event_id="le-cli-manifest",
            artifact_kind="training_sample",
            artifact_hash="la-cli-manifest-sample",
            target_type="skill",
            target_id="learn-cli-skill",
            department="AI小组",
            skill_id="learn-cli-skill",
            title="CLI 训练样本",
            summary="用于验证 CLI 可以读取治理后的 manifest。",
            content_json={
                "instruction": "分析",
                "input": {"business_id": "item-1"},
                "output": {"summary": "建议"},
                "source_type": "todo_feedback",
                "source_channel": "dingtalk",
                "human_feedback_input": {
                    "source_channel": "dingtalk",
                    "fields": {
                        "feedback_type": "确认有效",
                        "rating": 5,
                        "feedback": "这段用户填写原文不能进入 manifest",
                    },
                },
                "feedback": {"status": "approved", "source_channel": "dingtalk"},
                "model_context": {
                    "model_deployment_id": "deploy-cli-manifest",
                    "artifact_sha256": "e" * 64,
                },
            },
            labels_json=["training"],
            quality_score=0.9,
            confidence=0.8,
            status="materialized",
            sink_type="training_sample",
            created_at=now_bjt(),
            updated_at=now_bjt(),
        ))
        session.add(LearningArtifact(
            id="la-cli-manifest-ready-sample",
            event_id="le-cli-manifest",
            artifact_kind="training_sample",
            artifact_hash="la-cli-manifest-ready-sample",
            target_type="skill",
            target_id="learn-cli-skill",
            department="AI小组",
            skill_id="learn-cli-skill",
            title="CLI 未物化训练样本",
            summary="ready 样本不能进入训练 manifest。",
            content_json={"instruction": "分析", "output": "暂不进入训练"},
            labels_json=["training"],
            quality_score=0.8,
            confidence=0.7,
            status="ready",
            sink_type=None,
            created_at=now_bjt(),
            updated_at=now_bjt(),
        ))
        await session.commit()

    try:
        resp = await client.get(
            "/api/learning/training-manifest",
            headers={"Authorization": f"Bearer {raw_token}"},
            params={"skill_id": "learn-cli-skill"},
        )
    finally:
        if old_current_user is not None:
            app.dependency_overrides[get_current_user] = old_current_user
        if old_web_or_cli is not None:
            app.dependency_overrides[require_state_active_web_or_cli] = old_web_or_cli

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sample_total"] == 1
    assert body["dataset_ref"] == "learning-artifacts://learn-cli-skill/training/latest"
    assert body["governance"]["raw_payload_returned"] is False
    assert body["samples"][0]["artifact_id"] == "la-cli-manifest-sample"
    assert body["samples"][0]["status"] == "materialized"
    assert body["samples"][0]["sink_type"] == "training_sample"
    assert body["samples"][0]["source_type"] == "todo_feedback"
    assert body["samples"][0]["source_channel"] == "dingtalk"
    lineage = body["samples"][0]["lineage_summary"]
    assert lineage["has_input"] is True
    assert lineage["has_output"] is True
    assert lineage["has_human_feedback"] is True
    assert lineage["feedback_source"] == "dingtalk"
    assert lineage["feedback_fields"] == ["feedback", "feedback_type", "rating"]
    assert lineage["feedback_type"] == "确认有效"
    assert lineage["rating"] == 5
    assert lineage["model_deployment_id"] == "deploy-cli-manifest"
    assert lineage["artifact_sha256"] == "e" * 64
    assert "这段用户填写原文不能进入 manifest" not in str(body)
    assert "la-cli-manifest-ready-sample" not in {item["artifact_id"] for item in body["samples"]}
    assert "content_json" not in body["samples"][0]


@pytest.mark.asyncio
async def test_learning_auto_training_candidate_uses_auto_active_default_after_eval(client):
    import app.database as db_mod
    from app.auth.models import User
    from app.learning.models import LearningArtifact, LearningEvent
    from app.learning.service import ensure_training_candidates_from_learning_samples
    from app.training.models import TrainingJob
    from sqlalchemy import select

    yesterday = now_bjt() - timedelta(days=1)
    async with db_mod.async_session_factory() as session:
        user = User(
            id="learning-auto-train-admin",
            username="learning-auto-train-admin",
            name="学习闭环管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
        )
        skill = Skill(
            id="learn-auto-deploy-skill",
            name="自动部署审核 Skill",
            display_name="自动部署审核 Skill",
            department="AI小组",
            status="active",
            visibility="department",
            approval_level=0,
        )
        event = LearningEvent(
            id="le-auto-deploy-source",
            event_type="decision.feedback",
            source_type="decision_log",
            source_id="decision-auto-deploy",
            source_hash="decision-auto-deploy",
            department="AI小组",
            skill_id=skill.id,
            redacted_summary="自动训练候选样本。",
            quality_score=0.9,
            created_at=yesterday,
            updated_at=yesterday,
        )
        session.add_all([user, skill, event])
        await session.flush()
        for index in range(3):
            session.add(LearningArtifact(
                id=f"la-auto-deploy-sample-{index}",
                event_id=event.id,
                artifact_kind="training_sample" if index < 2 else "eval_case",
                artifact_hash=f"la-auto-deploy-sample-{index}",
                target_type="skill",
                target_id=skill.id,
                department="AI小组",
                skill_id=skill.id,
                title=f"自动训练样本 {index}",
                summary="用于验证学习闭环训练候选会自动提交部署审核。",
                content_json={
                    "input": {"item_id": f"item-{index}"},
                    "output": {"summary": "建议优化"},
                    "feedback": {"status": "approved"},
                    "source_type": "todo_feedback",
                },
                labels_json=["training"],
                quality_score=0.9,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
                created_at=yesterday,
                updated_at=yesterday,
            ))
        result = await ensure_training_candidates_from_learning_samples(
            session,
            user,
            days=7,
            min_sample_count=3,
            max_skills=1,
        )
        await session.commit()

    assert result["created"]
    created = result["created"][0]
    assert created["status"] == "awaiting_review"
    assert created["window"]["mode"] == "yesterday"

    async with db_mod.async_session_factory() as session:
        job = await session.get(TrainingJob, created["training_job_id"])
        assert job is not None
        spec = job.spec_json or {}

    assert spec["source"] == "learning_sample_threshold"
    assert spec["training_mode"] == "daily_incremental"
    assert spec["dataset"]["window"]["mode"] == "yesterday"
    assert spec["dataset"]["window"]["date"] == created["window"]["date"]
    assert spec["governance"]["no_auto_approval"] is False
    assert spec["governance"]["auto_training_agent"] == "learning_training_reviewer"
    assert spec["governance"]["incremental_training"] is True
    assert spec["governance"]["deployment_review_required"] is False
    assert spec["governance"]["auto_deployment_mode"] == "active_after_eval_readiness"
    assert "no_auto_deploy" not in spec["governance"]
    assert spec["model"] == {
        "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
        "source": "internal",
        "internal_model_ref": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
    }
    assert spec["deployment"]["auto_request"] is True
    assert spec["deployment"]["auto_active"] is True
    assert spec["deployment"]["target_skill_ids"] == ["learn-auto-deploy-skill"]
    assert spec["deployment"]["model_family"] == (
        "learn-auto-deploy-skill:qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive:learning-loop-adapter"
    )
    assert spec["deployment"]["deployment_target_gateway_id"] == "inference-primary"
    assert spec["deployment"]["deployment_runtime_profile"] == "mlx-qwen3.6-35b-a3b-lora"
    assert spec["deployment"]["rollout_percent"] == 100
    assert spec["deployment"]["keyword_review"]["enabled"] is True
    assert "安全套" in spec["deployment"]["keyword_review"]["keywords"]
    assert spec["dataset"]["bridge_dataset"]["source_gateway_id"] == "data-primary"
    assert spec["dataset"]["bridge_dataset"]["target_gateway_id"] == "training-primary"
    assert spec["governance"]["bridge_dataset"]["storage"] == "bridge_dataset"
    assert spec["eval_gate"]["manual_review_required"] is False


@pytest.mark.asyncio
async def test_ecommerce_sf_data_records_materialize_into_training_candidate(client):
    import app.database as db_mod
    from app.auth.models import User
    from app.codex.models import SfDataRecord
    from app.learning.models import LearningArtifact
    from app.learning.service import (
        ECOMMERCE_LEARNING_FLOW_DATA_NAMESPACE,
        ensure_training_candidates_from_learning_samples,
        materialize_ecommerce_learning_flow_dataset,
    )
    from app.training.models import TrainingJob
    from sqlalchemy import select

    yesterday = now_bjt() - timedelta(days=1)
    async with db_mod.async_session_factory() as session:
        user = User(
            id="ecommerce-dataset-admin",
            username="ecommerce-dataset-admin",
            name="电商数据集管理员",
            role="admin",
            department="EC",
            can_view_all=True,
            is_active=True,
        )
        skill = Skill(
            id="ecommerce-skill",
            name="电商动作 Skill",
            display_name="电商动作 Skill",
            department="EC",
            status="active",
            visibility="department",
            approval_level=0,
        )
        record = SfDataRecord(
            id="sfdata_ecommerce_learning_cases",
            namespace=ECOMMERCE_LEARNING_FLOW_DATA_NAMESPACE,
            title="电商 learning-flow 测试集",
            content_type="json",
            data_json=[
                {
                    "case_id": "ec-train-1",
                    "skill_id": skill.id,
                    "department": "EC",
                    "split": "train",
                    "input_features": {"shop_id": "shop-1", "sku_id": "sku-1", "traffic_drop": 0.32},
                    "expected_action": {"action": "optimize_title"},
                    "outcome_metrics": {"ctr_lift": 0.08},
                    "labels": ["tmall"],
                    "quality_score": 0.91,
                },
                {
                    "case_id": "ec-train-2",
                    "skill_id": skill.id,
                    "department": "EC",
                    "split": "train",
                    "input_features": {"shop_id": "shop-1", "sku_id": "sku-2", "conversion_drop": 0.21},
                    "expected_action": {"action": "adjust_coupon"},
                    "outcome_metrics": {"cvr_lift": 0.05},
                    "labels": ["tmall"],
                    "quality_score": 0.88,
                },
                {
                    "case_id": "ec-eval-1",
                    "skill_id": skill.id,
                    "department": "EC",
                    "split": "eval",
                    "input_features": {"shop_id": "shop-2", "sku_id": "sku-9", "link_decline": 0.41},
                    "expected_action": {"action": "diagnose_link_decline"},
                    "outcome_metrics": {"win": True},
                    "labels": ["tmall", "eval"],
                    "quality_score": 0.93,
                },
            ],
            metadata_json={"source": "pytest"},
            schema_json={"type": "array"},
            sha256="c" * 64,
            size_bytes=512,
            source="pytest",
            source_tool="ecommerce_fixture",
            skill_id=skill.id,
            user_id=user.id,
            department="EC",
            visibility="global",
            created_at=yesterday,
        )
        session.add_all([user, skill, record])
        await session.flush()

        materialized = await materialize_ecommerce_learning_flow_dataset(session, user)
        result = await ensure_training_candidates_from_learning_samples(
            session,
            user,
            days=7,
            min_sample_count=3,
            max_skills=1,
        )
        await session.commit()

    assert materialized["total"] == 3
    assert materialized["stats"]["train"] == 2
    assert materialized["stats"]["eval"] == 1
    assert result["ecommerce_dataset"]["materialized_total"] == 3
    assert result["created"]
    created = result["created"][0]
    assert created["skill_id"] == "ecommerce-skill"
    assert created["sample_total"] == 3
    assert created["eval_count"] == 1

    async with db_mod.async_session_factory() as session:
        artifacts = (
            await session.execute(
                select(LearningArtifact)
                .where(LearningArtifact.skill_id == "ecommerce-skill")
                .order_by(LearningArtifact.artifact_kind, LearningArtifact.id)
            )
        ).scalars().all()
        job = await session.get(TrainingJob, created["training_job_id"])

    assert len(artifacts) == 3
    assert {artifact.artifact_kind for artifact in artifacts} == {"training_sample", "eval_case"}
    assert all(artifact.status == "materialized" for artifact in artifacts)
    assert all(artifact.sink_type == "training_sample" for artifact in artifacts)
    spec = job.spec_json or {}
    assert spec["dataset"]["train_count"] == 2
    assert spec["dataset"]["eval_count"] == 1
    assert spec["deployment"]["auto_active"] is True
    assert spec["deployment"]["rollout_percent"] == 100
    assert spec["deployment"]["deployment_target_gateway_id"] == "inference-primary"
    assert spec["deployment"]["deployment_runtime_profile"] == "mlx-qwen3.6-35b-a3b-lora"


@pytest.mark.asyncio
async def test_learning_full_history_training_candidate_uses_parent_and_platform_selector(client):
    import app.database as db_mod
    from app.auth.models import User
    from app.learning.models import LearningArtifact, LearningEvent
    from app.learning.service import ensure_full_history_platform_training_candidate
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment
    from sqlalchemy import func, select

    now = now_bjt()
    target_skill_id = "full-history-target-skill"
    parent_deployment_id = "deploy_full_history_parent"
    async with db_mod.async_session_factory() as session:
        user = User(
            id="full-history-admin",
            username="full-history-admin",
            name="全历史管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
            state="active",
        )
        target_skill = Skill(
            id=target_skill_id,
            name="全历史训练目标 Skill",
            display_name="全历史训练目标 Skill",
            department="AI小组",
            status="active",
            visibility="company",
            approval_level=0,
        )
        session.add_all([
            user,
            target_skill,
            TrainingModelDeployment(
                id=parent_deployment_id,
                job_id="train_full_history_parent",
                department="AI小组",
                model_family="full-history-target-skill:learning-loop-adapter",
                artifact_id="artifact-full-history-parent",
                artifact_ref_json={
                    "uri": "file:///Users/skillforge/full-history-parent-adapter.tar.gz",
                    "source_uri": "file:///tmp/full-history-parent-adapter.tar.gz",
                    "training_gateway_id": "training-primary",
                    "deployment_target_gateway_id": "inference-primary",
                    "sha256": "f" * 64,
                },
                target_skill_ids_json=[target_skill_id],
                status="canary",
                rollout_percent=10,
                requested_by="admin",
                created_at=now,
                updated_at=now,
            ),
            LearningEvent(
                id="le-full-history-candidate",
                event_type="decision.feedback",
                source_type="decision_log",
                source_id="decision-full-history-candidate",
                source_hash="decision-full-history-candidate",
                department="AI小组",
                redacted_summary="全平台历史训练候选样本。",
                quality_score=0.9,
                created_at=now,
                updated_at=now,
            ),
        ])
        await session.flush()
        for idx in range(6):
            session.add(LearningArtifact(
                id=f"la-full-history-candidate-{idx}",
                event_id="le-full-history-candidate",
                artifact_kind="eval_case" if idx == 5 else "training_sample",
                artifact_hash=f"la-full-history-candidate-{idx}",
                target_type="skill",
                target_id=f"source-skill-{idx % 2}",
                department="AI小组",
                skill_id=f"source-skill-{idx % 2}",
                title=f"全历史训练样本 {idx}",
                summary="用于验证全平台历史输入、过程、输出会生成训练候选。",
                content_json={
                    "input": {"question": f"平台历史输入 {idx}"},
                    "output": {"summary": f"平台历史输出 {idx}"},
                    "formed_data": {"trace": idx},
                    "source_type": "execution_trace",
                },
                labels_json=["training"],
                quality_score=0.8 + idx / 100,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
                created_at=now + timedelta(minutes=idx),
                updated_at=now + timedelta(minutes=idx),
            ))

        result = await ensure_full_history_platform_training_candidate(
            session,
            user,
            min_sample_count=4,
            target_skill_id=target_skill_id,
            parent_deployment_id=parent_deployment_id,
        )
        await session.commit()

    assert result["created"]
    created = result["created"][0]
    assert created["training_mode"] == "full_history_incremental"
    assert created["sample_total"] == 6
    assert created["eval_count"] == 1
    assert created["parent_model_deployment_id"] == parent_deployment_id
    assert created["inline_limit"] == 6
    assert created["inline_eval_limit"] == 4

    async with db_mod.async_session_factory() as session:
        job = (
            await session.execute(
                select(TrainingJob).where(TrainingJob.id == created["training_job_id"])
            )
        ).scalar_one()

    assert job.status == "awaiting_review"
    assert job.created_by == "full-history-admin"
    spec = job.spec_json or {}
    assert spec["training_mode"] == "full_history_incremental"
    assert spec["dataset"]["selector"]["type"] == "learning_artifacts"
    assert spec["dataset"]["selector"]["scope"] == "platform"
    assert spec["dataset"]["selector"]["limit"] == 6
    assert spec["dataset"]["selector"]["inline_limit"] == 6
    assert spec["dataset"]["selector"]["inline_eval_limit"] == 4
    assert "learning_artifact_ids" not in spec["dataset"]
    assert spec["parent_model"]["model_deployment_id"] == parent_deployment_id
    assert spec["parent_model"]["artifact_uri"] == "file:///tmp/full-history-parent-adapter.tar.gz"
    assert spec["parent_model"]["artifact_uri_source"] == "source_uri"
    assert spec["governance"]["auto_training_agent"] == "learning_training_reviewer"
    assert spec["governance"]["full_platform_history"] is True
    assert spec["governance"]["deployment_review_required"] is False
    assert spec["governance"]["auto_deployment_mode"] == "active_after_eval_readiness"
    assert spec["model"]["profile"] == "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
    assert spec["deployment"]["auto_active"] is True
    assert spec["deployment"]["rollout_percent"] == 100
    assert spec["deployment"]["deployment_target_gateway_id"] == "inference-primary"
    assert spec["deployment"]["deployment_runtime_profile"] == "mlx-qwen3.6-35b-a3b-lora"


@pytest.mark.asyncio
async def test_learning_full_history_training_candidate_uses_latest_parent_by_default(client):
    import app.database as db_mod
    from app.auth.models import User
    from app.learning.models import LearningArtifact, LearningEvent
    from app.learning.service import ensure_full_history_platform_training_candidate
    from app.training.models import TrainingJob, TrainingModelDeployment
    from sqlalchemy import func, select

    now = now_bjt()
    target_skill_id = "full-history-latest-parent-skill"
    async with db_mod.async_session_factory() as session:
        user = User(
            id="full-history-latest-admin",
            username="full-history-latest-admin",
            name="全历史最新父模型管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
            state="active",
        )
        target_skill = Skill(
            id=target_skill_id,
            name="全历史最新父模型 Skill",
            display_name="全历史最新父模型 Skill",
            department="AI小组",
            status="active",
            visibility="company",
            approval_level=0,
        )
        session.add_all([
            user,
            target_skill,
            TrainingModelDeployment(
                id="deploy_full_history_old_parent",
                job_id="train_full_history_old_parent",
                department="AI小组",
                model_family="full-history-latest-parent-skill:learning-loop-adapter",
                artifact_id="artifact-full-history-old-parent",
                artifact_ref_json={"uri": "file:///tmp/full-history-old-parent.tar.gz", "sha256": "a" * 64},
                target_skill_ids_json=[target_skill_id],
                status="canary",
                rollout_percent=10,
                requested_by="admin",
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=2),
            ),
            TrainingModelDeployment(
                id="deploy_full_history_latest_parent",
                job_id="train_full_history_latest_parent",
                department="AI小组",
                model_family="full-history-latest-parent-skill:platform-full-history-adapter",
                artifact_id="artifact-full-history-latest-parent",
                artifact_ref_json={"uri": "file:///tmp/full-history-latest-parent.tar.gz", "sha256": "b" * 64},
                target_skill_ids_json=[target_skill_id],
                status="canary",
                rollout_percent=10,
                requested_by="admin",
                created_at=now,
                updated_at=now,
            ),
            LearningEvent(
                id="le-full-history-latest-parent",
                event_type="decision.feedback",
                source_type="decision_log",
                source_id="decision-full-history-latest-parent",
                source_hash="decision-full-history-latest-parent",
                department="AI小组",
                redacted_summary="全平台历史最新父模型训练候选样本。",
                quality_score=0.9,
                created_at=now,
                updated_at=now,
            ),
        ])
        await session.flush()
        for idx in range(8):
            session.add(LearningArtifact(
                id=f"la-full-history-latest-parent-{idx}",
                event_id="le-full-history-latest-parent",
                artifact_kind="eval_case" if idx in {6, 7} else "training_sample",
                artifact_hash=f"la-full-history-latest-parent-{idx}",
                target_type="skill",
                target_id=f"source-latest-parent-{idx % 2}",
                department="AI小组",
                skill_id=f"source-latest-parent-{idx % 2}",
                title=f"全历史最新父模型样本 {idx}",
                summary="用于验证默认选择最新训练部署作为父模型。",
                content_json={
                    "input": {"question": f"平台历史输入 {idx}"},
                    "output": {"summary": f"平台历史输出 {idx}"},
                    "expected_signal": "保留评测样本。",
                    "formed_data": {"trace": idx},
                    "source_type": "execution_trace",
                },
                labels_json=["training"],
                quality_score=0.8 + idx / 100,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
                created_at=now + timedelta(minutes=idx),
                updated_at=now + timedelta(minutes=idx),
            ))

        result = await ensure_full_history_platform_training_candidate(
            session,
            user,
            min_sample_count=4,
            target_skill_id=target_skill_id,
        )
        await session.commit()

    assert result["created"]
    created = result["created"][0]
    assert created["parent_model_deployment_id"] == "deploy_full_history_latest_parent"
    assert created["inline_eval_limit"] == 4

    async with db_mod.async_session_factory() as session:
        job = (
            await session.execute(
                select(TrainingJob).where(TrainingJob.id == created["training_job_id"])
            )
        ).scalar_one()

    spec = job.spec_json or {}
    assert spec["parent_model"]["model_deployment_id"] == "deploy_full_history_latest_parent"
    assert spec["lineage"]["parent_model_selection"] == "latest_active_or_canary"
    assert spec["dataset"]["selector"]["inline_eval_limit"] == 4


@pytest.mark.asyncio
async def test_system_full_training_flow_captures_all_sources_and_advances_training(client, monkeypatch):
    import app.database as db_mod
    from app.auth.models import User
    from app.execution.models import OpenClawInstance
    from app.learning.models import LearningArtifact
    from app.learning.service import (
        LEARNING_FULL_HISTORY_PARENT_DEPLOYMENT_ID,
        LEARNING_FULL_HISTORY_TARGET_SKILL_ID,
        run_system_full_training_flow,
    )
    from app.training.models import TrainingJob, TrainingModelDeployment
    from sqlalchemy import func, select

    calls: list[tuple[str, str]] = []

    async def fake_approve_training_job(db, user, job_id):
        calls.append(("approve_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "queued"
        await db.flush()
        return {"id": job_id, "status": "queued", "target_gateway_id": job.target_gateway_id}

    async def fake_dispatch_training_job(db, user, job_id):
        calls.append(("dispatch_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "running"
        await db.flush()
        return {"id": job_id, "status": "running", "target_gateway_id": job.target_gateway_id}

    async def fake_get_training_gateway_model_status(db, user, gateway_id, payload):
        return {
            "model": {
                "status": "ready",
                "profile": payload["profile"],
                "exists": True,
                "model_dir": "/models/qwen3.6",
            }
        }

    monkeypatch.setattr("app.training.service.approve_training_job", fake_approve_training_job)
    monkeypatch.setattr("app.training.service.dispatch_training_job", fake_dispatch_training_job)
    monkeypatch.setattr("app.training.service.get_training_gateway_model_status", fake_get_training_gateway_model_status)
    monkeypatch.setattr("app.aiclaw.bridge_registry.bridge_registry.is_online", lambda instance_id: instance_id == "training-primary")
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_ENABLED", True)
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_DISPATCH_ENABLED", True)
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_MIN_SAMPLES", 1)

    now = now_bjt()
    async with db_mod.async_session_factory() as session:
        user = User(
            id="system-full-training-admin",
            username="system-full-training-admin",
            name="系统全量训练管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
            state="active",
        )
        source_skill = Skill(
            id="system-full-training-source",
            name="系统全量训练源 Skill",
            display_name="系统全量训练源 Skill",
            department="AI小组",
            status="active",
            visibility="company",
            approval_level=0,
        )
        target_skill = Skill(
            id=LEARNING_FULL_HISTORY_TARGET_SKILL_ID,
            name="系统全量训练目标 Skill",
            display_name="系统全量训练目标 Skill",
            department="AI小组",
            status="active",
            visibility="company",
            approval_level=0,
        )
        parent = TrainingModelDeployment(
            id=LEARNING_FULL_HISTORY_PARENT_DEPLOYMENT_ID,
            job_id="train_system_full_parent",
            department="AI小组",
            model_family=f"{LEARNING_FULL_HISTORY_TARGET_SKILL_ID}:platform-full-history-adapter",
            artifact_id="artifact-system-full-parent",
            artifact_ref_json={"uri": "file:///tmp/system-full-parent.tar.gz", "sha256": "c" * 64},
            target_skill_ids_json=[LEARNING_FULL_HISTORY_TARGET_SKILL_ID],
            status="canary",
            rollout_percent=10,
            requested_by="admin",
            created_at=now - timedelta(hours=1),
            updated_at=now - timedelta(hours=1),
        )
        gateway = OpenClawInstance(
            id="training-primary",
            name="GB10 237 · NVIDIA GB10",
            department="AI小组",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["lora", "qlora", "eval"],
                    "gpu_count": 1,
                    "worker_count": 1,
                },
            }),
            is_active=True,
        )
        run = ExecutionRun(
            id="run-system-full-training",
            skill_id=source_skill.id,
            trigger_type="scheduled",
            run_mode="scheduled_real",
            status="completed",
            total_steps=1,
            completed_steps=1,
            summary="系统全量训练需要沉淀执行输入、过程、输出。",
            started_at=now - timedelta(minutes=10),
            completed_at=now - timedelta(minutes=9),
        )
        step = ExecutionStep(
            run_id=run.id,
            skill_id=source_skill.id,
            step_order=1,
            status="completed",
            input_data={"question": "全量训练输入"},
            output_data={"summary": "全量训练输出", "actions": ["继续训练"]},
            started_at=now - timedelta(minutes=10),
            completed_at=now - timedelta(minutes=9),
            duration_ms=320,
        )
        session.add_all([user, source_skill, target_skill, parent, gateway, run, step])
        await session.flush()

        result = await run_system_full_training_flow(
            session,
            user,
            days=3650,
            per_source_limit=50,
            min_sample_count=1,
            advance=True,
            max_jobs=1,
            capture_sources=True,
        )
        await session.commit()

    assert result["captured"]["captured"]["execution_run"] >= 1
    assert result["captured"]["total_captured"] >= 1
    assert result["training_candidate"]["created"]
    created = result["training_candidate"]["created"][0]
    assert created["training_mode"] == "full_history_incremental"
    assert created["sample_total"] >= 1
    assert calls == [
        ("approve_job", created["training_job_id"]),
        ("dispatch_job", created["training_job_id"]),
    ]

    async with db_mod.async_session_factory() as session:
        job = (
            await session.execute(
                select(TrainingJob).where(TrainingJob.id == created["training_job_id"])
            )
        ).scalar_one()
        sample_count = int((
            await session.execute(
                select(func.count(LearningArtifact.id)).where(
                    LearningArtifact.artifact_kind == "training_sample",
                    LearningArtifact.status == "materialized",
                    LearningArtifact.sink_type == "training_sample",
                )
            )
        ).scalar() or 0)

    assert sample_count >= 1
    assert job.status == "running"
    assert job.spec_json["training_mode"] == "full_history_incremental"
    assert job.spec_json["dataset"]["selector"]["scope"] == "platform"
    assert job.spec_json["governance"]["full_platform_history"] is True


@pytest.mark.asyncio
async def test_learning_full_history_training_evaluation_report_syncs_to_inbox(client):
    import app.database as db_mod
    from app.inbox.models import InboxReportCard
    from app.learning.service import publish_training_evaluation_report_to_inbox
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment
    from sqlalchemy import select

    now = now_bjt()
    async with db_mod.async_session_factory() as session:
        job = TrainingJob(
            id="train_full_history_report",
            title="全平台历史训练报告",
            department="AI小组",
            created_by="learning_auto_flow",
            status="completed",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="full-history-report-skill",
            dataset_ref="learning-artifacts://platform/training/full-history/test",
            spec_json={
                "source": "learning_sample_threshold",
                "training_mode": "full_history_incremental",
                "parent_model": {"model_deployment_id": "deploy-parent-report"},
                "dataset": {
                    "sample_total": 42,
                    "eval_count": 6,
                    "window": {"mode": "full_history"},
                },
                "governance": {
                    "auto_training_agent": "learning_training_reviewer",
                    "incremental_training": True,
                    "raw_payload_returned": False,
                },
            },
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        session.add(TrainingJobTask(
            job_id=job.id,
            gateway_id="node-report",
            status="completed",
            progress=100,
            metrics_json={
                "op": "training.evaluate",
                "metrics": {
                    "train_samples": 42,
                    "eval_samples": 6,
                    "eval_evaluated_samples": 6,
                    "eval_mode": "holdout_generation_smoke",
                    "train_runtime_seconds": 12,
                    "win_rate": 0.68,
                    "tokens_per_second": 18.5,
                },
                "checks": [
                    {"name": "win_rate", "passed": True, "actual": 0.68, "operator": ">=", "expected": 0.55}
                ],
            },
            created_at=now,
            updated_at=now,
        ))
        deployment = TrainingModelDeployment(
            id="deploy_full_history_report",
            job_id=job.id,
            department="AI小组",
            model_family="full-history-report-skill:platform-full-history-adapter",
            artifact_id="artifact-full-history-report",
            artifact_ref_json={"sha256": "a" * 64, "uri": "file:///tmp/full-history-report.tar.gz"},
            target_skill_ids_json=["full-history-report-skill"],
            status="canary",
            rollout_percent=10,
            requested_by="learning_training_reviewer",
            created_at=now,
            updated_at=now,
        )
        session.add(deployment)
        await session.flush()

        result = await publish_training_evaluation_report_to_inbox(session, job, deployment=deployment)
        await session.commit()

    assert result["published"] is True
    async with db_mod.async_session_factory() as session:
        card = (
            await session.execute(
                select(InboxReportCard).where(InboxReportCard.decision_log_id == result["decision_log_id"])
            )
        ).scalar_one()

    assert card.title == "全平台历史模型训练评估报告"
    assert card.channel == "model_training_evaluation"
    assert card.skill_id == "full-history-report-skill"
    assert "候选样本 42 条" in card.summary
    assert "实际内联 48 条" in card.summary
    assert "验证状态=离线冒烟通过" in card.summary
    assert "全平台历史" in (card.tags or [])


@pytest.mark.asyncio
async def test_learning_training_automation_advances_via_training_state_machines(client, monkeypatch):
    import app.database as db_mod
    from app.auth.models import User
    from app.execution.models import OpenClawInstance
    from app.learning.service import advance_learning_training_automation
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

    calls: list[tuple[str, str]] = []

    async def fake_approve_training_job(db, user, job_id):
        calls.append(("approve_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "queued"
        job.target_gateway_id = "learn-auto-gateway"
        await db.flush()
        return {"id": job_id, "status": "queued", "target_gateway_id": "learn-auto-gateway"}

    async def fake_dispatch_training_job(db, user, job_id):
        calls.append(("dispatch_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "running"
        await db.flush()
        return {"id": job_id, "status": "running", "target_gateway_id": job.target_gateway_id}

    async def fake_retry_training_job(db, user, job_id):
        calls.append(("retry_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "running"
        job.failure_stage = None
        await db.flush()
        return {"id": job_id, "status": "running", "target_gateway_id": job.target_gateway_id}

    async def fake_collect_training_job_result(db, user, job_id):
        calls.append(("collect_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "completed"
        await db.flush()
        return {"id": job_id, "status": "completed", "updated_at": now_bjt().isoformat()}

    async def fake_evaluate_training_job(db, user, job_id):
        calls.append(("evaluate_job", job_id))
        return {"id": job_id, "status": "completed", "latest_deployment": {"id": "md-learning-auto-advance"}}

    async def fake_approve_training_deployment(db, user, deployment_id):
        calls.append(("approve_deployment", deployment_id))
        deployment = await db.get(TrainingModelDeployment, deployment_id)
        deployment.status = "canary"
        await db.flush()
        return {"deployment": {"id": deployment_id, "status": "canary", "rollout_percent": deployment.rollout_percent}}

    monkeypatch.setattr("app.training.service.approve_training_job", fake_approve_training_job)
    monkeypatch.setattr("app.training.service.dispatch_training_job", fake_dispatch_training_job)
    monkeypatch.setattr("app.training.service.retry_training_job", fake_retry_training_job)
    monkeypatch.setattr("app.training.service.collect_training_job_result", fake_collect_training_job_result)
    monkeypatch.setattr("app.training.service.evaluate_training_job", fake_evaluate_training_job)
    monkeypatch.setattr("app.training.service.approve_training_deployment", fake_approve_training_deployment)
    monkeypatch.setattr("app.aiclaw.bridge_registry.bridge_registry.is_online", lambda instance_id: instance_id == "learn-auto-gateway")

    async with db_mod.async_session_factory() as session:
        user = User(
            id="learning-auto-advance-admin",
            username="learning-auto-advance-admin",
            name="学习闭环自动推进管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
        )
        gateway = OpenClawInstance(
            id="learn-auto-gateway",
            name="学习自动训练网关",
            department="AI小组",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["lora", "qlora", "eval"],
                    "gpu_count": 1,
                    "worker_count": 3,
                },
            }),
            is_active=True,
        )
        job = TrainingJob(
            id="tj-learning-auto-advance",
            title="学习闭环自动推进任务",
            department="AI小组",
            created_by="learning_auto_flow",
            status="awaiting_review",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="learning-auto-advance-skill",
            dataset_ref="learning-artifacts://learning-auto-advance-skill/training/yesterday/2026-01-01",
            spec_json={
                "source": "learning_sample_threshold",
                "training_mode": "daily_incremental",
                "dataset": {
                    "manifest_hash": "manifest-learning-auto-advance",
                    "sample_total": 30,
                    "eval_count": 3,
                    "avg_quality": 0.9,
                    "window": {
                        "mode": "yesterday",
                        "date": (now_bjt() - timedelta(days=1)).date().isoformat(),
                    },
                },
                "lineage": {"source": "learning_artifacts"},
                "governance": {
                    "auto_created": True,
                    "raw_payload_returned": False,
                    "auto_training_agent": "learning_training_reviewer",
                    "incremental_training": True,
                },
            },
        )
        old_job = TrainingJob(
            id="tj-learning-auto-advance-old-shape",
            title="旧形态学习闭环候选",
            department="AI小组",
            created_by="learning_auto_flow",
            status="awaiting_review",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="learning-auto-advance-skill-old",
            dataset_ref="learning-artifacts://learning-auto-advance-skill-old/training/latest",
            spec_json={
                "source": "learning_sample_threshold",
                "dataset": {"manifest_hash": "manifest-old"},
                "lineage": {"source": "learning_artifacts"},
                "governance": {"auto_created": True},
            },
        )
        old_noise_jobs = [
            TrainingJob(
                id=f"tj-learning-auto-noise-{idx}",
                title=f"旧形态学习闭环噪声 {idx}",
                department="AI小组",
                created_by="learning_auto_flow",
                status="awaiting_review",
                job_type="lora",
                training_strategy="learning_sample_threshold",
                target_skill_id=f"learning-auto-noise-skill-{idx}",
                dataset_ref=f"learning-artifacts://learning-auto-noise-skill-{idx}/training/latest",
                spec_json={
                    "source": "learning_sample_threshold",
                    "dataset": {"manifest_hash": f"manifest-noise-{idx}"},
                    "lineage": {"source": "learning_artifacts"},
                    "governance": {"auto_created": True},
                },
            )
            for idx in range(15)
        ]
        older_noise_jobs = [
            TrainingJob(
                id=f"tj-learning-auto-older-noise-{idx:03d}",
                title=f"更早旧形态学习闭环噪声 {idx}",
                department="AI小组",
                created_by="learning_auto_flow",
                status="awaiting_review",
                job_type="lora",
                training_strategy="learning_sample_threshold",
                target_skill_id=f"learning-auto-older-noise-skill-{idx}",
                dataset_ref=f"learning-artifacts://learning-auto-older-noise-skill-{idx}/training/latest",
                spec_json={
                    "source": "learning_sample_threshold",
                    "dataset": {"manifest_hash": f"manifest-older-noise-{idx}"},
                    "lineage": {"source": "learning_artifacts"},
                    "governance": {"auto_created": True},
                },
                created_at=now_bjt() - timedelta(days=20, minutes=idx),
                updated_at=now_bjt() - timedelta(days=20, minutes=idx),
            )
            for idx in range(120)
        ]
        retry_job = TrainingJob(
            id="tj-learning-auto-advance-retry",
            title="学习闭环自动重试任务",
            department="AI小组",
            created_by="learning_auto_flow",
            status="queued",
            failure_stage="dispatch",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="learning-auto-retry-skill",
            target_gateway_id="learn-auto-gateway",
            dataset_ref="learning-artifacts://learning-auto-retry-skill/training/yesterday/2026-01-01",
            spec_json={
                "source": "learning_sample_threshold",
                "training_mode": "daily_incremental",
                "dataset": {
                    "manifest_hash": "manifest-learning-auto-retry",
                    "sample_total": 30,
                    "eval_count": 3,
                    "avg_quality": 0.88,
                    "window": {
                        "mode": "yesterday",
                        "date": (now_bjt() - timedelta(days=1)).date().isoformat(),
                    },
                },
                "lineage": {"source": "learning_artifacts"},
                "governance": {
                    "auto_created": True,
                    "raw_payload_returned": False,
                    "auto_training_agent": "learning_training_reviewer",
                    "incremental_training": True,
                },
                "parent_model": {"artifact_uri": "artifact://previous", "artifact_sha256": "b" * 64},
            },
        )
        recovery_job = TrainingJob(
            id="tj-learning-auto-dispatch-recovered",
            title="历史 dispatch 失败恢复任务",
            department="AI小组",
            created_by="learning_auto_flow",
            status="queued",
            failure_stage="dispatch",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="learning-auto-dispatch-recovered-skill",
            target_gateway_id="learn-auto-gateway",
            dataset_ref="learning-artifacts://learning-auto-dispatch-recovered-skill/training/yesterday/2026-01-01",
            spec_json={
                "source": "learning_sample_threshold",
                "training_mode": "daily_incremental",
                "dataset": {
                    "manifest_hash": "manifest-learning-auto-dispatch-recovered",
                    "sample_total": 30,
                    "eval_count": 3,
                    "avg_quality": 0.87,
                    "window": {
                        "mode": "yesterday",
                        "date": (now_bjt() - timedelta(days=3)).date().isoformat(),
                    },
                },
                "lineage": {"source": "learning_artifacts"},
                "governance": {
                    "auto_created": True,
                    "raw_payload_returned": False,
                    "auto_training_agent": "learning_training_reviewer",
                    "incremental_training": True,
                },
            },
            created_at=now_bjt() - timedelta(days=3),
            updated_at=now_bjt() - timedelta(minutes=15),
        )
        deployment = TrainingModelDeployment(
            id="md-learning-auto-advance",
            job_id=job.id,
            department="AI小组",
            model_family="learning-auto-advance-skill:learning-loop-adapter",
            artifact_id="artifact-learning-auto-advance",
            artifact_ref_json={"id": "artifact-learning-auto-advance", "uri": "artifact://adapter", "sha256": "a" * 64},
            target_skill_ids_json=["learning-auto-advance-skill"],
            status="awaiting_review",
            rollout_percent=10,
            requested_by="learning_auto_flow",
        )
        evaluation_task = TrainingJobTask(
            job_id=job.id,
            gateway_id="learn-auto-gateway",
            status="completed",
            progress=1,
            metrics_json={
                "op": "training.evaluate",
                "passed": True,
                "metrics": {
                    "eval_samples": 3,
                    "eval_evaluated_samples": 3,
                    "win_rate": 0.67,
                },
            },
        )
        session.add_all([user, gateway, job, old_job, retry_job, recovery_job, deployment, evaluation_task, *old_noise_jobs, *older_noise_jobs])
        await session.flush()

        result = await advance_learning_training_automation(session, user, days=7, max_jobs=3)
        await session.commit()

    assert result["enabled"] is True
    assert result["approved_jobs"][0]["job_id"] == "tj-learning-auto-advance"
    assert result["approved_jobs"][0]["approved_by"] == "learning_training_reviewer"
    assert result["dispatched_jobs"][0]["job_id"] == "tj-learning-auto-advance"
    assert result["dispatched_jobs"][0]["dispatched_by"] == "learning_training_reviewer"
    assert result["collected"]["items"][0]["job_id"] == "tj-learning-auto-advance"
    assert result["approved_deployments"][0]["deployment_id"] == "md-learning-auto-advance"
    assert result["approved_deployments"][0]["approved_by"] == "learning_training_reviewer"
    assert ("approve_job", "tj-learning-auto-advance") in calls
    assert ("dispatch_job", "tj-learning-auto-advance") in calls
    assert ("retry_job", "tj-learning-auto-advance-retry") in calls
    assert any(item["job_id"] == "tj-learning-auto-advance-retry" and item["retry"] is True for item in result["dispatched_jobs"])
    assert ("retry_job", "tj-learning-auto-dispatch-recovered") in calls
    assert any(item["job_id"] == "tj-learning-auto-dispatch-recovered" for item in result["retried_dispatch_jobs"])
    assert ("approve_job", "tj-learning-auto-advance-old-shape") not in calls
    assert all(("approve_job", f"tj-learning-auto-noise-{idx}") not in calls for idx in range(15))
    assert all(("approve_job", f"tj-learning-auto-older-noise-{idx:03d}") not in calls for idx in range(120))
    assert ("collect_job", "tj-learning-auto-advance") in calls
    assert ("evaluate_job", "tj-learning-auto-advance") in calls
    assert ("approve_deployment", "md-learning-auto-advance") in calls
    assert any(item["reason"] == "not_incremental_candidate" for item in result["skipped"])


@pytest.mark.asyncio
async def test_learning_training_automation_prefers_default_gb10_mac_route(client, monkeypatch):
    import app.database as db_mod
    from app.auth.models import User
    from app.execution.models import OpenClawInstance
    from app.learning.service import advance_learning_training_automation
    from app.training.models import TrainingJob

    calls: list[tuple[str, str]] = []

    async def fake_approve_training_job(db, user, job_id):
        calls.append(("approve_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "queued"
        job.target_gateway_id = "training-primary"
        await db.flush()
        return {"id": job_id, "status": "queued", "target_gateway_id": "training-primary"}

    async def fake_dispatch_training_job(db, user, job_id):
        calls.append(("dispatch_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "running"
        await db.flush()
        return {"id": job_id, "status": "running", "target_gateway_id": job.target_gateway_id}

    async def fake_get_training_gateway_model_status(db, user, gateway_id, payload):
        calls.append(("model_status", gateway_id))
        return {
            "model": {
                "status": "ready",
                "profile": payload["profile"],
                "exists": True,
                "model_dir": "/models/qwen3.6",
            },
        }

    monkeypatch.setattr("app.training.service.approve_training_job", fake_approve_training_job)
    monkeypatch.setattr("app.training.service.dispatch_training_job", fake_dispatch_training_job)
    monkeypatch.setattr("app.training.service.get_training_gateway_model_status", fake_get_training_gateway_model_status)
    monkeypatch.setattr("app.aiclaw.bridge_registry.bridge_registry.is_online", lambda instance_id: instance_id == "training-primary")

    yesterday = (now_bjt() - timedelta(days=1)).date().isoformat()

    def candidate(
        job_id: str,
        *,
        default_route: bool,
        avg_quality: float,
        status: str = "awaiting_review",
        target_gateway_id: str = "training-primary",
        training_mode: str = "daily_incremental",
        sample_total: int = 30,
    ) -> TrainingJob:
        window = (
            {"mode": "full_history"}
            if training_mode == "full_history_incremental"
            else {"mode": "yesterday", "date": yesterday}
        )
        spec = {
            "source": "learning_sample_threshold",
            "training_mode": training_mode,
            "dataset": {
                "manifest_hash": f"manifest-{job_id}",
                "sample_total": sample_total,
                "eval_count": 3,
                "avg_quality": avg_quality,
                "window": window,
            },
            "lineage": {"source": "learning_artifacts"},
            "governance": {
                "auto_created": True,
                "raw_payload_returned": False,
                "auto_training_agent": "learning_training_reviewer",
                "incremental_training": True,
            },
        }
        if default_route:
            spec["model"] = {
                "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
                "source": "internal",
            }
            spec["deployment"] = {
                "auto_request": True,
                "auto_active": True,
                "deployment_target_gateway_id": "inference-primary",
                "deployment_runtime_profile": "mlx-qwen3.6-35b-a3b-lora",
            }
        return TrainingJob(
            id=job_id,
            title=f"默认路线优先 {job_id}",
            department="AI小组",
            created_by="learning_auto_flow",
            status=status,
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="learning-default-route-skill",
            target_gateway_id=target_gateway_id,
            dataset_ref=f"learning-artifacts://learning-default-route-skill/training/{job_id}",
            spec_json=spec,
        )

    async with db_mod.async_session_factory() as session:
        user = User(
            id="learning-default-route-admin",
            username="learning-default-route-admin",
            name="学习闭环默认路线管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
            state="active",
        )
        gateway = OpenClawInstance(
            id="training-primary",
            name="GB10 237 · NVIDIA GB10",
            department="AI小组",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["lora", "qlora", "eval"],
                    "gpu_count": 1,
                    "worker_count": 1,
                },
            }),
            is_active=True,
        )
        session.add_all([
            user,
            gateway,
            candidate("tj-learning-default-route-legacy", default_route=False, avg_quality=0.99),
            candidate(
                "tj-learning-default-route-legacy-queued",
                default_route=False,
                avg_quality=0.98,
                status="queued",
                target_gateway_id="内容电商",
            ),
            candidate("tj-learning-default-route-daily-high-quality", default_route=True, avg_quality=0.99),
            candidate(
                "tj-learning-default-route-full-history-old",
                default_route=True,
                avg_quality=0.99,
                training_mode="full_history_incremental",
                sample_total=30,
            ),
            candidate(
                "tj-learning-default-route-new",
                default_route=True,
                avg_quality=0.7,
                training_mode="full_history_incremental",
                sample_total=300,
            ),
        ])
        await session.flush()

        result = await advance_learning_training_automation(session, user, days=7, max_jobs=1)
        await session.commit()

    assert result["approved_jobs"][0]["job_id"] == "tj-learning-default-route-new"
    assert result["dispatched_jobs"][0]["job_id"] == "tj-learning-default-route-new"
    assert ("approve_job", "tj-learning-default-route-new") in calls
    assert ("model_status", "training-primary") in calls
    assert ("approve_job", "tj-learning-default-route-legacy") not in calls
    assert ("approve_job", "tj-learning-default-route-daily-high-quality") not in calls
    assert ("approve_job", "tj-learning-default-route-full-history-old") not in calls
    assert ("dispatch_job", "tj-learning-default-route-legacy-queued") not in calls
    assert any(
        item.get("job_id") == "tj-learning-default-route-legacy-queued"
        and item.get("reason") == "legacy_candidate_superseded_by_default_route"
        for item in result["skipped"]
    )


@pytest.mark.asyncio
async def test_learning_training_automation_skips_dispatch_when_default_model_missing(client, monkeypatch):
    import app.database as db_mod
    from app.auth.models import User
    from app.execution.models import OpenClawInstance
    from app.learning.service import advance_learning_training_automation
    from app.training.models import TrainingJob

    calls: list[tuple[str, str]] = []

    async def fake_approve_training_job(db, user, job_id):
        calls.append(("approve_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "queued"
        job.target_gateway_id = "training-primary"
        await db.flush()
        return {"id": job_id, "status": "queued", "target_gateway_id": "training-primary"}

    async def fake_dispatch_training_job(db, user, job_id):
        calls.append(("dispatch_job", job_id))
        return {"id": job_id, "status": "running", "target_gateway_id": "training-primary"}

    async def fake_get_training_gateway_model_status(db, user, gateway_id, payload):
        calls.append(("model_status", gateway_id))
        return {
            "model": {
                "status": "failed",
                "profile": payload["profile"],
                "exists": False,
                "model_dir": "/home/skillforge/.skillforge_bridge/89cd6683/training_models/qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive/model",
                "error": "internal model path does not exist",
            },
        }

    async def fake_prepare_training_gateway_model(db, user, gateway_id, payload):
        calls.append(("prepare_model", str(payload.get("auto_discover"))))
        return {
            "model": {
                "status": "failed",
                "exists": False,
                "source_exists": False,
                "error": "internal model path does not exist",
                "scan_summary": {"candidate_count": 0, "existing_roots": 0},
            },
        }

    monkeypatch.setattr("app.training.service.approve_training_job", fake_approve_training_job)
    monkeypatch.setattr("app.training.service.dispatch_training_job", fake_dispatch_training_job)
    monkeypatch.setattr("app.training.service.get_training_gateway_model_status", fake_get_training_gateway_model_status)
    monkeypatch.setattr("app.training.service.prepare_training_gateway_model", fake_prepare_training_gateway_model)
    monkeypatch.setattr("app.aiclaw.bridge_registry.bridge_registry.is_online", lambda instance_id: instance_id == "training-primary")

    yesterday = (now_bjt() - timedelta(days=1)).date().isoformat()
    async with db_mod.async_session_factory() as session:
        user = User(
            id="learning-missing-model-admin",
            username="learning-missing-model-admin",
            name="学习闭环缺模型管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
            state="active",
        )
        gateway = OpenClawInstance(
            id="training-primary",
            name="GB10 237 · NVIDIA GB10",
            department="AI小组",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["lora", "qlora", "eval"],
                    "gpu_count": 1,
                    "worker_count": 1,
                },
            }),
            is_active=True,
        )
        job = TrainingJob(
            id="tj-learning-default-route-model-missing",
            title="默认路线模型缺失",
            department="AI小组",
            created_by="learning_auto_flow",
            status="awaiting_review",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="learning-default-route-model-missing-skill",
            target_gateway_id="training-primary",
            dataset_ref="learning-artifacts://learning-default-route-model-missing-skill/training/yesterday",
            spec_json={
                "source": "learning_sample_threshold",
                "training_mode": "daily_incremental",
                "model": {
                    "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
                    "source": "internal",
                },
                "deployment": {
                    "auto_request": True,
                    "auto_active": True,
                    "deployment_target_gateway_id": "inference-primary",
                    "deployment_runtime_profile": "mlx-qwen3.6-35b-a3b-lora",
                },
                "dataset": {
                    "manifest_hash": "manifest-learning-default-route-model-missing",
                    "sample_total": 30,
                    "eval_count": 3,
                    "avg_quality": 0.88,
                    "window": {"mode": "yesterday", "date": yesterday},
                },
                "lineage": {"source": "learning_artifacts"},
                "governance": {
                    "auto_created": True,
                    "raw_payload_returned": False,
                    "auto_training_agent": "learning_training_reviewer",
                    "incremental_training": True,
                },
            },
        )
        session.add_all([user, gateway, job])
        await session.flush()

        result = await advance_learning_training_automation(session, user, days=7, max_jobs=1)
        stored = await session.get(TrainingJob, job.id)
        await session.commit()

    assert result["approved_jobs"][0]["job_id"] == "tj-learning-default-route-model-missing"
    assert result["dispatched_jobs"] == []
    assert ("model_status", "training-primary") in calls
    assert ("prepare_model", "True") in calls
    assert ("dispatch_job", "tj-learning-default-route-model-missing") not in calls
    assert result["skipped_dispatch_jobs"][0]["reason"] == "training_model_not_ready"
    assert result["skipped_dispatch_jobs"][0]["model_status"]["exists"] is False
    assert result["skipped_dispatch_jobs"][0]["model_status"]["auto_discover_prepare_status"]["error"] == "internal model path does not exist"
    assert stored.status == "queued"
    assert stored.failure_stage == "model_preflight"


@pytest.mark.asyncio
async def test_learning_training_automation_configures_discovered_model_candidate(client, monkeypatch):
    import app.database as db_mod
    from app.auth.models import User
    from app.execution.models import OpenClawInstance
    from app.learning.service import advance_learning_training_automation
    from app.training.models import TrainingJob

    candidate_path = "/mnt/models/qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive/snapshots/main"
    calls: list[tuple[str, str]] = []

    async def fake_approve_training_job(db, user, job_id):
        calls.append(("approve_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "queued"
        await db.flush()
        return {"id": job_id, "status": "queued", "target_gateway_id": job.target_gateway_id}

    async def fake_get_training_gateway_model_status(db, user, gateway_id, payload):
        calls.append(("model_status", gateway_id))
        return {
            "model": {
                "status": "failed",
                "profile": payload["profile"],
                "exists": False,
                "source_exists": False,
                "source_error": "internal model path does not exist",
                "model_dir": "/home/skillforge/.skillforge_bridge/89cd6683/training_models/qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive/model",
            },
        }

    async def fake_discover_training_models_across_gateways(db, user, payload):
        calls.append(("discover_all", payload["training_gateway_id"]))
        return {
            "training_gateway_id": payload["training_gateway_id"],
            "summary": {"train_ready_count": 1, "candidate_count": 1},
            "train_ready_candidates": [
                {
                    "path": candidate_path,
                    "gateway_id": "training-primary",
                    "source_gateway_id": "inference-primary",
                    "train_ready": True,
                    "verified_on_training_gateway": True,
                }
            ],
            "found_but_not_train_ready": [],
        }

    async def fake_configure_training_gateway_runtime(db, user, gateway_id, payload):
        calls.append(("configure_runtime", payload["qwen36_35b_model_dir"]))
        return {
            "model": {
                "status": "not_downloaded",
                "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
                "source_exists": True,
                "source_path": candidate_path,
            },
        }

    async def fake_prepare_training_gateway_model(db, user, gateway_id, payload):
        calls.append(("prepare_model", payload["internal_model_ref"]))
        return {
            "model": {
                "status": "succeeded",
                "exists": True,
                "model_dir": "/home/skillforge/.skillforge_bridge/89cd6683/training_models/qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive/model",
                "source_path": candidate_path,
            },
        }

    async def fake_dispatch_training_job(db, user, job_id):
        calls.append(("dispatch_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "running"
        job.failure_stage = None
        await db.flush()
        return {"id": job_id, "status": "running", "target_gateway_id": job.target_gateway_id}

    monkeypatch.setattr("app.training.service.approve_training_job", fake_approve_training_job)
    monkeypatch.setattr("app.training.service.get_training_gateway_model_status", fake_get_training_gateway_model_status)
    monkeypatch.setattr("app.training.service.discover_training_models_across_gateways", fake_discover_training_models_across_gateways)
    monkeypatch.setattr("app.training.service.configure_training_gateway_runtime", fake_configure_training_gateway_runtime)
    monkeypatch.setattr("app.training.service.prepare_training_gateway_model", fake_prepare_training_gateway_model)
    monkeypatch.setattr("app.training.service.dispatch_training_job", fake_dispatch_training_job)
    monkeypatch.setattr("app.aiclaw.bridge_registry.bridge_registry.is_online", lambda instance_id: instance_id == "training-primary")

    yesterday = (now_bjt() - timedelta(days=1)).date().isoformat()
    async with db_mod.async_session_factory() as session:
        user = User(
            id="learning-candidate-model-admin",
            username="learning-candidate-model-admin",
            name="学习闭环候选模型管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
            state="active",
        )
        gateway = OpenClawInstance(
            id="training-primary",
            name="GB10 237 · NVIDIA GB10",
            department="AI小组",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                "training": {"gateway": True, "supported_tasks": ["lora", "qlora", "eval"], "gpu_count": 1},
            }),
            is_active=True,
        )
        job = TrainingJob(
            id="tj-learning-default-route-model-candidate",
            title="默认路线模型候选自动配置",
            department="AI小组",
            created_by="learning_auto_flow",
            status="awaiting_review",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="learning-default-route-model-candidate-skill",
            target_gateway_id="training-primary",
            dataset_ref="learning-artifacts://learning-default-route-model-candidate-skill/training/yesterday",
            spec_json={
                "source": "learning_sample_threshold",
                "training_mode": "daily_incremental",
                "model": {"profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive", "source": "internal"},
                "deployment": {
                    "auto_request": True,
                    "auto_active": True,
                    "deployment_target_gateway_id": "inference-primary",
                    "deployment_runtime_profile": "mlx-qwen3.6-35b-a3b-lora",
                },
                "dataset": {
                    "manifest_hash": "manifest-learning-default-route-model-candidate",
                    "sample_total": 30,
                    "eval_count": 3,
                    "avg_quality": 0.88,
                    "window": {"mode": "yesterday", "date": yesterday},
                },
                "lineage": {"source": "learning_artifacts"},
                "governance": {
                    "auto_created": True,
                    "raw_payload_returned": False,
                    "auto_training_agent": "learning_training_reviewer",
                    "incremental_training": True,
                },
            },
        )
        session.add_all([user, gateway, job])
        await session.flush()

        result = await advance_learning_training_automation(session, user, days=7, max_jobs=1)
        stored = await session.get(TrainingJob, job.id)
        await session.commit()

    assert ("discover_all", "training-primary") in calls
    assert ("configure_runtime", candidate_path) in calls
    assert ("prepare_model", candidate_path) in calls
    assert ("dispatch_job", "tj-learning-default-route-model-candidate") in calls
    assert result["dispatched_jobs"][0]["job_id"] == "tj-learning-default-route-model-candidate"
    assert result["skipped_dispatch_jobs"] == []
    assert stored.status == "running"
    assert stored.failure_stage is None


@pytest.mark.asyncio
async def test_learning_training_automation_transfers_model_candidate_from_235(client, monkeypatch):
    import app.database as db_mod
    from app.auth.models import User
    from app.execution.models import OpenClawInstance
    from app.learning.service import advance_learning_training_automation
    from app.training.models import TrainingJob

    source_path = "/srv/ai/models/hf/hub/models--Qwen--Qwen3.6-35B-A3B-FP8/snapshots/main"
    internal_ref = "/home/node/.skillforge_bridge/state/training_model_imports/qwen36/a"
    calls: list[tuple[str, str]] = []

    async def fake_approve_training_job(db, user, job_id):
        calls.append(("approve_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "queued"
        await db.flush()
        return {"id": job_id, "status": "queued", "target_gateway_id": job.target_gateway_id}

    async def fake_get_training_gateway_model_status(db, user, gateway_id, payload):
        calls.append(("model_status", gateway_id))
        return {
            "model": {
                "status": "failed",
                "profile": payload["profile"],
                "exists": False,
                "source_exists": False,
                "source_error": "internal model path does not exist",
            },
        }

    async def fake_discover_training_models_across_gateways(db, user, payload):
        calls.append(("discover_all", payload["training_gateway_id"]))
        return {
            "training_gateway_id": payload["training_gateway_id"],
            "summary": {"train_ready_count": 0, "candidate_count": 1, "found_but_not_train_ready_count": 1},
            "train_ready_candidates": [],
            "found_but_not_train_ready": [
                {
                    "path": source_path,
                    "gateway_id": "data-primary",
                    "source_gateway_id": "data-primary",
                    "train_ready": False,
                    "score": 100,
                }
            ],
        }

    async def fake_transfer_training_model_to_training_gateway(db, user, payload):
        calls.append(("transfer_model", payload["source_gateway_id"]))
        assert payload["training_gateway_id"] == "training-primary"
        assert payload["source_path"] == source_path
        return {
            "status": "transferred",
            "profile": payload["profile"],
            "training_gateway_id": "training-primary",
            "source_gateway_id": "data-primary",
            "source_path": source_path,
            "internal_model_ref": internal_ref,
            "import": {"status": "succeeded", "internal_model_ref": internal_ref},
            "prepare": {
                "model": {
                    "status": "succeeded",
                    "exists": True,
                    "model_dir": "/home/node/.skillforge_bridge/state/training_models/qwen36/model",
                    "source_path": internal_ref,
                }
            },
        }

    async def fake_dispatch_training_job(db, user, job_id):
        calls.append(("dispatch_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "running"
        job.failure_stage = None
        await db.flush()
        return {"id": job_id, "status": "running", "target_gateway_id": job.target_gateway_id}

    monkeypatch.setattr("app.training.service.approve_training_job", fake_approve_training_job)
    monkeypatch.setattr("app.training.service.get_training_gateway_model_status", fake_get_training_gateway_model_status)
    monkeypatch.setattr("app.training.service.discover_training_models_across_gateways", fake_discover_training_models_across_gateways)
    monkeypatch.setattr("app.training.service.start_training_model_transfer", fake_transfer_training_model_to_training_gateway)
    monkeypatch.setattr("app.training.service.dispatch_training_job", fake_dispatch_training_job)
    monkeypatch.setattr("app.aiclaw.bridge_registry.bridge_registry.is_online", lambda instance_id: instance_id == "training-primary")

    yesterday = (now_bjt() - timedelta(days=1)).date().isoformat()
    async with db_mod.async_session_factory() as session:
        user = User(
            id="learning-transfer-model-admin",
            username="learning-transfer-model-admin",
            name="学习闭环模型转运管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
            state="active",
        )
        gateway = OpenClawInstance(
            id="training-primary",
            name="GB10 237 · NVIDIA GB10",
            department="AI小组",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                "training": {"gateway": True, "supported_tasks": ["lora", "qlora", "eval"], "gpu_count": 1},
            }),
            is_active=True,
        )
        job = TrainingJob(
            id="tj-learning-transfer-model-from-235",
            title="默认路线模型从235转运",
            department="AI小组",
            created_by="learning_auto_flow",
            status="awaiting_review",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="learning-transfer-model-skill",
            target_gateway_id="training-primary",
            dataset_ref="learning-artifacts://learning-transfer-model-skill/training/yesterday",
            spec_json={
                "source": "learning_sample_threshold",
                "training_mode": "daily_incremental",
                "model": {"profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive", "source": "internal"},
                "deployment": {
                    "auto_request": True,
                    "auto_active": True,
                    "deployment_target_gateway_id": "inference-primary",
                    "deployment_runtime_profile": "mlx-qwen3.6-35b-a3b-lora",
                },
                "dataset": {
                    "manifest_hash": "manifest-learning-transfer-model",
                    "sample_total": 30,
                    "eval_count": 3,
                    "avg_quality": 0.88,
                    "window": {"mode": "yesterday", "date": yesterday},
                },
                "lineage": {"source": "learning_artifacts"},
                "governance": {
                    "auto_created": True,
                    "raw_payload_returned": False,
                    "auto_training_agent": "learning_training_reviewer",
                    "incremental_training": True,
                },
            },
        )
        session.add_all([user, gateway, job])
        await session.flush()

        result = await advance_learning_training_automation(session, user, days=7, max_jobs=1)
        stored = await session.get(TrainingJob, job.id)
        await session.commit()

    assert ("discover_all", "training-primary") in calls
    assert ("transfer_model", "data-primary") in calls
    assert ("dispatch_job", "tj-learning-transfer-model-from-235") in calls
    assert result["dispatched_jobs"][0]["job_id"] == "tj-learning-transfer-model-from-235"
    assert result["skipped_dispatch_jobs"] == []
    assert stored.status == "running"
    assert stored.failure_stage is None


@pytest.mark.asyncio
async def test_learning_training_automation_limits_dispatch_recovery_per_gateway(client, monkeypatch):
    import app.database as db_mod
    from app.auth.models import User
    from app.execution.models import OpenClawInstance
    from app.learning.service import advance_learning_training_automation
    from app.training.models import TrainingJob

    retry_calls: list[str] = []

    async def fake_retry_training_job(db, user, job_id):
        retry_calls.append(job_id)
        job = await db.get(TrainingJob, job_id)
        job.status = "running"
        job.failure_stage = None
        await db.flush()
        return {"id": job_id, "status": "running", "target_gateway_id": job.target_gateway_id}

    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_ENABLED", True)
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_DISPATCH_ENABLED", True)
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_COLLECT_STALE_SECONDS", 60)
    monkeypatch.setattr("app.training.service.retry_training_job", fake_retry_training_job)
    monkeypatch.setattr("app.aiclaw.bridge_registry.bridge_registry.is_online", lambda instance_id: instance_id == "learn-auto-single-gateway")

    def retry_job(job_id: str, skill_id: str, *, minutes_old: int) -> TrainingJob:
        return TrainingJob(
            id=job_id,
            title=f"单网关恢复限流 {skill_id}",
            department="AI小组",
            created_by="learning_auto_flow",
            status="queued",
            failure_stage="dispatch",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id=skill_id,
            target_gateway_id="learn-auto-single-gateway",
            dataset_ref=f"learning-artifacts://{skill_id}/training/yesterday/2026-01-01",
            spec_json={
                "source": "learning_sample_threshold",
                "training_mode": "daily_incremental",
                "dataset": {
                    "manifest_hash": f"manifest-{skill_id}",
                    "sample_total": 30,
                    "eval_count": 3,
                    "avg_quality": 0.88,
                    "window": {
                        "mode": "yesterday",
                        "date": (now_bjt() - timedelta(days=3)).date().isoformat(),
                    },
                },
                "lineage": {"source": "learning_artifacts"},
                "governance": {
                    "auto_created": True,
                    "raw_payload_returned": False,
                    "auto_training_agent": "learning_training_reviewer",
                    "incremental_training": True,
                },
            },
            created_at=now_bjt() - timedelta(minutes=minutes_old),
            updated_at=now_bjt() - timedelta(minutes=minutes_old),
        )

    async with db_mod.async_session_factory() as session:
        user = User(
            id="learning-auto-gateway-limit-admin",
            username="learning-auto-gateway-limit-admin",
            name="学习闭环网关限流管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
        )
        gateway = OpenClawInstance(
            id="learn-auto-single-gateway",
            name="单 worker 自动训练网关",
            department="AI小组",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["lora", "qlora", "eval"],
                    "gpu_count": 1,
                    "worker_count": 1,
                },
            }),
            is_active=True,
        )
        first = retry_job("tj-learning-auto-gateway-limit-1", "learning-auto-gateway-limit-a", minutes_old=30)
        second = retry_job("tj-learning-auto-gateway-limit-2", "learning-auto-gateway-limit-b", minutes_old=29)
        session.add_all([user, gateway, first, second])
        await session.flush()

        result = await advance_learning_training_automation(
            session,
            user,
            days=7,
            max_jobs=5,
            dispatch_recovery_only=True,
        )
        await session.commit()

    assert retry_calls == ["tj-learning-auto-gateway-limit-1"]
    assert result["retried_dispatch_jobs"][0]["job_id"] == "tj-learning-auto-gateway-limit-1"
    skipped = [item for item in result["skipped_dispatch_jobs"] if item["job_id"] == "tj-learning-auto-gateway-limit-2"]
    assert skipped
    assert skipped[0]["reason"] == "gateway_training_capacity_busy"
    assert skipped[0]["gateway_capacity"]["limit"] == 1
    assert skipped[0]["gateway_capacity"]["active_job_ids"] == ["tj-learning-auto-gateway-limit-1"]


@pytest.mark.asyncio
async def test_learning_training_automation_auto_approves_deployment_without_eval_samples(client, monkeypatch):
    import app.database as db_mod
    from app.auth.models import User
    from app.learning.service import advance_learning_training_automation
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

    calls: list[tuple[str, str]] = []

    async def fake_approve_training_deployment(db, user, deployment_id):
        calls.append(("approve_deployment", deployment_id))
        return {"deployment": {"id": deployment_id, "status": "canary", "rollout_percent": 10}}

    monkeypatch.setattr("app.training.service.approve_training_deployment", fake_approve_training_deployment)
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_ENABLED", True)
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_DEPLOYMENT_APPROVE_ENABLED", True)

    now = now_bjt()
    async with db_mod.async_session_factory() as session:
        user = User(
            id="learning-auto-no-eval-admin",
            username="learning-auto-no-eval-admin",
            name="学习闭环零评测管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
            state="active",
        )
        job = TrainingJob(
            id="tj-learning-auto-no-eval",
            title="零评测自动部署保护",
            department="AI小组",
            created_by="learning_auto_flow",
            status="completed",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="learning-auto-no-eval-skill",
            dataset_ref="learning-artifacts://platform/training/full-history/no-eval",
            spec_json={
                "source": "learning_sample_threshold",
                "training_mode": "full_history_incremental",
                "dataset": {
                    "manifest_hash": "manifest-learning-auto-no-eval",
                    "sample_total": 80,
                    "eval_count": 12,
                    "avg_quality": 0.9,
                    "window": {"mode": "full_history"},
                },
                "lineage": {"source": "learning_artifacts"},
                "governance": {
                    "auto_created": True,
                    "raw_payload_returned": False,
                    "auto_training_agent": "learning_training_reviewer",
                    "incremental_training": True,
                    "full_platform_history": True,
                },
            },
            created_at=now,
            updated_at=now,
        )
        deployment = TrainingModelDeployment(
            id="md-learning-auto-no-eval",
            job_id=job.id,
            department="AI小组",
            model_family="learning-auto-no-eval-skill:learning-loop-adapter",
            artifact_id="artifact-learning-auto-no-eval",
            artifact_ref_json={"id": "artifact-learning-auto-no-eval", "uri": "artifact://adapter", "sha256": "a" * 64},
            target_skill_ids_json=["learning-auto-no-eval-skill"],
            status="awaiting_review",
            rollout_percent=10,
            requested_by="learning_auto_flow",
            created_at=now,
            updated_at=now,
        )
        evaluation_task = TrainingJobTask(
            job_id=job.id,
            gateway_id="learn-auto-gateway",
            status="completed",
            progress=1,
            metrics_json={
                "op": "training.evaluate",
                "passed": True,
                "metrics": {
                    "eval_samples": 0,
                    "eval_evaluated_samples": 0,
                    "win_rate": 0.0,
                },
            },
        )
        session.add_all([user, job, deployment, evaluation_task])
        await session.flush()

        result = await advance_learning_training_automation(session, user, days=7, max_jobs=3)
        await session.commit()

    assert result["approved_deployments"][0]["deployment_id"] == "md-learning-auto-no-eval"
    assert result["approved_deployments"][0]["approval_risk"] == "no_eval_samples"
    assert result["approved_deployments"][0]["eval_readiness"]["reason"] == "no_eval_samples"
    assert ("approve_deployment", "md-learning-auto-no-eval") in calls
    assert not [item for item in result["skipped"] if item.get("deployment_id") == "md-learning-auto-no-eval"]


@pytest.mark.asyncio
async def test_learning_training_automation_skips_dispatch_retry_when_gateway_offline(client, monkeypatch):
    import app.database as db_mod
    from app.auth.models import User
    from app.execution.models import OpenClawInstance
    from app.learning.service import advance_learning_training_automation
    from app.training.models import TrainingJob

    retry_calls: list[str] = []

    async def fake_retry_training_job(db, user, job_id):
        retry_calls.append(job_id)
        return {"id": job_id, "status": "running", "target_gateway_id": "learn-auto-offline-gateway"}

    monkeypatch.setattr("app.training.service.retry_training_job", fake_retry_training_job)
    monkeypatch.setattr("app.aiclaw.bridge_registry.bridge_registry.is_online", lambda instance_id: False)

    async with db_mod.async_session_factory() as session:
        user = User(
            id="learning-auto-offline-admin",
            username="learning-auto-offline-admin",
            name="学习闭环离线网关管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
        )
        gateway = OpenClawInstance(
            id="learn-auto-offline-gateway",
            name="离线学习自动训练网关",
            department="AI小组",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["lora", "eval"],
                    "gpu_count": 1,
                    "worker_count": 3,
                },
            }),
            is_active=True,
        )
        job = TrainingJob(
            id="tj-learning-auto-offline-retry",
            title="离线网关恢复跳过任务",
            department="AI小组",
            created_by="learning_auto_flow",
            status="queued",
            failure_stage="dispatch",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="learning-auto-offline-skill",
            target_gateway_id=gateway.id,
            dataset_ref="learning-artifacts://learning-auto-offline-skill/training/yesterday/2026-01-01",
            spec_json={
                "source": "learning_sample_threshold",
                "training_mode": "daily_incremental",
                "dataset": {
                    "manifest_hash": "manifest-learning-auto-offline",
                    "sample_total": 30,
                    "eval_count": 3,
                    "avg_quality": 0.88,
                    "window": {
                        "mode": "yesterday",
                        "date": (now_bjt() - timedelta(days=3)).date().isoformat(),
                    },
                },
                "lineage": {"source": "learning_artifacts"},
                "governance": {
                    "auto_created": True,
                    "raw_payload_returned": False,
                    "auto_training_agent": "learning_training_reviewer",
                    "incremental_training": True,
                },
            },
            created_at=now_bjt() - timedelta(days=3),
            updated_at=now_bjt() - timedelta(minutes=15),
        )
        session.add_all([user, gateway, job])
        await session.flush()

        result = await advance_learning_training_automation(session, user, days=7, max_jobs=2)
        await session.commit()

    assert retry_calls == []
    assert result["retried_dispatch_jobs"] == []
    assert any(
        item["job_id"] == "tj-learning-auto-offline-retry" and item["reason"] == "gateway_offline"
        for item in result["skipped_dispatch_jobs"]
    )


@pytest.mark.asyncio
async def test_learning_training_automation_advances_admin_created_full_history_candidate(client, monkeypatch):
    import app.database as db_mod
    from app.auth.models import User
    from app.learning.service import advance_learning_training_automation
    from app.training.models import TrainingJob

    calls: list[tuple[str, str]] = []

    async def fake_approve_training_job(db, user, job_id):
        calls.append(("approve_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "queued"
        await db.flush()
        return {"id": job_id, "status": "queued", "target_gateway_id": job.target_gateway_id}

    async def fake_dispatch_training_job(db, user, job_id):
        calls.append(("dispatch_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "running"
        await db.flush()
        return {"id": job_id, "status": "running", "target_gateway_id": job.target_gateway_id}

    monkeypatch.setattr("app.training.service.approve_training_job", fake_approve_training_job)
    monkeypatch.setattr("app.training.service.dispatch_training_job", fake_dispatch_training_job)

    now = now_bjt()
    async with db_mod.async_session_factory() as session:
        user = User(
            id="full-history-automation-admin",
            username="full-history-automation-admin",
            name="全历史自动审核管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
            state="active",
        )
        job = TrainingJob(
            id="train_full_history_admin_created",
            title="管理员触发的全历史训练",
            department="AI小组",
            created_by="admin",
            status="awaiting_review",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="full-history-admin-skill",
            target_gateway_id="node-full-history",
            dataset_ref="learning-artifacts://platform/training/full-history/test",
            spec_json={
                "source": "learning_sample_threshold",
                "training_mode": "full_history_incremental",
                "dataset": {
                    "sample_total": 12,
                    "eval_count": 2,
                    "window": {"mode": "full_history"},
                    "manifest_hash": "full-history-admin-manifest",
                },
                "lineage": {"source": "learning_artifacts"},
                "governance": {
                    "auto_training_agent": "learning_training_reviewer",
                    "incremental_training": True,
                    "raw_payload_returned": False,
                },
            },
            created_at=now,
            updated_at=now,
        )
        session.add_all([user, job])
        await session.commit()

    async with db_mod.async_session_factory() as session:
        user = await session.get(User, "full-history-automation-admin")
        result = await advance_learning_training_automation(session, user, days=30, max_jobs=3)
        await session.commit()

    assert calls == [
        ("approve_job", "train_full_history_admin_created"),
        ("dispatch_job", "train_full_history_admin_created"),
    ]
    assert result["approved_jobs"][0]["approved_by"] == "learning_training_reviewer"
    assert result["dispatched_jobs"][0]["job_id"] == "train_full_history_admin_created"


@pytest.mark.asyncio
async def test_learning_training_automation_prefers_pending_full_history_over_completed(client, monkeypatch):
    import app.database as db_mod
    from app.auth.models import User
    from app.learning.service import advance_learning_training_automation
    from app.training.models import TrainingJob

    calls: list[tuple[str, str]] = []

    async def fake_approve_training_job(db, user, job_id):
        calls.append(("approve_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "queued"
        await db.flush()
        return {"id": job_id, "status": "queued", "target_gateway_id": job.target_gateway_id}

    async def fake_dispatch_training_job(db, user, job_id):
        calls.append(("dispatch_job", job_id))
        job = await db.get(TrainingJob, job_id)
        job.status = "running"
        await db.flush()
        return {"id": job_id, "status": "running", "target_gateway_id": job.target_gateway_id}

    async def fake_evaluate_training_job(db, user, job_id):
        calls.append(("evaluate_job", job_id))
        return {"id": job_id, "status": "completed", "latest_deployment": {"id": "deploy-existing"}}

    monkeypatch.setattr("app.training.service.approve_training_job", fake_approve_training_job)
    monkeypatch.setattr("app.training.service.dispatch_training_job", fake_dispatch_training_job)
    monkeypatch.setattr("app.training.service.evaluate_training_job", fake_evaluate_training_job)
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_ENABLED", True)
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_DISPATCH_ENABLED", True)
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_MIN_SAMPLES", 1)

    now = now_bjt()
    dataset = {
        "sample_total": 80,
        "eval_count": 4,
        "avg_quality": 0.7,
        "window": {"mode": "full_history"},
    }
    spec = {
        "source": "learning_sample_threshold",
        "training_mode": "full_history_incremental",
        "dataset": dataset,
        "parent_model": {"artifact_uri": "artifact://parent", "artifact_sha256": "a" * 64},
        "lineage": {"source": "learning_artifacts"},
        "governance": {
            "auto_training_agent": "learning_training_reviewer",
            "incremental_training": True,
            "raw_payload_returned": False,
        },
    }
    async with db_mod.async_session_factory() as session:
        user = User(
            id="full-history-pending-admin",
            username="full-history-pending-admin",
            name="全历史待训练管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
            state="active",
        )
        completed = TrainingJob(
            id="train_full_history_existing_completed",
            title="旧全历史已完成",
            department="AI小组",
            created_by="learning_auto_flow",
            status="completed",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="full-history-pending-skill",
            dataset_ref="learning-artifacts://platform/training/full-history/old",
            spec_json={**spec, "dataset": {**dataset, "avg_quality": 0.95}},
            created_at=now - timedelta(hours=2),
            updated_at=now - timedelta(hours=2),
        )
        pending = TrainingJob(
            id="train_full_history_pending_new",
            title="新全历史待训练",
            department="AI小组",
            created_by="learning_auto_flow",
            status="awaiting_review",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="full-history-pending-skill",
            dataset_ref="learning-artifacts://platform/training/full-history/new",
            spec_json={**spec, "dataset": {**dataset, "avg_quality": 0.72}},
            created_at=now,
            updated_at=now,
        )
        session.add_all([user, completed, pending])
        await session.commit()

    async with db_mod.async_session_factory() as session:
        user = await session.get(User, "full-history-pending-admin")
        result = await advance_learning_training_automation(session, user, days=30, max_jobs=1)
        await session.commit()

    assert ("approve_job", "train_full_history_pending_new") in calls
    assert ("dispatch_job", "train_full_history_pending_new") in calls
    assert ("evaluate_job", "train_full_history_existing_completed") not in calls
    assert result["approved_jobs"][0]["job_id"] == "train_full_history_pending_new"


@pytest.mark.asyncio
async def test_execution_trace_samples_create_learning_training_candidate(client):
    import app.database as db_mod
    from app.auth.models import User
    from app.learning.models import LearningArtifact, LearningEvent
    from app.learning.service import ensure_training_candidates_from_learning_samples
    from app.training.models import TrainingJob

    yesterday = now_bjt() - timedelta(days=1)
    async with db_mod.async_session_factory() as session:
        user = User(
            id="learning-trace-admin",
            username="learning-trace-admin",
            name="执行轨迹训练管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
        )
        skill = Skill(
            id="learn-trace-skill",
            name="执行轨迹 Skill",
            display_name="执行轨迹 Skill",
            department="AI小组",
            status="active",
            visibility="department",
            approval_level=0,
        )
        event = LearningEvent(
            id="le-trace-samples-source",
            event_type="skill.run.completed",
            source_type="execution_run",
            source_id="run-trace-samples-source",
            source_hash="run-trace-samples-source",
            department="AI小组",
            skill_id=skill.id,
            run_id="run-trace-samples-source",
            redacted_summary="执行轨迹样本自动训练候选。",
            quality_score=0.8,
            created_at=yesterday,
            updated_at=yesterday,
        )
        session.add_all([user, skill, event])
        await session.flush()
        trace_artifact_ids = []
        for index in range(2):
            artifact_id = f"la-trace-sample-{index}"
            trace_artifact_ids.append(artifact_id)
            session.add(LearningArtifact(
                id=artifact_id,
                event_id=event.id,
                artifact_kind="training_sample",
                artifact_hash=artifact_id,
                target_type="skill",
                target_id=skill.id,
                department="AI小组",
                skill_id=skill.id,
                run_id=f"run-trace-samples-{index}",
                title=f"执行轨迹训练样本 {index}",
                summary="执行输入、输出和形成数据可进入学习训练候选。",
                content_json={
                    "instruction": "基于执行轨迹学习输入输出和中间产物。",
                    "input": {"steps": [{"input": {"question": "链接下滑原因"}}]},
                    "output": {"steps": [{"output": {"summary": "搜索访客下降"}}]},
                    "formed_data": {
                        "steps": [{"duration_ms": 120 + index}],
                        "runtime_agent": {
                            "agent_id": "trace-node-1",
                            "contract": {"complete": True, "flow_traceable": True},
                        },
                    },
                    "source_type": "execution_trace",
                    "source_channel": "manual",
                },
                labels_json=["training", "execution_trace"],
                quality_score=0.8,
                confidence=0.74,
                status="materialized",
                sink_type="training_sample",
                created_at=yesterday,
                updated_at=yesterday,
            ))
        result = await ensure_training_candidates_from_learning_samples(
            session,
            user,
            days=7,
            min_sample_count=2,
            max_skills=1,
        )
        await session.commit()

    assert result["created"]
    created = result["created"][0]
    assert created["skill_id"] == "learn-trace-skill"
    assert created["sample_total"] == 2

    async with db_mod.async_session_factory() as session:
        job = await session.get(TrainingJob, created["training_job_id"])
        assert job is not None
        spec = job.spec_json or {}

    assert job.status == "awaiting_review"
    assert spec["source"] == "learning_sample_threshold"
    assert spec["dataset"]["sample_total"] == 2
    assert set(spec["dataset"]["learning_artifact_ids"]) == set(trace_artifact_ids)
    assert spec["lineage"]["source"] == "learning_artifacts"
    assert set(spec["lineage"]["learning_artifact_ids"]) == set(trace_artifact_ids)
    assert spec["deployment"]["auto_request"] is True
    assert spec["deployment"]["auto_active"] is True
    assert spec["deployment"]["rollout_percent"] == 100
    assert spec["governance"]["deployment_review_required"] is False
    assert spec["governance"]["auto_deployment_mode"] == "active_after_eval_readiness"
    assert spec["governance"]["no_auto_approval"] is False
    assert spec["governance"]["auto_training_agent"] == "learning_training_reviewer"
    assert spec["dataset"]["window"]["mode"] == "yesterday"

    manifest_resp = await client.get(
        "/api/learning/training-manifest",
        params={"skill_id": "learn-trace-skill"},
    )
    assert manifest_resp.status_code == 200, manifest_resp.text
    manifest_sample = next(
        item
        for item in manifest_resp.json()["samples"]
        if item["artifact_id"] == "la-trace-sample-0"
    )
    lineage = manifest_sample["lineage_summary"]
    assert lineage["has_input"] is True
    assert lineage["has_output"] is True
    assert lineage["has_formed_data"] is True
    assert lineage["formed_data_sha256"]
    assert lineage["has_runtime_agent"] is True
    assert lineage["runtime_agent_id"] == "trace-node-1"
    assert lineage["runtime_agent_contract_complete"] is True
    assert "duration_ms" not in json.dumps(manifest_sample, ensure_ascii=False)


@pytest.mark.asyncio
async def test_learning_training_candidates_loop_across_platform_without_mixing_departments(client):
    import app.database as db_mod
    from app.auth.models import User
    from app.learning.models import LearningArtifact, LearningEvent
    from app.learning.service import ensure_training_candidates_from_learning_samples
    from app.training.models import TrainingJob
    from sqlalchemy import select

    skills = [
        ("learn-platform-ec-skill", "EC"),
        ("learn-platform-hr-skill", "HR"),
    ]

    yesterday = now_bjt() - timedelta(days=1)
    async with db_mod.async_session_factory() as session:
        admin = User(
            id="learning-platform-admin",
            username="learning-platform-admin",
            name="全台学习管理员",
            role="admin",
            department="平台",
            can_view_all=True,
            is_active=True,
        )
        ec_user = User(
            id="learning-platform-ec-user",
            username="learning-platform-ec-user",
            name="EC 学习用户",
            role="operator",
            department="EC",
            can_view_all=False,
            is_active=True,
        )
        session.add_all([admin, ec_user])
        for skill_id, department in skills:
            skill = Skill(
                id=skill_id,
                name=f"{department} 全台循环 Skill",
                display_name=f"{department} 全台循环 Skill",
                department=department,
                status="active",
                visibility="department",
                approval_level=0,
            )
            event = LearningEvent(
                id=f"le-platform-loop-{department.lower()}",
                event_type="skill.run.completed",
                source_type="execution_run",
                source_id=f"run-platform-loop-{department.lower()}",
                source_hash=f"run-platform-loop-{department.lower()}",
                department=department,
                skill_id=skill_id,
                run_id=f"run-platform-loop-{department.lower()}",
                redacted_summary=f"{department} 部门样本池达到训练阈值。",
                quality_score=0.86,
                created_at=yesterday,
                updated_at=yesterday,
            )
            session.add_all([skill, event])
            await session.flush()
            for index in range(3):
                session.add(LearningArtifact(
                    id=f"la-platform-{department.lower()}-{index}",
                    event_id=event.id,
                    artifact_kind="training_sample",
                    artifact_hash=f"la-platform-{department.lower()}-{index}",
                    target_type="skill",
                    target_id=skill_id,
                    department=department,
                    skill_id=skill_id,
                    run_id=f"run-platform-loop-{department.lower()}-{index}",
                    title=f"{department} 样本 {index}",
                    summary=f"{department} 部门独立训练样本。",
                    content_json={
                        "instruction": "学习本部门输入、输出和形成数据。",
                        "input": {"department": department, "item_id": f"{department}-{index}"},
                        "output": {"summary": f"{department} 处理建议"},
                        "formed_data": {"department": department, "step": index},
                        "source_type": "execution_trace",
                        "source_channel": "node_scheduler",
                    },
                    labels_json=["training", "execution_trace", f"department:{department}"],
                    quality_score=0.86,
                    confidence=0.8,
                    status="materialized",
                    sink_type="training_sample",
                    created_at=yesterday,
                    updated_at=yesterday,
                ))
        global_result = await ensure_training_candidates_from_learning_samples(
            session,
            admin,
            days=7,
            min_sample_count=3,
            max_skills=5,
        )
        await session.commit()

    created_by_skill = {item["skill_id"]: item for item in global_result["created"]}
    assert set(created_by_skill) == {"learn-platform-ec-skill", "learn-platform-hr-skill"}

    async with db_mod.async_session_factory() as session:
        jobs = (
            await session.execute(
                select(TrainingJob)
                .where(TrainingJob.target_skill_id.in_(list(created_by_skill)))
            )
        ).scalars().all()
        by_skill = {job.target_skill_id: job for job in jobs}

    assert by_skill["learn-platform-ec-skill"].department == "EC"
    assert by_skill["learn-platform-hr-skill"].department == "HR"
    ec_ids = set(by_skill["learn-platform-ec-skill"].spec_json["dataset"]["learning_artifact_ids"])
    hr_ids = set(by_skill["learn-platform-hr-skill"].spec_json["dataset"]["learning_artifact_ids"])
    assert ec_ids == {f"la-platform-ec-{index}" for index in range(3)}
    assert hr_ids == {f"la-platform-hr-{index}" for index in range(3)}
    assert ec_ids.isdisjoint(hr_ids)
    assert by_skill["learn-platform-ec-skill"].spec_json["dataset"]["manifest_hash"] != (
        by_skill["learn-platform-hr-skill"].spec_json["dataset"]["manifest_hash"]
    )
    assert by_skill["learn-platform-ec-skill"].spec_json["deployment"]["target_skill_ids"] == [
        "learn-platform-ec-skill"
    ]
    assert by_skill["learn-platform-hr-skill"].spec_json["deployment"]["target_skill_ids"] == [
        "learn-platform-hr-skill"
    ]

    async with db_mod.async_session_factory() as session:
        ec_user = await session.get(User, "learning-platform-ec-user")
        hr_job = await session.get(TrainingJob, by_skill["learn-platform-hr-skill"].id)
        hr_job.status = "failed"
        scoped_result = await ensure_training_candidates_from_learning_samples(
            session,
            ec_user,
            days=7,
            min_sample_count=3,
            max_skills=5,
        )
        await session.commit()

    scoped_skills = {item["skill_id"] for item in scoped_result["created"] + scoped_result["skipped"]}
    assert scoped_skills == {"learn-platform-ec-skill"}
    assert scoped_result["skipped"][0]["reason"] == "duplicate_manifest"
    assert scoped_result["skipped"][0]["training_job_id"] == by_skill["learn-platform-ec-skill"].id


@pytest.mark.asyncio
async def test_learning_training_candidates_include_all_project_and_skill_samples_without_caps(client, monkeypatch):
    import app.database as db_mod
    import app.learning.service as learning_service
    from app.auth.models import User
    from app.learning.models import LearningArtifact, LearningEvent
    from app.training.models import TrainingJob
    from app.training.service import _build_learning_gateway_dataset_package
    from sqlalchemy import select

    monkeypatch.setattr(learning_service, "LEARNING_DAILY_TRAINING_SPEC_INLINE_ID_MAX_BYTES", 128)
    yesterday = now_bjt() - timedelta(days=1)
    project_source_id = "project:learning-full-project"
    skill_source_id = "learning-full-skill"
    async with db_mod.async_session_factory() as session:
        admin = User(
            id="learning-full-source-admin",
            username="learning-full-source-admin",
            name="全量学习管理员",
            role="admin",
            department="平台",
            can_view_all=True,
            is_active=True,
        )
        skill = Skill(
            id=skill_source_id,
            name="全量普通 Skill",
            display_name="全量普通 Skill",
            department="EC",
            status="active",
            visibility="department",
            approval_level=0,
        )
        session.add_all([admin, skill])
        project_event = LearningEvent(
            id="le-full-project-source",
            event_type="project.ingress.received",
            source_type="project_ingress_event",
            source_id="project-full-source-run",
            source_hash="project-full-source-run",
            department="EC",
            skill_id=project_source_id,
            run_id="project-full-source-run",
            redacted_summary="项目来源训练样本池。",
            quality_score=0.87,
            created_at=yesterday,
            updated_at=yesterday,
        )
        skill_event = LearningEvent(
            id="le-full-skill-source",
            event_type="skill.run.completed",
            source_type="execution_run",
            source_id="skill-full-source-run",
            source_hash="skill-full-source-run",
            department="EC",
            skill_id=skill_source_id,
            run_id="skill-full-source-run",
            redacted_summary="普通 Skill 来源训练样本池。",
            quality_score=0.86,
            created_at=yesterday,
            updated_at=yesterday,
        )
        session.add_all([project_event, skill_event])
        await session.flush()
        for index in range(305):
            session.add(LearningArtifact(
                id=f"la-full-project-{index:03d}",
                event_id=project_event.id,
                artifact_kind="eval_case" if index in {10, 150} else "training_sample",
                artifact_hash=f"la-full-project-{index:03d}",
                target_type="project",
                target_id="learning-full-project",
                department="EC",
                skill_id=project_source_id,
                run_id=f"project-full-source-run-{index}",
                title=f"项目全量样本 {index}",
                summary="用于验证每日训练不会按 project 来源裁剪 200 条。",
                content_json={
                    "input": {"project_id": "learning-full-project", "item": index},
                    "output": {"summary": f"项目输出 {index}"},
                    "formed_data": {"step": index},
                    "source_type": "project_ingress",
                },
                labels_json=["training", "project"],
                quality_score=0.87,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
                created_at=yesterday + timedelta(seconds=index),
                updated_at=yesterday + timedelta(seconds=index),
            ))
        for index in range(4):
            session.add(LearningArtifact(
                id=f"la-full-skill-{index}",
                event_id=skill_event.id,
                artifact_kind="training_sample",
                artifact_hash=f"la-full-skill-{index}",
                target_type="skill",
                target_id=skill_source_id,
                department="EC",
                skill_id=skill_source_id,
                run_id=f"skill-full-source-run-{index}",
                title=f"Skill 全量样本 {index}",
                summary="用于验证 max_skills 不再裁剪来源。",
                content_json={
                    "input": {"skill_id": skill_source_id, "item": index},
                    "output": {"summary": f"Skill 输出 {index}"},
                    "formed_data": {"step": index},
                    "source_type": "execution_trace",
                },
                labels_json=["training", "skill"],
                quality_score=0.86,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
                created_at=yesterday + timedelta(minutes=5, seconds=index),
                updated_at=yesterday + timedelta(minutes=5, seconds=index),
            ))

        result = await learning_service.ensure_training_candidates_from_learning_samples(
            session,
            admin,
            days=7,
            min_sample_count=4,
            max_skills=1,
        )
        await session.commit()

    created_by_skill = {item["skill_id"]: item for item in result["created"]}
    assert set(created_by_skill) == {project_source_id, skill_source_id}
    assert created_by_skill[project_source_id]["sample_total"] == 305
    assert created_by_skill[project_source_id]["eval_count"] == 2
    assert created_by_skill[project_source_id]["learning_artifact_ids_inlined"] is False
    assert created_by_skill[skill_source_id]["sample_total"] == 4

    async with db_mod.async_session_factory() as session:
        jobs = (
            await session.execute(
                select(TrainingJob).where(TrainingJob.target_skill_id.in_([project_source_id, skill_source_id]))
            )
        ).scalars().all()
        by_skill = {job.target_skill_id: job for job in jobs}
        package = await _build_learning_gateway_dataset_package(session, by_skill[project_source_id])
        project_spec = by_skill[project_source_id].spec_json
        skill_spec = by_skill[skill_source_id].spec_json
    assert "learning_artifact_ids" not in project_spec["dataset"]
    assert project_spec["dataset"]["learning_artifact_ids_count"] == 305
    assert project_spec["dataset"]["learning_artifact_ids_hash"]
    assert project_spec["dataset"]["learning_artifact_ids_inlined"] is False
    assert project_spec["dataset"]["sample_total"] == 305
    assert project_spec["dataset"]["selector"]["target_skill_id"] == project_source_id
    assert project_spec["dataset"]["selector"]["inline_limit"] == 305
    assert project_spec["lineage"]["learning_artifact_ids_count"] == 305
    assert package is not None
    assert package["sample_count"] == 305
    assert len(package["samples"]) == 305
    assert skill_spec["dataset"]["sample_total"] == 4
    assert set(skill_spec["dataset"]["learning_artifact_ids"]) == {f"la-full-skill-{index}" for index in range(4)}


@pytest.mark.asyncio
async def test_learning_backfill_creates_sf_iteration_candidate(client):
    import app.database as db_mod

    async with db_mod.async_session_factory() as session:
        row = CodexMcpCallAudit(
            request_id="req-learning-sf",
            proof_id="proof-learning-sf",
            user_id="admin",
            skill_id=None,
            debug_run_id=None,
            server="skillforge",
            tool="tmall_missing_tool",
            data_scope="test",
            run_mode="web_dry_run",
            dry_run=True,
            ok=False,
            error_code="PARAM_INVALID",
            detail_json={"detail": "missing itemId"},
            created_at=now_bjt(),
        )
        session.add(row)
        await session.commit()

    res = await client.post("/api/learning/events/backfill", json={"days": 7, "limit": 50, "materialize": False})
    assert res.status_code == 200, res.text

    candidates = (await client.get("/api/learning/candidates", params={"target_type": "sf"})).json()["items"]
    assert candidates
    assert candidates[0]["target_type"] == "sf"
    assert "tmall_missing_tool" in (candidates[0].get("target_id") or candidates[0].get("title") or "")


@pytest.mark.asyncio
async def test_learning_backfill_creates_high_frequency_sf_candidate(client):
    import app.database as db_mod

    async with db_mod.async_session_factory() as session:
        rows = []
        for idx in range(3):
            rows.append(
                CodexMcpCallAudit(
                    request_id=f"req-learning-sf-hot-{idx}",
                    proof_id=f"proof-learning-sf-hot-{idx}",
                    user_id="admin",
                    skill_id=None,
                    debug_run_id=None,
                    server="skillforge",
                    tool="tmall_hot_tool",
                    data_scope="test",
                    run_mode="web_dry_run",
                    dry_run=True,
                    ok=True,
                    detail_json={"idx": idx},
                    created_at=now_bjt(),
                )
            )
        session.add_all(rows)
        await session.commit()

    res = await client.post("/api/learning/events/backfill", json={"days": 7, "limit": 50, "materialize": False})
    assert res.status_code == 200, res.text
    assert res.json()["captured"]["sf_usage_aggregate"] >= 1

    candidates = (await client.get("/api/learning/candidates", params={"target_type": "sf", "q": "tmall_hot_tool"})).json()["items"]
    assert any("固化高频 SF 能力" in item["title"] for item in candidates)

    events = (
        await client.get(
            "/api/learning/events",
            params={"days": 7, "source_type": "sf_mcp_call", "sf_tool": "tmall_hot_tool"},
        )
    ).json()["items"]
    assert len(events) >= 3
    assert {item["source_type"] for item in events} == {"sf_mcp_call"}

    summary = (
        await client.get(
            "/api/learning/summary",
            params={"days": 7, "source_type": "sf_mcp_call", "sf_tool": "tmall_hot_tool"},
        )
    ).json()
    assert summary["events"]["total"] >= 3
    assert summary["filters"]["source_type"] == "sf_mcp_call"
    assert summary["filters"]["sf_tool"] == "tmall_hot_tool"

    tool_candidates = (await client.get("/api/learning/candidates", params={"sf_tool": "tmall_hot_tool"})).json()["items"]
    assert any(item["target_type"] == "sf" for item in tool_candidates)

    topology = (
        await client.get(
            "/api/learning/flow-topology",
            params={"days": 7, "source_type": "sf_mcp_call", "sf_tool": "tmall_hot_tool"},
        )
    ).json()
    assert topology["filters"]["source_type"] == "sf_mcp_call"
    assert topology["filters"]["sf_tool"] == "tmall_hot_tool"
    assert topology["connectors"][0]["count"] >= 3
    source_nodes = next(stage for stage in topology["stages"] if stage["key"] == "source")["nodes"]
    assert source_nodes
    assert {node["payload"]["source_type"] for node in source_nodes} == {"sf_mcp_call"}

    graph = (
        await client.get(
            "/api/learning/flow-graph",
            params={"days": 7, "source_type": "sf_usage_aggregate", "sf_tool": "tmall_hot_tool"},
        )
    ).json()
    assert graph["edges"]
    assert {edge["source"].split(":", 1)[0] for edge in graph["edges"]} == {"sf_usage_aggregate"}

    bottlenecks = (
        await client.get(
            "/api/learning/bottlenecks",
            params={"days": 7, "target_type": "sf", "sf_tool": "tmall_hot_tool"},
        )
    ).json()
    assert bottlenecks["filters"]["target_type"] == "sf"
    assert bottlenecks["filters"]["sf_tool"] == "tmall_hot_tool"
    assert bottlenecks["summary"]["open_candidates"] >= 1
    sections = {section["key"]: section for section in bottlenecks["sections"]}
    assert sections["open_candidates"]["items"]
    assert sections["open_candidates"]["items"][0]["target_type"] == "sf"
    assert bottlenecks["governance"]["raw_payload_returned"] is False


@pytest.mark.asyncio
async def test_learning_ai_judges_high_frequency_flow_should_be_agent_candidate(client):
    import app.database as db_mod

    async with db_mod.async_session_factory() as session:
        rows = []
        for idx in range(3):
            rows.append(
                CodexMcpCallAudit(
                    request_id=f"req-learning-agentize-{idx}",
                    proof_id=f"proof-learning-agentize-{idx}",
                    user_id="admin",
                    skill_id=None,
                    debug_run_id=None,
                    server="skillforge",
                    tool="tmall_agentize_tool",
                    data_scope="test",
                    run_mode="web_dry_run",
                    dry_run=True,
                    ok=True,
                    detail_json={"idx": idx},
                    created_at=now_bjt(),
                )
            )
        session.add_all(rows)
        await session.commit()

    res = await client.post("/api/learning/events/backfill", json={"days": 7, "limit": 50, "materialize": False})
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["captured"]["sf_usage_aggregate"] >= 1
    assert payload["agentization_candidates_created"] >= 1
    assert payload["agentization"]["governance"]["candidate_only"] is True
    assert payload["agentization"]["governance"]["no_auto_publish"] is True
    assert payload["agentization"]["review_handoffs"]
    assert payload["agentization"]["review_handoffs"][0]["governance_queue"] == "learning_agent_review"
    assert payload["agentization"]["created"][0]["review_handoff"]["governance_task_id"].startswith("lgt_")

    candidates = (
        await client.get(
            "/api/learning/candidates",
            params={"target_type": "agent", "q": "tmall_agentize_tool"},
        )
    ).json()["items"]
    assert candidates
    candidate = candidates[0]
    assert candidate["target_type"] == "agent"
    assert "Agent" in candidate["title"]
    assert "affected_modules" in candidate["expected_impact"]
    assert "sf" in candidate["expected_impact"]["affected_modules"]
    assert candidate["expected_impact"]["ai_used"] is False
    assert candidate["status"] == "reviewing"
    assert candidate["external_ref"]["governance_queue"] == "learning_agent_review"
    assert candidate["external_ref"]["governance_task_id"].startswith("lgt_")
    assert candidate["external_ref"]["agent_draft_id"].startswith("draft-agent-")
    assert candidate["external_ref"]["agent_draft_status"] == "ready"
    assert candidate["external_ref"]["no_auto_publish"] is True
    assert candidate["external_ref"]["agent_draft_validation"]["status"] == "passed"
    assert candidate["external_ref"]["agent_draft_validation"]["no_code_execution"] is True

    from app.learning.models import LearningGovernanceTask
    from app.workbench.models import SkillStudioDraft

    async with db_mod.async_session_factory() as session:
        task = await session.get(LearningGovernanceTask, candidate["external_ref"]["governance_task_id"])
        assert task is not None
        assert task.queue_id == "learning_agent_review"
        assert task.candidate_id == candidate["id"]
        assert task.status == "pending"
        draft = await session.get(SkillStudioDraft, candidate["external_ref"]["agent_draft_id"])
        assert draft is not None
        assert draft.generation_status == "ready"
        assert draft.skill_id is None
        assert (draft.contract_json or {}).get("lineage", {}).get("learning_candidate_id") == candidate["id"]
        assert "agent_blueprint.json" in (draft.extra_files or {})
        assert "learning-lineage.json" in (draft.extra_files or {})
        assert "no_auto_publish" in (draft.extra_files or {}).get("learning-lineage.json", "")
        assert "tmall_agentize_tool" in (draft.skill_md or draft.source_message or "")

    artifacts = (
        await client.get(
            "/api/learning/artifacts",
            params={"artifact_kind": "agent_creation_candidate", "q": "tmall_agentize_tool"},
        )
    ).json()["items"]
    assert artifacts
    assert artifacts[0]["target_type"] == "agent"
    assert "content" not in artifacts[0]
    assert artifacts[0]["content_preview"]["source_type"] == "agentization_judgement"

    manual = (
        await client.post(
            "/api/learning/agentization/run",
            json={"days": 7, "limit": 20, "use_ai": False},
        )
    ).json()
    assert manual["governance"]["candidate_only"] is True
    assert manual["governance"]["raw_payload_returned"] is False
    assert manual["governance"]["draft_only"] is True

    draft_again = (await client.post(f"/api/learning/candidates/{candidate['id']}/create-agent-draft")).json()
    assert draft_again["idempotent"] is True
    assert draft_again["agent_draft"]["id"] == candidate["external_ref"]["agent_draft_id"]
    assert draft_again["governance"]["no_auto_publish"] is True

    reviewed = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "reviewed_by"})).json()
    assert any(
        edge["source"] == f"improvement_candidate:{candidate['id']}"
        and edge["target"] == "governance_queue:learning_agent_review"
        for edge in reviewed["edges"]
    )
    queued = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "queued_as"})).json()
    assert any(
        edge["source"] == f"improvement_candidate:{candidate['id']}"
        and edge["target"] == f"learning_governance_task:{candidate['external_ref']['governance_task_id']}"
        for edge in queued["edges"]
    )
    drafted = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "drafted_as"})).json()
    assert any(
        edge["source"] == f"improvement_candidate:{candidate['id']}"
        and edge["target"] == f"agent_draft:{candidate['external_ref']['agent_draft_id']}"
        and edge["metadata"]["no_auto_publish"] is True
        for edge in drafted["edges"]
    )
    validated = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "validated_by"})).json()
    assert any(
        edge["source"] == f"improvement_candidate:{candidate['id']}"
        and edge["target"].startswith("learning_event:")
        and edge["metadata"]["status"] == "passed"
        for edge in validated["edges"]
    )
    assert any(
        edge["source"] == f"agent_draft:{candidate['external_ref']['agent_draft_id']}"
        and edge["target"].startswith("learning_event:")
        and edge["metadata"]["status"] == "passed"
        for edge in validated["edges"]
    )

    journeys = (
        await client.get(
            "/api/learning/flow-journeys",
            params={"days": 7, "target_type": "agent", "q": "tmall_agentize_tool"},
        )
    ).json()
    assert any(
        step["entity_type"] == "agent_draft"
        and step["payload"]["agent_draft_id"] == candidate["external_ref"]["agent_draft_id"]
        and step["payload"]["no_auto_publish"] is True
        for item in journeys["items"]
        for step in item["steps"]
    )
    assert any(
        step["entity_type"] == "learning_governance_task"
        and step["entity_id"] == candidate["external_ref"]["governance_task_id"]
        and step["payload"]["governance_queue"] == "learning_agent_review"
        for item in journeys["items"]
        for step in item["steps"]
    )

    task_list = (
        await client.get(
            "/api/learning/governance-tasks",
            params={"queue_id": "learning_agent_review", "candidate_id": candidate["id"]},
        )
    ).json()
    assert task_list["total"] == 1
    assert task_list["items"][0]["id"] == candidate["external_ref"]["governance_task_id"]
    assert task_list["items"][0]["status"] == "pending"

    summary = (await client.get("/api/learning/summary", params={"days": 7})).json()
    assert summary["self_iteration"]["governance_tasks"] >= 1
    assert summary["self_iteration"]["governance_tasks_pending"] >= 1
    assert summary["self_iteration"]["governance_tasks_by_status"]["pending"] >= 1

    bottlenecks = (
        await client.get(
            "/api/learning/bottlenecks",
            params={"days": 7, "target_type": "agent"},
        )
    ).json()
    bottleneck_sections = {section["key"]: section for section in bottlenecks["sections"]}
    assert bottleneck_sections["pending_governance_tasks"]["count"] >= 1
    assert any(
        item["id"] == candidate["external_ref"]["governance_task_id"]
        for item in bottleneck_sections["pending_governance_tasks"]["items"]
    )
    assert bottlenecks["summary"]["pending_governance_tasks"] >= 1

    accepted = (
        await client.post(
            f"/api/learning/governance-tasks/{candidate['external_ref']['governance_task_id']}/status",
            json={"decision": "accepted", "reason": "确认需要新增 Agent，但仍需人工实施。"},
        )
    ).json()
    assert accepted["task"]["status"] == "completed"
    assert accepted["candidate"]["status"] == "accepted"
    assert accepted["candidate"]["external_ref"]["implementation_queue"] == "learning_agent_implementation"
    assert accepted["candidate"]["external_ref"]["implementation_status"] == "awaiting_human_implementation"
    assert accepted["candidate"]["external_ref"]["no_auto_publish"] is True
    resolved = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "resolved_as"})).json()
    assert any(
        edge["source"] == f"learning_governance_task:{candidate['external_ref']['governance_task_id']}"
        and edge["target"] == f"improvement_candidate:{candidate['id']}"
        and edge["metadata"]["decision"] == "accepted"
        for edge in resolved["edges"]
    )

    implementation_graph = (
        await client.get(
            "/api/learning/flow-graph",
            params={"days": 7, "relation": "ready_for_implementation"},
        )
    ).json()
    assert any(
        edge["source"] == f"improvement_candidate:{candidate['id']}"
        and edge["target"] == "governance_queue:learning_agent_implementation"
        and edge["metadata"]["no_auto_publish"] is True
        for edge in implementation_graph["edges"]
    )
    assert any(
        edge["source"] == f"agent_draft:{candidate['external_ref']['agent_draft_id']}"
        and edge["target"] == "governance_queue:learning_agent_implementation"
        for edge in implementation_graph["edges"]
    )

    rejected = (
        await client.post(
            f"/api/learning/candidates/{candidate['id']}/reject",
            json={"reason": "证据不足，暂不需要新增 Agent。"},
        )
    ).json()
    assert rejected["status"] == "rejected"

    feedback_events = (
        await client.get(
            "/api/learning/events",
            params={"days": 7, "source_type": "improvement_candidate"},
        )
    ).json()["items"]
    assert any(item["event_type"] == "candidate.accepted" and item["source_id"] == f"{candidate['id']}:accepted" for item in feedback_events)
    assert any(item["event_type"] == "candidate.rejected" and item["source_id"] == f"{candidate['id']}:rejected" for item in feedback_events)

    feedback_artifacts = (
        await client.get(
            "/api/learning/artifacts",
            params={"days": 7, "source_type": "improvement_candidate"},
        )
    ).json()["items"]
    feedback_kinds = {item["artifact_kind"] for item in feedback_artifacts}
    assert {"training_sample", "eval_case", "agent_policy_candidate"} <= feedback_kinds

    policy_candidates = (
        await client.get(
            "/api/learning/candidates",
            params={"target_type": "agent", "target_id": "learning_agentization_judge"},
        )
    ).json()["items"]
    assert policy_candidates
    assert "候选生成策略" in policy_candidates[0]["title"]

    feedback_graph = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "feedback_event"})).json()
    assert any(
        edge["source"] == f"improvement_candidate:{candidate['id']}"
        and edge["target"].startswith("learning_event:")
        for edge in feedback_graph["edges"]
    )


@pytest.mark.asyncio
async def test_learning_self_audit_remediates_missing_agent_draft_and_reports_ai_gap(client):
    import app.database as db_mod
    from app.learning.models import ImprovementCandidate, LearningArtifact, LearningEvent
    from app.workbench.models import SkillStudioDraft

    async with db_mod.async_session_factory() as session:
        event = LearningEvent(
            id="le-self-audit-agent-source",
            event_type="sf.usage.hotspot",
            source_type="sf_usage_aggregate",
            source_id="legacy_agent_tool:7d",
            source_hash="legacy-agent-source",
            department="AI小组",
            redacted_summary="legacy_agent_tool 高频调用，需要 Agent 化。",
            quality_score=0.82,
            metadata_json={"tool": "legacy_agent_tool", "count": 5},
            created_at=now_bjt(),
            updated_at=now_bjt(),
        )
        artifact = LearningArtifact(
            id="la-self-audit-agent-source",
            event_id=event.id,
            artifact_kind="agent_creation_candidate",
            artifact_hash="legacy-agent-artifact",
            target_type="agent",
            target_id="legacy_agent_tool_agent",
            department="AI小组",
            title="Agent 化候选：legacy_agent_tool",
            summary="旧数据中已进入治理但缺少草稿。",
            content_json={
                "target_type": "agent",
                "target_id": "legacy_agent_tool_agent",
                "proposal": "补齐 legacy_agent_tool Agent 化流程。",
                "risk_level": "R2",
                "expected_impact": {"affected_modules": ["sf", "agent"]},
                "agent_blueprint": {
                    "name": "legacy_agent_tool 高频流程 Agent",
                    "goal": "把 legacy_agent_tool 高频操作变成可审核 Agent 流程。",
                    "trigger": "manual_review",
                    "workflow": ["读取学习事件", "dry-run 工具", "进入治理"],
                    "permissions": ["mcp:legacy_agent_tool:dry_run"],
                    "affected_modules": ["sf", "agent"],
                    "guardrails": ["不自动发布"],
                },
                "source_type": "agentization_judgement",
            },
            labels_json=["candidate", "agent"],
            quality_score=0.82,
            confidence=0.76,
            status="materialized",
            sink_type="improvement_candidate",
            sink_id="ic-self-audit-agent-source",
            policy_result_json={"action": "improvement_candidate", "sink_type": "candidate"},
            created_at=now_bjt(),
            updated_at=now_bjt(),
        )
        candidate = ImprovementCandidate(
            id="ic-self-audit-agent-source",
            source_artifact_id=artifact.id,
            target_type="agent",
            target_id="legacy_agent_tool_agent",
            department="AI小组",
            title="Agent 化候选：legacy_agent_tool",
            proposal="旧候选缺少治理草稿，应由自审自动补齐。",
            evidence_event_ids_json=[event.id],
            risk_level="R2",
            expected_impact_json={"affected_modules": ["sf", "agent"]},
            priority_score=0.82,
            status="reviewing",
            external_ref_json={"governance_queue": "learning_agent_review"},
            created_by="admin",
            updated_by="admin",
            created_at=now_bjt(),
            updated_at=now_bjt(),
        )
        session.add_all([event, artifact, candidate])
        await session.commit()

    result = (
        await client.post(
            "/api/learning/self-audit/run",
            json={"days": 7, "limit": 50, "auto_remediate": True},
        )
    ).json()
    assert result["governance"]["auto_remediate"] is True
    assert any(item["candidate_id"] == "ic-self-audit-agent-source" and item["action"] == "created_agent_draft" for item in result["remediated"])
    assert any(item["candidate_id"] == "ic-self-audit-agent-source" and item["status"] == "passed" for item in result["validations"])
    assert any(item["gap_key"] == "ai_config_missing" for item in result["created"])

    async with db_mod.async_session_factory() as session:
        refreshed = await session.get(ImprovementCandidate, "ic-self-audit-agent-source")
        assert refreshed is not None
        draft_id = (refreshed.external_ref_json or {}).get("agent_draft_id")
        assert str(draft_id).startswith("draft-agent-")
        assert (refreshed.external_ref_json or {}).get("agent_draft_validation", {}).get("status") == "passed"
        draft = await session.get(SkillStudioDraft, draft_id)
        assert draft is not None
        assert draft.generation_status == "ready"

    gaps = (
        await client.get(
            "/api/learning/candidates",
            params={"target_type": "ai_system", "target_id": "ai_config_missing"},
        )
    ).json()["items"]
    assert gaps
    assert gaps[0]["external_ref"]["governance_queue"] == "learning_ai_system_review"
    assert gaps[0]["external_ref"]["governance_task_id"].startswith("lgt_")

    summary = (await client.get("/api/learning/summary", params={"days": 7})).json()
    assert summary["self_iteration"]["agent_candidates"] >= 1
    assert summary["self_iteration"]["agent_drafts"] >= 1
    assert summary["self_iteration"]["draft_validation_passed"] >= 1
    assert summary["self_iteration"]["ai_system_gaps_open"] >= 1
    assert summary["self_iteration"]["health_score"] < 100

    drafted = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "drafted_as"})).json()
    assert any(
        edge["source"] == "improvement_candidate:ic-self-audit-agent-source"
        and edge["target"].startswith("agent_draft:draft-agent-")
        for edge in drafted["edges"]
    )


@pytest.mark.asyncio
async def test_learning_self_audit_uses_ai_to_create_system_gap_candidate(client, monkeypatch):
    get_config = AsyncMock(return_value={"model": "mock-self-audit"})
    llm = AsyncMock(return_value={
        "gaps": [
            {
                "gap_key": "missing_cost_guardrail",
                "gap_type": "cost_control",
                "title": "AI 自迭代缺口：缺少成本闸门",
                "summary": "自动发现和自审循环已有运行能力，但缺少面向模型调用成本的治理闸门。",
                "proposal": "增加自迭代运行的模型调用预算、成本告警和降级策略，并进入治理队列评审。",
                "risk_level": "R2",
                "priority": 0.83,
                "expected_impact": {"cost_control": "降低自迭代失控风险"},
            }
        ]
    })
    monkeypatch.setattr("app.learning.service.get_ai_config", get_config)
    monkeypatch.setattr("app.learning.service.call_llm", llm)

    result = (
        await client.post(
            "/api/learning/self-audit/run",
            json={"days": 7, "limit": 50, "auto_remediate": False, "use_ai": True},
        )
    ).json()
    assert result["ai_enabled"] is True
    assert result["governance"]["ai_used"] is True
    assert result["ai_suggestions"]
    assert result["ai_suggestions"][0]["gap_key"] == "ai_suggested:missing_cost_guardrail"
    assert get_config.await_count >= 1
    assert llm.await_count == 1

    candidates = (
        await client.get(
            "/api/learning/candidates",
            params={"target_type": "ai_system", "target_id": "ai_suggested_missing_cost_guardrail"},
        )
    ).json()["items"]
    assert candidates
    assert candidates[0]["external_ref"]["governance_queue"] == "learning_ai_system_review"
    assert candidates[0]["external_ref"]["governance_task_id"].startswith("lgt_")
    assert candidates[0]["expected_impact"]["ai_suggested"] is True
    assert "成本闸门" in candidates[0]["title"]


@pytest.mark.asyncio
async def test_learning_self_audit_detects_stale_governance_queue(client):
    import app.database as db_mod
    from app.common.cache import invalidate_page_cache
    from app.learning.models import ImprovementCandidate, LearningGovernanceTask

    stale_at = now_bjt() - timedelta(hours=30)
    async with db_mod.async_session_factory() as session:
        candidate = ImprovementCandidate(
            id="ic-stale-governance-agent",
            source_artifact_id=None,
            target_type="agent",
            target_id="stale_governance_agent",
            department="AI小组",
            title="Agent 治理任务超时样例",
            proposal="该候选用于验证治理队列超时后能进入 AI 自审缺口。",
            evidence_event_ids_json=[],
            risk_level="R2",
            expected_impact_json={"governance_sla": "需要超时监控"},
            priority_score=0.82,
            status="reviewing",
            external_ref_json={
                "governance_queue": "learning_agent_review",
                "governance_task_id": "lgt-stale-governance-agent",
                "no_auto_publish": True,
            },
            created_by="admin",
            updated_by="admin",
            created_at=stale_at,
            updated_at=stale_at,
        )
        task = LearningGovernanceTask(
            id="lgt-stale-governance-agent",
            candidate_id=candidate.id,
            queue_id="learning_agent_review",
            target_type="agent",
            target_id="stale_governance_agent",
            department="AI小组",
            title="待处理 Agent 治理任务",
            summary="该治理任务已超过 SLA，不能让自动循环停在人工队列。",
            status="pending",
            priority_score=0.82,
            payload_json={"no_auto_publish": True},
            created_by="admin",
            created_at=stale_at,
            updated_at=stale_at,
        )
        session.add_all([candidate, task])
        await session.commit()

    await invalidate_page_cache("learning")
    status = (await client.get("/api/learning/automation-status", params={"days": 7})).json()
    assert status["backlog"]["governance_tasks_by_status"]["pending"] >= 1
    assert status["backlog"]["governance_pending"] >= 1
    assert status["backlog"]["governance_stale"] >= 1
    assert status["backlog"]["governance_stale_threshold_hours"] == 24

    result = (
        await client.post(
            "/api/learning/self-audit/run",
            json={"days": 7, "limit": 50, "auto_remediate": False, "use_ai": False},
        )
    ).json()
    assert any(item["id"] == "lgt-stale-governance-agent" and item["is_stale"] is True for item in result["stale_governance_tasks"])
    assert any(item["gap_key"] == "governance_queue_backlog:learning_agent_review" for item in result["created"])

    gaps = (
        await client.get(
            "/api/learning/candidates",
            params={"target_type": "ai_system", "target_id": "governance_queue_backlog_learning_agent_review"},
        )
    ).json()["items"]
    assert gaps
    assert gaps[0]["external_ref"]["governance_queue"] == "learning_ai_system_review"
    assert gaps[0]["external_ref"]["governance_task_id"].startswith("lgt_")
    assert "治理队列处理超时" in gaps[0]["title"]


@pytest.mark.asyncio
async def test_learning_self_audit_detects_governance_task_without_decision_feedback(client):
    import app.database as db_mod
    from app.common.cache import invalidate_page_cache
    from app.learning.models import ImprovementCandidate, LearningGovernanceTask

    completed_at = now_bjt() - timedelta(hours=1)
    async with db_mod.async_session_factory() as session:
        candidate = ImprovementCandidate(
            id="ic-decisionless-governance",
            source_artifact_id=None,
            target_type="agent",
            target_id="decisionless_governance_agent",
            department="AI小组",
            title="缺少治理决策反馈的 Agent 候选",
            proposal="该候选用于验证治理任务终态必须回流 accepted/rejected/implemented 决策。",
            evidence_event_ids_json=[],
            risk_level="R2",
            expected_impact_json={"feedback_loop": "需要候选决策回流"},
            priority_score=0.81,
            status="reviewing",
            external_ref_json={
                "governance_queue": "learning_agent_review",
                "governance_task_id": "lgt-decisionless-governance",
                "no_auto_publish": True,
            },
            created_by="admin",
            updated_by="admin",
            created_at=completed_at,
            updated_at=completed_at,
        )
        task = LearningGovernanceTask(
            id="lgt-decisionless-governance",
            candidate_id=candidate.id,
            queue_id="learning_agent_review",
            target_type="agent",
            target_id="decisionless_governance_agent",
            department="AI小组",
            title="已完成但缺少候选决策的治理任务",
            summary="治理任务完成了，但没有 accepted/rejected/implemented 决策，学习闭环无法形成反馈样本。",
            status="completed",
            priority_score=0.81,
            payload_json={"last_status": "completed", "no_auto_publish": True},
            created_by="admin",
            created_at=completed_at,
            updated_at=completed_at,
            completed_at=completed_at,
        )
        session.add_all([candidate, task])
        await session.commit()

    await invalidate_page_cache("learning")
    status = (await client.get("/api/learning/automation-status", params={"days": 7})).json()
    assert status["backlog"]["governance_decisionless"] >= 1
    assert status["backlog"]["automation_stale"] is True

    result = (
        await client.post(
            "/api/learning/self-audit/run",
            json={"days": 7, "limit": 50, "auto_remediate": False, "use_ai": False},
        )
    ).json()
    assert any(
        item["id"] == "lgt-decisionless-governance"
        and item["candidate_status"] == "reviewing"
        for item in result["decisionless_governance_tasks"]
    )
    assert any(item["gap_key"] == "governance_task_missing_decision:lgt-decisionless-governance" for item in result["created"])

    gaps = (
        await client.get(
            "/api/learning/candidates",
            params={"target_type": "ai_system", "target_id": "governance_task_missing_decision_lgt_decisionless_governance"},
        )
    ).json()["items"]
    assert gaps
    assert gaps[0]["external_ref"]["governance_queue"] == "learning_ai_system_review"
    assert gaps[0]["external_ref"]["governance_task_id"].startswith("lgt_")
    assert "缺少候选决策反馈" in gaps[0]["title"]


@pytest.mark.asyncio
async def test_learning_self_audit_detects_failed_automation_runs(client):
    import app.database as db_mod
    from app.common.cache import invalidate_page_cache
    from app.learning.models import LearningAutomationRun

    failed_at = now_bjt() - timedelta(hours=2)
    async with db_mod.async_session_factory() as session:
        session.add(
            LearningAutomationRun(
                id="lar-failed-self-loop",
                trigger_type="scheduled",
                status="failed",
                days=7,
                limit=50,
                materialize=True,
                captured_json={},
                result_json={"error": "capture timeout"},
                error="capture timeout",
                metadata_json={"scope": "global"},
                created_by="learning_auto_flow",
                started_at=failed_at,
                finished_at=failed_at,
                created_at=failed_at,
                updated_at=failed_at,
            )
        )
        await session.commit()

    await invalidate_page_cache("learning")
    status = (await client.get("/api/learning/automation-status", params={"days": 7})).json()
    assert status["backlog"]["automation_runs_by_status"]["failed"] >= 1
    assert status["backlog"]["automation_failed"] >= 1

    result = (
        await client.post(
            "/api/learning/self-audit/run",
            json={"days": 7, "limit": 50, "auto_remediate": False, "use_ai": False},
        )
    ).json()
    assert result["automation_health"]["failed_runs"] >= 1
    assert any(item["gap_key"] == "learning_automation_run_failures" for item in result["created"])
    assert any(item["gap_key"] == "learning_automation_reconciler_stale" for item in result["created"])

    gaps = (
        await client.get(
            "/api/learning/candidates",
            params={"target_type": "ai_system", "target_id": "learning_automation_run_failures"},
        )
    ).json()["items"]
    assert gaps
    assert gaps[0]["external_ref"]["governance_queue"] == "learning_ai_system_review"
    assert gaps[0]["external_ref"]["governance_task_id"].startswith("lgt_")
    assert "自动循环运行失败" in gaps[0]["title"]
    first_gap_id = gaps[0]["id"]

    async with db_mod.async_session_factory() as session:
        session.add(
            LearningAutomationRun(
                id="lar-failed-self-loop-2",
                trigger_type="scheduled",
                status="failed",
                days=7,
                limit=50,
                materialize=True,
                captured_json={},
                result_json={"error": "materialize timeout"},
                error="materialize timeout",
                metadata_json={"scope": "global"},
                created_by="learning_auto_flow",
                started_at=now_bjt() - timedelta(minutes=30),
                finished_at=now_bjt() - timedelta(minutes=30),
                created_at=now_bjt() - timedelta(minutes=30),
                updated_at=now_bjt() - timedelta(minutes=30),
            )
        )
        await session.commit()

    rerun = (
        await client.post(
            "/api/learning/self-audit/run",
            json={"days": 7, "limit": 50, "auto_remediate": False, "use_ai": False},
        )
    ).json()
    assert any(
        item["gap_key"] == "learning_automation_run_failures" and item["idempotent"] is True
        for item in rerun["created"]
    )
    gap_candidates = (
        await client.get(
            "/api/learning/candidates",
            params={"target_type": "ai_system", "target_id": "learning_automation_run_failures"},
        )
    ).json()["items"]
    assert [item["id"] for item in gap_candidates] == [first_gap_id]


@pytest.mark.asyncio
async def test_learning_blocks_agent_implementation_when_draft_validation_fails(client):
    import json

    import app.database as db_mod
    from app.learning.models import ImprovementCandidate, LearningArtifact, LearningEvent
    from app.workbench.models import SkillStudioDraft

    async with db_mod.async_session_factory() as session:
        event = LearningEvent(
            id="le-bad-agent-draft-source",
            event_type="sf.usage.hotspot",
            source_type="sf_usage_aggregate",
            source_id="bad_agent_tool:7d",
            source_hash="bad-agent-source",
            department="AI小组",
            redacted_summary="bad_agent_tool 高频调用，需要 Agent 化。",
            quality_score=0.82,
            metadata_json={"tool": "bad_agent_tool", "count": 5},
            created_at=now_bjt(),
            updated_at=now_bjt(),
        )
        artifact = LearningArtifact(
            id="la-bad-agent-draft-source",
            event_id=event.id,
            artifact_kind="agent_creation_candidate",
            artifact_hash="bad-agent-artifact",
            target_type="agent",
            target_id="bad_agent_tool_agent",
            department="AI小组",
            title="Agent 化候选：bad_agent_tool",
            summary="候选草稿含外部副作用风险。",
            content_json={
                "target_type": "agent",
                "target_id": "bad_agent_tool_agent",
                "proposal": "补齐 bad_agent_tool Agent 化流程。",
                "risk_level": "R2",
                "source_type": "agentization_judgement",
            },
            labels_json=["candidate", "agent"],
            quality_score=0.82,
            confidence=0.76,
            status="materialized",
            sink_type="improvement_candidate",
            sink_id="ic-bad-agent-draft-source",
            policy_result_json={"action": "improvement_candidate", "sink_type": "candidate"},
            created_at=now_bjt(),
            updated_at=now_bjt(),
        )
        candidate = ImprovementCandidate(
            id="ic-bad-agent-draft-source",
            source_artifact_id=artifact.id,
            target_type="agent",
            target_id="bad_agent_tool_agent",
            department="AI小组",
            title="Agent 化候选：bad_agent_tool",
            proposal="该候选草稿应被静态校验阻断。",
            evidence_event_ids_json=[event.id],
            risk_level="R2",
            expected_impact_json={"affected_modules": ["sf", "agent"]},
            priority_score=0.82,
            status="reviewing",
            external_ref_json={"governance_queue": "learning_agent_review", "agent_draft_id": "draft-agent-bad"},
            created_by="admin",
            updated_by="admin",
            created_at=now_bjt(),
            updated_at=now_bjt(),
        )
        draft = SkillStudioDraft(
            id="draft-agent-bad",
            skill_id=None,
            branch="main",
            user_id="admin",
            source_message="坏草稿",
            intent_md="# intent\n",
            skill_md="---\nname: bad\n---\n# bad\n",
            policy_yaml="version: 1\nno_auto_publish: true\n",
            contract_json={
                "lineage": {"learning_candidate_id": "ic-bad-agent-draft-source"},
                "output": {"adapter": "learning_governance_queue"},
            },
            review_state_json={},
            extra_files={
                "scripts/main.py": "import requests\n\ndef main(payload=None):\n    requests.post('https://example.com')\n    return {'no_auto_publish': True}\n",
                "tests/test_main.py": "def test_placeholder():\n    assert True\n",
                "learning-lineage.json": json.dumps({"learning_candidate_id": "ic-bad-agent-draft-source", "no_auto_publish": True}),
            },
            generation_status="ready",
        )
        session.add_all([event, artifact, candidate, draft])
        await session.commit()

    accepted = (
        await client.post(
            "/api/learning/candidates/ic-bad-agent-draft-source/accept",
            json={"reason": "业务确认需要，但草稿必须先过校验。"},
        )
    ).json()
    assert accepted["status"] == "accepted"
    assert accepted["external_ref"]["agent_draft_validation"]["status"] == "failed"
    assert accepted["external_ref"]["implementation_status"] == "blocked_by_agent_draft_validation"
    assert "implementation_queue" not in accepted["external_ref"]

    gaps = (
        await client.get(
            "/api/learning/candidates",
            params={"target_type": "ai_system", "q": "阻断实施"},
        )
    ).json()["items"]
    assert gaps
    assert gaps[0]["external_ref"]["governance_queue"] == "learning_ai_system_review"
    assert gaps[0]["external_ref"]["governance_task_id"].startswith("lgt_")

    implementation_graph = (
        await client.get(
            "/api/learning/flow-graph",
            params={"days": 7, "relation": "ready_for_implementation"},
        )
    ).json()
    assert not any(edge["source"] == "improvement_candidate:ic-bad-agent-draft-source" for edge in implementation_graph["edges"])


@pytest.mark.asyncio
async def test_learning_knowledge_query_document_ids_create_lineage_and_gap_candidate(client):
    import app.database as db_mod
    from app.knowledge.models import KnowledgeQueryLog
    from app.learning.service import capture_knowledge_query

    async with db_mod.async_session_factory() as session:
        used = KnowledgeQueryLog(
            kb_id="kb-learning-flow",
            department="AI小组",
            user_id="admin",
            query="五星价格力 SOP",
            mode="context",
            top_k=3,
            result_count=2,
            context_json={"document_ids": ["kdoc-a", "kdoc-b", "kdoc-a"], "modality": "text"},
            retrieval_backend="lightrag",
            lightrag_mode="hybrid",
            source_trace_json={},
            created_at=now_bjt(),
        )
        missing = KnowledgeQueryLog(
            kb_id="kb-learning-flow",
            department="AI小组",
            user_id="admin",
            query="没有沉淀过的运营口径",
            mode="search",
            top_k=3,
            result_count=0,
            context_json={"document_ids": [], "modality": "text"},
            retrieval_backend="lightrag",
            lightrag_mode="hybrid",
            source_trace_json={},
            created_at=now_bjt(),
        )
        session.add_all([used, missing])
        await session.flush()
        await capture_knowledge_query(session, used)
        await capture_knowledge_query(session, missing)
        await session.commit()

    graph = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "used_as_context"})).json()
    used_edges = [edge for edge in graph["edges"] if edge["relation"] == "used_as_context"]
    assert {edge["source"] for edge in used_edges} >= {"knowledge_document:kdoc-a", "knowledge_document:kdoc-b"}
    assert len([edge for edge in used_edges if edge["source"] == "knowledge_document:kdoc-a"]) == 1
    assert all(edge["target"].startswith("knowledge_query:") for edge in used_edges)

    candidates = (
        await client.get(
            "/api/learning/candidates",
            params={"target_type": "knowledge", "q": "没有沉淀过的运营口径"},
        )
    ).json()["items"]
    assert candidates
    assert candidates[0]["target_type"] == "knowledge"
    assert candidates[0]["risk_level"] == "R1"
    assert "补齐该问题相关的部门知识" in candidates[0]["proposal"]

    review = (await client.post(f"/api/learning/candidates/{candidates[0]['id']}/create-review")).json()
    assert review["candidate"]["status"] == "reviewing"
    assert review["governance_queue"] == "learning_knowledge_review"
    assert review["governance_task_id"].startswith("lgt_")
    assert review["candidate"]["external_ref"]["governance_task_id"] == review["governance_task_id"]

    reviewed = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "reviewed_by"})).json()
    assert any(
        edge["source"] == f"improvement_candidate:{candidates[0]['id']}"
        and edge["target"] == "governance_queue:learning_knowledge_review"
        for edge in reviewed["edges"]
    )


@pytest.mark.asyncio
async def test_learning_agent_thread_links_context_model_tools_and_skill_candidate(client):
    import app.database as db_mod
    from app.learning.service import capture_agent_thread

    user = MagicMock()
    user.id = "agent-user"
    user.role = "admin"
    user.department = "AI小组"
    user.can_view_all = True

    async with db_mod.async_session_factory() as session:
        await capture_agent_thread(
            session,
            user=user,
            thread_id="uagent-user-flow-1",
            message="每天生成价格力复盘并推送运营群",
            mode="publish",
            status="completed",
            state={
                "knowledge_context": {
                    "document_ids": ["kdoc-agent-a"],
                    "sources": [{"document_id": "kdoc-agent-b", "score": 0.91, "title": "价格力 SOP"}],
                },
                "model_context": {"deployment_id": "dep-agent-1"},
                "tool_calls": [{"name": "tmall_item_reviews"}],
                "tools": ["knowledge.search"],
                "skill_id": "agent-base-skill",
                "contract": {
                    "goal": "每天生成价格力复盘",
                    "trigger": {"type": "cron"},
                    "output": {"adapter": "dingtalk_card"},
                    "risks": {"level": "R2", "department": "AI小组"},
                    "permissions": [{"action": "read_data"}],
                },
                "skill": {
                    "meta": {"name": "price_power_daily", "department": "AI小组", "description": "价格力复盘"},
                    "goal": "每天生成价格力复盘",
                    "rules": [{"id": "step_1"}],
                    "test_cases": [{"name": "正常场景"}],
                    "scripts": {"main.py": "print('should not leak')"},
                },
                "gate": {"can_publish": False},
            },
        )
        await session.commit()

    used = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "used_as_context"})).json()
    used_edges = [edge for edge in used["edges"] if edge["target"] == "agent_thread:uagent-user-flow-1"]
    assert {edge["source"] for edge in used_edges} >= {"knowledge_document:kdoc-agent-a", "knowledge_document:kdoc-agent-b"}

    model_edges = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "used_as_model"})).json()["edges"]
    assert any(edge["source"] == "model_deployment:dep-agent-1" and edge["target"] == "agent_thread:uagent-user-flow-1" for edge in model_edges)

    skill_edges = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "used_skill"})).json()["edges"]
    assert any(edge["source"] == "skill:agent-base-skill" and edge["target"] == "agent_thread:uagent-user-flow-1" for edge in skill_edges)

    tool_edges = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "called_tool"})).json()["edges"]
    assert any(edge["source"] == "agent_tool:tmall_item_reviews" and edge["target"] == "agent_thread:uagent-user-flow-1" for edge in tool_edges)

    candidates = (
        await client.get(
            "/api/learning/candidates",
            params={"target_type": "skill", "q": "Agent 生成 Skill 候选"},
        )
    ).json()["items"]
    assert candidates
    assert candidates[0]["target_type"] == "skill"
    assert candidates[0]["target_id"] == "price_power_daily"

    journeys = (
        await client.get(
            "/api/learning/flow-journeys",
            params={"days": 7, "source_type": "agent_thread", "target_type": "skill"},
        )
    ).json()
    assert journeys["items"]
    steps = journeys["items"][0]["steps"]
    assert "candidate" in {step["stage"] for step in steps}
    assert "candidate_open" in journeys["items"][0]["blockers"]
    assert "should not leak" not in str(journeys)


@pytest.mark.asyncio
async def test_learning_todo_decision_feedback_enters_training_and_candidate_without_raw_payload(client):
    from app.auth.models import User
    import app.database as db_mod
    from app.todos.models import AITodo, DecisionRequest

    async with db_mod.async_session_factory() as session:
        session.add(User(
            id="admin",
            username="admin",
            name="管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
            state="active",
        ))
        session.add(
            Skill(
                id="todo-feedback-skill",
                name="待办反馈闭环 Skill",
                display_name="待办反馈闭环 Skill",
                department="AI小组",
                status="active",
                visibility="department",
                approval_level=0,
            )
        )
        session.add(
            ExecutionRun(
                id="run-todo-feedback-1",
                skill_id="todo-feedback-skill",
                trigger_type="manual",
                run_mode="manual_real",
                status="completed",
                summary="待办反馈闭环来源运行。",
                started_at=now_bjt(),
                completed_at=now_bjt(),
            )
        )
        request = DecisionRequest(
            id="dr-todo-feedback-1",
            source_type="execution",
            source_id="run-todo-feedback-1",
            skill_id="todo-feedback-skill",
            run_id="run-todo-feedback-1",
            kind="review",
            title="请确认是否执行价格力调整",
            summary="Skill 建议调整价格力，但需要审批。",
            payload={
                "priority": "P1",
                "item_id": "item-10001",
                "secret_token": "secret-token-value-should-not-leak",
                "raw_customer": {"phone": "13800000000"},
            },
            decision_mode="any_of",
            aggregate_status="completed",
            aggregate_decision="rejected",
            sla_at=now_bjt(),
            completed_at=now_bjt(),
        )
        session.add(request)
        await session.flush()
        todo = AITodo(
            request_id=request.id,
            kind="review",
            assignee="admin",
            status="rejected",
            decided_at=now_bjt(),
            decided_by="admin",
            decision_reason="审批人指出：缺少价格力证据，暂不执行。",
            decision_channel="web",
        )
        session.add(todo)
        await session.commit()

    res = await client.post("/api/learning/events/backfill", json={"days": 7, "limit": 50, "materialize": False})
    assert res.status_code == 200, res.text
    assert res.json()["captured"]["ai_todo"] >= 1

    events = (await client.get("/api/learning/events", params={"source_type": "ai_todo", "days": 7})).json()["items"]
    assert events
    assert events[0]["event_type"] == "decision.feedback"

    artifact_payload = (
        await client.get(
            "/api/learning/artifacts",
            params={"source_type": "ai_todo", "skill_id": "todo-feedback-skill", "days": 7},
        )
    ).json()
    assert artifact_payload["governance"]["raw_payload_returned"] is False
    assert artifact_payload["items"]
    kinds = {item["artifact_kind"] for item in artifact_payload["items"]}
    assert {"training_sample", "eval_case", "skill_improvement_candidate"} <= kinds
    assert all("content" not in item for item in artifact_payload["items"])
    assert "secret-token-value-should-not-leak" not in str(artifact_payload)

    manifest = (await client.get("/api/learning/training-manifest", params={"skill_id": "todo-feedback-skill"})).json()
    assert manifest["sample_total"] >= 2
    assert manifest["governance"]["raw_payload_returned"] is False

    from app.database import async_session_factory
    from app.learning.models import LearningArtifact
    from app.learning.service import ensure_training_candidates_from_learning_samples
    from app.training.models import TrainingJob
    from sqlalchemy import select

    async with async_session_factory() as session:
        materialized_samples = (
            await session.execute(
                select(LearningArtifact)
                .where(LearningArtifact.skill_id == "todo-feedback-skill")
                .where(LearningArtifact.artifact_kind.in_(["training_sample", "eval_case"]))
                .where(LearningArtifact.status == "materialized")
                .where(LearningArtifact.sink_type == "training_sample")
            )
        ).scalars().all()
        assert len(materialized_samples) >= 2
        yesterday = now_bjt() - timedelta(days=1)
        for sample in materialized_samples:
            sample.created_at = yesterday
            sample.updated_at = yesterday
        user = await session.get(User, "admin")
        result = await ensure_training_candidates_from_learning_samples(
            session,
            user,
            days=7,
            min_sample_count=2,
            max_skills=1,
        )
        await session.commit()

    assert result["created"]
    training_job_id = result["created"][0]["training_job_id"]
    async with async_session_factory() as session:
        job = await session.get(TrainingJob, training_job_id)
    assert job is not None
    assert job.target_skill_id == "todo-feedback-skill"
    assert job.status == "awaiting_review"
    assert job.spec_json["deployment"]["auto_request"] is True

    candidates = (
        await client.get(
            "/api/learning/candidates",
            params={"target_type": "skill", "skill_id": "todo-feedback-skill", "q": "待办驳回"},
        )
    ).json()["items"]
    assert candidates
    assert candidates[0]["target_id"] == "todo-feedback-skill"


@pytest.mark.asyncio
async def test_learning_maintenance_todo_decision_is_capture_only(client):
    import app.database as db_mod
    from app.todos.models import AITodo, DecisionRequest

    async with db_mod.async_session_factory() as session:
        session.add(
            Skill(
                id="todo-cleanup-skill",
                name="待办清理闭环 Skill",
                display_name="待办清理闭环 Skill",
                department="AI小组",
                status="active",
                visibility="department",
                approval_level=0,
            )
        )
        session.add(
            ExecutionRun(
                id="run-todo-cleanup-1",
                skill_id="todo-cleanup-skill",
                trigger_type="manual",
                run_mode="manual_real",
                status="completed",
                summary="待办清理来源运行。",
                started_at=now_bjt(),
                completed_at=now_bjt(),
            )
        )
        request = DecisionRequest(
            id="dr-todo-cleanup-1",
            source_type="execution",
            source_id="run-todo-cleanup-1",
            skill_id="todo-cleanup-skill",
            run_id="run-todo-cleanup-1",
            kind="review",
            title="清理无效待办",
            summary="维护动作归档旧待办，不代表业务反馈。",
            payload={"priority": "P3", "item_id": "cleanup-10001"},
            decision_mode="any_of",
            aggregate_status="completed",
            aggregate_decision="rejected",
            sla_at=now_bjt(),
            completed_at=now_bjt(),
        )
        session.add(request)
        await session.flush()
        session.add(
            AITodo(
                request_id=request.id,
                kind="review",
                assignee="admin",
                status="rejected",
                decided_at=now_bjt(),
                decided_by="admin",
                decision_reason="data_cleanup_2026_06_11: superseded invalid run",
                decision_channel="data_cleanup",
            )
        )
        await session.commit()

    res = await client.post("/api/learning/events/backfill", json={"days": 7, "limit": 50, "materialize": True})
    assert res.status_code == 200, res.text

    events = (
        await client.get(
            "/api/learning/events",
            params={"source_type": "ai_todo", "skill_id": "todo-cleanup-skill", "days": 7},
        )
    ).json()["items"]
    cleanup_event = next(item for item in events if item["source_id"])
    assert cleanup_event["event_type"] == "decision.feedback"
    assert cleanup_event["policy_result"]["action"] == "capture_only"
    assert cleanup_event["metadata"]["maintenance_decision"] is True

    artifacts = (
        await client.get(
            "/api/learning/artifacts",
            params={"source_type": "ai_todo", "skill_id": "todo-cleanup-skill", "days": 7},
        )
    ).json()["items"]
    assert artifacts == []


@pytest.mark.asyncio
async def test_learning_training_and_model_deployment_flow_back_to_skill(client):
    import app.database as db_mod
    import json

    from app.execution.models import OpenClawInstance
    from app.learning.models import ImprovementCandidate
    from app.learning.service import capture_model_deployment, capture_training_job
    from app.training.models import TrainingJob, TrainingModelDeployment

    async with db_mod.async_session_factory() as session:
        session.add(
            Skill(
                id="train-flow-skill",
                name="训练闭环 Skill",
                display_name="训练闭环 Skill",
                department="AI小组",
                status="active",
                visibility="department",
                approval_level=0,
            )
        )
        session.add(
            ExecutionRun(
                id="run-training-flow-1",
                skill_id="train-flow-skill",
                trigger_type="manual",
                run_mode="manual_real",
                status="completed",
                summary="训练闭环来源运行。",
                started_at=now_bjt(),
                completed_at=now_bjt(),
            )
        )
        session.add(
            OpenClawInstance(
                id="agent-training-flow-1",
                name="训练闭环 Agent",
                department="AI小组",
                gateway_url="ws://agent-training-flow-1",
                reload_hook_url="",
                reload_token="",
                agent_type="aiclaw",
                agent_purpose="training",
                bridge_gateway_kind="openclaw",
                bridge_capabilities_json=json.dumps({
                    "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                    "training": {
                        "gateway": True,
                        "supported_tasks": ["lora", "eval"],
                        "gpu_count": 1,
                        "worker_count": 1,
                    },
                }),
                is_active=True,
            )
        )
        candidate = ImprovementCandidate(
            id="ic-training-flow-1",
            source_artifact_id=None,
            target_type="skill",
            target_id="train-flow-skill",
            department="AI小组",
            skill_id="train-flow-skill",
            run_id="run-training-flow-1",
            title="训练闭环候选",
            proposal="用反馈样本训练小模型。",
            evidence_event_ids_json=[],
            risk_level="R2",
            expected_impact_json={},
            priority_score=0.8,
            status="accepted",
        )
        job = TrainingJob(
            id="tj-training-flow-1",
            title="训练闭环任务",
            department="AI小组",
            created_by="admin",
            status="completed",
            job_type="lora",
            target_skill_id="train-flow-skill",
            target_gateway_id="agent-training-flow-1",
            dataset_ref="learning-artifacts://train-flow-skill/training/latest",
            objective="用反馈样本提升训练闭环 Skill。",
            risk_level="R2",
            gateway_payload_json={
                "job_id": "tj-training-flow-1",
                "control": {
                    "agent_id": "agent-training-flow-1",
                    "agent_purpose": "training",
                },
                "dataset_package": {
                    "format": "skillforge_sft_jsonl_v1",
                    "source": "learning_artifacts",
                    "dataset_ref": "learning-artifacts://train-flow-skill/training/latest",
                    "target_skill_id": "train-flow-skill",
                    "manifest_hash": "manifest-training-flow-1",
                    "sample_count": 1,
                    "train_count": 1,
                    "eval_count": 0,
                    "input_contract": {
                        "schema": "skillforge_sft_jsonl_v1",
                        "required_fields": ["instruction", "input", "output"],
                        "lineage_field": "metadata",
                    },
                    "output_contract": {
                        "result_mode": "bridge_op_callback",
                        "callback_required": True,
                        "requires_artifact_sha256": True,
                    },
                    "lineage": {
                        "source": "learning_artifacts",
                        "training_job_id": "tj-training-flow-1",
                        "target_skill_id": "train-flow-skill",
                        "learning_artifact_ids": ["la-training-flow-sample-1"],
                        "manifest_hash": "manifest-training-flow-1",
                    },
                    "raw_payload_returned": False,
                    "samples": [
                        {
                            "id": "la-training-flow-sample-1",
                            "split": "train",
                            "instruction": "训练输入不应进入学习流图",
                            "input": "raw input should not appear",
                            "output": "raw output should not appear",
                            "metadata": {
                                "source_type": "todo_feedback",
                                "source_channel": "dingtalk",
                                "input_sha256": "a" * 64,
                                "output_sha256": "b" * 64,
                                "sample_sha256": "c" * 64,
                            },
                        }
                    ],
                },
            },
            spec_json={
                "source": "learning_loop_candidate",
                "source_skill_id": "train-flow-skill",
                "dataset": {
                    "ref": "learning-artifacts://train-flow-skill/training/latest",
                    "learning_artifact_ids": ["la-training-flow-sample-1"],
                },
                "lineage": {
                    "source": "execution_run",
                    "skill_id": "train-flow-skill",
                    "run_id": "run-training-flow-1",
                    "learning_candidate_id": candidate.id,
                },
            },
        )
        previous = TrainingModelDeployment(
            id="md-training-flow-prev",
            job_id=job.id,
            department="AI小组",
            model_family="train-flow-skill:learning_loop_improvement",
            artifact_id="artifact-prev",
            artifact_ref_json={"sha256": "prev"},
            target_skill_ids_json=["train-flow-skill"],
            status="rolled_back",
            rollout_percent=100,
            requested_by="admin",
        )
        review_request = TrainingModelDeployment(
            id="md-training-flow-review",
            job_id=job.id,
            department="AI小组",
            model_family="train-flow-skill:learning_loop_candidate",
            artifact_id="artifact-review",
            artifact_ref_json={"sha256": "c" * 64, "uri": "artifact://training-flow/review"},
            target_skill_ids_json=["train-flow-skill"],
            status="awaiting_review",
            rollout_percent=10,
            requested_by="training_automation",
            request_reason="训练评估通过后提交部署审核。",
        )
        deployment = TrainingModelDeployment(
            id="md-training-flow-active",
            job_id=job.id,
            department="AI小组",
            model_family="train-flow-skill:learning_loop_improvement",
            artifact_id="artifact-active",
            artifact_ref_json={"sha256": "active"},
            target_skill_ids_json=["train-flow-skill"],
            status="active",
            rollout_percent=100,
            rollback_to=previous.id,
            requested_by="admin",
            activated_at=now_bjt(),
        )
        session.add_all([candidate, job, previous, review_request, deployment])
        await session.flush()
        await capture_training_job(session, job)
        await capture_model_deployment(session, deployment)
        await capture_model_deployment(session, previous)
        await session.commit()

    trained = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "trained_from"})).json()["edges"]
    assert any(edge["source"] == "run:run-training-flow-1" and edge["target"] == "training_job:tj-training-flow-1" for edge in trained)
    assert any(edge["source"] == "improvement_candidate:ic-training-flow-1" and edge["target"] == "training_job:tj-training-flow-1" for edge in trained)
    assert any(edge["source"] == "skill:train-flow-skill" and edge["target"] == "training_job:tj-training-flow-1" for edge in trained)
    assert any(edge["source"] == "learning_artifact:la-training-flow-sample-1" and edge["target"] == "training_job:tj-training-flow-1" for edge in trained)

    controlled = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "controlled_training"})).json()["edges"]
    control_edge = next(
        edge
        for edge in controlled
        if edge["source"] == "agent:agent-training-flow-1"
        and edge["target"] == "training_job:tj-training-flow-1"
    )
    contract = control_edge["metadata"]["contract"]
    assert contract["complete"] is True
    assert contract["submission_ready"] is True
    assert contract["lifecycle_ready"] is True
    assert contract["missing_ops"] == []
    assert contract["input_channels"] == ["training_job.gateway_payload", "learning_artifacts.dataset_package"]
    assert contract["output_channels"] == ["training_job.gateway_result", "training_artifact_refs", "training_metrics"]
    dataset_package = control_edge["metadata"]["dataset_package"]
    assert dataset_package["manifest_hash"] == "manifest-training-flow-1"
    assert dataset_package["sample_count"] == 1
    assert dataset_package["raw_payload_returned"] is False
    assert dataset_package["samples"] == "[REDACTED]"
    assert dataset_package["sample_fingerprints"] == [
        {
            "id": "la-training-flow-sample-1",
            "split": "train",
            "source_type": "todo_feedback",
            "source_channel": "dingtalk",
            "input_sha256": "a" * 64,
            "output_sha256": "b" * 64,
            "sample_sha256": "c" * 64,
        }
    ]
    assert "raw input should not appear" not in json.dumps(dataset_package, ensure_ascii=False)

    events = (
        await client.get(
            "/api/learning/events",
            params={"days": 7, "source_type": "training_job", "event_type": "training.job.completed"},
        )
    ).json()["items"]
    event = next(item for item in events if item["source_id"] == "tj-training-flow-1")
    assert event["metadata"]["training_agent"]["agent_id"] == "agent-training-flow-1"
    assert event["metadata"]["training_agent"]["contract"]["flow_traceable"] is True
    assert event["metadata"]["dataset_package"]["manifest_hash"] == "manifest-training-flow-1"
    assert event["metadata"]["dataset_package"]["samples"] == "[REDACTED]"
    assert event["metadata"]["deployment_review"]["requested"] is True
    assert event["metadata"]["deployment_review"]["latest"]["deployment_id"] == "md-training-flow-review"
    assert event["metadata"]["deployment_review"]["latest"]["review_required"] is True
    assert event["metadata"]["deployment_review"]["governance"]["no_auto_approval"] is True
    assert "raw output should not appear" not in json.dumps(event["metadata"]["dataset_package"], ensure_ascii=False)

    requested_review = (
        await client.get(
            "/api/learning/flow-graph",
            params={"days": 7, "relation": "requested_deployment_review"},
        )
    ).json()["edges"]
    review_edge = next(
        edge
        for edge in requested_review
        if edge["source"] == "training_job:tj-training-flow-1"
        and edge["target"] == "model_deployment:md-training-flow-review"
    )
    assert review_edge["metadata"]["review_required"] is True
    assert review_edge["metadata"]["auto_approval"] is False
    assert review_edge["metadata"]["governance"]["approval_state_machine"] == "training_model_deployment"

    deployed = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "deployed_to"})).json()["edges"]
    assert any(edge["source"] == "training_job:tj-training-flow-1" and edge["target"] == "model_deployment:md-training-flow-active" for edge in deployed)
    assert any(edge["source"] == "model_deployment:md-training-flow-active" and edge["target"] == "skill:train-flow-skill" for edge in deployed)

    rollback_target = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "rollback_target"})).json()["edges"]
    assert any(
        edge["source"] == "model_deployment:md-training-flow-active"
        and edge["target"] == "model_deployment:md-training-flow-prev"
        for edge in rollback_target
    )

    candidates = (await client.get("/api/learning/candidates", params={"target_type": "training"})).json()["items"]
    assert any(item["target_id"] == "tj-training-flow-1" for item in candidates)

    journeys = (
        await client.get(
            "/api/learning/flow-journeys",
            params={"days": 7, "skill_id": "train-flow-skill", "source_type": "training_job"},
        )
    ).json()
    training_journey = next(
        item for item in journeys["items"] if item["source_id"] == "tj-training-flow-1"
    )
    control_step = next(step for step in training_journey["steps"] if step["action"] == "controlled_training")
    assert control_step["stage"] == "control"
    assert control_step["payload"]["agent_id"] == "agent-training-flow-1"
    assert control_step["payload"]["contract"]["flow_traceable"] is True
    assert control_step["payload"]["dataset_package"]["manifest_hash"] == "manifest-training-flow-1"
    assert control_step["payload"]["dataset_package"]["sample_fingerprints"][0]["sample_sha256"] == "c" * 64
    assert control_step["payload"]["dataset_package"]["samples"] == "[REDACTED]"
    review_step = next(
        step for step in training_journey["steps"] if step["action"] == "requested_deployment_review"
    )
    assert review_step["stage"] == "review"
    assert review_step["payload"]["deployment_id"] == "md-training-flow-review"
    assert review_step["payload"]["governance"]["approval_state_machine"] == "training_model_deployment"


@pytest.mark.asyncio
async def test_learning_pulse_returns_five_stage_details(client):
    import app.database as db_mod

    from app.execution.models import OpenClawInstance
    from app.knowledge.models import DepartmentKnowledgeBase, KnowledgeChunk, KnowledgeDocument
    from app.learning.models import LearningArtifact, LearningEvent, LearningFlowEdge, LearningIngestionJob
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment

    async with db_mod.async_session_factory() as session:
        indexed_at = now_bjt() - timedelta(minutes=1)
        session.add(
            Skill(
                id="pulse-flow-skill",
                name="脉动学习流 Skill",
                display_name="脉动学习流 Skill",
                department="AI小组",
                status="active",
                visibility="department",
                approval_level=0,
            )
        )
        kb = DepartmentKnowledgeBase(
            id="kb-pulse-flow",
            department="AI小组",
            name="脉动学习流知识库",
            status="active",
        )
        gateway = OpenClawInstance(
            id="gw-pulse-flow",
            name="脉动训练网关",
            department="AI小组",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["lora", "eval"],
                    "gpu_count": 1,
                    "worker_count": 3,
                },
            }),
            is_active=True,
        )
        event = LearningEvent(
            id="le-pulse-flow-1",
            event_type="skill.run.completed",
            source_type="execution_run",
            source_id="run-pulse-flow-1",
            source_hash="pulse-flow-event",
            department="AI小组",
            skill_id="pulse-flow-skill",
            run_id="run-pulse-flow-1",
            redacted_summary="原始运行数据进入学习流。",
            status="captured",
            quality_score=0.9,
            metadata_json={"source": "pytest"},
        )
        report = LearningArtifact(
            id="la-pulse-flow-report",
            event_id=event.id,
            artifact_kind="report_summary",
            artifact_hash="pulse-flow-report",
            target_type="knowledge",
            target_id="pulse-flow-skill",
            department="AI小组",
            skill_id="pulse-flow-skill",
            run_id="run-pulse-flow-1",
            title="清晰化报告",
            summary="已经提炼为可复用结论。",
            content_json={"summary": "清晰化后的结论"},
            labels_json=["pulse"],
            quality_score=0.86,
            confidence=0.8,
            status="materialized",
            sink_type="knowledge_document",
            sink_id="doc-pulse-flow-1",
        )
        report_duplicate = LearningArtifact(
            id="la-pulse-flow-report-2",
            event_id=event.id,
            artifact_kind="report_summary",
            artifact_hash="pulse-flow-report-2",
            target_type="knowledge",
            target_id="pulse-flow-skill",
            department="AI小组",
            skill_id="pulse-flow-skill",
            run_id="run-pulse-flow-1",
            title="清晰化报告",
            summary="同类报告资产。",
            content_json={"summary": "同类清洗资产"},
            labels_json=["pulse"],
            quality_score=0.84,
            confidence=0.78,
            status="materialized",
            sink_type="knowledge_document",
            sink_id="doc-pulse-flow-1",
        )
        doc = KnowledgeDocument(
            id="doc-pulse-flow-1",
            kb_id=kb.id,
            department="AI小组",
            title="清晰化报告",
            source_type="report",
            source_ref=f"learning:{report.id}",
            source_hash="3" * 64,
            status="active",
            index_status="indexed",
            index_version=3,
            source_version="pulse-flow-v1",
            content="已经提炼为可复用结论。",
            content_hash="4" * 64,
            chunk_count=1,
            token_count=6,
            indexed_at=indexed_at,
        )
        chunk = KnowledgeChunk(
            document_id=doc.id,
            kb_id=kb.id,
            department="AI小组",
            chunk_index=0,
            content="已经提炼为可复用结论。",
            content_hash="5" * 64,
            token_count=6,
            embedding_json=[0.1, 0.2, 0.3],
        )
        sample = LearningArtifact(
            id="la-pulse-flow-sample",
            event_id=event.id,
            artifact_kind="training_sample",
            artifact_hash="pulse-flow-sample",
            target_type="training",
            target_id="pulse-flow-skill",
            department="AI小组",
            skill_id="pulse-flow-skill",
            run_id="run-pulse-flow-1",
            title="训练样本",
            summary="已形成微调样本。",
            content_json={
                "input_sha256": "1" * 64,
                "output_sha256": "2" * 64,
                "raw_text": "raw input should not leak",
            },
            labels_json=["training"],
            quality_score=0.91,
            confidence=0.84,
            status="materialized",
            sink_type="training_sample",
            sink_id="la-pulse-flow-sample",
        )
        job = TrainingJob(
            id="tj-pulse-flow-1",
            title="脉动微调任务",
            department="AI小组",
            created_by="admin",
            status="running",
            job_type="lora",
            target_skill_id="pulse-flow-skill",
            target_gateway_id="gw-pulse-flow",
            dataset_ref="learning-artifacts://pulse-flow-skill/training/latest",
            objective="验证学习流脉动五段聚合。",
            risk_level="R2",
            spec_json={
                "dataset": {
                    "ref": "learning-artifacts://pulse-flow-skill/training/latest",
                    "manifest_hash": "manifest-pulse-flow",
                    "sample_total": 1,
                    "learning_artifact_ids": ["la-pulse-flow-sample"],
                },
                "deployment": {"model_family": "pulse-flow-skill:lora"},
                "lineage": {"source": "learning_artifacts"},
            },
        )
        running_task = TrainingJobTask(
            job_id=job.id,
            gateway_id="gw-pulse-flow",
            worker_id="worker-pulse-flow",
            status="running",
            progress=0.5,
            metrics_json={"train_loss": 0.42},
            created_at=now_bjt() - timedelta(minutes=5),
            updated_at=now_bjt(),
        )
        eval_task = TrainingJobTask(
            job_id=job.id,
            gateway_id="gw-pulse-flow",
            worker_id="worker-pulse-flow",
            status="completed",
            progress=1,
            metrics_json={"passed": True, "win_rate": 1.0, "artifact_sha256": "a" * 64},
            created_at=now_bjt() - timedelta(minutes=12),
            updated_at=now_bjt() - timedelta(minutes=2),
        )
        eval_task.metrics_json["gateway_result"] = {
            "artifacts": [
                {
                    "id": "artifact-pulse-flow",
                    "uri": "artifact://pulse-flow/model",
                    "sha256": "a" * 64,
                }
            ]
        }
        deployment = TrainingModelDeployment(
            id="md-pulse-flow-active",
            job_id=job.id,
            department="AI小组",
            model_family="pulse-flow-skill:lora",
            artifact_id="artifact-pulse-flow",
            artifact_ref_json={"sha256": "a" * 64, "uri": "artifact://pulse-flow/model"},
            target_skill_ids_json=["pulse-flow-skill"],
            status="active",
            rollout_percent=100,
            requested_by="admin",
            activated_at=now_bjt(),
        )
        application_edge = LearningFlowEdge(
            from_type="model_deployment",
            from_id=deployment.id,
            to_type="run",
            to_id="run-pulse-flow-1",
            relation="used_as_model",
            department="AI小组",
            skill_id="pulse-flow-skill",
            run_id="run-pulse-flow-1",
            metadata_json={"result": "analysis_output_used_model"},
        )
        ingestion = LearningIngestionJob(
            job_id="lij-pulse-flow-sample",
            event_id=event.id,
            artifact_id=sample.id,
            sink_type="training_sample",
            sink_id=sample.id,
            department="AI小组",
            skill_id="pulse-flow-skill",
            action="materialize",
            status="completed",
            attempts=1,
            policy_json={"reason": "pytest"},
            created_by="admin",
            started_at=now_bjt() - timedelta(minutes=4),
            finished_at=now_bjt() - timedelta(minutes=3),
        )
        session.add_all([kb, gateway, event, report, report_duplicate, doc, sample, job, running_task, eval_task, deployment, application_edge, ingestion])
        await session.flush()
        session.add(chunk)
        await session.commit()

    res = await client.get("/api/learning/pulse", params={"days": 7, "skill_id": "pulse-flow-skill"})
    assert res.status_code == 200, res.text
    payload = res.json()
    modules = {item["key"]: item for item in payload["modules"]}
    assert list(modules) == [
        "raw_inputs",
        "clarified_data",
        "finetuning",
        "test_results",
        "application_outputs",
    ]
    assert payload["governance"]["raw_payload_returned"] is False
    assert modules["raw_inputs"]["metrics"][0]["value"] >= 1
    assert modules["raw_inputs"]["total_count"] >= 1
    assert modules["raw_inputs"]["returned_count"] >= 1
    assert modules["raw_inputs"]["items"][0]["entity_type"] == "learning_event"
    assert modules["raw_inputs"]["title"] == "原始数据"
    assert modules["raw_inputs"]["flow_detail"]["sections"][0]["title"] == "新增进入记录"
    assert modules["raw_inputs"]["flow_detail"]["sections"][0]["items"][0]["destination"] == "数据清洗"
    assert modules["raw_inputs"]["flow_detail"]["sections"][0]["total_count"] >= 1
    assert modules["clarified_data"]["title"] == "数据清洗"
    assert modules["clarified_data"]["detail"]["by_kind"]["training_sample"] >= 1
    assert modules["clarified_data"]["detail"]["storage"]["database_version_at"]
    assert modules["clarified_data"]["flow_detail"]["storage"]["database_version_at"]
    assert modules["clarified_data"]["flow_detail"]["sections"][0]["title"] == "保留并进入下游的数据"
    assert modules["clarified_data"]["detail"]["storage"]["max_index_version"] >= 3
    assert any(metric["label"] == "数据库存储" and metric["value"] >= 2 for metric in modules["clarified_data"]["metrics"])
    assert any(metric["label"] == "向量分片" and metric["value"] >= 1 for metric in modules["clarified_data"]["metrics"])
    assert any(metric["label"] == "训练样本" and metric["value"] >= 1 for metric in modules["clarified_data"]["metrics"])
    assert any((item["payload"].get("group_count") or 1) >= 2 for item in modules["clarified_data"]["items"])
    storage_section = next(item for item in modules["clarified_data"]["flow_detail"]["sections"] if item["key"] == "storage")
    assert storage_section["total_count"] >= 1
    assert storage_section["items"][0]["entity_type"] == "learning_ingestion_job"
    assert modules["finetuning"]["title"] == "微调模型"
    assert any(metric["label"] == "预计时间" for metric in modules["finetuning"]["metrics"])
    assert modules["finetuning"]["items"][0]["entity_type"] == "training_job"
    assert modules["finetuning"]["flow_detail"]["sections"][0]["title"] == "进入训练的任务"
    assert modules["finetuning"]["flow_detail"]["sections"][0]["items"][0]["destination"] == "gw-pulse-flow"
    assert modules["test_results"]["title"] == "模型测试"
    assert modules["test_results"]["status"] == "passed"
    assert modules["test_results"]["detail"]["passed_count"] >= 1
    assert modules["test_results"]["flow_detail"]["sections"][0]["title"] == "测试任务与 Agent"
    eval_row = modules["test_results"]["flow_detail"]["sections"][0]["items"][0]
    assert eval_row["input_preview"]["worker_id"] == "worker-pulse-flow"
    assert eval_row["input_preview"]["training_model"] == "pulse-flow-skill:lora"
    assert eval_row["input_preview"]["training_date"]
    model_context = eval_row["metadata"]["model_test_context"]
    assert model_context["artifact_model_name"] == "pulse-flow-skill:lora"
    assert model_context["training_data"]["sample_count"] == 1
    assert model_context["training_data"]["endpoint"] == "/api/learning/training-jobs/tj-pulse-flow-1/dataset"
    assert eval_row["metadata"]["chat_context"]["model_deployment_id"] == "md-pulse-flow-active"
    assert eval_row["metadata"]["chat_context"]["inference_ready"] is True
    assert modules["application_outputs"]["title"] == "部署输出"
    assert modules["application_outputs"]["status"] == "applied"
    assert modules["application_outputs"]["detail"]["relation_counts"]["used_as_model"] >= 1
    assert modules["application_outputs"]["items"][0]["entity_type"] == "run"
    assert modules["application_outputs"]["flow_detail"]["sections"][0]["title"] == "部署位置"
    assert modules["application_outputs"]["flow_detail"]["sections"][1]["title"] == "实际应用链路"
    assert modules["application_outputs"]["flow_detail"]["sections"][2]["title"] == "模型对话输入输出"

    drilldown = await client.get(
        "/api/learning/pulse/drilldown",
        params={
            "days": 7,
            "skill_id": "pulse-flow-skill",
            "module_key": "clarified_data",
            "section_key": "storage",
            "page": 1,
            "page_size": 1,
        },
    )
    assert drilldown.status_code == 200, drilldown.text
    drilldown_payload = drilldown.json()
    assert drilldown_payload["total"] >= 1
    assert drilldown_payload["returned_count"] == 1
    assert drilldown_payload["items"][0]["entity_type"] == "learning_ingestion_job"

    eval_drilldown = await client.get(
        "/api/learning/pulse/drilldown",
        params={
            "days": 7,
            "skill_id": "pulse-flow-skill",
            "module_key": "test_results",
            "section_key": "eval_tasks",
            "page": 1,
            "page_size": 1,
        },
    )
    assert eval_drilldown.status_code == 200, eval_drilldown.text
    eval_drilldown_row = eval_drilldown.json()["items"][0]
    assert eval_drilldown_row["metadata"]["model_test_context"]["training_job_id"] == "tj-pulse-flow-1"
    assert eval_drilldown_row["metadata"]["training_data"]["raw_payload_returned"] is False

    dataset_resp = await client.get("/api/learning/training-jobs/tj-pulse-flow-1/dataset")
    assert dataset_resp.status_code == 200, dataset_resp.text
    dataset_payload = dataset_resp.json()
    assert dataset_payload["dataset"]["raw_payload_returned"] is False
    assert dataset_payload["dataset"]["sample_count"] == 1
    assert dataset_payload["items"][0]["artifact_id"] == "la-pulse-flow-sample"
    encoded_dataset = json.dumps(dataset_payload, ensure_ascii=False)
    assert "raw input should not leak" not in encoded_dataset


@pytest.mark.asyncio
async def test_learning_pulse_model_test_ignores_submit_failures(client):
    import app.database as db_mod
    from app.training.models import TrainingJob, TrainingJobTask

    now = now_bjt()
    async with db_mod.async_session_factory() as session:
        failed_submit_job = TrainingJob(
            id="tj-pulse-submit-failed",
            title="提交阶段失败训练任务",
            department="AI小组",
            created_by="admin",
            status="failed",
            job_type="lora",
            target_skill_id="pulse-submit-filter-skill",
            target_gateway_id="gw-pulse-submit-filter",
            dataset_ref="learning-artifacts://pulse-submit-filter/training",
            objective="验证提交阶段失败不算模型测试失败。",
            risk_level="R2",
            created_at=now - timedelta(minutes=20),
            updated_at=now - timedelta(minutes=19),
        )
        passed_eval_job = TrainingJob(
            id="tj-pulse-eval-passed",
            title="评估通过训练任务",
            department="AI小组",
            created_by="admin",
            status="completed",
            job_type="lora",
            target_skill_id="pulse-submit-filter-skill",
            target_gateway_id="gw-pulse-submit-filter",
            dataset_ref="learning-artifacts://pulse-submit-filter/training",
            objective="验证模型测试统计只看评估任务。",
            risk_level="R2",
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=1),
        )
        session.add_all([
            failed_submit_job,
            passed_eval_job,
            TrainingJobTask(
                job_id=failed_submit_job.id,
                gateway_id="gw-pulse-submit-filter",
                worker_id="worker-pulse-submit-filter",
                status="failed",
                progress=0,
                metrics_json={
                    "op": "training.submit_job",
                    "accepted": False,
                },
                error_message="实例 内容电商 的 bridge 未连接",
                created_at=now - timedelta(minutes=20),
                updated_at=now - timedelta(minutes=19),
            ),
            TrainingJobTask(
                job_id=passed_eval_job.id,
                gateway_id="gw-pulse-submit-filter",
                worker_id="worker-pulse-submit-filter",
                status="completed",
                progress=1,
                metrics_json={
                    "op": "training.evaluate",
                    "passed": True,
                    "metrics": {
                        "win_rate": 1.0,
                        "eval_samples": 4,
                        "eval_evaluated_samples": 4,
                        "adapter_sha256": "b" * 64,
                    },
                },
                created_at=now - timedelta(minutes=2),
                updated_at=now - timedelta(minutes=1),
            ),
        ])
        await session.commit()

    res = await client.get("/api/learning/pulse", params={"days": 7, "skill_id": "pulse-submit-filter-skill"})
    assert res.status_code == 200, res.text
    modules = {item["key"]: item for item in res.json()["modules"]}
    test_module = modules["test_results"]
    metrics = {item["key"]: item["value"] for item in test_module["metrics"]}
    assert metrics["passed"] == 1
    assert metrics["failed"] == 0
    assert metrics["win_rate"] == 1.0
    assert metrics["artifact"] == "b" * 64
    assert test_module["status"] == "passed"
    eval_items = test_module["flow_detail"]["sections"][0]["items"]
    assert len(eval_items) == 1
    assert eval_items[0]["metadata"]["task_id"]
    assert test_module["flow_detail"]["sections"][0]["total_count"] == 1

    drilldown = await client.get(
        "/api/learning/pulse/drilldown",
        params={
            "days": 7,
            "skill_id": "pulse-submit-filter-skill",
            "module_key": "test_results",
            "section_key": "eval_tasks",
            "page": 1,
            "page_size": 10,
        },
    )
    assert drilldown.status_code == 200, drilldown.text
    drilldown_payload = drilldown.json()
    assert drilldown_payload["total"] == 1
    assert drilldown_payload["items"][0]["metadata"]["task_id"] == eval_items[0]["metadata"]["task_id"]


@pytest.mark.asyncio
async def test_learning_training_gateway_result_flows_to_model_artifact_and_deployment(client):
    import app.database as db_mod
    from sqlalchemy import select

    from app.learning.models import LearningArtifact
    from app.learning.service import capture_model_deployment
    from app.training.models import TrainingJob, TrainingModelDeployment
    from app.training.service import apply_training_gateway_result, issue_training_callback_token

    async with db_mod.async_session_factory() as session:
        session.add(
            Skill(
                id="gateway-flow-skill",
                name="网关结果闭环 Skill",
                display_name="网关结果闭环 Skill",
                department="AI小组",
                status="active",
                visibility="department",
                approval_level=0,
            )
        )
        job = TrainingJob(
            id="tj-gateway-flow-1",
            title="网关结果闭环任务",
            department="AI小组",
            created_by="admin",
            status="running",
            job_type="lora",
            target_skill_id="gateway-flow-skill",
            target_gateway_id="gw-learning-flow",
            dataset_ref="learning-artifacts://gateway-flow-skill/training/latest",
            objective="验证训练网关结果能自动进入学习数据流。",
            risk_level="R2",
            spec_json={
                "dataset": {"ref": "learning-artifacts://gateway-flow-skill/training/latest"},
                "lineage": {"skill_id": "gateway-flow-skill"},
            },
        )
        session.add(job)
        await session.flush()
        token = issue_training_callback_token(job)
        result = await apply_training_gateway_result(
            session,
            job_id=job.id,
            token=token,
            payload={
                "job_id": job.id,
                "gateway_id": "gw-learning-flow",
                "worker_id": "worker-learning-flow",
                "status": "completed",
                "progress": 1,
                "metrics": {"accuracy": 0.91, "eval_loss": 0.12},
                "artifacts": [
                    {
                        "id": "artifact-gateway-flow-1",
                        "type": "lora_adapter",
                        "name": "gateway-flow-adapter",
                        "uri": "artifact://training/gateway-flow-adapter",
                        "sha256": "sha-gateway-flow",
                    }
                ],
            },
        )
        assert result["status"] == "completed"
        assert result["training_plan"]["artifact"]["uri"] == "artifact://training/gateway-flow-adapter"

    events = (
        await client.get(
            "/api/learning/events",
            params={"days": 7, "source_type": "training_job", "event_type": "training.job.completed"},
        )
    ).json()["items"]
    assert any(item["source_id"] == "tj-gateway-flow-1" for item in events)

    produced = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "produced_artifact"})).json()["edges"]
    assert any(
        edge["source"] == "training_job:tj-gateway-flow-1"
        and edge["target"] == "model_artifact:artifact-gateway-flow-1"
        for edge in produced
    )

    async with db_mod.async_session_factory() as session:
        knowledge = (
            await session.execute(
                select(LearningArtifact).where(
                    LearningArtifact.artifact_kind == "knowledge_note",
                    LearningArtifact.target_id == "gateway-flow-skill",
                    LearningArtifact.title.ilike("%网关结果闭环任务%"),
                )
            )
        ).scalars().first()
        assert knowledge is not None
        assert "accuracy" in (knowledge.content_json or {}).get("source", {}).get("metrics_keys", [])
        assert "artifact-gateway-flow-1" in (knowledge.content_json or {}).get("source", {}).get("artifact_ids", [])

        deployment = TrainingModelDeployment(
            id="md-gateway-flow-1",
            job_id="tj-gateway-flow-1",
            department="AI小组",
            model_family="gateway-flow-skill:lora",
            artifact_id="artifact-gateway-flow-1",
            artifact_ref_json={"sha256": "sha-gateway-flow"},
            target_skill_ids_json=["gateway-flow-skill"],
            status="canary",
            rollout_percent=20,
            requested_by="admin",
        )
        session.add(deployment)
        await session.flush()
        await capture_model_deployment(session, deployment)
        await session.commit()

    deployed = (await client.get("/api/learning/flow-graph", params={"days": 7, "relation": "deployed_to"})).json()["edges"]
    assert any(
        edge["source"] == "model_artifact:artifact-gateway-flow-1"
        and edge["target"] == "model_deployment:md-gateway-flow-1"
        for edge in deployed
    )
    assert any(
        edge["source"] == "model_deployment:md-gateway-flow-1"
        and edge["target"] == "skill:gateway-flow-skill"
        for edge in deployed
    )


@pytest.mark.asyncio
async def test_learning_decision_log_links_used_model_deployment(client):
    import app.database as db_mod
    from app.learning.service import capture_decision_log

    async with db_mod.async_session_factory() as session:
        session.add(
            Skill(
                id="model-decision-flow-skill",
                name="模型参与决策 Skill",
                display_name="模型参与决策 Skill",
                department="AI小组",
                status="active",
                visibility="department",
                approval_level=0,
            )
        )
        session.add(
            ExecutionRun(
                id="run-model-decision-flow-1",
                skill_id="model-decision-flow-skill",
                trigger_type="manual",
                run_mode="manual_real",
                status="completed",
                summary="模型部署参与了本次业务分析。",
                started_at=now_bjt(),
                completed_at=now_bjt(),
            )
        )
        decision = DecisionLog(
            run_id="run-model-decision-flow-1",
            skill_id="model-decision-flow-skill",
            input_snapshot={"video_id": "video-1"},
            output_result={
                "summary": "低消耗视频应对比高消耗同主题素材。",
                "_skillforge_meta": {
                    "model_context": {
                        "model_deployment_id": "deploy-model-decision-flow-1",
                        "model_family": "model-decision-flow-skill:learning_loop_improvement",
                        "deployment_status": "active",
                        "rollout_percent": 100,
                        "artifact_id": "adapter-model-decision-flow-1",
                        "artifact_sha256": "b" * 64,
                        "analysis_backend": "agent",
                        "analysis_agent_id": "analysis-node",
                        "inference_status": "used",
                        "inference_backend": "bridge_local_lora_inference",
                        "inference_gateway_id": "analysis-node",
                        "inference_text_sha256": "c" * 64,
                        "inference_metrics": {"generated_tokens": 8, "cuda_available": True},
                    }
                },
            },
            model_id="deploy-model-decision-flow-1",
            created_at=now_bjt(),
            is_sandbox=False,
        )
        session.add(decision)
        await session.flush()
        decision_id = decision.id
        await capture_decision_log(session, decision)
        await session.commit()

    graph_resp = await client.get(
        "/api/learning/flow-graph",
        params={"days": 7, "relation": "used_as_model"},
    )
    assert graph_resp.status_code == 200, graph_resp.text
    used_edges = graph_resp.json()["edges"]
    assert any(
        edge["source"] == "model_deployment:deploy-model-decision-flow-1"
        and edge["target"] == f"decision_log:{decision_id}"
        for edge in used_edges
    )
    assert any(
        edge["source"] == "model_deployment:deploy-model-decision-flow-1"
        and edge["target"] == "run:run-model-decision-flow-1"
        for edge in used_edges
    )

    events_resp = await client.get(
        "/api/learning/events",
        params={"source_type": "decision_log", "skill_id": "model-decision-flow-skill", "days": 7},
    )
    assert events_resp.status_code == 200, events_resp.text
    events = events_resp.json()["items"]
    assert events[0]["metadata"]["model_context"]["model_deployment_id"] == "deploy-model-decision-flow-1"
    assert events[0]["metadata"]["model_context"]["inference_status"] == "used"
    assert events[0]["metadata"]["model_context"]["inference_text_sha256"] == "c" * 64

    journeys_resp = await client.get(
        "/api/learning/flow-journeys",
        params={"source_type": "decision_log", "skill_id": "model-decision-flow-skill", "days": 7},
    )
    assert journeys_resp.status_code == 200, journeys_resp.text
    decision_journey = next(item for item in journeys_resp.json()["items"] if item["source_id"] == str(decision_id))
    model_step = next(step for step in decision_journey["steps"] if step.get("action") == "used_as_model")
    assert model_step["stage"] == "decision"
    assert model_step["label"] == "模型参与决策"
    assert model_step["payload"]["deployment_id"] == "deploy-model-decision-flow-1"
    assert model_step["payload"]["decision_target_type"] == "decision_log"
    assert model_step["payload"]["inference_status"] == "used"
    assert model_step["payload"]["inference_text_sha256"] == "c" * 64
    assert "低消耗视频应对比高消耗同主题素材" not in json.dumps(model_step, ensure_ascii=False)


@pytest.mark.asyncio
async def test_learning_auto_flow_worker_captures_and_materializes(client, _isolate_learning_auto_flow_worker_state):
    import app.database as db_mod
    from app.learning.models import LearningArtifact, LearningEvent
    from app.learning.worker import get_learning_auto_flow_state, run_learning_auto_flow_once
    from app.training.models import TrainingJob
    from sqlalchemy import select

    yesterday = now_bjt() - timedelta(days=1)
    async with db_mod.async_session_factory() as session:
        session.add(
            Skill(
                id="learn-worker-skill",
                name="学习自动流动 Skill",
                display_name="学习自动流动 Skill",
                department="AI小组",
                status="active",
                visibility="department",
                approval_level=0,
            )
        )
        session.add(
            ExecutionRun(
                id="run-learning-worker-1",
                skill_id="learn-worker-skill",
                trigger_type="manual",
                run_mode="manual_real",
                status="completed",
                total_steps=1,
                completed_steps=1,
                summary="自动流动引擎应捕获并沉淀这条运行摘要。",
                started_at=now_bjt(),
                completed_at=now_bjt(),
            )
        )
        trace_event = LearningEvent(
            id="le-worker-trace-training",
            event_type="skill.run.completed",
            source_type="execution_run",
            source_id="run-worker-trace-training",
            source_hash="run-worker-trace-training",
            department="AI小组",
            skill_id="learn-worker-skill",
            run_id="run-worker-trace-training",
            redacted_summary="自动流执行轨迹训练样本。",
            quality_score=0.8,
            created_at=yesterday,
            updated_at=yesterday,
        )
        session.add(trace_event)
        for index in range(30):
            session.add(LearningArtifact(
                id=f"la-worker-trace-training-{index}",
                event_id=trace_event.id,
                artifact_kind="training_sample",
                artifact_hash=f"la-worker-trace-training-{index}",
                target_type="skill",
                target_id="learn-worker-skill",
                department="AI小组",
                skill_id="learn-worker-skill",
                run_id=f"run-worker-trace-training-{index}",
                title=f"自动流执行轨迹训练样本 {index}",
                summary="自动学习流应把执行轨迹样本池推进到训练候选。",
                content_json={
                    "instruction": "基于执行轨迹学习输入输出和形成数据。",
                    "input": {"steps": [{"input": {"question": f"链接下滑原因 {index}"}}]},
                    "output": {"steps": [{"output": {"summary": "搜索访客下降"}}]},
                    "formed_data": {"steps": [{"duration_ms": 100 + index}]},
                    "source_type": "execution_trace",
                    "source_channel": "node_scheduler",
                },
                labels_json=["training", "execution_trace"],
                quality_score=0.8,
                confidence=0.74,
                status="materialized",
                sink_type="training_sample",
                created_at=yesterday,
                updated_at=yesterday,
            ))
        await session.commit()

    result = await run_learning_auto_flow_once(days=7, limit=50, materialize=True)
    assert result["status"] == "succeeded"
    assert result["result"]["captured"]["execution_run"] >= 1
    assert result["result"]["materialized"] >= 1
    assert result["result"]["training_candidates_created"] >= 1
    created_training = result["result"]["training_candidates"]["created"][0]
    assert created_training["skill_id"] == "learn-worker-skill"
    assert created_training["sample_total"] >= 30
    assert get_learning_auto_flow_state()["status"] == "succeeded"

    async with db_mod.async_session_factory() as session:
        jobs = (
            await session.execute(
                select(TrainingJob)
                .where(TrainingJob.target_skill_id == "learn-worker-skill")
            )
        ).scalars().all()
        job = next(
            item
            for item in jobs
            if (item.spec_json or {}).get("source") == "learning_sample_threshold"
        )
    assert job is not None
    assert job.status in {"awaiting_review", "queued"}
    assert job.spec_json["deployment"]["auto_request"] is True
    assert job.spec_json["governance"]["no_auto_approval"] is False
    assert job.spec_json["governance"]["auto_training_agent"] == "learning_training_reviewer"

    status = (await client.get("/api/learning/automation-status", params={"days": 7})).json()
    assert status["automation"]["enabled"] is True
    assert status["automation"]["status"] == "succeeded"
    assert status["backlog"]["auto_materializable"] == 0
    assert status["automation_runs"]
    assert status["automation_runs"][0]["status"] == "succeeded"
    assert status["automation_runs"][0]["trigger_type"] == "scheduled"
    assert status["latest"]["automation_run"]["id"] == status["automation_runs"][0]["id"]
    assert status["governance"]["raw_payload_returned"] is False


def test_learning_auto_flow_status_reads_worker_service_snapshot(tmp_path, monkeypatch, _isolate_learning_auto_flow_worker_state):
    from app.common.time_utils import isoformat_bjt
    import app.learning.worker as worker

    snapshot_path = tmp_path / "learning-auto-flow-state.json"
    started_at = now_bjt() - timedelta(minutes=15)
    snapshot_path.write_text(
        json.dumps(
            {
                "status": "succeeded",
                "service_status": "running",
                "service_mode": "independent_worker",
                "service_owner": "skillforge-learning-auto-flow.service",
                "service_started_at": isoformat_bjt(started_at),
                "first_run_planned_at": isoformat_bjt(started_at + timedelta(seconds=120)),
                "next_run_planned_at": isoformat_bjt(started_at + timedelta(minutes=20)),
                "snapshot_updated_at": isoformat_bjt(now_bjt()),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(worker, "_STATE_PATH", snapshot_path)

    status = worker.get_learning_auto_flow_state()

    assert status["status"] == "succeeded"
    assert status["service_status"] == "running"
    assert status["service_mode"] in {"independent_worker", "web_worker"}
    assert status["service_owner"].endswith(".service")
    assert status["first_run_planned_at"]
    assert status["next_run_planned_at"]
    assert status["service_uptime_seconds"] >= 14 * 60


def test_learning_auto_flow_status_merge_fills_latest_result_for_same_run():
    from app.learning.router import _merge_latest_automation_run

    automation = {
        "status": "succeeded",
        "run_id": "lar-same",
        "started_at": "2026-06-01T15:00:00+08:00",
        "finished_at": "2026-06-01T15:10:00+08:00",
        "result": None,
        "service_started_at": "2026-06-01T14:58:00+08:00",
    }
    latest = {
        "id": "lar-same",
        "status": "succeeded",
        "started_at": "2026-06-01T15:00:00+08:00",
        "finished_at": "2026-06-01T15:10:00+08:00",
        "result": {"auto_limits": {"training_next_after": "2026-06-02T15:10:00+08:00"}},
    }

    merged = _merge_latest_automation_run(automation, latest)

    assert merged["service_started_at"] == "2026-06-01T14:58:00+08:00"
    assert merged["result"]["auto_limits"]["training_next_after"] == "2026-06-02T15:10:00+08:00"


@pytest.mark.asyncio
async def test_learning_auto_flow_marks_failed_materialization_without_retry(client, monkeypatch, _isolate_learning_auto_flow_worker_state):
    import app.database as db_mod
    from app.learning.models import LearningArtifact, LearningEvent
    from app.learning.worker import run_learning_auto_flow_once
    from sqlalchemy import select

    event_id = "le-auto-materialize-fail"
    artifact_id = "la-auto-materialize-fail"
    async with db_mod.async_session_factory() as session:
        session.add(
            LearningEvent(
                id=event_id,
                event_type="skill.run.completed",
                source_type="execution_run",
                source_id="run-auto-materialize-fail",
                source_hash="run-auto-materialize-fail",
                department="AI小组",
                skill_id="learn-auto-fail-skill",
                run_id="run-auto-materialize-fail",
                redacted_summary="自动物化失败后不应无限重试。",
                quality_score=0.8,
                created_at=now_bjt(),
                updated_at=now_bjt(),
            )
        )
        session.add(
            LearningArtifact(
                id=artifact_id,
                event_id=event_id,
                artifact_kind="knowledge_note",
                artifact_hash=artifact_id,
                target_type="knowledge",
                target_id="learn-auto-fail-skill",
                department="AI小组",
                skill_id="learn-auto-fail-skill",
                run_id="run-auto-materialize-fail",
                title="自动物化失败样本",
                summary="自动物化失败后标记失败，等待人工重试。",
                content_json={"text": "auto materialize fail"},
                labels_json=["knowledge"],
                quality_score=0.82,
                confidence=0.8,
                status="ready",
                policy_result_json={"action": "auto_index", "sink_type": "knowledge_document"},
                created_at=now_bjt(),
                updated_at=now_bjt(),
            )
        )
        await session.commit()

    calls: list[str] = []

    async def fake_materialize(db, user, artifact_id_arg, *, force=False):
        calls.append(str(artifact_id_arg))
        if artifact_id_arg == artifact_id:
            raise RuntimeError("model api unavailable")
        artifact = await db.get(LearningArtifact, artifact_id_arg)
        if artifact is not None:
            artifact.status = "materialized"
        return {"artifact": {"id": artifact_id_arg}}

    monkeypatch.setattr("app.learning.service.materialize_artifact", fake_materialize)

    first = await run_learning_auto_flow_once(days=7, limit=10, materialize=True)
    assert first["status"] == "succeeded"
    assert artifact_id in calls

    async with db_mod.async_session_factory() as session:
        artifact = (
            await session.execute(select(LearningArtifact).where(LearningArtifact.id == artifact_id))
        ).scalar_one()
        assert artifact.status == "failed"
        assert artifact.review_status == "materialize_failed"
        assert artifact.policy_result_json["manual_retry_allowed"] is True
        assert "model api unavailable" in artifact.policy_result_json["last_auto_materialize_error"]

    calls.clear()
    second = await run_learning_auto_flow_once(days=7, limit=10, materialize=True)
    assert second["status"] == "succeeded"
    assert artifact_id not in calls


@pytest.mark.asyncio
async def test_learning_auto_flow_marks_stale_running_runs_failed(client):
    import app.database as db_mod
    from app.auth.models import User
    from app.learning.models import LearningAutomationRun
    from app.learning.service import run_learning_automation_once
    from sqlalchemy import select

    old_at = now_bjt() - timedelta(minutes=45)
    async with db_mod.async_session_factory() as session:
        session.add(
            LearningAutomationRun(
                id="lar-stale-running-auto-flow",
                trigger_type="scheduled",
                status="running",
                days=7,
                limit=300,
                materialize=True,
                captured_json={},
                result_json={},
                metadata_json={"scope": "global"},
                created_by="learning_auto_flow",
                started_at=old_at,
                finished_at=None,
                created_at=old_at,
                updated_at=old_at,
            )
        )
        actor = User(
            id="learning_auto_flow",
            username="learning_auto_flow",
            name="智能闭环自动流动引擎",
            role="system_admin",
            can_view_all=True,
            state="active",
            is_active=True,
            must_change_password=False,
            permissions_rev=0,
        )
        result = await run_learning_automation_once(
            session,
            actor,
            days=7,
            limit=5,
            materialize=False,
            trigger_type="scheduled",
            created_by="learning_auto_flow",
        )
        await session.commit()
        assert result["status"] == "succeeded"

        stale = (
            await session.execute(
                select(LearningAutomationRun).where(LearningAutomationRun.id == "lar-stale-running-auto-flow")
            )
        ).scalar_one()
        latest = (
            await session.execute(
                select(LearningAutomationRun)
                .where(LearningAutomationRun.id == result["run"]["id"])
            )
        ).scalar_one()
        assert stale.status == "failed"
        assert stale.finished_at is not None
        assert "stale scheduled automation run exceeded" in (stale.error or "")
        assert stale.result_json["stale_marked_by"] == "learning_auto_flow"
        assert latest.metadata_json["stale_runs_marked_failed"] >= 1


@pytest.mark.asyncio
async def test_learning_auto_flow_skips_training_until_daily_interval(client, monkeypatch):
    import app.database as db_mod
    from app.auth.models import User
    from app.execution.models import OpenClawInstance
    from app.learning.models import LearningArtifact, LearningAutomationRun, LearningEvent
    from app.learning.service import run_learning_automation_once
    from app.training.models import TrainingJob, TrainingJobTask, TrainingModelDeployment
    from sqlalchemy import select

    collect_calls: list[str] = []
    evaluate_calls: list[str] = []
    approve_deployment_calls: list[str] = []
    retry_calls: list[str] = []
    dispatch_calls: list[str] = []
    model_status_calls: list[str] = []
    prepare_model_calls: list[str] = []

    async def fake_retry_training_job(db, user, job_id):
        retry_calls.append(job_id)
        job = await db.get(TrainingJob, job_id)
        job.status = "running"
        job.failure_stage = None
        await db.flush()
        return {"id": job_id, "status": "running", "target_gateway_id": job.target_gateway_id}

    async def fake_dispatch_training_job(db, user, job_id):
        dispatch_calls.append(job_id)
        job = await db.get(TrainingJob, job_id)
        job.status = "running"
        job.failure_stage = None
        job.updated_at = now_bjt()
        await db.flush()
        return {"id": job_id, "status": "running", "target_gateway_id": job.target_gateway_id}

    async def fake_get_training_gateway_model_status(db, user, gateway_id, payload):
        model_status_calls.append(gateway_id)
        return {
            "model": {
                "status": "not_downloaded",
                "profile": payload["profile"],
                "exists": False,
                "source_exists": True,
                "source_path": "/mnt/models/qwen36",
                "model_dir": "/models/qwen36",
            },
        }

    async def fake_prepare_training_gateway_model(db, user, gateway_id, payload):
        prepare_model_calls.append(gateway_id)
        return {
            "model": {
                "status": "succeeded",
                "profile": payload["profile"],
                "exists": True,
                "source_exists": True,
                "model_dir": "/models/qwen36",
            },
        }

    async def fake_collect_training_job_result(db, user, job_id):
        collect_calls.append(job_id)
        job = await db.get(TrainingJob, job_id)
        job.status = "completed"
        await db.flush()
        return {"id": job_id, "status": "completed", "updated_at": now_bjt().isoformat()}

    async def fake_evaluate_training_job(db, user, job_id):
        evaluate_calls.append(job_id)
        return {
            "id": job_id,
            "status": "completed",
            "latest_deployment": {"id": "md-daily-train-running-existing"},
        }

    async def fake_approve_training_deployment(db, user, deployment_id):
        approve_deployment_calls.append(deployment_id)
        deployment = await db.get(TrainingModelDeployment, deployment_id)
        deployment.status = "canary"
        deployment.rollout_percent = 10
        await db.flush()
        return {"deployment": {"id": deployment_id, "status": "canary", "rollout_percent": 10}}

    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_ENABLED", True)
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_DISPATCH_ENABLED", True)
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_DEPLOYMENT_APPROVE_ENABLED", True)
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_COLLECT_STALE_SECONDS", 60)
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_INTERVAL_SECONDS", 24 * 60 * 60)
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_DAYS", 1)
    monkeypatch.setattr("app.training.service.collect_training_job_result", fake_collect_training_job_result)
    monkeypatch.setattr("app.training.service.evaluate_training_job", fake_evaluate_training_job)
    monkeypatch.setattr("app.training.service.approve_training_deployment", fake_approve_training_deployment)
    monkeypatch.setattr("app.training.service.retry_training_job", fake_retry_training_job)
    monkeypatch.setattr("app.training.service.dispatch_training_job", fake_dispatch_training_job)
    monkeypatch.setattr("app.training.service.get_training_gateway_model_status", fake_get_training_gateway_model_status)
    monkeypatch.setattr("app.training.service.prepare_training_gateway_model", fake_prepare_training_gateway_model)
    monkeypatch.setattr("app.aiclaw.bridge_registry.bridge_registry.is_online", lambda instance_id: instance_id == "daily-train-retry-gateway")

    trained_at = now_bjt() - timedelta(hours=1)
    yesterday = now_bjt() - timedelta(days=1)
    async with db_mod.async_session_factory() as session:
        session.add(
            Skill(
                id="daily-train-skill",
                name="每日训练 Skill",
                display_name="每日训练 Skill",
                department="AI小组",
                status="active",
                visibility="department",
                approval_level=0,
            )
        )
        session.add(
            LearningAutomationRun(
                id="lar-daily-training-recent",
                trigger_type="scheduled",
                status="succeeded",
                days=1,
                limit=10,
                materialize=False,
                captured_json={},
                result_json={"auto_limits": {"training_due": True}},
                metadata_json={"scope": "global"},
                created_by="learning_auto_flow",
                started_at=trained_at,
                finished_at=trained_at,
                created_at=trained_at,
                updated_at=trained_at,
            )
        )
        event = LearningEvent(
            id="le-daily-train-recent",
            event_type="skill.run.completed",
            source_type="execution_run",
            source_id="run-daily-train-recent",
            source_hash="run-daily-train-recent",
            department="AI小组",
            skill_id="daily-train-skill",
            redacted_summary="每日训练样本池。",
            quality_score=0.8,
            created_at=yesterday,
            updated_at=yesterday,
        )
        session.add(event)
        for index in range(30):
            session.add(LearningArtifact(
                id=f"la-daily-train-recent-{index}",
                event_id=event.id,
                artifact_kind="training_sample",
                artifact_hash=f"la-daily-train-recent-{index}",
                target_type="skill",
                target_id="daily-train-skill",
                department="AI小组",
                skill_id="daily-train-skill",
                title=f"每日训练样本 {index}",
                summary="应等待每日训练窗口。",
                content_json={"input": {"q": index}, "output": {"a": index}},
                labels_json=["training"],
                quality_score=0.8,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
                created_at=yesterday,
                updated_at=yesterday,
            ))
        gateway = OpenClawInstance(
            id="daily-train-retry-gateway",
            name="每日训练恢复网关",
            department="AI小组",
            gateway_url="http://127.0.0.1:18789",
            reload_hook_url="http://127.0.0.1:9000/reload",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="training",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": ["training.submit_job", "training.collect_result", "training.inference"],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["lora", "eval"],
                    "gpu_count": 1,
                    "worker_count": 3,
                },
            }),
            is_active=True,
        )
        retry_job = TrainingJob(
            id="tj-daily-train-retry-old-dispatch",
            title="每日训练间隔内恢复旧派发失败",
            department="AI小组",
            created_by="learning_auto_flow",
            status="queued",
            failure_stage="dispatch",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="daily-train-retry-skill",
            target_gateway_id=gateway.id,
            dataset_ref="learning-artifacts://daily-train-retry-skill/training/yesterday/2026-01-01",
            spec_json={
                "source": "learning_sample_threshold",
                "training_mode": "daily_incremental",
                "dataset": {
                    "manifest_hash": "manifest-daily-train-retry",
                    "sample_total": 30,
                    "eval_count": 3,
                    "avg_quality": 0.88,
                    "window": {
                        "mode": "yesterday",
                        "date": (now_bjt() - timedelta(days=3)).date().isoformat(),
                    },
                },
                "lineage": {"source": "learning_artifacts"},
                "governance": {
                    "auto_created": True,
                    "raw_payload_returned": False,
                    "auto_training_agent": "learning_training_reviewer",
                    "incremental_training": True,
                },
            },
            created_at=now_bjt() - timedelta(days=3),
            updated_at=now_bjt() - timedelta(minutes=15),
        )
        model_preflight_job = TrainingJob(
            id="tj-daily-train-retry-model-preflight",
            title="每日训练间隔内恢复模型预检",
            department="AI小组",
            created_by="learning_auto_flow",
            status="queued",
            failure_stage="model_preflight",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="daily-train-model-preflight-skill",
            target_gateway_id=gateway.id,
            dataset_ref="learning-artifacts://daily-train-model-preflight-skill/training/yesterday/2026-01-01",
            spec_json={
                "source": "learning_sample_threshold",
                "training_mode": "daily_incremental",
                "model": {
                    "profile": "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive",
                    "source": "internal",
                },
                "dataset": {
                    "manifest_hash": "manifest-daily-train-model-preflight",
                    "sample_total": 30,
                    "eval_count": 3,
                    "avg_quality": 0.88,
                    "window": {
                        "mode": "yesterday",
                        "date": (now_bjt() - timedelta(days=3)).date().isoformat(),
                    },
                },
                "lineage": {"source": "learning_artifacts"},
                "governance": {
                    "auto_created": True,
                    "raw_payload_returned": False,
                    "auto_training_agent": "learning_training_reviewer",
                    "incremental_training": True,
                },
            },
            created_at=now_bjt() - timedelta(days=3),
            updated_at=now_bjt() - timedelta(minutes=15),
        )
        old_dispatch_noise_jobs = [
            TrainingJob(
                id=f"tj-daily-train-dispatch-noise-{index:02d}",
                title=f"旧派发失败噪声 {index}",
                department="AI小组",
                created_by="manual-test",
                status="queued",
                failure_stage="dispatch",
                job_type="lora",
                training_strategy=None,
                target_skill_id=f"daily-train-noise-skill-{index}",
                target_gateway_id=gateway.id,
                dataset_ref=f"manual://daily-train-noise/{index}",
                spec_json={"source": "manual"},
                created_at=now_bjt() - timedelta(days=5, minutes=index),
                updated_at=now_bjt() - timedelta(days=5, minutes=index),
            )
            for index in range(25)
        ]
        running_job = TrainingJob(
            id="tj-daily-train-running-existing",
            title="每日训练间隔内收集已有训练结果",
            department="AI小组",
            created_by="learning_auto_flow",
            status="running",
            job_type="lora",
            training_strategy="learning_sample_threshold",
            target_skill_id="daily-train-running-skill",
            target_gateway_id=gateway.id,
            dataset_ref="learning-artifacts://daily-train-running-skill/training/yesterday/2026-01-01",
            spec_json={
                "source": "learning_sample_threshold",
                "training_mode": "daily_incremental",
                "dataset": {
                    "manifest_hash": "manifest-daily-train-running",
                    "sample_total": 30,
                    "eval_count": 3,
                    "avg_quality": 0.91,
                    "window": {
                        "mode": "yesterday",
                        "date": (now_bjt() - timedelta(days=2)).date().isoformat(),
                    },
                },
                "lineage": {"source": "learning_artifacts"},
                "governance": {
                    "auto_created": True,
                    "raw_payload_returned": False,
                    "auto_training_agent": "learning_training_reviewer",
                    "incremental_training": True,
                },
            },
            created_at=now_bjt() - timedelta(days=2),
            updated_at=now_bjt() - timedelta(minutes=15),
        )
        deployment = TrainingModelDeployment(
            id="md-daily-train-running-existing",
            job_id=running_job.id,
            department="AI小组",
            model_family="daily-train-running-skill:learning-loop-adapter",
            artifact_id="artifact-daily-train-running",
            artifact_ref_json={"id": "artifact-daily-train-running", "uri": "artifact://daily-train-running", "sha256": "c" * 64},
            target_skill_ids_json=["daily-train-running-skill"],
            status="awaiting_review",
            rollout_percent=0,
            requested_by="learning_auto_flow",
            created_at=now_bjt() - timedelta(minutes=15),
            updated_at=now_bjt() - timedelta(minutes=15),
        )
        evaluation_task = TrainingJobTask(
            job_id=running_job.id,
            gateway_id=gateway.id,
            status="completed",
            progress=1,
            metrics_json={
                "op": "training.evaluate",
                "passed": True,
                "metrics": {
                    "eval_samples": 3,
                    "eval_evaluated_samples": 3,
                    "win_rate": 0.67,
                },
            },
            created_at=now_bjt() - timedelta(minutes=15),
            updated_at=now_bjt() - timedelta(minutes=15),
        )
        actor = User(
            id="learning_auto_flow",
            username="learning_auto_flow",
            name="智能闭环自动流动引擎",
            role="system_admin",
            can_view_all=True,
            state="active",
            is_active=True,
            must_change_password=False,
            permissions_rev=0,
        )
        session.add_all([
            gateway,
            retry_job,
            model_preflight_job,
            *old_dispatch_noise_jobs,
            running_job,
            deployment,
            evaluation_task,
        ])
        result = await run_learning_automation_once(
            session,
            actor,
            days=7,
            limit=5,
            materialize=False,
            trigger_type="scheduled",
            created_by="learning_auto_flow",
        )
        await session.commit()

        assert result["status"] == "succeeded"
        assert result["result"]["auto_limits"]["training_due"] is False
        assert result["result"]["auto_limits"]["training_days"] == 1
        assert result["result"]["training_candidates_created"] == 0
        assert result["result"]["training_candidates"]["skipped"][0]["reason"] == "training_interval_not_due"
        assert result["result"]["training_automation"]["skipped"][0]["reason"] == "training_interval_not_due"
        assert result["result"]["training_automation"]["dispatch_recovery_only"] is True
        assert retry_calls == ["tj-daily-train-retry-old-dispatch"]
        assert dispatch_calls == ["tj-daily-train-retry-model-preflight"]
        assert model_status_calls == ["daily-train-retry-gateway"]
        assert prepare_model_calls == ["daily-train-retry-gateway"]
        assert collect_calls == ["tj-daily-train-running-existing"]
        assert evaluate_calls == ["tj-daily-train-running-existing"]
        assert approve_deployment_calls == ["md-daily-train-running-existing"]
        assert result["result"]["training_automation"]["retried_dispatch_jobs"][0]["job_id"] == "tj-daily-train-retry-old-dispatch"
        assert any(
            item.get("job_id") == "tj-daily-train-retry-model-preflight"
            and item.get("recovered_model_preflight") is True
            for item in result["result"]["training_automation"]["dispatched_jobs"]
        )
        assert result["result"]["training_automation"]["collected"]["items"][0]["job_id"] == "tj-daily-train-running-existing"
        assert result["result"]["training_automation"]["approved_deployments"][0]["deployment_id"] == "md-daily-train-running-existing"
        jobs = (
            await session.execute(
                select(TrainingJob).where(TrainingJob.target_skill_id == "daily-train-skill")
            )
        ).scalars().all()
        assert jobs == []


@pytest.mark.asyncio
async def test_learning_auto_flow_trains_when_daily_interval_due(client, monkeypatch):
    import app.database as db_mod
    from app.auth.models import User
    from app.learning.models import LearningArtifact, LearningAutomationRun, LearningEvent
    from app.learning.service import run_learning_automation_once
    from app.training.models import TrainingJob, TrainingModelDeployment
    from sqlalchemy import select

    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_ENABLED", True)
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_INTERVAL_SECONDS", 24 * 60 * 60)
    monkeypatch.setattr("app.learning.service.settings.LEARNING_AUTO_TRAINING_DAYS", 1)

    trained_at = now_bjt() - timedelta(days=2)
    yesterday = now_bjt() - timedelta(days=1)
    async with db_mod.async_session_factory() as session:
        session.add(
            Skill(
                id="daily-train-due-skill",
                name="到期每日训练 Skill",
                display_name="到期每日训练 Skill",
                department="AI小组",
                status="active",
                visibility="department",
                approval_level=0,
            )
        )
        session.add(
            LearningAutomationRun(
                id="lar-daily-training-old",
                trigger_type="scheduled",
                status="succeeded",
                days=1,
                limit=10,
                materialize=False,
                captured_json={},
                result_json={"auto_limits": {"training_due": True}},
                metadata_json={"scope": "global"},
                created_by="learning_auto_flow",
                started_at=trained_at,
                finished_at=trained_at,
                created_at=trained_at,
                updated_at=trained_at,
            )
        )
        session.add(
            TrainingModelDeployment(
                id="md-daily-train-due-active",
                job_id="tj-daily-train-due-previous",
                department="AI小组",
                model_family="daily-train-due-skill:learning-loop-adapter",
                artifact_id="artifact-daily-train-due-active",
                artifact_ref_json={
                    "id": "artifact-daily-train-due-active",
                    "uri": "artifact://daily-train-due/adapter.tar.gz",
                    "sha256": "a" * 64,
                },
                target_skill_ids_json=["daily-train-due-skill"],
                status="active",
                rollout_percent=100,
                requested_by="learning_auto_flow",
                approved_by="learning_auto_flow",
                approved_at=trained_at,
                activated_at=trained_at,
                created_at=trained_at,
                updated_at=trained_at,
            )
        )
        event = LearningEvent(
            id="le-daily-train-due",
            event_type="skill.run.completed",
            source_type="execution_run",
            source_id="run-daily-train-due",
            source_hash="run-daily-train-due",
            department="AI小组",
            skill_id="daily-train-due-skill",
            redacted_summary="到期每日训练样本池。",
            quality_score=0.8,
            created_at=yesterday,
            updated_at=yesterday,
        )
        session.add(event)
        for index in range(30):
            session.add(LearningArtifact(
                id=f"la-daily-train-due-{index}",
                event_id=event.id,
                artifact_kind="training_sample",
                artifact_hash=f"la-daily-train-due-{index}",
                target_type="skill",
                target_id="daily-train-due-skill",
                department="AI小组",
                skill_id="daily-train-due-skill",
                title=f"到期每日训练样本 {index}",
                summary="应进入每日训练窗口。",
                content_json={"input": {"q": index}, "output": {"a": index}},
                labels_json=["training"],
                quality_score=0.8,
                confidence=0.8,
                status="materialized",
                sink_type="training_sample",
                created_at=yesterday,
                updated_at=yesterday,
            ))
        actor = User(
            id="learning_auto_flow",
            username="learning_auto_flow",
            name="智能闭环自动流动引擎",
            role="system_admin",
            can_view_all=True,
            state="active",
            is_active=True,
            must_change_password=False,
            permissions_rev=0,
        )
        result = await run_learning_automation_once(
            session,
            actor,
            days=7,
            limit=5,
            materialize=False,
            trigger_type="scheduled",
            created_by="learning_auto_flow",
        )
        await session.commit()

        assert result["status"] == "succeeded"
        assert result["result"]["auto_limits"]["training_due"] is True
        assert result["result"]["auto_limits"]["training_days"] == 1
        assert result["result"]["training_candidates_created"] >= 1
        jobs = (
            await session.execute(
                select(TrainingJob).where(TrainingJob.target_skill_id == "daily-train-due-skill")
            )
        ).scalars().all()
        assert any((job.spec_json or {}).get("source") == "learning_sample_threshold" for job in jobs)
        job = next(job for job in jobs if (job.spec_json or {}).get("source") == "learning_sample_threshold")
        spec = job.spec_json or {}
        assert spec["dataset"]["window"]["mode"] == "yesterday"
        assert spec["dataset"]["window"]["date"] == result["result"]["training_candidates"]["window"]["date"]
        assert spec["parent_model"]["model_deployment_id"] == "md-daily-train-due-active"
        assert spec["parent_model"]["artifact_uri"] == "artifact://daily-train-due/adapter.tar.gz"
        assert spec["lineage"]["parent_model_deployment_id"] == "md-daily-train-due-active"


@pytest.mark.asyncio
async def test_learning_training_deployment_decision_loop_end_to_end(client, monkeypatch, tmp_path):
    import app.database as db_mod
    import app.intelligence.service as intelligence_service
    from app.aiclaw.bridge_registry import bridge_registry
    from app.aiclaw.client import AIClawClient
    from app.auth.models import User
    from app.common.models import SystemConfig
    from app.execution.execution_service import issue_run_token
    from app.execution.models import OpenClawInstance
    from app.learning.models import LearningArtifact, LearningEvent, LearningFlowEdge
    from app.learning.service import ensure_training_candidates_from_learning_samples
    from app.skills.core.git_service import git_service
    from app.training.models import TrainingJob, TrainingModelDeployment
    from sqlalchemy import select

    app = client._transport.app
    if not getattr(app.state, "learning_intelligence_router_registered", False):
        from app.intelligence.router import router as intelligence_router

        app.include_router(intelligence_router, prefix="/api/intelligence")
        app.state.learning_intelligence_router_registered = True

    skill_id = "learn-e2e-loop-skill"
    gateway_id = "learn-e2e-node"
    deployment_gateway_id = "inference-primary"
    run_id = "run-learn-e2e-decision"
    commit = "a" * 40
    artifact_sha = "8" * 64
    source_adapter = tmp_path / "learn-e2e-loop-adapter.tar.gz"
    source_adapter.write_bytes(b"adapter")
    inference_calls: list[dict] = []

    monkeypatch.setattr(
        bridge_registry,
        "is_online",
        lambda instance_id: instance_id in {gateway_id, deployment_gateway_id},
    )

    async def fake_model_status(self, payload, *, timeout=30):
        return {
            "status": "ready",
            "profile": payload.get("profile") or "qwen3.5-4b",
            "exists": True,
            "bridge_instance_id": self.instance_id,
        }

    async def fake_artifact_status(self, payload, *, timeout=30):
        return {
            "exists": True,
            "uri": str(source_adapter),
            "sha256": artifact_sha,
        }

    async def fake_register_openwebui_model(self, payload, *, timeout=30):
        return {
            "registered": True,
            "model_id": payload["model_id"],
            "base_url": "http://127.0.0.1:18080/v1",
        }

    async def fake_openwebui_chat(self, payload, *, timeout=600):
        return {
            "status": "succeeded",
            "ok": True,
            "model": payload["model_id"],
            "text": "成人用品电商关键词验证通过。",
            "http_status": 200,
        }

    async def fake_training_inference(self, payload, *, timeout=300):
        inference_calls.append({"instance_id": self.instance_id, "payload": payload})
        return {
            "text": "训练完成的本地 adapter 参与本次决策。",
            "text_sha256": "9" * 64,
            "metrics": {"generated_tokens": 11},
        }

    async def fake_llm(**kwargs):
        active = kwargs["context_pack"]["skillforge"]["active_model_deployment"]
        deployed_inference = kwargs["context_pack"]["skillforge"]["deployed_model_inference"]
        assert active["id"] == activated_deployment_id
        assert active["runtime_status"]["inference_ready"] is True
        assert active["runtime_status"]["raw_payload_returned"] is False
        assert deployed_inference["model_deployment_id"] == activated_deployment_id
        return intelligence_service.LLMAnalyzeResult(
            output={"summary": "学习闭环模型已参与决策"},
            usage={"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
            raw_output='{"summary":"学习闭环模型已参与决策"}',
        )

    monkeypatch.setattr(AIClawClient, "get_training_model_status", fake_model_status)
    monkeypatch.setattr(AIClawClient, "get_training_artifact_status", fake_artifact_status)
    monkeypatch.setattr(AIClawClient, "register_openwebui_model", fake_register_openwebui_model)
    monkeypatch.setattr(AIClawClient, "test_openwebui_chat", fake_openwebui_chat)
    monkeypatch.setattr(AIClawClient, "run_training_inference", fake_training_inference)
    async def fake_list_local_skills(self, target_dir=None):
        return {"skills": [skill_id]}

    monkeypatch.setattr(AIClawClient, "list_local_skills", fake_list_local_skills)

    async def fake_sync_schedules(self, *, schedules, submit_token, submit_url, config_version):
        return {"accepted": len(schedules), "config_version": config_version}

    monkeypatch.setattr(AIClawClient, "sync_schedules", fake_sync_schedules)
    monkeypatch.setattr(git_service, "get_file_at_commit", lambda *args, **kwargs: "learning loop prompt")
    monkeypatch.setattr(intelligence_service, "_call_llm_json", fake_llm)

    yesterday = now_bjt() - timedelta(days=1)
    async with db_mod.async_session_factory() as session:
        user = User(
            id="learning-e2e-admin",
            username="learning-e2e-admin",
            name="学习闭环端到端管理员",
            role="admin",
            department="AI小组",
            can_view_all=True,
            is_active=True,
            state="active",
        )
        skill = Skill(
            id=skill_id,
            name="学习训练部署决策闭环 Skill",
            display_name="学习训练部署决策闭环 Skill",
            department="AI小组",
            status="active",
            visibility="department",
            git_commit=commit,
            approval_level=0,
        )
        gateway = OpenClawInstance(
            id=gateway_id,
            name="学习闭环训练与推理节点",
            department="AI小组",
            gateway_url="http://127.0.0.1:18790",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="mixed",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": [
                    "run_agent_skill",
                    "run_skill_script",
                    "training.submit_job",
                    "training.collect_result",
                    "training.inference",
                ],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["lora"],
                    "gpu_count": 1,
                    "worker_count": 1,
                },
            }),
            is_active=True,
        )
        deployment_gateway = OpenClawInstance(
            id=deployment_gateway_id,
            name="Mac 236 · M3 Ultra",
            department="AI小组",
            gateway_url="http://127.0.0.1:18791",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="mixed",
            connection_mode="bridge",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({
                "ops": [
                    "training.inference",
                    "training.import_artifact",
                    "training.artifact_status",
                    "training.register_openwebui_model",
                ],
                "training": {
                    "gateway": True,
                    "supported_tasks": ["eval"],
                    "gpu_count": 1,
                    "worker_count": 1,
                },
                "gpu": [{"name": "Apple M3 Ultra", "backend": "metal"}],
            }),
            is_active=True,
            is_platform_default=True,
        )
        event = LearningEvent(
            id="le-e2e-loop-source",
            event_type="skill.run.completed",
            source_type="execution_run",
            source_id="run-e2e-loop-source",
            source_hash="run-e2e-loop-source",
            department="AI小组",
            skill_id=skill_id,
            run_id="run-e2e-loop-source",
            redacted_summary="执行轨迹样本进入训练、部署并参与后续决策。",
            quality_score=0.86,
            created_at=now_bjt(),
            updated_at=now_bjt(),
        )
        session.add_all([user, skill, gateway, deployment_gateway, event])
        await session.flush()
        for index in range(3):
            session.add(LearningArtifact(
                id=f"la-e2e-loop-training-{index}",
                event_id=event.id,
                artifact_kind="training_sample",
                artifact_hash=f"la-e2e-loop-training-{index}",
                target_type="skill",
                target_id=skill_id,
                department="AI小组",
                skill_id=skill_id,
                run_id=f"run-e2e-loop-training-{index}",
                title=f"闭环训练样本 {index}",
                summary="端到端回归样本保留输入、输出和形成数据。",
                content_json={
                    "instruction": "基于执行轨迹学习输入输出和中间形成数据。",
                    "input": {"question": f"商品转化下滑原因 {index}"},
                    "output": {"summary": "搜索访客下降，建议优化关键词和价格力。"},
                    "formed_data": {"steps": [{"duration_ms": 200 + index, "node": gateway_id}]},
                    "source_type": "execution_trace",
                    "source_channel": "node_scheduler",
                    "runtime_agent": {
                        "agent_id": gateway_id,
                        "agent_purpose": "mixed",
                        "control_ops": ["training.submit_job", "training.inference"],
                    },
                },
                labels_json=["training", "execution_trace"],
                quality_score=0.86,
                confidence=0.82,
                status="materialized",
                sink_type="training_sample",
                created_at=yesterday,
                updated_at=yesterday,
            ))
        session.add(LearningArtifact(
            id="la-e2e-loop-dingtalk-feedback",
            event_id=event.id,
            artifact_kind="training_sample",
            artifact_hash="la-e2e-loop-dingtalk-feedback",
            target_type="skill",
            target_id=skill_id,
            department="AI小组",
            skill_id=skill_id,
            run_id="run-e2e-loop-dingtalk-feedback",
            title="闭环钉钉反馈样本",
            summary="用户在钉钉里填写的结构化反馈进入同一轮训练。",
            content_json={
                "instruction": "学习钉钉人工反馈对模型输出的修正信号。",
                "input": {"question": "商品转化下滑原因 反馈样本"},
                "output": {"summary": "搜索访客下降，但预算承接证据不足。"},
                "source_type": "todo_feedback",
                "source_channel": "dingtalk",
                "feedback": {
                    "status": "rejected",
                    "source_channel": "dingtalk",
                    "structured": {
                        "source_channel": "dingtalk",
                        "fields": {
                            "source": "dingtalk_interactive_card",
                            "feedback_type": "证据不足",
                            "rating": 2,
                            "reject_reason": "缺少预算承接",
                        },
                    },
                },
                "human_feedback_input": {
                    "source_channel": "dingtalk",
                    "fields": {
                        "source": "dingtalk_interactive_card",
                        "feedback_type": "证据不足",
                        "rating": 2,
                        "reject_reason": "缺少预算承接",
                        "feedback": "预算、出价、人群包没有讲清楚。",
                    },
                },
                "decision": {
                    "decision_log_id": 86001,
                    "todo_id": 96001,
                    "rating": 2,
                    "feedback_type": "证据不足",
                },
                "model_context": {
                    "model_deployment_id": "deploy-before-e2e-feedback",
                    "artifact_sha256": "7" * 64,
                },
            },
            labels_json=["training", "todo_feedback", "channel:dingtalk"],
            quality_score=0.9,
            confidence=0.84,
            status="materialized",
            sink_type="training_sample",
            created_at=yesterday,
            updated_at=yesterday,
        ))
        session.add(
            SystemConfig(
                key="ai.pricing.deepseek-v4-pro",  # gitleaks:allow -- enum/config key or deliberate synthetic test fixture; not a credential
                value={"input": 1.0, "output": 2.0, "cache_read": 0, "cache_write": 0},
            )
        )
        training_result = await ensure_training_candidates_from_learning_samples(
            session,
            user,
            days=7,
            min_sample_count=4,
            max_skills=1,
        )
        await session.commit()

    assert training_result["created"]
    job_id = training_result["created"][0]["training_job_id"]

    approve_resp = await client.post(f"/api/training/jobs/{job_id}/approve")
    assert approve_resp.status_code == 200, approve_resp.text
    approved_job = approve_resp.json()
    assert approved_job["status"] == "queued"
    assert approved_job["target_gateway_id"] == gateway_id
    dataset_package = approved_job["gateway_payload"]["dataset_package"]
    assert dataset_package["sample_count"] == 4
    assert dataset_package["samples"] == "[REDACTED]"

    async with db_mod.async_session_factory() as session:
        job = await session.get(TrainingJob, job_id)
        callback_token = job.gateway_payload_json["control"]["callback_token"]
        raw_dataset_package = job.gateway_payload_json["dataset_package"]
    source_types = {item["metadata"]["source_type"] for item in raw_dataset_package["samples"]}
    assert source_types == {"execution_trace", "todo_feedback"}
    trace_sample = next(item for item in raw_dataset_package["samples"] if item["metadata"]["source_type"] == "execution_trace")
    dingtalk_sample = next(item for item in raw_dataset_package["samples"] if item["metadata"]["source_type"] == "todo_feedback")
    assert f'"formed_data":{{"steps":[{{"duration_ms":200,"node":"{gateway_id}"}}]}}' in trace_sample["input"]
    assert trace_sample["metadata"]["has_input"] is True
    assert trace_sample["metadata"]["has_output"] is True
    assert trace_sample["metadata"]["has_formed_data"] is True
    assert trace_sample["metadata"]["formed_data_sha256"]
    assert dingtalk_sample["metadata"]["source_channel"] == "dingtalk"
    assert dingtalk_sample["metadata"]["has_human_feedback"] is True
    assert dingtalk_sample["metadata"]["feedback_source"] == "dingtalk"
    assert dingtalk_sample["metadata"]["has_model_context"] is True
    assert dingtalk_sample["metadata"]["model_deployment_id"] == "deploy-before-e2e-feedback"
    assert '"source_channel":"dingtalk"' in dingtalk_sample["input"]
    assert '"feedback_type":"证据不足"' in dingtalk_sample["input"]
    assert '"model_deployment_id":"deploy-before-e2e-feedback"' in dingtalk_sample["input"]

    gateway_resp = await client.post(
        f"/api/training/jobs/{job_id}/gateway-result",
        headers={"X-Training-Callback-Token": callback_token},
        json={
            "job_id": job_id,
            "gateway_id": gateway_id,
            "worker_id": "worker-learning-e2e",
            "status": "completed",
                "metrics": {"win_rate": 0.73},
                "artifacts": [
                    {
                        "type": "adapter",
                        "uri": str(source_adapter),
                        "sha256": artifact_sha,
                    }
                ],
            },
    )
    assert gateway_resp.status_code == 200, gateway_resp.text
    deployment = gateway_resp.json()["latest_deployment"]
    deployment_id = deployment["id"]
    activated_deployment_id = deployment_id
    assert deployment["status"] == "active"
    assert deployment["target_skill_ids"] == [skill_id]
    assert deployment["deployment_target_gateway_id"] == deployment_gateway_id
    auto_request = gateway_resp.json()["auto_evaluation"]["auto_deployment_request"]
    assert auto_request["status"] == "active"
    assert auto_request["review_required"] is False
    assert auto_request["deployment_target_gateway_id"] == deployment_gateway_id

    async with db_mod.async_session_factory() as session:
        session.add(
            ExecutionRun(
                id=run_id,
                skill_id=skill_id,
                run_mode="manual_real",
                trigger_type="manual",
                status="running",
                source_instance_id=gateway_id,
                metadata_json={},
            )
        )
        await session.commit()

    run_token = issue_run_token(
        skill_id=skill_id,
        run_id=run_id,
        instance_id=gateway_id,
        skill_git_commit_full=commit,
        department="AI小组",
    )
    analyze_resp = await client.post(
        "/api/intelligence/analyze",
        headers={"X-Run-Token": run_token},
        json={
            "skill_id": skill_id,
            "skill_git_commit_full": commit,
            "run_id": run_id,
            "instance_id": gateway_id,
            "prompt_version": "analysis_v1",
            "cache_key": "learning-e2e-loop-cache",
            "context_pack": {"facts": {"gmv": 100, "conversion": 0.12}},
        },
    )
    assert analyze_resp.status_code == 200, analyze_resp.text
    analyze_body = analyze_resp.json()
    assert analyze_body["active_model_deployment"]["id"] == activated_deployment_id
    assert analyze_body["deployed_model_inference"]["status"] == "used"
    assert inference_calls
    assert inference_calls[0]["instance_id"] == deployment_gateway_id
    assert inference_calls[0]["payload"]["deployment_id"] == activated_deployment_id

    submit_resp = await client.post(
        "/api/executions/submit-result",
        headers={"X-Run-Token": run_token},
        json={
            "skill_id": skill_id,
            "run_id": run_id,
            "instance_id": gateway_id,
            "output": {"summary": "学习闭环模型辅助形成了最终经营动作。"},
            "params": {"date": "2026-06-18"},
            "triggered_by": "skill_sdk",
        },
    )
    assert submit_resp.status_code == 200, submit_resp.text
    submit_body = submit_resp.json()
    assert submit_body["model_context"]["model_deployment_id"] == activated_deployment_id
    assert submit_body["model_context"]["inference_status"] == "used"
    assert submit_body["model_context"]["inference_gateway_id"] == deployment_gateway_id

    async with db_mod.async_session_factory() as session:
        stored_job = await session.get(TrainingJob, job_id)
        stored_deployment = await session.get(TrainingModelDeployment, activated_deployment_id)
        decision = (
            await session.execute(select(DecisionLog).where(DecisionLog.run_id == run_id))
        ).scalar_one()
        run = await session.get(ExecutionRun, run_id)
        analyze_run = (
            await session.execute(
                select(IntelligenceAnalyzeRun)
                .where(IntelligenceAnalyzeRun.run_id == run_id)
                .order_by(IntelligenceAnalyzeRun.id.desc())
                .limit(1)
            )
        ).scalar_one()
        model_edge = (
            await session.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "model_deployment")
                .where(LearningFlowEdge.from_id == activated_deployment_id)
                .where(LearningFlowEdge.to_type == "decision_log")
                .where(LearningFlowEdge.to_id == str(decision.id))
                .where(LearningFlowEdge.relation == "used_as_model")
            )
        ).scalar_one()
        training_edge = (
            await session.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "agent")
                .where(LearningFlowEdge.from_id == gateway_id)
                .where(LearningFlowEdge.to_type == "training_job")
                .where(LearningFlowEdge.to_id == job_id)
                .where(LearningFlowEdge.relation == "controlled_training")
            )
        ).scalar_one()
        runtime_edge = (
            await session.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "agent")
                .where(LearningFlowEdge.from_id == gateway_id)
                .where(LearningFlowEdge.to_type == "run")
                .where(LearningFlowEdge.to_id == run_id)
                .where(LearningFlowEdge.relation == "controlled_run")
            )
        ).scalar_one()
        inference_edge = (
            await session.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "agent")
                .where(LearningFlowEdge.from_id == deployment_gateway_id)
                .where(LearningFlowEdge.to_type == "intelligence_analyze")
                .where(LearningFlowEdge.to_id == str(analyze_run.id))
                .where(LearningFlowEdge.relation == "controlled_inference")
            )
        ).scalar_one()
        recycled_sample = (
            await session.execute(
                select(LearningArtifact)
                .where(LearningArtifact.run_id == run_id)
                .where(LearningArtifact.artifact_kind == "training_sample")
                .where(LearningArtifact.sink_type == "training_sample")
            )
        ).scalars().all()
        recycled_sample = next(
            item
            for item in recycled_sample
            if (item.content_json or {}).get("source_type") == "execution_trace"
        )
        recycled_content = recycled_sample.content_json or {}

    assert stored_job.status == "completed"
    assert (stored_job.spec_json or {})["source"] == "learning_sample_threshold"
    assert stored_deployment.status == "active"
    assert stored_deployment.artifact_ref_json["sha256"] == artifact_sha
    assert decision.model_id == activated_deployment_id
    assert run.metadata_json["model_context"]["model_deployment_id"] == activated_deployment_id
    assert model_edge.metadata_json["inference_status"] == "used"
    assert training_edge.metadata_json["contract"]["input_channels"] == [
        "training_job.gateway_payload",
        "learning_artifacts.dataset_package",
    ]
    assert training_edge.metadata_json["contract"]["output_channels"] == [
        "training_job.gateway_result",
        "training_artifact_refs",
        "training_metrics",
    ]
    assert training_edge.metadata_json["contract"]["lifecycle_ready"] is True
    assert runtime_edge.metadata_json["contract"]["input_channels"] == [
        "node_schedule_snapshot",
        "skill_run_request",
    ]
    assert runtime_edge.metadata_json["contract"]["output_channels"] == [
        "decision_log",
        "execution_artifact",
        "execution_run",
    ]
    assert runtime_edge.metadata_json["contract"]["flow_traceable"] is True
    assert inference_edge.metadata_json["contract"]["lifecycle_ready"] is True
    assert recycled_sample.status == "materialized"
    assert recycled_content["source_type"] == "execution_trace"
    assert recycled_content["source_channel"] == "skill_sdk"
    assert recycled_content["input"]["run"]["source_instance_id"] == gateway_id
    assert recycled_content["input"]["steps"][0]["input"]["date"] == "2026-06-18"
    assert recycled_content["input"]["steps"][0]["input"]["model_context"]["model_deployment_id"] == activated_deployment_id
    assert recycled_content["output"]["steps"][0]["output"]["summary"] == "学习闭环模型辅助形成了最终经营动作。"
    assert (
        recycled_content["output"]["steps"][0]["output"]["_skillforge_meta"]["model_context"]["model_deployment_id"]
        == activated_deployment_id
    )
    assert recycled_content["formed_data"]["metadata"]["model_context"]["model_deployment_id"] == activated_deployment_id
    assert recycled_content["formed_data"]["runtime_agent"]["agent_id"] == gateway_id
    assert recycled_content["formed_data"]["runtime_agent"]["contract"]["flow_traceable"] is True

    async with db_mod.async_session_factory() as session:
        next_cycle_user = await session.get(User, "learning-e2e-admin")
        stored_recycled = await session.get(LearningArtifact, recycled_sample.id)
        stored_recycled.created_at = now_bjt() - timedelta(days=1)
        stored_recycled.updated_at = now_bjt() - timedelta(days=1)
        next_cycle = await ensure_training_candidates_from_learning_samples(
            session,
            next_cycle_user,
            days=7,
            min_sample_count=5,
            max_skills=1,
        )
        await session.commit()

    assert next_cycle["created"]
    next_cycle_job_id = next_cycle["created"][0]["training_job_id"]
    assert next_cycle_job_id != job_id
    assert next_cycle["created"][0]["sample_total"] >= 5

    async with db_mod.async_session_factory() as session:
        next_job = await session.get(TrainingJob, next_cycle_job_id)
        next_spec = next_job.spec_json or {}
    assert next_job.status == "awaiting_review"
    assert next_spec["source"] == "learning_sample_threshold"
    assert recycled_sample.id in next_spec["dataset"]["learning_artifact_ids"]
    assert next_spec["dataset"]["manifest_hash"] != (stored_job.spec_json or {})["dataset"]["manifest_hash"]
    assert next_spec["deployment"]["auto_request"] is True


@pytest.mark.asyncio
async def test_learning_auto_flow_worker_captures_recent_lifecycle_updates_for_old_records(client, _isolate_learning_auto_flow_worker_state):
    import app.database as db_mod
    from sqlalchemy import select

    from app.learning.models import LearningArtifact, LearningEvent, LearningFlowEdge
    from app.learning.worker import run_learning_auto_flow_once
    from app.todos.models import DecisionRequest, TodoDispatchTask
    from app.training.models import TrainingJob, TrainingModelDeployment

    old_at = now_bjt() - timedelta(days=30)
    recent_at = now_bjt()

    async with db_mod.async_session_factory() as session:
        session.add(
            Skill(
                id="learn-worker-lifecycle-skill",
                name="自动流动生命周期 Skill",
                display_name="自动流动生命周期 Skill",
                department="AI小组",
                status="active",
                visibility="department",
                approval_level=0,
            )
        )
        request = DecisionRequest(
            id="req-worker-old-dispatch",
            source_type="skill",
            source_id="run-worker-old-dispatch",
            skill_id="learn-worker-lifecycle-skill",
            run_id="run-worker-old-dispatch",
            kind="dispatch",
            title="老派发任务",
            summary="创建很早，但刚刚在钉钉完成。",
            payload={"item_id": "item-worker-lifecycle"},
            aggregate_status="completed",
            aggregate_decision="approved",
            sla_at=old_at,
            created_at=old_at,
            completed_at=recent_at,
        )
        session.add(request)
        dispatch_task = TodoDispatchTask(
            request_id=request.id,
            executor="worker-executor",
            content="运营动作：补充成交承接说明",
            deadline=old_at,
            status="done",
            ack_note="刚刚从钉钉补充完成说明",
            ack_channel="dingtalk",
            created_at=old_at,
            updated_at=recent_at,
            dispatched_at=old_at,
            ack_at=recent_at,
            extra={
                "ack_feedback_payload": {
                    "source_channel": "dingtalk",
                    "action": "dispatch_ack",
                    "note_text": "刚刚从钉钉补充完成说明",
                    "fields": {
                        "source": "dingtalk_interactive_card",
                        "feedback_type": "已执行",
                        "rating": 5,
                    },
                }
            },
        )
        session.add(dispatch_task)
        training_job = TrainingJob(
            id="tj-worker-old-completed",
            title="老训练任务刚完成",
            department="AI小组",
            created_by="admin",
            status="completed",
            job_type="lora",
            target_skill_id="learn-worker-lifecycle-skill",
            target_gateway_id="worker-training-agent",
            dataset_ref="learning-artifacts://learn-worker-lifecycle-skill/training/latest",
            objective="验证最近完成的老训练任务会被自动回流。",
            created_at=old_at,
            updated_at=recent_at,
            approved_at=old_at,
            gateway_payload_json={
                "job_id": "tj-worker-old-completed",
                "dataset_package": {
                    "format": "skillforge_sft_jsonl_v1",
                    "source": "learning_artifacts",
                    "dataset_ref": "learning-artifacts://learn-worker-lifecycle-skill/training/latest",
                    "target_skill_id": "learn-worker-lifecycle-skill",
                    "manifest_hash": "manifest-worker-lifecycle",
                    "sample_count": 1,
                    "train_count": 1,
                    "eval_count": 0,
                    "lineage": {
                        "source": "learning_artifacts",
                        "training_job_id": "tj-worker-old-completed",
                        "target_skill_id": "learn-worker-lifecycle-skill",
                        "learning_artifact_ids": ["la-worker-lifecycle"],
                        "manifest_hash": "manifest-worker-lifecycle",
                    },
                    "raw_payload_returned": False,
                    "samples": [
                        {
                            "id": "la-worker-lifecycle",
                            "split": "train",
                            "input": "raw lifecycle input should not leak",
                            "output": "raw lifecycle output should not leak",
                            "metadata": {
                                "source_type": "dispatch_ack",
                                "source_channel": "dingtalk",
                                "input_sha256": "d" * 64,
                                "output_sha256": "e" * 64,
                                "sample_sha256": "f" * 64,
                            },
                        }
                    ],
                },
            },
        )
        deployment = TrainingModelDeployment(
            id="md-worker-old-active",
            job_id=training_job.id,
            department="AI小组",
            model_family="learn-worker-lifecycle-skill:lora",
            artifact_id="artifact-worker-lifecycle",
            artifact_ref_json={"sha256": "a" * 64, "uri": "artifact://worker/lifecycle"},
            target_skill_ids_json=["learn-worker-lifecycle-skill"],
            status="active",
            rollout_percent=100,
            requested_by="admin",
            created_at=old_at,
            updated_at=recent_at,
            approved_at=recent_at,
            activated_at=recent_at,
        )
        session.add_all([training_job, deployment])
        await session.flush()
        dispatch_task_id = dispatch_task.id
        await session.commit()

    result = await run_learning_auto_flow_once(days=7, limit=50, materialize=True)
    assert result["status"] == "succeeded"
    captured = result["result"]["captured"]
    assert captured["decision_request"] >= 1
    assert captured["todo_dispatch_task"] >= 1
    assert captured["training_job"] >= 1
    assert captured["model_deployment"] >= 1

    async with db_mod.async_session_factory() as session:
        dispatch_event = (
            await session.execute(
                select(LearningEvent)
                .where(LearningEvent.source_type == "todo_dispatch_task")
                .where(LearningEvent.source_id == str(dispatch_task_id))
            )
        ).scalar_one()
        dispatch_sample = (
            await session.execute(
                select(LearningArtifact)
                .where(LearningArtifact.event_id == dispatch_event.id)
                .where(LearningArtifact.artifact_kind == "training_sample")
            )
        ).scalar_one()
        training_event = (
            await session.execute(
                select(LearningEvent)
                .where(LearningEvent.source_type == "training_job")
                .where(LearningEvent.source_id == "tj-worker-old-completed")
            )
        ).scalar_one()
        deployment_edge = (
            await session.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "model_deployment")
                .where(LearningFlowEdge.from_id == "md-worker-old-active")
                .where(LearningFlowEdge.to_type == "skill")
                .where(LearningFlowEdge.to_id == "learn-worker-lifecycle-skill")
                .where(LearningFlowEdge.relation == "deployed_to")
            )
        ).scalar_one()

    assert dispatch_event.event_type == "todo.completed"
    assert dispatch_sample.status == "materialized"
    assert dispatch_sample.content_json["human_feedback_input"]["fields"]["feedback_type"] == "已执行"
    assert training_event.event_type == "training.job.completed"
    assert training_event.metadata_json["dataset_package"]["manifest_hash"] == "manifest-worker-lifecycle"
    assert training_event.metadata_json["dataset_package"]["samples"] == "[REDACTED]"
    assert "raw lifecycle input should not leak" not in json.dumps(training_event.metadata_json, ensure_ascii=False)
    assert deployment_edge.metadata_json["status"] == "active"


def test_learning_automation_status_prefers_newer_persisted_run():
    from app.learning.router import _merge_latest_automation_run

    automation = {
        "status": "failed",
        "run_id": "lar-old",
        "finished_at": "2026-06-03T17:14:08+08:00",
        "error": "relation learning_automation_runs does not exist",
    }
    latest_run = {
        "id": "lar-new",
        "status": "succeeded",
        "started_at": "2026-06-03T17:16:00+08:00",
        "finished_at": "2026-06-03T17:16:10+08:00",
        "error": None,
        "result": {"captured": {"execution_run": 1}},
    }

    merged = _merge_latest_automation_run(automation, latest_run)

    assert merged["status"] == "succeeded"
    assert merged["run_id"] == "lar-new"
    assert merged["error"] is None
    assert merged["result"]["captured"]["execution_run"] == 1


def test_learning_automation_status_keeps_running_state():
    from app.learning.router import _merge_latest_automation_run

    automation = {
        "status": "running",
        "run_id": "lar-current",
        "started_at": "2026-06-03T17:20:00+08:00",
    }
    latest_run = {
        "id": "lar-older",
        "status": "succeeded",
        "finished_at": "2026-06-03T17:19:00+08:00",
        "error": None,
        "result": {},
    }

    merged = _merge_latest_automation_run(automation, latest_run)

    assert merged["status"] == "running"
    assert merged["run_id"] == "lar-current"
