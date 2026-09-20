import { computed } from 'vue'
import type {
  SkillDocument,
  SkillMeta,
  SkillOutputField,
  SkillParam,
  SkillRuleStep,
  SkillTestCase,
  SkillTodoSpec,
  SkillWorkflowPreview,
  StructuredSkillResponse,
} from '@/types/skill'
import {
  isStructuredSkillResponse,
  type SkillStudioCreateForm,
  type SkillStudioSkillApi,
} from '@/types/skillstudio'
import { documentToMarkdown } from '@/utils/markdownParser'

// SkillStudioSkillApi 是精选子集，缺少我们要用的 saveStructured / create。
// 用 union 补齐（这些方法在实际 skillApi 上存在，只是上游类型未覆盖）。
type DocumentSkillApi = SkillStudioSkillApi & {
  saveStructured: (id: string, data: Record<string, unknown>) => Promise<unknown>
  create: (payload: Record<string, unknown>) => Promise<unknown>
}

/**
 * v2.5: 把 deprecated `useIDEStore` 里的"跨 store 编排"方法迁到 composable。
 * 负责 Studio 文档级 lifecycle（initForCreate / initForEdit / updateModule /
 * saveDirectEdits / createSkill）+ 派生（doc / isCreate / hasContent / moduleList）。
 *
 * state 真相源：
 * - `skillId / studioLoaded / studioSaving / studioCreateForm` → workbench store
 * - `dirty / dirtyModules` → document store（消灭 ide 自维护的副本）
 * - `studioMode / studioActiveModule` → ui store
 */

type ModuleKey = 'overview' | 'meta' | 'goal' | 'rules' | 'params' | 'output_table' | 'todos' | 'test_cases' | 'workflow'
type EditableModuleKey = Exclude<ModuleKey, 'overview'>

const MODULES: ReadonlyArray<{ key: ModuleKey; label: string; icon: string }> = [
  { key: 'overview', label: '概览', icon: 'IconHome' },
  { key: 'meta', label: '基础信息', icon: 'IconIdcard' },
  { key: 'goal', label: '目标', icon: 'IconBulb' },
  { key: 'rules', label: '规则', icon: 'IconList' },
  { key: 'params', label: '参数', icon: 'IconSettings' },
  { key: 'output_table', label: '输出', icon: 'IconFile' },
  { key: 'todos', label: '待办', icon: 'IconCheckCircle' },
  { key: 'test_cases', label: '测试', icon: 'IconExperiment' },
  { key: 'workflow', label: '工作流', icon: 'IconShareAlt' },
]

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

type WorkbenchFacade = {
  skillId: string
  studioLoaded: boolean
  studioSaving: boolean
  studioCreateForm: SkillStudioCreateForm
  reset: (options?: { keepSkillId?: boolean }) => void
  setSkillDocument?: (doc: SkillDocument) => void
  snapshotMessagesForSkill?: (id: string) => void
  pausePersist?: () => void
  resumePersist?: () => void
  restoreMessagesForSkill?: (id: string) => boolean
  loadChatForSkill?: (id: string) => boolean
  messages?: unknown[]
  resetStudioCreateForm: () => void
}

type UIFacade = {
  studioMode: 'create' | 'edit'
  studioActiveModule: ModuleKey
  studioBlockValidation: Record<string, unknown>
  setStudioMode: (m: 'create' | 'edit') => void
  setStudioActiveModule: (k: string) => void
  resetStudioBlockValidation: () => void
}

type DocumentModulePayloadMap = {
  meta: SkillMeta
  goal: string
  rules: SkillRuleStep[]
  params: SkillParam[]
  output_table: SkillOutputField[]
  todos: SkillTodoSpec[]
  test_cases: SkillTestCase[]
  workflow: SkillWorkflowPreview
}

type DocumentFacade = {
  flatDocument: SkillDocument
  dirty: boolean
  dirtyModules: Set<string>
  reset: () => void
  loadFromApi: (data: StructuredSkillResponse & Record<string, unknown>) => void
  markSaved: () => void
  setFromFlatDocument: (doc: Partial<SkillDocument>, opts?: { markDirty?: boolean }) => void
  updateModuleStructured: <K extends EditableModuleKey>(key: K, value: DocumentModulePayloadMap[K]) => void
  toStructuredPayloadData: () => Record<string, unknown>
}

