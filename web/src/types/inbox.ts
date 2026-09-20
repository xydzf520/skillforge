export type InboxTabKey = 'pending' | 'reports' | 'drift'

export type ReportChannel = 'dingtalk_card' | 'dingtalk_markdown' | 'email' | 'feishu' | string
export type ReportTriggerType = 'cron' | 'manual' | 'event' | string
export type ReportMetricTrend = 'up' | 'down' | 'flat' | string

export interface ReportMetric {
  label: string
  value: string
  trend?: ReportMetricTrend | null
  delta?: string | null
}

export interface ReportDebugContext {
  raw_output_result?: Record<string, unknown> | null
  input_snapshot?: Record<string, unknown> | null
}

export interface RelatedReportSummary {
  id: string
  decision_log_id: number
  title: string
  created_at: string
  summary?: string | null
}

export interface RelatedTodoSummary {
  id: number
  request_id: string
  kind: 'review' | 'dispatch' | string
  title: string
  aggregate_status?: string | null
  sla_at?: string | null
  assignees?: string[]
}

export interface ReportListParams {
  page?: number
  page_size?: number
  skill_id?: string
  date_from?: string
  date_to?: string
  tag?: string
  channel?: string
  trigger_type?: string
}

export interface ReportDesignMeta {
  template_id?: string
  id?: string
  name?: string
  source_type?: 'design_system' | 'skill' | string
  category?: string
  source?: string
  license?: string
  upstream?: string
  design_standard?: Record<string, unknown> | null
}

export interface ReportDesignTemplate {
  id: string
  source_type: 'design_system' | 'skill' | string
  name: string
  slug?: string | null
  category?: string | null
  summary?: string | null
  tags?: string[]
  license?: string | null
  upstream?: string | null
  preview?: {
    colors?: string[]
    prompt?: string
    layout?: string[]
  }
}

export interface ReportListItem {
  id: string
  decision_log_id: number
  report_index: number
  run_id?: string | null
  skill_id: string
  skill_name?: string | null
  skill_department?: string | null
  channel?: ReportChannel | null
  title: string
  summary: string
  metrics: ReportMetric[]
  tags: string[]
  trigger_type?: ReportTriggerType | null
  related_todo_count: number
  related_pending_request_count: number
  created_at: string
  report_design?: ReportDesignMeta | null
}

export interface ReportListResponse {
  total: number
  page: number
  page_size: number
  items: ReportListItem[]
}

export interface ReportDetailResponse extends ReportListItem {
  content_markdown?: string | null
  payload?: Record<string, unknown> | null
  debug_context?: ReportDebugContext | null
  related_todos: RelatedTodoSummary[]
}

export type InboxTodoKind = 'review' | 'dispatch' | string
export type InboxTodoStatus = 'pending' | 'approved' | 'rejected' | 'expired' | 'resolved_by_peer' | 'done' | string
export type InboxDecisionMode = 'any_of' | 'all_of' | 'independent' | string

export interface MetricPreview extends ReportMetric {}

export interface AggregateProgress {
  total: number
  done: number
  waiting_on: string[]
}

export interface RequesterBrief {
  id: string
  name?: string | null
}

export interface InboxDispatchCompletion {
  task_id: number
  executor?: string | null
  executor_name?: string | null
  content?: string | null
  ack_at?: string | null
  ack_note?: string | null
  ack_channel?: string | null
}

export interface DispatchDefaultSummary {
  executor_ids: string[]
  executor_names: string[]
  missing_count: number
  total_count: number
}

export interface InboxTodoListItem {
  id: number
  request_id: string
  kind: InboxTodoKind
  assignee?: string | null
  status: InboxTodoStatus
  title: string
  summary?: string | null
  skill_id?: string | null
  run_id?: string | null
  decision_mode?: InboxDecisionMode | null
  aggregate_status?: string | null
  aggregate_decision?: string | null
  decided_at?: string | null
  decided_by?: string | null
  decision_reason?: string | null
  created_at?: string | null
  updated_at?: string | null
  sla_at?: string | null
  related_report?: RelatedReportSummary | null
  // GAP-1 rich list 字段（后端 v2.0.13+ 返回，旧前端忽略即可）
  suggested_actions?: string[] | null
  metrics_preview?: MetricPreview[] | null
  priority?: string | null
  priority_amount?: number | string | null
  priority_basis?: string | null
  priority_basis_source?: string | null
  approval_level?: string | null
  requester?: RequesterBrief | null
  requester_department?: string | null
  aggregate_progress?: AggregateProgress | null
  dispatch_done_count?: number | null
  latest_dispatch_completion?: InboxDispatchCompletion | null
  dispatch_completions?: InboxDispatchCompletion[] | null
  dispatch_default_summary?: DispatchDefaultSummary | null
  object_id?: string | null
  object_title?: string | null
  search_text?: string | null
  ranking_visitors?: number | string | null
  ranking_orders?: number | string | null
  ranking_visitors_detail?: string | null
  ranking_orders_detail?: string | null
  payment_change_pct?: number | string | null
  payment_change_text?: string | null
  decline_coef?: number | string | null
  post_training_model_evaluation?: PostTrainingModelEvaluationSummary | null
}

