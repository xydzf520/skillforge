<template>
  <article
    class="sf-inbox-card todo-card"
    :class="[`status-${item.status}`, `urgency-${urgency}`, { 'todo-card--dense': dense }]"
    :data-testid="`todo-card-${item.id}`"
  >
    <!-- 密集模式：单行行式布局，对齐 design/inbox.jsx 的 row-style card -->
    <template v-if="dense">
      <div class="todo-dense-row">
        <!-- 左：业务优先级 pill；审批级别单独用 chip 展示 -->
        <span
          class="ai-pill todo-dense-prio"
          :class="prioPillClass"
          :title="prioTitle"
        >{{ prioLabel }}</span>

        <!-- 主体：kind/status/mode chips + 标题 + SKU + 时间 -->
        <div class="todo-dense-main">
          <div class="todo-dense-chip-row">
            <span class="ai-pill todo-dense-chip" :class="kindPillClass">{{ todoKindLabel(item.kind) }}</span>
            <span class="ai-pill todo-dense-chip" :class="statusPillClass">{{ todoStatusLabel(item.status) }}</span>
            <span
              v-if="item.decision_mode && item.decision_mode !== 'any_of'"
              class="ai-pill todo-dense-chip"
              :title="decisionModeTooltip"
            >{{ decisionModeLabel(item.decision_mode) }}</span>
            <span
              v-if="item.approval_level"
              class="todo-level-chip todo-dense-chip"
              :class="`level-${item.approval_level.toLowerCase()}`"
              :title="`审批级别 ${item.approval_level}`"
            >{{ item.approval_level }}</span>
          </div>
          <button
            type="button"
            class="todo-dense-title"
            :title="item.title"
            @click="$emit('open', item.id)"
          >{{ item.title }}</button>
          <div class="todo-dense-sub">
            <span v-if="objectId" class="todo-dense-sku" :title="objectId">{{ objectId }}</span>
            <span v-if="objectId && createdRelative" class="todo-dense-sep">·</span>
            <span v-if="createdRelative" class="todo-dense-when" :title="formatTime(item.created_at)">{{ createdRelative }}</span>
            <template v-if="item.requester">
              <span class="todo-dense-sep">·</span>
              <span class="todo-dense-requester" :title="requesterTitle">由 {{ item.requester.name || item.requester.id }}</span>
            </template>
          </div>
        </div>

        <!-- 关键指标列：主指标 value + 次要 metrics 内联 -->
        <div class="todo-dense-metrics">
          <div
            v-if="primaryMetric"
            class="todo-dense-primary"
            :class="`tone-${primaryMetricTone}`"
            :title="primaryMetricTitle"
            data-testid="todo-primary-metric"
          >
            <span class="todo-dense-primary-value">{{ primaryMetric.value }}</span>
            <span class="todo-dense-primary-label">{{ primaryMetric.label }}</span>
          </div>
          <div v-if="denseInlineMetrics.length" class="todo-dense-chips" data-testid="todo-dense-metric-list">
            <span
              v-for="chip in denseInlineMetrics"
              :key="chip.key"
              class="todo-dense-metric-chip"
              :class="`tone-${chip.tone}`"
              :title="chip.title"
            >
              <span class="todo-dense-metric-label">{{ chip.label }}</span>
              <span v-if="chip.value" class="todo-dense-metric-value">{{ chip.value }}</span>
            </span>
          </div>
        </div>

        <!-- 来源 Skill -->
        <div class="todo-dense-skill" :title="item.skill_id || ''">{{ shortSkillId }}</div>

        <!-- SLA -->
        <div
          v-if="item.sla_at"
          class="todo-dense-sla"
          :class="[`urgency-${urgency}`]"
          :title="`截止 ${formatTime(item.sla_at)}`"
        >{{ deadlineText }}</div>
        <div v-else class="todo-dense-sla todo-dense-sla--empty">—</div>

        <!-- 操作 -->
        <div class="todo-dense-actions">
          <template v-if="item.status === 'pending'">
            <a-button size="mini" type="outline" status="danger" @click.stop="$emit('decide', item.id, 'rejected')">驳回</a-button>
            <a-button size="mini" type="outline" @click.stop="$emit('open', item.id)">详情</a-button>
            <a-button size="mini" type="primary" @click.stop="$emit('decide', item.id, 'approved')">
              {{ item.kind === 'dispatch' ? '通过并派发' : '通过' }}
            </a-button>
          </template>
          <template v-else>
            <a-button size="mini" type="outline" @click.stop="$emit('open', item.id)">详情</a-button>
          </template>
        </div>
      </div>

      <!-- 次要信息（备注 / dispatch 完成）在 dense 模式下也保留，但折叠成第二行 -->
      <div v-if="denseSecondaryVisible" class="todo-dense-aux">
        <div v-if="noteMessage" class="todo-dense-note" :class="{ 'is-reason': isResolved }">
          <span v-if="isResolved" class="todo-note-label">决策理由</span>
          {{ noteMessage }}
        </div>
        <button
          v-if="item.related_report?.id"
          type="button"
          class="todo-dense-report"
          @click.stop="onOpenReport"
        >
          <icon-bar-chart class="todo-report-icon" />
          <span>查看完整报告</span>
        </button>
        <span v-if="dispatchDefaultText" class="todo-dense-dispatch">
          <span class="dispatch-default-label">默认派发</span>
          <span class="dispatch-default-value">{{ dispatchDefaultText }}</span>
        </span>
        <span v-if="dispatchCompletions.length" class="todo-dense-completion-hint">
          已完成 {{ dispatchCompletions.length + hiddenDispatchCompletionCount }} 条
        </span>
      </div>
    </template>

    <!-- 默认（非密集）模式：保留原始堆叠卡片布局 -->
    <template v-else>
    <!-- P0-5 / V5：顶部标签合并单行，超宽 ellipsis。
         次要信息（approval_level / decision_mode）合并进来，避免多行 -->
    <div class="todo-tag-row">
      <a-tag size="small" :color="todoKindColor(item.kind)">{{ todoKindLabel(item.kind) }}</a-tag>
      <a-tag size="small" :color="todoStatusColor(item.status)" class="todo-status-tag">
        {{ todoStatusLabel(item.status) }}
      </a-tag>
      <a-tag
        v-if="item.decision_mode && item.decision_mode !== 'any_of'"
        size="small"
        color="purple"
        class="todo-mode-tag"
        :title="decisionModeTooltip"
      >
        {{ decisionModeLabel(item.decision_mode) }}
      </a-tag>
      <!-- GAP-1：审批级别 chip -->
      <span
        v-if="item.approval_level"
        class="todo-level-chip"
        :class="`level-${item.approval_level.toLowerCase()}`"
        :title="`审批级别 ${item.approval_level}`"
      >
        {{ item.approval_level }}
      </span>
      <span
        v-if="item.sla_at"
        class="todo-sla-chip"
        :class="[`urgency-${urgency}`]"
        :title="`截止 ${formatTime(item.sla_at)}`"
      >
        {{ deadlineText }}
      </span>
      <span
        v-if="postTrainingBadgeText"
        class="todo-model-chip"
        :class="postTrainingBadgeClass"
        :title="postTrainingBadgeTitle"
      >
        后训练模型 {{ postTrainingBadgeText }}
      </span>
    </div>

    <button
      type="button"
      class="todo-title"
      @click="$emit('open', item.id)"
    >{{ item.title }}</button>

    <!-- 经营待办主指标优先展示详情页同口径的支付金额变化率；
         旧 primary_indicator/metrics_preview 只作为缺失时的兜底。 -->
    <div
      v-if="primaryMetric"
      class="todo-primary-metric"
      :class="`tone-${primaryMetricTone}`"
      :title="primaryMetricTitle"
      data-testid="todo-primary-metric"
    >
      <span class="todo-primary-label">{{ primaryMetric.label }}</span>
      <span class="todo-primary-value">
        {{ primaryMetric.value }}
      </span>
    </div>

    <div
      v-if="visibleSecondaryMetrics.length"
      class="todo-metric-list"
      data-testid="todo-metric-list"
      aria-label="关键指标"
    >
      <span
        v-for="metric in visibleSecondaryMetrics"
        :key="metric.key"
        class="todo-metric-chip"
        :class="`tone-${metric.tone}`"
        :title="metric.title"
      >
        <span class="todo-metric-label">{{ metric.label }}</span>
        <span class="todo-metric-value">{{ metric.value }}</span>
      </span>
    </div>

    <div v-if="rankBadges.length || (paymentChangeText && !usesPaymentPrimary)" class="todo-business-strip">
      <span
        v-if="paymentChangeText && !usesPaymentPrimary"
        class="business-badge payment-change"
        :class="paymentChangeTone"
        title="按详情页数据支撑中的支付金额变化率展示"
      >
        支付变化 {{ paymentChangeText }}
      </span>
      <span
        v-for="badge in rankBadges"
        :key="badge.key"
        class="business-badge"
        :title="badge.title"
      >
        {{ badge.label }} {{ badge.value }}
      </span>
    </div>

    <button
      v-if="item.related_report?.id"
      type="button"
      class="todo-report-chip"
      @click.stop="onOpenReport"
    >
      <icon-bar-chart class="todo-report-icon" />
      <span class="todo-report-label">查看完整报告</span>
      <span class="todo-report-arrow">→</span>
    </button>

    <div v-if="noteMessage" class="todo-note" :class="{ 'is-reason': isResolved }">
      <span v-if="isResolved" class="todo-note-label">决策理由</span>
      {{ noteMessage }}
    </div>

    <div v-if="dispatchDefaultText" class="todo-dispatch-default">
      <span class="dispatch-default-label">默认派发</span>
      <span class="dispatch-default-value">{{ dispatchDefaultText }}</span>
    </div>

    <div v-if="dispatchCompletions.length" class="todo-dispatch-completions">
      <div
        v-for="completion in dispatchCompletions"
        :key="completion.task_id"
        class="todo-dispatch-completion"
      >
        <div class="completion-head">
          <span class="completion-name">{{ completionName(completion) }}</span>
          <span v-if="completion.ack_at" class="completion-time">{{ formatTime(completion.ack_at) }}</span>
        </div>
        <div v-if="completion.ack_note" class="completion-note">{{ completion.ack_note }}</div>
        <div v-if="completion.content" class="completion-content">
          {{ sanitizeSummaryText(completion.content, 140) }}
        </div>
      </div>
      <div v-if="hiddenDispatchCompletionCount > 0" class="completion-more">
        还有 {{ hiddenDispatchCompletionCount }} 条完成记录
      </div>
    </div>

    <div class="todo-info-row">
      <span class="todo-info-item todo-info-skill" :title="item.skill_id || ''">
        <span class="todo-info-label">Skill</span>
        <span class="todo-info-value">{{ shortSkillId }}</span>
      </span>
      <!-- GAP-1：发起人 + 部门 -->
      <template v-if="item.requester">
        <span class="todo-info-sep">·</span>
        <span class="todo-info-item" :title="requesterTitle">
          <span class="todo-info-label">由</span>
          <span class="todo-info-value">
            {{ item.requester.name || item.requester.id }}
            <span v-if="item.requester_department" class="todo-info-dept">{{ item.requester_department }}</span>
          </span>
        </span>
      </template>
      <!-- GAP-1：会签进度胶囊 -->
      <template v-if="showProgress">
        <span class="todo-info-sep">·</span>
        <span
          class="todo-progress-chip"
          :title="progressTitle"
        >
          {{ item.aggregate_progress?.done ?? 0 }}/{{ item.aggregate_progress?.total ?? 0 }} 已决
        </span>
      </template>
      <span v-if="createdRelative" class="todo-info-sep">·</span>
      <span v-if="createdRelative" class="todo-info-item" :title="formatTime(item.created_at)">
        <span class="todo-info-value">{{ createdRelative }}</span>
      </span>
    </div>

    <div class="todo-actions">
      <template v-if="item.status === 'pending'">
        <a-button size="small" type="primary" status="success" @click="$emit('decide', item.id, 'approved')">
          {{ item.kind === 'dispatch' ? '通过并派发' : '通过' }}
        </a-button>
        <a-button size="small" type="primary" status="danger" @click="$emit('decide', item.id, 'rejected')">驳回</a-button>
        <a-button size="small" type="primary" class="todo-v2-entry" @click="$emit('open-v2', item.id)">新版 V2</a-button>
        <a-button size="small" type="outline" @click="$emit('open', item.id)">详情</a-button>
      </template>
      <template v-else>
        <a-button size="small" type="primary" class="todo-v2-entry" @click="$emit('open-v2', item.id)">新版 V2</a-button>
        <a-button size="small" type="outline" @click="$emit('open', item.id)">详情</a-button>
      </template>
    </div>
    </template>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import { IconBarChart } from '@arco-design/web-vue/es/icon'
