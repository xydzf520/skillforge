import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  portalApi: {
    getSkill: vi.fn(),
    getSkillUi: vi.fn(),
    previewSkillUi: vi.fn(),
    saveSkillUiPreference: vi.fn(),
    listSkillUiPreferences: vi.fn(),
    restoreSkillUiPreference: vi.fn(),
    deleteSkillUiPreference: vi.fn(),
    submitSkill: vi.fn(),
    listSubmissions: vi.fn(),
  },
  router: {
    push: vi.fn(),
  },
  route: {
    params: { id: 'skill-1' },
    query: {} as Record<string, unknown>,
  },
  messages: {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
  },
}))

vi.mock('@/api', () => ({
  portalApi: mocks.portalApi,
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => mocks.router,
}))

vi.mock('@arco-design/web-vue', () => ({
  Input: { props: ['modelValue'], template: '<input :value="modelValue" />' },
  InputNumber: { template: '<input type="number" />' },
  Switch: { template: '<input type="checkbox" />' },
  Select: { template: '<select />' },
  Message: mocks.messages,
}))

vi.mock('@arco-design/web-vue/es/icon', () => ({
  IconLeft: { template: '<span />' },
  IconPlayArrow: { template: '<span />' },
  IconRefresh: { template: '<span />' },
  IconRobot: { template: '<span />' },
  IconSave: { template: '<span />' },
  IconSend: { template: '<span />' },
}))

import PortalSkillDetail from '@/pages/portal/PortalSkillDetail.vue'