function getDocumentSkillApi(api: DocumentSkillApi | SkillStudioSkillApi): DocumentSkillApi {
  if (typeof (api as Partial<DocumentSkillApi>).saveStructured !== 'function' || typeof (api as Partial<DocumentSkillApi>).create !== 'function') {
    throw new Error('SkillStudio requires saveStructured/create on skillApi')
  }
  return api as DocumentSkillApi
}

export function useSkillStudioDocument(deps: {
  wb: WorkbenchFacade
  ui: UIFacade
  documentStore: DocumentFacade
  skillApi: DocumentSkillApi | SkillStudioSkillApi
}) {
  const skillApi = getDocumentSkillApi(deps.skillApi)

  const { wb, ui, documentStore } = deps

  const doc = computed<SkillDocument>(() => documentStore.flatDocument)
  const isCreate = computed(() => ui.studioMode === 'create')
  const hasContent = computed(() => {
    const d = doc.value
    return !!(d?.goal || d?.rules?.length || d?.params?.length)
  })

  function getModuleStats(key: ModuleKey) {
    const d = doc.value
    if (!d) return { hasContent: false, count: 0, summary: '' }
    switch (key) {
      case 'overview':
        return { hasContent: true, count: 0, summary: 'Skill 概览' }
      case 'meta':
        return { hasContent: !!d.meta?.name, count: d.meta?.name ? 1 : 0, summary: d.meta?.name || '未填写' }
      case 'goal':
        return { hasContent: !!d.goal, count: d.goal ? 1 : 0, summary: d.goal?.slice(0, 40) || '未填写' }
      case 'rules':
        return { hasContent: (d.rules?.length ?? 0) > 0, count: d.rules?.length ?? 0, summary: `${d.rules?.length ?? 0} 条规则` }
      case 'params':
        return { hasContent: (d.params?.length ?? 0) > 0, count: d.params?.length ?? 0, summary: `${d.params?.length ?? 0} 个参数` }
      case 'output_table':
        return { hasContent: (d.output_table?.length ?? 0) > 0, count: d.output_table?.length ?? 0, summary: `${d.output_table?.length ?? 0} 个字段` }
      case 'todos':
        return { hasContent: (d.todos?.length ?? 0) > 0, count: d.todos?.length ?? 0, summary: (d.todos?.length ?? 0) ? `${d.todos?.length ?? 0} 个待办模板` : '未配置待办' }
      case 'test_cases':
        return { hasContent: (d.test_cases?.length ?? 0) > 0, count: d.test_cases?.length ?? 0, summary: `${d.test_cases?.length ?? 0} 个样例` }
      case 'workflow': {
        const nodes = d.workflow?.nodes?.length || 0
        return { hasContent: nodes > 0, count: nodes, summary: nodes ? `${nodes} 个节点` : '暂无' }
      }
      default:
        return { hasContent: false, count: 0, summary: '' }
    }
  }

  const moduleList = computed(() => MODULES.map((m) => {
    const stats = getModuleStats(m.key)
    return {
      ...m,
      active: ui.studioActiveModule === m.key,
      hasContent: stats.hasContent,
      count: stats.count,
      summary: stats.summary,
      dirty: documentStore.dirtyModules.has(m.key),
      validation: (ui.studioBlockValidation as Record<string, { status?: string } | undefined>)[m.key] || { status: 'idle' },
    }
  }))

  function setActiveModule(key: string): void {
    ui.setStudioActiveModule(key)
  }

  async function initForCreate(): Promise<void> {
    ui.setStudioMode('create')
    wb.skillId = ''
    wb.studioLoaded = true
    documentStore.dirty = false
    documentStore.dirtyModules.clear()
    ui.setStudioActiveModule('meta')
    ui.resetStudioBlockValidation()
    wb.resetStudioCreateForm()
    if (typeof wb.reset === 'function') wb.reset({ keepSkillId: false })
    documentStore.reset()
  }

  async function initForEdit(id: string): Promise<void> {
    // 若从同一 skill 的其它页面切回来（SkillStudio unmount→remount），保留 workbench
    // 会话状态（messages / patches / references ...）—— 让流式中的 AI 对话无缝续上。
    const prevSkillId = wb.skillId
    const sameSkillReentry = prevSkillId === id && ui.studioMode === 'edit'

    // 跨 skill 切换：离开前把当前 messages 快照到 messagesBySkillId[prevSkillId]，
    // 回来时再从缓存恢复。这样 A → B → A 也能保留 A 的对话。
    if (!sameSkillReentry && prevSkillId && typeof wb.snapshotMessagesForSkill === 'function') {
      wb.snapshotMessagesForSkill(prevSkillId)
    }

    ui.setStudioMode('edit')
    wb.skillId = id
    wb.studioLoaded = false
    documentStore.dirty = false
    documentStore.dirtyModules.clear()
    ui.setStudioActiveModule('overview')
    if (!sameSkillReentry && typeof wb.reset === 'function') {
      if (typeof wb.pausePersist === 'function') wb.pausePersist()
      try {
        wb.skillId = id
        wb.reset({ keepSkillId: true })
        const restored = typeof wb.restoreMessagesForSkill === 'function'
          ? wb.restoreMessagesForSkill(id)
          : false
        if (!restored && typeof wb.loadChatForSkill === 'function') {
          wb.loadChatForSkill(id)
        }
      } finally {
        if (typeof wb.resumePersist === 'function') wb.resumePersist()
      }
    } else if (sameSkillReentry && typeof wb.loadChatForSkill === 'function') {
      if ((wb.messages?.length || 0) === 0) {
        wb.loadChatForSkill(id)
      }
    }

    try {
      const data = await skillApi.get(id)
      if (isStructuredSkillResponse(data)) {
        documentStore.loadFromApi(data)
      }
      if (typeof wb.setSkillDocument === 'function') wb.setSkillDocument(documentStore.flatDocument)
      if (documentStore.flatDocument?.meta) {
        Object.assign(wb.studioCreateForm, {
          skill_id: id,
          name: documentStore.flatDocument.meta.name || wb.studioCreateForm.name,
          department: documentStore.flatDocument.meta.department || wb.studioCreateForm.department,
          role: documentStore.flatDocument.meta.role || wb.studioCreateForm.role,
          trigger_type: documentStore.flatDocument.meta.trigger_type || wb.studioCreateForm.trigger_type,
          trigger_expression: documentStore.flatDocument.meta.trigger_expression || wb.studioCreateForm.trigger_expression,
          risk_level: documentStore.flatDocument.meta.risk_level || wb.studioCreateForm.risk_level,
          approval_level: Number(documentStore.flatDocument.meta.approval_level) || wb.studioCreateForm.approval_level,
        })
      }
    } finally {
      wb.studioLoaded = true
    }
  }

  function updateModule(key: EditableModuleKey | 'overview', value: unknown): void {
    switch (key) {
      case 'meta':
        if (!isRecord(value)) break
        documentStore.updateModuleStructured('meta', {
          ...(documentStore.flatDocument?.meta || {}),
          ...(value as SkillMeta),
        })
        Object.assign(wb.studioCreateForm, {
          skill_id: typeof value.skill_id === 'string' ? value.skill_id : wb.studioCreateForm.skill_id,
          name: typeof value.name === 'string' ? value.name : wb.studioCreateForm.name,
          department: typeof value.department === 'string' ? value.department : wb.studioCreateForm.department,
          role: typeof value.role === 'string' ? value.role : wb.studioCreateForm.role,
          trigger_type: typeof value.trigger_type === 'string' ? value.trigger_type : wb.studioCreateForm.trigger_type,
          trigger_expression: typeof value.trigger_expression === 'string' ? value.trigger_expression : wb.studioCreateForm.trigger_expression,
          risk_level: typeof value.risk_level === 'string' ? value.risk_level : wb.studioCreateForm.risk_level,
          approval_level: typeof value.approval_level === 'number'
            ? value.approval_level
            : wb.studioCreateForm.approval_level,
        })
        break
      case 'goal':
        if (typeof value === 'string') documentStore.updateModuleStructured('goal', value)
        break
      case 'rules':
        if (Array.isArray(value)) documentStore.updateModuleStructured('rules', value)
        break
      case 'params':
        if (Array.isArray(value)) documentStore.updateModuleStructured('params', value)
        break
      case 'output_table':
        if (Array.isArray(value)) documentStore.updateModuleStructured('output_table', value)
        break
      case 'todos':
        if (Array.isArray(value)) documentStore.updateModuleStructured('todos', value)
        break
      case 'test_cases':
        if (Array.isArray(value)) documentStore.updateModuleStructured('test_cases', value)
        break
      case 'workflow':
        if (isRecord(value)) documentStore.updateModuleStructured('workflow', value as SkillWorkflowPreview)
        break
    }
    if (typeof wb.setSkillDocument === 'function') wb.setSkillDocument(documentStore.flatDocument)
  }

  async function saveDirectEdits(): Promise<void> {
    if (!wb.skillId || !documentStore.dirty) return
    wb.studioSaving = true
    try {
      const payload = documentStore.toStructuredPayloadData()
      await skillApi.saveStructured(wb.skillId, payload)
      documentStore.markSaved()
    } finally {
      wb.studioSaving = false
    }
  }

  async function createSkill(): Promise<string> {
    wb.studioSaving = true
    try {
      const d = documentStore.flatDocument
      const createForm = wb.studioCreateForm
      const createFormMeta = Object.fromEntries(
        Object.entries(createForm).filter(([, value]) => value !== undefined && value !== null && value !== ''),
      )
      const meta = { ...createFormMeta, ...(d.meta || {}) }
      const sid =
        String(meta.skill_id || meta.id || '').trim()
        || String(meta.name || '').trim().replace(/\s+/g, '-')
        || `skill-${Date.now()}`
      const skillMdMeta = { ...meta }
      delete (skillMdMeta as Record<string, unknown>).skill_id
      delete (skillMdMeta as Record<string, unknown>).id

      const createPayload = {
        skill_id: sid,
        name: meta.name || sid,
        department: meta.department || '未指定',
        role: meta.role || '',
        trigger_type: meta.trigger_type || 'manual',
        risk_level: meta.risk_level || 'R2',
        skill_md: documentToMarkdown({ ...d, meta: skillMdMeta }),
        policy_pack: {},
      }
      await skillApi.create(createPayload)

      wb.skillId = sid
      documentStore.setFromFlatDocument({ ...d, meta }, { markDirty: true })
      if (typeof wb.setSkillDocument === 'function') wb.setSkillDocument(documentStore.flatDocument)
      const payload = documentStore.toStructuredPayloadData()
      await skillApi.saveStructured(sid, payload)

      const artifacts = (d?.custom_sections as Record<string, unknown> | undefined)?.__artifacts as Record<string, unknown> | undefined || {}
      for (const [path, rawContent] of Object.entries(artifacts)) {
        if (!path || rawContent === undefined || rawContent === null || rawContent === '') continue
        const content = typeof rawContent === 'string' ? rawContent : JSON.stringify(rawContent, null, 2)
        await skillApi.saveFile(sid, path, content)
      }

      const draftId = ((d?.custom_sections as Record<string, unknown> | undefined)?.__task_contract_meta as Record<string, unknown> | undefined)?.draft_id
      if (draftId) {
        try {
          const { workbenchApi } = await import('@/api')
          await workbenchApi.bindTaskContract(String(draftId), { skill_id: sid })
        } catch {
          // 绑定失败不阻断创建；文件侧产物仍已保存。
        }
      }

      ui.setStudioMode('edit')
      documentStore.markSaved()
      return sid
    } finally {
      wb.studioSaving = false
    }
  }

  return {
    doc,
    isCreate,
    hasContent,
    moduleList,
    MODULES,
    getModuleStats,
    setActiveModule,
    initForCreate,
    initForEdit,
    updateModule,
    saveDirectEdits,
    createSkill,
  }
}
