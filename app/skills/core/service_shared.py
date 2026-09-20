"""Skills 服务层共享 helper。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.skills.core.models import Skill
from app.skills.core.access import require_skill_access

# v2.8.0 / v2.8.3 D1：SKILL_ID_PATTERN + validate_skill_id 在 core.id_gen
from app.skills.core.id_gen import (  # noqa: F401,E402
    SKILL_ID_PATTERN,
    validate_skill_id as _validate_skill_id,
)


async def ensure_skill_access(db: AsyncSession, skill_id: str, user, action: str = "read") -> Skill:
    """统一 Skill 资源访问校验。"""
    return await require_skill_access(db, skill_id, user, action)


async def ensure_skill_department_access(db: AsyncSession, skill_id: str, user) -> Skill:
    """兼容旧调用点：按 read 访问校验 Skill。"""
    return await ensure_skill_access(db, skill_id, user, "read")


def validate_skill_id(skill_id: str) -> str:
    """v2.8.0：委托到 id_gen，保留此 wrapper 防破坏 import path。"""
    return _validate_skill_id(skill_id)


def parse_approval_level(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    mapping = {
        "none": 0,
        "l0": 0,
        "lead": 1,
        "l1": 1,
        "director": 2,
        "l2": 2,
        "chairman": 3,
        "l3": 3,
    }
    text = str(value).strip().lower()
    if text.isdigit():
        return int(text)
    return mapping.get(text)


def sync_skill_fields_from_frontmatter(skill: Skill, frontmatter: dict) -> None:
    if frontmatter.get("name"):
        skill.name = frontmatter["name"]
    if frontmatter.get("department"):
        skill.department = frontmatter["department"]
    if frontmatter.get("role") is not None:
        skill.role = frontmatter["role"]
    if frontmatter.get("trigger_type"):
        skill.trigger_type = frontmatter["trigger_type"]
    if frontmatter.get("trigger_expression") is not None:
        skill.trigger_expression = frontmatter["trigger_expression"]
    if frontmatter.get("risk_level"):
        skill.risk_level = frontmatter["risk_level"]
    approval_level = parse_approval_level(frontmatter.get("approval_level"))
    if approval_level is not None:
        skill.approval_level = approval_level
    target_users = frontmatter.get("target_users")
    if isinstance(target_users, list):
        skill.target_users = target_users
    approver = frontmatter.get("approver")
    if approver is not None:
        skill.approver = approver
