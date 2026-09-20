import { test, expect } from '@playwright/test'
import { loginAsAdmin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe('T03 BrowserConnect summary cards 4→3', () => {
  test('连接管理 > 浏览器采集 的摘要卡 = 3', async ({ page }) => {
    await loginAsAdmin(page)
    await page.goto('/connections?tab=browser')
    await expect(page.getByRole('heading', { name: '连接管理' })).toBeVisible()
    await expect(page.locator('.bc-summary-card').first()).toBeVisible()

    const count = await page.locator('.bc-summary-card').count()
    expect(count).toBe(3)

    // 第一张卡的 meta 应该包含「当前平台」提示（合并进"远程会话"卡）
    const firstCardMeta = await page.locator('.bc-summary-card').first().locator('.bc-summary-meta').textContent()
    expect(firstCardMeta).toMatch(/当前平台|需要先启动/)

    await snap(page, 'T03-summary-cards')
  })
})
