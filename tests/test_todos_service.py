import pytest


async def _seed_post_training_model(session, *, skill_id: str, gateway_id: str = "node-post-train-todo"):
    import json

    from app.auth.models import User
    from app.execution.models import OpenClawInstance
    from app.skills.core.models import Skill
    from app.training.models import TrainingJob, TrainingModelDeployment

    user = await session.get(User, "model-reviewer")
    if user is None:
        session.add(User(
            id="model-reviewer",
            username="model-reviewer",
            name="模型审核员",
            role="ai_engineer",
            department="销售二部",
            is_active=True,
            state="active",
        ))
    skill = await session.get(Skill, skill_id)
    if skill is None:
        session.add(Skill(
            id=skill_id,
            name=skill_id,
            department="销售二部",
            status="active",
            approval_level=1,
        ))
    instance = await session.get(OpenClawInstance, gateway_id)
    if instance is None:
        session.add(OpenClawInstance(
            id=gateway_id,
            name="后训练评估节点",
            department="销售二部",
            gateway_url="ws://post-train-todo",
            reload_hook_url="",
            reload_token="",
            agent_type="aiclaw",
            agent_purpose="mixed",
            bridge_gateway_kind="openclaw",
            bridge_capabilities_json=json.dumps({"ops": ["training.inference"]}),
            is_active=True,
        ))
    job = TrainingJob(
        id=f"train-{skill_id[:18]}",
        title="后训练评估任务",
        department="销售二部",
        created_by="model-reviewer",
        status="completed",
        target_skill_id=skill_id,
        target_gateway_id=gateway_id,
        spec_json={"model_profile": "qwen3.5-4b"},
    )
    deployment = TrainingModelDeployment(
        id=f"deploy-{skill_id[:18]}",
        job_id=job.id,
        department="销售二部",
        model_family=f"{skill_id}:qwen3.5-4b-qlora",
        artifact_id=f"artifact-{skill_id[:18]}",
        artifact_ref_json={"uri": f"/tmp/{skill_id}.tar.gz", "sha256": "a" * 64},
        target_skill_ids_json=[skill_id],
        status="active",
        rollout_percent=100,
        requested_by="model-reviewer",
    )
    session.add_all([job, deployment])
    await session.flush()


async def _seed_shared_platform_post_training_model(session, *, gateway_id: str = "node-shared-platform-model"):
    import json

    from app.auth.models import User
    from app.execution.models import OpenClawInstance
    from app.training.models import TrainingJob, TrainingModelDeployment

    if await session.get(User, "model-reviewer") is None:
        session.add(User(
            id="model-reviewer",
            username="model-reviewer",
            name="模型审核员",
            role="ai_engineer",
            department="销售二部",
            is_active=True,
            state="active",
        ))
    session.add(OpenClawInstance(
        id=gateway_id,
        name="全平台后训练评估节点",
        department="销售二部",
        gateway_url="ws://shared-platform-model",
        reload_hook_url="",
        reload_token="",
        agent_type="aiclaw",
        agent_purpose="mixed",
        bridge_gateway_kind="openclaw",
        bridge_capabilities_json=json.dumps({"ops": ["training.inference"]}),
        is_active=True,
    ))
    job = TrainingJob(
        id="train-shared-platform-history",
        title="全平台历史数据增量训练",
        department="销售二部",
        created_by="model-reviewer",
        status="completed",
        target_skill_id="samplebrand-video-low-consumption-operator-v1",
        target_gateway_id=gateway_id,
        spec_json={"model_profile": "qwen3.5-4b"},
    )
    deployment = TrainingModelDeployment(
        id="deploy-shared-platform-history",
        job_id=job.id,
        department="销售二部",
        model_family="samplebrand-video-low-consumption-operator-v1:platform-full-history-adapter",
        artifact_id="artifact-shared-platform-history",
        artifact_ref_json={"uri": "/tmp/shared-platform-history.tar.gz", "sha256": "b" * 64},
        target_skill_ids_json=["samplebrand-video-low-consumption-operator-v1"],
        status="canary",
        rollout_percent=10,
        requested_by="model-reviewer",
    )
    session.add_all([job, deployment])
    await session.flush()


@pytest.mark.asyncio
async def test_target_skill_todo_creation_stores_post_training_evaluation(client, monkeypatch):
    from sqlalchemy import select

    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.database import async_session_factory
    from app.todos.models import DecisionRequest
    from app.todos.post_training_evaluation import POST_TRAINING_EVALUATION_KEY
    from app.todos.service import todo_service

    skill_id = "samplebrand-video-low-consumption-operator-v1"
    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "node-post-train-todo")
    calls = []

    async def fake_inference(self, payload, *, timeout=300):
        calls.append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "text": "评估结论：可用\n关键依据：待办输出包含低消耗视频和建议动作。\n建议动作：管理员复核后派发。\n审核建议：建议通过。",
            "metrics": {"generated_tokens": 42, "duration_ms": 21000},
            "finish_reason": "stop",
        }

    monkeypatch.setattr(AIClawClient, "run_training_inference", fake_inference)

    async with async_session_factory() as session:
        await _seed_post_training_model(session, skill_id=skill_id)
        requests = await todo_service.create_from_execution(
            session,
            run_id="run-post-training-eval-1",
            skill_id=skill_id,
            skill_meta={"name": "SampleBrand 低消耗视频", "approval_level": 1},
            decision={
                "input_snapshot": {"date": "2026-06-24"},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "低消耗视频处理",
                        "summary": "需要处理低消耗视频",
                        "reviewers": ["model-reviewer"],
                        "payload": {
                            "card_type": "video_low_consumption_decision_card",
                            "recommended_decision": "建议改版",
                            "key_metrics": [{"name": "低消耗视频数", "value": "1"}],
                        },
                        "tasks": [{"content": "输出视频改版方案"}],
                    }]
                },
            },
        )
        await session.commit()
        request_id = requests[0].id

    async with async_session_factory() as session:
        request = (await session.execute(select(DecisionRequest).where(DecisionRequest.id == request_id))).scalar_one()
        evaluation = request.payload[POST_TRAINING_EVALUATION_KEY]

    assert evaluation["status"] == "used"
    assert evaluation["model_deployment_id"].startswith("deploy-samplebrand-video-low-")
    assert evaluation["text"].startswith("评估结论：可用")
    assert evaluation["metrics"]["tokens_per_second"] == 2.0
    assert calls
    assert calls[0]["instance_id"] == "node-post-train-todo"
    assert calls[0]["payload"]["deployment_id"] == evaluation["model_deployment_id"]
    assert calls[0]["payload"]["max_prompt_chars"] == 5600
    assert len(calls[0]["payload"]["prompt"]) <= 5600
    assert calls[0]["payload"]["prompt"].rstrip().endswith("<|assistant|>")
    assert calls[0]["timeout"] == 45


