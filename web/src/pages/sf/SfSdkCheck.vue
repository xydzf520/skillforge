<template>
  <div class="sf-sdk-page ai-main">
    <div class="sf-sdk-head ai-pagehead">
      <div>
        <div class="ai-crumbs">SkillForge · 插件 · 检查</div>
        <h1 class="ai-title">sdk</h1>
        <p class="ai-sub">Project SDK API Key、外网 URL、236 常驻模型和 GB10 237 数据落库验证。</p>
      </div>
      <div class="sf-sdk-actions">
        <button class="ai-btn" type="button" :disabled="loading || modelLoading" @click="loadPage">
          <SfShellIcon name="refresh" />
          {{ loading || modelLoading ? '刷新中' : '刷新' }}
        </button>
        <button class="ai-btn primary" type="button" :disabled="!validationToken || verifying || !callableModels.length" @click="verifySdk('one')">
          <SfShellIcon name="check" />
          验证
        </button>
        <button class="ai-btn" type="button" :disabled="!validationToken || verifying || !callableModels.length" @click="verifySdk('all')">
          <SfShellIcon name="gpu" />
          验证全部模型
        </button>
      </div>
    </div>

    <section class="sdk-status-strip" aria-label="SDK 状态">
      <article v-for="item in statusCards" :key="item.label" class="sdk-status-item" :class="item.tone">
        <span>{{ item.label }}</span>
        <strong>{{ item.value }}</strong>
        <em>{{ item.meta }}</em>
      </article>
    </section>

    <section class="sdk-layout">
      <div class="sdk-panel sdk-key-panel">
        <div class="sdk-panel-head">
          <div>
            <h2>API Key</h2>
            <p>{{ canManageSdkTokens ? '创建后完整 key 只显示一次。' : '当前账号没有 token 管理权限。' }}</p>
          </div>
          <span class="sdk-pill" :class="canManageSdkTokens ? 'ok' : 'warn'">{{ canManageSdkTokens ? '可管理' : '只读' }}</span>
        </div>

        <div class="token-create-row">
          <input v-model="tokenForm.name" :disabled="!canManageSdkTokens || creatingToken" class="sdk-input" placeholder="token 名称" />
          <input v-model="tokenForm.expiresInDays" :disabled="!canManageSdkTokens || creatingToken" class="sdk-input compact" inputmode="numeric" placeholder="365" />
          <button class="ai-btn primary" type="button" :disabled="!canManageSdkTokens || creatingToken" @click="createApiKey">
            <SfShellIcon name="plus" />
            {{ creatingToken ? '创建中' : '创建 API Key' }}
          </button>
        </div>

        <label class="api-key-box">
          <span>API Key</span>
          <textarea
            v-model="validationToken"
            spellcheck="false"
            rows="3"
            placeholder="创建 API Key 后会直接显示在这里；也可以粘贴已有 Project SDK token 用于验证。"
          />
        </label>
        <div class="key-actions">
          <button class="ai-btn sm" type="button" :disabled="!validationToken" @click="copyText(validationToken, 'API Key 已复制')">
            <SfShellIcon name="copy" />
            复制
          </button>
          <button class="ai-btn sm" type="button" :disabled="!createdApiKey" @click="validationToken = createdApiKey">
            <SfShellIcon name="bolt" />
            使用新 Key
          </button>
        </div>

        <div v-if="createdTokenRow" class="created-token-meta">
          <span>新建</span>
          <strong>{{ createdTokenRow.token_prefix }}</strong>
          <em>{{ formatTime(createdTokenRow.expires_at) }}</em>
        </div>

        <div class="token-list">
          <div class="token-list-head">
            <strong>Token 列表</strong>
            <span>{{ activeTokenCount }} active / {{ sdkTokens.length }} total</span>
          </div>
          <div v-if="sdkTokens.length" class="token-rows">
            <article v-for="token in sdkTokens" :key="token.id" class="token-row">
              <div>
                <strong>{{ token.name || token.token_prefix }}</strong>
                <span>{{ token.token_prefix }} · {{ tokenStatusLabel(token.status) }} · {{ formatTime(token.expires_at) }}</span>
              </div>
              <button class="ai-btn sm" type="button" :disabled="revokingTokenId === token.id || token.status === 'revoked'" @click="revokeApiKey(token)">
                {{ token.status === 'revoked' ? '已吊销' : '吊销' }}
              </button>
            </article>
          </div>
          <div v-else class="sdk-empty">{{ tokenLoading ? '正在加载 token' : '暂无 token' }}</div>
        </div>
      </div>

      <div class="sdk-panel">
        <div class="sdk-panel-head">
          <div>
            <h2>URL</h2>
            <p>{{ httpAccessNote }}</p>
          </div>
          <span class="sdk-pill info">{{ originProtocol }}</span>
        </div>
        <div class="url-list">
          <article v-for="item in urlRows" :key="item.key" class="url-row">
            <div>
              <span>{{ item.label }}</span>
              <code>{{ item.url }}</code>
            </div>
            <button class="icon-btn" type="button" :title="`复制 ${item.label}`" @click="copyText(item.url, 'URL 已复制')">
              <SfShellIcon name="copy" />
            </button>
          </article>
        </div>

        <div class="sdk-panel-head compact-head">
          <div>
            <h2>验证结果</h2>
            <p>{{ verifySummary }}</p>
          </div>
          <span class="sdk-pill" :class="verificationTone">{{ verificationLabel }}</span>
        </div>
        <div class="check-list">
          <article v-for="item in urlChecks" :key="item.key" class="check-row" :class="item.status">
            <span class="check-dot"></span>
            <div>
              <strong>{{ item.label }}</strong>
              <em>{{ item.detail || '-' }}</em>
            </div>
            <code>{{ item.httpStatus || '-' }}</code>
            <small>{{ item.latencyMs ? `${item.latencyMs}ms` : '-' }}</small>
          </article>
        </div>
      </div>
    </section>

    <section class="sdk-panel">
      <div class="sdk-panel-head">
        <div>
          <h2>可用模型</h2>
          <p>{{ modelSubtitle }}</p>
        </div>
        <div class="model-actions">
          <select v-model="selectedModelId" class="sdk-select">
            <option v-for="model in callableModels" :key="modelId(model)" :value="modelId(model)">
              {{ modelId(model) }}
            </option>
          </select>
          <button class="ai-btn sm" type="button" :disabled="modelLoading" @click="loadModels">
            <SfShellIcon name="refresh" />
            模型
          </button>
        </div>
      </div>
      <div class="model-grid">
        <article v-for="model in residentModels" :key="modelId(model)" class="model-row" :class="{ callable: model.callable, loaded: model.loaded }">
          <div>
            <strong>{{ modelId(model) }}</strong>
            <span>{{ model.runtime_profile || '-' }} · {{ model.deployment_id || 'base' }}</span>
          </div>
          <div class="model-tags">
            <span class="sdk-pill" :class="model.callable ? 'ok' : 'warn'">{{ model.callable ? 'callable' : 'blocked' }}</span>
            <span class="sdk-pill" :class="model.loaded ? 'ok' : 'info'">{{ model.loaded ? 'loaded' : 'registered' }}</span>
          </div>
        </article>
      </div>
    </section>

    <section class="sdk-panel">
      <div class="sdk-panel-head">
        <div>
          <h2>模型调用</h2>
          <p>{{ verifying ? `正在验证 ${verifyProgress.done}/${verifyProgress.total}` : '外网 HTTP + Bearer token + 236 chat completion；工具调用会转为 OpenAI tool_calls。' }}</p>
        </div>
        <span class="sdk-pill">GB10 237</span>
      </div>
      <div v-if="modelChecks.length" class="model-check-table">
        <article v-for="row in modelChecks" :key="`${row.model}-${row.projectRunId || row.httpStatus}`" class="model-check-row" :class="row.ok ? 'ok' : 'fail'">
          <div>
            <strong>{{ row.model }}</strong>
            <span>{{ row.contentPreview || row.error || '-' }}</span>
          </div>
          <code>{{ row.httpStatus }}</code>
          <small>{{ row.elapsedSeconds }}s</small>
          <small>{{ row.runStatus || '-' }}</small>
          <small>{{ row.sinkStatus || '-' }}</small>
          <button class="icon-btn" type="button" :disabled="!row.projectRunId" title="复制 run id" @click="copyText(row.projectRunId || '', 'Run ID 已复制')">
            <SfShellIcon name="copy" />
          </button>
        </article>
      </div>
      <div v-else class="sdk-empty">{{ verifying ? '等待模型返回' : '尚未执行模型调用验证' }}</div>
    </section>

    <section class="sdk-panel">
      <div class="sdk-panel-head">
        <div>
          <h2>调用示例</h2>
          <p>服务端保存 API Key 到环境变量，不写入前端代码；usage_reliable=false 时不要用 token usage 计费。</p>
        </div>
        <button class="ai-btn sm" type="button" @click="copyText(curlSnippet, 'curl 示例已复制')">
          <SfShellIcon name="copy" />
          复制
        </button>
      </div>
      <pre class="sdk-code"><code>{{ curlSnippet }}</code></pre>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { projectApi, type ProjectResidentModelCatalog, type ProjectResidentModelRow, type ProjectRow, type ProjectSdkTokenRow } from '@/api'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

