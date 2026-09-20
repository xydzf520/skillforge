"""M2 审批配置模块测试。"""

import pytest


@pytest.mark.asyncio
async def test_create_and_list_approval_rules(client):
    create_resp = await client.post("/api/approval/rules", json={
        "id": "rule-skill-publish-high",
        "business_type": "skill_publish",
        "condition_json": {"risk_level": "high"},
        "approval_chain": [{"approver_id": "director-1"}, {"approver_id": "admin"}],
        "enabled": True,
        "priority": 10,
    })
    assert create_resp.status_code == 200

    list_resp = await client.get("/api/approval/rules?business_type=skill_publish")
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert data["total"] >= 1
    assert any(item["id"] == "rule-skill-publish-high" for item in data["items"])


@pytest.mark.asyncio
async def test_create_instance_matches_rule_and_builds_steps(client):
    await client.post("/api/approval/rules", json={
        "id": "rule-prod-exec",
        "business_type": "prod_execution",
        "condition_json": {"risk_level": "high"},
        "approval_chain": [{"approver_id": "lead-1"}, {"approver_id": "director-1"}],
        "enabled": True,
        "priority": 5,
    })

    resp = await client.post("/api/approval/instances", json={
        "business_type": "prod_execution",
        "business_id": "exec-001",
        "payload": {"risk_level": "high"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["business_id"] == "exec-001"
    assert data["rule_id"] == "rule-prod-exec"
    assert len(data["steps"]) == 2
    assert data["steps"][0]["approver_id"] == "lead-1"


@pytest.mark.asyncio
async def test_approval_instance_progression_to_approved(client):
    """进阶流程测试：用 service 层直连传入不同 approver_id 模拟不同用户。
    (新安全策略下 router 校验 current_user.id 匹配 step.approver_id，mock fixture 固定 admin 无法同时扮两个审批人。)
    """
    await client.post("/api/approval/rules", json={
        "id": "rule-data-access",
        "business_type": "data_access",
        "condition_json": {"sensitivity": "L3"},
        "approval_chain": [{"approver_id": "manager-1"}, {"approver_id": "director-1"}],
        "enabled": True,
        "priority": 20,
    })
    create_resp = await client.post("/api/approval/instances", json={
        "business_type": "data_access",
        "business_id": "grant-001",
        "payload": {"sensitivity": "L3"},
    })
    instance_id = create_resp.json()["id"]

    import app.database as db_mod
    from app.approval.service import advance_instance
    async with db_mod.async_session_factory() as session:
        r1 = await advance_instance(
            session, instance_id=instance_id, step_order=1,
            approver_id="manager-1", decision="approved", comment="ok",
        )
        await session.commit()
    assert r1["status"] == "pending"
    async with db_mod.async_session_factory() as session:
        r2 = await advance_instance(
            session, instance_id=instance_id, step_order=2,
            approver_id="director-1", decision="approved", comment="done",
        )
        await session.commit()
    assert r2["status"] == "approved"


@pytest.mark.asyncio
async def test_approval_instance_rejection_finishes_flow(client):
    await client.post("/api/approval/rules", json={
        "id": "rule-reject-path",
        "business_type": "skill_publish",
        "condition_json": {"risk_level": "medium"},
        "approval_chain": [{"approver_id": "reviewer-1"}],
        "enabled": True,
        "priority": 1,
    })
    create_resp = await client.post("/api/approval/instances", json={
        "business_type": "skill_publish",
        "business_id": "skill-xyz",
        "payload": {"risk_level": "medium"},
    })
    instance_id = create_resp.json()["id"]

    import app.database as db_mod
    from app.approval.service import advance_instance
    async with db_mod.async_session_factory() as session:
        data = await advance_instance(
            session, instance_id=instance_id, step_order=1,
            approver_id="reviewer-1", decision="rejected", comment="risk too high",
        )
        await session.commit()
    assert data["status"] == "rejected"
    assert data["steps"][0]["status"] == "rejected"


# === C1 / H3：身份校验与防自批 ===

@pytest.mark.asyncio
async def test_approval_decide_not_your_step(client):
    """current_user=admin，step.approver_id=other → 403 APPROVAL_NOT_YOUR_STEP。"""
    await client.post("/api/approval/rules", json={
        "id": "rule-not-your-step",
        "business_type": "t_not_your_step",
        "condition_json": None,
        "approval_chain": [{"approver_id": "other-user"}],
        "enabled": True,
        "priority": 1,
    })
    create = await client.post("/api/approval/instances", json={
        "business_type": "t_not_your_step",
        "business_id": "case-001",
    })
    instance_id = create.json()["id"]
    decide = await client.post(f"/api/approval/instances/{instance_id}/decide", json={
        "step_order": 1,
        "decision": "approved",
    })
    assert decide.status_code == 403
    assert decide.json()["error"]["code"] == "APPROVAL_NOT_YOUR_STEP"


@pytest.mark.asyncio
async def test_approval_requester_in_chain_rejected(client):
    """requester=admin 在链上 → create_instance 抛 APPROVAL_REQUESTER_IN_CHAIN。"""
    await client.post("/api/approval/rules", json={
        "id": "rule-self-approve",
        "business_type": "t_self_approve",
        "condition_json": None,
        "approval_chain": [{"approver_id": "admin"}],
        "enabled": True,
        "priority": 1,
    })
    resp = await client.post("/api/approval/instances", json={
        "business_type": "t_self_approve",
        "business_id": "case-002",
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "APPROVAL_REQUESTER_IN_CHAIN"


@pytest.mark.asyncio
async def test_approval_chain_duplicate_rejected(client):
    """create_rule 审批链中审批人重复 → 400 APPROVAL_CHAIN_DUPLICATE。"""
    resp = await client.post("/api/approval/rules", json={
        "id": "rule-dup-chain",
        "business_type": "t_dup_chain",
        "condition_json": None,
        "approval_chain": [{"approver_id": "a"}, {"approver_id": "b"}, {"approver_id": "a"}],
        "enabled": True,
        "priority": 1,
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "APPROVAL_CHAIN_DUPLICATE"


@pytest.mark.asyncio
async def test_approval_step_approver_unresolved(client):
    """step.approver_id=None（动态审批人未解析）→ 500 APPROVAL_STEP_APPROVER_UNRESOLVED。"""
    await client.post("/api/approval/rules", json={
        "id": "rule-no-approver",
        "business_type": "t_no_approver",
        "condition_json": None,
        "approval_chain": [{"other_field": "x"}],  # 无 approver_id
        "enabled": True,
        "priority": 1,
    })
    create = await client.post("/api/approval/instances", json={
        "business_type": "t_no_approver",
        "business_id": "case-003",
    })
    instance_id = create.json()["id"]
    decide = await client.post(f"/api/approval/instances/{instance_id}/decide", json={
        "step_order": 1,
        "decision": "approved",
    })
    assert decide.status_code == 500
    assert decide.json()["error"]["code"] == "APPROVAL_STEP_APPROVER_UNRESOLVED"
