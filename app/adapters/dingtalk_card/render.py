"""Render a DingTalk-style preview card."""

from __future__ import annotations


def render_card(payload: dict) -> dict:
    title = payload.get("title") or "预演卡片"
    markdown = payload.get("markdown") or ""
    actions = payload.get("actions") or []
    return {
        "title": title,
        "markdown": markdown,
        "actions": actions,
        "rendered": f"{title}\n\n{markdown}",
    }
