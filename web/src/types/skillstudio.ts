import type { Ref } from 'vue'
import type { RouteLocationNormalizedLoaded, Router } from 'vue-router'

import type {
  CodingFileChangePayload,
  CodingPermissionRequestPayload,
  CodingRuntimeProfile,
  CodingSessionReadyPayload,
  CodingToolCallPayload,
  CodingToolResultPayload,
  CodingUsagePayload,
} from './coding-agent'
import type { SkillDocument, SkillRuleStep, SkillTestCase, StructuredSkillResponse } from './skill'

export type SkillStudioPerspective = 'create' | 'edit' | 'explain' | 'review'

export type SkillStudioModuleKey =
  | 'overview'
  | 'meta'
  | 'goal'
  | 'rules'
  | 'params'
  | 'output_table'
  | 'todos'
  | 'test_cases'
  | 'workflow'

export type SkillStudioSelectionRange = {
  moduleId?: string | null
  startLine?: number | null
  endLine?: number | null
  text?: string
}

export type WorkbenchPatchDraft = {
  patch_id?: string
  target_module?: string | null
  [key: string]: unknown
}

export type WorkbenchChatMessage = {
  role: string
  content: string
  module_id?: string
  _boundary?: boolean
  _original_count?: number
  streaming?: boolean
  _streamStartTs?: number
  _streamErrorReason?: string
  gitCommit?: string
  gitCommitFull?: string
  diffSummary?: Record<string, unknown> | null
  usage?: CodingUsagePayload
  durationMs?: number
  totalCostUsd?: number
  toolCallCount?: number
  toolCalls?: Array<CodingToolCallPayload & { status?: 'pending' | 'success' | 'error'; result?: string }>
  fileChanges?: CodingFileChangePayload[]
  [key: string]: unknown
}

export type ConversationSummary = {
  id: string
  title: string
  created_at?: string
}

export type ConversationMessage = {
  role: string
  content?: string
  message?: string
  text?: string
  module_id?: string
  _boundary?: boolean
  _original_count?: number
}

export type ConversationListResponse = {
  conversations?: Array<{
    id?: string
    conversation_id?: string
    title?: string
    created_at?: string
  }>
}

export type ConversationDetailResponse = {
  messages?: ConversationMessage[]
  budget_used?: number
  budget_limit?: number
  budget_status?: string
}

export type ChatBudget = {
  used: number
  limit: number
  status: string
}

export type CodingSessionMeta = {
  session_id: string
  model: string
  prompt_hash: string
  config_dir: string
  tools_count: number
  runtime_profile: CodingRuntimeProfile | null
}

export type UploadReportResponse = {
  skill_md?: string
  content?: string
  skill?: SkillDocument
  message?: string
}

export type GenerateDraftResponse = {
  skill?: SkillDocument
  source?: string
  message?: string
}

export type FileTreeNode = {
  path: string
  name: string
  type: 'file' | 'dir'
  children?: FileTreeNode[]
}

export type WorkbenchSessionResponse = {
  session_id?: string
  sessionId?: string
  id?: string
  mode?: string
  current_module?: string
  currentModule?: string
  messages?: ConversationMessage[]
  [key: string]: unknown
}

export type WorkbenchChatResult = {
  message: string
  type?: string
  patch?: WorkbenchPatchDraft | null
}

export type CoachSuggestion = {
  id?: string
  title?: string
  description?: string
  severity?: string
  action?: string
  evidence?: Record<string, unknown>
}

export type GuardianItem = {
  severity?: string
  dimension?: string
  segment?: string
  metric?: string
  description?: string
  evidence?: Record<string, unknown>
}

export type HealthScore = Record<string, unknown> | null
export type ShadowDivergenceRate = Record<string, unknown> | null

export type RootCauseReport = {
  summary?: string
  causes?: Array<{ probability: number; cause: string }>
  candidate_patches?: Array<{ category?: string; description: string; risk?: string }>
}

