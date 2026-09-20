export type SkillModuleName =
  | 'meta'
  | 'goal'
  | 'rules'
  | 'params'
  | 'output_table'
  | 'todos'
  | 'antipatterns'
  | 'data_inputs'
  | 'test_cases'
  | 'workflow'

export interface SkillRuleBranch {
  condition?: string
  conclusion?: string
  action?: string
  next_step?: string | null
}

export interface SkillRuleStep {
  id?: string
  name?: string
  description?: string
  branches?: SkillRuleBranch[]
}

export interface SkillParam {
  name?: string
  default_value?: string | number | boolean | null
  default_value_str?: string | null
  value?: string | number | boolean | null
  description?: string
}

export interface SkillMeta {
  id?: string
  name?: string
  description?: string
  department?: string
  owner?: string
  status?: string
  version?: string
  role?: string
  trigger_type?: string
  trigger_expression?: string
  risk_level?: string
  approval_level?: string | number
  [key: string]: unknown
}

export interface SkillOutputField {
  name?: string
  field?: string
  label?: string
  format?: string
  recipient?: string
  approval_level?: string | number
}

export interface SkillTodoTaskSpec {
  executor?: string
  content?: string
  deadline?: string
  extra?: Record<string, unknown>
}

export interface SkillTodoSpec {
  kind?: 'review' | 'dispatch' | string
  title?: string
  summary?: string
  reviewer_role?: string
  reviewers?: string[]
  sla_hours?: number | null
  decision_mode?: 'any_of' | 'all_of' | 'independent' | string
  payload_fields?: string[]
  tasks?: SkillTodoTaskSpec[]
}

export interface SkillTestCase {
  name?: string
  input?: Record<string, unknown>
  input_data?: Record<string, unknown>
  expected_output?: Record<string, unknown>
  assert_rules?: string[]
}

export interface SkillAntipattern {
  scenario?: string
  correct_action?: string
  source?: string
  [key: string]: unknown
}

export interface SkillDataInput {
  name?: string
  source?: string
  frequency?: string
  [key: string]: unknown
}

export interface WorkflowNode {
  id?: string
  type?: string
  position?: { x: number; y: number }
  data?: Record<string, unknown>
  [key: string]: unknown
}

export interface WorkflowEdge {
  id?: string
  source?: string
  target?: string
  type?: string
  data?: Record<string, unknown>
  [key: string]: unknown
}

export interface WorkflowBinding {
  source?: string
  target?: string
  field?: string
  [key: string]: unknown
}

export interface SkillWorkflowPreview {
  nodes?: WorkflowNode[]
  edges?: WorkflowEdge[]
  bindings?: WorkflowBinding[]
  diff_preview?: Record<string, unknown>
  summary?: string
  status?: string
  updated_at?: string
  [key: string]: unknown
}

export interface SkillCustomSections {
  __artifacts?: Record<string, string>
  __task_contract?: Record<string, unknown>
  __task_contract_meta?: Record<string, unknown>
  __task_gate?: Record<string, unknown>
  [key: string]: unknown
}

export interface SkillDocument {
  meta?: SkillMeta
  goal?: string
  rules?: SkillRuleStep[]
  params?: SkillParam[]
  output_table?: SkillOutputField[]
  todos?: SkillTodoSpec[]
  test_cases?: SkillTestCase[]
  antipatterns?: SkillAntipattern[]
  data_inputs?: SkillDataInput[]
  workflow?: SkillWorkflowPreview
  custom_sections?: SkillCustomSections
}

export interface SkillManifestScript {
  path: string
  purpose?: string
  bytes?: number
}

export interface SkillManifest {
  skill_id: string
  path: string
  total_files: number
  key_files: Record<string, boolean>
  script_count: number
  reference_count: number
  data_file_count: number
  scripts: SkillManifestScript[]
  references: string[]
  data_files: string[]
  other_files: string[]
  all_files: string[]
}

export interface StructuredSkillResponse {
  id?: string
  name?: string
  department?: string
  role?: string
  description?: string
  trigger_type?: string
  risk_level?: string
  approval_level?: number
  structured?: Record<string, unknown>
  parsed?: Record<string, unknown>
  policy_pack?: Record<string, unknown>
  workflow?: SkillWorkflowPreview
  other_files?: Record<string, string>
}
