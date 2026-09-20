"""
钉钉审批流管理：创建L2/L3审批实例 + 监听审批结果。
支持两种场景：
1. Skill变更审核（关联Review表）
2. 执行结果审批（关联DecisionLog表）
"""

from loguru import logger

from app.config import settings
from app.dingtalk.client import dingtalk_client


async def _resolve_originator(submitter_dingtalk_id: str) -> str:
    """
    解析审批发起人钉钉ID。
    如果传入的ID有效则直接使用，否则查找第一个有钉钉ID的 active system_admin / admin 作为 fallback。

    v2.0.15 C2：
    - 收口到 v2 主角色 ``system_admin`` 与 legacy ``admin`` 两档
    - 强制 ``state == 'active'``——``state='pending'`` 钉钉首登未激活、``state='disabled'``
      管理员被禁用，都不能被当成"审批发起人"
    """
    if submitter_dingtalk_id:
        return submitter_dingtalk_id

    try:
        from sqlalchemy import select
        from app.auth.models import User
        from app.database import async_session_factory

        async with async_session_factory() as session:
            user = (await session.execute(
                select(User)
                .where(User.role.in_(("system_admin", "admin")))
                .where(User.state == "active")
                .where(User.is_active == True)  # noqa: E712
                .where(User.dingtalk_user_id.isnot(None))
                .where(User.dingtalk_user_id != "")
                .order_by(
                    # 优先 v2 system_admin，legacy admin 次之
                    (User.role == "system_admin").desc(),
                    User.id.asc(),
                )
                .limit(1)
            )).scalar_one_or_none()
            if user:
                return user.dingtalk_user_id
    except Exception as e:
        logger.warning(f"查询 admin 钉钉ID失败: {e}")

    logger.error("无法获取有效的审批发起人钉钉ID")
    return ""


async def create_l2_approval(
    skill_id: str,
    submitter_name: str,
    change_summary: str,
    approver_dingtalk_id: str,
    submitter_dingtalk_id: str = "",
) -> dict:
    """创建L2审批（业务负责人审批）"""
    process_code = settings.DINGTALK_APPROVAL_PROCESS_CODE_L2
    if not process_code:
        logger.warning("L2审批模板code未配置")
        return {"ok": False, "error": "审批模板未配置"}

    originator = await _resolve_originator(submitter_dingtalk_id)
    if not originator:
        return {"ok": False, "error": "审批发起人钉钉ID不可用"}

    form_data = [
        {"name": "Skill", "value": skill_id},
        {"name": "提交人", "value": submitter_name},
        {"name": "变更内容", "value": change_summary},
    ]

    result = await dingtalk_client.create_approval(
        process_code=process_code,
        originator_id=originator,
        approver_id=approver_dingtalk_id,
        form_data=form_data,
    )

    if result.get("ok"):
        logger.info(f"L2审批创建成功: skill={skill_id} instance={result.get('instance_id')}")
    else:
        logger.error(f"L2审批创建失败: {result}")

    return result


async def create_l3_approval(
    skill_id: str,
    submitter_name: str,
    change_summary: str,
    approver_dingtalk_id: str,
    submitter_dingtalk_id: str = "",
) -> dict:
    """创建L3审批（董事长审批）"""
    process_code = settings.DINGTALK_APPROVAL_PROCESS_CODE_L3
    if not process_code:
        logger.warning("L3审批模板code未配置")
        return {"ok": False, "error": "审批模板未配置"}

    originator = await _resolve_originator(submitter_dingtalk_id)
    if not originator:
        return {"ok": False, "error": "审批发起人钉钉ID不可用"}

    form_data = [
        {"name": "Skill", "value": skill_id},
        {"name": "提交人", "value": submitter_name},
        {"name": "变更内容", "value": change_summary},
        {"name": "风险说明", "value": "高风险操作，需要董事长审批"},
    ]

    result = await dingtalk_client.create_approval(
        process_code=process_code,
        originator_id=originator,
        approver_id=approver_dingtalk_id,
        form_data=form_data,
    )

    return result