import { formatTime, relativeTime } from '@/utils/format'
import type { InboxTodoListItem } from '@/types/inbox'
import {
  deadlineUrgency,
  decisionModeLabel,
  formatRelativeDeadline,
  reportTrendTone,
  sanitizeSummaryText,
  todoKindColor,
  todoKindLabel,
  todoStatusColor,
  todoStatusLabel,
} from './presentation'

const props = defineProps({
  item: { type: Object as PropType<InboxTodoListItem>, required: true },
  // dense=true 时渲染单行行式布局，对齐 design/inbox.jsx 收件列表密集稿；默认 false 保留原堆叠卡片
  dense: { type: Boolean, default: false },
})

const emit = defineEmits<{
  (event: 'open', id: number): void
  (event: 'open-v2', id: number): void
  (event: 'decide', id: number, decision: 'approved' | 'rejected'): void
  (event: 'open-report', reportId: string): void
}>()

function onOpenReport() {
  const reportId = props.item.related_report?.id
  if (!reportId) return
  emit('open-report', reportId)
}

const isResolved = computed(() => ['approved', 'rejected', 'resolved_by_peer'].includes(String(props.item.status)))

const noteMessage = computed(() => {
  if (props.item.status === 'resolved_by_peer') {
    const who = props.item.decided_by || '—'
    const when = formatTime(props.item.decided_at)
    const reason = props.item.decision_reason ? ` · ${props.item.decision_reason}` : ''
    return `已被同事 ${who} 在 ${when} 处理完成${reason}`
  }
  if (isResolved.value && props.item.decision_reason) {
    return props.item.decision_reason
  }
  const firstAction = (props.item.suggested_actions || []).find((item) => String(item || '').trim())
  const summary = String(props.item.summary || '').trim()
  return firstAction ? String(firstAction) : summary
})

