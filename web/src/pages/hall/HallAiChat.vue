<template>
  <div class="ai-chat-page" @paste="onPaste">
    <aside class="thread-sidebar" :class="{ open: mobileSidebarOpen }">
      <div class="sidebar-head">
        <a-button class="new-thread" type="text" @click="startNewThread">
          <template #icon><icon-edit /></template>
          新对话
        </a-button>
        <a-button class="mobile-close" type="text" aria-label="关闭会话列表" @click="mobileSidebarOpen = false">
          <icon-close />
        </a-button>
      </div>
      <a-input-search
        v-model="threadSearch"
        allow-clear
        placeholder="搜索对话标题"
        class="thread-search"
        @input="queueThreadSearch"
      />
      <div class="history-tabs">
        <button :class="{ active: !showArchived }" @click="switchArchive(false)">历史</button>
        <button :class="{ active: showArchived }" @click="switchArchive(true)">已归档</button>
      </div>
      <div class="thread-list" @scroll.passive="onThreadListScroll">
        <button
          v-for="thread in threads"
          :key="thread.id"
          class="thread-item"
          :class="{ active: activeThread?.id === thread.id }"
          @click="selectThread(thread)"
        >
          <span class="thread-title">{{ thread.title }}</span>
          <span class="thread-meta">
            <span>{{ thread.message_count || 0 }} 条</span>
            <span>{{ relativeTime(thread.last_message_at) }}</span>
          </span>
        </button>
        <div v-if="threadsLoading" class="list-state"><a-spin :size="14" /> 加载中</div>
        <button v-else-if="threadsHasMore" class="load-more" @click="loadThreads(true)">加载更多</button>
        <div v-else-if="!threads.length" class="list-state">{{ showArchived ? '暂无归档对话' : '还没有对话' }}</div>
      </div>
      <div class="sidebar-foot">
        <span class="retention-dot"></span>
        连续记忆已开启 · 仅自己可见
      </div>
    </aside>

    <main class="chat-main">
      <header class="chat-head">
        <a-button class="mobile-menu" type="text" @click="mobileSidebarOpen = true"><icon-menu /></a-button>
        <div class="title-wrap">
          <a-input
            v-if="renaming && activeThread"
            ref="titleInputRef"
            v-model="renameDraft"
            size="small"
            :max-length="160"
            @press-enter="saveRename"
            @blur="saveRename"
          />
          <button v-else class="chat-title" :disabled="!activeThread" @click="beginRename">
            <span>{{ activeThread?.title || 'AI Chat' }}</span>
            <icon-edit v-if="activeThread" class="title-edit-icon" />
          </button>
          <span v-if="activeThread" class="title-hint">点击修改名称</span>
        </div>
        <div class="head-actions">
          <a-dropdown v-if="activeThread" trigger="click" position="br">
            <a-button class="head-more" type="text" aria-label="对话操作"><icon-more /></a-button>
            <template #content>
              <a-doption @click="beginRename">重命名对话</a-doption>
              <a-doption @click="toggleArchive">{{ activeThread.archived ? '恢复对话' : '归档对话' }}</a-doption>
              <a-doption class="danger-option" @click="requestDeleteThread">删除对话</a-doption>
            </template>
          </a-dropdown>
        </div>
      </header>

      <section ref="messageViewport" class="message-viewport" @scroll.passive="onMessageScroll">
        <div class="message-column">
          <div v-if="messagesHasMore" class="older-wrap">
            <a-button size="small" type="text" :loading="messagesLoading" :disabled="messages.length >= maxRenderedMessages" @click="loadOlderMessages">
              {{ messages.length >= maxRenderedMessages ? '已加载最近 150 条' : '加载更早消息' }}
            </a-button>
          </div>

          <section v-if="!messages.length && !messagesLoading" class="welcome">
            <div class="welcome-logo">AI</div>
            <h1>开始一段新对话</h1>
            <p>{{ visionIntro }}</p>
            <div class="starter-grid">
              <button @click="useStarter('帮我把这段内容整理成简洁的工作汇报')">整理工作汇报</button>
              <button v-if="visionReady" @click="useStarter('分析这张图片中的重点信息和可能存在的问题')">分析图片</button>
              <button @click="useStarter('给我三个可执行的方案，并说明各自取舍')">制定方案</button>
            </div>
          </section>

          <article v-for="message in visibleMessages" :key="message.id" class="message-row" :class="message.role">
            <div class="message-avatar">{{ message.role === 'user' ? '你' : 'AI' }}</div>
            <div class="message-content">
              <div v-if="message.attachments?.length" class="message-images">
                <a
                  v-for="asset in message.attachments"
                  :key="asset.id"
                  :href="asset.url"
                  target="_blank"
                  rel="noopener"
                >
                  <img :src="asset.thumbnail_url || asset.url" :alt="asset.name" loading="lazy" decoding="async" />
                </a>
              </div>
              <div v-if="message.role === 'assistant'" class="markdown-body" v-html="renderMd(message.content || streamPlaceholder(message))" />
              <div v-else class="user-text">{{ message.content }}</div>
              <div v-if="message.status === 'failed' || message.error_message" class="message-error">
                {{ message.error_message || '生成失败，请重试' }}
              </div>
              <div v-if="message.role === 'assistant'" class="message-foot">
                <span v-if="message.model" class="model-badge">{{ message.model }}</span>
                <span v-if="message.vision_model && message.vision_model !== message.model" class="vision-badge">
                  图片识别：{{ message.vision_model }}
                </span>
                <span v-if="message.reasoning_effort && message.reasoning_effort !== 'auto'" class="reasoning-badge">
                  思考：{{ reasoningLabel(message.reasoning_effort) }}
                </span>
                <span v-if="message.latency_ms" class="latency">{{ Math.round(message.latency_ms / 100) / 10 }}s</span>
                <a-button type="text" size="mini" @click="copyMessage(message)"><icon-copy /> 复制</a-button>
                <a-button
                  v-if="['completed', 'failed', 'interrupted'].includes(message.status)"
                  type="text"
                  size="mini"
                  :disabled="running"
                  @click="regenerate(message)"
                >
                  <icon-refresh /> 重新生成
                </a-button>
              </div>
            </div>
          </article>
        </div>
      </section>

      <footer class="composer-shell">
        <div class="composer-card" :class="{ dragover: dragging, uploading }" @dragenter.prevent="dragging = true" @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="onDrop">
          <input ref="fileInput" type="file" accept="image/png,image/jpeg,image/webp" multiple hidden @change="onFileChange" />
          <div v-if="pendingAttachments.length" class="pending-images">
            <div v-for="asset in pendingAttachments" :key="asset.id" class="pending-image">
              <img :src="asset.thumbnail_url || asset.url" :alt="asset.name" />
              <button :aria-label="`移除 ${asset.name}`" @click="removeAttachment(asset.id)">×</button>
            </div>
          </div>
          <div v-if="attachmentStatus" class="attachment-status" role="status">
            <a-spin v-if="uploading" :size="13" />
            <icon-check v-else />
            {{ attachmentStatus }}
          </div>
          <a-textarea
            v-model="draft"
            class="chat-input"
            :auto-size="{ minRows: 1, maxRows: 8 }"
            :max-length="32000"
            placeholder="给 AI Chat 发消息"
            @keydown="onInputKeydown"
          />
          <div class="composer-actions">
            <div class="composer-left">
              <a-tooltip :content="visionTooltip">
                <a-button class="attach-button" shape="circle" :aria-label="visionButtonLabel" :disabled="!visionReady || uploading || pendingAttachments.length >= 4" :loading="uploading" @click="openFilePicker">
                  <span v-if="!uploading" class="attach-plus" aria-hidden="true">＋</span>
                </a-button>
              </a-tooltip>
              <a-dropdown trigger="click" position="top">
                <button class="model-trigger" aria-label="选择模型">
                  <span class="model-status" :class="selectedModelStatus"></span>
                  <span class="model-trigger-name">{{ selectedModelName }}</span>
                  <icon-down class="model-trigger-arrow" />
                </button>
                <template #content>
                  <template v-for="group in visibleModelGroups" :key="group.id">
                    <a-doption class="model-group-label" disabled>{{ group.name }}</a-doption>
                    <a-doption v-for="model in group.models" :key="model.id" class="model-option" @click="chooseModel(model.id)">
                      <span class="model-option-copy">
                        <span class="model-option-title">
                          <span>{{ model.name }}</span>
                          <span class="modality-badge" :class="{ vision: model.supports_vision }">{{ model.vision_label || (model.supports_vision ? '支持图片' : '仅文本') }}</span>
                        </span>
                        <small :title="model.limit_note">{{ officialModelCapacity(model) }}</small>
                      </span>
                      <span v-if="selectedModel === model.id" class="model-current">当前</span>
                    </a-doption>
                  </template>
                </template>
              </a-dropdown>
              <a-dropdown v-if="selectedReasoningOptions.length > 1" trigger="click" position="top">
                <button class="reasoning-trigger" aria-label="选择思考深度">
                  <span>{{ reasoningOptionLabel(reasoningEffort) }}</span>
                  <icon-down />
                </button>
                <template #content>
                  <a-doption class="reasoning-menu-label" disabled>思考深度</a-doption>
                  <a-doption
                    v-for="option in selectedReasoningOptions"
                    :key="option"
                    class="reasoning-option"
                    @click="chooseReasoningEffort(option)"
                  >
                    <span>{{ reasoningOptionLabel(option) }}</span>
                    <icon-check v-if="reasoningEffort === option" />
                  </a-doption>
                </template>
              </a-dropdown>
              <a-tooltip :content="visionRouteLabel">
                <span class="vision-indicator" :class="{ unavailable: !visionReady }" aria-label="图片识别状态">
                  <icon-image />
                </span>
              </a-tooltip>
            </div>
            <a-button v-if="running" class="send-button stop" shape="circle" aria-label="停止生成" @click="stopGeneration">
              <icon-stop />
            </a-button>
            <a-button v-else class="send-button" type="primary" shape="circle" aria-label="发送" :disabled="!canSend" @click="sendMessage">
              <svg class="send-arrow-icon" viewBox="0 0 20 20" aria-hidden="true">
                <path d="M10 15.5V4.5M5.75 8.75 10 4.5l4.25 4.25" />
              </svg>
            </a-button>
          </div>
        </div>
        <div class="composer-note">Enter 发送 · Shift+Enter 换行 · Ctrl+V 可直接粘贴图片</div>
      </footer>
    </main>
    <div v-if="mobileSidebarOpen" class="sidebar-mask" @click="mobileSidebarOpen = false"></div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { hallApi } from '@/api'
