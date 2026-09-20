"""M2 审批钉钉联通测试。"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_create_instance_creates_dingtalk_process_when_users_configured(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.config import settings

    settings.DINGTALK_APPROVAL_PROCESS_CODE_L2 = "PROC_L2"
    async with async_session_factory() as session:
        session.add(
            User(
                id="admin",
                username="admin",
                name="管理员",
                role="admin",
                department="总部",
                dingtalk_user_id="dt_admin",
                is_active=True,
                must_change_password=False,
            )
        )
        session.add(
            User(
                id="lead-1",
                username="lead-1",
                name="负责人",
                role="operator",
                department="总部",
                dingtalk_user_id="dt_lead_1",
                is_active=True,
                must_change_password=False,
            )
        )
        await session.commit()

    await client.post("/api/approval/rules", json={
        "id": "rule-dt",
        "business_type": "skill_publish",
        "condition_json": {"risk_level": "high"},
        "approval_chain": [{"approver_id": "lead-1"}],
        "enabled": True,
        "priority": 1,
    })

    with patch("app.approval.service.dingtalk_client.create_approval", new=AsyncMock(return_value={"ok": True, "instance_id": "dt-process-1"})):
        resp = await client.post("/api/approval/instances", json={
            "business_type": "skill_publish",
            "business_id": "skill-1",
            "payload": {"risk_level": "high"},
        })

    assert resp.status_code == 200
    assert resp.json()["dingtalk_process_id"] == "dt-process-1"


@pytest.mark.asyncio
async def test_dingtalk_callback_advances_approval_instance(client):
    from app.auth.models import User
    from app.database import async_session_factory
    from app.approval.models import ApprovalInstance, ApprovalStep
    from app.dingtalk.approval import handle_approval_result

    async with async_session_factory() as session:
        session.add(
            User(
                id="admin",
                username="admin",
                name="管理员",
                role="admin",
                department="总部",
                is_active=True,
                must_change_password=False,
            )
        )
        session.add(
            User(
                id="approver-1",
                username="approver-1",
                name="审批人",
                role="operator",
                department="总部",
                is_active=True,
                must_change_password=False,
            )
        )
        session.add(
            ApprovalInstance(
                id="ai-test-1",
                business_type="skill_publish",
                business_id="skill-2",
                requester_id="admin",
                status="pending",
                current_step=1,
                dingtalk_process_id="dt-process-2",
            )
        )
        session.add(
            ApprovalStep(
                id="as-test-1",
                instance_id="ai-test-1",
                step_order=1,
                approver_id="approver-1",
                status="pending",
            )
        )
        await session.commit()

    await handle_approval_result("dt-process-2", "agree")

    async with async_session_factory() as session:
        instance = await session.get(ApprovalInstance, "ai-test-1")
        step = await session.get(ApprovalStep, "as-test-1")

    assert instance is not None
    assert step is not None
    assert instance.status == "approved"
    assert step.status == "approved"
