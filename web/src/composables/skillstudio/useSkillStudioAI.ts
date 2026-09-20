import { onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { Message, Modal, Notification } from '@arco-design/web-vue'
import { CodingAgentStreamClient, type StreamSubscriber } from '@/api/stream'
import type { CodingPermissionRequestPayload } from '@/types/coding-agent'
import type { StructuredSkillResponse } from '@/types/skill'
import type {
  ChatBudget,
  ChatOptions,
  CodingSessionMeta,
  FileChangeTarget,
  SkillStudioAiOptions,
  WorkbenchChatMessage,
} from '@/types/skillstudio'
import { useRunningTasksStore } from '@/stores/runningTasks'
import {
  getErrorMessage,
  normalizeConversationList,
  normalizeConversationMessages,
} from '@/types/skillstudio'
import { logger } from '@/utils/logger'

const VALID_MODULES = new Set(['meta', 'goal', 'rules', 'params', 'output_table', 'todos', 'test_cases', 'workflow'])

/**
 * 每个用户最多保留的 coding client 连接数。超出时 LRU 淘汰最旧的一个。
 * 实际使用中用户很少同时操作 >3 个 skill，3 足够；再多会吃 WS 连接额度。
 */
const MAX_CLIENTS = 3
const DISCONNECT_AUTO_RESUME_DELAY_MS = 800
const RESUME_SILENCE_TIMEOUT_MS = 13 * 60 * 1000

type ChatContext = {
  active_module: string | null
  draft_revision: number
  selection?: {
    module_id: string
    start_line: number | null
    end_line: number | null
    text: string
  }
  draft_snapshot?: Record<string, unknown>
}

type CodingTimelineItem = {
  role?: string
  content?: string
  intent?: Record<string, unknown> | null
  module_id?: string
  turn_id?: string | null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function stringifyToolResult(value: unknown): string {
  if (typeof value === 'string') return value
  if (isRecord(value)) {
    const detail = value.error || value.message || value.stderr || value.detail
    if (detail) return String(detail)
  }
  try { return JSON.stringify(value) } catch { return String(value) }
}

function humanizeCodingError(code?: string, error?: string): string {
  const raw = String(error || '')
  const lower = raw.toLowerCase()
  if (code === 'RESUME_NO_SESSION') return '会话已过期，上次生成未完成。请重新发送消息继续。'
  if (code === 'CODING_AGENT_SKILL_DIR_NOT_FOUND') return 'Skill 工作目录不存在或已被清理，请刷新页面后重试。'
  if (lower.includes('no such file') || lower.includes('not found') || lower.includes('file not found') || lower.includes('不存在')) {
    return '文件不存在或已被移动，请刷新文件列表后重试。'
  }
  if (lower.includes('/tmp/') || lower.includes('scratch') || lower.includes('temporary') || lower.includes('临时')) {
    return '临时工作目录已过期，请重新发起这次操作。'
  }
  if (code === 'CODING_AGENT_INIT_TIMEOUT' || code === 'CODING_AGENT_SPAWN_FAILED') {
    return 'AI 助手未就绪，请检查后台配置。'
  }
  if (code === 'CODING_AGENT_PERMISSION_VIOLATION') return `权限被拒${raw ? `：${raw}` : ''}`
  if (code === 'CODING_AGENT_TURN_INACTIVITY_TIMEOUT') return raw || 'AI 会话长时间无响应，已自动终止。请重新发送消息。'
  return `AI 错误 [${code || 'unknown'}]: ${raw || '未知'}`
}

function normalizeSkillFilePath(rawPath: unknown, skillId: string): string {
  const text = String(rawPath || '').replace(/\\/g, '/').trim()
  if (!text) return ''
  const skillMarker = skillId ? `/skills-repo/${skillId}/` : ''
  const markerIndex = skillMarker ? text.indexOf(skillMarker) : -1
  if (markerIndex >= 0) {
    return text.slice(markerIndex + skillMarker.length).replace(/^\/+/, '')
  }
  if (skillId && text.startsWith(`${skillId}/`)) {
    return text.slice(skillId.length + 1).replace(/^\/+/, '')
  }
  return text.replace(/^\.\/+/, '')
}

function isUsableSkillFilePath(path: string): boolean {
  if (!path) return false
  if (path.startsWith('/')) return false
  if (/^[a-zA-Z]:\//.test(path)) return false
  return !path.split('/').includes('..')
}

export function useSkillStudioAI(options: SkillStudioAiOptions) {
  const {
    wb,
    studioDoc,
    ui,
    skillApi,
    testApi,
    workbenchApi,
    chatStarted,
    studioMode,
    pendingPatch,
    loadFileList,
    handleSelectFile,
    loadHistory,
    loadHealthScore,
  } = options

  const submitting = ref(false)
  const conversations = ref(normalizeConversationList(null))
  const activeConversationId = ref('')
  const chatBudget = ref<ChatBudget>({ used: 0, limit: 0, status: 'ok' })
  const pendingPermissionRequest = ref<CodingPermissionRequestPayload | null>(null)
  const codingSessionMeta = ref<CodingSessionMeta | null>(null)
  const trackingMode = ref(true)

  /**
   * 多 skill 并行时，每个 skillId 一个 client（避免切换 skill 丢消息 / 相互覆盖）。
   * LRU 通过 `lastUsedAt` 排序，超过 MAX_CLIENTS 时淘汰最旧的一个。
   */
  const codingClientsBySkill = new Map<string, CodingAgentStreamClient>()
  let refreshSkillTimer: ReturnType<typeof setTimeout> | null = null

  const runningTasks = useRunningTasksStore()

  function getOrCreateCodingClient(skillId: string): CodingAgentStreamClient {
    let client = codingClientsBySkill.get(skillId)
    if (!client) {
      client = new CodingAgentStreamClient(skillId)
      codingClientsBySkill.set(skillId, client)
      enforceLruLimit()
    }
    client.lastUsedAt = Date.now()
    return client
  }

  function enforceLruLimit() {
    if (codingClientsBySkill.size <= MAX_CLIENTS) return
    // 按 lastUsedAt 升序排序，淘汰最旧的，但跳过仍有活跃 subscribers 的 client
    const sorted = Array.from(codingClientsBySkill.entries()).sort(
      (a, b) => a[1].lastUsedAt - b[1].lastUsedAt,
    )
    for (const [skillId, client] of sorted) {
      if (codingClientsBySkill.size <= MAX_CLIENTS) break
      if (client.hasSubscribers()) continue // 还在流式，不能关
      try { client.close() } catch { /* ignore */ }
      codingClientsBySkill.delete(skillId)
    }
  }

  function closeCodingClient(skillId: string) {
    const client = codingClientsBySkill.get(skillId)
    if (!client) return
    try { client.close() } catch { /* ignore */ }
    codingClientsBySkill.delete(skillId)
  }

  function startChatMode() {
    chatStarted.value = true
    ui.assistantPaneOpen = true
    loadConversations()
    loadCodingTimelineIfEmpty()
  }

  function normalizeCodingTimeline(payload: unknown): CodingTimelineItem[] {
    if (Array.isArray(payload)) return payload.filter(isRecord) as CodingTimelineItem[]
    if (isRecord(payload) && Array.isArray(payload.items)) {
      return payload.items.filter(isRecord) as CodingTimelineItem[]
    }
    return []
  }

  function ensureTimelineAssistant(messages: WorkbenchChatMessage[]): WorkbenchChatMessage {
    const last = messages[messages.length - 1]
    if (last?.role === 'assistant') return last
    const msg: WorkbenchChatMessage = { role: 'assistant', content: '', toolCalls: [], fileChanges: [] }
    messages.push(msg)
    return msg
  }

  function hydrateMessagesFromCodingTimeline(items: CodingTimelineItem[], skillId: string): WorkbenchChatMessage[] {
    const messages: WorkbenchChatMessage[] = []
    for (const item of items) {
      const role = String(item.role || '')
      const event = isRecord(item.intent) ? item.intent : {}
      const eventType = String(event.type || '')
      const content = String(item.content || '')

      if (role === 'user') {
        messages.push({ role: 'user', content, module_id: item.module_id })
        continue
      }

      if (role === 'assistant') {
        const assistant = ensureTimelineAssistant(messages)
        assistant.content = assistant.content
          ? `${assistant.content}\n\n${content}`.trim()
          : content
        continue
      }

      if (eventType === 'tool_call' || content.startsWith('tool_call:')) {
        const assistant = ensureTimelineAssistant(messages)
        assistant.toolCalls = assistant.toolCalls || []
        const id = String(event.id || event.tool_call_id || `tool-${assistant.toolCalls.length + 1}`)
        if (!assistant.toolCalls.find((call) => call.id === id)) {
          assistant.toolCalls.push({
            id,
            tool: String(event.tool || content.replace(/^tool_call:/, '') || 'tool'),
            input: isRecord(event.input) ? event.input : {},
            decision: typeof event.decision === 'string' ? event.decision : undefined,
            decision_reason: typeof event.decision_reason === 'string' ? event.decision_reason : undefined,
            status: 'pending',
          })
        }
        continue
      }

      if (eventType === 'tool_result' || content.startsWith('tool_result:')) {
        const assistant = ensureTimelineAssistant(messages)
        assistant.toolCalls = assistant.toolCalls || []
        const id = String(event.id || event.tool_call_id || '')
        let toolCall = id ? assistant.toolCalls.find((call) => call.id === id) : undefined
        if (!toolCall) {
          toolCall = {
            id: id || `result-${assistant.toolCalls.length + 1}`,
            tool: 'tool',
            input: {},
            status: 'pending',
          }
          assistant.toolCalls.push(toolCall)
        }
        toolCall.status = event.is_error ? 'error' : 'success'
        const result = event.content ?? event.output ?? content
        toolCall.result = stringifyToolResult(result)
        continue
      }

      if (eventType === 'file_change' || content.startsWith('file_change:')) {
        const assistant = ensureTimelineAssistant(messages)
        const normalizedPath = normalizeSkillFilePath(event.path || event.file_path || content.replace(/^file_change:/, ''), skillId)
        if (!isUsableSkillFilePath(normalizedPath)) continue
        assistant.fileChanges = assistant.fileChanges || []
        assistant.fileChanges.push({
          path: normalizedPath,
          operation: String(event.operation || 'edit'),
          old_string: event.old_string as string | undefined,
          new_string: event.new_string as string | undefined,
          snippet_preview: event.snippet_preview as string | undefined,
          edit_count: event.edit_count as number | undefined,
          edits_preview: event.edits_preview as unknown[] | undefined,
        })
        continue
      }

      if (eventType === 'error') {
        const assistant = ensureTimelineAssistant(messages)
        assistant.streaming = false
        assistant._streamErrorReason = humanizeCodingError(String(event.code || ''), String(event.error || content || 'AI 会话异常'))
        if (!assistant.content) assistant.content = `⚠️ ${assistant._streamErrorReason}`
        continue
      }

      if (eventType === 'git_commit') {
        const assistant = ensureTimelineAssistant(messages)
        assistant.gitCommit = String(event.git_commit || '')
        assistant.gitCommitFull = String(event.git_commit_full || event.git_commit || '')
        assistant.diffSummary = isRecord(event.diff_summary) ? event.diff_summary : null
        if (Array.isArray(event.changed_files)) {
          assistant.fileChanges = assistant.fileChanges || []
          for (const path of event.changed_files) {
            const normalizedPath = normalizeSkillFilePath(path, skillId)
            if (!isUsableSkillFilePath(normalizedPath)) continue
            if (!assistant.fileChanges.find((fc) => fc.path === normalizedPath)) {
              assistant.fileChanges.push({ path: normalizedPath, operation: 'edit' })
            }
          }
        }
        continue
      }

      if (eventType === 'done') {
        const assistant = messages[messages.length - 1]
        if (assistant?.role === 'assistant') assistant.streaming = false
      }
    }
    return messages.filter((msg) =>
      msg.role === 'user' ||
      msg.content ||
      msg.toolCalls?.length ||
      msg.fileChanges?.length,
    )
  }

  async function loadCodingTimelineIfEmpty() {
    if (!wb.skillId || (wb.messages?.length || 0) > 0) return
    try {
      const response = await workbenchApi.codingTimeline(wb.skillId, { limit: 80 })
      const messages = hydrateMessagesFromCodingTimeline(normalizeCodingTimeline(response), wb.skillId)
      if (messages.length && (wb.messages?.length || 0) === 0) {
        wb.setMessages?.(messages)
      }
    } catch (error) {
      console.warn('[coding-timeline] 加载失败', error)
    }
  }

  async function handleUploadReport(file: File) {
    if (!file) return
    submitting.value = true
    wb.appendMessage?.('user', `上传报告: ${file.name}`)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const response = await skillApi.generateFromReport(formData)
      if (response.skill_md || response.content) {
        wb.appendMessage?.('assistant', '已从报告生成 Skill 草稿。请在左侧模块中查看和编辑。')
        if (response.skill && typeof wb.setSkillDocument === 'function') {
          wb.setSkillDocument(response.skill)
        }
        studioDoc.setActiveModule('goal')
      } else {
        wb.appendMessage?.('assistant', response.message || '报告已处理，请继续编辑。')
      }
    } catch (error) {
      Message.error(getErrorMessage(error, '报告上传失败'))
    } finally {
      submitting.value = false
    }
  }

  async function loadConversations() {
    if (!wb.skillId) return
    try {
      const list = await testApi.listChats(wb.skillId)
      conversations.value = normalizeConversationList(list)
    } catch {
      // ignore conversation loading failures
    }
  }

  async function handleNewConversation() {
    if (!wb.skillId) return
    try {
      const response = await testApi.startChat(wb.skillId)
      const conversationId = response.conversation_id || response.id
      if (!conversationId) {
        throw new Error('缺少 conversation_id')
      }
      activeConversationId.value = conversationId
      wb.setMessages?.([])
      Message.success('新对话已创建')
      await loadConversations()
    } catch (error) {
      Message.error(getErrorMessage(error, '创建对话失败'))
    }
  }

  async function handleSwitchConversation(conversationId: string) {
    if (!conversationId) return
    activeConversationId.value = conversationId
    try {
      const response = await testApi.getConversation(conversationId)
      const messages = normalizeConversationMessages(response)
      wb.setMessages?.(messages)
      chatBudget.value = {
        used: response.budget_used || 0,
        limit: response.budget_limit || 0,
        status: response.budget_status || 'ok',
      }
    } catch (error) {
      Message.error(getErrorMessage(error, '加载对话失败'))
    }
  }

  async function handleSend(message: string, options: ChatOptions = {}) {
    wb.appendMessage?.('user', message)

    if (studioDoc.isCreate.value && !wb.skillId) {
      submitting.value = true
      try {
        const draft = await workbenchApi.generateDraft({ message, mode: 'novice', references: wb.selectedReferences || [] })
        if (draft.skill) wb.setSkillDocument?.(draft.skill)
        wb.appendMessage?.('assistant', `草稿已生成（${draft.source === 'ai' ? 'AI' : '模板'}）。左侧各模块已填充，可以直接编辑或继续对话调整。`)
        studioDoc.setActiveModule('goal')
      } catch (error) {
        Message.error(getErrorMessage(error, '生成草稿失败'))
      } finally {
        submitting.value = false
      }
      return
    }

    if (!wb.hasSession && wb.skillId) {
      try {
        const session = await workbenchApi.createSession(wb.skillId, {
          mode: 'pro',
          active_module: ui.studioActiveModule && VALID_MODULES.has(ui.studioActiveModule) ? ui.studioActiveModule : null,
        })
        wb.setSession?.(session)
      } catch (error) {
        console.warn('[workbench] session 创建失败，降级到无 session 模式', error)
      }
    }

    submitting.value = true

    if (options.isCommand && message.startsWith('/')) {
      const command = message.slice(1).trim().split(/\s+/)[0]
      try {
        const result = await workbenchApi.command(wb.skillId, {
          session_id: wb.sessionId,
          command,
          context: buildChatContext(options),
        })
        wb.appendMessage?.('assistant', result.message)
        if (result.type === 'patch' && result.patch) {
          pendingPatch.value = result.patch
        }
      } catch (error) {
        Message.error(getErrorMessage(error, `命令执行失败: /${command}`))
      } finally {
        submitting.value = false
      }
      return
    }

    try {
      await sendViaStream(message, options)
    } catch (error) {
      console.warn('[stream] 流式调用失败，降级为同步：', error)
      const placeholder = wb.messages?.[wb.messages.length - 1]
      const reusePlaceholder = placeholder && placeholder.role === 'assistant' && !placeholder.content
      try {
        const result = await workbenchApi.chat(wb.skillId, {
          session_id: wb.sessionId,
          message,
          context: buildChatContext(options),
          intent: options.intent || null,
          reference_ids: wb.selectedReferenceIds || [],
        })
        if (reusePlaceholder) {
          placeholder.content = result.message
          placeholder.streaming = false
        } else {
          wb.appendMessage?.('assistant', result.message)
        }
        if (result.type === 'patch' && result.patch) {
          pendingPatch.value = result.patch
          const targetModule = result.patch.target_module
          if (targetModule) {
            wb.setActiveModule?.(targetModule)
            studioDoc.setActiveModule(targetModule)
          }
        }
      } catch (fallbackError) {
        if (reusePlaceholder) {
          wb.messages.pop()
        }
        Message.error(getErrorMessage(fallbackError, '操作失败'))
      }
    } finally {
      submitting.value = false
    }
  }

  function stopGeneration() {
    // 中断当前 skill 的 client
    const client = codingClientsBySkill.get(wb.skillId)
    client?.interrupt()
    const last = wb.messages?.[wb.messages.length - 1]
    if (last && last.streaming) {
      last.streaming = false
    }
    submitting.value = false
  }

  function respondCodingPermission(behavior: string, opts: Record<string, unknown> = {}) {
    const client = codingClientsBySkill.get(wb.skillId)
    if (!pendingPermissionRequest.value || !client?.connected) return
    const request = pendingPermissionRequest.value
    try {
      const requestId = 'request_id' in request ? String(request.request_id || '') : ''
      client.respondPermission(requestId, behavior, opts)
    } catch (error) {
      Message.error(`响应权限请求失败: ${getErrorMessage(error, '未知错误')}`)
    } finally {
      pendingPermissionRequest.value = null
    }
  }

  async function handleJumpToFile(change: FileChangeTarget | null | undefined) {
    if (!change?.path) return
    const filePath = normalizeSkillFilePath(change.path, wb.skillId)
    if (!filePath) return
    const path = filePath.toLowerCase()
    let targetModule = 'overview'
    if (path.includes('policy') || path.endsWith('.yaml') || path.endsWith('.yml')) {
      targetModule = 'params'
    } else if (path.endsWith('skill.md') || path.endsWith('readme.md')) {
      targetModule = 'overview'
    } else if (path.includes('test')) {
      targetModule = 'test_cases'
    }
    try {
      if ('navigatorOpen' in ui) ui.navigatorOpen = true
      if ('navigatorSection' in ui) ui.navigatorSection = 'files'
      ui.setViewMode?.('code')
      await handleSelectFile(filePath)
      Message.info({ content: `已打开 ${filePath}`, duration: 1800 })
      return
    } catch (error) {
      console.warn('[tracking] openFile failed, fallback to module', error)
    }

    try {
      wb.setActiveModule?.(targetModule)
      studioDoc.setActiveModule?.(targetModule)
      Message.info({ content: `已定位到 ${filePath}`, duration: 1800 })
    } catch (error) {
      console.warn('[tracking] jumpToFile failed', error)
    }
  }

  async function sendViaStream(message: string, options: ChatOptions = {}) {
    // 1. 拿到（或创建）该 skill 的专属 client。client 复用很重要：
    //    用户切到别的 skill 再切回来时，如果上一次的流还没结束，
    //    我们必须让那个流继续（dispatcher 会发给原来的 subscriber）。
    const ownedSkillId = wb.skillId
    const client = getOrCreateCodingClient(ownedSkillId)

    // Dock 任务：使用稳定 refKey `chat:${skillId}`，这样 autoResume 或第二次 send
    // 都会复用同一条任务，不会在 dock 里留下"伪失败"的历史 chat 任务。
    const chatTaskId = runningTasks.startOrUpdate(`chat:${ownedSkillId}`, {
      kind: 'chat',
      title: 'AI 对话',
      skillId: ownedSkillId,
      skillName: studioDoc.doc.value?.meta?.name || ownedSkillId,
      message: (message || '').trim().slice(0, 40) || '生成中...',
      returnPath: `/skills/${ownedSkillId}?panel=assistant`,
    })

    // 2. 把 assistantMessage 作为对 wb.messages 中元素的**直接对象引用**占位。
    //    即使之后用户切换到别的 skill / 别的 conversation 导致 wb.messages
    //    被 setMessages 替换掉，这个闭包里的 assistantMessage 引用仍然指向
    //    同一个对象（它只是不在当前展示数组里了），回调继续 mutate 它不会出错。
    //    当用户切回时，如果对应的 conversation 依然引用这个 object，
    //    更新就会体现在 UI 上；否则这条消息就"孤立"了但不造成副作用。
    let assistantMessage: WorkbenchChatMessage = {
      role: 'assistant',
      content: '',
      streaming: true,
      _streamStartTs: Date.now(),
      _wasStreaming: true, // 标记为曾经流式，支持刷新后 resume 检测
      toolCalls: [],
      fileChanges: [],
    }
    wb.messages.push(assistantMessage)
    assistantMessage = wb.messages[wb.messages.length - 1]

    let buffer = ''
    let lastFlushAt = 0
    const flushIntervalMs = 50
    let receivedAnyChunk = false

    function flushBuffer() {
      // 注意：不再 gate on `wb.skillId === ownedSkillId`。
      // assistantMessage 是对象引用，mutate 它是安全的，不会影响其他 skill。
      // 过去的 `isStillOwning()` 拦截会造成切换 skill 后旧流永远 streaming=true。
      assistantMessage.content = buffer
    }

    function endStreaming() {
      assistantMessage.streaming = false
      delete (assistantMessage as Record<string, unknown>)._wasStreaming
    }

    function endStreamingWithError(reason: string) {
      assistantMessage.streaming = false
      delete (assistantMessage as Record<string, unknown>)._wasStreaming
      assistantMessage._streamErrorReason = reason
      if (!assistantMessage.content) {
        assistantMessage.content = `⚠️ ${reason}`
      }
    }

    // 只有 UI 全局副作用（toast / modal）才需要判断"用户是否还在看这个 skill"。
    // 切了 skill 再弹 "AI 已自动保存" notification 会让用户困惑。
    function isUserStillViewing(): boolean {
      return wb.skillId === ownedSkillId
    }

    return new Promise<void>((resolve, reject) => {
      // 每次 send 用唯一 subscriberId，onDone/onError 时 removeSubscriber。
      // 关键：两次 send 并存时，两个 subscriber 各自拿到 raw 事件、各自
      // resolve 自己的 Promise，再也不会互相覆盖导致 Promise 永不 resolve。
      const subscriberId = `turn-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      let resolved = false
      let latestUsage: WorkbenchChatMessage['usage'] | undefined

      const cleanup = () => {
        client.removeSubscriber(subscriberId)
        // 如果该 client 已没有活跃 subscriber，考虑在一段时间后关闭以释放 WS。
        // 延时 30s 是为了让紧接着的下一次 send 可以复用同一 socket（避免握手开销）。
        if (!client.hasSubscribers()) {
          scheduleCloseIfIdle(ownedSkillId)
        }
      }

      const subscriber: StreamSubscriber = {
        id: subscriberId,

        onSessionReady: ({ session_id, model, tools, prompt_hash, config_dir, runtime_profile }) => {
          // sessionMeta 是 UI 全局状态，仅在用户还在看时更新；
          // 切走后即使 session 仍在跑，我们不覆盖别的 skill 的 meta。
          if (!isUserStillViewing()) return
          codingSessionMeta.value = {
            session_id,
            model,
            prompt_hash: prompt_hash || '',
            config_dir: config_dir || '',
            tools_count: Array.isArray(tools) ? tools.length : 0,
            runtime_profile: runtime_profile || null,
          }
        },

        onSeqAdvance: (seq: number) => {
          // 同步 lastSeq 到 wb store，刷新后 resume 协议用。
          if (typeof wb.setLastSeqForSkill === 'function' && ownedSkillId) {
            wb.setLastSeqForSkill(ownedSkillId, seq)
          }
        },

        onTextDelta: (chunk: string) => {
          receivedAnyChunk = true
          buffer += chunk
          const now = Date.now()
          if (now - lastFlushAt >= flushIntervalMs) {
            flushBuffer()
            lastFlushAt = now
          }
        },

        onToolCall: (call) => {
          assistantMessage.toolCalls?.push({ ...call, status: 'pending' })
        },

        onToolResult: ({ id, content, is_error }) => {
          const toolCall = assistantMessage.toolCalls?.find((item) => item.id === id)
          if (toolCall) {
            toolCall.status = is_error ? 'error' : 'success'
            toolCall.result = typeof content === 'string' ? content : JSON.stringify(content)
          }
        },

        onFileChange: (change) => {
          const normalizedPath = normalizeSkillFilePath(change.path, ownedSkillId)
          if (!isUsableSkillFilePath(normalizedPath)) {
            console.warn('[tracking] ignore invalid file_change path:', change.path)
            return
          }
          const normalizedChange = {
            ...change,
            path: normalizedPath,
          }
          assistantMessage.fileChanges?.push(normalizedChange)
          // scheduleSkillRefresh 只对"用户正在看的那个 skill"有意义：它会 reload
          // 左侧文件树，刷新 health 之类的全局 UI。给别的 skill 静默写就好。
          if (isUserStillViewing()) {
            scheduleSkillRefresh(loadFileList, loadHistory, loadHealthScore, skillApi, wb)
            if (trackingMode.value) {
              handleJumpToFile(normalizedChange)
            }
          }
        },

        onPermissionRequest: (request) => {
          // 权限请求必须弹给当前用户；如果用户切走了，auto-deny 是保守安全选择。
          // 这里先保持"只在还看着时弹，否则丢弃（后端 auto-timeout 会 deny）"。
          if (!isUserStillViewing()) return
          if (request.auto_approved) {
            if (pendingPermissionRequest.value?.request_id === request.request_id) {
              pendingPermissionRequest.value = null
            }
            return
          }
          pendingPermissionRequest.value = request
        },

        onUsage: (usage) => {
          latestUsage = usage
          assistantMessage.usage = usage
        },

        onDone: ({ total_cost_usd, duration_ms, usage, git_commit, git_commit_full, git_commit_error, changed_files, diff_summary }) => {
          flushBuffer()
          endStreaming()
          assistantMessage.usage = usage || latestUsage
          assistantMessage.durationMs = duration_ms
          assistantMessage.totalCostUsd = total_cost_usd
          assistantMessage.toolCallCount = assistantMessage.toolCalls?.length || 0
          if (git_commit) {
            assistantMessage.gitCommit = git_commit
            assistantMessage.gitCommitFull = git_commit_full || git_commit
            assistantMessage.diffSummary = isRecord(diff_summary) ? diff_summary : null
          }
          if (Array.isArray(changed_files)) {
            assistantMessage.fileChanges = assistantMessage.fileChanges || []
            for (const path of changed_files) {
              const normalizedPath = normalizeSkillFilePath(path, ownedSkillId)
              if (!isUsableSkillFilePath(normalizedPath)) continue
              if (!assistantMessage.fileChanges.find((fc) => fc.path === normalizedPath)) {
                assistantMessage.fileChanges.push({ path: normalizedPath, operation: 'edit' })
              }
            }
          }
          const changedFiles = assistantMessage.fileChanges || []
          if ((changedFiles.length || git_commit || git_commit_error) && isUserStillViewing()) {
            // 刷新文件树 + 弹"已自动保存"通知，都属于 UI 全局副作用，
            // 切走后就不该再打扰用户。
            scheduleSkillRefresh(loadFileList, loadHistory, loadHealthScore, skillApi, wb)
            if (git_commit) {
              const changedLabel = changedFiles.length ? `${changedFiles.length} 个文件变更` : 'Skill 变更'
              Notification.success({
                id: 'ai-auto-commit',
                title: 'AI 已保存到 Git',
                content: `${changedLabel}已提交：${git_commit}`,
                duration: 3000,
                closable: true,
              })
            } else if (git_commit_error) {
              Notification.warning({
                id: 'ai-auto-commit',
                title: 'AI 已修改，Git 提交失败',
                content: git_commit_error,
                duration: 5000,
                closable: true,
              })
            } else if (changedFiles.length) {
              Notification.info({
                id: 'ai-auto-commit',
                title: 'AI 已更新文件',
                content: `${changedFiles.length} 个文件已更新，未检测到新的 Git 提交`,
                duration: 3000,
                closable: true,
              })
            }
          }
          if (typeof total_cost_usd === 'number' && total_cost_usd > 0) {
            logger.debug(`[coding] done, cost=$${total_cost_usd.toFixed(4)}`)
          }
          runningTasks.finish(chatTaskId, {
            status: 'success',
            message: (assistantMessage.fileChanges?.length || 0) > 0
              ? `完成 · 改动 ${assistantMessage.fileChanges!.length} 个文件`
              : '完成',
          })
          if (!resolved) { resolved = true; resolve() }
          cleanup()
        },

        onError: ({ code, error }) => {
          flushBuffer()
          const reason = humanizeCodingError(code, error)
          endStreamingWithError(reason)
          runningTasks.fail(chatTaskId, reason.slice(0, 100))
          // 这些都是 UI 全局弹窗，仅在用户还在看时弹。
          if (isUserStillViewing()) {
            if (code === 'CODING_AGENT_INIT_TIMEOUT' || code === 'CODING_AGENT_SPAWN_FAILED') {
              Modal.warning({
                title: 'AI 编程助手未就绪',
                content: `${error || reason}\n\n请到"管理 → 系统配置 → AI 编程助手"检查 API key / 模型 / base_url 配置，并点击"测试连接"。`,
              })
            } else if (code === 'CODING_AGENT_PERMISSION_VIOLATION') {
              Message.warning(reason)
            } else {
              Message.error(reason)
            }
          }
          if (!resolved) { resolved = true; resolve() }
          cleanup()
        },

        onDisconnect: (closeCode?: number) => {
          if (!receivedAnyChunk) {
            endStreamingWithError(`连接异常 (code=${closeCode}), 请检查网络后重试`)
            runningTasks.fail(chatTaskId, `连接异常 (code=${closeCode})`)
            if (!resolved) { resolved = true; reject(new Error(`coding WS 关闭 (code=${closeCode})`)) }
          } else {
            flushBuffer()
            assistantMessage.streaming = true
            assistantMessage._wasStreaming = true
            assistantMessage._streamErrorReason = `连接中断 (code=${closeCode}), 正在自动续流`
            runningTasks.finish(chatTaskId, {
              status: 'blocked',
              message: '连接中断，正在续流',
            })
            window.setTimeout(() => {
              if (!ownedSkillId || !isUserStillViewing()) return
              if (!assistantMessage._wasStreaming && !assistantMessage.streaming) return
              void autoResumeChatIfPending()
            }, DISCONNECT_AUTO_RESUME_DELAY_MS)
            if (!resolved) { resolved = true; resolve() }
          }
          cleanup()
        },
      }

      client.addSubscriber(subscriber)

      ;(async () => {
        try {
          if (!client.connected) {
            await client.connect()
          }
          client.sendMessage(message, { images: options.images, mode: studioMode.value })
        } catch (error) {
          endStreaming()
          if (!resolved) { resolved = true; reject(error) }
          cleanup()
        }
      })()
    })
  }

  /**
   * 延迟关闭空闲 client 的计时器（per-skill）。
   * 30s 是经验值：一次来回的对话间隔多半小于这个数，保留连接可省去下次握手开销。
   */
  const idleCloseTimers = new Map<string, ReturnType<typeof setTimeout>>()

  function scheduleCloseIfIdle(skillId: string) {
    const existing = idleCloseTimers.get(skillId)
    if (existing) clearTimeout(existing)
    const timer = setTimeout(() => {
      const client = codingClientsBySkill.get(skillId)
      if (client && !client.hasSubscribers()) {
        closeCodingClient(skillId)
      }
      idleCloseTimers.delete(skillId)
    }, 30_000)
    idleCloseTimers.set(skillId, timer)
  }

  function cleanupClients() {
    for (const [, client] of codingClientsBySkill) {
      try { client.close() } catch { /* ignore */ }
    }
    codingClientsBySkill.clear()
    for (const timer of idleCloseTimers.values()) clearTimeout(timer)
    idleCloseTimers.clear()
    pendingPermissionRequest.value = null
  }

  // 切换 skillId 时不再强制关所有 client——
  // 别的 skill 的流可能还在跑，保留连接让它们继续；
  // 若超过 MAX_CLIENTS，LRU 会自然淘汰。
  // 只在组件卸载时全量 cleanup。
  watch(() => wb.skillId, (newId, oldId) => {
    if (newId !== oldId) {
      // 切 skill 时 pendingPermissionRequest 是上一个 skill 的，清掉避免误响应
      pendingPermissionRequest.value = null
    }
  })

  onBeforeUnmount(() => {
    if (refreshSkillTimer) {
      clearTimeout(refreshSkillTimer)
      refreshSkillTimer = null
    }
    cleanupClients()
  })

  function scheduleSkillRefresh(
    reloadFiles: (force?: boolean) => Promise<unknown>,
    reloadHistory: () => Promise<unknown>,
    reloadHealth: () => Promise<unknown>,
    localSkillApi: SkillStudioAiOptions['skillApi'],
    localWorkbench: SkillStudioAiOptions['wb'],
  ) {
    if (refreshSkillTimer) {
      clearTimeout(refreshSkillTimer)
    }
    refreshSkillTimer = setTimeout(async () => {
      if (!wb.skillId || studioDoc.isCreate.value) return
      try {
        await reloadFiles(true)
        const data = await localSkillApi.get(wb.skillId)
        const { fromSkillResponse } = await import('@/utils/schemaMapper')
        const mapped = fromSkillResponse(data as StructuredSkillResponse)
        if (mapped && typeof localWorkbench.setSkillDocument === 'function') {
          localWorkbench.setSkillDocument(mapped)
        }
        try { await reloadHistory() } catch {}
        try { await reloadHealth() } catch {}
      } catch (error) {
        console.warn('[refresh] schedule refresh failed:', error)
      }
    }, 800)
  }

  /**
   * 自动续流：SkillStudio mount 后若 wb.messages 里还有 _wasStreaming（或 streaming）
   * 的 assistant 消息（说明刷新时 AI 还在打字），就建 client + 发 resume，把后端
   * session 里剩下的事件（`fe_history` 里 fe_seq > last_seq 的 + 续 live）补回来。
   */
  async function autoResumeChatIfPending() {
    const skillId = wb.skillId
    if (!skillId) return
    const messages = (wb.messages || []) as WorkbenchChatMessage[]
    if (!messages.length) return
    const pending = messages.find((m: any) =>
      m && m.role === 'assistant' && (m._wasStreaming || m.streaming),
    )
    if (!pending) return

    const lastSeq = typeof wb.getLastSeqForSkill === 'function'
      ? wb.getLastSeqForSkill(skillId) || 0
      : 0

    const client = getOrCreateCodingClient(skillId)
    const subscriberId = `resume-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    let resolved = false
    let latestUsage: WorkbenchChatMessage['usage'] | undefined

    const subscriber: StreamSubscriber = {
      id: subscriberId,
      onSeqAdvance: (seq: number) => {
        if (typeof wb.setLastSeqForSkill === 'function') {
          wb.setLastSeqForSkill(skillId, seq)
        }
      },
      onTextDelta: (chunk: string) => {
        pending.content = (pending.content || '') + chunk
      },
      onToolCall: (call) => {
        pending.toolCalls = pending.toolCalls || []
        if (!pending.toolCalls.find((x) => x.id === call.id)) {
          pending.toolCalls.push({ ...call, status: 'pending' })
        }
      },
      onToolResult: ({ id, content, is_error }) => {
        const tc = pending.toolCalls?.find((x) => x.id === id)
        if (tc) {
          tc.status = is_error ? 'error' : 'success'
          tc.result = stringifyToolResult(content)
        }
      },
      onFileChange: (change) => {
        const normalizedPath = normalizeSkillFilePath(change.path, skillId)
        if (!isUsableSkillFilePath(normalizedPath)) {
          console.warn('[chat-resume] ignore invalid file_change path:', change.path)
          return
        }
        pending.fileChanges = pending.fileChanges || []
        pending.fileChanges.push({
          ...change,
          path: normalizedPath,
        })
        if (wb.skillId === skillId) {
          scheduleSkillRefresh(loadFileList, loadHistory, loadHealthScore, skillApi, wb)
        }
      },
      onUsage: (usage) => {
        latestUsage = usage
        pending.usage = usage
      },
      onDone: ({ git_commit, git_commit_full, changed_files, diff_summary, duration_ms, total_cost_usd, usage }) => {
        pending.streaming = false
        delete (pending as any)._wasStreaming
        pending.usage = usage || latestUsage
        pending.durationMs = duration_ms
        pending.totalCostUsd = total_cost_usd
        pending.toolCallCount = pending.toolCalls?.length || 0
        if (git_commit) {
          pending.gitCommit = git_commit
          pending.gitCommitFull = git_commit_full || git_commit
          pending.diffSummary = isRecord(diff_summary) ? diff_summary : null
        }
        if (Array.isArray(changed_files)) {
          pending.fileChanges = pending.fileChanges || []
          for (const path of changed_files) {
            const normalizedPath = normalizeSkillFilePath(path, skillId)
            if (!isUsableSkillFilePath(normalizedPath)) continue
            if (!pending.fileChanges.find((fc) => fc.path === normalizedPath)) {
              pending.fileChanges.push({ path: normalizedPath, operation: 'edit' })
            }
          }
        }
        runningTasks.finish(
          runningTasks.startOrUpdate(`chat:${skillId}`, {
            kind: 'chat', title: 'AI 对话', skillId,
            skillName: studioDoc.doc.value?.meta?.name || skillId,
            returnPath: `/skills/${skillId}?panel=assistant`,
          }),
          { status: 'success', message: '已续流完成' },
        )
        if (!resolved) { resolved = true; client.removeSubscriber(subscriberId) }
      },
      onError: ({ code, error }) => {
        pending.streaming = false
        delete (pending as any)._wasStreaming
        if (code === 'RESUME_NO_SESSION') {
          pending.content = (pending.content || '')
            + `\n\n*（${humanizeCodingError(code, error)}）*`
        } else {
          ;(pending as any)._streamErrorReason = humanizeCodingError(code, error)
        }
        runningTasks.fail(
          runningTasks.startOrUpdate(`chat:${skillId}`, {
            kind: 'chat', title: 'AI 对话', skillId,
            skillName: studioDoc.doc.value?.meta?.name || skillId,
            returnPath: `/skills/${skillId}?panel=assistant`,
          }),
          code === 'RESUME_NO_SESSION' ? '会话已过期' : (error || code || '续流失败'),
        )
        if (!resolved) { resolved = true; client.removeSubscriber(subscriberId) }
      },
      onDisconnect: () => {
        pending.streaming = false
        ;(pending as any)._wasStreaming = true
        if (!resolved) { resolved = true; client.removeSubscriber(subscriberId) }
      },
    }

    client.addSubscriber(subscriber)
    pending.streaming = true

    // 兜底：与后端 12min inactivity watchdog 对齐。长 Bash/API 期间可能数分钟没有
    // tool_result，不能 30s 就把续流 UI 清掉，否则用户会以为“回到一半停掉了”。
    const watchdogTimer = setTimeout(() => {
      if (resolved) return
      resolved = true
      pending.streaming = false
      delete (pending as any)._wasStreaming
      client.removeSubscriber(subscriberId)
      console.warn('[chat-resume] 长时间无终止事件, 兜底清 UI 状态')
    }, RESUME_SILENCE_TIMEOUT_MS)
    const clearWatchdog = () => { clearTimeout(watchdogTimer) }
    const origOnDone = subscriber.onDone
    const origOnError = subscriber.onError
    const origOnDisconnect = subscriber.onDisconnect
    subscriber.onDone = (payload) => { clearWatchdog(); origOnDone?.(payload) }
    subscriber.onError = (payload) => { clearWatchdog(); origOnError?.(payload) }
    subscriber.onDisconnect = (code) => { clearWatchdog(); origOnDisconnect?.(code) }

    try {
      if (!client.connected) await client.connect()
      client.sendResume(lastSeq)
    } catch (err) {
      clearWatchdog()
      pending.streaming = false
      delete (pending as any)._wasStreaming
      if (!resolved) { resolved = true; client.removeSubscriber(subscriberId) }
      console.warn('[chat-resume] 恢复失败', err)
    }
  }

  return {
    submitting,
    conversations,
    chatBudget,
    pendingPermissionRequest,
    codingSessionMeta,
    trackingMode,
    handleUploadReport,
    startChatMode,
    handleNewConversation,
    handleSwitchConversation,
    handleSend,
    stopGeneration,
    respondCodingPermission,
    handleJumpToFile,
    autoResumeChatIfPending,
  }

  // v2.6: buildChatContext 移到 composable 闭包内，直接用 ui / studioDoc 引用
  function buildChatContext(options: ChatOptions = {}): ChatContext {
    const rawModule = options.targetModule || ui.studioActiveModule || ''
    const context: ChatContext = {
      active_module: VALID_MODULES.has(rawModule) ? rawModule : null,
      draft_revision: 0,
    }
    if (options.selectionRange?.text) {
      context.selection = {
        module_id: options.selectionRange.moduleId || ui.studioActiveModule || '',
        start_line: options.selectionRange.startLine || null,
        end_line: options.selectionRange.endLine || null,
        text: options.selectionRange.text,
      }
    }
    const activeKey = ui.studioActiveModule
    const moduleData = activeKey ? (studioDoc.doc.value as Record<string, unknown> | null | undefined)?.[activeKey] : undefined
    if (moduleData !== undefined) {
      context.draft_snapshot = { [activeKey as string]: moduleData }
    }
    return context
  }
}
