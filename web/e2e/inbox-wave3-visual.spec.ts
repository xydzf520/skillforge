/**
 * Wave 3 视觉回归：给关键修复点各留一张截图，存到
 * web/playwright-report/v2.0.17/
 *
 * 覆盖：
 * - H6：InboxCenter 初始渲染（未读红点 + tab）
 * - H8：TodoBulkBar 选中多条的状态（选择集保留逻辑的入口界面）
 */
import { expect, test } from '@playwright/test'
import { installMockApi } from './mock-api'

test.describe('Wave 3 视觉验证截图', () => {
  test.beforeEach(async ({ page }) => {
    await installMockApi(page)
  })

  test('H6 · InboxCenter 初始状态 + 切换到报告 tab', async ({ page }) => {
    await page.goto('/inbox')
    await expect(page.getByText('待处理').first()).toBeVisible()
    await page.screenshot({
      path: 'playwright-report/v2.0.17/inbox-initial-pending.png',
      fullPage: true,
    })

    // 切到报告 tab，验证 mark-read 逻辑运转、UI 响应。
    await page.locator('.inbox-tab').filter({ hasText: '报告' }).click()
    await page.waitForTimeout(300)
    await page.screenshot({
      path: 'playwright-report/v2.0.17/inbox-reports-after-mark.png',
      fullPage: true,
    })
  })

  test('H8 · TodoBulkBar 选中多条的视觉状态', async ({ page }) => {
    await page.goto('/inbox')
    await page.waitForTimeout(300)
    const cells = page.locator('.todo-select-cell')
    const count = await cells.count()
    if (count >= 2) {
      await cells.nth(0).click()
      await cells.nth(1).click()
    } else if (count === 1) {
      await cells.nth(0).click()
    }
    await expect(page.getByTestId('todo-bulk-bar')).toBeVisible({ timeout: 8000 })
    await page.screenshot({
      path: 'playwright-report/v2.0.17/inbox-bulk-bar-selected.png',
      fullPage: true,
    })
  })
})
