<template>
  <div class="inbox-center">
    <!-- 设计稿 inbox.jsx 的 ai-sidebar：视图 / 报告流 / 来源 Skill 三组 -->
    <aside
      class="inbox-sidebar ai-sidebar"
      :class="{ 'inbox-sidebar--open': sidebarOpen }"
      data-testid="inbox-sidebar"
    >
      <div class="inbox-sidebar-head">
        <span class="inbox-sidebar-title">收件</span>
        <button class="inbox-sidebar-close" @click="sidebarOpen = false" aria-label="关闭导航">
          <SfShellIcon name="x" class="ic" />
        </button>
      </div>
      <div class="ai-side-group">
        <div class="ai-side-label">视图</div>
        <div
          class="ai-side-item"
          :class="{ active: sidebarView === 'all' && activeTab === 'pending' }"
          @click="onSidebarSelectView('all')"
        >
          <SfShellIcon name="inbox" class="ic" />
          <span>所有待办</span>
          <span class="count">{{ pendingCount }}</span>
        </div>
        <div
          class="ai-side-item"
          :class="{ active: sidebarView === 'p0' && activeTab === 'pending' }"
          @click="onSidebarSelectView('p0')"
        >
          <SfShellIcon name="warn" class="ic" />
          <span>P0 紧急</span>
          <span class="count" :class="{ 'count-bad': p0Count > 0 }">{{ p0Count }}</span>
        </div>
        <div
          class="ai-side-item"
          :class="{ active: sidebarView === 'p1' && activeTab === 'pending' }"
          @click="onSidebarSelectView('p1')"
        >
          <SfShellIcon name="warn" class="ic" />
          <span>P1 优先</span>
          <!-- 设计稿用红色表达"有 P1 待办需关注"。0 件时回归普通灰，避免误导用户。 -->
          <span class="count" :class="{ 'count-bad': p1Count > 0 }">{{ p1Count }}</span>
        </div>
        <div
          class="ai-side-item"
          :class="{ active: sidebarView === 'mine' && activeTab === 'pending' }"
          @click="onSidebarSelectView('mine')"
        >
          <SfShellIcon name="users" class="ic" />
          <span>派给我</span>
          <span class="count">{{ mineCount }}</span>
        </div>
        <div
          class="ai-side-item"
          :class="{ active: sidebarView === 'done' && activeTab === 'pending' }"
          @click="onSidebarSelectView('done')"
        >
          <SfShellIcon name="check" class="ic" />
          <span>我已处理</span>
          <span class="count">{{ doneCount }}</span>
        </div>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">报告流</div>
        <div
          class="ai-side-item"
          :class="{ active: activeTab === 'reports' }"
          @click="onSidebarSelectTab('reports')"
        >
          <SfShellIcon name="doc" class="ic" />
          <span>报告</span>
          <span class="count">{{ reportsCount }}</span>
        </div>
        <div
          class="ai-side-item"
          :class="{ active: activeTab === 'drift' }"
          @click="onSidebarSelectTab('drift')"
        >
          <SfShellIcon name="trend" class="ic" />
          <span>Drift / 漂移</span>
          <span class="count">{{ driftCount }}</span>
        </div>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">来源 Skill</div>
        <div
          v-for="skill in sidebarSkills"
          :key="skill.id"
          class="ai-side-item"
          :class="{ active: sidebarSkillFilter === skill.id && activeTab === 'pending' }"
          @click="onSidebarSelectSkill(skill.id)"
        >
          <SfShellIcon name="cube" class="ic" />
          <span :title="skill.name">{{ skill.name }}</span>
          <span class="count">{{ skill.count }}</span>
        </div>
      </div>
    </aside>

    <div class="inbox-main ai-main">
      <button class="inbox-sidebar-toggle" @click="sidebarOpen = !sidebarOpen" aria-label="打开导航">
        <SfShellIcon name="list" class="ic" />
        <span>{{ sidebarLabel }}</span>
      </button>

      <section class="inbox-pagehead ai-pagehead">
        <div class="inbox-pagehead-top">
          <div>
            <div class="ai-crumbs">工作台 · 收件</div>
            <h1 class="ai-title">收件</h1>
            <p class="ai-sub">由能力产出的待办、报告与漂移在此统一处理</p>
          </div>
          <div class="inbox-top-actions">
            <button
              type="button"
              class="ai-btn"
              :disabled="!canBatchDispatch"
              :title="canBatchDispatch ? '前往派发任务批量分配' : '当前 tab 不支持批量派发'"
              @click="onClickBatchDispatch"
            >
              <SfShellIcon name="filter" />
              批量派发
            </button>
            <button
              type="button"
              class="ai-btn"
              :disabled="exporting"
              :title="exportTooltip"
              @click="onClickExport"
            >
              <SfShellIcon name="download" />
              {{ exporting ? '导出中' : '导出' }}
            </button>
            <button
              type="button"
              class="ai-btn primary"
              :disabled="markAllReadDisabled"
              :title="markAllReadTooltip"
              data-testid="inbox-mark-all-read"
              @click="onClickMarkAllRead"
            >
              <SfShellIcon name="check" />
              全部已读
            </button>
          </div>
        </div>
      </section>

      <InboxHeader :stats="todoStats" :overview="overview" />

      <div class="inbox-tabs ai-tabs">
        <button
          class="inbox-tab ai-tab"
          :class="{ active: activeTab === 'pending' }"
          type="button"
          @click="onTabChange('pending')"
        >
          <span class="inbox-tab-title">
            待办
            <span class="ai-pill bad inbox-tab-pill">{{ pendingCount }}</span>
          </span>
        </button>
        <button
          class="inbox-tab ai-tab"
          :class="{ active: activeTab === 'reports' }"
          type="button"
          @click="onTabChange('reports')"
        >
          <span class="inbox-tab-title report-tab-title">
            报告
            <span class="ai-pill inbox-tab-pill">{{ reportsCount }}</span>
            <span v-if="showUnreadDot" class="report-tab-dot" data-testid="report-tab-dot" />
          </span>
        </button>
        <button
          class="inbox-tab ai-tab"
          :class="{ active: activeTab === 'drift' }"
          type="button"
          @click="onTabChange('drift')"
        >
          <span class="inbox-tab-title">
            Drift
            <span class="ai-pill warn inbox-tab-pill">{{ driftCount }}</span>
          </span>
        </button>
      </div>

      <div class="inbox-body ai-pagebody">
        <TodosTab v-if="activeTab === 'pending'" :active="activeTab === 'pending'" @stats-change="handleTodoStats" />
        <ReportsTab v-else-if="activeTab === 'reports'" :active="activeTab === 'reports'" @latest-report-change="handleLatestReportChange" />
        <DriftAlertsTab v-else-if="activeTab === 'drift'" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import InboxHeader from './InboxHeader.vue'
