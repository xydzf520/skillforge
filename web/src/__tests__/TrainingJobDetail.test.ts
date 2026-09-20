import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  route: {
    params: { id: 'train-1' },
  },
  router: {
    push: vi.fn(),
  },
  trainingApi: {
    getJob: vi.fn(),
    jobLogs: vi.fn(),
    jobArtifacts: vi.fn(),
    approveJob: vi.fn(),
    dispatchJob: vi.fn(),
    retryJob: vi.fn(),
    collectResult: vi.fn(),
    evaluateJob: vi.fn(),
    requestDeployment: vi.fn(),
    approveDeployment: vi.fn(),
    activateDeployment: vi.fn(),
    rejectDeployment: vi.fn(),
    rollbackDeployment: vi.fn(),
    cancelJob: vi.fn(),
    forceCancelJob: vi.fn(),
  },
  learningApi: {
    lineage: vi.fn(),
  },
}))

vi.mock('@/api', () => ({
  trainingApi: mocks.trainingApi,
  learningApi: mocks.learningApi,
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
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
  IconApps: { template: '<span />' },
  IconBook: { template: '<span />' },
  IconClose: { template: '<span />' },
  IconFile: { template: '<span />' },
  IconLeft: { template: '<span />' },
  IconRefresh: { template: '<span />' },
  IconRobot: { template: '<span />' },
  IconSend: { template: '<span />' },
}))

import TrainingJobDetail from '@/pages/training/TrainingJobDetail.vue'

function jobPayload(overrides: Record<string, unknown> = {}) {
  return {
    id: 'train-1',
    title: '训练商品下滑动作推荐模型',
    department: 'EC',
    created_by: 'admin',
    status: 'completed',
    job_type: 'lora',
    training_strategy: 'lora_task_parallel',
    target_skill_id: 'skill-recommend',
    target_gateway_id: 'node-1',
    dataset_ref: 'dataset://ec/actions/v1',
    objective: '提升动作推荐准确率',
    risk_level: 'R2',
    failure_stage: '',
    spec: { eval_gate: { min_win_rate: 0.55 } },
    gateway_payload: { job_id: 'train-1', control: { callback_token: '***' } },
    approved_by: 'admin',
    approved_at: '2026-05-21T10:00:00+08:00',
    created_at: '2026-05-21T09:00:00+08:00',
    updated_at: '2026-05-21T11:00:00+08:00',
    tasks: [
      {
        id: 1,
        gateway_id: 'node-1',
        worker_id: 'worker-1',
        status: 'completed',
        progress: 1,
        metrics: { gateway_result: { metrics: { win_rate: 0.61 }, artifacts: [] } },
        updated_at: '2026-05-21T10:30:00+08:00',
      },
      {
        id: 2,
        gateway_id: 'node-1',
        worker_id: '',
        status: 'completed',
        progress: 1,
        metrics: { op: 'training.evaluate', passed: true, checks: [{ name: 'min_win_rate', passed: true }] },
        updated_at: '2026-05-21T11:00:00+08:00',
      },
    ],
    deployments: [{
      id: 'deploy-1',
      status: 'awaiting_review',
      model_family: 'item_decline_ranker',
      artifact_id: 'artifact-1',
      rollout_percent: 0,
      target_skill_ids: ['skill-recommend'],
    }],
    latest_deployment: {
      id: 'deploy-1',
      status: 'awaiting_review',
      model_family: 'item_decline_ranker',
      artifact_id: 'artifact-1',
      rollout_percent: 0,
      target_skill_ids: ['skill-recommend'],
    },
    ...overrides,
  }
}

