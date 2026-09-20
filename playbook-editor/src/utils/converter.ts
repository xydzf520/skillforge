import type {
  CanvasLayout,
  ConditionValue,
  FlowExport,
  PlaybookEdge,
  PlaybookNode,
  PlaybookStep,
} from '../types'
import { getConditionLabel } from './condition'
import { autoLayout } from './layout'

interface NormalizedDependency {
  stepId: string
  condition?: ConditionValue
}

export function stepsToFlow(
  steps: PlaybookStep[] = [],
  canvasLayout: CanvasLayout = {},
): { nodes: PlaybookNode[]; edges: PlaybookEdge[] } {
  const nodes: PlaybookNode[] = steps.map((step, index) => ({
    id: step.id,
    type: 'skill',
    position: canvasLayout[step.id] || { x: 100, y: index * 120 },
    data: {
      stepId: step.id,
      skillId: step.skill_id || step.skill || '',
      label: step.name || step.skill_id || step.id,
      timeout: step.timeout || 300,
      onFailure: step.on_failure || 'terminate',
      paramsOverride: step.params_override || {},
      status: null,
    },
  }))

  const edges: PlaybookEdge[] = []
  steps.forEach((step) => {
    normalizeDeps(step.depends_on).forEach((dependency) => {
      edges.push({
        id: `e-${dependency.stepId}-${step.id}`,
        source: dependency.stepId,
        target: step.id,
        type: dependency.condition ? 'condition' : 'default',
        label: getConditionLabel(dependency.condition || null),
        data: { condition: dependency.condition || '' },
        animated: false,
      })
    })
  })

  const hasLayout = Object.keys(canvasLayout).length > 0
  return {
    nodes: hasLayout ? nodes : autoLayout(nodes, edges),
    edges,
  }
}

export function flowToSteps(nodes: PlaybookNode[], edges: PlaybookEdge[]): FlowExport {
  const canvasLayout: CanvasLayout = {}

  const steps = nodes.map((node) => {
    canvasLayout[node.id] = {
      x: Math.round(node.position.x),
      y: Math.round(node.position.y),
    }

    const inEdges = edges.filter((edge) => edge.target === node.id)
    let dependsOn: PlaybookStep['depends_on']

    if (inEdges.length === 0) {
      dependsOn = undefined
    } else if (inEdges.every((edge) => !edge.data?.condition)) {
      const stepIds = inEdges.map((edge) => edge.source)
      dependsOn = stepIds.length === 1 ? stepIds[0] : stepIds
    } else {
      dependsOn = inEdges.map((edge) => {
        const item: { step_id: string; condition?: ConditionValue } = { step_id: edge.source }
        if (edge.data?.condition) item.condition = edge.data.condition
        return item
      })
    }

    const step: PlaybookStep = {
      id: node.id,
      skill_id: node.data.skillId,
    }

    if (node.data.label && node.data.label !== node.data.skillId) step.name = node.data.label
    if (dependsOn) step.depends_on = dependsOn
    if (node.data.timeout && node.data.timeout !== 300) step.timeout = node.data.timeout
    if (node.data.onFailure && node.data.onFailure !== 'terminate') step.on_failure = node.data.onFailure
    if (node.data.paramsOverride && Object.keys(node.data.paramsOverride).length > 0) {
      step.params_override = node.data.paramsOverride
    }

    return step
  })

  return { steps, _canvas_layout: canvasLayout }
}

function normalizeDeps(dependencies: PlaybookStep['depends_on']): NormalizedDependency[] {
  if (!dependencies) return []
  if (typeof dependencies === 'string') return [{ stepId: dependencies }]
  if (!Array.isArray(dependencies)) return []

  return dependencies.map((dependency) => {
    if (typeof dependency === 'string') return { stepId: dependency }
    return {
      stepId: dependency.step_id || '',
      condition: dependency.condition || '',
    }
  })
}
