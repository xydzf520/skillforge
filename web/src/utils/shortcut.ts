/**
 * 快捷键文案工具：根据平台自动选择 Mac 符号（⌘/⇧）或 Windows/Linux 文字（Ctrl+ / Shift+）。
 *
 * 用法：
 *   import { metaKey, shiftKey } from '@/utils/shortcut'
 *   `面板 (${metaKey}J)`        // Mac: 面板 (⌘J)   非 Mac: 面板 (Ctrl+J)
 *   `保存 (${metaKey}${shiftKey}S)` // Mac: 保存 (⌘⇧S) 非 Mac: 保存 (Ctrl+Shift+S)
 */

const isMac =
  typeof navigator !== 'undefined' &&
  /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent || '')

export const metaKey = isMac ? '⌘' : 'Ctrl+'
export const shiftKey = isMac ? '⇧' : 'Shift+'
export const isMacPlatform = isMac
