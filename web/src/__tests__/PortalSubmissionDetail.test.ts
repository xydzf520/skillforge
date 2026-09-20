import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  portalApi: {
    getSubmission: vi.fn(),
    getSkillUi: vi.fn(),
    previewSkillUi: vi.fn(),
    saveSkillUiPreference: vi.fn(),
    deleteSkillUiPreference: vi.fn(),
  },
  runTraceApi: {
    getExecutionTrace: vi.fn(),
    analyzeExecutionTrace: vi.fn(),
  },
  trainingApi: {
    createRunCandidate: vi.fn(),
  },
  router: {
    push: vi.fn(),
    back: vi.fn(),
  },
  messages: {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
  },
}))

vi.mock('@/api', () => ({
  portalApi: mocks.portalApi,
  runTraceApi: mocks.runTraceApi,
  trainingApi: mocks.trainingApi,
}))

vi.mock('vue-echarts', () => ({ default: { template: '<div class="chart-stub" />' } }))

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: 'sub-1' } }),
  useRouter: () => mocks.router,
}))

vi.mock('@arco-design/web-vue', () => ({
  Message: mocks.messages,
}))

vi.mock('@arco-design/web-vue/es/icon', () => ({
  IconLeft: { template: '<span />' },
  IconRefresh: { template: '<span />' },
  IconRobot: { template: '<span />' },
  IconSave: { template: '<span />' },
  IconSend: { template: '<span />' },
}))

import PortalSubmissionDetail from '@/pages/portal/PortalSubmissionDetail.vue'

