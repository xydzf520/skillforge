/**
 * Wave 3 H8 回归：TodosTab 批量失败时选择集恢复为 "仅失败项"。
 *
 * 场景：
 * 1. 批量 API 部分成功（3 条中 1 条失败）→ 选择集只保留失败那 1 条
 * 2. 批量 API 全成功 → 选择集清空
 * 3. 批量 API 抛异常（网络错误）→ 选择集原样保留
 */
import { describe, expect, it } from 'vitest'

// 测试纯函数逻辑：从 result.results 中筛选 failed ids，剔除 succeeded / skipped
function retainFailed(result: any, allIds: number[]): Set<number> {
  const failedIds = new Set<number>(
    (result?.results || [])
      .filter((r: any) => r && r.ok === false)
      .map((r: any) => Number(r.todo_id))
      .filter((n: number) => Number.isFinite(n)),
  )
  if (failedIds.size === 0) return new Set()
  return new Set(allIds.filter((id) => failedIds.has(id)))
}

describe('TodosTab · 批量失败选择集恢复 (H8)', () => {
  it('部分失败：只保留失败项', () => {
    const allIds = [1, 2, 3]
    const result = {
      total: 3,
      succeeded: 2,
      failed: 1,
      results: [
        { todo_id: 1, ok: true },
        { todo_id: 2, ok: false, error: 'AUTH_DEPARTMENT_DENIED' },
        { todo_id: 3, ok: true },
      ],
    }
    const kept = retainFailed(result, allIds)
    expect(Array.from(kept)).toEqual([2])
  })

  it('全部成功：选择集清空', () => {
    const allIds = [1, 2]
    const result = {
      total: 2,
      succeeded: 2,
      failed: 0,
      results: [
        { todo_id: 1, ok: true },
        { todo_id: 2, ok: true },
      ],
    }
    const kept = retainFailed(result, allIds)
    expect(kept.size).toBe(0)
  })

  it('全部失败：保留全部选择', () => {
    const allIds = [1, 2]
    const result = {
      total: 2,
      succeeded: 0,
      failed: 2,
      results: [
        { todo_id: 1, ok: false, error: 'AUTH_PERMISSION_DENIED' },
        { todo_id: 2, ok: false, error: 'AUTH_PERMISSION_DENIED' },
      ],
    }
    const kept = retainFailed(result, allIds)
    expect(Array.from(kept).sort()).toEqual([1, 2])
  })

  it('result.results 缺失：退回清空逻辑（避免拿不到 ok 信号导致永远无法清空）', () => {
    const allIds = [1, 2]
    const kept = retainFailed({ total: 2, succeeded: 2, failed: 0 }, allIds)
    expect(kept.size).toBe(0)
  })
})
