<template>
  <div class="admin-mcp-servers-page ai-main">
    <div class="ai-pagehead admin-mcp-pagehead">
      <div>
        <div class="ai-crumbs">管理后台 · 数据与服务</div>
        <h1 class="ai-title">MCP Servers</h1>
        <p class="ai-sub">代码内置自动注入 · 外部配置所有 Skill 共享</p>
      </div>
      <div class="admin-mcp-head-actions">
        <button class="ai-btn" type="button" @click="exportMcpServers"><SfShellIcon name="download" /> 导出</button>
        <button class="ai-btn" type="button" @click="triggerImport"><SfShellIcon name="upload" /> 导入</button>
        <button class="ai-btn primary" type="button" @click="openMcpAdd"><SfShellIcon name="plus" /> 添加</button>
      </div>
    </div>
    <input ref="importFileRef" class="hidden-file-input" type="file" accept=".json" @change="handleImportFile" />

    <section class="ai-pagebody admin-mcp-body">
    <div class="mcp-kpis">
      <article class="mcp-kpi ai-card">
        <span class="mcp-kpi-label">总 Server</span>
        <strong>{{ mcpServers.length }}</strong>
        <span>内置 {{ builtinCount }} / 后台 {{ configuredCount }}</span>
      </article>
      <article class="mcp-kpi ai-card">
        <span class="mcp-kpi-label">启用中</span>
        <strong>{{ enabledCount }}</strong>
        <span>{{ disabledCount }} 个已停用</span>
      </article>
      <article class="mcp-kpi ai-card">
        <span class="mcp-kpi-label">ToolMeta</span>
        <strong>{{ toolMetaTotal }}</strong>
        <span>已声明平台 / scope 元数据</span>
      </article>
      <article class="mcp-kpi ai-card" :class="{ danger: failingCount > 0 }">
        <span class="mcp-kpi-label">连接测试</span>
        <strong>{{ testedCount }}</strong>
        <span>{{ failingCount ? `${failingCount} 个失败` : '暂无失败' }}</span>
      </article>
    </div>

    <div class="mcp-section">

      <div class="mcp-status-note ai-card">
        <div class="note-title">状态说明</div>
        <div class="note-text">“代码内置”随项目启动自动注册，不允许在后台删除；“后台配置”用于补充外部 MCP，保存后立即参与 Skill 执行。</div>
      </div>

      <div v-if="mcpServers.length === 0" class="mcp-empty">
        <SfShellIcon name="grid" class="mcp-empty-icon" />
        <p>暂无 MCP server。可添加本地 stdio 或远程 http/sse server。</p>
        <button class="ai-btn primary" type="button" @click="openMcpAdd">添加 MCP server</button>
      </div>

      <template v-else>
        <div class="mcp-grid">
          <div
            v-for="srv in mcpServers"
            :key="srv.name"
            class="mcp-card ai-card"
            :class="{
              'mcp-card-enabled': srv.config.enabled !== false,
              'mcp-card-disabled': srv.config.enabled === false,
              'mcp-card-expanded': expandedServer === srv.name,
            }"
            @click="expandedServer = expandedServer === srv.name ? null : srv.name"
          >
            <div class="mcp-card-title-row">
              <div class="mcp-title-left">
                <div class="mcp-dot" :class="srv.config.enabled === false ? 'mcp-dot-off' : 'mcp-dot-on'" />
                <div class="mcp-name">{{ srv.name }}</div>
              </div>
              <SfShellIcon v-if="expandedServer === srv.name" name="chev" class="mcp-chevron" />
              <SfShellIcon v-else name="chevr" class="mcp-chevron" />
            </div>

            <div class="mcp-tags">
              <span class="ai-pill dot" :class="enabledTone(srv)">{{ enabledLabel(srv) }}</span>
              <span class="ai-pill" :class="serverKindTone(srv)">{{ serverKindLabel(srv) }}</span>
              <span class="ai-pill accent">{{ srv.config.type || 'stdio' }}</span>
              <span class="ai-pill" :class="toolMetaCount(srv) ? 'ok' : 'warn'">
                ToolMeta {{ toolMetaCount(srv) || '未注册' }}
              </span>
            </div>

            <div class="mcp-summary">{{ serverSummary(srv) }}</div>
            <div class="mcp-command">{{ formatMcpDetail(srv.config) || '—' }}</div>

            <div class="mcp-meta">
              <span>工具：{{ toolsCountLabel(srv) }}</span>
              <span>本周调用：{{ srv.config.weekly_calls ?? 0 }}</span>
              <span :class="testStatusClass(srv)">{{ testStatusLabel(srv) }}</span>
            </div>
          </div>
        </div>

        <div v-if="selectedServer" class="mcp-detail-panel ai-card" @click.stop>
          <div class="mcp-detail-head">
            <div>
              <div class="mcp-detail-title">{{ selectedServer.name }}</div>
              <div class="mcp-detail-subtitle">Server 详情与操作</div>
            </div>
            <div class="mcp-detail-actions">
              <button class="ai-btn sm primary" type="button" @click.stop="testMcpServer(selectedServer.name)">测试连接</button>
              <button
                class="ai-btn sm"
                type="button"
                :disabled="!!selectedServer.config.builtin"
                @click.stop="openMcpEdit(selectedServer)"
              >
                编辑
              </button>
              <a-switch
                :model-value="selectedServer.config.enabled !== false"
                size="small"
                :disabled="!!selectedServer.config.builtin"
                @change="toggleMcpServer(selectedServer.name)"
              />
              <a-popconfirm v-if="!selectedServer.config.builtin" content="确认删除？" @ok="deleteMcpServer(selectedServer.name)">
                <button class="ai-btn sm danger" type="button">删除</button>
              </a-popconfirm>
            </div>
          </div>

          <div class="mcp-detail-grid">
            <div class="mcp-detail-item">
              <div class="mcp-detail-label">注册方式</div>
              <div class="mcp-detail-value">
                <span class="ai-pill" :class="serverKindTone(selectedServer)">{{ serverKindLabel(selectedServer) }}</span>
              </div>
            </div>
            <div class="mcp-detail-item">
              <div class="mcp-detail-label">Transport</div>
              <div class="mcp-detail-value mcp-mono">{{ selectedServer.config.type || 'stdio' }}</div>
            </div>
            <div class="mcp-detail-item">
              <div class="mcp-detail-label">启用状态</div>
              <div class="mcp-detail-value">
                <span class="ai-pill dot" :class="enabledTone(selectedServer)">{{ enabledLabel(selectedServer) }}</span>
              </div>
            </div>
            <div class="mcp-detail-item">
              <div class="mcp-detail-label">最近测试</div>
              <div class="mcp-detail-value" :class="testStatusClass(selectedServer)">{{ testStatusLabel(selectedServer) }}</div>
            </div>
            <div class="mcp-detail-item mcp-detail-wide">
              <div class="mcp-detail-label">用途说明</div>
              <div class="mcp-detail-value mcp-muted">{{ selectedServer.config.description || serverSummary(selectedServer) }}</div>
            </div>
            <div class="mcp-detail-item mcp-detail-wide">
              <div class="mcp-detail-label">启动参数 / URL</div>
              <pre class="mcp-code-block">{{ formatMcpDetail(selectedServer.config) || '—' }}</pre>
            </div>
            <div class="mcp-detail-item">
              <div class="mcp-detail-label">环境变量</div>
              <div class="mcp-detail-value mcp-mono">{{ objectCount(selectedServer.config.env) }} 项</div>
            </div>
            <div class="mcp-detail-item">
              <div class="mcp-detail-label">Headers</div>
              <div class="mcp-detail-value mcp-mono">{{ objectCount(selectedServer.config.headers) }} 项</div>
            </div>
            <div class="mcp-detail-item mcp-detail-wide">
              <div class="mcp-detail-label">工具列表</div>
              <div class="mcp-detail-value mcp-muted">{{ toolsPreview(selectedServer) }}</div>
            </div>
            <div class="mcp-detail-item mcp-detail-wide">
              <div class="mcp-detail-label">ToolMeta 映射</div>
              <div class="mcp-toolmeta-list">
                <div v-if="!toolMetaRows(selectedServer).length" class="mcp-muted">
                  未注册 ToolMeta。采集类工具应声明 platform / data_scope / endpoint_family / warning_group。
                </div>
                <div v-for="row in toolMetaRows(selectedServer)" :key="row.name" class="mcp-toolmeta-row">
                  <span class="mcp-toolmeta-name">{{ row.name }}</span>
                  <span>{{ row.platform || '-' }}</span>
                  <span>{{ row.data_scope || '-' }}</span>
                  <span>{{ row.endpoint_family || '-' }}</span>
                  <span>{{ row.warning_group || '-' }}</span>
                </div>
              </div>
            </div>
            <div v-if="selectedServer._testResult?.error" class="mcp-detail-item mcp-detail-wide">
              <div class="mcp-detail-label">测试错误</div>
              <div class="mcp-detail-value mcp-error">{{ selectedServer._testResult.error }}</div>
            </div>
          </div>
        </div>
      </template>
    </div>
    </section>

    <a-modal v-model:visible="showMcpModal" :title="mcpEditMode ? '编辑 MCP server' : '添加 MCP server'" @ok="saveMcpServer" :width="'min(90vw, 600px)'">
      <a-form :model="mcpForm" layout="vertical" size="small">
        <a-form-item label="Server 名称" required>
          <a-input v-model="mcpForm.name" placeholder="filesystem" :disabled="mcpEditMode" />
          <span class="form-hint-inline">小写字母+数字+下划线/连字符, ≤50 字符</span>
        </a-form-item>
        <a-form-item label="Transport">
          <a-radio-group v-model="mcpForm.type" type="button" size="small">
            <a-radio value="stdio">stdio (本地子进程)</a-radio>
            <a-radio value="http">http</a-radio>
            <a-radio value="sse">sse</a-radio>
          </a-radio-group>
        </a-form-item>

        <template v-if="mcpForm.type === 'stdio'">
          <a-form-item label="Command" required>
            <a-input v-model="mcpForm.command" placeholder="npx" />
          </a-form-item>
          <a-form-item label="Args (每行一个)">
            <a-textarea
              :model-value="(mcpForm.args || []).join('\n')"
              :auto-size="{ minRows: 2, maxRows: 6 }"
              placeholder="-y&#10;@modelcontextprotocol/server-filesystem&#10;/tmp"
              class="mono-textarea"
              @update:model-value="val => mcpForm.args = val.split('\n').filter(Boolean)"
            />
          </a-form-item>
          <a-form-item label="环境变量 (KEY=VALUE 每行一个)">
            <a-textarea
              :model-value="formatEnvAsLines(mcpForm.env)"
              :auto-size="{ minRows: 2, maxRows: 6 }"
              placeholder="GITHUB_TOKEN=ghp_xxx&#10;NODE_OPTIONS=--max-old-space-size=4096"
              class="mono-textarea"
              @update:model-value="val => mcpForm.env = parseEnvLines(val)"
            />
          </a-form-item>
        </template>

        <template v-else>
          <a-form-item label="URL" required>
            <a-input v-model="mcpForm.url" :placeholder="`https://example.com/mcp`" />
          </a-form-item>
          <a-form-item label="Headers (HEADER=VALUE 每行一个)">
            <a-textarea
              :model-value="formatEnvAsLines(mcpForm.headers)"
              :auto-size="{ minRows: 2, maxRows: 6 }"
              placeholder="Authorization=Bearer xxx&#10;X-Api-Key=secret"
              class="mono-textarea"
              @update:model-value="val => mcpForm.headers = parseEnvLines(val)"
            />
          </a-form-item>
        </template>

        <a-form-item label="用途说明">
          <a-input v-model="mcpForm.description" placeholder="例如：浏览器自动化 — 抓包发现页面API、提取数据" />
        </a-form-item>
        <a-form-item>
          <a-checkbox v-model="mcpForm.enabled">启用此 server</a-checkbox>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'
