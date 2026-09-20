<template>
  <div class="ai-app project-detail-page" :class="{ 'app-only': appOnly, standalone: standaloneApp }">
    <aside v-if="!appOnly" class="ai-sidebar detail-sidebar">
      <div class="sidebar-kicker">
        <span class="sidebar-mark"><SfShellIcon :name="typeIcon(project?.type)" /></span>
        <div>
          <strong>{{ project?.name || route.params.id }}</strong>
          <small>{{ project?.department || '全公司项目' }}</small>
        </div>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">项目导航</div>
        <button type="button" class="ai-side-item side-button" @click="router.push('/projects')">
          <SfShellIcon name="arrowl" class="ic" />
          <span>返回项目</span>
        </button>
        <button type="button" class="ai-side-item side-button" :disabled="!entryUrl" @click="openEntry">
          <SfShellIcon name="link" class="ic" />
          <span>平台内打开</span>
        </button>
        <button type="button" class="ai-side-item side-button active">
          <SfShellIcon name="hist" class="ic" />
          <span>Run Trace</span>
          <span class="count">{{ activeRun?.id ? 1 : 0 }}</span>
        </button>
        <button type="button" class="ai-side-item side-button" :disabled="!entryUrl" @click="openAppView">
          <SfShellIcon name="play" class="ic" />
          <span>直接打开使用</span>
        </button>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">运行操作</div>
        <button type="button" class="ai-side-item side-button" :disabled="creatingRun" @click="ensureRun(true)">
          <SfShellIcon name="plus" class="ic" />
          <span>{{ creatingRun ? '新开中…' : '新开一次' }}</span>
        </button>
        <button type="button" class="ai-side-item side-button" :disabled="!activeRun" @click="submitSampleOutput">
          <SfShellIcon name="send" class="ic" />
          <span>提交示例输出</span>
        </button>
        <button type="button" class="ai-side-item side-button" :disabled="!activeRun || analyzing" @click="analyzeRun()">
          <SfShellIcon name="spark" class="ic" />
          <span>{{ analyzing ? '分析中…' : '触发 AI 分析' }}</span>
        </button>
      </div>

      <div class="trace-card ai-card">
        <span class="trace-label">ACTIVE RUN</span>
        <strong>{{ runStatusLabel(activeRun?.status) }}</strong>
        <code class="ai-mono-id">{{ activeRun?.id || 'no-run' }}</code>
      </div>
    </aside>

    <main class="ai-main">
      <header v-if="!standaloneApp" class="ai-pagehead detail-pagehead">
        <div>
          <div class="ai-crumbs">平台 / 项目 / {{ project?.id || route.params.id }}</div>
          <h1 class="ai-title">{{ project?.name || route.params.id }}</h1>
          <p class="ai-sub">
            {{ project?.description || '这个项目通过平台 Project Gateway 记录运行、能力调用和输出回传。' }}
          </p>
        </div>
        <div class="page-actions">
          <span class="ai-pill" :class="typeTone(project?.type)">{{ typeLabel(project?.type) }}</span>
          <span class="ai-pill ok">{{ project?.runtime?.containerized ? '容器运行' : '无容器运行' }}</span>
          <button type="button" class="ai-btn" :disabled="loading" @click="reloadAll">
            <SfShellIcon name="refresh" />
            刷新
          </button>
          <button type="button" class="ai-btn" :disabled="!project" @click="shareCurrentProject">
            <SfShellIcon name="link" />
            分享项目
          </button>
          <button v-if="appOnly" type="button" class="ai-btn" @click="openTraceView">
            <SfShellIcon name="hist" />
            运行看板
          </button>
          <button v-else type="button" class="ai-btn" :disabled="!entryUrl" @click="openAppView">
            <SfShellIcon name="play" />
            直接打开
          </button>
          <button type="button" class="ai-btn primary" :disabled="creatingRun" @click="ensureRun(true)">
            <SfShellIcon name="play" />
            {{ creatingRun ? '新开中…' : '重新打开一次' }}
          </button>
        </div>
      </header>

      <section class="ai-pagebody detail-pagebody">
        <section class="runtime-layout" :class="{ 'app-runtime-layout': appOnly }">
          <div class="runtime-panel ai-card" :class="{ 'standalone-runtime-panel': standaloneApp }">
            <div v-if="!standaloneApp" class="ai-card-h">
              <div>
                <span class="t">{{ appOnly ? '项目已在平台内打开' : '项目运行区' }}</span>
                <span class="s">iframe 宿主，输出通过 postMessage 写回平台</span>
              </div>
              <span class="ai-mono-id">{{ project?.runtime?.gateway || 'project_gateway' }}</span>
            </div>
            <div v-if="appOnly && !standaloneApp" class="app-status-strip">
              <div class="app-status-main">
                <span class="ai-pill dot" :class="runTone(activeRun?.status)">{{ runStatusLabel(activeRun?.status) }}</span>
                <strong>{{ activeRun?.id || '正在创建运行' }}</strong>
                <small>{{ runHint }}</small>
              </div>
              <div class="app-status-facts">
                <div v-for="item in appStatusFacts" :key="item.label" class="app-status-fact">
                  <span>{{ item.label }}</span>
                  <code>{{ item.value }}</code>
                </div>
              </div>
            </div>
            <a-spin :loading="loading || creatingRun" class="frame-spin">
              <div v-if="entryUrl && activeRun?.id" class="frame-wrap">
                <iframe
                  ref="frameRef"
                  class="project-frame"
                  :src="frameUrl"
                  sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads"
                  title="SkillForge project runtime"
                  @load="handleProjectFrameLoad"
                />
              </div>
              <div v-else-if="entryUrl" class="frame-empty">
                <SfShellIcon name="play" />
                <strong>{{ appOnly ? '正在创建项目运行上下文' : '尚未打开项目运行' }}</strong>
                <span>{{ appOnly ? '平台会先生成 ProjectRun / ExecutionRun，再把运行参数下发给网页。' : '点击“直接打开”或“新开一次”，即可在平台内运行这个项目。' }}</span>
              </div>
              <div v-else class="frame-empty">
                <SfShellIcon name="link" />
                <strong>该项目还没有入口 URL</strong>
                <span>请在平台注册入口，或上传静态项目包后运行。</span>
              </div>
            </a-spin>
          </div>

          <div v-if="!appOnly" class="inspector-stack">
            <section class="status-card ai-card">
              <div class="ai-card-h">
                <span class="t">运行状态</span>
                <span class="ai-pill dot" :class="runTone(activeRun?.status)">{{ runStatusLabel(activeRun?.status) }}</span>
              </div>
              <a-spin :loading="loading || creatingRun">
                <div class="status-box" :class="runTone(activeRun?.status)">
                  <span>{{ activeRun?.id || '未创建运行' }}</span>
                  <strong>{{ runStatusLabel(activeRun?.status) }}</strong>
                  <small>{{ runHint }}</small>
                </div>
                <div class="kv-list">
                  <div v-for="item in runtimeFacts" :key="item.label" class="kv">
                    <span>{{ item.label }}</span>
                    <code>{{ item.value }}</code>
                  </div>
                </div>
                <div class="action-grid">
                  <button type="button" class="ai-btn" :disabled="!activeRun" @click="submitSampleOutput">
                    <SfShellIcon name="send" />
                    示例输出
                  </button>
                  <button type="button" class="ai-btn primary" :disabled="!activeRun || analyzing" @click="analyzeRun()">
                    <SfShellIcon name="spark" />
                    {{ analyzing ? '分析中…' : 'AI 分析' }}
                  </button>
                </div>
              </a-spin>
            </section>

            <section class="sdk-card ai-card">
              <div class="ai-card-h">
                <div>
                  <span class="t">Project Gateway SDK</span>
                  <span class="s">浏览器 SDK + 公司后端 SDK</span>
                </div>
              </div>
              <p class="sdk-tip">网页不读取平台密钥；由父窗口网关代写入输出、报告与待办。</p>
              <pre class="code-block"><code>{{ sdkSnippet }}</code></pre>
              <div class="server-sdk-panel">
                <div class="sdk-facts">
                  <div v-for="item in serverSdkFacts" :key="item.label" class="sdk-fact">
                    <span>{{ item.label }}</span>
                    <strong>{{ item.value }}</strong>
                  </div>
                </div>
                <div class="server-sdk-head">
                  <div>
                    <strong>公司后端 SDK token</strong>
                    <span>{{ canManageSdkTokens ? '创建后只显示一次；后端用 Bearer token 调 /api/projects/sdk/*。' : '需要项目编辑权限。' }}</span>
                  </div>
                  <button type="button" class="ai-btn sm" :disabled="sdkTokenLoading || !canManageSdkTokens" @click="loadSdkTokens">
                    <SfShellIcon name="refresh" />
                    刷新
                  </button>
                </div>
                <div class="token-create-row">
                  <input v-model="sdkTokenForm.name" :disabled="!canManageSdkTokens || creatingSdkToken" class="sdk-input" placeholder="token 名称，例如 orders-api" />
                  <input v-model="sdkTokenForm.expiresInDays" :disabled="!canManageSdkTokens || creatingSdkToken" class="sdk-input short" inputmode="numeric" placeholder="365" />
                  <button type="button" class="ai-btn primary" :disabled="!canManageSdkTokens || creatingSdkToken" @click="createSdkToken">
                    <SfShellIcon name="plus" />
                    {{ creatingSdkToken ? '创建中…' : '创建 token' }}
                  </button>
                </div>
                <div v-if="createdSdkToken" class="token-secret-box">
                  <span>一次性 token</span>
                  <code>{{ createdSdkToken }}</code>
                  <button type="button" class="ai-btn sm" @click="copySdkToken">复制</button>
                </div>
                <div v-if="sdkTokens.length" class="token-list">
                  <div v-for="token in sdkTokens" :key="token.id" class="token-row">
                    <div>
                      <strong>{{ token.name || token.token_prefix }}</strong>
                      <span class="ai-mono-id">{{ token.token_prefix }} · {{ tokenStatusLabel(token.status) }}</span>
                    </div>
                    <button type="button" class="ai-btn sm" :disabled="revokingSdkTokenId === token.id || token.status === 'revoked'" @click="revokeSdkToken(token)">
                      {{ token.status === 'revoked' ? '已吊销' : '吊销' }}
                    </button>
                  </div>
                </div>
                <div v-else class="trace-empty">{{ sdkTokenLoading ? '正在加载 token…' : '暂无公司后端 SDK token。' }}</div>
                <div class="server-sdk-samples">
                  <pre class="code-block compact"><code>{{ nodeServerSdkSnippet }}</code></pre>
                  <pre class="code-block compact"><code>{{ pythonServerSdkSnippet }}</code></pre>
                </div>
              </div>
              <div class="resident-model-panel">
                <div class="server-sdk-head">
                  <div>
                    <strong>macOS 常驻模型</strong>
                    <span>{{ residentModelHint }}</span>
                  </div>
                  <button type="button" class="ai-btn sm" :disabled="residentModelLoading" @click="loadResidentModelStatus">
                    <SfShellIcon name="refresh" />
                    验证
                  </button>
                </div>
                <div class="resident-status" :class="residentModelTone">
                  <span class="ai-pill dot" :class="residentModelTone">{{ residentModelStatusLabel }}</span>
                  <strong>{{ residentModelTitle }}</strong>
                  <small>{{ residentModelSubtitle }}</small>
                </div>
                <div class="resident-url-list">
                  <div>
                    <span>Models URL</span>
                    <code>{{ openAi236ModelsUrl }}</code>
                  </div>
                  <div>
                    <span>Chat URL</span>
                    <code>{{ openAi236ChatUrl }}</code>
                  </div>
                </div>
                <div class="sdk-facts">
                  <div v-for="item in residentModelFacts" :key="item.label" class="sdk-fact">
                    <span>{{ item.label }}</span>
                    <strong>{{ item.value }}</strong>
                  </div>
                </div>
                <div v-if="residentModelPreviewRows.length" class="resident-model-list">
                  <div v-for="modelRow in residentModelPreviewRows" :key="residentModelId(modelRow)" class="resident-model-row">
                    <code>{{ residentModelId(modelRow) }}</code>
                    <span>{{ residentContextSummary(modelRow) }}</span>
                  </div>
                  <span v-if="residentGatewayModels.length > residentModelPreview.length">
                    +{{ residentGatewayModels.length - residentModelPreview.length }}
                  </span>
                </div>
                <div class="model-actions">
                  <button type="button" class="ai-btn sm" @click="openTrainingModels">模型部署</button>
                  <button type="button" class="ai-btn sm" :disabled="!residentGatewayId" @click="openResidentGateway">平台节点</button>
                </div>
              </div>
            </section>

            <section class="asset-card ai-card">
              <div class="ai-card-h">
                <div>
                  <span class="t">运行资产</span>
                  <span class="s">平台只保存通用资产，具体业务解析由项目自己编排</span>
                </div>
                <span class="ai-mono-id">{{ runAssets.length }} files</span>
              </div>
              <div class="asset-actions">
                <label class="ai-btn" :class="{ disabled: !activeRun || uploadingAsset }">
                  <SfShellIcon name="plus" />
                  {{ uploadingAsset ? '上传中…' : '上传资产' }}
                  <input type="file" multiple :disabled="!activeRun || uploadingAsset" @change="uploadRunAssets" />
                </label>
                <button type="button" class="ai-btn" :disabled="!activeRun" @click="loadRunAssets()">刷新</button>
              </div>
              <div v-if="runAssets.length" class="asset-list">
                <a v-for="asset in runAssets" :key="asset.id" class="asset-row" :href="asset.download_url" target="_blank" rel="noreferrer">
                  <span>{{ asset.file_name }}</span>
                  <small>{{ asset.source_kind || 'file' }} · {{ formatBytes(asset.byte_size || 0) }}</small>
                </a>
              </div>
              <div v-else class="trace-empty">暂无运行资产。项目可基于这些资产自行实现业务解析。</div>
            </section>
          </div>
        </section>

        <section v-if="!appOnly" class="result-grid">
          <article class="data-card ai-card">
            <div class="ai-card-h">
              <span class="t">AI 分析结果</span>
              <span class="ai-mono-id">{{ aiKeyCount }} keys</span>
            </div>
            <pre class="json-block"><code>{{ pretty(activeRun?.ai_summary || {}) }}</code></pre>
          </article>
          <article class="data-card ai-card">
            <div class="ai-card-h">
              <span class="t">输出与报告</span>
              <span class="ai-mono-id">reports {{ reportCount }} / todos {{ todoCount }}</span>
            </div>
            <pre class="json-block"><code>{{ pretty(activeRun?.output_result || {}) }}</code></pre>
          </article>
        </section>

        <section v-if="!appOnly" class="gateway-trace ai-card">
          <div class="ai-card-h">
            <div>
              <span class="t">Gateway Trace</span>
              <span class="s">项目输出、平台 AI 和能力调用的审计轨迹</span>
            </div>
            <span class="ai-mono-id">
              calls {{ traceCount('capability_calls') }} / input {{ traceCount('input_events') }} / output {{ traceCount('output_events') }}
            </span>
          </div>
          <a-spin :loading="traceLoading">
            <div v-if="traceTimeline.length" class="trace-list">
              <div v-for="row in traceTimeline" :key="`${row.kind}-${row.id}`" class="trace-row">
                <span class="ai-pill dot" :class="traceTone(row.status)">{{ traceKind(row.kind) }}</span>
                <div class="trace-main">
                  <strong>{{ traceTitle(row) }}</strong>
                  <small class="ai-mono-id">{{ row.request_id || 'no-request-id' }}</small>
                </div>
                <time class="ai-mono-id">{{ traceTime(row.created_at) }}</time>
              </div>
            </div>
            <div v-else class="trace-empty">暂无网关调用轨迹。</div>
          </a-spin>
          <div v-if="runTrace" class="trace-pagebar">
            <span class="ai-mono-id">{{ traceLoadedLabel }}</span>
            <button type="button" class="ai-btn sm" :disabled="!hasMoreTrace || traceMoreLoading" @click="loadMoreRunTrace">
              <SfShellIcon name="download" />
              {{ traceMoreLoading ? '加载中…' : hasMoreTrace ? '加载更多 Trace' : '已全部载入' }}
            </button>
          </div>
        </section>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { useRoute, useRouter } from 'vue-router'
