<template>
  <section class="inbox-header" data-testid="inbox-header">
    <div class="inbox-kpi-strip">
      <div class="kpi-cell" :class="{ 'kpi-danger': pendingCount > 0 }">
        <div class="kpi-label">待处理</div>
        <div class="kpi-value">{{ pendingCount }}</div>
        <div v-if="weeklyDeltaText" class="kpi-sub">{{ weeklyDeltaText }}</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">今日新到</div>
        <div class="kpi-value">{{ todayCount }}</div>
        <div class="kpi-sub">{{ todayBreakdown }}</div>
      </div>
      <div class="kpi-cell" :class="{ 'kpi-danger': overdueCount > 0 }">
        <div class="kpi-label">已过期</div>
        <div class="kpi-value">{{ overdueCount }}</div>
        <div class="kpi-sub">24h SLA</div>
      </div>
      <div class="kpi-cell">
        <div class="kpi-label">派发执行中</div>
        <div class="kpi-value">{{ dispatchPending }}</div>
        <div v-if="dispatchPending > 0" class="kpi-sub">{{ dispatchSub }}</div>
      </div>
      <div v-if="overview" class="kpi-cell" :class="{ 'kpi-danger': slaDanger, 'kpi-ok': slaOk, 'kpi-muted': !slaHasActivity }" data-testid="inbox-sla-card">
        <div class="kpi-label">SLA 达成率</div>
        <div class="kpi-value">{{ slaPercent }}<span class="kpi-suffix">%</span></div>
        <div class="kpi-sub">近 7 日</div>
      </div>
      <div v-else class="kpi-cell">
        <div class="kpi-label">已决总量</div>
        <div class="kpi-value">{{ resolvedTotal }}</div>
        <div class="kpi-sub">近 7 日</div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import type { InboxOverviewResponse, InboxTodoStats } from '@/types/inbox'

const props = defineProps({
  stats: { type: Object as PropType<InboxTodoStats>, default: () => ({}) },
  overview: {
    type: Object as PropType<InboxOverviewResponse | null>,
    default: null,
  },
})

const pendingCount = computed(() => props.overview?.pending ?? props.stats?.pending ?? 0)
const overdueCount = computed(() => props.overview?.overdue ?? props.stats?.overdue ?? props.stats?.expired ?? 0)
const dispatchPending = computed(() => props.stats?.dispatch_pending ?? 0)
const resolvedTotal = computed(
  () => props.stats?.done
    ?? ((props.stats?.approved ?? 0) + (props.stats?.rejected ?? 0) + (props.stats?.resolved_by_peer ?? 0)),
)

const todayCount = computed(() => {
  if (typeof props.stats?.today_new === 'number') return props.stats.today_new
  return Math.max(0, pendingCount.value - overdueCount.value)
})

const todayBreakdown = computed(() => {
  const byPriority = props.stats?.today_by_priority || {}
  const p0 = Number(byPriority.P0 || 0)
  const parts = [
    ...(p0 > 0 ? [`${p0} P0`] : []),
    `${Number(byPriority.P1 || 0)} P1`,
    `${Number(byPriority.P2 || 0)} P2`,
    `${Number(byPriority.P3 || 0)} P3`,
  ]
  const unclassified = Number(byPriority.unclassified || 0)
  if (unclassified > 0) parts.push(`未分级 ${unclassified}`)
  return parts.join(' · ')
})

const weeklyDeltaText = computed(() => {
  const resolved7d = props.overview?.resolved_last_7d ?? 0
  if (!resolved7d) return ''
  return `近 7 日 ${resolved7d} 件已决`
})

const dispatchSub = computed(() => `Skill 派发 ${dispatchPending.value} 件`)

const slaPercent = computed(() => {
  const rate = props.overview?.sla_hit_rate ?? 1
  return Math.round(rate * 1000) / 10
})

// 仅在有近期决策活动时才用 SLA < 95% 触发红色。0/0 时 SLA 无意义，红色会误导用户。
const slaHasActivity = computed(() => {
  const resolved7d = props.overview?.resolved_last_7d ?? 0
  return resolved7d > 0
})
const slaDanger = computed(() => slaHasActivity.value && slaPercent.value < 95)
const slaOk = computed(() => slaHasActivity.value && slaPercent.value >= 99)
</script>

<style scoped>
/* 设计稿 inbox.jsx 的 KPI strip：紧贴 pagehead 下方，整条横向分隔，不再做成卡片。 */
.inbox-header {
  min-width: 0;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
  font-family: var(--ai-font-sans);
}

.inbox-kpi-strip {
  display: flex;
  align-items: stretch;
  gap: 0;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  overflow: visible;
}

.kpi-cell {
  flex: 1 1 0;
  min-width: 0;
  padding: 14px 20px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  border-right: 1px solid var(--ai-border);
  background: transparent;
}
.kpi-cell:last-child {
  border-right: 0;
}

.kpi-label {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 400;
  text-transform: none;
  letter-spacing: 0;
  line-height: 1.2;
}

.kpi-value {
  color: var(--ai-ink-1);
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
  display: inline-flex;
  align-items: baseline;
  margin-top: 2px;
}

.kpi-suffix {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-4);
  margin-left: 2px;
  font-family: var(--ai-font-mono);
}

.kpi-sub {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  margin-top: 2px;
}

.kpi-cell.kpi-danger .kpi-value {
  color: var(--ai-bad);
}
.kpi-cell.kpi-danger .kpi-sub {
  color: var(--ai-bad);
}
.kpi-cell.kpi-ok .kpi-value {
  color: var(--ai-ok);
}
.kpi-cell.kpi-muted .kpi-value {
  color: var(--ai-ink-4);
}

@media (max-width: 640px) {
  .inbox-kpi-strip {
    flex-wrap: wrap;
  }
  .kpi-cell {
    flex: 1 1 50%;
    border-right: 1px solid var(--ai-border);
    border-top: 1px solid var(--ai-border);
  }
  .kpi-cell:nth-child(-n+2) {
    border-top: 0;
  }
  .kpi-cell:nth-child(even),
  .kpi-cell:last-child {
    border-right: 0;
  }
}
</style>
