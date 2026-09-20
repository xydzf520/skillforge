<template>
  <article
    class="sf-kpi-card"
    :class="[toneClass, { 'has-trend': showTrend }]"
    :data-tone="resolvedTone"
  >
    <span class="sf-kpi-label">{{ label }}</span>

    <div class="sf-kpi-value-row">
      <span class="sf-kpi-value">
        <slot name="value">{{ value }}</slot>
      </span>
      <span v-if="suffix" class="sf-kpi-suffix">{{ suffix }}</span>
    </div>

    <span v-if="hint" class="sf-kpi-hint">{{ hint }}</span>

    <span
      v-if="showTrend"
      class="sf-kpi-trend"
      :class="`sf-kpi-trend-${resolvedTrend}`"
      :aria-label="`趋势 ${resolvedTrend}`"
    >
      <span class="sf-kpi-trend-arrow">{{ trendArrow }}</span>
      <span v-if="delta != null" class="sf-kpi-trend-delta">{{ formattedDelta }}</span>
    </span>
  </article>
</template>

<script setup lang="ts">
/**
 * SfKpiCard — 页面顶部大号 KPI 卡
 *
 * 使用场景：InboxHeader / TaskTreeHeader / 各看板的"顶部 4 卡"区。
 * 替代多处重复实现（inbox-kpi-card / tt-kpi-item 等）。
 *
 * Props:
 * - label：KPI 名称（"待处理" / "节点总数"）
 * - value：主指标值（number 或 string，支持 slot 自定义）
 * - suffix：单位（"%" / "小时" 等，紧跟 value）
 * - hint：副说明（"占比 3.2%" / "online 40 · offline 5"）
 * - tone：视觉 tone（见 utils/tone.ts）。不传则 neutral
 * - trend：up/down/flat（未指定则用 delta 推断）
 * - delta：数值变化（正负带色）
 *
 * 视觉：圆角 14，使用 sf-tone-* CSS 变量着色 label/value。
 */
import { computed } from 'vue'
import type { Tone } from '@/utils/tone'
import { metricTone, toneClass as toneClassFn } from '@/utils/tone'

const props = withDefaults(
  defineProps<{
    label: string
    value?: string | number
    suffix?: string
    hint?: string
    tone?: Tone
    trend?: 'up' | 'down' | 'flat'
    delta?: number
  }>(),
  {
    value: '',
    suffix: '',
    hint: '',
    tone: 'neutral',
    trend: undefined,
    delta: undefined,
  },
)

// tone 优先级：显式 prop > metricTone 推断 > neutral
const baseResolvedTone = computed<Tone>(() => {
  if (props.tone && props.tone !== 'neutral') return props.tone
  if (props.trend || typeof props.delta === 'number') {
    return metricTone(props.trend, props.delta)
  }
  return props.tone || 'neutral'
})

// 数值 0 时降级到 neutral，避免"失败 0 红 / 达标 0 绿"等信号色误导。
// 与 SfStatChip 模式一致（迭代 40）。仅在 value 是显式 number 0 时降级。
const isZeroSignal = computed(() => typeof props.value === 'number' && props.value === 0)
const resolvedTone = computed<Tone>(() => (isZeroSignal.value ? 'neutral' : baseResolvedTone.value))

const toneClass = computed(() => toneClassFn(resolvedTone.value))

const showTrend = computed(() => Boolean(props.trend) || typeof props.delta === 'number')

const resolvedTrend = computed<'up' | 'down' | 'flat'>(() => {
  if (props.trend) return props.trend
  if (typeof props.delta === 'number') {
    if (props.delta > 0) return 'up'
    if (props.delta < 0) return 'down'
  }
  return 'flat'
})

const trendArrow = computed(() => {
  if (resolvedTrend.value === 'up') return '↑'
  if (resolvedTrend.value === 'down') return '↓'
  return '→'
})

// delta 美化：保留 1 位小数，添加 ± 号
const formattedDelta = computed(() => {
  if (typeof props.delta !== 'number' || !Number.isFinite(props.delta)) return ''
  const rounded = Math.round(props.delta * 10) / 10
  const sign = rounded > 0 ? '+' : ''
  return `${sign}${rounded}`
})
</script>

<style scoped>
.sf-kpi-card {
  /* 使用 sf-tone-* 暴露的 CSS 变量着色；neutral 时保持原来的白底卡片外观 */
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid var(--ai-border);
  min-width: 0;
  position: relative;
}

/* 当 tone != neutral 时，用对应 tone 的浅底色强调 */
.sf-kpi-card[data-tone='danger'],
.sf-kpi-card[data-tone='warning'],
.sf-kpi-card[data-tone='success'],
.sf-kpi-card[data-tone='info'],
.sf-kpi-card[data-tone='brand'] {
  background: var(--sf-tone-bg);
  border-color: var(--sf-tone-border);
}

.sf-kpi-label {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--ai-ink-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sf-kpi-value-row {
  display: flex;
  align-items: baseline;
  gap: 4px;
  min-width: 0;
}

.sf-kpi-value {
  font-size: 26px;
  font-weight: 800;
  line-height: 1.1;
  color: var(--ai-ink-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 着色 value，但仅当 tone 非 neutral 时；避免默认白卡片过度彩色化 */
.sf-kpi-card[data-tone='danger'] .sf-kpi-value,
.sf-kpi-card[data-tone='warning'] .sf-kpi-value,
.sf-kpi-card[data-tone='success'] .sf-kpi-value,
.sf-kpi-card[data-tone='info'] .sf-kpi-value,
.sf-kpi-card[data-tone='brand'] .sf-kpi-value {
  color: var(--sf-tone-fg);
}

.sf-kpi-suffix {
  font-size: 14px;
  font-weight: 700;
  color: var(--ai-ink-3);
}

.sf-kpi-hint {
  font-size: 12px;
  font-weight: 600;
  color: var(--ai-ink-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sf-kpi-trend {
  position: absolute;
  top: 12px;
  right: 12px;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px 6px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
}

.sf-kpi-trend-up {
  background: var(--sf-tone-success-bg);
  color: var(--sf-tone-success-fg);
}

.sf-kpi-trend-down {
  background: var(--sf-tone-danger-bg);
  color: var(--sf-tone-danger-fg);
}

.sf-kpi-trend-flat {
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
}

.sf-kpi-trend-arrow {
  font-weight: 800;
}

@media (max-width: 640px) {
  .sf-kpi-card {
    padding: 12px;
  }
  .sf-kpi-value {
    font-size: 22px;
  }
}
</style>
