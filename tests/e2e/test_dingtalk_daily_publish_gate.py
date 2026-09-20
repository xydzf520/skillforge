from app.workbench.task_contract import build_gate_status, build_task_contract, default_review_state


def test_dingtalk_daily_publish_gate():
    contract = build_task_contract("每天 18:00 发昨日销售钉钉日报到销售运营群")
    review_state = default_review_state(contract)
    review_state["target"]["approved"] = True
    review_state["target"]["decision"] = "approved"
    review_state["permission"]["approved"] = True
    review_state["permission"]["decision"] = "approved"
    review_state["preview"]["approved"] = True
    review_state["preview"]["decision"] = "approved"
    review_state["responsibility"]["approved"] = True
    review_state["responsibility"]["decision"] = "approved"
    gate = build_gate_status(contract, review_state, {"success": True})
    assert gate["can_generate_skill"] is True
    assert gate["can_publish"] is True
