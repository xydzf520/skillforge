"""Signed DingTalk-only detail links.

The URL token is scoped to a single todo/dispatch task and the bound DingTalk
user. The public renderer still checks the request comes from DingTalk; normal
browser opens are redirected back to login.
"""

from __future__ import annotations

from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

_LINK_MAX_AGE_SECONDS = 7 * 24 * 3600
_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="dingtalk-open-detail")


def is_dingtalk_request(user_agent: str | None) -> bool:
    ua = (user_agent or "").lower()
    return "dingtalk" in ua or "aliapp(dingtalk" in ua


def create_todo_view_token(*, todo_id: int, assignee_user_id: str, dingtalk_user_id: str) -> str:
    return _signer.dumps({
        "kind": "todo",
        "todo_id": int(todo_id),
        "user_id": assignee_user_id,
        "dingtalk_user_id": dingtalk_user_id,
    })


def create_dispatch_task_view_token(*, task_id: int, executor_user_id: str, dingtalk_user_id: str) -> str:
    return _signer.dumps({
        "kind": "dispatch",
        "task_id": int(task_id),
        "user_id": executor_user_id,
        "dingtalk_user_id": dingtalk_user_id,
    })


def verify_view_token(token: str) -> dict[str, Any] | None:
    try:
        payload = _signer.loads(token, max_age=_LINK_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return payload if isinstance(payload, dict) else None
