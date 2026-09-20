<template>
  <div class="assistant-tabs-wrap">
    <div class="assistant-tab-bar" role="tablist" aria-label="助手面板切换">
      <button
        type="button"
        role="tab"
        class="assistant-tab"
        :class="{ active: activeTab === 'chat' }"
        :aria-selected="activeTab === 'chat'"
        @click="emit('update:activeTab', 'chat')"
      >
        <icon-robot :size="13" /> AI 对话
      </button>
      <button
        v-if="showDepsTab"
        type="button"
        role="tab"
        class="assistant-tab"
        :class="{ active: activeTab === 'deps' }"
        :aria-selected="activeTab === 'deps'"
        @click="emit('update:activeTab', 'deps')"
      >
        <icon-mind-mapping :size="13" /> 依赖图
      </button>
    </div>
    <div v-show="activeTab === 'deps'" class="assistant-tab-panel">
      <SkillDependencyView :skill-id="skillId || ''" :active="activeTab === 'deps'" />
    </div>
    <div v-show="activeTab === 'chat'" class="assistant-tab-panel">
      <slot name="chat" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { IconMindMapping, IconRobot } from '@arco-design/web-vue/es/icon'
import SkillDependencyView from '@/components/ide/SkillDependencyView.vue'

defineProps<{
  activeTab: 'chat' | 'deps'
  showDepsTab: boolean
  skillId: string
}>()

const emit = defineEmits<{
  (e: 'update:activeTab', v: 'chat' | 'deps'): void
}>()
</script>

<style scoped>
.assistant-tabs-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  font-family: var(--ai-font-sans);
}
.assistant-tab-bar {
  display: flex;
  align-items: stretch;
  gap: 4px;
  padding: 0 12px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
  flex-shrink: 0;
}
.assistant-tab {
  height: 36px;
  padding: 0 12px;
  background: none;
  border: 0;
  font-size: 13px;
  color: var(--ai-ink-3);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-bottom: 1.5px solid transparent;
  margin-bottom: -1px;
  transition: color .15s;
  font-weight: 450;
}
.assistant-tab:hover {
  color: var(--ai-ink-1);
}
.assistant-tab.active {
  color: var(--ai-ink-1);
  border-bottom-color: var(--ai-ink-1);
  font-weight: 500;
}
.assistant-tab-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--ai-surface);
}
</style>
