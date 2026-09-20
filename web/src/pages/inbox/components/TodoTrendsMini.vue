<template>
  <div v-if="points?.length" class="trends-mini" data-testid="todo-trends-mini">
    <div class="trends-mini-head">
      <span class="trends-mini-label">{{ days }} 天待办趋势</span>
      <span class="trends-mini-total">共 {{ totalCount }} 条</span>
    </div>
    <svg
      class="trends-mini-svg"
      :viewBox="`0 0 ${width} ${height}`"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <polyline
        v-if="linePoints"
        :points="linePoints"
        fill="none"
        stroke="currentColor"
        stroke-width="1.6"
        vector-effect="non-scaling-stroke"
      />
      <circle
        v-for="(pt, idx) in pointDots"
        :key="idx"
        :cx="pt.x"
        :cy="pt.y"
        r="1.5"
        fill="currentColor"
      />
    </svg>
    <div class="trends-mini-ticks">
      <span>{{ firstDay }}</span>
      <span>{{ lastDay }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'

type TrendPoint = {
  day: string
  pending: number
  approved: number
  rejected: number
  expired: number
  resolved_by_peer: number
  total: number
}

const props = defineProps({
  points: { type: Array as PropType<TrendPoint[]>, default: () => [] },
  days: { type: Number, default: 7 },
})

const width = 320
const height = 48

const totalCount = computed(() => (props.points || []).reduce((sum, p) => sum + (p.total || 0), 0))
const firstDay = computed(() => props.points?.[0]?.day?.slice(5) ?? '')
const lastDay = computed(() => props.points?.[props.points.length - 1]?.day?.slice(5) ?? '')

const maxValue = computed(() => Math.max(1, ...(props.points || []).map((p) => p.total || 0)))

const pointDots = computed(() => {
  if (!props.points?.length) return []
  const step = props.points.length <= 1 ? 0 : width / (props.points.length - 1)
  return props.points.map((p, idx) => ({
    x: idx * step,
    y: height - (p.total / maxValue.value) * (height - 4) - 2,
  }))
})

const linePoints = computed(() =>
  pointDots.value.map((pt) => `${pt.x.toFixed(2)},${pt.y.toFixed(2)}`).join(' ')
)
</script>

<style scoped>
.trends-mini {
  padding: 10px 14px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.85);
  border: 1px solid var(--ai-border);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.trends-mini-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: 11px;
  color: var(--ai-ink-3);
  font-weight: 700;
  letter-spacing: 0.04em;
}

.trends-mini-label {
  text-transform: uppercase;
}

.trends-mini-total {
  color: var(--ai-ink-2);
  font-weight: 800;
}

.trends-mini-svg {
  width: 100%;
  height: 48px;
  color: var(--ai-info);
}

.trends-mini-ticks {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: var(--ai-ink-3);
}
</style>
