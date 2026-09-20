from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_run_coach_event_enriches_param_changed_context(monkeypatch):
    from app.workbench import coach_service
    import app.skills.param_index as param_index
    import app.workbench.coach as coach_module

    seen = {}

    async def fake_handle_event(event, structured, context=None):
        seen["event"] = event
        seen["structured"] = structured
        seen["context"] = context
        return SimpleNamespace(to_dict=lambda: {"ok": True, "context": context})

    monkeypatch.setattr(coach_service, "_load_structured_skill", lambda skill_id: {"skill_id": skill_id})
    monkeypatch.setattr(param_index, "find_param_usages", AsyncMock(return_value=[{"skill_id": "other-skill"}]))
    monkeypatch.setattr(coach_module, "handle_event", fake_handle_event)

    result = await coach_service.run_coach_event(
        skill_id="skill-a",
        event="param_changed",
        context={"param_name": "roi_ratio", "old_value": 1.0, "new_value": 1.3},
        db=object(),
    )

    assert result["ok"] is True
    assert seen["event"] == "param_changed"
    assert seen["structured"] == {"skill_id": "skill-a"}
    assert seen["context"]["cross_skill_usages"] == [{"skill_id": "other-skill"}]


@pytest.mark.asyncio
async def test_run_coach_event_records_repeated_edit(monkeypatch):
    from app.workbench import coach_service
    import app.workbench.coach as coach_module
    import app.common.telemetry as telemetry_module

    recorded = {}

    async def fake_handle_event(event, structured, context=None):
        return SimpleNamespace(to_dict=lambda: {"event": event})

    def fake_record_repeated_edit(location, count):
        recorded["location"] = location
        recorded["count"] = count

    monkeypatch.setattr(coach_service, "_load_structured_skill", lambda skill_id: {"skill_id": skill_id})
    monkeypatch.setattr(coach_module, "handle_event", fake_handle_event)
    monkeypatch.setattr(telemetry_module, "record_repeated_edit", fake_record_repeated_edit)

    result = await coach_service.run_coach_event(
        skill_id="skill-a",
        event="reverted_repeatedly",
        context={"location": "param:roi_ratio", "revert_count": 4},
        db=object(),
    )

    assert result == {"event": "reverted_repeatedly"}
    assert recorded == {"location": "param:roi_ratio", "count": 4}


def test_record_coach_accepted_records_telemetry(monkeypatch):
    from app.workbench import coach_service
    import app.common.telemetry as telemetry_module

    recorded = {}

    def fake_record_coach_suggestion(*, accepted, action):
        recorded["accepted"] = accepted
        recorded["action"] = action

    monkeypatch.setattr(telemetry_module, "record_coach_suggestion", fake_record_coach_suggestion)

    result = coach_service.record_coach_accepted("complete_branches")

    assert result == {"ok": True}
    assert recorded == {"accepted": True, "action": "complete_branches"}


def test_load_structured_skill_falls_back_when_skill_md_missing(monkeypatch):
    from app.workbench import coach_service
    from app.skills.core.parser import SkillStructured
    from app.skills.core.git_service import git_service

    monkeypatch.setattr(git_service, "read_file", lambda skill_id, path: "")

    result = coach_service._load_structured_skill("missing-md-skill")

    assert isinstance(result, SkillStructured)
    assert result.steps == []
