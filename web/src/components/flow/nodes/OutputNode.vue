<template>
  <div class="flow-node output-node">
    <Handle type="target" :position="Position.Left" />
    <Handle type="source" :position="Position.Right" />
    <div class="node-icon">📤</div>
    <div class="node-body">
      <div class="node-title">{{ data.name || '输出' }}</div>
      <div class="node-meta">{{ data.format || '-' }} · {{ data.recipient || '-' }}</div>
      <div class="output-badge">{{ data.approval_level || 'L0' }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { PropType } from 'vue'
import { Handle, Position } from '@vue-flow/core'
defineProps({ data: Object as PropType<any> })
</script>

<style scoped>
/* 设计稿统一 ai-* token，避免 hardcoded arcoblue 跟全站 ink-1 风格冲突 */
.output-node { background: var(--ai-info-soft); border: 1.5px solid var(--ai-info); min-width: 180px; padding-left: 20px; }
.output-badge {
  display: inline-block; margin-top: 4px;
  font-size: 10px; font-weight: 600; color: var(--ai-info);
  background: var(--ai-info-soft); padding: 1px 6px; border-radius: 4px;
}
</style>
