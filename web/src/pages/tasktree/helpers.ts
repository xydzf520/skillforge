import {
  IconCheck,
  IconClockCircle,
  IconClose,
  IconPlayCircle,
} from '@arco-design/web-vue/es/icon'
import { bjtParts, formatTime, relativeTime, toDate } from '@/utils/format'
import type {
  TaskTreeDashboardResponse,
  TaskTreeDashboardMetrics,
  TaskTreeDepartmentNode,
  TaskTreeInstanceAnomaly,
  TaskTreeInstanceMeta,
  TaskTreeInstanceNode,
  TaskTreeResponse,
  TaskTreeSkillRunItem,
  TaskTreeStatsResponse,
  TaskTreeTimelineBucket,
  TaskTreeTimeWindowValue,
  TaskTreeNodeStatus,
} from './types'
import { NodeStatus, SkillRunStatus, TaskTreeTimeWindow, WritebackStatus } from './types'

// 轮询节奏：
// - tree 默认 10s
// - stats 30s
// - 连续失败 3 次后 tree 退避到 30s
export const TASKTREE_POLL_INTERVAL_SECONDS = 10
export const TASKTREE_SECONDARY_POLL_INTERVAL_SECONDS = 30
export const TASKTREE_BACKOFF_POLL_INTERVAL_SECONDS = 30
export const TASKTREE_BACKOFF_FAILURE_THRESHOLD = 3
export const TASKTREE_DEFAULT_BASELINE_MINUTES = 15
export const TASKTREE_HOURLY_VALUE_CNY = 100

export const STUCK_THRESHOLD_MS = 10 * 60 * 1000
export const HIGH_FAILURE_THRESHOLD = 0.2
export const MIN_FAILURE_SAMPLE_SIZE = 5

export const TIMELINE_BUCKETS: Record<TaskTreeTimeWindowValue, { count: number; spanMs: number }> = {
  [TaskTreeTimeWindow.ONE_HOUR]: {
    count: 60,
    spanMs: 60 * 1000,
  },
  [TaskTreeTimeWindow.ONE_DAY]: {
    count: 48,
    spanMs: 30 * 60 * 1000,
  },
  [TaskTreeTimeWindow.SEVEN_DAYS]: {
    count: 84,
    spanMs: 2 * 60 * 60 * 1000,
  },
}

export function toNumber(value: unknown, fallback = 0): number {
  const next = typeof value === 'string' ? Number(value) : Number(value ?? fallback)
  return Number.isFinite(next) ? next : fallback
}

export function formatDuration(seconds?: number | null): string {
  if (seconds == null || Number.isNaN(Number(seconds))) return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const remain = Math.round(seconds % 60)
  return `${minutes}m ${remain}s`
}

export function formatMoney(value?: number | null): string {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `¥${Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`
}

export function formatPercent(value?: number | null): string {
  if (value == null || Number.isNaN(Number(value))) return '-'
  return `${(value <= 1 ? value * 100 : value).toFixed(1)}%`
}

export function heartbeatLabel(instance: TaskTreeInstanceNode): string {
  if (instance.heartbeat_ago_text) return instance.heartbeat_ago_text
  if (instance.last_heartbeat_ago) return instance.last_heartbeat_ago
  if (!instance.last_heartbeat_at) return '-'
  return `${relativeTime(instance.last_heartbeat_at)}（${formatTime(instance.last_heartbeat_at)}）`
}

export function computeNodeStatus(instance: TaskTreeInstanceNode): TaskTreeNodeStatus {
  if (
    instance.node_status === NodeStatus.ONLINE ||
    instance.node_status === NodeStatus.MAYBE_OFFLINE ||
    instance.node_status === NodeStatus.OFFLINE
  ) {
    return instance.node_status
  }
  if (instance.is_active === false) return NodeStatus.OFFLINE
  if (!instance.last_heartbeat_at) return NodeStatus.OFFLINE
  const heartbeatAt = toDate(instance.last_heartbeat_at)
  const ageSeconds = heartbeatAt ? (Date.now() - heartbeatAt.getTime()) / 1000 : Number.NaN
  if (!Number.isFinite(ageSeconds)) return NodeStatus.OFFLINE
  if (ageSeconds > 300) return NodeStatus.OFFLINE
  if (ageSeconds > 120) return NodeStatus.MAYBE_OFFLINE
  return NodeStatus.ONLINE
}

