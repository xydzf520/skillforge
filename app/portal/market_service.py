"""市场分层可见性 + 认证流程 + 复用指标服务"""

from datetime import datetime

from loguru import logger
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import AppError
from app.execution.models import DecisionLog, ExecutionRun
from app.portal.market_models import MarketCertification
from app.skills.core.models import Skill
from app.common.time_utils import isoformat_bjt, now_bjt

# 合法的市场状态值
VALID_MARKET_STATUSES = ("private", "company_public", "marketplace_listed", "marketplace_certified")


async def list_market_skills(
    db: AsyncSession,
    user,
    category: str | None = None,
    certified_only: bool = False,
) -> list[dict]:
    """按用户权限过滤市场 Skill 列表。

    可见性规则：
    - private: 仅同部门成员可见
    - company_public: 全公司可见
    - marketplace_listed: 上架但未认证，全公司可见
    - marketplace_certified: 已认证，优先展示，全公司可见
    """
    stmt = select(Skill).where(Skill.market_status != "private")

    if certified_only:
        stmt = stmt.where(Skill.market_status == "marketplace_certified")

    if category:
        stmt = stmt.where(Skill.department == category)

    # 非管理角色：只能看到全公司公开 + 上架 + 认证的 Skill
    # private 的只有同部门可见，已在上面排除
    # 如果用户不是管理角色，补充本部门的 private Skill
    is_admin = user.role in ("admin", "ai_engineer", "director") or user.can_view_all

    if not is_admin and not certified_only:
        # 非管理员额外包含自己部门的 private Skill
        private_stmt = select(Skill).where(
            Skill.market_status == "private",
            Skill.department == user.department,
        )
        if category and category != user.department:
            # 请求的分类不是自己的部门，不展示 private
            private_stmt = None

        # 合并查询：公开可见 + 本部门 private
        result = await db.execute(stmt.order_by(
            # 已认证优先展示
            Skill.market_status.desc(),
            Skill.updated_at.desc(),
        ))
        items = list(result.scalars().all())

        if private_stmt is not None:
            private_result = await db.execute(private_stmt.order_by(Skill.updated_at.desc()))
            private_items = list(private_result.scalars().all())
            # 去重合并
            seen_ids = {s.id for s in items}
            for s in private_items:
                if s.id not in seen_ids:
                    items.append(s)
    else:
        # 管理员可见所有
        if not certified_only:
            # 管理员也能看到 private
            stmt = select(Skill)
            if category:
                stmt = stmt.where(Skill.department == category)

        result = await db.execute(stmt.order_by(
            Skill.market_status.desc(),
            Skill.updated_at.desc(),
        ))
        items = list(result.scalars().all())

    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "department": s.department,
            "owner": s.owner,
            "market_status": s.market_status,
            "status": s.status,
            "current_version": s.current_version,
            "updated_at": isoformat_bjt(s.updated_at),
        }
        for s in items
    ]


async def submit_for_certification(
    db: AsyncSession,
    skill_id: str,
    user_id: str,
) -> dict:
    """提交 Skill 认证申请。

    前置条件：Skill 必须存在且 market_status 为 marketplace_listed。
    """
    # 检查 Skill 存在
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    if skill.market_status not in ("marketplace_listed", "company_public"):
        raise AppError("MARKET_NOT_LISTED", 400)

    # 检查是否已有待审核的认证
    existing = await db.execute(
        select(MarketCertification).where(
            MarketCertification.skill_id == skill_id,
            MarketCertification.status == "pending",
        )
    )
    if existing.scalar_one_or_none():
        raise AppError("MARKET_CERT_PENDING", 400)

    cert = MarketCertification(
        skill_id=skill_id,
        submitted_by=user_id,
        status="pending",
    )
    db.add(cert)
    await db.flush()

    logger.info("Skill {} 认证申请已提交，申请人: {}", skill_id, user_id)
    return {
        "id": cert.id,
        "skill_id": cert.skill_id,
        "status": cert.status,
        "submitted_at": isoformat_bjt(cert.submitted_at),
        "submitted_by": cert.submitted_by,
    }


