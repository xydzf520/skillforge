"""回填现有数据源的 owner_contact + visibility 默认值。

v2.7 大厅 v3 部署后运行一次即可：
- 现有行 visibility 已由迁移默认 'department'，本脚本主要补 owner_contact = created_by
- 用法：PYTHONPATH=. python scripts/seed_datasource_visibility.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.datasources.models import DataSource


async def main():
    engine = create_async_engine(str(settings.DATABASE_URL))
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with sm() as db:
        from sqlalchemy import select, update

        # 回填 owner_contact：空 → created_by
        rows = (
            await db.execute(
                select(DataSource).where(
                    DataSource.owner_contact.is_(None),
                    DataSource.created_by.isnot(None),
                )
            )
        ).scalars().all()
        print(f"待回填 owner_contact 的数据源：{len(rows)}")
        if rows:
            for src in rows:
                src.owner_contact = src.created_by
            await db.commit()
            print(f"已回填 {len(rows)} 个数据源")

        # visibility 审计：统计三档分布
        from sqlalchemy import func

        dist = (
            await db.execute(
                select(DataSource.visibility, func.count(DataSource.id))
                .group_by(DataSource.visibility)
            )
        ).all()
        print("visibility 分布：")
        for v, c in dist:
            print(f"  {v or '(空)'}: {c}")


if __name__ == "__main__":
    asyncio.run(main())
