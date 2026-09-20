/**
 * v2.11.0 V211-2：Skill Studio 里散落的事件 handler 集中 composable
 *
 * 把 SkillStudio.vue 里 3 个 handler 函数抽出：
 *   - handleBlockUpdate（block 编辑 + Coach 埋点）
 *   - handleSectionChange（navigator section 切换 + 按需加载）
 *   - handleBottomTabChange（bottom panel tab 切换 + 按需加载）
 *
 * 这些函数共享一组 deps（studioDoc / ui / wb / 多个 load 方法），集中后可减少
 * SkillStudio.vue 里 50+ 行 script 内容。
 */
import type { Ref } from 'vue'
import { silentWarn } from '@/utils/errorBoundary'
import type { UIStoreLike, WorkbenchStoreLike, StudioDocLike } from '@/types/studioComposableDeps'

type EditableModuleKey = 'meta' | 'goal' | 'rules' | 'params' | 'output_table' | 'test_cases' | 'workflow'
type ParamChange = { param_name: string; old_value: unknown; new_value: unknown }

export interface StudioHandlersDeps {
  wb: WorkbenchStoreLike
  studioDoc: StudioDocLike
  ui: UIStoreLike
  commitHistory: Ref<unknown[]>
  fileList: Ref<unknown[]>
  shadowReport: Ref<unknown | null>
  shadowDivergenceRate: Ref<unknown | null>
  loadHistory: () => Promise<void> | void
  loadFileList: () => Promise<void> | void
  loadShadowReport: () => Promise<void> | void
  loadShadowDivergenceRate: () => Promise<void> | void
  triggerCoachEvent: (event: string, context?: unknown) => Promise<unknown> | unknown
  trackAndCheckRevert: (key: string, newValue: unknown) => void
}

export function useStudioHandlers(deps: StudioHandlersDeps) {
  function handleBlockUpdate(key: EditableModuleKey, value: unknown) {
    // 先检测参数变化（在 store 更新之前捕获 old value）
    let paramChange: ParamChange | null = null
    if (key === 'params' && Array.isArray(value)) {
      const old = (deps.studioDoc.doc?.value?.params || []) as Array<{ name: string; default_value: unknown }>
      for (const np of value as Array<{ name: string; default_value: unknown }>) {
        const op = old.find((p) => p.name === np.name)
        if (op && String(op.default_value) !== String(np.default_value)) {
          paramChange = {
            param_name: np.name,
            old_value: op.default_value,
            new_value: np.default_value,
          }
          break
        }
      }
    }
    deps.studioDoc.updateModule(key, value)

    // Coach 埋点
    if (key === 'rules') {
      const maybe = deps.triggerCoachEvent('branch_changed') as Promise<unknown>
      if (maybe && typeof maybe.catch === 'function') {
        maybe.catch((e: unknown) => silentWarn(e, 'coach.branch_changed'))
      }
    } else if (key === 'params' && paramChange) {
      const maybe = deps.triggerCoachEvent('param_changed', paramChange) as Promise<unknown>
      if (maybe && typeof maybe.catch === 'function') {
        maybe.catch((e: unknown) => silentWarn(e, 'coach.param_changed'))
      }
      deps.trackAndCheckRevert(`param:${paramChange.param_name}`, paramChange.new_value)
    }
  }

  function handleSectionChange(section: unknown) {
    deps.ui.navigatorSection = section as string
    if (section === 'versions' && deps.wb.skillId && !deps.commitHistory.value.length) {
      deps.loadHistory()
    }
    if (section === 'files' && deps.wb.skillId && !deps.fileList.value.length) {
      deps.loadFileList()
    }
  }

  function handleBottomTabChange(tab: unknown) {
    deps.ui.bottomPanelTab = tab as string
    if (tab === 'history' && deps.wb.skillId && !deps.commitHistory.value.length) {
      deps.loadHistory()
    }
    if (tab === 'shadow' && deps.wb.skillId && !deps.shadowReport.value) {
      deps.loadShadowReport()
    }
    if (tab === 'shadow' && deps.wb.skillId && !deps.shadowDivergenceRate.value) {
      deps.loadShadowDivergenceRate()
    }
  }

  return { handleBlockUpdate, handleSectionChange, handleBottomTabChange }
}