import {
  AI_CHAT_DEFAULT_MODEL,
  AI_CHAT_DEFAULT_VISION_MODEL,
  createBuiltInAiChatModelGroups,
  type AiChatModelGroup as ModelGroup,
  type AiChatModelRow as ModelRow,
} from '@/config/aiChatModels'
import { confirmDelete } from '@/utils/confirmDelete'
import { renderMd } from '@/utils/renderMd'

type Thread = {
  id: string
  title: string
  default_model: string
  message_count: number
  archived: boolean
  last_message_at?: string
}

type Attachment = {
  id: string
  name: string
  url: string
  thumbnail_url?: string
  byte_size?: number
}

type ChatMessage = {
  id: string
  thread_id?: string
  role: 'user' | 'assistant'
  content: string
  status: string
  model?: string
  vision_model?: string
  reasoning_effort?: string
  latency_ms?: number
  error_message?: string
  attachments?: Attachment[]
}

const maxRenderedMessages = 150
const lastModelStorageKey = 'skillforge.ai-chat.last-model'
const builtInModelGroups = createBuiltInAiChatModelGroups()
const builtInModelIds = new Set(builtInModelGroups.flatMap(group => group.models.map(model => model.id)))
const storedModel = window.localStorage.getItem(lastModelStorageKey) || ''
const threads = ref<Thread[]>([])
const threadsCursor = ref<string | null>(null)
const threadsHasMore = ref(false)
const threadsLoading = ref(false)
const threadSearch = ref('')
const showArchived = ref(false)
const activeThread = ref<Thread | null>(null)
const messages = ref<ChatMessage[]>([])
const messagesCursor = ref<string | null>(null)
const messagesHasMore = ref(false)
const messagesLoading = ref(false)
const modelGroups = ref<ModelGroup[]>(builtInModelGroups)
const selectedModel = ref(builtInModelIds.has(storedModel) ? storedModel : AI_CHAT_DEFAULT_MODEL)
const reasoningEffort = ref('auto')
const visionReady = ref(true)
const visionModel = ref(AI_CHAT_DEFAULT_VISION_MODEL)
const draft = ref('')
const pendingAttachments = ref<Attachment[]>([])
const uploading = ref(false)
const attachmentStatus = ref('')
const running = ref(false)
const activeAssistantId = ref('')
const activeAbort = ref<AbortController | null>(null)
const dragging = ref(false)
const mobileSidebarOpen = ref(false)
const renaming = ref(false)
const renameDraft = ref('')
const messageViewport = ref<HTMLElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const titleInputRef = ref<any>(null)
const threadCache = new Map<string, { items: ChatMessage[]; cursor: string | null; hasMore: boolean }>()
const handledPasteEvents = new WeakSet<Event>()
let searchTimer: number | undefined
let attachmentStatusTimer: number | undefined
let recentPasteFingerprint = ''
let recentPasteAt = 0

