import type { Edge, Node, XYPosition } from '@xyflow/react'

export type FailureAction = 'terminate' | 'retry' | 'skip' | string
export type StepStatus =
  | 'completed'
  | 'success'
  | 'running'
  | 'pending'
  | 'waiting'
  | 'failed'
  | 'timeout'
  | 'skipped'
  | string

export interface StructuredConditionRule {
  source: string
  ref: string
  op: string
  value: string
}

export interface StructuredCondition {
  type: string
  children: StructuredConditionRule[]
  _preview?: string
}

export type ConditionValue = string | StructuredCondition | null

export interface ConditionDependency {
  step_id?: string
  condition?: ConditionValue
}

export type DependsOnValue = string | string[] | ConditionDependency[] | undefined

export interface PlaybookStep {
  id: string
  skill_id?: string
  skill?: string
  name?: string
  depends_on?: DependsOnValue
  condition?: ConditionValue
  timeout?: number
  on_failure?: FailureAction
  params_override?: Record<string, unknown>
  output_fields?: string[]
}

export type CanvasLayout = Record<string, XYPosition>

export interface PlaybookDocument {
  name?: string
  description?: string
  department?: string
  steps?: PlaybookStep[]
  _canvas_layout?: CanvasLayout
}

export interface SkillSummary {
  id: string
  name?: string
  department?: string
}

export interface LiveNodeStyle {
  status: StepStatus
  color?: string
  animation?: string
  borderStyle?: string
}

export interface PlaybookNodeData {
  [key: string]: unknown
  stepId: string
  skillId: string
  label: string
  timeout: number
  onFailure: FailureAction
  paramsOverride: Record<string, unknown>
  status: StepStatus | null
  statusColor?: string
  statusAnimation?: string
  borderStyle?: string
}

export interface PlaybookEdgeData {
  [key: string]: unknown
  condition: ConditionValue
}

export type PlaybookNode = Node<PlaybookNodeData, 'skill'>
export type PlaybookEdge = Edge<PlaybookEdgeData, 'condition' | 'default'>

export interface FlowExport {
  steps: PlaybookStep[]
  _canvas_layout: CanvasLayout
}

export interface HistoryState {
  nodes: PlaybookNode[]
  edges: PlaybookEdge[]
}

export interface ParentMessageMap {
  'load-playbook': PlaybookDocument
  'set-skills': SkillSummary[]
  'update-status': Record<string, StepStatus>
  'update-node-styles': Record<string, LiveNodeStyle>
  'update-condition': {
    edgeId?: string | null
    condition?: ConditionValue
  }
  'focus-element': {
    nodeId?: string | null
    edgeId?: string | null
  }
}

export interface IframeMessageMap {
  'playbook-changed': FlowExport
  'editor-ready': Record<string, never>
  'selection-changed': {
    nodeId?: string | null
    edgeId?: string | null
  }
  'edit-condition': {
    edgeId: string
    condition: ConditionValue
  }
  'validation-error': {
    errors: string[]
  }
}