const decisionModeTooltip = computed(() => {
  if (props.item.decision_mode === 'all_of') return '所有接收人都完成后才会结案'
  if (props.item.decision_mode === 'independent') return '你的决策独立记录，不会覆盖其他接收人'
  return ''
})

// 主指标优先用列表/详情同口径的支付金额变化率。
// 旧 primary_indicator 与 metrics_preview 保留为兜底，避免历史待办空白。
type PrimaryIndicatorPayload = {
  label?: string | null
  value?: string | null
  severity?: string | null
  tone?: string | null
}
type PrimaryMetric = {
  label: string
  value: string
  delta?: string | null
  tone: string
}

// item 类型里没有声明 primary_indicator（后端通过 Pydantic extra=allow 透传）。
// 这里用局部 cast 读取，避免触碰全局 types 文件；字段不存在时为 undefined。
const rawPrimaryIndicator = computed<PrimaryIndicatorPayload | null>(() => {
  const raw = (props.item as unknown as { primary_indicator?: PrimaryIndicatorPayload | null })
    .primary_indicator
  return raw && typeof raw === 'object' ? raw : null
})

function normalizeRankValue(value: unknown): string {
  const text = String(value ?? '').trim()
  if (!text) return ''
  if (/^第/.test(text)) return text
  const matched = text.match(/\d+/)?.[0]
  return matched ? `第${matched}名` : text
}

function parsePaymentChangeNumber(value: unknown): number | null {
  if (value == null || value === '') return null
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  const matched = String(value).replace(/,/g, '').match(/[+-]?\d+(?:\.\d+)?/)
  if (!matched) return null
  const n = Number(matched[0])
  return Number.isFinite(n) ? n : null
}

function normalizePaymentChange(value: unknown): string {
  const explicit = String(props.item.payment_change_text || '').trim()
  if (explicit) return explicit
  if (value == null || value === '') return ''
  const num = Number(value)
  if (!Number.isFinite(num)) return String(value)
  const sign = num > 0 ? '+' : ''
  return `${sign}${Math.round(num * 100) / 100}%`
}

