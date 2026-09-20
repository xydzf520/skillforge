<template>
  <div class="page-container admin-audit-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">管理后台 · 治理与合规</div>
        <h2 class="page-title">审计日志</h2>
        <p class="page-subtitle">操作流水、登录失败、权限拒绝 · 全量留痕 · 支持 CSV 导出</p>
      </div>
      <a-space>
        <a-button class="ai-btn-like" @click="handleExport">导出CSV</a-button>
      </a-space>
    </div>

    <!-- 日志表格 -->
    <a-card class="page-list-card">
      <div class="filter-bar">
        <a-space wrap>
          <a-input v-model="filters.user_id" placeholder="用户ID" allow-clear class="filter-user" />
          <a-select v-model="filters.action" placeholder="操作类型" allow-clear allow-search class="filter-action">
            <a-option
              v-for="opt in actionOptions"
              :key="opt.action"
              :value="opt.action"
            >
              {{ opt.label }}
            </a-option>
          </a-select>
          <a-select v-model="filters.result" placeholder="结果" allow-clear class="filter-result">
            <a-option value="success">成功</a-option>
            <a-option value="failed">失败</a-option>
          </a-select>
          <a-date-picker v-model="filters.date_from" placeholder="开始日期" class="filter-date" />
          <a-date-picker v-model="filters.date_to" placeholder="结束日期" class="filter-date" />
          <a-button class="ai-btn-like primary" type="primary" @click="loadLogs">查询</a-button>
        </a-space>
        <!-- 快捷日期 -->
        <div class="quick-range">
          <a-button class="ai-btn-like" size="mini" @click="setQuickRange('today')">今日</a-button>
          <a-button class="ai-btn-like" size="mini" @click="setQuickRange('week')">本周</a-button>
          <a-button class="ai-btn-like" size="mini" @click="setQuickRange('month')">本月</a-button>
          <a-button class="ai-btn-like" size="mini" @click="setQuickRange('clear')">清除</a-button>
        </div>
      </div>
      <a-spin :loading="loading">
        <a-table class="page-list-table" :scroll="{ x: '100%' }" :data="logs" :pagination="false" row-key="id">
          <template #columns>
            <a-table-column title="时间" data-index="created_at" :width="140">
              <template #cell="{ record }">
                <span class="cell-mono">{{ formatTimeFull(record.created_at) }}</span>
              </template>
            </a-table-column>
            <a-table-column title="用户" data-index="user_id" :width="100">
              <template #cell="{ record }">
                <span class="cell-mono">{{ record.user_id }}</span>
              </template>
            </a-table-column>
            <a-table-column title="操作" data-index="action" :width="160">
              <template #cell="{ record }">
                <!-- P0-4：操作类型不是链接，不再用 arcoblue 标签误导视觉；
                     只有安全事件用 danger tone 强调，其余用等宽字体 + 中性色 -->
                <span
                  v-if="isSecurityEvent(record.action)"
                  class="audit-action-tag is-danger"
                >
                  {{ record.action }}
                </span>
                <span v-else class="audit-action-code">{{ record.action }}</span>
              </template>
            </a-table-column>
            <a-table-column title="目标" data-index="target_type" :width="80" />
            <a-table-column title="目标ID" :width="160">
              <template #cell="{ record }">
                <!-- P0-4：真链接（skill / review / playbook）用 accent 色 + 箭头图标；
                     非可点目标用等宽字体中性色 -->
                <a
                  v-if="canLinkTarget(record)"
                  class="audit-target-link"
                  :href="targetHref(record)"
                  @click.prevent="openTarget(record)"
                >
                  <span class="audit-target-id">{{ record.target_id }}</span>
                  <icon-launch class="audit-target-arrow" />
                </a>
                <span
                  v-else-if="record.target_id"
                  class="audit-target-muted"
                >{{ record.target_id }}</span>
                <span v-else class="detail-muted">-</span>
              </template>
            </a-table-column>
            <a-table-column title="详情">
              <template #cell="{ record }">
                <div class="detail-inline">
                  <template v-if="record.detail">
                    <span
                      v-for="(val, key) in extractKeyFields(record.detail)"
                      :key="key"
                      class="detail-tag"
                    >
                      <b>{{ detailLabel(String(key)) }}</b>: {{ val }}
                    </span>
                    <a-button size="mini" type="text" class="ai-btn-text" @click="openDetail(record)">查看原始</a-button>
                  </template>
                  <span v-else class="detail-muted">-</span>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="IP" data-index="ip_address" :width="120">
              <template #cell="{ record }">
                <span class="cell-mono">{{ record.ip_address || '-' }}</span>
              </template>
            </a-table-column>
          </template>
        </a-table>
      </a-spin>

      <div class="bottom-bar">
        <div class="stats-inline">
          <SfStatChip label="总记录" :value="stats.total || 0" />
          <SfStatChip label="登录失败" :value="stats.security?.login_failures || 0" tone="danger" />
          <SfStatChip label="权限拒绝" :value="stats.security?.permission_denials || 0" tone="warning" />
        </div>
        <a-pagination
          :current="pagination.current"
          :page-size="pagination.pageSize"
          :total="pagination.total"
          :page-size-options="[20, 50, 100, 200]"
          show-page-size
          show-total
          size="small"
          @change="onPageChange"
          @page-size-change="onPageSizeChange"
        />
      </div>
    </a-card>

    <a-drawer
      :visible="detailVisible"
      :width="'min(90vw, 640px)'"
      title="审计详情"
      @cancel="detailVisible = false"
      :footer="false"
    >
      <div v-if="activeDetail" class="audit-drawer">
        <a-descriptions :column="1" bordered size="small" class="audit-desc">
          <a-descriptions-item label="时间"><span class="cell-mono">{{ formatTimeFull(activeDetail.created_at) }}</span></a-descriptions-item>
          <a-descriptions-item label="用户"><span class="cell-mono">{{ activeDetail.user_id }}</span></a-descriptions-item>
          <a-descriptions-item label="操作">{{ activeDetail.action }}</a-descriptions-item>
          <a-descriptions-item v-if="activeDetail.target_id" label="目标"><span class="cell-mono">{{ activeDetail.target_type }} / {{ activeDetail.target_id }}</span></a-descriptions-item>
          <a-descriptions-item label="IP"><span class="cell-mono">{{ activeDetail.ip_address || '-' }}</span></a-descriptions-item>
        </a-descriptions>
        <div class="audit-detail-title">原始详情 (JSON)</div>
        <pre class="audit-detail-json">{{ JSON.stringify(activeDetail.detail, null, 2) }}</pre>
      </div>
    </a-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconLaunch } from '@arco-design/web-vue/es/icon'
