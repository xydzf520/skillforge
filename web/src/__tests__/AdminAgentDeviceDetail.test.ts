import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  aiclawApi: {
    getInstance: vi.fn(),
    capabilities: vi.fn(),
    listAgents: vi.fn(),
    listSkills: vi.fn(),
    removeSkillFromDevice: vi.fn(),
    importSkillFromDevice: vi.fn(),
    syncSkill: vi.fn(),
    getMediaBootstrap: vi.fn(),
    startMediaBootstrap: vi.fn(),
    cancelMediaBootstrap: vi.fn(),
  },
  skillApi: {
    list: vi.fn(),
  },
  route: {
    params: { id: 'node-1' },
  },
  router: {
    push: vi.fn(),
  },
}))

vi.mock('@/api', () => ({
  aiclawApi: mocks.aiclawApi,
  skillApi: mocks.skillApi,
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
}))

vi.mock('@arco-design/web-vue', () => ({
  Message: {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
  },
  Modal: {
    confirm: vi.fn(),
    warning: vi.fn(),
  },
}))

vi.mock('@arco-design/web-vue/es/icon', () => ({
  IconDelete: { template: '<span />' },
  IconEdit: { template: '<span />' },
  IconImport: { template: '<span />' },
  IconLeft: { template: '<span />' },
  IconMessage: { template: '<span />' },
  IconRefresh: { template: '<span />' },
  IconRobot: { template: '<span />' },
  IconStorage: { template: '<span />' },
  IconUpload: { template: '<span />' },
}))

import AdminAgentDeviceDetail from '@/pages/admin/AdminAgentDeviceDetail.vue'

function stubs() {
  return {
    SfEmptyState: { props: ['description'], template: '<div>{{ description }}</div>' },
    SfLoadingState: { props: ['tip'], template: '<div>{{ tip }}</div>' },
    'a-button': {
      props: ['loading', 'disabled', 'type', 'size'],
      emits: ['click'],
      template: '<button :disabled="disabled || loading" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>',
    },
    'a-card': { props: ['title'], template: '<section><h2>{{ title }}</h2><slot /></section>' },
    'a-descriptions': { template: '<dl><slot /></dl>' },
    'a-descriptions-item': { props: ['label'], template: '<div><dt>{{ label }}</dt><dd><slot /></dd></div>' },
    'a-input-search': { props: ['modelValue'], template: '<input :value="modelValue" />' },
    'a-link': { emits: ['click'], template: '<a @click="$emit(\'click\')"><slot /></a>' },
    'a-alert': { template: '<div><slot /></div>' },
    'a-progress': { props: ['percent'], template: '<div>{{ percent }}</div>' },
    'a-popconfirm': { template: '<div><slot /></div>' },
    'a-radio': { props: ['value'], template: '<label><slot /></label>' },
    'a-radio-group': { props: ['modelValue'], template: '<div><slot /></div>' },
    'a-space': { template: '<div><slot /></div>' },
    'a-table': { template: '<table><slot name="columns" /></table>' },
    'a-table-column': { props: ['title'], template: '<th>{{ title }}</th>' },
    'a-tag': { template: '<span><slot /></span>' },
    'a-tooltip': { template: '<span><slot /></span>' },
  }
}

