/**
 * TaskTree 前端类型定义。
 *
 * 同步自 app/tasktree/schemas.py，任何改动两边一起动。
 * 后端对应的 pydantic 枚举：NodeStatus / SkillRunStatus / WritebackStatus。
 */

// ========================================================================
// 节点状态枚举（同步自 app/tasktree/schemas.py:NodeStatus）
// ========================================================================
export const NodeStatus = {
  ONLINE: 'online',
  MAYBE_OFFLINE: 'maybe_offline',
  OFFLINE: 'offline',
} as const

export type NodeStatusValue = typeof NodeStatus[keyof typeof NodeStatus]
// 向前兼容导出（旧代码中使用 TaskTreeNodeStatus）
export type TaskTreeNodeStatus = NodeStatusValue

// ========================================================================
// Skill 执行状态枚举（同步自 app/tasktree/schemas.py:SkillRunStatus）
// ========================================================================
export const SkillRunStatus = {
  RUNNING: 'running',
  COMPLETED: 'completed',
  FAILED: 'failed',
  IDLE: 'idle',
  QUEUED: 'queued',
} as const

export type SkillRunStatusValue = typeof SkillRunStatus[keyof typeof SkillRunStatus]
export type TaskTreeRunStatus = SkillRunStatusValue

// ========================================================================
// 回写状态枚举（同步自 app/tasktree/schemas.py:WritebackStatus）
// ========================================================================
export const WritebackStatus = {
  NONE: 'none',
  PENDING: 'pending',
  APPROVED: 'approved',
  DISPATCHED: 'dispatched',
  REJECTED: 'rejected',
} as const

export type WritebackStatusValue = typeof WritebackStatus[keyof typeof WritebackStatus]
export type TaskTreeWritebackStatus = WritebackStatusValue

// ========================================================================
// 过滤器状态（UI 专属，前端聚合枚举 + 'all'）
// ========================================================================
export const NodeStatusFilter = {
  ALL: 'all',
  ONLINE: NodeStatus.ONLINE,
  MAYBE_OFFLINE: NodeStatus.MAYBE_OFFLINE,
  OFFLINE: NodeStatus.OFFLINE,
} as const

export type NodeStatusFilterValue = typeof NodeStatusFilter[keyof typeof NodeStatusFilter]

export const TaskTreeTimeWindow = {
  ONE_HOUR: '1h',
  ONE_DAY: '24h',
  SEVEN_DAYS: '7d',
} as const

export type TaskTreeTimeWindowValue = typeof TaskTreeTimeWindow[keyof typeof TaskTreeTimeWindow]

export type TaskTreeInstanceAnomaly = 'healthy' | 'offline' | 'stuck' | 'high_failure'

export type TaskTreeDetailTab = 'detail' | 'diagnosis' | 'chain' | 'schedules' | 'value'

export interface TaskTreeSkillRunItem {
  run_id: string
  skill_id: string
  skill_name?: string
  status: string
  trigger_type?: string
  started_at?: string
  completed_at?: string
  duration_seconds?: number
  input_summary?: Record<string, unknown> | string | null
  output_summary?: Record<string, unknown> | string | null
  error_message?: string | null
  writeback?: string
  writeback_detail?: Record<string, unknown> | null
}

export interface TaskTreeInstanceSyncItem {
  job_id?: number | null
  attempt_id?: number | null
  skill_id?: string | null
  skill_name?: string | null
  version_tag?: string | null
  review_id?: number | null
  status?: string | null
  error?: string | null
  trigger?: string | null
  target_scope?: string | null
  fallback_target?: boolean
  fallback_reason?: string | null
  started_at?: string | null
  completed_at?: string | null
  created_at?: string | null
}

export interface TaskTreeTimelineBucket {
  index: number
  label: string
  state: 'empty' | 'success' | 'failed' | 'running' | 'queued'
  startedAt: string
  endedAt: string
  runs: TaskTreeSkillRunItem[]
}

export interface TaskTreeInstanceMeta {
  anomalyKind: TaskTreeInstanceAnomaly
  nodeStatus: TaskTreeNodeStatus
  windowRunCount: number
  failedInWindow: number
  successInWindow: number
  latestAnomalyRun: TaskTreeSkillRunItem | null
  stuckRun: TaskTreeSkillRunItem | null
  lastSuccessAt: string | null
  consecutiveFailures: number
  avgDurationSeconds: number | null
  buckets: TaskTreeTimelineBucket[]
}