type CheckStatus = 'pending' | 'ok' | 'fail' | 'skip'

type UrlCheck = {
  key: string
  label: string
  status: CheckStatus
  httpStatus?: number
  latencyMs?: number
  detail?: string
}

type ModelCheck = {
  model: string
  ok: boolean
  httpStatus: number
  elapsedSeconds: number
  contentPreview: string
  error: string
  projectRunId: string
  capabilityCallId: string
  runStatus: string
  sinkStatus: string
}

const SDK_PROJECT_ID = 'skillforge-company-sdk'
const DEFAULT_TOKEN_DAYS = '365'

const loading = ref(false)
const tokenLoading = ref(false)
const modelLoading = ref(false)
const creatingToken = ref(false)
const verifying = ref(false)
const revokingTokenId = ref('')
const project = ref<ProjectRow | null>(null)
const sdkTokens = ref<ProjectSdkTokenRow[]>([])
const createdApiKey = ref('')
const createdTokenRow = ref<ProjectSdkTokenRow | null>(null)
const validationToken = ref('')
const residentCatalog = ref<ProjectResidentModelCatalog | null>(null)
const selectedModelId = ref('')
const urlChecks = ref<UrlCheck[]>(defaultUrlChecks())
const modelChecks = ref<ModelCheck[]>([])
const verifyProgress = ref({ done: 0, total: 0 })
const tokenForm = ref({ name: 'company-backend', expiresInDays: DEFAULT_TOKEN_DAYS })

