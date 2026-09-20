<template>
  <div class="finetuned-chat ai-main">
    <div class="finetuned-head ai-pagehead">
      <div>
        <div class="ai-crumbs">能力大厅 · Skill</div>
        <h1 class="ai-title">微调模型对话</h1>
        <p class="ai-sub">选择已灰度或已激活的训练后模型，直接发起对话并做同题实测。</p>
      </div>
      <div class="finetuned-actions">
        <button type="button" class="ai-btn" :disabled="loading" @click="loadAll">
          <SfShellIcon name="refresh" />
          刷新
        </button>
        <button type="button" class="ai-btn primary" :disabled="running" @click="runCycles">
          <SfShellIcon name="flow" />
          {{ running ? '推进中' : '执行三轮' }}
        </button>
      </div>
    </div>

    <div class="finetuned-body ai-pagebody">
      <section class="model-panel ai-card">
        <header class="panel-head">
          <div>
            <div class="panel-title">可用模型</div>
            <div class="panel-sub">{{ modelSummary }}</div>
          </div>
          <span class="ai-pill" :class="availableModels.length ? 'ok' : 'warn'">
            {{ availableModels.length }}
          </span>
        </header>

        <div class="route-grid">
          <div class="route-node">
            <SfShellIcon name="gpu" />
            <strong>{{ selectedTrainingGateway?.name || '-' }}</strong>
            <span>{{ selectedTrainingGateway?.id || '无在线训练网关' }}</span>
          </div>
          <div class="route-node">
            <SfShellIcon name="cube" />
            <strong>{{ selectedDeploymentGateway?.name || '-' }}</strong>
            <span>{{ selectedDeploymentGateway?.id || '无在线部署网关' }}</span>
          </div>
        </div>

        <div v-if="blockers.length" class="blocker-row">
          <span
            v-for="blocker in blockers"
            :key="blocker.key"
            class="blocker"
            :class="blocker.severity === 'error' ? 'bad' : 'warn'"
          >
            {{ blocker.label }}
          </span>
        </div>

        <div class="model-list">
          <button
            v-for="model in availableModels"
            :key="model.id"
            type="button"
            class="model-item"
            :class="{ selected: model.id === selectedDeploymentId }"
            @click="selectModel(model.id)"
          >
            <span class="model-icon"><SfShellIcon name="bot" /></span>
            <span class="model-main">
              <strong>{{ modelLabel(model) }}</strong>
              <small>{{ model.id }}</small>
              <em>{{ modelRoute(model) }}</em>
            </span>
            <span class="model-status" :class="deploymentStatusTone(model.status)">
              {{ deploymentStatusText(model.status) }}
            </span>
          </button>

          <div v-if="!loading && !availableModels.length" class="empty-state">
            当前账号暂无 active/canary 微调模型。
          </div>
        </div>
      </section>

      <section class="chat-panel ai-card">
        <header class="panel-head">
          <div>
            <div class="panel-title">模型对话</div>
            <div class="panel-sub">{{ chatPanelSubtitle }}</div>
          </div>
          <button type="button" class="ai-btn sm" :disabled="sending || !selectedModel" @click="sendProbe">
            <SfShellIcon name="spark" />
            验证
          </button>
        </header>

        <div v-if="selectedModel" class="selected-model-strip">
          <span>{{ selectedModel.model_family || selectedModel.id }}</span>
          <span>{{ selectedModel.gatewayId || '自动寻路' }}</span>
          <span>{{ selectedModel.jobTitle || selectedModel.job_id }}</span>
        </div>

        <div v-if="lastChatMeta" class="chat-meta">
          <span>{{ lastChatMeta.backend }}</span>
          <span>{{ lastChatMeta.gateway }}</span>
          <span>{{ lastChatMeta.speed }}</span>
          <span v-if="lastChatMeta.finishReason">{{ lastChatMeta.finishReason }}</span>
        </div>

        <div class="message-list" aria-live="polite">
          <div
            v-for="message in messages"
            :key="message.id"
            class="message-row"
            :class="`role-${message.role}`"
          >
            <div class="message-bubble">{{ message.content }}</div>
          </div>
        </div>

        <form class="chat-input-row" @submit.prevent="sendMessage">
          <textarea
            v-model="draft"
            :disabled="sending || !selectedModel"
            rows="3"
            placeholder="输入要验证的问题"
          />
          <button type="submit" class="ai-btn primary" :disabled="sending || !selectedModel || !draft.trim()">
            <SfShellIcon name="send" />
            {{ sending ? '发送中' : '发送' }}
          </button>
        </form>
      </section>

      <section class="compare-panel ai-card">
        <header class="panel-head">
          <div>
            <div class="panel-title">同题实测</div>
            <div class="panel-sub">最多选择 3 个可用模型</div>
          </div>
          <button type="button" class="ai-btn sm primary" :disabled="compareRunning || !canRunCompare" @click="runCompare">
            <SfShellIcon name="play" />
            {{ compareRunning ? '测试中' : '运行' }}
          </button>
        </header>

        <textarea
          v-model="comparePrompt"
          class="compare-prompt"
          :disabled="compareRunning"
          rows="4"
          placeholder="输入同题测试问题"
        />

        <div class="compare-select-list">
          <label
            v-for="model in availableModels"
            :key="`compare-${model.id}`"
            class="compare-choice"
            :class="{ checked: compareSelection.includes(model.id) }"
          >
            <input
              type="checkbox"
              :checked="compareSelection.includes(model.id)"
              :disabled="compareRunning"
              @change="toggleCompareModel(model.id, $event)"
            />
            <span>{{ modelLabel(model) }}</span>
            <small>{{ model.status }}</small>
          </label>
        </div>

        <div class="compare-results">
          <article
            v-for="result in compareResults"
            :key="result.deploymentId"
            class="compare-result"
            :class="`state-${result.status}`"
          >
            <header>
              <strong>{{ result.label }}</strong>
              <span>{{ compareStatusText(result.status) }}</span>
            </header>
            <p v-if="result.error" class="compare-error">{{ result.error }}</p>
            <p v-else class="compare-answer">{{ result.answer || '等待返回' }}</p>
            <footer v-if="result.status !== 'running'">
              <span>{{ result.gateway || '-' }}</span>
              <span>{{ result.speed || '速度待返回' }}</span>
              <span v-if="result.durationMs">{{ formatDuration(result.durationMs) }}</span>
              <span v-if="result.finishReason">{{ result.finishReason }}</span>
            </footer>
          </article>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { trainingApi as rawTrainingApi } from '@/api'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

