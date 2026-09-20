"""节点定时任务查询。"""

from __future__ import annotations

from sqlalchemy import Float, case, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.models import DecisionLog, ExecutionRun, NodeScheduleConfig, OpenClawInstance, SkillSyncAttempt
from app.skills.core.models import Skill
from app.tasktree.schemas import NodeSchedulesResponse, ScheduledJobItem


# 节点定时页只统计桥端上报和节点漏跑 watchdog fallback；
# 中心 APScheduler 的普通 scheduler 运行不算作节点本地定时。
SCHEDULE_TRIGGER_TYPES = ("node_scheduler", "scheduler:fallback")


def _short_commit(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text[:8] if text else None


def _attempt_git_commit(attempt: SkillSyncAttempt | None) -> str | None:
    if attempt is None or not isinstance(attempt.result, dict):
        return None
    for key in ("deployed_git_commit_full", "docker_git_commit_full", "git_commit_full"):
        value = str(attempt.result.get(key) or "").strip()
        if value:
            return value
    value = str(attempt.result.get("git_commit") or "").strip()
    return value or None


def _latest_skill_git_commit_full(skill_id: str, fallback: str | None = None) -> str | None:
    try:
        from app.skills.core.git_service import git_service

        logs = git_service.log(skill_id=skill_id, max_count=1)
        if logs:
            return str(logs[0].get("hash_full") or "").strip() or None
    except Exception:
        pass
    return str(fallback or "").strip() or None


async def get_instance_schedules(
    db: AsyncSession,
    instance_id: str,
) -> NodeSchedulesResponse:
    """查询节点关联的定时任务及执行统计。

    这里以 NodeScheduleConfig + 节点自身的定时执行历史为准。
    旧实现返回所有 cron Skill 的全局统计，会把其它节点和手动执行混进来。
    """
    inst = (await db.execute(
        select(OpenClawInstance).where(OpenClawInstance.id == instance_id)
    )).scalar_one_or_none()

    inst_name = inst.name if inst else instance_id

    # 当前已下发到该节点的定时配置。
    config_rows = (await db.execute(
        select(NodeScheduleConfig)
        .where(NodeScheduleConfig.instance_id == instance_id)
        .order_by(NodeScheduleConfig.skill_id)
    )).scalars().all()
    config_map = {row.skill_id: row for row in config_rows}

    # 也把该节点曾经定时执行过的 Skill 纳入（即使当前已归档），方便看历史。
    # 平台默认节点只展示当前真正下发到该节点的配置。旧兜底/误装历史不能
    # 让平台节点看起来像安装了业务 Skill。
    if inst and bool(getattr(inst, "is_platform_default", False)):
        historic_ids = []
    else:
        historic_ids = (await db.execute(
            select(ExecutionRun.skill_id)
            .where(ExecutionRun.trigger_type.in_(SCHEDULE_TRIGGER_TYPES))
            .where(ExecutionRun.source_instance_id == instance_id)
            .where(ExecutionRun.skill_id.isnot(None))
            .group_by(ExecutionRun.skill_id)
        )).scalars().all()

    combined_ids = list(set(config_map) | set(historic_ids))
    if not combined_ids:
        return NodeSchedulesResponse(instance_id=instance_id, instance_name=inst_name, jobs=[])

    # 批量查 Skill 名称
    name_rows = (await db.execute(
        select(
            Skill.id,
            Skill.name,
            Skill.display_name,
            Skill.trigger_type,
            Skill.trigger_expression,
            Skill.status,
            Skill.current_version,
            Skill.git_commit,
        )
        .where(Skill.id.in_(combined_ids))
    )).all()
    skill_map = {
        row.id: {
            "name": row.display_name or row.name or row.id,
            "trigger_type": row.trigger_type,
            "cron": row.trigger_expression,
            "status": row.status,
            "current_version": row.current_version,
            "git_commit": _latest_skill_git_commit_full(row.id, row.git_commit),
        }
        for row in name_rows
    }

    sync_rows = (await db.execute(
        select(SkillSyncAttempt)
        .where(SkillSyncAttempt.instance_id == instance_id)
        .where(SkillSyncAttempt.skill_id.in_(combined_ids))
        .where(SkillSyncAttempt.status == "succeeded")
        .order_by(SkillSyncAttempt.completed_at.desc().nullslast(), SkillSyncAttempt.id.desc())
    )).scalars().all()
    latest_sync_map: dict[str, SkillSyncAttempt] = {}
    for attempt in sync_rows:
        latest_sync_map.setdefault(attempt.skill_id, attempt)

    # 执行统计（只统计该节点的定时执行，不混入手动执行和其它节点）
    case_ok = func.sum(case((ExecutionRun.status == "completed", 1), else_=0))
    case_fail = func.sum(case((ExecutionRun.status.in_(("failed", "timeout")), 1), else_=0))
    elapsed_seconds = func.extract("epoch", ExecutionRun.completed_at) - func.extract("epoch", ExecutionRun.started_at)
    runtime_duration_seconds = cast(func.jsonb_extract_path_text(ExecutionRun.metadata_json, "runtime", "duration_ms"), Float) / 1000.0
    script_duration_seconds = cast(func.jsonb_extract_path_text(ExecutionRun.metadata_json, "runtime", "script_duration_ms"), Float) / 1000.0
    case_dur = func.avg(case(
        (elapsed_seconds > 0, elapsed_seconds),
        else_=func.coalesce(runtime_duration_seconds, script_duration_seconds, elapsed_seconds),
    ))
    stats = (await db.execute(
        select(
            ExecutionRun.skill_id,
            func.count().label("total"),
            case_ok.label("ok"),
            case_fail.label("fail"),
            func.max(ExecutionRun.started_at).label("last_run"),
            case_dur.label("avg_dur"),
        )
        .where(ExecutionRun.skill_id.in_(combined_ids))
        .where(ExecutionRun.trigger_type.in_(SCHEDULE_TRIGGER_TYPES))
        .where(ExecutionRun.source_instance_id == instance_id)
        .group_by(ExecutionRun.skill_id)
    )).all()

    stats_map = {}
    for row in stats:
        stats_map[row.skill_id] = {
            "total": row.total or 0,
            "ok": row.ok or 0,
            "fail": row.fail or 0,
            "last_run": row.last_run,
            "avg_dur": float(row.avg_dur) if row.avg_dur else None,
        }

    # 最近一次执行的状态
    last_status_rows = (await db.execute(
        select(ExecutionRun.skill_id, ExecutionRun.status)
        .where(ExecutionRun.skill_id.in_(combined_ids))
        .where(ExecutionRun.trigger_type.in_(SCHEDULE_TRIGGER_TYPES))
        .where(ExecutionRun.source_instance_id == instance_id)
        .order_by(ExecutionRun.started_at.desc())
        .limit(len(combined_ids) * 2)
    )).all()
    last_status_map: dict[str, str] = {}
    for row in last_status_rows:
        if row.skill_id not in last_status_map:
            last_status_map[row.skill_id] = row.status

    # 输出项数只统计成功产物；失败 traceback/timeout 不能算作业务输出。
    output_rows = (await db.execute(
        select(
            DecisionLog.skill_id,
            func.count().label("cnt"),
        )
        .join(ExecutionRun, ExecutionRun.id == DecisionLog.run_id)
        .where(DecisionLog.skill_id.in_(combined_ids))
        .where(ExecutionRun.trigger_type.in_(SCHEDULE_TRIGGER_TYPES))
        .where(ExecutionRun.source_instance_id == instance_id)
        .where(ExecutionRun.status == "completed")
        .where(DecisionLog.output_result.isnot(None))
        .where(text("NOT (decision_log.output_result ? 'error')"))
        .group_by(DecisionLog.skill_id)
    )).all()
    output_map = {row.skill_id: row.cnt for row in output_rows}

    jobs = []
    for sid in combined_ids:
        info = skill_map.get(sid, {"name": sid, "cron": None, "status": "deleted"})
        config = config_map.get(sid)
        st = stats_map.get(sid, {})
        attempt = latest_sync_map.get(sid)
        current_commit = str(info.get("git_commit") or "").strip() or None
        deployed_commit = _attempt_git_commit(attempt)
        trigger_type = str(info.get("trigger_type") or "").strip()
        cron_expression = str(info.get("cron") or "").strip()
        job_status = "deleted" if info["status"] == "deleted" else "stopped"
        if config is not None:
            # ack_ok 有三态：True=节点确认, False=推送失败, None=未推送
            # 节点实际执行过说明配置已生效，即使 ack 丢失也显示 active
            has_run = st.get("total", 0) > 0
            if config.ack_ok is True or has_run:
                job_status = "active"
            elif config.ack_ok is False:
                job_status = "sync_failed"
            else:
                job_status = "pending"
        elif info["status"] == "archived":
            job_status = "archived"
        elif trigger_type == "cron" and cron_expression:
            job_status = "not_scheduled"
        jobs.append(ScheduledJobItem(
            skill_id=sid,
            skill_name=info["name"],
            cron_expression=config.cron_expression if config else info["cron"],
            status=job_status,
            total_runs=st.get("total", 0),
            success_count=st.get("ok", 0),
            failed_count=st.get("fail", 0),
            last_run_at=st.get("last_run"),
            last_status=last_status_map.get(sid),
            avg_duration_seconds=st.get("avg_dur"),
            total_output_items=output_map.get(sid, 0),
            current_version=info.get("current_version"),
            git_commit=_short_commit(current_commit),
            git_commit_full=current_commit,
            deployed_git_commit=_short_commit(deployed_commit),
            deployed_git_commit_full=deployed_commit,
            docker_git_commit=_short_commit(deployed_commit),
            docker_git_commit_full=deployed_commit,
            sync_version_tag=attempt.version_tag if attempt else None,
            sync_completed_at=attempt.completed_at if attempt else None,
            git_deploy_recorded=bool(deployed_commit),
        ))

    from datetime import datetime as _dt
    _epoch = _dt(2000, 1, 1)
    jobs.sort(key=lambda j: j.last_run_at or _epoch, reverse=True)
    return NodeSchedulesResponse(instance_id=instance_id, instance_name=inst_name, jobs=jobs)


async def _schedule_target_instance_ids(db: AsyncSession, skill_id: str) -> list[str]:
    """找出应同步该 Skill 定时快照的节点。"""
    non_platform_ids = set((await db.execute(
        select(OpenClawInstance.id)
        .where(OpenClawInstance.is_active == True)  # noqa: E712
        .where(OpenClawInstance.is_platform_default == False)  # noqa: E712
        .where(
            OpenClawInstance.id.in_(
                select(NodeScheduleConfig.instance_id)
                .where(NodeScheduleConfig.skill_id == skill_id)
            )
        )
    )).scalars().all())
    non_platform_ids.update((await db.execute(
        select(OpenClawInstance.id)
        .where(OpenClawInstance.is_active == True)  # noqa: E712
        .where(OpenClawInstance.is_platform_default == False)  # noqa: E712
        .where(
            OpenClawInstance.id.in_(
                select(SkillSyncAttempt.instance_id)
                .where(SkillSyncAttempt.skill_id == skill_id)
                .where(SkillSyncAttempt.status == "succeeded")
                .where(SkillSyncAttempt.instance_id.isnot(None))
            )
        )
    )).scalars().all())
    config_ids = set((await db.execute(
        select(NodeScheduleConfig.instance_id)
        .where(NodeScheduleConfig.skill_id == skill_id)
    )).scalars().all())
    synced_ids = set((await db.execute(
        select(SkillSyncAttempt.instance_id)
        .where(SkillSyncAttempt.skill_id == skill_id)
        .where(SkillSyncAttempt.status == "succeeded")
        .where(SkillSyncAttempt.instance_id.isnot(None))
        .group_by(SkillSyncAttempt.instance_id)
    )).scalars().all())
    target_ids = config_ids | synced_ids
    if non_platform_ids:
        platform_ids = set((await db.execute(
            select(OpenClawInstance.id)
            .where(OpenClawInstance.is_platform_default == True)  # noqa: E712
        )).scalars().all())
        target_ids.difference_update(platform_ids)
    return sorted(target_ids)


async def _push_schedule_snapshot(skill_id: str, target_instance_ids: list[str], logger) -> None:
    if not target_instance_ids:
        return
    try:
        from app.execution.sync_service import sync_service

        await sync_service.push_schedules_to_targets(
            skill_ids=[skill_id],
            target_instance_ids=target_instance_ids,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("定时任务远端同步失败: {} targets={} error={}", skill_id, target_instance_ids, exc)



async def update_skill_schedule(
    db: AsyncSession,
    skill_id: str,
    action: str,
    cron_expression: str | None,
    user_id: str,
) -> dict:
    """启动/停止/修改 Skill 的定时调度，同步更新 DB + APScheduler。"""
    from app.common.exceptions import AppError
    from app.execution.scheduler import scheduler
    from apscheduler.triggers.cron import CronTrigger
    from loguru import logger

    skill = (await db.execute(select(Skill).where(Skill.id == skill_id))).scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    job_id = f"skill_{skill_id}"
    target_instance_ids = await _schedule_target_instance_ids(db, skill_id)

    if action == "stop":
        skill.trigger_type = "manual"
        skill.trigger_expression = None
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        await db.commit()
        await _push_schedule_snapshot(skill_id, target_instance_ids, logger)
        logger.info("定时任务已停止: {} 操作人: {}", skill_id, user_id)
        return {"skill_id": skill_id, "action": "stop", "message": "定时任务已停止"}

    if action == "start":
        if not skill.trigger_expression and not cron_expression:
            raise AppError("PARAM_INVALID", 400, {"detail": "启动需要 cron 表达式"})
        cron = cron_expression or skill.trigger_expression
        try:
            CronTrigger.from_crontab(cron)
        except Exception as e:
            raise AppError("PARAM_INVALID", 400, {"detail": f"cron 表达式无效: {e}"})
        skill.trigger_type = "cron"
        skill.trigger_expression = cron
        from app.execution.scheduler import _execute_skill_job
        scheduler.add_job(
            _execute_skill_job,
            CronTrigger.from_crontab(cron),
            args=[skill_id],
            id=job_id,
            replace_existing=True,
            name=f"Skill: {skill.name}",
        )
        await db.commit()
        await _push_schedule_snapshot(skill_id, target_instance_ids, logger)
        logger.info("定时任务已启动: {} cron={} 操作人: {}", skill_id, cron, user_id)
        return {"skill_id": skill_id, "action": "start", "cron": cron, "message": "定时任务已启动"}

    if action == "update":
        if not cron_expression:
            raise AppError("PARAM_INVALID", 400, {"detail": "修改需要新的 cron 表达式"})
        try:
            CronTrigger.from_crontab(cron_expression)
        except Exception as e:
            raise AppError("PARAM_INVALID", 400, {"detail": f"cron 表达式无效: {e}"})
        skill.trigger_expression = cron_expression
        skill.trigger_type = "cron"
        from app.execution.scheduler import _execute_skill_job
        scheduler.add_job(
            _execute_skill_job,
            CronTrigger.from_crontab(cron_expression),
            args=[skill_id],
            id=job_id,
            replace_existing=True,
            name=f"Skill: {skill.name}",
        )
        await db.commit()
        await _push_schedule_snapshot(skill_id, target_instance_ids, logger)
        logger.info("定时任务已更新: {} cron={} 操作人: {}", skill_id, cron_expression, user_id)
        return {"skill_id": skill_id, "action": "update", "cron": cron_expression, "message": "调度规则已更新"}

    raise AppError("PARAM_INVALID", 400, {"detail": f"未知操作: {action}"})