import {
  projectApi,
  trainingApi,
  type ProjectResidentModelCatalog,
  type ProjectRow,
  type ProjectRunAssetRow,
  type ProjectRunRow,
  type ProjectRunTrace,
  type ProjectSdkTokenRow,
} from '@/api'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'
import { normalizeGatewayIngestPayload } from './gatewayPayload'
import { projectSharePath, resumableProjectRunId } from './routes'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const creatingRun = ref(false)
const analyzing = ref(false)
const uploadingAsset = ref(false)
const traceLoading = ref(false)
const traceMoreLoading = ref(false)
const sdkTokenLoading = ref(false)
const creatingSdkToken = ref(false)
const revokingSdkTokenId = ref('')
const residentModelLoading = ref(false)
const project = ref<ProjectRow | null>(null)
const activeRun = ref<ProjectRunRow | null>(null)
const runTrace = ref<ProjectRunTrace | null>(null)
const runAssets = ref<ProjectRunAssetRow[]>([])
const sdkTokens = ref<ProjectSdkTokenRow[]>([])
const createdSdkToken = ref('')
const trainingResources = ref<Array<Record<string, unknown>>>([])
const trainingDeployments = ref<Array<Record<string, unknown>>>([])
const residentModelCatalog = ref<ProjectResidentModelCatalog | null>(null)
const residentModelError = ref('')
const sdkTokenForm = ref({
  name: 'company-backend',
  expiresInDays: '365',
})
const frameRef = ref<HTMLIFrameElement | null>(null)
let heartbeatTimer: ReturnType<typeof setInterval> | null = null
let openRunRequestId = ''
let gatewaySessionToken = ''
let gatewayContextRunId = ''
let gatewayContextIssuedRunId = ''
const mediaEventStreams = new Map<string, EventSource>()
const tracePageSize = 100

function closeMediaEventStreams() {
  mediaEventStreams.forEach(stream => stream.close())
  mediaEventStreams.clear()
}

function handleProjectFrameLoad() {
  // An iframe reload creates a new project SDK runtime. EventSources owned by
  // the previous frame can no longer deliver to its listener, but they remain
  // alive in the parent unless explicitly closed. Clear them before issuing a
  // fresh gateway context so the new workbench can subscribe immediately and
  // receive job/quality completion events instead of hitting the two-stream
  // safety limit with orphaned subscriptions.
  closeMediaEventStreams()
  postRuntimeContext(true)
}

const projectId = computed(() => String(route.params.id || ''))
const standaloneApp = computed(() => route.meta.standaloneProject === true || route.query.shell === 'none')
const appOnly = computed(() => standaloneApp.value || route.query.view === 'app')
const entryUrl = computed(() => project.value?.entry_url || '')
const declaredCapabilities = computed(() => normalizeList(project.value?.metadata?.capabilities))
const declaredOutputs = computed(() => {
  const outputs = project.value?.metadata?.outputs
  return outputs && typeof outputs === 'object' && !Array.isArray(outputs) ? outputs as Record<string, unknown> : {}
})
const frameUrl = computed(() => buildFrameUrl(entryUrl.value))
const gatewaySdkUrl = computed(() => {
  const path = project.value?.runtime?.sdk_path || '/project-gateway-sdk.js'
  return new URL(path, window.location.origin).toString()
})
const openAi236ModelsUrl = computed(() => new URL('/api/projects/openai/236/v1/models', window.location.origin).toString())
const openAi236ChatUrl = computed(() => new URL('/api/projects/openai/236/v1/chat/completions', window.location.origin).toString())
const gatewayLimits = computed(() => objectRecord(project.value?.runtime?.gateway_limits))
const gatewayContext = computed(() => ({
  protocol: 'postMessage',
  platform_origin: window.location.origin,
  sdk_url: gatewaySdkUrl.value,
  gateway_limits: gatewayLimits.value,
  request: {
    context: 'skillforge.project.context.request',
    heartbeat: 'skillforge.project.heartbeat',
    close: 'skillforge.project.close',
    input: 'skillforge.project.input',
    ingest: 'skillforge.project.ingest',
    analyze: 'skillforge.project.analyze',
    capability: 'skillforge.project.capability',
    assets_list: 'skillforge.project.assets.list',
    assets_upload: 'skillforge.project.assets.upload',
  },
  response: {
    context: 'skillforge.project.context',
    heartbeat: 'skillforge.project.heartbeat.result',
    close: 'skillforge.project.close.result',
    input: 'skillforge.project.input.result',
    ingest: 'skillforge.project.ingest.result',
    analyze: 'skillforge.project.analyze.result',
    capability: 'skillforge.project.capability.result',
    assets_list: 'skillforge.project.assets.list.result',
    assets_upload: 'skillforge.project.assets.upload.result',
  },
}))
const sdkSnippet = computed(() => `<!-- 上传项目可直接引用平台 Project Gateway SDK；外部 URL 项目请使用绝对地址 -->
<script src="${gatewaySdkUrl.value}"><\/script>
<script>
async function boot() {
  const gateway = window.PlatformProjectGateway
  const ctx = await gateway.ready() // project/run/capabilities/gateway
  gateway.startHeartbeat({ intervalMs: 25000 })

  // 用户在项目里的查询/表单/筛选输入先进入平台 Trace / Learning Loop
  await gateway.input({
    input: { keyword: '今日运营', filters: { department: ctx.project.department } }
  })

  // 平台 AI / 数据能力：网页不持有 key，由 Project Gateway 代调用并记录
  const result = await gateway.capability({
    capability: 'ai.generate', // 或 manifest 已声明的 mcp://...
    prompt: '请总结当前页面数据',
    input: { project: ctx.project, rows: [] }
  })
  console.log('AI result:', result)

  // 输出回传：进入 Run Trace / DecisionLog / Learning Loop
  await gateway.ingest({
    output: { summary: result?.result?.output || result.output || result.text || '完成分析' },
    reports: [{ title: '项目报告', summary: '关键结论' }],
    todos: []
  })
}
boot().catch(console.error)
<\/script>`)

