<template>
  <div class="workspace-shell">
    <!-- 顶栏：紧凑 detail toolbar -->
    <div class="ws-toolbar">
      <div class="ws-title-block">
        <a-button size="small" @click="$router.push('/aiclaw')">
          <template #icon><icon-left /></template>返回
        </a-button>
        <div class="ws-title">{{ instance?.name || instanceId }}</div>
        <a-tag size="small" :color="instance?.bridge_online ? 'green' : 'gray'">
          <span class="status-dot" :class="{ online: instance?.bridge_online }"></span>
          {{ instance?.bridge_online ? '在线' : '离线' }}
        </a-tag>
        <a-tag v-if="capabilities.gateway_kind" size="small" color="arcoblue">
          {{ capabilities.gateway_kind }}
        </a-tag>
      </div>
      <div class="ws-actions">
        <a-button size="small" @click="showSyncModal = true" :disabled="!instance?.bridge_online">
          <template #icon><icon-upload /></template>推送 Skill
        </a-button>
        <a-button size="small" @click="loadData">
          <template #icon><icon-refresh /></template>
        </a-button>
      </div>
    </div>

    <!-- 主体：左侧 agents + skills，右侧 chat -->
    <div class="ws-body">
      <!-- ═══ 左侧 sidebar ═══ -->
      <aside class="ws-sidebar">
        <!-- Agents 区 -->
        <section class="ws-section">
          <div class="ws-section-head">
            <span>Agents</span>
            <span class="ws-count">{{ agents.length }}</span>
          </div>
          <a-spin :loading="loading" style="width: 100%">
            <a-empty v-if="agents.length === 0" description="暂无 agent" />
            <div v-else class="agent-list">
              <button
                v-for="agent in agents"
                :key="agent.id || agent.name"
                class="agent-item"
                :class="{ active: selectedAgentId === (agent.id || agent.name) }"
                @click="selectAgent(agent)"
              >
                <div class="agent-emoji">{{ agent.identity?.emoji || '🤖' }}</div>
                <div class="agent-info">
                  <div class="agent-title">{{ agent.identity?.name || agent.displayName || agent.name || agent.id }}</div>
                  <div class="agent-meta">{{ agent.id || agent.name }}</div>
                </div>
              </button>
            </div>
          </a-spin>
        </section>

        <!-- Skills 区 -->
        <section class="ws-section ws-section-skills">
          <div class="ws-section-head">
            <span>已加载 Skills</span>
            <span class="ws-count">{{ filteredSkills.length }}</span>
            <a-tag v-if="capabilities.skills_dir" size="small" color="gray" class="ws-dir-tag">
              {{ shortDir(capabilities.skills_dir) }}
            </a-tag>
          </div>
          <a-input-search
            v-if="skills.length > 8"
            v-model="skillFilter"
            placeholder="搜索 skill"
            size="small"
            allow-clear
            class="skill-filter"
          />
          <div class="skill-list-scroll">
            <a-empty v-if="skills.length === 0" description="暂无 skill" />
            <div v-else class="skill-grid">
              <a-tooltip
                v-for="skill in filteredSkills"
                :key="skill.skillKey || skill.name || skill.id"
                :content="skillTooltip(skill)"
                mini
                position="right"
              >
                <button
                  class="skill-pill"
                  :class="{ linked: skillExistsInPlatform(skill) }"
                  @click="onSkillClick(skill)"
                >
                  <span class="skill-dot"></span>
                  <span class="skill-name">{{ skill.displayName || skill.name || skill.id }}</span>
                  <span
                    v-if="skillGitShort(skill)"
                    class="skill-git"
                    :class="`skill-git--${skillGitState(skill)}`"
                  >
                    {{ skillGitShort(skill) }}
                  </span>
                </button>
              </a-tooltip>
            </div>
          </div>
        </section>
      </aside>

      <!-- ═══ 右侧 chat ═══ -->
      <main class="ws-chat">
        <div class="chat-head">
          <div class="chat-title">{{ selectedAgentLabel }}</div>
          <div v-if="selectedAgentId" class="chat-subtitle">{{ selectedAgentId }}</div>
        </div>
        <div class="chat-log" ref="chatLogRef">
          <a-empty v-if="messages.length === 0" description="选择左侧 agent 后开始对话" style="margin-top: 80px" />
          <StreamingMessage
            v-for="(msg, index) in messages"
            :key="`${index}-${msg.role}`"
            :role="msg.role"
            :content="msg.content"
            :streaming="msg.streaming"
            :meta="chatMessageMeta(msg)"
          />
        </div>
        <div class="chat-input">
          <a-textarea
            v-model="draft"
            :auto-size="{ minRows: 2, maxRows: 6 }"
            :placeholder="selectedAgentId ? `跟 ${selectedAgentLabel} 对话…` : '请先在左侧选择一个 agent'"
            :disabled="!selectedAgentId"
            @keydown.enter.exact.prevent="sendMessage"
          />
          <div v-if="attachments.length" class="attachment-list">
            <a-tag
              v-for="(item, index) in attachments"
              :key="`${item.name}-${index}`"
              closable
              @close="removeAttachment(index)"
            >
              <icon-image /> {{ item.name }}
            </a-tag>
          </div>
          <div class="chat-actions">
            <input ref="fileInputRef" type="file" multiple accept="image/*" class="hidden-file-input" @change="handleFileChange">
            <a-button size="small" @click="pickFiles" :disabled="!selectedAgentId">
              <template #icon><icon-paste /></template>附件
            </a-button>
            <a-button size="small" @click="abortRun" :disabled="!activeRunId">
              <template #icon><icon-pause /></template>中止
            </a-button>
            <a-button type="primary" size="small" :loading="sending" :disabled="!selectedAgentId || !draft.trim()" @click="sendMessage">
              发送 <icon-send />
            </a-button>
          </div>
        </div>
      </main>
    </div>

    <!-- 推送 Skill 弹窗（提到 template 根） -->
    <a-modal v-model:visible="showSyncModal" title="推送 Skill Studio 的 Skill 到设备" @ok="doSyncSkill" :ok-loading="syncing" :width="500">
      <a-alert type="info" style="margin-bottom: 12px">
        把 Skill Studio 的 Skill 写到设备
        <code style="font-size: 11px">{{ capabilities.skills_dir || '~/.aiclaw/skills' }}/</code>，
        AIClaw 自动加载。
      </a-alert>
      <a-form layout="vertical">
        <a-form-item label="选择 Skill">
          <a-select v-model="syncForm.skillId" placeholder="搜索 Skill Studio 中的 Skill" allow-search :loading="loadingPlatformSkills">
            <a-option v-for="s in platformSkills" :key="s.id" :value="s.id">
              {{ s.id }}<template v-if="s.name && s.name !== s.id"> — {{ s.name }}</template>
            </a-option>
          </a-select>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, h, watch } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'
