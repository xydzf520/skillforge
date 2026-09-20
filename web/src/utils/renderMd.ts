/**
 * Markdown → HTML 渲染（用于聊天消息气泡）。
 *
 * 用 marked + DOMPurify：完整支持表格、标题、列表、代码块、链接、引用。
 * AI 助手输出经常用 markdown 表格和代码块，必须能正确渲染，否则用户看到的就是
 * 一坨纯文本。
 */
import { marked } from 'marked'
import DOMPurify from 'dompurify'

// marked 14 配置：GFM (GitHub Flavored Markdown，含表格)、断行 = 软换行
marked.setOptions({
  gfm: true,
  breaks: true,           // 单换行 = <br>，符合 chat 场景的习惯
  pedantic: false,
  silent: true,           // 解析失败不抛，返回原文
})

// DOMPurify 白名单：允许常见 markdown 产物 + 表格 + 代码块
// 不允许 script / iframe / style / on* 事件属性
// [M6] 限制 href / src 协议白名单, 拒绝 javascript: / data: / vbscript: 等危险 URL
const PURIFY_OPTIONS = {
  ALLOWED_TAGS: [
    'p', 'br', 'strong', 'em', 'del', 'code', 'pre',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li',
    'blockquote', 'hr',
    'a', 'span', 'div',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
  ],
  ALLOWED_ATTR: ['href', 'title', 'target', 'rel', 'class', 'alt', 'align'],
  ALLOW_DATA_ATTR: false,
  // [M6] 协议白名单 — 仅允许 http(s) 和相对路径; 拒绝 javascript: / data: / vbscript: / file:
  // DOMPurify 默认 ALLOWED_URI_REGEXP 已较严格, 但显式覆盖更稳定避免依赖默认值变化
  ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto):|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
}

export function renderMd(text: string | null | undefined): string {
  if (!text) return ''
  try {
    const html = marked.parse(String(text)) as string
    return DOMPurify.sanitize(html, PURIFY_OPTIONS)
  } catch {
    // 兜底：原文转义后逐行换 <br>，至少不丢内容
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\n/g, '<br>')
  }
}
