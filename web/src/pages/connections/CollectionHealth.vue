<template>
  <div class="page-container page-wide collection-health-page">
    <div class="page-header">
      <div class="page-heading">
        <div class="page-kicker">管理后台 · 数据与服务</div>
        <h2 class="page-title">采集健康</h2>
        <p class="page-subtitle">按部门 / 店铺聚合 Cookie 覆盖、7 天数据健康、Scope 缺口和 Circuit 状态。</p>
      </div>
      <a-space class="page-actions" wrap>
        <router-link to="/admin/connections">
          <a-button class="ai-btn-like">连接管理</a-button>
        </router-link>
        <a-button class="ai-btn-like primary" type="primary" :loading="loading" @click="load">
          <icon-refresh />刷新
        </a-button>
      </a-space>
    </div>

    <div class="conn-tabs">
      <router-link to="/admin/connections" class="conn-tab">
        <icon-link /> 平台连接
      </router-link>
      <router-link to="/admin/connections?tab=browser" class="conn-tab">
        <icon-desktop /> 浏览器采集
      </router-link>
      <router-link to="/admin/connections/health" class="conn-tab active">
        <icon-bar-chart /> 采集健康
      </router-link>
      <router-link to="/admin/connector-keys" class="conn-tab">
        <icon-safe /> Connector Keys
      </router-link>
    </div>

    <div class="health-kpis">
      <article class="health-kpi ai-card">
        <div class="kpi-label">店铺数</div>
        <div class="kpi-value">{{ stats.shop_count ?? rows.length }}</div>
        <div class="kpi-hint">当前纳入巡检的店铺</div>
      </article>
      <article class="health-kpi ai-card">
        <div class="kpi-label">活跃凭证</div>
        <div class="kpi-value">{{ stats.active_credential_count ?? activeTotal }}</div>
        <div class="kpi-hint">可用 Cookie / 连接凭证</div>
      </article>
      <article class="health-kpi ai-card">
        <div class="kpi-label">平均健康度</div>
        <div class="kpi-value">{{ pct(stats.avg_data_health_ratio) }}</div>
        <div class="kpi-hint">最近 7 天数据完整度</div>
      </article>
      <article class="health-kpi ai-card danger-card">
        <div class="kpi-label">Circuit Open</div>
        <div class="kpi-value danger">{{ circuitOpenCount }}</div>
        <div class="kpi-hint">需要排查的熔断店铺</div>
      </article>
    </div>

    <div class="health-panels">
      <section class="health-panel ai-card">
        <header class="ai-card-h">
          <div>
            <div class="t">部门 / 店铺矩阵</div>
            <div class="s">健康度低于 80% 或 Circuit Open 会被标记</div>
          </div>
        </header>
        <div class="health-panel-body">
        <div class="matrix">
          <div v-for="dept in departmentMatrix" :key="dept.department" class="matrix-row">
            <div class="matrix-dept">{{ dept.department }}</div>
            <div class="matrix-shops">
              <router-link
                v-for="shop in dept.shops"
                :key="`${dept.department}-${shop.platform}-${shop.shop_id}`"
                class="matrix-shop"
                :class="{ warn: normalizedRatio(shop) < 0.8 || isCircuitOpen(shop) }"
                :to="poolLink(shop)"
              >
                <span>{{ shop.shop_name || shop.shop_id || '未绑定店铺' }}</span>
                <strong>{{ pct(normalizedRatio(shop)) }}</strong>
              </router-link>
            </div>
          </div>
        </div>
        </div>
      </section>

      <section class="health-panel ai-card">
        <header class="ai-card-h">
          <div>
            <div class="t">缺失 Scope Top3</div>
            <div class="s">优先补齐影响最大的采集权限</div>
          </div>
        </header>
        <div class="health-panel-body">
        <div v-if="missingScopeTop.length" class="scope-list">
          <div v-for="item in missingScopeTop" :key="item.scope" class="scope-row">
            <span>{{ item.scope }}</span>
            <a-tag color="orange">{{ item.count }}</a-tag>
          </div>
        </div>
        <div v-else class="health-empty">
          <icon-check-circle />
          <span>暂无缺失 Scope</span>
        </div>
        </div>
      </section>
    </div>

    <section class="health-table-card ai-card">
      <header class="ai-card-h">
        <div>
          <div class="t">店铺明细</div>
          <div class="s">Cookie 覆盖、数据健康、熔断状态和缺失权限</div>
        </div>
        <a-tag size="small" color="gray">{{ rows.length }} shops</a-tag>
      </header>
      <a-table
        class="health-table"
        :data="rows"
        :loading="loading"
        :pagination="{ pageSize: 20, showTotal: true }"
        :bordered="false"
        size="small"
        row-key="id"
      >
        <template #columns>
          <a-table-column title="部门" data-index="department" :width="130">
            <template #cell="{ record }">{{ record.department || '未归属' }}</template>
          </a-table-column>
          <a-table-column title="平台" data-index="platform" :width="110" />
          <a-table-column title="店铺" :width="180">
            <template #cell="{ record }">
              <div>{{ record.shop_name || record.shop_id || '-' }}</div>
              <div class="muted">{{ record.shop_id || '-' }}</div>
            </template>
          </a-table-column>
          <a-table-column title="Cookie 覆盖" :width="150">
            <template #cell="{ record }">
              Active {{ record.active_count || 0 }} / {{ record.total_count || 0 }}
            </template>
          </a-table-column>
          <a-table-column title="多账号混用" :width="120">
            <template #cell="{ record }">
              <a-tag :color="mixedCredentials(record) ? 'orange' : 'green'">
                {{ mixedCredentials(record) ? 'mixed' : 'clean' }}
              </a-tag>
            </template>
          </a-table-column>
          <a-table-column title="数据健康" :width="140">
            <template #cell="{ record }">
              <a-progress :percent="normalizedRatio(record)" size="small" />
            </template>
          </a-table-column>
          <a-table-column title="7 天趋势" :width="150">
            <template #cell="{ record }">
              <div class="trend-bars">
                <span
                  v-for="(point, index) in trendPoints(record)"
                  :key="index"
                  class="trend-bar"
                  :class="{ low: point < 0.8 }"
                  :style="{ height: `${Math.max(4, Math.round(point * 28))}px` }"
                />
              </div>
            </template>
          </a-table-column>
          <a-table-column title="Circuit" :width="120">
            <template #cell="{ record }">
              <a-tag :color="isCircuitOpen(record) ? 'red' : 'green'">
                {{ record.circuit_state || 'closed' }}
              </a-tag>
            </template>
          </a-table-column>
          <a-table-column title="Warning Group">
            <template #cell="{ record }">
              <a-space wrap>
                <a-tag v-for="group in record.warning_groups || []" :key="group" size="small" color="orange">{{ group }}</a-tag>
                <span v-if="!(record.warning_groups || []).length" class="muted">-</span>
              </a-space>
            </template>
          </a-table-column>
          <a-table-column title="缺失 Scope">
            <template #cell="{ record }">
              <a-space wrap>
                <a-tag v-for="scope in missingScopes(record).slice(0, 3)" :key="scope" size="small">{{ scope }}</a-tag>
                <span v-if="!missingScopes(record).length" class="muted">-</span>
              </a-space>
            </template>
          </a-table-column>
          <a-table-column title="操作" :width="170" fixed="right">
            <template #cell="{ record }">
              <a-space>
                <router-link :to="poolLink(record)">Cookie 池</router-link>
                <router-link :to="{ path: '/inbox', query: { tab: 'drift', platform: record.platform, shop_id: record.shop_id } }">
                  Drift
                </router-link>
              </a-space>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { IconBarChart, IconCheckCircle, IconDesktop, IconLink, IconRefresh, IconSafe } from '@arco-design/web-vue/es/icon'
