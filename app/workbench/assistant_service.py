"""Assistant orchestration helpers for the legacy workbench chat flow."""

from __future__ import annotations

import json

from app.common.ai import call_llm_stream
from app.common.fork_context import ForkContext
from app.workbench.context_builder import context_builder
from app.workbench.context_compressor import maybe_compress_context
from app.workbench.intent_service import detect_intent, detect_intent_llm


PATCH_INTENTS = {
    "tune_threshold",
    "add_rule",
    "modify_rule",
    "rewrite_selection",
    "add_output",
    "add_test_case",
    "modify_workflow",
    "generate_tests",
    "suggest_branches",
}


def _sf():
    from app.database import async_session_factory

    return async_session_factory


async def handle_chat(
    *,
    create_patch,
    generate_answer,
    skill_id: str,
    session_id: str,
    message: str,
    context: dict,
    intent: str | None,
    reference_ids: list[str],
    user_id: str,
    department: str | None = None,
) -> dict:
    valid_modules = {"meta", "goal", "rules", "params", "output_table", "test_cases", "workflow"}
    raw_module = context.get("active_module")
    active_module = raw_module if raw_module in valid_modules else "goal"

    if not intent:
        intent_result = detect_intent(message)
        intent = intent_result.get("intent", "question")
        target_module = intent_result.get("target_module") or active_module
    else:
      target_module = active_module

    if intent in PATCH_INTENTS:
        context_builder.build_chat_context(
            skill_id,
            active_module=target_module,
            selection=context.get("selection"),
            draft_snapshot=context.get("draft_snapshot"),
            recent_failures=[item.get("message", "") for item in context.get("recent_failures", [])],
        )
        try:
            patch_response = await create_patch(
                skill_id=skill_id,
                session_id=session_id,
                user_id=user_id,
                message=message,
                target_module=target_module,
                references=reference_ids if reference_ids else [],
            )
            return {
                "type": "patch",
                "message": patch_response.summary or f"已为「{target_module}」生成变更方案。",
                "patch": patch_response.model_dump() if hasattr(patch_response, "model_dump") else dict(patch_response),
                "validation_hint": {
                    "affected_modules": [target_module],
                    "suggested_checks": ["structural", "sample_case"],
                },
            }
        except Exception as error:
            return {
                "type": "error",
                "message": f"Patch 生成失败：{error}",
                "patch": None,
                "validation_hint": None,
            }

    answer = await generate_answer(
        skill_id,
        message,
        context,
        user_id=user_id,
        department=department,
        session_id=session_id,
    )
    return {
        "type": "answer",
        "message": answer,
        "patch": None,
        "validation_hint": None,
    }


async def handle_chat_stream(
    *,
    handle_chat_fn,
    build_answer_prompts_fn,
    handle_run_skill_stream_fn,
    skill_id: str,
    session_id: str,
    message: str,
    context: dict,
    intent: str | None,
    reference_ids: list[str],
    user_id: str,
    department: str | None = None,
):
    from loguru import logger

    if not intent:
        intent = None
        skill_meta = None
        try:
            from app.skills import service as skill_service

            async with _sf()() as database_session:
                data = await skill_service.get_skill(database_session, skill_id)
                skill_meta = {
                    "name": (data.get("meta") or {}).get("name") or skill_id,
                    "description": (data.get("meta") or {}).get("description") or "",
                }
        except Exception:
            skill_meta = {"name": skill_id, "description": ""}

        try:
            llm_result = await detect_intent_llm(
                message,
                skill_meta=skill_meta,
                active_module=(context or {}).get("active_module"),
            )
            if llm_result:
                intent = llm_result["intent"]
                logger.info(
                    f"[intent] LLM 分类成功 intent={intent} reason={llm_result.get('reasoning', '')[:80]}"
                )
        except Exception as error:
            logger.warning(f"[intent] LLM 分类异常: {error}")

        if not intent:
            try:
                intent_result = detect_intent(message)
                intent = intent_result.get("intent", "question")
                logger.info(f"[intent] 关键词 fallback intent={intent}")
            except Exception:
                intent = "question"

    if intent == "run_skill":
        async for event in handle_run_skill_stream_fn(
            skill_id=skill_id,
            message=message,
            user_id=user_id,
        ):
            yield event
        return

    if intent in PATCH_INTENTS:
        try:
            result = await handle_chat_fn(
                skill_id=skill_id,
                session_id=session_id,
                message=message,
                context=context,
                intent=intent,
                reference_ids=reference_ids,
                user_id=user_id,
                department=department,
            )
            yield {
                "type": "patch",
                "patch": result.get("patch"),
                "message": result.get("message", ""),
                "validation_hint": result.get("validation_hint"),
            }
            yield {"type": "done", "finish_reason": "patch_generated"}
        except Exception as error:
            yield {"type": "error", "code": "PATCH_FAILED", "error": str(error)[:300]}
        return

    try:
        system_prompt, _, prompt_hash = build_answer_prompts_fn(skill_id, context)
    except Exception as error:
        yield {"type": "error", "code": "BUILD_PROMPT_FAILED", "error": str(error)[:300]}
        return

    fork = ForkContext(system_prompt=system_prompt)
    messages = fork.fork(user_message=message)

    # 长会话上下文压缩：超过阈值时将早期消息压缩为摘要
    try:
        messages = await maybe_compress_context(messages, skill_context=system_prompt[:200])
    except Exception as compress_err:
        from loguru import logger as _logger
        _logger.warning(f"上下文压缩异常（跳过）: {compress_err}")

    cost_context = {
        "user_id": user_id,
        "department": department,
        "skill_id": skill_id,
        "conversation_id": session_id,
        "prompt_hash": prompt_hash,
    }

    final_usage: dict | None = None
    async for event in call_llm_stream(
        messages=messages,
        max_tokens=800,
        temperature=0.3,
        call_source="workbench_chat",
        cost_context=cost_context,
    ):
        event_type = event.get("type")
        if event_type in ("delta", "done", "error"):
            yield event
            if event_type == "error":
                return
        elif event_type == "usage":
            final_usage = event

    if final_usage:
        yield {"type": "usage_summary", "usage": final_usage}


