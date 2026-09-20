/**
 * Wave 5 e2e 公共登录 helper（真实后端）
 *
 * 用法：
 *   import { loginAsAdmin, loginAsRole } from './helpers/login'
 *
 *   test.beforeEach(async ({ page }) => {
 *     await loginAsAdmin(page)
 *   })
 */
import { expect, type Page } from '@playwright/test'

const BACKEND = 'http://127.0.0.1:8000'

/** 按角色预置的测试账号（密码统一 admin123） */
export const TEST_USERS = {
  admin: { username: 'admin', password: 'admin123' },
  dept_admin: { username: 'bd_tester', password: 'admin123' },
  engineer: { username: 'aibp_test', password: 'admin123' },
  observer: { username: 'observer_test', password: 'admin123' },
} as const

export type TestRole = keyof typeof TEST_USERS

/** 等后端 /health 通；最多 6 秒 */
async function waitBackendReady(page: Page): Promise<void> {
  for (let i = 0; i < 20; i += 1) {
    try {
      const health = await page.request.get(`${BACKEND}/health`)
      if (health.ok()) return
    } catch {
      // ignore, retry
    }
    await page.waitForTimeout(300)
  }
  throw new Error('backend /health not ready within 6s')
}

/** 真实后端登录 + 把 session cookie 注入 page.context */
export async function loginAsRole(page: Page, role: TestRole = 'admin'): Promise<void> {
  await waitBackendReady(page)
  const creds = TEST_USERS[role]

  let resp = await page.request.post(`${BACKEND}/api/auth/login`, { data: creds })
  for (let i = 0; i < 10 && !resp.ok(); i += 1) {
    await page.waitForTimeout(300)
    resp = await page.request.post(`${BACKEND}/api/auth/login`, { data: creds })
  }
  expect(resp.ok(), `login as ${role} failed`).toBeTruthy()

  const setCookie = resp.headers()['set-cookie']
  const match = /skillforge_session=([^;]+)/.exec(setCookie || '')
  expect(match, 'skillforge_session cookie not set').toBeTruthy()

  await page.context().addCookies([
    {
      name: 'skillforge_session',
      value: match![1],
      domain: '127.0.0.1',
      path: '/',
      httpOnly: true,
      sameSite: 'Lax',
    },
  ])
}

/**
 * loginAsAdmin：在 v2.1.1 之后默认无操作——playwright.config.ts 已通过 globalSetup
 * 预登录 admin 并注入 storageState。保留函数签名以兼容现有 spec。
 * 需要刷新会话的场景（T18 logoutAndRelogin）仍可显式调 loginAsRole。
 */
export async function loginAsAdmin(_page: Page): Promise<void> {
  // no-op: storageState already mounted by globalSetup
  return
}

/** 清 cookie 后重登（用于权限变更场景，如 T18） */
export async function logoutAndRelogin(page: Page, role: TestRole = 'admin'): Promise<void> {
  await page.context().clearCookies()
  await loginAsRole(page, role)
}

/**
 * W3-A `withAdmin(page, fn)`：封装常用前置（后端 ready + admin 会话），
 * 避免每个 spec 重复 3 行 setup。storageState 由 globalSetup 注入，这里
 * 只补做一次 /api/auth/me 探活并等后端响应。
 *
 * 用法：
 *   test('xxx', async ({ page }) => {
 *     await withAdmin(page, async (p) => {
 *       await p.goto('/skills/list')
 *       // ...
 *     })
 *   })
 */
export async function withAdmin(page: Page, fn: (page: Page) => Promise<void>): Promise<void> {
  await waitBackendReady(page)
  // storageState 已在 globalSetup 注入，这里仅让 cookie 在 context 里激活
  const meResp = await page.request.get(`${BACKEND}/api/auth/me`)
  if (!meResp.ok()) {
    // 会话过期或丢失，fallback 重登
    await loginAsRole(page, 'admin')
  }
  await fn(page)
}

/** 从现场 API 拿第一条真实 skill id，跳过 e2e 若库里无数据。 */
export async function getFirstSkillId(page: Page): Promise<string | null> {
  try {
    const res = await page.request.get(`${BACKEND}/api/skills/?page=1&page_size=1`)
    const data = await res.json()
    return (data.items || [])[0]?.id || null
  } catch {
    return null
  }
}