import { mcpApi as rawMcpApi } from '@/api'
import { bjtDateString } from '@/utils/format'

defineOptions({ name: 'AdminMcpServers' })

interface McpServerRow {
  name: string
  config: Record<string, unknown> & {
    type?: string
    enabled?: boolean
    description?: string
    builtin?: boolean
    managed?: string
    command?: string
    args?: string[]
    env?: Record<string, string>
    url?: string
    headers?: Record<string, string>
    tools_meta?: Record<string, unknown>
    tool_meta?: Record<string, unknown>
    weekly_calls?: number
    warning_count?: number
  }
  _testResult: { ok: boolean; error?: string; duration_ms?: number; tools?: string[]; tool_meta?: Record<string, unknown> } | null
}

const mcpApi: any = rawMcpApi
const mcpServers = ref<McpServerRow[]>([])
const expandedServer = ref<string | null>(null)
const showMcpModal = ref(false)
const mcpEditMode = ref(false)

const selectedServer = computed(() => (
  mcpServers.value.find((srv) => srv.name === expandedServer.value) || null
))
const builtinCount = computed(() => mcpServers.value.filter((srv) => !!srv.config.builtin).length)
const configuredCount = computed(() => mcpServers.value.length - builtinCount.value)
const enabledCount = computed(() => mcpServers.value.filter((srv) => srv.config.enabled !== false).length)
const disabledCount = computed(() => mcpServers.value.length - enabledCount.value)
const testedCount = computed(() => mcpServers.value.filter((srv) => !!srv._testResult && srv._testResult.error !== '正在测试...').length)
const failingCount = computed(() => mcpServers.value.filter((srv) => srv._testResult && !srv._testResult.ok && srv._testResult.error !== '正在测试...').length)
const toolMetaTotal = computed(() => mcpServers.value.reduce((sum, srv) => sum + toolMetaCount(srv), 0))

