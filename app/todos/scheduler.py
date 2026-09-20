"""AI 待办后台调度。"""

from __future__ import annotations

import asyncio

from loguru import logger
from sqlalchemy import select

from app.config import settings
from app.database import async_session_factory

from .models import DecisionRequest
from .service import todo_service


async def expire_old_todos_loop():
    interval = max(30, settings.TODO_EXPIRE_LOOP_INTERVAL)
    while True:
        try:
            async with async_session_factory() as session:
                expired = await todo_service.expire_due_todos(session)
                if expired:
                    await session.commit()
                    logger.info("AI 待办过期归档完成: {} 条", len(expired))
                    await _notify_expired(expired)
                else:
                    await session.rollback()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("AI 待办过期归档失败: {}", exc)
        await asyncio.sleep(interval)


async def callback_retry_loop():
    """P2-3: 周期扫描需要重试的 OpenClaw 决策回调。

    挑选条件：callback_status='pending' AND callback_payload 非空 AND
    callback_completed_at IS NULL AND callback_attempts < 5。
    达到 5 次仍失败的转为 'failed' 永久放弃。
    """
    interval = 60
    while True:
        try:
            async with async_session_factory() as session:
                stmt = (
                    select(DecisionRequest)
                    .where(DecisionRequest.callback_status == "pending")
                    .where(DecisionRequest.callback_completed_at.is_(None))
                    .where(DecisionRequest.callback_attempts < 5)
                    .where(DecisionRequest.callback_attempts > 0)  # 0 = 首次, 由主流程触发
                    .limit(20)
                )
                requests = (await session.execute(stmt)).scalars().all()
                for req in requests:
                    try:
                        await todo_service._notify_openclaw_callback(session, req)
                    except Exception as exc:
                        logger.warning(
                            "callback retry 失败 request={} err={}", req.id, exc,
                        )
                # 超过 5 次的转为 failed
                from sqlalchemy import update as sa_update
                await session.execute(
                    sa_update(DecisionRequest)
                    .where(DecisionRequest.callback_status == "pending")
                    .where(DecisionRequest.callback_attempts >= 5)
                    .values(callback_status="failed")
                )
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("callback retry loop 失败: {}", exc)
        await asyncio.sleep(interval)


async def _notify_expired(expired_records: list[dict]) -> None:
    """过期只更新平台待办状态，不直接推钉钉。"""
    if expired_records:
        logger.info("AI 待办过期，仅更新平台状态，不推钉钉: {} 条", len(expired_records))
