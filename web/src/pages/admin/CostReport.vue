<template>
  <div class="page-container admin-costs-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">管理后台 · 数据与服务</div>
        <h2 class="page-title">成本报表</h2>
        <p class="page-subtitle">按用户、部门、Skill、模型、来源聚合 AI 调用成本与 token 用量</p>
      </div>
      <a-space>
        <a-checkbox v-model="showCompare" @change="loadCompare">对比上一周期</a-checkbox>
        <a-button @click="exportCsv" :disabled="!report.items?.length">导出 CSV</a-button>
        <a-tag color="arcoblue" size="medium">仅 Admin 可见</a-tag>
      </a-space>
    </div>

    <!-- KPI strip：参照 AdminCodexPlugin / inbox.jsx 的 5 cell 横向条 -->
    <div class="costs-kpi-strip">
      <div class="kpi-cell">
        <div class="kpi-label">调用数</div>
        <div class="kpi-value">{{ (report.total_calls || 0).toLocaleString() }}</div>
      </div>
      <div class="kpi-cell kpi-warn">
        <div class="kpi-label">总成本 USD</div>
        <div class="kpi-value">${{ (report.total_cost_usd || 0).toFixed(4) }}</div>
      </div>
      <div class="kpi-cell kpi-accent">
        <div class="kpi-label">输入 token</div>
        <div class="kpi-value">{{ formatTokens(report.total_input_tokens || 0) }}</div>
      </div>
      <div class="kpi-cell kpi-ok">
        <div class="kpi-label">输出 token</div>
        <div class="kpi-value">{{ formatTokens(report.total_output_tokens || 0) }}</div>
      </div>
      <div class="kpi-cell" :class="{ 'kpi-bad': !!report.truncated }">
        <div class="kpi-label">分组总数</div>
        <div class="kpi-value">{{ (report.total_groups || report.items?.length || 0).toLocaleString() }}</div>
      </div>
    </div>

    <!-- 筛选器 -->
    <a-card class="page-list-card filters-card">
      <a-space wrap>
        <span class="filter-label">时间范围</span>
        <a-select v-model="filters.days" class="filter-days" @change="loadAll">
          <a-option :value="1">今日</a-option>
          <a-option :value="7">近 7 天</a-option>
          <a-option :value="14">近 14 天</a-option>
          <a-option :value="30">近 30 天</a-option>
          <a-option :value="90">近 90 天</a-option>
        </a-select>

        <span class="filter-label">分组维度</span>
        <a-select v-model="filters.group_by" class="filter-group" @change="loadReport">
          <a-option value="user_id">按用户</a-option>
          <a-option value="department">按部门</a-option>
          <a-option value="skill_id">按 Skill</a-option>
          <a-option value="model">按模型</a-option>
          <a-option value="call_source">按调用来源</a-option>
        </a-select>

        <span class="filter-label">部门</span>
        <a-input
          v-model="filters.department"
          placeholder="（不限）"
          allow-clear
          class="filter-department"
          @change="loadReport"
        />

        <span class="filter-label">模型</span>
        <a-input
          v-model="filters.model"
          placeholder="（不限）"
          allow-clear
          class="filter-model"
          @change="loadReport"
        />

        <span class="filter-label">来源</span>
        <a-tooltip
          v-if="sectionErrors.callSources"
          :content="`调用来源加载失败：${sectionErrors.callSources}`"
        >
          <a-select
            v-model="filters.call_source"
            placeholder="加载失败"
            allow-clear
            class="filter-source"
            :disabled="true"
          />
        </a-tooltip>
        <a-select
          v-else
          v-model="filters.call_source"
          placeholder="（不限）"
          allow-clear
          class="filter-source"
          @change="loadReport"
        >
          <a-option v-for="src in callSourceOptions" :key="src" :value="src">{{ src }}</a-option>
        </a-select>

        <a-button type="primary" @click="loadAll">刷新</a-button>
      </a-space>
    </a-card>

    <a-card class="page-list-card trace-card" title="Run Trace 快查">
      <div class="trace-search">
        <a-input v-model="traceRunId" placeholder="输入 run_id" allow-clear class="trace-run-input" @press-enter="loadTrace" />
        <a-button type="primary" :loading="traceLoading" :disabled="!traceRunId" @click="loadTrace">查询</a-button>
        <a-alert v-if="traceError" type="warning" class="trace-inline-alert">{{ traceError }}</a-alert>
      </div>
      <div v-if="trace" class="trace-summary-grid">
        <div class="trace-stat">
          <span>Skill</span>
          <strong>{{ traceSkillId }}</strong>
        </div>
        <div class="trace-stat">
          <span>状态</span>
          <strong>{{ traceStatus }}</strong>
        </div>
        <div class="trace-stat">
          <span>采集 proof</span>
          <strong>{{ trace.collection_proofs?.length || 0 }}</strong>
        </div>
        <div class="trace-stat">
          <span>AI 调用</span>
          <strong>{{ trace.intelligence_analyze_runs?.length || 0 }}</strong>
        </div>
      </div>
    </a-card>

    <!-- 分组报表表格 -->
    <a-card class="page-list-card" :title="`按 ${groupByLabel} 分组（近 ${filters.days} 天）`">
      <a-alert
        v-if="sectionErrors.report"
        type="error"
        class="section-alert"
        :show-icon="true"
      >
        加载失败：{{ sectionErrors.report }}
      </a-alert>
      <a-alert
        v-else-if="report.truncated"
        type="warning"
        class="section-alert"
        :show-icon="true"
      >
        共 {{ report.total_groups }} 个 {{ groupByLabel }}，仅显示成本最高的前
        {{ report.limit }} 个。如需更多请缩小查询范围或调整 limit。
      </a-alert>
      <a-spin :loading="loading">
        <SfEmptyState v-if="!report.items?.length && !sectionErrors.report" title="成本报表" description="所选范围无成本记录" hint="可以扩大时间范围，或切换分组维度后重新查询。" icon="database" />
        <a-table
          v-else
          :data="report.items"
          :pagination="{ pageSize: 20 }"
          row-key="key"
          size="medium"
        >
          <template #columns>
            <a-table-column :title="groupByLabel" data-index="key">
              <template #cell="{ record }">
                <a-tag color="arcoblue" size="small">{{ record.key }}</a-tag>
              </template>
            </a-table-column>
            <a-table-column title="调用数" data-index="calls" :width="100" align="right">
              <template #cell="{ record }">
                <span class="num-cell">{{ record.calls.toLocaleString() }}</span>
              </template>
            </a-table-column>
            <a-table-column title="成本 (USD)" :width="140" align="right">
              <template #cell="{ record }">
                <span class="cost-value">${{ record.cost_usd.toFixed(4) }}</span>
              </template>
            </a-table-column>
            <a-table-column v-if="showCompare" title="对比上期" :width="140" align="right">
              <template #cell="{ record }">
                <span v-if="prevMap[record.key] != null" :class="compareClass(record.cost_usd, prevMap[record.key])">
                  {{ compareLabel(record.cost_usd, prevMap[record.key]) }}
                </span>
                <span v-else class="cost-muted">—</span>
              </template>
            </a-table-column>
            <a-table-column title="占比" :width="160">
              <template #cell="{ record }">
                <a-progress
                  :percent="report.total_cost_usd > 0 ? record.cost_usd / report.total_cost_usd : 0"
                  :show-text="true"
                  size="small"
                />
              </template>
            </a-table-column>
            <a-table-column title="输入 token" :width="120" align="right">
              <template #cell="{ record }">
                <span class="num-cell">{{ formatTokens(record.input_tokens) }}</span>
              </template>
            </a-table-column>
            <a-table-column title="输出 token" :width="120" align="right">
              <template #cell="{ record }">
                <span class="num-cell">{{ formatTokens(record.output_tokens) }}</span>
              </template>
            </a-table-column>
          </template>
        </a-table>
      </a-spin>
    </a-card>

    <a-row :gutter="16">
      <!-- Top 用户 -->
      <a-col :span="12">
        <a-card class="page-list-card" title="Top 10 用户（近 7 天）">
          <a-alert
            v-if="sectionErrors.topUsers"
            type="error"
            class="section-alert"
            :show-icon="true"
          >
            加载失败：{{ sectionErrors.topUsers }}
          </a-alert>
          <SfEmptyState v-else-if="!topUsers.length" title="Top 用户" description="无数据" hint="近 7 天暂无可聚合的 AI 成本。" icon="users" />
          <a-table
            v-else
            :data="topUsers"
            :pagination="false"
            size="small"
            row-key="user_id"
          >
            <template #columns>
              <a-table-column title="用户" data-index="user_id" />
              <a-table-column title="调用数" data-index="calls" :width="80" align="right">
                <template #cell="{ record }">
                  <span class="num-cell">{{ (record.calls || 0).toLocaleString() }}</span>
                </template>
              </a-table-column>
              <a-table-column title="成本 (USD)" :width="120" align="right">
                <template #cell="{ record }">
                  <span class="cost-value">${{ record.cost_usd.toFixed(4) }}</span>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-card>
      </a-col>

      <!-- 按天成本曲线（横向条形） -->
      <a-col :span="12">
        <a-card class="page-list-card" :title="`按天成本（近 ${filters.days} 天）`">
          <a-alert
            v-if="sectionErrors.byDay"
            type="error"
            class="section-alert"
            :show-icon="true"
          >
            加载失败：{{ sectionErrors.byDay }}
          </a-alert>
          <SfEmptyState v-else-if="!byDay.length" title="按天成本" description="无数据" hint="所选周期内暂无成本趋势。" icon="trend" />
          <div v-else class="day-bars">
            <div v-for="d in byDay" :key="d.date" class="day-row">
              <div class="day-label">{{ d.date?.slice(5) || '-' }}</div>
              <a-progress
                :percent="dayMaxCost > 0 ? d.cost_usd / dayMaxCost : 0"
                :show-text="false"
                size="medium"
                :color="dayBarColor"
                class="day-progress"
              />
              <div class="day-cost">${{ d.cost_usd.toFixed(4) }}</div>
              <div class="day-calls">{{ d.calls }} 次</div>
            </div>
          </div>
        </a-card>
      </a-col>
    </a-row>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { Message } from '@arco-design/web-vue'
