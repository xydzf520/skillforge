import { test, expect } from '@playwright/test'
import { loginAsRole, logoutAndRelogin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe.configure({ mode: 'serial' })

test.describe('T01 admin shell menu filter by role', () => {
  test('admin 看全部 10 项', async ({ page }) => {
    await loginAsRole(page, 'admin')
    await page.goto('/admin')
    await expect(page).toHaveURL(/\/admin\/users/)

    // 等左栏 item 渲染完
    await expect(page.locator('.admin-nav-item').first()).toBeVisible()
    const count = await page.locator('.admin-nav-item').count()
    expect(count).toBe(10)

    await snap(page, 'T01-admin-menu')
  })

  test('dept_admin 只看 1 项（待激活用户）', async ({ page }) => {
    await logoutAndRelogin(page, 'dept_admin')
    // dept_admin 直接访问 /admin/users/pending（其 visible menu 里唯一的入口）
    await page.goto('/admin/users/pending')
    await expect(page).toHaveURL(/\/admin\/users\/pending/)
    await expect(page.locator('.admin-nav-item').first()).toBeVisible()

    const count = await page.locator('.admin-nav-item').count()
    expect(count).toBe(1)
    await expect(page.locator('.admin-nav-item').first()).toContainText('待激活用户')

    await snap(page, 'T01-deptadmin-menu')
  })
})
