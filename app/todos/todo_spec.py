"""Skill output → 待办规格解析。

约定：Skill 在 OpenClaw 上跑完后返回的 output 中可以包含 `todos` 字段：

    output:
      todos:
        - kind: review                 # 必填: review | dispatch | train_model
          title: "..."                  # 必填
          summary: "..."                # 可选
          payload: {...}                # 可选, 任意上下文 JSON
          decision_mode: any_of         # 可选, any_of/all_of/independent
          sla_hours: 24                 # 可选, 默认 settings.TODO_SLA_HOURS
          # 审批人 (review/dispatch 都用)
          reviewers: ["user1", "user2"] # 显式 user_id 列表; review 时为审批人, dispatch 时即管理者
          reviewer_role: "ai_engineer"  # 或按角色解析(取所有该角色的 active 用户)
          # 仅 dispatch 用
          tasks:
            - executor: "zhang3"        # 可选：预先指定执行人 user_id；不填则待管理者分配
              content: "..."            # 任务描述, 钉钉卡片正文
              deadline: "2026-04-09T18:00:00"  # ISO 时间, 可选
              extra: {...}              # 透传字段
          # 决策完成后回调 OpenClaw
          callback:
            instance_id: "demo-prod"    # 必填, 哪台 OpenClaw/AIClaw 实例
            skill_id: "ec-bid-01"       # 必填
            run_id: "run-..."           # 必填, Skill 当前 run 标识
            next_step: "step_3"         # 可选, Skill 业务自定义 token
            extra: {...}                # 透传字段

如果 output 中没有 todos 字段, 回退到旧逻辑（按 frontmatter 的 approval_level/reviewer 自动生成单条 review）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.common.time_utils import isoformat_bjt, parse_bjt_datetime


@dataclass
class DispatchTaskSpec:
    executor: str | None
    content: str
    deadline: datetime | None = None
    extra: dict | None = None


@dataclass
class TodoSpec:
    kind: str  # review | dispatch | train_model
    title: str
    summary: str | None = None
    payload: dict | None = None
    decision_mode: str = "any_of"
    sla_hours: int | None = None
    reviewers: list[str] = field(default_factory=list)
    reviewer_role: str | None = None
    tasks: list[DispatchTaskSpec] = field(default_factory=list)
    callback: dict | None = None


class TodoSpecParseError(ValueError):
    pass


def build_dispatch_task(
    content: str,
    *,
    executor: str | None = None,
    deadline: str | datetime | None = None,
    extra: dict | None = None,
) -> dict:
    """构造一条 dispatch 子任务 payload。"""
    item = {
        "executor": executor,
        "content": content,
        "deadline": isoformat_bjt(deadline) if isinstance(deadline, datetime) else deadline,
        "extra": extra or None,
    }
    return {k: v for k, v in item.items() if v is not None}


def build_review_todo(
    title: str,
    *,
    summary: str | None = None,
    payload: dict | None = None,
    decision_mode: str = "any_of",
    sla_hours: int | None = None,
    reviewers: list[str] | None = None,
    reviewer_role: str | None = None,
    callback: dict | None = None,
) -> dict:
    """构造 review 待办 payload。"""
    item = {
            "kind": "review",
        "title": title,
        "summary": summary,
        "payload": payload,
        "decision_mode": decision_mode,
        "sla_hours": sla_hours,
        "reviewers": reviewers or None,
        "reviewer_role": reviewer_role,
        "callback": callback,
    }
    return {k: v for k, v in item.items() if v is not None}


def build_dispatch_todo(
    title: str,
    *,
    tasks: list[dict],
    summary: str | None = None,
    payload: dict | None = None,
    decision_mode: str = "any_of",
    sla_hours: int | None = None,
    reviewers: list[str] | None = None,
    reviewer_role: str | None = None,
    callback: dict | None = None,
) -> dict:
    """构造 dispatch 待办 payload。"""
    item = {
        "kind": "dispatch",
        "title": title,
        "summary": summary,
        "payload": payload,
        "decision_mode": decision_mode,
        "sla_hours": sla_hours,
        "reviewers": reviewers or None,
        "reviewer_role": reviewer_role,
        "tasks": tasks,
        "callback": callback,
    }
    return {k: v for k, v in item.items() if v is not None}


def _parse_deadline(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return parse_bjt_datetime(value)
        except ValueError:
            return None
    return None


def parse_todo_specs(output: dict | None) -> list[TodoSpec]:
    """从 Skill output 解析待办规格列表。

    output 不是 dict / 没有 todos 字段 → 返回 []，调用方走 legacy 路径。
    todos 不是 list / 任意一条不合法 → 抛 TodoSpecParseError。
    """
    if not isinstance(output, dict):
        return []
    raw = output.get("todos")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TodoSpecParseError("output.todos 必须是数组")

    specs: list[TodoSpec] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TodoSpecParseError(f"output.todos[{index}] 必须是对象")

        kind = str(item.get("kind") or "review").strip()
        if kind not in {"review", "dispatch", "train_model"}:
            raise TodoSpecParseError(
                f"output.todos[{index}].kind 必须是 review、dispatch 或 train_model (当前: {kind})"
            )

        title = str(item.get("title") or "").strip()
        if not title:
            raise TodoSpecParseError(f"output.todos[{index}].title 必填")

        decision_mode = str(item.get("decision_mode") or "any_of").strip()
        if decision_mode not in {"any_of", "all_of", "independent"}:
            raise TodoSpecParseError(
                f"output.todos[{index}].decision_mode 非法: {decision_mode}"
            )

        reviewers_raw = item.get("reviewers") or []
        if not isinstance(reviewers_raw, list):
            raise TodoSpecParseError(f"output.todos[{index}].reviewers 必须是数组")
        reviewers = [str(x).strip() for x in reviewers_raw if str(x).strip()]

        spec = TodoSpec(
            kind=kind,
            title=title[:200],
            summary=(str(item.get("summary")) if item.get("summary") else None),
            payload=item.get("payload") if isinstance(item.get("payload"), dict) else None,
            decision_mode=decision_mode,
            sla_hours=int(item["sla_hours"]) if isinstance(item.get("sla_hours"), int) else None,
            reviewers=reviewers,
            reviewer_role=(
                str(item["reviewer_role"]).strip()
                if item.get("reviewer_role")
                else None
            ),
            callback=(
                item["callback"]
                if isinstance(item.get("callback"), dict)
                else None
            ),
        )

        if spec.summary:
            spec.summary = spec.summary[:500]

        if kind == "dispatch":
            tasks_raw = item.get("tasks") or []
            if not isinstance(tasks_raw, list) or not tasks_raw:
                raise TodoSpecParseError(
                    f"output.todos[{index}] kind=dispatch 必须包含至少一条 tasks"
                )
            for ti, task in enumerate(tasks_raw):
                if not isinstance(task, dict):
                    raise TodoSpecParseError(
                        f"output.todos[{index}].tasks[{ti}] 必须是对象"
                    )
                executor = str(task.get("executor") or "").strip() or None
                content = str(task.get("content") or "").strip()
                if not content:
                    raise TodoSpecParseError(
                        f"output.todos[{index}].tasks[{ti}].content 必填"
                    )
                spec.tasks.append(
                    DispatchTaskSpec(
                        executor=executor,
                        content=content,
                        deadline=_parse_deadline(task.get("deadline")),
                        extra=task.get("extra") if isinstance(task.get("extra"), dict) else None,
                    )
                )

        if spec.callback is not None:
            cb = spec.callback
            for required in ("instance_id", "skill_id", "run_id"):
                if not cb.get(required):
                    raise TodoSpecParseError(
                        f"output.todos[{index}].callback.{required} 必填"
                    )

        specs.append(spec)

    return specs
