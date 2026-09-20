"""ABAC 策略评估服务。

提供属性级访问控制的策略 CRUD 与运行时评估。
评估逻辑：
1. 查询所有匹配 resource_type + action + enabled 的策略
2. 按 priority 降序排列
3. 对每条策略：evaluate_condition(subject_condition, subject) AND
   evaluate_condition(resource_condition, resource) AND
   evaluate_condition(environment_condition, environment)
4. 第一条匹配的策略决定结果（effect = allow / deny）
5. 无匹配策略 / 策略表为空 → (False, "no_matching_policy") —— **fail-closed 默认拒绝**

RBAC 已在路由层 `require_role` / `get_current_user` 保底；ABAC 是对 RBAC 之上
的精细策略。未命中策略必须拒绝，否则"策略表空或写错就放行"=无防护。
"""

from __future__ import annotations

from datetime import datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approval.condition_engine import evaluate_condition
from app.approval.condition_schema import validate_condition_json
from app.auth.abac_models import AbacPolicy
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt


# ── 策略评估 ──

async def evaluate_abac(
    db: AsyncSession,
    resource_type: str,
    action: str,
    subject: dict,
    resource: dict,
    environment: dict,
) -> tuple[bool, str]:
    """评估 ABAC 策略。

    Args:
        db: 数据库 session
        resource_type: 资源类型（skill / datasource / execution）
        action: 操作类型（read / edit / execute / publish / export）
        subject: 用户属性 {"user_id", "role", "department", "org_units", ...}
        resource: 资源属性 {"skill_id", "risk_level", "data_sensitivity", ...}
        environment: 环境属性 {"is_production", "timestamp", "ip", ...}

    Returns:
        (allowed: bool, reason: str)
    """
    stmt = (
        select(AbacPolicy)
        .where(
            AbacPolicy.resource_type == resource_type,
            AbacPolicy.action == action,
            AbacPolicy.enabled == True,  # noqa: E712
        )
        .order_by(AbacPolicy.priority.desc(), AbacPolicy.id.asc())
    )
    result = await db.execute(stmt)
    policies = result.scalars().all()

    if not policies:
        logger.debug(
            "ABAC 策略表为空: resource={}:{} → 默认拒绝",
            resource_type, action,
        )
        return False, "no_policy_defined"

    for policy in policies:
        # 运行期重新校验策略条件格式，非法策略跳过（配合 C2 fail-closed 防止旧坏策略永久豁免）
        for field_name, cond in (
            ("subject_condition", policy.subject_condition),
            ("resource_condition", policy.resource_condition),
            ("environment_condition", policy.environment_condition),
        ):
            if cond is not None and validate_condition_json(cond):
                logger.warning(
                    "ABAC 策略条件非法，跳过: policy_id={} field={}",
                    policy.id, field_name,
                )
                break
        else:
            if (
                evaluate_condition(policy.subject_condition, subject)
                and evaluate_condition(policy.resource_condition, resource)
                and evaluate_condition(policy.environment_condition, environment)
            ):
                allowed = policy.effect == "allow"
                reason = f"policy:{policy.id}:{policy.name}"
                logger.debug(
                    "ABAC 策略命中: policy_id={}, effect={}, resource={}:{}",
                    policy.id, policy.effect, resource_type, action,
                )
                return allowed, reason

    # 无匹配策略 → fail-closed 默认拒绝
    logger.debug(
        "ABAC 无策略命中: resource={}:{} → 默认拒绝",
        resource_type, action,
    )
    return False, "no_matching_policy"


# ── 策略 CRUD ──

async def list_policies(
    db: AsyncSession,
    resource_type: str | None = None,
) -> list[dict]:
    """查询策略列表。"""
    stmt = select(AbacPolicy).order_by(AbacPolicy.priority.desc(), AbacPolicy.id.asc())
    if resource_type:
        stmt = stmt.where(AbacPolicy.resource_type == resource_type)
    result = await db.execute(stmt)
    return [_policy_to_dict(p) for p in result.scalars().all()]


