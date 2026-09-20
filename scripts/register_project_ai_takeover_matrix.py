#!/usr/bin/env python3
"""Register generated Codex projects that try to call external AI providers.

The generated packages intentionally contain public model endpoint URLs but no
secrets.  Uploading them verifies that SkillForge detects direct provider calls,
injects Project Gateway + Autowire, marks bottom-layer AI takeover as pass, and
then runs the same input/output/service/learning trace path as ordinary
uploaded projects.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import sys
import tarfile
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml

from app.database import async_session_factory
from app.projects import service as project_service

DEFAULT_OUTPUT = ROOT / "artifacts" / "project-checks" / "project-ai-takeover-matrix-result.json"


@dataclass(frozen=True)
class AiTakeoverSpec:
    index: int
    project_id: str
    provider: str
    endpoint: str
    call_style: str
    app_shape: str
    file_shape: str
    department: str


PROVIDERS = [
    ("openai", "https://api.openai.com/v1/chat/completions"),
    ("anthropic", "https://api.anthropic.com/v1/messages"),
    ("gemini", "https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent"),
    ("dashscope", "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"),
    ("deepseek", "https://api.deepseek.com/v1/chat/completions"),
]
CALL_STYLES = ["fetch", "xhr", "beacon", "worker_fetch", "module_fetch"]
APP_SHAPES = ["chat_agent", "report_builder", "agent_console", "form_workflow", "dashboard"]
FILE_SHAPES = ["inline_html", "entry_script", "nested_asset", "worker", "module"]
DEPARTMENTS = ["AI小组", "研发部", "运营部", "客服部", "数据部", "安全部", "市场部", "供应链部", "财务部", "法务部"]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def build_specs(limit: int = 100) -> list[AiTakeoverSpec]:
    specs: list[AiTakeoverSpec] = []
    for idx in range(max(0, limit)):
        provider, endpoint = PROVIDERS[idx % len(PROVIDERS)]
        call_style = CALL_STYLES[(idx // len(PROVIDERS)) % len(CALL_STYLES)]
        app_shape = APP_SHAPES[(idx // max(1, limit // len(APP_SHAPES))) % len(APP_SHAPES)]
        file_shape = FILE_SHAPES[(idx // 4) % len(FILE_SHAPES)]
        specs.append(
            AiTakeoverSpec(
                index=idx + 1,
                project_id=f"ai_takeover_{idx + 1:03d}",
                provider=provider,
                endpoint=endpoint,
                call_style=call_style,
                app_shape=app_shape,
                file_shape=file_shape,
                department=DEPARTMENTS[idx % len(DEPARTMENTS)],
            )
        )
    return specs


def _provider_body(spec: AiTakeoverSpec) -> str:
    if spec.provider == "anthropic":
        return "{model:'claude-3-haiku',max_tokens:128,messages:[{role:'user',content:prompt}]}"
    if spec.provider == "gemini":
        return "{contents:[{parts:[{text:prompt}]}]}"
    if spec.provider == "dashscope":
        return "{model:'qwen-turbo',input:{prompt}}"
    return "{model:'demo-chat',messages:[{role:'user',content:prompt}]}"


def _call_js(spec: AiTakeoverSpec) -> tuple[str, dict[str, str]]:
    body = _provider_body(spec)
    endpoint = spec.endpoint
    if spec.call_style == "xhr":
        return (
            f"""
export function callProvider(prompt) {{
  return new Promise((resolve) => {{
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '{endpoint}');
    xhr.onload = () => resolve({{ ok: xhr.status >= 200 && xhr.status < 500, provider: '{spec.provider}' }});
    xhr.onerror = () => resolve({{ ok: false, provider: '{spec.provider}' }});
    xhr.send(JSON.stringify({body}));
  }});
}}
""",
            {},
        )
    if spec.call_style == "beacon":
        return (
            f"""
export function callProvider(prompt) {{
  const ok = navigator.sendBeacon('{endpoint}', JSON.stringify({body}));
  return Promise.resolve({{ ok, provider: '{spec.provider}', mode: 'beacon' }});
}}
""",
            {},
        )
    if spec.call_style == "worker_fetch":
        worker = f"""
