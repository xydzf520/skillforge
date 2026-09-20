"""
钉钉回调接收路由：互动卡片点击、审批结果回调。
必须验证签名，防止伪造请求。
支持AES-256-CBC加密模式（企业内部应用回调标准）。
"""

import base64
import hashlib
import hmac
import json
import os
import struct
from html import escape
from urllib.parse import parse_qs, quote

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.audit import audit
from app.common.exceptions import AppError
from app.config import settings
from app.database import get_db
from app.dingtalk.open_links import is_dingtalk_request, verify_view_token
from app.todos.models import AITodo, DecisionRequest, TodoDispatchTask
from app.todos.service import todo_service
from app.common.time_utils import BJT, now_bjt
from app.dingtalk.card_templates import build_dispatch_execution_content

router = APIRouter()

SAMPLEBRAND_LOW_CONSUMPTION_SKILL_ID = "samplebrand-video-low-consumption-operator-v1"

SAMPLEBRAND_OPEN_TODO_STYLE = """
    .samplebrand-open-page h3 { font-size:13px; margin:14px 0 6px; color:#172033; }
    .samplebrand-open-page .pill.warn { background:#fff6e6; color:#9a5b00; }
    .samplebrand-open-page .pill.ok { background:#eaf8ef; color:#1f7a3c; }
    .samplebrand-open-page .hero { border-bottom:1px solid #edf0f5; padding-bottom:12px; margin-bottom:12px; }
    .samplebrand-open-page .summary { background:#f8faff; border:1px solid #e7edff; border-radius:8px; padding:10px 12px; line-height:1.65; font-size:14px; }
    .samplebrand-open-page .kv-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin:10px 0; }
    .samplebrand-open-page .kv { border:1px solid #edf0f5; border-radius:8px; padding:9px 10px; background:#fbfcff; min-width:0; }
    .samplebrand-open-page .kv span { display:block; color:#687386; font-size:12px; margin-bottom:4px; }
    .samplebrand-open-page .kv strong { display:block; font-size:15px; line-height:1.35; word-break:break-word; }
    .samplebrand-open-page .section { border-top:1px solid #edf0f5; margin-top:16px; padding-top:2px; }
    .samplebrand-open-page .subcard { border:1px solid #e7eaf0; border-radius:8px; padding:11px 12px; margin:10px 0; background:#fff; }
    .samplebrand-open-page .subcard-title { font-size:14px; font-weight:700; line-height:1.45; margin-bottom:6px; }
    .samplebrand-open-page .submeta { color:#687386; font-size:12px; line-height:1.55; margin-bottom:8px; word-break:break-word; }
    .samplebrand-open-page .evidence { background:#fafbfc; border-left:3px solid #9eb7ff; padding:8px 10px; color:#39445a; font-size:13px; line-height:1.6; margin:8px 0; white-space:pre-wrap; word-break:break-word; }
    .samplebrand-open-page .notice { background:#fff8e8; border:1px solid #f5d99a; border-radius:8px; padding:9px 10px; font-size:13px; line-height:1.6; color:#6b4a00; }
    .samplebrand-open-page .chart-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin:10px 0; }
    .samplebrand-open-page .chart-panel { border:1px solid #e7eaf0; border-radius:8px; padding:10px 11px; background:#fbfcff; min-width:0; }
    .samplebrand-open-page .chart-title { font-size:13px; font-weight:700; color:#172033; line-height:1.4; margin-bottom:4px; }
    .samplebrand-open-page .chart-desc { color:#687386; font-size:12px; line-height:1.45; margin-bottom:8px; }
    .samplebrand-open-page .chart-row { display:grid; grid-template-columns:minmax(62px,86px) minmax(80px,1fr) auto; gap:7px; align-items:center; margin:7px 0; }
    .samplebrand-open-page .chart-label { color:#39445a; font-size:12px; line-height:1.35; word-break:break-word; }
    .samplebrand-open-page .chart-track { height:8px; border-radius:99px; background:#edf1f7; overflow:hidden; }
    .samplebrand-open-page .chart-track i { display:block; height:100%; border-radius:99px; background:#2454c6; }
    .samplebrand-open-page .chart-track .current { background:#f97316; }
    .samplebrand-open-page .chart-value { color:#172033; font-size:12px; font-weight:700; white-space:nowrap; }
    .samplebrand-open-page ol { margin:8px 0; padding-left:20px; }
    .samplebrand-open-page .md { line-height:1.65; font-size:14px; word-break:break-word; }
    .samplebrand-open-page .md h3 { margin-top:14px; }
    .samplebrand-open-page .md p { margin:8px 0; }
    .samplebrand-open-page .more { color:#687386; font-size:12px; margin-top:8px; }
    @media (max-width: 520px) { .samplebrand-open-page .kv-grid, .samplebrand-open-page .chart-grid { grid-template-columns:1fr; } .samplebrand-open-page .chart-row { grid-template-columns:1fr; } }
"""


def _callback_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value).strip()
    return str(value).strip()