export function nodeStatusColor(status?: string): string {
  if (status === NodeStatus.ONLINE) return 'green'
  if (status === NodeStatus.MAYBE_OFFLINE) return 'orange'
  return 'red'
}

export function nodeStatusLabel(status?: string): string {
  if (status === NodeStatus.ONLINE) return '在线'
  if (status === NodeStatus.MAYBE_OFFLINE) return '可能离线'
  if (status === NodeStatus.OFFLINE) return '离线'
  return status || '-'
}

export function runStatusColor(status?: string): string {
  if (status === SkillRunStatus.RUNNING) return 'arcoblue'
  if (status === SkillRunStatus.COMPLETED) return 'green'
  if (isFailureStatus(status)) return 'red'
  if (status === SkillRunStatus.QUEUED) return 'orange'
  return 'gray'
}

export function runStatusIcon(status?: string) {
  if (status === SkillRunStatus.RUNNING) return IconPlayCircle
  if (status === SkillRunStatus.COMPLETED) return IconCheck
  if (isFailureStatus(status)) return IconClose
  return IconClockCircle
}

export function runStatusLabel(status?: string): string {
  if (status === SkillRunStatus.RUNNING) return '运行中'
  if (status === SkillRunStatus.COMPLETED) return '已完成'
  if (status === SkillRunStatus.FAILED) return '失败'
  if (status === 'timeout') return '超时'
  if (status === 'blocked') return '阻塞'
  if (status === SkillRunStatus.QUEUED) return '排队中'
  if (status === 'stale') return '滞留'
  if (status === SkillRunStatus.IDLE) return '空闲'
  return status || '-'
}

export function writebackColor(writeback?: string): string {
  if (writeback === WritebackStatus.PENDING) return 'orange'
  if (writeback === WritebackStatus.APPROVED || writeback === WritebackStatus.DISPATCHED) return 'green'
  if (writeback === WritebackStatus.REJECTED) return 'red'
  return 'gray'
}

export function writebackText(writeback?: string): string {
  const map: Record<string, string> = {
    [WritebackStatus.NONE]: '',
    [WritebackStatus.PENDING]: '待审批',
    [WritebackStatus.APPROVED]: '已通过',
    [WritebackStatus.DISPATCHED]: '已派发',
    [WritebackStatus.REJECTED]: '已驳回',
  }
  return map[writeback || ''] || ''
}

export function normalizeRunWriteback(run: TaskTreeSkillRunItem): string {
  return run.writeback || WritebackStatus.NONE
}

export function collectDepartments(tree: TaskTreeResponse | null): TaskTreeDepartmentNode[] {
  return tree?.departments ? [...tree.departments] : []
}

export function collectInstances(tree: TaskTreeResponse | null): TaskTreeInstanceNode[] {
  return collectDepartments(tree).flatMap((department) => department.instances || [])
}

export function collectRuns(tree: TaskTreeResponse | null): TaskTreeSkillRunItem[] {
  return collectInstances(tree).flatMap((instance) => instance.recent_skills || [])
}

export function windowLabel(window: TaskTreeTimeWindowValue): string {
  if (window === TaskTreeTimeWindow.ONE_HOUR) return '1h'
  if (window === TaskTreeTimeWindow.SEVEN_DAYS) return '7d'
  return '24h'
}

export function windowDurationMs(window: TaskTreeTimeWindowValue): number {
  const meta = TIMELINE_BUCKETS[window] || TIMELINE_BUCKETS[TaskTreeTimeWindow.ONE_DAY]
  return meta.count * meta.spanMs
}

