<template>
  <div class="agent-chat-panel">
    <!-- v7 B1：在原 chat 之上叠加 LangGraph events 区，展示 thread_id / cost / 中间 reasoning -->
    <div v-if="streamMessage" class="acp-stream-section">
      <div class="acp-stream-header">
        <span class="acp-stream-title">v7 引擎进度</span>
        <span v-if="threadId" class="acp-thread">thread: {{ threadId.slice(0, 12) }}</span>
        <span v-if="totalCost" class="acp-cost">$ {{ totalCost.toFixed(4) }}</span>
      </div>
      <AgentProgressStream
        :message="streamMessage"
        :mode="streamMode"
        :auto-start="true"
        @event="onStreamEvent"
        @done="onStreamDone"
        @interrupt="onStreamInterrupt"
        @error="onStreamError"
      />
    </div>
    <WorkbenchAssistantPane
      v-bind="$props"
      @send="(...a) => emit('send', ...a)"
      @stop="(...a) => emit('stop', ...a)"
      @close="(...a) => emit('close', ...a)"
      @update-references="(...a) => emit('update-references', ...a)"
      @switch-conversation="(...a) => emit('switch-conversation', ...a)"
      @new-conversation="(...a) => emit('new-conversation', ...a)"
      @upload-report="(...a) => emit('upload-report', ...a)"
      @coach-action="(...a) => emit('coach-action', ...a)"
      @coach-dismiss="(...a) => emit('coach-dismiss', ...a)"
      @toggle-tracking="(...a) => emit('toggle-tracking', ...a)"
      @respond-permission="(...a) => emit('respond-permission', ...a)"
      @jump-to-file="(...a) => emit('jump-to-file', ...a)"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, type PropType } from 'vue'
import WorkbenchAssistantPane from '@/components/workbench/WorkbenchAssistantPane.vue'
import AgentProgressStream from '@/components/chat/AgentProgressStream.vue'

const props = defineProps({
  messages: { type: Array as PropType<any[]>, default: () => [] },
  loading: { type: Boolean, default: false },
  activeModule: { type: String, default: '' },
  activeFile: { type: String, default: '' },
  selection: { type: Object as PropType<any>, default: null },
  references: { type: Array as PropType<any[]>, default: () => [] },
  selectedReferenceIds: { type: Array as PropType<Array<string | number>>, default: () => [] },
  isCreate: { type: Boolean, default: false },
  conversations: { type: Array as PropType<any[]>, default: () => [] },
  doc: { type: Object as PropType<any>, default: () => ({}) },
  coachSuggestions: { type: Array as PropType<any[]>, default: () => [] },
  budgetUsed: { type: Number, default: 0 },
  budgetLimit: { type: Number, default: 0 },
  budgetStatus: { type: String, default: 'ok' },
  // v7 B1：流式输入
  streamMessage: { type: String, default: '' },
  streamMode: { type: String, default: 'save' },
  // Phase 4: 跟踪模式 + 权限审批
  trackingEnabled: { type: Boolean, default: false },
  pendingPermission: { type: Object as PropType<any>, default: null },
  sessionMeta: { type: Object as PropType<any>, default: null },
  studioMode: { type: String, default: 'edit' },
})

// Vue 3 中 v-on="$attrs" 不会把 onSend 自动映射成 send 事件，
// 必须显式 defineEmits + 显式 forward，否则父组件的 @send 监听器会被吞掉。
const emit = defineEmits([
  'send', 'stop', 'close',
  'update-references', 'switch-conversation', 'new-conversation',
  'upload-report', 'coach-action', 'coach-dismiss',
  // Phase 4
  'toggle-tracking', 'respond-permission', 'jump-to-file',
])

const threadId = ref<string | null>(null)
const totalCost = ref(0)

function onStreamEvent(ev: any) {
  if (ev.type === 'thread_id' && ev.thread_id) threadId.value = ev.thread_id
  if (ev.cost_usd) totalCost.value += parseFloat(ev.cost_usd) || 0
}
function onStreamDone(payload: any) {
  if (payload?.threadId) threadId.value = payload.threadId
}
function onStreamInterrupt(_ev: any) {}
function onStreamError(_err: any) {}
</script>

<style scoped>
.agent-chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  gap: 8px;
}
.acp-stream-section {
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  padding: 10px 12px;
  background: var(--ai-surface-2);
}
.acp-stream-header {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: var(--ai-ink-2);
  margin-bottom: 8px;
}
.acp-stream-title {
  font-weight: 600;
  color: var(--ai-ink-1);
}
.acp-thread,
.acp-cost {
  font-family: var(--ai-font-mono);
  font-size: 11px;
  color: var(--ai-ink-3);
}
</style>