const paymentChangeText = computed(() => normalizePaymentChange(props.item.payment_change_pct))

const paymentChangeNumber = computed(() => {
  const fromValue = parsePaymentChangeNumber(props.item.payment_change_pct)
  if (fromValue != null) return fromValue
  return parsePaymentChangeNumber(props.item.payment_change_text)
})

const paymentTrendTone = computed(() => {
  const n = paymentChangeNumber.value
  if (n != null && n < 0) return 'down'
  if (n != null && n > 0) return 'up'
  return 'neutral'
})

const usesPaymentPrimary = computed(() => Boolean(paymentChangeText.value))

const paymentChangeTone = computed(() => {
  if (paymentTrendTone.value === 'down') return 'tone-down'
  if (paymentTrendTone.value === 'up') return 'tone-up'
  return 'tone-flat'
})

const primaryMetric = computed<PrimaryMetric | null>(() => {
  if (paymentChangeText.value) {
    return {
      label: '支付金额变化率',
      value: paymentChangeText.value,
      delta: null,
      tone: paymentTrendTone.value,
    }
  }
  const declared = rawPrimaryIndicator.value
  if (declared && declared.label && declared.value) {
    const tone = declared.tone
      || (declared.severity === 'critical' || declared.severity === 'high'
        ? 'danger'
        : declared.severity === 'medium'
          ? 'warning'
          : declared.severity === 'low'
            ? 'neutral'
            : 'neutral')
    return {
      label: String(declared.label),
      value: String(declared.value),
      delta: null,
      tone,
    }
  }
  // Fallback：metrics_preview[0]（保留旧逻辑，trend 驱动 tone）
  const list = props.item.metrics_preview || []
  if (!list.length) return null
  const first = list[0]
  return {
    label: first.label,
    value: first.value,
    delta: first.delta ?? null,
    tone: reportTrendTone(first.trend),
  }
})

const primaryMetricTone = computed(() => primaryMetric.value?.tone || 'neutral')

const primaryMetricTitle = computed(() => {
  const m = primaryMetric.value
  if (!m) return ''
  const delta = m.delta ? ` (${m.delta})` : ''
  return `${m.label} ${m.value}${delta}`
})

const rankBadges = computed(() => {
  const rows: Array<{ key: string; label: string; value: string; title: string }> = []
  const visitorRank = normalizeRankValue(props.item.ranking_visitors)
  if (visitorRank) {
    rows.push({
      key: 'visitors',
      label: '访客',
      value: visitorRank,
      title: props.item.ranking_visitors_detail || '全店访客排名',
    })
  }
  const orderRank = normalizeRankValue(props.item.ranking_orders)
  if (orderRank) {
    rows.push({
      key: 'orders',
      label: '支付',
      value: orderRank,
      title: props.item.ranking_orders_detail || '全店成交排名（实时支付金额）',
    })
  }
  return rows
})

type VisibleMetric = {
  key: string
  label: string
  value: string
  delta: string
  tone: string
  title: string
}

function metricTitle(metric: { label?: string; value?: string; delta?: string | null }): string {
  const delta = metric.delta ? ` (${metric.delta})` : ''
  return `${metric.label || '指标'} ${metric.value || '-'}${delta}`
}

function normalizeVisibleMetric(metric: { label?: string; value?: string; delta?: string | null; trend?: string | null }, index: number): VisibleMetric {
  const label = String(metric.label || '指标')
  const rawValue = String(metric.value || '').trim()
  const value = rawValue && rawValue !== '空' ? rawValue : '-'
  const delta = metric.delta ? String(metric.delta) : ''
  return {
    key: `${index}-${label}-${value}-${delta}`,
    label,
    value,
    delta,
    tone: reportTrendTone(metric.trend),
    title: metricTitle({ label, value, delta }),
  }
}

const visibleSecondaryMetrics = computed<VisibleMetric[]>(() => {
  const list = props.item.metrics_preview || []
  const start = usesPaymentPrimary.value || rawPrimaryIndicator.value ? 0 : 1
  return list.slice(start).map((metric, index) => normalizeVisibleMetric(metric, start + index))
})

const shortSkillId = computed(() => {
  const id = props.item.skill_id || ''
  if (!id) return '未绑定'
  if (id.length <= 28) return id
  return `${id.slice(0, 18)}…${id.slice(-6)}`
})
const urgency = computed(() => props.item.status === 'pending'
  ? deadlineUrgency(props.item.sla_at)
  : 'normal')
const deadlineText = computed(() => {
  if (props.item.status !== 'pending') return formatTime(props.item.sla_at)
  return formatRelativeDeadline(props.item.sla_at) || formatTime(props.item.sla_at)
})

const createdRelative = computed(() => {
  if (!props.item.created_at) return ''
  const ts = relativeTime(props.item.created_at)
  return ts ? `${ts}发起` : ''
})

const showProgress = computed(() => {
  const p = props.item.aggregate_progress
  return !!(p && (p.total || 0) > 1)
})

const progressTitle = computed(() => {
  const p = props.item.aggregate_progress
  if (!p) return ''
  const waiting = p.waiting_on?.length ? `，待决：${p.waiting_on.join('、')}` : ''
  return `共 ${p.total} 人审批，已决 ${p.done} 人${waiting}`
})

const requesterTitle = computed(() => {
  const r = props.item.requester
  if (!r) return ''
  return `${r.name || r.id}${props.item.requester_department ? ' · ' + props.item.requester_department : ''}`
})

