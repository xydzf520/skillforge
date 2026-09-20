import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api', () => ({
  workbenchApi: {
    createSession: vi.fn(),
    getSession: vi.fn(),
    parseIntent: vi.fn(),
  },
  authApi: {
    login: vi.fn(),
    logout: vi.fn(),
    me: vi.fn(),
  },
  skillApi: {
    get: vi.fn(),
  },
}))

function resolveField(store: Record<string, unknown>, candidates: string[]): string {
  for (const key of candidates) {
    if (key in store) return key
  }
  throw new Error(`Missing workbench store field: ${candidates.join(', ')}`)
}

function isEmptyValue(value: unknown): boolean {
  if (value == null) return true
  if (Array.isArray(value)) return value.length === 0
  if (typeof value === 'object') return Object.keys(value).length === 0
  return false
}

// jsdom 不实现 matchMedia；user.ts 启动时读主题偏好会用到
if (!(globalThis.window as any).matchMedia) {
  (globalThis.window as any).matchMedia = () => ({
    matches: false,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })
}

describe('useWorkbenchStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  it('exposes the baseline workbench state', async () => {
    const { useWorkbenchStore } = await import('@/stores/workbench')
    const store = useWorkbenchStore() as unknown as Record<string, unknown>

    expect((store as { $id: string }).$id).toBe('workbench')

    const sessionKey = resolveField(store, ['session', 'currentSession'])
    const skillKey = resolveField(store, ['skill', 'currentSkill'])
    const moduleKey = resolveField(store, ['activeModule', 'currentModule'])
    const patchKey = resolveField(store, ['patch', 'currentPatch'])
    const validationKey = resolveField(store, ['validation', 'validationResult'])
    const referencesKey = resolveField(store, ['references', 'referenceObjects'])
    const statusKey = resolveField(store, ['status', 'state'])

    expect(isEmptyValue(store[sessionKey])).toBe(true)
    expect(typeof store[skillKey]).toBe('object')
    expect(['goal', null, undefined]).toContain(store[moduleKey] ?? null)
    expect(typeof store[patchKey]).toBe('object')
    expect(typeof store[validationKey]).toBe('object')
    expect(Array.isArray(store[referencesKey])).toBe(true)
    expect(store[referencesKey]).toEqual([])
    expect([
      'idle',
      'loading_structure',
      'ready',
      'generating_patch',
      'validating_patch',
      'applying_patch',
      'patch_error',
      'validation_error',
    ]).toContain(store[statusKey])
  })

  it('keeps core workbench state writable', async () => {
    const { useWorkbenchStore } = await import('@/stores/workbench')
    const store = useWorkbenchStore() as unknown as Record<string, unknown>

    const sessionKey = resolveField(store, ['session', 'currentSession'])
    const skillKey = resolveField(store, ['skill', 'currentSkill'])
    const moduleKey = resolveField(store, ['activeModule', 'currentModule'])
    const patchKey = resolveField(store, ['patch', 'currentPatch'])
    const validationKey = resolveField(store, ['validation', 'validationResult'])
    const referencesKey = resolveField(store, ['references', 'referenceObjects'])
    const statusKey = resolveField(store, ['status', 'state'])

    store[sessionKey] = {
      session_id: 'wb-123',
      skill_id: 'SKILL-123',
      mode: 'pro',
      active_module: 'rules',
    }
    store[skillKey] = {
      name: '投放优化',
      goal: '优化 ROI',
      rules: [],
      params: [],
      output_table: [],
      test_cases: [],
      custom_sections: {},
    }
    store[moduleKey] = 'params'
    store[patchKey] = {
      target_module: 'params',
      summary: '提高阈值',
      patch: { params: [{ name: 'roi_threshold', default_value: 1.8 }] },
    }
    store[validationKey] = {
      can_apply: true,
      structural_checks: [],
      sample_case_checks: [],
      historical_replay_checks: [],
      impact_summary: { improved: 1, regressed: 0, unchanged: 0 },
    }
    store[referencesKey] = [
      { source_type: 'skill_module', source_id: 'SKILL-002', source_module: 'rules' },
    ]
    store[statusKey] = 'validating_patch'

    expect(store[sessionKey]).toMatchObject({ session_id: 'wb-123', skill_id: 'SKILL-123', mode: 'pro' })
    expect(store[skillKey]).toMatchObject({ name: '投放优化', goal: '优化 ROI' })
    expect(store[moduleKey]).toBe('params')
    expect(store[patchKey]).toMatchObject({ target_module: 'params', summary: '提高阈值' })
    expect(store[validationKey]).toMatchObject({ can_apply: true })
    expect(store[referencesKey]).toHaveLength(1)
    expect(store[statusKey]).toBe('validating_patch')
  })

  it('mirrors skillDocument from document store instead of owning a second copy', async () => {
    const { useWorkbenchStore } = await import('@/stores/workbench')
    const { useDocumentStore } = await import('@/stores/document')
    const workbench = useWorkbenchStore()
    const documentStore = useDocumentStore()

    documentStore.updateModuleStructured('goal', '唯一事实源目标')
    documentStore.updateModuleStructured('rules', [{ id: 'step_1', name: '判断', branches: [] }])

    expect(workbench.skillDocument.goal).toBe('唯一事实源目标')
    expect(workbench.skillDocument.rules).toHaveLength(1)
  })

  it('anon key cleared after login', async () => {
    // 模拟场景：未登录期写了 anon 前缀的对话缓存，用户登录后 anon 残留应被清理；
    // 其他真实 userId 的缓存不受影响。
    vi.resetModules()
    sessionStorage.clear()
    setActivePinia(createPinia())

    // 预置两条 sessionStorage 记录：一条 anon（应被清理），一条 u1（应保留）
    const anonKey = 'sf-chat:anon:skill-1'
    const realKey = 'sf-chat:u1:skill-1'
    sessionStorage.setItem(anonKey, JSON.stringify({ messages: [{ role: 'user', text: 'hi' }], lastSeq: 1 }))
    sessionStorage.setItem(realKey, JSON.stringify({ messages: [{ role: 'user', text: 'hi-u1' }], lastSeq: 1 }))

    const { useUserStore } = await import('@/stores/user')
    const { useWorkbenchStore } = await import('@/stores/workbench')

    const user = useUserStore()
    // 初始状态：未登录（userInfo 为 null → currentUserId 为空串）
    user.userInfo = null
    useWorkbenchStore()

    // 切到真实 userId，触发 watcher
    user.userInfo = { user_id: 'u42', username: 'u42', role: 'admin' } as any
    // setup store 的 watch 是异步的，等一个 flush
    await Promise.resolve()
    await Promise.resolve()

    expect(sessionStorage.getItem(anonKey)).toBeNull()
    expect(sessionStorage.getItem(realKey)).not.toBeNull()
  })
})
