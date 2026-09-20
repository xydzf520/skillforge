<template>
  <div class="inbox-tab">
    <div class="filter-bar">
      <div class="filter-controls">
        <a-input-search
          v-model="searchText"
          class="todo-search"
          size="small"
          allow-clear
          placeholder="标题 / 商品ID / 优先级 / 派发或反馈内容"
          search-button
          @search="applyFilters"
          @press-enter="applyFilters"
          @clear="onSearchClear"
        />
        <a-select v-model="kindFilter" style="width: 100px" size="small" allow-clear placeholder="类型" @change="applyFilters">
          <a-option value="review">审核</a-option>
          <a-option value="dispatch">派发</a-option>
        </a-select>
        <a-select v-model="statusFilter" style="width: 110px" size="small" allow-clear placeholder="状态" @change="onStatusChange">
          <a-option value="pending">待处理</a-option>
          <a-option value="approved">已派发</a-option>
          <a-option value="rejected">已驳回</a-option>
          <a-option value="done">已处理</a-option>
          <a-option value="expired">已过期</a-option>
          <a-option value="resolved_by_peer">同事已处理</a-option>
          <a-option value="feedback_done">已反馈</a-option>
        </a-select>
        <a-select v-model="priorityFilter" style="width: 100px" size="small" allow-clear placeholder="优先级" @change="applyFilters">
          <a-option value="P0">P0</a-option>
          <a-option value="P1">P1</a-option>
          <a-option value="P2">P2</a-option>
        </a-select>
        <a-range-picker
          v-model="dateRange"
          value-format="YYYY-MM-DDTHH:mm:ss"
          size="small"
          style="min-width: 200px; max-width: 260px;"
          @change="onDateRangeChange"
        />
        <span class="filter-spacer" />
        <button type="button" class="ai-btn sm" @click="refreshAll">刷新</button>
      </div>
      <!-- 设计稿 inbox.jsx 右侧 quick filter pills -->
      <div class="filter-quick-pills">
        <span
          v-if="decisionLogFilter"
          class="ai-pill filter-quick-pill active"
          title="当前仅展示这份报告关联的待办"
          @click="clearReportFilter"
        >报告关联 #{{ decisionLogFilter }} ×</span>
        <span
          class="ai-pill filter-quick-pill"
          :class="{ active: (!statusFilter || statusFilter === 'pending') && !assigneeFilter && !slaStateFilter }"
          @click="quickFilter({ status: 'pending', kind: '', priority: '', assignee: '' })"
          title="筛选所有待处理"
        >待处理 {{ stats.pending ?? 0 }}</span>
        <span
          class="ai-pill filter-quick-pill"
          :class="{ active: assigneeFilter === 'me' }"
          @click="quickFilter({ status: 'pending', kind: '', priority: '', assignee: 'me' })"
          title="仅展示派给我的待办"
        >派给我</span>
        <span
          class="ai-pill filter-quick-pill warn"
          :class="{ active: slaStateFilter === 'due_soon' }"
          @click="quickFilter({ status: 'pending', kind: '', priority: '', assignee: '', sla_state: 'due_soon', sort: 'sla_at:asc' })"
          title="24 小时内到期或已经逾期的待处理待办"
        >即将逾期 {{ stats.due_soon ?? 0 }}</span>
      </div>
    </div>

    <TodoBulkBar
      :selected-count="selectedIds.size"
      :total-count="selectableItems.length"
      :loading="bulkLoading"
      :can-decide="anySelectedPending"
      @clear="clearSelection"
      @toggle-all="onToggleAll"
      @batch-decide="onBatchDecide"
      @batch-extend-sla="onBatchExtendSla"
      @batch-reassign="onBatchReassign"
    />

    <a-spin :loading="loading" style="width: 100%">
      <div v-if="items.length > 0" class="todo-grid">
        <!-- 设计稿 inbox.jsx 的列头：与 TodoCard dense 列宽对齐 -->
        <div class="todo-grid-header" aria-hidden="true">
          <span class="col-select"></span>
          <span class="col-prio">优先</span>
          <span class="col-main">待办</span>
          <span class="col-metric">关键指标</span>
          <span class="col-skill">来源 Skill</span>
          <span class="col-sla">SLA</span>
          <span class="col-actions">操作</span>
        </div>
        <div
          v-for="item in items"
          :key="item.id"
          class="todo-grid-cell"
          :class="{ 'is-selected': selectedIds.has(item.id) }"
        >
          <label class="todo-select-cell">
            <a-checkbox
              :model-value="selectedIds.has(item.id)"
              @change="(v: boolean | (string | number | boolean)[]) => onToggleOne(item.id, Boolean(v))"
            />
          </label>
          <TodoCard
            :item="item"
            :dense="true"
            @open="openTodo"
            @open-v2="openTodoV2"
            @decide="decide"
            @open-report="openReport"
          />
        </div>
      </div>
      <SfEmptyState
        v-if="!loading && items.length === 0"
        icon="inbox"
        title="暂无待办"
        description="当前筛选下没有需要处理的待办"
        hint="可切换左侧视图、清除筛选条件，或稍后等待 Skill 产生新待办。"
      />
    </a-spin>

    <!-- 对齐 inbox.jsx 底部：左侧批量提示 + 右侧"共 X 条·显示 Y/X"和翻页。
         快速切换状态 chip 已经在顶部 filter row（待处理/派发任务/即将逾期）提供，
         底部不再重复一组 stat-chip。 -->
    <div class="footer-bar">
      <span class="footer-hint">选择多个待办可批量「通过并派发」给同一负责人</span>
      <div class="footer-right">
        <span class="footer-count">共 {{ pagination.total }} 条 · 显示 {{ items.length }} / {{ pagination.total }}</span>
        <a-pagination
          v-if="pagination.total > pagination.pageSize"
          :current="pagination.current"
          :page-size="pagination.pageSize"
          :total="pagination.total"
          @change="onPageChange"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, h, ref, watch } from 'vue'
