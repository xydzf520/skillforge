import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// Mock API
vi.mock('@/api', () => ({
  authApi: {
    me: vi.fn().mockResolvedValue({ user_id: 'u1', username: 'admin', role: 'admin', department: 'EC', can_view_all: true }),
    login: vi.fn().mockResolvedValue({ user_id: 'u1', username: 'admin', role: 'admin' }),
    logout: vi.fn().mockResolvedValue({ ok: true }),
  },
}))

// Mock matchMedia
Object.defineProperty(window, 'matchMedia', {
  value: vi.fn().mockImplementation(() => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })),
})

describe('useUserStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('starts logged out', async () => {
    const { useUserStore } = await import('@/stores/user')
    const store = useUserStore()
    expect(store.isLoggedIn).toBe(false)
    expect(store.role).toBe('')
  })

  it('isAdmin/isEngineer computed correctly for admin', async () => {
    const { useUserStore } = await import('@/stores/user')
    const store = useUserStore()
    store.userInfo = { user_id: 'u1', role: 'admin', department: 'EC', can_view_all: true }
    expect(store.isAdmin).toBe(true)
    expect(store.isEngineer).toBe(true)
    expect(store.canViewAll).toBe(true)
  })

  it('isAdmin is false for ai_engineer', async () => {
    const { useUserStore } = await import('@/stores/user')
    const store = useUserStore()
    store.userInfo = { user_id: 'u2', role: 'ai_engineer', department: 'EC' }
    expect(store.isAdmin).toBe(false)
    expect(store.isEngineer).toBe(true)
  })

  it('isEngineer is false for biz_owner', async () => {
    const { useUserStore } = await import('@/stores/user')
    const store = useUserStore()
    store.userInfo = { user_id: 'u3', role: 'biz_owner', department: 'EC' }
    expect(store.isAdmin).toBe(false)
    expect(store.isEngineer).toBe(false)
  })

  it('theme cycle works', async () => {
    const { useUserStore } = await import('@/stores/user')
    const store = useUserStore()
    store.setTheme('auto')
    expect(store.theme).toBe('auto')
    store.cycleTheme()
    expect(store.theme).toBe('light')
    store.cycleTheme()
    expect(store.theme).toBe('dark')
    store.cycleTheme()
    expect(store.theme).toBe('auto')
  })
})
