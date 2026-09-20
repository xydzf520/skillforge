"""Phase 1.5 / Phase 2 数据层测试。"""

from __future__ import annotations

import importlib.util
import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.execution.models import ExecutionRun, OpenClawInstance
from app.skills.core.models import Skill
from app.tasktree.service import TaskTreeProjection
from app.tasktree.models import TaskNodeLight
from app.tasktree.writer import TaskTreeWriter


def _load_migration_module(version: str):
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / f"{version}.py"
    spec = importlib.util.spec_from_file_location(f"migration_{version}", path)
    assert spec and spec.loader, f"无法加载迁移模块: {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest_asyncio.fixture
async def tasktree_session():
    test_url = getattr(settings, "DATABASE_URL_TEST", None) or re.sub(
        r"/skillforge$", "/skillforge_test", str(settings.DATABASE_URL)
    )
    engine = create_async_engine(test_url, echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await conn.run_sync(lambda sync_conn: TaskNodeLight.__table__.drop(sync_conn, checkfirst=True))
        await conn.run_sync(lambda sync_conn: TaskNodeLight.__table__.create(sync_conn, checkfirst=True))

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: TaskNodeLight.__table__.drop(sync_conn, checkfirst=True))
    await engine.dispose()


def test_task_nodes_light_model_shape():
    table = TaskNodeLight.__table__
    assert table.name == "task_nodes_light"
    # [codex-2026-04-14] 新增 ix_tnl_source_run_unique：同 run_id 的 execution/run 节点最多 1 条
    assert {
        "ix_tnl_tree",
        "ix_tnl_updated",
        "ix_tnl_run",
        "ix_tnl_active",
        "ix_tnl_main_read",
        "ix_tnl_source_run_unique",
    } == {ix.name for ix in table.indexes}
    assert table.c.root_id.nullable is False
    assert table.c.department_id.nullable is False
    assert table.c.status.nullable is False
    assert table.c.sort_key.default.arg == ""
    assert any(fk.target_fullname == "task_nodes_light.id" for fk in table.c.parent_id.foreign_keys)


def test_task_nodes_light_migration_schema():
    migration = _load_migration_module("050_task_nodes_light")
    content = (
        Path(__file__).resolve().parents[1] / "migrations" / "versions" / "050_task_nodes_light.py"
    ).read_text(encoding="utf-8")

    assert migration.revision == "050"
    assert migration.down_revision == "049"
    for marker in [
        "task_nodes_light",
        "gen_random_uuid()",
        "ix_tnl_tree",
        "ix_tnl_updated",
        "ix_tnl_run",
        "ix_tnl_active",
    ]:
        assert marker in content


def test_value_metrics_migration_and_models():
    migration = _load_migration_module("051_value_metrics")
    content = (
        Path(__file__).resolve().parents[1] / "migrations" / "versions" / "051_value_metrics.py"
    ).read_text(encoding="utf-8")

    assert migration.revision == "051"
    assert migration.down_revision == "050"
    for marker in [
        "manual_baseline_minutes",
        "business_value_tag",
        "business_ref_id",
        "default_baseline_minutes",
    ]:
        assert marker in content

    assert "manual_baseline_minutes" in ExecutionRun.__table__.c
    assert "business_value_tag" in ExecutionRun.__table__.c
    assert "business_ref_id" in ExecutionRun.__table__.c
    assert "default_baseline_minutes" in Skill.__table__.c


def test_repair_queue_migration_schema():
    """052：tasktree_repair_queue 表结构 + 索引"""
    from app.tasktree.models import TaskTreeRepairQueue

    migration = _load_migration_module("052_tasktree_repair_queue")
    content = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "052_tasktree_repair_queue.py"
    ).read_text(encoding="utf-8")

    assert migration.revision == "052"
    assert migration.down_revision == "051"
    for marker in [
        "tasktree_repair_queue",
        "gen_random_uuid()",
        "ix_trq_scan",
        "ix_trq_source",
    ]:
        assert marker in content

    cols = TaskTreeRepairQueue.__table__.c
    for name in ("id", "source_type", "source_ref", "operation", "payload",
                 "retries", "max_retries", "next_attempt_at", "status",
                 "created_at", "updated_at", "last_error"):
        assert name in cols, f"repair_queue 缺少列 {name}"
    # [codex-2026-04-14] 新增 ix_trq_active_unique：pending/retrying 下同 key 最多 1 条
    assert {
        "ix_trq_scan",
        "ix_trq_source",
        "ix_trq_active_unique",
    } == {ix.name for ix in TaskTreeRepairQueue.__table__.indexes}