defineOptions({ name: 'HallFinetunedModelChat' })

type ChatMessage = {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
}

type ModelCandidate = {
  id: string
  job_id: string
  department: string
  model_family: string
  artifact_id: string
  status: string
  rollout_percent: number
  deployment_target_gateway_id: string
  deployment_runtime_profile: string
  target_skill_ids: string[]
  created_at: string
  updated_at: string
  jobTitle: string
  jobStatus: string
  gatewayId: string
}

type CompareStatus = 'running' | 'success' | 'error'

type CompareResult = {
  deploymentId: string
  label: string
  status: CompareStatus
  answer: string
  error: string
  backend: string
  gateway: string
  speed: string
  finishReason: string
  durationMs: number
}

const trainingApi: any = rawTrainingApi
const loading = ref(false)
const running = ref(false)
const sending = ref(false)
const compareRunning = ref(false)
const status = ref<any | null>(null)
const availableModels = ref<ModelCandidate[]>([])
const selectedDeploymentId = ref('')
const draft = ref('')
const lastChatMeta = ref<any | null>(null)
const comparePrompt = ref('请用三句话说明当前模型适合处理什么问题，并给出一个示例回答。')
const compareSelection = ref<string[]>([])
const compareResults = ref<CompareResult[]>([])
const messages = ref<ChatMessage[]>([])

