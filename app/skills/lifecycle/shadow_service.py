"""
影子运行服务：start/stop/promote/stats/human_record。
从 service.py 拆分而来，专注于影子运行相关逻辑。
"""

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import audit
from app.common.exceptions import AppError
from app.execution.models import DecisionLog
from app.skills.core.models import Skill
from app.skills.core.service_shared import validate_skill_id
from app.common.time_utils import now_bjt


async def start_shadow(db: AsyncSession, skill_id: str, user_id: str) -> dict:
    """
    开始影子运行：draft → shadow。
    影子模式下Skill正常执行，但不推送钉钉给运营。
    """
    validate_skill_id(skill_id)
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    if skill.status not in ("draft", "active"):
        raise AppError("SKILL_VERSION_CONFLICT", 409,
                        detail={"message": f"当前状态 {skill.status} 不能进入影子运行"})

    skill.status = "shadow"
    skill.shadow_start_date = date.today()
    await db.flush()

    await audit.log(user_id, "skill.status_change", "skill", skill_id,
                    detail={"from": "draft", "to": "shadow"})

    return {"skill_id": skill_id, "status": "shadow", "shadow_start_date": str(skill.shadow_start_date)}


async def stop_shadow(db: AsyncSession, skill_id: str, user_id: str) -> dict:
    """停止影子运行：shadow → draft"""
    validate_skill_id(skill_id)
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    if skill.status != "shadow":
        raise AppError("SKILL_VERSION_CONFLICT", 409,
                        detail={"message": "当前不在影子运行状态"})

    skill.status = "draft"
    skill.shadow_start_date = None
    await db.flush()

    await audit.log(user_id, "skill.status_change", "skill", skill_id,
                    detail={"from": "shadow", "to": "draft"})

    return {"skill_id": skill_id, "status": "draft"}


async def promote_shadow(db: AsyncSession, skill_id: str, user_id: str) -> dict:
    """
    影子转正式：shadow → active。
    需要影子运行至少3天 + 手动确认。
    """
    validate_skill_id(skill_id)
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    if skill.status != "shadow":
        raise AppError("SKILL_VERSION_CONFLICT", 409,
                        detail={"message": "当前不在影子运行状态"})

    # 检查影子运行天数
    if skill.shadow_start_date:
        days = (date.today() - skill.shadow_start_date).days
        if days < 3:
            raise AppError("SKILL_VERSION_CONFLICT", 409,
                            detail={"message": f"影子运行仅{days}天，至少需要3天"})
    else:
        raise AppError("SKILL_VERSION_CONFLICT", 409,
                        detail={"message": "影子运行开始日期未记录"})

    # 检查一致率 >= 70%
    stats = await get_shadow_stats(db, skill_id)
    if stats["total_with_human"] > 0 and stats["consistency_rate"] < 70.0:
        raise AppError("SKILL_VERSION_CONFLICT", 409,
                        detail={"message": f"一致率仅{stats['consistency_rate']}%，至少需要70%"})

    skill.status = "active"
    skill.shadow_start_date = None
    skill.updated_at = now_bjt()
    await db.flush()

    await audit.log(user_id, "skill.status_change", "skill", skill_id,
                    detail={"from": "shadow", "to": "active",
                            "shadow_days": days,
                            "consistency_rate": stats["consistency_rate"]})

    return {"skill_id": skill_id, "status": "active",
            "shadow_days": days, "consistency_rate": stats["consistency_rate"]}


