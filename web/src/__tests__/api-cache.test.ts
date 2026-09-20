import { describe, expect, it } from 'vitest'
import { __apiCacheForTest, resolveApiCachePolicy } from '@/api/cache'

describe('api cache policy', () => {
  it('uses long cache for generated pages and short cache for live pages', () => {
    expect(resolveApiCachePolicy({ url: '/sf/overview', method: 'get' })?.ttl).toBe(6 * 60 * 60)
    expect(resolveApiCachePolicy({ url: '/learning/summary', method: 'get' })?.namespace).toBe('learning')
    expect(resolveApiCachePolicy({ url: '/learning/flow-topology', method: 'get' })?.namespace).toBe('learning')
    expect(resolveApiCachePolicy({ url: '/learning/flow-journeys', method: 'get' })?.namespace).toBe('learning')
    expect(resolveApiCachePolicy({ url: '/learning/bottlenecks', method: 'get' })?.namespace).toBe('learning')
    expect(resolveApiCachePolicy({ url: '/learning/training-manifest', method: 'get' })?.namespace).toBe('learning')
    expect(resolveApiCachePolicy({ url: '/learning/automation-status', method: 'get' })?.namespace).toBe('learning')
    expect(resolveApiCachePolicy({ url: '/task-tree', method: 'get' })?.ttl).toBe(120)
    expect(resolveApiCachePolicy({ url: '/task-tree', method: 'get', cache: { refresh: true } } as any)?.ttl).toBe(120)
    expect(resolveApiCachePolicy({ url: '/task-tree', method: 'get', cache: false } as any)).toBeNull()
    expect(resolveApiCachePolicy({ url: '/training/jobs', method: 'get' })?.namespace).toBe('training')
    expect(resolveApiCachePolicy({ url: '/todos/', method: 'get' })).toBeNull()
    expect(resolveApiCachePolicy({ url: '/todos/stats', method: 'get', cache: { ttl: 20, namespace: 'todos' } } as any)?.ttl).toBe(20)
    expect(resolveApiCachePolicy({ url: '/inbox/overview', method: 'get' })).toBeNull()
    expect(resolveApiCachePolicy({ url: '/inbox/reports', method: 'get' })).toBeNull()
  })

  it('allows safe query posts but excludes generation and secret/file endpoints', () => {
    expect(resolveApiCachePolicy({ url: '/knowledge/search', method: 'post', data: { query: 'x' } })?.namespace).toBe('knowledge')
    expect(resolveApiCachePolicy({ url: '/knowledge/context', method: 'post', data: { query: 'x' } })?.namespace).toBe('knowledge')
    expect(resolveApiCachePolicy({ url: '/knowledge/ask', method: 'post', data: { query: 'x' } })).toBeNull()
    expect(resolveApiCachePolicy({ url: '/data-sources/connector-api-key', method: 'get' })).toBeNull()
    expect(resolveApiCachePolicy({ url: '/training/jobs/j1/artifacts/a1/download', method: 'get', responseType: 'blob' })).toBeNull()
  })

  it('maps mutations to affected namespaces', () => {
    expect(__apiCacheForTest.mutationNamespaces('/knowledge/documents')).toEqual(['knowledge', 'learning'])
    expect(__apiCacheForTest.mutationNamespaces('/executions/run')).toEqual(['execution', 'runtrace', 'tasktree', 'todos', 'inbox', 'sf', 'learning'])
    expect(__apiCacheForTest.mutationNamespaces('/skills/demo')).toEqual(['skills', 'hall', 'portal', 'training', 'agent', 'tasktree', 'learning'])
    expect(__apiCacheForTest.mutationNamespaces('/learning/events/backfill')).toEqual(['learning', 'knowledge', 'training', 'sf', 'agent'])
    expect(__apiCacheForTest.mutationNamespaces('/learning/automation/run')).toEqual(['learning', 'knowledge', 'training', 'sf', 'agent'])
  })

  it('boosts cache ttl as an endpoint gets hotter', () => {
    __apiCacheForTest.clear()
    expect(__apiCacheForTest.adaptiveApiCacheTtl(30, 1)).toBe(30)
    expect(__apiCacheForTest.adaptiveApiCacheTtl(30, 2)).toBe(30)
    expect(__apiCacheForTest.adaptiveApiCacheTtl(30, 3)).toBe(90)
    expect(__apiCacheForTest.adaptiveApiCacheTtl(6 * 60 * 60, 3)).toBe(6 * 60 * 60)

    expect(__apiCacheForTest.recordApiCacheAccess('knowledge:u1:get:/knowledge/bases::')).toBe(1)
    expect(__apiCacheForTest.recordApiCacheAccess('knowledge:u1:get:/knowledge/bases::')).toBe(2)
    expect(__apiCacheForTest.recordApiCacheAccess('knowledge:u1:get:/knowledge/bases::')).toBe(3)
  })

  it('persists only stable cache namespaces', () => {
    expect(__apiCacheForTest.canPersistPolicy({ namespace: 'learning', ttl: 60 })).toBe(true)
    expect(__apiCacheForTest.canPersistPolicy({ namespace: 'notifications', ttl: 20 })).toBe(false)
    expect(__apiCacheForTest.canPersistPolicy({ namespace: 'todos', ttl: 20 })).toBe(false)
  })
})
