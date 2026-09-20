<template>
  <article
    class="sf-inbox-card dispatch-card"
    :class="[`dispatch-${item.status}`, { focus: isFocus }]"
    :data-testid="`dispatch-card-${item.id}`"
  >
    <div class="sf-inbox-card-head">
      <div class="sf-inbox-title-stack">
        <h3 class="sf-inbox-title">
          <span class="dispatch-id">#{{ item.id }}</span>
          <span>{{ item.title || `任务 #${item.id}` }}</span>
        </h3>
        <div v-if="cleanContent" class="sf-inbox-summary sf-inbox-clamp-2">{{ cleanContent }}</div>
      </div>
      <a-tag :color="dispatchStatusColor(item.status)">{{ dispatchStatusLabel(item.status) }}</a-tag>
    </div>

    <div class="sf-inbox-meta">
      <span v-if="item.skill_id">Skill {{ item.skill_id }}</span>
      <span>截止 {{ formatTime(item.deadline) || '-' }}</span>
      <span v-if="item.ack_at">完成 {{ formatTime(item.ack_at) }}</span>
    </div>

    <div v-if="item.ack_note" class="dispatch-ack-note">
      <span>完成说明</span>
      {{ item.ack_note }}
    </div>

    <div class="dispatch-actions">
      <a-button
        v-if="item.status === 'sent' || item.status === 'pushed_no_dingtalk'"
        type="primary"
        size="small"
        @click="$emit('ack', item)"
      >
        标记完成
      </a-button>
      <span v-else-if="item.ack_at" class="dispatch-done-text">已完成</span>
      <span v-else class="dispatch-done-text">—</span>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import { formatTime } from '@/utils/format'
import { dispatchStatusColor, dispatchStatusLabel, sanitizeSummaryText } from './presentation'

type DispatchItem = {
  id: number
  title?: string
  content?: string
  skill_id?: string
  deadline?: string
  status: string
  ack_at?: string
  ack_note?: string | null
  ack_channel?: string | null
}

const props = defineProps({
  item: { type: Object as PropType<DispatchItem>, required: true },
  isFocus: { type: Boolean, default: false },
})

const cleanContent = computed(() => sanitizeSummaryText(props.item.content))

defineEmits<{
  (event: 'ack', item: DispatchItem): void
}>()
</script>

<style scoped>
@import './inbox-card.css';

.dispatch-card {
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.dispatch-card.focus {
  border-color: var(--ai-accent);
  box-shadow: 0 0 0 3px var(--ai-accent-soft);
}

.dispatch-id {
  padding: 2px 8px;
  margin-right: 8px;
  border-radius: 999px;
  background: var(--ai-surface-2);
  font-size: 12px;
  font-weight: 700;
  color: var(--ai-ink-3);
  vertical-align: middle;
}

.sf-inbox-title {
  display: flex;
  align-items: center;
  gap: 4px;
}

.dispatch-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}

.dispatch-ack-note {
  margin-top: 8px;
  padding: 9px 10px;
  border-left: 3px solid var(--ai-ok);
  border-radius: 6px;
  background: var(--ai-ok-soft);
  color: var(--ai-ink-2);
  font-size: 13px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.dispatch-ack-note span {
  display: inline-block;
  margin-right: 6px;
  color: var(--ai-ok);
  font-weight: 800;
}

.dispatch-done-text {
  color: var(--ai-ink-3);
  font-size: 13px;
  font-weight: 600;
}
</style>
