/**
 * API 接口定义：与 FastAPI 后端一一对应
 */
import type { SkillDependencyGraphResponse } from '@/types/skillstudio'
import request from './request'

export type SkillDepartmentOption = {
  name: string
}

export type HallDepartmentOption = {
  id?: string
  name?: string
  department?: string
  type?: string
  parent_id?: string | null
  path?: string | null
}

export type ConnectorKeyRow = {
  id?: string
  key_id?: string
  key_hash?: string
  key_prefix?: string
  key_last4?: string
  hash_prefix?: string
  name?: string
  device_label?: string
  owner_user_id?: string
  owner_name?: string
  department?: string
  scope?: Record<string, unknown>
  scopes?: string[]
  platforms?: string[]
  shop_ids?: string[]
  status?: string
  is_active?: boolean
  revoked_at?: string | null
  created_at?: string | null
  last_used_at?: string | null
  plaintext_key?: string
  api_key?: string
  encrypted_key?: string
}

export type CookiePoolSummaryRow = {
  id?: string
  platform?: string
  shop_id?: string
  shop_name?: string
  department?: string
  active_count?: number
  standby_count?: number
  disabled_count?: number
  total_count?: number
  verify_pass_rate?: number
  mixed_credentials_ratio?: number
  avg_health?: number
  latest_sync_at?: string | null
  warning_count?: number
  data_health_ratio?: number
  data_health_trend?: Array<number | { date?: string; day?: string; ratio?: number; data_health_ratio?: number }>
  health_trend?: Array<number | { date?: string; day?: string; ratio?: number; data_health_ratio?: number }>
  mixed_credentials?: boolean
  circuit_state?: string
  missing_data_scopes?: string[]
  missing_scopes?: string[]
}

export type CookiePoolCredentialRow = {
  id?: string
  credential_alias?: string
  owner_user_id?: string
  owner_name?: string
  owner_department?: string
  owner_status?: string
  device_label?: string
  account_login?: string
  health?: number
  verification_status?: string
  capability_summary?: string | string[] | Record<string, unknown>
  last_used_at?: string | null
  last_verified_at?: string | null
  priority?: number
  is_active?: boolean
  status?: string
}

export type CollectionHealthRow = CookiePoolSummaryRow & {
  data_health_ratio?: number
  proof_count?: number
  warning_groups?: string[]
  circuit_state?: string
  missing_data_scopes?: string[]
  missing_scopes?: string[]
  data_health_trend?: Array<number | { date?: string; day?: string; ratio?: number; data_health_ratio?: number }>
  health_trend?: Array<number | { date?: string; day?: string; ratio?: number; data_health_ratio?: number }>
  mixed_credentials?: boolean
  related_drift_alert_count?: number
}

export type DriftAlertRow = {
  id: string
  platform?: string
  shop_id?: string
  endpoint?: string
  endpoint_hash?: string
  change_type?: string
  first_seen_at?: string | null
  latest_snapshot_at?: string | null
  acknowledged?: boolean
  ack_status?: string
  acknowledged_by?: string | null
  acknowledged_at?: string | null
  before_keys?: string[]
  after_keys?: string[]
  missing_fields?: string[]
  added_fields?: string[]
  row_count?: number | null
  previous_row_count?: number | null
  row_count_change?: number | null
  affected_skills?: string[]
  related_todo_id?: number | string | null
  review_todo_id?: number | string | null
  todo_id?: number | string | null
  review_status?: string | null
  handling_status?: string | null
  handling_result?: string | Record<string, unknown> | null
  result?: string | Record<string, unknown> | null
  diff?: Record<string, unknown> | null
}

export type BrowserSlotRow = {
  slot_id: string
  node_id?: string
  egress_group?: string
  egress_ip_hash?: string
  status?: string
  current_credential_alias?: string
  last_used_at?: string | null
  run_success_rate?: number | null
  daily_success_rate?: number | null
  health?: number | null
  current_run_id?: string | null
}

export type CollectionProofRow = {
  proof_id?: string
  tool_name?: string
  data_scope?: string
  status?: string
  credential_alias?: string
  slot_id?: string
  egress_group?: string
  row_count?: number
  retry_of_proof_id?: string | null
  created_at?: string | null
}

export type RunTraceResponse = {
  run_id: string
  execution_run?: Record<string, unknown>
  credential_plan?: unknown[]
  collection_proofs?: CollectionProofRow[]
  intelligence_analyze_runs?: IntelligenceAnalyzeRunRow[]
  usage_logs?: unknown[]
  platform_cookie_audit?: unknown[]
  decision_log?: Record<string, unknown> | Record<string, unknown>[] | null
  reports?: unknown[]
  _skillforge_meta?: Record<string, unknown>
}

export type IntelligenceAnalyzeRunRow = {
  id?: number
  cache_key?: string
  cache_id?: number | null
  cache_hit?: boolean
  cache_hit_of_run_id?: string | null
  skill_id?: string
  skill_git_commit_full?: string
  prompt_git_ref?: string
  run_id?: string
  instance_id?: string | null
  department?: string | null
  model?: string
  analysis_backend?: string | null
  analysis_agent_id?: string | null
  analysis_delegate_route?: Record<string, unknown> | null
  prompt_version?: string
  prompt_hash?: string
  context_hash?: string
  data_health_ratio?: number | null
  prompt_tokens?: number | null
  completion_tokens?: number | null
  total_tokens?: number | null
  cost_usd?: number | null
  duration_ms?: number | null
  degraded?: boolean | null
  degraded_reason?: string | null
  output_hash?: string | null
  llm_output_hash?: string | null
  evidence_passed?: boolean | null
  error_code?: string | null
  created_at?: string | null
}

export type RunTraceAnalyzeRequest = {
  prompt?: string
  include_raw?: boolean
  json_mode?: boolean
  max_output_tokens?: number
  temperature?: number
  step_limit?: number
  decision_limit?: number
  proof_limit?: number
  snapshot_limit?: number
}

export type RunTraceAnalyzeResponse = {
  ok?: boolean
  run_id?: string
  skill_id?: string | null
  analysis?: unknown
  model?: string
  max_context_tokens?: number
  prompt_hash?: string
  raw_counts?: Record<string, number>
  raw_data?: unknown
  credential_location?: string
}

export type LearningSummaryResponse = {
  scope?: string
  department?: string | null
  days?: number
  generated_at?: string
  events?: { total?: number }
  artifacts?: { by_kind?: Record<string, number>; by_status?: Record<string, number> }
  knowledge?: { indexed?: number }
  training?: { samples?: number }
  agent?: { used_context_edges?: number; memories?: number }
  candidates?: { by_target?: Record<string, number>; by_status?: Record<string, number> }
  self_iteration?: {
    health_score?: number
    agent_candidates?: number
    agent_drafts?: number
    draft_validation_passed?: number
    draft_validation_failed?: number
    implementation_ready?: number
    implementation_blocked?: number
    ai_system_gaps?: number
    ai_system_gaps_open?: number
    ai_system_gaps_reviewing?: number
    governance_tasks?: number
    governance_tasks_pending?: number
    governance_tasks_by_status?: Record<string, number>
    feedback_events?: number
    self_audit_events?: number
  }
}

export type LearningEventRow = {
  id: string
  event_type?: string
  source_type?: string
  source_id?: string
  department?: string | null
  skill_id?: string | null
  run_id?: string | null
  redacted_summary?: string | null
  status?: string
  quality_score?: number | null
  created_at?: string | null
}

export type LearningArtifactRow = {
  id: string
  event_id?: string
  artifact_kind?: string
  title?: string | null
  summary?: string | null
  content_preview?: Record<string, unknown>
  target_type?: string | null
  target_id?: string | null
  skill_id?: string | null
  status?: string
  sink_type?: string | null
  sink_id?: string | null
  quality_score?: number
  confidence?: number
  policy_result?: Record<string, unknown>
  created_at?: string | null
}

export type ImprovementCandidateRow = {
  id: string
  target_type?: string
  target_id?: string | null
  skill_id?: string | null
  title?: string
  proposal?: string
  risk_level?: string | null
  expected_impact?: Record<string, unknown>
  priority_score?: number
  status?: string
  todo_id?: string | null
  external_ref?: Record<string, unknown>
  created_at?: string | null
}

export type LearningGovernanceTaskRow = {
  id: string
  candidate_id?: string
  queue_id?: string
  target_type?: string
  target_id?: string | null
  title?: string
  summary?: string | null
  assignee?: string | null
  status?: string
  priority_score?: number
  payload?: Record<string, unknown>
  created_at?: string | null
  updated_at?: string | null
  completed_at?: string | null
}

export type LearningFlowGraphResponse = {
  nodes?: Array<{ id: string; entity_type?: string; entity_id?: string; label?: string; group?: string }>
  edges?: Array<{ id?: number | string; source?: string; target?: string; relation?: string; weight?: number; created_at?: string | null; metadata?: Record<string, unknown> }>
  days?: number
  generated_at?: string
}

export type LearningFlowTopologyNode = {
  id: string
  label?: string
  meta?: string
  icon?: string
  heat?: number
  tone?: string
  entity_type?: string
  entity_id?: string | null
  payload?: Record<string, unknown>
}

export type LearningFlowTopologyStage = {
  key: string
  title?: string
  caption?: string
  count?: number
  tone?: string
  nodes?: LearningFlowTopologyNode[]
}

export type LearningFlowTopologyConnector = {
  from: string
  to: string
  count?: number
  label?: string
  relation?: string
  intensity?: number
  animated?: boolean
}

export type LearningFlowTopologyResponse = {
  stages?: LearningFlowTopologyStage[]
  connectors?: LearningFlowTopologyConnector[]
  relation_counts?: Record<string, number>
  days?: number
  skill_id?: string | null
  generated_at?: string
  governance?: Record<string, unknown>
}

export type LearningFlowJourneyStep = {
  stage?: string
  label?: string
  entity_type?: string
  entity_id?: string | null
  status?: string
  status_text?: string
  at?: string | null
  summary?: string | null
  meta?: string | null
  action?: string | null
  payload?: Record<string, unknown>
}

export type LearningFlowJourneyRow = {
  id: string
  event_id?: string
  title?: string
  source_type?: string
  source_id?: string
  skill_id?: string | null
  run_id?: string | null
  department?: string | null
  state?: string
  progress?: number
  blockers?: string[]
  stage_count?: number
  artifact_count?: number
  candidate_count?: number
  sink_count?: number
  started_at?: string | null
  last_at?: string | null
  steps?: LearningFlowJourneyStep[]
}

export type LearningFlowJourneyResponse = {
  items?: LearningFlowJourneyRow[]
  total?: number
  state_counts?: Record<string, number>
  blocker_counts?: Record<string, number>
  days?: number
  generated_at?: string
  filters?: Record<string, unknown>
  governance?: Record<string, unknown>
}

export type LearningBottleneckSection = {
  key: string
  title?: string
  count?: number
  severity?: 'ok' | 'warning' | 'danger' | string
  action?: string
  items?: Array<Record<string, unknown>>
}

export type LearningBottlenecksResponse = {
  days?: number
  generated_at?: string
  summary?: Record<string, number>
  sections?: LearningBottleneckSection[]
  filters?: Record<string, unknown>
  governance?: Record<string, unknown>
}

export type LearningTrainingManifestResponse = {
  dataset_ref?: string
  manifest_hash?: string
  sample_total?: number
  skills?: Array<{
    skill_id?: string | null
    sample_count?: number
    eval_count?: number
    avg_quality?: number
    artifact_ids?: string[]
    latest_at?: string | null
    dataset_ref?: string
    manifest_hash?: string
  }>
  samples?: Array<{
    artifact_id?: string
    artifact_kind?: string
    skill_id?: string | null
    run_id?: string | null
    event_id?: string
    quality_score?: number
    confidence?: number
    labels?: string[]
    created_at?: string | null
  }>
  governance?: Record<string, unknown>
  generated_at?: string
}

export type LearningAutomationRunRow = {
  id?: string
  trigger_type?: string
  status?: string
  days?: number
  limit?: number
  materialize?: boolean
  captured?: Record<string, number>
  result?: Record<string, unknown>
  error?: string | null
  created_by?: string | null
  started_at?: string | null
  finished_at?: string | null
}

