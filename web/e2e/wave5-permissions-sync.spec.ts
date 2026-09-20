import { test, expect } from '@playwright/test'
import { loginAsRole, logoutAndRelogin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe('T18 permissions_rev live sync', () => {
  test('切换路由时重新拉 /auth/me，权限变化后跳 403', async ({ page }) => {
    // 用 admin 登录 → 进 /admin/settings（admin-only）
    await loginAsRole(page, 'admin')
    await page.goto('/admin/settings')
    await expect(page.getByRole('heading', { name: '系统配置' })).toBeVisible()
    await snap(page, 'T18-before')

    // 模拟后端把当前用户"降级"成 dept_admin：后续 /auth/me 返回 dept_admin
    let intercepted = false
    await page.route('**/api/auth/me', async (route) => {
      intercepted = true
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          user_id: 'admin',
          username: 'admin',
          name: '管理员',
          role: 'dept_admin',          // 关键：降级
          department: 'EC',
          state: 'active',
          permissions_rev: 99,
          managed_departments: ['EC'],
          accessible_departments: ['EC'],
          can_view_all: false,
          must_change_password: false,
        }),
      })
    })

    // 切换路由触发同步
    await page.goto('/admin/costs')
    // dept_admin 不能进 /admin/costs → 应被 guard 跳 403
    await expect(page).toHaveURL(/\/error\/403/)
    expect(intercepted).toBeTruthy()

    await snap(page, 'T18-after-revoke')
  })
})
