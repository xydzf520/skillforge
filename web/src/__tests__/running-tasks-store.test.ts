/**
 * running-tasks-store.test.ts
 *
 * 覆盖 useRunningTasksStore 的两个关键属性：
 *   1. startOrUpdate 幂等：同 refKey 复用 id，不创建重复任务；不同 refKey 新建。
 *   2. 按 userId 分片 sessionStorage：切换账号后不会看到前一个账号的任务（越权风险）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api', () => ({
  authApi: { login: vi.fn(), logout: vi.fn(), me: vi.fn() },
  skillApi: { get: vi.fn() },
  workbenchApi: { createSession: vi.fn(), getSession: vi.fn(), parseIntent: vi.fn() },
}))

// jsdom 缺省没有 matchMedia；user store 读主题偏好会用到
if (!(globalThis.window as any).matchMedia) {
  ;(globalThis.window as any).matchMedia = () => ({
    matches: false,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })
}

async function freshStore(userId: string | null = 'u1', opts: { keepStorage?: boolean } = {}) {
  vi.resetModules()
  if (!opts.keepStorage) sessionStorage.clear()
  setActivePinia(createPinia())
  const { useUserStore } = await import('@/stores/user')
  const user = useUserStore()
  if (userId) {
    user.userInfo = { user_id: userId, username: userId, role: 'admin', department: 'EC' } as any
  } else {
    user.userInfo = null
  }
  const { useRunningTasksStore } = await import('@/stores/runningTasks')
  const store = useRunningTasksStore()
  return { store, user }
}

describe('useRunningTasksStore.startOrUpdate 幂等', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  afterEach(() => {
    sessionStorage.clear()
  })

  it('同 refKey 复用同一个任务 id', async () => {
    const { store } = await freshStore()
    const id1 = store.startOrUpdate('chat:EC-100', {
      kind: 'chat', title: 'AI 对话', skillId: 'EC-100',
      returnPath: '/skills/EC-100?panel=assistant',
    })
    const id2 = store.startOrUpdate('chat:EC-100', {
      kind: 'chat', title: 'AI 对话（已更新）', skillId: 'EC-100',
      returnPath: '/skills/EC-100?panel=assistant',
    })
    expect(id1).toBe(id2)
    expect(store.tasks.length).toBe(1)
    expect(store.tasks[0].title).toBe('AI 对话（已更新）')
  })

  it('不同 refKey 新建独立任务', async () => {
    const { store } = await freshStore()
    const id1 = store.startOrUpdate('chat:EC-100', {
      kind: 'chat', title: 'A', skillId: 'EC-100', returnPath: '/a',
    })
    const id2 = store.startOrUpdate('chat:EC-200', {
      kind: 'chat', title: 'B', skillId: 'EC-200', returnPath: '/b',
    })
    expect(id1).not.toBe(id2)
    expect(store.tasks.length).toBe(2)
  })

  it('startOrUpdate 会把已 finish 的任务重置回 running（恢复语义）', async () => {
    const { store } = await freshStore()
    const id = store.startOrUpdate('chat:EC-100', {
      kind: 'chat', title: 'A', skillId: 'EC-100', returnPath: '/a',
    })
    store.finish(id, { status: 'success', message: 'ok' })
    expect(store.tasks[0].status).toBe('success')
    store.startOrUpdate('chat:EC-100', {
      kind: 'chat', title: 'A again', skillId: 'EC-100', returnPath: '/a',
    })
    expect(store.tasks[0].status).toBe('running')
    expect(store.tasks[0].finishedAt).toBeUndefined()
  })

  it('findByRefKey 返回对应任务', async () => {
    const { store } = await freshStore()
    const id = store.startOrUpdate('chat:EC-100', {
      kind: 'chat', title: 'A', skillId: 'EC-100', returnPath: '/a',
    })
    const task = store.findByRefKey('chat:EC-100')
    expect(task).toBeDefined()
    expect(task!.id).toBe(id)
  })

  it('大量 startOrUpdate 同 refKey 不会膨胀数组', async () => {
    const { store } = await freshStore()
    for (let i = 0; i < 50; i += 1) {
      store.startOrUpdate('chat:EC-100', {
        kind: 'chat', title: `t-${i}`, skillId: 'EC-100', returnPath: '/a',
      })
    }
    expect(store.tasks.length).toBe(1)
  })
})

describe('useRunningTasksStore 用户隔离（sessionStorage 分片）', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  afterEach(() => {
    sessionStorage.clear()
  })

  it('u1 的任务不会出现在 u2 的视图里', async () => {
    // u1 登录 + 创建一个任务；首次清空 storage
    {
      const { store } = await freshStore('u1')
      store.start({
        kind: 'chat', title: 'u1 任务', skillId: 'EC-100', returnPath: '/x',
      })
      expect(store.tasks.length).toBe(1)
      // 等 watcher 把 tasks 写入 sessionStorage（deep watch 是异步的）
      await new Promise((r) => setTimeout(r, 0))
    }

    // u2 重新 hydrate（保留 storage，切 userId → 不同 storage key）
    {
      const { store } = await freshStore('u2', { keepStorage: true })
      expect(store.tasks.length).toBe(0)
    }

    // u1 回来仍然看得到（保留 storage）
    {
      const { store } = await freshStore('u1', { keepStorage: true })
      expect(store.tasks.length).toBe(1)
      expect(store.tasks[0].title).toBe('u1 任务')
    }
  })

  it('切账号 watcher：同 pinia 下 userInfo 变化时重载任务列表', async () => {
    vi.resetModules()
    sessionStorage.clear()
    setActivePinia(createPinia())
    const { useUserStore } = await import('@/stores/user')
    const { useRunningTasksStore } = await import('@/stores/runningTasks')

    const user = useUserStore()
    user.userInfo = { user_id: 'u1', username: 'u1', role: 'admin' } as any
    const store = useRunningTasksStore()
    store.start({ kind: 'chat', title: 'u1 任务', skillId: 'E', returnPath: '/' })
    expect(store.tasks.length).toBe(1)

    // 切换到 u2（同 store 实例，watch(currentUserId) 应触发 loadTasks(u2)）
    user.userInfo = { user_id: 'u2', username: 'u2', role: 'admin' } as any
    // pinia 的 watch 是同步触发的，但 setup store 里用的是 vue 的 watch——需要等一 flush
    await Promise.resolve()
    await Promise.resolve()
    expect(store.tasks.length).toBe(0)
  })

  it('登出（userInfo=null）切回 anon storage key', async () => {
    vi.resetModules()
    sessionStorage.clear()
    setActivePinia(createPinia())
    const { useUserStore } = await import('@/stores/user')
    const { useRunningTasksStore } = await import('@/stores/runningTasks')

    const user = useUserStore()
    user.userInfo = { user_id: 'u1' } as any
    const store = useRunningTasksStore()
    store.start({ kind: 'chat', title: 't', skillId: 'E', returnPath: '/' })
    expect(store.tasks.length).toBe(1)

    user.userInfo = null
    await Promise.resolve()
    await Promise.resolve()
    expect(store.tasks.length).toBe(0)
  })
})

describe('useRunningTasksStore 登录后清理 anon 前缀残留', () => {
  beforeEach(() => { sessionStorage.clear() })
  afterEach(() => { sessionStorage.clear() })

  it('anon running task storage cleared after login', async () => {
    vi.resetModules()
    sessionStorage.clear()
    setActivePinia(createPinia())

    // 预置 anon 前缀的任务/结果缓存 + 另一个真实 uid 的缓存（不应被影响）
    sessionStorage.setItem('sf-running-tasks:anon', JSON.stringify([{ id: 'old', kind: 'chat', title: 'anon-task', skillId: 'x', returnPath: '/' }]))
    sessionStorage.setItem('sf-task-results:anon', JSON.stringify({ 'x': { chat: { result: {}, at: Date.now() } } }))
    sessionStorage.setItem('sf-running-tasks:u99', JSON.stringify([{ id: 'other', kind: 'chat', title: 'u99-task', skillId: 'y', returnPath: '/' }]))
    sessionStorage.setItem('sf-task-results:u99', JSON.stringify({ 'y': { chat: { result: {}, at: Date.now() } } }))

    const { useUserStore } = await import('@/stores/user')
    const { useRunningTasksStore } = await import('@/stores/runningTasks')
    const user = useUserStore()
    // 初始未登录（userInfo=null → currentUserId=''）
    user.userInfo = null
    useRunningTasksStore()

    // 登录：切到真实 u42
    user.userInfo = { user_id: 'u42', username: 'u42', role: 'admin' } as any
    await Promise.resolve()
    await Promise.resolve()

    // anon 两个 key 应被清理
    expect(sessionStorage.getItem('sf-running-tasks:anon')).toBeNull()
    expect(sessionStorage.getItem('sf-task-results:anon')).toBeNull()
    // 其他用户的 key 不受影响
    expect(sessionStorage.getItem('sf-running-tasks:u99')).not.toBeNull()
    expect(sessionStorage.getItem('sf-task-results:u99')).not.toBeNull()
  })
})

describe('useRunningTasksStore.finish 已终结任务不被回退', () => {
  beforeEach(() => { sessionStorage.clear() })

  it('error 任务不会被后到的 success 覆盖', async () => {
    const { store } = await freshStore()
    const id = store.start({
      kind: 'chat', title: 't', skillId: 'EC-100', returnPath: '/a',
    })
    store.fail(id, '失败了')
    expect(store.tasks[0].status).toBe('error')
    // 晚到的 success：应该被忽略
    store.finish(id, { status: 'success' })
    expect(store.tasks[0].status).toBe('error')
    expect(store.tasks[0].message).toBe('失败了')
  })

  it('error 任务可被明确的 blocked/error 再次覆盖', async () => {
    const { store } = await freshStore()
    const id = store.start({
      kind: 'chat', title: 't', skillId: 'EC-100', returnPath: '/a',
    })
    store.fail(id, '初次失败')
    // 业务上如果显式传入非 success 的新状态，目前实现允许覆盖（第 176 行 condition）
    store.finish(id, { status: 'error', message: '二次失败' })
    expect(store.tasks[0].status).toBe('error')
    expect(store.tasks[0].message).toBe('二次失败')
  })
})
