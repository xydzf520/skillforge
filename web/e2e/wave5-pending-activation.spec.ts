import { test, expect } from '@playwright/test'
import { loginAsAdmin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe('T06 Pending activation hint', () => {
  test('激活弹窗里的联动规则有显式文案提示', async ({ page }) => {
    // Mock 一条 pending 用户，这样打开页面能点激活按钮
    await page.route('**/api/users/pending*', async (route) => {
      if (route.request().method() !== 'GET') return route.continue()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [
            {
              id: 'pending_alice',
              name: 'Alice',
              dingtalk_department: 'BD',
              pending_since: '2026-04-18T10:00:00Z',
            },
          ],
        }),
      })
    })
    // org list 返 BD 部门
    await page.route('**/api/org/tree**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          tree: [{ id: 'bd', name: 'BD', type: 'department', children: [] }],
        }),
      })
    })

    await loginAsAdmin(page)
    await page.goto('/admin/users/pending')
    // v2.1.1: 合并后页面 heading 统一为"用户管理"，内部靠 pending-summary 区分
    await expect(page.getByRole('heading', { name: '用户管理' })).toBeVisible()
    await expect(page.locator('.pending-summary')).toBeVisible()

    // 点激活按钮 -> 打开弹窗
    await page.getByRole('button', { name: /激活/ }).first().click()
    // 弹窗应包含联动 hint 文案
    const hint1 = page.locator('.activate-hint').first()
    await expect(hint1).toBeVisible()
    await expect(hint1).toContainText('dept_admin')
    await expect(hint1).toContainText('自动')

    await snap(page, 'T06-activate-modal')
  })
})
