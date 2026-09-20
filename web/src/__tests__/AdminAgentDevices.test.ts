import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  aiclawApi: {
    listInstances: vi.fn(),
    agentCoverage: vi.fn(),
    createInstance: vi.fn(),
  },
  hallApi: {
    departments: vi.fn(),
  },
  router: {
    push: vi.fn(),
  },
  route: {
    query: {} as Record<string, unknown>,
  },
  userStore: {
    isSystemAdmin: true,
    isDeptAdmin: true,
    userInfo: {
      department: 'EC',
      managed_departments: [],
      accessible_departments: [],
    },
  },
}))

vi.mock('@/api', () => ({
  aiclawApi: mocks.aiclawApi,
  hallApi: mocks.hallApi,
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => mocks.userStore,
}))

vi.mock('vue-router', () => ({
  useRouter: () => mocks.router,
  useRoute: () => mocks.route,
}))

vi.mock('@arco-design/web-vue', () => ({
  Message: {
    success: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    error: vi.fn(),
  },
}))

vi.mock('@arco-design/web-vue/es/icon', () => ({
  IconDown: { template: '<span />' },
  IconPlus: { template: '<span />' },
}))

import AdminAgentDevices from '@/pages/admin/AdminAgentDevices.vue'

function stubs() {
  return {
    SfPageHeader: { props: ['title', 'subtitle'], template: '<header><h1>{{ title }}</h1><slot name="actions" /></header>' },
    SfTagChip: { props: ['label'], template: '<span><slot />{{ label }}</span>' },
    'a-alert': { template: '<div><slot name="title" /><slot /></div>' },
    'a-button': {
      props: ['loading', 'disabled', 'type', 'size'],
      emits: ['click'],
      template: '<button :disabled="disabled || loading" @click="$emit(\'click\', $event)"><slot /></button>',
    },
    'a-card': { template: '<section><slot /></section>' },
    'a-col': { template: '<div><slot /></div>' },
    'a-collapse': { template: '<div><slot /></div>' },
    'a-collapse-item': { template: '<div><slot /></div>' },
    'a-doption': { emits: ['click'], template: '<button @click="$emit(\'click\')"><slot /></button>' },
    'a-dropdown': { template: '<div><slot /><slot name="content" /></div>' },
    'a-form': { template: '<form><slot /></form>' },
    'a-form-item': { props: ['label'], template: '<label><span>{{ label }}</span><slot /><slot name="help" /></label>' },
    'a-input': { props: ['modelValue'], template: '<input :value="modelValue" />' },
    'a-input-password': { props: ['modelValue'], template: '<input :value="modelValue" />' },
    'a-input-search': { props: ['modelValue'], template: '<input :value="modelValue" />' },
    'a-link': { emits: ['click'], template: '<a @click="$emit(\'click\', $event)"><slot /></a>' },
    'a-modal': { props: ['visible'], template: '<section v-if="visible"><slot /></section>' },
    'a-option': { props: ['value'], template: '<option :value="value"><slot /></option>' },
    'a-popconfirm': { template: '<span><slot /></span>' },
    'a-radio': { props: ['value'], template: '<label><slot /></label>' },
    'a-radio-group': { template: '<div><slot /></div>' },
    'a-row': { template: '<div><slot /></div>' },
    'a-select': { props: ['modelValue'], template: '<select :value="modelValue"><slot /></select>' },
    'a-space': { template: '<div><slot /></div>' },
    'a-switch': { props: ['modelValue'], template: '<input type="checkbox" :checked="modelValue" />' },
    'a-table': {
      props: ['data'],
      provide(this: any) {
        return { tableState: this }
      },
      template: '<table><slot name="columns" /></table>',
    },
    'a-table-column': {
      props: ['title', 'dataIndex'],
      inject: { tableState: { default: () => ({ data: [] }) } },
      template: `
        <th>
          {{ title }}
          <div v-for="record in (tableState.data || [])" :key="record.id">
            <slot v-if="$slots.cell" name="cell" :record="record" />
            <span v-else>{{ dataIndex ? record[dataIndex] : '' }}</span>
          </div>
        </th>
      `,
    },
    'a-tag': { template: '<span><slot /></span>' },
  }
}

