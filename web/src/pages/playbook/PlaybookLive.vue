<template>
  <div class="page-container">
    <div class="page-header page-detail-toolbar">
        <a-button size="small" @click="$router.push('/playbooks')"><icon-left /> 返回</a-button>
        <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-top: 6px">
          <h2 class="page-title">实时执行 — {{ pbName }}</h2>
          <a-tag v-if="runStatus" :color="statusColor(runStatus)">{{ runStatus }}</a-tag>
      </div>
      <a-space>
        <a-tag>{{ completedSteps }}/{{ totalSteps }} 步骤</a-tag>
      </a-space>
    </div>

    <!-- 画布（live 模式） -->
    <a-card class="page-section-card" title="执行画布" style="margin-bottom: 16px">
      <div class="live-canvas-area">
        <iframe
          ref="liveFrame"
          :src="`/playbook-editor/index.html?name=${pbName}&readonly=1&live=1`"
          class="live-iframe"
          @load="onFrameLoad"
        />
      </div>
    </a-card>

    <!-- 步骤状态日志 -->
    <a-card class="page-section-card" title="执行日志">
      <div v-if="logs.length === 0" style="color: var(--ai-ink-4); text-align: center; padding: 20px">
        {{ connectionMessage }}
      </div>
      <a-timeline v-else>
        <a-timeline-item v-for="(log, i) in logs" :key="i"
          :dotColor="statusDotColor(log.status)"
        >
          <span style="font-weight: 500">{{ log.step_id || log.type }}</span>
          <a-tag size="small" :color="statusColor(log.status)" style="margin-left: 8px">{{ log.status }}</a-tag>
          <span style="font-size: 12px; color: var(--ai-ink-4); margin-left: 8px">{{ log.time }}</span>
        </a-timeline-item>
      </a-timeline>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { IconLeft } from '@arco-design/web-vue/es/icon'
import { formatTimeOnly } from '@/utils/format'

const route: any = useRoute()
const pbName: string = route.params.name
const runId = route.query.run_id || ''

const liveFrame = ref<any>(null)
const wsConnected = ref(false)
const connectionMessage = ref('正在连接...')
const runStatus = ref('running')
const completedSteps = ref(0)
const totalSteps = ref(0)
const logs = ref<any[]>([])
const stepStatuses = ref<Record<string, string>>({})

let ws: WebSocket | null = null
let heartbeatTimer: ReturnType<typeof setInterval> | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempts = 0
const MAX_RECONNECT_ATTEMPTS = 5

function statusColor(status: string) {
  const map: Record<string, string> = { completed: 'green', success: 'green', running: 'blue', failed: 'red', timeout: 'red', skipped: 'orangered', pending: 'gray' }
  return map[status] || 'gray'
}

function statusDotColor(status: string) {
  const map: Record<string, string> = { completed: 'rgb(var(--green-6))', success: 'rgb(var(--green-6))', running: 'rgb(var(--arcoblue-6))', failed: 'rgb(var(--red-6))', timeout: 'rgb(var(--red-6))', skipped: 'rgb(var(--orange-6))' }
  return map[status] || 'var(--color-text-4)'
}

function onFrameLoad() {
  // 画布加载后已通过 URL 参数自动获取 playbook 数据
}

// C8: 节点状态颜色映射
const nodeColorMap: Record<string, { color: string; animation: string; borderStyle?: string }> = {
  pending: { color: 'rgb(var(--gray-4))', animation: 'none' },
  running: { color: 'rgb(var(--arcoblue-6))', animation: 'pulse' },
  success: { color: 'rgb(var(--green-6))', animation: 'flash' },
  completed: { color: 'rgb(var(--green-6))', animation: 'flash' },
  failed: { color: 'rgb(var(--red-6))', animation: 'shake' },
  timeout: { color: 'rgb(var(--red-6))', animation: 'shake' },
  skipped: { color: 'var(--color-fill-3)', animation: 'none', borderStyle: 'dashed' },
}

function pushStatusToCanvas() {
  const frame = liveFrame.value
  if (!frame || !frame.contentWindow) return
  // 附带颜色和动画信息，画布侧直接使用
  const enriched: Record<string, any> = {}
  for (const [stepId, status] of Object.entries(stepStatuses.value)) {
    enriched[stepId] = {
      status,
      ...(nodeColorMap[status] || nodeColorMap.pending),
    }
  }
  frame.contentWindow.postMessage({
    type: 'update-node-styles',
    payload: enriched,
  }, window.location.origin)
}

function connectWs() {
  if (!runId) return

  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = new URL(`${protocol}//${location.host}/api/playbooks/ws/playbook-live/${runId}`)
  const explicitToken = typeof route.query.token === 'string' ? route.query.token : ''
  if (explicitToken) {
    wsUrl.searchParams.set('token', explicitToken)
  }

  ws = new WebSocket(wsUrl.toString())
  ws.onopen = () => {
    wsConnected.value = true
    connectionMessage.value = '等待执行事件...'
    reconnectAttempts = 0
    startHeartbeat()
  }
  ws.onmessage = (e: MessageEvent) => {
    try {
      const event = JSON.parse(e.data)
      handleEvent(event)
    } catch { /* ignore malformed */ }
  }
  ws.onclose = (event) => {
    wsConnected.value = false
    stopHeartbeat()
    if (event.code === 4001) {
      connectionMessage.value = '认证失败，请重新登录'
      return
    }
    if (event.code === 4003) {
      connectionMessage.value = '无权查看该执行记录'
      return
    }
    connectionMessage.value = '连接已断开，正在重试...'
    // 自动重连（仅在运行中时）
    if (runStatus.value === 'running' && reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
      const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 10000)
      reconnectTimer = setTimeout(() => {
        reconnectAttempts++
        connectWs()
      }, delay)
      return
    }
    if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      connectionMessage.value = '连接失败，请刷新页面重试'
    }
  }
  ws.onerror = () => {
    wsConnected.value = false
    connectionMessage.value = '连接失败，正在重试...'
  }
}

function startHeartbeat() {
  stopHeartbeat()
  heartbeatTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'ping' }))
    }
  }, 30000)
}

function stopHeartbeat() {
  clearInterval(heartbeatTimer)
  heartbeatTimer = null
}

function handleEvent(event: any) {
  const now = formatTimeOnly(Date.now())

  if (event.type === 'step_status') {
    stepStatuses.value = { ...stepStatuses.value, [event.step_id]: event.status }
    logs.value.push({ ...event, time: now })
    pushStatusToCanvas()
  }

  if (event.type === 'run_complete') {
    runStatus.value = event.status
    completedSteps.value = event.completed_steps
    totalSteps.value = event.total_steps
    logs.value.push({ ...event, time: now, step_id: '运行结束' })
  }
}

onMounted(() => {
  if (!runId) {
    connectionMessage.value = '缺少 run_id，无法订阅实时执行'
    return
  }
  connectWs()
})

onUnmounted(() => {
  stopHeartbeat()
  clearTimeout(reconnectTimer)
  reconnectAttempts = MAX_RECONNECT_ATTEMPTS // 阻止重连
  if (ws) ws.close()
})
</script>

<style scoped>
.live-canvas-area {
  height: min(500px, 50vh);
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  overflow: hidden;
}
.live-iframe {
  width: 100%;
  height: 100%;
  border: none;
}
</style>
