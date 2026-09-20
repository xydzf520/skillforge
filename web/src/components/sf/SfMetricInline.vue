<template>
  <span class="sf-metric-inline" :class="[effectiveToneClass]" :data-tone="effectiveTone">
    <span v-if="label" class="sf-metric-inline-label">{{ label }}</span>
    <span class="sf-metric-inline-value">
      <slot>{{ value }}</slot>
    </span>
  </span>
</template>

<script setup lang="ts">
/**
 * SfMetricInline — 卡内迷你嵌入式指标
 *
 * 使用场景：portal skill 卡片 / tasktree instance card 内部的"最近成功 12m 前"
 * 这种 label + value 两段式迷你指标。
 *
 * 与 SfStatChip 的区别：
 * - SfStatChip 是独立 chip（圆角胶囊 + 背景色）
 * - SfMetricInline 是"inline 文本 + label 前缀"，更紧凑，用在卡内不抢戏
 *
 * Props:
 * - label：前缀文案（"最近成功"）
 * - value：数值（"12m 前" / "98%"）
 * - tone：非 neutral 时用 tone.fg 着色 value
 */
import { computed } from 'vue'
import type { Tone } from '@/utils/tone'
import { toneClass as toneClassFn } from '@/utils/tone'

const props = withDefaults(
  defineProps<{
    label?: string
    value?: string | number
    tone?: Tone
  }>(),
  {
    label: '',
    value: '',
    tone: 'neutral',
  },
)

// 与 SfStatChip / SfKpiCard 模式一致（迭代 40-41）：
// value 是显式数字 0 时降级 neutral，避免"-12 信号色 0"误导。
const isZeroSignal = computed(() => typeof props.value === 'number' && props.value === 0)
const effectiveTone = computed<Tone>(() => (isZeroSignal.value ? 'neutral' : props.tone))
const effectiveToneClass = computed(() => toneClassFn(effectiveTone.value))
</script>

<style scoped>
.sf-metric-inline {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ai-ink-3);
  line-height: 1.4;
}

.sf-metric-inline-label {
  font-size: 11px;
  letter-spacing: 0.04em;
  color: var(--ai-ink-3);
  opacity: 0.8;
}

.sf-metric-inline-value {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 700;
}

/* 非 neutral tone → value 着色；label 保持灰以不抢戏 */
.sf-metric-inline[data-tone='danger'] .sf-metric-inline-value,
.sf-metric-inline[data-tone='warning'] .sf-metric-inline-value,
.sf-metric-inline[data-tone='success'] .sf-metric-inline-value,
.sf-metric-inline[data-tone='info'] .sf-metric-inline-value,
.sf-metric-inline[data-tone='brand'] .sf-metric-inline-value {
  color: var(--sf-tone-fg);
}
</style>
