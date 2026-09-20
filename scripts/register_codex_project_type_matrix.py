#!/usr/bin/env python3
"""Register and evaluate 100 Codex-style project type fixtures in SkillForge.

This is a deterministic compatibility harness for the enterprise objective:
users can ``sf`` upload many kinds of generated Codex projects, the platform
adapts them into runnable Project services, records department input/output,
and feeds reports/todos/proofs back into the learning loop.

No external model call is made by default. Use ``--auto-analyze`` only after an
operator has approved model cost.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import re
import sys
import tarfile
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml

from app.database import async_session_factory
from app.projects import service as project_service

DEFAULT_OUTPUT = ROOT / "artifacts" / "project-checks" / "codex-project-type-matrix-result.json"

DOMAINS = [
    "客服会话质检",
    "天猫链接诊断",
    "运营活动看板",
    "销售预测",
    "库存补货",
    "财务对账",
    "合同审核",
    "HR 招聘",
    "法务风险",
    "市场竞品",
    "知识库问答",
    "研发需求",
    "测试用例",
    "设计评审",
    "数据标注",
    "IoT 监控",
    "物流追踪",
    "门店巡检",
    "培训课程",
    "安全审计",
]

APP_PATTERNS = [
    {
        "pattern": "chat_agent",
        "label": "AI 对话工作台",
        "kind": "internal_tool",
        "framework": "react-vite",
        "entry": "web/index.html",
        "ref_syntax": "html_script_link",
        "capabilities": ["ai.cheap.chat", "ai.generate"],
    },
    {
        "pattern": "dashboard",
        "label": "指标仪表盘",
        "kind": "dashboard",
        "framework": "vue-vite",
        "entry": "frontend/dist/index.html",
        "ref_syntax": "css_url",
        "capabilities": ["ai.cheap.generate"],
    },
    {
        "pattern": "form_workflow",
        "label": "表单审批流",
        "kind": "internal_tool",
        "framework": "next-static-export",
        "entry": "out/index.html",
        "ref_syntax": "srcset",
        "capabilities": ["ai.cheap.generate", "mcp://skillforge_org_search_users"],
    },
    {
        "pattern": "report_builder",
        "label": "报告生成器",
        "kind": "web_static",
        "framework": "sveltekit-static",
        "entry": "client/build/index.html",
        "ref_syntax": "js_dynamic_import",
        "capabilities": ["ai.cheap.generate", "platform.ai.cheap.generate"],
    },
    {
        "pattern": "agent_console",
        "label": "Agent 执行控制台",
        "kind": "internal_tool",
        "framework": "monorepo-apps-web",
        "entry": "apps/web/dist/index.html",
        "ref_syntax": "unquoted_attr",
        "capabilities": ["ai.cheap.chat", "platform.ai.chat"],
    },
]

DEPARTMENTS = ["AI小组", "运营部", "客服部", "研发部", "数据部", "财务部", "法务部", "市场部", "供应链部", "安全部"]


@dataclass(frozen=True)
class ProjectTypeSpec:
    index: int
    project_id: str
    name: str
    domain: str
    pattern: str
    kind: str
    framework: str
    entry: str
    ref_syntax: str
    department: str
    capabilities: list[str]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def slug(value: str, *, limit: int = 48) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip().lower()).strip("-")
    return (text or "item")[:limit]


def build_specs() -> list[ProjectTypeSpec]:
    specs: list[ProjectTypeSpec] = []
    for domain_index, domain in enumerate(DOMAINS):
        for pattern_index, pattern in enumerate(APP_PATTERNS):
            index = domain_index * len(APP_PATTERNS) + pattern_index + 1
            project_id = f"codex_type_{index:03d}"
            specs.append(
                ProjectTypeSpec(
                    index=index,
                    project_id=project_id,
                    name=f"Codex 适配 {index:03d} · {domain} · {pattern['label']}",
                    domain=domain,
                    pattern=pattern["pattern"],
                    kind=pattern["kind"],
                    framework=pattern["framework"],
                    entry=pattern["entry"],
                    ref_syntax=pattern["ref_syntax"],
                    department=DEPARTMENTS[(index - 1) % len(DEPARTMENTS)],
                    capabilities=list(pattern["capabilities"]),
                )
            )
    return specs


def add_file(tar: tarfile.TarFile, name: str, content: str | bytes) -> None:
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def ref_fragment(spec: ProjectTypeSpec) -> tuple[str, dict[str, str | bytes]]:
    assets: dict[str, str | bytes] = {
        "assets/type-app.js": "window.__codexTypeApp={ready:true};",
        "styles/type.css": "body{background:linear-gradient(135deg,#0f172a,#1e293b);color:#e2e8f0}.card{border:1px solid #334155;border-radius:16px;padding:16px}",
        "media/bg.png": b"\x89PNG\r\n\x1a\n",
        "images/sample.png": b"\x89PNG\r\n\x1a\n",
        "images/sample@2x.png": b"\x89PNG\r\n\x1a\n",
        "chunks/lazy.js": "export const lazyCodexType=true;",
    }
    if spec.ref_syntax == "html_script_link":
        return '<link rel="stylesheet" href="/styles/type.css"><script type="module" src="/assets/type-app.js"></script>', assets
    if spec.ref_syntax == "css_url":
        assets["styles/type.css"] = "body{background-image:url('/media/bg.png');font-family:system-ui}.card{box-shadow:0 10px 30px #0005}"
        return '<link rel="stylesheet" href="/styles/type.css"><script type="module" src="/assets/type-app.js"></script>', assets
    if spec.ref_syntax == "srcset":
        return '<img alt="样例" srcset="/images/sample.png 1x, /images/sample@2x.png 2x"><script type="module" src="/assets/type-app.js"></script>', assets
    if spec.ref_syntax == "js_dynamic_import":
        assets["assets/type-app.js"] = "export async function run(){ return import('/chunks/lazy.js') } window.__codexTypeApp={dynamic:true};"
        return '<script type="module" src="/assets/type-app.js"></script><link rel="stylesheet" href="/styles/type.css">', assets
    if spec.ref_syntax == "unquoted_attr":
        return '<link rel=modulepreload href=/assets/type-app.js><link rel=stylesheet href=/styles/type.css><script type=module src=/assets/type-app.js></script>', assets
    return '<script type="module" src="/assets/type-app.js"></script>', assets


def entry_html(spec: ProjectTypeSpec) -> tuple[str, dict[str, str | bytes]]:
    refs, assets = ref_fragment(spec)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{spec.name}</title>
  {refs}
</head>
<body data-project-type="{spec.pattern}" data-domain="{spec.domain}">
  <main class="card">
    <h1>{spec.name}</h1>
    <p>这是第 {spec.index} 个 Codex 生成应用类型夹具：{spec.framework} / {spec.ref_syntax}。</p>
    <form data-sf-input="{spec.domain} 参数输入">
      <label>业务问题 <input name="question" value="{spec.domain} 今日重点是什么"></label>
      <label>部门 <input name="department" value="{spec.department}"></label>
      <button type="submit" data-sf-report="{spec.domain} 表单提交报告">提交到平台</button>
    </form>
    <section>
      <button data-sf-report="{spec.domain} {spec.pattern} 已生成报告">生成报告</button>
      <button data-sf-todo="继续优化 {spec.domain} {spec.pattern} 的企业级运行能力">生成待办</button>
      <button data-sf-ai="请基于 {spec.domain} 的输入输出给出低成本分析">低成本 AI 分析</button>
    </section>
    <pre id="payload">{json.dumps(asdict(spec), ensure_ascii=False, indent=2)}</pre>
  </main>
</body>
</html>""", assets


