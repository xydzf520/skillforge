<template>
  <div class="page-container cd-page" data-testid="contract-drift-page">
    <div class="page-header">
      <div class="page-heading">
        <div class="page-kicker">治理视图</div>
        <h2 class="page-title">契约偏离看板</h2>
        <p class="page-subtitle">Skill 运行时实际输出与 `contract.output_schema` 不一致的汇总。warn-only，不阻塞业务。</p>
      </div>
      <div class="cd-filters">
        <a-select v-model="days" :options="dayOpts" size="small" style="width: 120px" @change="load" />
        <a-select v-model="ackFilter" :options="ackOpts" size="small" style="width: 140px" @change="load" />
        <a-button size="small" @click="load" :loading="loading">刷新</a-button>
      </div>
    </div>

    <div class="page-chip-row cd-chip-row">
      <span class="page-chip is-info">时间范围 {{ dayLabel }}</span>
      <span class="page-chip">处理状态 {{ ackLabel }}</span>
      <span class="page-chip is-warning">最近偏离 {{ latestDetectedAt }}</span>
    </div>

    <!-- KPI 条 -->
    <div class="cd-kpi-strip">
      <div class="kpi-cell">
        <div class="kpi-label">涉及 Skill</div>
        <div class="kpi-value">{{ items.length }}</div>
      </div>
      <div class="kpi-cell" :class="{ 'kpi-bad': totalDriftCount > 0 }">
        <div class="kpi-label">偏离总数</div>
        <div class="kpi-value">{{ totalDriftCount }}</div>
      </div>
      <div class="kpi-cell" :class="{ 'kpi-ok': acknowledgedCount > 0 }">
        <div class="kpi-label">已处理</div>
        <div class="kpi-value">{{ acknowledgedCount }}</div>
      </div>
      <div class="kpi-cell" :class="{ 'kpi-warn': pendingDriftCount > 0 }">
        <div class="kpi-label">待处理</div>
        <div class="kpi-value">{{ pendingDriftCount }}</div>
      </div>
    </div>

    <a-card class="page-list-card">
      <a-alert class="cd-alert" type="warning">
        该看板只用于观测 contract 偏离，不会阻塞运行链路。需要正式更新 schema 时，请走审核单。
      </a-alert>

      <a-table :data="items" :pagination="false" :loading="loading" row-key="skill_id" size="small" class="page-list-table">
        <template #columns>
          <a-table-column title="Skill" data-index="name">
            <template #cell="{ record }">
              <router-link :to="`/skill/${record.skill_id}`" class="cd-link">{{ record.name }}</router-link>
              <div class="cd-skill-meta">{{ record.skill_id }} · {{ record.department || '-' }}</div>
            </template>
          </a-table-column>
          <a-table-column title="Drift 数" align="right">
            <template #cell="{ record }">
              <div class="cd-count-block">
                <strong class="cd-count">{{ record.drift_count }}</strong>
                <span class="cd-ack">已处理 {{ record.ack_count }}</span>
              </div>
            </template>
          </a-table-column>
          <a-table-column title="最近一条" data-index="latest.detected_at">
            <template #cell="{ record }">
              <div class="cd-latest">
                <div class="cd-time">{{ formatTime(record.latest?.detected_at) }}</div>
                <div class="cd-error">{{ record.latest?.first_error || '-' }}</div>
                <div v-if="record.latest?.error_count > 1" class="cd-more">+{{ record.latest.error_count - 1 }} 条</div>
              </div>
            </template>
          </a-table-column>
          <a-table-column title="操作" align="right">
            <template #cell="{ record }">
              <a-space size="mini" wrap>
                <a-button size="mini" @click="viewDetail(record.latest?.drift_id)" :disabled="!record.latest?.drift_id">详情</a-button>
                <a-button size="mini" type="text" @click="acknowledge(record.latest?.drift_id)" :disabled="!record.latest?.drift_id">标记处理</a-button>
                <a-button size="mini" type="primary" status="warning" @click="proposeSchema(record.skill_id)">提交审核（反向覆盖）</a-button>
              </a-space>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </a-card>

    <a-modal v-model:visible="detailVisible" title="Drift 详情" :footer="false" width="640px">
      <div v-if="detail" class="cd-detail">
        <div><strong>Skill</strong>: {{ detail.skill_id }}</div>
        <div><strong>Run</strong>: {{ detail.run_id }}</div>
        <div><strong>时间</strong>: {{ formatTime(detail.detected_at) }}</div>
        <div><strong>状态</strong>: {{ detail.acknowledged ? `已处理 (${detail.acknowledged_by})` : '未处理' }}</div>
        <div class="cd-errors-title">Schema 错误（{{ detail.schema_errors?.length || 0 }} 条）</div>
        <div class="cd-errors">
          <div v-for="(err, i) in detail.schema_errors" :key="i" class="cd-err-row">{{ err }}</div>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Message, Modal } from '@arco-design/web-vue'
