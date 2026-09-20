<template>
  <div class="ai-health-bar" :title="`健康度 ${value}/100`">
    <div class="health-track">
      <div class="health-fill" :style="{ width: `${pct}%`, background: color }" />
    </div>
    <span class="health-num mono" :style="{ color }">{{ value }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

/*
 * 设计稿 design/src/skills.jsx 中的 HealthBar 组件 1:1 还原。
 * 5 档颜色：>=90 ok / >=80 ink-3 / >=70 warn / <70 bad。
 */
const props = defineProps<{ value: number | null | undefined }>()

const pct = computed(() => {
  const v = Number(props.value ?? 0)
  if (!Number.isFinite(v)) return 0
  return Math.max(0, Math.min(100, v))
})

const color = computed(() => {
  const v = pct.value
  if (v >= 90) return 'var(--ai-ok)'
  if (v >= 80) return 'var(--ai-ink-3)'
  if (v >= 70) return 'var(--ai-warn)'
  return 'var(--ai-bad)'
})
</script>

<style scoped>
.ai-health-bar {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.health-track {
  width: 44px;
  height: 4px;
  background: var(--ai-surface-3);
  border-radius: 2px;
  overflow: hidden;
  flex: 0 0 auto;
}

.health-fill {
  height: 100%;
  transition: width 0.2s ease, background 0.2s ease;
}

.health-num {
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  font-variant-numeric: tabular-nums;
  font-weight: 500;
  letter-spacing: 0;
}
</style>