import {
  IconLeft, IconUpload, IconRefresh, IconImage, IconPaste, IconPause, IconSend,
} from '@arco-design/web-vue/es/icon'
import { useRoute, useRouter } from 'vue-router'
import { aiclawApi as rawAiclawApi, skillApi as rawSkillApi } from '@/api'
import { createAiclawChatSocket } from '@/api/aiclawWs'
import StreamingMessage from '@/components/StreamingMessage.vue'
import { getErrorMessage } from '@/types/skillstudio'

type AgentRecord = {
  id?: string
  name?: string
  displayName?: string
  identity?: { name?: string; emoji?: string }
}

type SkillRecord = {
  id?: string
  name?: string
  description?: string
  displayName?: string
  skillKey?: string
  current_version?: string
  git_commit?: string
  git_commit_full?: string
  skill_git_commit?: string
  skill_git_commit_full?: string
  deployed_git_commit?: string
  deployed_git_commit_full?: string
  docker_git_commit?: string
  docker_git_commit_full?: string
  sync_version_tag?: string
  sync_completed_at?: string
}

type ChatMessage = {
  role: string
  content: string
  streaming?: boolean
  finishReason?: string
  metrics?: Record<string, any>
}

type AttachmentRecord = {
  name: string
  mimeType: string
  data: string
  sizeBytes: number
}

