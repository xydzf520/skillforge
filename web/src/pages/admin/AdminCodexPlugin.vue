<template>
  <div class="admin-codex-page ai-main">
    <div class="ai-pagehead admin-codex-pagehead">
      <div>
        <div class="ai-crumbs">管理后台 · 数据与服务</div>
        <h1 class="ai-title">Codex 插件</h1>
        <p class="ai-sub">统计 Codex 插件登录、CLI 使用和 MCP 能力调用</p>
      </div>
      <div class="admin-codex-head-actions">
        <a-select v-model="days" class="days-select" @change="loadAll">
          <a-option :value="7">近 7 天</a-option>
          <a-option :value="30">近 30 天</a-option>
          <a-option :value="90">近 90 天</a-option>
        </a-select>
        <button class="ai-btn" type="button" :disabled="loading" @click="loadAll">
          <SfShellIcon name="refresh" />
          {{ loading ? '刷新中' : '刷新' }}
        </button>
      </div>
    </div>

    <!-- KPI 条：参照设计稿 admin.jsx AdminCodex 部分的横向 KPI strip -->
    <div class="codex-kpi-strip">
      <div class="kpi-cell">
        <div class="kpi-label">插件用户</div>
        <div class="kpi-value">{{ summary.plugin_users || 0 }}</div>
      </div>
      <div class="kpi-cell" :class="{ 'kpi-ok': (summary.active_sessions || 0) > 0 }">
        <div class="kpi-label">活跃会话</div>
        <div class="kpi-value">{{ summary.active_sessions || 0 }}</div>
      </div>
      <div class="kpi-cell" :class="{ 'kpi-accent': (summary.api_calls || 0) > 0 }">
        <div class="kpi-label">CLI/API 调用</div>
        <div class="kpi-value">{{ summary.api_calls || 0 }}</div>
      </div>
      <div class="kpi-cell" :class="{ 'kpi-warn': (summary.mcp_calls || 0) > 0 }">
        <div class="kpi-label">MCP 调用</div>
        <div class="kpi-value">{{ summary.mcp_calls || 0 }}</div>
      </div>
      <div class="kpi-cell" :class="{ 'kpi-bad': (summary.failed_mcp_calls || 0) > 0 }">
        <div class="kpi-label">失败 MCP</div>
        <div class="kpi-value">{{ summary.failed_mcp_calls || 0 }}</div>
      </div>
    </div>

    <div class="admin-codex-body ai-pagebody">
      <a-card class="codex-table-card ai-card">
        <div class="section-head">
          <div>
            <h3>插件用户</h3>
            <p>钉钉登录后绑定 Codex CLI 的用户</p>
          </div>
        </div>
        <a-table :data="users" :loading="loadingUsers" :pagination="false" row-key="user_id" size="small">
          <template #columns>
            <a-table-column title="用户">
              <template #cell="{ record }">
                <div class="user-cell">
                  <span class="user-name">{{ displayUserName(record) }}</span>
                  <span class="user-id">{{ displayUserMeta(record) }}</span>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="会话" :width="140">
              <template #cell="{ record }">{{ record.open_sessions || 0 }} 活跃 / {{ record.session_count || 0 }} 总数</template>
            </a-table-column>
            <a-table-column title="MCP 调用" data-index="mcp_call_count" :width="110" />
            <a-table-column title="最近活跃" :width="180">
              <template #cell="{ record }">{{ formatTime(record.last_codex_activity_at) }}</template>
            </a-table-column>
          </template>
        </a-table>
      </a-card>

      <a-card class="codex-table-card ai-card">
        <div class="section-head">
          <div>
            <h3>调用记录</h3>
            <p>最近 {{ days }} 天 · {{ total }} 条</p>
          </div>
          <div class="section-filters">
            <a-select v-model="kind" allow-clear placeholder="类型" class="kind-select" @change="loadEvents">
              <a-option value="api">CLI/API</a-option>
              <a-option value="mcp">MCP</a-option>
            </a-select>
            <a-input-search v-model="userId" allow-clear placeholder="用户 ID" class="user-filter" @search="loadEvents" @press-enter="loadEvents" />
          </div>
        </div>
        <a-table :data="events" :loading="loading" :pagination="pagination" row-key="id" @page-change="onPageChange">
          <template #columns>
            <a-table-column title="时间" :width="170">
              <template #cell="{ record }">{{ formatTime(record.created_at) }}</template>
            </a-table-column>
            <a-table-column title="用户" :width="180">
              <template #cell="{ record }">
                <div class="user-cell">
                  <span class="user-name">{{ displayUserName(record) }}</span>
                  <span class="user-id">{{ displayUserMeta(record) }}</span>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="类型" :width="90">
              <template #cell="{ record }">
                <a-tag size="small" :color="record.kind === 'mcp' ? 'orange' : 'blue'">{{ record.kind === 'mcp' ? 'MCP' : 'CLI' }}</a-tag>
              </template>
            </a-table-column>
            <a-table-column title="动作 / 能力">
              <template #cell="{ record }">
                <div class="event-main">
                  <code>{{ record.tool || record.action }}</code>
                  <span v-if="record.skill_id">Skill: {{ record.skill_id }}</span>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="结果" :width="100">
              <template #cell="{ record }">
                <a-tag v-if="record.kind === 'mcp'" size="small" :color="record.ok ? 'green' : 'red'">{{ record.ok ? '成功' : '失败' }}</a-tag>
                <a-tag v-else size="small" color="green">已记录</a-tag>
              </template>
            </a-table-column>
            <a-table-column title="模式" :width="130">
              <template #cell="{ record }">
                <span>{{ record.run_mode || '-' }}</span>
                <span v-if="record.dry_run === true"> · dry-run</span>
              </template>
            </a-table-column>
          </template>
        </a-table>
      </a-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { codexAdminApi } from '@/api'
