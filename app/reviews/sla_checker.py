"""
审核 SLA 催办：定期检查超时未审的 review，通过钉钉催办。
由 scheduler 每 30 分钟调用一次。
"""

from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.models import SystemConfig
from app.reviews.models import Review
from app.skills.core.models import Skill
from app.common.time_utils import now_bjt

# SLA 默认阈值（小时），按 approval_level 区分
DEFAULT_SLA_HOURS = {
    0: 4,
    1: 8,
    2: 24,
    3: 48,
}

# 同一审核 24h 内最多催办次数
MAX_REMINDERS_PER_DAY = 3


async def check_review_sla():
    """检查所有 pending 审核的 SLA，超时则通过钉钉催办。"""
    from app.database import async_session_factory
    from app.dingtalk.outbox import outbox
    from app.common.audit import audit

    async with async_session_factory() as session:
        sla_config = await _get_sla_config(session)

        result = await session.execute(
            select(Review).where(Review.status == "pending")
        )
        pending_reviews = result.scalars().all()

        reminded_count = 0

        for review in pending_reviews:
            if not review.created_at:
                continue

            # 获取对应 Skill 的 approval_level 和审核人
            skill_result = await session.execute(
                select(Skill).where(Skill.id == review.skill_id)
            )
            skill = skill_result.scalar_one_or_none()
            level = skill.approval_level if skill else 0

            # 计算 SLA 阈值
            sla_hours = sla_config.get(str(level), DEFAULT_SLA_HOURS.get(level, 24))
            deadline = review.created_at + timedelta(hours=sla_hours)

            if now_bjt() <= deadline:
                continue

            # 超时 → 催办
            hours_overdue = (now_bjt() - deadline).total_seconds() / 3600

            # 去重：检查 24h 内已催办次数
            reminder_count = await _count_recent_reminders(session, review.id)
            if reminder_count >= MAX_REMINDERS_PER_DAY:
                continue

            # 查找审核人
            reviewer_id = skill.approver if skill else None
            if not reviewer_id:
                continue

            reviewer_user = (await session.execute(
                select(User).where(User.id == reviewer_id)
            )).scalar_one_or_none()

            if not reviewer_user or not reviewer_user.dingtalk_user_id:
                continue

            # 构建催办消息
            payload = _build_sla_reminder(
                review_id=review.id,
                skill_id=review.skill_id,
                submitter=review.submitter,
                hours_overdue=round(hours_overdue, 1),
            )

            # [H1] 共享 SLA 扫描的 session: 催办计数与 outbox 入队同一事务
            await outbox.enqueue(
                message_type="work_notice",
                recipient=reviewer_user.dingtalk_user_id,
                payload=payload,
                priority=1,
                related_type="review_sla",
                related_id=str(review.id),
                session=session,
            )

            reminded_count += 1
            logger.info(
                f"审核SLA催办: review={review.id} "
                f"超时 {hours_overdue:.1f}h to={reviewer_user.dingtalk_user_id}"
            )

        if reminded_count:
            await audit.log("svc_reviews", "review.sla_reminder", detail={
                "reminded_count": reminded_count,
            })

    logger.info(f"SLA催办检查完成: 催办 {reminded_count} 条")


async def _get_sla_config(session: AsyncSession) -> dict:
    """从 system_config 表读取 SLA 配置"""
    result = await session.execute(
        select(SystemConfig).where(SystemConfig.key == "review_sla_hours")
    )
    config = result.scalar_one_or_none()
    if config and config.value:
        return config.value
    return {str(k): v for k, v in DEFAULT_SLA_HOURS.items()}


async def _count_recent_reminders(session: AsyncSession, review_id: int) -> int:
    """查询 24h 内对同一审核的催办次数（通过 outbox 记录）"""
    from app.dingtalk.models import DingTalkOutbox
    from sqlalchemy import func

    cutoff = now_bjt() - timedelta(hours=24)
    result = await session.execute(
        select(func.count(DingTalkOutbox.id))
        .where(DingTalkOutbox.related_type == "review_sla")
        .where(DingTalkOutbox.related_id == str(review_id))
        .where(DingTalkOutbox.created_at >= cutoff)
    )
    return result.scalar() or 0


def _build_sla_reminder(review_id: int, skill_id: str, submitter: str,
                        hours_overdue: float) -> dict:
    """构建催办消息"""
    return {
        "title": f"审核催办: {skill_id}",
        "markdown": (
            f"**审核超时提醒**\n\n"
            f"- **审核ID**: #{review_id}\n"
            f"- **Skill**: {skill_id}\n"
            f"- **提交人**: {submitter}\n"
            f"- **超时**: {hours_overdue}小时\n\n"
            f"请尽快完成审核。"
        ),
    }