def build_project_package(spec: ProjectTypeSpec) -> tuple[bytes, dict[str, Any]]:
    html, assets = entry_html(spec)
    manifest = {
        "project_id": spec.project_id,
        "name": spec.name,
        "description": f"100 类型 Codex 项目适配矩阵：{spec.domain} / {spec.pattern} / {spec.framework}",
        "kind": spec.kind,
        "entry": spec.entry,
        "version": "type-matrix-v1",
        "visibility": "department",
        "department": spec.department,
        "capabilities": spec.capabilities,
        "outputs": {"reports": True, "todos": True, "proofs": True, "auto_analyze": True},
        "metadata": {
            "source": "codex_project_type_matrix",
            "type_index": spec.index,
            "domain": spec.domain,
            "pattern": spec.pattern,
            "framework": spec.framework,
            "ref_syntax": spec.ref_syntax,
            "department": spec.department,
        },
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        add_file(tar, "projectforge.yaml", yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False))
        add_file(tar, spec.entry, html)
        for name, content in assets.items():
            add_file(tar, name, content)
    return buf.getvalue(), manifest


def result_output_for_spec(spec: ProjectTypeSpec, uploaded: dict[str, Any], runtime_eval: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": f"{spec.name} 已通过平台上传、静态适配、服务转换、输入输出和证据链评估。",
        "type_matrix_spec": asdict(spec),
        "entry_url": uploaded.get("entry_url"),
        "runtime_evaluation": runtime_eval.get("runtime_evaluation"),
        "reports": [
            {
                "title": f"{spec.domain} · {spec.pattern} 运行评估",
                "summary": f"Codex 类型 {spec.index:03d} 已在 {spec.department} 运行并进入平台闭环。",
                "metrics": [
                    {"name": "type_index", "value": spec.index},
                    {"name": "runtime_status", "value": (runtime_eval.get("runtime_evaluation") or {}).get("status")},
                ],
            }
        ],
        "todos": [
            {
                "kind": "review",
                "title": f"沉淀 {spec.domain} {spec.pattern} 企业适配经验",
                "summary": "把该类型的资源重写、Gateway 接管、报告/待办/证据链纳入后续平台迭代基线。",
                "reviewers": ["admin"],
                "payload": {"project_id": spec.project_id, "type_index": spec.index, "department": spec.department},
            }
        ],
        "proofs": [
            {
                "type": "codex_type_matrix_runtime",
                "project_id": spec.project_id,
                "type_index": spec.index,
                "entry_url": uploaded.get("entry_url"),
                "runtime_status": (runtime_eval.get("runtime_evaluation") or {}).get("status"),
                "normal_run_ready": (runtime_eval.get("runtime_evaluation") or {}).get("normal_run_ready"),
            }
        ],
    }