export function isFailureStatus(status?: string): boolean {
  return status === SkillRunStatus.FAILED || status === 'timeout' || status === 'blocked'
}

export function isQueuedStatus(status?: string): boolean {
  return status === SkillRunStatus.QUEUED || status === 'stale'
}

export function isInWindow(startedAt?: string, window: TaskTreeTimeWindowValue = TaskTreeTimeWindow.ONE_DAY, now = Date.now()): boolean {
  if (!startedAt) return false
  const ts = toDate(startedAt)?.getTime() ?? Number.NaN
  if (!Number.isFinite(ts)) return false
  return ts >= now - windowDurationMs(window) && ts <= now
}

export function isInRange(startedAt?: string, startMs?: number, endMs?: number): boolean {
  if (!startedAt || startMs == null || endMs == null) return false
  const ts = toDate(startedAt)?.getTime() ?? Number.NaN
  return Number.isFinite(ts) && ts >= startMs && ts < endMs
}

export function computeInstanceAnomaly(
  instance: TaskTreeInstanceNode,
  window: TaskTreeTimeWindowValue,
  now = Date.now(),
): TaskTreeInstanceAnomaly {
  const status = computeNodeStatus(instance)
  if (status === NodeStatus.OFFLINE || status === NodeStatus.MAYBE_OFFLINE) {
    return 'offline'
  }

  const runs = instance.recent_skills || []
  if (getLatestStuckRun(instance, now)) return 'stuck'

  const windowed = runs.filter((run) => isInWindow(run.started_at, window, now))
  if (windowed.length >= MIN_FAILURE_SAMPLE_SIZE) {
    const failed = windowed.filter((run) => isFailureStatus(run.status)).length
    if (failed / windowed.length > HIGH_FAILURE_THRESHOLD) {
      return 'high_failure'
    }
  }

  return 'healthy'
}

export function anomalyRank(kind: TaskTreeInstanceAnomaly): number {
  if (kind === 'offline') return 0
  if (kind === 'stuck') return 1
  if (kind === 'high_failure') return 2
  return 3
}

export function anomalyLabel(kind: TaskTreeInstanceAnomaly): string {
  if (kind === 'offline') return '离线'
  if (kind === 'stuck') return '卡住'
  if (kind === 'high_failure') return '失败率高'
  return '健康'
}

export function sortInstancesByAnomaly(
  instances: TaskTreeInstanceNode[],
  window: TaskTreeTimeWindowValue,
  now = Date.now(),
): TaskTreeInstanceNode[] {
  return [...instances].sort((left, right) => {
    const leftRank = anomalyRank(computeInstanceAnomaly(left, window, now))
    const rightRank = anomalyRank(computeInstanceAnomaly(right, window, now))
    if (leftRank !== rightRank) return leftRank - rightRank
    return (left.name || '').localeCompare(right.name || '', 'zh-CN')
  })
}

export function computeDepartmentAnomalyCount(
  department: TaskTreeDepartmentNode,
  window: TaskTreeTimeWindowValue,
  now = Date.now(),
): number {
  return (department.instances || []).filter((instance) => computeInstanceAnomaly(instance, window, now) !== 'healthy').length
}

export function clipText(value?: string | null, maxLength = 30): string {
  if (!value) return ''
  return value.length > maxLength ? `${value.slice(0, maxLength)}...` : value
}

export function getLatestStuckRun(instance: TaskTreeInstanceNode, now = Date.now()): TaskTreeSkillRunItem | null {
  const stuckRuns = (instance.recent_skills || []).filter((run) => {
    if (run.status !== SkillRunStatus.RUNNING || !run.started_at) return false
    const startedAt = toDate(run.started_at)?.getTime() ?? Number.NaN
    return Number.isFinite(startedAt) && now - startedAt > STUCK_THRESHOLD_MS
  })
  return sortRunsByStartedDesc(stuckRuns)[0] || null
}

