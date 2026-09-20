<template>
  <article
    class="tt-instance-card"
    :class="[`anomaly-${anomalyKind}`, { selected, compact }]"
    :data-anomaly="anomalyKind"
    :data-testid="`tasktree-instance-${instance.instance_id}`"
    role="button"
    tabindex="0"
    :aria-label="ariaLabel"
    :title="bridgeTooltip"
    @click="$emit('select', instance)"
    @keydown.enter.prevent="$emit('select', instance)"
    @keydown.space.prevent="$emit('select', instance)"
  >
    <div class="tt-card-head">
      <div class="tt-card-main">
        <span class="tt-status-dot" :class="nodeStatus" />
        <div class="tt-card-copy">
          <div class="tt-card-title-row">
            <strong>{{ instance.name }}</strong>
          </div>
          <div class="tt-card-metrics">
            <span class="tt-metric" :title="lastSuccessTooltip">
              <span class="tt-metric-label">最近成功</span>
              <span class="tt-metric-value">{{ lastSuccessText }}</span>
            </span>
            <span class="tt-metric" :class="{ alert: consecutiveFailures > 0 }">
              <span class="tt-metric-label">连续失败</span>
              <span class="tt-metric-value">{{ consecutiveFailures }}</span>
            </span>
            <span class="tt-metric">
              <span class="tt-metric-label">平均耗时</span>
              <span class="tt-metric-value">{{ avgDurationText }}</span>
            </span>
          </div>
        </div>
      </div>

      <div class="tt-card-side">
        <span class="tt-status-pill" :class="nodeStatus">{{ nodeStatusLabel(nodeStatus) }}</span>
        <span class="tt-heartbeat">{{ heartbeatLabel(instance) }}</span>
      </div>
    </div>

    <div v-if="instance.is_platform_node" class="tt-machine-row">
      <span class="tt-machine-role">{{ instance.machine_role || '平台机器' }}</span>
      <span v-if="instance.gpu_name" class="tt-machine-detail" :title="instance.gpu_name">
        {{ instance.gpu_name }}
      </span>
      <span v-if="gpuMemoryText" class="tt-machine-detail">显存 {{ gpuMemoryText }}</span>
      <span v-if="instance.memory_total_gb" class="tt-machine-detail">内存 {{ formatGb(instance.memory_total_gb) }}</span>
      <span class="tt-machine-cap" :class="{ training: instance.training_available, media: instance.media_available }">
        {{ machineCapabilityText }}
      </span>
    </div>

    <div class="tt-card-timeline-row">
      <span class="tt-timeline-label">近 {{ windowLabel(window) }}</span>
      <RunTimelineStrip
        class="tt-card-timeline"
        :runs="instance.recent_skills || []"
        :window="window"
        :selected-range="selectedRange"
        :buckets="meta.buckets"
        @select-bucket="$emit('focus-bucket', instance, $event)"
      />
      <span class="tt-timeline-count">{{ windowRunCount }} 次</span>
    </div>

    <button
      v-if="alertRun"
      type="button"
      class="tt-card-foot--alert"
      @click.stop="$emit('open-diagnosis', instance, alertRun)"
    >
      <span class="tt-alert-title">{{ alertTitle }}</span>
      <span class="tt-alert-text">{{ alertText }}</span>
    </button>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { relativeTime } from '@/utils/format'
import RunTimelineStrip from './RunTimelineStrip.vue'
import {
  anomalyLabel,
  ensureInstanceMeta,
  formatDuration,
  heartbeatLabel,
  nodeStatusLabel,
  windowLabel,
} from './helpers'
import type {
  TaskTreeInstanceNode,
  TaskTreeSkillRunItem,
  TaskTreeTimelineBucket,
  TaskTreeTimeWindowValue,
} from './types'

const props = defineProps<{
  instance: TaskTreeInstanceNode
  window: TaskTreeTimeWindowValue
  selected: boolean
  selectedRange?: { start: string; end: string } | null
  compact?: boolean
}>()

defineEmits<{
  select: [instance: TaskTreeInstanceNode]
  'open-diagnosis': [instance: TaskTreeInstanceNode, run: TaskTreeSkillRunItem]
  'focus-bucket': [instance: TaskTreeInstanceNode, bucket: TaskTreeTimelineBucket]
}>()

