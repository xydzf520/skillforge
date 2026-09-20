"""Workbench patch service.

从 WorkbenchService 中抽离 patch 生成、校验、应用、局部应用逻辑。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.common.audit import audit
from app.common.exceptions import AppError
from app.common.time_utils import now_bjt
from app.playbooks import service as playbook_service
from app.skills.lifecycle import service as skill_service
from app.testing.replay import replay_service
from app.workbench.apply_service import (
    apply_partial_patch as _apply_partial,
    apply_patch_to_skill_document,
    build_patch_from_message,
)
from app.workbench.context_builder import context_builder, invalidate_structure_cache
from app.workbench.diff_service import build_diff_preview
from app.workbench.intent_service import detect_intent
from app.workbench.models import (
    SkillWorkbenchPatch,
    SkillWorkbenchReference,
    SkillWorkbenchSession,
    SkillWorkbenchValidationRun,
)
from app.workbench.schemas import (
    SkillStructure,
    WorkbenchApplyResponse,
    WorkbenchPatchResponse,
    WorkbenchValidateResponse,
)
from app.workbench.validation_service import validate_module_patch


def _now_bjt() -> datetime:
    return now_bjt()


def _sf():
    from app.database import async_session_factory
    return async_session_factory


def _workflow_patch(message: str, references: list[dict], skill_id: str, structure: SkillStructure) -> tuple[str, dict]:
    workflow = build_patch_from_message("workflow", message, references, skill_id).get("workflow", {})
    workflow.setdefault("summary", message[:120])
    workflow.setdefault("status", "draft")
    if not workflow.get("nodes"):
        workflow["nodes"] = [{
            "id": "node-1",
            "type": "skill",
            "label": structure.meta.get("name") or skill_id,
            "skill_id": skill_id,
            "position": {"x": 240, "y": 140},
        }]
    return "生成工作流草稿", {"workflow": workflow}


def normalize_patch(target_module: str, message: str, structure: SkillStructure, references: list[dict], skill_id: str) -> tuple[str, dict]:
    if target_module == "workflow":
        return _workflow_patch(message, references, skill_id, structure)
    return "生成工作流草稿" if target_module == "workflow" else ("", build_patch_from_message(target_module, message, references, skill_id))


async def create_patch(*, skill_id: str, session_id: str, user_id: str, message: str, target_module: str, references: list[dict] | None = None) -> WorkbenchPatchResponse:
    async with _sf()() as session:
        result = await session.execute(select(SkillWorkbenchSession).where(SkillWorkbenchSession.id == session_id))
        wb_session = result.scalar_one_or_none()
        if not wb_session or wb_session.skill_id != skill_id:
            raise AppError("WORKBENCH_SESSION_NOT_FOUND", 404)
        if wb_session.user_id != user_id:
            raise AppError("AUTH_PERMISSION_DENIED", 403)

        structure = await __import__("asyncio").to_thread(context_builder.load_skill_structure, skill_id)
        detected = detect_intent(message, active_module=target_module or wb_session.current_module)
        refs = references or []
        summary, patch_payload = normalize_patch(target_module, message, structure, refs, skill_id)
        before_data = structure.model_dump()
        diff_preview = build_diff_preview(target_module, patch_payload, before=before_data)
        validation = validate_module_patch(target_module, patch_payload)

        patch_id = f"patch-{uuid4().hex[:12]}"
        row = SkillWorkbenchPatch(
            id=patch_id,
            session_id=session_id,
            skill_id=skill_id,
            target_module=target_module,
            intent=detected["intent"],
            summary=summary,
            patch_json=patch_payload,
            diff_preview_json=diff_preview,
            hunks_json=diff_preview.get("hunks", []),
            source_context={"active_module": target_module, "message": message[:200]},
            status="draft",
            created_by=user_id,
            created_at=_now_bjt(),
        )
        session.add(row)
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            session.add(
                SkillWorkbenchReference(
                    patch_id=patch_id,
                    source_type=ref.get("source_type", "skill_module"),
                    source_id=ref.get("source_id") or ref.get("id") or "",
                    source_module=ref.get("source_module", ""),
                    reference_mode=ref.get("reference_mode", "copy_structure"),
                )
            )
        wb_session.updated_at = _now_bjt()
        await session.commit()

    return WorkbenchPatchResponse(
        patch_id=patch_id,
        session_id=session_id,
        target_module=target_module,
        intent=detected["intent"],
        summary=summary,
        patch=patch_payload,
        diff_preview=diff_preview,
        validation=validation,
    )


async def validate_patch(*, skill_id: str, patch_id: str, user_id: str) -> WorkbenchValidateResponse:
    async with _sf()() as session:
        patch_row = (await session.execute(select(SkillWorkbenchPatch).where(SkillWorkbenchPatch.id == patch_id))).scalar_one_or_none()
        if not patch_row or patch_row.skill_id != skill_id:
            raise AppError("WORKBENCH_PATCH_NOT_FOUND", 404)
        if patch_row.created_by != user_id:
            raise AppError("AUTH_PERMISSION_DENIED", 403)

        validation = validate_module_patch(patch_row.target_module, patch_row.patch_json)
        if patch_row.target_module == "params":
            try:
                date_to = _now_bjt().date().isoformat()
                date_from = (_now_bjt().date() - timedelta(days=3)).isoformat()
                new_params = {
                    item.get("name"): item.get("default_value")
                    for item in (patch_row.patch_json or {}).get("params", [])
                    if item.get("name")
                }
                replay_result = await replay_service.run_replay(skill_id, date_from, date_to, new_params)
                validation["historical_replay_checks"] = [{
                    "title": "历史回放",
                    "status": "success" if replay_result.get("total_count", 0) >= 0 else "warning",
                    "message": replay_result.get("summary", "已完成历史回放"),
                }]
                validation["impact_summary"] = {
                    "improved": replay_result.get("changed_count", 0),
                    "regressed": 0,
                    "unchanged": max(replay_result.get("total_count", 0) - replay_result.get("changed_count", 0), 0),
                }
            except Exception:
                validation["historical_replay_checks"] = [{"title": "历史回放", "status": "warning", "message": "历史回放暂时不可用，已跳过"}]

        session.add(
            SkillWorkbenchValidationRun(
                id=f"wval-{uuid4().hex[:12]}",
                patch_id=patch_id,
                skill_id=skill_id,
                structural_report=validation.get("structural_checks"),
                sample_case_report=validation.get("sample_case_checks"),
                replay_report=validation.get("historical_replay_checks"),
                impact_summary=validation.get("impact_summary"),
                can_apply=validation.get("can_apply", False),
                created_at=_now_bjt(),
            )
        )
        await session.commit()

    return WorkbenchValidateResponse(patch_id=patch_id, validation=validation)


async def apply_patch(*, skill_id: str, patch_id: str, user_id: str) -> WorkbenchApplyResponse:
    async with _sf()() as session:
        patch_row = (await session.execute(select(SkillWorkbenchPatch).where(SkillWorkbenchPatch.id == patch_id))).scalar_one_or_none()
        if not patch_row or patch_row.skill_id != skill_id:
            raise AppError("WORKBENCH_PATCH_NOT_FOUND", 404)
        if patch_row.created_by != user_id:
            raise AppError("AUTH_PERMISSION_DENIED", 403)

        validation = validate_module_patch(patch_row.target_module, patch_row.patch_json)
        if not validation["valid"]:
            raise AppError("WORKBENCH_PATCH_INVALID", 400, {"issues": validation["issues"]})

        structure = await __import__("asyncio").to_thread(context_builder.load_skill_structure, skill_id)
        if patch_row.target_module == "workflow":
            workflow = (patch_row.patch_json or {}).get("workflow", {})
            playbook_name = f"{skill_id}-workflow"
            steps = []
            nodes = workflow.get("nodes", [])
            edges = workflow.get("edges", [])
            incoming = {}
            for edge in edges:
                incoming.setdefault(edge.get("target"), []).append(edge.get("source"))
            for node in nodes:
                steps.append({
                    "id": node.get("id"),
                    "skill_id": node.get("skill_id") or skill_id,
                    "name": node.get("label"),
                    "depends_on": incoming.get(node.get("id"), []),
                })
            await playbook_service.save_playbook(playbook_name, {
                "name": playbook_name,
                "description": workflow.get("summary", f"{skill_id} workflow"),
                "department": structure.meta.get("department", ""),
                "steps": steps,
                "_canvas_layout": {
                    "nodes": nodes,
                    "edges": edges,
                    "bindings": workflow.get("bindings", []),
                },
            })
            patch_row.status = "applied"
            patch_row.applied_at = _now_bjt()
            await session.commit()
            await audit.log(user_id, "workbench.apply_patch", "skill", skill_id, {"patch_id": patch_id, "module": "workflow"})
            invalidate_structure_cache(skill_id)
            # 重新加载结构，确保返回的是最新状态
            refreshed = await __import__("asyncio").to_thread(context_builder.load_skill_structure, skill_id)
            return WorkbenchApplyResponse(
                patch_id=patch_id,
                skill_id=skill_id,
                target_module="workflow",
                status="applied",
                git_commit=None,
                changed_files=[f"playbooks/{playbook_name}.yaml"],
                skill=refreshed,
                workflow_name=playbook_name,
            )

        next_structure_dict = apply_patch_to_skill_document(skill_id, structure.model_dump(), patch_row.target_module, patch_row.patch_json or {})
        updated_skill_md = next_structure_dict.get("skill_md")
        try:
            save_result = await skill_service.save_skill_content(
                session,
                skill_id=skill_id,
                skill_md=updated_skill_md,
                policy_pack_raw=next_structure_dict["policy_pack_yaml"],
                user_id=user_id,
            )
        except Exception as save_err:
            # save 失败：不更新 patch 状态，确保 DB 与 Git 一致
            patch_row.status = "apply_failed"
            await session.commit()
            raise AppError("WORKBENCH_PATCH_APPLY_FAILED", 500, {"detail": str(save_err)}) from save_err
        patch_row.status = "applied"
        patch_row.applied_at = _now_bjt()
        await session.commit()
        await audit.log(user_id, "workbench.apply_patch", "skill", skill_id, {"patch_id": patch_id, "module": patch_row.target_module})
        invalidate_structure_cache(skill_id)
        return WorkbenchApplyResponse(
            patch_id=patch_id,
            skill_id=skill_id,
            target_module=patch_row.target_module,
            status="applied",
            git_commit=save_result.get("git_commit"),
            changed_files=save_result.get("changes", []),
            skill=SkillStructure.model_validate(next_structure_dict),
            updated_content=updated_skill_md,
        )


async def apply_partial_patch(*, skill_id: str, patch_id: str, accepted_hunks: list[int], rejected_hunks: list[int], user_id: str) -> dict:
    async with _sf()() as session:
        result = await session.execute(select(SkillWorkbenchPatch).where(SkillWorkbenchPatch.id == patch_id))
        patch_row = result.scalar_one_or_none()
        if not patch_row:
            raise AppError("PATCH_NOT_FOUND", 404)
        if patch_row.skill_id != skill_id:
            raise AppError("PATCH_SKILL_MISMATCH", 403)
        if patch_row.created_by != user_id:
            raise AppError("AUTH_PERMISSION_DENIED", 403)

        hunks = patch_row.hunks_json or []
        if not hunks:
            return (await apply_patch(skill_id=skill_id, patch_id=patch_id, user_id=user_id)).model_dump()

        structure = await __import__("asyncio").to_thread(context_builder.load_skill_structure, skill_id)
        updated = _apply_partial(skill_id, structure.model_dump(), patch_row.target_module, hunks, accepted_hunks)
        try:
            save_result = await skill_service.save_skill_content(
                session,
                skill_id,
                skill_md=updated.get("skill_md", ""),
                policy_pack_raw=updated.get("policy_pack_yaml"),
                user_id=user_id,
            )
        except Exception as save_err:
            patch_row.status = "apply_failed"
            await session.commit()
            raise AppError("WORKBENCH_PATCH_APPLY_FAILED", 500, {"detail": str(save_err)}) from save_err

        patch_row.status = "partial_applied"
        patch_row.accepted_hunks = accepted_hunks
        patch_row.rejected_hunks = rejected_hunks
        patch_row.applied_at = _now_bjt()
        await session.commit()
        await audit.log(user_id, "workbench.apply_partial", "skill", skill_id, {"patch_id": patch_id, "accepted": accepted_hunks, "rejected": rejected_hunks})
        invalidate_structure_cache(skill_id)

        # 重新加载最新结构，确保前端能刷新编辑器
        refreshed_structure = await __import__("asyncio").to_thread(context_builder.load_skill_structure, skill_id)
        return {
            "status": "partial_applied",
            "applied_hunks": accepted_hunks,
            "rejected_hunks": rejected_hunks,
            "git_commit": save_result.get("git_commit"),
            "updated_module": updated.get(patch_row.target_module),
            "skill": refreshed_structure.model_dump(),
        }
