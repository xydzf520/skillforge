"""Workbench review context / task contract service.

从 WorkbenchService 中拆出的任务合同、预演、确认点、草稿锁相关逻辑。
当前阶段先提供可复用函数，WorkbenchService 保留薄委托层以保持 API 兼容。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

import yaml
from sqlalchemy import func, select

from app.common.cache import cache_get, cache_set
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt
from app.auth.models import User
from app.workbench.models import (
    SkillStudioDraft,
    SkillStudioPreview,
    SkillStudioReview,
    SkillStudioRun,
)
from app.workbench.schemas import (
    WorkbenchTaskContractBindResponse,
    WorkbenchTaskContractResponse,
    WorkbenchTaskContractReviewResponse,
)
from app.workbench.task_contract import (
    TASK_CONTRACT_PROMPT_VERSION,
    build_gate_status,
    build_preview,
    build_preview_cache_key,
    checkpoint_list,
    merge_review_state,
    review_expires_at,
)


def _now_bjt() -> datetime:
    return now_bjt()


def _sf():
    from app.database import async_session_factory
    return async_session_factory


CREATION_REQUIRED_FILES = (
    "contract.json",
    "SKILL.md",
    "intent.md",
    "policy.yaml",
    "scripts/main.py",
    "tests/test_main.py",
    "fixtures/sample_input.json",
)

_PLACEHOLDER_TEXTS = {"", "未指定", "待选择", "待填写", "-", "—", "unknown", "none", "null"}


def _clean_creation_text(value: object, *, max_len: int | None = None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in _PLACEHOLDER_TEXTS or text in _PLACEHOLDER_TEXTS:
        return ""
    if max_len is not None:
        return text[:max_len].strip()
    return text


def _parse_skill_md_frontmatter(skill_md: str | None) -> dict[str, object]:
    if not skill_md:
        return {}
    match = re.match(r"^---\r?\n(.*?)\r?\n---", skill_md, re.DOTALL)
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_creation_display_name(contract: dict, files: dict[str, str], skill_id_fallback: str) -> str:
    frontmatter = _parse_skill_md_frontmatter(files.get("SKILL.md"))
    meta = contract.get("meta") if isinstance(contract.get("meta"), dict) else {}
    for candidate in (
        frontmatter.get("name"),
        contract.get("name"),
        meta.get("name"),
    ):
        name = _clean_creation_text(candidate, max_len=80)
        if name:
            return name
    goal_name = _clean_creation_text(contract.get("goal"), max_len=30)
    return goal_name or skill_id_fallback


def _resolve_contract_department(contract: dict, files: dict[str, str]) -> str:
    frontmatter = _parse_skill_md_frontmatter(files.get("SKILL.md"))
    risks = contract.get("risks") if isinstance(contract.get("risks"), dict) else {}
    for candidate in (
        risks.get("department"),
        contract.get("department"),
        frontmatter.get("department"),
    ):
        department = _clean_creation_text(candidate, max_len=50)
        if department:
            return department
    return ""


async def _resolve_tasktree_department_name(session, raw: object) -> str:
    department = _clean_creation_text(raw, max_len=50)
    if not department:
        return ""
    try:
        from app.tasktree.service import (
            _load_org_indexes,
            _normalize_requested_department,
            _resolve_user_lv1,
        )

        lv1_by_id, _lv1_by_name, lv1_names, org_by_name = await _load_org_indexes(session)
        normalized = _resolve_user_lv1(
            department,
            lv1_by_id=lv1_by_id,
            lv1_names=lv1_names,
            org_by_name=org_by_name,
        ) or _normalize_requested_department(
            department,
            lv1_by_id=lv1_by_id,
            lv1_names=lv1_names,
            org_by_name=org_by_name,
        )
        return _clean_creation_text(normalized, max_len=50) or department
    except Exception:
        return department


async def _resolve_user_tasktree_department(session, user_id: str) -> str:
    """Resolve the user's own task-tree department for one-shot Skill publish."""
    from app.org.models import OrgUnit, UserOrgMembership

    rows = (
        await session.execute(
            select(OrgUnit.name, UserOrgMembership.membership_type)
            .join(UserOrgMembership, UserOrgMembership.org_unit_id == OrgUnit.id)
            .where(UserOrgMembership.user_id == user_id)
        )
    ).all()
    primary_candidates = [
        await _resolve_tasktree_department_name(session, name)
        for name, membership_type in rows
        if membership_type == "primary"
    ]
    other_candidates = [
        await _resolve_tasktree_department_name(session, name)
        for name, membership_type in rows
        if membership_type != "primary"
    ]
    for department in [*primary_candidates, *other_candidates]:
        if department:
            return department

    user = await session.get(User, user_id)
    return await _resolve_tasktree_department_name(session, getattr(user, "department", None)) if user else ""


