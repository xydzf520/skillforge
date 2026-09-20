<template>
  <div class="block">
    <div class="block-header">
      <div class="block-title">工作流</div>
      <a-button v-if="hasNodes" size="mini" type="outline" @click="emit('openCanvas')">打开画布编辑器</a-button>
    </div>

    <div v-if="!hasNodes" class="block-empty">
      <icon-share-alt :size="28" style="color: var(--ai-ink-4); margin-bottom: 8px" />
      <div>暂无工作流。可通过 AI 对话自动生成，或在画布编辑器中手动创建。</div>
    </div>

    <div v-else class="wf-summary">
      <div class="wf-stat">
        <span class="wf-num">{{ nodes.length }}</span>
        <span class="wf-label">节点</span>
      </div>
      <div class="wf-stat">
        <span class="wf-num">{{ edges.length }}</span>
        <span class="wf-label">连线</span>
      </div>
      <div class="wf-stat">
        <span class="wf-num">{{ bindings.length }}</span>
        <span class="wf-label">绑定</span>
      </div>
    </div>

    <!-- 节点列表 -->
    <div v-if="hasNodes" class="wf-nodes">
      <div v-for="n in nodes" :key="n.id" class="wf-node">
        <icon-apps :size="14" />
        <span class="wf-node-label">{{ n.label || n.name || n.id }}</span>
        <a-tag size="small" v-if="n.skill_id">{{ n.skill_id }}</a-tag>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import { IconShareAlt, IconApps } from '@arco-design/web-vue/es/icon'

const props = defineProps({
  value: { type: Object as PropType<any>, default: () => ({}) },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['openCanvas'])

const nodes = computed(() => props.value?.nodes || [])
const edges = computed(() => props.value?.edges || [])
const bindings = computed(() => props.value?.bindings || [])
const hasNodes = computed(() => nodes.value.length > 0)
</script>

<style scoped>
.block { padding: 4px 0; }
.block-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.block-title { font-size: 15px; font-weight: 700; color: var(--ai-ink-1); }
.block-empty { color: var(--ai-ink-4); font-size: 13px; padding: 32px 0; text-align: center; display: flex; flex-direction: column; align-items: center; }

.wf-summary { display: flex; gap: 16px; margin-bottom: 14px; }
.wf-stat { display: flex; flex-direction: column; align-items: center; padding: 10px 16px; border-radius: 8px; background: var(--ai-surface-2); flex: 1; }
.wf-num { font-size: 18px; font-weight: 700; color: var(--ai-accent-ink); }
.wf-label { font-size: 11px; color: var(--ai-ink-4); }

.wf-nodes { display: flex; flex-direction: column; gap: 4px; }
.wf-node {
  display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  border-radius: 6px; background: var(--ai-surface-2); font-size: 13px;
}
.wf-node-label { flex: 1; font-weight: 500; }
</style>
