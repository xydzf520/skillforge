<template>
  <div class="dashboard-page ai-main">
    <div class="dashboard-head ai-pagehead">
      <div>
        <div class="ai-crumbs">看板 · 效果</div>
        <h1 class="ai-title">效果看板</h1>
        <p class="ai-sub">Skill 执行 / 采纳率 / 业务影响 · 按时段汇总</p>
      </div>
      <a-space class="dashboard-head-actions">
        <a-select v-model="days" style="width: 120px" @change="loadAll">
          <a-option :value="7">近 7 天</a-option>
          <a-option :value="30">近 30 天</a-option>
          <a-option :value="90">近 90 天</a-option>
        </a-select>
      </a-space>
    </div>

    <div v-if="loading" class="dashboard-body ai-pagebody">
      <SfLoadingState tip="加载看板数据..." height="400px" />
    </div>
    <div v-else class="dashboard-body ai-pagebody">
      <!-- 概览 KPI 条：参照设计稿 InboxHeader / AdminCodexPlugin 的 5 cell 风格
           value=0 时不应用信号色，避免无数据时仍显示绿/蓝/黄装饰（同 SfStatChip / adoption-pct 模式） -->
      <div class="dashboard-kpi-strip">
        <div class="kpi-cell">
          <div class="kpi-label">总执行</div>
          <div class="kpi-value">{{ overview.executions?.total || 0 }}</div>
        </div>
        <div class="kpi-cell" :class="{ 'kpi-ok': (overview.executions?.success || 0) > 0 }">
          <div class="kpi-label">成功</div>
          <div class="kpi-value">{{ overview.executions?.success || 0 }}</div>
        </div>
        <div class="kpi-cell" :class="{ 'kpi-accent': (overview.active_skills || 0) > 0 }">
          <div class="kpi-label">活跃 Skill</div>
          <div class="kpi-value">{{ overview.active_skills || 0 }}</div>
        </div>
        <div class="kpi-cell" :class="{ 'kpi-warn': (overview.time_saved_hours || 0) > 0 }">
          <div class="kpi-label">节省工时</div>
          <div class="kpi-value">{{ overview.time_saved_hours || 0 }}<span class="kpi-suffix">h</span></div>
        </div>
      </div>

      <!-- AI 成本卡已迁出：完整成本分析请去 /admin/costs（仅管理员可见） -->
      <a-alert
        v-if="canViewCosts"
        type="info"
        class="cost-moved-hint"
        closable
      >
        <template #title>AI 成本分析已独立</template>
        想看分组/环比/按天成本？
        <a-link @click="$router.push('/admin/costs')">去 AI 成本报表 →</a-link>
      </a-alert>


      <!-- 趋势图（ECharts lazy load） -->
      <a-row :gutter="16" class="section-row">
        <a-col :xs="24" :sm="24" :md="12">
          <a-card class="dashboard-card ai-card" title="执行量趋势">
            <TrendChart
              v-if="trends.length"
              :labels="trendLabels"
              :values="trendExecValues"
              color="rgb(var(--primary-6))"
            />
            <a-empty v-else description="暂无趋势数据" />
          </a-card>
        </a-col>
        <a-col :xs="24" :sm="24" :md="12">
          <a-card class="dashboard-card ai-card" title="采纳率趋势">
            <TrendChart
              v-if="trends.length"
              :labels="trendLabels"
              :values="trendAdoptionValues"
              color="rgb(var(--green-6))"
              unit="%"
            />
            <a-empty v-else description="暂无趋势数据" />
          </a-card>
        </a-col>
      </a-row>

      <a-row :gutter="16" class="section-row">
        <!-- 采纳率 -->
        <a-col :xs="24" :sm="24" :md="12">
          <a-card class="dashboard-card ai-card" title="采纳率">
            <div v-if="adoption.length">
              <div v-for="item in adoption" :key="item.skill_id" class="adoption-row">
                <span class="adoption-name">{{ item.skill_id }}</span>
                <a-progress :percent="item.adoption_rate / 100" :style="{ flex: 1 }" />
                <span class="adoption-pct" :class="{ muted: item.adoption_rate === 0 }">{{ item.adoption_rate }}%</span>
              </div>
            </div>
            <a-empty v-else description="暂无数据" />
          </a-card>
        </a-col>
        <!-- 业务影响 -->
        <a-col :xs="24" :sm="24" :md="12">
          <a-card class="dashboard-card ai-card" title="业务影响">
            <div v-if="impact.top_skills && impact.top_skills.length">
              <div v-for="item in impact.top_skills" :key="item.skill_id" class="impact-row">
                <span class="impact-name">{{ item.skill_id }}</span>
                <span class="impact-val">{{ item.total_amount || '-' }}</span>
              </div>
            </div>
            <a-empty v-else description="暂无数据" />
          </a-card>
        </a-col>
      </a-row>

      <!-- 数据源健康 -->
      <a-card class="dashboard-card ai-card" title="数据源健康">
        <a-table v-if="dataHealth.length" :data="dataHealth" :pagination="false" size="small">
          <template #columns>
            <a-table-column title="数据源">
              <template #cell="{ record }">{{ cleanDatasourceName(record.name) }}</template>
            </a-table-column>
            <a-table-column title="状态" :width="120">
              <template #cell="{ record }">
                <a-tag :color="record.is_stale ? 'red' : 'green'">{{ record.is_stale ? '数据过期' : '正常' }}</a-tag>
              </template>
            </a-table-column>
            <a-table-column title="部门" data-index="department" />
            <a-table-column title="过期时长" :width="120">
              <template #cell="{ record }">{{ record.hours_since_update ? `${record.hours_since_update}h` : '-' }}</template>
            </a-table-column>
          </template>
        </a-table>
        <SfEmptyState v-else description="暂无数据源" />
      </a-card>

      <!-- 变更热力图 -->
      <a-card class="dashboard-card ai-card" title="变更频率" style="margin-top: 16px">
        <SfLoadingState v-if="heatmapLoading" height="120px" />
        <template v-else>
          <a-table v-if="heatmapData.length" :data="heatmapData" :pagination="false" size="small">
            <template #columns>
              <a-table-column title="Skill" data-index="skill_id" />
              <a-table-column title="编辑次数" data-index="edit_count" :width="100" />
              <a-table-column title="拒绝次数" data-index="reject_count" :width="100" />
              <a-table-column title="频率" :width="200">
                <template #cell="{ record }">
                  <a-progress :percent="Math.min(record.edit_count / maxEdits * 100, 100)" size="small" :show-text="false"
                    :color="record.reject_count > 2 ? 'rgb(var(--red-6))' : 'rgb(var(--primary-6))'" />
                </template>
              </a-table-column>
            </template>
          </a-table>
          <SfEmptyState v-else description="暂无变更数据" />
        </template>
      </a-card>

      <!-- 健康排行榜 -->
      <a-card class="dashboard-card ai-card" title="Skill 健康排行" style="margin-top: 16px">
        <SfLoadingState v-if="healthLoading" height="120px" />
        <template v-else>
          <a-table v-if="healthData.length" :data="healthData" :pagination="false" size="small">
            <template #columns>
              <a-table-column title="Skill" data-index="skill_id" />
              <a-table-column :width="80">
                <template #title><a-tooltip content="综合健康评分（满分100）">评分</a-tooltip></template>
                <template #cell="{ record }">
                  <a-tag :color="record.score >= 80 ? 'green' : record.score >= 60 ? 'orange' : 'red'">{{ record.score }}</a-tag>
                </template>
              </a-table-column>
              <a-table-column title="等级" data-index="grade" :width="60" />
              <a-table-column :width="80">
                <template #title><a-tooltip content="最近30天执行结果被采纳的比例">采纳率</a-tooltip></template>
                <template #cell="{ record }">{{ record.details?.adoption || 0 }}%</template>
              </a-table-column>
              <a-table-column :width="80">
                <template #title><a-tooltip content="最近30天执行成功率">成功率</a-tooltip></template>
                <template #cell="{ record }">{{ record.details?.success_rate || 0 }}%</template>
              </a-table-column>
            </template>
          </a-table>
          <SfEmptyState v-else description="暂无健康数据" />
        </template>
      </a-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, defineAsyncComponent, onMounted } from 'vue'