import { Message, Modal, Option as AOption, Select as ASelect, Tag as ATag } from '@arco-design/web-vue'
import { useRoute, useRouter } from 'vue-router'
import TodoCard from './TodoCard.vue'
import TodoBulkBar from './components/TodoBulkBar.vue'
import { SfEmptyState } from '@/components/common'
import { todoApi } from '@/api'
import { defaultInboxDateRange, readQueryPositiveInt, readQueryText } from './presentation'
import type { InboxDispatchTask, InboxTodoDetailResponse, InboxTodoListItem, InboxTodoStats } from '@/types/inbox'

type StatChip = {
  key: string
  label: string
  count: number
  filter: { status?: string; kind?: string }
  tooltip?: string
}

type AssignableUser = {
  id: string
  name?: string
  role?: string
  department?: string
  dingtalk_bound?: boolean
}

const props = defineProps({
  active: { type: Boolean, default: false },
})

const emit = defineEmits<{
  (event: 'stats-change', stats: InboxTodoStats): void
}>()

const route: any = useRoute()
const router: any = useRouter()

const loading = ref(false)
const items = ref<InboxTodoListItem[]>([])
const stats = ref<InboxTodoStats>({})
const statsLoaded = ref(false)
const statusFilter = ref('pending')
const kindFilter = ref('')
const priorityFilter = ref('')
const skillFilter = ref('')
const decisionLogFilter = ref('')
const runFilter = ref('')
const assigneeFilter = ref('')
const slaStateFilter = ref('')
const searchText = ref('')
const explicitStatusFilter = ref(false)
const sortValue = ref('created_at:desc')
function defaultDateRange(): string[] {
  return defaultInboxDateRange(7)
}
const dateRange = ref<string[]>(defaultDateRange())
const pagination = ref({ current: 1, pageSize: 20, total: 0 })

function normalizeDateRange(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => String(item || '').trim()).filter(Boolean)
}

// GAP-7/8/9：多选 & 批量操作状态
const selectedIds = ref<Set<number>>(new Set())
const bulkLoading = ref(false)
const selectableItems = computed(() => items.value)
const anySelectedPending = computed(() =>
  items.value.some((it) => selectedIds.value.has(it.id) && it.status === 'pending')
)

function clearSelection() {
  selectedIds.value = new Set()
}
function onToggleOne(id: number, checked: boolean) {
  const next = new Set(selectedIds.value)
  if (checked) next.add(id)
  else next.delete(id)
  selectedIds.value = next
}
function onToggleAll(checked: boolean) {
  if (!checked) {
    selectedIds.value = new Set()
    return
  }
  selectedIds.value = new Set(items.value.map((it) => it.id))
}

