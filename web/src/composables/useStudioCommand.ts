import { ref, type Ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import type { SkillStudioSkillApi } from '@/types/skillstudio'

/**
 * v2.5: 抽出 SkillStudio 命令面板分发（~75 行 → composable）。
 * 包含 drift / antipattern 两个 modal 的 loading + results + visibility 状态。
 * 'validate' / 'test' / 'sandbox' 三个命令回调由外部注入（它们已经被各自 composable 管理）。
 */
export function useStudioCommand(deps: {
  // v2.6: 细分依赖
  wb: { skillId: string; appendMessage?: (role: string, content: string) => void }
  studioDoc: { setActiveModule: (key: string) => void }
  ui: { commandPaletteOpen: boolean; toggleBottomPanel: (tab?: string) => void; setEditorTheme: (t: string) => void; editorTheme: string; toggleFullscreen: () => void }
  skillApi: SkillStudioSkillApi
  handleValidate: () => Promise<unknown> | unknown
  handleRunTest: () => Promise<unknown> | unknown
  openMultiRun: () => void
  toggleMermaid: () => void
  toggleFlow: () => void
}) {
  const { wb, studioDoc, ui, skillApi, handleValidate, handleRunTest, openMultiRun, toggleMermaid, toggleFlow } = deps

  const driftModalVisible = ref(false)
  const driftResults: Ref<import('@/types/skillstudio').DriftCheckResponse | null> = ref(null)
  const driftLoading = ref(false)

  const antipatternModalVisible = ref(false)
  const antipatternResults: Ref<import('@/types/skillstudio').DiscoverAntipatternsResponse | null> = ref(null)
  const antipatternLoading = ref(false)

  async function handleCommand(command: string) {
    ui.commandPaletteOpen = false
    const map: Record<string, () => Promise<unknown> | unknown> = {
      'generate-tests': async () => {
        const r = await skillApi.generateTests(wb.skillId, { strategy: 'comprehensive' })
        wb.appendMessage?.('assistant', `已生成 ${r.count || 0} 个测试用例。`)
      },
      'discover-antipatterns': async () => {
        antipatternLoading.value = true
        antipatternModalVisible.value = true
        try {
          const r = await skillApi.discoverAntipatterns(wb.skillId, 30)
          antipatternResults.value = r
          wb.appendMessage?.('assistant', `发现 ${r.discovered?.length || 0} 个潜在反例，已在弹窗中展示。`)
        } catch {
          antipatternResults.value = null
          antipatternModalVisible.value = false
        } finally {
          antipatternLoading.value = false
        }
      },
      'suggest-branches': async () => {
        const r = await skillApi.suggestBranches(wb.skillId, {})
        wb.appendMessage?.('assistant', `建议 ${r.branches?.length || 0} 个新分支。`)
      },
      'drift-check': async () => {
        driftLoading.value = true
        driftModalVisible.value = true
        try {
          const r = await skillApi.driftCheck(wb.skillId)
          driftResults.value = r
          const count = r.drifts?.length || 0
          wb.appendMessage?.('assistant', count ? `检测到 ${count} 个参数漂移，已在弹窗中展示。` : '未检测到参数漂移。')
          if (!count) driftModalVisible.value = false
        } catch {
          driftResults.value = null
          driftModalVisible.value = false
        } finally {
          driftLoading.value = false
        }
      },
      'derive-thresholds': async () => {
        const r = await skillApi.deriveThresholds(wb.skillId, {})
        const count = r.thresholds?.length || r.params?.length || 0
        wb.appendMessage?.('assistant', count ? `已推导 ${count} 个阈值参数。请在参数模块查看。` : '未推导出新阈值。')
        if (count) studioDoc.setActiveModule('params')
      },
      'run-aiclaw': async () => {
        const r = await skillApi.runOnAiclaw(wb.skillId)
        wb.appendMessage?.('assistant', `AIClaw 执行完成。\n\`\`\`json\n${JSON.stringify(r.output || r, null, 2)}\n\`\`\``)
      },
      'validate': () => handleValidate(),
      'test': () => { ui.toggleBottomPanel('test'); handleRunTest() },
      'sandbox': () => ui.toggleBottomPanel('sandbox'),
      'multi-run': () => openMultiRun(),
      'batch-create': async () => {
        const input = window.prompt('批量创建 Skill（每行: skill-id | name | department）:')
        if (!input?.trim()) return
        const skills = input.split('\n').map(line => {
          const [skill_id, name, department] = line.split('|').map(s => s.trim())
          return { skill_id, name: name || skill_id, department: department || '未指定' }
        }).filter(s => s.skill_id)
        if (!skills.length) return
        try {
          const r = await skillApi.batchCreate({ skills })
          Message.success(`批量创建完成: ${r.created || skills.length} 个`)
        } catch (e) { Message.error(String((e as Record<string, unknown>)?._message || '批量创建失败')) }
      },
      'toggle-mermaid': () => toggleMermaid(),
      'toggle-flow': () => toggleFlow(),
      'toggle-theme': () => ui.setEditorTheme(ui.editorTheme === 'dark' ? 'light' : 'dark'),
      'fullscreen': () => ui.toggleFullscreen(),
    }
    if (!wb.skillId && command !== 'validate') return
    try { await map[command]?.() } catch (e) { Message.error(String((e as Record<string, unknown>)._message || `命令执行失败: ${command}`)) }
  }

  return {
    driftModalVisible,
    driftResults,
    driftLoading,
    antipatternModalVisible,
    antipatternResults,
    antipatternLoading,
    handleCommand,
  }
}