import { dashboardApi as rawDashboardApi, runTraceApi, type RunTraceResponse } from '@/api'
import { SfEmptyState } from '@/components/common'

const dashboardApi: any = rawDashboardApi
const loading = ref(false)
const report = ref<any>({
  total_calls: 0, total_cost_usd: 0,
  total_input_tokens: 0, total_output_tokens: 0,
  items: [],
  truncated: false,
  total_groups: 0,
  limit: 100,
})
const topUsers = ref<any[]>([])
const byDay = ref<any[]>([])
const callSourceOptions = ref<string[]>([])
const traceRunId = ref('')
const traceLoading = ref(false)
const traceError = ref('')
const trace = ref<RunTraceResponse | null>(null)

// Codex 二轮 MEDIUM-3 修复：分别追踪每个 section 的失败状态，
// 让 UI 显式显示加载失败而不是静默空数据
// 三轮补：callSources 也纳入，避免筛选器静默空选项
const sectionErrors = ref<any>({
  report: null,
  topUsers: null,
  byDay: null,
  callSources: null,
})

const filters = reactive({
  days: 7,
  group_by: 'user_id',
  department: '',
  model: '',
  call_source: undefined,
})

const GROUP_BY_LABELS: Record<string, string> = {
  user_id: '用户',
  department: '部门',
  skill_id: 'Skill',
  model: '模型',
  call_source: '调用来源',
}
const groupByLabel = computed(() => GROUP_BY_LABELS[filters.group_by] || filters.group_by)
const traceSkillId = computed(() => String(trace.value?.execution_run?.skill_id || trace.value?.execution_run?.skill_name || trace.value?.run_id || '-'))
const traceStatus = computed(() => String(trace.value?.execution_run?.status || trace.value?._skillforge_meta?.status || '-'))