const blockers = computed<any[]>(() => Array.isArray(status.value?.blockers) ? status.value.blockers : [])
const selectedTrainingGateway = computed(() => status.value?.selected?.training_gateway || null)
const selectedDeploymentGateway = computed(() => status.value?.selected?.deployment_gateway || null)
const latestDeploymentId = computed(() => String(status.value?.latest_deployment?.id || ''))
const completedCycles = computed(() => Number(status.value?.completed_cycles || 0))
const cycleCount = computed(() => Number(status.value?.cycle_count || 3))
const selectedModel = computed(() => availableModels.value.find(item => item.id === selectedDeploymentId.value) || null)
const canRunCompare = computed(() => compareSelection.value.length > 0 && Boolean(comparePrompt.value.trim()))
const modelSummary = computed(() => {
  const active = availableModels.value.filter(item => item.status === 'active').length
  const canary = availableModels.value.filter(item => item.status === 'canary').length
  return `${active} active / ${canary} 灰度 · ${completedCycles.value}/${cycleCount.value} 轮`
})
const chatPanelSubtitle = computed(() => {
  if (!availableModels.value.length) return '等待 active/canary 部署'
  if (!selectedModel.value) return '请选择模型'
  return `${deploymentStatusText(selectedModel.value.status)} · ${selectedModel.value.gatewayId || '自动兜底'}`
})

function textValue(value: unknown): string {
  return String(value || '').trim()
}

function listValue<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : []
}

function safeNumber(value: unknown): number {
  const num = Number(value)
  return Number.isFinite(num) ? num : 0
}

function normalizeModel(row: Record<string, unknown>): ModelCandidate {
  const job = row.job && typeof row.job === 'object' ? row.job as Record<string, unknown> : {}
  const gatewayId = textValue(row.deployment_target_gateway_id)
    || textValue(job.deployment_target_gateway_id)
    || textValue(job.target_gateway_id)
  return {
    id: textValue(row.id),
    job_id: textValue(row.job_id),
    department: textValue(row.department),
    model_family: textValue(row.model_family),
    artifact_id: textValue(row.artifact_id),
    status: textValue(row.status),
    rollout_percent: safeNumber(row.rollout_percent),
    deployment_target_gateway_id: textValue(row.deployment_target_gateway_id),
    deployment_runtime_profile: textValue(row.deployment_runtime_profile),
    target_skill_ids: listValue(row.target_skill_ids).map(item => textValue(item)).filter(Boolean),
    created_at: textValue(row.created_at),
    updated_at: textValue(row.updated_at),
    jobTitle: textValue(job.title),
    jobStatus: textValue(job.status),
    gatewayId,
  }
}

function mergeDeployments(responses: unknown[]): ModelCandidate[] {
  const byId = new Map<string, ModelCandidate>()
  for (const response of responses) {
    const record = response && typeof response === 'object' ? response as Record<string, unknown> : {}
    for (const item of listValue<Record<string, unknown>>(record.items)) {
      const model = normalizeModel(item)
      if (model.id && ['active', 'canary'].includes(model.status)) byId.set(model.id, model)
    }
  }
  return Array.from(byId.values()).sort((a, b) => {
    if (a.id === latestDeploymentId.value) return -1
    if (b.id === latestDeploymentId.value) return 1
    if (a.status !== b.status) return a.status === 'active' ? -1 : 1
    return b.updated_at.localeCompare(a.updated_at) || b.created_at.localeCompare(a.created_at)
  })
}

function modelLabel(model: ModelCandidate): string {
  return model.model_family || model.jobTitle || model.id
}

function modelRoute(model: ModelCandidate): string {
  const parts = [
    model.gatewayId || '自动兜底',
    model.department,
    model.deployment_runtime_profile,
  ].filter(Boolean)
  return parts.join(' · ')
}

function deploymentStatusText(value: string): string {
  if (value === 'active') return 'active'
  if (value === 'canary') return '灰度'
  return value || 'unknown'
}

function deploymentStatusTone(value: string): string {
  if (value === 'active') return 'ok'
  if (value === 'canary') return 'warn'
  return 'muted'
}

function resetMessages() {
  const label = selectedModel.value ? modelLabel(selectedModel.value) : '未选择模型'
  messages.value = [{
    id: `system-${Date.now()}`,
    role: 'system',
    content: `当前对话模型：${label}`,
  }]
  lastChatMeta.value = null
}

function appendMessage(role: ChatMessage['role'], content: string) {
  messages.value.push({
    id: `${role}-${Date.now()}-${messages.value.length}`,
    role,
    content,
  })
}

