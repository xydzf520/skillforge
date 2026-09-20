<template>
  <div class="agent-progress-stream">
    <div v-if="!events.length && !connecting" class="aps-empty">
      <span>等待业务方输入...</span>
    </div>
    <div v-else>
      <div v-if="connecting" class="aps-connecting">
        <a-spin /> <span>正在连接 v7 引擎...</span>
      </div>
      <div class="aps-events">
        <div
          v-for="(ev, idx) in events"
          :key="idx"
          class="aps-event"
          :class="['aps-event-' + ev.type, { 'aps-current': idx === events.length - 1 && !done }]"
        >
          <div class="aps-event-icon">
            <span v-if="ev.type === 'progress'">⏳</span>
            <span v-else-if="ev.type === 'node_result'">✓</span>
            <span v-else-if="ev.type === 'interrupt'">⏸</span>
            <span v-else-if="ev.type === 'done'">✅</span>
            <span v-else-if="ev.type === 'error'">⚠️</span>
            <span v-else>·</span>
          </div>
          <div class="aps-event-body">
            <div class="aps-event-msg">{{ ev.message || ev.error || '' }}</div>
            <div v-if="ev.node" class="aps-event-meta">{{ ev.node }}</div>
          </div>
        </div>
      </div>
      <div v-if="done && finalState" class="aps-done">
        <div class="aps-done-title">任务合同已生成</div>
        <div class="aps-done-meta">门禁项数：{{ (finalState.gate && finalState.gate.items || []).length }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onBeforeUnmount, watch } from 'vue'
import { agentCoreApi as rawAgentCoreApi } from '@/api'

type AgentProgressEvent = {
  type?: string
  message?: string
  error?: string
  node?: string
  state?: any
  thread_id?: string
}

const props = defineProps({
  message: { type: String, default: '' },
  mode: { type: String, default: 'save' },
  autoStart: { type: Boolean, default: true },
})

const emit = defineEmits(['done', 'interrupt', 'error', 'event'])

const agentCoreApi: any = rawAgentCoreApi
const events = ref<AgentProgressEvent[]>([])
const connecting = ref(false)
const done = ref(false)
const finalState = ref<any>(null)
const threadId = ref<string | null>(null)
let ws: WebSocket | null = null

function start() {
  if (!props.message) return
  events.value = []
  done.value = false
  finalState.value = null
  connecting.value = true

  ws = agentCoreApi.openStream()

  ws!.onopen = () => {
    connecting.value = false
    ws!.send(JSON.stringify({ message: props.message, mode: props.mode }))
  }

  ws!.onmessage = (event: MessageEvent) => {
    try {
      const data = JSON.parse(String(event.data)) as AgentProgressEvent
      if (data.type === 'thread_id') {
        threadId.value = data.thread_id ?? null
        return
      }
      events.value.push(data)
      emit('event', data)
      if (data.type === 'interrupt') {
        emit('interrupt', data)
      }
      if (data.type === 'done') {
        done.value = true
        finalState.value = data.state
        emit('done', { state: data.state, threadId: threadId.value })
      }
      if (data.type === 'error') {
        emit('error', data.error)
      }
    } catch (e) {
      console.error('AgentProgressStream parse error', e)
    }
  }

  ws!.onerror = (e: Event) => {
    connecting.value = false
    emit('error', e)
  }

  ws!.onclose = () => {
    connecting.value = false
  }
}

function stop() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.close()
  }
  ws = null
}

watch(
  () => props.message,
  (newMsg: string) => {
    if (props.autoStart && newMsg) {
      stop()
      start()
    }
  },
  { immediate: false },
)

onBeforeUnmount(stop)

defineExpose({ start, stop, threadId })
</script>

<style scoped>
.agent-progress-stream {
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  padding: 12px 16px;
  background: var(--ai-surface-2);
  font-size: 13px;
  min-height: 80px;
}
.aps-empty {
  color: var(--ai-ink-3);
  text-align: center;
  padding: 16px 0;
}
.aps-connecting {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ai-ink-2);
  padding: 8px 0;
}
.aps-events {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.aps-event {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 8px 10px;
  background: var(--ai-surface);
  border-radius: 6px;
  transition: all 0.2s;
}
.aps-event-icon {
  font-size: 14px;
  line-height: 18px;
  width: 18px;
  text-align: center;
}
.aps-event-body {
  flex: 1;
  min-width: 0;
}
.aps-event-msg {
  color: var(--ai-ink-1);
}
.aps-event-meta {
  font-size: 11px;
  color: var(--ai-ink-3);
  margin-top: 2px;
}
.aps-current {
  background: var(--ai-info-soft);
  border-left: 2px solid var(--ai-info);
}
.aps-event-interrupt {
  background: var(--ai-warn-soft);
}
.aps-event-error {
  background: var(--ai-bad-soft);
}
.aps-done {
  margin-top: 12px;
  padding: 8px 12px;
  background: var(--ai-ok-soft);
  border-radius: 6px;
}
.aps-done-title {
  color: var(--ai-ok);
  font-weight: 600;
}
.aps-done-meta {
  font-size: 11px;
  color: var(--ai-ink-3);
  margin-top: 2px;
}
</style>
