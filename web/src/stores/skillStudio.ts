/**
 * v2.8.3 B2: Skill Studio 统一 store facade
 *
 * 对外单一入口 `useSkillStudio()`，内部组合 workbench / ui / document 三个已有 store。
 * 现有代码仍可直接用三个老 store；新代码建议用本 facade。
 *
 * 消灭多源状态：
 * - `isDirty`（原 `studioDirty` 是多源计算）= documentStore.dirtyModules.size > 0
 * - `isCreate` = ui.studioMode === 'create'
 * - `isReadOnly` = !editMode || lockedByOther
 */
import { computed } from 'vue'
import { useWorkbenchStore } from './workbench'
import { useUIStore } from './ui'
import { useDocumentStore } from './document'

export function useSkillStudio() {
  // v2.11.0：去掉 as any，用 Pinia 的精确 Store 类型
  const workbench = useWorkbenchStore()
  const ui = useUIStore()
  const document = useDocumentStore()

  // 组合属性：统一语义，消灭下游手工拼接
  const isDirty = computed(() => (document.dirtyModules?.size ?? 0) > 0)
  const isCreate = computed(() => ui.studioMode === 'create')
  // editMode 实际在 useSkillStudioSession 而非 ui store；此处保留名字但取占位，
  // 真实值由 SkillStudio.vue 里的 session 结构覆盖
  const isReadOnly = computed(() => !((ui as unknown as Record<string, unknown>).editMode))
  const skillId = computed(() => workbench.skillId)
  const skillLoaded = computed(() => !!workbench.studioLoaded)
  const saving = computed(() => !!workbench.studioSaving)

  return {
    // 子 store（直接暴露，SkillStudio.vue 可以 .session.xxx / .ui.xxx / .content.xxx）
    session: workbench,
    ui,
    content: document,

    // 聚合属性（单一真相源）
    isDirty,
    isCreate,
    isReadOnly,
    skillId,
    skillLoaded,
    saving,
  }
}

export type SkillStudioFacade = ReturnType<typeof useSkillStudio>