const mcpForm = reactive({
  name: '',
  type: 'stdio',
  enabled: true,
  command: '',
  args: [] as string[],
  env: {} as Record<string, string>,
  url: '',
  headers: {} as Record<string, string>,
  description: '',
})

function resetMcpForm() {
  mcpForm.name = ''
  mcpForm.type = 'stdio'
  mcpForm.enabled = true
  mcpForm.command = ''
  mcpForm.args = []
  mcpForm.env = {}
  mcpForm.url = ''
  mcpForm.headers = {}
  mcpForm.description = ''
}

function formatEnvAsLines(obj: Record<string, string>) {
  if (!obj || typeof obj !== 'object') return ''
  return Object.entries(obj).map(([k, v]) => `${k}=${v}`).join('\n')
}

function parseEnvLines(text: string) {
  const out: Record<string, string> = {}
  for (const line of (text || '').split('\n')) {
    const trimmed = line.trim()
    if (!trimmed) continue
    const eq = trimmed.indexOf('=')
    if (eq < 1) continue
    out[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1)
  }
  return out
}

function formatMcpDetail(cfg: McpServerRow['config']) {
  if (cfg.type === 'stdio') {
    return `${cfg.command || ''} ${(cfg.args || []).join(' ')}`.slice(0, 80)
  }
  return cfg.url || ''
}

