from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_training_datasets_list_readiness_without_raw_decision_payloads(client):
    from app.common.time_utils import now_bjt
    from app.database import async_session_factory
    from app.execution.models import DecisionLog
    from app.skills.core.models import Skill

    async with async_session_factory() as db:
        db.add(Skill(
            id="skill-recommend",
            name="商品推荐",
            department="EC",
            status="active",
            owner="admin",
            risk_level="R2",
        ))
        now = now_bjt()
        for index in range(120):
            db.add(DecisionLog(
                run_id=f"run-{index}",
                skill_id="skill-recommend",
                input_snapshot={"sku_id": index, "token": "raw-secret-should-not-leak"},
                output_result={"todos": [{"title": f"动作 {index}"}]},
                suggested_action={"type": "todo_dispatch", "rank": index % 3},
                user_action="rejected" if index < 10 else ("completed" if index < 60 else None),
                rating=4 if 10 <= index < 60 else None,
                is_sandbox=False,
                created_at=now,
            ))
        db.add(DecisionLog(
            run_id="sandbox-secret",
            skill_id="skill-recommend",
            input_snapshot={"secret": "sandbox-secret-should-not-count"},
            output_result={"todos": [{"title": "sandbox"}]},
            suggested_action={"type": "todo_dispatch"},
            is_sandbox=True,
            created_at=now,
        ))
        await db.commit()

    resp = await client.get("/api/training/datasets")
    assert resp.status_code == 200
    payload = resp.json()
    row = next(item for item in payload["items"] if item["skill_id"] == "skill-recommend")
    encoded = resp.text

    assert row["passed"] is True
    assert row["can_create_candidate"] is True
    assert row["dataset_ref"] == "decision-log://skill-recommend/action-outcome/latest"
    assert row["sample_counts"]["sft_samples"] == 120
    assert row["sample_counts"]["preference_samples"] == 60
    assert row["sample_counts"]["action_outcome_samples"] == 120
    assert row["sample_counts"]["eval_samples"] == 60
    assert payload["stats"]["ready"] >= 1
    assert payload["stats"]["action_outcome_samples"] >= 120
    assert "raw-secret-should-not-leak" not in encoded
    assert "sandbox-secret-should-not-count" not in encoded


@pytest.mark.asyncio
async def test_training_datasets_use_unified_skill_read_scope(client):
    from app.database import async_session_factory
    from app.skills.core.models import Skill
    from app.training.service import list_training_datasets

    async with async_session_factory() as db:
        db.add_all([
            Skill(
                id="skill-ec",
                name="EC Skill",
                department="EC",
                status="active",
                owner="ec-owner",
                visibility="department",
            ),
            Skill(
                id="skill-hr",
                name="HR Skill",
                department="HR",
                status="active",
                owner="hr-owner",
                visibility="department",
            ),
        ])
        await db.commit()

    user = SimpleNamespace(
        id="ec-user",
        role="observer",
        department="EC",
        can_view_all=False,
        is_active=True,
        state="active",
        permissions_rev=0,
    )
    async with async_session_factory() as db:
        result = await list_training_datasets(db, user)  # type: ignore[arg-type]

    ids = {item["skill_id"] for item in result["items"]}
    assert "skill-ec" in ids
    assert "skill-hr" not in ids