type InstanceRecord = {
  name?: string
  bridge_online?: boolean
  bridge_connected_at?: string | null
}

type CapabilitiesRecord = {
  gateway_kind?: string
  skills_dir?: string
  [key: string]: unknown
}

type ChatEventPayload = {
  state?: string
  runId?: string
  delta?: string
  text?: string
  finishReason?: string
  finish_reason?: string
  metrics?: Record<string, any>
}

type ChatSocketMessage = {
  type?: string
  message?: string
  payload?: ChatEventPayload
}

type AiclawApiLike = typeof rawAiclawApi
type SkillApiLike = typeof rawSkillApi
type AgentListResponse = { items?: AgentRecord[] }
type DeviceImportResponse = { files_imported?: number; skill_id?: string }
type SyncSkillResponse = { files_pushed?: number; result?: { path?: string } }

const aiclawApi: AiclawApiLike = rawAiclawApi
const skillApi: SkillApiLike = rawSkillApi
const route = useRoute()
const router = useRouter()
const instanceId = String(route.params.id || '')

const loading = ref(false)
const sending = ref(false)
const instance = ref<InstanceRecord | null>(null)
const agents = ref<AgentRecord[]>([])
const skills = ref<SkillRecord[]>([])
const skillFilter = ref('')
const selectedAgentId = ref('')
const draft = ref('')
const messages = ref<ChatMessage[]>([])
const activeRunId = ref('')
const attachments = ref<AttachmentRecord[]>([])
const fileInputRef = ref<HTMLInputElement | null>(null)
const chatLogRef = ref<HTMLElement | null>(null)

// 设备能力（gateway_kind / skills_dir 等）
const capabilities = ref<CapabilitiesRecord>({})
// SkillForge 平台所有 skill（用于映射 + 推送选择）
const platformSkills = ref<SkillRecord[]>([])
const platformSkillIds = ref<Set<string>>(new Set())
const loadingPlatformSkills = ref(false)
// 推送 skill 弹窗
const showSyncModal = ref(false)
const syncing = ref(false)
const syncForm = reactive({ skillId: '' })

let socket: WebSocket | null = null

const selectedAgentLabel = computed(() => {
  const agent = agents.value.find(item => (item.id || item.name) === selectedAgentId.value)
  if (!agent) return '选择 Agent'
  return agent.identity?.name || agent.displayName || agent.name || agent.id
})

const filteredSkills = computed(() => {
  if (!skillFilter.value.trim()) return skills.value
  const q = skillFilter.value.toLowerCase()
  return skills.value.filter(s => {
    const name = (s.name || s.id || '').toLowerCase()
    const desc = (s.description || '').toLowerCase()
    return name.includes(q) || desc.includes(q)
  })
})

function shortDir(dir: string | undefined) {
  if (!dir) return ''
  // 缩短 home 路径：/home/skillforge/.aiclaw/skills → ~/.aiclaw/skills
  return dir.replace(/^\/home\/[^/]+/, '~').replace(/^\/Users\/[^/]+/, '~')
}

// 自动滚到底部
watch(() => messages.value.length, () => {
  nextTick(() => {
    if (chatLogRef.value) {
      chatLogRef.value.scrollTop = chatLogRef.value.scrollHeight
    }
  })
})

