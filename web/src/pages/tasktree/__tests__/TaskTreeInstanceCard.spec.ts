import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import TaskTreeInstanceCard from '../TaskTreeInstanceCard.vue'
import { STUCK_THRESHOLD_MS } from '../helpers'
import { NodeStatus, SkillRunStatus, TaskTreeTimeWindow, type TaskTreeInstanceNode } from '../types'

const stubs = {
  'a-tag': {
    props: ['color', 'size'],
    template: '<span class="a-tag" :data-color="color"><slot /></span>',
  },
}

function makeInstance(overrides: Partial<TaskTreeInstanceNode> = {}): TaskTreeInstanceNode {
  return {
    instance_id: 'inst-1',
    name: '主节点',
    department: '研发部',
    node_status: NodeStatus.ONLINE,
    heartbeat_ago_text: '30s 前',
    bridge_version: '1.0.0',
    bridge_platform: 'linux',
    recent_skills: [],
    ...overrides,
  }
}

describe('TaskTreeInstanceCard', () => {
  it('offline 节点渲染红色异常态 class', () => {
    const wrapper = mount(TaskTreeInstanceCard, {
      global: { stubs },
      props: {
        instance: makeInstance({ node_status: NodeStatus.OFFLINE }),
        window: TaskTreeTimeWindow.ONE_DAY,
        selected: false,
      },
    })
    expect(wrapper.classes()).toContain('anomaly-offline')
    expect(wrapper.attributes('data-anomaly')).toBe('offline')
  })

  it('失败运行显示最近异常摘要', () => {
    const recentFailureAt = new Date(Date.now() - 5 * 60 * 1000).toISOString()
    const wrapper = mount(TaskTreeInstanceCard, {
      global: { stubs },
      props: {
        instance: makeInstance({
          recent_skills: [{
            run_id: 'run-1',
            skill_id: 'skill-1',
            skill_name: 'ad_roi_check',
            status: SkillRunStatus.FAILED,
            started_at: recentFailureAt,
            error_message: '参数校验失败：campaign_id 缺失',
          }],
        }),
        window: TaskTreeTimeWindow.ONE_DAY,
        selected: false,
      },
    })
    expect(wrapper.text()).toContain('最近异常')
    expect(wrapper.text()).toContain('ad_roi_check')
  })

  it('运行超时的任务显示卡住摘要而不是无异常', async () => {
    const now = new Date('2026-05-14T05:10:00.000Z')
    vi.useFakeTimers()
    vi.setSystemTime(now)
    const wrapper = mount(TaskTreeInstanceCard, {
      global: { stubs },
      props: {
        instance: makeInstance({
          recent_skills: [{
            run_id: 'run-stuck',
            skill_id: 'skill-1',
            skill_name: '天猫店铺链接下滑分析',
            status: SkillRunStatus.RUNNING,
            started_at: new Date(now.getTime() - STUCK_THRESHOLD_MS - 1000).toISOString(),
          }],
        }),
        window: TaskTreeTimeWindow.ONE_DAY,
        selected: false,
      },
    })

    expect(wrapper.classes()).toContain('anomaly-stuck')
    expect(wrapper.text()).toContain('运行卡住')
    expect(wrapper.text()).not.toContain('无异常')

    await wrapper.find('button.tt-card-foot--alert').trigger('click')
    expect(wrapper.emitted('open-diagnosis')?.[0]?.[1]).toMatchObject({ run_id: 'run-stuck' })
    vi.useRealTimers()
  })
})
