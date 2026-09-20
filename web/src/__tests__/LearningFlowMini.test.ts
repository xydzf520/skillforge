import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  learningApi: {
    lineage: vi.fn(),
  },
  routerPush: vi.fn(),
}))

vi.mock('@/api', () => ({
  learningApi: mocks.learningApi,
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mocks.routerPush }),
}))

import LearningFlowMini from '@/components/learning/LearningFlowMini.vue'

describe('LearningFlowMini', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.learningApi.lineage.mockResolvedValue({
      edges: [
        { id: 0, from_type: 'agent', from_id: 'agent-training-1', to_type: 'training_job', to_id: 'tj-1', relation: 'controlled_training' },
        { id: 1, from_type: 'learning_artifact', from_id: 'la-sample-1', to_type: 'training_job', to_id: 'tj-1', relation: 'trained_from' },
        { id: 2, from_type: 'training_job', from_id: 'tj-1', to_type: 'model_artifact', to_id: 'artifact-1', relation: 'produced_artifact' },
        { id: 3, from_type: 'model_artifact', from_id: 'artifact-1', to_type: 'model_deployment', to_id: 'md-1', relation: 'deployed_to' },
        { id: 4, from_type: 'dingtalk_feedback', from_id: 'ding:todo-1', to_type: 'todo', to_id: 'todo-1', relation: 'submitted_feedback' },
      ],
    })
  })

  it('loads lineage and opens the full data-flow page', async () => {
    const wrapper = mount(LearningFlowMini, {
      props: {
        entityType: 'training_job',
        entityId: 'tj-1',
        title: '训练数据流',
      },
      global: {
        stubs: {
          SfShellIcon: { props: ['name'], template: '<i>{{ name }}</i>' },
        },
      },
    })
    await flushPromises()

    expect(mocks.learningApi.lineage).toHaveBeenCalledWith('training_job', 'tj-1', { limit: 12 })
    expect(wrapper.text()).toContain('训练数据流')
    expect(wrapper.text()).toContain('训练 Agent 控制')
    expect(wrapper.text()).toContain('训练来源')
    expect(wrapper.text()).toContain('产出数据/模型')
    expect(wrapper.text()).toContain('部署')
    expect(wrapper.text()).toContain('反馈回传')
    expect(wrapper.text()).toContain('钉钉反馈')
    expect(wrapper.text()).toContain('模型产物')
    expect(wrapper.text()).toContain('原始数据false')

    const openButton = wrapper.findAll('button').find((item) => item.text().includes('打开数据流'))
    expect(openButton).toBeTruthy()
    await openButton!.trigger('click')
    expect(mocks.routerPush).toHaveBeenCalledWith({
      path: '/learning-flow',
      query: { entity_type: 'training_job', entity_id: 'tj-1' },
    })
  })
})
