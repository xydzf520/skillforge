"""
后台创建任务管理器。

核心设计：创建任务一旦开始就在后台独立运行，不依赖 WS 连接。
WS 只是"窗口"——断了任务继续跑，刷新浏览器后重新连上看进度。

生命周期：
  start()  → 后台 asyncio.Task 开始，DB draft=running
  subscribe() → WS 消费者连到 task 的事件 queue
  WS 断开 → queue 无消费者，task 继续跑，事件丢弃（但 DB 已有关键状态）
  WS 重连 → 先从 DB 读已完成的 milestone/contract，再 subscribe 新事件
  task 完成 → DB draft=ready/failed，queue 推 sentinel
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass
from typing import Any, AsyncIterator
from uuid import uuid4

from loguru import logger
from sqlalchemy import select

from app.coding_agent.skill_creation_runner import (
    CreationEvent,
    CreationEventType,
    run_skill_creation,
)
from app.workbench.task_contract import (
    build_gate_status,
    build_preview_cache_key,
    checkpoint_list,
    merge_review_state,
    review_expires_at,
)


def _now_bjt():
    from app.common.time_utils import now_bjt
    return now_bjt()


def _iso_bjt(value):
    from app.common.time_utils import isoformat_bjt
    return isoformat_bjt(value)


def _sf():
    from app.database import async_session_factory
    return async_session_factory


MAX_CONCURRENT_TASKS = 2  # 同时运行的创建任务上限；超出的任务进入 FIFO 队列
MAX_PENDING_TASKS = 50  # FIFO 等待队列上限；超出即拒绝,防止内存堆积


class CreationQueueFullError(Exception):
    """等待队列已满,用于让调用方返回 429 给用户。"""

    def __init__(self, pending: int, limit: int):
        self.pending = pending
        self.limit = limit
        super().__init__(f"pending queue full: {pending}/{limit}")


@dataclass
class _PendingCreationTask:
    draft_id: str
    message: str
    user_id: str


class CreationTaskManager:
    """全局单例：管理后台运行的 skill 创建任务。"""

    def __init__(self):
        # draft_id → asyncio.Task
        self._tasks: dict[str, asyncio.Task] = {}
        # draft_id → asyncio.Queue (WS 消费者订阅用；None sentinel = done)
        self._queues: dict[str, asyncio.Queue] = {}
        # FIFO 等待队列；只在 running 槽位满时入队
        self._pending: deque[_PendingCreationTask] = deque()
        self._pending_ids: set[str] = set()

    def start(self, draft_id: str, message: str, user_id: str) -> None:
        """启动后台创建任务。立即返回,不阻塞。

        Raises:
            CreationQueueFullError: 等待队列已满时由调用方转成 HTTP 429。
        """
        if draft_id in self._tasks or draft_id in self._pending_ids:
            logger.warning("[task_mgr] draft {} already running, skip", draft_id)
            return
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        if self._running_count() >= MAX_CONCURRENT_TASKS:
            if len(self._pending) >= MAX_PENDING_TASKS:
                logger.warning(
                    "[task_mgr] pending queue full {}/{} reject draft={}",
                    len(self._pending), MAX_PENDING_TASKS, draft_id,
                )
                raise CreationQueueFullError(len(self._pending), MAX_PENDING_TASKS)
            self._queues[draft_id] = q
            self._pending.append(_PendingCreationTask(draft_id, message, user_id))
            self._pending_ids.add(draft_id)
            logger.info(
                "[task_mgr] draft={} queued position={} max_concurrent={}",
                draft_id,
                len(self._pending),
                MAX_CONCURRENT_TASKS,
            )
            self._publish_queue_positions()
            return
        self._queues[draft_id] = q
        self._launch_task(draft_id, message, user_id, q)

    def is_running(self, draft_id: str) -> bool:
        t = self._tasks.get(draft_id)
        return (t is not None and not t.done()) or draft_id in self._pending_ids

    async def subscribe(self, draft_id: str) -> AsyncIterator[dict]:
        """订阅事件流。task 不存在或已完成时立即返回。"""
        q = self._queues.get(draft_id)
        if q is None:
            return
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=600)
            except asyncio.TimeoutError:
                return
            if event is None:  # sentinel = done
                return
            yield event

    def _running_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())

    def _launch_task(self, draft_id: str, message: str, user_id: str, q: asyncio.Queue) -> None:
        task = asyncio.create_task(
            self._run_background(draft_id, message, user_id, q),
            name=f"skill-creation-{draft_id}",
        )
        self._tasks[draft_id] = task

    def _publish_queue_positions(self) -> None:
        for position, item in enumerate(self._pending, start=1):
            q = self._queues.get(item.draft_id)
            if q is None:
                continue
            self._push_event(
                q,
                {
                    "type": "queued",
                    "position": position,
                    "max_concurrent_tasks": MAX_CONCURRENT_TASKS,
                },
            )

    def _start_next_pending(self) -> None:
        while self._running_count() < MAX_CONCURRENT_TASKS and self._pending:
            item = self._pending.popleft()
            self._pending_ids.discard(item.draft_id)
            q = self._queues.get(item.draft_id)
            if q is None:
                continue
            self._launch_task(item.draft_id, item.message, item.user_id, q)
        self._publish_queue_positions()

    # ─── 内部：后台任务主逻辑 ───

    async def _run_background(
        self, draft_id: str, message: str, user_id: str, q: asyncio.Queue,
    ) -> None:
        """后台跑完整生成流程。DB 写入不依赖消费者。"""
        try:
            if not await self._mark_running(draft_id):
                self._push_event(q, {"type": "done", "status": "cancelled"})
                return
            logger.info("[task_mgr] start draft={}", draft_id)
            async for ev in run_skill_creation(message=message, draft_id=draft_id, user_id=user_id):
                # 1) 推到 queue 给 WS 消费者（满则丢最老的，不阻塞）
                self._push_event(q, ev.to_dict())

                # 2) 关键状态写 DB（不依赖消费者）
                if ev.type == CreationEventType.MILESTONE:
                    await self._save_milestone(draft_id, ev.payload)
                elif ev.type == CreationEventType.CONTRACT_READY:
                    await self._save_contract(draft_id, ev.payload)
                elif ev.type == CreationEventType.SKILL_READY:
                    logger.info("[task_mgr] draft={} saving skill_ready to DB...", draft_id)
                    await self._save_skill_ready(draft_id, ev.payload, user_id)
                    logger.info("[task_mgr] draft={} building enriched frame...", draft_id)
                    enriched = await self._build_enriched_frame(draft_id, ev.payload)
                    logger.info("[task_mgr] draft={} enriched={}", draft_id, "OK" if enriched else "NONE")
                    if enriched:
                        self._push_event(q, enriched)
                        logger.info("[task_mgr] draft={} enriched pushed to queue", draft_id)
                elif ev.type == CreationEventType.ERROR:
                    await self._save_error(draft_id, ev.payload)
        except Exception:
            logger.exception("[task_mgr] draft={} unexpected error", draft_id)
            await self._save_error(draft_id, {
                "code": "TASK_CRASHED",
                "detail": "后台任务异常退出",
            })
        finally:
            # 推 sentinel 给消费者
            self._push_event(q, None)
            # 清理 task 引用（queue 保留一段时间供延迟重连）
            self._tasks.pop(draft_id, None)
            self._start_next_pending()
            # 60s 后清理 queue（给重连一点窗口）
            asyncio.get_running_loop().call_later(60, self._cleanup_queue, draft_id)
            logger.info("[task_mgr] done draft={}", draft_id)

    def _cleanup_queue(self, draft_id: str) -> None:
        self._queues.pop(draft_id, None)

    @staticmethod
    def _push_event(q: asyncio.Queue, event: dict | None) -> None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # 丢最老的事件，确保新事件能进去
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    # ─── DB 写入（关键状态持久化，不依赖 WS 消费者） ───

    @staticmethod
    async def _save_milestone(draft_id: str, payload: dict) -> None:
        """保存已完成的 milestone 到 draft（供重连时恢复进度条）。"""
        from app.workbench.models import SkillStudioDraft
        try:
            async with _sf()() as session:
                draft = (
                    await session.execute(
                        select(SkillStudioDraft).where(SkillStudioDraft.id == draft_id)
                    )
                ).scalar_one_or_none()
                if not draft:
                    return
                # 把 milestone 追加到 error_detail 里的 milestones 数组（复用已有 JSONB 字段）
                progress = draft.error_detail or {}
                milestones = progress.get("milestones") or []
                milestones.append({
                    "key": payload.get("milestone"),
                    "label": payload.get("label"),
                    "elapsed_s": payload.get("elapsed_s"),
                })
                progress["milestones"] = milestones
                draft.error_detail = progress
                draft.updated_at = _now_bjt()
                await session.commit()
        except Exception:
            logger.warning("[task_mgr] save_milestone failed draft={}", draft_id)

    @staticmethod
    async def _save_contract(draft_id: str, payload: dict) -> None:
        from app.workbench.models import SkillStudioDraft
        try:
            async with _sf()() as session:
                draft = (
                    await session.execute(
                        select(SkillStudioDraft).where(SkillStudioDraft.id == draft_id)
                    )
                ).scalar_one_or_none()
                if not draft:
                    return
                draft.contract_json = payload.get("contract")
                draft.updated_at = _now_bjt()
                await session.commit()
        except Exception:
            logger.debug("[task_mgr] save_contract failed draft={}", draft_id)

    @staticmethod
    async def _save_skill_ready(draft_id: str, payload: dict, user_id: str) -> None:
        from app.workbench.models import SkillStudioDraft
        try:
            contract = payload.get("contract") or {}
            files = payload.get("files") or {}
            standard_keys = {"SKILL.md", "intent.md", "policy.yaml", "contract.json"}
            extra = {k: v for k, v in files.items() if k not in standard_keys}

            review_state = merge_review_state(contract, {})
            # 反疲劳
            from app.workbench.service import workbench_service
            user_confirmations = await workbench_service._count_user_confirmations(user_id)
            fatigue = workbench_service._derive_fatigue_state(user_confirmations)
            review_state = workbench_service._apply_fatigue_to_review_state(review_state, fatigue)

            async with _sf()() as session:
                draft = (
                    await session.execute(
                        select(SkillStudioDraft).where(SkillStudioDraft.id == draft_id)
                    )
                ).scalar_one_or_none()
                if not draft:
                    return
                draft.contract_json = contract
                draft.skill_md = files.get("SKILL.md")
                draft.intent_md = files.get("intent.md")
                draft.policy_yaml = files.get("policy.yaml")
                draft.extra_files = extra
                draft.review_state_json = review_state
                draft.generation_status = "ready"
                # 成功时清掉 milestone 进度残留,防止前端 has_error 误报
                draft.error_detail = None
                draft.updated_at = _now_bjt()
                await session.commit()
        except Exception:
            logger.exception("[task_mgr] save_skill_ready failed draft={}", draft_id)

    @staticmethod
    async def _build_enriched_frame(draft_id: str, payload: dict) -> dict | None:
        """构造 skill_ready_enriched 帧，含 checkpoints + gate，前端靠这个渲染 4 必感知点按钮。"""
        try:
            from app.workbench.models import SkillStudioDraft
            async with _sf()() as session:
                draft = (
                    await session.execute(
                        select(SkillStudioDraft).where(SkillStudioDraft.id == draft_id)
                    )
                ).scalar_one_or_none()
                if not draft or not draft.contract_json:
                    return None

                contract = draft.contract_json
                review_state = draft.review_state_json or merge_review_state(contract, {})
                files: dict[str, str] = {}
                if draft.skill_md:
                    files["SKILL.md"] = draft.skill_md
                if draft.intent_md:
                    files["intent.md"] = draft.intent_md
                if draft.policy_yaml:
                    files["policy.yaml"] = draft.policy_yaml
                if draft.contract_json:
                    files["contract.json"] = json.dumps(draft.contract_json, ensure_ascii=False, indent=2)
                for rel, content in (draft.extra_files or {}).items():
                    files[rel] = content
                preview_cache_key = build_preview_cache_key(contract, files)

                # 生成真实预演：(1) adapter 渲染 (2) 真跑一次 main.py 看 todos/reports 实际产出
                try:
                    from app.workbench.task_contract import build_preview
                    preview_payload = build_preview(
                        contract,
                        cache_key=preview_cache_key,
                        cached=False,
                        files=files,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[task_mgr] build_preview 失败 draft={}: {}", draft_id, exc)
                    preview_payload = {
                        "adapter": (contract.get("output") or {}).get("adapter") or "",
                        "cache_key": preview_cache_key,
                        "rendered_output": f"（预演渲染失败：{exc}）",
                        "card_payload": {},
                        "fixture_used": {},
                        "cached": False,
                        "generated_at": _iso_bjt(_now_bjt()),
                        "success": False,
                    }

                # (2) 真实 sample_input 执行 main.py 拿 todos/reports 统计 + schema 对齐结果
                try:
                    from app.common.contract_schema import (
                        build_verified_preview,
                    )

                    preview_payload = await build_verified_preview(
                        contract,
                        files,
                        cache_key=preview_cache_key,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[task_mgr] sandbox preview 失败 draft={}: {}", draft_id, exc)

                gate = build_gate_status(contract, review_state, preview_payload)

                return {
                    "type": "skill_ready_enriched",
                    "draft_id": draft_id,
                    "contract": contract,
                    "files": files,
                    "checkpoints": checkpoint_list(review_state),
                    "gate": gate,
                    "preview": preview_payload,
                }
        except Exception:
            logger.exception("[task_mgr] build_enriched_frame failed draft={}", draft_id)
            return None

    @staticmethod
    async def _save_error(draft_id: str, payload: dict) -> None:
        from app.workbench.models import SkillStudioDraft
        try:
            async with _sf()() as session:
                draft = (
                    await session.execute(
                        select(SkillStudioDraft).where(SkillStudioDraft.id == draft_id)
                    )
                ).scalar_one_or_none()
                if not draft:
                    return
                draft.generation_status = "failed"
                existing = draft.error_detail or {}
                existing["code"] = payload.get("code")
                existing["detail"] = payload.get("detail")
                existing["failed_at"] = _iso_bjt(_now_bjt())
                draft.error_detail = existing
                draft.updated_at = _now_bjt()
                await session.commit()
        except Exception:
            logger.exception("[task_mgr] save_error failed draft={}", draft_id)

    @staticmethod
    async def _mark_running(draft_id: str) -> bool:
        from app.workbench.models import SkillStudioDraft
        try:
            async with _sf()() as session:
                draft = (
                    await session.execute(
                        select(SkillStudioDraft).where(SkillStudioDraft.id == draft_id)
                    )
                ).scalar_one_or_none()
                if not draft:
                    return False
                if draft.generation_status not in {"pending", "running"}:
                    return False
                draft.generation_status = "running"
                draft.updated_at = _now_bjt()
                await session.commit()
                return True
        except Exception:
            logger.exception("[task_mgr] mark_running failed draft={}", draft_id)
            return False


# 全局单例
creation_task_manager = CreationTaskManager()