const origin = computed(() => window.location.origin)
const originProtocol = computed(() => window.location.protocol.replace(':', '').toUpperCase())
const urlRows = computed(() => [
  { key: 'sdk-js', label: 'Web SDK', url: new URL('/project-gateway-sdk.js', origin.value).toString() },
  { key: 'sdk-api', label: 'Project SDK API', url: new URL('/api/projects/sdk/*', origin.value).toString() },
  { key: 'models', label: '236 Models', url: new URL('/api/projects/openai/236/v1/models', origin.value).toString() },
  { key: 'chat', label: '236 Chat', url: new URL('/api/projects/openai/236/v1/chat/completions', origin.value).toString() },
])
const sdkJsUrl = computed(() => urlRows.value.find(item => item.key === 'sdk-js')?.url || '')
const modelsUrl = computed(() => urlRows.value.find(item => item.key === 'models')?.url || '')
const chatUrl = computed(() => urlRows.value.find(item => item.key === 'chat')?.url || '')
const canManageSdkTokens = computed(() => Boolean(project.value?.permissions?.edit))
const activeTokenCount = computed(() => sdkTokens.value.filter(item => tokenStatus(item.status) === 'active').length)
const residentModels = computed(() => normalizeModels(residentCatalog.value))
const callableModels = computed(() => residentModels.value.filter(item => item.callable))
const modelSubtitle = computed(() => {
  const total = Number(residentCatalog.value?.total ?? residentModels.value.length)
  const callable = Number(residentCatalog.value?.callable_count ?? callableModels.value.length)
  const loaded = Number(residentCatalog.value?.loaded_count ?? residentModels.value.filter(item => item.loaded).length)
  const gateway = String(residentCatalog.value?.gateway?.id || 'inference-primary')
  return `${gateway} · ${callable}/${total} callable · ${loaded} loaded`
})
const httpAccessNote = computed(() => {
  if (window.location.protocol === 'https:') return '当前页面使用 HTTPS。'
  const host = window.location.hostname
  if (host === 'localhost' || host === '127.0.0.1') return '本地 HTTP 调试。'
  return '当前是外网 HTTP 临时验证；生产 SDK 建议切到有效 HTTPS。'
})
const statusCards = computed(() => [
  {
    label: 'Project',
    value: project.value?.id || SDK_PROJECT_ID,
    meta: project.value?.status || 'loading',
    tone: project.value ? 'ok' : 'warn',
  },
  {
    label: 'API Key',
    value: createdApiKey.value ? '已显示' : `${activeTokenCount.value} active`,
    meta: createdApiKey.value ? 'one-time visible' : 'hash only after create',
    tone: createdApiKey.value || activeTokenCount.value ? 'ok' : 'warn',
  },
  {
    label: '236 Models',
    value: `${callableModels.value.length}/${residentModels.value.length}`,
    meta: 'callable / total',
    tone: callableModels.value.length ? 'ok' : 'warn',
  },
  {
    label: 'Training Sink',
    value: 'GB10 237',
    meta: 'NVIDIA GB10',
    tone: 'ok',
  },
])
const verificationTone = computed(() => {
  if (verifying.value) return 'info'
  if (modelChecks.value.some(item => !item.ok)) return 'bad'
  if (modelChecks.value.some(item => item.ok)) return 'ok'
  if (urlChecks.value.some(item => item.status === 'fail')) return 'bad'
  if (urlChecks.value.some(item => item.status === 'ok')) return 'ok'
  return 'info'
})
const verificationLabel = computed(() => {
  if (verifying.value) return '验证中'
  if (modelChecks.value.some(item => !item.ok)) return '失败'
  if (modelChecks.value.some(item => item.ok)) return '可用'
  return '待验证'
})
const verifySummary = computed(() => {
  const ok = modelChecks.value.filter(item => item.ok).length
  const fail = modelChecks.value.length - ok
  if (modelChecks.value.length) return `${ok} 成功 / ${fail} 失败`
  const checked = urlChecks.value.filter(item => item.status === 'ok').length
  return checked ? `${checked} 项 URL 已通过` : '尚未验证'
})
const curlSnippet = computed(() => {
  const model = selectedModelId.value || modelId(callableModels.value[0]) || '<MODEL_ID>'
  const token = validationToken.value || '<PROJECT_SDK_TOKEN>'
  return `curl '${chatUrl.value}' \\
  -H 'Authorization: Bearer ${token}' \\
  -H 'Content-Type: application/json' \\
  -d '${JSON.stringify({
    model,
    messages: [{ role: 'user', content: '只回复 ok' }],
    max_tokens: 8,
    timeout_seconds: 180,
  }, null, 2).replace(/'/g, "'\\''")}'`
})

