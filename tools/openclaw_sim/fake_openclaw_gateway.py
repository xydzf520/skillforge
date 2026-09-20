#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

import websockets


HOST = os.getenv("OPENCLAW_SIM_HOST", "127.0.0.1")
PORT = int(os.getenv("OPENCLAW_SIM_PORT", "18789"))
EVENT_LOG = Path(os.getenv("OPENCLAW_SIM_EVENT_LOG", "/tmp/openclaw-sim-events.jsonl"))
GLM_API_URL = os.getenv("OPENCLAW_SIM_GLM_API_URL", "https://open.bigmodel.cn/api/anthropic/v1/messages")
GLM_MODEL = os.getenv("OPENCLAW_SIM_GLM_MODEL", "glm-5.1")
TRUE_VALUES = {"1", "true", "yes", "on"}


def _safe_rel(value: str | None, default: str) -> str:
    rel = str(value or default).strip().lstrip("/")
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return default
    return rel


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").lower() in TRUE_VALUES


def _read_text(path: Path, max_chars: int = 12000) -> str:
    try:
        value = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except UnicodeDecodeError:
        value = path.read_text(encoding="utf-8", errors="replace")
    if len(value) > max_chars:
        return value[:max_chars] + "\n...[truncated]..."
    return value


def _extract_json_object(text: str) -> dict:
    stripped = (text or "").strip()
    if not stripped:
        return {}
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(stripped[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _log(event: str, payload: dict) -> None:
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with EVENT_LOG.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps({
            "ts": time.time(),
            "event": event,
            "payload": payload,
        }, ensure_ascii=False, default=str) + "\n")


def _glm_messages(messages: list[dict], max_tokens: int = 512) -> dict:
    """Call the real GLM Anthropic-compatible API and return redacted metadata."""
    api_key = os.getenv("GLM_API_KEY") or os.getenv("ZHIPU_API_KEY")
    if not api_key:
        return {"ok": False, "error": "GLM_API_KEY missing"}
    started = time.time()
    body = json.dumps({
        "model": GLM_MODEL,
        "max_tokens": max_tokens,
        "messages": messages,
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        GLM_API_URL,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - test-only endpoint from env
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        return {
            "ok": False,
            "status_code": exc.code,
            "model": GLM_MODEL,
            "duration_ms": int((time.time() - started) * 1000),
            "error": raw[:500],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "model": GLM_MODEL,
            "duration_ms": int((time.time() - started) * 1000),
            "error": str(exc)[:500],
        }
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {}
    text = ""
    content = parsed.get("content") if isinstance(parsed, dict) else None
    if isinstance(content, list):
        text = "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return {
        "ok": status == 200,
        "status_code": status,
        "provider": "bigmodel_anthropic",
        "model": parsed.get("model") if isinstance(parsed, dict) else GLM_MODEL,
        "duration_ms": int((time.time() - started) * 1000),
        "text": text.strip(),
        "usage": parsed.get("usage") if isinstance(parsed, dict) else None,
    }


def _glm_probe(skill_id: str) -> dict:
    """Run a minimal real GLM call so Docker OpenClaw tests prove model wiring."""
    if not _env_truthy("OPENCLAW_SIM_GLM_PROBE"):
        return {"enabled": False}
    result = _glm_messages([
        {
            "role": "user",
            "content": f"Skill {skill_id}: reply with exactly OK",
        }
    ], max_tokens=16)
    return {
        "enabled": True,
        **result,
        "ok": bool(result.get("ok")) and str(result.get("text") or "").strip() == "OK",
    }


def _agent_decide(skill_dir: Path, runtime: dict, params: dict, skill_id: str) -> dict:
    """Use GLM as the remote OpenClaw agent planner before running the skill tool."""
    if not _env_truthy("OPENCLAW_SIM_AGENT_RUNTIME"):
        return {"enabled": False}

    skill_root = skill_dir.resolve(strict=False)
    files = {
        "SKILL.md": _read_text(skill_root / "SKILL.md", 16000),
        "contract.json": _read_text(skill_root / "contract.json", 12000),
        "policy_pack.yaml": _read_text(skill_root / "policy_pack.yaml", 6000),
        "skillforge.yaml": _read_text(skill_root / "skillforge.yaml", 6000),
    }
    payload_preview = json.dumps(params, ensure_ascii=False, default=str)
    if len(payload_preview) > 10000:
        payload_preview = payload_preview[:10000] + "\n...[truncated]..."

    prompt = "\n".join([
        "You are the OpenClaw node Agent executing an installed Skill package.",
        "Read the full Skill definition, contract, policy, runtime config, and incoming payload.",
        "Do not create business output yourself. Decide which declared runtime tool should execute the Skill.",
        "Return strict JSON only with this shape:",
        '{"decision":"run_script","script_entry":"scripts/main.py","validated_skill_package":true,"reason":"...","required_inputs_seen":["..."]}',
        "",
        f"skill_id: {skill_id}",
        f"runtime: {json.dumps(runtime, ensure_ascii=False, default=str)}",
        f"payload_preview: {payload_preview}",
        "",
        "<SKILL.md>",
        files["SKILL.md"],
        "</SKILL.md>",
        "<contract.json>",
        files["contract.json"],
        "</contract.json>",
        "<policy_pack.yaml>",
        files["policy_pack.yaml"],
        "</policy_pack.yaml>",
        "<skillforge.yaml>",
        files["skillforge.yaml"],
        "</skillforge.yaml>",
    ])
    call = _glm_messages([{"role": "user", "content": prompt}], max_tokens=700)
    decision = _extract_json_object(str(call.get("text") or ""))
    return {
        "enabled": True,
        "ok": bool(call.get("ok")) and decision.get("decision") == "run_script",
        "call": {
            key: call.get(key)
            for key in ("ok", "status_code", "provider", "model", "duration_ms", "usage", "error")
            if key in call
        },
        "decision": decision,
        "prompt_files": [name for name, content in files.items() if content],
        "raw_text": str(call.get("text") or "")[:500],
    }


def _run_script(skill_dir: Path, runtime: dict, params: dict, timeout: int) -> dict:
    script_entry = _safe_rel(runtime.get("script_entry"), "scripts/main.py")
    script = (skill_dir / script_entry).resolve(strict=False)
    skill_root = skill_dir.resolve(strict=False)
    try:
        script.relative_to(skill_root)
    except ValueError:
        return {
            "success": False,
            "error": f"script escapes skill root: {script_entry}",
        }
    if not script.exists():
        return {
            "success": False,
            "error": f"script not found: {script_entry}",
        }

    started = time.time()
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(skill_root),
        input=json.dumps(params, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=max(1, min(int(timeout), 3600)),
        env={
            **os.environ,
            "PYTHONPATH": str(skill_root / "scripts"),
            "SKILLFORGE_OPENCLAW_SIM": "1",
        },
    )
    stdout = (proc.stdout or "").strip()
    parsed = None
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = {"stdout": stdout}
    return {
        "success": proc.returncode == 0,
        "returncode": proc.returncode,
        "duration_ms": int((time.time() - started) * 1000),
        "output": parsed if isinstance(parsed, dict) else {"output": parsed},
        "stderr": (proc.stderr or "")[-4000:],
        "run_backend": "openclaw_agent",
        "simulated_gateway": True,
    }


def _run_skill_via_agent(skill_dir: Path, runtime: dict, params: dict, timeout: int, skill_id: str) -> dict:
    agent = _agent_decide(skill_dir, runtime, params, skill_id)
    if agent.get("enabled"):
        _log("glm.agent.decide", {
            "skill_id": skill_id,
            "ok": agent.get("ok"),
            "decision": agent.get("decision"),
            "call": agent.get("call"),
        })
        if not agent.get("ok"):
            return {
                "success": False,
                "error": "GLM agent did not authorize declared skill runtime",
                "agent_runtime": "glm",
                "agent": agent,
                "run_backend": "openclaw_agent_glm",
                "simulated_gateway": True,
            }
        decision = agent.get("decision") if isinstance(agent.get("decision"), dict) else {}
        runtime = {**runtime, "script_entry": decision.get("script_entry") or runtime.get("script_entry")}

    result = _run_script(skill_dir, runtime, params, timeout)
    if agent.get("enabled") and isinstance(result.get("output"), dict):
        meta = result["output"].get("_skillforge_meta")
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta["openclaw_agent_runtime"] = {
            "type": "glm_code_agent",
            "provider": "bigmodel_anthropic",
            "model": GLM_MODEL,
            "agent_decision": agent.get("decision"),
            "agent_call": agent.get("call"),
            "prompt_files": agent.get("prompt_files"),
        }
        result["output"]["_skillforge_meta"] = meta
        result["agent"] = {
            "runtime": "glm_code_agent",
            "model": GLM_MODEL,
            "decision": agent.get("decision"),
            "call": agent.get("call"),
        }
        result["run_backend"] = "openclaw_agent_glm"
    return result


async def _handle_request(message: dict) -> dict:
    method = str(message.get("method") or "")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    request_id = message.get("id") or f"sim-{uuid4().hex[:8]}"

    if method == "connect":
        _log("connect", params)
        return {"type": "res", "id": request_id, "ok": True, "result": {"gateway_version": "openclaw-sim-1.0"}}

    if method == "skills.reload":
        _log("skills.reload", params)
        return {"type": "res", "id": request_id, "ok": True, "result": {"ok": True, "status": "ok"}}

    if method == "skill.register":
        skill_dir = Path(str(params.get("skill_dir") or ""))
        ok = bool(params.get("skill_id")) and skill_dir.exists() and (skill_dir / "SKILL.md").exists()
        _log("skill.register", {"ok": ok, **params})
        return {
            "type": "res",
            "id": request_id,
            "ok": ok,
            "result": {
                "registered": ok,
                "success": ok,
                "skill_id": params.get("skill_id"),
                "skill_dir": str(skill_dir),
                "runtime": params.get("runtime") or {},
                "gateway_version": "openclaw-sim-1.0",
            },
            "error": None if ok else {"message": "skill.register validation failed"},
        }

    if method == "skill.run":
        skill_dir = Path(str(params.get("skill_dir") or ""))
        runtime = params.get("runtime") if isinstance(params.get("runtime"), dict) else {}
        payload = params.get("params") if isinstance(params.get("params"), dict) else {}
        timeout = int(runtime.get("timeout") or 60)
        _log("skill.run.start", {
            "skill_id": params.get("skill_id"),
            "skill_dir": str(skill_dir),
            "runtime": runtime,
            "context": params.get("context") or {},
        })
        try:
            result = await asyncio.to_thread(
                _run_skill_via_agent,
                skill_dir,
                runtime,
                payload,
                timeout,
                str(params.get("skill_id") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            result = {"success": False, "error": str(exc), "run_backend": "openclaw_agent"}
        probe = await asyncio.to_thread(_glm_probe, str(params.get("skill_id") or ""))
        if probe.get("enabled") and isinstance(result.get("output"), dict):
            meta = result["output"].get("_skillforge_meta")
            meta = dict(meta) if isinstance(meta, dict) else {}
            meta["openclaw_sim_llm"] = probe
            result["output"]["_skillforge_meta"] = meta
        if probe.get("enabled"):
            result["llm_probe"] = probe
            _log("glm.probe", {"skill_id": params.get("skill_id"), **probe})
        _log("skill.run.done", {"skill_id": params.get("skill_id"), "success": result.get("success")})
        return {
            "type": "res",
            "id": request_id,
            "ok": bool(result.get("success")),
            "result": result,
            "error": None if result.get("success") else {"message": result.get("error") or result.get("stderr") or "skill.run failed"},
        }

    _log("unknown", {"method": method, "params": params})
    return {
        "type": "res",
        "id": request_id,
        "ok": False,
        "error": {"message": f"unknown method: {method}"},
    }


async def handler(ws):
    await ws.send(json.dumps({
        "event": "connect.challenge",
        "payload": {"nonce": uuid4().hex},
    }))
    async for raw in ws:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if message.get("type") != "req":
            continue
        await ws.send(json.dumps(await _handle_request(message), ensure_ascii=False))


async def main() -> None:
    print(f"openclaw-sim listening on ws://{HOST}:{PORT}", flush=True)
    async with websockets.serve(handler, HOST, PORT, max_size=64 * 1024 * 1024, ping_interval=None):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