const dispatchCompletions = computed(() => (props.item.dispatch_completions || []).slice(0, 2))
const hiddenDispatchCompletionCount = computed(() => {
  const total = props.item.dispatch_done_count ?? dispatchCompletions.value.length
  return Math.max(total - dispatchCompletions.value.length, 0)
})
const dispatchDefaultText = computed(() => {
  if (props.item.kind !== 'dispatch') return ''
  const summary = props.item.dispatch_default_summary
  const names = (summary?.executor_names || []).filter(Boolean)
  if (!summary || !names.length) return ''
  const primary = names.length === 1 ? names[0] : `${names[0]} 等 ${names.length} 人`
  const missing = Number(summary.missing_count || 0)
  return missing > 0 ? `${primary}，${missing} 条待选择` : primary
})

function completionName(completion: { executor_name?: string | null; executor?: string | null }) {
  return completion.executor_name || completion.executor || '执行人'
}

// ─────────── 密集模式（dense=true）专用派生属性 ───────────

const objectId = computed(() => {
  const id = props.item.object_id
  return id ? String(id) : ''
})

function normalizedPriority(value: unknown) {
  const text = String(value || '').trim().toUpperCase()
  return ['P0', 'P1', 'P2', 'P3'].includes(text) ? text : ''
}

// 优先级 pill：优先使用 Skill 输出的业务优先级；老数据再按标题/审批级别/SLA 兜底。
const prioLabel = computed(() => {
  const businessPriority = normalizedPriority(props.item.priority)
  if (businessPriority) return businessPriority
  const titlePriority = normalizedPriority(String(props.item.title || '').match(/^(P[0-3])(?:[｜|\s]|$)/)?.[1])
  if (titlePriority) return titlePriority
  const lvl = (props.item.approval_level || '').toUpperCase()
  if (lvl === 'L3') return 'P0'
  if (lvl === 'L2') return 'P1'
  if (lvl === 'L1') return 'P2'
  if (lvl === 'L0') return 'P3'
  // 没有 approval_level → 用 urgency 兜底成 P1/P2/P3
  if (urgency.value === 'overdue' || urgency.value === 'urgent') return 'P1'
  if (urgency.value === 'soon') return 'P2'
  return 'P3'
})

const prioPillClass = computed(() => {
  const label = prioLabel.value
  if (label === 'P0' || label === 'P1') return 'bad'
  if (label === 'P2') return 'warn'
  if (label === 'P3') return 'info'
  return ''
})

const prioTitle = computed(() => {
  if (normalizedPriority(props.item.priority)) {
    const amount = props.item.priority_amount
    const amountText = amount !== undefined && amount !== null && amount !== '' ? `，金额 ${amount}` : ''
    return `业务优先级 ${props.item.priority}${amountText}`
  }
  const lvl = props.item.approval_level
  return lvl ? `按审批级别 ${lvl} 推导` : '优先级（按 SLA 推导）'
})

const kindPillClass = computed(() => (props.item.kind === 'dispatch' ? 'warn' : 'accent'))

const statusPillClass = computed(() => {
  const status = props.item.status
  if (status === 'approved') return 'ok'
  if (status === 'rejected') return 'bad'
  if (status === 'expired') return ''
  if (status === 'resolved_by_peer') return 'accent'
  return 'info'
})

const postTrainingBadgeText = computed(() => {
  const status = String(props.item.post_training_model_evaluation?.status || '').toLowerCase()
  if (status === 'used') return '已评估'
  if (status === 'skipped') return '已跳过'
  if (status === 'failed') return '失败'
  return ''
})

const postTrainingBadgeClass = computed(() => {
  const status = String(props.item.post_training_model_evaluation?.status || '').toLowerCase()
  if (status === 'used') return 'model-ok'
  if (status === 'failed') return 'model-bad'
  if (status === 'skipped') return 'model-warn'
  return ''
})

const postTrainingBadgeTitle = computed(() => {
  const ev = props.item.post_training_model_evaluation
  if (!ev) return ''
  return [
    ev.model_family,
    ev.model_deployment_id,
    ev.reason,
    ev.evaluated_at ? formatTime(ev.evaluated_at) : '',
  ].filter(Boolean).join(' · ')
})

// 密集模式也必须完整展示关键指标；rank/payment 是经营上下文，metrics_preview 是完整指标口径。
type DenseInlineMetric = VisibleMetric
const denseInlineMetrics = computed<DenseInlineMetric[]>(() => {
  const chips: DenseInlineMetric[] = visibleSecondaryMetrics.value.map((metric) => ({ ...metric }))
  for (const badge of rankBadges.value) {
    chips.push({
      key: `rank-${badge.key}`,
      label: badge.label,
      value: badge.value,
      delta: '',
      tone: 'neutral',
      title: badge.title,
    })
  }
  if (!usesPaymentPrimary.value && paymentChangeText.value) {
    chips.push({
      key: 'payment-change',
      label: '支付变化',
      value: paymentChangeText.value,
      delta: '',
      tone: paymentTrendTone.value,
      title: '按支付金额变化率展示',
    })
  }
  return chips
})

// dense 模式的辅助行：决策理由 / 报告链接 / dispatch 完成提示
const denseSecondaryVisible = computed(() => {
  return Boolean(
    noteMessage.value
      || props.item.related_report?.id
      || dispatchDefaultText.value
      || dispatchCompletions.value.length,
  )
})
</script>

<style scoped>
@import './inbox-card.css';

.todo-card {
  position: relative;
  padding: 14px 14px 14px 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 168px;
}

