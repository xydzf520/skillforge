/**
 * v2.9.0+：推荐从 aggregate barrel import；新 call 点优先走 `usePublishing`.
 * 本 composable 仍作为内部实现保留，aggregate 复用它来承载提交审核逻辑。
 *   new: import { ... } from "@/composables/usePublishing"
 */
import { ref, type Ref } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'
import { getErrorMessage } from '@/types/skillstudio'
import type { PublishReadinessReport, SkillStudioReviewApi, SkillStudioSkillApi } from '@/types/skillstudio'

/**
 * v2.5: handleSubmitReview 抽到 composable。
 * 原 Studio 内 46 行，含：validate → readiness gate → reviewApi.create → refreshAfterSubmitReview。
 * validationResult / publishReadinessReport 两个 ref 仍由 Studio 侧的 composable 持有（用 ref 注入）。
 */
export function useStudioReviewSubmit(deps: {
  // v2.6: 细分依赖
  wb: { skillId: string }
  studioDoc: { isCreate: Ref<boolean>; doc: Ref<{ meta?: { name?: string } } | null | undefined> }
  ui: { bottomPanelOpen: boolean; bottomPanelTab: string }
  skillApi: SkillStudioSkillApi
  reviewApi: SkillStudioReviewApi
  validationResult: Ref<unknown>
  publishReadinessReport: Ref<PublishReadinessReport | null>
  refreshAfterSubmitReview: () => Promise<unknown> | unknown
}) {
  const { wb, studioDoc, ui, skillApi, reviewApi, validationResult, publishReadinessReport, refreshAfterSubmitReview } = deps
  const submittingReview = ref(false)

  function confirmForceSubmit(title: string, issues: string[]): Promise<boolean> {
    const issueText = issues.length ? issues.map((item, index) => `${index + 1}. ${item}`).join('\n') : '存在未明确归因的提交前问题。'
    return new Promise((resolve) => {
      Modal.warning({
        title,
        content: `当前问题：\n${issueText}\n\n建议先继续修改并重新校验；如果你确认这些问题不影响本次审核，可以强制提交，审核记录会保留这些问题和强制提交标记。`,
        okText: '强制提交审核',
        cancelText: '继续修改',
        width: 520,
        onOk: () => resolve(true),
        onCancel: () => resolve(false),
      })
    })
  }

  async function handleSubmitReview() {
    if (!wb.skillId) return
    if (submittingReview.value) return
    submittingReview.value = true
    let forceSubmit = false
    const forceReasons: string[] = []
    try {
      validationResult.value = await skillApi.validate(wb.skillId)
      const validationBlocks = Array.isArray((validationResult.value as Record<string, unknown>)?.blocks)
        ? (validationResult.value as Record<string, unknown>).blocks as Array<Record<string, unknown>>
        : []
      const failedBlocks = validationBlocks.filter((block) => block?.status === 'fail')
      if ((validationResult.value as Record<string, unknown>)?.valid === false || failedBlocks.length > 0) {
        ui.bottomPanelOpen = true
        ui.bottomPanelTab = 'validation'
        const labels = failedBlocks
          .map(block => String(block.message || block.name || '未知问题'))
          .filter(Boolean)
        const confirmed = await confirmForceSubmit(
          `提交审核前校验未通过：${failedBlocks.length || '存在'} 个问题`,
          labels,
        )
        if (!confirmed) return
        forceSubmit = true
        forceReasons.push(`基础校验未通过：${labels.join('；') || `${failedBlocks.length || '存在'} 个问题`}`)
      }

      publishReadinessReport.value = await skillApi.publishReadiness(wb.skillId, false)
      const gate = publishReadinessReport.value?.review_gate
      if (publishReadinessReport.value && (gate?.can_submit_review === false || !publishReadinessReport.value.can_publish)) {
        ui.bottomPanelOpen = true
        ui.bottomPanelTab = 'validation'
        const failed = (gate?.items || []).filter(item => item.severity === 'block' && !item.passed)
        const labels = failed.map(item => `${item.label || item.key || '门禁项'}：${item.detail || item.suggestion || '未通过'}`).filter(Boolean)
        const confirmed = await confirmForceSubmit(
          `质量门禁未通过：${publishReadinessReport.value.blocker_count || failed.length || 0} 个阻断问题`,
          labels,
        )
        if (!confirmed) return
        forceSubmit = true
        forceReasons.push(labels.length ? `质量门禁未通过：${labels.join('；')}` : `质量门禁未通过：${publishReadinessReport.value.blocker_count || 0} 个阻断问题`)
      }

      await reviewApi.create({
        skill_id: wb.skillId,
        change_type: studioDoc.isCreate.value ? 'new_skill' : 'update',
        diff_summary: `${studioDoc.doc.value?.meta?.name || wb.skillId} 提交审核`,
        reason: 'SkillStudio 提交审核',
        force_submit: forceSubmit,
        force_reason: forceReasons.join('；'),
      })
      Message.success('已提交审核')
      await refreshAfterSubmitReview()
    } catch (error) {
      Message.error(getErrorMessage(error, '提交审核失败'))
    } finally {
      submittingReview.value = false
    }
  }

  return { submittingReview, handleSubmitReview }
}
