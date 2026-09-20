import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  aiclawApi: {
    listInstances: vi.fn(),
    listAgents: vi.fn(),
    listSkills: vi.fn(),
    agentCoverage: vi.fn(),
    listAnalysisAgents: vi.fn(),
    validateChatModelContext: vi.fn(),
  },
  learningApi: {
    candidates: vi.fn(),
  },
  router: {
    push: vi.fn(),
  },
  route: {
    query: {} as Record<string, unknown>,
  },
  socket: {
    close: vi.fn(),
    send: vi.fn(),
    readyState: 1,
  },
  userStore: {
    isAdmin: true,
    isSystemAdmin: true,
    isDeptAdmin: false,
    userInfo: {
      name: '管理员',
      username: 'admin',
    },
  },
}))

vi.mock('@/api', () => ({
  aiclawApi: mocks.aiclawApi,
  learningApi: mocks.learningApi,
}))

vi.mock('@/api/aiclawWs', () => ({
  createAiclawChatSocket: vi.fn((_instanceId: string, _agentId: string, handlers: any) => {
    handlers?.onOpen?.()
    return mocks.socket
  }),
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => mocks.userStore,
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
}))

vi.mock('@/utils/renderMd', () => ({
  renderMd: (value: string) => value,
}))

vi.mock('@/utils/confirmDelete', () => ({
  confirmDelete: vi.fn(),
}))

vi.mock('@/utils/format', () => ({
  bjtDateString: () => '2026-05-22',
  formatTimeOnly: () => '10:00',
  formatTimeShort: () => '05-22 10:00',
}))

vi.mock('@arco-design/web-vue', () => ({
  Message: {
    error: vi.fn(),
    warning: vi.fn(),
  },
}))

vi.mock('@arco-design/web-vue/es/icon', () => ({
  IconApps: { template: '<span />' },
  IconArrowUp: { template: '<span />' },
  IconAttachment: { template: '<span />' },
  IconClose: { template: '<span />' },
  IconCode: { template: '<span />' },
  IconDelete: { template: '<span />' },
  IconDown: { template: '<span />' },
  IconFile: { template: '<span />' },
  IconHistory: { template: '<span />' },
  IconImage: { template: '<span />' },
  IconInfoCircle: { template: '<span />' },
  IconLoading: { template: '<span />' },
  IconMenu: { template: '<span />' },
  IconPause: { template: '<span />' },
  IconPlus: { template: '<span />' },
  IconRefresh: { template: '<span />' },
  IconSettings: { template: '<span />' },
  IconThunderbolt: { template: '<span />' },
  IconUp: { template: '<span />' },
}))

import BrainHome from '@/pages/brain/BrainHome.vue'
import { createAiclawChatSocket } from '@/api/aiclawWs'

function stubs() {
  return {
    'a-button': {
      props: ['loading', 'disabled', 'size'],
      emits: ['click'],
      template: '<button :disabled="disabled || loading" @click="$emit(\'click\', $event)"><slot name="icon" /><slot /></button>',
    },
    'a-select': {
      props: ['modelValue', 'options', 'disabled'],
      emits: ['update:modelValue', 'change'],
      template: `
        <select
          :disabled="disabled"
          :value="modelValue"
          @change="$emit('update:modelValue', $event.target.value); $emit('change', $event.target.value)"
        >
          <option v-for="item in options" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
      `,
    },
    'a-space': { template: '<div><slot /></div>' },
    'a-modal': {
      props: ['visible', 'okLoading'],
      emits: ['ok', 'cancel'],
      template: '<section v-if="visible"><slot /><button data-test-modal-ok @click="$emit(\'ok\')">OK</button></section>',
    },
    'a-form': { template: '<form><slot /></form>' },
    'a-form-item': { props: ['label'], template: '<label><span>{{ label }}</span><slot /></label>' },
    'a-input': { props: ['modelValue', 'disabled'], template: '<input :disabled="disabled" :value="modelValue" />' },
    'a-input-number': { props: ['modelValue', 'disabled'], template: '<input type="number" :disabled="disabled" :value="modelValue" />' },
    'a-checkbox-group': { template: '<div><slot /></div>' },
    'a-checkbox': { props: ['value'], template: '<label><input type="checkbox" :value="value" /><slot /></label>' },
    'a-switch': { props: ['modelValue'], template: '<input type="checkbox" :checked="modelValue" />' },
    'a-textarea': { props: ['modelValue', 'disabled'], template: '<textarea :disabled="disabled" :value="modelValue" />' },
    'a-tooltip': { template: '<span><slot /></span>' },
  }
}

