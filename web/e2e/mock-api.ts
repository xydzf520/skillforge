import type { Page, Route, WebSocketRoute } from '@playwright/test'

type PortalSkill = {
  id: string
  name: string
  display_name: string
  summary: string
  description: string
  category: string
  icon: string
  owner_name: string
  org_unit_name: string
  usage_count: number
  success_rate: number
  last_run_at: string
  tags: string[]
  status: string
  visibility: string
  param_ui_schema?: {
    type: 'object'
    required?: string[]
    properties?: Record<string, {
      type?: string
      format?: string
      title?: string
      description?: string
      default?: unknown
      enum?: Array<string | number>
    }>
  }
  result_ui_schema?: {
    type: 'table' | 'text'
    columns?: Array<{ key: string; title: string; format?: string }>
  }
  recent_runs?: Array<{
    id: string
    status: string
    created_at: string
    requester_name: string
  }>
}

type Submission = {
  id: string
  skill_id: string
  skill_display_name: string
  requester_id: string
  requester_name: string
  org_unit_id: string
  org_unit_name: string
  status: string
  params: Record<string, unknown>
  result_summary?: {
    type: 'table' | 'text'
    data?: Array<Record<string, unknown>>
    analysis?: string
    text?: string
    content?: string
  }
  result_ui_schema?: {
    type: 'table' | 'text'
    columns?: Array<{ key: string; title: string; format?: string }>
  }
  execution_id: string
  created_at: string
  completed_at?: string
  error_message?: string
}

type PlaybookStep = {
  id: string
  name?: string
  skill_id?: string
  skill?: string
  depends_on?: string[] | string
  timeout?: number
  on_failure?: string
  params_override?: Record<string, unknown>
  output_fields?: string[]
}

type Playbook = {
  file_name: string
  name: string
  description: string
  department: string
  summary?: string
  owner_name?: string
  trigger?: {
    type?: string
    schedule?: string
  }
  steps: PlaybookStep[]
  sla_minutes?: number
  updated_at?: string
  status?: string
  _canvas_layout?: Record<string, unknown>
  raw_config?: string
}

type PlaybookValidationIssue = {
  level: 'error' | 'warning'
  title: string
  message: string
  step_id?: string
}

type PlaybookRunStep = {
  id: string
  step_id: string
  step_order: number
  skill_id: string
  status: string
  started_at?: string
  completed_at?: string
  duration_ms?: number
  error_message?: string
}

type PlaybookRunDecision = {
  id: string
  skill_id: string
  suggested_action: string
  approval_status: string
  approval_level: number
}

type PlaybookRun = {
  id: string
  run_id: string
  playbook_id: string
  trigger_type: string
  status: string
  status_label: string
  total_steps: number
  completed_steps: number
  started_at: string
  completed_at?: string
  summary?: string
  steps: PlaybookRunStep[]
  decisions: PlaybookRunDecision[]
  websocket_events: Array<Record<string, unknown>>
}

type ReviewRecord = {
  id: number
  skill_id: string
  submitter: string
  reviewer: string | null
  change_type: string
  diff_summary: string
  reason: string
  status: 'pending' | 'approved' | 'rejected'
  created_at: string
  decided_at?: string | null
  diff_text?: string
  comments?: Array<Record<string, unknown>>
}

type OrgUnit = {
  id: string
  name: string
  type: string
  parent_id: string | null
  member_count: number
  children?: OrgUnit[]
}

type OrgMember = {
  user_id: string
  username: string
  name: string
  membership_type: string
  is_manager: boolean
  joined_at: string
}

type MockState = {
  user: Record<string, unknown>
  skills: PortalSkill[]
  playbooks: Playbook[]
  playbookRuns: Record<string, PlaybookRun>
  reviews: ReviewRecord[]
  templates: Array<Record<string, unknown>>
  submissions: Submission[]
  orgTree: OrgUnit[]
  membersByOrg: Record<string, OrgMember[]>
  nextSubmissionId: number
  nextOrgId: number
  nextPlaybookRunId: number
  nextReviewId: number
}

function fillListResponse<T>(items: T[]): { items: T[]; total: number } {
  return { items, total: items.length }
}

function jsonBody(body: unknown): string {
  return JSON.stringify(body)
}