import { driftAlertApi, inboxApi, todoApi } from '@/api'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'
import {
  defaultInboxDateRange,
  readQueryText,
  reportChannelLabel,
  reportTriggerLabel,
  todoKindLabel,
  todoStatusLabel,
} from './presentation'
import type { DriftAlertRow } from '@/api'
import type { InboxOverviewResponse, InboxTabKey, InboxTodoListItem, InboxTodoStats, ReportListItem } from '@/types/inbox'

const TodosTab = defineAsyncComponent(() => import('./TodosTab.vue').then((mod) => mod.default))
const ReportsTab = defineAsyncComponent(() => import('./ReportsTab.vue').then((mod) => mod.default))
const DriftAlertsTab = defineAsyncComponent(() => import('./DriftAlertsTab.vue').then((mod) => mod.default))

const route: any = useRoute()
const router: any = useRouter()

const activeTab = ref<InboxTabKey>('pending')
// 移动端 sidebar 抽屉开关
const sidebarOpen = ref(false)
const exporting = ref(false)

// 左侧 sidebar 视图状态（all / p0 / p1 / mine / done），仅在 pending tab 内生效
type SidebarView = 'all' | 'p0' | 'p1' | 'mine' | 'done'
const sidebarView = ref<SidebarView>('all')
const sidebarSkillFilter = ref<string>('')

// 来源 Skill 列表：服务端 GET /todos/skills/top 返回当前用户可见的 pending todo 按 skill 聚合
const sidebarSkills = ref<Array<{ id: string; name: string; count: number }>>([])
// P0 / P1 / 派给我在 stats 首帧回来前用列表 total 兜底，之后复用服务端聚合，避免口径漂移。
const fallbackP0Count = ref(0)
const fallbackP1Count = ref(0)
const fallbackMineCount = ref(0)
// Drift / 漂移计数：来自 /data-sources/platform-api-drift-alerts 列表 total
const driftCount = ref(0)

async function loadSidebarTopSkills() {
  try {
    const res: any = await (inboxApi as any).topSourceSkills?.({ limit: 6, status: 'pending' })
      ?? await fetch('/api/todos/skills/top?limit=6&status=pending', { credentials: 'include' })
        .then(r => r.ok ? r.json() : { items: [] })
    sidebarSkills.value = Array.isArray(res?.items) ? res.items : []
  } catch {
    sidebarSkills.value = []
  }
}