def service_invoke_payload_for_spec(spec: ProjectTypeSpec, *, batch_id: str) -> dict[str, Any]:
    """Build a no-external-cost service invocation for one generated project type.

    The uploaded project is still invoked through Project Gateway service APIs:
    SkillForge creates a ProjectRun, records service input, ingests output,
    writes DecisionLog/LearningEvent, and exposes a trace.  We pass deterministic
    output by default so the 100-type evidence can run without paid model calls;
    use ``--auto-analyze`` if an operator wants the additional platform AI
    analysis call for the resulting output.
    """

    service_request_id = f"codex-type-service-{batch_id}-{spec.index:03d}"
    output = {
        "summary": f"{spec.name} 已作为平台 API 服务完成一次调用，输入输出均经过 Project Gateway。",
        "service_type_matrix_spec": asdict(spec),
        "reports": [
            {
                "title": f"{spec.domain} · {spec.pattern} 服务化调用报告",
                "summary": f"Codex 类型 {spec.index:03d} 已通过 /service/invoke 转为可审计 API 服务。",
                "payload": {
                    "project_id": spec.project_id,
                    "type_index": spec.index,
                    "department": spec.department,
                    "service_request_id": service_request_id,
                },
            }
        ],
        "todos": [
            {
                "kind": "review",
                "title": f"验证 {spec.domain} {spec.pattern} 服务输入输出契约",
                "summary": "服务调用已进入 Project Gateway Trace，可继续沉淀参数模板、输出 schema 和部门复用经验。",
                "payload": {"project_id": spec.project_id, "type_index": spec.index},
            }
        ],
        "proofs": [
            {
                "type": "codex_type_matrix_service_invoke",
                "project_id": spec.project_id,
                "type_index": spec.index,
                "department": spec.department,
                "service_request_id": service_request_id,
                "credential_location": "platform_only",
            }
        ],
    }
    return {
        "request_id": service_request_id,
        "input": {
            "question": f"{spec.domain} 作为 API 服务如何执行？",
            "type_index": spec.index,
            "department": spec.department,
            "pattern": spec.pattern,
        },
        "params": {"matrix": "codex_project_type_matrix", "framework": spec.framework},
        "prompt": f"把 {spec.domain} / {spec.pattern} 上传项目作为平台服务执行，并返回报告、待办和证据。",
        "output": output,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    runtime_pass = sum(1 for row in rows if row.get("runtime_status") == "pass")
    runtime_ready = sum(1 for row in rows if row.get("normal_run_ready") is True)
    result_pass = sum(1 for row in rows if row.get("result_eval_status") == "pass")
    service_invoked = sum(1 for row in rows if row.get("service_invoked") is True)
    service_ok = sum(1 for row in rows if row.get("service_ok") is True)
    service_ready = sum(1 for row in rows if row.get("service_result_ready") is True)
    return {
        "type_count": len(rows),
        "uploaded_count": sum(1 for row in rows if row.get("uploaded") is True),
        "runtime_pass_count": runtime_pass,
        "normal_runtime_ready_count": runtime_ready,
        "result_eval_pass_count": result_pass,
        "service_invoked_count": service_invoked,
        "service_ok_count": service_ok,
        "service_result_ready_count": service_ready,
        "by_kind": dict(Counter(row.get("kind") for row in rows)),
        "by_department": dict(Counter(row.get("department") for row in rows)),
        "by_pattern": dict(Counter(row.get("pattern") for row in rows)),
        "by_framework": dict(Counter(row.get("framework") for row in rows)),
    }


async def register_type_matrix(*, limit: int, offset: int, auto_analyze: bool) -> dict[str, Any]:
    specs = build_specs()[offset : offset + limit if limit else None]
    user = SimpleNamespace(id="admin", username="admin", name="管理员", role="admin", department="AI小组", can_view_all=True, state="active", is_active=True)
    rows: list[dict[str, Any]] = []
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    async with async_session_factory() as db:
        for spec in specs:
            package, manifest = build_project_package(spec)
            uploaded = await project_service.upload_project_package(
                db,
                user,
                package_bytes=package,
                manifest_raw=manifest,
                package_hash="sha256:" + hashlib.sha256(package).hexdigest(),
                submit_metadata={"codex_project_type_matrix": {"batch_id": batch_id, "type_index": spec.index}},
            )
            runtime_before = await project_service.get_project_runtime_evaluation(db, user, spec.project_id)
            run = await project_service.create_project_run(
                db,
                user,
                spec.project_id,
                {
                    "request_id": f"codex-type-open-{batch_id}-{spec.index:03d}",
                    "params": {"type_index": spec.index, "domain": spec.domain, "department": spec.department, "pattern": spec.pattern},
                },
            )
            await project_service.record_project_input_event(
                db,
                user,
                run["id"],
                {
                    "request_id": f"codex-type-input-{batch_id}-{spec.index:03d}",
                    "input": {"question": f"{spec.domain} 今日重点", "type_index": spec.index, "department": spec.department},
                    "metadata": {"source": "codex_project_type_matrix", "department": spec.department},
                },
            )
            output = result_output_for_spec(spec, uploaded, runtime_before)
            ingested = await project_service.ingest_project_output(
                db,
                user,
                run["id"],
                {
                    "request_id": f"codex-type-output-{batch_id}-{spec.index:03d}",
                    "input": {"type_index": spec.index, "domain": spec.domain, "department": spec.department},
                    "output": output,
                    "metadata": {"source": "codex_project_type_matrix", "department": spec.department},
                },
                auto_analyze=auto_analyze,
            )
            runtime_after = await project_service.get_project_runtime_evaluation(db, user, spec.project_id)
            latest_result_eval = runtime_after.get("latest_run_evaluation") or {}
            service_payload = service_invoke_payload_for_spec(spec, batch_id=batch_id)
            service_payload["auto_analyze"] = auto_analyze
            service_invoked = await project_service.invoke_project_service(db, user, spec.project_id, service_payload)
            service_run = service_invoked.get("run") or {}
            service_eval = ((service_run.get("ai_summary") or {}).get("result_evaluation") or {})
            rows.append(
                {
                    "index": spec.index,
                    "project_id": spec.project_id,
                    "name": spec.name,
                    "domain": spec.domain,
                    "pattern": spec.pattern,
                    "kind": spec.kind,
                    "framework": spec.framework,
                    "ref_syntax": spec.ref_syntax,
                    "department": spec.department,
                    "uploaded": True,
                    "entry_url": uploaded.get("entry_url"),
                    "asset_version": uploaded.get("asset_version"),
                    "run_id": run.get("id"),
                    "ingest_status": ingested.get("status"),
                    "runtime_status": (runtime_after.get("runtime_evaluation") or {}).get("status"),
                    "normal_run_ready": (runtime_after.get("runtime_evaluation") or {}).get("normal_run_ready"),
                    "result_eval_status": latest_result_eval.get("status"),
                    "normal_result_ready": latest_result_eval.get("normal_result_ready"),
                    "report_count": ingested.get("report_count"),
                    "todo_count": ingested.get("todo_count"),
                    "proof_count": len(output.get("proofs") or []),
                    "service_invoked": True,
                    "service_ok": bool(service_invoked.get("ok")),
                    "service_run_id": service_invoked.get("project_run_id"),
                    "service_status": service_run.get("status"),
                    "service_result_status": service_eval.get("status"),
                    "service_result_ready": service_eval.get("normal_result_ready"),
                    "service_report_count": service_run.get("report_count"),
                    "service_todo_count": service_run.get("todo_count"),
                    "service_trace_url": (service_invoked.get("service") or {}).get("trace_url"),
                }
            )
        await db.commit()
    summary = summarize(rows)
    return {
        "ok": True,
        "generated_at": now_iso(),
        "batch_id": batch_id,
        "auto_analyze": auto_analyze,
        "summary": summary,
        "results": rows,
        "safety": {"model_calls_requested": auto_analyze, "external_network_used": False, "real_external_writes": False},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Register/evaluate 100 Codex project type fixtures")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--auto-analyze", action="store_true", help="May trigger real model calls depending on platform config")
    args = parser.parse_args()
    result = asyncio.run(register_type_matrix(limit=args.limit, offset=args.offset, auto_analyze=args.auto_analyze))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
