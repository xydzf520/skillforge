"""v2.8.2 D5：architect/synthesize 的契约测试。

覆盖 LLM 返回各种"坏形态"时，后端不要 500、不要吞错，要明确报告失败
（`result.skill._error` 或 `result.can_publish=False`），让前端可以给用户"重试"提示。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.workbench.skill_pipeline import run_pipeline


@pytest.mark.asyncio
async def test_synthesize_empty_dict():
    """LLM 返回 {} （没 meta 字段）→ 走 _compute 的 early-return。"""
    with patch(
        "app.workbench.skill_pipeline.synthesize_skill_from_interview",
        new=AsyncMock(return_value={}),
    ):
        with patch(
            "app.workbench.skill_pipeline.find_similar_skills",
            new=AsyncMock(return_value=[]),
        ):
            result = await run_pipeline(None, "test description", {})  # type: ignore[arg-type]

    assert result.can_publish is False
    assert "未返回有效" in result.summary or "失败" in result.summary


@pytest.mark.asyncio
async def test_synthesize_returns_none():
    """LLM 完全失败返回 None → 同样不 500。"""
    with patch(
        "app.workbench.skill_pipeline.synthesize_skill_from_interview",
        new=AsyncMock(return_value=None),
    ):
        with patch(
            "app.workbench.skill_pipeline.find_similar_skills",
            new=AsyncMock(return_value=[]),
        ):
            result = await run_pipeline(None, "test description", {})  # type: ignore[arg-type]

    assert result.can_publish is False
    assert "LLM 未返回" in result.summary or "失败" in result.summary


@pytest.mark.asyncio
async def test_synthesize_raises_exception():
    """LLM 异常被 catch，错误类型暴露给前端。"""
    with patch(
        "app.workbench.skill_pipeline.synthesize_skill_from_interview",
        new=AsyncMock(side_effect=TimeoutError("upstream timeout")),
    ):
        with patch(
            "app.workbench.skill_pipeline.find_similar_skills",
            new=AsyncMock(return_value=[]),
        ):
            result = await run_pipeline(None, "test description", {})  # type: ignore[arg-type]

    assert result.can_publish is False
    # v2.8.1 加的 _error 字段应出现
    assert result.skill.get("_error") is not None
    assert result.skill["_error"]["type"] == "TimeoutError"


@pytest.mark.asyncio
async def test_synthesize_happy_path():
    """正常返回完整骨架时，can_publish 基于 lint_report。"""
    valid_skill = {
        "meta": {"name": "test", "department": "测试部"},
        "goal": "test goal",
        "rules": [
            {
                "id": "step_1",
                "name": "step",
                "branches": [
                    {"condition": "x > 0", "conclusion": "positive", "action": "log"},
                    {"condition": "其他", "conclusion": "negative", "action": "skip"},
                ],
            }
        ],
        "test_cases": [],
        "antipatterns": [],
        "output_table": [],
        "data_inputs": [],
    }
    # mock lint 通过 —— 直接 mock to_dict 回返成"通过"结果，避开 LintReport dataclass 构造细节
    class _FakeLint:
        passed = True
        error_count = 0

        def to_dict(self):
            return {"passed": True, "error_count": 0, "issues": []}

    fake_lint = _FakeLint()

    with patch(
        "app.workbench.skill_pipeline.synthesize_skill_from_interview",
        new=AsyncMock(return_value=valid_skill),
    ), patch(
        "app.workbench.skill_pipeline.find_similar_skills",
        new=AsyncMock(return_value=[]),
    ), patch(
        "app.workbench.skill_pipeline.lint_skill",
        return_value=fake_lint,
    ):
        result = await run_pipeline(None, "test", {})  # type: ignore[arg-type]

    assert result.skill == valid_skill
    assert result.can_publish is True
    assert "通过质检" in result.summary


@pytest.mark.asyncio
async def test_synthesize_missing_meta_field():
    """LLM 返回 dict 但缺 meta 字段（常见 schema drift）→ 报 _error。"""
    partial = {"goal": "x", "rules": []}  # 没 meta
    with patch(
        "app.workbench.skill_pipeline.synthesize_skill_from_interview",
        new=AsyncMock(return_value=partial),
    ):
        with patch(
            "app.workbench.skill_pipeline.find_similar_skills",
            new=AsyncMock(return_value=[]),
        ):
            result = await run_pipeline(None, "test", {})  # type: ignore[arg-type]

    # 现在的实现：isinstance dict + get('meta') 为空 → summary 说失败
    assert result.can_publish is False
    assert "LLM 未返回" in result.summary or "失败" in result.summary