async function loadData() {
  loading.value = true
  try {
    const [instanceData, agentData, capData] = await Promise.all([
      aiclawApi.getInstance(instanceId) as Promise<InstanceRecord>,
      aiclawApi.listAgents(instanceId) as Promise<AgentListResponse>,
      aiclawApi.capabilities(instanceId).catch(() => ({} as Record<string, unknown>)),
    ])
    instance.value = instanceData
    agents.value = agentData.items || []
    capabilities.value = (capData || {}) as CapabilitiesRecord
    if (!selectedAgentId.value && agents.value.length) {
      await selectAgent(agents.value[0])
    } else if (selectedAgentId.value) {
      await loadSkills(selectedAgentId.value)
      connectSocket()
    }
    // 异步加载平台所有 skill 用于映射判断
    loadPlatformSkills()
  } catch (e) {
    Message.error(getErrorMessage(e, '加载 AIClaw 对话失败'))
  } finally {
    loading.value = false
  }
}

async function loadPlatformSkills() {
  // P2-6 全量分页加载：超过 200 个 skill 时单页拉取会截断,
  // 导致 skillExistsInPlatform 误判 → 触发误导入。改为按 total 循环分页拉满。
  loadingPlatformSkills.value = true
  try {
    const PAGE_SIZE = 200
    const MAX_PAGES = 50  // 安全上限：最多 1 万 skill
    const all = []
    let page = 1
    let total = Infinity
    while (page <= MAX_PAGES && all.length < total) {
      const res = await skillApi.list({ page, page_size: PAGE_SIZE }) as { items?: SkillRecord[]; total?: number }
      const items = res.items || []
      all.push(...items)
      total = typeof res.total === 'number' ? res.total : items.length
      if (items.length < PAGE_SIZE) break
      page += 1
    }
    if (all.length < total) {
      console.warn(`loadPlatformSkills 截断: 已加载 ${all.length}/${total}`)
    }
    platformSkills.value = all
    platformSkillIds.value = new Set(all.map(s => (s.id || '').toLowerCase()))
  } catch (e) {
    platformSkills.value = []
    platformSkillIds.value = new Set()
    console.error('loadPlatformSkills failed', e)
  } finally {
    loadingPlatformSkills.value = false
  }
}

function skillExistsInPlatform(skill: SkillRecord) {
  const name = (skill.id || skill.name || '').toLowerCase()
  return name && platformSkillIds.value.has(name)
}

function shortCommit(value: unknown): string {
  const text = String(value || '').trim()
  if (!text) return ''
  return text.length > 8 ? text.slice(0, 8) : text
}

function skillGitFull(skill: SkillRecord): string {
  return String(
    skill.deployed_git_commit_full ||
    skill.docker_git_commit_full ||
    skill.skill_git_commit_full ||
    skill.git_commit_full ||
    (String(skill.git_commit || '').length > 8 ? skill.git_commit : '') ||
    ''
  ).trim()
}

function skillGitShort(skill: SkillRecord): string {
  return String(
    skill.deployed_git_commit ||
    skill.docker_git_commit ||
    skill.skill_git_commit ||
    skill.git_commit ||
    shortCommit(skillGitFull(skill)) ||
    ''
  ).trim()
}

function skillGitState(skill: SkillRecord): 'ok' | 'stale' | 'unknown' {
  const deployed = String(skill.deployed_git_commit_full || '').trim()
  const current = String(skill.skill_git_commit_full || skill.git_commit_full || '').trim()
  if (deployed && current && deployed !== current) return 'stale'
  if (skillExistsInPlatform(skill) && !deployed && !skill.sync_completed_at) return 'unknown'
  return 'ok'
}

