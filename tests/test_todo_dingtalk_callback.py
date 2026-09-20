"""钉钉互动卡片回调单测：actor=assignee 校验、重放、签名。"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_card_callback_rejects_actor_not_assignee(client, monkeypatch):
    """卡片绑定 alice，但 bob 点击 → 拒绝。"""
    from app.auth.models import User
    from app.config import settings
    from app.database import async_session_factory
    from app.todos.service import todo_service

    # 关闭 dingtalk 验签，方便测试聚焦 actor 校验
    monkeypatch.setattr(settings, "DINGTALK_CALLBACK_TOKEN", "")

    async with async_session_factory() as session:
        session.add_all(
            [
                User(
                    id="alice", username="alice", name="Alice",
                    role="operator", is_active=True, dingtalk_user_id="ding-alice",
                ),
                User(
                    id="bob", username="bob", name="Bob",
                    role="operator", is_active=True, dingtalk_user_id="ding-bob",
                ),
            ]
        )
        await session.commit()
        await todo_service.create_from_execution(
            session,
            run_id="run-cb-1",
            skill_id="skill-cb-1",
            skill_meta={"name": "回调测试", "approval_level": 1, "reviewer": ["alice"]},
            decision={"input_snapshot": {}, "output_result": {"summary": "需要审批"}, "suggested_action": None},
        )
        await session.commit()
        # 拿到 todo_id
        rows = (
            await session.execute(
                __import__("sqlalchemy").text("SELECT id FROM ai_todos WHERE assignee='alice' ORDER BY id DESC LIMIT 1")
            )
        ).first()
        todo_id = rows[0]

    # bob 点击属于 alice 的卡片
    body = {
        "actionId": "approve",
        "params": {"action": "approve"},
        "cardPrivateData": {
            "todo_id": str(todo_id),
            "request_id": "ignored",
            "assignee_user_id": "alice",
            "nonce": "n1",
        },
        "userId": "ding-bob",
    }
    resp = await client.post("/api/dingtalk/card-callback", json=body)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_card_callback_admin_can_override(client, monkeypatch):
    """admin 可以代审非自己的待办（actor=admin 例外）。"""
    from app.auth.models import User
    from app.config import settings
    from app.database import async_session_factory
    from app.todos.service import todo_service

    monkeypatch.setattr(settings, "DINGTALK_CALLBACK_TOKEN", "")

    async with async_session_factory() as session:
        session.add_all(
            [
                User(
                    id="op1", username="op1", name="Op1",
                    role="operator", is_active=True, dingtalk_user_id="ding-op1",
                ),
                User(
                    id="admin1", username="admin1", name="Admin1",
                    role="admin", is_active=True, dingtalk_user_id="ding-admin1",
                ),
            ]
        )
        await session.commit()
        await todo_service.create_from_execution(
            session,
            run_id="run-cb-2",
            skill_id="skill-cb-2",
            skill_meta={"name": "admin 代审", "approval_level": 1, "reviewer": ["op1"]},
            decision={"input_snapshot": {}, "output_result": {"summary": "x"}, "suggested_action": None},
        )
        await session.commit()
        rows = (
            await session.execute(
                __import__("sqlalchemy").text("SELECT id FROM ai_todos WHERE assignee='op1' ORDER BY id DESC LIMIT 1")
            )
        ).first()
        todo_id = rows[0]

    body = {
        "actionId": "approve",
        "params": {"action": "approve"},
        "cardPrivateData": {
            "todo_id": str(todo_id),
            "request_id": "ignored",
            "assignee_user_id": "op1",
            "nonce": "n2",
        },
        "userId": "ding-admin1",
    }
    resp = await client.post("/api/dingtalk/card-callback", json=body)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["todo"]["status"] == "approved"


@pytest.mark.asyncio
async def test_card_callback_missing_assignee_returns_400(client, monkeypatch):
    """卡片 private_data 没带 assignee_user_id 直接拒绝。"""
    from app.config import settings

    monkeypatch.setattr(settings, "DINGTALK_CALLBACK_TOKEN", "")

    body = {
        "actionId": "approve",
        "params": {"action": "approve"},
        "cardPrivateData": {"todo_id": "1"},  # 缺 assignee_user_id
        "userId": "ding-x",
    }
    resp = await client.post("/api/dingtalk/card-callback", json=body)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_card_callback_feedback_updates_decision_log_and_learning_loop(client, monkeypatch):
    from sqlalchemy import select

    from app.auth.models import User
    from app.config import settings
    from app.database import async_session_factory
    from app.execution.models import DecisionLog, ExecutionRun
    from app.learning.models import LearningArtifact, LearningEvent, LearningFlowEdge
    from app.skills.core.models import Skill
    from app.todos.models import AITodo, DecisionRequest

    monkeypatch.setattr(settings, "DINGTALK_CALLBACK_TOKEN", "")

    async with async_session_factory() as session:
        session.add(User(
            id="ding-feedback-op",
            username="ding-feedback-op",
            name="反馈运营",
            role="operator",
            department="AI小组",
            is_active=True,
            dingtalk_user_id="ding-feedback-op",
        ))
        session.add(Skill(
            id="dingtalk-feedback-skill",
            name="钉钉反馈 Skill",
            display_name="钉钉反馈 Skill",
            department="AI小组",
            status="active",
            visibility="department",
            approval_level=1,
        ))
        session.add(ExecutionRun(
            id="run-dingtalk-feedback-1",
            skill_id="dingtalk-feedback-skill",
            trigger_type="manual",
            run_mode="manual_real",
            status="completed",
            metadata_json={"model_context": {"model_deployment_id": "deploy-ding-feedback"}},
            summary="钉钉反馈来源运行。",
        ))
        decision = DecisionLog(
            run_id="run-dingtalk-feedback-1",
            skill_id="dingtalk-feedback-skill",
            input_snapshot={"item_id": "item-ding-feedback"},
            output_result={
                "summary": "建议调整投放承接。",
                "todos": [{"title": "补查预算"}],
                "_skillforge_meta": {
                    "model_context": {
                        "model_deployment_id": "deploy-ding-feedback",
                        "model_family": "dingtalk-feedback-skill:qwen3.5-4b-qlora",
                        "deployment_status": "active",
                        "artifact_id": "artifact-ding-feedback",
                        "artifact_sha256": "c" * 64,
                    }
                },
            },
            approval_level=1,
            approval_status="pending",
            is_sandbox=False,
        )
        session.add(decision)
        await session.flush()
        request = DecisionRequest(
            id="dr-dingtalk-feedback-1",
            source_type="execution",
            source_id="run-dingtalk-feedback-1",
            skill_id="dingtalk-feedback-skill",
            run_id="run-dingtalk-feedback-1",
            decision_log_id=decision.id,
            kind="review",
            title="请确认投放承接建议",
            summary="需要审批运营动作。",
            payload={"output": {"summary": "建议调整投放承接。"}},
            decision_mode="any_of",
            sla_at=datetime.now(),
        )
        session.add(request)
        await session.flush()
        todo = AITodo(request_id=request.id, kind="review", assignee="ding-feedback-op")
        session.add(todo)
        await session.commit()
        todo_id = todo.id
        decision_id = decision.id

    body = {
        "actionId": "reject",
        "params": {
            "action": "reject",
            "rating": 2,
            "feedback_type": "证据不足",
            "reject_reason": "缺少预算承接",
            "feedback": "预算、出价、人群包没有讲清楚。",
        },
        "cardPrivateData": {
            "todo_id": str(todo_id),
            "request_id": "dr-dingtalk-feedback-1",
            "assignee_user_id": "ding-feedback-op",
            "nonce": "feedback-1",
        },
        "userId": "ding-feedback-op",
    }
    resp = await client.post("/api/dingtalk/card-callback", json=body)
    assert resp.status_code == 200, resp.text

    async with async_session_factory() as session:
        todo = await session.get(AITodo, todo_id)
        decision = await session.get(DecisionLog, decision_id)
        assert todo.status == "rejected"
        assert todo.decision_channel == "dingtalk"
        assert "驳回原因: 缺少预算承接" in (todo.decision_reason or "")
        assert todo.feedback_payload["source_channel"] == "dingtalk"
        assert todo.feedback_payload["fields"]["source"] == "dingtalk_interactive_card"
        assert todo.feedback_payload["fields"]["feedback_type"] == "证据不足"
        assert todo.feedback_payload["fields"]["reject_reason"] == "缺少预算承接"
        assert todo.feedback_payload["fields"]["feedback"] == "预算、出价、人群包没有讲清楚。"
        assert todo.feedback_payload["fields"]["rating"] == 2
        assert decision.user_action == "rejected"
        assert decision.approver == "ding-feedback-op"
        assert decision.rating == 2
        assert decision.feedback_type == "证据不足"
        assert decision.reject_reason == "缺少预算承接"
        assert "钉钉审批反馈" in (decision.user_feedback or "")
        assert "预算、出价、人群包没有讲清楚。" in (decision.user_feedback or "")

        decision_event = (
            await session.execute(
                select(LearningEvent)
                .where(LearningEvent.source_type == "decision_log")
                .where(LearningEvent.source_id == str(decision_id))
            )
        ).scalar_one()
        assert decision_event.event_type == "decision.feedback"
        todo_event = (
            await session.execute(
                select(LearningEvent)
                .where(LearningEvent.source_type == "ai_todo")
                .where(LearningEvent.source_id == str(todo_id))
            )
        ).scalar_one()
        assert todo_event.event_type == "decision.feedback"
        artifact_count = (
            await session.execute(
                select(LearningArtifact)
                .where(LearningArtifact.event_id.in_([decision_event.id, todo_event.id]))
                .where(LearningArtifact.artifact_kind == "training_sample")
            )
        ).scalars().all()
        assert artifact_count
        todo_training_sample = next(
            item
            for item in artifact_count
            if (item.content_json or {}).get("source_type") == "todo_feedback"
        )
        assert todo_training_sample.content_json["source_channel"] == "dingtalk"
        assert todo_training_sample.content_json["feedback"]["source_channel"] == "dingtalk"
        assert todo_training_sample.content_json["feedback"]["structured"]["fields"]["source"] == "dingtalk_interactive_card"
        assert todo_training_sample.content_json["human_feedback_input"]["fields"]["feedback_type"] == "证据不足"
        assert todo_training_sample.content_json["human_feedback_input"]["fields"]["reject_reason"] == "缺少预算承接"
        assert todo_training_sample.content_json["input"] == {"item_id": "item-ding-feedback"}
        assert todo_training_sample.content_json["output"]["summary"] == "建议调整投放承接。"
        assert todo_training_sample.content_json["decision"]["decision_log_id"] == decision_id
        assert todo_training_sample.content_json["decision"]["rating"] == 2
        assert todo_training_sample.content_json["model_context"]["model_deployment_id"] == "deploy-ding-feedback"
        assert todo_training_sample.content_json["model_context"]["artifact_sha256"] == "c" * 64
        assert "channel:dingtalk" in (todo_training_sample.labels_json or [])
        assert todo_training_sample.status == "materialized"
        assert todo_training_sample.sink_type == "training_sample"
        feedback_edges = (
            await session.execute(
                select(LearningFlowEdge)
                .where(
                    LearningFlowEdge.relation.in_(
                        [
                            "feedback_for_decision",
                            "feedback_on_model_decision",
                            "created_sample",
                            "submitted_feedback",
                        ]
                    )
                )
                .where(LearningFlowEdge.run_id == "run-dingtalk-feedback-1")
            )
        ).scalars().all()
        edge_index = {
            (edge.from_type, edge.from_id, edge.to_type, edge.to_id, edge.relation): edge
            for edge in feedback_edges
        }
        decision_feedback_edge = edge_index[
            ("todo", str(todo_id), "decision_log", str(decision_id), "feedback_for_decision")
        ]
        model_feedback_edge = edge_index[
            ("model_deployment", "deploy-ding-feedback", "todo", str(todo_id), "feedback_on_model_decision")
        ]
        dingtalk_feedback_edge = next(
            edge
            for edge in feedback_edges
            if edge.from_type == "dingtalk_feedback"
            and edge.to_type == "todo"
            and edge.to_id == str(todo_id)
            and edge.relation == "submitted_feedback"
        )
        sample_edge = edge_index[
            (
                "learning_artifact",
                todo_training_sample.id,
                "training_sample",
                todo_training_sample.id,
                "created_sample",
            )
        ]
        assert decision_feedback_edge.metadata_json["source_channel"] == "dingtalk"
        assert decision_feedback_edge.metadata_json["rating"] == 2
        assert model_feedback_edge.metadata_json["decision_log_id"] == decision_id
        assert model_feedback_edge.metadata_json["artifact_sha256"] == "c" * 64
        assert dingtalk_feedback_edge.metadata_json["source_channel"] == "dingtalk"
        assert dingtalk_feedback_edge.metadata_json["source"] == "dingtalk_interactive_card"
        assert dingtalk_feedback_edge.metadata_json["feedback_fields"] == [
            "action",
            "dingtalk_user_id",
            "feedback",
            "feedback_type",
            "rating",
            "reject_reason",
            "request_id",
            "source",
            "todo_id",
        ]
        assert dingtalk_feedback_edge.metadata_json["feedback_sha256"]
        assert dingtalk_feedback_edge.metadata_json["raw_text_returned"] is False
        assert "预算、出价、人群包没有讲清楚" not in json.dumps(
            dingtalk_feedback_edge.metadata_json,
            ensure_ascii=False,
        )
        assert sample_edge.skill_id == "dingtalk-feedback-skill"

    manifest_resp = await client.get(
        "/api/learning/training-manifest",
        params={"skill_id": "dingtalk-feedback-skill"},
    )
    assert manifest_resp.status_code == 200, manifest_resp.text
    manifest_sample = next(
        item
        for item in manifest_resp.json()["samples"]
        if item["artifact_id"] == todo_training_sample.id
    )
    assert manifest_sample["source_type"] == "todo_feedback"
    assert manifest_sample["source_channel"] == "dingtalk"
    lineage = manifest_sample["lineage_summary"]
    assert lineage["has_input"] is True
    assert lineage["has_output"] is True
    assert lineage["has_human_feedback"] is True
    assert lineage["feedback_source"] == "dingtalk"
    assert set(lineage["feedback_fields"]) >= {"feedback", "feedback_type", "rating", "reject_reason", "source"}
    assert lineage["feedback_type"] == "证据不足"
    assert lineage["rating"] == 2
    assert lineage["decision_log_id"] == decision_id
    assert lineage["todo_id"] == todo_id
    assert lineage["model_deployment_id"] == "deploy-ding-feedback"
    assert lineage["artifact_sha256"] == "c" * 64
    assert "预算、出价、人群包没有讲清楚" not in json.dumps(manifest_sample, ensure_ascii=False)

    journeys_resp = await client.get(
        "/api/learning/flow-journeys",
        params={"days": 7, "skill_id": "dingtalk-feedback-skill"},
    )
    assert journeys_resp.status_code == 200, journeys_resp.text
    journeys_payload = journeys_resp.json()
    todo_journey = next(
        item
        for item in journeys_payload["items"]
        if item["source_type"] == "ai_todo" and item["source_id"] == str(todo_id)
    )
    feedback_step = next(step for step in todo_journey["steps"] if step["action"] == "submitted_feedback")
    assert feedback_step["stage"] == "feedback"
    assert feedback_step["payload"]["source_channel"] == "dingtalk"
    assert feedback_step["payload"]["feedback_sha256"]
    assert feedback_step["payload"]["raw_text_returned"] is False
    assert "预算、出价、人群包没有讲清楚" not in json.dumps(feedback_step, ensure_ascii=False)


def test_dispatch_executor_card_uses_readable_markdown_and_bound_detail_link():
    from app.dingtalk.card_templates import build_dispatch_executor_card

    request = SimpleNamespace(title="商品诊断卡")
    task = SimpleNamespace(
        id=3098,
        deadline=datetime(2026, 4, 24, 23, 59),
        content=(
            "全店主线：数据不足为主；决策：建议通过；关键证据：下滑系数 0.1266；"
            "主因：商品基础/价格（活动待核对；价格力待核对）；免费访客环比 +17.07%；"
            "免费转化环比 -14.58%；运营动作：确认活动是否掉线；禁止动作：不要直接删词；验证指标：次日回升"
        ),
    )
    user = SimpleNamespace(id="exec-open", name="运营A", dingtalk_user_id="ding-open")

    card = build_dispatch_executor_card(request, task, user)

    assert "- 全店主线：数据不足为主" in card["markdown"]
    assert "- 决策：建议通过" in card["markdown"]
    assert "- 免费访客环比 +17.07%" in card["markdown"]
    assert "- 免费转化环比 -14.58%" in card["markdown"]
    assert "- 运营动作：确认活动是否掉线" in card["markdown"]
    assert card["buttons"][1]["action_url"].startswith("http")
    assert "/api/dingtalk/open/dispatch/3098/ack?token=" in card["buttons"][0]["action_url"]
    assert "/api/dingtalk/open/dispatch/3098?token=" in card["buttons"][1]["action_url"]


def test_dispatch_execution_content_preserves_original_content_and_compacts_top300():
    from app.dingtalk.card_templates import build_dispatch_execution_content

    request = SimpleNamespace(
        payload={
            "key_metrics": [
                {
                    "name": "市场Top300状态",
                    "value": "榜内下滑",
                    "status": "榜内下滑",
                    "delta": "当前第 45，昨日第 31；采集 300 / 300",
                },
                {"name": "全店访客排名", "value": "第33名"},
                {"name": "全店成交排名", "value": "第29名"},
                {"name": "—变化率—", "note": "支付-16.7%｜件数-10.0%"},
            ],
            "operation_actions": [
                {
                    "dimension": "免费流",
                    "priority": "高",
                    "human_action": "复核搜索词承接和主图点击",
                    "evidence": "搜索/免费转化下滑",
                },
            ],
        },
    )
    task = SimpleNamespace(
        content=(
            "全店主线：数据不足为主，转化问题为辅；"
            "Top300：榜内下滑（当前 未进 / 昨日 未进；采集 300 / 300）；"
            "决策：建议通过；"
            "关键证据：下滑系数 -1.1036；"
            "主因：免费流；"
            "运营动作：确认活动是否掉线；"
            "禁止动作：不要直接删词"
        ),
    )

    content = build_dispatch_execution_content(request, task)

    assert "全店主线：数据不足为主，转化问题为辅" in content
    assert "决策：建议通过" in content
    assert "关键证据：下滑系数 -1.1036" in content
    assert "主因：免费流" in content
    assert "Top300：榜内下滑（当前第 45，昨日第 31）" in content
    assert "当前 未进 / 昨日 未进；采集 300 / 300" not in content
    assert "需要执行：免费流 / 高：复核搜索词承接和主图点击（搜索/免费转化下滑）" in content
    assert "数据支撑：支付金额变化率 -16.7%；全店访客排名 第33名；全店成交排名 第29名（实时支付金额）" in content


@pytest.mark.asyncio
async def test_dingtalk_open_dispatch_detail_is_public_only_inside_dingtalk(client):
    from sqlalchemy import select

    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk.open_links import create_dispatch_task_view_token
    from app.learning.models import LearningArtifact, LearningEvent, LearningFlowEdge
    from app.skills.core.models import Skill
    from app.todos.models import DecisionRequest, TodoDispatchTask

    async with async_session_factory() as session:
        session.add(User(
            id="exec-open", username="exec-open", name="运营A",
            role="operator", is_active=True, dingtalk_user_id="ding-open",
        ))
        session.add(
            Skill(
                id="skill-open",
                name="开放钉钉派发 Skill",
                department="EC",
                status="active",
                git_commit="a" * 40,
                approval_level=0,
            )
        )
        request = DecisionRequest(
            id="req-open-dispatch",
            source_type="skill",
            source_id="run-open-dispatch",
            skill_id="skill-open",
            run_id="run-open",
            kind="dispatch",
            title="商品诊断卡",
            summary="全店主线：数据不足；决策：建议先复核",
            payload={
                "key_metrics": [
                    {"name": "免费访客环比", "value": "+17.07%", "status": "上升"},
                ],
                "top_actions": [
                    {
                        "rank": 1,
                        "dimension": "商品基础/价格",
                        "action": "先补采缺口数据",
                        "evidence": "商品级活动参与待核对",
                        "priority": "高",
                    },
                ],
                "data_source_links": [
                    {"label": "商品360", "url": "https://sycm.taobao.com/example"},
                ],
            },
            sla_at=datetime(2026, 4, 24, 23, 59),
        )
        session.add(request)
        task = TodoDispatchTask(
            request_id=request.id,
            executor="exec-open",
            content="运营动作：确认活动是否掉线；禁止动作：不要直接删词；验证指标：次日回升",
            deadline=datetime(2026, 4, 24, 23, 59),
            status="sent",
        )
        session.add(task)
        await session.flush()
        task_id = task.id
        token = create_dispatch_task_view_token(
            task_id=task_id,
            executor_user_id="exec-open",
            dingtalk_user_id="ding-open",
        )
        await session.commit()

    url = f"/api/dingtalk/open/dispatch/{task_id}?token={token}"
    resp = await client.get(url, headers={"user-agent": "Mozilla/5.0 DingTalk"})
    assert resp.status_code == 200
    assert "商品诊断卡" in resp.text
    assert "需要执行" in resp.text
    assert "先补采缺口数据" in resp.text
    assert "商品级活动参与待核对" in resp.text
    assert "商品360" in resp.text
    assert "samplebrand-open-page" not in resp.text

    browser_resp = await client.get(url, headers={"user-agent": "Mozilla/5.0"})
    assert browser_resp.status_code == 302
    assert browser_resp.headers["location"].startswith("/login?redirect=")

    ack_url = f"/api/dingtalk/open/dispatch/{task_id}/ack?token={token}"
    browser_ack = await client.get(ack_url, headers={"user-agent": "Mozilla/5.0"})
    assert browser_ack.status_code == 302
    assert browser_ack.headers["location"].startswith("/login?redirect=")

    ack_page = await client.get(ack_url, headers={"user-agent": "Mozilla/5.0 DingTalk"})
    assert ack_page.status_code == 200
    assert "标记完成" in ack_page.text
    assert "maxlength=\"1000\"" in ack_page.text
    assert "textarea" in ack_page.text

    async with async_session_factory() as session:
        task_after_get = await session.get(TodoDispatchTask, task_id)
        assert task_after_get.status == "sent"

    empty_ack = await client.post(
        ack_url,
        data={"note": ""},
        headers={"user-agent": "Mozilla/5.0 DingTalk"},
    )
    assert empty_ack.status_code == 400
    assert "请填写完成说明" in empty_ack.text

    ack_resp = await client.post(
        ack_url,
        data={"note": "已完成主图和价格复核"},
        headers={"user-agent": "Mozilla/5.0 DingTalk"},
    )
    assert ack_resp.status_code == 200
    assert "已标记完成" in ack_resp.text
    assert "已完成主图和价格复核" in ack_resp.text

    async with async_session_factory() as session:
        task_after = await session.get(TodoDispatchTask, task_id)
        assert task_after.status == "done"
        assert task_after.ack_channel == "dingtalk_open"
        assert task_after.ack_note == "已完成主图和价格复核"
        event = (
            await session.execute(
                select(LearningEvent)
                .where(LearningEvent.source_type == "todo_dispatch_task")
                .where(LearningEvent.source_id == str(task_id))
            )
        ).scalar_one()
        assert event.event_type == "todo.completed"
        assert event.skill_id == "skill-open"
        assert event.run_id == "run-open"
        assert event.metadata_json["source_channel"] == "dingtalk_open"
        samples = (
            await session.execute(
                select(LearningArtifact)
                .where(LearningArtifact.event_id == event.id)
                .where(LearningArtifact.artifact_kind == "training_sample")
            )
        ).scalars().all()
        assert len(samples) == 1
        sample = samples[0]
        assert sample.status == "materialized"
        assert sample.sink_type == "training_sample"
        assert sample.content_json["source_type"] == "dispatch_ack"
        assert sample.content_json["source_channel"] == "dingtalk_open"
        assert sample.content_json["input"]["task"]["content"].startswith("运营动作：确认活动是否掉线")
        assert sample.content_json["output"]["ack_note"] == "已完成主图和价格复核"
        assert sample.content_json["feedback"]["executor"] == "exec-open"
        assert "channel:dingtalk_open" in (sample.labels_json or [])
        edge = (
            await session.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "learning_artifact")
                .where(LearningFlowEdge.from_id == sample.id)
                .where(LearningFlowEdge.to_type == "training_sample")
                .where(LearningFlowEdge.to_id == sample.id)
                .where(LearningFlowEdge.relation == "created_sample")
            )
        ).scalar_one()
        ack_source_edge = (
            await session.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "dingtalk_feedback")
                .where(LearningFlowEdge.to_type == "todo_dispatch_task")
                .where(LearningFlowEdge.to_id == str(task_id))
                .where(LearningFlowEdge.relation == "submitted_feedback")
            )
        ).scalar_one()
        assert edge.skill_id == "skill-open"
        assert ack_source_edge.skill_id == "skill-open"
        assert ack_source_edge.metadata_json["source_channel"] == "dingtalk_open"
        assert ack_source_edge.metadata_json["source"] == "dingtalk_dispatch_ack"
        assert ack_source_edge.metadata_json["action"] == "dispatch_ack"
        assert ack_source_edge.metadata_json["feedback_sha256"]
        assert ack_source_edge.metadata_json["ack_note_sha256"]
        assert ack_source_edge.metadata_json["raw_text_returned"] is False
        assert "已完成主图和价格复核" not in json.dumps(
            ack_source_edge.metadata_json,
            ensure_ascii=False,
        )

    journeys_resp = await client.get(
        "/api/learning/flow-journeys",
        params={"days": 7, "skill_id": "skill-open", "source_type": "todo_dispatch_task"},
    )
    assert journeys_resp.status_code == 200, journeys_resp.text
    dispatch_journey = next(
        item for item in journeys_resp.json()["items"] if item["source_id"] == str(task_id)
    )
    ack_step = next(step for step in dispatch_journey["steps"] if step["action"] == "submitted_feedback")
    assert ack_step["stage"] == "feedback"
    assert ack_step["payload"]["source_channel"] == "dingtalk_open"
    assert ack_step["payload"]["source"] == "dingtalk_dispatch_ack"
    assert ack_step["payload"]["dispatch_task_id"] == task_id
    assert ack_step["payload"]["raw_text_returned"] is False
    assert "已完成主图和价格复核" not in json.dumps(ack_step, ensure_ascii=False)


@pytest.mark.asyncio
async def test_dingtalk_card_dispatch_ack_carries_structured_feedback_to_training_loop(client, monkeypatch):
    from sqlalchemy import select

    from app.auth.models import User
    from app.config import settings
    from app.database import async_session_factory
    from app.learning.models import LearningArtifact, LearningEvent, LearningFlowEdge
    from app.skills.core.models import Skill
    from app.todos.models import DecisionRequest, TodoDispatchTask

    monkeypatch.setattr(settings, "DINGTALK_CALLBACK_TOKEN", "")

    async with async_session_factory() as session:
        session.add(User(
            id="exec-card",
            username="exec-card",
            name="运营卡片执行人",
            role="operator",
            is_active=True,
            dingtalk_user_id="ding-exec-card",
        ))
        session.add(
            Skill(
                id="skill-card-dispatch",
                name="卡片派发 Skill",
                department="EC",
                status="active",
                git_commit="b" * 40,
                approval_level=0,
            )
        )
        request = DecisionRequest(
            id="req-card-dispatch",
            source_type="skill",
            source_id="run-card-dispatch",
            skill_id="skill-card-dispatch",
            run_id="run-card-dispatch",
            kind="dispatch",
            title="卡片派发任务",
            summary="复核预算承接",
            payload={"item_id": "item-card-dispatch", "priority": "high"},
            sla_at=datetime(2026, 4, 24, 23, 59),
        )
        session.add(request)
        task = TodoDispatchTask(
            request_id=request.id,
            executor="exec-card",
            content="运营动作：补齐预算承接截图；验证指标：点击率恢复",
            deadline=datetime(2026, 4, 24, 23, 59),
            status="sent",
        )
        session.add(task)
        await session.flush()
        task_id = task.id
        await session.commit()

    resp = await client.post(
        "/api/dingtalk/card-callback",
        json={
            "actionId": "ack",
            "params": {
                "action": "ack",
                "note": "已补齐预算截图，并备注人群包。",
                "feedback_type": "已执行",
                "rating": 5,
            },
            "cardPrivateData": {
                "kind": "dispatch_ack",
                "dispatch_task_id": str(task_id),
                "request_id": "req-card-dispatch",
                "executor_user_id": "exec-card",
            },
            "userId": "ding-exec-card",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["dispatch_task"]["status"] == "done"

    async with async_session_factory() as session:
        task_after = await session.get(TodoDispatchTask, task_id)
        assert task_after.status == "done"
        assert task_after.ack_channel == "dingtalk"
        assert task_after.ack_note == "已补齐预算截图，并备注人群包。"
        ack_feedback = task_after.extra["ack_feedback_payload"]
        assert ack_feedback["source_channel"] == "dingtalk"
        assert ack_feedback["note_text"] == "已补齐预算截图，并备注人群包。"
        assert ack_feedback["fields"]["source"] == "dingtalk_interactive_card"
        assert ack_feedback["fields"]["feedback_type"] == "已执行"
        assert ack_feedback["fields"]["rating"] == 5

        event = (
            await session.execute(
                select(LearningEvent)
                .where(LearningEvent.source_type == "todo_dispatch_task")
                .where(LearningEvent.source_id == str(task_id))
            )
        ).scalar_one()
        assert event.event_type == "todo.completed"
        assert event.metadata_json["source_channel"] == "dingtalk"
        assert set(event.metadata_json["feedback_fields"]) >= {
            "source",
            "feedback_type",
            "rating",
            "dispatch_task_id",
        }
        sample = (
            await session.execute(
                select(LearningArtifact)
                .where(LearningArtifact.event_id == event.id)
                .where(LearningArtifact.artifact_kind == "training_sample")
            )
        ).scalar_one()
        assert sample.status == "materialized"
        assert sample.sink_type == "training_sample"
        assert sample.content_json["source_type"] == "dispatch_ack"
        assert sample.content_json["source_channel"] == "dingtalk"
        assert sample.content_json["output"]["ack_note"] == "已补齐预算截图，并备注人群包。"
        assert sample.content_json["feedback"]["structured"]["fields"]["feedback_type"] == "已执行"
        assert sample.content_json["human_feedback_input"]["fields"]["rating"] == 5

        ack_source_edge = (
            await session.execute(
                select(LearningFlowEdge)
                .where(LearningFlowEdge.from_type == "dingtalk_feedback")
                .where(LearningFlowEdge.to_type == "todo_dispatch_task")
                .where(LearningFlowEdge.to_id == str(task_id))
                .where(LearningFlowEdge.relation == "submitted_feedback")
            )
        ).scalar_one()
        assert ack_source_edge.metadata_json["source_channel"] == "dingtalk"
        assert ack_source_edge.metadata_json["source"] == "dingtalk_interactive_card"
        assert ack_source_edge.metadata_json["feedback_type"] == "已执行"
        assert ack_source_edge.metadata_json["rating"] == 5
        assert ack_source_edge.metadata_json["feedback_sha256"]
        assert ack_source_edge.metadata_json["note_text_sha256"]
        assert ack_source_edge.metadata_json["raw_text_returned"] is False
        assert "已补齐预算截图" not in json.dumps(
            ack_source_edge.metadata_json,
            ensure_ascii=False,
        )

    manifest_resp = await client.get(
        "/api/learning/training-manifest",
        params={"skill_id": "skill-card-dispatch"},
    )
    assert manifest_resp.status_code == 200, manifest_resp.text
    manifest_sample = next(
        item
        for item in manifest_resp.json()["samples"]
        if item["artifact_id"] == sample.id
    )
    assert manifest_sample["source_type"] == "dispatch_ack"
    assert manifest_sample["source_channel"] == "dingtalk"
    assert manifest_sample["lineage_summary"]["has_human_feedback"] is True
    assert manifest_sample["lineage_summary"]["feedback_source"] == "dingtalk"
    assert set(manifest_sample["lineage_summary"]["feedback_fields"]) >= {
        "source",
        "feedback_type",
        "rating",
    }
    assert manifest_sample["lineage_summary"]["feedback_type"] == "已执行"
    assert manifest_sample["lineage_summary"]["rating"] == 5


@pytest.mark.asyncio
async def test_dingtalk_open_dispatch_enhances_only_samplebrand_low_consumption_skill(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk.open_links import create_dispatch_task_view_token
    from app.dingtalk.router import SAMPLEBRAND_LOW_CONSUMPTION_SKILL_ID
    from app.todos.models import DecisionRequest, TodoDispatchTask

    async with async_session_factory() as session:
        session.add(User(
            id="samplebrand-executor", username="samplebrand-executor", name="示例成员4",
            role="operator", is_active=True, dingtalk_user_id="ding-samplebrand-executor",
        ))
        payload = {
            "card_type": "video_low_consumption_decision_card",
            "analysis_schema": "samplebrand_video_low_consumption_daily_operator_v1",
            "priority": "P1",
            "account_name": "彭嘉欣",
            "key_metrics": [
                {"name": "低消耗视频数", "value": "1", "status": "建议关注"},
                {"name": "首条视频", "value": "示例企业一组_20260612-LWL-PJX_战甲AA_这个小雨伞牛啊_02"},
            ],
            "metric_sections": [
                {
                    "title": "当前视频 vs 同主题参考",
                    "metrics": [
                        {"name": "当前 ROI", "value": "1.02"},
                        {"name": "参考消耗", "value": "16204.05"},
                    ],
                },
            ],
            "chart_sections": [
                {
                    "type": "segmented_bar",
                    "key": "issue_mix",
                    "title": "问题分型分布",
                    "items": [{"key": "offer_conversion", "label": "成交承接优化", "value": 1}],
                },
                {
                    "type": "bar_compare",
                    "key": "video_metric_compare_1",
                    "title": "1. 低消耗 vs 高消耗指标",
                    "items": [
                        {"key": "cost", "label": "消耗", "unit": "¥", "low": 393.68, "benchmark": 16204.05},
                        {"key": "roi", "label": "ROI", "low": 1.02, "benchmark": 1.07},
                    ],
                },
            ],
            "detail_markdown": (
                "# 云视频账号 彭嘉欣 低消耗视频详细分析 2026-06-14\n"
                "- 结论摘要：建议关注 1 条。\n"
                "- 改进：补价格锚点、优惠理由、适用场景和明确下单引导。"
            ),
            "operation_actions": [
                {
                    "section": "建议视频 1",
                    "scenario": "示例企业一组_20260612-LWL-PJX_战甲AA_这个小雨伞牛啊_02",
                    "priority": "P1",
                    "dimension": "成交承接优化",
                    "topic_key": "这个小雨伞牛啊",
                    "theme_name": "这个小雨伞牛啊",
                    "editor_code": "PJX",
                    "product_code": "战甲AA",
                    "video_id": "101571379",
                    "benchmark_label": "同主题高消耗参考",
                    "benchmark_editor_code": "MJL",
                    "benchmark_video_title": "示例企业一组_20260609-LWL-MJL_战甲AA_再不买就亏大了_其他_01",
                    "evidence": "当前 ROI 1.02，点击率 1.47，转化率 7.27。",
                    "root_causes": ["当前素材基础信号不差，不能整条推倒重做。"],
                    "actions": ["补价格锚点、优惠理由、适用场景和明确下单引导。"],
                    "qianchuan_lifecycle_value_points": {
                        "manager_value": "高消耗参考整体点击 43719、峰值 01s，可用于判断当前低消耗是成交承接弱。",
                        "consumer_value": "消费者在 01s 附近被价格理由和适用场景触发点击。",
                        "optimization_focus": "围绕价格理由、适用场景和 CTA 做轻改。",
                    },
                    "low_visual_evidence": "低消耗视频：产品包装展示和优惠堆叠明确。",
                    "benchmark_visual_evidence": "通过加量不加价制造超值感。",
                },
            ],
            "data_quality_items": [
                {"dimension": "证据门槛", "issue": "low + benchmark 完整视频 passed。"},
            ],
            "forbidden_actions": ["不要跨主题套用好视频脚本或结论。"],
        }
        request = DecisionRequest(
            id="req-open-samplebrand-dispatch",
            source_type="skill",
            source_id="run-open-samplebrand-dispatch",
            skill_id=SAMPLEBRAND_LOW_CONSUMPTION_SKILL_ID,
            run_id="run-open-samplebrand-dispatch",
            kind="dispatch",
            title="P1｜云视频账号 彭嘉欣 2026-06-14 同主题低消耗视频改进",
            summary="建议补成交承接。",
            payload=payload,
            sla_at=datetime(2026, 6, 16, 8, 30),
        )
        session.add(request)
        await session.flush()
        task = TodoDispatchTask(
            request_id=request.id,
            executor="samplebrand-executor",
            content="需要执行：成交承接优化 / P1：建议补成交承接。",
            deadline=None,
            status="sent",
            extra={
                "required_output": "提交 1 条轻改版方案，写清保留点、弱项、行动引导和复测指标。",
                "retest_metrics": "复测消耗、点击率、转化率、ROI、成交金额。",
            },
        )
        session.add(task)
        await session.flush()
        task_id = task.id
        token = create_dispatch_task_view_token(
            task_id=task_id,
            executor_user_id="samplebrand-executor",
            dingtalk_user_id="ding-samplebrand-executor",
        )
        await session.commit()

    resp = await client.get(
        f"/api/dingtalk/open/dispatch/{task_id}?token={token}",
        headers={"user-agent": "Mozilla/5.0 DingTalk"},
    )

    assert resp.status_code == 200
    assert "samplebrand-open-page" in resp.text
    assert "任务概览" in resp.text
    assert "执行动作" in resp.text
    assert "视频与对标" in resp.text
    assert "数据图表" in resp.text
    assert "问题分型分布" in resp.text
    assert "低消耗 vs 高消耗指标" in resp.text
    assert "¥393.68" in resp.text
    assert "¥16,204.05" in resp.text
    assert "判断依据" in resp.text
    assert "视觉证据" in resp.text
    assert "验证与边界" in resp.text
    assert "补价格锚点、优惠理由、适用场景和明确下单引导" in resp.text
    assert "示例企业一组_20260612-LWL-PJX_战甲AA_这个小雨伞牛啊_02" in resp.text
    assert "剪辑人" in resp.text
    assert "PJX" in resp.text
    assert "参考等级" in resp.text
    assert "同主题高消耗参考" in resp.text
    assert "参考剪辑人" in resp.text
    assert "MJL" in resp.text
    assert "高点击依据" in resp.text
    assert "高消耗参考整体点击 43719" in resp.text
    assert "消费者在 01s 附近被价格理由和适用场景触发点击" in resp.text
    assert "low + benchmark 完整视频 passed" in resp.text
    assert "完整分析原文" not in resp.text
    assert resp.text.count("补价格锚点、优惠理由、适用场景和明确下单引导") == 1
    assert resp.text.count("低消耗视频：产品包装展示和优惠堆叠明确。") == 1
    assert resp.text.count("通过加量不加价制造超值感。") == 1


@pytest.mark.asyncio
async def test_dingtalk_open_todo_enhances_only_samplebrand_low_consumption_skill(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk.open_links import create_todo_view_token
    from app.dingtalk.router import SAMPLEBRAND_LOW_CONSUMPTION_SKILL_ID
    from app.todos.models import AITodo, DecisionRequest

    async with async_session_factory() as session:
        session.add(User(
            id="samplebrand-manager", username="samplebrand-manager", name="大莹",
            role="operator", is_active=True, dingtalk_user_id="ding-samplebrand-manager",
        ))
        payload = {
            "card_type": "video_low_consumption_decision_card",
            "analysis_schema": "samplebrand_video_low_consumption_daily_operator_v1",
            "priority": "P1",
            "account_name": "彭嘉欣",
            "analysis_date": "2026-06-14",
            "data_quality": "有完整视频证据",
            "business_action_allowed": True,
            "recommended_decision": "建议优先处理低消耗视频并补成交承接。",
            "key_metrics": [
                {"name": "低消耗视频数", "value": "1", "status": "建议关注"},
                {"name": "Agent 分型", "value": "成交承接优化1条", "status": "已结构化"},
            ],
            "detail_markdown": (
                "# 云视频账号 彭嘉欣 低消耗视频详细分析 2026-06-14\n"
                "- 低消耗画面证据：开头铺垫慢，商品露出晚。"
            ),
            "chart_sections": [
                {
                    "type": "segmented_bar",
                    "key": "benchmark_mix",
                    "title": "对标质量",
                    "items": [{"key": "category_fallback", "label": "同品类弱参考", "value": 1}],
                },
                {
                    "type": "bar_compare",
                    "key": "video_metric_compare_1",
                    "title": "1. 低消耗 vs 高消耗指标",
                    "items": [
                        {"key": "cost", "label": "消耗", "unit": "¥", "low": 393.68, "benchmark": 16204.05},
                    ],
                },
            ],
            "operation_actions": [
                {
                    "section": "建议视频 1",
                    "scenario": "示例企业一组_20260612-LWL-PJX_战甲AA_这个小雨伞牛啊_02",
                    "priority": "P1",
                    "dimension": "成交承接优化",
                    "topic_key": "这个小雨伞牛啊",
                    "theme_name": "这个小雨伞牛啊",
                    "editor_code": "PJX",
                    "product_code": "战甲AA",
                    "video_id": "101571379",
                    "benchmark_label": "同品类降级参考",
                    "benchmark_warning": "未找到非本人同主题高消耗视频，当前仅用同品类高消耗视频作弱参考；不能照搬脚本。",
                    "benchmark_editor_code": "MJL",
                    "root_causes": ["分型：成交承接优化。当前素材基础信号不差，优先补价格锚点。"],
                    "actions": ["补价格锚点、优惠理由和明确下单引导。"],
                    "qianchuan_lifecycle_value_points": {
                        "manager_value": "高消耗参考整体点击 43719、峰值 01s，可用于判断当前低消耗是成交承接弱。",
                        "consumer_value": "消费者在 01s 附近被价格理由和适用场景触发点击。",
                        "optimization_focus": "围绕价格理由、适用场景和 CTA 做轻改。",
                    },
                    "low_visual_evidence": "低消耗视频：商品露出晚。",
                    "benchmark_visual_evidence": "卖点清晰。",
                },
            ],
            "data_quality_items": [
                {"dimension": "投放承接数据", "issue": "预算、出价、人群包未完整返回。"},
            ],
            "forbidden_actions": ["不要跨主题复用高消耗视频脚本。"],
            "dispatch_tasks": [
                {
                    "content": (
                        "# 云视频账号 彭嘉欣 低消耗视频详细分析 2026-06-14\n"
                        "- 低消耗画面证据：这段完整 Markdown 不应直接展示。"
                    ),
                    "executor": "samplebrand-executor",
                    "extra": {"required_output": "提交改版视频方向、复用样本和复测指标。"},
                },
            ],
        }
        request = DecisionRequest(
            id="req-open-samplebrand-video",
            source_type="skill",
            source_id="run-open-samplebrand-video",
            skill_id=SAMPLEBRAND_LOW_CONSUMPTION_SKILL_ID,
            run_id="run-open-samplebrand-video",
            kind="dispatch",
            title="P1｜云视频账号 彭嘉欣 2026-06-14 同主题低消耗视频改进",
            summary="云视频账号 彭嘉欣 有 1 条低消耗视频通过视觉证据门槛。",
            payload=payload,
            sla_at=datetime(2026, 6, 16, 8, 30),
        )
        session.add(request)
        await session.flush()
        todo = AITodo(request_id=request.id, kind="dispatch", assignee="samplebrand-manager", status="pending")
        session.add(todo)
        await session.flush()
        todo_id = todo.id
        token = create_todo_view_token(
            todo_id=todo_id,
            assignee_user_id="samplebrand-manager",
            dingtalk_user_id="ding-samplebrand-manager",
        )
        await session.commit()

    resp = await client.get(
        f"/api/dingtalk/open/todos/{todo_id}?token={token}",
        headers={"user-agent": "Mozilla/5.0 DingTalk"},
    )

    assert resp.status_code == 200
    assert "samplebrand-open-page" in resp.text
    assert "处理结论" in resp.text
    assert "建议派发" in resp.text
    assert "视频与对标" in resp.text
    assert "数据图表" in resp.text
    assert "对标质量" in resp.text
    assert "同品类弱参考" in resp.text
    assert "低消耗 vs 高消耗指标" in resp.text
    assert "为什么这样处理" in resp.text
    assert "建议动作" in resp.text
    assert "视觉证据" in resp.text
    assert "复测与风险边界" in resp.text
    assert "补价格锚点、优惠理由和明确下单引导" in resp.text
    assert "同品类降级参考" in resp.text
    assert "弱参考" in resp.text
    assert "不能照搬脚本" in resp.text
    assert "高点击依据" in resp.text
    assert "高消耗参考整体点击 43719" in resp.text
    assert "消费者在 01s 附近被价格理由和适用场景触发点击" in resp.text
    assert "提交改版视频方向、复用样本和复测指标" in resp.text
    assert "预算、出价、人群包未完整返回" in resp.text
    assert "完整 Markdown 不应直接展示" not in resp.text
    assert "详细分析" not in resp.text
    assert "逐视频建议" not in resp.text
    assert resp.text.count("补价格锚点、优惠理由和明确下单引导") == 1
    assert resp.text.count("低消耗视频：商品露出晚。") == 1
    assert resp.text.count("卖点清晰。") == 1


@pytest.mark.asyncio
async def test_dingtalk_open_todo_keeps_other_skills_on_legacy_renderer(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk.open_links import create_todo_view_token
    from app.todos.models import AITodo, DecisionRequest

    async with async_session_factory() as session:
        session.add(User(
            id="other-manager", username="other-manager", name="其他主管",
            role="operator", is_active=True, dingtalk_user_id="ding-other-manager",
        ))
        request = DecisionRequest(
            id="req-open-other-video",
            source_type="skill",
            source_id="run-open-other-video",
            skill_id="other-video-skill",
            run_id="run-open-other-video",
            kind="dispatch",
            title="其他 Skill 视频待办",
            summary="保留旧版摘要渲染。",
            payload={
                "card_type": "video_low_consumption_decision_card",
                "analysis_schema": "samplebrand_video_low_consumption_daily_operator_v1",
                "key_metrics": [{"name": "低消耗视频数", "value": "1"}],
                "detail_markdown": "# 不应展示的详细分析\n- 这段只允许目标 Skill 展示。",
                "operation_actions": [{"scenario": "不应展示的逐视频建议", "actions": ["不要显示"]}],
            },
            sla_at=datetime(2026, 6, 16, 8, 30),
        )
        session.add(request)
        await session.flush()
        todo = AITodo(request_id=request.id, kind="dispatch", assignee="other-manager", status="pending")
        session.add(todo)
        await session.flush()
        todo_id = todo.id
        token = create_todo_view_token(
            todo_id=todo_id,
            assignee_user_id="other-manager",
            dingtalk_user_id="ding-other-manager",
        )
        await session.commit()

    resp = await client.get(
        f"/api/dingtalk/open/todos/{todo_id}?token={token}",
        headers={"user-agent": "Mozilla/5.0 DingTalk"},
    )

    assert resp.status_code == 200
    assert "保留旧版摘要渲染" in resp.text
    assert "samplebrand-open-page" not in resp.text
    assert "详细分析" not in resp.text
    assert "不应展示的逐视频建议" not in resp.text
