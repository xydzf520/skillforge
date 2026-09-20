<template>
  <div class="flow-node conclusion-node" :class="`c-${data.color || 'blue'}`">
    <Handle type="target" :position="Position.Left" />
    <Handle v-if="data.next_step" type="source" :position="Position.Right" />
    <div class="conclusion-badge">{{ icon }}</div>
    <div class="node-body">
      <div class="conclusion-text">{{ data.conclusion || '结论' }}</div>
      <div v-if="data.action" class="conclusion-action">{{ data.action }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import { Handle, Position } from '@vue-flow/core'
const props = defineProps({ data: { type: Object as PropType<{ color?: string; conclusion?: string; action?: string; next_step?: string | null }>, default: () => ({}) } })
const iconMap: Record<string, string> = { green: '✅', yellow: '⚠️', red: '❌', blue: '📋' }
const icon = computed(() => iconMap[props.data?.color || ''] || '📋')
</script>

<style scoped>
.conclusion-node { min-width: 140px; padding: 10px 14px; }
.conclusion-node.c-green { background: #ecfdf3; border: 1.5px solid #86efac; }
.conclusion-node.c-green::before { background: #12b76a; }
.conclusion-node.c-yellow { background: #fffbeb; border: 1.5px solid #fde68a; }
.conclusion-node.c-yellow::before { background: #f79009; }
.conclusion-node.c-red { background: #fef2f2; border: 1.5px solid #fca5a5; }
.conclusion-node.c-red::before { background: #f04438; }
.conclusion-node.c-blue { background: #eff6ff; border: 1.5px solid #93c5fd; }
.conclusion-node.c-blue::before { background: #2e90fa; }
.conclusion-badge { font-size: 18px; width: 28px; height: 28px; display: flex; align-items: center; justify-content: center; }
.conclusion-text { font-weight: 600; font-size: 13px; color: #1d2129; }
.conclusion-action { font-size: 11px; color: #667085; margin-top: 2px; }
</style>
