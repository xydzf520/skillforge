"""数据治理 API 路由：资产 / 产品 / 策略 / 契约"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_role, require_state_active
from app.auth.models import User
from app.common.time_utils import isoformat_bjt
from app.database import get_db
from app.datasources import governance_service as gov_svc

router = APIRouter()


# ═══════════════════════════ 请求模型 ═══════════════════════════

class CreateAssetRequest(BaseModel):
    model_config = {"populate_by_name": True}

    id: str
    name: str
    description: str | None = None
    source_id: str | None = None
    asset_type: str | None = None  # table/api/file/stream
    schema_def: list[dict] | None = None  # 字段定义，映射到 ORM 的 schema_json
    owner_org_unit_id: str | None = None
    owner_user_id: str | None = None
    sensitivity: str = "L2"
    tags: list[str] | None = None


class CreateProductRequest(BaseModel):
    id: str
    name: str
    description: str | None = None
    asset_ids: list[str]
    output_schema: dict | None = None
    sla_json: dict | None = None
    access_mode: str = "read"
    published: bool = False
    owner_id: str | None = None


class CreatePolicyRequest(BaseModel):
    name: str
    description: str | None = None
    policy_type: str  # masking/export_limit/access_control/retention
    target_type: str  # asset/product/field
    target_id: str
    rules_json: dict
    enabled: bool = True
    priority: int = 0


class CreateContractRequest(BaseModel):
    skill_id: str
    product_id: str | None = None
    asset_id: str | None = None
    contract_type: str = "consumer"
    fields_used: list[str] | None = None
    access_level: str = "read"
    sla_requirement: dict | None = None


# ═══════════════════════════ 数据资产 ═══════════════════════════

@router.get("/assets")
async def list_assets(
    org_unit_id: str | None = Query(None),
    sensitivity: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """数据资产列表"""
    return await gov_svc.list_assets(db, org_unit_id=org_unit_id, sensitivity=sensitivity, page=page, page_size=page_size)


@router.post("/assets")
async def create_asset(
    body: CreateAssetRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """创建数据资产"""
    data = body.model_dump()
    # 映射 schema_def → schema_json（避免 Pydantic BaseModel 字段名冲突）
    data["schema_json"] = data.pop("schema_def", None)
    data["owner_user_id"] = data.get("owner_user_id") or current_user.id
    return await gov_svc.create_asset(db, data)


@router.get("/assets/{asset_id}")
async def get_asset(
    asset_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """资产详情"""
    return await gov_svc.get_asset(db, asset_id)


# ═══════════════════════════ 数据产品 ═══════════════════════════

@router.get("/products")
async def list_products(
    published_only: bool = Query(True),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """数据产品列表"""
    return await gov_svc.list_products(db, published_only=published_only, page=page, page_size=page_size)


@router.post("/products")
async def create_product(
    body: CreateProductRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """创建数据产品"""
    data = body.model_dump()
    data["owner_id"] = data.get("owner_id") or current_user.id
    return await gov_svc.create_product(db, data)


@router.get("/products/{product_id}")
async def get_product(
    product_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """产品详情"""
    # 直接复用 list_products 内的查询逻辑不合适，这里做单独查询
    from app.datasources.governance_models import DataProduct
    from sqlalchemy import select
    row = (await db.execute(select(DataProduct).where(DataProduct.id == product_id))).scalar_one_or_none()
    if not row:
        from app.common.exceptions import AppError
        raise AppError("NOT_FOUND", 404, {"detail": f"数据产品 {product_id} 不存在"})
    return {
        "id": row.id, "name": row.name, "description": row.description,
        "asset_ids": row.asset_ids, "output_schema": row.output_schema,
        "sla_json": row.sla_json, "access_mode": row.access_mode,
        "consumer_count": row.consumer_count, "published": row.published,
        "owner_id": row.owner_id,
        "created_at": isoformat_bjt(row.created_at),
        "updated_at": isoformat_bjt(row.updated_at),
    }


# ═══════════════════════════ 数据策略 ═══════════════════════════

@router.get("/policies")
async def list_policies(
    target_type: str | None = Query(None),
    target_id: str | None = Query(None),
    policy_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """数据策略列表"""
    return await gov_svc.list_policies(
        db, target_type=target_type, target_id=target_id,
        policy_type=policy_type, page=page, page_size=page_size,
    )


@router.post("/policies")
async def create_policy(
    body: CreatePolicyRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """创建数据策略"""
    data = body.model_dump()
    data["created_by"] = current_user.id
    return await gov_svc.create_policy(db, data)


# ═══════════════════════════ 数据契约 ═══════════════════════════

@router.get("/contracts")
async def list_contracts(
    skill_id: str | None = Query(None),
    product_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """数据契约列表"""
    return await gov_svc.list_contracts(db, skill_id=skill_id, product_id=product_id, page=page, page_size=page_size)


@router.post("/contracts")
async def create_contract(
    body: CreateContractRequest,
    current_user: User = Depends(require_role("admin", "ai_engineer")),
    db: AsyncSession = Depends(get_db),
):
    """创建数据契约"""
    return await gov_svc.create_contract(
        db,
        skill_id=body.skill_id,
        product_id=body.product_id,
        asset_id=body.asset_id,
        fields_used=body.fields_used,
        sla_requirement=body.sla_requirement,
        contract_type=body.contract_type,
        access_level=body.access_level,
    )


@router.get("/contracts/compliance/{skill_id}")
async def check_compliance(
    skill_id: str,
    current_user: User = Depends(require_state_active),
    db: AsyncSession = Depends(get_db),
):
    """检查 Skill 的数据契约合规性"""
    return await gov_svc.check_contract_compliance(db, skill_id)
