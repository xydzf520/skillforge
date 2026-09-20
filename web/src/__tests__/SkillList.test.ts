import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// Mock API
vi.mock('@/api', () => ({
  skillApi: {
    list: vi.fn().mockResolvedValue({ items: [], total: 0, status_counts: {} }),
    pinned: vi.fn().mockResolvedValue([]),
    listDepartments: vi.fn().mockResolvedValue({ departments: [] }),
    delete: vi.fn().mockResolvedValue({}),
  },
}))

// Mock router
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

// Mock stores
vi.mock('@/stores/user', () => ({
  useUserStore: () => ({
    isEngineer: true,
    role: 'admin',
    department: 'EC',
    canViewAll: true,
  }),
}))

describe('SkillList basic', () => {
  it('should be importable', async () => {
    // Just verify the component can be imported without errors
    const module = await import('@/pages/skill/SkillList.vue')
    expect(module.default).toBeDefined()
  })
})