export type LearningAutomationStatusResponse = {
  days?: number
  generated_at?: string
  automation?: {
    enabled?: boolean
    auto_materialize_enabled?: boolean
    auto_materialize_max_artifacts?: number
    auto_training_enabled?: boolean
    auto_training_dispatch_enabled?: boolean
    auto_deployment_approve_enabled?: boolean
    auto_training_max_jobs?: number
    auto_agentization_ai_enabled?: boolean
    auto_self_audit_ai_enabled?: boolean
    auto_self_audit_remediate_enabled?: boolean
    status?: string
    service_status?: string | null
    service_mode?: string | null
    service_owner?: string | null
    service_started_at?: string | null
    service_stopped_at?: string | null
    service_uptime_seconds?: number | null
    first_run_planned_at?: string | null
    next_run_planned_at?: string | null
    last_loop_finished_at?: string | null
    snapshot_updated_at?: string | null
    started_at?: string | null
    finished_at?: string | null
    error?: string | null
    result?: Record<string, unknown> | null
    interval_seconds?: number
    first_delay_seconds?: number
    days?: number
    limit?: number
  }
  backlog?: {
    ready_by_action?: Record<string, number>
    auto_materializable?: number
    review_required?: number
    governance_tasks_by_status?: Record<string, number>
    governance_pending?: number
    governance_stale?: number
    governance_decisionless?: number
    governance_stale_threshold_hours?: number
    automation_runs_by_status?: Record<string, number>
    automation_failed?: number
    automation_latest_success_at?: string | null
    automation_stale?: boolean
    automation_stale_threshold_hours?: number
  }
  jobs?: { by_status?: Record<string, number> }
  latest?: { event_at?: string | null; job_at?: string | null; automation_run?: LearningAutomationRunRow | null }
  automation_runs?: LearningAutomationRunRow[]
  governance?: Record<string, unknown>
}

export type LearningTrainingJobDatasetSample = {
  artifact_id?: string
  artifact_kind?: string
  title?: string
  summary?: string | null
  skill_id?: string | null
  target_type?: string | null
  target_id?: string | null
  run_id?: string | null
  event_id?: string | null
  quality_score?: number | null
  confidence?: number | null
  status?: string | null
  sink_type?: string | null
  sink_id?: string | null
  labels?: string[]
  source_type?: string | null
  lineage_summary?: Record<string, unknown>
  created_at?: string | null
  updated_at?: string | null
}

export type LearningTrainingJobDatasetResponse = {
  job?: Record<string, unknown>
  dataset?: Record<string, unknown>
  items?: LearningTrainingJobDatasetSample[]
  total?: number
  limit?: number
  generated_at?: string | null
  governance?: Record<string, unknown>
}

export type LearningHomeResponse = {
  summary?: LearningSummaryResponse
  topology?: LearningFlowTopologyResponse
  automation?: LearningAutomationStatusResponse
}

export type LearningPulseMetric = {
  key: string
  label?: string
  value?: string | number | boolean | null
  suffix?: string
}

export type LearningPulseItem = {
  id: string
  title?: string
  meta?: string
  status?: string | null
  entity_type?: string | null
  entity_id?: string | null
  payload?: Record<string, unknown>
}

export type LearningPulseFlowRow = {
  title?: string
  summary?: string
  status?: string | null
  status_text?: string | null
  reason?: string
  source?: string
  destination?: string
  entity_type?: string | null
  entity_id?: string | null
  entity?: string
  input_preview?: unknown
  output_preview?: unknown
  metadata?: Record<string, unknown>
  value?: string | number | boolean | null
}

export type LearningPulseFlowSection = {
  key?: string
  title?: string
  description?: string
  empty_text?: string
  items?: LearningPulseFlowRow[]
  returned_count?: number
  total_count?: number
  hidden_count?: number
}

export type LearningPulseFlowDetail = {
  title?: string
  summary?: string
  sections?: LearningPulseFlowSection[]
  storage?: Record<string, unknown>
  eta?: Record<string, unknown>
}

export type LearningPulseModule = {
  key: string
  title?: string
  subtitle?: string
  status?: string
  status_text?: string
  tone?: string
  metrics?: LearningPulseMetric[]
  items?: LearningPulseItem[]
  detail?: Record<string, unknown>
  flow_detail?: LearningPulseFlowDetail
  filters?: Record<string, unknown>
  returned_count?: number
  total_count?: number
  hidden_count?: number
}

export type LearningPulseDrilldownResponse = {
  module_key?: string
  section_key?: string
  title?: string
  description?: string
  page?: number
  page_size?: number
  total?: number
  returned_count?: number
  hidden_count?: number
  has_more?: boolean
  items?: LearningPulseFlowRow[]
  days?: number
  generated_at?: string
  filters?: Record<string, unknown>
}

export type LearningPulseResponse = {
  scope?: string
  department?: string | null
  days?: number
  generated_at?: string
  filters?: Record<string, unknown>
  modules?: LearningPulseModule[]
  summary?: Record<string, number>
  governance?: Record<string, unknown>
}

export type KnowledgeBaseRow = {
  id: string
  org_unit_id?: string | null
  department: string
  name: string
  description?: string | null
  storage_backend?: string
  status?: string
  config?: Record<string, unknown>
  stats?: {
    document_count?: number
    chunk_count?: number
    media_count?: number
    last_indexed_at?: string | null
  }
  can_write?: boolean
  created_at?: string | null
  updated_at?: string | null
}