function skillTooltip(skill: SkillRecord): string {
  const action = skillExistsInPlatform(skill) ? '点击进入 Skill 编辑器' : '点击从设备导入到 Skill Studio'
  const lines = [action]
  const deployed = String(skill.deployed_git_commit_full || '').trim()
  const current = String(skill.skill_git_commit_full || skill.git_commit_full || '').trim()
  const full = skillGitFull(skill)
  if (deployed) lines.push(`节点同步 commit: ${deployed}`)
  else if (full) lines.push(`Skill Git commit: ${full}`)
  if (current && deployed && current !== deployed) lines.push(`当前仓库 commit: ${current}`)
  if (skill.sync_version_tag) lines.push(`发布标签: ${skill.sync_version_tag}`)
  if (skill.current_version) lines.push(`业务版本: ${skill.current_version}`)
  if (!deployed && skillExistsInPlatform(skill)) lines.push('旧记录没有保存节点 commit，显示当前仓库版本')
  return lines.join('\n')
}

async function onSkillClick(skill: SkillRecord) {
  const name = skill.id || skill.name
  if (!name) return
  // 如果平台 skill 列表还在加载，等一下；避免误判为"未在平台"
  if (loadingPlatformSkills.value) {
    Message.info('正在加载平台 Skill 列表，请稍候')
    return
  }
  // 二次确认：即使列表里有，也实际探测一下（防止 list 缓存与 single 不同步）
  if (skillExistsInPlatform(skill)) {
    try {
      await skillApi.get(name)
      router.push(`/skills/${name}`)
      return
    } catch (e) {
      if ((e as { response?: { status?: number } })?.response?.status === 404) {
        // 列表说有，单条又 404 — 列表过期。从 platformSkillIds 摘掉，falls through 到导入流程
        platformSkillIds.value.delete(name.toLowerCase())
        platformSkills.value = platformSkills.value.filter(s => s.id !== name)
      } else {
        // 其它错误（403 / 500 等），让用户知道但不掉到导入流程
        Message.error(getErrorMessage(e, '加载 Skill 失败'))
        return
      }
    }
  }
  // 设备上有但 SkillForge 平台没有 → 弹「从设备导入」对话框
  Modal.confirm({
    title: `从设备导入 ${name}？`,
    content: () => h('div', { style: 'line-height: 1.6' }, [
      h('p', { style: 'margin: 0 0 8px' },
        `Skill "${name}" 在设备上存在，但 Skill Studio 没有对应记录。`),
      h('p', { style: 'margin: 0 0 8px' },
        '点击确定 → 通过 bridge 把设备上的 SKILL.md / scripts / 配置文件读回平台，'
        + '创建 Skill Studio 记录，然后跳到 SkillStudio 编辑。'),
      h('p', { style: 'margin: 0; color: var(--ai-ink-3); font-size: 12px' },
        '⚠️ 这是一次性领养，导入后再次"推送 Skill" 才会回写到设备。'),
    ]),
    okText: '导入到 Skill Studio',
    cancelText: '取消',
    async onOk() {
      try {
        const res = await aiclawApi.importSkillFromDevice(instanceId, name) as DeviceImportResponse
        Message.success(`已导入 ${res.files_imported} 个文件`)
        // 刷新平台 skill 列表（让 tag 变蓝）
        await loadPlatformSkills()
        // 跳到 SkillStudio 编辑
        router.push(`/skills/${res.skill_id}`)
      } catch (e) {
        Message.error(getErrorMessage(e, '导入失败'))
        throw e
      }
    },
  })
}

async function doSyncSkill() {
  if (!syncForm.skillId) {
    Message.warning('请选择一个 Skill')
    return
  }
  syncing.value = true
  try {
    const res = await aiclawApi.syncSkill(instanceId, syncForm.skillId) as SyncSkillResponse
    Message.success(`已推送 ${res.files_pushed} 个文件到 ${res.result?.path || '设备'}`)
    showSyncModal.value = false
    syncForm.skillId = ''
    // 重新拉一下当前 agent 的 skills 列表
    if (selectedAgentId.value) {
      await loadSkills(selectedAgentId.value)
    }
  } catch (e) {
    Message.error(getErrorMessage(e, '推送失败'))
  } finally {
    syncing.value = false
  }
}