function syncFromRoute() {
  const routeStatus = readQueryText(route.query.status)
  const routeSearch = readQueryText(route.query.q) || readQueryText(route.query.search)
  explicitStatusFilter.value = Boolean(routeStatus)
  statusFilter.value = routeStatus || (routeSearch ? '' : 'pending')
  kindFilter.value = readQueryText(route.query.kind)
  priorityFilter.value = readQueryText(route.query.priority)
  skillFilter.value = readQueryText(route.query.skill_id)
  decisionLogFilter.value = readQueryText(route.query.decision_log_id)
  runFilter.value = readQueryText(route.query.run_id)
  assigneeFilter.value = readQueryText(route.query.assignee)
  slaStateFilter.value = readQueryText(route.query.sla_state)
  searchText.value = routeSearch
  sortValue.value = readQueryText(route.query.sort) || sortFromQuery()
  pagination.value.current = readQueryPositiveInt(route.query.page, 1)
  const from = readQueryText(route.query.date_from)
  const to = readQueryText(route.query.date_to)
  dateRange.value = from && to ? [from, to] : defaultDateRange()
}

function sortFromQuery(): string {
  const sortBy = readQueryText(route.query.sort_by) || 'created_at'
  const sortOrder = readQueryText(route.query.sort_order) || 'desc'
  const allowed = new Set(['created_at:desc', 'sla_at:asc', 'ranking_visitors:asc', 'ranking_orders:asc', 'decline_coef:desc'])
  const value = `${sortBy}:${sortOrder}`
  return allowed.has(value) ? value : 'created_at:desc'
}

function parsedSort(): { sort_by: string; sort_order: string } {
  const [sort_by, sort_order] = String(sortValue.value || 'created_at:desc').split(':')
  return {
    sort_by: sort_by || 'created_at',
    sort_order: sort_order === 'asc' ? 'asc' : 'desc',
  }
}

async function loadStats(force = false) {
  if (statsLoaded.value && !force) return
  const nextStats = await todoApi.stats() as any
  stats.value = nextStats || {}
  statsLoaded.value = true
  emit('stats-change', stats.value)
}

async function loadList() {
  loading.value = true
  try {
    const sort = parsedSort()
    const list = await todoApi.list({
      status: statusFilter.value || undefined,
      kind: kindFilter.value || undefined,
      skill_id: skillFilter.value || undefined,
      decision_log_id: decisionLogFilter.value || undefined,
      run_id: runFilter.value || undefined,
      priority: priorityFilter.value || undefined,
      assignee: assigneeFilter.value || undefined,
      sla_state: slaStateFilter.value || undefined,
      q: searchText.value || undefined,
      sort_by: sort.sort_by,
      sort_order: sort.sort_order,
      date_from: dateRange.value[0] || undefined,
      date_to: dateRange.value[1] || undefined,
      page: pagination.value.current,
      page_size: pagination.value.pageSize,
    }) as any
    items.value = list.items || []
    pagination.value.total = list.total || 0
  } catch (error: any) {
    Message.error(error._message || '加载待办失败')
  } finally {
    loading.value = false
  }
}

async function loadAll(options: { refreshStats?: boolean } = {}) {
  await Promise.all([
    loadList(),
    loadStats(Boolean(options.refreshStats)),
  ])
}

function refreshAll() {
  void loadAll({ refreshStats: true })
}

function assignableUserLabel(user: AssignableUser): string {
  const parts = [
    user.name || user.id,
    user.department || '',
    user.role || '',
    user.dingtalk_bound ? '已绑钉钉' : '未绑钉钉',
  ].filter(Boolean)
  return parts.join(' · ')
}

function assignableDepartmentLabel(users: AssignableUser[]): string {
  const departments = Array.from(new Set(users.map((user) => String(user.department || '').trim()).filter(Boolean)))
  if (!departments.length) return '当前部门'
  if (departments.length === 1) return departments[0]
  return `${departments[0]} 等 ${departments.length} 个部门`
}