async def handle_approval_result(
    process_instance_id: str,
    result: str,
) -> None:
    """
    处理审批回调结果。
    result: "agree" / "refuse"
    由dingtalk/router.py中的回调处理函数调用。
    支持两种关联：Review（Skill变更审核）和 DecisionLog（执行结果审批）。
    """
    from sqlalchemy import select, update
    from app.approval.models import ApprovalInstance, ApprovalStep
    from app.approval.service import advance_instance
    from app.reviews.models import Review
    from app.execution.models import DecisionLog
    from app.reviews import service as review_service
    from app.common.audit import audit

    logger.info(f"审批结果: instance={process_instance_id} result={result}")

    def _sf():
        from app.database import async_session_factory
        return async_session_factory

    # 0. 优先处理 approval_instances（M2 审批配置模块）
    async with _sf()() as session:
        instance = (await session.execute(
            select(ApprovalInstance)
            .where(ApprovalInstance.dingtalk_process_id == process_instance_id)
            .where(ApprovalInstance.status == "pending")
        )).scalar_one_or_none()
        if instance:
            step = (await session.execute(
                select(ApprovalStep)
                .where(ApprovalStep.instance_id == instance.id)
                .where(ApprovalStep.status == "pending")
                .order_by(ApprovalStep.step_order.asc())
                .limit(1)
            )).scalar_one_or_none()
            if step:
                decision = "approved" if result == "agree" else "rejected"
                await advance_instance(
                    session,
                    instance_id=instance.id,
                    step_order=step.step_order,
                    approver_id=step.approver_id or "dingtalk_approval",
                    decision=decision,
                    comment="via dingtalk callback",
                )
                await session.commit()
                await audit.log(
                    "dingtalk",
                    f"approval_instance.{result}",
                    "approval_instance",
                    instance.id,
                    detail={"process_instance_id": process_instance_id},
                )
                logger.info(f"审批回调处理完成(ApprovalInstance): instance_id={instance.id} result={result}")
                return

    # 1. 先尝试查找关联的Review（Skill变更审核）
    async with _sf()() as session:
        review = (await session.execute(
            select(Review)
            .where(Review.dingtalk_msg_id == process_instance_id)
            .where(Review.status == "pending")
        )).scalar_one_or_none()

        if review:
            review_id = review.id
            skill_id = review.skill_id

            if result == "agree":
                await review_service.approve_review(session, review_id, "dingtalk_approval")
            elif result == "refuse":
                await review_service.reject_review(session, review_id, "dingtalk_approval",
                                                   reason="钉钉审批驳回")
            await session.commit()

            await audit.log("dingtalk", f"approval.{result}", "review", str(review_id),
                            detail={"process_instance_id": process_instance_id, "skill_id": skill_id})
            logger.info(f"审批回调处理完成(Review): review_id={review_id} result={result}")
            return

    # 2. 未找到Review，查找关联的DecisionLog（执行结果审批）
    async with _sf()() as session:
        decision = (await session.execute(
            select(DecisionLog)
            .where(DecisionLog.dingtalk_msg_id == process_instance_id)
            .where(DecisionLog.approval_status == "pending")
        )).scalar_one_or_none()

        if decision:
            approval_status = "approved" if result == "agree" else "rejected"
            await session.execute(
                update(DecisionLog)
                .where(DecisionLog.id == decision.id)
                .values(
                    approval_status=approval_status,
                    approver="dingtalk_approval",
                    user_action="completed" if result == "agree" else "rejected",
                )
            )
            await session.commit()

            await audit.log("dingtalk", f"execution_approval.{result}", "decision_log",
                            str(decision.id),
                            detail={"process_instance_id": process_instance_id,
                                    "skill_id": decision.skill_id,
                                    "run_id": decision.run_id})
            logger.info(f"审批回调处理完成(Execution): decision_id={decision.id} result={result}")
            return

    logger.warning(f"未找到审批关联的Review或DecisionLog: instance={process_instance_id}")
