"""数据治理业务逻辑：资产 / 产品 / 策略 / 契约 CRUD + 脱敏 + 合规检查"""

import re
from datetime import datetime

from loguru import logger
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import AppError
from app.datasources.governance_models import DataAsset, DataContract, DataPolicy, DataProduct
from app.common.time_utils import isoformat_bjt, now_bjt


# ═══════════════════════════ 数据资产 ═══════════════════════════

async def create_asset(db: AsyncSession, data: dict) -> dict:
    """创建数据资产"""
    asset_id = data.get("id", "").strip()
    if not asset_id or not re.match(r"^[a-zA-Z0-9_\-:.]+$", asset_id) or len(asset_id) > 100:
        raise AppError("PARAM_INVALID", 400, {"detail": "资产 ID 格式非法（字母/数字/短横线/下划线/冒号/点，最多100字符）"})

    # 检查重复
    existing = await db.execute(select(DataAsset.id).where(DataAsset.id == asset_id))
    if existing.scalar_one_or_none():
        raise AppError("PARAM_INVALID", 409, {"detail": f"资产 ID {asset_id} 已存在"})

    asset = DataAsset(
        id=asset_id,
        name=data["name"],
        description=data.get("description"),
        source_id=data.get("source_id"),
        asset_type=data.get("asset_type"),
        schema_json=data.get("schema_json"),
        owner_org_unit_id=data.get("owner_org_unit_id"),
        owner_user_id=data.get("owner_user_id"),
        sensitivity=data.get("sensitivity", "L2"),
        tags=data.get("tags"),
        status=data.get("status", "active"),
    )
    db.add(asset)
    await db.flush()
    logger.info("[data-governance] 创建资产: {}", asset_id)
    return _asset_to_dict(asset)


async def list_assets(
    db: AsyncSession,
    org_unit_id: str | None = None,
    sensitivity: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """分页查询数据资产"""
    q = select(DataAsset).where(DataAsset.status != "deleted")
    count_q = select(func.count()).select_from(DataAsset).where(DataAsset.status != "deleted")

    if org_unit_id:
        q = q.where(DataAsset.owner_org_unit_id == org_unit_id)
        count_q = count_q.where(DataAsset.owner_org_unit_id == org_unit_id)
    if sensitivity:
        q = q.where(DataAsset.sensitivity == sensitivity)
        count_q = count_q.where(DataAsset.sensitivity == sensitivity)

    total = (await db.execute(count_q)).scalar() or 0
    q = q.order_by(DataAsset.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(q)).scalars().all()
    return {"total": total, "page": page, "items": [_asset_to_dict(r) for r in rows]}


async def get_asset(db: AsyncSession, asset_id: str) -> dict:
    """获取单个资产详情"""
    row = (await db.execute(select(DataAsset).where(DataAsset.id == asset_id))).scalar_one_or_none()
    if not row:
        raise AppError("NOT_FOUND", 404, {"detail": f"数据资产 {asset_id} 不存在"})
    return _asset_to_dict(row)


def _asset_to_dict(a: DataAsset) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "description": a.description,
        "source_id": a.source_id,
        "asset_type": a.asset_type,
        "schema_json": a.schema_json,
        "owner_org_unit_id": a.owner_org_unit_id,
        "owner_user_id": a.owner_user_id,
        "sensitivity": a.sensitivity,
        "tags": a.tags,
        "status": a.status,
        "created_at": isoformat_bjt(a.created_at),
        "updated_at": isoformat_bjt(a.updated_at),
    }


# ═══════════════════════════ 数据产品 ═══════════════════════════

