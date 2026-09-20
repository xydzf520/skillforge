"""
钉钉消息持久化队列（PostgreSQL outbox模式）。
解决：进程重启不丢消息 + 速率限制18条/分钟 + 消息优先级。
"""

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import select, update

from app.dingtalk.client import dingtalk_client
from app.dingtalk.models import DingTalkOutbox
from app.common.time_utils import now_bjt


def _sf():
    """延迟获取async_session_factory"""
    from app.database import async_session_factory
    return async_session_factory


@dataclass(slots=True)
class _ClaimedOutboxMessage:
    id: int
    message_type: str
    recipient_user_id: str | None
    payload: dict
    priority: int
    attempts: int
    max_attempts: int
    lease_deadline: datetime


class DingTalkOutboxService:
    """持久化消息队列服务"""

    RATE_LIMIT = 18  # 每分钟发18条（留2条余量）
    SEND_INTERVAL = 60 / 18  # ≈3.3秒/条
    MAX_CONCURRENCY = 4
    SENDING_TIMEOUT_SECONDS = 300  # P1-3 sending 状态超时：5 分钟未变更视为进程崩溃，回滚到 pending
    IDLE_POLL_INTERVAL_SECONDS = 5
    ACTIVE_POLL_INTERVAL_SECONDS = 0.5
    RETRY_BASE_DELAY_SECONDS = 30
    RETRY_MAX_DELAY_SECONDS = 300
    RETRY_JITTER_MIN = 0.5
    RETRY_JITTER_MAX = 1.5

    def __init__(self) -> None:
        self._rate_limit_lock = asyncio.Lock()
        self._next_send_monotonic = 0.0

    async def enqueue(
        self,
        message_type: str,
        recipient: str,
        payload: dict,
        priority: int = 5,
        related_type: str | None = None,
        related_id: str | None = None,
        session=None,
    ) -> int:
        """写入outbox表，返回消息ID。

        P1-2 事务一致性：调用方传入 session 时复用外层事务，确保 outbox 行
        与业务表（如 dispatch_task.status）在同一事务内一起 commit/rollback，
        避免"业务侧回滚但消息已入队 → 重复推送"的问题。

        不传 session 时退回独立 session（兼容旧调用方）。
        """
        msg = DingTalkOutbox(
            message_type=message_type,
            priority=priority,
            recipient_user_id=recipient,
            payload=payload,
            related_type=related_type,
            related_id=related_id,
        )
        if session is not None:
            session.add(msg)
            await session.flush()  # 拿到自增 id 但不提前 commit
            logger.info(f"消息入队(共享事务): #{msg.id} type={message_type} to={recipient} priority={priority}")
            return msg.id

        async with _sf()() as new_session:
            new_session.add(msg)
            await new_session.commit()
            await new_session.refresh(msg)
            logger.info(f"消息入队: #{msg.id} type={message_type} to={recipient} priority={priority}")
            return msg.id

    async def process_loop(self) -> None:
        """后台循环：批量 claim + 并发发送，发包速率仍受全局节流控制。"""
        logger.info("钉钉outbox消费者启动")
        inflight: set[asyncio.Task] = set()

        while True:
            try:
                await self._drain_done_tasks(inflight)

                await self._reclaim_stale_sending()

                available_slots = max(0, self.MAX_CONCURRENCY - len(inflight))
                claimed = await self._claim_batch(available_slots)
                for msg in claimed:
                    inflight.add(asyncio.create_task(self._deliver_one(msg)))

                if claimed:
                    continue

                if inflight:
                    done, pending = await asyncio.wait(
                        inflight,
                        timeout=self.ACTIVE_POLL_INTERVAL_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        await self._await_delivery_task(task)
                    inflight = set(pending)
                else:
                    await asyncio.sleep(self.IDLE_POLL_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                for task in inflight:
                    task.cancel()
                if inflight:
                    await asyncio.gather(*inflight, return_exceptions=True)
                logger.info("钉钉outbox消费者停止")
                break
            except Exception as e:
                logger.error(f"outbox处理异常: {e}")
                await asyncio.sleep(10)

    async def _process_one(self) -> None:
        """兼容旧调用路径：claim 1 条并完成发送。"""
        await self._reclaim_stale_sending()
        claimed = await self._claim_batch(1)
        if not claimed:
            await asyncio.sleep(self.IDLE_POLL_INTERVAL_SECONDS)
            return
        await self._deliver_one(claimed[0])

    async def _drain_done_tasks(self, inflight: set[asyncio.Task]) -> None:
        done = [task for task in inflight if task.done()]
        for task in done:
            inflight.discard(task)
            await self._await_delivery_task(task)

    async def _await_delivery_task(self, task: asyncio.Task) -> None:
        try:
            await task
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"outbox投递任务异常: {e}")

    async def _reclaim_stale_sending(self) -> int:
        """回收 lease 过期的 sending 消息。

        sending 状态下，`next_retry_at` 表示发送 lease 截止时间，而不是 retry 时间。
        一旦 lease 过期，恢复为 pending，并清掉 lease。
        """
        async with _sf()() as session:
            now = now_bjt()
            result = await session.execute(
                update(DingTalkOutbox)
                .where(DingTalkOutbox.status == "sending")
                .where(DingTalkOutbox.next_retry_at.is_not(None))
                .where(DingTalkOutbox.next_retry_at < now)
                .values(
                    status="pending",
                    next_retry_at=None,
                    error_message="sending timeout reclaimed",
                )
            )
            await session.commit()
            reclaimed = result.rowcount or 0
            if reclaimed:
                logger.warning(f"回收超时sending消息: {reclaimed}条")
            return reclaimed

    async def _claim_batch(self, limit: int) -> list[_ClaimedOutboxMessage]:
        """按优先级 claim 一批待发送消息。"""
        if limit <= 0:
            return []

        async with _sf()() as session:
            now = now_bjt()
            stmt = (
                select(DingTalkOutbox)
                .where(DingTalkOutbox.status == "pending")
                .where(
                    (DingTalkOutbox.next_retry_at.is_(None))
                    | (DingTalkOutbox.next_retry_at <= now)
                )
                .order_by(DingTalkOutbox.priority.asc(), DingTalkOutbox.created_at.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())

            if not rows:
                return []

            claimed: list[_ClaimedOutboxMessage] = []
            for msg in rows:
                lease_deadline = now_bjt() + timedelta(seconds=self.SENDING_TIMEOUT_SECONDS)
                msg.status = "sending"
                msg.next_retry_at = lease_deadline
                claimed.append(
                    _ClaimedOutboxMessage(
                        id=msg.id,
                        message_type=msg.message_type,
                        recipient_user_id=msg.recipient_user_id,
                        payload=msg.payload,
                        priority=msg.priority,
                        attempts=msg.attempts,
                        max_attempts=msg.max_attempts,
                        lease_deadline=lease_deadline,
                    )
                )
            await session.commit()
            return claimed

    async def _deliver_one(self, msg: _ClaimedOutboxMessage) -> None:
        """发送单条已 claim 消息。"""
        await self._acquire_send_slot()

        try:
            send_result = await dingtalk_client.send(msg)
            if send_result.get("ok"):
                await self._mark_sent(msg)
                return

            error = send_result.get("error", "未知错误")
            await self._mark_failed_attempt(msg, error)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._mark_failed_attempt(msg, str(e))
            logger.error(f"消息发送异常: #{msg.id} {e}")

    async def _acquire_send_slot(self) -> None:
        """全局发包节流：无论并发多少，整体发包速率维持在 18/min 左右。"""
        async with self._rate_limit_lock:
            now = asyncio.get_running_loop().time()
            wait_seconds = max(0.0, self._next_send_monotonic - now)
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
                now = asyncio.get_running_loop().time()
            self._next_send_monotonic = max(self._next_send_monotonic, now) + self.SEND_INTERVAL

    def _compute_retry_delay(self, attempts: int) -> float:
        base_delay = self.RETRY_BASE_DELAY_SECONDS * (2 ** max(0, attempts - 1))
        capped_delay = min(float(base_delay), float(self.RETRY_MAX_DELAY_SECONDS))
        jitter_factor = random.uniform(self.RETRY_JITTER_MIN, self.RETRY_JITTER_MAX)
        return min(capped_delay * jitter_factor, float(self.RETRY_MAX_DELAY_SECONDS))

    async def _mark_sent(self, msg: _ClaimedOutboxMessage) -> None:
        async with _sf()() as session:
            result = await session.execute(
                update(DingTalkOutbox)
                .where(DingTalkOutbox.id == msg.id)
                .where(DingTalkOutbox.status == "sending")
                .where(DingTalkOutbox.attempts == msg.attempts)
                .where(DingTalkOutbox.next_retry_at == msg.lease_deadline)
                .values(
                    status="sent",
                    sent_at=now_bjt(),
                    next_retry_at=None,
                    error_message=None,
                )
            )
            await session.commit()

        if result.rowcount:
            logger.info(f"消息发送成功: #{msg.id}")
        else:
            logger.warning(f"消息发送结果已过期，忽略成功回写: #{msg.id}")

    async def _mark_failed_attempt(self, msg: _ClaimedOutboxMessage, error: str) -> None:
        attempts = msg.attempts + 1
        next_retry_at = None
        new_status = "failed"
        if attempts < msg.max_attempts:
            new_status = "pending"
            delay_seconds = self._compute_retry_delay(attempts)
            next_retry_at = now_bjt() + timedelta(seconds=delay_seconds)

        async with _sf()() as session:
            result = await session.execute(
                update(DingTalkOutbox)
                .where(DingTalkOutbox.id == msg.id)
                .where(DingTalkOutbox.status == "sending")
                .where(DingTalkOutbox.attempts == msg.attempts)
                .where(DingTalkOutbox.next_retry_at == msg.lease_deadline)
                .values(
                    status=new_status,
                    attempts=attempts,
                    next_retry_at=next_retry_at,
                    error_message=error,
                )
            )
            await session.commit()

        if not result.rowcount:
            logger.warning(f"消息发送结果已过期，忽略失败回写: #{msg.id}")
            return

        logger.warning(f"消息发送失败: #{msg.id} attempt={attempts} status={new_status} error={error}")

    async def get_stats(self) -> dict:
        """队列统计"""
        async with _sf()() as session:
            from sqlalchemy import func
            result = await session.execute(
                select(DingTalkOutbox.status, func.count(DingTalkOutbox.id))
                .group_by(DingTalkOutbox.status)
            )
            counts = {row[0]: row[1] for row in result.all()}
            return {
                "pending": counts.get("pending", 0),
                "sending": counts.get("sending", 0),
                "sent": counts.get("sent", 0),
                "failed": counts.get("failed", 0),
                "total": sum(counts.values()),
            }


# 全局实例
outbox = DingTalkOutboxService()
