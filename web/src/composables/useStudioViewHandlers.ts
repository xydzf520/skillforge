import type { Router } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import type { SkillDocument, StructuredSkillResponse } from '@/types/skill'
import {
  getErrorMessage,
  type ChatOptions,
  type SkillStudioSelectionRange,
  type SkillStudioSkillApi,
  type SkillStudioWorkbenchApi,
  type StudioDocBridge,
  type StudioWbBridge,
} from '@/types/skillstudio'
import { markdownToDocument } from '@/utils/markdownParser'

type TemplateRecord = { skill_id?: string; id?: string; name?: string }

/**
 * v2.6+: Studio 剩余 6 个小 view-handler 合并到一处，最后一轮把 SkillStudio.vue 降到 900 以内。
 *
 * 覆盖：
 *   - handleBlueprintConfirm（Blueprint 4 步引导确认 / 导入 markdown）
 *   - handleApplyTemplate（加载模板 Skill 的内容）
 *   - handleGoModule（9 宫格 → block 模式 + 切模块）
 *   - handleOpenCanvas（打开 workflow 画布）
 *   - handleInlinePrompt（Cmd+K 内联 prompt 分发给 chat）
 *   - loadReferences（初始化引用列表）
 *
 * 这 6 件 deps 差异大（router / skillApi / workbenchApi 都要），但放一处至少比分散 6 个
 * 独立 composable 开销低。
 */
export function useStudioViewHandlers(deps: {
  wb: StudioWbBridge & {
    setSkillDocument?: (doc: SkillDocument) => void
    setReferences?: (refs: unknown[]) => void
  }
  studioDoc: StudioDocBridge
  ui: { viewMode: string; setViewMode: (m: string) => void; inlinePromptVisible: boolean; studioActiveModule: string }
  router: Router
  skillApi: SkillStudioSkillApi
  workbenchApi: SkillStudioWorkbenchApi
  /** 当前选中的编辑器范围（Inline Prompt 用） */
  currentSelection: { value: SkillStudioSelectionRange | null }
  /** 向 AI 助手发送指令 */
  sendChat: (prompt: string, options?: ChatOptions) => Promise<unknown> | unknown
}) {
  const { wb, studioDoc, ui, router, skillApi, workbenchApi, currentSelection, sendChat } = deps

  function handleBlueprintConfirm(skill: SkillDocument & { _rawMarkdown?: string }): void {
    if (skill._rawMarkdown) {
      // 导入模式：直接解析 markdown
      const parsed = markdownToDocument(skill._rawMarkdown)
      if (parsed) wb.setSkillDocument?.(parsed)
    } else {
      wb.setSkillDocument?.(skill)
    }
    studioDoc.setActiveModule('overview')
    Message.success('Skill 骨架已生成，请编辑完善')
  }

  async function handleApplyTemplate(tpl: TemplateRecord): Promise<void> {
    if (!tpl?.skill_id && !tpl?.id) return
    try {
      const data = await skillApi.get(tpl.skill_id || tpl.id || '')
      const { fromSkillResponse } = await import('@/utils/schemaMapper')
      const mapped = fromSkillResponse(data as StructuredSkillResponse)
      if (mapped) {
        wb.setSkillDocument?.(mapped)
        studioDoc.setActiveModule('goal')
        Message.success(`已加载模板: ${tpl.name || tpl.skill_id}`)
      }
    } catch (error) {
      Message.error(getErrorMessage(error, '加载模板失败'))
    }
  }

  // 九宫格 → block 视图 + 切模块
  function handleGoModule(mod: string): void {
    if (ui.viewMode === 'grid') ui.setViewMode('block')
    studioDoc.setActiveModule(mod)
  }

  function handleOpenCanvas(): void {
    const wf = (studioDoc.doc.value as { workflow?: { name?: string; playbook_name?: string } } | null | undefined)?.workflow
    const name = wf?.name || wf?.playbook_name || (wb.skillId ? `${wb.skillId}-workflow` : '')
    if (name) router.push(`/playbook/${name}/edit`)
    else Message.warning('请先生成工作流')
  }

  function handleInlinePrompt(prompt: unknown): void {
    ui.inlinePromptVisible = false
    sendChat(String(prompt), {
      targetModule: ui.studioActiveModule,
      selectionRange: currentSelection.value,
      intent: 'rewrite_selection',
    })
  }

  async function loadReferences(): Promise<void> {
    try {
      const r = await workbenchApi.listReferences({
        skill_id: wb.skillId || undefined,
        source_type: 'all',
        module: 'all',
        reference_mode: 'all',
      })
      wb.setReferences?.(r.items || [])
    } catch { /* 静默 */ }
  }

  return {
    handleBlueprintConfirm,
    handleApplyTemplate,
    handleGoModule,
    handleOpenCanvas,
    handleInlinePrompt,
    loadReferences,
  }
}
