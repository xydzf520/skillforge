/**
 * v2.3.3: Studio 讲解包 + 审核驳回修复 的独立 composable
 *
 * 从 SkillStudio.vue 抽出：
 *   - explainPack / explainPackLoading state
 *   - loadExplainPack(): 拉 /api/skills/{id}/explain-pack
 *   - handleExplainToReview(): 讲解模式切到审核 + 触发提审
 *   - handleAskAIFix({reasons}): 审核驳回后切编辑 + 自动发修复 prompt
 *
 * 不包含 handleSubmitReview 本体（它耦合 validationResult / publishReadinessReport
 * 两个跨 composable 状态，留在 SkillStudio.vue 里更稳）。
 */
import { ref } from 'vue'

export interface StudioExplainDeps {
  // v2.6: 细分依赖
  wb: any  // skillId
  studioDoc: any  // isCreate (ComputedRef)
  ui: { assistantPaneOpen: boolean } | any
  skillApi: { explainPack: (id: string) => Promise<any> } | any
  /** 切换 studio perspective（由 SkillStudio.vue 提供） */
  applyStudioPerspective: (name: string) => void
  /** 触发提交审核（SkillStudio.vue 里的 handleSubmitReview） */
  submitReview: () => void | Promise<void>
  /** 发送 chat prompt 到 AssistantPane（SkillStudio.vue 里的 handleSend） */
  sendChat: (prompt: string, opts?: Record<string, unknown>) => void
}

export function useStudioExplain(deps: StudioExplainDeps) {
  const explainPack = ref<Record<string, unknown> | null>(null)
  const explainPackLoading = ref(false)

  async function loadExplainPack() {
    if (!deps.wb.skillId || deps.studioDoc.isCreate.value) {
      explainPack.value = null
      return
    }
    explainPackLoading.value = true
    try {
      explainPack.value = await deps.skillApi.explainPack(deps.wb.skillId)
    } catch (error) {
      // eslint-disable-next-line no-console
      console.warn('[explain-pack] load failed', error)
      explainPack.value = null
    } finally {
      explainPackLoading.value = false
    }
  }

  function handleExplainToReview() {
    deps.applyStudioPerspective('review')
    void deps.submitReview()
  }

  function handleAskAIFix(payload: { reasons: string[] }) {
    deps.applyStudioPerspective('edit')
    deps.ui.assistantPaneOpen = true
    const reasonList = (payload.reasons || []).filter(Boolean)
    const prompt = reasonList.length
      ? `审核被驳回，请根据以下反馈修复 Skill：\n${reasonList.map((r, i) => `${i + 1}. ${r}`).join('\n')}`
      : '审核被驳回，请检查并修复 Skill 中的问题。'
    deps.sendChat(prompt, { intent: 'fix' })
  }

  return {
    explainPack,
    explainPackLoading,
    loadExplainPack,
    handleExplainToReview,
    handleAskAIFix,
  }
}
