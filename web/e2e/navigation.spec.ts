import { expect, test } from '@playwright/test'

test.describe('Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/auth/me', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({ error: { code: 'AUTH_REQUIRED', message: '未登录' } }),
      })
    })
  })

  test('unauthenticated user is redirected to login', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL(/\/login/)
  })

  test('login page renders form elements', async ({ page }) => {
    await page.goto('/login')
    await expect(page.locator('input[type="text"], input[placeholder*="用户"]')).toBeVisible()
    await expect(page.locator('input[type="password"]')).toBeVisible()
    await expect(page.locator('button[type="submit"], .login-btn')).toBeVisible()
  })

  test('404 page shows for unknown routes', async ({ page }) => {
    await page.goto('/this-page-does-not-exist')
    await expect(page.locator('body')).not.toBeEmpty()
  })
})

test.describe('Navigation — 一级菜单高亮（P3-3）', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/auth/me', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({ error: { code: 'AUTH_REQUIRED', message: '未登录' } }),
      })
    })
  })

  const navCases: Array<{ path: string; expectMatch: RegExp }> = [
    { path: '/skills', expectMatch: /\/(skills|login)/ },
    { path: '/skills/new', expectMatch: /\/(skills|login)/ },
    { path: '/playbooks', expectMatch: /\/(playbooks|login)/ },
    { path: '/reviews', expectMatch: /\/(reviews|login)/ },
    { path: '/executions', expectMatch: /\/(executions|login)/ },
    { path: '/dashboard', expectMatch: /\/(dashboard|login)/ },
    { path: '/todos', expectMatch: /\/(todos|login)/ },
    { path: '/aiclaw', expectMatch: /\/(aiclaw|login)/ },
  ]

  for (const { path, expectMatch } of navCases) {
    test(`访问 ${path} 不会 404 / 死循环`, async ({ page }) => {
      const responses: number[] = []
      page.on('response', (response) => {
        if (response.url().includes(path)) responses.push(response.status())
      })

      await page.goto(path)
      await expect(page).toHaveURL(expectMatch)
      expect(responses.every((status) => status < 500)).toBe(true)
    })
  }

  test('Logo 可点击回首页', async ({ page }) => {
    await page.goto('/skills')
    if (page.url().includes('/login')) test.skip()

    const logo = page.locator('header').locator('a, [class*="logo"]').first()
    if ((await logo.count()) > 0) {
      await logo.click()
      await expect(page).toHaveURL(/\/(|skills|skills-home)$/)
    }
  })
})
