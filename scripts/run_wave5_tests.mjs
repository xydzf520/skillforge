#!/usr/bin/env node
/**
 * Wave 5 e2e 批量运行 + 汇总
 *
 * 用法：
 *   node scripts/run_wave5_tests.mjs           # 全跑
 *   node scripts/run_wave5_tests.mjs T01 T02   # 只跑指定
 *
 * 功能：
 * 1. cd web && 调 playwright 跑 wave5-*.spec.ts
 * 2. 收集 screenshots/wave5/*.png
 * 3. 生成 screenshots/wave5/_summary.html（缩略图 + spec 状态）
 */
import { spawn } from 'node:child_process'
import { readdirSync, writeFileSync } from 'node:fs'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(__dirname, '..')
const WEB = join(ROOT, 'web')
const SHOTS = join(ROOT, 'screenshots/wave5')

const args = process.argv.slice(2)
const filter = args.length ? args.map(s => `wave5-${s.toLowerCase()}`).join('|') : 'wave5-'

function runPlaywright() {
  return new Promise((resolve) => {
    console.log(`▶  npx playwright test ${filter} --project=chromium`)
    const p = spawn('npx', ['playwright', 'test', filter, '--project=chromium', '--reporter=line'], {
      cwd: WEB,
      stdio: 'inherit',
      shell: true,
    })
    p.on('close', (code) => resolve(code ?? 0))
  })
}

function buildSummary(exitCode) {
  const pngs = readdirSync(SHOTS).filter((f) => f.endsWith('.png')).sort()
  const status = exitCode === 0 ? '✅ pass' : `❌ exit ${exitCode}`
  const rows = pngs
    .map((f) => {
      const rel = `./${f}`
      const name = f.replace(/\.png$/, '')
      return `<div class="card"><div class="name">${name}</div><a href="${rel}" target="_blank"><img src="${rel}" loading="lazy" /></a></div>`
    })
    .join('\n')

  const html = `<!doctype html>
<html><head><meta charset="utf-8"/><title>Wave 5 E2E Summary</title>
<style>
body{font-family:ui-sans-serif,system-ui;margin:0;padding:24px;background:#f8f4ec;}
h1{margin:0 0 8px;font-size:22px}
.meta{color:#5b6476;font-size:13px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.card{background:#fff;border:1px solid rgba(22,32,51,0.08);border-radius:12px;padding:10px;overflow:hidden}
.name{font-family:ui-monospace,monospace;font-size:12px;font-weight:700;margin-bottom:8px;color:#162033}
.card img{width:100%;border-radius:6px;display:block;border:1px solid rgba(22,32,51,0.05)}
</style></head>
<body>
<h1>Wave 5 e2e summary</h1>
<div class="meta">${new Date().toLocaleString('zh-CN')} · ${pngs.length} 张截图 · playwright ${status}</div>
<div class="grid">${rows}</div>
</body></html>`

  const target = join(SHOTS, '_summary.html')
  writeFileSync(target, html)
  console.log(`📋 summary → ${target}`)
}

const code = await runPlaywright()
buildSummary(code)
process.exit(code)
