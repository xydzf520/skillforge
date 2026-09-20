#!/usr/bin/env python3
"""[C3] 扫描 OpenClawInstance, 列出仍持有 v1.10.0 之前 sha256 enrollment hash 的实例。

背景:
  v1.10.1 (2026-04-09) 把 enrollment token 哈希算法从 sha256 升级为 bcrypt。
  sha256 不可逆 → 已存的 hash 无法 rehash。
  所幸 enrollment_token_hash 是一次性的 (验证后会被 bridge_router 清空), 因此:
    1. verify_enrollment_token 已支持双层兼容 (security.py 中的 _legacy_sha256_hash)
    2. 老 hash 在第一次成功 enroll 后会自然消失
    3. 新签发的 token 一律走 bcrypt

本脚本不做迁移 (做不了), 仅 *报告* 哪些 instance 还持有老 hash, 让运维:
  - 关注它们能否正常 enroll
  - 必要时手动 regenerate enrollment 让 instance 重新走流程

用法:
    python scripts/check_enrollment_token_legacy.py
    python scripts/check_enrollment_token_legacy.py --json   # 输出 JSON 给监控用

依赖: asyncpg (脚本不用 SQLAlchemy, 走原生 SQL — 项目惯例 reference_asyncpg_over_sqlalchemy)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from urllib.parse import urlparse

import asyncpg
from app.common.time_utils import now_bjt


def _is_legacy_sha256_hash(h: str | None) -> bool:
    """64 字符 hex 字符串 = sha256 hex 输出格式。"""
    if not h or len(h) != 64:
        return False
    try:
        int(h, 16)
        return True
    except ValueError:
        return False


def _is_bcrypt_hash(h: str | None) -> bool:
    if not h:
        return False
    return h.startswith(("$2a$", "$2b$", "$2y$")) and len(h) >= 50


async def scan(database_url: str) -> list[dict]:
    parsed = urlparse(database_url)
    # asyncpg 用 postgresql:// 不是 postgresql+asyncpg://
    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn=dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT id, name, department, is_active, enrollment_token_hash,
                   rotation_token_hash, enrollment_consumed_at, enrollment_expires_at
            FROM openclaw_instances
            WHERE enrollment_token_hash IS NOT NULL
               OR rotation_token_hash IS NOT NULL
            ORDER BY id
            """
        )
    finally:
        await conn.close()

    findings: list[dict] = []
    for row in rows:
        e_legacy = _is_legacy_sha256_hash(row["enrollment_token_hash"])
        r_legacy = _is_legacy_sha256_hash(row["rotation_token_hash"])
        if not (e_legacy or r_legacy):
            continue

        # 描述老 hash 的暴露面
        flags = []
        if e_legacy:
            consumed = row["enrollment_consumed_at"] is not None
            expired = (
                row["enrollment_expires_at"] is not None
                and row["enrollment_expires_at"] < __import__("datetime").now_bjt()
            )
            if consumed:
                flags.append("enrollment_token_hash=sha256(已消费, 无影响)")
            elif expired:
                flags.append("enrollment_token_hash=sha256(已过期, 无影响)")
            else:
                flags.append("enrollment_token_hash=sha256(待消费, 仍可 enroll)")
        if r_legacy:
            flags.append("rotation_token_hash=sha256(待消费, 仍可 rotate)")

        findings.append({
            "instance_id": row["id"],
            "name": row["name"],
            "department": row["department"],
            "status": "active" if row["is_active"] else "inactive",
            "issues": flags,
        })
    return findings


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出 JSON 给监控/CI 用")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: 环境变量 DATABASE_URL 未设置", file=sys.stderr)
        sys.exit(1)

    findings = await scan(database_url)

    if args.json:
        print(json.dumps({"legacy_hash_count": len(findings), "findings": findings}, ensure_ascii=False, indent=2))
        return

    if not findings:
        print("✓ 所有 instance 的 enrollment / rotation token hash 均已是 bcrypt 格式")
        return

    print(f"⚠ 发现 {len(findings)} 个 instance 仍持有 sha256 (legacy) hash:\n")
    for f in findings:
        print(f"  ▸ {f['instance_id']}  ({f['name']}, {f['department']}, status={f['status']})")
        for issue in f["issues"]:
            print(f"      - {issue}")
        print()
    print(
        "说明: verify_enrollment_token 已兼容 legacy sha256 hash, 这些 instance 仍能正常 enroll/rotate.\n"
        "    enroll 成功后 hash 会被清空, 后续新签发的 token 一律走 bcrypt.\n"
        "    若希望立即清掉, 可在管理界面 regenerate-enrollment 让流程重走。"
    )


if __name__ == "__main__":
    asyncio.run(main())