async function selectAgent(agent: AgentRecord) {
  selectedAgentId.value = agent.id || agent.name || ''
  await loadSkills(selectedAgentId.value)
  connectSocket()
}

async function loadSkills(agentId: string) {
  try {
    const data = await aiclawApi.listSkills(instanceId, agentId) as { items?: SkillRecord[] }
    skills.value = data.items || []
  } catch (e) {
    skills.value = []
    Message.error(getErrorMessage(e, '加载 Skills 失败'))
  }
}

// v2.11.3: 前端快速切换 agent 时，旧 WS 的异步 close 还没完成，
// 新 new WebSocket() 就已经建立并被 close()，导致后端日志里看到
// 背靠背的 "accepted → connection open → connection closed"。
// 用 seq 守护：只有最后一次 connectSocket 能真正建连。
let connectSeq = 0
async function connectSocket() {
  if (!selectedAgentId.value) return
  const seq = ++connectSeq
  const oldSocket = socket
  socket = null
  oldSocket?.close()
  // 让浏览器处理 close 事件，防止新旧 WS 重叠
  await new Promise<void>((r) => setTimeout(r, 0))
  if (seq !== connectSeq) return
  if (!selectedAgentId.value) return
  socket = createAiclawChatSocket(instanceId, selectedAgentId.value, {
    onMessage: handleSocketMessage,
    onClose: () => {
      activeRunId.value = ''
    },
  })
}

function handleSocketMessage(msg: ChatSocketMessage) {
  if (msg.type === 'error') {
    Message.error(msg.message || '对话失败')
    finalizeAssistant(msg.message || '对话失败')
    return
  }
  if (msg.type !== 'chat_event') return
  const payload = msg.payload || {}
  if (payload.state === 'started') {
    activeRunId.value = payload.runId || ''
    return
  }

  const assistant = ensureAssistantMessage()
  // 后端 _normalize_chat_event 把 agent 事件的 text 当作 delta 字段返回，
  // 但实际上是 snapshot 全文（每次都是 AI 已生成的完整内容），所以要"替换"而不是"追加"。
  if (payload.delta != null && payload.delta !== '') {
    assistant.content = payload.delta
  } else if (payload.text != null && payload.text !== '') {
    assistant.content = payload.text
  }

  if (['final', 'error', 'aborted'].includes(payload.state || '')) {
    const finishReason = payload.finishReason || payload.finish_reason || ''
    assistant.finishReason = finishReason
    assistant.metrics = payload.metrics || {}
    if (finishReason === 'length' && assistant.content && !assistant.content.includes('输出达到长度上限')) {
      assistant.content += '\n\n[系统提示] 输出达到长度上限，回复可能未完整结束；可以继续追问“继续”。'
    }
    assistant.streaming = false
    activeRunId.value = ''
    sending.value = false
  }
}

function formatNumber(value: unknown, digits = 2): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return ''
  return num.toFixed(digits).replace(/\.?0+$/, '')
}

function chatMessageMeta(msg: ChatMessage): string {
  if (msg.role === 'user') return ''
  const metrics = msg.metrics || {}
  const parts: string[] = []
  const tokensPerSecond = formatNumber(metrics.tokens_per_second ?? metrics.tokensPerSecond)
  if (tokensPerSecond) parts.push(`${tokensPerSecond} token/s`)
  const generatedTokens = formatNumber(metrics.generated_tokens ?? metrics.generatedTokens, 0)
  const maxTokens = formatNumber(metrics.effective_max_new_tokens ?? metrics.max_new_tokens ?? metrics.maxNewTokens, 0)
  if (generatedTokens && maxTokens) {
    parts.push(`${generatedTokens}/${maxTokens} tokens`)
  } else if (generatedTokens) {
    parts.push(`${generatedTokens} tokens`)
  }
  const runtime = metrics.inference_runtime === 'local_bridge' ? '本地 Bridge' : ''
  if (runtime) parts.push(runtime)
  return parts.join(' · ')
}

