#!/usr/bin/env python3
"""
用户管理命令行工具 — 创建/重置密码/列出用户。
使用项目自身的 bcrypt hash_password()，避免哈希方式不一致导致无法登录。

用法:
  python scripts/manage_users.py create --username admin --password "$SKILLFORGE_ADMIN_PASSWORD" --role admin --department EC
  python scripts/manage_users.py reset-password --username admin --password "$SKILLFORGE_ADMIN_PASSWORD"
  python scripts/manage_users.py list
"""

import argparse
import asyncio
import sys
from pathlib import Path
from app.common.time_utils import now_bjt

# 将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def create_user(args):
    from app.database import init_db
    from app.auth.service import hash_password

    await init_db()

    import asyncpg
    from app.config import settings

    # 直接用 asyncpg 写入（避免 SQLAlchemy async session 在脚本中的事务问题）
    dsn = str(settings.DATABASE_URL).replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)

    # 检查用户是否已存在
    existing = await conn.fetchrow(
        "SELECT id FROM users WHERE username=$1", args.username
    )
    if existing:
        print(f"用户 {args.username} 已存在 (id={existing['id']})")
        if not args.force:
            print("使用 --force 覆盖")
            await conn.close()
            return
        # 覆盖：更新密码和角色
        pw_hash = hash_password(args.password)
        await conn.execute(
            "UPDATE users SET password_hash=$1, role=$2, department=$3, "
            "is_active=true, can_view_all=$4, name=$5 WHERE username=$6",
            pw_hash, args.role, args.department,
            args.role == "admin", args.name or args.username, args.username,
        )
        print(f"✓ 用户 {args.username} 已更新")
    else:
        from datetime import datetime
        now = now_bjt()
        pw_hash = hash_password(args.password)
        user_id = args.id or args.username

        await conn.execute(
            "INSERT INTO users (id, username, password_hash, name, role, "
            "can_view_all, department, is_active, must_change_password, "
            "state, created_at, updated_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
            user_id, args.username, pw_hash,
            args.name or args.username, args.role,
            args.role == "admin", args.department,
            True, False, "active", now, now,
        )
        print(f"✓ 用户 {args.username} 已创建 (id={user_id}, role={args.role})")

    await conn.close()


async def reset_password(args):
    from app.database import init_db
    from app.auth.service import hash_password
    from app.config import settings

    await init_db()

    import asyncpg
    dsn = str(settings.DATABASE_URL).replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)

    row = await conn.fetchrow(
        "SELECT id FROM users WHERE username=$1", args.username
    )
    if not row:
        print(f"用户 {args.username} 不存在")
        await conn.close()
        return

    pw_hash = hash_password(args.password)
    await conn.execute(
        "UPDATE users SET password_hash=$1, permissions_rev = permissions_rev + 1 WHERE username=$2",
        pw_hash, args.username,
    )
    print(f"✓ 用户 {args.username} 密码已重置（旧 session 已失效）")
    await conn.close()


async def list_users(_args):
    from app.config import settings

    import asyncpg
    dsn = str(settings.DATABASE_URL).replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)

    rows = await conn.fetch(
        "SELECT id, username, name, role, department, is_active, can_view_all "
        "FROM users ORDER BY created_at"
    )
    if not rows:
        print("无用户")
    else:
        print(f"{'ID':<15} {'用户名':<12} {'姓名':<10} {'角色':<12} {'部门':<6} {'活跃':<4} {'全局':<4}")
        print("-" * 75)
        for r in rows:
            print(
                f"{r['id']:<15} {r['username']:<12} {r['name']:<10} "
                f"{r['role']:<12} {r['department'] or '-':<6} "
                f"{'✓' if r['is_active'] else '✗':<4} "
                f"{'✓' if r['can_view_all'] else '-':<4}"
            )
    await conn.close()


def main():
    parser = argparse.ArgumentParser(description="SkillForge 用户管理")
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = sub.add_parser("create", help="创建用户")
    p_create.add_argument("--username", required=True)
    p_create.add_argument("--password", required=True)
    p_create.add_argument("--role", default="ai_engineer",
                          choices=["admin", "ai_engineer", "biz_owner", "operator", "aibp", "director",
                                   "system_admin", "dept_admin", "engineer", "observer", "member", "viewer"])
    p_create.add_argument("--department", default="EC")
    p_create.add_argument("--name", default="")
    p_create.add_argument("--id", default="", help="用户ID，默认与username相同")
    p_create.add_argument("--force", action="store_true", help="已存在则覆盖")

    # reset-password
    p_reset = sub.add_parser("reset-password", help="重置密码")
    p_reset.add_argument("--username", required=True)
    p_reset.add_argument("--password", required=True)

    # list
    sub.add_parser("list", help="列出所有用户")

    args = parser.parse_args()

    if args.command == "create":
        asyncio.run(create_user(args))
    elif args.command == "reset-password":
        asyncio.run(reset_password(args))
    elif args.command == "list":
        asyncio.run(list_users(args))


if __name__ == "__main__":
    main()
