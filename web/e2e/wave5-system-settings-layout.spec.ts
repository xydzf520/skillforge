import { test, expect } from '@playwright/test'
import { loginAsAdmin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe('T09 System settings design shell', () => {
  test('系统配置页还原 admin.jsx shell 和设置卡片', async ({ page }) => {
    await loginAsAdmin(page)
    await page.goto('/admin/settings')
    await expect(page.getByRole('heading', { name: '系统配置' })).toBeVisible()
    await expect(page.locator('.system-settings-pagehead')).toBeVisible()
    await expect(page.locator('.system-settings-tabs.ai-tabs')).toBeVisible()
    await expect(page.locator('.system-settings-body.ai-pagebody')).toBeVisible()
    await expect(page.locator('.system-settings-page .page-list-card')).toHaveCount(0)
    await expect(page.locator('.system-settings-tab.active')).toContainText('安全设置')
    await expect(page.locator('.setting-row.ai-card')).toHaveCount(2)
    await expect(page.getByText('security.bypass_review_direct_publish')).toBeVisible()
    await expect.poll(async () => page.locator('.system-settings-page').evaluate((el) => getComputedStyle(el).paddingLeft)).toBe('0px')

    await page.locator('.system-settings-tab', { hasText: 'AI' }).click()
    await expect(page.locator('.system-settings-tab.active')).toContainText('AI')
    await expect(page.getByText('ai.api_base')).toBeVisible()
    await expect(page.locator('.setting-row.ai-card')).toHaveCount(6)

    await snap(page, 'T09-system-settings-shell')
  })
})
