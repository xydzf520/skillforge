/**
 * SkillStructure (IDE 内存 / workbench schema) 与
 * SkillStructured (后端 parser schema) 之间的双向映射。
 */

import type {
  SkillAntipattern,
  SkillCustomSections,
  SkillDataInput,
  SkillDocument,
  SkillOutputField,
  SkillParam,
  SkillRuleStep,
  SkillTestCase,
  SkillTodoSpec,
  StructuredSkillResponse,
} from '@/types/skill'
import { documentToMarkdown, markdownToDocument, parseTodoSpecs, serializeTodoSpecs } from './markdownParser'

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

type StructuredPayload = {
  frontmatter: Record<string, unknown>
  purpose: string
  steps: Array<Record<string, unknown>>
  antipatterns: SkillAntipattern[]
  output_definition: Array<Record<string, unknown>>
  data_inputs: SkillDataInput[]
  test_cases: Array<Record<string, unknown>>
  custom_sections: Record<string, unknown>
  policy_pack: Record<string, unknown>
}

function safeParseJson<T>(value: unknown, fallback: T): T {
  if (typeof value !== 'string') return fallback
  try {
    return JSON.parse(value) as T
  } catch {
    return fallback
  }
}

export function toStructuredPayload(doc: SkillDocument | null | undefined): StructuredPayload | Record<string, never> {
  if (!doc) return {}

  const frontmatter = { ...(doc.meta || {}) }
  const policyPack: Record<string, unknown> = {}
  for (const p of (doc.params || []) as SkillParam[]) {
    if (p.name?.trim()) {
      policyPack[p.name.trim()] = p.default_value ?? p.value ?? ''
    }
  }

  const steps = ((doc.rules || []) as SkillRuleStep[]).map((r) => ({
    id: r.id || '',
    name: r.name || '',
    description: r.description || '',
    branches: (r.branches || []).map((b) => ({
      condition: b.condition || '',
      conclusion: b.conclusion || '',
      action: b.action || '',
      next_step: b.next_step || null,
    })),
  }))

  const outputDef = ((doc.output_table || []) as SkillOutputField[]).map((o) => ({
    name: o.name || o.field || '',
    field: o.field || o.name || '',
    label: o.label || o.name || '',
    format: o.format || 'text',
    recipient: o.recipient || '',
    approval_level: o.approval_level || '',
  }))

  const testCases = ((doc.test_cases || []) as SkillTestCase[]).map((tc) => {
    let input: unknown = tc.input_data ?? tc.input ?? {}
    let expected: unknown = tc.expected_output ?? {}
    if (typeof input === 'string') input = safeParseJson(input, input)
    if (typeof expected === 'string') expected = safeParseJson(expected, expected)
    return {
      name: tc.name || '',
      input_data: input,
      expected_output: expected,
      assert_rules: tc.assert_rules || [],
    }
  })

  const customSections: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(doc.custom_sections || {})) {
    if (!key || key.startsWith('__') || key === '待办') continue
    customSections[key] = value
  }
  const todoMarkdown = serializeTodoSpecs(doc.todos as SkillTodoSpec[] | undefined)
  if (todoMarkdown) {
    customSections['待办'] = todoMarkdown
  }

  return {
    frontmatter,
    purpose: doc.goal || '',
    steps,
    antipatterns: doc.antipatterns || [],
    output_definition: outputDef,
    data_inputs: doc.data_inputs || [],
    test_cases: testCases,
    custom_sections: customSections,
    policy_pack: policyPack,
  }
}