function isAssignableExecutor(users: AssignableUser[], executorId?: string | null): boolean {
  const id = String(executorId || '').trim()
  return !!id && users.some((user) => user.id === id)
}

function missingDispatchExecutors(tasks: InboxDispatchTask[]): InboxDispatchTask[] {
  return tasks.filter((task) => !String(task.executor || '').trim() && !['done', 'cancelled'].includes(String(task.status || '')))
}

function actionableDispatchTasks(tasks: InboxDispatchTask[]): InboxDispatchTask[] {
  return tasks.filter((task) => !['done', 'cancelled'].includes(String(task.status || '')))
}

function requiresDispatchSelection(sourceType: unknown): boolean {
  return String(sourceType || '').startsWith('skill_execution_')
}

function approvalDispatchTasks(detail: InboxTodoDetailResponse): InboxDispatchTask[] {
  const tasks = actionableDispatchTasks(detail.dispatch_tasks || [])
  if (requiresDispatchSelection(detail.request?.source_type)) return tasks
  return missingDispatchExecutors(tasks)
}

function normalizeAssignableUsers(rows: unknown): AssignableUser[] {
  if (!Array.isArray(rows)) return []
  return rows.map((item) => {
    const record = item && typeof item === 'object' ? item as Record<string, unknown> : {}
    return {
      id: String(record.id || ''),
      name: String(record.name || ''),
      role: String(record.role || ''),
      department: String(record.department || ''),
      dingtalk_bound: Boolean(record.dingtalk_bound),
    }
  }).filter((item) => item.id)
}

async function ensureDispatchExecutors(item: InboxTodoListItem): Promise<boolean> {
  if (item.kind !== 'dispatch') return true
  const detail = await todoApi.get(item.id) as InboxTodoDetailResponse
  const confirmAllTasks = requiresDispatchSelection(detail.request?.source_type)
  const candidateTasks = approvalDispatchTasks(detail)
  if (!candidateTasks.length) return true

  const requestId = detail.request?.id || item.request_id
  const assignableUsers = normalizeAssignableUsers(
    requestId ? await todoApi.listAssignableUsers({ request_id: requestId }) : [],
  )
  if (!assignableUsers.length) {
    Message.warning(
      confirmAllTasks
        ? '当前部门组织架构下没有可用执行人，无法确认派发对象'
        : '当前部门组织架构下没有可用执行人，请先补充成员归属',
    )
    return false
  }

  const selections = ref<Record<number, string>>(
    Object.fromEntries(
      candidateTasks.map((task) => {
        const assigned = String(task.executor || '').trim()
        const fallback = String(task.default_executor || '').trim()
        const preset = isAssignableExecutor(assignableUsers, assigned)
          ? assigned
          : (isAssignableExecutor(assignableUsers, fallback) ? fallback : '')
        return [task.id, preset || (assignableUsers.length === 1 ? assignableUsers[0].id : '')]
      }),
    ),
  )

  return new Promise((resolve) => {
    Modal.open({
      title: confirmAllTasks ? '确认派发执行人' : '选择当前部门执行人',
      okText: confirmAllTasks ? '确认并通过' : '保存并通过',
      cancelText: '取消',
      width: 680,
      content: () => h('div', { class: 'dispatch-approve-modal' }, [
        h('div', { class: 'dispatch-approve-head' }, [
          h('p', { class: 'dispatch-approve-tip' }, confirmAllTasks
            ? 'Skill 生成的派发任务需由平台确认执行人后才能通过。'
            : '通过前请从当前部门组织架构中选择执行人。'),
          h('div', { class: 'dispatch-approve-meta' }, [
            h(ATag, { size: 'small', color: 'arcoblue' }, () => assignableDepartmentLabel(assignableUsers)),
            h(ATag, { size: 'small', color: 'green' }, () => `可选 ${assignableUsers.length} 人`),
          ]),
        ]),
        ...candidateTasks.map((task, index) => h('div', { key: task.id, class: 'dispatch-approve-row' }, [
          h('div', { class: 'dispatch-approve-summary' }, [
            h('strong', `任务 ${index + 1}`),
            h('span', String(task.content || '').slice(0, 120) || '未填写任务内容'),
          ]),
          h(ASelect, {
            class: 'dispatch-approve-select',
            modelValue: selections.value[task.id] || undefined,
            placeholder: '选择本部门成员',
            allowSearch: true,
            onChange: (value: any) => {
              selections.value = {
                ...selections.value,
                [task.id]: String(value || ''),
              }
            },
          }, {
            default: () => assignableUsers.map((user) => h(
              AOption,
              { key: user.id, value: user.id },
              () => assignableUserLabel(user),
            )),
          }),
        ])),
      ]),
      onBeforeOk: async () => {
        const missingSelection = candidateTasks.find((task) => !String(selections.value[task.id] || '').trim())
        if (missingSelection) {
          Message.warning(confirmAllTasks ? '请先确认每条派发任务的执行人' : '请先为每条派发任务选择执行人')
          return false
        }
        try {
          await todoApi.updateDraft(item.id, {
            dispatch_tasks: candidateTasks.map((task) => ({
              id: task.id,
              executor: selections.value[task.id],
            })),
          })
          resolve(true)
          return true
        } catch (error: any) {
          Message.error(error?._message || '保存执行人失败')
          return false
        }
      },
      onCancel: () => resolve(false),
    })
  })
}