@pytest.mark.asyncio
async def test_tmall_skill_without_dedicated_deployment_uses_shared_platform_model(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.models import User
    from app.database import async_session_factory
    from app.skills.core.models import Skill
    from app.todos.post_training_evaluation import POST_TRAINING_EVALUATION_KEY
    from app.todos.service import todo_service

    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "node-shared-platform-model")
    calls = []

    async def fake_inference(self, payload, *, timeout=300):
        calls.append({"instance_id": self.instance_id, "payload": payload, "timeout": timeout})
        return {
            "text": "评估结论：可用\n关键依据：输出包含商品下滑数据和建议动作。\n建议动作：管理员复核后派发。",
            "metrics": {"generated_tokens": 8, "duration_ms": 4000},
        }

    monkeypatch.setattr(AIClawClient, "run_training_inference", fake_inference)

    async with async_session_factory() as session:
        session.add(User(
            id="model-reviewer",
            username="model-reviewer",
            name="模型审核员",
            role="ai_engineer",
            department="销售二部",
            is_active=True,
            state="active",
        ))
        session.add(Skill(
            id="tmall-link-decline-operator-v1",
            name="Tmall 下滑治理",
            department="销售二部",
            status="active",
            approval_level=1,
        ))
        await _seed_shared_platform_post_training_model(session)
        requests = await todo_service.create_from_execution(
            session,
            run_id="run-post-training-shared-model-1",
            skill_id="tmall-link-decline-operator-v1",
            skill_meta={"name": "Tmall 下滑治理", "approval_level": 1},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "review",
                        "title": "商品下滑复核",
                        "summary": "需要复核商品下滑",
                        "reviewers": ["model-reviewer"],
                        "payload": {"card_type": "product_decline_decision_card", "item_id": "123"},
                    }]
                },
            },
        )
        await session.commit()
        evaluation = requests[0].payload[POST_TRAINING_EVALUATION_KEY]

    assert evaluation["status"] == "used"
    assert evaluation["model_deployment_id"] == "deploy-shared-platform-history"
    assert evaluation["model_scope"] == "shared_platform_history_fallback"
    assert evaluation["text"].startswith("评估结论：可用")
    assert calls[0]["instance_id"] == "node-shared-platform-model"


@pytest.mark.asyncio
async def test_post_training_evaluation_marks_unusable_echo_as_failed(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.models import User
    from app.database import async_session_factory
    from app.skills.core.models import Skill
    from app.todos.post_training_evaluation import POST_TRAINING_EVALUATION_KEY
    from app.todos.service import todo_service

    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "node-shared-platform-model")

    async def echo_inference(self, payload, *, timeout=300):
        return {
            "text": '{"dispatch_task": {"content": "模型回显输入片段"}}',
            "metrics": {"generated_tokens": 5, "duration_ms": 1000},
        }

    monkeypatch.setattr(AIClawClient, "run_training_inference", echo_inference)

    async with async_session_factory() as session:
        session.add(User(
            id="model-reviewer",
            username="model-reviewer",
            name="模型审核员",
            role="ai_engineer",
            department="销售二部",
            is_active=True,
            state="active",
        ))
        session.add(Skill(
            id="tmall-link-decline-operator-v1",
            name="Tmall 下滑治理",
            department="销售二部",
            status="active",
            approval_level=1,
        ))
        await _seed_shared_platform_post_training_model(session)
        requests = await todo_service.create_from_execution(
            session,
            run_id="run-post-training-echo-failed-1",
            skill_id="tmall-link-decline-operator-v1",
            skill_meta={"name": "Tmall 下滑治理", "approval_level": 1},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "review",
                        "title": "商品下滑复核",
                        "summary": "需要复核商品下滑",
                        "reviewers": ["model-reviewer"],
                        "payload": {"card_type": "product_decline_decision_card", "item_id": "123"},
                    }]
                },
            },
        )
        await session.commit()
        evaluation = requests[0].payload[POST_TRAINING_EVALUATION_KEY]

    assert evaluation["status"] == "failed"
    assert evaluation["error_code"] == "POST_TRAINING_EVALUATION_UNUSABLE_OUTPUT"
    assert "模型未返回可用评估文本" in evaluation["reason"]


@pytest.mark.asyncio
async def test_post_training_evaluation_empty_text_does_not_store_result_json_preview(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.aiclaw.bridge_registry import bridge_registry
    from app.auth.models import User
    from app.database import async_session_factory
    from app.skills.core.models import Skill
    from app.todos.post_training_evaluation import POST_TRAINING_EVALUATION_KEY
    from app.todos.service import todo_service

    monkeypatch.setattr(bridge_registry, "is_online", lambda instance_id: instance_id == "node-shared-platform-model")

    async def empty_inference(self, payload, *, timeout=300):
        return {
            "ok": True,
            "backend": "bridge_local_inference",
            "status": "completed",
            "text": "",
            "metrics": {"generated_tokens": 1, "duration_ms": 4000},
        }

    monkeypatch.setattr(AIClawClient, "run_training_inference", empty_inference)

    async with async_session_factory() as session:
        session.add(User(
            id="model-reviewer",
            username="model-reviewer",
            name="模型审核员",
            role="ai_engineer",
            department="销售二部",
            is_active=True,
            state="active",
        ))
        session.add(Skill(
            id="tmall-link-decline-operator-v1",
            name="Tmall 下滑治理",
            department="销售二部",
            status="active",
            approval_level=1,
        ))
        await _seed_shared_platform_post_training_model(session)
        requests = await todo_service.create_from_execution(
            session,
            run_id="run-post-training-empty-output-1",
            skill_id="tmall-link-decline-operator-v1",
            skill_meta={"name": "Tmall 下滑治理", "approval_level": 1},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "review",
                        "title": "商品下滑复核",
                        "summary": "需要复核商品下滑",
                        "reviewers": ["model-reviewer"],
                        "payload": {"card_type": "product_decline_decision_card", "item_id": "123"},
                    }]
                },
            },
        )
        await session.commit()
        evaluation = requests[0].payload[POST_TRAINING_EVALUATION_KEY]

    assert evaluation["status"] == "failed"
    assert evaluation["error_code"] == "POST_TRAINING_EVALUATION_UNUSABLE_OUTPUT"
    assert "raw_text_preview" not in evaluation


