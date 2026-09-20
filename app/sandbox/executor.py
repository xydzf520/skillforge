"""Sandbox preview executor for v7 TaskContract flow（G1：真隔离 subprocess）。

策略：
1. 优先用 build_preview 渲染快路径（无脚本时直接给业务方看 markdown）。
2. 若 contract.script_path 指定了用户脚本（例如模板的 scripts/main.py），
   通过 subprocess.run([python, "-m", "app.sandbox.runner"], env=stripped) 隔离执行：
   - env 剥离 DATABASE_URL / 密钥 / token / AWS_xxx 等敏感变量
   - timeout=10s
   - 子进程内 setrlimit(CPU/MEM/NOFILE)
   - dry_run=True 让用户脚本知道当前是预演模式，不能真发钉钉/邮件
3. 子进程返回的 output 合并到 preview.script_output 字段，前端可对比"模板渲染"与"脚本输出"。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.adapters.dingtalk_card.render import render_card
from app.workbench.task_contract import build_preview, build_preview_cache_key


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # /home/skillforge/skillforge

# 这些环境变量永远不能传给沙箱子进程
SENSITIVE_ENV_KEYS = {
    "DATABASE_URL",
    "DATABASE_URL_SYNC",
    "DATABASE_URL_TEST",
    "DINGTALK_APP_KEY",
    "DINGTALK_APP_SECRET",
    "DINGTALK_AGENT_ID",
    "DINGTALK_CALLBACK_TOKEN",
    "DINGTALK_CALLBACK_AES_KEY",
    "AI_API_KEY",
    "OPENCLAW_DEFAULT_AUTH",
    "OPENCLAW_RELOAD_TOKEN",
    "SESSION_SECRET",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "REDIS_URL",
}


def load_fixture(name: str) -> dict:
    path = FIXTURE_DIR / name
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _build_sandbox_env() -> dict[str, str]:
    """剥离敏感变量后返回干净 env。"""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in SENSITIVE_ENV_KEYS:
            continue
        if key.startswith(("AWS_", "GCP_", "AZURE_")):
            continue
        if key.endswith(("_TOKEN", "_SECRET", "_KEY")) and key not in {"PATH", "HOME"}:
            continue
        env[key] = value
    # 强制最小集
    env.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
    env.setdefault("HOME", os.environ.get("HOME", "/tmp"))
    env["SANDBOX_MODE"] = "1"
    return env


def _run_user_script(script_path: str, fixture: dict, contract: dict, timeout: int = 10) -> dict[str, Any]:
    """在子进程中跑 user script（隔离临时目录 + 网络阻断），返回标准化结果。"""
    import tempfile

    payload = {
        "script_path": script_path,
        "fixture": fixture,
        "contract": contract,
        "dry_run": True,
    }

    # 每次执行使用独立 tmpdir，结束后自动清理
    with tempfile.TemporaryDirectory(prefix="sf_sandbox_") as tmpdir:
        env = _build_sandbox_env()
        env["TMPDIR"] = tmpdir
        env["HOME"] = tmpdir  # 防止脚本读写真实 home

        try:
            proc = subprocess.run(
                [sys.executable, "-m", "app.sandbox.runner"],
                input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                capture_output=True,
                timeout=timeout,
                env=env,
                cwd=str(PROJECT_ROOT),  # 让 runner 能 import app.sandbox.runner
            )
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"sandbox timeout (>{timeout}s)", "output": None}
        except FileNotFoundError as exc:
            return {"success": False, "error": f"sandbox runner not found: {exc}", "output": None}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"sandbox launch failed: {exc}", "output": None}

        stdout = proc.stdout.decode("utf-8", "ignore").strip()
        stderr = proc.stderr.decode("utf-8", "ignore").strip()
        if not stdout:
            return {"success": False, "error": stderr or "sandbox returned empty output", "output": None}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            return {"success": False, "error": f"sandbox stdout not json: {exc}", "output": None, "raw": stdout[:500]}


def run_script(script_path: str, payload: dict | None = None, timeout: int = 10) -> dict[str, Any]:
    """隔离执行单个 Skill 脚本，兼容 main(payload) / execute(input_data)。"""
    request = {
        "script_path": script_path,
        "payload": payload or {},
        "dry_run": True,
    }
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "app.sandbox.runner"],
            input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            env=_build_sandbox_env(),
            cwd=str(PROJECT_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"sandbox timeout (>{timeout}s)", "output": None}
    except FileNotFoundError as exc:
        return {"success": False, "error": f"sandbox runner not found: {exc}", "output": None}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"sandbox launch failed: {exc}", "output": None}

    stdout = proc.stdout.decode("utf-8", "ignore").strip()
    stderr = proc.stderr.decode("utf-8", "ignore").strip()
    if not stdout:
        return {"success": False, "error": stderr or "sandbox returned empty output", "output": None}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        return {"success": False, "error": f"sandbox stdout not json: {exc}", "output": None, "raw": stdout[:500]}


def run_preview(contract: dict, fixture: dict | None = None) -> dict:
    """生成预演结果。

    1) 调 build_preview 渲染模板版（始终有 fallback 输出给业务方看）
    2) 若 contract.script_path 指向用户脚本 → 在子进程隔离执行 → 把脚本输出附加到 preview
    """
    from app.common.contract_schema import get_preview_input

    draft = dict(contract or {})
    preview_input = fixture or get_preview_input(contract)
    if preview_input:
        draft["sample_input"] = preview_input
    preview = build_preview(draft, cache_key=build_preview_cache_key(draft), cached=False)
    if preview.get("adapter") == "dingtalk_card":
        preview["card_payload"] = render_card(preview.get("card_payload") or {})

    script_path = (contract or {}).get("script_path") or (contract or {}).get("scripts_main")
    if script_path:
        sandbox_result = _run_user_script(
            script_path=str(script_path),
            fixture=preview_input or {},
            contract=contract or {},
        )
        preview["script_output"] = sandbox_result
        # 如果脚本成功且返回 card_payload，覆盖模板版（脚本是真的判断逻辑）
        if sandbox_result.get("success") and isinstance(sandbox_result.get("output"), dict):
            output = sandbox_result["output"]
            if "card_payload" in output:
                preview["card_payload"] = output["card_payload"]
            if "rendered_output" in output:
                preview["rendered_output"] = output["rendered_output"]

    return preview
