/**
 * flowToSkill — Vue Flow nodes → AST JSON
 *
 * 修复 (Codex 审查):
 * - B1: 用 stepId/名称定位而非数组 index
 * - B2: 分离持久化 — 只回写 SKILL.md 可表达的部分，policy_pack 单独保存
 * - H1: 分支数据只从 decisionStep 节点读取（conclusion 节点是只读视图）
 * - H4: next_step 校验 — 只允许指向已有的 step id
 */

/**
 * @param {Array} nodes - Vue Flow nodes
 * @param {Object} originalFlowData - 原始 flow-data
 * @returns {{ ast: Object, policy_pack: Object|null }} 分离的 AST 和参数
 */
type FlowNode = {
  type?: string
  data?: any
}

export function flowToSkill(nodes: FlowNode[], originalFlowData: Record<string, any> | null | undefined): { ast: Record<string, any>; policy_pack: Record<string, any> | null } {
  if (!originalFlowData) return { ast: {}, policy_pack: null }

  const ast = JSON.parse(JSON.stringify(originalFlowData))

  // 收集所有 step id 用于 next_step 校验
  const validStepIds = new Set((ast.steps || []).map((s: Record<string, string>) => s.id))
  let policyPackUpdated = null

  for (const node of nodes) {
    const d = node.data
    if (!d) continue

    // 数据源 — 用 _srcName 定位
    if (node.type === 'dataSource' && d._srcName) {
      const idx = (ast.data_inputs || []).findIndex((di: any) => di.name === d._srcName)
      if (idx >= 0) {
        ast.data_inputs[idx] = {
          name: d.name || d._srcName,
          source: d.source || '',
          frequency: d.frequency || '',
        }
      }
    }

    // 决策步骤 — 用 step.id 定位（H1: 唯一分支数据来源）
    if (node.type === 'decisionStep' && d.id) {
      const step = (ast.steps || []).find((s: any) => s.id === d.id)
      if (step) {
        step.name = d.name || step.name
        step.description = d.description || step.description || ''
        if (Array.isArray(d.branches)) {
          step.branches = d.branches.map((b: any) => ({
            condition: b.condition || '',
            conclusion: b.conclusion || '',
            action: b.action || '',
            // H4: next_step 校验
            next_step: (b.next_step && validStepIds.has(String(b.next_step))) ? b.next_step : null,
          }))
        }
      } else {
        // 新增步骤 — addStep 创建的
        ast.steps.push({
          id: d.id,
          name: d.name || '新步骤',
          description: d.description || '',
          branches: (d.branches || []).map((b: any) => ({
            condition: b.condition || '',
            conclusion: b.conclusion || '',
            action: b.action || '',
            next_step: (b.next_step && validStepIds.has(String(b.next_step))) ? b.next_step : null,
          })),
        })
        validStepIds.add(d.id)
      }
    }

    // conclusion 节点：H1 跳过，不回写（只读视图）

    // 参数 — B2: 单独输出，不混入 AST
    if (node.type === 'param' && d.params) {
      policyPackUpdated = { ...d.params }
    }

    // 输出 — 用 _outName 定位
    if (node.type === 'output' && d._outName) {
      const idx = (ast.output_definition || []).findIndex((o: any) => o.name === d._outName)
      if (idx >= 0) {
        ast.output_definition[idx] = {
          name: d.name || d._outName,
          format: d.format || '',
          recipient: d.recipient || '',
          approval_level: d.approval_level || '',
        }
      }
    }
  }

  return { ast, policy_pack: policyPackUpdated }
}