// 三个独立计数并发请求；任一失败回退到 0，不阻塞 sidebar 渲染
async function loadSidebarCounts() {
  // 防御：测试环境或 API mock 缺失时直接降级为 0，不抛异常
  const safeTodoList = (params: Record<string, unknown>) => {
    try { return todoApi?.list ? todoApi.list(params) : Promise.resolve({ items: [], total: 0 }) }
    catch { return Promise.resolve({ items: [], total: 0 }) }
  }
  const safeDriftList = (params: Record<string, unknown>) => {
    try { return driftAlertApi?.list ? driftAlertApi.list(params) : Promise.resolve({ items: [], total: 0 }) }
    catch { return Promise.resolve({ items: [], total: 0 }) }
  }
  const [p0Res, p1Res, mineRes, driftRes] = await Promise.allSettled([
    safeTodoList({ status: 'pending', priority: 'P0', page: 1, page_size: 1 }),
    safeTodoList({ status: 'pending', priority: 'P1', page: 1, page_size: 1 }),
    safeTodoList({ status: 'pending', assignee: 'me', page: 1, page_size: 1 }),
    safeDriftList({ acknowledged: false, page_size: 1 }),
  ])
  if (p0Res.status === 'fulfilled') {
    const r: any = p0Res.value
    fallbackP0Count.value = Number(r?.total ?? (Array.isArray(r?.items) ? r.items.length : 0)) || 0
  }
  if (p1Res.status === 'fulfilled') {
    const r: any = p1Res.value
    fallbackP1Count.value = Number(r?.total ?? (Array.isArray(r?.items) ? r.items.length : 0)) || 0
  }
  if (mineRes.status === 'fulfilled') {
    const r: any = mineRes.value
    fallbackMineCount.value = Number(r?.total ?? (Array.isArray(r?.items) ? r.items.length : 0)) || 0
  }
  if (driftRes.status === 'fulfilled') {
    const r: any = driftRes.value
    const items = r?.items || []
    driftCount.value = Number(r?.total ?? items.length) || 0
  }
}

// unreadCount 为服务端计算结果；null 表示未知（请求失败时显示 "—" 而非猜测）
const unreadCount = ref<number | null>(0)
const showUnreadDot = ref(false)
const todoStats = ref<InboxTodoStats>({})
const overview = ref<InboxOverviewResponse | null>(null)

// v2.0.17 H6：去重 mark-read / getUnreadCount 竞态。快速切 tab 时：
// - 正在 inflight 的 mark-read 被 abort，只有最新一次请求结果决定 UI
// - getUnreadCount 单调增 requestId 守卫，过期响应不覆盖新状态
let markReadAbort: AbortController | null = null
let unreadReqId = 0
let sidebarLoadScheduled = false
let sidebarLoadTimer: ReturnType<typeof setTimeout> | null = null

function handleTodoStats(next: InboxTodoStats) {
  todoStats.value = next || {}
}

// 左侧 sidebar 计数：复用 todoStats / overview
const pendingCount = computed(
  () => overview.value?.pending ?? todoStats.value.pending ?? 0,
)
const p0Count = computed(() => {
  const priorityCounts = todoStats.value.pending_by_priority
  if (priorityCounts) return Number(priorityCounts.P0 ?? 0) || 0
  return fallbackP0Count.value
})
const p1Count = computed(() => {
  const priorityCounts = todoStats.value.pending_by_priority
  if (priorityCounts) return Number(priorityCounts.P1 ?? 0) || 0
  return fallbackP1Count.value
})
const mineCount = computed(() => fallbackMineCount.value)
// "我已处理" = 通过 + 驳回 + 同事已处理；优先使用服务端 done 聚合。
const doneCount = computed(
  () => todoStats.value.done
    ?? ((todoStats.value.approved ?? 0) + (todoStats.value.rejected ?? 0) + (todoStats.value.resolved_by_peer ?? 0)),
)
const reportsCount = computed(() => unreadCount.value ?? 0)

// 顶部右上角三按钮的可用性 / tooltip
const canBatchDispatch = computed(() => activeTab.value === 'pending')
const batchDispatchTooltip = computed(() =>
  canBatchDispatch.value ? '前往派发任务批量分配' : '切到「待办」tab 后可批量派发',
)
const exportTooltip = computed(() => {
  if (activeTab.value === 'reports') return '导出当前报告列表'
  if (activeTab.value === 'drift') return '导出 Drift 列表'
  return '导出当前待办列表'
})
const markAllReadDisabled = computed(() => (unreadCount.value ?? 0) <= 0)
const markAllReadTooltip = computed(() =>
  markAllReadDisabled.value ? '当前没有未读报告' : '标记所有报告为已读',
)

