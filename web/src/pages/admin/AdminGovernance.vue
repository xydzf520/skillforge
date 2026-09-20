<template>
  <div class="page-container admin-governance-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">管理后台 · 治理与合规</div>
        <h2 class="page-title">治理中心</h2>
        <p class="page-subtitle">聚合“需要处理”的治理事项，点一条就能跳到对应页执行。</p>
      </div>
      <a-space>
        <a-button class="ai-btn-like" size="small" :loading="loading" @click="loadData">
          <template #icon><icon-refresh /></template>
          刷新
        </a-button>
      </a-space>
    </div>

    <!-- 需要处理 -->
    <a-card class="page-list-card gov-section" :bordered="false">
      <div class="gov-section-head">
        <div class="gov-section-title">需要处理</div>
        <div class="gov-section-hint">{{ totalPending }} 项待处理</div>
      </div>
      <div class="gov-action-list">
        <router-link to="/reviews?status=pending" class="gov-action-row">
          <div class="gov-action-icon gov-action-icon--warn"><icon-check-square /></div>
          <div class="gov-action-main">
            <div class="gov-action-name">待审批 Skill</div>
            <div class="gov-action-key">reviews.pending</div>
            <div class="gov-action-meta">
              <span>去审核中心处理</span>
              <span>更新即时刷新</span>
            </div>
          </div>
          <div class="gov-action-count">{{ data.approvals?.pending || 0 }}</div>
          <span class="ai-btn-like sm gov-action-cta">前往</span>
        </router-link>

        <router-link to="/admin/org" class="gov-action-row">
          <div class="gov-action-icon gov-action-icon--info"><icon-lock /></div>
          <div class="gov-action-main">
            <div class="gov-action-name">待授权数据访问</div>
            <div class="gov-action-key">data_access.pending</div>
            <div class="gov-action-meta">
              <span>去组织架构处理</span>
              <span>由部门管理员授权</span>
            </div>
          </div>
          <div class="gov-action-count">{{ data.data_access?.pending || 0 }}</div>
          <span class="ai-btn-like sm gov-action-cta">前往</span>
        </router-link>

        <div class="gov-action-row gov-action-row--static">
          <div class="gov-action-icon gov-action-icon--bad"><icon-thunderbolt /></div>
          <div class="gov-action-main">
            <div class="gov-action-name">触发降级的组织</div>
            <div class="gov-action-key">quotas.throttled_orgs</div>
            <div class="gov-action-meta">
              <span>配额不足，需增配</span>
              <span>由系统管理员处理</span>
            </div>
          </div>
          <div class="gov-action-count">{{ data.quotas?.throttled_orgs || 0 }}</div>
          <span class="gov-action-static-tag">需扩容</span>
        </div>
      </div>
    </a-card>

    <a-row :gutter="16" class="gov-grid">
      <!-- 运行中 Worker -->
      <a-col :xs="24" :lg="12">
        <a-card class="page-list-card gov-section gov-section-flush" :bordered="false">
          <div class="gov-section-head gov-section-head--card">
            <div class="gov-section-title">Worker 在线</div>
            <div class="gov-section-hint">{{ workers.length }} 个</div>
          </div>
          <a-table
            :data="workers"
            :pagination="false"
            row-key="id"
            :bordered="false"
            class="page-list-table"
          >
            <template #empty>
              <div class="gov-empty">暂无在线 Worker</div>
            </template>
            <template #columns>
              <a-table-column title="名称" data-index="name">
                <template #cell="{ record }">
                  <span class="gov-cell-name">{{ record.name || '-' }}</span>
                </template>
              </a-table-column>
              <a-table-column title="队列" data-index="queue_name">
                <template #cell="{ record }">
                  <span class="gov-cell-mono">{{ record.queue_name || '-' }}</span>
                </template>
              </a-table-column>
              <a-table-column title="容量" data-index="capacity" :width="80">
                <template #cell="{ record }">
                  <span class="gov-cell-mono">{{ record.capacity ?? '-' }}</span>
                </template>
              </a-table-column>
              <a-table-column title="状态" :width="90">
                <template #cell="{ record }">
                  <a-tag size="small" :color="workerStatusColor(record.status)">{{ record.status || '未知' }}</a-tag>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-card>
      </a-col>

      <!-- 运营快照 -->
      <a-col :xs="24" :lg="12">
        <a-card class="page-list-card gov-section" :bordered="false">
          <div class="gov-section-head">
            <div class="gov-section-title">运营快照</div>
            <a class="gov-section-link" @click="$router.push('/dashboard')">效果看板 →</a>
          </div>
          <div class="gov-snap-list">
            <router-link to="/skills" class="gov-snap-row">
              <div class="gov-snap-main">
                <div class="gov-snap-label">已发布 Skill</div>
                <div class="gov-snap-key">market.active_skills</div>
              </div>
              <div class="gov-snap-value">{{ data.market?.active_skills || 0 }}</div>
            </router-link>
            <router-link to="/admin/org" class="gov-snap-row">
              <div class="gov-snap-main">
                <div class="gov-snap-label">已跟踪组织</div>
                <div class="gov-snap-key">quotas.tracked</div>
              </div>
              <div class="gov-snap-value">{{ data.quotas?.tracked || 0 }}</div>
            </router-link>
            <router-link to="/admin/org" class="gov-snap-row">
              <div class="gov-snap-main">
                <div class="gov-snap-label">已批准数据授权</div>
                <div class="gov-snap-key">data_access.approved</div>
              </div>
              <div class="gov-snap-value">{{ data.data_access?.approved || 0 }}</div>
            </router-link>
          </div>
        </a-card>
      </a-col>
    </a-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { dashboardApi as rawDashboardApi, executionApi as rawExecutionApi } from '@/api'