export type KnowledgeMediaAsset = {
  id: string
  document_id?: string
  kb_id?: string
  file_name?: string
  mime_type?: string
  byte_size?: number
  sha256?: string
  status?: string
  caption?: string | null
  visible_text?: string | null
  objects?: string[]
  metadata?: Record<string, unknown>
  index_error?: string | null
  content_url?: string
  indexed_at?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export type KnowledgeDocumentRow = {
  id: string
  kb_id: string
  org_unit_id?: string | null
  department: string
  title: string
  source_type: string
  source_ref?: string | null
  status: string
  index_status?: string
  index_error?: string | null
  index_version?: number
  content_mime?: string | null
  source_version?: string | null
  content?: string
  modality?: 'text' | 'image' | string
  media_assets?: KnowledgeMediaAsset[]
  tags?: string[]
  metadata?: Record<string, unknown>
  chunk_count?: number
  token_count?: number
  indexed_at?: string | null
  updated_at?: string | null
  can_write?: boolean
}

export type KnowledgeSearchHit = {
  score: number
  vector_score?: number
  keyword_score?: number
  title_score?: number
  rerank_score?: number
  chunk_id?: number
  document_id: string
  document_title: string
  kb_id: string
  department: string
  source_type: string
  source_ref?: string | null
  modality?: 'text' | 'image' | string
  media_asset_id?: string | null
  media?: KnowledgeMediaAsset | null
  tags?: string[]
  snippet: string
  matched_terms?: string[]
  updated_at?: string | null
}

export type SkillCreateUnifiedResponse = {
  skill_id?: string
  [key: string]: unknown
}

export type SkillExecuteBatchError = string | {
  type?: string
  message?: string
}

export type SkillExecuteBatchRun = {
  run_id: string | null
  index: number
  status: 'ok' | 'err'
  output?: unknown
  error?: SkillExecuteBatchError
}

export type SkillExecuteBatchPayload = {
  params?: Record<string, unknown>
  sandbox?: boolean
  n: number
}

export type SkillExecuteBatchResponse = {
  batch_id?: string
  n?: number
  runs?: SkillExecuteBatchRun[]
}

export type SkillMarkWinnerResponse = {
  ok?: boolean
  batch_id?: string
  run_id?: string
  [key: string]: unknown
}

export type ArchitectGenerationMode = 'basic' | 'swarm'

export type ArchitectAnswer = string | string[]

export type ArchitectAnswerMap = Record<string, ArchitectAnswer>

export type ArchitectQuestionKind = 'single' | 'multi' | 'text' | 'tags'

export interface ArchitectQuestion {
  id: string
  prompt: string
  kind: ArchitectQuestionKind
  candidates: string[]
  default: string | string[]
  hint: string
}

export interface ArchitectRound {
  id: string
  title: string
  description: string
  questions: ArchitectQuestion[]
  inferred: Record<string, unknown>
}

export interface ArchitectNextDoneResponse {
  done: true
  message?: string
}

export type ArchitectNextResponse = ArchitectRound | ArchitectNextDoneResponse

export interface ArchitectSimilarSkill {
  skill_id: string
  name: string
  department: string
  status: string
  semantic_score?: number
  structural_score?: number
  quality_score?: number
  total_score?: number
  reason?: string
  motifs?: string[]
}

export interface ArchitectFindSimilarResponse {
  items?: ArchitectSimilarSkill[]
}

export type ArchitectAssessTier = 'complete' | 'partial' | 'insufficient'
export type ArchitectRecommendedPath = 'direct_synthesize' | 'short_interview' | 'full_interview'
export type ArchitectSkillComplexity = 'simple' | 'moderate' | 'complex'

export interface ArchitectAssessResponse {
  tier: ArchitectAssessTier
  recommended_path: ArchitectRecommendedPath
  skill_complexity: ArchitectSkillComplexity
  confidence: number
  covered_dimensions: string[]
  missing_dimensions: string[]
  rationale: string
  user_hint: string
}

export interface ArchitectGeneratedSkillMeta {
  name?: string
  description?: string
  department?: string
  trigger_type?: string
  risk_level?: string
  [key: string]: unknown
}

export interface ArchitectGeneratedSkillRuleBranch {
  condition?: string
  conclusion?: string
  action?: string
  next_step?: string | null
  [key: string]: unknown
}

export interface ArchitectGeneratedSkillRule {
  id?: string
  name?: string
  description?: string
  branches?: ArchitectGeneratedSkillRuleBranch[]
  [key: string]: unknown
}

export interface ArchitectGeneratedSkillTestCase {
  name: string
  [key: string]: unknown
}

export interface ArchitectGeneratedSkillAntipattern {
  scenario: string
  [key: string]: unknown
}

export interface ArchitectGeneratedSkill {
  meta?: ArchitectGeneratedSkillMeta
  goal?: string
  rules?: ArchitectGeneratedSkillRule[]
  test_cases?: ArchitectGeneratedSkillTestCase[]
  antipatterns?: ArchitectGeneratedSkillAntipattern[]
  _error?: {
    type?: string
    message?: string
  }
  [key: string]: unknown
}

export interface ArchitectLintReport {
  passed?: boolean
  error_count?: number
  [key: string]: unknown
}

export interface ArchitectNextPayload {
  current_round: string
  description: string
  answers: Record<string, ArchitectAnswerMap>
  skill_draft: Record<string, unknown>
}

export interface ArchitectSynthesizePayload {
  description: string
  answers: Record<string, ArchitectAnswerMap>
  mode?: ArchitectGenerationMode
}

export interface ArchitectPipelineResponse {
  skill?: ArchitectGeneratedSkill
  similar_skills?: ArchitectSimilarSkill[]
  lint_report?: ArchitectLintReport | null
  verify_report?: Record<string, unknown>
  can_publish?: boolean
  summary?: string
  explorer_findings?: Record<string, unknown>
  rules_draft?: Record<string, unknown>
  tests_notes?: unknown[]
  verifier_issues?: unknown[]
  leader_summary?: string
}

export interface SkillCreationContext {
  default_department: string
  allowed_departments: string[]
  department_source: string
  can_choose_department: boolean
  naming_policy: {
    mode?: string
    description?: string
    editable?: boolean
    max_length?: number
    [key: string]: unknown
  }
}

export interface SkillCreationIdPreview {
  name: string
  department: string
  skill_id: string
  naming_policy: string
}

export const authApi = {
  login: (data: Record<string, unknown>) => request.post('/auth/login', data),
  logout: () => request.post('/auth/logout'),
  me: () => request.get('/auth/me'),
  changePassword: (data: Record<string, unknown>) => request.post('/auth/change-password', data),
  providers: () => request.get('/auth/providers'),
}

export const skillApi = {
  list: (params?: Record<string, unknown>) => request.get('/skills/', { params }),
  listDepartments: () => request.get<{ departments?: SkillDepartmentOption[] }>('/skills/departments'),
  get: (id: string) => request.get(`/skills/${id}`),
  // v2.8.2 B4：首屏合并端点（skill + SKILL.md + lock 一次返）
  bootstrap: (id: string) => request.get(`/skills/${id}/bootstrap`),
  create: (data: Record<string, unknown>) => request.post('/skills/', data),
  createUnified: (data: Record<string, unknown>) => request.post<SkillCreateUnifiedResponse>('/skills/create', data),
  scanRepo: (dryRun = false) => request.post('/skills/scan-repo', null, {
    params: { dry_run: dryRun },
    headers: dryRun ? undefined : { 'X-Confirm': 'I-UNDERSTAND' },
  }),
  generatePreview: (data: Record<string, unknown>) => request.post('/skills/generate-preview', data),
  batchCreate: (data: Record<string, unknown>) => request.post('/skills/batch-create', data),
  runOnAiclaw: (id: string) => request.post(`/skills/${id}/run-aiclaw`),
  // v2.9.1 M2 多次运行投票
  executeBatch: (id: string, data: SkillExecuteBatchPayload) =>
    request.post<SkillExecuteBatchResponse>(`/skills/${id}/execute-batch`, data),
  markWinner: (runId: string, batchId: string) =>
    request.post<SkillMarkWinnerResponse>(`/skills/runs/${runId}/mark-winner`, { batch_id: batchId }),
  // v2.9.1 H2 依赖图
  dependencyGraph: (id: string) => request.get<SkillDependencyGraphResponse>(`/skills/${id}/dependency-graph`),
  saveContent: (id: string, data: Record<string, unknown>) => request.put(`/skills/${id}/content`, data),
  saveStructured: (id: string, data: Record<string, unknown>) => request.put(`/skills/${id}/structured`, data),
  updateParams: (id: string, data: Record<string, unknown>) => request.put(`/skills/${id}/params`, data),
  history: (id: string) => request.get(`/skills/${id}/history`),
  diff: (id: string, params?: Record<string, unknown>) => request.get(`/skills/${id}/diff`, { params }),
  diffSummary: (id: string, target: string, base = 'HEAD') => request.get(`/skills/${id}/diff-summary`, { params: { target, base } }),
  validate: (id: string) => request.post(`/skills/${id}/validate-all`),
  validateBlock: (id: string, blockType: string, content: Record<string, unknown> | unknown[]) => request.post(`/skills/${id}/validate-block`, { block_type: blockType, content }),
  validateRule: (id: string, data: { step_id: string; condition: string; verdict: string; action: string; next_step: string | null }) => request.post(`/skills/${id}/validate-rule`, data),
  compareParams: (id: string, data: Record<string, unknown>) => request.post(`/skills/${id}/compare-params`, data),
  readFile: (id: string, path: string) => request.get(`/skills/${id}/files/${path}`),
  saveFile: (id: string, path: string, content: string) => request.put(`/skills/${id}/files/${path}`, { content }),
  createFile: (id: string, data: Record<string, unknown>) => request.post(`/skills/${id}/files`, data),
  deleteFile: (id: string, path: string) => request.delete(`/skills/${id}/files/${path}`),
  renameFile: (id: string, path: string, newPath: string) => request.patch(`/skills/${id}/files/${path}`, { new_path: newPath }),
  acquireLock: (id: string) => request.post(`/skills/${id}/lock`),
  releaseLock: (id: string) => request.delete(`/skills/${id}/lock`),
  lockStatus: (id: string) => request.get(`/skills/${id}/lock`),
  startShadow: (id: string) => request.post(`/skills/${id}/shadow/start`),
  stopShadow: (id: string) => request.post(`/skills/${id}/shadow/stop`),
  promoteShadow: (id: string) => request.post(`/skills/${id}/shadow/promote`),
  shadowReport: (id: string) => request.get(`/skills/${id}/shadow/report`),
  delete: (id: string) => request.delete(`/skills/${id}`),
  deprecate: (id: string) => request.post(`/skills/${id}/deprecate`),
  rollback: (id: string, targetCommit: string) => request.post(`/skills/${id}/rollback`, { target_commit: targetCommit }),
  readFileHead: (id: string, path: string) => request.get(`/skills/${id}/file-head/${path}`),
  mermaid: (id: string) => request.get(`/skills/${id}/mermaid`),
  lint: (id: string) => request.post(`/skills/${id}/lint`),
  verifyAI: (id: string) => request.post(`/skills/${id}/verify-ai`),
  publishReadiness: (id: string, includeAI = true) => request.post(`/skills/${id}/publish-readiness`, null, { params: { include_ai: includeAI } }),
  guardianAnomalies: (id: string, windowHours = 24) => request.get(`/skills/${id}/guardian/anomalies`, { params: { window_hours: windowHours } }),
  guardianRootCause: (id: string) => request.post(`/skills/${id}/guardian/root-cause`),
  guardianConflicts: (id: string, scope = 'same_department') => request.get(`/skills/${id}/guardian/conflicts`, { params: { scope } }),
  guardianDrift: (id: string, windowHours = 24, baselineDays = 14) => request.get(`/skills/${id}/guardian/drift`, { params: { window_hours: windowHours, baseline_days: baselineDays } }),
  reviewerSummarize: (id: string, commitA: string, commitB: string) => request.post(`/skills/${id}/reviewer/summarize`, null, { params: { commit_a: commitA, commit_b: commitB } }),
  explainPack: (id: string) => request.get(`/skills/${id}/explain-pack`),
  motifLibrary: () => request.get('/skills/motif-library'),
  generateTests: (id: string, data?: Record<string, unknown>) => request.post(`/skills/${id}/generate-tests`, data || {}),
  regressionDiff: (id: string, against = 'HEAD~1') => request.get(`/skills/${id}/regression-diff`, { params: { against } }),
  paramUsages: (paramName: string, excludeSkill?: string) => request.get(`/skills/params/${paramName}/usages`, { params: { exclude_skill: excludeSkill } }),
  refreshParamIndex: () => request.post('/skills/params/index/refresh'),
  crossSkillConflicts: (department?: string) => request.get('/skills/conflicts', { params: { department } }),
  skillConflicts: (id: string) => request.get(`/skills/${id}/conflicts`),
  driftCheck: (id: string) => request.get(`/skills/${id}/drift-check`),
  pin: (id: string) => request.post(`/skills/${id}/pin`),
  unpin: (id: string) => request.delete(`/skills/${id}/pin`),
  pinned: () => request.get('/skills/pinned'),
  listMembers: (id: string) => request.get(`/skills/${id}/members`),
  memberCandidates: (id: string, params?: Record<string, unknown>) => request.get(`/skills/${id}/member-candidates`, { params }),
  upsertMember: (id: string, data: { user_id: string; role: string }) => request.post(`/skills/${id}/members`, data),
  deleteMember: (id: string, userId: string) => request.delete(`/skills/${id}/members/${encodeURIComponent(userId)}`),
  updateVisibility: (id: string, visibility: string) => request.put(`/skills/${id}/visibility`, { visibility }),
  healthScore: (id: string) => request.get(`/skills/${id}/health-score`),
  validateAntipattern: (id: string, data: Record<string, unknown>) => request.post(`/skills/${id}/validate-antipattern`, data),
  previewOutput: (id: string) => request.post(`/skills/${id}/preview-output`),
  shadowComparisons: (id: string) => request.get(`/skills/${id}/shadow/comparisons`),
  shadowDivergenceRate: (id: string) => request.get(`/skills/${id}/shadow/divergence-rate`),
  batchPublish: (ids: string[]) => request.post('/skills/batch-publish', { skill_ids: ids }),
  generateFromReport: (formData: FormData) => request.post('/skills/generate-from-report', formData),
  suggestParamTuning: (id: string, days?: number) => request.post(`/skills/${id}/suggest-param-tuning`, null, { params: { days } }),
  paramEvidence: (id: string, paramName: string, days = 30) => request.get(`/skills/${id}/param-evidence`, { params: { param_name: paramName, days } }),
  discoverAntipatterns: (id: string, days?: number) => request.post(`/skills/${id}/discover-antipatterns`, null, { params: { days } }),
  suggestBranches: (id: string, data: Record<string, unknown>) => request.post(`/skills/${id}/suggest-branches`, data),
  deriveThresholds: (id: string, data: Record<string, unknown>) => request.post(`/skills/${id}/derive-thresholds`, data),
  recommendTemplate: (data: Record<string, unknown>) => request.post('/skills/recommend-template', data),
  listTemplates: () => request.get('/skills/templates'),
  getTemplate: (id: string) => request.get(`/skills/templates/${id}`),
  forkTemplate: (id: string, data: Record<string, unknown>) => request.post(`/skills/templates/${id}/fork`, data),

  // Skill 大厅 API
  hall: (params?: Record<string, unknown>) => request.get('/skills/hall', { params }),
  hallStats: () => request.get('/skills/hall/stats'),
  hallFilters: () => request.get('/skills/hall/filters'),
  forkSkill: (id: string, data: Record<string, unknown>) => request.post(`/skills/${id}/fork`, data),
}

export const workbenchApi = {
  generateDraft: (data: Record<string, unknown>) => request.post('/skills/workbench/generate-draft', data),
  createSkill: (data: Record<string, unknown>) => request.post('/skills/workbench/create-skill', data),
  openCreateSkillStream: () => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return new WebSocket(`${proto}//${window.location.host}/api/skills/workbench/create-skill/stream`)
  },
  finalizeCreateSkill: (draftId: string) => request.post(`/skills/workbench/create-skill/finalize/${draftId}`),
  getCreationContext: () => request.get<SkillCreationContext>('/skills/workbench/create-skill/context'),
  previewCreationId: (data: { name: string; department?: string }) =>
    request.post<SkillCreationIdPreview>('/skills/workbench/create-skill/preview-id', data),
  getMyActiveDraft: () => request.get('/skills/workbench/my-active-draft'),
  dismissDraft: (draftId: string) => request.post(`/skills/workbench/draft/${draftId}/dismiss`),
  generateTaskContract: (data: Record<string, unknown>) => request.post('/skills/workbench/task-contract', data),
  reviewTaskContract: (draftId: string, data: Record<string, unknown>) => request.post(`/skills/workbench/task-contract/${draftId}/review`, data),
  bindTaskContract: (draftId: string, data: Record<string, unknown>) => request.post(`/skills/workbench/task-contract/${draftId}/bind`, data),
  listReferences: (params?: Record<string, unknown>) => request.get('/skills/workbench/references', { params }),
  createSession: (skillId: string, data?: Record<string, unknown>) => request.post(`/skills/${skillId}/workbench/session`, data || {}),
  getSession: (skillId: string, sessionId: string) => request.get(`/skills/${skillId}/workbench/session/${sessionId}`),
  codingTimeline: (skillId: string, params?: Record<string, unknown>) =>
    request.get(`/skills/${skillId}/workbench/coding/timeline`, { params }),
  codingTurns: (skillId: string, params?: Record<string, unknown>) =>
    request.get(`/skills/${skillId}/workbench/coding/turns`, { params }),
  parseIntent: (skillId: string, data: Record<string, unknown>) => request.post(`/skills/${skillId}/workbench/intent`, data),
  createPatch: (skillId: string, data: Record<string, unknown>) => request.post(`/skills/${skillId}/workbench/patch`, data),
  validatePatch: (skillId: string, data: Record<string, unknown>) => request.post(`/skills/${skillId}/workbench/validate`, data),
  applyPatch: (skillId: string, data: Record<string, unknown>) => request.post(`/skills/${skillId}/workbench/apply`, data),
  chat: (skillId: string, data: Record<string, unknown>) => request.post(`/skills/${skillId}/workbench/chat`, data),
  command: (skillId: string, data: Record<string, unknown>) => request.post(`/skills/${skillId}/workbench/command`, data),
  applyPartial: (skillId: string, data: Record<string, unknown>) => request.post(`/skills/${skillId}/workbench/apply-partial`, data),
  architectAssess: (description: string) =>
    request.post<ArchitectAssessResponse>('/skills/architect/assess', { description }),
  // v2.11.4 实验端点（保留）；默认不走
  architectPrepareWorkspace: (description: string, department?: string) =>
    request.post<{ skill_id: string; draft_id: string; work_dir: string; department: string }>(
      '/skills/architect/prepare-workspace',
      { description, department },
    ),
  architectFinalizeCreation: (data: {
    skill_id: string
    name: string
    department: string
    risk_level: string
  }) =>
    request.post<{ skill_id: string; status: string; name: string; department: string; risk_level: string }>(
      '/skills/architect/finalize-creation',
      data,
    ),
  // v2.11.5：老的稳定创建路径（skill_creation_runner 后台任务 + 3 次重试）
  createSkillFinalize: (draftId: string, override?: Record<string, unknown>) =>
    request.post<{ draft_id: string; skill_id: string; git_commit?: string; quality_score?: number }>(
      `/skills/workbench/create-skill/finalize/${encodeURIComponent(draftId)}`,
      override || {},
    ),
  architectStart: (description: string) =>
    request.post<ArchitectRound>('/skills/architect/start', { description }),
  architectNext: (data: ArchitectNextPayload) =>
    request.post<ArchitectNextResponse>('/skills/architect/next', data),
  architectSynthesize: (data: ArchitectSynthesizePayload) =>
    request.post<ArchitectPipelineResponse>('/skills/architect/synthesize', data),
  architectFindSimilar: (description: string) =>
    request.post<ArchitectFindSimilarResponse>('/skills/architect/find-similar', { description }),
  swarmGenerate: (data: ArchitectSynthesizePayload) =>
    request.post<ArchitectPipelineResponse>('/skills/architect/swarm-generate', data),
  coachEvent: (skillId: string, event: string, context: Record<string, unknown> = {}) => request.post(`/skills/${skillId}/coach/event`, { event, context }),
  recordCoachAccepted: (skillId: string, action: string) => request.post(`/skills/${skillId}/coach/accepted`, { action }),
  recordEditDuration: (skillId: string, seconds: number) => request.post(`/skills/${skillId}/telemetry/edit-duration`, { seconds }),
  recordCoverageDelta: (skillId: string, oldCoverage: number, newCoverage: number) =>
    request.post(`/skills/${skillId}/telemetry/coverage-delta`, { old_coverage: oldCoverage, new_coverage: newCoverage }),
}