function ensureAssistantMessage(): ChatMessage {
  const last = messages.value[messages.value.length - 1]
  if (last?.role === 'assistant' && last.streaming) return last
  const msg = { role: 'assistant', content: '', streaming: true }
  messages.value.push(msg)
  return msg
}

function finalizeAssistant(text: string) {
  const assistant = ensureAssistantMessage()
  if (text && !assistant.content) assistant.content = text
  assistant.streaming = false
  sending.value = false
}

function sendMessage() {
  if (!draft.value.trim() || !socket || socket.readyState !== WebSocket.OPEN) {
    Message.warning('连接未就绪')
    return
  }
  const text = draft.value.trim()
  messages.value.push({ role: 'user', content: text, streaming: false })
  messages.value.push({ role: 'assistant', content: '', streaming: true })
  socket.send(JSON.stringify({ type: 'user_message', content: text, attachments: attachments.value }))
  draft.value = ''
  attachments.value = []
  sending.value = true
}

function abortRun() {
  if (!activeRunId.value || !socket || socket.readyState !== WebSocket.OPEN) return
  socket.send(JSON.stringify({ type: 'abort', runId: activeRunId.value }))
}

function pickFiles() {
  fileInputRef.value?.click()
}

function removeAttachment(index: number) {
  attachments.value.splice(index, 1)
}

const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
const MAX_ATTACHMENTS_TOTAL_BYTES = 10 * 1024 * 1024

async function handleFileChange(event: Event) {
  const target = event.target as HTMLInputElement | null
  const files = Array.from(target?.files || []) as File[]
  for (const file of files) {
    if (!file.type || !file.type.startsWith('image/')) {
      Message.warning(`${file.name} 不是图片，已跳过（仅支持 image/*）`)
      continue
    }
    if (file.size > MAX_ATTACHMENT_BYTES) {
      Message.warning(`${file.name} 超过 5MB 限制`)
      continue
    }
    const currentTotal = attachments.value.reduce((sum, a) => sum + (a.sizeBytes || 0), 0)
    if (currentTotal + file.size > MAX_ATTACHMENTS_TOTAL_BYTES) {
      Message.warning(`附件总大小将超过 10MB，已停止添加`)
      break
    }
    try {
      const encoded = await fileToAttachment(file)
      attachments.value.push(encoded)
    } catch {
      Message.error(`${file.name} 读取失败`)
    }
  }
  if (target) target.value = ''
}

function fileToAttachment(file: File): Promise<AttachmentRecord> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = String(reader.result || '')
      // dataUrl 形如 "data:image/png;base64,iVBORw0..."
      const commaIdx = dataUrl.indexOf(',')
      const base64 = commaIdx >= 0 ? dataUrl.slice(commaIdx + 1) : dataUrl
      resolve({
        name: file.name,
        mimeType: file.type || 'application/octet-stream',
        data: base64,
        sizeBytes: file.size,
      })
    }
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

onMounted(loadData)
onBeforeUnmount(() => socket?.close())
</script>

<style scoped>
/* ═══ 全屏 IDE 布局：顶栏 + 主体两栏 ═══ */
.workspace-shell {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 96px);
  background: var(--ai-bg);
}

/* 顶栏 */
.ws-toolbar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
  gap: 12px;
}
.ws-title-block {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}
.ws-title {
  font-size: 16px;
  font-weight: 800;
  color: var(--ai-ink-1);
  letter-spacing: -0.01em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 320px;
}
.ws-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}
.status-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ai-ink-4);
  margin-right: 4px;
  vertical-align: middle;
}
.status-dot.online {
  background: var(--ai-ok);
  box-shadow: 0 0 0 3px var(--ai-ok-soft);
}

