#!/usr/bin/env python3
"""Audit Vue pages/components against SkillForge AI design tokens.

This is intentionally static and repeatable: it scans scoped/global style blocks
for token adoption, hard-coded colors, pixel density and shell-class adoption so
we can keep pages aligned with `web/src/styles/brand-theme.css`.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MD = ROOT / "docs" / "reviews" / f"{date.today().isoformat()}-design-consistency-audit.md"
DEFAULT_JSON = ROOT / "docs" / "reviews" / f"{date.today().isoformat()}-design-consistency-audit.json"

STYLE_RE = re.compile(r"<style\b[^>]*>(?P<body>.*?)</style>", re.S | re.I)
HEX_RE = re.compile(r"#[0-9A-Fa-f]{3,8}\b")
RGBA_RE = re.compile(r"\b(?:rgb|rgba|hsl|hsla)\s*\(", re.I)
PX_RE = re.compile(r"(?<![\w.-])-?\d+(?:\.\d+)?px\b")
INLINE_STYLE_RE = re.compile(r"(?<![:\w-])style\s*=\s*['\"]", re.I)
FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;]+);", re.I)
TOKEN_RE = re.compile(r"var\(--(?:ai|sf)-")
AI_TOKEN_RE = re.compile(r"var\(--ai-")
SF_TOKEN_RE = re.compile(r"var\(--sf-")
MEDIA_RE = re.compile(r"@media\b")
SHELL_RE = re.compile(r"\b(?:ai-main|ai-pagebody|page-container|page-wide|page-narrow|sf-page|admin-shell)\b")
ARCO_EMPTY_RE = re.compile(r"<a-empty\b", re.I)
OLD_SF_RE = re.compile(r"var\(--sf-")
HARD_GRADIENT_RE = re.compile(r"linear-gradient\([^;]*#[0-9A-Fa-f]{3,8}", re.I)


@dataclass
class Finding:
    file: str
    scope: str
    lines: int
    token_refs: int
    ai_token_refs: int
    sf_token_refs: int
    hard_hex: int
    rgba_refs: int
    px_refs: int
    inline_styles: int
    font_family_refs: int
    media_queries: int
    shell_adopted: bool
    arco_empty: int
    hard_gradients: int
    score: int
    severity: str
    reasons: list[str]


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _style_text(text: str) -> str:
    return "\n".join(match.group("body") for match in STYLE_RE.finditer(text))


def _scope(path: Path) -> str:
    try:
        parts = path.relative_to(ROOT / "web" / "src").parts
    except ValueError:
        return "unknown"
    if parts and parts[0] == "pages" and len(parts) > 1:
        return parts[1]
    if parts and parts[0] == "components" and len(parts) > 1:
        return f"components/{parts[1]}"
    return parts[0] if parts else "unknown"


def score_file(path: Path) -> Finding:
    text = path.read_text(encoding="utf-8", errors="ignore")
    style = _style_text(text)
    token_refs = len(TOKEN_RE.findall(style))
    hard_hex = len(HEX_RE.findall(style))
    rgba_refs = len(RGBA_RE.findall(style))
    px_refs = len(PX_RE.findall(style))
    inline_styles = len(INLINE_STYLE_RE.findall(text))
    font_family_refs = sum(1 for value in FONT_FAMILY_RE.findall(style) if "var(--ai-font" not in value and "var(--sf-font" not in value)
    media_queries = len(MEDIA_RE.findall(style))
    shell_adopted = bool(SHELL_RE.search(text))
    is_page = _rel(path).startswith("web/src/pages/")
    arco_empty = len(ARCO_EMPTY_RE.findall(text))
    hard_gradients = len(HARD_GRADIENT_RE.findall(style))
    ai_token_refs = len(AI_TOKEN_RE.findall(style))
    sf_token_refs = len(SF_TOKEN_RE.findall(style))

    score = 100
    reasons: list[str] = []
    if is_page and not shell_adopted:
        score -= 18
        reasons.append("缺少 ai-main/page-container 等统一页面壳")
    if token_refs == 0 and style.strip():
        score -= 18
        reasons.append("样式块未使用设计 token")
    if hard_hex:
        penalty = min(24, hard_hex * 2)
        score -= penalty
        reasons.append(f"硬编码 hex {hard_hex} 处")
    if rgba_refs > max(token_refs, 4):
        score -= min(16, rgba_refs)
        reasons.append(f"rgba/hsl 字面量偏多 {rgba_refs} 处")
    if px_refs > 80:
        score -= 14
        reasons.append(f"px 尺寸密度过高 {px_refs} 处")
    elif px_refs > 40:
        score -= 8
        reasons.append(f"px 尺寸较多 {px_refs} 处")
    if inline_styles:
        score -= min(10, inline_styles * 2)
        reasons.append(f"内联 style {inline_styles} 处")
    if font_family_refs:
        score -= min(8, font_family_refs * 2)
        reasons.append(f"本地 font-family {font_family_refs} 处")
    if media_queries == 0 and is_page and len(text.splitlines()) > 180:
        score -= 8
        reasons.append("长页面缺少响应式 @media")
    if arco_empty:
        score -= min(8, arco_empty * 2)
        reasons.append(f"直接使用 a-empty {arco_empty} 处")
    if hard_gradients:
        score -= min(8, hard_gradients * 3)
        reasons.append(f"硬编码渐变 {hard_gradients} 处")
    score = max(0, min(100, score))
    severity = "pass" if score >= 80 else "warn" if score >= 60 else "fail"
    return Finding(
        file=_rel(path),
        scope=_scope(path),
        lines=len(text.splitlines()),
        token_refs=token_refs,
        ai_token_refs=ai_token_refs,
        sf_token_refs=sf_token_refs,
        hard_hex=hard_hex,
        rgba_refs=rgba_refs,
        px_refs=px_refs,
        inline_styles=inline_styles,
        font_family_refs=font_family_refs,
        media_queries=media_queries,
        shell_adopted=shell_adopted,
        arco_empty=arco_empty,
        hard_gradients=hard_gradients,
        score=score,
        severity=severity,
        reasons=reasons[:8],
    )


def iter_vue_files(include_components: bool) -> Iterable[Path]:
    roots = [ROOT / "web" / "src" / "pages"]
    if include_components:
        roots.append(ROOT / "web" / "src" / "components")
    for root in roots:
        yield from sorted(root.rglob("*.vue"))


def build_audit(include_components: bool) -> dict:
    findings = [score_file(path) for path in iter_vue_files(include_components)]
    pages = [item for item in findings if item.file.startswith("web/src/pages/")]
    admin_pages = [item for item in pages if item.file.startswith("web/src/pages/admin/")]
    by_scope: dict[str, list[Finding]] = {}
    for item in findings:
        by_scope.setdefault(item.scope, []).append(item)
    scope_summary = []
    for scope, rows in sorted(by_scope.items()):
        scope_summary.append({
            "scope": scope,
            "files": len(rows),
            "avg_score": round(sum(row.score for row in rows) / len(rows), 1),
            "fail": sum(row.severity == "fail" for row in rows),
            "warn": sum(row.severity == "warn" for row in rows),
            "pass": sum(row.severity == "pass" for row in rows),
        })
    summary = {
        "page_count": len(pages),
        "component_count": len(findings) - len(pages),
        "file_count": len(findings),
        "avg_score": round(sum(row.score for row in findings) / max(len(findings), 1), 1),
        "page_avg_score": round(sum(row.score for row in pages) / max(len(pages), 1), 1),
        "fail_count": sum(row.severity == "fail" for row in findings),
        "warn_count": sum(row.severity == "warn" for row in findings),
        "pass_count": sum(row.severity == "pass" for row in findings),
        "hard_hex_files": sum(row.hard_hex > 0 for row in findings),
        "missing_shell_pages": sum((not row.shell_adopted) for row in pages),
        "direct_a_empty_files": sum(row.arco_empty > 0 for row in findings),
        "generated_at": date.today().isoformat(),
    }
    return {
        "summary": summary,
        "scopes": sorted(scope_summary, key=lambda row: (row["avg_score"], row["scope"])),
        "top_offenders": [asdict(row) for row in sorted(findings, key=lambda row: (row.score, -row.lines))[:40]],
        "hall_gpt_imagegen": [asdict(row) for row in findings if "HallGptImageGen" in row.file or "DirectCapabilityProjectSurface" in row.file],
        "admin_pages": [asdict(row) for row in sorted(admin_pages, key=lambda row: (row.score, row.file))],
        "findings": [asdict(row) for row in findings],
    }


def write_markdown(audit: dict, output: Path) -> None:
    s = audit["summary"]
    lines = [
        "# SkillForge 前端设计规范巡查报告（自动扫描）",
        "",
        f"- 巡查日期：{s['generated_at']}",
        "- 范围：`web/src/pages/**/*.vue` + `web/src/components/**/*.vue`",
        "- 标准：优先使用 `web/src/styles/ai-tokens.css` 的 `--ai-*` token、统一页面壳 `ai-main/page-container/ai-pagebody`、统一状态组件，减少硬编码颜色/尺寸。",
        "- 当前品牌与主题值统一由 `web/src/styles/brand-theme.css` 定义。",
        "- 本报告是源码启发式扫描，不是视觉验收。图表配色、边框遮罩、代码高亮和合理的固定尺寸可能触发提示；不能单凭分数判定界面有问题。",
        "",
        "## 总览",
        "",
        f"- 页面：{s['page_count']} 个；组件：{s['component_count']} 个；总文件：{s['file_count']} 个",
        f"- 平均分：{s['avg_score']}；页面平均分：{s['page_avg_score']}",
        f"- Fail：{s['fail_count']}；Warn：{s['warn_count']}；Pass：{s['pass_count']}",
        f"- 含硬编码 hex 的文件：{s['hard_hex_files']}",
        f"- 缺统一页面壳的页面：{s['missing_shell_pages']}",
        f"- 直接使用 `<a-empty>` 的文件：{s['direct_a_empty_files']}",
        "",
        "## gpt-imagegen 专项",
        "",
        "| 文件 | 分数 | 级别 | 问题 |",
        "|---|---:|---|---|",
    ]
    for row in audit["hall_gpt_imagegen"]:
        lines.append(f"| `{row['file']}` | {row['score']} | {row['severity']} | {'；'.join(row['reasons']) or '-'} |")
    admin_pages = audit.get("admin_pages", [])
    if admin_pages:
        admin_avg = round(sum(row["score"] for row in admin_pages) / len(admin_pages), 1)
        admin_fail = sum(row["severity"] == "fail" for row in admin_pages)
        admin_warn = sum(row["severity"] == "warn" for row in admin_pages)
        admin_pass = sum(row["severity"] == "pass" for row in admin_pages)
        lines += [
            "",
            "## Admin 专项",
            "",
            f"- 范围：`web/src/pages/admin/*.vue` 共 {len(admin_pages)} 个页面",
            f"- 平均分：{admin_avg}；Fail：{admin_fail}；Warn：{admin_warn}；Pass：{admin_pass}",
            "- 本轮整改目标：Admin 页面不再直接使用硬编码 hex/rgba/硬编码渐变、内联 `style=` 和页面级 `<a-empty>`；统一空状态与响应式兜底。",
            "",
            "| 文件 | 分数 | 级别 | 问题 |",
            "|---|---:|---|---|",
        ]
        for row in admin_pages:
            lines.append(f"| `{row['file']}` | {row['score']} | {row['severity']} | {'；'.join(row['reasons']) or '-'} |")
    lines += [
        "",
        "## 模块均分最低 Top 20",
        "",
        "| 模块 | 文件数 | 均分 | Fail | Warn | Pass |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in audit["scopes"][:20]:
        lines.append(f"| {row['scope']} | {row['files']} | {row['avg_score']} | {row['fail']} | {row['warn']} | {row['pass']} |")
    lines += [
        "",
        "## 单文件偏差 Top 40",
        "",
        "| 文件 | 分数 | 行数 | token | hex | rgba/hsl | px | 原因 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in audit["top_offenders"]:
        lines.append(
            f"| `{row['file']}` | {row['score']} | {row['lines']} | {row['token_refs']} | {row['hard_hex']} | {row['rgba_refs']} | {row['px_refs']} | {'；'.join(row['reasons']) or '-'} |"
        )
    lines += [
        "",
        "## 结论与后续门禁",
        "",
        f"1. 本次扫描有 {s['hard_hex_files']} 个文件包含硬编码 hex，需结合用途逐项审阅。",
        f"2. 有 {s['missing_shell_pages']} 个页面未匹配既定页面壳 class；独立登录页或专用画布需单独判断。",
        "3. 表中的静态分数仅用于安排检查顺序，不能代替实际页面、主题、响应式与键盘操作检查。",
        "4. 修改后记录类型检查、构建和相关回归；不要以自动生成的固定结论宣称整个模块已验收。",
        "",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages-only", action="store_false", dest="include_components", default=True)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MD)
    args = parser.parse_args()
    audit = build_audit(args.include_components)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(audit, args.markdown)
    print(json.dumps(audit["summary"], ensure_ascii=False))
    print(args.markdown)
    print(args.json)


if __name__ == "__main__":
    main()
