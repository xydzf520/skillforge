import { computed, ref } from 'vue'
import type { SkillDocument } from '@/types/skill'
import type { WorkbenchChatMessage, WorkbenchPatchDraft, WorkbenchSessionResponse } from '@/types/skillstudio'

// 跨 skill 缓存的最大 skill 数：超过后用 LRU 淘汰最久未访问的条目，避免
// 长时间浏览多个 skill 时 messagesBySkillId 无限膨胀。
const MAX_CACHED_SKILLS = 10
import {
  buildParsedSnapshot,
  cloneValue,
  createWorkbenchContext,
  normalizeMessage,
  normalizeModuleName,
  normalizeSkillDocument,
} from './workbenchShared'

type SessionPayload = WorkbenchSessionResponse & {
  skill_id?: string
  skillId?: string
  context?: Record<string, unknown>
  skill_structure?: Record<string, unknown>
  skillStructure?: Record<string, unknown>
  skill?: Record<string, unknown>
  structure?: Record<string, unknown>
  parsed?: Record<string, unknown>
  workflow?: Record<string, unknown> | null
  meta?: Record<string, unknown>
  skill_document?: Record<string, unknown>
  skillDocument?: Record<string, unknown>
  intent?: unknown
  patch?: WorkbenchPatchDraft | Record<string, unknown>
  latest_patch?: WorkbenchPatchDraft | Record<string, unknown>
  latestPatch?: WorkbenchPatchDraft | Record<string, unknown>
  validation?: unknown
  latest_validation?: unknown
  latestValidation?: unknown
}

type SessionStoreOptions = {
  documentStore: {
    flatDocument: SkillDocument
    setFromFlatDocument: (document: SkillDocument) => void
  }
  setIntent: (payload: Record<string, unknown>) => void
  setPatch: (payload: WorkbenchPatchDraft | Record<string, unknown>) => void
  setValidationReport: (payload: Record<string, unknown>) => void
}