function enabledLabel(srv: McpServerRow) {
  return srv.config.enabled === false ? '已停用' : '已启用'
}

function enabledTone(srv: McpServerRow) {
  return srv.config.enabled === false ? '' : 'ok'
}

function serverKindLabel(srv: McpServerRow) {
  return srv.config.builtin ? '代码内置' : '后台配置'
}

function serverKindTone(srv: McpServerRow) {
  return srv.config.builtin ? 'accent' : 'warn'
}

function serverSummary(srv: McpServerRow) {
  if (srv.config.description) return srv.config.description
  if (srv.name === 'tmall') return '天猫、生意参谋、阿里妈妈采集工具'
  if (srv.name === 'yuyidata') return '语忆开放平台上传、回查和 SSO 工具'
  if (srv.name === 'browser') return '浏览器抓包、页面提取、请求复用'
  if (srv.name === 'skillforge_internal') return 'Skill 文件、脚本、契约校验工具'
  return '外部 MCP server'
}

function toolsCountLabel(srv: McpServerRow) {
  if (!srv._testResult?.ok) return '未测试'
  return `${(srv._testResult.tools || []).length} 个`
}

function testStatusLabel(srv: McpServerRow) {
  if (!srv._testResult) return '未测试'
  if (srv._testResult.error === '正在测试...') return '测试中'
  if (srv._testResult.ok) return `通过 ${srv._testResult.duration_ms ?? 0}ms`
  return '失败'
}

function testStatusClass(srv: McpServerRow) {
  if (!srv._testResult) return 'mcp-test-muted'
  if (srv._testResult.error === '正在测试...') return 'mcp-test-running'
  return srv._testResult.ok ? 'mcp-test-ok' : 'mcp-test-error'
}

