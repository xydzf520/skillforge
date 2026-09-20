/**
 * Playwright globalSetup：
 * 启动时 login admin 一次，把 storageState 存到 web/e2e/.auth/admin.json。
 * 各 spec 里的 loginAsAdmin() 会优先复用该 storageState（通过 use.storageState 注入），
 * 避免多 worker 并行时 18 个 spec 同时打 POST /auth/login 引发 session race。
 */
import type { FullConfig } from '@playwright/test'
import { chromium } from '@playwright/test'
import path from 'node:path'
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const AUTH_DIR = path.resolve(HERE, '../.auth')
const ADMIN_STATE = path.join(AUTH_DIR, 'admin.json')
const BACKEND = 'http://127.0.0.1:8000'
const ADMIN_USERNAME = process.env.E2E_ADMIN_USERNAME || 'admin'
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'admin123'

async function waitBackendReady(): Promise<void> {
  for (let i = 0; i < 30; i += 1) {
    try {
      const res = await fetch(`${BACKEND}/health`)
      if (res.ok) return
    } catch {
      // ignore
    }
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error('backend /health not ready within 15s')
}

export default async function globalSetup(_config: FullConfig): Promise<void> {
  mkdirSync(AUTH_DIR, { recursive: true })
  await waitBackendReady()
  const browser = await chromium.launch()
  const ctx = await browser.newContext()

  // 真实后端登录，拿 cookie
  const resp = await ctx.request.post(`${BACKEND}/api/auth/login`, {
    data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD },
  })
  if (!resp.ok()) throw new Error(`admin login failed: ${resp.status()}`)

  const raw = resp.headers()['set-cookie'] || ''
  const m = /skillforge_session=([^;]+)/.exec(raw)
  if (!m) throw new Error('skillforge_session cookie missing')
  await ctx.addCookies([
    {
      name: 'skillforge_session',
      value: m[1],
      domain: '127.0.0.1',
      path: '/',
      httpOnly: true,
      sameSite: 'Lax',
    },
  ])
  await ctx.storageState({ path: ADMIN_STATE })
  await browser.close()
  console.log(`[globalSetup] admin storageState → ${ADMIN_STATE}`)
}
