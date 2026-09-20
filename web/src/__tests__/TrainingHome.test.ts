import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  trainingApi: {
    resources: vi.fn(),
    automationStatus: vi.fn(),
    runAutomation: vi.fn(),
    fullHistoryFinetuneStatus: vi.fn(),
    runFullHistoryFinetune: vi.fn(),
    jobs: vi.fn(),
    createJob: vi.fn(),
    jobLogs: vi.fn(),
    jobArtifacts: vi.fn(),
    downloadJobArtifact: vi.fn(),
    approveJob: vi.fn(),
    dispatchJob: vi.fn(),
    retryJob: vi.fn(),
    collectResult: vi.fn(),
    collectDue: vi.fn(),
    simulateJobResult: vi.fn(),
    evaluateJob: vi.fn(),
    requestDeployment: vi.fn(),
    cancelJob: vi.fn(),
    forceCancelJob: vi.fn(),
  },
  router: {
    push: vi.fn(),
    replace: vi.fn(),
  },
  route: {
    query: {} as Record<string, unknown>,
  },
}))

vi.mock('@/api', () => ({
  trainingApi: mocks.trainingApi,
}))

vi.mock('@arco-design/web-vue', () => ({
  Message: {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
  },
  Modal: {
    confirm: vi.fn(),
  },
}))

vi.mock('vue-router', () => ({
  useRouter: () => mocks.router,
  useRoute: () => mocks.route,
}))

vi.mock('@arco-design/web-vue/es/icon', () => ({
  IconClose: { template: '<span />' },
  IconFile: { template: '<span />' },
  IconPlus: { template: '<span />' },
  IconRefresh: { template: '<span />' },
  IconSend: { template: '<span />' },
  IconBook: { template: '<span />' },
  IconDashboard: { template: '<span />' },
  IconRelation: { template: '<span />' },
}))

import TrainingHome from '@/pages/training/TrainingHome.vue'

function defaultFullHistoryStatus() {
  return {
    ok: false,
    ready_to_run: true,
    model_profile: 'qwen3.5-4b',
    cycle_count: 3,
    completed_cycles: 0,
    current_step: 'create_cycle_job',
    current_cycle: 1,
    current_job_id: null,
    next_action: '创建第 1 轮 4B 训练任务',
    last_error: null,
    background_running: false,
    data_preparation: {
      status: 'completed',
      captured: { total_captured: 2 },
      materialized: { materialized: 3 },
    },
    automation_run: {
      id: 'fhauto-test',
      status: 'blocked',
      current_step: 'create_cycle_job',
      data_preparation: {
        status: 'completed',
        captured: { total_captured: 2 },
        materialized: { materialized: 3 },
      },
      actions: [],
    },
    selected: {
      training_gateway: { id: 'training-primary', name: 'GB10 237', online: true },
      deployment_gateway: { id: 'inference-primary', name: 'Mac236', online: true },
    },
    cycles: [
      { cycle_index: 1, status: 'pending', job: null, deployment: null },
      { cycle_index: 2, status: 'pending', job: null, deployment: null },
      { cycle_index: 3, status: 'pending', job: null, deployment: null },
    ],
    chat: { ready: false, disabled_reason: '等待完成三轮' },
    blockers: [],
  }
}

