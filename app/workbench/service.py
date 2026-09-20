"""Workbench service: draft generation, references, patching, validation and apply."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import re
from uuid import uuid4


def _now_bjt() -> datetime:
    """返回北京时间（naive），兼容 TIMESTAMP WITHOUT TIME ZONE 列。"""
    return now_bjt()

import yaml
from loguru import logger
from sqlalchemy import select

from app.common.ai import call_llm, call_llm_stream
from app.common.audit import audit
from app.common.cache import cache_get, cache_set
from app.common.exceptions import AppError
from app.common.fork_context import ForkContext
from app.common.time_utils import isoformat_bjt, now_bjt
from app.playbooks import service as playbook_service
from app.skills.lifecycle import service as skill_service
from app.skills.core.models import Skill
from app.testing.replay import replay_service
from app.workbench.apply_service import apply_patch_to_skill_document, apply_partial_patch as _apply_partial, build_patch_from_message
from app.workbench.context_builder import context_builder, invalidate_structure_cache
from app.workbench.diff_service import build_diff_preview
from app.workbench.intent_service import detect_intent, detect_intent_llm
from app.workbench.models import (
    SkillStudioDraft,
    SkillStudioPreview,
    SkillStudioReview,
    SkillStudioRun,
    SkillWorkbenchPatch,
    SkillWorkbenchReference,
    SkillWorkbenchSession,
    SkillWorkbenchValidationRun,
)
from app.workbench.schemas import (
    ReferenceItem,
    SkillStructure,
    WorkbenchApplyResponse,
    WorkbenchTaskContractBindResponse,
    WorkbenchCreateSkillFromDraftResponse,
    WorkbenchTaskContractResponse,
    WorkbenchTaskContractReviewResponse,
    WorkbenchGenerateDraftResponse,
    WorkbenchIntentResponse,
    WorkbenchMode,
    WorkbenchPatchResponse,
    WorkbenchReferenceListResponse,
    WorkbenchSessionResponse,
    WorkbenchValidateResponse,
)
from app.workbench import patch_service as wb_patch_service
from app.workbench import assistant_service as wb_assistant_service
from app.workbench import review_context_service as review_ctx_service
from app.workbench import session_orchestrator
from app.workbench.message_persistence import (
    attribute_ai_error,
    build_coding_session_id,
    ensure_session_row,
    load_coding_timeline as _load_coding_timeline,
    load_recent_ai_turns as _load_recent_ai_turns,
    save_message,
)
from app.workbench.validation_service import validate_module_patch
from app.workbench.task_contract import (
    TASK_CONTRACT_PROMPT_VERSION,
    build_bundle_from_contract,
    build_gate_status,
    build_preview,
    build_preview_cache_key,
    build_task_contract_bundle,
    checkpoint_list,
    merge_review_state,
    review_expires_at,
)


def _sf():
    """Lazy session factory lookup for test isolation."""
    from app.database import async_session_factory
    return async_session_factory


def _generate_name_from_message(message: str) -> str:
    cleaned = re.sub(r"\s+", " ", (message or "")).strip()
    return cleaned[:24] if cleaned else "未命名Skill"


def _slugify_skill_id(name: str) -> str:
    """v2.8.0：委托到 id_gen.gen_from_name，保留此 wrapper 兼容旧 import 路径。"""
    from app.skills.core.id_gen import gen_from_name

    return gen_from_name(name)


def _extract_numbers(message: str) -> list[float]:
    values = []
    for match in re.findall(r"-?\d+(?:\.\d+)?", message or ""):
        try:
            values.append(float(match))
        except ValueError:
            continue
    return values


def _normalize_skill_structure(raw: dict, message: str) -> SkillStructure:
    meta = raw.get("meta") or {}
    def _pick(item, *keys, default=""):
        for key in keys:
            if key in item and item[key] not in (None, ""):
                return item[key]
        return default

    goal_value = raw.get("goal") or raw.get("purpose") or message.strip()
    if isinstance(goal_value, dict):
        goal_value = (
            goal_value.get("primary")
            or goal_value.get("summary")
            or goal_value.get("description")
            or next((str(v) for v in goal_value.values() if isinstance(v, str) and v.strip()), message.strip())
        )

    params_value = raw.get("params") or []
    if isinstance(params_value, dict):
        flattened = []
        for value in params_value.values():
            if isinstance(value, list):
                flattened.extend(value)
        params_value = flattened

    rules_value = raw.get("rules") or raw.get("steps") or []
    if isinstance(rules_value, dict):
        rules_value = rules_value.get("items") or list(rules_value.values())

    output_value = raw.get("output_table") or raw.get("outputs") or []
    if isinstance(output_value, dict):
        output_value = output_value.get("columns") or output_value.get("items") or list(output_value.values())

    test_value = raw.get("test_cases") or []
    if isinstance(test_value, dict):
        test_value = test_value.get("items") or list(test_value.values())

    return SkillStructure(
        meta={
            "name": _pick(meta, "name", "skill_name", default=_generate_name_from_message(message)),
            "department": _pick(meta, "department", default=""),
            "trigger_type": _pick(meta, "trigger_type", default="manual"),
            "risk_level": _pick(meta, "risk_level", default="R2"),
        },
        goal=str(goal_value or message.strip()),
        rules=rules_value if isinstance(rules_value, list) else [],
        params=params_value if isinstance(params_value, list) else [],
        output_table=output_value if isinstance(output_value, list) else [],
        test_cases=test_value if isinstance(test_value, list) else [],
        workflow=raw.get("workflow") or {},
        custom_sections=raw.get("custom_sections") or {},
    )


def _fallback_generate_draft(message: str, references: list[dict] | None = None) -> WorkbenchGenerateDraftResponse:
    refs = references or []
    numbers = _extract_numbers(message)
    skill = SkillStructure(
        meta={
            "name": _generate_name_from_message(message),
            "department": "",
            "trigger_type": "manual",
            "risk_level": "R2",
        },
        goal=message.strip(),
        rules=[
            {
                "id": "rule-1",
                "name": "核心判断",
                "description": "根据需求补充具体判断条件",
                "branches": [
                    {
                        "condition": "待补充具体条件",
                        "conclusion": "待确认",
                        "action": "人工确认",
                    }
                ],
            }
        ],
        params=[
            {
                "name": "threshold",
                "default_value": numbers[0] if numbers else 1.0,
                "description": "默认阈值",
            }
        ],
        output_table=[
            {"name": "建议动作", "format": "table", "recipient": "运营"},
            {"name": "说明", "format": "text", "recipient": "运营"},
        ],
        test_cases=[
            {"name": "样例1", "input_data": {}, "expected_output": {}},
        ],
        workflow=build_patch_from_message("workflow", message, refs, _slugify_skill_id(_generate_name_from_message(message))).get("workflow", {}),
    )
    return WorkbenchGenerateDraftResponse(
        skill=skill,
        summary="已按本地 fallback 规则生成完整 Skill 草稿。",
        source="fallback",
        modules=context_builder.summarize_modules(skill),
        validation=validate_module_patch("goal", {"goal": skill.goal}),
    )


def _sanitize_user_input(text: str, max_length: int = 2000) -> str:
    """清洗用户输入，防止 prompt injection。"""
    truncated = (text or "").strip()[:max_length]
    # 移除可能干扰 LLM 指令层的标记
    for marker in ("```", "<|", "|>", "<<SYS>>", "<</SYS>>", "[INST]", "[/INST]"):
        truncated = truncated.replace(marker, "")
    return truncated


async def _generate_draft_with_ai(message: str, references: list[dict] | None = None) -> WorkbenchGenerateDraftResponse | None:
    safe_message = _sanitize_user_input(message)
    refs_text = yaml.safe_dump(references or [], allow_unicode=True, sort_keys=False) if references else ""

    system_prompt = """你是 SkillForge 平台的 Skill 设计助手。