const meta = computed(() => ensureInstanceMeta(props.instance, props.window))
const anomalyKind = computed(() => meta.value.anomalyKind)
const nodeStatus = computed(() => meta.value.nodeStatus)
const windowRunCount = computed(() => meta.value.windowRunCount)
const consecutiveFailures = computed(() => meta.value.consecutiveFailures)
const alertRun = computed(() => meta.value.stuckRun || meta.value.latestAnomalyRun)
const alertTitle = computed(() => (meta.value.stuckRun ? '运行卡住' : '最近异常'))
const alertText = computed(() => {
  const run = alertRun.value
  if (!run) return ''
  return [run.skill_name || run.skill_id || run.run_id, run.error_message].filter(Boolean).join(' · ')
})

const lastSuccessText = computed(() => {
  const ts = meta.value.lastSuccessAt
  if (!ts) return '-'
  return relativeTime(ts)
})

const lastSuccessTooltip = computed(() => {
  const ts = meta.value.lastSuccessAt
  return ts || '窗口内暂无成功执行'
})

const avgDurationText = computed(() => {
  const avg = meta.value.avgDurationSeconds
  if (avg == null) return '-'
  return formatDuration(avg)
})

const gpuMemoryText = computed(() => {
  const total = props.instance.gpu_memory_total_gb
  if (total == null) return ''
  const used = props.instance.gpu_memory_used_gb
  return used == null ? formatGb(total) : `${formatGb(used)} / ${formatGb(total)}`
})

const machineCapabilityText = computed(() => {
  if (props.instance.media_available) return '可生成'
  if (props.instance.machine_role === '视频生成') return '待配置'
  return props.instance.training_available ? '可训练' : '可调用'
})

const bridgeTooltip = computed(() => {
  const parts = [
    props.instance.bridge_version ? `Bridge ${props.instance.bridge_version}` : '',
    platformText(props.instance.bridge_platform),
    `类型 ${runtimeTypeText(props.instance)}`,
  ].filter(Boolean)
  return parts.join(' · ')
})

const ariaLabel = computed(() => {
  const pieces = [
    props.instance.name,
    nodeStatusLabel(nodeStatus.value),
    anomalyKind.value !== 'healthy' ? anomalyLabel(anomalyKind.value) : '',
  ].filter(Boolean)
  return pieces.join('，')
})

function runtimeTypeText(instance: TaskTreeInstanceNode): string {
  const kind = (instance.runtime_type || instance.bridge_gateway_kind || instance.agent_type || '').toLowerCase()
  if (kind === 'openclaw') return 'OpenClaw'
  if (kind === 'openclaw-cn') return 'OpenClaw CN'
  if (kind === 'aiclaw') return 'AIClaw'
  if (kind === 'hermes') return 'Hermes'
  return kind || '-'
}

function platformText(value?: string | null): string {
  const platform = String(value || '').toLowerCase()
  if (platform === 'linux') return 'Linux'
  if (platform === 'darwin') return 'macOS'
  if (platform === 'win32' || platform === 'windows') return 'Windows'
  return value || ''
}

function formatGb(value: number): string {
  return `${Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 1 })} GB`
}
</script>

<style scoped>
/* 设计稿 .ai-card：surface + border + radius 8px，无大阴影；
   异常状态用 soft 色背景或 1.5px border 增强，不上 gradient。 */
.tt-instance-card {
  --tt-card-border: var(--ai-border);
  --tt-card-bg: var(--ai-surface);
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  background: var(--tt-card-bg);
  border: 1px solid var(--tt-card-border);
  border-radius: var(--ai-radius);
  box-shadow: none;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
  font-family: var(--ai-font-sans);
}

.tt-instance-card:hover {
  background: var(--ai-surface-2);
}

.tt-instance-card.anomaly-high_failure,
.tt-instance-card.anomaly-stuck {
  --tt-card-border: var(--ai-border);
  --tt-card-bg: var(--ai-surface);
}

.tt-instance-card.anomaly-offline {
  --tt-card-border: var(--ai-border);
  --tt-card-bg: var(--ai-surface);
}

.tt-instance-card.selected {
  --tt-card-border: var(--ai-ink-1);
  border-width: 1.5px;
  padding: 11.5px 13.5px; /* compensate for 1.5px border */
}