import request from '@/api/request'
import { formatTime as formatBjtTime, toDate } from '@/utils/format'

const router = useRouter()

const days = ref(7)
const ackFilter = ref<null | boolean>(null)
const loading = ref(false)
const items = ref<any[]>([])
const detail = ref<any>(null)
const detailVisible = ref(false)

const dayOpts = [
  { label: '最近 1 天', value: 1 },
  { label: '最近 7 天', value: 7 },
  { label: '最近 30 天', value: 30 },
  { label: '最近 90 天', value: 90 },
]
const ackOpts = [
  { label: '全部', value: null },
  { label: '未处理', value: false },
  { label: '已处理', value: true },
]

const dayLabel = computed(() => dayOpts.find((option) => option.value === days.value)?.label || `最近 ${days.value} 天`)
const ackLabel = computed(() => ackOpts.find((option) => option.value === ackFilter.value)?.label || '全部')
const totalDriftCount = computed(() => items.value.reduce((sum, item) => sum + Number(item?.drift_count || 0), 0))
const acknowledgedCount = computed(() => items.value.reduce((sum, item) => sum + Number(item?.ack_count || 0), 0))
const pendingDriftCount = computed(() => {
  return items.value.reduce((sum, item) => sum + Math.max(Number(item?.drift_count || 0) - Number(item?.ack_count || 0), 0), 0)
})
const latestDetectedAt = computed(() => {
  const timestamps = items.value
    .map((item) => item?.latest?.detected_at)
    .filter(Boolean)
    .map((value) => toDate(String(value))?.getTime() ?? Number.NaN)
    .filter((value) => !Number.isNaN(value))
  if (!timestamps.length) return '暂无'
  return formatTime(Math.max(...timestamps))
})

function formatTime(s: string | number | null): string {
  return formatBjtTime(s)
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const params: any = { days: days.value }
    if (ackFilter.value !== null) params.acknowledged = ackFilter.value
    const data = await request.get<any>('/dashboard/contract-drift', { params })
    items.value = data.items || []
  } catch {
    // request 拦截器已弹出友好 Message，不再重复
  } finally {
    loading.value = false
  }
}

async function viewDetail(driftId: number | null): Promise<void> {
  if (!driftId) return
  try {
    const data = await request.get<any>(`/dashboard/contract-drift/${driftId}`)
    detail.value = data
    detailVisible.value = true
  } catch {
    // 拦截器已提示
  }
}

async function acknowledge(driftId: number | null): Promise<void> {
  if (!driftId) return
  try {
    await request.post(`/dashboard/contract-drift/${driftId}/acknowledge`)
    Message.success('已标记为处理')
    await load()
  } catch {
    // 拦截器已提示
  }
}

async function proposeSchema(skillId: string): Promise<void> {
  Modal.warning({
    title: '提交"反向覆盖 output_schema"审核单？',
    content: `平台会从最近 10 次非沙箱执行的 output 反推一份保守 jsonschema，并创建一条审核单。审核人在 /reviews 批准后才会真正修改 ${skillId}/contract.json。`,
    okText: '提交审核',
    cancelText: '取消',
    onOk: async () => {
      try {
        const data = await request.post<any>(`/dashboard/contract-drift/${skillId}/propose-schema`)
        Message.success({
          content: `审核单 #${data.review_id} 已创建，样本数 ${data.sample_count}`,
          duration: 3000,
        })
        // 直接跳到对应审核单
        router.push(`/skills/${skillId}?perspective=review&review_id=${data.review_id}`)
      } catch {
        // 拦截器已提示
      }
    },
  })
}