const canManageSdkTokens = computed(() => Boolean(project.value?.permissions?.edit))
const activeSdkTokenCount = computed(() => sdkTokens.value.filter(item => tokenStatusValue(item.status) === 'active').length)
const serverSdkFacts = computed(() => [
  { label: 'SDK', value: 'sdk/node · sdk/python' },
  { label: '登录', value: canManageSdkTokens.value ? 'Web 登录后创建 token' : '需要项目编辑权限' },
  { label: '默认权限', value: 'run/capability/trace' },
  { label: '稳定性', value: 'timeout + retry + 429' },
  { label: 'OpenAI URL', value: openAi236ChatUrl.value },
  { label: '模型 key', value: 'platform_only' },
  { label: '训练存储', value: 'GB10 237' },
  { label: '活跃 token', value: activeSdkTokenCount.value },
])
const residentSnippetModel = computed(() => String(residentModel.value?.id || residentModel.value?.model || 'skillforge-base-qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive'))
const nodeServerSdkSnippet = computed(() => `// Node: sdk/node/index.ts
import { SkillForgeProjectSdkError, createSkillForgeProjectSdk } from './sdk/node'

const sf = createSkillForgeProjectSdk({
  baseUrl: process.env.SKILLFORGE_BASE_URL || '${window.location.origin}',
  projectId: '${projectId.value}',
  token: process.env.SKILLFORGE_PROJECT_TOKEN!,
  timeoutMs: 180_000,
  maxRetries: 2,
})

try {
  const run = await sf.startRun({ request_id: 'order-A1001', input: { order_id: 'A1001' } })
  const models = await sf.list236Models()
  const ai = await sf.chat236(run.projectRunId, '${residentSnippetModel.value}', [
    { role: 'user', content: '生成订单分析' },
  ], { order_id: 'A1001' })
  await sf.ingest(run.projectRunId, { output: ai.result || ai.output })
  console.log(models.total, ai.trainingSink) // target_gateway_id: GB10 237
} catch (error) {
  if (error instanceof SkillForgeProjectSdkError && error.category === 'quota_error') {
    console.warn('retry later', error.retryAfterSeconds)
  }
  throw error
}`)
const pythonServerSdkSnippet = computed(() => `# Python: sdk/python/skillforge_project_sdk.py
import os
from skillforge_project_sdk import SkillForgeProjectSdk, SkillForgeProjectSdkError

sf = SkillForgeProjectSdk(
    base_url=os.environ.get("SKILLFORGE_BASE_URL", "${window.location.origin}"),
    project_id="${projectId.value}",
    token=os.environ["SKILLFORGE_PROJECT_TOKEN"],
    timeout=180.0,
    max_retries=2,
)

try:
    run = sf.start_run({"request_id": "order-A1001", "input": {"order_id": "A1001"}})
    models = sf.list_236_models()
    ai = sf.chat_236(run["projectRunId"], "${residentSnippetModel.value}", [
        {"role": "user", "content": "生成订单分析"},
    ], {"order_id": "A1001"})
    sf.ingest(run["projectRunId"], {"output": ai.get("result") or ai.get("output")})
except SkillForgeProjectSdkError as exc:
    if exc.category == "quota_error":
        print("retry later", exc.retry_after_seconds)
    raise`)
const macTrainingResources = computed(() => trainingResources.value.filter(isMacTrainingResource))
const resident236Gateway = computed(() => objectRecord(residentModelCatalog.value?.gateway))
const resident236Models = computed(() => normalizeRecordList(residentModelCatalog.value?.data || residentModelCatalog.value?.models))
const resident236Total = computed(() => Number(residentModelCatalog.value?.total ?? resident236Models.value.length))
const resident236CallableCount = computed(() => Number(residentModelCatalog.value?.callable_count ?? resident236Models.value.filter(item => item.callable).length))
const resident236LoadedCount = computed(() => Number(residentModelCatalog.value?.loaded_count ?? resident236Models.value.filter(item => item.loaded).length))
const residentDeployments = computed(() => {
  const rows = trainingDeployments.value
    .filter(item => ['active', 'canary'].includes(String(item.status || '')))
    .filter(item => {
      const target = deploymentGatewayId(item)
      return !target || target === 'inference-primary' || target.startsWith('platform-mac-') || macTrainingResources.value.some(resource => String(resource.id || '') === target)
    })
  return [...rows].sort((left, right) => {
    const leftRank = String(left.status || '') === 'active' ? 0 : 1
    const rightRank = String(right.status || '') === 'active' ? 0 : 1
    if (leftRank !== rightRank) return leftRank - rightRank
    return String(right.updated_at || right.created_at || '').localeCompare(String(left.updated_at || left.created_at || ''))
  })
})
const residentDeployment = computed(() => residentDeployments.value[0] || null)
const residentGatewayId = computed(() => {
  const catalogGateway = String(resident236Gateway.value.id || '')
  if (catalogGateway) return catalogGateway
  const deploymentTarget = residentDeployment.value ? deploymentGatewayId(residentDeployment.value) : ''
  if (deploymentTarget) return deploymentTarget
  return String(macTrainingResources.value[0]?.id || '')
})
const residentGateway = computed(() => {
  const id = residentGatewayId.value
  if (resident236Gateway.value.id) {
    return { ...resident236Gateway.value, id, resident_models: resident236Models.value }
  }
  return trainingResources.value.find(item => String(item.id || '') === id) || macTrainingResources.value[0] || null
})
const residentGatewayModels = computed(() => resident236Models.value.length ? resident236Models.value : normalizeRecordList(residentGateway.value?.resident_models))
const residentModel = computed(() => {
  const deploymentId = String(residentDeployment.value?.id || '')
  if (deploymentId) {
    const matched = residentGatewayModels.value.find(item => String(item.deployment_id || '') === deploymentId)
    if (matched) return matched
  }
  return residentGatewayModels.value.find(item => item.loaded || item.callable) || residentGatewayModels.value[0] || null
})
const residentModelStatusLabel = computed(() => {
  if (residentModelLoading.value) return '验证中'
  if (residentModelError.value) return '不可验证'
  if (resident236Total.value > 0 && residentGateway.value?.online) return resident236LoadedCount.value >= resident236Total.value ? '全部常驻' : '可调用'
  if (resident236Total.value > 0) return '节点离线'
  if (residentModel.value?.loaded && residentGateway.value?.online) return '常驻'
  if (residentDeployment.value && residentGateway.value?.online) return '已部署'
  if (residentDeployment.value) return '待节点确认'
  return residentGateway.value ? '未上报模型' : '未发现'
})
const residentModelTone = computed(() => {
  if (residentModelLoading.value) return 'info'
  if (residentModelError.value) return 'warn'
  if (resident236CallableCount.value > 0 && residentGateway.value?.online) return 'ok'
  if (residentModel.value?.loaded && residentGateway.value?.online) return 'ok'
  if (residentDeployment.value && residentGateway.value?.online) return 'warn'
  return residentGateway.value ? 'warn' : 'bad'
})
const residentModelTitle = computed(() => {
  if (resident236Total.value > 1) return `${resident236Total.value} 个 236 模型`
  return String(
    residentModel.value?.id
    || residentModel.value?.model
    || residentDeployment.value?.model_family
    || residentDeployment.value?.deployment_runtime_profile
    || 'macOS 平台节点模型',
  )
})
const residentModelSubtitle = computed(() => {
  if (residentModelError.value) return residentModelError.value
  if (residentGateway.value) {
    const status = residentGateway.value.online ? '在线' : '离线'
    return `${residentGatewayId.value || 'inference-primary'} · ${platformLabel(residentGateway.value.bridge_platform)} · ${status}`
  }
  return '等待 macOS Bridge 上报或 active/canary deployment 绑定。'
})
const residentModelHint = computed(() => {
  if (resident236CallableCount.value > 0) return 'Project SDK token 可调用 236 模型；调用数据写入 Trace，并排队同步到 GB10 237。'
  if (residentModel.value?.loaded && residentGateway.value?.online) return 'SDK 经 SkillForge 路由到 macOS 常驻模型；训练数据进入 GB10 237。'
  if (residentDeployment.value) return '已找到部署记录，等待 macOS Bridge 常驻模型上报。'
  return '验证 inference-primary 或其它 macOS 平台节点。'
})
const residentModelFacts = computed(() => [
  { label: '平台节点', value: residentGatewayId.value || '-' },
  { label: '平台', value: platformLabel(residentGateway.value?.bridge_platform) },
  { label: '可调用模型', value: resident236CallableCount.value || resident236Total.value || residentGatewayModels.value.length || 0 },
  { label: '常驻 loaded', value: resident236LoadedCount.value || residentGatewayModels.value.filter(item => item.loaded).length || 0 },
  { label: '上下文窗口', value: tokenLimitLabel(residentModel.value?.context_window) },
  { label: '推荐输入', value: tokenLimitLabel(residentModel.value?.recommended_input_tokens) },
  { label: 'Deployment', value: String(residentDeployment.value?.id || residentModel.value?.deployment_id || '-') },
  { label: 'Runtime', value: String(residentDeployment.value?.deployment_runtime_profile || residentModel.value?.runtime_profile || '-') },
  { label: '模型状态', value: residentModelStatusLabel.value },
  { label: '上下文实测', value: contextStatusLabel(residentModel.value?.context_status) },
  { label: '训练数据', value: 'GB10 237' },
])
const residentModelPreview = computed(() => residentGatewayModels.value.slice(0, 6).map(item => String(item.id || item.model || '')).filter(Boolean))
const residentModelPreviewRows = computed(() => residentGatewayModels.value.slice(0, 6))

const reportCount = computed(() => Number(activeRun.value?.report_count ?? activeRun.value?.report_cards?.length ?? 0))
const todoCount = computed(() => Number(activeRun.value?.todo_count ?? 0))
const aiKeyCount = computed(() => Object.keys(activeRun.value?.ai_summary || {}).length)
const latestCapability = computed(() => objectRecord(activeRun.value?.latest_capability_call))
const latestCapabilityLabel = computed(() => {
  if (!latestCapability.value.id) return '-'
  const key = String(latestCapability.value.capability_key || 'capability')
  const status = capabilityStatusLabel(latestCapability.value.status)
  const latency = latestCapability.value.latency_ms
  const latencyLabel = typeof latency === 'number' ? ` · ${latency}ms` : ''
  return `${key} · ${status}${latencyLabel}`
})
const traceTimeline = computed(() => runTrace.value?.timeline || [])
const tracePagination = computed(() => objectRecord(runTrace.value?.pagination))
const hasMoreTrace = computed(() => Boolean(tracePagination.value.has_more_ingress_events || tracePagination.value.has_more_capability_calls))
const traceLoadedLabel = computed(() => {
  const ingressLoaded = runTrace.value?.ingress_events?.length || 0
  const capabilityLoaded = runTrace.value?.capability_calls?.length || 0
  const ingressTotal = traceCount('ingress_events')
  const capabilityTotal = traceCount('capability_calls')
  return `已载入 ${ingressLoaded}/${ingressTotal} 输入输出 · ${capabilityLoaded}/${capabilityTotal} 能力`
})
const runHint = computed(() => {
  const status = runtimeBucket(activeRun.value?.status)
  if (status === 'running') return '用户正在使用，输出可随时回传。'
  if (status === 'stale') return '运行心跳已超时，平台已保留历史输入输出，可重新打开恢复。'
  if (status === 'waiting_ai') return '输出已进入平台，等待 AI 分析。'
  if (status === 'ai_completed') return 'AI 分析已沉淀到运行记录。'
  if (status === 'failed') return activeRun.value?.error || '运行或 AI 分析失败。'
  return '打开项目会自动创建一次可追踪运行。'
})

