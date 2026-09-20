<template>
  <span class="sf-stat-chip" :class="[effectiveToneClass]" :data-tone="effectiveTone">
    <span v-if="label" class="sf-stat-chip-label">{{ label }}</span>
    <span class="sf-stat-chip-value">
      <slot>{{ value }}</slot>
    </span>
  </span>
</template>

<script setup lang="ts">
/**
 * SfStatChip — 轻量统计 chip
 *
 * 使用场景：Dashboard / 审计日志 底部的 stat-chip 条；toolbar 里的小型辅助统计。
 * 替代各页重复实现（`.stat-chip` / `.stat-blue` 等类）。
 *
 * Props:
 * - label：前缀文案（"总执行" / "成功"）
 * - value：数值（若用 slot 则此 prop 忽略）
 * - tone：视觉 tone（见 utils/tone.ts）
 *
 * 典型用法：
 *   <SfStatChip label="成功" :value="245" tone="success" />
 *   <SfStatChip label="失败" tone="danger"><b>12</b> 次</SfStatChip>
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

// 数值为 0 时降级到 neutral，避免"运行中 0 蓝 / 异常 0 红"等信号色误导。
// 仅在 value 是显式数字 0 时降级；slot 形态或非零值保留原 tone。
const isZeroSignal = computed(() => typeof props.value === 'number' && props.value === 0)
const effectiveTone = computed<Tone>(() => (isZeroSignal.value ? 'neutral' : props.tone))
const effectiveToneClass = computed(() => toneClassFn(effectiveTone.value))
</script>

<style scoped>
.sf-stat-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 14px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
  background: var(--sf-tone-bg);
  color: var(--sf-tone-fg);
  border: 1px solid transparent;
  white-space: nowrap;
}

/* neutral 时，用更淡的灰，避免 chip 太"死" */
.sf-stat-chip[data-tone='neutral'] {
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
}

.sf-stat-chip-label {
  color: inherit;
  opacity: 0.82;
  font-weight: 600;
}

.sf-stat-chip-value {
  color: inherit;
  font-weight: 800;
}

.sf-stat-chip-value :deep(b) {
  font-size: var(--sf-text-body);
  font-weight: 800;
}
</style>
