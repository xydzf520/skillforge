#!/usr/bin/env python3
"""Verify and publish safe context windows for the 236 OpenAI-compatible gateway.

The script uses a Project SDK token against the public OpenAI-compatible API.
For probing above the currently published limit, pass --probe-max-input-tokens;
that writes a temporary "probing" SystemConfig limit before the external calls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from statistics import median
from types import SimpleNamespace
from typing import Any
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CONFIG_KEY = "project.openai236.context_measurements"
DEFAULT_LEVELS = [8192, 16384, 32768, 65536, 98304, 131072]
SAFETY_MARGIN_TOKENS = 2048
MAX_OUTPUT_TOKENS = 4096
DEFAULT_REPEATS = 2
DEFAULT_RECOMMENDED_P95_SECONDS = 180.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _estimate_tokens(text: str) -> int:
    cjk = 0
    non_space = 0
    for char in text:
        if char.isspace():
            continue
        non_space += 1
        if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff":
            cjk += 1
    return max(1, ceil(cjk * 1.1 + max(0, non_space - cjk) / 3.5))


def _request_json(method: str, url: str, token: str, body: dict[str, Any] | None, timeout: int) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return int(getattr(resp, "status", None) or getattr(resp, "code", 200) or 200), json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {"raw": raw[:2000]}
        return int(exc.code), payload


def _context_prompt(target_tokens: int, require_markers: str) -> tuple[str, dict[str, str]]:
    markers = {
        "head": f"SFCTX_HEAD_{target_tokens}_{uuid4().hex[:10]}",
        "middle": f"SFCTX_MID_{target_tokens}_{uuid4().hex[:10]}",
        "tail": f"SFCTX_TAIL_{target_tokens}_{uuid4().hex[:10]}",
    }
    filler_unit = " context_probe_data "
    filler_chunk = filler_unit * max(4, min(80, target_tokens // 128))
    parts = [
        "You are verifying long context retention for SkillForge 236.",
        f"Head marker: {markers['head']}",
    ]
    while _estimate_tokens("\n".join(parts)) < max(1, target_tokens // 2):
        parts.append(filler_chunk)
    parts.append(f"Middle marker: {markers['middle']}")
    while _estimate_tokens("\n".join(parts)) < max(1, target_tokens - 120):
        parts.append(filler_chunk)
    parts.append(f"Tail marker: {markers['tail']}")
    if require_markers == "all":
        parts.append(
            "Return exactly these three markers as compact JSON with keys head, middle, tail: "
            + json.dumps(markers, ensure_ascii=False, separators=(",", ":"))
        )
    else:
        parts.append(f"Return exactly this tail marker and no other text: {markers['tail']}")
    return "\n".join(parts), markers


async def _write_system_config(value: dict[str, Any]) -> None:
    from app.common.models import SystemConfig
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory

    async with async_session_factory() as db:
        row = await db.get(SystemConfig, CONFIG_KEY)
        if row is None:
            row = SystemConfig(key=CONFIG_KEY, value=value, updated_by="verify_236_context_window")
            db.add(row)
        else:
            row.value = value
            row.updated_by = "verify_236_context_window"
            row.updated_at = now_bjt()
        await db.commit()


async def _temp_token_user() -> Any:
    from sqlalchemy import or_, select

    from app.auth.models import User
    from app.database import async_session_factory

    async with async_session_factory() as db:
        user = (
            await db.execute(
                select(User)
                .where(
                    or_(User.role.in_(["admin", "system_admin"]), User.can_view_all.is_(True)),
                    User.state != "disabled",
                    User.is_active.is_(True),
                )
                .order_by(User.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if user:
            db.expunge(user)
            return user
    return SimpleNamespace(
        id="verify_236_context_window",
        username="verify_236_context_window",
        name="236 context verifier",
        role="system_admin",
        department=None,
        can_view_all=True,
        is_active=True,
        state="active",
        permissions_rev=0,
    )


async def _create_temp_project_token(project_id: str) -> tuple[str, str, str]:
    from app.database import async_session_factory
    from app.projects import service as project_service

    user = await _temp_token_user()
    async with async_session_factory() as db:
        if project_id == project_service.PROJECT_COMPANY_SDK_PROJECT_ID:
            await project_service.ensure_company_sdk_project(db, user)
        created = await project_service.create_project_sdk_token(
            db,
            user,
            project_id,
            {
                "name": "236-context-speed-validation",
                "expires_in_days": 1,
                "metadata": {"source": "verify_236_context_window", "temporary": True},
            },
        )
        await db.commit()
        return str(created["token"]), str(created["id"]), str(created["project_id"])


async def _revoke_temp_project_token(project_id: str, token_id: str) -> None:
    from app.database import async_session_factory
    from app.projects import service as project_service

    user = await _temp_token_user()
    async with async_session_factory() as db:
        try:
            await project_service.revoke_project_sdk_token(db, user, project_id, token_id)
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"warning: failed to revoke temp Project SDK token {token_id}: {exc}", file=sys.stderr)


async def _read_system_config() -> dict[str, Any] | None:
    from app.common.models import SystemConfig
    from app.database import async_session_factory

    async with async_session_factory() as db:
        row = await db.get(SystemConfig, CONFIG_KEY)
        return dict(row.value) if row is not None and isinstance(row.value, dict) else None


async def _restore_system_config(value: dict[str, Any] | None) -> None:
    from app.common.models import SystemConfig
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory

    async with async_session_factory() as db:
        row = await db.get(SystemConfig, CONFIG_KEY)
        if value is None:
            if row is not None:
                await db.delete(row)
        elif row is None:
            db.add(SystemConfig(key=CONFIG_KEY, value=value, updated_by="verify_236_context_window_restore"))
        else:
            row.value = value
            row.updated_by = "verify_236_context_window_restore"
            row.updated_at = now_bjt()
        await db.commit()


async def _prepare_probe_limits(model_ids: list[str], max_input_tokens: int) -> None:
    now = _utc_now()
    payload = {
        "generated_at": now,
        "source": "verify_236_context_window_probe",
        "models": {
            model_id: {
                "context_status": "probing",
                "context_window": max_input_tokens + MAX_OUTPUT_TOKENS + SAFETY_MARGIN_TOKENS,
                "max_input_tokens": max_input_tokens,
                "recommended_input_tokens": min(16384, max_input_tokens),
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "context_verified_at": None,
            }
            for model_id in model_ids
        },
    }
    await _write_system_config(payload)


def _model_ids(models_payload: dict[str, Any], only: list[str]) -> list[str]:
    ids = [
        str(item.get("id") or "").strip()
        for item in models_payload.get("data", [])
        if isinstance(item, dict) and item.get("id") and item.get("callable", True)
    ]
    ids = [item for item in ids if item]
    if only:
        allowed = set(only)
        ids = [item for item in ids if item in allowed]
    return ids


def _response_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") if isinstance(payload.get("choices"), list) else []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    return str(message.get("content") or first.get("text") or "")


def _success(payload: dict[str, Any], markers: dict[str, str], required_names: list[str]) -> tuple[bool, list[str], list[str]]:
    text = _response_text(payload)
    missing_all = [name for name, marker in markers.items() if marker not in text]
    missing_required = [name for name in required_names if name in missing_all]
    return not missing_required, missing_required, missing_all


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _level_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    elapsed = [float(row["elapsed_seconds"]) for row in rows if row.get("ok")]
    all_ok = bool(rows) and all(bool(row.get("ok")) for row in rows)
    return {
        "ok": all_ok,
        "attempts": len(rows),
        "successes": sum(1 for row in rows if row.get("ok")),
        "p50_elapsed_seconds": round(float(median(elapsed)), 3) if elapsed else None,
        "p95_elapsed_seconds": round(float(_percentile(elapsed, 0.95)), 3) if elapsed else None,
        "max_elapsed_seconds": round(max(elapsed), 3) if elapsed else None,
        "min_elapsed_seconds": round(min(elapsed), 3) if elapsed else None,
    }


def _recommended_input(levels: list[dict[str, Any]], *, p95_threshold: float) -> int:
    passed = [row for row in levels if row.get("ok")]
    if not passed:
        return 16384
    fast = [
        row
        for row in passed
        if isinstance(row.get("p95_elapsed_seconds"), (int, float))
        and float(row["p95_elapsed_seconds"]) <= p95_threshold
    ]
    if fast:
        return int(max(row["input_tokens"] for row in fast))
    return int(min(passed, key=lambda row: float(row.get("p50_elapsed_seconds") or row.get("max_elapsed_seconds") or 10**9))["input_tokens"])


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("SKILLFORGE_BASE_URL", "http://skillforge.example.com"))
    parser.add_argument("--token", default=os.environ.get("SKILLFORGE_PROJECT_TOKEN", ""))
    parser.add_argument("--models", default="", help="Comma-separated model IDs. Defaults to all callable models.")
    parser.add_argument("--levels", default=",".join(str(item) for item in DEFAULT_LEVELS))
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--output", default="/tmp/sf_236_context_validation_results.json")
    parser.add_argument("--probe-max-input-tokens", type=int, default=0)
    parser.add_argument("--write-system-config", action="store_true")
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--recommended-p95-seconds", type=float, default=DEFAULT_RECOMMENDED_P95_SECONDS)
    parser.add_argument("--require-markers", choices=["tail", "all"], default="tail")
    parser.add_argument("--create-temp-token", action="store_true")
    parser.add_argument("--temp-project-id", default="skillforge-company-sdk")
    args = parser.parse_args()

    temp_token_id = ""
    temp_project_id = ""
    token = args.token
    if not token and args.create_temp_token:
        token, temp_token_id, temp_project_id = await _create_temp_project_token(args.temp_project_id)
        print(f"created temporary Project SDK token id={temp_token_id} project_id={temp_project_id}", flush=True)
    if not token:
        raise SystemExit("SKILLFORGE_PROJECT_TOKEN or --token is required")

    base_url = args.base_url.rstrip("/")
    selected_models = [item.strip() for item in args.models.split(",") if item.strip()]
    levels = [int(item.strip()) for item in args.levels.split(",") if item.strip()]
    started_at = _utc_now()

    status, models_payload = _request_json("GET", f"{base_url}/api/projects/openai/236/v1/models", token, None, args.timeout)
    if status != 200:
        raise SystemExit(f"/models failed: HTTP {status} {json.dumps(models_payload, ensure_ascii=False)[:1000]}")
    model_ids = _model_ids(models_payload, selected_models)
    if not model_ids:
        raise SystemExit("no callable 236 models found")

    previous_config: dict[str, Any] | None = None
    if args.probe_max_input_tokens > 0:
        previous_config = await _read_system_config()
        await _prepare_probe_limits(model_ids, args.probe_max_input_tokens)
        time.sleep(1)

    results: dict[str, Any] = {
        "base_url": base_url,
        "started_at": started_at,
        "levels": levels,
        "repeats": max(1, int(args.repeats or 1)),
        "require_markers": args.require_markers,
        "recommended_p95_seconds": args.recommended_p95_seconds,
        "models": {},
    }
    measurements: dict[str, Any] = {
        "generated_at": _utc_now(),
        "source": "verify_236_context_window",
        "models": {},
    }

    for model_id in model_ids:
        model_result = {"levels": [], "max_stable_input_tokens": 0, "recommended_input_tokens": 0}
        for level in levels:
            attempts = []
            for attempt in range(max(1, int(args.repeats or 1))):
                prompt_target_tokens = max(512, level - min(1024, max(256, level // 16)))
                prompt, markers = _context_prompt(prompt_target_tokens, args.require_markers)
                required_marker_names = list(markers.keys()) if args.require_markers == "all" else ["tail"]
                estimated_prompt_tokens = _estimate_tokens(prompt)
                body = {
                    "request_id": f"ctx-{model_id[:24]}-{level}-{attempt + 1}-{uuid4().hex[:8]}",
                    "model": model_id,
                    "messages": [
                        {"role": "system", "content": "Return only the requested sentinel markers."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 128,
                    "max_input_tokens": level,
                    "context_window": level + MAX_OUTPUT_TOKENS + SAFETY_MARGIN_TOKENS,
                    "truncation": "disabled",
                    "timeout_seconds": args.timeout,
                }
                t0 = time.perf_counter()
                http_status, payload = _request_json(
                    "POST",
                    f"{base_url}/api/projects/openai/236/v1/chat/completions",
                    token,
                    body,
                    args.timeout + 90,
                )
                elapsed = round(time.perf_counter() - t0, 3)
                marker_ok, missing_markers, missing_all_markers = (
                    _success(payload, markers, required_marker_names)
                    if http_status == 200
                    else (False, required_marker_names, list(markers.keys()))
                )
                usage = payload.get("usage") if isinstance(payload, dict) else {}
                skillforge = payload.get("skillforge") if isinstance(payload, dict) else {}
                ok = http_status == 200 and marker_ok
                attempt_row = {
                    "attempt": attempt + 1,
                    "http_status": http_status,
                    "ok": ok,
                    "marker_ok": marker_ok,
                    "missing_markers": missing_markers,
                    "missing_all_markers": missing_all_markers,
                    "prompt_target_tokens": prompt_target_tokens,
                    "estimated_prompt_tokens": estimated_prompt_tokens,
                    "elapsed_seconds": elapsed,
                    "tokens_per_second_estimate": round(estimated_prompt_tokens / elapsed, 3) if elapsed > 0 else None,
                    "project_run_id": ((skillforge or {}).get("project_run_id") if isinstance(skillforge, dict) else None),
                    "usage": usage if isinstance(usage, dict) else {},
                    "usage_reliable": ((skillforge or {}).get("usage_reliable") if isinstance(skillforge, dict) else None),
                    "error": (payload.get("error") if isinstance(payload, dict) else None),
                }
                attempts.append(attempt_row)
                print(json.dumps({"model": model_id, "input_tokens": level, **attempt_row}, ensure_ascii=False), flush=True)
                if not ok:
                    break
            summary = _level_summary(attempts)
            row = {
                "input_tokens": level,
                "attempts": attempts,
                **summary,
            }
            model_result["levels"].append(row)
            if summary["ok"]:
                model_result["max_stable_input_tokens"] = level
            else:
                break

        safe_input = int(model_result["max_stable_input_tokens"] or 0)
        recommended_input = _recommended_input(model_result["levels"], p95_threshold=float(args.recommended_p95_seconds))
        if safe_input:
            recommended_input = min(recommended_input, safe_input)
        model_result["recommended_input_tokens"] = recommended_input
        status_value = "verified" if safe_input else "failed"
        measurements["models"][model_id] = {
            "context_status": status_value,
            "context_window": safe_input + MAX_OUTPUT_TOKENS + SAFETY_MARGIN_TOKENS if safe_input else 32768,
            "measured_context_window": safe_input + MAX_OUTPUT_TOKENS + SAFETY_MARGIN_TOKENS if safe_input else None,
            "max_input_tokens": safe_input or 16384,
            "recommended_input_tokens": recommended_input,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "context_verified_at": _utc_now(),
            "recommended_p95_seconds": args.recommended_p95_seconds,
            "level_summaries": [
                {key: value for key, value in row.items() if key != "attempts"}
                for row in model_result["levels"]
            ],
        }
        results["models"][model_id] = model_result

    results["measurements"] = measurements
    output_path = Path(args.output)
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_system_config:
        await _write_system_config(measurements)
    elif args.probe_max_input_tokens > 0:
        await _restore_system_config(previous_config)
    if temp_token_id and temp_project_id:
        await _revoke_temp_project_token(temp_project_id, temp_token_id)
        status_after_revoke, _ = _request_json("GET", f"{base_url}/api/projects/openai/236/v1/models", token, None, 30)
        results["temp_token_revoked_check"] = {"http_status": status_after_revoke}
        output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"revoked temporary Project SDK token id={temp_token_id}; post-revoke /models HTTP {status_after_revoke}", flush=True)
    print(f"wrote {output_path}")
    if args.write_system_config:
        print(f"updated SystemConfig {CONFIG_KEY}")
    elif args.probe_max_input_tokens > 0:
        print(f"restored SystemConfig {CONFIG_KEY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