/* SLA 紧急度左侧色条：3px 宽，红/橙/黄/灰 4 档 */
.todo-card::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--ai-border-2);
  border-top-left-radius: inherit;
  border-bottom-left-radius: inherit;
}

.todo-card.urgency-overdue::before {
  background: var(--ai-bad);
}

.todo-card.urgency-urgent::before {
  background: var(--ai-bad);
}

.todo-card.urgency-soon::before {
  background: var(--ai-warn);
}

/* P0-5：标签改单行横向滚动（移动设备可滑动），超长 ellipsis；
   原 flex-wrap 会把 3-5 个标签拉成 2-3 行，现在收敛到 1 行 */
.todo-tag-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: nowrap;
  min-height: 24px;
  padding-left: 28px;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: none;
}

.todo-tag-row::-webkit-scrollbar {
  display: none;
}

.todo-status-tag,
.todo-mode-tag {
  flex: 0 0 auto;
  white-space: nowrap;
}

.todo-sla-chip {
  margin-left: auto;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: var(--sf-text-tiny);
  font-weight: 700;
  white-space: nowrap;
}

.todo-sla-chip.urgency-overdue {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}

.todo-sla-chip.urgency-urgent {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}

.todo-sla-chip.urgency-soon {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
}

.todo-model-chip {
  flex: 0 0 auto;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: var(--sf-text-tiny);
  font-weight: 700;
  white-space: nowrap;
  border: 1px solid var(--ai-border);
}
.todo-model-chip.model-ok {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
.todo-model-chip.model-warn {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.todo-model-chip.model-bad {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}

.todo-title {
  align-self: flex-start;
  padding: 0;
  margin: 0;
  border: 0;
  background: transparent;
  font-size: var(--sf-text-h2);
  font-weight: 700;
  line-height: 1.4;
  color: var(--ai-ink-1);
  text-align: left;
  cursor: pointer;
  width: 100%;
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.todo-title:hover {
  color: var(--ai-accent-ink);
}

.todo-title:focus-visible {
  outline: 2px solid var(--ai-accent);
  outline-offset: 2px;
  border-radius: 2px;
}

.todo-report-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  align-self: flex-start;
  padding: 8px 12px;
  border-radius: 999px;
  border: 1px solid var(--ai-border);
  background: var(--ai-info-soft);
  color: var(--ai-info);
  font-size: var(--sf-text-caption);
  font-weight: 700;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.todo-report-chip:hover {
  background: var(--ai-info-soft);
  border-color: var(--ai-border-2);
}

.todo-business-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.business-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 4px 8px;
  border-radius: 6px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: 11px;
  font-weight: 800;
  line-height: 1.2;
}

.business-badge.payment-change {
  background: var(--ai-info-soft);
  color: var(--ai-info);
}

.business-badge.payment-change.tone-down {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}

.business-badge.payment-change.tone-up {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
}

.todo-report-icon {
  font-size: 14px;
  flex-shrink: 0;
}

.todo-report-arrow {
  opacity: 0.7;
  font-weight: 800;
}

.todo-note {
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.55;
}

.todo-note.is-reason {
  border-left: 3px solid var(--ai-accent);
  background: var(--ai-accent-soft);
}

.todo-note-label {
  display: inline-block;
  padding: 1px 6px;
  margin-right: 6px;
  border-radius: 4px;
  background: var(--ai-info-soft);
  color: var(--ai-info);
  font-size: var(--sf-text-tiny);
  font-weight: 700;
}

.todo-dispatch-default {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  padding: 7px 9px;
  border-radius: 6px;
  background: var(--ai-info-soft);
  color: var(--ai-ink-2);
  font-size: 12px;
  line-height: 1.4;
}

.dispatch-default-label {
  flex: 0 0 auto;
  color: var(--ai-info);
  font-weight: 800;
}

.dispatch-default-value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 700;
}

.todo-info-row {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ai-ink-4);
  font-size: 11.5px;
  min-width: 0;
  flex-wrap: wrap;
  font-variant-numeric: tabular-nums;
}

.todo-info-item {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  min-width: 0;
}

.todo-info-skill {
  flex: 1 1 auto;
  min-width: 0;
  font-family: var(--ai-font-mono);
}

.todo-info-sep {
  color: var(--ai-ink-5);
  opacity: 1;
  flex: 0 0 auto;
}

.todo-info-label {
  color: var(--ai-ink-4);
  opacity: 1;
  font-weight: 500;
  letter-spacing: 0;
  font-size: 10.5px;
  text-transform: uppercase;
  flex: 0 0 auto;
}

.todo-info-value {
  color: var(--ai-ink-2);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.todo-info-skill .todo-info-value {
  max-width: 100%;
}

.todo-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: auto;
}

.todo-v2-entry {
  font-weight: 800;
  box-shadow: 0 4px 12px rgba(22, 93, 255, 0.18);
}

.status-resolved_by_peer {
  opacity: 0.82;
}

.status-resolved_by_peer .todo-note {
  background: var(--ai-surface-3);
  color: var(--ai-ink-2);
}

.todo-dispatch-completions {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.todo-dispatch-completion {
  padding: 9px 10px;
  border: 1px solid var(--ai-border);
  border-left: 3px solid var(--ai-ok);
  border-radius: 6px;
  background: var(--ai-ok-soft);
  color: var(--ai-ink-2);
  font-size: 12px;
  line-height: 1.5;
}

.completion-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-bottom: 4px;
}

.completion-name {
  color: var(--ai-ok);
  font-weight: 800;
}

