#!/usr/bin/env python3
"""Backfill post-training model evaluations for pending target Skill todos."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.common.time_utils import now_bjt
from app.database import async_session_factory, init_db
from app.todos.post_training_evaluation import (
    POST_TRAINING_EVALUATED_SKILL_IDS,
    backfill_post_training_evaluations,
)


async def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill inbox todo post-training model evaluations for selected Skills."
    )
    parser.add_argument(
        "--skill-id",
        action="append",
        choices=sorted(POST_TRAINING_EVALUATED_SKILL_IDS),
        help="Target Skill id. May be passed multiple times. Defaults to both supported Skills.",
    )
    parser.add_argument("--limit", type=int, default=5, help="Max pending DecisionRequest rows to scan.")
    parser.add_argument("--since-days", type=int, default=30, help="Only scan todos created in the last N days.")
    parser.add_argument("--request-id", action="append", help="Specific DecisionRequest id to backfill. May be repeated.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing post-training evaluations.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Inspect candidates without writing payload updates.")
    mode.add_argument("--apply", action="store_true", help="Write missing evaluations into DecisionRequest.payload.")
    args = parser.parse_args()

    await init_db()
    skill_ids = set(args.skill_id or POST_TRAINING_EVALUATED_SKILL_IDS)
    since = now_bjt() - timedelta(days=max(1, int(args.since_days or 30)))
    async with async_session_factory() as session:
        if args.dry_run:
            result = await backfill_post_training_evaluations(
                session,
                skill_ids=skill_ids,
                limit=args.limit,
                since=since,
                dry_run=True,
                force=args.force,
                request_ids=args.request_id,
            )
            await session.rollback()
            result["dry_run"] = True
        else:
            result = await backfill_post_training_evaluations(
                session,
                skill_ids=skill_ids,
                limit=args.limit,
                since=since,
                force=args.force,
                request_ids=args.request_id,
            )
            await session.commit()
            result["dry_run"] = False

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
