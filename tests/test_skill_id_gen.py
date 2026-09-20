"""app/skills/id_gen.py 单元测试（v2.8.0）。"""

from __future__ import annotations

import pytest

from app.skills.core.id_gen import (
    AUTO_PREFIXES,
    SKILL_ID_PATTERN,
    classify_skill_id,
    gen_for_test,
    gen_from_fork,
    gen_from_import,
    gen_from_name,
    is_e2e_test_id,
    validate_skill_id,
)
from app.common.exceptions import AppError


class TestGenFromName:
    def test_english(self):
        sid = gen_from_name("My Skill", "Marketing")
        assert sid.startswith("my-skill-")
        assert SKILL_ID_PATTERN.match(sid)

    def test_chinese_pinyin(self):
        sid = gen_from_name("投诉分级", "客服部")
        # 拼音化
        assert sid.startswith("tou-su-fen-ji-")
        assert SKILL_ID_PATTERN.match(sid)

    def test_mixed(self):
        sid = gen_from_name("营销 ROI 预警", "营销部")
        assert "roi" in sid.lower()
        assert SKILL_ID_PATTERN.match(sid)

    def test_empty_fallbacks(self):
        sid = gen_from_name("", "")
        assert sid.startswith("skill-")

    def test_unique(self):
        a = gen_from_name("同名", "部门")
        b = gen_from_name("同名", "部门")
        assert a != b


class TestGenFromImport:
    def test_format(self):
        sid = gen_from_import(department="客服部")
        assert sid.startswith("imp-")
        assert SKILL_ID_PATTERN.match(sid)
        # 含部门 abbr + uuid8
        parts = sid.split("-")
        assert len(parts) >= 3  # imp / {abbr} / {uuid}

    def test_no_department(self):
        sid = gen_from_import()
        assert sid.startswith("imp-dept-") or sid.startswith("imp-unkn-") or sid.startswith("imp-unk-")


class TestGenFromFork:
    def test_format(self):
        sid = gen_from_fork("parent-skill-id")
        assert sid.startswith("fork-")
        assert "parent-skill" in sid
        assert SKILL_ID_PATTERN.match(sid)


class TestGenForTest:
    def test_prefix(self):
        sid = gen_for_test()
        assert sid.startswith("test-")
        assert SKILL_ID_PATTERN.match(sid)


class TestValidation:
    def test_valid_english(self):
        assert validate_skill_id("my-skill-001") == "my-skill-001"

    def test_valid_chinese(self):
        assert validate_skill_id("EC-投放-01") == "EC-投放-01"

    def test_invalid_empty(self):
        with pytest.raises(AppError) as exc:
            validate_skill_id("")
        assert exc.value.code == "SKILL_ID_INVALID"

    def test_invalid_path_traversal(self):
        with pytest.raises(AppError):
            validate_skill_id("../evil")
        with pytest.raises(AppError):
            validate_skill_id("a/b")
        with pytest.raises(AppError):
            validate_skill_id("a\\b")

    def test_invalid_too_long(self):
        with pytest.raises(AppError):
            validate_skill_id("a" * 51)

    def test_invalid_special_chars(self):
        with pytest.raises(AppError):
            validate_skill_id("skill!")
        with pytest.raises(AppError):
            validate_skill_id("skill@dept")


class TestClassify:
    def test_import(self):
        assert classify_skill_id("imp-dept-abcdef12") == "import"

    def test_fork(self):
        assert classify_skill_id("fork-parent-a1b2c3") == "fork"

    def test_e2e_new(self):
        assert classify_skill_id("test-1234567890") == "e2e_test"

    def test_e2e_legacy_base36(self):
        # 老 e2e 格式：`imported-{Date.now().toString(36)}`
        assert classify_skill_id("imported-mo57w3iy") == "e2e_test"

    def test_real_import_legacy_hex(self):
        # 老 zip import 格式：`imported-{uuid4().hex[:8]}`
        assert classify_skill_id("imported-a1b2c3d4") == "import"

    def test_manual(self):
        assert classify_skill_id("EC-投放-01") == "manual"
        assert classify_skill_id("my-custom-skill") == "manual"

    def test_is_e2e_test_id(self):
        assert is_e2e_test_id("test-abc")
        assert is_e2e_test_id("imported-mo57w3iy")
        assert not is_e2e_test_id("imp-dept-abcdef12")
        assert not is_e2e_test_id("EC-投放-01")


def test_auto_prefixes_contract():
    """AUTO_PREFIXES 必须和 gen_* 函数的产出前缀保持一致。"""
    assert "imp-" in AUTO_PREFIXES
    assert "fork-" in AUTO_PREFIXES
    assert "test-" in AUTO_PREFIXES
    assert gen_from_import().startswith("imp-")
    assert gen_from_fork("x").startswith("fork-")
    assert gen_for_test().startswith("test-")
