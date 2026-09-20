#!/usr/bin/env python3
"""一次性把历史 execution_runs 补到 task_nodes_light（用 asyncpg 直连）。

独立脚本遵循项目 feedback：独立脚本用 asyncpg 而不是 SQLAlchemy，
避免 ORM session 在长时间循环中 hold 连接。

用法：
  python scripts/backfill_task_nodes_light.py --dry-run
  python scripts/backfill_task_nodes_light.py --since-days 30
  python scripts/backfill_task_nodes_light.py --department EC --since-days 60
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4
from app.common.time_utils import now_bjt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _coerce_uuid(value) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


async def backfill(
    *,
    dry_run: bool = False,
    department: str | None = None,
    since_days: int = 30,
    batch_size: int = 500,
) -> dict:
    """补写历史 execution_runs 到 task_nodes_light。

    Returns: {"scanned": N, "inserted": M, "skipped": K}
    """
    from app.config import settings

    import asyncpg

    dsn = str(settings.DATABASE_URL).replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)

    since = now_bjt() - timedelta(days=since_days)

    # 先确保 pgcrypto 存在（UUID 生成需要）
    await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    filters = ["er.started_at >= $1"]
    params: list = [since]
    if department:
        filters.append("s.department = $2")
        params.append(department)
    where_clause = " AND ".join(filters)

    query = f"""
        SELECT
            er.id AS run_id,
            er.status AS run_status,
            er.started_at,
            er.completed_at,
            dl.skill_id,
            s.department,
            s.name AS skill_name
        FROM execution_runs er
        LEFT JOIN decision_log dl ON dl.run_id = er.id
        LEFT JOIN skills s ON s.id = dl.skill_id
        WHERE {where_clause}
          AND NOT EXISTS (
            SELECT 1 FROM task_nodes_light tnl
            WHERE tnl.source_run_id = er.id
              AND tnl.node_type = 'execution'
          )
        ORDER BY er.started_at ASC
    """

    rows = await conn.fetch(query, *params)
    scanned = len(rows)
    print(f"扫描到 {scanned} 条 execution_runs 缺失 light 节点"
          f" (since_days={since_days}, department={department or 'ALL'})")

    if dry_run:
        print("--dry-run 模式，仅展示前 20 条样本：")
        for row in rows[:20]:
            print(f"  run_id={row['run_id']} skill={row['skill_id']} "
                  f"dept={row['department']} status={row['run_status']} "
                  f"started_at={row['started_at']}")
        await conn.close()
        return {"scanned": scanned, "inserted": 0, "skipped": 0}

    inserted = 0
    skipped = 0

    # [m7] 原来挂了裸 ON CONFLICT DO NOTHING 但没给任何约束 → Postgres 只对
    # 带 unique_constraint/unique_index 的列有效，这里 task_nodes_light 主查询
    # 已经在上面 WHERE NOT EXISTS 做了去重，裸 ON CONFLICT 是死代码，直接去掉。
    insert_sql = """
        INSERT INTO task_nodes_light (
            id, root_id, parent_id, source_run_id, source_instance_id,
            department_id, node_type, title, status, sort_key,
            started_at, finished_at, last_heartbeat_at, created_at, updated_at
        ) VALUES (
            gen_random_uuid(), $1, NULL, $2, NULL,
            $3, 'execution', $4, $5, $6,
            $7, $8, NULL, NOW(), NOW()
        )
    """

    async with conn.transaction():
        for row in rows:
            run_id = row["run_id"]
            if not run_id:
                skipped += 1
                continue
            department_id = row["department"] or "unknown"
            title = row["skill_name"] or row["skill_id"] or run_id
            sort_key = title[:100]
            status = row["run_status"] or "running"

            # root_id：优先用 run_id（UUID 字符串）转成 UUID，否则新 UUID
            root_uuid = _coerce_uuid(run_id) or uuid4()

            try:
                await conn.execute(
                    insert_sql,
                    root_uuid,
                    run_id,
                    department_id,
                    title,
                    status,
                    sort_key,
                    row["started_at"],
                    row["completed_at"],
                )
                inserted += 1
            except Exception as e:
                print(f"  插入失败 run_id={run_id}: {e}")
                skipped += 1

            if inserted and inserted % batch_size == 0:
                print(f"  已插入 {inserted} / {scanned}")

    await conn.close()
    print(f"完成: 扫描 {scanned}, 插入 {inserted}, 跳过 {skipped}")
    return {"scanned": scanned, "inserted": inserted, "skipped": skipped}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="回填历史 execution_runs 到 task_nodes_light")
    parser.add_argument("--dry-run", action="store_true", help="仅打印前 20 条缺口不写入")
    parser.add_argument("--department", type=str, default=None, help="仅处理指定部门（可选）")
    parser.add_argument("--since-days", type=int, default=30, help="回溯天数（默认 30）")
    parser.add_argument("--batch-size", type=int, default=500, help="进度打印粒度（默认 500）")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    await backfill(
        dry_run=args.dry_run,
        department=args.department,
        since_days=args.since_days,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    asyncio.run(_main())