function stubs() {
  return {
    SfPageHeader: { props: ['title', 'subtitle'], template: '<header><h1>{{ title }}</h1><p>{{ subtitle }}</p><slot name="actions" /></header>' },
    SfKpiCard: { props: ['label', 'value', 'hint', 'suffix'], template: '<section><b>{{ label }}</b><span>{{ value }}</span><small>{{ hint }}</small><i>{{ suffix }}</i></section>' },
    SfEmptyState: { props: ['title', 'description', 'hint'], template: '<div>{{ title }}{{ description }}{{ hint }}</div>' },
    LearningFlowMini: { template: '<section data-test="learning-flow-mini" />' },
    'a-button': {
      props: ['loading', 'type', 'status'],
      emits: ['click'],
      template: '<button :disabled="loading" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>',
    },
    'a-card': { props: ['title'], template: '<section><h2>{{ title }}</h2><slot /></section>' },
    'a-col': { template: '<div><slot /></div>' },
    'a-descriptions': { template: '<dl><slot /></dl>' },
    'a-descriptions-item': { props: ['label'], template: '<div><dt>{{ label }}</dt><dd><slot /></dd></div>' },
    'a-modal': {
      props: ['visible', 'title', 'okLoading'],
      emits: ['ok', 'cancel'],
      template: '<section v-if="visible"><h3>{{ title }}</h3><slot /><button class="modal-ok" :disabled="okLoading" @click="$emit(\'ok\')">确定</button></section>',
    },
    'a-progress': { props: ['percent'], template: '<progress :value="percent" />' },
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
    'a-textarea': {
      props: ['modelValue'],
      emits: ['update:modelValue'],
      template: '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    },
  }
}

describe('TrainingJobDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.trainingApi.getJob.mockResolvedValue(jobPayload())
    mocks.learningApi.lineage.mockResolvedValue({ edges: [] })
  })

  it('renders the training job detail route with tasks, evaluation and deployment state', async () => {
    const wrapper = mount(TrainingJobDetail, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    expect(mocks.trainingApi.getJob).toHaveBeenCalledWith('train-1')
    expect(wrapper.text()).toContain('训练商品下滑动作推荐模型')
    expect(wrapper.text()).toContain('阶段时间线')
    expect(wrapper.text()).toContain('Worker Tasks')
    expect(wrapper.text()).toContain('评估门禁')
    expect(wrapper.text()).toContain('部署待审')
    expect(wrapper.text()).toContain('callback_token')
    expect(wrapper.text()).toContain('***')
  })

  it('links the job back to its gateway task pool and Agent terminal', async () => {
    const wrapper = mount(TrainingJobDetail, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const backButton = wrapper.findAll('button').find(item => item.text().includes('返回'))
    const gatewayButton = wrapper.findAll('button').find(item => item.text().includes('网关任务'))
    const agentButton = wrapper.findAll('button').find(item => item.text().includes('Agent 终端'))
    expect(backButton?.exists()).toBe(true)
    expect(gatewayButton?.exists()).toBe(true)
    expect(agentButton?.exists()).toBe(true)

    await backButton?.trigger('click')
    await gatewayButton?.trigger('click')
    await agentButton?.trigger('click')

    expect(mocks.router.push).toHaveBeenNthCalledWith(1, '/training?gateway=node-1')
    expect(mocks.router.push).toHaveBeenNthCalledWith(2, '/training?gateway=node-1')
    expect(mocks.router.push).toHaveBeenNthCalledWith(3, '/admin/agent-devices/node-1')
  })

  it('approves the latest deployment from the detail page and refreshes local state', async () => {
    mocks.trainingApi.approveDeployment.mockResolvedValue({
      job: jobPayload({
        deployments: [{ id: 'deploy-1', status: 'canary', model_family: 'item_decline_ranker', rollout_percent: 10 }],
        latest_deployment: { id: 'deploy-1', status: 'canary', model_family: 'item_decline_ranker', rollout_percent: 10 },
      }),
    })
    const wrapper = mount(TrainingJobDetail, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const button = wrapper.findAll('button').find(item => item.text().includes('审批部署'))
    expect(button?.exists()).toBe(true)
    await button?.trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.approveDeployment).toHaveBeenCalledWith('deploy-1')
    expect(wrapper.text()).toContain('灰度中')
  })

  it('rejects the latest deployment from the detail page and refreshes local state', async () => {
    mocks.trainingApi.rejectDeployment.mockResolvedValue({
      job: jobPayload({
        deployments: [{ id: 'deploy-1', status: 'rejected', model_family: 'item_decline_ranker', rollout_percent: 0 }],
        latest_deployment: { id: 'deploy-1', status: 'rejected', model_family: 'item_decline_ranker', rollout_percent: 0 },
      }),
    })
    const wrapper = mount(TrainingJobDetail, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const button = wrapper.findAll('button').find(item => item.text().includes('驳回部署'))
    expect(button?.exists()).toBe(true)
    await button?.trigger('click')
    await flushPromises()
    await wrapper.find('textarea').setValue('评估窗口业务指标不稳定，暂不进入灰度')
    await wrapper.find('.modal-ok').trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.rejectDeployment).toHaveBeenCalledWith(
      'deploy-1',
      '评估窗口业务指标不稳定，暂不进入灰度',
    )
    expect(wrapper.text()).toContain('已驳回')
  })

  it('opens redacted logs and artifact manifests through control-plane APIs', async () => {
    mocks.trainingApi.jobLogs.mockResolvedValue({
      available: true,
      lines: [{ ts: '2026-05-21T10:00:00Z', type: 'info', message: '[REDACTED]' }],
    })
    mocks.trainingApi.jobArtifacts.mockResolvedValue({
      download_proxy_available: false,
      items: [{ id: '1:0', type: 'adapter', name: 'model.bin', uri: 'https://object-store/model.bin', sha256: 'a'.repeat(64), size_bytes: 1024 }],
    })
    const wrapper = mount(TrainingJobDetail, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const logsButton = wrapper.findAll('button').find(item => item.text().includes('日志'))
    const artifactsButton = wrapper.findAll('button').find(item => item.text().includes('checkpoint'))
    await logsButton?.trigger('click')
    await artifactsButton?.trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.jobLogs).toHaveBeenCalledWith('train-1')
    expect(mocks.trainingApi.jobArtifacts).toHaveBeenCalledWith('train-1')
    expect(wrapper.text()).toContain('[REDACTED]')
    expect(wrapper.text()).toContain('model.bin')
  })

  it('opens model chat with training job department context', async () => {
    mocks.trainingApi.getJob.mockResolvedValueOnce(jobPayload({
      deployments: [{
        id: 'deploy-1',
        status: 'active',
        model_family: 'item_decline_ranker',
        artifact_id: 'artifact-1',
        rollout_percent: 100,
        target_skill_ids: ['skill-recommend'],
      }],
      latest_deployment: {
        id: 'deploy-1',
        status: 'active',
        model_family: 'item_decline_ranker',
        artifact_id: 'artifact-1',
        rollout_percent: 100,
        target_skill_ids: ['skill-recommend'],
      },
    }))
    const wrapper = mount(TrainingJobDetail, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const chatButton = wrapper.findAll('button').find(item => item.text().includes('和模型对话'))
    expect(chatButton?.exists()).toBe(true)
    await chatButton?.trigger('click')

    expect(mocks.router.push).toHaveBeenCalledWith(
      '/agent?model_deployment_id=deploy-1&model_family=item_decline_ranker&training_job_id=train-1&department=EC&artifact_id=artifact-1&target_gateway_id=node-1',
    )
  })

  it('shows why model chat is not ready before deployment goes live', async () => {
    const wrapper = mount(TrainingJobDetail, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Agent 对话')
    expect(wrapper.text()).toContain('未就绪')
    expect(wrapper.text()).toContain('部署状态为 awaiting_review')
    const chatButton = wrapper.findAll('button').find(item => item.text().includes('和模型对话'))
    expect(chatButton?.attributes('disabled')).toBeDefined()
  })
})