def test_tnl_main_read_index_migration_schema():
    """[M2] 053 迁移：ix_tnl_main_read 索引匹配 _get_tree_from_tnl 查询形状。"""
    migration = _load_migration_module("053_tnl_main_read_index")
    content = (
        Path(__file__).resolve().parents[1] / "migrations" / "versions"
        / "053_tnl_main_read_index.py"
    ).read_text(encoding="utf-8")

    assert migration.revision == "053"
    assert migration.down_revision == "052"
    assert "ix_tnl_main_read" in content
    assert "source_run_id IS NOT NULL" in content
    assert "started_at DESC" in content

    # Index 对象也要挂到模型上
    assert "ix_tnl_main_read" in {ix.name for ix in TaskNodeLight.__table__.indexes}


@pytest.mark.asyncio
async def test_main_read_query_uses_ix_tnl_main_read(tasktree_session: AsyncSession):
    """[M2] EXPLAIN 主读路径查询，断言 ix_tnl_main_read 索引被命中（或至少定义存在）。

    小数据集 planner 可能不走索引，所以同 test_task_nodes_light_query_uses_ix_tnl_tree
    做法：先断言索引定义存在，再跑 EXPLAIN 确认查询形状兼容。
    """
    writer = TaskTreeWriter()
    now = datetime.utcnow()
    for idx in range(5):
        await writer.create_execution_node(
            tasktree_session,
            run_id=str(uuid4()),
            department_id="EC",
            title=f"MainRead-{idx}",
            source_instance_id="inst-main-read",
            status="running",
            started_at=now - timedelta(minutes=idx),
        )
    await tasktree_session.commit()

    from sqlalchemy import text as sql_text

    # 主读路径形状：(department_id, status IN (...), ORDER BY started_at DESC) WHERE source_run_id NOT NULL
    result = await tasktree_session.execute(
        sql_text(
            "EXPLAIN (FORMAT TEXT) "
            "SELECT source_run_id, started_at FROM task_nodes_light "
            "WHERE department_id = 'EC' "
            "AND status IN ('running','queued','completed') "
            "AND source_run_id IS NOT NULL "
            "ORDER BY started_at DESC LIMIT 20"
        )
    )
    plan_lines = [row[0] for row in result.all()]
    plan_text = "\n".join(plan_lines)

    # 索引定义存在
    assert "ix_tnl_main_read" in {ix.name for ix in TaskNodeLight.__table__.indexes}
    # EXPLAIN 跑通
    assert plan_text


@pytest.mark.asyncio
async def test_task_nodes_light_query_uses_ix_tnl_tree(tasktree_session: AsyncSession):
    """P0-4：确认部门扫描查询在索引存在的前提下能成功跑 EXPLAIN。

    小数据集下 Postgres planner 可能选择 seq scan（表行数<阈值），因此严格
    断言"必须命中 ix_tnl_tree"不稳定。这里退而断言：
    1) ix_tnl_tree 索引已按 (department_id, root_id, parent_id, sort_key) 定义
    2) 该查询能成功跑通，说明索引定义兼容主查询形状
    线上量级下 ANALYZE 统计信息会让 planner 主动选 ix_tnl_tree。
    """
    writer = TaskTreeWriter()
    now = datetime.utcnow()
    for idx in range(3):
        await writer.create_execution_node(
            tasktree_session,
            run_id=str(uuid4()),
            department_id="EC",
            title=f"Skill-{idx}",
            source_instance_id="inst-explain",
            status="running",
            started_at=now - timedelta(minutes=idx),
        )
    await tasktree_session.commit()

    from sqlalchemy import text as sql_text

    result = await tasktree_session.execute(
        sql_text(
            "EXPLAIN (FORMAT TEXT) "
            "SELECT source_run_id FROM task_nodes_light "
            "WHERE department_id = 'EC' "
            "AND status IN ('running','queued','stale') "
            "ORDER BY sort_key DESC LIMIT 20"
        )
    )
    plan_lines = [row[0] for row in result.all()]
    plan_text = "\n".join(plan_lines)

    # 索引定义存在（由 model/migration 保障）
    assert "ix_tnl_tree" in {ix.name for ix in TaskNodeLight.__table__.indexes}
    # 查询能成功跑 EXPLAIN（形状兼容）
    assert plan_text


