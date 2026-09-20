<template>
  <div class="drift-tab">
    <div class="drift-toolbar">
      <a-input v-model="platform" allow-clear placeholder="platform" style="width: 180px" @press-enter="load" />
      <a-input v-model="shopId" allow-clear placeholder="shop_id" style="width: 180px" @press-enter="load" />
      <a-button :loading="loading" @click="load">刷新</a-button>
    </div>

    <a-table :data="rows" :loading="loading" :pagination="{ pageSize: 12 }" row-key="id">
      <template #empty>
        <SfEmptyState
          icon="trend"
          title="暂无 Drift"
          description="当前筛选下没有平台 API 漂移告警"
          hint="漂移监测发现字段新增、缺失或结构变化后会在这里出现。"
        />
      </template>
      <template #columns>
        <a-table-column title="平台" data-index="platform" :width="100" />
        <a-table-column title="店铺" data-index="shop_id" :width="120" />
        <a-table-column title="变化" :width="140">
          <template #cell="{ record }">
            <a-tag :color="changeColor(record.change_type)">{{ record.change_type || 'snapshot' }}</a-tag>
          </template>
        </a-table-column>
        <a-table-column title="ACK" :width="110">
          <template #cell="{ record }">
            <a-tag :color="isAcked(record) ? 'green' : 'orange'">
              {{ isAcked(record) ? '已 ACK' : '待 ACK' }}
            </a-tag>
          </template>
        </a-table-column>
        <a-table-column title="Endpoint">
          <template #cell="{ record }">
            <div class="endpoint">{{ record.endpoint }}</div>
          </template>
        </a-table-column>
        <a-table-column title="字段变化">
          <template #cell="{ record }">
            <a-space wrap>
              <a-tag v-for="field in record.missing_fields || []" :key="`m-${field}`" color="red" size="small">- {{ field }}</a-tag>
              <a-tag v-for="field in record.added_fields || []" :key="`a-${field}`" color="green" size="small">+ {{ field }}</a-tag>
              <span v-if="!(record.missing_fields || []).length && !(record.added_fields || []).length" class="muted">-</span>
            </a-space>
          </template>
        </a-table-column>
        <a-table-column title="Row Count" :width="120">
          <template #cell="{ record }">
            {{ record.previous_row_count ?? '-' }} → {{ record.row_count ?? '-' }}
          </template>
        </a-table-column>
        <a-table-column title="发现时间" data-index="latest_snapshot_at" :width="170" />
        <a-table-column title="处理结果" :width="150">
          <template #cell="{ record }">
            <span class="result-text">{{ handlingResultText(record) }}</span>
          </template>
        </a-table-column>
        <a-table-column title="操作" :width="180" fixed="right">
          <template #cell="{ record }">
            <a-space>
              <a-button size="mini" @click="openDetail(record)">详情</a-button>
              <a-button size="mini" type="primary" :disabled="isAcked(record)" @click="ack(record)">ACK</a-button>
            </a-space>
          </template>
        </a-table-column>
      </template>
    </a-table>

    <a-drawer v-model:visible="detailVisible" :width="720" title="Drift 详情" unmount-on-close>
      <template v-if="selected">
        <div class="detail-head">
          <div>
            <div class="detail-title">{{ selected.endpoint || selected.endpoint_hash }}</div>
            <div class="muted">{{ selected.platform || '-' }} / {{ selected.shop_id || '-' }}</div>
          </div>
          <a-tag :color="isAcked(selected) ? 'green' : 'orange'">{{ isAcked(selected) ? '已 ACK' : '待 ACK' }}</a-tag>
        </div>

        <a-descriptions :column="2" bordered size="small" class="detail-desc">
          <a-descriptions-item label="变化类型">{{ selected.change_type || 'snapshot' }}</a-descriptions-item>
          <a-descriptions-item label="发现时间">{{ selected.latest_snapshot_at || selected.first_seen_at || '-' }}</a-descriptions-item>
          <a-descriptions-item label="ACK 人">{{ selected.acknowledged_by || '-' }}</a-descriptions-item>
          <a-descriptions-item label="ACK 时间">{{ selected.acknowledged_at || '-' }}</a-descriptions-item>
          <a-descriptions-item label="关联复核">
            <router-link v-if="relatedTodoId(selected)" :to="`/inbox/todos/${relatedTodoId(selected)}`">
              待办 #{{ relatedTodoId(selected) }}
            </router-link>
            <span v-else class="muted">暂无</span>
          </a-descriptions-item>
          <a-descriptions-item label="处理结果">{{ handlingResultText(selected) }}</a-descriptions-item>
        </a-descriptions>

        <div class="diff-grid">
          <div class="diff-panel">
            <div class="diff-title">Before</div>
            <a-tag v-for="field in selected.before_keys || []" :key="`b-${field}`" size="small">{{ field }}</a-tag>
            <div v-if="!(selected.before_keys || []).length" class="diff-empty">
              <SfShellIcon name="database" />
              <span>无 before keys</span>
            </div>
          </div>
          <div class="diff-panel">
            <div class="diff-title">After</div>
            <a-tag
              v-for="field in selected.after_keys || []"
              :key="`f-${field}`"
              :color="(selected.added_fields || []).includes(field) ? 'green' : undefined"
              size="small"
            >
              {{ field }}
            </a-tag>
            <div v-if="!(selected.after_keys || []).length" class="diff-empty">
              <SfShellIcon name="database" />
              <span>无 after keys</span>
            </div>
          </div>
        </div>

        <a-alert v-if="(selected.missing_fields || []).length" type="warning" class="detail-alert">
          缺失字段：{{ (selected.missing_fields || []).join('、') }}
        </a-alert>
        <a-alert v-if="(selected.added_fields || []).length" type="success" class="detail-alert">
          新增字段：{{ (selected.added_fields || []).join('、') }}
        </a-alert>

      </template>
      <template #footer>
        <a-space v-if="selected">
          <router-link v-if="relatedTodoId(selected)" :to="`/inbox/todos/${relatedTodoId(selected)}`">
            <a-button>打开复核待办</a-button>
          </router-link>
          <a-button type="primary" :disabled="isAcked(selected)" @click="ack(selected)">ACK</a-button>
        </a-space>
      </template>
    </a-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { driftAlertApi, type DriftAlertRow } from '@/api'
