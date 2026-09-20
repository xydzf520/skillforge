<template>
  <div class="flow-node ds-node">
    <Handle type="source" :position="Position.Right" />
    <div class="node-icon">📊</div>
    <div class="node-body">
      <div class="node-title">{{ data.name || '数据源' }}</div>
      <div class="node-meta">来源: {{ data.source || '未指定' }}</div>
      <div v-if="data.frequency" class="node-meta">{{ data.frequency }}</div>
      <div v-if="data.is_active !== null && data.is_active !== undefined" class="node-status">
        <span :class="data.is_active ? 'status-ok' : 'status-err'">
          {{ data.is_active ? '● 在线' : '● 离线' }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { PropType } from 'vue'
import { Handle, Position } from '@vue-flow/core'
defineProps({ data: Object as PropType<any> })
</script>

<style scoped>
.ds-node { background: #f0f9ff; border: 1.5px solid #bae6fd; min-width: 180px; padding-left: 20px; }
</style>