describe('AdminAgentDeviceDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.params = { id: 'node-1' }
    mocks.aiclawApi.getInstance.mockResolvedValue({
      id: 'node-1',
      name: '训练节点 A',
      department: 'EC',
      agent_purpose: 'analysis',
      bridge_online: true,
      bridge_gateway_kind: 'openclaw',
      bridge_platform: 'linux',
      training: {
        active_jobs_count: 1,
        active_jobs: [{ id: 'train-1', title: 'EC LoRA 训练', status: 'running', job_type: 'lora' }],
      },
      active_training_jobs_count: 1,
      active_training_jobs: [{ id: 'train-1', title: 'EC LoRA 训练', status: 'running', job_type: 'lora' }],
    })
    mocks.aiclawApi.capabilities.mockResolvedValue({
      training: {
        gateway: true,
        supported_tasks: ['lora', 'eval'],
        gpu_count: 1,
        worker_count: 2,
      },
      gpu: [{
        name: 'RTX 4060',
        vram_total_mb: 8192,
        vram_used_mb: 1024,
        vram_free_mb: 7168,
        gpu_util_pct: 3,
      }],
      memory: { total_gb: 32, avail_gb: 20, used_pct: 38 },
      disk: { '/': { total_gb: 512, used_gb: 120, avail_gb: 392, use_pct: 24 } },
    })
    mocks.aiclawApi.listAgents.mockResolvedValue({ items: [] })
    mocks.aiclawApi.getMediaBootstrap.mockResolvedValue({ status: 'not_started', progress_percent: 0 })
    mocks.aiclawApi.startMediaBootstrap.mockResolvedValue({ status: 'downloading', progress_percent: 1, worker_active: true })
    mocks.aiclawApi.cancelMediaBootstrap.mockResolvedValue({ status: 'cancelled', progress_percent: 1 })
    mocks.skillApi.list.mockResolvedValue({ items: [], total: 0 })
  })

  it('shows bridge training capability and links to the selected training gateway', async () => {
    const wrapper = mount(AdminAgentDeviceDetail, {
      global: {
        mocks: { $router: mocks.router },
        stubs: stubs(),
      },
    })
    await flushPromises()

    expect(mocks.aiclawApi.capabilities).toHaveBeenCalledWith('node-1')
    expect(wrapper.text()).toContain('训练能力')
    expect(wrapper.text()).toContain('分析 Agent')
    expect(wrapper.text()).toContain('训练网关已启用')
    expect(wrapper.text()).toContain('2 Worker')
    expect(wrapper.text()).toContain('1 GPU')
    expect(wrapper.text()).toContain('LoRA')
    expect(wrapper.text()).toContain('评估')
    expect(wrapper.text()).toContain('运行中 1')
    expect(wrapper.text()).toContain('EC LoRA 训练')

    const trainingButton = wrapper.findAll('button').find(item => item.text().includes('训练工作台'))
    expect(trainingButton?.exists()).toBe(true)
    await trainingButton?.trigger('click')

    expect(mocks.router.push).toHaveBeenCalledWith('/training?gateway=node-1')

    const jobLink = wrapper.findAll('a').find(item => item.text().includes('EC LoRA 训练'))
    expect(jobLink?.exists()).toBe(true)
    await jobLink?.trigger('click')
    expect(mocks.router.push).toHaveBeenCalledWith('/training/jobs/train-1')
  })

  it('shows the controlled H3 bootstrap status for a media node', async () => {
    mocks.aiclawApi.getInstance.mockResolvedValue({
      id: 'node-1',
      name: 'PRO 6000',
      department: '平台',
      agent_purpose: 'media',
      bridge_online: true,
      bridge_gateway_kind: 'bridge',
      bridge_platform: 'linux',
    })
    mocks.aiclawApi.capabilities.mockResolvedValue({
      media: { configured: false, supported_modes: [] },
      gpu: [{ name: 'RTX PRO 6000', vram_total_mb: 98304 }],
    })
    mocks.aiclawApi.getMediaBootstrap.mockResolvedValue({
      status: 'downloading',
      progress_percent: 36.7,
      downloaded_bytes: 23_000_000_000,
      total_bytes: 63_440_000_000,
      speed_bytes_per_second: 3_145_728,
      eta_seconds: 12_800,
      current_step: 'download_models',
      current_file: 'minimax_h3_ref2va_pruned_int8_convrot.safetensors',
      worker_active: true,
    })

    const wrapper = mount(AdminAgentDeviceDetail, {
      global: {
        mocks: { $router: mocks.router },
        stubs: stubs(),
      },
    })
    await flushPromises()

    expect(mocks.aiclawApi.getMediaBootstrap).toHaveBeenCalledWith('node-1')
    expect(wrapper.text()).toContain('MiniMax H3 媒体环境')
    expect(wrapper.text()).toContain('下载模型')
    expect(wrapper.text()).toContain('36.7')
    expect(wrapper.text()).toContain('3.0 MB/s')
    expect(wrapper.text()).toContain('模型、节点和模板自检通过后才会进入调度')
    wrapper.unmount()
  })
})
