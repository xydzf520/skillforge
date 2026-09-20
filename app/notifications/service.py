"""通知服务：创建通知 + 查询"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.time_utils import isoformat_bjt
from app.notifications.models import Notification


async def notify(db: AsyncSession, user_id: str, type: str, title: str,
                 body: str = None, link: str = None) -> None:
    """创建一条通知。仅 flush 不 commit，由调用方统一控制事务边界。"""
    db.add(Notification(
        user_id=user_id, type=type, title=title, body=body, link=link,
    ))
    await db.flush()


async def list_notifications(db: AsyncSession, user_id: str,
                              unread_only: bool = False, page: int = 1, page_size: int = 20) -> dict:
    """分页查询通知"""
    stmt = select(Notification).where(Notification.user_id == user_id)
    count_stmt = select(func.count(Notification.id)).where(Notification.user_id == user_id)

    if unread_only:
        stmt = stmt.where(Notification.read == False)  # noqa: E712
        count_stmt = count_stmt.where(Notification.read == False)  # noqa: E712

    total = (await db.execute(count_stmt)).scalar() or 0
    unread = (await db.execute(
        select(func.count(Notification.id))
        .where(Notification.user_id == user_id)
        .where(Notification.read == False)  # noqa: E712
    )).scalar() or 0

    stmt = stmt.order_by(Notification.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    items = result.scalars().all()

    return {
        "total": total,
        "unread": unread,
        "page": page,
        "items": [
            {
                "id": n.id, "type": n.type, "title": n.title, "body": n.body,
                "link": n.link, "read": n.read,
                "created_at": isoformat_bjt(n.created_at),
            }
            for n in items
        ],
    }


async def mark_read(db: AsyncSession, notification_id: int, user_id: str) -> None:
    """标记单条通知已读"""
    await db.execute(
        update(Notification)
        .where(Notification.id == notification_id)
        .where(Notification.user_id == user_id)
        .values(read=True)
    )
    await db.commit()


async def mark_all_read(db: AsyncSession, user_id: str) -> int:
    """标记全部已读，返回影响行数"""
    result = await db.execute(
        update(Notification)
        .where(Notification.user_id == user_id)
        .where(Notification.read == False)  # noqa: E712
        .values(read=True)
    )
    await db.commit()
    return result.rowcount
