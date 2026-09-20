<template>
  <section class="tt-value-card ai-card" data-testid="tasktree-detail-value">
    <div class="ai-card-h tt-card-title">
      <span class="t">Skill 价值</span>
      <span class="s">近 {{ skillValue?.period_days ?? 30 }} 天</span>
    </div>

    <div v-if="loading" class="tt-value-loading">
      <a-spin />
    </div>

    <div v-else-if="skillValue" class="tt-value-body">
      <div class="tt-value-grid">
        <div class="tt-value-item">
          <div class="tt-mini-label">节省工时</div>
          <div class="tt-value-big">{{ skillValue.saved_hours ?? 0 }}h</div>
        </div>
        <div class="tt-value-item">
          <div class="tt-mini-label">估算收益</div>
          <div class="tt-value-big">¥{{ skillValue.estimated_cost_saving ?? 0 }}</div>
        </div>
        <div class="tt-value-item">
          <div class="tt-mini-label">风险规避</div>
          <div class="tt-value-big">{{ skillValue.risk_events_prevented ?? 0 }}</div>
        </div>
      </div>
      <div v-if="skillValue.recommendation" class="tt-value-recommendation">
        <div class="tt-mini-label">建议</div>
        <div class="tt-value-reco-text">{{ skillValue.recommendation }}</div>
      </div>
    </div>

    <div v-else class="tt-value-body">
      <SfEmptyState description="选择执行记录以查看 Skill 价值指标" />
    </div>
  </section>
</template>

<script setup lang="ts">
import { SfEmptyState } from '@/components/common'
import type { TaskTreeSkillValueResponse } from '../types'

defineProps<{
  skillValue: TaskTreeSkillValueResponse | null
  loading: boolean
}>()
</script>

<style scoped>
.tt-value-card {
  min-width: 0;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  box-shadow: none;
  overflow: hidden;
}

.tt-card-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  font-weight: 500;
  font-size: 13px;
  color: var(--ai-ink-1);
}
.tt-card-title .s {
  font-family: var(--ai-font-mono);
}
.tt-value-body {
  padding: 16px;
}
.tt-value-loading {
  display: grid;
  place-items: center;
  min-height: 120px;
}

/* 设计稿 KPI 风格：flex cell + 竖线分隔 + 24px mono tabular */
.tt-value-grid {
  display: flex;
  align-items: stretch;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  overflow: hidden;
  background: var(--ai-surface);
}

.tt-value-item {
  flex: 1 1 0;
  min-width: 0;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  border-left: 1px solid var(--ai-border);
  background: transparent;
}
.tt-value-item:first-child { border-left: 0; }

.tt-mini-label {
  margin-bottom: 0;
  color: var(--ai-ink-4);
  font-size: 10.5px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  line-height: 1.2;
}

.tt-value-big {
  font-size: 24px;
  font-weight: 600;
  color: var(--ai-ink-1);
  letter-spacing: -0.02em;
  line-height: 1.1;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

.tt-value-recommendation {
  margin-top: 12px;
}

.tt-value-reco-text {
  padding: 10px 12px;
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  color: var(--ai-ink-2);
  line-height: 1.65;
  white-space: pre-wrap;
  font-size: 12.5px;
}
</style>