async def create_product(db: AsyncSession, data: dict) -> dict:
    """创建数据产品"""
    product_id = data.get("id", "").strip()
    if not product_id or not re.match(r"^[a-zA-Z0-9_\-:.]+$", product_id) or len(product_id) > 100:
        raise AppError("PARAM_INVALID", 400, {"detail": "产品 ID 格式非法"})

    existing = await db.execute(select(DataProduct.id).where(DataProduct.id == product_id))
    if existing.scalar_one_or_none():
        raise AppError("PARAM_INVALID", 409, {"detail": f"数据产品 ID {product_id} 已存在"})

    asset_ids = data.get("asset_ids", [])
    if not asset_ids:
        raise AppError("PARAM_INVALID", 400, {"detail": "数据产品必须关联至少一个资产"})

    product = DataProduct(
        id=product_id,
        name=data["name"],
        description=data.get("description"),
        asset_ids=asset_ids,
        output_schema=data.get("output_schema"),
        sla_json=data.get("sla_json"),
        access_mode=data.get("access_mode", "read"),
        published=data.get("published", False),
        owner_id=data.get("owner_id"),
    )
    db.add(product)
    await db.flush()
    logger.info("[data-governance] 创建产品: {}", product_id)
    return _product_to_dict(product)


async def list_products(
    db: AsyncSession,
    published_only: bool = True,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """分页查询数据产品"""
    q = select(DataProduct)
    count_q = select(func.count()).select_from(DataProduct)

    if published_only:
        q = q.where(DataProduct.published == True)  # noqa: E712
        count_q = count_q.where(DataProduct.published == True)  # noqa: E712

    total = (await db.execute(count_q)).scalar() or 0
    q = q.order_by(DataProduct.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(q)).scalars().all()
    return {"total": total, "page": page, "items": [_product_to_dict(r) for r in rows]}


async def subscribe_product(db: AsyncSession, product_id: str, skill_id: str) -> dict:
    """Skill 订阅数据产品 → 自动创建数据契约 + consumer_count+1"""
    product = (await db.execute(select(DataProduct).where(DataProduct.id == product_id))).scalar_one_or_none()
    if not product:
        raise AppError("NOT_FOUND", 404, {"detail": f"数据产品 {product_id} 不存在"})

    # 防重复订阅
    existing = await db.execute(
        select(DataContract.id).where(
            DataContract.product_id == product_id,
            DataContract.skill_id == skill_id,
            DataContract.status == "active",
        )
    )
    if existing.scalar_one_or_none():
        raise AppError("PARAM_INVALID", 409, {"detail": "该 Skill 已订阅此数据产品"})

    contract = DataContract(
        skill_id=skill_id,
        product_id=product_id,
        contract_type="consumer",
        access_level=product.access_mode or "read",
        status="active",
    )
    db.add(contract)

    # 消费者计数 +1
    product.consumer_count = (product.consumer_count or 0) + 1
    await db.flush()

    logger.info("[data-governance] Skill {} 订阅产品 {}", skill_id, product_id)
    return {"contract_id": contract.id, "product_id": product_id, "skill_id": skill_id}


def _product_to_dict(p: DataProduct) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "asset_ids": p.asset_ids,
        "output_schema": p.output_schema,
        "sla_json": p.sla_json,
        "access_mode": p.access_mode,
        "consumer_count": p.consumer_count,
        "published": p.published,
        "owner_id": p.owner_id,
        "created_at": isoformat_bjt(p.created_at),
        "updated_at": isoformat_bjt(p.updated_at),
    }


# ═══════════════════════════ 数据策略 ═══════════════════════════

async def create_policy(db: AsyncSession, data: dict) -> dict:
    """创建数据策略"""
    policy_type = data.get("policy_type", "")
    if policy_type not in ("masking", "export_limit", "access_control", "retention"):
        raise AppError("PARAM_INVALID", 400, {"detail": f"不支持的策略类型: {policy_type}"})

    target_type = data.get("target_type", "")
    if target_type not in ("asset", "product", "field"):
        raise AppError("PARAM_INVALID", 400, {"detail": f"不支持的目标类型: {target_type}"})

    rules_json = data.get("rules_json")
    if not rules_json or not isinstance(rules_json, dict):
        raise AppError("PARAM_INVALID", 400, {"detail": "rules_json 必须是非空 JSON 对象"})

    policy = DataPolicy(
        name=data["name"],
        description=data.get("description"),
        policy_type=policy_type,
        target_type=target_type,
        target_id=data["target_id"],
        rules_json=rules_json,
        enabled=data.get("enabled", True),
        priority=data.get("priority", 0),
        created_by=data.get("created_by"),
    )
    db.add(policy)
    await db.flush()
    logger.info("[data-governance] 创建策略: {} → {}/{}", policy.name, target_type, data["target_id"])
    return _policy_to_dict(policy)


