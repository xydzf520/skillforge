import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  computeInstanceAnomaly,
  sortInstancesByAnomaly,
  STUCK_THRESHOLD_MS,
} from '../helpers'
import { NodeStatus, TaskTreeTimeWindow, type TaskTreeInstanceNode, type TaskTreeSkillRunItem } from '../types'

function minutesAgo(minutes: number): string {
  return new Date(Date.now() - (minutes * 60 * 1000)).toISOString()
}

function makeRun(overrides: Partial<TaskTreeSkillRunItem> = {}): TaskTreeSkillRunItem {
  return {
    run_id: `run-${Math.random()}`,
    skill_id: 'skill-1',
    skill_name: 'ROI检查',
    status: 'completed',
    started_at: minutesAgo(5),
    ...overrides,
  }
}

function makeInstance(overrides: Partial<TaskTreeInstanceNode> = {}): TaskTreeInstanceNode {
  return {
    instance_id: `inst-${Math.random()}`,
    name: 'OpenClaw',
    node_status: NodeStatus.ONLINE,
    recent_skills: [],
    ...overrides,
  }
}

describe('computeInstanceAnomaly', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-04-16T12:00:00.000Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('offline / maybe_offline 优先标记为 offline', () => {
    expect(computeInstanceAnomaly(makeInstance({ node_status: NodeStatus.OFFLINE }), TaskTreeTimeWindow.ONE_DAY)).toBe('offline')
    expect(computeInstanceAnomaly(makeInstance({ node_status: NodeStatus.MAYBE_OFFLINE }), TaskTreeTimeWindow.ONE_DAY)).toBe('offline')
  })

  it('运行超过 10 分钟时标记为 stuck', () => {
    const instance = makeInstance({
      recent_skills: [
        makeRun({
          status: 'running',
          started_at: new Date(Date.now() - STUCK_THRESHOLD_MS - 1000).toISOString(),
        }),
      ],
    })

    expect(computeInstanceAnomaly(instance, TaskTreeTimeWindow.ONE_DAY)).toBe('stuck')
  })

  it('5 条以上样本且失败率超过 20% 时标记为 high_failure', () => {
    const instance = makeInstance({
      recent_skills: [
        makeRun({ status: 'failed', started_at: minutesAgo(10) }),
        makeRun({ status: 'failed', started_at: minutesAgo(20) }),
        makeRun({ started_at: minutesAgo(30) }),
        makeRun({ started_at: minutesAgo(40) }),
        makeRun({ started_at: minutesAgo(50) }),
      ],
    })

    expect(computeInstanceAnomaly(instance, TaskTreeTimeWindow.ONE_DAY)).toBe('high_failure')
  })

  it('样本不足时即使有失败也保持 healthy', () => {
    const instance = makeInstance({
      recent_skills: [
        makeRun({ status: 'failed', started_at: minutesAgo(10) }),
        makeRun({ started_at: minutesAgo(20) }),
        makeRun({ started_at: minutesAgo(30) }),
        makeRun({ started_at: minutesAgo(40) }),
      ],
    })

    expect(computeInstanceAnomaly(instance, TaskTreeTimeWindow.ONE_DAY)).toBe('healthy')
  })

  it('健康实例保持 healthy', () => {
    const instance = makeInstance({
      recent_skills: [
        makeRun({ started_at: minutesAgo(10) }),
        makeRun({ started_at: minutesAgo(20) }),
        makeRun({ started_at: minutesAgo(30) }),
        makeRun({ started_at: minutesAgo(40) }),
        makeRun({ started_at: minutesAgo(50) }),
      ],
    })

    expect(computeInstanceAnomaly(instance, TaskTreeTimeWindow.ONE_DAY)).toBe('healthy')
  })

  it('按异常严重度排序：offline > stuck > high_failure > healthy', () => {
    const items = sortInstancesByAnomaly([
      makeInstance({ instance_id: 'healthy', name: 'healthy' }),
      makeInstance({
        instance_id: 'high-failure',
        name: 'high-failure',
        recent_skills: [
          makeRun({ status: 'failed', started_at: minutesAgo(10) }),
          makeRun({ status: 'failed', started_at: minutesAgo(20) }),
          makeRun({ started_at: minutesAgo(30) }),
          makeRun({ started_at: minutesAgo(40) }),
          makeRun({ started_at: minutesAgo(50) }),
        ],
      }),
      makeInstance({
        instance_id: 'stuck',
        name: 'stuck',
        recent_skills: [makeRun({ status: 'running', started_at: new Date(Date.now() - STUCK_THRESHOLD_MS - 1000).toISOString() })],
      }),
      makeInstance({
        instance_id: 'offline',
        name: 'offline',
        node_status: NodeStatus.OFFLINE,
      }),
    ], TaskTreeTimeWindow.ONE_DAY)

    expect(items.map((item) => item.instance_id)).toEqual(['offline', 'stuck', 'high-failure', 'healthy'])
  })
})
