"""Skill parser 中 reviewer / decision_mode 字段的归一化与校验单测。"""

from __future__ import annotations

import pytest

from app.common.exceptions import AppError
from app.skills.core.parser import SkillParser


def _build_md(reviewer_block: str = "", decision_mode: str = "") -> str:
    parts = [
        "---",
        "name: 测试 Skill",
    ]
    if reviewer_block:
        parts.append(reviewer_block)
    if decision_mode:
        parts.append(f"decision_mode: {decision_mode}")
    parts.append("---")
    parts.append("")
    parts.append("# 测试 Skill")
    parts.append("")
    parts.append("## 目的")
    parts.append("内容")
    return "\n".join(parts)


def test_reviewer_string_normalized_to_list():
    parser = SkillParser()
    md = _build_md("reviewer: alice")
    parsed = parser.parse(md)
    assert parsed.frontmatter["reviewer"] == ["alice"]


def test_reviewer_list_keeps_strings():
    parser = SkillParser()
    md = _build_md("reviewer:\n  - alice\n  - bob")
    parsed = parser.parse(md)
    assert parsed.frontmatter["reviewer"] == ["alice", "bob"]


def test_reviewer_invalid_type_raises():
    parser = SkillParser()
    md = _build_md("reviewer: 123")  # int
    with pytest.raises(AppError) as exc:
        parser.parse(md)
    assert exc.value.code == "SKILL_FRONTMATTER_INVALID"


def test_decision_mode_valid_keeps_value():
    parser = SkillParser()
    md = _build_md(decision_mode="all_of")
    parsed = parser.parse(md)
    assert parsed.frontmatter["decision_mode"] == "all_of"


def test_decision_mode_invalid_raises():
    parser = SkillParser()
    md = _build_md(decision_mode="random")
    with pytest.raises(AppError) as exc:
        parser.parse(md)
    assert exc.value.code == "SKILL_FRONTMATTER_INVALID"


def test_reviewer_role_must_be_string():
    parser = SkillParser()
    md = "---\nname: x\nreviewer_role:\n  - foo\n---\n# x\n## 目的\n\nx"
    with pytest.raises(AppError) as exc:
        parser.parse(md)
    assert exc.value.code == "SKILL_FRONTMATTER_INVALID"