async def get_shadow_stats(db: AsyncSession, skill_id: str) -> dict:
    """
    影子运行统计：计算AI建议与人工决策的一致率。
    1. 查decision_log中is_sandbox=True的记录（影子执行）
    2. 查同期人工记录（user_action字段）
    3. 计算一致率 = 一致数 / 有人工记录的总数
    """
    validate_skill_id(skill_id)

    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    if skill.status != "shadow":
        raise AppError("SKILL_VERSION_CONFLICT", 409,
                        detail={"message": "该Skill不在影子运行状态"})

    # 影子运行天数
    shadow_days = 0
    if skill.shadow_start_date:
        shadow_days = (date.today() - skill.shadow_start_date).days

    # 查所有影子期间的决策日志（is_sandbox=True 表示影子执行）
    shadow_logs_result = await db.execute(
        select(DecisionLog)
        .where(DecisionLog.skill_id == skill_id)
        .where(DecisionLog.is_sandbox == True)  # noqa: E712
        .order_by(DecisionLog.created_at.asc())
    )
    shadow_logs = shadow_logs_result.scalars().all()

    total_shadow = len(shadow_logs)
    # 有人工记录的条目（user_action不为空）
    with_human = [log for log in shadow_logs if log.user_action]
    total_with_human = len(with_human)

    # 一致率计算：AI建议的action与人工user_action一致
    # "completed" 表示人工采纳AI建议（一致），"rejected" 表示不一致
    consistent_count = sum(1 for log in with_human if log.user_action == "completed")
    consistency_rate = round(consistent_count / total_with_human * 100, 1) if total_with_human > 0 else 0.0

    # 逐日统计
    daily_stats: dict[str, dict] = {}
    for log in shadow_logs:
        day_str = log.created_at.strftime("%Y-%m-%d") if log.created_at else "unknown"
        if day_str not in daily_stats:
            daily_stats[day_str] = {"date": day_str, "total": 0, "with_human": 0, "consistent": 0}
        daily_stats[day_str]["total"] += 1
        if log.user_action:
            daily_stats[day_str]["with_human"] += 1
            if log.user_action == "completed":
                daily_stats[day_str]["consistent"] += 1

    # 计算每日一致率
    daily_list = []
    for day_data in sorted(daily_stats.values(), key=lambda x: x["date"]):
        rate = round(day_data["consistent"] / day_data["with_human"] * 100, 1) if day_data["with_human"] > 0 else 0.0
        daily_list.append({
            "date": day_data["date"],
            "total": day_data["total"],
            "with_human": day_data["with_human"],
            "consistent": day_data["consistent"],
            "rate": rate,
        })

    return {
        "skill_id": skill_id,
        "shadow_days": shadow_days,
        "shadow_start_date": str(skill.shadow_start_date) if skill.shadow_start_date else None,
        "total_shadow_runs": total_shadow,
        "total_with_human": total_with_human,
        "consistent_count": consistent_count,
        "consistency_rate": consistency_rate,
        "can_promote": shadow_days >= 3 and (total_with_human == 0 or consistency_rate >= 70.0),
        "daily": daily_list,
    }


async def record_human_decision(
    db: AsyncSession, skill_id: str, run_id: str, human_action: str, user_id: str,
) -> dict:
    """
    记录人工决策（用于影子对比）。
    human_action: "completed"=采纳AI建议 / "rejected"=拒绝 / "na"=不适用
    """
    validate_skill_id(skill_id)

    # 查找对应的决策日志
    result = await db.execute(
        select(DecisionLog)
        .where(DecisionLog.run_id == run_id)
        .where(DecisionLog.skill_id == skill_id)
    )
    log = result.scalar_one_or_none()
    if not log:
        raise AppError("SKILL_NOT_FOUND", 404, detail={"message": "未找到对应的执行记录"})

    if human_action not in ("completed", "rejected", "na"):
        raise AppError("SKILL_VERSION_CONFLICT", 400,
                        detail={"message": "human_action 必须为 completed/rejected/na"})

    log.user_action = human_action
    await db.flush()

    # 同步更新 ShadowComparison 记录（前端对比表展示用）
    from app.execution.models import ShadowComparison
    comp_result = await db.execute(
        select(ShadowComparison)
        .where(ShadowComparison.run_id == run_id)
        .where(ShadowComparison.skill_id == skill_id)
    )
    comp = comp_result.scalar_one_or_none()
    if comp:
        comp.human_action = human_action
        comp.human_recorded_by = user_id
        comp.human_recorded_at = now_bjt()
        await db.flush()

    await db.commit()

    await audit.log(user_id, "shadow.human_record", "skill", skill_id,
                    detail={"run_id": run_id, "human_action": human_action})

    return {
        "skill_id": skill_id,
        "run_id": run_id,
        "human_action": human_action,
    }
