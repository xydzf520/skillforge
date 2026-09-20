/// <reference types="vite/client" />

// 构建时由 vite.config.ts 注入，取自 CHANGELOG.md 顶部版本号
declare const __APP_VERSION__: string

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}

// Node.js 的 clearTimeout/clearInterval 不接受 null，但浏览器环境可以。
// 项目中大量使用 `let timer: ReturnType<typeof setTimeout> | null = null` 模式，
// 这里扩展全局声明使其兼容 null 参数。
declare function clearTimeout(id: ReturnType<typeof setTimeout> | null | undefined): void
declare function clearInterval(id: ReturnType<typeof setInterval> | null | undefined): void

declare module 'monaco-editor/esm/vs/editor/editor.api' {
  const monaco: any
  export = monaco
}

declare module 'monaco-editor/esm/vs/language/json/monaco.contribution' {
  const contribution: any
  export default contribution
}

declare module 'monaco-editor/esm/vs/basic-languages/python/python.contribution' {
  const contribution: any
  export default contribution
}

declare module 'monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution' {
  const contribution: any
  export default contribution
}
