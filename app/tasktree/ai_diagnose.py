"""任务树 Phase 2 智能异常诊断。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ai import call_llm_cached
from app.common.exceptions import AppError
from app.common.metrics import ai_diagnose_failure_rate, tasktree_slow_query_total
from app.common.time_utils import isoformat_bjt
from app.execution.models import DecisionLog, ExecutionRun, ExecutionStep
from app.skills.core.git_service import git_service
from app.skills.core.models import Skill
from app.skills.core.parser import skill_parser


# 滑动窗口近似失败率：最近 N 次结果
_DIAG_WINDOW_SIZE: int = 50
_diag_window: list[bool] = []  # True = success, False = failure


def _record_diag_result(success: bool) -> None:
    """维护诊断失败率 Gauge。"""
    _diag_window.append(success)
    if len(_diag_window) > _DIAG_WINDOW_SIZE:
        del _diag_window[: len(_diag_window) - _DIAG_WINDOW_SIZE]
    if _diag_window:
        failures = sum(1 for flag in _diag_window if not flag)
        try:
            ai_diagnose_failure_rate.set(failures / len(_diag_window))
        except Exception as e:
            logger.debug("ai_diagnose_failure_rate 指标上报失败: {}", e)


async def diagnose_failure(
    db: AsyncSession,
    run_id: str,
    *,
    user_id: str | None = None,
    department: str | None = None,
) -> dict[str, Any]:
    """分析一次失败执行并返回诊断结果。

    返回 dict：{"text": str, "ai_available": bool, "source": "llm"|"rule_engine"}
    """

    run = await db.get(ExecutionRun, run_id)
    if not run:
        raise AppError("NOT_FOUND", 404, {"detail": f"execution run {run_id} not found"})

    step = await _load_primary_step(db, run_id)
    decision_log = await _load_latest_decision_log(db, run_id)

    skill_id = step["skill_id"] if step else (decision_log.skill_id if decision_log else "")
    skill = await db.get(Skill, skill_id) if skill_id else None
    skill_md = git_service.read_file(skill_id, "SKILL.md") if skill_id else ""
    skill_md = skill_md or ""
    skill_parsed = skill_parser.parse(skill_md) if skill_md else None

    context = _build_context(run, step, decision_log, skill, skill_parsed, skill_md)
    context_hash = hashlib.md5(
        json.dumps(context, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]

    system = (
        "你是任务树异常诊断助手。基于执行记录、输入快照、错误信息和 Skill 目标，"
        "输出 1 段中文诊断，必须包含根因判断和可执行修复建议。"
        "不要分点，不要输出 JSON。"
    )
    user = (
        f"## 诊断上下文\n```json\n{json.dumps(context, ensure_ascii=False, indent=2, default=str)}\n```\n\n"
        "请输出 1 段话，不超过 220 字。"
    )

    llm_result: Any = None
    try:
        llm_result = await call_llm_cached(
            f"tasktree:diag:{run_id}:{context_hash}",
            system,
            user,
            cache_ttl=300,
            max_tokens=700,
            timeout=25,
            json_mode=False,
            call_source="tasktree.ai_diagnose",
            cost_context={
                "run_id": run_id,
                "skill_id": skill_id or None,
                "department": department or (skill.department if skill else None),
                "user_id": user_id,
                "prompt_hash": context_hash,
            },
        )
    except Exception as exc:
        logger.warning("ai_diagnose LLM 调用失败，走规则引擎兜底 run_id={} err={}", run_id, exc)
        try:
            tasktree_slow_query_total.labels(operation="ai_diagnose").inc()
        except Exception as metric_err:
            logger.debug("ai_diagnose slow_query 指标上报失败: {}", metric_err)
        llm_result = None

    text = _normalize_llm_text(llm_result)
    if text:
        _record_diag_result(True)
        return {"text": text, "ai_available": True, "source": "llm"}

    # LLM 不可用/空返回 → 规则引擎 fallback
    _record_diag_result(False)
    return {
        "text": _fallback_diagnosis(context),
        "ai_available": False,
        "source": "rule_engine",
    }


async def _load_primary_step(db: AsyncSession, run_id: str) -> dict[str, Any] | None:
    stmt = (
        select(
            ExecutionStep.skill_id,
            ExecutionStep.status,
            ExecutionStep.input_data,
            ExecutionStep.output_data,
            ExecutionStep.duration_ms,
            ExecutionStep.error_message,
            ExecutionStep.started_at,
            ExecutionStep.completed_at,
            Skill.name.label("skill_name"),
            Skill.department.label("skill_department"),
        )
        .join(Skill, Skill.id == ExecutionStep.skill_id)
        .where(ExecutionStep.run_id == run_id)
        .order_by(ExecutionStep.step_order.asc().nullsfirst(), ExecutionStep.id.asc())
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if not row:
        return None
    return {
        "skill_id": row.skill_id,
        "skill_name": row.skill_name,
        "skill_department": row.skill_department,
        "status": row.status,
        "input_data": row.input_data,
        "output_data": row.output_data,
        "duration_ms": row.duration_ms,
        "error_message": row.error_message,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
    }


async def _load_latest_decision_log(db: AsyncSession, run_id: str) -> DecisionLog | None:
    stmt = (
        select(DecisionLog)
        .where(DecisionLog.run_id == run_id)
        .order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _build_context(
    run: ExecutionRun,
    step: dict[str, Any] | None,
    decision_log: DecisionLog | None,
    skill: Skill | None,
    skill_parsed,
    skill_md: str,
) -> dict[str, Any]:
    skill_goal = ""
    if skill_parsed is not None:
        skill_goal = getattr(skill_parsed, "purpose", "") or ""

    return {
        "run": {
            "id": run.id,
            "status": run.status,
            "trigger_type": run.trigger_type,
            "started_at": isoformat_bjt(run.started_at),
            "completed_at": isoformat_bjt(run.completed_at),
            "summary": run.summary,
        },
        "step": step,
        "decision_log": _serialize_decision_log(decision_log) if decision_log else None,
        "skill": {
            "id": getattr(skill, "id", None) or (step["skill_id"] if step else ""),
            "name": getattr(skill, "name", None),
            "department": getattr(skill, "department", None),
            "goal": skill_goal,
            "markdown_excerpt": skill_md[:4000],
        },
    }


def _serialize_decision_log(log: DecisionLog) -> dict[str, Any]:
    return {
        "id": log.id,
        "run_id": log.run_id,
        "skill_id": log.skill_id,
        "approval_status": log.approval_status,
        "approval_level": log.approval_level,
        "user_action": log.user_action,
        "user_feedback": log.user_feedback,
        "reject_reason": log.reject_reason,
        "business_impact": log.business_impact,
        "input_snapshot": log.input_snapshot,
        "output_result": log.output_result,
        "created_at": isoformat_bjt(log.created_at),
        "token_count": log.token_count,
        "model_id": log.model_id,
    }


def _normalize_llm_text(result: Any) -> str | None:
    if isinstance(result, str):
        text = result.strip()
        return text or None
    if isinstance(result, dict):
        for key in ("diagnosis", "recommendation", "summary", "result"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _fallback_diagnosis(context: dict[str, Any]) -> str:
    run = context["run"]
    step = context.get("step") or {}
    decision_log = context.get("decision_log") or {}
    skill = context.get("skill") or {}

    parts: list[str] = []
    status = run.get("status") or "unknown"
    error_message = step.get("error_message") or ""

    if error_message:
        if "timeout" in error_message.lower() or "超时" in error_message:
            parts.append("这次失败更像是执行超时，优先检查外部依赖、重试次数和超时阈值。")
        elif "key" in error_message.lower() or "missing" in error_message.lower():
            parts.append("这次失败更像是输入字段缺失或字段名不匹配，优先核对输入快照和参数映射。")
        else:
            parts.append(f"错误信息显示为「{error_message[:80]}」，建议先从该异常栈和输入边界排查。")
    else:
        parts.append(f"执行 {run['id']} 当前状态为 {status}，需要结合最近步骤和回写链路继续定位。")

    if decision_log.get("reject_reason"):
        parts.append(f"历史决策里的驳回原因是「{decision_log['reject_reason']}」，说明问题可能已经被人工识别。")

    goal = skill.get("goal") or ""
    if goal:
        parts.append(f"该 Skill 的目标是「{goal[:120]}」，检查失败分支是否覆盖了这个场景。")

    parts.append("建议先核对 SKILL.md、输入快照和外部系统状态，再重跑一次验证修复是否生效。")
    return "".join(parts)
