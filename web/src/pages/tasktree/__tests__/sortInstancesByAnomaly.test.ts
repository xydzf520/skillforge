import { describe, expect, it } from 'vitest'
import { sortInstancesByAnomaly } from '../helpers'
import { NodeStatus, SkillRunStatus, TaskTreeTimeWindow, type TaskTreeInstanceNode } from '../types'

function makeInstance(name: string, overrides: Partial<TaskTreeInstanceNode> = {}): TaskTreeInstanceNode {
  return {
    instance_id: name,
    name,
    node_status: NodeStatus.ONLINE,
    recent_skills: [],
    ...overrides,
  }
}

describe('sortInstancesByAnomaly', () => {
  it('按 offline > stuck > high_failure > healthy 排序', () => {
    const now = new Date('2026-04-16T12:00:00Z').getTime()
    const instances: TaskTreeInstanceNode[] = [
      makeInstance('healthy'),
      makeInstance('high_failure', {
        recent_skills: Array.from({ length: 5 }, (_, index) => ({
          run_id: `hf-${index}`,
          skill_id: 'skill-hf',
          skill_name: '失败率高',
          status: index < 2 ? SkillRunStatus.FAILED : SkillRunStatus.COMPLETED,
          started_at: new Date(now - (index + 1) * 60_000).toISOString(),
        })),
      }),
      makeInstance('stuck', {
        recent_skills: [{
          run_id: 'stuck-1',
          skill_id: 'skill-stuck',
          skill_name: '卡住',
          status: SkillRunStatus.RUNNING,
          started_at: new Date(now - 20 * 60_000).toISOString(),
        }],
      }),
      makeInstance('offline', { node_status: NodeStatus.OFFLINE }),
    ]

    const names = sortInstancesByAnomaly(instances, TaskTreeTimeWindow.ONE_DAY, now).map((item) => item.name)
    expect(names).toEqual(['offline', 'stuck', 'high_failure', 'healthy'])
  })
})
