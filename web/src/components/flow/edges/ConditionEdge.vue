<template>
  <BaseEdge :path="path" :style="edgeStyle" :marker-end="markerEnd" />
  <EdgeLabelRenderer v-if="label">
    <div :style="labelStyle" class="edge-label" :class="edgeClass">
      {{ label }}
    </div>
  </EdgeLabelRenderer>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import { BaseEdge, EdgeLabelRenderer, getBezierPath } from '@vue-flow/core'

const props = defineProps({
  sourceX: { type: Number, default: 0 },
  sourceY: { type: Number, default: 0 },
  targetX: { type: Number, default: 0 },
  targetY: { type: Number, default: 0 },
  sourcePosition: String,
  targetPosition: String,
  data: Object as PropType<{ style?: string; color?: string }>,
  label: String,
  markerEnd: String,
})

const path = computed(() => {
  const [p] = getBezierPath({
    sourceX: props.sourceX, sourceY: props.sourceY,
    targetX: props.targetX, targetY: props.targetY,
    sourcePosition: props.sourcePosition as any,
    targetPosition: props.targetPosition as any,
  })
  return p
})

const styleType = computed(() => props.data?.style || 'flow')

const edgeStyle = computed(() => {
  const styles: Record<string, any> = {
    dataInput: { stroke: '#98a2b3', strokeWidth: 1.5, strokeDasharray: '6 3' },
    param: { stroke: '#eab308', strokeWidth: 1.5, strokeDasharray: '4 4' },
    branch: { stroke: colorMap[props.data?.color as keyof typeof colorMap] || '#667085', strokeWidth: 2 },
    flow: { stroke: '#344054', strokeWidth: 1.5 },
    script: { stroke: '#f97316', strokeWidth: 1.5, strokeDasharray: '5 3' },
    output: { stroke: '#3a5bd9', strokeWidth: 1.5 },
    approval: { stroke: '#a855f7', strokeWidth: 1.5 },
  }
  return styles[styleType.value] || styles.flow
})

const edgeClass = computed(() => `edge-${styleType.value}`)

const labelStyle = computed<any>(() => ({
  position: 'absolute',
  transform: `translate(-50%, -50%) translate(${(props.sourceX + props.targetX) / 2}px, ${(props.sourceY + props.targetY) / 2}px)`,
  pointerEvents: 'all',
}))

const colorMap = { green: '#12b76a', yellow: '#f79009', red: '#f04438', blue: '#2e90fa' }
</script>

<style>
.edge-label {
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 4px;
  background: #fff;
  border: 1px solid #e5e7eb;
  color: #667085;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.edge-label.edge-branch { font-weight: 500; color: #344054; }
.edge-label.edge-script { color: #f97316; border-color: #fdba74; }
.edge-label.edge-param { color: #854d0e; border-color: #fde047; }
</style>
