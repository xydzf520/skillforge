"""Skill 模板市场服务。

按行业/场景分类模板库，支持一键 fork 创建新 Skill。
模板存储在 skills-repo/_templates/ 目录下。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.skills.core.parser import skill_parser


def _templates_dir() -> Path:
    repo_dir = Path(settings.SKILL_REPO_PATH) / "_templates"
    if repo_dir.exists():
        return repo_dir
    return Path(__file__).resolve().parent / "templates"


def _validate_path_component(name: str, label: str = "ID") -> None:
    """校验路径组件，防止路径遍历攻击。"""
    from app.common.exceptions import AppError
    if not name or ".." in name or "/" in name or "\\" in name or name.startswith("."):
        raise AppError("PARAM_INVALID", 400, {"detail": f"{label} 不允许包含 .. / \\ 或以 . 开头"})


def _repair_template_frontmatter(content: str) -> str:
    if not content.startswith("---"):
        return content
    if re.match(r"^---\s*\n.*?\n---\s*\n", content, re.DOTALL):
        return content
    marker = content.find("---", 3)
    if marker <= 3:
        return content
    before = content[:marker].rstrip()
    after = content[marker + 3:]
    if after and not after.startswith(("\n", "\r")):
        after = "\n" + after
    return before + "\n---" + after


def _default_template_script(skill_id: str) -> str:
    return f'''#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

try:
    from skillforge_sdk import SkillForge
except Exception:
    class SkillForge:
        def __init__(self, skill_id):
            self.skill_id = skill_id

        def explore(self, prompt):
            return {{"prompt": prompt, "items": []}}


sf = SkillForge({skill_id!r})


def collect_inputs(payload):
    if isinstance(payload, dict) and payload:
        return payload
    return {{"source": "runtime", "collected": sf.explore("collect live inputs for this skill")}}


def main(payload):
    data = collect_inputs(payload or {{}})
    return {{
        "reports": [
            {{
                "title": "Skill run result",
                "summary": f"Processed {{len(data) if isinstance(data, dict) else 0}} input fields",
            }}
        ],
        "todos": [],
        "data": data,
    }}


if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    payload = json.loads(raw) if raw else (json.loads(sys.argv[1]) if len(sys.argv) > 1 else {{}})
    print(json.dumps(main(payload), ensure_ascii=False, indent=2))
'''


def list_templates() -> list[dict]:
    """列出所有可用模板，按行业分类。"""
    tpl_dir = _templates_dir()
    if not tpl_dir.exists():
        return []

    templates = []
    for skill_dir in sorted(tpl_dir.iterdir()):
        if skill_dir.name.startswith("."):
            continue
        if skill_dir.is_dir():
            skill_md_path = skill_dir / "SKILL.md"
            template_id = skill_dir.name
        else:
            if skill_dir.suffix.lower() != ".md":
                continue
            skill_md_path = skill_dir
            template_id = skill_dir.stem
        if not skill_md_path.exists():
            continue

        try:
            content = _repair_template_frontmatter(skill_md_path.read_text(encoding="utf-8"))
            parsed = skill_parser.parse(content)
            fm = parsed.frontmatter

            templates.append({
                "id": template_id,
                "name": fm.get("name", template_id),
                "description": fm.get("description", ""),
                "department": fm.get("department", ""),
                "category": fm.get("category", fm.get("department", "通用")),
                "trigger_type": fm.get("trigger_type", "manual"),
                "risk_level": fm.get("risk_level", "R1"),
                "display_name": fm.get("display_name", fm.get("name", template_id)),
                "steps_count": len(parsed.steps),
                "test_cases_count": len(parsed.test_cases),
                "tags": fm.get("tags", []),
            })
        except Exception as e:
            logger.warning("模板 {} 解析失败: {}", template_id, e)

    return templates


def get_template_detail(template_id: str) -> dict | None:
    """获取模板详情（含完整 SKILL.md 内容）。"""
    _validate_path_component(template_id, "template_id")
    tpl_root = _templates_dir()
    dir_candidate = tpl_root / template_id
    file_candidate = tpl_root / f"{template_id}.md"
    if dir_candidate.is_dir():
        tpl_dir = dir_candidate
        skill_md_path = tpl_dir / "SKILL.md"
    elif file_candidate.is_file():
        tpl_dir = tpl_root
        skill_md_path = file_candidate
    else:
        return None
    if not skill_md_path.exists():
        return None

    content = _repair_template_frontmatter(skill_md_path.read_text(encoding="utf-8"))
    parsed = skill_parser.parse(content)

    # 读取附加文件列表
    files = []
    if skill_md_path.parent == tpl_root:
        files.append("SKILL.md")
    else:
        for f in tpl_dir.rglob("*"):
            if f.is_file() and not f.name.startswith("."):
                files.append(str(f.relative_to(tpl_dir)))

    return {
        "id": template_id,
        "name": parsed.frontmatter.get("name", template_id),
        "display_name": parsed.frontmatter.get("display_name", parsed.frontmatter.get("name", template_id)),
        "description": parsed.frontmatter.get("description", ""),
        "category": parsed.frontmatter.get("category", parsed.frontmatter.get("department", "通用")),
        "department": parsed.frontmatter.get("department", ""),
        "skill_md": content,
        "frontmatter": parsed.frontmatter,
        "purpose": parsed.purpose,
        "steps_count": len(parsed.steps),
        "test_cases_count": len(parsed.test_cases),
        "files": files,
    }


async def fork_template(
    db: AsyncSession,
    template_id: str,
    new_skill_id: str,
    department: str,
    user_id: str,
) -> dict:
    """从模板 fork 创建新 Skill。"""
    from app.common.exceptions import AppError
    from app.skills import service as skill_service

    _validate_path_component(template_id, "template_id")
    _validate_path_component(new_skill_id, "skill_id")
    tpl_root = _templates_dir()
    dir_candidate = tpl_root / template_id
    file_candidate = tpl_root / f"{template_id}.md"
    if dir_candidate.is_dir():
        tpl_dir = dir_candidate
    elif file_candidate.is_file():
        tpl_dir = tpl_root
    else:
        raise AppError("TEMPLATE_NOT_FOUND", 404)
    files: dict[str, str] = {}
    if dir_candidate.is_dir():
        for file_path in tpl_dir.rglob("*"):
            if file_path.is_file() and not file_path.name.startswith("."):
                files[str(file_path.relative_to(tpl_dir))] = _repair_template_frontmatter(file_path.read_text(encoding="utf-8"))
    else:
        files["SKILL.md"] = _repair_template_frontmatter(file_candidate.read_text(encoding="utf-8"))

    skill_md = files.get("SKILL.md", "")
    if not skill_md:
        raise AppError("SKILL_VALIDATION_FAILED", 422, {"detail": "模板缺少 SKILL.md"})
    files.setdefault("scripts/main.py", _default_template_script(new_skill_id))
    parsed = skill_parser.parse(skill_md)
    frontmatter = dict(parsed.frontmatter or {})
    frontmatter["name"] = frontmatter.get("display_name") or frontmatter.get("name") or template_id
    if not frontmatter.get("description"):
        frontmatter["description"] = f"Forked from template {template_id}."
    frontmatter["department"] = department
    if not frontmatter.get("trigger_type"):
        frontmatter["trigger_type"] = "manual"
    if not frontmatter.get("risk_level"):
        metadata = frontmatter.get("metadata") if isinstance(frontmatter.get("metadata"), dict) else {}
        frontmatter["risk_level"] = (
            frontmatter.get("risk-level")
            or metadata.get("risk_level")
            or metadata.get("risk-level")
            or "R2"
        )
    parsed.frontmatter = frontmatter
    files["SKILL.md"] = skill_parser.render(parsed)

    display_name = frontmatter.get("display_name") or frontmatter.get("name") or new_skill_id
    result = await skill_service.create_skill_from_files(
        db,
        skill_id=new_skill_id,
        name=str(display_name),
        department=department,
        files=files,
        user_id=user_id,
        trigger_type=str(frontmatter.get("trigger_type", "manual")),
        trigger_expression=str(frontmatter.get("trigger_expression", "")),
        risk_level=str(frontmatter.get("risk_level", "R2")),
        approval_level=int(frontmatter.get("approval_level", 1) or 1),
    )

    # Fork 关系追踪：记录来源模板和版本
    from sqlalchemy import update
    from app.skills.core.access import SkillMember
    from app.skills.core.models import Skill

    db.add(
        SkillMember(
            skill_id=new_skill_id,
            user_id=user_id,
            role="owner",
            granted_by=user_id,
        )
    )
    await db.execute(
        update(Skill)
        .where(Skill.id == new_skill_id)
        .values(
            forked_from=template_id,
            fork_type="template",
            parent_version=result.get("git_commit"),
        )
    )

    return {
        "skill_id": new_skill_id,
        "template_id": template_id,
        "git_commit": result.get("git_commit"),
        "quality_score": result.get("quality_score"),
    }
