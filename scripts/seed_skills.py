#!/usr/bin/env python3
"""
从 skills-repo Git 仓库扫描所有 Skill 目录，同步到数据库 skills 表。
解决：Skill 文件存在但 DB 记录丢失的问题。

用法:
  python scripts/seed_skills.py           # 扫描并导入
  python scripts/seed_skills.py --dry-run # 只列出，不写入
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from app.common.time_utils import now_bjt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def seed_skills(dry_run: bool = False):
    from app.config import settings

    import asyncpg
    import yaml

    dsn = str(settings.DATABASE_URL).replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)

    repo_path = Path(settings.SKILL_REPO_PATH)
    if not repo_path.exists():
        print(f"skills-repo 不存在: {repo_path}")
        await conn.close()
        return

    # 扫描所有 Skill 目录（含 SKILL.md 的目录）
    skill_dirs = []
    for d in sorted(repo_path.iterdir()):
        if d.is_dir() and not d.name.startswith(".") and (d / "SKILL.md").exists():
            skill_dirs.append(d)

    print(f"发现 {len(skill_dirs)} 个 Skill 目录")

    # 查询已存在的 skill_id
    existing = await conn.fetch("SELECT id FROM skills")
    existing_ids = {r["id"] for r in existing}

    imported = 0
    skipped = 0

    for d in skill_dirs:
        skill_id = d.name
        if skill_id in existing_ids:
            print(f"  跳过 {skill_id}（已存在）")
            skipped += 1
            continue

        # 解析 SKILL.md frontmatter
        skill_md = (d / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = {}
        if skill_md.startswith("---"):
            parts = skill_md.split("---", 2)
            if len(parts) >= 3:
                try:
                    frontmatter = yaml.safe_load(parts[1]) or {}
                except yaml.YAMLError:
                    pass

        name = frontmatter.get("name", skill_id)
        department = frontmatter.get("department", "")
        trigger_type = frontmatter.get("trigger_type", "manual")
        trigger_expr = frontmatter.get("trigger_expression", "")
        risk_level = frontmatter.get("risk_level", "R2")
        approval_level = frontmatter.get("approval_level", 1)

        print(f"  导入 {skill_id} ({name}) dept={department}")

        if not dry_run:
            now = now_bjt()
            await conn.execute(
                """INSERT INTO skills
                   (id, name, department, trigger_type, trigger_expression,
                    risk_level, approval_level, status, created_at, updated_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                   ON CONFLICT (id) DO NOTHING""",
                skill_id, name, department, trigger_type, trigger_expr,
                risk_level, approval_level, "draft", now, now,
            )
            imported += 1

    await conn.close()

    if dry_run:
        print(f"\n[dry-run] 将导入 {len(skill_dirs) - skipped} 个 Skill")
    else:
        print(f"\n✓ 导入 {imported} 个，跳过 {skipped} 个")


def main():
    parser = argparse.ArgumentParser(description="从 Git 仓库同步 Skill 到数据库")
    parser.add_argument("--dry-run", action="store_true", help="只列出，不写入")
    args = parser.parse_args()
    asyncio.run(seed_skills(args.dry_run))


if __name__ == "__main__":
    main()
