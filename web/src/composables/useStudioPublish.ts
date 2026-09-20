/**
 * v2.9.0+：推荐从 aggregate barrel import；新 call 点优先走 `usePublishing`.
 * 本 composable 仍作为内部实现保留，aggregate 复用它来承载发布相关逻辑。
 *   new: import { ... } from "@/composables/usePublishing"
 */
/**
 * W4-E v2.3.0: Studio publish/deprecate/lint 三组 handler 抽出
 *
 * 从 SkillStudio.vue 拆出独立单元（O13 进展）：
 *   - handlePublish: 发布前门禁检查 → batchPublish
 *   - handleDeprecate: 弹 Modal 确认 → deprecate API
 *   - runLintCheck: publishReadiness 质检
 *
 * 共享状态：publishReadinessReport / linting ref 也挪到这里。
 */
import { ref } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'

export interface StudioPublishDeps {
  wb: { skillId?: string | null } | any
  ui: { toggleBottomPanel: (tab?: string) => void; bottomPanelOpen: boolean; bottomPanelTab: string } | any
  router: { push: (path: string) => void } | any
  skillApi: {
    publishReadiness: (id: string, include_warnings?: boolean) => Promise<any>
    batchPublish: (ids: string[]) => Promise<any>
    deprecate: (id: string) => Promise<any>
  } | any
}

export function useStudioPublish(deps: StudioPublishDeps) {
  const publishReadinessReport = ref<any>(null)
  const linting = ref(false)

  async function runLintCheck() {
    if (!deps.wb.skillId) return
    linting.value = true
    try {
      publishReadinessReport.value = await deps.skillApi.publishReadiness(deps.wb.skillId, false)
      const r = publishReadinessReport.value
      if (r.can_publish) {
        Message.success(`质检通过${r.warning_count ? `（${r.warning_count} 个警告）` : ''}`)
      } else {
        Message.warning(`发现 ${r.blocker_count} 个阻断问题`)
      }
    } catch (error) {
      Message.error(String((error as Record<string, unknown>)?._message || '质检失败'))
    } finally {
      linting.value = false
    }
  }

  async function handlePublish() {
    if (!deps.wb.skillId) return
    try {
      const readiness = await deps.skillApi.publishReadiness(deps.wb.skillId, false)
      if (!readiness.can_publish) {
        Message.error(`质量门禁未通过：${readiness.blocker_count} 个阻断问题，请修复后重试`)
        deps.ui.toggleBottomPanel('validation')
        publishReadinessReport.value = readiness
        return
      }
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn('发布前门禁检查失败:', e)
    }
    try {
      await deps.skillApi.batchPublish([deps.wb.skillId])
      Message.success('已发布')
    } catch (e) {
      const errData = (e as Record<string, Record<string, unknown>>).data
      if (errData?.status === 'blocked') {
        Message.error(`发布被阻断：${errData.message}`)
      } else {
        Message.error(String((e as Record<string, unknown>)?._message || '发布失败'))
      }
    }
  }

  async function handleDeprecate() {
    if (!deps.wb.skillId) return
    Modal.confirm({
      title: '停用此 Skill？',
      content: '停用后该 Skill 不再执行，仍可随时恢复。',
      okText: '停用',
      cancelText: '取消',
      okButtonProps: { status: 'danger' },
      onOk: async () => {
        try {
          await deps.skillApi.deprecate(deps.wb.skillId)
          Message.success('已停用')
          deps.router.push('/')
        } catch (e) {
          Message.error(String((e as Record<string, unknown>)?._message || '停用失败'))
        }
      },
    })
  }

  return {
    publishReadinessReport,
    linting,
    runLintCheck,
    handlePublish,
    handleDeprecate,
  }
}
