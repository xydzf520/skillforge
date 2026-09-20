"""Public deployments must configure recipients; an empty name never means everyone."""
from unittest.mock import AsyncMock

import pytest


def test_recipient_settings_have_no_personal_defaults(monkeypatch):
    from app.config import Settings
    for key in ('LINK_DECLINE_OPERATOR_RECIPIENT_QUERY', 'LINK_DECLINE_OPERATOR_DINGTALK_USER_ID'):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.LINK_DECLINE_OPERATOR_RECIPIENT_QUERY == ''
    assert settings.LINK_DECLINE_OPERATOR_DINGTALK_USER_ID == ''


@pytest.mark.asyncio
async def test_unconfigured_notification_stops_before_database_access(monkeypatch):
    from app.execution import execution_service as service
    monkeypatch.setattr(service, 'LINK_DECLINE_OPERATOR_SUCCESS_RECIPIENT_QUERY', '')
    monkeypatch.setattr(service, 'LINK_DECLINE_OPERATOR_SUCCESS_DINGTALK_USER_ID', '')
    def forbidden_db():
        raise AssertionError('An unconfigured notification must not look up arbitrary users')
    monkeypatch.setattr(service, '_sf', forbidden_db)
    result = await service._notify_link_decline_operator_success(
        run_id='example-run', decision_log_id=None, output={}, todo_count=0,
    )
    assert result == {'status': 'skipped', 'reason': 'recipient_not_configured'}


@pytest.mark.asyncio
async def test_unconfigured_inbox_stops_before_database_access(monkeypatch):
    from app.todos import service
    monkeypatch.setattr(service, 'TMALL_LINK_DECLINE_OPERATOR_INBOX_ASSIGNEE_QUERY', '')
    monkeypatch.setattr(service, 'TMALL_LINK_DECLINE_OPERATOR_INBOX_DINGTALK_USER_ID', '')
    db = AsyncMock()
    assert await service.todo_service._resolve_tmall_operator_inbox_assignee(db) is None
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_id_only_configuration_selects_only_the_requested_recipient(client, monkeypatch):
    from sqlalchemy import select
    from app.auth.models import User
    from app.database import async_session_factory
    from app.dingtalk.models import DingTalkOutbox
    from app.execution import execution_service as execution
    from app.todos import service as todos

    monkeypatch.setattr(execution, 'LINK_DECLINE_OPERATOR_SUCCESS_RECIPIENT_QUERY', '')
    monkeypatch.setattr(execution, 'LINK_DECLINE_OPERATOR_SUCCESS_DINGTALK_USER_ID', 'example-target-id')
    monkeypatch.setattr(todos, 'TMALL_LINK_DECLINE_OPERATOR_INBOX_ASSIGNEE_QUERY', '')
    monkeypatch.setattr(todos, 'TMALL_LINK_DECLINE_OPERATOR_INBOX_DINGTALK_USER_ID', 'example-target-id')
    async with async_session_factory() as db:
        db.add_all([
            User(id='example-other', username='example-other', name='', role='operator', state='active', is_active=True, dingtalk_user_id='example-other-id'),
            User(id='example-target', username='example-target', name='Target', role='operator', state='active', is_active=True, dingtalk_user_id='example-target-id'),
        ])
        await db.commit()
        assignee = await todos.todo_service._resolve_tmall_operator_inbox_assignee(db)
        assert assignee.id == 'example-target'
    await execution._notify_link_decline_operator_success(
        run_id='example-id-only', decision_log_id=None, output={'reports': [], 'todos': []}, todo_count=0,
    )
    async with async_session_factory() as db:
        recipients = (await db.execute(select(DingTalkOutbox.recipient_user_id))).scalars().all()
        assert recipients == ['example-target-id']
