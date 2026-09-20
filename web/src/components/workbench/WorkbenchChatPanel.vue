<template>
  <div class="chat-panel">
    <!-- 消息区 -->
    <div class="chat-messages" ref="msgContainer">
      <!-- 欢迎屏 -->
      <div v-if="!messages.length" class="chat-welcome">
        <div class="welcome-icon">
          <img src="/brand/mark.svg" alt="" width="64" height="64" />
        </div>
        <div class="welcome-title">SkillForge AI</div>
        <div class="welcome-desc">{{ welcomeText }}</div>
        <div class="welcome-hints">
          <button v-for="hint in hints" :key="hint" class="hint-chip" @click="emit('send', hint)">
            {{ hint }}
          </button>
        </div>
      </div>

      <!-- 消息列表 -->
      <div v-for="(msg, i) in messages" :key="i" :class="['msg-row', `msg-${msg.role}`]">
        <div v-if="msg.role !== 'user'" class="msg-avatar ai">
          <img src="/brand/mark.svg" alt="" width="32" height="32" />
        </div>
        <div class="msg-bubble">
          <div class="msg-meta">
            <span class="msg-sender">{{ msg.role === 'user' ? '你' : 'SkillForge' }}</span>
            <span v-if="msg.created_at" class="msg-time">{{ formatMsgTime(msg.created_at) }}</span>
          </div>
          <div class="msg-body" v-html="renderContent(msg.content)" />
          <!-- patch 卡片 -->
          <div v-if="msg.patch" class="msg-patch-card">
            <div class="patch-card-header">
              <icon-code :size="14" />
              <span>Patch · {{ msg.patch.target_module }}</span>
              <a-tag size="small" :color="msg.patch.status === 'applied' ? 'green' : 'arcoblue'">
                {{ msg.patch.status === 'applied' ? '已应用' : '待确认' }}
              </a-tag>
            </div>
            <div class="patch-card-summary">{{ msg.patch.summary }}</div>
          </div>
        </div>
        <div v-if="msg.role === 'user'" class="msg-avatar user">
          <icon-user :size="16" />
        </div>
      </div>

      <!-- 打字指示器 -->
      <div v-if="submitting" class="msg-row msg-assistant">
        <div class="msg-avatar ai"><img src="/brand/mark.svg" alt="" width="32" height="32" /></div>
        <div class="msg-bubble">
          <div class="typing-indicator">
            <span /><span /><span />
          </div>
        </div>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="chat-input-area">
      <div class="input-context" v-if="activeModule">
        <a-tag size="small" color="arcoblue" closable @close="emit('clearModule')">
          @{{ activeModule }}
        </a-tag>
      </div>
      <div class="input-row">
        <a-textarea
          ref="inputEl"
          v-model="draft"
          :auto-size="{ minRows: 1, maxRows: 5 }"
          :placeholder="placeholder"
          class="chat-textarea"
          @keydown.enter.exact.prevent="submit"
          @keydown.shift.enter.exact="() => {}"
        />
        <button class="send-btn" :class="{ active: draft.trim() }" :disabled="!draft.trim() || submitting" @click="submit">
          <icon-send :size="18" />
        </button>
      </div>
      <div class="input-hint">Enter 发送 · Shift+Enter 换行</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick, onMounted } from 'vue'
import type { PropType } from 'vue'
import { IconUser, IconSend, IconCode } from '@arco-design/web-vue/es/icon'
import { formatTimeOnly } from '@/utils/format'

const props = defineProps({
  messages: { type: Array as PropType<any[]>, default: () => [] },
  placeholder: { type: String, default: '描述你想做的 Skill，或输入调优指令...' },
  submitting: { type: Boolean, default: false },
  activeModule: { type: String, default: '' },
  welcomeText: { type: String, default: '描述你想构建的 Skill，我会帮你生成完整草稿，再逐步调优。' },
  hints: {
    type: Array as PropType<string[]>,
    default: () => [
      '帮我生成一个监控关键指标异常并通知相关角色的 Skill',
      '创建一个自动化审批 / 派单的 Skill',
      '做一个风险预警 / 异常检测的 Skill',
    ],
  },
})

const emit = defineEmits(['send', 'clearModule'])
const draft = ref('')
const msgContainer = ref<HTMLElement | null>(null)
const inputEl = ref<any>(null)

function submit() {
  const value = draft.value.trim()
  if (!value || props.submitting) return
  emit('send', value)
  draft.value = ''
}

function renderContent(text: string) {
  if (!text) return ''
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>')
    .replace(/\n/g, '<br>')
}

function formatMsgTime(ts: string) {
  if (!ts) return ''
  const formatted = formatTimeOnly(ts)
  return formatted === '-' ? '' : formatted
}

function scrollToBottom() {
  nextTick(() => {
    if (msgContainer.value) {
      msgContainer.value.scrollTop = msgContainer.value.scrollHeight
    }
  })
}