export type ReviewRecord = {
  id?: number | string
  status?: string
  reviewer?: string
  submitter?: string
  git_commit_before?: string
  git_commit_after?: string
  [key: string]: unknown
}

export type ReviewContext = {
  review: ReviewRecord
  semantic_diff: Record<string, unknown> | null
  ai_review: unknown
  reviewer_report: unknown
}

export type PublishReadinessReport = {
  can_publish?: boolean
  warning_count?: number
  blocker_count?: number
  review_gate?: {
    can_submit_review?: boolean
    score?: number
    threshold?: number
    status?: string
    message?: string
    items?: Array<{
      key?: string
      label?: string
      passed?: boolean
      severity?: string
      detail?: string
      suggestion?: string
    }>
  }
  [key: string]: unknown
}

export type ValidationBlockResponse = {
  errors?: unknown[]
  warnings?: unknown[]
  status?: string
}

export type ValidateRuleIssue = {
  field?: string
  message: string
}

export type ValidateRuleResponse = {
  valid: boolean
  errors?: ValidateRuleIssue[]
  warnings?: ValidateRuleIssue[]
  hints?: ValidateRuleIssue[]
}

export type ReadFileHeadResponse = {
  content?: string
}

export type RegressionDiffResponse = {
  has_changes?: boolean
  test_cases_affected?: unknown[]
  steps?: unknown[]
  output_definition_changed?: boolean
  [key: string]: unknown
}

export type SkillHistoryResponse = {
  commits?: Array<{ hash?: string; message?: string; date?: string; timestamp?: string }>
}

export type SkillDiffResponse = {
  old_content?: string
  before?: string
  new_content?: string
  after?: string
}

export type ListReferencesResponse = {
  items?: unknown[]
}

export type ApplyPatchResponse = {
  skill?: SkillDocument
  changed_files?: string[]
}

export type GenerateTestsResponse = {
  count?: number
}

export type DiscoverAntipatternsResponse = {
  antipatterns?: unknown[]
  discovered?: Record<string, unknown>[]
  stats?: Record<string, unknown>
}

export type SuggestBranchesResponse = {
  branches?: unknown[]
}

export type DriftCheckResponse = {
  drifts?: Record<string, unknown>[]
  message?: string
}

export type DeriveThresholdsResponse = {
  thresholds?: unknown[]
  params?: unknown[]
}

export type SkillDependencyNodeType = 'skill' | 'datasource' | 'playbook'

export type SkillDependencyGraphNode = {
  id: string
  route_id?: string
  type: SkillDependencyNodeType
  label: string
  status?: string
  department?: string
  is_self?: boolean
  can_view?: boolean
}

export type SkillDependencyGraphEdge = {
  from: string
  to: string
  kind: string
}

export type SkillDependencyGraphSummary = {
  skill_count: number
  datasource_count: number
  playbook_count: number
  total_edges: number
}

export type SkillDependencyGraphResponse = {
  nodes?: SkillDependencyGraphNode[]
  edges?: SkillDependencyGraphEdge[]
  summary?: SkillDependencyGraphSummary | null
}

export type RunOnAiclawResponse = {
  output?: unknown
  [key: string]: unknown
}

export type BatchCreateResponse = {
  created?: number
}

export type ShadowComparisonsResponse = {
  comparisons?: Record<string, unknown>[]
}

export type FileChangeTarget = Pick<CodingFileChangePayload, 'path'>

export type ChatOptions = {
  targetModule?: string | null
  selectionRange?: SkillStudioSelectionRange | null
  intent?: string | null
  isCommand?: boolean
  images?: unknown[]
}

export type SkillStudioCreateForm = {
  skill_id: string
  name: string
  department: string
  role: string
  trigger_type: string
  trigger_expression: string
  risk_level: string
  approval_level: number
}

export type SkillStudioDraftSnapshot = {
  doc?: SkillDocument
  activeModule?: string
  activeFile?: string
  activeFileContent?: string | null
  activeFileOriginalContent?: string
  activeFileLanguage?: string
  activeFileDirty?: boolean
}

