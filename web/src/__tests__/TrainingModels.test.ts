import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  trainingApi: {
    deployments: vi.fn(),
    jobs: vi.fn(),
    requestDeployment: vi.fn(),
    downloadJobArtifact: vi.fn(),
    approveDeployment: vi.fn(),
    activateDeployment: vi.fn(),
    rejectDeployment: vi.fn(),
    rollbackDeployment: vi.fn(),
    syncDeploymentOpenWebUI: vi.fn(),
  },
  router: {
    push: vi.fn(),
  },
}))

vi.mock('@/api', () => ({
  trainingApi: mocks.trainingApi,
}))

vi.mock('vue-router', () => ({
  useRouter: () => mocks.router,
}))

vi.mock('@arco-design/web-vue', () => ({
  Message: {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
  },
  Modal: {
    confirm: vi.fn((options: any) => options?.onOk?.()),
  },
}))

vi.mock('@arco-design/web-vue/es/icon', () => ({
  IconClose: { template: '<span />' },
  IconLeft: { template: '<span />' },
  IconRefresh: { template: '<span />' },
  IconSend: { template: '<span />' },
}))

import TrainingModels from '@/pages/training/TrainingModels.vue'

function deploymentPayload(overrides: Record<string, unknown> = {}) {
  return {
    id: 'deploy-1',
    job_id: 'train-1',
    department: 'EC',
    model_family: 'item_decline_ranker',
    artifact_id: 'artifact-1',
    artifact_ref: { sha256: 'a'.repeat(64) },
    target_skill_ids: ['skill-recommend'],
    status: 'awaiting_review',
    rollout_percent: 0,
    rollback_to: '',
    updated_at: '2026-05-21T11:00:00+08:00',
    job: {
      id: 'train-1',
      title: '动作推荐训练',
      status: 'completed',
      target_skill_id: 'skill-recommend',
      target_gateway_id: 'node-1',
    },
    ...overrides,
  }
}

