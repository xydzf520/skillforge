from app.adapters.dingtalk_card.render import render_card


def test_dingtalk_daily_card_render():
    payload = render_card({
        "title": "昨日销售日报",
        "markdown": "- 销售额：¥3,540,000\n- 环比：+5.2%",
        "actions": ["查看详情", "我已知道"],
    })
    assert payload["title"] == "昨日销售日报"
    assert "销售额" in payload["rendered"]
    assert len(payload["actions"]) == 2