function parseBody(raw: string | null): Record<string, any> {
  if (!raw) return {}
  try {
    return JSON.parse(raw) as Record<string, any>
  } catch {
    return {}
  }
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function createState(): MockState {
  const now = '2026-04-13T08:00:00.000Z'
  return {
    user: {
      user_id: 'u-admin',
      username: 'admin',
      name: '管理员',
      role: 'admin',
      department: '总部',
      can_view_all: true,
      must_change_password: false,
      avatar_url: 'https://example.com/avatar.png',
    },
    skills: [
      {
        id: 'skill-forecast',
        name: 'inventory_forecast',
        display_name: '库存优化助手',
        summary: '根据最近订单生成补货建议',
        description: '面向运营同学的库存优化建议 Skill。',
        category: '运营',
        icon: 'bar-chart',
        owner_name: '王敏',
        org_unit_name: '供应链中心',
        usage_count: 128,
        success_rate: 0.94,
        last_run_at: '2026-04-12T08:00:00.000Z',
        tags: ['库存', '补货'],
        status: 'active',
        visibility: 'department',
        param_ui_schema: {
          type: 'object',
          required: ['keywords'],
          properties: {
            keywords: {
              type: 'string',
              title: '关键词',
              description: '输入需要分析的关键词',
              default: '补货分析',
            },
            report_date: {
              type: 'string',
              format: 'date',
              title: '报表日期',
              default: 'yesterday',
            },
            retries: {
              type: 'integer',
              title: '重试次数',
              default: 2,
            },
            include_analysis: {
              type: 'boolean',
              title: '包含分析',
              default: false,
            },
            scope: {
              type: 'string',
              title: '范围',
              enum: ['全量', '重点商品'],
            },
            tags: {
              type: 'array',
              title: '标签列表',
              format: 'textarea',
            },
          },
        },
        result_ui_schema: {
          type: 'table',
          columns: [
            { key: 'recommendation', title: '推荐补货量' },
            { key: 'reason', title: '建议原因' },
          ],
        },
        recent_runs: [
          {
            id: 'submission-history-1',
            status: 'completed',
            created_at: now,
            requester_name: '李想',
          },
        ],
      },
      {
        id: 'skill-summary',
        name: 'meeting_summary',
        display_name: '会议纪要整理',
        summary: '生成会议纪要和待办',
        description: '把会议记录整理成结构化纪要。',
        category: '协作',
        icon: 'file',
        owner_name: '赵蕾',
        org_unit_name: '协同平台部',
        usage_count: 56,
        success_rate: 0.87,
        last_run_at: '2026-04-11T13:30:00.000Z',
        tags: ['纪要', '待办'],
        status: 'active',
        visibility: 'company',
      },
    ],
    playbooks: [
      {
        file_name: 'replenishment-check',
        name: '补货巡检',
        description: '串联库存分析与纪要沉淀，形成补货巡检闭环。',
        department: '供应链中心',
        summary: '每天汇总库存风险并生成补货建议给值班同学。',
        owner_name: '王敏',
        trigger: {
          type: 'schedule',
          schedule: '每天 09:00',
        },
        sla_minutes: 45,
        updated_at: '2026-04-13T07:40:00.000Z',
        status: 'draft',
        steps: [
          {
            id: 'collect',
            name: '收集库存数据',
            skill_id: 'skill-forecast',
            timeout: 300,
            on_failure: 'terminate',
            output_fields: ['inventory_gap', 'risk_items'],
          },
          {
            id: 'summarize',
            name: '生成巡检纪要',
            skill_id: 'skill-summary',
            depends_on: ['collect'],
            timeout: 240,
            on_failure: 'continue',
            params_override: { include_actions: true },
            output_fields: ['summary', 'actions'],
          },
        ],
        _canvas_layout: {
          nodes: {
            collect: { x: 160, y: 180 },
            summarize: { x: 460, y: 180 },
          },
          viewport: { x: 0, y: 0, zoom: 0.92 },
        },
        raw_config: [
          'name: 补货巡检',
          'department: 供应链中心',
          'trigger:',
          '  type: schedule',
          '  schedule: 每天 09:00',
          'steps:',
          '  - id: collect',
          '    skill_id: skill-forecast',
          '  - id: summarize',
          '    skill_id: skill-summary',
          '    depends_on:',
          '      - collect',
        ].join('\n'),
      },
    ],
    playbookRuns: {
      'run-pb-seeded-1': {
        id: 'run-pb-seeded-1',
        run_id: 'run-pb-seeded-1',
        playbook_id: 'replenishment-check',
        trigger_type: 'manual:u-admin',
        status: 'completed',
        status_label: '已完成',
        total_steps: 2,
        completed_steps: 2,
        started_at: '2026-04-13T07:30:00.000Z',
        completed_at: '2026-04-13T07:31:10.000Z',
        summary: '最近一次巡检已完成，补货建议已生成。',
        steps: [
          {
            id: 'run-pb-seeded-1-step-1',
            step_id: 'collect',
            step_order: 1,
            skill_id: 'skill-forecast',
            status: 'completed',
            started_at: '2026-04-13T07:30:00.000Z',
            completed_at: '2026-04-13T07:30:42.000Z',
            duration_ms: 42000,
          },
          {
            id: 'run-pb-seeded-1-step-2',
            step_id: 'summarize',
            step_order: 2,
            skill_id: 'skill-summary',
            status: 'completed',
            started_at: '2026-04-13T07:30:44.000Z',
            completed_at: '2026-04-13T07:31:10.000Z',
            duration_ms: 26000,
          },
        ],
        decisions: [],
        websocket_events: [],
      },
    },
    reviews: [
      {
        id: 301,
        skill_id: 'playbook:replenishment-check',
        submitter: 'u-admin',
        reviewer: 'u-admin',
        change_type: 'new_skill',
        diff_summary: 'Playbook replenishment-check: 2 个步骤',
        reason: 'Playbook [补货巡检] 提交审核',
        status: 'pending',
        created_at: now,
        decided_at: null,
        diff_text: 'graph TD\n  collect --> summarize',
        comments: [],
      },
    ],
    templates: [
      {
        id: 'tpl-roi',
        name: 'ec-roi-check',
        display_name: 'ROI 投放检查',
        description: '每日检查投放 ROI，自动判断加预算/暂停/维持。',
        department: 'EC',
        category: '投放优化',
        trigger_type: 'manual',
        risk_level: 'R2',
        steps_count: 2,
        test_cases_count: 3,
        tags: ['投放', 'ROI'],
        files: ['SKILL.md'],
        skill_md: '# ROI 投放检查',
      },
    ],
    submissions: [
      {
        id: 'submission-overview-1',
        skill_id: 'skill-summary',
        skill_display_name: '会议纪要整理',
        requester_id: 'u-li',
        requester_name: '李想',
        org_unit_id: 'org-root',
        org_unit_name: '总部',
        status: 'completed',
        params: {
          topic: '周例会',
          include_actions: true,
        },
        result_summary: {
          type: 'table',
          data: [
            { recommendation: '补充 120 件', reason: '库存低于安全线' },
          ],
          analysis: '已生成 1 条建议',
        },
        result_ui_schema: {
          type: 'table',
          columns: [
            { key: 'recommendation', title: '推荐补货量' },
            { key: 'reason', title: '建议原因' },
          ],
        },
        execution_id: 'exec-overview-1',
        created_at: '2026-04-13T07:50:00.000Z',
        completed_at: '2026-04-13T07:50:18.000Z',
      },
    ],
    orgTree: [
      {
        id: 'org-root',
        name: '总部',
        type: 'department',
        parent_id: null,
        member_count: 2,
        children: [
          {
            id: 'org-supply',
            name: '供应链中心',
            type: 'department',
            parent_id: 'org-root',
            member_count: 1,
            children: [],
          },
        ],
      },
    ],
    membersByOrg: {
      'org-root': [
        {
          user_id: 'u-admin',
          username: 'admin',
          name: '管理员',
          membership_type: 'primary',
          is_manager: true,
          joined_at: now,
        },
      ],
      'org-supply': [
        {
          user_id: 'u-li',
          username: 'lili',
          name: '李莉',
          membership_type: 'secondary',
          is_manager: false,
          joined_at: now,
        },
      ],
    },
    nextSubmissionId: 2,
    nextOrgId: 2,
    nextPlaybookRunId: 2,
    nextReviewId: 302,
  }
}

function findOrgUnit(nodes: OrgUnit[], id: string): OrgUnit | null {
  for (const node of nodes) {
    if (node.id === id) return node
    const found = node.children?.length ? findOrgUnit(node.children, id) : null
    if (found) return found
  }
  return null
}

function addOrgUnit(nodes: OrgUnit[], parentId: string | null, unit: OrgUnit): boolean {
  if (!parentId) {
    nodes.push(unit)
    return true
  }
  for (const node of nodes) {
    if (node.id === parentId) {
      node.children ||= []
      node.children.push(unit)
      return true
    }
    if (node.children?.length && addOrgUnit(node.children, parentId, unit)) {
      return true
    }
  }
  return false
}

function updateOrgMemberCount(nodes: OrgUnit[], orgId: string, memberCount: number): boolean {
  const node = findOrgUnit(nodes, orgId)
  if (!node) return false
  node.member_count = memberCount
  return true
}

function buildOverview(state: MockState) {
  const submissions = [...state.submissions].sort((a, b) => b.created_at.localeCompare(a.created_at))
  return {
    org_unit: { id: 'org-root', name: '总部' },
    stats: {
      total_skills: state.skills.length,
      active_skills: state.skills.length,
      today_runs: state.submissions.length,
      success_rate_7d: 0.92,
      total_runs_7d: state.submissions.length,
    },
    recent_submissions: submissions.slice(0, 5),
  }
}

function listPortalSkills(state: MockState, url: URL) {
  const search = (url.searchParams.get('search') || url.searchParams.get('q') || '').trim().toLowerCase()
  const category = (url.searchParams.get('category') || '').trim().toLowerCase()
  const page = Math.max(1, Number(url.searchParams.get('page') || '1'))
  const pageSize = Math.max(1, Number(url.searchParams.get('page_size') || '12'))
  const filtered = state.skills.filter((skill) => {
    const searchable = [
      skill.display_name,
      skill.name,
      skill.summary,
      skill.category,
      ...(skill.tags || []),
    ].join(' ').toLowerCase()
    const matchesSearch = !search || searchable.includes(search)
    const matchesCategory = !category || skill.category.toLowerCase() === category
    return matchesSearch && matchesCategory
  })
  const start = (page - 1) * pageSize
  return {
    items: filtered.slice(start, start + pageSize),
    total: filtered.length,
    page,
    page_size: pageSize,
  }
}

function createSubmission(state: MockState, skill: PortalSkill, params: Record<string, unknown>): Submission {
  const id = `submission-forecast-${state.nextSubmissionId++}`
  const createdAt = `2026-04-13T08:0${state.nextSubmissionId}:00.000Z`
  const submission: Submission = {
    id,
    skill_id: skill.id,
    skill_display_name: skill.display_name,
    requester_id: 'u-admin',
    requester_name: '管理员',
    org_unit_id: 'org-root',
    org_unit_name: '总部',
    status: 'completed',
    params: clone(params),
    result_summary: {
      type: 'table',
      data: [
        { recommendation: '补货 120 件', reason: '最近 7 天需求增长 18%' },
      ],
      analysis: '已根据库存阈值生成建议',
    },
    result_ui_schema: {
      type: 'table',
      columns: [
        { key: 'recommendation', title: '推荐补货量' },
        { key: 'reason', title: '建议原因' },
      ],
    },
    execution_id: `exec-${id}`,
    created_at: createdAt,
    completed_at: createdAt,
  }
  state.submissions.unshift(submission)
  skill.recent_runs = [
    {
      id,
      status: 'completed',
      created_at: createdAt,
      requester_name: '管理员',
    },
    ...(skill.recent_runs || []),
  ]
  return submission
}

function findPlaybook(state: MockState, name: string): Playbook | undefined {
  return state.playbooks.find((item) => item.file_name === name || item.name === name)
}

function normalizeDependsOn(step: PlaybookStep): string[] {
  if (!step.depends_on) return []
  return Array.isArray(step.depends_on) ? step.depends_on : [step.depends_on]
}

function buildPlaybookMermaid(playbook: Playbook): string {
  if (!playbook.steps.length) return 'graph TD\n  start[开始]'
  const lines = ['graph TD']
  for (const step of playbook.steps) {
    const stepLabel = step.name || step.id
    lines.push(`  ${step.id}[${stepLabel}]`)
    const deps = normalizeDependsOn(step)
    if (!deps.length) continue
    for (const dep of deps) {
      lines.push(`  ${dep} --> ${step.id}`)
    }
  }
  return lines.join('\n')
}

function latestPlaybookReview(state: MockState, name: string): ReviewRecord | null {
  return state.reviews.find((item) => item.skill_id === `playbook:${name}`) || null
}

function latestPlaybookRun(state: MockState, name: string): PlaybookRun | null {
  const runs = Object.values(state.playbookRuns)
    .filter((item) => item.playbook_id === name)
    .sort((a, b) => b.started_at.localeCompare(a.started_at))
  return runs[0] || null
}

function validatePlaybookDocument(playbook: Partial<Playbook>) {
  const issues: PlaybookValidationIssue[] = []
  const steps = Array.isArray(playbook.steps) ? playbook.steps : []

  if (!playbook.name) {
    issues.push({
      level: 'error',
      title: '流程缺少名称',
      message: '请先补充流程名称，再进行保存或提交审核。',
    })
  }

  if (!steps.length) {
    issues.push({
      level: 'error',
      title: '流程还没有步骤',
      message: '从左侧拖入 Skill 开始搭建，或添加一个空节点。',
    })
  }

  const stepIds = new Set<string>()
  for (let index = 0; index < steps.length; index += 1) {
    const step = steps[index]
    const stepId = String(step?.id || '').trim()
    if (!stepId) {
      issues.push({
        level: 'error',
        title: `步骤 ${index + 1} 缺少编号`,
        message: '每个步骤都需要唯一的 step id，方便问题定位和运行监控。',
      })
      continue
    }

    if (stepIds.has(stepId)) {
      issues.push({
        level: 'error',
        title: `步骤 ${stepId} 重复`,
        message: '步骤编号需要保持唯一，请修改重复的 step id。',
        step_id: stepId,
      })
    }
    stepIds.add(stepId)

    const skillId = String(step.skill_id || step.skill || '').trim()
    if (!skillId) {
      issues.push({
        level: 'error',
        title: `步骤 ${stepId} 未配置 Skill`,
        message: '请为该步骤绑定一个 Skill，避免运行时停在空节点。',
        step_id: stepId,
      })
    }
  }

  for (const step of steps) {
    const stepId = String(step?.id || '').trim()
    for (const dep of normalizeDependsOn(step)) {
      if (!stepIds.has(dep)) {
        issues.push({
          level: 'error',
          title: `步骤 ${stepId || '未命名步骤'} 依赖不存在`,
          message: `依赖步骤 ${dep} 不存在，请检查分支或依赖链路。`,
          step_id: stepId || undefined,
        })
      }
    }
  }

  const visiting = new Set<string>()
  const visited = new Set<string>()
  const stepsById = new Map(steps.map((step) => [step.id, step]))

  function visit(stepId: string): boolean {
    if (visited.has(stepId)) return false
    if (visiting.has(stepId)) return true
    visiting.add(stepId)
    const step = stepsById.get(stepId)
    for (const dep of step ? normalizeDependsOn(step) : []) {
      if (!stepsById.has(dep)) continue
      if (visit(dep)) return true
    }
    visiting.delete(stepId)
    visited.add(stepId)
    return false
  }

  if (steps.some((step) => step.id && visit(step.id))) {
    issues.push({
      level: 'error',
      title: '流程存在循环依赖',
      message: '请检查步骤依赖关系，确保流程可以从起点顺序执行到终点。',
    })
  }

  const errors = issues.filter((item) => item.level === 'error')
  return {
    valid: errors.length === 0,
    errors: errors.map((item) => item.title),
    issues,
    checked_scope: 'draft',
    summary: {
      total: issues.length,
      error_count: errors.length,
      warning_count: issues.length - errors.length,
    },
    message: errors.length === 0
      ? '当前没有发现结构问题。可以保存，或直接提交审核。'
      : `发现 ${errors.length} 个问题，请先处理后再继续。`,
  }
}

function buildPlaybookSummary(state: MockState, playbook: Playbook) {
  const latestReview = latestPlaybookReview(state, playbook.file_name)
  const latestRun = latestPlaybookRun(state, playbook.file_name)
  const validation = validatePlaybookDocument(playbook)
  return {
    name: playbook.name,
    file_name: playbook.file_name,
    description: playbook.description,
    department: playbook.department,
    owner_name: playbook.owner_name || '管理员',
    trigger: playbook.trigger || { type: 'manual', schedule: '手动触发' },
    step_count: playbook.steps.length,
    sla_minutes: playbook.sla_minutes || null,
    status: latestReview?.status || playbook.status || 'draft',
    updated_at: playbook.updated_at || null,
    has_unsaved_changes: false,
    validation,
    review: latestReview
      ? {
          id: latestReview.id,
          status: latestReview.status,
          reviewer: latestReview.reviewer,
          diff_summary: latestReview.diff_summary,
          reason: latestReview.reason,
          created_at: latestReview.created_at,
        }
      : null,
    latest_run: latestRun
      ? {
          run_id: latestRun.run_id,
          status: latestRun.status,
          status_label: latestRun.status_label,
          total_steps: latestRun.total_steps,
          completed_steps: latestRun.completed_steps,
          started_at: latestRun.started_at,
          completed_at: latestRun.completed_at,
          summary: latestRun.summary,
        }
      : null,
    advanced: {
      text_flowchart: buildPlaybookMermaid(playbook),
      raw_config: playbook.raw_config || jsonBody(playbook),
    },
  }
}

function buildPlaybookRunEvents(run: PlaybookRun) {
  const events: Array<Record<string, unknown>> = []
  for (const step of run.steps) {
    events.push({
      type: 'step_status',
      run_id: run.run_id,
      step_id: step.step_id,
      status: 'running',
      status_label: '执行中',
      message: `${step.step_id} 开始执行`,
    })
    events.push({
      type: 'step_status',
      run_id: run.run_id,
      step_id: step.step_id,
      status: step.status,
      status_label: step.status === 'completed' ? '已完成' : step.status,
      message: `${step.step_id} ${step.status === 'completed' ? '执行完成' : '执行结束'}`,
    })
  }
  events.push({
    type: 'run_complete',
    run_id: run.run_id,
    status: run.status,
    status_label: run.status_label,
    completed_steps: run.completed_steps,
    total_steps: run.total_steps,
    summary: run.summary,
  })
  return events
}

function createPlaybookRun(state: MockState, playbook: Playbook) {
  const runId = `run-pb-${state.nextPlaybookRunId++}`
  const startedAt = `2026-04-13T08:${String(state.nextPlaybookRunId).padStart(2, '0')}:00.000Z`
  const steps: PlaybookRunStep[] = playbook.steps.map((step, index) => ({
    id: `${runId}-step-${index + 1}`,
    step_id: step.id,
    step_order: index + 1,
    skill_id: step.skill_id || step.skill || `skill-${index + 1}`,
    status: 'completed',
    started_at: startedAt,
    completed_at: startedAt,
    duration_ms: 18000 + index * 6000,
  }))
  const run: PlaybookRun = {
    id: runId,
    run_id: runId,
    playbook_id: playbook.file_name,
    trigger_type: 'manual:u-admin',
    status: 'running',
    status_label: '执行中',
    total_steps: steps.length,
    completed_steps: 0,
    started_at: startedAt,
    summary: '本次巡检正在执行，请在执行监控中查看节点状态和日志。',
    steps,
    decisions: [],
    websocket_events: [],
  }
  run.websocket_events = buildPlaybookRunEvents({
    ...run,
    status: 'completed',
    status_label: '已完成',
    completed_steps: steps.length,
    completed_at: startedAt,
    summary: '本次巡检已完成，所有步骤均已执行成功。',
  })
  state.playbookRuns[runId] = run
  setTimeout(() => {
    const latest = state.playbookRuns[runId]
    if (!latest) return
    latest.status = 'completed'
    latest.status_label = '已完成'
    latest.completed_steps = latest.total_steps
    latest.completed_at = latest.started_at
    latest.summary = '本次巡检已完成，所有步骤均已执行成功。'
  }, 400)
  return run
}

function createReview(
  state: MockState,
  payload: Record<string, unknown>,
  overrides: Partial<ReviewRecord> = {},
): ReviewRecord {
  const review: ReviewRecord = {
    id: state.nextReviewId++,
    skill_id: String(payload.skill_id || ''),
    submitter: 'u-admin',
    reviewer: 'u-admin',
    change_type: String(payload.change_type || 'update'),
    diff_summary: String(payload.diff_summary || ''),
    reason: String(payload.reason || ''),
    status: 'pending',
    created_at: '2026-04-13T08:15:00.000Z',
    decided_at: null,
    diff_text: String(payload.diff_summary || ''),
    comments: [],
    ...overrides,
  }
  state.reviews.unshift(review)
  if (review.skill_id.startsWith('playbook:')) {
    const playbook = findPlaybook(state, review.skill_id.slice('playbook:'.length))
    if (playbook) playbook.status = 'pending'
  }
  return review
}

function listReviews(state: MockState, url: URL) {
  const status = (url.searchParams.get('status') || '').trim()
  const skillId = (url.searchParams.get('skill_id') || '').trim()
  const submitter = (url.searchParams.get('submitter') || '').trim()
  const reviewer = (url.searchParams.get('reviewer') || '').trim()
  const page = Math.max(1, Number(url.searchParams.get('page') || '1'))
  const pageSize = Math.max(1, Number(url.searchParams.get('page_size') || '20'))
  const items = state.reviews.filter((item) => {
    if (status && item.status !== status) return false
    if (skillId && item.skill_id !== skillId) return false
    if (submitter && item.submitter !== submitter && submitter !== 'me') return false
    if (reviewer && item.reviewer !== reviewer && reviewer !== 'me') return false
    return true
  })
  const start = (page - 1) * pageSize
  return {
    items: clone(items.slice(start, start + pageSize)),
    total: items.length,
    page,
    page_size: pageSize,
  }
}

function approveReview(state: MockState, reviewId: number) {
  const review = state.reviews.find((item) => item.id === reviewId)
  if (!review) return null
  review.status = 'approved'
  review.decided_at = '2026-04-13T08:20:00.000Z'
  if (review.skill_id.startsWith('playbook:')) {
    const playbook = findPlaybook(state, review.skill_id.slice('playbook:'.length))
    if (playbook) playbook.status = 'approved'
  }
  return {
    review_id: review.id,
    status: review.status,
    new_version: review.skill_id.startsWith('playbook:') ? 'v0.1' : 'v1.1',
  }
}

function rejectReview(state: MockState, reviewId: number, reason: string) {
  const review = state.reviews.find((item) => item.id === reviewId)
  if (!review) return null
  review.status = 'rejected'
  review.reason = reason || review.reason
  review.decided_at = '2026-04-13T08:21:00.000Z'
  if (review.skill_id.startsWith('playbook:')) {
    const playbook = findPlaybook(state, review.skill_id.slice('playbook:'.length))
    if (playbook) playbook.status = 'rejected'
  }
  return {
    review_id: review.id,
    status: review.status,
  }
}

function addMembership(state: MockState, body: Record<string, any>) {
  const orgId = String(body.org_unit_id || '')
  const userId = String(body.user_id || '')
  const member: OrgMember = {
    user_id: userId,
    username: userId,
    name: userId === 'zhangsan' ? '张三' : userId,
    membership_type: String(body.membership_type || 'secondary'),
    is_manager: Boolean(body.is_manager),
    joined_at: '2026-04-13T08:05:00.000Z',
  }
  state.membersByOrg[orgId] ||= []
  const nextMembers = state.membersByOrg[orgId].filter((item) => item.user_id !== userId)
  nextMembers.push(member)
  state.membersByOrg[orgId] = nextMembers
  updateOrgMemberCount(state.orgTree, orgId, nextMembers.length)
  return member
}

function removeMembership(state: MockState, orgId: string, userId: string) {
  const nextMembers = (state.membersByOrg[orgId] || []).filter((item) => item.user_id !== userId)
  state.membersByOrg[orgId] = nextMembers
  updateOrgMemberCount(state.orgTree, orgId, nextMembers.length)
  return { ok: true }
}

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json; charset=utf-8',
    body: jsonBody(body),
  })
}

