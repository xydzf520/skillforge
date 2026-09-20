/**
 * P5 验证：基础 a11y
 *
 * - SkillList 状态 tab 有 role="tablist" + role="tab" + aria-selected
 * - SkillCard 根有 role="button" + aria-label
 */
import { test, expect } from '@playwright/test'
import { snap } from './helpers/snapshot'

test.describe('P5 · Skills a11y', () => {
  test('SkillList 状态 tab 带 tablist / tab 语义', async ({ page }) => {
    await page.goto('/skills/list')
    await expect(page.locator('[role="tablist"]')).toBeVisible()
    const tabs = page.locator('[role="tab"]')
    const count = await tabs.count()
    expect(count).toBeGreaterThan(0)
    // 至少一个 aria-selected=true（当前选中的 tab）
    const selected = page.locator('[role="tab"][aria-selected="true"]')
    await expect(selected).toHaveCount(1)
  })

  test('SkillCard 根有 role="button" + aria-label', async ({ page }) => {
    await page.addInitScript(() => { localStorage.setItem('sf-skill-view', 'card') })
    await page.goto('/skills/list')
    const firstCard = page.locator('.skill-card-wrap').first()
    await expect(firstCard).toHaveAttribute('role', 'button')
    const aria = await firstCard.getAttribute('aria-label')
    expect(aria).toBeTruthy()
    expect(aria).toContain('状态')
    expect(aria).toContain('部门')
    await snap(page, 'T83-a11y-card')
  })
})
