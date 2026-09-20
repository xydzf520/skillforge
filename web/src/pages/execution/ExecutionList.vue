<template>
  <div class="execution-list-page ai-main">
    <div class="execution-list-head ai-pagehead">
      <div>
        <div class="ai-crumbs">Skills · 执行监控</div>
        <h2 class="ai-title">执行监控</h2>
        <p class="ai-sub">查看手工 / 定时 / API 触发的运行记录，支持 run 对比</p>
      </div>
    </div>

    <div class="execution-list-body ai-pagebody">
    <section class="execution-list-card ai-card">
      <!-- 筛选栏 -->
      <div class="filter-bar">
        <a-space>
          <a-select v-model="filters.status" placeholder="状态" allow-clear style="width: 130px" @change="loadRuns">
            <a-option value="success">成功</a-option>
            <a-option value="failed">失败</a-option>
            <a-option value="running">运行中</a-option>
            <a-option value="pending">等待中</a-option>
          </a-select>
          <a-select v-model="filters.trigger_type" placeholder="触发方式" allow-clear style="width: 130px" @change="loadRuns">
            <a-option value="manual">手动</a-option>
            <a-option value="cron">定时</a-option>
            <a-option value="api">API</a-option>
          </a-select>
        </a-space>
        <button class="ai-btn" :disabled="selectedKeys.length !== 2 || comparing" @click="handleCompare">
          <SfShellIcon name="flow" />
          <span>对比选中 ({{ selectedKeys.length }}/2)</span>
        </button>
      </div>

      <a-table class="page-list-table table-scroll-x" :data="runs" :pagination="false" row-key="id" :loading="loading"
               :row-selection="{ type: 'checkbox', showCheckedAll: false }" v-model:selectedKeys="selectedKeys"
               :scroll="{ x: '100%' }">
        <template #columns>
          <a-table-column title="执行ID" data-index="id" :width="100">
            <template #cell="{ record }">
              <a-link @click="$router.push(`/execution/${record.id}`)">{{ record.id.slice(0, 8) }}</a-link>
            </template>
          </a-table-column>
          <a-table-column title="Skill" data-index="skill_id" ellipsis>
            <template #cell="{ record }">{{ record.skill_id || record.playbook_id || '—' }}</template>
          </a-table-column>
          <a-table-column title="触发" data-index="trigger_type" :width="120">
            <template #cell="{ record }">
              <a-tooltip :content="`原始值：${record.trigger_type}`" mini>
                <a-tag size="small">{{ formatTrigger(record.trigger_type) }}</a-tag>
              </a-tooltip>
            </template>
          </a-table-column>
          <a-table-column title="状态" data-index="status" :width="80">
            <template #cell="{ record }">
              <a-tag :color="(runStatusColor as Record<string, string>)[record.status]" size="small">{{ (runStatusLabel as Record<string, string>)[record.status] || record.status }}</a-tag>
            </template>
          </a-table-column>
          <a-table-column title="时间" data-index="started_at" :width="140">
            <template #cell="{ record }">
              <a-tooltip :content="formatTime(record.started_at)" mini>
                <span class="time-text">{{ relativeTime(record.started_at) }}</span>
              </a-tooltip>
            </template>
          </a-table-column>
          <a-table-column title="" :width="80">
            <template #cell="{ record }">
              <a-button type="text" size="small" @click="$router.push(`/execution/${record.id}`)">详情</a-button>
            </template>
          </a-table-column>
        </template>
      </a-table>

      <div class="bottom-bar">
        <div class="stats-inline">
          <SfStatChip label="总执行" :value="stats.total" />
          <SfStatChip label="成功" :value="stats.success" tone="success" />
          <SfStatChip label="失败" :value="stats.failed" tone="danger" />
          <SfStatChip label="运行中" :value="stats.running" tone="info" />
        </div>
        <a-pagination :current="pagination.current" :page-size="pagination.pageSize" :total="pagination.total" @change="onPageChange" size="small" />
      </div>
    </section>
    </div>

    <!-- 执行对比弹窗 -->
    <a-modal v-model:visible="showCompare" title="执行对比" width="900px" :footer="false">
      <template v-if="compareData">
        <a-descriptions :column="2" bordered size="small" style="margin-bottom: 16px">
          <a-descriptions-item label="执行1">{{ compareData.run1?.run_id?.slice(0, 8) }}</a-descriptions-item>
          <a-descriptions-item label="执行2">{{ compareData.run2?.run_id?.slice(0, 8) }}</a-descriptions-item>
          <a-descriptions-item label="Skill">{{ compareData.run1?.skill_id }}</a-descriptions-item>
          <a-descriptions-item label="Skill">{{ compareData.run2?.skill_id }}</a-descriptions-item>
        </a-descriptions>
        <a-divider>输出差异</a-divider>
        <a-tag v-if="compareData.is_same_output" color="green">输出完全一致</a-tag>
        <a-table v-else :data="compareData.output_diff || []" :pagination="false" size="small">
          <template #columns>
            <a-table-column title="字段" data-index="key" />
            <a-table-column title="执行1" data-index="old_value">
              <template #cell="{ record }"><pre class="diff-pre">{{ JSON.stringify(record.old_value, null, 2) }}</pre></template>
            </a-table-column>
            <a-table-column title="执行2" data-index="new_value">
              <template #cell="{ record }"><pre class="diff-pre">{{ JSON.stringify(record.new_value, null, 2) }}</pre></template>
            </a-table-column>
          </template>
        </a-table>
      </template>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { Message } from '@arco-design/web-vue'
