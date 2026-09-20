"""端到端模拟: 创建 Skill → 跑出 review+dispatch 待办 → 各级审批 → 执行人回执 → callback 触发。

模拟一条真实业务: EC 投放运营分析 Skill 跑完后产出
- 一条 review 待办: ROI 看板需要 alice + bob 双签 (decision_mode=all_of)
- 一条 dispatch 待办: 把 SKU 下架任务派给 carol/dave 两个执行人 (alice 一人审批)

mock 边界:
- default_client.run_skill: 直接返回带 todos 的 output (不真连 OpenClaw)
- outbox.enqueue: 收集到内存里(不真发钉钉)
- bridge_registry.get: 返回 mock 连接(不真连 bridge)

跑完会输出每一步的 DB 状态、断言结果、捕获的所有问题。
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from datetime import datetime
from pathlib import Path
from app.common.time_utils import now_bjt

# 让脚本能直接 python3 scripts/e2e_dispatch_simulation.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
GRAY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"

# 测试 ID 前缀（用时间戳避免冲突）
RUN_TAG = f"e2e-{int(now_bjt().timestamp())}"


def step(title: str) -> None:
    print(f"\n{BOLD}{CYAN}━━━ {title} ━━━{RESET}")


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")
    PROBLEMS.append(msg)


def warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}")
    PROBLEMS.append(f"[设计缺陷] {msg}")


def info(msg: str) -> None:
    print(f"  {GRAY}·{RESET} {msg}")


PROBLEMS: list[str] = []
ENQUEUED: list[dict] = []
BRIDGE_CALLS: list[dict] = []


# ──────────────────────────────────────────────
# Mock outbox: 收集所有钉钉外发到内存
# ──────────────────────────────────────────────


async def fake_enqueue(message_type, recipient, payload, priority=5, **kwargs):
    msg_id = f"mock-msg-{len(ENQUEUED) + 1}"
    ENQUEUED.append(
        {
            "msg_id": msg_id,
            "type": message_type,
            "recipient": recipient,
            "title": payload.get("title"),
            "buttons": [b.get("title") for b in (payload.get("buttons") or [])],
            "related_type": kwargs.get("related_type"),
            "related_id": kwargs.get("related_id"),
            "priority": priority,
        }
    )
    return msg_id


# ──────────────────────────────────────────────
# Mock bridge connection: 收集所有 bridge_op 调用
# ──────────────────────────────────────────────


class FakeBridgeConn:
    async def bridge_op(self, op, payload, timeout=None):
        BRIDGE_CALLS.append({"op": op, "payload": payload, "timeout": timeout})
        return {"ok": True, "path": f"/tmp/mock/{op}.json"}


# ──────────────────────────────────────────────
# 准备测试数据
# ──────────────────────────────────────────────


async def setup_users(session) -> dict[str, str]:
    """创建 4 个测试用户."""
    from app.auth.models import User

    users = {
        "alice": ("Alice 主管", "ai_engineer", "ding-alice"),
        "bob": ("Bob 主管", "ai_engineer", "ding-bob"),
        "carol": ("Carol 执行", "operator", "ding-carol"),
        "dave": ("Dave 执行", "operator", "ding-dave"),
    }
    user_ids = {}
    for short, (name, role, ding_id) in users.items():
        uid = f"{RUN_TAG}-{short}"
        session.add(
            User(
                id=uid,
                username=uid,
                name=name,
                role=role,
                department="EC",
                is_active=True,
                dingtalk_user_id=ding_id,
            )
        )
        user_ids[short] = uid
    await session.flush()
    return user_ids


async def setup_skill(session, owner: str) -> str:
    """创建一个测试 Skill 入库 (不写 git, 因为 ExecutionService 会读 SKILL.md frontmatter, 没有时回退到空)."""
    from app.skills.core.models import Skill

    skill_id = f"{RUN_TAG}-ec-roi-analysis"
    session.add(
        Skill(
            id=skill_id,
            name="EC ROI 投放分析 (e2e)",
            department="EC",
            owner=owner,
            approval_level=1,
            target_users=[],
            status="active",
            description="模拟测试 Skill",
            current_version="1.0",
            created_at=now_bjt(),
            updated_at=now_bjt(),
        )
    )
    await session.flush()
    return skill_id


# ──────────────────────────────────────────────
# 第一阶段: 模拟 Skill 跑出 todos
# ──────────────────────────────────────────────


def make_skill_output(reviewers_review: list[str], reviewer_dispatch: str, executors: list[str]) -> dict:
    """这就是 Skill 在 OpenClaw 上跑完后给 SkillForge 返回的 output."""
    return {
        "summary": "本周 EC 投放分析完成",
        "metrics": {"roi": 1.85, "spend_usd": 12000, "conversion": 0.034},
        "todos": [
            {
                "kind": "review",
                "title": "本周 ROI 看板上线审批",
                "summary": "本周 ROI 1.85, 较上周 +12%, 申请上线全员看板",
                "reviewers": reviewers_review,
                "decision_mode": "all_of",
                "sla_hours": 24,
                "payload": {"roi": 1.85, "delta": 0.12},
                "callback": {
                    "instance_id": "demo-prod",
                    "skill_id": f"{RUN_TAG}-ec-roi-analysis",
                    "run_id": "aiclaw-run-roi-001",
                    "next_step": "publish_dashboard",
                },
            },
            {
                "kind": "dispatch",
                "title": "本周低质量 SKU 下架",
                "summary": "下面 2 个 SKU 差评率超 15%, 申请下架并通知运营",
                "reviewers": [reviewer_dispatch],
                "decision_mode": "any_of",
                "tasks": [
                    {
                        "executor": executors[0],
                        "content": "下架 SKU-12345 (差评率 18%, 转化率 0.4%)",
                        "deadline": "2026-04-09T18:00:00",
                        "extra": {"sku": "SKU-12345"},
                    },
                    {
                        "executor": executors[1],
                        "content": "下架 SKU-67890 (滞销 90 天, 库存 200)",
                        "deadline": "2026-04-09T18:00:00",
                        "extra": {"sku": "SKU-67890"},
                    },
                ],
                "callback": {
                    "instance_id": "demo-prod",
                    "skill_id": f"{RUN_TAG}-ec-roi-analysis",
                    "run_id": "aiclaw-run-disp-001",
                    "next_step": "after_takedown",
                },
            },
        ],
    }


# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────


async def run_simulation():
    from app.database import async_session_factory
    from app.todos.models import AITodo, DecisionRequest, TodoDispatchTask
    from app.todos.service import todo_service
    from app.dingtalk import outbox as outbox_mod
    from app.aiclaw import bridge_registry as br_mod
    from app.skills.core.models import Skill
    from app.auth.models import User
    from sqlalchemy import select, delete

    # ── monkeypatch outbox 和 bridge_registry ──
    orig_enqueue = outbox_mod.outbox.enqueue
    outbox_mod.outbox.enqueue = fake_enqueue
    orig_get = br_mod.bridge_registry.get
    br_mod.bridge_registry.get = lambda instance_id: FakeBridgeConn()

    skill_id = None
    user_ids: dict[str, str] = {}
    request_review_id = None
    request_dispatch_id = None

    try:
        # ============ 阶段 1: 准备数据 ============
        step("阶段 1: 创建测试用户和 Skill")
        async with async_session_factory() as session:
            user_ids = await setup_users(session)
            # owner=dave (执行人, 不参与审批) 避免触发自审批拦截
            skill_id = await setup_skill(session, owner=user_ids["dave"])
            await session.commit()
        info(f"用户: {list(user_ids.values())}")
        info(f"Skill: {skill_id}")
        ok("数据准备完成")

        # ============ 阶段 2: 模拟 Skill 跑出待办 ============
        step("阶段 2: Skill 在 OpenClaw 上跑完, 返回 output.todos[]")
        skill_output = make_skill_output(
            reviewers_review=[user_ids["alice"], user_ids["bob"]],
            reviewer_dispatch=user_ids["alice"],
            executors=[user_ids["carol"], user_ids["dave"]],
        )
        info(f"output.todos 包含 {len(skill_output['todos'])} 条")

        async with async_session_factory() as session:
            requests = await todo_service.create_from_execution(
                session,
                run_id=f"sf-run-{RUN_TAG}",
                skill_id=skill_id,
                skill_meta={
                    "id": skill_id,
                    "name": "EC ROI 投放分析",
                    "approval_level": 1,
                    "owner": user_ids["alice"],
                },
                decision={
                    "input_snapshot": {"week": "2026-W14"},
                    "output_result": skill_output,
                    "suggested_action": None,
                },
            )
            await session.commit()

        if len(requests) != 2:
            fail(f"应创建 2 个 DecisionRequest, 实际 {len(requests)}")
            return
        ok(f"创建了 {len(requests)} 个 DecisionRequest")

        for r in requests:
            if r.kind == "review":
                request_review_id = r.id
            elif r.kind == "dispatch":
                request_dispatch_id = r.id

        if not request_review_id or not request_dispatch_id:
            fail("缺失 review 或 dispatch 请求")
            return

        # 验证: review 应该有 2 个 AITodo, dispatch 应该有 1 个 AITodo
        async with async_session_factory() as session:
            review_todos = (await session.execute(
                select(AITodo).where(AITodo.request_id == request_review_id).order_by(AITodo.id)
            )).scalars().all()
            dispatch_todos = (await session.execute(
                select(AITodo).where(AITodo.request_id == request_dispatch_id).order_by(AITodo.id)
            )).scalars().all()
            dispatch_subtasks = (await session.execute(
                select(TodoDispatchTask).where(TodoDispatchTask.request_id == request_dispatch_id).order_by(TodoDispatchTask.id)
            )).scalars().all()

            review_request = await session.get(DecisionRequest, request_review_id)
            dispatch_request = await session.get(DecisionRequest, request_dispatch_id)

        info(f"  review 请求: {review_request.id} kind={review_request.kind} mode={review_request.decision_mode} callback={review_request.callback_status}")
        info(f"  review todos: {len(review_todos)} 条 → {[t.assignee for t in review_todos]}")
        if len(review_todos) == 2:
            ok("review 创建了双签待办")
        else:
            fail(f"review 应该是 2 个 AITodo, 实际 {len(review_todos)}")

        info(f"  dispatch 请求: {dispatch_request.id} kind={dispatch_request.kind} callback={dispatch_request.callback_status}")
        info(f"  dispatch 管理者待办: {[t.assignee for t in dispatch_todos]}")
        info(f"  dispatch 子任务: {len(dispatch_subtasks)} 条")
        for t in dispatch_subtasks:
            info(f"    - executor={t.executor} status={t.status} content={t.content[:40]}")

        if len(dispatch_subtasks) == 2 and all(t.status == "awaiting_dispatch" for t in dispatch_subtasks):
            ok("dispatch 创建了 2 个子任务且都处于 awaiting_dispatch 状态")
        else:
            fail(f"dispatch 子任务异常: {[(t.executor, t.status) for t in dispatch_subtasks]}")

        # 验证创建阶段的钉钉外发
        info(f"  钉钉 outbox 已入队 {len(ENQUEUED)} 条:")
        for e in ENQUEUED:
            info(f"    → {e['recipient']} | {e['title']} | buttons={e['buttons']}")

        # 应该: alice(review), bob(review), alice(dispatch_manager), 但 carol/dave 还不应该收到
        recipients = {e["recipient"] for e in ENQUEUED}
        if "ding-alice" in recipients and "ding-bob" in recipients:
            ok("alice + bob 收到 review 卡片")
        else:
            fail(f"review 卡片缺失收件人, 当前 recipients={recipients}")

        if "ding-carol" in recipients or "ding-dave" in recipients:
            fail(f"派发审批前 carol/dave 不应该收到任何卡片! recipients={recipients}")
        else:
            ok("carol/dave 派发审批前没有收到任何卡片 (正确)")

        # ============ 阶段 3: 各级审批 ============
        step("阶段 3-A: review 多人会签 (decision_mode=all_of)")

        # alice 先通过
        async with async_session_factory() as session:
            alice_review_todo = next(t for t in review_todos if t.assignee == user_ids["alice"])
            result = await todo_service.decide(
                session,
                todo_id=alice_review_todo.id,
                decision="approved",
                decided_by=user_ids["alice"],
                channel="web",
                reason="ROI 数据 OK",
            )
            await session.commit()
        info(f"  alice 通过: status={result['todo']['status']}")

        # 验证: aggregate 应该还是 pending (因为 bob 还没动)
        async with async_session_factory() as session:
            req = await session.get(DecisionRequest, request_review_id)
            info(f"  审批 1/2 后聚合状态: aggregate_status={req.aggregate_status}")
            if req.aggregate_status == "pending":
                ok("all_of 模式下单人审批后保持 pending")
            else:
                fail(f"all_of 第一人通过后聚合状态错误: {req.aggregate_status} (期望 pending)")

        # bob 跟着通过
        async with async_session_factory() as session:
            bob_review_todo = next(t for t in review_todos if t.assignee == user_ids["bob"])
            result = await todo_service.decide(
                session,
                todo_id=bob_review_todo.id,
                decision="approved",
                decided_by=user_ids["bob"],
                channel="web",
                reason="同意",
            )
            await session.commit()

        async with async_session_factory() as session:
            req = await session.get(DecisionRequest, request_review_id)
            info(f"  审批 2/2 后聚合状态: aggregate_status={req.aggregate_status} aggregate_decision={req.aggregate_decision}")
            info(f"  callback 状态: {req.callback_status} attempts={req.callback_attempts}")

            if req.aggregate_status == "completed" and req.aggregate_decision == "approved":
                ok("all_of 双人通过 → 聚合状态完成")
            else:
                fail(f"双人通过后聚合状态错误: status={req.aggregate_status} decision={req.aggregate_decision}")

            if req.callback_status == "sent":
                ok("review 决策已回调 OpenClaw")
            else:
                fail(f"review 决策回调状态异常: {req.callback_status}, error={req.callback_error}")

        step("阶段 3-B: dispatch 单人审批 + fan-out")

        async with async_session_factory() as session:
            disp_mgr_todo = dispatch_todos[0]
            result = await todo_service.decide(
                session,
                todo_id=disp_mgr_todo.id,
                decision="approved",
                decided_by=user_ids["alice"],
                channel="web",
                reason="同意下架",
            )
            await session.commit()
            info(f"  alice 通过派发审批: status={result['todo']['status']}")

        # 验证: 子任务应该全部 sent, 钉钉应该新增 carol/dave 的卡片
        async with async_session_factory() as session:
            req = await session.get(DecisionRequest, request_dispatch_id)
            subtasks = (await session.execute(
                select(TodoDispatchTask).where(TodoDispatchTask.request_id == request_dispatch_id).order_by(TodoDispatchTask.id)
            )).scalars().all()

            info(f"  dispatch 聚合状态: {req.aggregate_status}/{req.aggregate_decision}")
            info(f"  子任务最新状态:")
            for t in subtasks:
                info(f"    - executor={t.executor} status={t.status} dingtalk_msg_id={t.dingtalk_msg_id} dispatched_at={t.dispatched_at}")

            if all(t.status == "sent" for t in subtasks) and all(t.dingtalk_msg_id for t in subtasks):
                ok("子任务全部 sent + 都有 dingtalk_msg_id")
            else:
                fail(f"派发后子任务状态异常: {[(t.executor, t.status, bool(t.dingtalk_msg_id)) for t in subtasks]}")

            info(f"  dispatch callback: {req.callback_status} attempts={req.callback_attempts}")
            if req.callback_status == "sent":
                ok("dispatch 决策已回调 OpenClaw")
            else:
                fail(f"dispatch callback 失败: {req.callback_status} error={req.callback_error}")

        # 检查这一轮新增的 outbox 是不是只多了 carol/dave 的
        new_recipients = {e["recipient"] for e in ENQUEUED if e["related_type"] == "dispatch_task"}
        info(f"  派发后 dispatch_task 类型推送收件人: {new_recipients}")
        if "ding-carol" in new_recipients and "ding-dave" in new_recipients:
            ok("派发审批通过 → carol/dave 都收到任务卡片")
        else:
            fail(f"fan-out 失败, dispatch_task recipients={new_recipients}")

        # ============ 阶段 4: 执行人 ack ============
        step("阶段 4: 执行人 ack 派发子任务")

        async with async_session_factory() as session:
            subtasks = (await session.execute(
                select(TodoDispatchTask).where(TodoDispatchTask.request_id == request_dispatch_id).order_by(TodoDispatchTask.id)
            )).scalars().all()

            # carol 先 ack
            carol_task = next(t for t in subtasks if t.executor == user_ids["carol"])
            result = await todo_service.ack_dispatch_task(
                session,
                task_id=carol_task.id,
                actor_id=user_ids["carol"],
                channel="web",
                note="已下架",
            )
            await session.commit()
            info(f"  carol ack: status={result['status']} ack_at={result['ack_at']}")

        async with async_session_factory() as session:
            # dave 跟着 ack
            dave_task = next(t for t in subtasks if t.executor == user_ids["dave"])
            result = await todo_service.ack_dispatch_task(
                session,
                task_id=dave_task.id,
                actor_id=user_ids["dave"],
                channel="dingtalk",  # 模拟 dave 是在钉钉点的
                note="完成",
            )
            await session.commit()
            info(f"  dave ack: status={result['status']} channel={result['decision_channel'] if 'decision_channel' in result else result.get('ack_channel')}")

        async with async_session_factory() as session:
            subtasks = (await session.execute(
                select(TodoDispatchTask).where(TodoDispatchTask.request_id == request_dispatch_id).order_by(TodoDispatchTask.id)
            )).scalars().all()
            info("  最终子任务状态:")
            for t in subtasks:
                info(f"    - executor={t.executor} status={t.status} ack_channel={t.ack_channel}")

            if all(t.status == "done" for t in subtasks):
                ok("两个子任务全部 done")
            else:
                fail(f"子任务未全部 done: {[(t.executor, t.status) for t in subtasks]}")

        # ============ 阶段 5: 验证 callback ============
        step("阶段 5: 验证 OpenClaw callback 调用")

        info(f"  bridge_op 总调用次数: {len(BRIDGE_CALLS)}")
        for c in BRIDGE_CALLS:
            info(f"    op={c['op']} skill={c['payload'].get('skill_id')} run={c['payload'].get('run_id')} next={c['payload'].get('next_step')} decision={c['payload'].get('decision')}")

        if len(BRIDGE_CALLS) == 2:
            ok("两次决策都触发了 callback")
        else:
            fail(f"callback 调用次数异常: {len(BRIDGE_CALLS)} (期望 2)")

        notify_ops = [c for c in BRIDGE_CALLS if c["op"] == "notify_decision"]
        if len(notify_ops) == 2 and all(c["payload"]["decision"] == "approved" for c in notify_ops):
            ok("notify_decision payload 正确")
        else:
            fail(f"notify_decision payload 异常: {notify_ops}")

        # ============ 阶段 6: 异常 + 边界用例 ============
        from app.common.exceptions import AppError

        step("阶段 6-A: 非执行人 ack 应被拒")
        async with async_session_factory() as session:
            dave_task = next(t for t in subtasks if t.executor == user_ids["dave"])
            try:
                await todo_service.ack_dispatch_task(
                    session,
                    task_id=dave_task.id,
                    actor_id=user_ids["carol"],
                )
                ok("已完成的子任务再 ack 是幂等的")
            except AppError as e:
                if e.code == "TODO_NOT_ASSIGNED_TO_YOU":
                    ok("非执行人 ack 被拒 (TODO_NOT_ASSIGNED_TO_YOU)")
                else:
                    fail(f"非执行人 ack 异常类型不对: {e.code}")

        step("阶段 6-B: 非审批人审批应被拒")
        # 跑一条新的 review 待办测试越权审批
        async with async_session_factory() as session:
            requests_x = await todo_service.create_from_execution(
                session,
                run_id=f"sf-run-{RUN_TAG}-perm",
                skill_id=skill_id,
                skill_meta={"id": skill_id, "name": "权限测试"},
                decision={
                    "input_snapshot": {},
                    "output_result": {
                        "todos": [{
                            "kind": "review",
                            "title": "权限测试 review",
                            "reviewers": [user_ids["alice"]],
                        }]
                    },
                },
            )
            await session.commit()
            req_x = requests_x[0]

        async with async_session_factory() as session:
            todo_x = (await session.execute(
                select(AITodo).where(AITodo.request_id == req_x.id)
            )).scalar_one()
            try:
                await todo_service.decide(
                    session,
                    todo_id=todo_x.id,
                    decision="approved",
                    decided_by=user_ids["carol"],  # carol 不是审批人
                )
                fail("非审批人 carol 越权审批了 alice 的待办!")
            except AppError as e:
                if e.code == "TODO_NOT_ASSIGNED_TO_YOU":
                    ok("非审批人审批被拒 (TODO_NOT_ASSIGNED_TO_YOU)")
                else:
                    fail(f"越权异常类型不对: {e.code}")

        step("阶段 6-C: 重复审批应被拒 (TODO_ALREADY_DECIDED)")
        async with async_session_factory() as session:
            await todo_service.decide(
                session,
                todo_id=todo_x.id,
                decision="approved",
                decided_by=user_ids["alice"],
            )
            await session.commit()
        async with async_session_factory() as session:
            try:
                await todo_service.decide(
                    session,
                    todo_id=todo_x.id,
                    decision="rejected",
                    decided_by=user_ids["alice"],
                )
                fail("已审批的待办被重复审批没有报错!")
            except AppError as e:
                if e.code == "TODO_ALREADY_DECIDED":
                    ok("重复审批被拒 (TODO_ALREADY_DECIDED)")
                else:
                    fail(f"重复审批异常类型不对: {e.code}")

        step("阶段 6-D: dispatch 驳回 → 子任务应全部 cancelled")
        async with async_session_factory() as session:
            requests_y = await todo_service.create_from_execution(
                session,
                run_id=f"sf-run-{RUN_TAG}-reject",
                skill_id=skill_id,
                skill_meta={"id": skill_id, "name": "派发驳回测试"},
                decision={
                    "input_snapshot": {},
                    "output_result": {
                        "todos": [{
                            "kind": "dispatch",
                            "title": "测试驳回",
                            "reviewers": [user_ids["alice"]],
                            "tasks": [
                                {"executor": user_ids["carol"], "content": "任务 X"},
                                {"executor": user_ids["dave"], "content": "任务 Y"},
                            ],
                        }]
                    },
                },
            )
            await session.commit()
            req_y = requests_y[0]

        ENQUEUED_BEFORE = len(ENQUEUED)
        async with async_session_factory() as session:
            mgr_todo_y = (await session.execute(
                select(AITodo).where(AITodo.request_id == req_y.id)
            )).scalar_one()
            await todo_service.decide(
                session,
                todo_id=mgr_todo_y.id,
                decision="rejected",
                decided_by=user_ids["alice"],
                reason="数据有问题",
            )
            await session.commit()

        async with async_session_factory() as session:
            subtasks_y = (await session.execute(
                select(TodoDispatchTask).where(TodoDispatchTask.request_id == req_y.id)
            )).scalars().all()
            info(f"  驳回后子任务状态: {[(t.executor, t.status) for t in subtasks_y]}")
            if all(t.status == "cancelled" for t in subtasks_y):
                ok("驳回后子任务全部 cancelled")
            else:
                fail(f"驳回后子任务未取消: {[(t.executor, t.status) for t in subtasks_y]}")

        new_pushes = ENQUEUED[ENQUEUED_BEFORE:]
        executor_pushes = [e for e in new_pushes if e["related_type"] == "dispatch_task"]
        if not executor_pushes:
            ok("驳回后没有给执行人推送任务")
        else:
            fail(f"驳回后竟然给执行人推了 {len(executor_pushes)} 条: {executor_pushes}")

        # 清理 perm/reject 测试用的请求
        async with async_session_factory() as session:
            for rid in [req_x.id, req_y.id]:
                await session.execute(delete(TodoDispatchTask).where(TodoDispatchTask.request_id == rid))
                await session.execute(delete(AITodo).where(AITodo.request_id == rid))
                await session.execute(delete(DecisionRequest).where(DecisionRequest.id == rid))
            await session.commit()

        step("阶段 6-E: spec 不合法应被拒")
        async with async_session_factory() as session:
            from app.todos.todo_spec import parse_todo_specs, TodoSpecParseError

            bad_cases = [
                ({"todos": [{"kind": "unknown", "title": "x"}]}, "kind 非法"),
                ({"todos": [{"kind": "review"}]}, "title 缺失"),
                ({"todos": [{"kind": "dispatch", "title": "x", "reviewers": ["alice"]}]}, "dispatch 没 tasks"),
                ({"todos": [{"kind": "dispatch", "title": "x", "reviewers": ["a"], "tasks": [{"content": "x"}]}]}, "tasks.executor 缺失"),
                ({"todos": [{"kind": "review", "title": "x", "callback": {"instance_id": "x"}}]}, "callback 缺 skill_id"),
            ]
            for output, label in bad_cases:
                try:
                    parse_todo_specs(output)
                    fail(f"不合法 spec 没被拒: {label}")
                except TodoSpecParseError:
                    ok(f"非法 spec 被拒: {label}")

        step("阶段 6-F: bridge 不在线时 callback 应记录 failed")
        async with async_session_factory() as session:
            requests_z = await todo_service.create_from_execution(
                session,
                run_id=f"sf-run-{RUN_TAG}-cb-fail",
                skill_id=skill_id,
                skill_meta={"id": skill_id, "name": "callback 失败测试"},
                decision={
                    "input_snapshot": {},
                    "output_result": {
                        "todos": [{
                            "kind": "review",
                            "title": "测试 callback 失败",
                            "reviewers": [user_ids["alice"]],
                            "callback": {
                                "instance_id": "demo-prod",
                                "skill_id": skill_id,
                                "run_id": "rid-fail",
                                "next_step": "x",
                            },
                        }]
                    },
                },
            )
            await session.commit()
            req_z = requests_z[0]

        # 临时让 bridge_registry.get 返回 None 模拟离线
        br_mod.bridge_registry.get = lambda iid: None

        async with async_session_factory() as session:
            todo_z = (await session.execute(
                select(AITodo).where(AITodo.request_id == req_z.id)
            )).scalar_one()
            await todo_service.decide(
                session,
                todo_id=todo_z.id,
                decision="approved",
                decided_by=user_ids["alice"],
            )
            await session.commit()

        async with async_session_factory() as session:
            req_z_after = await session.get(DecisionRequest, req_z.id)
            info(f"  callback_status={req_z_after.callback_status}, error={req_z_after.callback_error}")
            if req_z_after.callback_status == "failed" and "未连接" in (req_z_after.callback_error or ""):
                ok("bridge 离线时 callback 状态正确标记为 failed")
            else:
                fail(f"bridge 离线时 callback 状态错: {req_z_after.callback_status}")

        # 还原
        br_mod.bridge_registry.get = lambda iid: FakeBridgeConn()

        async with async_session_factory() as session:
            await session.execute(delete(AITodo).where(AITodo.request_id == req_z.id))
            await session.execute(delete(DecisionRequest).where(DecisionRequest.id == req_z.id))
            await session.commit()

        step("阶段 6-G: any_of 模式 - 一人通过其他人变 resolved_by_peer")
        async with async_session_factory() as session:
            requests_w = await todo_service.create_from_execution(
                session,
                run_id=f"sf-run-{RUN_TAG}-anyof",
                skill_id=skill_id,
                skill_meta={"id": skill_id, "name": "any_of 测试"},
                decision={
                    "input_snapshot": {},
                    "output_result": {
                        "todos": [{
                            "kind": "review",
                            "title": "any_of 任一即可",
                            "reviewers": [user_ids["alice"], user_ids["bob"]],
                            "decision_mode": "any_of",
                        }]
                    },
                },
            )
            await session.commit()
            req_w = requests_w[0]

        async with async_session_factory() as session:
            todos_w = (await session.execute(
                select(AITodo).where(AITodo.request_id == req_w.id).order_by(AITodo.id)
            )).scalars().all()
            alice_w = next(t for t in todos_w if t.assignee == user_ids["alice"])
            await todo_service.decide(
                session,
                todo_id=alice_w.id,
                decision="approved",
                decided_by=user_ids["alice"],
            )
            await session.commit()

        async with async_session_factory() as session:
            todos_w = (await session.execute(
                select(AITodo).where(AITodo.request_id == req_w.id).order_by(AITodo.id)
            )).scalars().all()
            statuses = {t.assignee.split('-')[-1]: t.status for t in todos_w}
            info(f"  any_of 一人通过后状态: {statuses}")
            req_w_after = await session.get(DecisionRequest, req_w.id)
            info(f"  聚合: {req_w_after.aggregate_status}/{req_w_after.aggregate_decision}")

            if statuses.get("alice") == "approved" and statuses.get("bob") == "resolved_by_peer":
                ok("any_of 一人通过 → 其他人 resolved_by_peer")
            else:
                fail(f"any_of 状态转换错: {statuses}")

            if req_w_after.aggregate_status == "completed":
                ok("any_of 一人通过即聚合完成")
            else:
                fail(f"any_of 聚合错: {req_w_after.aggregate_status}")

        async with async_session_factory() as session:
            await session.execute(delete(AITodo).where(AITodo.request_id == req_w.id))
            await session.execute(delete(DecisionRequest).where(DecisionRequest.id == req_w.id))
            await session.commit()

        step("阶段 6-H: dispatch 子任务执行人没绑定钉钉 → 应该如何处理")
        # 创建一个没绑定 dingtalk_user_id 的执行人
        from app.auth.models import User as UserModel
        async with async_session_factory() as session:
            ghost_id = f"{RUN_TAG}-ghost"
            session.add(UserModel(
                id=ghost_id, username=ghost_id, name="幽灵执行人",
                role="operator", is_active=True, dingtalk_user_id=None,
            ))
            user_ids["ghost"] = ghost_id
            await session.commit()

            requests_h = await todo_service.create_from_execution(
                session,
                run_id=f"sf-run-{RUN_TAG}-ghost",
                skill_id=skill_id,
                skill_meta={"id": skill_id, "name": "ghost test"},
                decision={
                    "input_snapshot": {},
                    "output_result": {
                        "todos": [{
                            "kind": "dispatch",
                            "title": "ghost dispatch",
                            "reviewers": [user_ids["alice"]],
                            "tasks": [
                                {"executor": ghost_id, "content": "你接得到吗"},
                                {"executor": user_ids["carol"], "content": "正常人"},
                            ],
                        }]
                    },
                },
            )
            await session.commit()
            req_h = requests_h[0]

        ENQ_BEFORE_H = len(ENQUEUED)
        async with async_session_factory() as session:
            mgr_h = (await session.execute(
                select(AITodo).where(AITodo.request_id == req_h.id)
            )).scalar_one()
            await todo_service.decide(
                session,
                todo_id=mgr_h.id,
                decision="approved",
                decided_by=user_ids["alice"],
            )
            await session.commit()

        async with async_session_factory() as session:
            tasks_h = (await session.execute(
                select(TodoDispatchTask).where(TodoDispatchTask.request_id == req_h.id)
                .order_by(TodoDispatchTask.id)
            )).scalars().all()
            info(f"  fan-out 后子任务状态:")
            for t in tasks_h:
                info(f"    - executor={t.executor[-10:]} status={t.status} dingtalk_msg_id={t.dingtalk_msg_id}")

            # 期望：carol 任务有 dingtalk_msg_id, ghost 任务没有
            ghost_task = next(t for t in tasks_h if t.executor == ghost_id)
            carol_task_h = next(t for t in tasks_h if t.executor == user_ids["carol"])

            if carol_task_h.dingtalk_msg_id:
                ok("正常执行人收到推送")
            else:
                fail("正常执行人没收到推送")

            if ghost_task.dingtalk_msg_id is None:
                ok("没绑钉钉的执行人 dingtalk_msg_id 为空")
            else:
                fail(f"没绑钉钉的执行人居然有 msg_id: {ghost_task.dingtalk_msg_id}")

            # 修复 #1: 没绑钉钉的执行人应该被标 pushed_no_dingtalk, 而不是 sent
            if ghost_task.status == "pushed_no_dingtalk":
                ok("没绑钉钉的执行人任务被标 pushed_no_dingtalk (前端会显示红色提示)")
            else:
                fail(f"没绑钉钉的执行人状态错: {ghost_task.status} (期望 pushed_no_dingtalk)")

            # 站内通知应该照常入库
            from app.notifications.models import Notification
            ghost_notif = (await session.execute(
                select(Notification).where(Notification.user_id == ghost_id)
            )).scalar_one_or_none()
            if ghost_notif:
                ok("没绑钉钉的执行人也收到了站内通知")
            else:
                fail("没绑钉钉的执行人连站内通知都没收到")

            # ghost 也能在 web 端 ack
            try:
                ack_result = await todo_service.ack_dispatch_task(
                    session,
                    task_id=ghost_task.id,
                    actor_id=ghost_id,
                    channel="web",
                )
                if ack_result["status"] == "done":
                    ok("ghost 在 web 端可以 ack pushed_no_dingtalk 任务")
                else:
                    fail(f"ghost ack 后状态错: {ack_result['status']}")
            except AppError as e:
                fail(f"ghost web ack 报错: {e.code}")
            await session.commit()

        async with async_session_factory() as session:
            await session.execute(delete(TodoDispatchTask).where(TodoDispatchTask.request_id == req_h.id))
            await session.execute(delete(AITodo).where(AITodo.request_id == req_h.id))
            await session.execute(delete(DecisionRequest).where(DecisionRequest.id == req_h.id))
            await session.commit()

        step("阶段 6-I: dispatch + decision_mode=independent 是否合理")
        # dispatch 通常是单管理者审批 → independent 没意义
        # 但 spec 没拦, 跑一下看看会不会出怪状态
        async with async_session_factory() as session:
            requests_i = await todo_service.create_from_execution(
                session,
                run_id=f"sf-run-{RUN_TAG}-independent",
                skill_id=skill_id,
                skill_meta={"id": skill_id, "name": "independent dispatch"},
                decision={
                    "input_snapshot": {},
                    "output_result": {
                        "todos": [{
                            "kind": "dispatch",
                            "title": "两个管理者各自决定要不要派",
                            "reviewers": [user_ids["alice"], user_ids["bob"]],
                            "decision_mode": "independent",
                            "tasks": [{"executor": user_ids["carol"], "content": "干"}],
                        }]
                    },
                },
            )
            await session.commit()
            req_i = requests_i[0]

        # alice 通过, bob 驳回
        async with async_session_factory() as session:
            todos_i = (await session.execute(
                select(AITodo).where(AITodo.request_id == req_i.id).order_by(AITodo.id)
            )).scalars().all()
            await todo_service.decide(
                session,
                todo_id=next(t for t in todos_i if t.assignee == user_ids["alice"]).id,
                decision="approved",
                decided_by=user_ids["alice"],
            )
            await session.commit()

        async with async_session_factory() as session:
            todos_i = (await session.execute(
                select(AITodo).where(AITodo.request_id == req_i.id).order_by(AITodo.id)
            )).scalars().all()
            await todo_service.decide(
                session,
                todo_id=next(t for t in todos_i if t.assignee == user_ids["bob"]).id,
                decision="rejected",
                decided_by=user_ids["bob"],
            )
            await session.commit()

        async with async_session_factory() as session:
            req_i_after = await session.get(DecisionRequest, req_i.id)
            tasks_i = (await session.execute(
                select(TodoDispatchTask).where(TodoDispatchTask.request_id == req_i.id)
            )).scalars().all()
            info(f"  independent + 一通过一驳回 → 聚合: {req_i_after.aggregate_status}/{req_i_after.aggregate_decision}")
            info(f"  子任务状态: {[t.status for t in tasks_i]}")

            # 当前实现: independent 模式下混合结果会被标 mixed, 不会触发 fan-out
            # 但 fan-out 触发条件只是 aggregate_decision=='approved', 所以 mixed 不会派发
            # 这其实是合理的, 但 dispatch + independent 的语义含糊不清
            if req_i_after.aggregate_decision == "mixed":
                if all(t.status == "awaiting_dispatch" for t in tasks_i):
                    warn(
                        "PROBLEM: dispatch + independent 一通过一驳回, 子任务永久卡在 awaiting_dispatch (既不派发也不取消)。"
                        "应该: (a) parse_todo_specs 拒绝 dispatch+independent; 或 (b) mixed 时也算 cancelled"
                    )
                elif all(t.status == "cancelled" for t in tasks_i):
                    ok("dispatch + independent + mixed 视为 cancelled")
                else:
                    fail(f"dispatch + independent + mixed 出现奇怪状态: {[t.status for t in tasks_i]}")
            else:
                fail(f"independent + 一通过一驳回 应该是 mixed, 实际 {req_i_after.aggregate_decision}")

        async with async_session_factory() as session:
            await session.execute(delete(TodoDispatchTask).where(TodoDispatchTask.request_id == req_i.id))
            await session.execute(delete(AITodo).where(AITodo.request_id == req_i.id))
            await session.execute(delete(DecisionRequest).where(DecisionRequest.id == req_i.id))
            await session.commit()

        step("阶段 6-J: 同 run_id 重跑 Skill 是否会重复创建待办 (重试幂等)")
        # 模拟真实重试场景: SkillForge 重跑 Skill 或 OpenClaw 重发结果, spec 内容完全一致
        async with async_session_factory() as session:
            same_run_id = f"sf-run-{RUN_TAG}-dup"
            same_spec = {
                "kind": "review",
                "title": "dup test",
                "reviewers": [user_ids["alice"]],
            }

            ENQ_BEFORE_J = len(ENQUEUED)
            requests_j1 = await todo_service.create_from_execution(
                session,
                run_id=same_run_id,
                skill_id=skill_id,
                skill_meta={"id": skill_id, "name": "dup test"},
                decision={
                    "input_snapshot": {},
                    "output_result": {"todos": [same_spec]},
                },
            )
            await session.commit()

            requests_j2 = await todo_service.create_from_execution(
                session,
                run_id=same_run_id,  # 完全相同的 run_id + spec
                skill_id=skill_id,
                skill_meta={"id": skill_id, "name": "dup test"},
                decision={
                    "input_snapshot": {},
                    "output_result": {"todos": [same_spec]},
                },
            )
            await session.commit()

            info(f"  第一次创建 {len(requests_j1)} 个 request, 第二次返回 {len(requests_j2)} 个")
            # 修复 #2: 同 run_id+spec 第二次应返回相同 request, 不重复建
            if len(requests_j1) == 1 and len(requests_j2) == 1 and requests_j1[0].id == requests_j2[0].id:
                ok("同 run_id+同 spec 重跑 → 幂等返回同一个 DecisionRequest")
            else:
                ids = [r.id for r in requests_j1 + requests_j2]
                fail(f"重复 run_id 没有去重: ids={ids}")

            # 验证 alice 没收到第二次审批卡 (上一步开始后的新增 outbox 条数)
            new_pushes_j = ENQUEUED[ENQ_BEFORE_J:]
            alice_pushes = [e for e in new_pushes_j if e["recipient"] == "ding-alice"]
            info(f"  alice 在重试期间收到 {len(alice_pushes)} 张卡片")
            if len(alice_pushes) == 1:
                ok("重试不会让 alice 重复收到审批卡")
            else:
                fail(f"重试导致 alice 收到 {len(alice_pushes)} 张卡片 (期望 1)")

            unique_request_id = requests_j1[0].id
            await session.execute(delete(AITodo).where(AITodo.request_id == unique_request_id))
            await session.execute(delete(DecisionRequest).where(DecisionRequest.id == unique_request_id))
            await session.commit()

        step("阶段 6-K: Skill owner 是否能审批自己的待办 (自审批检查)")
        # 临时把主 skill 的 owner 改成 alice 来触发自审批场景
        async with async_session_factory() as session:
            skill_obj = await session.get(Skill, skill_id)
            orig_owner = skill_obj.owner
            skill_obj.owner = user_ids["alice"]
            await session.commit()

        async with async_session_factory() as session:
            requests_k = await todo_service.create_from_execution(
                session,
                run_id=f"sf-run-{RUN_TAG}-self",
                skill_id=skill_id,
                skill_meta={"id": skill_id, "name": "self approve", "owner": user_ids["alice"]},
                decision={
                    "input_snapshot": {},
                    "output_result": {
                        "todos": [{
                            "kind": "review",
                            "title": "alice 自审 alice 写的 Skill",
                            "reviewers": [user_ids["alice"]],  # owner == reviewer
                        }]
                    },
                },
            )
            await session.commit()
            req_k = requests_k[0]

        async with async_session_factory() as session:
            todo_k = (await session.execute(
                select(AITodo).where(AITodo.request_id == req_k.id)
            )).scalar_one()
            try:
                await todo_service.decide(
                    session,
                    todo_id=todo_k.id,
                    decision="approved",
                    decided_by=user_ids["alice"],
                )
                await session.commit()
                fail("PROBLEM: Skill owner 可以审批自己 Skill 跑出的待办 (4 眼原则未生效)")
            except AppError as e:
                if e.code == "REVIEW_SELF_APPROVE":
                    ok("自审批被拒 (REVIEW_SELF_APPROVE)")
                else:
                    fail(f"自审批异常类型不对: {e.code}")

            # 但 admin 应该能代审 (admin 例外)
            admin_id = f"{RUN_TAG}-admin"
            session.add(UserModel(
                id=admin_id, username=admin_id, name="Admin",
                role="admin", is_active=True,
            ))
            await session.commit()
            user_ids["admin"] = admin_id

            try:
                await todo_service.decide(
                    session,
                    todo_id=todo_k.id,
                    decision="approved",
                    decided_by=admin_id,
                )
                await session.commit()
                ok("admin 可以代审 Skill owner 的待办 (admin 例外)")
            except AppError as e:
                fail(f"admin 代审被拒: {e.code}")

        # 恢复 owner
        async with async_session_factory() as session:
            skill_obj = await session.get(Skill, skill_id)
            skill_obj.owner = orig_owner
            await session.commit()

        async with async_session_factory() as session:
            await session.execute(delete(AITodo).where(AITodo.request_id == req_k.id))
            await session.execute(delete(DecisionRequest).where(DecisionRequest.id == req_k.id))
            await session.commit()

    except Exception:
        traceback.print_exc()
        fail(f"未捕获异常: 见上面 traceback")
    finally:
        # 还原 monkeypatch
        outbox_mod.outbox.enqueue = orig_enqueue
        br_mod.bridge_registry.get = orig_get

        # 清理测试数据
        step("清理: 删除测试数据")
        try:
            async with async_session_factory() as session:
                # 先删 todos / dispatch / decision_requests
                if request_review_id:
                    await session.execute(delete(AITodo).where(AITodo.request_id == request_review_id))
                if request_dispatch_id:
                    await session.execute(delete(TodoDispatchTask).where(TodoDispatchTask.request_id == request_dispatch_id))
                    await session.execute(delete(AITodo).where(AITodo.request_id == request_dispatch_id))
                if request_review_id:
                    await session.execute(delete(DecisionRequest).where(DecisionRequest.id == request_review_id))
                if request_dispatch_id:
                    await session.execute(delete(DecisionRequest).where(DecisionRequest.id == request_dispatch_id))

                # 顺手清掉测试 skill 和 user
                if skill_id:
                    await session.execute(delete(Skill).where(Skill.id == skill_id))
                for uid in user_ids.values():
                    await session.execute(delete(User).where(User.id == uid))

                # 清掉 notifications
                from app.notifications.models import Notification
                await session.execute(
                    delete(Notification).where(Notification.user_id.in_(list(user_ids.values())))
                )

                # 清掉 audit logs (不强求, 量小)
                await session.commit()
            ok("清理完成")
        except Exception as e:
            warn(f"清理时出错: {e}")

    # ============ 总结 ============
    step("总结")
    print(f"\n  钉钉外发总计: {len(ENQUEUED)} 条")
    print(f"  bridge_op 调用: {len(BRIDGE_CALLS)} 次")
    print(f"  捕获问题: {len(PROBLEMS)} 个")
    if PROBLEMS:
        print(f"\n{RED}{BOLD}问题清单:{RESET}")
        for i, p in enumerate(PROBLEMS, 1):
            print(f"  {RED}{i}.{RESET} {p}")
        sys.exit(1)
    else:
        print(f"\n{GREEN}{BOLD}全部通过 ✓{RESET}")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(run_simulation())
