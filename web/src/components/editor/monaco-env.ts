/**
 * Monaco Editor Web Worker 配置
 *
 * 显式加载 Monaco CSS，并且只引入当前页面实际需要的 API / 语言贡献 / worker。
 * skill-md 使用自定义 Monarch tokenizer，yaml/python 使用 basic-languages，不需要专用 worker。
 */

import 'monaco-editor/min/vs/editor/editor.main.css'
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api'
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import jsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker'

const monacoGlobal = globalThis as any
const existingMonacoEnvironment = monacoGlobal.MonacoEnvironment || {}

monacoGlobal.MonacoEnvironment = {
  ...existingMonacoEnvironment,
  getWorker(_: string, label: string) {
    if (label === 'json') return new jsonWorker()
    return new editorWorker()
  },
}

const languageSupportLoaders: Record<string, () => Promise<unknown>> = {
  json: () => import('monaco-editor/esm/vs/language/json/monaco.contribution'),
  python: () => import('monaco-editor/esm/vs/basic-languages/python/python.contribution'),
  yaml: () => import('monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution'),
}

const languageSupportPromises = new Map<string, Promise<void>>()

export function ensureMonacoLanguageSupport(language: string): Promise<void> {
  const loadLanguageSupport = languageSupportLoaders[language]
  if (!loadLanguageSupport) return Promise.resolve()

  if (!languageSupportPromises.has(language)) {
    languageSupportPromises.set(
      language,
      loadLanguageSupport().then(() => undefined)
    )
  }

  return languageSupportPromises.get(language)!
}

export { monaco }