function onClickBatchDispatch() {
  if (!canBatchDispatch.value) return
  router.push('/inbox/dispatch')
}

async function onClickExport() {
  if (exporting.value) return
  exporting.value = true
  try {
    const count = activeTab.value === 'reports'
      ? await exportReportsCsv()
      : activeTab.value === 'drift'
        ? await exportDriftCsv()
        : await exportTodosCsv()
    if (count > 0) {
      Message.success(`已导出 ${count} 条数据`)
    }
  } catch (error: any) {
    Message.error(error?._message || error?.message || '导出失败')
  } finally {
    exporting.value = false
  }
}

function defaultExportDateRange(): string[] {
  return defaultInboxDateRange(7)
}

function queryDateRange(fromKey: string, toKey: string): string[] {
  const from = readQueryText(route.query[fromKey])
  const to = readQueryText(route.query[toKey])
  return from && to ? [from, to] : defaultExportDateRange()
}

function currentTodoSort(): { sort_by: string; sort_order: string } {
  const rawSort = readQueryText(route.query.sort)
  const [sortByFromRaw, sortOrderFromRaw] = rawSort ? rawSort.split(':') : ['', '']
  const sortBy = sortByFromRaw || readQueryText(route.query.sort_by) || 'created_at'
  const sortOrder = sortOrderFromRaw || readQueryText(route.query.sort_order) || 'desc'
  const allowedSort = new Set(['created_at', 'sla_at', 'ranking_visitors', 'ranking_orders', 'decline_coef'])
  return {
    sort_by: allowedSort.has(sortBy) ? sortBy : 'created_at',
    sort_order: sortOrder === 'asc' ? 'asc' : 'desc',
  }
}

async function collectPagedItems<T>(
  pageSize: number,
  fetchPage: (page: number) => Promise<{ items?: T[]; total?: number }>,
): Promise<T[]> {
  const items: T[] = []
  for (let page = 1; page <= 200; page += 1) {
    const res = await fetchPage(page)
    const batch = Array.isArray(res?.items) ? res.items : []
    items.push(...batch)
    const total = Number(res?.total ?? items.length)
    if (batch.length === 0 || items.length >= total || batch.length < pageSize) break
  }
  return items
}

async function exportTodosCsv(): Promise<number> {
  const [dateFrom, dateTo] = queryDateRange('date_from', 'date_to')
  const sort = currentTodoSort()
  const pageSize = 100
  const items = await collectPagedItems<InboxTodoListItem>(pageSize, (page) => todoApi.list({
    status: readQueryText(route.query.status) || 'pending',
    kind: readQueryText(route.query.kind) || undefined,
    skill_id: readQueryText(route.query.skill_id) || undefined,
    decision_log_id: readQueryText(route.query.decision_log_id) || undefined,
    priority: readQueryText(route.query.priority) || undefined,
    assignee: readQueryText(route.query.assignee) || undefined,
    q: readQueryText(route.query.q) || readQueryText(route.query.search) || undefined,
    sort_by: sort.sort_by,
    sort_order: sort.sort_order,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    page,
    page_size: pageSize,
  }) as Promise<{ items?: InboxTodoListItem[]; total?: number }>)
  const headers = ['ID', '请求ID', '类型', '状态', '优先级', '标题', '摘要', 'Skill ID', 'Run ID', '接收人', '请求人', 'SLA', '创建时间', '更新时间']
  return downloadCsv(
    `inbox-todos-${timestampForFilename()}.csv`,
    headers,
    items.map((item) => [
      item.id,
      item.request_id,
      todoKindLabel(item.kind),
      todoStatusLabel(item.status),
      todoPriority(item),
      item.title,
      item.summary,
      item.skill_id,
      item.run_id,
      item.assignee,
      item.requester?.name || item.requester?.id,
      item.sla_at,
      item.created_at,
      item.updated_at,
    ]),
  )
}