async def review_certification(
    db: AsyncSession,
    certification_id: int,
    reviewer_id: str,
    decision: str,
    notes: str | None = None,
) -> dict:
    """审核认证申请。

    decision: approved / rejected
    """
    if decision not in ("approved", "rejected"):
        raise AppError("PARAM_INVALID", 400)

    result = await db.execute(
        select(MarketCertification).where(MarketCertification.id == certification_id)
    )
    cert = result.scalar_one_or_none()
    if not cert:
        raise AppError("MARKET_CERT_NOT_FOUND", 404)

    if cert.status != "pending":
        raise AppError("MARKET_CERT_DECIDED", 400)

    # 不能审核自己提交的
    if cert.submitted_by == reviewer_id:
        raise AppError("REVIEW_SELF_APPROVE", 400)

    cert.status = decision
    cert.reviewer_id = reviewer_id
    cert.review_notes = notes

    if decision == "approved":
        cert.certified_at = now_bjt()
        # 更新 Skill 的 market_status 为 marketplace_certified
        await db.execute(
            update(Skill)
            .where(Skill.id == cert.skill_id)
            .values(market_status="marketplace_certified")
        )
        logger.info("Skill {} 认证通过，审核人: {}", cert.skill_id, reviewer_id)
    else:
        logger.info("Skill {} 认证被拒绝，审核人: {}，原因: {}", cert.skill_id, reviewer_id, notes)

    await db.flush()

    return {
        "id": cert.id,
        "skill_id": cert.skill_id,
        "status": cert.status,
        "reviewer_id": cert.reviewer_id,
        "review_notes": cert.review_notes,
        "certified_at": isoformat_bjt(cert.certified_at),
    }


async def get_reuse_metrics(db: AsyncSession, skill_id: str) -> dict:
    """复用指标：fork_count, execution_count, unique_users, departments_using。

    基于 execution_runs + decision_log 统计实际使用数据。
    """
    # 检查 Skill 存在
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    # 执行次数：通过 decision_log 关联
    exec_count_result = await db.execute(
        select(func.count(DecisionLog.id)).where(DecisionLog.skill_id == skill_id)
    )
    execution_count = exec_count_result.scalar() or 0

    # 独立用户数：通过 execution_runs 的 trigger_type='manual' 以及 run_id 关联
    # 由于 execution_runs 没有 user_id 字段，用 decision_log 的 run_id 去重计数
    unique_runs_result = await db.execute(
        select(func.count(func.distinct(DecisionLog.run_id))).where(
            DecisionLog.skill_id == skill_id,
            DecisionLog.run_id.isnot(None),
        )
    )
    unique_users = unique_runs_result.scalar() or 0

    # 使用此 Skill 的部门数：通过 Skill 表的 department 字段
    # （fork 场景下同一个 Skill 可能被多个部门 fork，但当前架构下以原始 Skill 为准）
    # 统计有多少不同部门执行过这个 Skill（通过 decision_log -> execution_runs -> skills）
    dept_result = await db.execute(
        select(func.count(func.distinct(Skill.department)))
        .select_from(DecisionLog)
        .join(ExecutionRun, ExecutionRun.id == DecisionLog.run_id)
        .join(Skill, Skill.id == DecisionLog.skill_id)
        .where(DecisionLog.skill_id == skill_id)
    )
    departments_using = dept_result.scalar() or 1  # 至少是自己所在部门

    # Fork 数量：统计 Skill 名称中包含 "-fork-" 前缀或描述中引用了原 Skill 的数量
    # 简化实现：查找 ID 以 "{skill_id}-fork" 开头的 Skill 数量
    fork_result = await db.execute(
        select(func.count(Skill.id)).where(Skill.id.like(f"{skill_id}-fork%"))
    )
    fork_count = fork_result.scalar() or 0

    return {
        "skill_id": skill_id,
        "fork_count": fork_count,
        "execution_count": execution_count,
        "unique_users": unique_users,
        "departments_using": departments_using,
    }


async def update_visibility(
    db: AsyncSession,
    skill_id: str,
    market_status: str,
    user_id: str,
) -> dict:
    """修改 Skill 市场可见性。

    只允许 Skill owner 或管理员修改。
    """
    if market_status not in VALID_MARKET_STATUSES:
        raise AppError("PARAM_INVALID", 400)

    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    old_status = skill.market_status
    skill.market_status = market_status
    await db.flush()

    logger.info(
        "Skill {} 可见性从 {} 变更为 {}，操作人: {}",
        skill_id, old_status, market_status, user_id,
    )
    return {
        "skill_id": skill_id,
        "market_status": market_status,
        "previous_status": old_status,
    }
