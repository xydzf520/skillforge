"""派发待办端到端: review + dispatch 两条链路。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_review_spec_creates_review_todo(client, monkeypatch):
    """Skill output.todos 包含一条 review → 直接生成 review 待办，无 dispatch_tasks。"""
    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.todos.models import AITodo, DecisionRequest, TodoDispatchTask
    from app.todos.service import todo_service
    from sqlalchemy import select

    enqueued: list[dict] = []

    async def fake_enqueue(*args, **kwargs):
        enqueued.append({"args": args, "kwargs": kwargs})
        return "msg-should-not-happen"

    monkeypatch.setattr(outbox_mod.outbox, "enqueue", fake_enqueue)

    async with async_session_factory() as session:
        session.add(User(
            id="rev1", username="rev1", name="Rev1",
            role="ai_engineer", is_active=True,
            dingtalk_user_id="ding-rev1",
        ))
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-spec-rev-1",
            skill_id="skill-spec",
            skill_meta={"name": "Spec Skill"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "summary": "ignore",
                    "todos": [
                        {
                            "kind": "review",
                            "title": "本周高 ROI 加预算",
                            "summary": "ROI 远高于盈亏线",
                            "reviewers": ["rev1"],
                            "decision_mode": "any_of",
                            "payload": {"sku": "X-1"},
                        }
                    ],
                },
            },
        )
        await session.commit()
        assert len(requests) == 1
        request = requests[0]
        assert request.kind == "review"
        assert request.callback_status == "skipped"

        todos = (
            await session.execute(select(AITodo).where(AITodo.request_id == request.id))
        ).scalars().all()
        assert len(todos) == 1
        assert todos[0].kind == "review"
        assert todos[0].assignee == "rev1"

        dispatch = (
            await session.execute(
                select(TodoDispatchTask).where(TodoDispatchTask.request_id == request.id)
            )
        ).scalars().all()
        assert dispatch == []
        assert enqueued == []


@pytest.mark.asyncio
async def test_explicit_spec_payload_does_not_embed_full_input_output(client):
    """显式 payload 是运营决策卡，不再被平台补进完整 input/output 快照。"""
    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.models import DecisionRequest
    from app.todos.service import todo_service
    from sqlalchemy import select

    async with async_session_factory() as session:
        session.add(User(
            id="rev-payload", username="rev-payload", name="RevPayload",
            role="ai_engineer", is_active=True,
        ))
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-spec-payload-1",
            skill_id="skill-payload",
            skill_meta={"name": "Payload Skill"},
            decision={
                "input_snapshot": {"raw": ["should-not-appear"]},
                "output_result": {"raw_output": {"huge": True}, "todos": [
                    {
                        "kind": "review",
                        "title": "运营可读决策卡",
                        "summary": "只展示卡片摘要",
                        "reviewers": ["rev-payload"],
                        "payload": {
                            "object_id": "SKU-1",
                            "recommended_decision": "建议通过",
                            "output": {"summary": "处理 SKU-1", "suggestions": ["补采价格"]},
                        },
                    }
                ]},
            },
        )
        await session.commit()
        request = (
            await session.execute(
                select(DecisionRequest).where(DecisionRequest.id == requests[0].id)
            )
        ).scalar_one()

    assert request.payload["object_id"] == "SKU-1"
    assert request.payload["output"]["summary"] == "处理 SKU-1"
    assert "raw" not in request.payload.get("input", {})
    assert "raw_output" not in request.payload.get("output", {})
    assert request.payload["_debug_ref"]["run_id"] == "run-spec-payload-1"


@pytest.mark.asyncio
async def test_dispatch_spec_fans_out_after_approval(client, monkeypatch):
    """dispatch 待办：管理者审批通过 → 子任务全部 sent + 推送 outbox。"""
    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.todos.models import AITodo, DecisionRequest, TodoDispatchTask
    from app.todos.service import todo_service
    from sqlalchemy import select

    enqueued: list[dict] = []

    async def fake_enqueue(message_type, recipient, payload, priority=5, **kwargs):
        enqueued.append({
            "type": message_type,
            "recipient": recipient,
            "payload_title": payload.get("title"),
            "related_type": kwargs.get("related_type"),
            "related_id": kwargs.get("related_id"),
        })
        return f"msg-{len(enqueued)}"

    monkeypatch.setattr(outbox_mod.outbox, "enqueue", fake_enqueue)

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="mgr1", username="mgr1", name="Manager",
                role="ai_engineer", is_active=True,
                dingtalk_user_id="ding-mgr1",
            ),
            User(
                id="exec_a", username="exec_a", name="ExecA",
                role="operator", is_active=True,
                dingtalk_user_id="ding-exec-a",
            ),
            User(
                id="exec_b", username="exec_b", name="ExecB",
                role="operator", is_active=True,
                dingtalk_user_id="ding-exec-b",
            ),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-spec-disp-1",
            skill_id="skill-disp",
            skill_meta={"name": "Disp Skill"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [
                        {
                            "kind": "dispatch",
                            "title": "本周低质量 SKU 下架",
                            "summary": "请下架以下 2 个",
                            "reviewers": ["mgr1"],
                            "tasks": [
                                {
                                    "executor": "exec_a",
                                    "content": "下架 SKU-12345（差评率 18%）",
                                    "deadline": "2026-04-09T18:00:00",
                                },
                                {
                                    "executor": "exec_b",
                                    "content": "下架 SKU-67890（滞销 90 天）",
                                },
                            ],
                        }
                    ]
                },
            },
        )
        await session.commit()

    assert len(requests) == 1
    request = requests[0]
    assert request.kind == "dispatch"

    # 创建阶段：只进入平台待办，不直接推钉钉；子任务全部 awaiting_dispatch
    enqueue_after_create = list(enqueued)
    assert enqueue_after_create == []

    async with async_session_factory() as session:
        request = await session.get(DecisionRequest, request.id)
        tasks_before = (
            await session.execute(
                select(TodoDispatchTask).where(TodoDispatchTask.request_id == request.id)
            )
        ).scalars().all()
        assert len(tasks_before) == 2
        assert all(t.status == "awaiting_dispatch" for t in tasks_before)

        manager_todo = (
            await session.execute(
                select(AITodo)
                .where(AITodo.request_id == request.id)
                .where(AITodo.assignee == "mgr1")
            )
        ).scalar_one()
        # 管理者通过审批 → 自动 fan-out
        decision_result = await todo_service.decide(
            session,
            todo_id=manager_todo.id,
            decision="approved",
            decided_by="mgr1",
            channel="web",
        )
        await session.commit()
        assert decision_result["dispatch_fanout"] == {
            "total": 2,
            "statuses": {"sent": 2},
            "sent": 2,
            "degraded": 0,
        }

    # 平台通过后才把子任务推到执行人个人钉钉
    recipients = [e["recipient"] for e in enqueued]
    assert "ding-exec-a" in recipients
    assert "ding-exec-b" in recipients

    async with async_session_factory() as session:
        tasks_after = (
            await session.execute(
                select(TodoDispatchTask).where(TodoDispatchTask.request_id == request.id)
            )
        ).scalars().all()
        assert len(tasks_after) == 2

        assert all(t.status == "sent" for t in tasks_after)
        assert all(t.dingtalk_msg_id for t in tasks_after)

        # 执行人 ack 一个子任务 → status=done
        target = tasks_after[0]
        result = await todo_service.ack_dispatch_task(
            session,
            task_id=target.id,
            actor_id=target.executor,
            note="已完成 SKU 下架",
        )
        await session.commit()
        assert result["status"] == "done"
        assert result["ack_note"] == "已完成 SKU 下架"


@pytest.mark.asyncio
async def test_dispatch_default_executor_is_remembered_and_visible(client, monkeypatch):
    """通过并派发成功后，同一管理者/Skill/部门的后续待办带出默认派发人。"""
    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.todos.models import AITodo, TodoDispatchPreference, TodoDispatchTask
    from app.todos.service import todo_service
    from sqlalchemy import select

    async def fake_enqueue(*args, **kwargs):
        return "msg-default"

    monkeypatch.setattr(outbox_mod.outbox, "enqueue", fake_enqueue)

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="mgr-default", username="mgr-default", name="主管",
                role="operator", department="运营部", is_active=True, state="active",
            ),
            User(
                id="exec-default-a", username="exec-default-a", name="张三",
                role="operator", department="运营部", is_active=True, state="active",
                dingtalk_user_id="ding-a",
            ),
            User(
                id="exec-default-b", username="exec-default-b", name="李四",
                role="operator", department="运营部", is_active=True, state="active",
                dingtalk_user_id="ding-b",
            ),
        ])
        await session.commit()

        first_requests = await todo_service.create_from_execution(
            session,
            run_id="run-default-1",
            skill_id="skill-default-dispatch",
            skill_meta={"name": "默认派发 Skill"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "第一次派发",
                        "reviewers": ["mgr-default"],
                        "tasks": [{"executor": "exec-default-a", "content": "处理 A"}],
                    }],
                },
            },
        )
        first_todo = (
            await session.execute(
                select(AITodo).where(AITodo.request_id == first_requests[0].id)
            )
        ).scalar_one()
        await todo_service.decide(
            session,
            todo_id=first_todo.id,
            decision="approved",
            decided_by="mgr-default",
            channel="web",
        )
        await session.commit()

        preference = (
            await session.execute(select(TodoDispatchPreference))
        ).scalar_one()
        assert preference.actor_id == "mgr-default"
        assert preference.skill_id == "skill-default-dispatch"
        assert preference.dispatch_object_key == ""
        assert preference.last_executor_id == "exec-default-a"

        second_requests = await todo_service.create_from_execution(
            session,
            run_id="run-default-2",
            skill_id="skill-default-dispatch",
            skill_meta={"name": "默认派发 Skill"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "第二次派发",
                        "reviewers": ["mgr-default"],
                        "tasks": [{"content": "处理 B"}],
                    }],
                },
            },
        )
        second_todo = (
            await session.execute(
                select(AITodo).where(AITodo.request_id == second_requests[0].id)
            )
        ).scalar_one()
        manager = await session.get(User, "mgr-default")
        detail = await todo_service.get_todo_detail(
            session,
            todo_id=second_todo.id,
            current_user=manager,
        )
        task = detail["dispatch_tasks"][0]
        assert task["executor"] is None
        assert task["default_executor"] == "exec-default-a"
        assert task["default_executor_name"] == "张三"
        assert task["executor_source"] == "default"

        listed = await todo_service.list_todos(
            session,
            current_user=manager,
            status="pending",
            kind="dispatch",
            page_size=10,
        )
        item = next(row for row in listed["items"] if row["id"] == second_todo.id)
        assert item["dispatch_default_summary"] == {
            "executor_ids": ["exec-default-a"],
            "executor_names": ["张三"],
            "missing_count": 0,
            "total_count": 1,
        }

        second_task = (
            await session.execute(
                select(TodoDispatchTask).where(TodoDispatchTask.request_id == second_requests[0].id)
            )
        ).scalar_one()
        await todo_service.update_todo_draft(
            session,
            todo_id=second_todo.id,
            actor=manager,
            dispatch_tasks=[{"id": second_task.id, "executor": "exec-default-b"}],
        )
        await todo_service.decide(
            session,
            todo_id=second_todo.id,
            decision="approved",
            decided_by="mgr-default",
            channel="web",
        )
        await session.commit()

        refreshed_preference = (
            await session.execute(select(TodoDispatchPreference))
        ).scalar_one()
        assert refreshed_preference.dispatch_object_key == ""
        assert refreshed_preference.last_executor_id == "exec-default-b"


@pytest.mark.asyncio
async def test_dispatch_default_executor_is_product_specific(client, monkeypatch):
    """不同商品 ID 记住不同派发人，后续任务按商品分别带默认值。"""
    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.todos.models import AITodo, TodoDispatchPreference
    from app.todos.service import todo_service
    from sqlalchemy import select

    async def fake_enqueue(*args, **kwargs):
        return "msg-product-default"

    monkeypatch.setattr(outbox_mod.outbox, "enqueue", fake_enqueue)

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="mgr-product-default", username="mgr-product-default", name="主管",
                role="operator", department="运营部", is_active=True, state="active",
            ),
            User(
                id="exec-product-a", username="exec-product-a", name="张三",
                role="operator", department="运营部", is_active=True, state="active",
                dingtalk_user_id="ding-product-a",
            ),
            User(
                id="exec-product-b", username="exec-product-b", name="李四",
                role="operator", department="运营部", is_active=True, state="active",
                dingtalk_user_id="ding-product-b",
            ),
        ])
        await session.commit()

        first_requests = await todo_service.create_from_execution(
            session,
            run_id="run-product-default-1",
            skill_id="skill-product-dispatch",
            skill_meta={"name": "商品派发 Skill"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "第一次商品派发",
                        "reviewers": ["mgr-product-default"],
                        "tasks": [
                            {
                                "executor": "exec-product-a",
                                "content": "商品 8001 免费流量整改",
                                "extra": {"item_id": "8001"},
                            },
                            {
                                "executor": "exec-product-b",
                                "content": "商品 8002 付费投放整改",
                                "extra": {"item_id": "8002"},
                            },
                        ],
                    }],
                },
            },
        )
        first_todo = (
            await session.execute(
                select(AITodo).where(AITodo.request_id == first_requests[0].id)
            )
        ).scalar_one()
        await todo_service.decide(
            session,
            todo_id=first_todo.id,
            decision="approved",
            decided_by="mgr-product-default",
            channel="web",
        )
        await session.commit()

        preferences = (
            await session.execute(select(TodoDispatchPreference))
        ).scalars().all()
        assert {
            preference.dispatch_object_key: preference.last_executor_id
            for preference in preferences
        } == {
            "item:8001": "exec-product-a",
            "item:8002": "exec-product-b",
        }

        second_requests = await todo_service.create_from_execution(
            session,
            run_id="run-product-default-2",
            skill_id="skill-product-dispatch",
            skill_meta={"name": "商品派发 Skill"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "第二次商品派发",
                        "reviewers": ["mgr-product-default"],
                        "tasks": [
                            {
                                "content": "商品 8002 复查",
                                "extra": {"item_id": "8002"},
                            },
                            {
                                "content": "商品 8001 复查",
                                "extra": {"item_id": "8001"},
                            },
                            {
                                "content": "商品 8003 复查",
                                "extra": {"item_id": "8003"},
                            },
                        ],
                    }],
                },
            },
        )
        second_todo = (
            await session.execute(
                select(AITodo).where(AITodo.request_id == second_requests[0].id)
            )
        ).scalar_one()
        manager = await session.get(User, "mgr-product-default")
        detail = await todo_service.get_todo_detail(
            session,
            todo_id=second_todo.id,
            current_user=manager,
        )

        assert [
            (
                task["dispatch_object_key"],
                task["default_executor"],
                task["default_executor_name"],
                task["executor_source"],
            )
            for task in detail["dispatch_tasks"]
        ] == [
            ("item:8002", "exec-product-b", "李四", "default"),
            ("item:8001", "exec-product-a", "张三", "default"),
            ("item:8003", None, None, "none"),
        ]

        listed = await todo_service.list_todos(
            session,
            current_user=manager,
            status="pending",
            kind="dispatch",
            page_size=10,
        )
        item = next(row for row in listed["items"] if row["id"] == second_todo.id)
        assert item["dispatch_default_summary"] == {
            "executor_ids": ["exec-product-b", "exec-product-a"],
            "executor_names": ["李四", "张三"],
            "missing_count": 1,
            "total_count": 3,
        }


@pytest.mark.asyncio
async def test_dispatch_default_executor_ignores_disabled_user(client, monkeypatch):
    """上次派发人被禁用后，详情和列表不再展示该默认值。"""
    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.todos.models import AITodo, TodoDispatchPreference
    from app.todos.service import todo_service
    from sqlalchemy import select

    async def fake_enqueue(*args, **kwargs):
        return "msg-disabled"

    monkeypatch.setattr(outbox_mod.outbox, "enqueue", fake_enqueue)

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="mgr-disabled-default", username="mgr-disabled-default", name="主管",
                role="operator", department="运营部", is_active=True, state="active",
            ),
            User(
                id="exec-disabled-default", username="exec-disabled-default", name="张三",
                role="operator", department="运营部", is_active=False, state="disabled",
            ),
        ])
        session.add(TodoDispatchPreference(
            actor_id="mgr-disabled-default",
            skill_id="skill-disabled-default",
            target_org_unit_id="",
            department="运营部",
            dispatch_object_key="",
            last_executor_id="exec-disabled-default",
        ))
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-disabled-default",
            skill_id="skill-disabled-default",
            skill_meta={"name": "禁用默认派发 Skill"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "禁用默认派发",
                        "reviewers": ["mgr-disabled-default"],
                        "tasks": [{"content": "处理 C"}],
                    }],
                },
            },
        )
        todo = (
            await session.execute(
                select(AITodo).where(AITodo.request_id == requests[0].id)
            )
        ).scalar_one()
        manager = await session.get(User, "mgr-disabled-default")

        detail = await todo_service.get_todo_detail(
            session,
            todo_id=todo.id,
            current_user=manager,
        )
        assert detail["dispatch_tasks"][0]["default_executor"] is None
        assert detail["dispatch_tasks"][0]["executor_source"] == "none"

        listed = await todo_service.list_todos(
            session,
            current_user=manager,
            status="pending",
            kind="dispatch",
        )
        item = next(row for row in listed["items"] if row["id"] == todo.id)
        assert item["dispatch_default_summary"]["executor_ids"] == []
        assert item["dispatch_default_summary"]["missing_count"] == 1


@pytest.mark.asyncio
async def test_dispatch_preference_insert_upserts_existing_scope(client, monkeypatch):
    """并发首次写入同一范围时，偏好写入应走 upsert 而不是撞唯一键。"""
    from datetime import datetime

    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.models import DecisionRequest, TodoDispatchPreference, TodoDispatchTask
    from app.todos.service import todo_service
    from sqlalchemy import select

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="mgr-upsert", username="mgr-upsert", name="主管",
                role="operator", department="运营部", is_active=True, state="active",
            ),
            User(
                id="exec-upsert-a", username="exec-upsert-a", name="张三",
                role="operator", department="运营部", is_active=True, state="active",
            ),
            User(
                id="exec-upsert-b", username="exec-upsert-b", name="李四",
                role="operator", department="运营部", is_active=True, state="active",
            ),
        ])
        request = DecisionRequest(
            id="req-upsert-pref",
            source_type="test",
            source_id="req-upsert-pref",
            skill_id="skill-upsert-pref",
            kind="dispatch",
            title="偏好 upsert",
            summary="",
            payload={},
            sla_at=datetime(2026, 5, 6),
        )
        session.add(request)
        await session.flush()
        session.add(TodoDispatchPreference(
            actor_id="mgr-upsert",
            skill_id="skill-upsert-pref",
            target_org_unit_id="",
            department="运营部",
            last_executor_id="exec-upsert-a",
        ))
        session.add(TodoDispatchTask(
            request_id=request.id,
            executor="exec-upsert-b",
            content="处理 B",
            status="sent",
        ))
        await session.flush()

        async def fake_missing_preference(*args, **kwargs):
            return None

        monkeypatch.setattr(todo_service, "_load_dispatch_preference", fake_missing_preference)

        await todo_service._remember_dispatch_preference(
            session,
            request=request,
            actor_id="mgr-upsert",
        )
        await session.commit()

        preferences = (
            await session.execute(select(TodoDispatchPreference))
        ).scalars().all()
        assert len(preferences) == 1
        assert preferences[0].actor_id == "mgr-upsert"
        assert preferences[0].last_executor_id == "exec-upsert-b"


@pytest.mark.asyncio
async def test_manager_can_edit_pending_dispatch_todo_before_approval(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.models import AITodo, TodoDispatchTask
    from app.todos.service import todo_service
    from sqlalchemy import select

    async with async_session_factory() as session:
        session.add_all([
            User(id="mgr-edit", username="mgr-edit", name="ManagerEdit", role="ai_engineer", is_active=True),
            User(id="exec-edit", username="exec-edit", name="ExecEdit", role="operator", is_active=True),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-edit-disp-1",
            skill_id="skill-edit-disp",
            skill_meta={"name": "Editable Dispatch"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [
                        {
                            "kind": "dispatch",
                            "title": "AI 生成待办",
                            "summary": "AI 初稿",
                            "reviewers": ["mgr-edit"],
                            "payload": {
                                "recommended_decision": "AI 建议",
                                "suggested": ["AI 动作"],
                                "forbidden_actions": ["AI 禁止"],
                            },
                            "tasks": [{"executor": "exec-edit", "content": "AI 派发内容"}],
                        }
                    ]
                },
            },
        )
        await session.commit()
        manager_todo = (
            await session.execute(select(AITodo).where(AITodo.request_id == requests[0].id))
        ).scalar_one()
        task = (
            await session.execute(select(TodoDispatchTask).where(TodoDispatchTask.request_id == requests[0].id))
        ).scalar_one()

        result = await todo_service.update_todo_draft(
            session,
            todo_id=manager_todo.id,
            actor=await session.get(User, "mgr-edit"),
            recommended_decision="主管改后的建议",
            suggested_actions=["主管动作 1", "主管动作 2"],
            forbidden_actions=["主管禁止"],
            dispatch_tasks=[{"id": task.id, "executor": "exec-edit", "content": "主管改后的派发内容", "deadline": "2026-04-23T18:00:00"}],
        )
        await session.commit()

    assert result["payload"]["recommended_decision"] == "主管改后的建议"
    assert result["payload"]["suggested"] == ["主管动作 1", "主管动作 2"]
    assert result["payload"]["forbidden_actions"] == ["主管禁止"]
    assert result["dispatch_tasks"][0]["content"] == "主管改后的派发内容"
    assert result["dispatch_tasks"][0]["deadline"].startswith("2026-04-23T18:00:00")


@pytest.mark.asyncio
async def test_dispatch_rejection_cancels_subtasks(client, monkeypatch):
    """dispatch 待办被驳回 → 子任务全部 cancelled，executor 不会收到推送。"""
    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.todos.models import AITodo, TodoDispatchTask
    from app.todos.service import todo_service
    from sqlalchemy import select

    enqueued: list[dict] = []

    async def fake_enqueue(message_type, recipient, payload, priority=5, **kwargs):
        enqueued.append({"recipient": recipient})
        return f"msg-{len(enqueued)}"

    monkeypatch.setattr(outbox_mod.outbox, "enqueue", fake_enqueue)

    async with async_session_factory() as session:
        session.add_all([
            User(id="mgr2", username="mgr2", name="M2", role="ai_engineer",
                 is_active=True, dingtalk_user_id="ding-mgr2"),
            User(id="ex2", username="ex2", name="E2", role="operator",
                 is_active=True, dingtalk_user_id="ding-ex2"),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-spec-disp-2",
            skill_id="skill-disp-2",
            skill_meta={"name": "Disp 2"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "派发被驳回",
                        "reviewers": ["mgr2"],
                        "tasks": [{"executor": "ex2", "content": "干活"}],
                    }]
                },
            },
        )
        await session.commit()

    request = requests[0]

    async with async_session_factory() as session:
        manager_todo = (
            await session.execute(
                select(AITodo).where(AITodo.assignee == "mgr2")
            )
        ).scalar_one()
        await todo_service.decide(
            session,
            todo_id=manager_todo.id,
            decision="rejected",
            decided_by="mgr2",
        )
        await session.commit()

    async with async_session_factory() as session:
        tasks = (
            await session.execute(
                select(TodoDispatchTask).where(TodoDispatchTask.request_id == request.id)
            )
        ).scalars().all()
        assert len(tasks) == 1
        assert tasks[0].status == "cancelled"

    # 没有 ex2 的钉钉推送
    assert not any(e["recipient"] == "ding-ex2" for e in enqueued)


def test_todo_spec_build_helpers():
    from app.todos.todo_spec import build_dispatch_task, build_dispatch_todo, build_review_todo

    task = build_dispatch_task("处理 SKU-1", executor="alice", deadline="2026-04-12T18:00:00")
    assert task["executor"] == "alice"
    assert task["content"] == "处理 SKU-1"

    dispatch = build_dispatch_todo(
        "整改任务",
        tasks=[task],
        reviewers=["mgr1"],
        summary="请处理",
    )
    assert dispatch["kind"] == "dispatch"
    assert dispatch["tasks"][0]["content"] == "处理 SKU-1"
    assert dispatch["reviewers"] == ["mgr1"]

    review = build_review_todo(
        "审批任务",
        reviewers=["mgr1"],
        summary="请审批",
    )
    assert review["kind"] == "review"
    assert review["reviewers"] == ["mgr1"]


@pytest.mark.asyncio
async def test_build_dispatch_spec_api(client):
    resp = await client.post(
        "/api/todos/spec/build-dispatch",
        json={
            "title": "整改任务",
            "summary": "请处理 2 个 SKU",
            "reviewers": ["mgr1"],
            "tasks": [
                {"executor": "alice", "content": "处理 SKU-1"},
                {"content": "处理 SKU-2"},
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()["todo"]
    assert data["kind"] == "dispatch"
    assert len(data["tasks"]) == 2
    assert data["tasks"][0]["executor"] == "alice"


@pytest.mark.asyncio
async def test_build_review_spec_api(client):
    resp = await client.post(
        "/api/todos/spec/build-review",
        json={
            "title": "审批任务",
            "summary": "请确认",
            "reviewers": ["mgr1"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()["todo"]
    assert data["kind"] == "review"
    assert data["reviewers"] == ["mgr1"]


@pytest.mark.asyncio
async def test_dispatch_without_executor_cannot_be_approved(client, monkeypatch):
    """未选择执行人的 dispatch 子任务不能直接审批通过。"""
    from app.auth.models import User
    from app.common.exceptions import AppError
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.todos.models import AITodo, TodoDispatchTask
    from app.todos.service import todo_service
    from sqlalchemy import select

    monkeypatch.setattr(outbox_mod.outbox, "enqueue", AsyncMock(return_value="msg-1"))

    async with async_session_factory() as session:
        session.add(User(
            id="mgr-no-exec", username="mgr-no-exec", name="Manager",
            role="ai_engineer", is_active=True, dingtalk_user_id="ding-mgr",
        ))
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-disp-unassigned",
            skill_id="skill-disp-unassigned",
            skill_meta={"name": "Disp Unassigned"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "待分配任务",
                        "reviewers": ["mgr-no-exec"],
                        "tasks": [{"content": "盘点异常库存 SKU"}],
                    }]
                },
            },
        )
        await session.commit()
        request = requests[0]

    async with async_session_factory() as session:
        manager_todo = (
            await session.execute(
                select(AITodo).where(AITodo.request_id == request.id)
            )
        ).scalar_one()
        with pytest.raises(AppError) as exc_info:
            await todo_service.decide(
                session,
                todo_id=manager_todo.id,
                decision="approved",
                decided_by="mgr-no-exec",
            )
        assert exc_info.value.code == "TODO_DISPATCH_EXECUTOR_REQUIRED"

    async with async_session_factory() as session:
        task = (
            await session.execute(
                select(TodoDispatchTask).where(TodoDispatchTask.request_id == request.id)
            )
        ).scalar_one()
        assert task.executor is None
        assert task.status == "awaiting_dispatch"


@pytest.mark.asyncio
async def test_dispatch_with_invalid_prefilled_executor_cannot_be_approved(client, monkeypatch):
    """旧 skill 预填了错误执行人时，审批前也必须拦截。"""
    from app.auth.models import User
    from app.common.exceptions import AppError
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.todos.models import AITodo
    from app.todos.service import todo_service
    from sqlalchemy import select

    monkeypatch.setattr(outbox_mod.outbox, "enqueue", AsyncMock(return_value="msg-1"))

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="mgr-prefilled", username="mgr-prefilled", name="Manager",
                role="ai_engineer", department="EC", state="active", is_active=True,
            ),
            User(
                id="admin-prefilled", username="admin-prefilled", name="Admin",
                role="admin", department="平台", state="active", is_active=True,
            ),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-disp-prefilled-invalid",
            skill_id="skill-disp-prefilled-invalid",
            skill_meta={"name": "Disp Prefilled Invalid", "department": "EC"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "旧默认执行人应拦截",
                        "reviewers": ["mgr-prefilled"],
                        "tasks": [{"executor": "admin-prefilled", "content": "处理售后工单"}],
                    }]
                },
            },
        )
        await session.commit()
        request = requests[0]

    async with async_session_factory() as session:
        manager_todo = (
            await session.execute(select(AITodo).where(AITodo.request_id == request.id))
        ).scalar_one()
        with pytest.raises(AppError) as exc_info:
            await todo_service.decide(
                session,
                todo_id=manager_todo.id,
                decision="approved",
                decided_by="mgr-prefilled",
            )
        assert exc_info.value.code == "TODO_DISPATCH_EXECUTOR_REQUIRED"
        invalid = exc_info.value.detail.get("invalid_executors") or []
        assert invalid
        assert invalid[0]["executor"] == "admin-prefilled"


@pytest.mark.asyncio
async def test_dispatch_task_reassigns_immediately_after_approval(client, monkeypatch):
    """审批通过后的派发任务改派新执行人，应立即重新派发。"""
    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.todos.models import AITodo, TodoDispatchTask
    from app.todos.service import todo_service
    from sqlalchemy import select

    enqueued: list[str] = []

    async def fake_enqueue(message_type, recipient, payload, priority=5, **kwargs):
        enqueued.append(recipient)
        return f"msg-{len(enqueued)}"

    monkeypatch.setattr(outbox_mod.outbox, "enqueue", fake_enqueue)

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="mgr-assign", username="mgr-assign", name="Manager",
                role="ai_engineer", is_active=True, dingtalk_user_id="ding-mgr-assign",
            ),
            User(
                id="exec-assign", username="exec-assign", name="Exec",
                role="operator", department=None, is_active=True, dingtalk_user_id="ding-exec-assign",
            ),
            User(
                id="exec-assign-2", username="exec-assign-2", name="Exec2",
                role="operator", department=None, is_active=True, dingtalk_user_id="ding-exec-assign-2",
            ),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-disp-assign",
            skill_id="skill-disp-assign",
            skill_meta={"name": "Disp Assign"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "待管理者分配",
                        "reviewers": ["mgr-assign"],
                        "tasks": [{"executor": "exec-assign", "content": "处理售后工单"}],
                    }]
                },
            },
        )
        await session.commit()
        request = requests[0]

    async with async_session_factory() as session:
        manager_todo = (
            await session.execute(select(AITodo).where(AITodo.request_id == request.id))
        ).scalar_one()
        await todo_service.decide(
            session,
            todo_id=manager_todo.id,
            decision="approved",
            decided_by="mgr-assign",
        )
        await session.commit()

    async with async_session_factory() as session:
        task = (
            await session.execute(select(TodoDispatchTask).where(TodoDispatchTask.request_id == request.id))
        ).scalar_one()
        result = await todo_service.assign_dispatch_task(
            session,
            task_id=task.id,
            executor_id="exec-assign-2",
            actor_id="mgr-assign",
        )
        await session.commit()
        assert result["executor"] == "exec-assign-2"
        assert result["status"] == "sent"

    assert "ding-exec-assign" in enqueued
    assert "ding-exec-assign-2" in enqueued


@pytest.mark.asyncio
async def test_list_dispatch_assignable_users_only_returns_active_same_department_users(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="mgr-dept", username="mgr-dept", name="Manager",
                role="ai_engineer", department="EC", state="active", is_active=True,
            ),
            User(
                id="exec-ec", username="exec-ec", name="ExecEc",
                role="operator", department="EC", state="active", is_active=True,
            ),
            User(
                id="exec-pending", username="exec-pending", name="ExecPending",
                role="operator", department="EC", state="pending", is_active=True,
            ),
            User(
                id="exec-retail", username="exec-retail", name="ExecRetail",
                role="operator", department="Retail", state="active", is_active=True,
            ),
            User(
                id="exec-disabled", username="exec-disabled", name="ExecDisabled",
                role="operator", department="EC", state="disabled", is_active=False,
            ),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-disp-assignable",
            skill_id="skill-disp-assignable",
            skill_meta={"name": "Disp Assignable", "department": "EC"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "指定本部门执行人",
                        "reviewers": ["mgr-dept"],
                        "tasks": [{"content": "处理链接下滑"}],
                    }]
                },
            },
        )
        await session.commit()

        users = await todo_service.list_dispatch_assignable_users(
            session,
            current_user=await session.get(User, "mgr-dept"),
            request_id=requests[0].id,
        )

    user_ids = {item["id"] for item in users}
    assert user_ids == {"mgr-dept", "exec-ec"}


@pytest.mark.asyncio
async def test_list_dispatch_assignable_users_allows_same_department_viewer(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.skills.core.models import Skill
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="mgr-visible", username="mgr-visible", name="Manager",
                role="operator", department="EC", state="active", is_active=True,
            ),
            User(
                id="viewer-visible", username="viewer-visible", name="Viewer",
                role="operator", department="EC", state="active", is_active=True,
            ),
            User(
                id="exec-visible", username="exec-visible", name="Executor",
                role="operator", department="EC", state="active", is_active=True,
            ),
            Skill(
                id="skill-disp-visible",
                name="Disp Visible",
                department="EC",
            ),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-disp-visible",
            skill_id="skill-disp-visible",
            skill_meta={"name": "Disp Visible", "department": "EC"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "同部门查看者也能选执行人",
                        "reviewers": ["mgr-visible"],
                        "tasks": [{"content": "处理站点告警"}],
                    }]
                },
            },
        )
        await session.commit()

        users = await todo_service.list_dispatch_assignable_users(
            session,
            current_user=await session.get(User, "viewer-visible"),
            request_id=requests[0].id,
        )

    user_ids = {item["id"] for item in users}
    assert user_ids == {"mgr-visible", "viewer-visible", "exec-visible"}


@pytest.mark.asyncio
async def test_list_dispatch_assignable_users_includes_child_org_members(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.org.models import OrgUnit, UserOrgMembership
    from app.skills.core.models import Skill
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        session.add_all([
            OrgUnit(id="disp-root", name="Root", type="company", path="/disp-root"),
            OrgUnit(
                id="disp-ec",
                name="EC",
                type="department",
                parent_id="disp-root",
                path="/disp-root/disp-ec",
            ),
            OrgUnit(
                id="disp-ec-content",
                name="EC内容组",
                type="department",
                parent_id="disp-ec",
                path="/disp-root/disp-ec/disp-ec-content",
            ),
            User(
                id="mgr-parent-org", username="mgr-parent-org", name="Manager",
                role="observer", department="EC", state="active", is_active=True,
            ),
            User(
                id="exec-child-org", username="exec-child-org", name="Executor",
                role="operator", department="EC内容组", state="active", is_active=True,
            ),
            User(
                id="exec-other-org", username="exec-other-org", name="Other",
                role="operator", department="Other", state="active", is_active=True,
            ),
            UserOrgMembership(
                user_id="mgr-parent-org",
                org_unit_id="disp-ec",
                membership_type="primary",
                is_manager=True,
            ),
            UserOrgMembership(
                user_id="exec-child-org",
                org_unit_id="disp-ec-content",
                membership_type="primary",
            ),
            Skill(
                id="skill-disp-child-org",
                name="Disp Child Org",
                department="EC",
                org_unit_id="disp-ec",
            ),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-disp-child-org",
            skill_id="skill-disp-child-org",
            skill_meta={"name": "Disp Child Org", "department": "EC"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "父部门派发给子部门成员",
                        "reviewers": ["mgr-parent-org"],
                        "tasks": [{"content": "处理站点告警"}],
                    }]
                },
            },
        )
        await session.commit()

        users = await todo_service.list_dispatch_assignable_users(
            session,
            current_user=await session.get(User, "mgr-parent-org"),
            request_id=requests[0].id,
        )

    user_ids = {item["id"] for item in users}
    assert {"mgr-parent-org", "exec-child-org"} <= user_ids
    assert "exec-other-org" not in user_ids


@pytest.mark.asyncio
async def test_list_dispatch_assignable_users_allows_global_admin_any_department(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.skills.core.models import Skill
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="admin-global", username="admin-global", name="Admin",
                role="admin", department="平台", state="active", is_active=True,
            ),
            User(
                id="exec-ec-global", username="exec-ec-global", name="ExecutorEC",
                role="operator", department="EC", state="active", is_active=True,
            ),
            User(
                id="exec-retail-global", username="exec-retail-global", name="ExecutorRetail",
                role="operator", department="Retail", state="active", is_active=True,
            ),
            Skill(
                id="skill-disp-global",
                name="Disp Global",
                department="EC",
            ),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-disp-global",
            skill_id="skill-disp-global",
            skill_meta={"name": "Disp Global", "department": "EC"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "管理员可跨部门选执行人",
                        "reviewers": ["admin-global"],
                        "tasks": [{"content": "处理跨部门告警"}],
                    }]
                },
            },
        )
        await session.commit()

        users = await todo_service.list_dispatch_assignable_users(
            session,
            current_user=await session.get(User, "admin-global"),
            request_id=requests[0].id,
        )

    user_ids = {item["id"] for item in users}
    assert {"admin-global", "exec-ec-global", "exec-retail-global"} <= user_ids


@pytest.mark.asyncio
async def test_update_todo_draft_allows_global_admin_cross_department_executor(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.models import AITodo, TodoDispatchTask
    from app.todos.service import todo_service
    from sqlalchemy import select

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="admin-draft-global", username="admin-draft-global", name="AdminDraft",
                role="admin", department="平台", state="active", is_active=True,
            ),
            User(
                id="exec-retail-draft", username="exec-retail-draft", name="ExecutorRetailDraft",
                role="operator", department="Retail", state="active", is_active=True,
            ),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-disp-admin-draft",
            skill_id="skill-disp-admin-draft",
            skill_meta={"name": "Disp Admin Draft", "department": "EC"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "管理员可跨部门确认执行人",
                        "reviewers": ["admin-draft-global"],
                        "tasks": [{"content": "处理跨部门告警"}],
                    }]
                },
            },
        )
        await session.commit()

        manager_todo = (
            await session.execute(select(AITodo).where(AITodo.request_id == requests[0].id))
        ).scalar_one()
        task = (
            await session.execute(select(TodoDispatchTask).where(TodoDispatchTask.request_id == requests[0].id))
        ).scalar_one()

        result = await todo_service.update_todo_draft(
            session,
            todo_id=manager_todo.id,
            actor=await session.get(User, "admin-draft-global"),
            dispatch_tasks=[{"id": task.id, "executor": "exec-retail-draft"}],
        )
        await session.commit()

    assert result["dispatch_tasks"][0]["executor"] == "exec-retail-draft"


@pytest.mark.asyncio
async def test_dispatch_approval_allows_global_admin_cross_department_executor(client, monkeypatch):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.todos.models import AITodo, TodoDispatchTask
    from app.todos.service import todo_service
    from sqlalchemy import select

    monkeypatch.setattr(outbox_mod.outbox, "enqueue", AsyncMock(return_value="msg-1"))

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="admin-approve-global", username="admin-approve-global", name="AdminApprove",
                role="admin", department="平台", state="active", is_active=True,
            ),
            User(
                id="exec-retail-approve", username="exec-retail-approve", name="ExecutorRetailApprove",
                role="operator", department="Retail", state="active", is_active=True,
                dingtalk_user_id="ding-exec-retail-approve",
            ),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-disp-admin-approve",
            skill_id="skill-disp-admin-approve",
            skill_meta={"name": "Disp Admin Approve", "department": "EC"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "管理员可跨部门审批派发",
                        "reviewers": ["admin-approve-global"],
                        "tasks": [{"content": "处理跨部门告警"}],
                    }]
                },
            },
        )
        await session.commit()

        manager_todo = (
            await session.execute(select(AITodo).where(AITodo.request_id == requests[0].id))
        ).scalar_one()
        task = (
            await session.execute(select(TodoDispatchTask).where(TodoDispatchTask.request_id == requests[0].id))
        ).scalar_one()
        task.executor = "exec-retail-approve"
        await session.commit()

        result = await todo_service.decide(
            session,
            todo_id=manager_todo.id,
            decision="approved",
            decided_by="admin-approve-global",
        )
        await session.commit()

    assert result["request"]["aggregate_decision"] == "approved"
    assert result["dispatch_tasks"][0]["executor"] == "exec-retail-approve"
    assert result["dispatch_tasks"][0]["status"] == "sent"


@pytest.mark.asyncio
async def test_assign_dispatch_task_rejects_cross_department_executor(client, monkeypatch):
    """派发执行人必须是本部门 active 用户。"""
    from app.auth.models import User
    from app.common.exceptions import AppError
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.todos.models import TodoDispatchTask
    from app.todos.service import todo_service
    from sqlalchemy import select

    monkeypatch.setattr(outbox_mod.outbox, "enqueue", AsyncMock(return_value="msg-1"))

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="mgr-cross", username="mgr-cross", name="Manager",
                role="ai_engineer", department="EC", state="active", is_active=True,
            ),
            User(
                id="exec-cross", username="exec-cross", name="ExecCross",
                role="operator", department="Retail", state="active", is_active=True,
            ),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-disp-cross",
            skill_id="skill-disp-cross",
            skill_meta={"name": "Disp Cross", "department": "EC"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "跨部门指派应拦截",
                        "reviewers": ["mgr-cross"],
                        "tasks": [{"content": "处理售后异常"}],
                    }]
                },
            },
        )
        await session.commit()
        request = requests[0]

    async with async_session_factory() as session:
        task = (
            await session.execute(select(TodoDispatchTask).where(TodoDispatchTask.request_id == request.id))
        ).scalar_one()
        with pytest.raises(AppError) as exc_info:
            await todo_service.assign_dispatch_task(
                session,
                task_id=task.id,
                executor_id="exec-cross",
                actor_id="mgr-cross",
            )

    assert exc_info.value.code == "AUTH_DEPARTMENT_DENIED"


@pytest.mark.asyncio
async def test_assign_dispatch_task_allows_child_org_executor(client, monkeypatch):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.org.models import OrgUnit, UserOrgMembership
    from app.skills.core.models import Skill
    from app.todos.models import TodoDispatchTask
    from app.todos.service import todo_service
    from sqlalchemy import select

    monkeypatch.setattr(outbox_mod.outbox, "enqueue", AsyncMock(return_value="msg-1"))

    async with async_session_factory() as session:
        session.add_all([
            OrgUnit(id="assign-root", name="Root", type="company", path="/assign-root"),
            OrgUnit(
                id="assign-ec",
                name="EC",
                type="department",
                parent_id="assign-root",
                path="/assign-root/assign-ec",
            ),
            OrgUnit(
                id="assign-ec-content",
                name="EC内容组",
                type="department",
                parent_id="assign-ec",
                path="/assign-root/assign-ec/assign-ec-content",
            ),
            User(
                id="mgr-child-assign", username="mgr-child-assign", name="Manager",
                role="observer", department="EC", state="active", is_active=True,
            ),
            User(
                id="exec-child-assign", username="exec-child-assign", name="Executor",
                role="operator", department="EC内容组", state="active", is_active=True,
                dingtalk_user_id="ding-exec-child-assign",
            ),
            UserOrgMembership(
                user_id="mgr-child-assign",
                org_unit_id="assign-ec",
                membership_type="primary",
                is_manager=True,
            ),
            UserOrgMembership(
                user_id="exec-child-assign",
                org_unit_id="assign-ec-content",
                membership_type="primary",
            ),
            Skill(
                id="skill-child-assign",
                name="Child Assign",
                department="EC",
                org_unit_id="assign-ec",
            ),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-child-assign",
            skill_id="skill-child-assign",
            skill_meta={"name": "Child Assign", "department": "EC"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "父部门可派发子部门执行人",
                        "reviewers": ["mgr-child-assign"],
                        "tasks": [{"content": "处理站点告警"}],
                    }]
                },
            },
        )
        await session.commit()
        request = requests[0]

    async with async_session_factory() as session:
        task = (
            await session.execute(select(TodoDispatchTask).where(TodoDispatchTask.request_id == request.id))
        ).scalar_one()
        result = await todo_service.assign_dispatch_task(
            session,
            task_id=task.id,
            executor_id="exec-child-assign",
            actor_id="mgr-child-assign",
        )

    assert result["executor"] == "exec-child-assign"


@pytest.mark.asyncio
async def test_assign_dispatch_task_allows_global_admin_cross_department_executor(client, monkeypatch):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.todos.models import TodoDispatchTask
    from app.todos.service import todo_service
    from sqlalchemy import select

    monkeypatch.setattr(outbox_mod.outbox, "enqueue", AsyncMock(return_value="msg-1"))

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="admin-cross-ok", username="admin-cross-ok", name="Admin",
                role="admin", department="平台", state="active", is_active=True,
            ),
            User(
                id="exec-cross-ok", username="exec-cross-ok", name="ExecCross",
                role="operator", department="Retail", state="active", is_active=True,
            ),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-disp-cross-ok",
            skill_id="skill-disp-cross-ok",
            skill_meta={"name": "Disp Cross OK", "department": "EC"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "管理员可跨部门指派",
                        "reviewers": ["admin-cross-ok"],
                        "tasks": [{"content": "处理跨部门工单"}],
                    }]
                },
            },
        )
        await session.commit()
        request = requests[0]

    async with async_session_factory() as session:
        task = (
            await session.execute(select(TodoDispatchTask).where(TodoDispatchTask.request_id == request.id))
        ).scalar_one()
        result = await todo_service.assign_dispatch_task(
            session,
            task_id=task.id,
            executor_id="exec-cross-ok",
            actor_id="admin-cross-ok",
        )

    assert result["executor"] == "exec-cross-ok"


@pytest.mark.asyncio
async def test_dispatch_task_status_flow(client, monkeypatch):
    """执行人可以 sent -> in_progress -> blocked -> in_progress -> done。"""
    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.todos.models import AITodo, TodoDispatchTask
    from app.todos.service import todo_service
    from sqlalchemy import select

    monkeypatch.setattr(outbox_mod.outbox, "enqueue", AsyncMock(return_value="msg-1"))

    async with async_session_factory() as session:
        session.add_all([
            User(id="mgr-flow", username="mgr-flow", name="Mgr", role="ai_engineer",
                 is_active=True, dingtalk_user_id="ding-mgr-flow"),
            User(id="exec-flow", username="exec-flow", name="Exec", role="operator",
                 is_active=True, dingtalk_user_id="ding-exec-flow"),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-disp-flow",
            skill_id="skill-disp-flow",
            skill_meta={"name": "Disp Flow"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "dispatch",
                        "title": "执行状态流转",
                        "reviewers": ["mgr-flow"],
                        "tasks": [{"executor": "exec-flow", "content": "更新落地页"}],
                    }]
                },
            },
        )
        await session.commit()
        request = requests[0]

    async with async_session_factory() as session:
        manager_todo = (
            await session.execute(select(AITodo).where(AITodo.request_id == request.id))
        ).scalar_one()
        await todo_service.decide(
            session,
            todo_id=manager_todo.id,
            decision="approved",
            decided_by="mgr-flow",
        )
        await session.commit()

    async with async_session_factory() as session:
        task = (
            await session.execute(select(TodoDispatchTask).where(TodoDispatchTask.request_id == request.id))
        ).scalar_one()
        assert task.status == "sent"

        result = await todo_service.update_dispatch_task_status(
            session, task_id=task.id, actor_id="exec-flow", status="in_progress"
        )
        assert result["status"] == "in_progress"
        result = await todo_service.update_dispatch_task_status(
            session, task_id=task.id, actor_id="exec-flow", status="blocked"
        )
        assert result["status"] == "blocked"
        result = await todo_service.update_dispatch_task_status(
            session, task_id=task.id, actor_id="exec-flow", status="in_progress"
        )
        assert result["status"] == "in_progress"
        result = await todo_service.ack_dispatch_task(
            session, task_id=task.id, actor_id="exec-flow"
        )
        await session.commit()
        assert result["status"] == "done"


@pytest.mark.asyncio
async def test_callback_to_openclaw_invoked_when_callback_payload_set(client, monkeypatch):
    """spec 带 callback → 决策完成后调用 bridge.bridge_op('notify_decision', ...)。"""
    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.models import AITodo, DecisionRequest
    from app.todos.service import todo_service
    from sqlalchemy import select

    bridge_calls = []

    fake_conn = AsyncMock()
    fake_conn.bridge_op.side_effect = lambda op, payload, timeout=None: bridge_calls.append({"op": op, "payload": payload}) or {"ok": True}

    from app.aiclaw import bridge_registry as br_mod

    monkeypatch.setattr(br_mod.bridge_registry, "get", lambda instance_id: fake_conn)

    async with async_session_factory() as session:
        session.add(User(id="cbrev", username="cbrev", name="CB", role="ai_engineer", is_active=True))
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-cb-spec-1",
            skill_id="skill-cb",
            skill_meta={"name": "CB"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "todos": [{
                        "kind": "review",
                        "title": "需要审批 + 回调",
                        "reviewers": ["cbrev"],
                        "callback": {
                            "instance_id": "demo-prod",
                            "skill_id": "skill-cb",
                            "run_id": "aiclaw-run-xyz",
                            "next_step": "step_3",
                        },
                    }]
                },
            },
        )
        await session.commit()
    request = requests[0]
    assert request.callback_status == "pending"

    async with async_session_factory() as session:
        todo = (
            await session.execute(
                select(AITodo).where(AITodo.request_id == request.id)
            )
        ).scalar_one()
        await todo_service.decide(
            session,
            todo_id=todo.id,
            decision="approved",
            decided_by="cbrev",
        )
        await session.commit()

    assert bridge_calls, "bridge.bridge_op should have been called"
    call = bridge_calls[0]
    assert call["op"] == "notify_decision"
    assert call["payload"]["skill_id"] == "skill-cb"
    assert call["payload"]["run_id"] == "aiclaw-run-xyz"
    assert call["payload"]["next_step"] == "step_3"
    assert call["payload"]["decision"] == "approved"

    async with async_session_factory() as session:
        request = await session.get(DecisionRequest, request.id)
        assert request.callback_status == "sent"
        assert request.callback_completed_at is not None


@pytest.mark.asyncio
async def test_invalid_spec_falls_back_to_legacy(client):
    """output.todos 缺失 → 回退到旧 frontmatter 路径，行为与改造前一致。"""
    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        session.add(User(id="lgcy", username="lgcy", name="L", role="operator", is_active=True))
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-legacy-1",
            skill_id="skill-legacy",
            skill_meta={"name": "Legacy", "approval_level": 1, "reviewer": ["lgcy"]},
            decision={"input_snapshot": {}, "output_result": {"summary": "请审批"}, "suggested_action": None},
        )
        await session.commit()

    assert len(requests) == 1
    assert requests[0].kind == "review"
    assert requests[0].source_type.startswith("skill_execution_l")


@pytest.mark.asyncio
async def test_product360_idle_poll_with_empty_todos_skips_legacy(client):
    """商品360空轮询明确返回 todos: [] 时，不应被 legacy 回退生成审批待办。"""
    from sqlalchemy import select

    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.models import DecisionRequest
    from app.todos.service import todo_service

    run_id = "run-product360-idle-empty-todos"
    skill_id = "tmall-product360-auto-collector-v1"

    async with async_session_factory() as session:
        session.add(
            User(
                id="product360-reviewer",
                username="product360-reviewer",
                name="P360",
                role="operator",
                is_active=True,
            )
        )
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id=run_id,
            skill_id=skill_id,
            skill_meta={
                "name": "商品360数据自动化获取采集器",
                "approval_level": 0,
                "reviewer": ["product360-reviewer"],
            },
            decision={
                "input_snapshot": {"mode": "poll_pending"},
                "output_result": {
                    "schema": "product360_auto_collection_output_v1",
                    "summary": {
                        "status": "idle",
                        "validation_ok": False,
                        "source_type": "none",
                        "message": "no pending 商品360 collection request",
                    },
                    "reports": [{"title": "商品360数据自动化获取", "summary": "没有待处理采集请求"}],
                    "todos": [],
                },
                "suggested_action": None,
            },
        )
        await session.commit()

        existing = (
            await session.execute(
                select(DecisionRequest).where(
                    DecisionRequest.run_id == run_id,
                    DecisionRequest.skill_id == skill_id,
                )
            )
        ).scalars().all()

    assert requests == []
    assert existing == []


@pytest.mark.asyncio
async def test_failure_output_skips_legacy_todo_fallback(client):
    """失败输出没有 todos 时不能回退生成“需要审批”的 legacy 待办。"""
    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        session.add(User(id="err-owner", username="err-owner", name="E", role="operator", is_active=True))
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-error-legacy",
            skill_id="skill-error",
            skill_meta={"name": "Error Skill", "approval_level": 1, "owner": "err-owner"},
            decision={
                "input_snapshot": {},
                "output_result": {
                    "error": "runtime_execution_failed",
                    "message": "Traceback ...",
                },
            },
        )
        await session.commit()

    assert requests == []