import { SfEmptyState } from '@/components/common'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

const route = useRoute()
const loading = ref(false)
const platform = ref(String(route.query.platform || ''))
const shopId = ref(String(route.query.shop_id || ''))
const rows = ref<DriftAlertRow[]>([])
const detailVisible = ref(false)
const selected = ref<DriftAlertRow | null>(null)

function changeColor(type?: string): string {
  if (type === 'field_missing') return 'red'
  if (type === 'row_count_spike') return 'orange'
  if (type === 'field_added') return 'green'
  return 'gray'
}

function isAcked(row: DriftAlertRow): boolean {
  return row.acknowledged === true || row.ack_status === 'acked' || Boolean(row.acknowledged_at)
}

function relatedTodoId(row: DriftAlertRow): string {
  return String(row.related_todo_id || row.review_todo_id || row.todo_id || '')
}

function handlingResultText(row: DriftAlertRow): string {
  const value = row.handling_result ?? row.result ?? row.handling_status ?? row.review_status
  if (!value) return '未处理'
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

function openDetail(row: DriftAlertRow) {
  selected.value = row
  detailVisible.value = true
}

async function load() {
  loading.value = true
  try {
    const res = await driftAlertApi.list({ platform: platform.value || undefined, shop_id: shopId.value || undefined })
    const items = res.items || []
    rows.value = shopId.value ? items.filter((item) => String(item.shop_id || '') === shopId.value) : items
  } finally {
    loading.value = false
  }
}

async function ack(row: DriftAlertRow) {
  await driftAlertApi.ack(row.id, { note: 'ack from Drift Inbox' })
  Message.success('已标记')
  await load()
  if (selected.value?.id === row.id) {
    selected.value = rows.value.find((item) => item.id === row.id) || { ...row, acknowledged: true }
  }
}

onMounted(load)
</script>

<style scoped>
.drift-tab {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.drift-toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
}

.endpoint {
  max-width: 520px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

.result-text {
  display: inline-block;
  max-width: 130px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.detail-title {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  font-weight: 700;
  word-break: break-all;
}

.detail-desc,
.detail-alert {
  margin-bottom: 14px;
}

.diff-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.diff-panel {
  min-height: 180px;
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
}

.diff-title {
  margin-bottom: 8px;
  font-weight: 800;
}

.diff-empty {
  min-height: 120px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 8px;
  color: var(--ai-ink-4);
  font-size: 12px;
}

.diff-empty svg {
  width: 22px;
  height: 22px;
}

.muted {
  color: var(--ai-ink-3);
}
</style>