async def list_policies(
    db: AsyncSession,
    target_type: str | None = None,
    target_id: str | None = None,
    policy_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """分页查询数据策略"""
    q = select(DataPolicy)
    count_q = select(func.count()).select_from(DataPolicy)

    if target_type:
        q = q.where(DataPolicy.target_type == target_type)
        count_q = count_q.where(DataPolicy.target_type == target_type)
    if target_id:
        q = q.where(DataPolicy.target_id == target_id)
        count_q = count_q.where(DataPolicy.target_id == target_id)
    if policy_type:
        q = q.where(DataPolicy.policy_type == policy_type)
        count_q = count_q.where(DataPolicy.policy_type == policy_type)

    total = (await db.execute(count_q)).scalar() or 0
    q = q.order_by(DataPolicy.priority.desc(), DataPolicy.created_at.desc())
    q = q.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(q)).scalars().all()
    return {"total": total, "page": page, "items": [_policy_to_dict(r) for r in rows]}


async def apply_masking(data: dict, asset_id: str, db: AsyncSession) -> dict:
    """对数据应用脱敏策略。查询 asset 关联的 masking 策略并执行。

    Args:
        data: 原始数据字典（key=字段名, value=字段值）
        asset_id: 数据资产 ID
        db: 数据库 session

    Returns:
        脱敏后的数据字典
    """
    # 查找该资产的 masking 策略（按优先级排序）
    policies = (await db.execute(
        select(DataPolicy).where(
            DataPolicy.target_type == "asset",
            DataPolicy.target_id == asset_id,
            DataPolicy.policy_type == "masking",
            DataPolicy.enabled == True,  # noqa: E712
        ).order_by(DataPolicy.priority.desc())
    )).scalars().all()

    if not policies:
        return data

    result = dict(data)
    for policy in policies:
        rules = policy.rules_json or {}
        fields = rules.get("fields", [])
        method = rules.get("method", "partial_mask")

        for field in fields:
            if field in result and result[field]:
                result[field] = _mask_value(str(result[field]), method)

    return result


def _mask_value(value: str, method: str) -> str:
    """按脱敏方法处理字段值"""
    if not value:
        return value

    if method == "full_mask":
        return "***"
    elif method == "partial_mask":
        # 保留首尾各1字符，中间用 * 替代
        if len(value) <= 2:
            return "*" * len(value)
        return value[0] + "*" * (len(value) - 2) + value[-1]
    elif method == "hash":
        import hashlib
        return hashlib.sha256(value.encode()).hexdigest()[:16]
    else:
        # 默认部分脱敏
        if len(value) <= 2:
            return "*" * len(value)
        return value[0] + "*" * (len(value) - 2) + value[-1]


def _policy_to_dict(p: DataPolicy) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "policy_type": p.policy_type,
        "target_type": p.target_type,
        "target_id": p.target_id,
        "rules_json": p.rules_json,
        "enabled": p.enabled,
        "priority": p.priority,
        "created_by": p.created_by,
        "created_at": isoformat_bjt(p.created_at),
    }


# ═══════════════════════════ 数据契约 ═══════════════════════════

async def create_contract(
    db: AsyncSession,
    skill_id: str,
    product_id: str | None = None,
    asset_id: str | None = None,
    fields_used: list[str] | None = None,
    sla_requirement: dict | None = None,
    contract_type: str = "consumer",
    access_level: str = "read",
) -> dict:
    """创建数据契约"""
    if not product_id and not asset_id:
        raise AppError("PARAM_INVALID", 400, {"detail": "product_id 和 asset_id 至少需要提供一个"})

    contract = DataContract(
        skill_id=skill_id,
        product_id=product_id,
        asset_id=asset_id,
        contract_type=contract_type,
        fields_used=fields_used,
        access_level=access_level,
        sla_requirement=sla_requirement,
        status="active",
    )
    db.add(contract)
    await db.flush()
    logger.info("[data-governance] 创建契约: skill={} product={} asset={}", skill_id, product_id, asset_id)
    return _contract_to_dict(contract)


