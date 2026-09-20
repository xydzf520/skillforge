import { describe, expect, it, vi } from 'vitest'
import {
  computeInstanceAnomaly,
  computeInstanceMeta,
  HIGH_FAILURE_THRESHOLD,
  MIN_FAILURE_SAMPLE_SIZE,
  STUCK_THRESHOLD_MS,
} from '../helpers'
import { NodeStatus, SkillRunStatus, TaskTreeTimeWindow, type TaskTreeInstanceNode } from '../types'

function makeInstance(overrides: Partial<TaskTreeInstanceNode> = {}): TaskTreeInstanceNode {
  return {
    instance_id: 'inst-1',
    name: '实例一',
    node_status: NodeStatus.ONLINE,
    recent_skills: [],
    ...overrides,
  }
}

function makeRun(status: string, startedAt: string) {
  return {
    run_id: `${status}-${startedAt}`,
    skill_id: 'skill-1',
    skill_name: '技能一',
    status,
    started_at: startedAt,
  }
}

function makeRunWithCompleted(status: string, startedAt: string, completedAt: string) {
  return {
    ...makeRun(status, startedAt),
    completed_at: completedAt,
  }
}

describe('computeInstanceAnomaly', () => {
  const now = new Date('2026-04-16T12:00:00Z').getTime()

  it('offline 节点优先返回 offline', () => {
    const instance = makeInstance({ node_status: NodeStatus.OFFLINE })
    expect(computeInstanceAnomaly(instance, TaskTreeTimeWindow.ONE_DAY, now)).toBe('offline')
  })

  it('running 超过阈值返回 stuck', () => {
    const instance = makeInstance({
      recent_skills: [
        makeRun(
          SkillRunStatus.RUNNING,
          new Date(now - STUCK_THRESHOLD_MS - 60_000).toISOString(),
        ),
      ],
    })
    expect(computeInstanceAnomaly(instance, TaskTreeTimeWindow.ONE_DAY, now)).toBe('stuck')
  })

  it('窗口内失败率高返回 high_failure', () => {
    const runs = Array.from({ length: MIN_FAILURE_SAMPLE_SIZE }, (_, index) => {
      const startedAt = new Date(now - (index + 1) * 60_000).toISOString()
      return makeRun(index < Math.ceil((MIN_FAILURE_SAMPLE_SIZE + 1) * HIGH_FAILURE_THRESHOLD) ? SkillRunStatus.FAILED : SkillRunStatus.COMPLETED, startedAt)
    })
    const instance = makeInstance({ recent_skills: runs })
    expect(computeInstanceAnomaly(instance, TaskTreeTimeWindow.ONE_DAY, now)).toBe('high_failure')
  })

  it('样本不足时保持 healthy', () => {
    const instance = makeInstance({
      recent_skills: [
        makeRun(SkillRunStatus.FAILED, new Date(now - 60_000).toISOString()),
        makeRun(SkillRunStatus.COMPLETED, new Date(now - 120_000).toISOString()),
      ],
    })
    expect(computeInstanceAnomaly(instance, TaskTreeTimeWindow.ONE_DAY, now)).toBe('healthy')
  })

  it('窗口外失败不计入当前时间窗', () => {
    const instance = makeInstance({
      recent_skills: Array.from({ length: MIN_FAILURE_SAMPLE_SIZE }, (_, index) =>
        makeRun(
          index < MIN_FAILURE_SAMPLE_SIZE - 1 ? SkillRunStatus.FAILED : SkillRunStatus.COMPLETED,
          new Date(now - (25 * 60 * 60 * 1000) - index * 60_000).toISOString(),
        )),
    })
    expect(computeInstanceAnomaly(instance, TaskTreeTimeWindow.ONE_DAY, now)).toBe('healthy')
  })

  it('正常节点返回 healthy', () => {
    vi.useFakeTimers()
    vi.setSystemTime(now)
    const instance = makeInstance({
      recent_skills: [
        makeRun(SkillRunStatus.COMPLETED, new Date(now - 60_000).toISOString()),
      ],
    })
    expect(computeInstanceAnomaly(instance, TaskTreeTimeWindow.ONE_DAY, now)).toBe('healthy')
    vi.useRealTimers()
  })
})


describe('computeInstanceMeta', () => {
  const now = new Date('2026-04-16T12:00:00Z').getTime()

  it('仅在当前时间窗内展示最近异常', () => {
    const instance = makeInstance({
      recent_skills: [
        makeRun(SkillRunStatus.COMPLETED, new Date(now - 60_000).toISOString()),
        makeRun(SkillRunStatus.FAILED, new Date(now - 25 * 60 * 60 * 1000).toISOString()),
      ],
    })

    const meta = computeInstanceMeta(instance, TaskTreeTimeWindow.ONE_DAY, now)

    expect(meta.failedInWindow).toBe(0)
    expect(meta.latestAnomalyRun).toBeNull()
  })

  it('后续成功执行覆盖同窗旧失败，不再展示最近异常', () => {
    const instance = makeInstance({
      recent_skills: [
        makeRun(SkillRunStatus.COMPLETED, new Date(now - 60_000).toISOString()),
        makeRun(SkillRunStatus.FAILED, new Date(now - 30 * 60_000).toISOString()),
      ],
    })

    const meta = computeInstanceMeta(instance, TaskTreeTimeWindow.ONE_DAY, now)

    expect(meta.failedInWindow).toBe(1)
    expect(meta.consecutiveFailures).toBe(0)
    expect(meta.latestAnomalyRun).toBeNull()
  })

  it('长任务按完成时间覆盖中途 fallback 失败', () => {
    const instance = makeInstance({
      recent_skills: [
        makeRunWithCompleted(
          SkillRunStatus.COMPLETED,
          new Date(now - 33 * 60_000).toISOString(),
          new Date(now - 60_000).toISOString(),
        ),
        makeRun(SkillRunStatus.FAILED, new Date(now - 28 * 60_000).toISOString()),
      ],
    })

    const meta = computeInstanceMeta(instance, TaskTreeTimeWindow.ONE_DAY, now)

    expect(meta.failedInWindow).toBe(1)
    expect(meta.consecutiveFailures).toBe(0)
    expect(meta.latestAnomalyRun).toBeNull()
  })
})
