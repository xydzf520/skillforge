import pytest
from sqlalchemy import select

from app.common.audit import AuditLog


@pytest.mark.asyncio
async def test_skill_test_writes_gate_audit(client, monkeypatch):
    async def fake_run_test_cases(skill_id, case_ids):
        return {"total": 1, "passed": 1, "failed": 0, "results": []}

    monkeypatch.setattr("app.testing.router.run_test_cases", fake_run_test_cases)

    resp = await client.post("/api/skills/customer-need-analysis/test", json={"run_all": True})

    assert resp.status_code == 200
    import app.database as db_mod

    async with db_mod.async_session_factory() as session:
        row = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "testing.run",
                    AuditLog.target_type == "skill",
                    AuditLog.target_id == "customer-need-analysis",
                )
            )
        ).scalar_one()
    assert row.user_id == "admin"
    assert row.detail["passed"] == 1
