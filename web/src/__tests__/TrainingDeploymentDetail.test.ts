import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  trainingApi: {
    getDeployment: vi.fn(),
    approveDeployment: vi.fn(),
    activateDeployment: vi.fn(),
    rejectDeployment: vi.fn(),
    rollbackDeployment: vi.fn(),
  },
  route: {
    params: { id: 'deploy-1' },
  },
  router: {
    push: vi.fn(),
  },
}))

vi.mock('@/api', () => ({
  trainingApi: mocks.trainingApi,
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
  IconClose: { template: '<span />' },
  IconLeft: { template: '<span />' },
  IconRefresh: { template: '<span />' },
  IconSend: { template: '<span />' },
}))

import TrainingDeploymentDetail from '@/pages/training/TrainingDeploymentDetail.vue'

function deploymentPayload(overrides: Record<string, unknown> = {}) {
  return {
    id: 'deploy-1',
    job_id: 'train-1',
    department: 'EC',
    model_family: 'item_decline_ranker',
    artifact_id: 'artifact-1',
    artifact_ref: { sha256: 'a'.repeat(64) },
    eval_task_id: 2,
    target_skill_ids: ['skill-recommend'],
    status: 'awaiting_review',
    rollout_percent: 10,
    rollback_to: '',
    requested_by: 'admin',
    request_reason: '评估通过，提交部署审批',
    approved_by: '',
    approved_at: '',
    rejected_by: '',
    rejected_at: '',
    reject_reason: '',
    activated_at: '',
    created_at: '2026-05-21T11:00:00+08:00',
    updated_at: '2026-05-21T11:00:00+08:00',
    ...overrides,
  }
}

function detailPayload(deployment = deploymentPayload()) {
  return {
    deployment,
    job: {
      id: 'train-1',
      title: '动作推荐训练',
      department: 'EC',
      status: 'completed',
      job_type: 'lora',
      target_skill_id: 'skill-recommend',
      target_gateway_id: 'node-1',
      dataset_ref: 'decision-log://skill-recommend/action-outcome/latest',
      tasks: [{
        id: 2,
        status: 'completed',
        metrics: {
          op: 'training.evaluate',
          passed: true,
          checks: [{ name: 'min_win_rate', actual: 0.61, expected: 0.55, passed: true }],
        },
      }],
      deployments: [deployment],
    },
  }
}

function stubs() {
  return {
    SfPageHeader: { props: ['title', 'subtitle'], template: '<header><h1>{{ title }}</h1><p>{{ subtitle }}</p><slot name="actions" /></header>' },
    SfKpiCard: { props: ['label', 'value', 'hint', 'suffix'], template: '<section><b>{{ label }}</b><span>{{ value }}</span><small>{{ hint }}</small><i>{{ suffix }}</i></section>' },
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
    'a-tag': { template: '<span><slot /></span>' },
    'a-textarea': {
      props: ['modelValue'],
      emits: ['update:modelValue'],
      template: '<textarea :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    },
  }
}

describe('TrainingDeploymentDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.params.id = 'deploy-1'
    mocks.trainingApi.getDeployment.mockResolvedValue(detailPayload())
  })

  it('renders deployment lifecycle details with job evaluation context', async () => {
    const wrapper = mount(TrainingDeploymentDetail, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    expect(mocks.trainingApi.getDeployment).toHaveBeenCalledWith('deploy-1')
    expect(wrapper.text()).toContain('item_decline_ranker')
    expect(wrapper.text()).toContain('部署待审')
    expect(wrapper.text()).toContain('动作推荐训练')
    expect(wrapper.text()).toContain('min_win_rate')
    expect(wrapper.text()).toContain('sha256')
  })

  it('approves deployment and renders the updated canary state', async () => {
    mocks.trainingApi.approveDeployment.mockResolvedValue(detailPayload(deploymentPayload({
      status: 'canary',
      approved_by: 'admin',
      rollout_percent: 10,
    })))
    const wrapper = mount(TrainingDeploymentDetail, {
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

  it('rejects deployment approval requests from the detail page', async () => {
    mocks.trainingApi.rejectDeployment.mockResolvedValue(detailPayload(deploymentPayload({
      status: 'rejected',
      rejected_by: 'admin',
      rejected_at: '2026-05-21T12:00:00+08:00',
      reject_reason: '部署审批未通过，退回训练产物重新确认',
    })))
    const wrapper = mount(TrainingDeploymentDetail, {
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
    expect(wrapper.text()).toContain('已驳回')
    expect(wrapper.text()).toContain('部署审批未通过')
  })

  it('opens model chat with deployment department context', async () => {
    mocks.trainingApi.getDeployment.mockResolvedValueOnce(detailPayload(deploymentPayload({
      status: 'active',
      artifact_ref: { id: 'artifact-1', sha256: 'a'.repeat(64) },
    })))
    const wrapper = mount(TrainingDeploymentDetail, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const chatButton = wrapper.findAll('button').find(item => item.text().includes('和模型对话'))
    expect(chatButton?.exists()).toBe(true)
    await chatButton?.trigger('click')

    expect(mocks.router.push).toHaveBeenCalledWith(
      `/agent?model_deployment_id=deploy-1&model_family=item_decline_ranker&training_job_id=train-1&department=EC&artifact_id=artifact-1&artifact_sha256=${'a'.repeat(64)}&target_gateway_id=node-1`,
    )
  })

  it('shows the Agent chat route readiness reason before deployment is live', async () => {
    const wrapper = mount(TrainingDeploymentDetail, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Agent 对话路由')
    expect(wrapper.text()).toContain('未就绪')
    expect(wrapper.text()).toContain('部署未进入灰度或已部署')
    const chatButton = wrapper.findAll('button').find(item => item.text().includes('和模型对话'))
    expect(chatButton?.exists()).toBe(true)
    expect(chatButton?.attributes('disabled')).toBeDefined()
  })
})