export interface InboxTodoStats {
  pending?: number
  dispatch_pending?: number
  feedback_done?: number
  approved?: number
  rejected?: number
  expired?: number
  resolved_by_peer?: number
  done?: number
  due_soon?: number
  overdue?: number
  today_new?: number
  today_by_priority?: Record<string, number>
  pending_by_priority?: Record<string, number>
}

export interface InboxTodoListResponse {
  total: number
  items: InboxTodoListItem[]
}

// GAP-2：/api/inbox/overview
export interface BacklogBucket {
  bucket: '<1h' | '1-6h' | '6-24h' | '>24h' | string
  count: number
}

export interface InboxOverviewResponse {
  pending: number
  overdue: number
  resolved_today: number
  sla_hit_rate: number // 0 ~ 1
  avg_resolve_minutes: number
  backlog_by_age: BacklogBucket[]
  resolved_last_7d: number
  approved_last_7d: number
  rejected_last_7d: number
}

export interface InboxTodoRecord {
  id: number
  request_id: string
  kind: InboxTodoKind
  assignee?: string | null
  status: InboxTodoStatus
  decided_at?: string | null
  decided_by?: string | null
  decision_reason?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface InboxTodoRequestRecord {
  id: string
  kind: InboxTodoKind
  source_type?: string | null
  source_id?: string | null
  skill_id?: string | null
  run_id?: string | null
  title: string
  summary?: string | null
  decision_mode?: InboxDecisionMode | null
  aggregate_status?: string | null
  aggregate_decision?: string | null
  sla_at?: string | null
  created_at?: string | null
  completed_at?: string | null
  has_callback?: boolean
  callback_status?: string | null
}

export interface InboxSkillMeta {
  id: string
  name: string
  department?: string | null
  approval_level?: string | null
  owner?: string | null
  status?: string | null
}

export interface InboxDispatchTask {
  id: number
  request_id: string
  manager_todo_id?: number | null
  executor?: string | null
  executor_name?: string | null
  executor_department?: string | null
  default_executor?: string | null
  default_executor_name?: string | null
  default_executor_department?: string | null
  executor_source?: 'assigned' | 'default' | 'single_assignable' | 'none' | string
  dispatch_object_key?: string | null
  content: string
  display_content?: string | null
  deadline?: string | null
  extra?: Record<string, unknown> | null
  status: string
  assigned_by?: string | null
  assigned_at?: string | null
  dingtalk_msg_id?: string | null
  dispatched_at?: string | null
  ack_at?: string | null
  ack_note?: string | null
  ack_channel?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface InboxTodoStructuredContext {
  input_params?: Record<string, unknown>
  output_summary?: string | Record<string, unknown> | null
  decision_reasoning?: string | string[] | null
  suggested_actions?: string[]
}

export interface InboxDecisionChain {
  log_id: number
  input_snapshot?: Record<string, unknown> | null
  output_result?: Record<string, unknown> | null
  approval_level?: string | null
  created_at?: string | null
}

export interface PostTrainingModelEvaluationSummary {
  status?: 'used' | 'skipped' | 'failed' | string | null
  model_deployment_id?: string | null
  model_family?: string | null
  evaluated_at?: string | null
  reason?: string | null
}

export interface PostTrainingModelEvaluation extends PostTrainingModelEvaluationSummary {
  schema?: string | null
  skill_id?: string | null
  run_id?: string | null
  deployment_status?: string | null
  model_scope?: 'target_skill_deployment' | 'shared_platform_history_fallback' | string | null
  artifact_id?: string | null
  artifact_sha256?: string | null
  text?: string | null
  text_sha256?: string | null
  gateway_id?: string | null
  gateway_kind?: string | null
  model_profile?: string | null
  metrics?: Record<string, unknown> | null
  prompt_sha256?: string | null
  finish_reason?: string | null
  error_code?: string | null
  error?: string | null
}

export interface InboxTodoDetailResponse {
  todo?: InboxTodoRecord | null
  request?: InboxTodoRequestRecord | null
  skill_meta?: InboxSkillMeta | null
  payload?: Record<string, unknown> | null
  structured?: InboxTodoStructuredContext | null
  decision_chain?: InboxDecisionChain | null
  dispatch_tasks?: InboxDispatchTask[]
  related_report?: RelatedReportSummary | null
  post_training_model_evaluation?: PostTrainingModelEvaluation | null
}
