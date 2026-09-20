"""Node 5: run preview in sandbox."""

from __future__ import annotations

from app.common.contract_schema import get_preview_input
from app.sandbox.executor import load_fixture, run_preview


async def sandbox_run_node(state: dict) -> dict:
    next_state = dict(state)
    contract = next_state.get("contract") or {}
    fixture = get_preview_input(contract) or load_fixture("bi_daily_sample.json")
    next_state["preview_result"] = run_preview(contract, fixture=fixture)
    return next_state