import { formatTime } from '@/utils/format'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

const loading = ref(false)
const days = ref(30)
const kind = ref<string | undefined>(undefined)
const userId = ref('')
const page = ref(1)
const pageSize = 50
const total = ref(0)
const events = ref<any[]>([])
const users = ref<any[]>([])
const loadingUsers = ref(false)
const summary = reactive({
  plugin_users: 0,
  active_sessions: 0,
  api_calls: 0,
  mcp_calls: 0,
  failed_mcp_calls: 0,
})

const pagination = computed(() => ({
  current: page.value,
  pageSize,
  total: total.value,
  showTotal: true,
}))

async function loadSummary() {
  const res: any = await codexAdminApi.usageSummary({ days: days.value })
  Object.assign(summary, res || {})
}

async function loadEvents() {
  loading.value = true
  try {
    const res: any = await codexAdminApi.usageEvents({
      days: days.value,
      kind: kind.value || undefined,
      user_id: userId.value || undefined,
      page: page.value,
      page_size: pageSize,
    })
    events.value = res?.items || []
    total.value = res?.total || 0
  } catch (e: any) {
    Message.error(e?._message || '加载 Codex 插件记录失败')
  } finally {
    loading.value = false
  }
}

async function loadUsers() {
  loadingUsers.value = true
  try {
    const res: any = await codexAdminApi.users()
    users.value = res?.items || []
  } finally {
    loadingUsers.value = false
  }
}

async function loadAll() {
  page.value = 1
  loading.value = true
  try {
    await Promise.all([loadSummary(), loadEvents(), loadUsers()])
  } finally {
    loading.value = false
  }
}

function onPageChange(next: number) {
  page.value = next
  loadEvents()
}

function displayUserName(record: any) {
  return record?.user_name || record?.name || record?.username || record?.user_id || '-'
}

function displayUserMeta(record: any) {
  const parts = [record?.username, record?.user_id, record?.department].filter(Boolean)
  return parts.length ? parts.join(' · ') : '-'
}

onMounted(loadAll)
</script>