async function doDecide(id: number, decision: 'approved' | 'rejected') {
  try {
    const item = items.value.find((candidate) => candidate.id === id)
    if (decision === 'approved' && item && !(await ensureDispatchExecutors(item))) return
    await todoApi.decide(id, { decision, reason: '' })
    Message.success(decision === 'approved' ? '已通过' : '已驳回')
    await loadAll({ refreshStats: true })
  } catch (error: any) {
    Message.error(error._message || '操作失败')
  }
}

function decide(id: number, decision: 'approved' | 'rejected') {
  if (decision === 'rejected') {
    Modal.confirm({
      title: '确认驳回',
      content: '驳回后将通知发起人，是否继续？',
      onOk: () => doDecide(id, decision),
    })
    return
  }
  doDecide(id, decision)
}

// v2.0.17 H8：批量操作部分失败时，只保留失败项在选择集，让用户一键重试
// 失败的那部分，无需重新勾选。完全成功才 clearSelection。
function _retainFailedSelection(result: any, allIds: number[]) {
  const failedIds = new Set<number>(
    (result?.results || [])
      .filter((r: any) => r && r.ok === false)
      .map((r: any) => Number(r.todo_id))
      .filter((n: number) => Number.isFinite(n)),
  )
  if (failedIds.size === 0) {
    clearSelection()
  } else {
    // 只保留失败项——成功 + 跳过的都从选择集移除
    selectedIds.value = new Set(allIds.filter((id) => failedIds.has(id)))
  }
}

async function onBatchDecide(decision: 'approved' | 'rejected') {
  const ids = Array.from(selectedIds.value)
  if (!ids.length) return
  bulkLoading.value = true
  try {
    const result: any = await todoApi.batchDecide(ids, decision)
    const succeeded = result?.succeeded ?? 0
    const failed = result?.failed ?? 0
    if (failed === 0) {
      Message.success(`已处理 ${succeeded}/${result?.total ?? ids.length} 条`)
    } else {
      const firstErr = (result?.results || []).find((r: any) => !r?.ok)
      Message.warning(
        `${succeeded} 成功 / ${failed} 失败（${firstErr?.error || '未知原因'}）— 失败项已保留，可重试`,
      )
    }
    _retainFailedSelection(result, ids)
    await loadAll({ refreshStats: true })
  } catch (error: any) {
    // 网络层异常：完全不知道后端实际有没有处理 —— 保留选择集不动
    Message.error(`${error?._message || '批量处理失败'} — 选择集保留，可重试`)
  } finally {
    bulkLoading.value = false
  }
}

async function onBatchExtendSla(hours: number) {
  const ids = Array.from(selectedIds.value)
  if (!ids.length) return
  bulkLoading.value = true
  try {
    const result: any = await todoApi.batchExtendSla(ids, hours)
    const succeeded = result?.succeeded ?? 0
    const failed = result?.failed ?? 0
    if (failed === 0) {
      Message.success(`已延期 ${succeeded}/${result?.total ?? ids.length} 条 · +${hours}h`)
    } else {
      const firstErr = (result?.results || []).find((r: any) => !r?.ok)
      Message.warning(
        `${succeeded} 成功 / ${failed} 失败（${firstErr?.error || '未知原因'}）— 失败项已保留，可重试`,
      )
    }
    _retainFailedSelection(result, ids)
    await loadAll({ refreshStats: true })
  } catch (error: any) {
    Message.error(`${error?._message || '批量延期失败'} — 选择集保留，可重试`)
  } finally {
    bulkLoading.value = false
  }
}