describe('AdminAgentDevices', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.query = {}
    mocks.aiclawApi.listInstances.mockResolvedValue([{
      id: 'node-1',
      name: '训练节点 A',
      department: 'EC',
      agent_type: 'aiclaw',
      agent_purpose: 'analysis',
      runtime_type: 'openclaw',
      bridge_online: true,
      has_device_pubkey: true,
      bridge_version: '0.1.6',
      bridge_latest_version: '0.1.8',
      bridge_update_available: true,
      training: {
        gateway: true,
        supported_tasks: ['lora', 'eval'],
        gpu_count: 2,
        worker_count: 3,
        vram_total_gb: 24,
        vram_free_gb: 20,
        active_jobs_count: 1,
        active_jobs: [{ id: 'train-1', title: 'EC LoRA 训练', status: 'running', job_type: 'lora' }],
      },
      active_training_jobs_count: 1,
      active_training_jobs: [{ id: 'train-1', title: 'EC LoRA 训练', status: 'running', job_type: 'lora' }],
    }])
    mocks.aiclawApi.agentCoverage.mockResolvedValue({
      items: [{
        department: 'EC',
        status: 'fallback',
        capabilities: {
          skill_runtime: { ready: true, online: 1, count: 1 },
          analysis: { ready: true, online: 1, count: 1 },
          training: { ready: false, fallback_ready: true, fallback_count: 1 },
        },
      }],
    })
    mocks.aiclawApi.createInstance.mockResolvedValue({
      id: 'platform-media-5080',
      enrollment_token: 'enroll-5080',
      expires_at: '2026-08-11T12:00:00Z',
    })
    mocks.hallApi.departments.mockResolvedValue({ departments: [] })
  })

  it('renders training capability on the Agent terminal list and links to Training', async () => {
    const wrapper = mount(AdminAgentDevices, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('训练网关')
    expect(wrapper.text()).toContain('Bridge 0.1.6->0.1.8 自动')
    expect(wrapper.text()).not.toContain('强制更新')
    expect(wrapper.text()).toContain('GPU 2')
    expect(wrapper.text()).toContain('Worker 3')
    expect(wrapper.text()).toContain('分析')
    expect(wrapper.text()).toContain('LoRA / 评估')
    expect(wrapper.text()).toContain('显存 20/24 GB')
    expect(wrapper.text()).toContain('运行中 1')
    expect(wrapper.text()).toContain('EC LoRA 训练')
    expect(wrapper.text()).toContain('分析 1')
    expect(wrapper.text()).toContain('可训练 1')
    expect(wrapper.text()).toContain('平台兜底')
    expect(wrapper.text()).toContain('执行 1/1')
    expect(wrapper.text()).toContain('训练 兜底 1')

    const trainingButton = wrapper.findAll('button').find(item => item.text().trim() === '训练')
    expect(trainingButton?.exists()).toBe(true)
    await trainingButton?.trigger('click')

    expect(mocks.router.push).toHaveBeenCalledWith('/training?gateway=node-1')

    const jobLink = wrapper.findAll('a').find(item => item.text().includes('EC LoRA 训练'))
    expect(jobLink?.exists()).toBe(true)
    await jobLink?.trigger('click')
    expect(mocks.router.push).toHaveBeenCalledWith('/training/jobs/train-1')
  })

  it('opens a department Agent create form directly from coverage lanes', async () => {
    const wrapper = mount(AdminAgentDevices, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const quickCreateButton = wrapper.findAll('button').find(item => item.text().includes('训练 兜底 1'))
    expect(quickCreateButton?.exists()).toBe(true)
    await quickCreateButton?.trigger('click')
    await flushPromises()

    const vm = wrapper.vm as any
    expect(vm.formData.department).toBe('EC')
    expect(vm.formData.agent_purpose).toBe('training')
    expect(vm.formData.name).toBe('EC训练 Agent')
    expect(vm.filterDepartment).toBe('EC')
    expect(wrapper.text()).toContain('创建后会弹出一次性 enrollment token')
  })

  it('opens a prefilled create form from department and purpose route query', async () => {
    mocks.route.query = { department: 'EC', purpose: 'analysis', create: '1' }
    const wrapper = mount(AdminAgentDevices, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const vm = wrapper.vm as any
    expect(vm.formData.department).toBe('EC')
    expect(vm.formData.agent_purpose).toBe('analysis')
    expect(vm.formData.name).toBe('EC分析 Agent')
    expect(vm.filterDepartment).toBe('EC')
    expect(wrapper.text()).toContain('创建后会弹出一次性 enrollment token')
  })

  it('allows a system admin to create a platform media node without a department', async () => {
    const wrapper = mount(AdminAgentDevices, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const vm = wrapper.vm as any
    Object.assign(vm.formData, {
      id: 'platform-media-5080',
      name: 'RTX 5080 媒体主节点',
      department: '',
      agent_purpose: 'media',
      is_platform_default: true,
    })
    await vm.handleSave()
    await flushPromises()

    expect(mocks.aiclawApi.createInstance).toHaveBeenCalledWith(expect.objectContaining({
      id: 'platform-media-5080',
      department: '',
      agent_purpose: 'media',
      is_platform_default: true,
    }))
  })
})
