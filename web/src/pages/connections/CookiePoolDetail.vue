<template>
  <section class="cookie-pool-detail">
    <div class="cp-head">
      <div>
        <div class="cp-title">
          {{ platformLabel(platform) }}
          <span class="cp-shop">{{ mergedSummary.shop_name || shopId || '未命名设备' }}</span>
        </div>
        <div class="cp-subtitle">按授权账号隔离存储，执行时只展示脱敏 credential alias。</div>
      </div>
      <a-space>
        <a-button size="small" :loading="loading" @click="loadDetail">刷新</a-button>
        <a-button size="small" @click="$emit('close')">收起</a-button>
      </a-space>
    </div>

    <div class="cp-stat-grid">
      <div class="cp-stat">
        <span>Active</span>
        <strong>{{ mergedSummary.active_count || 0 }}</strong>
      </div>
      <div class="cp-stat">
        <span>Standby</span>
        <strong>{{ mergedSummary.standby_count || 0 }}</strong>
      </div>
      <div class="cp-stat">
        <span>验证通过率</span>
        <strong>{{ formatRatio(mergedSummary.verify_pass_rate) }}</strong>
      </div>
      <div class="cp-stat">
        <span>多账号混用</span>
        <strong>{{ formatRatio(mergedSummary.mixed_credentials_ratio) }}</strong>
      </div>
    </div>

    <a-alert v-if="loadError" type="warning" class="cp-alert">
      {{ loadError }}
    </a-alert>

    <a-table
      :data="credentials"
      :loading="loading"
      :pagination="false"
      row-key="id"
      size="small"
      class="cp-table"
    >
      <template #columns>
        <a-table-column title="授权人" :width="150">
          <template #cell="{ record }">
            <div class="cp-main">{{ displayOwner(record) }}</div>
            <div class="cp-owner-meta">
              <span v-if="record.owner_department" class="cp-muted">{{ record.owner_department }}</span>
              <a-tag v-if="record.owner_status === 'revoked'" size="small" color="red">revoked</a-tag>
            </div>
          </template>
        </a-table-column>
        <a-table-column title="设备 / 账号" :width="180">
          <template #cell="{ record }">
            <div class="cp-main">{{ record.device_label || '未命名设备' }}</div>
            <div class="cp-muted">{{ record.account_login || record.credential_alias || '-' }}</div>
          </template>
        </a-table-column>
        <a-table-column title="健康" :width="110">
          <template #cell="{ record }">
            <a-tag :color="healthColor(record.health)" size="small">{{ formatHealth(record.health) }}</a-tag>
          </template>
        </a-table-column>
        <a-table-column title="验证" :width="120">
          <template #cell="{ record }">
            <a-tag :color="verifyColor(record.verification_status)" size="small">
              {{ verifyLabel(record.verification_status) }}
            </a-tag>
          </template>
        </a-table-column>
        <a-table-column title="能力" ellipsis>
          <template #cell="{ record }">
            <span class="cp-muted">{{ capabilityText(record.capability_summary) }}</span>
          </template>
        </a-table-column>
        <a-table-column title="最近使用" :width="150">
          <template #cell="{ record }">{{ formatTime(record.last_used_at) }}</template>
        </a-table-column>
        <a-table-column title="优先级" :width="120">
          <template #cell="{ record }">
            <a-space size="mini">
              <a-button size="mini" :disabled="!record.id" @click="adjustPriority(record, -1)">-</a-button>
              <span class="cp-priority">{{ record.priority ?? 0 }}</span>
              <a-button size="mini" :disabled="!record.id" @click="adjustPriority(record, 1)">+</a-button>
            </a-space>
          </template>
        </a-table-column>
        <a-table-column title="状态" :width="90">
          <template #cell="{ record }">
            <a-tag :color="record.is_active === false ? 'gray' : 'green'" size="small">
              {{ record.is_active === false ? '停用' : '启用' }}
            </a-tag>
          </template>
        </a-table-column>
        <a-table-column title="操作" :width="160" fixed="right">
          <template #cell="{ record }">
            <a-space size="mini">
              <a-button size="mini" :disabled="!record.id" @click="verifyCredential(record)">验证</a-button>
              <a-button
                size="mini"
                status="danger"
                :disabled="!record.id || record.is_active === false"
                @click="disableCredential(record)"
              >
                停用
              </a-button>
            </a-space>
          </template>
        </a-table-column>
      </template>
    </a-table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { cookiePoolApi, type CookiePoolCredentialRow, type CookiePoolSummaryRow } from '@/api'
import { formatTime as formatBjtTime } from '@/utils/format'

const props = defineProps<{
  platform: string
  shopId: string
  summary?: CookiePoolSummaryRow | null
}>()

const emit = defineEmits<{
  (event: 'changed'): void
  (event: 'close'): void
}>()

const loading = ref(false)
const loadError = ref('')
const detailSummary = ref<CookiePoolSummaryRow | null>(null)
const credentials = ref<CookiePoolCredentialRow[]>([])

const mergedSummary = computed<CookiePoolSummaryRow>(() => ({
  ...(props.summary || {}),
  ...(detailSummary.value || {}),
}))

const PLATFORM_LABELS: Record<string, string> = {
  taobao: '淘宝/天猫',
  sycm: '生意参谋',
  alimama: '阿里妈妈',
  douyin: '抖店/抖音',
  jd: '京东',
  pdd: '拼多多',
}

