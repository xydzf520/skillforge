<template>
  <div class="metric-chip" :class="`tone-${tone}`">
    <span class="metric-label">{{ metric.label }}</span>
    <strong class="metric-value">{{ metric.value }}</strong>
    <span v-if="metric.delta" class="metric-delta">{{ metric.delta }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import type { ReportMetric } from '@/types/inbox'
import { reportTrendTone } from '../presentation'

const props = defineProps({
  metric: { type: Object as PropType<ReportMetric>, required: true },
})

const tone = computed(() => reportTrendTone(props.metric.trend))
</script>

<style scoped>
@import '../inbox-card.css';

.metric-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 11px;
  font-weight: 500;
  font-family: var(--ai-font-sans);
  font-variant-numeric: tabular-nums;
  border: 1px solid var(--ai-border);
}

.metric-label {
  color: var(--ai-ink-4);
  font-weight: 500;
}

.metric-value {
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-weight: 500;
}

.metric-delta {
  padding-left: 6px;
  border-left: 1px solid var(--ai-border);
  font-family: var(--ai-font-mono);
}

.tone-up {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
.tone-up .metric-label,
.tone-up .metric-value {
  color: var(--ai-ok);
}
.tone-up .metric-delta {
  border-left-color: rgba(29, 123, 72, 0.25);
}

.tone-down {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}
.tone-down .metric-label,
.tone-down .metric-value {
  color: var(--ai-bad);
}
.tone-down .metric-delta {
  border-left-color: rgba(182, 50, 27, 0.25);
}
</style>
