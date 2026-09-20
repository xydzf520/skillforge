"""Node 2: 权限/风险确认 — 用 langgraph interrupt 等待业务方授权。"""

from __future__ import annotations

from app.workbench.task_contract import merge_review_state


async def risk_gate_node(state: dict) -> dict:
    """计算 review_state，若有不可逆 permission 且未授权 → interrupt 暂停等业务方确认。

    interrupt() 会让 LangGraph 抛 GraphInterrupt，由 runtime 捕获并把图状态写到
    checkpointer。前端通过 /api/agent-core/resume 提交 Command(resume=...) 恢复执行。
    """
    next_state = dict(state)
    contract = next_state.get("contract") or {}
    review_state = merge_review_state(contract, next_state.get("review_state") or {})
    next_state["review_state"] = review_state

    permission = review_state.get("permission") or {}
    if not (permission.get("required") and not permission.get("approved")):
        return next_state

    # 触发 interrupt，graph 会停在这里等待业务方
    from langgraph.types import interrupt

    payload = interrupt({
        "checkpoint": "permission",
        "reason": "存在不可逆动作（推送/写入），请业务方授权",
        "permissions": contract.get("permissions") or [],
    })

    # interrupt() 返回业务方 resume 时提交的 detail
    if isinstance(payload, dict):
        permission["approved"] = bool(payload.get("approved", True))
        permission["decision"] = "approved" if permission["approved"] else "rejected"
        if payload.get("detail"):
            merged = dict(permission.get("detail") or {})
            merged.update(payload["detail"])
            permission["detail"] = merged
        review_state["permission"] = permission
        next_state["review_state"] = review_state

    return next_state
