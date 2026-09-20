<template>
  <a-tag :color="color" size="small">{{ label || status }}</a-tag>
</template>

<script setup lang="ts">
import { computed } from 'vue'

/**
 * 统一状态标签组件。
 * 用法：<StatusTag status="completed" /> 或 <StatusTag status="active" type="skill" />
 */
const props = defineProps({
  status: { type: String, required: true },
  label: { type: String, default: '' },
  type: { type: String, default: '' }, // skill / run / review
})

const STATUS_COLORS: Record<string, string> = {
  // 通用
  completed: 'green', success: 'green', active: 'green', approved: 'green',
  running: 'blue', pending: 'orangered', processing: 'blue', shadow: 'blue',
  failed: 'red', rejected: 'red', timeout: 'red', deprecated: 'gray',
  skipped: 'orange', warning: 'orange', draft: 'gray',
}

const color = computed(() => STATUS_COLORS[props.status] || 'gray')
</script>