function chatHistoryPayload(nextUserContent: string) {
  const history = messages.value
    .filter(item => item.role === 'user' || item.role === 'assistant')
    .slice(-8)
    .map(item => ({ role: item.role, content: item.content }))
  return [
    { role: 'system', content: '禁止输出思考过程、Thinking Process、Reasoning 或内部分析，只输出最终答案。' },
    ...history,
    { role: 'user', content: nextUserContent },
  ]
}

function extractAnswer(result: any): string {
  return textValue(result?.choices?.[0]?.message?.content)
}

function formatSpeed(metrics: any): string {
  const speed = Number(metrics?.tokens_per_second || metrics?.tokensPerSecond || 0)
  return speed > 0 ? `${speed.toFixed(2)} tok/s` : '速度待返回'
}

function updateChatMeta(result: any) {
  const metrics = result?.metrics || {}
  lastChatMeta.value = {
    backend: textValue(result?.skillforge_backend) || 'model_proxy',
    gateway: textValue(result?.fallback_gateway_id) || textValue(result?.gateway_id) || '-',
    speed: formatSpeed(metrics),
    finishReason: textValue(result?.finish_reason || result?.choices?.[0]?.finish_reason),
  }
}

function compareStatusText(value: CompareStatus): string {
  if (value === 'running') return '运行中'
  if (value === 'success') return '完成'
  return '失败'
}

function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return ''
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

function selectModel(id: string) {
  selectedDeploymentId.value = id
}

function syncSelections() {
  const ids = availableModels.value.map(item => item.id)
  if (!selectedDeploymentId.value || !ids.includes(selectedDeploymentId.value)) {
    selectedDeploymentId.value = ids.includes(latestDeploymentId.value) ? latestDeploymentId.value : (ids[0] || '')
  }
  compareSelection.value = compareSelection.value.filter(id => ids.includes(id)).slice(0, 3)
  if (!compareSelection.value.length) {
    const preferred = selectedDeploymentId.value || ids[0] || ''
    compareSelection.value = preferred ? [preferred] : []
  }
}

async function loadAll() {
  loading.value = true
  try {
    const [statusResult, activeResult, canaryResult] = await Promise.all([
      trainingApi.fullHistoryFinetuneStatus().catch(() => null),
      trainingApi.deployments({ status: 'active' }),
      trainingApi.deployments({ status: 'canary' }),
    ])
    status.value = statusResult
    availableModels.value = mergeDeployments([activeResult, canaryResult])
    syncSelections()
    if (!messages.value.length) resetMessages()
  } catch (error: any) {
    Message.error(error?._message || '加载可用模型失败')
  } finally {
    loading.value = false
  }
}

async function runCycles() {
  running.value = true
  try {
    const result = await trainingApi.runFullHistoryFinetune({
      cycles: 3,
      capture_sources: true,
      blocking_source_sync: false,
      days: 3650,
      per_source_limit: 2000,
      materialize_limit: 50000,
      min_sample_count: 4,
      background_dispatch: true,
    })
    status.value = result?.after || status.value
    Message.success(result?.ok ? '三轮微调已完成' : '已推进，仍有任务等待真实节点完成')
  } catch (error: any) {
    Message.error(error?._message || '执行三轮微调失败')
  } finally {
    running.value = false
    await loadAll()
  }
}

async function sendProbe() {
  const previous = draft.value
  draft.value = '请回复 PONG，并说明当前模型部署 ID。'
  await sendMessage()
  draft.value = previous
}

async function sendMessage() {
  const content = draft.value.trim()
  const model = selectedModel.value
  if (!content || sending.value || !model) return
  const outboundMessages = chatHistoryPayload(content)
  appendMessage('user', content)
  draft.value = ''
  sending.value = true
  try {
    const result = await trainingApi.chatDeploymentModel(model.id, {
      messages: outboundMessages,
      max_tokens: 512,
      timeout_seconds: 600,
    })
    updateChatMeta(result)
    appendMessage('assistant', extractAnswer(result) || '模型没有返回文本。')
  } catch (error: any) {
    appendMessage('assistant', error?._message || '调用失败')
  } finally {
    sending.value = false
  }
}

