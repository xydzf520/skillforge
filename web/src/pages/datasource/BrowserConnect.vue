<template>
  <div
    :class="[embedded ? 'browser-connect-embedded' : 'page-container page-wide', 'browser-connect-page']"
    data-testid="browser-connect-page"
  >
    <div v-if="!embedded" class="page-header">
      <div class="page-heading">
        <div class="page-kicker">采集工作台</div>
        <h2 class="page-title">浏览器连接</h2>
        <p class="page-subtitle">启动远程浏览器完成平台登录，再发起采集任务并查看执行历史。</p>
      </div>
      <div class="bc-header-actions">
        <a-button
          v-if="status !== 'running'"
          type="primary"
          :loading="starting"
          @click="handleStart"
        >
          <template #icon><icon-play-arrow /></template>
          启动浏览器
        </a-button>
        <a-button
          v-if="status === 'running'"
          status="danger"
          :loading="stopping"
          @click="handleStop"
        >
          <template #icon><icon-pause /></template>
          停止
        </a-button>
        <a-button @click="refreshStatus">
          <template #icon><icon-refresh /></template>
        </a-button>
      </div>
    </div>
    <!-- embedded 模式：头部操作按钮独立渲染在上方 -->
    <div v-if="embedded" class="bc-embed-actions">
      <div class="bc-header-actions">
        <a-button
          v-if="status !== 'running'"
          type="primary"
          :loading="starting"
          @click="handleStart"
        >
          <template #icon><icon-play-arrow /></template>
          启动浏览器
        </a-button>
        <a-button
          v-if="status === 'running'"
          status="danger"
          :loading="stopping"
          @click="handleStop"
        >
          <template #icon><icon-pause /></template>
          停止
        </a-button>
        <a-button @click="refreshStatus">
          <template #icon><icon-refresh /></template>
        </a-button>
      </div>
    </div>

    <div class="page-chip-row bc-chip-row">
      <span class="page-chip" :class="statusChipClass">状态 {{ statusText }}</span>
      <span v-if="browserInfo" class="page-chip">浏览器 {{ browserInfo }}</span>
      <span class="page-chip is-info">采集平台 {{ currentPlatformLabel }}</span>
      <span class="page-chip is-warning">任务类型 {{ currentTaskLabel }}</span>
      <span class="page-chip">历史任务 {{ taskTotal }}</span>
    </div>

    <div class="bc-summary-grid">
      <a-card class="page-section-card bc-summary-card">
        <div class="bc-summary-label">远程会话</div>
        <div class="bc-summary-value">{{ isRunning ? '已建立' : '未建立' }}</div>
        <div class="bc-summary-meta">
          {{ isRunning
            ? `当前平台：${currentPlatformLabel}，可直接进行登录与采集`
            : '需要先启动浏览器' }}
        </div>
      </a-card>
      <a-card class="page-section-card bc-summary-card">
        <div class="bc-summary-label">最近任务</div>
        <div class="bc-summary-value">{{ latestTaskTime }}</div>
        <div class="bc-summary-meta">默认展示最近 10 条采集历史</div>
      </a-card>
      <a-card class="page-section-card bc-summary-card">
        <div class="bc-summary-label">当前任务</div>
        <div class="bc-summary-value">{{ currentTaskLabel }}</div>
        <div class="bc-summary-meta">登录检查和经营概览都依赖当前浏览器会话</div>
      </a-card>
    </div>

    <BrowserSlotPanel />

    <a-card class="page-section-card bc-vnc-card" :body-style="{ padding: 0 }">
      <template #title>
        <div class="bc-card-head">
          <div>
            <div class="bc-card-title">远程浏览器</div>
            <div class="bc-card-subtitle">在远程桌面中登录电商平台，保持会话后即可复用该登录态发起采集。</div>
          </div>
          <a-button v-if="isRunning" size="small" @click="reconnectVnc">重新连接</a-button>
        </div>
      </template>

      <template v-if="isRunning">
        <div class="bc-vnc-toolbar">
          <span class="page-chip is-success">远程桌面已连接</span>
          <span class="bc-vnc-hint">支持登录淘宝、抖店、小红书等平台，登录完成后无需离开页面即可继续采集。</span>
        </div>
        <!-- noVNC 侧边栏和应用风格不符，但跨 iframe 无法注入 CSS。此处仅包容器圆角化和边框与主题对齐 -->
        <iframe
          v-if="novncUrl"
          :src="novncUrl"
          class="vnc-canvas"
          frameborder="0"
          allow="clipboard-read; clipboard-write"
        />
      </template>
      <div v-else class="bc-placeholder">
        <icon-desktop :size="52" class="bc-placeholder-icon" />
        <div class="bc-placeholder-title">浏览器尚未启动</div>
        <div class="bc-placeholder-desc">点击右上角「启动浏览器」建立远程会话，然后在远程桌面中完成平台登录。</div>
      </div>
    </a-card>

    <a-card class="page-list-card" data-testid="browser-connect-collect">
      <template #title>
        <div class="bc-card-head">
          <div>
            <div class="bc-card-title">数据采集</div>
            <div class="bc-card-subtitle">选择平台与任务类型，直接复用上方远程浏览器中的登录状态。</div>
          </div>
        </div>
      </template>

      <div class="filter-bar bc-collect-form">
        <a-select v-model="collectPlatform" placeholder="选择平台" style="width: 180px">
          <a-option value="sycm">生意参谋</a-option>
          <a-option value="alimama">阿里妈妈</a-option>
          <a-option value="douyin">抖店</a-option>
          <a-option value="xiaohongshu">小红书</a-option>
        </a-select>
        <a-select v-model="collectType" placeholder="任务类型" style="width: 180px">
          <a-option value="check_login">检查登录</a-option>
          <a-option value="dashboard">经营概览</a-option>
        </a-select>
        <a-button type="primary" :loading="collecting" :disabled="status !== 'running'" @click="handleCollect">
          采集
        </a-button>
      </div>

      <a-alert v-if="status !== 'running'" class="bc-alert" type="warning">
        当前浏览器未启动，无法发起采集。请先建立远程浏览器会话并完成平台登录。
      </a-alert>

      <div v-if="collectResult" class="bc-result-panel" data-testid="browser-connect-result">
        <div class="bc-result-head">
          <a-tag :color="collectResult.status === 'success' ? 'green' : 'red'">
            {{ collectResult.status }}
          </a-tag>
          <span class="bc-result-meta">平台 {{ currentPlatformLabel }} · 任务 {{ currentTaskLabel }}</span>
        </div>
        <pre class="result-json">{{ JSON.stringify(collectResult.result || collectResult.error, null, 2) }}</pre>
      </div>

      <a-table
        :data="tasks"
        :pagination="{ total: taskTotal, pageSize: 10, current: taskPage }"
        :loading="loadingTasks"
        size="small"
        class="page-list-table"
        @page-change="(p: number) => { taskPage = p; loadTasks() }"
      >
        <template #columns>
          <a-table-column title="ID" data-index="id" :width="80" />
          <a-table-column title="平台" data-index="platform" :width="120" />
          <a-table-column title="类型" data-index="task_type" :width="140" />
          <a-table-column title="状态" :width="100">
            <template #cell="{ record }">
              <a-tag :color="record.status === 'success' ? 'green' : record.status === 'failed' ? 'red' : 'blue'" size="small">
                {{ record.status }}
              </a-tag>
            </template>
          </a-table-column>
          <a-table-column title="时间" data-index="created_at" :width="180" />
          <a-table-column title="结果预览" data-index="result_preview" ellipsis />
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconDesktop, IconPause, IconPlayArrow, IconRefresh } from '@arco-design/web-vue/es/icon'
import { browserApi as rawBrowserApi } from '@/api'
import { formatTime as formatBjtTime } from '@/utils/format'
import BrowserSlotPanel from './BrowserSlotPanel.vue'

