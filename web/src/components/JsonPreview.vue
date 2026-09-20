<template>
  <pre class="json-preview" :style="{ maxHeight: maxHeight + 'px' }">{{ formatted }}</pre>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'

/**
 * JSON 格式化预览组件。
 * 用法：<JsonPreview :data="someObject" />
 */
const props = defineProps({
  data: { type: [Object, Array, String] as PropType<any>, default: null },
  maxHeight: { type: Number, default: 200 },
})

const formatted = computed(() => {
  if (props.data === null || props.data === undefined) return '-'
  if (typeof props.data === 'string') {
    try { return JSON.stringify(JSON.parse(props.data), null, 2) }
    catch { return props.data }
  }
  return JSON.stringify(props.data, null, 2)
})
</script>

<style scoped>
.json-preview {
  background: var(--ai-surface-2);
  border-radius: 4px;
  padding: 8px 12px;
  font-family: var(--ai-font-mono);
  font-size: 12px;
  overflow: auto;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