import { cookiePoolApi, type CollectionHealthRow } from '@/api'

const loading = ref(false)
const rows = ref<CollectionHealthRow[]>([])
const stats = ref<Record<string, any>>({})
const activeTotal = computed(() => rows.value.reduce((sum, row) => sum + Number(row.active_count || 0), 0))
const circuitOpenCount = computed(() => rows.value.filter(isCircuitOpen).length)
const departmentMatrix = computed(() => {
  const grouped = new Map<string, CollectionHealthRow[]>()
  for (const row of rows.value) {
    const dept = row.department || '未归属'
    grouped.set(dept, [...(grouped.get(dept) || []), row])
  }
  return Array.from(grouped.entries()).map(([department, shops]) => ({ department, shops }))
})
const missingScopeTop = computed(() => {
  const counts = new Map<string, number>()
  for (const row of rows.value) {
    for (const scope of missingScopes(row)) counts.set(scope, (counts.get(scope) || 0) + 1)
  }
  return Array.from(counts.entries())
    .map(([scope, count]) => ({ scope, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 3)
})

function pct(value: unknown): string {
  const n = Number(value || 0)
  const normalized = n > 1 ? n / 100 : n
  return `${Math.round(normalized * 100)}%`
}

function normalizedRatio(row: CollectionHealthRow): number {
  const raw = row.data_health_ratio ?? row.avg_health ?? row.verify_pass_rate ?? 0
  const num = Number(raw)
  if (!Number.isFinite(num)) return 0
  return Math.max(0, Math.min(1, num > 1 ? num / 100 : num))
}

function trendPoints(row: CollectionHealthRow): number[] {
  const raw = row.data_health_trend || row.health_trend || []
  const points = raw.map((item) => {
    const value = typeof item === 'number' ? item : (item.ratio ?? item.data_health_ratio ?? 0)
    const num = Number(value)
    return Number.isFinite(num) ? Math.max(0, Math.min(1, num > 1 ? num / 100 : num)) : 0
  })
  if (points.length) return points.slice(-7)
  return Array.from({ length: 7 }, () => normalizedRatio(row))
}

function missingScopes(row: CollectionHealthRow): string[] {
  return [...(row.missing_data_scopes || []), ...(row.missing_scopes || [])].filter(Boolean).slice(0, 20)
}

function mixedCredentials(row: CollectionHealthRow): boolean {
  if (typeof row.mixed_credentials === 'boolean') return row.mixed_credentials
  return Number(row.mixed_credentials_ratio || 0) > 0
}

function isCircuitOpen(row: CollectionHealthRow): boolean {
  return String(row.circuit_state || '').toLowerCase() === 'open'
}

function poolLink(row: CollectionHealthRow) {
  return `/admin/connections/${encodeURIComponent(String(row.platform || ''))}/${encodeURIComponent(String(row.shop_id || ''))}`
}

async function load() {
  loading.value = true
  try {
    const res = await cookiePoolApi.health()
    rows.value = Array.isArray(res) ? res : (res.items || [])
    stats.value = res.stats || {}
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.collection-health-page {
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}

.collection-health-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}

.collection-health-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
}

.collection-health-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}

.collection-health-page :deep(.arco-btn.ai-btn-like) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
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

.collection-health-page :deep(.arco-btn.ai-btn-like .arco-btn-content) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.collection-health-page :deep(.arco-btn.ai-btn-like:hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}

.collection-health-page :deep(.arco-btn-primary.ai-btn-like) {
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
}

.collection-health-page :deep(.arco-btn-primary.ai-btn-like:hover) {
  background: #000;
  border-color: #000;
}

.conn-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 16px;
  border-bottom: 1px solid var(--ai-border);
}

