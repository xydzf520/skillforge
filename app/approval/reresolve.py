"""审批链重新解析（role-matrix-v2 §6.5 / §10）。

触发场景：
- 用户被禁用（disable_user）
- 用户角色被降权（update_user role→aibp/observer）
- 用户从 pending 激活（activate_pending_user，主要用于把新增的 dept_admin 加入）

重解析逻辑（approver 侧）：
- 定位受影响用户作为 assignee 的、未决审批 DecisionRequest（source_type='approval_step'）
- 针对每条请求，通过 approval_chain + approver_resolver 推算当前审批步骤的新候选
- 如果受影响用户已不在新候选列表：把其 pending AITodo 标记为 resolved_by_peer
  （借用已有状态机，语义是"本人无法再接手，由其他候选人接手"）
- 为新候选补齐 AITodo + Notification（调 _ensure_step_todos 复用幂等入队）
- 如果没有任何新候选：保留 todo 为 pending 但解绑接手人（实践上可能进入告警通道）

取消逻辑（requester 侧 §6.5 第二条）：
- 用户作为 requester 发起的未决审批实例 → 直接置 cancelled，级联 DR + 待办
  （见 `cancel_requester_approvals`）

只改 AITodo 与 ApprovalStep.approver_id，不改 DecisionRequest 的 aggregate_status
（仍 pending），也不改 ApprovalInstance.status（仍 pending）——上述仅针对 approver 侧。
requester 侧则会把实例、DR、待办全部置 cancelled。
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select, text
from sqlalchemy.exc import NoSuchTableError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.approval.models import ApprovalInstance, ApprovalRule, ApprovalStep
from app.common.audit import audit
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt
from app.todos.models import AITodo, DecisionRequest

__all__ = [
    "reresolve_affected_decision_requests",
    "cancel_requester_approvals",
]

_APPROVAL_SOURCE_TYPE = "approval_step"
# ApprovalInstance.status 运行中可能出现的值集合；目前代码只落 "pending"，
# 但 spec 预留 "in_progress"，这里一并防御。
_IN_FLIGHT_STATUSES: tuple[str, ...] = ("pending", "in_progress")


async def cancel_requester_approvals(
    db: AsyncSession,
    *,
    user_id: str,
) -> list[str]:
    """role-matrix-v2 §6.5 第二条：把 user_id 发起的、仍未决的审批实例置为 cancelled。

    作用：当 requester 本人被禁用时，其发起的未决审批应立刻作废，避免审批人
    继续审批一个"提交人已离场"的请求。

    具体动作：
    - 把每个 `ApprovalInstance.status in {pending, in_progress}` 且 requester_id==user_id
      的实例 `status` 置 `cancelled`、`completed_at=now`、`cancelled_reason=\"requester_disabled\"`
      与 `cancelled_at=now` 写入专属列；同时在 `payload` 里双写一份以兼容旧查询
      （`payload ->> 'cancelled_reason'`）。
    - 对该实例关联的 approval_step 类 `DecisionRequest`（`aggregate_status in {pending}`）
      级联置 `aggregate_status='cancelled'`、`completed_at=now`。
    - 对这些 DR 下仍为 `pending` 的 `AITodo` 置 `status='cancelled'`、
      `decision_channel='reresolve'`、`decision_reason='requester_disabled'`。
    - 每取消一条实例写一条 audit.log（`approval.cancel_on_requester_disabled`）。

    Returns:
        被取消的 ApprovalInstance id 列表（不含已是终态的实例）。
    """
    rows = (
        await db.execute(
            select(ApprovalInstance).where(
                ApprovalInstance.requester_id == user_id,
                ApprovalInstance.status.in_(_IN_FLIGHT_STATUSES),
            )
        )
    ).scalars().all()

    cancelled_ids: list[str] = []
    if not rows:
        return cancelled_ids

    now = now_bjt()

    for instance in rows:
        original_status = instance.status
        # 1) 置实例 cancelled；cancelled_reason / cancelled_at 写专属列（真源），
        # 同时保留 payload 双写以兼容旧查询（`payload ->> 'cancelled_reason'`）。
        instance.status = "cancelled"
        instance.completed_at = now
        instance.cancelled_reason = "requester_disabled"
        instance.cancelled_at = now
        payload = dict(instance.payload) if isinstance(instance.payload, dict) else {}
        payload["cancelled_reason"] = "requester_disabled"
        payload["cancelled_at"] = isoformat_bjt(now)
        instance.payload = payload

        # 2) 级联所有 approval_step 类 DR 到 cancelled（仅处理仍 pending 的）。
        source_ids_prefix = f"{instance.id}:"
        drs = (
            await db.execute(
                select(DecisionRequest).where(
                    DecisionRequest.source_type == _APPROVAL_SOURCE_TYPE,
                    DecisionRequest.source_id.like(f"{source_ids_prefix}%"),
                    DecisionRequest.aggregate_status == "pending",
                )
            )
        ).scalars().all()

        cancelled_todo_count = 0
        for dr in drs:
            dr.aggregate_status = "cancelled"
            dr.completed_at = now

            # 3) 级联 pending AITodo 到 cancelled。
            todos = (
                await db.execute(
                    select(AITodo).where(
                        AITodo.request_id == dr.id,
                        AITodo.status == "pending",
                    )
                )
            ).scalars().all()
            for todo in todos:
                todo.status = "cancelled"
                todo.decision_channel = "reresolve"
                todo.decision_reason = "requester_disabled"
                todo.decided_at = now
                cancelled_todo_count += 1

        cancelled_ids.append(instance.id)

        await audit.log(
            "svc_approval",
            "approval.cancel_on_requester_disabled",
            "approval_instance",
            instance.id,
            detail={
                "requester_id": user_id,
                "previous_status": original_status,
                "cancelled_requests": len(drs),
                "cancelled_todos": cancelled_todo_count,
                "business_type": instance.business_type,
                "business_id": instance.business_id,
            },
        )

    await db.flush()
    return cancelled_ids


async def reresolve_affected_decision_requests(
    db: AsyncSession,
    user_id: str,
) -> dict[str, int]:
    """重新解析所有与 user_id 相关的、未决的审批决策请求。

    role-matrix-v2 §6.5 统一入口：本函数同时承担两件事——
    1. 若 user_id 是尚未决审批的 requester，把这些实例级联作废（cancelled）；
    2. 若 user_id 是尚未决审批某步的 approver 候选，重新解析新候选。

    这样 `disable_user` / `update_user` 只需调一次就能覆盖 §6.5 两条子款。

    Returns:
        {"scanned": N, "updated": M, "released": K, "cancelled_as_requester": C}
        - scanned: 匹配到的 approver 侧待办数量
        - updated: 成功重新分派了新接手人的请求数
        - released: 将 user_id 的 todo 标记为 resolved_by_peer 的数量
        - cancelled_as_requester: 作为 requester 被级联取消的实例数
    """
    # 延迟引入避免循环依赖
    from app.approval.service import _ensure_step_todos, _resolve_step_approvers

    # v2.0.16 H5：收口到 app/common/advisory_lock::acquire_user_lock；key 命名空间
    # 统一为 "user:{user_id}"，与 users/service.disable_user 共享 lock，保证同一 user
    # 的 disable / update / reresolve 三种写操作串行化，避免 TOCTOU。
    # SQLite / 无 pg_advisory_xact_lock 的轻量夹具自动降级到"无锁"。
    from app.common.advisory_lock import acquire_user_lock

    await acquire_user_lock(db, user_id)

    # 先跑 requester 侧：把 user_id 发起的未决审批作废。必须先于 approver 侧，
    # 否则若 user_id 同时是某步 approver 候选，approver 侧再去解析这些已作废
    # 的步骤就做了无用功。
    #
    # 用 savepoint 包一层：某些轻量测试夹具不建 approval_instances 表，这种场景
    # 只需回滚 savepoint 继续跑 approver 侧。生产/集成环境表必然存在，此 try 仅
    # 捕获"表缺失"类的 DB schema 异常，不吞任何业务错误——业务错误冒泡到外层。
    cancelled_as_requester: list[str] = []
    try:
        async with db.begin_nested():
            cancelled_as_requester = await cancel_requester_approvals(
                db, user_id=user_id
            )
    except (ProgrammingError, NoSuchTableError) as exc:
        logger.warning(
            "approval.reresolve cancel_requester skipped (schema missing) user_id={} error={}",
            user_id,
            exc,
        )
        cancelled_as_requester = []

    scanned = 0
    updated = 0
    released = 0

    # 找到 user_id 还挂着 pending 的、approval_step 类型的 DecisionRequest
    rows = (
        await db.execute(
            select(AITodo, DecisionRequest)
            .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
            .where(
                AITodo.assignee == user_id,
                AITodo.status == "pending",
                DecisionRequest.source_type == _APPROVAL_SOURCE_TYPE,
                DecisionRequest.aggregate_status == "pending",
            )
        )
    ).all()

    for todo, request in rows:
        scanned += 1
        payload = request.payload if isinstance(request.payload, dict) else {}
        instance_id = payload.get("instance_id")
        step_order = payload.get("step_order")
        if not instance_id or not isinstance(step_order, int):
            logger.warning(
                "approval.reresolve missing instance/step request_id={} payload={}",
                request.id,
                payload,
            )
            continue

        instance = await db.get(ApprovalInstance, instance_id)
        if instance is None or instance.status != "pending":
            continue
        step = (
            await db.execute(
                select(ApprovalStep)
                .where(ApprovalStep.instance_id == instance_id)
                .where(ApprovalStep.step_order == step_order)
            )
        ).scalar_one_or_none()
        if step is None or step.status != "pending":
            continue

        rule = (
            await db.get(ApprovalRule, instance.rule_id) if instance.rule_id else None
        )
        if rule is None or not isinstance(rule.approval_chain, list):
            continue
        if len(rule.approval_chain) < step_order:
            continue
        chain_step = rule.approval_chain[step_order - 1]
        if not isinstance(chain_step, dict):
            continue

        # M2：静态 approver_id 链路 + 该 approver 已 disable → audit + 钉钉告警 admin。
        # 旧逻辑只 continue，配合 disable_user 的 successor 转派"approval_step 类排除"
        # 子句，导致此 todo 既无新候选也不会被转派 → 卡死无人处理。
        # 这里不强行修改 chain_step / step.approver_id（运维需要保留原始静态人），
        # 只输出告警。todo 的 stuck_reason 字段当前 schema 没有，先用 audit + 钉钉。
        if chain_step.get("approver_id") and "type" not in chain_step:
            static_approver = chain_step.get("approver_id")
            if static_approver == user_id:
                await audit.log(
                    "svc_approval",
                    "approval.static_approver_disabled_stuck",
                    "approval_instance",
                    instance.id,
                    detail={
                        "trigger_user_id": user_id,
                        "instance_id": instance.id,
                        "step_order": step_order,
                        "static_approver_id": static_approver,
                        "chain_step": chain_step,
                        "business_type": instance.business_type,
                        "business_id": instance.business_id,
                        "priority": "high",
                    },
                )
                # 钉钉告警 system_admin（priority=2 = 任务级，避免噪声但需立即处理）
                try:
                    from app.dingtalk.outbox import DingTalkOutboxService

                    outbox = DingTalkOutboxService()
                    await outbox.enqueue(
                        # message_type 字段 varchar(20)，用简短标识
                        message_type="approval_stuck",
                        recipient="system_admin",
                        payload={
                            "title": "审批卡死告警：静态审批人已禁用",
                            "instance_id": instance.id,
                            "business_type": instance.business_type,
                            "business_id": instance.business_id,
                            "static_approver_id": static_approver,
                            "step_order": step_order,
                            "hint": "请运维介入：把静态 approver 替换为新人或人工 cancel 该实例。",
                        },
                        priority=2,
                        related_type="approval_instance",
                        related_id=instance.id,
                        session=db,
                    )
                except (KeyboardInterrupt,):
                    raise
                except Exception as exc:  # noqa: BLE001
                    # 钉钉入队失败仅 warning（audit 已落盘，主路径不阻断）
                    logger.warning(
                        "approval.static_approver_stuck dingtalk_enqueue failed instance={} error={}",
                        instance.id,
                        exc,
                    )
            continue

        try:
            new_approvers = await _resolve_step_approvers(
                db,
                instance=instance,
                chain_step=chain_step,
            )
        except AppError as exc:
            logger.warning(
                "approval.reresolve resolve_failed request_id={} code={} detail={}",
                request.id,
                exc.code,
                exc.detail,
            )
            continue

        new_approvers_set = set(new_approvers)
        previous_approver_id = step.approver_id

        # 第一步：如果 user_id 已不在新候选中，把其 pending todo 标记为 resolved_by_peer
        released_this_row = False
        if user_id not in new_approvers_set:
            todo.status = "resolved_by_peer"
            todo.decision_channel = "reresolve"
            released += 1
            released_this_row = True

        # 第二步：为新候选补 pending todo（_ensure_step_todos 幂等）
        assigned_this_row = False
        if new_approvers:
            await _ensure_step_todos(
                db,
                instance=instance,
                step=step,
                approvers=list(new_approvers_set),
            )
            if len(new_approvers) == 1:
                step.approver_id = new_approvers[0]
            else:
                step.approver_id = None
            updated += 1
            assigned_this_row = True
        else:
            # 无候选：清掉单 approver 指针（后续审批人由运维补齐）
            step.approver_id = None

        # role-matrix-v2 §6.5 审计：approver 侧重分派全量可追溯
        if released_this_row or assigned_this_row or previous_approver_id != step.approver_id:
            await audit.log(
                "svc_approval",
                "approval.reresolve_on_approver_change",
                "approval_step",
                f"{instance.id}:{step_order}",
                detail={
                    "trigger_user_id": user_id,
                    "instance_id": instance.id,
                    "step_order": step_order,
                    "released_trigger_user": released_this_row,
                    "new_approvers": sorted(new_approvers_set),
                    "previous_approver_id": previous_approver_id,
                    "new_approver_id": step.approver_id,
                    "business_type": instance.business_type,
                    "business_id": instance.business_id,
                },
            )

    await db.flush()
    return {
        "scanned": scanned,
        "updated": updated,
        "released": released,
        "cancelled_as_requester": len(cancelled_as_requester),
    }
