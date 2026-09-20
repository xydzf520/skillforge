"""Skill runtime 路由：执行、影子运行、发布、回滚、执行轨迹。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.advisory_lock import acquire_xact_lock
from app.common.cache import invalidate_skill
from app.common.exceptions import AppError
from app.database import async_session_factory, get_db
from app.execution.models import DecisionLog, ExecutionRun, ShadowComparison
from app.skills.lifecycle import service, shadow_service
from app.skills.lifecycle.dependency_graph import build_skill_dependency_graph
from app.skills.core.service_shared import ensure_skill_access
from app.common.time_utils import isoformat_bjt, now_bjt

router = APIRouter()


class RunSkillDirectRequest(BaseModel):
    params: dict
    sandbox: bool = True
    run_mode: str | None = None
    parent_run_id: str | None = None


# v2.9.1 H2：Skill 依赖图 —— fork 血缘 + 依赖数据源 + 下游 Playbook / Skill 引用
@router.get("/{skill_id}/dependency-graph")
async def skill_dependency_graph(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """返回 Skill 的完整依赖图。"""
    return await build_skill_dependency_graph(
        db=db,
        skill_id=skill_id,
        current_user=current_user,
    )


class HumanRecordRequest(BaseModel):
    run_id: str
    human_action: str


class BatchPublishRequest(BaseModel):
    skill_ids: list[str]


class RollbackRequest(BaseModel):
    target_commit: str


@router.post("/{skill_id}/run")
async def run_skill_direct(
    skill_id: str,
    body: RunSkillDirectRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    from app.execution.execution_service import execution_service

    await ensure_skill_access(db, skill_id, current_user, "execute")
    return await execution_service.execute_skill(
        skill_id=skill_id,
        params=body.params,
        sandbox=body.sandbox,
        triggered_by=f"manual:{current_user.id}",
        run_mode=body.run_mode,
        parent_run_id=body.parent_run_id,
    )


# v2.9.1 M2：同输入并发 N 次 + 用户挑 winner —— Skill Multi-Run Voting Phase 1
class ExecuteBatchRequest(BaseModel):
    params: dict = {}
    sandbox: bool = True
    n: int = 3  # 并发次数


def _serialize_execute_batch_error(exc: Exception) -> dict:
    message = str(exc).strip() or type(exc).__name__
    return {
        "type": type(exc).__name__,
        "message": message[:500],
    }


@router.post("/{skill_id}/execute-batch")
async def execute_skill_batch(
    skill_id: str,
    body: ExecuteBatchRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """同 skill / 同 params 并发跑 N 次，返回 batch_id + run_ids。

    N 限制 2-5（防 token 爆）；每 run 写独立 `execution_runs` + 共享 `batch_id`。
    用户之后用 `POST /executions/runs/{id}/mark-winner` 标 winner。
    """
    import asyncio
    import uuid

    from app.common.audit import audit
    from app.common.exceptions import AppError
    from app.execution.execution_service import execution_service

    if body.n < 2 or body.n > 5:
        raise AppError("PARAM_INVALID", 400, {"detail": "n 取值范围 2..5"})

    await ensure_skill_access(db, skill_id, current_user, "execute")

    batch_id = f"batch-{uuid.uuid4().hex[:12]}"

    async def _run_one(idx: int) -> dict:
        try:
            result = await execution_service.execute_skill(
                skill_id=skill_id,
                params=body.params,
                sandbox=body.sandbox,
                triggered_by=f"batch:{batch_id}:{idx}:{current_user.id}",
                run_mode="batch_candidate",
                batch_id=batch_id,
            )
            run_id = result.get("run_id")
            # 兼容旧 execute_skill 返回：这里仍做一次幂等回填。
            if run_id:
                from sqlalchemy import update
                async with async_session_factory() as task_db:
                    await task_db.execute(
                        update(ExecutionRun).where(ExecutionRun.id == run_id).values(batch_id=batch_id)
                    )
                    await task_db.commit()
            return {"run_id": run_id, "index": idx, "status": "ok", "output": result.get("output")}
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            error = _serialize_execute_batch_error(e)
            logger.exception("execute-batch 单 run 失败 skill={} batch={} idx={} err_type={} err={}", skill_id, batch_id, idx, error["type"], error["message"])
            return {"run_id": None, "index": idx, "status": "err", "error": error}

    results = await asyncio.gather(*[_run_one(i) for i in range(body.n)])
    try:
        await audit.log(
            user_id=current_user.id,
            action="execution.execute_batch",
            target_type="skill",
            target_id=skill_id,
            detail={
                "batch_id": batch_id,
                "n": body.n,
                "sandbox": body.sandbox,
                "success": sum(1 for item in results if item["status"] == "ok"),
                "failed": sum(1 for item in results if item["status"] == "err"),
                "failure_types": sorted({
                    item["error"]["type"]
                    for item in results
                    if item["status"] == "err" and isinstance(item.get("error"), dict) and item["error"].get("type")
                }),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("execute-batch audit log 失败 skill={} batch={} err={}", skill_id, batch_id, exc)
    return {
        "batch_id": batch_id,
        "n": body.n,
        "runs": results,
    }


class MarkWinnerRequest(BaseModel):
    batch_id: str  # 校验：run 必须属于此 batch


@router.post("/runs/{run_id}/mark-winner")
async def mark_run_winner(
    run_id: str,
    body: MarkWinnerRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """v2.9.1 M2：用户选中某 run 作为 batch 的冠军 —— 同 batch 其他 run 的 is_winner 设回 false。

    作为后续"选中结果进训练集"的基础：只有 winner run 会作为 adoption signal。
    """
    from sqlalchemy import select, update

    from app.common.audit import audit
    from app.common.exceptions import AppError
    from app.execution.models import ExecutionRun

    target = (
        await db.execute(select(ExecutionRun).where(ExecutionRun.id == run_id).with_for_update())
    ).scalar_one_or_none()
    if not target:
        raise AppError("RUN_NOT_FOUND", 404)
    if target.batch_id != body.batch_id:
        raise AppError("BATCH_MISMATCH", 400, {"detail": "run 不属于此 batch"})

    await acquire_xact_lock(db, f"execution-batch-winner:{body.batch_id}")
    await db.execute(
        select(ExecutionRun.id).where(ExecutionRun.batch_id == body.batch_id).with_for_update()
    )

    # 清掉同 batch 其他的 winner 标记；再设本 run 为 winner
    await db.execute(
        update(ExecutionRun)
        .where(ExecutionRun.batch_id == body.batch_id)
        .values(is_winner=False)
    )
    await db.execute(
        update(ExecutionRun).where(ExecutionRun.id == run_id).values(is_winner=True)
    )
    await db.commit()

    try:
        await audit.log(
            user_id=current_user.id,
            action="execution.mark_winner",
            target_type="execution_run",
            target_id=run_id,
            detail={"batch_id": body.batch_id},
        )
    except Exception:  # noqa: BLE001
        pass

    return {"run_id": run_id, "batch_id": body.batch_id, "is_winner": True}


@router.post("/{skill_id}/shadow/start")
async def start_shadow(
    skill_id: str,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    await ensure_skill_access(db, skill_id, current_user, "edit")
    result = await shadow_service.start_shadow(db, skill_id, current_user.id)
    try:
        await invalidate_skill(skill_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("缓存失效失败: {}", e)
    return result


@router.post("/{skill_id}/shadow/stop")
async def stop_shadow(
    skill_id: str,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    await ensure_skill_access(db, skill_id, current_user, "edit")
    result = await shadow_service.stop_shadow(db, skill_id, current_user.id)
    try:
        await invalidate_skill(skill_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("缓存失效失败: {}", e)
    return result


@router.post("/{skill_id}/shadow/promote")
async def promote_shadow(
    skill_id: str,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp")),
    db: AsyncSession = Depends(get_db),
):
    await ensure_skill_access(db, skill_id, current_user, "edit")
    result = await shadow_service.promote_shadow(db, skill_id, current_user.id)
    try:
        await invalidate_skill(skill_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("缓存失效失败: {}", e)
    return result


@router.get("/{skill_id}/shadow/stats")
@router.get("/{skill_id}/shadow/report")
async def shadow_stats(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    await ensure_skill_access(db, skill_id, current_user, "read")
    return await shadow_service.get_shadow_stats(db, skill_id)


@router.post("/{skill_id}/shadow/human-record")
async def human_record(
    skill_id: str,
    body: HumanRecordRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    await ensure_skill_access(db, skill_id, current_user, "read")
    return await shadow_service.record_human_decision(
        db, skill_id, body.run_id, body.human_action, current_user.id,
    )


@router.get("/{skill_id}/shadow/comparisons")
async def shadow_comparisons(
    skill_id: str,
    limit: int = Query(20, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    await ensure_skill_access(db, skill_id, current_user, "read")
    result = await db.execute(
        select(ShadowComparison)
        .where(ShadowComparison.skill_id == skill_id)
        .order_by(ShadowComparison.created_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return [
        {
            "id": row.id,
            "skill_id": row.skill_id,
            "run_id": row.run_id,
            "old_version": row.old_version,
            "new_version": row.new_version,
            "old_output": row.old_output,
            "new_output": row.new_output,
            "is_divergent": row.is_divergent,
            "divergence_detail": row.divergence_detail,
            "human_action": row.human_action,
            "created_at": isoformat_bjt(row.created_at),
        }
        for row in rows
    ]


@router.get("/{skill_id}/shadow/divergence-rate")
async def shadow_divergence_rate(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    await ensure_skill_access(db, skill_id, current_user, "read")
    total = (await db.execute(
        select(func.count(ShadowComparison.id)).where(ShadowComparison.skill_id == skill_id)
    )).scalar() or 0
    divergent = (await db.execute(
        select(func.count(ShadowComparison.id))
        .where(ShadowComparison.skill_id == skill_id)
        .where(ShadowComparison.is_divergent == True)  # noqa: E712
    )).scalar() or 0

    return {
        "skill_id": skill_id,
        "total_runs": total,
        "divergent_runs": divergent,
        "divergence_rate": round(divergent / total * 100, 1) if total > 0 else 0,
    }


@router.post("/batch-publish")
async def batch_publish(
    body: BatchPublishRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer", "aibp", "biz_owner")),
    db: AsyncSession = Depends(get_db),
):
    from app.execution.sync_service import sync_service
    from app.skills.lifecycle.publish_readiness import check_publish_readiness
    from app.common.audit import audit

    results = []
    for sid in body.skill_ids:
        try:
            skill = await ensure_skill_access(db, sid, current_user, "publish")
            if skill.status not in ("draft", "active", "shadow"):
                results.append({"skill_id": sid, "status": "error", "message": f"状态 {skill.status} 不允许发布"})
                continue

            readiness = await check_publish_readiness(sid, db, include_ai_verify=False)
            if not readiness.can_publish:
                results.append({
                    "skill_id": sid,
                    "status": "blocked",
                    "message": f"质量门禁未通过：{readiness.blocker_count} 个阻断问题",
                    "blockers": readiness.blockers[:5],
                })
                continue

            ver = skill.current_version or "v0.0"
            try:
                parts = ver.lstrip("v").split(".")
                major, minor = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
            except (ValueError, IndexError):
                major, minor = 0, 0
            new_version = f"v{major}.{minor + 1}"
            skill.current_version = new_version
            skill.status = "active"
            skill.updated_at = now_bjt()

            tag_name = f"{sid}/{new_version}"
            try:
                from app.skills.core.git_service import git_service

                git_service.tag(tag_name, f"批量发布 by {current_user.id}")
            except Exception as e:  # noqa: BLE001
                logger.warning("Git tag 失败: {} error={}", tag_name, e)

            results.append({"skill_id": sid, "status": "ok", "version": new_version})
        except Exception as e:  # noqa: BLE001
            results.append({"skill_id": sid, "status": "error", "message": str(e)})

    await db.commit()

    try:
        await audit.log(
            user_id=current_user.id,
            action="skill.batch_publish",
            target_type="skill_batch",
            target_id="batch-publish",
            detail={
                "skill_ids": body.skill_ids,
                "success": sum(1 for item in results if item["status"] == "ok"),
                "failed": sum(1 for item in results if item["status"] == "error"),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("batch-publish audit log 失败 skill_ids={}: {}", body.skill_ids, exc)

    reload_result = {}
    try:
        reload_result = await sync_service.reload_all()
    except Exception as e:  # noqa: BLE001
        reload_result = {"error": str(e)}

    try:
        for sid in body.skill_ids:
            await invalidate_skill(sid)
    except Exception as e:
        logger.warning("批量发布后缓存失效失败 skill_ids={}: {}", body.skill_ids, e)

    return {
        "total": len(body.skill_ids),
        "success": sum(1 for item in results if item["status"] == "ok"),
        "failed": sum(1 for item in results if item["status"] == "error"),
        "results": results,
        "reload": reload_result,
    }


@router.get("/{skill_id}/diff-summary")
async def get_diff_summary(
    skill_id: str,
    target: str = "",
    base: str = "HEAD~1",
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    await ensure_skill_access(db, skill_id, current_user, "read")
    from app.skills.core.git_service import git_service

    # target 默认取最新 commit
    if not target:
        logs = git_service.log(skill_id=skill_id, max_count=1)
        target = logs[0]["hash_full"] if logs else "HEAD"
    return git_service.diff_summary(skill_id, commit_a=base, commit_b=target)


@router.post("/{skill_id}/rollback")
async def rollback_skill(
    skill_id: str,
    body: RollbackRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    result = await service.rollback_skill(db, skill_id, body.target_commit, current_user.id)
    try:
        from app.common.telemetry import record_rollback

        record_rollback(skill_id)
    except Exception as e:
        logger.debug("rollback 遥测记录失败 skill={}: {}", skill_id, e)
    try:
        await invalidate_skill(skill_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("缓存失效失败: {}", e)
    return result


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: str,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await service.delete_skill(db, skill_id, current_user.id)
    try:
        await invalidate_skill(skill_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("缓存失效失败: {}", e)
    return result


@router.post("/{skill_id}/deprecate")
async def deprecate_skill(
    skill_id: str,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    result = await service.deprecate_skill(db, skill_id, current_user.id)
    try:
        await invalidate_skill(skill_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("缓存失效失败: {}", e)
    return result


@router.get("/{skill_id}/execution-trace/{run_id}")
async def get_execution_trace(
    skill_id: str,
    run_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    run_result = await db.execute(select(ExecutionRun).where(ExecutionRun.id == run_id))
    run = run_result.scalar_one_or_none()
    if not run:
        raise AppError("NOT_FOUND", 404)

    log_result = await db.execute(
        select(DecisionLog)
        .where(DecisionLog.run_id == run_id)
        .where(DecisionLog.skill_id == skill_id)
        .order_by(DecisionLog.created_at)
    )
    logs = log_result.scalars().all()

    trace = []
    for log in logs:
        trace.append({
            "log_id": log.id,
            "input_snapshot": log.input_snapshot,
            "output_result": log.output_result,
            "suggested_action": log.suggested_action,
            "approval_status": log.approval_status,
            "user_action": log.user_action,
            "created_at": isoformat_bjt(log.created_at),
        })

    duration_ms = None
    if run.started_at and run.completed_at:
        duration_ms = int((run.completed_at - run.started_at).total_seconds() * 1000)

    return {
        "run_id": run_id,
        "skill_id": skill_id,
        "status": run.status,
        "duration_ms": duration_ms,
        "summary": run.summary,
        "run_mode": run.run_mode,
        "parent_run_id": run.parent_run_id,
        "batch_id": run.batch_id,
        "data_proofs": (run.metadata_json or {}).get("data_proofs") if isinstance(run.metadata_json, dict) else [],
        "sample_used": (run.metadata_json or {}).get("sample_used") if isinstance(run.metadata_json, dict) else None,
        "trace": trace,
        "started_at": isoformat_bjt(run.started_at),
        "completed_at": isoformat_bjt(run.completed_at),
    }


@router.post("/{skill_id}/render-ast")
async def render_ast(
    skill_id: str,
    body: dict,
    current_user: User = Depends(require_state_active),
):
    from app.skills.core.parser import (
        Antipattern,
        Branch,
        DataInput,
        DecisionStep,
        OutputItem,
        SkillStructured,
        skill_parser,
    )

    try:
        parsed = SkillStructured()
        parsed.frontmatter = body.get("frontmatter", {})
        parsed.purpose = body.get("purpose", "")
        parsed.steps = [
            DecisionStep(
                id=item.get("id", ""),
                name=item.get("name", ""),
                description=item.get("description", ""),
                branches=[
                    Branch(
                        condition=branch.get("condition", ""),
                        conclusion=branch.get("conclusion", ""),
                        action=branch.get("action", ""),
                        next_step=branch.get("next_step"),
                    )
                    for branch in item.get("branches", [])
                ],
            )
            for item in body.get("steps", [])
        ]
        parsed.antipatterns = [
            Antipattern(scenario=item.get("scenario", ""), correct_action=item.get("correct_action", ""))
            for item in body.get("antipatterns", [])
        ]
        parsed.output_definition = [
            OutputItem(
                name=item.get("name", ""),
                format=item.get("format", ""),
                recipient=item.get("recipient", ""),
                approval_level=item.get("approval_level", ""),
            )
            for item in body.get("output_definition", [])
        ]
        parsed.data_inputs = [
            DataInput(name=item.get("name", ""), source=item.get("source", ""), frequency=item.get("frequency", ""))
            for item in body.get("data_inputs", [])
        ]
        md = skill_parser.render(parsed)
        return {"skill_md": md}
    except Exception as e:  # noqa: BLE001
        raise AppError("RENDER_FAILED", 400, {"detail": str(e)})


@router.get("/{skill_id}/flow-data")
async def get_flow_data(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    import yaml as _yaml
    from pathlib import Path as _Path

    from app.config import settings
    from app.datasources.models import DataSource
    from app.skills.core.git_service import git_service
    from app.skills.core.parser import skill_parser

    skill_md = git_service.read_file(skill_id, "SKILL.md")
    if not skill_md:
        raise AppError("SKILL_NOT_FOUND", 404)
    parsed = skill_parser.parse(skill_md)

    policy_raw = git_service.read_file(skill_id, "policy_pack.yaml") or ""
    policy_data = {}
    if policy_raw:
        try:
            policy_data = _yaml.safe_load(policy_raw) or {}
        except Exception as e:
            logger.warning("policy_pack.yaml 解析失败 skill={}: {}", skill_id, e)

    scripts = []
    scripts_dir = _Path(settings.SKILL_REPO_PATH) / skill_id / "scripts"
    if scripts_dir.is_dir():
        for item in sorted(scripts_dir.iterdir()):
            if item.is_file() and item.suffix == ".py":
                scripts.append({
                    "name": item.name,
                    "path": f"scripts/{item.name}",
                    "size": item.stat().st_size,
                })

    datasource_status = []
    for di in (parsed.data_inputs or []):
        src_name = di.source if hasattr(di, "source") else ""
        ds_result = await db.execute(select(DataSource).where(DataSource.name == src_name).limit(1))
        ds = ds_result.scalar_one_or_none()
        datasource_status.append({
            "name": di.name if hasattr(di, "name") else "",
            "source": src_name,
            "frequency": di.frequency if hasattr(di, "frequency") else "",
            "is_active": ds.is_active if ds else None,
            "last_updated": isoformat_bjt(ds.updated_at) if ds else None,
        })

    fm = parsed.frontmatter or {}
    return {
        "skill_id": skill_id,
        "frontmatter": fm,
        "purpose": parsed.purpose or "",
        "steps": [
            {
                "id": step.id,
                "name": step.name,
                "description": getattr(step, "description", ""),
                "branches": [
                    {
                        "condition": branch.condition,
                        "conclusion": branch.conclusion,
                        "action": branch.action,
                        "next_step": branch.next_step,
                    }
                    for branch in (step.branches or [])
                ],
            }
            for step in (parsed.steps or [])
        ],
        "antipatterns": [
            {"scenario": item.scenario, "correct_action": item.correct_action}
            for item in (parsed.antipatterns or [])
        ],
        "output_definition": [
            {
                "name": item.name,
                "format": item.format,
                "recipient": item.recipient,
                "approval_level": item.approval_level,
            }
            for item in (parsed.output_definition or [])
        ],
        "data_inputs": datasource_status,
        "policy_pack": policy_data,
        "scripts": scripts,
        "test_case_count": len(parsed.test_cases) if parsed.test_cases else 0,
    }
