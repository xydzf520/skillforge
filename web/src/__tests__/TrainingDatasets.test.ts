import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  trainingApi: {
    datasets: vi.fn(),
    assetSources: vi.fn(),
    datasetVersions: vi.fn(),
    automationStatus: vi.fn(),
    syncDatasetVersion: vi.fn(),
    createSkillCandidate: vi.fn(),
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
    error: vi.fn(),
  },
}))

vi.mock('@arco-design/web-vue/es/icon', () => ({
  IconLeft: { template: '<span />' },
  IconRefresh: { template: '<span />' },
  IconSend: { template: '<span />' },
}))

import TrainingDatasets from '@/pages/training/TrainingDatasets.vue'

function datasetPayload(overrides: Record<string, unknown> = {}) {
  return {
    skill_id: 'skill-recommend',
    skill_name: '商品推荐',
    department: 'EC',
    status: 'active',
    dataset_ref: 'decision-log://skill-recommend/action-outcome/latest',
    passed: true,
    can_create_candidate: true,
    sample_counts: {
      sft_samples: 120,
      preference_samples: 60,
      action_outcome_samples: 120,
      eval_samples: 60,
      recent_total: 120,
      recent_failed: 10,
    },
    checks: [
      { key: 'sft_samples', label: 'SFT 样本', actual: 120, threshold: 100, passed: true },
      { key: 'recent_failure_rate', label: '最近 7 天失败率', actual: 0.0833, threshold: 0.3, passed: true },
    ],
    recent_failure_rate: 0.0833,
    last_log_at: '2026-05-21T11:00:00+08:00',
    ...overrides,
  }
}

function stubs() {
  return {
    SfPageHeader: { props: ['title', 'subtitle'], template: '<header><h1>{{ title }}</h1><p>{{ subtitle }}</p><slot name="actions" /></header>' },
    SfKpiCard: { props: ['label', 'value', 'hint'], template: '<section><b>{{ label }}</b><span>{{ value }}</span><small>{{ hint }}</small></section>' },
    SfEmptyState: { props: ['title', 'description', 'hint'], template: '<div>{{ title }}{{ description }}{{ hint }}</div>' },
    'a-button': {
      props: ['loading', 'disabled', 'type', 'status'],
      emits: ['click'],
      template: '<button :disabled="loading || disabled" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>',
    },
    'a-card': { props: ['title'], template: '<section><h2>{{ title }}</h2><slot /></section>' },
    'a-col': { template: '<div><slot /></div>' },
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
      template: '<th>{{ title }}<div v-for="record in tableData" :key="record.skill_id"><slot name="cell" :record="record" /></div></th>',
    },
    'a-tag': { template: '<span><slot /></span>' },
  }
}

describe('TrainingDatasets', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.trainingApi.datasets.mockResolvedValue({
      items: [datasetPayload()],
      stats: { ready: 1, action_outcome_samples: 120, can_create_candidate: 1 },
    })
    mocks.trainingApi.assetSources.mockResolvedValue({
      items: [{
        id: 'tas_1',
        source_type: 'project_run_asset',
        source_id: 'asset-1',
        title: '训练图文资产',
        department: 'EC',
        modality: 'image_text',
        storage_backend: 'project_assets',
        storage_ref: 'project://asset-1',
        sample_count: 1,
        sha256: 'a'.repeat(64),
        created_at: '2026-05-21T10:00:00+08:00',
        updated_at: '2026-05-21T10:00:00+08:00',
      }],
    })
    mocks.trainingApi.datasetVersions.mockResolvedValue({
      items: [{
        id: 'tdv_1',
        name: 'sdk-vision',
        version: '2026073001',
        dataset_profile: 'vision_instruction_v1',
        modality: 'image_text',
        department: 'EC',
        sample_count: 1,
        media_count: 1,
        manifest_sha256: 'b'.repeat(64),
        manifest_uri: 'skillforge://training-datasets/tdv_1/manifest.jsonl',
        target_gateway_id: 'training-primary',
        sync_status: 'pending',
        automation_status: {
          status: 'running',
          steps: [
            { key: 'entry', label: '入口', status: 'completed', detail: 'sdk-vision' },
            { key: 'dataset_version', label: '数据集版本', status: 'completed', detail: '2026073001' },
            { key: 'gb10_sync', label: 'GB10 同步', status: 'pending', detail: 'pending' },
          ],
        },
        created_at: '2026-05-21T10:00:00+08:00',
        updated_at: '2026-05-21T10:00:00+08:00',
      }],
    })
    mocks.trainingApi.automationStatus.mockResolvedValue({
      department_flow: {
        window_days: 7,
        summary: {
          department_count: 1,
          raw_events: 12,
          artifact_total: 100,
          cleaned_samples: 100,
          training_samples: 96,
          eval_samples: 4,
          jobs_total: 3,
          jobs_running: 1,
          models: 2,
          eval_completed: 2,
          deployments_active: 1,
          stage_counts: { data: 100, clean: 100, train: 3, model: 2, eval: 2, run: 1 },
        },
        departments: [{
          id: 'dept:EC',
          department: 'EC',
          current_stage: 'run',
          current_stage_label: '运行',
          coverage: 100,
          latest_at: '2026-05-21T10:00:00+08:00',
          metrics: {
            raw_events: 12,
            artifact_total: 100,
            cleaned_samples: 100,
            training_samples: 96,
            eval_samples: 4,
            jobs_running: 1,
            jobs_completed: 2,
            jobs_failed: 0,
            models: 2,
            eval_completed: 2,
            eval_failed: 0,
            deployments_active: 1,
            deployments_canary: 0,
          },
          stages: [],
          blockers: [],
        }],
      },
    })
    mocks.trainingApi.syncDatasetVersion.mockResolvedValue({ ok: true })
  })

  it('renders data asset readiness without raw decision samples', async () => {
    const wrapper = mount(TrainingDatasets, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    expect(mocks.trainingApi.datasets).toHaveBeenCalled()
    expect(mocks.trainingApi.automationStatus).toHaveBeenCalled()
    expect(wrapper.text()).toContain('数据资产')
    expect(wrapper.text()).toContain('部门历史样本资产')
    expect(wrapper.text()).toContain('100')
    expect(wrapper.text()).toContain('1 / 2')
    expect(wrapper.text()).toContain('1 active')
    expect(wrapper.text()).toContain('商品推荐')
    expect(wrapper.text()).toContain('已达标')
    expect(wrapper.text()).toContain('动作 120')
    expect(wrapper.text()).toContain('decision-log://skill-recommend/action-outcome/latest')
  })

  it('creates a reviewed training candidate from a ready dataset row', async () => {
    mocks.trainingApi.createSkillCandidate.mockResolvedValue({ id: 'train-created' })
    const wrapper = mount(TrainingDatasets, {
      global: { stubs: stubs() },
    })
    await flushPromises()

    const button = wrapper.findAll('button').find(item => item.text().includes('生成候选'))
    expect(button?.exists()).toBe(true)
    await button?.trigger('click')
    await flushPromises()

    expect(mocks.trainingApi.createSkillCandidate).toHaveBeenCalledWith('skill-recommend', {
      dataset_ref: 'decision-log://skill-recommend/action-outcome/latest',
    })
    expect(mocks.router.push).toHaveBeenCalledWith('/training/jobs/train-created')
  })
})
