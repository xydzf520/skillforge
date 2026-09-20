/**
 * monacopilot AI 补全集成
 *
 * 两种触发模式同时生效：
 * 1. onIdle — 停止输入后自动触发
 * 2. 快捷键 — Ctrl+Shift+Space 手动触发
 */

import { registerCompletion } from 'monacopilot'

type AICompletionOptions = {
  filename?: string
  language?: string
}

/**
 * 为 Monaco Editor 实例注册 AI 补全
 */
export function setupAICompletion(monaco: any, editor: any, options: AICompletionOptions = {}): any {
  const { filename = 'SKILL.md', language = 'skill-md' } = options

  const completion = registerCompletion(monaco, editor, {
    endpoint: '/api/editor/completions',
    language,
    filename,
    trigger: 'onIdle',
  })

  // 注册快捷键手动触发
  if (completion && typeof completion.trigger === 'function') {
    // Ctrl+Shift+Space
    editor.addAction({
      id: 'ai-completion-trigger',
      label: 'AI 补全',
      keybindings: [
        monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.Space,
      ],
      run: () => completion.trigger(),
    })
    // Tab 也可触发（当没有 suggest widget 和 ghost text 时）
    editor.addAction({
      id: 'ai-completion-tab',
      label: 'AI 补全 (Tab)',
      keybindings: [monaco.KeyCode.Tab],
      precondition: 'editorTextFocus && !suggestWidgetVisible && !inlineSuggestionVisible',
      run: () => completion.trigger(),
    })
  }

  return completion
}

/**
 * 销毁 AI 补全实例
 */
export function teardownAICompletion(completion: any): void {
  if (completion && typeof completion.deregister === 'function') {
    completion.deregister()
  }
}