async def _normalize_department_list(session, values: list[object]) -> list[str]:
    seen: set[str] = set()
    departments: list[str] = []
    for raw in values:
        normalized = await _resolve_tasktree_department_name(session, raw)
        if normalized and normalized not in seen:
            seen.add(normalized)
            departments.append(normalized)
    return departments


async def get_creation_context(*, user: User) -> dict:
    """Return the backend-owned defaults for the one-shot Skill creation page."""
    from app.org.models import OrgUnit, UserOrgMembership

    async with _sf()() as session:
        can_view_all = user.role in ("system_admin", "admin") or bool(getattr(user, "can_view_all", False))
        department_source = "none"
        raw_departments: list[object] = []

        if can_view_all:
            rows = (
                await session.execute(
                    select(OrgUnit.name)
                    .where(OrgUnit.type == "department")
                    .order_by(OrgUnit.sort_order.asc(), OrgUnit.name.asc())
                )
            ).scalars().all()
            raw_departments.extend(rows)
            department_source = "org_units" if rows else ("user_profile" if getattr(user, "department", None) else "none")
        else:
            rows = (
                await session.execute(
                    select(OrgUnit.name, UserOrgMembership.membership_type)
                    .join(UserOrgMembership, UserOrgMembership.org_unit_id == OrgUnit.id)
                    .where(UserOrgMembership.user_id == user.id)
                    .order_by(UserOrgMembership.membership_type.desc(), OrgUnit.sort_order.asc(), OrgUnit.name.asc())
                )
            ).all()
            primary = [name for name, membership_type in rows if membership_type == "primary"]
            secondary = [name for name, membership_type in rows if membership_type != "primary"]
            raw_departments.extend([*primary, *secondary])
            if rows:
                department_source = "org_membership"
            elif getattr(user, "department", None):
                department_source = "user_profile"

        if getattr(user, "department", None):
            raw_departments.append(getattr(user, "department"))

        allowed_departments = await _normalize_department_list(session, raw_departments)
        default_department = await _resolve_user_tasktree_department(session, user.id)
        if not default_department and allowed_departments:
            default_department = allowed_departments[0]
        if default_department and default_department not in allowed_departments:
            allowed_departments.insert(0, default_department)

    return {
        "default_department": default_department,
        "allowed_departments": allowed_departments,
        "department_source": department_source,
        "can_choose_department": can_view_all or len(allowed_departments) > 1,
        "naming_policy": {
            "mode": "gen_from_name",
            "description": "留空时后端按 Skill 名称生成拼音 slug，并追加随机 6 位后缀。",
            "editable": True,
            "max_length": 50,
        },
    }


def preview_creation_name(*, name: str, department: str = "") -> dict:
    from app.skills.core.id_gen import gen_from_name

    clean_name = _clean_creation_text(name, max_len=80)
    clean_department = _clean_creation_text(department, max_len=50)
    if not clean_name:
        raise AppError("PARAM_INVALID", 422, {"detail": "name 不能为空"})
    return {
        "name": clean_name,
        "department": clean_department,
        "skill_id": gen_from_name(clean_name, clean_department),
        "naming_policy": "gen_from_name",
    }


async def _ensure_creation_department_allowed(session, *, user_id: str, department: str) -> None:
    user = await session.get(User, user_id)
    if not user:
        raise AppError("AUTH_PERMISSION_DENIED", 403)
    context = await get_creation_context(user=user)
    allowed = set(context.get("allowed_departments") or [])
    if department not in allowed:
        raise AppError(
            "AUTH_PERMISSION_DENIED",
            403,
            {"detail": f"你没有权限向部门 {department!r} 发布 Skill", "allowed_departments": sorted(allowed)},
        )


