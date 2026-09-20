#!/usr/bin/env python3
"""Verify SkillForge platform capability matrix and feed results into Projects.

Covers the pieces the project-hosting objective depends on:
- platform AI profile readiness (default/cheap), without exposing keys;
- MCP catalog breadth and schema quality;
- safe real MCP samples: read-only data capability list and DingTalk dry-run;
- Project Gateway/project capacity, runtime evaluation, report/todo learning loop;
- external GitHub Codex/AI evaluation result integration.

By default no paid model call and no real external write is performed. Use
``--call-ai`` only after explicit approval to spend model credits.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml
from sqlalchemy import select

from app.common.ai import (
    LLMAuthError,
    LLMQuotaExceededError,
    LLMRateLimitError,
    _ai_required_fields_missing,
    call_llm,
    get_ai_profile_config,
    redact_secret_text,
)
from app.common.models import SystemConfig
from app.codex import service as codex_service
from app.database import async_session_factory
from app.projects import service as project_service

DEFAULT_OUTPUT = ROOT / "artifacts" / "project-checks" / "platform-capability-matrix-result.json"
DEFAULT_GITHUB_EVAL = ROOT / "artifacts" / "project-checks" / "github-codex-ai-run-eval.json"
DEFAULT_GITHUB_REGISTRATION = ROOT / "artifacts" / "project-checks" / "github-codex-registration-result.json"
DEFAULT_TYPE_MATRIX = ROOT / "artifacts" / "project-checks" / "codex-project-type-matrix-result.json"
DEFAULT_SCALE_INVENTORY = ROOT / "artifacts" / "project-checks" / "project-enterprise-scale-inventory-result.json"
DEFAULT_AI_TAKEOVER = ROOT / "artifacts" / "project-checks" / "project-ai-takeover-matrix-result.json"
SENSITIVE_KEY_PARTS = ("key", "token", "secret", "password", "cookie", "authorization")
MUTATING_TOOL_VERBS = (
    "send",
    "create",
    "update",
    "delete",
    "upload",
    "submit",
    "publish",
    "apply",
    "notify",
    "write",
)
BUSINESS_CACHED_SAMPLE_TOOLS = (
    ("mcp_tmall_cached_sample", "天猫缓存数据样本", "skillforge_tmall_link_decline_data_latest", {"limit": 1}),
    ("mcp_yuyidata_cached_sample", "语艺缓存数据样本", "skillforge_yuyidata_customer_service_data_latest", {"limit": 1}),
)


@dataclass
class Check:
    key: str
    label: str
    status: str
    detail: str
    evidence: dict[str, Any] | None = None


@dataclass
class CommandResult:
    cmd: str
    returncode: int
    stdout_tail: str
    stderr_tail: str


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def run_cmd(cmd: list[str], *, timeout: int = 60) -> CommandResult:
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    return CommandResult(
        cmd=" ".join(cmd),
        returncode=proc.returncode,
        stdout_tail=(proc.stdout or "")[-1_000_000:],
        stderr_tail=(proc.stderr or "")[-4000:],
    )


def extract_json(text: str) -> Any:
    text = text or ""
    starts = [idx for idx, ch in enumerate(text) if ch in "[{]"]
    for idx in starts:
        try:
            return json.loads(text[idx:])
        except Exception:
            continue
    raise ValueError("no JSON object found in command output")


def safe_url_host(value: str) -> str:
    parsed = urlparse(str(value or ""))
    if parsed.netloc:
        return parsed.netloc
    if parsed.path:
        return parsed.path.split("/", 1)[0]
    return ""


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in config.items():
        lowered = key.lower()
        if lowered.endswith(("max_tokens", "timeout", "temperature")):
            out[key] = value
        elif any(part in lowered for part in SENSITIVE_KEY_PARTS):
            out[key] = {"present": bool(value), "redacted": True}
        elif lowered.endswith("api_base") or lowered.endswith("base"):
            out[key] = safe_url_host(str(value or ""))
        else:
            out[key] = value
    return out


def tool_name_looks_mutating(tool_name: str) -> bool:
    parts = [part for part in str(tool_name or "").lower().replace("-", "_").split("_") if part]
    return any(part in MUTATING_TOOL_VERBS for part in parts)


def summarize_mcp_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    servers = catalog.get("servers") if isinstance(catalog, dict) else []
    tools: list[dict[str, Any]] = []
    for server in servers or []:
        for tool in server.get("tools") or []:
            item = dict(tool)
            item["server"] = server.get("name")
            tools.append(item)
    by_platform: dict[str, int] = {}
    write_count = 0
    schema_missing = []
    requires_external = []
    potential_write_tools = []
    write_meta_gaps = []
    for tool in tools:
        meta = tool.get("meta") if isinstance(tool.get("meta"), dict) else {}
        tool_name = str(tool.get("name") or "")
        platform = str(meta.get("platform") or tool.get("server") or "unknown")
        by_platform[platform] = by_platform.get(platform, 0) + 1
        meta_write = bool(meta.get("write"))
        if meta_write:
            write_count += 1
        if not tool.get("inputSchema"):
            schema_missing.append(tool.get("name"))
        if meta.get("requires_shop_id") or platform in {"tmall", "yuyidata"}:
            requires_external.append(tool.get("name"))
        if tool_name_looks_mutating(tool_name):
            potential_write_tools.append(tool_name)
            if not meta_write:
                write_meta_gaps.append(tool_name)
    return {
        "server_count": len(servers or []),
        "tool_count": len(tools),
        "write_tool_count": write_count,
        "read_tool_count": len(tools) - write_count,
        "by_platform": by_platform,
        "schema_missing": schema_missing[:50],
        "external_or_business_tools": requires_external[:80],
        "potential_write_tools_by_name": potential_write_tools[:80],
        "write_meta_gaps": write_meta_gaps[:80],
    }


def load_local_mcp_catalog_snapshot() -> tuple[dict[str, Any], CommandResult]:
    catalog = codex_service.mcp_catalog_snapshot()
    return catalog, CommandResult(
        cmd="python:app.codex.service.mcp_catalog_snapshot",
        returncode=0,
        stdout_tail=json.dumps(catalog, ensure_ascii=False)[-1_000_000:],
        stderr_tail="",
    )


async def verify_ai_profiles() -> tuple[list[Check], dict[str, Any]]:
    checks: list[Check] = []
    details: dict[str, Any] = {}
    async with async_session_factory() as db:
        rows = (await db.execute(select(SystemConfig).where(SystemConfig.key.like("ai.%")))).scalars().all()
    configured_keys = sorted(row.key for row in rows)
    for profile in ("default", "cheap"):
        try:
            cfg = await get_ai_profile_config(model_profile=profile, require_system_config=False)
            missing = _ai_required_fields_missing(cfg)
            key_prefix = "ai.cheap." if profile == "cheap" else "ai."
            explicit_keys = [key for key in configured_keys if key.startswith(key_prefix)]
            status = "pass" if not missing else "warn"
            detail = "后台 AI 配置可用于平台调用" if not missing else "AI 配置未完整，真实模型调用会失败；本次未触发付费调用"
            details[profile] = {"configured": not missing, "explicit_key_count": len(explicit_keys), "config": redact_config(cfg)}
            checks.append(Check(f"ai_{profile}", f"AI profile: {profile}", status, detail, {"explicit_keys": explicit_keys}))
        except Exception as exc:  # noqa: BLE001
            details[profile] = {"configured": False, "error": str(exc)[:500]}
            checks.append(Check(f"ai_{profile}", f"AI profile: {profile}", "fail", str(exc)[:500]))
    return checks, details


def verify_mcp(*, include_samples: bool) -> tuple[list[Check], dict[str, Any]]:
    checks: list[Check] = []
    details: dict[str, Any] = {}
    try:
        catalog, catalog_cmd = load_local_mcp_catalog_snapshot()
        details["catalog_source"] = "local_inprocess"
        details["catalog_command"] = asdict(catalog_cmd)
    except Exception as exc:  # noqa: BLE001
        details["catalog_source"] = "sf_cli_fallback"
        details["catalog_local_error"] = str(exc)[:500]
        catalog_cmd = run_cmd(["sf", "mcp", "catalog", "--json"], timeout=90)
        details["catalog_command"] = asdict(catalog_cmd)
        if catalog_cmd.returncode != 0:
            checks.append(Check("mcp_catalog", "MCP catalog", "fail", "本地 MCP catalog 读取失败，且 sf mcp catalog --json 失败", asdict(catalog_cmd)))
            return checks, details
        try:
            catalog = extract_json(catalog_cmd.stdout_tail)
        except Exception as parse_exc:  # noqa: BLE001
            checks.append(Check("mcp_catalog_parse", "MCP catalog parse", "fail", str(parse_exc)[:500]))
            return checks, details

    try:
        summary = summarize_mcp_catalog(catalog)
        summary["source"] = details["catalog_source"]
        details["catalog_summary"] = summary
        checks.append(
            Check(
                "mcp_catalog",
                "MCP catalog",
                "pass" if summary["tool_count"] >= 40 and not summary["schema_missing"] and not summary["write_meta_gaps"] else "warn",
                f"发现 {summary['tool_count']} 个 MCP 工具，显式写工具 {summary['write_tool_count']} 个，疑似写/敏感工具 {len(summary['potential_write_tools_by_name'])} 个，外部/业务类 {len(summary['external_or_business_tools'])} 个",
                summary,
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("mcp_catalog_parse", "MCP catalog parse", "fail", str(exc)[:500]))
        return checks, details

    if include_samples:
        data_cmd = run_cmd(["sf", "mcp", "call", "skillforge_data_capability_list", "--args", "{}"], timeout=90)
        details["data_capability_sample"] = asdict(data_cmd)
        try:
            payload = extract_json(data_cmd.stdout_tail)
            count = int((payload.get("data") or {}).get("count") or 0)
            checks.append(
                Check(
                    "mcp_read_sample",
                    "MCP read sample",
                    "pass" if data_cmd.returncode == 0 and payload.get("ok") and count >= 1 else "warn",
                    f"读取数据能力目录 {count} 项",
                    {"proof": payload.get("proof"), "count": count},
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("mcp_read_sample", "MCP read sample", "fail", str(exc)[:500], asdict(data_cmd)))

        dry_args = json.dumps(
            {
                "title": "SkillForge 平台能力验证 dry-run",
                "markdown": "dry-run: 平台能力验证，不真实发送",
                "query": "管理员",
                "dryRun": True,
                "limit": 1,
            },
            ensure_ascii=False,
        )
        write_cmd = run_cmd(["sf", "mcp", "call", "skillforge_dingtalk_send_work_notice", "--args", dry_args], timeout=90)
        details["write_dry_run_sample"] = asdict(write_cmd)
        try:
            payload = extract_json(write_cmd.stdout_tail)
            data = payload.get("data") or {}
            checks.append(
                Check(
                    "mcp_write_dry_run",
                    "MCP write dry-run",
                    "pass" if write_cmd.returncode == 0 and payload.get("ok") and data.get("dryRun") is True else "warn",
                    f"钉钉工作通知 dry-run recipient_count={data.get('recipient_count')}",
                    {"proof": payload.get("proof"), "recipient_count": data.get("recipient_count"), "dryRun": data.get("dryRun")},
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("mcp_write_dry_run", "MCP write dry-run", "fail", str(exc)[:500], asdict(write_cmd)))
        for key, label, tool_name, sample_args in BUSINESS_CACHED_SAMPLE_TOOLS:
            sample_cmd = run_cmd(["sf", "mcp", "call", tool_name, "--args", json.dumps(sample_args, ensure_ascii=False)], timeout=90)
            details[key] = asdict(sample_cmd)
            try:
                payload = extract_json(sample_cmd.stdout_tail)
                data = payload.get("data") or {}
                proof = payload.get("proof") or {}
                count = int(data.get("count") or 0)
                checks.append(
                    Check(
                        key,
                        label,
                        "pass" if sample_cmd.returncode == 0 and payload.get("ok") and count >= 1 else "warn",
                        f"{tool_name} 缓存摘要 {count} 项，凭据位置 {proof.get('credential_location')}",
                        {"tool": tool_name, "proof": proof, "count": count, "platform": data.get("platform")},
                    )
                )
            except Exception as exc:  # noqa: BLE001
                checks.append(Check(key, label, "fail", str(exc)[:500], asdict(sample_cmd)))
    return checks, details


async def verify_ai_live_call() -> Check:
    try:
        response = await call_llm(
            "你是 SkillForge 平台能力验证器，只输出 JSON。",
            "请返回 {\"ok\":true,\"capability\":\"ai_live_call\",\"message\":\"ready\"}。",
            max_tokens=80,
            temperature=0,
            timeout=20,
            json_mode=True,
            call_source="platform_capability_matrix",
            cost_context={"purpose": "platform_capability_verification", "profile": "cheap"},
            model_profile="cheap",
            require_system_config=False,
        )
        return Check(
            "ai_live_call",
            "AI live model call",
            "pass" if isinstance(response, dict) and response.get("ok") is True else "warn",
            "cheap profile 真实模型响应已返回" if response else "cheap profile 真实模型无有效响应",
            {
                "profile": "cheap",
                "response_type": type(response).__name__,
                "response": response if isinstance(response, dict) else str(response or "")[:200],
            },
        )
    except (LLMAuthError, LLMQuotaExceededError, LLMRateLimitError) as exc:
        return Check("ai_live_call", "AI live model call", "fail", redact_secret_text(exc), {"profile": "cheap", "status_code": getattr(exc, "status_code", None)})
    except Exception as exc:  # noqa: BLE001
        return Check("ai_live_call", "AI live model call", "fail", redact_secret_text(exc), {"profile": "cheap"})


def verify_sf_cli_project_upload_policy() -> Check:
    """Prove sf project doctor/submit preflight no longer blocks public provider endpoints.

    The actual upload endpoint and platform runtime checks are covered by the
    100-project AI takeover matrix.  This local CLI proof guards the user-facing
    `sf project submit` preflight path: frontend secrets still fail, while a
    public OpenAI/Anthropic/etc. endpoint without a key is allowed to reach the
    platform where Project Autowire/Gateway takes over bottom-layer AI calls.
    """

    try:
        sf_path = ROOT / "scripts" / "sf.py"
        spec = importlib.util.spec_from_file_location("sf_cli_matrix_probe", sf_path)
        if not spec or not spec.loader:
            raise RuntimeError(f"无法加载 {sf_path}")
        sf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sf)
        with tempfile.TemporaryDirectory(prefix="sf-cli-ai-takeover-") as tmp:
            root = Path(tmp)
            (root / "web").mkdir(parents=True)
            (root / "projectforge.yaml").write_text(
                "project_id: sf_cli_ai_takeover_probe\n"
                "name: sf CLI AI takeover probe\n"
                "kind: web_static\n"
                "entry: web/index.html\n"
                "capabilities:\n"
                "  - ai.cheap.generate\n",
                encoding="utf-8",
            )
            endpoint = "https://api.openai.com/v1/chat/completions"
            (root / "web" / "index.html").write_text(
                f"<html><script>fetch('{endpoint}',{{method:'POST',body:'{{}}'}})</script></html>",
                encoding="utf-8",
            )
            doctor = sf.project_doctor(SimpleNamespace(path=str(root), quiet=True))
            package, digest, manifest = sf.build_project_package(root, manifest_override=None)
            with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as tar:
                html = tar.extractfile("web/index.html").read().decode("utf-8")
            security = doctor.get("static_security") or {}
            evidence = {
                "project_id": manifest.get("project_id"),
                "doctor_ok": doctor.get("ok") is True,
                "package_hash": digest,
                "package_bytes": len(package),
                "direct_ai_endpoint_count": security.get("direct_ai_endpoint_count"),
                "ai_endpoint_policy": security.get("ai_endpoint_policy"),
                "ai_takeover_status": (doctor.get("ai_takeover") or {}).get("status"),
                "endpoint_preserved_for_platform_proxy": endpoint in html,
                "frontend_secret_present": "sk-" in html or "Authorization" in html,
            }
            passed = (
                evidence["doctor_ok"] is True
                and int(evidence["direct_ai_endpoint_count"] or 0) >= 1
                and evidence["ai_endpoint_policy"] == "allowed_with_project_gateway_proxy"
                and evidence["ai_takeover_status"] == "gateway_proxy_candidate"
                and evidence["endpoint_preserved_for_platform_proxy"] is True
                and evidence["frontend_secret_present"] is False
                and str(digest).startswith("sha256:")
            )
            return Check(
                "sf_project_cli_ai_takeover_preflight",
                "sf project submit AI takeover preflight",
                "pass" if passed else "warn",
                (
                    f"sf project doctor 允许 {evidence['direct_ai_endpoint_count']} 个公开模型端点作为 "
                    f"{evidence['ai_takeover_status']}，包 hash {digest[:18]}..."
                ),
                evidence,
            )
    except SystemExit as exc:
        return Check(
            "sf_project_cli_ai_takeover_preflight",
            "sf project submit AI takeover preflight",
            "fail",
            f"sf project doctor/submit preflight 阻断了公开模型端点: {exc}",
            {"blocked": True, "message": str(exc)},
        )
    except Exception as exc:  # noqa: BLE001
        return Check(
            "sf_project_cli_ai_takeover_preflight",
            "sf project submit AI takeover preflight",
            "fail",
            f"sf CLI 项目预检验证失败: {exc}",
            {"error": str(exc)[:500]},
        )


async def verify_projects(
    github_eval_path: Path,
    github_registration_path: Path,
    type_matrix_path: Path,
    scale_inventory_path: Path,
    ai_takeover_path: Path,
) -> tuple[list[Check], dict[str, Any]]:
    checks: list[Check] = []
    user = SimpleNamespace(id="admin", username="admin", name="管理员", role="admin", department="AI小组", can_view_all=True, state="active", is_active=True)
    async with async_session_factory() as db:
        status = await project_service.get_project_runtime_status(db, user)
        listed = await project_service.list_projects(db, user, scope="mine", search="GitHub", include_playbooks=False, page=1, page_size=100)
    details: dict[str, Any] = {"runtime_status": status, "github_mine_total": listed.get("pagination", {}).get("total")}
    sf_cli_check = verify_sf_cli_project_upload_policy()
    checks.append(sf_cli_check)
    details["sf_project_cli_upload_policy"] = sf_cli_check.evidence
    capacity = status.get("capacity") or {}
    checks.append(
        Check(
            "project_capacity",
            "Project capacity",
            "pass" if int(capacity.get("target_concurrent_runs") or 0) >= 100 and int(capacity.get("target_visible_projects") or 0) >= 1000 else "warn",
            f"并发目标 {capacity.get('target_concurrent_runs')}，项目目录目标 {capacity.get('target_visible_projects')}",
            capacity,
        )
    )
    checks.append(
        Check(
            "project_github_registered",
            "GitHub project registrations",
            "pass" if int(listed.get("pagination", {}).get("total") or 0) >= 1 else "warn",
            f"mine/GitHub 可见项目 {listed.get('pagination', {}).get('total')} 个",
            {"items": [{"id": item.get("id"), "status": (item.get("latest_run") or {}).get("status")} for item in listed.get("items", [])[:20]]},
        )
    )
    if github_eval_path.is_file():
        github_eval = json.loads(github_eval_path.read_text(encoding="utf-8"))
        details["github_eval_summary"] = github_eval.get("summary")
        checks.append(
            Check(
                "external_github_eval",
                "External GitHub Codex/AI eval",
                "pass" if int((github_eval.get("summary") or {}).get("runnable_count") or 0) >= 1 else "warn",
                f"已实测外部 GitHub 项目 {((github_eval.get('summary') or {}).get('repo_count'))} 个，可运行 {((github_eval.get('summary') or {}).get('runnable_count'))} 个",
                github_eval.get("summary"),
            )
        )
    else:
        checks.append(Check("external_github_eval", "External GitHub Codex/AI eval", "warn", f"未找到 {github_eval_path}"))
    if github_registration_path.is_file():
        github_registration = json.loads(github_registration_path.read_text(encoding="utf-8"))
        summary = github_registration.get("summary") or github_registration
        details["github_registration_summary"] = summary
        registered_count = int(summary.get("registered_count") or 0)
        expanded_variant_count = int(summary.get("expanded_variant_count") or 0)
        source_repo_count = int(summary.get("source_repo_count") or 0)
        runtime_ready_count = int(summary.get("normal_runtime_ready_count") or 0)
        result_pass_count = int(summary.get("result_eval_pass_count") or 0)
        service_converted_count = int(summary.get("service_converted_count") or 0)
        gateway_injected_count = int(summary.get("gateway_injected_count") or 0)
        service_invoked_count = int(summary.get("service_invoked_count") or 0)
        service_ready_count = int(summary.get("service_result_ready_count") or 0)
        checks.append(
            Check(
                "github_codex_adapter_variants_100",
                "100 GitHub Codex adapter variants",
                "pass"
                if min(
                    registered_count,
                    expanded_variant_count,
                    runtime_ready_count,
                    result_pass_count,
                    service_converted_count,
                    gateway_injected_count,
                    service_invoked_count,
                    service_ready_count,
                )
                >= 100
                and source_repo_count >= 8
                else "warn",
                (
                    f"GitHub 来源变体：源仓库 {source_repo_count}，注册 {registered_count}，可运行 {runtime_ready_count}，"
                    f"结果通过 {result_pass_count}，转服务 {service_converted_count}，Gateway 注入 {gateway_injected_count}，"
                    f"服务调用 {service_invoked_count}，服务结果通过 {service_ready_count}"
                ),
                summary,
            )
        )
    else:
        checks.append(Check("github_codex_adapter_variants_100", "100 GitHub Codex adapter variants", "warn", f"未找到 {github_registration_path}"))
    if type_matrix_path.is_file():
        type_matrix = json.loads(type_matrix_path.read_text(encoding="utf-8"))
        summary = type_matrix.get("summary") or {}
        details["codex_type_matrix_summary"] = summary
        type_count = int(summary.get("type_count") or 0)
        uploaded_count = int(summary.get("uploaded_count") or 0)
        runtime_ready_count = int(summary.get("normal_runtime_ready_count") or 0)
        result_pass_count = int(summary.get("result_eval_pass_count") or 0)
        service_invoked_count = int(summary.get("service_invoked_count") or 0)
        service_ready_count = int(summary.get("service_result_ready_count") or 0)
        checks.append(
            Check(
                "codex_project_type_matrix_100",
                "100 Codex project type matrix",
                "pass" if min(type_count, uploaded_count, runtime_ready_count, result_pass_count, service_invoked_count, service_ready_count) >= 100 else "warn",
                (
                    f"100 类型适配：定义 {type_count}，上传 {uploaded_count}，可运行 {runtime_ready_count}，"
                    f"结果评估通过 {result_pass_count}，转服务调用 {service_invoked_count}，服务结果通过 {service_ready_count}"
                ),
                summary,
            )
        )
    else:
        checks.append(Check("codex_project_type_matrix_100", "100 Codex project type matrix", "warn", f"未找到 {type_matrix_path}"))
    if scale_inventory_path.is_file():
        scale_inventory = json.loads(scale_inventory_path.read_text(encoding="utf-8"))
        summary = scale_inventory.get("summary") or {}
        details["project_scale_inventory_summary"] = summary
        scale_project_count = int(summary.get("scale_project_count") or 0)
        target_project_count = int(summary.get("target_project_count") or 0)
        department_count = int(summary.get("department_count") or 0)
        sample_run_count = int(summary.get("sample_run_count") or 0)
        sample_input_count = int(summary.get("sample_input_event_count") or 0)
        sample_output_count = int(summary.get("sample_output_event_count") or 0)
        department_visible = int(summary.get("department_visible_projects") or 0)
        admin_visible = int(summary.get("admin_visible_projects") or 0)
        department_latest_run_count = int(summary.get("department_latest_run_count") or 0)
        checks.append(
            Check(
                "project_enterprise_scale_inventory_1000",
                "1000 project inventory scale",
                "pass"
                if min(scale_project_count, target_project_count, admin_visible) >= 1000
                and department_count >= 10
                and department_visible >= 100
                and sample_run_count >= 100
                and sample_input_count >= 100
                and sample_output_count >= 100
                and department_latest_run_count >= 10
                and summary.get("supports_hundreds_to_thousands_projects") is True
                and summary.get("supports_100_concurrent_runs") is True
                else "warn",
                (
                    f"千级目录：规模项目 {scale_project_count}/{target_project_count}，部门 {department_count}，"
                    f"部门可见 {department_visible}，部门可见运行 {department_latest_run_count}，"
                    f"样本运行 {sample_run_count}，输入 {sample_input_count}，输出 {sample_output_count}"
                ),
                summary,
            )
        )
    else:
        checks.append(Check("project_enterprise_scale_inventory_1000", "1000 project inventory scale", "warn", f"未找到 {scale_inventory_path}"))
    if ai_takeover_path.is_file():
        ai_takeover = json.loads(ai_takeover_path.read_text(encoding="utf-8"))
        summary = ai_takeover.get("summary") or {}
        details["project_ai_takeover_summary"] = summary
        type_count = int(summary.get("type_count") or 0)
        direct_ai_detected_count = int(summary.get("direct_ai_detected_count") or 0)
        gateway_injected_count = int(summary.get("gateway_injected_count") or 0)
        ai_takeover_pass_count = int(summary.get("ai_takeover_pass_count") or 0)
        runtime_pass_count = int(summary.get("runtime_pass_count") or 0)
        service_invoked_count = int(summary.get("service_invoked_count") or 0)
        service_ready_count = int(summary.get("service_result_ready_count") or 0)
        provider_count = len(summary.get("by_provider") or {})
        call_style_count = len(summary.get("by_call_style") or {})
        checks.append(
            Check(
                "project_ai_takeover_matrix_100",
                "100 bottom AI takeover project variants",
                "pass"
                if min(
                    type_count,
                    direct_ai_detected_count,
                    gateway_injected_count,
                    ai_takeover_pass_count,
                    runtime_pass_count,
                    service_invoked_count,
                    service_ready_count,
                )
                >= 100
                and provider_count >= 5
                and call_style_count >= 5
                else "warn",
                (
                    f"AI 接管：变体 {type_count}，检测端点 {direct_ai_detected_count}，Gateway 注入 {gateway_injected_count}，"
                    f"接管通过 {ai_takeover_pass_count}，服务调用 {service_invoked_count}，服务结果通过 {service_ready_count}"
                ),
                summary,
            )
        )
    else:
        checks.append(Check("project_ai_takeover_matrix_100", "100 bottom AI takeover project variants", "warn", f"未找到 {ai_takeover_path}"))
    return checks, details


def overall_status(checks: list[Check]) -> str:
    statuses = {item.status for item in checks}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def make_html(result: dict[str, Any]) -> str:
    rows = []
    for item in result.get("checks") or []:
        rows.append(f"<tr><td>{item['key']}</td><td>{item['label']}</td><td>{item['status']}</td><td>{item['detail']}</td></tr>")
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>SkillForge 平台能力矩阵验证</title>
<style>body{font-family:system-ui;margin:24px;background:#0f172a;color:#e2e8f0}table{width:100%;border-collapse:collapse}td,th{border:1px solid #334155;padding:8px}.pass{color:#86efac}.warn{color:#fde68a}.fail{color:#fca5a5}pre{white-space:pre-wrap;background:#111827;padding:16px;border-radius:12px}</style></head><body>
<h1>SkillForge 平台能力矩阵验证</h1><p>覆盖 AI/MCP/外部 GitHub/项目 Gateway/报告待办学习循环。默认不触发付费模型或真实外部写入。</p>
<table><thead><tr><th>Key</th><th>能力</th><th>状态</th><th>说明</th></tr></thead><tbody>""" + "\n".join(rows) + """</tbody></table>
<button data-sf-report="平台能力矩阵验证已进入改进循环">沉淀平台能力验证报告</button>
<pre>""" + json.dumps(result, ensure_ascii=False, indent=2) + """</pre></body></html>"""


