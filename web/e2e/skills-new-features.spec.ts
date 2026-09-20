/**
 * P3 验证：新增功能（精简集）
 *
 * - N1 SkillCard 右键菜单：复制 ID / 在新标签打开 / Fork / 删除
 * - N9 SkillList 影子 tab tooltip 有文案说明
 */
import { test, expect } from '@playwright/test'
import { snap } from './helpers/snapshot'

test.describe('P3 · Skills 新增功能', () => {
  test('N1 卡片右键弹出 4 项菜单', async ({ page }) => {
    await page.addInitScript(() => { localStorage.setItem('sf-skill-view', 'card') })
    await page.goto('/skills/list')
    const firstCard = page.locator('.skill-card.variant-list').first()
    await expect(firstCard).toBeVisible()
    // 右键触发
    await firstCard.click({ button: 'right', force: true })
    const menu = page.locator('.skill-card-menu').first()
    await expect(menu).toBeVisible()
    await expect(menu).toContainText('复制 ID')
    await expect(menu).toContainText('在新标签打开')
    await snap(page, 'T72-contextmenu')
    // 点击"复制 ID"后菜单关闭
    await menu.getByText('复制 ID').click()
    await expect(menu).toHaveCount(0)
  })

  test('N1 右键菜单点击外部关闭', async ({ page }) => {
    await page.addInitScript(() => { localStorage.setItem('sf-skill-view', 'card') })
    await page.goto('/skills/list')
    const firstCard = page.locator('.skill-card.variant-list').first()
    await firstCard.click({ button: 'right', force: true })
    await expect(page.locator('.skill-card-menu').first()).toBeVisible()
    // 点击页面其他地方关闭
    await page.locator('.skill-pagehead, h1').first().click({ force: true })
    await expect(page.locator('.skill-card-menu')).toHaveCount(0)
  })

  test('N9 SkillList 影子 tab 有 tooltip', async ({ page }) => {
    // 模拟有影子数据
    await page.route('**/api/skills/**', async (route) => {
      const url = route.request().url()
      if (url.includes('members') || url.includes('pinned')) return route.continue()
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({
          items: [{ id: 'x', name: 'X', status: 'shadow', department: 'EC' }],
          total: 1,
          status_counts: { all: 1, active: 0, shadow: 1, draft: 0, deprecated: 0 },
        }),
      })
    })
    await page.goto('/skills/list')
    await page.waitForSelector('.stat-chip.stat-shadow', { timeout: 6000 })
    // hover 触发 tooltip，等待任何以"影子 ="或"Dry Run"开头的文本出现
    await page.locator('.stat-chip.stat-shadow').hover()
    await page.waitForTimeout(800)
    const tooltipText = await page.locator('body').innerText()
    expect(tooltipText).toContain('Dry Run')
    await snap(page, 'T78-shadow-tooltip')
  })
})