const runtimeFacts = computed(() => [
  { label: 'Project', value: project.value?.id || '-' },
  { label: 'ExecutionRun', value: activeRun.value?.execution_run_id || '-' },
  { label: 'DecisionLog', value: activeRun.value?.decision_log_id || '-' },
  { label: '部门', value: project.value?.department || '全公司' },
  { label: 'Gateway', value: project.value?.runtime?.gateway || 'project_gateway' },
  { label: '能力调用', value: activeRun.value?.capability_call_count ?? 0 },
  { label: '最新能力', value: latestCapabilityLabel.value },
  { label: 'Heartbeat', value: traceTime(activeRun.value?.last_heartbeat_at) },
  { label: '存活态', value: livenessLabel(activeRun.value?.liveness) },
])
const appStatusFacts = computed(() => [
  { label: '最新能力', value: latestCapabilityLabel.value },
  { label: '能力调用', value: activeRun.value?.capability_call_count ?? 0 },
  { label: '报告/待办', value: `${reportCount.value}/${todoCount.value}` },
  { label: 'Heartbeat', value: traceTime(activeRun.value?.last_heartbeat_at) },
])

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value || {}, null, 2)
  } catch {
    return String(value || '')
  }
}

function traceCount(key: string): number {
  const counts = runTrace.value?.counts || {}
  const totalValue = counts[`${key}_total`]
  if (typeof totalValue === 'number') return totalValue
  const value = counts[key]
  return typeof value === 'number' ? value : 0
}

function traceKind(value: unknown): string {
  const kind = String(value || '')
  if (kind === 'capability') return '能力'
  if (kind === 'input') return '输入'
  return '输出'
}

function capabilityStatusLabel(value: unknown): string {
  const map: Record<string, string> = {
    running: '运行中',
    completed: '已完成',
    failed: '失败',
  }
  return map[String(value || '')] || String(value || '未知')
}

function mergeRunRow(next: ProjectRunRow, previous = activeRun.value): ProjectRunRow {
  if (next.latest_capability_call || !previous?.latest_capability_call) return next
  return { ...next, latest_capability_call: previous.latest_capability_call }
}

function traceTone(value: unknown): string {
  const status = String(value || '')
  if (['completed', 'ai_completed', 'output'].includes(status)) return 'ok'
  if (['running', 'waiting_ai'].includes(status)) return 'warn'
  if (['failed', 'ai_failed'].includes(status)) return 'bad'
  return ''
}

function traceTitle(row: Record<string, unknown>): string {
  return String(row.title || row.status || row.kind || 'trace')
}

function traceTime(value: unknown): string {
  return String(value || '-').replace('T', ' ').slice(0, 16)
}

function formatBytes(value: number): string {
  const size = Number(value || 0)
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${size} B`
}

function gatewayErrorText(error: any, fallback: string): string {
  const status = error?.response?.status
  const backendCode = String(error?._backendCode || error?.response?.data?.error?.code || error?.response?.data?.code || '')
  const backendMessage = String(error?._backendMessage || '')
  const friendly = String(error?._message || error?.message || '')
  if (backendCode === 'PROJECT_ASSET_TOO_LARGE') {
    const detail = objectRecord(error?.response?.data?.error?.detail || error?.response?.data?.detail)
    const actual = Number(detail.actual_bytes || 0)
    const max = Number(detail.max_bytes || 0)
    const sizeText = actual || max
      ? `文件大小 ${actual ? formatBytes(actual) : '未知'}，上限 ${max ? formatBytes(max) : '未知'}`
      : '文件超过平台上传上限'
    return `${fallback}：${sizeText}`
  }
  if (backendMessage) return `${fallback}：${backendMessage}`
  if (status) return `${fallback}：HTTP ${status}${friendly ? ` · ${friendly}` : ''}`
  if (error?.code === 'ECONNABORTED') return `${fallback}：请求超时，请检查文件大小或网络后重试`
  if (friendly && friendly !== fallback) return `${fallback}：${friendly}`
  return fallback
}

function gatewayAssetErrorText(error: any, file?: File | Blob | null): string {
  const name = file && 'name' in file ? String(file.name || '') : ''
  const size = file && 'size' in file ? Number(file.size || 0) : 0
  const suffix = [name, size ? formatBytes(size) : ''].filter(Boolean).join(' · ')
  const fallback = `上传运行资产失败${suffix ? `（${suffix}）` : ''}`
  return gatewayErrorText(error, fallback)
}

function sdkTokenTtlDays(): number {
  const value = Number.parseInt(String(sdkTokenForm.value.expiresInDays || ''), 10)
  if (!Number.isFinite(value)) return 365
  return Math.max(1, Math.min(value, 3660))
}

async function loadSdkTokens() {
  if (!project.value?.id || !canManageSdkTokens.value) {
    sdkTokens.value = []
    return
  }
  sdkTokenLoading.value = true
  try {
    const data = await projectApi.listSdkTokens(project.value.id)
    sdkTokens.value = data.items || []
  } catch (error: any) {
    sdkTokens.value = []
    Message.warning(error?._message || 'SDK token 列表不可用')
  } finally {
    sdkTokenLoading.value = false
  }
}

async function createSdkToken() {
  if (!project.value?.id || !canManageSdkTokens.value) return
  creatingSdkToken.value = true
  try {
    const created = await projectApi.createSdkToken(project.value.id, {
      name: String(sdkTokenForm.value.name || '').trim() || 'company-backend',
      expires_in_days: sdkTokenTtlDays(),
      metadata: { source: 'project_detail_sdk_panel' },
    })
    createdSdkToken.value = String(created.token || '')
    await loadSdkTokens()
    Message.success('Project SDK token 已创建')
  } catch (error: any) {
    Message.error(error?._message || '创建 Project SDK token 失败')
  } finally {
    creatingSdkToken.value = false
  }
}

async function copySdkToken() {
  if (!createdSdkToken.value) return
  const copied = await copyToClipboard(createdSdkToken.value)
  if (copied) Message.success('SDK token 已复制')
  else Message.info(createdSdkToken.value)
}

async function revokeSdkToken(token: ProjectSdkTokenRow) {
  if (!project.value?.id || !token.id || token.status === 'revoked') return
  const confirmed = window.confirm(`确认吊销 ${token.name || token.token_prefix || token.id}？`)
  if (!confirmed) return
  revokingSdkTokenId.value = token.id
  try {
    const updated = await projectApi.revokeSdkToken(project.value.id, token.id)
    sdkTokens.value = sdkTokens.value.map(item => item.id === updated.id ? updated : item)
    Message.success('Project SDK token 已吊销')
  } catch (error: any) {
    Message.error(error?._message || '吊销 Project SDK token 失败')
  } finally {
    revokingSdkTokenId.value = ''
  }
}

async function loadResidentModelStatus() {
  residentModelLoading.value = true
  residentModelError.value = ''
  try {
    const [resourcesPayload, deploymentsPayload, catalogPayload] = await Promise.all([
      trainingApi.resources(),
      trainingApi.deployments(),
      projectApi.models236(),
    ])
    trainingResources.value = normalizeRecordList((resourcesPayload as Record<string, unknown> | undefined)?.items)
    trainingDeployments.value = normalizeRecordList((deploymentsPayload as Record<string, unknown> | undefined)?.items)
    residentModelCatalog.value = catalogPayload
  } catch (error: any) {
    trainingResources.value = []
    trainingDeployments.value = []
    residentModelCatalog.value = null
    residentModelError.value = error?._message || '无法读取 236 模型目录或训练节点状态'
  } finally {
    residentModelLoading.value = false
  }
}

function openTrainingModels() {
  router.push('/training/models')
}

function openResidentGateway() {
  if (!residentGatewayId.value) return
  router.push(`/admin/agent-devices/${encodeURIComponent(residentGatewayId.value)}`)
}

function runtimeBucket(status?: string): string {
  const raw = String(status || 'none')
  if (['opening', 'running'].includes(raw)) return 'running'
  if (raw === 'stale') return 'stale'
  if (raw === 'waiting_ai') return 'waiting_ai'
  if (['ai_completed', 'completed'].includes(raw)) return 'ai_completed'
  if (['ai_failed', 'failed'].includes(raw)) return 'failed'
  return 'none'
}

function runStatusLabel(status?: string): string {
  const map: Record<string, string> = {
    opening: '打开中',
    running: '使用中',
    stale: '已失活',
    waiting_ai: '等待 AI',
    ai_completed: 'AI 已完成',
    ai_failed: 'AI 失败',
    completed: '已完成',
    failed: '失败',
    none: '未运行',
  }
  return map[String(status || 'none')] || String(status || '未运行')
}

function runTone(status?: string): string {
  const bucket = runtimeBucket(status)
  if (bucket === 'running') return 'info'
  if (bucket === 'stale') return 'warn'
  if (bucket === 'waiting_ai') return 'warn'
  if (bucket === 'ai_completed') return 'ok'
  if (bucket === 'failed') return 'bad'
  return ''
}

function typeLabel(type?: string): string {
  const map: Record<string, string> = {
    playbook: 'Playbook',
    external_web: '外部网页',
    web_static: '静态网页',
    dashboard: '仪表盘',
    internal_tool: '内部工具',
    playbook_ref: 'Playbook 引用',
  }
  return map[String(type || '')] || String(type || '项目')
}

function typeIcon(type?: string): string {
  const map: Record<string, string> = {
    playbook: 'book',
    external_web: 'link',
    web_static: 'doc',
    dashboard: 'trend',
    internal_tool: 'cube',
    playbook_ref: 'book',
  }
  return map[String(type || '')] || 'folder'
}

function typeTone(type?: string): string {
  const map: Record<string, string> = {
    playbook: 'accent',
    external_web: 'info',
    web_static: 'ok',
    dashboard: 'warn',
    internal_tool: '',
    playbook_ref: 'accent',
  }
  return map[String(type || '')] || ''
}

function newRequestId(prefix = 'req'): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`
}

function livenessLabel(value?: string): string {
  const map: Record<string, string> = {
    online: '在线',
    stale: '失活',
    waiting_ai: '等待 AI',
    terminal: '终态',
    unknown: '未知',
  }
  return map[String(value || 'unknown')] || String(value || '未知')
}

function normalizeList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => String(item || '').trim()).filter(Boolean)
}

function normalizeRecordList(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter(item => item && typeof item === 'object' && !Array.isArray(item)) as Array<Record<string, unknown>>
    : []
}

function objectRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function tokenStatusValue(value: unknown): string {
  const status = String(value || 'active').toLowerCase()
  if (['active', 'expired', 'revoked'].includes(status)) return status
  return 'active'
}

function tokenStatusLabel(value: unknown): string {
  const map: Record<string, string> = {
    active: 'active',
    expired: 'expired',
    revoked: 'revoked',
  }
  return map[tokenStatusValue(value)] || 'active'
}

function platformLabel(value: unknown): string {
  const platform = String(value || '').toLowerCase()
  if (platform === 'darwin') return 'macOS'
  if (platform === 'linux') return 'Linux'
  if (platform === 'win32' || platform === 'windows') return 'Windows'
  return platform || '-'
}

function numericValue(value: unknown): number {
  const number = Number(value || 0)
  return Number.isFinite(number) && number > 0 ? number : 0
}

function tokenLimitLabel(value: unknown): string {
  const number = numericValue(value)
  if (!number) return '-'
  if (number >= 1000) return `${Math.round(number / 1000)}k tokens`
  return `${number} tokens`
}

function contextStatusLabel(value: unknown): string {
  const status = String(value || '').toLowerCase()
  if (status === 'verified') return '已实测'
  if (status === 'probing') return '实测中'
  if (status === 'failed') return '实测失败'
  return '未实测'
}

function residentModelId(item: Record<string, unknown>): string {
  return String(item.id || item.model || '')
}

function residentContextSummary(item: Record<string, unknown>): string {
  const recommended = tokenLimitLabel(item.recommended_input_tokens)
  const windowSize = tokenLimitLabel(item.context_window)
  return `${contextStatusLabel(item.context_status)} · 输入 ${recommended} · 窗口 ${windowSize}`
}

function isMacTrainingResource(item: Record<string, unknown>): boolean {
  const platform = String(item.bridge_platform || item.platform || '').toLowerCase()
  const id = String(item.id || '')
  return platform === 'darwin' || id === 'inference-primary' || id.startsWith('platform-mac-')
}

function deploymentGatewayId(item: Record<string, unknown>): string {
  const job = objectRecord(item.job)
  return String(
    item.deployment_target_gateway_id
    || item.target_gateway_id
    || job.deployment_target_gateway_id
    || job.target_gateway_id
    || '',
  )
}

function gatewayClone<T>(value: T, fallback: T): T {
  try {
    if (value === undefined || value === null) return fallback
    return JSON.parse(JSON.stringify(value)) as T
  } catch {
    return fallback
  }
}

function buildFrameUrl(raw: string): string {
  if (!raw) return ''
  try {
    const url = new URL(raw, window.location.origin)
    if (project.value?.id) url.searchParams.set('sf_project_id', project.value.id)
    if (activeRun.value?.id) url.searchParams.set('sf_run_id', activeRun.value.id)
    url.searchParams.set('sf_gateway', 'postMessage')
    url.searchParams.set('sf_gateway_origin', window.location.origin)
    url.searchParams.set('sf_gateway_sdk', gatewaySdkUrl.value)
    if (url.origin === window.location.origin) return `${url.pathname}${url.search}${url.hash}`
    return url.toString()
  } catch {
    return raw
  }
}

function ensureGatewaySessionToken(runId: string): string {
  if (gatewayContextRunId !== runId || !gatewaySessionToken) {
    gatewayContextRunId = runId
    gatewaySessionToken = newRequestId('pgw_token')
    gatewayContextIssuedRunId = ''
  }
  return gatewaySessionToken
}

function incomingGatewayToken(data: Record<string, unknown>): string {
  const payload = objectRecord(data.payload)
  return String(data.gateway_token || data.gatewayToken || payload.gateway_token || payload.gatewayToken || '')
}

function hasValidGatewayToken(data: Record<string, unknown>): boolean {
  return Boolean(gatewaySessionToken && incomingGatewayToken(data) === gatewaySessionToken)
}

function sanitizedGatewayPayload(data: Record<string, unknown>): Record<string, unknown> {
  const payload = { ...objectRecord(data.payload) }
  delete payload.gateway_token
  delete payload.gatewayToken
  delete payload.session_token
  delete payload.sessionToken
  return payload
}

function gatewayResultType(type: string): string {
  const map: Record<string, string> = {
    'skillforge.project.heartbeat': 'skillforge.project.heartbeat.result',
    'skillforge.project.close': 'skillforge.project.close.result',
    'skillforge.project.input': 'skillforge.project.input.result',
    'skillforge.project.ingest': 'skillforge.project.ingest.result',
    'skillforge.project.analyze': 'skillforge.project.analyze.result',
    'skillforge.project.capability': 'skillforge.project.capability.result',
    'skillforge.project.ai': 'skillforge.project.capability.result',
    'skillforge.project.assets.list': 'skillforge.project.assets.list.result',
    'skillforge.project.assets.upload': 'skillforge.project.assets.upload.result',
  }
  return map[type] || `${type}.result`
}

function rejectGatewayMessage(frameWindow: Window, data: Record<string, unknown>, error: string) {
  frameWindow.postMessage({
    type: gatewayResultType(String(data.type || 'skillforge.project.gateway')),
    request_id: String(data.request_id || data.requestId || ''),
    ok: false,
    error,
  }, '*')
}

function postRuntimeContext(force = false) {
  const frameWindow = frameRef.value?.contentWindow
  if (!frameWindow || !project.value || !activeRun.value?.id) return
  const runId = activeRun.value.id
  const token = ensureGatewaySessionToken(runId)
  if (!force && gatewayContextIssuedRunId === runId) return
  gatewayContextIssuedRunId = runId
  const capabilities = [...declaredCapabilities.value]
  const outputs = gatewayClone(declaredOutputs.value, {})
  const limits = gatewayClone(gatewayLimits.value, {})
  const inputSnapshot = gatewayClone(activeRun.value.input_snapshot || {}, {})
  const latestCapability = activeRun.value.latest_capability_call
    ? gatewayClone(activeRun.value.latest_capability_call, {})
    : null
  const gateway = {
    protocol: 'postMessage',
    platform_origin: window.location.origin,
    sdk_url: gatewaySdkUrl.value,
    gateway_limits: limits,
    request: {
      context: 'skillforge.project.context.request',
      heartbeat: 'skillforge.project.heartbeat',
      close: 'skillforge.project.close',
      input: 'skillforge.project.input',
      ingest: 'skillforge.project.ingest',
      analyze: 'skillforge.project.analyze',
      capability: 'skillforge.project.capability',
      assets_list: 'skillforge.project.assets.list',
      assets_upload: 'skillforge.project.assets.upload',
    },
    response: {
      context: 'skillforge.project.context',
      heartbeat: 'skillforge.project.heartbeat.result',
      close: 'skillforge.project.close.result',
      input: 'skillforge.project.input.result',
      ingest: 'skillforge.project.ingest.result',
      analyze: 'skillforge.project.analyze.result',
      capability: 'skillforge.project.capability.result',
      assets_list: 'skillforge.project.assets.list.result',
      assets_upload: 'skillforge.project.assets.upload.result',
    },
    session_required: true,
    session_token: token,
  }
  frameWindow.postMessage({
    type: 'skillforge.project.context',
    ok: true,
    gateway_token: token,
    project: {
      id: project.value.id,
      name: project.value.name,
      type: project.value.type,
      department_id: project.value.department_id,
      department: project.value.department,
      visibility: project.value.visibility,
      current_version_id: project.value.current_version_id,
      capabilities,
      outputs,
    },
    capabilities,
    outputs,
    limits,
    platform: {
      origin: window.location.origin,
      sdk_url: gatewaySdkUrl.value,
    },
    run: {
      id: activeRun.value.id,
      project_id: activeRun.value.project_id,
      version_id: activeRun.value.version_id,
      execution_run_id: activeRun.value.execution_run_id,
      decision_log_id: activeRun.value.decision_log_id,
      status: activeRun.value.status,
      liveness: activeRun.value.liveness,
      input_snapshot: inputSnapshot,
      latest_capability_call: latestCapability,
    },
    gateway,
    gateway_security: {
      session_required: true,
      token_transport: 'postMessage.gateway_token',
    },
  }, '*')
}

async function openEntry() {
  if (!entryUrl.value) return
  await openAppView()
}

async function openAppView() {
  const hasActiveRun = Boolean(activeRun.value?.id)
  const activeRunIsResumable = Boolean(resumableProjectRunId(activeRun.value))
  if (project.value?.id && (!hasActiveRun || !activeRunIsResumable)) await ensureRun(hasActiveRun)
  const query = { ...route.query, view: 'app', ...(activeRun.value?.id ? { run_id: activeRun.value.id } : {}) }
  await router.replace({ path: route.path, query })
  postRuntimeContext(true)
}

async function openTraceView() {
  const query = { ...route.query }
  delete query.view
  await router.replace({ path: route.path, query })
  postRuntimeContext(true)
}

async function shareCurrentProject() {
  const id = project.value?.id || projectId.value
  const href = router.resolve(projectSharePath(id)).href
  const url = new URL(href, window.location.origin).toString()
  const copied = await copyToClipboard(url)
  if (copied) {
    Message.success('项目分享链接已复制；访问者需登录，未登录会引导扫码登录')
  } else {
    Message.info(`项目分享链接：${url}`)
  }
}

async function copyToClipboard(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.setAttribute('readonly', 'true')
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(textarea)
      return ok
    } catch {
      return false
    }
  }
}

function clearHeartbeatTimer() {
  if (!heartbeatTimer) return
  clearInterval(heartbeatTimer)
  heartbeatTimer = null
}

async function heartbeatRun(metadata: Record<string, unknown> = { source: 'project_detail' }) {
  if (!activeRun.value?.id) return
  try {
    activeRun.value = mergeRunRow(await projectApi.heartbeatRun(activeRun.value.id, {
      status: 'running',
      metadata,
    }))
  } catch {
    // 心跳失败不弹连续提示，Run Trace/刷新会暴露最终状态。
  }
}

async function closeActiveRun(metadata: Record<string, unknown> = { source: 'project_detail_close' }, rethrow = false) {
  if (!activeRun.value?.id) return null
  const runId = activeRun.value.id
  try {
    activeRun.value = mergeRunRow(await projectApi.closeRun(runId, { metadata }))
    clearHeartbeatTimer()
    return activeRun.value
  } catch (error) {
    if (rethrow) throw error
    return null
  }
}

function startHeartbeatTimer() {
  if (!activeRun.value?.id) return
  clearHeartbeatTimer()
  void heartbeatRun({ source: 'project_detail_open' })
  heartbeatTimer = setInterval(() => {
    void heartbeatRun()
  }, 25_000)
}

async function loadProject() {
  loading.value = true
  try {
    const data = await projectApi.get(projectId.value)
    if (data.type === 'playbook' && data.entry_url) {
      await router.replace(data.entry_url)
      return
    }
    project.value = data
    // The standalone/app-only project host only needs project metadata and a
    // run context before it can mount the iframe. SDK tokens and resident
    // training-model status belong to the project detail panels and can be
    // comparatively expensive (several independent admin/training requests).
    // Waiting for them here made an otherwise healthy static project look
    // unavailable while the host was still showing "creating run context".
    if (!appOnly.value) {
      await Promise.all([
        loadSdkTokens(),
        loadResidentModelStatus(),
      ])
    }
  } catch (error: any) {
    Message.error(error?._message || '加载项目失败')
  } finally {
    loading.value = false
  }
}

