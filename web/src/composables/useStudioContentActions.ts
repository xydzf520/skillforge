/**
 * v2.9.0+：推荐从 aggregate barrel import；新 call 点优先走 `useLifecycleUnified`.
 * 本 composable 仍作为内部实现保留，aggregate 复用它来承载内容动作逻辑。
 *   new: import { ... } from "@/composables/useLifecycleUnified"
 */
import { ref, type Ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { getErrorMessage } from '@/types/skillstudio'
import type { SkillDocument, SkillRuleStep } from '@/types/skill'
import type { ChatOptions, SkillStudioSkillApi, SkillStudioWorkbenchApi, WorkbenchPatchDraft } from '@/types/skillstudio'

type OutputPreviewData = { output_text?: string; dingtalk_card_html?: string; message?: string } | null

/**
 * v2.5: Studio 四件小事件处理器合并到一个 composable。
 * - handlePreviewOutput（输出+钉钉卡片预览，带 loading/visible/data 三 state）
 * - handleValidateAntipattern（单条反例验证）
 * - handleNodeAIAction（§4.4 节点级 AI 动作，6 按钮分发给 chat）
 * - handleApplyPendingPatch（workbench patch 应用 + dirty 清理）
 *
 * 这四件都属于"小 handler 调 API + toast 反馈"类型，单独抽 composable 开销大于收益；
 * 合并后 Studio 侧 script 省 ~80 行，outputPreview 的 state 也随之下沉。
 */
export function useStudioContentActions(deps: {
  // v2.6: 细分依赖
  wb: {
    skillId: string
    setSkillDocument?: (doc: SkillDocument) => void
    appendMessage?: (role: string, content: string) => void
  }
  studioDoc: { doc: Ref<SkillDocument | null | undefined> }
  documentStore: { dirty: boolean; dirtyModules: Set<string> }
  ui: { assistantPaneOpen: boolean; toggleAssistant: () => void }
  skillApi: SkillStudioSkillApi
  workbenchApi: SkillStudioWorkbenchApi
  pendingPatch: Ref<WorkbenchPatchDraft | null>
  sendChat: (prompt: string, options?: ChatOptions) => Promise<unknown> | unknown
}) {
  const { wb, studioDoc, documentStore, ui, skillApi, workbenchApi, pendingPatch, sendChat } = deps

  const outputPreviewVisible = ref(false)
  const outputPreviewData = ref<OutputPreviewData>(null)
  const outputPreviewLoading = ref(false)
  const applyingPatch = ref(false)

  async function handlePreviewOutput() {
    if (!wb.skillId) { Message.warning('请先打开 Skill'); return }
    outputPreviewLoading.value = true
    outputPreviewVisible.value = true
    try {
      const r = await skillApi.previewOutput(wb.skillId) as Record<string, unknown>
      outputPreviewData.value = {
        output_text: (r.output_text as string) || (r.rendered_text as string) || '',
        dingtalk_card_html: (r.dingtalk_card_html as string) || (r.card_html as string) || '',
        message: (r.message as string) || '',
      }
    } catch (e) {
      outputPreviewData.value = null
      Message.error(getErrorMessage(e, '输出预览失败'))
      outputPreviewVisible.value = false
    } finally {
      outputPreviewLoading.value = false
    }
  }

  async function handleValidateAntipattern(data: { scenario: string; expected_action: string }) {
    if (!wb.skillId) { Message.warning('请先打开 Skill'); return }
    if (!data?.scenario) { Message.warning('未找到反例内容，请将光标置于反例区块内'); return }
    try {
      const r = await skillApi.validateAntipattern(wb.skillId, {
        scenario: data.scenario,
        expected_action: data.expected_action,
      }) as Record<string, unknown>
      if (r.valid || r.passed) {
        Message.success(`反例验证通过：${(r.message as string) || '当前反例逻辑正确'}`)
      } else {
        Message.error(`反例验证失败：${(r.message as string) || (r.reason as string) || '请检查反例描述'}`)
      }
    } catch (e) {
      Message.error(getErrorMessage(e, '反例验证失败'))
    }
  }

  async function handleNodeAIAction({ action, nodeId }: { action: string; nodeId: string }) {
    if (!nodeId) {
      Message.warning('请先选中一个节点')
      return
    }
    const step: SkillRuleStep | undefined = studioDoc.doc.value?.rules?.find((r) => r.id === nodeId)
    if (!step) return

    const stepDesc = `步骤「${step.name || nodeId}」` +
      (step.branches?.length ? `（${step.branches.length} 条分支）` : '')
    const prompts: Record<string, string> = {
      'complete-branches': `为${stepDesc}补全缺失的 else / 兜底分支，使其覆盖所有输入情况。`,
      'extract-param': `将${stepDesc}条件中的硬编码数值提取为可配置参数，给每个参数起个有意义的名字。`,
      'generate-boundary-test': `围绕${stepDesc}的条件边界值，生成 3 个典型测试用例（等于边界、略小、略大）。`,
      'generate-counter-example': `为${stepDesc}生成 2 个容易误判的反例场景（antipattern）。`,
      'explain-to-business': `用非技术语言解释${stepDesc}在做什么、为什么这么判。`,
      'fix-failed-test': `分析最近${stepDesc}失败的测试用例，给出具体修复建议。`,
    }
    const prompt = prompts[action]
    if (!prompt) return

    if (!ui.assistantPaneOpen) ui.toggleAssistant()
    await sendChat(prompt, { targetModule: 'rules', selectionRange: null, isCommand: false })
  }

  async function handleApplyPendingPatch() {
    if (!pendingPatch.value?.patch_id || !wb.skillId) return
    applyingPatch.value = true
    try {
      const applied = await workbenchApi.applyPatch(wb.skillId, {
        patch_id: pendingPatch.value.patch_id, confirm: true,
      })
      if (applied.skill) wb.setSkillDocument?.(applied.skill)
      documentStore.dirty = false
      documentStore.dirtyModules.clear()
      wb.appendMessage?.('assistant', `已应用变更。${applied.changed_files?.length ? '文件: ' + applied.changed_files.join(', ') : ''}`)
      pendingPatch.value = null
      Message.success('变更已应用')
    } catch (e) { Message.error(String((e as Record<string, unknown>)?._message || '应用失败')) }
    finally { applyingPatch.value = false }
  }

  return {
    outputPreviewVisible,
    outputPreviewData,
    outputPreviewLoading,
    applyingPatch,
    handlePreviewOutput,
    handleValidateAntipattern,
    handleNodeAIAction,
    handleApplyPendingPatch,
  }
}
