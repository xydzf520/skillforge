"""审批规则与实例服务。

职责：
- 审批规则 CRUD（approval_rules 表）
- 审批实例创建 / 推进（approval_instances + approval_steps）
- 钉钉审批流联通（可选，需配置 dingtalk_user_id + 审批模板 code）

与 `condition_engine` 的分工：本模块不做条件求值，委托给 evaluate_condition。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approval.approver_resolver import resolve_approvers
from app.approval.condition_engine import evaluate_condition
from app.approval.models import ApprovalInstance, ApprovalRule, ApprovalStep
from app.auth.models import User
from app.common.exceptions import AppError
from app.config import settings
from app.dingtalk.client import dingtalk_client  # 模块属性，供测试 patch
from app.todos.models import AITodo, DecisionRequest
from app.common.time_utils import isoformat_bjt, now_bjt

__all__ = [
    "dingtalk_client",
    "list_rules",
    "create_rule",
    "update_rule",
    "create_instance",
    "get_instance",
    "advance_instance",
    "_rule_matches",
    "_ensure_step_todos",
    "_resolve_step_approvers",
]


def _rule_matches(condition_json: dict | None, payload: dict | None) -> bool:
    """条件匹配：condition_json 为空视为无限制。"""
    return evaluate_condition(condition_json, payload or {})


def _rule_to_dict(rule: ApprovalRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "business_type": rule.business_type,
        "condition_json": rule.condition_json,
        "approval_chain": rule.approval_chain,
        "enabled": rule.enabled,
        "priority": rule.priority,
        "created_at": isoformat_bjt(rule.created_at),
        "updated_at": isoformat_bjt(rule.updated_at),
    }


def _step_to_dict(step: ApprovalStep) -> dict[str, Any]:
    return {
        "id": step.id,
        "instance_id": step.instance_id,
        "step_order": step.step_order,
        "approver_id": step.approver_id,
        "status": step.status,
        "comment": step.comment,
        "acted_at": isoformat_bjt(step.acted_at),
    }


def _instance_to_dict(instance: ApprovalInstance, steps: list[ApprovalStep]) -> dict[str, Any]:
    return {
        "id": instance.id,
        "rule_id": instance.rule_id,
        "business_type": instance.business_type,
        "business_id": instance.business_id,
        "status": instance.status,
        "current_step": instance.current_step,
        "dingtalk_process_id": instance.dingtalk_process_id,
        "requester_id": instance.requester_id,
        "payload": instance.payload,
        "created_at": isoformat_bjt(instance.created_at),
        "completed_at": isoformat_bjt(instance.completed_at),
        "steps": [_step_to_dict(s) for s in sorted(steps, key=lambda x: x.step_order)],
    }


async def list_rules(
    db: AsyncSession,
    *,
    business_type: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    stmt = select(ApprovalRule)
    if business_type:
        stmt = stmt.where(ApprovalRule.business_type == business_type)
    if enabled is not None:
        stmt = stmt.where(ApprovalRule.enabled == enabled)
    stmt = stmt.order_by(ApprovalRule.priority.desc(), ApprovalRule.created_at.desc())
    rules = (await db.execute(stmt)).scalars().all()
    return {"total": len(rules), "items": [_rule_to_dict(r) for r in rules]}


def _validate_approval_chain(approval_chain: list | None) -> None:
    """校验审批链 approver_id 不重复（避免"第 1 步 A 批了第 2 步又转给 A"空转）。"""
    if not approval_chain:
        return
    seen: set[str] = set()
    for idx, entry in enumerate(approval_chain, start=1):
        if not isinstance(entry, dict):
            continue
        aid = entry.get("approver_id")
        if not aid:
            continue
        if aid in seen:
            raise AppError(
                "APPROVAL_CHAIN_DUPLICATE",
                400,
                {"detail": f"第 {idx} 步审批人 {aid} 在链上已出现", "approver_id": aid},
            )
        seen.add(aid)


async def create_rule(
    db: AsyncSession,
    *,
    rule_id: str,
    business_type: str,
    condition_json: dict | None,
    approval_chain: list | None,
    enabled: bool = True,
    priority: int = 0,
) -> dict[str, Any]:
    existing = await db.get(ApprovalRule, rule_id)
    if existing is not None:
        raise AppError("APPROVAL_RULE_EXISTS", 409)
    _validate_approval_chain(approval_chain)
    rule = ApprovalRule(
        id=rule_id,
        business_type=business_type,
        condition_json=condition_json,
        approval_chain=approval_chain,
        enabled=enabled,
        priority=priority,
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return _rule_to_dict(rule)


async def update_rule(
    db: AsyncSession,
    *,
    rule_id: str,
    business_type: str | None = None,
    condition_json: dict | None = None,
    approval_chain: list | None = None,
    enabled: bool | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    rule = await db.get(ApprovalRule, rule_id)
    if rule is None:
        raise AppError("APPROVAL_RULE_NOT_FOUND", 404)
    if business_type is not None:
        rule.business_type = business_type
    if condition_json is not None:
        rule.condition_json = condition_json
    if approval_chain is not None:
        _validate_approval_chain(approval_chain)
        rule.approval_chain = approval_chain
    if enabled is not None:
        rule.enabled = enabled
    if priority is not None:
        rule.priority = priority
    rule.updated_at = now_bjt()
    await db.flush()
    await db.refresh(rule)
    return _rule_to_dict(rule)


async def _match_rule(
    db: AsyncSession,
    *,
    business_type: str,
    payload: dict | None,
) -> ApprovalRule | None:
    """按 priority desc, created_at desc 找首条匹配的启用规则。"""
    rules = (await db.execute(
        select(ApprovalRule)
        .where(ApprovalRule.business_type == business_type)
        .where(ApprovalRule.enabled.is_(True))
        .order_by(ApprovalRule.priority.desc(), ApprovalRule.created_at.desc())
    )).scalars().all()
    for rule in rules:
        if _rule_matches(rule.condition_json, payload):
            return rule
    return None


async def _try_create_dingtalk_process(
    db: AsyncSession,
    *,
    instance: ApprovalInstance,
    requester_id: str,
    first_approver_id: str | None,
) -> str | None:
    """尝试创建钉钉审批流。任一条件缺失 / 调用失败都静默返回 None（回退到平台内审批）。"""
    process_code = getattr(settings, "DINGTALK_APPROVAL_PROCESS_CODE_L2", "") or ""
    if not process_code:
        return None
    requester = await db.get(User, requester_id)
    originator = getattr(requester, "dingtalk_user_id", None) if requester else None
    if not originator:
        return None
    approver = await db.get(User, first_approver_id) if first_approver_id else None
    approver_dt = getattr(approver, "dingtalk_user_id", None) if approver else None
    if not approver_dt:
        return None
    form_data = [
        {"name": "业务类型", "value": instance.business_type},
        {"name": "业务单据", "value": instance.business_id},
    ]
    try:
        result = await dingtalk_client.create_approval(
            process_code=process_code,
            originator_id=originator,
            approver_id=approver_dt,
            form_data=form_data,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("approval.create_dingtalk_process 异常: {}", exc)
        return None
    if not isinstance(result, dict) or not result.get("ok"):
        return None
    return result.get("instance_id")


async def create_instance(
    db: AsyncSession,
    *,
    business_type: str,
    business_id: str,
    requester_id: str,
    payload: dict | None = None,
    dingtalk_process_id: str | None = None,
    create_dingtalk: bool = True,
) -> dict[str, Any]:
    rule = await _match_rule(db, business_type=business_type, payload=payload)
    # 防自批：requester 不能出现在审批链上
    if rule and rule.approval_chain:
        for entry in rule.approval_chain:
            if isinstance(entry, dict) and entry.get("approver_id") == requester_id:
                raise AppError(
                    "APPROVAL_REQUESTER_IN_CHAIN",
                    400,
                    {"detail": "发起人不能同时是审批人", "requester_id": requester_id},
                )
    instance = ApprovalInstance(
        id=f"ai-{uuid4().hex[:16]}",
        rule_id=rule.id if rule else None,
        business_type=business_type,
        business_id=business_id,
        requester_id=requester_id,
        status="pending",
        current_step=1,
        payload=payload,
        dingtalk_process_id=dingtalk_process_id,
    )
    db.add(instance)
    await db.flush()

    steps: list[ApprovalStep] = []
    if rule and rule.approval_chain:
        for idx, entry in enumerate(rule.approval_chain, start=1):
            approver_id = entry.get("approver_id") if isinstance(entry, dict) else None
            step = ApprovalStep(
                id=f"as-{uuid4().hex[:16]}",
                instance_id=instance.id,
                step_order=idx,
                approver_id=approver_id,
                status="pending",
            )
            db.add(step)
            steps.append(step)
        await db.flush()

    if create_dingtalk and dingtalk_process_id is None and steps:
        dt_id = await _try_create_dingtalk_process(
            db,
            instance=instance,
            requester_id=requester_id,
            first_approver_id=steps[0].approver_id,
        )
        if dt_id:
            instance.dingtalk_process_id = dt_id
            await db.flush()

    await db.refresh(instance)
    for step in steps:
        await db.refresh(step)
    return _instance_to_dict(instance, steps)


ADMIN_ROLES: frozenset[str] = frozenset({"admin", "ai_engineer"})


async def get_instance(
    db: AsyncSession,
    instance_id: str,
    *,
    current_user: User | None = None,
) -> dict[str, Any]:
    instance = await db.get(ApprovalInstance, instance_id)
    if instance is None:
        raise AppError("APPROVAL_INSTANCE_NOT_FOUND", 404)
    steps = (await db.execute(
        select(ApprovalStep).where(ApprovalStep.instance_id == instance_id)
    )).scalars().all()
    # 访问控制：非 admin 用户必须是 requester 或 审批链上的某位 approver
    if current_user is not None and current_user.role not in ADMIN_ROLES:
        approvers = {s.approver_id for s in steps if s.approver_id}
        if current_user.id != instance.requester_id and current_user.id not in approvers:
            raise AppError("APPROVAL_ACCESS_DENIED", 403)
    return _instance_to_dict(instance, list(steps))


async def advance_instance(
    db: AsyncSession,
    *,
    instance_id: str,
    step_order: int,
    approver_id: str,
    decision: str,
    comment: str = "",
) -> dict[str, Any]:
    """推进审批步骤。被 HTTP 路由与 dingtalk 回调共同调用。"""
    if decision not in ("approved", "rejected"):
        raise AppError("APPROVAL_DECISION_INVALID", 400)

    instance = await db.get(ApprovalInstance, instance_id)
    if instance is None:
        raise AppError("APPROVAL_INSTANCE_NOT_FOUND", 404)
    if instance.status != "pending":
        raise AppError("APPROVAL_INSTANCE_CLOSED", 400)

    step = (await db.execute(
        select(ApprovalStep)
        .where(ApprovalStep.instance_id == instance_id)
        .where(ApprovalStep.step_order == step_order)
    )).scalar_one_or_none()
    if step is None:
        raise AppError("APPROVAL_STEP_NOT_FOUND", 404)
    if step.status != "pending":
        raise AppError("APPROVAL_STEP_CLOSED", 400)

    # 身份校验：approver_id 必须匹配 step 记录的审批人；钉钉回调侧传入 step.approver_id 自然匹配。
    if not step.approver_id:
        raise AppError("APPROVAL_STEP_APPROVER_UNRESOLVED", 500)
    if step.approver_id != approver_id:
        raise AppError("APPROVAL_NOT_YOUR_STEP", 403)
    # 防止发起人自批
    if instance.requester_id == approver_id:
        raise AppError("APPROVAL_SELF_APPROVE_FORBIDDEN", 403)

    step.status = decision
    step.comment = comment
    step.acted_at = now_bjt()

    if decision == "rejected":
        instance.status = "rejected"
        instance.completed_at = now_bjt()
    else:
        remaining = (await db.execute(
            select(ApprovalStep)
            .where(ApprovalStep.instance_id == instance_id)
            .where(ApprovalStep.status == "pending")
            .where(ApprovalStep.step_order > step_order)
            .order_by(ApprovalStep.step_order.asc())
        )).scalars().all()
        if remaining:
            instance.current_step = remaining[0].step_order
        else:
            instance.status = "approved"
            instance.completed_at = now_bjt()

    await db.flush()
    steps = (await db.execute(
        select(ApprovalStep).where(ApprovalStep.instance_id == instance_id)
    )).scalars().all()
    return _instance_to_dict(instance, list(steps))


# =============================================================================
# role-matrix-v2 §6.5：权限变更后的审批链重解析支持函数
#
# 这两个函数被 app/approval/reresolve.py 调用；拆到 service.py 避免循环依赖。
# =============================================================================


async def _resolve_step_approvers(
    db: AsyncSession,
    *,
    instance: ApprovalInstance,
    chain_step: dict,
) -> list[str]:
    """基于当前 instance + chain_step 重新解析 approvers。

    context 优先从 instance.payload 取 skill 部门 / requester / skill_id 等字段。
    """
    payload = instance.payload if isinstance(instance.payload, dict) else {}
    context = {
        "requester_id": instance.requester_id,
        "skill_id": payload.get("skill_id") or instance.business_id,
        "skill_department": payload.get("skill_department")
            or payload.get("department_id")
            or payload.get("department"),
        "org_unit_id": payload.get("org_unit_id"),
    }
    return await resolve_approvers(db, chain_step, context)


async def _ensure_step_todos(
    db: AsyncSession,
    *,
    instance: ApprovalInstance,
    step: ApprovalStep,
    approvers: list[str],
) -> None:
    """幂等地为审批步骤创建 AITodo（每个 approver 一条 pending）。

    role-matrix-v2 §6.3：N 个 dept_admin 各一条 todo，任一人审批即关闭。
    需要先有 DecisionRequest (source_type='approval_step', source_id='<instance_id>:<step_order>')。
    如果 DecisionRequest 不存在，自动按幂等方式创建一条兜底。
    """
    if not approvers:
        return

    source_id = f"{instance.id}:{step.step_order}"
    dr_stmt = (
        select(DecisionRequest)
        .where(DecisionRequest.source_type == "approval_step")
        .where(DecisionRequest.source_id == source_id)
    )
    request = (await db.execute(dr_stmt)).scalars().first()

    if request is None:
        # 容错：审批 instance 创建时如果没建 DecisionRequest，这里补建。
        # SLA 默认 24h（create_instance 后续接入可改为按 rule 配置）。
        from datetime import timedelta

        request = DecisionRequest(
            id=f"dr-approval-{instance.id}-{step.step_order}",
            source_type="approval_step",
            source_id=source_id,
            skill_id=instance.business_id,
            kind="review",
            title=f"{instance.business_type} 审批",
            summary=f"待审批（step {step.step_order}）",
            payload={
                "instance_id": instance.id,
                "step_order": step.step_order,
                "business_type": instance.business_type,
                "business_id": instance.business_id,
            },
            decision_mode="any_of",
            aggregate_status="pending",
            sla_at=now_bjt() + timedelta(hours=24),
        )
        db.add(request)
        await db.flush()

    # 查已有的 AITodo，按 assignee 去重
    existing_rows = (
        await db.execute(
            select(AITodo).where(AITodo.request_id == request.id)
        )
    ).scalars().all()
    existing_by_assignee = {t.assignee: t for t in existing_rows}

    for assignee in approvers:
        todo = existing_by_assignee.get(assignee)
        if todo is None:
            db.add(
                AITodo(
                    request_id=request.id,
                    kind="review",
                    assignee=assignee,
                    status="pending",
                )
            )
            continue
        # 已有但之前被标记成 resolved_by_peer / expired：不复活（维持语义）
        # 保持现状即可

    await db.flush()
