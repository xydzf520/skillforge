/**
 * AppLayout 一级导航迁移保护测试。
 *
 * 断言 migratePrimaryNavStorage / persistPrimaryNav 在以下场景正确行事：
 * - 旧 key 'portal' → 迁移为 'projects'
 * - 旧 key 'skills' / 'knowledge' / 'agent' / 'learning' → 迁移为 'workflow'
 * - 新 key 'workflow' / 'training' / 'projects' / 'tasktree' / 'sf' / 'admin' → 保留
 * - 未知 key 'galaxy' → 清除 storage，返回默认 'hall'
 * - 空 storage → 返回默认 'hall'
 * - persistPrimaryNav 只接受白名单 key
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  PRIMARY_NAV_KEYS,
  PRIMARY_NAV_LABELS,
  WORKFLOW_DEFAULT_PATH,
  WORKFLOW_SUBNAV_KEYS,
  WORKFLOW_SUBNAV_LABELS,
  PRIMARY_NAV_MIGRATIONS,
  LAST_PRIMARY_NAV_STORAGE_KEY,
  DEFAULT_PRIMARY_NAV,
  migratePrimaryNavStorage,
  persistPrimaryNav,
} from '../primaryNav'

function createMemoryStorage(): Storage & { _map: Record<string, string> } {
  const map: Record<string, string> = {}
  return {
    _map: map,
    getItem: (k) => (k in map ? map[k] : null),
    setItem: (k, v) => {
      map[k] = String(v)
    },
    removeItem: (k) => {
      delete map[k]
    },
    clear: () => {
      for (const k of Object.keys(map)) delete map[k]
    },
    key: (i) => Object.keys(map)[i] ?? null,
    get length() {
      return Object.keys(map).length
    },
  }
}

describe('primaryNav migration', () => {
  let storage: Storage & { _map: Record<string, string> }

  beforeEach(() => {
    storage = createMemoryStorage()
  })

  it('PRIMARY_NAV_MIGRATIONS 包含 portal → projects 的已知迁移', () => {
    expect(PRIMARY_NAV_MIGRATIONS.portal).toBe('projects')
  })

  it('PRIMARY_NAV_KEYS 包含 tasktree（新一级导航）', () => {
    expect((PRIMARY_NAV_KEYS as readonly string[]).includes('tasktree')).toBe(true)
  })

  it('PRIMARY_NAV_KEYS 包含 AI Workflow 和项目一级导航', () => {
    expect((PRIMARY_NAV_KEYS as readonly string[]).includes('workflow')).toBe(true)
    expect((PRIMARY_NAV_KEYS as readonly string[]).includes('training')).toBe(true)
    expect((PRIMARY_NAV_KEYS as readonly string[]).includes('projects')).toBe(true)
    expect(PRIMARY_NAV_MIGRATIONS.brain).toBe('workflow')
  })

  it('PRIMARY_NAV_KEYS 包含插件一级导航', () => {
    expect((PRIMARY_NAV_KEYS as readonly string[]).includes('sf')).toBe(true)
  })

  it('PRIMARY_NAV_MIGRATIONS 将脉动旧一级导航迁入 AI Workflow', () => {
    expect(PRIMARY_NAV_MIGRATIONS.learning).toBe('workflow')
  })

  it('PRIMARY_NAV_KEYS 包含 admin 管理后台一级导航', () => {
    expect((PRIMARY_NAV_KEYS as readonly string[]).includes('admin')).toBe(true)
  })

  it('PRIMARY_NAV_KEYS 顺序匹配顶部一级导航', () => {
    expect([...PRIMARY_NAV_KEYS]).toEqual(['hall', 'workflow', 'training', 'projects', 'tasktree', 'sf', 'inbox', 'admin'])
    expect(PRIMARY_NAV_KEYS.map(key => PRIMARY_NAV_LABELS[key])).toEqual(['能力大厅', 'AI Workflow', '训练', '项目', '任务树', '插件', '收件', '管理后台'])
  })

  it('AI Workflow 二级导航只包含指定入口', () => {
    expect([...WORKFLOW_SUBNAV_KEYS]).toEqual(['learning', 'agent', 'skills', 'knowledge'])
    expect(WORKFLOW_SUBNAV_KEYS.map(key => WORKFLOW_SUBNAV_LABELS[key])).toEqual(['脉动', 'Agent', 'Skills', '知识库'])
  })

  it('AI Workflow 默认打开脉动页', () => {
    expect(WORKFLOW_DEFAULT_PATH).toBe('/learning-flow')
  })


  it('lastPrimaryNav 是废弃的 portal → 写回 projects', () => {
    storage.setItem(LAST_PRIMARY_NAV_STORAGE_KEY, 'portal')
    const result = migratePrimaryNavStorage(storage)
    expect(result).toBe('projects')
    // migration 会把新 key 写回 storage
    expect(storage.getItem(LAST_PRIMARY_NAV_STORAGE_KEY)).toBe('projects')
  })

  it('lastPrimaryNav 是 tasktree（仍在白名单）→ 保留', () => {
    storage.setItem(LAST_PRIMARY_NAV_STORAGE_KEY, 'tasktree')
    const result = migratePrimaryNavStorage(storage)
    expect(result).toBe('tasktree')
    expect(storage.getItem(LAST_PRIMARY_NAV_STORAGE_KEY)).toBe('tasktree')
  })

  it('lastPrimaryNav 是 sf（仍在白名单）→ 保留', () => {
    storage.setItem(LAST_PRIMARY_NAV_STORAGE_KEY, 'sf')
    const result = migratePrimaryNavStorage(storage)
    expect(result).toBe('sf')
    expect(storage.getItem(LAST_PRIMARY_NAV_STORAGE_KEY)).toBe('sf')
  })

  it('lastPrimaryNav 是 training（仍在白名单）→ 保留', () => {
    storage.setItem(LAST_PRIMARY_NAV_STORAGE_KEY, 'training')
    const result = migratePrimaryNavStorage(storage)
    expect(result).toBe('training')
    expect(storage.getItem(LAST_PRIMARY_NAV_STORAGE_KEY)).toBe('training')
  })

  it('lastPrimaryNav 是 workflow（仍在白名单）→ 保留', () => {
    storage.setItem(LAST_PRIMARY_NAV_STORAGE_KEY, 'workflow')
    const result = migratePrimaryNavStorage(storage)
    expect(result).toBe('workflow')
    expect(storage.getItem(LAST_PRIMARY_NAV_STORAGE_KEY)).toBe('workflow')
  })

  it('lastPrimaryNav 是旧 learning → 迁移到 workflow', () => {
    storage.setItem(LAST_PRIMARY_NAV_STORAGE_KEY, 'learning')
    const result = migratePrimaryNavStorage(storage)
    expect(result).toBe('workflow')
    expect(storage.getItem(LAST_PRIMARY_NAV_STORAGE_KEY)).toBe('workflow')
  })

  it('lastPrimaryNav 是旧 brain → 迁移到 workflow', () => {
    storage.setItem(LAST_PRIMARY_NAV_STORAGE_KEY, 'brain')
    const result = migratePrimaryNavStorage(storage)
    expect(result).toBe('workflow')
    expect(storage.getItem(LAST_PRIMARY_NAV_STORAGE_KEY)).toBe('workflow')
  })

  it('lastPrimaryNav 是完全未知的 galaxy → 清除 storage 并 fallback 到 hall', () => {
    storage.setItem(LAST_PRIMARY_NAV_STORAGE_KEY, 'galaxy')
    const result = migratePrimaryNavStorage(storage)
    expect(result).toBe(DEFAULT_PRIMARY_NAV)
    expect(storage.getItem(LAST_PRIMARY_NAV_STORAGE_KEY)).toBeNull()
  })

  it('storage 为空 → 返回默认 hall', () => {
    expect(migratePrimaryNavStorage(storage)).toBe('hall')
  })

  it('storage 抛异常 → 静默 fallback 到 hall', () => {
    const broken: Storage = {
      getItem: () => {
        throw new Error('boom')
      },
      setItem: () => undefined,
      removeItem: () => undefined,
      clear: () => undefined,
      key: () => null,
      length: 0,
    }
    expect(migratePrimaryNavStorage(broken)).toBe('hall')
  })

  it('persistPrimaryNav 写入合法 key', () => {
    persistPrimaryNav('workflow', storage)
    expect(storage.getItem(LAST_PRIMARY_NAV_STORAGE_KEY)).toBe('workflow')
    persistPrimaryNav('admin', storage)
    expect(storage.getItem(LAST_PRIMARY_NAV_STORAGE_KEY)).toBe('admin')
  })

  it('persistPrimaryNav 拒绝非法 key（不写 storage）', () => {
    persistPrimaryNav('portal', storage)
    expect(storage.getItem(LAST_PRIMARY_NAV_STORAGE_KEY)).toBeNull()
    persistPrimaryNav(undefined, storage)
    expect(storage.getItem(LAST_PRIMARY_NAV_STORAGE_KEY)).toBeNull()
  })

  it('persistPrimaryNav 在 storage 抛异常时静默', () => {
    const broken: Storage = {
      getItem: () => null,
      setItem: () => {
        throw new Error('quota')
      },
      removeItem: () => undefined,
      clear: () => undefined,
      key: () => null,
      length: 0,
    }
    expect(() => persistPrimaryNav('workflow', broken)).not.toThrow()
  })
})

/**
 * 同时校验 AppLayout 能被导入（冒烟测试：primaryNav.ts 模块与 AppLayout.vue 依赖兼容）
 */
describe('AppLayout smoke', () => {
  beforeEach(() => {
    vi.resetModules()
  })

  it('AppLayout.vue 可被导入', async () => {
    const module = await import('@/layouts/AppLayout.vue')
    expect(module.default).toBeDefined()
  }, 10_000)
})