async function exportReportsCsv(): Promise<number> {
  const [dateFrom, dateTo] = queryDateRange('report_date_from', 'report_date_to')
  const pageSize = 50
  const items = await collectPagedItems<ReportListItem>(pageSize, (page) => inboxApi.listReports({
    page,
    page_size: pageSize,
    skill_id: readQueryText(route.query.report_skill_id) || undefined,
    tag: readQueryText(route.query.report_tag) || undefined,
    channel: readQueryText(route.query.report_channel) || undefined,
    trigger_type: readQueryText(route.query.report_trigger_type) || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
  }) as Promise<{ items?: ReportListItem[]; total?: number }>)
  const headers = ['报告ID', 'Run ID', 'Skill ID', 'Skill', '部门', '渠道', '触发方式', '标题', '摘要', '标签', '关联待办', '待处理请求', '创建时间']
  return downloadCsv(
    `inbox-reports-${timestampForFilename()}.csv`,
    headers,
    items.map((item) => [
      item.id,
      item.run_id,
      item.skill_id,
      item.skill_name,
      item.skill_department,
      reportChannelLabel(item.channel),
      reportTriggerLabel(item.trigger_type),
      item.title,
      item.summary,
      item.tags,
      item.related_todo_count,
      item.related_pending_request_count,
      item.created_at,
    ]),
  )
}

async function exportDriftCsv(): Promise<number> {
  const pageSize = 100
  const items = await collectPagedItems<DriftAlertRow>(pageSize, (page) => driftAlertApi.list({
    platform: readQueryText(route.query.platform) || undefined,
    shop_id: readQueryText(route.query.shop_id) || undefined,
    page,
    page_size: pageSize,
  }))
  const headers = ['ID', '平台', '店铺', '变化类型', 'ACK', 'Endpoint', '缺失字段', '新增字段', 'Before Row Count', 'After Row Count', '发现时间', '关联待办', '处理结果']
  return downloadCsv(
    `inbox-drift-${timestampForFilename()}.csv`,
    headers,
    items.map((item) => [
      item.id,
      item.platform,
      item.shop_id,
      item.change_type || 'snapshot',
      driftAckLabel(item),
      item.endpoint,
      item.missing_fields,
      item.added_fields,
      item.previous_row_count,
      item.row_count,
      item.latest_snapshot_at || item.first_seen_at,
      item.related_todo_id || item.review_todo_id || item.todo_id,
      driftHandlingText(item),
    ]),
  )
}

function todoPriority(item: InboxTodoListItem): string {
  const titlePriority = String(item.title || '').match(/^(P[0-3])(?:[｜|\s]|$)/)?.[1]
  return item.priority || titlePriority || item.approval_level || ''
}

function driftAckLabel(item: DriftAlertRow): string {
  return item.acknowledged === true || item.ack_status === 'acked' || Boolean(item.acknowledged_at) ? '已 ACK' : '待 ACK'
}

