"""Skill 成员 scope helper 测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.skills.core.access import list_user_member_departments, list_user_member_scope


def _result(rows):
    result = MagicMock()
    result.all.return_value = rows
    return result


@pytest.mark.asyncio
async def test_list_user_member_scope_returns_skill_ids_and_departments():
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=_result(
            [
                ("skill-a", "EC"),
                ("skill-b", "BD"),
                ("skill-a", "EC"),
            ]
        )
    )

    skill_ids, departments = await list_user_member_scope(db, "user-1")

    assert skill_ids == {"skill-a", "skill-b"}
    assert departments == {"EC", "BD"}


@pytest.mark.asyncio
async def test_list_user_member_departments_keeps_single_column_query_shape():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_result([("AM",), ("研发部",), ("AM",)]))

    departments = await list_user_member_departments(db, "user-1")

    assert departments == {"AM", "研发部"}
