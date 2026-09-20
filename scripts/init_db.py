"""
初始化数据库：创建admin账号。
运行：python scripts/init_db.py
"""

import asyncio
import getpass
import os
import sys
from pathlib import Path

# 确保项目根目录在Python路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from app.database import async_session_factory
from app.auth.models import User
from app.auth.service import hash_password


def initial_admin_password() -> str:
    """Read a deployment-specific password without printing or committing it."""
    password = os.environ.get("SKILLFORGE_ADMIN_PASSWORD", "")
    if not password:
        if not sys.stdin.isatty():
            raise ValueError("请设置 SKILLFORGE_ADMIN_PASSWORD，或在终端交互输入初始密码")
        password = getpass.getpass("管理员初始密码（至少 14 位）：")
        if password != getpass.getpass("再次输入密码："):
            raise ValueError("两次输入不一致")
    if len(password) < 14:
        raise ValueError("管理员初始密码至少需要 14 位")
    return password


async def init():
    async with async_session_factory() as session:
        # 检查admin是否已存在
        result = await session.execute(select(User).where(User.username == "admin"))
        existing = result.scalar_one_or_none()

        if existing:
            print(f"admin 账号已存在 (id={existing.id})")
            return

        # 创建admin账号
        admin = User(
            id="admin",
            username="admin",
            password_hash=hash_password(initial_admin_password()),
            name="管理员",
            role="admin",
            can_view_all=True,
            department=None,
            is_active=True,
            must_change_password=True,
        )
        session.add(admin)
        await session.commit()
        print("admin 账号创建成功（用户名: admin；密码为本次输入值）")
        print("首次登录后请修改密码")


if __name__ == "__main__":
    asyncio.run(init())