function fulfillPlaybookLiveSocket(ws: WebSocketRoute, run: PlaybookRun | null) {
  if (!run) {
    setTimeout(() => {
      ws.send(jsonBody({
        type: 'run_complete',
        status: 'failed',
        status_label: '失败',
        completed_steps: 0,
        total_steps: 0,
        message: '未找到对应的运行记录',
      }))
      void ws.close({ code: 1000, reason: 'mock-finished' })
    }, 20)
    return
  }

  run.websocket_events.forEach((event, index) => {
    setTimeout(() => {
      ws.send(jsonBody(event))
      if (event.type === 'run_complete') {
        void ws.close({ code: 1000, reason: 'mock-finished' })
      }
    }, 80 * (index + 1))
  })
}

export async function installMockApi(page: Page): Promise<MockState> {
  const state = createState()

  await page.addInitScript(() => {
    localStorage.setItem('sf-theme', 'light')
    localStorage.removeItem('sf_recent_skills')
    localStorage.removeItem('sf-changelog-read')
  })

  await page.routeWebSocket(/\/api\/playbooks\/ws\/playbook-live\/.+$/, (ws) => {
    const url = new URL(ws.url())
    const runId = url.pathname.split('/').pop() || ''
    const run = state.playbookRuns[runId] || null
    fulfillPlaybookLiveSocket(ws, run)
  })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const pathname = url.pathname.replace(/^\/api/, '') || '/'
    if (!pathname.startsWith('/')) {
      await route.continue()
      return
    }
    if (!url.pathname.startsWith('/api/')) {
      await route.continue()
      return
    }
    const method = request.method().toUpperCase()
    const segments = pathname.split('/').filter(Boolean)
    const body = parseBody(request.postData())

    if (method === 'GET' && pathname === '/auth/me') {
      await fulfillJson(route, state.user)
      return
    }
    if (method === 'GET' && pathname === '/skills/motif-library') {
      await fulfillJson(route, { motifs: [] })
      return
    }
    if (method === 'GET' && pathname === '/skills/workbench/my-active-draft') {
      await fulfillJson(route, null)
      return
    }
    if (segments[0] === 'skills' && segments[1] === 'templates' && method === 'GET' && segments.length === 2) {
      await fulfillJson(route, fillListResponse(state.templates))
      return
    }
    if (segments[0] === 'skills' && segments[1] === 'templates' && method === 'GET' && segments.length === 3) {
      const template = state.templates.find((item) => item.id === segments[2])
      if (!template) {
        await fulfillJson(route, { detail: 'Template not found' }, 404)
        return
      }
      await fulfillJson(route, template)
      return
    }
    if (segments[0] === 'skills' && segments[1] === 'templates' && method === 'POST' && segments[3] === 'fork') {
      const template = state.templates.find((item) => item.id === segments[2])
      if (!template) {
        await fulfillJson(route, { detail: 'Template not found' }, 404)
        return
      }
      const skillId = body.skill_id || `fork-${segments[2]}`
      state.skills.unshift({
        id: skillId,
        name: skillId,
        display_name: String(template.display_name || template.name || skillId),
        summary: String(template.description || ''),
        description: String(template.description || ''),
        category: String(template.category || '通用'),
        icon: 'apps',
        owner_name: '管理员',
        org_unit_name: String(body.department || '总部'),
        usage_count: 0,
        success_rate: 1,
        last_run_at: '2026-04-13T08:00:00.000Z',
        tags: Array.isArray(template.tags) ? (template.tags as string[]) : [],
        status: 'draft',
        visibility: 'department',
      })
      await fulfillJson(route, { skill_id: skillId, template_id: segments[2], git_commit: 'mock-commit' })
      return
    }
    if (segments[0] === 'skills' && method === 'GET' && segments.length === 1) {
      await fulfillJson(route, fillListResponse(state.skills.map((skill) => ({
        id: skill.id,
        name: skill.name,
        display_name: skill.display_name,
        summary: skill.summary,
        status: skill.status,
        visibility: skill.visibility,
      }))))
      return
    }
    if (segments[0] === 'skills' && method === 'GET' && segments.length === 2) {
      const skill = state.skills.find((item) => item.id === segments[1])
      if (!skill) {
        await fulfillJson(route, { detail: 'Skill not found' }, 404)
        return
      }
      await fulfillJson(route, {
        id: skill.id,
        name: skill.name,
        display_name: skill.display_name,
        description: skill.description,
        summary: skill.summary,
        department: skill.org_unit_name,
        skill_md: '# mock skill',
        policy_pack: {},
        file_tree: [],
        parsed: { frontmatter: { name: skill.display_name }, purpose: skill.description, steps: [] },
        status: skill.status,
        visibility: skill.visibility,
      })
      return
    }
    if (segments[0] === 'skills' && method === 'GET' && segments[2] === 'lock') {
      await fulfillJson(route, { locked: false })
      return
    }
    if (segments[0] === 'skills' && method === 'GET' && segments[2] === 'history') {
      await fulfillJson(route, [])
      return
    }
    if (segments[0] === 'skills' && method === 'GET' && segments[2] === 'diff') {
      await fulfillJson(route, { diff: '' })
      return
    }
    if (segments[0] === 'skills' && method === 'POST' && segments[2] === 'validate-all') {
      await fulfillJson(route, {
        valid: true,
        blocks: [
          { key: 'meta', status: 'pass' },
          { key: 'goal', status: 'pass' },
        ],
      })
      return
    }
    if (segments[0] === 'skills' && method === 'POST' && segments[2] === 'publish-readiness') {
      await fulfillJson(route, {
        can_publish: true,
        blocker_count: 0,
        groups: [],
        items: [],
      })
      return
    }
    if (segments[0] === 'skills' && method === 'POST' && segments[2] === 'reviewer' && segments[3] === 'summarize') {
      await fulfillJson(route, {
        summary: 'mock reviewer summary',
        risks: [],
      })
      return
    }
    if (segments[0] === 'skills' && segments.length >= 3) {
      const tail = segments.slice(2).join('/')
      if (method === 'POST' && tail === 'coach/event') {
        await fulfillJson(route, { items: [] })
        return
      }
      if (method === 'GET' && tail === 'workbench/my-active-draft') {
        await fulfillJson(route, null)
        return
      }
      if (method === 'GET') {
        await fulfillJson(route, {})
        return
      }
      if (method === 'POST' || method === 'PUT' || method === 'PATCH' || method === 'DELETE') {
        await fulfillJson(route, { ok: true })
        return
      }
    }
    if (method === 'POST' && pathname === '/auth/logout') {
      await fulfillJson(route, { ok: true })
      return
    }
    if (method === 'GET' && pathname === '/changelog') {
      await fulfillJson(route, { versions: [{ version: '2026.04.13', title: 'Portal E2E coverage' }] })
      return
    }
    if (method === 'GET' && pathname === '/todos/stats') {
      await fulfillJson(route, { pending: 0, dispatch_pending: 0 })
      return
    }
    if (method === 'GET' && pathname === '/todos/skills/top') {
      await fulfillJson(route, {
        items: [
          { id: 'mock-skill', name: 'Mock Skill', count: 1 },
          { id: 'mock-skill-2', name: 'Mock Skill 2', count: 1 },
        ],
      })
      return
    }
    if (method === 'GET' && pathname === '/data-sources/platform-api-drift-alerts') {
      await fulfillJson(route, { total: 0, page: 1, page_size: Number(url.searchParams.get('page_size') || '20'), items: [] })
      return
    }
    if (method === 'GET' && pathname === '/notifications/') {
      await fulfillJson(route, {
        items: [],
        unread: 0,
        total: 0,
        page: 1,
        page_size: Number(url.searchParams.get('page_size') || '15'),
      })
      return
    }
    if (method === 'POST' && pathname === '/notifications/read-all') {
      await fulfillJson(route, { ok: true })
      return
    }
    if (method === 'POST' && /^\/notifications\/\d+\/read$/.test(pathname)) {
      await fulfillJson(route, { ok: true })
      return
    }

    // GAP-1/2/5/6/7/8/9/10/11/12 mocks（inbox-api-gaps.md）
    if (method === 'GET' && pathname === '/todos/') {
      await fulfillJson(route, {
        total: 2,
        page: 1,
        page_size: 20,
        items: [
          {
            id: 101,
            request_id: 'dr-mock-1',
            kind: 'review',
            assignee: 'admin',
            status: 'pending',
            title: 'Mock 审批待办',
            summary: '用于 e2e 断言 GAP-1 富字段',
            skill_id: 'mock-skill',
            decision_mode: 'any_of',
            aggregate_status: 'pending',
            sla_at: '2026-04-25T08:00:00',
            created_at: '2026-04-18T06:00:00',
            suggested_actions: ['下调预算 20%', '同部门复核后再执行'],
            metrics_preview: [
              { label: 'ROI', value: '1.6', trend: 'up', delta: '+8%' },
              { label: 'GMV', value: '120k', trend: 'down', delta: '-3%' },
            ],
            approval_level: 'L2',
            requester: { id: 'bob', name: 'Bob' },
            requester_department: '商务部',
            aggregate_progress: { total: 2, done: 1, waiting_on: ['admin'] },
          },
          {
            id: 102,
            request_id: 'dr-mock-2',
            kind: 'review',
            assignee: 'admin',
            status: 'pending',
            title: 'Mock 第二条',
            summary: '第二条摘要',
            skill_id: 'mock-skill-2',
            decision_mode: 'any_of',
            aggregate_status: 'pending',
            sla_at: '2026-04-26T08:00:00',
            created_at: '2026-04-18T07:00:00',
            suggested_actions: [],
            metrics_preview: [],
            approval_level: 'L1',
            requester: null,
            aggregate_progress: null,
          },
        ],
      })
      return
    }
    if (method === 'GET' && pathname === '/inbox/overview') {
      await fulfillJson(route, {
        pending: 2,
        overdue: 0,
        resolved_today: 3,
        sla_hit_rate: 0.833,
        avg_resolve_minutes: 42.5,
        backlog_by_age: [
          { bucket: '<1h', count: 0 },
          { bucket: '1-6h', count: 2 },
          { bucket: '6-24h', count: 0 },
          { bucket: '>24h', count: 0 },
        ],
        resolved_last_7d: 18,
        approved_last_7d: 15,
        rejected_last_7d: 3,
      })
      return
    }
    if (method === 'GET' && pathname === '/inbox/reports/unread-count') {
      await fulfillJson(route, { unread: 0, last_viewed_at: '2026-04-18T09:00:00' })
      return
    }
    if (method === 'POST' && pathname === '/inbox/reports/mark-read') {
      await fulfillJson(route, { ok: true, last_viewed_at: new Date().toISOString() })
      return
    }
    if (method === 'GET' && pathname === '/todos/trends') {
      const days = Number(url.searchParams.get('days') ?? 7)
      const points: any[] = []
      const base = new Date('2026-04-18T00:00:00')
      for (let i = days - 1; i >= 0; i -= 1) {
        const d = new Date(base)
        d.setDate(d.getDate() - i)
        points.push({
          day: d.toISOString().slice(0, 10),
          pending: i === 0 ? 2 : 0,
          approved: i === 1 ? 3 : 1,
          rejected: 0,
          expired: 0,
          resolved_by_peer: 0,
          total: i === 0 ? 2 : i === 1 ? 3 : 1,
        })
      }
      await fulfillJson(route, { days, points })
      return
    }
    if (method === 'POST' && pathname === '/todos/batch-decide') {
      const ids = (body as any)?.todo_ids || []
      await fulfillJson(route, {
        total: ids.length,
        succeeded: ids.length,
        failed: 0,
        results: ids.map((id: number) => ({ todo_id: id, ok: true, status: 'approved' })),
      })
      return
    }
    if (method === 'POST' && pathname === '/todos/batch-extend-sla') {
      const ids = (body as any)?.todo_ids || []
      await fulfillJson(route, {
        total: ids.length,
        succeeded: ids.length,
        failed: 0,
        results: ids.map((id: number) => ({ todo_id: id, ok: true })),
      })
      return
    }
    if (method === 'POST' && pathname === '/todos/batch-reassign') {
      const ids = (body as any)?.todo_ids || []
      await fulfillJson(route, {
        total: ids.length,
        succeeded: ids.length,
        failed: 0,
        results: ids.map((id: number) => ({ todo_id: id, ok: true })),
      })
      return
    }
    if (method === 'POST' && pathname === '/todos/bulk-summary') {
      const ids = (body as any)?.ids || []
      const summaries: Record<number, any> = {}
      for (const id of ids) {
        summaries[id] = {
          id,
          title: `Mock ${id}`,
          status: 'pending',
          suggested_actions: [],
          metrics: [],
          skill_meta: null,
        }
      }
      await fulfillJson(route, { summaries })
      return
    }
    if (method === 'GET' && /^\/todos\/\d+\/preview$/.test(pathname)) {
      const id = Number(pathname.match(/\/todos\/(\d+)\/preview/)?.[1] || 0)
      await fulfillJson(route, {
        id,
        title: `Preview ${id}`,
        status: 'pending',
        suggested_actions: ['建议 A'],
        metrics: [],
        decision_reasoning: ['推理 1'],
        skill_meta: { id: 'mock-skill', name: 'Mock', approval_level: 1, owner: null },
        related_report: null,
      })
      return
    }
    if (method === 'GET' && /^\/todos\/\d+$/.test(pathname)) {
      const id = Number(pathname.match(/\/todos\/(\d+)/)?.[1] || 0)
      await fulfillJson(route, {
        todo: {
          id,
          status: 'approved',
          request_id: 99,
          decided_by: null,
          decided_at: null,
          decision_reason: null,
        },
        request: {
          id: 99,
          kind: 'review',
          title: 'Mock Todo Detail',
          summary: '验证付费环比展示',
          aggregate_decision: 'approved',
          aggregate_status: 'approved',
          skill_id: 'mock-skill',
          decision_mode: 'any_of',
          callback_status: 'skipped',
          source_type: 'manual',
          sla_at: '2026-04-25T08:00:00',
        },
        skill_meta: {
          id: 'mock-skill',
          name: 'Mock Skill',
          department: 'EC',
        },
        payload: {
          card_type: 'product_decline_decision_card',
          item_id: '835103629624',
          item_title: '示例品牌测试商品',
          data_time: '2026-04-29 10:52',
          priority: 'P1',
          confidence: '中',
          type: '商品基础/价格 + 流量转化',
          recommended_decision: '建议继续观察',
          business_action_allowed: true,
          metric_sections: [
            {
              title: '标准口径：当前24小时 vs 前24小时',
              metrics: [
                { name: '—当前24h—', note: '支付¥100｜件数10｜转化5.0%｜访客200｜加购20' },
                { name: '—对比24h—', note: '支付¥120｜件数12｜转化5.5%｜访客210｜加购24' },
                { name: '—变化率—', status: '下降', note: '支付-16.7%｜件数-16.7%｜转化-9.1%｜访客-4.8%｜加购-16.7%' },
                { name: '付费访客环比', value: '0.0%', status: '正常', delta: '当前 100 / 对比 100' },
                { name: '付费转化环比', value: '0.0%', status: '正常', delta: '当前 2.0% / 对比 2.0%' },
                { name: '市场Top300状态', value: '未进Top300', status: 'Top300覆盖不足', delta: '今日Top300覆盖不足，不能判定是否在榜' },
              ],
            },
          ],
          paid_flow_analysis_basis: [
            {
              label: '关键词推广-商品汇总实时花费/直接ROI/CPC',
              value: '实时花费金额 ¥3836.42 / 实时直接ROI 1.4386 / 直接ROI环比 -25.41%；实时CPC ¥9.89 / CPC环比 -3.54%',
              status: '已采集计划级明细；关键词/创意级明细待补采',
              detail: '实时花费金额 ¥3836.42 / 实时直接ROI 1.4386 / 直接ROI环比 -25.41%；实时CPC ¥9.89 / CPC环比 -3.54%',
              source: 'tmall_item_promotion_required_metrics',
              period: { current_label: '2026-05-06 03:07~2026-05-07 03:07' },
            },
            {
              label: '关键词推广-计划明细底部合计',
              value: '花费 ¥3836.42 / 直接成交金额 ¥4155.5 / 直接ROI 1.4386 / 点击转化率 16.96% / CPC ¥9.89 / 点击率 6.27%',
              status: 'manage/search底部合计',
              detail: 'one.alimama.com manage/search 底部合计；当前筛选计划 12 个',
              source: 'tmall_item_promotion_required_metrics',
            },
            {
              label: '关键词推广-低直接ROI/高CPC词',
              value: '低直接ROI词 0 / 高CPC词 0',
              status: '关键词/创意级明细待补采',
              detail: '',
              source: 'tmall_item_promotion_required_metrics',
            },
          ],
          free_flow_analysis_basis: [
            {
              label: '流量来源-搜索访客当前24h环比',
              value: '待补采',
              status: '待补采',
              detail: '必须使用item_archives流量来源搜索节点实时数据',
              source: 'tmall_item_flow_required_metrics',
            },
            {
              label: '流量来源-搜索转化率当前24h环比',
              value: '待补采',
              status: '待补采',
              detail: '不能使用商品整体转化率替代',
              source: 'tmall_item_flow_required_metrics',
            },
          ],
          decision_context: {
            paid_detail: {
              budget_status: '异常计划：测试计划A',
              plan_details: [
                { plan_id: 'p1', plan_name: '测试计划A', charge: 100, roi: 1.2, ppc: 8.5, diagnosis: 'CPC升高' },
                { plan_id: 'p2', plan_name: '零值缺采计划', charge: 0, roi: 0, ppc: 0 },
              ],
            },
          },
        },
        structured: null,
        dispatch_tasks: [],
        related_report: null,
      })
      return
    }
    if (method === 'GET' && /^\/todos\/\d+\/related-timeline$/.test(pathname)) {
      await fulfillJson(route, { skill_id: 'mock-skill', items: [] })
      return
    }
    if (method === 'GET' && pathname === '/todos/dispatch/calendar') {
      await fulfillJson(route, { date_from: '2026-04-01', date_to: '2026-04-30', days: [] })
      return
    }
    if (method === 'POST' && pathname === '/todos/dispatch/batch-ack') {
      const ids = (body as any)?.task_ids || []
      await fulfillJson(route, {
        total: ids.length,
        succeeded: ids.length,
        failed: 0,
        results: ids.map((id: number) => ({ task_id: id, ok: true })),
      })
      return
    }
    if (method === 'GET' && pathname === '/inbox/reports') {
      await fulfillJson(route, { total: 0, page: 1, page_size: 20, items: [] })
      return
    }

    if (segments[0] === 'playbooks' && method === 'GET' && segments.length === 1) {
      await fulfillJson(route, clone(state.playbooks))
      return
    }
    if (segments[0] === 'playbooks' && method === 'POST' && segments[1] === 'validate') {
      await fulfillJson(route, validatePlaybookDocument(body as Partial<Playbook>))
      return
    }
    if (segments[0] === 'playbooks' && method === 'GET' && segments[2] === 'summary') {
      const playbook = findPlaybook(state, segments[1])
      if (!playbook) {
        await fulfillJson(route, { detail: 'Playbook not found' }, 404)
        return
      }
      await fulfillJson(route, buildPlaybookSummary(state, playbook))
      return
    }
    if (segments[0] === 'playbooks' && method === 'GET' && segments[2] === 'runs' && segments[3] === 'latest') {
      const latestRun = latestPlaybookRun(state, segments[1])
      if (!latestRun) {
        await fulfillJson(route, null)
        return
      }
      await fulfillJson(route, clone({
        run_id: latestRun.run_id,
        status: latestRun.status,
        status_label: latestRun.status_label,
        completed_steps: latestRun.completed_steps,
        total_steps: latestRun.total_steps,
        started_at: latestRun.started_at,
        completed_at: latestRun.completed_at,
        summary: latestRun.summary,
      }))
      return
    }
    if (segments[0] === 'playbooks' && method === 'GET' && segments[2] === 'history') {
      await fulfillJson(route, [
        {
          id: 'pb-history-1',
          actor: '管理员',
          action: 'save',
          created_at: '2026-04-13T07:40:00.000Z',
          summary: '更新补货巡检流程说明与节点布局',
        },
      ])
      return
    }
    if (segments[0] === 'playbooks' && method === 'GET' && segments[2] === 'mermaid') {
      const playbook = findPlaybook(state, segments[1])
      if (!playbook) {
        await fulfillJson(route, { detail: 'Playbook not found' }, 404)
        return
      }
      await fulfillJson(route, {
        mermaid: buildPlaybookMermaid(playbook),
        text_flowchart: buildPlaybookMermaid(playbook),
      })
      return
    }
    if (segments[0] === 'playbooks' && method === 'GET' && segments.length === 2) {
      const playbook = findPlaybook(state, segments[1])
      if (!playbook) {
        await fulfillJson(route, { detail: 'Playbook not found' }, 404)
        return
      }
      await fulfillJson(route, clone(playbook))
      return
    }
    if (segments[0] === 'playbooks' && method === 'PUT' && segments.length === 2) {
      const playbook = findPlaybook(state, segments[1])
      if (!playbook) {
        await fulfillJson(route, { detail: 'Playbook not found' }, 404)
        return
      }
      const nextPlaybook: Playbook = {
        ...playbook,
        ...body,
        file_name: playbook.file_name,
        name: String(body.name || playbook.name),
        description: String(body.description ?? playbook.description),
        department: String(body.department ?? playbook.department),
        steps: Array.isArray(body.steps) ? clone(body.steps) : clone(playbook.steps),
        trigger: body.trigger ? clone(body.trigger) : playbook.trigger,
        updated_at: '2026-04-13T08:18:00.000Z',
        status: 'saved',
        raw_config: jsonBody({
          name: body.name || playbook.name,
          description: body.description ?? playbook.description,
          department: body.department ?? playbook.department,
          trigger: body.trigger ?? playbook.trigger,
          steps: body.steps ?? playbook.steps,
        }),
      }
      if (body._canvas_layout && typeof body._canvas_layout === 'object') {
        nextPlaybook._canvas_layout = clone(body._canvas_layout as Record<string, unknown>)
      }
      const index = state.playbooks.findIndex((item) => item.file_name === playbook.file_name)
      state.playbooks[index] = nextPlaybook
      await fulfillJson(route, {
        ...clone(nextPlaybook),
        saved_at: nextPlaybook.updated_at,
        message: '已保存',
      })
      return
    }
    if (segments[0] === 'playbooks' && method === 'POST' && segments[2] === 'validate') {
      const playbook = findPlaybook(state, segments[1])
      if (!playbook) {
        await fulfillJson(route, { detail: 'Playbook not found' }, 404)
        return
      }
      const validation = validatePlaybookDocument(playbook)
      await fulfillJson(route, { ...validation, checked_scope: 'saved' })
      return
    }
    if (segments[0] === 'playbooks' && method === 'POST' && segments[2] === 'run') {
      const playbook = findPlaybook(state, segments[1])
      if (!playbook) {
        await fulfillJson(route, { detail: 'Playbook not found' }, 404)
        return
      }
      const run = createPlaybookRun(state, playbook)
      await fulfillJson(route, {
        run_id: run.run_id,
        status: run.status,
        status_label: run.status_label,
        total_steps: run.total_steps,
        completed_steps: run.completed_steps,
        summary: run.summary,
      })
      return
    }
    if (segments[0] === 'playbooks' && method === 'POST' && segments[2] === 'publish') {
      const playbook = findPlaybook(state, segments[1])
      if (!playbook) {
        await fulfillJson(route, { detail: 'Playbook not found' }, 404)
        return
      }
      const validation = validatePlaybookDocument(playbook)
      if (!validation.valid) {
        await fulfillJson(route, { detail: 'Playbook invalid', errors: validation.errors, issues: validation.issues }, 400)
        return
      }
      const review = createReview(
        state,
        {
          skill_id: `playbook:${segments[1]}`,
          change_type: 'new_skill',
          diff_summary: `Playbook ${segments[1]}: ${playbook.steps.length} 个步骤`,
          reason: `Playbook [${playbook.name}] 提交审核`,
        },
        {
          diff_text: buildPlaybookMermaid(playbook),
        },
      )
      await fulfillJson(route, { message: '已提交审核', review_id: review.id })
      return
    }

    if (segments[0] === 'reviews' && method === 'GET' && segments.length === 1) {
      await fulfillJson(route, listReviews(state, url))
      return
    }
    if (segments[0] === 'reviews' && method === 'POST' && segments.length === 1) {
      const review = createReview(state, body)
      await fulfillJson(route, {
        review_id: review.id,
        skill_id: review.skill_id,
        reviewer: review.reviewer,
        status: review.status,
      })
      return
    }
    if (segments[0] === 'reviews' && method === 'GET' && segments.length === 2) {
      const review = state.reviews.find((item) => item.id === Number(segments[1]))
      if (!review) {
        await fulfillJson(route, { detail: 'Review not found' }, 404)
        return
      }
      await fulfillJson(route, clone(review))
      return
    }
    if (segments[0] === 'reviews' && method === 'POST' && segments[2] === 'approve') {
      const result = approveReview(state, Number(segments[1]))
      if (!result) {
        await fulfillJson(route, { detail: 'Review not found' }, 404)
        return
      }
      await fulfillJson(route, result)
      return
    }
    if (segments[0] === 'reviews' && method === 'POST' && segments[2] === 'reject') {
      const result = rejectReview(state, Number(segments[1]), String(body.reason || ''))
      if (!result) {
        await fulfillJson(route, { detail: 'Review not found' }, 404)
        return
      }
      await fulfillJson(route, result)
      return
    }
    if (segments[0] === 'reviews' && method === 'POST' && segments[2] === 'semantic-diff') {
      await fulfillJson(route, {
        ai_review: {
          risk_score: 1,
          opinions: [],
        },
      })
      return
    }

    if (segments[0] === 'executions' && method === 'GET' && segments[1] === 'runs' && segments.length === 3) {
      const run = state.playbookRuns[segments[2]]
      if (!run) {
        await fulfillJson(route, { detail: 'Run not found' }, 404)
        return
      }
      await fulfillJson(route, clone({
        id: run.id,
        run_id: run.run_id,
        playbook_id: run.playbook_id,
        trigger_type: run.trigger_type,
        status: run.status,
        status_label: run.status_label,
        total_steps: run.total_steps,
        completed_steps: run.completed_steps,
        started_at: run.started_at,
        completed_at: run.completed_at,
        summary: run.summary,
      }))
      return
    }
    if (segments[0] === 'executions' && method === 'GET' && segments[1] === 'runs' && segments[3] === 'steps') {
      const run = state.playbookRuns[segments[2]]
      if (!run) {
        await fulfillJson(route, { detail: 'Run not found' }, 404)
        return
      }
      await fulfillJson(route, clone(run.steps))
      return
    }
    if (segments[0] === 'executions' && method === 'GET' && segments[1] === 'runs' && segments[3] === 'decisions') {
      const run = state.playbookRuns[segments[2]]
      if (!run) {
        await fulfillJson(route, { detail: 'Run not found' }, 404)
        return
      }
      await fulfillJson(route, clone(run.decisions))
      return
    }

    if (segments[0] === 'portal' && segments[1] === 'skills' && method === 'GET' && segments.length === 2) {
      await fulfillJson(route, listPortalSkills(state, url))
      return
    }
    if (segments[0] === 'portal' && segments[1] === 'skills' && method === 'GET' && segments.length === 3) {
      const skill = state.skills.find((item) => item.id === segments[2])
      if (!skill) {
        await fulfillJson(route, { detail: 'Skill not found' }, 404)
        return
      }
      await fulfillJson(route, skill)
      return
    }
    if (segments[0] === 'portal' && segments[1] === 'skills' && method === 'GET' && segments[3] === 'ui' && segments[4] === 'preferences') {
      await fulfillJson(route, { items: [], total: 0 })
      return
    }
    if (segments[0] === 'portal' && segments[1] === 'skills' && method === 'GET' && segments[3] === 'ui') {
      const surface = url.searchParams.get('surface') || 'run_form'
      await fulfillJson(route, {
        surface,
        overlay: {},
        saved_prompt: '',
        merged_ui_schema_hash: `sha256:e2e-${segments[2]}-${surface}`,
        merged_schema: {
          schema_version: '1.0',
          skill_id: segments[2],
          surface,
          components: surface === 'result'
            ? [{ id: 'result-table', type: 'table', binding: 'result.data', title: '结果' }]
            : [{ id: 'field-date', type: 'field', binding: 'params.date', title: '日期' }],
        },
        permissions: { read: true, execute: true, customize_ui: true },
      })
      return
    }
    if (segments[0] === 'portal' && segments[1] === 'skills' && method === 'POST' && segments[3] === 'submit') {
      const skill = state.skills.find((item) => item.id === segments[2])
      if (!skill) {
        await fulfillJson(route, { detail: 'Skill not found' }, 404)
        return
      }
      const submission = createSubmission(state, skill, body.params || {})
      await fulfillJson(route, { id: submission.id, execution_id: submission.execution_id })
      return
    }
    if (segments[0] === 'portal' && segments[1] === 'submissions' && method === 'GET' && segments.length === 2) {
      await fulfillJson(route, fillListResponse(state.submissions))
      return
    }
    if (segments[0] === 'portal' && segments[1] === 'submissions' && method === 'GET' && segments.length === 3) {
      const submission = state.submissions.find((item) => item.id === segments[2])
      if (!submission) {
        await fulfillJson(route, { detail: 'Submission not found' }, 404)
        return
      }
      await fulfillJson(route, submission)
      return
    }
    if (pathname === '/portal/overview' && method === 'GET') {
      await fulfillJson(route, buildOverview(state))
      return
    }
    if (pathname === '/portal/market' && method === 'GET') {
      await fulfillJson(route, {
        categories: ['投放优化', '运营'],
        skills: state.skills,
        templates: state.templates,
        stats: { skill_count: state.skills.length, template_count: state.templates.length },
      })
      return
    }

    if (pathname === '/org/tree' && method === 'GET') {
      await fulfillJson(route, clone(state.orgTree))
      return
    }
    if (pathname === '/dashboard/governance' && method === 'GET') {
      await fulfillJson(route, {
        workers: { total: 1, online: 1 },
        approvals: { pending: 2 },
        data_access: { pending: 1, approved: 3 },
        market: { templates: state.templates.length, active_skills: state.skills.length },
        quotas: { tracked: 2, throttled_orgs: 0 },
      })
      return
    }
    if (pathname === '/executions/workers' && method === 'GET') {
      await fulfillJson(route, {
        items: [{ id: 'worker-a', name: 'Worker A', queue_name: 'default', capacity: 4, status: 'online' }],
        total: 1,
      })
      return
    }
    if (pathname === '/org/sync-dingtalk' && method === 'POST') {
      await fulfillJson(route, { ok: true, synced_units: 1, synced_users: 2, errors: [] })
      return
    }
    if (pathname === '/org/units' && method === 'POST') {
      const parentId = body.parent_id ? String(body.parent_id) : null
      const unit: OrgUnit = {
        id: `org-new-${state.nextOrgId++}`,
        name: String(body.name || '新组织'),
        type: String(body.type || 'department'),
        parent_id: parentId,
        member_count: 0,
        children: [],
      }
      addOrgUnit(state.orgTree, parentId, unit)
      state.membersByOrg[unit.id] = []
      await fulfillJson(route, unit)
      return
    }
    if (segments[0] === 'org' && segments[1] === 'units' && segments[3] === 'members' && method === 'GET') {
      await fulfillJson(route, clone(state.membersByOrg[segments[2]] || []))
      return
    }
    if (pathname === '/org/memberships' && method === 'POST') {
      await fulfillJson(route, addMembership(state, body))
      return
    }
    if (segments[0] === 'org' && segments[1] === 'memberships' && method === 'DELETE' && segments.length === 4) {
      await fulfillJson(route, removeMembership(state, segments[3], segments[2]))
      return
    }

    throw new Error(`Unhandled mock API route: ${method} ${pathname}`)
  })

  return state
}
