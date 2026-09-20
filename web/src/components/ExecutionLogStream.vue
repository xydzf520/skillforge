<template>
  <div class="log-stream" ref="logContainer">
    <div v-for="(log, i) in logs" :key="i" :class="['log-line', `log-${log.level}`]">
      <span class="log-ts">[{{ log.ts }}]</span>
      <span class="log-msg">{{ log.message }}</span>
    </div>
    <div v-if="running" class="log-cursor"></div>
    <div v-if="!running && logs.length" class="log-line log-done">
      [{{ doneTs }}] 完成 ({{ elapsed }})
    </div>
    <div v-if="!running && !logs.length" class="log-empty">等待执行...</div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import type { PropType } from 'vue'
import { formatTimeFull } from '@/utils/format'

const props = defineProps({
  logs: { type: Array as PropType<Array<{ ts?: string; message?: string; level?: string }>>, default: () => [] },
  running: { type: Boolean, default: false },
  startTime: { type: Number, default: 0 },
})

const logContainer = ref<HTMLElement | null>(null)
const doneTs = ref('')
const elapsed = ref('')

// 自动滚动到底部
watch(() => props.logs.length, async () => {
  await nextTick()
  if (logContainer.value) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight
  }
})

watch(() => props.running, (val) => {
  if (!val && props.startTime) {
    const now = Date.now()
    const dur = ((now - props.startTime) / 1000).toFixed(1)
    elapsed.value = `${dur}s`
    doneTs.value = formatTimeFull(now).split(' ')[1] || '--:--:--'
  }
})
</script>

<style scoped>
.log-stream {
  background: #1e1e1e;
  color: #d4d4d4;
  font-family: var(--ai-font-mono);
  font-size: 12px;
  line-height: 1.6;
  padding: 12px 16px;
  border-radius: 8px;
  max-height: 400px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.log-line { padding: 1px 0; }
.log-ts { color: #6a9955; margin-right: 6px; }
.log-info .log-msg { color: #d4d4d4; }
.log-step .log-msg { color: #569cd6; font-weight: 600; }
.log-branch .log-msg { color: #6a9955; padding-left: 16px; }
.log-warning .log-msg { color: #ce9178; }
.log-error .log-msg { color: #f44747; }
.log-done { color: #6a9955; font-weight: 600; }
.log-cursor::after {
  content: '\258C';
  animation: blink 1s step-end infinite;
  color: #569cd6;
}
@keyframes blink { 50% { opacity: 0; } }
.log-empty { color: #6a6a6a; text-align: center; padding: 20px; }
</style>
