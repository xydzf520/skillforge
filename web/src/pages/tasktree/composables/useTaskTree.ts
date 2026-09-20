import {
  computed,
  nextTick,
  onActivated,
  onDeactivated,
  onMounted,
  onUnmounted,
  ref,
  watch,
} from 'vue'
import { Message } from '@arco-design/web-vue'
import { useRoute, useRouter } from 'vue-router'
import { executionApi as rawExecutionApi, taskTreeApi as rawTaskTreeApi } from '@/api'
import { useUserStore } from '@/stores/user'
import {
  anomalyRank,
  computeInstanceMeta,
  summarizeTaskTreeMetrics,
  TASKTREE_BACKOFF_FAILURE_THRESHOLD,
  TASKTREE_BACKOFF_POLL_INTERVAL_SECONDS,
  TASKTREE_POLL_INTERVAL_SECONDS,
  TASKTREE_SECONDARY_POLL_INTERVAL_SECONDS,
} from '../helpers'
import {
  TaskTreeTimeWindow,
  type TaskTreeDashboardMetrics,
  type TaskTreeDashboardResponse,
  type TaskTreeDepartmentNode,
  type TaskTreeDetailTab,
  type TaskTreeFailureDiagnosisResponse,
  type TaskTreeInstanceAnomaly,
  type TaskTreeInstanceNode,
  type TaskTreeNodeDetailResponse,
  type TaskTreeResponse,
  type TaskTreeRunChainResponse,
  type TaskTreeSkillRunItem,
  type TaskTreeSkillValueResponse,
  type TaskTreeStatsResponse,
  type TaskTreeTimelineBucket,
  type TaskTreeTimeWindowValue,
} from '../types'

// taskTreeApi/executionApi 是 @/api 聚合下的动态命名空间，保留 any 以避免逐 method 定义签名
const taskTreeApi: any = rawTaskTreeApi
const executionApi: any = rawExecutionApi
const ALL_DEPARTMENTS = '__all_lv1__'

type LoadResult = { ok?: boolean; stale?: boolean; error?: unknown }
type ApiError = { _message?: string; message?: string }

function errorMessage(err: unknown, fallback: string): string {
  const e = err as ApiError | null
  return e?._message || e?.message || fallback
}

function fallbackRoute() {
  return { query: {} as Record<string, unknown> }
}

function fallbackRouter() {
  return { replace: async () => undefined }
}

function resolveRouteQueryValue(value: unknown): string {
  if (Array.isArray(value)) return String(value[0] || '')
  return typeof value === 'string' ? value : ''
}

function isSafeRouteToken(value: string): boolean {
  return /^[A-Za-z0-9._~-]+$/.test(value)
}

function routeHashToken(value: string, prefix: string): string {
  let hash = 2166136261
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return `${prefix}-${(hash >>> 0).toString(36)}`
}

function instanceRouteToken(instance: TaskTreeInstanceNode): string {
  const rawId = String(instance.instance_id || '').trim()
  if (rawId && isSafeRouteToken(rawId)) return rawId
  const source = rawId || String(instance.name || instance.department || '').trim()
  return routeHashToken(source || 'unknown', 'node')
}

function departmentApiValue(department: TaskTreeDepartmentNode): string {
  return String(department.department_id || department.department_name || '')
}

function departmentRouteToken(department: TaskTreeDepartmentNode): string {
  const rawId = String(department.department_id || '').trim()
  if (rawId && isSafeRouteToken(rawId)) return rawId
  const rawName = String(department.department_name || '').trim()
  if (rawName && isSafeRouteToken(rawName)) return rawName
  return rawName || rawId
}

function findDepartment(tree: TaskTreeResponse | null, departmentValue: string): TaskTreeDepartmentNode | null {
  if (!tree || !departmentValue) return null
  return (tree.departments || []).find((department) => (
    department.department_id === departmentValue ||
    department.department_name === departmentValue ||
    departmentRouteToken(department) === departmentValue
  )) || null
}

function departmentRouteValue(tree: TaskTreeResponse | null, departmentValue: string): string {
  if (!departmentValue || departmentValue === ALL_DEPARTMENTS) return ''
  const department = findDepartment(tree, departmentValue)
  if (department) return departmentRouteToken(department)
  return departmentValue
}

function cloneDetail(detail: TaskTreeNodeDetailResponse | null): TaskTreeNodeDetailResponse | null {
  if (!detail) return null
  return {
    ...detail,
    active_runs: [...(detail.active_runs || [])],
    recent_completed: [...(detail.recent_completed || [])],
  }
}

