"""[H3] aiclaw 反向导入的 frontmatter 部门越权防御测试。

漏洞:
  原实现 require_department_access(fm_department, ai_engineer) 始终为 True,
  所以 ai_engineer 用户能让 frontmatter 把 skill 写到任意第三个部门。

修复:
  frontmatter 部门必须 ∈ {instance.department, current_user.department},
  admin 例外。
"""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.aiclaw.router import _import_skill_from_device_impl
from app.common.exceptions import AppError


def _b64_skill_md(department: str, name: str = "test-skill") -> str:
    md = f"""---
name: {name}
description: 测试导入 Skill
department: {department}
risk_level: R2
trigger_type: manual
---

# 测试 Skill
"""
    return base64.b64encode(md.encode("utf-8")).decode("ascii")


def _make_user(role: str, department: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"u_{role}",
        role=role,
        department=department,
        can_view_all=False,
    )


def _make_db_with_no_existing_skill():
    """mock db: scalar() 返回 None (skill 不存在)。"""
    db = MagicMock()
    db.scalar = AsyncMock(return_value=None)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_aiclaw_deps(monkeypatch, tmp_path):
    """mock bridge / git_service / settings, 隔离测试。"""
    from app.aiclaw import router as aiclaw_router

    # 1. mock _get_instance
    instance = SimpleNamespace(
        id="inst-EC",
        department="EC",
        name="EC bridge",
    )
    monkeypatch.setattr(
        aiclaw_router, "_get_instance",
        AsyncMock(return_value=instance),
    )

    # 2. mock AIClawClient — read_skill_from_device 默认返回 SKILL.md
    fake_client = MagicMock()
    fake_client.read_skill_from_device = AsyncMock(return_value={
        "files": [
            {"path": "SKILL.md", "content_b64": _b64_skill_md("SEM")},  # 默认 frontmatter SEM
        ],
    })
    monkeypatch.setattr(aiclaw_router, "AIClawClient", lambda iid: fake_client)

    # 3. mock git_service — create_skill_dir / write_file 走 tmp_path 不真碰系统
    fake_git = MagicMock()
    fake_git.create_skill_dir = MagicMock()
    fake_git.write_file = MagicMock()
    fake_git.commit_all = MagicMock(return_value="abc123")
    fake_git.has_uncommitted_changes = MagicMock(return_value=False)
    lock_calls: list[str] = []

    @asynccontextmanager
    async def _noop_lock(_skill_id: str):
        lock_calls.append(_skill_id)
        yield

    fake_git.skill_advisory_lock = _noop_lock
    monkeypatch.setattr("app.skills.core.git_service.git_service", fake_git)

    # 4. settings.SKILL_REPO_PATH → tmp_path 避免触碰真实仓库
    from app.config import settings as real_settings
    monkeypatch.setattr(real_settings, "SKILL_REPO_PATH", str(tmp_path / "skills_repo"))

    return {"instance": instance, "client": fake_client, "git": fake_git, "lock_calls": lock_calls}


# ═══════════════════════════════════════════════════════
# 越权拒绝
# ═══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_import_rejects_third_party_department_for_ai_engineer(mock_aiclaw_deps):
    """[H3] ai_engineer 从 EC 设备 import, frontmatter 声明 SEM (第三方部门) → 拒绝。

    旧实现因 require_department_access(SEM, ai_engineer)=True 会放过, 让 SEM
    部门凭空多出一个 skill。修复后必须拒绝。
    """
    # 用户是 EC 部门 ai_engineer; instance 也是 EC; frontmatter 是 SEM
    user = _make_user("ai_engineer", "EC")
    db = _make_db_with_no_existing_skill()

    # 默认 fixture 已经把 frontmatter 设为 SEM
    with pytest.raises(AppError) as exc_info:
        await _import_skill_from_device_impl(
            db,
            instance_id="inst-EC",
            skill_id="EC-test-01",
            current_user=user,
        )
    assert exc_info.value.code == "AUTH_DEPARTMENT_DENIED"
    assert "SEM" in str(exc_info.value.detail)
    assert mock_aiclaw_deps["lock_calls"] == ["EC-test-01"]
    mock_aiclaw_deps["git"].commit_all.assert_not_called()


def _captured_skill_department(db_mock) -> str | None:
    """从 db.add 的调用记录里找到 Skill 实例并取它的 department。"""
    for call in db_mock.add.call_args_list:
        obj = call.args[0] if call.args else None
        if obj is not None and hasattr(obj, "department"):
            return getattr(obj, "department")
    return None