async function onBatchReassign({ toUserId, reason }: { toUserId: string; reason: string }) {
  const ids = Array.from(selectedIds.value)
  if (!ids.length || !toUserId) return
  bulkLoading.value = true
  try {
    const result: any = await todoApi.batchReassign(ids, toUserId, reason)
    if ((result?.succeeded ?? 0) > 0) {
      Message.success(`已转派 ${result.succeeded}/${result.total} 条给 ${toUserId}`)
    }
    if ((result?.failed ?? 0) > 0) {
      const firstErr = result.results?.find((r: any) => !r.ok)
      Message.warning(
        `${result.failed} 条转派失败（${firstErr?.error || '未知原因'}）— 失败项已保留，可重试`,
      )
    }
    _retainFailedSelection(result, ids)
    await loadAll({ refreshStats: true })
  } catch (error: any) {
    Message.error(`${error?._message || '批量转派失败'} — 选择集保留，可重试`)
  } finally {
    bulkLoading.value = false
  }
}

function openTodo(id: number) {
  router.push(`/inbox/todos/${id}`)
}

function openTodoV2(id: number) {
  router.push(`/inbox/todos/${id}/v2`)
}

function openReport(reportId: string) {
  router.push(`/inbox/reports/${encodeURIComponent(reportId)}`)
}

function replaceQuery(patch: Record<string, string | undefined>) {
  const nextQuery: Record<string, string> = {}
  Object.entries({ ...route.query, ...patch, tab: 'pending' }).forEach(([key, value]) => {
    const normalized = readQueryText(value)
    if (normalized) nextQuery[key] = normalized
  })
  router.replace({ path: '/inbox', query: nextQuery })
}

function applyFilters() {
  const hasSearch = Boolean(searchText.value.trim())
  if (hasSearch && statusFilter.value === 'pending' && !explicitStatusFilter.value) {
    statusFilter.value = ''
  }
  replaceQuery({
    page: undefined,
    status: statusFilter.value || undefined,
    kind: kindFilter.value || undefined,
    skill_id: skillFilter.value || undefined,
    decision_log_id: decisionLogFilter.value || undefined,
    run_id: runFilter.value || undefined,
    priority: priorityFilter.value || undefined,
    assignee: assigneeFilter.value || undefined,
    sla_state: slaStateFilter.value || undefined,
    q: searchText.value || undefined,
    sort: sortValue.value === 'created_at:desc' ? undefined : sortValue.value,
    sort_by: undefined,
    sort_order: undefined,
    date_from: dateRange.value[0] || undefined,
    date_to: dateRange.value[1] || undefined,
  })
}

function clearReportFilter() {
  decisionLogFilter.value = ''
  replaceQuery({ decision_log_id: undefined, page: undefined })
}

function onStatusChange() {
  explicitStatusFilter.value = true
  applyFilters()
}

function onSearchClear() {
  searchText.value = ''
  if (!explicitStatusFilter.value && !statusFilter.value) {
    statusFilter.value = 'pending'
  }
  applyFilters()
}

function onDateRangeChange(value: unknown) {
  dateRange.value = normalizeDateRange(value)
  applyFilters()
}

function quickFilter(next: {
  status?: string
  kind?: string
  priority?: string
  assignee?: string
  sla_state?: string
  sort?: string
}) {
  if ('status' in next) statusFilter.value = next.status ?? ''
  if ('kind' in next) kindFilter.value = next.kind ?? ''
  if ('priority' in next) priorityFilter.value = next.priority ?? ''
  if ('assignee' in next) assigneeFilter.value = next.assignee ?? ''
  if ('sla_state' in next) {
    slaStateFilter.value = next.sla_state ?? ''
  } else if ('status' in next || 'kind' in next || 'priority' in next || 'assignee' in next) {
    slaStateFilter.value = ''
  }
  sortValue.value = next.sort || 'created_at:desc'
  applyFilters()
}

