/**
 * v2.9.0+：推荐从 aggregate barrel import；新 call 点优先走 `useLifecycleUnified`.
 * 本 composable 仍作为内部实现保留，aggregate 复用它来承载保存/创建流程。
 *   new: import { ... } from "@/composables/useLifecycleUnified"
 */
/**
 * v2.4.0 Studio Save/Create 流程 composable
 *
 * 从 SkillStudio.vue 抽出 handleSave / executeSave / handleCreate / fetchRegressionDiff
 * 及相关 diff 预览 / regression 弹窗状态。
 *
 * 依赖项通过 deps 传入，保持 composable 和 SkillStudio 的状态解耦。
 */
import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { documentToMarkdown } from '@/utils/markdownParser'
import { silentWarn } from '@/utils/errorBoundary'

export interface StudioSaveDeps {
  // v2.6: 细分依赖
  wb: any       // skillId
  studioDoc: any // isCreate / hasContent / doc (ComputedRef) + saveDirectEdits / createSkill
  ui: any
  router: any
  skillApi: any
  activeFile: { value: string | null } | any
  activeFileContent: { value: string | null } | any
  activeFileDirty: { value: boolean } | any
  activeFileOriginalContent: { value: string } | any
  loadFileList: (force?: boolean) => Promise<void>
  isWritablePerspective: () => boolean
  studioMode: { value: string } | any
  triggerCoachEvent: (event: string, context?: Record<string, unknown>) => void | Promise<void>
  recordPostSaveTelemetry: () => void | Promise<void>
}

function countDiffLines(a: string, b: string): number {
  const al = a.split(/\r?\n/)
  const bl = b.split(/\r?\n/)
  const maxLen = Math.max(al.length, bl.length)
  let diff = 0
  for (let i = 0; i < maxLen; i += 1) if ((al[i] ?? '') !== (bl[i] ?? '')) diff += 1
  return diff
}

export function useStudioSave(deps: StudioSaveDeps) {
  // 初始用空对象（非 null）：模板里 :original-content="diffData.original" 直接取属性，
  // 若 null 会在 showDiffPreview=false 时也抛错
  const diffData = ref<{ original: string; modified: string }>({ original: '', modified: '' })
  const showDiffPreview = ref(false)
  const regressionDiffData = ref<Record<string, unknown> | null>(null)
  const showRegressionDiff = ref(false)

  async function handleSave() {
    if (!deps.isWritablePerspective()) {
      Message.warning(deps.studioMode.value === 'explain' ? '讲解模式不支持保存' : '审批模式不支持保存')
      return
    }
    // 1) 活跃文件有未保存 diff → 只保存文件，不做整体 doc 保存
    if (
      deps.activeFileContent.value !== null &&
      deps.activeFileDirty.value &&
      deps.wb.skillId &&
      deps.activeFile.value
    ) {
      try {
        await deps.skillApi.saveFile(deps.wb.skillId, deps.activeFile.value, deps.activeFileContent.value)
        deps.activeFileOriginalContent.value = deps.activeFileContent.value
        deps.activeFileDirty.value = false
        Message.success(`已保存 ${deps.activeFile.value}`)
        await deps.loadFileList(true)
      } catch (error: any) {
        Message.error(error?._message || `保存文件失败: ${deps.activeFile.value}`)
      }
      return
    }

    // 2) Coach save_pending
    void deps.triggerCoachEvent('save_pending')

    // 3) 保存前 Diff 预览（非新建时对比 HEAD）
    if (deps.wb.skillId && !deps.studioDoc.isCreate.value) {
      try {
        const headContent = await deps.skillApi.readFileHead(deps.wb.skillId, 'SKILL.md')
        const headText = typeof headContent === 'string' ? headContent : headContent?.content || ''
        const currentText = documentToMarkdown(deps.studioDoc.doc.value) || ''
        if (headText && headText !== currentText) {
          const lines = countDiffLines(headText, currentText)
          if (lines > 3) {
            diffData.value = { original: headText, modified: currentText }
            showDiffPreview.value = true
            return
          }
        }
      } catch { /* HEAD 读取失败，直接保存 */ }
    }

    await executeSave()
  }

  async function executeSave() {
    if (!deps.isWritablePerspective()) return
    showDiffPreview.value = false
    try {
      await deps.studioDoc.saveDirectEdits()
      Message.success('已保存')
      if (deps.wb.skillId) {
        await deps.recordPostSaveTelemetry()
        fetchRegressionDiff().catch((e: unknown) => silentWarn(e, 'studio.regression_diff'))
      }
    } catch (error: any) {
      Message.error(error?._message || '保存失败')
    }
  }

  async function fetchRegressionDiff() {
    if (!deps.wb.skillId || deps.studioDoc.isCreate.value) return
    try {
      const diff = await deps.skillApi.regressionDiff(deps.wb.skillId, 'HEAD~1')
      regressionDiffData.value = diff
      const meaningful =
        diff?.has_changes &&
        ((diff.test_cases_affected?.length || 0) > 0 ||
          (diff.steps?.length || 0) > 0 ||
          diff.output_definition_changed)
      if (meaningful) {
        showRegressionDiff.value = true
      }
    } catch { /* 静默：HEAD~1 可能不存在 */ }
  }

  async function handleCreate() {
    if (!deps.studioDoc.hasContent.value) return
    const dept = deps.studioDoc.doc.value?.meta?.department
    if (!dept || !String(dept).trim()) {
      Message.warning('请先在基础信息里选择所属部门，再创建 Skill')
      deps.ui.setViewMode('block')
      return
    }
    try {
      const newId = await deps.studioDoc.createSkill()
      Message.success(`已创建：${newId}`)
      deps.router.replace(`/skills/${newId}`)
    } catch (error: any) {
      Message.error(error?._message || '创建失败')
    }
  }

  return {
    diffData,
    showDiffPreview,
    regressionDiffData,
    showRegressionDiff,
    handleSave,
    executeSave,
    fetchRegressionDiff,
    handleCreate,
  }
}
