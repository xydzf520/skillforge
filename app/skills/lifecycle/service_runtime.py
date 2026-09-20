"""Skills runtime 域服务。"""

from __future__ import annotations

from datetime import datetime

import yaml
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.time_utils import now_bjt

from app.common.audit import audit
from app.common.exceptions import AppError
from app.config import settings
from app.execution.models import DecisionLog, ExecutionStep, NodeScheduleConfig, ShadowComparison
from app.execution.scheduler import scheduler
from app.optimizer.models import (
    BenchmarkCase,
    BenchmarkPack,
    BenchmarkRun,
    OptimizerCandidate,
    OptimizerSession,
)
from app.reviews.models import Review, ReviewComment
from app.skills.core.git_service import git_service
from app.skills.core.models import Skill, SkillLock, UserSkillPin
from app.skills.core.service_shared import validate_skill_id
from app.testing.models import Conversation
from app.workbench.models import (
    SkillWorkbenchAIRun,
    SkillWorkbenchMessage,
    SkillWorkbenchPatch,
    SkillWorkbenchReference,
    SkillWorkbenchSession,
    SkillWorkbenchValidationRun,
)


async def get_skill_history(skill_id: str, max_count: int = 20) -> list[dict]:
    validate_skill_id(skill_id)
    return git_service.log(skill_id=skill_id, max_count=max_count)


async def get_skill_diff(skill_id: str, commit_a: str = "HEAD~1", commit_b: str = "HEAD") -> str:
    validate_skill_id(skill_id)
    return git_service.diff(skill_id=skill_id, commit_a=commit_a, commit_b=commit_b)


async def update_params(
    db: AsyncSession,
    skill_id: str,
    params: dict,
    user_id: str = "system",
) -> dict:
    validate_skill_id(skill_id)

    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    raw = git_service.read_file(skill_id, "policy_pack.yaml") or ""
    try:
        current = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        current = {}

    current.update(params)
    new_raw = yaml.dump(current, allow_unicode=True, default_flow_style=False)
    git_service.write_file(skill_id, "policy_pack.yaml", new_raw)

    commit_sha = git_service.commit_all("更新参数", user_id, skill_id=skill_id)
    if commit_sha:
        skill.git_commit = commit_sha
    skill.updated_at = now_bjt()
    await db.flush()
    await audit.log(user_id, "skill.edit.params", "skill", skill_id, detail={"params": list(params.keys())})
    return {"skill_id": skill_id, "git_commit": commit_sha, "params": current}


async def deprecate_skill(db: AsyncSession, skill_id: str, user_id: str) -> dict:
    validate_skill_id(skill_id)
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    old_status = skill.status
    skill.status = "deprecated"
    skill.updated_at = now_bjt()
    await db.flush()
    await audit.log(user_id, "skill.deprecate", "skill", skill_id, detail={"from": old_status, "to": "deprecated"})
    return {"skill_id": skill_id, "status": "deprecated"}


