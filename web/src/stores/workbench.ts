import { defineStore } from 'pinia'
import { computed, reactive, ref, watch } from 'vue'
import { useDocumentStore } from './document'
import { useUserStore } from './user'
import { MODULES } from './workbenchShared'
import { createWorkbenchReferenceStore } from './workbenchReferenceStore'
import { createWorkbenchValidationStore } from './workbenchValidationStore'
import { createWorkbenchPatchStore } from './workbenchPatchStore'
import { createWorkbenchSessionStore } from './workbenchSessionStore'
import type { SkillDocument } from '@/types/skill'

// sessionStorage key 前缀：按 userId + skillId 分片持久化对话 + lastSeq。
// 加 userId 前缀避免登出/切账号后前一个用户的对话残留到下一个用户（越权风险）。
// userId 未就绪时退化为 'anon'（首次 hydrate 完成后会被 user watcher 重新拉一次）。
const CHAT_STORAGE_PREFIX = 'sf-chat:'

function chatStorageKey(userId: string, skillId: string): string {
  const uid = userId || 'anon'
  return `${CHAT_STORAGE_PREFIX}${uid}:${skillId}`
}

function loadChatFromStorage(userId: string, skillId: string): { messages: unknown[]; lastSeq: number } | null {
  if (!skillId) return null
  try {
    const raw = sessionStorage.getItem(chatStorageKey(userId, skillId))
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return null
    return {
      messages: Array.isArray(parsed.messages) ? parsed.messages : [],
      lastSeq: Number.isFinite(parsed.lastSeq) ? parsed.lastSeq : 0,
    }
  } catch {
    return null
  }
}

function saveChatToStorage(userId: string, skillId: string, messages: unknown[], lastSeq: number) {
  if (!skillId) return
  // 不写空数组：reset 瞬间空 messages 会把前一次保存的历史冲掉。
  // 真正"清空"语义走 clearChatForSkill 显式接口，而不是被 watcher 误触发。
  if (!Array.isArray(messages) || messages.length === 0) return
  try {
    // 落盘前把 streaming 标记清掉：刷新后不应该出现"一直在转"的幽灵状态。
    // 真正还在跑的 backend session 会通过 resume 协议把后续事件补回来。
    const sanitized = messages.map((m: any) => {
      if (m && typeof m === 'object' && m.streaming) {
        return { ...m, streaming: false, _wasStreaming: true }
      }
      return m
    })
    sessionStorage.setItem(
      chatStorageKey(userId, skillId),
      JSON.stringify({ messages: sanitized, lastSeq }),
    )
  } catch {
    // 配额 / 序列化失败时静默忽略
  }
}

// 登录后清理 anon 前缀的对话缓存：userId 从 ''/'anon' 变成真实值时，
// sessionStorage 里可能残留 'sf-chat:anon:*' 这类 key。这些 key 不会被新用户读到
// （新用户用真实 uid 前缀），但堆积会浪费 5MB 配额，且如果有路径在 userId 未就绪时
// 又写回 anon key，可能再度读回旧账号的数据。统一在登录时一次性清掉。
function clearAnonChatStorage() {
  if (typeof window === 'undefined') return
  const anonPrefix = `${CHAT_STORAGE_PREFIX}anon:`
  try {
    // 先收集再删：边遍历边删 sessionStorage.key(i) 会跳过 index，漏删。
    const toRemove: string[] = []
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const k = sessionStorage.key(i)
      if (k && k.startsWith(anonPrefix)) toRemove.push(k)
    }
    for (const k of toRemove) sessionStorage.removeItem(k)
  } catch {
    // 安全模式 / 禁用 storage 时静默忽略
  }
}

type LoadingState = {
  loadingStructure?: boolean
  generatingPatch?: boolean
  validatingPatch?: boolean
  applyingPatch?: boolean
}

type ErrorState = {
  patchError?: unknown
  validationError?: unknown
  structureError?: unknown
}

type WorkbenchHydration = Record<string, unknown>

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