function platformLabel(value?: string) {
  return PLATFORM_LABELS[value || ''] || value || '未知平台'
}

function normalizeList(value: unknown): CookiePoolCredentialRow[] {
  if (Array.isArray(value)) return value as CookiePoolCredentialRow[]
  if (value && typeof value === 'object') {
    const obj = value as { credentials?: unknown; items?: unknown }
    if (Array.isArray(obj.credentials)) return obj.credentials as CookiePoolCredentialRow[]
    if (Array.isArray(obj.items)) return obj.items as CookiePoolCredentialRow[]
  }
  return []
}

async function loadDetail() {
  if (!props.platform || !props.shopId) {
    credentials.value = []
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    const res = await cookiePoolApi.detail(props.platform, props.shopId, { include_audit: 0 })
    detailSummary.value = res.summary || null
    credentials.value = normalizeList(res)
  } catch (error: any) {
    credentials.value = []
    loadError.value = error?._message || 'Cookie 池接口未就绪，当前仅展示连接摘要。'
  } finally {
    loading.value = false
  }
}

function displayOwner(record: CookiePoolCredentialRow) {
  if (record.owner_name) return record.owner_name
  const id = record.owner_user_id || ''
  if (!id) return '未知授权人'
  if (id.length <= 8) return id
  return `${id.slice(0, 4)}...${id.slice(-3)}`
}

function formatRatio(value: unknown) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '-'
  const normalized = num > 1 ? num / 100 : num
  return `${Math.round(normalized * 100)}%`
}

function formatHealth(value: unknown) {
  const num = Number(value)
  return Number.isFinite(num) ? `${Math.round(num)}` : '-'
}

function healthColor(value: unknown) {
  const num = Number(value)
  if (!Number.isFinite(num)) return 'gray'
  if (num >= 80) return 'green'
  if (num >= 55) return 'orange'
  return 'red'
}

function verifyLabel(value?: string) {
  const map: Record<string, string> = {
    valid: '有效',
    passed: '有效',
    expired: '已失效',
    failed: '失败',
    pending: '待验证',
    unknown: '未知',
  }
  return map[value || ''] || value || '未验证'
}

function verifyColor(value?: string) {
  if (value === 'valid' || value === 'passed') return 'green'
  if (value === 'expired' || value === 'failed') return 'red'
  if (value === 'pending') return 'orange'
  return 'gray'
}

function capabilityText(value: CookiePoolCredentialRow['capability_summary']) {
  if (!value) return '-'
  if (Array.isArray(value)) return value.join('、')
  if (typeof value === 'object') {
    return Object.entries(value).map(([key, val]) => `${key}:${String(val)}`).join(' · ')
  }
  return String(value)
}

function formatTime(value?: string | null) {
  if (!value) return '-'
  const formatted = formatBjtTime(value)
  return formatted === '-' ? value : formatted
}

async function verifyCredential(record: CookiePoolCredentialRow) {
  if (!record.id) return
  try {
    await cookiePoolApi.verifyCredential(record.id)
    Message.success('已发起验证')
    await loadDetail()
    emit('changed')
  } catch (error: any) {
    Message.error(error?._message || '验证失败')
  }
}

async function disableCredential(record: CookiePoolCredentialRow) {
  if (!record.id) return
  try {
    await cookiePoolApi.disableCredential(record.id)
    Message.success('已停用')
    await loadDetail()
    emit('changed')
  } catch (error: any) {
    Message.error(error?._message || '停用失败')
  }
}

async function adjustPriority(record: CookiePoolCredentialRow, delta: number) {
  if (!record.id) return
  const next = Number(record.priority || 0) + delta
  try {
    await cookiePoolApi.updateCredentialPriority(record.id, next)
    record.priority = next
    emit('changed')
  } catch (error: any) {
    Message.error(error?._message || '调整优先级失败')
  }
}

watch(() => [props.platform, props.shopId], () => {
  void loadDetail()
})

onMounted(loadDetail)
</script>

<style scoped>
.cookie-pool-detail {
  margin-top: 12px;
  padding: 14px 16px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
}

.cp-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--ai-border);
  margin-bottom: 12px;
}

.cp-title {
  color: var(--ai-ink-1);
  font-size: 14px;
  font-weight: 800;
}

.cp-shop {
  margin-left: 8px;
  color: var(--ai-ink-3);
  font-family: var(--ai-font-mono);
  font-size: 12px;
  font-weight: 700;
}

.cp-subtitle,
.cp-muted {
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.5;
}

.cp-stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}

.cp-stat {
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
}

.cp-stat span {
  display: block;
  color: var(--ai-ink-3);
  font-size: 11px;
}

.cp-stat strong {
  display: block;
  margin-top: 4px;
  color: var(--ai-ink-1);
  font-size: 18px;
  font-weight: 800;
}

.cp-alert {
  margin-bottom: 12px;
}

.cp-main {
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 700;
}

.cp-priority {
  min-width: 20px;
  text-align: center;
  font-family: var(--ai-font-mono);
}

.cp-owner-meta {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-top: 2px;
}

@media (max-width: 900px) {
  .cp-head {
    flex-direction: column;
  }

  .cp-stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