// embedded=true 时跳过外层 page-container 和 page-header，由父容器提供
const props = defineProps<{ embedded?: boolean }>()
const embedded = computed(() => Boolean(props.embedded))

const browserApi: any = rawBrowserApi
// 状态
const status = ref('stopped')
const browserInfo = ref('')
const starting = ref(false)
const stopping = ref(false)
const novncUrl = ref('')

// 采集
const collectPlatform = ref('sycm')
const collectType = ref('check_login')
const collecting = ref(false)
const collectResult = ref<any>(null)

// 任务列表
const tasks = ref<any[]>([])
const taskTotal = ref(0)
const taskPage = ref(1)
const loadingTasks = ref(false)

const statusText = ref('已停止')

const platformLabels: Record<string, string> = {
  sycm: '生意参谋',
  alimama: '阿里妈妈',
  douyin: '抖店',
  xiaohongshu: '小红书',
}

const collectTypeLabels: Record<string, string> = {
  check_login: '检查登录',
  dashboard: '经营概览',
}

const isRunning = computed(() => status.value === 'running')
const currentPlatformLabel = computed(() => platformLabels[collectPlatform.value] || collectPlatform.value)
const currentTaskLabel = computed(() => collectTypeLabels[collectType.value] || collectType.value)
const statusChipClass = computed(() => {
  if (status.value === 'running') return 'is-success'
  if (status.value === 'starting') return 'is-info'
  if (status.value === 'error') return 'is-danger'
  return 'is-warning'
})
const latestTaskTime = computed(() => formatTime(tasks.value[0]?.created_at))

