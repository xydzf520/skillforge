import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import RunTimelineStrip from '../RunTimelineStrip.vue'
import { SkillRunStatus, TaskTreeTimeWindow, type TaskTreeSkillRunItem } from '../types'

function makeRun(id: string, status: string, startedAt: string): TaskTreeSkillRunItem {
  return {
    run_id: id,
    skill_id: `skill-${id}`,
    skill_name: `技能-${id}`,
    status,
    started_at: startedAt,
  }
}

describe('RunTimelineStrip', () => {
  it('24h 视图渲染 48 个 bucket，且按状态着色', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-04-16T12:00:00Z'))

    const runs = [
      makeRun('failed', SkillRunStatus.FAILED, '2026-04-16T11:35:00Z'),
      makeRun('running', SkillRunStatus.RUNNING, '2026-04-16T11:05:00Z'),
      makeRun('success', SkillRunStatus.COMPLETED, '2026-04-16T10:35:00Z'),
    ]

    const wrapper = mount(RunTimelineStrip, {
      props: {
        runs,
        window: TaskTreeTimeWindow.ONE_DAY,
        showAxis: true,
      },
    })

    const buckets = wrapper.findAll('[data-state]')
    expect(buckets).toHaveLength(48)
    expect(buckets.some((bucket) => bucket.attributes('data-state') === 'failed')).toBe(true)
    expect(buckets.some((bucket) => bucket.attributes('data-state') === 'running')).toBe(true)
    expect(buckets.some((bucket) => bucket.attributes('data-state') === 'success')).toBe(true)
    expect(wrapper.find('.tt-timeline-axis').text()).toBe('24h前18h前12h前6h前现在')

    vi.useRealTimers()
  })
})
