"""构建��作台所需的 Skill 结构和模块摘要。"""

from __future__ import annotations

import time
from types import SimpleNamespace

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import AppError
from app.skills.core.git_service import git_service
from app.skills.core.models import Skill
from app.skills.core.parser import skill_parser
from app.workbench.schemas import ModuleState, SkillStructure

# 简易内存缓存，避免短时间内重复解析同一 Skill
_structure_cache: dict[str, tuple[float, SkillStructure]] = {}
_CACHE_TTL = 10  # 秒


def invalidate_structure_cache(skill_id: str | None = None):
    """清除结构缓存。skill_id=None 时清除全部。"""
    if skill_id:
        _structure_cache.pop(skill_id, None)
    else:
        _structure_cache.clear()


def load_skill_structure(skill_id: str) -> SkillStructure:
    """从现有 Skill 文件读取最小结构化状态（带 10s 内存缓存）。"""
    cached = _structure_cache.get(skill_id)
    if cached and (time.monotonic() - cached[0]) < _CACHE_TTL:
        return cached[1]

    skill_md = git_service.read_file(skill_id, "SKILL.md")
    if not skill_md:
        raise AppError("SKILL_NOT_FOUND", 404)

    policy_pack_raw = git_service.read_file(skill_id, "policy_pack.yaml") or ""
    parsed = skill_parser.parse(skill_md)

    try:
        policy_pack = yaml.safe_load(policy_pack_raw) or {}
    except yaml.YAMLError:
        policy_pack = {}

    params = []
    if isinstance(policy_pack, dict):
        for key, value in policy_pack.items():
            params.append({
                "name": key,
                "default_value": value,
                "description": "",
            })

    result = SkillStructure(
        meta={
            "name": parsed.frontmatter.get("name", skill_id),
            "department": parsed.frontmatter.get("department", ""),
            "trigger_type": parsed.frontmatter.get("trigger_type", ""),
            "risk_level": parsed.frontmatter.get("risk_level", ""),
        },
        goal=parsed.purpose or parsed.frontmatter.get("description", ""),
        rules=[
            {
                "id": step.id,
                "name": step.name,
                "description": step.description,
                "branches": [
                    {
                        "condition": branch.condition,
                        "conclusion": branch.conclusion,
                        "action": branch.action,
                        "next_step": branch.next_step,
                    }
                    for branch in step.branches
                ],
            }
            for step in parsed.steps
        ],
        params=params,
        output_table=[
            {
                "name": item.name,
                "format": item.format,
                "recipient": item.recipient,
                "approval_level": item.approval_level,
            }
            for item in parsed.output_definition
        ],
        test_cases=[
            {
                "name": item.name,
                "input_data": item.input_data,
                "expected_output": item.expected_output,
                "assert_rules": item.assert_rules,
            }
            for item in parsed.test_cases
        ],
        custom_sections=parsed.custom_sections,
    )
    _structure_cache[skill_id] = (time.monotonic(), result)
    return result


def summarize_modules(structure: SkillStructure) -> list[ModuleState]:
    """根据 Skill 结构计算模块状态摘要。"""
    return [
        ModuleState(module="goal", label="目标", status="ready" if structure.goal else "empty", item_count=1 if structure.goal else 0),
        ModuleState(module="rules", label="规则", status="ready" if structure.rules else "empty", item_count=len(structure.rules)),
        ModuleState(module="params", label="参数", status="ready" if structure.params else "empty", item_count=len(structure.params)),
        ModuleState(
            module="output_table", label="输出表格",
            status="ready" if structure.output_table else "empty",
            item_count=len(structure.output_table),
        ),
        ModuleState(
            module="test_cases", label="测试样例",
            status="ready" if structure.test_cases else "empty",
            item_count=len(structure.test_cases),
        ),
        ModuleState(module="workflow", label="工作流", status="draft", item_count=0),
    ]


def build_chat_context(
    skill_id: str,
    *,
    active_module: str | None = None,
    selection: dict | None = None,
    draft_snapshot: dict | None = None,
    recent_failures: list | None = None,
    message_history: list | None = None,
) -> dict:
    """
    构建 AI 对话上下文。

    优先级：
    1. draft_snapshot（未保存的前端状态）> 已落库的 Skill 数据
    2. selection（用户选中的内容）作为重点修改区域
    3. recent_failures 作为修改动机
    """
    # 1. 加载已落库的 Skill 结构
    persisted = load_skill_structure(skill_id)
    structure_dict = persisted.model_dump()

    # 2. 如果前端传了 draft_snapshot，用它覆盖对应模块
    if draft_snapshot:
        for module_key, module_data in draft_snapshot.items():
            if module_key in structure_dict:
                structure_dict[module_key] = module_data

    # 3. 构建上下文对象
    context = {
        "skill_id": skill_id,
        "skill_structure": structure_dict,
        "active_module": active_module,
        "selection": selection,
        "recent_failures": recent_failures or [],
        "message_history": message_history or [],
    }

    # 4. 如果有选区，提取选区周围的上下文
    if selection and selection.get("text"):
        context["selection_context"] = {
            "module": selection.get("module_id", active_module),
            "text": selection["text"],
            "line_range": f"{selection.get('start_line', '?')}-{selection.get('end_line', '?')}",
        }

    return context


context_builder = SimpleNamespace(
    load_skill_structure=load_skill_structure,
    summarize_modules=summarize_modules,
    build_chat_context=build_chat_context,
)


async def build_skill_context(db: AsyncSession, skill_id: str) -> dict:
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise AppError("SKILL_NOT_FOUND", 404)

    structure = load_skill_structure(skill_id)
    return {
        "id": skill_id,
        "name": skill.name,
        "status": skill.status,
        "department": skill.department,
        "risk_level": skill.risk_level,
        "skill": {
            "name": skill.name,
            "meta": structure.meta,
            "goal": structure.goal,
            "status": skill.status,
            "department": skill.department,
            "rules": structure.rules,
            "params": structure.params,
            "output_table": structure.output_table,
            "test_cases": structure.test_cases,
            "custom_sections": structure.custom_sections,
        },
        "modules": [item.model_dump() for item in summarize_modules(structure)],
        "parsed": {
            "frontmatter": structure.meta,
            "goal": structure.goal,
            "rules": structure.rules,
            "params": structure.params,
            "output_table": structure.output_table,
            "test_cases": structure.test_cases,
        },
    }