function updateStatusDisplay(s: string) {
  status.value = s
  if (s === 'running') statusText.value = '运行中'
  else if (s === 'starting') statusText.value = '启动中'
  else if (s === 'error') statusText.value = '异常'
  else statusText.value = '已停止'
}

async function refreshStatus() {
  try {
    const res = await browserApi.status()
    updateStatusDisplay(res.status)
    browserInfo.value = res.browser || ''
    if (res.status === 'running') connectVnc()
  } catch { updateStatusDisplay('stopped') }
}

async function handleStart() {
  starting.value = true
  updateStatusDisplay('starting')
  try {
    const res = await browserApi.start()
    updateStatusDisplay(res.status)
    browserInfo.value = res.browser || ''
    Message.success('浏览器已启动')
    await nextTick()
    connectVnc()
  } catch (e: any) {
    updateStatusDisplay('error')
    Message.error(e._message || '启动失败')
  } finally { starting.value = false }
}

async function handleStop() {
  stopping.value = true
  try {
    await browserApi.stop()
    updateStatusDisplay('stopped')
    disconnectVnc()
    Message.success('浏览器已停止')
  } catch (e: any) {
    Message.error(e._message || '停止失败')
  } finally { stopping.value = false }
}

// noVNC iframe 连接
function connectVnc() {
  // noVNC HTML 和 WebSocket 都走 FastAPI 代理（只需 8000 端口）
  const h = window.location.hostname
  const p = window.location.port || (window.location.protocol === 'https:' ? '443' : '80')
  const encrypt = window.location.protocol === 'https:' ? '1' : '0'
  // view_clip / view_only 不设，但用 bell=false + show_dot=false 减少视觉干扰；
  // 侧边工具栏 noVNC 官方 vnc.html 不支持从 URL 关掉，只能默认折叠态（点击后展开），这点已是最好结果。
  novncUrl.value = `/api/browser/novnc/vnc.html?autoconnect=true&resize=remote&reconnect=true&reconnect_delay=3000&show_dot=false&bell=false&host=${h}&port=${p}&encrypt=${encrypt}&path=api/browser/novnc/websockify`
}

function disconnectVnc() {
  novncUrl.value = ''
}

function reconnectVnc() {
  disconnectVnc()
  setTimeout(() => connectVnc(), 100)
}

