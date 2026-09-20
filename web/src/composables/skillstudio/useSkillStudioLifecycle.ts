/**
 * v2.9.0+：推荐从 aggregate barrel import；新 call 点优先走 `useLifecycleUnified`.
 * 本 composable 仍作为内部实现保留，aggregate 复用它来承载页面生命周期主控流。
 *   new: import { ... } from "@/composables/useLifecycleUnified"
 */
import { onBeforeUnmount, onMounted } from 'vue'
import type { RouteLocationNormalizedLoaded } from 'vue-router'
import {
  getBackendErrorCode,
  getErrorMessage,
  getErrorStatus,
  type StudioDocBridge,
  type StudioWbBridge,
} from '@/types/skillstudio'

/**
 * v2.6: SkillStudio 页面生命周期主控流。
 *
 * 从 SkillStudio.vue 抽出：
 *   - onMounted：编辑模式 initForEdit（含 404/500 落地 SkillNotFound 短路）+
 *     并行启动 telemetry/files/history/coach/guardian/health/lock +
 *     restoreCachedTaskResults + autoResumeChat + markVisited；
 *     /skills/new 创建模式 initForCreate + 默认 block 视图；
 *     query 参数恢复（panel / bottom / view / perspective / nav）
 *   - onBeforeUnmount：persistToStorage + releaseLock（draft save + route-leave 由 useStudioDraft 托管）
 *
 * deps 总数 20+ —— 这是 Studio 主控流的固有耦合，抽出来虽然 deps 长但让 Studio 的
 * 核心 setup 更容易阅读（只剩 composable 声明 + facade 组装 + 模板）。
 */
export function useSkillStudioLifecycle(deps: {
  route: RouteLocationNormalizedLoaded
  wb: StudioWbBridge
  studioDoc: StudioDocBridge
  ui: {
    restoreFromStorage: () => void
    persistToStorage: () => void
    assistantPaneOpen: boolean
    toggleBottomPanel: (tab?: string) => void
    setViewMode: (mode: string) => void
    studioPerspective: string
    navigatorSection: string
  }
  chatStarted: { value: boolean }
  editMode: { value: boolean }
  canEditPerspective: { value: boolean }
  loadError: { value: null | 'not-found' | 'load-error' }
  loadErrorMessage: { value: string }
  notFoundSkillId: { value: string }
  applyStudioPerspective: (name: string, options?: { syncRoute?: boolean }) => void
  initializeSkillTelemetry: () => void
  loadFileList: (force?: boolean) => Promise<unknown>
  loadHistory: () => Promise<unknown>
  triggerCoachEvent: (event: string) => Promise<unknown> | unknown
  loadGuardianReport: () => Promise<unknown> | unknown
  loadHealthScore: () => Promise<unknown> | unknown
  checkLockStatus: () => Promise<unknown> | unknown
  releaseLock: () => Promise<unknown> | unknown
  restoreCachedTaskResults: () => void
  autoResumeChatIfPending: () => Promise<unknown> | unknown
  restoreDraft: () => void
  loadReferences: () => Promise<unknown> | unknown
  studioRecent: { markVisited: (item: { id: string; name: string }) => void }
}) {
  onMounted(async () => {
    deps.ui.restoreFromStorage()

    const id = typeof deps.route.params.id === 'string' ? deps.route.params.id : ''
    if (id) {
      try {
        await deps.studioDoc.initForEdit?.(id)
      } catch (error: unknown) {
        // B2: skill 加载失败（404 / 403 / 5xx / 网络错）→ 一律显示 SkillNotFound 落地页
        const status = getErrorStatus(error)
        const backendCode = getBackendErrorCode(error)
        deps.notFoundSkillId.value = id
        if (status === 404 || backendCode === 'SKILL_NOT_FOUND') {
          deps.loadError.value = 'not-found'
        } else {
          deps.loadError.value = 'load-error'
          deps.loadErrorMessage.value = getErrorMessage(error, '加载 Skill 失败')
        }
        return
      }
      deps.initializeSkillTelemetry()
      // 初始化后立即加载文件列表和版本历史（不等用户切 tab）
      deps.loadFileList()
      deps.loadHistory()
      // Coach: 打开编辑器时主动给建议
      deps.triggerCoachEvent('open_editor')
      // Guardian: 加载运行时告警（异常 + 跨 Skill 冲突）
      deps.loadGuardianReport()
      // 加载 5 维健康评分（顶栏徽章）
      deps.loadHealthScore()
      // 主动查锁状态：被他人占用时顶栏显示"XX 编辑中"
      deps.checkLockStatus()
      // 从任务坞缓存恢复沙箱 / 测试结果（跨页面回来 or 刷新后）
      deps.restoreCachedTaskResults()
      // 刷新场景：若有 _wasStreaming 的 AI 回复，发 resume 把历史+live 补回来
      try { await deps.autoResumeChatIfPending() } catch { /* 静默 */ }
      // 写入"最近编辑"列表给 SkillList 用
      if (deps.wb.skillId && !deps.studioDoc.isCreate.value) {
        deps.studioRecent.markVisited({
          id: deps.wb.skillId,
          name: deps.studioDoc.doc.value?.meta?.name || deps.wb.skillId,
        })
      }
    } else {
      // /skills/new 创建模式
      deps.studioDoc.initForCreate?.()
      deps.chatStarted.value = false
      deps.editMode.value = true
      deps.ui.setViewMode('block')
    }
    deps.loadReferences()

    // 从 query 恢复面板状态
    const q = deps.route.query
    const queryPanel = Array.isArray(q.panel) ? q.panel[0] : q.panel
    const queryBottom = Array.isArray(q.bottom) ? q.bottom[0] : q.bottom
    const queryView = Array.isArray(q.view) ? q.view[0] : q.view
    const queryPerspective = Array.isArray(q.perspective) ? q.perspective[0] : q.perspective
    const queryNav = Array.isArray(q.nav) ? q.nav[0] : q.nav
    if (queryPanel === 'assistant') deps.ui.assistantPaneOpen = true
    if (queryBottom) deps.ui.toggleBottomPanel(String(queryBottom))
    if (queryView) deps.ui.setViewMode(String(queryView))
    if (queryPerspective) deps.applyStudioPerspective(String(queryPerspective), { syncRoute: false })
    else if (deps.studioDoc.isCreate.value) deps.applyStudioPerspective('edit', { syncRoute: false })
    else if (!deps.canEditPerspective.value && deps.ui.studioPerspective === 'edit') {
      deps.applyStudioPerspective('review', { syncRoute: false })
    }
    if (queryNav) deps.ui.navigatorSection = String(queryNav)

    // 恢复未保存的 draft（刷新防丢失）
    deps.restoreDraft()
  })

  onBeforeUnmount(() => {
    deps.ui.persistToStorage()
    // draft save + route-leave 由 useStudioDraft 托管
    deps.releaseLock()
  })
}
