"""Render JSON webhook preview payload."""

from __future__ import annotations

import json


def render_json_webhook(payload: dict) -> dict:
    body = payload.get("payload") or {}
    return {
        "payload": body,
        "rendered": json.dumps(body, ensure_ascii=False, indent=2),
    }
