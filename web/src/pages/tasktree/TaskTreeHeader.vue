<template>
  <section class="tt-header-shell" data-testid="tasktree-header">
    <div class="tt-header-top ai-pagehead">
      <div class="tt-title-block">
        <div class="ai-crumbs">监控 · 任务树</div>
        <h1 class="ai-title">任务树</h1>
        <p class="ai-sub">
          所有部门 Agent 节点的运行状态、调度与诊断
          <span v-if="projectedAt"> · 刷新 {{ formatTime(projectedAt) }}</span>
        </p>
      </div>

      <div class="tt-header-meta">
        <span v-if="!refreshing && nextRefreshSeconds !== undefined" class="ai-pill">
          下次 {{ nextRefreshSeconds }}s
        </span>
        <a-tooltip v-if="etag" content="版本戳（内部缓存命中用）">
          <span class="ai-pill tt-meta-chip--debug mono">版本 {{ etag.slice(0, 8) }}</span>
        </a-tooltip>
        <span v-if="refreshing" class="ai-pill info">刷新中</span>
        <a-dropdown
          v-if="(anomalyCount ?? 0) > 0"
          trigger="click"
          position="br"
        >
          <button type="button" class="ai-btn tt-bulk-btn">
            <SfShellIcon name="warn" class="tt-bulk-warn-icon" />
            批量操作
          </button>
          <template #content>
            <a-doption @click="$emit('copy-offline')">复制离线节点列表</a-doption>
          </template>
        </a-dropdown>
        <button type="button" class="ai-btn primary" :disabled="refreshing" @click="$emit('refresh')">
          <SfShellIcon name="refresh" />
          {{ refreshing ? '刷新中' : '刷新' }}
        </button>
      </div>
    </div>

    <div class="tt-kpi-strip">
      <div
        v-for="card in cards"
        :key="card.key"
        class="kpi-cell"
        :class="{ 'kpi-danger': card.danger, 'kpi-ok': card.ok && !card.danger, loading }"
      >
        <div class="kpi-label">{{ card.label }}</div>
        <div class="kpi-value">{{ loading ? '--' : card.value }}</div>
        <div class="kpi-sub">{{ loading ? '' : card.meta }}</div>
      </div>
    </div>

    <div v-if="loadError" class="tt-error-banner">
      <span>加载失败：{{ loadError }}</span>
      <a-button size="mini" type="outline" @click="$emit('refresh')">重试</a-button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { formatTime } from '@/utils/format'
import { formatPercent } from './helpers'
import type { TaskTreeDashboardMetrics } from './types'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

const props = defineProps<{
  dashboard: TaskTreeDashboardMetrics
  projectedAt: string
  etag: string
  refreshing: boolean
  loadError: string
  loading?: boolean
  nextRefreshSeconds?: number
  anomalyCount?: number
}>()

defineEmits<{
  refresh: []
  'copy-offline': []
}>()

const cards = computed(() => {
  const offlineRatio = props.dashboard.totalNodes > 0
    ? props.dashboard.totalOffline / props.dashboard.totalNodes
    : 0

  return [
    {
      key: 'nodes',
      label: '节点总数',
      value: props.dashboard.totalNodes,
      meta: `在线 ${props.dashboard.totalOnline} · 离线 ${props.dashboard.totalOffline}`,
      danger: false,
    },
    {
      key: 'offline',
      label: '离线',
      value: props.dashboard.totalOffline,
      meta: `占比 ${formatPercent(offlineRatio)}`,
      danger: props.dashboard.totalOffline > 0,
    },
    {
      key: 'running',
      label: '运行中',
      value: props.dashboard.totalRunning,
      meta: `当前活跃 ${props.dashboard.totalRunning}`,
      danger: false,
    },
    {
      key: 'failed',
      label: '今日失败率',
      value: formatPercent(props.dashboard.todayFailedRate),
      meta: `失败 ${props.dashboard.todayFailed} / 总 ${props.dashboard.todayExecutions}`,
      // 对齐设计稿 task-tree.jsx：>0.2 红色（异常）/ ===0 绿色（健康，无失败）/ 其他黑色
      danger: props.dashboard.todayFailedRate > 0.2,
      ok: props.dashboard.todayFailedRate === 0,
    },
    {
      key: 'schedule',
      label: '即将到期定时',
      value: props.dashboard.scheduledDueSoon,
      meta: '未来 1 小时内',
      danger: false,
    },
  ]
})
</script>

