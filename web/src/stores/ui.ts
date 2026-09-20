import { defineStore } from 'pinia'
import { reactive, ref, watch, type Ref } from 'vue'

type ViewMode = 'block' | 'code' | 'grid'
type StudioPerspective = 'edit' | 'explain' | 'review'
type StudioMode = 'create' | 'edit'
type StudioModuleKey = 'overview' | 'meta' | 'goal' | 'rules' | 'params' | 'output_table' | 'todos' | 'test_cases' | 'workflow'
const STUDIO_MODULE_KEYS: readonly StudioModuleKey[] = ['overview', 'meta', 'goal', 'rules', 'params', 'output_table', 'todos', 'test_cases', 'workflow']

type BlockValidationState = { status?: string;[key: string]: unknown }

type StoredUiState = {
  viewMode?: ViewMode
  studioPerspective?: StudioPerspective
  editorTheme?: string
  editorFontSize?: number
  assistantPaneOpen?: boolean
  assistantPaneWidth?: number
  assistantWidth?: number
  bottomPanelOpen?: boolean
  bottomPanelHeight?: number
  bottomPanelTab?: string
  bottomMaximized?: boolean
  navigatorOpen?: boolean
  navigatorWidth?: number
  navigatorSection?: string
}

const STORAGE_KEY = 'sf-wb-ui'

function loadFromStorage(): StoredUiState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) as StoredUiState : {}
  } catch {
    return {}
  }
}