export interface TaskTreeInstanceNode {
  instance_id: string
  name: string
  department?: string
  agent_type?: string
  runtime_type?: string
  bridge_gateway_kind?: string
  bridge_gateway_version?: string
  node_status?: string
  last_heartbeat_ago?: string
  heartbeat_ago_text?: string
  last_heartbeat_at?: string
  bridge_version?: string
  bridge_latest_version?: string
  bridge_update_available?: boolean
  bridge_platform?: string
  bridge_skills_dir?: string
  bridge_skills_dirs_json?: string | null
  last_sync_at?: string
  last_sync_ok?: boolean
  latest_sync?: TaskTreeInstanceSyncItem | null
  active_count?: number
  capacity?: number | null
  is_active?: boolean
  is_platform_node?: boolean
  machine_role?: string | null
  gpu_name?: string | null
  gpu_backend?: string | null
  gpu_unified_memory?: boolean
  gpu_memory_total_gb?: number | null
  gpu_memory_used_gb?: number | null
  memory_total_gb?: number | null
  training_available?: boolean
  media_available?: boolean
  supported_tasks?: string[]
  recent_skills?: TaskTreeSkillRunItem[]
  active_runs?: TaskTreeSkillRunItem[]
  recent_completed?: TaskTreeSkillRunItem[]
  // 由 useTaskTree.visibleTree 预计算挂载，避免每卡重复 compute。
  __meta?: TaskTreeInstanceMeta
}

export interface TaskTreeDepartmentNode {
  department_id: string
  department_name: string
  node_count?: number
  online_count?: number
  offline_count?: number
  today_executions?: number
  is_virtual?: boolean
  anomaly_count?: number
  instances?: TaskTreeInstanceNode[]
}

export interface TaskTreeResponse {
  departments?: TaskTreeDepartmentNode[]
  projected_at?: string
  etag?: string
  total_online?: number
  total_offline?: number
  total_running?: number
  today_failed?: number
  today_executions?: number
}

export interface TaskTreeStatsResponse {
  online_nodes?: number
  total_nodes?: number
  today_executions?: number
  today_failed?: number
  today_failed_rate?: number
  today_success_rate?: number
  saved_hours?: number
  token_cost_today?: number
  human_takeover_rate?: number
  department_count?: number
  instance_count?: number
  running_count?: number
  scheduled_due_soon?: number
}

export interface TaskTreeRunChainStep {
  type: string
  id: string | number
  status: string
  title?: string | null
  assignee?: string | null
  decided_at?: string | null
  created_at?: string | null
}

export interface TaskTreeTrainingJobSummaryItem {
  job_id?: string | null
  title?: string | null
  status: string
  failure_stage?: string | null
  relation?: string | null
  target_skill_id?: string | null
  target_gateway_id?: string | null
  model_family?: string | null
  training_strategy?: string | null
  training_mode?: string | null
  full_history_cycle_index?: number | null
  full_history_cycle_count?: number | null
  automation_step?: string | null
  dataset_ref?: string | null
  dataset_window_date?: string | null
  dataset_window_start?: string | null
  dataset_window_end?: string | null
  dataset_timezone?: string | null
  source_run_id?: string | null
  source_sample_count?: number
  sample_count?: number | null
  train_count?: number | null
  eval_count?: number | null
  parent_model_deployment_id?: string | null
  parent_model_family?: string | null
  parent_artifact_id?: string | null
  approved_by?: string | null
  approved_at?: string | null
  created_at?: string | null
  updated_at?: string | null
  latest_task_id?: number | null
  latest_task_status?: string | null
  latest_task_progress?: number | null
  latest_task_error?: string | null
  latest_task_updated_at?: string | null
  deployment_id?: string | null
  deployment_status?: string | null
  rollout_percent?: number | null
  artifact_id?: string | null
  artifact_sha256?: string | null
  next_training_window_date?: string | null
  planned_training_after?: string | null
}

export interface TaskTreeRunChainResponse {
  run_id: string
  skill_id: string
  skill_name?: string | null
  chain?: TaskTreeRunChainStep[]
  training_jobs?: TaskTreeTrainingJobSummaryItem[]
  chain_complete?: boolean
}

export interface TaskTreeNodeDetailResponse extends TaskTreeInstanceNode {
  active_runs?: TaskTreeSkillRunItem[]
  recent_completed?: TaskTreeSkillRunItem[]
}

export interface TaskTreeDashboardMetrics {
  totalOnline: number
  totalOffline: number
  onlineNodes: number
  totalNodes: number
  totalRunning: number
  todayFailed: number
  todayExecutions: number
  todayFailedRate: number
  todaySuccessRate: number
  savedHours: number
  tokenCostToday: number | null
  humanTakeoverRate: number
  departmentCount: number
  instanceCount: number
  runningCount: number
  scheduledDueSoon: number
  roi: number | null
}

export interface TaskTreeDashboardResponse {
  department?: string | null
  period_days?: number
  executions?: number
  saved_hours?: number
  token_cost?: number
  roi?: number | null
  top_skills?: Array<Record<string, unknown>>
}

export interface TaskTreeSkillValueResponse {
  skill_id: string
  period_days?: number
  saved_hours?: number
  estimated_cost_saving?: number
  risk_events_prevented?: number
  recommendation?: string
}

/**
 * 失败诊断响应（同步自 app/tasktree/schemas.py:FailureDiagnosisResponse）。
 *
 * ai_available: 可选字段（后端 Group B 加上）。
 *   - true / undefined：AI 诊断结果（视为默认）
 *   - false：AI 服务不可用，返回规则引擎的回退结果，UI 应提示用户。
 */
export interface TaskTreeFailureDiagnosisResponse {
  run_id: string
  diagnosis: string
  ai_available?: boolean
}