const allModels = computed(() => modelGroups.value.flatMap(group => group.models))
const visibleModelGroups = computed(() => modelGroups.value
  .map(group => ({ ...group, models: group.models.filter(model => model.available) }))
  .filter(group => group.models.length))
const selectedModelRow = computed(() => allModels.value.find(model => model.id === selectedModel.value))
const selectedModelStatus = computed(() => selectedModelRow.value?.status || 'unavailable')
const selectedModelName = computed(() => selectedModelRow.value?.name || selectedModel.value || '选择模型')
const selectedModelSupportsVision = computed(() => Boolean(selectedModelRow.value?.supports_vision))
const selectedReasoningOptions = computed(() => selectedModelRow.value?.reasoning_options || ['auto'])
const visionIntro = computed(() => {
  if (!visionReady.value) return '选择模型，输入问题。图片识别暂不可用。'
  if (selectedModelSupportsVision.value) return '选择模型，输入问题；当前模型也可以直接识别图片。'
  return `选择模型，输入问题；图片会先由 ${visionModel.value} 识别，再交给当前模型回答。`
})
const visionTooltip = computed(() => {
  if (!visionReady.value) return '图片识别模型暂不可用'
  return selectedModelSupportsVision.value
    ? '添加图片，也支持 Ctrl+V 粘贴'
    : `添加图片；先由 ${visionModel.value} 识别，再交给当前模型回答`
})
const visionButtonLabel = computed(() => visionReady.value ? '添加图片' : '图片识别暂不可用')
const visionRouteLabel = computed(() => {
  if (!visionReady.value) return '图片识别暂不可用'
  return selectedModelSupportsVision.value ? '当前模型可直接识别图片' : `图片先由 ${visionModel.value} 识别`
})
const canSend = computed(() => Boolean((draft.value.trim() || pendingAttachments.value.length) && selectedModelRow.value?.available && !running.value && !uploading.value))
const visibleMessages = computed(() => messages.value.slice(-maxRenderedMessages))

function streamPlaceholder(message: ChatMessage) {
  if (message.status === 'streaming') return '正在思考…'
  if (message.status === 'interrupted') return message.content || '生成已停止'
  return ''
}

function relativeTime(value?: string) {
  if (!value) return ''
  const delta = Date.now() - new Date(value).getTime()
  if (delta < 60_000) return '刚刚'
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)} 分钟前`
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)} 小时前`
  return new Date(value).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
}

function compactTokens(value?: number) {
  const tokens = Number(value || 0)
  if (tokens >= 1_000_000) return `${Math.round(tokens / 10_000) / 100}M`
  if (tokens >= 1_000) return `${Math.round(tokens / 100) / 10}K`
  return String(tokens || '—')
}

function officialModelCapacity(model: ModelRow) {
  const output = model.max_output_display || compactTokens(model.max_output_tokens)
  return `官方上限 ${compactTokens(model.context_window_tokens)} · 最大输出 ${output}`
}

const reasoningLabels: Record<string, string> = {
  auto: '自动',
  none: '关闭',
  low: '低',
  medium: '中',
  high: '高',
  xhigh: '极高',
  max: '最大',
}

function reasoningLabel(value?: string) {
  return reasoningLabels[String(value || 'auto')] || String(value || '自动')
}

function reasoningOptionLabel(value: string) {
  if (selectedModel.value === 'deepseek-v4-pro' && value === 'high') return '开启'
  return reasoningLabel(value)
}

function reasoningStorageKey(modelId: string) {
  return `skillforge.ai-chat.reasoning.${modelId}`
}

function restoreReasoning(modelId: string) {
  const allowed = allModels.value.find(model => model.id === modelId)?.reasoning_options || ['auto']
  const saved = window.localStorage.getItem(reasoningStorageKey(modelId)) || 'auto'
  reasoningEffort.value = allowed.includes(saved) ? saved : 'auto'
}

function chooseReasoningEffort(value: string) {
  if (!selectedReasoningOptions.value.includes(value)) return
  reasoningEffort.value = value
  window.localStorage.setItem(reasoningStorageKey(selectedModel.value), value)
}

async function loadModels() {
  try {
    const result: any = await hallApi.aiChatModels()
    if (Array.isArray(result?.groups) && result.groups.length) modelGroups.value = result.groups
    visionReady.value = Boolean(result?.vision?.ready)
    visionModel.value = result?.vision?.model || AI_CHAT_DEFAULT_VISION_MODEL
    const localPreferred = allModels.value.find(model => model.id === window.localStorage.getItem(lastModelStorageKey) && model.available)
    const preferred = allModels.value.find(model => model.id === result?.preferred_model && model.available)
    const fallback = allModels.value.find(model => model.id === result?.default_model && model.available) || allModels.value.find(model => model.available)
    selectedModel.value = localPreferred?.id || preferred?.id || fallback?.id || result?.default_model || AI_CHAT_DEFAULT_MODEL
    restoreReasoning(selectedModel.value)
  } catch (error: any) {
    console.warn('AI Chat model metadata refresh failed; using the built-in catalog', error?._message || error)
  }
}