watch(() => props.messages.length, scrollToBottom)
watch(() => props.submitting, scrollToBottom)

onMounted(() => {
  scrollToBottom()
  inputEl.value?.focus?.()
})
</script>

<style scoped>
.chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: transparent;
}

/* ── 消息区 ── */
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px 20px;
  scroll-behavior: smooth;
}

/* 欢迎屏 */
.chat-welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px 40px;
  text-align: center;
}
.welcome-icon {
  width: 64px; height: 64px; border-radius: 12px;
  background: transparent;
  display: flex; align-items: center; justify-content: center;
  color: #fff; margin-bottom: 16px;
  box-shadow: none;
}
.welcome-title {
  font-size: 22px; font-weight: 800; color: var(--ai-ink-1); margin-bottom: 8px; letter-spacing: -0.01em;
}
.welcome-desc {
  font-size: 14px; color: var(--ai-ink-2); max-width: 420px; line-height: 1.6;
  margin-bottom: 28px; font-weight: 600;
}
.welcome-hints {
  display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; max-width: 520px;
}
.hint-chip {
  padding: 8px 16px; border-radius: 999px; font-size: 13px;
  background: var(--ai-surface); color: var(--ai-ink-2);
  border: 1px solid var(--ai-border); cursor: pointer;
  transition: all var(--sf-transition);
  font-weight: 800;
}
.hint-chip:hover {
  background: var(--ai-warn-soft); border-color: var(--ai-warn);
  color: var(--ai-warn);
}

/* ── 消息行 ── */
.msg-row {
  display: flex; gap: 10px; margin-bottom: 20px;
  max-width: 85%; animation: msgIn 0.2s ease;
}
.msg-row.msg-user {
  margin-left: auto; flex-direction: row-reverse;
}
@keyframes msgIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
.msg-avatar {
  width: 32px; height: 32px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0; font-size: 14px;
}
.msg-avatar.ai {
  background: transparent;
  color: #fff;
  box-shadow: none;
}
.msg-avatar.user {
  background: var(--ai-border); color: var(--ai-ink-1);
}
.msg-bubble {
  min-width: 0;
}
.msg-meta {
  display: flex; align-items: center; gap: 8px;
  font-size: 12px; color: var(--ai-ink-3); margin-bottom: 4px;
  padding: 0 2px;
}
.msg-sender { font-weight: 800; color: var(--ai-ink-2); }
.msg-body {
  padding: 10px 14px; border-radius: 8px; line-height: 1.65;
  font-size: 13.5px; word-break: break-word;
  font-weight: 400;
}
.msg-assistant .msg-body {
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  border: 1px solid var(--ai-border);
  border-top-left-radius: var(--sf-radius-xs);
}
.msg-user .msg-body {
  background: var(--sf-brand-action); color: var(--sf-on-action);
  border-top-right-radius: var(--sf-radius-xs);
}
.msg-body :deep(.inline-code) {
  padding: 1px 5px; border-radius: var(--sf-radius-xs); font-size: 12px;
  font-family: var(--ai-font-mono);
  background: var(--ai-border);
}
.msg-user .msg-body :deep(.inline-code) {
  background: rgba(255,255,255,0.18);
}

/* patch 内嵌卡片 */
.msg-patch-card {
  margin-top: 8px; padding: 10px 12px; border-radius: 8px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}
.patch-card-header {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; font-weight: 800; color: var(--ai-ink-1); margin-bottom: 4px;
}
.patch-card-summary {
  font-size: 12px; color: var(--ai-ink-2); line-height: 1.5; font-weight: 600;
}

/* typing indicator */
.typing-indicator {
  display: flex; gap: 4px; padding: 4px 0;
}
.typing-indicator span {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--ai-ink-3);
  animation: blink 1.4s infinite both;
}
.typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink {
  0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
  40% { opacity: 1; transform: scale(1); }
}

/* ── 输入区 ── */
.chat-input-area {
  border-top: 1px solid var(--ai-border);
  padding: 12px 20px 10px;
  background: var(--ai-surface);
}
.input-context {
  margin-bottom: 8px;
}
.input-row {
  display: flex; align-items: flex-end; gap: 8px;
}
.chat-textarea {
  flex: 1;
}
.chat-textarea :deep(.arco-textarea) {
  border-radius: 8px !important; padding: 10px 14px !important;
  font-size: 13.5px !important; line-height: 1.5 !important;
  resize: none !important;
  background: var(--ai-surface) !important;
}
.send-btn {
  width: 38px; height: 38px; border-radius: 8px; border: none;
  background: var(--ai-border); color: var(--ai-ink-3);
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; transition: all var(--sf-transition); flex-shrink: 0;
}
.send-btn.active {
  background: var(--sf-brand-action); color: var(--sf-on-action);
  box-shadow: var(--ai-shadow-1);
}
.send-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.input-hint {
  font-size: 11px; color: var(--ai-ink-3); margin-top: 6px; padding-left: 2px; font-weight: 600;
}
</style>
