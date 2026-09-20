"""钉钉模块测试（mock，不真实发送）"""

import pytest


@pytest.mark.asyncio
async def test_card_templates():
    """测试卡片模板生成"""
    from app.dingtalk.card_templates import build_task_card, build_confirm_card, build_review_card

    card = build_task_card("测试Skill", {"status": "ok"}, "run123")
    assert "title" in card
    assert "markdown" in card

    confirm = build_confirm_card("测试Skill", {"action": "暂停"}, "run456")
    assert "buttons" in confirm
    assert len(confirm["buttons"]) == 3

    review = build_review_card("EC-投放-01", 1, "张三", "params", "调整ROI")
    assert "审核" in review["title"]


@pytest.mark.asyncio
async def test_outbox_enqueue(client):
    """测试消息入队（通过已初始化的app上下文）"""
    from app.dingtalk.outbox import outbox

    msg_id = await outbox.enqueue(
        message_type="work_notice",
        recipient="test_user_id",
        payload={"title": "测试", "markdown": "pytest测试消息"},
        priority=5,
    )
    assert msg_id > 0


@pytest.mark.asyncio
async def test_outbox_stats(client):
    """测试队列统计"""
    from app.dingtalk.outbox import outbox

    stats = await outbox.get_stats()
    assert "pending" in stats
    assert "total" in stats


@pytest.mark.asyncio
async def test_card_callback_decides_todo(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        session.add(User(
            id='ding_actor',
            username='ding_actor',
            name='钉钉审批人',
            role='operator',
            is_active=True,
            dingtalk_user_id='ding-user-001',
        ))
        await session.commit()
        requests = await todo_service.create_from_execution(
            session,
            run_id='run-ding-001',
            skill_id='skill-ding',
            skill_meta={'name': '钉钉测试', 'approval_level': 1, 'reviewer': ['ding_actor']},
            decision={'input_snapshot': {}, 'output_result': {'summary': '请审批'}, 'suggested_action': None},
        )
        request = requests[0]
        await session.commit()
        todo = (await todo_service.list_todos(session, current_user=await session.get(User, 'ding_actor')))['items'][0]

    resp = await client.post(
        '/api/dingtalk/card-callback',
        json={
            'actionId': 'approve',
            'params': {'action': 'approve'},
            'cardPrivateData': {
                'todo_id': str(todo['id']),
                'request_id': request.id,
                'assignee_user_id': 'ding_actor',
                'nonce': 'n1',
            },
            'userId': 'ding-user-001',
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['ok'] is True
    assert body['todo']['status'] == 'approved'
