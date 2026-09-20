/**
 * v2.9.0 Skill Studio 观测聚合 composable
 *
 * 把 Validation + CommitHistory + Telemetry 三个 composable 聚合。
 * Validation 产出 `validationResult` 给 Publishing.review 用（上层调用方把
 * 本聚合的 `.validation.validationResult` 传给 useSkillPublishing）。
 */
import { useStudioValidation } from './useStudioValidation'
import { useStudioCommitHistory } from './useStudioCommitHistory'
import { useSkillStudioTelemetry } from './skillstudio/useSkillStudioTelemetry'
import type { ObservabilityMegaDeps } from '@/types/studioComposableDeps'

// v2.9.0：re-export 三个原子 composable，让调用方二选一
export { useStudioValidation, useStudioCommitHistory, useSkillStudioTelemetry }

export type StudioObservabilityDeps = ObservabilityMegaDeps

export function useStudioObservability(deps: StudioObservabilityDeps) {
  // 调用顺序：Validation（pure） → Telemetry（provides loadHealthScore） → CommitHistory（needs it）
  const validation = useStudioValidation({
    wb: deps.wb,
    studioDoc: deps.studioDoc,
    ui: deps.ui,
    runningTasks: deps.runningTasks,
    skillApi: deps.skillApi,
    testApi: deps.testApi,
    executionApi: deps.executionApi,
  })
  const telemetry = useSkillStudioTelemetry({
    wb: deps.wb,
    studioDoc: deps.studioDoc,
    ui: deps.ui,
    workbenchApi: deps.workbenchApi,
    skillApi: deps.skillApi,
    handleCommand: deps.handleCommand,
    handleSend: deps.handleSend,
    runLintCheck: deps.runLintCheck,
  })
  const history = useStudioCommitHistory({
    wb: deps.wb,
    studioDoc: deps.studioDoc,
    skillApi: deps.skillApi,
    loadFileList: deps.loadFileList,
    // 内部读：loadHealthScore 来自同聚合的 telemetry
    loadHealthScore: () => telemetry.loadHealthScore(),
  })
  return { validation, history, telemetry }
}