export const promptApi = {
  list: () => request.get('/admin/prompts'),
  get: (name: string) => request.get(`/admin/prompts/${name}`),
  setDefault: (name: string, version: string) => request.put(`/admin/prompts/${name}/default`, { version }),
}

export const testApi = {
  runTest: (skillId: string, data: Record<string, unknown>) => request.post(`/skills/${skillId}/test`, data),
  startChat: (skillId: string) => request.post(`/skills/${skillId}/chat/start`),
  sendMessage: (convId: string, data: Record<string, unknown>) => request.post(`/skills/chat/${convId}/message`, data),
  getConversation: (convId: string) => request.get(`/skills/chat/${convId}`),
  listChats: (skillId: string) => request.get(`/skills/${skillId}/chat/list`),
  replay: (skillId: string, data: Record<string, unknown>) => request.post(`/skills/${skillId}/replay`, data),
}

export const reviewApi = {
  list: (params?: Record<string, unknown>) => request.get('/reviews/', { params }),
  get: (id: string) => request.get(`/reviews/${id}`),
  create: (data: Record<string, unknown>) => request.post('/reviews/', data),
  approve: (id: string, data: Record<string, unknown>) => request.post(`/reviews/${id}/approve`, data),
  reject: (id: string, data: Record<string, unknown>) => request.post(`/reviews/${id}/reject`, data),
  comment: (id: string, data: Record<string, unknown>) => request.post(`/reviews/${id}/comment`, data),
  semanticDiff: (id: string) => request.post(`/reviews/${id}/semantic-diff`),
  resolveComment: (reviewId: string, commentId: number) => request.post(`/reviews/${reviewId}/comments/${commentId}/resolve`),
}

export const executionApi = {
  run: (data: Record<string, unknown>) => request.post('/executions/run', data),
  listRuns: (params?: Record<string, unknown>) => request.get('/executions/runs', { params }),
  runsStats: () => request.get('/executions/runs/stats'),
  getRun: (id: string) => request.get(`/executions/runs/${id}`),
  getSteps: (id: string) => request.get(`/executions/runs/${id}/steps`),
  getDecisions: (id: string) => request.get(`/executions/runs/${id}/decisions`),
  compare: (run1: string, run2: string) => request.get('/executions/compare', { params: { run1, run2 } }),
  openclawInstances: () => request.get('/executions/openclaw/instances'),
  createInstance: (data: Record<string, unknown>) => request.post('/executions/openclaw/instances', data),
  updateInstance: (id: string, data: Record<string, unknown>) => request.put(`/executions/openclaw/instances/${id}`, data),
  deleteInstance: (id: string) => request.delete(`/executions/openclaw/instances/${id}`),
  testInstance: (id: string) => request.post(`/executions/openclaw/instances/${id}/test`),
  listWorkers: () => request.get('/executions/workers'),
  registerWorker: (data: Record<string, unknown>) => request.post('/executions/workers/register', data),
  heartbeatWorker: (id: string, data: Record<string, unknown>) => request.post(`/executions/workers/${id}/heartbeat`, data),
}

export const taskTreeApi = {
  getTree: (params?: Record<string, unknown>, options?: { refresh?: boolean }) =>
    request.get('/task-tree', {
      params,
      ...(options?.refresh ? { cache: { refresh: true } } : {}),
    } as any),
  getStats: (params?: Record<string, unknown>, options?: { refresh?: boolean }) =>
    request.get('/task-tree/stats', {
      params,
      ...(options?.refresh ? { cache: { refresh: true } } : {}),
    } as any),
  getDashboard: (params?: Record<string, unknown>, options?: { refresh?: boolean }) =>
    request.get('/task-tree/dashboard', {
      params,
      ...(options?.refresh ? { cache: { refresh: true } } : {}),
    } as any),
  getNodeDetail: (instanceId: string) => request.get(`/task-tree/node/${instanceId}`),
  getRunChain: (runId: string) => request.get(`/task-tree/run/${runId}/chain`),
  diagnoseRun: (runId: string) => request.get(`/task-tree/run/${runId}/diagnose`),
  getSkillValue: (skillId: string, params?: Record<string, unknown>) => request.get(`/task-tree/skill/${skillId}/value`, { params }),
}

export const aiclawApi = {
  listInstances: () => request.get('/aiclaw/instances'),
  agentCoverage: () => request.get('/aiclaw/departments/agent-coverage'),
  agentKpi: () => request.get('/aiclaw/departments/agent-kpi'),
  listAnalysisAgents: (params?: Record<string, unknown>) =>
    request.get('/agents/analysis-agents', { params }),
  upsertAnalysisAgent: (data: Record<string, unknown>) =>
    request.post('/agents/analysis-agents', data),
  validateAnalysisAgent: (id: string, data?: Record<string, unknown>) =>
    request.post(`/agents/analysis-agents/${encodeURIComponent(id)}/validate`, data || {}),
  getInstance: (id: string) => request.get(`/aiclaw/instances/${id}`),
  getStatus: (id: string) => request.get(`/aiclaw/instances/${id}/status`),
  createInstance: (data: Record<string, unknown>) => request.post('/aiclaw/instances', data),
  updateInstance: (id: string, data: Record<string, unknown>) => request.put(`/aiclaw/instances/${id}`, data),
  deleteInstance: (id: string) => request.delete(`/aiclaw/instances/${id}`),
  regenerateToken: (id: string) => request.post(`/aiclaw/instances/${id}/regenerate-token`),
  regenerateEnrollment: (id: string) => request.post(`/aiclaw/instances/${id}/regenerate-enrollment`),
  revokePubkey: (id: string) => request.post(`/aiclaw/instances/${id}/revoke-pubkey`),
  resetBinding: (id: string) => request.post(`/aiclaw/instances/${id}/reset-binding`),
  rotateKey: (id: string) => request.post(`/aiclaw/instances/${id}/rotate-key`),
  downloadBridgeScript: (id: string, oneTimeToken: string, platform: 'linux' | 'darwin') =>
    request.get(`/aiclaw/instances/${id}/bridge-script`, {
      params: { one_time_token: oneTimeToken, platform },
      responseType: 'blob',
    }),
  downloadSystemdUnit: (id: string) =>
    request.get(`/aiclaw/instances/${id}/systemd-unit`, { responseType: 'blob' }),
  listAgents: (id: string) => request.get(`/aiclaw/instances/${id}/agents`),
  validateChatModelContext: (data: Record<string, unknown>) => request.post('/aiclaw/chat/model-context/validate', data),
  listSkills: (id: string, agentId: string) => request.get(`/aiclaw/instances/${id}/agents/${agentId}/skills`),
  capabilities: (id: string) => request.get(`/aiclaw/instances/${id}/capabilities`),
  listSyncTargets: (skillId: string) => request.get(`/aiclaw/sync-targets/${skillId}`),
  syncSkillToTargets: (skillId: string, data: Record<string, unknown> = {}) =>
    request.post(`/aiclaw/sync-skill/${skillId}`, data),
  syncSkill: (id: string, skillId: string) => request.post(`/aiclaw/instances/${id}/sync-skill/${skillId}`),
  removeSkillFromDevice: (id: string, skillId: string) => request.delete(`/aiclaw/instances/${id}/sync-skill/${skillId}`),
  importSkillFromDevice: (id: string, skillId: string) => request.post(`/aiclaw/instances/${id}/import-skill/${skillId}`),
  forceUpdateBridge: (id: string) => request.post(`/aiclaw/instances/${id}/force-update`),
  disconnectBridge: (id: string) => request.post(`/aiclaw/instances/${id}/disconnect`),
  getMediaBootstrap: (id: string) => request.get(`/aiclaw/instances/${id}/media/bootstrap`),
  startMediaBootstrap: (id: string, data: Record<string, unknown>) =>
    request.post(`/aiclaw/instances/${id}/media/bootstrap`, data),
  cancelMediaBootstrap: (id: string) =>
    request.post(`/aiclaw/instances/${id}/media/bootstrap/cancel`),
}

export const trainingApi = {
  resources: () => request.get('/training/resources'),
  automationStatus: (params?: Record<string, unknown>) => request.get('/training/automation/status', { params }),
  runAutomation: (data: Record<string, unknown> = {}) => request.post('/training/automation/run', data),
  fullHistoryFinetuneStatus: () => request.get('/training/full-history-finetune/status'),
  runFullHistoryFinetune: (data: Record<string, unknown> = {}) =>
    request.post('/training/full-history-finetune/run', data),
  chatFullHistoryFinetunedModel: (data: Record<string, unknown> = {}) =>
    request.post('/training/full-history-finetune/chat', data),
  jobs: (params?: Record<string, unknown>) => request.get('/training/jobs', { params }),
  deployments: (params?: Record<string, unknown>) => request.get('/training/deployments', { params }),
  getDeployment: (id: string) => request.get(`/training/deployments/${id}`),
  chatDeploymentModel: (id: string, data: Record<string, unknown> = {}) =>
    request.post(`/training/deployments/${encodeURIComponent(id)}/chat`, data),
  datasets: () => request.get('/training/datasets'),
  assetSources: (params?: Record<string, unknown>) => request.get('/training/assets/sources', { params }),
  assetSamples: (params?: Record<string, unknown>) => request.get('/training/assets/samples', { params }),
  datasetVersions: (params?: Record<string, unknown>) => request.get('/training/dataset-versions', { params }),
  getDatasetVersion: (id: string, params?: Record<string, unknown>) =>
    request.get(`/training/dataset-versions/${encodeURIComponent(id)}`, { params }),
  datasetVersionAutomation: (id: string) =>
    request.get(`/training/dataset-versions/${encodeURIComponent(id)}/automation`),
  datasetVersionManifest: (id: string) =>
    request.get(`/training/dataset-versions/${encodeURIComponent(id)}/manifest`),
  createDatasetVersion: (data: Record<string, unknown>) => request.post('/training/dataset-versions', data),
  syncDatasetVersion: (id: string, data: Record<string, unknown> = {}) =>
    request.post(`/training/dataset-versions/${encodeURIComponent(id)}/sync`, data),
  autoRunDatasetVersion: (id: string, data: Record<string, unknown> = {}) =>
    request.post(`/training/dataset-versions/${encodeURIComponent(id)}/auto-run`, data),
  createJob: (data: Record<string, unknown>) => request.post('/training/jobs', data),
  skillCandidateReadiness: (skillId: string) => request.get(`/training/skills/${skillId}/candidate-readiness`),
  createSkillCandidate: (skillId: string, data: Record<string, unknown> = {}) =>
    request.post(`/training/skills/${skillId}/candidate`, data),
  createRunCandidate: (runId: string, data: Record<string, unknown> = {}) =>
    request.post(`/training/runs/${runId}/candidate`, data),
  getJob: (id: string) => request.get(`/training/jobs/${id}`),
  jobLogs: (id: string) => request.get(`/training/jobs/${id}/logs`),
  jobArtifacts: (id: string) => request.get(`/training/jobs/${id}/artifacts`),
  downloadJobArtifact: (jobId: string, artifactId: string) =>
    request.get(`/training/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifactId)}/download`, {
      responseType: 'blob',
    }),
  approveJob: (id: string) => request.post(`/training/jobs/${id}/approve`),
  dispatchJob: (id: string) => request.post(`/training/jobs/${id}/dispatch`),
  retryJob: (id: string) => request.post(`/training/jobs/${id}/retry`),
  autoAdvanceJob: (id: string, data: Record<string, unknown> = {}) =>
    request.post(`/training/jobs/${id}/auto-advance`, data),
  simulateJobResult: (id: string, data: Record<string, unknown> = {}) =>
    request.post(`/training/jobs/${id}/simulate-result`, data),
  collectResult: (id: string) => request.post(`/training/jobs/${id}/collect-result`),
  collectDue: (data: Record<string, unknown> = {}) => request.post('/training/jobs/collect-due', data),
  evaluateJob: (id: string) => request.post(`/training/jobs/${id}/evaluate`),
  requestDeployment: (id: string, data: Record<string, unknown>) =>
    request.post(`/training/jobs/${id}/deploy-request`, data),
  approveDeployment: (id: string) => request.post(`/training/deployments/${id}/approve`),
  activateDeployment: (id: string) => request.post(`/training/deployments/${id}/activate`),
  syncDeploymentOpenWebUI: (id: string) => request.post(`/training/deployments/${id}/sync-openwebui`),
  rejectDeployment: (id: string, reason: string) =>
    request.post(`/training/deployments/${id}/reject`, { reason }),
  rollbackDeployment: (id: string, reason: string) =>
    request.post(`/training/deployments/${id}/rollback`, { reason }),
  cancelJob: (id: string) => request.post(`/training/jobs/${id}/cancel`),
  forceCancelJob: (id: string, reason: string) => request.post(`/training/jobs/${id}/force-cancel`, { reason }),
}

