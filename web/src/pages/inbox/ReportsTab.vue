<template>
  <div class="inbox-tab">
    <div class="filter-bar">
      <div class="filter-controls">
        <a-input v-model="skillFilter" allow-clear placeholder="Skill ID" style="width: 180px" @press-enter="applyFilters" />
        <a-input v-model="tagFilter" allow-clear placeholder="标签" style="width: 160px" @press-enter="applyFilters" />
        <a-select v-model="channelFilter" style="width: 170px" allow-clear placeholder="渠道" @change="applyFilters">
          <a-option value="dingtalk_card">钉钉卡片</a-option>
          <a-option value="dingtalk_markdown">钉钉 Markdown</a-option>
          <a-option value="email">邮件</a-option>
          <a-option value="feishu">飞书</a-option>
        </a-select>
        <a-select v-model="triggerFilter" style="width: 170px" allow-clear placeholder="触发方式" @change="applyFilters">
          <a-option value="cron">定时触发</a-option>
          <a-option value="manual">手动执行</a-option>
          <a-option value="event">事件触发</a-option>
        </a-select>
        <a-range-picker
          v-model="dateRange"
          value-format="YYYY-MM-DDTHH:mm:ss"
          style="min-width: 240px; max-width: 320px; flex: 1 1 auto;"
          @change="applyFilters"
        />
        <div class="filter-actions">
          <a-button type="primary" @click="applyFilters">筛选</a-button>
          <a-button @click="resetFilters">重置</a-button>
          <a-button @click="loadAll">刷新</a-button>
        </div>
      </div>
      <div class="quick-tags">
        <a-button size="mini" @click="applyTag('degraded')">降级</a-button>
        <a-button size="mini" @click="applyTag('mixed_credentials')">多账号</a-button>
        <a-button size="mini" @click="applyTag('stale_data')">数据过期</a-button>
      </div>
    </div>

    <!-- 设计稿 reports.jsx 顶部 tag pills 行：全部 + 常用 tag 一键过滤 -->
    <div class="report-tag-row" data-testid="report-tag-row">
      <span
        class="ai-pill report-tag-pill"
        :class="{ active: !tagFilter }"
        @click="setTagFilter('')"
      >全部 {{ pagination.total }}</span>
      <span
        v-for="tag in topTags"
        :key="tag.label"
        class="ai-pill report-tag-pill"
        :class="{ active: tagFilter === tag.value }"
        @click="setTagFilter(tag.value)"
      >{{ tag.label }}</span>
    </div>

    <a-spin :loading="loading" style="width: 100%">
      <div class="report-grid">
        <ReportCard v-for="item in items" :key="item.id" :item="item" @open="openReport" />
      </div>
      <SfEmptyState
        v-if="!loading && items.length === 0"
        icon="doc"
        title="暂无报告"
        description="当前筛选下没有 Skill 报告"
        hint="调整筛选条件，或等待订阅的 Skill 运行后产出报告。"
      />
    </a-spin>

    <div class="pagination-wrap">
      <a-pagination
        :current="pagination.current"
        :page-size="pagination.pageSize"
        :total="pagination.total"
        @change="onPageChange"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { useRoute, useRouter } from 'vue-router'
import ReportCard from './ReportCard.vue'
import { SfEmptyState } from '@/components/common'
import { inboxApi } from '@/api'
import { defaultInboxDateRange, readQueryPositiveInt, readQueryText } from './presentation'
import type { ReportListItem } from '@/types/inbox'

const props = defineProps({
  active: { type: Boolean, default: false },
})

const emit = defineEmits<{
  (event: 'latest-report-change', createdAt: string): void
}>()

const route: any = useRoute()
const router: any = useRouter()

const loading = ref(false)
const items = ref<ReportListItem[]>([])
const skillFilter = ref('')
const tagFilter = ref('')
const channelFilter = ref('')
const triggerFilter = ref('')
function defaultDateRange(): string[] {
  return defaultInboxDateRange(7)
}
const dateRange = ref<string[]>(defaultDateRange())
const pagination = ref({ current: 1, pageSize: 20, total: 0 })