.tt-card-head,
.tt-card-main,
.tt-card-side,
.tt-card-stats {
  display: flex;
}

.tt-card-head {
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.tt-card-main {
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.tt-card-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.tt-card-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.tt-card-title-row strong {
  font-size: 13px;
  color: var(--ai-ink-1);
  font-weight: 600;
  letter-spacing: -0.005em;
}

.tt-card-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  margin-top: 2px;
}

.tt-metric {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  font-size: 11px;
  color: var(--ai-ink-3);
  font-weight: 450;
}

.tt-metric-label {
  font-size: 11px;
  color: var(--ai-ink-4);
  font-weight: 450;
}

.tt-metric-value {
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 500;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

.tt-metric.alert .tt-metric-value {
  color: var(--ai-bad);
}

.tt-card-side {
  flex-direction: row;
  align-items: flex-end;
  gap: 6px;
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 450;
  white-space: nowrap;
}

.tt-machine-row {
  display: flex;
  align-items: center;
  gap: 6px 12px;
  min-width: 0;
  padding: 7px 9px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 11px;
  font-family: var(--ai-font-mono);
}

.tt-machine-role {
  color: var(--ai-ink-1);
  font-weight: 600;
  white-space: nowrap;
}

.tt-machine-detail {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tt-machine-cap {
  flex: none;
  margin-left: auto;
  padding: 2px 5px;
  border-radius: 4px;
  color: var(--ai-ink-3);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}

.tt-machine-cap.training,
.tt-machine-cap.media {
  color: var(--ai-ok);
}

.tt-status-dot {
  width: 7px;
  height: 7px;
  margin-top: 4px;
  border-radius: 50%;
  background: var(--ai-ink-4);
  flex: none;
}

.tt-status-dot.online { background: var(--ai-ok); }
.tt-status-dot.maybe_offline { background: var(--ai-warn); }
.tt-status-dot.offline { background: var(--ai-bad); }

.tt-heartbeat {
  color: var(--ai-ink-4);
}

.tt-heartbeat {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

.tt-status-pill {
  display: inline-flex;
  align-items: center;
  height: 16px;
  padding: 0 5px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
}
.tt-status-pill.online {
  color: var(--ai-ok);
  background: var(--ai-ok-soft);
}
.tt-status-pill.offline {
  color: var(--ai-bad);
  background: var(--ai-bad-soft);
}
.tt-status-pill.maybe_offline {
  color: var(--ai-warn);
  background: var(--ai-warn-soft);
}

.tt-card-timeline-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 2px;
}

.tt-card-foot--alert {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 7px 8px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  cursor: pointer;
  text-align: left;
  font-family: var(--ai-font-sans);
}
.tt-card-foot--alert:hover {
  border-color: color-mix(in srgb, var(--ai-bad) 30%, transparent);
}
.tt-alert-title {
  flex: 0 0 auto;
  font-size: 11px;
  font-weight: 600;
}
.tt-alert-text {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-ink-2);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tt-card-timeline {
  flex: 1;
  min-width: 0;
}

.tt-timeline-label,
.tt-timeline-count {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
}

.tt-timeline-label {
  width: 40px;
}

.tt-timeline-count {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

.tt-card-stats {
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--ai-ink-3);
  font-size: 11.5px;
  font-weight: 450;
  font-variant-numeric: tabular-nums;
}

.tt-card-foot {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 450;
}

.tt-card-foot--alert {
  padding: 0;
  border: none;
  background: transparent;
  color: var(--ai-bad);
  cursor: pointer;
  text-align: left;
  font-family: var(--ai-font-sans);
}

.tt-instance-card.compact {
  padding: 10px 12px;
  gap: 8px;
}

.tt-instance-card.compact .tt-card-title-row strong {
  font-size: 12.5px;
}

.tt-instance-card.compact .tt-metric-value {
  font-size: 11.5px;
}

.tt-instance-card.compact .tt-card-metrics {
  gap: 4px 10px;
}

.tt-instance-card:focus-visible {
  outline: 1.5px solid var(--ai-ink-1);
  outline-offset: 2px;
}

@media (max-width: 640px) {
  .tt-card-head,
  .tt-card-stats {
    flex-direction: column;
    align-items: flex-start;
  }

.tt-card-side {
    align-items: flex-start;
  }
}
</style>
