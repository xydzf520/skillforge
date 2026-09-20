import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  aiclawApi: {
    listInstances: vi.fn(),
    listAgents: vi.fn(),
  },
  learningApi: {
    home: vi.fn(),
    summary: vi.fn(),
    pulse: vi.fn(),
    events: vi.fn(),
    artifacts: vi.fn(),
    candidates: vi.fn(),
    flowGraph: vi.fn(),
    flowTopology: vi.fn(),
    flowJourneys: vi.fn(),
    bottlenecks: vi.fn(),
    trainingManifest: vi.fn(),
    automationStatus: vi.fn(),
    runAutomation: vi.fn(),
    backfill: vi.fn(),
    materializeArtifact: vi.fn(),
    ignoreArtifact: vi.fn(),
    acceptCandidate: vi.fn(),
    rejectCandidate: vi.fn(),
    createCandidateReview: vi.fn(),
    createCandidateTrainingJob: vi.fn(),
    lineage: vi.fn(),
  },
  message: {
    success: vi.fn(),
    info: vi.fn(),
  },
  routerPush: vi.fn(),
  routerReplace: vi.fn(),
  routeQuery: {} as Record<string, string>,
}))

vi.mock('@/api', () => ({
  aiclawApi: mocks.aiclawApi,
  learningApi: mocks.learningApi,
}))

vi.mock('@/api/aiclawWs', () => ({
  createAiclawChatSocket: vi.fn(),
}))

vi.mock('@arco-design/web-vue', () => ({
  Message: mocks.message,
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mocks.routerPush, replace: mocks.routerReplace }),
  useRoute: () => ({ path: '/learning-flow', query: mocks.routeQuery }),
}))

import LearningFlow from '@/pages/learning/LearningFlow.vue'

function stubs() {
  return {
    SfShellIcon: { props: ['name'], template: '<i class="icon">{{ name }}</i>' },
    'a-select': {
      props: ['modelValue'],
      emits: ['update:modelValue', 'change'],
      template: '<select @change="$emit(\'change\', $event.target.value)"><slot /></select>',
    },
    'a-option': { props: ['value'], template: '<option :value="value"><slot /></option>' },
    'a-input': { props: ['modelValue'], template: '<input :value="modelValue" />' },
    'a-input-search': { props: ['modelValue'], emits: ['search'], template: '<input :value="modelValue" @keyup.enter="$emit(\'search\')" />' },
    'a-empty': { props: ['description'], template: '<div class="empty">{{ description }}</div>' },
    'a-drawer': {
      props: ['visible', 'title'],
      emits: ['cancel', 'ok'],
      template: '<aside v-if="visible" class="drawer"><h2>{{ title }}</h2><slot /></aside>',
    },
    'a-modal': {
      props: ['visible', 'title', 'modalClass'],
      emits: ['update:visible'],
      template: '<section v-if="visible" :class="[modalClass, \'modal\']"><h2>{{ title }}</h2><slot /></section>',
    },
  }
}