export const useUIStore = defineStore('ui', () => {
  const saved = loadFromStorage()

  const viewMode = ref<ViewMode>(saved.viewMode || 'block')
  const studioPerspective = ref<StudioPerspective>(saved.studioPerspective || 'edit')
  const editorTheme = ref<string>(saved.editorTheme ?? (localStorage.getItem('sf-editor-dark') === 'true' ? 'dark' : 'light'))
  const editorFontSize = ref<number>(saved.editorFontSize || parseInt(localStorage.getItem('sf-editor-font-size') || '14', 10) || 14)

  const assistantPaneOpen = ref<boolean>(saved.assistantPaneOpen !== false)
  const assistantPaneWidth = ref<number>(saved.assistantPaneWidth || 380)
  const assistantWidth = ref<number>(saved.assistantWidth || saved.assistantPaneWidth || 560)
  const bottomPanelOpen = ref<boolean>(saved.bottomPanelOpen || false)
  const bottomPanelHeight = ref<number>(saved.bottomPanelHeight || 250)
  const bottomPanelTab = ref<string>(saved.bottomPanelTab || 'validation')
  const bottomMaximized = ref<boolean>(saved.bottomMaximized || false)
  const navigatorOpen = ref<boolean>(saved.navigatorOpen !== false)
  const navigatorWidth = ref<number>(saved.navigatorWidth || 220)
  const navigatorSection = ref<string>(saved.navigatorSection || 'modules')

  const commandPaletteOpen = ref<boolean>(false)
  const fullscreen = ref<boolean>(false)
  const inlinePromptVisible = ref<boolean>(false)
  const inlinePromptPosition = ref<any>(null)

  // v2.5: SkillStudio UI 视图状态（从 deprecated useIDEStore 迁过来）
  const studioMode = ref<StudioMode>('create')
  const studioActiveModule = ref<StudioModuleKey>('meta')
  const studioBlockValidation = reactive<Record<string, BlockValidationState>>({})

  function setStudioMode(m: StudioMode): void {
    studioMode.value = m
  }

  function setStudioActiveModule(key: string): void {
    if ((STUDIO_MODULE_KEYS as readonly string[]).includes(key)) {
      studioActiveModule.value = key as StudioModuleKey
    }
  }

  function resetStudioBlockValidation(): void {
    Object.keys(studioBlockValidation).forEach((k) => { delete studioBlockValidation[k] })
  }

  function toggleAssistant(): void {
    assistantPaneOpen.value = !assistantPaneOpen.value
  }

  function toggleBottomPanel(tab?: string): void {
    if (!tab) {
      bottomPanelOpen.value = !bottomPanelOpen.value
    } else if (bottomPanelOpen.value && bottomPanelTab.value === tab) {
      bottomPanelOpen.value = false
    } else {
      bottomPanelOpen.value = true
      bottomPanelTab.value = tab
    }
  }

  function toggleNavigator(): void {
    navigatorOpen.value = !navigatorOpen.value
  }

  function setNavigatorWidth(w: number): void {
    navigatorWidth.value = Math.max(180, Math.min(400, Number(w) || 220))
  }

  function setAssistantWidth(w: number): void {
    const n = Math.max(300, Math.min(1200, Number(w) || 560))
    assistantWidth.value = n
    assistantPaneWidth.value = n
  }

  function toggleBottomMaximized(): void {
    bottomMaximized.value = !bottomMaximized.value
  }

  function toggleCommandPalette(): void {
    commandPaletteOpen.value = !commandPaletteOpen.value
  }

  function toggleFullscreen(): void {
    fullscreen.value = !fullscreen.value
    if (fullscreen.value) {
      document.documentElement.requestFullscreen?.()
    } else {
      document.exitFullscreen?.()
    }
  }

  function setViewMode(mode: ViewMode | string): void {
    if (mode === 'block' || mode === 'code' || mode === 'grid') viewMode.value = mode
  }

  function setStudioPerspective(perspective: StudioPerspective | string): void {
    if (perspective === 'edit' || perspective === 'explain' || perspective === 'review') {
      studioPerspective.value = perspective
    }
  }

  function setEditorTheme(theme: string): void {
    editorTheme.value = theme
    localStorage.setItem('sf-editor-dark', theme === 'dark' ? 'true' : 'false')
  }

  function setEditorFontSize(size: number): void {
    const s = Math.max(12, Math.min(24, size))
    editorFontSize.value = s
    localStorage.setItem('sf-editor-font-size', String(s))
  }

  function persistToStorage(): void {
    const data: StoredUiState = {
      viewMode: viewMode.value,
      studioPerspective: studioPerspective.value,
      editorTheme: editorTheme.value,
      editorFontSize: editorFontSize.value,
      assistantPaneOpen: assistantPaneOpen.value,
      assistantPaneWidth: assistantPaneWidth.value,
      assistantWidth: assistantWidth.value,
      bottomPanelOpen: bottomPanelOpen.value,
      bottomPanelHeight: bottomPanelHeight.value,
      bottomPanelTab: bottomPanelTab.value,
      bottomMaximized: bottomMaximized.value,
      navigatorOpen: navigatorOpen.value,
      navigatorWidth: navigatorWidth.value,
      navigatorSection: navigatorSection.value,
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
    try { localStorage.setItem('sf-ui', JSON.stringify(data)) } catch { /* ignore */ }
  }

  function restoreFromStorage(): void {
    const data = loadFromStorage()
    if (data.viewMode) viewMode.value = data.viewMode
    if (data.studioPerspective) studioPerspective.value = data.studioPerspective
    if (data.editorTheme) editorTheme.value = data.editorTheme
    if (data.editorFontSize) editorFontSize.value = data.editorFontSize
    if (data.assistantPaneOpen !== undefined) assistantPaneOpen.value = data.assistantPaneOpen
    if (data.assistantPaneWidth) assistantPaneWidth.value = data.assistantPaneWidth
    if (data.assistantWidth) assistantWidth.value = data.assistantWidth
    if (data.bottomPanelOpen !== undefined) bottomPanelOpen.value = data.bottomPanelOpen
    if (data.bottomPanelHeight) bottomPanelHeight.value = data.bottomPanelHeight
    if (data.bottomPanelTab) bottomPanelTab.value = data.bottomPanelTab
    if (data.bottomMaximized !== undefined) bottomMaximized.value = data.bottomMaximized
    if (data.navigatorOpen !== undefined) navigatorOpen.value = data.navigatorOpen
    if (data.navigatorWidth) navigatorWidth.value = data.navigatorWidth
    if (data.navigatorSection) navigatorSection.value = data.navigatorSection
  }

  watch(
    [
      viewMode,
      studioPerspective,
      assistantPaneOpen,
      assistantWidth,
      bottomPanelOpen,
      bottomPanelTab,
      bottomPanelHeight,
      bottomMaximized,
      navigatorOpen,
      navigatorWidth,
      navigatorSection,
    ] as Ref<unknown>[],
    () => persistToStorage(),
    { flush: 'post' },
  )

  return {
    viewMode, studioPerspective, editorTheme, editorFontSize,
    assistantPaneOpen, assistantPaneWidth, assistantWidth,
    bottomPanelOpen, bottomPanelHeight, bottomPanelTab, bottomMaximized,
    navigatorOpen, navigatorWidth, navigatorSection,
    commandPaletteOpen, fullscreen,
    inlinePromptVisible, inlinePromptPosition,
    studioMode, studioActiveModule, studioBlockValidation,
    toggleAssistant, toggleBottomPanel, toggleNavigator,
    setNavigatorWidth, setAssistantWidth, toggleBottomMaximized,
    toggleCommandPalette, toggleFullscreen,
    setViewMode, setStudioPerspective, setEditorTheme, setEditorFontSize,
    setStudioMode, setStudioActiveModule, resetStudioBlockValidation,
    persistToStorage, restoreFromStorage,
  }
})
