<template>
  <a-card class="page-list-card browser-slot-panel" data-testid="browser-slot-panel">
    <template #title>
      <div class="slot-head">
        <div>
          <div class="slot-title">浏览器 Slot 池</div>
          <div class="slot-subtitle">独立 profile/context 隔离采集；公网出口仅展示 hash 和 egress group。</div>
        </div>
        <a-button size="small" :loading="loading" @click="loadSlots">
          <template #icon><icon-refresh /></template>
        </a-button>
      </div>
    </template>

    <a-alert v-if="loadError" type="warning" class="slot-alert">{{ loadError }}</a-alert>

    <div class="slot-kpis">
      <span class="page-chip is-success">idle {{ slotCounts.idle }}</span>
      <span class="page-chip is-info">busy {{ slotCounts.busy }}</span>
      <span class="page-chip is-warning">maintenance {{ slotCounts.maintenance }}</span>
      <span class="page-chip is-danger">dead {{ slotCounts.dead }}</span>
    </div>

    <a-table
      :data="slots"
      :loading="loading"
      :pagination="false"
      row-key="slot_id"
      size="small"
      class="page-list-table"
    >
      <template #columns>
        <a-table-column title="Slot" :width="160">
          <template #cell="{ record }">
            <div class="slot-main">{{ record.slot_id }}</div>
            <div class="slot-muted">{{ record.node_id || '-' }}</div>
          </template>
        </a-table-column>
        <a-table-column title="出口" :width="180">
          <template #cell="{ record }">
            <div class="slot-main">{{ record.egress_group || '-' }}</div>
            <div class="slot-muted">ip {{ shortHash(record.egress_ip_hash) }}</div>
          </template>
        </a-table-column>
        <a-table-column title="状态" :width="120">
          <template #cell="{ record }">
            <a-tag :color="slotStatusColor(record.status)" size="small">{{ slotStatusLabel(record.status) }}</a-tag>
          </template>
        </a-table-column>
        <a-table-column title="当前凭证" :width="180">
          <template #cell="{ record }">{{ record.current_credential_alias || '-' }}</template>
        </a-table-column>
        <a-table-column title="最近使用" :width="160">
          <template #cell="{ record }">{{ formatTime(record.last_used_at) }}</template>
        </a-table-column>
        <a-table-column title="成功率" :width="160">
          <template #cell="{ record }">
            <div class="slot-rate">
              <span>run {{ formatRatio(record.run_success_rate) }}</span>
              <span>day {{ formatRatio(record.daily_success_rate) }}</span>
            </div>
          </template>
        </a-table-column>
        <a-table-column title="操作" :width="230" fixed="right">
          <template #cell="{ record }">
            <a-space size="mini">
              <a-button size="mini" @click="restartSlot(record.slot_id)">重启</a-button>
              <a-button
                size="mini"
                @click="setMaintenance(record.slot_id, record.status !== 'maintenance')"
              >
                {{ record.status === 'maintenance' ? '恢复' : '维护' }}
              </a-button>
              <a-button size="mini" status="warning" :disabled="record.status !== 'busy'" @click="releaseSlot(record.slot_id)">
                释放
              </a-button>
            </a-space>
          </template>
        </a-table-column>
      </template>
    </a-table>
  </a-card>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconRefresh } from '@arco-design/web-vue/es/icon'
import { browserSlotApi, type BrowserSlotRow } from '@/api'
import { formatTime as formatBjtTime } from '@/utils/format'

const loading = ref(false)
const loadError = ref('')
const slots = ref<BrowserSlotRow[]>([])

const slotCounts = computed(() => {
  const counts = { idle: 0, busy: 0, maintenance: 0, dead: 0 }
  for (const slot of slots.value) {
    const status = String(slot.status || 'idle')
    if (status === 'busy') counts.busy += 1
    else if (status === 'maintenance') counts.maintenance += 1
    else if (status === 'dead' || status === 'error') counts.dead += 1
    else counts.idle += 1
  }
  return counts
})

function normalizeSlots(value: unknown): BrowserSlotRow[] {
  if (Array.isArray(value)) return value as BrowserSlotRow[]
  if (value && typeof value === 'object' && Array.isArray((value as { items?: unknown }).items)) {
    return (value as { items: BrowserSlotRow[] }).items
  }
  return []
}

async function loadSlots() {
  loading.value = true
  loadError.value = ''
  try {
    const res = await browserSlotApi.list()
    slots.value = normalizeSlots(res)
  } catch (error: any) {
    slots.value = []
    loadError.value = error?._message || 'Slot 接口未就绪；当前浏览器仍按 legacy 单会话工作。'
  } finally {
    loading.value = false
  }
}

function shortHash(value?: string) {
  return value ? value.slice(0, 8) : '-'
}

function slotStatusLabel(value?: string) {
  const map: Record<string, string> = {
    idle: '空闲',
    busy: '采集中',
    maintenance: '维护',
    dead: '不可用',
    error: '异常',
  }
  return map[value || ''] || value || '未知'
}

function slotStatusColor(value?: string) {
  if (value === 'idle') return 'green'
  if (value === 'busy') return 'arcoblue'
  if (value === 'maintenance') return 'orange'
  if (value === 'dead' || value === 'error') return 'red'
  return 'gray'
}

function formatRatio(value: unknown) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '-'
  const normalized = num > 1 ? num / 100 : num
  return `${Math.round(normalized * 100)}%`
}

function formatTime(value?: string | null) {
  if (!value) return '-'
  const formatted = formatBjtTime(value)
  return formatted === '-' ? value : formatted
}

async function restartSlot(slotId: string) {
  try {
    await browserSlotApi.restart(slotId)
    Message.success('已发起重启')
    await loadSlots()
  } catch (error: any) {
    Message.error(error?._message || '重启失败')
  }
}

async function setMaintenance(slotId: string, maintenance: boolean) {
  try {
    await browserSlotApi.setMaintenance(slotId, maintenance)
    Message.success(maintenance ? '已置为维护态' : '已恢复')
    await loadSlots()
  } catch (error: any) {
    Message.error(error?._message || '切换失败')
  }
}

async function releaseSlot(slotId: string) {
  try {
    await browserSlotApi.release(slotId, 'manual_release_from_ui')
    Message.success('已释放')
    await loadSlots()
  } catch (error: any) {
    Message.error(error?._message || '释放失败')
  }
}

onMounted(loadSlots)
</script>

<style scoped>
.browser-slot-panel {
  margin-bottom: 0;
}

.slot-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  width: 100%;
}

.slot-title {
  color: var(--ai-ink-1);
  font-size: 15px;
  font-weight: 800;
}

.slot-subtitle,
.slot-muted {
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.5;
}

.slot-alert {
  margin-bottom: 12px;
}

.slot-kpis {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.slot-main {
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 12px;
  font-weight: 800;
}

.slot-rate {
  display: flex;
  flex-direction: column;
  gap: 2px;
  color: var(--ai-ink-2);
  font-family: var(--ai-font-mono);
  font-size: 11px;
}
</style>
