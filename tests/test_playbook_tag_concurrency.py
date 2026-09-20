"""H7 回归：同 playbook 并发 approve 不能产生重复 tag。

修复前：
  ``_next_playbook_version`` 直接读 git tag 计算下一个版本号，
  两个 approve 协程在 create_tag 之前都读到相同的 max version，
  会产生 "tag already exists" 冲突 / 同 tag 名重复 / 版本号撞号。

修复后：
  调用方在 ``approve_review`` 中用 ``pg_advisory_xact_lock(hashtext(playbook:name))``
  串行化 (read tag → compute version → create_tag) 临界区。
  - PG 上：天然串行，永不重号
  - SQLite 测试夹具：advisory lock 调用静默降级（语义靠测试中顺序两次 approve 验证）

由于测试夹具默认走 PG（conftest 用 settings.DATABASE_URL_TEST），
本测试同时验证：
  1. 顺序两次 approve 同一 playbook → tag 名为 vN 与 vN+1，互不冲突
  2. ``_acquire_playbook_version_lock`` 在 PG 上不抛异常（覆盖 advisory_lock 路径）
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_playbook_version_lock_acquires_on_pg(client):
    """直接调用 advisory_lock 不应抛错（PG 必走，SQLite 优雅降级）。"""
    from app.database import async_session_factory
    from app.reviews.service import _acquire_playbook_version_lock

    async with async_session_factory() as session:
        # 不抛异常即视为通过；PG 真正持锁，SQLite 走 except 分支
        await _acquire_playbook_version_lock(session, "concurrency-test-playbook")
        await session.commit()


@pytest.mark.asyncio
async def test_playbook_sequential_approves_get_distinct_versions(client):
    """顺序 approve 两次同一 playbook，应产生 v0.1 / v0.2 两个 tag，且不冲突。

    这是 H7 修复后的最小契约保证：
      - 版本号严格递增
      - 第二次 approve 不会因 tag 撞号而失败
    """
    from app.database import async_session_factory
    from app.reviews.models import Review
    from app.reviews import service as review_service

    # 准备两条 pending Playbook review
    async with async_session_factory() as session:
        rev1 = Review(
            skill_id="playbook:hot-pb",
            submitter="alice",
            change_type="logic",
            status="pending",
            diff_summary="rev1",
            created_at=datetime.utcnow(),
        )
        rev2 = Review(
            skill_id="playbook:hot-pb",
            submitter="alice",
            change_type="logic",
            status="pending",
            diff_summary="rev2",
            created_at=datetime.utcnow(),
        )
        session.add_all([rev1, rev2])
        await session.commit()
        rev1_id, rev2_id = rev1.id, rev2.id

    # 记录 create_tag 调用，避免真触发 git 操作
    created_tags: list[str] = []

    def fake_tag(tag_name: str, message: str = "") -> None:
        if tag_name in created_tags:
            raise RuntimeError(f"tag {tag_name} already exists")
        created_tags.append(tag_name)

    fake_repo = MagicMock()
    fake_repo.tags = []  # 初始无 tag → 第一次 v0.1，第二次 v0.2

    fake_sync_ok = AsyncMock(return_value={"all_ok": True, "push_ok": True})

    # 注意：_next_playbook_version 第二次必须能看到第一次的 tag。
    # 用 MagicMock 模拟一个会被追加的 tags 列表。
    class _FakeTagObj:
        def __init__(self, name): self._name = name
        def __str__(self): return self._name

    real_tags: list[_FakeTagObj] = []
    fake_repo.tags = real_tags

    def fake_tag_appending(tag_name: str, message: str = "") -> None:
        if any(str(t) == tag_name for t in real_tags):
            raise RuntimeError(f"tag {tag_name} already exists")
        real_tags.append(_FakeTagObj(tag_name))

    with patch("app.reviews.service.git_service") as mock_git:
        mock_git.repo = fake_repo
        mock_git.tag.side_effect = fake_tag_appending
        mock_git.delete_tag = MagicMock(return_value=True)

        with patch("app.execution.sync_service.sync_service.sync_playbook_after_approval", new=fake_sync_ok):
            async with async_session_factory() as session:
                r1 = await review_service.approve_review(session, rev1_id, "bob")
                await session.commit()
            async with async_session_factory() as session:
                r2 = await review_service.approve_review(session, rev2_id, "bob")
                await session.commit()

    # 关键断言：版本号严格递增，tag 不冲突
    assert r1["status"] == "approved"
    assert r2["status"] == "approved"
    assert r1["new_version"] == "v0.1"
    assert r2["new_version"] == "v0.2"
    tag_names = [str(t) for t in real_tags]
    assert tag_names == ["playbook/hot-pb/v0.1", "playbook/hot-pb/v0.2"]
    assert len(set(tag_names)) == 2, "两次 approve 不能产生重复 tag"
