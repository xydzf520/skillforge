/**
 * Studio 键盘快捷键（O13 拆分第 3 个 composable）
 *
 * 从 SkillStudio.vue 抽出的独立单元：
 *   ⌘P → 命令面板
 *   ⌘K → inline prompt
 *   ⌘S → 保存（或新建时创建）
 *   ⌘B → 左侧 navigator
 *   ⌘J → 底部面板
 *   ⌘⇧A → 右侧 assistant
 *   Esc → 关命令面板 / 退出全屏
 *
 * 使用 `onMounted` / `onBeforeUnmount` 自动装/卸；父组件只需提供一批 callback。
 */
import { onBeforeUnmount, onMounted } from 'vue'

export interface StudioKeyboardDeps {
  ui: {
    toggleCommandPalette: () => void
    inlinePromptVisible: boolean
    toggleNavigator: () => void
    toggleBottomPanel: (tab?: string) => void
    toggleAssistant: () => void
    toggleFullscreen: () => void
    commandPaletteOpen: boolean
    fullscreen: boolean
  } | any
  /** 是否处于"可保存"模式（非 explain / 非 review）；false 时 ⌘S 会弹 warning */
  isWritable: () => boolean
  /** isCreate ? create() : save() */
  onSaveOrCreate: () => void
  /** 非可写模式下尝试保存时的提示文案 */
  notWritableHint: () => string
  /** Message.warning 函数（通常是 arco Message.warning） */
  warn: (msg: string) => void
}

export function useStudioKeyboard(deps: StudioKeyboardDeps) {
  function onKeydown(e: KeyboardEvent) {
    const mod = e.metaKey || e.ctrlKey
    if (mod && e.key === 'p') { e.preventDefault(); deps.ui.toggleCommandPalette() }
    if (mod && e.key === 'k') { e.preventDefault(); deps.ui.inlinePromptVisible = true }
    if (mod && e.key === 's') {
      e.preventDefault()
      if (!deps.isWritable()) {
        deps.warn(deps.notWritableHint())
        return
      }
      deps.onSaveOrCreate()
    }
    if (mod && e.key === 'b') { e.preventDefault(); deps.ui.toggleNavigator() }
    if (mod && e.key === 'j') { e.preventDefault(); deps.ui.toggleBottomPanel() }
    if (mod && e.shiftKey && (e.key === 'A' || e.key === 'a')) { e.preventDefault(); deps.ui.toggleAssistant() }
    if (e.key === 'Escape') {
      if (deps.ui.commandPaletteOpen) deps.ui.commandPaletteOpen = false
      else if (deps.ui.fullscreen) deps.ui.toggleFullscreen()
    }
  }

  onMounted(() => window.addEventListener('keydown', onKeydown))
  onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

  return { onKeydown }
}
