<template>
  <div v-if="report" class="related-report" data-testid="related-report-link">
    <div class="related-report-copy">
      <span class="related-report-eyebrow">上下文报告</span>
      <strong class="related-report-title">{{ report.title }}</strong>
      <span v-if="report.summary" class="related-report-summary sf-inbox-clamp-2">{{ report.summary }}</span>
    </div>
    <div class="related-report-actions">
      <span class="related-report-time">{{ formatTime(report.created_at) }}</span>
      <a-button
        type="text"
        size="small"
        data-testid="related-report-open"
        @click="$emit('open', report.id)"
      >
        查看报告
      </a-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { PropType } from 'vue'
import { formatTime } from '@/utils/format'
import type { RelatedReportSummary } from '@/types/inbox'

defineProps({
  report: { type: Object as PropType<RelatedReportSummary | null>, default: null },
})

defineEmits<{
  (event: 'open', id: string): void
}>()
</script>

<style scoped>
@import '../inbox-card.css';

.related-report {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 14px 16px;
  border-radius: 14px;
  background: var(--ai-accent-soft);
  border: 1px solid var(--ai-border);
}

.related-report-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.related-report-eyebrow {
  color: var(--ai-ink-3);
  font-size: 12px;
  font-weight: 700;
}

.related-report-title {
  color: var(--ai-ink-1);
  font-size: 15px;
  line-height: 1.4;
}

.related-report-summary {
  color: var(--ai-ink-2);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.55;
}

.related-report-actions {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  flex-shrink: 0;
}

.related-report-time {
  color: var(--ai-ink-3);
  font-size: 12px;
  font-weight: 700;
}

@media (max-width: 768px) {
  .related-report {
    flex-direction: column;
    align-items: flex-start;
  }

  .related-report-actions {
    align-items: flex-start;
  }
}
</style>