<style scoped>
.tt-bulk-warn-icon {
  color: var(--ai-warn);
}

.tt-header-shell {
  display: flex;
  flex-direction: column;
  gap: 0;
  background: var(--ai-surface);
  border: 0;
  border-bottom: 1px solid var(--ai-border);
  border-radius: 0;
  overflow: hidden;
  box-shadow: none;
  font-family: var(--ai-font-sans);
}

.tt-header-top {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  min-width: 0;
}

.tt-title-block {
  display: flex;
  flex-direction: column;
  gap: 0;
  flex: 1 1 auto;
  min-width: 0;
}

.tt-title-block .ai-title {
  margin: 0;
  line-height: 1.15;
}

.tt-header-meta {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
  flex: 0 0 auto;
}
.tt-header-meta .ai-btn {
  cursor: pointer;
  font-family: var(--ai-font-sans);
}
.tt-header-meta .ai-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.tt-header-meta .ai-btn svg {
  width: 12px;
  height: 12px;
  flex: 0 0 12px;
}

.tt-meta-chip--debug {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

.tt-error-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border-top: 1px solid var(--ai-border);
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  font-size: 13px;
  font-weight: 500;
}

/* KPI 条：响应式网格，副文案必须完整显示 */
.tt-kpi-strip {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  border-top: 0;
  border-bottom: 1px solid var(--ai-border);
}

.kpi-cell {
  box-sizing: border-box;
  min-width: 0;
  min-height: 72px;
  padding: 12px 18px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  border-left: 1px solid var(--ai-border);
  background: transparent;
}
.kpi-cell:first-child { border-left: 0; }
.kpi-cell.loading { opacity: 0.6; }

.kpi-label {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0;
  line-height: 1.25;
}

.kpi-value {
  color: var(--ai-ink-1);
  font-size: 22px;
  font-weight: 600;
  letter-spacing: 0;
  line-height: 1.12;
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
}

.kpi-sub {
  min-width: 0;
  margin-top: 1px;
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 500;
  line-height: 1.3;
  white-space: normal;
  overflow-wrap: anywhere;
}

.kpi-cell.kpi-danger .kpi-value { color: var(--ai-bad); }
.kpi-cell.kpi-ok .kpi-value { color: var(--ai-ok); }
.kpi-cell.kpi-danger .kpi-sub { color: var(--ai-bad); }

@media (max-width: 1080px) {
  .tt-header-top {
    flex-wrap: wrap;
  }
  .tt-kpi-strip {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .kpi-cell:nth-child(3n + 1) {
    border-left: 0;
  }
  .kpi-cell:nth-child(n + 4) {
    border-top: 1px solid var(--ai-border);
  }
}

@media (max-width: 768px) {
  .tt-header-meta {
    justify-content: flex-start;
  }
  .tt-meta-chip--debug {
    display: none;
  }
}

@media (max-width: 720px) {
  .tt-kpi-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .kpi-cell {
    border-left: 1px solid var(--ai-border);
  }
  .kpi-cell:nth-child(odd) {
    border-left: 0;
  }
  .kpi-cell:nth-child(n + 3) {
    border-top: 1px solid var(--ai-border);
  }
}


@media (max-width: 560px) {
  .tt-header-top {
    flex-direction: column;
    align-items: flex-start;
  }

  .tt-title-block {
    min-width: 0;
  }

  .tt-error-banner {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