import { useRouter } from 'vue-router'
import { auditApi as rawAuditApi } from '@/api'
import { bjtDateString, bjtParts, dateFromBjtParts, formatTimeFull } from '@/utils/format'
import { SfStatChip } from '@/components/sf'

const router = useRouter()

const auditApi: any = rawAuditApi
const loading = ref(false)
const logs = ref<any[]>([])
const stats = ref<any>({})
const filters = reactive<{ user_id: string; action?: string; result?: string; date_from?: string; date_to?: string }>({ user_id: '', action: undefined, result: undefined, date_from: '', date_to: '' })
const pagination = reactive({ current: 1, pageSize: 50, total: 0 })

function setQuickRange(kind: 'today' | 'week' | 'month' | 'clear') {
  if (kind === 'clear') {
    filters.date_from = ''
    filters.date_to = ''
    loadLogs()
    return
  }
  const now = bjtParts(Date.now())
  if (!now) return
  const end = dateFromBjtParts(now.year, now.month, now.day)
  let start = dateFromBjtParts(now.year, now.month, now.day)
  if (kind === 'today') {
    // start=today, end=today
  } else if (kind === 'week') {
    // 周一作为一周开始
    const dow = (new Date(Date.UTC(now.year, now.month - 1, now.day)).getUTCDay() + 6) % 7
    start = dateFromBjtParts(now.year, now.month, now.day - dow)
  } else if (kind === 'month') {
    start = dateFromBjtParts(now.year, now.month, 1)
  }
  filters.date_from = bjtDateString(start)
  filters.date_to = bjtDateString(end)
  loadLogs()
}

const securityActions = ['user.login_failed', 'auth.permission_denied']

function isSecurityEvent(action: string | undefined) { return securityActions.some(s => action?.includes(s)) }

// P0-4：判断目标 ID 是否可跳转到详情页。
// 只有 skill/review/playbook 这类有专属页面的目标类型才视为可点击。
const LINKABLE_TARGET_TYPES = new Set(['skill', 'review', 'playbook', 'execution', 'dispatch'])
function canLinkTarget(record: { target_type?: string; target_id?: string }): boolean {
  if (!record?.target_id || !record?.target_type) return false
  return LINKABLE_TARGET_TYPES.has(record.target_type.toLowerCase())
}

// 目标类型 → 详情页路径
function targetHref(record: { target_type?: string; target_id?: string }): string {
  const type = (record.target_type || '').toLowerCase()
  const id = encodeURIComponent(record.target_id || '')
  switch (type) {
    case 'skill': return `/skills/${id}`
    case 'review': return `/reviews/${id}`
    case 'playbook': return `/playbooks/${id}`
    case 'execution': return `/executions?run_id=${id}`
    case 'dispatch': return `/inbox/dispatch/${id}`
    default: return ''
  }
}

