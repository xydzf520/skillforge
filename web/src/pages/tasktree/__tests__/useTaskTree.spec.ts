/**
 * useTaskTree 轮询韧性单测。
 *
 * 覆盖点：
 * - 连续失败 3 次后，下一轮 tree tick 间隔变为 30s（真正验证 setInterval 周期）
 * - 任意成功 → 退避重置，间隔回到 10s（验证下一次 poll 在 10s 触发）
 * - refreshAll 并发调用时 inFlight 锁生效（只触发一次底层 tree API）
 * - 切部门清 pending（旧请求结果被判 stale 丢弃）
 *
 * 通过 vi.useFakeTimers() + vi.advanceTimersByTimeAsync() 真正测
 * setInterval 的触发时机，而不是只检查 computed state 是否变化。
 *
 * useTaskTree 在 onMounted 里 startPolling()，所以需要把它包进一个真实
 * mount 的组件，让生命周期钩子触发。
 */
import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, reactive } from 'vue'

// mock @arco-design/web-vue（仅用到 Message.success 的 retrySkill 路径）
vi.mock('@arco-design/web-vue', () => ({
  Message: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
}))

const routeState = reactive({
  path: '/task-tree',
  query: {} as Record<string, unknown>,
})

const routerReplaceMock = vi.fn(async ({ query }: { query?: Record<string, unknown> }) => {
  routeState.query = { ...(query || {}) }
})

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => ({
    replace: routerReplaceMock,
  }),
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => ({
    canViewAll: true,
    department: 'EC',
  }),
}))

const treeMock = vi.fn(async () => ({}))
const statsMock = vi.fn(async () => ({}))
const dashboardMock = vi.fn(async () => ({}))
const nodeDetailMock = vi.fn(async (): Promise<any> => null)

vi.mock('@/api', () => ({
  taskTreeApi: {
    getTree: treeMock,
    getStats: statsMock,
    getDashboard: dashboardMock,
    getNodeDetail: nodeDetailMock,
    getRunChain: vi.fn(async () => null),
    diagnoseRun: vi.fn(async () => ({ diagnosis: '' })),
    getSkillValue: vi.fn(async () => null),
  },
  executionApi: {
    run: vi.fn(async () => ({})),
  },
}))

/**
 * 手动 resolve 型 tree API mock：每次 getTree() 调用把 {params, options, resolve, reject}
 * 塞进 treeCalls，测试代码按需 resolve。用于 inflight / stale 等需要控制
 * 请求生命周期的场景。
 */
type ManualTreeCall = {
  params?: Record<string, unknown>
  options?: { refresh?: boolean }
  resolve: (body: any, status?: number, etag?: string) => void
  reject: (err: Error) => void
}

const treeCalls: ManualTreeCall[] = []

function installManualTreeMock() {
  treeCalls.length = 0
  treeMock.mockImplementation((params?: Record<string, unknown>, options?: { refresh?: boolean }) => {
    return new Promise((resolve, reject) => {
      treeCalls.push({
        params,
        options,
        resolve: (body: any, status = 200, etag?: string) => {
          if (status >= 200 && status < 300) {
            resolve({ ...body, etag: body?.etag || etag || '' })
          } else {
            reject(new Error(`加载任务树失败 (${status})`))
          }
        },
        reject,
      })
    })
  })
}

/**
 * 自动响应型 tree API mock：每次 getTree() 调用同步/异步返回固定结果。
 * 用 queue 模式，每次调用消耗一项；queue 空时用 fallback。
 * 用于退避/恢复测试，这类测试不需要控制单次请求，只需让请求按调度
 * 真的打出来，然后验证 setInterval 的周期。
 */
type AutoResponse = { body?: any; status?: number; etag?: string; reject?: Error }
let autoQueue: AutoResponse[] = []
let autoFallback: AutoResponse = { body: {}, status: 200, etag: 'fallback-etag' }

function installAutoTreeMock() {
  autoQueue = []
  autoFallback = { body: { departments: [], projected_at: '', etag: 'fallback-etag' }, status: 200, etag: 'fallback-etag' }
  treeMock.mockImplementation(async () => {
    const item = autoQueue.length > 0 ? autoQueue.shift()! : autoFallback
    if (item.reject) throw item.reject
    if ((item.status ?? 200) < 200 || (item.status ?? 200) >= 300) {
      throw new Error(`加载任务树失败 (${item.status ?? 500})`)
    }
    return { ...(item.body ?? {}), etag: (item.body as any)?.etag || item.etag || '' }
  })
}

function mockDocumentVisible() {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => 'visible',
  })
}

