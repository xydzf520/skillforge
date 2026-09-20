import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.exceptions import AppError


def _db_no_existing_skill():
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _skill_md(*, name: str = "最小reports测试") -> str:
    return f"""---
name: {name}
description: 最小可通过创建门禁的 Skill
department: AI小组
trigger_type: manual
risk_level: R2
---

## 目的
验证 create_skill_from_files 门禁。
"""


@pytest.mark.asyncio
async def test_create_skill_from_files_blocks_placeholder_main():
    from app.skills.lifecycle.service import create_skill_from_files

    db = _db_no_existing_skill()
    files = {
        "SKILL.md": _skill_md(),
        "scripts/main.py": (
            "def main(payload):\n"
            "    raise NotImplementedError('todo')\n"
        ),
        "fixtures/sample_input.json": json.dumps({"date": "2026-04-15"}, ensure_ascii=False),
    }

    with patch("app.skills.lifecycle.service.git_service") as mock_git, \
         patch("app.skills.lifecycle.service.audit") as mock_audit, \
         patch("app.common.contract_schema.build_verified_preview", new=AsyncMock(side_effect=AssertionError("preview should not run"))):
        mock_audit.log = AsyncMock()

        with pytest.raises(AppError) as exc_info:
            await create_skill_from_files(
                db,
                skill_id="SKILL-GATE-PLACEHOLDER",
                name="最小reports测试",
                department="AI小组",
                files=files,
                user_id="admin",
            )

    assert exc_info.value.status == 422
    assert exc_info.value.code == "SKILL_VALIDATION_FAILED"
    assert any("NotImplementedError" in err or "占位" in err for err in exc_info.value.detail["errors"])
    mock_git.create_skill_dir.assert_not_called()
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_skill_from_files_blocks_datasource_without_sdk():
    from app.skills.lifecycle.service import create_skill_from_files

    db = _db_no_existing_skill()
    files = {
        "SKILL.md": _skill_md(name="datasource 检查"),
        "contract.json": json.dumps(
            {
                "input": [
                    {
                        "name": "生意参谋_店铺排行榜",
                        "type": "json",
                        "source": "datasource",
                        "required": True,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        "scripts/main.py": (
            "def collect_inputs(payload):\n"
            "    return payload\n"
        ),
        "fixtures/sample_input.json": json.dumps({"date": "2026-04-15"}, ensure_ascii=False),
    }

    with patch("app.skills.lifecycle.service.git_service") as mock_git, \
         patch("app.skills.lifecycle.service.audit") as mock_audit, \
         patch("app.common.contract_schema.build_verified_preview", new=AsyncMock(side_effect=AssertionError("preview should not run"))):
        mock_audit.log = AsyncMock()

        with pytest.raises(AppError) as exc_info:
            await create_skill_from_files(
                db,
                skill_id="SKILL-GATE-DATASOURCE",
                name="datasource 检查",
                department="AI小组",
                files=files,
                user_id="admin",
            )

    assert exc_info.value.status == 422
    assert exc_info.value.code == "SKILL_VALIDATION_FAILED"
    assert any("SkillForge SDK" in err or "真实采集逻辑" in err for err in exc_info.value.detail["errors"])
    mock_git.create_skill_dir.assert_not_called()
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_skill_from_files_allows_minimal_reports_output(tmp_path):
    from app.skills.lifecycle.service import create_skill_from_files

    db = _db_no_existing_skill()
    files = {
        "SKILL.md": _skill_md(name="reports 最小通过"),
        "contract.json": json.dumps(
            {
                "output_schema": {
                    "$schema": "http://json-schema.org/draft-07/schema#",
                    "type": "object",
                    "required": ["reports"],
                    "properties": {
                        "reports": {"type": "array"},
                    },
                },
                "output": {
                    "adapter": "json_webhook",
                    "schema": {},
                },
            },
            ensure_ascii=False,
        ),
        "scripts/main.py": (
            "import json\n"
            "import sys\n\n"
            "def main(payload):\n"
            "    return {'reports': [{'channel': 'json_webhook', 'title': 'ok'}]}\n\n"
            "if __name__ == '__main__':\n"
            "    payload = json.loads(sys.stdin.read() or '{}')\n"
            "    print(json.dumps(main(payload), ensure_ascii=False))\n"
        ),
        "fixtures/sample_input.json": json.dumps({"date": "2026-04-15"}, ensure_ascii=False),
    }

    with patch("app.skills.lifecycle.service.git_service") as mock_git, \
         patch("app.skills.lifecycle.service.audit") as mock_audit:
        mock_git.create_skill_dir = MagicMock()
        mock_git.skill_dir.return_value = Path(tmp_path / "SKILL-GATE-REPORTS")
        mock_git.write_file = MagicMock()
        mock_git.commit_all.return_value = "commit-123"
        mock_audit.log = AsyncMock()

        result = await create_skill_from_files(
            db,
            skill_id="SKILL-GATE-REPORTS",
            name="reports 最小通过",
            department="AI小组",
            files=files,
            user_id="admin",
        )

    assert result["skill_id"] == "SKILL-GATE-REPORTS"
    assert result["git_commit"] == "commit-123"
    mock_git.create_skill_dir.assert_called_once_with("SKILL-GATE-REPORTS")
    assert mock_git.write_file.call_count >= 4
    db.add.assert_called_once()
    db.flush.assert_awaited()
