import { flushPromises, mount, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  route: {
    params: { id: 'gpt-imagegen' },
    query: {} as Record<string, unknown>,
  },
  router: {
    replace: vi.fn(),
  },
  hallApi: {
    directCapability: vi.fn(),
    directCapabilityUi: vi.fn(),
    previewDirectCapabilityUi: vi.fn(),
    saveDirectCapabilityUiPreference: vi.fn(),
    listDirectCapabilityUiPreferences: vi.fn(),
    restoreDirectCapabilityUiPreference: vi.fn(),
    deleteDirectCapabilityUiPreference: vi.fn(),
    createDirectCapabilityTask: vi.fn(),
    directCapabilityTasks: vi.fn(),
    directCapabilityTask: vi.fn(),
    directCapabilityImageHistory: vi.fn(),
    runDirectCapability: vi.fn(),
  },
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => mocks.router,
}))

vi.mock('@/api', () => ({
  hallApi: mocks.hallApi,
}))

import DirectCapabilityUiDesigner from '@/components/hall/DirectCapabilityUiDesigner.vue'
import HallGptImageGenForm from '@/pages/hall/HallGptImageGenForm.vue'
import HallGptImageGenWorkspace from '@/pages/hall/HallGptImageGenWorkspace.vue'

function arcoStubs() {
  return {
    HallDetailHeader: { template: '<header><slot name="actions" /></header>' },
    GptImageParamInspector: true,
    'a-button': {
      props: ['disabled', 'loading'],
      emits: ['click'],
      template: '<button :disabled="disabled || loading" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>',
    },
    'a-dropdown': { template: '<div><slot /><slot name="content" /></div>' },
    'a-doption': {
      props: ['disabled'],
      emits: ['click'],
      template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
    },
    'a-tooltip': { template: '<span><slot /></span>' },
    'a-tag': { template: '<span><slot /></span>' },
    'a-typography-text': { template: '<span><slot /></span>' },
    'a-spin': { template: '<div><slot /></div>' },
    'a-modal': {
      props: ['visible'],
      template: '<section v-if="visible"><slot /></section>',
    },
    'a-textarea': {
      props: ['modelValue', 'disabled'],
      emits: ['update:modelValue'],
      template: '<textarea :disabled="disabled" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    },
    'a-empty': { template: '<div />' },
  }
}

describe('Hall GPT ImageGen page mode entry', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mocks.route.params = { id: 'gpt-imagegen' }
    mocks.route.query = {}
    mocks.hallApi.directCapability.mockResolvedValue({
      id: 'gpt-imagegen',
      display_name: 'GPT ImageGen',
      input_schema: { type: 'object', properties: {} },
      permissions: { read: true, execute: true },
      project_surface: {
        enabled: true,
        virtual_project_id: 'direct_gpt_imagegen',
        boundary: 'Hall 直接运行 Skill，按项目运行面拆分。',
        segments: [
          { key: 'run', label: '运行面', summary: '输入参数由平台提交。' },
          { key: 'design', label: '设计面', summary: '个人页面 overlay。' },
          { key: 'history', label: '记录面', summary: '任务和产物历史。' },
          { key: 'loop', label: 'AI 循环面', summary: '报告待办进入学习。' },
        ],
      },
    })
    mocks.hallApi.directCapabilityUi.mockResolvedValue({
      skill_id: 'gpt-imagegen',
      capability_id: 'gpt-imagegen',
      surface: 'run_form',
      overlay: {},
      merged_schema: { components: [] },
      merged_ui_schema_hash: 'sha256:default',
      permissions: { customize_ui: true },
    })
    mocks.hallApi.previewDirectCapabilityUi.mockResolvedValue({
      skill_id: 'gpt-imagegen',
      capability_id: 'gpt-imagegen',
      surface: 'run_form',
      overlay: { schema_version: 'skill-ui/v1', surface: 'run_form' },
      merged_schema: { components: [] },
      merged_ui_schema_hash: 'sha256:preview',
      permissions: { customize_ui: true },
      ai_status: 'llm',
    })
    mocks.hallApi.saveDirectCapabilityUiPreference.mockResolvedValue({
      skill_id: 'gpt-imagegen',
      capability_id: 'gpt-imagegen',
      surface: 'run_form',
      ui_pref_id: 'uipref-1',
      ui_pref_version: 1,
      overlay: {},
      merged_schema: { components: [] },
      merged_ui_schema_hash: 'sha256:saved',
      permissions: { customize_ui: true },
    })
    mocks.hallApi.listDirectCapabilityUiPreferences.mockResolvedValue({ items: [], total: 0, surface: 'run_form' })
    mocks.hallApi.restoreDirectCapabilityUiPreference.mockResolvedValue({
      skill_id: 'gpt-imagegen',
      capability_id: 'gpt-imagegen',
      surface: 'run_form',
      ui_pref_id: 'uipref-restored',
      ui_pref_version: 3,
      overlay: {},
      merged_schema: { components: [] },
      merged_ui_schema_hash: 'sha256:restored',
      permissions: { customize_ui: true },
    })
    mocks.hallApi.deleteDirectCapabilityUiPreference.mockResolvedValue({
      skill_id: 'gpt-imagegen',
      capability_id: 'gpt-imagegen',
      surface: 'run_form',
      overlay: {},
      merged_schema: { components: [] },
      merged_ui_schema_hash: 'sha256:default',
      permissions: { customize_ui: true },
    })
    mocks.hallApi.directCapabilityTasks.mockResolvedValue({ items: [], total: 0 })
    mocks.hallApi.directCapabilityTask.mockResolvedValue({})
    mocks.hallApi.directCapabilityImageHistory.mockResolvedValue({ items: [], total: 0, has_more: false })
    mocks.hallApi.runDirectCapability.mockResolvedValue({})
  })

  it('routes the same ability page to chat mode from the wrapper', async () => {
    const wrapper = mount(HallGptImageGenForm, {
      global: {
        stubs: {
          HallGptImageGenWorkspace: {
            emits: ['switch-chat'],
            template: '<button class="switch-chat" @click="$emit(\'switch-chat\')">对话页面</button>',
          },
          HallGptImageGenChat: { template: '<div>chat page</div>' },
          HallGptImageGenLegacy: { template: '<div>legacy page</div>' },
          DirectCapabilityUiDesigner: true,
        },
      },
    })

    await wrapper.find('.switch-chat').trigger('click')

    expect(mocks.router.replace).toHaveBeenCalledWith({
      query: { mode: 'chat' },
    })
    expect(localStorage.getItem('skillforge:gpt-imagegen:view-mode')).toBe('chat')
  })

  it('keeps the runnable ability page free of redundant route chrome', async () => {
    const wrapper = mount(HallGptImageGenForm, {
      global: {
        stubs: {
          HallGptImageGenWorkspace: { template: '<div>image workspace</div>' },
          HallGptImageGenChat: { template: '<div />' },
          HallGptImageGenLegacy: { template: '<div />' },
          DirectCapabilityUiDesigner: true,
        },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('image workspace')
    expect(wrapper.text()).not.toContain('能力大厅 / 图像生成')
    expect(wrapper.text()).not.toContain('输入提示词或参考图')
    expect(wrapper.text()).not.toContain('工作区对话高级表单')
    expect(wrapper.text()).not.toContain('项目设计分割')
    expect(wrapper.text()).not.toContain('虚拟项目')
    expect(wrapper.text()).not.toContain('AI 循环面')
  })

  it('keeps mode switching under more functions on the workspace page', async () => {
    const wrapper = shallowMount(HallGptImageGenWorkspace, {
      global: { stubs: arcoStubs() },
    })

    expect(wrapper.text()).not.toContain('页面调整')
    const chatOption = wrapper.findAll('button').find(button => button.text().includes('对话页面'))
    expect(chatOption).toBeTruthy()
    await chatOption!.trigger('click')

    expect(wrapper.emitted('switch-chat')).toHaveLength(1)
    wrapper.unmount()
  })

  it('keeps workspace list polling from advancing queued image generation tasks', async () => {
    const wrapper = shallowMount(HallGptImageGenWorkspace, {
      global: { stubs: arcoStubs() },
    })
    await flushPromises()

    expect(mocks.hallApi.directCapabilityTasks).toHaveBeenCalledWith('gpt-imagegen', {
      page: 1,
      page_size: 50,
      advance: false,
    })
    const params = mocks.hallApi.directCapabilityTasks.mock.calls[0][1]
    expect(params).toHaveProperty('advance', false)
    wrapper.unmount()
  })

  it('keeps generation available when a large history request fails', async () => {
    mocks.hallApi.directCapabilityImageHistory.mockRejectedValueOnce(new Error('timeout'))

    const wrapper = shallowMount(HallGptImageGenWorkspace, {
      global: { stubs: arcoStubs() },
    })
    await flushPromises()

    expect(wrapper.text()).not.toContain('能力不可用')
    expect(wrapper.text()).toContain('历史图片暂时加载失败，不影响新任务生成。')
    expect(mocks.hallApi.directCapabilityImageHistory).toHaveBeenCalledWith('gpt-imagegen', {
      page: 1,
      page_size: 40,
    })
    wrapper.unmount()
  })

  it('shows server history by default even when it has no current workspace id', async () => {
    mocks.hallApi.directCapabilityImageHistory.mockResolvedValueOnce({
      items: [{
        id: 88,
        url: 'https://img.example.com/legacy-history.png',
        name: 'legacy-history.png',
        extra: {},
        created_at: '2026-06-30T12:10:00+08:00',
      }],
      total: 1,
      has_more: false,
    })

    const wrapper = shallowMount(HallGptImageGenWorkspace, {
      global: { stubs: arcoStubs() },
    })
    await flushPromises()

    expect(wrapper.find('.history-card img[src="https://img.example.com/legacy-history.png"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('shows completed images for the current workspace even when a saved workspace id changed', async () => {
    localStorage.setItem('skillforge:gpt-imagegen:workspaces:gpt-imagegen', JSON.stringify({
      activeWorkspaceId: 'ws-current',
      items: [{
        id: 'ws-current',
        name: '工作区1',
        form: { prompt: '白底商品图', operation: 'generate', size: '1:1', resolution: '1K', n: 1, references: [] },
      }],
    }))
    mocks.hallApi.directCapabilityTasks.mockResolvedValueOnce({
      items: [{
        id: 'task-new',
        workspace_id: 'ws-old',
        workspace_name: '工作区1',
        status: 'completed',
        prompt: '白底商品图',
        image_urls: ['https://img.example.com/new.png'],
        created_at: '2026-06-30T12:00:00+08:00',
      }],
      total: 1,
      active_limit: 3,
    })

    const wrapper = shallowMount(HallGptImageGenWorkspace, {
      global: { stubs: arcoStubs() },
    })
    await flushPromises()

    expect(wrapper.find('img[src="https://img.example.com/new.png"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('treats a task as completed when history already contains its upstream image', async () => {
    localStorage.setItem('skillforge:gpt-imagegen:workspaces:gpt-imagegen', JSON.stringify({
      activeWorkspaceId: 'ws-current',
      items: [{
        id: 'ws-current',
        name: '工作区1',
        form: { prompt: 'Banner', operation: 'generate', size: '16:9', resolution: '2K', n: 1, references: [] },
      }],
    }))
    mocks.hallApi.directCapabilityImageHistory.mockResolvedValueOnce({
      items: [{
        id: 99,
        url: 'https://img.example.com/history.png',
        name: 'history.png',
        task_id: 'upstream-99',
        extra: { workspace_id: 'ws-current', workspace_name: '工作区1' },
        created_at: '2026-06-30T12:10:00+08:00',
      }],
      total: 1,
      has_more: false,
    })
    mocks.hallApi.directCapabilityTasks.mockResolvedValueOnce({
      items: [{
        id: 'task-running',
        workspace_id: 'ws-current',
        workspace_name: '工作区1',
        upstream_task_id: 'upstream-99',
        status: 'in_progress',
        prompt: 'Banner',
        result: { result: { status: 'in_progress', task_id: 'upstream-99' } },
        created_at: '2026-06-30T12:09:00+08:00',
      }],
      total: 1,
      active_limit: 3,
    })

    const wrapper = shallowMount(HallGptImageGenWorkspace, {
      global: { stubs: arcoStubs() },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('完成')
    expect(wrapper.text()).not.toContain('生成中')
    expect(wrapper.find('img[src="https://img.example.com/history.png"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('keeps refreshing history for a completed task until its delayed image appears', async () => {
    vi.useFakeTimers()
    try {
      localStorage.setItem('skillforge:gpt-imagegen:workspaces:gpt-imagegen', JSON.stringify({
        activeWorkspaceId: 'ws-current',
        items: [{
          id: 'ws-current',
          name: '工作区1',
          form: { prompt: '主图', operation: 'generate', size: '1:1', resolution: '1K', n: 1, references: [] },
        }],
      }))
      mocks.hallApi.directCapabilityTasks.mockResolvedValue({
        items: [{
          id: 'task-completed-no-image',
          workspace_id: 'ws-current',
          workspace_name: '工作区1',
          upstream_task_id: 'upstream-delayed',
          status: 'completed',
          prompt: '主图',
          result: { result: { status: 'completed', task_id: 'upstream-delayed' } },
          created_at: '2026-06-30T12:20:00+08:00',
        }],
        total: 1,
        active_limit: 3,
      })
      mocks.hallApi.directCapabilityImageHistory
        .mockResolvedValueOnce({ items: [], total: 0, has_more: false })
        .mockResolvedValueOnce({
          items: [{
            id: 100,
            url: 'https://img.example.com/delayed.png',
            name: 'delayed.png',
            task_id: 'upstream-delayed',
            extra: { workspace_id: 'ws-current', workspace_name: '工作区1' },
            created_at: '2026-06-30T12:21:00+08:00',
          }],
          total: 1,
          has_more: false,
        })

      const wrapper = shallowMount(HallGptImageGenWorkspace, {
        global: { stubs: arcoStubs() },
      })
      await flushPromises()

      expect(wrapper.find('img[src="https://img.example.com/delayed.png"]').exists()).toBe(false)

      await vi.advanceTimersByTimeAsync(1000)
      await flushPromises()

      expect(mocks.hallApi.directCapabilityImageHistory).toHaveBeenCalledTimes(2)
      expect(wrapper.find('img[src="https://img.example.com/delayed.png"]').exists()).toBe(true)
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not discard a slow completed task response because polling fired again', async () => {
    vi.useFakeTimers()
    try {
      localStorage.setItem('skillforge:gpt-imagegen:workspaces:gpt-imagegen', JSON.stringify({
        activeWorkspaceId: 'ws-current',
        items: [{
          id: 'ws-current',
          name: '工作区1',
          form: { prompt: '主图', operation: 'generate', size: '1:1', resolution: '1K', n: 1, references: [] },
        }],
      }))
      let resolveTasks: (value: unknown) => void = () => {}
      mocks.hallApi.directCapabilityTasks.mockImplementationOnce(() => new Promise(resolve => {
        resolveTasks = resolve
      }))
      mocks.hallApi.directCapabilityImageHistory.mockResolvedValue({ items: [], total: 0, has_more: false })

      const wrapper = shallowMount(HallGptImageGenWorkspace, {
        global: { stubs: arcoStubs() },
      })
      await flushPromises()

      await vi.advanceTimersByTimeAsync(1000)
      await flushPromises()

      expect(mocks.hallApi.directCapabilityTasks).toHaveBeenCalledTimes(1)

      resolveTasks({
        items: [{
          id: 'task-slow-completed',
          workspace_id: 'ws-current',
          workspace_name: '工作区1',
          upstream_task_id: 'upstream-slow',
          status: 'completed',
          prompt: '主图',
          image_urls: ['https://img.example.com/slow.png'],
          created_at: '2026-06-30T13:33:00+08:00',
        }],
        total: 1,
        active_limit: 3,
      })
      await flushPromises()

      expect(wrapper.find('img[src="https://img.example.com/slow.png"]').exists()).toBe(true)
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('refreshes the current workspace from a watched submitted task without waiting for list polling', async () => {
    vi.useFakeTimers()
    try {
      localStorage.setItem('skillforge:gpt-imagegen:workspaces:gpt-imagegen', JSON.stringify({
        activeWorkspaceId: 'ws-current',
        items: [{
          id: 'ws-current',
          name: '工作区1',
          form: { prompt: '主图', operation: 'generate', size: '1:1', resolution: '1K', n: 1, references: [] },
        }],
      }))
      mocks.hallApi.createDirectCapabilityTask.mockResolvedValue({
        id: 'task-watch',
        workspace_id: 'ws-current',
        workspace_name: '工作区1',
        upstream_task_id: 'upstream-watch',
        status: 'in_progress',
        prompt: '主图',
        params: { prompt: '主图' },
        created_at: '2026-06-30T13:33:00+08:00',
      })
      mocks.hallApi.directCapabilityTask.mockResolvedValue({
        id: 'task-watch',
        workspace_id: 'ws-current',
        workspace_name: '工作区1',
        upstream_task_id: 'upstream-watch',
        status: 'completed',
        prompt: '主图',
        image_urls: ['https://img.example.com/watched.png'],
        completed_at: '2026-06-30T13:34:00+08:00',
        created_at: '2026-06-30T13:33:00+08:00',
      })

      const wrapper = shallowMount(HallGptImageGenWorkspace, {
        global: { stubs: arcoStubs() },
      })
      await flushPromises()

      const submit = wrapper.findAll('button').find(button => button.text().includes('提交到队列'))
      expect(submit).toBeTruthy()
      await submit!.trigger('click')
      await flushPromises()

      await vi.advanceTimersByTimeAsync(1000)
      await flushPromises()

      expect(mocks.hallApi.directCapabilityTask).toHaveBeenCalledWith('gpt-imagegen', 'task-watch', { advance: true })
      expect(wrapper.find('img[src="https://img.example.com/watched.png"]').exists()).toBe(true)
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps a watched completed task from being overwritten by a stale running list item', async () => {
    vi.useFakeTimers()
    try {
      localStorage.setItem('skillforge:gpt-imagegen:workspaces:gpt-imagegen', JSON.stringify({
        activeWorkspaceId: 'ws-current',
        items: [{
          id: 'ws-current',
          name: '工作区1',
          form: { prompt: '美妆主图', operation: 'generate', size: '1:1', resolution: '1K', n: 1, references: [] },
        }],
      }))
      mocks.hallApi.directCapabilityTasks.mockResolvedValue({
        items: [{
          id: 'task-stale',
          workspace_id: 'ws-current',
          workspace_name: '工作区1',
          upstream_task_id: 'tsk_img_stale',
          status: 'in_progress',
          prompt: '美妆主图',
          params: { prompt: '美妆主图', size: '1:1', resolution: '1K', n: 1 },
          result: { result: { status: 'in_progress', task_id: 'tsk_img_stale' } },
          created_at: '2026-06-30T14:08:00+08:00',
          updated_at: '2026-06-30T14:08:30+08:00',
        }],
        total: 1,
        active_limit: 3,
      })
      mocks.hallApi.createDirectCapabilityTask.mockResolvedValue({
        id: 'task-stale',
        workspace_id: 'ws-current',
        workspace_name: '工作区1',
        upstream_task_id: 'tsk_img_stale',
        status: 'in_progress',
        prompt: '美妆主图',
        params: { prompt: '美妆主图', size: '1:1', resolution: '1K', n: 1 },
        created_at: '2026-06-30T14:08:00+08:00',
      })
      mocks.hallApi.directCapabilityTask.mockResolvedValue({
        id: 'task-stale',
        workspace_id: 'ws-current',
        workspace_name: '工作区1',
        upstream_task_id: 'tsk_img_stale',
        status: 'completed',
        prompt: '美妆主图',
        params: { prompt: '美妆主图', size: '1:1', resolution: '1K', n: 1 },
        image_urls: ['https://img.example.com/completed.png'],
        completed_at: '2026-06-30T14:09:33+08:00',
        created_at: '2026-06-30T14:08:00+08:00',
        updated_at: '2026-06-30T14:09:33+08:00',
      })

      const wrapper = shallowMount(HallGptImageGenWorkspace, {
        global: { stubs: arcoStubs() },
      })
      await flushPromises()

      const submit = wrapper.findAll('button').find(button => button.text().includes('提交到队列'))
      expect(submit).toBeTruthy()
      await submit!.trigger('click')
      await flushPromises()

      await vi.advanceTimersByTimeAsync(1000)
      await flushPromises()

      expect(wrapper.find('img[src="https://img.example.com/completed.png"]').exists()).toBe(true)
      expect(wrapper.text()).toContain('完成')
      expect(wrapper.text()).not.toContain('生成中')

      await vi.advanceTimersByTimeAsync(1000)
      await flushPromises()

      expect(wrapper.find('img[src="https://img.example.com/completed.png"]').exists()).toBe(true)
      expect(wrapper.text()).toContain('完成')
      expect(wrapper.text()).not.toContain('生成中')
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('opens a floating AI dialog and previews page structure changes', async () => {
    const wrapper = mount(DirectCapabilityUiDesigner, {
      props: { capabilityId: 'gpt-imagegen', modelValue: null },
      global: { stubs: arcoStubs() },
    })
    await flushPromises()

    await wrapper.find('button[aria-label="AI 调整页面"]').trigger('click')
    await wrapper.find('textarea').setValue('把参数分组')
    const send = wrapper.findAll('button').find(button => button.text().includes('发送给 AI'))
    expect(send).toBeTruthy()
    await send!.trigger('click')
    await flushPromises()

    expect(mocks.hallApi.directCapabilityUi).toHaveBeenCalledWith('gpt-imagegen', { surface: 'run_form' })
    expect(mocks.hallApi.previewDirectCapabilityUi).toHaveBeenCalledWith('gpt-imagegen', {
      surface: 'run_form',
      instruction: '把参数分组',
      current_overlay: {},
    })
    expect(wrapper.text()).toContain('已生成页面结构预览')
    expect(wrapper.text()).toContain('预览未保存')

    await wrapper.find('.ui-state-action.primary').trigger('click')
    await flushPromises()

    expect(mocks.hallApi.saveDirectCapabilityUiPreference).toHaveBeenCalledWith('gpt-imagegen', {
      surface: 'run_form',
      overlay: { schema_version: 'skill-ui/v1', surface: 'run_form' },
      generated_by: 'ai',
      prompt_summary: '把参数分组',
    })
    expect(wrapper.text()).toContain('我的页面 v1')

    const restoreButton = wrapper.findAll('.ui-state-action').find(button => button.text().includes('恢复默认'))
    expect(restoreButton).toBeTruthy()
    await restoreButton!.trigger('click')
    await flushPromises()

    expect(mocks.hallApi.deleteDirectCapabilityUiPreference).toHaveBeenCalledWith('gpt-imagegen', { surface: 'run_form' })
  })

  it('shows saved UI history and restores a previous direct capability page version', async () => {
    mocks.hallApi.listDirectCapabilityUiPreferences
      .mockResolvedValueOnce({
        items: [
          {
            id: 'pref-2',
            version: 2,
            enabled: true,
            generated_by: 'ai',
            prompt_summary: '当前布局',
            component_count: 1,
            created_at: '2026-05-22T10:00:00+08:00',
          },
          {
            id: 'pref-1',
            version: 1,
            enabled: false,
            generated_by: 'ai',
            prompt_summary: '提示词放到最上面',
            component_count: 2,
            created_at: '2026-05-22T09:00:00+08:00',
          },
        ],
        total: 2,
        surface: 'run_form',
      })
      .mockResolvedValueOnce({
        items: [
          {
            id: 'pref-3',
            version: 3,
            enabled: true,
            generated_by: 'ai',
            prompt_summary: '提示词放到最上面',
            component_count: 2,
            created_at: '2026-05-22T11:00:00+08:00',
          },
        ],
        total: 3,
        surface: 'run_form',
      })

    const wrapper = mount(DirectCapabilityUiDesigner, {
      props: { capabilityId: 'gpt-imagegen', modelValue: null },
      global: { stubs: arcoStubs() },
    })
    await flushPromises()

    await wrapper.find('button[aria-label="AI 调整页面"]').trigger('click')
    await flushPromises()

    expect(mocks.hallApi.listDirectCapabilityUiPreferences).toHaveBeenCalledWith('gpt-imagegen', {
      surface: 'run_form',
      limit: 20,
    })
    expect(wrapper.text()).toContain('保存记录')
    expect(wrapper.text()).toContain('提示词放到最上面')

    const oldVersion = wrapper.findAll('.ui-history-item')
      .find(button => button.text().includes('提示词放到最上面'))
    expect(oldVersion).toBeTruthy()
    await oldVersion!.trigger('click')
    await flushPromises()

    expect(mocks.hallApi.restoreDirectCapabilityUiPreference).toHaveBeenCalledWith('gpt-imagegen', 'pref-1', {
      surface: 'run_form',
    })
    expect(wrapper.text()).toContain('已恢复页面 v1')
    expect(wrapper.text()).toContain('我的页面 v3')
  })
})
