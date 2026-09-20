"""Skill hall 成员查询 helper 测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.skills import hall_service


def _result(rows):
    result = MagicMock()
    result.all.return_value = rows
    return result


@pytest.mark.asyncio
async def test_get_member_skill_ids_uses_core_member_helper():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_result([("skill-a",), ("skill-b",)]))

    assert await hall_service._get_member_skill_ids(db, "user-1") == ["skill-a", "skill-b"]