.completion-time {
  color: var(--ai-ink-3);
}

.completion-note {
  color: var(--ai-ink-1);
  font-weight: 700;
  white-space: pre-wrap;
  word-break: break-word;
}

.completion-content {
  margin-top: 3px;
  color: var(--ai-ink-3);
  word-break: break-word;
}

.completion-more {
  color: var(--ai-ink-3);
  font-size: 12px;
  font-weight: 700;
}

/* GAP-1：审批级别 / metric 行 / 会签进度 — 改设计稿 .ai-pill 平直风格 */
.todo-level-chip {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  white-space: nowrap;
}

.todo-level-chip.level-l0 {
  background: var(--ai-ok-soft);
  border-color: transparent;
  color: var(--ai-ok);
}
.todo-level-chip.level-l1 {
  background: var(--ai-info-soft);
  border-color: transparent;
  color: var(--ai-info);
}
.todo-level-chip.level-l2 {
  background: var(--ai-warn-soft);
  border-color: transparent;
  color: var(--ai-warn);
}
.todo-level-chip.level-l3 {
  background: var(--ai-bad-soft);
  border-color: transparent;
  color: var(--ai-bad);
}

.todo-metric-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

/* P0-5：主指标独立大号展示 — 改 mono 等宽 + ink 中性色调 */
.todo-primary-metric {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 4px 0 2px;
}

.todo-primary-label {
  color: var(--ai-ink-4);
  font-size: 10.5px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: uppercase;
  white-space: nowrap;
}

.todo-primary-value {
  color: var(--ai-ink-1);
  font-size: 18px;
  font-weight: 600;
  font-family: var(--ai-font-mono);
  line-height: 1.15;
  letter-spacing: -0.01em;
  white-space: normal;
  overflow: visible;
  text-overflow: clip;
  max-width: none;
  overflow-wrap: anywhere;
  font-variant-numeric: tabular-nums;
}

.todo-primary-delta {
  margin-left: 4px;
  padding-left: 8px;
  border-left: 1px solid var(--ai-border);
  font-size: 13px;
  font-weight: 500;
  font-family: var(--ai-font-mono);
}

.todo-primary-metric.tone-up .todo-primary-value,
.todo-primary-metric.tone-up .todo-primary-delta,
.todo-primary-metric.tone-success .todo-primary-value,
.todo-primary-metric.tone-success .todo-primary-delta {
  color: var(--ai-ok);
}

.todo-primary-metric.tone-down .todo-primary-value,
.todo-primary-metric.tone-down .todo-primary-delta,
.todo-primary-metric.tone-danger .todo-primary-value,
.todo-primary-metric.tone-danger .todo-primary-delta {
  color: var(--ai-bad);
}

.todo-primary-metric.tone-warning .todo-primary-value,
.todo-primary-metric.tone-warning .todo-primary-delta {
  color: var(--ai-warn);
}

.todo-primary-metric.tone-info .todo-primary-value,
.todo-primary-metric.tone-info .todo-primary-delta {
  color: var(--ai-info);
}

.todo-metric-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.todo-metric-chip {
  display: inline-flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px;
  min-width: 0;
  max-width: 100%;
  padding: 3px 7px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 11px;
  font-weight: 500;
  border: 1px solid var(--ai-border);
  white-space: normal;
  overflow: visible;
}

.todo-metric-label,
.todo-dense-metric-label {
  flex: 0 1 auto;
  min-width: 0;
  color: var(--ai-ink-4);
  white-space: normal;
  overflow-wrap: anywhere;
}

.todo-metric-value,
.todo-dense-metric-value {
  flex: 0 1 auto;
  min-width: 0;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: visible;
  text-overflow: clip;
}

.todo-metric-delta,
.todo-dense-metric-delta {
  flex: 0 1 auto;
  min-width: 0;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: visible;
  text-overflow: clip;
}

.todo-metric-chip.tone-up .todo-metric-value,
.todo-metric-chip.tone-success .todo-metric-value,
.todo-dense-metric-chip.tone-up .todo-dense-metric-value,
.todo-dense-metric-chip.tone-success .todo-dense-metric-value {
  color: var(--ai-ok);
}

.todo-metric-chip.tone-down .todo-metric-value,
.todo-metric-chip.tone-danger .todo-metric-value,
.todo-dense-metric-chip.tone-down .todo-dense-metric-value,
.todo-dense-metric-chip.tone-danger .todo-dense-metric-value {
  color: var(--ai-bad);
}

.todo-metric-chip.tone-warning .todo-metric-value,
.todo-dense-metric-chip.tone-warning .todo-dense-metric-value {
  color: var(--ai-warn);
}

.todo-progress-chip {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
}

.todo-info-dept {
  margin-left: 4px;
  color: var(--ai-ink-3);
  opacity: 0.72;
}

/* ─────────── 密集模式（dense=true） — 对齐 design/inbox.jsx 行式布局 ───────────
 * 思路：横向 flex，关键指标列保留完整展示空间并允许换行撑高。
 * 主区域 flex:1 ellipsis；hover 上 surface-2，与设计稿 row hover 一致。
 * 注意：左侧 status accent 色条由 .sf-inbox-card::before 控制（inbox-card.css），这里不动。
 */
.todo-card--dense {
  display: block;
  padding: 10px 14px 10px 18px;
  min-height: 0;
  gap: 0;
}

.todo-card--dense:hover {
  background: var(--ai-surface-2);
}

.todo-dense-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
}

