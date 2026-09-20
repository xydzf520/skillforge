"""审计日志独立API路由（仅admin）"""

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from loguru import logger

from app.auth.dependencies import require_role
from app.auth.models import User
from app.common.audit import audit
from app.common.cache import cache_get, cache_set
from app.common.time_utils import now_bjt
from app.config import settings

router = APIRouter()


@router.get("/")
async def query_audit(
    action: str | None = Query(None),
    user_id: str | None = Query(None),
    target_type: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(require_role("admin")),
):
    """查询审计日志"""
    return await audit.query(
        action=action,
        user_id=user_id,
        target_type=target_type,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )


@router.get("/actions")
async def audit_actions(
    current_user: User = Depends(require_role("admin")),
):
    """可用的 audit.action 枚举。
    从实际日志里 distinct 出最近 90 天使用过的 action，再合并硬编码核心 action 保底。
    前端 AdminAudit 的操作类型下拉用此接口动态填充。
    """
    import asyncpg
    from app.config import settings

    # 核心硬编码 action（保证常用项即便还没产生过日志也能选到）
    core = [
        {"action": "user.login", "label": "登录"},
        {"action": "user.login_failed", "label": "登录失败"},
        {"action": "skill.create", "label": "创建 Skill"},
        {"action": "skill.edit", "label": "编辑 Skill"},
        {"action": "review.approve", "label": "审核通过"},
        {"action": "review.reject", "label": "审核驳回"},
        {"action": "codex.auth.login", "label": "Codex 登录"},
        {"action": "codex.api.call", "label": "Codex API 调用"},
        {"action": "codex.mcp.catalog", "label": "Codex MCP 目录"},
        {"action": "codex.mcp.call", "label": "Codex MCP 调用"},
        {"action": "codex.skill.submit", "label": "Codex 提交 Skill"},
        {"action": "org.member.add", "label": "组织 · 加成员"},
        {"action": "org.member.remove", "label": "组织 · 移除成员"},
        {"action": "org.unit.move", "label": "组织 · 迁移"},
        {"action": "org.unit.delete", "label": "组织 · 删除"},
    ]
    known = {x["action"] for x in core}

    cache_key = "audit:actions:v1"
    try:
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)

    # 从 DB 拉 distinct action
    try:
        dsn = str(settings.DATABASE_URL).replace("+asyncpg", "")
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch(
                """
                SELECT DISTINCT action
                FROM audit_logs
                WHERE created_at > now() - interval '90 days'
                ORDER BY action
                """
            )
            discovered = [r["action"] for r in rows if r["action"] and r["action"] not in known]
        finally:
            await conn.close()
    except Exception as e:
        logger.warning("加载审计 action 枚举失败: {}", e)
        discovered = []

    items = core + [{"action": a, "label": a} for a in discovered]
    result = {"items": items}
    try:
        await cache_set(cache_key, result, ttl=300)
    except Exception as e:
        logger.warning("缓存操作失败: {}", e)
    return result


@router.get("/detail-fields")
async def audit_detail_fields(
    current_user: User = Depends(require_role("admin")),
):
    """审计日志 detail 里允许展示为 inline tag 的字段白名单 + 中文标签。
    前端 AdminAudit 从这里动态拉取，失败降级到前端默认白名单。"""
    return {
        "fields": [
            {"key": "reason", "label": "原因", "priority": 100},
            {"key": "username", "label": "用户名", "priority": 90},
            {"key": "role_from", "label": "旧角色", "priority": 85},
            {"key": "role_to", "label": "新角色", "priority": 85},
            {"key": "skill_id", "label": "Skill", "priority": 80},
            {"key": "from", "label": "From", "priority": 70},
            {"key": "to", "label": "To", "priority": 70},
            {"key": "block", "label": "Block", "priority": 60},
            {"key": "unit_id", "label": "组织", "priority": 50},
            {"key": "member_id", "label": "成员", "priority": 50},
            {"key": "target", "label": "目标", "priority": 40},
        ]
    }


@router.get("/stats")
async def audit_stats(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(require_role("admin")),
):
    """审计统计：按action分组计数、安全事件统计"""
    cache_key = f"audit:stats:{days}"
    try:
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached
    except Exception as e:
        logger.warning("缓存读取失败，穿透到数据库: {}", e)
    result = await audit.get_stats(days)
    try:
        await cache_set(cache_key, result, ttl=settings.CACHE_TTL_AUDIT)
    except Exception as e:
        logger.warning("缓存写入失败: {}", e)
    return result


@router.get("/security-events")
async def security_events(
    days: int = Query(7, ge=1, le=90),
    current_user: User = Depends(require_role("admin")),
):
    """安全事件：登录失败、权限拒绝等"""
    cache_key = f"audit:security:{days}"
    try:
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached
    except Exception as e:
        logger.warning("缓存读取失败，穿透到数据库: {}", e)
    result = await audit.query(
        action="user.login_failed",
        date_from=now_bjt() - timedelta(days=days),
        date_to=None,
        page=1,
        page_size=200,
    )
    try:
        await cache_set(cache_key, result, ttl=settings.CACHE_TTL_AUDIT)
    except Exception as e:
        logger.warning("缓存写入失败: {}", e)
    return result


@router.get("/export")
async def audit_export(
    action: str | None = Query(None),
    user_id: str | None = Query(None),
    target_type: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    current_user: User = Depends(require_role("admin")),
):
    """导出审计日志CSV"""
    csv_content = await audit.export_csv(
        action=action,
        user_id=user_id,
        target_type=target_type,
        date_from=date_from,
        date_to=date_to,
    )
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
    )
