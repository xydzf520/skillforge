import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { useUserStore } from './user'

export type TaskKind = 'sandbox' | 'test' | 'execute' | 'creation' | 'validate' | 'chat'
export type TaskStatus = 'running' | 'success' | 'error' | 'blocked'

export interface RunningTask {
  id: string
  kind: TaskKind
  title: string
  subtitle?: string
  skillId?: string
  skillName?: string
  startedAt: number
  finishedAt?: number
  status: TaskStatus
  message?: string
  returnPath: string
  runId?: string
  refKey?: string  // 用于跨组件复用（如 draftId、chat sessionId），同 refKey 的任务视为同一任务
}

interface CachedResult {
  result: any
  at: number
}

type ResultCache = Record<string, Partial<Record<TaskKind, CachedResult>>>

const AUTO_DISMISS_MS = 30_000
const MAX_TASKS = 12
const CACHE_TTL_MS = 30 * 60_000
// 按 userId 分片 sessionStorage key，避免切账号看到前一个用户的任务（越权风险）。
// userId 未就绪时退化为 'anon'，首次 hydrate 完成后 watcher 会切到真实 userId 重新加载。
const STORAGE_TASKS_PREFIX = 'sf-running-tasks:'
const STORAGE_RESULTS_PREFIX = 'sf-task-results:'

function tasksKey(userId: string): string {
  return `${STORAGE_TASKS_PREFIX}${userId || 'anon'}`
}

function resultsKey(userId: string): string {
  return `${STORAGE_RESULTS_PREFIX}${userId || 'anon'}`
}