function toggleCompareModel(id: string, event: Event) {
  const checked = Boolean((event.target as HTMLInputElement | null)?.checked)
  if (checked) {
    if (!compareSelection.value.includes(id)) {
      if (compareSelection.value.length >= 3) {
        Message.warning('同题实测最多选择 3 个模型')
        return
      }
      compareSelection.value = [...compareSelection.value, id]
    }
    return
  }
  compareSelection.value = compareSelection.value.filter(item => item !== id)
}

function setCompareResult(next: CompareResult) {
  const index = compareResults.value.findIndex(item => item.deploymentId === next.deploymentId)
  if (index >= 0) {
    compareResults.value.splice(index, 1, next)
  } else {
    compareResults.value.push(next)
  }
}

async function runCompare() {
  const prompt = comparePrompt.value.trim()
  const ids = compareSelection.value.slice(0, 3)
  if (!prompt || !ids.length || compareRunning.value) return
  compareRunning.value = true
  compareResults.value = ids.map((id) => {
    const model = availableModels.value.find(item => item.id === id)
    return {
      deploymentId: id,
      label: model ? modelLabel(model) : id,
      status: 'running',
      answer: '',
      error: '',
      backend: '',
      gateway: '',
      speed: '',
      finishReason: '',
      durationMs: 0,
    }
  })
  try {
    await Promise.all(ids.map(async (id) => {
      const model = availableModels.value.find(item => item.id === id)
      const started = window.performance.now()
      try {
        const result = await trainingApi.chatDeploymentModel(id, {
          messages: [
            { role: 'system', content: '禁止输出思考过程。只输出最终答案。' },
            { role: 'user', content: prompt },
          ],
          max_tokens: 512,
          timeout_seconds: 600,
        })
        setCompareResult({
          deploymentId: id,
          label: model ? modelLabel(model) : id,
          status: 'success',
          answer: extractAnswer(result) || '模型没有返回文本。',
          error: '',
          backend: textValue(result?.skillforge_backend),
          gateway: textValue(result?.fallback_gateway_id) || textValue(result?.gateway_id),
          speed: formatSpeed(result?.metrics || {}),
          finishReason: textValue(result?.finish_reason || result?.choices?.[0]?.finish_reason),
          durationMs: window.performance.now() - started,
        })
      } catch (error: any) {
        setCompareResult({
          deploymentId: id,
          label: model ? modelLabel(model) : id,
          status: 'error',
          answer: '',
          error: error?._message || '模型实测失败',
          backend: '',
          gateway: '',
          speed: '',
          finishReason: '',
          durationMs: window.performance.now() - started,
        })
      }
    }))
  } finally {
    compareRunning.value = false
  }
}

watch(selectedDeploymentId, (next, previous) => {
  if (next && previous && next !== previous) resetMessages()
})

onMounted(loadAll)
</script>

<style scoped>
.finetuned-chat {
  min-height: 100%;
}

.finetuned-head {
  align-items: flex-start;
}

.finetuned-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.finetuned-body {
  display: grid;
  grid-template-columns: minmax(280px, 360px) minmax(420px, 1fr) minmax(300px, 400px);
  gap: 16px;
  align-items: stretch;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 16px;
}

.panel-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--ai-text);
}

.panel-sub {
  margin-top: 4px;
  color: var(--ai-muted);
  font-size: 13px;
}

.route-grid {
  display: grid;
  gap: 10px;
}

.route-node {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr);
  column-gap: 10px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  padding: 12px;
}

.route-node svg {
  margin-top: 2px;
  color: var(--ai-accent);
}

.route-node strong,
.route-node span {
  min-width: 0;
  overflow-wrap: anywhere;
}

.route-node span {
  color: var(--ai-muted);
  font-size: 12px;
}

.blocker-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 14px 0;
}

.blocker {
  border-radius: 999px;
  padding: 4px 9px;
  font-size: 12px;
  border: 1px solid var(--ai-border);
}

.blocker.warn {
  color: var(--ai-warn);
  background: var(--ai-warn-soft);
}

.blocker.bad {
  color: var(--ai-bad);
  background: var(--ai-bad-soft);
}

.model-panel,
.chat-panel,
.compare-panel {
  min-height: 680px;
  display: flex;
  flex-direction: column;
}

.model-list,
.compare-select-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow: auto;
}

.model-list {
  margin-top: 14px;
}

