/**
 * v2.9.1 M3：SkillCreationWizard 移动端（375 × 812）截图 + 基本交互。
 */
import type { Page, Route } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { snapFull } from './helpers/snapshot'

const MOBILE = { width: 375, height: 812 }

async function mockDescribe(page: Page) {
  await page.route('**/api/skills/architect/find-similar', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) })
  })
  await page.route(/\/api\/skills\/departments(\?.*)?$/, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ departments: [{ name: '测试部', skill_count: 3 }] }),
    })
  })
}

test.describe('SkillCreationWizard · 移动端', () => {
  test('01 /skills/new Step 1 描述页（窄屏）', async ({ page }) => {
    await mockDescribe(page)
    await page.setViewportSize(MOBILE)
    await page.goto('/skills/new')
    await page.waitForSelector('.wizard-panel', { timeout: 8000 })
    await page.waitForTimeout(300)
    await expect(page.getByText('新建 Skill').first()).toBeVisible()
    // wizard-actions 在窄屏应该列向堆叠（按钮全宽）
    const actions = page.locator('.wizard-actions').first()
    await expect(actions).toBeVisible()
    await snapFull(page, 'wizard-v291-mobile-01-describe')
  })

  test('02 textarea 可用，按钮可点（窄屏）', async ({ page }) => {
    await mockDescribe(page)
    await page.setViewportSize(MOBILE)
    await page.goto('/skills/new')
    await page.waitForSelector('.wizard-panel', { timeout: 8000 })
    const textarea = page.locator('textarea').first()
    await textarea.fill('移动端测试：每天 10 点自动统计订单')
    // 按钮在窄屏仍应可点
    const startBtn = page.getByRole('button', { name: /开始采访/ })
    await expect(startBtn).toBeVisible()
    await expect(startBtn).toBeEnabled()
  })
})