import { executionApi as rawExecutionApi } from '@/api'
import { formatTime, relativeTime } from '@/utils/format'
import { runStatusLabel, runStatusColor } from '@/utils/constants'
import { SfStatChip } from '@/components/sf'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

const executionApi: any = rawExecutionApi

// 把后端 trigger_type 字符串（manual:admin / e2e-manual / schedule:daily 等）翻译为人类可读
function formatTrigger(raw: string | undefined): string {
  if (!raw) return '-'
  const r = String(raw)
  if (r.startsWith('manual:')) return `手工 · ${r.slice(7)}`
  if (r === 'manual') return '手工'
  if (r.startsWith('e2e')) return 'E2E 回归'
  if (r.startsWith('schedule')) return '定时'
  if (r.startsWith('webhook')) return '外部触发'
  if (r.startsWith('dingtalk')) return '钉钉触发'
  if (r.startsWith('api')) return 'API'
  return r
}

const loading = ref(false)
const runs = ref<any[]>([])
const filters = reactive<{ status?: string; trigger_type?: string }>({ status: undefined, trigger_type: undefined })
const pagination = reactive({ current: 1, pageSize: 20, total: 0 })

// ── 执行对比 ──
const selectedKeys = ref<string[]>([])
const comparing = ref(false)
const showCompare = ref(false)
const compareData = ref<any>(null)

async function handleCompare() {
  if (selectedKeys.value.length !== 2) return
  comparing.value = true
  try {
    compareData.value = await executionApi.compare(selectedKeys.value[0], selectedKeys.value[1])
    showCompare.value = true
  } catch (e: any) {
    Message.error(e._message || '对比失败')
  } finally {
    comparing.value = false
  }
}

// 全局执行统计（服务端聚合，不受当前分页影响）
const stats = ref({ total: 0, success: 0, failed: 0, running: 0, pending: 0 })

async function loadStats() {
  try {
    const s = await executionApi.runsStats()
    stats.value = { ...stats.value, ...s }
  } catch {
    // 统计失败不挡主列表，保持旧值
  }
}

async function loadRuns() {
  loading.value = true
  try {
    const res = await executionApi.listRuns({
      status: filters.status || undefined,
      trigger_type: filters.trigger_type || undefined,
      page: pagination.current,
      page_size: pagination.pageSize,
    })
    runs.value = Array.isArray(res) ? res : (res.items || [])
    if (res.total !== undefined) pagination.total = res.total
  } catch (e: any) {
    Message.error(e._message || '加载失败')
  } finally {
    loading.value = false
  }
}

function onPageChange(page: number) {
  pagination.current = page
  loadRuns()
}

onMounted(() => {
  loadRuns()
  loadStats()
})
</script>

<style scoped>
.execution-list-page {
  min-height: 100%;
}
.execution-list-body {
  min-width: 0;
}
.execution-list-card {
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  box-shadow: none;
  padding: 16px;
}
.filter-bar .ai-btn svg {
  width: 14px;
  height: 14px;
  flex: 0 0 auto;
}

.table-scroll-x { overflow-x: auto; }
.time-text { font-size: 12px; }
.diff-pre { margin: 0; font-size: 12px; }
.bottom-bar { display: flex; align-items: center; justify-content: space-between; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--ai-border); }
.stats-inline { display: flex; gap: 6px; flex-wrap: wrap; }
.stat-chip { display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; border-radius: 6px; font-size: 12px; cursor: default; background: var(--ai-surface-2); color: var(--ai-ink-3); }
.stat-chip b { font-size: 13px; }
.stat-green { background: var(--ai-ok-soft); color: var(--ai-ok); }
.stat-red { background: var(--ai-bad-soft); color: var(--ai-bad); }
.stat-orange { background: var(--ai-warn-soft); color: var(--ai-warn); }
.stat-blue { background: var(--ai-accent-soft); color: var(--ai-accent-ink); }
</style>
