"""Selectable report design templates sourced from open-design.

The catalog is a compact, attributed derivative of nexu-io/open-design
DESIGN.md/SKILL.md files. It is used as report-generation guidance and report
rendering metadata; it does not publish upstream Skills into SkillForge.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt

CATALOG_PATH = Path(__file__).with_name("report_design_catalog.json")
REPORT_DESIGN_META_KEY = "_report_design"


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 1000) -> str:
    text = str(value or "").strip()
    return text[:limit]


@lru_cache(maxsize=1)
def load_report_design_catalog() -> dict[str, Any]:
    if not CATALOG_PATH.exists():
        return {
            "catalog": "open-design-report-templates",
            "version": 1,
            "source": {"repo": "https://github.com/nexu-io/open-design", "license": "Apache-2.0"},
            "summary": {"design_system_count": 0, "skill_count": 0, "template_count": 0},
            "templates": [],
        }
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    templates = [item for item in _safe_list(data.get("templates")) if isinstance(item, dict) and item.get("id")]
    return {**data, "templates": templates}


@lru_cache(maxsize=1)
def _template_map() -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in load_report_design_catalog().get("templates", [])}


def _public_template(item: dict[str, Any], *, include_standard: bool = False) -> dict[str, Any]:
    standard = _safe_dict(item.get("design_standard"))
    result = {
        "id": item.get("id"),
        "source_type": item.get("source_type"),
        "name": item.get("name"),
        "slug": item.get("slug"),
        "category": item.get("category"),
        "summary": item.get("summary"),
        "tags": _safe_list(item.get("tags")),
        "license": item.get("license") or "Apache-2.0",
        "upstream": item.get("upstream"),
        "upstream_path": item.get("upstream_path"),
        "content_hash": item.get("content_hash"),
        "preview": {
            "colors": _safe_list(standard.get("colors"))[:6],
            "prompt": _text(standard.get("prompt"), limit=320),
            "layout": _safe_list(standard.get("report_layout"))[:4],
        },
    }
    if include_standard:
        result["design_standard"] = standard
    return result


def get_report_design_template(template_id: str, *, include_standard: bool = True) -> dict[str, Any]:
    template = _template_map().get(str(template_id or "").strip())
    if not template:
        raise AppError("REPORT_DESIGN_TEMPLATE_NOT_FOUND", 404, {"template_id": template_id})
    return _public_template(template, include_standard=include_standard)


def list_report_design_templates(
    *,
    q: str | None = None,
    source_type: str | None = None,
    category: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    catalog = load_report_design_catalog()
    rows = [_public_template(item, include_standard=False) for item in catalog.get("templates", [])]
    q_norm = str(q or "").strip().lower()
    source_norm = str(source_type or "").strip().lower()
    category_norm = str(category or "").strip().lower()
    if source_norm:
        rows = [row for row in rows if str(row.get("source_type") or "").lower() == source_norm]
    if category_norm:
        rows = [row for row in rows if category_norm in str(row.get("category") or "").lower()]
    if q_norm:
        rows = [
            row for row in rows
            if q_norm in " ".join(
                [
                    str(row.get("id") or ""),
                    str(row.get("name") or ""),
                    str(row.get("category") or ""),
                    str(row.get("summary") or ""),
                    " ".join(str(tag) for tag in _safe_list(row.get("tags"))),
                ]
            ).lower()
        ]
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 200))
    offset = (page - 1) * page_size
    return {
        "ok": True,
        "catalog": catalog.get("catalog"),
        "version": catalog.get("version"),
        "source": catalog.get("source") or {},
        "summary": catalog.get("summary") or {},
        "total": len(rows),
        "page": page,
        "page_size": page_size,
        "items": rows[offset : offset + page_size],
    }


def selected_report_design_template_id(*values: Any) -> str | None:
    """Find the first template id from request/report/payload shapes."""

    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
            continue
        record = _safe_dict(value)
        if not record:
            continue
        direct = (
            record.get("report_design_template_id")
            or record.get("report_template_id")
            or record.get("design_template_id")
        )
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        payload = _safe_dict(record.get("payload"))
        meta = _safe_dict(payload.get(REPORT_DESIGN_META_KEY) or record.get(REPORT_DESIGN_META_KEY))
        nested = meta.get("template_id") or meta.get("id")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
        for report in _safe_list(record.get("reports")):
            nested_report = selected_report_design_template_id(report)
            if nested_report:
                return nested_report
    return None


def report_design_generation_context(template_id: str | None) -> dict[str, Any] | None:
    if not template_id:
        return None
    template = _template_map().get(str(template_id).strip())
    if not template:
        return None
    standard = _safe_dict(template.get("design_standard"))
    return {
        "template_id": template.get("id"),
        "name": template.get("name"),
        "source_type": template.get("source_type"),
        "category": template.get("category"),
        "license": template.get("license") or "Apache-2.0",
        "upstream": template.get("upstream"),
        "prompt": standard.get("prompt"),
        "colors": _safe_list(standard.get("colors"))[:6],
        "layout": _safe_list(standard.get("report_layout"))[:4],
    }


def decorate_report_with_design(
    report: dict[str, Any],
    *,
    template_id: str | None,
    source: str = "user_selected",
    include_applied_at: bool = False,
) -> dict[str, Any]:
    if not isinstance(report, dict) or not template_id:
        return report
    template = _template_map().get(str(template_id).strip())
    if not template:
        return report
    standard = _safe_dict(template.get("design_standard"))
    payload = dict(_safe_dict(report.get("payload")))
    design_meta = {
        "template_id": template.get("id"),
        "name": template.get("name"),
        "source_type": template.get("source_type"),
        "category": template.get("category"),
        "source": source,
        "license": template.get("license") or "Apache-2.0",
        "upstream": template.get("upstream"),
        "content_hash": template.get("content_hash"),
        "design_standard": {
            "prompt": standard.get("prompt"),
            "colors": _safe_list(standard.get("colors"))[:6],
            "layout": _safe_list(standard.get("report_layout"))[:4],
            "key_characteristics": _safe_list(standard.get("key_characteristics"))[:5],
            "workflow_rules": _safe_list(standard.get("workflow_rules"))[:5],
        },
    }
    if include_applied_at:
        design_meta["applied_at"] = isoformat_bjt(now_bjt())
    payload[REPORT_DESIGN_META_KEY] = design_meta
    updated = dict(report)
    updated["payload"] = payload
    tags = [str(tag) for tag in _safe_list(updated.get("tags")) if str(tag).strip()]
    for tag in ("设计模板", f"design:{template.get('slug') or template.get('id')}"):
        if tag not in tags:
            tags.append(tag)
    updated["tags"] = tags[:30]
    return updated


def decorate_reports_with_design(output: dict[str, Any], *, template_id: str | None, source: str = "user_selected") -> dict[str, Any]:
    if not template_id or not isinstance(output, dict):
        return output
    reports = _safe_list(output.get("reports"))
    if not reports:
        return output
    decorated = [
        decorate_report_with_design(report, template_id=selected_report_design_template_id(report) or template_id, source=source)
        if isinstance(report, dict) else report
        for report in reports
    ]
    updated = dict(output)
    updated["reports"] = decorated
    meta = dict(_safe_dict(updated.get("_skillforge_meta")))
    context = report_design_generation_context(template_id)
    if context:
        meta["report_design"] = {key: value for key, value in context.items() if key not in {"prompt"}}
        updated["_skillforge_meta"] = meta
    return updated
