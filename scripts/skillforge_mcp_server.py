#!/usr/bin/env python3
"""SkillForge MCP Server (stdio 模式, asyncio 版本).

继承 AsyncMcpServer 基类：统一 JSON-RPC 协议 + asyncio.wait_for 超时保护。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from app.auth.models import User
from app.codex import service as codex_service
from app.common.audit import AuditLog
from app.database import async_session_factory
from app.sf import data_service as sf_data_service
from app.users.service import flush_post_commit_callbacks
from app.skills.core.git_service import git_service
from app.skills.lifecycle import service as skill_service
from app.skills.router_quality import (
    _MODULE_TO_BLOCK_TYPE,
    _build_structured_kwargs_for_module,
    _extract_module_payload,
)
from app.skills.tooling.validation_service import validate_block
from app.todos.todo_spec import build_dispatch_task, build_dispatch_todo, build_review_todo

# 添加 scripts/ 到 sys.path，便于导入 mcp_base
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_base import AsyncMcpServer


# skill_run_script 由用户传 timeout (默认 10s)，外层 MCP 超时要比 subprocess
# 多 10s 余量，否则 subprocess 还在跑外层就 cancel 了。
RUN_SCRIPT_TIMEOUT_BUFFER_SEC = 10


def _skill_id() -> str:
    skill_id = os.environ.get("SKILLFORGE_SKILL_ID", "").strip()
    if not skill_id:
        raise ValueError("缺少 SKILLFORGE_SKILL_ID")
    return skill_id


def _user_id() -> str:
    return os.environ.get("SKILLFORGE_USER_ID", "coding_agent")


def _platform_skill_context(skill_id: str) -> str:
    """Creation preview sessions use synthetic skill ids; do not treat them as real Skill scope."""
    if skill_id.startswith("_creating_") or skill_id.startswith("__builtin"):
        return ""
    return skill_id


async def _load_skill(skill_id: str) -> dict:
    async with async_session_factory() as db:
        return await skill_service.get_skill(db, skill_id)


async def _load_platform_user(db) -> User:
    user_id = _user_id()
    user = await db.get(User, user_id)
    if not user:
        raise ValueError(f"当前 MCP 用户不存在或未授权: {user_id}")
    codex_service.assert_active_user(user)
    return user


def _sanitize_platform_ai_arguments(arguments: dict) -> dict:
    cleaned = dict(arguments or {})
    for key in ("prompt", "question", "query", "system", "context"):
        if key in cleaned and isinstance(cleaned.get(key), str):
            cleaned[key] = codex_service.redact_secret_text(cleaned[key], limit=12000)
    for key in ("context_pack", "contextPack"):
        if key in cleaned:
            cleaned[key] = codex_service._sanitize_raw_data_value(cleaned[key])
    return cleaned


def _platform_mcp_effective_skill_id(skill_id: str, arguments: dict) -> str:
    explicit = str(arguments.get("skill_id") or arguments.get("skillId") or "").strip()
    return explicit or _platform_skill_context(skill_id)


async def _run_platform_mcp_tool(tool_name: str, skill_id: str, arguments: dict) -> dict:
    args = dict(arguments or {})
    effective_skill_id = _platform_mcp_effective_skill_id(skill_id, args)
    async with async_session_factory() as db:
        user = await _load_platform_user(db)
        if tool_name == "skillforge_agent_coverage":
            result = await codex_service._builtin_agent_coverage(db, user, args)
        elif tool_name == "skillforge_ai_analyze":
            args = _sanitize_platform_ai_arguments(args)
            if effective_skill_id and not args.get("skill_id") and not args.get("skillId"):
                args["skill_id"] = effective_skill_id
            result = await codex_service._builtin_platform_ai_analyze(
                db,
                user,
                args,
                effective_skill_id=effective_skill_id,
                effective_run_id=str(args.get("run_id") or args.get("runId") or ""),
            )
        elif tool_name == "skillforge_raw_data_query":
            result = await codex_service._builtin_raw_data_query(
                db,
                user,
                args,
                effective_skill_id=effective_skill_id,
            )
        elif tool_name == "skillforge_sf_data_write":
            result = await sf_data_service.write_sf_data(
                db,
                user,
                args,
                dry_run=bool(args.get("dry_run", True)),
                idempotency_key=str(args.get("idempotency_key") or args.get("idempotencyKey") or "").strip() or None,
                source_tool=tool_name,
                source="skillforge_internal_mcp",
                skill_id=effective_skill_id,
                run_id=str(args.get("run_id") or args.get("runId") or os.environ.get("SKILLFORGE_RUN_ID") or "").strip() or None,
            )
        elif tool_name == "skillforge_sf_data_list":
            result = await sf_data_service.list_sf_data_records(
                db,
                user,
                page=int(args.get("page") or 1),
                page_size=int(args.get("page_size") or args.get("pageSize") or 50),
                namespace=str(args.get("namespace") or "").strip() or None,
                content_type=str(args.get("content_type") or args.get("contentType") or "").strip() or None,
                skill_id=str(args.get("skill_id") or args.get("skillId") or effective_skill_id or "").strip() or None,
                run_id=str(args.get("run_id") or args.get("runId") or "").strip() or None,
                source=str(args.get("source") or "").strip() or None,
                q=str(args.get("q") or args.get("query") or "").strip() or None,
            )
        elif tool_name == "skillforge_sf_data_get":
            record_id = str(args.get("id") or args.get("record_id") or args.get("recordId") or "").strip()
            if not record_id:
                raise ValueError("缺少 id")
            result = await sf_data_service.get_sf_data_record(db, user, record_id)
        elif tool_name == "skillforge_run_analyze":
            result = await codex_service._builtin_run_analyze(
                db,
                user,
                args,
                effective_skill_id=effective_skill_id,
            )
        else:
            raise ValueError(f"不支持的平台 MCP 工具: {tool_name}")

        db.add(AuditLog(
            user_id=user.id,
            action=f"skillforge_internal_mcp.{tool_name}",
            target_type="skillforge_internal_mcp",
            target_id=effective_skill_id or str(args.get("run_id") or args.get("runId") or "platform"),
            detail={
                "tool": tool_name,
                "skill_id": effective_skill_id or None,
                "run_id": args.get("run_id") or args.get("runId") or args.get("execution_run_id") or None,
                "payload_redacted": True,
                "credential_location": "platform_only",
            },
        ))
        await db.commit()
        await flush_post_commit_callbacks(db)
        return result


async def _validate_module(skill_id: str, module_name: str, content):
    block_type = _MODULE_TO_BLOCK_TYPE.get(module_name)
    if not block_type:
        raise ValueError(f"不支持的 module: {module_name}")
    async with async_session_factory() as db:
        return await validate_block(db, skill_id, block_type, content)


async def _apply_module(skill_id: str, module_name: str, content):
    kwargs = _build_structured_kwargs_for_module(module_name, content)
    async with async_session_factory() as db:
        return await skill_service.save_skill_structured(
            db, skill_id=skill_id, user_id=_user_id(), **kwargs
        )


class SkillForgeMcpServer(AsyncMcpServer):
    server_name = "skillforge"
    DEFAULT_TIMEOUT = 60

    TOOLS = [
        {"name": "skill_get_manifest", "description": "读取当前 Skill 的 manifest 摘要。", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "skill_read_module", "description": "读取当前 Skill 的单个模块。", "inputSchema": {"type": "object", "properties": {"module": {"type": "string"}}, "required": ["module"]}},
        {"name": "skill_validate_patch", "description": "对单模块 patch 内容做校验。", "inputSchema": {"type": "object", "properties": {"module": {"type": "string"}, "content": {}}, "required": ["module", "content"]}},
        {"name": "skill_apply_structured_patch", "description": "把结构化 patch 应用到单模块。", "inputSchema": {"type": "object", "properties": {"module": {"type": "string"}, "content": {}}, "required": ["module", "content"]}},
        {"name": "skill_list_scripts", "description": "列出当前 Skill 可执行脚本。", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "skill_run_script", "description": "隔离执行当前 Skill 的某个 scripts/ 下脚本。", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "payload": {}, "timeout": {"type": "integer", "default": 10}}, "required": ["path"]}},
        {"name": "skillforge_agent_coverage", "description": "查询当前 MCP 用户可见部门的执行、分析、训练 Agent 覆盖度和平台兜底状态。", "inputSchema": codex_service._agent_coverage_schema()},
        {"name": "skillforge_ai_analyze", "description": "通过平台 AI 配置调用 DeepSeek V4 Pro 1M 上下文模型做分析，不向 MCP 子进程暴露模型密钥。", "inputSchema": codex_service._platform_ai_analyze_schema()},
        {"name": "skillforge_raw_data_query", "description": "按当前 MCP 用户权限查询 Skill 运行后的脱敏原始数据。", "inputSchema": codex_service._raw_data_query_schema()},
        {"name": "skillforge_run_analyze", "description": "读取某次 Skill 运行后的脱敏原始数据，并调用平台 AI 生成运行复盘。", "inputSchema": codex_service._run_analyze_schema()},
        {"name": "skillforge_sf_data_write", "description": "向 SkillForge 通用 SF 数据库存储 JSON、文本、表格或大型 artifact 引用。", "inputSchema": codex_service._sf_data_write_schema()},
        {"name": "skillforge_sf_data_list", "description": "分页查询通用 SF 数据库。", "inputSchema": codex_service._sf_data_list_schema()},
        {"name": "skillforge_sf_data_get", "description": "读取单条通用 SF 数据记录详情。", "inputSchema": codex_service._sf_data_get_schema()},
        {"name": "todo_build_dispatch", "description": "构造 dispatch 待办 payload。", "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "summary": {"type": "string"}, "reviewers": {"type": "array", "items": {"type": "string"}}, "reviewer_role": {"type": "string"}, "tasks": {"type": "array"}, "payload": {}}, "required": ["title", "tasks"]}},
        {"name": "todo_build_review", "description": "构造 review 待办 payload。", "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "summary": {"type": "string"}, "reviewers": {"type": "array", "items": {"type": "string"}}, "reviewer_role": {"type": "string"}, "payload": {}}, "required": ["title"]}},
    ]

    def effective_timeout(self, tool_name: str, tool_args: dict) -> int:
        if tool_name == "skill_run_script":
            user_timeout = int(tool_args.get("timeout") or 10)
            return max(self.DEFAULT_TIMEOUT, user_timeout + RUN_SCRIPT_TIMEOUT_BUFFER_SEC)
        return self.DEFAULT_TIMEOUT

    async def handle_tool_call(self, name: str, arguments: dict) -> str:
        skill_id = _skill_id()

        if name == "skill_get_manifest":
            data = await _load_skill(skill_id)
            return json.dumps(data.get("manifest") or {}, ensure_ascii=False, indent=2)
        if name == "skill_read_module":
            module = str(arguments["module"])
            if "/" in module or "." in Path(module).name:
                content = git_service.read_file(skill_id, module, errors="replace")
                if content is not None:
                    return json.dumps(
                        {"path": module, "content": content, "num_lines": content.count("\n") + (0 if content.endswith("\n") else 1)},
                        ensure_ascii=False, indent=2,
                    )
            data = await _load_skill(skill_id)
            return json.dumps(_extract_module_payload(data, module), ensure_ascii=False, indent=2)
        if name == "skill_validate_patch":
            result = await _validate_module(skill_id, arguments["module"], arguments.get("content"))
            return json.dumps(result, ensure_ascii=False, indent=2)
        if name == "skill_apply_structured_patch":
            result = await _apply_module(skill_id, arguments["module"], arguments.get("content"))
            return json.dumps(result, ensure_ascii=False, indent=2)
        if name == "skill_list_scripts":
            return json.dumps(skill_service.list_scripts(skill_id), ensure_ascii=False, indent=2)
        if name == "skill_run_script":
            result = await asyncio.to_thread(
                skill_service.run_script,
                skill_id, arguments["path"],
                payload=arguments.get("payload") or {},
                timeout=int(arguments.get("timeout") or 10),
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        if name in {
            "skillforge_agent_coverage",
            "skillforge_ai_analyze",
            "skillforge_raw_data_query",
            "skillforge_run_analyze",
            "skillforge_sf_data_write",
            "skillforge_sf_data_list",
            "skillforge_sf_data_get",
        }:
            result = await _run_platform_mcp_tool(name, skill_id, arguments)
            return json.dumps(result, ensure_ascii=False, indent=2, default=str)
        if name == "todo_build_dispatch":
            tasks = [
                build_dispatch_task(t.get("content", ""), executor=t.get("executor"), deadline=t.get("deadline"), extra=t.get("extra"))
                for t in (arguments.get("tasks") or [])
            ]
            result = build_dispatch_todo(
                arguments["title"], tasks=tasks, summary=arguments.get("summary"),
                payload=arguments.get("payload"), reviewers=arguments.get("reviewers"),
                reviewer_role=arguments.get("reviewer_role"),
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        if name == "todo_build_review":
            result = build_review_todo(
                arguments["title"], summary=arguments.get("summary"),
                payload=arguments.get("payload"), reviewers=arguments.get("reviewers"),
                reviewer_role=arguments.get("reviewer_role"),
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

        return f"未知工具: {name}"


# 模块级 server 实例，用于 asyncio.run(main()) 入口和向后兼容测试引用
_server = SkillForgeMcpServer()
handle_tool_call = _server.handle_tool_call
send_response = _server.send_response
send_error = _server.send_error
TOOLS = _server.TOOLS
TOOL_CALL_DEFAULT_TIMEOUT_SEC = _server.DEFAULT_TIMEOUT
_dispatch = _server._dispatch


def _effective_timeout(tool_name: str, tool_args: dict) -> int:
    """模块级包装：使用 TOOL_CALL_DEFAULT_TIMEOUT_SEC（可被测试 monkeypatch）。"""
    if tool_name == "skill_run_script":
        user_timeout = int(tool_args.get("timeout") or 10)
        return max(TOOL_CALL_DEFAULT_TIMEOUT_SEC, user_timeout + RUN_SCRIPT_TIMEOUT_BUFFER_SEC)
    return TOOL_CALL_DEFAULT_TIMEOUT_SEC


async def _handle_tool_call_request(req_id, params):
    """模块级包装：使用 handle_tool_call / TOOL_CALL_DEFAULT_TIMEOUT_SEC（可被测试 monkeypatch）。"""
    tool_name = str(params.get("name", ""))
    tool_args = params.get("arguments", {}) or {}
    timeout = TOOL_CALL_DEFAULT_TIMEOUT_SEC
    if tool_name == "skill_run_script":
        user_timeout = int(tool_args.get("timeout") or 10)
        timeout = max(TOOL_CALL_DEFAULT_TIMEOUT_SEC, user_timeout + RUN_SCRIPT_TIMEOUT_BUFFER_SEC)
    try:
        result_text = await asyncio.wait_for(handle_tool_call(tool_name, tool_args), timeout=timeout)
        send_response(req_id, {"content": [{"type": "text", "text": result_text}]})
    except asyncio.TimeoutError:
        _server.log(f"tools/call timeout tool={tool_name} timeout={timeout}s")
        send_response(req_id, {
            "content": [{"type": "text", "text": f"工具执行超时(>{timeout}s): {tool_name}"}],
            "isError": True,
        })
    except Exception as e:
        _server.log(f"tools/call exception tool={tool_name}: {e!r}")
        send_response(req_id, {
            "content": [{"type": "text", "text": f"工具执行异常: {e}"}],
            "isError": True,
        })

if __name__ == "__main__":
    try:
        asyncio.run(_server.main())
    except KeyboardInterrupt:
        pass
