import { test, expect } from '@playwright/test'
import { loginAsAdmin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe('T15 /auth/me email + phone in /me page', () => {
  test('/auth/me 返回 email/phone 时，/me 账户卡显示两个 chip', async ({ page }) => {
    // mock /auth/me 加 email/phone
    await page.route('**/api/auth/me', async (route) => {
      if (route.request().method() !== 'GET') return route.continue()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          user_id: 'admin',
          username: 'admin',
          name: '管理员',
          role: 'system_admin',
          department: 'EC',
          state: 'active',
          can_view_all: true,
          must_change_password: false,
          email: 'admin@example.com',
          phone: '13800138000',
          permissions_rev: 0,
          managed_departments: [],
          accessible_departments: [],
        }),
      })
    })

    await loginAsAdmin(page)
    await page.goto('/me')
    await expect(page.getByRole('heading', { name: '个人设置' })).toBeVisible()

    const meta = page.locator('.me-account-meta')
    await expect(meta).toContainText('admin@example.com')
    await expect(meta).toContainText('13800138000')

    await snap(page, 'T15-me-with-contacts')
  })
})
