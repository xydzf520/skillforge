<template>
  <!--
    P0-3：原本一行横排所有异常节点 chip（可能 20+ 个彩色墙），
    现在改为 3 组 summary（运行中 / 异常 / 已完成）+ 折叠式"展开详情"。
    - 默认只显示 3 个 SfStatChip，视觉收敛
    - 展开后才渲染完整的异常节点 chip 列表，点击可定位
    - 数据来源仍然是父组件传下来的 items（异常节点列表）
  -->
  <div class="tt-anomaly-strip" data-testid="tasktree-anomaly-strip">
    <div class="tt-anomaly-head">
      <!-- 3 组 summary chip：全健康时合并为一行简洁提示 -->
      <div class="tt-anomaly-summary">
        <template v-if="anomalyCount === 0">
          <SfStatChip label="全部健康" tone="success" :value="completedCount" data-testid="tt-summary-healthy" />
          <span v-if="runningCount > 0" class="tt-summary-extra">· {{ runningCount }} 运行中</span>
        </template>
        <template v-else>
          <SfStatChip label="运行中" tone="info" :value="runningCount" data-testid="tt-summary-running" />
          <SfStatChip label="异常" tone="danger" :value="anomalyCount" data-testid="tt-summary-anomaly" />
          <SfStatChip label="已完成" tone="success" :value="completedCount" data-testid="tt-summary-completed" />
        </template>
      </div>

      <a-button
        v-if="items.length"
        size="mini"
        type="text"
        class="tt-anomaly-toggle"
        @click="expanded = !expanded"
        :data-testid="'tasktree-anomaly-toggle'"
      >
        {{ expanded ? '收起详情' : `展开详情（${items.length}）` }}
      </a-button>
    </div>

    <!-- 展开后：原来的异常 chip 列表 -->
    <div v-if="expanded && items.length" class="tt-anomaly-chips">
      <button
        v-for="item in items"
        :key="item.instance.instance_id"
        type="button"
        class="tt-anomaly-chip"
        :class="[
          `kind-${item.anomalyKind}`,
          { selected: item.instance.instance_id === selectedInstanceId },
        ]"
        :title="`${item.departmentName} · ${kindLabel(item.anomalyKind)}`"
        :aria-label="`${item.instance.name}，${item.departmentName}，${kindLabel(item.anomalyKind)}`"
        @click="$emit('focus', item.instance.instance_id)"
      >
        <span class="tt-anomaly-kind">{{ kindLabel(item.anomalyKind) }}</span>
        <span class="tt-anomaly-name">{{ item.instance.name }}</span>
        <span class="tt-anomaly-dept">{{ item.departmentName }}</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { anomalyLabel } from './helpers'
import type { TaskTreeInstanceAnomaly, TaskTreeInstanceNode } from './types'
import { SfStatChip } from '@/components/sf'

const props = defineProps<{
  items: Array<{
    instance: TaskTreeInstanceNode
    departmentName: string
    anomalyKind: TaskTreeInstanceAnomaly
  }>
  selectedInstanceId: string
  // 可选：父组件传入 dashboard metrics，用于 summary；未传则兜底用 items.length
  runningCount?: number
  completedCount?: number
}>()

defineEmits<{
  focus: [instanceId: string]
}>()

// 是否展开详情（默认收起，保证视觉收敛）
const expanded = ref(false)

// 异常数量 = items.length（父组件只会把异常节点传过来）
const anomalyCount = computed(() => props.items.length)
const runningCount = computed(() => props.runningCount ?? 0)
const completedCount = computed(() => props.completedCount ?? 0)

function kindLabel(kind: TaskTreeInstanceAnomaly): string {
  return anomalyLabel(kind)
}
</script>

<style scoped>
/* 设计稿 .ai-card 风格：surface + border + radius 8px，无大阴影 */
.tt-anomaly-strip {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 14px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  box-shadow: none;
}

.tt-anomaly-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.tt-anomaly-summary {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.tt-summary-extra {
  color: var(--ai-ink-3);
  font-size: 12px;
  font-weight: 500;
}

.tt-anomaly-toggle {
  color: var(--ai-ink-3);
  font-weight: 500;
}

.tt-anomaly-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding-top: 6px;
  /* webkit 1px dashed 渲染成 solid，用 background gradient pattern 代替 */
  background-image: linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%);
  background-position: top;
  background-size: 6px 1px;
  background-repeat: repeat-x;
}

/* 设计稿 .ai-pill 风格：方角 4px，矮 chip */
.tt-anomaly-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding: 0 8px;
  border-radius: 4px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: 11.5px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.tt-anomaly-chip:hover {
  background: var(--ai-surface-3);
  color: var(--ai-ink-1);
}

.tt-anomaly-chip.selected {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  border-color: var(--ai-ink-1);
}
.tt-anomaly-chip.selected .tt-anomaly-kind,
.tt-anomaly-chip.selected .tt-anomaly-dept {
  color: var(--ai-surface);
}

.tt-anomaly-chip.kind-offline {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}

.tt-anomaly-chip.kind-stuck {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}

.tt-anomaly-chip.kind-high_failure {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}

.tt-anomaly-kind {
  padding: 0 5px;
  border-radius: 3px;
  background: rgba(0, 0, 0, 0.06);
  font-size: 10.5px;
  font-weight: 500;
}

.tt-anomaly-name {
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tt-anomaly-dept {
  color: var(--ai-ink-4);
  font-size: 10.5px;
  font-weight: 500;
}
</style>