export function getLatestAnomalyRun(instance: TaskTreeInstanceNode): TaskTreeSkillRunItem | null {
  const runs = [...(instance.recent_skills || [])]
  runs.sort((left, right) => {
    const leftTs = runEventTimestamp(left)
    const rightTs = runEventTimestamp(right)
    return rightTs - leftTs
  })
  const latestHealthyTs = runs.reduce((latest, run) => {
    const isHealthy = run.status === SkillRunStatus.COMPLETED || run.status === SkillRunStatus.RUNNING
    if (!isHealthy) return latest
    const ts = runEventTimestamp(run)
    return Number.isFinite(ts) ? Math.max(latest, ts) : latest
  }, 0)
  return runs.find((run) => {
    if (!isFailureStatus(run.status)) return false
    const ts = runEventTimestamp(run)
    return ts >= latestHealthyTs
  }) || null
}

function runEventTimestamp(run: TaskTreeSkillRunItem): number {
  const timestamp = run.completed_at || run.started_at
  return timestamp ? toDate(timestamp)?.getTime() ?? 0 : 0
}

function sortRunsByStartedDesc(runs: TaskTreeSkillRunItem[]): TaskTreeSkillRunItem[] {
  return [...runs].sort((left, right) => {
    const leftTs = left.started_at ? toDate(left.started_at)?.getTime() ?? 0 : 0
    const rightTs = right.started_at ? toDate(right.started_at)?.getTime() ?? 0 : 0
    return rightTs - leftTs
  })
}

function sortRunsByEventDesc(runs: TaskTreeSkillRunItem[]): TaskTreeSkillRunItem[] {
  return [...runs].sort((left, right) => runEventTimestamp(right) - runEventTimestamp(left))
}

export function computeInstanceMeta(
  instance: TaskTreeInstanceNode,
  window: TaskTreeTimeWindowValue,
  now = Date.now(),
): TaskTreeInstanceMeta {
  const anomalyKind = computeInstanceAnomaly(instance, window, now)
  const nodeStatus = computeNodeStatus(instance)
  const runs = instance.recent_skills || []
  const windowRuns = runs.filter((run) => isInWindow(run.started_at, window, now))
  const failedInWindow = windowRuns.filter((run) => isFailureStatus(run.status)).length
  const successInWindow = windowRuns.filter((run) => run.status === SkillRunStatus.COMPLETED).length
  const latestAnomalyRun = getLatestAnomalyRun({ ...instance, recent_skills: windowRuns })
  const stuckRun = getLatestStuckRun(instance, now)

  const sortedDesc = sortRunsByEventDesc(runs)
  const lastSuccessRun = sortedDesc.find((run) => run.status === SkillRunStatus.COMPLETED)
  const lastSuccessAt = lastSuccessRun?.completed_at || lastSuccessRun?.started_at || null

  let consecutiveFailures = 0
  for (const run of sortedDesc) {
    if (isFailureStatus(run.status)) consecutiveFailures += 1
    else if (run.status === SkillRunStatus.COMPLETED) break
  }

  const durations = windowRuns
    .map((run) => (typeof run.duration_seconds === 'number' && Number.isFinite(run.duration_seconds) ? run.duration_seconds : null))
    .filter((value): value is number => value != null && value >= 0)
  const avgDurationSeconds = durations.length
    ? durations.reduce((sum, value) => sum + value, 0) / durations.length
    : null

  const buckets = buildTimelineBuckets(runs, window, now)

  return {
    anomalyKind,
    nodeStatus,
    windowRunCount: windowRuns.length,
    failedInWindow,
    successInWindow,
    latestAnomalyRun,
    stuckRun,
    lastSuccessAt,
    consecutiveFailures,
    avgDurationSeconds,
    buckets,
  }
}

