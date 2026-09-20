<template>
  <!-- Mermaid 决策树面板 -->
  <div v-if="showMermaid" class="viz-panel">
    <MermaidPanel
      v-if="skillId"
      :skill-id="skillId"
      :cursor-line="cursorLineForMermaid"
      @jump-to-line="(line: number) => emit('jumpToLine', line)"
      @close="emit('update:showMermaid', false)"
    />
    <div v-else style="padding:20px;text-align:center;color:var(--ai-ink-4);font-size:12px;font-family:var(--ai-font-sans)">
      请先打开 Skill
    </div>
  </div>
  <!-- Flow 流程图面板 -->
  <div v-if="showFlow" class="viz-panel">
    <div class="viz-header">
      <span>流程图</span>
      <button class="viz-close" @click="emit('update:showFlow', false)">
        <icon-close :size="13" />
      </button>
    </div>
    <SkillFlowPanel :skill="doc" :skill-id="skillId || ''" style="flex:1" />
  </div>
</template>

<script setup lang="ts">
/**
 * v2.4.0 Studio 可视化面板集（Mermaid + Flow）
 * 从 SkillStudio.vue 抽出，减少主模板行数。
 */
import { defineAsyncComponent } from 'vue'
import { IconClose } from '@arco-design/web-vue/es/icon'

const MermaidPanel = defineAsyncComponent(() => import('@/components/editor/MermaidPanel.vue'))
const SkillFlowPanel = defineAsyncComponent(() => import('@/components/flow/SkillFlowPanel.vue'))

defineProps<{
  showMermaid: boolean
  showFlow: boolean
  skillId: string | null | undefined
  cursorLineForMermaid: number
  doc: unknown
}>()

const emit = defineEmits<{
  (e: 'update:showMermaid', v: boolean): void
  (e: 'update:showFlow', v: boolean): void
  (e: 'jumpToLine', line: number): void
}>()
</script>

<style scoped>
.viz-panel {
  width: 360px; flex-shrink: 0; display: flex; flex-direction: column;
  background: var(--ai-surface);
  border-left: 1px solid var(--ai-border);
  overflow: hidden;
  font-family: var(--ai-font-sans);
}
.viz-header {
  display: flex; align-items: center; justify-content: space-between;
  height: 36px; padding: 0 14px; flex-shrink: 0;
  border-bottom: 1px solid var(--ai-border);
  font-size: 13px; font-weight: 500; color: var(--ai-ink-1);
}
.viz-close {
  width: 24px; height: 24px; border-radius: 4px; border: 0;
  background: transparent; color: var(--ai-ink-4); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: background .15s, color .15s;
}
.viz-close:hover { background: var(--ai-surface-2); color: var(--ai-ink-1); }
</style>