function driftHandlingText(item: DriftAlertRow): string {
  const value = item.handling_result ?? item.result ?? item.handling_status ?? item.review_status
  if (!value) return '未处理'
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

function timestampForFilename(): string {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
}

function downloadCsv(filename: string, headers: string[], rows: unknown[][]): number {
  if (rows.length === 0) {
    Message.info('当前筛选没有可导出的数据')
    return 0
  }
  const lines = [headers, ...rows].map((row) => row.map(csvCell).join(','))
  const blob = new Blob([`\ufeff${lines.join('\r\n')}`], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
  return rows.length
}

function csvCell(value: unknown): string {
  let text = ''
  if (Array.isArray(value)) {
    text = value.map((item) => String(item ?? '')).filter(Boolean).join('; ')
  } else if (value && typeof value === 'object') {
    text = JSON.stringify(value)
  } else if (value != null) {
    text = String(value)
  }
  return `"${text.replace(/"/g, '""').replace(/\r?\n/g, ' ')}"`
}

function onClickMarkAllRead() {
  if (markAllReadDisabled.value) return
  // 复用现有 markReportsRead 端点；前端先乐观清红点，再异步同步服务端
  showUnreadDot.value = false
  unreadCount.value = 0
  triggerMarkReportsRead()
  Message.success('已将报告标记为已读')
}

// 移动端 toggle 按钮上展示的当前视图标签
const sidebarLabel = computed(() => {
  if (activeTab.value === 'reports') return '报告'
  if (activeTab.value === 'drift') return 'Drift / 漂移'
  switch (sidebarView.value) {
    case 'p0':
      return 'P0 紧急'
    case 'p1':
      return 'P1 优先'
    case 'mine':
      return '派给我'
    case 'done':
      return '我已处理'
    default:
      return '所有待办'
  }
})

// 同步 URL → sidebarView / sidebarSkillFilter（让外部链接 / 刷新也能命中正确高亮）
function syncSidebarFromRoute() {
  const tab = readQueryText(route.query.tab)
  if (tab === 'reports' || tab === 'drift') {
    // 报告 / Drift tab 时 sidebarView 不参与高亮
    return
  }
  const priority = readQueryText(route.query.priority)
  const status = readQueryText(route.query.status)
  const assignee = readQueryText(route.query.assignee)
  const skillId = readQueryText(route.query.skill_id)
  sidebarSkillFilter.value = skillId
  if (skillId) {
    sidebarView.value = 'all'
    return
  }
  if (priority === 'P0') {
    sidebarView.value = 'p0'
  } else if (priority === 'P1') {
    sidebarView.value = 'p1'
  } else if (assignee === 'me') {
    sidebarView.value = 'mine'
  } else if (status === 'done' || status === 'approved' || status === 'rejected' || status === 'resolved_by_peer') {
    sidebarView.value = 'done'
  } else {
    sidebarView.value = 'all'
  }
}

// 写 URL（统一通过 router.replace 让 TodosTab 通过 watch(route.query) 重新加载）
function pushQuery(patch: Record<string, string | undefined>) {
  const nextQuery: Record<string, string> = {}
  Object.entries({ ...route.query, ...patch }).forEach(([key, value]) => {
    const normalized = readQueryText(value)
    if (normalized) nextQuery[key] = normalized
  })
  router.replace({ path: '/inbox', query: nextQuery })
}

function onSidebarSelectView(view: SidebarView) {
  sidebarView.value = view
  sidebarSkillFilter.value = ''
  const patch: Record<string, string | undefined> = {
    tab: 'pending',
    skill_id: undefined,
    page: undefined,
  }
  if (view === 'p0' || view === 'p1') {
    patch.priority = view === 'p0' ? 'P0' : 'P1'
    patch.status = 'pending'
    patch.assignee = undefined
  } else if (view === 'done') {
    patch.priority = undefined
    patch.status = 'done'
    patch.assignee = undefined
  } else if (view === 'mine') {
    // "派给我"：显式带 assignee=me，让后端按当前用户过滤
    patch.priority = undefined
    patch.status = 'pending'
    patch.assignee = 'me'
  } else {
    // all：清掉所有筛选
    patch.priority = undefined
    patch.status = 'pending'
    patch.assignee = undefined
  }
  pushQuery(patch)
  sidebarOpen.value = false
}

function onSidebarSelectTab(tab: 'reports' | 'drift') {
  pushQuery({ tab })
  sidebarOpen.value = false
}

function onSidebarSelectSkill(skillId: string) {
  // 再次点击已选 Skill 时清除筛选
  const next = sidebarSkillFilter.value === skillId ? '' : skillId
  sidebarSkillFilter.value = next
  sidebarView.value = 'all'
  pushQuery({
    tab: 'pending',
    skill_id: next || undefined,
    status: 'pending',
    priority: undefined,
    page: undefined,
  })
  sidebarOpen.value = false
}

async function refreshOverview() {
  try {
    overview.value = (await inboxApi.overview()) as InboxOverviewResponse
  } catch {
    overview.value = null // 请求失败 fallback 到 stats，不阻塞页面
  }
}

function syncFromRoute() {
  const tab = readQueryText(route.query.tab)
  const type = readQueryText(route.query.type)
  activeTab.value = tab === 'reports' ? 'reports' : (tab === 'drift' || type === 'drift' ? 'drift' : 'pending')
  syncSidebarFromRoute()
}

async function refreshLatestReportState() {
  // GAP-5：完全以服务端 unread-count 为准；不再读 localStorage。
  // v2.0.17 H6：requestId 守卫防乱序——旧响应迟到时不覆盖新状态
  const thisReqId = ++unreadReqId
  try {
    const unread: any = await inboxApi.getUnreadCount()
    if (thisReqId !== unreadReqId) return // 已有更新的请求发出，放弃过期结果
    const count = Number(unread?.unread ?? 0)
    unreadCount.value = Number.isFinite(count) ? count : null
    showUnreadDot.value = activeTab.value !== 'reports' && (unreadCount.value ?? 0) > 0
  } catch (error) {
    if (thisReqId !== unreadReqId) return
    // 服务端 404/500：unreadCount 显示 "—"（即 null），红点不强制翻转
    // eslint-disable-next-line no-console
    console.warn('[inbox] getUnreadCount failed, showing unknown state', error)
    unreadCount.value = null
  }
}

function triggerMarkReportsRead() {
  // v2.0.17 H6：取消上一次还在飞的 mark-read，避免响应乱序把红点抖回来
  if (markReadAbort) markReadAbort.abort()
  markReadAbort = new AbortController()
  const signal = markReadAbort.signal
  // inboxApi.markReportsRead 不支持 signal 时忽略；这里用 signal 只是标识"最新意图"
  // 的 sentinel——下一次 abort 调用会让本次结果即使到达也不用了（下面的 then 守卫）
  inboxApi
    .markReportsRead()
    .then(() => {
      if (signal.aborted) return
      // 成功后刷一次 unread 状态，服务端精确值（0）会压制本地乐观 0
      void refreshLatestReportState()
    })
    .catch(() => {
      if (signal.aborted) return
      // 失败不管——红点本地已乐观重置为 0，不强制回滚
    })
}

function handleLatestReportChange(_createdAt: string) {
  // 列表抛出最新报告时间戳，红点状态完全交给 refreshLatestReportState 计算
  // 这里仅在 reports tab 重新激活后做一次刷新，确保切换 tab 时数字最新
  if (activeTab.value !== 'reports') {
    void refreshLatestReportState()
  }
}

function onTabChange(nextKey: string | number) {
  // v2.0.17 H6：此函数只管 URL 同步；mark-read 逻辑下沉到 watch(activeTab)，
  // 单一源头避免两处都发请求。
  const nextTab = nextKey === 'reports' ? 'reports' : (nextKey === 'drift' ? 'drift' : 'pending')
  activeTab.value = nextTab
  const nextQuery = { ...route.query, tab: nextTab }
  router.replace({ path: '/inbox', query: nextQuery })
}

async function initializeFromRoute() {
  syncFromRoute()
  await refreshLatestReportState()
  void nextTick(() => {
    void refreshOverview()
    scheduleSidebarLoad()
  })
}

// 路由变化只做 tab 同步（红点状态由 activeTab watch 负责）
watch(() => route.query, syncFromRoute, { deep: true })

watch(activeTab, (nextTab, prev) => {
  // v2.0.17 H6：唯一触发 mark-read 的地方。prev 为 undefined 的首次运行不触发，
  // 避免页面冷启动就给服务端写一次"空点击"——由 initializeFromRoute 决定首次行为。
  if (nextTab === prev) return
  if (nextTab === 'reports') {
    // UI 先乐观——前端立即清红点
    showUnreadDot.value = false
    unreadCount.value = 0
    // 服务端最新未读数 + mark-read；两个请求都有去重守卫
    triggerMarkReportsRead()
  } else {
    // 切回 pending 时刷新 unreadCount，避免后台又来了新报告但红点未亮
    void refreshLatestReportState()
  }
})

onMounted(() => {
  initializeFromRoute()
})

function scheduleSidebarLoad() {
  if (sidebarLoadScheduled) return
  sidebarLoadScheduled = true
  const run = () => {
    sidebarLoadTimer = null
    void Promise.allSettled([loadSidebarTopSkills(), loadSidebarCounts()])
  }
  const idle = (window as unknown as { requestIdleCallback?: (cb: () => void, options?: { timeout: number }) => number }).requestIdleCallback
  if (typeof idle === 'function') {
    idle(run, { timeout: 1500 })
    return
  }
  sidebarLoadTimer = setTimeout(run, 800)
}

onBeforeUnmount(() => {
  // 卸载时取消飞行请求，避免内存泄漏
  if (markReadAbort) {
    markReadAbort.abort()
    markReadAbort = null
  }
  if (sidebarLoadTimer) {
    clearTimeout(sidebarLoadTimer)
    sidebarLoadTimer = null
  }
})
</script>

<style scoped>
.inbox-center {
  max-width: none !important;
  padding: 0 !important;
  display: flex;
  flex-direction: row;
  gap: 0;
  align-items: stretch;
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
  background: var(--ai-bg);
  min-height: calc(100vh - 52px); /* 设计稿 Screen 主体：扣掉顶部一级导航 */
}

/* 左侧 ai-sidebar（设计稿 200px 固定） */
.inbox-sidebar {
  position: sticky;
  top: 0;
  align-self: flex-start;
  width: 200px;
  flex: 0 0 200px;
  height: calc(100vh - 52px);
  max-height: calc(100vh - 52px);
  border-right: 1px solid var(--ai-border);
  background: var(--ai-surface);
  padding: 16px 12px;
  overflow-y: auto;
}

.inbox-sidebar-head {
  display: none;
  align-items: center;
  justify-content: space-between;
  padding: 0 4px 6px;
}
.inbox-sidebar-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}
.inbox-sidebar-close {
  display: none;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 0;
  cursor: pointer;
  color: var(--ai-ink-4);
  padding: 4px;
}
.inbox-sidebar-close:hover {
  color: var(--ai-ink-1);
}
.inbox-sidebar-toggle {
  display: none;
  align-items: center;
  gap: 8px;
  margin: 12px 16px 0;
  padding: 0 12px;
  height: 30px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ai-ink-1);
  cursor: pointer;
  font-family: var(--ai-font-sans);
  align-self: flex-start;
}
.inbox-sidebar-toggle .ic,
.inbox-sidebar-close .ic {
  width: 13px;
  height: 13px;
  flex: 0 0 13px;
}

