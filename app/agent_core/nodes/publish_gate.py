"""Node 6: 发布门禁 — 计算 60 分门禁状态，未通过时 interrupt 等业务方处理。"""

from __future__ import annotations

from datetime import datetime, time as dtime

from app.agent_core.checks.numeric_compare import compare as numeric_compare
from app.agent_core.checks.threshold import evaluate_threshold
from app.agent_core.checks.time_window import in_window
from app.workbench.task_contract import build_gate_status, merge_review_state


def _run_dynamic_checks(contract: dict, preview: dict, gate_items: list) -> list[dict]:
    """根据 contract 动态调用 checks/ 函数节点，返回额外检查项。"""
    extras = []
    risks = contract.get("risks") or {}
    trigger = contract.get("trigger") or {}
    output = contract.get("output") or {}

    # 1) 风险等级阈值检查（threshold.evaluate_threshold）
    risk_score_map = {"R1": 1.0, "R2": 2.5, "R3": 4.0}
    risk_score = risk_score_map.get(risks.get("level", "R1"), 1.0)
    severity = evaluate_threshold(risk_score, warning=2.0, critical=3.5)
    extras.append({
        "key": "risk_threshold",
        "label": f"风险等级阈值（{risks.get('level', 'R1')}）",
        "passed": severity != "critical",
        "detail": f"风险评分 {risk_score} → {severity}（critical 时禁止自动发布）",
    })

    # 2) cron 定时窗口校验（time_window.in_window）
    if trigger.get("type") == "cron":
        expr = (trigger.get("expression") or "").split()
        if len(expr) >= 2:
            try:
                minute = int(expr[0])
                hour = int(expr[1])
                # 只允许在 06:00-23:00 之间执行
                ok = in_window(
                    datetime(2026, 1, 1, hour, minute),
                    dtime(6, 0),
                    dtime(23, 0),
                )
                extras.append({
                    "key": "trigger_window",
                    "label": "触发时间窗口合规",
                    "passed": ok,
                    "detail": f"cron 时间 {hour:02d}:{minute:02d} {'在' if ok else '不在'} 06:00-23:00 允许窗口",
                })
            except (ValueError, IndexError):
                extras.append({
                    "key": "trigger_window",
                    "label": "触发时间窗口合规",
                    "passed": False,
                    "detail": f"cron 表达式 '{trigger.get('expression')}' 解析失败",
                })

    # 3) 输出指标数量检查（numeric_compare.compare）
    schema = output.get("schema") or {}
    metrics = schema.get("metrics") if isinstance(schema, dict) else None
    if isinstance(metrics, list):
        ok = numeric_compare(len(metrics), 1, ">=")
        extras.append({
            "key": "output_metrics_count",
            "label": "输出指标数量足够",
            "passed": ok,
            "detail": f"输出 schema.metrics 共 {len(metrics)} 个（要求 ≥ 1）",
        })

    return extras


async def publish_gate_node(state: dict) -> dict:
    next_state = dict(state)
    contract = next_state.get("contract") or {}
    review_state = merge_review_state(contract, next_state.get("review_state") or {})
    preview = next_state.get("preview_result") or {}
    gate = build_gate_status(contract, review_state, preview)

    # D5：把动态 checks 结果合并进 gate items
    dynamic = _run_dynamic_checks(contract, preview, gate.get("items") or [])
    if dynamic:
        gate["items"] = (gate.get("items") or []) + dynamic
        # can_publish 必须所有动态检查都通过
        gate["can_publish"] = gate.get("can_publish") and all(c["passed"] for c in dynamic)

    next_state["gate"] = gate
    next_state["review_state"] = review_state

    pending_checkpoints = [
        key for key in ("target", "preview", "responsibility")
        if (review_state.get(key) or {}).get("required") and not (review_state.get(key) or {}).get("approved")
    ]
    if not pending_checkpoints:
        return next_state

    from langgraph.types import interrupt

    payload = interrupt({
        "checkpoint": "publish_gate",
        "pending": pending_checkpoints,
        "gate_items": gate.get("items"),
        "reason": "60 分门禁未通过，等业务方完成必感知点确认",
    })

    if isinstance(payload, dict):
        for key in pending_checkpoints:
            if key in payload and isinstance(payload[key], dict):
                item = review_state.get(key) or {}
                item["approved"] = bool(payload[key].get("approved", True))
                item["decision"] = "approved" if item["approved"] else "rejected"
                if payload[key].get("detail"):
                    merged = dict(item.get("detail") or {})
                    merged.update(payload[key]["detail"])
                    item["detail"] = merged
                review_state[key] = item
        gate = build_gate_status(contract, review_state, preview)
        dynamic = _run_dynamic_checks(contract, preview, gate.get("items") or [])
        if dynamic:
            gate["items"] = (gate.get("items") or []) + dynamic
            gate["can_publish"] = gate.get("can_publish") and all(c["passed"] for c in dynamic)
        next_state["gate"] = gate
        next_state["review_state"] = review_state

    return next_state