async function loadThreads(append = false) {
  if (threadsLoading.value) return
  threadsLoading.value = true
  try {
    const result: any = await hallApi.aiChatThreads({
      archived: showArchived.value,
      q: threadSearch.value.trim() || undefined,
      cursor: append ? threadsCursor.value || undefined : undefined,
      limit: 30,
    })
    const incoming = result?.items || []
    threads.value = append ? [...threads.value, ...incoming] : incoming
    threadsCursor.value = result?.next_cursor || null
    threadsHasMore.value = Boolean(result?.has_more)
    if (!append && !activeThread.value && threads.value.length && !showArchived.value) {
      await selectThread(threads.value[0])
    }
  } catch (error: any) {
    Message.error(error?._message || '历史对话加载失败')
  } finally {
    threadsLoading.value = false
  }
}

function queueThreadSearch() {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => loadThreads(false), 250)
}

async function switchArchive(value: boolean) {
  showArchived.value = value
  activeThread.value = null
  messages.value = []
  await loadThreads(false)
}

function cacheCurrentThread() {
  if (!activeThread.value) return
  threadCache.delete(activeThread.value.id)
  threadCache.set(activeThread.value.id, {
    items: messages.value.slice(-maxRenderedMessages),
    cursor: messagesCursor.value,
    hasMore: messagesHasMore.value,
  })
  while (threadCache.size > 3) {
    const oldest = threadCache.keys().next().value
    if (oldest) threadCache.delete(oldest)
  }
}

async function selectThread(thread: Thread) {
  if (running.value) await stopGeneration()
  cacheCurrentThread()
  activeThread.value = thread
  const threadModel = allModels.value.find(model => model.id === thread.default_model && model.available)
  if (threadModel) {
    selectedModel.value = threadModel.id
    restoreReasoning(threadModel.id)
  }
  mobileSidebarOpen.value = false
  const cached = threadCache.get(thread.id)
  if (cached && !cached.items.some(item => ['queued', 'streaming'].includes(item.status))) {
    messages.value = [...cached.items]
    messagesCursor.value = cached.cursor
    messagesHasMore.value = cached.hasMore
    await scrollToBottom(false)
    return
  }
  messagesLoading.value = true
  try {
    const result: any = await hallApi.aiChatMessages(thread.id, { limit: 50 })
    messages.value = result?.items || []
    messagesCursor.value = result?.next_cursor || null
    messagesHasMore.value = Boolean(result?.has_more)
    await scrollToBottom(false)
  } catch (error: any) {
    Message.error(error?._message || '消息加载失败')
  } finally {
    messagesLoading.value = false
  }
}

async function loadOlderMessages() {
  if (!activeThread.value || !messagesCursor.value || messagesLoading.value || messages.value.length >= maxRenderedMessages) return
  const viewport = messageViewport.value
  const oldHeight = viewport?.scrollHeight || 0
  messagesLoading.value = true
  try {
    const result: any = await hallApi.aiChatMessages(activeThread.value.id, { cursor: messagesCursor.value, limit: 50 })
    messages.value = [...(result?.items || []), ...messages.value].slice(-maxRenderedMessages)
    messagesCursor.value = result?.next_cursor || null
    messagesHasMore.value = Boolean(result?.has_more)
    await nextTick()
    if (viewport) viewport.scrollTop += viewport.scrollHeight - oldHeight
  } finally {
    messagesLoading.value = false
  }
}

function onMessageScroll() {
  if ((messageViewport.value?.scrollTop || 0) < 80 && messagesHasMore.value) loadOlderMessages()
}

function onThreadListScroll(event: Event) {
  const el = event.currentTarget as HTMLElement
  if (el.scrollHeight - el.scrollTop - el.clientHeight < 120 && threadsHasMore.value) loadThreads(true)
}

async function scrollToBottom(smooth = true) {
  await nextTick()
  messageViewport.value?.scrollTo({ top: messageViewport.value.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
}

function startNewThread() {
  cacheCurrentThread()
  activeThread.value = null
  messages.value = []
  messagesCursor.value = null
  messagesHasMore.value = false
  draft.value = ''
  pendingAttachments.value = []
  mobileSidebarOpen.value = false
}

function useStarter(value: string) {
  draft.value = value
  focusComposer()
}

async function ensureThread() {
  if (activeThread.value) return activeThread.value
  const created: any = await hallApi.createAiChatThread({ model: selectedModel.value })
  activeThread.value = created
  threads.value = [created, ...threads.value.filter(item => item.id !== created.id)]
  return created as Thread
}

async function chooseModel(modelId: string) {
  const model = allModels.value.find(item => item.id === modelId && item.available)
  if (!model) return
  selectedModel.value = model.id
  window.localStorage.setItem(lastModelStorageKey, model.id)
  restoreReasoning(model.id)
  try {
    await hallApi.updateAiChatPreference({ last_model: model.id })
    if (activeThread.value) {
      const updated: any = await hallApi.updateAiChatThread(activeThread.value.id, { default_model: model.id })
      activeThread.value = updated
      const index = threads.value.findIndex(item => item.id === updated.id)
      if (index >= 0) threads.value[index] = updated
    }
  } catch (error: any) {
    Message.error(error?._message || '模型设置保存失败')
  }
}

function openFilePicker() {
  if (visionReady.value) fileInput.value?.click()
}

function showAttachmentStatus(message: string, timeout = 1800) {
  window.clearTimeout(attachmentStatusTimer)
  attachmentStatus.value = message
  if (timeout > 0) {
    attachmentStatusTimer = window.setTimeout(() => { attachmentStatus.value = '' }, timeout)
  }
}

function inferredImageType(file: File) {
  if (file.type.startsWith('image/')) return file.type
  const extension = file.name.split('.').pop()?.toLowerCase()
  if (extension === 'jpg' || extension === 'jpeg') return 'image/jpeg'
  if (extension === 'webp') return 'image/webp'
  if (extension === 'png') return 'image/png'
  return ''
}

function isImageFile(file: File | null | undefined, itemType = ''): file is File {
  if (!file) return false
  return Boolean(inferredImageType(file) || itemType.startsWith('image/'))
}

function withSafeImageMetadata(file: File, index: number) {
  const mimeType = inferredImageType(file) || 'image/png'
  if (file.type === mimeType && file.name) return file
  const extension = mimeType === 'image/jpeg' ? 'jpg' : mimeType.split('/')[1] || 'png'
  return new File([file], file.name || `clipboard-${index + 1}.${extension}`, {
    type: mimeType,
    lastModified: file.lastModified || Date.now(),
  })
}

async function normalizeImageForUpload(file: File, index: number) {
  const safeFile = withSafeImageMetadata(file, index)
  if (typeof createImageBitmap !== 'function') return safeFile
  let bitmap: ImageBitmap | null = null
  try {
    bitmap = await createImageBitmap(safeFile)
    if (!bitmap.width || !bitmap.height || bitmap.width * bitmap.height > 40_000_000) {
      throw new Error('图片尺寸不合法')
    }
    const canvas = document.createElement('canvas')
    canvas.width = bitmap.width
    canvas.height = bitmap.height
    const context = canvas.getContext('2d')
    if (!context) throw new Error('浏览器无法处理剪贴板图片')
    context.drawImage(bitmap, 0, 0)
    const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, 'image/png'))
    if (!blob) throw new Error('浏览器无法转换剪贴板图片')
    return new File([blob], `clipboard-${Date.now()}-${index + 1}.png`, {
      type: 'image/png',
      lastModified: Date.now(),
    })
  } finally {
    bitmap?.close()
  }
}