你的任务是根据用户的自然语言描述，生成一个完整的 Skill 结构化草稿。
输出必须是严格的 JSON 格式，不含任何其他内容。"""

    user_prompt = f"""请根据用户需求生成完整的 Skill 草稿 JSON。

## 输出格式要求
{{
  "meta": {{
    "name": "简短的 Skill 名称",
    "department": "推测所属部门",
    "trigger_type": "manual|scheduler|webhook|event",
    "risk_level": "R1|R2|R3|R4"
  }},
  "goal": "一段完整的目标描述，至少 30 字",
  "rules": [
    {{
      "id": "step_1",
      "name": "规则名称",
      "description": "规则描述",
      "branches": [
        {{"condition": "判断条件", "conclusion": "结论", "action": "执行动作", "next_step": null}}
      ]
    }}
  ],
  "params": [
    {{"name": "参数名", "default_value": 数值或字符串, "description": "参数用途说明"}}
  ],
  "output_table": [
    {{"name": "字段名", "format": "text|table|number|chart", "recipient": "接收方角色"}}
  ],
  "test_cases": [
    {{"name": "测试场景名", "input_data": {{}}, "expected_output": {{}}}}
  ]
}}

## 质量要求
- rules 至少 2 条，每条至少 1 个分支
- params 至少 2 个可调参数
- output_table 至少 3 个输出字段
- test_cases 至少 2 个样例，覆盖正常和边界情况
- goal 描述要有业务价值，不是简单重复用户输入
{f'''
## 参考已有 Skill 结构
{refs_text}
''' if refs_text else ''}
## 用户需求
{safe_message}"""

    start_ms = _now_bjt()
    result = await call_llm(
        system=system_prompt,
        user=user_prompt,
        max_tokens=3000,
        temperature=0.15,
        json_mode=True,
        timeout=90,
    )
    elapsed_ms = int((_now_bjt() - start_ms).total_seconds() * 1000)

    # 记录 AI 调用（启用之前未使用的 SkillWorkbenchAIRun 模型）
    try:
        from app.workbench.models import SkillWorkbenchAIRun
        async with _sf()() as session:
            session.add(SkillWorkbenchAIRun(
                id=f"airun-{uuid4().hex[:12]}",
                session_id="",  # 生成草稿时尚无 session
                run_type="generate_draft",
                model_id="default",
                prompt_key="generate_draft_v2",
                latency_ms=elapsed_ms,
                success=isinstance(result, dict),
                error_message=None if isinstance(result, dict) else "非 dict 返回",
                created_at=_now_bjt(),
            ))
            await session.commit()
    except Exception:
        pass  # 追踪失败不阻断主流程

    if not isinstance(result, dict):
        return None

    try:
        skill = _normalize_skill_structure(result, message)
    except Exception:
        return None
    if not skill.workflow:
        skill.workflow = build_patch_from_message("workflow", message, references or [], _slugify_skill_id(skill.meta.get("name", ""))).get("workflow", {})
    return WorkbenchGenerateDraftResponse(
        skill=skill,
        summary="已通过 AI 生成完整 SkillDraft。",
        source="ai",
        modules=context_builder.summarize_modules(skill),
        validation=validate_module_patch("goal", {"goal": skill.goal}),
    )


def _workflow_patch(message: str, references: list[dict], skill_id: str, structure: SkillStructure) -> tuple[str, dict]:
    workflow = build_patch_from_message("workflow", message, references, skill_id).get("workflow", {})
    if not workflow.get("nodes"):
        workflow["nodes"] = [{
            "id": f"{skill_id}_node",
            "label": structure.meta.get("name") or skill_id,
            "detail": "当前 Skill 节点",
            "skill_id": skill_id,
        }]
    workflow.setdefault("edges", [])
    workflow.setdefault("bindings", [])
    workflow.setdefault("status", "draft")
    workflow.setdefault("summary", message or "工作流草稿")
    return "生成工作流草稿", {"workflow": workflow}


def _normalize_goal_patch(patch_message: str, structure: SkillStructure) -> tuple[str, dict]:
    """目标模块 patch：直接用用户消息覆盖 goal。"""
    return "更新目标描述", {"goal": patch_message.strip()}


def _normalize_params_patch(patch_message: str, structure: SkillStructure) -> tuple[str, dict]:
    """参数模块 patch：从消息中提取数值，更新第一个参数的默认值。"""
    numbers = _extract_numbers(patch_message)
    existing = structure.params[0] if structure.params else {"name": "threshold", "description": "默认阈值"}
    return "调整参数阈值", {
        "params": [
            {
                "name": existing.get("name", "threshold"),
                "default_value": numbers[0] if numbers else existing.get("default_value", 1.0),
                "description": existing.get("description", ""),
            }
        ]
    }


def _normalize_rules_patch(patch_message: str, structure: SkillStructure, references: list[dict] | None = None) -> tuple[str, dict]:
    """规则模块 patch：用消息重写规则，附带参考来源。"""
    detail = patch_message.strip()
    if references:
        detail = f"{detail}\n参考来源：{', '.join(ref.get('title', ref.get('source_id', '引用项')) for ref in references[:3])}"
    return "重写规则模块", {
        "rules": [
            {
                "id": "rule-1",
                "name": "规则调整",
                "description": detail,
                "branches": [
                    {
                        "condition": patch_message.strip(),
                        "conclusion": "待确认",
                        "action": "按新规则执行",
                    }
                ],
            }
        ]
    }


def _normalize_output_patch(patch_message: str, structure: SkillStructure) -> tuple[str, dict]:
    """输出表格模块 patch：根据消息关键字决定字段列表。"""
    names = ["建议动作", "说明"]
    if "表格" in patch_message or "字段" in patch_message:
        names.append("置信度")
    return "重写输出表格", {
        "output_table": [
            {"name": name, "format": "table" if name == "建议动作" else "text", "recipient": "运营"}
            for name in names
        ]
    }


def _normalize_test_cases_patch(patch_message: str, structure: SkillStructure) -> tuple[str, dict]:
    """测试用例模块 patch：生成一个骨架测试样例。"""
    return "补充测试样例", {
        "test_cases": [
            {
                "name": "样例1",
                "input_data": {"message": patch_message},
                "expected_output": {"status": "ok"},
            }
        ]
    }


def _normalize_patch(target_module: str, message: str, structure: SkillStructure, references: list[dict], skill_id: str) -> tuple[str, dict]:
    """按 target_module 分发到对应子函数，生成标准化 patch。"""
    if target_module == "goal":
        return _normalize_goal_patch(message, structure)

    if target_module == "params":
        return _normalize_params_patch(message, structure)

    if target_module == "rules":
        return _normalize_rules_patch(message, structure, references)

    if target_module == "output_table":
        return _normalize_output_patch(message, structure)

    if target_module == "test_cases":
        return _normalize_test_cases_patch(message, structure)

    if target_module == "workflow":
        return _workflow_patch(message, references, skill_id, structure)

    return "生成工作流草稿", build_patch_from_message(target_module, message, references, skill_id)


class WorkbenchService:
    """Create/get sessions and operate on patches."""

    async def _invoke_agent_core_save(self, message: str) -> dict:
        """走 v7 LangGraph save 流程；任何异常都 fallback 到正则版 bundle，保证可用性。"""
        from loguru import logger

        try:
            from app.agent_core.runtime import run_skill_graph

            state = await run_skill_graph(message, mode="save")
            contract = state.get("contract") or {}
            if not contract:
                raise RuntimeError("agent_core 返回空 contract")
            # 拼回 bundle 形状（兼容旧 review_state / preview / gate / skill 等字段）
            bundle = build_bundle_from_contract(message, contract)
            # 保留 graph 的 review_state（已 merge 过）
            if state.get("review_state"):
                bundle["review_state"] = state["review_state"]
            if state.get("preview_result"):
                bundle["preview"] = state["preview_result"]
            if state.get("skill_md"):
                bundle["skill_md"] = state["skill_md"]
            return bundle
        except Exception as exc:  # noqa: BLE001
            logger.warning("[workbench] agent_core save 失败 fallback 到 build_task_contract_bundle: {}", exc)
            return build_task_contract_bundle(message)

    async def _get_task_preview(self, cache_key: str, contract: dict) -> dict:
        return await review_ctx_service.get_task_preview(cache_key, contract)

    async def _check_skill_lock_conflict(
        self,
        session,
        skill_id: str | None,
        branch: str,
        user_id: str,
    ) -> None:
        await review_ctx_service.check_skill_lock_conflict(
            session,
            skill_id=skill_id,
            branch=branch,
            user_id=user_id,
        )

    async def fork_personal_branch(
        self,
        skill_id: str,
        user_id: str,
    ) -> dict:
        return await review_ctx_service.fork_personal_branch(skill_id, user_id)

    async def force_takeover_draft(
        self,
        draft_id: str,
        admin_user_id: str,
        admin_role: str,
    ) -> dict:
        return await review_ctx_service.force_takeover_draft(
            draft_id,
            admin_user_id=admin_user_id,
            admin_role=admin_role,
        )

    async def generate_task_contract(self, message: str, user_id: str) -> WorkbenchTaskContractResponse:
        return await review_ctx_service.generate_task_contract(
            message=message,
            user_id=user_id,
            invoke_agent_core_save=self._invoke_agent_core_save,
        )

    async def _count_user_confirmations(self, user_id: str) -> int:
        return await review_ctx_service.count_user_confirmations(user_id)

    @staticmethod
    def _derive_fatigue_state(confirmation_count: int) -> dict:
        return review_ctx_service.derive_fatigue_state(confirmation_count)

    @staticmethod
    def _apply_fatigue_to_review_state(review_state: dict, fatigue: dict) -> dict:
        return review_ctx_service.apply_fatigue_to_review_state(review_state, fatigue)

    async def review_task_contract(
        self,
        draft_id: str,
        checkpoint: str,
        decision: str,
        detail: dict,
        user_id: str,
    ) -> WorkbenchTaskContractReviewResponse:
        return await review_ctx_service.review_task_contract(
            draft_id=draft_id,
            checkpoint=checkpoint,
            decision=decision,
            detail=detail,
            user_id=user_id,
        )

    # ═══════════════════════════════════════════════════════════════
    # 一步到位 skill 创建：后台任务模式
    # ═══════════════════════════════════════════════════════════════

    async def start_skill_creation_task(self, *, message: str, user_id: str) -> str:
        return await review_ctx_service.start_skill_creation_task(message=message, user_id=user_id)

    async def get_my_active_draft(self, *, user_id: str) -> dict | None:
        return await review_ctx_service.get_my_active_draft(user_id=user_id)

    async def stream_skill_creation(
        self, *, message: str, user_id: str,
    ):
        """
        一步到位 skill 创建主流程（异步生成器，专给 WS 端点用）。

        流程：
          1. 创建 SkillStudioDraft 行（generation_status=running）
          2. 调 skill_creation_runner.run_skill_creation
          3. 把 runner 的事件原样 yield 给 WS 端点（前端直接消费）
          4. 拦截 SKILL_READY 事件，把 contract + 6 文件落到 DB（generation_status=ready）
          5. 拦截 ERROR 事件，把诊断信息写到 error_detail（generation_status=failed）

        yield 的事件除了 runner 的所有事件外，还会在最开始多 yield 一个
        DRAFT_CREATED 事件（含 draft_id），让前端立即拿到 draft_id 用于后续
        review / finalize 调用。
        """
        from app.coding_agent.skill_creation_runner import (
            run_skill_creation,
            CreationEventType,
        )

        draft_id = f"draft-{uuid4().hex[:12]}"
        now = _now_bjt()

        # 1) 先建草稿（pending → running 由 runner STARTED 事件推进）
        async with _sf()() as session:
            session.add(
                SkillStudioDraft(
                    id=draft_id,
                    skill_id=None,
                    branch="main",
                    user_id=user_id,
                    source_message=message,
                    intent_md=None,
                    skill_md=None,
                    policy_yaml=None,
                    contract_json=None,
                    review_state_json=None,
                    locked_until=review_expires_at(),
                    confirmation_count=0,
                    trust_mode=False,
                    extra_files=None,
                    generation_status="running",
                    error_detail=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

        # 让前端立即拿到 draft_id（review / finalize 调用都需要它）
        yield {"type": "draft_created", "draft_id": draft_id}

        last_contract: dict | None = None

        # 2) 跑 runner，把每个事件包成 WS 帧
        async for ev in run_skill_creation(message=message, draft_id=draft_id, user_id=user_id):
            payload = ev.to_dict()
            yield payload

            if ev.type == CreationEventType.CONTRACT_READY:
                last_contract = ev.payload.get("contract")

            elif ev.type == CreationEventType.SKILL_READY:
                # 3) 写完整 skill 到 DB
                contract = ev.payload.get("contract") or last_contract or {}
                files = ev.payload.get("files") or {}
                # 4 个标准字段抽出来占独立列，剩下的 (scripts/* tests/*) 进 extra_files
                standard_keys = {"SKILL.md", "intent.md", "policy.yaml", "contract.json"}
                extra = {k: v for k, v in files.items() if k not in standard_keys}

                review_state = merge_review_state(contract, {})
                from app.common.contract_schema import build_verified_preview
                preview_payload = await build_verified_preview(
                    contract,
                    files,
                    cache_key=build_preview_cache_key(contract, files),
                )
                # 反疲劳：根据用户历史确认次数自动 approve 部分 checkpoint
                user_confirmations = await self._count_user_confirmations(user_id)
                fatigue = self._derive_fatigue_state(user_confirmations)
                review_state = self._apply_fatigue_to_review_state(review_state, fatigue)
                gate = build_gate_status(contract, review_state, preview_payload)

                async with _sf()() as session:
                    draft = (
                        await session.execute(
                            select(SkillStudioDraft).where(SkillStudioDraft.id == draft_id)
                        )
                    ).scalar_one()
                    draft.contract_json = contract
                    draft.skill_md = files.get("SKILL.md")
                    draft.intent_md = files.get("intent.md")
                    draft.policy_yaml = files.get("policy.yaml")
                    draft.extra_files = extra
                    draft.review_state_json = review_state
                    draft.generation_status = "ready"
                    draft.updated_at = _now_bjt()
                    await session.commit()

                # 给前端推一个增强版 SKILL_READY，附带 review_state / gate / fatigue / draft_id
                # （前端拿到这个就能渲染 4 必感知点卡片）
                yield {
                    "type": "skill_ready_enriched",
                    "draft_id": draft_id,
                    "contract": contract,
                    "checkpoints": checkpoint_list(review_state),
                    "gate": gate,  # build_gate_status 返回 dict
                    "fatigue": fatigue,
                    "preview": preview_payload,
                }

            elif ev.type == CreationEventType.ERROR:
                # 4) 写失败信息到 DB（保留 scratch_dir 路径供 debug）
                async with _sf()() as session:
                    draft = (
                        await session.execute(
                            select(SkillStudioDraft).where(SkillStudioDraft.id == draft_id)
                        )
                    ).scalar_one()
                    draft.generation_status = "failed"
                    draft.error_detail = {
                        "code": ev.payload.get("code"),
                        "detail": ev.payload.get("detail"),
                        "scratch_dir": ev.payload.get("scratch_dir"),
                        "failed_at": isoformat_bjt(_now_bjt()),
                    }
                    draft.updated_at = _now_bjt()
                    await session.commit()

    async def dismiss_draft(self, *, draft_id: str, user_id: str) -> dict:
        return await review_ctx_service.dismiss_draft(draft_id=draft_id, user_id=user_id)

    async def finalize_skill_creation(
        self, *, draft_id: str, user_id: str,
    ) -> dict:
        return await review_ctx_service.finalize_skill_creation(
            draft_id=draft_id,
            user_id=user_id,
        )

    async def bind_task_contract(self, draft_id: str, skill_id: str, user_id: str) -> WorkbenchTaskContractBindResponse:
        return await review_ctx_service.bind_task_contract(
            draft_id=draft_id,
            skill_id=skill_id,
            user_id=user_id,
        )

    async def create_session(self, skill_id: str, user_id: str, mode: WorkbenchMode = "novice", active_module: str | None = None) -> WorkbenchSessionResponse:
        return await session_orchestrator.create_session(
            skill_id=skill_id,
            user_id=user_id,
            mode=mode,
            active_module=active_module,
        )

    async def get_session(self, skill_id: str, session_id: str, user_id: str) -> WorkbenchSessionResponse:
        return await session_orchestrator.get_session(
            skill_id=skill_id,
            session_id=session_id,
            user_id=user_id,
        )

    async def analyze_intent(self, skill_id: str, session_id: str, user_id: str, message: str, active_module: str | None = None) -> WorkbenchIntentResponse:
        return await session_orchestrator.analyze_intent(
            skill_id=skill_id,
            session_id=session_id,
            user_id=user_id,
            message=message,
            active_module=active_module,
        )

    async def load_coding_timeline(self, skill_id: str, user_id: str, limit: int = 50) -> list[dict]:
        return await _load_coding_timeline(skill_id=skill_id, user_id=user_id, limit=limit)

    async def load_recent_ai_turns(self, skill_id: str, user_id: str, limit: int = 10) -> list[dict]:
        return await _load_recent_ai_turns(skill_id=skill_id, user_id=user_id, limit=limit)

    async def create_patch(self, skill_id: str, session_id: str, user_id: str, message: str, target_module: str, references: list[dict] | None = None) -> WorkbenchPatchResponse:
        return await wb_patch_service.create_patch(
            skill_id=skill_id,
            session_id=session_id,
            user_id=user_id,
            message=message,
            target_module=target_module,
            references=references,
        )

    async def validate_patch(self, skill_id: str, patch_id: str, user_id: str) -> WorkbenchValidateResponse:
        return await wb_patch_service.validate_patch(
            skill_id=skill_id,
            patch_id=patch_id,
            user_id=user_id,
        )

    async def apply_patch(self, skill_id: str, patch_id: str, user_id: str) -> WorkbenchApplyResponse:
        return await wb_patch_service.apply_patch(
            skill_id=skill_id,
            patch_id=patch_id,
            user_id=user_id,
        )

    async def generate_draft(self, message: str, references: list[dict] | None = None) -> WorkbenchGenerateDraftResponse:
        ai_result = await _generate_draft_with_ai(message, references)
        if ai_result:
            return ai_result
        return _fallback_generate_draft(message, references)

    async def create_skill_from_draft(self, draft: SkillStructure, user_id: str, skill_id: str | None = None) -> WorkbenchCreateSkillFromDraftResponse:
        from types import SimpleNamespace
        from app.auth.models import User

        draft_skill_id = skill_id or _slugify_skill_id(draft.meta.get("name", ""))
        actor = None
        async with _sf()() as session:
            result = await skill_service.create_skill(
                session,
                skill_id=draft_skill_id,
                name=draft.meta.get("name") or draft_skill_id,
                # W1-E: 不再 fallback "未指定"；缺失部门透传让 create_skill 抛 422
                department=draft.meta.get("department") or "",
                role="",
                trigger_type=draft.meta.get("trigger_type") or "manual",
                risk_level=draft.meta.get("risk_level") or "R2",
                skill_md=apply_patch_to_skill_document(draft_skill_id, draft.model_dump(), "goal", {"goal": draft.goal})["skill_md"],
                policy_pack={item.get("name"): item.get("default_value") for item in draft.params if item.get("name")},
                user_id=user_id,
            )
            user = await session.get(User, user_id)
            if user:
                actor = SimpleNamespace(
                    id=user.id,
                    role=user.role,
                    department=user.department,
                    can_view_all=user.can_view_all,
                )
            await session.commit()
        try:
            from app.execution.sync_service import sync_service

            await sync_service.push_skill_to_targets(result["skill_id"], actor=actor)
        except Exception as exc:  # noqa: BLE001
            logger.warning("create_skill_from_draft sync 失败 skill={}: {}", result.get("skill_id"), exc)
        await audit.log(user_id, "workbench.create_skill", "skill", result["skill_id"])
        return WorkbenchCreateSkillFromDraftResponse(
            skill_id=result["skill_id"],
            git_commit=result.get("git_commit"),
            quality_score=result.get("quality_score"),
        )

    async def list_references(
        self,
        skill_id: str | None = None,
        query: str = "",
        source_type: str = "all",
        module: str = "all",
        reference_mode: str = "all",
        user_department: str | None = None,
    ) -> WorkbenchReferenceListResponse:
        return await session_orchestrator.list_references(
            skill_id=skill_id,
            query=query,
            source_type=source_type,
            module=module,
            reference_mode=reference_mode,
            user_department=user_department,
        )


    # ═══ Studio 新增：统一对话入口 ═══

    async def handle_chat(
        self,
        skill_id: str,
        session_id: str,
        message: str,
        context: dict,
        intent: str | None,
        reference_ids: list[str],
        user_id: str,
        department: str | None = None,
    ) -> dict:
        return await wb_assistant_service.handle_chat(
            create_patch=wb_patch_service.create_patch,
            generate_answer=self._generate_answer,
            skill_id=skill_id,
            session_id=session_id,
            message=message,
            context=context,
            intent=intent,
            reference_ids=reference_ids,
            user_id=user_id,
            department=department,
        )

    def _build_answer_prompts(self, skill_id: str, context: dict) -> tuple[str, str, str]:
        """提取 _generate_answer 的 prompt 构建逻辑，供同步和流式版本复用。

        Codex 审计修复：改用 PromptRegistry 渲染，让 prompt_hash 是模板版本 hash
        而非运行时字节串 hash，保证跨链路可比较。

        BUG 修复 (2026-04-07)：
        原代码用 `structure.purpose / steps / data_inputs` 等字段名访问 SkillStructure，
        但 SkillStructure (pydantic) 实际字段是 goal / rules / params / output_table / test_cases，
        所有 hasattr 永远 False → 永远输出"Skill 内容为空"，AI 助手看不到任何 Skill 内容。
        现已修正字段名 + 增加 output_table / test_cases 注入。

        Returns:
            (system_prompt, skill_context_text, prompt_hash) 三元组。
            prompt_hash 来自 PromptRegistry，可用于审计/cost 归因。
        """
        _VALID_MODULES = {"meta", "goal", "rules", "params", "output_table", "test_cases", "workflow"}
        raw_module = context.get("active_module")
        active_module = raw_module if raw_module in _VALID_MODULES else "goal"
        try:
            structure = context_builder.load_skill_structure(skill_id)
        except Exception:
            structure = None

        context_parts: list[str] = []
        if structure:
            skill_name = (structure.meta or {}).get("name") or skill_id

            # 目标（goal）
            if structure.goal:
                context_parts.append(f"【目标】\n{structure.goal[:800]}")

            # 决策规则（rules：list[dict]，每项含 name/branches）
            if structure.rules:
                rule_lines = []
                for s in structure.rules[:10]:
                    name = s.get("name", "未命名步骤")
                    branches = s.get("branches") or []
                    branches_text = "；".join(
                        f"{b.get('condition', '')} → {b.get('conclusion', '')}"
                        + (f"（动作：{b.get('action')}）" if b.get('action') else "")
                        for b in branches
                    )
                    rule_lines.append(f"- {name}: {branches_text}" if branches_text else f"- {name}")
                rules_text = "\n".join(rule_lines)
                context_parts.append(f"【规则/决策步骤】\n{rules_text[:1200]}")

            # 输入参数（params：list[dict]）
            if structure.params:
                params_text = "\n".join(
                    f"- {p.get('name', '')}"
                    + (f" (默认值: {p.get('default_value')})" if p.get('default_value') is not None else "")
                    + (f" — {p.get('description')}" if p.get('description') else "")
                    for p in structure.params[:20]
                )
                context_parts.append(f"【输入参数】\n{params_text[:600]}")

            # 输出定义（output_table：list[dict]）
            if structure.output_table:
                output_text = "\n".join(
                    f"- {o.get('name', '')}"
                    + (f" 格式={o.get('format')}" if o.get('format') else "")
                    + (f" 收件={o.get('recipient')}" if o.get('recipient') else "")
                    + (f" 审批级别=L{o.get('approval_level')}" if o.get('approval_level') is not None else "")
                    for o in structure.output_table[:10]
                )
                context_parts.append(f"【输出定义】\n{output_text[:600]}")

            # 测试用例（test_cases：list[dict]，仅展示数量和前 3 个名字，避免上下文膨胀）
            if structure.test_cases:
                tc_count = len(structure.test_cases)
                tc_names = "、".join(
                    str(t.get("name", "")) for t in structure.test_cases[:3] if t.get("name")
                )
                tc_summary = f"共 {tc_count} 个用例" + (f"（如：{tc_names}）" if tc_names else "")
                context_parts.append(f"【测试用例】\n{tc_summary}")
        else:
            skill_name = skill_id
            draft = context.get("draft_snapshot", {})
            module_data = draft.get(active_module) if draft else None
            if module_data:
                context_parts.append(f"【{active_module} 模块内容】\n{str(module_data)[:600]}")

        skill_context = "\n\n".join(context_parts) if context_parts else "（Skill 内容为空）"

        # F3+: 通过 PromptRegistry 构建，prompt_hash 是模板版本 hash
        try:
            from app.common.prompt_registry import prompt_registry
            system_prompt, prompt_hash = prompt_registry.build(
                "workbench_answer",
                context={"skill_name": skill_name, "skill_context": skill_context},
            )
        except KeyError:
            # registry 未注册时回退到内联文本（启动时一定会注册，这里只是防御）
            system_prompt = (
                f"你是 SkillForge 工作台助手，正在帮助用户分析 Skill「{skill_name}」。\n\n"
                f"{skill_context}\n\n"
                "请用中文简洁回答用户的问题。如果是提取/列举类请求，以 Markdown 列表格式返回结果。"
            )
            prompt_hash = ""

        return system_prompt, skill_context, prompt_hash

    async def handle_chat_stream(
        self,
        skill_id: str,
        session_id: str,
        message: str,
        context: dict,
        intent: str | None,
        reference_ids: list[str],
        user_id: str,
        department: str | None = None,
    ):
        async for event in wb_assistant_service.handle_chat_stream(
            handle_chat_fn=self.handle_chat,
            build_answer_prompts_fn=self._build_answer_prompts,
            handle_run_skill_stream_fn=self._handle_run_skill_stream,
            skill_id=skill_id,
            session_id=session_id,
            message=message,
            context=context,
            intent=intent,
            reference_ids=reference_ids,
            user_id=user_id,
            department=department,
        ):
            yield event

    async def _handle_run_skill_stream(
        self,
        skill_id: str,
        message: str,
        user_id: str,
    ):
        """run_skill 意图：拉远端 OpenClaw Gateway 执行当前 Skill,
        on_progress 回调把 agent 流式 token 转成 chat delta 事件。

        这是用户期待的「在对话框直接跑 Skill 看结果」体验,
        不用切到底部沙箱 Tab、不用手填 JSON 参数。
        """
        from app.execution.execution_service import execution_service

        yield {
            "type": "delta",
            "content": f"⚙️ 正在调用 `{skill_id}` 远端执行…\n\n",
        }

        # OpenClawClient.run_skill 的 on_progress 回调是 async,
        # 需要把它桥接到 generator 的 yield。用 asyncio.Queue 中转。
        import asyncio as _aio
        queue: _aio.Queue = _aio.Queue()
        SENTINEL = object()

        async def _on_progress(payload: dict):
            text = (payload or {}).get("text") or ""
            if text:
                await queue.put({"type": "delta", "content": text})

        async def _runner():
            try:
                result = await execution_service.execute_skill(
                    skill_id=skill_id,
                    params={
                        "query": message,
                        "triggered_by": f"chat:{user_id}",
                        "_execution_backend": "bridge_script",
                    },
                    sandbox=False,
                    triggered_by=f"chat:{user_id}",
                    run_mode="manual_real",
                )
                final_text = json.dumps(result if isinstance(result, dict) else {"output": str(result)}, ensure_ascii=False, indent=2)
                await queue.put({"type": "delta", "content": "\n\n```json\n" + final_text + "\n```"})
                await queue.put({"type": "done", "finish_reason": "run_skill_completed"})
            except AppError as e:
                await queue.put({
                    "type": "error",
                    "code": e.code,
                    "error": e.message or "远端执行失败",
                })
            except Exception as e:
                await queue.put({
                    "type": "error",
                    "code": "RUN_SKILL_FAILED",
                    "error": str(e)[:300],
                })
            finally:
                await queue.put(SENTINEL)

        runner_task = _aio.create_task(_runner())
        try:
            while True:
                ev = await queue.get()
                if ev is SENTINEL:
                    break
                yield ev
                if ev.get("type") == "error":
                    break
        finally:
            if not runner_task.done():
                runner_task.cancel()
                try:
                    await runner_task
                except (asyncio.CancelledError, Exception) as e:
                    logger.debug("coding runner task 取消等待异常: {}", e)

    async def _generate_answer(
        self,
        skill_id: str,
        message: str,
        context: dict,
        user_id: str | None = None,
        department: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """基于 Skill 上下文生成纯文本回答。

        跨模块问题（如「提取参数」）会同时注入目标和规则内容，让 LLM 有完整上下文。

        Codex 审计修复：
        - 用 _build_answer_prompts 统一构建（消除重复，PromptRegistry 渲染）
        - 接受 user_id/department/session_id 用于成本归因
        """
        # 复用 _build_answer_prompts，避免和 handle_chat_stream 不一致
        try:
            system_prompt, skill_context, prompt_hash = self._build_answer_prompts(skill_id, context)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("_build_answer_prompts 失败: %s", e)
            return f"当前 Skill 「{skill_id}」 上下文加载失败，请检查 Skill 是否存在。"

        cost_context = {
            "user_id": user_id,
            "department": department,
            "skill_id": skill_id,
            "conversation_id": session_id,
            "prompt_hash": prompt_hash,
        }

        try:
            resp = await call_llm(
                system=system_prompt,
                user=message,
                max_tokens=800,
                temperature=0.3,
                json_mode=False,
                call_source="workbench_chat",
                cost_context=cost_context,
            )
            if isinstance(resp, str):
                return resp
            if isinstance(resp, dict):
                return resp.get("content", resp.get("text", "抱歉，无法生成回答。"))
            return "抱歉，无法生成回答。"
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("AI answer generation failed: %s", e)
            if skill_context and skill_context != "（Skill 内容为空）":
                return f"**Skill 内容摘要（AI 暂时不可用）**\n\n{skill_context}"
            active_module = context.get("active_module") or "goal"
            return f"当前「{active_module}」模块的内容暂时无法加载，请检查 Skill 是否存在。"

    async def handle_command(
        self,
        skill_id: str,
        session_id: str,
        command: str,
        context: dict,
        user_id: str,
    ) -> dict:
        return await wb_assistant_service.handle_command(
            skill_id=skill_id,
            session_id=session_id,
            command=command,
            context=context,
            user_id=user_id,
        )

    async def apply_partial_patch(
        self, skill_id: str, patch_id: str,
        accepted_hunks: list[int], rejected_hunks: list[int],
        user_id: str,
    ) -> dict:
        return await wb_patch_service.apply_partial_patch(
            skill_id=skill_id,
            patch_id=patch_id,
            accepted_hunks=accepted_hunks,
            rejected_hunks=rejected_hunks,
            user_id=user_id,
        )

    async def cleanup_expired_sessions(self, max_age_hours: int = 72) -> int:
        return await session_orchestrator.cleanup_expired_sessions(max_age_hours=max_age_hours)

    async def load_coding_timeline(self, *, skill_id: str, user_id: str, limit: int = 80) -> list[dict]:
        from app.workbench.message_persistence import load_coding_timeline

        return await load_coding_timeline(skill_id=skill_id, user_id=user_id, limit=limit)

    async def load_recent_ai_turns(self, *, skill_id: str, user_id: str, limit: int = 10) -> list[dict]:
        from app.workbench.message_persistence import load_recent_ai_turns

        return await load_recent_ai_turns(skill_id=skill_id, user_id=user_id, limit=limit)

    async def resume_coding_chat_stream(
        self,
        *,
        skill_id: str,
        user_id: str,
        last_seq: int = 0,
    ):
        """
        客户端重连 / 刷新后恢复 chat：不发新 user_message，只把后端 session 里
        未被旧 WS 消费的事件（fe_history 中 fe_seq > last_seq）补发出去，然后续接 live 流。

        last_seq 使用前端细粒度 fe_seq（每个 translated Event 独立编号）。
        """
        from app.coding_agent.session_service import session_service as cas
        try:
            async for event in cas.resume_stream(
                skill_id=skill_id, user_id=user_id, last_fe_seq=last_seq,
            ):
                yield event.to_dict()
        except AppError as e:
            yield {"type": "error", "code": e.code, "error": e.message}
        except Exception as e:  # noqa: BLE001
            from loguru import logger
            logger.exception(f"coding resume stream crashed: {e}")
            yield {
                "type": "error",
                "code": "CODING_AGENT_INTERNAL",
                "error": str(e)[:300],
            }

    # ═══════════════════════════════════════════════════════════════
    # Phase 3: 接入 coding_agent (vendor/aiclawcode) 的真 Agent 对话
    # 与旧 handle_chat / handle_chat_stream 完全独立的新链路。
    # 旧链路保留用于 task_contract / patch 模式 (intent in patch_intents)。
    # ═══════════════════════════════════════════════════════════════

    async def handle_coding_chat_stream(
        self,
        *,
        skill_id: str,
        user_id: str,
        message: str,
        session_id: str | None = None,
        images: list | None = None,
        mode: str = "edit",
    ):
        """
        把 coding_agent.session_service.chat 的 Event 流转成
        Workbench WS 协议帧 (dict) yield 出去。

        前端收到的事件类型：
            session_ready / text_delta / tool_call / tool_result / file_change /
            permission_request / usage / done / error

        AI auto-commit (流结束时):
            aiclawcode 的 Edit/Write/MultiEdit 工具是文件系统操作, 不走 git。
            为了让"版本"tab 能看到 AI 的改动, chat 流结束时如果 git 仓库 dirty,
            自动 commit 一次。commit message 用 user 的 prompt 前缀, 让人能
            一眼看出"这是 AI 因为什么需求改的"。
        """
        from app.coding_agent.schemas import EventType
        from app.coding_agent.session_service import session_service as cas
        from app.common.audit import audit
        from loguru import logger

        session = None
        try:
            session = await cas.ensure_session(skill_id=skill_id, user_id=user_id, mode=mode)
        except Exception:
            session = None

        # 审计：用户开启 coding 对话
        try:
            await audit.log(
                user_id,
                "coding_agent.chat_start",
                target_type="skill",
                target_id=skill_id,
                detail={"message_preview": message[:200]},
                prompt_hash=getattr(session, "prompt_hash", None),
            )
        except Exception as e:
            logger.warning(f"audit log failed: {e}")

        async def _persist_timeline(
            *,
            session_id: str,
            role: str,
            content: str,
            intent_json: dict | None = None,
        ) -> str:
            safe_session_id = await ensure_session_row(
                session_id=session_id,
                skill_id=skill_id,
                user_id=user_id,
                mode=mode,
            )
            await save_message(
                session_id=safe_session_id,
                role=role,
                content=content,
                intent_json=intent_json,
            )
            return safe_session_id

        current_session_id = session_id or build_coding_session_id(skill_id, user_id)
        current_session_id = await ensure_session_row(
            session_id=current_session_id,
            skill_id=skill_id,
            user_id=user_id,
            mode=mode,
        )
        baseline_git_signature = await self._skill_git_signature(skill_id)
        await _persist_timeline(
            session_id=current_session_id,
            role="user",
            content=message,
            intent_json={
                "type": "user_message",
                "mode": mode,
                "images": len(images or []),
            },
        )

        had_file_changes = False
        assistant_chunks: list[str] = []
        terminal_seen = False
        fallback_error: dict | None = None
        auto_commit_checked = False
        ran_command = False
        ran_tests = False
        ran_samples = False
        used_real_data = False
        generated_reports = False
        generated_todos = False

        async def _maybe_auto_commit(payload: dict | None = None) -> str | None:
            nonlocal auto_commit_checked
            if auto_commit_checked or mode != "edit":
                return None
            auto_commit_checked = True
            current_signature = await self._skill_git_signature(skill_id)
            if not had_file_changes and current_signature == baseline_git_signature:
                return None
            sha = await self._auto_commit_ai_changes(
                skill_id=skill_id, user_id=user_id, prompt=message,
            )
            if sha:
                diff_summary = await self._git_commit_diff_summary(skill_id, sha)
                changed_files = [
                    str(item.get("path"))
                    for item in (diff_summary or {}).get("files", [])
                    if isinstance(item, dict) and item.get("path")
                ]
                if payload is not None:
                    payload["git_commit"] = sha[:8]
                    payload["git_commit_full"] = sha
                    payload["changed_files"] = changed_files
                    if diff_summary:
                        payload["diff_summary"] = diff_summary
                await _persist_timeline(
                    session_id=current_session_id,
                    role="system",
                    content="git_commit",
                    intent_json={
                        "type": "git_commit",
                        "git_commit": sha[:8],
                        "git_commit_full": sha,
                        "changed_files": changed_files,
                        "diff_summary": diff_summary,
                    },
                )
            return sha

        try:
            try:
                async for event in cas.chat(skill_id=skill_id, user_id=user_id, message=message, images=images, mode=mode):
                    payload = event.to_dict()
                    event_session_id = payload.get("session_id")

                    if event.type == EventType.FILE_CHANGE:
                        had_file_changes = True

                    if event.type == EventType.SESSION_READY:
                        if event_session_id:
                            current_session_id = str(event_session_id)
                        current_session_id = await _persist_timeline(
                            session_id=current_session_id,
                            role="system",
                            content="session_ready",
                            intent_json=payload,
                        )
                        payload["session_id"] = current_session_id
                        yield payload
                        continue

                    if event.type == EventType.TEXT_DELTA:
                        text = str(payload.get("content") or "")
                        if text:
                            assistant_chunks.append(text)

                    elif event.type == EventType.TOOL_CALL:
                        tool_text = json.dumps(payload, ensure_ascii=False).lower()
                        if any(marker in tool_text for marker in ("pytest", "python", "run", "执行", "测试")):
                            ran_command = True
                        if "pytest" in tool_text or "test_main" in tool_text:
                            ran_tests = True
                        if "sample_input" in tool_text or "sandbox" in tool_text or "fixtures/" in tool_text:
                            ran_samples = True
                        if any(marker in tool_text for marker in ("fetch_api", "capture_apis", "skillforge_sdk", "真实", "api")) and "sample_input" not in tool_text:
                            used_real_data = True
                        await _persist_timeline(
                            session_id=current_session_id,
                            role="tool",
                            content=f"tool_call:{payload.get('tool') or ''}",
                            intent_json=payload,
                        )

                        # tool_call 落 decision_log（审计 tool_use）
                        try:
                            await audit.log(
                                user_id,
                                "coding_agent.tool_use",
                                target_type="skill",
                                target_id=skill_id,
                                detail={
                                    "tool": payload.get("tool"),
                                    "decision": payload.get("decision"),
                                    "input_preview": str(payload.get("input"))[:200],
                                },
                                prompt_hash=payload.get("prompt_hash"),
                            )
                        except Exception as e:
                            logger.debug("coding_agent 工具使用审计写入失败: {}", e)

                    elif event.type == EventType.TOOL_RESULT:
                        result_text = json.dumps(payload, ensure_ascii=False).lower()
                        if "reports" in result_text:
                            generated_reports = True
                        if "todos" in result_text:
                            generated_todos = True
                        await _persist_timeline(
                            session_id=current_session_id,
                            role="result",
                            content=f"tool_result:{payload.get('id') or ''}",
                            intent_json=payload,
                        )

                    elif event.type == EventType.FILE_CHANGE:
                        await _persist_timeline(
                            session_id=current_session_id,
                            role="tool",
                            content=f"file_change:{payload.get('path') or ''}",
                            intent_json=payload,
                        )

                    elif event.type == EventType.USAGE:
                        await _persist_timeline(
                            session_id=current_session_id,
                            role="system",
                            content="usage",
                            intent_json=payload,
                        )

                    elif event.type in {EventType.DONE, EventType.ERROR}:
                        assistant_text = "".join(assistant_chunks).strip()
                        if assistant_text:
                            await _persist_timeline(
                                session_id=current_session_id,
                                role="assistant",
                                content=assistant_text,
                                intent_json={"type": "assistant_text", "source": "coding_agent"},
                            )
                            assistant_chunks.clear()
                        payload.update({
                            "ran": ran_command,
                            "ran_tests": ran_tests,
                            "ran_samples": ran_samples,
                            "used_real_data": used_real_data,
                            "generated_reports": generated_reports,
                            "generated_todos": generated_todos,
                        })
                        if event.type == EventType.ERROR:
                            payload["error_reason"] = attribute_ai_error(
                                str(payload.get("code") or ""),
                                str(payload.get("error") or payload.get("message") or ""),
                            )
                        await _persist_timeline(
                            session_id=current_session_id,
                            role="system",
                            content=str(event.type.value),
                            intent_json=payload,
                        )
                        try:
                            await _maybe_auto_commit(payload)
                        except Exception as commit_exc:
                            logger.warning(
                                f"AI auto-commit 失败 skill={skill_id}: {commit_exc}"
                            )
                            payload["git_commit_error"] = str(commit_exc)[:300]
                        terminal_seen = True

                    yield payload
                    if terminal_seen:
                        break
            except AppError as e:
                fallback_error = {"type": "error", "code": e.code, "error": e.message}
            except Exception as e:  # noqa: BLE001
                logger.exception(f"coding chat stream crashed: {e}")
                fallback_error = {
                    "type": "error",
                    "code": "CODING_AGENT_INTERNAL",
                    "error": str(e)[:300],
                }
            if not terminal_seen:
                assistant_text = "".join(assistant_chunks).strip()
                if assistant_text:
                    await _persist_timeline(
                        session_id=current_session_id,
                        role="assistant",
                        content=assistant_text,
                        intent_json={"type": "assistant_text", "source": "coding_agent"},
                    )
                error_event = fallback_error or {
                    "type": "error",
                    "code": "CODING_AGENT_STREAM_INCOMPLETE",
                    "error": "AI 会话已中断，未返回完成事件，请重新发送或刷新续流",
                }
                error_event.update({
                    "ran": ran_command,
                    "ran_tests": ran_tests,
                    "ran_samples": ran_samples,
                    "used_real_data": used_real_data,
                    "generated_reports": generated_reports,
                    "generated_todos": generated_todos,
                    "error_reason": attribute_ai_error(
                        str(error_event.get("code") or ""),
                        str(error_event.get("error") or error_event.get("message") or ""),
                    ),
                })
                await _persist_timeline(
                    session_id=current_session_id,
                    role="system",
                    content="error",
                    intent_json=error_event,
                )
                try:
                    await _maybe_auto_commit(error_event)
                except Exception as commit_exc:
                    logger.warning(
                        f"AI auto-commit 失败 skill={skill_id}: {commit_exc}"
                    )
                    error_event["git_commit_error"] = str(commit_exc)[:300]
                yield error_event
        finally:
            if not auto_commit_checked:
                try:
                    await _maybe_auto_commit()
                except Exception as commit_exc:
                    logger.warning(
                        f"AI auto-commit 失败 skill={skill_id}: {commit_exc}"
                    )

    async def _skill_git_signature(self, skill_id: str) -> str:
        """返回某个 Skill 当前 git 工作状态签名，用于识别 Bash 等非 Edit 工具写文件。"""
        import asyncio
        import hashlib
        from app.skills.core.git_service import git_service

        def _inner() -> str:
            try:
                repo = git_service.repo
                rel = str(git_service.skill_dir(skill_id).relative_to(git_service.repo_path))
                parts = [
                    repo.git.status("--porcelain", "--", rel),
                    repo.git.diff("HEAD", "--", rel),
                    repo.git.diff("--cached", "HEAD", "--", rel),
                ]
                untracked = [
                    path for path in repo.untracked_files
                    if path == rel or path.startswith(rel + "/")
                ]
                for path in sorted(untracked):
                    parts.append(f"?? {path}")
                    fpath = git_service.repo_path / path
                    if fpath.is_file():
                        try:
                            parts.append(hashlib.sha256(fpath.read_bytes()).hexdigest())
                        except OSError:
                            parts.append("<unreadable>")
                return hashlib.sha256("\n".join(parts).encode("utf-8", errors="replace")).hexdigest()
            except Exception:
                return ""

        return await asyncio.to_thread(_inner)

    async def _git_commit_diff_summary(self, skill_id: str, commit_sha: str) -> dict | None:
        """返回 AI auto-commit 的 diff summary，供前端回合面板直接展示。"""
        import asyncio
        from app.skills.core.git_service import git_service

        def _inner() -> dict | None:
            try:
                repo = git_service.repo
                commit = repo.commit(commit_sha)
                if not commit.parents:
                    return None
                summary = git_service.diff_summary(
                    skill_id,
                    commit_a=commit.parents[0].hexsha,
                    commit_b=commit.hexsha,
                )
                prefix = f"{skill_id}/"
                for item in summary.get("files") or []:
                    if isinstance(item, dict) and isinstance(item.get("path"), str):
                        path = item["path"]
                        if path.startswith(prefix):
                            item["path"] = path[len(prefix):]
                return summary
            except Exception as exc:  # noqa: BLE001
                logger.debug("AI auto-commit diff summary 失败 skill={} sha={}: {}", skill_id, commit_sha, exc)
                return None

        return await asyncio.to_thread(_inner)

    async def _auto_commit_ai_changes(
        self, *, skill_id: str, user_id: str, prompt: str,
    ) -> str | None:
        """流结束后把 AI 的改动 commit 一次, 让 git history 能追溯。

        - 只 commit 这个 skill 子目录, 避免污染其他 skill 的 staging
        - commit message 包含 user prompt 前缀 + AI auto-commit 标记
        - 用 asyncio.to_thread 避免阻塞 event loop
        """
        import asyncio
        from app.skills.core.git_service import git_service
        from loguru import logger

        async def _do_commit_safe() -> str | None:
            async with git_service.skill_advisory_lock(skill_id):
                def _inner():
                    if not git_service.has_uncommitted_changes(skill_id):
                        return None
                    short_prompt = (prompt or "").strip().replace("\n", " ")
                    if len(short_prompt) > 80:
                        short_prompt = short_prompt[:77] + "..."
                    commit_msg = f"AI: {short_prompt or '修改 Skill'}"
                    return git_service.commit_all(
                        commit_msg, author=user_id, skill_id=skill_id, validate=False,
                    )
                return await asyncio.to_thread(_inner)

        sha = await _do_commit_safe()
        if sha:
            logger.info(
                f"AI auto-commit skill={skill_id} sha={sha[:8]} prompt={prompt[:60]!r}"
            )
        return sha

    async def coding_respond_permission(
        self,
        *,
        skill_id: str,
        user_id: str,
        request_id: str,
        behavior: str,
        updated_input: dict | None = None,
        message: str | None = None,
    ) -> None:
        """前端用户对 permission_request 的响应。"""
        from app.coding_agent.session_service import session_service as cas
        await cas.respond_permission(
            skill_id=skill_id,
            user_id=user_id,
            request_id=request_id,
            behavior=behavior,
            updated_input=updated_input,
            message=message,
        )

    async def coding_interrupt(self, *, skill_id: str, user_id: str) -> None:
        """前端的"暂停"按钮 → 中断当前 AI 任务。"""
        from app.coding_agent.session_service import session_service as cas
        await cas.interrupt(skill_id=skill_id, user_id=user_id)

    async def coding_close_session(self, *, skill_id: str, user_id: str) -> None:
        from app.coding_agent.session_service import session_service as cas
        await cas.close_session(skill_id=skill_id, user_id=user_id)


workbench_service = WorkbenchService()