// v2.5: SkillStudio 专用的页面级运行时状态（从 deprecated useIDEStore 迁过来）
type StudioCreateForm = {
  skill_id: string
  name: string
  department: string
  role: string
  trigger_type: string
  trigger_expression: string
  risk_level: string
  approval_level: number
}

function createStudioCreateFormDefault(): StudioCreateForm {
  return {
    skill_id: '',
    name: '',
    department: '',
    role: '',
    trigger_type: 'manual',
    trigger_expression: '',
    risk_level: 'R2',
    approval_level: 1,
  }
}

export const useWorkbenchStore = defineStore('workbench', () => {
  const documentStore = useDocumentStore()
  const validationStore = createWorkbenchValidationStore()
  const patchStore = createWorkbenchPatchStore({
    activeModule: computed(() => sessionStore.activeModule.value),
    setValidationReport: validationStore.setValidationReport,
    setWorkflowPreview: validationStore.setWorkflowPreview,
  })
  const sessionStore = createWorkbenchSessionStore({
    documentStore,
    setIntent: () => {},
    setPatch: (payload) => patchStore.setPatch(isRecord(payload) ? payload : {}),
    setValidationReport: validationStore.setValidationReport,
  })
  const referenceStore = createWorkbenchReferenceStore()
  const currentPhase = computed(() => {
    if (validationStore.loadingStructure.value) return 'loading_structure'
    if (patchStore.generatingPatch.value) return 'generating_patch'
    if (validationStore.validatingPatch.value) return 'validating_patch'
    if (patchStore.applyingPatch.value) return 'applying_patch'
    if (patchStore.patchError.value) return 'patch_error'
    if (validationStore.validationError.value) return 'validation_error'
    return 'idle'
  })
  const status = ref('idle')

  const moduleList = computed(() => MODULES.map(module => {
    const stats = getModuleStats(module.key)
    return {
      ...module,
      active: sessionStore.activeModule.value === module.key,
      hasContent: stats.hasContent,
      count: stats.count,
      summary: stats.summary,
    }
  }))
  const currentModule = computed(() => moduleList.value.find(module => module.key === sessionStore.activeModule.value) || moduleList.value[0])

  function getModuleStats(moduleKey: string) {
    const skillDocument = sessionStore.skillDocument.value as SkillDocument
    switch (moduleKey) {
      case 'meta':
        return {
          hasContent: !!skillDocument.meta?.name,
          count: skillDocument.meta?.name ? 1 : 0,
          summary: skillDocument.meta?.name || '未填写',
        }
      case 'goal':
        return {
          hasContent: !!(skillDocument.goal || skillDocument.meta?.name || skillDocument.meta?.description),
          count: skillDocument.goal ? 1 : 0,
          summary: skillDocument.goal || skillDocument.meta?.name || skillDocument.meta?.description || '未填写',
        }
      case 'rules':
        return {
          hasContent: Array.isArray(skillDocument.rules) && skillDocument.rules.length > 0,
          count: skillDocument.rules?.length || 0,
          summary: skillDocument.rules?.length ? `${skillDocument.rules.length} 条规则` : '暂无规则',
        }
      case 'params':
        return {
          hasContent: Array.isArray(skillDocument.params) && skillDocument.params.length > 0,
          count: skillDocument.params?.length || 0,
          summary: skillDocument.params?.length ? `${skillDocument.params.length} 个参数` : '暂无参数',
        }
      case 'output_table':
        return {
          hasContent: Array.isArray(skillDocument.output_table) && skillDocument.output_table.length > 0,
          count: skillDocument.output_table?.length || 0,
          summary: skillDocument.output_table?.length ? `${skillDocument.output_table.length} 个字段` : '暂无输出定义',
        }
      case 'todos':
        return {
          hasContent: Array.isArray(skillDocument.todos) && skillDocument.todos.length > 0,
          count: skillDocument.todos?.length || 0,
          summary: skillDocument.todos?.length ? `${skillDocument.todos.length} 个待办模板` : '未配置待办',
        }
      case 'test_cases':
        return {
          hasContent: Array.isArray(skillDocument.test_cases) && skillDocument.test_cases.length > 0,
          count: skillDocument.test_cases?.length || 0,
          summary: skillDocument.test_cases?.length ? `${skillDocument.test_cases.length} 个样例` : '暂无测试样例',
        }
      case 'workflow':
        return {
          hasContent: (validationStore.workflowPreview.value.nodes?.length ?? 0) > 0 || (validationStore.workflowPreview.value.edges?.length ?? 0) > 0 || (validationStore.workflowPreview.value.bindings?.length ?? 0) > 0,
          count: validationStore.workflowPreview.value.nodes?.length ?? 0,
          summary: (validationStore.workflowPreview.value.nodes?.length ?? 0)
            ? `${validationStore.workflowPreview.value.nodes?.length ?? 0} 个节点 / ${validationStore.workflowPreview.value.edges?.length ?? 0} 条连线`
            : '暂无工作流',
        }
      default:
        return { hasContent: false, count: 0, summary: '' }
    }
  }

  // v2.5: SkillStudio 页面运行时状态（从 deprecated useIDEStore 迁过来）
  const studioLoaded = ref(false)
  const studioSaving = ref(false)
  const studioCreateForm = reactive<StudioCreateForm>(createStudioCreateFormDefault())

  function resetStudioCreateForm(): void {
    Object.assign(studioCreateForm, createStudioCreateFormDefault())
  }

  const userStore = useUserStore()
  const currentUserId = computed(() => userStore.userInfo?.user_id || '')

  // persistPaused：在 reset/切 skill 的"空窗期"临时关掉落盘，避免空 messages 被
  // debounce watcher 写进目标 skill 的 sessionStorage，冲掉它上一次的历史。
  const persistPaused = ref(false)
  function pausePersist() { persistPaused.value = true }
  function resumePersist() { persistPaused.value = false }

  // 持久化：watch [skillId, messages, lastSeqBySkillId] → debounce 写 sessionStorage。
  // handler 里再做一次守卫（skillId 非空 + messages 非空 + 未暂停）避免竞态下写坏数据。
  let persistTimer: ReturnType<typeof setTimeout> | null = null
  function schedulePersist() {
    if (persistTimer) clearTimeout(persistTimer)
    persistTimer = setTimeout(() => {
      if (persistPaused.value) return
      const id = sessionStore.skillId.value
      if (!id) return
      const list = sessionStore.messages.value as unknown[]
      if (!Array.isArray(list) || list.length === 0) return
      const seq = sessionStore.getLastSeqForSkill(id)
      saveChatToStorage(currentUserId.value, id, list, seq)
    }, 300)
  }
  // 去掉 deep: true —— 流式 token delta 会让深比较触发成本爆炸。
  // 改成监听"长度 + 最新 seq"的组合 key：消息新增 / 最新条变化时 schedulePersist（300ms debounce）。
  watch(
    [
      () => sessionStore.skillId.value,
      () => (sessionStore.messages.value as unknown[]).length,
      () => sessionStore.getLastSeqForSkill(sessionStore.skillId.value || ''),
    ] as const,
    schedulePersist,
  )

  // 从 sessionStorage 恢复：尝试把 skill 的对话从盘里拉回来
  function loadChatForSkill(id: string): boolean {
    const saved = loadChatFromStorage(currentUserId.value, id)
    if (!saved || saved.messages.length === 0) return false
    sessionStore.setMessages(saved.messages)
    if (saved.lastSeq > 0) sessionStore.setLastSeqForSkill(id, saved.lastSeq)
    return true
  }

  // userId 从空/anon → 真实值时（登录后首次 hydrate），重新尝试把当前 skill 的历史
  // 从"真实 userId"对应的 key 里拉一次。避免登录前用 anon 前缀，登录后看不到之前的对话。
  // 同时清理 anon 前缀残留（堆积浪费配额 + 防止串读回）。
  watch(currentUserId, (newId, oldId) => {
    if (!newId || newId === oldId) return
    // 登录场景：oldId 是空/'anon'，newId 是真实 userId → 清掉 anon 前缀残留
    // 登出场景（oldId=真实uid → newId=''/'anon'）不清，保留数据等重新登录再用
    const isLogin = newId !== 'anon' && (oldId === 'anon' || oldId === '' || oldId == null)
    if (isLogin) clearAnonChatStorage()
    const id = sessionStore.skillId.value
    if (!id) return
    if ((sessionStore.messages.value?.length || 0) === 0) {
      loadChatForSkill(id)
    }
  })

  function reset(options: { keepSkillId?: boolean } = {}) {
    // reset 本身是同步批处理：resetSessionState 会瞬间清空 messages，紧跟的调用方
    // （如 ide.initForEdit）会立即 setMessages(restored) 或维持 []。持久化守卫三条线：
    // 1. 外层调用方（ide.initForEdit）可用 pausePersist/resumePersist 显式标注空窗期；
    // 2. schedulePersist handler 里拒绝 messages.length===0 的写入；
    // 3. watcher 的 debounce 300ms 只保留"最终状态"，不会把中间态落盘。
    // 这里不再自行切换 persistPaused，以免与外层 pause 嵌套时被提前解开。
    sessionStore.resetSessionState(options)
    patchStore.resetPatchState()
    validationStore.resetValidationState()
    referenceStore.resetReferenceState()
    status.value = 'idle'
    documentStore.reset()
  }

  function setLoadingState(state: LoadingState = {}) {
    if (state.loadingStructure !== undefined) validationStore.loadingStructure.value = !!state.loadingStructure
    if (state.generatingPatch !== undefined) patchStore.generatingPatch.value = !!state.generatingPatch
    if (state.validatingPatch !== undefined) validationStore.validatingPatch.value = !!state.validatingPatch
    if (state.applyingPatch !== undefined) patchStore.applyingPatch.value = !!state.applyingPatch
    status.value = currentPhase.value
  }

  function setErrors(errors: ErrorState = {}) {
    if (errors.patchError !== undefined) patchStore.patchError.value = String(errors.patchError || '')
    if (errors.validationError !== undefined) validationStore.validationError.value = String(errors.validationError || '')
    if (errors.structureError !== undefined) validationStore.structureError.value = String(errors.structureError || '')
    status.value = currentPhase.value
  }

  function clearErrors() {
    patchStore.patchError.value = ''
    validationStore.validationError.value = ''
    validationStore.structureError.value = ''
  }

  function hydrateWorkbench(payload: WorkbenchHydration = {}) {
    if (payload.session_id || payload.sessionId) sessionStore.sessionId.value = String(payload.session_id || payload.sessionId || '')
    if (payload.skill_id || payload.skillId) sessionStore.skillId.value = String(payload.skill_id || payload.skillId || '')
    if (typeof payload.mode === 'string') sessionStore.setMode(payload.mode)
    if (payload.active_module || payload.activeModule) sessionStore.setActiveModule(String(payload.active_module || payload.activeModule || ''))
    if (isRecord(payload.session) || isRecord(payload.currentSession)) sessionStore.setSession((payload.session || payload.currentSession) as never)
    if (payload.context !== undefined) sessionStore.setContext(payload.context)
    if (Array.isArray(payload.messages)) sessionStore.setMessages(payload.messages)
    if (payload.intent !== undefined) sessionStore.setIntent(payload.intent)

    const skillSource = payload.skill_document
      || payload.skillDocument
      || payload.skillStructure
      || payload.skill_structure
      || payload.skill
    if (skillSource) sessionStore.setSkillDocument(skillSource)

    const patchSource = payload.current_patch
      || payload.currentPatch
      || payload.patchDraft
      || payload.patch_draft
      || payload.patch
    if (isRecord(patchSource)) patchStore.setPatch(patchSource as Record<string, unknown>)

    const validationSource = payload.validation_report
      || payload.validationReport
      || payload.validation_result
      || payload.validation
    if (validationSource) validationStore.setValidationReport(validationSource)

    const workflowSource = payload.workflow_document
      || payload.workflowDocument
      || payload.workflowPreview
      || payload.workflow_preview
      || payload.workflow
    if (workflowSource) validationStore.setWorkflowPreview(workflowSource)

    const references = Array.isArray(payload.references) ? payload.references : (Array.isArray(payload.referenceList) ? payload.referenceList : undefined)
    if (references) referenceStore.setReferences(references)
    const selectedReferenceIds = Array.isArray(payload.selected_reference_ids)
      ? payload.selected_reference_ids
      : (Array.isArray(payload.selectedReferenceIds) ? payload.selectedReferenceIds : undefined)
    if (selectedReferenceIds) referenceStore.setSelectedReferenceIds(selectedReferenceIds)
    if (payload.reference_query || payload.referenceQuery || payload.reference_source_type || payload.referenceSourceType || payload.reference_mode || payload.referenceMode) {
      referenceStore.setReferenceFilters({
        query: payload.reference_query || payload.referenceQuery,
        sourceType: payload.reference_source_type || payload.referenceSourceType,
        referenceMode: payload.reference_mode || payload.referenceMode,
      })
    }
  }

  return {
    sessionId: sessionStore.sessionId,
    skillId: sessionStore.skillId,
    mode: sessionStore.mode,
    activeModule: sessionStore.activeModule,
    context: sessionStore.context,
    messages: sessionStore.messages,
    session: sessionStore.session,
    currentSession: sessionStore.currentSession,
    latestIntent: sessionStore.latestIntent,
    latestPatch: patchStore.latestPatch,
    latestValidation: validationStore.latestValidation,
    hasSession: sessionStore.hasSession,
    skill: sessionStore.skill,
    skillDocument: sessionStore.skillDocument,
    skillStructure: sessionStore.skillStructure,
    workflowPreview: validationStore.workflowPreview,
    workflowDocument: validationStore.workflowDocument,
    patch: patchStore.patch,
    currentPatch: patchStore.currentPatch,
    validation: validationStore.validation,
    validationReport: validationStore.validationReport,
    status,
    modules: moduleList,
    references: referenceStore.references,
    selectedReferenceIds: referenceStore.selectedReferenceIds,
    selectedReferences: referenceStore.selectedReferences,
    referenceQuery: referenceStore.referenceQuery,
    referenceSourceType: referenceStore.referenceSourceType,
    referenceMode: referenceStore.referenceMode,
    loadingStructure: validationStore.loadingStructure,
    generatingPatch: patchStore.generatingPatch,
    validatingPatch: validationStore.validatingPatch,
    applyingPatch: patchStore.applyingPatch,
    patchError: patchStore.patchError,
    validationError: validationStore.validationError,
    structureError: validationStore.structureError,
    currentPhase,
    validationSummary: validationStore.validationSummary,
    validationStatus: validationStore.validationStatus,
    workflowSummary: validationStore.workflowSummary,
    patchSummary: patchStore.patchSummary,
    moduleList,
    currentModule,
    reset,
    setSession: sessionStore.setSession,
    setActiveModule: sessionStore.setActiveModule,
    setMode: sessionStore.setMode,
    setContext: sessionStore.setContext,
    appendMessage: sessionStore.appendMessage,
    setMessages: sessionStore.setMessages,
    setIntent: sessionStore.setIntent,
    setPatch: patchStore.setPatch,
    setValidationReport: validationStore.setValidationReport,
    setWorkflowPreview: validationStore.setWorkflowPreview,
    setSkillDocument: sessionStore.setSkillDocument,
    snapshotMessagesForSkill: sessionStore.snapshotMessagesForSkill,
    restoreMessagesForSkill: sessionStore.restoreMessagesForSkill,
    getLastSeqForSkill: sessionStore.getLastSeqForSkill,
    setLastSeqForSkill: sessionStore.setLastSeqForSkill,
    loadChatForSkill,
    pausePersist,
    resumePersist,
    setReferences: referenceStore.setReferences,
    setSelectedReferenceIds: referenceStore.setSelectedReferenceIds,
    toggleReference: referenceStore.toggleReference,
    setReferenceFilters: referenceStore.setReferenceFilters,
    setLoadingState,
    setErrors,
    clearErrors,
    hydrateWorkbench,
    // v2.5: SkillStudio 页面运行时状态
    studioLoaded,
    studioSaving,
    studioCreateForm,
    resetStudioCreateForm,
  }
})
