import { test, expect } from '@playwright/test'
import { loginAsAdmin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe('T11 Playbook AI Designer onboarding tour', () => {
  test.beforeEach(async ({ page }) => {
    // 确保每次测试开始 localStorage 是干净的（未看过教程）
    await page.addInitScript(() => {
      try { localStorage.removeItem('sf-ai-designer-tour-seen') } catch {}
    })
  })

  test('首次打开显示 3 步引导，点"我知道了"后再打开不显示', async ({ page }) => {
    await loginAsAdmin(page)
    await page.goto('/playbooks')
    await expect(page.getByRole('heading', { name: /Playbook/ })).toBeVisible()

    // 打开 AI 推荐编排
    await page.locator('button', { hasText: 'AI 推荐编排' }).click()
    const tour = page.locator('[data-testid="designer-tour"]')
    await expect(tour).toBeVisible()
    await expect(tour.locator('.tour-step')).toHaveCount(3)

    await snap(page, 'T11-onboarding-step1')

    // 点"我知道了"
    await tour.locator('button:has-text("我知道了")').click()
    await expect(tour).toHaveCount(0)

    // 关闭 modal
    await page.keyboard.press('Escape')
    // 再次打开
    await page.locator('button', { hasText: 'AI 推荐编排' }).click()
    // 这次应该不再显示 tour
    await expect(page.locator('[data-testid="designer-tour"]')).toHaveCount(0)

    await snap(page, 'T11-onboarding-step2')
  })
})
