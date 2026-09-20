<template>
  <article
    class="sf-inbox-card report-card"
    tabindex="0"
    role="button"
    :aria-label="item.title"
    :data-testid="`report-card-${item.id}`"
    @click="handleOpen"
    @keydown.enter.prevent="handleOpen"
    @keydown.space.prevent="handleOpen"
  >
    <div class="report-tag-row">
      <a-tag size="small" color="gray" class="report-channel-tag">{{ reportChannelLabel(item.channel) }}</a-tag>
      <a-tag size="small" color="arcoblue">{{ reportTriggerLabel(item.trigger_type) }}</a-tag>
      <span class="report-time-chip">{{ formatTime(item.created_at) }}</span>
    </div>

    <h3 class="sf-inbox-title">{{ item.title }}</h3>

    <div class="sf-inbox-summary sf-inbox-clamp-3">{{ cleanSummary }}</div>

    <div v-if="item.metrics.length || item.tags.length" class="sf-inbox-chip-row report-chip-row">
      <MetricChip v-for="metric in item.metrics" :key="`${item.id}-${metric.label}`" :metric="metric" />
      <a-tag v-for="tag in item.tags" :key="`${item.id}-${tag}`" size="small" color="arcoblue">#{{ tag }}</a-tag>
    </div>

    <div class="report-info-row">
      <span class="report-info-item" :title="item.skill_id || ''">
        <span class="report-info-label">Skill</span>
        <span class="report-info-value">{{ item.skill_name || item.skill_id }}</span>
      </span>
      <span class="report-info-item">
        <span class="report-info-label">部门</span>
        <span class="report-info-value">{{ item.skill_department || '未分组' }}</span>
      </span>
      <span class="report-info-item">{{ relatedLabel }}</span>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import { formatTime } from '@/utils/format'
import type { ReportListItem } from '@/types/inbox'
import MetricChip from './components/MetricChip.vue'
import { reportChannelLabel, reportTriggerLabel, sanitizeSummaryText } from './presentation'

const props = defineProps({
  item: { type: Object as PropType<ReportListItem>, required: true },
})

const emit = defineEmits<{
  (event: 'open', id: string): void
}>()

const relatedLabel = computed(() => {
  const total = props.item.related_todo_count || 0
  const pending = props.item.related_pending_request_count || 0
  if (!total) return '暂无关联待办'
  return `关联 ${total} 个待办 · ${pending} 个未决`
})

const cleanSummary = computed(() => sanitizeSummaryText(props.item.summary) || '暂无摘要')

function handleOpen() {
  emit('open', props.item.id)
}
</script>

<style scoped>
@import './inbox-card.css';

.report-card {
  padding: 14px 14px 14px 18px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.report-card:focus-visible {
  outline: 2px solid var(--ai-accent);
  outline-offset: 2px;
}

.report-tag-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.report-channel-tag {
  flex: 0 0 auto;
  white-space: nowrap;
}

/* Arco a-tag 在卡片内的样式：贴近设计稿 .ai-pill */
.report-card :deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.report-card :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.report-card :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
  border-color: transparent;
}

.report-time-chip {
  margin-left: auto;
  padding: 0 6px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  font-family: var(--ai-font-mono);
}

.sf-inbox-title {
  margin: 0;
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.35;
  letter-spacing: -0.005em;
  color: var(--ai-ink-1);
}

.report-chip-row {
  margin-top: 0;
}

.report-info-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  color: var(--ai-ink-4);
  font-size: 11.5px;
  margin-top: 4px;
}

.report-info-item {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  min-width: 0;
  max-width: 100%;
  font-variant-numeric: tabular-nums;
}

.report-info-label {
  color: var(--ai-ink-4);
  opacity: 1;
  font-weight: 500;
  letter-spacing: 0;
  flex: 0 0 auto;
  text-transform: uppercase;
  font-size: 10.5px;
}

.report-info-value {
  color: var(--ai-ink-2);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 180px;
}
</style>
