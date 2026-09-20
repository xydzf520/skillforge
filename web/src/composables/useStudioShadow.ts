/**
 * v2.9.0+：推荐从 aggregate barrel import；新 call 点优先走 `usePublishing`.
 * 本 composable 仍作为内部实现保留，aggregate 复用它来承载 shadow 流程。
 *   new: import { ... } from "@/composables/usePublishing"
 */
/**
 * v2.4.0 Studio 影子运行（Shadow）composable
 *
 * 从 SkillStudio.vue 抽出：
 *   - shadowReport / shadowComparisons state
 *   - loadShadowReport / loadShadowComparisons
 *   - handleStartShadow / handleStopShadow / handlePromoteShadow
 *   - handleRecordHumanDecision：人工决策回填
 */
import { ref } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'

export interface StudioShadowDeps {
  wb: any
  skillApi: any
}

export function useStudioShadow(deps: StudioShadowDeps) {
  const shadowReport = ref<Record<string, unknown> | null>(null)
  const shadowComparisons = ref<Record<string, unknown>[]>([])

  async function loadShadowReport() {
    if (!deps.wb.skillId) return
    try { shadowReport.value = await deps.skillApi.shadowReport(deps.wb.skillId) }
    catch { /* 静默 */ }
  }

  async function loadShadowComparisons() {
    if (!deps.wb.skillId) return
    try {
      const r = await deps.skillApi.shadowComparisons(deps.wb.skillId)
      shadowComparisons.value = Array.isArray(r) ? r : (r.comparisons || [])
    } catch { shadowComparisons.value = [] }
  }

  async function handleRecordHumanDecision(payload: { run_id: string; action: string }) {
    if (!deps.wb.skillId || !payload?.run_id) return
    try {
      const { default: request } = await import('@/api/request')
      await request.post(`/skills/${deps.wb.skillId}/shadow/human-record`, {
        run_id: payload.run_id,
        human_action: payload.action,
      })
      Message.success('已记录')
      await loadShadowComparisons()
    } catch (e) {
      Message.error(String((e as Record<string, unknown>)?._message || '记录失败'))
    }
  }

  async function handleStartShadow() {
    if (!deps.wb.skillId) return
    try {
      await deps.skillApi.startShadow(deps.wb.skillId)
      Message.success('影子运行已启动')
      await loadShadowReport()
    } catch (e) {
      Message.error(String((e as Record<string, unknown>)?._message || '启动影子运行失败'))
    }
  }

  async function handleStopShadow() {
    if (!deps.wb.skillId) return
    try {
      await deps.skillApi.stopShadow(deps.wb.skillId)
      Message.success('影子运行已停止')
      await loadShadowReport()
    } catch (e) {
      Message.error(String((e as Record<string, unknown>)?._message || '停止影子运行失败'))
    }
  }

  async function handlePromoteShadow() {
    if (!deps.wb.skillId) return
    Modal.confirm({
      title: '将影子版本推正？',
      content: '推正后影子版本会成为正式版本，执行于生产流量。',
      okText: '推正',
      cancelText: '取消',
      onOk: async () => {
        try {
          await deps.skillApi.promoteShadow(deps.wb.skillId)
          Message.success('已推正')
          await loadShadowReport()
        } catch (e) {
          Message.error(String((e as Record<string, unknown>)?._message || '推正失败'))
        }
      },
    })
  }

  return {
    shadowReport,
    shadowComparisons,
    loadShadowReport,
    loadShadowComparisons,
    handleRecordHumanDecision,
    handleStartShadow,
    handleStopShadow,
    handlePromoteShadow,
  }
}