export function ensureInstanceMeta(
  instance: TaskTreeInstanceNode,
  window: TaskTreeTimeWindowValue,
  now = Date.now(),
): TaskTreeInstanceMeta {
  return instance.__meta ?? computeInstanceMeta(instance, window, now)
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function formatBucketLabel(startMs: number, window: TaskTreeTimeWindowValue): string {
  const start = bjtParts(startMs)
  if (!start) return '-'
  if (window === TaskTreeTimeWindow.SEVEN_DAYS) {
    return `${pad(start.month)}-${pad(start.day)} ${pad(start.hour)}:00`
  }
  return `${pad(start.hour)}:${pad(start.minute)}`
}

function resolveBucketState(runs: TaskTreeSkillRunItem[]): TaskTreeTimelineBucket['state'] {
  if (!runs.length) return 'empty'
  if (runs.some((run) => isFailureStatus(run.status))) return 'failed'
  if (runs.some((run) => run.status === SkillRunStatus.RUNNING)) return 'running'
  if (runs.some((run) => isQueuedStatus(run.status))) return 'queued'
  return 'success'
}

export function buildTimelineBuckets(
  runs: TaskTreeSkillRunItem[],
  window: TaskTreeTimeWindowValue,
  now = Date.now(),
): TaskTreeTimelineBucket[] {
  const meta = TIMELINE_BUCKETS[window]
  const start = now - (meta.count * meta.spanMs)
  const buckets: TaskTreeTimelineBucket[] = Array.from({ length: meta.count }, (_, index) => {
    const startedAtMs = start + (index * meta.spanMs)
    const endedAtMs = startedAtMs + meta.spanMs
    return {
      index,
      label: formatBucketLabel(startedAtMs, window),
      state: 'empty',
      startedAt: new Date(startedAtMs).toISOString(),
      endedAt: new Date(endedAtMs).toISOString(),
      runs: [],
    }
  })

  for (const run of runs || []) {
    if (!run.started_at) continue
    const ts = toDate(run.started_at)?.getTime() ?? Number.NaN
    if (!Number.isFinite(ts) || ts < start || ts > now) continue
    const rawIndex = Math.floor((ts - start) / meta.spanMs)
    const bucketIndex = Math.min(meta.count - 1, Math.max(0, rawIndex))
    buckets[bucketIndex].runs.push(run)
  }

  for (const bucket of buckets) {
    bucket.state = resolveBucketState(bucket.runs)
  }

  return buckets
}

export function timelineBucketColor(state: TaskTreeTimelineBucket['state']): string {
  // 颜色走 ai-tokens.css 设计变量；保留 --tt-bucket-* 作为最高优先级覆盖位。
  if (state === 'success') return 'var(--tt-bucket-success, var(--ai-ok))'
  if (state === 'failed') return 'var(--tt-bucket-failed, var(--ai-bad))'
  if (state === 'running') return 'var(--tt-bucket-running, var(--ai-info))'
  if (state === 'queued') return 'var(--tt-bucket-queued, var(--ai-warn))'
  return 'var(--tt-bucket-empty, var(--ai-surface-3))'
}

export function timelineBucketTooltip(bucket: TaskTreeTimelineBucket): string {
  if (!bucket.runs.length) {
    return `${bucket.label} · 暂无执行`
  }
  const failed = bucket.runs.filter((run) => isFailureStatus(run.status)).length
  const running = bucket.runs.filter((run) => run.status === SkillRunStatus.RUNNING).length
  const queued = bucket.runs.filter((run) => isQueuedStatus(run.status)).length
  const completed = bucket.runs.filter((run) => run.status === SkillRunStatus.COMPLETED).length
  const pieces = [
    `${bucket.label}`,
    `${bucket.runs.length} 次执行`,
  ]
  if (failed > 0) pieces.push(`失败 ${failed}`)
  if (running > 0) pieces.push(`运行中 ${running}`)
  if (queued > 0) pieces.push(`排队 ${queued}`)
  if (completed > 0) pieces.push(`成功 ${completed}`)
  return pieces.join(' · ')
}

function countCompletedRuns(runs: TaskTreeSkillRunItem[]): number {
  return runs.filter((run) => run.status === SkillRunStatus.COMPLETED).length
}

function countFailedRuns(runs: TaskTreeSkillRunItem[]): number {
  return runs.filter((run) => isFailureStatus(run.status)).length
}

function countTakeoverRuns(runs: TaskTreeSkillRunItem[]): number {
  const takeoverWriteback: string[] = [WritebackStatus.PENDING, WritebackStatus.REJECTED]
  return runs.filter(
    (run) => takeoverWriteback.includes(normalizeRunWriteback(run)) || isFailureStatus(run.status),
  ).length
}

export function summarizeTaskTreeMetrics(
  tree: TaskTreeResponse | null,
  stats: TaskTreeStatsResponse | null,
  dashboardData?: TaskTreeDashboardResponse | null,
): TaskTreeDashboardMetrics {
  const departments = collectDepartments(tree)
  const instances = collectInstances(tree)
  const runs = collectRuns(tree)

  const totalOnline = toNumber(stats?.online_nodes ?? tree?.total_online)
  const totalNodes = toNumber(stats?.total_nodes ?? (toNumber(tree?.total_online) + toNumber(tree?.total_offline)))
  const totalOffline = Math.max(0, totalNodes - totalOnline)
  const totalRunning = toNumber(stats?.running_count ?? tree?.total_running)
  const todayExecutions = toNumber(
    stats?.today_executions ??
    tree?.today_executions ??
    dashboardData?.executions ??
    departments.reduce((sum, department) => sum + toNumber(department.today_executions), 0),
  )
  const todayFailed = toNumber(
    stats?.today_failed ??
    tree?.today_failed ??
    countFailedRuns(runs),
  )
  const todayFailedRate = typeof stats?.today_failed_rate === 'number'
    ? stats.today_failed_rate
    : todayExecutions > 0
      ? todayFailed / todayExecutions
      : 0
  const todaySuccessRate = stats?.today_success_rate ?? (() => {
    const completed = countCompletedRuns(runs)
    const failed = countFailedRuns(runs)
    const total = completed + failed
    return total > 0 ? completed / total : 0
  })()
  const savedHours = dashboardData?.saved_hours ?? stats?.saved_hours ?? (todayExecutions * TASKTREE_DEFAULT_BASELINE_MINUTES) / 60
  const tokenCostToday = typeof dashboardData?.token_cost === 'number'
    ? dashboardData.token_cost
    : typeof stats?.token_cost_today === 'number'
      ? stats.token_cost_today
      : null
  const humanTakeoverRate = stats?.human_takeover_rate ?? (() => {
    const takeoverRuns = countTakeoverRuns(runs)
    return runs.length > 0 ? takeoverRuns / runs.length : 0
  })()
  const roi = typeof dashboardData?.roi === 'number'
    ? dashboardData.roi
    : tokenCostToday && tokenCostToday > 0
      ? (savedHours * TASKTREE_HOURLY_VALUE_CNY) / tokenCostToday
      : null

  return {
    totalOnline,
    totalOffline,
    onlineNodes: totalOnline,
    totalNodes,
    totalRunning,
    todayFailed,
    todayExecutions,
    todayFailedRate,
    todaySuccessRate,
    savedHours,
    tokenCostToday,
    humanTakeoverRate,
    departmentCount: stats?.department_count ?? departments.length,
    instanceCount: stats?.instance_count ?? instances.length,
    runningCount: totalRunning,
    scheduledDueSoon: stats?.scheduled_due_soon ?? 0,
    roi,
  }
}

export function formatNodeCapacity(instance: TaskTreeInstanceNode): string {
  if (instance.capacity == null) return '容量 -'
  const active = toNumber(instance.active_count)
  return `${active}/${instance.capacity}`
}