def test_post_training_prompt_keeps_assistant_marker_and_valid_json_for_large_payload():
    import json

    from app.todos.post_training_evaluation import (
        POST_TRAINING_EVALUATION_MAX_PROMPT_CHARS,
        _clean_inference_output,
        _prompt_for_todo,
    )

    payload = {
        "card_type": "product_decline_decision_card",
        "item_id": "800000000000",
        "item_title": "测试商品",
        "key_metrics": [
            {
                "name": f"指标{i}",
                "value": "下滑",
                "note": "这是一段很长的指标说明" * 50,
            }
            for i in range(120)
        ],
        "analysis_basis": [
            {"basis": "数据缺口" * 80, "dimension": f"维度{i}"}
            for i in range(80)
        ],
        "detail_markdown": "不应进入模型上下文" * 1000,
        "dispatch_tasks": [
            {"executor": "ops", "content": "补采并复核" * 100, "extra": {"k": "v" * 500}}
            for _ in range(20)
        ],
    }

    prompt = _prompt_for_todo(
        skill_id="tmall-link-decline-operator-v1",
        run_id="run-large",
        title="大 payload 复核",
        summary="需要评估",
        kind="dispatch",
        payload=payload,
        dispatch_tasks=payload["dispatch_tasks"],
    )
    assistant_index = prompt.find("<|assistant|>")
    user_start = prompt.find("<|user|>") + len("<|user|>")
    material = json.loads(prompt[user_start:assistant_index].strip())

    assert len(prompt) <= POST_TRAINING_EVALUATION_MAX_PROMPT_CHARS
    assert assistant_index > 0
    assert prompt.rstrip().endswith("<|assistant|>")
    assert "detail_markdown" not in json.dumps(material, ensure_ascii=False)
    assert len(material["dispatch_tasks"]) <= 4
    assert material.get("reason") != "context_exceeds_prompt_limit"
    assert "preview" not in material

    tmall_payload = {
        "card_type": "product_decline_decision_card",
        "priority": "P3",
        "decision_type": "approve_dispatch",
        "recommended_decision": "建议通过：主因 商品基础/价格，先补采缺口数据和计划级明细，暂不直接改价。",
        "confidence": "低",
        "business_action_allowed": False,
        "item_id": "800556990323",
        "item_title": "【示例品牌超薄玻玻体验】玻尿酸避孕套超薄官方旗舰店正品安全男用",
        "decline_amount_text": "访客少9",
        "decline_coef": 0.0,
        "data_quality": "存在 6 个数据缺口，需先补采后再做价格/投放强动作",
        "data_audit_summary": {
            "total": 50,
            "completeness_gap_count": 41,
            "accuracy_pending_count": 26,
        },
        "market_top300_status": {"value": "未进Top300", "status": "当前未进 Top300"},
        "primary_indicator": {"label": "实时下滑系数", "value": "-0.0", "delta_text": "访客少9"},
        "key_metrics": [
            {
                "name": f"指标{i}",
                "value": "-0.0",
                "delta": "访客少9",
                "note": "访客下滑" * 40,
                "source": "生意参谋",
                "status": "medium",
            }
            for i in range(40)
        ],
        "analysis_basis": [
            {
                "dimension": "主因归因",
                "basis": "主因=商品基础/价格；SKU 最终到手价待核对" * 30,
                "data_gap": "存在 6 个数据缺口",
                "confidence": "低",
            }
            for _ in range(20)
        ],
        "data_quality_items": [
            {"dimension": "免费流", "issue": "搜索节点未命中，免费转化不可判定" * 20}
            for _ in range(20)
        ],
        "operation_actions": [
            {"section": "免费流量端", "action": "补采搜索访客并核对主图点击率" * 30, "owner": "运营"}
            for _ in range(20)
        ],
        "dispatch_tasks": [
            {"content": "补采缺口数据并复核" * 100, "deadline": "2026-06-26T23:59:59+08:00"}
            for _ in range(10)
        ],
    }
    prompt = _prompt_for_todo(
        skill_id="tmall-link-decline-operator-v1",
        run_id="run-large-tmall",
        title="商品诊断卡",
        summary="需要复核商品下滑",
        kind="dispatch",
        payload=tmall_payload,
        dispatch_tasks=tmall_payload["dispatch_tasks"],
    )
    assistant_index = prompt.find("<|assistant|>")
    user_start = prompt.find("<|user|>") + len("<|user|>")
    material = json.loads(prompt[user_start:assistant_index].strip())
    dumped = json.dumps(material, ensure_ascii=False)

    assert len(prompt) <= POST_TRAINING_EVALUATION_MAX_PROMPT_CHARS
    assert prompt.rstrip().endswith("<|assistant|>")
    assert material.get("reason") != "context_exceeds_prompt_limit"
    assert "preview" not in material
    assert dumped.count('"keys"') < 8
    assert "访客少9" in dumped
    assert "商品基础/价格" in dumped

    cleaned = _clean_inference_output(
        "### 1. 评估结论\n需复核\n### 2. 关键依据\n包含数据缺口。\n\nsystem\n你是 SkillForge 后训练模型评估器。"
    )

    assert "system" not in cleaned
    assert "你是 SkillForge" not in cleaned


@pytest.mark.asyncio
async def test_target_skill_without_any_deployment_marks_post_training_skipped(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.post_training_evaluation import POST_TRAINING_EVALUATION_KEY
    from app.todos.service import todo_service

    async def fail_inference(*args, **kwargs):
        raise AssertionError("inference should not run without active deployment")

    monkeypatch.setattr(AIClawClient, "run_training_inference", fail_inference)

    async with async_session_factory() as session:
        session.add(User(
            id="model-reviewer",
            username="model-reviewer",
            name="模型审核员",
            role="ai_engineer",
            department="销售二部",
            is_active=True,
            state="active",
        ))
        requests = await todo_service.create_from_execution(
            session,
            run_id="run-post-training-skipped-1",
            skill_id="tmall-link-decline-operator-v1",
            skill_meta={"name": "Tmall 下滑治理", "approval_level": 1},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "review",
                        "title": "商品下滑复核",
                        "summary": "需要复核商品下滑",
                        "reviewers": ["model-reviewer"],
                        "payload": {"card_type": "product_decline_decision_card", "item_id": "123"},
                    }]
                },
            },
        )
        await session.commit()
        evaluation = requests[0].payload[POST_TRAINING_EVALUATION_KEY]

    assert evaluation["status"] == "skipped"
    assert "未找到" in evaluation["reason"]