export type ApiErrorLike = {
  _message?: string
  message?: string
  status?: number
  detail?: {
    locked_by?: string
    [key: string]: unknown
  } | string
  [key: string]: unknown
}

export type ApiErrorWithResponse = ApiErrorLike & {
  response?: {
    status?: number
  }
  _backendCode?: string
}

export function getErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object') {
    const candidate = error as ApiErrorLike
    const detail = candidate.detail
    if (detail && typeof detail === 'object') {
      const reason = detail.reason || detail.message
      const suggestion = detail.suggestion
      if (reason || suggestion) return [reason, suggestion].filter(Boolean).join('：')
    }
    if (candidate._message) return candidate._message
    if (typeof candidate.message === 'string' && candidate.message) return candidate.message
  }
  return fallback
}

export function getErrorStatus(error: unknown): number | undefined {
  if (!error || typeof error !== 'object') return undefined
  const candidate = error as ApiErrorWithResponse
  if (typeof candidate.response?.status === 'number') return candidate.response.status
  if (typeof candidate.status === 'number') return candidate.status
  return undefined
}

export function getBackendErrorCode(error: unknown): string | undefined {
  if (!error || typeof error !== 'object') return undefined
  const candidate = error as ApiErrorWithResponse
  return typeof candidate._backendCode === 'string' ? candidate._backendCode : undefined
}

export function isStructuredSkillResponse(value: unknown): value is StructuredSkillResponse & Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

