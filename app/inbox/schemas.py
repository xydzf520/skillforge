"""Inbox report response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class InboxReportMetric(BaseModel):
    label: str
    value: str
    trend: str | None = None
    delta: str | None = None


class InboxReportRelatedTodo(BaseModel):
    id: int | None = None
    request_id: str
    kind: str
    title: str
    aggregate_status: str
    sla_at: str | None = None
    assignees: list[str] = Field(default_factory=list)


class InboxReportDebugContext(BaseModel):
    raw_output_result: dict | None = None
    input_snapshot: dict | None = None


class InboxReportListItem(BaseModel):
    id: str
    decision_log_id: int
    report_index: int
    run_id: str | None = None
    skill_id: str
    skill_name: str | None = None
    skill_department: str | None = None
    channel: str | None = None
    title: str
    summary: str
    metrics: list[InboxReportMetric] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    trigger_type: str | None = None
    related_todo_count: int = 0
    related_pending_request_count: int = 0
    created_at: str | None = None
    report_design: dict | None = None


class InboxReportListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[InboxReportListItem] = Field(default_factory=list)


class InboxReportDetailResponse(InboxReportListItem):
    content_markdown: str | None = None
    payload: dict | None = None
    related_todos: list[InboxReportRelatedTodo] = Field(default_factory=list)
    debug_context: InboxReportDebugContext | None = None