.conn-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 12px;
  font-size: 13px;
  font-weight: 450;
  color: var(--ai-ink-3);
  text-decoration: none;
  border-bottom: 1.5px solid transparent;
  margin-bottom: -1px;
  transition: color 0.15s ease, border-color 0.15s ease;
}

.conn-tab:hover {
  color: var(--ai-ink-2);
}

.conn-tab.active {
  color: var(--ai-ink-1);
  font-weight: 500;
  border-bottom-color: var(--ai-ink-1);
}

.health-kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.health-kpi {
  padding: 14px 16px;
}

.danger-card {
  border-left: 2px solid var(--ai-bad);
}

.health-panels {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(260px, 1fr);
  gap: 12px;
  margin-bottom: 14px;
}

.health-panel,
.health-table-card {
  overflow: hidden;
}

.health-panel-body {
  padding: 14px;
}

.kpi-label {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.kpi-value {
  margin-top: 6px;
  font-size: 26px;
  font-weight: 650;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
}

.kpi-hint {
  margin-top: 4px;
  font-size: 12px;
  color: var(--ai-ink-4);
}

.kpi-value.danger {
  color: var(--ai-bad);
}

.matrix {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.matrix-row {
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}

.matrix-dept {
  color: var(--ai-ink-2);
  font-size: 12.5px;
  font-weight: 600;
}

.matrix-shops {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.matrix-shop {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  min-height: 28px;
  padding: 0 9px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  color: var(--ai-ink-1);
  text-decoration: none;
  background: var(--ai-surface);
  font-size: 12px;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.matrix-shop:hover {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}

.matrix-shop.warn {
  border-color: transparent;
  color: var(--ai-warn);
  background: var(--ai-warn-soft);
}

.scope-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.scope-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 30px;
  padding: 0 2px;
  color: var(--ai-ink-2);
  font-size: 12.5px;
  border-bottom: 1px solid var(--ai-border);
}

.scope-row:last-child {
  border-bottom: 0;
}

.health-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 96px;
  color: var(--ai-ink-4);
  font-size: 12.5px;
}

.trend-bars {
  display: flex;
  height: 30px;
  gap: 3px;
  align-items: end;
}

.trend-bar {
  width: 10px;
  border-radius: 3px 3px 0 0;
  background: var(--ai-ok);
}

.trend-bar.low {
  background: var(--ai-warn);
}

.muted {
  color: var(--ai-ink-4);
}

.collection-health-page :deep(.arco-table-th) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 8px 10px !important;
}

.collection-health-page :deep(.arco-table-th .arco-table-th-title) {
  color: var(--ai-ink-4);
  font-weight: 500;
}

.collection-health-page :deep(.arco-table-td) {
  font-size: 12.5px !important;
  color: var(--ai-ink-1) !important;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 10px !important;
  background: var(--ai-surface) !important;
}

.collection-health-page :deep(.arco-table-tr:hover .arco-table-td),
.collection-health-page :deep(.arco-table-tr-hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

.collection-health-page :deep(.arco-tag.arco-tag-size-small),
.collection-health-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
}

.collection-health-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
}

.collection-health-page :deep(.arco-tag-color-orange),
.collection-health-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
}

.collection-health-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
}

.collection-health-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-3) !important;
}

.collection-health-page :deep(.arco-progress-line-text) {
  font-size: 11px;
  color: var(--ai-ink-4);
}

@media (max-width: 1180px) {
  .health-kpis,
  .health-panels {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .health-kpis,
  .health-panels,
  .matrix-row {
    grid-template-columns: 1fr;
  }

  .conn-tabs {
    overflow-x: auto;
  }
}
</style>
