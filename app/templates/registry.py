"""任务模板库注册（v7 G3）。

集中管理 skills-repo/_templates/ 下的可选模板，并提供 list_templates() 供前端 SkillStudio
列出供业务方选择。

v7 E1：列表与单个模板元数据都通过 Redis tpl:list / tpl:meta 命名空间缓存。
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

import yaml


_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "skills-repo" / "_templates"
_cache: list[dict] | None = None
_lock = Lock()


def _load_template_meta(template_dir: Path) -> dict | None:
    intent = template_dir / "intent.md"
    skill = template_dir / "SKILL.md"
    policy = template_dir / "policy.yaml"
    scripts_main = template_dir / "scripts" / "main.py"
    if not (intent.exists() and skill.exists() and policy.exists()):
        return None

    try:
        policy_data = yaml.safe_load(policy.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        policy_data = {}

    skill_text = skill.read_text(encoding="utf-8")
    name = template_dir.name
    description = ""
    department = ""
    risk_level = ""
    if skill_text.startswith("---"):
        try:
            head = skill_text.split("---", 2)[1]
            front = yaml.safe_load(head) or {}
            name = front.get("name", name)
            description = front.get("description", "")
            department = front.get("department", "")
            risk_level = front.get("risk_level", "")
        except yaml.YAMLError:
            pass

    test_cases = []
    tests_dir = template_dir / "tests"
    if tests_dir.exists():
        for case_file in sorted(tests_dir.glob("*.json")):
            try:
                test_cases.append(json.loads(case_file.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue

    return {
        "id": template_dir.name,
        "name": name,
        "description": description,
        "department": department,
        "risk_level": risk_level,
        "trigger": (policy_data.get("trigger") or {}).get("description", ""),
        "output_adapter": (policy_data.get("output") or {}).get("adapter", ""),
        "has_scripts": scripts_main.exists(),
        "test_count": len(test_cases),
        "files": {
            "intent_md": "intent.md",
            "skill_md": "SKILL.md",
            "policy_yaml": "policy.yaml",
            "scripts_main": "scripts/main.py" if scripts_main.exists() else None,
        },
    }


def list_templates(use_cache: bool = True) -> list[dict]:
    """列出所有模板的元数据（进程级缓存）。"""
    global _cache
    if use_cache and _cache is not None:
        return _cache
    with _lock:
        if use_cache and _cache is not None:
            return _cache
        if not _TEMPLATES_DIR.exists():
            _cache = []
            return _cache
        templates = []
        for sub in sorted(_TEMPLATES_DIR.iterdir()):
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            meta = _load_template_meta(sub)
            if meta:
                templates.append(meta)
        _cache = templates
        return _cache


async def list_templates_async() -> list[dict]:
    """async 版本：先读 Redis tpl:list 命名空间，未命中走文件系统并写回。"""
    from app.common.cache import cached_namespace_get, cached_namespace_set

    cached = await cached_namespace_get("tpl:list", "all")
    if cached is not None:
        return cached
    templates = list_templates()
    await cached_namespace_set("tpl:list", "all", templates)
    return templates


async def get_template_async(template_id: str) -> dict | None:
    """async 版本：先读 Redis tpl:meta 命名空间。"""
    from app.common.cache import cached_namespace_get, cached_namespace_set

    cached = await cached_namespace_get("tpl:meta", template_id)
    if cached is not None:
        return cached
    template = get_template(template_id)
    if template is not None:
        await cached_namespace_set("tpl:meta", template_id, template)
    return template


def reset_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def get_template(template_id: str) -> dict | None:
    return next((t for t in list_templates() if t["id"] == template_id), None)