async def get_policy(db: AsyncSession, policy_id: int) -> dict:
    """获取单条策略。"""
    result = await db.execute(select(AbacPolicy).where(AbacPolicy.id == policy_id))
    policy = result.scalar_one_or_none()
    if not policy:
        raise AppError("NOT_FOUND", 404)
    return _policy_to_dict(policy)


async def create_policy(db: AsyncSession, data: dict, user_id: str) -> dict:
    """创建策略。"""
    # 校验条件格式
    for field in ("subject_condition", "resource_condition", "environment_condition"):
        errors = validate_condition_json(data.get(field))
        if errors:
            raise AppError("PARAM_INVALID", 400, detail={"field": field, "errors": errors})

    # 校验 effect 值
    if data.get("effect", "deny") not in ("allow", "deny"):
        raise AppError("PARAM_INVALID", 400, detail={"field": "effect", "errors": ["effect 必须是 allow 或 deny"]})

    policy = AbacPolicy(
        name=data["name"],
        description=data.get("description"),
        resource_type=data["resource_type"],
        action=data["action"],
        effect=data.get("effect", "deny"),
        priority=data.get("priority", 0),
        subject_condition=data.get("subject_condition"),
        resource_condition=data.get("resource_condition"),
        environment_condition=data.get("environment_condition"),
        enabled=data.get("enabled", True),
        created_by=user_id,
    )
    db.add(policy)
    await db.flush()
    logger.info("ABAC 策略创建: id={}, name={}, by={}", policy.id, policy.name, user_id)
    return _policy_to_dict(policy)


async def update_policy(db: AsyncSession, policy_id: int, data: dict) -> dict:
    """更新策略。"""
    result = await db.execute(select(AbacPolicy).where(AbacPolicy.id == policy_id))
    policy = result.scalar_one_or_none()
    if not policy:
        raise AppError("NOT_FOUND", 404)

    # 校验条件格式
    for field in ("subject_condition", "resource_condition", "environment_condition"):
        if field in data:
            errors = validate_condition_json(data[field])
            if errors:
                raise AppError("PARAM_INVALID", 400, detail={"field": field, "errors": errors})

    # 校验 effect 值
    if "effect" in data and data["effect"] not in ("allow", "deny"):
        raise AppError("PARAM_INVALID", 400, detail={"field": "effect", "errors": ["effect 必须是 allow 或 deny"]})

    updatable = (
        "name", "description", "resource_type", "action", "effect",
        "priority", "subject_condition", "resource_condition",
        "environment_condition", "enabled",
    )
    for key in updatable:
        if key in data:
            setattr(policy, key, data[key])

    policy.updated_at = now_bjt()
    await db.flush()
    logger.info("ABAC 策略更新: id={}", policy_id)
    return _policy_to_dict(policy)


async def delete_policy(db: AsyncSession, policy_id: int) -> None:
    """删除策略。"""
    result = await db.execute(select(AbacPolicy).where(AbacPolicy.id == policy_id))
    policy = result.scalar_one_or_none()
    if not policy:
        raise AppError("NOT_FOUND", 404)

    await db.delete(policy)
    await db.flush()
    logger.info("ABAC 策略删除: id={}", policy_id)


# ── 辅助 ──

def _policy_to_dict(policy: AbacPolicy) -> dict:
    """ORM → dict（API 响应）。"""
    return {
        "id": policy.id,
        "name": policy.name,
        "description": policy.description,
        "resource_type": policy.resource_type,
        "action": policy.action,
        "effect": policy.effect,
        "priority": policy.priority,
        "subject_condition": policy.subject_condition,
        "resource_condition": policy.resource_condition,
        "environment_condition": policy.environment_condition,
        "enabled": policy.enabled,
        "created_by": policy.created_by,
        "created_at": isoformat_bjt(policy.created_at),
        "updated_at": isoformat_bjt(policy.updated_at),
    }
