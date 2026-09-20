/**
 * v2.4.0 Studio 块编辑（Block Validation）composable
 *
 * 从 SkillStudio.vue 抽出：
 *   - currentBlock / validationErrorCount / validationWarningCount state
 *   - handleCursorBlockChange：光标移动时按 block 类型防抖校验
 *   - validateCurrentBlock：对当前 block 跑 /validate-block
 *   - handleValidateCurrentBlock：手动触发当前 block 校验
 */
import { ref, onBeforeUnmount } from 'vue'

export interface StudioBlocksDeps {
  // v2.6: 细分依赖（wb 提供 skillId 真相源；studioDoc.doc 是 ComputedRef<SkillDocument>）
  wb: any
  studioDoc: any
  skillApi: any
}

export function useStudioBlocks(deps: StudioBlocksDeps) {
  const currentBlock = ref('')
  const validationErrorCount = ref(0)
  const validationWarningCount = ref(0)

  let blockValidateTimer: ReturnType<typeof setTimeout> | null = null

  function normalizeBlockContent(blockType: string, content: unknown): Record<string, unknown> | unknown[] {
    if (Array.isArray(content)) return content
    if (content && typeof content === 'object') return content as Record<string, unknown>
    if (blockType === 'purpose') return { text: typeof content === 'string' ? content : '' }
    if (['steps', 'params', 'antipatterns', 'output_definition', 'todos', 'data_inputs', 'test_cases'].includes(blockType)) {
      return []
    }
    return {}
  }

  function handleCursorBlockChange(blockType: string) {
    currentBlock.value = blockType
    // 800ms 防抖后验证当前 block
    if (blockValidateTimer) clearTimeout(blockValidateTimer)
    if (!deps.wb.skillId) return
    blockValidateTimer = setTimeout(() => validateCurrentBlock(blockType), 800)
  }

  async function validateCurrentBlock(blockType: string) {
    if (!deps.wb.skillId || !blockType) return
    try {
      const docKey = blockType === 'steps' ? 'rules' : blockType
      const doc = deps.studioDoc.doc.value as Record<string, unknown> | null
      const content = normalizeBlockContent(blockType, doc?.[docKey])
      const r = await deps.skillApi.validateBlock(
        deps.wb.skillId,
        blockType,
        content,
      )
      const errors = r.errors?.length || (r.status === 'error' ? 1 : 0)
      const warnings = r.warnings?.length || (r.status === 'warning' ? 1 : 0)
      validationErrorCount.value = errors
      validationWarningCount.value = warnings
    } catch { /* 静默 */ }
  }

  async function handleValidateCurrentBlock() {
    if (currentBlock.value) await validateCurrentBlock(currentBlock.value)
  }

  onBeforeUnmount(() => {
    if (blockValidateTimer) clearTimeout(blockValidateTimer)
  })

  return {
    currentBlock,
    validationErrorCount,
    validationWarningCount,
    handleCursorBlockChange,
    validateCurrentBlock,
    handleValidateCurrentBlock,
  }
}
