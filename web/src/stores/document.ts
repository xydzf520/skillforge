import { defineStore } from 'pinia'
import { computed, reactive, ref } from 'vue'
import { fromSkillResponse, toStructuredPayload } from '@/utils/schemaMapper'
import { documentToMarkdown, markdownToDocument } from '@/utils/markdownParser'
import type {
  SkillCustomSections,
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
import type { WorkbenchPatchDraft } from '@/types/skillstudio'

const MODULE_KEYS = ['meta', 'goal', 'rules', 'params', 'output_table', 'todos', 'test_cases', 'workflow'] as const
type ModuleKey = typeof MODULE_KEYS[number]
type DocumentKey = ModuleKey | 'custom_sections'
type FlatDocument = SkillDocument & {
  antipatterns: unknown[]
  data_inputs: unknown[]
}
type FileEntry = {
  dirty?: boolean
  [key: string]: unknown
}
type ParseErrorEntry = {
  module: ModuleKey
  message: string
}
type SelectionState = {
  moduleId: string | null
  startLine: number | null
  endLine: number | null
  text: string
}
type DocumentState = {
  meta: DocumentModule<SkillMeta>
  goal: DocumentModule<string>
  rules: DocumentModule<SkillRuleStep[]>
  params: DocumentModule<SkillParam[]>
  output_table: DocumentModule<SkillOutputField[]>
  todos: DocumentModule<SkillTodoSpec[]>
  test_cases: DocumentModule<SkillTestCase[]>
  workflow: DocumentModule<SkillWorkflowPreview>
  custom_sections: SkillCustomSections
}

type DocumentModule<T> = {
  structuredValue: T
  rawMarkdown: string
  sourceRange: unknown
}

function createEmptyModule<T>(defaultValue: T): DocumentModule<T> {
  return {
    structuredValue: defaultValue,
    rawMarkdown: '',
    sourceRange: null,
  }
}

function createEmptyDocument(): DocumentState {
  return {
    meta: createEmptyModule({
      id: '', name: '', description: '', department: '',
      owner: '', status: 'draft', version: '',
      trigger_type: '', trigger_expression: '',
      risk_level: '', approval_level: '',
    }),
    goal: createEmptyModule(''),
    rules: createEmptyModule<SkillRuleStep[]>([]),
    params: createEmptyModule<SkillParam[]>([]),
    output_table: createEmptyModule<SkillOutputField[]>([]),
    todos: createEmptyModule<SkillTodoSpec[]>([]),
    test_cases: createEmptyModule<SkillTestCase[]>([]),
    workflow: createEmptyModule<SkillWorkflowPreview>({ nodes: [], edges: [], bindings: [] }),
    custom_sections: {} as SkillCustomSections,
  }
}

function createEmptyWorkflow(): SkillWorkflowPreview {
  return { nodes: [], edges: [], bindings: [] }
}

function cloneValue<T>(value: unknown, fallback: T): T {
  if (value === undefined || value === null) return fallback
  try {
    return JSON.parse(JSON.stringify(value)) as T
  } catch {
    return fallback
  }
}

export const useDocumentStore = defineStore('document', () => {
  const skillDocument = ref(createEmptyDocument())
  const files = reactive<Record<string, FileEntry>>({})
  const parseState = ref<'idle' | 'parsing' | 'parsed' | 'error'>('idle')
  const parseErrors = ref<ParseErrorEntry[]>([])
  const dirty = ref(false)
  const dirtyModules = reactive(new Set<string>())
  const lastSavedAt = ref<string | null>(null)
  const activeModule = ref<ModuleKey>('meta')
  const selection = ref<SelectionState>({
    moduleId: null as string | null,
    startLine: null as number | null,
    endLine: null as number | null,
    text: '',
  })
  const draftRevision = ref(0)
  let _cachedMarkdown = ''

  const activeModuleData = computed(() => skillDocument.value[activeModule.value])
  const flatDocument = computed<FlatDocument>(() => _toFlatDoc())

  const moduleStates = computed(() =>
    MODULE_KEYS.map((key) => {
      const mod = skillDocument.value[key]
      if (!mod) return { key, status: 'empty', dirty: false }
      const sv = mod.structuredValue
      let hasContent = false
      if (Array.isArray(sv)) hasContent = sv.length > 0
      else if (typeof sv === 'string') hasContent = sv.length > 0
      else if (sv && typeof sv === 'object') hasContent = Object.values(sv).some((v) => v !== '' && v !== null && v !== undefined)

      let status = 'empty'
      if (hasContent) status = dirtyModules.has(key) ? 'modified' : 'ready'
      return { key, status, dirty: dirtyModules.has(key) }
    }),
  )

  const fullMarkdown = computed(() => {
    _cachedMarkdown = documentToMarkdown(_toFlatDoc())
    return _cachedMarkdown
  })

  const dirtyFileList = computed(() =>
    Object.entries(files).filter(([, f]) => Boolean(f.dirty)).map(([k]) => k),
  )

  function loadFromApi(apiResponse: StructuredSkillResponse & Record<string, unknown>): void {
    const mapped = fromSkillResponse(apiResponse)
    if (!mapped) return
    setFromFlatDocument({
      ...mapped,
      meta: {
        ...(mapped.meta || {}),
        id: typeof apiResponse.id === 'string' ? apiResponse.id : '',
        department: mapped.meta?.department || (typeof apiResponse.department === 'string' ? apiResponse.department : ''),
        status: typeof apiResponse.status === 'string' ? apiResponse.status : 'draft',
        version: typeof apiResponse.version === 'string' ? apiResponse.version : '',
      },
    })
  }

  function loadFromDraft(draftResponse: { skill?: SkillDocument } & Partial<SkillDocument>): void {
    const draftPayload = draftResponse
    const skill = draftPayload.skill || draftPayload
    setFromFlatDocument({
      ...flatDocument.value,
      ...skill,
      meta: { ...(flatDocument.value.meta || {}), ...(skill.meta || {}) },
    })
  }

  function updateModuleStructured<K extends ModuleKey>(moduleKey: K, newValue: DocumentState[K]['structuredValue']): void {
    const mod = skillDocument.value[moduleKey]
    if (!mod) return
    mod.structuredValue = newValue
    dirtyModules.add(moduleKey)
    dirty.value = true
    draftRevision.value++
  }

  function updateModuleRaw(moduleKey: ModuleKey, newRawMarkdown: string): void {
    const mod = skillDocument.value[moduleKey]
    if (!mod) return
    mod.rawMarkdown = newRawMarkdown
    try {
      const parsed = markdownToDocument(newRawMarkdown)
      if (parsed && parsed[moduleKey] !== undefined) {
        mod.structuredValue = parsed[moduleKey] as DocumentState[typeof moduleKey]['structuredValue']
      }
    } catch {
      parseErrors.value.push({ module: moduleKey, message: '解析失败，保留原始内容' })
    }
    dirtyModules.add(moduleKey)
    dirty.value = true
    draftRevision.value++
  }

  function updateFullMarkdown(newMarkdown: string): void {
    try {
      const parsed = markdownToDocument(newMarkdown)
      if (!parsed) return
      const doc = skillDocument.value
      if (parsed.meta) doc.meta.structuredValue = { ...doc.meta.structuredValue, ...parsed.meta }
      if (parsed.goal !== undefined) doc.goal.structuredValue = parsed.goal
      if (parsed.rules) doc.rules.structuredValue = parsed.rules
      if (parsed.output_table) doc.output_table.structuredValue = parsed.output_table
      if (parsed.todos) doc.todos.structuredValue = parsed.todos
      if (parsed.test_cases) doc.test_cases.structuredValue = parsed.test_cases
      if (parsed.custom_sections) doc.custom_sections = parsed.custom_sections
      MODULE_KEYS.forEach((k) => dirtyModules.add(k))
      dirty.value = true
      draftRevision.value++
      parseState.value = 'parsed'
    } catch {
      parseState.value = 'error'
    }
  }

  function applyPatch(patch: WorkbenchPatchDraft & { patch_json?: Record<string, unknown> }): void {
    const target = patch.target_module as ModuleKey | undefined
    if (!target || !MODULE_KEYS.includes(target)) return
    const patchData = patch.patch_json || patch.patch || {}
    const mod = skillDocument.value[target]
    if (!mod) return
    if ((patchData as Record<string, unknown>)[target] !== undefined) {
      mod.structuredValue = (patchData as Record<string, unknown>)[target] as DocumentState[typeof target]['structuredValue']
    } else if (Object.keys(patchData).length) {
      mod.structuredValue = patchData as DocumentState[typeof target]['structuredValue']
    }
    dirtyModules.add(target)
    dirty.value = true
    draftRevision.value++
  }

  function setFromFlatDocument(source: Partial<FlatDocument>, options: { markDirty?: boolean } = {}): void {
    const raw = cloneValue<Partial<FlatDocument>>(source, {})
    const doc = skillDocument.value

    doc.meta.structuredValue = {
      ...createEmptyDocument().meta.structuredValue,
      ...(cloneValue<SkillMeta>(raw.meta, {} as SkillMeta)),
    }
    doc.goal.structuredValue = raw.goal || ''
    doc.rules.structuredValue = Array.isArray(raw.rules) ? cloneValue<SkillRuleStep[]>(raw.rules, []) : []
    doc.params.structuredValue = Array.isArray(raw.params) ? cloneValue<SkillParam[]>(raw.params, []) : []
    doc.output_table.structuredValue = Array.isArray(raw.output_table) ? cloneValue<SkillOutputField[]>(raw.output_table, []) : []
    doc.todos.structuredValue = Array.isArray(raw.todos) ? cloneValue<SkillTodoSpec[]>(raw.todos, []) : []
    doc.test_cases.structuredValue = Array.isArray(raw.test_cases) ? cloneValue<SkillTestCase[]>(raw.test_cases, []) : []
    doc.workflow.structuredValue = cloneValue<SkillWorkflowPreview>(raw.workflow, createEmptyWorkflow())
    doc.custom_sections = cloneValue<SkillCustomSections>(raw.custom_sections, {} as SkillCustomSections)

    parseState.value = 'parsed'
    if (options.markDirty) {
      dirty.value = true
      MODULE_KEYS.forEach((key) => dirtyModules.add(key))
      draftRevision.value++
    } else {
      dirty.value = false
      dirtyModules.clear()
      draftRevision.value = 0
    }
  }

  function setSelection(moduleId: string | null, startLine: number | null, endLine: number | null, text: string): void {
    selection.value = { moduleId, startLine, endLine, text }
  }

  function setActiveModule(key: ModuleKey): void {
    if (MODULE_KEYS.includes(key)) activeModule.value = key
  }

  function toStructuredPayloadData(): Record<string, unknown> {
    return toStructuredPayload(_toFlatDoc())
  }

  function markSaved(): void {
    dirty.value = false
    dirtyModules.clear()
    lastSavedAt.value = new Date().toISOString()
  }

  function reset(): void {
    Object.assign(skillDocument.value, createEmptyDocument())
    Object.keys(files).forEach((k) => delete files[k])
    parseState.value = 'idle'
    parseErrors.value = []
    dirty.value = false
    dirtyModules.clear()
    lastSavedAt.value = null
    activeModule.value = 'meta'
    selection.value = { moduleId: null, startLine: null, endLine: null, text: '' }
    draftRevision.value = 0
  }

  function _toFlatDoc(): FlatDocument {
    const doc = skillDocument.value
    return {
      meta: doc.meta.structuredValue,
      goal: doc.goal.structuredValue,
      rules: doc.rules.structuredValue,
      params: doc.params.structuredValue,
      output_table: doc.output_table.structuredValue,
      todos: doc.todos.structuredValue,
      test_cases: doc.test_cases.structuredValue,
      workflow: doc.workflow.structuredValue,
      antipatterns: [],
      data_inputs: [],
      custom_sections: doc.custom_sections,
    }
  }

  return {
    skillDocument, files,
    parseState, parseErrors, dirty, dirtyModules, lastSavedAt,
    activeModule, selection, draftRevision,
    activeModuleData, moduleStates, fullMarkdown, dirtyFileList, flatDocument,
    loadFromApi, loadFromDraft,
    updateModuleStructured, updateModuleRaw, updateFullMarkdown,
    applyPatch, setSelection, setActiveModule, setFromFlatDocument,
    toStructuredPayloadData, markSaved, reset,
  }
})