/* 主体 grid */
.ws-body {
  flex: 1;
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  min-height: 0;
  overflow: hidden;
}

/* ═══ 左侧 sidebar ═══ */
.ws-sidebar {
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  min-height: 0;
  overflow: hidden;
}
.ws-section {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 12px 14px;
  border-bottom: 1px solid var(--ai-border);
}
.ws-section-skills {
  flex: 1;
  border-bottom: none;
}
.ws-section-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--ai-ink-3);
  margin-bottom: 8px;
  flex-shrink: 0;
}
.ws-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 16px;
  padding: 0 6px;
  font-size: 10px;
  font-weight: 700;
  background: var(--ai-surface-3);
  color: var(--ai-ink-2);
  border-radius: 8px;
}
.ws-dir-tag {
  margin-left: auto;
  font-family: var(--ai-font-mono);
  font-size: 10px !important;
  text-transform: none;
  letter-spacing: 0;
  font-weight: 600;
}

/* Agents 列表 */
.agent-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.agent-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  text-align: left;
  padding: 8px 10px;
  cursor: pointer;
  transition: all 0.12s;
}
.agent-item:hover {
  background: var(--ai-surface-2);
}
.agent-item.active {
  background: var(--ai-accent-soft);
  border-color: var(--ai-accent-ink);
}
.agent-emoji {
  font-size: 18px;
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--ai-surface);
  border-radius: 8px;
  border: 1px solid var(--ai-border);
}
.agent-info {
  flex: 1;
  min-width: 0;
}
.agent-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--ai-ink-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.agent-meta {
  font-size: 11px;
  color: var(--ai-ink-3);
  font-family: var(--ai-font-mono);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Skills 列表（独立 scroll） */
.skill-filter {
  margin-bottom: 8px;
}
.skill-list-scroll {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  margin: 0 -4px;
  padding: 0 4px;
}
.skill-list-scroll::-webkit-scrollbar {
  width: 6px;
}
.skill-list-scroll::-webkit-scrollbar-thumb {
  background: var(--ai-surface-3);
  border-radius: 3px;
}
.skill-grid {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-bottom: 8px;
}
.skill-pill {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  text-align: left;
  padding: 5px 8px;
  cursor: pointer;
  font-size: 12px;
  color: var(--ai-ink-2);
  transition: all 0.1s;
}
.skill-pill:hover {
  background: var(--ai-surface-2);
  border-color: var(--ai-border);
}
.skill-pill.linked {
  color: var(--ai-accent-ink);
}
.skill-pill.linked:hover {
  background: var(--ai-accent-soft);
  border-color: var(--ai-accent-ink);
}
.skill-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ai-ink-5);
  flex-shrink: 0;
}
.skill-pill.linked .skill-dot {
  background: var(--ai-accent-ink);
}
.skill-name {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-weight: 600;
  min-width: 0;
}
.skill-git {
  flex-shrink: 0;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-family: var(--ai-font-mono);
  font-size: 10px;
  line-height: 16px;
  font-weight: 700;
}
.skill-git--stale {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
}
.skill-git--unknown {
  color: var(--ai-ink-3);
}

/* ═══ 右侧 chat ═══ */
.ws-chat {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--ai-bg);
}
.chat-head {
  flex-shrink: 0;
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 14px 24px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
}
.chat-title {
  font-size: 16px;
  font-weight: 800;
  color: var(--ai-ink-1);
}
.chat-subtitle {
  font-size: 12px;
  color: var(--ai-ink-3);
  font-family: var(--ai-font-mono);
}
.chat-log {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 24px;
}
.chat-input {
  flex-shrink: 0;
  padding: 14px 20px 18px;
  border-top: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
}
.chat-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}
.attachment-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.hidden-file-input {
  display: none;
}

@media (max-width: 960px) {
  .ws-body {
    grid-template-columns: 1fr;
  }
  .ws-sidebar {
    display: none;
  }
}
</style>