function genId() {
  return `t-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

function loadTasks(userId: string): RunningTask[] {
  try {
    const raw = sessionStorage.getItem(tasksKey(userId))
    if (!raw) return []
    const parsed = JSON.parse(raw) as RunningTask[]
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function loadResults(userId: string): ResultCache {
  try {
    const raw = sessionStorage.getItem(resultsKey(userId))
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return (parsed && typeof parsed === 'object') ? parsed : {}
  } catch {
    return {}
  }
}

function pruneResults(cache: ResultCache): ResultCache {
  const now = Date.now()
  const out: ResultCache = {}
  for (const [skillId, byKind] of Object.entries(cache)) {
    const kept: Partial<Record<TaskKind, CachedResult>> = {}
    for (const [kind, entry] of Object.entries(byKind || {})) {
      if (entry && now - entry.at < CACHE_TTL_MS) {
        kept[kind as TaskKind] = entry
      }
    }
    if (Object.keys(kept).length > 0) out[skillId] = kept
  }
  return out
}

// 登录后清理 anon 前缀的任务/结果缓存：userId 从 ''/'anon' 变成真实值时，
// sessionStorage 里可能残留 'sf-running-tasks:anon' 和 'sf-task-results:anon'。
// 新用户用真实 uid 前缀，不会串读，但堆积浪费 5MB 配额。只删这两个精确 key。
function clearAnonRunningTaskStorage() {
  if (typeof window === 'undefined') return
  try {
    sessionStorage.removeItem(`${STORAGE_TASKS_PREFIX}anon`)
    sessionStorage.removeItem(`${STORAGE_RESULTS_PREFIX}anon`)
  } catch {
    // 安全模式 / 禁用 storage 时静默忽略
  }
}

export const useRunningTasksStore = defineStore('runningTasks', () => {
  const userStore = useUserStore()
  const currentUserId = computed(() => userStore.userInfo?.user_id || '')
  const activeUserId = ref<string>(currentUserId.value)

  const tasks = ref<RunningTask[]>(loadTasks(activeUserId.value))
  const resultCache = ref<ResultCache>(pruneResults(loadResults(activeUserId.value)))
  // 默认折叠（只显示小徽章），避免覆盖页面其他交互
  const dockOpen = ref(false)

  const running = computed(() => tasks.value.filter(t => t.status === 'running'))
  const runningCount = computed(() => running.value.length)
  const hasTasks = computed(() => tasks.value.length > 0)

  watch(tasks, (v) => {
    try { sessionStorage.setItem(tasksKey(activeUserId.value), JSON.stringify(v)) } catch { /* ignore quota */ }
  }, { deep: true })

  watch(resultCache, (v) => {
    try { sessionStorage.setItem(resultsKey(activeUserId.value), JSON.stringify(v)) } catch { /* ignore quota */ }
  }, { deep: true })

  // userId 发生变化（登录 / 切账号 / 登出）时，以新用户的 storage key 重载，
  // 避免旧用户的任务和结果泄露到新账号视图；登出时 userId 清空回落到 'anon'，
  // 也确保不把下一个登录账号的数据再写到 anon 空间。
  watch(currentUserId, (newId, oldId) => {
    activeUserId.value = newId
    tasks.value = loadTasks(newId)
    resultCache.value = pruneResults(loadResults(newId))
    // 登录场景（oldId 空/'anon' → newId 真实 uid）：清掉 anon 前缀残留
    // 登出场景（oldId 真实 uid → newId 空/'anon'）：保留数据等重新登录
    const isLogin = newId && newId !== 'anon' && (oldId === 'anon' || oldId === '' || oldId == null)
    if (isLogin) clearAnonRunningTaskStorage()
  })

  // 启动时给历史 running 任务打个"状态未知"标记，避免 UI 卡 loading
  for (const t of tasks.value) {
    if (t.status === 'running') {
      t.status = 'error'
      t.message = '页面刷新，状态丢失'
      t.finishedAt = Date.now()
    }
  }

  function start(payload: Omit<RunningTask, 'id' | 'status' | 'startedAt'>): string {
    const id = genId()
    tasks.value.unshift({
      ...payload,
      id,
      status: 'running',
      startedAt: Date.now(),
    })
    if (tasks.value.length > MAX_TASKS) tasks.value = tasks.value.slice(0, MAX_TASKS)
    // 不自动展开 dock：避免遮挡页面输入/按钮。用户手动点徽章展开。
    return id
  }

  function findByRefKey(refKey: string): RunningTask | undefined {
    return tasks.value.find(t => t.refKey === refKey)
  }

  // 按 refKey 获取或创建任务：已存在则复用并更新字段，不存在则新建
  function startOrUpdate(
    refKey: string,
    payload: Omit<RunningTask, 'id' | 'status' | 'startedAt' | 'refKey'>,
  ): string {
    const existing = findByRefKey(refKey)
    if (existing) {
      Object.assign(existing, payload)
      if (existing.status !== 'running') {
        existing.status = 'running'
        existing.finishedAt = undefined
        existing.message = payload.message
      }
      return existing.id
    }
    return start({ ...payload, refKey })
  }

  function updateTask(id: string, patch: Partial<Omit<RunningTask, 'id' | 'startedAt'>>) {
    const task = tasks.value.find(t => t.id === id)
    if (!task) return
    Object.assign(task, patch)
  }

  function finish(
    id: string,
    patch: { status?: TaskStatus; message?: string; runId?: string; result?: any } = {},
  ) {
    const task = tasks.value.find(t => t.id === id)
    if (!task) return
    // 已终结的任务（error/success/blocked）不被覆盖成 success —— 避免晚到的事件把 error 改回 success
    if (task.status !== 'running' && (patch.status === undefined || patch.status === 'success')) {
      return
    }
    task.status = patch.status ?? 'success'
    task.message = patch.message
    task.runId = patch.runId ?? task.runId
    task.finishedAt = Date.now()
    if (patch.result !== undefined && task.skillId && task.status !== 'error') {
      cacheResult(task.skillId, task.kind, patch.result)
    }
    scheduleAutoDismiss(id)
  }

  function fail(id: string, message: string) {
    finish(id, { status: 'error', message })
  }

  function cacheResult(skillId: string, kind: TaskKind, result: any) {
    const next = { ...resultCache.value }
    const bySkill = { ...(next[skillId] || {}) }
    bySkill[kind] = { result, at: Date.now() }
    next[skillId] = bySkill
    resultCache.value = next
  }

  function getCachedResult(skillId: string, kind: TaskKind): CachedResult | null {
    const entry = resultCache.value[skillId]?.[kind]
    if (!entry) return null
    if (Date.now() - entry.at >= CACHE_TTL_MS) return null
    return entry
  }

  function clearCachedResult(skillId: string, kind?: TaskKind) {
    const next = { ...resultCache.value }
    if (!kind) {
      delete next[skillId]
    } else {
      const bySkill = { ...(next[skillId] || {}) }
      delete bySkill[kind]
      if (Object.keys(bySkill).length === 0) delete next[skillId]
      else next[skillId] = bySkill
    }
    resultCache.value = next
  }

  function dismiss(id: string) {
    tasks.value = tasks.value.filter(t => t.id !== id)
    if (tasks.value.length === 0) dockOpen.value = false
  }

  function clearFinished() {
    tasks.value = tasks.value.filter(t => t.status === 'running')
    if (tasks.value.length === 0) dockOpen.value = false
  }

  function toggleDock() {
    dockOpen.value = !dockOpen.value
  }

  function scheduleAutoDismiss(id: string) {
    setTimeout(() => {
      const task = tasks.value.find(t => t.id === id)
      if (task && task.status !== 'running') dismiss(id)
    }, AUTO_DISMISS_MS)
  }

  // 批量将符合条件的 running 任务改为 error（常用：页面卸载时标记所有活跃会话为连接断）
  function failRunningWhere(predicate: (t: RunningTask) => boolean, message: string) {
    const now = Date.now()
    for (const t of tasks.value) {
      if (t.status === 'running' && predicate(t)) {
        t.status = 'error'
        t.message = message
        t.finishedAt = now
        scheduleAutoDismiss(t.id)
      }
    }
  }

  return {
    tasks,
    dockOpen,
    running,
    runningCount,
    hasTasks,
    start,
    startOrUpdate,
    findByRefKey,
    updateTask,
    finish,
    fail,
    failRunningWhere,
    dismiss,
    clearFinished,
    toggleDock,
    getCachedResult,
    clearCachedResult,
  }
})