@pytest.mark.asyncio
async def test_non_target_skill_does_not_call_post_training_evaluation(client, monkeypatch):
    from app.aiclaw.client import AIClawClient
    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.post_training_evaluation import POST_TRAINING_EVALUATION_KEY
    from app.todos.service import todo_service

    async def fail_inference(*args, **kwargs):
        raise AssertionError("non-target skill should not call inference")

    monkeypatch.setattr(AIClawClient, "run_training_inference", fail_inference)

    async with async_session_factory() as session:
        session.add(User(
            id="model-reviewer",
            username="model-reviewer",
            name="模型审核员",
            role="ai_engineer",
            department="销售二部",
            is_active=True,
            state="active",
        ))
        requests = await todo_service.create_from_execution(
            session,
            run_id="run-post-training-non-target-1",
            skill_id="some-other-skill",
            skill_meta={"name": "普通 Skill", "approval_level": 1},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "review",
                        "title": "普通待办",
                        "summary": "无需后训练评估",
                        "reviewers": ["model-reviewer"],
                        "payload": {"summary": "ok"},
                    }]
                },
            },
        )
        await session.commit()

    assert POST_TRAINING_EVALUATION_KEY not in requests[0].payload


@pytest.mark.asyncio
async def test_post_training_backfill_api_dry_run_lists_candidates(client):
    from datetime import datetime

    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.models import AITodo, DecisionRequest

    async with async_session_factory() as session:
        session.add(User(
            id="model-reviewer",
            username="model-reviewer",
            name="模型审核员",
            role="ai_engineer",
            department="销售二部",
            is_active=True,
            state="active",
        ))
        request = DecisionRequest(
            id="dr-post-training-api-dry-run-1",
            source_type="test",
            source_id="post-training-api-dry-run-1",
            skill_id="samplebrand-video-low-consumption-operator-v1",
            run_id="run-post-training-api-dry-run-1",
            kind="review",
            title="低消耗视频复核",
            summary="dry-run 候选",
            payload={"card_type": "video_low_consumption_decision_card"},
            sla_at=datetime(2026, 6, 26),
        )
        session.add(request)
        await session.flush()
        session.add(AITodo(request_id=request.id, kind="review", assignee="model-reviewer", status="pending"))
        await session.commit()

    resp = await client.post(
        "/api/todos/post-training-evaluations/backfill",
        json={
            "skill_ids": ["samplebrand-video-low-consumption-operator-v1"],
            "limit": 10,
            "since_days": 30,
            "dry_run": True,
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["dry_run"] is True
    assert body["checked"] >= 1
    assert body["candidates"] >= 1


@pytest.mark.asyncio
async def test_list_todos_date_to_includes_whole_selected_day(client):
    from datetime import datetime

    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.models import AITodo, DecisionRequest
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        session.add(User(id="date-user", username="date-user", name="Date User", role="operator", is_active=True))
        first = DecisionRequest(
            id="req-date-1",
            source_type="test",
            source_id="date-1",
            skill_id="skill-date-filter",
            kind="review",
            title="范围内待办",
            summary="",
            payload={},
            sla_at=datetime(2026, 5, 3),
        )
        second = DecisionRequest(
            id="req-date-2",
            source_type="test",
            source_id="date-2",
            skill_id="skill-date-filter",
            kind="review",
            title="范围外待办",
            summary="",
            payload={},
            sla_at=datetime(2026, 5, 3),
        )
        session.add_all([first, second])
        await session.flush()
        session.add_all([
            AITodo(
                request_id=first.id,
                kind="review",
                assignee="date-user",
                status="pending",
                created_at=datetime(2026, 5, 1, 15, 30),
            ),
            AITodo(
                request_id=second.id,
                kind="review",
                assignee="date-user",
                status="pending",
                created_at=datetime(2026, 5, 2, 0, 1),
            ),
        ])
        await session.commit()

        result = await todo_service.list_todos(
            session,
            current_user=await session.get(User, "date-user"),
            date_from="2026-05-01T00:00:00",
            date_to="2026-05-01T00:00:00",
            page_size=10,
        )

    assert [item["title"] for item in result["items"]] == ["范围内待办"]


@pytest.mark.asyncio
async def test_todo_detail_omits_raw_decision_json_unless_debug(client):
    from datetime import datetime

    from app.auth.models import User
    from app.database import async_session_factory
    from app.execution.models import DecisionLog, ExecutionRun
    from app.todos.models import AITodo, DecisionRequest
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        user = User(id="detail-user", username="detail-user", name="Detail User", role="operator", is_active=True)
        session.add(user)
        session.add(ExecutionRun(id="run-detail-cache", skill_id="skill-detail-cache", status="completed"))
        session.add(
            DecisionLog(
                run_id="run-detail-cache",
                skill_id="skill-detail-cache",
                input_snapshot={"raw": "input" * 1000},
                output_result={"raw": "output" * 1000},
                approval_level=1,
                is_sandbox=False,
            )
        )
        request = DecisionRequest(
            id="req-detail-cache",
            source_type="test",
            source_id="detail-cache",
            skill_id="skill-detail-cache",
            run_id="run-detail-cache",
            kind="review",
            title="详情缓存轻量化",
            summary="",
            payload={"output": {"summary": "需要确认"}},
            sla_at=datetime(2026, 5, 3),
        )
        session.add(request)
        await session.flush()
        todo = AITodo(request_id=request.id, kind="review", assignee=user.id, status="pending")
        session.add(todo)
        await session.commit()
        todo_id = todo.id

    async with async_session_factory() as session:
        user = await session.get(User, "detail-user")
        normal = await todo_service.get_todo_detail(session, todo_id=todo_id, current_user=user)
        debug = await todo_service.get_todo_detail(
            session,
            todo_id=todo_id,
            current_user=user,
            include_debug=True,
        )

    assert normal["decision_chain"]["log_id"]
    assert "input_snapshot" not in normal["decision_chain"]
    assert "output_result" not in normal["decision_chain"]
    assert debug["decision_chain"]["input_snapshot"]["raw"].startswith("input")
    assert debug["decision_chain"]["output_result"]["raw"].startswith("output")


@pytest.mark.asyncio
async def test_todo_detail_skips_large_decision_log_payload_on_normal_path(client, monkeypatch):
    from datetime import datetime

    from app.auth.models import User
    from app.database import async_session_factory
    from app.execution.models import DecisionLog, ExecutionRun
    from app.todos.models import AITodo, DecisionRequest
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        user = User(id="detail-large-user", username="detail-large-user", name="Large User", role="operator", is_active=True)
        session.add(user)
        session.add(ExecutionRun(id="run-detail-large", skill_id="skill-detail-large", status="completed"))
        session.add(
            DecisionLog(
                run_id="run-detail-large",
                skill_id="skill-detail-large",
                input_snapshot={"raw": "input"},
                output_result={
                    "reports": [{"title": "大日志报告"}],
                    "huge": "x" * 4096,
                },
                approval_level=2,
                is_sandbox=False,
            )
        )
        request = DecisionRequest(
            id="req-detail-large",
            source_type="test",
            source_id="detail-large",
            skill_id="skill-detail-large",
            run_id="run-detail-large",
            kind="review",
            title="大日志详情",
            summary="",
            payload={"item_id": "800001", "card_type": "product_decline_decision_card"},
            sla_at=datetime(2026, 5, 3),
        )
        session.add(request)
        await session.flush()
        todo = AITodo(request_id=request.id, kind="review", assignee=user.id, status="pending")
        session.add(todo)
        await session.commit()
        todo_id = todo.id

    monkeypatch.setattr(todo_service, "_should_load_detail_decision_log_for_compares", lambda *args, **kwargs: False)

    async with async_session_factory() as session:
        user = await session.get(User, "detail-large-user")
        normal = await todo_service.get_todo_detail(session, todo_id=todo_id, current_user=user)

    assert normal["decision_chain"]["approval_level"] == 2
    assert "input_snapshot" not in normal["decision_chain"]
    assert "output_result" not in normal["decision_chain"]
    assert "free_search_compare" not in normal["payload"]
    assert normal["related_report"]["title"] == "大日志报告"


@pytest.mark.asyncio
async def test_list_todos_filters_by_decision_log_id(client):
    from datetime import datetime

    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.models import AITodo, DecisionRequest
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        user = User(id="report-user", username="report-user", name="Report User", role="operator", is_active=True)
        session.add(user)
        requests = [
            DecisionRequest(
                id="req-report-a",
                source_type="test",
                source_id="report-a",
                skill_id="skill-report-filter",
                decision_log_id=81001,
                kind="review",
                title="报告关联待办",
                summary="",
                payload={},
                sla_at=datetime(2026, 5, 3),
            ),
            DecisionRequest(
                id="req-report-b",
                source_type="test",
                source_id="report-b",
                skill_id="skill-report-filter",
                decision_log_id=81002,
                kind="review",
                title="其他报告待办",
                summary="",
                payload={},
                sla_at=datetime(2026, 5, 3),
            ),
        ]
        session.add_all(requests)
        await session.flush()
        session.add_all([
            AITodo(request_id="req-report-a", kind="review", assignee=user.id, status="pending"),
            AITodo(request_id="req-report-b", kind="review", assignee=user.id, status="pending"),
        ])
        await session.commit()

        result = await todo_service.list_todos(
            session,
            current_user=user,
            status="pending",
            decision_log_id=81001,
            page_size=10,
        )

    assert result["total"] == 1
    assert [item["request_id"] for item in result["items"]] == ["req-report-a"]


@pytest.mark.asyncio
async def test_list_todos_filters_by_run_id(client):
    from datetime import datetime

    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.models import AITodo, DecisionRequest
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        session.add(User(id="run-filter-user", username="run-filter-user", name="Run User", role="admin", is_active=True))
        first = DecisionRequest(
            id="req-run-filter-1",
            source_type="test",
            source_id="run-filter-1",
            skill_id="skill-run-filter",
            run_id="run-filter-target",
            kind="review",
            title="目标 run 待办",
            summary="",
            payload={"priority": "P0"},
            sla_at=datetime(2026, 5, 3),
        )
        second = DecisionRequest(
            id="req-run-filter-2",
            source_type="test",
            source_id="run-filter-2",
            skill_id="skill-run-filter",
            run_id="run-filter-other",
            kind="review",
            title="其他 run 待办",
            summary="",
            payload={"priority": "P0"},
            sla_at=datetime(2026, 5, 3),
        )
        session.add_all([first, second])
        await session.flush()
        session.add_all([
            AITodo(request_id=first.id, kind="review", assignee="run-filter-user", status="pending"),
            AITodo(request_id=second.id, kind="review", assignee="run-filter-user", status="pending"),
        ])
        await session.commit()

        result = await todo_service.list_todos(
            session,
            current_user=await session.get(User, "run-filter-user"),
            run_id="run-filter-target",
            page_size=10,
        )

    assert [item["title"] for item in result["items"]] == ["目标 run 待办"]


@pytest.mark.asyncio
async def test_todo_stats_returns_today_priority_breakdown(client):
    from datetime import timedelta

    from app.auth.models import User
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.todos.models import AITodo, DecisionRequest
    from app.todos.service import todo_service

    now = now_bjt()
    yesterday = now - timedelta(days=1)
    async with async_session_factory() as session:
        user = User(
            id="stats-user",
            username="stats-user",
            name="Stats User",
            role="operator",
            department="AI小组",
            is_active=True,
        )
        session.add(user)
        requests = [
            DecisionRequest(
                id="req-stats-p1",
                source_type="test",
                source_id="stats-p1",
                skill_id="skill-stats-p1",
                kind="review",
                title="P1｜今日高优先级",
                payload={},
                sla_at=now + timedelta(days=1),
            ),
            DecisionRequest(
                id="req-stats-p2",
                source_type="test",
                source_id="stats-p2",
                skill_id="skill-stats-p2",
                kind="review",
                title="P2 今日中优先级",
                payload={},
                sla_at=now + timedelta(days=1),
            ),
            DecisionRequest(
                id="req-stats-p3",
                source_type="test",
                source_id="stats-p3",
                skill_id="skill-stats-p3",
                kind="review",
                title="今日低优先级",
                payload={"priority": "P3"},
                sla_at=now + timedelta(days=1),
            ),
            DecisionRequest(
                id="req-stats-old-p1",
                source_type="test",
                source_id="stats-old-p1",
                skill_id="skill-stats-old-p1",
                kind="review",
                title="P1｜昨日遗留",
                payload={},
                sla_at=now + timedelta(days=1),
            ),
            DecisionRequest(
                id="req-stats-archived",
                source_type="test",
                source_id="stats-archived",
                skill_id="skill-stats-archived",
                kind="review",
                title="P1｜已归档",
                payload={},
                sla_at=now + timedelta(days=1),
                archived_at=now,
            ),
            DecisionRequest(
                id="req-stats-peer",
                source_type="test",
                source_id="stats-peer",
                skill_id="skill-stats-peer",
                kind="review",
                title="同事已处理",
                payload={},
                sla_at=now + timedelta(days=1),
            ),
        ]
        session.add_all(requests)
        await session.flush()
        session.add_all([
            AITodo(request_id="req-stats-p1", kind="review", assignee=user.id, status="pending", created_at=now),
            AITodo(request_id="req-stats-p2", kind="review", assignee=user.id, status="pending", created_at=now),
            AITodo(request_id="req-stats-p3", kind="review", assignee=user.id, status="approved", created_at=now),
            AITodo(request_id="req-stats-old-p1", kind="review", assignee=user.id, status="pending", created_at=yesterday),
            AITodo(request_id="req-stats-archived", kind="review", assignee=user.id, status="pending", created_at=now),
            AITodo(request_id="req-stats-peer", kind="review", assignee=user.id, status="resolved_by_peer", created_at=yesterday),
        ])
        await session.commit()

        stats = await todo_service.get_stats(session, current_user=user)

    assert stats["pending"] == 3
    assert stats["approved"] == 1
    assert stats["resolved_by_peer"] == 1
    assert stats["done"] == 2
    assert stats["due_soon"] == 3
    assert stats["overdue"] == 0
    assert stats["today_new"] == 3
    assert stats["today_by_priority"] == {"P1": 1, "P2": 1, "P3": 1}
    assert stats["pending_by_priority"] == {"P1": 2, "P2": 1}


@pytest.mark.asyncio
async def test_list_todos_supports_sla_state_filters(client):
    from datetime import timedelta

    from app.auth.models import User
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.todos.models import AITodo, DecisionRequest
    from app.todos.service import todo_service

    now = now_bjt()
    async with async_session_factory() as session:
        user = User(
            id="sla-filter-user",
            username="sla-filter-user",
            name="SLA Filter User",
            role="operator",
            department="AI小组",
            is_active=True,
        )
        session.add(user)
        requests = [
            DecisionRequest(
                id="req-sla-overdue",
                source_type="test",
                source_id="sla-overdue",
                skill_id="skill-sla",
                kind="review",
                title="已逾期待办",
                payload={},
                sla_at=now - timedelta(hours=2),
            ),
            DecisionRequest(
                id="req-sla-soon",
                source_type="test",
                source_id="sla-soon",
                skill_id="skill-sla",
                kind="review",
                title="即将逾期待办",
                payload={},
                sla_at=now + timedelta(hours=2),
            ),
            DecisionRequest(
                id="req-sla-later",
                source_type="test",
                source_id="sla-later",
                skill_id="skill-sla",
                kind="review",
                title="稍后处理待办",
                payload={},
                sla_at=now + timedelta(days=3),
            ),
            DecisionRequest(
                id="req-sla-expired-status",
                source_type="test",
                source_id="sla-expired-status",
                skill_id="skill-sla",
                kind="review",
                title="已过期状态",
                payload={},
                sla_at=now - timedelta(hours=3),
            ),
        ]
        session.add_all(requests)
        await session.flush()
        session.add_all([
            AITodo(request_id="req-sla-overdue", kind="review", assignee=user.id, status="pending"),
            AITodo(request_id="req-sla-soon", kind="review", assignee=user.id, status="pending"),
            AITodo(request_id="req-sla-later", kind="review", assignee=user.id, status="pending"),
            AITodo(request_id="req-sla-expired-status", kind="review", assignee=user.id, status="expired"),
        ])
        await session.commit()

        due_soon = await todo_service.list_todos(
            session,
            current_user=user,
            sla_state="due_soon",
            sort_by="sla_at",
            sort_order="asc",
            page_size=10,
        )
        due_24h = await todo_service.list_todos(
            session,
            current_user=user,
            sla_state="due_24h",
            sort_by="sla_at",
            sort_order="asc",
            page_size=10,
        )
        overdue = await todo_service.list_todos(
            session,
            current_user=user,
            sla_state="overdue",
            page_size=10,
        )

    assert [item["request_id"] for item in due_soon["items"]] == ["req-sla-overdue", "req-sla-soon"]
    assert [item["request_id"] for item in due_24h["items"]] == ["req-sla-soon"]
    assert [item["request_id"] for item in overdue["items"]] == ["req-sla-overdue"]


@pytest.mark.asyncio
async def test_list_todos_supports_done_alias_and_payload_priority(client):
    from datetime import datetime

    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.models import AITodo, DecisionRequest
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        user = User(
            id="filter-user",
            username="filter-user",
            name="Filter User",
            role="operator",
            department="AI小组",
            is_active=True,
        )
        other = User(
            id="filter-other",
            username="filter-other",
            name="Filter Other",
            role="operator",
            department="AI小组",
            is_active=True,
        )
        session.add_all([user, other])
        requests = [
            DecisionRequest(
                id="req-filter-payload-p1",
                source_type="test",
                source_id="filter-payload-p1",
                skill_id="skill-filter",
                kind="review",
                title="payload 优先级",
                payload={"priority": "P1"},
                sla_at=datetime(2026, 5, 3),
            ),
            DecisionRequest(
                id="req-filter-approved",
                source_type="test",
                source_id="filter-approved",
                skill_id="skill-filter",
                kind="review",
                title="已通过",
                payload={},
                sla_at=datetime(2026, 5, 3),
            ),
            DecisionRequest(
                id="req-filter-rejected",
                source_type="test",
                source_id="filter-rejected",
                skill_id="skill-filter",
                kind="review",
                title="已驳回",
                payload={},
                sla_at=datetime(2026, 5, 3),
            ),
            DecisionRequest(
                id="req-filter-other",
                source_type="test",
                source_id="filter-other",
                skill_id="skill-filter",
                kind="review",
                title="他人待办",
                payload={},
                sla_at=datetime(2026, 5, 3),
            ),
        ]
        session.add_all(requests)
        await session.flush()
        session.add_all([
            AITodo(request_id="req-filter-payload-p1", kind="review", assignee=user.id, status="pending"),
            AITodo(request_id="req-filter-approved", kind="review", assignee=user.id, status="approved"),
            AITodo(request_id="req-filter-rejected", kind="review", assignee=user.id, status="rejected"),
            AITodo(request_id="req-filter-other", kind="review", assignee=other.id, status="pending"),
        ])
        await session.commit()

        p1_result = await todo_service.list_todos(
            session,
            current_user=user,
            status="pending",
            priority="P1",
            assignee="me",
        )
        done_result = await todo_service.list_todos(
            session,
            current_user=user,
            status="done",
            page_size=10,
        )

    assert [item["request_id"] for item in p1_result["items"]] == ["req-filter-payload-p1"]
    assert p1_result["items"][0]["priority"] == "P1"
    assert {item["request_id"] for item in done_result["items"]} == {
        "req-filter-approved",
        "req-filter-rejected",
    }


@pytest.mark.asyncio
async def test_create_and_decide_any_of_todos(client):
    from sqlalchemy import select

    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.models import AITodo, DecisionRequest
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        session.add_all([
            User(id='alice', username='alice', name='Alice', role='operator', is_active=True),
            User(id='bob', username='bob', name='Bob', role='operator', is_active=True),
        ])
        await session.commit()

    async with async_session_factory() as session:
        requests = await todo_service.create_from_execution(
            session,
            run_id='run-todo-001',
            skill_id='skill-001',
            skill_meta={'name': '测试 Skill', 'approval_level': 1, 'reviewer': ['alice', 'bob']},
            decision={'input_snapshot': {}, 'output_result': {'summary': '需要人工确认'}, 'suggested_action': None},
        )
        await session.commit()
        assert requests
        request = requests[0]

    async with async_session_factory() as session:
        todos = (
            await session.execute(
                select(AITodo)
                .where(AITodo.request_id == request.id)
                .order_by(AITodo.id.asc())
            )
        ).scalars().all()
        assert len(todos) == 2
        first, second = todos

        result = await todo_service.decide(
            session,
            todo_id=first.id,
            decision='approved',
            decided_by='alice',
            channel='web',
        )
        await session.commit()
        assert result['todo']['status'] == 'approved'

    async with async_session_factory() as session:
        refreshed = (
            await session.execute(
                select(AITodo)
                .where(AITodo.request_id == request.id)
                .order_by(AITodo.id.asc())
            )
        ).scalars().all()
        req = await session.get(DecisionRequest, request.id)

        assert refreshed[0].status == 'approved'
        assert refreshed[1].status == 'resolved_by_peer'
        assert req.aggregate_status == 'completed'
        assert req.aggregate_decision == 'approved'


@pytest.mark.asyncio
async def test_samplebrand_low_consumption_todo_backfills_lifecycle_gap(client):
    from sqlalchemy import select

    from app.auth.models import User
    from app.database import async_session_factory
    from app.execution.models import DecisionLog, ExecutionRun
    from app.todos.models import DecisionRequest
    from app.todos.service import SAMPLEBRAND_LOW_CONSUMPTION_SKILL_ID, todo_service

    async with async_session_factory() as session:
        session.add(User(id="samplebrand-reviewer", username="samplebrand-reviewer", name="SampleBrand Reviewer", role="operator", is_active=True))
        session.add(ExecutionRun(id="run-samplebrand-lifecycle-gap", skill_id=SAMPLEBRAND_LOW_CONSUMPTION_SKILL_ID, status="completed"))
        session.add(
            DecisionLog(
                run_id="run-samplebrand-lifecycle-gap",
                skill_id=SAMPLEBRAND_LOW_CONSUMPTION_SKILL_ID,
                output_result={},
                approval_level=1,
                is_sandbox=False,
            )
        )
        await session.flush()
        decision_log_id = (
            await session.execute(
                select(DecisionLog.id).where(DecisionLog.run_id == "run-samplebrand-lifecycle-gap")
            )
        ).scalar_one()
        await session.commit()

    payload = {
        "card_type": "video_low_consumption_decision_card",
        "analysis_schema": "samplebrand_video_low_consumption_daily_operator_v1",
        "account_name": "彭嘉欣",
        "operation_actions": [
            {
                "video_id": "101571379",
                "benchmark_video_id": "96289777",
                "benchmark_video_title": "高消耗参考",
                "benchmark_qianchuan_lifecycle": {"available": False},
                "root_causes": ["对标选择：相似主题高消耗参考。"],
                "data_checks": [],
            }
        ],
        "chart_sections": [
            {
                "key": "video_metric_compare_1",
                "type": "bar_compare",
                "title": "低消耗 vs 高消耗指标",
                "items": [{"key": "cost", "label": "消耗", "low": 100, "benchmark": 1000}],
            }
        ],
    }

    async with async_session_factory() as session:
        requests = await todo_service.create_from_execution(
            session,
            run_id="run-samplebrand-lifecycle-gap",
            skill_id=SAMPLEBRAND_LOW_CONSUMPTION_SKILL_ID,
            skill_meta={"name": "示例品牌低消耗", "approval_level": 1},
            decision={
                "decision_log_id": decision_log_id,
                "input_snapshot": {},
                "output_result": {
                    "todos": [
                        {
                            "kind": "review",
                            "title": "P1｜云视频账号 彭嘉欣 低消耗视频诊断改进",
                            "summary": "生命周期缺口应明确展示",
                            "payload": payload,
                            "reviewers": ["samplebrand-reviewer"],
                        }
                    ]
                },
            },
        )
        await session.commit()
        assert requests

    async with async_session_factory() as session:
        request = (
            await session.execute(
                select(DecisionRequest).where(DecisionRequest.run_id == "run-samplebrand-lifecycle-gap")
            )
        ).scalar_one()

    saved = request.payload
    operation = saved["operation_actions"][0]
    assert operation["benchmark_qianchuan_lifecycle"]["status"] == "unavailable"
    assert "不能把“第几秒点击峰值”写成确定结论" in operation["qianchuan_lifecycle_evidence"]
    assert "消费者秒级触发点当前不能被千川曲线直接证明" in operation["qianchuan_lifecycle_value_points"]["consumer_value"]
    assert any(item["key"] == "qianchuan_lifecycle_status" for item in saved["chart_sections"])
    assert any(item["dimension"] == "千川点击生命周期" for item in saved["data_quality_items"])
    assert any("不要把秒级点击峰值" in item for item in saved["forbidden_actions"])


@pytest.mark.asyncio
async def test_todo_decide_conflict_returns_already_decided(client):
    from app.auth.models import User
    from app.common.exceptions import AppError
    from app.database import async_session_factory
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        session.add(User(id='owner1', username='owner1', name='Owner', role='operator', is_active=True))
        requests = await todo_service.create_from_execution(
            session,
            run_id='run-todo-002',
            skill_id='skill-002',
            skill_meta={'name': '测试 Skill 2', 'approval_level': 1, 'reviewer': ['owner1']},
            decision={'input_snapshot': {}, 'output_result': {'summary': '待确认'}, 'suggested_action': None},
        )
        await session.commit()
        assert requests
        request = requests[0]

    async with async_session_factory() as session:
        todo_id = (await todo_service.list_todos(session, current_user=await session.get(User, 'owner1')))['items'][0]['id']
        await todo_service.decide(session, todo_id=todo_id, decision='approved', decided_by='owner1')
        await session.commit()

    async with async_session_factory() as session:
        with pytest.raises(AppError) as exc:
            await todo_service.decide(session, todo_id=todo_id, decision='rejected', decided_by='owner1')
        assert exc.value.code == 'TODO_ALREADY_DECIDED'


@pytest.mark.asyncio
async def test_legacy_todo_backfill_decision_log_id(client):
    """spec §5.3 要求 `_create_from_legacy()` 写入 fallback_decision["decision_log_id"],
    保证老 Skill 的待办能反向关联报告。

    场景:
      1. fallback_decision 含 decision_log_id=<int> → 新建 DecisionRequest.decision_log_id 写入
      2. fallback_decision 缺 decision_log_id → decision_log_id IS NULL 不崩
      3. GET /api/todos/{id} 当 decision_log 带 reports 时, related_report 非 null
    """
    from sqlalchemy import select

    from app.auth.models import User
    from app.database import async_session_factory
    from app.execution.models import DecisionLog
    from app.todos.models import AITodo, DecisionRequest
    from app.todos.service import todo_service

    # case 1: 带 decision_log_id — 命中报告
    async with async_session_factory() as session:
        session.add(User(id='legacy-rev-1', username='legacy-rev-1', name='R1', role='operator', is_active=True))
        await session.flush()
        log = DecisionLog(
            run_id='run-legacy-backfill-1',
            skill_id='skill-legacy-backfill-1',
            input_snapshot={'date': '2026-04-17'},
            output_result={
                'summary': '待审批',
                'reports': [
                    {
                        'title': '回填日报',
                        'summary': '需要审批',
                        'content_markdown': '# 回填日报',
                    }
                ],
            },
            approval_level=1,
            is_sandbox=False,
        )
        session.add(log)
        await session.flush()
        log_id = log.id

        # 直接调用 _create_from_legacy 验证内部写入语义
        req = await todo_service._create_from_legacy(
            session,
            run_id='run-legacy-backfill-1',
            skill_id='skill-legacy-backfill-1',
            skill_meta={'name': 'Legacy回填', 'approval_level': 1, 'reviewer': ['legacy-rev-1']},
            decision={
                'input_snapshot': {'date': '2026-04-17'},
                'output_result': log.output_result,
                'suggested_action': None,
                'decision_log_id': log_id,
            },
        )
        await session.commit()
        assert req is not None
        assert req.decision_log_id == log_id
        req_id = req.id

    async with async_session_factory() as session:
        persisted = await session.get(DecisionRequest, req_id)
        assert persisted is not None
        assert persisted.decision_log_id == log_id

        todo = (
            await session.execute(
                select(AITodo).where(AITodo.request_id == req_id).order_by(AITodo.id.asc())
            )
        ).scalar_one()
        reviewer = await session.get(User, 'legacy-rev-1')
        detail = await todo_service.get_todo_detail(
            session, todo_id=todo.id, current_user=reviewer
        )
        assert detail['related_report'] is not None
        assert detail['related_report']['decision_log_id'] == log_id
        assert detail['related_report']['title'] == '回填日报'

    # case 2: fallback_decision 无 decision_log_id — 不崩、字段允许 NULL
    async with async_session_factory() as session:
        session.add(User(id='legacy-rev-2', username='legacy-rev-2', name='R2', role='operator', is_active=True))
        await session.flush()

        req_no_log = await todo_service._create_from_legacy(
            session,
            run_id='run-legacy-backfill-2',
            skill_id='skill-legacy-backfill-2',
            skill_meta={'name': 'Legacy无log', 'approval_level': 1, 'reviewer': ['legacy-rev-2']},
            decision={
                'input_snapshot': {},
                'output_result': {'summary': '无 log 场景'},
                'suggested_action': None,
                # 故意不传 decision_log_id
            },
        )
        await session.commit()
        assert req_no_log is not None
        assert req_no_log.decision_log_id is None
        req_no_log_id = req_no_log.id

    async with async_session_factory() as session:
        persisted = await session.get(DecisionRequest, req_no_log_id)
        assert persisted is not None
        assert persisted.decision_log_id is None

        todo = (
            await session.execute(
                select(AITodo).where(AITodo.request_id == req_no_log_id).order_by(AITodo.id.asc())
            )
        ).scalar_one()
        reviewer = await session.get(User, 'legacy-rev-2')
        detail = await todo_service.get_todo_detail(
            session, todo_id=todo.id, current_user=reviewer
        )
        # 无 decision_log_id → related_report 返回 None, 不抛错
        assert detail['related_report'] is None


@pytest.mark.asyncio
async def test_expire_due_todos_invalidates_inbox_cache(client, monkeypatch):
    """spec §5.3 & plan §7: aggregate_status 从 pending 变 expired 会影响
    inbox 报告的 related_pending_request_count, 所以 expire_due_todos 必须
    主动清 inbox 缓存, 不能只靠 60s TTL 兜底。
    """
    from datetime import datetime, timedelta

    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.models import AITodo, DecisionRequest
    from app.todos.service import todo_service

    calls: list[bool] = []

    async def fake_invalidate_inbox() -> None:
        calls.append(True)

    # raising=False: 如果本地 cache.py 还没把 invalidate_inbox 加进去(比如
    # 跟 plan §5.5 的 cache.py 改动还没合入),monkeypatch 仍然能把属性
    # 注入进模块对象上,保证 `from app.common.cache import invalidate_inbox`
    # 在 expire_due_todos 内部 import 时能成功解析到 fake。
    monkeypatch.setattr(
        "app.common.cache.invalidate_inbox",
        fake_invalidate_inbox,
        raising=False,
    )

    async with async_session_factory() as session:
        session.add(User(id='expire-rev-1', username='expire-rev-1', name='R', role='operator', is_active=True))
        await session.flush()

        past_sla = datetime.utcnow() - timedelta(hours=1)
        request = DecisionRequest(
            id='dr-expire-invalidate-1',
            kind='review',
            source_type='skill_execution_l1',
            source_id='run-expire-invalidate-1',
            skill_id='skill-expire-invalidate-1',
            run_id='run-expire-invalidate-1',
            title='过期待办',
            summary='SLA 已过',
            decision_mode='any_of',
            aggregate_status='pending',
            sla_at=past_sla,
        )
        session.add(request)
        await session.flush()
        todo = AITodo(request_id=request.id, kind='review', assignee='expire-rev-1')
        session.add(todo)
        await session.commit()

    async with async_session_factory() as session:
        expired = await todo_service.expire_due_todos(session)
        await session.commit()
        assert len(expired) == 1
        assert expired[0]['request_id'] == 'dr-expire-invalidate-1'

    # invalidate_inbox 必须被调用 1 次(有 aggregate_status 变更)
    assert calls == [True]

    # 没有过期待办时不应再触发清缓存
    calls.clear()
    async with async_session_factory() as session:
        again = await todo_service.expire_due_todos(session)
        await session.commit()
        assert again == []
    assert calls == []


@pytest.mark.asyncio
async def test_invalid_reviewer_config_raises(client):
    from app.auth.models import User
    from app.common.exceptions import AppError
    from app.database import async_session_factory
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        session.add(User(id='valid-user', username='valid-user', name='Valid', role='operator', is_active=True))
        await session.commit()
        with pytest.raises(AppError) as exc:
            await todo_service.create_from_execution(
                session,
                run_id='run-invalid-reviewer',
                skill_id='skill-invalid-reviewer',
                skill_meta={'name': '非法 reviewer', 'approval_level': 1, 'reviewer': ['missing-user']},
                decision={'input_snapshot': {}, 'output_result': {'summary': '待确认'}, 'suggested_action': None},
            )
        assert exc.value.code == 'INVALID_REVIEWER_CONFIG'
