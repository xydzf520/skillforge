import type {
  SkillCustomSections,
  SkillDocument,
  SkillMeta,
  SkillOutputField,
  SkillParam,
  SkillRuleStep,
  SkillTestCase,
  SkillTodoSpec,
  SkillWorkflowPreview,
} from '@/types/skill'
import type {
  WorkbenchChatMessage,
  WorkbenchPatchDraft,
} from '@/types/skillstudio'

export const MODULES = [
  { key: 'meta', label: '基础信息', description: '名称、部门、触发方式等元信息' },
  { key: 'goal', label: '目标', description: '一句话目标与范围' },
  { key: 'rules', label: '规则', description: '决策条件与动作规则' },
  { key: 'params', label: '参数', description: '可调阈值与配置项' },
  { key: 'output_table', label: '输出表格', description: '字段定义与输出结构' },
  { key: 'todos', label: '待办', description: '收件中心待办输出契约' },
  { key: 'test_cases', label: '测试样例', description: '输入输出样例与回放基线' },
  { key: 'workflow', label: '工作流', description: '节点、连线与字段绑定' },
]

type ValidationStatus = 'info' | 'success' | 'warning' | 'error'

type WorkbenchReference = {
  id: string
  title: string
  summary: string
  source_type: string
  source_module: string
  source_id: string
  reference_mode: string
  tags: string[]
  [key: string]: unknown
}

type ValidationItem = {
  key: string
  title: string
  status: ValidationStatus
  message: string
  detail: string
  [key: string]: unknown
}

type ValidationReport = {
  structural_checks: ValidationItem[]
  sample_case_checks: ValidationItem[]
  historical_replay_checks: ValidationItem[]
  impact_summary: {
    improved: number
    regressed: number
    unchanged: number
  }
  can_apply: boolean | null
  summary: string
  warnings: unknown[]
  errors: unknown[]
  created_at: string
  [key: string]: unknown
}

type WorkflowSnapshot = {
  parsed: Record<string, unknown>
  workflow: SkillWorkflowPreview | null
  meta: Record<string, unknown>
  raw: unknown
}

type WorkflowPreviewLike = SkillWorkflowPreview

type NormalizableRecord = Record<string, unknown>

function isRecord(value: unknown): value is NormalizableRecord {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' ? value : fallback
}

export function cloneValue<T>(value: unknown, fallback: T): T {
  if (value === undefined || value === null) return fallback
  try {
    return JSON.parse(JSON.stringify(value)) as T
  } catch {
    return fallback
  }
}

export function parseMaybeJson<T>(value: unknown, fallback: T): T {
  if (value === undefined || value === null) return fallback
  if (typeof value !== 'string') return cloneValue(value, fallback)
  try {
    return JSON.parse(value) as T
  } catch {
    return fallback
  }
}

export function createSkillDocument(): SkillDocument {
  return {
    meta: {
      id: '',
      name: '',
      description: '',
      department: '',
      owner: '',
      status: '',
      version: '',
      trigger_type: '',
      risk_level: '',
    },
    goal: '',
    rules: [],
    params: [],
    output_table: [],
    todos: [],
    test_cases: [],
    workflow: {
      nodes: [],
      edges: [],
      bindings: [],
      diff_preview: {},
      summary: '',
      status: '',
      updated_at: '',
    },
    custom_sections: {} as SkillCustomSections,
  }
}

export function createWorkbenchContext(): WorkflowSnapshot {
  return {
    parsed: {},
    workflow: null,
    meta: {},
    raw: null,
  }
}

export function createWorkflowPreview(): WorkflowPreviewLike {
  return {
    nodes: [],
    edges: [],
    bindings: [],
    diff_preview: {},
    summary: '',
    status: '',
    updated_at: '',
  }
}

export function createPatchDraft(): WorkbenchPatchDraft {
  return {
    id: '',
    target_module: '',
    intent: '',
    summary: '',
    patch: {},
    diff_preview: {},
    warnings: [],
    needs_confirmation: false,
    status: 'draft',
    references: [],
    created_at: '',
    updated_at: '',
  }
}

export function createValidationReport(): ValidationReport {
  return {
    structural_checks: [],
    sample_case_checks: [],
    historical_replay_checks: [],
    impact_summary: {
      improved: 0,
      regressed: 0,
      unchanged: 0,
    },
    can_apply: null,
    summary: '',
    warnings: [],
    errors: [],
    created_at: '',
  }
}

export function normalizeModuleName(moduleKey: string) {
  return MODULES.some(module => module.key === moduleKey) ? moduleKey : 'goal'
}

