"""Render slack preview payload."""

from __future__ import annotations


def render_slack(payload: dict) -> dict:
    title = payload.get("title") or "Slack message"
    blocks = payload.get("blocks") or []
    rendered = "\n".join(f"- {item}" for item in blocks)
    return {
        "title": title,
        "blocks": blocks,
        "rendered": f"{title}\n\n{rendered}",
    }