// 去掉「待处理」chip（和顶部的 status 筛选语义重复），只留快速切换到其它状态的入口。
// tooltip 统一说明"统计口径"，避免用户被 0 或极端比率误导
const statChips = computed<StatChip[]>(() => [
  {
    key: 'dispatch', label: '执行中', count: stats.value.dispatch_pending || 0,
    filter: { kind: 'dispatch', status: '' },
    tooltip: '正在执行或等待被执行的派发任务（按当前用户过滤）',
  },
  {
    key: 'feedback', label: '已反馈', count: stats.value.feedback_done || 0,
    filter: { kind: 'dispatch', status: 'feedback_done' },
    tooltip: '派发给执行人的任务已完成反馈，按当前用户名下主待办过滤',
  },
  {
    key: 'approved', label: '已派发', count: stats.value.approved || 0,
    filter: { status: 'approved' },
    tooltip: '最近 30 天你处理过并进入派发/执行链路的待办（按时间窗聚合，不含已过期）',
  },
  {
    key: 'rejected', label: '已驳回', count: stats.value.rejected || 0,
    filter: { status: 'rejected' },
    tooltip: '最近 30 天你处理过的审批：驳回数（含"驳回重做"和"直接驳回"）',
  },
  {
    key: 'expired', label: '已过期', count: stats.value.expired || 0,
    filter: { status: 'expired' },
    tooltip: 'SLA 超时后自动过期的待办（未处理），和成功率无关',
  },
])

function isChipActive(chip: StatChip): boolean {
  if (chip.filter.status != null && chip.filter.status !== (statusFilter.value || '')) return false
  if (chip.filter.kind != null && chip.filter.kind !== (kindFilter.value || '')) return false
  return chip.filter.status != null || chip.filter.kind != null
}

function onPageChange(page: number) {
  replaceQuery({ page: String(page) })
}

watch(() => route.query, () => {
  clearSelection() // 切换筛选 / 分页 / tab 时清空批量选择
  syncFromRoute()
  if (props.active) {
    void loadList()
    void loadStats()
  }
}, { deep: true, immediate: true })

</script>

<style scoped>
/* 设计稿 inbox.jsx 的 dense filter row：无边框，inline 行式排列；与列表保持视觉一体 */
.filter-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 0;
  background: transparent;
  border: 0;
  border-radius: 0;
  flex-wrap: wrap;
}

.filter-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  flex: 1 1 auto;
  min-width: 0;
}

.filter-label {
  font-size: var(--sf-text-caption);
  font-weight: 600;
  color: var(--ai-ink-2);
}

.filter-spacer {
  flex: 1 1 0;
}

.filter-quick-pills {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
  flex-wrap: wrap;
}

.filter-quick-pill {
  cursor: pointer;
  font-size: 11.5px;
  height: 22px;
}
.filter-quick-pill.active {
  background: var(--ai-ink-1);
  color: white;
  border-color: var(--ai-ink-1);
}

.todo-search {
  width: 280px;
  flex: 0 1 280px;
}

.filter-controls :deep(.arco-input-wrapper),
.filter-controls :deep(.arco-select-view),
.filter-controls :deep(.arco-picker) {
  min-height: 30px;
  height: 30px;
  border-radius: 6px;
  border-color: var(--ai-border);
  background: var(--ai-surface);
  box-shadow: none;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-sans);
  font-size: 12.5px;
}

.filter-controls :deep(.arco-input-wrapper:hover),
.filter-controls :deep(.arco-select-view:hover),
.filter-controls :deep(.arco-picker:hover) {
  border-color: var(--ai-border-2);
  background: var(--ai-surface-2);
}

.filter-controls :deep(.arco-input),
.filter-controls :deep(.arco-select-view-value),
.filter-controls :deep(.arco-picker-input input) {
  color: var(--ai-ink-1);
  font-size: 12.5px;
}

.filter-controls :deep(.arco-input::placeholder),
.filter-controls :deep(.arco-select-view-placeholder),
.filter-controls :deep(.arco-picker-input input::placeholder) {
  color: var(--ai-ink-4);
}


/* 设计稿 inbox.jsx 的 row-style list：单列、无 gap，行间靠 hairline 分隔 */
.todo-grid {
  display: flex;
  flex-direction: column;
  gap: 0;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  overflow-x: auto;
  overflow-y: hidden;
}