const dayMaxCost = computed(() => {
  if (!byDay.value.length) return 0
  return Math.max(...byDay.value.map(d => d.cost_usd || 0))
})

// 进度条颜色用 ai-warn token（成本走 warn 语义）
const dayBarColor = 'var(--ai-warn)'

function formatTokens(n: number) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k'
  return String(n)
}

function _errMessage(e: any) {
  if (e?.status === 403) return '需要 admin 权限'
  if (e?.status === 422) return e?._message || '参数错误'
  return e?._message || '加载失败'
}

async function loadReport() {
  loading.value = true
  sectionErrors.value.report = null
  try {
    const params: any = {
      days: filters.days,
      group_by: filters.group_by,
      limit: 100,
    }
    if (filters.department) params.department = filters.department
    if (filters.model) params.model = filters.model
    if (filters.call_source) params.call_source = filters.call_source
    report.value = await dashboardApi.costsReport(params)
  } catch (e: any) {
    sectionErrors.value.report = _errMessage(e)
    if (e?.status === 403) {
      Message.error('需要 admin 权限才能查看成本报表')
    } else if (e?.status === 422) {
      Message.error(`参数错误: ${e?._message || 'group_by 不在允许列表'}`)
    } else {
      Message.error(e?._message || '加载报表失败')
    }
    throw e
  } finally {
    loading.value = false
  }
}

async function loadTopUsers() {
  sectionErrors.value.topUsers = null
  try {
    const res = await dashboardApi.costsTopUsers(10, 7)
    topUsers.value = res?.items || []
  } catch (e: any) {
    sectionErrors.value.topUsers = _errMessage(e)
    topUsers.value = []
    throw e
  }
}

