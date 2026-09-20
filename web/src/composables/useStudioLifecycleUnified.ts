/**
 * v2.9.0 Studio 生命周期聚合 composable
 *
 * Save + ContentActions 两条编辑向的 composable 聚合。
 * （`useSkillStudioLifecycle` 无返回值、只挂 route hook，放 SkillStudio.vue 里
 * 直接调就行，不进本 facade 以免返回值结构变复杂。）
 */
import { useStudioSave } from './useStudioSave'
import { useStudioContentActions } from './useStudioContentActions'
import type { LifecycleUnifiedMegaDeps } from '@/types/studioComposableDeps'

// v2.9.0：re-export 两个原子 composable + useSkillStudioLifecycle（route hook），组成完整生命周期工具箱
export { useStudioSave, useStudioContentActions }
export { useSkillStudioLifecycle } from './skillstudio/useSkillStudioLifecycle'

export type StudioLifecycleUnifiedDeps = LifecycleUnifiedMegaDeps

export function useStudioLifecycleUnified(deps: StudioLifecycleUnifiedDeps) {
  const save = useStudioSave({
    wb: deps.wb,
    studioDoc: deps.studioDoc,
    ui: deps.ui,
    router: deps.router,
    skillApi: deps.skillApi,
    activeFile: deps.activeFile,
    activeFileContent: deps.activeFileContent,
    activeFileDirty: deps.activeFileDirty,
    activeFileOriginalContent: deps.activeFileOriginalContent,
    loadFileList: deps.loadFileList,
    isWritablePerspective: deps.isWritablePerspective,
    studioMode: deps.studioMode,
    triggerCoachEvent: deps.triggerCoachEvent,
    recordPostSaveTelemetry: deps.recordPostSaveTelemetry,
  })
  const content = useStudioContentActions({
    wb: deps.wb,
    studioDoc: deps.studioDoc,
    documentStore: deps.documentStore,
    ui: deps.ui,
    skillApi: deps.skillApi,
    workbenchApi: deps.workbenchApi,
    pendingPatch: deps.pendingPatch,
    sendChat: deps.sendChat,
  })
  return { save, content }
}