function formatTime(value?: string) {
  if (!value) return '暂无'
  const formatted = formatBjtTime(value)
  return formatted === '-' ? value : formatted
}

// 采集
async function handleCollect() {
  collecting.value = true
  collectResult.value = null
  try {
    const res = await browserApi.collect({
      platform: collectPlatform.value,
      task_type: collectType.value,
    })
    collectResult.value = res
    await loadTasks()
  } catch (e: any) {
    collectResult.value = { status: 'failed', error: e._message || '采集失败' }
  } finally { collecting.value = false }
}

async function loadTasks() {
  loadingTasks.value = true
  try {
    const res = await browserApi.tasks({ page: taskPage.value, page_size: 10 })
    tasks.value = res.items || []
    taskTotal.value = res.total || 0
  } catch {}
  finally { loadingTasks.value = false }
}

let pollTimer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  refreshStatus()
  loadTasks()
  pollTimer = setInterval(refreshStatus, 15000)
})
onBeforeUnmount(() => {
  clearInterval(pollTimer)
})
</script>

<style scoped>
.browser-connect-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.browser-connect-embedded {
  /* embedded 模式下父容器提供 padding，自己只做 stack */
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.bc-embed-actions {
  display: flex;
  justify-content: flex-end;
}

.bc-header-actions,
.bc-collect-form {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.bc-summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.bc-summary-card :deep(.arco-card-body) {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.bc-summary-label {
  color: var(--ai-ink-3);
  font-size: 12px;
  font-weight: 700;
}

.bc-summary-value {
  color: var(--ai-ink-1);
  font-size: 20px;
  line-height: 1.3;
  font-weight: 800;
  letter-spacing: -0.02em;
}

.bc-summary-meta {
  color: var(--ai-ink-2);
  font-size: 12px;
  line-height: 1.6;
  font-weight: 600;
}

.bc-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  width: 100%;
}

.bc-card-title {
  color: var(--ai-ink-1);
  font-size: 15px;
  font-weight: 800;
}

.bc-card-subtitle {
  margin-top: 4px;
  color: var(--ai-ink-2);
  font-size: 12px;
  font-weight: 600;
}

.bc-vnc-card :deep(.arco-card-head),
.bc-vnc-card :deep(.arco-card-head-wrapper) {
  align-items: flex-start;
}

.bc-vnc-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
}

.bc-vnc-hint {
  color: var(--ai-ink-2);
  font-size: 12px;
  font-weight: 600;
}

.vnc-canvas {
  width: 100%;
  height: 620px;
  background: var(--ai-surface-3);
  border-radius: var(--ai-radius);
  border: 1px solid var(--ai-border);
  overflow: hidden;
}

.vnc-canvas :deep(canvas) {
  width: 100% !important;
  height: 100% !important;
}

.bc-placeholder {
  min-height: 320px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 32px;
  background: var(--ai-surface-2);
}

.bc-placeholder-icon {
  color: var(--ai-ink-3);
}

.bc-placeholder-title {
  color: var(--ai-ink-1);
  font-size: 18px;
  font-weight: 800;
}

.bc-placeholder-desc {
  max-width: 420px;
  color: var(--ai-ink-2);
  font-size: 13px;
  line-height: 1.7;
  font-weight: 600;
  text-align: center;
}

.bc-alert {
  margin-bottom: 16px;
}

.bc-result-panel {
  margin-bottom: 16px;
  padding: 12px 16px;
  border-radius: var(--ai-radius);
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
}

.bc-result-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.bc-result-meta {
  color: var(--ai-ink-3);
  font-size: 12px;
  font-weight: 700;
}

.result-json {
  margin: 8px 0 0;
  padding: 12px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: var(--sf-font-xs);
  line-height: 1.6;
  max-height: 220px;
  overflow: auto;
}

@media (max-width: 1200px) {
  .bc-summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .bc-summary-grid {
    grid-template-columns: 1fr;
  }

  .bc-vnc-toolbar,
  .bc-card-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .vnc-canvas {
    height: 480px;
  }
}
</style>