async def delete_skill(db: AsyncSession, skill_id: str, user_id: str) -> dict:
    """删除Skill：级联清理所有关联表 + Git仓库目录。

    清理顺序：
    - 先删 FK 子表（OptimizerCandidate → OptimizerSession）
    - 再删按 skill_id 关联的孤儿表
    - 最后删 Skill 主记录 + Git 目录
    """
    import asyncio
    import shutil
    from sqlalchemy import delete as sa_delete, text

    validate_skill_id(skill_id)
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    # 1. 置顶记录 + 编辑锁
    await db.execute(sa_delete(UserSkillPin).where(UserSkillPin.skill_id == skill_id))
    await db.execute(sa_delete(SkillLock).where(SkillLock.skill_id == skill_id))

    # 2. Optimizer 级联清理
    session_ids_stmt = select(OptimizerSession.id).where(OptimizerSession.skill_id == skill_id)
    await db.execute(sa_delete(OptimizerCandidate).where(
        OptimizerCandidate.session_id.in_(session_ids_stmt)
    ))
    await db.execute(text(
        "DELETE FROM optimizer_events WHERE session_id IN "
        "(SELECT id FROM optimizer_sessions WHERE skill_id = :sid)"
    ), {"sid": skill_id})
    await db.execute(sa_delete(OptimizerSession).where(OptimizerSession.skill_id == skill_id))

    # 3. Benchmark 级联清理
    pack_ids_stmt = select(BenchmarkPack.id).where(BenchmarkPack.skill_id == skill_id)
    await db.execute(sa_delete(BenchmarkRun).where(BenchmarkRun.pack_id.in_(pack_ids_stmt)))
    await db.execute(sa_delete(BenchmarkCase).where(BenchmarkCase.pack_id.in_(pack_ids_stmt)))
    await db.execute(sa_delete(BenchmarkPack).where(BenchmarkPack.skill_id == skill_id))

    # 4. 审核 + 执行决策
    await db.execute(sa_delete(ReviewComment).where(
        ReviewComment.review_id.in_(select(Review.id).where(Review.skill_id == skill_id))
    ))
    await db.execute(sa_delete(Review).where(Review.skill_id == skill_id))
    await db.execute(sa_delete(DecisionLog).where(DecisionLog.skill_id == skill_id))
    await db.execute(sa_delete(ExecutionStep).where(ExecutionStep.skill_id == skill_id))
    await db.execute(sa_delete(ShadowComparison).where(ShadowComparison.skill_id == skill_id))

    # 5. Workbench 清理（按 FK 依赖顺序：messages/ai_runs → references → patches → sessions）
    wb_session_ids = select(SkillWorkbenchSession.id).where(SkillWorkbenchSession.skill_id == skill_id)
    await db.execute(sa_delete(SkillWorkbenchMessage).where(SkillWorkbenchMessage.session_id.in_(wb_session_ids)))
    wb_patch_ids = select(SkillWorkbenchPatch.id).where(SkillWorkbenchPatch.skill_id == skill_id)
    await db.execute(sa_delete(SkillWorkbenchAIRun).where(SkillWorkbenchAIRun.session_id.in_(wb_session_ids)))
    await db.execute(sa_delete(SkillWorkbenchReference).where(SkillWorkbenchReference.patch_id.in_(wb_patch_ids)))
    await db.execute(sa_delete(SkillWorkbenchValidationRun).where(SkillWorkbenchValidationRun.skill_id == skill_id))
    await db.execute(sa_delete(SkillWorkbenchPatch).where(SkillWorkbenchPatch.skill_id == skill_id))
    await db.execute(sa_delete(SkillWorkbenchSession).where(SkillWorkbenchSession.skill_id == skill_id))

    # 6. Agent 对话
    await db.execute(sa_delete(Conversation).where(Conversation.skill_id == skill_id))

    # 7. 节点调度配置 + 远端节点定时任务
    await db.execute(sa_delete(NodeScheduleConfig).where(NodeScheduleConfig.skill_id == skill_id))

    # 8. Skill 同步记录（sync_jobs / sync_attempts）
    await db.execute(text("DELETE FROM skill_sync_attempts WHERE skill_id = :sid"), {"sid": skill_id})
    await db.execute(text("DELETE FROM skill_sync_jobs WHERE skill_id = :sid"), {"sid": skill_id})

    # 9. Skill 实例（下发到各节点的副本）
    await db.execute(text("DELETE FROM skill_instances WHERE skill_id = :sid"), {"sid": skill_id})

    # 10. Skill 生态（tags / members / drafts / submissions / releases / previews）
    await db.execute(text("DELETE FROM skill_tags WHERE skill_id = :sid"), {"sid": skill_id})
    await db.execute(text("DELETE FROM skill_members WHERE skill_id = :sid"), {"sid": skill_id})
    await db.execute(text("DELETE FROM skill_drafts WHERE skill_id = :sid"), {"sid": skill_id})
    await db.execute(text("DELETE FROM skill_submissions WHERE skill_id = :sid"), {"sid": skill_id})
    await db.execute(text("DELETE FROM skill_previews WHERE skill_id = :sid"), {"sid": skill_id})
    await db.execute(text("DELETE FROM skill_runs WHERE skill_id = :sid"), {"sid": skill_id})
    await db.execute(text("DELETE FROM skill_reviews WHERE skill_id = :sid"), {"sid": skill_id})
    await db.execute(text("DELETE FROM skill_releases WHERE skill_id = :sid"), {"sid": skill_id})
    await db.execute(text("DELETE FROM contract_drifts WHERE skill_id = :sid"), {"sid": skill_id})

    # 11. 移除 APScheduler 定时任务
    job_id = f"skill_{skill_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        logger.info("Skill 删除同步清除定时任务: {} job={}", skill_id, job_id)

    # 12. 删除主记录
    await db.delete(skill)

    # 13. 先 flush DB 确认事务成功，再操作文件系统
    # 注：ExecutionRun 无 skill_id 列，通过 ExecutionStep 间接关联，
    # 删除后会产生孤儿 run 记录，这是可接受的（执行历史保留）。
    # audit_logs / usage_logs 也有意保留（审计不可变）。
    await db.flush()
    await audit.log(user_id, "skill.delete", "skill", skill_id)

    # 14. DB 成功后才操作文件系统（避免 DB 回滚但文件已删的不一致）
    skill_dir = git_service.skill_dir(skill_id)
    if skill_dir.exists():
        try:
            await asyncio.to_thread(git_service.delete_skill_dir, skill_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("删除 skills-repo 目录失败，DB 删除将回滚: {}", exc)
            raise AppError(
                "SKILL_DELETE_FILES_FAILED",
                500,
                {"detail": f"删除 Skill 文件目录失败: {exc}", "path": str(skill_dir)},
            ) from exc
        try:
            await asyncio.to_thread(
                git_service.commit_all,
                f"删除 Skill: {skill_id}",
                user_id,
                skill_id=skill_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("删除 Skill git commit 失败（文件已删除，DB 删除继续）: {}", exc)

    # 15. 级联清理 AIClaw skills 目录 + 触发 reload，否则 AIClaw 仍会执行已删除的 Skill
    if settings.AICLAW_SKILLS_DIR:
        from pathlib import Path
        aiclaw_dir = Path(settings.AICLAW_SKILLS_DIR) / skill_id
        if aiclaw_dir.is_dir():
            try:
                await asyncio.to_thread(shutil.rmtree, aiclaw_dir)
            except Exception as exc:  # noqa: BLE001
                logger.warning("删除 AIClaw 目录失败（DB 已清理，需手动清理）: {} path={}", exc, aiclaw_dir)
        try:
            from app.execution.openclaw_client import default_client
            await default_client.reload_skills()
        except Exception as exc:  # noqa: BLE001
            logger.debug("AIClaw reload 失败（非阻塞）: {}", exc)

    return {"skill_id": skill_id, "deleted": True}


async def rollback_skill(
    db: AsyncSession,
    skill_id: str,
    target_commit: str,
    user_id: str = "system",
) -> dict:
    validate_skill_id(skill_id)
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    old_content = git_service.get_file_at_commit(skill_id, "SKILL.md", target_commit)
    if old_content is None:
        raise AppError("GIT_OPERATION_FAILED", 400, detail={"reason": f"目标版本 {target_commit} 中未找到 {skill_id}/SKILL.md"})

    repo = git_service.repo
    skill_dir_rel = skill_id
    try:
        head_files = set(git_service.list_files_at_commit(skill_id, "HEAD"))
        target_files = set(git_service.list_files_at_commit(skill_id, target_commit))
        files_to_remove = head_files - target_files

        if files_to_remove:
            try:
                repo.git.rm("-f", "--", *sorted(files_to_remove))
            except Exception as rm_exc:  # noqa: BLE001
                from pathlib import Path

                from loguru import logger
                for fp in files_to_remove:
                    abs_path = Path(settings.SKILL_REPO_PATH) / fp
                    try:
                        if abs_path.exists():
                            abs_path.unlink()
                    except Exception as unlink_err:
                        logger.warning("fallback unlink 失败 path={}: {}", abs_path, unlink_err)
                logger.warning("git rm 部分失败 已 fallback unlink: {}", rm_exc)

        repo.git.checkout(target_commit, "--", skill_dir_rel)
    except Exception as e:  # noqa: BLE001
        raise AppError("GIT_OPERATION_FAILED", 500, detail={"reason": f"Git checkout失败: {e}"})

    commit_hash = git_service.commit_all(
        message=f"回滚到版本 {target_commit[:8]}",
        author=user_id,
        skill_id=skill_id,
    )

    old_status = skill.status
    skill.status = "draft"
    skill.git_commit = commit_hash or skill.git_commit
    skill.updated_at = now_bjt()
    await db.flush()
    await audit.log(
        user_id, "skill.rollback", "skill", skill_id,
        detail={"target_commit": target_commit, "new_commit": commit_hash, "old_status": old_status},
    )
    return {
        "skill_id": skill_id,
        "status": "draft",
        "rolled_back_to": target_commit[:8],
        "new_commit": commit_hash[:8] if commit_hash else None,
    }
