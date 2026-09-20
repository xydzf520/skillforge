<template>
  <div class="exec-overlay">
    <div class="overlay-header">
      <span class="overlay-title">执行轨迹</span>
      <select v-model="selectedRunId" class="run-select" @change="loadTrace">
        <option value="">选择执行记录</option>
        <option v-for="r in recentRuns" :key="r.id" :value="r.id">
          {{ r.id.slice(0, 8) }} · {{ r.status }} · {{ formatTime(r.started_at) }}
        </option>
      </select>
      <button v-if="active" class="clear-btn" @click="clearOverlay">清除</button>
    </div>

    <div v-if="trace" class="overlay-info">
      <span>状态: <strong>{{ trace.status }}</strong></span>
      <span v-if="trace.duration_ms">耗时: {{ trace.duration_ms }}ms</span>
      <span v-if="trace.summary" class="overlay-summary">{{ trace.summary }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import type { PropType } from 'vue'
import request from '@/api/request'
import { formatTimeShort } from '@/utils/format'

const props = defineProps({
  skillId: String,
  nodes: Array as PropType<any[]>,
  edges: Array as PropType<any[]>,
})

const emit = defineEmits(['highlight'])

const recentRuns = ref<any[]>([])
const selectedRunId = ref('')
const trace = ref<any>(null)
const active = ref(false)

async function loadRecentRuns() {
  try {
    const data: any = await request.get(`/executions/`, { params: { skill_id: props.skillId, limit: 10 } })
    recentRuns.value = data.items || data || []
  } catch { recentRuns.value = [] }
}

async function loadTrace() {
  if (!selectedRunId.value) { clearOverlay(); return }
  try {
    trace.value = await request.get(`/skills/${props.skillId}/execution-trace/${selectedRunId.value}`) as any
    active.value = true

    // 发送高亮信息到父组件
    const highlights: Record<string, any> = {}
    if (trace.value.trace?.length) {
      const t = trace.value.trace[0]
      // 如果有输出结果，高亮匹配的结论节点
      if (t.output_result) {
        highlights.output = t.output_result
        highlights.status = t.approval_status
      }
    }
    emit('highlight', { runId: selectedRunId.value, trace: trace.value, highlights })
  } catch (e) {
    console.warn('加载执行轨迹失败', e)
  }
}

function clearOverlay() {
  trace.value = null
  active.value = false
  selectedRunId.value = ''
  emit('highlight', null)
}

function formatTime(iso: string | undefined) {
  if (!iso) return ''
  const formatted = formatTimeShort(iso)
  return formatted === '-' ? '' : formatted
}

watch(() => props.skillId, loadRecentRuns, { immediate: true })
</script>

<style scoped>
.exec-overlay {
  padding: 8px 12px;
  background: #fffbeb;
  border-bottom: 1px solid #fde68a;
  font-size: 12px;
}
.overlay-header { display: flex; align-items: center; gap: 8px; }
.overlay-title { font-weight: 600; color: #92400e; }
.run-select {
  flex: 1; padding: 3px 6px; border: 1px solid #e5e7eb; border-radius: 4px;
  font-size: 11px; outline: none;
}
.clear-btn {
  border: none; background: none; color: #dc2626; cursor: pointer; font-size: 11px;
}
.overlay-info {
  display: flex; gap: 12px; margin-top: 4px; color: #78350f;
}
.overlay-summary {
  flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
</style>