self.onmessage = async (event) => {{
  const prompt = String((event.data && event.data.prompt) || 'worker prompt');
  const res = await fetch('{endpoint}', {{ method: 'POST', body: JSON.stringify({_provider_body(spec)}) }});
  self.postMessage({{ ok: !!res, provider: '{spec.provider}', mode: 'worker_fetch' }});
}};
"""
        return (
            """
export function callProvider(prompt) {
  return new Promise((resolve) => {
    const worker = new Worker('./worker.js', { type: 'module' });
    worker.onmessage = (event) => resolve(event.data || {});
    worker.onerror = () => resolve({ ok: false, mode: 'worker_fetch' });
    worker.postMessage({ prompt });
  });
}
""",
            {"web/assets/worker.js": worker},
        )
    if spec.call_style == "module_fetch":
        helper = f"""
export async function providerCall(prompt) {{
  const res = await fetch('{endpoint}', {{ method: 'POST', body: JSON.stringify({_provider_body(spec)}) }});
  return {{ ok: !!res, provider: '{spec.provider}', mode: 'module_fetch' }};
}}
"""
        return (
            """
export async function callProvider(prompt) {
  const mod = await import('./provider-module.js');
  return mod.providerCall(prompt);
}
""",
            {"web/assets/provider-module.js": helper},
        )
    return (
        f"""
export async function callProvider(prompt) {{
  const res = await fetch('{endpoint}', {{ method: 'POST', body: JSON.stringify({body}) }});
  return {{ ok: !!res, provider: '{spec.provider}', mode: 'fetch' }};
}}
""",
        {},
    )


def build_project_package(spec: AiTakeoverSpec) -> tuple[bytes, dict[str, Any]]:
    call_js, extra_files = _call_js(spec)
    inline_call = ""
    if spec.file_shape == "inline_html":
        inline_call = f"""
