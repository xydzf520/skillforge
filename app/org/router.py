"""组织架构API路由（仅admin可操作）"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.cache import cached, invalidate_admin_cache
from app.config import settings
from app.database import get_db
from app.org import service

router = APIRouter()

_CACHE_KEY_EXCLUDES = frozenset({"db", "session", "request", "background_tasks", "current_user"})
_CACHE_SCOPE = ("current_user.id", "current_user.role", "current_user.department", "current_user.can_view_all")


def _unit_cache_key(*args, **kwargs) -> str:
    return str(kwargs.get("org_unit_id") or (args[0] if args else ""))


class CreateOrgUnitRequest(BaseModel):
    name: str
    parent_id: str | None = None
    manager_user_id: str | None = None
    sort_order: int = 0
    type: str | None = None
    id: str | None = None


class MoveOrgUnitRequest(BaseModel):
    new_parent_id: str | None = None


class MembershipRequest(BaseModel):
    user_id: str
    org_unit_id: str
    membership_type: str = "primary"
    is_manager: bool = False


@router.get("/tree")
@cached(
    "admin:org:tree",
    ttl=settings.CACHE_TTL_ADMIN,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_org_tree(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """获取组织树"""
    return await service.get_org_tree(db)


async def _create_unit_handler(body: CreateOrgUnitRequest, current_user: User, db: AsyncSession):
    result = await service.create_org_unit(
        db,
        name=body.name,
        parent_id=body.parent_id,
        user_id=current_user.id,
        manager_user_id=body.manager_user_id,
        sort_order=body.sort_order,
        unit_id=body.id,
        unit_type=body.type,
    )
    await invalidate_admin_cache()
    return result


@router.post("/")
async def create_org_unit(
    body: CreateOrgUnitRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """创建组织单元"""
    return await _create_unit_handler(body, current_user, db)


@router.post("/units")
async def create_org_unit_alt(
    body: CreateOrgUnitRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """创建组织单元（/units 别名路由，与钉钉同步命名一致）"""
    return await _create_unit_handler(body, current_user, db)


@router.get("/units/{org_unit_id}/members")
@cached(
    "admin:org:members",
    ttl=settings.CACHE_TTL_ADMIN,
    scope_by=_CACHE_SCOPE,
    key_builder=_unit_cache_key,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def list_unit_members(
    org_unit_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_org_unit_members(db, org_unit_id)


@router.post("/memberships")
async def add_membership(
    body: MembershipRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await service.add_membership(
        db,
        user_id=body.user_id,
        org_unit_id=body.org_unit_id,
        membership_type=body.membership_type,
        is_manager=body.is_manager,
        actor_id=current_user.id,
    )
    await invalidate_admin_cache()
    return result


@router.delete("/memberships/{user_id}/{org_unit_id}")
async def remove_membership(
    user_id: str,
    org_unit_id: str,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await service.remove_membership(
        db,
        user_id=user_id,
        org_unit_id=org_unit_id,
        actor_id=current_user.id,
    )
    await invalidate_admin_cache()
    return result


@router.post("/sync-dingtalk")
async def sync_dingtalk(
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await service.sync_dingtalk_org(db, actor_id=current_user.id)
    await invalidate_admin_cache()
    return result


@router.delete("/{org_unit_id}")
async def delete_org_unit_api(
    org_unit_id: str,
    force: bool = Query(False, description="强制删除：子部门上移到父节点"),
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """删除组织单元。force=true 时子部门上移到父节点。"""
    result = await service.delete_org_unit(
        db,
        org_unit_id=org_unit_id,
        user_id=current_user.id,
        force=force,
    )
    await invalidate_admin_cache()
    return result


@router.put("/{org_unit_id}/move")
async def move_org_unit_api(
    org_unit_id: str,
    body: MoveOrgUnitRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """移动组织单元到新的父节点"""
    result = await service.move_org_unit(
        db,
        org_unit_id=org_unit_id,
        new_parent_id=body.new_parent_id,
        user_id=current_user.id,
    )
    await invalidate_admin_cache()
    return result