onMounted(() => {
  loadPage()
})

function defaultUrlChecks(): UrlCheck[] {
  return [
    { key: 'sdk-js', label: 'Web SDK', status: 'pending' },
    { key: 'models-token', label: '236 models + token', status: 'pending' },
    { key: 'chat-token', label: '236 chat + GB10', status: 'pending' },
  ]
}

async function loadPage() {
  loading.value = true
  try {
    project.value = await projectApi.ensureSdkCheckProject()
    await Promise.all([loadTokens(), loadModels()])
  } catch (error: any) {
    Message.error(error?._message || '加载 SDK 检查页失败')
  } finally {
    loading.value = false
  }
}

async function loadTokens() {
  if (!project.value?.id || !canManageSdkTokens.value) {
    sdkTokens.value = []
    return
  }
  tokenLoading.value = true
  try {
    const payload = await projectApi.listSdkTokens(project.value.id)
    sdkTokens.value = payload.items || []
  } catch (error: any) {
    sdkTokens.value = []
    Message.warning(error?._message || 'SDK token 列表不可用')
  } finally {
    tokenLoading.value = false
  }
}

async function loadModels() {
  modelLoading.value = true
  try {
    residentCatalog.value = await projectApi.models236()
    if (!selectedModelId.value || !callableModels.value.some(item => modelId(item) === selectedModelId.value)) {
      selectedModelId.value = modelId(callableModels.value[0]) || ''
    }
  } catch (error: any) {
    residentCatalog.value = null
    Message.warning(error?._message || '236 模型目录不可用')
  } finally {
    modelLoading.value = false
  }
}