export function isSkillDocument(value: unknown): value is SkillDocument {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

export function emptyCoverageDocument(): Pick<SkillDocument, 'rules' | 'test_cases'> {
  return {
    rules: [],
    test_cases: [],
  }
}

/**
 * v2.6: 细分 bridge —— 替代 deprecated SkillStudioIdeBridge。
 * - `StudioWbBridge`  : workbench store 相关（skillId + 对话 session / create 表单等）
 * - `StudioDocBridge` : useSkillStudioDocument composable 返回（文档级派生 + 编辑方法）
 * - `StudioDocStoreBridge` : document store 相关（dirty / dirtyModules 真相源）
 *
 * SkillStudioIdeBridge 仍保留类型定义，但已不被任何 composable 的 options 使用；
 * 下次可以 git rm 此类型。
 */
export type StudioWbBridge = {
  skillId: string
}

export type StudioDocBridge = {
  // Vue Ref<T>（由 useSkillStudioDocument 返回的 computed）
  doc: { value: SkillDocument | null | undefined }
  isCreate: { value: boolean }
  hasContent: { value: boolean }
  setActiveModule: (module: string) => void
  initForCreate?: () => Promise<unknown>
  initForEdit?: (id: string) => Promise<unknown>
  saveDirectEdits?: () => Promise<unknown>
  createSkill?: () => Promise<string>
}

export type StudioDocStoreBridge = {
  dirty: boolean
  dirtyModules: Set<string>
}

export type StudioUiModuleBridge = {
  studioActiveModule: string
}

/** @deprecated v2.6: 仅保留类型，不再被 composable 使用 */
export type SkillStudioIdeBridge = {
  skillId: string
  isCreate: boolean
  activeModule: string
  doc: SkillDocument
  dirty: boolean
  hasContent?: boolean
  setActiveModule: (module: string) => void
  initForEdit?: (id: string) => Promise<unknown>
  saveDirectEdits?: () => Promise<unknown>
  createSkill?: () => Promise<string>
  dirtyModules?: { clear: () => void }
  cursorLine?: number
  setCursorLine?: (line: number) => void
}

export type SkillStudioUiBridge = {
  assistantPaneOpen: boolean
  studioPerspective: string
  navigatorOpen?: boolean
  navigatorSection?: string
  toggleAssistant: () => void
  toggleBottomPanel: (tab?: string) => void
  setStudioPerspective: (perspective: string) => void
  setViewMode?: (mode: string) => void
}

export type SkillStudioWorkbenchBridge = {
  messages: WorkbenchChatMessage[]
  selectedReferences?: unknown[]
  selectedReferenceIds?: string[]
  appendMessage?: (role: string, content: string) => void
  setMessages?: (messages: ConversationMessage[] | WorkbenchChatMessage[]) => void
  setSkillDocument?: (document: SkillDocument) => void
  setSession?: (session: WorkbenchSessionResponse) => void
  setActiveModule?: (module: string) => void
  hasSession?: boolean
  sessionId?: string
  // resume 协议支持的 per-skill seq 游标（由 workbench store 提供）
  setLastSeqForSkill?: (skillId: string, seq: number) => void
  getLastSeqForSkill?: (skillId: string) => number
}

export type SkillStudioUserBridge = {
  role: string
  canViewAll: boolean
  userInfo?: {
    id?: string
    user_id?: string
  } | null
}

export type SkillStudioSkillApi = {
  generateFromReport: (formData: FormData) => Promise<UploadReportResponse>
  get: (id: string) => Promise<Record<string, unknown> & { file_tree?: FileTreeNode[] }>
  readFile: (id: string, path: string) => Promise<{ content?: string } | string>
  createFile: (id: string, payload: Record<string, unknown>) => Promise<unknown>
  renameFile: (id: string, path: string, newPath: string) => Promise<unknown>
  deleteFile: (id: string, path: string) => Promise<unknown>
  saveFile: (id: string, path: string, content: string) => Promise<unknown>
  lockStatus: (id: string) => Promise<{ locked_by?: string }>
  acquireLock: (id: string) => Promise<unknown>
  releaseLock: (id: string) => Promise<unknown>
  healthScore: (id: string) => Promise<HealthScore>
  shadowDivergenceRate: (id: string) => Promise<ShadowDivergenceRate>
  guardianAnomalies: (id: string, windowHours?: number) => Promise<{ anomalies?: GuardianItem[] }>
  guardianConflicts: (id: string, scope?: string) => Promise<{ conflicts?: Array<Record<string, unknown>> }>
  guardianDrift: (id: string, windowHours?: number, baselineDays?: number) => Promise<{ reports?: Array<Record<string, unknown>> }>
  guardianRootCause: (id: string) => Promise<{ reports?: RootCauseReport[]; message?: string }>
  reviewerSummarize: (id: string, commitA: string, commitB: string) => Promise<unknown>
  explainPack: (id: string) => Promise<Record<string, unknown> | null>
  validateBlock: (id: string, blockType: string, content: Record<string, unknown> | unknown[] | string) => Promise<ValidationBlockResponse>
  validateRule: (
    id: string,
    data: { step_id: string; condition: string; verdict: string; action: string; next_step: string | null },
  ) => Promise<ValidateRuleResponse>
  readFileHead: (id: string, path: string) => Promise<ReadFileHeadResponse | string>
  regressionDiff: (id: string, against?: string) => Promise<RegressionDiffResponse>
  history: (id: string) => Promise<SkillHistoryResponse | Array<{ hash?: string; message?: string; date?: string; timestamp?: string }>>
  diff: (id: string, params?: Record<string, unknown>) => Promise<SkillDiffResponse>
  diffSummary: (id: string, target: string, base?: string) => Promise<Record<string, unknown>>
  shadowReport: (id: string) => Promise<Record<string, unknown>>
  shadowComparisons: (id: string) => Promise<ShadowComparisonsResponse | Record<string, unknown>[]>
  publishReadiness: (id: string, includeAI?: boolean) => Promise<PublishReadinessReport>
  validate: (id: string) => Promise<Record<string, unknown>>
  deprecate: (id: string) => Promise<unknown>
  batchPublish: (ids: string[]) => Promise<unknown>
  startShadow: (id: string) => Promise<unknown>
  stopShadow: (id: string) => Promise<unknown>
  promoteShadow: (id: string) => Promise<unknown>
  rollback: (id: string, targetCommit: string) => Promise<unknown>
  generateTests: (id: string, data?: Record<string, unknown>) => Promise<GenerateTestsResponse>
  discoverAntipatterns: (id: string, days?: number) => Promise<DiscoverAntipatternsResponse>
  suggestBranches: (id: string, data: Record<string, unknown>) => Promise<SuggestBranchesResponse>
  driftCheck: (id: string) => Promise<DriftCheckResponse>
  deriveThresholds: (id: string, data: Record<string, unknown>) => Promise<DeriveThresholdsResponse>
  runOnAiclaw: (id: string) => Promise<RunOnAiclawResponse>
  batchCreate: (data: Record<string, unknown>) => Promise<BatchCreateResponse>
  validateAntipattern: (id: string, data: Record<string, unknown>) => Promise<Record<string, unknown>>
  previewOutput: (id: string) => Promise<Record<string, unknown>>
}

export type SkillStudioTestApi = {
  listChats: (skillId: string) => Promise<ConversationListResponse | ConversationSummary[]>
  startChat: (skillId: string) => Promise<{ conversation_id?: string; id?: string }>
  getConversation: (conversationId: string) => Promise<ConversationDetailResponse>
  runTest: (skillId: string, data: Record<string, unknown>) => Promise<Record<string, unknown>>
}

export type SkillStudioWorkbenchApi = {
  generateDraft: (data: Record<string, unknown>) => Promise<GenerateDraftResponse>
  createSession: (skillId: string, data?: Record<string, unknown>) => Promise<WorkbenchSessionResponse>
  codingTimeline: (skillId: string, params?: Record<string, unknown>) => Promise<{ items?: unknown[] }>
  codingTurns: (skillId: string, params?: Record<string, unknown>) => Promise<{ items?: unknown[] }>
  command: (skillId: string, data: Record<string, unknown>) => Promise<WorkbenchChatResult>
  chat: (skillId: string, data: Record<string, unknown>) => Promise<WorkbenchChatResult>
  coachEvent: (skillId: string, event: string, context?: Record<string, unknown>) => Promise<{ suggestions?: CoachSuggestion[] }>
  recordCoachAccepted?: (skillId: string, action: string) => Promise<unknown>
  recordEditDuration?: (skillId: string, seconds: number) => Promise<unknown>
  recordCoverageDelta?: (skillId: string, oldCoverage: number, newCoverage: number) => Promise<unknown>
  listReferences: (params?: Record<string, unknown>) => Promise<ListReferencesResponse>
  applyPatch: (skillId: string, data: Record<string, unknown>) => Promise<ApplyPatchResponse>
}

export type SkillStudioReviewApi = {
  list: (params?: Record<string, unknown>) => Promise<{ items?: ReviewRecord[] }>
  get: (id: string) => Promise<ReviewRecord>
  semanticDiff: (id: string) => Promise<Record<string, unknown> | null>
  approve: (id: string, payload: Record<string, unknown>) => Promise<unknown>
  reject: (id: string, payload: Record<string, unknown>) => Promise<unknown>
  comment: (id: string, payload: Record<string, unknown>) => Promise<unknown>
  resolveComment: (reviewId: string, commentId: number) => Promise<unknown>
  create: (data: Record<string, unknown>) => Promise<unknown>
}

export type SkillStudioExecutionApi = {
  run: (data: Record<string, unknown>) => Promise<Record<string, unknown>>
}

export type SkillStudioSessionOptions = {
  route: RouteLocationNormalizedLoaded
  router: Router
  // v2.6: ide facade 拆散
  wb: StudioWbBridge
  studioDoc: StudioDocBridge
  documentStore: StudioDocStoreBridge
  ui: SkillStudioUiBridge
  userStore: SkillStudioUserBridge
  skillApi: Pick<SkillStudioSkillApi, 'lockStatus' | 'acquireLock' | 'releaseLock'>
}

export type SkillStudioReviewOptions = {
  route: RouteLocationNormalizedLoaded
  router: Router
  // v2.6
  wb: StudioWbBridge
  studioDoc: StudioDocBridge
  reviewApi: SkillStudioReviewApi
  skillApi: Pick<SkillStudioSkillApi, 'reviewerSummarize'>
  userStore: SkillStudioUserBridge
  studioMode: Ref<string>
  loadHistory: () => Promise<unknown>
}

export type SkillStudioTelemetryOptions = {
  // v2.6
  wb: StudioWbBridge & SkillStudioWorkbenchBridge
  studioDoc: StudioDocBridge
  ui: SkillStudioUiBridge & { studioActiveModule?: string }
  workbenchApi: SkillStudioWorkbenchApi
  skillApi: Pick<
    SkillStudioSkillApi,
    'healthScore' | 'shadowDivergenceRate' | 'guardianAnomalies' | 'guardianConflicts' | 'guardianDrift' | 'guardianRootCause'
  >
  handleCommand: (command: string) => unknown
  handleSend: (message: string, options?: ChatOptions) => unknown
  runLintCheck: () => Promise<unknown>
}

export type SkillStudioAiOptions = {
  // v2.6
  wb: StudioWbBridge & SkillStudioWorkbenchBridge
  studioDoc: StudioDocBridge
  ui: Pick<SkillStudioUiBridge, 'assistantPaneOpen' | 'navigatorOpen' | 'navigatorSection' | 'setViewMode'> & { studioActiveModule?: string }
  skillApi: Pick<SkillStudioSkillApi, 'generateFromReport' | 'get'>
  testApi: SkillStudioTestApi
  workbenchApi: Pick<SkillStudioWorkbenchApi, 'generateDraft' | 'createSession' | 'codingTimeline' | 'codingTurns' | 'command' | 'chat'>
  chatStarted: Ref<boolean>
  studioMode: Ref<string>
  pendingPatch: Ref<WorkbenchPatchDraft | null>
  loadFileList: (force?: boolean) => Promise<unknown>
  handleSelectFile: (path: string) => Promise<void>
  loadHistory: () => Promise<unknown>
  loadHealthScore: () => Promise<unknown>
}

export type CodingAgentPermissionResponse = {
  updatedInput?: Record<string, unknown>
  message?: string
}

export type CodingAgentClientHandlers = {
  onSessionReady?: (payload: CodingSessionReadyPayload) => void
  onTextDelta?: (text: string) => void
  onToolCall?: (payload: CodingToolCallPayload) => void
  onToolResult?: (payload: CodingToolResultPayload) => void
  onFileChange?: (payload: CodingFileChangePayload) => void
  onPermissionRequest?: (payload: CodingPermissionRequestPayload) => void
}

export function getSkillStudioUserId(userStore: SkillStudioUserBridge): string {
  return userStore.userInfo?.user_id || userStore.userInfo?.id || ''
}

export function computeBranchCoverage(document: Pick<SkillDocument, 'rules' | 'test_cases'>): number {
  const rules = document.rules || []
  const tests = document.test_cases || []
  const totalBranches = rules.reduce((sum: number, rule: SkillRuleStep) => sum + (rule.branches?.length || 0), 0)
  if (!totalBranches) return 0
  return Math.min(1, tests.length / totalBranches)
}

export function normalizeConversationList(
  payload: ConversationListResponse | ConversationSummary[] | null | undefined,
): ConversationSummary[] {
  const rawItems = Array.isArray(payload)
    ? payload
    : payload?.conversations || []

  return rawItems.map((item) => ({
    id: item.id || '',
    title: item.title || item.id?.slice(0, 8) || '对话',
    created_at: item.created_at,
  }))
}

export function normalizeConversationMessages(payload: ConversationDetailResponse): WorkbenchChatMessage[] {
  return (payload.messages || []).map((message) => ({
    role: message.role,
    content: message.content || message.message || message.text || '',
    module_id: message.module_id,
    _boundary: message._boundary || false,
    _original_count: message._original_count || 0,
  }))
}
