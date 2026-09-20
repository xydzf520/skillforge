"""Validator 集成端到端测试: 上传非法/合规 SKILL.md, 验证拦截行为。

测试矩阵:
  T1) frontmatter 缺 description → 拒
  T2) name 含未知字段 typo → 拒
  T3) description 含尖括号 → 拒
  T4) approval_level 超范围 → 拒
  T5) decision_mode 非法值 → 拒
  T6) ASCII name 不是 hyphen-case → 拒
  T7) 中文 name 通过 (SkillForge 显示名)
  T8) 完全合规的 SKILL.md → 通过 + git commit 成功
  T9) 超过 1000 行的 SKILL.md → structural_validator 拒
  T10) git_service.commit_all 在结构校验失败时阻止 commit
"""

from __future__ import annotations

import sys
from pathlib import Path
from app.common.time_utils import now_bjt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
GRAY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"

PROBLEMS: list[str] = []


def step(title: str) -> None:
    print(f"\n{BOLD}{CYAN}━━━ {title} ━━━{RESET}")


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")
    PROBLEMS.append(msg)


def warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}")


def info(msg: str) -> None:
    print(f"  {GRAY}·{RESET} {msg}")


# ──────────────────────────────────────────────
# T1-T7: quick_validate 单元用例
# ──────────────────────────────────────────────


def test_quick_validate_cases():
    from app.skills.validators import quick_validate

    step("T1: frontmatter 缺 description → 拒")
    md = "---\nname: my-skill\n---\n# Hi\n"
    r = quick_validate(md)
    if not r.ok and any("description" in e for e in r.errors):
        ok("缺 description 被拒")
    else:
        fail(f"应拒缺 description, 实际 ok={r.ok}, errors={r.errors}")

    step("T2: 未知 frontmatter 字段 (typo) → 拒")
    md = "---\nname: my-skill\ndescription: x\nappoval_level: 1\n---\n# Hi\n"
    r = quick_validate(md)
    if not r.ok and any("appoval_level" in str(e) for e in r.errors):
        ok("typo 'appoval_level' 被识别为未知字段")
    else:
        fail(f"未拦截 typo 字段, errors={r.errors}")

    step("T3: description 含尖括号 → 拒")
    md = "---\nname: my-skill\ndescription: a <b> c\n---\n# Hi\n"
    r = quick_validate(md)
    if not r.ok and any("尖括号" in e for e in r.errors):
        ok("尖括号被拒")
    else:
        fail(f"未拒含尖括号, errors={r.errors}")

    step("T4: approval_level 超范围 → 拒")
    md = "---\nname: my-skill\ndescription: x\napproval_level: 99\n---\n# Hi\n"
    r = quick_validate(md)
    if not r.ok and any("approval_level" in e for e in r.errors):
        ok("approval_level=99 被拒")
    else:
        fail(f"approval_level 范围未校验, errors={r.errors}")

    step("T5: decision_mode 非法值 → 拒")
    md = "---\nname: my-skill\ndescription: x\ndecision_mode: random\n---\n# Hi\n"
    r = quick_validate(md)
    if not r.ok and any("decision_mode" in e for e in r.errors):
        ok("decision_mode=random 被拒")
    else:
        fail(f"decision_mode 未校验, errors={r.errors}")

    step("T6: ASCII name 不是 hyphen-case → 拒")
    md = "---\nname: MySkill\ndescription: x\n---\n# Hi\n"
    r = quick_validate(md)
    if not r.ok and any("hyphen-case" in e for e in r.errors):
        ok("MySkill (CamelCase) 被拒")
    else:
        fail(f"ASCII 非 hyphen-case 未拒, errors={r.errors}")

    step("T7: 中文 name 通过 (SkillForge 显示名)")
    md = "---\nname: 同步测试\ndescription: 同步测试 Skill\ndepartment: AI\n---\n# Hi\n"
    r = quick_validate(md)
    if r.ok:
        ok("中文显示名通过")
    else:
        fail(f"中文 name 被拒, errors={r.errors}")


# ──────────────────────────────────────────────
# T8-T10: 集成 service.save_skill_content 和 git commit
# ──────────────────────────────────────────────


