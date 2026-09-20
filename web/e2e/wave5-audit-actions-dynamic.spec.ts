import { test, expect } from '@playwright/test'
import { loginAsAdmin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe('T13 Audit actions dropdown dynamic', () => {
  test('下拉从 /api/audit/actions 动态加载，含自定义 action', async ({ page }) => {
    await page.route('**/api/audit/actions', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [
            { action: 'user.login', label: '登录' },
            { action: 'my.custom.event', label: '自定义事件' },
            { action: 'compliance.block', label: '合规阻断' },
          ],
        }),
      })
    })

    await loginAsAdmin(page)
    await page.goto('/admin/audit')
    await expect(page.getByRole('heading', { name: '审计日志' })).toBeVisible()

    // 点操作类型下拉（filter-bar 里第 1 个 select；第 0 个是 user_id input，没 select；实际 select 按顺序：操作类型/结果）
    await page.locator('.filter-bar .arco-select').first().click()
    // 期待出现自定义选项
    const custom = page.locator('.arco-select-option', { hasText: '自定义事件' })
    await expect(custom).toBeVisible()
    const compliance = page.locator('.arco-select-option', { hasText: '合规阻断' })
    await expect(compliance).toBeVisible()

    await snap(page, 'T13-audit-actions')
  })
})
