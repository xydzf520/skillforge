"""Admin-only Skill 管理端点（v2.8.0）。

数据卫生 / 批量清理 / ID 分类扫描 —— 全部要求 admin 角色。
子路由挂载到 /api/skills 下，URL 模式 `/skills/admin/*`。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role
from app.auth.models import User
from app.common.audit import audit
from app.common.cache import invalidate_skill
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt
from app.database import get_db
from app.skills.core.id_gen import classify_skill_id
from app.skills.core.models import Skill
from app.skills.lifecycle import service as skill_service

router = APIRouter()


@router.get("/admin/hygiene")
async def scan_hygiene(
    filter_class: str | None = Query(
        None,
        description="按分类过滤：e2e_test / import / fork / manual；不传则返回全部 + 统计",
    ),
    current_user: User = Depends(require_role("admin", "system_admin")),
    db: AsyncSession = Depends(get_db),
):
    """扫描全库 Skill，按 id 来源分类，用于数据卫生面板。

    返回：
    ```
    {
      "total": 132,
      "counts": {"manual": 100, "import": 20, "fork": 10, "e2e_test": 2},
      "items": [...]  # 过滤后的
    }
    ```
    """
    rows = (await db.execute(select(Skill))).scalars().all()

    counts: dict[str, int] = {"manual": 0, "import": 0, "fork": 0, "e2e_test": 0}
    items: list[dict] = []
    for s in rows:
        category = classify_skill_id(s.id)
        counts[category] = counts.get(category, 0) + 1
        if filter_class and category != filter_class:
            continue
        items.append(
            {
                "id": s.id,
                "name": s.name,
                "department": s.department,
                "status": s.status,
                "owner": s.owner,
                "category": category,
                "created_at": isoformat_bjt(s.created_at),
                "updated_at": isoformat_bjt(s.updated_at),
            }
        )

    items.sort(key=lambda x: (x["category"], x["id"]))
    return {"total": len(rows), "counts": counts, "items": items}


class BulkCleanupRequest(BaseModel):
    skill_ids: list[str]
    # 软校验：要求每个 id 的 classify 在这里，避免误删生产
    expected_category: str = "e2e_test"
    confirm_text: str = ""  # UI 要求用户输入 "DELETE" 才放行


@router.post("/admin/hygiene/bulk-cleanup")
async def bulk_cleanup(
    body: BulkCleanupRequest,
    current_user: User = Depends(require_role("admin", "system_admin")),
    db: AsyncSession = Depends(get_db),
):
    """批量清理脏 Skill。

    双重保护：
    1. 每个 id 必须 `classify_skill_id(id) == expected_category`
    2. UI 要求输入 "DELETE" 才放行（后端校验）
    """
    if body.confirm_text != "DELETE":
        raise AppError(
            "CONFIRM_REQUIRED", 400,
            {"detail": "批量清理需确认，请在 confirm_text 传 'DELETE'"},
        )

    deleted: list[str] = []
    skipped: list[dict] = []
    errors: list[dict] = []

    for sid in body.skill_ids:
        actual = classify_skill_id(sid)
        if actual != body.expected_category:
            skipped.append({"id": sid, "actual_category": actual})
            continue
        try:
            await skill_service.delete_skill(db, sid, current_user.id)
            try:
                await invalidate_skill(sid)
            except Exception:
                pass
            deleted.append(sid)
        except Exception as e:  # noqa: BLE001
            errors.append({"id": sid, "error": str(e)[:200]})

    try:
        await audit.log(
            user_id=current_user.id,
            action="skill.admin.bulk_cleanup",
            detail={
                "requested": len(body.skill_ids),
                "deleted": len(deleted),
                "skipped": len(skipped),
                "errors": len(errors),
                "expected_category": body.expected_category,
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("audit log 失败: {}", e)

    return {
        "requested": len(body.skill_ids),
        "deleted": deleted,
        "skipped": skipped,
        "errors": errors,
    }
