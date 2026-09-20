#!/usr/bin/env python3
"""Run the learning auto-flow reconciler outside the web worker pool."""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.bootstrap.health_checks import verify_required_dependencies
from app.bootstrap.runtime_init import load_prompt_registry
from app.common.cache import close_cache, init_cache
from app.database import close_db, init_db
from app.learning.worker import learning_auto_flow_loop


async def main() -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    await verify_required_dependencies()
    await init_db()
    await init_cache()
    load_prompt_registry()

    task = asyncio.create_task(learning_auto_flow_loop(), name="learning-auto-flow")
    logger.info("learning auto-flow worker started")
    try:
        await stop_event.wait()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await close_cache()
        await close_db()
        logger.info("learning auto-flow worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