.inbox-sidebar .ai-side-group {
  margin-bottom: 18px;
}
.inbox-sidebar .ai-side-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-4);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 0 8px 6px;
}
.inbox-sidebar .ai-side-item {
  display: flex;
  align-items: center;
  gap: 7px;
  height: 28px;
  padding: 0 7px;
  border-radius: 5px;
  font-size: 12px;
  font-weight: 500;
  line-height: 1;
  color: var(--ai-ink-2);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.inbox-sidebar .ai-side-item .ic {
  color: var(--ai-ink-4);
  width: 13px;
  height: 13px;
  flex: 0 0 13px;
}
.inbox-sidebar .ai-side-item > span:not(.count) {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1;
}
.inbox-sidebar .ai-side-item:hover {
  background: var(--ai-surface-2);
}
.inbox-sidebar .ai-side-item.active {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
  font-weight: 500;
}
.inbox-sidebar .ai-side-item.active .ic {
  color: var(--ai-ink-1);
}
.inbox-sidebar .ai-side-item .count {
  margin-left: auto;
  min-width: 20px;
  height: 16px;
  padding: 0 5px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
  color: var(--ai-ink-4);
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
  flex: 0 0 auto;
}
.inbox-sidebar .ai-side-item .count.count-bad {
  color: var(--ai-bad);
  background: color-mix(in srgb, var(--ai-bad) 8%, var(--ai-surface));
  border-color: color-mix(in srgb, var(--ai-bad) 22%, var(--ai-border));
}

/* 主区 */
.inbox-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
  padding: 0;
  overflow: hidden;
  max-width: none;
}