<script>
window.inlineProviderCall = async function(prompt) {{
  return fetch('{spec.endpoint}', {{ method: 'POST', body: JSON.stringify({_provider_body(spec)}) }});
}};
</script>
"""
    html = f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <title>AI takeover {spec.index:03d}</title>
    <script type="module" src="/assets/app.js"></script>
    {inline_call}
  </head>
  <body>
    <main data-provider="{spec.provider}" data-call-style="{spec.call_style}" data-app-shape="{spec.app_shape}">
      <h1>Codex AI 接管样本 {spec.index:03d}</h1>
      <p>该生成项目尝试直连 {spec.provider} 模型端点；平台上传后必须由 Project Autowire 接管到底层 AI Gateway。</p>
      <form data-sf-input="ai-takeover">
        <input name="prompt" value="分析这个上传项目是否被平台接管">
        <button type="submit">运行</button>
      </form>
      <button data-sf-report="AI 接管样本已生成报告">沉淀报告</button>
    </main>
  </body>
</html>
"""
    app_js = f"""
import {{ callProvider }} from './provider-call.js';

const state = {{ provider: '{spec.provider}', callStyle: '{spec.call_style}', appShape: '{spec.app_shape}' }};
window.codexAiTakeoverSample = state;
document.addEventListener('submit', async (event) => {{
  event.preventDefault();
  const prompt = new FormData(event.target).get('prompt') || '默认输入';
  const result = await callProvider(String(prompt));
  document.body.dataset.providerResult = JSON.stringify(result);
}});
"""
    manifest = {
        "project_id": spec.project_id,
        "name": f"AI 接管矩阵 · {spec.provider} · {spec.call_style}",
        "description": "Codex 生成项目底层 AI 接管验证样本：前端包含公开模型端点但不含密钥，必须由 SkillForge Project Gateway/Autowire 代理。",
        "kind": "web_static",
        "entry": "web/index.html",
        "visibility": "department",
        "department": spec.department,
        "capabilities": ["ai.cheap.generate", "ai.generate"],
        "metadata": {
            "source": "project_ai_takeover_matrix",
            "provider": spec.provider,
            "call_style": spec.call_style,
            "app_shape": spec.app_shape,
            "file_shape": spec.file_shape,
            "direct_provider_endpoint_expected": True,
        },
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        files = {
            "projectforge.yaml": yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
            "web/index.html": html,
            "web/assets/app.js": app_js,
            "web/assets/provider-call.js": call_js,
            **extra_files,
        }
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue(), manifest


def _check_status(metadata: dict[str, Any], key: str) -> str | None:
    for check in ((metadata.get("runtime_evaluation") or {}).get("checks") or []):
        if isinstance(check, dict) and check.get("key") == key:
            return str(check.get("status") or "")
    return None


def service_payload_for_spec(spec: AiTakeoverSpec, *, batch_id: str) -> dict[str, Any]:
    request_id = f"ai-takeover-service-{batch_id}-{spec.index:03d}"
    output = {
        "summary": f"{spec.project_id} 已证明 {spec.provider}/{spec.call_style} 前端模型调用由平台接管。",
        "ai_takeover_spec": asdict(spec),
        "reports": [
            {
                "title": f"AI 接管验证 · {spec.provider} · {spec.call_style}",
                "summary": "上传项目包含公开模型端点，运行时由 Project Autowire/Gateway 接管，密钥仍只在平台侧。",
                "payload": {"project_id": spec.project_id, "provider": spec.provider, "call_style": spec.call_style},
            }
        ],
        "todos": [
            {
                "kind": "review",
                "title": f"复核 {spec.provider} {spec.call_style} 接管 trace",
                "summary": "继续沉淀不同 Codex 生成项目的 provider SDK/Worker/PWA 调用签名。",
                "payload": {"project_id": spec.project_id, "provider": spec.provider, "call_style": spec.call_style},
            }
        ],
        "proofs": [
            {
                "type": "project_ai_takeover_matrix",
                "project_id": spec.project_id,
                "provider": spec.provider,
                "call_style": spec.call_style,
                "credential_location": "platform_only",
            }
        ],
    }
    return {
        "request_id": request_id,
        "input": {"provider": spec.provider, "call_style": spec.call_style, "department": spec.department},
        "params": {"matrix": "project_ai_takeover", "file_shape": spec.file_shape, "app_shape": spec.app_shape},
        "prompt": f"验证上传项目 {spec.project_id} 的底层 AI 调用已由平台接管。",
        "output": output,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type_count": len(rows),
        "uploaded_count": sum(1 for row in rows if row.get("uploaded") is True),
        "direct_ai_detected_count": sum(1 for row in rows if int(row.get("direct_ai_endpoint_count") or 0) > 0),
        "gateway_injected_count": sum(1 for row in rows if row.get("gateway_injected") is True),
        "ai_takeover_pass_count": sum(1 for row in rows if row.get("ai_takeover_status") == "pass"),
        "runtime_pass_count": sum(1 for row in rows if row.get("runtime_status") == "pass"),
        "service_invoked_count": sum(1 for row in rows if row.get("service_invoked") is True),
        "service_ok_count": sum(1 for row in rows if row.get("service_ok") is True),
        "service_result_ready_count": sum(1 for row in rows if row.get("service_result_ready") is True),
        "by_provider": dict(Counter(row.get("provider") for row in rows)),
        "by_call_style": dict(Counter(row.get("call_style") for row in rows)),
        "by_app_shape": dict(Counter(row.get("app_shape") for row in rows)),
        "by_file_shape": dict(Counter(row.get("file_shape") for row in rows)),
    }


async def register_ai_takeover_matrix(*, limit: int, auto_analyze: bool) -> dict[str, Any]:
    specs = build_specs(limit)
    user = SimpleNamespace(id="admin", username="admin", name="管理员", role="admin", department="AI小组", can_view_all=True, state="active", is_active=True)
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rows: list[dict[str, Any]] = []
    async with async_session_factory() as db:
        for spec in specs:
            package, manifest = build_project_package(spec)
            uploaded = await project_service.upload_project_package(
                db,
                user,
                package_bytes=package,
                manifest_raw=manifest,
                package_hash="sha256:" + hashlib.sha256(package).hexdigest(),
                submit_metadata={"project_ai_takeover_matrix": {"batch_id": batch_id, "index": spec.index}},
            )
            metadata = uploaded.get("metadata") or {}
            security = metadata.get("package_security") or {}
            run = await project_service.create_project_run(
                db,
                user,
                spec.project_id,
                {"request_id": f"ai-takeover-open-{batch_id}-{spec.index:03d}", "params": asdict(spec)},
            )
            await project_service.record_project_input_event(
                db,
                user,
                run["id"],
                {
                    "request_id": f"ai-takeover-input-{batch_id}-{spec.index:03d}",
                    "input": {"provider": spec.provider, "call_style": spec.call_style, "department": spec.department},
                    "metadata": {"source": "project_ai_takeover_matrix", "department": spec.department},
                },
            )
            ingested = await project_service.ingest_project_output(
                db,
                user,
                run["id"],
                {
                    "request_id": f"ai-takeover-output-{batch_id}-{spec.index:03d}",
                    "input": {"provider": spec.provider, "call_style": spec.call_style, "department": spec.department},
                    "output": {
                        "summary": f"{spec.project_id} 上传后已检测到底层 AI 端点并注入 Project Autowire。",
                        "reports": [{"title": f"{spec.provider} 接管报告", "summary": "输入输出已进入平台循环。"}],
                        "todos": [{"kind": "review", "title": f"复核 {spec.provider} 接管", "summary": "确认生成项目的 AI 调用 trace。"}],
                        "proofs": [{"type": "ai_takeover_upload", "project_id": spec.project_id, "credential_location": "platform_only"}],
                    },
                    "metadata": {"source": "project_ai_takeover_matrix", "department": spec.department},
                },
                auto_analyze=auto_analyze,
            )
            service_payload = service_payload_for_spec(spec, batch_id=batch_id)
            service_payload["auto_analyze"] = auto_analyze
            service_invoked = await project_service.invoke_project_service(db, user, spec.project_id, service_payload)
            service_run = service_invoked.get("run") or {}
            service_eval = ((service_run.get("ai_summary") or {}).get("result_evaluation") or {})
            rows.append(
                {
                    **asdict(spec),
                    "uploaded": True,
                    "entry_url": uploaded.get("entry_url"),
                    "run_id": run.get("id"),
                    "ingest_status": ingested.get("status"),
                    "direct_ai_endpoint_count": security.get("direct_ai_endpoint_count"),
                    "direct_ai_endpoints": security.get("direct_ai_endpoints"),
                    "gateway_injected": (metadata.get("gateway_bootstrap") or {}).get("injected"),
                    "runtime_status": (metadata.get("runtime_evaluation") or {}).get("status"),
                    "ai_takeover_status": _check_status(metadata, "ai_provider_gateway_proxy"),
                    "service_invoked": True,
                    "service_ok": bool(service_invoked.get("ok")),
                    "service_run_id": service_invoked.get("project_run_id"),
                    "service_result_status": service_eval.get("status"),
                    "service_result_ready": service_eval.get("normal_result_ready"),
                    "service_trace_url": (service_invoked.get("service") or {}).get("trace_url"),
                }
            )
        await db.commit()
    return {
        "ok": True,
        "generated_at": now_iso(),
        "batch_id": batch_id,
        "auto_analyze": auto_analyze,
        "summary": summarize(rows),
        "results": rows,
        "safety": {"model_calls_requested": auto_analyze, "external_network_used": False, "real_external_writes": False, "frontend_secrets_allowed": False},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Register AI provider takeover project matrix")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--auto-analyze", action="store_true", help="May trigger platform AI analysis calls")
    args = parser.parse_args()
    result = asyncio.run(register_ai_takeover_matrix(limit=args.limit, auto_analyze=args.auto_analyze))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