function tokenTtlDays(): number {
  const raw = Number.parseInt(String(tokenForm.value.expiresInDays || ''), 10)
  return Number.isFinite(raw) ? Math.max(1, Math.min(raw, 3660)) : Number(DEFAULT_TOKEN_DAYS)
}

async function createApiKey() {
  if (!project.value?.id || !canManageSdkTokens.value) return
  creatingToken.value = true
  try {
    const created = await projectApi.createSdkToken(project.value.id, {
      name: String(tokenForm.value.name || '').trim() || 'company-backend',
      expires_in_days: tokenTtlDays(),
      metadata: { source: 'sf_sdk_check_page' },
    })
    createdTokenRow.value = created
    createdApiKey.value = String(created.token || '')
    validationToken.value = createdApiKey.value
    await loadTokens()
    Message.success('API Key 已创建')
  } catch (error: any) {
    Message.error(error?._message || '创建 API Key 失败')
  } finally {
    creatingToken.value = false
  }
}

async function revokeApiKey(token: ProjectSdkTokenRow) {
  if (!project.value?.id || !token.id || token.status === 'revoked') return
  if (!window.confirm(`确认吊销 ${token.name || token.token_prefix || token.id}？`)) return
  revokingTokenId.value = token.id
  try {
    const updated = await projectApi.revokeSdkToken(project.value.id, token.id)
    sdkTokens.value = sdkTokens.value.map(item => item.id === updated.id ? updated : item)
    Message.success('API Key 已吊销')
  } catch (error: any) {
    Message.error(error?._message || '吊销 API Key 失败')
  } finally {
    revokingTokenId.value = ''
  }
}