.model-item {
  width: 100%;
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  gap: 10px;
  align-items: start;
  text-align: left;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  color: var(--ai-text);
  padding: 11px;
  cursor: pointer;
}

.model-item:hover,
.model-item.selected {
  border-color: var(--ai-accent);
  background: var(--ai-surface-2);
}

.model-icon {
  display: inline-flex;
  width: 28px;
  height: 28px;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
}

.model-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.model-main strong,
.model-main small,
.model-main em {
  min-width: 0;
  overflow-wrap: anywhere;
}

.model-main strong {
  font-size: 13px;
}

.model-main small,
.model-main em {
  color: var(--ai-muted);
  font-size: 12px;
  font-style: normal;
}

.model-status {
  border: 1px solid var(--ai-border);
  border-radius: 999px;
  padding: 3px 7px;
  font-size: 12px;
  white-space: nowrap;
}

.model-status.ok {
  color: var(--ai-ok);
  background: var(--ai-ok-soft);
}

.model-status.warn {
  color: var(--ai-warn);
  background: var(--ai-warn-soft);
}

.empty-state {
  border: 1px dashed var(--ai-border);
  border-radius: 8px;
  padding: 16px;
  color: var(--ai-muted);
  background: var(--ai-surface);
  text-align: center;
}

.selected-model-strip,
.chat-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: -4px 0 12px;
}

.selected-model-strip span,
.chat-meta span {
  border: 1px solid var(--ai-border);
  border-radius: 999px;
  padding: 4px 9px;
  background: var(--ai-surface);
  color: var(--ai-muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.message-list {
  flex: 1;
  min-height: 360px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  overflow: auto;
}

.message-row {
  display: flex;
}

.message-row.role-user {
  justify-content: flex-end;
}

.message-row.role-system {
  justify-content: center;
}

.message-bubble {
  max-width: min(720px, 82%);
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--ai-surface-2);
  color: var(--ai-text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.role-user .message-bubble {
  background: var(--ai-accent);
  color: var(--ai-accent-contrast);
}

.role-system .message-bubble {
  max-width: 100%;
  color: var(--ai-muted);
  background: transparent;
  border: 1px dashed var(--ai-border);
}

.chat-input-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  margin-top: 12px;
  align-items: end;
}

.chat-input-row textarea,
.compare-prompt {
  width: 100%;
  resize: vertical;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  color: var(--ai-text);
  padding: 10px 12px;
  outline: none;
}

.chat-input-row textarea {
  min-height: 82px;
}

.chat-input-row textarea:focus,
.compare-prompt:focus {
  border-color: var(--ai-accent);
}

.compare-select-list {
  max-height: 190px;
  margin-top: 12px;
}

.compare-choice {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  padding: 9px;
  color: var(--ai-text);
}

.compare-choice.checked {
  border-color: var(--ai-accent);
  background: var(--ai-surface-2);
}

.compare-choice span {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 13px;
}

.compare-choice small {
  color: var(--ai-muted);
  font-size: 12px;
}

.compare-results {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 14px;
  overflow: auto;
}

.compare-result {
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  padding: 12px;
}

.compare-result.state-success {
  border-color: color-mix(in srgb, var(--ai-ok), transparent 58%);
}

.compare-result.state-error {
  border-color: color-mix(in srgb, var(--ai-bad), transparent 55%);
}

.compare-result header,
.compare-result footer {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: space-between;
}

.compare-result header strong {
  min-width: 0;
  overflow-wrap: anywhere;
}

.compare-result header span,
.compare-result footer span {
  color: var(--ai-muted);
  font-size: 12px;
}

.compare-answer,
.compare-error {
  margin: 10px 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.6;
}

.compare-error {
  color: var(--ai-bad);
}

@media (max-width: 1280px) {
  .finetuned-body {
    grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
  }

  .compare-panel {
    grid-column: 1 / -1;
    min-height: 420px;
  }
}

@media (max-width: 900px) {
  .finetuned-body {
    grid-template-columns: 1fr;
  }

  .model-panel,
  .chat-panel,
  .compare-panel {
    min-height: auto;
  }

  .chat-input-row {
    grid-template-columns: 1fr;
  }
}
</style>
