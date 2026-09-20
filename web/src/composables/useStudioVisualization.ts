/**
 * W4-F v2.3.0: Studio 可视化面板（Mermaid 决策树 + Flow 流程图）的 toggle + 跳转逻辑
 *
 * 从 SkillStudio.vue 拆出独立单元：
 *   - showMermaidPanel / showFlowPanel ref 由 composable 持有
 *   - toggleMermaid / toggleFlow / handleMermaidJumpToLine 三个 handler
 *   - cursorLineForMermaid computed
 */
import { computed, ref } from 'vue'

export interface StudioVisualizationDeps {
  // v2.6: 细分依赖
  studioDoc: {
    setActiveModule?: (mod: string) => void
  } | any
  ui: {
    viewMode: string
  } | any
}

export function useStudioVisualization(deps: StudioVisualizationDeps) {
  const showMermaidPanel = ref(false)
  const showFlowPanel = ref(false)

  function toggleMermaid() {
    showFlowPanel.value = false
    showMermaidPanel.value = !showMermaidPanel.value
  }

  function toggleFlow() {
    showMermaidPanel.value = false
    showFlowPanel.value = !showFlowPanel.value
  }

  function handleMermaidJumpToLine(line: number | null | undefined) {
    if (!line) return
    // Block 模式下切到 rules 模块（决策树都在 rules 下）
    if (deps.ui.viewMode === 'block' && typeof deps.studioDoc.setActiveModule === 'function') {
      deps.studioDoc.setActiveModule('rules')
    }
    // v2.6: setCursorLine / cursorLine 从未被任何 store 实际暴露，删掉 soft 接口
    void line
  }

  const cursorLineForMermaid = computed<number>(() => 1)

  return {
    showMermaidPanel,
    showFlowPanel,
    toggleMermaid,
    toggleFlow,
    handleMermaidJumpToLine,
    cursorLineForMermaid,
  }
}
