import { fileURLToPath, URL } from 'node:url'
import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ArcoResolver } from 'unplugin-vue-components/resolvers'

// 构建时从 CHANGELOG.md 读取最新版本号，注入为 __APP_VERSION__ 常量
function readAppVersion(): string {
  try {
    const changelogPath = fileURLToPath(new URL('../CHANGELOG.md', import.meta.url))
    const content = readFileSync(changelogPath, 'utf-8')
    const m = content.match(/^##\s*\[([^\]]+)\]/m)
    return m ? m[1] : '0.0.0'
  } catch {
    return '0.0.0'
  }
}

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      resolvers: [ArcoResolver()],
      dts: false,
    }),
    Components({
      resolvers: [
        ArcoResolver({
          sideEffect: true,
        }),
      ],
      dts: false,
    }),
  ],
  base: '/',
  define: {
    __APP_VERSION__: JSON.stringify(readAppVersion()),
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  optimizeDeps: {
    include: ['monaco-editor/esm/vs/editor/editor.api'],
  },
  build: {
    chunkSizeWarningLimit: 4000,
    // v2.7.5：不把 monaco / echarts 加进入口 modulepreload。
    // 它们会被 manualChunks 切出独立 chunk，并在 Studio / 看板路由动态 import 时再加载。
    // hall 页面从此不再被 monaco 的 ~4MB 预加载拖累。
    modulePreload: {
      resolveDependencies(_filename: string, deps: string[]) {
        return deps.filter((d) => !/monaco|echarts|zrender|cytoscape|mermaid|dagre/.test(d))
      },
    },
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          // Vue 运行时单独拆 chunk，防止 Rollup 把 Vue DOM 工具
          // 和 echarts/mermaid 的共享符号合并到同一个 chunk 拖进首屏。
          if (id.includes('node_modules/@vue/runtime-dom') || id.includes('node_modules/@vue/runtime-core')) {
            return 'vue-runtime'
          }
          if (id.includes('node_modules/@vue/shared')) {
            return 'vue-shared'
          }

          // Arco Design Vue locale：单独拆 chunk，避免和 echarts/mermaid
          // 的重导出符号合并到同一个 auto chunk 拖进首屏。
          if (id.includes('@arco-design/web-vue/es/locale')) {
            return 'arco-locale'
          }
          if (id.includes('@arco-design/web-vue')) {
            return 'arco-ui'
          }

          if (id.includes('node_modules/monaco-editor')) {
            if (id.includes('/language/json/')) return 'monaco-json'
            if (id.includes('/basic-languages/python/')) return 'monaco-python'
            if (id.includes('/basic-languages/yaml/')) return 'monaco-yaml'
            if (id.includes('/editor/standalone/')) return 'monaco-editor-standalone'
            if (id.includes('/editor/contrib/')) return 'monaco-editor-contrib'
            if (id.includes('/editor/browser/')) return 'monaco-editor-browser'
            if (id.includes('/editor/common/')) return 'monaco-editor-common'
            if (id.includes('/editor/')) return 'monaco-editor-core'
            if (id.includes('/platform/')) return 'monaco-platform'
            if (id.includes('/base/browser/')) return 'monaco-base-browser'
            if (id.includes('/base/common/')) return 'monaco-base-common'
            if (id.includes('/base/')) return 'monaco-base'
            return 'monaco-core'
          }

          if (id.includes('node_modules/monacopilot')) {
            return 'monaco-ai'
          }

          if (id.includes('node_modules/echarts') || id.includes('node_modules/zrender')) {
            return 'echarts'
          }
          if (id.includes('node_modules/vue-echarts')) {
            return 'vue-echarts'
          }
          // Mermaid + 其重型依赖 cytoscape/dagre：拆到独立 chunk，避免进入口 bundle 拖累首屏
          if (
            id.includes('node_modules/mermaid') ||
            id.includes('node_modules/@mermaid-js') ||
            id.includes('node_modules/cytoscape') ||
            id.includes('node_modules/@dagrejs')
          ) {
            return 'mermaid-graphs'
          }

          return undefined
        },
      },
    },
  },
  worker: {
    format: 'es',
  },
})