export const knowledgeApi = {
  summary: () => request.get<{
    department_count?: number
    document_count?: number
    chunk_count?: number
    media_count?: number
    storage_backend?: string
    last_indexed_at?: string | null
    departments?: KnowledgeBaseRow[]
  }>('/knowledge/summary'),
  bases: () => request.get<{ items?: KnowledgeBaseRow[]; total?: number; storage?: Record<string, unknown> }>('/knowledge/bases'),
  sync: (data: { kb_id?: string; org_unit_id?: string } = {}) => request.post('/knowledge/sync', data),
  documents: (params?: Record<string, unknown>) =>
    request.get<{ items?: KnowledgeDocumentRow[]; total?: number }>('/knowledge/documents', { params }),
  createDocument: (data: Record<string, unknown>) => request.post<KnowledgeDocumentRow>('/knowledge/documents', data),
  getDocument: (id: string) => request.get<KnowledgeDocumentRow>(`/knowledge/documents/${encodeURIComponent(id)}`),
  updateDocument: (id: string, data: Record<string, unknown>) =>
    request.put<KnowledgeDocumentRow>(`/knowledge/documents/${encodeURIComponent(id)}`, data),
  archiveDocument: (id: string) => request.delete<KnowledgeDocumentRow>(`/knowledge/documents/${encodeURIComponent(id)}`),
  deleteDocument: (id: string) => request.delete<KnowledgeDocumentRow>(`/knowledge/documents/${encodeURIComponent(id)}`),
  reindexDocument: (id: string) => request.post<KnowledgeDocumentRow>(`/knowledge/documents/${encodeURIComponent(id)}/reindex`),
  uploadDocument: (data: FormData) => request.post<KnowledgeDocumentRow>('/knowledge/documents/upload', data),
  importUrl: (data: Record<string, unknown>) => request.post<KnowledgeDocumentRow>('/knowledge/documents/import-url', data),
  indexJobs: (params?: Record<string, unknown>) => request.get<{ items?: Record<string, unknown>[]; total?: number }>('/knowledge/index-jobs', { params }),
  ragHealth: () => request.get<Record<string, unknown>>('/knowledge/rag/health'),
  testRagServer: (data: Record<string, unknown>) => request.post<Record<string, unknown>>('/knowledge/rag/test-server', data),
  syncRagServer: (data: Record<string, unknown> = {}) => request.post<Record<string, unknown>>('/knowledge/rag/sync-server', data),
  search: (data: { query: string; kb_id?: string; org_unit_id?: string; top_k?: number; modality?: string }) =>
    request.post<{ query?: string; modality?: string; items?: KnowledgeSearchHit[]; total?: number; retrieval?: Record<string, unknown> }>('/knowledge/search', data),
  searchByImage: (data: FormData) =>
    request.post<{
      query?: string
      modality?: string
      image_query?: Record<string, unknown>
      items?: KnowledgeSearchHit[]
      total?: number
      retrieval?: Record<string, unknown>
    }>('/knowledge/search/upload', data),
  ask: (data: { query: string; kb_id?: string; org_unit_id?: string; top_k?: number; use_llm?: boolean; modality?: string }) =>
    request.post<{
      query?: string
      answer?: string
      answer_mode?: string
      confidence?: number
      insufficient_evidence?: boolean
      citations?: KnowledgeSearchHit[]
      sources?: KnowledgeSearchHit[]
      retrieval?: Record<string, unknown>
    }>('/knowledge/ask', data),
  context: (data: { query: string; kb_id?: string; org_unit_id?: string; top_k?: number; max_chars?: number; modality?: string }) =>
    request.post<{
      query?: string
      prompt_context?: string
      context_chars?: number
      sources?: KnowledgeSearchHit[]
      retrieval?: Record<string, unknown>
      usage?: Record<string, unknown>
    }>('/knowledge/context', data),
}

export const agentCoreApi = {
  run: (data: Record<string, unknown>) => request.post('/agent-core/run', data),
  resume: (data: Record<string, unknown>) => request.post('/agent-core/resume', data),
  openStream: () => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    return new WebSocket(`${proto}//${host}/api/agent-core/stream`)
  },
}

export const todoApi = {
  list: (params?: Record<string, unknown>) => request.get('/todos/', { params }),
  stats: () => request.get('/todos/stats', { cache: { ttl: 20, namespace: 'todos' } } as any),
  get: (id: number) => request.get(`/todos/${id}`),
  updateDraft: (id: number, data: Record<string, unknown>) => request.patch(`/todos/${id}/draft`, data),
  checkSource: (id: number, data: Record<string, unknown>) => request.post(`/todos/${id}/source-check`, data),
  decide: (id: number, data: Record<string, unknown>) => request.post(`/todos/${id}/decide`, data),
  extendSla: (id: number, data: Record<string, unknown>) => request.post(`/todos/${id}/extend-sla`, data),
  listAssignableUsers: (params?: Record<string, unknown>) => request.get('/todos/dispatch/assignable-users', { params }),
  listMyDispatchTasks: (params?: Record<string, unknown>) => request.get('/todos/dispatch/mine', { params }),
  assignDispatchTask: (taskId: number, data: Record<string, unknown>) => request.post(`/todos/dispatch/${taskId}/assign`, data),
  updateDispatchTaskStatus: (taskId: number, data: Record<string, unknown>) => request.post(`/todos/dispatch/${taskId}/status`, data),
  ackDispatchTask: (taskId: number, data: Record<string, unknown>) => request.post(`/todos/dispatch/${taskId}/ack`, data),
  // GAP-3：轻量 preview，省 payload / decision_chain，hover 浮层 / 备用场景使用
  preview: (id: number) => request.get(`/todos/${id}/preview`),
  // GAP-4：批量摘要，列表页"嫌重"或多选悬浮预览时用
  bulkSummary: (ids: number[]) => request.post('/todos/bulk-summary', { ids }),
  // GAP-7：批量延期（hours 1-168）
  batchExtendSla: (todoIds: number[], hours: number) =>
    request.post('/todos/batch-extend-sla', { todo_ids: todoIds, hours }),
  // GAP-8：批量转派（同部门校验）
  batchReassign: (todoIds: number[], toUserId: string, reason: string) =>
    request.post('/todos/batch-reassign', { todo_ids: todoIds, to_user_id: toUserId, reason }),
  // GAP-9：批量派发确认
  batchAckDispatch: (taskIds: number[], note: string = '') =>
    request.post('/todos/dispatch/batch-ack', { task_ids: taskIds, note }),
  // 批量决策（已存在后端，但此前前端未封装）
  batchDecide: (todoIds: number[], decision: 'approved' | 'rejected', reason: string = '') =>
    request.post('/todos/batch-decide', { todo_ids: todoIds, decision, reason }),
  // GAP-10：近 N 天待办趋势
  trends: (days: 7 | 30 | 90 = 7) => request.get('/todos/trends', { params: { days } }),
  // GAP-11：同 Skill 历史决策时间线
  relatedTimeline: (id: number, limit: number = 10) =>
    request.get(`/todos/${id}/related-timeline`, { params: { limit } }),
  // GAP-12：派发任务日历视图
  dispatchCalendar: (dateFrom: string, dateTo: string) =>
    request.get('/todos/dispatch/calendar', { params: { date_from: dateFrom, date_to: dateTo } }),
}

export const datasourceApi = {
  list: (params?: Record<string, unknown>) => request.get('/data-sources/', { params }),
  get: (id: string) => request.get(`/data-sources/${id}`),
  create: (data: Record<string, unknown>) => request.post('/data-sources/', data),
  update: (id: string, data: Record<string, unknown>) => request.put(`/data-sources/${id}`, data),
  upload: (id: string, formData: FormData) => request.post(`/data-sources/${id}/upload`, formData),
  previewUpload: (id: string, formData: FormData) => request.post(`/data-sources/${id}/preview-upload`, formData),
  pull: (id: string) => request.post(`/data-sources/${id}/pull`),
  preview: (id: string, params?: Record<string, unknown>) => request.get(`/data-sources/${id}/preview`, { params }),
  history: (id: string, params?: Record<string, unknown>) => request.get(`/data-sources/${id}/history`, { params }),
  quality: (id: string) => request.get(`/data-sources/${id}/quality`),
  migrationPreview: (id: string, data: Record<string, unknown>) => request.post(`/data-sources/${id}/migration-preview`, data),
  getConnectorApiKey: () => request.get('/data-sources/connector-api-key'),
  rotateConnectorApiKey: () => request.post('/data-sources/connector-api-key/rotate'),
  platformConnect: (data: Record<string, unknown>) => request.post('/data-sources/platform-connect', data),
  pushCookies: (sourceId: string, data: Record<string, unknown>) => request.post(`/data-sources/${sourceId}/push-cookies`, data),
  disconnectPlatform: (sourceId: string) => request.post(`/data-sources/${sourceId}/disconnect`),
  testPlatformConnection: (sourceId: string) => request.post(`/data-sources/${sourceId}/test-connection`),
  getCookies: (sourceId: string) => request.get(`/data-sources/${sourceId}/cookies`),
  listApiDiscoveries: (params?: Record<string, unknown>) => request.get('/data-sources/api-discovery', { params }),
  // v2.7 大厅 v3
  requestAccess: (id: string, data: { reason: string }) => request.post(`/data-sources/${id}/request-access`, data),
  listRequests: (id: string) => request.get(`/data-sources/${id}/requests`),
  listMyRequests: () => request.get('/data-sources/requests/my'),
  listPendingRequests: () => request.get('/data-sources/requests/pending'),
  approveRequest: (reqId: number, data: { expires_at?: string; comment?: string }) => request.post(`/data-sources/requests/${reqId}/approve`, data),
  rejectRequest: (reqId: number, data: { comment: string }) => request.post(`/data-sources/requests/${reqId}/reject`, data),
}

export const connectorKeyApi = {
  list: (params?: Record<string, unknown>) =>
    request.get<{ items?: ConnectorKeyRow[]; total?: number } | ConnectorKeyRow[]>('/data-sources/connector-keys', { params }),
  create: (data: Record<string, unknown>) =>
    request.post<ConnectorKeyRow>('/data-sources/connector-keys', data),
  rotate: (keyId: string, data: Record<string, unknown> = {}) =>
    request.post<ConnectorKeyRow>(`/data-sources/connector-keys/${encodeURIComponent(keyId)}/rotate`, data),
  revoke: (keyId: string, data: Record<string, unknown> = {}) =>
    request.post(`/data-sources/connector-keys/${encodeURIComponent(keyId)}/revoke`, data),
  getLegacyKey: () => request.get<{ api_key?: string }>('/data-sources/connector-api-key'),
  rotateLegacyKey: () => request.post<{ api_key?: string }>('/data-sources/connector-api-key/rotate'),
}