function stubs() {
  return {
    SfPageHeader: { props: ['title', 'subtitle'], template: '<header><h1>{{ title }}</h1><p>{{ subtitle }}</p><slot name="actions" /></header>' },
    SfKpiCard: { props: ['label', 'value', 'hint', 'suffix'], template: '<section><b>{{ label }}</b><span>{{ value }}</span><small>{{ hint }}</small><i>{{ suffix }}</i></section>' },
    SfEmptyState: { props: ['title', 'description', 'hint'], template: '<div>{{ title }}{{ description }}{{ hint }}</div>' },
    'a-button': {
      props: ['loading', 'disabled', 'type', 'status'],
      emits: ['click'],
      template: '<button :disabled="loading || disabled" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>',
    },
    'a-card': { props: ['title'], template: '<section><h2>{{ title }}</h2><slot /></section>' },
    'a-col': { template: '<div><slot /></div>' },
    'a-link': { emits: ['click'], template: '<a @click="$emit(\'click\')"><slot /></a>' },
    'a-form': { template: '<form><slot /></form>' },
    'a-form-item': { props: ['label'], template: '<label><span>{{ label }}</span><slot /><slot name="help" /></label>' },
    'a-input': {
      props: ['modelValue'],
      emits: ['update:modelValue'],
      template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    },
    'a-modal': {
      props: ['visible', 'title', 'okLoading'],
      emits: ['ok'],
      template: '<section v-if="visible"><h3>{{ title }}</h3><slot /><button class="modal-ok" :disabled="okLoading" @click="$emit(\'ok\')">确定</button></section>',
    },
    'a-option': { props: ['value'], template: '<option :value="value"><slot /></option>' },
    'a-progress': { props: ['percent'], template: '<progress :value="percent" />' },
    'a-result': { props: ['title'], template: '<div>{{ title }}</div>' },
    'a-row': { template: '<div><slot /></div>' },
    'a-space': { template: '<div><slot /></div>' },
    'a-spin': { template: '<div><slot /></div>' },
    'a-select': {
      props: ['modelValue'],
      emits: ['update:modelValue'],
      template: '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)"><slot /></select>',
    },
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

describe('TrainingHome', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.query = {}
    mocks.trainingApi.resources.mockResolvedValue({
      context: { department: 'EC', can_view_all: false },
      items: [{
        id: 'node-1',
        name: '训练网关 A',
        department: 'EC',
        online: true,
        gateway_kind: 'openclaw',
        training: {
          gateway: true,
          supported_tasks: ['lora', 'qlora', 'eval', 'merge', 'inference'],
        },
        gpu: [
          { name: 'RTX 4060', vram_total_mb: 8192, vram_used_mb: 2048, vram_free_mb: 6144, gpu_util_pct: 5 },
        ],
        gpu_count: 1,
        idle_gpu_count: 1,
        busy_gpu_count: 0,
        vram_total_gb: 8,
        vram_free_gb: 6,
        active_training_jobs_count: 1,
        active_training_jobs: [{
          id: 'train-1',
          title: '训练商品下滑动作推荐模型',
          status: 'queued',
          job_type: 'lora',
          target_skill_id: 'skill-recommend',
        }],
      }],
    })
    mocks.trainingApi.automationStatus.mockResolvedValue({
      ok: true,
      policy: {
        approve_jobs: true,
        deployment_request: true,
        activate_deployment: true,
      },
      factors: [
        { key: 'gb10_online', label: 'GB10 在线', ok: true, node_id: 'training-primary' },
        { key: 'dataset_sync', label: '训练数据同步', ok: true, pending: 0 },
        { key: 'mac236_online', label: 'Mac236 在线', ok: true, node_id: 'inference-primary' },
      ],
      blockers: [],
      summary: { dataset_versions: 1, dataset_unsynced: 0 },
      nodes: {},
    })
    mocks.trainingApi.fullHistoryFinetuneStatus.mockResolvedValue(defaultFullHistoryStatus())
    mocks.trainingApi.runFullHistoryFinetune.mockResolvedValue({
      ok: false,
      status: 'running_or_blocked',
      after: defaultFullHistoryStatus(),
      actions: [],
    })
    mocks.trainingApi.jobs.mockResolvedValue({
      items: [{
        id: 'train-1',
        title: '训练商品下滑动作推荐模型',
        department: 'EC',
        status: 'queued',
        job_type: 'lora',
        target_gateway_id: 'node-1',
        dataset_ref: 'dataset://ec/actions/v1',
        created_at: '2026-05-21T10:00:00+08:00',
      }],
    })
    mocks.trainingApi.jobArtifacts.mockResolvedValue({ items: [] })
    mocks.trainingApi.downloadJobArtifact.mockResolvedValue({
      data: new Blob(['model-binary']),
      headers: { 'content-disposition': 'attachment; filename="model.bin"' },
    })
    Object.defineProperty(URL, 'createObjectURL', {
      value: vi.fn(() => 'blob:training-model'),
      configurable: true,
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      value: vi.fn(),
      configurable: true,
    })
  })

  it('renders training as a top-level product page backed by agent resources', async () => {
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    expect(mocks.trainingApi.resources).toHaveBeenCalled()
    expect(mocks.trainingApi.jobs).toHaveBeenCalled()
    expect(wrapper.text()).toContain('训练')
    expect(wrapper.text()).toContain('训练网关')
    expect(wrapper.text()).toContain('GPU')
    expect(wrapper.text()).toContain('1 个任务')
    expect(wrapper.text()).toContain('训练任务池')
  })

  it('loads the daily department flow when today is selected', async () => {
    mocks.route.query = { window: 'day' }
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    expect(mocks.trainingApi.automationStatus).toHaveBeenCalledWith({ window_days: 1 })
    expect(wrapper.text()).toContain('部门过程数据 · 本日')
    const dayWindow = wrapper.findAll('.pipeline-period-button').find(item => item.text().includes('本日'))
    expect(dayWindow?.attributes('aria-pressed')).toBe('true')
  })

  it('loads the weekly department flow by default', async () => {
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    expect(mocks.trainingApi.automationStatus).toHaveBeenCalledWith({ window_days: 7 })
    expect(wrapper.text()).toContain('部门过程数据 · 本周')
    const weekWindow = wrapper.findAll('.pipeline-period-button').find(item => item.text().includes('本周'))
    expect(weekWindow?.attributes('aria-pressed')).toBe('true')
  })

  it('loads the monthly department flow when month is selected', async () => {
    mocks.route.query = { window: 'month' }
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    expect(mocks.trainingApi.automationStatus).toHaveBeenCalledWith({ window_days: 30 })
    expect(wrapper.text()).toContain('部门过程数据 · 本月')
    const monthWindow = wrapper.findAll('.pipeline-period-button').find(item => item.text().includes('本月'))
    expect(monthWindow?.attributes('aria-pressed')).toBe('true')
  })

  it('writes the today pipeline window to the route query', async () => {
    mocks.route.query = { stage: 'train' }
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const dayWindow = wrapper.findAll('.pipeline-period-button').find(item => item.text().includes('本日'))
    expect(dayWindow?.exists()).toBe(true)
    await dayWindow?.trigger('click')

    expect(mocks.router.replace).toHaveBeenCalledWith({
      query: { stage: 'train', window: 'day' },
    })
  })

  it('writes the monthly pipeline window to the route query', async () => {
    mocks.route.query = { stage: 'train', gateway: 'node-1' }
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const monthWindow = wrapper.findAll('.pipeline-period-button').find(item => item.text().includes('本月'))
    expect(monthWindow?.exists()).toBe(true)
    await monthWindow?.trigger('click')

    expect(mocks.router.replace).toHaveBeenCalledWith({
      query: { stage: 'train', gateway: 'node-1', window: 'month' },
    })
  })

  it('clears the pipeline window query when selecting week', async () => {
    mocks.route.query = { stage: 'train', gateway: 'node-1', window: 'day' }
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const weekWindow = wrapper.findAll('.pipeline-period-button').find(item => item.text().includes('本周'))
    expect(weekWindow?.exists()).toBe(true)
    await weekWindow?.trigger('click')

    expect(mocks.router.replace).toHaveBeenCalledWith({
      query: { stage: 'train', gateway: 'node-1' },
    })
  })

  it('renders department process data as a nine-stage training flow', async () => {
    mocks.trainingApi.automationStatus.mockResolvedValue({
      ok: true,
      policy: { approve_jobs: true, deployment_request: true, activate_deployment: true },
      factors: [],
      blockers: [],
      summary: { dataset_versions: 1, dataset_unsynced: 0 },
      nodes: {},
      department_flow: {
        window_days: 7,
        stage_order: [
          { key: 'data', label: '入口' },
          { key: 'clean', label: '样本' },
          { key: 'dataset', label: '数据集' },
          { key: 'gb10_sync', label: 'GB10 同步' },
          { key: 'route', label: '自动寻路' },
          { key: 'train', label: '训练' },
          { key: 'eval', label: '评估' },
          { key: 'deploy', label: 'Mac236 部署' },
          { key: 'run', label: '运行' },
        ],
        summary: {
          department_count: 1,
          raw_events: 12,
          artifact_total: 10,
          entry_sdk: 2,
          entry_learning: 10,
          entry_manual: 1,
          entry_tasks: 3,
          entry_datasets: 1,
          sample_text: 8,
          sample_image: 1,
          sample_image_text: 1,
          cleaned_samples: 10,
          dataset_versions: 1,
          dataset_samples: 10,
          datasets_synced: 1,
          datasets_unsynced: 0,
          jobs_total: 3,
          jobs_target_gb10: 3,
          jobs_target_fallback: 0,
          jobs_unrouted: 0,
          jobs_awaiting: 0,
          jobs_queued: 0,
          jobs_training: 1,
          jobs_evaluating: 0,
          jobs_running: 1,
          models: 2,
          eval_completed: 2,
          deployments: 1,
          deployments_active: 1,
          stage_counts: { data: 12, clean: 10, dataset: 1, gb10_sync: 1, route: 3, train: 1, eval: 2, deploy: 1, run: 1 },
        },
        departments: [{
          id: 'dept:EC',
          department: 'EC',
          current_stage: 'run',
          current_stage_label: '运行',
          coverage: 100,
          latest_at: '2026-08-09T10:00:00+08:00',
          metrics: {
            raw_events: 12,
            artifact_total: 10,
            entry_sdk: 2,
            entry_learning: 10,
            entry_manual: 1,
            entry_tasks: 3,
            entry_datasets: 1,
            sample_text: 8,
            sample_image: 1,
            sample_image_text: 1,
            cleaned_samples: 10,
            dataset_versions: 1,
            dataset_samples: 10,
            datasets_synced: 1,
            datasets_unsynced: 0,
            training_samples: 8,
            eval_samples: 2,
            jobs_total: 3,
            jobs_target_gb10: 3,
            jobs_target_fallback: 0,
            jobs_unrouted: 0,
            jobs_awaiting: 0,
            jobs_queued: 0,
            jobs_training: 1,
            jobs_evaluating: 0,
            jobs_running: 1,
            jobs_completed: 2,
            jobs_failed: 0,
            models: 2,
            eval_completed: 2,
            eval_failed: 0,
            deployments: 1,
            deployments_active: 1,
            deployments_canary: 0,
          },
          stages: [],
          blockers: [],
        }],
      },
    })
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('入口 → 样本 → 数据集 → GB10 同步 → 自动寻路 → 训练 → 评估 → Mac236 部署 → 运行')
    const entryStage = wrapper.findAll('.pipeline-stage').find(item => item.text().includes('入口'))
    expect(entryStage?.exists()).toBe(true)
    expect(wrapper.text()).toContain('2 SDK / 10 学习闭环 / 1 人工')
    expect(wrapper.text()).toContain('版本化 manifest')
    expect(wrapper.text()).toContain('0 待同步')
    expect(wrapper.text()).toContain('全部数据同步到 237')
    expect(wrapper.text()).toContain('GB10 未在线')
    expect(wrapper.text()).toContain('文字 / 图片 / 图文')
    expect(wrapper.text()).toContain('训练样本进入资产目录')
    expect(wrapper.text()).toContain('8 文字 / 1 图片 / 1 图文')
    expect(wrapper.text()).toContain('GB10 237 强优先')
    expect(wrapper.text()).toContain('3 指向 GB10')
    expect(wrapper.text()).toContain('1 任务进行中')
    expect(wrapper.text()).toContain('0 自动审核 / 1 训练中')
    expect(wrapper.text()).toContain('Mac236 部署')
    expect(wrapper.text()).toContain('内网自动部署')
    expect(wrapper.text()).toContain('2 通过')
    expect(wrapper.text()).toContain('1 active')
  })

  it('filters the task pool from the pipeline stage query', async () => {
    mocks.route.query = { stage: 'train' }
    mocks.trainingApi.jobs.mockResolvedValue({
      items: [
        {
          id: 'train-running',
          title: '训练阶段任务',
          department: 'EC',
          status: 'running',
          job_type: 'lora',
          target_gateway_id: 'node-1',
          dataset_ref: 'dataset://ec/actions/v1',
          created_at: '2026-05-21T10:00:00+08:00',
        },
        {
          id: 'train-served',
          title: '线上服务任务',
          department: 'EC',
          status: 'completed',
          job_type: 'lora',
          target_gateway_id: 'node-1',
          dataset_ref: 'dataset://ec/actions/v1',
          latest_deployment: { id: 'deploy-1', status: 'active' },
          created_at: '2026-05-21T11:00:00+08:00',
        },
      ],
    })
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('阶段 · 训练')
    expect(wrapper.text()).toContain('在线 GPU 执行 4B 三轮微调')
    expect(wrapper.text()).toContain('当前查看 · 1 任务进行中')
    expect(wrapper.text()).toContain('入口')
    expect(wrapper.text()).toContain('过程')
    expect(wrapper.text()).toContain('结果')
    expect(wrapper.text()).toContain('影响')
    expect(wrapper.text()).toContain('训练推进')
    expect(wrapper.text()).toContain('1 / 2 个任务')
    expect(wrapper.text()).toContain('训练阶段任务')
    expect(wrapper.text()).not.toContain('线上服务任务')
  })

  it('opens hall chat from the run stage when an active deployment exists', async () => {
    mocks.route.query = { stage: 'run' }
    mocks.trainingApi.jobs.mockResolvedValue({
      items: [{
        id: 'train-active-deployment',
        title: '已部署训练任务',
        department: 'EC',
        status: 'completed',
        job_type: 'lora',
        target_gateway_id: 'training-primary',
        dataset_ref: 'dataset://ec/actions/v1',
        latest_deployment: {
          id: 'deploy-active',
          status: 'active',
          deployment_target_gateway_id: 'inference-primary',
        },
        created_at: '2026-05-21T10:00:00+08:00',
      }],
    })
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const runButton = wrapper.findAll('.pipeline-detail-actions button').find(item => item.text().includes('大厅对话'))
    expect(runButton?.exists()).toBe(true)
    await runButton?.trigger('click')

    expect(mocks.router.push).toHaveBeenCalledWith('/hall/finetuned-model-chat')
    expect(mocks.trainingApi.runAutomation).not.toHaveBeenCalled()
  })

  it('writes pipeline stage selection to the route query', async () => {
    mocks.route.query = { gateway: 'node-1' }
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const evalStage = wrapper.findAll('.pipeline-stage').find(item => item.text().includes('评估'))
    expect(evalStage?.exists()).toBe(true)
    await evalStage?.trigger('click')

    expect(mocks.router.replace).toHaveBeenCalledWith({
      query: { gateway: 'node-1', stage: 'eval' },
    })
  })

  it('keeps the active pipeline stage selected when clicking it again', async () => {
    mocks.route.query = { stage: 'eval' }
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const evalStage = wrapper.findAll('.pipeline-stage').find(item => item.text().includes('评估'))
    expect(evalStage?.attributes('aria-label')).toContain('查看评估阶段明细')
    await evalStage?.trigger('click')

    expect(mocks.router.replace).toHaveBeenCalledWith({
      query: { stage: 'eval' },
    })
  })

  it('shows auto routed platform training gateways in the task pool and create modal', async () => {
    mocks.trainingApi.resources.mockResolvedValue({
      context: { department: 'EC', can_view_all: false },
      items: [{
        id: 'platform-training-1',
        name: '平台训练 Agent',
        department: 'AI',
        online: true,
        is_platform_default: true,
        agent_purpose: 'training',
        route_scope: 'platform_fallback',
        training: { gateway: true, supported_tasks: ['lora'] },
        gpu_count: 2,
        idle_gpu_count: 2,
        busy_gpu_count: 0,
        vram_total_gb: 48,
        vram_free_gb: 40,
        active_training_jobs_count: 0,
        active_training_jobs: [],
      }],
    })
    mocks.trainingApi.jobs.mockResolvedValue({
      items: [{
        id: 'train-platform',
        title: '平台训练任务',
        department: 'EC',
        status: 'queued',
        job_type: 'lora',
        target_gateway_id: 'platform-training-1',
        gateway_routing: { mode: 'auto', scope: 'platform_fallback', status: 'assigned' },
        created_at: '2026-05-21T10:00:00+08:00',
      }],
    })
    mocks.trainingApi.createJob.mockResolvedValue({
      id: 'train-platform-new',
      title: '新平台训练任务',
      department: 'AI',
      status: 'awaiting_review',
      job_type: 'lora',
      target_gateway_id: 'platform-training-1',
      gateway_routing: { mode: 'auto', scope: 'platform_fallback', status: 'assigned' },
      created_at: '2026-05-21T11:00:00+08:00',
    })
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('自动·平台')
    const createButton = wrapper.findAll('button').find(item => item.text().includes('新建'))
    await createButton?.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('平台训练 Agent')
    expect(wrapper.text()).toContain('任务归属 EC')
    await wrapper.find('input').setValue('新平台训练任务')
    await wrapper.find('.modal-ok').trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.createJob).toHaveBeenCalledWith(expect.objectContaining({
      department: 'EC',
      target_gateway_id: 'platform-training-1',
    }))
  })

  it('highlights the gateway carried from Agent detail and uses it for new jobs', async () => {
    mocks.route.query = { gateway: 'node-2' }
    mocks.trainingApi.resources.mockResolvedValue({
      items: [
        {
          id: 'node-1',
          name: '训练网关 A',
          department: 'EC',
          online: true,
          training: { gateway: true, supported_tasks: ['lora'] },
          gpu_count: 1,
          idle_gpu_count: 1,
          busy_gpu_count: 0,
          vram_total_gb: 8,
          vram_free_gb: 6,
          active_training_jobs_count: 0,
          active_training_jobs: [],
        },
        {
          id: 'node-2',
          name: '训练网关 B',
          department: 'AI',
          online: true,
          training: { gateway: true, supported_tasks: ['qlora'] },
          gpu_count: 2,
          idle_gpu_count: 2,
          busy_gpu_count: 0,
          vram_total_gb: 48,
          vram_free_gb: 44,
          active_training_jobs_count: 0,
          active_training_jobs: [],
        },
      ],
    })
    mocks.trainingApi.jobs.mockResolvedValue({
      items: [{
        id: 'train-node-2',
        title: '训练指定网关',
        department: 'AI',
        status: 'awaiting_review',
        job_type: 'lora',
        target_gateway_id: 'node-2',
        dataset_ref: 'dataset://ai/actions/v1',
        created_at: '2026-05-21T11:00:00+08:00',
      }],
    })
    mocks.trainingApi.createJob.mockResolvedValue({
      id: 'train-node-2',
      title: '训练指定网关',
      department: 'AI',
      status: 'awaiting_review',
      job_type: 'lora',
      target_gateway_id: 'node-2',
      created_at: '2026-05-21T11:00:00+08:00',
    })
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('已选')
    expect(wrapper.text()).toContain('网关上下文')
    expect(wrapper.text()).toContain('训练任务池 · 当前网关')
    expect(mocks.trainingApi.jobs).toHaveBeenCalledWith({ target_gateway_id: 'node-2' })
    const createButton = wrapper.findAll('button').find(item => item.text().includes('新建'))
    await createButton?.trigger('click')
    await flushPromises()

    await wrapper.find('input').setValue('训练指定网关')
    await wrapper.find('.modal-ok').trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.createJob).toHaveBeenCalledWith(expect.objectContaining({
      department: 'AI',
      target_gateway_id: 'node-2',
    }))
  })

  it('limits batch collection to the selected gateway context', async () => {
    mocks.route.query = { gateway: 'node-1' }
    mocks.trainingApi.collectDue.mockResolvedValue({ stats: { collected: 1, skipped: 0, failed: 0 } })
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const button = wrapper.findAll('button').find(item => item.text().includes('批量同步'))
    expect(button?.exists()).toBe(true)
    await button?.trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.collectDue).toHaveBeenCalledWith({
      stale_seconds: 0,
      max_jobs: 20,
      target_gateway_id: 'node-1',
    })
  })

  it('batch collects active training jobs through the control plane', async () => {
    mocks.trainingApi.collectDue.mockResolvedValue({ stats: { collected: 1, skipped: 0, failed: 0 } })
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const button = wrapper.findAll('button').find(item => item.text().includes('批量同步'))
    expect(button?.exists()).toBe(true)
    await button?.trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.collectDue).toHaveBeenCalledWith({ stale_seconds: 0, max_jobs: 20 })
    expect(mocks.trainingApi.jobs).toHaveBeenCalledTimes(2)
  })

  it('runs full training automation from the page header', async () => {
    mocks.trainingApi.runAutomation.mockResolvedValue({
      ok: true,
      stats: { jobs_seen: 1, jobs_changed: 1, datasets_seen: 0, datasets_synced: 0, failed: 0 },
    })
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const button = wrapper.findAll('button').find(item => item.text().includes('全自动推进'))
    expect(button?.exists()).toBe(true)
    await button?.trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.runAutomation).toHaveBeenCalledWith({
      trigger: 'web',
      materialize_learning: true,
      materialize_limit: 50000,
      promote_learning_samples: true,
      create_datasets: true,
      max_dataset_samples: 5000,
      datasets: true,
      jobs: true,
      dispatch: true,
      evaluate: true,
      max_datasets: 20,
      max_jobs: 50,
    })
    expect(mocks.trainingApi.runFullHistoryFinetune).toHaveBeenCalledWith({
      trigger: 'web_auto',
      cycles: 3,
      capture_sources: true,
      blocking_source_sync: false,
      days: 3650,
      per_source_limit: 2000,
      materialize_limit: 50000,
      min_sample_count: 4,
      background_dispatch: true,
    })
    expect(mocks.trainingApi.jobs).toHaveBeenCalledTimes(2)
  })

  it('runs automation from the entry stage when process data exists', async () => {
    mocks.route.query = { stage: 'data' }
    mocks.trainingApi.runAutomation.mockResolvedValue({
      ok: true,
      stats: { jobs_seen: 0, jobs_changed: 0, datasets_seen: 1, datasets_synced: 1, failed: 0 },
    })
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const button = wrapper.findAll('.pipeline-detail-actions button').find(item => item.text().includes('入口推进'))
    expect(button?.exists()).toBe(true)
    await button?.trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.runAutomation).toHaveBeenCalledWith({
      trigger: 'web',
      materialize_learning: true,
      materialize_limit: 50000,
      promote_learning_samples: true,
      create_datasets: true,
      max_dataset_samples: 5000,
      datasets: true,
      jobs: true,
      dispatch: true,
      evaluate: true,
      max_datasets: 20,
      max_jobs: 50,
    })
    expect(mocks.router.push).not.toHaveBeenCalledWith('/training/datasets')
  })

  it('runs automation from the sample stage', async () => {
    mocks.route.query = { stage: 'samples' }
    mocks.trainingApi.runAutomation.mockResolvedValue({
      ok: true,
      stats: {
        learning_materialized: 0,
        learning_samples_promoted: 1,
        datasets_created: 1,
        jobs_seen: 0,
        jobs_changed: 0,
        datasets_seen: 0,
        datasets_synced: 0,
        failed: 0,
      },
    })
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const button = wrapper.findAll('.pipeline-detail-actions button').find(item => item.text().includes('样本同步'))
    expect(button?.exists()).toBe(true)
    await button?.trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.runAutomation).toHaveBeenCalledWith({
      trigger: 'web',
      materialize_learning: true,
      materialize_limit: 50000,
      promote_learning_samples: true,
      create_datasets: true,
      max_dataset_samples: 5000,
      datasets: true,
      jobs: true,
      dispatch: true,
      evaluate: true,
      max_datasets: 20,
      max_jobs: 50,
    })
    expect(mocks.router.push).not.toHaveBeenCalledWith('/training/datasets')
  })

  it('prefills new training as a full-history 4B automatic job', async () => {
    mocks.trainingApi.resources.mockResolvedValue({
      context: { department: '', can_view_all: true },
      items: [
        {
          id: 'training-primary',
          name: 'GB10 237',
          department: '',
          online: true,
          is_platform_default: true,
          agent_purpose: 'mixed',
          route_scope: 'platform_fallback',
          training: { gateway: true, supported_tasks: ['lora', 'qlora', 'eval', 'merge', 'inference'] },
          gpu_count: 1,
          idle_gpu_count: 1,
          busy_gpu_count: 0,
          vram_total_gb: 512,
          vram_free_gb: 400,
          active_training_jobs_count: 0,
          active_training_jobs: [],
        },
        {
          id: 'inference-primary',
          name: 'Mac236',
          department: '',
          online: true,
          is_platform_default: true,
          agent_purpose: 'mixed',
          route_scope: 'platform_fallback',
          training: { gateway: true, supported_tasks: ['eval'] },
          gpu_count: 1,
          idle_gpu_count: 1,
          busy_gpu_count: 0,
          vram_total_gb: 128,
          vram_free_gb: 120,
          active_training_jobs_count: 0,
          active_training_jobs: [],
        },
      ],
    })
    mocks.trainingApi.createJob.mockResolvedValue({
      id: 'train-full-history-created',
      title: 'SkillForge 全量数据 4B 微调',
      department: 'AI平台',
      status: 'awaiting_review',
      job_type: 'lora',
      target_gateway_id: 'training-primary',
      target_skill_id: 'skillforge-finetuned-model-chat',
      dataset_ref: 'learning-artifacts://platform/training/full-history/latest',
      created_at: '2026-08-09T20:00:00+08:00',
    })
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const createButton = wrapper.findAll('button').find(item => item.text().includes('新建'))
    expect(createButton?.exists()).toBe(true)
    await createButton?.trigger('click')
    await flushPromises()

    await wrapper.find('.modal-ok').trigger('click')
    await flushPromises()

    const payload = mocks.trainingApi.createJob.mock.calls[0]?.[0]
    expect(payload).toEqual(expect.objectContaining({
      title: 'SkillForge 全量数据 4B 微调',
      department: 'AI平台',
      job_type: 'lora',
      training_strategy: 'learning_sample_threshold',
      target_gateway_id: 'training-primary',
      target_skill_id: 'skillforge-finetuned-model-chat',
      dataset_ref: 'learning-artifacts://platform/training/full-history/latest',
    }))
    expect(payload.spec).toEqual(expect.objectContaining({
      model_profile: 'qwen3.5-4b',
      training_mode: 'full_history_incremental',
      automation: expect.objectContaining({
        full_auto: true,
        auto_approve_job: true,
        training_gateway_id: 'training-primary',
        deployment_gateway_id: 'inference-primary',
      }),
      deployment: expect.objectContaining({
        deployment_target_gateway_id: 'inference-primary',
        deployment_runtime_profile: 'mlx-qwen3.5-4b-lora',
        auto_active: true,
      }),
    }))
  })

  it('simulates a training output and renders verified changes', async () => {
    mocks.trainingApi.simulateJobResult.mockResolvedValue({
      ok: true,
      job_id: 'train-1',
      simulated_request: {
        gateway_id: 'training-primary',
        status: 'completed',
        artifacts: [{
          uri: 'artifact://training-sim/train-1/model.safetensors',
          sha256: 'a'.repeat(64),
        }],
      },
      before: { status: 'queued', artifact_uri: null },
      after: {
        status: 'completed',
        evaluation_status: 'completed',
        deployment_status: 'active',
        artifact_uri: 'artifact://training-sim/train-1/model.safetensors',
      },
      changed_fields: [
        { field: 'status', before: 'queued', after: 'completed' },
        { field: 'artifact_uri', before: null, after: 'artifact://training-sim/train-1/model.safetensors' },
      ],
      job: {
        id: 'train-1',
        title: '训练商品下滑动作推荐模型',
        department: 'EC',
        status: 'completed',
        job_type: 'lora',
        target_gateway_id: 'training-primary',
        dataset_ref: 'dataset://ec/actions/v1',
        latest_deployment: { id: 'deploy-1', status: 'active' },
        training_plan: {
          artifact: {
            id: '1:0',
            name: 'model.safetensors',
            uri: 'artifact://training-sim/train-1/model.safetensors',
            sha256: 'a'.repeat(64),
          },
        },
        created_at: '2026-05-21T10:00:00+08:00',
      },
    })
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const button = wrapper.findAll('button').find(item => item.text().includes('模拟输出并验证'))
    expect(button?.exists()).toBe(true)
    await button?.trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.simulateJobResult).toHaveBeenCalledWith('train-1', expect.objectContaining({
      auto_advance_first: true,
      status: 'completed',
      progress: 1,
    }))
    expect(wrapper.text()).toContain('模拟请求已验证')
    expect(wrapper.text()).toContain('training-primary')
    expect(wrapper.text()).toContain('2 项变化')
  })

  it('creates a reviewed training job candidate from the training page', async () => {
    mocks.trainingApi.createJob.mockResolvedValue({
      id: 'train-created',
      title: '训练新模型',
      department: 'EC',
      status: 'awaiting_review',
      job_type: 'lora',
      target_gateway_id: 'node-1',
      created_at: '2026-05-21T11:00:00+08:00',
    })
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const createButton = wrapper.findAll('button').find(item => item.text().includes('新建'))
    expect(createButton?.exists()).toBe(true)
    await createButton?.trigger('click')
    await flushPromises()

    await wrapper.find('input').setValue('训练新模型')
    await wrapper.find('.modal-ok').trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.createJob).toHaveBeenCalledWith(expect.objectContaining({
      title: '训练新模型',
      department: 'EC',
      job_type: 'lora',
      target_gateway_id: 'node-1',
    }))
  })

  it('downloads model artifacts directly from the training task pool', async () => {
    mocks.trainingApi.jobs.mockResolvedValue({
      items: [{
        id: 'train-completed',
        title: '已完成训练模型',
        department: 'EC',
        status: 'completed',
        job_type: 'lora',
        target_gateway_id: 'node-1',
        dataset_ref: 'dataset://ec/actions/v1',
        created_at: '2026-05-21T10:00:00+08:00',
      }],
    })
    mocks.trainingApi.jobArtifacts.mockResolvedValue({
      items: [{
        id: '1:0',
        type: 'adapter',
        name: 'model.bin',
        uri: 'artifact://train/model.bin',
        sha256: 'a'.repeat(64),
        size_bytes: 1024,
        downloadable: true,
        download_url: '/api/training/jobs/train-completed/artifacts/1%3A0/download',
      }],
    })
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const artifactButton = wrapper.findAll('.training-job-actions button').find(item => item.text().includes('产物'))
    expect(artifactButton?.exists()).toBe(true)
    await artifactButton?.trigger('click')
    await flushPromises()

    const downloadButton = wrapper.findAll('button').find(item => item.text().includes('下载模型'))
    expect(downloadButton?.exists()).toBe(true)
    await downloadButton?.trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.jobArtifacts).toHaveBeenCalledWith('train-completed')
    expect(mocks.trainingApi.downloadJobArtifact).toHaveBeenCalledWith('train-completed', '1:0')
    expect(clickSpy).toHaveBeenCalled()
    clickSpy.mockRestore()
  })

  it('approves awaiting training jobs before dispatch', async () => {
    mocks.trainingApi.jobs.mockResolvedValue({
      items: [{
        id: 'train-review',
        title: '训练待审模型',
        department: 'EC',
        status: 'awaiting_review',
        job_type: 'lora',
        target_gateway_id: 'node-1',
        created_at: '2026-05-21T10:00:00+08:00',
      }],
    })
    mocks.trainingApi.approveJob.mockResolvedValue({
      id: 'train-review',
      title: '训练待审模型',
      department: 'EC',
      status: 'queued',
      job_type: 'lora',
      target_gateway_id: 'node-1',
      created_at: '2026-05-21T10:00:00+08:00',
    })
    const wrapper = mount(TrainingHome, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const approveButton = wrapper.findAll('button').find(item => item.text().includes('审批'))
    expect(approveButton?.exists()).toBe(true)
    await approveButton?.trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.approveJob).toHaveBeenCalledWith('train-review')
  })
})
