/**
 * skillToFlow — 将后端 flow-data 转换为 Vue Flow 的 nodes + edges
 *
 * 修复 (Codex 审查):
 * - B1: 节点用 stepId 而非数组 index 作为稳定 ID
 * - H1: 分支状态只存在 step 节点，conclusion 节点为只读视图
 * - H3: 全部安全访问，空数据不崩溃
 */
import { autoLayout } from './layoutEngine'

interface FlowBranch {
  condition?: string
  conclusion?: string
  action?: string
  next_step?: string | number | null
  [key: string]: unknown
}

interface FlowStep {
  id: string | number
  name?: string
  description?: string
  branches?: FlowBranch[]
}

interface FlowDataInput {
  name?: string
  [key: string]: unknown
}

interface FlowScript {
  name: string
  [key: string]: unknown
}

interface FlowOutput {
  name?: string
  [key: string]: unknown
}

interface FlowFrontmatter {
  approval_level?: string
  target_users?: unknown[]
  trigger_type?: string
  [key: string]: unknown
}

interface FlowData {
  steps?: FlowStep[]
  data_inputs?: FlowDataInput[]
  scripts?: FlowScript[]
  output_definition?: FlowOutput[]
  policy_pack?: Record<string, unknown>
  frontmatter?: FlowFrontmatter
}

type SkillFlowNode = {
  id: string
  type?: string
  position: { x: number; y: number }
  data?: Record<string, unknown>
}

type SkillFlowEdge = {
  id?: string
  source: string
  target: string
  type?: string
  label?: string
  animated?: boolean
  data?: Record<string, unknown>
}

function conclusionColor(text: string | null | undefined): string {
  const t = (text || '').toLowerCase()
  if (t.includes('绿') || t.includes('green') || t.includes('适用') || t.includes('完备') || t.includes('成功') || t.includes('完成')) return 'green'
  if (t.includes('黄') || t.includes('yellow') || t.includes('维持')) return 'yellow'
  if (t.includes('红') || t.includes('red') || t.includes('暂停') || t.includes('失败') || t.includes('不适用')) return 'red'
  return 'blue'
}

/**
 * @param {Object} flowData
 * @returns {{ nodes: Array, edges: Array }}
 */
