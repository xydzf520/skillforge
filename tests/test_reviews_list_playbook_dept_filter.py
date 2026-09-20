"""H6 回归：list_reviews 必须按部门正确过滤 Playbook review。

修复前：
  list_reviews 用 INNER JOIN ``Skill.id == Review.skill_id`` 过滤部门，
  Playbook review 的 skill_id 形如 ``playbook:xxx``，与 Skill 表无任何匹配，
  导致：
    a) 普通用户看不到本部门 Playbook 的审核（漏出）；
    b) 跨部门 Playbook review 在某些跳 JOIN 路径下全可见（越权）。

修复后（Python 二次过滤）：
  - dept_X 用户只看到 dept_X Skill review + dept_X Playbook review
  - admin（不传 department）看到全部
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.reviews import service as review_service


@pytest.mark.asyncio
async def test_list_reviews_playbook_department_filter(client):
    """构造 dept_X Skill review + dept_Y Playbook review，
    验证按部门过滤的三种视角：dept_X / dept_Y / admin。
    """
    from app.database import async_session_factory
    from app.skills.core.models import Skill
    from app.reviews.models import Review

    # ── 1. 准备数据：dept_X 一个 Skill + 它的一个 review ──
    async with async_session_factory() as session:
        session.add(Skill(
            id="EC-投放-X1",
            name="投放优化X",
            department="dept_X",
            status="active",
        ))
        session.add(Skill(
            id="EC-备货-Y1",
            name="备货Y",
            department="dept_Y",
            status="active",
        ))
        await session.flush()

        session.add(Review(
            skill_id="EC-投放-X1",
            submitter="alice",
            change_type="params",
            status="pending",
            diff_summary="dept_X skill review",
            created_at=datetime.utcnow(),
        ))
        # dept_X Playbook
        session.add(Review(
            skill_id="playbook:campaign-x",
            submitter="alice",
            change_type="logic",
            status="pending",
            diff_summary="dept_X playbook review",
            created_at=datetime.utcnow(),
        ))
        # dept_Y Playbook
        session.add(Review(
            skill_id="playbook:restock-y",
            submitter="bob",
            change_type="logic",
            status="pending",
            diff_summary="dept_Y playbook review",
            created_at=datetime.utcnow(),
        ))
        # dept_Y Skill review（用于交叉对照）
        session.add(Review(
            skill_id="EC-备货-Y1",
            submitter="bob",
            change_type="params",
            status="pending",
            diff_summary="dept_Y skill review",
            created_at=datetime.utcnow(),
        ))
        await session.commit()

    # ── 2. mock get_playbook 提供两个 Playbook 的 department ──
    async def fake_get_playbook(name: str):
        mapping = {
            "campaign-x": {"name": "campaign-x", "department": "dept_X"},
            "restock-y": {"name": "restock-y", "department": "dept_Y"},
        }
        if name not in mapping:
            raise RuntimeError(f"unknown playbook {name}")
        return mapping[name]

    # ── 3. dept_X 用户视角 ──
    async with async_session_factory() as session:
        with patch("app.playbooks.service.get_playbook", new=AsyncMock(side_effect=fake_get_playbook)):
            res_x = await review_service.list_reviews(session, department="dept_X", page=1, page_size=50)

    skill_ids_x = sorted(item["skill_id"] for item in res_x["items"])
    assert "EC-投放-X1" in skill_ids_x, "dept_X 用户应看到本部门 Skill review"
    assert "playbook:campaign-x" in skill_ids_x, "dept_X 用户应看到本部门 Playbook review"
    assert "playbook:restock-y" not in skill_ids_x, "dept_X 用户绝不能看到 dept_Y 的 Playbook review"
    assert "EC-备货-Y1" not in skill_ids_x, "dept_X 用户绝不能看到 dept_Y Skill review"

    async with async_session_factory() as session:
        with patch("app.playbooks.service.get_playbook", new=AsyncMock(side_effect=fake_get_playbook)):
            res_x_update = await review_service.list_reviews(
                session, department="dept_X", change_type="update", q="dept_X", page=1, page_size=50
            )
    skill_ids_x_update = sorted(item["skill_id"] for item in res_x_update["items"])
    assert skill_ids_x_update == ["EC-投放-X1", "playbook:campaign-x"]

    # ── 4. dept_Y 用户视角 ──
    async with async_session_factory() as session:
        with patch("app.playbooks.service.get_playbook", new=AsyncMock(side_effect=fake_get_playbook)):
            res_y = await review_service.list_reviews(session, department="dept_Y", page=1, page_size=50)

    skill_ids_y = sorted(item["skill_id"] for item in res_y["items"])
    assert "EC-备货-Y1" in skill_ids_y, "dept_Y 用户应看到本部门 Skill review"
    assert "playbook:restock-y" in skill_ids_y, "dept_Y 用户应看到本部门 Playbook review"
    assert "playbook:campaign-x" not in skill_ids_y, "dept_Y 用户绝不能看到 dept_X 的 Playbook review"
    assert "EC-投放-X1" not in skill_ids_y, "dept_Y 用户绝不能看到 dept_X Skill review"

    # ── 5. admin（不传 department）视角：所有 review 全可见 ──
    async with async_session_factory() as session:
        res_all = await review_service.list_reviews(session, page=1, page_size=50)
    skill_ids_all = sorted(item["skill_id"] for item in res_all["items"])
    for sid in ("EC-投放-X1", "EC-备货-Y1", "playbook:campaign-x", "playbook:restock-y"):
        assert sid in skill_ids_all, f"admin 应能看到 {sid}"