@media (max-width: 768px) {
  .inbox-center {
    flex-direction: column;
  }
  .inbox-sidebar {
    position: fixed;
    top: 0;
    left: 0;
    height: 100vh;
    width: min(280px, 80vw);
    max-height: none;
    z-index: 200;
    flex: 0 0 auto;
    border-right: 1px solid var(--ai-border);
    border-bottom: 0;
    transform: translateX(-110%);
    transition: transform 0.2s ease;
    box-shadow: var(--ai-shadow-2);
  }
  .inbox-sidebar--open {
    transform: translateX(0);
  }
  .inbox-sidebar-head {
    display: flex;
  }
  .inbox-sidebar-close {
    display: inline-flex;
  }
  .inbox-sidebar-toggle {
    display: inline-flex;
  }
}

.inbox-pagehead {
  align-items: flex-end;
}

.inbox-pagehead-top {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  width: 100%;
}

.inbox-top-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex: 0 0 auto;
  min-width: 0;
  padding-top: 2px;
}

.inbox-top-actions .ai-btn {
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: auto;
  min-width: max-content;
  white-space: nowrap;
  line-height: 1;
}

.inbox-top-actions .ai-btn :deep(svg) {
  width: 13px;
  height: 13px;
  flex: 0 0 13px;
}

.inbox-top-actions .ai-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.inbox-tab-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.inbox-tab-pill {
  font-size: 10px;
  height: 16px;
  padding: 0 5px;
  font-weight: 500;
}

.inbox-tab {
  border: 0;
  background: transparent;
  cursor: pointer;
  font-family: var(--ai-font-sans);
}
.inbox-body {
  min-height: 0;
  overflow: auto;
}

.report-tab-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.report-tab-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ai-bad);
  box-shadow: none;
}

@media (max-width: 720px) {
  .inbox-pagehead {
    padding: 16px;
  }

  .inbox-pagehead-top {
    flex-direction: column;
    align-items: stretch;
  }

  .inbox-top-actions {
    justify-content: flex-start;
    flex-wrap: wrap;
    gap: 8px;
  }

  .inbox-tabs {
    overflow-x: auto;
  }

  .inbox-body {
    padding: 16px;
  }
}
</style>
