"""CHANGELOG.md parser used by the changelog API.

The file has years of hand-written release notes, so this parser keeps the
legacy Markdown path tolerant and treats rich blocks as optional metadata.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger


SECTION_KEYS = ("highlights", "features", "improvements", "fixes")
_RICH_BLOCK_RE = re.compile(r"<!--\s*changelog:(?P<name>[\w-]+)\b(?P<body>.*?)-->", re.DOTALL)
_HEADER_RE = re.compile(r"^\[([^\]]+)\]\s*-\s*(\d{4}-\d{2}-\d{2})\s*(?:—\s*(.+))?")


def extract_changelog_block(raw_section: str, name: str) -> dict[str, Any] | None:
    """Extract an optional JSON metadata block from a changelog section."""

    for match in _RICH_BLOCK_RE.finditer(raw_section):
        if match.group("name") != name:
            continue
        raw_body = match.group("body").strip()
        if not raw_body:
            return None
        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            logger.warning("解析 changelog {} 失败: {}", name, exc)
            return None
        if not isinstance(data, dict):
            logger.warning("解析 changelog {} 失败: 顶层必须是 JSON object", name)
            return None
        return data
    return None


def strip_changelog_blocks(raw_section: str) -> str:
    """Remove optional metadata blocks before line-based Markdown parsing."""

    return _RICH_BLOCK_RE.sub("", raw_section)


def pick_changelog_category(raw_name: str) -> str:
    """Map historical free-form section names to stable UI categories."""

    name = raw_name.strip().lower()
    if any(token in name for token in (
        "修复", "fix", "bug", "安全", "hotfix", "回归", "验证", "兼容", "稳定",
    )):
        return "fixes"
    if any(token in name for token in (
        "新功能", "feature", "体验", "交付", "落地", "上线", "新增", "能力", "功能", "用户",
    )):
        return "features"
    if any(token in name for token in (
        "内部重构", "重构", "优化", "性能", "文档", "架构", "策略", "类型", "清理", "收尾",
        "内部", "技术内幕", "工程", "数据库", "迁移", "设计", "开发者", "测试",
    )):
        return "improvements"
    if re.fullmatch(r"p[0-3](?:\b|[^a-z0-9].*)?", name):
        return "features"
    return "improvements"


def normalize_changelog_item(raw_item: str) -> str:
    """Normalize one Markdown bullet for compact UI display."""

    text = raw_item.strip()
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    if len(text) > 120:
        colon_pos = text.find("：")
        if 0 < colon_pos < 60:
            text = text[:colon_pos + 1] + text[colon_pos + 1:120] + "..."
        else:
            text = text[:120] + "..."
    return text


def parse_changelog(content: str, limit: int = 100) -> list[dict[str, Any]]:
    """Parse CHANGELOG.md content into API entries.

    The legacy keys are kept for older clients. The additive ``sections`` key
    preserves original Markdown headings so newer UI can render old releases
    without forcing every historical heading into four generic labels.
    """

    versions: list[dict[str, Any]] = []

    sections = re.split(r"^## ", content, flags=re.MULTILINE)
    for raw_section in sections[1:]:
        lines = raw_section.strip().split("\n")
        if not lines:
            continue

        header = lines[0]
        match = _HEADER_RE.match(header)
        if not match:
            continue

        version, date, title = match.group(1), match.group(2), (match.group(3) or "").strip()
        entry: dict[str, Any] = {
            "version": version,
            "date": date,
            "highlights": [title] if title else [],
            "features": [],
            "improvements": [],
            "fixes": [],
            "sections": [],
        }

        spotlight = extract_changelog_block(raw_section, "spotlight")
        if spotlight:
            entry["spotlight"] = spotlight

        markdown_section = strip_changelog_blocks(raw_section)
        markdown_lines = markdown_section.strip().split("\n")[1:]
        current_cat = "improvements"
        current_section: dict[str, Any] | None = None

        for line in markdown_lines:
            line = line.strip()
            if line.startswith("### "):
                title_text = line[4:].strip()
                current_cat = pick_changelog_category(title_text)
                current_section = {
                    "title": title_text,
                    "category": current_cat,
                    "items": [],
                }
                entry["sections"].append(current_section)
                continue
            if line.startswith("- "):
                text = normalize_changelog_item(line[2:])
                entry[current_cat].append(text)
                if current_section is None:
                    current_section = {
                        "title": "变更",
                        "category": current_cat,
                        "items": [],
                    }
                    entry["sections"].append(current_section)
                current_section["items"].append(text)
                continue
            if line == "---":
                break

        entry["sections"] = [section for section in entry["sections"] if section["items"]]
        for key in SECTION_KEYS:
            if not entry[key]:
                del entry[key]
        if not entry["sections"]:
            del entry["sections"]

        versions.append(entry)
        if len(versions) >= limit:
            break

    return versions
