"""Helpers for writing Skill regression cases into Skill Git."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt


REGRESSION_CASE_DIR = "tests/regression/cases"
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")


@dataclass(frozen=True)
class RegressionCaseArtifact:
    case_id: str
    path: str
    content: str
    payload: dict


def sanitize_case_id(raw: str | None) -> str:
    value = str(raw or "").strip()
    if not value:
        raise AppError("REGRESSION_CASE_INVALID", 422, {"detail": "回归用例 id 不能为空"})
    if "/" in value or "\\" in value or ".." in value:
        raise AppError("REGRESSION_CASE_INVALID", 422, {"detail": "回归用例 id 不能包含路径片段"})
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-_.")
    if not normalized or not _CASE_ID_RE.fullmatch(normalized):
        raise AppError("REGRESSION_CASE_INVALID", 422, {"detail": "回归用例 id 只允许字母、数字、点、下划线和连字符"})
    return normalized


def validate_regression_case_payload(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise AppError("REGRESSION_CASE_INVALID", 422, {"detail": "回归用例必须是 JSON object"})

    case_id = payload.get("id")
    if case_id is not None and not isinstance(case_id, str):
        raise AppError("REGRESSION_CASE_INVALID", 422, {"detail": "回归用例 id 必须是字符串"})

    list_fields = ("expected_signals", "must_not_say", "assertions")
    for field in list_fields:
        if field in payload and not isinstance(payload.get(field), list):
            raise AppError("REGRESSION_CASE_INVALID", 422, {"detail": f"{field} 必须是数组"})

    if "expected_output" in payload and not isinstance(payload.get("expected_output"), dict):
        raise AppError("REGRESSION_CASE_INVALID", 422, {"detail": "expected_output 必须是 object"})

    has_expectation = any(
        key in payload and payload.get(key) not in (None, [], {})
        for key in ("expected_signals", "must_not_say", "expected_output", "assertions")
    )
    if not has_expectation:
        raise AppError(
            "REGRESSION_CASE_INVALID",
            422,
            {"detail": "回归用例至少需要 expected_signals、must_not_say、expected_output 或 assertions 之一"},
        )

    snapshot = payload.get("snapshot")
    if snapshot is not None and not isinstance(snapshot, str):
        raise AppError("REGRESSION_CASE_INVALID", 422, {"detail": "snapshot 必须是字符串路径"})
    if isinstance(snapshot, str) and (".." in snapshot or snapshot.startswith(("/", "\\"))):
        raise AppError("REGRESSION_CASE_INVALID", 422, {"detail": "snapshot 不能使用绝对路径或路径穿越"})


def build_regression_case_artifact(
    payload: dict,
    *,
    case_id: str | None,
    actor_id: str,
) -> RegressionCaseArtifact:
    validate_regression_case_payload(payload)
    resolved_case_id = sanitize_case_id(case_id or payload.get("id") or f"case_{now_bjt().strftime('%Y%m%d%H%M%S')}")
    normalized = dict(payload)
    normalized["id"] = resolved_case_id
    normalized.setdefault("created_by", actor_id)
    normalized.setdefault("created_at", isoformat_bjt(now_bjt()))
    validate_regression_case_payload(normalized)
    path = f"{REGRESSION_CASE_DIR}/{resolved_case_id}.json"
    content = json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return RegressionCaseArtifact(
        case_id=resolved_case_id,
        path=path,
        content=content,
        payload=normalized,
    )
