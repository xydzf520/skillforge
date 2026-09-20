#!/usr/bin/env python3
"""一次性迁移: 把主仓 ./playbooks/ 搬到 {SKILL_REPO_PATH}/playbooks/。

动机: v2.0.8 把 Playbook 纳入 Skill 同仓管理, PLAYBOOKS_DIR 改为
`{SKILL_REPO_PATH}/playbooks`, 这样 git_service 能统一做 version/tag/push,
reviews/service 的 Playbook 审核流程自动工作在正确的仓库上。

用法:
    python scripts/migrate_playbooks_to_skills_repo.py           # 执行迁移
    python scripts/migrate_playbooks_to_skills_repo.py --dry-run # 预演
    python scripts/migrate_playbooks_to_skills_repo.py --force   # 覆盖已存在文件

迁移内容 (所有 *.yaml / *.yml + templates/ 子目录):
    ./playbooks/                 → {SKILL_REPO_PATH}/playbooks/
    ./playbooks/templates/       → {SKILL_REPO_PATH}/playbooks/templates/

迁移后:
    1. 旧 ./playbooks/ 目录留 .gitkeep 空目录(不再作为运行时路径, 已被 app 忽略)
    2. 新目录由 git_service commit -> tag -> push 管理
    3. 第一次 `save_playbook` 或 `publish` 会触发 commit 并进入 skills-repo 版本流
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _find_repo_root() -> Path:
    """回溯找 SkillForge 主仓根 (带 app/ + playbooks/)。"""
    here = Path(__file__).resolve().parent
    for candidate in [here, here.parent, here.parent.parent]:
        if (candidate / "app").is_dir() and (candidate / "playbooks").is_dir():
            return candidate
    print("ERROR: 无法定位 SkillForge 主仓根目录", file=sys.stderr)
    sys.exit(1)


def _resolve_target_root() -> Path:
    """读 settings.SKILL_REPO_PATH 决定目标路径。"""
    sys.path.insert(0, str(_find_repo_root()))
    from app.config import settings  # noqa: E402
    target = Path(settings.SKILL_REPO_PATH) / "playbooks"
    return target


def _iter_source_entries(source_root: Path):
    """枚举 source_root 下需要迁移的文件/目录 (yaml/yml + templates/)。"""
    if not source_root.is_dir():
        return
    for entry in sorted(source_root.iterdir()):
        if entry.name.startswith("."):
            continue  # .gitkeep 保留在源目录
        if entry.is_dir() and entry.name == "templates":
            yield entry
        elif entry.is_file() and entry.suffix in (".yaml", ".yml"):
            yield entry


def _copy_entry(src: Path, dst: Path, *, force: bool, dry: bool) -> str:
    """拷贝单个文件或目录, 返回人类可读的动作描述。"""
    if dst.exists() and not force:
        return f"[skip-exists] {src.name}"
    if dry:
        return f"[dry-run]    {src.name} -> {dst}"
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return f"[migrated]   {src.name} -> {dst}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="预演, 不实际迁移")
    parser.add_argument("--force", action="store_true", help="目标已存在时覆盖")
    parser.add_argument(
        "--remove-source",
        action="store_true",
        help="迁移成功后删除主仓 ./playbooks/ 下的 *.yaml / *.yml 和 templates/ (保留 .gitkeep)",
    )
    args = parser.parse_args()

    repo_root = _find_repo_root()
    source = repo_root / "playbooks"
    target = _resolve_target_root()

    print(f"source: {source}")
    print(f"target: {target}")
    print()

    entries = list(_iter_source_entries(source))
    if not entries:
        print("没有可迁移的 playbook 文件")
        return 0

    target.mkdir(parents=True, exist_ok=True)
    print(f"将迁移 {len(entries)} 项:")
    results = []
    for entry in entries:
        dst = target / entry.name
        results.append(_copy_entry(entry, dst, force=args.force, dry=args.dry_run))
    for line in results:
        print(f"  {line}")

    if args.remove_source and not args.dry_run:
        removed = 0
        for entry in entries:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed += 1
        print(f"\n已移除源目录 {removed} 项 (保留 .gitkeep)")

    print("\n完成。下一步:")
    print("  1. cd $SKILL_REPO_PATH && git add playbooks/ && git commit -m 'migrate: playbooks 纳入 skills-repo'")
    print("  2. 重启 SkillForge 后端")
    print("  3. 跑一次 pytest tests/test_playbooks.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
