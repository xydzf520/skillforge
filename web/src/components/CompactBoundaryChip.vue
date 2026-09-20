<template>
  <div class="sf-compact-boundary">
    <div class="sf-compact-boundary-bar">
      <div class="sf-compact-boundary-line" />
      <a-tag
        size="small"
        color="gray"
        class="sf-compact-boundary-chip"
        @click="expanded = !expanded"
      >
        <icon-archive />
        <span class="sf-compact-boundary-text">
          已自动压缩前 {{ originalCount }} 条历史消息
        </span>
        <icon-down v-if="!expanded" />
        <icon-up v-else />
      </a-tag>
      <div class="sf-compact-boundary-line" />
    </div>
    <div v-if="expanded" class="sf-compact-boundary-summary">
      <div class="sf-compact-boundary-summary-title">摘要内容</div>
      <div class="sf-compact-boundary-summary-body">{{ cleanContent }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { IconArchive, IconDown, IconUp } from '@arco-design/web-vue/es/icon'

/**
 * 压缩边界提示 Chip。
 * 出现在对话流中标记"前 N 条消息已被 AI 压缩"，点击展开摘要内容。
 *
 * 用法：
 *   <CompactBoundaryChip
 *     :original-count="message._original_count"
 *     :content="message.content"
 *   />
 */
const props = defineProps({
  originalCount: { type: Number, required: true },
  content: { type: String, default: '' },
})

const expanded = ref(false)

// 从摘要文本中剥掉前缀，只保留纯摘要正文
const cleanContent = computed(() => {
  const text = props.content || ''
  // 摘要格式：[历史摘要] 前 N 条消息已由 AI 自动压缩：\n\n<正文>
  const idx = text.indexOf('压缩：')
  if (idx >= 0) {
    return text.slice(idx + 3).trim()
  }
  return text.replace(/^\[历史摘要\]\s*/, '').trim()
})
</script>

<style scoped>
.sf-compact-boundary {
  margin: 16px 0;
  user-select: none;
}
.sf-compact-boundary-bar {
  display: flex;
  align-items: center;
  gap: 12px;
}
.sf-compact-boundary-line {
  flex: 1;
  height: 1px;
  background: var(--ai-border);
}
.sf-compact-boundary-chip {
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 10px;
  border-radius: 12px;
  transition: background 0.2s;
}
.sf-compact-boundary-chip:hover {
  background: var(--ai-surface-2);
}
.sf-compact-boundary-text {
  font-size: var(--sf-text-caption, 12px);
}
.sf-compact-boundary-summary {
  margin-top: 8px;
  padding: 12px 16px;
  background: var(--ai-surface-2);
  border-radius: 6px;
  border-left: 3px solid var(--ai-border-2);
}
.sf-compact-boundary-summary-title {
  font-size: 11px;
  color: var(--ai-ink-3);
  margin-bottom: 6px;
  letter-spacing: 0.5px;
}
.sf-compact-boundary-summary-body {
  font-size: var(--sf-text-caption, 12px);
  color: var(--ai-ink-2);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
