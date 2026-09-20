<template>
  <div class="page-container page-wide admin-connector-keys-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">管理后台 · 数据与服务</div>
        <h2 class="page-title">{{ userStore.isAdmin ? 'Connector Keys' : '我的 Connector Keys' }}</h2>
        <p class="page-subtitle">Chrome 扩展授权密钥。普通用户管理自己的 Key，管理员可查看全局概览。</p>
      </div>
      <a-space>
        <a-radio-group v-if="userStore.isAdmin" v-model="viewMode" type="button" size="small">
          <a-radio value="overview">概览</a-radio>
          <a-radio value="mine">我的 Key</a-radio>
        </a-radio-group>
        <a-button class="ai-btn-like" @click="loadKeys">刷新</a-button>
        <a-button class="ai-btn-like primary" type="primary" @click="createVisible = true">创建 Key</a-button>
      </a-space>
    </div>

    <a-alert v-if="oneTimeKey" type="info" class="key-alert">
      <template #title>Connector Key 已生成</template>
      <div class="one-time-row">
        <a-input :model-value="oneTimeKey" readonly class="click-copy-input" title="点击复制" @click="copy(oneTimeKey)" />
        <a-button type="primary" @click="copy(oneTimeKey)">复制</a-button>
      </div>
    </a-alert>

    <a-card class="page-list-card">
      <a-table :data="displayKeys" :loading="loading" :pagination="{ pageSize: 20 }" row-key="id">
        <template #columns>
          <a-table-column title="名称" data-index="name" :width="180" />
          <a-table-column v-if="userStore.isAdmin && viewMode === 'overview'" title="Owner" :width="170">
            <template #cell="{ record }">
              <div class="owner-name">{{ record.owner_name || record.owner_user_id || '-' }}</div>
              <div v-if="record.department" class="muted">{{ record.department }}</div>
            </template>
          </a-table-column>
          <a-table-column title="Connector Key" :width="260">
            <template #cell="{ record }">
              <button class="key-copy" type="button" :title="connectorPlaintext(record) ? '点击复制完整 Key' : '旧 Key 无完整明文，请轮换后复制'" @click="copyConnectorKey(record)">
                <span class="mono">{{ keyDisplay(record) }}</span>
              </button>
            </template>
          </a-table-column>
          <a-table-column title="授权范围">
            <template #cell="{ record }">
              <a-space wrap>
                <a-tag v-for="scope in normalizeList(record.scopes)" :key="scope" size="small">{{ scope }}</a-tag>
                <a-tag v-for="platform in normalizeList(record.platforms)" :key="`p-${platform}`" size="small" color="blue">{{ platform }}</a-tag>
              </a-space>
            </template>
          </a-table-column>
          <a-table-column title="最近使用" :width="150">
            <template #cell="{ record }">
              <span class="muted-cell">{{ record.last_used_at || '-' }}</span>
            </template>
          </a-table-column>
          <a-table-column title="状态" :width="90">
            <template #cell="{ record }">
              <a-tag :color="record.status === 'active' || record.is_active ? 'green' : 'gray'">
                {{ record.status || (record.is_active ? 'active' : 'revoked') }}
              </a-tag>
            </template>
          </a-table-column>
          <a-table-column title="操作" :width="180" fixed="right">
            <template #cell="{ record }">
              <a-space>
                <a-button size="mini" :disabled="!isActive(record)" @click="rotate(record)">轮换</a-button>
                <a-button size="mini" status="danger" :disabled="!isActive(record)" @click="revoke(record)">撤销</a-button>
              </a-space>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </a-card>

    <a-modal v-model:visible="createVisible" title="创建 Connector Key" @ok="createKey">
      <a-form :model="form" layout="vertical">
        <a-form-item label="设备备注">
          <a-input v-model="form.name" placeholder="例如：运营一组 Chrome" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'
import { connectorKeyApi, type ConnectorKeyRow } from '@/api'
import { useUserStore } from '@/stores/user'
import { copyText } from '@/utils/clipboard'

const userStore = useUserStore()
const loading = ref(false)
const keys = ref<ConnectorKeyRow[]>([])
const viewMode = ref<'overview' | 'mine'>(userStore.isAdmin ? 'overview' : 'mine')
const createVisible = ref(false)
const oneTimeKey = ref('')
const form = reactive({ name: '' })

const displayKeys = computed(() => {
  if (!userStore.isAdmin || viewMode.value === 'mine') {
    const uid = userStore.userInfo?.user_id
    return uid ? keys.value.filter((item) => !item.owner_user_id || item.owner_user_id === uid) : keys.value
  }
  return keys.value
})

function normalizeList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).filter(Boolean)
  if (typeof value === 'string') return value.split(',').map((item) => item.trim()).filter(Boolean)
  return []
}

function isActive(row: ConnectorKeyRow): boolean {
  return row.status === 'active' || row.is_active === true
}

function connectorPlaintext(row: ConnectorKeyRow): string {
  return row.api_key || row.plaintext_key || ''
}