export function skillToFlow(flowData: FlowData | null | undefined): { nodes: SkillFlowNode[]; edges: SkillFlowEdge[] } {
  if (!flowData) return { nodes: [], edges: [] }

  const nodes: SkillFlowNode[] = []
  const edges: SkillFlowEdge[] = []
  const steps: FlowStep[] = flowData.steps || []
  const data_inputs: FlowDataInput[] = flowData.data_inputs || []
  const output_definition: FlowOutput[] = flowData.output_definition || []
  const scripts: FlowScript[] = flowData.scripts || []
  const policy_pack = flowData.policy_pack || {}
  const frontmatter = flowData.frontmatter || {}

  // 用于 next_step 校验的 step id 集合
  const validStepIds = new Set(steps.map((s) => String(s.id)))

  // ── 1. 数据源节点 ──
  data_inputs.forEach((input, i) => {
    const name = input.name || `数据源${i + 1}`
    nodes.push({
      id: `ds-${name}`,  // B1: 用名称作为稳定 ID
      type: 'dataSource',
      position: { x: 0, y: 0 },
      data: { ...input, _srcName: name },
    })
  })

  // ── 2. 参数节点 ──
  if (Object.keys(policy_pack).length > 0) {
    nodes.push({
      id: 'params',
      type: 'param',
      position: { x: 0, y: 0 },
      data: { params: policy_pack },
    })
  }

  // ── 3. 决策步骤 + 结论节点 ──
  steps.forEach((step, si) => {
    const stepNodeId = `step-${step.id}`
    const branches: FlowBranch[] = step.branches || []

    // H1: 分支数据只存在 step 节点的 data.branches 里（单一真源）
    nodes.push({
      id: stepNodeId,
      type: 'decisionStep',
      position: { x: 0, y: 0 },
      data: {
        id: step.id,
        name: step.name || '',
        description: step.description || '',
        branches: branches.map((b) => ({ ...b })),
      },
    })

    // 数据源/参数 → 第一个步骤
    if (si === 0) {
      data_inputs.forEach((input) => {
        const dsId = `ds-${input.name || '数据源'}`
        edges.push({ id: `e-${dsId}-${stepNodeId}`, source: dsId, target: stepNodeId, type: 'condition', data: { style: 'dataInput' } })
      })
      if (Object.keys(policy_pack).length > 0) {
        edges.push({ id: `e-params-${stepNodeId}`, source: 'params', target: stepNodeId, type: 'condition', data: { style: 'param' } })
      }
    }

    // 步骤顺序连线
    const hasExplicitNext = branches.some((b) => b.next_step)
    if (si < steps.length - 1 && !hasExplicitNext) {
      edges.push({
        id: `e-seq-${step.id}-${steps[si + 1].id}`,
        source: stepNodeId, target: `step-${steps[si + 1].id}`,
        type: 'condition', data: { style: 'flow', label: '下一步' },
      })
    }

    // 分支 → 只读结论视图节点
    branches.forEach((branch, bi) => {
      const conclusionId = `conclusion-${step.id}-b${bi}`
      nodes.push({
        id: conclusionId,
        type: 'conclusion',
        position: { x: 0, y: 0 },
        data: {
          conclusion: branch.conclusion || '',
          action: branch.action || '',
          next_step: branch.next_step || null,
          color: conclusionColor(branch.conclusion),
          // H1: 只读引用，编辑走 step 节点的 branches
          _readOnly: true,
          _sourceStepId: step.id,
          _sourceBranchIndex: bi,
        },
      })

      edges.push({
        id: `e-branch-${step.id}-b${bi}`,
        source: stepNodeId, target: conclusionId,
        type: 'condition',
        label: (branch.condition || '').slice(0, 40),
        data: { style: 'branch', color: conclusionColor(branch.conclusion) },
      })

      // H4: next_step 校验 — 只连向存在的步骤
      if (branch.next_step && validStepIds.has(String(branch.next_step))) {
        edges.push({
          id: `e-next-${step.id}-b${bi}`,
          source: conclusionId, target: `step-${branch.next_step}`,
          type: 'condition', animated: true,
          data: { style: 'flow', label: `→ step ${branch.next_step}` },
        })
      }
    })
  })

  // ── 4. 脚本节点 ──
  scripts.forEach((script) => {
    const scriptId = `script-${script.name}`  // B1: 用文件名作为稳定 ID
    nodes.push({
      id: scriptId, type: 'script',
      position: { x: 0, y: 0 },
      data: { ...script },
    })
    if (steps.length > 0) {
      edges.push({ id: `e-${scriptId}`, source: scriptId, target: `step-${steps[0].id}`, type: 'condition', data: { style: 'script' } })
    }
  })

  // ── 5. 输出节点 ──
  output_definition.forEach((out) => {
    const outName = out.name || '输出'
    const outputId = `output-${outName}`  // B1: 用名称作为稳定 ID
    nodes.push({
      id: outputId, type: 'output',
      position: { x: 0, y: 0 },
      data: { ...out, _outName: outName },
    })
    if (steps.length > 0) {
      edges.push({ id: `e-out-${outName}`, source: `step-${steps[steps.length - 1].id}`, target: outputId, type: 'condition', data: { style: 'output' } })
    }
  })

  // ── 6. 审批节点 ──
  if (frontmatter.approval_level || frontmatter.target_users) {
    nodes.push({
      id: 'approval', type: 'approval',
      position: { x: 0, y: 0 },
      data: { level: frontmatter.approval_level || 'L0', targets: frontmatter.target_users || [], trigger_type: frontmatter.trigger_type || 'manual' },
    })
    output_definition.forEach((out) => {
      edges.push({ id: `e-appr-${out.name || 'out'}`, source: `output-${out.name || '输出'}`, target: 'approval', type: 'condition', data: { style: 'approval' } })
    })
  }

  // ── 7. 自动布局 ──
  const { nodes: layoutedNodes } = autoLayout(nodes, edges, { direction: 'LR', rankSep: 100, nodeSep: 50 })
  return { nodes: layoutedNodes, edges }
}