export const cookiePoolApi = {
  summary: (params?: Record<string, unknown>) =>
    request.get<{ items?: CookiePoolSummaryRow[]; total?: number; stats?: Record<string, unknown> } | CookiePoolSummaryRow[]>(
      '/data-sources/cookie-pools',
      { params },
    ),
  detail: (platform: string, shopId: string, params?: Record<string, unknown>) =>
    request.get<{
      summary?: CookiePoolSummaryRow
      credentials?: CookiePoolCredentialRow[]
      items?: CookiePoolCredentialRow[]
      audit?: unknown[]
    }>(`/data-sources/cookie-pools/${encodeURIComponent(platform)}/${encodeURIComponent(shopId)}`, { params }),
  disableCredential: (credentialId: string, data: Record<string, unknown> = {}) =>
    request.post(`/data-sources/cookie-pools/credentials/${encodeURIComponent(credentialId)}/disable`, data),
  verifyCredential: (credentialId: string) =>
    request.post(`/data-sources/cookie-pools/credentials/${encodeURIComponent(credentialId)}/verify`),
  updateCredentialPriority: (credentialId: string, priority: number) =>
    request.patch(`/data-sources/cookie-pools/credentials/${encodeURIComponent(credentialId)}/priority`, { priority }),
  audit: (params?: Record<string, unknown>) =>
    request.get('/data-sources/cookie-pools/audit', { params }),
  health: (params?: Record<string, unknown>) =>
    request.get<{ items?: CollectionHealthRow[]; total?: number; stats?: Record<string, unknown> }>(
      '/data-sources/collection-health',
      { params },
    ),
}

export const driftAlertApi = {
  list: (params?: Record<string, unknown>) =>
    request.get<{ items?: DriftAlertRow[]; total?: number }>('/data-sources/platform-api-drift-alerts', { params }),
  ack: (alertId: string, data: Record<string, unknown> = {}) =>
    request.post(`/data-sources/platform-api-drift-alerts/${encodeURIComponent(alertId)}/ack`, data),
}

export const browserSlotApi = {
  list: (params?: Record<string, unknown>) =>
    request.get<{ items?: BrowserSlotRow[]; total?: number } | BrowserSlotRow[]>('/browser/slots', { params }),
  restart: (slotId: string) => request.post(`/browser/slots/${encodeURIComponent(slotId)}/restart`),
  setMaintenance: (slotId: string, maintenance: boolean, reason?: string) =>
    request.post(`/browser/slots/${encodeURIComponent(slotId)}/maintenance`, { maintenance, reason }),
  release: (slotId: string, reason?: string) =>
    request.post(`/browser/slots/${encodeURIComponent(slotId)}/release`, { reason }),
}

export const mcpApi = {
  listServers: () => request.get<{ servers: Record<string, Record<string, unknown>>; count?: number }>('/admin/coding-agent/mcp-servers'),
  upsertServer: (name: string, data: Record<string, unknown>) =>
    request.put(`/admin/coding-agent/mcp-servers/${encodeURIComponent(name)}`, data),
  deleteServer: (name: string) => request.delete(`/admin/coding-agent/mcp-servers/${encodeURIComponent(name)}`),
  toggleServer: (name: string) => request.post(`/admin/coding-agent/mcp-servers/${encodeURIComponent(name)}/toggle`),
  testServer: (name: string) =>
    request.post<{ ok: boolean; error?: string; duration_ms?: number; tools?: string[]; tool_meta?: Record<string, unknown> }>(
      `/admin/coding-agent/mcp-servers/${encodeURIComponent(name)}/test`,
    ),
  exportServers: () =>
    request.get<{ export_type: string; exported_at: string; data: Record<string, unknown> }>(
      '/admin/coding-agent/mcp-servers/export',
    ),
  importServers: (data: Record<string, unknown>) =>
    request.post<{ message: string; total: number }>('/admin/coding-agent/mcp-servers/import', data),
}

export const runTraceApi = {
  get: (runId: string, params?: Record<string, unknown>) =>
    request.get<RunTraceResponse>(`/admin/runs/${encodeURIComponent(runId)}/trace`, { params }),
  getExecutionTrace: (runId: string, params?: Record<string, unknown>) =>
    request.get<RunTraceResponse>(`/executions/runs/${encodeURIComponent(runId)}/trace`, { params }),
  analyze: (runId: string, data?: RunTraceAnalyzeRequest) =>
    request.post<RunTraceAnalyzeResponse>(
      `/admin/runs/${encodeURIComponent(runId)}/trace/analyze`,
      data || {},
      { timeout: 180000 },
    ),
  analyzeExecutionTrace: (runId: string, data?: RunTraceAnalyzeRequest) =>
    request.post<RunTraceAnalyzeResponse>(
      `/executions/runs/${encodeURIComponent(runId)}/trace/analyze`,
      data || {},
      { timeout: 180000 },
    ),
}

// v2.7 大厅 v3
export const hallApi = {
  data: (params?: Record<string, unknown>) => request.get('/hall/data', { params }),
  dataDetail: (id: string, params?: Record<string, unknown>) => request.get(`/hall/data/${id}`, { params }),
  departments: () => request.get<{ departments?: HallDepartmentOption[] }>('/hall/departments'),
  teamList: (params?: Record<string, unknown>) => request.get('/hall/team', { params }),
  teamDetail: (dept: string) => request.get(`/hall/team/${encodeURIComponent(dept)}`),
  overview: () => request.get('/hall/overview'),
  directCapabilities: (params?: Record<string, unknown>) => request.get('/hall/direct-capabilities', { params }),
  directCapability: (id: string) => request.get(`/hall/direct-capabilities/${encodeURIComponent(id)}`),
  directCapabilityUi: (id: string, params?: Record<string, unknown>) =>
    request.get(`/hall/direct-capabilities/${encodeURIComponent(id)}/ui`, { params }),
  previewDirectCapabilityUi: (id: string, data: Record<string, unknown>) =>
    request.post(`/hall/direct-capabilities/${encodeURIComponent(id)}/ui/ai-preview`, data),
  saveDirectCapabilityUiPreference: (id: string, data: Record<string, unknown>) =>
    request.put(`/hall/direct-capabilities/${encodeURIComponent(id)}/ui/preferences`, data),
  listDirectCapabilityUiPreferences: (id: string, params?: Record<string, unknown>) =>
    request.get(`/hall/direct-capabilities/${encodeURIComponent(id)}/ui/preferences`, { params }),
  restoreDirectCapabilityUiPreference: (id: string, preferenceId: string, params?: Record<string, unknown>) =>
    request.post(
      `/hall/direct-capabilities/${encodeURIComponent(id)}/ui/preferences/${encodeURIComponent(preferenceId)}/restore`,
      null,
      { params },
    ),
  deleteDirectCapabilityUiPreference: (id: string, params?: Record<string, unknown>) =>
    request.delete(`/hall/direct-capabilities/${encodeURIComponent(id)}/ui/preferences`, { params }),
  directCapabilityImageDownloadUrl: (id: string, url: string, filename?: string) => {
    const params = new URLSearchParams({ url })
    if (filename) params.set('filename', filename)
    return `/api/hall/direct-capabilities/${encodeURIComponent(id)}/download-image?${params.toString()}`
  },
  directCapabilityImageHistory: (id: string, params?: Record<string, unknown>) =>
    request.get(`/hall/direct-capabilities/${encodeURIComponent(id)}/image-history`, { params }),
  directCapabilityArtifacts: (params?: Record<string, unknown>) =>
    request.get('/hall/direct-artifacts', { params }),
  uploadDirectCapabilityReference: (id: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request.post(`/hall/direct-capabilities/${encodeURIComponent(id)}/uploads`, fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
  },
  runDirectCapability: (id: string, data: Record<string, unknown>) =>
    request.post(`/hall/direct-capabilities/${encodeURIComponent(id)}/run`, data, { timeout: 900000 }),
  createDirectCapabilityTask: (id: string, data: Record<string, unknown>) =>
    request.post(`/hall/direct-capabilities/${encodeURIComponent(id)}/tasks`, data, { timeout: 120000 }),
  directCapabilityTasks: (id: string, params?: Record<string, unknown>) =>
    request.get(`/hall/direct-capabilities/${encodeURIComponent(id)}/tasks`, { params }),
  directCapabilityTask: (id: string, taskId: string, params?: Record<string, unknown>) =>
    request.get(`/hall/direct-capabilities/${encodeURIComponent(id)}/tasks/${encodeURIComponent(taskId)}`, { params }),
  retryDirectCapabilityTask: (id: string, taskId: string) =>
    request.post(`/hall/direct-capabilities/${encodeURIComponent(id)}/tasks/${encodeURIComponent(taskId)}/retry`),
  openDirectCapabilityTaskStream: (id: string) => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    return new WebSocket(`${proto}//${host}/api/hall/direct-capabilities/${encodeURIComponent(id)}/tasks/stream`)
  },
  aiChatModels: () => request.get('/hall/ai-chat/models', { cache: false } as any),
  aiChatThreads: (params?: Record<string, unknown>) =>
    request.get('/hall/ai-chat/threads', { params, cache: false } as any),
  createAiChatThread: (data: Record<string, unknown>) =>
    request.post('/hall/ai-chat/threads', data),
  updateAiChatThread: (threadId: string, data: Record<string, unknown>) =>
    request.patch(`/hall/ai-chat/threads/${encodeURIComponent(threadId)}`, data),
  deleteAiChatThread: (threadId: string) =>
    request.delete(`/hall/ai-chat/threads/${encodeURIComponent(threadId)}`),
  updateAiChatPreference: (data: { last_model: string }) =>
    request.patch('/hall/ai-chat/preferences', data),
  aiChatMessages: (threadId: string, params?: Record<string, unknown>) =>
    request.get(`/hall/ai-chat/threads/${encodeURIComponent(threadId)}/messages`, { params, cache: false } as any),
  uploadAiChatAttachment: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request.post('/hall/ai-chat/attachments', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
  },
  aiChatAttachmentUrl: (attachmentId: string, thumbnail = false) =>
    `/api/hall/ai-chat/attachments/${encodeURIComponent(attachmentId)}/content${thumbnail ? '?thumbnail=true' : ''}`,
  cancelAiChatMessage: (messageId: string) =>
    request.post(`/hall/ai-chat/messages/${encodeURIComponent(messageId)}/cancel`),
  // v2.7.2 按能力分类
  capabilities: (params?: Record<string, unknown>) => request.get('/hall/capabilities', { params }),
  capability: (cat: string) => request.get(`/hall/capability/${encodeURIComponent(cat)}`),
}

export const browserApi = {
  start: () => request.post('/browser/start'),
  stop: () => request.post('/browser/stop'),
  status: () => request.get('/browser/status'),
  collect: (data: Record<string, unknown>) => request.post('/browser/collect', data),
  tasks: (params?: Record<string, unknown>) => request.get('/browser/tasks', { params }),
  verifyLogin: (sourceId: string) => request.post('/browser/verify-login', { source_id: sourceId }),
  discover: (data: { url: string, platform?: string, page_title?: string, wait_ms?: number }) =>
    request.post('/browser/discover', data),
}

export const dashboardApi = {
  overview: (params?: Record<string, unknown>) => request.get('/dashboard/overview', { params }),
  adoption: (params?: Record<string, unknown>) => request.get('/dashboard/adoption', { params }),
  trends: (params?: Record<string, unknown>) => request.get('/dashboard/trends', { params }),
  impact: (params?: Record<string, unknown>) => request.get('/dashboard/impact', { params }),
  dataHealth: () => request.get('/dashboard/data-health'),
  weeklyReport: () => request.get('/dashboard/weekly-report'),
  health: () => request.get('/dashboard/health'),
  changeHeatmap: (params?: Record<string, unknown>) => request.get('/dashboard/change-heatmap', { params }),
  costsToday: () => request.get('/dashboard/costs/today'),
  costsSummary: (days = 30) => request.get('/dashboard/costs/summary', { params: { days } }),
  costsTopSkills: (limit = 10, days = 7) => request.get('/dashboard/costs/top-skills', { params: { limit, days } }),
  costsTopUsers: (limit = 10, days = 7) => request.get('/dashboard/costs/top-users', { params: { limit, days } }),
  costsByDay: (days = 14) => request.get('/dashboard/costs/by-day', { params: { days } }),
  costsReport: (params?: Record<string, unknown>) => request.get('/dashboard/costs/report', { params }),
  costsCallSources: (days = 30) => request.get('/dashboard/costs/call-sources', { params: { days } }),
  governance: () => request.get('/dashboard/governance'),
  quotas: (params?: Record<string, unknown>) => request.get('/approval/quotas', { params }),
}

export const systemApi = {
  list: () => request.get('/system-config/'),
  set: (key: string, value: unknown) => request.put(`/system-config/${key}`, { value }),
  delete: (key: string) => request.delete(`/system-config/${key}`),
  health: () => request.get('/health'),
}

