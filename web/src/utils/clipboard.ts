/**
 * 跨环境剪贴板工具。
 *
 * navigator.clipboard.writeText() 只在 secure context（HTTPS / localhost / 127.0.0.1 / file://）
 * 下可用。SkillForge 非 HTTPS 部署（例如 http://127.0.0.1:2116）下直接调会
 * throw/静默失败 —— 本工具提供 execCommand('copy') fallback，保证复制能成功。
 *
 * 调用方：`if (await copyText(text)) { Message.success('已复制') } else { Message.warning('复制失败') }`
 */
export async function copyText(text: string): Promise<boolean> {
  if (!text) return false

  // 优先：现代 Clipboard API（HTTPS / localhost 下可用）
  if (typeof navigator !== 'undefined' && navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 继续 fallback
    }
  }

  // Fallback：临时 textarea + execCommand('copy')（deprecated 但 HTTP 下能用）
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.readOnly = true
    ta.style.position = 'fixed'
    ta.style.top = '-9999px'
    ta.style.left = '-9999px'
    ta.style.opacity = '0'
    ta.style.fontSize = '16px'
    document.body.appendChild(ta)
    const originalSelection = document.getSelection()?.rangeCount
      ? document.getSelection()?.getRangeAt(0)
      : null
    const sel = document.getSelection()
    ta.focus({ preventScroll: true })
    ta.select()
    ta.setSelectionRange(0, ta.value.length)
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    // 恢复原有选区，避免打断用户正在选中的文本
    sel?.removeAllRanges()
    if (originalSelection) {
      sel?.addRange(originalSelection)
    }
    return ok
  } catch {
    return false
  }
}
