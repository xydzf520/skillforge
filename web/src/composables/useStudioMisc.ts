/**
 * v2.9.1 杂项 Studio composable barrel
 *
 * 把 4 个功能独立但各自很小（52 ~ 121 行）的 composable 通过一个 barrel
 * 导出，让 SkillStudio.vue 的 import 行数再缩一点。
 *
 * 本 barrel **不做 aggregate facade**（它们之间无 dep 依赖，强行聚合反而
 * 引入伪耦合），只做 re-export：
 *
 *   import { useSkillStudioRecent, useStudioDraft, useStudioBlocks, useStudioViewHandlers }
 *     from '@/composables/useStudioMisc'
 *
 * 等价于原来 4 条 import 语句。
 */
export { useSkillStudioRecent } from './useSkillStudioRecent'
export { useStudioDraft } from './useStudioDraft'
export { useStudioBlocks } from './useStudioBlocks'
export { useStudioViewHandlers } from './useStudioViewHandlers'
