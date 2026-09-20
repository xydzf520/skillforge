"""Render email preview payload."""

from __future__ import annotations


def render_email(payload: dict) -> dict:
    subject = payload.get("subject") or "业务通知"
    body = payload.get("body") or ""
    return {
        "subject": subject,
        "body": body,
        "rendered": f"Subject: {subject}\n\n{body}",
    }