function keyDisplay(row: ConnectorKeyRow): string {
  const plaintext = connectorPlaintext(row)
  if (plaintext) return plaintext
  if (row.key_prefix || row.key_last4) return `${row.key_prefix || ''}...${row.key_last4 || ''}（需轮换）`
  if (row.hash_prefix) return `${row.hash_prefix}...`
  return '-'
}

async function loadKeys() {
  loading.value = true
  try {
    const res = await connectorKeyApi.list()
    keys.value = Array.isArray(res) ? res : (res.items || [])
  } finally {
    loading.value = false
  }
}

async function createKey() {
  const payload = {
    name: form.name,
    owner_user_id: viewMode.value === 'mine' ? userStore.userInfo?.user_id : undefined,
  }
  const res = await connectorKeyApi.create(payload)
  oneTimeKey.value = connectorPlaintext(res)
  createVisible.value = false
  await loadKeys()
}

async function rotate(row: ConnectorKeyRow) {
  if (!row.id) return
  const res = await connectorKeyApi.rotate(row.id)
  oneTimeKey.value = connectorPlaintext(res)
  await loadKeys()
}

function revoke(row: ConnectorKeyRow) {
  if (!row.id) return
  Modal.confirm({
    title: '撤销 Connector Key',
    content: '撤销后该 key 写入的活跃 cookie 会同步禁用。',
    async onOk() {
      await connectorKeyApi.revoke(row.id as string, { reason: 'admin revoke connector key' })
      Message.success('已撤销')
      await loadKeys()
    },
  })
}

async function copy(text: string) {
  const ok = await copyText(text)
  if (ok) Message.success('已复制')
  else Message.warning('复制失败，请手动选中复制')
}

async function copyConnectorKey(row: ConnectorKeyRow) {
  const plaintext = connectorPlaintext(row)
  if (plaintext) {
    await copy(plaintext)
    return
  }
  Message.warning('旧 Key 没有保存完整明文，请点击"轮换"生成可复制的新 Key')
}

onMounted(loadKeys)
</script>

<style scoped>
/* 设计稿 page chrome 覆盖 —— 与 AdminUsers 同模式 */
.admin-connector-keys-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.admin-connector-keys-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
}
.admin-connector-keys-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.admin-connector-keys-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

/* 顶部按钮统一外形 —— 30px / 6px / 12.5px */
.admin-connector-keys-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary)) {
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
.admin-connector-keys-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.admin-connector-keys-page :deep(.arco-btn-primary.ai-btn-like) {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
  font-size: 12.5px;
  font-weight: 500;
  box-shadow: none;
}
.admin-connector-keys-page :deep(.arco-btn-primary.ai-btn-like:hover) {
  background: var(--ai-ink-2);
  border-color: var(--ai-ink-2);
}

/* 表头 —— 11.5px / uppercase / ink-4 */
.admin-connector-keys-page :deep(.arco-table-th) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 8px 10px !important;
}
.admin-connector-keys-page :deep(.arco-table-th .arco-table-th-title) {
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* 单元格 —— 12.5px / ink-1 / ai-border 底分隔线 */
.admin-connector-keys-page :deep(.arco-table-td) {
  font-size: 12.5px !important;
  color: var(--ai-ink-1) !important;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 10px 10px !important;
  background: var(--ai-surface) !important;
}

/* 行 hover：surface-2 */
.admin-connector-keys-page :deep(.arco-table-tr:hover .arco-table-td),
.admin-connector-keys-page :deep(.arco-table-tr-hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

/* tag pills —— 20px / 4px / 11px/500 —— gray / arcoblue / green / red / orange */
.admin-connector-keys-page :deep(.arco-tag.arco-tag-size-small),
.admin-connector-keys-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.admin-connector-keys-page :deep(.arco-tag-color-arcoblue),
.admin-connector-keys-page :deep(.arco-tag-color-blue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.admin-connector-keys-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.admin-connector-keys-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.admin-connector-keys-page :deep(.arco-tag-color-orange),
.admin-connector-keys-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.admin-connector-keys-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-2) !important;
  border-color: transparent !important;
}

/* one-time key 提示条 */
.key-alert {
  margin-bottom: 14px;
}

.one-time-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

/* Connector Key / hash 等 ID 字段统一 mono */
.mono {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  color: var(--ai-ink-1);
}

.key-copy {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--ai-ink-2);
  cursor: pointer;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.key-copy:hover {
  color: var(--ai-accent-ink);
}

.click-copy-input {
  cursor: pointer;
  font-family: var(--ai-font-mono);
}

/* Owner 列 */
.owner-name {
  font-size: 12.5px;
  color: var(--ai-ink-1);
}
.muted {
  font-size: 11.5px;
  color: var(--ai-ink-4);
  margin-top: 2px;
}

.muted-cell {
  font-size: 12px;
  color: var(--ai-ink-4);
  font-variant-numeric: tabular-nums;
}
</style>