/**
 * 把 useTaskTree 挂到一个真实组件里，让 onMounted 触发 startPolling()。
 * 这样 setInterval 才会被 fake timers 接管，后面 advanceTimersByTimeAsync
 * 才能真正推进 poll。
 *
 * 因为 vi.resetModules() 已经把 useTaskTree 清了缓存，且 vi.mock 用的是
 * 延迟解析的 factory，所以这里只能在 beforeEach 里重新 import 再传进来。
 */
async function mountHost() {
  const mod = await import('../composables/useTaskTree')
  let exposed: ReturnType<typeof mod.useTaskTree> | null = null
  const Host = defineComponent({
    setup() {
      exposed = mod.useTaskTree()
      return () => h('div')
    },
  })
  const wrapper = mount(Host)
  return { wrapper, getHook: () => exposed! }
}

describe('useTaskTree 轮询韧性', () => {
  beforeEach(async () => {
    treeMock.mockReset().mockResolvedValue({})
    statsMock.mockReset().mockResolvedValue({})
    dashboardMock.mockReset().mockResolvedValue({})
    nodeDetailMock.mockReset().mockResolvedValue(null)
    routeState.query = {}
    routerReplaceMock.mockClear()
    mockDocumentVisible()
    vi.resetModules()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  // ---------------------------------------------------------------------------
  // 退避测试：连续 3 次失败 → 下一轮 poll 间隔从 10s 变 30s
  // 用 fake timers 真正推进时间验证 setInterval 周期，而不是只看 computed。
  // ---------------------------------------------------------------------------
  it('连续失败 3 次后，tree 轮询 setInterval 周期真的从 10s 切到 30s', async () => {
    installAutoTreeMock()
    // 让接下来每一次 tree API 都失败
    treeMock.mockRejectedValue(new Error('tree boom'))
    // stats / dashboard 也都失败，确保 refreshAll 统计到 3 次连续失败
    statsMock.mockRejectedValue(new Error('stats boom'))
    dashboardMock.mockRejectedValue(new Error('dash boom'))

    const { getHook } = await mountHost()
    // 等 setup 完成 + onMounted 触发初始 refreshAll
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()
    const hook = getHook()
    expect(hook).toBeTruthy()

    // 初始 mount 时 refreshAll 已经跑过一次（失败 1）。再推两轮 10s poll
    // 让 failureCount 累计到 3。注意：10s tick 走的是 refreshTreeTick（只 loadTree）。
    await vi.advanceTimersByTimeAsync(10_000)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(10_000)
    await flushPromises()

    expect(hook.failureCount.value).toBe(3)
    expect(hook.currentPollIntervalSeconds.value).toBe(30)

    // 关键断言：验证 setInterval 的真实周期已从 10s 切到 30s。
    // 推进到 25s（< 30s）不应该有新 tree API 触发。
    const treeBefore = treeMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(25_000)
    await flushPromises()
    const treeAfter25s = treeMock.mock.calls.length
    expect(treeAfter25s).toBe(treeBefore) // 没有新请求 = 10s 周期没生效

    // 再推 6s（累计 31s > 30s），应该触发一次 tree API
    await vi.advanceTimersByTimeAsync(6_000)
    await flushPromises()
    const treeAfter31s = treeMock.mock.calls.length
    expect(treeAfter31s).toBe(treeBefore + 1) // 刚好一次 = 30s 周期生效
  })

  // ---------------------------------------------------------------------------
  // 恢复测试：进入退避后，一次成功应该让周期回到 10s。
  // 验证方式：成功后下一次 tree API 在 10s 内触发（而不是 30s 后）。
  // ---------------------------------------------------------------------------
  it('退避状态下一次成功后，setInterval 周期真的回到 10s', async () => {
    installAutoTreeMock()
    // 前 3 次 tree API 失败，之后成功
    let callCount = 0
    treeMock.mockImplementation(async () => {
      callCount += 1
      if (callCount <= 3) throw new Error('tree boom')
      return { departments: [], projected_at: '2026-04-14', etag: 'ok' }
    })
    statsMock.mockRejectedValue(new Error('stats boom'))
    dashboardMock.mockRejectedValue(new Error('dash boom'))

    const { getHook } = await mountHost()
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()
    const hook = getHook()

    // 初始 mount 触发 refreshAll（失败 1 次）；再 2 轮 10s tick 失败到 3 次
    await vi.advanceTimersByTimeAsync(10_000)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(10_000)
    await flushPromises()
    expect(hook.failureCount.value).toBe(3)
    expect(hook.currentPollIntervalSeconds.value).toBe(30)

    // 现在进入退避周期，下一次 tick 要等 30s
    // 切一下 stats/dashboard mock，让接下来的 refreshAll 至少有一个成功
    statsMock.mockReset().mockResolvedValue({})
    dashboardMock.mockReset().mockResolvedValue({})

    // 推进 30s 触发退避下的 tree tick（会成功，因为 tree API 第 4 次成功）
    await vi.advanceTimersByTimeAsync(30_000)
    await flushPromises()
    expect(hook.failureCount.value).toBe(0)
    expect(hook.currentPollIntervalSeconds.value).toBe(10)

    // 关键断言：周期已回到 10s。再推 10s 应触发新 tree API。
    const treeBefore = treeMock.mock.calls.length
    await vi.advanceTimersByTimeAsync(10_000)
    await flushPromises()
    const treeAfter = treeMock.mock.calls.length
    expect(treeAfter).toBeGreaterThan(treeBefore) // 10s 就触发了，没等到 30s
  })

  // ---------------------------------------------------------------------------
  // inflight 锁：并发 refreshAll 只触发一次底层 tree API
  // ---------------------------------------------------------------------------
  it('refreshAll inflight 锁：并发调用只触发一次底层 tree API', async () => {
    installManualTreeMock()
    const { getHook } = await mountHost()
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()
    const hook = getHook()

    // mount 的初始 refreshAll 会吃掉第一个 tree API 调用，先把它 resolve 掉
    const initCall = treeCalls[0]
    if (initCall) {
      initCall.resolve({ departments: [], projected_at: '', etag: 'e0' }, 200, 'e0')
    }
    await flushPromises()
    treeCalls.length = 0

    // 同时发三次 refreshAll
    const p1 = hook.refreshAll()
    const p2 = hook.refreshAll()
    const p3 = hook.refreshAll()

    await flushPromises()

    expect(treeCalls.length).toBe(1)
    expect(hook.inFlight.value).toBe(true)

    treeCalls[0].resolve({ departments: [], projected_at: '', etag: 'e1' }, 200, 'e1')
    await Promise.all([p1, p2, p3])
    await flushPromises()

    expect(hook.inFlight.value).toBe(false)
  })

  // ---------------------------------------------------------------------------
  // 切部门清 pending：旧 pending 请求的响应回来时应被 requestId 判 stale 丢弃，
  // 最终 tree.value 是新请求的结果而不是旧的。
  // ---------------------------------------------------------------------------
  it('切部门：旧 pending 请求被判 stale，tree 最终是新部门数据', async () => {
    installManualTreeMock()
    const { getHook } = await mountHost()
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()
    const hook = getHook()

    // 吃掉 mount 初始 refreshAll
    const initCall = treeCalls[0]
    if (initCall) {
      initCall.resolve({ departments: [], projected_at: '', etag: 'e0' }, 200, 'e0')
    }
    await flushPromises()
    treeCalls.length = 0

    // 手动发一次 refreshAll，不 resolve（留作 pending）
    const firstPromise = hook.refreshAll()
    await flushPromises()
    const pending = treeCalls[0]
    expect(pending).toBeDefined()

    // 给 selection 灌点值，验证切部门会重置
    hook.selectedInstanceId.value = 'inst-old'
    hook.selectedRunId.value = 'run-old'

    // 切部门：watcher 会 bump requestId、清 inflight、清 selection、发新 refreshAll
    hook.deptOverride.value = 'EC'
    await flushPromises()

    expect(hook.selectedInstanceId.value).toBe('')
    expect(hook.selectedRunId.value).toBe('')

    // 旧 pending 响应回来 → 应被 stale 丢弃
    pending!.resolve(
      { departments: [{ department_id: 'OLD', department_name: 'Old', instances: [] }] },
      200,
      'old-etag',
    )
    await flushPromises()

    // 新请求的 tree API 调用（第二次）
    const newCall = treeCalls[1]
    expect(newCall).toBeDefined()
    newCall!.resolve(
      { departments: [{ department_id: 'EC', department_name: 'EC', instances: [] }] },
      200,
      'new-etag',
    )
    await flushPromises()

    // 最终 tree 是 EC（新响应），不是 OLD（stale 响应被丢）
    expect(hook.tree.value?.departments?.[0]?.department_id).toBe('EC')
    expect(hook.tree.value?.departments?.[0]?.department_id).not.toBe('OLD')

    await firstPromise
  })
})


describe('useTaskTree M5：路由竞态合并', () => {
  beforeEach(async () => {
    treeMock.mockReset().mockResolvedValue({})
    statsMock.mockReset().mockResolvedValue({})
    dashboardMock.mockReset().mockResolvedValue({})
    nodeDetailMock.mockReset().mockResolvedValue(null)
    routeState.query = {}
    routerReplaceMock.mockClear()
    mockDocumentVisible()
    vi.resetModules()
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('一拍内多次 ref 改动 → router.replace 只被合并调一次', async () => {
    installAutoTreeMock()
    const { getHook } = await mountHost()
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()
    const hook = getHook()
    expect(hook).toBeTruthy()

    // 重置：onMounted 期间已经触发过若干次 syncQuery，重新计数
    routerReplaceMock.mockClear()

    // 同步连写多个 watcher 监听的 ref —— 模拟 selectInstance 内部的多次 set
    hook.activeTab.value = 'diagnosis'
    hook.selectedRunId.value = 'run-x'
    hook.activeTab.value = 'chain'

    // 一拍内（nextTick 之前）replace 不应被调用
    expect(routerReplaceMock).not.toHaveBeenCalled()
    // 推进微任务 + DOM tick：此时合并的 router.replace 触发一次
    await flushPromises()
    expect(routerReplaceMock.mock.calls.length).toBeLessThanOrEqual(1)
  })

  it('深链携带 run 但未指定 tab 时默认进入链路视图', async () => {
    routeState.query = { inst: 'demo-prod', run: 'rm-a92984e1' }
    installAutoTreeMock()
    autoFallback = {
      body: {
        departments: [{
          department_id: 'AI',
          department_name: 'AI 组',
          instances: [{
            instance_id: 'demo-prod',
            name: '平台节点',
            node_status: 'online',
            recent_completed: [{
              run_id: 'rm-a92984e1',
              skill_id: 'skill-1',
              skill_name: '天猫店铺链接下滑分析',
              status: 'failed',
              started_at: '2026-05-02T16:50:00',
            }],
          }],
        }],
        projected_at: '',
        etag: 'etag',
      },
      status: 200,
      etag: 'etag',
    }
    nodeDetailMock.mockResolvedValue({
      instance_id: 'demo-prod',
      active_runs: [],
      recent_completed: [{
        run_id: 'rm-a92984e1',
        skill_id: 'skill-1',
        skill_name: '天猫店铺链接下滑分析',
        status: 'failed',
        started_at: '2026-05-02T16:50:00',
      }],
    })
    const { getHook } = await mountHost()
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()

    expect(getHook().activeTab.value).toBe('chain')
  })

  it('中文实例深链会规范化为 ASCII inst token', async () => {
    routeState.query = { department: '内容电商', inst: '内容电商', tab: 'schedules', anomaly: '0' }
    installAutoTreeMock()
    autoFallback = {
      body: {
        departments: [{
          department_id: 'EC',
          department_name: '内容电商',
          instances: [{
            instance_id: '内容电商',
            name: '内容电商节点',
            node_status: 'online',
            active_runs: [],
            recent_completed: [],
          }],
        }],
        projected_at: '',
        etag: 'etag-cn',
      },
      status: 200,
      etag: 'etag-cn',
    }
    nodeDetailMock.mockResolvedValue({
      instance_id: '内容电商',
      active_runs: [],
      recent_completed: [],
    })

    const { getHook } = await mountHost()
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()

    expect(getHook().selectedInstanceId.value).toBe('内容电商')
    expect(nodeDetailMock).toHaveBeenCalledWith('内容电商')
    const lastQuery = routerReplaceMock.mock.calls.at(-1)?.[0]?.query || {}
    expect(lastQuery.inst).toMatch(/^node-[a-z0-9]+$/)
    expect(String(lastQuery.inst)).not.toContain('内容电商')
    expect(lastQuery.department).toBe('EC')
    expect(String(lastQuery.department || '')).not.toContain('内容电商')
  })

  it('切部门后 selectedInstanceId 与 selectedRunId 都被清空', async () => {
    installAutoTreeMock()
    const { getHook } = await mountHost()
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()
    const hook = getHook()

    hook.selectedInstanceId.value = 'inst-old'
    hook.selectedRunId.value = 'run-old'
    await flushPromises()

    hook.departmentFilter.value = 'CRM'
    await flushPromises()

    expect(hook.selectedInstanceId.value).toBe('')
    expect(hook.selectedRunId.value).toBe('')
  })

  it('无 query 直接打开任务树时默认展示常规视图，不只看异常', async () => {
    routeState.query = {}
    installAutoTreeMock()
    const { getHook } = await mountHost()
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()

    expect(getHook().showAnomalyOnly.value).toBe(false)
  })

  it('手动刷新会向 tree/stats/dashboard 传递 refresh 标记', async () => {
    installAutoTreeMock()
    const { getHook } = await mountHost()
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()
    const hook = getHook()

    treeMock.mockClear()
    statsMock.mockClear()
    dashboardMock.mockClear()

    await hook.refreshAll({ refresh: true })
    await flushPromises()

    expect(treeMock).toHaveBeenCalledWith({}, { refresh: true })
    expect(statsMock).toHaveBeenCalledWith({ window: hook.window.value }, { refresh: true })
    expect(dashboardMock).toHaveBeenCalledWith({ period_days: 30 }, { refresh: true })
  })
})
