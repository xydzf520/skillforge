"""Skills 质量域服务。"""

from __future__ import annotations

from datetime import datetime

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import audit
from app.common.exceptions import AppError
from app.skills.core.git_service import git_service
from app.skills.core.parser import (
    Antipattern,
    Branch,
    DataInput,
    DecisionStep,
    OutputItem,
    SkillStructured,
    TestCase,
    skill_parser,
)
from app.skills.core.service_shared import sync_skill_fields_from_frontmatter, validate_skill_id
from app.common.time_utils import now_bjt


async def save_skill_structured(
    db: AsyncSession,
    skill_id: str,
    frontmatter: dict | None = None,
    purpose: str | None = None,
    steps: list[dict] | None = None,
    antipatterns: list[dict] | None = None,
    output_definition: list[dict] | None = None,
    data_inputs: list[dict] | None = None,
    test_cases: list[dict] | None = None,
    custom_sections: dict | None = None,
    policy_pack: dict | None = None,
    user_id: str = "system",
) -> dict:
    from sqlalchemy import select
    from app.skills.core.models import Skill

    validate_skill_id(skill_id)
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    structured = SkillStructured()
    current_md = git_service.read_file(skill_id, "SKILL.md") or ""
    current_parsed = skill_parser.parse(current_md)
    structured.frontmatter = dict(current_parsed.frontmatter)
    if frontmatter:
        structured.frontmatter.update(frontmatter)

    structured.purpose = purpose if purpose is not None else current_parsed.purpose

    if steps is not None:
      structured.steps = [
          DecisionStep(
              id=item.get("id", ""),
              name=item.get("name", ""),
              description=item.get("description", ""),
              branches=[
                  Branch(
                      condition=branch.get("condition", ""),
                      conclusion=branch.get("conclusion", ""),
                      action=branch.get("action", ""),
                      next_step=branch.get("next_step"),
                  )
                  for branch in item.get("branches", [])
              ],
          )
          for item in steps
      ]
    else:
      structured.steps = list(current_parsed.steps)

    if antipatterns is not None:
      structured.antipatterns = [
          Antipattern(
              scenario=item.get("scenario", ""),
              correct_action=item.get("correct_action", ""),
              source=item.get("source", ""),
          )
          for item in antipatterns
      ]
    else:
      structured.antipatterns = list(current_parsed.antipatterns)

    if output_definition is not None:
      structured.output_definition = [
          OutputItem(
              name=item.get("name", ""),
              format=item.get("format", ""),
              recipient=item.get("recipient", ""),
              approval_level=item.get("approval_level", ""),
          )
          for item in output_definition
      ]
    else:
      structured.output_definition = list(current_parsed.output_definition)

    if data_inputs is not None:
      structured.data_inputs = [
          DataInput(
              name=item.get("name", ""),
              source=item.get("source", ""),
              frequency=item.get("frequency", ""),
          )
          for item in data_inputs
      ]
    else:
      structured.data_inputs = list(current_parsed.data_inputs)

    if test_cases is not None:
      structured.test_cases = [
          TestCase(
              name=item.get("name", ""),
              input_data=item.get("input_data", {}),
              expected_output=item.get("expected_output", {}),
              assert_rules=item.get("assert_rules", []),
          )
          for item in test_cases
      ]
    else:
      structured.test_cases = list(current_parsed.test_cases)

    structured.custom_sections = custom_sections if custom_sections is not None else dict(current_parsed.custom_sections)

    new_md = skill_parser.render(structured)
    skill_md_changed = new_md != current_md
    policy_pack_changed = False
    changes: list[str] = []

    if skill_md_changed:
        git_service.write_file(skill_id, "SKILL.md", new_md)
        changes.append("SKILL.md")

    if policy_pack is not None:
        policy_pack_raw = yaml.safe_dump(policy_pack, allow_unicode=True, sort_keys=False)
        old_policy_raw = git_service.read_file(skill_id, "policy_pack.yaml") or ""
        if old_policy_raw != policy_pack_raw:
            git_service.write_file(skill_id, "policy_pack.yaml", policy_pack_raw)
            changes.append("policy_pack.yaml")
            policy_pack_changed = True

    if not changes:
        return {
            "skill_id": skill_id,
            "git_commit": skill.git_commit,
            "skill_md_changed": False,
            "policy_pack_changed": False,
            "noop": True,
        }

    commit_sha = git_service.commit_all(f"结构化编辑更新 {', '.join(changes)}", user_id, skill_id=skill_id)
    if frontmatter:
        sync_skill_fields_from_frontmatter(skill, frontmatter)
    skill.git_commit = commit_sha
    skill.updated_at = now_bjt()
    await db.flush()
    await audit.log(user_id, "skill.edit.structured", "skill", skill_id, detail={"changes": changes})
    return {
        "skill_id": skill_id,
        "git_commit": commit_sha,
        "skill_md_changed": skill_md_changed,
        "policy_pack_changed": policy_pack_changed,
        "noop": False,
    }


def user_can_view_all_departments(user) -> bool:
    if not user:
        return False
    if getattr(user, "can_view_all", False):
        return True
    return user.role in ("admin", "ai_engineer", "director")


async def get_param_usages_grouped(
    db: AsyncSession,
    param_name: str,
    *,
    current_user,
    exclude_skill: str | None = None,
) -> dict:
    from app.skills.core.param_index import find_param_usages

    usages = await find_param_usages(db, param_name, exclude_skill=exclude_skill)
    if not user_can_view_all_departments(current_user):
        usages = [item for item in usages if item.get("department") == current_user.department]

    by_skill: dict[str, dict] = {}
    for item in usages:
        sid = item["skill_id"]
        if sid not in by_skill:
            by_skill[sid] = {
                "skill_id": sid,
                "skill_name": item["skill_name"],
                "department": item["department"],
                "usages": [],
            }
        by_skill[sid]["usages"].append({
            "step_id": item["step_id"],
            "step_name": item["step_name"],
            "branch_index": item["branch_index"],
            "condition": item["condition"],
            "conclusion": item["conclusion"],
            "action": item["action"],
            "kind": item["kind"],
        })

    return {
        "param_name": param_name,
        "total_usages": len(usages),
        "skills_count": len(by_skill),
        "by_skill": list(by_skill.values()),
    }


async def get_cross_skill_conflicts_scoped(
    db: AsyncSession,
    *,
    current_user,
    department: str | None = None,
) -> dict:
    from app.skills.lifecycle.cross_skill_conflict import get_conflicts

    if not user_can_view_all_departments(current_user):
        department = current_user.department

    conflicts = await get_conflicts(db, department=department)
    return {
        "total": len(conflicts),
        "department": department,
        "conflicts": conflicts,
    }


async def get_skill_conflicts_with_access(
    db: AsyncSession,
    skill_id: str,
    *,
    current_user,
) -> list[dict]:
    from app.skills.core.service_shared import ensure_skill_access
    from app.skills.lifecycle.cross_skill_conflict import conflicts_for_skill

    await ensure_skill_access(db, skill_id, current_user, "read")
    return await conflicts_for_skill(db, skill_id)
