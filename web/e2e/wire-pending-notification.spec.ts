/**
 * 接线验证：/pending 路由 + 顶栏通知铃铛
 *
 * 覆盖：
 *   1) admin 登录 → 顶栏看到通知铃铛
 *   2) 点铃铛 → 通知抽屉展开
 *   3) pending 用户（mock /auth/me 返回 state=pending）→ 自动跳 /pending 页
 *   4) 非 pending 用户访问 /pending → 被守卫踢回首页
 */
import { test, expect } from '@playwright/test'
import { snap } from './helpers/snapshot'

test.describe('WIRE: /pending + topbar 铃铛', () => {
  test('admin 顶栏能看到通知铃铛', async ({ page }) => {
    await page.goto('/')
    // 守卫通过 → 不应被踢到 /login
    await expect(page).not.toHaveURL(/\/login/)
    // 铃铛按钮挂在顶栏（topbar-notif-wrap 是 AppLayout 新加的包装）
    const bell = page.locator('.topbar-notif-wrap .notification-trigger')
    await expect(bell).toBeVisible()
    // 同时旁边的「更新日志」按钮也还在
    await expect(page.locator('.changelog-btn')).toBeVisible()
    await snap(page, 'WIRE-01-topbar-bell')
  })

  test('点铃铛弹出通知面板', async ({ page }) => {
    await page.goto('/')
    const bell = page.locator('.topbar-notif-wrap .notification-trigger')
    await expect(bell).toBeVisible()
    await bell.click()
    // 面板容器由 NotificationPanel 内部渲染
    const panel = page.locator('.notification-panel')
    await expect(panel).toBeVisible()
    // 标题行的「通知」字样
    await expect(panel.locator('.header-title')).toHaveText('通知')
    // 等 panel-fade transition 完成（0.2s），再等一个空态或列表项出现
    await page.waitForFunction(() => {
      const p = document.querySelector('.notification-panel') as HTMLElement | null
      if (!p) return false
      const hasEmpty = p.querySelector('.panel-empty')
      const hasItem = p.querySelector('.notification-item')
      const spin = p.querySelector('.arco-spin')
      return !spin && (hasEmpty || hasItem)
    }, null, { timeout: 5000 })
    await page.waitForTimeout(250)
    await snap(page, 'WIRE-02-panel-open')
  })

  test('pending 用户访问首页被守卫顶到 /pending 并渲染', async ({ page }) => {
    // mock /auth/me 让前端把当前会话当作 pending 用户
    await page.route('**/api/auth/me', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({
          user_id: 'pending_e2e',
          username: 'pending_e2e',
          name: '待激活用户',
          role: 'observer',
          department: 'BD',
          state: 'pending',
          must_change_password: false,
        }),
      })
    })
    await page.goto('/')
    await expect(page).toHaveURL(/\/pending$/)
    await expect(page.locator('.pending-card')).toBeVisible()
    await expect(page.locator('.pending-card h1')).toContainText('账号待审批')
    await snap(page, 'WIRE-03-pending-page')
  })

  test('非 pending 用户访问 /pending 被踢回首页', async ({ page }) => {
    // 不 mock，走真实 admin 会话（storageState 自动注入）
    await page.goto('/pending')
    // 守卫应把 admin 重定向到 /
    await expect(page).not.toHaveURL(/\/pending/)
  })
})
