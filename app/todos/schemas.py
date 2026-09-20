"""收件中心 Pydantic schemas（GAP-1/GAP-2/GAP-3/GAP-4/GAP-10/GAP-11/GAP-12 共用）。

新字段全部 optional，旧前端读不到 key 也不会炸；新前端按 v-if 渲染。
详见 docs/plans/2026-04-17-inbox-api-gaps.md。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MetricPreview(BaseModel):
    """Report metric 的轻量投影，list 接口只取前 3 条避免响应膨胀。"""

    label: str
    value: str
    trend: str | None = None
    delta: str | None = None


class PrimaryIndicator(BaseModel):
    """Report 的主指标（`/inbox` 待办卡片大号展示字段）。

    契约见 `docs/spec/skill-output-report-v1.md` 的 `primary_indicator`。
    缺省时前端 fallback 到 `metrics_preview[0]`，保持旧行为兼容。
    """

    label: str
    value: str
    severity: Literal["critical", "high", "medium", "low"] | None = None
    tone: str | None = None


class AggregateProgress(BaseModel):
    """会签进度：决策模式为 any_of / all_of 时的 N 审批人进度。"""

    total: int
    done: int
    waiting_on: list[str] = Field(default_factory=list)


class RequesterBrief(BaseModel):
    """触发审批的发起人（不一定是 Skill 执行者，可能是上游调用方）。"""

    id: str
    name: str | None = None


class DispatchCompletionBrief(BaseModel):
    """派发子任务完成摘要，用于“同事已处理”列表展示执行结果。"""

    task_id: int
    executor: str | None = None
    executor_name: str | None = None
    content: str | None = None
    ack_at: str | None = None
    ack_note: str | None = None
    ack_channel: str | None = None


class InboxTodoListItem(BaseModel):
    """`GET /api/todos/` 列表项。

    GAP-1：新增 suggested_actions / metrics_preview / approval_level /
    requester / requester_department / aggregate_progress 六个字段，全部 optional。
    """

    # 现有字段（与 _serialize_todo + _serialize_list_item 对齐）
    id: int
    request_id: str
    kind: str
    assignee: str
    status: str
    decided_at: str | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    decision_channel: str | None = None
    feedback_payload: dict | None = None
    created_at: str | None = None
    updated_at: str | None = None
    title: str
    summary: str | None = None
    skill_id: str
    run_id: str | None = None
    decision_mode: str
    aggregate_status: str
    aggregate_decision: str | None = None
    sla_at: str | None = None

    # GAP-1 新增
    suggested_actions: list[str] = Field(default_factory=list)
    metrics_preview: list[MetricPreview] = Field(default_factory=list)
    approval_level: str | None = None
    requester: RequesterBrief | None = None
    requester_department: str | None = None
    aggregate_progress: AggregateProgress | None = None
    dispatch_done_count: int = 0
    latest_dispatch_completion: DispatchCompletionBrief | None = None
    dispatch_completions: list[DispatchCompletionBrief] = Field(default_factory=list)
    object_id: str | None = None
    object_title: str | None = None
    search_text: str | None = None
    ranking_visitors: int | float | str | None = None
    ranking_orders: int | float | str | None = None
    ranking_visitors_detail: str | None = None
    ranking_orders_detail: str | None = None
    payment_change_pct: int | float | str | None = None
    payment_change_text: str | None = None
    decline_coef: int | float | str | None = None

    # P3-契约 D3：Skill output.reports[0].primary_indicator → DecisionRequest.payload
    # → 列表 API。缺省时前端 fallback 到 metrics_preview[0]。
    primary_indicator: PrimaryIndicator | None = None

    model_config = {"extra": "allow"}


class InboxTodoListResponse(BaseModel):
    items: list[InboxTodoListItem]
    total: int
    page: int
    page_size: int


class BacklogBucket(BaseModel):
    bucket: str  # "<1h" / "1-6h" / "6-24h" / ">24h"
    count: int


class InboxOverviewResponse(BaseModel):
    """GAP-2：`GET /api/inbox/overview`。

    同时覆盖 Hero banner KPI + SLA 达成率 + 积压分桶 + 近 7 天聚合。
    """

    pending: int
    overdue: int
    resolved_today: int
    sla_hit_rate: float  # 0.0 ~ 1.0，近 30 天 (approved+rejected)/(approved+rejected+expired)
    avg_resolve_minutes: float
    backlog_by_age: list[BacklogBucket]
    resolved_last_7d: int
    approved_last_7d: int
    rejected_last_7d: int