async function loadByDay() {
  sectionErrors.value.byDay = null
  try {
    const res = await dashboardApi.costsByDay(filters.days)
    byDay.value = res?.items || []
  } catch (e: any) {
    sectionErrors.value.byDay = _errMessage(e)
    byDay.value = []
    throw e
  }
}

async function loadCallSources() {
  sectionErrors.value.callSources = null
  try {
    const res = await dashboardApi.costsCallSources(30)
    callSourceOptions.value = res?.items || []
  } catch (e: any) {
    sectionErrors.value.callSources = _errMessage(e)
    callSourceOptions.value = []
    throw e
  }
}

async function loadAll() {
  // Codex MEDIUM-D3 修复：用 Promise.allSettled 让单个失败不拖垮整页
  // Codex 二轮 MEDIUM-3 修复：同时记录每个 section 的失败状态，UI 显式显示
  const results = await Promise.allSettled([
    loadReport(),
    loadTopUsers(),
    loadByDay(),
    loadCallSources(),
  ])
  results.forEach((r, idx) => {
    if (r.status === 'rejected') {
      const labels = ['报表', 'Top 用户', '按天成本', '调用来源列表']
      console.warn(`[CostReport] ${labels[idx]} 加载失败:`, r.reason)
    }
  })
  if (showCompare.value) loadCompare()
}

async function loadTrace() {
  const runId = traceRunId.value.trim()
  if (!runId) return
  traceLoading.value = true
  traceError.value = ''
  trace.value = null
  try {
    trace.value = await runTraceApi.get(runId)
  } catch (e: any) {
    try {
      trace.value = await runTraceApi.getExecutionTrace(runId)
    } catch {
      traceError.value = e?._message || 'Run Trace 接口未就绪或无权查看'
    }
  } finally {
    traceLoading.value = false
  }
}

// ── 对比上一周期（环比）──
const showCompare = ref(false)
const prevMap = ref<Record<string, number>>({})

async function loadCompare() {
  if (!showCompare.value) {
    prevMap.value = {}
    return
  }
  try {
    // 后端现在支持 offset_days：直接拉上一个周期的聚合结果，按 key 精确对齐
    const res = await dashboardApi.costsReport({
      days: filters.days,
      offset_days: filters.days,
      group_by: filters.group_by,
      department: filters.department || undefined,
      model: filters.model || undefined,
      call_source: filters.call_source || undefined,
      limit: 200,
    })
    const allItems = res?.items || []
    const map: Record<string, number> = {}
    for (const a of allItems) {
      map[a.key] = a.cost_usd || 0
    }
    prevMap.value = map
  } catch {
    prevMap.value = {}
  }
}

function compareLabel(curr: number, prev: number): string {
  if (prev <= 0) return curr > 0 ? '新增' : '—'
  const pct = ((curr - prev) / prev) * 100
  const sign = pct > 0 ? '+' : ''
  return `${sign}${pct.toFixed(1)}%`
}

function compareClass(curr: number, prev: number): string {
  if (prev <= 0) return 'cost-muted'
  const pct = ((curr - prev) / prev) * 100
  if (pct > 5) return 'cost-up'
  if (pct < -5) return 'cost-down'
  return 'cost-flat'
}

