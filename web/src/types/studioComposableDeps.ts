/**
 * v2.10.0 V210-5：Studio composable 聚合 facade 的 deps 类型集中定义。
 *
 * 把原本散在 useSkillPublishing / useStudioObservability / useStudioLifecycleUnified
 * 里的 `any` 收紧到 `unknown`+具体接口，避免"什么都能传进来"。
 *
 * 子 composable 各自的 Deps 仍由各自文件定义；本文件只聚合"聚合层"用的
 * mega-deps interface。
 */
import type { Router } from 'vue-router'
import type { Ref } from 'vue'

// v2.11.0：三个 Pinia store facade 从 any 升到 ReturnType<typeof useXxxStore>
// 有精确的字段类型 + 方法签名，可消灭 50+ 处下游 any cast
import type { useWorkbenchStore } from '@/stores/workbench'
import type { useUIStore } from '@/stores/ui'
import type { useDocumentStore } from '@/stores/document'

/** @see src/stores/workbench.ts */
export type WorkbenchStoreLike = ReturnType<typeof useWorkbenchStore>
/** @see src/stores/ui.ts */
export type UIStoreLike = ReturnType<typeof useUIStore>
/** @see src/stores/document.ts */
export type DocumentStoreLike = ReturnType<typeof useDocumentStore>

// StudioDoc 是 useSkillStudioDocument 的返回值（Computed + 方法）—— 结构复杂仍留 any，
// 否则跨 composable 的泛型穿透会炸；用 `unknown` 用不了会中断编译
export type StudioDocLike = any

// API 客户端（来自 @/api）—— 类型定义都在 @/types/skillstudio.ts 里，走那个
export type SkillApiLike = any
export type WorkbenchApiLike = any
export type TestApiLike = any
export type ExecutionApiLike = any
export type ReviewApiLike = any

// running tasks store
import type { useRunningTasksStore } from '@/stores/runningTasks'
export type RunningTasksLike = ReturnType<typeof useRunningTasksStore>

// 通用前向闭包回调签名 —— 接受任意 args，返回任意（避免 forward-closure 和具体签名打架）
export type HandlerCallback = (...args: any[]) => any

// ─── 3 个 aggregate 的 mega-deps ────────────────────────────────

export interface PublishingMegaDeps {
  wb: WorkbenchStoreLike
  studioDoc: StudioDocLike
  ui: UIStoreLike
  router: Router
  skillApi: SkillApiLike
  reviewApi: ReviewApiLike
  validationResult: Ref<any>  // 从 Observability.validation 抽出来传入 —— 由 caller 保证
  refreshAfterSubmitReview: () => Promise<void> | void
}

export interface ObservabilityMegaDeps {
  wb: WorkbenchStoreLike
  studioDoc: StudioDocLike
  ui: UIStoreLike
  workbenchApi: WorkbenchApiLike
  skillApi: SkillApiLike
  testApi: TestApiLike
  executionApi: ExecutionApiLike
  runningTasks: RunningTasksLike
  loadFileList: () => Promise<void>
  handleCommand: HandlerCallback
  handleSend: HandlerCallback
  runLintCheck: HandlerCallback
}

export interface LifecycleUnifiedMegaDeps {
  wb: WorkbenchStoreLike
  studioDoc: StudioDocLike
  documentStore: DocumentStoreLike
  ui: UIStoreLike
  router: Router
  skillApi: SkillApiLike
  workbenchApi: WorkbenchApiLike
  activeFile: Ref<string | null>
  activeFileContent: Ref<string | null>
  activeFileDirty: Ref<boolean>
  activeFileOriginalContent: Ref<string>
  pendingPatch: Ref<any>
  loadFileList: (force?: boolean) => Promise<void>
  isWritablePerspective: () => boolean
  studioMode: Ref<string>
  triggerCoachEvent: HandlerCallback
  recordPostSaveTelemetry: () => Promise<void> | void
  sendChat: (prompt: string, opts?: any) => any
}