async def list_contracts(
    db: AsyncSession,
    skill_id: str | None = None,
    product_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """分页查询数据契约"""
    q = select(DataContract)
    count_q = select(func.count()).select_from(DataContract)

    if skill_id:
        q = q.where(DataContract.skill_id == skill_id)
        count_q = count_q.where(DataContract.skill_id == skill_id)
    if product_id:
        q = q.where(DataContract.product_id == product_id)
        count_q = count_q.where(DataContract.product_id == product_id)

    total = (await db.execute(count_q)).scalar() or 0
    q = q.order_by(DataContract.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(q)).scalars().all()
    return {"total": total, "page": page, "items": [_contract_to_dict(r) for r in rows]}


async def check_contract_compliance(db: AsyncSession, skill_id: str) -> dict:
    """检查 Skill 的数据契约合规性：字段是否仍存在、SLA 是否满足等。

    Returns:
        {
            "skill_id": "...",
            "compliant": True/False,
            "issues": [{"contract_id": 1, "type": "...", "message": "..."}],
            "checked_at": "..."
        }
    """
    contracts = (await db.execute(
        select(DataContract).where(
            DataContract.skill_id == skill_id,
            DataContract.status == "active",
        )
    )).scalars().all()

    if not contracts:
        return {
            "skill_id": skill_id,
            "compliant": True,
            "issues": [],
            "contracts_count": 0,
            "checked_at": isoformat_bjt(now_bjt()),
        }

    issues = []
    for contract in contracts:
        # 检查关联的资产是否仍存在且 active
        if contract.asset_id:
            asset = (await db.execute(
                select(DataAsset).where(DataAsset.id == contract.asset_id)
            )).scalar_one_or_none()
            if not asset:
                issues.append({
                    "contract_id": contract.id,
                    "type": "asset_missing",
                    "message": f"契约关联的资产 {contract.asset_id} 不存在",
                })
            elif asset.status != "active":
                issues.append({
                    "contract_id": contract.id,
                    "type": "asset_inactive",
                    "message": f"资产 {contract.asset_id} 状态为 {asset.status}",
                })
            elif contract.fields_used and asset.schema_json:
                # 检查字段是否仍在 schema 中
                schema_fields = {f.get("name") for f in asset.schema_json if isinstance(f, dict)}
                missing = [f for f in contract.fields_used if f not in schema_fields]
                if missing:
                    issues.append({
                        "contract_id": contract.id,
                        "type": "fields_missing",
                        "message": f"字段 {', '.join(missing)} 在资产 schema 中不存在",
                    })

        # 检查关联的产品是否仍存在
        if contract.product_id:
            product = (await db.execute(
                select(DataProduct).where(DataProduct.id == contract.product_id)
            )).scalar_one_or_none()
            if not product:
                issues.append({
                    "contract_id": contract.id,
                    "type": "product_missing",
                    "message": f"契约关联的产品 {contract.product_id} 不存在",
                })
            elif not product.published:
                issues.append({
                    "contract_id": contract.id,
                    "type": "product_unpublished",
                    "message": f"产品 {contract.product_id} 尚未发布",
                })

    return {
        "skill_id": skill_id,
        "compliant": len(issues) == 0,
        "issues": issues,
        "contracts_count": len(contracts),
        "checked_at": isoformat_bjt(now_bjt()),
    }


def _contract_to_dict(c: DataContract) -> dict:
    return {
        "id": c.id,
        "skill_id": c.skill_id,
        "product_id": c.product_id,
        "asset_id": c.asset_id,
        "contract_type": c.contract_type,
        "fields_used": c.fields_used,
        "access_level": c.access_level,
        "sla_requirement": c.sla_requirement,
        "status": c.status,
        "approved_by": c.approved_by,
        "created_at": isoformat_bjt(c.created_at),
        "updated_at": isoformat_bjt(c.updated_at),
    }
