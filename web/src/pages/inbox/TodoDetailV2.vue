<template>
  <div class="todo-v2-root">
    <a-spin :loading="loading" style="width: 100%">
      <div class="v2-toolbar">
        <a-button size="small" @click="router.push(`/inbox/todos/${todoId}`)">
          <template #icon><icon-left /></template>
          返回旧版
        </a-button>
        <div class="toolbar-tags">
          <a-tag color="arcoblue">新版结构</a-tag>
          <a-tag :color="todoKindTagColor(detail.request?.kind)">{{ todoKindLabel(detail.request?.kind) }}</a-tag>
          <a-tag :color="todoStatusTagColor(detail.todo?.status)">{{ todoStatusLabel(detail.todo?.status) }}</a-tag>
        </div>
      </div>

      <section class="management-hero">
        <div class="hero-copy">
          <div class="eyebrow">管理结论</div>
          <h1>{{ itemTitle || detail.request?.title || '待办详情' }}</h1>
          <div class="hero-meta">
            <span v-if="itemId">商品 {{ itemId }}</span>
            <span v-if="dataTime">采集 {{ dataTime }}</span>
            <span v-if="detail.skill_meta?.name">Skill {{ detail.skill_meta.name }}</span>
          </div>
          <p class="decision-copy">
            {{ recommendedDecision || detail.request?.summary || structuredSummaryText || '暂无明确结论，请查看下方诊断与证据。' }}
          </p>
          <div class="decision-pills">
            <span class="status-pill" :class="prioritySlug(decisionPriority)">{{ decisionPriority || '优先级未标记' }}</span>
            <span class="status-pill" :class="confidenceSlug(decisionConfidence)">{{ decisionConfidence || '置信度待补' }}</span>
            <span class="status-pill" :class="businessActionAllowed ? 'pill-ok' : 'pill-warn'">
              {{ businessActionAllowed ? '可直接派发' : '建议先复核' }}
            </span>
          </div>
        </div>
        <div class="headline-grid">
          <article v-for="metric in headlineMetrics" :key="metric.key" class="headline-card" :class="metric.tone">
            <span>{{ metric.label }}</span>
            <strong>{{ metric.value }}</strong>
            <em>{{ metric.detail || '-' }}</em>
          </article>
        </div>
      </section>

      <div class="content-layout">
        <main class="main-column">
          <section class="panel">
            <header class="panel-head">
              <div>
                <span class="section-kicker">Diagnose</span>
                <h2>问题在哪</h2>
              </div>
              <p>按管理视角归并重复指标，先看整体成交，再看免费流量、付费推广和市场商品侧。</p>
            </header>

            <div class="diagnosis-grid">
              <article v-for="group in diagnosisGroups" :key="group.key" class="diagnosis-card">
                <div class="diagnosis-title">
                  <div>
                    <span>{{ group.label }}</span>
                    <strong>{{ group.summary }}</strong>
                  </div>
                  <a-tag :color="group.tone === 'danger' ? 'red' : group.tone === 'ok' ? 'green' : 'orange'">
                    {{ group.status }}
                  </a-tag>
                </div>
                <div class="signal-list">
                  <div v-for="row in group.rows" :key="row.key" class="signal-row">
                    <div>
                      <strong>{{ row.label }}</strong>
                      <span v-if="row.meta">{{ row.meta }}</span>
                    </div>
                    <div class="signal-values">
                      <b>{{ row.value }}</b>
                      <em v-if="row.detail">{{ row.detail }}</em>
                    </div>
                  </div>
                </div>
              </article>
            </div>
          </section>

          <section class="panel">
            <header class="panel-head">
              <div>
                <span class="section-kicker">Dispatch</span>
                <h2>待派发任务</h2>
              </div>
              <p>把运营动作和派发任务合并，管理者可以直接看负责人、截止时间和验证点。</p>
            </header>

            <div v-if="taskCards.length" class="task-stack">
              <article v-for="task in taskCards" :key="task.key" class="task-card">
                <div class="task-topline">
                  <div>
                    <span>{{ task.group }}</span>
                    <strong>{{ task.title }}</strong>
                  </div>
                  <a-tag :color="task.statusColor">{{ task.statusLabel }}</a-tag>
                </div>
                <p class="task-content">{{ task.content }}</p>
                <div v-if="task.segments.length" class="task-segments">
                  <span v-for="segment in task.segments" :key="segment.label">
                    <b>{{ segment.label }}</b>{{ segment.text }}
                  </span>
                </div>
                <div class="task-footer">
                  <span>负责人：{{ task.executor }}</span>
                  <span v-if="task.deadline">截止：{{ task.deadline }}</span>
                  <span v-if="task.evidence">依据：{{ task.evidence }}</span>
                </div>
                <div v-if="task.record && canEditDispatchExecutor(task.record)" class="assign-row">
                  <a-select
                    v-model="assignSelections[task.record.id]"
                    size="small"
                    allow-search
                    placeholder="选择执行人"
                  >
                    <a-option v-for="user in assignableUsers" :key="user.id" :value="user.id">
                      {{ assignableUserLabel(user) }}
                    </a-option>
                  </a-select>
                  <a-button
                    size="small"
                    type="primary"
                    :loading="assigningTaskId === task.record.id"
                    @click="saveDispatchExecutor(task.record)"
                  >
                    {{ detail.request?.aggregate_decision === 'approved' ? '指派' : '保存执行人' }}
                  </a-button>
                </div>
              </article>
            </div>
            <a-empty v-else description="暂无可派发动作" />
          </section>

          <section v-if="chartSections.length" class="panel">
            <header class="panel-head">
              <div>
                <span class="section-kicker">Charts</span>
                <h2>数据图表</h2>
              </div>
              <p>把高消耗对标、分型占比和点击生命周期转成图表，先看差距再决定改素材还是查投放承接。</p>
            </header>

            <div class="chart-grid">
              <article v-for="section in chartSections" :key="section.key" class="chart-panel">
                <div class="chart-head">
                  <strong>{{ section.title }}</strong>
                  <span v-if="section.description">{{ section.description }}</span>
                </div>
                <div v-if="section.type === 'bar_compare'" class="compare-chart">
                  <div v-for="item in section.items" :key="item.key" class="compare-row">
                    <div class="chart-label">{{ item.label }}</div>
                    <div class="compare-bars">
                      <div class="bar-line">
                        <span>当前</span>
                        <div class="bar-track">
                          <i class="bar-current" :style="{ width: `${item.lowWidth}%` }"></i>
                        </div>
                        <b>{{ item.lowText }}</b>
                      </div>
                      <div class="bar-line">
                        <span>对标</span>
                        <div class="bar-track">
                          <i class="bar-benchmark" :style="{ width: `${item.benchmarkWidth}%` }"></i>
                        </div>
                        <b>{{ item.benchmarkText }}</b>
                      </div>
                    </div>
                  </div>
                </div>
                <div v-else class="share-chart">
                  <div v-for="item in section.items" :key="item.key" class="share-row">
                    <div class="chart-label">{{ item.label }}</div>
                    <div class="share-track">
                      <i :style="{ width: `${item.width}%` }"></i>
                    </div>
                    <strong>{{ item.valueText }}</strong>
                    <span v-if="item.detail">{{ item.detail }}</span>
                  </div>
                </div>
              </article>
            </div>
          </section>

          <section class="panel">
            <header class="panel-head">
              <div>
                <span class="section-kicker">Evidence</span>
                <h2>证据详情</h2>
              </div>
              <p>保留原始分析依据，但按来源拆组，减少长句堆叠。</p>
            </header>

            <div class="evidence-grid">
              <article v-for="section in evidenceSections" :key="section.key" class="evidence-card">
                <div class="evidence-card-head">
                  <strong>{{ section.title }}</strong>
                  <span>{{ section.rows.length }} 条</span>
                </div>
                <div class="evidence-row" v-for="row in section.rows" :key="row.key">
                  <span>{{ row.label }}</span>
                  <strong>{{ row.value }}</strong>
                  <em v-if="row.detail">{{ row.detail }}</em>
                </div>
              </article>
            </div>
          </section>

          <section class="panel debug-panel">
            <a-button size="small" @click="showDebugPayload = !showDebugPayload">
              {{ showDebugPayload ? '收起原始决策 JSON' : '展开原始决策 JSON' }}
            </a-button>
            <pre v-if="showDebugPayload">{{ prettyPayload }}</pre>
          </section>
        </main>

        <aside class="side-column">
          <section class="side-card action-card">
            <div class="side-card-head">
              <span>审批动作</span>
              <strong>{{ todoStatusLabel(detail.todo?.status) }}</strong>
            </div>
            <p>{{ decisionModeLabel(detail.request?.decision_mode) }}</p>
            <div v-if="detail.todo?.status === 'pending'" class="action-buttons">
              <a-button long type="primary" status="success" @click="decide('approved')">通过并派发</a-button>
              <a-button long type="primary" status="danger" @click="decide('rejected')">驳回</a-button>
              <a-button long @click="extendSla">延期 24 小时</a-button>
            </div>
            <div v-else class="processed-state">
              <span>处理人：{{ detail.todo?.decided_by || '-' }}</span>
              <span>处理时间：{{ formatTime(detail.todo?.decided_at) }}</span>
            </div>
          </section>

          <section class="side-card">
            <div class="side-card-head">
              <span>数据质量</span>
              <strong>{{ dataQualityText || '未标记' }}</strong>
            </div>
            <div v-if="dataQualityRows.length" class="quality-list">
              <div v-for="row in dataQualityRows.slice(0, 6)" :key="row.key">
                <strong>{{ row.dimension }}</strong>
                <span>{{ row.issue }}</span>
              </div>
            </div>
            <p v-else>暂无显式数据缺口。</p>
          </section>

          <section v-if="relatedReportId" class="side-card">
            <div class="side-card-head">
              <span>关联报告</span>
              <strong>{{ relatedReportId }}</strong>
            </div>
            <a-button long @click="router.push(`/inbox/reports/${encodeURIComponent(relatedReportId)}`)">打开报告</a-button>
          </section>
        </aside>
      </div>
    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { computed, h, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { Message, Modal, Input } from '@arco-design/web-vue'
import { IconLeft } from '@arco-design/web-vue/es/icon'
import { useRoute, useRouter } from 'vue-router'
import { formatTime } from '@/utils/format'
import type { InboxDispatchTask, InboxTodoDetailResponse } from '@/types/inbox'
import { todoApi } from '@/api'
import {
  decisionModeLabel,
  dispatchStatusLabel,
  prettyJson,
  todoKindLabel,
  todoStatusLabel,
} from './presentation'

type ViewRow = {
  key: string
  label: string
  value: string
  detail?: string
  status?: string
  meta?: string
  tone?: string
}

const route: any = useRoute()
const router: any = useRouter()

const loading = ref(false)
const detail = ref<InboxTodoDetailResponse>({
  todo: null,
  request: null,
  skill_meta: null,
  payload: {},
  structured: null,
  dispatch_tasks: [],
  related_report: null,
})
const assignableUsers = ref<Array<{ id: string; name?: string; role?: string; department?: string; dingtalk_bound?: boolean }>>([])
const assignableUsersError = ref('')
const assigningTaskId = ref<number | null>(null)
const assignSelections = ref<Record<number, string>>({})
let deferredLoadTimer: number | null = null

const todoId = computed(() => Number(route.params.id))
const showDebugPayload = ref(false)
const payload = computed(() => asRecord(detail.value.payload) || {})
const outputPayload = computed(() => asRecord(payload.value.output) || {})
const inputPayload = computed(() => asRecord(payload.value.input) || {})
const prettyPayload = computed(() => showDebugPayload.value ? prettyJson(detail.value.payload || {}) : '')
const structuredSummaryText = computed(() => summaryText(detail.value.structured?.output_summary))

const decisionPriority = computed(() => textValue(payload.value.priority))
const decisionConfidence = computed(() => textValue(payload.value.confidence))
const businessActionAllowed = computed(() => payload.value.business_action_allowed === true)
const recommendedDecision = computed(() => textValue(payload.value.recommended_decision) || textValue(outputPayload.value.recommendation))
const itemId = computed(() => textValue(payload.value.item_id) || textValue(inputPayload.value.item_id))
const itemTitle = computed(() => textValue(payload.value.item_title) || textValue(inputPayload.value.item_title))
const dataTime = computed(() => textValue(payload.value.data_time) || textValue(inputPayload.value.data_time))
const dataQualityText = computed(() => textValue(payload.value.data_quality) || textValue(outputPayload.value.data_quality))
const relatedReportId = computed(() => textValue(detail.value.related_report?.id) || textValue((detail.value.related_report as any)?.report_id))

const paidDetailMetricNames = new Set(['付费商品汇总实时', '付费计划明细'])
const paidDetailBasisLabels = new Set(['关键词推广-低直接ROI计划', '关键词推广-低ROI计划', '关键词推广-低直接ROI/高CPC词', '关键词推广-低ROI/高PPC词', '关键词推广-低效创意'])
const freeSearchFactMetricNames = new Set([
  '免费搜索-访客数',
  '免费搜索-支付买家数',
  '免费搜索-支付转化率',
  '免费搜索-支付金额',
])
const promotedCompareMetricNames = new Set([
  '免费搜索/免费访客环比',
  '搜索/免费转化环比',
  '付费访客环比',
  '付费转化环比',
])
const paidRealtimeSummaryMetricNames = new Set([
  '付费商品汇总实时',
  '关键词推广-商品汇总实时花费/直接ROI/CPC',
])

const standardCompareTable = computed(() => {
  const rawSections = arrayValue(payload.value.metric_sections).length
    ? arrayValue(payload.value.metric_sections)
    : arrayValue(outputPayload.value.metric_sections)
  if (!rawSections.length) return null
  const sec0 = asRecord(rawSections[0]) || {}
  const title = textValue(sec0.title) || ''
  if (!title.startsWith('标准口径')) return null
  const metrics = arrayValue(sec0.metrics)
  if (metrics.length < 3) return null
  const cur = asRecord(metrics[0]) || {}
  const prev = asRecord(metrics[1]) || {}
  const delta = asRecord(metrics[2]) || {}
  if (textValue(cur.name) !== '—当前24h—' || textValue(prev.name) !== '—对比24h—' || textValue(delta.name) !== '—变化率—') return null
  const curParts = (textValue(cur.note) || '').split('｜').map(s => s.trim())
  const prevParts = (textValue(prev.note) || '').split('｜').map(s => s.trim())
  const deltaParts = (textValue(delta.note) || '').split('｜').map(s => s.trim())
  const labelMap: Record<string, string> = { 支付: '支付金额', 件数: '支付件数', 转化: '支付转化率', 访客: '商品访客数', 加购: '商品加购件数' }
  const coreRows: ViewRow[] = []
  for (let i = 0; i < curParts.length; i += 1) {
    const lm = curParts[i].match(/^([^\d¥\-+%.]+)/)
    const rawLabel = lm ? lm[1] : `指标${i + 1}`
    const label = labelMap[rawLabel] || rawLabel
    const curVal = curParts[i].replace(rawLabel, '').trim()
    const prevVal = prevParts[i] ? prevParts[i].replace(rawLabel, '').trim() : '未获取'
    const deltaVal = deltaParts[i] ? deltaParts[i].replace(rawLabel, '').trim() : ''
    const status = deltaVal.startsWith('-') ? '下降' : deltaVal.startsWith('+') ? '上升' : prevVal === '未获取' ? '未获取' : '正常'
    coreRows.push({
      key: `std-${i}`,
      label,
      value: curVal || '-',
      detail: `${prevVal || '未获取'} / ${deltaVal || '-'}`,
      status,
      tone: status === '下降' ? 'danger' : status === '上升' ? 'ok' : 'neutral',
      meta: '当前24h / 前24h / 变化率',
    })
  }
  const extraRows: ViewRow[] = []
  const promotedRows: ViewRow[] = []
  const freeSearchFactRows: ViewRow[] = []
  for (let i = 3; i < metrics.length; i += 1) {
    const m = asRecord(metrics[i]) || {}
    const name = textValue(m.name)
    if (!name || name.startsWith('—')) continue
    const row = {
      key: `std-extra-${i}`,
      label: name,
      value: textValue(m.value) || '-',
      detail: textValue(m.delta) || textValue(m.note) || textValue(m.status) || '',
      status: textValue(m.status) || '-',
      meta: textValue(m.source),
      tone: metricTone(textValue(m.status), textValue(m.value)),
    }
    if (freeSearchFactMetricNames.has(name)) freeSearchFactRows.push(row)
    else if (promotedCompareMetricNames.has(name)) promotedRows.push(row)
    else extraRows.push(buildPaidRealtimeSummaryRow(row))
  }
  return { title, coreRows, promotedRows, extraRows, freeSearchFactRows }
})

const freeSearchRealtimeFacts = computed(() => {
  const rows = standardCompareTable.value?.freeSearchFactRows || []
  if (!rows.length) return []
  const byLabel = new Map(rows.map(row => [row.label, row]))
  return [
    { key: 'free-fact-visitor', label: '访客', source: byLabel.get('免费搜索-访客数') },
    { key: 'free-fact-buyer', label: '买家', source: byLabel.get('免费搜索-支付买家数') },
    { key: 'free-fact-conversion', label: '转化率', source: byLabel.get('免费搜索-支付转化率') },
    { key: 'free-fact-amount', label: '支付金额', source: byLabel.get('免费搜索-支付金额') },
  ].map(item => ({
    key: item.key,
    label: item.label,
    value: textValue(item.source?.value) || '-',
    detail: textValue(item.source?.detail),
    status: textValue(item.source?.status),
    meta: '生意参谋搜索节点实时事实',
    tone: metricTone(textValue(item.source?.status), textValue(item.source?.value)),
  })).filter(row => row.value && row.value !== '-')
})

const freeSearchCompareRows = computed(() => normalizeCompareRows(payload.value.free_search_compare, 'free-search-compare'))
const paidRealtimeCompareRows = computed(() => {
  const explicitRows = normalizeCompareRows(payload.value.paid_realtime_compare, 'paid-realtime-compare')
  return explicitRows.length ? explicitRows : paidSummaryCompareRows()
})
const paidRealtimeComparePeriod = computed(() => {
  const explicitPeriod = comparePeriodText(payload.value.paid_realtime_compare)
  if (explicitPeriod) return explicitPeriod
  const period = paidSummaryPeriod()
  const current = textValue(period?.current_label)
  const previous = textValue(period?.compare_label)
  return current && previous ? `${current} / ${previous}` : current || previous
})

const paidFlowRows = computed(() =>
  normalizeBasisRows(arrayValue(payload.value.paid_flow_analysis_basis), 'paid-flow')
    .filter(row => !paidDetailBasisLabels.has(row.label)),
)
const paidDetailRows = computed(() =>
  normalizeBasisRows(arrayValue(payload.value.paid_flow_analysis_basis), 'paid-detail')
    .filter(row => paidDetailBasisLabels.has(row.label) && (row.value !== '0' || row.detail || row.status !== '-')),
)
const freeFlowRows = computed(() => normalizeBasisRows(arrayValue(payload.value.free_flow_analysis_basis), 'free-flow'))
const analysisBasisRows = computed(() =>
  arrayValue(payload.value.analysis_basis).slice(0, 8).map((raw, index) => {
    const row = asRecord(raw) || {}
    return {
      key: `analysis-${index}-${textValue(row.dimension)}`,
      label: textValue(row.dimension) || '分析维度',
      value: textValue(row.basis) || '-',
      detail: textValue(row.data_gap) || textValue(row.confidence),
      status: textValue(row.confidence),
      tone: metricTone(textValue(row.confidence), textValue(row.data_gap)),
    }
  }),
)
const operationActionRows = computed(() => {
  const rows = normalizeActionRows(payload.value.operation_actions, 'operation')
  return rows.length ? rows : normalizeActionRows(payload.value.top_actions, 'top-action')
})
const operatorActionRows = computed(() => normalizeOperatorActionRows(payload.value.operation_actions))
const chartSections = computed(() => normalizeChartSections(payload.value.chart_sections, operationActionRows.value))
const suggestedActions = computed(() => {
  for (const source of [payload.value.operation_actions, payload.value.suggested, outputPayload.value.suggestions]) {
    const items = stringList(source)
    if (items.length) return items
  }
  return []
})
const dataQualityRows = computed(() => {
  const explicit = arrayValue(payload.value.data_quality_items)
    .map((raw, i) => {
      const row = asRecord(raw) || {}
      return {
        key: `quality-${i}-${textValue(row.dimension)}`,
        dimension: textValue(row.dimension) || '数据缺口',
        issue: textValue(row.issue) || textValue(row.detail) || textValue(row.data_gap) || '-',
      }
    })
    .filter(row => row.issue && row.issue !== '-')
  if (explicit.length) return explicit
  return analysisBasisRows.value
    .filter(row => row.detail)
    .map((row, i) => ({ key: `quality-fallback-${i}-${row.label}`, dimension: row.label, issue: row.detail || '-' }))
})

const headlineMetrics = computed(() => {
  const rows: ViewRow[] = []
  const core = standardCompareTable.value?.coreRows || []
  for (const label of ['支付金额', '支付件数', '支付转化率', '商品访客数']) {
    const row = core.find(item => item.label === label)
    if (row) rows.push(row)
  }
  const paidRoi = paidRealtimeCompareRows.value.find(row => row.label.toUpperCase() === 'ROI')
  if (paidRoi) rows.push({ ...paidRoi, label: '付费 ROI', detail: `${paidRoi.detail || ''}${paidRealtimeComparePeriod.value ? ` · ${paidRealtimeComparePeriod.value}` : ''}` })
  const paidCharge = paidRealtimeCompareRows.value.find(row => /花费/.test(row.label))
  if (paidCharge) rows.push({ ...paidCharge, label: '付费花费' })
  return rows.slice(0, 6)
})

const diagnosisGroups = computed(() => {
  const coreRows = standardCompareTable.value?.coreRows || []
  const promotedRows = standardCompareTable.value?.promotedRows || []
  const extraRows = standardCompareTable.value?.extraRows || []
  const overall = coreRows.slice(0, 5)
  const freeRows = [
    ...freeSearchRealtimeFacts.value,
    ...freeSearchCompareRows.value,
    ...promotedRows.filter(row => /免费搜索|搜索\/免费/.test(row.label)),
    ...freeFlowRows.value,
  ].slice(0, 8)
  const paidRows = [
    ...paidRealtimeCompareRows.value.map(row => ({
      ...row,
      detail: [row.detail, paidRealtimeComparePeriod.value].filter(Boolean).join(' · '),
    })),
    ...promotedRows.filter(row => /付费/.test(row.label)),
    ...paidFlowRows.value,
  ].slice(0, 8)
  const marketRows = [
    ...extraRows.filter(row => /市场|竞品|评价|SKU|活动|价格|等级|属性/.test(row.label)),
    ...analysisBasisRows.value,
  ].slice(0, 8)
  return [
    buildDiagnosisGroup('overall', '整体成交', overall, '看 GMV、件数、转化和访客是否同时掉'),
    buildDiagnosisGroup('free', '免费搜索', freeRows, '看搜索访客与搜索转化是否是主因'),
    buildDiagnosisGroup('paid', '付费推广', paidRows, '看花费、ROI、CPC 和付费转化是否异常'),
    buildDiagnosisGroup('market', '市场/商品侧', marketRows, '看市场排名、价格力、评价和商品基础风险'),
  ].filter(group => group.rows.length > 0)
})

const taskCards = computed(() => {
  const dispatchTasks = detail.value.dispatch_tasks || []
  if (dispatchTasks.length) {
    return dispatchTasks.map((task, index) => {
      const content = textValue(task.display_content) || textValue(task.content)
      return {
        key: `dispatch-${task.id}`,
        group: `任务 ${index + 1}`,
        title: dispatchPrimary(task),
        content: content || '未填写任务内容',
        segments: dispatchSegments(content),
        executor: dispatchExecutorLabel(task),
        deadline: formatTime(task.deadline),
        evidence: textValue(asRecord(task.extra)?.evidence),
        statusLabel: dispatchStatusLabel(task.status),
        statusColor: dispatchStatusColor(task.status),
        record: task,
      }
    })
  }
  if (operatorActionRows.value.length) {
    return operatorActionRows.value.map((row, index) => ({
      key: row.key,
      group: row.section || `动作 ${index + 1}`,
      title: row.scenario || row.dimension || row.action,
      content: row.action,
      segments: row.actions.map((action, i) => ({ label: `动作 ${i + 1}`, text: action })),
      executor: '待派发',
      deadline: '',
      evidence: row.evidence,
      statusLabel: row.priority || '建议',
      statusColor: row.priority === '高' ? 'red' : 'orange',
      record: null as InboxDispatchTask | null,
    }))
  }
  return operationActionRows.value.map((row, index) => ({
    key: row.key,
    group: `动作 ${index + 1}`,
    title: row.dimension || row.priority || '运营动作',
    content: row.action,
    segments: [],
    executor: '待派发',
    deadline: '',
    evidence: row.evidence,
    statusLabel: row.priority || '建议',
    statusColor: row.priority === '高' ? 'red' : 'orange',
    record: null as InboxDispatchTask | null,
  })).concat(suggestedActions.value.map((action, index) => ({
    key: `suggested-${index}`,
    group: `建议 ${index + 1}`,
    title: '建议动作',
    content: action,
    segments: [],
    executor: '待派发',
    deadline: '',
    evidence: '',
    statusLabel: '建议',
    statusColor: 'orange',
    record: null as InboxDispatchTask | null,
  })))
})

const evidenceSections = computed(() => {
  const sections = [
    { key: 'standard', title: '标准口径：当前24h vs 前24h', rows: standardCompareTable.value?.coreRows || [] },
    { key: 'free', title: '免费流依据', rows: [...freeSearchRealtimeFacts.value, ...freeSearchCompareRows.value, ...freeFlowRows.value] },
    { key: 'paid', title: '付费流依据', rows: [...paidRealtimeCompareRows.value, ...paidFlowRows.value, ...paidDetailRows.value] },
    { key: 'other', title: '市场/商品/数据缺口', rows: [...(standardCompareTable.value?.extraRows || []), ...analysisBasisRows.value] },
  ]
  return sections.filter(section => section.rows.length > 0)
})

async function loadDetail() {
  if (!Number.isFinite(todoId.value)) return
  cancelDeferredDetailLoads()
  showDebugPayload.value = false
  loading.value = true
  try {
    detail.value = await todoApi.get(todoId.value) as InboxTodoDetailResponse
    scheduleDeferredDetailLoads()
  } catch (error: any) {
    Message.error(error?._message || '加载待办详情失败')
  } finally {
    loading.value = false
  }
}

function scheduleDeferredDetailLoads() {
  nextTick(() => {
    deferredLoadTimer = window.setTimeout(() => {
      void loadAssignableUsers()
    }, 0)
  })
}

function cancelDeferredDetailLoads() {
  if (deferredLoadTimer != null) {
    clearTimeout(deferredLoadTimer)
    deferredLoadTimer = null
  }
}

async function loadAssignableUsers() {
  assignableUsers.value = []
  assignableUsersError.value = ''
  assignSelections.value = {}
  if (detail.value.request?.kind !== 'dispatch' || !detail.value.request?.id) return
  try {
    const data = await todoApi.listAssignableUsers({ request_id: detail.value.request.id }) as Array<Record<string, unknown>>
    assignableUsers.value = Array.isArray(data)
      ? data.map(item => ({
        id: String(item.id || ''),
        name: String(item.name || ''),
        role: String(item.role || ''),
        department: String(item.department || ''),
        dingtalk_bound: Boolean(item.dingtalk_bound),
      })).filter(item => item.id)
      : []
  } catch (error: any) {
    assignableUsersError.value = error?._message || '可选执行人加载失败'
    assignableUsers.value = []
  }
  for (const task of detail.value.dispatch_tasks || []) {
    const executorId = effectiveDispatchExecutorId(task)
    if (executorId) assignSelections.value[task.id] = executorId
  }
}

async function promptRejectReason(): Promise<string | null> {
  const reason = ref('')
  return new Promise(resolve => {
    Modal.open({
      title: '驳回理由',
      content: () => h('div', { style: 'padding-top:4px' }, [
        h('p', { style: 'margin:0 0 8px;color:var(--sf-ink-3)' }, '请简要说明驳回原因：'),
        h(Input.TextArea as any, {
          modelValue: reason.value,
          placeholder: '必填，最多500字',
          maxLength: 500,
          autoSize: { minRows: 3, maxRows: 6 },
          'onUpdate:modelValue': (val: string) => { reason.value = val },
        }),
      ]),
      okText: '确认驳回',
      cancelText: '取消',
      onBeforeOk: () => {
        const text = reason.value.trim()
        if (!text) {
          Message.warning('请填写驳回理由')
          return false
        }
        resolve(text)
        return true
      },
      onCancel: () => resolve(null),
    })
  })
}

async function decide(decision: 'approved' | 'rejected') {
  let reason = ''
  if (decision === 'rejected') {
    const result = await promptRejectReason()
    if (result === null) return
    reason = result
  }
  if (decision === 'approved' && !(await ensureDispatchExecutorsForApproval())) return
  try {
    await todoApi.decide(todoId.value, { decision, reason })
    Message.success(decision === 'approved' ? '已通过' : '已驳回')
    await loadDetail()
  } catch (error: any) {
    Message.error(error?._message || '操作失败')
  }
}

function extendSla() {
  Modal.confirm({
    title: '确认延期',
    content: '将 SLA 截止时间延后 24 小时，是否继续？',
    onOk: async () => {
      try {
        await todoApi.extendSla(todoId.value, { hours: 24 })
        Message.success('已延期 24 小时')
        await loadDetail()
      } catch (error: any) {
        Message.error(error?._message || '延期失败')
      }
    },
  })
}

async function ensureDispatchExecutorsForApproval() {
  if (detail.value.request?.kind !== 'dispatch') return true
  const tasks = (detail.value.dispatch_tasks || []).filter(task => !['done', 'cancelled'].includes(String(task.status || '')))
  const missing = tasks.filter(task => !String(effectiveDispatchExecutorId(task) || assignSelections.value[task.id] || '').trim())
  if (!missing.length) return true
  if (!assignableUsers.value.length) {
    Message.warning(assignableUsersError.value || '当前部门没有可用执行人')
    return false
  }
  const unresolved = missing.filter(task => !String(assignSelections.value[task.id] || '').trim())
  if (unresolved.length) {
    Message.warning('请先为待派发任务选择执行人')
    return false
  }
  try {
    await todoApi.updateDraft(todoId.value, {
      dispatch_tasks: missing.map(task => ({ id: task.id, executor: assignSelections.value[task.id] })),
    })
    await loadDetail()
    return true
  } catch (error: any) {
    Message.error(error?._message || '保存执行人失败')
    return false
  }
}

function canEditDispatchExecutor(record: InboxDispatchTask) {
  if (detail.value.request?.kind !== 'dispatch') return false
  return !['done', 'cancelled'].includes(record.status)
}

async function saveDispatchExecutor(record: InboxDispatchTask) {
  const executorId = String(assignSelections.value[record.id] || '').trim()
  if (!executorId) {
    Message.warning('请先选择执行人')
    return
  }
  assigningTaskId.value = record.id
  try {
    if (detail.value.request?.aggregate_decision === 'approved') {
      await todoApi.assignDispatchTask(record.id, { executor_id: executorId })
      Message.success(record.executor ? '已重新指派' : '已完成指派')
    } else {
      await todoApi.updateDraft(todoId.value, { dispatch_tasks: [{ id: record.id, executor: executorId }] })
      Message.success('已保存执行人')
    }
    await loadDetail()
  } catch (error: any) {
    Message.error(error?._message || '保存执行人失败')
  } finally {
    assigningTaskId.value = null
  }
}

function buildDiagnosisGroup(key: string, label: string, rows: ViewRow[], fallback: string) {
  const hasDanger = rows.some(row => row.tone === 'danger' || /下降|下滑|异常|风险|未采集|待补采/.test(`${row.status || ''} ${row.value} ${row.detail || ''}`))
  const hasOk = rows.length > 0 && rows.every(row => row.tone === 'ok' || /上升|正常|已采集/.test(`${row.status || ''} ${row.value}`))
  return {
    key,
    label,
    rows,
    summary: summarizeGroup(rows) || fallback,
    tone: hasDanger ? 'danger' : hasOk ? 'ok' : 'warn',
    status: hasDanger ? '需处理' : hasOk ? '正常' : '待复核',
  }
}

function summarizeGroup(rows: ViewRow[]) {
  const top = rows.find(row => row.tone === 'danger' || /下降|下滑|异常|风险/.test(`${row.status || ''} ${row.value} ${row.detail || ''}`)) || rows[0]
  if (!top) return ''
  return `${top.label} ${top.value}${top.detail ? `，${top.detail}` : ''}`
}

function normalizeCompareRows(raw: unknown, prefix: string): ViewRow[] {
  const compare = asRecord(raw)
  const rows = arrayValue(compare?.metrics)
  return rows.map((item, index) => {
    const row = asRecord(item) || {}
    const currentText = textValue(row.current_text) || '未采集'
    const previousText = textValue(row.previous_text) || '未采集'
    const changeText = textValue(row.change_text) || '-'
    return {
      key: `${prefix}-${textValue(row.key) || index}`,
      label: textValue(row.label) || '指标',
      value: currentText,
      detail: `${previousText} / ${changeText}`,
      status: changeText,
      meta: comparePeriodText(raw),
      tone: changeText.startsWith('-') ? 'danger' : changeText.startsWith('+') ? 'ok' : 'neutral',
    }
  }).filter(row => row.value !== '未采集' || !row.detail?.startsWith('未采集'))
}

function comparePeriodText(raw: unknown) {
  const period = asRecord(asRecord(raw)?.period)
  const current = textValue(period?.current_label)
  const previous = textValue(period?.compare_label)
  return current && previous ? `${current} / ${previous}` : current || previous || ''
}

function buildPaidRealtimeSummaryRow(row: ViewRow) {
  if (!paidRealtimeSummaryMetricNames.has(row.label)) return row
  if (!paidRealtimeCompareRows.value.length) return { ...row, status: '昨日同刻未采集' }
  const hasPrevious = paidRealtimeCompareRows.value.some(metric => metric.detail && !metric.detail.startsWith('未采集'))
  return {
    ...row,
    value: paidRealtimeCompareRows.value.map(metric => `${metric.label} ${metric.value}`).join(' / ') || row.value,
    detail: paidRealtimeComparePeriod.value || row.detail,
    status: hasPrevious ? '昨日同刻已对比' : '昨日同刻未采集',
    tone: hasPrevious ? 'ok' : 'warn',
  }
}

function paidSummaryPeriod() {
  for (const raw of arrayValue(payload.value.paid_flow_analysis_basis)) {
    const row = asRecord(raw)
    const period = asRecord(row?.period)
    if (period && (textValue(period.current_label) || textValue(period.compare_label))) return period
  }
  return null
}

function paidSummaryCompareRows(): ViewRow[] {
  const paidDetail = asRecord(asRecord(payload.value.decision_context)?.paid_detail)
  const summary = asRecord(paidDetail?.product_summary)
  if (!summary) return []
  const specs = [
    { key: 'charge', label: '实时花费金额', currentKey: 'charge', previousKey: 'charge_prev', changeKey: 'charge_change_pct', formatter: moneyText },
    { key: 'roi', label: 'ROI', currentKey: 'roi', previousKey: 'roi_prev', deltaKey: 'roi_delta', changeKey: 'roi_change_pct', formatter: metricNumberText },
    { key: 'cpc', label: 'CPC', currentKey: 'cpc', previousKey: 'cpc_prev', deltaKey: 'cpc_delta', changeKey: 'cpc_change_pct', formatter: moneyText },
  ]
  return specs.map(spec => {
    const current = numberValue(summary[spec.currentKey])
    const change = numberValue(summary[spec.changeKey])
    const previous = numberValue(summary[spec.previousKey])
      ?? previousFromDelta(current, numberValue(spec.deltaKey ? summary[spec.deltaKey] : null))
      ?? previousFromChange(current, change)
    const changeText = signedPercentText(change)
    return {
      key: `paid-summary-${spec.key}`,
      label: spec.label,
      value: spec.formatter(current),
      detail: `${spec.formatter(previous)} / ${changeText}`,
      status: changeText,
      tone: changeText.startsWith('-') ? 'danger' : changeText.startsWith('+') ? 'ok' : 'neutral',
    }
  }).filter(row => row.value !== '-' || !row.detail.startsWith('-'))
}

function normalizeBasisRows(rows: unknown[], prefix: string): ViewRow[] {
  return rows
    .filter(raw => {
      if (!arrayValue(payload.value.paid_flow_analysis_basis).length) return true
      return !paidDetailMetricNames.has(textValue(asRecord(raw)?.name))
    })
    .map((raw, index) => {
      const row = asRecord(raw) || {}
      const label = textValue(row.label) || textValue(row.name) || textValue(row.dimension) || '指标'
      const status = textValue(row.status)
      const period = asRecord(row.period)
      const currentLabel = textValue(period?.current_label)
      const source = textValue(row.source)
      return {
        key: `${prefix}-${index}-${label}`,
        label,
        value: textValue(row.value) || '-',
        detail: textValue(row.detail) || textValue(row.delta) || textValue(row.note),
        status: status || '-',
        meta: [source, currentLabel].filter(Boolean).join(' · '),
        tone: metricTone(status, textValue(row.value)),
      }
    })
}

function normalizeActionRows(v: unknown, prefix: string) {
  return arrayValue(v).map((raw, i) => {
    const row = asRecord(raw) || {}
    const action = textValue(row.action) || textValue(raw)
    return {
      key: `${prefix}-${i}-${action}`,
      action,
      priority: textValue(row.priority),
      dimension: textValue(row.dimension),
      evidence: textValue(row.evidence),
    }
  }).filter(row => row.action)
}

function normalizeOperatorActionRows(v: unknown) {
  return arrayValue(v).map((raw, i) => {
    const row = asRecord(raw)
    if (!row || textValue(row.source) !== 'operator_playbook') return null
    const actions = stringList(row.actions)
    const action = textValue(row.action) || actions[0]
    const selectionReason = textValue(row.benchmark_selection_reason)
    const similarityText = textValue(row.benchmark_similarity_text)
    const evidenceParts = [
      selectionReason ? `为什么这样对标：${selectionReason}` : '',
      similarityText ? `相似参考依据：${similarityText}` : '',
      textValue(row.evidence) || textValue(row.business_conclusion),
    ].filter(Boolean)
    return {
      key: `operator-${i}-${textValue(row.playbook_id) || action}`,
      section: textValue(row.section) || '运营动作',
      scenario: textValue(row.scenario),
      action,
      priority: textValue(row.priority),
      dimension: textValue(row.dimension),
      evidence: evidenceParts.join('；'),
      business_conclusion: textValue(row.business_conclusion),
      benchmark_selection_reason: selectionReason,
      benchmark_similarity_text: similarityText,
      actions,
    }
  }).filter((row): row is NonNullable<typeof row> => Boolean(row && row.action))
}

function chartValueText(value: number, unit = '') {
  const rounded = Math.round(value * 100) / 100
  const text = rounded.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  return `${unit === '¥' ? '¥' : ''}${text}${unit && unit !== '¥' ? unit : ''}`
}

function normalizeChartItem(raw: unknown) {
  const row = asRecord(raw) || {}
  const label = textValue(row.label) || textValue(row.name) || textValue(row.key)
  const value = numberValue(row.value)
  if (!label || value == null) return null
  const unit = textValue(row.unit)
  return {
    key: textValue(row.key) || label,
    label,
    value,
    unit,
    detail: textValue(row.detail),
    valueText: chartValueText(value, unit),
    width: 0,
  }
}

function normalizeCompareChartItem(raw: unknown) {
  const row = asRecord(raw) || {}
  const label = textValue(row.label) || textValue(row.name) || textValue(row.key)
  const low = numberValue(row.low)
  const benchmark = numberValue(row.benchmark)
  if (!label || (low == null && benchmark == null)) return null
  const unit = textValue(row.unit)
  return {
    key: textValue(row.key) || label,
    label,
    low: low ?? 0,
    benchmark: benchmark ?? 0,
    unit,
    lowText: chartValueText(low ?? 0, unit),
    benchmarkText: chartValueText(benchmark ?? 0, unit),
    lowWidth: 0,
    benchmarkWidth: 0,
  }
}

function fallbackChartSections(actions: Array<Record<string, any>>) {
  const issueCounts = new Map<string, number>()
  const benchmarkCounts = new Map<string, number>()
  for (const row of actions) {
    const issue = textValue(row.dimension)
    if (issue) issueCounts.set(issue, (issueCounts.get(issue) || 0) + 1)
    const benchmark = textValue(row.benchmark_label) || textValue(row.benchmark_level)
    if (benchmark) benchmarkCounts.set(benchmark, (benchmarkCounts.get(benchmark) || 0) + 1)
  }
  const result: any[] = []
  if (issueCounts.size) {
    result.push({
      type: 'segmented_bar',
      key: 'issue_mix_fallback',
      title: '问题分型分布',
      description: '根据当前待办视频建议汇总。',
      items: [...issueCounts.entries()].map(([label, value]) => ({ key: label, label, value })),
    })
  }
  if (benchmarkCounts.size) {
    result.push({
      type: 'segmented_bar',
      key: 'benchmark_mix_fallback',
      title: '对标质量',
      description: '根据当前待办参考等级汇总。',
      items: [...benchmarkCounts.entries()].map(([label, value]) => ({ key: label, label, value })),
    })
  }
  return result
}

function normalizeChartSections(rawSections: unknown, actions: Array<Record<string, any>>) {
  const source = arrayValue(rawSections).length ? arrayValue(rawSections) : fallbackChartSections(actions)
  return source.map((raw, index) => {
    const section = asRecord(raw) || {}
    const type = textValue(section.type) === 'bar_compare' ? 'bar_compare' : 'bar'
    const title = textValue(section.title) || `图表 ${index + 1}`
    const description = textValue(section.description)
    if (type === 'bar_compare') {
      const items = arrayValue(section.items).map(normalizeCompareChartItem).filter(Boolean) as any[]
      const max = Math.max(1, ...items.flatMap(item => [item.low, item.benchmark]))
      for (const item of items) {
        item.lowWidth = Math.max(4, Math.round((item.low / max) * 100))
        item.benchmarkWidth = Math.max(4, Math.round((item.benchmark / max) * 100))
      }
      return items.length ? {
        key: textValue(section.key) || `chart-${index}`,
        type,
        title,
        description,
        items,
      } : null
    }
    const items = arrayValue(section.items).map(normalizeChartItem).filter(Boolean) as any[]
    const max = Math.max(1, ...items.map(item => item.value))
    for (const item of items) {
      item.width = Math.max(4, Math.round((item.value / max) * 100))
    }
    return items.length ? {
      key: textValue(section.key) || `chart-${index}`,
      type,
      title,
      description,
      items,
    } : null
  }).filter(Boolean) as any[]
}

function dispatchPrimary(record: InboxDispatchTask) {
  const extra = asRecord(record.extra)
  const id = textValue(extra?.item_id)
  const priority = textValue(extra?.priority)
  const time = textValue(extra?.data_time)
  return [priority, id ? `商品 ${id}` : '', time].filter(Boolean).join(' · ') || '派发任务'
}

function dispatchSegments(content: string) {
  return String(content || '').split('；').map(part => part.trim()).filter(Boolean).slice(0, 6).map((part, index) => {
    const [label, ...rest] = part.split('：')
    return rest.length
      ? { label: label.trim(), text: rest.join('：').trim() }
      : { label: `要点 ${index + 1}`, text: part }
  })
}

function assignableUserLabel(user: { id: string; name?: string; role?: string; department?: string; dingtalk_bound?: boolean }) {
  return [user.name || user.id, user.department || '', user.role || '', user.dingtalk_bound ? '已绑钉钉' : '未绑钉钉'].filter(Boolean).join(' · ')
}

function effectiveDispatchExecutorId(record: InboxDispatchTask) {
  const assigned = String(record.executor || '').trim()
  if (assigned) return assigned
  return String(record.default_executor || '').trim()
}

function dispatchExecutorName(executorId: string, record?: InboxDispatchTask) {
  const user = assignableUsers.value.find(item => item.id === executorId)
  if (user) return assignableUserLabel(user)
  if (record?.executor === executorId && record.executor_name) return [record.executor_name, record.executor_department || ''].filter(Boolean).join(' · ')
  if (record?.default_executor === executorId && record.default_executor_name) return [record.default_executor_name, record.default_executor_department || ''].filter(Boolean).join(' · ')
  return executorId
}

function dispatchExecutorLabel(record: InboxDispatchTask) {
  const assigned = String(record.executor || '').trim()
  if (assigned) return dispatchExecutorName(assigned, record)
  const fallback = effectiveDispatchExecutorId(record)
  if (fallback) return `${dispatchExecutorName(fallback, record)} · 默认派发人`
  return '待指定'
}

function dispatchStatusColor(status?: string | null) {
  return {
    awaiting_dispatch: 'gray',
    sent: 'arcoblue',
    pushed_no_dingtalk: 'red',
    done: 'green',
    cancelled: 'red',
    pending_assignment: 'orange',
  }[status || ''] || 'orange'
}

function todoKindTagColor(kind: string | undefined | null) {
  return kind === 'dispatch' ? 'arcoblue' : kind === 'review' ? 'orange' : 'gray'
}

function todoStatusTagColor(status: string | undefined | null) {
  return status === 'pending' ? 'orange' : status === 'approved' ? 'green' : status === 'rejected' ? 'red' : 'gray'
}

function prioritySlug(priority: string) {
  return priority === 'P0' ? 'pill-danger' : priority === 'P1' ? 'pill-warn' : 'pill-info'
}

function confidenceSlug(confidence: string) {
  if (confidence.includes('高')) return 'pill-ok'
  if (confidence.includes('中')) return 'pill-info'
  return 'pill-warn'
}

function metricTone(status: string, value: string) {
  const text = `${status} ${value}`
  if (/下滑|下降|异常|缺少|未采集|风险|权限不足|安全校验|待补采|待复核|采集失败|-/.test(text)) return 'danger'
  if (/上升|正常|已采集|有/.test(text)) return 'ok'
  return 'neutral'
}

function stringList(v: unknown): string[] {
  if (Array.isArray(v)) {
    return v.map(item => {
      if (typeof item === 'string') return item.trim()
      const row = asRecord(item)
      if (!row) return ''
      return textValue(row.action) || textValue(row.text) || textValue(row.content) || textValue(row.suggestion) || textValue(row.basis)
    }).filter(Boolean)
  }
  const text = textValue(v)
  return text ? [text] : []
}

function summaryText(v: unknown) {
  const text = textValue(v)
  if (text) return text
  const row = asRecord(v)
  return row ? (textValue(row.recommendation) || textValue(row.summary) || textValue(row.text) || textValue(row.reason)) : ''
}

function signedPercentText(value: number | null) {
  if (value == null) return '-'
  const rounded = Math.round(value * 100) / 100
  return `${rounded > 0 ? '+' : ''}${rounded}%`
}

function previousFromChange(current: number | null, changePct: number | null) {
  if (current == null || changePct == null) return null
  const denominator = 1 + changePct / 100
  if (!Number.isFinite(denominator) || Math.abs(denominator) < 0.000001) return null
  return current / denominator
}

function previousFromDelta(current: number | null, delta: number | null) {
  if (current == null || delta == null) return null
  return current - delta
}

function moneyText(value: number | null) {
  if (value == null) return '-'
  return `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}

function metricNumberText(value: number | null) {
  if (value == null) return '-'
  return String(Math.round(value * 10000) / 10000)
}

function numberValue(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v
  if (typeof v === 'string') {
    const parsed = Number(v.replace(/[¥,%\s,]/g, ''))
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

function asRecord(v: unknown): Record<string, any> | null {
  return v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, any> : null
}

function arrayValue(v: unknown): unknown[] {
  return Array.isArray(v) ? v : []
}

function textValue(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'string') return v.trim()
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  return ''
}

watch(() => route.params.id, loadDetail, { immediate: true })
onBeforeUnmount(cancelDeferredDetailLoads)
</script>

<style scoped>
.todo-v2-root {
  max-width: 1440px;
  margin: 0 auto;
  padding: 16px 24px 48px;
}

.v2-toolbar,
.toolbar-tags,
.hero-meta,
.decision-pills,
.task-footer,
.assign-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.v2-toolbar {
  justify-content: space-between;
  margin-bottom: 12px;
}

.management-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(360px, 0.95fr);
  gap: 16px;
  padding: 18px;
  border: 1px solid rgba(24, 35, 56, 0.1);
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 10px 26px rgba(24, 35, 56, 0.06);
}

.eyebrow,
.section-kicker {
  font-size: 12px;
  font-weight: 700;
  color: #5f6b7a;
}

.hero-copy h1 {
  margin: 4px 0 8px;
  color: #172033;
  font-size: 24px;
  line-height: 1.28;
}

.hero-meta {
  color: #687385;
  font-size: 13px;
}

.hero-meta span {
  padding: 2px 8px;
  border-radius: 6px;
  background: #f5f7fb;
}

.decision-copy {
  max-width: 820px;
  margin: 14px 0 12px;
  color: #2c3546;
  font-size: 15px;
  line-height: 1.75;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 700;
}

.pill-danger { color: #b42318; background: #fff1f0; }
.pill-warn { color: #ad6800; background: #fff7e6; }
.pill-info { color: #0f5ca8; background: #eaf5ff; }
.pill-ok { color: #067647; background: #e9f8ef; }

.headline-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.headline-card {
  min-height: 98px;
  padding: 12px;
  border: 1px solid #e7ebf2;
  border-radius: 8px;
  background: #f8fafc;
}

.headline-card span,
.headline-card em {
  display: block;
  color: #6b7484;
  font-size: 12px;
  font-style: normal;
  line-height: 1.45;
}

.headline-card strong {
  display: block;
  margin: 5px 0;
  color: #172033;
  font-size: 24px;
  line-height: 1.15;
}

.headline-card.danger { border-color: #ffd8d4; background: #fff7f6; }
.headline-card.ok { border-color: #ccebd8; background: #f4fbf6; }

.content-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 312px;
  gap: 16px;
  margin-top: 16px;
}

.main-column,
.side-column {
  min-width: 0;
}

.main-column {
  display: grid;
  gap: 16px;
}

.side-column {
  display: grid;
  align-content: start;
  gap: 12px;
}

.panel,
.side-card {
  border: 1px solid #e7ebf2;
  border-radius: 8px;
  background: #fff;
}

.panel {
  padding: 16px;
}

.side-card {
  padding: 14px;
}

.panel-head,
.side-card-head,
.diagnosis-title,
.task-topline,
.evidence-card-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.panel-head {
  align-items: flex-start;
  margin-bottom: 14px;
}

.panel-head h2 {
  margin: 2px 0 0;
  color: #172033;
  font-size: 18px;
}

.panel-head p,
.side-card p {
  max-width: 560px;
  margin: 0;
  color: #687385;
  font-size: 13px;
  line-height: 1.6;
}

.diagnosis-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.diagnosis-card,
.task-card,
.evidence-card {
  border: 1px solid #e9edf4;
  border-radius: 8px;
  background: #fbfcfe;
}

.diagnosis-card {
  padding: 12px;
}

.diagnosis-title {
  align-items: flex-start;
  margin-bottom: 10px;
}

.diagnosis-title span,
.task-topline span {
  display: block;
  color: #687385;
  font-size: 12px;
}

.diagnosis-title strong,
.task-topline strong {
  display: block;
  margin-top: 2px;
  color: #172033;
  font-size: 14px;
  line-height: 1.45;
}

.signal-list {
  display: grid;
  gap: 8px;
}

.signal-row,
.evidence-row {
  display: grid;
  grid-template-columns: minmax(140px, 0.8fr) minmax(0, 1fr);
  gap: 10px;
  padding: 8px 0;
  border-top: 1px solid #eef1f6;
}

.signal-row:first-child,
.evidence-row:first-of-type {
  border-top: 0;
}

.signal-row strong,
.evidence-row span {
  color: #263247;
  font-size: 13px;
}

.signal-row span,
.evidence-row em {
  display: block;
  margin-top: 2px;
  color: #7b8493;
  font-size: 12px;
  font-style: normal;
  line-height: 1.45;
}

.signal-values b,
.evidence-row strong {
  color: #172033;
  font-size: 14px;
}

.signal-values em {
  display: block;
  margin-top: 3px;
  color: #687385;
  font-size: 12px;
  font-style: normal;
  line-height: 1.45;
}

.task-stack {
  display: grid;
  gap: 10px;
}

.chart-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.chart-panel {
  padding: 12px;
  border: 1px solid #e9edf4;
  border-radius: 8px;
  background: #fbfcfe;
}

.chart-head {
  display: grid;
  gap: 4px;
  margin-bottom: 12px;
}

.chart-head strong {
  color: #172033;
  font-size: 14px;
  line-height: 1.35;
}

.chart-head span {
  color: #687385;
  font-size: 12px;
  line-height: 1.5;
}

.share-chart,
.compare-chart {
  display: grid;
  gap: 10px;
}

.share-row {
  display: grid;
  grid-template-columns: minmax(96px, 0.62fr) minmax(120px, 1fr) auto;
  gap: 8px;
  align-items: center;
}

.compare-row {
  display: grid;
  grid-template-columns: minmax(96px, 0.44fr) minmax(0, 1fr);
  gap: 10px;
}

.chart-label {
  color: #263247;
  font-size: 12px;
  line-height: 1.45;
  word-break: break-word;
}

.share-track,
.bar-track {
  position: relative;
  height: 8px;
  overflow: hidden;
  border-radius: 999px;
  background: #edf1f7;
}

.share-track i,
.bar-track i {
  display: block;
  height: 100%;
  border-radius: inherit;
}

.share-track i {
  background: linear-gradient(90deg, #2878ff, #39a0ff);
}

.share-row strong,
.share-row span,
.bar-line b {
  color: #172033;
  font-size: 12px;
  white-space: nowrap;
}

.share-row span {
  grid-column: 2 / 4;
  color: #7b8493;
  white-space: normal;
}

.compare-bars {
  display: grid;
  gap: 6px;
}

.bar-line {
  display: grid;
  grid-template-columns: 36px minmax(90px, 1fr) auto;
  gap: 8px;
  align-items: center;
}

.bar-line span {
  color: #687385;
  font-size: 12px;
}

.bar-current {
  background: #2b7fff;
}

.bar-benchmark {
  background: #16a36a;
}

.task-card {
  padding: 12px;
}

.task-topline {
  align-items: flex-start;
}

.task-content {
  margin: 10px 0;
  color: #263247;
  font-size: 14px;
  line-height: 1.7;
}

.task-segments {
  display: grid;
  gap: 6px;
  margin-bottom: 10px;
}

.task-segments span {
  display: block;
  padding: 7px 9px;
  border-radius: 6px;
  background: #fff;
  color: #3b4556;
  font-size: 13px;
  line-height: 1.5;
}

.task-segments b {
  margin-right: 6px;
  color: #172033;
}

.task-footer {
  color: #687385;
  font-size: 12px;
}

.assign-row {
  margin-top: 10px;
}

.assign-row :deep(.arco-select) {
  min-width: 260px;
  flex: 1;
}

.evidence-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.evidence-card {
  padding: 12px;
}

.evidence-card-head {
  align-items: center;
  padding-bottom: 8px;
  border-bottom: 1px solid #eef1f6;
}

.evidence-card-head strong {
  color: #172033;
}

.evidence-card-head span {
  color: #7b8493;
  font-size: 12px;
}

.side-card-head {
  align-items: flex-start;
  margin-bottom: 8px;
}

.side-card-head span {
  color: #687385;
  font-size: 12px;
}

.side-card-head strong {
  color: #172033;
  font-size: 14px;
}

.action-buttons {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.processed-state,
.quality-list {
  display: grid;
  gap: 8px;
  color: #687385;
  font-size: 13px;
}

.quality-list div {
  padding-top: 8px;
  border-top: 1px solid #eef1f6;
}

.quality-list div:first-child {
  border-top: 0;
  padding-top: 0;
}

.quality-list strong,
.quality-list span {
  display: block;
}

.quality-list strong {
  color: #263247;
}

.quality-list span {
  margin-top: 2px;
  line-height: 1.5;
}

.debug-panel {
  padding: 6px 12px;
}

.debug-panel pre {
  max-height: 420px;
  overflow: auto;
  margin: 0;
  padding: 12px;
  border-radius: 6px;
  background: #111827;
  color: #e5e7eb;
  font-size: 12px;
  line-height: 1.55;
}

@media (max-width: 1180px) {
  .management-hero,
  .content-layout {
    grid-template-columns: 1fr;
  }

  .chart-grid {
    grid-template-columns: 1fr;
  }

  .side-column {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 780px) {
  .todo-v2-root {
    padding: 12px;
  }

  .headline-grid,
  .diagnosis-grid,
  .evidence-grid,
  .side-column {
    grid-template-columns: 1fr;
  }

  .signal-row,
  .evidence-row,
  .share-row,
  .compare-row {
    grid-template-columns: 1fr;
  }

  .share-row span {
    grid-column: auto;
  }
}
</style>