export function normalizeReference(item: unknown): WorkbenchReference | null {
  if (!item) return null
  if (typeof item === 'string') {
    return {
      id: item,
      title: item,
      summary: '',
      source_type: 'skill_module',
      source_module: '',
      source_id: item,
      reference_mode: 'copy_structure',
      tags: [],
    }
  }

  if (!isRecord(item)) return null

  const sourceType = item.source_type || item.sourceType || 'skill_module'
  const sourceModule = item.source_module || item.sourceModule || item.module || ''
  const sourceId = item.source_id || item.sourceId || item.id || ''
  const referenceMode = item.reference_mode || item.referenceMode || 'copy_structure'
  const title = item.title || item.name || item.label || sourceId || sourceModule || '引用项'

  return {
    ...cloneValue(item, {}),
    id: asString(item.id, `${String(sourceType)}:${String(sourceId || sourceModule || title)}`),
    title: String(title),
    summary: asString(item.summary) || asString(item.description),
    source_type: String(sourceType),
    source_module: String(sourceModule),
    source_id: String(sourceId),
    reference_mode: String(referenceMode),
    tags: Array.isArray(item.tags) ? cloneValue<string[]>(item.tags, []) : [],
  }
}

export function normalizeMessage(item: unknown): WorkbenchChatMessage | null {
  if (!item) return null
  if (typeof item === 'string') {
    return {
      role: 'assistant',
      content: item,
      message: item,
      text: item,
      created_at: new Date().toISOString(),
    }
  }

  if (!isRecord(item)) return null

  const content = asString(item.content) || asString(item.message) || asString(item.text)
  return {
    ...cloneValue(item, {}),
    role: asString(item.role, 'assistant'),
    content,
    message: content,
    text: content,
    created_at: asString(item.created_at) || asString(item.createdAt) || new Date().toISOString(),
  }
}

function normalizeValidationItems(items: unknown, fallbackPrefix: string): ValidationItem[] {
  if (isRecord(items)) {
    return Object.entries(items).map(([key, value], index) => normalizeValidationItem(value, key || `${fallbackPrefix}-${index}`, fallbackPrefix, index))
  }
  return (Array.isArray(items) ? items : []).map((item, index) => normalizeValidationItem(item, `${fallbackPrefix}-${index}`, fallbackPrefix, index))
}

function normalizeValidationItem(item: unknown, key: string, fallbackPrefix: string, index: number): ValidationItem {
  if (typeof item === 'string') {
    return {
      key,
      title: item,
      status: 'info',
      message: '',
      detail: '',
    }
  }

  if (!isRecord(item)) {
    return {
      key,
      title: `检查 ${index + 1}`,
      status: 'info',
      message: '',
      detail: '',
    }
  }

  const statusValue = String(item.status || (item.ok === false ? 'error' : item.warning ? 'warning' : 'success')).toLowerCase()
  const status = (['info', 'success', 'warning', 'error'].includes(statusValue) ? statusValue : 'info') as ValidationStatus
  return {
    ...cloneValue(item, {}),
    key: asString(item.key) || asString(item.id) || `${fallbackPrefix}-${index}`,
    title: asString(item.title) || asString(item.name) || asString(item.block) || asString(item.section) || `检查 ${index + 1}`,
    status,
    message: asString(item.message) || asString(item.detail) || asString(item.reason),
    detail: asString(item.detail) || asString(item.explanation),
  }
}

export function buildParsedSnapshot(doc: Partial<SkillDocument> = {}): Pick<SkillDocument, 'rules' | 'params' | 'output_table' | 'todos' | 'test_cases' | 'workflow'> {
  return {
    rules: Array.isArray(doc.rules) ? cloneValue<SkillRuleStep[]>(doc.rules, []) : [],
    params: Array.isArray(doc.params) ? cloneValue<SkillParam[]>(doc.params, []) : [],
    output_table: Array.isArray(doc.output_table) ? cloneValue<SkillOutputField[]>(doc.output_table, []) : [],
    todos: Array.isArray(doc.todos) ? cloneValue<SkillTodoSpec[]>(doc.todos, []) : [],
    test_cases: Array.isArray(doc.test_cases) ? cloneValue<SkillTestCase[]>(doc.test_cases, []) : [],
    workflow: cloneValue<SkillWorkflowPreview | undefined>(doc.workflow || undefined, undefined),
  }
}