async function verifySdk(mode: 'one' | 'all') {
  if (verifying.value) return
  verifying.value = true
  urlChecks.value = defaultUrlChecks()
  modelChecks.value = []
  verifyProgress.value = { done: 0, total: 0 }
  try {
    await verifySdkAsset()
    const token = validationToken.value.trim()
    if (!token) {
      markCheck('models-token', 'skip', undefined, undefined, '需要 API Key')
      markCheck('chat-token', 'skip', undefined, undefined, '需要 API Key')
      return
    }
    const modelsResult = await fetchJson(modelsUrl.value, {
      headers: { Authorization: `Bearer ${token}` },
    }, 180000)
    markCheck(
      'models-token',
      modelsResult.ok ? 'ok' : 'fail',
      modelsResult.httpStatus,
      modelsResult.latencyMs,
      modelsResult.ok ? `${modelCount(modelsResult.body)} 个模型` : errorText(modelsResult.body, modelsResult.text),
    )
    if (!modelsResult.ok) return
    const liveModels = normalizeModels(modelsResult.body as ProjectResidentModelCatalog)
    const targets = mode === 'all'
      ? liveModels.filter(item => item.callable).map(modelId).filter(Boolean)
      : [selectedModelId.value || modelId(liveModels.find(item => item.callable))]
    const uniqueTargets = [...new Set(targets.filter(Boolean))]
    verifyProgress.value = { done: 0, total: uniqueTargets.length }
    for (const target of uniqueTargets) {
      const row = await verifyModel(token, target)
      modelChecks.value.push(row)
      verifyProgress.value = { done: verifyProgress.value.done + 1, total: uniqueTargets.length }
    }
    const failed = modelChecks.value.find(item => !item.ok)
    const first = modelChecks.value[0]
    markCheck(
      'chat-token',
      failed ? 'fail' : modelChecks.value.length ? 'ok' : 'skip',
      first?.httpStatus,
      first ? Math.round(first.elapsedSeconds * 1000) : undefined,
      modelChecks.value.length ? `${modelChecks.value.filter(item => item.ok).length}/${modelChecks.value.length} 模型，GB10 237` : '没有可调用模型',
    )
  } catch (error: any) {
    Message.error(error?.message || 'SDK 验证失败')
  } finally {
    verifying.value = false
  }
}

async function verifySdkAsset() {
  const result = await fetchJson(sdkJsUrl.value, {}, 60000, false)
  markCheck('sdk-js', result.ok ? 'ok' : 'fail', result.httpStatus, result.latencyMs, result.ok ? '可访问' : result.text.slice(0, 120))
}

async function verifyModel(token: string, model: string): Promise<ModelCheck> {
  const requestId = `sf-sdk-${Date.now().toString(36)}-${model.replace(/[^a-zA-Z0-9]+/g, '').slice(0, 20)}`
  const started = performance.now()
  const result = await fetchJson(chatUrl.value, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      request_id: requestId,
      model,
      messages: [{ role: 'user', content: '只回复 ok，不要解释。' }],
      max_tokens: 8,
      timeout_seconds: 180,
      stream: false,
    }),
  }, 240000)
  const body = objectRecord(result.body)
  const skillforge = objectRecord(body.skillforge)
  const projectRunId = String(skillforge.project_run_id || '')
  let runStatus = ''
  let sinkStatus = ''
  if (projectRunId) {
    try {
      const summary = await projectApi.sdkCheckRun(projectRunId)
      runStatus = String(summary.run?.status || '')
      const sink = (summary.training_sink_jobs || [])[0] || {}
      sinkStatus = String(sink.status || sink.target_gateway_id || '')
    } catch {
      runStatus = 'summary_failed'
    }
  }
  const choice = normalizeList(body.choices)[0] as Record<string, unknown> | undefined
  const message = objectRecord(choice?.message)
  const content = String(message.content || '')
  return {
    model,
    ok: result.ok && Boolean(content.trim()) && (!runStatus || runStatus === 'completed'),
    httpStatus: result.httpStatus,
    elapsedSeconds: Number(((performance.now() - started) / 1000).toFixed(3)),
    contentPreview: content.slice(0, 120),
    error: result.ok ? '' : errorText(body, result.text),
    projectRunId,
    capabilityCallId: String(skillforge.capability_call_id || ''),
    runStatus,
    sinkStatus,
  }
}

async function fetchJson(url: string, init: RequestInit, timeoutMs: number, parseJson = true) {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  const started = performance.now()
  try {
    const response = await fetch(url, {
      ...init,
      cache: 'no-store',
      signal: controller.signal,
    })
    const text = await response.text()
    let body: unknown = text
    if (parseJson) {
      try {
        body = JSON.parse(text)
      } catch {
        body = {}
      }
    }
    return {
      ok: response.ok,
      httpStatus: response.status,
      latencyMs: Math.round(performance.now() - started),
      body,
      text,
    }
  } finally {
    window.clearTimeout(timer)
  }
}

