import { expect, test } from '@playwright/test'

test.describe('Login Flow', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/auth/me', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({ error: { code: 'AUTH_REQUIRED', message: '未登录' } }),
      })
    })
  })

  test('should show login page', async ({ page }) => {
    await page.goto('/login')
    await expect(page.locator('h1, h2, .login-title')).toBeVisible()
  })

  test('should reject invalid credentials', async ({ page }) => {
    await page.route('**/api/auth/login', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({ error: { code: 'AUTH_INVALID_CREDENTIALS', message: '用户名或密码错误' } }),
      })
    })
    await page.goto('/login')
    await page.fill('input[type="text"], input[placeholder*="用户"]', 'invalid')
    await page.fill('input[type="password"]', 'wrong')
    await page.click('button[type="submit"], .login-btn')
    await expect(page.locator('.arco-message-error, .arco-alert')).toBeVisible({ timeout: 5000 })
  })

  test('should redirect to login when unauthenticated', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL(/\/login/)
  })
})