export function normalizeSkillDocument(source: unknown = {}): SkillDocument {
  const base = createSkillDocument()
  const raw = cloneValue<NormalizableRecord>(source, {})

  if (raw.meta || raw.goal || raw.rules || raw.params || raw.output_table || raw.todos || raw.test_cases || raw.custom_sections) {
    return {
      ...base,
      ...raw,
      meta: { ...base.meta, ...(cloneValue<SkillMeta>(raw.meta, {} as SkillMeta)) },
      rules: Array.isArray(raw.rules) ? cloneValue<SkillRuleStep[]>(raw.rules, []) : [],
      params: Array.isArray(raw.params) ? cloneValue<SkillParam[]>(raw.params, []) : [],
      output_table: Array.isArray(raw.output_table) ? cloneValue<SkillOutputField[]>(raw.output_table, []) : [],
      todos: Array.isArray(raw.todos) ? cloneValue<SkillTodoSpec[]>(raw.todos, []) : [],
      test_cases: Array.isArray(raw.test_cases) ? cloneValue<SkillTestCase[]>(raw.test_cases, []) : [],
      workflow: normalizeWorkflowPreview(raw.workflow || {}),
      custom_sections: { ...base.custom_sections, ...(cloneValue<SkillCustomSections>(raw.custom_sections, {} as SkillCustomSections)) },
    }
  }

  const parsed = isRecord(raw.parsed) ? raw.parsed : {}
  const frontmatter = isRecord(raw.frontmatter) ? raw.frontmatter : (isRecord(parsed.frontmatter) ? parsed.frontmatter : {})
  const params: SkillParam[] = Array.isArray(raw.params)
    ? cloneValue<SkillParam[]>(raw.params, [])
    : Object.entries(frontmatter).map(([key, value]) => ({ name: key, value: value as string | number | boolean | null }))

  return {
    ...base,
    meta: {
      ...base.meta,
      id: asString(raw.id),
      name: asString(raw.name),
      description: asString(raw.description),
      department: asString(raw.department),
      owner: asString(raw.owner) || asString(raw.created_by),
      status: asString(raw.status),
      version: asString(raw.version),
      trigger_type: asString(frontmatter.trigger_type) || asString(raw.trigger_type),
      risk_level: asString(frontmatter.risk_level) || asString(raw.risk_level),
    },
    goal: asString(raw.goal) || asString(parsed.purpose) || asString(raw.purpose),
    rules: Array.isArray(parsed.steps) ? cloneValue(parsed.steps, []) : (Array.isArray(raw.rules) ? cloneValue(raw.rules, []) : []),
    params,
    output_table: Array.isArray(parsed.output_definition)
      ? cloneValue(parsed.output_definition, [])
      : (Array.isArray(raw.output_table) ? cloneValue(raw.output_table, []) : []),
    todos: Array.isArray(raw.todos) ? cloneValue<SkillTodoSpec[]>(raw.todos, []) : [],
    test_cases: Array.isArray(parsed.test_cases)
      ? cloneValue(parsed.test_cases, [])
      : (Array.isArray(raw.test_cases) ? cloneValue(raw.test_cases, []) : []),
    workflow: normalizeWorkflowPreview(raw.workflow || parsed.workflow || {}),
    custom_sections: cloneValue<SkillCustomSections>(raw.custom_sections, {} as SkillCustomSections),
  }
}

export function normalizeWorkflowPreview(source: unknown = {}): WorkflowPreviewLike {
  const raw = cloneValue<NormalizableRecord>(source, {})
  const nestedWorkflow = isRecord(raw.workflow) ? raw.workflow : raw
  const payload = raw.nodes || raw.edges || raw.bindings || raw.diff_preview ? raw : nestedWorkflow

  return {
    nodes: Array.isArray(payload.nodes) ? cloneValue(payload.nodes, []) : [],
    edges: Array.isArray(payload.edges) ? cloneValue(payload.edges, []) : [],
    bindings: Array.isArray(payload.bindings) ? cloneValue(payload.bindings, []) : [],
    diff_preview: parseMaybeJson(payload.diff_preview || payload.diffPreview || {}, {}),
    summary: asString(payload.summary),
    status: asString(payload.status),
    updated_at: asString(payload.updated_at) || asString(payload.updatedAt),
  }
}

export function normalizePatch(source: unknown = {}): WorkbenchPatchDraft {
  const raw = cloneValue<NormalizableRecord>(source, {})

  return {
    ...createPatchDraft(),
    ...raw,
    patch: parseMaybeJson(raw.patch || raw.patch_json || raw.patchJson || {}, {}),
    diff_preview: parseMaybeJson(raw.diff_preview || raw.diffPreview || raw.diff_preview_json || raw.diffPreviewJson || {}, {}),
    warnings: Array.isArray(raw.warnings) ? cloneValue(raw.warnings, []) : [],
    references: Array.isArray(raw.references) ? raw.references.map(normalizeReference).filter(Boolean) : [],
  }
}

export function normalizeValidationReport(source: unknown = {}): ValidationReport {
  const raw = cloneValue<NormalizableRecord>(source, {})
  const impactSummary = isRecord(raw.impact_summary) ? raw.impact_summary : (isRecord(raw.impactSummary) ? raw.impactSummary : {})

  return {
    ...createValidationReport(),
    ...raw,
    structural_checks: normalizeValidationItems(raw.structural_checks || raw.structuralChecks || raw.blocks || [], 'structural'),
    sample_case_checks: normalizeValidationItems(raw.sample_case_checks || raw.sampleCaseChecks || [], 'sample'),
    historical_replay_checks: normalizeValidationItems(raw.historical_replay_checks || raw.historicalReplayChecks || [], 'replay'),
    impact_summary: {
      improved: asNumber(impactSummary.improved),
      regressed: asNumber(impactSummary.regressed),
      unchanged: asNumber(impactSummary.unchanged),
    },
    warnings: Array.isArray(raw.warnings) ? cloneValue(raw.warnings, []) : [],
    errors: Array.isArray(raw.errors) ? cloneValue(raw.errors, []) : [],
  }
}