function toolsPreview(srv: McpServerRow) {
  const tools = srv._testResult?.tools || []
  if (!srv._testResult) return '尚未测试。点击“测试连接”后显示该 server 暴露的工具。'
  if (!srv._testResult.ok) return '测试失败，暂无工具列表。'
  if (!tools.length) return '连接成功，但没有返回工具。'
  return tools.join('、')
}

function toolMetaObject(srv: McpServerRow | null) {
  if (!srv) return {}
  const fromTest = srv._testResult?.tool_meta
  const fromConfig = srv.config.tool_meta || srv.config.tools_meta
  const value = fromTest || fromConfig
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function toolMetaCount(srv: McpServerRow) {
  return Object.keys(toolMetaObject(srv)).length
}

function toolMetaRows(srv: McpServerRow | null) {
  return Object.entries(toolMetaObject(srv)).map(([name, value]) => {
    const meta = value && typeof value === 'object' && !Array.isArray(value)
      ? value as Record<string, unknown>
      : {}
    return {
      name,
      platform: String(meta.platform || ''),
      data_scope: String(meta.data_scope || ''),
      endpoint_family: String(meta.endpoint_family || ''),
      warning_group: String(meta.warning_group || ''),
    }
  })
}

function objectCount(obj: unknown) {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return 0
  return Object.keys(obj as Record<string, unknown>).length
}

async function loadMcpServers() {
  try {
    const res = await mcpApi.listServers()
    const list: McpServerRow[] = []
    for (const [name, config] of Object.entries(res.servers || {})) {
      list.push({ name, config: config as McpServerRow['config'], _testResult: null })
    }
    list.sort((a, b) => {
      const builtinDiff = Number(Boolean(b.config.builtin)) - Number(Boolean(a.config.builtin))
      if (builtinDiff !== 0) return builtinDiff
      return a.name.localeCompare(b.name)
    })
    mcpServers.value = list
    if (expandedServer.value && !list.some((srv) => srv.name === expandedServer.value)) {
      expandedServer.value = null
    }
  } catch (e) {
    console.warn('[mcp] 加载失败', e)
  }
}

function openMcpAdd() {
  resetMcpForm()
  mcpEditMode.value = false
  showMcpModal.value = true
}

function openMcpEdit(srv: McpServerRow) {
  resetMcpForm()
  mcpEditMode.value = true
  mcpForm.name = srv.name
  mcpForm.type = String(srv.config.type || 'stdio')
  mcpForm.enabled = srv.config.enabled !== false
  mcpForm.description = srv.config.description || ''
  if (mcpForm.type === 'stdio') {
    mcpForm.command = srv.config.command || ''
    mcpForm.args = srv.config.args || []
    mcpForm.env = srv.config.env || {}
  } else {
    mcpForm.url = srv.config.url || ''
    mcpForm.headers = srv.config.headers || {}
  }
  showMcpModal.value = true
}

async function saveMcpServer() {
  if (!mcpForm.name) {
    Message.warning('请填 server 名称')
    return
  }
  const body: Record<string, unknown> = {
    enabled: mcpForm.enabled,
    type: mcpForm.type,
    description: mcpForm.description || '',
  }
  if (mcpForm.type === 'stdio') {
    body.command = mcpForm.command
    body.args = mcpForm.args
    body.env = mcpForm.env
  } else {
    body.url = mcpForm.url
    body.headers = mcpForm.headers
  }
  try {
    await mcpApi.upsertServer(mcpForm.name, body)
    Message.success('已保存')
    showMcpModal.value = false
    await loadMcpServers()
  } catch (e) {
    Message.error(String((e as { _message?: string })?._message || '保存失败'))
  }
}

async function deleteMcpServer(name: string) {
  try {
    await mcpApi.deleteServer(name)
    Message.success('已删除')
    await loadMcpServers()
  } catch (e) {
    Message.error(String((e as { _message?: string })?._message || '删除失败'))
  }
}

async function toggleMcpServer(name: string) {
  try {
    await mcpApi.toggleServer(name)
    await loadMcpServers()
  } catch (e) {
    Message.error(String((e as { _message?: string })?._message || '切换失败'))
  }
}

async function testMcpServer(name: string) {
  const srv = mcpServers.value.find(s => s.name === name)
  if (!srv) return
  srv._testResult = { ok: false, error: '正在测试...', duration_ms: 0, tools: [] }
  try {
    const res = await mcpApi.testServer(name)
    srv._testResult = res
    if (res.ok) Message.success(`${name}: 暴露 ${(res.tools || []).length} 个工具`)
    else Message.error(`${name}: ${res.error}`)
  } catch (e) {
    srv._testResult = { ok: false, error: String((e as { _message?: string })?._message || e), duration_ms: 0, tools: [] }
  }
}

const importFileRef = ref<HTMLInputElement>()

function triggerImport() {
  importFileRef.value?.click()
}

async function handleImportFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    const text = await file.text()
    const json = JSON.parse(text)
    if (json.export_type !== 'mcp_servers') {
      Message.error('文件格式不匹配，需要 mcp_servers 导出文件')
      return
    }
    const res = await mcpApi.importServers(json)
    Message.success(res.message)
    await loadMcpServers()
  } catch (e) {
    Message.error(String((e as { _message?: string })?._message || '导入失败'))
  } finally {
    if (importFileRef.value) importFileRef.value.value = ''
  }
}

