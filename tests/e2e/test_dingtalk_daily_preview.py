from app.sandbox.executor import run_preview
from app.workbench.task_contract import build_task_contract


def test_dingtalk_daily_preview():
    contract = build_task_contract("每天 18:00 发昨日销售钉钉日报到销售运营群")
    preview = run_preview(contract)
    assert preview["adapter"] == "dingtalk_card"
    assert "昨日销售日报" in preview["rendered_output"]
    assert "title" in preview["card_payload"]
