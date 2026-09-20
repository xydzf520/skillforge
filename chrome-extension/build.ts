import { build } from 'esbuild'
import { cpSync, mkdirSync, rmSync } from 'node:fs'
import { resolve } from 'node:path'

const rootDir = process.cwd()
const distDir = resolve(rootDir, 'dist')
const staticFiles = ['manifest.json', 'popup.html', 'icon16.png', 'icon48.png', 'icon128.png'] as const

rmSync(distDir, { recursive: true, force: true })
mkdirSync(distDir, { recursive: true })

await build({
  entryPoints: {
    background: resolve(rootDir, 'src/background.ts'),
    content: resolve(rootDir, 'src/content.ts'),
    popup: resolve(rootDir, 'src/popup.ts'),
  },
  outdir: distDir,
  bundle: true,
  format: 'iife',
  target: ['chrome114'],
  logLevel: 'info',
})

for (const file of staticFiles) {
  cpSync(resolve(rootDir, file), resolve(distDir, file))
}