function mockLearningResponses() {
  mocks.learningApi.pulse.mockResolvedValue({
    modules: [
      {
        key: 'raw_inputs',
        title: '原始数据',
        subtitle: '原始数据进来哪些、来源哪里',
        status: 'ready',
        status_text: '18 条原始记录',
        tone: 'source',
        metrics: [{ key: 'events', label: '原始记录', value: 18 }],
        items: [{ id: 'e1', title: 'Skill 运行完成', meta: 'Skill 运行 · run-learning-1', entity_type: 'learning_event', entity_id: 'e1', payload: { id: 'e1', source_type: 'execution_run', source_id: 'run-learning-1' } }],
        flow_detail: {
          title: '原始数据进入明细',
          summary: '展示本周期进入学习流的脱敏原始记录。',
          sections: [
            {
              key: 'new_income',
              title: '新增进入记录',
              description: '已进入数据清洗。',
              items: [
                {
                  title: 'Skill 运行完成',
                  summary: '原始运行数据进入学习流。',
                  status: 'captured',
                  status_text: '已捕获',
                  source: 'Skill 运行 · run-learning-1',
                  destination: '数据清洗',
                  entity_type: 'learning_event',
                  entity_id: 'e1',
                  input_preview: { source_id: 'run-learning-1' },
                  output_preview: { event_id: 'e1' },
                  reason: '通过来源哈希与权限范围校验后进入学习流。',
                },
              ],
            },
          ],
        },
      },
      {
        key: 'clarified_data',
        title: '数据清洗',
        subtitle: '清洗后同步入数据库和向量库，形成可复用资产',
        status: 'ready',
        status_text: '9 条清洗资产',
        tone: 'artifact',
        metrics: [
          { key: 'artifacts', label: '清洗资产', value: 9 },
          { key: 'database_synced', label: '数据库存储', value: 8 },
          { key: 'vector_chunks', label: '向量分片', value: 12 },
          { key: 'database_version', label: '数据库版本', value: '06-22 10:30' },
        ],
        items: [{ id: 'artifact-group-1', title: '链接下滑结果复核（同类 3 条）', meta: '报告摘要 · 已物化 · 已合并重复明细', entity_type: 'learning_artifact', entity_id: 'a1', payload: { id: 'a1', artifact_kind: 'report_summary', group_count: 3 } }],
        detail: {
          storage: {
            database_synced_count: 8,
            vector_chunk_count: 12,
            database_version: '06-22 10:30',
            database_version_at: '2026-06-22T10:30:00+08:00',
            max_index_version: 4,
          },
        },
        flow_detail: {
          title: '数据清洗处理明细',
          summary: '展示保留、丢弃和同步存储。',
          storage: {
            database_version: '06-22 10:30',
            vector_chunk_count: 12,
          },
          sections: [
            {
              key: 'kept',
              title: '保留并进入下游的数据',
              description: '满足质量、置信度或物化条件。',
              items: [
                {
                  title: '链接下滑结果复核（同类 3 条）',
                  summary: '搜索访客下降。',
                  status: 'materialized',
                  status_text: '已物化',
                  source: 'learning_event:e1',
                  destination: '知识库 / 向量库 · doc1',
                  reason: '质量分 0.9；已同步到知识库 / 向量库',
                },
              ],
            },
            {
              key: 'dropped',
              title: '丢弃或不进入下游的数据',
              description: '未进入后续链路。',
              empty_text: '当前筛选范围内没有丢弃记录。',
              items: [],
            },
          ],
        },
      },
      {
        key: 'finetuning',
        title: '微调模型',
        subtitle: '微调模型进行到哪、预计还要多久',
        status: 'running',
        status_text: '运行中',
        tone: 'warn',
        metrics: [{ key: 'eta', label: '预计时间', value: '约 8 分钟' }],
        items: [{ id: 'tj1', title: '训练闭环任务', meta: 'lora · 运行中', entity_type: 'training_job', entity_id: 'tj1', payload: { id: 'tj1' } }],
        flow_detail: {
          title: '微调训练流转明细',
          summary: '展示训练任务是否进入训练 Agent。',
          eta: { text: '约 8 分钟' },
          sections: [
            {
              key: 'training_jobs',
              title: '进入训练的任务',
              description: '每条任务都关联训练样本集。',
              items: [
                {
                  title: '训练闭环任务',
                  status: 'running',
                  status_text: '运行中',
                  source: 'learning-artifacts://visible/training/latest',
                  destination: 'gw-learning',
                  reason: '已进入训练 Agent 执行链路。',
                  input_preview: { dataset_ref: 'learning-artifacts://visible/training/latest' },
                  output_preview: { progress: 0.5 },
                },
              ],
            },
          ],
        },
      },
      {
        key: 'test_results',
        title: '模型测试',
        subtitle: '评估是否通过、产物是否可用',
        status: 'passed',
        status_text: '已通过',
        tone: 'good',
        metrics: [{ key: 'passed', label: '通过', value: 1 }],
        items: [{ id: 'task-1', title: '评估任务 1', meta: '已完成', entity_type: 'training_job', entity_id: 'tj1', payload: { task_id: 1 } }],
        flow_detail: {
          title: '模型测试明细',
          summary: '展示测试任务、执行 Agent、输入产物、评估指标和输出结果。',
          sections: [
            {
              key: 'eval_tasks',
              title: '测试任务与 Agent',
              description: '测试任务来自训练 Agent 回传。',
              items: [
                {
                  title: '评估任务 1',
                  status: 'passed',
                  status_text: '已通过',
                  source: 'training_job:tj1',
                  destination: 'artifact://model',
                  input_preview: { worker_id: 'worker-1' },
                  output_preview: { passed: true, win_rate: 1 },
                },
              ],
            },
          ],
        },
      },
      {
        key: 'application_outputs',
        title: '部署输出',
        subtitle: '应用在哪里输出、结果怎么样',
        status: 'applied',
        status_text: '已应用',
        tone: 'good',
        metrics: [{ key: 'used_as_model', label: '参与决策', value: 2 }],
        items: [{ id: 'edge-1', title: '模型参与决策', meta: 'model_deployment:deploy-1 → run:run-learning-1', entity_type: 'run', entity_id: 'run-learning-1', payload: { relation: 'used_as_model' } }],
        flow_detail: {
          title: '部署与应用输出明细',
          summary: '展示模型部署位置、目标 Skill、实际参与链路和输入输出。',
          sections: [
            {
              key: 'deployments',
              title: '部署位置',
              description: '展示模型产物部署到哪里。',
              items: [
                {
                  title: 'learn-skill:lora',
                  status: 'active',
                  status_text: '已激活',
                  source: 'training_job:tj1',
                  destination: 'learn-skill',
                  input_preview: { rollout_percent: 100, model_deployment_id: 'deploy-1', model_family: 'learn-skill:lora', training_job_id: 'tj1', department: 'EC', artifact_id: 'artifact-1' },
                  output_preview: { deployment_id: 'deploy-1', model_deployment_id: 'deploy-1', model_family: 'learn-skill:lora', training_job_id: 'tj1', department: 'EC', artifact_id: 'artifact-1', status: 'active' },
                  metadata: { chat_context: { ready: true, model_deployment_id: 'deploy-1', model_family: 'learn-skill:lora', training_job_id: 'tj1', department: 'EC', artifact_id: 'artifact-1', deployment_status: 'active' } },
                },
              ],
            },
            {
              key: 'conversation_examples',
              title: '模型对话输入输出',
              description: '展示可用的推理摘要。',
              items: [
                {
                  title: '模型参与决策',
                  status: 'active',
                  status_text: '已激活',
                  source: 'model_deployment:deploy-1',
                  destination: 'run:run-learning-1',
                  input_preview: { prompt: '分析库存风险' },
                  output_preview: { result: '建议补货' },
                },
              ],
            },
          ],
        },
      },
    ],
    generated_at: '2026-06-01T15:09:00',
  })
  mocks.aiclawApi.listInstances.mockResolvedValue({ items: [] })
  mocks.aiclawApi.listAgents.mockResolvedValue({ items: [] })
  const summaryResponse = {
    scope: 'global',
    events: { total: 18 },
    artifacts: { by_kind: { report_summary: 4, training_sample: 3, agent_memory: 2 }, by_status: { ready: 3, materialized: 6 } },
    knowledge: { indexed: 4 },
    training: { samples: 3 },
    agent: { used_context_edges: 5, memories: 2 },
    candidates: { by_target: { skill: 2, sf: 1 }, by_status: { open: 2, reviewing: 1 } },
  }
  mocks.learningApi.summary.mockResolvedValue(summaryResponse)
  const topologyResponse = {
    stages: [
      { key: 'source', title: '数据源', caption: '运行 / SF / Agent / 训练', count: 18, tone: 'source', nodes: [{ id: 'source:execution_run', label: 'Skill 运行', meta: '8 条', icon: 'play', heat: 0.7, tone: 'source', entity_type: 'source_type', entity_id: 'execution_run', payload: { count: 8 } }] },
      { key: 'event', title: '标准记录', caption: '统一脱敏、去重、打分', count: 18, tone: 'event', nodes: [{ id: 'event:e1', label: 'decision.completed', meta: '决策反馈', icon: 'spark', heat: 0.8, tone: 'event', entity_type: 'learning_event', entity_id: 'e1', payload: { id: 'e1', source_type: 'execution_run', source_id: 'run-learning-1' } }] },
      { key: 'artifact', title: '学习资产', caption: '知识 / 样本 / 记忆 / 建议', count: 9, tone: 'artifact', nodes: [{ id: 'artifact:a1', label: '链接下滑结果复核', meta: '报告摘要 · 已生成', icon: 'doc', heat: 0.9, tone: 'artifact', payload: { id: 'a1' } }] },
      { key: 'sink', title: '自动生成知识', caption: '知识库 / 训练池 / Agent 记忆', count: 9, tone: 'sink', nodes: [{ id: 'sink:knowledge_document:a1', label: '已生成', meta: 'knowledge_document · doc1', icon: 'database', heat: 0.8, tone: 'sink', payload: { artifact_id: 'a1' } }] },
      { key: 'candidate', title: '迭代建议', caption: 'Skill / SF / Agent 自动建议', count: 3, tone: 'candidate', nodes: [{ id: 'candidate:c1', label: '优化关键词诊断 Skill', meta: 'skill · 开放 · R2', icon: 'edit', heat: 0.75, tone: 'candidate', payload: { id: 'c1' } }] },
      { key: 'review', title: '审核流程', caption: '待办评审 / 训练 / 发布控制', count: 1, tone: 'review', nodes: [{ id: 'review:c1', label: '评审中', meta: '优化关键词诊断 Skill · ldr1', icon: 'inbox', heat: 0.6, tone: 'review', payload: { id: 'c1' } }] },
    ],
    connectors: [
      { from: 'source', to: 'event', count: 18, label: '同步', relation: 'captured_as_event', intensity: 0.9, animated: true },
      { from: 'event', to: 'artifact', count: 9, label: '提取', relation: 'produced', intensity: 0.45, animated: true },
      { from: 'artifact', to: 'sink', count: 9, label: '生成知识', relation: 'materialized_to_sink', intensity: 0.45, animated: true },
      { from: 'sink', to: 'candidate', count: 3, label: '改进建议', relation: 'fed_back_to_candidate', intensity: 0.25, animated: true },
      { from: 'candidate', to: 'review', count: 1, label: '审核流程', relation: 'governed_by', intensity: 0.1, animated: true },
    ],
    governance: { raw_payload_returned: false, scope: 'global' },
  }
  mocks.learningApi.flowTopology.mockResolvedValue(topologyResponse)
  mocks.learningApi.flowGraph.mockResolvedValue({ nodes: [], edges: [{ id: 1, source: 'run:r1', target: 'learning_artifact:a1', relation: 'produced' }] })
  mocks.learningApi.flowJourneys.mockResolvedValue({
    items: [
      {
        id: 'journey:e1',
        event_id: 'e1',
        title: '搜索访客下降，建议优化关键词和价格力。',
        source_type: 'execution_run',
        source_id: 'run-learning-1',
        skill_id: 'learn-skill',
        state: 'flowing',
        progress: 0.72,
        blockers: ['candidate_open'],
        last_at: '2026-06-01T15:08:00',
        steps: [
          { stage: 'source', label: 'Skill 运行', entity_type: 'run', entity_id: 'run-learning-1', status: 'completed', meta: 'learn-skill' },
          { stage: 'event', label: 'skill.run.completed', entity_type: 'learning_event', entity_id: 'e1', status: 'captured', meta: 'Q 0.9' },
          { stage: 'control', label: '运行 Agent 控制', entity_type: 'agent', entity_id: 'agent-runtime-1', status: 'completed', meta: 'OpenClaw' },
          { stage: 'artifact', label: '链接下滑结果复核', entity_type: 'learning_artifact', entity_id: 'a1', status: 'materialized', meta: '报告摘要 · auto_index' },
          { stage: 'sink', label: 'knowledge_document', entity_type: 'knowledge_document', entity_id: 'doc1', status: 'materialized', meta: '知识库' },
          { stage: 'feedback', label: '钉钉反馈回传', entity_type: 'dingtalk_feedback', entity_id: 'ding:todo-1', status: 'completed', meta: 'dingtalk' },
          { stage: 'deployment', label: '模型部署可用', entity_type: 'model_deployment', entity_id: 'deploy-1', status: 'completed', meta: 'item_decline_ranker' },
          { stage: 'decision', label: '模型参与决策', entity_type: 'model_deployment', entity_id: 'deploy-1', status: 'completed', meta: 'item_decline_ranker' },
          { stage: 'candidate', label: '优化关键词诊断 Skill', entity_type: 'improvement_candidate', entity_id: 'c1', status: 'open', meta: 'skill · R2' },
        ],
      },
    ],
    total: 1,
    state_counts: { flowing: 1 },
    blocker_counts: { candidate_open: 1 },
    governance: { raw_payload_returned: false },
  })
  mocks.learningApi.bottlenecks.mockResolvedValue({
    summary: { total: 2, ready_artifacts: 1, open_candidates: 1 },
    sections: [
      { key: 'ready_artifacts', title: '待生成知识内容', count: 1, severity: 'warning', action: 'materialize', items: [{ id: 'a1', artifact_kind: 'report_summary', title: '链接下滑结果复核', status: 'ready' }] },
      { key: 'open_candidates', title: '待审核建议', count: 1, severity: 'warning', action: 'create_review', items: [{ id: 'c1', target_type: 'skill', title: '优化关键词诊断 Skill', status: 'open' }] },
    ],
    governance: { raw_payload_returned: false },
  })
  mocks.learningApi.events.mockResolvedValue({ items: [{ id: 'e1', event_type: 'decision.completed', source_type: 'decision_log', source_id: 'd1', redacted_summary: '报告有效。', status: 'captured', quality_score: 0.9, created_at: '2026-06-01T15:00:00' }], total: 1 })
  mocks.learningApi.artifacts.mockResolvedValue({ items: [{ id: 'a1', artifact_kind: 'report_summary', title: '链接下滑结果复核', summary: '搜索访客下降。', status: 'materialized', quality_score: 0.9 }], total: 1 })
  mocks.learningApi.candidates.mockResolvedValue({ items: [{ id: 'c1', target_type: 'skill', title: '优化关键词诊断 Skill', proposal: '整理为标准 Skill。', status: 'open', risk_level: 'R2', priority_score: 0.8 }], total: 1 })
  mocks.learningApi.trainingManifest.mockResolvedValue({ dataset_ref: 'learning-artifacts://visible/training/latest', sample_total: 3, skills: [{ skill_id: 'learn-skill', sample_count: 3 }], governance: { raw_payload_returned: false } })
  const automationResponse = {
    automation: {
      enabled: true,
      status: 'succeeded',
      service_status: 'running',
      service_mode: 'independent_worker',
      service_owner: 'skillforge-learning-auto-flow.service',
      service_started_at: '2026-06-01T15:00:00',
      service_uptime_seconds: 900,
      first_delay_seconds: 120,
      first_run_planned_at: '2026-06-01T15:02:00',
      next_run_planned_at: '2026-06-01T15:15:00',
      interval_seconds: 300,
      auto_training_enabled: true,
      finished_at: '2026-06-01T15:10:00',
      result: {
        auto_limits: {
          training_interval_seconds: 86400,
          training_days: 1,
          latest_training_at: '2026-06-01T15:10:00',
          training_next_after: '2026-06-02T15:10:00',
        },
      },
    },
    backlog: { auto_materializable: 2, review_required: 1 },
    jobs: { by_status: { failed: 0 } },
    latest: { event_at: '2026-06-01T15:05:00', automation_run: { id: 'lar1', status: 'succeeded' } },
    automation_runs: [{ id: 'lar1', trigger_type: 'scheduled', status: 'succeeded', finished_at: '2026-06-01T15:10:00', result: { materialized: 2 } }],
    governance: { raw_payload_returned: false },
  }
  mocks.learningApi.automationStatus.mockResolvedValue(automationResponse)
  mocks.learningApi.home.mockResolvedValue({
    summary: summaryResponse,
    topology: topologyResponse,
    automation: automationResponse,
  })
  mocks.learningApi.runAutomation.mockResolvedValue({ status: 'succeeded', result: { captured: {}, materialized: 0 } })
  mocks.learningApi.materializeArtifact.mockResolvedValue({ ok: true })
  mocks.learningApi.createCandidateReview.mockResolvedValue({ ok: true })
  mocks.learningApi.backfill.mockResolvedValue({ captured: {}, materialized: {} })
  mocks.learningApi.lineage.mockResolvedValue({
    edges: [{ id: 1, from_type: 'run', from_id: 'r1', to_type: 'learning_artifact', to_id: 'a1', relation: 'produced' }],
  })
}