/* 表头：关键指标列保留完整展示空间，避免用 +N 省略。 */
.todo-grid-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  background: var(--ai-surface-2);
  border-bottom: 1px solid var(--ai-border);
  font-size: 11.5px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ai-ink-4);
  font-weight: 500;
}
.todo-grid-header .col-select {
  width: 18px;
  flex: 0 0 18px;
}
.todo-grid-header .col-prio {
  width: 36px;
  flex: 0 0 36px;
}
.todo-grid-header .col-main {
  flex: 0 1 clamp(180px, 24vw, 360px);
  min-width: 0;
  max-width: clamp(180px, 24vw, 360px);
}
.todo-grid-header .col-metric {
  width: 360px;
  min-width: 340px;
  max-width: 560px;
  flex: 1 0 360px;
}
.todo-grid-header .col-skill {
  width: 130px;
  flex: 0 0 130px;
}
.todo-grid-header .col-sla {
  width: 80px;
  flex: 0 0 80px;
}
.todo-grid-header .col-actions {
  width: 200px;
  flex: 0 0 200px;
  text-align: right;
}

.todo-grid-cell {
  position: relative;
  display: flex;
  flex-direction: column;
  border-bottom: 1px solid var(--ai-border);
}
.todo-grid-cell:last-child {
  border-bottom: 0;
}

.todo-grid-cell.is-selected {
  background: var(--ai-accent-soft);
}

.todo-select-cell {
  position: absolute;
  top: 14px;
  left: 14px;
  z-index: 2;
  padding: 0;
}

.todo-grid-cell :deep(.todo-card) {
  /* dense row 自带左缩进给 select；这里把 padding-left 调大让 checkbox 不与内容重叠 */
  padding-left: 40px;
  border: 0;
  border-radius: 0;
  background: transparent;
}

.todo-grid-cell :deep(.todo-card::before) {
  /* 让 status accent 色条与列头对齐；保留色条但变细 */
  width: 2px;
}

.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 18px;
}

.footer-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 14px;
  color: var(--ai-ink-4);
  font-size: 12px;
}

.footer-left {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.footer-hint {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 400;
}

.footer-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-variant-numeric: tabular-nums;
}

.footer-count {
  color: var(--ai-ink-4);
  font-size: 12px;
  white-space: nowrap;
}

.footer-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.stat-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: 16px;
  font-size: var(--sf-text-caption);
  cursor: pointer;
  border: 1px solid transparent;
  transition: filter 0.15s ease, border-color 0.15s ease;
}

.stat-chip:hover {
  filter: brightness(0.96);
}

.stat-chip.active {
  border-color: currentColor;
}

.stat-chip b {
  font-size: var(--sf-text-body);
}

.stat-dispatch { background: var(--ai-info-soft); color: var(--ai-info); }
.stat-approved { background: var(--ai-ok-soft); color: var(--ai-ok); }
.stat-rejected { background: var(--ai-bad-soft); color: var(--ai-bad); }
.stat-expired { background: var(--ai-surface-2); color: var(--ai-ink-3); }

:deep(.dispatch-approve-modal) {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

:deep(.dispatch-approve-head) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

:deep(.dispatch-approve-tip) {
  margin: 0;
  color: var(--ai-ink-2);
}

:deep(.dispatch-approve-meta) {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

:deep(.dispatch-approve-row) {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  border-radius: 10px;
  background: var(--ai-surface-2);
}

:deep(.dispatch-approve-summary) {
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: var(--ai-ink-1);
}

:deep(.dispatch-approve-select) {
  width: 100%;
}

:deep(.dispatch-approve-select .arco-select-view) {
  min-height: 40px;
  border-radius: 10px;
  background: var(--ai-surface-2);
  border-color: var(--ai-border);
}

:deep(.dispatch-approve-select .arco-select-view-value) {
  color: var(--ai-ink-1);
}

@media (max-width: 960px) {
  .filter-bar {
    flex-direction: column;
    align-items: stretch;
  }

  .footer-bar {
    flex-direction: column;
    align-items: flex-start;
  }
  .footer-stats {
    justify-content: flex-start;
  }
}
</style>