function findInstance(tree: TaskTreeResponse | null, instanceId: string): TaskTreeInstanceNode | null {
  if (!tree || !instanceId) return null
  const instances = (tree.departments || []).flatMap((department) => department.instances || [])
  const direct = instances.find((instance) => instance.instance_id === instanceId)
  if (direct) return direct
  const routed = instances.find((instance) => instanceRouteToken(instance) === instanceId)
  if (routed) return routed
  return null
}

function instanceRouteValue(tree: TaskTreeResponse | null, instanceId: string): string {
  if (!instanceId) return ''
  const instance = findInstance(tree, instanceId)
  if (instance) return instanceRouteToken(instance)
  if (isSafeRouteToken(instanceId)) return instanceId
  return routeHashToken(instanceId, 'node')
}

function findRun(detail: TaskTreeNodeDetailResponse | null, runId: string): TaskTreeSkillRunItem | null {
  if (!detail || !runId) return null
  return [...(detail.active_runs || []), ...(detail.recent_completed || [])].find((run) => run.run_id === runId) || null
}

export function useTaskTree() {
  // useRoute/useRouter/useUserStore 在某些测试/SSR 场景抛错，各自 fallback 的结构与正式类型不完全兼容，
  // 因此统一用 any 接住两种形态，调用方只用到了 .query / .replace / role 等字段。
  let route: any = fallbackRoute()
  let router: any = fallbackRouter()
  let userStore: any = {
    isAdmin: false,
    isEngineer: false,
    canViewAll: false,
    role: '',
    department: '',
  }

  try {
    route = useRoute()
    router = useRouter()
  } catch {}

  try {
    userStore = useUserStore()
  } catch {}

  const tree = ref<TaskTreeResponse | null>(null)
  const stats = ref<TaskTreeStatsResponse | null>(null)
  const dashboardData = ref<TaskTreeDashboardResponse | null>(null)
  const loadError = ref('')
  const treeLoading = ref(false)
  const statsLoading = ref(false)
  const detailLoading = ref(false)
  const chainLoading = ref(false)
  const refreshing = ref(false)
  const inFlight = ref(false)
  const showAnomalyOnly = ref(false)
  const window = ref<TaskTreeTimeWindowValue>(TaskTreeTimeWindow.ONE_DAY)
  const departmentFilter = ref<string>(ALL_DEPARTMENTS)
  const nameQuery = ref('')
  const compactView = ref(false)
  const etag = ref('')
  const projectedAt = ref('')
  const selectedInstanceId = ref('')
  const selectedRunId = ref('')
  const activeTab = ref<TaskTreeDetailTab>('detail')
  const selectedRange = ref<{ start: string; end: string } | null>(null)
  const selectedInstanceDetail = ref<TaskTreeNodeDetailResponse | null>(null)
  const selectedRun = ref<TaskTreeSkillRunItem | null>(null)
  const selectedChain = ref<TaskTreeRunChainResponse | null>(null)
  const runDiagnosis = ref('')
  const diagnosisAiAvailable = ref(true)
  const diagnosisLoading = ref(false)
  const skillValue = ref<TaskTreeSkillValueResponse | null>(null)
  const skillValueLoading = ref(false)
  const collapsedDepartments = ref<Record<string, boolean>>({})
  const failureCount = ref(0)

  let treeRequestId = 0
  let statsRequestId = 0
  let dashboardRequestId = 0
  let querySyncReady = false
  let treePollTimer: ReturnType<typeof setInterval> | null = null
  let secondaryPollTimer: ReturnType<typeof setInterval> | null = null
  // M5：合并 router.replace 调用，避免 selectInstance 内连写多个 ref 触发多次 replace
  let routeReplacePending = false
  let routeInitializing = false

  const canSwitchDepartment = computed(() => Boolean(userStore?.canViewAll || userStore?.isAdmin || userStore?.isEngineer))

  const currentPollIntervalSeconds = computed<number>(() => {
    if (failureCount.value >= TASKTREE_BACKOFF_FAILURE_THRESHOLD) {
      return TASKTREE_BACKOFF_POLL_INTERVAL_SECONDS
    }
    return TASKTREE_POLL_INTERVAL_SECONDS
  })

  const departmentOptions = computed(() => {
    return (tree.value?.departments || []).map((department) => ({
      label: department.department_name,
      value: departmentApiValue(department),
    }))
  })

  const visibleTree = computed<TaskTreeResponse | null>(() => {
    if (!tree.value) return null
    const now = Date.now()
    const keyword = nameQuery.value.trim().toLowerCase()
    const keywordTokens = keyword ? keyword.split(/\s+/).filter(Boolean) : []

    const departments = (tree.value.departments || [])
      .map((department) => {
        const sourceInstances = department.instances || []
        const enriched = sourceInstances.map((instance) => ({
          ...instance,
          __meta: computeInstanceMeta(instance, window.value, now),
        }))

        const anomaly_count = enriched.filter((instance) => instance.__meta.anomalyKind !== 'healthy').length

        let filtered = enriched
        if (showAnomalyOnly.value) {
          filtered = filtered.filter((instance) => instance.__meta.anomalyKind !== 'healthy')
        }
        if (keywordTokens.length) {
          filtered = filtered.filter((instance) => {
            const haystack = `${instance.name || ''} ${instance.instance_id || ''}`.toLowerCase()
            return keywordTokens.every((token) => haystack.includes(token))
          })
        }

        filtered = [...filtered].sort((left, right) => {
          const leftRank = anomalyRank(left.__meta.anomalyKind)
          const rightRank = anomalyRank(right.__meta.anomalyKind)
          if (leftRank !== rightRank) return leftRank - rightRank
          return (left.name || '').localeCompare(right.name || '', 'zh-CN')
        })

        return {
          ...department,
          anomaly_count,
          instances: filtered,
        }
      })
      .filter((department) => {
        if (showAnomalyOnly.value && (department.instances || []).length === 0) return false
        if (keywordTokens.length && (department.instances || []).length === 0) return false
        // 隐藏既无节点也无实例的空部门（比如仅在 stats 中出现的占位）。
        if ((department.node_count || 0) === 0 && (department.instances || []).length === 0) return false
        return true
      })
      .sort((left, right) => {
        const leftAnomaly = left.anomaly_count ?? 0
        const rightAnomaly = right.anomaly_count ?? 0
        if (leftAnomaly !== rightAnomaly) return rightAnomaly - leftAnomaly
        if (left.is_virtual !== right.is_virtual) return left.is_virtual ? 1 : -1
        if ((left.node_count || 0) !== (right.node_count || 0)) return (right.node_count || 0) - (left.node_count || 0)
        return (left.department_name || '').localeCompare(right.department_name || '', 'zh-CN')
      })

    return {
      ...tree.value,
      departments,
    }
  })

  const dashboard = computed<TaskTreeDashboardMetrics>(() => {
    return summarizeTaskTreeMetrics(visibleTree.value, stats.value, dashboardData.value)
  })

  // 全部异常节点扁平列表：用于顶部"异常快速导航条"。
  // 不受 showAnomalyOnly / nameQuery 过滤影响——导航条始终展示全部。
  const anomalyInstances = computed(() => {
    if (!tree.value) return [] as Array<{
      instance: TaskTreeInstanceNode
      departmentName: string
      anomalyKind: TaskTreeInstanceAnomaly
    }>
    const now = Date.now()
    const list: Array<{
      instance: TaskTreeInstanceNode
      departmentName: string
      anomalyKind: TaskTreeInstanceAnomaly
    }> = []
    for (const department of tree.value.departments || []) {
      for (const instance of department.instances || []) {
        const meta = computeInstanceMeta(instance, window.value, now)
        if (meta.anomalyKind !== 'healthy') {
          list.push({
            instance,
            departmentName: department.department_name,
            anomalyKind: meta.anomalyKind,
          })
        }
      }
    }
    list.sort((a, b) => {
      const rankA = anomalyRank(a.anomalyKind)
      const rankB = anomalyRank(b.anomalyKind)
      if (rankA !== rankB) return rankA - rankB
      return (a.instance.name || '').localeCompare(b.instance.name || '', 'zh-CN')
    })
    return list
  })

  // 下次刷新倒计时（秒）。依赖 projectedAt 作为"上次成功刷新"时间。
  const nextRefreshCountdown = ref(currentPollIntervalSeconds.value)
  const countdownTimer = ref<ReturnType<typeof setInterval> | null>(null)

  function resetCountdown() {
    nextRefreshCountdown.value = currentPollIntervalSeconds.value
  }

  // v2.0.18 L4：页面被切到后台时 setInterval 仍每秒唤醒但什么也不做（浪费）。
  // 监听 visibilitychange：hidden 时 clearInterval，回到前台重启——总调用次数减少 90%+。
  function _createCountdownInterval() {
    return setInterval(() => {
      if (refreshing.value) {
        resetCountdown()
        return
      }
      nextRefreshCountdown.value = Math.max(0, nextRefreshCountdown.value - 1)
      if (nextRefreshCountdown.value === 0) {
        resetCountdown()
      }
    }, 1000)
  }

  function _onVisibilityChange() {
    if (typeof document === 'undefined') return
    if (document.visibilityState === 'visible') {
      // 前台回来：补一次 countdown 实时值，继续计时
      if (!countdownTimer.value) {
        countdownTimer.value = _createCountdownInterval()
      }
    } else {
      // 进入后台：停表，省 CPU
      if (countdownTimer.value) {
        clearInterval(countdownTimer.value)
        countdownTimer.value = null
      }
    }
  }

  function startCountdown() {
    if (countdownTimer.value) return
    resetCountdown()
    countdownTimer.value = _createCountdownInterval()
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', _onVisibilityChange)
    }
  }

  function stopCountdown() {
    if (countdownTimer.value) {
      clearInterval(countdownTimer.value)
      countdownTimer.value = null
    }
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', _onVisibilityChange)
    }
  }

  watch(currentPollIntervalSeconds, () => {
    resetCountdown()
  })

  watch(projectedAt, () => {
    resetCountdown()
  })

  const drawerOpen = computed({
    get: () => Boolean(selectedInstanceId.value),
    set: (value: boolean) => {
      if (!value) {
        resetSelection()
      }
    },
  })

  function applyDefaultExpansion() {
    const next = { ...collapsedDepartments.value }
    for (const department of visibleTree.value?.departments || []) {
      const key = department.department_name
      if (!(key in next)) {
        // 虚拟组默认折叠；其它部门在无异常时折叠。
        next[key] = department.is_virtual ? true : (department.anomaly_count ?? 0) === 0
      }
    }
    collapsedDepartments.value = next
  }

  function isCollapsed(departmentKey: string): boolean {
    return collapsedDepartments.value[departmentKey] === true
  }

  function findDepartmentKeyForInstance(instanceId: string): string | null {
    for (const department of visibleTree.value?.departments || []) {
      if ((department.instances || []).some((instance) => instance.instance_id === instanceId)) {
        return department.department_name
      }
    }
    return null
  }

  async function focusInstanceById(instanceId: string): Promise<TaskTreeInstanceNode | null> {
    const found = findInstance(tree.value, instanceId)
    if (!found) return null
    const deptKey = findDepartmentKeyForInstance(found.instance_id)
    if (deptKey && isCollapsed(deptKey)) {
      collapsedDepartments.value = {
        ...collapsedDepartments.value,
        [deptKey]: false,
      }
    }
    await selectInstance(found)
    return found
  }

  function setNameQuery(value: string) {
    nameQuery.value = value || ''
  }

  function resetFilters() {
    nameQuery.value = ''
    showAnomalyOnly.value = false
  }

  function toggleDepartment(departmentKey: string) {
    collapsedDepartments.value = {
      ...collapsedDepartments.value,
      [departmentKey]: !collapsedDepartments.value[departmentKey],
    }
  }

  function resetSelection() {
    selectedInstanceId.value = ''
    selectedRunId.value = ''
    selectedRange.value = null
    activeTab.value = 'detail'
    selectedInstanceDetail.value = null
    selectedRun.value = null
    selectedChain.value = null
    runDiagnosis.value = ''
    diagnosisAiAvailable.value = true
    skillValue.value = null
  }

  function requestedDepartment(): string | undefined {
    if (!canSwitchDepartment.value) return undefined
    if (!departmentFilter.value || departmentFilter.value === ALL_DEPARTMENTS) return undefined
    return departmentFilter.value
  }

  function buildTreeParams(): Record<string, unknown> {
    const params: Record<string, unknown> = {}
    const department = requestedDepartment()
    if (department) params.department = department
    return params
  }

  async function loadTree(options: { refresh?: boolean } = {}) {
    const requestId = ++treeRequestId
    treeLoading.value = true
    loadError.value = ''
    try {
      const payload = await taskTreeApi.getTree(buildTreeParams(), { refresh: options.refresh }) as TaskTreeResponse
      if (requestId !== treeRequestId) return { stale: true }

      tree.value = {
        ...payload,
        departments: payload.departments || [],
      }
      etag.value = payload.etag || ''
      projectedAt.value = payload.projected_at || ''
      applyDefaultExpansion()

      if (selectedInstanceId.value && !findInstance(tree.value, selectedInstanceId.value)) {
        resetSelection()
      }

      return { ok: true } as LoadResult
    } catch (error) {
      if (requestId !== treeRequestId) return { stale: true } as LoadResult
      loadError.value = errorMessage(error, '加载任务树失败')
      return { ok: false, error } as LoadResult
    } finally {
      if (requestId === treeRequestId) treeLoading.value = false
    }
  }

  async function loadStats(options: { refresh?: boolean } = {}) {
    const requestId = ++statsRequestId
    statsLoading.value = true
    try {
      const params: Record<string, unknown> = { window: window.value }
      const department = requestedDepartment()
      if (department) params.department = department
      const data = await taskTreeApi.getStats(params, { refresh: options.refresh })
      if (requestId !== statsRequestId) return { stale: true }
      stats.value = data || {}
      return { ok: true }
    } catch (error) {
      if (requestId !== statsRequestId) return { stale: true }
      console.warn('[TaskTree] loadStats failed', error)
      return { ok: false, error }
    } finally {
      if (requestId === statsRequestId) statsLoading.value = false
    }
  }

  async function loadDashboard(options: { refresh?: boolean } = {}) {
    const requestId = ++dashboardRequestId
    try {
      const params: Record<string, unknown> = { period_days: 30 }
      const department = requestedDepartment()
      if (department) params.department = department
      const data = await taskTreeApi.getDashboard(params, { refresh: options.refresh })
      if (requestId !== dashboardRequestId) return { stale: true }
      dashboardData.value = data || null
      return { ok: true }
    } catch (error) {
      if (requestId !== dashboardRequestId) return { stale: true }
      console.warn('[TaskTree] loadDashboard failed', error)
      dashboardData.value = null
      return { ok: false, error }
    }
  }

  async function refreshAll(options: { refresh?: boolean } = {}) {
    if (inFlight.value || refreshing.value) return
    inFlight.value = true
    refreshing.value = true
    try {
      const results = (await Promise.all([
        loadTree({ refresh: options.refresh }),
        loadStats({ refresh: options.refresh }),
        loadDashboard({ refresh: options.refresh }),
      ])) as LoadResult[]
      const realResults = results.filter((item) => !item?.stale)
      if (!realResults.length) return
      const anySuccess = realResults.some((item) => item?.ok === true)
      const anyFailure = realResults.some((item) => item?.ok === false)
      if (anySuccess) {
        const wasBackedOff = failureCount.value >= TASKTREE_BACKOFF_FAILURE_THRESHOLD
        failureCount.value = 0
        if (wasBackedOff) restartTreePolling()
      } else if (anyFailure) {
        failureCount.value += 1
        if (failureCount.value === TASKTREE_BACKOFF_FAILURE_THRESHOLD) {
          restartTreePolling()
        }
      }
    } finally {
      refreshing.value = false
      inFlight.value = false
    }
  }

  async function refreshTreeTick() {
    const result = (await loadTree()) as LoadResult | undefined
    if (!result || result.stale) return
    if (result.ok) {
      const wasBackedOff = failureCount.value >= TASKTREE_BACKOFF_FAILURE_THRESHOLD
      failureCount.value = 0
      if (wasBackedOff) restartTreePolling()
    } else {
      failureCount.value += 1
      if (failureCount.value === TASKTREE_BACKOFF_FAILURE_THRESHOLD) {
        restartTreePolling()
      }
    }
  }

  async function loadSkillValue(skillId: string) {
    skillValueLoading.value = true
    try {
      skillValue.value = await taskTreeApi.getSkillValue(skillId, { period_days: 30 }) || null
    } catch (error) {
      console.warn('[TaskTree] loadSkillValue failed', error)
      skillValue.value = null
    } finally {
      skillValueLoading.value = false
    }
  }

  // v2.0.18 M5：快速切换节点时旧响应会覆盖新选中
  // 用 reqId 守卫（简单且避免牵扯 axios cancel token 框架）
  let detailReqId = 0

  async function selectInstance(instance: TaskTreeInstanceNode) {
    const thisReqId = ++detailReqId
    selectedInstanceId.value = instance.instance_id
    selectedChain.value = null
    selectedRange.value = null

    // 手风琴：折叠其他组，展开当前节点所在组
    const deptKey = findDepartmentKeyForInstance(instance.instance_id)
    if (deptKey) {
      const next: Record<string, boolean> = {}
      for (const dept of visibleTree.value?.departments || []) {
        next[dept.department_name] = dept.department_name !== deptKey
      }
      collapsedDepartments.value = next
    }
    if (!(selectedRunId.value && activeTab.value === 'chain')) {
      activeTab.value = 'schedules'
    }
    detailLoading.value = true
    try {
      const detail = await taskTreeApi.getNodeDetail(instance.instance_id) as TaskTreeNodeDetailResponse
      if (thisReqId !== detailReqId) {
        // 用户已切换到其他节点，本次响应作废——不覆盖当前 state
        return null
      }
      selectedInstanceDetail.value = {
        ...instance,
        ...detail,
        department: instance.department || detail.department,
        active_runs: detail.active_runs || [],
        recent_completed: detail.recent_completed || [],
      }
      const firstRun =
        selectedRun.value && findRun(selectedInstanceDetail.value, selectedRun.value.run_id)
          ? findRun(selectedInstanceDetail.value, selectedRun.value.run_id)
          : selectedInstanceDetail.value.active_runs?.[0] || selectedInstanceDetail.value.recent_completed?.[0] || null
      selectedRun.value = firstRun
      selectedRunId.value = firstRun?.run_id || ''
      if (firstRun?.skill_id) void loadSkillValue(firstRun.skill_id)
      if (firstRun?.run_id) void loadRunChainSummary(firstRun.run_id)
      return selectedInstanceDetail.value
    } catch (error) {
      if (thisReqId !== detailReqId) return null
      selectedInstanceDetail.value = {
        ...instance,
        active_runs: [],
        recent_completed: [],
      }
      selectedRun.value = null
      selectedRunId.value = ''
      loadError.value = errorMessage(error, '加载节点详情失败')
      return selectedInstanceDetail.value
    } finally {
      if (thisReqId === detailReqId) {
        detailLoading.value = false
      }
    }
  }

  async function selectRun(instance: TaskTreeInstanceNode | TaskTreeNodeDetailResponse, run: TaskTreeSkillRunItem) {
    if (selectedInstanceId.value !== instance.instance_id || !selectedInstanceDetail.value) {
      await selectInstance(instance)
    }
    selectedRunId.value = run.run_id
    runDiagnosis.value = ''
    diagnosisAiAvailable.value = true
    selectedRun.value = findRun(selectedInstanceDetail.value, run.run_id) || run
    if (selectedRun.value?.skill_id) {
      void loadSkillValue(selectedRun.value.skill_id)
    }
    void loadRunChainSummary(run.run_id)
  }

  async function loadRunChainSummary(runId: string) {
    if (!runId) return
    selectedRunId.value = runId
    chainLoading.value = true
    try {
      const data = await taskTreeApi.getRunChain(runId) as TaskTreeRunChainResponse
      selectedChain.value = data
      const matched = findRun(selectedInstanceDetail.value, runId)
      if (matched) selectedRun.value = matched
    } catch (error) {
      loadError.value = errorMessage(error, '加载回写链路失败')
    } finally {
      chainLoading.value = false
    }
  }

  async function showRunChain(runId: string) {
    await loadRunChainSummary(runId)
  }

  async function diagnoseRun(runId: string) {
    diagnosisLoading.value = true
    try {
      const payload = await taskTreeApi.diagnoseRun(runId) as TaskTreeFailureDiagnosisResponse
      runDiagnosis.value = payload?.diagnosis || ''
      diagnosisAiAvailable.value = payload?.ai_available !== false
    } catch (error) {
      loadError.value = errorMessage(error, '加载智能诊断失败')
      runDiagnosis.value = ''
      diagnosisAiAvailable.value = true
    } finally {
      diagnosisLoading.value = false
    }
  }

  async function retrySkill(skillId: string) {
    try {
      await executionApi.run({ skill_id: skillId })
      Message.success('已触发重试')
      await refreshAll()
    } catch (error) {
      loadError.value = errorMessage(error, '触发重试失败')
    }
  }

  async function openDiagnosis(instance: TaskTreeInstanceNode, run: TaskTreeSkillRunItem) {
    await selectRun(instance, run)
    activeTab.value = 'diagnosis'
  }

  async function focusBucket(instance: TaskTreeInstanceNode, bucket: TaskTreeTimelineBucket) {
    await selectInstance(instance)
    selectedRange.value = { start: bucket.startedAt, end: bucket.endedAt }
    activeTab.value = 'chain'
    if (bucket.runs?.length) {
      const run = bucket.runs[0]
      await selectRun(instance, run)
    }
  }

  function clearRunFilter() {
    selectedRange.value = null
  }

  function startTreePolling() {
    stopTreePolling()
    treePollTimer = setInterval(() => {
      if (typeof document === 'undefined' || document.visibilityState === 'visible') {
        void refreshTreeTick()
      }
    }, currentPollIntervalSeconds.value * 1000)
  }

  function stopTreePolling() {
    if (treePollTimer) {
      clearInterval(treePollTimer)
      treePollTimer = null
    }
  }

  function restartTreePolling() {
    if (treePollTimer) startTreePolling()
  }

  function startSecondaryPolling() {
    stopSecondaryPolling()
    secondaryPollTimer = setInterval(() => {
      if (typeof document === 'undefined' || document.visibilityState === 'visible') {
        void loadStats()
        void loadDashboard()
      }
    }, TASKTREE_SECONDARY_POLL_INTERVAL_SECONDS * 1000)
  }

  function stopSecondaryPolling() {
    if (secondaryPollTimer) {
      clearInterval(secondaryPollTimer)
      secondaryPollTimer = null
    }
  }

  function buildRouteQuery(): Record<string, string> {
    const nextQuery: Record<string, string> = {}
    if (window.value !== TaskTreeTimeWindow.ONE_DAY) nextQuery.window = window.value
    nextQuery.anomaly = showAnomalyOnly.value ? '1' : '0'
    if (canSwitchDepartment.value && departmentFilter.value && departmentFilter.value !== ALL_DEPARTMENTS) {
      nextQuery.department = departmentRouteValue(tree.value, departmentFilter.value)
    }
    if (selectedInstanceId.value) nextQuery.inst = instanceRouteValue(tree.value, selectedInstanceId.value)
    if (activeTab.value !== 'detail') nextQuery.tab = activeTab.value
    if (selectedRunId.value) nextQuery.run = selectedRunId.value
    return nextQuery
  }

  function commitRouteUpdate() {
    if (!querySyncReady || !router || typeof router.replace !== 'function') return
    const nextQuery = buildRouteQuery()

    const current = {
      window: resolveRouteQueryValue(route.query?.window),
      anomaly: resolveRouteQueryValue(route.query?.anomaly),
      department: resolveRouteQueryValue(route.query?.department),
      inst: resolveRouteQueryValue(route.query?.inst),
      tab: resolveRouteQueryValue(route.query?.tab),
      run: resolveRouteQueryValue(route.query?.run),
    }

    if (
      current.window === (nextQuery.window || '') &&
      current.anomaly === (nextQuery.anomaly || '') &&
      current.department === (nextQuery.department || '') &&
      current.inst === (nextQuery.inst || '') &&
      current.tab === (nextQuery.tab || '') &&
      current.run === (nextQuery.run || '')
    ) {
      return
    }

    void router.replace({
      query: nextQuery,
    })
  }

  // M5：合并多次 ref 写入触发的 router.replace。selectInstance 内会同步 set
  // selectedInstanceId / activeTab / 后续 selectedRunId，原本会触发 1~3 次 replace。
  // 用 pending flag + nextTick 让一拍内的所有改动只调一次 replace。
  function syncQuery() {
    if (!querySyncReady) return
    if (routeReplacePending) return
    routeReplacePending = true
    void nextTick(() => {
      routeReplacePending = false
      commitRouteUpdate()
    })
  }

  function initializeFromQuery() {
    routeInitializing = true
    const queryWindow = resolveRouteQueryValue(route.query?.window)
    const queryDepartment = resolveRouteQueryValue(route.query?.department)
    const queryTab = resolveRouteQueryValue(route.query?.tab)

    try {
      if (queryWindow === TaskTreeTimeWindow.ONE_HOUR || queryWindow === TaskTreeTimeWindow.ONE_DAY || queryWindow === TaskTreeTimeWindow.SEVEN_DAYS) {
        window.value = queryWindow
      }
      const queryAnomaly = resolveRouteQueryValue(route.query?.anomaly)
      showAnomalyOnly.value = queryAnomaly ? queryAnomaly === '1' : false
      if (canSwitchDepartment.value && queryDepartment) {
        departmentFilter.value = queryDepartment
      }
      selectedInstanceId.value = resolveRouteQueryValue(route.query?.inst)
      selectedRunId.value = resolveRouteQueryValue(route.query?.run)
      if (queryTab === 'diagnosis' || queryTab === 'chain' || queryTab === 'schedules' || queryTab === 'value' || queryTab === 'detail') {
        activeTab.value = queryTab
      } else if (selectedRunId.value) {
        activeTab.value = 'chain'
      }
    } finally {
      void nextTick(() => {
        routeInitializing = false
      })
    }
  }

  async function resolveInitialSelection() {
    if (!selectedInstanceId.value || !tree.value) return
    const requestedRunId = selectedRunId.value
    const requestedTab = resolveRouteQueryValue(route.query?.tab)
    const instance = findInstance(tree.value, selectedInstanceId.value)
    if (!instance) {
      resetSelection()
      return
    }
    await selectInstance(instance)
    if (requestedTab === 'diagnosis' || requestedTab === 'chain' || requestedTab === 'schedules' || requestedTab === 'value' || requestedTab === 'detail') {
      activeTab.value = requestedTab
    }
    if (requestedRunId) {
      const matched = findRun(selectedInstanceDetail.value, requestedRunId)
      if (matched) {
        selectedRunId.value = requestedRunId
        selectedRun.value = matched
        if (!requestedTab) activeTab.value = 'chain'
        void loadRunChainSummary(requestedRunId)
      } else {
        // run UUID 过期/不匹配，清除避免下游 tab 展示空数据
        selectedRunId.value = ''
        selectedRun.value = null
      }
    }
    syncQuery()
  }

  watch(departmentFilter, () => {
    if (routeInitializing) return
    treeRequestId += 1
    statsRequestId += 1
    dashboardRequestId += 1
    inFlight.value = false
    refreshing.value = false
    etag.value = ''
    resetSelection()
    void refreshAll()
  })

  watch(window, () => {
    etag.value = ''
    selectedRange.value = null
    applyDefaultExpansion()
    void refreshAll()
  })

  watch(showAnomalyOnly, () => {
    applyDefaultExpansion()
  })

  watch(
    () => tree.value,
    async () => {
      applyDefaultExpansion()
      if (selectedInstanceId.value && !selectedInstanceDetail.value) {
        await resolveInitialSelection()
      }
    },
    { deep: false },
  )

  watch([window, showAnomalyOnly, departmentFilter, selectedInstanceId, selectedRunId, activeTab, nameQuery], () => {
    syncQuery()
  })

  watch(compactView, (value) => {
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem('tasktree:compact', value ? '1' : '0')
      }
    } catch {}
  })

  // 初始化：从 localStorage 恢复紧凑视图
  try {
    if (typeof localStorage !== 'undefined') {
      compactView.value = localStorage.getItem('tasktree:compact') === '1'
    }
  } catch {}

  onMounted(async () => {
    initializeFromQuery()
    querySyncReady = true
    await refreshAll()
    syncQuery()
    await resolveInitialSelection()
    startTreePolling()
    startSecondaryPolling()
    startCountdown()
  })

  onActivated(() => {
    startTreePolling()
    startSecondaryPolling()
    startCountdown()
  })

  onDeactivated(() => {
    stopTreePolling()
    stopSecondaryPolling()
    stopCountdown()
  })

  onUnmounted(() => {
    stopTreePolling()
    stopSecondaryPolling()
    stopCountdown()
  })

  return {
    activeTab,
    anomalyInstances,
    canSwitchDepartment,
    chainLoading,
    clearRunFilter,
    collapsedDepartments,
    compactView,
    currentPollIntervalSeconds,
    dashboard,
    dashboardData,
    deptOverride: departmentFilter,
    departmentFilter,
    departmentOptions,
    focusInstanceById,
    nameQuery,
    nextRefreshCountdown,
    resetFilters,
    setNameQuery,
    detailLoading,
    diagnosisAiAvailable,
    diagnosisLoading,
    drawerOpen,
    etag,
    failureCount,
    focusBucket,
    inFlight,
    isCollapsed,
    loadError,
    openDiagnosis,
    projectedAt,
    refreshAll,
    refreshing,
    retrySkill,
    runDiagnosis,
    selectedChain,
    selectedInstanceDetail,
    selectedInstanceId,
    selectedRange,
    selectedRun,
    selectedRunId,
    selectInstance,
    selectRun,
    showAnomalyOnly,
    showRunChain,
    skillValue,
    skillValueLoading,
    stats,
    statsLoading,
    toggleDepartment,
    tree,
    treeLoading,
    visibleTree,
    window,
    diagnoseRun,
  }
}
