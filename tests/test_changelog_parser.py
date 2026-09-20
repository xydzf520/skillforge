from pathlib import Path

from app.changelog import parse_changelog


def test_parse_public_changelog_keeps_preparation_entries_and_sections():
    content = (Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text(encoding="utf-8")

    versions = parse_changelog(content, limit=300)

    assert len(versions) == content.count("\n## [public-")
    assert len(versions) > 0
    assert all(entry["version"] and entry["date"] for entry in versions)
    assert all(any(entry.get(key) for key in ("highlights", "features", "improvements", "fixes")) for entry in versions)
    assert all(entry.get("sections") for entry in versions)


def test_parse_changelog_preserves_historical_headings_with_categories():
    content = """# Changelog

## [1.0.0] - 2026-01-01 — 历史版本

### 体验变更

- **新版入口**：用户能直接看到。

### 内部变更（面向开发者）

- **解析器**：保留原始章节名。

### 回归

- **修复**：兼容旧格式。
"""

    [entry] = parse_changelog(content)

    assert entry["highlights"] == ["历史版本"]
    assert entry["features"] == ["新版入口：用户能直接看到。"]
    assert entry["improvements"] == ["解析器：保留原始章节名。"]
    assert entry["fixes"] == ["修复：兼容旧格式。"]
    assert entry["sections"] == [
        {"title": "体验变更", "category": "features", "items": ["新版入口：用户能直接看到。"]},
        {"title": "内部变更（面向开发者）", "category": "improvements", "items": ["解析器：保留原始章节名。"]},
        {"title": "回归", "category": "fixes", "items": ["修复：兼容旧格式。"]},
    ]


def test_spotlight_block_allows_braces_in_json_strings():
    content = """# Changelog

## [2.0.0] - 2026-01-02 — 富内容

<!-- changelog:spotlight
{"schema":1,"title":"支持 } 字符","summary":"JSON 字符串里可以包含 }。"}
-->

### 用户可感能力

- **图文导览**：只在有 spotlight 时显示。
"""

    [entry] = parse_changelog(content)

    assert entry["spotlight"]["title"] == "支持 } 字符"
    assert entry["features"] == ["图文导览：只在有 spotlight 时显示。"]


def test_invalid_spotlight_degrades_to_legacy_markdown():
    content = """# Changelog

## [2.0.1] - 2026-01-03 — 降级

<!-- changelog:spotlight
{"title":
-->

### 修复

- 坏 spotlight 不影响普通日志。
"""

    [entry] = parse_changelog(content)

    assert "spotlight" not in entry
    assert entry["fixes"] == ["坏 spotlight 不影响普通日志。"]