// ── CSV 导出 ──
function exportCsv() {
  const items = report.value.items || []
  if (!items.length) {
    Message.warning('没有可导出的数据')
    return
  }
  const headers = [groupByLabel.value, '调用数', '成本 (USD)', '输入 token', '输出 token']
  if (showCompare.value) headers.push('对比上期')
  const rows = items.map((x: any) => {
    const row = [
      x.key,
      x.calls,
      x.cost_usd?.toFixed?.(4) ?? x.cost_usd ?? '',
      x.input_tokens ?? '',
      x.output_tokens ?? '',
    ]
    if (showCompare.value) {
      const prev = prevMap.value[x.key]
      row.push(prev != null ? compareLabel(x.cost_usd, prev) : '')
    }
    return row
  })
  const csv = [headers, ...rows]
    .map((r) => r.map((cell: any) => `"${String(cell ?? '').replace(/"/g, '""')}"`).join(','))
    .join('\n')
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `cost_report_${filters.group_by}_${filters.days}d_${Date.now()}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(loadAll)
</script>

<style scoped>
/* ── 设计稿 page chrome 覆盖 ── */
.admin-costs-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.admin-costs-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
}
.admin-costs-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.admin-costs-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
  margin-bottom: 16px;
}
.admin-costs-page :deep(.page-list-card .arco-card-header) {
  border-bottom: 1px solid var(--ai-border);
  padding: 10px 16px;
  min-height: 0;
}
.admin-costs-page :deep(.page-list-card .arco-card-header-title) {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}

/* ── KPI strip ── */
.costs-kpi-strip {
  display: flex;
  align-items: stretch;
  padding: 0;
  margin-bottom: 16px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  overflow: hidden;
  font-family: var(--ai-font-sans);
}
.kpi-cell {
  flex: 1 1 0;
  min-width: 0;
  padding: 14px 20px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  border-left: 1px solid var(--ai-border);
  background: transparent;
}
.kpi-cell:first-child { border-left: 0; }
.kpi-label {
  color: var(--ai-ink-4);
  font-size: 10.5px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  line-height: 1.2;
}
.kpi-value {
  color: var(--ai-ink-1);
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
}
.kpi-cell.kpi-ok .kpi-value { color: var(--ai-ok); }
.kpi-cell.kpi-accent .kpi-value { color: var(--ai-accent-ink); }
.kpi-cell.kpi-warn .kpi-value { color: var(--ai-warn); }
.kpi-cell.kpi-bad .kpi-value { color: var(--ai-bad); }

/* ── 筛选区 ── */
.filters-card :deep(.arco-card-body) { padding: 12px 16px; }
.filter-label {
  font-size: 11.5px;
  color: var(--ai-ink-4);
  margin-right: 4px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-weight: 500;
}

/* ── 表格内的数字与成本 ── */
.cost-value {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--ai-warn);
  font-weight: 500;
}
.num-cell {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--ai-ink-1);
}
.cost-up {
  color: var(--ai-bad);
  font-weight: 600;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.cost-down {
  color: var(--ai-ok);
  font-weight: 600;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.cost-flat {
  color: var(--ai-ink-3);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.cost-muted { color: var(--ai-ink-4); }

/* ── Run Trace 快查 ── */
.trace-search {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.trace-inline-alert {
  flex: 1 1 280px;
}
.trace-summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}
.trace-stat {
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
}
.trace-stat span {
  display: block;
  color: var(--ai-ink-4);
  font-size: 10.5px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-weight: 500;
}
.trace-stat strong {
  display: block;
  margin-top: 4px;
  color: var(--ai-ink-1);
  font-size: 14px;
  font-weight: 600;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── 按天成本横向条 ── */
.day-bars {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 320px;
  overflow-y: auto;
}
.day-row {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
}
.day-label {
  width: 48px;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.day-cost {
  width: 80px;
  text-align: right;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--ai-warn);
}
.day-calls {
  width: 60px;
  text-align: right;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

/* ── Arco a-tag → ai-pill 风格映射 ── */
.admin-costs-page :deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.admin-costs-page :deep(.arco-tag-color-arcoblue),
.admin-costs-page :deep(.arco-tag-color-blue) {
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
  border-color: transparent;
}
.admin-costs-page :deep(.arco-tag-color-orange) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.admin-costs-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
.admin-costs-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}
.admin-costs-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  border-color: transparent;
}

/* ── Arco table 行 — 密集化 ── */
.admin-costs-page :deep(.arco-table-th) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 8px 10px !important;
}
.admin-costs-page :deep(.arco-table-td) {
  font-size: 12.5px;
  color: var(--ai-ink-1);
  padding: 10px 10px !important;
  border-bottom: 1px solid var(--ai-border) !important;
}
.admin-costs-page :deep(.arco-table-tr:hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

@media (max-width: 640px) {
  .costs-kpi-strip { flex-wrap: wrap; }
  .kpi-cell {
    flex: 1 1 50%;
    border-left: 0;
    border-top: 1px solid var(--ai-border);
  }
  .kpi-cell:nth-child(-n+2) { border-top: 0; }
}

/* Admin sweep utilities */
.filter-days {
  width: 120px;
}
.filter-group,
.filter-department {
  width: 140px;
}
.filter-model {
  width: 180px;
}
.filter-source {
  width: 200px;
}
.trace-run-input {
  max-width: 360px;
}
.section-alert {
  margin-bottom: 12px;
}
.day-progress {
  flex: 1;
}
@media (max-width: 900px) {
  .costs-kpi-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .filter-days,
  .filter-group,
  .filter-department,
  .filter-model,
  .filter-source,
  .trace-run-input {
    width: 100%;
    max-width: none;
  }
  .trace-search {
    flex-direction: column;
    align-items: stretch;
  }
}

</style>