def build_package(project_id: str, result: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    html = make_html(result).encode("utf-8")
    manifest = {
        "project_id": project_id,
        "name": "SkillForge 平台能力矩阵验证",
        "description": "验证 AI、MCP、外部 GitHub Codex/AI 项目、Project Gateway、报告待办和学习循环，并形成平台迭代证据。",
        "kind": "web_static",
        "entry": "web/index.html",
        "visibility": "department",
        "department": "AI小组",
        "capabilities": ["ai.cheap.generate", "ai.generate"],
        "metadata": {
            "source": "platform_capability_matrix",
            "overall_status": result.get("overall_status"),
            "runtime_evaluation": {
                "status": result.get("overall_status"),
                "normal_run_ready": result.get("overall_status") in {"pass", "warn"},
                "summary": "平台能力矩阵页面已生成，可作为项目服务打开并把报告/待办写入平台循环。",
            },
        },
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False).encode("utf-8")
        info = tarfile.TarInfo("projectforge.yaml")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        info = tarfile.TarInfo("web/index.html")
        info.size = len(html)
        tar.addfile(info, io.BytesIO(html))
    return buf.getvalue(), manifest


def collect_result_proofs(result: dict[str, Any]) -> list[dict[str, Any]]:
    proofs: list[dict[str, Any]] = [
        {
            "type": "platform_capability_matrix",
            "title": "能力矩阵汇总",
            "overall_status": result.get("overall_status"),
            "generated_at": result.get("generated_at"),
        }
    ]
    for check in result.get("checks") or []:
        evidence = check.get("evidence") if isinstance(check, dict) else None
        if isinstance(evidence, dict) and evidence.get("proof"):
            proofs.append(
                {
                    "type": "mcp_proof",
                    "title": check.get("label"),
                    "status": check.get("status"),
                    "proof": evidence.get("proof"),
                    "tool": evidence.get("tool"),
                    "platform": evidence.get("platform"),
                    "count": evidence.get("count"),
                }
            )
    mcp_summary = ((result.get("details") or {}).get("mcp") or {}).get("catalog_summary") or {}
    if mcp_summary:
        proofs.append(
            {
                "type": "mcp_catalog_summary",
                "title": "MCP 工具目录摘要",
                "tool_count": mcp_summary.get("tool_count"),
                "write_meta_gaps": mcp_summary.get("write_meta_gaps"),
                "by_platform": mcp_summary.get("by_platform"),
            }
        )
    sf_cli_policy = ((result.get("details") or {}).get("projects") or {}).get("sf_project_cli_upload_policy") or {}
    if sf_cli_policy:
        proofs.append({"type": "sf_project_cli_ai_takeover_preflight", "title": "sf 上传公开模型端点预检摘要", **sf_cli_policy})
    github_summary = ((result.get("details") or {}).get("projects") or {}).get("github_eval_summary") or {}
    if github_summary:
        proofs.append({"type": "external_github_eval_summary", "title": "外部 GitHub 项目评估摘要", **github_summary})
    github_registration_summary = ((result.get("details") or {}).get("projects") or {}).get("github_registration_summary") or {}
    if github_registration_summary:
        proofs.append({"type": "github_codex_adapter_variants_summary", "title": "100 个 GitHub 来源 Codex 项目变体适配摘要", **github_registration_summary})
    type_matrix_summary = ((result.get("details") or {}).get("projects") or {}).get("codex_type_matrix_summary") or {}
    if type_matrix_summary:
        proofs.append({"type": "codex_project_type_matrix_summary", "title": "100 类型 Codex 项目适配摘要", **type_matrix_summary})
    scale_inventory_summary = ((result.get("details") or {}).get("projects") or {}).get("project_scale_inventory_summary") or {}
    if scale_inventory_summary:
        proofs.append({"type": "project_enterprise_scale_inventory_summary", "title": "千级项目目录和部门隔离摘要", **scale_inventory_summary})
    ai_takeover_summary = ((result.get("details") or {}).get("projects") or {}).get("project_ai_takeover_summary") or {}
    if ai_takeover_summary:
        proofs.append({"type": "project_ai_takeover_matrix_summary", "title": "上传项目底层 AI 接管摘要", **ai_takeover_summary})
    return proofs[:50]


async def register_result(result: dict[str, Any], *, project_id: str) -> dict[str, Any]:
    package, manifest = build_package(project_id, result)
    user = SimpleNamespace(id="admin", username="admin", name="管理员", role="admin", department="AI小组", can_view_all=True, state="active", is_active=True)
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    async with async_session_factory() as db:
        uploaded = await project_service.upload_project_package(
            db,
            user,
            package_bytes=package,
            manifest_raw=manifest,
            package_hash="sha256:" + hashlib.sha256(package).hexdigest(),
            submit_metadata={"platform_capability_matrix": {"batch_id": batch_id, "overall_status": result.get("overall_status")}},
        )
        run = await project_service.create_project_run(
            db,
            user,
            project_id,
            {"request_id": f"platform-capability-open-{batch_id}", "params": {"overall_status": result.get("overall_status")}},
        )
        output = {
            "summary": f"平台能力矩阵验证完成：{result.get('overall_status')}；AI/MCP/外部/Gateway/学习循环已记录。",
            "capability_matrix": result,
            "proofs": collect_result_proofs(result),
            "reports": [
                {
                    "title": "SkillForge 平台能力矩阵验证",
                    "summary": "AI/MCP/外部 GitHub/Project Gateway/报告待办/学习循环验证结果已沉淀。",
                    "metrics": [
                        {"name": "checks", "value": len(result.get("checks") or [])},
                        {"name": "overall_status", "value": result.get("overall_status")},
                    ],
                }
            ],
            "todos": [
                {
                    "kind": "review",
                    "title": "按能力矩阵补齐企业级项目运行平台",
                    "summary": "优先补 AI cheap profile 配置、MCP 真实样本覆盖、长期进程沙箱、外部项目运行证据和平台可观测性。",
                    "reviewers": ["admin"],
                    "payload": {"project_id": project_id, "overall_status": result.get("overall_status"), "checks": result.get("checks")},
                }
            ],
        }
        ingested = await project_service.ingest_project_output(
            db,
            user,
            run["id"],
            {
                "request_id": f"platform-capability-output-{batch_id}",
                "input": {"source": "platform_capability_matrix"},
                "output": output,
                "metadata": {"source": "platform_capability_matrix", "department": "AI小组"},
            },
            auto_analyze=False,
        )
        await db.commit()
    return {"project_id": uploaded.get("id"), "entry_url": uploaded.get("entry_url"), "run_id": run.get("id"), "run_status": ingested.get("status"), "report_count": ingested.get("report_count"), "todo_count": ingested.get("todo_count")}


async def build_matrix(args: argparse.Namespace) -> dict[str, Any]:
    checks: list[Check] = []
    details: dict[str, Any] = {}
    ai_checks, ai_details = await verify_ai_profiles()
    checks.extend(ai_checks)
    details["ai"] = ai_details
    mcp_checks, mcp_details = verify_mcp(include_samples=not args.skip_mcp_samples)
    checks.extend(mcp_checks)
    details["mcp"] = mcp_details
    project_checks, project_details = await verify_projects(args.github_eval, args.github_registration, args.type_matrix, args.scale_inventory, args.ai_takeover)
    checks.extend(project_checks)
    details["projects"] = project_details
    if not args.call_ai:
        checks.append(Check("ai_live_call", "AI live model call", "warn", "未触发真实模型调用，避免未确认扣费；加 --call-ai 后可验证 cheap/default 真实响应"))
    else:
        checks.append(await verify_ai_live_call())
    result = {
        "ok": True,
        "generated_at": now_iso(),
        "overall_status": overall_status(checks),
        "checks": [asdict(item) for item in checks],
        "details": details,
        "safety": {"model_calls_requested": bool(args.call_ai), "real_external_writes": False, "mcp_write_sample": "dry-run only"},
    }
    return result


async def run_main(args: argparse.Namespace) -> dict[str, Any]:
    result = await build_matrix(args)
    if args.register:
        result["registration"] = await register_result(result, project_id=args.project_id)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify SkillForge platform capability matrix")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--github-eval", type=Path, default=DEFAULT_GITHUB_EVAL)
    parser.add_argument("--github-registration", type=Path, default=DEFAULT_GITHUB_REGISTRATION)
    parser.add_argument("--type-matrix", type=Path, default=DEFAULT_TYPE_MATRIX)
    parser.add_argument("--scale-inventory", type=Path, default=DEFAULT_SCALE_INVENTORY)
    parser.add_argument("--ai-takeover", type=Path, default=DEFAULT_AI_TAKEOVER)
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--project-id", default="platform-capability-matrix")
    parser.add_argument("--skip-mcp-samples", action="store_true")
    parser.add_argument("--call-ai", action="store_true", help="Reserved for explicit approval; real model calls may cost money")
    args = parser.parse_args()
    result = asyncio.run(run_main(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
