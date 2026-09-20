"""
审计日志：记录所有关键操作（登录/编辑/审核/执行等）。
全局实例 audit 供各模块调用。
"""

import csv
import io
from datetime import datetime, timedelta

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import select, func, and_

from app.database import Base, async_session_factory
from app.common.time_utils import isoformat_bjt, now_bjt


# ===== ORM 模型 =====

class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(50), index=True)
    action: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(30))  # skill/review/playbook/user/datasource
    target_id: Mapped[str | None] = mapped_column(String(50))
    detail: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(String(45))
    # F3: 本次操作使用的 prompt 版本 hash（PromptRegistry），用于追溯 LLM 决策依据
    prompt_hash: Mapped[str | None] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_bjt)


# ===== 审计日志服务 =====

class AuditLogger:
    """
    全局审计日志记录器。
    在每个需要审计的操作点调用 audit.log()。
    """

    async def log(
        self,
        user_id: str,
        action: str,
        target_type: str | None = None,
        target_id: str | None = None,
        detail: dict | None = None,
        ip_address: str | None = None,
        prompt_hash: str | None = None,
    ) -> None:
        """记录一条审计日志。

        Args:
            prompt_hash: F3 - 如果本次操作触发了 LLM 调用，传入对应 PromptRegistry hash，
                         便于后续审核时追溯"用的哪版 prompt"
        """
        from app.database import async_session_factory  # 延迟导入，确保用最新的factory
        async with async_session_factory() as session:
            entry = AuditLog(
                user_id=user_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                detail=detail,
                ip_address=ip_address,
                prompt_hash=prompt_hash,
            )
            session.add(entry)
            await session.commit()

    async def query(
        self,
        action: str | None = None,
        user_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        date_from: str | datetime | None = None,
        date_to: str | datetime | None = None,
        prompt_hash: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """分页查询审计日志"""
        from app.database import async_session_factory
        async with async_session_factory() as session:
            # 构建过滤条件
            conditions = []
            if action:
                conditions.append(AuditLog.action == action)
            if user_id:
                conditions.append(AuditLog.user_id == user_id)
            if target_type:
                conditions.append(AuditLog.target_type == target_type)
            if target_id:
                conditions.append(AuditLog.target_id == target_id)
            if date_from:
                conditions.append(AuditLog.created_at >= date_from)
            if date_to:
                conditions.append(AuditLog.created_at <= date_to)
            if prompt_hash:
                conditions.append(AuditLog.prompt_hash == prompt_hash)

            where_clause = and_(*conditions) if conditions else True

            offset = (page - 1) * page_size
            stmt = (
                select(AuditLog)
                .where(where_clause)
                .order_by(AuditLog.created_at.desc())
                .offset(offset)
                .limit(page_size)
            )
            count_stmt = select(func.count(AuditLog.id)).where(where_clause)
            total = (await session.execute(count_stmt)).scalar() or 0
            result = await session.execute(stmt)
            items = result.scalars().all()

            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "items": [
                    {
                        "id": item.id,
                        "user_id": item.user_id,
                        "action": item.action,
                        "target_type": item.target_type,
                        "target_id": item.target_id,
                        "detail": item.detail,
                        "ip_address": item.ip_address,
                        "prompt_hash": item.prompt_hash,
                        "created_at": isoformat_bjt(item.created_at),
                    }
                    for item in items
                ],
            }

    async def get_stats(self, days: int = 30) -> dict:
        """审计统计：按action分组计数，安全事件计数"""
        from app.database import async_session_factory
        async with async_session_factory() as session:
            cutoff = now_bjt() - timedelta(days=days)

            # 按action分组计数
            action_counts_stmt = (
                select(AuditLog.action, func.count(AuditLog.id))
                .where(AuditLog.created_at >= cutoff)
                .group_by(AuditLog.action)
                .order_by(func.count(AuditLog.id).desc())
            )
            result = await session.execute(action_counts_stmt)
            action_counts = {row[0]: row[1] for row in result.all()}

            # 总数
            total = sum(action_counts.values())

            # 安全事件：登录失败
            login_failures = action_counts.get("user.login_failed", 0)

            # 安全事件：权限拒绝
            permission_denials = action_counts.get("auth.permission_denied", 0)

            # 安全事件总计（含其他异常操作）
            security_actions = [
                "user.login_failed",
                "auth.permission_denied",
                "auth.session_invalid",
            ]
            security_count = sum(
                action_counts.get(a, 0) for a in security_actions
            )

            return {
                "days": days,
                "total": total,
                "by_action": action_counts,
                "security": {
                    "total": security_count,
                    "login_failures": login_failures,
                    "permission_denials": permission_denials,
                },
            }

    async def export_csv(
        self,
        action: str | None = None,
        user_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        date_from: str | datetime | None = None,
        date_to: str | datetime | None = None,
    ) -> str:
        """导出审计日志为CSV字符串"""
        from app.database import async_session_factory
        async with async_session_factory() as session:
            # 构建过滤条件（与query()相同逻辑）
            conditions = []
            if action:
                conditions.append(AuditLog.action == action)
            if user_id:
                conditions.append(AuditLog.user_id == user_id)
            if target_type:
                conditions.append(AuditLog.target_type == target_type)
            if target_id:
                conditions.append(AuditLog.target_id == target_id)
            if date_from:
                conditions.append(AuditLog.created_at >= date_from)
            if date_to:
                conditions.append(AuditLog.created_at <= date_to)

            where_clause = and_(*conditions) if conditions else True

            # 查询全部符合条件的记录（导出不分页，限制10000条防止内存溢出）
            stmt = (
                select(AuditLog)
                .where(where_clause)
                .order_by(AuditLog.created_at.desc())
                .limit(10000)
            )
            result = await session.execute(stmt)
            items = result.scalars().all()

            # 生成CSV
            output = io.StringIO()
            writer = csv.writer(output)
            # 表头
            writer.writerow([
                "id", "user_id", "action", "target_type",
                "target_id", "detail", "ip_address", "created_at",
            ])
            # 数据行
            for item in items:
                writer.writerow([
                    item.id,
                    item.user_id or "",
                    item.action,
                    item.target_type or "",
                    item.target_id or "",
                    str(item.detail) if item.detail else "",
                    item.ip_address or "",
                    isoformat_bjt(item.created_at) or "",
                ])

            return output.getvalue()


# 全局实例
audit = AuditLogger()