<style scoped>
.admin-codex-page {
  gap: 0;
  padding: 0;
  max-width: none;
  margin: 0;
  min-height: calc(100vh - 92px);
  overflow-x: auto;
}
.admin-codex-pagehead {
  flex: 0 0 auto;
}
.admin-codex-head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  flex-shrink: 0;
}
.admin-codex-head-actions .ai-btn {
  cursor: pointer;
  font-family: var(--ai-font-sans);
}
.admin-codex-head-actions .ai-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.admin-codex-head-actions svg {
  width: 12px;
  height: 12px;
  flex: 0 0 12px;
}
.admin-codex-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* KPI 条：参照 inbox.jsx / admin.jsx 的横向 KPI strip */
.codex-kpi-strip {
  display: flex;
  align-items: stretch;
  padding: 0;
  margin: 0;
  border-radius: 0;
  background: var(--ai-surface);
  border: 0;
  border-bottom: 1px solid var(--ai-border);
  overflow: hidden;
  font-family: var(--ai-font-sans);
}
.kpi-cell {
  flex: 1 1 0;
  min-width: 0;
  padding: 14px 20px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  border-left: 1px solid var(--ai-border);
  background: transparent;
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
}
.kpi-cell.kpi-ok .kpi-value { color: var(--ai-ok); }
.kpi-cell.kpi-accent .kpi-value { color: var(--ai-accent-ink); }
.kpi-cell.kpi-warn .kpi-value { color: var(--ai-warn); }
.kpi-cell.kpi-bad .kpi-value { color: var(--ai-bad); }

.codex-table-card {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}
.admin-codex-page :deep(.codex-table-card .arco-card-body) {
  padding: 0 !important;
}

/* 卡片内的 section 头 */
.section-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 12px 14px;
  border-bottom: 1px solid var(--ai-border);
}
.section-head h3 {
  margin: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}
.section-head p {
  margin: 3px 0 0;
  color: var(--ai-ink-4);
  font-size: 11.5px;
}
.section-filters {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

/* 用户单元格 */
.user-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.user-name {
  font-weight: 500;
  color: var(--ai-ink-1);
  font-size: 12.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.user-id {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-family: var(--ai-font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 调用记录的 action / skill 单元格 */
.event-main { display: flex; flex-direction: column; gap: 3px; }
.event-main code {
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-1);
  font-size: 11.5px;
}
.event-main span {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-family: var(--ai-font-mono);
}

/* Arco a-tag → ai-pill 风格映射 */
.admin-codex-page :deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.admin-codex-page :deep(.arco-tag-color-blue) {
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
  border-color: transparent;
}
.admin-codex-page :deep(.arco-tag-color-orange) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.admin-codex-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
.admin-codex-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}

/* Arco table 行 — 密集化 */
.admin-codex-page :deep(.arco-table-th) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11px !important;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 8px 10px !important;
}
.admin-codex-page :deep(.arco-table-td) {
  font-size: 12.5px;
  padding: 10px 10px !important;
  border-bottom: 1px solid var(--ai-border) !important;
}
.admin-codex-page :deep(.arco-table-tr:hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

@media (max-width: 640px) {
  .admin-codex-pagehead {
    flex-direction: column;
    align-items: flex-start;
    padding: 16px;
  }
  .admin-codex-head-actions {
    justify-content: flex-start;
    width: 100%;
  }
  .admin-codex-body {
    padding: 16px;
  }
  .codex-kpi-strip { flex-wrap: wrap; }
  .kpi-cell {
    flex: 1 1 50%;
    border-left: 0;
    border-top: 1px solid var(--ai-border);
  }
  .kpi-cell:nth-child(-n+2) { border-top: 0; }
}

/* Admin sweep utilities */
.days-select {
  width: 120px;
}
.kind-select {
  width: 130px;
}
.user-filter {
  width: 180px;
}
@media (max-width: 900px) {
  .days-select,
  .kind-select,
  .user-filter {
    width: 100%;
  }
}

</style>
