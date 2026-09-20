<template>
  <div class="page-container portal-overview portal-overview-page">
    <div class="page-header">
      <div>
        <a-button type="text" size="small" class="back-btn" @click="$router.push('/portal')">← 返回门户</a-button>
        <div class="page-kicker">Skills · 应用门户 · 部门概览</div>
        <h2 class="page-title">部门概览 · <span class="org-name">{{ overview?.org_unit?.name || '当前组织' }}</span></h2>
        <div class="page-subtitle">查看本组织的执行活跃度与近期提交</div>
      </div>
      <a-button :loading="loading" @click="loadOverview">刷新</a-button>
    </div>

    <a-spin :loading="loading">
      <a-result v-if="loadError" status="warning" :title="loadError" class="page-list-card" />

      <template v-else>
        <!-- KPI 条：参照 InboxHeader / AdminCodexPlugin -->
        <div class="overview-kpi-strip">
          <div class="kpi-cell">
            <div class="kpi-label">可用 Skill</div>
            <div class="kpi-value">{{ stats.active_skills || 0 }}</div>
          </div>
          <div class="kpi-cell kpi-accent">
            <div class="kpi-label">今日执行</div>
            <div class="kpi-value">{{ stats.today_runs || 0 }}</div>
          </div>
          <div class="kpi-cell" :class="hasSuccessRate ? 'kpi-ok' : ''">
            <div class="kpi-label">7 日成功率</div>
            <div v-if="hasSuccessRate" class="kpi-value">{{ displaySuccessRate }}<span class="kpi-suffix">%</span></div>
            <div v-else class="kpi-value kpi-empty">—</div>
            <div v-if="!hasSuccessRate" class="kpi-sub">本期无执行</div>
          </div>
          <div class="kpi-cell">
            <div class="kpi-label">7 日总执行</div>
            <div class="kpi-value">{{ stats.total_runs_7d || 0 }}</div>
          </div>
        </div>

        <a-card class="page-list-card" title="最近执行">
          <a-table :data="recentSubmissions" :pagination="false" row-key="id">
            <template #columns>
              <a-table-column title="Skill" data-index="skill_display_name" />
              <a-table-column title="提交者" data-index="requester_name" />
              <a-table-column title="状态">
                <template #cell="{ record }">
                  <a-tag :color="submissionStatusColor(record.status)">{{ record.status || '-' }}</a-tag>
                </template>
              </a-table-column>
              <a-table-column title="时间">
                <template #cell="{ record }">{{ formatTime(record.created_at) }}</template>
              </a-table-column>
              <a-table-column title="操作" :width="90">
                <template #cell="{ record }">
                  <a-link v-if="record.can_view_detail && record.detail_id" @click="openSubmission(record.detail_id)">详情</a-link>
                  <span v-else class="muted-action">-</span>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-card>
      </template>
    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { portalApi as rawPortalApi } from '@/api'
import { formatTime } from '@/utils/format'
import { normalizeListResponse } from './shared'

defineOptions({ name: 'PortalOverview' })

type Overview = {
  org_unit?: { id?: string; name?: string }
  stats?: {
    total_skills?: number
    active_skills?: number
    today_runs?: number
    success_rate_7d?: number
    total_runs_7d?: number
  }
  recent_submissions?: Array<{
    id: string
    detail_id?: string | null
    can_view_detail?: boolean
    skill_display_name?: string
    requester_name?: string
    status?: string
    created_at?: string
  }>
}

const portalApi: any = rawPortalApi
const router = useRouter()
const loading = ref(false)
const loadError = ref('')
const overview = ref<Overview | null>(null)

const stats = computed(() => overview.value?.stats || {})
const recentSubmissions = computed(() => normalizeListResponse(overview.value?.recent_submissions))
// 判定是否有有效成功率：需同时看是否有分母（7 日执行数 > 0）
const hasSuccessRate = computed(() => {
  const runs = Number(stats.value.total_runs_7d || 0)
  const raw = stats.value.success_rate_7d
  return runs > 0 && raw !== null && raw !== undefined && Number.isFinite(Number(raw))
})
const displaySuccessRate = computed(() => {
  const value = Number(stats.value.success_rate_7d)
  if (!Number.isFinite(value)) return 0
  return value <= 1 ? value * 100 : value
})

async function loadOverview() {
  loading.value = true
  loadError.value = ''
  try {
    overview.value = await portalApi.getOverview()
  } catch (error: any) {
    overview.value = null
    loadError.value = error?._message || '加载部门概览失败'
  } finally {
    loading.value = false
  }
}

function openSubmission(id: string) {
  if (!id) return
  router.push(`/portal/submissions/${id}`)
}

function submissionStatusColor(status?: string): string {
  if (status === 'completed') return 'green'
  if (status === 'running') return 'arcoblue'
  if (status === 'failed') return 'red'
  if (status === 'pending') return 'orange'
  return 'gray'
}

onMounted(loadOverview)
</script>

<style scoped>
.portal-overview-page {
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}
.portal-overview-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.portal-overview-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
}
.portal-overview-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.portal-overview-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}
.portal-overview-page :deep(.page-list-card .arco-card-header-title) {
  font-weight: 500 !important;
  font-size: 13px !important;
  color: var(--ai-ink-1) !important;
}
.back-btn {
  align-self: flex-start;
  color: var(--ai-ink-4);
  font-size: 12px;
  margin-bottom: 4px;
  padding: 0;
}
.back-btn:hover {
  color: var(--ai-ink-1);
  background: transparent;
}
.org-name {
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-2);
  font-weight: 500;
}

/* KPI strip */
.overview-kpi-strip {
  display: flex;
  align-items: stretch;
  margin-bottom: 16px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  overflow: hidden;
}
.kpi-cell {
  flex: 1 1 0;
  min-width: 0;
  padding: 14px 20px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  border-left: 1px solid var(--ai-border);
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
  display: inline-flex;
  align-items: baseline;
}
.kpi-empty {
  color: var(--ai-ink-4);
}
.kpi-suffix {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-4);
  margin-left: 2px;
  font-family: var(--ai-font-mono);
}
.kpi-sub {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  margin-top: 2px;
}
.kpi-cell.kpi-ok .kpi-value { color: var(--ai-ok); }
.kpi-cell.kpi-accent .kpi-value { color: var(--ai-accent-ink); }

.muted-action {
  color: var(--ai-ink-4);
}

/* a-tag → ai-pill 五档配色 */
.portal-overview-page :deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.portal-overview-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
.portal-overview-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}
.portal-overview-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}
.portal-overview-page :deep(.arco-tag-color-orange) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.portal-overview-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}

/* table 密集化 */
.portal-overview-page :deep(.arco-table-th) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11px !important;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 8px 10px !important;
}
.portal-overview-page :deep(.arco-table-td) {
  font-size: 12.5px;
  padding: 10px 10px !important;
  border-bottom: 1px solid var(--ai-border) !important;
}
.portal-overview-page :deep(.arco-table-tr:hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

.portal-overview-page :deep(.arco-btn) {
  border-radius: 6px;
  height: 30px;
  font-weight: 500;
  font-size: 12.5px;
  box-shadow: none;
}
</style>