async function uploadFiles(files: File[], source: 'picker' | 'paste' | 'drop' = 'picker') {
  const room = 4 - pendingAttachments.value.length
  const selected = files.slice(0, room)
  if (!selected.length) return
  if (files.length > room) Message.warning('每条消息最多添加 4 张图片')
  uploading.value = true
  showAttachmentStatus(source === 'paste' ? '正在处理粘贴的图片…' : '正在处理图片…', 0)
  let uploaded = 0
  const failures: string[] = []
  try {
    for (const [index, file] of selected.entries()) {
      try {
        const normalized = await normalizeImageForUpload(file, index)
        const asset: any = await hallApi.uploadAiChatAttachment(normalized)
        if (!pendingAttachments.value.some(item => item.id === asset.id)) {
          pendingAttachments.value.push(asset)
          uploaded += 1
        }
      } catch (error: any) {
        failures.push(error?._message || error?.message || '图片上传失败')
      }
    }
  } finally {
    uploading.value = false
    if (uploaded) showAttachmentStatus(`已添加 ${uploaded} 张图片`)
    else attachmentStatus.value = ''
    if (failures.length) Message.error(failures[0])
  }
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  uploadFiles(Array.from(input.files || []), 'picker')
  input.value = ''
}

function onDrop(event: DragEvent) {
  dragging.value = false
  if (!visionReady.value) return Message.warning('图片识别模型尚未通过验证')
  uploadFiles(Array.from(event.dataTransfer?.files || []).filter(file => isImageFile(file)), 'drop')
}

function focusComposer() {
  nextTick(() => document.querySelector<HTMLTextAreaElement>('.chat-input textarea')?.focus())
}

async function onPaste(event: ClipboardEvent) {
  if (handledPasteEvents.has(event)) return
  handledPasteEvents.add(event)
  const clipboard = event.clipboardData
  if (!clipboard) return
  // Chrome can expose the same screenshot in both DataTransfer.items and
  // DataTransfer.files, sometimes with different names or timestamps. Prefer
  // items as the canonical source and only fall back to files when needed so
  // one paste event can never enqueue the mirrored image twice.
  const itemImages = Array.from(clipboard.items || [])
    .filter(item => item.kind === 'file')
    .map(item => ({ file: item.getAsFile(), itemType: item.type }))
    .filter((item): item is { file: File; itemType: string } => isImageFile(item.file, item.itemType))
    .map(item => item.file)
  const imageFiles = itemImages.length
    ? itemImages
    : Array.from(clipboard.files || []).filter(file => isImageFile(file))
  if (imageFiles.length) {
    event.preventDefault()
    if (!visionReady.value) {
      Message.warning('图片识别尚未通过生产验证，暂时只能粘贴文字')
      return
    }
    const fingerprint = imageFiles.map(file => `${file.type}:${file.size}:${file.lastModified}:${file.name}`).join('|')
    const now = Date.now()
    if (fingerprint && fingerprint === recentPasteFingerprint && now - recentPasteAt < 900) return
    recentPasteFingerprint = fingerprint
    recentPasteAt = now
    try {
      await uploadFiles(imageFiles, 'paste')
    } catch (error: any) {
      attachmentStatus.value = ''
      Message.error(error?.message || '剪贴板图片无法读取，请改用 PNG、JPG 或 WebP 文件上传')
    }
    return
  }
  const target = event.target as HTMLElement | null
  if (target?.matches('textarea, input, [contenteditable="true"]')) return
  const text = clipboard.getData('text/plain')
  if (!text) return
  event.preventDefault()
  const separator = draft.value && !draft.value.endsWith('\n') ? '\n' : ''
  draft.value = `${draft.value}${separator}${text}`.slice(0, 32000)
  focusComposer()
}

function removeAttachment(id: string) {
  pendingAttachments.value = pendingAttachments.value.filter(item => item.id !== id)
}

function onInputKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    if (canSend.value) sendMessage()
  }
}