async function loadRun(runId: string) {
  try {
    activeRun.value = mergeRunRow(await projectApi.getRun(runId))
    await loadRunTrace(runId)
    await loadRunAssets(runId)
    startHeartbeatTimer()
    postRuntimeContext(true)
  } catch (error: any) {
    Message.error(error?._message || '加载项目运行失败')
  }
}

async function loadRunAssets(runId = activeRun.value?.id || '') {
  if (!runId) {
    runAssets.value = []
    return []
  }
  const data = await projectApi.listRunAssets(runId)
  runAssets.value = data.items || []
  return runAssets.value
}

async function uploadRunAssets(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  if (!activeRun.value?.id || !files.length) return
  uploadingAsset.value = true
  try {
    for (const file of files) {
      const form = new FormData()
      form.append('file', file)
      form.append('metadata_json', JSON.stringify({ source: 'project_detail_asset_panel' }))
      await projectApi.uploadRunAsset(activeRun.value.id, form)
    }
    await loadRunAssets(activeRun.value.id)
    await loadRunTrace(activeRun.value.id)
    postRuntimeContext(true)
    Message.success('运行资产已上传')
  } catch (error: any) {
    Message.error(gatewayAssetErrorText(error))
  } finally {
    uploadingAsset.value = false
  }
}

function gatewayFileFromPayload(payload: Record<string, unknown>): File | Blob | null {
  const value = payload.file || payload.asset || payload.blob
  if (typeof File !== 'undefined' && value instanceof File) return value
  if (typeof Blob !== 'undefined' && value instanceof Blob) return value
  return null
}

function traceRowKey(row: Record<string, unknown>): string {
  return `${String(row.kind || row.event_type || 'trace')}:${String(row.id || row.request_id || '')}`
}

function mergeTraceRows(
  current: Array<Record<string, unknown>> = [],
  incoming: Array<Record<string, unknown>> = [],
): Array<Record<string, unknown>> {
  const rows = new Map<string, Record<string, unknown>>()
  for (const row of current) rows.set(traceRowKey(row), row)
  for (const row of incoming) rows.set(traceRowKey(row), row)
  return [...rows.values()]
}

function mergeTracePage(current: ProjectRunTrace | null, next: ProjectRunTrace, append: boolean): ProjectRunTrace {
  if (!append || !current) return next
  const timeline = mergeTraceRows(current.timeline || [], next.timeline || [])
    .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')))
  return {
    ...next,
    ingress_events: mergeTraceRows(current.ingress_events || [], next.ingress_events || []),
    capability_calls: mergeTraceRows(current.capability_calls || [], next.capability_calls || []),
    timeline,
  }
}

async function loadRunTrace(
  runId = activeRun.value?.id,
  options: { append?: boolean; offset?: number; limit?: number; ingressCursor?: string; capabilityCursor?: string } = {},
) {
  if (!runId) {
    runTrace.value = null
    return
  }
  const append = Boolean(options.append)
  if (append) traceMoreLoading.value = true
  else traceLoading.value = true
  try {
    const params: Record<string, unknown> = { limit: options.limit || tracePageSize }
    if (options.ingressCursor || options.capabilityCursor) {
      if (options.ingressCursor) params.ingress_cursor = options.ingressCursor
      if (options.capabilityCursor) params.capability_cursor = options.capabilityCursor
    } else {
      params.offset = options.offset || 0
    }
    const next = await projectApi.getRunTrace(runId, params)
    runTrace.value = mergeTracePage(runTrace.value, next, append)
  } catch (error: any) {
    Message.error(error?._message || '加载项目网关轨迹失败')
  } finally {
    if (append) traceMoreLoading.value = false
    else traceLoading.value = false
  }
}

async function loadMoreRunTrace() {
  if (!activeRun.value?.id || !hasMoreTrace.value || traceMoreLoading.value) return
  const ingressCursor = String(tracePagination.value.next_ingress_cursor || '')
  const capabilityCursor = String(tracePagination.value.next_capability_cursor || '')
  if (ingressCursor || capabilityCursor) {
    await loadRunTrace(activeRun.value.id, { append: true, ingressCursor, capabilityCursor, limit: tracePageSize })
    return
  }
  const offset = Number(tracePagination.value.next_offset || Number(tracePagination.value.offset || 0) + Number(tracePagination.value.limit || tracePageSize))
  await loadRunTrace(activeRun.value.id, { append: true, offset, limit: tracePageSize })
}

async function ensureRun(forceNew = false) {
  if (!project.value?.id || project.value.type === 'playbook') return
  const queryRunId = typeof route.query.run_id === 'string' ? route.query.run_id : ''
  if (!forceNew && queryRunId) {
    await loadRun(queryRunId)
    return
  }
  creatingRun.value = true
  try {
    const requestId = forceNew ? newRequestId('project_run') : (openRunRequestId || (openRunRequestId = newRequestId('project_run')))
    const run = await projectApi.createRun(project.value.id, { request_id: requestId, params: { source: 'project_page' } })
    activeRun.value = mergeRunRow(run)
    await router.replace({ path: route.path, query: { ...route.query, run_id: run.id } })
    await loadRunTrace(run.id)
    startHeartbeatTimer()
    postRuntimeContext(true)
  } catch (error: any) {
    Message.error(error?._message || '创建项目运行失败')
  } finally {
    creatingRun.value = false
  }
}

async function bootstrapRun() {
  const queryRunId = typeof route.query.run_id === 'string' ? route.query.run_id : ''
  if (queryRunId) {
    await loadRun(queryRunId)
    return
  }
  const latestRunId = String(project.value?.latest_run?.id || '')
  if (!appOnly.value) {
    if (latestRunId) await loadRun(latestRunId)
    return
  }
  await ensureRun(false)
}

async function reloadAll() {
  await loadProject()
  const queryRunId = typeof route.query.run_id === 'string' ? route.query.run_id : activeRun.value?.id
  if (queryRunId) {
    await loadRun(queryRunId)
    return
  }
  await bootstrapRun()
}

async function submitSampleOutput() {
  if (!activeRun.value?.id || !project.value) return
  try {
    const now = new Date().toISOString()
    activeRun.value = mergeRunRow(await projectApi.ingest(activeRun.value.id, {
      auto_analyze: false,
      status: 'completed',
      output: {
        summary: `${project.value.name || project.value.id} 示例输出已进入平台。`,
        metrics: [{ label: '提交时间', value: now }],
      },
      reports: [
        {
          title: `${project.value.name || project.value.id} 项目报告`,
          summary: '这是项目宿主页生成的示例报告，可进入收件与 AI 学习闭环。',
          tags: ['project', 'sample'],
        },
      ],
    }))
    await loadRunTrace(activeRun.value.id)
    postRuntimeContext(true)
    Message.success('示例输出已写入')
  } catch (error: any) {
    Message.error(error?._message || '提交示例输出失败')
  }
}

async function refreshActiveRunAfterGatewayFailure() {
  if (!activeRun.value?.id) return
  try {
    activeRun.value = mergeRunRow(await projectApi.getRun(activeRun.value.id))
    await loadRunTrace(activeRun.value.id)
    postRuntimeContext(true)
  } catch {
    // 网关失败后的状态刷新不再叠加错误提示；下一次手动刷新仍可恢复。
  }
}

async function analyzeRun(requestId = '', prompt = '请分析这个项目输出，给出结论、风险和下一步建议。') {
  if (!activeRun.value?.id) return
  analyzing.value = true
  try {
    const payload: Record<string, unknown> = { prompt }
    if (requestId) payload.request_id = requestId
    const result = await projectApi.analyze(activeRun.value.id, {
      ...payload,
    })
    activeRun.value = mergeRunRow(await projectApi.getRun(activeRun.value.id))
    await loadRunTrace(activeRun.value.id)
    postRuntimeContext(true)
    if (result?.ok === false) Message.warning('AI 分析未成功，请查看状态详情')
    else Message.success('AI 分析已完成')
  } catch (error: any) {
    Message.error(error?._message || 'AI 分析失败')
  } finally {
    analyzing.value = false
  }
}

