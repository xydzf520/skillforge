"""ABAC 策略管理 API 路由（仅 admin 可操作）"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_role
from app.auth.models import User
from app.database import get_db

router = APIRouter()


# ── 请求模型 ──

class PolicyCreate(BaseModel):
    name: str = Field(..., max_length=100, description="策略名称")
    description: str | None = Field(None, description="策略描述")
    resource_type: str = Field(..., max_length=50, description="资源类型: skill / datasource / execution")
    action: str = Field(..., max_length=50, description="操作类型: read / edit / execute / publish / export")
    effect: str = Field("deny", description="效果: allow / deny")
    priority: int = Field(0, description="优先级（高优先）")
    subject_condition: dict | None = Field(None, description="用户属性条件")
    resource_condition: dict | None = Field(None, description="资源属性条件")
    environment_condition: dict | None = Field(None, description="环境条件")
    enabled: bool = Field(True, description="是否启用")


class PolicyUpdate(BaseModel):
    name: str | None = Field(None, max_length=100)
    description: str | None = None
    resource_type: str | None = Field(None, max_length=50)
    action: str | None = Field(None, max_length=50)
    effect: str | None = None
    priority: int | None = None
    subject_condition: dict | None = None
    resource_condition: dict | None = None
    environment_condition: dict | None = None
    enabled: bool | None = None


# ── 路由 ──

@router.get("/policies")
async def list_policies(
    resource_type: str | None = Query(None, description="按资源类型过滤"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """查询 ABAC 策略列表"""
    from app.auth.abac_service import list_policies as svc_list
    return await svc_list(db, resource_type=resource_type)


@router.get("/policies/{policy_id}")
async def get_policy(
    policy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """获取单条 ABAC 策略"""
    from app.auth.abac_service import get_policy as svc_get
    return await svc_get(db, policy_id)


@router.post("/policies")
async def create_policy(
    body: PolicyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """创建 ABAC 策略"""
    from app.auth.abac_service import create_policy as svc_create
    return await svc_create(db, body.model_dump(), current_user.id)


@router.put("/policies/{policy_id}")
async def update_policy(
    policy_id: int,
    body: PolicyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """更新 ABAC 策略"""
    from app.auth.abac_service import update_policy as svc_update
    # 只传有值的字段
    data = body.model_dump(exclude_unset=True)
    return await svc_update(db, policy_id, data)


@router.delete("/policies/{policy_id}")
async def delete_policy(
    policy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    """删除 ABAC 策略"""
    from app.auth.abac_service import delete_policy as svc_delete
    await svc_delete(db, policy_id)
    return {"ok": True}