function makeIdempotencyKey() {
  if (globalThis.crypto?.randomUUID) return `chat-${globalThis.crypto.randomUUID()}`
  return `chat-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

async function readEventStream(response: Response, onEvent: (event: any) => void) {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body?.detail || body?.error?.message || `请求失败 (${response.status})`)
  }
  const reader = response.body?.getReader()
  if (!reader) throw new Error('浏览器不支持流式响应')
  const decoder = new TextDecoder()
  let buffer = ''
  let terminalReceived = false
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const blocks = buffer.split(/\r?\n\r?\n/)
    buffer = blocks.pop() || ''
    for (const block of blocks) {
      const dataLine = block.split(/\r?\n/).find(line => line.startsWith('data:'))
      if (!dataLine) continue
      try {
        const event = JSON.parse(dataLine.slice(5).trim())
        if (['message.completed', 'message.interrupted', 'message.failed'].includes(event?.type)) terminalReceived = true
        onEvent(event)
      } catch { /* ignore malformed upstream event */ }
    }
    if (done) break
  }
  return terminalReceived
}

function handleStreamEvent(event: any, assistant: ChatMessage) {
  if (event.type === 'message.started') {
    const started = event.message || {}
    assistant.id = started.id || assistant.id
    assistant.thread_id = started.thread_id || assistant.thread_id
    assistant.model = started.model || assistant.model
    activeAssistantId.value = assistant.id
  } else if (event.type === 'vision.completed') {
    assistant.vision_model = event.vision_model
  } else if (event.type === 'message.delta') {
    assistant.content += event.delta || ''
    scrollToBottom()
  } else if (['message.completed', 'message.interrupted', 'message.failed'].includes(event.type)) {
    Object.assign(assistant, event.message || {})
    if (event.error?.message && !assistant.error_message) assistant.error_message = event.error.message
  }
}

async function runStream(url: string, body: Record<string, unknown>, assistant: ChatMessage) {
  const controller = new AbortController()
  activeAbort.value = controller
  const response = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: controller.signal,
  })
  const terminalReceived = await readEventStream(response, event => handleStreamEvent(event, assistant))
  if (terminalReceived) return

  // A proxy/browser can close SSE without delivering its terminal event. Ask
  // the durable API once before showing a recoverable interruption locally.
  if (assistant.thread_id && !assistant.id.startsWith('local-')) {
    await new Promise(resolve => window.setTimeout(resolve, 250))
    const history: any = await hallApi.aiChatMessages(assistant.thread_id, { limit: 50 }).catch(() => null)
    const persisted = (history?.items || []).find((item: ChatMessage) => item.id === assistant.id)
    if (persisted) Object.assign(assistant, persisted)
    if (persisted && ['completed', 'interrupted', 'failed'].includes(persisted.status)) return
  }
  assistant.status = assistant.content ? 'interrupted' : 'failed'
  assistant.error_message = '生成连接提前结束，可重新生成'
  throw new Error(assistant.error_message)
}

async function sendMessage() {
  if (!canSend.value) return
  const text = draft.value.trim()
  const assets = [...pendingAttachments.value]
  const thread = await ensureThread()
  const userMessage: ChatMessage = {
    id: `local-user-${Date.now()}`,
    thread_id: thread.id,
    role: 'user',
    content: text,
    status: 'completed',
    model: selectedModel.value,
    reasoning_effort: reasoningEffort.value,
    attachments: assets,
  }
  const assistant: ChatMessage = {
    id: `local-assistant-${Date.now()}`,
    thread_id: thread.id,
    role: 'assistant',
    content: '',
    status: 'streaming',
    model: selectedModel.value,
    reasoning_effort: reasoningEffort.value,
    attachments: [],
  }
  messages.value.push(userMessage, assistant)
  draft.value = ''
  pendingAttachments.value = []
  running.value = true
  await scrollToBottom()
  try {
    await runStream(`/api/hall/ai-chat/threads/${encodeURIComponent(thread.id)}/messages/stream`, {
      content: text,
      model: selectedModel.value,
      reasoning_effort: reasoningEffort.value,
      attachment_ids: assets.map(item => item.id),
      idempotency_key: makeIdempotencyKey(),
    }, assistant)
  } catch (error: any) {
    if (error?.name !== 'AbortError') {
      if (assistant.status === 'streaming') assistant.status = 'failed'
      const errorMessage = String(error?.message || '生成失败')
      assistant.error_message ||= errorMessage
      Message.error(errorMessage)
    }
  } finally {
    running.value = false
    activeAssistantId.value = ''
    activeAbort.value = null
    await loadThreads(false)
    cacheCurrentThread()
  }
}

async function stopGeneration() {
  if (activeAssistantId.value) {
    await hallApi.cancelAiChatMessage(activeAssistantId.value).catch(() => undefined)
  }
  activeAbort.value?.abort()
  running.value = false
}

async function regenerate(message: ChatMessage) {
  if (!message.id || message.id.startsWith('local-') || running.value) return
  const assistant: ChatMessage = {
    id: `local-assistant-${Date.now()}`,
    thread_id: message.thread_id,
    role: 'assistant',
    content: '',
    status: 'streaming',
    model: selectedModel.value,
    reasoning_effort: reasoningEffort.value,
    attachments: [],
  }
  const index = messages.value.findIndex(item => item.id === message.id)
  messages.value.splice(index + 1, 0, assistant)
  running.value = true
  await scrollToBottom()
  try {
    await runStream(`/api/hall/ai-chat/messages/${encodeURIComponent(message.id)}/regenerate`, {
      model: selectedModel.value,
      reasoning_effort: reasoningEffort.value,
    }, assistant)
  } catch (error: any) {
    if (error?.name !== 'AbortError') {
      if (assistant.status === 'streaming') assistant.status = 'failed'
      assistant.error_message ||= error?.message || '重新生成失败'
    }
  } finally {
    running.value = false
    activeAssistantId.value = ''
    activeAbort.value = null
    await loadThreads(false)
  }
}

async function copyMessage(message: ChatMessage) {
  await navigator.clipboard.writeText(message.content || '')
  Message.success('已复制')
}

function beginRename() {
  if (!activeThread.value) return
  renameDraft.value = activeThread.value.title
  renaming.value = true
  nextTick(() => titleInputRef.value?.focus?.())
}

async function saveRename() {
  if (!renaming.value || !activeThread.value) return
  renaming.value = false
  const title = renameDraft.value.trim()
  if (!title || title === activeThread.value.title) return
  const updated: any = await hallApi.updateAiChatThread(activeThread.value.id, { title })
  activeThread.value = updated
  const index = threads.value.findIndex(item => item.id === updated.id)
  if (index >= 0) threads.value[index] = updated
}

async function toggleArchive() {
  if (!activeThread.value) return
  const target = !activeThread.value.archived
  await hallApi.updateAiChatThread(activeThread.value.id, { archived: target })
  threadCache.delete(activeThread.value.id)
  activeThread.value = null
  messages.value = []
  await loadThreads(false)
  Message.success(target ? '已归档，历史不会删除' : '已恢复')
}

function requestDeleteThread() {
  const thread = activeThread.value
  if (!thread) return
  confirmDelete(thread.title, async () => {
    if (running.value) await stopGeneration()
    await hallApi.deleteAiChatThread(thread.id)
    threadCache.delete(thread.id)
    threads.value = threads.value.filter(item => item.id !== thread.id)
    activeThread.value = null
    messages.value = []
    messagesCursor.value = null
    messagesHasMore.value = false
    Message.success('对话已删除')
  }, { content: '该对话及其中的消息和图片将永久删除，无法恢复。' })
}

onMounted(async () => {
  window.addEventListener('paste', onPaste, true)
  await Promise.all([loadModels(), loadThreads(false)])
})

onBeforeUnmount(() => {
  window.clearTimeout(searchTimer)
  window.clearTimeout(attachmentStatusTimer)
  window.removeEventListener('paste', onPaste, true)
  activeAbort.value?.abort()
})
</script>

<style scoped>
.ai-chat-page { width: 100%; max-width: 100vw; height: 100%; min-height: 0; display: grid; grid-template-columns: 248px minmax(0, 1fr); background: #fff; color: #1f1f1f; overflow: hidden; }
.thread-sidebar { display: flex; min-width: 0; max-width: 248px; flex-direction: column; border-right: 1px solid #ececee; background: #f7f7f8; overflow: hidden; }
.sidebar-head { display: flex; align-items: center; gap: 8px; padding: 12px 10px 8px; }
.new-thread { min-width: 0; height: 38px; flex: 1; justify-content: flex-start; border-radius: 9px; color: #222; font-size: 14px; font-weight: 500; }
.new-thread:hover { background: #ececef !important; }
.mobile-close, .mobile-menu { display: none; }
.thread-search { padding: 0 10px 8px; }
.thread-search :deep(.arco-input-wrapper) { min-height: 36px; border: 0; border-radius: 9px; background: #ededf0; box-shadow: none; }
.history-tabs { display: flex; gap: 2px; padding: 0 10px 8px; }
.history-tabs button { flex: 0 0 auto; border: 0; border-radius: 8px; padding: 6px 10px; color: #777; background: transparent; font-size: 12px; cursor: pointer; }
.history-tabs button.active { color: #111; background: #e9e9ec; font-weight: 600; }
.thread-list { min-width: 0; min-height: 0; flex: 1; overflow-x: hidden; overflow-y: auto; padding: 2px 7px 16px; }
.thread-item { display: block; box-sizing: border-box; width: 100%; min-width: 0; max-width: 100%; padding: 9px 10px; margin-bottom: 1px; overflow: hidden; border: 0; border-radius: 9px; text-align: left; background: transparent; cursor: pointer; }
.thread-item:hover { background: #ededf0; }
.thread-item.active { background: #e5e5e8; }
.thread-title { display: block; overflow: hidden; color: #232323; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.thread-meta { display: flex; justify-content: space-between; margin-top: 3px; color: #999; font-size: 10px; }
.list-state, .load-more { width: 100%; padding: 14px; border: 0; color: #888; text-align: center; background: transparent; }
.load-more { cursor: pointer; }
.sidebar-foot { padding: 10px 12px 12px; border-top: 1px solid #e7e7e9; color: #8d8d91; font-size: 10px; }
.retention-dot { display: inline-block; width: 6px; height: 6px; margin-right: 6px; border-radius: 50%; background: #32b36b; }
.chat-main { min-width: 0; min-height: 0; height: 100%; display: grid; grid-template-rows: 52px minmax(0, 1fr) auto; overflow: hidden; background: #fff; }
.chat-head { display: flex; align-items: center; padding: 0 18px; border-bottom: 1px solid #f0f0f1; background: rgba(255,255,255,.96); }
.title-wrap { min-width: 0; flex: 1; display: flex; align-items: center; gap: 10px; }
.chat-title { max-width: 560px; min-width: 0; display: flex; align-items: center; gap: 6px; overflow: hidden; border: 0; color: #222; font-weight: 600; background: transparent; cursor: text; }
.chat-title span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.title-edit-icon { flex: 0 0 auto; color: #999; font-size: 13px; }
.title-hint { color: #aaa; font-size: 11px; opacity: 0; transition: opacity .15s; }
.title-wrap:hover .title-hint { opacity: 1; }
.head-actions { display: flex; align-items: center; gap: 4px; }
.head-more { width: 34px; height: 34px; color: #555; }
.danger-option { color: #c93643 !important; }
.message-viewport { min-height: 0; overflow-y: auto; overscroll-behavior: contain; }
.message-column { width: min(900px, calc(100% - 48px)); min-height: 100%; margin: 0 auto; padding: 28px 0 46px; }
.older-wrap { display: flex; justify-content: center; min-height: 36px; }
.welcome { display: flex; min-height: calc(100vh - 320px); flex-direction: column; align-items: center; justify-content: center; text-align: center; }
.welcome-logo { width: 44px; height: 44px; display: grid; place-items: center; border-radius: 14px; background: #171717; color: #fff; font-size: 15px; font-weight: 700; }
.welcome h1 { margin: 16px 0 7px; font-size: 25px; letter-spacing: -.4px; }
.welcome p { margin: 0; color: #777; }
.starter-grid { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; width: min(650px, 100%); margin-top: 22px; }
.starter-grid button { min-height: 38px; padding: 8px 14px; border: 1px solid #e6e6e8; border-radius: 999px; color: #555; background: #fff; cursor: pointer; }
.starter-grid button:hover { border-color: #bbb; color: #111; }
.message-row { display: grid; grid-template-columns: 30px minmax(0, 1fr); gap: 14px; margin: 0 0 30px; content-visibility: auto; contain-intrinsic-size: auto 120px; }
.message-avatar { width: 30px; height: 30px; display: grid; place-items: center; border-radius: 9px; background: #111; color: #fff; font-size: 11px; font-weight: 700; }
.message-row.user .message-avatar { background: #e9e9eb; color: #555; }
.message-content { min-width: 0; padding-top: 4px; line-height: 1.75; }
.user-text { white-space: pre-wrap; word-break: break-word; }
.markdown-body { overflow-wrap: anywhere; }
.markdown-body :deep(pre) { max-width: 100%; overflow: auto; padding: 14px; border-radius: 10px; background: #171717; color: #eee; }
.markdown-body :deep(table) { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; }
.markdown-body :deep(th), .markdown-body :deep(td) { padding: 7px 10px; border: 1px solid #ddd; }
.message-images { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.message-images img { width: 128px; height: 128px; border-radius: 12px; object-fit: cover; background: #eee; }
.message-foot { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; min-height: 28px; margin-top: 8px; color: #929292; font-size: 11px; }
.model-badge, .vision-badge, .reasoning-badge { padding: 2px 7px; border-radius: 999px; background: #f0f1f3; }
.vision-badge { background: #edf6ff; color: #2870b8; }
.reasoning-badge { background: #f3efff; color: #6741b7; }
.message-error { margin-top: 8px; padding: 8px 10px; border-radius: 8px; color: #b4232d; background: #fff1f2; font-size: 13px; }
.composer-shell { min-width: 0; padding: 0 20px 12px; background: linear-gradient(180deg, rgba(255,255,255,0), #fff 18%); }
.composer-card { box-sizing: border-box; width: min(900px, 100%); margin: 0 auto; padding: 10px 12px 9px; border: 1px solid #dedee1; border-radius: 26px; background: #fff; box-shadow: 0 3px 14px rgba(0,0,0,.08); transition: border-color .15s, box-shadow .15s; }
.composer-card:focus-within, .composer-card.dragover { border-color: #a7a7ab; box-shadow: 0 5px 20px rgba(0,0,0,.1); }
.composer-card.uploading { border-color: #9c9ca2; }
.chat-input :deep(textarea) { padding: 6px 5px 10px; border: 0 !important; box-shadow: none !important; resize: none; font-size: 15px; line-height: 1.55; }
.attachment-status { display: flex; align-items: center; gap: 6px; margin: 0 5px 4px; color: #6e6e73; font-size: 11px; }
.composer-actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.composer-left { min-width: 0; display: flex; align-items: center; gap: 5px; }
.attach-button { width: 34px; height: 34px; flex: 0 0 auto; color: #303033; border: 0; background: transparent; }
.attach-button:hover { background: #f0f0f2 !important; }
.attach-plus { display: block; font-family: Arial, sans-serif; font-size: 22px; font-weight: 400; line-height: 1; transform: translateY(-1px); }
.model-status { width: 7px; height: 7px; flex: 0 0 auto; border-radius: 50%; background: #aaa; }
.model-status.available { background: #28a766; }
.model-status.unverified { background: #e7a11a; }
.model-status.unavailable { background: #e45a65; }
.model-trigger { max-width: 210px; height: 34px; display: flex; align-items: center; gap: 7px; padding: 0 10px; border: 0; border-radius: 999px; color: #38383c; background: #f1f1f3; cursor: pointer; }
.model-trigger:hover { background: #eaeaed; }
.model-trigger-name { min-width: 0; overflow: hidden; font-size: 12px; line-height: 16px; text-overflow: ellipsis; white-space: nowrap; }
.model-trigger-arrow { flex: 0 0 auto; color: #888; }
.model-group-label { min-height: 28px; color: #8b8b90 !important; font-size: 11px; font-weight: 600; opacity: 1 !important; }
.model-option { min-width: 286px; }
.model-option :deep(.arco-dropdown-option-content) { width: 100%; display: flex; justify-content: space-between; gap: 18px; }
.model-option-copy { min-width: 0; display: flex; flex-direction: column; }
.model-option-title { display: flex; align-items: center; gap: 7px; }
.modality-badge { padding: 1px 6px; border-radius: 999px; color: #77777c; background: #f0f0f2; font-size: 9px; line-height: 16px; font-weight: 500; }
.modality-badge.vision { color: #246b50; background: #e9f6ef; }
.model-option-copy small { margin-top: 2px; color: #929298; font-size: 10px; font-weight: 400; }
.model-current { color: #168653; font-size: 11px; }
.reasoning-trigger { height: 34px; display: flex; align-items: center; gap: 5px; padding: 0 10px; border: 0; border-radius: 999px; color: #55555a; background: transparent; font-size: 12px; cursor: pointer; }
.reasoning-trigger:hover { background: #f1f1f3; }
.reasoning-menu-label { color: #8b8b90 !important; font-size: 11px; font-weight: 600; opacity: 1 !important; }
.reasoning-option :deep(.arco-dropdown-option-content) { min-width: 150px; display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.vision-indicator { width: 30px; height: 30px; display: grid; place-items: center; border-radius: 50%; color: #747479; }
.vision-indicator.unavailable { color: #b26a00; background: #fff7e6; }
.send-button { width: 36px; min-width: 36px; height: 36px; flex: 0 0 auto; color: #fff; background: #171717; border-color: #171717; }
.send-button:not(.arco-btn-disabled):hover { background: #303033; border-color: #303033; }
.send-button.arco-btn-disabled { color: #8a8a8e !important; background: #e7e7e9 !important; border-color: #e7e7e9 !important; }
.send-arrow-icon { width: 19px; height: 19px; display: block; fill: none; stroke: currentColor; stroke-width: 1.9; stroke-linecap: round; stroke-linejoin: round; }
.send-button.stop { color: #fff; }
.composer-note { margin-top: 6px; color: #a0a0a4; font-size: 10px; text-align: center; }
.pending-images { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 7px; }
.pending-image { position: relative; flex: 0 0 auto; }
.pending-image img { width: 58px; height: 58px; border-radius: 10px; object-fit: cover; }
.pending-image button { position: absolute; top: -5px; right: -5px; width: 19px; height: 19px; padding: 0; border: 1px solid #ddd; border-radius: 50%; background: #fff; cursor: pointer; }
.sidebar-mask { display: none; }
@media (max-width: 900px) {
  .ai-chat-page { grid-template-columns: 1fr; }
  .thread-sidebar { position: fixed; z-index: 30; inset: 56px auto 0 0; width: min(86vw, 310px); max-width: none; transform: translateX(-105%); transition: transform .2s; box-shadow: 8px 0 30px rgba(0,0,0,.16); }
  .thread-sidebar.open { transform: translateX(0); }
  .mobile-close, .mobile-menu { display: inline-flex; }
  .sidebar-mask { position: fixed; z-index: 29; inset: 56px 0 0; display: block; background: rgba(0,0,0,.28); }
  .title-hint { display: none; }
  .chat-head { padding: 0 10px; }
  .message-column { width: calc(100% - 24px); }
  .composer-shell { padding-inline: 10px; }
  .starter-grid { grid-template-columns: 1fr; }
  .model-trigger { min-width: 0; max-width: 150px; }
  .vision-indicator { display: none; }
}
</style>
