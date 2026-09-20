"""Background automation for the intelligence learning loop.

The learning loop should not depend only on users opening the data-flow page and
pressing "capture". This worker is a control-plane reconciler: it periodically
captures new facts, materializes only policy-approved artifacts, and leaves
review-gated changes in the governed queue. It does not execute Skills, publish
Skill code, deploy models, or perform real external MCP side effects.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from app.auth.models import User
from app.common.cache import invalidate_page_cache
from app.common.time_utils import isoformat_bjt, now_bjt
from app.config import settings
from app.learning.service import run_learning_automation_once

_STATE_PATH = Path(os.getenv("LEARNING_AUTO_FLOW_STATE_PATH", "/tmp/skillforge-learning-auto-flow-state.json"))
_SNAPSHOT_RUN_KEYS = {
    "status",
    "run_id",
    "started_at",
    "finished_at",
    "error",
    "days",
    "limit",
    "materialize",
}
_LAST_AUTOMATION_RUN: dict[str, Any] = {
    "status": "never_run",
    "started_at": None,
    "finished_at": None,
    "error": None,
    "result": None,
}
_SERVICE_STATE: dict[str, Any] = {
    "service_status": "unknown",
    "service_mode": None,
    "service_owner": None,
    "service_started_at": None,
    "service_stopped_at": None,
    "first_run_planned_at": None,
    "next_run_planned_at": None,
    "last_loop_finished_at": None,
}


def _service_mode() -> str:
    if not settings.LEARNING_AUTO_FLOW_ENABLED:
        return "disabled"
    if settings.LEARNING_AUTO_FLOW_IN_WEB_WORKER_ENABLED:
        return "web_worker"
    return "independent_worker"


def _service_owner() -> str:
    if settings.LEARNING_AUTO_FLOW_IN_WEB_WORKER_ENABLED:
        return "skillforge.service"
    return "skillforge-learning-auto-flow.service"


def _automation_config_state() -> dict[str, Any]:
    return {
        "enabled": bool(settings.LEARNING_AUTO_FLOW_ENABLED),
        "auto_materialize_enabled": bool(settings.LEARNING_AUTO_MATERIALIZE_ENABLED),
        "auto_materialize_max_artifacts": int(settings.LEARNING_AUTO_MATERIALIZE_MAX_ARTIFACTS),
        "auto_training_enabled": bool(settings.LEARNING_AUTO_TRAINING_ENABLED),
        "auto_training_dispatch_enabled": bool(settings.LEARNING_AUTO_TRAINING_DISPATCH_ENABLED),
        "auto_deployment_approve_enabled": bool(settings.LEARNING_AUTO_DEPLOYMENT_APPROVE_ENABLED),
        "auto_training_interval_seconds": int(settings.LEARNING_AUTO_TRAINING_INTERVAL_SECONDS),
        "auto_training_days": int(settings.LEARNING_AUTO_TRAINING_DAYS),
        "auto_training_min_samples": int(settings.LEARNING_AUTO_TRAINING_MIN_SAMPLES),
        "auto_training_max_jobs": int(settings.LEARNING_AUTO_TRAINING_MAX_JOBS),
        "auto_agentization_ai_enabled": bool(settings.LEARNING_AUTO_AGENTIZATION_AI_ENABLED),
        "auto_self_audit_ai_enabled": bool(settings.LEARNING_AUTO_SELF_AUDIT_AI_ENABLED),
        "auto_self_audit_remediate_enabled": bool(settings.LEARNING_AUTO_SELF_AUDIT_REMEDIATE_ENABLED),
        "interval_seconds": int(settings.LEARNING_AUTO_FLOW_INTERVAL),
        "first_delay_seconds": int(settings.LEARNING_AUTO_FLOW_FIRST_DELAY),
        "days": int(settings.LEARNING_AUTO_FLOW_DAYS),
        "limit": int(settings.LEARNING_AUTO_FLOW_LIMIT),
        "service_mode": _service_mode(),
        "service_owner": _service_owner(),
    }


def _read_state_snapshot() -> dict[str, Any]:
    try:
        if not _STATE_PATH.exists():
            return {}
        payload = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("[learning-auto-flow] failed to read state snapshot {}: {}", _STATE_PATH, exc)
        return {}


def _write_state_snapshot() -> None:
    try:
        payload = {key: _LAST_AUTOMATION_RUN.get(key) for key in _SNAPSHOT_RUN_KEYS if key in _LAST_AUTOMATION_RUN}
        payload.update(_SERVICE_STATE)
        payload.update(_automation_config_state())
        payload["snapshot_updated_at"] = isoformat_bjt(now_bjt())
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _STATE_PATH.with_name(f"{_STATE_PATH.name}.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp_path.replace(_STATE_PATH)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[learning-auto-flow] failed to write state snapshot {}: {}", _STATE_PATH, exc)


def _parse_state_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now_bjt().tzinfo)
    return parsed


def _add_service_uptime(state: dict[str, Any]) -> dict[str, Any]:
    started_at = _parse_state_datetime(state.get("service_started_at"))
    if not started_at:
        state["service_uptime_seconds"] = None
        return state
    stopped_at = _parse_state_datetime(state.get("service_stopped_at"))
    end_at = stopped_at or now_bjt()
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=started_at.tzinfo)
    state["service_uptime_seconds"] = max(0, int((end_at - started_at).total_seconds()))
    return state


def _system_actor() -> User:
    return User(
        id="learning_auto_flow",
        username="learning_auto_flow",
        name="智能闭环自动流动引擎",
        role="system_admin",
        can_view_all=True,
        state="active",
        is_active=True,
        must_change_password=False,
        permissions_rev=0,
    )


def _session_factory():
    from app import database as db_mod

    return db_mod.async_session_factory


def get_learning_auto_flow_state() -> dict[str, Any]:
    state = {**_LAST_AUTOMATION_RUN, **_SERVICE_STATE}
    snapshot = _read_state_snapshot()
    if snapshot:
        state.update(snapshot)
    state.update(_automation_config_state())
    if not state.get("service_status"):
        state["service_status"] = "running" if state.get("service_started_at") and not state.get("service_stopped_at") else "unknown"
    return _add_service_uptime(state)


async def run_learning_auto_flow_once(
    *,
    days: int | None = None,
    limit: int | None = None,
    materialize: bool | None = None,
) -> dict[str, Any]:
    started_at = now_bjt()
    _LAST_AUTOMATION_RUN.update({
        "status": "running",
        "started_at": isoformat_bjt(started_at),
        "finished_at": None,
        "error": None,
    })
    _write_state_snapshot()
    clean_days = max(1, min(int(days if days is not None else settings.LEARNING_AUTO_FLOW_DAYS), 365))
    clean_limit = max(1, min(int(limit if limit is not None else settings.LEARNING_AUTO_FLOW_LIMIT), 1000))
    do_materialize = bool(settings.LEARNING_AUTO_MATERIALIZE_ENABLED if materialize is None else materialize)
    try:
        async with _session_factory()() as session:
            payload = await run_learning_automation_once(
                session,
                _system_actor(),
                days=clean_days,
                limit=clean_limit,
                materialize=do_materialize,
                trigger_type="scheduled",
                created_by="learning_auto_flow",
            )
            await session.commit()
        await invalidate_page_cache("learning", "knowledge", "training", "sf", "agent")
        run = payload.get("run") or {}
        _LAST_AUTOMATION_RUN.update({
            "status": payload.get("status"),
            "run_id": run.get("id"),
            "started_at": run.get("started_at") or isoformat_bjt(started_at),
            "finished_at": run.get("finished_at") or isoformat_bjt(now_bjt()),
            "days": clean_days,
            "limit": clean_limit,
            "materialize": do_materialize,
            "result": payload.get("result"),
            "error": run.get("error"),
        })
        _write_state_snapshot()
        if payload.get("status") == "failed":
            logger.warning("[learning-auto-flow] failed: {}", run.get("error"))
        return {
            "status": payload.get("status"),
            "run_id": run.get("id"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "days": clean_days,
            "limit": clean_limit,
            "materialize": do_materialize,
            "result": payload.get("result"),
            "run": run,
        }
    except Exception as exc:  # noqa: BLE001
        finished_at = now_bjt()
        error = str(exc)[:2000]
        _LAST_AUTOMATION_RUN.update({
            "status": "failed",
            "finished_at": isoformat_bjt(finished_at),
            "error": error,
        })
        _write_state_snapshot()
        logger.warning("[learning-auto-flow] failed: {}", exc)
        raise


async def learning_auto_flow_loop() -> None:
    if not settings.LEARNING_AUTO_FLOW_ENABLED:
        _SERVICE_STATE.update({
            "service_status": "disabled",
            "service_mode": "disabled",
            "service_owner": _service_owner(),
            "service_stopped_at": isoformat_bjt(now_bjt()),
            "next_run_planned_at": None,
        })
        _write_state_snapshot()
        logger.info("智能闭环自动流动已禁用 (LEARNING_AUTO_FLOW_ENABLED=false)")
        return
    interval = max(60, int(settings.LEARNING_AUTO_FLOW_INTERVAL))
    first_delay = max(0, int(settings.LEARNING_AUTO_FLOW_FIRST_DELAY))
    service_started_at = now_bjt()
    first_run_planned_at = service_started_at + timedelta(seconds=first_delay)
    _SERVICE_STATE.update({
        "service_status": "running",
        "service_mode": _service_mode(),
        "service_owner": _service_owner(),
        "service_started_at": isoformat_bjt(service_started_at),
        "service_stopped_at": None,
        "first_run_planned_at": isoformat_bjt(first_run_planned_at),
        "next_run_planned_at": isoformat_bjt(first_run_planned_at),
        "last_loop_finished_at": None,
    })
    _write_state_snapshot()
    logger.info(
        "智能闭环自动流动启动：首跑 {}s 后，之后每 {}s；days={} limit={} materialize={} "
        "materialize_max={} training_auto={} agentization_ai={} self_audit_ai={} self_audit_remediate={}",
        first_delay,
        interval,
        settings.LEARNING_AUTO_FLOW_DAYS,
        settings.LEARNING_AUTO_FLOW_LIMIT,
        settings.LEARNING_AUTO_MATERIALIZE_ENABLED,
        settings.LEARNING_AUTO_MATERIALIZE_MAX_ARTIFACTS,
        settings.LEARNING_AUTO_TRAINING_ENABLED,
        settings.LEARNING_AUTO_AGENTIZATION_AI_ENABLED,
        settings.LEARNING_AUTO_SELF_AUDIT_AI_ENABLED,
        settings.LEARNING_AUTO_SELF_AUDIT_REMEDIATE_ENABLED,
    )
    await asyncio.sleep(first_delay)
    while True:
        try:
            result = await run_learning_auto_flow_once()
            stats = result.get("result") or {}
            captured = stats.get("captured") or {}
            if captured or stats.get("materialized"):
                logger.info(
                    "[learning-auto-flow] captured={} materialized={}",
                    captured,
                    stats.get("materialized", 0),
                )
        except asyncio.CancelledError:
            _SERVICE_STATE.update({
                "service_status": "stopped",
                "service_stopped_at": isoformat_bjt(now_bjt()),
                "next_run_planned_at": None,
            })
            _write_state_snapshot()
            logger.info("智能闭环自动流动任务已取消")
            return
        except Exception:
            # Error is already recorded in run_learning_auto_flow_once. Keep loop alive.
            pass
        loop_finished_at = now_bjt()
        _SERVICE_STATE.update({
            "service_status": "running",
            "last_loop_finished_at": isoformat_bjt(loop_finished_at),
            "next_run_planned_at": isoformat_bjt(loop_finished_at + timedelta(seconds=interval)),
        })
        _write_state_snapshot()
        await asyncio.sleep(interval)
