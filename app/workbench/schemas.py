"""Schemas for the Skill Workbench API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ModuleName = Literal["meta", "goal", "rules", "params", "output_table", "test_cases", "workflow"]
WorkbenchMode = Literal["novice", "pro"]
TaskTriggerType = Literal["manual", "cron", "webhook", "event"]
TaskValueType = Literal["string", "number", "date", "json"]
TaskInputSource = Literal["user", "datasource", "previous_skill"]
TaskAdapterType = Literal["dingtalk_card", "email", "slack", "json_webhook", "csv_file"]
TaskPermissionAction = Literal["read_data", "write_data", "send_message", "external_api"]
TaskRiskLevel = Literal["R1", "R2", "R3"]
TaskDataClassification = Literal["public", "internal", "confidential"]
CheckpointKey = Literal["target", "permission", "preview", "responsibility"]
CheckpointDecision = Literal["pending", "approved", "rejected", "changed", "not_required", "auto_approved"]


class SkillStructure(BaseModel):
    meta: dict = Field(default_factory=dict)
    goal: str = ""
    rules: list[dict] = Field(default_factory=list)
    params: list[dict] = Field(default_factory=list)
    output_table: list[dict] = Field(default_factory=list)
    test_cases: list[dict] = Field(default_factory=list)
    workflow: dict = Field(default_factory=dict)
    custom_sections: dict = Field(default_factory=dict)


class ModuleState(BaseModel):
    module: ModuleName
    label: str = ""
    status: Literal["empty", "draft", "ready"] = "empty"
    item_count: int = 0


class ReferenceItem(BaseModel):
    id: str
    title: str
    summary: str = ""
    source_type: str
    source_module: str = ""
    source_id: str = ""
    reference_mode: str = "copy_structure"
    tags: list[str] = Field(default_factory=list)


class WorkbenchSessionCreateRequest(BaseModel):
    mode: WorkbenchMode = "pro"
    active_module: str | None = None  # 前端可能传 'overview' 等 UI 专用值，后端宽松接收


class WorkbenchSessionResponse(BaseModel):
    session_id: str
    skill_id: str
    mode: WorkbenchMode
    active_module: ModuleName | None = None
    current_module: ModuleName | None = None
    status: str = "active"
    skill: SkillStructure
    modules: list[ModuleState] = Field(default_factory=list)
    context: dict | None = None
    created_at: datetime | str
    updated_at: datetime | str
    persistence: Literal["database", "memory"] = "database"


class WorkbenchIntentRequest(BaseModel):
    session_id: str
    message: str = ""
    active_module: ModuleName | None = None
    references: list[dict] = Field(default_factory=list)


class WorkbenchIntentResponse(BaseModel):
    session_id: str
    skill_id: str
    message: str
    target_module: ModuleName
    intent: str
    confidence: float = Field(ge=0.0, le=1.0)
    need_clarification: bool = False
    clarification_question: str | None = None
    reasoning: str = ""


class WorkbenchPatchRequest(BaseModel):
    session_id: str
    message: str = ""
    target_module: ModuleName
    references: list[dict] = Field(default_factory=list)


class WorkbenchPatchResponse(BaseModel):
    patch_id: str
    session_id: str
    target_module: ModuleName
    intent: str
    summary: str
    patch: dict
    diff_preview: dict
    validation: dict


class WorkbenchGenerateDraftRequest(BaseModel):
    message: str = Field(min_length=1)
    mode: WorkbenchMode = "novice"
    references: list[dict] = Field(default_factory=list)


class WorkbenchGenerateDraftResponse(BaseModel):
    source: Literal["ai", "fallback"]
    summary: str
    skill: SkillStructure
    modules: list[ModuleState] = Field(default_factory=list)
    validation: dict = Field(default_factory=dict)


class WorkbenchValidateRequest(BaseModel):
    patch_id: str


class WorkbenchValidateResponse(BaseModel):
    patch_id: str
    validation: dict


class WorkbenchApplyRequest(BaseModel):
    patch_id: str
    confirm: bool = True


class WorkbenchApplyResponse(BaseModel):
    patch_id: str
    skill_id: str
    status: str
    target_module: ModuleName | None = None
    git_commit: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    skill: SkillStructure | None = None
    workflow_name: str | None = None
    updated_content: str | None = None  # Patch 应用后的 SKILL.md 全文，前端用于刷新编辑器


class WorkbenchCreateSkillFromDraftRequest(BaseModel):
    skill: SkillStructure
    skill_id: str | None = None


class WorkbenchCreateSkillFromDraftResponse(BaseModel):
    skill_id: str
    git_commit: str | None = None
    quality_score: int | None = None


class WorkbenchReferenceListResponse(BaseModel):
    items: list[ReferenceItem] = Field(default_factory=list)


# ═══ Studio 新增 Schemas（上下文感知对话） ═══

class SelectionContext(BaseModel):
    """前端选区信息"""
    module_id: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    text: str = ""


class FailureRecord(BaseModel):
    """最近的验证/测试失败记录"""
    type: str  # "validation" | "test" | "execution"
    module: str | None = None
    message: str = ""


class ChatContext(BaseModel):
    """上下文感知对话的上下文信息"""
    active_module: str | None = None  # 前端可能传 'overview' 等 UI 专用值，后端宽松接收
    selection: SelectionContext | None = None
    draft_revision: int = 0
    draft_snapshot: dict | None = None  # 前端未保存的模块数据
    recent_failures: list[FailureRecord] = Field(default_factory=list)


class ChatRequest(BaseModel):
    """上下文感知对话请求"""
    session_id: str
    message: str = Field(..., min_length=1, max_length=5000)
    context: ChatContext = Field(default_factory=ChatContext)
    intent: str | None = None
    reference_ids: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """上下文感知对话响应"""
    type: Literal["patch", "answer", "error"]
    message: str
    patch: dict | None = None
    validation_hint: dict | None = None


class CommandRequest(BaseModel):
    """Slash command 请求"""
    session_id: str
    command: str = Field(..., pattern=r"^[a-z][a-z0-9-]*$")
    context: ChatContext = Field(default_factory=ChatContext)


class PartialApplyRequest(BaseModel):
    """Hunk 级部分应用请求"""
    session_id: str
    patch_id: str
    accepted_hunks: list[int]
    rejected_hunks: list[int] = Field(default_factory=list)


class TaskContractTrigger(BaseModel):
    type: TaskTriggerType = "manual"
    expression: str = ""
    description: str = ""


class TaskContractInput(BaseModel):
    name: str
    type: TaskValueType = "string"
    source: TaskInputSource = "user"
    required: bool = True


class TaskContractOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    adapter: TaskAdapterType = "dingtalk_card"
    schema_: dict = Field(default_factory=dict, alias="schema")
    recipient: str = ""


class TaskContractPermission(BaseModel):
    action: TaskPermissionAction
    target: str
    reversible: bool = True


class TaskContractRisk(BaseModel):
    level: TaskRiskLevel = "R1"
    data_classification: TaskDataClassification = "internal"
    department: str = ""


class TaskContractTestCase(BaseModel):
    name: str
    input: dict = Field(default_factory=dict)
    expected_keywords: list[str] = Field(default_factory=list)


class TaskContract(BaseModel):
    goal: str
    trigger: TaskContractTrigger
    input: list[TaskContractInput] = Field(default_factory=list)
    output: TaskContractOutput
    permissions: list[TaskContractPermission] = Field(default_factory=list)
    risks: TaskContractRisk
    fixtures: list[dict] = Field(default_factory=list)
    test_cases: list[TaskContractTestCase] = Field(default_factory=list)


class WorkbenchCheckpointStatus(BaseModel):
    key: CheckpointKey
    title: str
    description: str = ""
    required: bool = True
    approved: bool = False
    decision: CheckpointDecision = "pending"
    detail: dict = Field(default_factory=dict)


class WorkbenchGateItem(BaseModel):
    key: str
    label: str
    passed: bool = False
    detail: str = ""


class WorkbenchGateStatus(BaseModel):
    items: list[WorkbenchGateItem] = Field(default_factory=list)
    required_count: int = 0
    completed_count: int = 0
    can_generate_skill: bool = False
    can_publish: bool = False


class WorkbenchTaskContractPreview(BaseModel):
    adapter: str
    cache_key: str
    rendered_output: str
    card_payload: dict = Field(default_factory=dict)
    fixture_used: dict = Field(default_factory=dict)
    cached: bool = False
    generated_at: datetime | str | None = None
    success: bool = True


class WorkbenchTaskContractRequest(BaseModel):
    # 用户在 Blueprint 输入框可以粘贴 SOP / 会议纪要 / API 文档。
    message: str = Field(..., min_length=1, max_length=50000)


class WorkbenchTaskContractResponse(BaseModel):
    draft_id: str
    intent_md: str
    policy_yaml: str
    contract: TaskContract
    preview: WorkbenchTaskContractPreview
    checkpoints: list[WorkbenchCheckpointStatus] = Field(default_factory=list)
    gate: WorkbenchGateStatus
    skill: SkillStructure


class WorkbenchTaskContractReviewRequest(BaseModel):
    checkpoint: CheckpointKey
    decision: Literal["approved", "rejected", "changed"]
    detail: dict = Field(default_factory=dict)


class WorkbenchTaskContractReviewResponse(BaseModel):
    draft_id: str
    checkpoints: list[WorkbenchCheckpointStatus] = Field(default_factory=list)
    gate: WorkbenchGateStatus


class WorkbenchTaskContractBindRequest(BaseModel):
    skill_id: str


class WorkbenchTaskContractBindResponse(BaseModel):
    draft_id: str
    skill_id: str
    bound: bool = True
