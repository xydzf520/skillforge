"""Workbench session orchestrator.

从 WorkbenchService 中抽离 session 生命周期与引用检索逻辑。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update

from app.common.time_utils import now_bjt
from app.common.exceptions import AppError
from app.playbooks import service as playbook_service
from app.skills.core.models import Skill
from app.workbench.context_builder import context_builder
from app.workbench.intent_service import detect_intent
from app.workbench.models import SkillWorkbenchSession
from app.workbench.schemas import (
    ReferenceItem,
    WorkbenchIntentResponse,
    WorkbenchMode,
    WorkbenchReferenceListResponse,
    WorkbenchSessionResponse,
)


def _now_bjt() -> datetime:
    return now_bjt()


def _sf():
    from app.database import async_session_factory
    return async_session_factory


async def create_session(
    *,
    skill_id: str,
    user_id: str,
    mode: WorkbenchMode = "novice",
    active_module: str | None = None,
) -> WorkbenchSessionResponse:
    structure = await asyncio.to_thread(context_builder.load_skill_structure, skill_id)
    now = _now_bjt()
    session_id = f"wb-{uuid4().hex[:12]}"

    async with _sf()() as session:
        session.add(
            SkillWorkbenchSession(
                id=session_id,
                skill_id=skill_id,
                user_id=user_id,
                mode=mode,
                current_module=active_module,
                status="active",
                context_snapshot=structure.model_dump(),
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    return WorkbenchSessionResponse(
        session_id=session_id,
        skill_id=skill_id,
        mode=mode,
        active_module=active_module,
        current_module=active_module,
        skill=structure,
        modules=context_builder.summarize_modules(structure),
        context={"skill": structure.model_dump()},
        created_at=now,
        updated_at=now,
        persistence="database",
    )


async def get_session(*, skill_id: str, session_id: str, user_id: str) -> WorkbenchSessionResponse:
    async with _sf()() as session:
        result = await session.execute(select(SkillWorkbenchSession).where(SkillWorkbenchSession.id == session_id))
        wb_session = result.scalar_one_or_none()
        if not wb_session or wb_session.skill_id != skill_id:
            raise AppError("WORKBENCH_SESSION_NOT_FOUND", 404)
        if wb_session.user_id != user_id:
            raise AppError("AUTH_PERMISSION_DENIED", 403)

        structure = await asyncio.to_thread(context_builder.load_skill_structure, skill_id)
        return WorkbenchSessionResponse(
            session_id=wb_session.id,
            skill_id=wb_session.skill_id,
            mode=wb_session.mode,
            active_module=wb_session.current_module,
            current_module=wb_session.current_module,
            skill=structure,
            modules=context_builder.summarize_modules(structure),
            context={"skill": structure.model_dump()},
            created_at=wb_session.created_at,
            updated_at=wb_session.updated_at,
            persistence="database",
        )


async def analyze_intent(
    *,
    skill_id: str,
    session_id: str,
    user_id: str,
    message: str,
    active_module: str | None = None,
) -> WorkbenchIntentResponse:
    async with _sf()() as session:
        result = await session.execute(select(SkillWorkbenchSession).where(SkillWorkbenchSession.id == session_id))
        wb_session = result.scalar_one_or_none()
        if not wb_session or wb_session.skill_id != skill_id:
            raise AppError("WORKBENCH_SESSION_NOT_FOUND", 404)
        if wb_session.user_id != user_id:
            raise AppError("AUTH_PERMISSION_DENIED", 403)

        detected = detect_intent(message, active_module=active_module or wb_session.current_module)
        wb_session.current_module = detected["target_module"]
        wb_session.updated_at = _now_bjt()
        await session.commit()

        return WorkbenchIntentResponse(
            session_id=session_id,
            skill_id=skill_id,
            message=message,
            **detected,
        )


async def list_references(
    *,
    skill_id: str | None = None,
    query: str = "",
    source_type: str = "all",
    module: str = "all",
    reference_mode: str = "all",
    user_department: str | None = None,
) -> WorkbenchReferenceListResponse:
    items: list[ReferenceItem] = []
    search = query.strip().lower()

    async with _sf()() as session:
        result = await session.execute(select(Skill).order_by(Skill.updated_at.desc()).limit(30))
        skills = result.scalars().all()
    for skill in skills:
        if skill_id and skill.id == skill_id:
            continue
        if user_department and skill.department != user_department:
            continue
        for module_name, title in [
            ("goal", "目标"),
            ("rules", "规则"),
            ("params", "参数"),
            ("output_table", "输出表格"),
            ("test_cases", "测试样例"),
        ]:
            items.append(
                ReferenceItem(
                    id=f"skill:{skill.id}:{module_name}",
                    title=f"{skill.name} / {title}",
                    summary=skill.description or "",
                    source_type="skill_module",
                    source_module=module_name,
                    source_id=skill.id,
                    reference_mode="copy_structure",
                    tags=[tag for tag in [skill.department, skill.status] if tag],
                )
            )

    for playbook in await playbook_service.list_playbooks():
        items.append(
            ReferenceItem(
                id=f"playbook:{playbook['file_name']}:workflow",
                title=f"{playbook['name']} / 工作流",
                summary=playbook.get("description", ""),
                source_type="workflow_template",
                source_module="workflow",
                source_id=playbook["file_name"],
                reference_mode="copy_logic",
                tags=[tag for tag in [playbook.get("department")] if tag],
            )
        )

    filtered = []
    for item in items:
        if source_type != "all" and item.source_type != source_type:
            continue
        if module != "all" and item.source_module != module:
            continue
        if reference_mode != "all" and item.reference_mode != reference_mode:
            continue
        haystack = f"{item.title} {item.summary} {item.source_type} {item.source_module} {item.source_id} {' '.join(item.tags)}".lower()
        if search and search not in haystack:
            continue
        filtered.append(item)

    return WorkbenchReferenceListResponse(items=filtered)


async def cleanup_expired_sessions(max_age_hours: int = 72) -> int:
    cutoff = _now_bjt() - timedelta(hours=max_age_hours)
    async with _sf()() as session:
        result = await session.execute(
            update(SkillWorkbenchSession)
            .where(SkillWorkbenchSession.status == "active")
            .where(SkillWorkbenchSession.updated_at < cutoff)
            .values(status="expired")
        )
        await session.commit()
        return result.rowcount
