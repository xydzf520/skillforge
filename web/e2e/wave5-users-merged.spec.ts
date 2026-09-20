import { test, expect } from '@playwright/test'
import { loginAsAdmin } from './helpers/login'
import { snap } from './helpers/snapshot'

/**
 * T08 用户 + 待激活合并（v2.1.1 真合并）
 *
 * v2.1.0: AdminUsers.vue + PendingUsers.vue 两个文件，靠 AdminUsersTabs 视觉桥接
 * v2.1.1: 物理合并——PendingUsers.vue 删除，逻辑全部进 AdminUsers.vue
 *         /admin/users/pending 路由仍可用，指向 AdminUsers.vue + 按 route.path 渲染 pending 视图
 */
test.describe('T08 Users page tabs bridge both components', () => {
  test('tab 切换：激活 / 待激活 / 停用，内部逻辑都走 AdminUsers.vue', async ({ page }) => {
    await loginAsAdmin(page)

    // 激活 tab
    await page.goto('/admin/users')
    await expect(page.getByRole('heading', { name: '用户管理' })).toBeVisible()
    await expect(page.locator('.admin-users-pagehead')).toBeVisible()
    await expect(page.locator('.admin-users-tabs.ai-tabs')).toBeVisible()
    await expect(page.locator('.admin-users-tab.active')).toContainText('激活')
    await expect(page.locator('.admin-users-page .page-list-card')).toHaveCount(0)
    await expect.poll(async () => page.locator('.admin-users-page').evaluate((el) => getComputedStyle(el).paddingLeft)).toBe('0px')
    const headIconPaths = await page.locator('.admin-users-head-actions .ai-btn svg path').evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute('d') || ''),
    )
    expect(headIconPaths[0]).toContain('M2 8a6 6')
    expect(headIconPaths[1]).toContain('M8 3v10')
    await snap(page, 'T08-tab-active')

    // 切到待激活 tab：路径变 /admin/users/pending，出现候选用户 chip
    await page.locator('.admin-users-tab').filter({ hasText: '待激活' }).click()
    await expect(page).toHaveURL(/\/admin\/users\/pending/)
    await expect(page.locator('.pending-summary')).toBeVisible()
    await expect(page.locator('.pending-summary')).toContainText('候选用户')
    await expect(page.locator('.admin-users-tab.active')).toContainText('待激活')
    await expect(page.locator('.admin-users-body--pending.ai-pagebody')).toBeVisible()
    await expect(page.locator('.admin-users-page .page-list-card')).toHaveCount(0)
    await snap(page, 'T08-tab-pending')

    // 切到停用 tab
    await page.locator('.admin-users-tab').filter({ hasText: '停用' }).click()
    await expect(page).toHaveURL(/\/admin\/users\?status=disabled/)
    await expect(page.locator('.admin-users-tab.active')).toContainText('停用')
    await snap(page, 'T08-tab-disabled')
  })
})