def _callback_rating(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        rating = int(value)
    except (TypeError, ValueError):
        return None
    if rating < 1 or rating > 5:
        return None
    return rating


def _extract_card_feedback(*sources: dict | None) -> dict:
    aliases = {
        "reason": ("reason", "decision_reason", "decisionReason"),
        "note": ("note", "remark", "comment"),
        "feedback": ("feedback", "user_feedback", "userFeedback"),
        "feedback_type": ("feedback_type", "feedbackType"),
        "reject_reason": ("reject_reason", "rejectReason"),
        "rating": ("rating", "score"),
    }
    result: dict[str, str | int] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        nested = source.get("value")
        if isinstance(nested, dict):
            nested_result = _extract_card_feedback(nested)
            result.update({k: v for k, v in nested_result.items() if v not in (None, "")})
        for target, keys in aliases.items():
            for key in keys:
                if key not in source:
                    continue
                if target == "rating":
                    rating = _callback_rating(source.get(key))
                    if rating is not None:
                        result[target] = rating
                    break
                text = _callback_text(source.get(key))
                if text:
                    result[target] = text
                    break
    return result


def _format_card_feedback_reason(feedback: dict) -> str:
    parts: list[str] = []
    labels = [
        ("reject_reason", "驳回原因"),
        ("feedback_type", "反馈类型"),
        ("rating", "评分"),
        ("reason", "原因"),
        ("feedback", "反馈"),
        ("note", "补充说明"),
    ]
    for key, label in labels:
        value = feedback.get(key)
        if value not in (None, ""):
            parts.append(f"{label}: {value}")
    return "\n".join(parts)[:2000]


def _card_feedback_payload(
    feedback: dict,
    *,
    action: str,
    actor_dingtalk_user_id: str | None,
    todo_id: int,
    request_id: str | None,
) -> dict:
    fields = {
        key: value
        for key, value in feedback.items()
        if key in {"reason", "note", "feedback", "feedback_type", "reject_reason", "rating"}
        and value not in (None, "", [], {})
    }
    return {
        "source": "dingtalk_interactive_card",
        "action": action,
        "todo_id": todo_id,
        "request_id": request_id,
        "dingtalk_user_id": actor_dingtalk_user_id,
        **fields,
    }


def _dispatch_ack_feedback_payload(
    feedback: dict,
    *,
    action: str,
    actor_dingtalk_user_id: str | None,
    dispatch_task_id: int,
    request_id: str | None,
) -> dict:
    fields = {
        key: value
        for key, value in feedback.items()
        if key in {"reason", "note", "feedback", "feedback_type", "rating"}
        and value not in (None, "", [], {})
    }
    return {
        "source": "dingtalk_interactive_card",
        "action": action or "dispatch_ack",
        "dispatch_task_id": dispatch_task_id,
        "request_id": request_id,
        "dingtalk_user_id": actor_dingtalk_user_id,
        **fields,
    }


def _login_redirect(path: str) -> RedirectResponse:
    return RedirectResponse(f"/login?redirect={quote(path, safe='')}", status_code=302)


def _html_page(title: str, body: str, *, status_code: int = 200, extra_style: str = "") -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>{escape(title)}</title>
  <style>
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:#f6f7f9; color:#172033; }}
    main {{ max-width:760px; margin:0 auto; padding:18px 14px 32px; }}
    .card {{ background:#fff; border:1px solid #e6e8ee; border-radius:8px; padding:16px; box-shadow:0 4px 18px rgba(23,32,51,.05); }}
    h1 {{ font-size:18px; line-height:1.35; margin:0 0 12px; }}
    h2 {{ font-size:14px; margin:18px 0 8px; color:#4b5565; }}
    .meta {{ display:flex; flex-wrap:wrap; gap:8px; margin:10px 0 14px; }}
    .pill {{ background:#eef3ff; color:#2454c6; border-radius:999px; padding:4px 9px; font-size:12px; }}
    .block {{ line-height:1.65; font-size:14px; white-space:pre-wrap; word-break:break-word; }}
    ul {{ margin:8px 0; padding-left:18px; }}
    li {{ margin:5px 0; }}
    .muted {{ color:#687386; font-size:12px; margin-top:16px; }}
    .action {{ display:inline-block; margin:14px 0 4px; padding:9px 14px; border-radius:6px; background:#2454c6; color:#fff; font-size:14px; }}
    button.action {{ border:0; cursor:pointer; font:inherit; }}
    textarea {{ width:100%; min-height:120px; box-sizing:border-box; resize:vertical; border:1px solid #d8dce5; border-radius:6px; padding:10px; font:14px/1.6 inherit; color:#172033; }}
    textarea:focus {{ outline:2px solid rgba(36,84,198,.18); border-color:#2454c6; }}
    .form-row {{ margin-top:12px; }}
    .form-help {{ color:#687386; font-size:12px; margin-top:6px; }}
    .form-error {{ color:#b42318; background:#fff1f0; border:1px solid #ffccc7; border-radius:6px; padding:8px 10px; font-size:13px; margin:10px 0; }}
    a {{ color:#2454c6; text-decoration:none; }}
    .action:visited {{ color:#fff; }}
    @media (max-width: 520px) {{ main {{ padding:12px 10px 24px; }} .card {{ padding:14px; }} }}
    {extra_style}
  </style>
</head>
<body><main><section class="card">{body}</section></main></body>
</html>""",
        status_code=status_code,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


def _format_text_html(text: str) -> str:
    import re

    raw = str(text or "").strip()
    if not raw:
        return "<div class=\"block\">无内容</div>"
    labels = (
        "Top300", "需要执行", "数据支撑",
        "全店主线", "决策", "关键证据", "运营动作", "禁止动作", "后续动作",
        "验证指标", "主因", "免费访客环比", "免费转化环比", "付费访客环比",
        "付费转化环比",
    )
    if "\n" in raw:
        parts = [line.strip() for line in raw.splitlines() if line.strip()]
    else:
        label_pattern = "|".join(re.escape(label) for label in labels)
        parts = [part.strip(" ；") for part in re.split(r"；(?=(?:" + label_pattern + r")(?:[:：\s]|$))", raw) if part.strip(" ；")]
    lis = "".join(f"<li>{escape(part)}</li>" for part in parts)
    return f"<ul>{lis}</ul>" if lis else f"<div class=\"block\">{escape(raw)}</div>"


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _text(value, *, limit: int = 1000) -> str:
    text = str(value or "").strip()
    if limit and len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def _first_text(obj: dict, *keys: str, limit: int = 1000) -> str:
    for key in keys:
        value = _text(obj.get(key), limit=limit)
        if value:
            return value
    return ""


def _list_html(items: list, *, ordered: bool = False, limit: int = 8) -> str:
    rows = []
    for item in items[:limit]:
        text = _text(item, limit=700)
        if text:
            rows.append(f"<li>{escape(text)}</li>")
    if not rows:
        return ""
    tag = "ol" if ordered else "ul"
    return f"<{tag}>" + "".join(rows) + f"</{tag}>"


def _markdown_like_html(markdown: str, *, max_chars: int = 9000) -> str:
    raw = _text(markdown, limit=max_chars)
    if not raw:
        return ""

    html: list[str] = []
    list_tag: str | None = None

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            html.append(f"</{list_tag}>")
            list_tag = None

    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.strip()
        if not stripped:
            close_list()
            continue
        if stripped.startswith("### "):
            close_list()
            html.append(f"<h3>{escape(stripped[4:].strip())}</h3>")
        elif stripped.startswith("## "):
            close_list()
            html.append(f"<h3>{escape(stripped[3:].strip())}</h3>")
        elif stripped.startswith("# "):
            close_list()
            html.append(f"<h3>{escape(stripped[2:].strip())}</h3>")
        elif stripped.startswith(("- ", "* ")):
            if list_tag != "ul":
                close_list()
                list_tag = "ul"
                html.append("<ul>")
            html.append(f"<li>{escape(stripped[2:].strip())}</li>")
        elif len(stripped) > 3 and stripped[0].isdigit() and ". " in stripped[:5]:
            if list_tag != "ol":
                close_list()
                list_tag = "ol"
                html.append("<ol>")
            html.append(f"<li>{escape(stripped.split('. ', 1)[1].strip())}</li>")
        else:
            close_list()
            html.append(f"<p>{escape(stripped)}</p>")
    close_list()
    more = "<div class=\"more\">内容较长，已展示钉钉轻量详情；完整调试 JSON 请登录 SkillForge 查看。</div>" if len(str(markdown or "")) > max_chars else ""
    return f"<div class=\"md\">{''.join(html)}{more}</div>"


def _video_summary_grid_html(payload: dict) -> str:
    fields = [
        ("账号", payload.get("account_name") or payload.get("person")),
        ("分析日期", payload.get("analysis_date") or payload.get("data_time")),
        ("主题", ", ".join(str(v) for v in _as_list(payload.get("topic_keys"))) or payload.get("topic_key")),
        ("数据质量", payload.get("data_quality")),
    ]
    rows = []
    for label, value in fields:
        text = _text(value, limit=120)
        if text:
            rows.append(f"<div class=\"kv\"><span>{escape(label)}</span><strong>{escape(text)}</strong></div>")
    primary = _as_dict(payload.get("primary_indicator"))
    if primary:
        label = _text(primary.get("label"), limit=40) or "核心指标"
        value = _text(primary.get("value") or primary.get("display_value"), limit=80)
        if value:
            rows.insert(0, f"<div class=\"kv\"><span>{escape(label)}</span><strong>{escape(value)}</strong></div>")
    return f"<div class=\"kv-grid\">{''.join(rows)}</div>" if rows else ""


def _metric_sections_html(payload: dict, *, limit_sections: int = 3, limit_metrics: int = 5) -> str:
    sections = []
    for section in _as_list(payload.get("metric_sections"))[:limit_sections]:
        if not isinstance(section, dict):
            continue
        title = _text(section.get("title"), limit=80) or "指标分组"
        body = _metric_list_html(_as_list(section.get("metrics")), limit=limit_metrics)
        sections.append(f"<div class=\"subcard\"><div class=\"subcard-title\">{escape(title)}</div>{body}</div>")
    return "".join(sections)


def _operation_actions_html(payload: dict, *, limit: int = 4) -> str:
    cards = []
    for index, raw in enumerate(_as_list(payload.get("operation_actions"))[:limit], 1):
        if not isinstance(raw, dict):
            continue
        title = (
            _first_text(raw, "scenario", limit=160)
            or _first_text(raw, "action", limit=160)
            or f"建议 {index}"
        )
        section = _first_text(raw, "section", limit=80)
        priority = _first_text(raw, "priority", limit=20)
        dimension = _first_text(raw, "dimension", limit=80)
        topic = _first_text(raw, "theme_name", "topic_key", limit=120)
        video_id = _first_text(raw, "video_id", limit=60)
        editor = _first_text(raw, "editor_code", limit=40)
        product = _first_text(raw, "product_code", limit=80)
        benchmark_label = _first_text(raw, "benchmark_label", "benchmark_level", limit=120)
        benchmark_editor = _first_text(raw, "benchmark_editor_code", limit=40)
        meta_parts = [
            part
            for part in (
                section,
                priority,
                dimension,
                f"主题 {topic}" if topic else "",
                f"剪辑人 {editor}" if editor else "",
                f"产品 {product}" if product else "",
                f"参考等级 {benchmark_label}" if benchmark_label else "",
                f"参考剪辑人 {benchmark_editor}" if benchmark_editor else "",
                f"video_id {video_id}" if video_id else "",
            )
            if part
        ]
        evidence = _first_text(raw, "evidence", "visual_evidence", limit=1200)
        benchmark_warning = _first_text(raw, "benchmark_warning", limit=500)
        if benchmark_warning:
            evidence = f"对标说明：{benchmark_warning}" + (f"\n{evidence}" if evidence else "")
        low_visual = _first_text(raw, "low_visual_evidence", limit=900)
        benchmark_visual = _first_text(raw, "benchmark_visual_evidence", limit=900)
        roots = _as_list(raw.get("root_causes"))
        actions = _as_list(raw.get("actions")) or ([raw.get("action")] if raw.get("action") else [])
        meta_html = ""
        if meta_parts:
            meta_text = " ｜ ".join(str(part) for part in meta_parts)
            meta_html = f"<div class=\"submeta\">{escape(meta_text)}</div>"
        evidence_html = f"<div class=\"evidence\">{escape(evidence)}</div>" if evidence else ""
        roots_html = f"<h3>原因判断</h3>{_list_html(roots, limit=5)}" if roots else ""
        actions_html = f"<h3>优化动作</h3>{_list_html(actions, ordered=True, limit=6)}" if actions else ""
        low_visual_html = (
            f"<h3>低消耗画面证据</h3><div class=\"block\">{escape(low_visual)}</div>"
            if low_visual else ""
        )
        benchmark_visual_html = (
            f"<h3>参考视频</h3><div class=\"block\">{escape(benchmark_visual)}</div>"
            if benchmark_visual else ""
        )
        card = (
            f"<div class=\"subcard\">"
            f"<div class=\"subcard-title\">{escape(title)}</div>"
            f"{meta_html}"
            f"{evidence_html}"
            f"{roots_html}"
            f"{actions_html}"
            f"{low_visual_html}"
            f"{benchmark_visual_html}"
            f"</div>"
        )
        cards.append(card)
    return "".join(cards)


def _data_quality_html(payload: dict) -> str:
    items = []
    for raw in _as_list(payload.get("data_quality_items"))[:6]:
        if not isinstance(raw, dict):
            continue
        dimension = _first_text(raw, "dimension", limit=80) or "数据缺口"
        issue = _first_text(raw, "issue", "detail", limit=500)
        if issue:
            items.append(f"<li><b>{escape(dimension)}</b>：{escape(issue)}</li>")
    return "<ul>" + "".join(items) + "</ul>" if items else ""


def _number(value) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(str(value).replace(",", "").replace("¥", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _chart_value_text(value, unit: str = "") -> str:
    number = _number(value)
    if number.is_integer():
        text = f"{int(number):,}"
    else:
        text = f"{number:,.2f}".rstrip("0").rstrip(".")
    if unit == "¥":
        return f"¥{text}"
    return f"{text}{unit}" if unit else text


def _samplebrand_chart_sections_html(payload: dict, *, limit: int = 4) -> str:
    panels: list[str] = []
    for raw in _as_list(payload.get("chart_sections"))[:limit]:
        if not isinstance(raw, dict):
            continue
        title = _text(raw.get("title"), limit=80) or "数据图表"
        desc = _text(raw.get("description"), limit=180)
        chart_type = _text(raw.get("type"), limit=40)
        items = [item for item in _as_list(raw.get("items")) if isinstance(item, dict)][:6]
        if not items:
            continue
        rows: list[str] = []
        if chart_type == "bar_compare":
            max_value = max([1.0] + [_number(item.get("low")) for item in items] + [_number(item.get("benchmark")) for item in items])
            for item in items[:4]:
                label = _text(item.get("label") or item.get("key"), limit=40)
                unit = _text(item.get("unit"), limit=8)
                low = _number(item.get("low"))
                benchmark = _number(item.get("benchmark"))
                low_width = max(4, min(100, round(low / max_value * 100)))
                benchmark_width = max(4, min(100, round(benchmark / max_value * 100)))
                rows.append(
                    f"<div class=\"chart-row\"><div class=\"chart-label\">{escape(label)} 当前</div>"
                    f"<div class=\"chart-track\"><i class=\"current\" style=\"width:{low_width}%\"></i></div>"
                    f"<div class=\"chart-value\">{escape(_chart_value_text(low, unit))}</div></div>"
                    f"<div class=\"chart-row\"><div class=\"chart-label\">{escape(label)} 参考</div>"
                    f"<div class=\"chart-track\"><i style=\"width:{benchmark_width}%\"></i></div>"
                    f"<div class=\"chart-value\">{escape(_chart_value_text(benchmark, unit))}</div></div>"
                )
        else:
            max_value = max([1.0] + [_number(item.get("value")) for item in items])
            for item in items:
                label = _text(item.get("label") or item.get("key"), limit=46)
                unit = _text(item.get("unit"), limit=8)
                value = _number(item.get("value"))
                width = max(4, min(100, round(value / max_value * 100)))
                detail = _text(item.get("detail"), limit=60)
                value_text = _chart_value_text(value, unit)
                if detail:
                    value_text = f"{value_text}｜{detail}"
                rows.append(
                    f"<div class=\"chart-row\"><div class=\"chart-label\">{escape(label)}</div>"
                    f"<div class=\"chart-track\"><i style=\"width:{width}%\"></i></div>"
                    f"<div class=\"chart-value\">{escape(value_text)}</div></div>"
                )
        if rows:
            panels.append(
                f"<div class=\"chart-panel\"><div class=\"chart-title\">{escape(title)}</div>"
                f"{f'<div class=\"chart-desc\">{escape(desc)}</div>' if desc else ''}"
                f"{''.join(rows)}</div>"
            )
    return f"<div class=\"chart-grid\">{''.join(panels)}</div>" if panels else ""


def _video_todo_extra_html(payload: dict) -> str:
    if not (
        payload.get("card_type") == "video_low_consumption_decision_card"
        or payload.get("analysis_schema") == "samplebrand_video_low_consumption_daily_operator_v1"
    ):
        return ""
    parts = [
        _video_summary_grid_html(payload),
    ]
    detail_md = (
        _text(payload.get("detail_markdown"), limit=0)
        or _text(payload.get("account_detail_markdown"), limit=0)
        or _text(payload.get("content_markdown"), limit=0)
    )
    if detail_md:
        parts.append(f"<div class=\"section\"><h2>详细分析</h2>{_markdown_like_html(detail_md)}</div>")
    action_html = _operation_actions_html(payload)
    if action_html:
        parts.append(f"<div class=\"section\"><h2>逐视频建议</h2>{action_html}</div>")
    charts = _samplebrand_chart_sections_html(payload)
    if charts:
        parts.append(f"<div class=\"section\"><h2>数据图表</h2>{charts}</div>")
    metric_sections = _metric_sections_html(payload)
    if metric_sections:
        parts.append(f"<div class=\"section\"><h2>指标与参考建议</h2>{metric_sections}</div>")
    data_quality = _data_quality_html(payload)
    forbidden = _list_html(_as_list(payload.get("forbidden_actions")), limit=6)
    if data_quality or forbidden:
        data_quality_html = f"<h3>补查数据</h3>{data_quality}" if data_quality else ""
        forbidden_html = f"<h3>不建议事项</h3><div class=\"notice\">{forbidden}</div>" if forbidden else ""
        parts.append(
            f"<div class=\"section\"><h2>补查与边界</h2>"
            f"{data_quality_html}"
            f"{forbidden_html}"
            f"</div>"
        )
    return "".join(parts)


def _metric_list_html(metrics: list | None, *, limit: int = 8) -> str:
    rows = []
    for item in (metrics or [])[:limit]:
        if not isinstance(item, dict):
            text = escape(str(item))
            if text:
                rows.append(f"<li>{text}</li>")
            continue
        action = item.get("action")
        name = (
            item.get("name")
            or item.get("label")
            or item.get("dimension")
            or item.get("cause_role")
            or (f"动作 {item.get('rank')}" if action and item.get("rank") else "")
            or "指标"
        )
        value = item.get("value") or action or item.get("summary") or item.get("title") or ""
        status = item.get("status") or item.get("trend") or item.get("priority") or item.get("cause_role") or ""
        delta = item.get("delta") or item.get("note") or item.get("evidence") or item.get("basis") or ""
        if name == "指标" and not any((value, status, delta)):
            continue
        name = escape(str(name))
        value = escape(str(value))
        status = escape(str(status))
        delta = escape(str(delta))
        rows.append(f"<li><b>{name}</b>：{value}{'｜' + status if status else ''}{'<br>' + delta if delta else ''}</li>")
    return "<ul>" + "".join(rows) + "</ul>" if rows else "<div class=\"block\">无关键指标</div>"


def _source_links_html(links: list | None, *, limit: int = 6) -> str:
    rows = []
    for item in (links or [])[:limit]:
        if not isinstance(item, dict):
            continue
        label = escape(str(item.get("label") or item.get("key") or item.get("source") or "数据来源"))
        url = str(item.get("url") or item.get("page_url") or item.get("api_url") or "")
        if url.startswith(("http://", "https://")):
            rows.append(f"<li><a href=\"{escape(url)}\">{label}</a></li>")
        else:
            rows.append(f"<li>{label}</li>")
    return "<ul>" + "".join(rows) + "</ul>" if rows else "<div class=\"block\">无数据来源链接</div>"


def _first_operation_action(payload: dict) -> dict:
    for raw in _as_list(payload.get("operation_actions")):
        if isinstance(raw, dict):
            return raw
    return {}


def _samplebrand_main_action_text(task: TodoDispatchTask, operation: dict) -> str:
    actions = _as_list(operation.get("actions"))
    for value in (
        _first_text(operation, "human_action", "action", limit=700),
        _text(actions[0], limit=700) if actions else "",
    ):
        if value:
            return value

    content = _text(task.content, limit=0)
    if "需要执行：" in content:
        return _text(content.rsplit("需要执行：", 1)[-1], limit=700)
    return _text(content, limit=700)


def _samplebrand_operation_grid_html(payload: dict, operation: dict) -> str:
    topic = _first_text(operation, "theme_name", "topic_key", limit=120)
    if not topic:
        topic = ", ".join(str(v) for v in _as_list(payload.get("topic_keys")))
    benchmark = (
        _first_text(operation, "benchmark_video_title", "benchmark_title", "reference_video", limit=160)
        or _first_text(operation, "benchmark_video_id", "reference_video_id", limit=120)
    )
    fields = [
        ("账号", payload.get("account_name") or payload.get("person")),
        ("视频", _first_text(operation, "scenario", "video_name", "title", limit=180)),
        ("主题", topic),
        ("剪辑人", _first_text(operation, "editor_code", limit=40)),
        ("产品", _first_text(operation, "product_code", limit=80)),
        ("分型", _first_text(operation, "dimension", limit=80)),
        ("优先级", _first_text(operation, "priority", limit=20)),
        ("参考等级", _first_text(operation, "benchmark_label", "benchmark_level", limit=120)),
        ("参考样本", benchmark),
        ("参考剪辑人", _first_text(operation, "benchmark_editor_code", limit=40)),
    ]
    rows = []
    for label, value in fields:
        text = _text(value, limit=180)
        if text:
            rows.append(f"<div class=\"kv\"><span>{escape(label)}</span><strong>{escape(text)}</strong></div>")
    video_id = _first_text(operation, "video_id", limit=80)
    if video_id:
        rows.append(f"<div class=\"kv\"><span>video_id</span><strong>{escape(video_id)}</strong></div>")
    return f"<div class=\"kv-grid\">{''.join(rows)}</div>" if rows else ""


def _samplebrand_dispatch_focus_html(task: TodoDispatchTask, payload: dict, operation: dict) -> str:
    task_extra = _as_dict(task.extra)
    required_output = _first_text(task_extra, "required_output", "required_outputs", "deliverable", limit=600)
    actions = [
        item for item in _dedupe_texts(_as_list(operation.get("actions")), limit=5)
        if not item.startswith("复盘输出建议")
    ]
    action_list = _list_html(actions, ordered=True, limit=4)
    if not action_list:
        main_action = _samplebrand_main_action_text(task, operation)
        action_list = f"<ol><li>{escape(main_action)}</li></ol>" if main_action else ""

    output_html = ""
    if required_output:
        output_html = (
            f"<div class=\"subcard\">"
            f"<div class=\"subcard-title\">交付物</div>"
            f"<div class=\"block\">{escape(required_output)}</div>"
            f"</div>"
        )
    return (
        f"<div class=\"subcard\">"
        f"<div class=\"subcard-title\">先做这件事</div>"
        f"{action_list or '<div class=\"block\">请按任务内容完成处理。</div>'}"
        f"</div>"
        f"{output_html}"
    )


def _dedupe_texts(items: list, *, limit: int = 8, text_limit: int = 700) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _text(item, limit=text_limit)
        key = " ".join(text.split())
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append(text)
        if len(rows) >= limit:
            break
    return rows


def _samplebrand_metric_highlights_html(payload: dict, operation: dict) -> str:
    metric_texts: list[str] = []
    benchmark_warning = _first_text(operation, "benchmark_warning", limit=360)
    if benchmark_warning:
        metric_texts.append(f"对标说明：{benchmark_warning}")
    value_points = _as_dict(operation.get("qianchuan_lifecycle_value_points"))
    for label, key in (("管理者价值", "manager_value"), ("消费者触发点", "consumer_value")):
        text = _text(value_points.get(key), limit=360)
        if text:
            metric_texts.append(f"{label}：{text}")
    for item in _as_list(payload.get("key_metrics")):
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"), limit=60)
        if name in {"Agent 分型", "首条视频", "证据门槛"}:
            continue
        value = _text(item.get("value"), limit=180)
        status = _text(item.get("status"), limit=80)
        delta = _text(item.get("delta"), limit=220)
        if not name:
            continue
        line = f"{name}：{value}"
        if status:
            line += f"｜{status}"
        if delta:
            line += f"｜{delta}"
        metric_texts.append(line)

    for root in _as_list(operation.get("root_causes")):
        text = _text(root, limit=260)
        if not text or any(key in text for key in ("低消耗画面证据", "参考视频画面证据", "同主题好视频画面证据")):
            continue
        if text.startswith("分型："):
            text = text.split("。", 1)[1].strip() if "。" in text else ""
        if text:
            metric_texts.append(text)

    return _list_html(_dedupe_texts(metric_texts, limit=6, text_limit=320), limit=6)


def _samplebrand_visual_evidence_html(operation: dict) -> str:
    low_visual = _first_text(operation, "low_visual_evidence", limit=900)
    benchmark_visual = _first_text(operation, "benchmark_visual_evidence", limit=900)
    if not low_visual and not benchmark_visual:
        visual_evidence = _first_text(operation, "visual_evidence", limit=1200)
        low_marker = "低消耗画面证据："
        benchmark_markers = ("参考视频画面证据：", "同主题好视频画面证据：")
        if low_marker in visual_evidence:
            after_low = visual_evidence.split(low_marker, 1)[1]
            for benchmark_marker in benchmark_markers:
                if benchmark_marker in after_low:
                    low_visual = after_low.split(benchmark_marker, 1)[0].strip("；; \n")
                    benchmark_visual = after_low.split(benchmark_marker, 1)[1].strip("；; \n")
                    break
    parts = []
    if low_visual:
        parts.append(
            f"<div class=\"subcard\"><div class=\"subcard-title\">当前视频画面</div>"
            f"<div class=\"block\">{escape(low_visual)}</div></div>"
        )
    if benchmark_visual:
        parts.append(
            f"<div class=\"subcard\"><div class=\"subcard-title\">参考视频画面</div>"
            f"<div class=\"block\">{escape(benchmark_visual)}</div></div>"
        )
    return "".join(parts) or "<div class=\"block\">无视觉证据</div>"


def _samplebrand_lifecycle_value_html(operation: dict) -> str:
    value_points = _as_dict(operation.get("qianchuan_lifecycle_value_points"))
    items = [
        ("管理者价值", value_points.get("manager_value")),
        ("消费者触发点", value_points.get("consumer_value")),
        ("优化焦点", value_points.get("optimization_focus")),
    ]
    rows = []
    for label, value in items:
        text = _text(value, limit=500)
        if text:
            rows.append(f"<li><b>{escape(label)}</b>：{escape(text)}</li>")
    return "<ul>" + "".join(rows) + "</ul>" if rows else ""


def _samplebrand_dispatch_basis_html(payload: dict, operation: dict) -> str:
    highlights = _samplebrand_metric_highlights_html(payload, operation)
    lifecycle_value = _samplebrand_lifecycle_value_html(operation)
    parts = []
    if highlights:
        parts.append(highlights)
    if lifecycle_value:
        parts.append(f"<h3>高点击依据</h3>{lifecycle_value}")
    return "".join(parts) or "<div class=\"block\">无判断依据</div>"


def _samplebrand_dispatch_retest_html(task: TodoDispatchTask | None, payload: dict) -> str:
    task_extra = _as_dict(task.extra) if task is not None else {}
    retest = (
        _first_text(task_extra, "retest_metrics", "verification_metrics", limit=600)
        or _first_text(payload, "verification_metrics", "retest_metrics", limit=600)
        or "上线后用消耗、点击率、3秒播放率、转化率、ROI、成交金额复测。"
    )
    data_quality = _data_quality_html(payload)
    forbidden_items = [
        item for item in _as_list(payload.get("forbidden_actions"))
        if "视觉证据" not in _text(item, limit=200) and "完整视频" not in _text(item, limit=200)
    ]
    forbidden = _list_html(forbidden_items, limit=4)
    return (
        f"<div class=\"subcard\"><div class=\"subcard-title\">上线后看这些指标</div>"
        f"<div class=\"block\">{escape(retest)}</div></div>"
        f"{f'<h3>补查数据</h3>{data_quality}' if data_quality else ''}"
        f"{f'<h3>不建议事项</h3><div class=\"notice\">{forbidden}</div>' if forbidden else ''}"
    )


def _samplebrand_dispatch_detail_body(
    task: TodoDispatchTask,
    decision_request: DecisionRequest,
    executor: User,
    token: str,
) -> str:
    payload = _as_dict(decision_request.payload)
    operation = _first_operation_action(payload)
    deadline = task.deadline.strftime("%Y-%m-%d %H:%M") if task.deadline else "无"
    ack_url = escape(f"/api/dingtalk/open/dispatch/{task.id}/ack?token={quote(token, safe='')}", quote=True)
    action_html = "" if task.status == "done" else f"<a class=\"action\" href=\"{ack_url}\">标记完成</a>"
    priority = _first_text(operation, "priority", limit=20) or _text(payload.get("priority"), limit=20)
    dimension = _first_text(operation, "dimension", limit=80)
    scenario = _first_text(operation, "scenario", "video_name", "title", limit=160)
    charts = _samplebrand_chart_sections_html(payload, limit=3)
    summary_parts = [
        f"处理视频：{scenario}" if scenario else "",
        "按下方清单提交轻改方案。",
    ]
    consumer_summary = "；".join(part for part in summary_parts if part)
    return (
        f"<div class=\"samplebrand-open-page samplebrand-dispatch-page\">"
        f"<div class=\"hero\"><h1>{escape(decision_request.title)}</h1>"
        f"<div class=\"meta\"><span class=\"pill\">任务 #{task.id}</span>"
        f"<span class=\"pill\">状态 {escape(task.status or '')}</span>"
        f"<span class=\"pill\">截止 {escape(deadline)}</span>"
        f"<span class=\"pill\">接收人 {escape(executor.name)}</span>"
        f"{f'<span class=\"pill warn\">{escape(priority)}</span>' if priority else ''}</div>"
        f"<div class=\"summary\"><b>任务概览：</b>{escape(consumer_summary or decision_request.summary or '请查看详情并处理。')}</div>"
        f"{action_html}</div>"
        f"<h2>执行动作</h2>{_samplebrand_dispatch_focus_html(task, payload, operation)}"
        f"<h2>视频与对标</h2>{_samplebrand_operation_grid_html(payload, operation) or '<div class=\"block\">无视频对标信息</div>'}"
        f"{f'<h2>数据图表</h2>{charts}' if charts else ''}"
        f"<h2>判断依据</h2>{_samplebrand_dispatch_basis_html(payload, operation)}"
        f"<h2>视觉证据</h2>{_samplebrand_visual_evidence_html(operation)}"
        f"<h2>验证与边界</h2>{_samplebrand_dispatch_retest_html(task, payload)}"
        f"<h2>数据来源</h2>{_source_links_html(payload.get('data_source_links'))}"
        f"<div class=\"muted\">完成后可回到钉钉卡片点击“标记完成”。此页面仅面向钉钉工作通知里的绑定接收人展示；在浏览器等其他环境打开会要求登录。</div>"
        f"</div>"
    )


def _samplebrand_todo_dispatch_tasks_html(payload: dict) -> str:
    rows = []
    for raw in _as_list(payload.get("dispatch_tasks"))[:5]:
        if not isinstance(raw, dict):
            continue
        extra = _as_dict(raw.get("extra"))
        executor = _text(raw.get("executor"), limit=80)
        required_output = _first_text(extra, "required_output", "required_outputs", "deliverable", limit=500)
        account = _first_text(extra, "account_name", "person", limit=80)
        details = []
        if executor:
            details.append(f"执行人：{executor}")
        if account:
            details.append(f"账号：{account}")
        title = required_output or _text(raw.get("summary") or raw.get("title"), limit=500)
        if not title:
            content = _text(raw.get("content"), limit=0)
            title = "提交改版视频方向、复用的高质量样本、复测指标。" if "# " in content else _text(content, limit=500)
        meta_html = f"<div class=\"submeta\">{' ｜ '.join(escape(part) for part in details)}</div>" if details else ""
        rows.append(
            f"<div class=\"subcard\"><div class=\"subcard-title\">派发任务 {len(rows) + 1}</div>"
            f"{meta_html}<div class=\"block\">{escape(title or '请按建议派发处理。')}</div></div>"
        )
    return "".join(rows) or "<div class=\"block\">无派发任务</div>"


def _samplebrand_todo_decision_summary_html(payload: dict, decision_request: DecisionRequest) -> str:
    operation = _first_operation_action(payload)
    topic = (
        _first_text(operation, "theme_name", "topic_key", limit=120)
        or ", ".join(str(v) for v in _as_list(payload.get("topic_keys")))
    )
    primary_value = _text(_as_dict(payload.get("primary_indicator")).get("value"), limit=80) or "1 条"
    fields = [
        ("账号", payload.get("account_name") or payload.get("person")),
        ("分析日期", payload.get("analysis_date") or payload.get("data_time")),
        ("建议关注", primary_value),
        ("主题范围", topic),
        ("数据质量", payload.get("data_quality")),
    ]
    rows = []
    for label, value in fields:
        text = _text(value, limit=120)
        if text:
            rows.append(f"<div class=\"kv\"><span>{escape(label)}</span><strong>{escape(text)}</strong></div>")
    account = _text(payload.get("account_name") or payload.get("person"), limit=80)
    date = _text(payload.get("analysis_date") or payload.get("data_time"), limit=40)
    decision = f"{account}账号 {date} 有 {primary_value}低消耗视频建议关注；主题为 {topic}；可按下方建议派发轻改。" if account and topic else _text(decision_request.summary, limit=260)
    grid = f"<div class=\"kv-grid\">{''.join(rows)}</div>" if rows else ""
    decision_html = f"<div class=\"summary\">{escape(decision)}</div>" if decision else ""
    return grid + decision_html


def _samplebrand_todo_action_html(payload: dict) -> str:
    operation = _first_operation_action(payload)
    actions = [
        item for item in _dedupe_texts(_as_list(operation.get("actions")), limit=5)
        if not item.startswith("复盘输出建议")
    ]
    return _list_html(actions, ordered=True, limit=4) or "<div class=\"block\">无建议动作</div>"


def _samplebrand_todo_detail_body(
    todo: AITodo,
    decision_request: DecisionRequest,
    assignee: User,
) -> str:
    payload = _as_dict(decision_request.payload)
    operation = _first_operation_action(payload)
    payload_priority = _text(payload.get("priority"), limit=20)
    business_action_allowed = payload.get("business_action_allowed") is True
    charts = _samplebrand_chart_sections_html(payload, limit=4)
    return (
        f"<div class=\"samplebrand-open-page\">"
        f"<div class=\"hero\"><h1>{escape(decision_request.title)}</h1>"
        f"<div class=\"meta\"><span class=\"pill\">{escape(decision_request.kind)}</span>"
        f"<span class=\"pill\">状态 {escape(decision_request.aggregate_status or '')}</span>"
        f"<span class=\"pill\">待办 {todo.id}</span>"
        f"<span class=\"pill\">接收人 {escape(assignee.name)}</span>"
        f"{f'<span class=\"pill warn\">{escape(payload_priority)}</span>' if payload_priority else ''}"
        f"<span class=\"pill {'ok' if business_action_allowed else 'warn'}\">"
        f"{'可派发' if business_action_allowed else '建议复核'}</span></div></div>"
        f"<h2>处理结论</h2>{_samplebrand_todo_decision_summary_html(payload, decision_request)}"
        f"<h2>建议派发</h2>{_samplebrand_todo_dispatch_tasks_html(payload)}"
        f"<h2>视频与对标</h2>{_samplebrand_operation_grid_html(payload, operation) or '<div class=\"block\">无视频对标信息</div>'}"
        f"{f'<h2>数据图表</h2>{charts}' if charts else ''}"
        f"<h2>为什么这样处理</h2>{_samplebrand_dispatch_basis_html(payload, operation)}"
        f"<h2>建议动作</h2>{_samplebrand_todo_action_html(payload)}"
        f"<h2>视觉证据</h2>{_samplebrand_visual_evidence_html(operation)}"
        f"<h2>复测与风险边界</h2>{_samplebrand_dispatch_retest_html(None, payload)}"
        f"<h2>数据来源</h2>{_source_links_html(payload.get('data_source_links'))}"
        f"<div class=\"muted\">原始分析和完整 payload 保留在 SkillForge；此钉钉页只展示去重后的管理者决策信息。</div>"
        f"</div>"
    )


def _dispatch_ack_done_body(
    task: TodoDispatchTask,
    decision_request: DecisionRequest,
    executor: User,
) -> str:
    note_html = ""
    if task.ack_note:
        note_html = f"<h2>完成说明</h2><div class=\"block\">{escape(task.ack_note)}</div>"
    return (
        f"<h1>已标记完成</h1>"
        f"<div class=\"meta\"><span class=\"pill\">任务 #{task.id}</span>"
        f"<span class=\"pill\">接收人 {escape(executor.name)}</span></div>"
        f"<div class=\"block\">{escape(decision_request.title)}</div>"
        f"{note_html}"
        f"<div class=\"muted\">状态和完成说明已回写到 Skill Studio 待办中心。</div>"
    )


def _dispatch_ack_form_body(
    task: TodoDispatchTask,
    decision_request: DecisionRequest,
    executor: User,
    token: str,
    *,
    note: str = "",
    error: str = "",
) -> str:
    action_url = escape(f"/api/dingtalk/open/dispatch/{task.id}/ack?token={quote(token, safe='')}", quote=True)
    error_html = f"<div class=\"form-error\">{escape(error)}</div>" if error else ""
    return (
        f"<h1>标记完成</h1>"
        f"<div class=\"meta\"><span class=\"pill\">任务 #{task.id}</span>"
        f"<span class=\"pill\">接收人 {escape(executor.name)}</span></div>"
        f"<h2>需要执行</h2>{_format_text_html(build_dispatch_execution_content(decision_request, task) or task.content)}"
        f"{error_html}"
        f"<form method=\"post\" action=\"{action_url}\">"
        f"<div class=\"form-row\"><textarea name=\"note\" maxlength=\"1000\" required "
        f"placeholder=\"填写实际处理结果，最多 1000 字\">{escape(note[:1000])}</textarea></div>"
        f"<div class=\"form-help\">请填写处理结果、核对结论或仍需跟进的问题，最多 1000 字。</div>"
        f"<button class=\"action\" type=\"submit\">标记完成</button>"
        f"</form>"
        f"<div class=\"muted\">此页面仅面向钉钉工作通知里的绑定接收人展示；在浏览器等其他环境打开会要求登录。</div>"
    )


async def _read_ack_note(request: Request) -> str:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            data = {}
        return str(data.get("note") or "") if isinstance(data, dict) else ""
    body = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(body, keep_blank_values=True)
    return parsed.get("note", [""])[0]


async def _load_bound_todo(db: AsyncSession, todo_id: int) -> tuple[AITodo, DecisionRequest]:
    row = (await db.execute(
        select(AITodo, DecisionRequest)
        .join(DecisionRequest, DecisionRequest.id == AITodo.request_id)
        .where(AITodo.id == todo_id)
    )).first()
    if not row:
        raise AppError("TODO_NOT_FOUND", 404)
    return row[0], row[1]


async def _load_bound_dispatch(
    db: AsyncSession,
    task_id: int,
    token: str,
) -> tuple[TodoDispatchTask, DecisionRequest, User] | None:
    payload = verify_view_token(token)
    try:
        token_task_id = int(payload.get("task_id") or 0) if payload else 0
    except (TypeError, ValueError):
        token_task_id = 0
    if not payload or payload.get("kind") != "dispatch" or token_task_id != task_id:
        return None

    row = (await db.execute(
        select(TodoDispatchTask, DecisionRequest)
        .join(DecisionRequest, DecisionRequest.id == TodoDispatchTask.request_id)
        .where(TodoDispatchTask.id == task_id)
    )).first()
    if not row:
        raise AppError("TODO_NOT_FOUND", 404)

    task, decision_request = row[0], row[1]
    executor = await db.get(User, task.executor) if task.executor else None
    if (
        not executor
        or not executor.dingtalk_user_id
        or payload.get("user_id") != executor.id
        or payload.get("dingtalk_user_id") != executor.dingtalk_user_id
    ):
        return None
    return task, decision_request, executor


@router.get("/open/todos/{todo_id}")
async def open_todo_from_dingtalk(
    todo_id: int,
    request: Request,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    target_path = f"/inbox/todos/{todo_id}"
    if not is_dingtalk_request(request.headers.get("user-agent")):
        return _login_redirect(target_path)
    payload = verify_view_token(token)
    if not payload or payload.get("kind") != "todo" or int(payload.get("todo_id") or 0) != todo_id:
        return _html_page("链接不可用", "<h1>链接不可用</h1><div class=\"block\">请从钉钉工作通知重新打开。</div>", status_code=403)
    todo, decision_request = await _load_bound_todo(db, todo_id)
    assignee = await db.get(User, todo.assignee)
    if (
        not assignee
        or not assignee.dingtalk_user_id
        or payload.get("user_id") != assignee.id
        or payload.get("dingtalk_user_id") != assignee.dingtalk_user_id
    ):
        return _html_page("链接不可用", "<h1>链接不可用</h1><div class=\"block\">钉钉绑定身份与待办不一致。</div>", status_code=403)
    req_payload = decision_request.payload if isinstance(decision_request.payload, dict) else {}
    dispatch_tasks = req_payload.get("dispatch_tasks") if isinstance(req_payload.get("dispatch_tasks"), list) else []
    task_items = "".join(
        f"<li>{escape(str(item.get('content') or ''))}</li>"
        for item in dispatch_tasks[:8] if isinstance(item, dict)
    )
    if decision_request.skill_id != SAMPLEBRAND_LOW_CONSUMPTION_SKILL_ID:
        body = (
            f"<h1>{escape(decision_request.title)}</h1>"
            f"<div class=\"meta\"><span class=\"pill\">{escape(decision_request.kind)}</span>"
            f"<span class=\"pill\">状态 {escape(decision_request.aggregate_status or '')}</span>"
            f"<span class=\"pill\">接收人 {escape(assignee.name)}</span></div>"
            f"<h2>摘要</h2>{_format_text_html(decision_request.summary or '')}"
            f"<h2>关键指标</h2>{_metric_list_html(req_payload.get('key_metrics'))}"
            f"<h2>待派发任务</h2>{'<ul>' + task_items + '</ul>' if task_items else '<div class=\"block\">无派发任务</div>'}"
            f"<h2>数据来源</h2>{_source_links_html(req_payload.get('data_source_links'))}"
            f"<div class=\"muted\">此页面仅面向钉钉工作通知里的绑定接收人展示；在浏览器等其他环境打开会要求登录。</div>"
        )
        return _html_page(decision_request.title, body)

    return _html_page(
        decision_request.title,
        _samplebrand_todo_detail_body(todo, decision_request, assignee),
        extra_style=SAMPLEBRAND_OPEN_TODO_STYLE,
    )


@router.get("/open/dispatch/{task_id}")
async def open_dispatch_from_dingtalk(
    task_id: int,
    request: Request,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    target_path = f"/inbox/dispatch?focus={task_id}"
    if not is_dingtalk_request(request.headers.get("user-agent")):
        return _login_redirect(target_path)
    verified = await _load_bound_dispatch(db, task_id, token)
    if not verified:
        return _html_page("链接不可用", "<h1>链接不可用</h1><div class=\"block\">请从钉钉工作通知重新打开。</div>", status_code=403)
    task, decision_request, executor = verified
    req_payload = decision_request.payload if isinstance(decision_request.payload, dict) else {}
    if decision_request.skill_id == SAMPLEBRAND_LOW_CONSUMPTION_SKILL_ID:
        return _html_page(
            decision_request.title,
            _samplebrand_dispatch_detail_body(task, decision_request, executor, token),
            extra_style=SAMPLEBRAND_OPEN_TODO_STYLE,
        )

    deadline = task.deadline.strftime("%Y-%m-%d %H:%M") if task.deadline else "无"
    ack_url = f"/api/dingtalk/open/dispatch/{task.id}/ack?token={quote(token, safe='')}"
    action_html = "" if task.status == "done" else f"<a class=\"action\" href=\"{ack_url}\">标记完成</a>"
    body = (
        f"<h1>{escape(decision_request.title)}</h1>"
        f"<div class=\"meta\"><span class=\"pill\">任务 #{task.id}</span>"
        f"<span class=\"pill\">状态 {escape(task.status or '')}</span>"
        f"<span class=\"pill\">截止 {escape(deadline)}</span>"
        f"<span class=\"pill\">接收人 {escape(executor.name)}</span></div>"
        f"<h2>需要执行</h2>{_format_text_html(build_dispatch_execution_content(decision_request, task) or task.content)}"
        f"{action_html}"
        f"<h2>数据支撑</h2>{_metric_list_html(req_payload.get('key_metrics'))}"
        f"<h2>数据来源</h2>{_source_links_html(req_payload.get('data_source_links'))}"
        f"<div class=\"muted\">完成后可回到钉钉卡片点击“标记完成”。此页面仅面向钉钉工作通知里的绑定接收人展示；在浏览器等其他环境打开会要求登录。</div>"
    )
    return _html_page(decision_request.title, body)


@router.get("/open/dispatch/{task_id}/ack")
async def ack_dispatch_from_dingtalk(
    task_id: int,
    request: Request,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    target_path = f"/inbox/dispatch?focus={task_id}"
    if not is_dingtalk_request(request.headers.get("user-agent")):
        return _login_redirect(target_path)

    verified = await _load_bound_dispatch(db, task_id, token)
    if not verified:
        return _html_page("链接不可用", "<h1>链接不可用</h1><div class=\"block\">请从钉钉工作通知重新打开。</div>", status_code=403)
    task, decision_request, executor = verified

    if task.status == "done":
        return _html_page("已标记完成", _dispatch_ack_done_body(task, decision_request, executor))
    if task.status not in {"sent", "pushed_no_dingtalk", "in_progress", "blocked"}:
        return _html_page(
            "无法标记完成",
            (
                f"<h1>无法标记完成</h1>"
                f"<div class=\"block\">当前任务状态是 {escape(task.status or '')}，不能从钉钉直接标记完成。</div>"
            ),
            status_code=409,
        )
    return _html_page(
        "标记完成",
        _dispatch_ack_form_body(task, decision_request, executor, token),
    )


@router.post("/open/dispatch/{task_id}/ack")
async def submit_ack_dispatch_from_dingtalk(
    task_id: int,
    request: Request,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    target_path = f"/inbox/dispatch?focus={task_id}"
    if not is_dingtalk_request(request.headers.get("user-agent")):
        return _login_redirect(target_path)

    verified = await _load_bound_dispatch(db, task_id, token)
    if not verified:
        return _html_page("链接不可用", "<h1>链接不可用</h1><div class=\"block\">请从钉钉工作通知重新打开。</div>", status_code=403)
    task, decision_request, executor = verified

    if task.status == "done":
        return _html_page("已标记完成", _dispatch_ack_done_body(task, decision_request, executor))
    if task.status not in {"sent", "pushed_no_dingtalk", "in_progress", "blocked"}:
        return _html_page(
            "无法标记完成",
            (
                f"<h1>无法标记完成</h1>"
                f"<div class=\"block\">当前任务状态是 {escape(task.status or '')}，不能从钉钉直接标记完成。</div>"
            ),
            status_code=409,
        )

    note = (await _read_ack_note(request)).strip()
    if not note:
        return _html_page(
            "标记完成",
            _dispatch_ack_form_body(task, decision_request, executor, token, note=note, error="请填写完成说明。"),
            status_code=400,
        )
    if len(note) > 1000:
        return _html_page(
            "标记完成",
            _dispatch_ack_form_body(task, decision_request, executor, token, note=note, error="完成说明最多 1000 字。"),
            status_code=400,
        )

    try:
        await todo_service.ack_dispatch_task(
            db,
            task_id=task.id,
            actor_id=executor.id,
            channel="dingtalk_open",
            note=note,
        )
        await audit.log(
            executor.id,
            "dispatch.ack",
            "dispatch_task",
            str(task.id),
            detail={"channel": "dingtalk_open", "note_length": len(note)},
        )
    except AppError as exc:
        if exc.code != "TODO_ALREADY_DECIDED":
            raise
        return _html_page(
            "无法标记完成",
            (
                f"<h1>无法标记完成</h1>"
                f"<div class=\"block\">当前任务状态是 {escape(task.status or '')}，不能从钉钉直接标记完成。</div>"
            ),
            status_code=409,
        )

    task.ack_note = note
    return _html_page("已标记完成", _dispatch_ack_done_body(task, decision_request, executor))


# ── 钉钉回调加解密 ──

def _get_aes_key() -> bytes | None:
    """从配置获取AES密钥（base64解码后32字节）"""
    key_b64 = settings.DINGTALK_CALLBACK_AES_KEY
    if not key_b64:
        return None
    try:
        # 钉钉AES key为43字符base64，补=后解码得32字节
        return base64.b64decode(key_b64 + "=")
    except Exception:
        logger.error("钉钉AES key解码失败")
        return None


def _decrypt_callback(encrypt_str: str, aes_key: bytes) -> str:
    """
    解密钉钉回调内容。
    格式：AES-CBC(random_16 + msg_len_4 + msg + corp_id)
    IV = aes_key[:16]
    """
    encrypted = base64.b64decode(encrypt_str)
    iv = aes_key[:16]
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    decrypted_padded = decryptor.update(encrypted) + decryptor.finalize()

    # 去除PKCS7填充
    unpadder = PKCS7(128).unpadder()
    decrypted = unpadder.update(decrypted_padded) + unpadder.finalize()

    # 解析：random(16) + msg_len(4, big-endian) + msg + corp_id
    msg_len = struct.unpack(">I", decrypted[16:20])[0]
    msg = decrypted[20:20 + msg_len].decode("utf-8")
    # 校验 corp_id（防止跨应用消息伪造）
    corp_id_received = decrypted[20 + msg_len:].decode("utf-8")
    expected_corp_id = settings.DINGTALK_CORP_ID
    if expected_corp_id and corp_id_received != expected_corp_id:
        logger.warning(f"钉钉回调corp_id不匹配: 期望={expected_corp_id} 实际={corp_id_received}")
        raise ValueError("corp_id mismatch")
    return msg


def _encrypt_response(msg: str, aes_key: bytes, nonce: str, timestamp: str) -> dict:
    """
    加密钉钉回调响应。
    返回标准格式：{msg_signature, timeStamp, nonce, encrypt}
    """
    corp_id = settings.DINGTALK_CORP_ID or ""
    msg_bytes = msg.encode("utf-8")
    corp_bytes = corp_id.encode("utf-8")
    msg_len = struct.pack(">I", len(msg_bytes))
    random_bytes = os.urandom(16)
    plaintext = random_bytes + msg_len + msg_bytes + corp_bytes

    # PKCS7填充
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()

    # AES-CBC加密
    iv = aes_key[:16]
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    encrypt_str = base64.b64encode(encrypted).decode("utf-8")

    # 签名：sha1(sort(token, timestamp, nonce, encrypt))
    token = settings.DINGTALK_CALLBACK_TOKEN
    sign_list = sorted([token, timestamp, nonce, encrypt_str])
    sign_str = "".join(sign_list)
    msg_signature = hashlib.sha1(sign_str.encode("utf-8")).hexdigest()

    return {
        "msg_signature": msg_signature,
        "timeStamp": timestamp,
        "nonce": nonce,
        "encrypt": encrypt_str,
    }


def _verify_signature(timestamp: str, sign: str) -> bool:
    """验证钉钉回调签名"""
    token = settings.DINGTALK_CALLBACK_TOKEN
    if not token:
        return False

    string_to_sign = f"{timestamp}\n{token}"
    hmac_code = hmac.new(
        token.encode(),
        string_to_sign.encode(),
        hashlib.sha256,
    ).digest()
    expected_sign = base64.b64encode(hmac_code).decode()
    return hmac.compare_digest(sign, expected_sign)


@router.post("/callback")
async def dingtalk_callback(
    request: Request,
    timestamp: str = Header("", alias="timestamp"),
    sign: str = Header("", alias="sign"),
):
    """
    接收钉钉回调（互动卡片点击、审批结果等）。
    验签通过后分发处理。
    """
    ip = request.client.host if request.client else "unknown"

    # 验证签名（必须配置token，未配置时拒绝请求）
    if not settings.DINGTALK_CALLBACK_TOKEN:
        logger.error("钉钉回调token未配置，拒绝请求")
        return JSONResponse(status_code=500, content={"error": "回调token未配置"})
    if not timestamp or not sign or not _verify_signature(timestamp, sign):
        await audit.log("unknown", "dingtalk.callback_invalid", ip_address=ip)
        logger.warning(f"钉钉回调签名验证失败 IP={ip}")
        return JSONResponse(status_code=403, content={"error": "签名验证失败"})

    # 防重放攻击：验证timestamp在5分钟窗口内
    try:
        from datetime import datetime
        ts_ms = int(timestamp)
        callback_time = datetime.fromtimestamp(ts_ms / 1000, BJT).replace(tzinfo=None)
        if abs((now_bjt() - callback_time).total_seconds()) > 300:
            logger.warning(f"钉钉回调时间戳过旧 IP={ip}")
            return JSONResponse(status_code=403, content={"error": "请求已过期"})
    except (ValueError, TypeError, OSError):
        logger.warning(f"钉钉回调时间戳无效: {timestamp} IP={ip}")
        return JSONResponse(status_code=403, content={"error": "时间戳无效"})

    # 解析请求体（支持AES加密模式和明文模式）
    aes_key = _get_aes_key()
    try:
        raw_body = await request.json()
    except Exception:
        raw_body = {}

    nonce = raw_body.get("nonce", "")

    if aes_key and "encrypt" in raw_body:
        # AES加密模式：先验证 body 级 msg_signature，再解密
        encrypt_str = raw_body["encrypt"]
        body_signature = raw_body.get("msg_signature", "")
        if body_signature:
            token = settings.DINGTALK_CALLBACK_TOKEN
            sign_list = sorted([token, timestamp, nonce, encrypt_str])
            expected_sig = hashlib.sha1("".join(sign_list).encode("utf-8")).hexdigest()
            if not hmac.compare_digest(body_signature, expected_sig):
                logger.warning(f"钉钉回调 msg_signature 验证失败 IP={ip}")
                return JSONResponse(status_code=403, content={"error": "body签名验证失败"})

        try:
            decrypted_str = _decrypt_callback(encrypt_str, aes_key)
            body = json.loads(decrypted_str)
            logger.debug("钉钉回调AES解密成功")
        except Exception as e:
            logger.error(f"钉钉回调AES解密失败: {e}")
            return JSONResponse(status_code=400, content={"error": "解密失败"})
    else:
        # 明文模式（开发环境或卡片回调）
        body = raw_body

    event_type = body.get("EventType", body.get("eventType", "unknown"))
    logger.info(f"收到钉钉回调: type={event_type} IP={ip}")

    # 分发处理
    if event_type in ("bpms_task_change", "bpms_instance_change"):
        # 审批流回调
        await _handle_approval_callback(body)
    elif event_type == "card_callback":
        # 互动卡片按钮点击
        await _handle_card_callback(body)
    else:
        logger.info(f"未处理的钉钉回调类型: {event_type}")

    await audit.log("dingtalk", "dingtalk.callback",
                    detail={"event_type": event_type}, ip_address=ip)

    # 返回加密或明文响应
    if aes_key:
        return _encrypt_response("success", aes_key, nonce, timestamp)
    return {"msg_signature": "", "timeStamp": timestamp, "nonce": nonce, "encrypt": "success"}


async def _handle_approval_callback(body: dict) -> None:
    """处理审批流回调（通过/驳回）"""
    from app.dingtalk.approval import handle_approval_result

    process_instance_id = body.get("processInstanceId", "")
    result = body.get("result", "")
    logger.info(f"审批回调: instance={process_instance_id} result={result}")

    if process_instance_id and result:
        await handle_approval_result(process_instance_id, result)


async def _handle_card_callback(body: dict) -> None:
    """处理互动卡片按钮点击（确认/驳回/需要帮助）"""
    from app.execution.models import DecisionLog

    user_id = body.get("userId", "")
    action_data = body.get("action", {})
    action_value = action_data.get("value", {}) if isinstance(action_data, dict) else {}
    run_id = action_value.get("run_id", "")
    user_action = action_value.get("action", "")  # confirm/reject
    logger.info(f"卡片回调: user={user_id} run_id={run_id} action={user_action}")

    if run_id and user_action:
        action_map = {"confirm": "completed", "reject": "rejected"}
        mapped_action = action_map.get(user_action, user_action)

        feedback = _extract_card_feedback(body, action_data, action_value)
        rating = feedback.get("rating")
        feedback_type = _callback_text(feedback.get("feedback_type"))
        reject_reason = _callback_text(feedback.get("reject_reason"))
        feedback_text = _callback_text(feedback.get("feedback"))
        reason_text = _callback_text(feedback.get("reason"))
        note_text = _callback_text(feedback.get("note"))
        feedback_lines = []
        if reason_text:
            feedback_lines.append(f"原因: {reason_text}")
        if feedback_text:
            feedback_lines.append(f"反馈: {feedback_text}")
        if note_text:
            feedback_lines.append(f"补充说明: {note_text}")
        user_feedback = "\n".join(feedback_lines) or f"via dingtalk card by {user_id}"

        def _sf():
            from app.database import async_session_factory
            return async_session_factory

        update_values = {
            "user_action": mapped_action,
            "user_feedback": user_feedback,
        }
        if rating is not None:
            update_values["rating"] = int(rating)
        if feedback_type:
            update_values["feedback_type"] = feedback_type
        if reject_reason:
            update_values["reject_reason"] = reject_reason

        async with _sf()() as session:
            decisions = (
                await session.execute(
                    select(DecisionLog)
                    .where(DecisionLog.run_id == run_id)
                    .order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc())
                )
            ).scalars().all()
            for decision in decisions:
                for key, value in update_values.items():
                    setattr(decision, key, value)
                decision.approver = user_id or decision.approver
                try:
                    from app.learning.service import capture_decision_log

                    await capture_decision_log(session, decision, user_id=user_id or None)
                except Exception as e:  # noqa: BLE001
                    logger.warning("capture dingtalk decision feedback failed run_id={}: {}", run_id, e)
            await session.commit()

        await audit.log(user_id, f"decision.{mapped_action}", "execution", run_id,
                        detail={"rating": rating, "feedback_type": feedback_type})
        logger.info(f"决策日志更新: run_id={run_id} action={mapped_action} rating={rating}")


@router.post("/card-callback")
async def card_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    timestamp: str = Header("", alias="timestamp"),
    sign: str = Header("", alias="sign"),
):
    """AI 待办互动卡片回调。

    安全模型：
    - 验签（timestamp + sign）
    - actor (钉钉 userId 映射) 必须等于卡片绑定的 assignee_user_id（admin 例外）
    - actor 不匹配 → 拒绝 + 单独私信提示 actor
    """
    if settings.DINGTALK_CALLBACK_TOKEN and timestamp and sign and not _verify_signature(timestamp, sign):
        await audit.log("unknown", "dingtalk.card_callback_invalid")
        raise AppError("DINGTALK_CALLBACK_INVALID", 403)

    body = await request.json()
    action_id = body.get("actionId") or body.get("action_id")
    params = body.get("params", {}) if isinstance(body.get("params"), dict) else {}
    private_data = body.get("cardPrivateData") or body.get("private_data") or {}
    if isinstance(private_data, str):
        try:
            private_data = json.loads(private_data)
        except Exception:
            private_data = {}

    actor_dingtalk_user_id = body.get("userId") or body.get("user_id")
    actor_user = await db.scalar(
        select(User).where(User.dingtalk_user_id == actor_dingtalk_user_id)
    )

    # 分支：派发任务执行人确认完成 (kind=dispatch_ack)
    kind_hint = private_data.get("kind")
    raw_action = params.get("action") or action_id

    if kind_hint == "dispatch_ack" or raw_action == "ack":
        return await _handle_dispatch_ack_callback(
            db,
            private_data=private_data,
            params=params,
            action=str(raw_action or ""),
            actor_dingtalk_user_id=actor_dingtalk_user_id,
            actor_user=actor_user,
        )

    # 默认分支：审批 (review / dispatch 审批阶段)
    decision = "approved" if raw_action == "approve" else "rejected"
    feedback = _extract_card_feedback(params, private_data)
    reason = _format_card_feedback_reason(feedback)
    todo_id_raw = private_data.get("todo_id")
    bound_assignee = private_data.get("assignee_user_id")

    if not todo_id_raw or not bound_assignee:
        raise AppError("DINGTALK_CALLBACK_INVALID", 400)

    try:
        todo_id = int(todo_id_raw)
    except (TypeError, ValueError):
        raise AppError("DINGTALK_CALLBACK_INVALID", 400)

    if not actor_user:
        await audit.log(
            "unknown",
            "todo.callback_unknown_actor",
            "todo",
            str(todo_id),
            detail={"dingtalk_user_id": actor_dingtalk_user_id},
        )
        raise AppError("AUTH_USER_NOT_FOUND", 403)

    if actor_user.id != bound_assignee and actor_user.role != "admin":
        await audit.log(
            actor_user.id,
            "todo.callback_wrong_actor",
            "todo",
            str(todo_id),
            detail={"expected_assignee": bound_assignee, "actual_actor": actor_user.id},
        )
        # 给误点的 actor 单独发私信
        try:
            from app.dingtalk.card_templates import build_todo_unauthorized_notice
            from app.dingtalk.outbox import outbox

            expected_user = await db.get(User, bound_assignee)
            # [H1] 共享 db 事务: 越权审计与提示消息同一事务
            await outbox.enqueue(
                "work_notice",
                actor_user.dingtalk_user_id,
                build_todo_unauthorized_notice(expected_user.name if expected_user else bound_assignee),
                priority=5,
                session=db,
            )
        except Exception as e:
            logger.warning("todo 未授权告警推送失败 todo_id={}: {}", todo_id, e)
        raise AppError("TODO_NOT_ASSIGNED_TO_YOU", 403)

    try:
        result = await todo_service.decide(
            db,
            todo_id=todo_id,
            decision=decision,
            decided_by=actor_user.id,
            channel="dingtalk",
            reason=reason,
            feedback_payload=_card_feedback_payload(
                feedback,
                action=str(raw_action or ""),
                actor_dingtalk_user_id=actor_dingtalk_user_id,
                todo_id=todo_id,
                request_id=str(private_data.get("request_id") or ""),
            ),
        )
    except AppError as exc:
        if exc.code == "TODO_ALREADY_DECIDED":
            return {"ok": True, "note": "already decided"}
        raise

    await audit.log(
        actor_user.id,
        f"todo.{decision}",
        "todo",
        str(todo_id),
        detail={"channel": "dingtalk"},
    )
    return {"ok": True, "todo": result["todo"], "request": result["request"]}


async def _handle_dispatch_ack_callback(
    db: AsyncSession,
    *,
    private_data: dict,
    params: dict,
    action: str,
    actor_dingtalk_user_id: str | None,
    actor_user: User | None,
) -> dict:
    """处理派发子任务执行人在钉钉点击「标记完成」按钮的回调。"""
    task_id_raw = private_data.get("dispatch_task_id")
    bound_executor = private_data.get("executor_user_id")

    if not task_id_raw or not bound_executor:
        raise AppError("DINGTALK_CALLBACK_INVALID", 400)
    try:
        task_id = int(task_id_raw)
    except (TypeError, ValueError):
        raise AppError("DINGTALK_CALLBACK_INVALID", 400)

    if not actor_user:
        await audit.log(
            "unknown",
            "dispatch.callback_unknown_actor",
            "dispatch_task",
            str(task_id),
        )
        raise AppError("AUTH_USER_NOT_FOUND", 403)

    if actor_user.id != bound_executor and actor_user.role != "admin":
        await audit.log(
            actor_user.id,
            "dispatch.callback_wrong_actor",
            "dispatch_task",
            str(task_id),
            detail={"expected_executor": bound_executor, "actual_actor": actor_user.id},
        )
        raise AppError("TODO_NOT_ASSIGNED_TO_YOU", 403)

    feedback = _extract_card_feedback(params, private_data)
    note = (
        _callback_text(feedback.get("note"))
        or _callback_text(feedback.get("feedback"))
        or _callback_text(feedback.get("reason"))
    )
    try:
        result = await todo_service.ack_dispatch_task(
            db,
            task_id=task_id,
            actor_id=actor_user.id,
            channel="dingtalk",
            note=note,
            feedback_payload=_dispatch_ack_feedback_payload(
                feedback,
                action=action,
                actor_dingtalk_user_id=actor_dingtalk_user_id,
                dispatch_task_id=task_id,
                request_id=str(private_data.get("request_id") or ""),
            ),
        )
    except AppError as exc:
        if exc.code == "TODO_ALREADY_DECIDED":
            return {"ok": True, "note": "already done"}
        raise

    await audit.log(
        actor_user.id,
        "dispatch.ack",
        "dispatch_task",
        str(task_id),
        detail={"channel": "dingtalk"},
    )
    return {"ok": True, "dispatch_task": result}


async def handle_text_message(sender_id: str, text: str) -> str | None:
    """
    B9: 钉钉内 Skill 自然语言问答。
    匹配消息中的 Skill ID/名称，用 AI 回答问题。
    返回回复文本，无匹配时返回 None。
    """
    import re

    # 匹配 Skill ID 格式（如 EC-投放-01、SEM-竞价-03）
    match = re.search(r"([A-Za-z]+[\-\u4e00-\u9fff]+[\-\w]+)", text)
    if not match:
        return None

    skill_ref = match.group(1)
    question = text

    try:
        from app.skills.intelligence.ai_service import answer_skill_question
        answer = await answer_skill_question(skill_ref, question)
        return answer
    except Exception as e:
        logger.warning("钉钉问答失败: {}", e)
        return None
