"""任务树响应模型。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class NodeStatus(str, Enum):
    ONLINE = "online"
    MAYBE_OFFLINE = "maybe_offline"
    OFFLINE = "offline"


class SkillRunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    IDLE = "idle"
    QUEUED = "queued"


class WritebackStatus(str, Enum):
    NONE = "none"
    PENDING = "pending"
    APPROVED = "approved"
    DISPATCHED = "dispatched"
    REJECTED = "rejected"


# ── 枚举取值常量导出 ────────────────────────────────────────────
# 前端可通过 openapi schema 拿到值列表做对齐，避免字符串硬编码。
NODE_STATUS_VALUES: list[str] = [e.value for e in NodeStatus]
SKILL_RUN_STATUS_VALUES: list[str] = [e.value for e in SkillRunStatus]
WRITEBACK_STATUS_VALUES: list[str] = [e.value for e in WritebackStatus]


class SkillRunItem(BaseModel):
    """单个 Skill 执行条目。"""

    run_id: str
    skill_id: str
    skill_name: str
    status: SkillRunStatus
    trigger_type: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    input_summary: dict[str, Any] | None = None
    output_summary: Any | None = None
    error_message: str | None = None
    writeback: WritebackStatus = WritebackStatus.NONE
    writeback_detail: dict[str, Any] | None = None


class InstanceSyncItem(BaseModel):
    """最近一次 Skill 同步/发布记录。"""

    job_id: int | None = None
    attempt_id: int | None = None
    skill_id: str | None = None
    skill_name: str | None = None
    version_tag: str | None = None
    review_id: int | None = None
    status: str | None = None
    error: str | None = None
    trigger: str | None = None
    target_scope: str | None = None
    fallback_target: bool = False
    fallback_reason: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None


class InstanceNode(BaseModel):
    """运行节点（L2）。"""

    instance_id: str
    name: str
    department: str | None = None
    agent_type: str
    runtime_type: str | None = None
    bridge_gateway_kind: str | None = None
    bridge_gateway_version: str | None = None
    node_status: NodeStatus
    heartbeat_ago_text: str
    last_heartbeat_ago: str | None = None
    last_heartbeat_at: datetime | None = None
    bridge_version: str | None = None
    bridge_latest_version: str | None = None
    bridge_update_available: bool = False
    bridge_platform: str | None = None
    bridge_skills_dir: str | None = None
    last_sync_at: datetime | None = None
    last_sync_ok: bool | None = None
    latest_sync: InstanceSyncItem | None = None
    capacity: int | None = None
    active_count: int = 0
    is_platform_node: bool = False
    machine_role: str | None = None
    gpu_name: str | None = None
    gpu_backend: str | None = None
    gpu_unified_memory: bool = False
    gpu_memory_total_gb: float | None = None
    gpu_memory_used_gb: float | None = None
    memory_total_gb: float | None = None
    training_available: bool = False
    media_available: bool = False
    supported_tasks: list[str] = Field(default_factory=list)
    recent_skills: list[SkillRunItem] = Field(default_factory=list)
    skills: list[SkillRunItem] = Field(default_factory=list)


class ScheduledJobItem(BaseModel):
    """节点上的定时任务。"""

    skill_id: str
    skill_name: str | None = None
    cron_expression: str | None = None
    status: str | None = None
    total_runs: int = 0
    success_count: int = 0
    failed_count: int = 0
    last_run_at: datetime | None = None
    last_status: str | None = None
    avg_duration_seconds: float | None = None
    total_output_items: int = 0
    current_version: str | None = None
    git_commit: str | None = None
    git_commit_full: str | None = None
    deployed_git_commit: str | None = None
    deployed_git_commit_full: str | None = None
    docker_git_commit: str | None = None
    docker_git_commit_full: str | None = None
    sync_version_tag: str | None = None
    sync_completed_at: datetime | None = None
    git_deploy_recorded: bool = False


class NodeSchedulesResponse(BaseModel):
    """节点定时任务列表。"""

    instance_id: str
    instance_name: str | None = None
    jobs: list[ScheduledJobItem] = Field(default_factory=list)


class DepartmentNode(BaseModel):
    """部门节点（L1）。"""

    department_id: str
    department_name: str
    node_count: int = 0
    online_count: int = 0
    offline_count: int = 0
    today_executions: int = 0
    instances: list[InstanceNode] = Field(default_factory=list)
    is_virtual: bool = False
    anomaly_count: int = 0


class TaskTreeResponse(BaseModel):
    """任务树主响应。"""

    departments: list[DepartmentNode] = Field(default_factory=list)
    projected_at: str
    etag: str
    total_online: int = 0
    total_offline: int = 0
    total_running: int = 0
    today_failed: int = 0
    today_executions: int = 0


class TaskTreeStatsResponse(BaseModel):
    """顶部统计条。"""

    online_nodes: int = 0
    total_nodes: int = 0
    today_executions: int = 0
    # v2.0.16 H1：新增完成数字段。today_executions 是窗口内总运行数（含 RUNNING），
    # today_finished 是已完成数（COMPLETED + FAILED），是 today_success_rate 的分母。
    today_finished: int = 0
    # 无完成样本时返回 None，前端显示 "—" 而非 "0%"——1h 窗口里可能几乎全是
    # RUNNING，把 0.0 当成"全部失败"是错误的
    today_success_rate: float | None = None
    today_failed: int = 0
    saved_hours: float = 0.0
    token_cost_today: float = 0.0
    human_takeover_rate: float = 0.0
    department_count: int = 0
    instance_count: int = 0
    running_count: int = 0
    scheduled_due_soon: int = 0


class NodeDetailResponse(BaseModel):
    """单节点详情。"""

    instance_id: str
    name: str
    department: str | None = None
    agent_type: str
    runtime_type: str | None = None
    bridge_gateway_kind: str | None = None
    bridge_gateway_version: str | None = None
    node_status: NodeStatus
    last_heartbeat_at: datetime | None = None
    capacity: int | None = None
    bridge_version: str | None = None
    bridge_latest_version: str | None = None
    bridge_update_available: bool = False
    bridge_platform: str | None = None
    bridge_skills_dir: str | None = None
    last_sync_at: datetime | None = None
    last_sync_ok: bool | None = None
    latest_sync: InstanceSyncItem | None = None
    heartbeat_history: list[datetime] = Field(default_factory=list)
    active_runs: list[SkillRunItem] = Field(default_factory=list)
    recent_completed: list[SkillRunItem] = Field(default_factory=list)


class RunChainStep(BaseModel):
    """回写链路中的一步。"""

    type: str
    id: str | int
    status: str
    title: str | None = None
    assignee: str | None = None
    decided_at: datetime | None = None
    created_at: datetime | None = None


class TrainingJobSummaryItem(BaseModel):
    """任务树里展示的训练任务基础信息。"""

    job_id: str | None = None
    title: str | None = None
    status: str
    failure_stage: str | None = None
    relation: str | None = None
    target_skill_id: str | None = None
    target_gateway_id: str | None = None
    model_family: str | None = None
    training_strategy: str | None = None
    training_mode: str | None = None
    full_history_cycle_index: int | None = None
    full_history_cycle_count: int | None = None
    automation_step: str | None = None
    dataset_ref: str | None = None
    dataset_window_date: str | None = None
    dataset_window_start: datetime | None = None
    dataset_window_end: datetime | None = None
    dataset_timezone: str | None = None
    source_run_id: str | None = None
    source_sample_count: int = 0
    sample_count: int | None = None
    train_count: int | None = None
    eval_count: int | None = None
    parent_model_deployment_id: str | None = None
    parent_model_family: str | None = None
    parent_artifact_id: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    latest_task_id: int | None = None
    latest_task_status: str | None = None
    latest_task_progress: float | None = None
    latest_task_error: str | None = None
    latest_task_updated_at: datetime | None = None
    deployment_id: str | None = None
    deployment_status: str | None = None
    rollout_percent: int | None = None
    artifact_id: str | None = None
    artifact_sha256: str | None = None
    next_training_window_date: str | None = None
    planned_training_after: datetime | None = None


class RunChainResponse(BaseModel):
    """回写链路。"""

    run_id: str
    skill_id: str
    skill_name: str | None = None
    execution: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    request: dict[str, Any] | None = None
    todos: list[dict[str, Any]] = Field(default_factory=list)
    dispatch_tasks: list[dict[str, Any]] = Field(default_factory=list)
    training_jobs: list[TrainingJobSummaryItem] = Field(default_factory=list)
    chain: list[RunChainStep] = Field(default_factory=list)
    chain_complete: bool = False


class TaskTreeDashboardResponse(BaseModel):
    """部门驾驶舱。"""

    department: str | None = None
    period_days: int = 30
    executions: int = 0
    saved_hours: float = 0.0
    token_cost: float = 0.0
    roi: float = 0.0
    top_skills: list[dict[str, Any]] = Field(default_factory=list)


class FailureDiagnosisResponse(BaseModel):
    """失败诊断结果。"""

    run_id: str
    diagnosis: str
    # 当前诊断是否由 LLM 产出；False 表示走了规则引擎 fallback
    ai_available: bool = True
    # 诊断来源：llm / rule_engine，便于前端区分来源与置信度
    source: str = "llm"


class SkillValueResponse(BaseModel):
    """Skill 价值估算。"""

    skill_id: str
    period_days: int = 30
    saved_hours: float = 0.0
    estimated_cost_saving: float = 0.0
    risk_events_prevented: int = 0
    recommendation: str = ""