function markCheck(key: string, status: CheckStatus, httpStatus?: number, latencyMs?: number, detail?: string) {
  urlChecks.value = urlChecks.value.map(item => item.key === key ? { ...item, status, httpStatus, latencyMs, detail } : item)
}

async function copyText(text: string, success: string) {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    Message.success(success)
  } catch {
    Message.info(text)
  }
}

function normalizeModels(value: ProjectResidentModelCatalog | unknown): ProjectResidentModelRow[] {
  const record = objectRecord(value)
  const rows = normalizeList(record.data || record.models)
  return rows.map(item => objectRecord(item)).filter(item => modelId(item))
}

function modelId(value: unknown): string {
  const item = objectRecord(value)
  return String(item.id || item.model || '')
}

function modelCount(value: unknown): number {
  const record = objectRecord(value)
  return Number(record.total ?? normalizeModels(record).length)
}

function objectRecord(value: unknown): Record<string, any> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, any> : {}
}

function normalizeList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function tokenStatus(value: unknown): string {
  const status = String(value || 'active').toLowerCase()
  return ['active', 'expired', 'revoked'].includes(status) ? status : 'active'
}

function tokenStatusLabel(value: unknown): string {
  return tokenStatus(value)
}

function errorText(body: unknown, fallback = ''): string {
  const record = objectRecord(body)
  const error = objectRecord(record.error)
  return String(error.code || error.message || record.message || fallback || '请求失败').slice(0, 500)
}

function formatTime(value: unknown): string {
  if (!value) return '-'
  const date = new Date(String(value))
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleString()
}
</script>

<style scoped>
.sf-sdk-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.sf-sdk-head {
  align-items: flex-start;
}

.sf-sdk-actions,
.model-actions,
.key-actions,
.token-create-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.sdk-status-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.sdk-status-item,
.sdk-panel {
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  border-radius: 8px;
}

.sdk-status-item {
  display: grid;
  gap: 4px;
  min-width: 0;
  padding: 12px;
}

.sdk-status-item span,
.sdk-status-item em,
.sdk-panel-head p,
.token-row span,
.url-row span,
.model-row span,
.model-check-row span,
.created-token-meta span,
.created-token-meta em {
  color: var(--ai-ink-3);
  font-size: 12px;
  font-style: normal;
}

.sdk-status-item strong,
.model-row strong,
.token-row strong,
.model-check-row strong {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sdk-layout {
  display: grid;
  grid-template-columns: minmax(320px, 0.9fr) minmax(420px, 1.1fr);
  gap: 16px;
}

.sdk-panel {
  min-width: 0;
  padding: 14px;
}

.sdk-panel-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 12px;
}

.sdk-panel-head h2 {
  margin: 0 0 4px;
  color: var(--ai-ink-1);
  font-size: 15px;
  font-weight: 650;
}

.sdk-panel-head p {
  margin: 0;
}

.compact-head {
  margin-top: 16px;
}

.sdk-input,
.sdk-select,
.api-key-box textarea {
  border: 1px solid var(--ai-border);
  background: var(--ai-bg);
  color: var(--ai-ink-1);
  border-radius: 6px;
  font: inherit;
}

.sdk-input,
.sdk-select {
  height: 34px;
  padding: 0 10px;
}

.sdk-input {
  flex: 1 1 180px;
  min-width: 0;
}

.sdk-input.compact {
  flex: 0 0 82px;
}

.sdk-select {
  width: min(520px, 100%);
}

.api-key-box {
  display: grid;
  gap: 6px;
  margin-top: 12px;
}

.api-key-box span {
  color: var(--ai-ink-2);
  font-size: 12px;
  font-weight: 600;
}

.api-key-box textarea {
  width: 100%;
  min-height: 74px;
  resize: vertical;
  padding: 10px;
  font-family: var(--ai-font-mono);
  font-size: 12px;
  line-height: 1.5;
}

.created-token-meta {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  margin-top: 10px;
  padding: 8px 10px;
  border: 1px solid color-mix(in srgb, var(--ai-green) 28%, transparent);
  background: color-mix(in srgb, var(--ai-green) 8%, var(--ai-surface));
  border-radius: 6px;
}