async function exportMcpServers() {
  try {
    const res = await mcpApi.exportServers()
    const blob = new Blob([JSON.stringify(res, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `mcp-servers-${bjtDateString(Date.now())}.json`
    a.click()
    URL.revokeObjectURL(url)
    Message.success('已导出')
  } catch (e) {
    Message.error(String((e as { _message?: string })?._message || '导出失败'))
  }
}

onMounted(loadMcpServers)
</script>

<style scoped>
.admin-mcp-servers-page {
  min-height: calc(100vh - 52px);
  background: var(--ai-bg);
  color: var(--ai-ink-1);
  font-family: var(--ai-font-sans);
}

.admin-mcp-pagehead {
  flex: 0 0 auto;
}

.admin-mcp-head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.admin-mcp-head-actions svg,
.mcp-empty-icon,
.mcp-chevron {
  width: 14px;
  height: 14px;
}

.admin-mcp-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.hidden-file-input {
  display: none;
}

.ai-btn.danger {
  color: var(--ai-bad);
  border-color: var(--ai-bad-soft);
  background: var(--ai-bad-soft);
}

.ai-btn.danger:hover {
  border-color: var(--ai-bad);
}

.mcp-kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.mcp-kpi {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
}

.mcp-kpi-label {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.mcp-kpi strong {
  font-size: 26px;
  line-height: 1.1;
  font-weight: 650;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
}

.mcp-kpi span:last-child {
  font-size: 12px;
  color: var(--ai-ink-4);
}

.mcp-kpi.danger {
  border-left: 2px solid var(--ai-bad);
}

@media (max-width: 1180px) {
  .mcp-kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .mcp-kpis,
  .mcp-grid {
    grid-template-columns: 1fr;
  }

  .mcp-status-note {
    align-items: flex-start;
    flex-direction: column;
  }
}

/* ────────── modal flatten ────────── */
.admin-mcp-servers-page :deep(.arco-modal) {
  box-shadow: var(--ai-shadow-2);
}
.admin-mcp-servers-page :deep(.arco-modal-header) {
  border-bottom: 1px solid var(--ai-border);
  padding: 12px 16px;
}
.admin-mcp-servers-page :deep(.arco-modal-title) {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.admin-mcp-servers-page :deep(.arco-modal-body) {
  padding: 16px;
  font-size: 13px;
  color: var(--ai-ink-2);
}
.admin-mcp-servers-page :deep(.arco-modal-footer) {
  border-top: 1px solid var(--ai-border);
  padding: 10px 16px;
}

/* ────────── 各 section / 状态说明 ────────── */
.mcp-section {
  margin-bottom: 16px;
}
.mcp-empty {
  text-align: center;
  padding: 32px;
  background: var(--ai-surface);
  border: 1px dashed var(--ai-border-2);
  border-radius: var(--ai-radius);
}
.mcp-empty p {
  margin: 8px 0 12px;
  color: var(--ai-ink-3);
  font-size: 12px;
}
.mcp-status-note {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 10px 12px;
  margin-bottom: 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
}
.note-title {
  flex: 0 0 auto;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ai-ink-4);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.note-text {
  min-width: 0;
  font-size: 12.5px;
  color: var(--ai-ink-2);
}

/* ────────── MCP 卡片网格 ────────── */
.mcp-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
  align-items: start;
}
.mcp-card {
  min-height: 176px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  padding: 14px 16px;
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
}
.mcp-card:hover {
  border-color: var(--ai-border-2);
  background: var(--ai-surface-2);
}
/* 启用/停用：左侧细色条 */
.mcp-card-enabled {
  border-left: 2px solid var(--ai-ok);
}
.mcp-card-disabled {
  border-left: 2px solid var(--ai-ink-5);
}
/* 选中：AgentRow-style 2px ink-1 左色条，覆盖启用 / 停用 */
.mcp-card-expanded,
.mcp-card-expanded.mcp-card-enabled,
.mcp-card-expanded.mcp-card-disabled {
  border-color: var(--ai-border-2);
  border-left: 2px solid var(--ai-ink-1);
  background: var(--ai-surface);
}

.mcp-card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.mcp-title-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.mcp-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.mcp-dot-on {
  background: var(--ai-ok);
}
.mcp-dot-off {
  background: var(--ai-ink-5);
}
.mcp-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.005em;
}
.mcp-chevron {
  color: var(--ai-ink-4);
  flex-shrink: 0;
}
.mcp-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.mcp-summary {
  color: var(--ai-ink-2);
  font-size: 12.5px;
  line-height: 1.5;
  min-height: 36px;
}
.mcp-command {
  color: var(--ai-ink-3);
  font-size: 11.5px;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.mcp-meta {
  margin-top: auto;
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: var(--ai-ink-4);
  font-size: 11px;
}

/* ────────── 详情面板 ────────── */
.mcp-detail-panel {
  margin-top: 12px;
  padding: 14px 16px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  cursor: default;
}
.mcp-detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--ai-border);
  margin-bottom: 12px;
}
.mcp-detail-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.005em;
}
.mcp-detail-subtitle {
  margin-top: 2px;
  font-size: 12px;
  color: var(--ai-ink-4);
}
.mcp-detail-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px 16px;
}
.mcp-detail-item {
  min-width: 0;
}
.mcp-detail-label {
  margin-bottom: 4px;
  color: var(--ai-ink-4);
  font-size: 10.5px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.mcp-detail-value {
  color: var(--ai-ink-1);
  font-size: 12.5px;
  word-break: break-word;
}
.mcp-detail-wide {
  grid-column: span 2;
}
.mcp-toolmeta-list {
  display: grid;
  gap: 6px;
}
.mcp-toolmeta-row {
  display: grid;
  grid-template-columns: 1.2fr 0.8fr 1.2fr 1.1fr 1.2fr;
  gap: 8px;
  padding: 6px 10px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 11px;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.mcp-toolmeta-name {
  color: var(--ai-ink-1);
  font-weight: 600;
}
.mcp-muted {
  color: var(--ai-ink-3);
  font-size: 11.5px;
}
.mcp-mono {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 12px;
}

/* code block —— ai-surface-2 / ai-border / mono 11.5px */
.mcp-code-block {
  margin: 0;
  padding: 10px 12px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 11.5px;
  color: var(--ai-ink-1);
  white-space: pre-wrap;
  word-break: break-all;
}

.mcp-test-muted {
  color: var(--ai-ink-4);
}
.mcp-test-running {
  color: var(--ai-info);
}
.mcp-test-ok {
  color: var(--ai-ok);
}
.mcp-test-error,
.mcp-error {
  color: var(--ai-bad);
}

.form-hint-inline {
  margin-left: 8px;
  color: var(--ai-ink-4);
  font-size: 11.5px;
}
.mono-textarea :deep(.arco-textarea) {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

@media (max-width: 720px) {
  .mcp-status-note,
  .mcp-detail-head {
    align-items: stretch;
    flex-direction: column;
  }
  .mcp-grid,
  .mcp-detail-grid {
    grid-template-columns: 1fr;
  }
  .mcp-detail-wide {
    grid-column: span 1;
  }
}
</style>