export function createWorkbenchSessionStore(options: SessionStoreOptions) {
  const { documentStore, setIntent, setPatch, setValidationReport } = options

  const sessionId = ref('')
  const skillId = ref('')
  const mode = ref('pro')
  const activeModule = ref('goal')
  const context = ref(createWorkbenchContext())
  const messages = ref<WorkbenchChatMessage[]>([])
  const session = ref<SessionPayload | null>(null)
  const skill = ref<Record<string, unknown> | null>(null)
  const currentSession = ref<SessionPayload | null>(null)
  const latestIntent = ref<unknown>(null)
  // 跨 skill 切换时缓存各 skill 的对话，返回时恢复。不落 sessionStorage（见下面的持久化分区）。
  // 用 Map 维护 LRU 顺序（插入/touch 顺序），messagesBySkillId 作为外部可读副本保留。
  const messagesCache = new Map<string, WorkbenchChatMessage[]>()
  const lastAccessOrder = new Map<string, number>()
  let accessCounter = 0
  const messagesBySkillId = ref<Record<string, WorkbenchChatMessage[]>>({})
  const lastSeqBySkillId = ref<Record<string, number>>({})

  function syncMessagesRefFromCache() {
    const snapshot: Record<string, WorkbenchChatMessage[]> = {}
    for (const [id, list] of messagesCache.entries()) snapshot[id] = list
    messagesBySkillId.value = snapshot
  }

  function touchAccess(id: string) {
    accessCounter += 1
    lastAccessOrder.set(id, accessCounter)
  }

  function evictIfNeeded() {
    while (messagesCache.size > MAX_CACHED_SKILLS) {
      // 找到 lastAccessOrder 最小（最久未访问）的 key
      let oldestKey: string | null = null
      let oldestTick = Number.POSITIVE_INFINITY
      for (const key of messagesCache.keys()) {
        const tick = lastAccessOrder.get(key) ?? 0
        if (tick < oldestTick) {
          oldestTick = tick
          oldestKey = key
        }
      }
      if (!oldestKey) break
      messagesCache.delete(oldestKey)
      lastAccessOrder.delete(oldestKey)
    }
  }

  const skillDocument = computed(() => documentStore.flatDocument)
  const skillStructure = skillDocument
  const hasSession = computed(() => !!sessionId.value)

  function setActiveModule(value: string) {
    activeModule.value = normalizeModuleName(value)
  }

  function setMode(value: string) {
    mode.value = value === 'novice' ? 'novice' : 'pro'
  }

  function setIntentState(payload: unknown) {
    latestIntent.value = cloneValue(payload, null)
    setIntent(cloneValue<Record<string, unknown>>(payload, {}))
  }

  function setSkillDocument(payload: unknown) {
    const normalized = normalizeSkillDocument(payload)
    skill.value = cloneValue<Record<string, unknown>>(payload, {})
    documentStore.setFromFlatDocument(normalized)
  }

  function setContext(value: unknown) {
    const raw = cloneValue<Record<string, unknown> | null>(value, null)
    if (!raw) {
      context.value = createWorkbenchContext()
      skill.value = null
      return
    }

    const normalizedSkill = normalizeSkillDocument(raw)
    context.value = {
      parsed: cloneValue(raw.parsed || buildParsedSnapshot(normalizedSkill), {}),
      workflow: cloneValue(raw.workflow || normalizedSkill.workflow || null, null),
      meta: cloneValue(raw.meta || normalizedSkill.meta || {}, {}),
      raw,
    }
    skill.value = cloneValue(raw.skill || raw.skill_document || raw.skillDocument || raw, {})
    documentStore.setFromFlatDocument(normalizedSkill)
  }

  function setSession(payload: SessionPayload = {}) {
    const raw = cloneValue<SessionPayload>(payload, {} as SessionPayload)
    session.value = raw
    currentSession.value = raw
    sessionId.value = raw.session_id || raw.sessionId || raw.id || sessionId.value || ''
    skillId.value = raw.skill_id || raw.skillId || skillId.value || ''
    if (raw.mode) mode.value = raw.mode
    if (raw.current_module || raw.currentModule) activeModule.value = normalizeModuleName((raw.current_module || raw.currentModule)!)

    const contextSource = raw.context || raw.skill_structure || raw.skillStructure || raw.skill || raw.structure
    if (contextSource) {
      const normalizedSkill = normalizeSkillDocument(contextSource)
      const hasExistingDoc = !!(documentStore.flatDocument && (
        documentStore.flatDocument.meta?.name ||
        (documentStore.flatDocument.rules && documentStore.flatDocument.rules.length) ||
        documentStore.flatDocument.goal
      ))
      context.value = {
        parsed: cloneValue(contextSource.parsed || raw.parsed || buildParsedSnapshot(normalizedSkill), {}),
        workflow: cloneValue(contextSource.workflow || raw.workflow || normalizedSkill.workflow || null, null),
        meta: cloneValue(contextSource.meta || raw.meta || normalizedSkill.meta || {}, {}),
        raw: cloneValue(contextSource, null),
      }
      if (!hasExistingDoc) {
        skill.value = cloneValue(raw.skill || raw.skill_document || raw.skillDocument || contextSource, {})
        documentStore.setFromFlatDocument(normalizedSkill)
      }
    }

    if (Array.isArray(raw.messages)) {
      messages.value = raw.messages.map(normalizeMessage).filter((m): m is NonNullable<typeof m> => m !== null)
    }
    if (raw.intent) setIntentState(raw.intent)
    if (raw.patch || raw.latest_patch || raw.latestPatch) setPatch((raw.patch || raw.latest_patch || raw.latestPatch) as Record<string, unknown>)
    if (raw.validation || raw.latest_validation || raw.latestValidation) setValidationReport((raw.validation || raw.latest_validation || raw.latestValidation) as Record<string, unknown>)
  }

  function appendMessage(role: string, content: unknown) {
    const message = normalizeMessage({ role, content })
    if (!message) return
    messages.value.push(message)
    if (currentSession.value) {
      if (!Array.isArray(currentSession.value.messages)) {
        currentSession.value.messages = []
      }
      currentSession.value.messages.push(message)
    }
  }

  function setMessages(list: unknown[] | null | undefined) {
    messages.value = Array.isArray(list) ? list.map(normalizeMessage).filter((m): m is NonNullable<typeof m> => m !== null) : []
  }

  function resetSessionState(options: { keepSkillId?: boolean } = {}) {
    if (!options.keepSkillId) skillId.value = ''
    sessionId.value = ''
    mode.value = 'pro'
    activeModule.value = 'goal'
    context.value = createWorkbenchContext()
    messages.value = []
    session.value = null
    skill.value = null
    currentSession.value = null
    latestIntent.value = null
  }

  // 把当前 messages 快照到 cache[id]。切 skill 前调用，不复制对象引用
  // （保留同一 assistantMessage 对象，以便流式中的闭包继续写入）。
  // 同时把仍处于 streaming=true 的消息打上 _wasStreaming 标记：真正的流式对象引用
  // 仍保留在内存里（闭包写入不受影响），但快照/恢复后 UI 可据此判断"这条是上次流式
  // 截断的残留"从而显示"已中断"提示，而不是一直转 spinner。
  function snapshotMessagesForSkill(id: string) {
    if (!id) return
    // 就地给内存中的 streaming 消息打标记，保证内存视图与 cache 视图一致。
    for (const m of messages.value as any[]) {
      if (m && typeof m === 'object' && m.streaming) {
        m._wasStreaming = true
      }
    }
    messagesCache.set(id, [...messages.value])
    touchAccess(id)
    evictIfNeeded()
    syncMessagesRefFromCache()
  }

  function restoreMessagesForSkill(id: string): boolean {
    if (!id) return false
    const cached = messagesCache.get(id)
    if (!Array.isArray(cached) || cached.length === 0) return false
    messages.value = [...cached]
    touchAccess(id)
    // 访问顺序更新后同步一下外部视图（不影响缓存内容，仅用来保持可观测一致）
    syncMessagesRefFromCache()
    return true
  }

  function getLastSeqForSkill(id: string): number {
    return lastSeqBySkillId.value[id] || 0
  }

  function setLastSeqForSkill(id: string, seq: number) {
    if (!id) return
    const prev = lastSeqBySkillId.value[id] || 0
    if (seq <= prev) return
    lastSeqBySkillId.value = { ...lastSeqBySkillId.value, [id]: seq }
  }

  return {
    sessionId,
    skillId,
    mode,
    activeModule,
    context,
    messages,
    session,
    skill,
    currentSession,
    latestIntent,
    messagesBySkillId,
    lastSeqBySkillId,
    skillDocument,
    skillStructure,
    hasSession,
    setSession,
    setActiveModule,
    setMode,
    setContext,
    appendMessage,
    setMessages,
    setIntent: setIntentState,
    setSkillDocument,
    resetSessionState,
    snapshotMessagesForSkill,
    restoreMessagesForSkill,
    getLastSeqForSkill,
    setLastSeqForSkill,
  }
}