function deploymentPulseResponse(deployments: any[]) {
  return {
    modules: [
      { key: 'raw_inputs', title: '原始数据', status: 'ready', metrics: [], items: [], flow_detail: { sections: [] } },
      { key: 'clarified_data', title: '数据清洗', status: 'ready', metrics: [], items: [], flow_detail: { sections: [] } },
      { key: 'finetuning', title: '微调模型', status: 'completed', metrics: [], items: [], flow_detail: { sections: [] } },
      { key: 'test_results', title: '模型测试', status: 'passed', metrics: [], items: [], flow_detail: { sections: [] } },
      {
        key: 'application_outputs',
        title: '部署输出',
        status: 'applied',
        metrics: [],
        items: [],
        flow_detail: {
          sections: [
            {
              key: 'deployments',
              title: '部署位置',
              items: deployments,
            },
          ],
        },
      },
    ],
    generated_at: '2026-06-24T10:00:00+08:00',
  }
}

describe('LearningFlow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.routeQuery = {}
    mockLearningResponses()
  })

  it('renders server-driven flowing topology and opens node detail', async () => {
    const wrapper = mount(LearningFlow, { global: { stubs: stubs() } })
    await flushPromises()

    expect(wrapper.text()).toContain('脉动')
    expect(wrapper.find('.learning-page').classes()).toContain('layout-simple')
    expect(wrapper.find('.pulse-chain-board').exists()).toBe(true)
    expect(wrapper.find('.pulse-chain-board').text()).toContain('原始数据、数据清洗、训练、模型测试、部署输出')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('原始数据')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('数据清洗')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('微调模型')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('模型测试')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('部署输出')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('预计时间')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('数据库存储')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('向量分片')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('数据库版本')
    expect(wrapper.findAll('.pulse-chain-card')).toHaveLength(5)
    await wrapper.find('.pulse-chain-card .pulse-chain-main').trigger('click')
    expect(wrapper.find('.drawer').text()).toContain('learning_pulse_module')
    expect(wrapper.find('.drawer').text()).toContain('原始数据')
    expect(wrapper.find('.drawer').text()).toContain('原始数据进入明细')
    expect(wrapper.find('.drawer').text()).toContain('原始数据接入日志')
    expect(wrapper.find('.drawer').text()).toContain('实时刷新')
    expect(wrapper.find('.drawer').text()).toContain('生产过程')
    expect(wrapper.find('.drawer').text()).toContain('生产明细')
    expect(wrapper.find('.drawer').text()).toContain('最近变化')
    expect(wrapper.find('.detail-terminal').exists()).toBe(true)
    expect(wrapper.findAll('.detail-terminal-line').length).toBeGreaterThan(0)
    expect(wrapper.find('.detail-terminal').text()).toContain('OPEN')
    expect(wrapper.find('.detail-terminal').text()).toContain('/api/learning/pulse returned module=raw_inputs')
    expect(wrapper.find('.detail-terminal').text()).toContain('IN')
    expect(wrapper.find('.detail-terminal').text()).toContain('RUN')
    expect(wrapper.find('.detail-terminal').text()).toContain('OUT')
    expect(wrapper.find('.detail-terminal').text()).toContain('通过来源哈希与权限范围校验后进入学习流')
    expect(wrapper.find('.drawer').text()).toContain('数据过程')
    expect(wrapper.find('.drawer').text()).toContain('输入')
    expect(wrapper.find('.drawer').text()).toContain('本步处理')
    expect(wrapper.find('.drawer').text()).toContain('输出')
    expect(wrapper.find('.drawer').text()).toContain('本步处理数据')
    expect(wrapper.find('.drawer').text()).toContain('输入数据')
    expect(wrapper.find('.drawer').text()).toContain('处理动作')
    expect(wrapper.find('.drawer').text()).toContain('输出去向')
    expect(wrapper.find('.drawer').text()).toContain('当前结果')
    expect(wrapper.find('.drawer').text()).toContain('正在接收并校验')
    expect(wrapper.find('.drawer').text()).toContain('新增进入记录')
    expect(wrapper.find('.drawer').text()).toContain('数据清洗')
    expect(wrapper.find('.drawer').text()).toContain('审计 JSON')
    await wrapper.find('.pulse-chain-card .pulse-chain-metrics button').trigger('click')
    expect(wrapper.find('.drawer').text()).toContain('learning_pulse_metric')
    expect(wrapper.find('.drawer').text()).toContain('原始记录')
    expect(wrapper.find('.drawer').text()).toContain('新增进入记录')
    await wrapper.find('.pulse-chain-card .pulse-chain-items button').trigger('click')
    expect(wrapper.find('.drawer').text()).toContain('Skill 运行完成')
    expect(wrapper.find('.drawer').text()).toContain('原始数据进入明细')
    expect(wrapper.find('.ai-side-context').text()).toContain('Skill 运行完成')
    await wrapper.findAll('.pulse-chain-card .pulse-chain-main')[1].trigger('click')
    expect(wrapper.find('.drawer').text()).toContain('数据清洗处理明细')
    expect(wrapper.find('.drawer').text()).toContain('数据清洗处理日志')
    expect(wrapper.find('.detail-terminal').text()).toContain('链接下滑结果复核')
    expect(wrapper.find('.detail-terminal').text()).toContain('质量分 0.9')
    expect(wrapper.find('.drawer').text()).toContain('正在清洗')
    expect(wrapper.find('.drawer').text()).toContain('保留并进入下游的数据')
    expect(wrapper.find('.drawer').text()).toContain('丢弃或不进入下游的数据')
    await wrapper.findAll('.pulse-chain-card .pulse-chain-main')[3].trigger('click')
    expect(wrapper.find('.drawer').text()).toContain('模型测试明细')
    expect(wrapper.find('.drawer').text()).toContain('模型测试日志')
    expect(wrapper.find('.drawer').text()).toContain('正在检查')
    expect(wrapper.find('.drawer').text()).toContain('测试任务与 Agent')
    await wrapper.findAll('.pulse-chain-card .pulse-chain-main')[4].trigger('click')
    expect(wrapper.find('.drawer').text()).toContain('部署与应用输出明细')
    expect(wrapper.find('.drawer').text()).toContain('部署输出日志')
    expect(wrapper.find('.detail-terminal').text()).toContain('learn-skill:lora')
    expect(wrapper.find('.drawer').text()).toContain('正在核对')
    expect(wrapper.find('.drawer').text()).toContain('部署输出验证')
    expect(wrapper.find('.drawer').text()).toContain('测试过程')
    expect(wrapper.find('.drawer').text()).toContain('输出过程')
    expect(wrapper.find('.drawer').text()).toContain('对话模型')
    await wrapper.findAll('button').find(item => item.text().includes('对话模型'))?.trigger('click')
    expect(wrapper.text()).toContain('训练后的模型')
    expect(wrapper.text()).toContain('发送')
    expect(wrapper.find('.drawer').text()).toContain('进入完整对话')
    expect(wrapper.find('.drawer').text()).toContain('部署位置')
    expect(wrapper.find('.drawer').text()).toContain('模型对话输入输出')
    expect(wrapper.find('.pulse-overview-board').exists()).toBe(true)
    expect(wrapper.find('.pulse-overview-board').text()).toContain('概览')
    expect(wrapper.find('.pulse-overview-board').text()).toContain('关键有效信息')
    expect(wrapper.find('.pulse-overview-board').text()).toContain('建议立即处理')
    expect(wrapper.find('.pulse-overview-board').text()).toContain('待处理')
    expect(wrapper.find('.pulse-value-panel').exists()).toBe(true)
    expect(wrapper.find('.pulse-value-panel').text()).toContain('价值评估')
    expect(wrapper.find('.pulse-value-panel').text()).toContain('当前收益或风险最高')
    expect(wrapper.find('.pulse-decision-strip').exists()).toBe(true)
    expect(wrapper.find('.pulse-decision-strip').text()).toContain('建议操作')
    expect(wrapper.find('.pulse-decision-strip').text()).toContain('主要依据')
    expect(wrapper.findAll('.pulse-decision-item')).toHaveLength(4)
    expect(wrapper.find('.pulse-automation-strip').exists()).toBe(true)
    expect(wrapper.find('.pulse-automation-strip').text()).toContain('自动化运行态')
    expect(wrapper.find('.pulse-automation-strip').text()).toContain('待生成知识')
    expect(wrapper.find('.pulse-automation-strip').text()).toContain('待审核')
    expect(wrapper.find('.pulse-automation-strip').text()).toContain('失败任务')
    expect(wrapper.findAll('.pulse-automation-card')).toHaveLength(4)
    expect(wrapper.find('.automation-service-grid').exists()).toBe(true)
    expect(wrapper.find('.automation-service-grid').text()).toContain('服务模式')
    expect(wrapper.find('.automation-service-grid').text()).toContain('独立 Worker')
    expect(wrapper.find('.automation-service-grid').text()).toContain('计划启动')
    expect(wrapper.find('.automation-service-grid').text()).toContain('服务时间')
    expect(wrapper.find('.automation-service-grid').text()).toContain('15分钟')
    expect(wrapper.find('.automation-service-grid').text()).toContain('下次检查')
    expect(wrapper.find('.automation-service-grid').text()).toContain('自动训练')
    expect(wrapper.find('.automation-service-grid').text()).toContain('2026-06-02')
    expect(wrapper.findAll('.pulse-overview-card')).toHaveLength(4)
    expect(wrapper.find('.pulse-overview-card').classes().some((name) => name.startsWith('sf-tone-'))).toBe(true)
    expect(wrapper.findAll('.pulse-overview-step')).toHaveLength(4)
    expect(wrapper.find('.pulse-overview-step').classes().some((name) => name.startsWith('sf-tone-'))).toBe(true)
    expect(wrapper.find('.pulse-command').exists()).toBe(false)
    expect(wrapper.find('.ai-side-menu').exists()).toBe(true)
    expect(wrapper.find('.ai-side-scroll-progress').exists()).toBe(true)
    expect(wrapper.find('.ai-side-menu').attributes('style')).toContain('--ai-side-scroll-progress')
    expect(wrapper.find('.ai-side-menu').text()).toContain('自动化工作台')
    expect(wrapper.find('.ai-side-menu').text()).toContain('下一步操作')
    expect(wrapper.find('.learning-page').classes()).toContain('motion-auto')
    expect(wrapper.find('.ai-side-quick-controls').exists()).toBe(true)
    expect(wrapper.findAll('.ai-side-quick-controls button')).toHaveLength(4)
    expect(wrapper.find('.ai-side-quick-controls').text()).toContain('标准')
    expect(wrapper.find('.ai-side-quick-controls').text()).toContain('阅读')
    expect(wrapper.find('.ai-side-quick-controls').text()).toContain('追踪')
    expect(wrapper.find('.ai-side-quick-controls').text()).toContain('详细视图')
    expect(wrapper.find('.ai-side-more-tools').exists()).toBe(true)
    expect(wrapper.find('.ai-side-more-tools').text()).toContain('展开更多工具')
    expect(wrapper.find('.ai-side-menu').classes()).not.toContain('tools-expanded')
    await wrapper.find('.ai-side-more-tools button').trigger('click')
    expect(wrapper.find('.ai-side-menu').classes()).toContain('tools-expanded')
    expect(wrapper.find('.ai-side-more-tools').text()).toContain('收起辅助工具')
    expect(wrapper.find('.ai-operation-center').exists()).toBe(true)
    expect(wrapper.find('.ai-operation-center').text()).toContain('自动处理建议')
    expect(wrapper.find('.ai-operation-center').text()).toContain('等待人工确认')
    expect(wrapper.find('.ai-operation-center').text()).toContain('处理依据')
    expect(wrapper.find('.ai-operation-center').text()).toContain('权限与安全')
    expect(wrapper.find('.ai-operation-center').attributes('style')).toContain('--operation-load')
    expect(wrapper.find('.ai-operation-status').exists()).toBe(true)
    expect(wrapper.find('.ai-operation-status').text()).toContain('上次检查')
    expect(wrapper.find('.ai-operation-status').text()).toContain('下次检查')
    expect(wrapper.find('.ai-operation-status').text()).toContain('当前压力')
    expect(wrapper.findAll('.ai-operation-status-item')).toHaveLength(3)
    expect(wrapper.find('.ai-operation-rail').exists()).toBe(false)
    expect(wrapper.find('.ai-operation-decision').exists()).toBe(true)
    expect(wrapper.find('.ai-operation-decision').text()).toContain('下一步操作')
    expect(wrapper.find('.ai-operation-decision').text()).toContain('主要依据')
    expect(wrapper.find('.ai-operation-decision').text()).toContain('影响范围')
    expect(wrapper.findAll('.ai-operation-decision-card')).toHaveLength(3)
    expect(wrapper.find('.ai-operation-queue').exists()).toBe(true)
    expect(wrapper.find('.ai-operation-queue').text()).toContain('待生成知识')
    expect(wrapper.find('.ai-operation-queue').text()).toContain('待审核')
    expect(wrapper.find('.ai-operation-queue').text()).toContain('失败任务')
    expect(wrapper.findAll('.ai-operation-queue-chip')).toHaveLength(3)
    expect(wrapper.find('.ai-simple-metrics').exists()).toBe(false)
    expect(wrapper.find('.ai-simple-priority').exists()).toBe(true)
    expect(wrapper.find('.ai-simple-priority').text()).toContain('处理清单')
    expect(wrapper.find('.ai-simple-priority').text()).toContain('优先级')
    expect(wrapper.findAll('.ai-simple-priority-item')).toHaveLength(3)
    expect(wrapper.findAll('.ai-simple-priority-item.primary')).toHaveLength(1)
    expect(wrapper.find('.ai-simple-priority-item').classes().some((name) => name.startsWith('sf-tone-'))).toBe(true)
    expect(wrapper.find('.ai-side-hud').exists()).toBe(true)
    expect(wrapper.findAll('.ai-side-hud-card')).toHaveLength(4)
    expect(wrapper.find('.ai-side-hud').text()).toContain('当前视窗')
    expect(wrapper.find('.ai-side-hud').text()).toContain('最优下一步')
    expect(wrapper.find('.ai-side-hud').text()).toContain('队列压力')
    expect(wrapper.find('.ai-side-hud').text()).toContain('显示状态')
    expect(wrapper.find('.ai-mission-brief').exists()).toBe(true)
    expect(wrapper.find('.ai-mission-brief').text()).toContain('当前建议')
    expect(wrapper.find('.ai-mission-brief').text()).toContain('待生成知识')
    expect(wrapper.find('.ai-mission-brief').text()).toContain('数据同步')
    expect(wrapper.find('.ai-mission-brief').text()).toContain('风险判断')
    expect(wrapper.find('.ai-mission-brief').text()).toContain('建议操作')
    expect(wrapper.find('.ai-mission-brief').text()).toContain('结果复核')
    expect(wrapper.findAll('.ai-mission-step')).toHaveLength(4)
    expect(wrapper.find('.ai-simple-rationale').exists()).toBe(false)
    expect(wrapper.find('.ai-simple-plan').exists()).toBe(false)
    expect(wrapper.find('.ai-sticky-command').exists()).toBe(true)
    expect(wrapper.find('.ai-sticky-command').text()).toContain('建议操作')
    expect(wrapper.find('.ai-sticky-command').text()).toContain('待生成知识')
    expect(wrapper.find('.ai-sticky-command-confidence').text()).toContain('可信度')
    expect(wrapper.find('.ai-sticky-command-confidence').text()).toContain('%')
    expect(wrapper.find('.ai-sticky-command-confidence').text()).toContain('条依据')
    expect(wrapper.find('.ai-sticky-command-preview').text()).toContain('操作预览')
    expect(wrapper.find('.ai-sticky-command-preview').text()).toContain('定位')
    expect(wrapper.find('.ai-sticky-command-preview').text()).toContain('聚焦')
    expect(wrapper.find('.ai-sticky-command-preview').text()).toContain('显示效果')
    expect(wrapper.find('.ai-sticky-command-preview').text()).toContain('安全说明')
    expect(wrapper.find('.ai-sticky-command-preview').text()).toContain('自动引擎')
    expect(wrapper.find('.ai-sticky-command-reason').text()).toContain('建议依据')
    expect(wrapper.find('.ai-sticky-command-reason').text()).toContain('证据')
    expect(wrapper.find('.ai-sticky-command-reason').text()).toContain('根因')
    expect(wrapper.find('.ai-sticky-command-reason').text()).toContain('收益')
    expect(wrapper.find('.ai-sticky-command-reason').text()).toContain('生成知识')
    expect(wrapper.findAll('.orbit-signal')).toHaveLength(6)
    expect(wrapper.find('.ai-side-index').exists()).toBe(true)
    expect(wrapper.findAll('.ai-side-index-button')).toHaveLength(8)
    expect(wrapper.find('.ai-side-index').text()).toContain('状态')
    expect(wrapper.find('.ai-side-index').text()).toContain('决策')
    expect(wrapper.find('.ai-side-index').text()).toContain('编排')
    expect(wrapper.find('.ai-side-index').text()).toContain('安全说明')
    expect(wrapper.find('.ai-side-index').text()).toContain('根因')
    expect(wrapper.find('.ai-side-index').text()).toContain('收益')
    const rootCauseIndex = wrapper.findAll('.ai-side-index-button').find((button) => button.text().includes('根因'))
    if (!rootCauseIndex) throw new Error('missing root cause side index')
    await rootCauseIndex.trigger('click')
    expect(rootCauseIndex.classes()).toContain('active')
    expect(wrapper.find('.ai-side-menu').text()).toContain('系统状态')
    expect(wrapper.find('.ai-heartbeat-core').text()).toContain('自动检查')
    expect(wrapper.find('.ai-heartbeat-core').text()).toContain('下次')
    expect(wrapper.findAll('.ai-heartbeat-step')).toHaveLength(3)
    expect(wrapper.find('.ai-side-menu').text()).toContain('队列压力')
    expect(wrapper.find('.ai-side-menu').text()).toContain('自动处理建议')
    expect(wrapper.find('.ai-autopilot').exists()).toBe(true)
    expect(wrapper.findAll('.ai-autopilot-stage')).toHaveLength(4)
    expect(wrapper.find('.ai-autopilot-primary').text()).toContain('查看待处理项')
    expect(wrapper.find('.ai-execution-guardrail').exists()).toBe(true)
    expect(wrapper.find('.ai-execution-guardrail').text()).toContain('执行前说明')
    expect(wrapper.findAll('.ai-guardrail-card')).toHaveLength(4)
    expect(wrapper.find('.ai-execution-guardrail').text()).toContain('范围')
    expect(wrapper.find('.ai-execution-guardrail').text()).toContain('影响范围')
    expect(wrapper.find('.ai-execution-guardrail').text()).toContain('规则')
    expect(wrapper.find('.ai-execution-guardrail').text()).toContain('查看')
    expect(wrapper.find('.ai-execution-guardrail').text()).toContain('不会自动发布或变更上线状态')
    expect(wrapper.find('.ai-side-menu').text()).toContain('建议先处理待审核事项')
    expect(wrapper.find('.ai-side-menu').text()).toContain('待审核事项')
    expect(wrapper.find('.ai-side-menu').text()).toContain('训练改进建议')
    expect(wrapper.find('.ai-side-menu').text()).toContain('风险判断证据')
    expect(wrapper.findAll('.ai-evidence-card')).toHaveLength(4)
    expect(wrapper.find('.ai-side-menu').text()).toContain('记录依据')
    expect(wrapper.find('.ai-side-menu').text()).toContain('知识生成覆盖')
    expect(wrapper.find('.ai-side-menu').text()).toContain('审核待办')
    expect(wrapper.find('.ai-side-menu').text()).toContain('原因分析')
    expect(wrapper.findAll('.ai-rootcause-card')).toHaveLength(2)
    expect(wrapper.find('.ai-side-menu').text()).toContain('审核未完成')
    expect(wrapper.find('.ai-side-menu').text()).toContain('内容未生成知识')
    expect(wrapper.find('.ai-side-menu').text()).toContain('影响评估')
    expect(wrapper.findAll('.ai-impact-card')).toHaveLength(3)
    expect(wrapper.find('.ai-side-menu').text()).toContain('审核完成收益')
    expect(wrapper.find('.ai-side-menu').text()).toContain('知识生成收益')
    expect(wrapper.find('.ai-side-menu').text()).toContain('优先级排序')
    expect(wrapper.findAll('.ai-heat-card')).toHaveLength(4)
    expect(wrapper.find('.ai-side-menu').text()).toContain('审核风险')
    expect(wrapper.find('.ai-side-menu').text()).toContain('知识生成机会')
    expect(wrapper.find('.ai-side-menu').text()).toContain('建议优先级')
    expect(wrapper.findAll('.ai-decision-item').length).toBeGreaterThanOrEqual(3)
    expect(wrapper.find('.ai-side-menu').text()).toContain('待生成知识')
    expect(wrapper.find('.ai-side-menu').text()).toContain('待生成知识')
    expect(wrapper.find('.ai-side-menu').text()).toContain('执行步骤')
    expect(wrapper.findAll('.ai-command-step')).toHaveLength(4)
    expect(wrapper.find('.ai-command-plan').text()).toContain('风险判断')
    expect(wrapper.find('.ai-command-plan').text()).toContain('安全说明')
    expect(wrapper.find('.ai-command-plan').text()).toContain('验证')
    expect(wrapper.find('.ai-side-menu').text()).toContain('快捷处理方案')
    expect(wrapper.findAll('.ai-playbook')).toHaveLength(4)
    expect(wrapper.find('.ai-side-menu').text()).toContain('优先处理风险')
    expect(wrapper.find('.ai-side-menu').text()).toContain('知识生成')
    expect(wrapper.find('.ai-action-trail').exists()).toBe(false)
    expect(wrapper.find('.ai-side-menu').text()).toContain('显示模式')
    expect(wrapper.findAll('.ai-motion-mode button')).toHaveLength(3)
    expect(wrapper.find('.ai-motion-mode').text()).toContain('标准')
    expect(wrapper.find('.ai-motion-mode').text()).toContain('阅读')
    expect(wrapper.find('.ai-motion-mode').text()).toContain('追踪')
    expect(wrapper.find('.ai-side-menu').text()).toContain('查看视角')
    expect(wrapper.findAll('.ai-perspective')).toHaveLength(5)
    expect(wrapper.find('.ai-side-menu').text()).toContain('聚焦控制')
    expect(wrapper.findAll('.ai-focus-shortcut')).toHaveLength(4)
    expect(wrapper.find('.ai-focus-radar').text()).toContain('检查')
    expect(wrapper.findAll('.ai-side-nav button')).toHaveLength(8)
    expect(wrapper.find('.ai-side-menu').text()).toContain('筛选条件')
    expect(wrapper.findAll('.ai-side-action').length).toBeGreaterThan(0)
    expect(wrapper.find('.ai-side-menu').text()).toContain('显示状态')
    expect(wrapper.findAll('.ai-motion-meter')).toHaveLength(3)
    expect(wrapper.find('.ai-side-route').exists()).toBe(true)
    expect(wrapper.find('.ai-side-menu').text()).toContain('处理路径')
    expect(wrapper.findAll('.ai-side-route-step')).toHaveLength(5)
    expect(wrapper.text()).toContain('运行评估')
    expect(wrapper.text()).toContain('系统处理路径')
    expect(wrapper.find('.run-brief-rail').exists()).toBe(true)
    expect(wrapper.findAll('.run-brief-packet')).toHaveLength(2)
    expect(wrapper.findAll('.run-brief-node')).toHaveLength(4)
    expect(wrapper.text()).toContain('全局处理视图')
    expect(wrapper.findAll('.global-flow-node')).toHaveLength(8)
    expect(wrapper.findAll('.global-edge-label')).toHaveLength(8)
    expect(wrapper.find('.global-flow-core').text()).toContain('整体状态')
    expect(wrapper.findAll('.global-node-meter')).toHaveLength(8)
    expect(wrapper.findAll('.flow-lens-node')).toHaveLength(5)
    expect(wrapper.find('.flow-lens-strip').text()).toContain('快速筛选')
    expect(wrapper.text()).toContain('新增')
    expect(wrapper.text()).toContain('更新')
    expect(wrapper.text()).toContain('动态标记=存在真实记录')
    const governanceEvidence = wrapper.findAll('.ai-evidence-card').find((item) => item.text().includes('审核待办'))
    expect(governanceEvidence).toBeTruthy()
    await governanceEvidence!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.drawer').text()).toContain('ai_evidence_signal')
    expect(wrapper.find('.drawer').text()).toContain('审核待办')
    expect(wrapper.find('.ai-side-menu').text()).toContain('操作记录')
    expect(wrapper.find('.ai-action-trail').text()).toContain('审核待办')
    expect(wrapper.find('.ai-side-menu').text()).toContain('1 步可查看记录')
    expect(wrapper.find('.ai-focus-chips').text()).toContain('建议状态')
    const clearCandidateChip = wrapper.findAll('.ai-focus-chips button').find((item) => item.text().includes('建议状态'))
    expect(clearCandidateChip).toBeTruthy()
    await clearCandidateChip!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.drawer').text()).toContain('ai_focus_control')
    expect(wrapper.find('.drawer').text()).toContain('移除聚焦筛选')
    expect(wrapper.find('.ai-focus-chips').exists()).toBe(false)
    expect(mocks.learningApi.candidates).toHaveBeenCalledWith(expect.objectContaining({ status: 'open' }))
    const clearEvidenceContext = wrapper.findAll('.ai-side-context-actions button').find((item) => item.text() === '清除')
    expect(clearEvidenceContext).toBeTruthy()
    await clearEvidenceContext!.trigger('click')
    expect(wrapper.find('.drawer').exists()).toBe(false)
    const sfSideFocus = wrapper.findAll('.ai-focus-shortcut').find((item) => item.text().includes('SF / MCP'))
    expect(sfSideFocus).toBeTruthy()
    await sfSideFocus!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.drawer').text()).toContain('ai_focus_command')
    expect(wrapper.find('.drawer').text()).toContain('SF / MCP')
    expect(wrapper.find('.ai-focus-chips').text()).toContain('来源')
    expect(wrapper.find('.ai-action-trail').text()).toContain('SF / MCP')
    expect(mocks.learningApi.home).toHaveBeenCalledWith(expect.objectContaining({ source_type: 'sf_mcp_call' }))
    const clearSideFocusContext = wrapper.findAll('.ai-side-context-actions button').find((item) => item.text() === '清除')
    expect(clearSideFocusContext).toBeTruthy()
    await clearSideFocusContext!.trigger('click')
    expect(wrapper.find('.drawer').exists()).toBe(false)
    const governanceHeat = wrapper.findAll('.ai-heat-card').find((item) => item.text().includes('审核风险'))
    expect(governanceHeat).toBeTruthy()
    await governanceHeat!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.drawer').text()).toContain('ai_heat_signal')
    expect(wrapper.find('.drawer').text()).toContain('审核风险')
    expect(wrapper.findAll('.ai-trail-item')).toHaveLength(3)
    expect(wrapper.find('.ai-action-trail').text()).toContain('审核风险')
    expect(mocks.learningApi.candidates).toHaveBeenCalledWith(expect.objectContaining({ status: 'open' }))
    const clearHeatContext = wrapper.findAll('.ai-side-context-actions button').find((item) => item.text() === '清除')
    expect(clearHeatContext).toBeTruthy()
    await clearHeatContext!.trigger('click')
    expect(wrapper.find('.drawer').exists()).toBe(false)
    const riskPlaybook = wrapper.findAll('.ai-playbook').find((item) => item.text().includes('优先处理风险'))
    expect(riskPlaybook).toBeTruthy()
    await riskPlaybook!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.drawer').text()).toContain('ai_playbook')
    expect(wrapper.find('.drawer').text()).toContain('优先处理风险')
    expect(wrapper.find('.learning-page').classes()).toContain('motion-trace')
    expect(wrapper.find('.ai-action-trail').text()).toContain('优先处理风险')
    expect(wrapper.findAll('.ai-trail-item')).toHaveLength(4)
    expect(mocks.learningApi.candidates).toHaveBeenCalledWith(expect.objectContaining({ status: 'open' }))
    const clearPlaybookContext = wrapper.findAll('.ai-side-context-actions button').find((item) => item.text() === '清除')
    expect(clearPlaybookContext).toBeTruthy()
    await clearPlaybookContext!.trigger('click')
    expect(wrapper.find('.drawer').exists()).toBe(false)
    const replayRiskTrail = wrapper.findAll('.ai-trail-item').find((item) => item.text().includes('优先处理风险'))
    expect(replayRiskTrail).toBeTruthy()
    await replayRiskTrail!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.drawer').text()).toContain('ai_playbook')
    expect(wrapper.find('.drawer').text()).toContain('优先处理风险')
    expect(wrapper.find('.learning-page').classes()).toContain('motion-trace')
    const clearReplayedContext = wrapper.findAll('.ai-side-context-actions button').find((item) => item.text() === '清除')
    expect(clearReplayedContext).toBeTruthy()
    await clearReplayedContext!.trigger('click')
    expect(wrapper.find('.drawer').exists()).toBe(false)
    const priorityDecision = wrapper.findAll('.ai-decision-item').find((item) => item.text().includes('优先处理项'))
    expect(priorityDecision).toBeTruthy()
    await priorityDecision!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.drawer').text()).toContain('ai_decision_queue')
    expect(wrapper.find('.drawer').text()).toContain('优先处理项')
    expect(wrapper.find('.ai-action-trail').text()).toContain('优先处理项')
    expect(mocks.learningApi.artifacts).toHaveBeenCalledWith(expect.objectContaining({ status: 'ready' }))
    const clearDecisionContext = wrapper.findAll('.ai-side-context-actions button').find((item) => item.text() === '清除')
    expect(clearDecisionContext).toBeTruthy()
    await clearDecisionContext!.trigger('click')
    expect(wrapper.find('.drawer').exists()).toBe(false)
    const autopilotGovernStage = wrapper.findAll('.ai-autopilot-stage').find((item) => item.text().includes('待审核事项'))
    expect(autopilotGovernStage).toBeTruthy()
    await autopilotGovernStage!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.drawer').text()).toContain('ai_autopilot_stage')
    expect(wrapper.find('.drawer').text()).toContain('待审核事项')
    expect(mocks.learningApi.candidates).toHaveBeenCalledWith(expect.objectContaining({ status: 'open' }))
    const clearAutopilotContext = wrapper.findAll('.ai-side-context-actions button').find((item) => item.text() === '清除')
    expect(clearAutopilotContext).toBeTruthy()
    await clearAutopilotContext!.trigger('click')
    expect(wrapper.find('.drawer').exists()).toBe(false)
    await wrapper.findAll('.global-flow-node')[2].trigger('click')
    expect(wrapper.find('.global-node-detail').text()).toContain('学习资产')
    expect(wrapper.find('.global-detail-narrative').text()).toContain('学习资产')
    expect(wrapper.findAll('.global-detail-metric')).toHaveLength(3)
    expect(wrapper.find('.global-node-detail').text()).toContain('新增明细')
    expect(wrapper.find('.global-node-detail').text()).toContain('更新明细')
    expect(wrapper.find('.drawer').exists()).toBe(false)
    const globalChange = wrapper.findAll('.global-detail-changes button').find((item) => item.text().includes('链接下滑结果复核'))
    expect(globalChange).toBeTruthy()
    await globalChange!.trigger('click')
    expect(wrapper.find('.drawer').text()).toContain('global_node_addition')
    expect(wrapper.find('.ai-side-context').text()).toContain('当前观察对象')
    expect(wrapper.find('.ai-side-context').text()).toContain('global_node_addition')
    expect(wrapper.find('.ai-side-context').text()).toContain('链接下滑结果复核')
    const extractEdge = wrapper.findAll('.global-edge-label').find((item) => item.text().includes('提取'))
    expect(extractEdge).toBeTruthy()
    await extractEdge!.trigger('click')
    expect(wrapper.find('.global-edge-focus').text()).toContain('正在查看路径')
    expect(wrapper.find('.drawer').text()).toContain('global_flow_edge')
    const calmMode = wrapper.findAll('.ai-motion-mode button').find((item) => item.text().includes('阅读'))
    expect(calmMode).toBeTruthy()
    await calmMode!.trigger('click')
    expect(wrapper.find('.learning-page').classes()).toContain('motion-calm')
    const sfFocus = wrapper.findAll('.flow-lens-node').find((item) => item.text().includes('SF / MCP'))
    expect(sfFocus).toBeTruthy()
    await sfFocus!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.drawer').text()).toContain('SF / MCP')
    expect(mocks.learningApi.home).toHaveBeenCalledWith(expect.objectContaining({ source_type: 'sf_mcp_call' }))
    const trainingView = wrapper.findAll('.ai-perspective').find((item) => item.text().includes('训练'))
    expect(trainingView).toBeTruthy()
    await trainingView!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.drawer').text()).toContain('ai_perspective')
    expect(wrapper.find('.drawer').text()).toContain('训练')
    expect(mocks.learningApi.home).toHaveBeenCalledWith(expect.objectContaining({ source_type: 'training_job' }))
    const canvasNav = wrapper.findAll('.ai-side-nav button').find((item) => item.text().includes('处理流程'))
    expect(canvasNav).toBeTruthy()
    await canvasNav!.trigger('click')
    expect(canvasNav!.classes()).toContain('active')
    const governRouteStep = wrapper.findAll('.ai-side-route-step').find((item) => item.text().includes('审核流程'))
    expect(governRouteStep).toBeTruthy()
    await governRouteStep!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.drawer').text()).toContain('ai_route_step')
    expect(wrapper.find('.drawer').text()).toContain('审核流程')
    const motionMeter = wrapper.findAll('.ai-motion-meter').find((item) => item.text().includes('待处理压力'))
    expect(motionMeter).toBeTruthy()
    await motionMeter!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.drawer').text()).toContain('ai_motion_meter')
    expect(wrapper.find('.drawer').text()).toContain('待处理压力')
    const sideAction = wrapper.findAll('.ai-side-action').find((item) => item.text().includes('待生成知识内容'))
    expect(sideAction).toBeTruthy()
    await sideAction!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.drawer').text()).toContain('链接下滑结果复核')
    expect(wrapper.find('.operation-stream').exists()).toBe(true)
    expect(wrapper.text()).toContain('运行记录')
    expect(wrapper.text()).toContain('血缘指示')
    expect(wrapper.text()).toContain('自动生成知识')
    expect(wrapper.findAll('.stream-signal').length).toBeGreaterThan(0)
    expect(wrapper.findAll('.material-token').length).toBeGreaterThan(0)

    expect(wrapper.findAll('.flow-stage')).toHaveLength(6)
    expect(wrapper.find('.flow-loop-track').exists()).toBe(true)
    expect(wrapper.findAll('.flow-loop-packet')).toHaveLength(2)
    expect(wrapper.findAll('.flow-loop-count').length).toBeGreaterThan(0)
    expect(wrapper.findAll('.flow-loop-label').length).toBeGreaterThan(0)
    expect(wrapper.text()).toContain('轨道方向=来源 → 事件 → 资产 → 生成知识 → 建议 → 审核流程')
    expect(wrapper.text()).toContain('数据处理流程')
    expect(wrapper.text()).toContain('数据源')
    expect(wrapper.text()).toContain('改进建议 · 3')
    expect(wrapper.text()).toContain('审核流程')
    expect(wrapper.text()).toContain('自动处理引擎')
    expect(wrapper.text()).toContain('待生成知识')
    expect(wrapper.text()).toContain('治理待办')
    expect(wrapper.text()).toContain('最近自动处理')
    expect(wrapper.findAll('.automation-signal')).toHaveLength(5)
    expect(wrapper.find('.manifest-beam').exists()).toBe(true)
    expect(wrapper.findAll('.manifest-skill').length).toBeGreaterThan(0)
    expect(wrapper.text()).toContain('待处理问题')
    expect(wrapper.text()).toContain('扫描=定时检测阻塞')
    expect(wrapper.find('.bottleneck-radar').exists()).toBe(true)
    expect(wrapper.findAll('.bottleneck-radar-node')).toHaveLength(2)
    expect(wrapper.text()).toContain('处理记录')
    expect(wrapper.text()).toContain('进度光带=这条事实已走到哪一步')
    expect(wrapper.find('.journey-flow-map').exists()).toBe(true)
    expect(wrapper.findAll('.journey-step-node').length).toBeGreaterThan(0)
    expect(wrapper.find('.journey-cursor').exists()).toBe(true)
    expect(wrapper.text()).toContain('Agent控制')
    expect(wrapper.text()).toContain('反馈回传')
    expect(wrapper.text()).toContain('模型部署')
    expect(wrapper.text()).toContain('决策输出')
    expect(wrapper.text()).toContain('运行 Agent 控制')
    expect(wrapper.text()).toContain('钉钉反馈回传')
    expect(wrapper.text()).toContain('模型部署可用')
    expect(wrapper.text()).toContain('模型参与决策')
    expect(wrapper.text()).toContain('建议待审核')
    expect(wrapper.text()).toContain('待生成知识内容')
    expect(mocks.learningApi.pulse).toHaveBeenCalledWith({ days: 30, limit: 6 })
    expect(mocks.learningApi.home).toHaveBeenCalledWith({ days: 30 })
    expect(mocks.learningApi.flowJourneys).toHaveBeenCalledWith({ days: 30, limit: 8 })
    expect(mocks.learningApi.bottlenecks).toHaveBeenCalledWith({ days: 30, limit: 12 })
    expect(mocks.learningApi.automationStatus).not.toHaveBeenCalled()

    await wrapper.find('.flow-node').trigger('click')
    expect(wrapper.find('.drawer').text()).toContain('处理详情')
    expect(wrapper.find('.detail-json').text()).toContain('source:execution_run')
    expect(wrapper.find('.ai-side-context').text()).toContain('Skill 运行')
    const sideOpen = wrapper.findAll('.ai-side-context-actions button').find((item) => item.text() === '打开原页面')
    expect(sideOpen).toBeTruthy()
    await sideOpen!.trigger('click')
    expect(mocks.routerPush).toHaveBeenCalledWith('/learning-flow?source_type=execution_run')

    await wrapper.find('.detail-actions .tiny-btn.primary').trigger('click')
    expect(mocks.routerPush).toHaveBeenCalledWith('/learning-flow?source_type=execution_run')

    await wrapper.findAll('.flow-node')[1].trigger('click')
    await flushPromises()
    expect(wrapper.find('.ai-side-context').text()).toContain('decision.completed')
    const sideLineage = wrapper.findAll('.ai-side-context-actions button').find((item) => item.text() === '查看血缘')
    expect(sideLineage).toBeTruthy()
    await sideLineage!.trigger('click')
    await flushPromises()
    expect(mocks.learningApi.lineage).toHaveBeenCalledWith('run', 'run-learning-1', { limit: 60 })
    expect(wrapper.find('.ai-side-context').text()).toContain('血缘 1')
    expect(wrapper.text()).toContain('相关血缘')

    const quickActions = wrapper.findAll('.bottleneck-action')
    const materializeAction = quickActions.find((item) => item.text() === '生成知识')
    expect(materializeAction).toBeTruthy()
    await materializeAction!.trigger('click')
    await flushPromises()
    expect(mocks.learningApi.materializeArtifact).toHaveBeenCalledWith('a1', { force: true })

    const reviewAction = wrapper.findAll('.bottleneck-action').find((item) => item.text() === '进待办')
    expect(reviewAction).toBeTruthy()
    await reviewAction!.trigger('click')
    await flushPromises()
    expect(mocks.learningApi.createCandidateReview).toHaveBeenCalledWith('c1')

    const autoButton = wrapper.findAll('button').find((item) => item.text().includes('执行一次自动处理'))
    expect(autoButton).toBeTruthy()
    await autoButton!.trigger('click')
    await flushPromises()
    expect(mocks.learningApi.runAutomation).toHaveBeenCalledWith({ days: 30, limit: 300, materialize: true })

    const clearContext = wrapper.findAll('.ai-side-context-actions button').find((item) => item.text() === '清除')
    expect(clearContext).toBeTruthy()
    await clearContext!.trigger('click')
    expect(wrapper.find('.ai-side-context').exists()).toBe(false)
    expect(wrapper.find('.drawer').exists()).toBe(false)
    const clearTrail = wrapper.find('.ai-trail-clear')
    expect(clearTrail.exists()).toBe(true)
    await clearTrail.trigger('click')
    expect(wrapper.find('.ai-action-trail').exists()).toBe(false)
  })

  it('keeps the five-stage pulse frame visible while pulse data is loading', async () => {
    mocks.learningApi.pulse.mockReturnValue(new Promise(() => {}))
    const wrapper = mount(LearningFlow, { global: { stubs: stubs() } })
    await flushPromises()

    expect(wrapper.find('.pulse-chain-board').exists()).toBe(true)
    expect(wrapper.find('.pulse-chain-board').text()).toContain('原始数据、数据清洗、训练、模型测试、部署输出')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('原始数据')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('数据清洗')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('微调模型')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('模型测试')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('部署输出')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('数据库存储')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('向量分片')
    expect(wrapper.find('.pulse-chain-board').text()).toContain('数据库版本')
    expect(wrapper.findAll('.pulse-chain-card')).toHaveLength(5)
    expect(wrapper.findAll('.pulse-chain-card.is-loading')).toHaveLength(5)
  })

  it('applies URL filters and opens deep-linked artifact', async () => {
    mocks.routeQuery = {
      days: '7',
      source_type: 'sf_mcp_call',
      sf_tool: 'tmall_hot_tool',
      artifact_id: 'a1',
    }
    const wrapper = mount(LearningFlow, { global: { stubs: stubs() } })
    await flushPromises()

    expect(mocks.learningApi.home).toHaveBeenCalledWith(expect.objectContaining({
      days: 7,
      source_type: 'sf_mcp_call',
      sf_tool: 'tmall_hot_tool',
    }))
    expect(mocks.learningApi.pulse).toHaveBeenCalledWith(expect.objectContaining({
      days: 7,
      source_type: 'sf_mcp_call',
      sf_tool: 'tmall_hot_tool',
      limit: 6,
    }))
    expect(mocks.learningApi.flowTopology).not.toHaveBeenCalled()
    expect(mocks.learningApi.flowJourneys).toHaveBeenCalledWith(expect.objectContaining({
      days: 7,
      source_type: 'sf_mcp_call',
      sf_tool: 'tmall_hot_tool',
      limit: 8,
    }))
    expect(mocks.learningApi.bottlenecks).toHaveBeenCalledWith(expect.objectContaining({
      days: 7,
      source_type: 'sf_mcp_call',
      sf_tool: 'tmall_hot_tool',
      limit: 12,
    }))
    expect(wrapper.find('.drawer').text()).toContain('链接下滑结果复核')
  })

  it('opens AI root-cause inference from the side menu', async () => {
    const wrapper = mount(LearningFlow, { global: { stubs: stubs() } })
    await flushPromises()

    const governanceCause = wrapper.findAll('.ai-rootcause-card').find((item) => item.text().includes('审核未完成'))
    expect(governanceCause).toBeTruthy()
    await governanceCause!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.drawer').text()).toContain('ai_root_cause')
    expect(wrapper.find('.drawer').text()).toContain('审核未完成')
    expect(wrapper.find('.drawer').text()).toContain('建议、审核或待办未处理')
    expect(wrapper.find('.learning-page').classes()).toContain('motion-trace')
    expect(wrapper.find('.ai-action-trail').text()).toContain('审核未完成')
    expect(mocks.learningApi.candidates).toHaveBeenCalledWith(expect.objectContaining({ status: 'open' }))
  })

  it('opens AI impact forecast and applies expected focus', async () => {
    const wrapper = mount(LearningFlow, { global: { stubs: stubs() } })
    await flushPromises()

    const materializeImpact = wrapper.findAll('.ai-impact-card').find((item) => item.text().includes('知识生成收益'))
    expect(materializeImpact).toBeTruthy()
    await materializeImpact!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.drawer').text()).toContain('ai_impact_forecast')
    expect(wrapper.find('.drawer').text()).toContain('知识生成收益')
    expect(wrapper.find('.drawer').text()).toContain('预计转入知识')
    expect(wrapper.find('.ai-action-trail').text()).toContain('知识生成收益')
    expect(mocks.learningApi.artifacts).toHaveBeenCalledWith(expect.objectContaining({ status: 'ready' }))
  })

  it('prefers an inference-ready deployment for the fifth-step model chat', async () => {
    mocks.learningApi.pulse.mockResolvedValue(deploymentPulseResponse([
      {
        title: '旧 EC runtime 测试模型',
        status: 'active',
        source: 'training_job:job-old',
        destination: 'skill-old',
        entity_type: 'model_deployment',
        entity_id: 'deploy-old',
        output_preview: { deployment_id: 'deploy-old', status: 'active' },
        metadata: {
          chat_context: {
            ready: false,
            inference_ready: false,
            disabled_reason: '训练后模型推理节点 node-runtime-model-old 不存在或未启用',
            inference_disabled_reason: '训练后模型推理节点 node-runtime-model-old 不存在或未启用',
            model_deployment_id: 'deploy-old',
            model_family: 'lora',
            training_job_id: 'job-old',
            department: 'EC',
            artifact_id: 'artifact-old',
            deployment_status: 'active',
            target_gateway_id: 'node-runtime-model-old',
          },
        },
      },
      {
        title: '低消耗视频分析模型',
        status: 'active',
        source: 'training_job:job-live',
        destination: 'samplebrand-video-low-consumption-operator-v1',
        entity_type: 'model_deployment',
        entity_id: 'deploy-live',
        output_preview: { deployment_id: 'deploy-live', status: 'active' },
        metadata: {
          chat_context: {
            ready: true,
            inference_ready: true,
            model_deployment_id: 'deploy-live',
            model_family: 'samplebrand-video-low-consumption-operator-v1:qwen3.5-4b-qlora',
            training_job_id: 'job-live',
            department: '销售二部',
            artifact_id: 'artifact-live',
            artifact_uri_present: true,
            deployment_status: 'active',
            target_gateway_id: '内容电商',
            target_gateway_kind: 'openclaw',
            target_gateway_active: true,
          },
        },
      },
    ]))

    const wrapper = mount(LearningFlow, { global: { stubs: stubs() } })
    await flushPromises()

    await wrapper.findAll('.pulse-chain-card .pulse-chain-main')[4].trigger('click')
    await wrapper.findAll('button').find(item => item.text().includes('对话模型'))?.trigger('click')
    await flushPromises()

    const modalText = wrapper.find('.deployment-chat-modal').text()
    expect(modalText).toContain('deploy-live')
    expect(modalText).toContain('内容电商')
    expect(modalText).not.toContain('node-runtime-model-old')
  })

  it('does not select an inactive runtime-model deployment as the fifth-step chat target', async () => {
    mocks.learningApi.pulse.mockResolvedValue(deploymentPulseResponse([
      {
        title: '传统电商旧训练模型',
        status: 'active',
        source: 'training_job:job-462170bc',
        destination: 'skill-old',
        entity_type: 'model_deployment',
        entity_id: 'deploy-462170bc',
        output_preview: { deployment_id: 'deploy-462170bc', status: 'active' },
        metadata: {
          chat_context: {
            ready: false,
            inference_ready: false,
            disabled_reason: '训练后模型推理节点 node-runtime-model-462170bc 不存在或未启用',
            inference_disabled_reason: '训练后模型推理节点 node-runtime-model-462170bc 不存在或未启用',
            model_deployment_id: 'deploy-462170bc',
            model_family: 'lora',
            training_job_id: 'job-462170bc',
            department: 'EC',
            artifact_id: 'artifact-old',
            deployment_status: 'active',
            target_gateway_id: 'node-runtime-model-462170bc',
          },
        },
      },
    ]))

    const wrapper = mount(LearningFlow, { global: { stubs: stubs() } })
    await flushPromises()

    await wrapper.findAll('.pulse-chain-card .pulse-chain-main')[4].trigger('click')
    await wrapper.findAll('button').find(item => item.text().includes('对话模型'))?.trigger('click')
    await flushPromises()

    const modalText = wrapper.find('.deployment-chat-modal').text()
    expect(modalText).toContain('当前第五步还没有训练后模型部署')
    expect(modalText).not.toContain('deploy-462170bc')
    expect(modalText).not.toContain('node-runtime-model-462170bc')
  })

  it('does not infer fifth-step chat readiness from active deployment rows without chat context', async () => {
    mocks.learningApi.pulse.mockResolvedValue(deploymentPulseResponse([
      {
        title: '占位训练模型',
        status: 'active',
        source: 'training_job:job-placeholder',
        destination: 'skill-runtime-model-placeholder',
        entity_type: 'model_deployment',
        entity_id: 'deploy-placeholder',
        input_preview: {
          model_deployment_id: 'deploy-placeholder',
          training_job_id: 'job-placeholder',
          artifact_id: 'artifact-placeholder',
          target_gateway_id: 'node-runtime-model-placeholder',
        },
        output_preview: {
          deployment_id: 'deploy-placeholder',
          model_deployment_id: 'deploy-placeholder',
          status: 'active',
        },
        metadata: {},
      },
    ]))

    const wrapper = mount(LearningFlow, { global: { stubs: stubs() } })
    await flushPromises()

    await wrapper.findAll('.pulse-chain-card .pulse-chain-main')[4].trigger('click')
    await wrapper.findAll('button').find(item => item.text().includes('对话模型'))?.trigger('click')
    await flushPromises()

    const modalText = wrapper.find('.deployment-chat-modal').text()
    expect(modalText).toContain('当前第五步还没有训练后模型部署')
    expect(modalText).not.toContain('deploy-placeholder')
    expect(modalText).not.toContain('node-runtime-model-placeholder')
  })
})