import { IconRefresh, IconCheckSquare, IconLock, IconThunderbolt } from '@arco-design/web-vue/es/icon'

defineOptions({ name: 'AdminGovernance' })

const dashboardApi: any = rawDashboardApi
const executionApi: any = rawExecutionApi
const data = ref<any>({})
const workers = ref<any[]>([])
const loading = ref(false)

const totalPending = computed(() => {
  return (
    (data.value?.approvals?.pending || 0) +
    (data.value?.data_access?.pending || 0) +
    (data.value?.quotas?.throttled_orgs || 0)
  )
})

function workerStatusColor(status: string | undefined): string {
  if (status === 'online' || status === 'healthy') return 'green'
  if (status === 'busy' || status === 'degraded') return 'orange'
  if (status === 'offline' || status === 'error') return 'red'
  return 'gray'
}

async function loadData() {
  loading.value = true
  try {
    const [gov, workerResp] = await Promise.all([
      dashboardApi.governance(),
      executionApi.listWorkers(),
    ])
    data.value = gov || {}
    workers.value = workerResp?.items || []
  } finally {
    loading.value = false
  }
}

onMounted(loadData)
</script>

<style scoped>
.admin-governance-page {
  display: grid;
  gap: 16px;
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}

/* 设计稿 page chrome 覆盖 —— 与 AdminUsers 同模式 */
.admin-governance-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.admin-governance-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
}
.admin-governance-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.admin-governance-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}
.admin-governance-page :deep(.page-list-card .arco-card-body) {
  padding: 16px 20px;
}

/* 顶部按钮 —— 30px / 6px 圆角 / 12.5px 字号 */
.admin-governance-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary)) {
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
.admin-governance-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}

/* 表头 —— 11.5px / uppercase / ink-4 */
.admin-governance-page :deep(.arco-table-th) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 8px 12px !important;
}
.admin-governance-page :deep(.arco-table-th .arco-table-th-title) {
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* 单元格 —— 12.5px / ink-1 */
.admin-governance-page :deep(.arco-table-td) {
  font-size: 12.5px !important;
  color: var(--ai-ink-1) !important;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 10px 12px !important;
  background: var(--ai-surface) !important;
}
.admin-governance-page :deep(.arco-table-tr:hover .arco-table-td),
.admin-governance-page :deep(.arco-table-tr-hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

/* tag pills —— 20px / 4px / 11px / 500 */
.admin-governance-page :deep(.arco-tag.arco-tag-size-small),
.admin-governance-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
}
.admin-governance-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.admin-governance-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.admin-governance-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.admin-governance-page :deep(.arco-tag-color-orange),
.admin-governance-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.admin-governance-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-2) !important;
  border-color: transparent !important;
}

