#!/usr/bin/env python3
"""Backfill tmall link-decline operator analysis from a collector DecisionLog."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import async_session_factory, init_db
from app.execution.execution_service import execution_service
from app.execution.models import DecisionLog, ExecutionRun


async def _inspect(run_id: str, decision_log_id: int | None) -> dict:
    async with async_session_factory() as session:
        stmt = select(DecisionLog).where(DecisionLog.skill_id == execution_service.COLLECTOR_SKILL_ID)
        if decision_log_id is not None:
            stmt = stmt.where(DecisionLog.id == decision_log_id)
        else:
            stmt = stmt.where(DecisionLog.run_id == run_id)
        decision = (await session.execute(stmt.order_by(DecisionLog.id.desc()).limit(1))).scalar_one_or_none()
        child = (
            await session.execute(
                select(ExecutionRun.id, ExecutionRun.status)
                .where(ExecutionRun.skill_id == execution_service.OPERATOR_SKILL_ID)
                .where(ExecutionRun.parent_run_id == run_id)
                .where(ExecutionRun.status.in_(["running", "completed"]))
                .order_by(ExecutionRun.started_at.desc())
                .limit(1)
            )
        ).first()

    output = decision.output_result if decision else None
    return {
        "collector_run_id": run_id,
        "collector_decision_log_id": decision.id if decision else decision_log_id,
        "decision_log_found": decision is not None,
        "collection_schema": output.get("collection_schema") if isinstance(output, dict) else None,
        "output_top_keys": list(output.keys())[:20] if isinstance(output, dict) else [],
        "operator_child_run_id": child.id if child else None,
        "operator_child_status": child.status if child else None,
    }


async def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill tmall-link-decline-operator-v1 from a completed collector run."
    )
    parser.add_argument("--run-id", required=True, help="Collector ExecutionRun id, e.g. rm-009c953e")
    parser.add_argument("--decision-log-id", type=int, default=None, help="Collector DecisionLog id")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Only inspect source data and duplicate state")
    mode.add_argument("--apply", action="store_true", help="Enqueue and immediately process the operator trigger")
    args = parser.parse_args()

    await init_db()
    summary = await _inspect(args.run_id, args.decision_log_id)
    if args.dry_run:
        print(json.dumps({"status": "dry_run", **summary}, ensure_ascii=False, indent=2))
        return 0

    if not summary["decision_log_found"]:
        print(json.dumps({"status": "error", "reason": "decision_log_not_found", **summary}, ensure_ascii=False, indent=2))
        return 1
    if summary["operator_child_run_id"]:
        print(json.dumps({"status": "skipped", "reason": "operator_child_exists", **summary}, ensure_ascii=False, indent=2))
        return 0

    result = await execution_service.enqueue_link_decline_operator_trigger_if_needed(
        collector_skill_id=execution_service.COLLECTOR_SKILL_ID,
        collector_run_id=args.run_id,
        collector_decision_log_id=summary["collector_decision_log_id"],
        collector_output={
            "collection_schema": summary["collection_schema"],
        },
        created_by="manual:backfill_link_decline_operator",
        process_now=False,
        operator_triggered_by="manual:backfill_link_decline_operator",
    )
    print(json.dumps({"status": "enqueued", **summary, "result": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
