#!/usr/bin/env python3
"""Build a compact report-design catalog from nexu-io/open-design.

The catalog intentionally stores short, attributed design standards instead of
vendoring full DESIGN.md/SKILL.md files. SkillForge uses it as selectable report
rendering/generation guidance while keeping source attribution and license data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "app" / "inbox" / "report_design_catalog.json"
GITHUB_BASE = "https://github.com/nexu-io/open-design/blob/main"


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip().lower()).strip("-")
    return text or "template"


def _clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[`*_#>\-]+", " ", line or "")).strip()


def _first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return _clean_line(line[2:]) or fallback
    return fallback


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    raw = text[4:end]
    body = text[end + 4 :]
    data: dict[str, Any] = {}
    current: str | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if re.match(r"^[A-Za-z0-9_-]+:\s*", line):
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip('"')
            if value in {"|", ">"}:
                data[key] = ""
                current = key
            else:
                data[key] = value
                current = None
            continue
        if current and line.startswith((" ", "\t")):
            data[current] = (str(data.get(current) or "") + "\n" + line.strip()).strip()
        elif line.strip().startswith("-"):
            data.setdefault("triggers", [])
            if isinstance(data["triggers"], list):
                data["triggers"].append(line.strip().lstrip("-").strip().strip('"'))
    return data, body


def _extract_section(text: str, title_prefix: str, *, max_chars: int = 900) -> str:
    lines = text.splitlines()
    start = -1
    for idx, line in enumerate(lines):
        if line.lower().startswith("##") and title_prefix.lower() in line.lower():
            start = idx + 1
            break
    if start < 0:
        return ""
    chunk: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        cleaned = _clean_line(line)
        if cleaned:
            chunk.append(cleaned)
        if len(" ".join(chunk)) >= max_chars:
            break
    return " ".join(chunk)[:max_chars].strip()


def _extract_quote_meta(text: str) -> tuple[str, str]:
    category = "General"
    tagline_parts: list[str] = []
    for line in text.splitlines()[:12]:
        if not line.startswith(">"):
            continue
        raw = line[1:].strip()
        if raw.lower().startswith("category:"):
            category = raw.split(":", 1)[1].strip() or category
        elif raw:
            tagline_parts.append(raw)
    return category, " ".join(tagline_parts)[:260]


def _extract_bullets_after(text: str, marker: str, *, limit: int = 6) -> list[str]:
    lines = text.splitlines()
    start = -1
    for idx, line in enumerate(lines):
        if marker.lower() in line.lower():
            start = idx + 1
            break
    if start < 0:
        return []
    items: list[str] = []
    for line in lines[start : start + 80]:
        if len(items) >= limit:
            break
        if line.startswith("##") and items:
            break
        if line.strip().startswith("-"):
            item = _clean_line(line)
            if item:
                items.append(item[:220])
    return items


def _extract_colors(text: str, *, limit: int = 8) -> list[str]:
    seen: list[str] = []
    for color in re.findall(r"#[0-9A-Fa-f]{6}\b", text):
        up = color.upper()
        if up not in seen:
            seen.append(up)
        if len(seen) >= limit:
            break
    return seen


def _compact_prompt(name: str, category: str, tagline: str, characteristics: list[str], colors: list[str], source_type: str) -> str:
    pieces = [
        f"按《{name}》设计标准生成/渲染报告。",
        f"适用场景：{category}。" if category else "",
        f"风格目标：{tagline}" if tagline else "",
    ]
    if characteristics:
        pieces.append("关键规则：" + "；".join(characteristics[:4]))
    if colors:
        pieces.append("优先色板：" + ", ".join(colors[:5]))
    if source_type == "skill":
        pieces.append("把该 Skill 的工作流约束转成报告结构：先结论、再证据、再行动。")
    else:
        pieces.append("报告首屏要保留管理者结论卡、指标卡、风险/待办区，正文和调试信息后置。")
    return " ".join(p for p in pieces if p)[:1200]


def parse_design(path: Path, root: Path) -> dict[str, Any]:
    rel = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8", errors="ignore")
    slug = path.parent.name
    name = _first_heading(text, slug).replace("Design System Inspired by", "").strip(" ()") or slug
    category, tagline = _extract_quote_meta(text)
    characteristics = _extract_bullets_after(text, "Key Characteristics", limit=7)
    colors = _extract_colors(text)
    visual = _extract_section(text, "Visual Theme", max_chars=420)
    typography = _extract_section(text, "Typography", max_chars=360)
    components = _extract_section(text, "Component", max_chars=420)
    template_id = f"od-design-{_slug(slug)}"
    return {
        "id": template_id,
        "source_type": "design_system",
        "name": name[:120],
        "slug": slug,
        "category": category[:120],
        "summary": tagline or (visual[:260] if visual else name),
        "tags": ["open-design", "design-system", _slug(category)],
        "design_standard": {
            "prompt": _compact_prompt(name, category, tagline, characteristics, colors, "design_system"),
            "visual_theme": visual,
            "key_characteristics": characteristics,
            "colors": colors,
            "typography": typography,
            "components": components,
            "report_layout": [
                "首屏先给一句话结论和 3-4 个关键指标",
                "第二屏给风险、证据和待办",
                "长 Markdown/原始数据默认折叠",
            ],
        },
        "license": "Apache-2.0",
        "upstream": f"{GITHUB_BASE}/{rel}",
        "upstream_path": rel,
        "content_hash": hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16],
    }


def parse_skill(path: Path, root: Path) -> dict[str, Any]:
    rel = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8", errors="ignore")
    fm, body = _frontmatter(text)
    slug = path.parent.name
    name = str(fm.get("name") or _first_heading(body, slug) or slug).strip()[:120]
    description = _clean_line(str(fm.get("description") or _extract_section(body, "What it does", max_chars=260) or ""))[:360]
    category = str(fm.get("category") or "Skill workflow").strip()[:120]
    triggers = [str(item).strip() for item in fm.get("triggers", []) if str(item).strip()] if isinstance(fm.get("triggers"), list) else []
    upstream = str(fm.get("upstream") or f"{GITHUB_BASE}/{rel}")
    characteristics = triggers[:5] or _extract_bullets_after(body, "How to use", limit=4)
    template_id = f"od-skill-{_slug(slug)}"
    return {
        "id": template_id,
        "source_type": "skill",
        "name": name,
        "slug": slug,
        "category": category or "Skill workflow",
        "summary": description or f"Open Design Skill workflow: {name}",
        "tags": ["open-design", "skill-workflow", _slug(category or "skill")],
        "design_standard": {
            "prompt": _compact_prompt(name, category or "Skill workflow", description, characteristics, [], "skill"),
            "workflow_rules": characteristics,
            "report_layout": [
                "用该 Skill 的方法论组织报告章节",
                "先输出可决策摘要，再列执行步骤和证据",
                "把未完成事项转成 todos[]",
            ],
        },
        "license": "Apache-2.0",
        "upstream": upstream,
        "upstream_path": rel,
        "content_hash": hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16],
    }


def build_catalog(open_design_root: Path) -> dict[str, Any]:
    design_dir = open_design_root / "design-systems"
    skill_dir = open_design_root / "skills"
    if not design_dir.exists() or not skill_dir.exists():
        raise SystemExit(f"open-design root missing design-systems/skills: {open_design_root}")
    templates = [parse_design(path, open_design_root) for path in sorted(design_dir.glob("*/DESIGN.md"))]
    templates += [parse_skill(path, open_design_root) for path in sorted(skill_dir.glob("*/SKILL.md"))]
    templates.sort(key=lambda item: (item["source_type"], item["category"], item["name"]))
    return {
        "catalog": "open-design-report-templates",
        "version": 1,
        "source": {
            "repo": "https://github.com/nexu-io/open-design",
            "commit": _git_commit(open_design_root),
            "license": "Apache-2.0",
            "license_path": "LICENSE",
        },
        "summary": {
            "design_system_count": sum(1 for item in templates if item["source_type"] == "design_system"),
            "skill_count": sum(1 for item in templates if item["source_type"] == "skill"),
            "template_count": len(templates),
        },
        "templates": templates,
    }


def _git_commit(root: Path) -> str | None:
    import subprocess

    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("open_design_root", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    catalog = build_catalog(args.open_design_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(catalog["summary"], ensure_ascii=False))
    print(args.output)


if __name__ == "__main__":
    main()