function openTarget(record: { target_type?: string; target_id?: string }) {
  const href = targetHref(record)
  if (href) router.push(href)
}

// 抽取详情里关键字段显示为 inline 标签，其余用"查看原始"展开 JSON
// 白名单默认值：加载后端 /api/audit/detail-fields 成功则替换；失败则继续用默认
const DEFAULT_DETAIL_WHITELIST = ['reason', 'username', 'role_from', 'role_to', 'skill_id', 'from', 'to', 'block']
const detailWhitelist = ref<string[]>(DEFAULT_DETAIL_WHITELIST)
const detailLabels = ref<Record<string, string>>({})

// 操作类型动态枚举：/api/audit/actions 拉取，失败保底用本地硬编码
const FALLBACK_ACTIONS = [
  { action: 'user.login', label: '登录' },
  { action: 'user.login_failed', label: '登录失败' },
  { action: 'skill.create', label: '创建Skill' },
  { action: 'skill.edit', label: '编辑Skill' },
  { action: 'review.approve', label: '审核通过' },
  { action: 'review.reject', label: '审核驳回' },
]
const actionOptions = ref<Array<{ action: string; label: string }>>(FALLBACK_ACTIONS)

async function loadActions() {
  try {
    const res = await auditApi.actions() as { items?: Array<{ action: string; label?: string }> }
    if (Array.isArray(res?.items) && res.items.length) {
      actionOptions.value = res.items.map(x => ({ action: x.action, label: x.label || x.action }))
    }
  } catch {
    // 保持 fallback
  }
}

async function loadDetailFields() {
  try {
    const res = await auditApi.detailFields() as { fields?: Array<{ key: string; label?: string }> }
    if (Array.isArray(res?.fields) && res.fields.length) {
      detailWhitelist.value = res.fields.map(f => f.key)
      detailLabels.value = Object.fromEntries(
        res.fields.filter(f => f.label).map(f => [f.key, f.label!]),
      )
    }
  } catch {
    // 静默降级到默认白名单
  }
}

function extractKeyFields(detail: any): Record<string, string> {
  if (!detail || typeof detail !== 'object') return {}
  const result: Record<string, string> = {}
  for (const key of detailWhitelist.value) {
    if (detail[key] != null) {
      const v = detail[key]
      const str = typeof v === 'object' ? JSON.stringify(v) : String(v)
      result[key] = str.length > 40 ? str.slice(0, 40) + '…' : str
    }
  }
  return result
}

/** 字段名 → 中文 label（后端返回为准） */
function detailLabel(key: string): string {
  return detailLabels.value[key] || key
}

const detailVisible = ref(false)
const activeDetail = ref<any>(null)

function openDetail(record: any) {
  activeDetail.value = record
  detailVisible.value = true
}

function onPageSizeChange(size: number) {
  pagination.pageSize = size
  pagination.current = 1
  loadLogs()
}

async function loadLogs() {
  loading.value = true
  try {
    const res = await auditApi.query({
      user_id: filters.user_id || undefined,
      action: filters.action || undefined,
      result: filters.result || undefined,  // 后端支持则生效；不支持则被忽略
      date_from: filters.date_from || undefined,
      date_to: filters.date_to || undefined,
      page: pagination.current,
      page_size: pagination.pageSize,
    })
    let items = res.items || []
    // 结果筛选的前端兜底：action 含 "_failed" / "permission_denied" 视为失败
    if (filters.result === 'failed') {
      items = items.filter((x: any) => /_failed|permission_denied|error/.test(x.action || ''))
    } else if (filters.result === 'success') {
      items = items.filter((x: any) => !/_failed|permission_denied|error/.test(x.action || ''))
    }
    logs.value = items
    pagination.total = res.total || 0
  } finally { loading.value = false }
}

async function loadStats() {
  try { stats.value = await auditApi.stats({ days: 30 }) } catch (e: any) { Message.error(e._message || '加载统计失败') }
}

async function handleExport() {
  try {
    const res = await auditApi.export(filters)
    const blob = res.data
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const disposition = res.headers['content-disposition']
    const match = disposition?.match(/filename="?(.+?)"?$/)
    a.download = match ? match[1] : 'audit_log.csv'
    a.click()
    URL.revokeObjectURL(url)
  } catch (e: any) { Message.error(e._message || '导出失败') }
}

function onPageChange(page: number) {
  pagination.current = page
  loadLogs()
}

onMounted(() => { loadLogs(); loadStats(); loadDetailFields(); loadActions() })
</script>

<style scoped>
/* 设计稿 page chrome 覆盖 —— 与 AdminUsers 同模式 */
.admin-audit-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.admin-audit-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
}
.admin-audit-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.admin-audit-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