def _captured_skill(db_mock):
    """从 db.add 的调用记录里找到被写入的 Skill 实例。"""
    for call in db_mock.add.call_args_list:
        obj = call.args[0] if call.args else None
        if obj is not None and hasattr(obj, "department") and hasattr(obj, "git_commit"):
            return obj
    return None


@pytest.mark.asyncio
async def test_import_allows_instance_department(monkeypatch, mock_aiclaw_deps):
    """frontmatter 声明的部门 == instance 部门 → 允许。"""
    user = _make_user("ai_engineer", "BG")  # 用户是 BG 部门
    db = _make_db_with_no_existing_skill()

    # 改 frontmatter 为 EC (instance 部门)
    mock_aiclaw_deps["client"].read_skill_from_device = AsyncMock(return_value={
        "files": [{"path": "SKILL.md", "content_b64": _b64_skill_md("EC")}],
    })

    result = await _import_skill_from_device_impl(
        db,
        instance_id="inst-EC",
        skill_id="EC-test-02",
        current_user=user,
    )
    assert result.get("ok") is True
    assert result.get("git_commit") == "abc123"
    assert _captured_skill_department(db) == "EC"
    skill = _captured_skill(db)
    assert skill is not None
    assert skill.git_commit == "abc123"
    assert mock_aiclaw_deps["lock_calls"] == ["EC-test-02"]
    mock_aiclaw_deps["git"].commit_all.assert_called_once_with(
        "从 AIClaw 设备 inst-EC 导入 Skill: EC-test-02",
        "u_ai_engineer",
        skill_id="EC-test-02",
    )


@pytest.mark.asyncio
async def test_import_allows_user_department(monkeypatch, mock_aiclaw_deps):
    """frontmatter 部门 == 当前用户部门 → 允许 (即便 instance 是不同部门)。"""
    user = _make_user("ai_engineer", "BG")  # BG 部门用户
    db = _make_db_with_no_existing_skill()

    # frontmatter 是 BG (用户自己的部门)
    mock_aiclaw_deps["client"].read_skill_from_device = AsyncMock(return_value={
        "files": [{"path": "SKILL.md", "content_b64": _b64_skill_md("BG")}],
    })

    # instance 是 EC, 但用户是 BG, 所以需要先把 instance 部门访问权 mock 通过
    # 实际 require_department_access(EC, ai_engineer)=True 因为 ai_engineer 全部门可读
    result = await _import_skill_from_device_impl(
        db,
        instance_id="inst-EC",
        skill_id="BG-test-03",
        current_user=user,
    )
    assert result.get("ok") is True
    assert result.get("git_commit") == "abc123"
    assert _captured_skill_department(db) == "BG"
    skill = _captured_skill(db)
    assert skill is not None
    assert skill.git_commit == "abc123"
    assert mock_aiclaw_deps["lock_calls"] == ["BG-test-03"]
    mock_aiclaw_deps["git"].commit_all.assert_called_once_with(
        "从 AIClaw 设备 inst-EC 导入 Skill: BG-test-03",
        "u_ai_engineer",
        skill_id="BG-test-03",
    )


@pytest.mark.asyncio
async def test_import_admin_can_specify_any_department(monkeypatch, mock_aiclaw_deps):
    """admin 用户 — frontmatter 可任意指定部门 (例外条款)。"""
    user = _make_user("admin", "EC")
    db = _make_db_with_no_existing_skill()

    # frontmatter 第三个部门 — admin 例外
    mock_aiclaw_deps["client"].read_skill_from_device = AsyncMock(return_value={
        "files": [{"path": "SKILL.md", "content_b64": _b64_skill_md("FAR-AWAY-DEPT")}],
    })

    result = await _import_skill_from_device_impl(
        db,
        instance_id="inst-EC",
        skill_id="X-test-04",
        current_user=user,
    )
    assert result.get("ok") is True
    assert result.get("git_commit") == "abc123"
    assert _captured_skill_department(db) == "FAR-AWAY-DEPT"
    skill = _captured_skill(db)
    assert skill is not None
    assert skill.git_commit == "abc123"
    assert mock_aiclaw_deps["lock_calls"] == ["X-test-04"]
    mock_aiclaw_deps["git"].commit_all.assert_called_once_with(
        "从 AIClaw 设备 inst-EC 导入 Skill: X-test-04",
        "u_admin",
        skill_id="X-test-04",
    )
