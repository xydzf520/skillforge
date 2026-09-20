#!/usr/bin/env python3
from __future__ import annotations
import asyncio, signal, sys
from pathlib import Path
from loguru import logger
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.bootstrap.health_checks import verify_required_dependencies
from app.bootstrap.runtime_init import load_prompt_registry
from app.common.cache import close_cache, init_cache
from app.database import async_session_factory, close_db, init_db
from app.media.continuous_fill import create_window_status, replenish_material_production_once
from app.media.service import process_media_jobs_once

async def _tick():
    async with async_session_factory() as session:
        jobs = await process_media_jobs_once(session, limit=20)
    window = create_window_status()
    fill = {"window": window, "created": 0}
    if window["open"]:
        async with async_session_factory() as session:
            fill = await replenish_material_production_once(session)
            await session.commit()
        if fill.get("created"):
            async with async_session_factory() as session:
                jobs = await process_media_jobs_once(session, limit=20)
    return {"jobs": jobs, "fill": fill}

async def main():
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)
    await verify_required_dependencies()
    await init_db()
    await init_cache()
    load_prompt_registry()
    logger.info("media job worker started root={} window={}", ROOT, create_window_status())
    idle = 3
    try:
        while not stop_event.is_set():
            try:
                result = await _tick()
                jobs = result.get("jobs") or {}
                fill = result.get("fill") or {}
                if jobs.get("advanced") or jobs.get("failed") or fill.get("created"):
                    logger.info("[media-worker] jobs={} fill={}", jobs, fill)
                    idle = 2
                else:
                    idle = min(20, idle + 2)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("media job worker error: {}", exc, exc_info=True)
                idle = 5
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=idle)
            except TimeoutError:
                continue
    finally:
        await close_cache()
        await close_db()
        logger.info("media job worker stopped")

if __name__ == "__main__":
    asyncio.run(main())