describe('BrainHome', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mocks.route.query = {}
    mocks.aiclawApi.listInstances.mockResolvedValue([
      {
        id: 'node-train',
        name: '训练节点',
        department: 'EC',
        agent_purpose: 'training',
        bridge_online: true,
        is_platform_default: true,
        active_training_jobs_count: 1,
        active_training_jobs: [{ id: 'job-1', name: 'LoRA 微调' }],
        training: {
          gateway: true,
          supported_tasks: ['lora', 'eval'],
          gpu_count: 2,
          worker_count: 2,
        },
      },
      {
        id: 'node-analysis',
        name: '分析节点',
        department: 'EC',
        agent_purpose: 'analysis',
        bridge_online: true,
        department_name: 'EC',
        analysis: {
          agent: true,
          ops: ['intelligence.analyze'],
        },
      },
    ])
    mocks.aiclawApi.listAgents.mockResolvedValue({
      items: [{ id: 'agent-1', name: '默认 Agent' }],
    })
    mocks.aiclawApi.listSkills.mockResolvedValue({
      items: [
        {
          id: 'tmall-link-decline',
          display_name: '天猫店铺链接下滑分析',
          risk_level: 'R1',
          department: 'EC',
          calls_today: 12,
        },
      ],
    })
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
    mocks.aiclawApi.listAnalysisAgents.mockResolvedValue({
      items: [{
        id: 'samplebrand-same-topic-video-diagnosis-agent',
        name: '示例品牌同主题视频消耗诊断 Agent',
        department: '示例品牌内容电商运营部',
        skill_id: 'samplebrand-weekly-video-diagnosis',
        prompt_version: 'analysis_v2',
        dimensions: ['商品露出', '行动引导'],
        permissions: { read: true, edit: true, control_run: true },
        prompt: { goal: '周度同主题视频消耗诊断' },
        capabilities: [
          { key: 'same_topic_compare', label: '同主题对比', enabled: true },
          { key: 'run_control', label: '运行参数控制', enabled: true },
        ],
        control: {
          effective: {
            default_params: {
              window_days: 14,
              top_n: 2,
              output_sections: ['executive_summary', 'topic_comparisons'],
              todo_enabled: false,
            },
          },
        },
      }],
    })
    mocks.aiclawApi.validateChatModelContext.mockResolvedValue({
      ready: true,
      context: {},
    })
    mocks.learningApi.candidates.mockResolvedValue({ items: [] })
  })

  it('surfaces the selected Agent training gateway and opens the training console', async () => {
    const wrapper = mount(BrainHome, {
      global: {
        mocks: { $router: mocks.router },
        stubs: stubs(),
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('训练节点')
    expect(wrapper.text()).toContain('GPU 2')
    expect(wrapper.text()).toContain('2 个运行 Agent')
    expect(wrapper.text()).toContain('分析')
    expect(wrapper.text()).toContain('LoRA 微调')
    expect(wrapper.text()).toContain('部门 Agent 覆盖')
    expect(wrapper.text()).toContain('训练兜底 1')
    expect(wrapper.text()).toContain('天猫店铺链接下滑分析')
    expect(wrapper.text()).toContain('tmall-link-decline')
    const iconPaths = wrapper.findAll('svg path').map(path => path.attributes('d'))
    expect(iconPaths).toContain('M2 13V6l6-4 6 4v7 M6 13v-4h4v4')
    expect(iconPaths).toContain('M2 8a6 6 0 0 1 10.7-3.7 M14 2v3h-3 M14 8a6 6 0 0 1-10.7 3.7 M2 14v-3h3')
    expect(iconPaths).toContain('M2 8l12-6-4 14-3-6-5-2z')

    const trainingButton = wrapper.findAll('button').find(item => item.text().trim() === '训练')
    expect(trainingButton?.exists()).toBe(true)
    await trainingButton?.trigger('click')

    expect(mocks.router.push).toHaveBeenCalledWith('/training?gateway=node-train')

    const coverageButton = wrapper.findAll('button').find(item => item.text().includes('训练兜底 1'))
    expect(coverageButton?.exists()).toBe(true)
    await coverageButton?.trigger('click')
    expect(mocks.router.push).toHaveBeenCalledWith('/admin/agent-devices?department=EC&purpose=training&create=1')
  })

  it('shows editable business Agent controls on the platform default node', async () => {
    const wrapper = mount(BrainHome, {
      global: {
        mocks: { $router: mocks.router },
        stubs: stubs(),
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('训练节点')
    expect(wrapper.text()).toContain('示例品牌同主题视频消耗诊断 Agent')
    const editButtons = wrapper.findAll('button').filter(item => item.text().trim() === '编辑 Agent')
    expect(editButtons.length).toBeGreaterThan(0)
    await editButtons[0].trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Prompt 目标')
    expect(wrapper.text()).toContain('Prompt 关注点')
  })

  it('carries model deployment context from training into Agent chat payloads', async () => {
    mocks.route.query = {
      model_deployment_id: 'deploy-1',
      model_family: 'item-decline-ranker',
      training_job_id: 'train-1',
      artifact_id: '1:0',
      department: 'EC',
      target_gateway_id: 'node-train',
    }
    const wrapper = mount(BrainHome, {
      global: {
        mocks: { $router: mocks.router },
        stubs: stubs(),
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('模型对话')
    expect(wrapper.text()).toContain('item-decline-ranker')
    expect(createAiclawChatSocket).toHaveBeenCalledWith('node-train', 'agent-1', expect.any(Object))

    const promptButton = wrapper.findAll('button').find(item => item.text().includes('试问效果'))
    await promptButton?.trigger('click')
    await flushPromises()
    const sendButton = wrapper.find('button.brain-send-btn')
    await sendButton.trigger('click')

    expect(mocks.socket.send).toHaveBeenCalledWith(expect.stringContaining('"model_deployment_id":"deploy-1"'))
    expect(mocks.socket.send).toHaveBeenCalledWith(expect.stringContaining('"model_family":"item-decline-ranker"'))
    expect(mocks.socket.send).toHaveBeenCalledWith(expect.stringContaining('"target_gateway_id":"node-train"'))
  })

  it('uses an online Agent as model chat channel when the deployment gateway is offline', async () => {
    mocks.route.query = {
      model_deployment_id: 'deploy-offline',
      model_family: 'item-decline-ranker',
      training_job_id: 'train-offline',
      artifact_id: 'artifact-offline',
      department: 'EC',
      target_gateway_id: 'node-train-offline',
    }
    mocks.aiclawApi.listInstances.mockResolvedValue([
      {
        id: 'node-train-offline',
        name: '离线训练节点',
        department: 'EC',
        agent_purpose: 'training',
        bridge_online: false,
        training: {
          gateway: true,
          supported_tasks: ['lora', 'eval'],
          ops: ['training.inference'],
        },
      },
      {
        id: 'platform-chat',
        name: '平台模型对话通道',
        department: 'AI',
        agent_purpose: 'training',
        bridge_online: true,
        is_platform_default: true,
        training: {
          gateway: true,
          supported_tasks: ['lora', 'eval'],
          ops: ['training.inference'],
        },
      },
    ])
    mocks.aiclawApi.listAgents.mockResolvedValue({
      items: [{ id: 'agent-channel', name: '默认 Agent' }],
    })

    const wrapper = mount(BrainHome, {
      global: {
        mocks: { $router: mocks.router },
        stubs: stubs(),
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('平台模型对话通道')
    expect(wrapper.text()).toContain('模型对话')
    expect(createAiclawChatSocket).toHaveBeenCalledWith('platform-chat', 'agent-channel', expect.any(Object))

    const promptButton = wrapper.findAll('button').find(item => item.text().includes('试问效果'))
    await promptButton?.trigger('click')
    await flushPromises()
    await wrapper.find('button.brain-send-btn').trigger('click')

    expect(mocks.socket.send).toHaveBeenCalledWith(expect.stringContaining('"model_deployment_id":"deploy-offline"'))
    expect(mocks.socket.send).toHaveBeenCalledWith(expect.stringContaining('"target_gateway_id":"node-train-offline"'))
  })

  it('shows artifact sync readiness when a retired deployment gateway falls back', async () => {
    mocks.route.query = {
      model_deployment_id: 'deploy-462170bc',
      model_family: 'lora',
      training_job_id: 'job-462170bc',
      department: 'EC',
      target_gateway_id: 'node-runtime-model-462170bc',
      draft: '请基于当前训练后的模型做一次测试对话',
    }
    mocks.aiclawApi.listInstances.mockResolvedValue([
      {
        id: 'node-runtime-model-462170bc',
        name: '旧推理节点',
        department: 'EC',
        agent_purpose: 'training',
        bridge_online: false,
        training: { gateway: true, ops: ['training.inference'] },
      },
      {
        id: 'content-commerce',
        name: '内容电商',
        department: '销售二部',
        agent_purpose: 'mixed',
        bridge_online: true,
        training: { gateway: true, ops: ['training.inference'] },
      },
    ])
    mocks.aiclawApi.validateChatModelContext.mockResolvedValue({
      ready: false,
      disabled_reason: '训练后模型产物只在原推理节点 node-runtime-model-462170bc 本地可用，当前在线推理节点 content-commerce 尚未同步模型产物；请重新训练或重新收集模型产物后再对话',
      context: {
        model_deployment_id: 'deploy-462170bc',
        model_family: 'lora',
        training_job_id: 'job-462170bc',
        target_gateway_id: 'content-commerce',
        bound_gateway_id: 'node-runtime-model-462170bc',
        inference_ready: 'false',
        inference_disabled_reason: '训练后模型产物只在原推理节点 node-runtime-model-462170bc 本地可用，当前在线推理节点 content-commerce 尚未同步模型产物；请重新训练或重新收集模型产物后再对话',
      },
    })

    const wrapper = mount(BrainHome, {
      global: {
        mocks: { $router: mocks.router },
        stubs: stubs(),
      },
    })
    await flushPromises()

    expect(createAiclawChatSocket).toHaveBeenCalledWith('content-commerce', 'agent-1', expect.any(Object))
    expect(wrapper.text()).toContain('尚未同步模型产物')
    await wrapper.find('button.brain-send-btn').trigger('click')
    expect(mocks.socket.send).not.toHaveBeenCalled()
  })
})
