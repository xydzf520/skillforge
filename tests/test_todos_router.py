import pytest


@pytest.mark.asyncio
async def test_todos_list_detail_and_decide(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.todos.service import todo_service

    async with async_session_factory() as session:
        session.add(User(id='admin', username='admin2', name='管理员', role='admin', is_active=True))
        await session.commit()
        requests = await todo_service.create_from_execution(
            session,
            run_id='run-router-001',
            skill_id='skill-router',
            skill_meta={'name': '路由测试', 'approval_level': 1, 'reviewer': ['admin']},
            decision={'input_snapshot': {}, 'output_result': {'summary': '请审批'}, 'suggested_action': None},
        )
        request = requests[0]
        await session.commit()
        todo = (await todo_service.list_todos(session, current_user=await session.get(User, 'admin')))['items'][0]

    list_resp = await client.get('/api/todos/')
    assert list_resp.status_code == 200
    assert list_resp.json()['total'] >= 1

    detail_resp = await client.get(f"/api/todos/{todo['id']}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()['request']['id'] == request.id

    decide_resp = await client.post(
        f"/api/todos/{todo['id']}/decide",
        json={'decision': 'approved', 'reason': 'ok'},
    )
    assert decide_resp.status_code == 200
    assert decide_resp.json()['todo']['status'] == 'approved'

    stats_resp = await client.get('/api/todos/stats')
    assert stats_resp.status_code == 200
    assert 'pending' in stats_resp.json()