onMounted(load)
</script>

<style scoped>
.cd-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}

/* 设计稿 page chrome 覆盖 */
.cd-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.cd-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
}
.cd-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.cd-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

.cd-filters {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

/* page-chip-row 重塑为 ai-pill */
.cd-chip-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.cd-chip-row :deep(.page-chip) {
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  font-size: 11.5px;
  font-weight: 500;
  letter-spacing: 0;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  display: inline-flex;
  align-items: center;
  font-variant-numeric: tabular-nums;
}
.cd-chip-row :deep(.page-chip.is-info) {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}
.cd-chip-row :deep(.page-chip.is-warning) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}

/* KPI 条 */
.cd-kpi-strip {
  display: flex;
  align-items: stretch;
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
.kpi-cell.kpi-warn .kpi-value { color: var(--ai-warn); }
.kpi-cell.kpi-bad .kpi-value { color: var(--ai-bad); }

/* 警告条 */
.cd-alert {
  margin-bottom: 14px;
}
.cd-page :deep(.arco-alert) {
  border-radius: var(--ai-radius);
}
.cd-page :deep(.arco-alert-warning) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border: 1px solid transparent;
}

.cd-count-block {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
}

.cd-link {
  font-weight: 500;
  color: var(--ai-ink-1);
  font-size: 12.5px;
  text-decoration: none;
}
.cd-link:hover {
  color: var(--ai-accent-ink);
}

.cd-skill-meta {
  font-size: 11px;
  color: var(--ai-ink-4);
  margin-top: 2px;
  font-family: var(--ai-font-mono);
}

.cd-count {
  font-size: 15px;
  color: var(--ai-bad);
  font-family: var(--ai-font-mono);
  font-weight: 600;
}

.cd-ack {
  font-size: 11px;
  color: var(--ai-ok);
  font-weight: 500;
  font-family: var(--ai-font-mono);
}

.cd-latest .cd-time {
  font-size: 11px;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
}

.cd-latest .cd-error {
  font-size: 12.5px;
  color: var(--ai-ink-1);
  margin-top: 2px;
}

.cd-latest .cd-more {
  font-size: 11px;
  color: var(--ai-warn);
  margin-top: 2px;
  font-family: var(--ai-font-mono);
}

.cd-detail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  font-size: 13px;
}

.cd-errors-title {
  margin-top: 12px;
  font-weight: 500;
  color: var(--ai-ink-4);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.cd-errors {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.cd-err-row {
  padding: 10px 12px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-1);
  word-break: break-all;
}

/* 表格密集化 */
.cd-page :deep(.arco-table-th) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11px !important;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 8px 10px !important;
}
.cd-page :deep(.arco-table-td) {
  font-size: 12.5px;
  padding: 10px 10px !important;
  border-bottom: 1px solid var(--ai-border) !important;
}
.cd-page :deep(.arco-table-tr:hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

.cd-page :deep(.arco-btn) {
  border-radius: 6px;
  font-weight: 500;
  font-size: 12px;
  box-shadow: none;
}
.cd-page :deep(.arco-btn-primary) {
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
}
.cd-page :deep(.arco-btn-primary.arco-btn-status-warning) {
  background: var(--ai-warn);
  border-color: var(--ai-warn);
  color: white;
}

@media (max-width: 768px) {
  .cd-count-block {
    align-items: flex-start;
    flex-direction: column;
    gap: 2px;
  }
  .cd-kpi-strip {
    flex-wrap: wrap;
  }
  .kpi-cell {
    flex: 1 1 50%;
    border-left: 0;
    border-top: 1px solid var(--ai-border);
  }
  .kpi-cell:nth-child(-n+2) { border-top: 0; }
}

@media (max-width: 768px) {
  .cd-count-block {
    align-items: flex-start;
    flex-direction: column;
    gap: 2px;
  }
}
</style>