.created-token-meta strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.token-list {
  margin-top: 14px;
}

.token-list-head,
.token-row,
.url-row,
.check-row,
.model-row,
.model-check-row {
  display: flex;
  gap: 10px;
  align-items: center;
}

.token-list-head {
  justify-content: space-between;
  margin-bottom: 8px;
  color: var(--ai-ink-2);
  font-size: 12px;
}

.token-rows,
.url-list,
.check-list,
.model-grid,
.model-check-table {
  display: grid;
  gap: 8px;
}

.token-row,
.url-row,
.check-row,
.model-row,
.model-check-row {
  min-width: 0;
  padding: 9px 10px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-bg);
}

.token-row > div,
.url-row > div,
.model-row > div,
.model-check-row > div {
  min-width: 0;
  flex: 1 1 auto;
}

.token-row strong,
.token-row span,
.url-row code,
.model-row strong,
.model-row span,
.model-check-row strong,
.model-check-row span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.url-row code,
.model-check-row code,
.check-row code,
.sdk-code {
  font-family: var(--ai-font-mono);
  font-size: 12px;
}

.icon-btn {
  display: inline-flex;
  width: 30px;
  height: 30px;
  flex: 0 0 30px;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-2);
  border-radius: 6px;
  cursor: pointer;
}

.icon-btn:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.sdk-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 22px;
  padding: 0 8px;
  border: 1px solid var(--ai-border);
  border-radius: 999px;
  color: var(--ai-ink-2);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.sdk-pill.ok,
.sdk-status-item.ok,
.check-row.ok,
.model-check-row.ok {
  border-color: color-mix(in srgb, var(--ai-green) 34%, var(--ai-border));
}

.sdk-pill.warn,
.sdk-status-item.warn,
.check-row.skip {
  border-color: color-mix(in srgb, var(--ai-warn) 38%, var(--ai-border));
}

.sdk-pill.bad,
.sdk-status-item.bad,
.check-row.fail,
.model-check-row.fail {
  border-color: color-mix(in srgb, var(--ai-red) 36%, var(--ai-border));
}

.sdk-pill.info {
  border-color: color-mix(in srgb, var(--ai-blue) 34%, var(--ai-border));
}

.check-dot {
  width: 8px;
  height: 8px;
  flex: 0 0 8px;
  border-radius: 999px;
  background: var(--ai-border-strong);
}

.check-row.ok .check-dot,
.model-row.callable .sdk-pill.ok {
  background: var(--ai-green);
}

.check-row.fail .check-dot {
  background: var(--ai-red);
}

.check-row.skip .check-dot {
  background: var(--ai-warn);
}

.check-row > div {
  min-width: 0;
  flex: 1 1 auto;
}

.check-row strong,
.check-row em {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.check-row em {
  color: var(--ai-ink-3);
  font-size: 12px;
  font-style: normal;
}

.model-tags {
  display: flex;
  flex: 0 0 auto;
  gap: 6px;
}

.model-check-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 64px 72px 96px 96px 32px;
}

.sdk-code {
  overflow: auto;
  max-width: 100%;
  margin: 0;
  padding: 12px;
  border: 1px solid var(--ai-border);
  background: var(--ai-bg);
  border-radius: 6px;
  color: var(--ai-ink-1);
  line-height: 1.55;
}

.sdk-empty {
  padding: 16px;
  border: 1px dashed var(--ai-border);
  border-radius: 6px;
  color: var(--ai-ink-3);
  text-align: center;
}

@media (max-width: 1100px) {
  .sdk-status-strip,
  .sdk-layout {
    grid-template-columns: 1fr;
  }

  .model-check-row {
    grid-template-columns: minmax(0, 1fr) 60px 68px;
  }

  .model-check-row small:nth-of-type(n + 2),
  .model-check-row .icon-btn {
    display: none;
  }
}
</style>