/* 顶部按钮统一外形 —— 30px / 6px 圆角 / 12.5px 字号，对应 .ai-btn */
.admin-audit-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary)) {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
  box-shadow: none;
}
.admin-audit-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.admin-audit-page :deep(.arco-btn-primary.ai-btn-like) {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
  font-size: 12.5px;
  font-weight: 500;
  box-shadow: none;
}
.admin-audit-page :deep(.arco-btn-primary.ai-btn-like:hover) {
  background: var(--ai-ink-2);
  border-color: var(--ai-ink-2);
}
/* mini 尺寸的快捷日期按钮稍紧凑一些 */
.admin-audit-page :deep(.arco-btn.ai-btn-like.arco-btn-size-mini) {
  height: 26px;
  padding: 0 10px;
  font-size: 12px;
}

/* 表头 —— 11.5px / uppercase / ink-4 */
.admin-audit-page :deep(.arco-table-th) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 8px 10px !important;
}
.admin-audit-page :deep(.arco-table-th .arco-table-th-title) {
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* 单元格 —— 12.5px / ink-1 / ai-border 底分隔线 */
.admin-audit-page :deep(.arco-table-td) {
  font-size: 12.5px !important;
  color: var(--ai-ink-1) !important;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 10px 10px !important;
  background: var(--ai-surface) !important;
}

/* 行 hover：surface-2 */
.admin-audit-page :deep(.arco-table-tr:hover .arco-table-td),
.admin-audit-page :deep(.arco-table-tr-hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

/* tag pills —— 20px 高 / 4px 方角 / 11px/500 —— 按 arcoblue / green / red / orange / gray 五档 */
.admin-audit-page :deep(.arco-tag.arco-tag-size-small),
.admin-audit-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
}
.admin-audit-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.admin-audit-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.admin-audit-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.admin-audit-page :deep(.arco-tag-color-orange),
.admin-audit-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.admin-audit-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-2) !important;
  border-color: transparent !important;
}

/* 筛选区域 */
.filter-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.quick-range {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

/* 等宽单元格 —— 时间 / 用户ID / IP */
.cell-mono {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  color: var(--ai-ink-1);
}

/* 底部统计条 */
.bottom-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--ai-border);
}
.stats-inline {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

/* P0-4：区分可点链接 / 不可点数据的视觉权重 */
.audit-action-code {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
  border: 1px solid var(--ai-border);
}
.audit-action-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-family: var(--ai-font-mono);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
.audit-action-tag.is-danger {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}
.audit-target-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--ai-accent-ink);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  font-weight: 500;
  text-decoration: none;
  cursor: pointer;
}
.audit-target-link:hover {
  text-decoration: underline;
}
.audit-target-link:hover .audit-target-arrow {
  opacity: 1;
}
.audit-target-id {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 130px;
}
.audit-target-arrow {
  font-size: 11px;
  opacity: 0.65;
  transition: opacity 0.15s ease;
}
.audit-target-muted {
  color: var(--ai-ink-2);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: inline-block;
  max-width: 150px;
  vertical-align: middle;
}
.detail-inline { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.detail-tag {
  display: inline-flex; align-items: baseline; gap: 3px;
  padding: 2px 8px; border-radius: 4px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  font-size: 11px; color: var(--ai-ink-2);
}
.detail-tag b {
  font-weight: 600;
  color: var(--ai-ink-4);
  text-transform: uppercase;
  font-size: 10px;
  letter-spacing: 0.04em;
}
.detail-muted { color: var(--ai-ink-4); font-size: 12px; }

/* "查看原始" 文本按钮去掉默认蓝 */
.admin-audit-page :deep(.arco-btn.ai-btn-text.arco-btn-text) {
  color: var(--ai-ink-3);
  font-size: 12px;
  height: auto;
  padding: 0 4px;
}
.admin-audit-page :deep(.arco-btn.ai-btn-text.arco-btn-text:hover) {
  color: var(--ai-ink-1);
  background: transparent;
}

.audit-drawer { font-size: 13px; }
.audit-detail-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--ai-ink-4);
  text-transform: uppercase;
  margin-bottom: 6px;
  letter-spacing: 0.04em;
}
.audit-detail-json {
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  padding: 12px;
  border-radius: var(--ai-radius-s);
  font-family: var(--ai-font-mono);
  font-size: 12px;
  color: var(--ai-ink-1);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 60vh;
  overflow-y: auto;
}

/* Admin sweep utilities */
.filter-user,
.filter-date {
  width: 140px;
}
.filter-action {
  width: 200px;
}
.filter-result {
  width: 110px;
}
.audit-desc {
  margin-bottom: 12px;
}
@media (max-width: 900px) {
  .admin-audit-page :deep(.arco-space-item),
  .filter-user,
  .filter-action,
  .filter-result,
  .filter-date {
    width: 100%;
  }
  .quick-range,
  .bottom-bar {
    justify-content: flex-start;
  }
}

</style>
