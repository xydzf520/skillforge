<template>
  <div class="sf-budget-progress" :class="`sf-budget-${status}`">
    <div class="sf-budget-progress-bar">
      <div
        class="sf-budget-progress-fill"
        :style="{ width: `${Math.min(percent, 100)}%` }"
      />
    </div>
    <div class="sf-budget-progress-text">
      <span class="sf-budget-label">上下文使用</span>
      <span class="sf-budget-value">
        {{ formatTokens(used) }} / {{ formatTokens(limit) }}
      </span>
      <a-tooltip v-if="status !== 'ok'" :content="statusTip">
        <span class="sf-budget-status">{{ statusLabel }}</span>
      </a-tooltip>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

/**
 * 上下文预算进度条。
 * 80% 起变黄（warn），95% 起变红（error）。compact 状态在 88-94% 之间。
 *
 * 用法：
 *   <BudgetProgress
 *     :used="conversation.budget_used"
 *     :limit="conversation.budget_limit"
 *     :status="conversation.budget_status"
 *   />
 */
const props = defineProps({
  used: { type: Number, default: 0 },
  limit: { type: Number, default: 150000 },
  status: { type: String, default: 'ok' }, // ok | warn | compact | error
})

const percent = computed(() => {
  if (!props.limit) return 0
  return Math.round((props.used / props.limit) * 100 * 10) / 10
})

const statusLabel = computed(() => {
  return {
    warn: '注意',
    compact: '即将压缩',
    error: '已超限',
  }[props.status] || ''
})

const statusTip = computed(() => {
  return {
    warn: '上下文使用超过 80%，下一次提问可能触发自动压缩',
    compact: '已达自动压缩阈值，下一次提问将自动压缩历史',
    error: '上下文已超过安全上限，请开启新会话',
  }[props.status] || ''
})

function formatTokens(n: number | null | undefined): string {
  if (!n && n !== 0) return '-'
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}
</script>

<style scoped>
.sf-budget-progress {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 8px;
  font-size: 12px;
}
.sf-budget-progress-bar {
  position: relative;
  height: 4px;
  background: var(--ai-surface-2);
  border-radius: 2px;
  overflow: hidden;
}
.sf-budget-progress-fill {
  height: 100%;
  background: var(--ai-ok-soft);
  border-radius: 2px;
  transition: width 0.3s ease, background 0.3s ease;
}
.sf-budget-ok .sf-budget-progress-fill {
  background: var(--ai-ok-soft);
}
.sf-budget-warn .sf-budget-progress-fill {
  background: var(--ai-warn-soft);
}
.sf-budget-compact .sf-budget-progress-fill {
  background: var(--ai-warn-soft);
}
.sf-budget-error .sf-budget-progress-fill {
  background: var(--ai-bad-soft);
}
.sf-budget-progress-text {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ai-ink-3);
}
.sf-budget-label {
  flex-shrink: 0;
}
.sf-budget-value {
  font-variant-numeric: tabular-nums;
  margin-left: auto;
}
.sf-budget-status {
  padding: 0 6px;
  border-radius: 8px;
  background: var(--ai-surface-2);
  cursor: help;
}
.sf-budget-warn .sf-budget-status,
.sf-budget-compact .sf-budget-status {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
}
.sf-budget-error .sf-budget-status {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}
</style>
