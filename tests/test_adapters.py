from app.adapters.dingtalk_card.render import render_card
from app.adapters.email.render import render_email
from app.adapters.json_webhook.render import render_json_webhook
from app.adapters.slack.render import render_slack


def test_render_dingtalk_card():
    payload = render_card({
        "title": "日报",
        "markdown": "内容",
        "actions": ["查看详情"],
    })
    assert payload["title"] == "日报"
    assert "内容" in payload["rendered"]


def test_render_email():
    payload = render_email({
        "subject": "日报邮件",
        "body": "这里是正文",
    })
    assert payload["subject"] == "日报邮件"
    assert "正文" in payload["rendered"]


def test_render_slack():
    payload = render_slack({
        "title": "Slack 通知",
        "blocks": ["摘要", "指标"],
    })
    assert payload["title"] == "Slack 通知"
    assert "- 摘要" in payload["rendered"]


def test_render_json_webhook():
    payload = render_json_webhook({
        "payload": {"ok": True, "count": 3},
    })
    assert payload["payload"]["ok"] is True
    assert '"count": 3' in payload["rendered"]
