from pathlib import Path

import pytest


def test_skill_creation_prompt_requires_reports_default():
    import app.coding_agent.skill_creation_runner as mod

    assert 'reports 是默认能力' in mod.SKILL_CREATION_SYSTEM_PROMPT
    assert '"required": ["<必须包含的 key 列表，必须和 main.py return dict 一字不差对齐>", "reports"]' in mod.SKILL_CREATION_SYSTEM_PROMPT


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_removed_runtime_preserves_drafts_without_retry(monkeypatch, tmp_path, existing):
    from unittest.mock import AsyncMock
    import app.coding_agent.skill_creation_runner as mod
    from app.coding_agent.runtime_availability import RUNTIME_UNAVAILABLE

    scratch = tmp_path / "draft"
    if existing:
        scratch.mkdir()
        (scratch / "SKILL.md").write_text("user draft")
    monkeypatch.setattr(mod, "_scratch_dir_for", lambda _draft: scratch)
    credentials = AsyncMock(side_effect=AssertionError("no credential reads"))
    sleep = AsyncMock(side_effect=AssertionError("no retry"))
    monkeypatch.setattr(mod.SessionService, "_build_subprocess_env_provider_model", credentials)
    monkeypatch.setattr(mod.asyncio, "sleep", sleep)
    events = [ev async for ev in mod.run_skill_creation(message="new skill", draft_id="demo")]
    assert [ev.type for ev in events] == [mod.CreationEventType.ERROR, mod.CreationEventType.DONE]
    assert events[0].payload["code"] == RUNTIME_UNAVAILABLE
    assert events[1].payload["status"] == "error"
    if existing:
        assert (scratch / "SKILL.md").read_text() == "user draft"
    else:
        assert not scratch.exists()
    credentials.assert_not_called()
    sleep.assert_not_called()


def test_user_message_template_repair_mode_keeps_existing_files():
    import app.coding_agent.skill_creation_runner as mod

    previous_error = mod.SkillCreationError(
        code="CONTRACT_SCHEMA_DRIFT",
        detail="main.py return 缺字段 ['reports']",
        scratch_dir=Path("/tmp/draft-x"),
    )

    message = mod._user_message_template("原始 SOP", previous_error)

    assert message.startswith("⚠️ 这是返工修复模式")
    assert "禁止从零重建整个 Skill" in message
    assert "只修改校验错误涉及的文件" in message
    assert "原始 SOP" in message


@pytest.mark.asyncio
async def test_run_skill_creation_rate_limit_retry_uses_backoff(monkeypatch, tmp_path):
    import app.coding_agent.skill_creation_runner as mod

    sleep_calls: list[int] = []

    async def fake_sleep(seconds: int):
        sleep_calls.append(seconds)

    async def fake_run_one_attempt(
        *,
        message: str,
        draft_id: str,
        attempt: int,
        scratch_dir: Path,
        previous_error,
        user_id: str | None = None,
    ):
        if attempt < 3:
            raise mod.SkillCreationError(
                code="RATE_LIMITED",
                detail="glm/glm-5.1 请求被限流",
                scratch_dir=scratch_dir,
            )
        yield mod.CreationEvent(
            mod.CreationEventType.SKILL_READY,
            {"contract": {}, "files": {}},
        )

    monkeypatch.setattr(mod, "_run_one_attempt", fake_run_one_attempt)
    monkeypatch.setattr(mod, "_scratch_dir_for", lambda draft_id: tmp_path / draft_id)
    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
    # _retry_delay_for 加了 jitter 防 thundering herd，测试里固定归零只验 base
    monkeypatch.setattr("random.randint", lambda a, b: 0)

    events = [
        ev
        async for ev in mod.run_skill_creation(
            message="生成一个技能",
            draft_id="draft-backoff",
        )
    ]

    retry_events = [ev for ev in events if ev.type == mod.CreationEventType.RETRY]

    assert sleep_calls == [15, 30]
    assert [ev.payload["retry_in_s"] for ev in retry_events] == [15, 30]
    assert events[-1].type == mod.CreationEventType.DONE
    assert events[-1].payload["status"] == "ok"
