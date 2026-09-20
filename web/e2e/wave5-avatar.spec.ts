import { test, expect } from '@playwright/test'
import { loginAsAdmin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe('T02 avatar fallback generated', () => {
  test('head-nav 头像 + /me 大头像 无真实头像时使用 emoji data URL', async ({ page }) => {
    await loginAsAdmin(page)
    await page.goto('/me')
    await expect(page.getByRole('heading', { name: '个人设置' })).toBeVisible()

    // Me 页大头像（a-avatar 内部渲染 img）
    const meAvatar = page.locator('.me-account .arco-avatar img').first()
    await expect(meAvatar).toBeVisible()
    const meSrc = await meAvatar.getAttribute('src')
    expect(meSrc).toMatch(/^data:image\/svg\+xml/)
    expect(decodeURIComponent(meSrc || '')).toContain('<text')

    // 顶栏头像（user-btn 下 a-avatar 内部 img）
    const navAvatar = page.locator('.user-btn .arco-avatar img').first()
    await expect(navAvatar).toBeVisible()
    const navSrc = await navAvatar.getAttribute('src')
    expect(navSrc).toBe(meSrc)

    await snap(page, 'T02-avatar-generated')
  })
})