export function fromSkillResponse(data: StructuredSkillResponse | null | undefined): SkillDocument | null {
  if (!data) return null

  const structured = isRecord(data.structured) ? data.structured : (isRecord(data.parsed) ? data.parsed : {})
  const fm = isRecord(structured.frontmatter) ? structured.frontmatter : {}
  const pp = isRecord(data.policy_pack) ? data.policy_pack : (isRecord(structured.policy_pack) ? structured.policy_pack : {})
  const otherFiles = data.other_files || {}

  let taskContract = null
  let taskReviewState = null
  try {
    taskContract = otherFiles['task-contract.json'] ? JSON.parse(otherFiles['task-contract.json']) : null
  } catch {
    taskContract = null
  }
  try {
    taskReviewState = otherFiles['task-review-state.json'] ? JSON.parse(otherFiles['task-review-state.json']) : null
  } catch {
    taskReviewState = null
  }

  const params: SkillParam[] = []
  if (typeof pp === 'object' && pp !== null) {
    for (const [k, v] of Object.entries(pp)) {
      params.push({ name: k, default_value: v as string | number | boolean | null | undefined, description: '' })
    }
  }

  const steps = Array.isArray(structured.steps) ? structured.steps : []
  const rules: SkillRuleStep[] = steps.map((s) => {
    const step = isRecord(s) ? s : {}
    const branches = Array.isArray(step.branches) ? step.branches : []
    return ({
      id: typeof step.id === 'string' ? step.id : '',
      name: typeof step.name === 'string' ? step.name : '',
      description: typeof step.description === 'string' ? step.description : '',
      branches: branches.map((b) => {
        const branch = isRecord(b) ? b : {}
        return {
          condition: typeof branch.condition === 'string' ? branch.condition : '',
          conclusion: typeof branch.conclusion === 'string' ? branch.conclusion : '',
          action: typeof branch.action === 'string' ? branch.action : '',
          next_step: typeof branch.next_step === 'string' ? branch.next_step : null,
        }
      }),
    })
  })

  const outputDefinition = Array.isArray(structured.output_definition) ? structured.output_definition : []
  const outputTable: SkillOutputField[] = outputDefinition.map((o) => {
    const output = isRecord(o) ? o : {}
    return {
      name: typeof output.name === 'string' ? output.name : (typeof output.field === 'string' ? output.field : ''),
      field: typeof output.field === 'string' ? output.field : (typeof output.name === 'string' ? output.name : ''),
      format: typeof output.format === 'string' ? output.format : 'text',
      recipient: typeof output.recipient === 'string' ? output.recipient : '',
      approval_level: typeof output.approval_level === 'string' || typeof output.approval_level === 'number' ? output.approval_level : '',
    }
  })

  const structuredTestCases = Array.isArray(structured.test_cases) ? structured.test_cases : []
  const testCases: SkillTestCase[] = structuredTestCases.map((tc) => {
    const testCase = isRecord(tc) ? tc : {}
    return {
      name: typeof testCase.name === 'string' ? testCase.name : '',
      input_data: (isRecord(testCase.input_data) ? testCase.input_data : (isRecord(testCase.input) ? testCase.input : {})) as Record<string, unknown>,
      expected_output: (isRecord(testCase.expected_output) ? testCase.expected_output : {}) as Record<string, unknown>,
      assert_rules: Array.isArray(testCase.assert_rules) ? testCase.assert_rules.filter((item): item is string => typeof item === 'string') : [],
    }
  })

  const customSections = isRecord(structured.custom_sections)
    ? (structured.custom_sections as SkillCustomSections)
    : ({} as SkillCustomSections)
  const todos = parseTodoSpecs(typeof customSections['待办'] === 'string' ? customSections['待办'] : '')
  const contract = parseContractFile(otherFiles['contract.json'])
  const outputCapabilities = inferOutputCapabilitiesFromContract(contract)
  const inferredTodos = todos.length ? todos : inferTodosFromContract(contract)
  const visibleCustomSections = Object.fromEntries(
    Object.entries(customSections).filter(([key]) => key !== '待办'),
  ) as SkillCustomSections

  return {
    meta: {
      name: typeof fm.name === 'string' ? fm.name : (data.name || ''),
      department: typeof fm.department === 'string' ? fm.department : (data.department || ''),
      role: typeof fm.role === 'string' ? fm.role : (data.role || ''),
      trigger_type: typeof fm.trigger_type === 'string' ? fm.trigger_type : (data.trigger_type || 'manual'),
      risk_level: typeof fm.risk_level === 'string' ? fm.risk_level : (data.risk_level || 'R2'),
      approval_level: typeof fm.approval_level === 'string' || typeof fm.approval_level === 'number' ? fm.approval_level : (data.approval_level || 1),
      description: typeof fm.description === 'string' ? fm.description : (data.description || ''),
    },
    goal: typeof structured.purpose === 'string' ? structured.purpose : (typeof fm.description === 'string' ? fm.description : (data.description || '')),
    rules,
    params,
    output_table: outputTable,
    todos: inferredTodos,
    test_cases: testCases,
    antipatterns: Array.isArray(structured.antipatterns) ? structured.antipatterns as SkillAntipattern[] : [],
    data_inputs: Array.isArray(structured.data_inputs) ? structured.data_inputs as SkillDataInput[] : [],
    workflow: data.workflow || {},
    custom_sections: {
      ...visibleCustomSections,
      __artifacts: {
        ...(otherFiles['intent.md'] ? { 'intent.md': otherFiles['intent.md'] } : {}),
        ...(otherFiles['policy.yaml'] ? { 'policy.yaml': otherFiles['policy.yaml'] } : {}),
        ...(otherFiles['task-contract.json'] ? { 'task-contract.json': otherFiles['task-contract.json'] } : {}),
        ...(otherFiles['task-review-state.json'] ? { 'task-review-state.json': otherFiles['task-review-state.json'] } : {}),
      },
      __output_capabilities: outputCapabilities,
      ...(taskContract ? { __task_contract: taskContract } : {}),
      ...(taskReviewState ? { __task_contract_meta: taskReviewState, __task_gate: taskReviewState.gate || {} } : {}),
    },
  }
}

function parseContractFile(raw: unknown): Record<string, unknown> | null {
  if (typeof raw !== 'string' || !raw.trim()) return null
  try {
    const parsed = JSON.parse(raw)
    return isRecord(parsed) ? parsed : null
  } catch {
    return null
  }
}

function getOutputSchema(contract: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!contract) return null
  if (isRecord(contract.output_schema)) return contract.output_schema
  const output = isRecord(contract.output) ? contract.output : null
  if (output && isRecord(output.output_schema)) return output.output_schema
  return null
}

export function inferOutputCapabilitiesFromContract(contract: Record<string, unknown> | null): { todos: boolean; reports: boolean } {
  const schema = getOutputSchema(contract)
  if (!schema) return { todos: false, reports: false }
  const required = Array.isArray(schema.required) ? schema.required : []
  const properties = isRecord(schema.properties) ? schema.properties : {}
  const declaresTodos = required.includes('todos') || Object.prototype.hasOwnProperty.call(properties, 'todos')
  return { todos: declaresTodos, reports: true }
}

function inferTodosFromContract(contract: Record<string, unknown> | null): SkillTodoSpec[] {
  const capability = inferOutputCapabilitiesFromContract(contract)
  if (!capability.todos) return []
  return [{
    kind: 'dispatch',
    title: '运行时 output.todos 待办输出',
    summary: 'contract.json 已声明 todos；scripts/main.py 返回 todos 后会进入收件待办。',
    reviewer_role: 'operator',
    sla_hours: 24,
    decision_mode: 'any_of',
    payload_fields: ['output'],
    tasks: [{ content: '以运行结果 todos[].tasks 为准' }],
  }]
}

export { documentToMarkdown, markdownToDocument }
