<template>
  <div class="sf-stream-msg" :class="role">
    <div v-if="role !== 'user'" class="sf-stream-avatar"><icon-robot :size="13" /></div>
    <div class="sf-stream-body">
      <div
        class="sf-stream-bubble"
        :class="{ 'is-streaming': streaming }"
      >
        <span v-html="rendered" />
        <span v-if="streaming" class="sf-stream-cursor" />
      </div>
      <div v-if="meta" class="sf-stream-meta">{{ meta }}</div>
    </div>
    <div v-if="role === 'user'" class="sf-stream-avatar user"><icon-user :size="13" /></div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { IconRobot, IconUser } from '@arco-design/web-vue/es/icon'
import { renderMd } from '@/utils/renderMd'

/**
 * 流式消息气泡。
 * 用于支持 F2 LLM 流式输出，生成中的最后一条消息显示闪烁光标。
 *
 * 用法：
 *   <StreamingMessage
 *     :role="msg.role"
 *     :content="msg.content"
 *     :streaming="msg.streaming"
 *   />
 *
 * 设计要点：
 * - role: user / assistant
 * - content: 当前累积的文本（可为空）
 * - streaming=true 时显示尾部闪烁光标
 * - 内部用 renderMd 渲染 Markdown，与 WorkbenchAssistantPane 行为一致
 */
const props = defineProps({
  role: { type: String, required: true },
  content: { type: String, default: '' },
  streaming: { type: Boolean, default: false },
  meta: { type: String, default: '' },
})

const rendered = computed(() => renderMd(props.content || ''))
</script>

<style scoped>
.sf-stream-msg {
  display: flex;
  gap: 8px;
  margin: 8px 0;
  align-items: flex-start;
}
.sf-stream-msg.user {
  flex-direction: row-reverse;
}
.sf-stream-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  flex-shrink: 0;
}
.sf-stream-avatar.user {
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
}
.sf-stream-body {
  flex: 1;
  min-width: 0;
}
.sf-stream-bubble {
  display: inline-block;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
  font-size: var(--sf-text-body, 13px);
  line-height: 1.6;
  word-break: break-word;
  max-width: 100%;
}
.sf-stream-msg.user .sf-stream-bubble {
  background: var(--ai-accent-soft);
}
.sf-stream-meta {
  margin-top: 4px;
  font-size: 11px;
  line-height: 1.4;
  color: var(--ai-ink-4);
}
.sf-stream-msg.user .sf-stream-meta {
  text-align: right;
}

/* 流式光标 */
.sf-stream-cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  vertical-align: text-bottom;
  margin-left: 2px;
  background: currentColor;
  animation: sf-blink 1s step-end infinite;
}
@keyframes sf-blink {
  from, 50% { opacity: 1; }
  51%, to { opacity: 0; }
}

/* Markdown 内嵌样式（继承现有 .msg-bubble 风格的简化版） */
.sf-stream-bubble :deep(p) { margin: 0 0 6px; }
.sf-stream-bubble :deep(p:last-child) { margin-bottom: 0; }
.sf-stream-bubble :deep(pre) {
  background: var(--ai-surface-2);
  padding: 8px;
  border-radius: 4px;
  overflow-x: auto;
  font-size: 12px;
}
.sf-stream-bubble :deep(code) {
  background: var(--ai-surface-2);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 12px;
}
.sf-stream-bubble :deep(ul), .sf-stream-bubble :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}
</style>
