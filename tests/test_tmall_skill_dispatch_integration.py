"""天猫链接下滑 Skill output.todos 与平台 dispatch 派发链路集成测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def configured_example_recipient(monkeypatch):
    monkeypatch.setattr("app.todos.service.TMALL_LINK_DECLINE_OPERATOR_INBOX_ASSIGNEE_QUERY", "示例成员甲")
    monkeypatch.setattr("app.todos.service.TMALL_LINK_DECLINE_OPERATOR_INBOX_DINGTALK_USER_ID", "900000000000000001")
    monkeypatch.setattr("app.execution.execution_service.LINK_DECLINE_OPERATOR_SUCCESS_RECIPIENT_QUERY", "示例成员甲")
    monkeypatch.setattr("app.execution.execution_service.LINK_DECLINE_OPERATOR_SUCCESS_DINGTALK_USER_ID", "900000000000000001")


def _load_tmall_skill_main():
    root = Path(__file__).resolve().parents[1]
    main_path = root / "skills-repo" / "tmall-link-decline-operator-v1" / "scripts" / "main.py"
    spec = importlib.util.spec_from_file_location("tmall_decline_skill_main", main_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module.main


@pytest.mark.asyncio
async def test_tmall_skill_dispatch_todo_reaches_inbox_and_fans_out_to_dingtalk(client, monkeypatch):
    """该 Skill 的 dispatch todos 必须可建收件待办，审批通过后推给默认执行人钉钉。"""
    from sqlalchemy import select

    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.todos.models import AITodo, TodoDispatchTask
    from app.todos.service import todo_service

    enqueued: list[dict] = []

    async def fake_enqueue(message_type, recipient, payload, priority=5, **kwargs):
        enqueued.append({
            "type": message_type,
            "recipient": recipient,
            "title": payload.get("title"),
            "related_type": kwargs.get("related_type"),
        })
        return f"msg-{len(enqueued)}"

    monkeypatch.setattr(outbox_mod.outbox, "enqueue", fake_enqueue)

    skill_main = _load_tmall_skill_main()
    output = skill_main({
        "date": "2026-04-22",
        "default_executor": "tmall-exec",
        "生意参谋_商品排行榜": [
            {
                "item_id": "800737120171",
                "title": "测试商品A",
                "visitor_count": 500,
                "visitor_count_prev": 1000,
                "conversion_rate": 0.03,
                "conversion_rate_prev": 0.05,
                "pay_amt": 8000,
                "pay_amt_prev": 15000,
            }
        ],
    })
    assert output["todos"]
    assert all(todo.get("tasks") for todo in output["todos"] if todo.get("kind") == "dispatch")

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="admin",
                username="admin",
                name="管理员",
                role="admin",
                is_active=True,
                dingtalk_user_id="ding-reviewer",
            ),
            User(
                id="example-recipient",
                username="example-recipient",
                name="示例成员甲",
                role="operator",
                department="阿里店群组",
                is_active=True,
                dingtalk_user_id="900000000000000001",
                avatar_url="https://example.test/avatar/xiao.png",
            ),
            User(
                id="dt_exampleShadowOpenidValue0001",
                username="dingtalk_exampleShadowOpenidValue0001",
                name="示例成员",
                role="aibp",
                department="阿里店群组",
                is_active=True,
                dingtalk_user_id="exampleShadowOpenidValue0001",
                avatar_url="https://example.test/avatar/xiao.png",
            ),
            User(
                id="tmall-exec",
                username="tmall-exec",
                name="天猫执行人",
                role="observer",
                department="阿里店群组",
                is_active=True,
                dingtalk_user_id="ding-exec",
            ),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-tmall-dispatch-integration",
            skill_id="tmall-link-decline-operator-v1",
            skill_meta={"name": "天猫链接下滑运营诊断"},
            decision={
                "input_snapshot": {},
                "output_result": output,
                "decision_log_id": None,
            },
        )
        await session.commit()
        assert requests, "Skill output.todos 应创建收件待办"
        request_id = requests[0].id

    async with async_session_factory() as session:
        manager_todo = (
            await session.execute(
                select(AITodo)
                .where(AITodo.request_id == request_id)
                .where(AITodo.assignee == "example-recipient")
            )
        ).scalar_one()
        assert manager_todo.kind == "dispatch"
        shadow_user = await session.get(User, "dt_exampleShadowOpenidValue0001")
        visible = await todo_service.list_todos(
            session,
            current_user=shadow_user,
            run_id="run-tmall-dispatch-integration",
        )
        assert visible["total"] == len(requests)
        assert {item["assignee"] for item in visible["items"]} == {"example-recipient"}
        await todo_service.decide(
            session,
            todo_id=manager_todo.id,
            decision="approved",
            decided_by="example-recipient",
            channel="web",
        )
        await session.commit()

    assert any(
        item["recipient"] == "ding-exec" and item["related_type"] == "dispatch_task"
        for item in enqueued
    )

    async with async_session_factory() as session:
        task = (
            await session.execute(
                select(TodoDispatchTask).where(TodoDispatchTask.request_id == request_id)
            )
        ).scalars().first()
        assert task is not None
        assert task.executor == "tmall-exec"
        assert task.status == "sent"


@pytest.mark.asyncio
async def test_tmall_operator_inbox_todo_skips_when_example_recipient_unresolved(client):
    """该 Skill 不应在示例成员甲无法解析时回退给 admin。"""
    from sqlalchemy import select

    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.models import AITodo
    from app.todos.service import todo_service

    output = {
        "todos": [
            {
                "kind": "dispatch",
                "title": "测试派发",
                "reviewers": ["admin"],
                "tasks": [{"executor": "tmall-exec", "content": "跟进商品"}],
            }
        ]
    }

    async with async_session_factory() as session:
        session.add_all([
            User(
                id="admin",
                username="admin",
                name="管理员",
                role="admin",
                is_active=True,
            ),
            User(
                id="tmall-exec",
                username="tmall-exec",
                name="天猫执行人",
                role="observer",
                is_active=True,
            ),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-tmall-no-xiao",
            skill_id="tmall-link-decline-operator-v1",
            skill_meta={"name": "天猫链接下滑运营诊断"},
            decision={
                "input_snapshot": {},
                "output_result": output,
                "decision_log_id": None,
            },
        )
        await session.commit()

        assert requests == []
        todos = (await session.execute(select(AITodo))).scalars().all()
        assert todos == []


@pytest.mark.asyncio
async def test_tmall_operator_shadow_account_can_default_dispatch_to_self(client, monkeypatch):
    """示例成员 shadow 账号审批时，空 executor 默认派发给示例成员甲正式账号。"""
    from sqlalchemy import select

    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk import outbox as outbox_mod
    from app.org.models import OrgUnit, UserOrgMembership
    from app.todos.models import AITodo, TodoDispatchTask
    from app.todos.service import todo_service

    enqueued: list[dict] = []

    async def fake_enqueue(message_type, recipient, payload, priority=5, **kwargs):
        enqueued.append({
            "type": message_type,
            "recipient": recipient,
            "title": payload.get("title"),
            "related_type": kwargs.get("related_type"),
        })
        return f"msg-{len(enqueued)}"

    monkeypatch.setattr(outbox_mod.outbox, "enqueue", fake_enqueue)

    output = {
        "todos": [
            {
                "kind": "dispatch",
                "title": "测试默认派发给自己",
                "reviewers": ["admin"],
                "tasks": [{"content": "跟进商品 800482527402", "extra": {"item_id": "800482527402"}}],
            }
        ]
    }

    async with async_session_factory() as session:
        session.add_all([
            OrgUnit(
                id="dept-ali",
                name="阿里店群组",
                type="department",
                path="/dept-ali",
                dingtalk_dept_id="dept-ali",
            ),
            User(
                id="example-recipient",
                username="example-recipient",
                name="示例成员甲",
                role="director",
                department="阿里店群组",
                is_active=True,
                dingtalk_user_id="900000000000000001",
                avatar_url="https://example.test/avatar/xiao.png",
            ),
            User(
                id="dt_exampleShadowOpenidValue0001",
                username="dingtalk_exampleShadowOpenidValue0001",
                name="示例成员",
                role="aibp",
                department="阿里店群组",
                is_active=True,
                dingtalk_user_id="exampleShadowOpenidValue0001",
                avatar_url="https://example.test/avatar/xiao.png",
            ),
            User(
                id="ali-member",
                username="ali-member",
                name="阿里组成员",
                role="observer",
                department="阿里店群组",
                is_active=True,
                dingtalk_user_id="ding-ali-member",
            ),
            UserOrgMembership(user_id="example-recipient", org_unit_id="dept-ali", membership_type="primary"),
            UserOrgMembership(user_id="dt_exampleShadowOpenidValue0001", org_unit_id="dept-ali", membership_type="primary"),
            UserOrgMembership(user_id="ali-member", org_unit_id="dept-ali", membership_type="primary"),
        ])
        await session.commit()

        requests = await todo_service.create_from_execution(
            session,
            run_id="run-tmall-shadow-default-self",
            skill_id="tmall-link-decline-operator-v1",
            skill_meta={"name": "天猫链接下滑运营诊断"},
            decision={
                "input_snapshot": {},
                "output_result": output,
                "decision_log_id": None,
            },
        )
        await session.commit()
        assert len(requests) == 1
        request_id = requests[0].id

    async with async_session_factory() as session:
        shadow = await session.get(User, "dt_exampleShadowOpenidValue0001")
        assignable = await todo_service.list_dispatch_assignable_users(
            session,
            current_user=shadow,
            request_id=request_id,
        )
        assert {user["id"] for user in assignable} >= {"example-recipient", "ali-member"}

        todo = (
            await session.execute(
                select(AITodo)
                .where(AITodo.request_id == request_id)
                .where(AITodo.assignee == "example-recipient")
            )
        ).scalar_one()
        detail = await todo_service.get_todo_detail(
            session,
            todo_id=todo.id,
            current_user=shadow,
        )
        assert detail["dispatch_tasks"][0]["default_executor"] == "example-recipient"

        await todo_service.decide(
            session,
            todo_id=todo.id,
            decision="approved",
            decided_by="dt_exampleShadowOpenidValue0001",
            channel="web",
        )
        await session.commit()

    async with async_session_factory() as session:
        task = (
            await session.execute(
                select(TodoDispatchTask).where(TodoDispatchTask.request_id == request_id)
            )
        ).scalar_one()
        assert task.executor == "example-recipient"
        assert task.status == "sent"

    assert any(
        item["recipient"] == "900000000000000001" and item["related_type"] == "dispatch_task"
        for item in enqueued
    )