.todo-dense-prio {
  flex: 0 0 36px;
  justify-content: center;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.todo-dense-main {
  flex: 0 1 clamp(180px, 24vw, 360px);
  min-width: 0;
  max-width: clamp(180px, 24vw, 360px);
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.todo-dense-chip-row {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
}

.todo-dense-chip {
  font-size: 10.5px;
  height: 18px;
  padding: 0 6px;
}

.todo-dense-title {
  appearance: none;
  background: transparent;
  border: 0;
  padding: 0;
  margin: 0;
  text-align: left;
  cursor: pointer;
  font-size: 12.5px;
  font-weight: 500;
  line-height: 1.35;
  color: var(--ai-ink-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.todo-dense-title:hover {
  color: var(--ai-accent);
}

.todo-dense-title:focus-visible {
  outline: 2px solid var(--ai-accent);
  outline-offset: 2px;
  border-radius: 2px;
}

.todo-dense-sub {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 10.5px;
  color: var(--ai-ink-4);
  min-width: 0;
  flex-wrap: wrap;
}

.todo-dense-sku {
  font-family: var(--ai-font-mono);
  font-size: 11px;
  color: var(--ai-ink-3);
  white-space: nowrap;
}

.todo-dense-sep {
  color: var(--ai-ink-5);
}

.todo-dense-when {
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-4);
  white-space: nowrap;
}

.todo-dense-requester {
  color: var(--ai-ink-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 110px;
}

.todo-dense-metrics {
  flex: 1 0 360px;
  min-width: 340px;
  max-width: 560px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow: visible;
}

.todo-dense-primary {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
  flex-wrap: wrap;
}

.todo-dense-primary-value {
  font-family: var(--ai-font-mono);
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
  font-variant-numeric: tabular-nums;
  white-space: normal;
  overflow: visible;
  text-overflow: clip;
  overflow-wrap: anywhere;
}

.todo-dense-primary-label {
  font-size: 10.5px;
  color: var(--ai-ink-4);
  white-space: nowrap;
}

.todo-dense-primary.tone-down .todo-dense-primary-value,
.todo-dense-primary.tone-danger .todo-dense-primary-value {
  color: var(--ai-bad);
}
.todo-dense-primary.tone-up .todo-dense-primary-value,
.todo-dense-primary.tone-success .todo-dense-primary-value {
  color: var(--ai-ok);
}
.todo-dense-primary.tone-warning .todo-dense-primary-value {
  color: var(--ai-warn);
}
.todo-dense-primary.tone-info .todo-dense-primary-value {
  color: var(--ai-info);
}

.todo-dense-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  min-width: 0;
}

.todo-dense-metric-chip {
  display: inline-flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 3px;
  min-width: 0;
  max-width: 100%;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  font-size: 10.5px;
  color: var(--ai-ink-3);
  font-weight: 500;
  white-space: normal;
  line-height: 1.35;
  overflow: visible;
}

.todo-dense-skill {
  flex: 0 0 130px;
  min-width: 0;
  font-family: var(--ai-font-mono);
  font-size: 11px;
  color: var(--ai-ink-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.todo-dense-sla {
  flex: 0 0 80px;
  font-size: 11px;
  color: var(--ai-ink-3);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.todo-dense-sla.urgency-overdue,
.todo-dense-sla.urgency-urgent {
  color: var(--ai-bad);
  font-weight: 600;
}

.todo-dense-sla.urgency-soon {
  color: var(--ai-warn);
  font-weight: 600;
}

.todo-dense-sla--empty {
  color: var(--ai-ink-5);
}

.todo-dense-actions {
  flex: 0 0 200px;
  width: 200px;
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  justify-content: flex-end;
}

.todo-dense-actions :deep(.arco-btn-mini) {
  height: 26px;
  padding: 0 10px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  box-shadow: none;
  font-family: var(--ai-font-sans);
  font-size: 12px;
  font-weight: 500;
}

.todo-dense-actions :deep(.arco-btn-mini:hover) {
  background: var(--ai-surface-2);
}

.todo-dense-actions :deep(.arco-btn-primary) {
  background: var(--ai-ink-1) !important;
  border-color: var(--ai-ink-1) !important;
  color: var(--ai-surface) !important;
}

.todo-dense-actions :deep(.arco-btn-status-danger:not(.arco-btn-primary)) {
  color: var(--ai-bad);
}

.todo-dense-aux {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 8px;
  padding-left: 48px; /* 与 prio pill 列宽 + gap 对齐 */
  font-size: 11.5px;
  color: var(--ai-ink-3);
}

.todo-dense-note {
  flex: 1 1 auto;
  min-width: 0;
  padding: 6px 8px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: 11.5px;
  line-height: 1.45;
}

.todo-dense-note.is-reason {
  border-left: 2px solid var(--ai-accent);
  background: var(--ai-accent-soft);
}

.todo-dense-report {
  appearance: none;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: 4px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-accent-ink);
  font-size: 11.5px;
  font-weight: 500;
  cursor: pointer;
}

.todo-dense-report:hover {
  background: var(--ai-accent-soft);
  border-color: transparent;
}

.todo-dense-dispatch {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  font-size: 11.5px;
  color: var(--ai-ink-3);
}

.todo-dense-completion-hint {
  color: var(--ai-ok);
  font-weight: 500;
}

/* 密集模式下隐藏 dense 模式不需要的容器（防御：理论上 v-if/v-else 已分流） */
.todo-card--dense .todo-tag-row,
.todo-card--dense .todo-title,
.todo-card--dense .todo-primary-metric,
.todo-card--dense .todo-business-strip,
.todo-card--dense .todo-info-row,
.todo-card--dense .todo-actions {
  display: none;
}
</style>