function syncFromRoute() {
  skillFilter.value = readQueryText(route.query.report_skill_id)
  tagFilter.value = readQueryText(route.query.report_tag)
  channelFilter.value = readQueryText(route.query.report_channel)
  triggerFilter.value = readQueryText(route.query.report_trigger_type)
  pagination.value.current = readQueryPositiveInt(route.query.report_page, 1)
  const from = readQueryText(route.query.report_date_from)
  const to = readQueryText(route.query.report_date_to)
  dateRange.value = from && to ? [from, to] : defaultDateRange()
}

function hasExplicitDateRange() {
  return Boolean(readQueryText(route.query.report_date_from) || readQueryText(route.query.report_date_to))
}

async function loadAll() {
  loading.value = true
  try {
    if (!hasExplicitDateRange()) {
      dateRange.value = defaultDateRange()
    }
    const data: any = await inboxApi.listReports({
      page: pagination.value.current,
      page_size: pagination.value.pageSize,
      skill_id: skillFilter.value || undefined,
      tag: tagFilter.value || undefined,
      channel: channelFilter.value || undefined,
      trigger_type: triggerFilter.value || undefined,
      date_from: dateRange.value[0] || undefined,
      date_to: dateRange.value[1] || undefined,
    })
    items.value = data.items || []
    pagination.value.total = data.total || 0
    emit('latest-report-change', items.value[0]?.created_at || '')
  } catch (error: any) {
    Message.error(error._message || '加载报告失败')
  } finally {
    loading.value = false
  }
}

function replaceQuery(patch: Record<string, string | undefined>) {
  const nextQuery: Record<string, string> = {}
  Object.entries({ ...route.query, ...patch, tab: 'reports' }).forEach(([key, value]) => {
    const normalized = readQueryText(value)
    if (normalized) nextQuery[key] = normalized
  })
  router.replace({ path: '/inbox', query: nextQuery })
}

function applyFilters() {
  replaceQuery({
    report_page: undefined,
    report_skill_id: skillFilter.value || undefined,
    report_tag: tagFilter.value || undefined,
    report_channel: channelFilter.value || undefined,
    report_trigger_type: triggerFilter.value || undefined,
    report_date_from: dateRange.value[0] || undefined,
    report_date_to: dateRange.value[1] || undefined,
  })
}

function resetFilters() {
  skillFilter.value = ''
  tagFilter.value = ''
  channelFilter.value = ''
  triggerFilter.value = ''
  dateRange.value = []
  applyFilters()
}

function applyTag(tag: string) {
  tagFilter.value = tag
  applyFilters()
}

// 设计稿 reports.jsx 顶部 tag pills：从当前页 items 聚合常用 tag（最多 8 个，按出现频次降序）
const topTags = computed<Array<{ label: string; value: string }>>(() => {
  const counts = new Map<string, number>()
  for (const item of items.value) {
    const tags = ((item as any).tags || []) as string[]
    if (!Array.isArray(tags)) continue
    for (const tag of tags) {
      const t = String(tag || '').trim()
      if (!t) continue
      counts.set(t, (counts.get(t) || 0) + 1)
    }
  }
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([t, n]) => ({ label: `${t} ${n}`, value: t }))
})

function setTagFilter(tag: string) {
  tagFilter.value = tag
  applyFilters()
}

function onPageChange(page: number) {
  replaceQuery({ report_page: String(page) })
}

function openReport(reportId: string) {
  router.push(`/inbox/reports/${encodeURIComponent(reportId)}`)
}

watch(() => route.query, () => {
  syncFromRoute()
  if (props.active) loadAll()
}, { deep: true, immediate: true })

</script>

<style scoped>
.filter-bar {
  margin-bottom: 8px;
  padding: 6px 8px;
  border-radius: 4px;
  border: 1px solid var(--ai-border);
  background: rgba(255, 255, 255, 0.9);
}

.filter-controls {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.filter-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}

.quick-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}

/* 设计稿 reports.jsx：4 列网格 + 12px gap，窄屏自动塌缩 */
.report-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

@media (max-width: 1280px) {
  .report-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
@media (max-width: 960px) {
  .report-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 640px) {
  .report-grid {
    grid-template-columns: 1fr;
  }
}

/* 设计稿 reports.jsx 顶部 tag pills 行 */
.report-tag-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin: 8px 0 12px;
}

.report-tag-pill {
  cursor: pointer;
}
.report-tag-pill.active {
  background: var(--ai-ink-1);
  color: white;
  border-color: var(--ai-ink-1);
}

.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 18px;
}
</style>