def _apply_creation_frontmatter_overrides(
    skill_md: str,
    *,
    name: str | None = None,
    department: str | None = None,
    risk_level: str | None = None,
) -> str:
    if not skill_md:
        return skill_md
    match = re.match(r"^---\r?\n(.*?)\r?\n---", skill_md, re.DOTALL)
    if not match:
        return skill_md
    try:
        frontmatter = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return skill_md
    if not isinstance(frontmatter, dict):
        return skill_md

    changed = False
    for key, value in (("name", name), ("department", department), ("risk_level", risk_level)):
        clean = _clean_creation_text(value, max_len=80 if key == "name" else 50)
        if clean and frontmatter.get(key) != clean:
            frontmatter[key] = clean
            changed = True
    if not changed:
        return skill_md

    fm_yaml = yaml.safe_dump(frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return f"---\n{fm_yaml}---{skill_md[match.end():]}"


def _collect_creation_files(draft: SkillStudioDraft) -> dict[str, str]:
    """Return files available for a creation draft, including live scratch files."""
    files: dict[str, str] = {}
    if draft.skill_md:
        files["SKILL.md"] = draft.skill_md
    if draft.intent_md:
        files["intent.md"] = draft.intent_md
    if draft.policy_yaml:
        files["policy.yaml"] = draft.policy_yaml
    if draft.contract_json:
        files["contract.json"] = json.dumps(draft.contract_json, ensure_ascii=False, indent=2)
    for rel, content in (draft.extra_files or {}).items():
        if isinstance(content, str):
            files[rel] = content

    if draft.generation_status in {"pending", "running"}:
        scratch_dir = Path("/tmp/sf_skill_creation") / draft.id
        for rel in CREATION_REQUIRED_FILES:
            if rel in files:
                continue
            path = scratch_dir / rel
            if not path.is_file():
                continue
            try:
                files[rel] = path.read_text(encoding="utf-8")
            except OSError:
                continue
    return files


READY_DRAFT_REOPEN_WINDOW_SECONDS = 90


def _ready_draft_reopen_deadline(draft: SkillStudioDraft) -> datetime:
    base = draft.updated_at or draft.created_at or _now_bjt()
    return base + timedelta(seconds=READY_DRAFT_REOPEN_WINDOW_SECONDS)


async def get_task_preview(cache_key: str, contract: dict, files: dict[str, str] | None = None) -> dict:
    from app.common.contract_schema import build_verified_preview, get_preview_input

    cache_name = f"wb:task-preview:{cache_key}"
    try:
        cached = await cache_get(cache_name)
        if isinstance(cached, dict) and cached.get("rendered_output"):
            if files:
                cached["fixture_used"] = get_preview_input(contract, files)
            cached["cached"] = True
            return cached
    except Exception as e:
        from loguru import logger
        logger.debug("task-preview 缓存读失败 key={}: {}", cache_name, e)
        cached = None

    if files and files.get("scripts/main.py"):
        preview = await build_verified_preview(contract, files, cache_key=cache_key)
        try:
            await cache_set(cache_name, preview, ttl=30 * 60)
        except Exception as e:
            from loguru import logger
            logger.debug("task-preview 缓存写失败(verified) key={}: {}", cache_name, e)
        return preview

    now = _now_bjt()
    async with _sf()() as session:
        preview_row = (
            await session.execute(
                select(SkillStudioPreview).where(
                    SkillStudioPreview.cache_key == cache_key,
                    SkillStudioPreview.expires_at > now,
                )
            )
        ).scalar_one_or_none()
        if preview_row:
            preview = {
                "adapter": preview_row.adapter_name or "",
                "cache_key": preview_row.cache_key,
                "rendered_output": preview_row.rendered_output or "",
                "card_payload": preview_row.card_payload_json or {},
                "fixture_used": get_preview_input(preview_row.contract_json or contract, files),
                "cached": True,
                "generated_at": isoformat_bjt(preview_row.created_at or now),
                "success": True,
            }
            try:
                await cache_set(cache_name, preview, ttl=30 * 60)
            except Exception as e:
                from loguru import logger
                logger.debug("task-preview 缓存写失败(db-hit) key={}: {}", cache_name, e)
            return preview

    preview = build_preview(contract, cache_key=cache_key, cached=False, files=files)
    expires_at = now + timedelta(minutes=30)
    async with _sf()() as session:
        await session.merge(
            SkillStudioPreview(
                cache_key=cache_key,
                contract_json=contract,
                adapter_name=(contract.get("output") or {}).get("adapter"),
                rendered_output=preview.get("rendered_output"),
                card_payload_json=preview.get("card_payload"),
                prompt_version=TASK_CONTRACT_PROMPT_VERSION,
                created_at=now,
                expires_at=expires_at,
            )
        )
        await session.commit()
    try:
        await cache_set(cache_name, preview, ttl=30 * 60)
    except Exception as e:
        from loguru import logger
        logger.debug("task-preview 缓存写失败(final) key={}: {}", cache_name, e)
    return preview


async def check_skill_lock_conflict(
    session,
    *,
    skill_id: str | None,
    branch: str,
    user_id: str,
) -> None:
    if not skill_id:
        return
    now = _now_bjt()
    existing = (
        await session.execute(
            select(SkillStudioDraft).where(
                SkillStudioDraft.skill_id == skill_id,
                SkillStudioDraft.branch == branch,
                SkillStudioDraft.locked_until > now,
                SkillStudioDraft.user_id != user_id,
            )
        )
    ).scalars().first()
    if existing:
        remaining_seconds = int((existing.locked_until - now).total_seconds())
        raise AppError(
            "SKILL_DRAFT_LOCKED",
            423,
            {
                "skill_id": skill_id,
                "branch": branch,
                "locked_by": existing.user_id,
                "draft_id": existing.id,
                "remaining_minutes": max(1, remaining_seconds // 60),
                "actions": ["fork_personal_branch", "force_takeover_admin"],
            },
        )


async def fork_personal_branch(skill_id: str, user_id: str) -> dict:
    now = _now_bjt()
    new_branch = f"personal/{user_id}"
    new_draft_id = f"draft-{uuid4().hex[:12]}"
    async with _sf()() as session:
        source = (
            await session.execute(
                select(SkillStudioDraft).where(
                    SkillStudioDraft.skill_id == skill_id,
                    SkillStudioDraft.branch == "main",
                ).order_by(SkillStudioDraft.updated_at.desc())
            )
        ).scalars().first()
        if not source:
            raise AppError("TASK_CONTRACT_DRAFT_NOT_FOUND", 404, {"skill_id": skill_id})
        session.add(
            SkillStudioDraft(
                id=new_draft_id,
                skill_id=skill_id,
                branch=new_branch,
                user_id=user_id,
                source_message=source.source_message,
                intent_md=source.intent_md,
                skill_md=source.skill_md,
                policy_yaml=source.policy_yaml,
                contract_json=source.contract_json,
                review_state_json=source.review_state_json,
                locked_until=review_expires_at(),
                lock_state={"forked_from": source.id, "forked_at": isoformat_bjt(now)},
                confirmation_count=source.confirmation_count or 0,
                trust_mode=source.trust_mode or False,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    return {"draft_id": new_draft_id, "branch": new_branch, "forked_from": source.id}


async def force_takeover_draft(
    draft_id: str,
    *,
    admin_user_id: str,
    admin_role: str,
) -> dict:
    if admin_role != "admin":
        raise AppError("AUTH_PERMISSION_DENIED", 403, {"required_role": "admin"})
    now = _now_bjt()
    async with _sf()() as session:
        draft = (await session.execute(select(SkillStudioDraft).where(SkillStudioDraft.id == draft_id))).scalar_one_or_none()
        if not draft:
            raise AppError("TASK_CONTRACT_DRAFT_NOT_FOUND", 404)
        previous_owner = draft.user_id
        draft.user_id = admin_user_id
        draft.locked_until = now
        existing_state = dict(draft.lock_state or {})
        existing_state.update({
            "force_takeover_at": isoformat_bjt(now),
            "previous_owner": previous_owner,
            "taken_over_by": admin_user_id,
        })
        draft.lock_state = existing_state
        draft.updated_at = now
        await session.commit()
    return {
        "draft_id": draft_id,
        "previous_owner": previous_owner,
        "new_owner": admin_user_id,
        "taken_over_at": isoformat_bjt(now),
    }


async def count_user_confirmations(user_id: str) -> int:
    async with _sf()() as session:
        count = (
            await session.execute(
                select(func.count(SkillStudioDraft.id)).where(
                    SkillStudioDraft.user_id == user_id,
                    SkillStudioDraft.skill_id.isnot(None),
                )
            )
        ).scalar_one()
    return int(count or 0)


def derive_fatigue_state(confirmation_count: int) -> dict:
    if confirmation_count >= 5:
        return {
            "level": "trust",
            "auto_approve": ["target", "permission", "responsibility"],
            "manual_only": ["preview"],
            "hint": f"信任模式：你已成功发布过 {confirmation_count} 个 Skill，仅保留预演确认。",
        }
    if confirmation_count >= 1:
        return {
            "level": "lite",
            "auto_approve": ["target", "responsibility"],
            "manual_only": ["permission", "preview"],
            "hint": "轻量模式：仅保留权限授予和预演确认。",
        }
    return {
        "level": "initial",
        "auto_approve": [],
        "manual_only": ["target", "permission", "preview", "responsibility"],
        "hint": "首次创建：4 张必感知点全部需要确认。",
    }


def apply_fatigue_to_review_state(review_state: dict, fatigue: dict) -> dict:
    for key in fatigue.get("auto_approve", []):
        item = review_state.get(key)
        if not item:
            continue
        item["approved"] = True
        item["decision"] = "auto_approved"
        detail = dict(item.get("detail") or {})
        detail["fatigue_auto"] = fatigue.get("level")
        item["detail"] = detail
    return review_state


async def generate_task_contract(
    *,
    message: str,
    user_id: str,
    invoke_agent_core_save: Callable[[str], Awaitable[dict]],
) -> WorkbenchTaskContractResponse:
    bundle = await invoke_agent_core_save(message)
    preview = await get_task_preview(bundle["cache_key"], bundle["contract"])
    review_state = merge_review_state(bundle["contract"], bundle["review_state"])
    gate = build_gate_status(bundle["contract"], review_state, preview)

    user_confirmations = await count_user_confirmations(user_id)
    fatigue = derive_fatigue_state(user_confirmations)
    review_state = apply_fatigue_to_review_state(review_state, fatigue)
    gate = build_gate_status(bundle["contract"], review_state, preview)

    draft_id = f"draft-{uuid4().hex[:12]}"
    now = _now_bjt()
    async with _sf()() as session:
        session.add(
            SkillStudioDraft(
                id=draft_id,
                skill_id=None,
                branch="main",
                user_id=user_id,
                source_message=message,
                intent_md=bundle["intent_md"],
                skill_md=None,
                policy_yaml=bundle["policy_yaml"],
                contract_json=bundle["contract"],
                review_state_json=review_state,
                locked_until=review_expires_at(),
                confirmation_count=user_confirmations,
                trust_mode=fatigue["level"] == "trust",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            SkillStudioRun(
                id=f"run-{uuid4().hex[:12]}",
                skill_id=None,
                draft_id=draft_id,
                thread_id=None,
                mode="save",
                contract_json=bundle["contract"],
                status="completed",
                preview_cache_key=bundle["cache_key"],
                started_at=now,
                completed_at=now,
                cost_usd=0,
            )
        )
        await session.commit()

    return WorkbenchTaskContractResponse(
        draft_id=draft_id,
        intent_md=bundle["intent_md"],
        policy_yaml=bundle["policy_yaml"],
        contract=bundle["contract"],
        preview=preview,
        checkpoints=checkpoint_list(review_state),
        gate=gate,
        skill=bundle.get("skill_struct") or bundle["skill"],
    )


async def review_task_contract(
    *,
    draft_id: str,
    checkpoint: str,
    decision: str,
    detail: dict,
    user_id: str,
) -> WorkbenchTaskContractReviewResponse:
    async with _sf()() as session:
        draft = (await session.execute(select(SkillStudioDraft).where(SkillStudioDraft.id == draft_id))).scalar_one_or_none()
        if not draft:
            raise AppError("TASK_CONTRACT_DRAFT_NOT_FOUND", 404)
        if draft.user_id != user_id:
            raise AppError("AUTH_PERMISSION_DENIED", 403)

        contract = draft.contract_json or {}
        review_state = merge_review_state(contract, draft.review_state_json or {})
        item = review_state.get(checkpoint)
        if not item:
            raise AppError("TASK_CONTRACT_CHECKPOINT_INVALID", 400)

        item["decision"] = decision
        item["approved"] = decision == "approved"
        if detail:
            merged_detail = dict(item.get("detail") or {})
            merged_detail.update(detail)
            item["detail"] = merged_detail

        draft.review_state_json = review_state
        draft.updated_at = _now_bjt()
        session.add(
            SkillStudioReview(
                skill_id=draft.skill_id,
                draft_id=draft.id,
                user_id=user_id,
                checkpoint=checkpoint,
                decision=decision,
                detail_json=detail or {},
                created_at=_now_bjt(),
            )
        )
        await session.commit()

    preview = await get_task_preview(build_preview_cache_key(contract), contract)
    gate = build_gate_status(contract, review_state, preview)
    return WorkbenchTaskContractReviewResponse(
        draft_id=draft_id,
        checkpoints=checkpoint_list(review_state),
        gate=gate,
    )


async def start_skill_creation_task(*, message: str, user_id: str) -> str:
    from app.coding_agent.creation_task_manager import (
        creation_task_manager,
        CreationQueueFullError,
    )

    draft_id = f"draft-{uuid4().hex[:12]}"
    now = _now_bjt()
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
                generation_status="pending",
                error_detail=None,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    try:
        creation_task_manager.start(draft_id, message, user_id)
    except CreationQueueFullError as exc:
        # 队列已满,回滚刚建的 draft 行
        async with _sf()() as session:
            stale = await session.get(SkillStudioDraft, draft_id)
            if stale:
                await session.delete(stale)
                await session.commit()
        raise AppError(
            "CREATION_QUEUE_FULL", 429,
            detail={
                "pending": exc.pending,
                "limit": exc.limit,
                "retry_after": 30,
            },
        ) from exc
    return draft_id


async def get_my_active_draft(*, user_id: str) -> dict | None:
    from app.coding_agent.creation_task_manager import creation_task_manager

    now = _now_bjt()
    async with _sf()() as session:
        stale_ready = (
            await session.execute(
                select(SkillStudioDraft).where(
                    SkillStudioDraft.user_id == user_id,
                    SkillStudioDraft.skill_id.is_(None),
                    SkillStudioDraft.generation_status == "ready",
                    SkillStudioDraft.updated_at < now - timedelta(seconds=READY_DRAFT_REOPEN_WINDOW_SECONDS),
                )
            )
        ).scalars().all()
        if stale_ready:
            for row in stale_ready:
                row.generation_status = "cancelled"
                row.updated_at = now
            await session.commit()

        draft = (
            await session.execute(
                select(SkillStudioDraft)
                .where(
                    SkillStudioDraft.user_id == user_id,
                    SkillStudioDraft.skill_id.is_(None),
                    SkillStudioDraft.generation_status.in_(["pending", "running", "ready"]),
                )
                .order_by(SkillStudioDraft.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if not draft:
            return None

        result = {
            "draft_id": draft.id,
            "generation_status": draft.generation_status,
            "contract": draft.contract_json,
            "source_message": draft.source_message or "",
            "source_excerpt": (draft.source_message or "")[:100],
            "created_at": isoformat_bjt(draft.created_at),
            "reopen_expires_at": (
                isoformat_bjt(_ready_draft_reopen_deadline(draft))
                if draft.generation_status == "ready"
                else None
            ),
            "task_alive": creation_task_manager.is_running(draft.id),
            "files": _collect_creation_files(draft),
        }

        if draft.generation_status == "ready" and draft.contract_json:
            contract = draft.contract_json
            review_state = merge_review_state(contract, draft.review_state_json or {})
            preview_files = _collect_creation_files(draft)
            preview = await get_task_preview(build_preview_cache_key(contract, preview_files), contract, preview_files)
            gate = build_gate_status(contract, review_state, preview)
            result["checkpoints"] = checkpoint_list(review_state)
            result["gate"] = gate
            result["preview"] = preview

        progress = draft.error_detail or {}
        result["milestones"] = progress.get("milestones") or []
        return result


async def dismiss_draft(*, draft_id: str, user_id: str) -> dict:
    async with _sf()() as session:
        draft = (await session.execute(select(SkillStudioDraft).where(SkillStudioDraft.id == draft_id))).scalar_one_or_none()
        if not draft:
            raise AppError("TASK_CONTRACT_DRAFT_NOT_FOUND", 404)
        if draft.user_id != user_id:
            raise AppError("AUTH_PERMISSION_DENIED", 403)
        draft.generation_status = "cancelled"
        draft.updated_at = _now_bjt()
        await session.commit()
    return {"draft_id": draft_id, "dismissed": True}


async def finalize_skill_creation(*, draft_id: str, user_id: str, override: dict | None = None) -> dict:
    from sqlalchemy.exc import IntegrityError
    from app.coding_agent.skill_creation_runner import REQUIRED_FILES
    from app.skills.core.models import Skill as SkillModel
    from app.skills.lifecycle.service import create_skill_from_files

    async with _sf()() as session:
        draft = (
            await session.execute(
                select(SkillStudioDraft)
                .where(SkillStudioDraft.id == draft_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not draft:
            raise AppError("TASK_CONTRACT_DRAFT_NOT_FOUND", 404)
        if draft.user_id != user_id:
            raise AppError("AUTH_PERMISSION_DENIED", 403)
        if draft.generation_status != "ready":
            raise AppError("PARAM_INVALID", 400, {"detail": f"draft 尚未生成完成，当前状态: {draft.generation_status}"})
        if draft.skill_id:
            return {
                "draft_id": draft_id,
                "skill_id": draft.skill_id,
                "git_commit": None,
                "quality_score": None,
                "already_finalized": True,
            }

        contract = draft.contract_json or {}
        review_state = merge_review_state(contract, draft.review_state_json or {})
        preview_files = {}
        for rel, content in (draft.extra_files or {}).items():
            preview_files[rel] = content
        preview = await get_task_preview(build_preview_cache_key(contract, preview_files), contract, preview_files)
        gate = build_gate_status(contract, review_state, preview)

        # 契约一致闸门：如果专门挂在 contract_consistency 上，抛专用错误码，
        # 让前端能弹"契约偏离"对话框并给出 schema_errors 细节。
        from app.common.contract_schema import get_output_schema, lint_output_schema

        cc_item = next((i for i in gate.get("items", []) if i.get("key") == "contract_consistency"), None)
        if cc_item and not cc_item.get("passed"):
            output_schema = get_output_schema(contract)
            schema_errors = lint_output_schema(output_schema) if output_schema else ["output_schema 缺失"]
            raise AppError("CONTRACT_DRIFT", 400, {
                "detail": "契约偏离：output_schema 不合法，无法发布",
                "schema_errors": schema_errors[:10],
                "gate": gate,
            })

        preview_item = next((i for i in gate.get("items", []) if i.get("key") == "sandbox_preview_passed"), None)
        if preview_item and not preview_item.get("passed"):
            raise AppError("CONTRACT_DRIFT", 400, {
                "detail": "契约偏离：真实预演未通过，无法发布",
                "schema_errors": (preview.get("schema_errors") or [preview.get("sandbox_summary") or "真实预演失败"])[:10],
                "gate": gate,
            })

        if not gate.get("can_publish"):
            raise AppError("PARAM_INVALID", 400, {"detail": "尚未通过 4 必感知点确认", "gate": gate})

        files: dict[str, str] = {}
        if draft.skill_md:
            files["SKILL.md"] = draft.skill_md
        if draft.intent_md:
            files["intent.md"] = draft.intent_md
        if draft.policy_yaml:
            files["policy.yaml"] = draft.policy_yaml
        if draft.contract_json:
            files["contract.json"] = json.dumps(draft.contract_json, ensure_ascii=False, indent=2)
        for rel, content in (draft.extra_files or {}).items():
            files[rel] = content

        from app.common.contract_schema import (
            materialize_skill_bundle,
            normalize_skill_bundle_files,
        )
        from app.coding_agent.skill_creation_runner import verify_schema_phase
        from pathlib import Path
        from tempfile import TemporaryDirectory

        files = normalize_skill_bundle_files(files, contract)

        missing = [f for f in REQUIRED_FILES if f not in files or not files[f].strip()]
        if missing:
            raise AppError("PARAM_INVALID", 400, {"detail": f"草稿缺少必需文件: {missing}"})

        # 发布前复验: 复用生成阶段同一套 verify_schema_phase。
        # 这样可以挡住 "ready 之后又漂了" 的窗口，而不是只 lint output_schema 结构。
        with TemporaryDirectory(prefix="sf_finalize_verify_") as td:
            verify_dir = Path(td)
            materialize_skill_bundle(verify_dir, files)
            schema_errors = await verify_schema_phase(verify_dir, contract)
        if schema_errors:
            raise AppError("CONTRACT_DRIFT", 400, {
                "detail": "契约偏离：发布前复验失败，无法发布",
                "schema_errors": schema_errors[:10],
                "gate": gate,
            })

        override = override or {}
        override_name = _clean_creation_text(override.get("name"), max_len=80)
        override_department = _clean_creation_text(override.get("department"), max_len=50)
        override_skill_id = _clean_creation_text(override.get("skill_id"), max_len=50)
        override_risk_level = _clean_creation_text(override.get("risk_level"), max_len=2)
        if override_risk_level and override_risk_level not in {"R1", "R2", "R3", "R4"}:
            raise AppError("PARAM_INVALID", 422, {"detail": f"风险等级必须是 R1-R4 之一，当前：{override_risk_level!r}"})

        goal = _clean_creation_text(contract.get("goal"), max_len=80) or "新建 skill"
        display_name = _resolve_creation_display_name(contract, files, goal)
        if override_name:
            display_name = override_name

        if override_skill_id:
            from app.skills.core.id_gen import validate_skill_id
            skill_id = validate_skill_id(override_skill_id)
            exists = await session.execute(select(SkillModel).where(SkillModel.id == skill_id))
            if exists.scalar_one_or_none():
                raise AppError("SKILL_ALREADY_EXISTS", 409, {"skill_id": skill_id})
        else:
            from app.skills.core.id_gen import gen_from_name
            skill_id = None
            for _ in range(5):
                candidate = gen_from_name(display_name)
                exists = await session.execute(select(SkillModel).where(SkillModel.id == candidate))
                if not exists.scalar_one_or_none():
                    skill_id = candidate
                    break
            if skill_id is None:
                skill_id = f"skill-{uuid4().hex[:8]}"

        risks = contract.get("risks") if isinstance(contract.get("risks"), dict) else {}
        trigger = contract.get("trigger") if isinstance(contract.get("trigger"), dict) else {}
        user_department = await _resolve_user_tasktree_department(session, user_id)
        creation_department = override_department or user_department or _resolve_contract_department(contract, files)
        if not creation_department:
            raise AppError(
                "PARAM_INVALID",
                422,
                {"detail": "无法确定 Skill 部门：请先在任务树/组织架构中给当前用户配置部门"},
            )
        if override_department:
            await _ensure_creation_department_allowed(session, user_id=user_id, department=creation_department)
        risk_level = override_risk_level or _clean_creation_text(risks.get("level"), max_len=2) or "R2"
        files["SKILL.md"] = _apply_creation_frontmatter_overrides(
            files.get("SKILL.md", ""),
            name=display_name,
            department=creation_department,
            risk_level=risk_level,
        )

        try:
            result = await create_skill_from_files(
                session,
                skill_id=skill_id,
                name=display_name,
                department=creation_department,
                files=files,
                user_id=user_id,
                trigger_type=trigger.get("type") or "manual",
                trigger_expression=trigger.get("expression") or "",
                risk_level=risk_level,
            )
        except IntegrityError:
            await session.rollback()
            skill_id = f"skill-{uuid4().hex[:8]}"
            result = await create_skill_from_files(
                session,
                skill_id=skill_id,
                name=display_name,
                department=creation_department,
                files=files,
                user_id=user_id,
                trigger_type=trigger.get("type") or "manual",
                trigger_expression=trigger.get("expression") or "",
                risk_level=risk_level,
            )
            draft = (await session.execute(select(SkillStudioDraft).where(SkillStudioDraft.id == draft_id))).scalar_one()

        draft.skill_id = result["skill_id"]
        draft.generation_status = "finalized"
        draft.confirmation_count = (draft.confirmation_count or 0) + 1
        draft.updated_at = _now_bjt()
        await session.commit()

    from app.common.audit import audit
    await audit.log(user_id, "workbench.finalize_skill_creation", "skill", result["skill_id"], {"draft_id": draft_id, "via": "aiclaw_one_shot"})
    return {
        "draft_id": draft_id,
        "skill_id": result["skill_id"],
        "git_commit": result.get("git_commit"),
        "quality_score": result.get("quality_score"),
    }


async def bind_task_contract(*, draft_id: str, skill_id: str, user_id: str) -> WorkbenchTaskContractBindResponse:
    async with _sf()() as session:
        draft = (await session.execute(select(SkillStudioDraft).where(SkillStudioDraft.id == draft_id))).scalar_one_or_none()
        if not draft:
            raise AppError("TASK_CONTRACT_DRAFT_NOT_FOUND", 404)
        if draft.user_id != user_id:
            raise AppError("AUTH_PERMISSION_DENIED", 403)

        await check_skill_lock_conflict(session, skill_id=skill_id, branch=draft.branch or "main", user_id=user_id)

        draft.skill_id = skill_id
        draft.confirmation_count = (draft.confirmation_count or 0) + 1
        draft.updated_at = _now_bjt()

        preview_rows = (await session.execute(select(SkillStudioPreview).where(SkillStudioPreview.draft_id == draft_id))).scalars().all()
        for row in preview_rows:
            row.skill_id = skill_id

        review_rows = (await session.execute(select(SkillStudioReview).where(SkillStudioReview.draft_id == draft_id))).scalars().all()
        for row in review_rows:
            row.skill_id = skill_id

        run_rows = (await session.execute(select(SkillStudioRun).where(SkillStudioRun.draft_id == draft_id))).scalars().all()
        for row in run_rows:
            row.skill_id = skill_id

        await session.commit()

    return WorkbenchTaskContractBindResponse(draft_id=draft_id, skill_id=skill_id, bound=True)