async function handleProjectMessage(event: MessageEvent) {
  const frameWindow = frameRef.value?.contentWindow
  if (!frameWindow || event.source !== frameWindow) return
  const data = event.data as Record<string, unknown> & { type?: string; request_id?: string; requestId?: string; payload?: Record<string, unknown>; prompt?: string }
  if (!data || typeof data !== 'object') return
  if (data.type === 'skillforge.project.ready' || data.type === 'skillforge.project.context.request') {
    postRuntimeContext(true)
    return
  }
  if (!activeRun.value?.id) return
  if (!hasValidGatewayToken(data)) {
    rejectGatewayMessage(frameWindow, data, '项目网关会话已过期，请先调用 gateway.ready() 重新获取上下文。')
    return
  }
  if (data.type === 'skillforge.project.media.subscribe') {
    const requestId = data.request_id || data.requestId || ''
    const payload = sanitizedGatewayPayload(data)
    const subscriptionId = newRequestId('media_subscription')
    try {
      if (mediaEventStreams.size >= 2) throw new Error('同一项目页面最多允许 2 个媒体事件订阅')
      const url = new URL(`/api/projects/runs/${encodeURIComponent(activeRun.value.id)}/media/events`, window.location.origin)
      const afterCursor = String(payload.after_cursor || payload.afterCursor || '')
      if (afterCursor) url.searchParams.set('after_cursor', afterCursor)
      const stream = new EventSource(url.toString(), { withCredentials: true })
      mediaEventStreams.set(subscriptionId, stream)
      const forward = (eventName: string, sourceEvent: Event) => {
        const message = sourceEvent as MessageEvent<string>
        let parsed: unknown = message.data
        try { parsed = JSON.parse(message.data || '{}') } catch { /* keep raw data */ }
        frameWindow.postMessage({
          type: 'skillforge.project.media.event',
          subscription_id: subscriptionId,
          event: eventName,
          data: gatewayClone(parsed, {}),
        }, '*')
      }
      stream.addEventListener('snapshot', event => forward('snapshot', event))
      stream.addEventListener('heartbeat', event => forward('heartbeat', event))
      ;['job.updated', 'batch.updated', 'continuation.updated', 'node.updated', 'review.updated', 'training.updated'].forEach(eventName => {
        stream.addEventListener(eventName, event => forward(eventName, event))
      })
      stream.onerror = event => forward('error', event)
      frameWindow.postMessage({
        type: 'skillforge.project.media.subscribe.result', request_id: requestId, ok: true,
        result: { subscription_id: subscriptionId, transport: 'sse', capability_quota_counted: false },
      }, '*')
    } catch (error: any) {
      frameWindow.postMessage({
        type: 'skillforge.project.media.subscribe.result', request_id: requestId, ok: false,
        error: error?._message || error?.message || '媒体事件订阅失败',
      }, '*')
    }
    return
  }
  if (data.type === 'skillforge.project.media.unsubscribe') {
    const requestId = data.request_id || data.requestId || ''
    const payload = sanitizedGatewayPayload(data)
    const subscriptionId = String(payload.subscription_id || '')
    mediaEventStreams.get(subscriptionId)?.close()
    mediaEventStreams.delete(subscriptionId)
    frameWindow.postMessage({
      type: 'skillforge.project.media.unsubscribe.result', request_id: requestId, ok: true,
      result: { subscription_id: subscriptionId, closed: true },
    }, '*')
    return
  }
  if (data.type === 'skillforge.project.heartbeat') {
    const requestId = data.request_id || data.requestId || ''
    try {
      activeRun.value = mergeRunRow(await projectApi.heartbeatRun(activeRun.value.id, {
        status: 'running',
        metadata: sanitizedGatewayPayload(data),
      }))
      frameWindow.postMessage({
        type: 'skillforge.project.heartbeat.result',
        request_id: requestId,
        ok: true,
        result: { last_heartbeat_at: activeRun.value.last_heartbeat_at, status: activeRun.value.status },
      }, '*')
    } catch (error: any) {
      frameWindow.postMessage({
        type: 'skillforge.project.heartbeat.result',
        request_id: requestId,
        ok: false,
        error: error?._message || error?.message || '项目心跳失败',
      }, '*')
    }
    return
  }
  if (data.type === 'skillforge.project.close') {
    const requestId = data.request_id || data.requestId || ''
    try {
      const closedRun = await closeActiveRun({ source: 'project_gateway_close', ...sanitizedGatewayPayload(data) }, true)
      await loadRunTrace(activeRun.value?.id)
      frameWindow.postMessage({
        type: 'skillforge.project.close.result',
        request_id: requestId,
        ok: true,
        result: {
          run_id: activeRun.value?.id,
          status: activeRun.value?.status,
          closed: Boolean(closedRun?.closed),
          completed_at: activeRun.value?.completed_at,
        },
      }, '*')
      postRuntimeContext(true)
    } catch (error: any) {
      frameWindow.postMessage({
        type: 'skillforge.project.close.result',
        request_id: requestId,
        ok: false,
        error: error?._message || error?.message || '项目关闭失败',
      }, '*')
    }
    return
  }
  if (data.type === 'skillforge.project.input') {
    const requestId = data.request_id || data.requestId || ''
    try {
      const recordedRun = mergeRunRow(await projectApi.recordInput(activeRun.value.id, {
        request_id: requestId,
        ...sanitizedGatewayPayload(data),
      }), activeRun.value)
      activeRun.value = recordedRun
      await loadRunTrace(recordedRun.id)
      activeRun.value = mergeRunRow(recordedRun, activeRun.value)
      const responseRun = activeRun.value
      frameWindow.postMessage({
        type: 'skillforge.project.input.result',
        request_id: requestId,
        ok: true,
        result: {
          run_id: responseRun.id,
          status: responseRun.status,
          input_event_id: responseRun.input_event_id,
          input_snapshot: gatewayClone(responseRun.input_snapshot || {}, {}),
        },
      }, '*')
      postRuntimeContext(true)
    } catch (error: any) {
      frameWindow.postMessage({
        type: 'skillforge.project.input.result',
        request_id: requestId,
        ok: false,
        error: error?._message || error?.message || '项目输入记录失败',
      }, '*')
      Message.error(error?._message || '项目输入记录失败')
    }
    return
  }
  if (data.type === 'skillforge.project.ingest') {
    const requestId = data.request_id || data.requestId || ''
    try {
      const ingestPayload = normalizeGatewayIngestPayload(sanitizedGatewayPayload(data), requestId)
      const ingestedRun = mergeRunRow(await projectApi.ingest(activeRun.value.id, ingestPayload), activeRun.value)
      activeRun.value = ingestedRun
      await loadRunTrace(ingestedRun.id)
      activeRun.value = mergeRunRow(ingestedRun, activeRun.value)
      const responseRun = activeRun.value
      frameWindow.postMessage({
        type: 'skillforge.project.ingest.result',
        request_id: requestId,
        ok: true,
        result: {
          run_id: responseRun.id,
          status: responseRun.status,
          report_count: responseRun.report_count,
          todo_count: responseRun.todo_count,
          ai_summary: gatewayClone(responseRun.ai_summary || {}, {}),
        },
      }, '*')
      postRuntimeContext(true)
      Message.success((ingestPayload.auto_analyze === false) ? '项目输出已回传' : '项目输出已回传并进入 AI 分析')
    } catch (error: any) {
      frameWindow.postMessage({
        type: 'skillforge.project.ingest.result',
        request_id: requestId,
        ok: false,
        error: error?._message || error?.message || '项目输出回传失败',
      }, '*')
      Message.error(error?._message || '项目输出回传失败')
    }
    return
  }
  if (data.type === 'skillforge.project.analyze') {
    const requestId = data.request_id || data.requestId || ''
    try {
      const payload = sanitizedGatewayPayload(data)
      await analyzeRun(requestId, String(payload.prompt || '请分析这个项目输出，给出结论、风险和下一步建议。'))
      frameWindow.postMessage({
        type: 'skillforge.project.analyze.result',
        request_id: requestId,
        ok: true,
        result: { run_id: activeRun.value?.id, status: activeRun.value?.status },
      }, '*')
    } catch (error: any) {
      frameWindow.postMessage({
        type: 'skillforge.project.analyze.result',
        request_id: requestId,
        ok: false,
        error: error?._message || error?.message || '项目 AI 分析失败',
      }, '*')
    }
    return
  }
  if (data.type === 'skillforge.project.assets.list') {
    const requestId = data.request_id || data.requestId || ''
    try {
      const items = await loadRunAssets(activeRun.value.id)
      frameWindow.postMessage({
        type: 'skillforge.project.assets.list.result',
        request_id: requestId,
        ok: true,
        result: { run_id: activeRun.value.id, items: gatewayClone(items, []), total: items.length },
      }, '*')
      postRuntimeContext(true)
    } catch (error: any) {
      frameWindow.postMessage({
        type: 'skillforge.project.assets.list.result',
        request_id: requestId,
        ok: false,
        error: error?._message || error?.message || '项目资产列表读取失败',
      }, '*')
    }
    return
  }
  if (data.type === 'skillforge.project.assets.upload') {
    const requestId = data.request_id || data.requestId || ''
    let uploadFile: File | Blob | null = null
    try {
      const payload = sanitizedGatewayPayload(data)
      const file = gatewayFileFromPayload(payload)
      uploadFile = file
      if (!file) throw new Error('项目资产上传缺少 file 字段')
      const form = new FormData()
      const name = String(payload.file_name || (file instanceof File ? file.name : '') || 'asset')
      form.append('file', file, name)
      form.append('metadata_json', JSON.stringify({
        source: 'project_gateway_asset_upload',
        ...objectRecord(payload.metadata),
      }))
      const uploaded = await projectApi.uploadRunAsset(activeRun.value.id, form, (event) => {
        const loaded = Math.max(0, Number(event.loaded || 0))
        const total = Math.max(0, Number(event.total || file.size || 0))
        const percent = total ? Math.min(99, Math.round((loaded / total) * 100)) : 0
        frameWindow.postMessage({
          type: 'skillforge.project.assets.upload.progress',
          request_id: requestId,
          ok: true,
          progress: { stage: 'uploading', loaded_bytes: loaded, total_bytes: total, percent },
        }, '*')
      })
      await loadRunAssets(activeRun.value.id)
      await loadRunTrace(activeRun.value.id)
      frameWindow.postMessage({
        type: 'skillforge.project.assets.upload.result',
        request_id: requestId,
        ok: true,
        result: gatewayClone(uploaded, {}),
      }, '*')
      postRuntimeContext(true)
    } catch (error: any) {
      frameWindow.postMessage({
        type: 'skillforge.project.assets.upload.result',
        request_id: requestId,
        ok: false,
        error: gatewayAssetErrorText(error, uploadFile),
      }, '*')
    }
    return
  }
  if (data.type === 'skillforge.project.capability' || data.type === 'skillforge.project.ai') {
    const requestId = data.request_id || data.requestId || ''
    try {
      const gatewayPayload = sanitizedGatewayPayload(data)
      const payload = {
        request_id: requestId,
        ...gatewayPayload,
        ...(data.type === 'skillforge.project.ai' ? { capability: gatewayPayload.capability || 'ai.generate' } : {}),
      }
      const result = await projectApi.callCapability(activeRun.value.id, payload)
      activeRun.value = mergeRunRow(await projectApi.getRun(activeRun.value.id))
      await loadRunTrace(activeRun.value.id)
      frameWindow.postMessage({
        type: 'skillforge.project.capability.result',
        request_id: requestId,
        ok: true,
        result: gatewayClone(result, {}),
      }, '*')
      postRuntimeContext(true)
    } catch (error: any) {
      await refreshActiveRunAfterGatewayFailure()
      const detail = objectRecord(error?.response?.data?.error?.detail)
      frameWindow.postMessage({
        type: 'skillforge.project.capability.result',
        request_id: requestId,
        ok: false,
        error: error?._message || error?.message || '项目能力调用失败',
        detail: gatewayClone(detail, {}),
      }, '*')
      Message.error(error?._message || '项目能力调用失败')
    }
    return
  }
}

onMounted(async () => {
  window.addEventListener('message', handleProjectMessage)
  await loadProject()
  await bootstrapRun()
})

onBeforeUnmount(() => {
  if (appOnly.value && runtimeBucket(activeRun.value?.status) === 'running') {
    void closeActiveRun({ source: 'project_detail_unmount', view: 'app' })
  }
  clearHeartbeatTimer()
  closeMediaEventStreams()
  window.removeEventListener('message', handleProjectMessage)
})
</script>

<style scoped>
.project-detail-page {
  min-height: calc(100vh - 104px);
  display: flex;
  background: var(--ai-bg);
}

.project-detail-page.app-only {
  min-height: calc(100vh - 104px);
}

.project-detail-page.app-only .ai-main {
  min-width: 0;
}

.project-detail-page.standalone {
  display: block;
  height: 100vh;
  min-height: 100vh;
  overflow: hidden;
  background: #fff;
}

.project-detail-page.standalone .ai-main {
  height: 100vh;
  min-height: 0;
  background: #fff;
}

.project-detail-page.standalone .detail-pagebody {
  height: 100vh;
  min-height: 0;
  padding: 0;
  gap: 0;
}

.project-detail-page.standalone .runtime-layout,
.project-detail-page.standalone .runtime-panel,
.project-detail-page.standalone .frame-spin {
  height: 100%;
  min-height: 0;
}

.project-detail-page.standalone .runtime-panel {
  border: 0;
  border-radius: 0;
  background: #fff;
}

.project-detail-page.standalone .frame-spin :deep(.arco-spin),
.project-detail-page.standalone .frame-spin :deep(.arco-spin-children) {
  display: block;
  height: 100%;
}

.project-detail-page.standalone .frame-wrap {
  height: 100%;
  min-height: 0;
  border-top: 0;
}

.project-detail-page.standalone .frame-empty {
  min-height: 100vh;
}