/* gov-section 间距：a-row 自带 16px gutter；card stack 间距由 .admin-governance-page 网格 16px 控制 */
.gov-grid {
  margin: 0;
}

.gov-section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 12px;
}
.gov-section-head--card {
  padding: 14px 20px 12px;
  margin-bottom: 0;
  border-bottom: 1px solid var(--ai-border);
}
.gov-section-title {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: -0.005em;
  color: var(--ai-ink-1);
}
.gov-section-hint {
  font-size: 11.5px;
  color: var(--ai-ink-4);
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}
.gov-section-link {
  font-size: 12px;
  color: var(--ai-ink-3);
  cursor: pointer;
  text-decoration: none;
}
.gov-section-link:hover {
  color: var(--ai-ink-1);
}

/* 需要处理 —— 设计稿 AdminConfig setting-row 风格 */
.gov-action-list {
  display: grid;
  gap: 10px;
}

.gov-action-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 16px;
  padding: 14px 16px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  text-decoration: none;
  color: inherit;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.gov-action-row:not(.gov-action-row--static):hover {
  border-color: var(--ai-border-2);
  box-shadow: var(--ai-shadow-1);
}
.gov-action-row--static {
  cursor: default;
}

.gov-action-icon {
  width: 32px;
  height: 32px;
  border-radius: 6px;
  display: grid;
  place-items: center;
  font-size: 16px;
  flex: 0 0 auto;
}
.gov-action-icon--warn {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
}
.gov-action-icon--info {
  background: var(--ai-info-soft);
  color: var(--ai-info);
}
.gov-action-icon--bad {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}

.gov-action-main {
  min-width: 0;
}
.gov-action-name {
  font-weight: 600;
  font-size: 13.5px;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}
.gov-action-key {
  margin-top: 2px;
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-4);
  word-break: break-all;
}
.gov-action-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-top: 8px;
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.5;
}

.gov-action-count {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 22px;
  font-weight: 600;
  color: var(--ai-ink-1);
  min-width: 48px;
  text-align: right;
}

.gov-action-cta,
.ai-btn-like.sm {
  display: inline-flex;
  align-items: center;
  height: 26px;
  padding: 0 10px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 500;
  text-decoration: none;
  white-space: nowrap;
}
.gov-action-row:not(.gov-action-row--static):hover .gov-action-cta {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}

.gov-action-static-tag {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  white-space: nowrap;
}

/* 运营快照 —— 复用 setting-row 风格 */
.gov-snap-list {
  display: grid;
  gap: 10px;
}
.gov-snap-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 14px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  text-decoration: none;
  color: inherit;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.gov-snap-row:hover {
  border-color: var(--ai-border-2);
  box-shadow: var(--ai-shadow-1);
}
.gov-snap-main {
  min-width: 0;
}
.gov-snap-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}
.gov-snap-key {
  margin-top: 2px;
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-4);
}
.gov-snap-value {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 20px;
  font-weight: 600;
  color: var(--ai-ink-1);
}

/* 表格内 cell 风格 */
.gov-cell-name {
  font-size: 12.5px;
  color: var(--ai-ink-1);
  font-weight: 500;
}
.gov-cell-mono {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  color: var(--ai-ink-2);
}

.gov-empty {
  padding: 24px;
  text-align: center;
  color: var(--ai-ink-4);
  font-size: 12.5px;
}

@media (max-width: 760px) {
  .gov-action-row {
    grid-template-columns: auto minmax(0, 1fr);
    grid-template-areas:
      'icon main'
      'count cta';
  }
  .gov-action-icon { grid-area: icon; }
  .gov-action-main { grid-area: main; }
  .gov-action-count { grid-area: count; text-align: left; }
  .gov-action-cta,
  .gov-action-static-tag { grid-area: cta; justify-self: end; }
}

/* Admin sweep utilities */
.gov-section-flush :deep(.arco-card-body) {
  padding: 0;
}
@media (max-width: 900px) {
  .gov-grid :deep(.arco-col) {
    margin-bottom: 12px;
  }
}

</style>
