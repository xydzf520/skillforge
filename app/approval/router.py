"""审批配置 API。"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_role
from app.auth.models import User
from app.database import get_db

from . import service
from . import quota_service

router = APIRouter()


class ApprovalRuleRequest(BaseModel):
    id: str
    business_type: str
    condition_json: dict | None = None
    approval_chain: list | None = None
    enabled: bool = True
    priority: int = 0


class ApprovalRuleUpdateRequest(BaseModel):
    business_type: str | None = None
    condition_json: dict | None = None
    approval_chain: list | None = None
    enabled: bool | None = None
    priority: int | None = None


class ApprovalInstanceRequest(BaseModel):
    business_type: str
    business_id: str
    payload: dict | None = None
    dingtalk_process_id: str | None = None
    create_dingtalk: bool = True


class ApprovalDecisionRequest(BaseModel):
    step_order: int
    decision: str
    comment: str = ""


class ResourceQuotaRequest(BaseModel):
    org_unit_id: str
    resource_type: str
    period: str = "monthly"
    quota_limit: int
    burst_limit: int | None = None
    enabled: bool = True


class ResourceQuotaUpdateRequest(BaseModel):
    quota_limit: int | None = None
    burst_limit: int | None = None
    enabled: bool | None = None


@router.get("/rules")
async def list_rules(
    business_type: str | None = Query(None),
    enabled: bool | None = Query(None),
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await service.list_rules(db, business_type=business_type, enabled=enabled)


@router.post("/rules")
async def create_rule(
    body: ApprovalRuleRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await service.create_rule(
        db,
        rule_id=body.id,
        business_type=body.business_type,
        condition_json=body.condition_json,
        approval_chain=body.approval_chain,
        enabled=body.enabled,
        priority=body.priority,
    )


@router.put("/rules/{rule_id}")
async def update_rule(
    rule_id: str,
    body: ApprovalRuleUpdateRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await service.update_rule(
        db,
        rule_id=rule_id,
        business_type=body.business_type,
        condition_json=body.condition_json,
        approval_chain=body.approval_chain,
        enabled=body.enabled,
        priority=body.priority,
    )


@router.post("/instances")
async def create_instance(
    body: ApprovalInstanceRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.create_instance(
        db,
        business_type=body.business_type,
        business_id=body.business_id,
        requester_id=current_user.id,
        payload=body.payload,
        dingtalk_process_id=body.dingtalk_process_id,
        create_dingtalk=body.create_dingtalk,
    )


@router.get("/instances/{instance_id}")
async def get_instance(
    instance_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.get_instance(db, instance_id, current_user=current_user)


@router.post("/instances/{instance_id}/decide")
async def decide_instance(
    instance_id: str,
    body: ApprovalDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await service.advance_instance(
        db,
        instance_id=instance_id,
        step_order=body.step_order,
        approver_id=current_user.id,
        decision=body.decision,
        comment=body.comment,
    )


@router.get("/quotas")
async def list_quotas(
    org_unit_id: str | None = Query(None),
    resource_type: str | None = Query(None),
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await quota_service.list_quotas(db, org_unit_id=org_unit_id, resource_type=resource_type)


@router.post("/quotas")
async def create_quota(
    body: ResourceQuotaRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await quota_service.create_quota(
        db,
        org_unit_id=body.org_unit_id,
        resource_type=body.resource_type,
        period=body.period,
        quota_limit=body.quota_limit,
        burst_limit=body.burst_limit,
        enabled=body.enabled,
    )


@router.put("/quotas/{quota_id}")
async def update_quota(
    quota_id: str,
    body: ResourceQuotaUpdateRequest,
    current_user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await quota_service.update_quota(
        db,
        quota_id=quota_id,
        quota_limit=body.quota_limit,
        burst_limit=body.burst_limit,
        enabled=body.enabled,
    )


@router.get("/quotas/usage")
async def quota_usage(
    org_unit_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await quota_service.get_quota_usage(db, org_unit_id=org_unit_id)