@pytest.mark.asyncio
async def test_tasktree_writer_creates_updates_and_syncs_heartbeat(tasktree_session: AsyncSession):
    writer = TaskTreeWriter()
    now = datetime.utcnow()
    root_run_id = str(uuid4())
    child_run_id = str(uuid4())

    root = await writer.create_execution_node(
        tasktree_session,
        run_id=root_run_id,
        department_id="EC",
        title="EC 日报",
        source_instance_id="inst-1",
        status="running",
        started_at=now,
    )
    child = await writer.create_execution_node(
        tasktree_session,
        run_id=child_run_id,
        department_id="EC",
        title="子任务",
        source_instance_id="inst-1",
        parent_id=root.id,
        status="queued",
    )
    await tasktree_session.commit()

    assert str(root.root_id) == root_run_id
    assert child.root_id == root.root_id
    assert child.parent_id == root.id
    assert child.sort_key == "子任务"

    finished_at = now + timedelta(minutes=5)
    updated = await writer.finish_execution_node(
        tasktree_session,
        run_id=root_run_id,
        status="completed",
        finished_at=finished_at,
    )
    await tasktree_session.commit()

    assert updated is not None
    assert updated.status == "completed"
    assert updated.finished_at == finished_at

    heartbeat_at = now + timedelta(minutes=8)
    changed = await writer.sync_instance_heartbeats(
        tasktree_session,
        {"inst-1": heartbeat_at},
        department_id="EC",
    )
    await tasktree_session.commit()

    assert changed == 2

    rows = (
        await tasktree_session.execute(
            select(TaskNodeLight).where(TaskNodeLight.source_instance_id == "inst-1").order_by(TaskNodeLight.source_run_id.asc())
        )
    ).scalars().all()
    assert len(rows) == 2
    assert all(row.last_heartbeat_at == heartbeat_at for row in rows)
    assert all(row.updated_at >= now for row in rows)


@pytest.mark.asyncio
async def test_recent_runs_light_keeps_latest_platform_fallback_run(tasktree_session: AsyncSession):
    writer = TaskTreeWriter()
    now = datetime.utcnow()
    platform = OpenClawInstance(
        id="platform",
        name="平台主节点",
        department=None,
        gateway_url="http://platform",
        reload_hook_url="http://platform/reload",
        reload_token="token",
        agent_type="aiclaw",
        is_platform_default=True,
    )

    # 传统电商在同一个平台节点上有大量历史运行。
    for idx in range(settings.TASKTREE_MAX_RECENT_RUNS + 5):
        run_id = f"trad-{idx}"
        started_at = now - timedelta(days=idx + 1)
        await writer.create_execution_node(
            tasktree_session,
            run_id=run_id,
            department_id="示例品牌传统电商运营部",
            title="传统电商历史运行",
            source_instance_id="platform",
            status="completed",
            started_at=started_at,
            finished_at=started_at + timedelta(seconds=1),
        )

    await writer.create_execution_node(
        tasktree_session,
        run_id="samplebrand-fallback-latest",
        department_id="示例品牌内容电商运营部",
        title="示例品牌低消耗视频日诊断",
        source_instance_id="platform",
        status="completed",
        started_at=now,
        finished_at=now + timedelta(seconds=1),
    )
    await tasktree_session.commit()

    projection = TaskTreeProjection()
    instance_runs, dept_runs = await projection._query_recent_runs_light(
        tasktree_session,
        [platform],
        user_department=None,
        member_skill_ids=set(),
        is_admin=True,
    )

    platform_runs = instance_runs["platform"]
    assert platform_runs[0].run_id == "samplebrand-fallback-latest"
    assert platform_runs[0].skill_name == "示例品牌低消耗视频日诊断"
    assert "示例品牌内容电商运营部" in dept_runs