import { Message } from '@arco-design/web-vue'
import { dashboardApi as rawDashboardApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { SfLoadingState, SfEmptyState } from '@/components/common'

// ECharts 懒加载：只有访问 Dashboard 才下载 echarts chunk
const TrendChart = defineAsyncComponent(() => import('@/components/charts/TrendChart.vue'))

const dashboardApi: any = rawDashboardApi
const userStore: any = useUserStore()
const canViewCosts = computed(() => userStore.isAdmin)

const loading = ref(false)
const days = ref(30)
const overview = ref<any>({})
const adoption = ref<any[]>([])
const impact = ref<any>({})
const dataHealth = ref<any[]>([])
const trends = ref<any[]>([])
const heatmapData = ref<any[]>([])
const heatmapLoading = ref(false)
const healthData = ref<any[]>([])
const healthLoading = ref(false)
const maxEdits = computed(() => Math.max(...heatmapData.value.map((d: any) => d.edit_count || 1), 1))

// 去掉数据源名中的 "（自动创建）" 后缀 — 技术细节不应暴露给用户
function cleanDatasourceName(name: string): string {
  if (!name) return '-'
  return name.replace(/（自动创建）|\(自动创建\)|（auto-created）|\(auto-created\)/gi, '').trim()
}

// ECharts 用的数据（labels + values），不再做 SVG 手绘坐标
const trendLabels = computed(() => trends.value.map((d: any) => (d.date || '').slice(5)))
const trendExecValues = computed(() => trends.value.map((d: any) => Number(d.executions || 0)))
const trendAdoptionValues = computed(() =>
  trends.value.map((d: any) => Number(((d.adoption_rate || 0) * 100).toFixed(1))),
)

async function loadHeatmap() {
  heatmapLoading.value = true
  try {
    const res = await dashboardApi.changeHeatmap({ days: days.value })
    heatmapData.value = res?.items || (Array.isArray(res) ? res : [])
  } catch { heatmapData.value = [] }
  finally { heatmapLoading.value = false }
}

async function loadHealth() {
  healthLoading.value = true
  try {
    const res = await dashboardApi.health()
    healthData.value = Array.isArray(res) ? res : (res?.items || [])
  } catch { healthData.value = [] }
  finally { healthLoading.value = false }
}

async function loadAll() {
  loading.value = true
  try {
    const [ov, ad, imp, dh, tr] = await Promise.all([
      dashboardApi.overview({ days: days.value }),
      dashboardApi.adoption({ days: days.value }),
      dashboardApi.impact({ days: days.value }),
      dashboardApi.dataHealth(),
      dashboardApi.trends({ days: days.value }),
    ])
    overview.value = ov || {}
    adoption.value = Array.isArray(ad) ? ad : []
    impact.value = imp || {}
    dataHealth.value = dh?.sources || []
    // 后端返回 {dates, executions, adoptions} 三个平行数组，转成 [{date, executions, adoption_rate}] 供 chart 使用
    if (tr && Array.isArray(tr.dates)) {
      trends.value = tr.dates.map((date: string, i: number) => {
        const exec = Number(tr.executions?.[i] || 0)
        const adopt = Number(tr.adoptions?.[i] || 0)
        return {
          date,
          executions: exec,
          adoption_rate: exec > 0 ? Math.round((adopt / exec) * 100) : 0,
        }
      })
    } else {
      trends.value = Array.isArray(tr) ? tr : (tr?.daily || [])
    }
  } catch (e) {
    Message.error(String((e as Record<string, unknown>)?._message || '加载失败'))
  } finally {
    loading.value = false
  }
  loadHeatmap()
  loadHealth()
}

onMounted(loadAll)
</script>

<style scoped>
.dashboard-page {
  min-height: 100%;
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}
.dashboard-head {
  align-items: center;
}
.dashboard-body {
  min-width: 0;
}
.dashboard-card {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}
.dashboard-card :deep(.arco-card-header-title) {
  font-weight: 500 !important;
  font-size: 13px !important;
  color: var(--ai-ink-1) !important;
  letter-spacing: -0.005em;
}

/* 概览 KPI 条：参照 InboxHeader / AdminCodexPlugin */
.dashboard-kpi-strip {
  display: flex;
  align-items: stretch;
  padding: 0;
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
.kpi-suffix {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-4);
  margin-left: 2px;
  font-family: var(--ai-font-mono);
}
.kpi-cell.kpi-ok .kpi-value { color: var(--ai-ok); }
.kpi-cell.kpi-accent .kpi-value { color: var(--ai-accent-ink); }
.kpi-cell.kpi-warn .kpi-value { color: var(--ai-warn); }

.section-row { margin-bottom: 16px; }

/* 成本迁出提示 */
.cost-moved-hint {
  margin-bottom: 14px;
  border-radius: var(--ai-radius);
}

/* 采纳率行 */
.adoption-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--ai-border);
  font-size: 12.5px;
}
.adoption-row:last-child { border-bottom: none; }
.adoption-name {
  min-width: 120px;
  max-width: 180px;
  flex: 0 0 auto;
  font-weight: 500;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.adoption-pct {
  width: 50px;
  text-align: right;
  font-weight: 600;
  color: var(--ai-accent-ink);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
/* 0% 时降级到 muted 灰，避免"无数据"用蓝色信号色误导（同 SfStatChip value=0 模式） */
.adoption-pct.muted {
  color: var(--ai-ink-4);
}

/* 业务影响行 */
.impact-row {
  display: flex;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid var(--ai-border);
  font-size: 12.5px;
}
.impact-row:last-child { border-bottom: none; }
.impact-name {
  font-weight: 500;
  color: var(--ai-ink-1);
}
.impact-val {
  color: var(--ai-ink-2);
  font-weight: 500;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

/* arco progress 颜色统一 */
.dashboard-page :deep(.arco-progress-line-bar-buffer) {
  background: var(--ai-accent);
}

/* a-tag → ai-pill 风格 */
.dashboard-page :deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
</style>