function stubs() {
  return {
    'a-alert': { props: ['title'], template: '<div>{{ title }}</div>' },
    'a-button': {
      props: ['disabled', 'loading'],
      emits: ['click'],
      template: '<button :disabled="disabled || loading" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>',
    },
    'a-card': { props: ['title'], template: '<section><h3>{{ title }}</h3><slot /></section>' },
    'a-descriptions': { template: '<dl><slot /></dl>' },
    'a-descriptions-item': { props: ['label'], template: '<div><dt>{{ label }}</dt><dd><slot /></dd></div>' },
    'a-empty': { props: ['description'], template: '<div>{{ description }}</div>' },
    'a-modal': {
      props: ['visible', 'title'],
      template: '<section v-if="visible" class="modal"><h3>{{ title }}</h3><slot /></section>',
    },
    'a-result': { props: ['title'], template: '<div>{{ title }}</div>' },
    'a-space': { template: '<div><slot /></div>' },
    'a-spin': { template: '<div><slot /></div>' },
    'a-table': { template: '<table><slot name="columns" /></table>' },
    'a-table-column': { props: ['title'], template: '<th>{{ title }}</th>' },
    'a-tag': { template: '<span><slot /></span>' },
    'a-textarea': {
      props: ['modelValue', 'disabled'],
      emits: ['update:modelValue'],
      template: '<textarea :disabled="disabled" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    },
    'a-typography-text': { template: '<span><slot /></span>' },
  }
}

function findButton(wrapper: any, label: string) {
  const button = wrapper.findAll('button').find((item: any) => item.text().includes(label))
  if (!button) throw new Error(`button not found: ${label}`)
  return button
}

function findResultAiFloatingButton(wrapper: any) {
  const button = wrapper.find('button[aria-label="AI 调整结果页面"]')
  if (!button.exists()) throw new Error('result AI floating button not found')
  return button
}

describe('PortalSubmissionDetail result UI preference', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.portalApi.getSubmission.mockResolvedValue({
      id: 'sub-1',
      skill_id: 'skill-1',
      skill_display_name: '投放日报',
      status: 'completed',
      execution_id: 'run-1',
      params: { date: '2026-05-21' },
      result_summary: { type: 'table', data: [{ date: '2026-05-21', cost: 12 }] },
      result_ui_schema: { type: 'table', columns: [{ key: 'date', title: '日期' }, { key: 'cost', title: '花费' }] },
    })
    mocks.portalApi.getSkillUi.mockResolvedValue({
      surface: 'result',
      overlay: {},
      saved_prompt: '',
      merged_ui_schema_hash: 'sha256:base',
      merged_schema: {
        components: [{
          id: 'result-table',
          type: 'table',
          binding: 'result.data',
          columns: [{ key: 'date', title: '日期' }, { key: 'cost', title: '花费' }],
        }],
      },
      permissions: { read: true, execute: false, customize_ui: true },
    })
    mocks.portalApi.previewSkillUi.mockResolvedValue({
      surface: 'result',
      overlay: {
        schema_version: 'skill-ui/v1',
        surface: 'result',
        operations: [{ op: 'set_chart_type', component_id: 'result-table', chart_type: 'bar_chart' }],
      },
      merged_ui_schema_hash: 'sha256:preview',
      merged_schema: {
        components: [{ id: 'result-table', type: 'bar_chart', binding: 'result.data' }],
      },
      permissions: { read: true, execute: false, customize_ui: true },
      ai_status: 'llm',
    })
    mocks.portalApi.saveSkillUiPreference.mockResolvedValue({
      surface: 'result',
      ui_pref_id: 'uipref-result',
      ui_pref_version: 1,
      overlay: {
        schema_version: 'skill-ui/v1',
        surface: 'result',
        operations: [{ op: 'set_chart_type', component_id: 'result-table', chart_type: 'bar_chart' }],
      },
      saved_prompt: '换成柱状图',
      merged_ui_schema_hash: 'sha256:saved',
      merged_schema: {
        components: [{ id: 'result-table', type: 'bar_chart', binding: 'result.data' }],
      },
      permissions: { read: true, execute: false, customize_ui: true },
    })
    mocks.portalApi.deleteSkillUiPreference.mockResolvedValue({
      surface: 'result',
      overlay: {},
      saved_prompt: '',
      merged_ui_schema_hash: 'sha256:base',
      merged_schema: {
        components: [{ id: 'result-table', type: 'table', binding: 'result.data' }],
      },
      permissions: { read: true, execute: false, customize_ui: true },
    })
    mocks.runTraceApi.getExecutionTrace.mockResolvedValue({
      execution_run: { status: 'completed' },
      collection_proofs: [{ proof_id: 'proof-1' }],
      intelligence_analyze_runs: [{ id: 1 }],
      decision_log: [{ id: 1 }, { id: 2 }],
    })
    mocks.runTraceApi.analyzeExecutionTrace.mockResolvedValue({
      run_id: 'run-1',
      skill_id: 'skill-1',
      analysis: '本次运行缺少库存 proof，建议补齐采集。',
      model: 'agent:analysis-node',
      prompt_hash: 'hash-run-1',
      raw_counts: { execution_steps: 2, decision_logs: 1 },
    })
    mocks.trainingApi.createRunCandidate.mockResolvedValue({
      id: 'train-run-1',
    })
  })

  it('previews, saves and resets personal result surface overlays', async () => {
    const wrapper = mount(PortalSubmissionDetail, {
      global: {
        stubs: stubs(),
        mocks: { $router: mocks.router },
      },
    })
    await flushPromises()

    expect(mocks.portalApi.getSkillUi).toHaveBeenCalledWith('skill-1', { surface: 'result' })

    await findResultAiFloatingButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('AI 对话调整结果页面')

    await wrapper.get('textarea').setValue('换成柱状图')
    await findButton(wrapper, '发送给 AI').trigger('click')
    await flushPromises()

    expect(mocks.portalApi.previewSkillUi).toHaveBeenCalledWith('skill-1', {
      surface: 'result',
      instruction: '换成柱状图',
      current_overlay: {},
    })

    await findButton(wrapper, '保存到我的页面').trigger('click')
    await flushPromises()

    expect(mocks.portalApi.saveSkillUiPreference).toHaveBeenCalledWith('skill-1', expect.objectContaining({
      surface: 'result',
      generated_by: 'ai',
      prompt_summary: '换成柱状图',
    }))

    await findButton(wrapper, '恢复默认').trigger('click')
    await flushPromises()

    expect(mocks.portalApi.deleteSkillUiPreference).toHaveBeenCalledWith('skill-1', { surface: 'result' })
  })

  it('renders declarative metric and summary result components', async () => {
    mocks.portalApi.getSubmission.mockResolvedValueOnce({
      id: 'sub-1',
      skill_id: 'skill-1',
      skill_display_name: '投放日报',
      status: 'completed',
      params: { date: '2026-05-21' },
      result_summary: {
        type: 'text',
        roi: 0.32,
        analysis: '成交效率高于昨日',
      },
    })
    mocks.portalApi.getSkillUi.mockResolvedValueOnce({
      surface: 'result',
      overlay: {},
      saved_prompt: '',
      merged_ui_schema_hash: 'sha256:metrics',
      merged_schema: {
        components: [
          { id: 'roi', type: 'metric', title: 'ROI', binding: 'result.roi', format: 'percent' },
          { id: 'summary', type: 'markdown_summary', title: '洞察', binding: 'result.analysis' },
        ],
      },
      permissions: { read: true, execute: false, customize_ui: true },
    })

    const wrapper = mount(PortalSubmissionDetail, {
      global: {
        stubs: stubs(),
        mocks: { $router: mocks.router },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('ROI')
    expect(wrapper.text()).toContain('32%')
    expect(wrapper.text()).toContain('成交效率高于昨日')
  })

  it('renders result components nested inside report sections and tabs', async () => {
    mocks.portalApi.getSubmission.mockResolvedValueOnce({
      id: 'sub-1',
      skill_id: 'skill-1',
      skill_display_name: '投放日报',
      status: 'completed',
      params: { date: '2026-05-21' },
      result_summary: {
        type: 'text',
        roi: 0.32,
        analysis: '成交效率高于昨日',
      },
    })
    mocks.portalApi.getSkillUi.mockResolvedValueOnce({
      surface: 'result',
      overlay: {},
      saved_prompt: '',
      merged_ui_schema_hash: 'sha256:nested',
      merged_schema: {
        components: [
          {
            id: 'section-main',
            type: 'report_section',
            title: '核心指标',
            children: [
              { id: 'roi', type: 'metric', title: 'ROI', binding: 'result.roi', format: 'percent' },
            ],
          },
          {
            id: 'tabs-main',
            type: 'tabs',
            title: '分析视图',
            children: [
              { id: 'summary', type: 'markdown_summary', title: '洞察', binding: 'result.analysis' },
            ],
          },
        ],
      },
      permissions: { read: true, execute: false, customize_ui: true },
    })

    const wrapper = mount(PortalSubmissionDetail, {
      global: {
        stubs: stubs(),
        mocks: { $router: mocks.router },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('核心指标 / ROI')
    expect(wrapper.text()).toContain('32%')
    expect(wrapper.text()).toContain('分析视图 / 洞察')
    expect(wrapper.text()).toContain('成交效率高于昨日')
  })

  it('warns when stale personal result UI is invalidated on load', async () => {
    mocks.portalApi.getSkillUi.mockResolvedValueOnce({
      surface: 'result',
      overlay: {},
      saved_prompt: null,
      ui_pref_invalidated: {
        ui_pref_id: 'uipref-stale',
        ui_pref_version: 3,
        reason: 'unknown param binding: params.removed',
      },
      merged_ui_schema_hash: 'sha256:base',
      merged_schema: {
        components: [{ id: 'result-table', type: 'table', binding: 'result.data' }],
      },
      permissions: { read: true, execute: false, customize_ui: true },
    })

    mount(PortalSubmissionDetail, {
      global: {
        stubs: stubs(),
        mocks: { $router: mocks.router },
      },
    })
    await flushPromises()

    expect(mocks.messages.warning).toHaveBeenCalledWith('个人结果界面已因 Skill 界面结构变化失效，已恢复默认界面')
  })

  it('refreshes result UI when saving hits a concurrent preference conflict', async () => {
    mocks.portalApi.saveSkillUiPreference.mockRejectedValueOnce({
      _backendCode: 'UI_PREF_CONFLICT',
      _backendMessage: '个人界面已被其他请求更新，请刷新后重试',
    })
    mocks.portalApi.getSkillUi
      .mockResolvedValueOnce({
        surface: 'result',
        overlay: {},
        saved_prompt: '',
        merged_ui_schema_hash: 'sha256:base',
        merged_schema: {
          components: [{ id: 'result-table', type: 'table', binding: 'result.data' }],
        },
        permissions: { read: true, execute: false, customize_ui: true },
      })
      .mockResolvedValueOnce({
        surface: 'result',
        overlay: {},
        saved_prompt: '最新结果提示词',
        merged_ui_schema_hash: 'sha256:latest',
        merged_schema: {
          components: [{ id: 'result-table', type: 'table', binding: 'result.data' }],
        },
        permissions: { read: true, execute: false, customize_ui: true },
      })

    const wrapper = mount(PortalSubmissionDetail, {
      global: {
        stubs: stubs(),
        mocks: { $router: mocks.router },
      },
    })
    await flushPromises()

    await findResultAiFloatingButton(wrapper).trigger('click')
    await flushPromises()
    await wrapper.get('textarea').setValue('调整结果界面')
    await findButton(wrapper, '发送给 AI').trigger('click')
    await flushPromises()
    await findButton(wrapper, '保存到我的页面').trigger('click')
    await flushPromises()

    expect(mocks.portalApi.saveSkillUiPreference).toHaveBeenCalledWith('skill-1', expect.objectContaining({
      surface: 'result',
      prompt_summary: '调整结果界面',
    }))
    expect(mocks.messages.warning).toHaveBeenCalledWith('个人界面已被其他请求更新，请刷新后重试')
    expect(mocks.portalApi.getSkillUi).toHaveBeenCalledTimes(2)
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('最新结果提示词')
  })

  it('runs trace analysis and creates a training candidate from the submission result', async () => {
    const wrapper = mount(PortalSubmissionDetail, {
      global: {
        stubs: stubs(),
        mocks: { $router: mocks.router },
      },
    })
    await flushPromises()

    await findButton(wrapper, '运行追溯').trigger('click')
    await flushPromises()

    expect(mocks.runTraceApi.getExecutionTrace).toHaveBeenCalledWith('run-1')
    expect(wrapper.text()).toContain('采集 Proof')
    expect(wrapper.text()).toContain('决策记录')

    await findButton(wrapper, 'AI 复盘').trigger('click')
    await flushPromises()

    expect(mocks.runTraceApi.analyzeExecutionTrace).toHaveBeenCalledWith('run-1', {
      include_raw: false,
      max_output_tokens: 4096,
    })
    expect(wrapper.text()).toContain('本次运行缺少库存 proof')

    await findButton(wrapper, '训练候选').trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.createRunCandidate).toHaveBeenCalledWith('run-1', expect.objectContaining({
      model_family: 'skill-1:run_trace_improvement',
      analysis_summary: '本次运行缺少库存 proof，建议补齐采集。',
      analysis_model: 'agent:analysis-node',
      analysis_prompt_hash: 'hash-run-1',
      raw_counts: { execution_steps: 2, decision_logs: 1 },
    }))
    expect(mocks.router.push).toHaveBeenCalledWith('/training/jobs/train-run-1')
  })
})
