<template>
  <div class="tt-timeline-wrap">
    <div
      class="tt-timeline"
      data-testid="tasktree-timeline"
      :style="{ '--tt-bucket-count': String(resolvedBuckets.length) }"
    >
      <button
        v-for="bucket in resolvedBuckets"
        :key="bucket.index"
        type="button"
        class="tt-timeline-bucket"
        :class="{ selected: isSelected(bucket) }"
        :data-state="bucket.state"
        :title="timelineBucketTooltip(bucket)"
        :style="{ backgroundColor: timelineBucketColor(bucket.state) }"
        @click="$emit('select-bucket', bucket)"
      />
    </div>
    <div
      v-if="showAxis && axisTicks.length"
      class="tt-timeline-axis"
      aria-hidden="true"
      :style="{ '--tt-bucket-count': String(resolvedBuckets.length) }"
    >
      <span
        v-for="tick in axisTicks"
        :key="tick.index"
        class="tt-axis-tick"
        :style="{ gridColumn: `${tick.index + 1} / span 1` }"
      >{{ tick.label }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  buildTimelineBuckets,
  timelineBucketColor,
  timelineBucketTooltip,
} from './helpers'
import { TaskTreeTimeWindow } from './types'
import type {
  TaskTreeSkillRunItem,
  TaskTreeTimelineBucket,
  TaskTreeTimeWindowValue,
} from './types'

const props = defineProps<{
  runs: TaskTreeSkillRunItem[]
  window: TaskTreeTimeWindowValue
  selectedRange?: { start: string; end: string } | null
  // 预计算 buckets（从 TaskTreeInstanceCard 的 meta 传入），避免重复计算。
  buckets?: TaskTreeTimelineBucket[] | null
  showAxis?: boolean
}>()

defineEmits<{
  'select-bucket': [bucket: TaskTreeTimelineBucket]
}>()

const resolvedBuckets = computed(() => {
  if (props.buckets && props.buckets.length) return props.buckets
  return buildTimelineBuckets(props.runs || [], props.window)
})
const showAxis = computed(() => props.showAxis === true)

function isSelected(bucket: TaskTreeTimelineBucket): boolean {
  return props.selectedRange?.start === bucket.startedAt && props.selectedRange?.end === bucket.endedAt
}

const NOW_LABEL = '现在'

// 轴刻度用相对时间，避免滚动窗口里的具体时刻被误读成定时任务计划。
// 1h 每 15min 一个（4 个），24h 每 6h 一个（4 个），7d 每 24h 一个（7 个）。
const axisTicks = computed(() => {
  const count = resolvedBuckets.value.length
  if (!count) return []
  let step: number
  if (props.window === TaskTreeTimeWindow.ONE_HOUR) {
    step = Math.max(1, Math.floor(count / 4))
  } else if (props.window === TaskTreeTimeWindow.SEVEN_DAYS) {
    step = Math.max(1, Math.floor(count / 7))
  } else {
    step = Math.max(1, Math.floor(count / 4))
  }
  const ticks: Array<{ index: number; label: string }> = []
  for (let i = 0; i < count; i += step) {
    ticks.push({ index: i, label: relativeTickLabel(i, count) })
  }
  // 最后一个桶（现在）始终显示
  if (ticks[ticks.length - 1]?.index !== count - 1) {
    ticks.push({ index: count - 1, label: NOW_LABEL })
  } else {
    ticks[ticks.length - 1].label = NOW_LABEL
  }
  return ticks
})

function relativeTickLabel(index: number, count: number): string {
  if (index >= count - 1) return NOW_LABEL
  const bucketCountFromEnd = Math.max(1, count - index)
  if (props.window === TaskTreeTimeWindow.ONE_HOUR) {
    return `${bucketCountFromEnd}m前`
  }
  if (props.window === TaskTreeTimeWindow.SEVEN_DAYS) {
    return `${Math.max(1, Math.round(bucketCountFromEnd / 12))}d前`
  }
  return `${Math.max(1, Math.round(bucketCountFromEnd / 2))}h前`
}
</script>

<style scoped>
/* 设计稿 sprite bar 风格：细 bar + 紧凑间距 + 方角 */
.tt-timeline {
  display: grid;
  grid-template-columns: repeat(var(--tt-bucket-count, 48), minmax(0, 1fr));
  gap: 2px;
}

.tt-timeline-bucket {
  width: 100%;
  min-width: 0;
  height: 16px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 2px;
  cursor: pointer;
  transition: opacity 0.15s ease, border-color 0.15s ease;
}

.tt-timeline-bucket:hover {
  opacity: 0.78;
}

.tt-timeline-bucket.selected {
  border-color: var(--ai-ink-1);
}

.tt-timeline-wrap {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tt-timeline-axis {
  display: grid;
  grid-template-columns: repeat(var(--tt-bucket-count, 48), minmax(0, 1fr));
  gap: 2px;
  font-size: 10px;
  color: var(--ai-ink-4);
  font-weight: 500;
  letter-spacing: 0.04em;
  line-height: 1;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

.tt-axis-tick {
  white-space: nowrap;
  pointer-events: none;
}

.tt-axis-tick:last-child {
  justify-self: end;
  color: var(--ai-ink-1);
}
</style>