async def test_integration():
    import asyncio
    from datetime import datetime

    from app.auth.models import User
    from app.common.exceptions import AppError
    from app.database import async_session_factory
    from app.skills.core.models import Skill
    from app.skills.lifecycle.service import save_skill_content, create_skill, validate_skill_id
    from sqlalchemy import delete

    RUN_TAG = f"e2eval-{int(now_bjt().timestamp())}"
    skill_id = f"{RUN_TAG}-test-skill"
    user_id = f"{RUN_TAG}-user"

    try:
        # 准备 user
        async with async_session_factory() as session:
            session.add(User(
                id=user_id, username=user_id, name="测试",
                role="ai_engineer", department="EC", is_active=True,
            ))
            await session.commit()

        step("T8: 完全合规的 SKILL.md 创建 → 通过 + git commit")
        clean_md = """---
name: 测试 Skill
description: 用于 validator e2e 测试
department: EC
approval_level: 1
---

# 测试 Skill

这是一个用于 validator 集成测试的 Skill。

## 步骤

简单的演示步骤。
"""
        async with async_session_factory() as session:
            try:
                result = await create_skill(
                    session,
                    skill_id=skill_id,
                    name="测试 Skill",
                    department="EC",
                    role="ai_engineer",
                    skill_md=clean_md,
                    user_id=user_id,
                )
                await session.commit()
                if result.get("git_commit"):
                    ok(f"合规 SKILL.md 创建成功, commit={result['git_commit'][:8]}")
                else:
                    fail(f"create_skill 没有返回 commit hash: {result}")
            except AppError as e:
                fail(f"合规 SKILL.md 被错误拒绝: {e.code}, detail={e.detail}")

        step("T9: 通过 save_skill_content 上传非法 SKILL.md → 应被拒")
        bad_md = "---\nname: 测试 Skill\n---\n# Hi\n"  # 缺 description
        async with async_session_factory() as session:
            try:
                await save_skill_content(
                    session,
                    skill_id=skill_id,
                    skill_md=bad_md,
                    user_id=user_id,
                )
                fail("非法 SKILL.md 没有被拒")
                await session.rollback()
            except AppError as e:
                if e.code == "SKILL_VALIDATION_FAILED":
                    ok(f"非法 SKILL.md 被拒, errors={e.detail.get('detail', {}).get('errors', [])[:2]}")
                else:
                    fail(f"异常类型不对: {e.code}")
                await session.rollback()

        step("T10: 通过 save_skill_content 上传超过 1000 行的 SKILL.md → 结构校验拒")
        # 1100 行的 SKILL.md
        long_lines = "\n".join(f"段落 {i}: 这是一行内容." for i in range(1100))
        long_md = f"---\nname: 测试 Skill\ndescription: 长 Skill\n---\n\n# 长 Skill\n\n{long_lines}\n"
        async with async_session_factory() as session:
            try:
                await save_skill_content(
                    session,
                    skill_id=skill_id,
                    skill_md=long_md,
                    user_id=user_id,
                )
                # quick_validate 不查行数, 由 git_service.commit_all 的 structural_validator 把关
                # 如果走到这里, 说明 quick_validate 通过了, 但 git commit 时被结构校验拒
                fail("1100 行 SKILL.md 被静默接受 (没有触发结构校验)")
                await session.rollback()
            except AppError as e:
                if e.code == "SKILL_VALIDATION_FAILED":
                    detail = e.detail.get("detail", {}).get("errors", []) if e.detail else []
                    if any("行数" in str(d) and "超过硬上限" in str(d) for d in detail):
                        ok("1100 行被结构校验硬上限拒")
                    else:
                        fail(f"行数硬上限错误信息异常: {detail}")
                else:
                    fail(f"异常类型不对: {e.code}")
                await session.rollback()

        step("T11: 真正的 git commit 后, SKILL.md 内容应该已落库")
        async with async_session_factory() as session:
            from sqlalchemy import select
            skill = (await session.execute(
                select(Skill).where(Skill.id == skill_id)
            )).scalar_one_or_none()
            if skill:
                from app.skills.core.git_service import git_service
                content = git_service.read_file(skill_id, "SKILL.md")
                if content and "用于 validator e2e 测试" in content:
                    ok(f"Git 仓库里能读到合规版本: {len(content)} 字节")
                else:
                    fail(f"Git 仓库内容不对: {content[:100] if content else None}")
            else:
                fail("DB 里找不到测试 Skill")

    finally:
        # 清理
        step("清理: 删除测试数据")
        try:
            async with async_session_factory() as session:
                await session.execute(delete(Skill).where(Skill.id == skill_id))
                await session.execute(delete(User).where(User.id == user_id))
                await session.commit()

            from app.skills.core.git_service import git_service
            import shutil
            sd = git_service.skill_dir(skill_id)
            if sd.exists():
                shutil.rmtree(sd)
            ok("清理完成")
        except Exception as e:
            warn(f"清理出错: {e}")


def main():
    test_quick_validate_cases()

    import asyncio
    asyncio.run(test_integration())

    step("总结")
    print(f"  捕获问题: {len(PROBLEMS)} 个")
    if PROBLEMS:
        print(f"\n{RED}{BOLD}问题清单:{RESET}")
        for i, p in enumerate(PROBLEMS, 1):
            print(f"  {RED}{i}.{RESET} {p}")
        sys.exit(1)
    else:
        print(f"\n{GREEN}{BOLD}全部通过 ✓{RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
