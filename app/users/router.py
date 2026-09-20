"""用户管理API路由（system_admin 全通，dept_admin 按本部门受限操作）"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.cache import cached, invalidate_admin_cache
from app.config import settings
from app.database import get_db
from app.users import service

router = APIRouter()

_CACHE_KEY_EXCLUDES = frozenset({"db", "session", "request", "background_tasks", "current_user"})
_CACHE_SCOPE = ("current_user.id", "current_user.role", "current_user.department", "current_user.can_view_all")


def _user_detail_cache_key(*args, **kwargs) -> str:
    return str(kwargs.get("user_id") or (args[0] if args else ""))


class CreateUserRequest(BaseModel):
    user_id: str
    username: str
    name: str
    role: str
    department: str | None = None
    password: str | None = None


class UpdateUserRequest(BaseModel):
    name: str | None = None
    role: str | None = None
    department: str | None = None
    is_active: bool | None = None
    can_view_all: bool | None = None
    email: str | None = None
    phone: str | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str


class UpdateUserStateRequest(BaseModel):
    state: str


class ActivatePendingUserRequest(BaseModel):
    """role-matrix-v2 §17.1 激活 pending 用户请求体"""

    role: str
    department_id: str
    is_manager: bool = False
    can_view_all: bool = False


@router.get("/")
@cached(
    "admin:users:list",
    ttl=settings.CACHE_TTL_ADMIN,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def list_users(
    role: str | None = Query(None),
    department: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    current_user: User = Depends(require_role("admin", "dept_admin")),
    db: AsyncSession = Depends(get_db),
):
    """用户列表"""
    return await service.list_users(db, role=role, department=department,
                                    page=page, page_size=page_size,
                                    operator=current_user)


@router.get("/{user_id}/detail")
@cached(
    "admin:users:detail",
    ttl=settings.CACHE_TTL_ADMIN,
    scope_by=_CACHE_SCOPE,
    key_builder=_user_detail_cache_key,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def get_user_detail(
    user_id: str,
    current_user: User = Depends(require_role("admin", "system_admin", "dept_admin")),
    db: AsyncSession = Depends(get_db),
):
    """单个用户详情（L3-E 用户详情抽屉用）。

    返回比列表更完整的字段：email / phone / state / permissions / can_view_all
    以及 Codex / 最近登录 / 最近活跃。permissions 按角色摘要推导，
    未来若引入显式权限表可在 service 层扩展。
    """
    return await service.get_user_detail(db, user_id, operator=current_user)


@router.post("/")
async def create_user(
    body: CreateUserRequest,
    current_user: User = Depends(require_role("admin", "dept_admin")),
    db: AsyncSession = Depends(get_db),
):
    """创建用户"""
    result = await service.create_user(
        db,
        user_id=body.user_id,
        username=body.username,
        name=body.name,
        role=body.role,
        department=body.department,
        password=body.password,
        operator_id=current_user.id,
    )
    await invalidate_admin_cache()
    return result


@router.put("/{user_id}")
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """更新用户信息"""
    result = await service.update_user(
        db,
        user_id=user_id,
        name=body.name,
        role=body.role,
        department=body.department,
        is_active=body.is_active,
        can_view_all=body.can_view_all,
        operator_id=current_user.id,
        operator=current_user,
    )
    await invalidate_admin_cache()
    return result


@router.post("/{user_id}/state")
async def update_user_state(
    user_id: str,
    body: UpdateUserStateRequest,
    current_user: User = Depends(require_role("admin", "dept_admin")),
    db: AsyncSession = Depends(get_db),
):
    """更新用户状态（active/disabled），保持 users.state 与 is_active 同步。"""
    result = await service.update_user_state(
        db,
        user_id=user_id,
        state=body.state,
        operator_id=current_user.id,
        operator=current_user,
    )
    await invalidate_admin_cache()
    return result


@router.post("/{user_id}/unlink-dingtalk")
async def unlink_dingtalk(
    user_id: str,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """解除用户的钉钉绑定。"""
    result = await service.unlink_dingtalk(db, user_id=user_id, operator_id=current_user.id)
    await invalidate_admin_cache()
    return result


@router.delete("/{user_id}")
async def disable_user(
    user_id: str,
    current_user: User = Depends(require_role("admin", "dept_admin")),
    db: AsyncSession = Depends(get_db),
):
    """禁用用户（软删除）"""
    result = await service.disable_user(db, user_id, operator_id=current_user.id, operator=current_user)
    await invalidate_admin_cache()
    return result


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: str,
    body: ResetPasswordRequest,
    current_user: User = Depends(require_role("admin", "dept_admin")),
    db: AsyncSession = Depends(get_db),
):
    """重置密码"""
    result = await service.reset_password(
        db, user_id, body.new_password, operator_id=current_user.id, operator=current_user,
    )
    await invalidate_admin_cache()
    return result


@router.post("/sync-dingtalk")
async def sync_dingtalk(
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """从钉钉组织架构同步用户"""
    result = await service.sync_dingtalk(db, operator_id=current_user.id)
    await invalidate_admin_cache()
    return result


@router.get("/pending")
@cached(
    "admin:users:pending",
    ttl=settings.CACHE_TTL_ADMIN,
    scope_by=_CACHE_SCOPE,
    key_excludes=_CACHE_KEY_EXCLUDES,
)
async def list_pending_users(
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """列出待激活（state='pending'）用户。

    role-matrix-v2 §17.1：
    - system_admin 看全局
    - dept_admin 看自己管辖部门内候选
    - 其他角色 → service 层抛 AUTH_PERMISSION_DENIED
    """
    return await service.list_pending_users(db, operator=current_user)


@router.post("/pending/{user_id}/activate")
async def activate_pending_user(
    user_id: str,
    body: ActivatePendingUserRequest,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """激活 pending 用户。

    role-matrix-v2 §9.2 / §17.1：
    - system_admin 可激活为四类 v2 角色
    - dept_admin 的 body.role ∈ {aibp, observer}
    - dept_admin 只能激活自己管辖部门内的 pending，且不能把 can_view_all 置 True
    """
    result = await service.activate_pending_user(
        db,
        user_id=user_id,
        new_role=body.role,
        org_unit_id=body.department_id,
        is_manager=body.is_manager,
        can_view_all=body.can_view_all,
        operator=current_user,
    )
    await invalidate_admin_cache()
    return result
