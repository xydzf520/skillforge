<template>
  <div class="flow-node script-node">
    <Handle type="source" :position="Position.Right" />
    <div class="node-icon">🔧</div>
    <div class="node-body">
      <div class="node-title">{{ data.name || 'script.py' }}</div>
      <div class="node-meta">{{ data.path }}</div>
      <div class="node-meta" style="font-family: var(--ai-font-mono);">{{ formatSize(data.size) }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { PropType } from 'vue'
import { Handle, Position } from '@vue-flow/core'
defineProps({ data: Object as PropType<any> })
function formatSize(b: number) { return b > 1024 ? `${(b/1024).toFixed(1)} KB` : `${b || 0} B` }
</script>

<style scoped>
.script-node { background: var(--ai-warn-soft); border: 1.5px solid var(--ai-border); min-width: 190px; padding-left: 20px; }
</style>