export const skillBatchApi = {
  batchFork: (data: { department: string; items: Array<{ source_skill_id: string; new_skill_id: string }> }) =>
    request.post('/skills/batch-fork', data),
  batchUnpublish: (skillIds: string[]) =>
    request.post('/skills/batch-unpublish', { skill_ids: skillIds }),
  batchSetTags: (items: Array<{ skill_id: string; tags: string[] }>) =>
    request.post('/skills/batch-set-tags', { items }),
  // W4-C: export 返 zip blob；import 接受 File
  exportSkill: (skillId: string) =>
    request.get(`/skills/${skillId}/export`, { responseType: 'blob' }).then((res: any) => res?.data ?? res),
  importSkill: (
    file: File,
    options?: {
      newSkillId?: string
      department?: string
      currentVersion?: string
    },
  ) => {
    const fd = new FormData()
    fd.append('file', file)
    const params: Record<string, string> = {}
    if (options?.newSkillId) params.new_skill_id = options.newSkillId
    if (options?.department) params.department = options.department
    if (options?.currentVersion) params.current_version = options.currentVersion
    return request.post('/skills/import', fd, {
      params,
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
}

export type ProjectRuntimeInfo = {
  mode?: string
  containerized?: boolean
  gateway?: string
  sdk_path?: string
  gateway_limits?: Record<string, unknown>
}

export type ProjectCapabilityCallSummary = {
  id?: number
  request_id?: string | null
  capability_key?: string | null
  capability_type?: string | null
  status?: string | null
  output_summary?: Record<string, unknown>
  latency_ms?: number | null
  error?: string | null
  created_at?: string | null
  completed_at?: string | null
}

export type ProjectRunRow = {
  id: string
  project_id?: string
  version_id?: string | null
  execution_run_id?: string | null
  request_id?: string | null
  deduped?: boolean
  closed?: boolean
  capacity_released?: boolean
  previous_status?: string | null
  decision_log_id?: number | null
  user_id?: string | null
  department_id?: string | null
  department?: string | null
  status?: string
  liveness?: string
  heartbeat_age_seconds?: number | null
  stale_after_seconds?: number | null
  input_snapshot?: Record<string, unknown>
  output_result?: Record<string, unknown>
  ai_summary?: Record<string, unknown>
  report_cards?: Array<Record<string, unknown>>
  report_count?: number
  todo_count?: number
  capability_call_count?: number
  latest_capability_call?: ProjectCapabilityCallSummary | null
  input_event_id?: number
  error?: string | null
  created_at?: string | null
  started_at?: string | null
  last_heartbeat_at?: string | null
  completed_at?: string | null
  updated_at?: string | null
}


export type ProjectRunAssetRow = {
  id: string
  project_id?: string
  project_run_id?: string
  source_kind?: string
  file_name?: string
  mime_type?: string | null
  byte_size?: number
  sha256?: string
  metadata?: Record<string, unknown>
  download_url?: string
  created_at?: string | null
}


export type ProjectRunAssetListResponse = {
  project?: Record<string, unknown>
  run?: ProjectRunRow
  items?: ProjectRunAssetRow[]
  total?: number
}

export type ProjectRow = {
  id: string
  name?: string
  description?: string | null
  type?: string
  department_id?: string | null
  department?: string | null
  owner_user_id?: string | null
  visibility?: string
  status?: string
  entry_url?: string | null
  current_version_id?: string | null
  metadata?: Record<string, unknown>
  runtime?: ProjectRuntimeInfo
  latest_run?: ProjectRunRow | null
  runs?: ProjectRunRow[]
  permissions?: Record<string, boolean>
  created_at?: string | null
  updated_at?: string | null
}

export type ProjectListResponse = {
  items?: ProjectRow[]
  stats?: Record<string, unknown>
  pagination?: {
    page?: number
    page_size?: number
    total?: number
    has_more?: boolean
  }
  runtime_policy?: Record<string, unknown>
}

export type ProjectRunTrace = {
  project?: Record<string, unknown>
  run?: ProjectRunRow
  ingress_events?: Array<Record<string, unknown>>
  capability_calls?: Array<Record<string, unknown>>
  timeline?: Array<Record<string, unknown>>
  counts?: Record<string, number>
  pagination?: Record<string, number | boolean | string | null>
}

export type ProjectSdkTokenRow = {
  id: string
  project_id?: string
  name?: string
  token_prefix?: string
  scopes?: string[]
  status?: string
  expires_at?: string | null
  last_used_at?: string | null
  revoked_at?: string | null
  created_at?: string | null
  updated_at?: string | null
  token?: string
  token_returned_once?: boolean
}

export type ProjectSdkTokenListResponse = {
  project_id?: string
  items?: ProjectSdkTokenRow[]
}

export type ProjectResidentModelRow = {
  id?: string
  model?: string
  gateway_id?: string
  display_name?: string | null
  runtime_profile?: string | null
  deployment_id?: string | null
  job_id?: string | null
  status?: string
  loaded?: boolean
  callable?: boolean
  source?: string
  context_window?: number | null
  measured_context_window?: number | null
  reported_context_window?: number | null
  max_input_tokens?: number | null
  recommended_input_tokens?: number | null
  max_output_tokens?: number | null
  context_status?: string | null
  context_verified_at?: string | null
  safety_margin_tokens?: number | null
}

export type ProjectResidentModelCatalog = {
  object?: string
  gateway?: Record<string, unknown>
  data?: ProjectResidentModelRow[]
  models?: ProjectResidentModelRow[]
  total?: number
  callable_count?: number
  loaded_count?: number
  live_status_error?: string | null
}

export type ProjectRuntimeStatus = {
  ok?: boolean
  generated_at?: string
  scope?: Record<string, unknown>
  capacity?: Record<string, unknown>
  runs?: Record<string, unknown>
  ai?: Record<string, unknown>
  projects?: Record<string, unknown>
  gateway?: Record<string, unknown>
  recent_errors?: Array<Record<string, unknown>>
}

export type ProjectSdkCheckRunSummary = {
  ok?: boolean
  project?: Record<string, unknown>
  run?: Record<string, unknown>
  capability_calls?: Array<Record<string, unknown>>
  training_sink_jobs?: Array<Record<string, unknown>>
  counts?: Record<string, number>
}

const projectLiveReadConfig = { cache: false } as any

export const projectApi = {
  list: (params?: Record<string, unknown>) => request.get<ProjectListResponse>('/projects/', { params, ...projectLiveReadConfig } as any),
  create: (data: Record<string, unknown>) => request.post<ProjectRow>('/projects/', data),
  autoRegister: (data: Record<string, unknown>) => request.post<ProjectRow>('/projects/auto-register', data),
  uploadPackage: (form: FormData) => request.post<ProjectRow>('/projects/upload', form, { headers: { 'Content-Type': 'multipart/form-data' } }),
  runtimeStatus: (params?: Record<string, unknown>) => request.get<ProjectRuntimeStatus>('/projects/runtime/status', { params, ...projectLiveReadConfig } as any),
  models236: () => request.get<ProjectResidentModelCatalog>('/projects/models/236', projectLiveReadConfig as any),
  ensureSdkCheckProject: () => request.post<ProjectRow>('/projects/sdk/check/project'),
  sdkCheckRun: (runId: string) =>
    request.get<ProjectSdkCheckRunSummary>(`/projects/sdk/check/runs/${encodeURIComponent(runId)}`, projectLiveReadConfig as any),
  get: (id: string) => request.get<ProjectRow>(`/projects/${encodeURIComponent(id)}`),
  listSdkTokens: (id: string) =>
    request.get<ProjectSdkTokenListResponse>(`/projects/${encodeURIComponent(id)}/tokens`, projectLiveReadConfig as any),
  createSdkToken: (id: string, data: Record<string, unknown> = {}) =>
    request.post<ProjectSdkTokenRow>(`/projects/${encodeURIComponent(id)}/tokens`, data),
  revokeSdkToken: (id: string, tokenId: string) =>
    request.delete<ProjectSdkTokenRow>(`/projects/${encodeURIComponent(id)}/tokens/${encodeURIComponent(tokenId)}`),
  createRun: (id: string, data: Record<string, unknown> = {}) =>
    request.post<ProjectRunRow>(`/projects/${encodeURIComponent(id)}/runs`, data),
  getRun: (runId: string) => request.get<ProjectRunRow>(`/projects/runs/${encodeURIComponent(runId)}`),
  getRunTrace: (runId: string, params?: Record<string, unknown>) => request.get<ProjectRunTrace>(`/projects/runs/${encodeURIComponent(runId)}/trace`, { params }),
  heartbeatRun: (runId: string, data: Record<string, unknown> = {}) =>
    request.post<ProjectRunRow>(`/projects/runs/${encodeURIComponent(runId)}/heartbeat`, data),
  closeRun: (runId: string, data: Record<string, unknown> = {}) =>
    request.post<ProjectRunRow>(`/projects/runs/${encodeURIComponent(runId)}/close`, data),
  ingest: (runId: string, data: Record<string, unknown>) =>
    request.post<ProjectRunRow>(`/projects/runs/${encodeURIComponent(runId)}/ingest`, data, { timeout: 180000 }),
  recordInput: (runId: string, data: Record<string, unknown>) =>
    request.post<ProjectRunRow>(`/projects/runs/${encodeURIComponent(runId)}/input`, data),
  analyze: (runId: string, data: Record<string, unknown> = {}) =>
    request.post<Record<string, unknown>>(`/projects/runs/${encodeURIComponent(runId)}/analyze`, data, { timeout: 180000 }),
  callCapability: (runId: string, data: Record<string, unknown>) =>
    request.post<Record<string, unknown>>(`/projects/runs/${encodeURIComponent(runId)}/capability`, data, { timeout: 180000 }),
  listRunAssets: (runId: string) =>
    request.get<ProjectRunAssetListResponse>(`/projects/runs/${encodeURIComponent(runId)}/assets`),
  uploadRunAsset: (
    runId: string,
    form: FormData,
    onUploadProgress?: (event: { loaded?: number; total?: number; progress?: number }) => void,
  ) =>
    request.post<{ ok?: boolean; deduped?: boolean; asset?: ProjectRunAssetRow }>(
      `/projects/runs/${encodeURIComponent(runId)}/assets`,
      form,
      { timeout: 300000, onUploadProgress },
    ),
}

export const playbookApi = {
  list: () => request.get('/playbooks/'),
  get: (name: string) => request.get(`/playbooks/${name}`),
  save: (name: string, data: Record<string, unknown>) => request.put(`/playbooks/${name}`, data),
  create: (data: Record<string, unknown>) => request.post('/playbooks/', data),
  run: (name: string, data: Record<string, unknown>) => request.post(`/playbooks/${name}/run`, data),
  validate: (name: string) => request.post(`/playbooks/${name}/validate`),
  validateDraft: (data: Record<string, unknown>) => request.post('/playbooks/validate', data),
  publish: (name: string) => request.post(`/playbooks/${name}/publish`),
  delete: (name: string) => request.delete(`/playbooks/${name}`),
  mermaid: (name: string) => request.get(`/playbooks/${name}/mermaid`),
  dependencyGraph: (skillId?: string) => request.get('/playbooks/dependency-graph', { params: skillId ? { skill_id: skillId } : undefined }),
  templates: () => request.get('/playbooks/templates'),
  design: (goal: string) => request.post('/playbooks/design', { goal }),
}

export const portalApi = {
  listSkills: (params?: Record<string, unknown>) => request.get('/portal/skills', { params }),
  getSkill: (id: string) => request.get(`/portal/skills/${id}`),
  getSkillUi: (id: string, params?: Record<string, unknown>) => request.get(`/portal/skills/${id}/ui`, { params }),
  previewSkillUi: (id: string, data: Record<string, unknown>) => request.post(`/portal/skills/${id}/ui/ai-preview`, data),
  saveSkillUiPreference: (id: string, data: Record<string, unknown>) => request.put(`/portal/skills/${id}/ui/preferences`, data),
  listSkillUiPreferences: (id: string, params?: Record<string, unknown>) => request.get(`/portal/skills/${id}/ui/preferences`, { params }),
  restoreSkillUiPreference: (id: string, preferenceId: string, params?: Record<string, unknown>) => request.post(`/portal/skills/${id}/ui/preferences/${preferenceId}/restore`, null, { params }),
  deleteSkillUiPreference: (id: string, params?: Record<string, unknown>) => request.delete(`/portal/skills/${id}/ui/preferences`, { params }),
  submitSkill: (id: string, data: Record<string, unknown>) => request.post(`/portal/skills/${id}/submit`, data),
  listSubmissions: (params?: Record<string, unknown>) => request.get('/portal/submissions', { params }),
  getSubmission: (id: string) => request.get(`/portal/submissions/${id}`),
  getOverview: () => request.get('/portal/overview'),
  getMarket: () => request.get('/portal/market'),
}

export const orgApi = {
  getTree: () => request.get('/org/tree'),
  syncDingtalk: () => request.post('/org/sync-dingtalk'),
  createUnit: (data: Record<string, unknown>) => request.post('/org/units', data),
  getMembers: (orgId: string) => request.get(`/org/units/${orgId}/members`),
  addMembership: (data: Record<string, unknown>) => request.post('/org/memberships', data),
  removeMembership: (userId: string, orgId: string) => request.delete(`/org/memberships/${userId}/${orgId}`),
  deleteUnit: (orgId: string, force = false) => request.delete(`/org/${orgId}`, { params: { force } }),
  moveUnit: (orgId: string, newParentId: string | null) =>
    request.put(`/org/${orgId}/move`, { new_parent_id: newParentId }),
}

export const userApi = {
  list: (params?: Record<string, unknown>) => request.get('/users/', { params }),
  // L3-E 用户详情抽屉用：返回 email/phone/state/permissions/can_view_all
  getDetail: (id: string) => request.get(`/users/${encodeURIComponent(id)}/detail`),
  create: (data: Record<string, unknown>) => request.post('/users/', data),
  update: (id: string, data: Record<string, unknown>) => request.put(`/users/${id}`, data),
  updateState: (id: string, state: 'active' | 'disabled') =>
    request.post(`/users/${encodeURIComponent(id)}/state`, { state }),
  unlinkDingtalk: (id: string) =>
    request.post(`/users/${encodeURIComponent(id)}/unlink-dingtalk`),
  disable: (id: string) => request.delete(`/users/${id}`),
  resetPassword: (id: string, data: Record<string, unknown>) => request.post(`/users/${id}/reset-password`, data),
  syncDingtalk: () => request.post('/users/sync-dingtalk'),
  // v2: onboarding 激活流程（role-matrix-v2 §3.6 / §9）
  listPending: (params?: Record<string, unknown>) => request.get('/users/pending', { params }),
  activate: (userId: string, data: Record<string, unknown>) =>
    request.post(`/users/pending/${encodeURIComponent(userId)}/activate`, data),
}

// v2: 收件中心（inbox-reports-plan §5 + api-gaps GAP-2/5/6）
export const inboxApi = {
  listReports: (params?: Record<string, unknown>) =>
    request.get('/inbox/reports', { params }),
  getReport: (cardId: string, params?: { include_debug?: boolean | number }) =>
    request.get(`/inbox/reports/${encodeURIComponent(cardId)}`, { params }),
  listReportDesignTemplates: (params?: Record<string, unknown>) =>
    request.get('/inbox/report-design-templates', { params }),
  getReportDesignTemplate: (templateId: string) =>
    request.get(`/inbox/report-design-templates/${encodeURIComponent(templateId)}`),
  topSourceSkills: (params?: Record<string, unknown>) =>
    request.get('/todos/skills/top', { params }),
  // GAP-2：KPI 概览（SLA 达成率 / 积压分桶 / 近 7 天聚合）
  overview: () => request.get('/inbox/overview'),
  // GAP-5：服务端精确未读计数（取代 localStorage 兜底）
  getUnreadCount: () => request.get('/inbox/reports/unread-count'),
  // GAP-6：标记报告列表"到此已读"
  markReportsRead: (until?: string | null) =>
    request.post('/inbox/reports/mark-read', until ? { until } : {}),
}

export const auditApi = {
  query: (params?: Record<string, unknown>) => request.get('/audit/', { params }),
  stats: (params?: Record<string, unknown>) => request.get('/audit/stats', { params }),
  detailFields: () => request.get('/audit/detail-fields'),
  actions: () => request.get('/audit/actions'),
  securityEvents: (params?: Record<string, unknown>) => request.get('/audit/security-events', { params }),
  export: (params?: Record<string, unknown>) => request.get('/audit/export', { params, responseType: 'blob' }),
}

export const codexAdminApi = {
  usageSummary: (params?: Record<string, unknown>) => request.get('/codex/admin/usage-summary', { params }),
  usageEvents: (params?: Record<string, unknown>) => request.get('/codex/admin/usage-events', { params }),
  users: () => request.get('/codex/admin/users'),
}

export const sfApi = {
  overview: (params?: Record<string, unknown>) => request.get('/sf/overview', { params }),
  catalog: (params?: Record<string, unknown>) => request.get('/sf/catalog', { params }),
  trace: (params?: Record<string, unknown>) => request.get('/sf/trace', { params }),
  data: (params?: Record<string, unknown>) => request.get('/sf/data', { params }),
  dataDetail: (id: string) => request.get(`/sf/data/${encodeURIComponent(id)}`),
  writeData: (data: Record<string, unknown>) => request.post('/sf/data', data),
  dryRunMcp: (data: Record<string, unknown>) => request.post('/sf/mcp/dry-run', data),
}

export const learningApi = {
  home: (params?: Record<string, unknown>) => request.get<LearningHomeResponse>('/learning/home', { params }),
  summary: (params?: Record<string, unknown>) => request.get<LearningSummaryResponse>('/learning/summary', { params }),
  pulse: (params?: Record<string, unknown>) => request.get<LearningPulseResponse>('/learning/pulse', { params, cache: false } as any),
  pulseDrilldown: (params?: Record<string, unknown>) => request.get<LearningPulseDrilldownResponse>('/learning/pulse/drilldown', { params, cache: false } as any),
  events: (params?: Record<string, unknown>) => request.get<{ items?: LearningEventRow[]; total?: number }>('/learning/events', { params }),
  artifacts: (params?: Record<string, unknown>) => request.get<{ items?: LearningArtifactRow[]; total?: number; governance?: Record<string, unknown> }>('/learning/artifacts', { params }),
  candidates: (params?: Record<string, unknown>) => request.get<{ items?: ImprovementCandidateRow[]; total?: number }>('/learning/candidates', { params }),
  governanceTasks: (params?: Record<string, unknown>) => request.get<{ items?: LearningGovernanceTaskRow[]; total?: number }>('/learning/governance-tasks', { params }),
  updateGovernanceTaskStatus: (taskId: string, data: Record<string, unknown> = {}) =>
    request.post(`/learning/governance-tasks/${encodeURIComponent(taskId)}/status`, data),
  flowGraph: (params?: Record<string, unknown>) => request.get<LearningFlowGraphResponse>('/learning/flow-graph', { params }),
  flowTopology: (params?: Record<string, unknown>) => request.get<LearningFlowTopologyResponse>('/learning/flow-topology', { params }),
  flowJourneys: (params?: Record<string, unknown>) => request.get<LearningFlowJourneyResponse>('/learning/flow-journeys', { params }),
  bottlenecks: (params?: Record<string, unknown>) => request.get<LearningBottlenecksResponse>('/learning/bottlenecks', { params }),
  trainingManifest: (params?: Record<string, unknown>) => request.get<LearningTrainingManifestResponse>('/learning/training-manifest', { params }),
  trainingJobDataset: (jobId: string, params?: Record<string, unknown>) =>
    request.get<LearningTrainingJobDatasetResponse>(`/learning/training-jobs/${encodeURIComponent(jobId)}/dataset`, { params, cache: false } as any),
  automationStatus: (params?: Record<string, unknown>) => request.get<LearningAutomationStatusResponse>('/learning/automation-status', { params }),
  runAutomation: (data: Record<string, unknown>) => request.post('/learning/automation/run', data),
  runAgentization: (data: Record<string, unknown>) => request.post('/learning/agentization/run', data),
  runSelfAudit: (data: Record<string, unknown>) => request.post('/learning/self-audit/run', data),
  lineage: (entityType: string, entityId: string, params?: Record<string, unknown>) =>
    request.get(`/learning/entities/${encodeURIComponent(entityType)}/${encodeURIComponent(entityId)}/lineage`, { params }),
  backfill: (data: Record<string, unknown>) => request.post('/learning/events/backfill', data),
  materializeArtifact: (artifactId: string, data: Record<string, unknown> = {}) =>
    request.post(`/learning/artifacts/${encodeURIComponent(artifactId)}/materialize`, data),
  ignoreArtifact: (artifactId: string, data: Record<string, unknown> = {}) =>
    request.post(`/learning/artifacts/${encodeURIComponent(artifactId)}/ignore`, data),
  acceptCandidate: (candidateId: string, data: Record<string, unknown> = {}) =>
    request.post(`/learning/candidates/${encodeURIComponent(candidateId)}/accept`, data),
  rejectCandidate: (candidateId: string, data: Record<string, unknown> = {}) =>
    request.post(`/learning/candidates/${encodeURIComponent(candidateId)}/reject`, data),
  createCandidateReview: (candidateId: string) =>
    request.post(`/learning/candidates/${encodeURIComponent(candidateId)}/create-review`),
  createAgentDraft: (candidateId: string) =>
    request.post(`/learning/candidates/${encodeURIComponent(candidateId)}/create-agent-draft`),
  createCandidateTrainingJob: (candidateId: string, data: Record<string, unknown> = {}) =>
    request.post(`/learning/candidates/${encodeURIComponent(candidateId)}/create-training-job`, data),
}

export const changelogApi = {
  get: (limit?: number) => request.get('/changelog', { params: { limit: limit || 50 } }),
}

export const editorApi = {
  completions: (data: Record<string, unknown>) => request.post('/editor/completions', data),
  inlineEdit: (data: Record<string, unknown>) => request.post('/editor/inline-edit', data),
}

export const notificationApi = {
  list: (params?: Record<string, unknown>) => request.get('/notifications/', { params, cache: { ttl: 20, namespace: 'notifications' } } as any),
  markRead: (id: number) => request.post(`/notifications/${id}/read`),
  markAllRead: () => request.post('/notifications/read-all'),
}

export const complianceApi = {
  list: (params?: Record<string, unknown>) => request.get('/compliance/', { params }),
  get: (id: number | string) => request.get(`/compliance/${id}`),
  create: (data: Record<string, unknown>) => request.post('/compliance/', data),
  update: (id: number | string, data: Record<string, unknown>) => request.put(`/compliance/${id}`, data),
  delete: (id: number | string) => request.delete(`/compliance/${id}`),
  checkContent: (data: Record<string, unknown>) => request.post('/compliance/check', data),
  export: (params?: Record<string, unknown>) => request.get('/compliance/export', { params, responseType: 'blob' }),
  versions: (ruleId: string) => request.get(`/compliance/${ruleId}/versions`),
  rollback: (ruleId: string, versionNo: number) => request.post(`/compliance/${ruleId}/rollback/${versionNo}`),
}

export const optimizerApi = {
  createSession: (skillId: string, data: Record<string, unknown>) => request.post(`/optimizer/sessions?skill_id=${skillId}`, data),
  listSessions: (skillId: string) => request.get('/optimizer/sessions', { params: { skill_id: skillId } }),
  getSession: (id: string) => request.get(`/optimizer/sessions/${id}`),
  startSession: (id: string) => request.post(`/optimizer/sessions/${id}/start`),
  pauseSession: (id: string) => request.post(`/optimizer/sessions/${id}/pause`),
  listCandidates: (sessionId: string) => request.get(`/optimizer/sessions/${sessionId}/candidates`),
  getCandidate: (id: string) => request.get(`/optimizer/candidates/${id}`),
  promoteCandidate: (id: string) => request.post(`/optimizer/candidates/${id}/promote`),
  rejectCandidate: (id: string, reason: string) => request.post(`/optimizer/candidates/${id}/reject?reason=${encodeURIComponent(reason || '')}`),
  createBenchmarkPack: (skillId: string, data: Record<string, unknown>) => request.post(`/optimizer/benchmark-packs?skill_id=${skillId}`, data),
  getBenchmarkPack: (id: string) => request.get(`/optimizer/benchmark-packs/${id}`),
  freezeBenchmarkPack: (id: string) => request.post(`/optimizer/benchmark-packs/${id}/freeze`),
  getBenchmarkRun: (id: string) => request.get(`/optimizer/benchmark-runs/${id}`),
  getReport: (sessionId: string) => request.get(`/optimizer/sessions/${sessionId}/report`),
  checkShadow: (candId: string) => request.post(`/optimizer/candidates/${candId}/check-shadow`),
  getLiveStatus: (sessionId: string) => request.get(`/optimizer/sessions/${sessionId}/live-status`),
}