.detail-sidebar {
  position: sticky;
  top: 0;
  height: calc(100vh - 104px);
  overflow: auto;
}

.sidebar-kicker {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 8px 16px;
  border-bottom: 1px solid var(--ai-border);
  margin-bottom: 16px;
}

.sidebar-kicker strong,
.sidebar-kicker small {
  display: block;
}

.sidebar-kicker strong {
  max-width: 140px;
  overflow: hidden;
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar-kicker small {
  color: var(--ai-ink-4);
  font-size: 11.5px;
}

.sidebar-mark {
  display: grid;
  width: 30px;
  height: 30px;
  flex: 0 0 30px;
  place-items: center;
  border: 1px solid var(--ai-border);
  border-radius: 7px;
  color: var(--ai-ink-3);
  background: var(--ai-surface-2);
}

.side-button {
  width: 100%;
  border: 0;
  background: transparent;
  text-align: left;
}

.side-button:disabled {
  cursor: not-allowed;
  opacity: 0.52;
}

.trace-card {
  margin: 20px 4px 0;
  padding: 12px;
}

.trace-card span,
.trace-card strong,
.trace-card code {
  display: block;
}

.trace-label {
  margin-bottom: 8px;
  color: var(--ai-ink-4);
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.08em;
}

.trace-card strong {
  margin-bottom: 4px;
  color: var(--ai-ink-1);
  font-size: 18px;
  font-weight: 500;
  letter-spacing: -0.03em;
}

.detail-pagehead {
  align-items: flex-start;
}

.page-actions,
.runtime-layout,
.result-grid,
.action-grid,
.kv,
.status-box,
.modal-actions {
  display: flex;
  gap: 10px;
}

.page-actions {
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.page-actions .ai-btn {
  flex: 0 0 auto;
  white-space: nowrap;
}

.detail-pagebody {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.runtime-layout {
  align-items: flex-start;
}

.app-runtime-layout {
  align-items: stretch;
}

.runtime-panel {
  min-width: 0;
  flex: 1;
}

.inspector-stack {
  display: flex;
  width: 360px;
  flex: 0 0 360px;
  flex-direction: column;
  gap: 12px;
}

.ai-card-h {
  justify-content: space-between;
}

.ai-card-h > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.frame-spin {
  display: block;
}

.app-status-strip {
  display: flex;
  align-items: stretch;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border-top: 1px solid var(--ai-border);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.72), rgba(250, 248, 241, 0.92));
}

.app-status-main {
  display: flex;
  min-width: 220px;
  flex: 1;
  align-items: center;
  gap: 10px;
}

.app-status-main strong {
  max-width: 190px;
  overflow: hidden;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-status-main small {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-ink-3);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-status-facts {
  display: grid;
  min-width: 420px;
  grid-template-columns: repeat(4, minmax(88px, 1fr));
  gap: 8px;
}

.app-status-fact {
  min-width: 0;
  padding: 7px 9px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface);
}

.app-status-fact span,
.app-status-fact code {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.app-status-fact span {
  color: var(--ai-ink-4);
  font-size: 10.5px;
}

.app-status-fact code {
  margin-top: 2px;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
}

.frame-wrap {
  height: 630px;
  overflow: hidden;
  border-top: 1px solid var(--ai-border);
  background: var(--ai-surface);
}

.app-only .frame-wrap {
  height: min(78vh, 920px);
  min-height: 680px;
}

.project-frame {
  width: 100%;
  height: 100%;
  border: 0;
  background: #fff;
}

.frame-empty {
  display: grid;
  min-height: 420px;
  place-items: center;
  align-content: center;
  gap: 10px;
  color: var(--ai-ink-3);
  text-align: center;
}

.frame-empty svg {
  width: 28px;
  height: 28px;
  color: var(--ai-ink-4);
}

.frame-empty strong {
  color: var(--ai-ink-1);
  font-size: 14px;
}

.status-card,
.sdk-card,
.data-card {
  overflow: hidden;
}

.status-card :deep(.arco-spin),
.status-card :deep(.arco-spin-children) {
  display: block;
}

.status-box {
  margin: 12px;
  padding: 12px;
  flex-direction: column;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-2);
}

.status-box.info {
  border-color: transparent;
  background: var(--ai-info-soft);
}

.status-box.warn {
  border-color: transparent;
  background: var(--ai-warn-soft);
}

.status-box.ok {
  border-color: transparent;
  background: var(--ai-ok-soft);
}

.status-box.bad {
  border-color: transparent;
  background: var(--ai-bad-soft);
}

.status-box span,
.status-box small {
  color: var(--ai-ink-3);
}

.status-box span {
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
}

.status-box strong {
  color: var(--ai-ink-1);
  font-size: 22px;
  font-weight: 500;
  letter-spacing: -0.04em;
}

.status-box small {
  font-size: 12px;
  line-height: 1.55;
}

.kv-list {
  padding: 0 12px;
}

.kv {
  align-items: center;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px solid var(--ai-border);
}

.kv span {
  color: var(--ai-ink-4);
  font-size: 12px;
}

.kv code {
  max-width: 190px;
  overflow: hidden;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.action-grid {
  align-items: stretch;
  padding: 12px;
}

.action-grid > * {
  flex: 1;
  justify-content: center;
}

.asset-actions {
  display: flex;
  gap: 8px;
  padding: 12px;
  border-top: 1px solid var(--ai-border);
}

.asset-actions input[type="file"] {
  display: none;
}

.ai-btn.disabled {
  cursor: not-allowed;
  opacity: 0.56;
}

.asset-list {
  border-top: 1px solid var(--ai-border);
}

.asset-row {
  display: block;
  padding: 10px 12px;
  border-bottom: 1px solid var(--ai-border);
  color: inherit;
  text-decoration: none;
}

.asset-row span,
.asset-row small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.asset-row span {
  color: var(--ai-ink-1);
  font-size: 12.5px;
}

.asset-row small {
  margin-top: 3px;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
}

.sdk-tip {
  margin: 12px 12px 10px;
  color: var(--ai-ink-3);
  font-size: 12.5px;
  line-height: 1.65;
}

.server-sdk-panel,
.resident-model-panel {
  padding: 12px;
  border-top: 1px solid var(--ai-border);
}

.sdk-facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.sdk-fact {
  min-width: 0;
  padding: 8px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-2);
}

.sdk-fact span,
.sdk-fact strong {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sdk-fact span {
  color: var(--ai-ink-4);
  font-size: 11.5px;
}

.sdk-fact strong {
  margin-top: 2px;
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 500;
}

.server-sdk-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 12px;
}

.server-sdk-head strong,
.server-sdk-head span {
  display: block;
}

.server-sdk-head strong {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 600;
}

.server-sdk-head span {
  margin-top: 2px;
  color: var(--ai-ink-4);
  font-size: 11.5px;
  line-height: 1.5;
}

.token-create-row,
.model-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}

.sdk-input {
  min-width: 0;
  flex: 1;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-1);
  color: var(--ai-ink-1);
  font-size: 12px;
}

.sdk-input.short {
  flex: 0 0 76px;
}

.token-secret-box {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding: 8px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-warn-soft);
}

.token-secret-box span {
  color: var(--ai-ink-3);
  font-size: 11.5px;
}

.token-secret-box code {
  overflow: hidden;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.token-list {
  margin-top: 10px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  overflow: hidden;
}

.token-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px;
  border-bottom: 1px solid var(--ai-border);
}

.token-row:last-child {
  border-bottom: 0;
}

.token-row > div {
  min-width: 0;
}

.token-row strong,
.token-row span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.token-row strong {
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 500;
}

.server-sdk-samples {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.resident-status {
  margin-top: 10px;
  padding: 10px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-2);
}

.resident-status.ok {
  border-color: transparent;
  background: var(--ai-ok-soft);
}

.resident-status.warn {
  border-color: transparent;
  background: var(--ai-warn-soft);
}

.resident-status.bad {
  border-color: transparent;
  background: var(--ai-bad-soft);
}

.resident-status strong,
.resident-status small {
  display: block;
  margin-top: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.resident-status strong {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 600;
}

.resident-status small {
  color: var(--ai-ink-3);
  font-size: 11.5px;
}

.resident-url-list {
  display: grid;
  gap: 6px;
  margin-top: 10px;
}

.resident-url-list div {
  min-width: 0;
  padding: 7px 8px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-2);
}

.resident-url-list span,
.resident-url-list code {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.resident-url-list span {
  color: var(--ai-ink-4);
  font-size: 11px;
}

.resident-url-list code {
  margin-top: 2px;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 11px;
}

.resident-model-list {
  display: grid;
  gap: 6px;
  margin-top: 10px;
}

.resident-model-row {
  min-width: 0;
  padding: 6px 7px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-2);
}

.resident-model-row code,
.resident-model-row span,
.resident-model-list > span {
  display: block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.resident-model-row code {
  color: var(--ai-ink-2);
  font-family: var(--ai-font-mono);
  font-size: 11px;
}

.resident-model-row span,
.resident-model-list > span {
  margin-top: 2px;
  color: var(--ai-ink-4);
  font-size: 11px;
}

.code-block,
.json-block {
  margin: 0;
  overflow: auto;
  padding: 12px;
  border-top: 1px solid var(--ai-border);
  background: #171611;
  color: #f5f1e8;
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  line-height: 1.65;
}

.code-block {
  max-height: 250px;
}

.code-block.compact {
  max-height: 190px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
}

.result-grid {
  align-items: stretch;
}

.data-card {
  min-width: 0;
  flex: 1;
}

.json-block {
  min-height: 280px;
  max-height: 420px;
}

.gateway-trace {
  overflow: hidden;
}

.trace-list {
  display: flex;
  flex-direction: column;
}

.trace-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-top: 1px solid var(--ai-border);
}

.trace-pagebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px 0;
  border-top: 1px dashed var(--ai-border);
}

.trace-main {
  min-width: 0;
  flex: 1;
}

.trace-main strong,
.trace-main small {
  display: block;
}

.trace-main strong {
  overflow: hidden;
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trace-empty {
  padding: 18px 12px;
  border-top: 1px solid var(--ai-border);
  color: var(--ai-ink-4);
  font-size: 12.5px;
}

.ai-btn:disabled {
  cursor: not-allowed;
  opacity: 0.56;
}

@media (max-width: 1180px) {
  .project-detail-page {
    flex-direction: column;
  }

  .detail-sidebar {
    position: static;
    width: 100%;
    height: auto;
    flex-basis: auto;
    border-right: 0;
    border-bottom: 1px solid var(--ai-border);
  }

  .runtime-layout,
  .result-grid {
    flex-direction: column;
  }

  .inspector-stack {
    width: 100%;
    flex-basis: auto;
  }
}

@media (max-width: 720px) {
  .detail-pagehead,
  .page-actions,
  .action-grid,
  .app-status-strip {
    flex-direction: column;
    align-items: stretch;
  }

  .app-status-main {
    align-items: flex-start;
    flex-direction: column;
  }

  .app-status-facts {
    min-width: 0;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .frame-wrap {
    height: 420px;
  }

  .app-only .frame-wrap {
    height: 68vh;
    min-height: 420px;
  }
}
</style>
