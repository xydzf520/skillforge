<template>
  <div class="flow-node approval-node">
    <Handle type="target" :position="Position.Left" />
    <div class="node-icon">👤</div>
    <div class="node-body">
      <div class="node-title">审批路由</div>
      <div class="approval-level">{{ data.level || 'L0' }}</div>
      <div class="node-meta">{{ data.trigger_type || 'manual' }}</div>
      <div v-if="data.targets?.length" class="node-meta">{{ data.targets.join(', ') }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { PropType } from 'vue'
import { Handle, Position } from '@vue-flow/core'
defineProps({ data: Object as PropType<any> })
</script>

<style scoped>
.approval-node { background: #faf5ff; border: 1.5px solid #d8b4fe; min-width: 160px; padding-left: 20px; }
.approval-level {
  display: inline-block; margin-top: 2px;
  font-size: 11px; font-weight: 600; color: #7c3aed;
  background: #ede9fe; padding: 1px 8px; border-radius: 4px;
}
</style>