function stubs() {
  return {
    'a-alert': { props: ['title'], template: '<div>{{ title }}<slot /></div>' },
    'a-button': {
      props: ['disabled', 'loading'],
      emits: ['click'],
      template: '<button :disabled="disabled || loading" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>',
    },
    'a-card': { props: ['title'], template: '<section><h3>{{ title }}</h3><slot name="extra" /><slot /></section>' },
    'a-col': { template: '<div><slot /></div>' },
    'a-descriptions': { template: '<dl><slot /></dl>' },
    'a-descriptions-item': { props: ['label'], template: '<div><dt>{{ label }}</dt><dd><slot /></dd></div>' },
    'a-empty': { props: ['description'], template: '<div>{{ description }}</div>' },
    'a-form': { template: '<form><slot /></form>' },
    'a-form-item': { props: ['label', 'help'], template: '<label>{{ label }}<slot /></label>' },
    'a-input': { template: '<input />' },
    'a-input-number': { template: '<input />' },
    'a-link': { template: '<a><slot /></a>' },
    'a-modal': {
      props: ['visible', 'title'],
      template: '<section v-if="visible" class="modal"><h3>{{ title }}</h3><slot /></section>',
    },
    'a-result': { props: ['title'], template: '<div>{{ title }}</div>' },
    'a-row': { template: '<div><slot /></div>' },
    'a-select': { template: '<select><slot /></select>' },
    'a-space': { template: '<div><slot /></div>' },
    'a-spin': { template: '<div><slot /></div>' },
    'a-switch': { template: '<input type="checkbox" />' },
    'a-table': { template: '<table><slot name="columns" /></table>' },
    'a-table-column': { props: ['title'], template: '<th>{{ title }}</th>' },
    'a-tag': { template: '<span><slot /></span>' },
    'a-textarea': {
      props: ['modelValue', 'disabled'],
      emits: ['update:modelValue'],
      template: '<textarea :disabled="disabled" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    },
    'a-tooltip': { template: '<span><slot /></span>' },
    'a-typography-text': { template: '<span><slot /></span>' },
  }
}

function findButton(wrapper: any, label: string) {
  const button = wrapper.findAll('button').find((item: any) => item.text().includes(label))
  if (!button) throw new Error(`button not found: ${label}`)
  return button
}

function findAiFloatingButton(wrapper: any) {
  const button = wrapper.find('button[aria-label="AI 调整页面"]')
  if (!button.exists()) throw new Error('AI floating button not found')
  return button
}

describe('PortalSkillDetail run UI preference', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.query = {}
    mocks.portalApi.getSkill.mockResolvedValue({
      id: 'skill-1',
      name: 'ad-daily',
      display_name: '投放日报',
      summary: '汇总投放效果',
      status: 'active',
      visibility: 'company',
      param_ui_schema: {
        type: 'object',
        properties: {
          date: { type: 'string', title: '日期' },
        },
      },
      recent_runs: [],
      permissions: { read: true, execute: true },
    })
    mocks.portalApi.getSkillUi.mockImplementation((_id: string, options?: { surface?: string }) => {
      const surface = options?.surface || 'run_form'
      return Promise.resolve({
        surface,
        overlay: {},
        saved_prompt: '',
        merged_ui_schema_hash: `sha256:base-${surface}`,
        merged_schema: {
          components: surface === 'result'
            ? [{ id: 'result-table', type: 'table', binding: 'result.data', title: '结果' }]
            : [{ id: 'field-date', type: 'field', binding: 'params.date', title: '日期' }],
        },
        permissions: { read: true, execute: true, customize_ui: true },
      })
    })
    mocks.portalApi.listSkillUiPreferences.mockResolvedValue({ items: [], total: 0 })
    mocks.portalApi.restoreSkillUiPreference.mockResolvedValue(null)
  })

  it('opens the AI page dialog when linked from the Portal card entry', async () => {
    mocks.route.query = { focus: 'ui' }

    const wrapper = mount(PortalSkillDetail, {
      global: {
        stubs: stubs(),
        mocks: { $router: mocks.router },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('AI 对话调整 Skill 页面')
    expect(wrapper.text()).toContain('运行页面')
  })

  it('opens a floating AI conversation entry inside the Skill detail', async () => {
    const wrapper = mount(PortalSkillDetail, {
      global: {
        stubs: stubs(),
        mocks: { $router: mocks.router },
      },
    })
    await flushPromises()

    await findAiFloatingButton(wrapper).trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('AI 对话调整 Skill 页面')
    expect(wrapper.text()).toContain('发送给 AI')
  })

  it('shows saved UI history and restores a previous run page version', async () => {
    mocks.portalApi.listSkillUiPreferences
      .mockResolvedValueOnce({
        items: [
          { id: 'pref-2', version: 2, enabled: true, generated_by: 'ai', prompt_summary: '当前版本' },
          { id: 'pref-1', version: 1, enabled: false, generated_by: 'manual', prompt_summary: '旧版运行页' },
        ],
        total: 2,
      })
      .mockResolvedValueOnce({
        items: [
          { id: 'pref-3', version: 3, enabled: true, generated_by: 'manual', prompt_summary: '旧版运行页' },
        ],
        total: 3,
      })
    mocks.portalApi.restoreSkillUiPreference.mockResolvedValueOnce({
      surface: 'run_form',
      ui_pref_id: 'pref-3',
      ui_pref_version: 3,
      overlay: {
        schema_version: 'skill-ui/v1',
        surface: 'run_form',
        operations: [{ op: 'rename_title', component_id: 'field-date', title: '旧版日期' }],
      },
      saved_prompt: '旧版运行页',
      merged_ui_schema_hash: 'sha256:restored',
      merged_schema: {
        components: [{ id: 'field-date', type: 'field', binding: 'params.date', title: '旧版日期' }],
      },
      permissions: { read: true, execute: true, customize_ui: true },
    })

    const wrapper = mount(PortalSkillDetail, {
      global: {
        stubs: stubs(),
        mocks: { $router: mocks.router },
      },
    })
    await flushPromises()

    await findAiFloatingButton(wrapper).trigger('click')
    await flushPromises()

    expect(mocks.portalApi.listSkillUiPreferences).toHaveBeenCalledWith('skill-1', { surface: 'run_form', limit: 20 })
    expect(wrapper.text()).toContain('保存记录')
    expect(wrapper.text()).toContain('旧版运行页')

    const oldVersion = wrapper.findAll('.ui-history-item').find(item => item.text().includes('v1'))
    expect(oldVersion?.exists()).toBe(true)
    await oldVersion!.trigger('click')
    await flushPromises()

    expect(mocks.portalApi.restoreSkillUiPreference).toHaveBeenCalledWith('skill-1', 'pref-1', { surface: 'run_form' })
    expect(wrapper.text()).toContain('已恢复运行页面 v1')
    expect(mocks.messages.success).toHaveBeenCalledWith('已恢复到历史版本')
  })

  it('sends a conversational run page adjustment to the AI preview endpoint', async () => {
    mocks.portalApi.previewSkillUi.mockResolvedValueOnce({
      surface: 'run_form',
      overlay: {
        schema_version: 'skill-ui/v1',
        surface: 'run_form',
        operations: [{ op: 'rename_title', component_id: 'field-date', title: '业务日期' }],
      },
      saved_prompt: '',
      merged_ui_schema_hash: 'sha256:run-preview',
      merged_schema: {
        components: [{ id: 'field-date', type: 'field', binding: 'params.date', title: '业务日期' }],
      },
      permissions: { read: true, execute: true, customize_ui: true },
    })

    const wrapper = mount(PortalSkillDetail, {
      global: {
        stubs: stubs(),
        mocks: { $router: mocks.router },
      },
    })
    await flushPromises()

    await findAiFloatingButton(wrapper).trigger('click')
    await flushPromises()
    await wrapper.get('textarea').setValue('把日期字段改名为业务日期')
    await findButton(wrapper, '发送给 AI').trigger('click')
    await flushPromises()

    expect(mocks.portalApi.previewSkillUi).toHaveBeenCalledWith('skill-1', {
      surface: 'run_form',
      instruction: '把日期字段改名为业务日期',
      current_overlay: {},
    })
    expect(wrapper.text()).toContain('已更新页面预览')
  })

  it('keeps visible save and restore controls after a run page preview', async () => {
    mocks.portalApi.previewSkillUi.mockResolvedValueOnce({
      surface: 'run_form',
      overlay: {
        schema_version: 'skill-ui/v1',
        surface: 'run_form',
        operations: [{ op: 'rename_title', component_id: 'field-date', title: '业务日期' }],
      },
      saved_prompt: '',
      merged_ui_schema_hash: 'sha256:run-preview',
      merged_schema: {
        components: [{ id: 'field-date', type: 'field', binding: 'params.date', title: '业务日期' }],
      },
      permissions: { read: true, execute: true, customize_ui: true },
    })
    mocks.portalApi.saveSkillUiPreference.mockResolvedValueOnce({
      surface: 'run_form',
      ui_pref_id: 'pref-1',
      ui_pref_version: 2,
      overlay: {
        schema_version: 'skill-ui/v1',
        surface: 'run_form',
      },
      saved_prompt: '把日期字段改名为业务日期',
      merged_ui_schema_hash: 'sha256:run-saved',
      merged_schema: {
        components: [{ id: 'field-date', type: 'field', binding: 'params.date', title: '业务日期' }],
      },
      permissions: { read: true, execute: true, customize_ui: true },
    })
    mocks.portalApi.deleteSkillUiPreference.mockResolvedValueOnce({
      surface: 'run_form',
      overlay: {},
      saved_prompt: '',
      merged_ui_schema_hash: 'sha256:base-run_form',
      merged_schema: {
        components: [{ id: 'field-date', type: 'field', binding: 'params.date', title: '日期' }],
      },
      permissions: { read: true, execute: true, customize_ui: true },
    })

    const wrapper = mount(PortalSkillDetail, {
      global: {
        stubs: stubs(),
        mocks: { $router: mocks.router },
      },
    })
    await flushPromises()

    await findAiFloatingButton(wrapper).trigger('click')
    await flushPromises()
    await wrapper.get('textarea').setValue('把日期字段改名为业务日期')
    await findButton(wrapper, '发送给 AI').trigger('click')
    await flushPromises()

    const strip = wrapper.get('.ui-save-strip')
    expect(strip.text()).toContain('未保存的运行页面预览')
    const restoreBeforeSave = strip.findAll('button').find(button => button.text().includes('恢复默认'))
    expect(restoreBeforeSave?.attributes('disabled')).toBeUndefined()

    const saveButton = strip.findAll('button').find(button => button.text().includes('保存运行页面'))
    expect(saveButton).toBeTruthy()
    await saveButton!.trigger('click')
    await flushPromises()

    expect(mocks.portalApi.saveSkillUiPreference).toHaveBeenCalledWith('skill-1', {
      surface: 'run_form',
      overlay: {
        schema_version: 'skill-ui/v1',
        surface: 'run_form',
        operations: [{ op: 'rename_title', component_id: 'field-date', title: '业务日期' }],
      },
      generated_by: 'ai',
      prompt_summary: '把日期字段改名为业务日期',
    })
    expect(wrapper.text()).toContain('界面 v2')

    const restoreAfterSave = wrapper.get('.ui-save-strip').findAll('button').find(button => button.text().includes('恢复默认'))
    await restoreAfterSave!.trigger('click')
    await flushPromises()

    expect(mocks.portalApi.deleteSkillUiPreference).toHaveBeenCalledWith('skill-1', { surface: 'run_form' })
  })

  it('keeps the AI UI entry discoverable when customization is not allowed', async () => {
    mocks.portalApi.getSkill.mockResolvedValueOnce({
      id: 'skill-1',
      name: 'ad-daily',
      display_name: '投放日报',
      summary: '汇总投放效果',
      status: 'active',
      visibility: 'company',
      param_ui_schema: {
        type: 'object',
        properties: {
          date: { type: 'string', title: '日期' },
        },
      },
      recent_runs: [],
      permissions: { read: true, execute: false },
    })
    mocks.portalApi.getSkillUi.mockImplementation((_id: string, options?: { surface?: string }) => Promise.resolve({
      surface: options?.surface || 'run_form',
      overlay: {},
      saved_prompt: '',
      merged_ui_schema_hash: 'sha256:base',
      merged_schema: {
        components: [{ id: 'field-date', type: 'field', binding: 'params.date', title: '日期' }],
      },
      permissions: { read: true, execute: false, customize_ui: false },
    }))

    const wrapper = mount(PortalSkillDetail, {
      global: {
        stubs: stubs(),
        mocks: { $router: mocks.router },
      },
    })
    await flushPromises()

    const entryButton = findAiFloatingButton(wrapper)
    expect(entryButton.attributes('disabled')).toBeDefined()
  })

  it('warns when stale personal run UI is invalidated on load', async () => {
    mocks.portalApi.getSkillUi.mockResolvedValueOnce({
      surface: 'run_form',
      overlay: {},
      saved_prompt: null,
      ui_pref_invalidated: {
        ui_pref_id: 'uipref-stale',
        ui_pref_version: 2,
        reason: 'unknown personal default: removed_field',
      },
      merged_ui_schema_hash: 'sha256:base',
      merged_schema: {
        components: [{ id: 'field-date', type: 'field', binding: 'params.date', title: '日期' }],
      },
      permissions: { read: true, execute: true, customize_ui: true },
    })

    mount(PortalSkillDetail, {
      global: {
        stubs: stubs(),
        mocks: { $router: mocks.router },
      },
    })
    await flushPromises()

    expect(mocks.messages.warning).toHaveBeenCalledWith('个人界面已因 Skill 界面结构变化失效，已恢复默认界面')
  })

  it('refreshes run UI when saving hits a concurrent preference conflict', async () => {
    mocks.portalApi.saveSkillUiPreference.mockRejectedValueOnce({
      _backendCode: 'UI_PREF_CONFLICT',
      _backendMessage: '个人界面已被其他请求更新，请刷新后重试',
    })
    mocks.portalApi.previewSkillUi.mockResolvedValueOnce({
      surface: 'run_form',
      overlay: {
        schema_version: 'skill-ui/v1',
        surface: 'run_form',
        operations: [{ op: 'rename_title', component_id: 'field-date', title: '调整界面' }],
      },
      saved_prompt: '',
      merged_ui_schema_hash: 'sha256:preview',
      merged_schema: {
        components: [{ id: 'field-date', type: 'field', binding: 'params.date', title: '调整界面' }],
      },
      permissions: { read: true, execute: true, customize_ui: true },
    })
    mocks.portalApi.getSkillUi
      .mockResolvedValueOnce({
        surface: 'run_form',
        overlay: {},
        saved_prompt: '',
        merged_ui_schema_hash: 'sha256:base',
        merged_schema: {
          components: [{ id: 'field-date', type: 'field', binding: 'params.date', title: '日期' }],
        },
        permissions: { read: true, execute: true, customize_ui: true },
      })
      .mockResolvedValueOnce({
        surface: 'result',
        overlay: {},
        saved_prompt: '',
        merged_ui_schema_hash: 'sha256:result',
        merged_schema: {
          components: [{ id: 'result-table', type: 'table', binding: 'result.data', title: '结果' }],
        },
        permissions: { read: true, execute: true, customize_ui: true },
      })
      .mockResolvedValueOnce({
        surface: 'run_form',
        overlay: {},
        saved_prompt: '最新提示词',
        merged_ui_schema_hash: 'sha256:latest',
        merged_schema: {
          components: [{ id: 'field-date', type: 'field', binding: 'params.date', title: '日期' }],
        },
        permissions: { read: true, execute: true, customize_ui: true },
      })

    const wrapper = mount(PortalSkillDetail, {
      global: {
        stubs: stubs(),
        mocks: { $router: mocks.router },
      },
    })
    await flushPromises()

    await findAiFloatingButton(wrapper).trigger('click')
    await flushPromises()
    await wrapper.find('textarea').setValue('调整界面')
    await findButton(wrapper, '发送给 AI').trigger('click')
    await flushPromises()
    await findButton(wrapper, '保存到我的页面').trigger('click')
    await flushPromises()

    expect(mocks.portalApi.saveSkillUiPreference).toHaveBeenCalled()
    expect(mocks.messages.warning).toHaveBeenCalledWith('个人界面已被其他请求更新，请刷新后重试')
    expect(mocks.portalApi.getSkillUi).toHaveBeenCalledTimes(3)
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('最新提示词')
  })

  it('exposes result UI customization from the skill detail entry', async () => {
    mocks.portalApi.previewSkillUi.mockResolvedValueOnce({
      surface: 'result',
      overlay: {
        schema_version: 'skill-ui/v1',
        surface: 'result',
        operations: [{ op: 'rename_title', component_id: 'result-table', title: '我的结果' }],
      },
      saved_prompt: '',
      merged_ui_schema_hash: 'sha256:result-preview',
      merged_schema: {
        components: [{ id: 'result-table', type: 'table', binding: 'result.data', title: '我的结果' }],
      },
      permissions: { read: true, execute: true, customize_ui: true },
    })
    mocks.portalApi.saveSkillUiPreference.mockResolvedValueOnce({
      surface: 'result',
      ui_pref_id: 'uipref-result',
      ui_pref_version: 2,
      overlay: {
        schema_version: 'skill-ui/v1',
        surface: 'result',
        operations: [{ op: 'rename_title', component_id: 'result-table', title: '我的结果' }],
      },
      saved_prompt: '结果表标题改成我的结果',
      merged_ui_schema_hash: 'sha256:result-saved',
      merged_schema: {
        components: [{ id: 'result-table', type: 'table', binding: 'result.data', title: '我的结果' }],
      },
      permissions: { read: true, execute: true, customize_ui: true },
    })
    mocks.portalApi.deleteSkillUiPreference.mockResolvedValueOnce({
      surface: 'result',
      overlay: {},
      saved_prompt: '',
      merged_ui_schema_hash: 'sha256:result-reset',
      merged_schema: {
        components: [{ id: 'result-table', type: 'table', binding: 'result.data', title: '结果' }],
      },
      permissions: { read: true, execute: true, customize_ui: true },
    })

    const wrapper = mount(PortalSkillDetail, {
      global: {
        stubs: stubs(),
        mocks: { $router: mocks.router },
      },
    })
    await flushPromises()

    expect(mocks.portalApi.getSkillUi).toHaveBeenCalledWith('skill-1', { surface: 'run_form' })
    expect(mocks.portalApi.getSkillUi).toHaveBeenCalledWith('skill-1', { surface: 'result' })

    await findAiFloatingButton(wrapper).trigger('click')
    await flushPromises()
    await findButton(wrapper, '结果页面').trigger('click')
    await flushPromises()
    await wrapper.get('textarea').setValue('结果表标题改成我的结果')
    await findButton(wrapper, '发送给 AI').trigger('click')
    await flushPromises()

    expect(mocks.portalApi.previewSkillUi).toHaveBeenCalledWith('skill-1', {
      surface: 'result',
      instruction: '结果表标题改成我的结果',
      current_overlay: {},
    })

    await findButton(wrapper, '保存到我的页面').trigger('click')
    await flushPromises()

    expect(mocks.portalApi.saveSkillUiPreference).toHaveBeenCalledWith('skill-1', {
      surface: 'result',
      overlay: {
        schema_version: 'skill-ui/v1',
        surface: 'result',
        operations: [{ op: 'rename_title', component_id: 'result-table', title: '我的结果' }],
      },
      generated_by: 'ai',
      prompt_summary: '结果表标题改成我的结果',
    })

    await findButton(wrapper, '恢复默认').trigger('click')
    await flushPromises()

    expect(mocks.portalApi.deleteSkillUiPreference).toHaveBeenCalledWith('skill-1', { surface: 'result' })
  })
})
