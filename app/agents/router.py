"""Department analysis Agent blueprint API."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from pypinyin import lazy_pinyin
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.access import can_access_department, role_matches_any
from app.auth.dependencies import require_state_active
from app.auth.models import User
from app.common.audit import audit
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt
from app.database import get_db
from app.org.models import OrgUnit, UserOrgMembership
from app.skills.core.access import SkillMember, get_skill_permissions
from app.skills.core.models import Skill

from .models import DepartmentAnalysisAgent


router = APIRouter()

DEFAULT_ANALYSIS_DIMENSIONS = [
    "首屏钩子",
    "商品露出",
    "节奏密度",
    "卖点证据",
    "行动引导",
]

ANALYSIS_OUTPUT_SECTIONS = [
    {"value": "executive_summary", "label": "核心结论"},
    {"value": "audit_review", "label": "团队/个人卡审"},
    {"value": "topic_comparisons", "label": "同主题对比"},
    {"value": "personal_improvements", "label": "个人改进"},
    {"value": "editing_mix", "label": "剪辑分类"},
    {"value": "next_actions", "label": "下一步动作"},
]

DEFAULT_PROMPT_GOAL = "按部门周度经营口径诊断同主题视频消耗，指出低消耗原因、可复制做法和下一轮动作。"

DEFAULT_PROMPT_FOCUS = [
    "同主题内高低消耗视频的首屏钩子、商品露出、节奏密度、卖点证据和行动引导差异",
    "团队和个人卡审拒因的集中问题",
    "可直接下发给剪辑、投放、审核协作人的改进动作",
]

DEFAULT_PROMPT_GUARDRAILS = [
    "只基于已授权的部门视频、消耗、卡审和脱敏样例输出做判断",
    "没有足够样本时明确标记为样本不足，避免把猜测写成结论",
    "个人改进建议只落到当前部门授权人员，不扩大数据范围",
]

DEFAULT_VERIFICATION_QUESTIONS = [
    "本周哪些同主题视频消耗低于同组均值，主要差异是什么？",
    "哪些个人或团队的卡审拒因最集中，需要下周优先修正？",
    "哪些输出章节会进入钉钉报告，是否生成个人待办？",
]

ANALYSIS_CAPABILITIES = [
    {"key": "video_metrics", "label": "云视频素材消耗", "type": "data"},
    {"key": "same_topic_compare", "label": "同主题对比", "type": "analysis"},
    {"key": "audit_rejects", "label": "卡审拒因复盘", "type": "analysis"},
    {"key": "personal_improvement", "label": "个人改进待办", "type": "output"},
    {"key": "dingtalk_report", "label": "钉钉周报", "type": "output"},
    {"key": "run_control", "label": "运行参数控制", "type": "control"},
]

ANALYSIS_PRESETS = [
    {
        "value": "weekly_standard",
        "label": "标准周报",
        "params": {
            "window_days": 7,
            "top_n": 5,
            "video_max_pages": 10,
            "audit_max_pages": 6,
            "audit_reject_detail_limit": 8,
            "output_sections": [item["value"] for item in ANALYSIS_OUTPUT_SECTIONS],
            "todo_enabled": True,
        },
    },
    {
        "value": "audit_review",
        "label": "卡审复盘",
        "params": {
            "window_days": 7,
            "top_n": 3,
            "video_max_pages": 8,
            "audit_max_pages": 12,
            "audit_reject_detail_limit": 30,
            "output_sections": ["executive_summary", "audit_review", "next_actions"],
            "todo_enabled": True,
        },
    },
    {
        "value": "personal_improvement",
        "label": "个人改进",
        "params": {
            "window_days": 7,
            "top_n": 5,
            "video_max_pages": 12,
            "audit_max_pages": 6,
            "audit_reject_detail_limit": 12,
            "output_sections": ["executive_summary", "topic_comparisons", "personal_improvements", "next_actions"],
            "todo_enabled": True,
        },
    },
    {
        "value": "brief_summary",
        "label": "轻量摘要",
        "params": {
            "window_days": 7,
            "top_n": 3,
            "video_max_pages": 5,
            "audit_max_pages": 3,
            "audit_reject_detail_limit": 5,
            "output_sections": ["executive_summary", "topic_comparisons"],
            "todo_enabled": False,
        },
    },
]

MODEL_PROFILE_OPTIONS = [
    {"value": "default", "label": "平台默认"},
    {"value": "deepseek-v4-flash", "label": "DeepSeek V4 Flash"},
    {"value": "deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
    {"value": "cheap", "label": "低成本档"},
]
MODEL_PROFILE_ALIASES = {
    "": "default",
    "default": "default",
    "platform": "default",
    "flash": "deepseek-v4-flash",
    "v4f": "deepseek-v4-flash",
    "deepseek-v4-flash": "deepseek-v4-flash",
    "pro": "deepseek-v4-pro",
    "v4-pro": "deepseek-v4-pro",
    "deepseek-v4-pro": "deepseek-v4-pro",
    "cheap": "cheap",
    "low-cost": "cheap",
    "deepseek-chat": "cheap",
}

DEFAULT_ANALYSIS_PARAMS = {
    "window": "rolling_7_complete_days",
    "timezone": "Asia/Shanghai",
    "same_topic_rule": "category_labels_title",
    "model_profile": "default",
    "window_days": 7,
    "top_n": 5,
    "video_max_pages": 10,
    "audit_max_pages": 6,
    "audit_reject_detail_limit": 8,
    "low_spend_rule": "zero_spend_first",
    "output_sections": [item["value"] for item in ANALYSIS_OUTPUT_SECTIONS],
    "todo_enabled": True,
    "report_channel": "dingtalk_markdown",
    "prompt_goal": DEFAULT_PROMPT_GOAL,
    "prompt_focus": DEFAULT_PROMPT_FOCUS,
    "prompt_guardrails": DEFAULT_PROMPT_GUARDRAILS,
    "verification_questions": DEFAULT_VERIFICATION_QUESTIONS,
}

ANALYSIS_CONTROL_SCHEMA = {
    "version": 1,
    "run_params": {
        "window_days": {"type": "integer", "min": 1, "max": 30, "label": "统计天数"},
        "top_n": {"type": "integer", "min": 1, "max": 20, "label": "同主题 TopN"},
        "video_max_pages": {"type": "integer", "min": 1, "max": 50, "label": "素材页数"},
        "audit_max_pages": {"type": "integer", "min": 1, "max": 30, "label": "卡审页数"},
        "audit_reject_detail_limit": {"type": "integer", "min": 0, "max": 100, "label": "拒因样本数"},
    },
    "output": {
        "sections": ANALYSIS_OUTPUT_SECTIONS,
        "todo_enabled": {"type": "boolean", "label": "生成个人改进待办"},
    },
    "model_profiles": MODEL_PROFILE_OPTIONS,
    "presets": ANALYSIS_PRESETS,
}


class AnalysisAgentUpsertRequest(BaseModel):
    id: str | None = Field(default=None, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    department: str = Field(min_length=1, max_length=100)
    department_id: str | None = Field(default=None, max_length=50)
    owner_user_id: str | None = Field(default=None, max_length=50)
    owner_query: str | None = Field(default=None, max_length=120)
    skill_id: str | None = Field(default=None, max_length=100)
    prompt_version: str = Field(default="analysis_v1", min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=2000)
    dimensions: list[str] = Field(default_factory=lambda: list(DEFAULT_ANALYSIS_DIMENSIONS))
    default_params: dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_ANALYSIS_PARAMS))
    editor_user_ids: list[str] = Field(default_factory=list)
    editor_queries: list[str] = Field(default_factory=list)
    status: str = Field(default="active", max_length=20)


class AnalysisAgentValidateRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


def _clean_agent_id(value: str | None, name: str) -> str:
    raw = (value or "").strip()
    if not raw:
        slug = "-".join(lazy_pinyin(name.strip()))
        slug = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")
        digest = hashlib.sha1(name.strip().encode("utf-8")).hexdigest()[:8]
        raw = f"agent-{slug[:44].strip('-')}-{digest}" if slug else f"agent-{digest}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,78}", raw):
        raise AppError("PARAM_INVALID", 400, {"field": "id", "detail": "Agent ID 格式非法"})
    return raw


async def _resolve_department(db: AsyncSession, user: User, department: str, department_id: str | None) -> OrgUnit:
    return await _resolve_department_by_scope(db, user, department, department_id, require_visible=True)


async def _resolve_department_by_scope(
    db: AsyncSession,
    user: User,
    department: str,
    department_id: str | None,
    *,
    require_visible: bool,
) -> OrgUnit:
    stmt = select(OrgUnit).where(OrgUnit.type == "department")
    if department_id:
        stmt = stmt.where(OrgUnit.id == department_id)
    else:
        stmt = stmt.where(OrgUnit.name == department)
    rows = (await db.execute(stmt.order_by(OrgUnit.path.asc()).limit(5))).scalars().all()
    if not rows:
        raise AppError("DEPARTMENT_NOT_FOUND", 404, {"department": department, "department_id": department_id})
    if not require_visible:
        return rows[0]
    visible = []
    for row in rows:
        if await can_access_department(db, user, row.id):
            visible.append(row)
    if not visible:
        raise AppError("FORBIDDEN", 403, {"detail": "当前账号无权访问该部门"})
    return visible[0]


async def _resolve_agent_department(
    db: AsyncSession,
    user: User,
    body: AnalysisAgentUpsertRequest,
    skill: Skill | None,
) -> OrgUnit:
    requested_department_id = (body.department_id or "").strip() or None
    dept_candidate = await _resolve_department_by_scope(
        db,
        user,
        body.department.strip(),
        requested_department_id,
        require_visible=False,
    )
    if await can_access_department(db, user, dept_candidate.id):
        return dept_candidate
    if not skill:
        raise AppError("FORBIDDEN", 403, {"detail": "当前账号无权访问该部门"})
    skill_department_id = getattr(skill, "org_unit_id", None)
    skill_department_name = str(getattr(skill, "department", "") or "").strip()
    same_skill_department = (
        bool(skill_department_id and dept_candidate.id == skill_department_id)
        or bool(skill_department_name and dept_candidate.name == skill_department_name)
    )
    if not same_skill_department:
        raise AppError("FORBIDDEN", 403, {"detail": "Agent 归属部门必须与绑定 Skill 部门一致"})
    # Codex/AIBP may be the owner/editor of a freshly submitted department Skill
    # even when their user org scope is elsewhere. Keep this path tied to the
    # object-level Skill permission checked by the caller and exact Skill dept.
    return dept_candidate


async def _resolve_user(
    db: AsyncSession,
    *,
    user_id: str | None = None,
    query: str | None = None,
    department_id: str | None = None,
    department_name: str | None = None,
) -> User | None:
    if user_id:
        user = await db.get(User, user_id)
        if not user or not user.is_active or user.state != "active":
            return None
        if not department_id:
            return user
        member = await db.get(UserOrgMembership, (user.id, department_id))
        if member or (department_name and user.department == department_name):
            return user
        return None
    needle = (query or "").strip()
    if not needle:
        return None
    like = f"%{needle}%"
    stmt = select(User).where(
        User.is_active == True,  # noqa: E712
        User.state == "active",
        or_(User.name.ilike(like), User.username.ilike(like), User.id.ilike(like), User.dingtalk_user_id.ilike(like)),
    )
    if department_id:
        member_ids = set(
            (
                await db.execute(
                    select(UserOrgMembership.user_id).where(UserOrgMembership.org_unit_id == department_id)
                )
            ).scalars().all()
        )
        scope_conditions = []
        if member_ids:
            scope_conditions.append(User.id.in_(member_ids))
        if department_name:
            scope_conditions.append(User.department == department_name)
        if scope_conditions:
            stmt = stmt.where(or_(*scope_conditions))
    rows = (await db.execute(stmt.order_by(User.name.asc(), User.id.asc()).limit(2))).scalars().all()
    if not rows:
        return None
    return rows[0]


async def _ensure_skill_editor(db: AsyncSession, skill_id: str, user_id: str, granted_by: str) -> None:
    existing = await db.get(SkillMember, (skill_id, user_id))
    if existing:
        if existing.role in {"owner", "editor"}:
            return
        existing.role = "editor"
        existing.granted_by = granted_by
        existing.granted_at = now_bjt()
        return
    db.add(
        SkillMember(
            skill_id=skill_id,
            user_id=user_id,
            role="editor",
            granted_by=granted_by,
            granted_at=now_bjt(),
        )
    )


def _normalize_model_profile(value: Any) -> str:
    raw = str(value or "default").strip().lower().replace("_", "-")
    normalized = MODEL_PROFILE_ALIASES.get(raw)
    if normalized:
        return normalized
    raise AppError(
        "PARAM_INVALID",
        400,
        {
            "field": "default_params.model_profile",
            "allowed": [item["value"] for item in MODEL_PROFILE_OPTIONS],
        },
    )


def _normalize_default_params(value: dict[str, Any] | None) -> dict[str, Any]:
    params = dict(value or {})
    params["model_profile"] = _normalize_model_profile(params.get("model_profile"))
    return params


async def _agent_permissions(
    db: AsyncSession,
    row: DepartmentAnalysisAgent,
    *,
    skill: Skill | None,
    current_user: User,
) -> dict[str, bool]:
    can_read = True
    can_edit = False
    can_control_run = False
    skill_perms = await get_skill_permissions(db, skill, current_user) if skill else {}
    is_owner_or_editor = (
        row.owner_user_id == current_user.id
        or current_user.id in set(row.editor_user_ids_json or [])
    )
    if _can_manage_business_agents(current_user) or is_owner_or_editor:
        department_visible = not row.department_id or await can_access_department(db, current_user, row.department_id)
        can_edit = bool(
            department_visible
            or is_owner_or_editor
            or skill_perms.get("edit")
            or skill_perms.get("manage_members")
        )
        can_control_run = can_edit and bool(row.skill_id)
    return {"read": can_read, "edit": can_edit, "control_run": can_control_run}


def _string_list(value: Any, fallback: list[str]) -> list[str]:
    source = value if isinstance(value, list) else fallback
    cleaned = [str(item).strip() for item in source if str(item).strip()]
    return cleaned or list(fallback)


def _effective_default_params(row: DepartmentAnalysisAgent) -> dict[str, Any]:
    default_params = dict(DEFAULT_ANALYSIS_PARAMS)
    if isinstance(row.default_params_json, dict):
        default_params.update(row.default_params_json)
    return default_params


def _prompt_view(row: DepartmentAnalysisAgent) -> dict[str, Any]:
    default_params = _effective_default_params(row)
    return {
        "version": row.prompt_version,
        "goal": str(default_params.get("prompt_goal") or DEFAULT_PROMPT_GOAL).strip(),
        "focus": _string_list(default_params.get("prompt_focus"), DEFAULT_PROMPT_FOCUS),
        "guardrails": _string_list(default_params.get("prompt_guardrails"), DEFAULT_PROMPT_GUARDRAILS),
        "verification_questions": _string_list(
            default_params.get("verification_questions"),
            DEFAULT_VERIFICATION_QUESTIONS,
        ),
        "editable_params": [
            "prompt_goal",
            "prompt_focus",
            "prompt_guardrails",
            "verification_questions",
            "dimensions",
            "output_sections",
            "model_profile",
        ],
    }


def _capabilities_view(row: DepartmentAnalysisAgent) -> list[dict[str, Any]]:
    default_params = _effective_default_params(row)
    output_sections = set(_string_list(default_params.get("output_sections"), []))
    todo_enabled = default_params.get("todo_enabled", True) is not False
    capabilities: list[dict[str, Any]] = []
    for item in ANALYSIS_CAPABILITIES:
        enabled = True
        if item["key"] == "personal_improvement":
            enabled = todo_enabled or "personal_improvements" in output_sections
        if item["key"] == "dingtalk_report":
            enabled = bool(default_params.get("report_channel"))
        capabilities.append({**item, "enabled": enabled})
    return capabilities


def _sample_run_payload(row: DepartmentAnalysisAgent, params: dict[str, Any] | None = None) -> dict[str, Any]:
    default_params = _effective_default_params(row)
    if params:
        default_params.update(params)
    dimensions = row.dimensions_json or []
    return {
        "skill_id": row.skill_id,
        "sandbox": True,
        "params": {
            **{
                key: default_params.get(key)
                for key in (
                    "window_days",
                    "top_n",
                    "video_max_pages",
                    "audit_max_pages",
                    "audit_reject_detail_limit",
                    "output_sections",
                    "todo_enabled",
                    "report_channel",
                    "preset",
                    "model_profile",
                )
                if key in default_params
            },
            "sample_data": True,
            "dry_run": True,
            "_analysis_agent_id": row.id,
            "analysis_agent": {
                "id": row.id,
                "name": row.name,
                "department": row.department,
                "skill_id": row.skill_id,
                "prompt_version": row.prompt_version,
                "dimensions": dimensions,
                "default_params": default_params,
            },
        },
    }


def _verification_view(row: DepartmentAnalysisAgent) -> dict[str, Any]:
    return {
        "mode": "config_payload",
        "sample_payload": _sample_run_payload(row),
        "expected_trace_fields": [
            "analysis_agent_control",
            "input_snapshot._analysis_agent_id",
            "input_snapshot.analysis_agent",
            "metadata.analysis_agent_control.prompt_version",
        ],
    }


def _control_view(row: DepartmentAnalysisAgent) -> dict[str, Any]:
    default_params = _effective_default_params(row)
    dimensions = row.dimensions_json or []
    return {
        "schema": ANALYSIS_CONTROL_SCHEMA,
        "effective": {
            "prompt_version": row.prompt_version,
            "dimensions": dimensions,
            "default_params": default_params,
            "output_sections": default_params.get("output_sections") or [],
            "todo_enabled": default_params.get("todo_enabled", True),
            "model_profile": default_params.get("model_profile") or "default",
        },
    }


def _serialize_agent(
    row: DepartmentAnalysisAgent,
    *,
    skill: Skill | None = None,
    owner: User | None = None,
    permissions: dict[str, bool] | None = None,
) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "department_id": row.department_id,
        "department": row.department,
        "owner_user_id": row.owner_user_id,
        "owner_name": getattr(owner, "name", None) if owner else None,
        "skill_id": row.skill_id,
        "skill_name": getattr(skill, "name", None) if skill else None,
        "prompt_version": row.prompt_version,
        "description": row.description or "",
        "dimensions": row.dimensions_json or [],
        "default_params": row.default_params_json or {},
        "editor_user_ids": row.editor_user_ids_json or [],
        "status": row.status,
        "is_active": bool(row.is_active),
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
        "permissions": permissions or {"read": True, "edit": False, "control_run": False},
        "control": _control_view(row),
        "prompt": _prompt_view(row),
        "capabilities": _capabilities_view(row),
        "verification": _verification_view(row),
    }


def _can_manage_business_agents(user: User) -> bool:
    return role_matches_any(user, ("admin", "dept_admin", "aibp"))


@router.get("/analysis-agents")
async def list_analysis_agents(
    request: Request,
    department: str = Query(default=""),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(DepartmentAnalysisAgent).where(DepartmentAnalysisAgent.is_active == True)  # noqa: E712
    if department:
        stmt = stmt.where(DepartmentAnalysisAgent.department == department)
    rows = (await db.execute(stmt.order_by(DepartmentAnalysisAgent.department.asc(), DepartmentAnalysisAgent.name.asc()))).scalars().all()
    skill_ids = {row.skill_id for row in rows if row.skill_id}
    skills = {
        item.id: item
        for item in (await db.execute(select(Skill).where(Skill.id.in_(skill_ids)))).scalars().all()
    } if skill_ids else {}
    visible: list[DepartmentAnalysisAgent] = []
    for row in rows:
        if not row.department_id or await can_access_department(db, current_user, row.department_id):
            visible.append(row)
            continue
        if row.owner_user_id == current_user.id or current_user.id in set(row.editor_user_ids_json or []):
            visible.append(row)
            continue
        skill = skills.get(row.skill_id)
        if skill:
            perms = await get_skill_permissions(db, skill, current_user)
            if perms.get("edit") or perms.get("manage_members"):
                visible.append(row)
    user_ids = {row.owner_user_id for row in visible if row.owner_user_id}
    users = {
        item.id: item
        for item in (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
    } if user_ids else {}
    items = []
    for row in visible:
        skill = skills.get(row.skill_id)
        items.append(
            _serialize_agent(
                row,
                skill=skill,
                owner=users.get(row.owner_user_id),
                permissions=await _agent_permissions(db, row, skill=skill, current_user=current_user),
            )
        )
    return {"items": items}


async def _load_visible_analysis_agent(
    db: AsyncSession,
    agent_id: str,
    current_user: User,
) -> tuple[DepartmentAnalysisAgent, Skill | None, User | None, dict[str, bool]]:
    row = await db.get(DepartmentAnalysisAgent, agent_id)
    if not row or not row.is_active:
        raise AppError("AGENT_NOT_FOUND", 404, {"agent_id": agent_id})
    skill = await db.get(Skill, row.skill_id) if row.skill_id else None
    visible = not row.department_id or await can_access_department(db, current_user, row.department_id)
    if not visible:
        visible = row.owner_user_id == current_user.id or current_user.id in set(row.editor_user_ids_json or [])
    if not visible and skill:
        skill_perms = await get_skill_permissions(db, skill, current_user)
        visible = bool(skill_perms.get("edit") or skill_perms.get("manage_members"))
    if not visible:
        raise AppError("FORBIDDEN", 403, {"detail": "当前账号无权访问该 Agent"})
    owner = await db.get(User, row.owner_user_id) if row.owner_user_id else None
    permissions = await _agent_permissions(db, row, skill=skill, current_user=current_user)
    return row, skill, owner, permissions


def _validation_checks(
    row: DepartmentAnalysisAgent,
    *,
    skill: Skill | None,
    permissions: dict[str, bool],
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    effective_params = _effective_default_params(row)
    effective_params.update(params)
    dimensions = row.dimensions_json or []
    output_values = {item["value"] for item in ANALYSIS_OUTPUT_SECTIONS}
    output_sections = _string_list(effective_params.get("output_sections"), [])
    invalid_sections = [item for item in output_sections if item not in output_values]
    prompt = _prompt_view(row)
    checks = [
        {
            "key": "bound_skill",
            "label": "绑定 Skill",
            "status": "passed" if row.skill_id and skill else "failed",
            "detail": row.skill_id or "未绑定",
        },
        {
            "key": "prompt_contract",
            "label": "Prompt contract",
            "status": "passed" if prompt["goal"] and prompt["focus"] else "failed",
            "detail": row.prompt_version,
        },
        {
            "key": "dimensions",
            "label": "分析维度",
            "status": "passed" if dimensions else "failed",
            "detail": f"{len(dimensions)} 个维度",
        },
        {
            "key": "output_sections",
            "label": "输出章节",
            "status": "passed" if output_sections and not invalid_sections else "failed",
            "detail": ",".join(output_sections) if output_sections else "未选择",
        },
        {
            "key": "run_control",
            "label": "运行控制",
            "status": "passed" if permissions.get("control_run") else "warning",
            "detail": "可控制运行参数" if permissions.get("control_run") else "当前账号仅可查看",
        },
        {
            "key": "traceability",
            "label": "追溯字段",
            "status": "passed",
            "detail": "run metadata 与 input_snapshot 会记录 Agent 控制信息",
        },
    ]
    if invalid_sections:
        checks.append(
            {
                "key": "invalid_output_sections",
                "label": "非法输出章节",
                "status": "failed",
                "detail": ",".join(invalid_sections),
            }
        )
    return checks


@router.post("/analysis-agents/{agent_id}/validate")
async def validate_analysis_agent(
    agent_id: str,
    body: AnalysisAgentValidateRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    row, skill, owner, permissions = await _load_visible_analysis_agent(db, agent_id, current_user)
    request_params = body.params if isinstance(body.params, dict) else {}
    checks = _validation_checks(row, skill=skill, permissions=permissions, params=request_params)
    return {
        "ok": not any(item["status"] == "failed" for item in checks),
        "agent": _serialize_agent(row, skill=skill, owner=owner, permissions=permissions),
        "checks": checks,
        "sample_payload": _sample_run_payload(row, request_params),
        "capabilities": _capabilities_view(row),
        "prompt": _prompt_view(row),
    }


@router.post("/analysis-agents")
async def upsert_analysis_agent(
    request: Request,
    body: AnalysisAgentUpsertRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    agent_id = _clean_agent_id(body.id, body.name)
    existing = await db.get(DepartmentAnalysisAgent, agent_id)
    existing_skill = await db.get(Skill, existing.skill_id) if existing and existing.skill_id else None
    existing_permissions = (
        await _agent_permissions(db, existing, skill=existing_skill, current_user=current_user)
        if existing
        else {"edit": False}
    )
    can_admin_manage = _can_manage_business_agents(current_user)
    if not can_admin_manage and not (existing and existing_permissions.get("edit")):
        raise AppError("FORBIDDEN", 403, {"detail": "当前账号无权创建或编辑部门分析 Agent"})
    if existing and not can_admin_manage:
        body.department = existing.department
        body.department_id = existing.department_id
        body.owner_user_id = existing.owner_user_id
        body.owner_query = None
        body.skill_id = existing.skill_id
        body.editor_user_ids = list(existing.editor_user_ids_json or [])
        body.editor_queries = []
        body.status = existing.status
    skill = None
    skill_perms: dict[str, bool] = {}
    if body.skill_id:
        skill = await db.get(Skill, body.skill_id)
        if not skill:
            raise AppError("SKILL_NOT_FOUND", 404, {"skill_id": body.skill_id})
        skill_perms = await get_skill_permissions(db, skill, current_user)
        if not existing and not (skill_perms.get("manage_members") or skill_perms.get("edit")):
            raise AppError("FORBIDDEN", 403, {"detail": "当前账号无权绑定该 Skill"})
    dept = await _resolve_agent_department(db, current_user, body, skill)
    if existing and not can_admin_manage:
        owner = await db.get(User, existing.owner_user_id) if existing.owner_user_id else None
        editor_users = list(existing.editor_user_ids_json or [])
    else:
        owner = await _resolve_user(
            db,
            user_id=(body.owner_user_id or "").strip() or None,
            query=(body.owner_query or "").strip() or None,
            department_id=dept.id,
            department_name=dept.name,
        )
        if (body.owner_user_id or body.owner_query) and not owner:
            raise AppError("USER_NOT_FOUND", 404, {"owner_user_id": body.owner_user_id, "owner_query": body.owner_query})
        editor_users: list[str] = []
        for user_id in body.editor_user_ids:
            user = await _resolve_user(db, user_id=user_id, department_id=dept.id, department_name=dept.name)
            if not user:
                raise AppError("USER_NOT_FOUND", 404, {"user_id": user_id})
            editor_users.append(user.id)
        for query in body.editor_queries:
            user = await _resolve_user(db, query=query, department_id=dept.id, department_name=dept.name)
            if not user:
                raise AppError("USER_NOT_FOUND", 404, {"query": query})
            editor_users.append(user.id)
        if owner:
            editor_users.append(owner.id)
        editor_users = sorted(set(editor_users))
    clean_dimensions = [str(item).strip() for item in body.dimensions if str(item).strip()]
    if not clean_dimensions:
        clean_dimensions = list(DEFAULT_ANALYSIS_DIMENSIONS)
    default_params = _normalize_default_params(body.default_params)
    if existing:
        row = existing
        row.name = body.name.strip()
        row.department_id = dept.id
        row.department = dept.name
        row.owner_user_id = owner.id if owner else row.owner_user_id
        row.skill_id = body.skill_id or row.skill_id
        row.prompt_version = body.prompt_version.strip()
        row.description = body.description
        row.dimensions_json = clean_dimensions
        row.default_params_json = default_params
        row.editor_user_ids_json = editor_users
        row.status = body.status.strip() or "active"
        row.is_active = row.status != "archived"
        row.updated_by = current_user.id
        row.updated_at = now_bjt()
    else:
        row = DepartmentAnalysisAgent(
            id=agent_id,
            name=body.name.strip(),
            department_id=dept.id,
            department=dept.name,
            owner_user_id=owner.id if owner else None,
            skill_id=body.skill_id,
            prompt_version=body.prompt_version.strip(),
            description=body.description,
            dimensions_json=clean_dimensions,
            default_params_json=default_params,
            editor_user_ids_json=editor_users,
            status=body.status.strip() or "active",
            is_active=(body.status.strip() or "active") != "archived",
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        db.add(row)
    if body.skill_id and (can_admin_manage or not existing):
        for user_id in editor_users:
            await _ensure_skill_editor(db, body.skill_id, user_id, current_user.id)
    await audit.log(
        current_user.id,
        "analysis_agent.upsert",
        "department_analysis_agent",
        agent_id[:50],
        detail={"department": dept.name, "skill_id": body.skill_id, "editor_count": len(editor_users)},
    )
    await db.flush()
    return {
        "ok": True,
        "agent": _serialize_agent(
            row,
            skill=skill,
            owner=owner,
            permissions=await _agent_permissions(db, row, skill=skill, current_user=current_user),
        ),
    }