function stubs() {
  return {
    SfPageHeader: { props: ['title', 'subtitle'], template: '<header><h1>{{ title }}</h1><p>{{ subtitle }}</p><slot name="actions" /></header>' },
    SfKpiCard: { props: ['label', 'value', 'hint'], template: '<section><b>{{ label }}</b><span>{{ value }}</span><small>{{ hint }}</small></section>' },
    SfEmptyState: { props: ['title', 'description', 'hint'], template: '<div>{{ title }}{{ description }}{{ hint }}</div>' },
    'a-button': {
      props: ['loading', 'type', 'status', 'disabled'],
      emits: ['click'],
      template: '<button :disabled="loading || disabled" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>',
    },
    'a-card': { props: ['title'], template: '<section><h2>{{ title }}</h2><slot /></section>' },
    'a-col': { template: '<div><slot /></div>' },
    'a-link': { emits: ['click'], template: '<a @click="$emit(\'click\')"><slot /></a>' },
    'a-modal': {
      props: ['visible', 'title', 'okLoading'],
      emits: ['ok', 'cancel'],
      template: '<section v-if="visible"><h3>{{ title }}</h3><slot /><button class="modal-ok" :disabled="okLoading" @click="$emit(\'ok\')">确定</button></section>',
    },
    'a-result': { props: ['title'], template: '<div>{{ title }}</div>' },
    'a-row': { template: '<div><slot /></div>' },
    'a-space': { template: '<div><slot /></div>' },
    'a-spin': { template: '<div><slot /></div>' },
    'a-table': {
      props: ['data'],
      provide(this: any) {
        return { tableData: this.data || [] }
      },
      template: '<table><slot name="columns" /></table>',
    },
    'a-table-column': {
      props: ['title'],
      inject: { tableData: { default: () => [] } },
      template: '<th>{{ title }}<div v-for="record in tableData" :key="record.id"><slot name="cell" :record="record" /></div></th>',
    },
    'a-tag': { template: '<span><slot /></span>' },
    'a-tooltip': { template: '<span><slot /></span>' },
    'a-textarea': {
      props: ['modelValue'],
      emits: ['update:modelValue'],
      template: '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    },
  }
}

describe('TrainingModels', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.trainingApi.deployments.mockResolvedValue({
      items: [deploymentPayload()],
      stats: { awaiting_review: 1, canary: 0, active: 0 },
    })
    mocks.trainingApi.jobs.mockResolvedValue({
      items: [
        {
          id: 'train-1',
          title: '动作推荐训练',
          department: 'EC',
          status: 'completed',
          target_skill_id: 'skill-recommend',
          target_gateway_id: 'node-1',
          updated_at: '2026-05-21T11:00:00+08:00',
          training_plan: { model_name: 'item_decline_ranker' },
          latest_deployment: deploymentPayload(),
        },
        {
          id: 'train-ready',
          title: '全量数据 4B 微调 第 3 轮',
          department: 'AI平台',
          status: 'completed',
          target_skill_id: 'skillforge-finetuned-model-chat',
          target_gateway_id: 'training-primary',
          updated_at: '2026-08-09T10:00:00+08:00',
          training_plan: {
            model_name: 'skillforge-full-history-4b-chat',
            artifact: { id: 'artifact-ready', name: 'adapter.safetensors', sha256: 'b'.repeat(64) },
            artifacts_count: 1,
          },
        },
      ],
    })
    mocks.trainingApi.requestDeployment.mockResolvedValue({
      deployment: deploymentPayload({
        id: 'deploy-ready',
        job_id: 'train-ready',
        department: 'AI平台',
        model_family: 'skillforge-full-history-4b-chat',
        artifact_id: 'artifact-ready',
        artifact_ref: { id: 'artifact-ready', sha256: 'b'.repeat(64) },
        target_skill_ids: ['skillforge-finetuned-model-chat'],
        status: 'awaiting_review',
      }),
      job: {
        id: 'train-ready',
        title: '全量数据 4B 微调 第 3 轮',
        department: 'AI平台',
        status: 'completed',
        target_skill_id: 'skillforge-finetuned-model-chat',
        target_gateway_id: 'training-primary',
        training_plan: {
          model_name: 'skillforge-full-history-4b-chat',
          artifact: { id: 'artifact-ready', name: 'adapter.safetensors', sha256: 'b'.repeat(64) },
          artifacts_count: 1,
        },
      },
    })
  })

  it('renders deployment registry rows with job summary and artifact metadata', async () => {
    const wrapper = mount(TrainingModels, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    expect(mocks.trainingApi.deployments).toHaveBeenCalled()
    expect(mocks.trainingApi.jobs).toHaveBeenCalledWith({})
    expect(wrapper.text()).toContain('模型库')
    expect(wrapper.text()).toContain('item_decline_ranker')
    expect(wrapper.text()).toContain('部署待审')
    expect(wrapper.text()).toContain('skillforge-full-history-4b-chat')
    expect(wrapper.text()).toContain('模型待部署')
    expect(wrapper.text()).toContain('动作推荐训练')
    expect(wrapper.text()).toContain('sha256')
  })

  it('shows completed training artifacts as deployable model candidates', async () => {
    const wrapper = mount(TrainingModels, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const deployButton = wrapper.findAll('button').find(item => item.text() === '部署')
    expect(deployButton?.exists()).toBe(true)
    await deployButton?.trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.requestDeployment).toHaveBeenCalledWith('train-ready', {
      target_skill_ids: ['skillforge-finetuned-model-chat'],
      reason: '模型库提交部署审批',
      rollout_percent: 0,
    })
  })

  it('approves deployment and updates registry state in place', async () => {
    mocks.trainingApi.approveDeployment.mockResolvedValue({
      deployment: deploymentPayload({ status: 'canary', rollout_percent: 10 }),
      job: {
        id: 'train-1',
        title: '动作推荐训练',
        status: 'completed',
        target_skill_id: 'skill-recommend',
        target_gateway_id: 'node-1',
      },
    })
    const wrapper = mount(TrainingModels, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const approveButton = wrapper.findAll('button').find(item => item.text().includes('审批'))
    expect(approveButton?.exists()).toBe(true)
    await approveButton?.trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.approveDeployment).toHaveBeenCalledWith('deploy-1')
    expect(wrapper.text()).toContain('灰度中')
  })

  it('rejects deployment approval requests from the registry', async () => {
    mocks.trainingApi.rejectDeployment.mockResolvedValue({
      deployment: deploymentPayload({
        status: 'rejected',
        rejected_by: 'admin',
        reject_reason: '部署审批未通过，退回训练产物重新确认',
      }),
      job: {
        id: 'train-1',
        title: '动作推荐训练',
        status: 'completed',
        target_skill_id: 'skill-recommend',
        target_gateway_id: 'node-1',
      },
    })
    const wrapper = mount(TrainingModels, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const rejectButton = wrapper.findAll('button').find(item => item.text().includes('驳回'))
    expect(rejectButton?.exists()).toBe(true)
    await rejectButton?.trigger('click')
    await flushPromises()
    await wrapper.find('textarea').setValue('评估窗口业务指标不稳定，暂不进入灰度')
    await wrapper.find('.modal-ok').trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.rejectDeployment).toHaveBeenCalledWith(
      'deploy-1',
      '评估窗口业务指标不稳定，暂不进入灰度',
    )
  })

  it('navigates from model registry to the training job detail', async () => {
    const wrapper = mount(TrainingModels, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const jobLink = wrapper.findAll('a').find(item => item.text().includes('动作推荐训练'))
    expect(jobLink?.exists()).toBe(true)
    await jobLink?.trigger('click')

    expect(mocks.router.push).toHaveBeenCalledWith('/training/jobs/train-1')
  })

  it('navigates from model registry to deployment detail', async () => {
    const wrapper = mount(TrainingModels, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const deploymentLink = wrapper.findAll('a').find(item => item.text().includes('item_decline_ranker'))
    expect(deploymentLink?.exists()).toBe(true)
    await deploymentLink?.trigger('click')

    expect(mocks.router.push).toHaveBeenCalledWith('/training/deployments/deploy-1')
  })

  it('opens model chat with deployment department context', async () => {
    mocks.trainingApi.deployments.mockResolvedValueOnce({
      items: [deploymentPayload({ status: 'active', artifact_ref: { id: 'artifact-1', sha256: 'a'.repeat(64) } })],
      stats: { awaiting_review: 0, canary: 0, active: 1 },
    })
    mocks.trainingApi.jobs.mockResolvedValueOnce({ items: [] })
    const wrapper = mount(TrainingModels, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const chatButton = wrapper.findAll('button').find(item => item.text().includes('对话') && !item.attributes('disabled'))
    expect(chatButton?.exists()).toBe(true)
    await chatButton?.trigger('click')

    expect(mocks.router.push).toHaveBeenCalledWith(
      '/agent?model_deployment_id=deploy-1&model_family=item_decline_ranker&training_job_id=train-1&department=EC&artifact_id=artifact-1',
    )
  })

  it('keeps model chat visible with a readiness reason before deployment is live', async () => {
    const wrapper = mount(TrainingModels, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Agent 路由')
    expect(wrapper.text()).toContain('未就绪')
    expect(wrapper.text()).toContain('部署未进入灰度或已部署')
    const chatButton = wrapper.findAll('button').find(item => item.text().includes('对话'))
    expect(chatButton?.exists()).toBe(true)
    expect(chatButton?.attributes('disabled')).toBeDefined()
  })
})