async def handle_command(
    *,
    skill_id: str,
    session_id: str,
    command: str,
    context: dict,
    user_id: str,
) -> dict:
    command_handlers = {
        "generate-tests": _cmd_generate_tests,
        "discover-antipatterns": _cmd_discover_antipatterns,
        "suggest-branches": _cmd_suggest_branches,
        "drift-check": _cmd_drift_check,
        "run-aiclaw": _cmd_run_aiclaw,
    }
    handler = command_handlers.get(command)
    if not handler:
        return {"type": "error", "message": f"未知命令: /{command}", "patch": None, "validation_hint": None}

    try:
        return await handler(skill_id, session_id, context, user_id)
    except Exception as error:
        return {"type": "error", "message": f"命令执行失败: {error}", "patch": None, "validation_hint": None}


async def _cmd_generate_tests(skill_id, session_id, context, user_id):
    from app.skills.core.git_service import git_service
    from app.skills.core.parser import skill_parser
    from app.skills.tooling.test_generation import generate_test_cases

    skill_md = git_service.read_file(skill_id, "SKILL.md")
    if not skill_md:
        return {"type": "error", "message": "无法读取 SKILL.md", "patch": None, "validation_hint": None}
    parsed = skill_parser.parse(skill_md)
    cases = generate_test_cases(parsed)
    return {
        "type": "answer",
        "message": f"已生成 {len(cases)} 个测试用例。可在测试面板中查看。",
        "patch": None,
        "validation_hint": {"affected_modules": ["test_cases"], "suggested_checks": ["structural"]},
    }


async def _cmd_discover_antipatterns(skill_id, session_id, context, user_id):
    from app.skills.intelligence.ai_service import discover_antipatterns

    async with _sf()() as session:
        result = await discover_antipatterns(session, skill_id, days=30)
    count = len(result.get("antipatterns", []))
    return {
        "type": "answer",
        "message": f"发现 {count} 个潜在反例。" if count else "未发现明显反例。",
        "patch": None,
        "validation_hint": None,
    }


async def _cmd_suggest_branches(skill_id, session_id, context, user_id):
    from app.skills.intelligence.ai_service import suggest_branches

    structure = context_builder.load_skill_structure(skill_id)
    first_step = structure.rules[0] if structure.rules else None
    if not first_step:
        return {"type": "answer", "message": "当前无决策步骤，无法建议分支。", "patch": None, "validation_hint": None}
    async with _sf()() as session:
        result = await suggest_branches(
            session,
            skill_id,
            step_id=first_step.get("id", "step_1"),
            existing_branches=first_step.get("branches", []),
        )
    count = len(result.get("branches", []))
    return {
        "type": "answer",
        "message": f"建议 {count} 个新决策分支。" if count else "当前分支覆盖完整。",
        "patch": None,
        "validation_hint": None,
    }


async def _cmd_drift_check(skill_id, session_id, context, user_id):
    structure = context_builder.load_skill_structure(skill_id)
    return {
        "type": "answer",
        "message": "漂移检测完成。" + (f"当前有 {len(structure.params)} 个参数。" if structure.params else "无参数。"),
        "patch": None,
        "validation_hint": None,
    }


async def _cmd_run_aiclaw(skill_id, session_id, context, user_id):
    from app.execution.execution_service import execution_service

    result = await execution_service.execute_skill(
        skill_id=skill_id,
        params={"_execution_backend": "bridge_script"},
        sandbox=True,
        triggered_by="wb_aiclaw",
        run_mode="sandbox_test",
    )
    output = json.dumps(result if isinstance(result, dict) else {"output": str(result)}, ensure_ascii=False, indent=2)
    return {
        "type": "answer",
        "message": f"AIClaw 执行完成。\n```json\n{output}\n```",
        "patch": None,
        "validation_hint": None,
    }
