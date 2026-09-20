/**
 * v2.9.0+：推荐从 aggregate barrel import；新 call 点优先走 `useObservability`.
 * 本 composable 仍作为内部实现保留，aggregate 复用它来承载遥测/健康逻辑。
 *   new: import { ... } from "@/composables/useObservability"
 */
import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { silentWarn } from '@/utils/errorBoundary'
import {
  computeBranchCoverage,
  emptyCoverageDocument,
  getErrorMessage,
  type CoachSuggestion,
  type GuardianItem,
  type HealthScore,
  type ShadowDivergenceRate,
  type SkillStudioTelemetryOptions,
} from '@/types/skillstudio'

export function useSkillStudioTelemetry(options: SkillStudioTelemetryOptions) {
  const { wb, studioDoc, ui, workbenchApi, skillApi, handleCommand, handleSend, runLintCheck } = options

  const coachSuggestions = ref<CoachSuggestion[]>([])
  const guardianItems = ref<GuardianItem[]>([])
  const guardianLoading = ref(false)
  const healthScore = ref<HealthScore>(null)
  const healthLoading = ref(false)
  const shadowDivergenceRate = ref<ShadowDivergenceRate>(null)
  const revertHistory = ref<Record<string, string[]>>({})
  const editSessionStart = ref<number | null>(null)
  const openCoverage = ref<number | null>(null)

  function initializeSkillTelemetry() {
    editSessionStart.value = Date.now()
    openCoverage.value = computeBranchCoverage(studioDoc.doc.value ?? emptyCoverageDocument())
  }

  async function loadHealthScore() {
    if (!wb.skillId || studioDoc.isCreate.value) return
    healthLoading.value = true
    try {
      healthScore.value = await skillApi.healthScore(wb.skillId)
    } catch {
      healthScore.value = null
    } finally {
      healthLoading.value = false
    }
  }

  async function loadShadowDivergenceRate() {
    if (!wb.skillId || studioDoc.isCreate.value) return
    try {
      shadowDivergenceRate.value = await skillApi.shadowDivergenceRate(wb.skillId)
    } catch {
      shadowDivergenceRate.value = null
    }
  }

  async function triggerCoachEvent(event: string, context: Record<string, unknown> = {}) {
    if (!wb.skillId) return
    try {
      const response = await workbenchApi.coachEvent(wb.skillId, event, context)
      coachSuggestions.value = response.suggestions || []
      const important = coachSuggestions.value.filter((item) => ['high', 'critical'].includes(item.severity ?? ''))
      if (important.length && !wb.messages?.length) {
        wb.appendMessage?.('assistant', `分析完成，发现 ${important.length} 项重要改进：\n${important.map((item) => `• ${item.title}`).join('\n')}`)
      }
    } catch {
      // ignore coach failures
    }
  }

  function trackAndCheckRevert(location: string, newValue: unknown) {
    const history = revertHistory.value[location] || []
    const normalized = String(newValue)
    history.push(normalized)
    if (history.length > 10) history.shift()
    revertHistory.value[location] = history

    if (history.length >= 3) {
      const counts: Record<string, number> = {}
      history.forEach((value) => {
        counts[value] = (counts[value] || 0) + 1
      })
      const maxRepeat = Math.max(...Object.values(counts))
      if (maxRepeat >= 2) {
        triggerCoachEvent('reverted_repeatedly', {
          location,
          revert_count: history.length,
          last_values: history,
        }).catch((e: unknown) => silentWarn(e, 'studio.telemetry'))
        revertHistory.value[location] = []
      }
    }
  }

  function handleCoachAction(suggestion: CoachSuggestion) {
    const actionMap: Partial<Record<string, () => void | unknown>> = {
      complete_branches: () => { studioDoc.setActiveModule('rules') },
      extract_param: () => handleCommand('suggest-branches'),
      generate_tests: () => handleCommand('generate-tests'),
      generate_counter_examples: () => handleCommand('discover-antipatterns'),
      fix_lint: () => { ui.toggleBottomPanel('validation'); runLintCheck() },
      simulate_param: () => { studioDoc.setActiveModule('params') },
      analyze_failure: () => { ui.toggleBottomPanel('shadow') },
      suggest_alternatives: () => {
        if (!ui.assistantPaneOpen) ui.toggleAssistant()
        const location = suggestion.evidence?.location || '当前位置'
        const rawValues = Array.isArray(suggestion.evidence?.last_values) ? suggestion.evidence.last_values : []
        const values = rawValues.map((item) => String(item)).join(' → ')
        handleSend(
          `我在「${location}」反复改动（${values}），能给我 2-3 个替代方案吗？`,
          { targetModule: ui.studioActiveModule, selectionRange: null, isCommand: false },
        )
      },
      simulate_replay: () => {
        ui.toggleBottomPanel('shadow')
        Message.info('已打开回放面板，查看参数变化对历史样本的影响')
      },
    }
    const action = suggestion.action ? actionMap[suggestion.action] : undefined
    if (action) action()
    else Message.info(suggestion.description || suggestion.title || '')
    if (wb.skillId && suggestion.action) {
      workbenchApi.recordCoachAccepted?.(wb.skillId, suggestion.action).catch((e: unknown) => silentWarn(e, 'studio.telemetry'))
    }
  }

  function handleCoachDismiss(suggestion: CoachSuggestion) {
    coachSuggestions.value = coachSuggestions.value.filter((item) => item.id !== suggestion.id)
  }

  async function loadGuardianReport() {
    if (!wb.skillId) return
    guardianLoading.value = true
    try {
      const [anomalyResp, conflictResp, driftResp] = await Promise.all([
        skillApi.guardianAnomalies(wb.skillId, 24).catch(() => ({ anomalies: [] })),
        skillApi.guardianConflicts(wb.skillId, 'same_department').catch(() => ({ conflicts: [] })),
        skillApi.guardianDrift(wb.skillId, 24, 14).catch(() => ({ reports: [] })),
      ])
      guardianItems.value = [
        ...(anomalyResp.anomalies || []),
        ...(conflictResp.conflicts || []).map((item): GuardianItem => ({
          severity: typeof item.severity === 'string' ? item.severity : 'medium',
          dimension: 'conflict',
          segment: typeof item.category === 'string' ? item.category : '冲突',
          description: typeof item.description === 'string' ? item.description : (typeof item.message === 'string' ? item.message : '跨 Skill 冲突'),
        })),
        ...(driftResp.reports || []).map((item): GuardianItem => ({
          severity: typeof item.severity === 'string' ? item.severity : 'low',
          dimension: 'drift',
          segment: `字段 ${item.field}`,
          metric: 'kl_divergence',
          description: `${item.field} 分布偏移 KL=${item.kl_divergence}（基线 ${item.baseline_size} / 当前 ${item.current_size}）`,
          evidence: { top_shifts: item.top_shifts || [] },
        })),
      ]
    } finally {
      guardianLoading.value = false
    }
  }

  async function runGuardianRootCause() {
    if (!wb.skillId) return
    try {
      const response = await skillApi.guardianRootCause(wb.skillId)
      const reports = response.reports || []
      if (reports.length === 0) {
        Message.info(response.message || '未检测到需要归因的异常')
        return
      }
      const top = reports[0]
      const causesText = (top.causes || []).slice(0, 3)
        .map((item) => `• [${Math.round(item.probability * 100)}%] ${item.cause}`)
        .join('\n')

      const categoryLabels: Record<string, string> = {
        param_tunable: '参数可调',
        rule_missing_branch: '规则缺支路',
        data_source_issue: '数据源异常',
      }
      const patchesByCategory: Record<string, Array<{ description: string; risk?: string }>> = {}
      for (const patch of (top.candidate_patches || [])) {
        const category = patch.category || 'other'
        if (!patchesByCategory[category]) patchesByCategory[category] = []
        patchesByCategory[category].push(patch)
      }
      const patchesText = Object.entries(patchesByCategory)
        .map(([category, patches]) => {
          const label = categoryLabels[category] || category
          const lines = patches.map((patch) => `  ${patch.description}（风险：${patch.risk || 'low'}）`).join('\n')
          return `【${label}】\n${lines}`
        })
        .join('\n\n')

      const message = [
        `**根因分析：** ${top.summary || ''}`,
        causesText,
        patchesText ? `\n**候选修复建议：**\n${patchesText}` : '',
      ].filter(Boolean).join('\n')

      wb.appendMessage?.('assistant', message)
      if (!ui.assistantPaneOpen) ui.toggleAssistant()
    } catch (error) {
      Message.error(getErrorMessage(error, '根因分析失败'))
    }
  }

  async function recordPostSaveTelemetry() {
    if (!wb.skillId) return
    if (editSessionStart.value) {
      const elapsed = Math.round((Date.now() - editSessionStart.value) / 1000)
      workbenchApi.recordEditDuration?.(wb.skillId, elapsed).catch((e: unknown) => silentWarn(e, 'studio.telemetry'))
      editSessionStart.value = Date.now()
    }
    if (openCoverage.value != null) {
      const newCoverage = computeBranchCoverage(studioDoc.doc.value ?? emptyCoverageDocument())
      if (newCoverage !== openCoverage.value) {
        workbenchApi.recordCoverageDelta?.(wb.skillId, openCoverage.value, newCoverage).catch((e: unknown) => silentWarn(e, 'studio.telemetry'))
        openCoverage.value = newCoverage
      }
    }
  }

  return {
    coachSuggestions,
    guardianItems,
    guardianLoading,
    healthScore,
    healthLoading,
    shadowDivergenceRate,
    initializeSkillTelemetry,
    loadHealthScore,
    loadShadowDivergenceRate,
    triggerCoachEvent,
    trackAndCheckRevert,
    handleCoachAction,
    handleCoachDismiss,
    loadGuardianReport,
    runGuardianRootCause,
    recordPostSaveTelemetry,
  }
}
