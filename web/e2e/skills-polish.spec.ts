/**
 * P2 验证：页面体验打磨
 *
 * - O4 /skills 4 卡网格（列表 / 对话创建 / 大厅 / 模板）
 * - O7 空搜索显示"清除筛选"按钮（List + Hall）
 * - O15 Portal detail "← 返回门户"样式升级（带 icon，hover 主色）
 */
import { test, expect } from '@playwright/test'
import { snap } from './helpers/snapshot'

test.describe('P2 · Skills 打磨', () => {
  test('O4 /skills 有 4 张 hub 卡片', async ({ page }) => {
    await page.goto('/skills', { waitUntil: 'networkidle' })
    // 4 张 hub-card（admin 可见全部）
    const cards = page.locator('.hub-grid .hub-card')
    await expect(cards).toHaveCount(4)
    // 每张卡标题命中
    await expect(page.locator('.hub-card').filter({ hasText: '浏览 Skill 列表' })).toBeVisible()
    await expect(page.locator('.hub-card').filter({ hasText: '通过对话创建 Skill' })).toBeVisible()
    await expect(page.locator('.hub-card').filter({ hasText: '浏览 Skill 大厅' })).toBeVisible()
    await expect(page.locator('.hub-card').filter({ hasText: '模板中心' })).toBeVisible()
    await snap(page, 'T68-home-4-cards')
  })

  test('O7 SkillList 空搜索显示"清除筛选"按钮', async ({ page }) => {
    await page.addInitScript(() => { localStorage.setItem('sf-skill-view', 'card') })
    // mock 空结果
    await page.route('**/api/skills/**', async (route) => {
      const url = route.request().url()
      if (url.includes('members') || url.includes('pinned')) return route.continue()
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({
          items: [], total: 0,
          status_counts: { all: 0, active: 0, shadow: 0, draft: 0, deprecated: 0 },
        }),
      })
    })
    await page.goto('/skills/list?q=__zzz_not_match__')
    await page.waitForSelector('.sf-empty-state', { timeout: 8000 })
    await snap(page, 'T69-list-empty-clear-debug')
    await expect(page.locator('.sf-empty-hint')).toContainText('试试清除部分筛选条件')
    // 清除按钮（由 SfEmptyState 内 actionLabel prop 渲染为 a-empty #extra slot）
    const clearBtn = page.getByRole('button', { name: '清除筛选' })
    await expect(clearBtn).toBeVisible({ timeout: 6000 })
    await snap(page, 'T69-list-empty-clear')
    await clearBtn.click()
    await page.waitForLoadState('networkidle')
    expect(page.url()).not.toContain('__zzz_not_match__')
  })

  test('O7 SkillHall 空搜索显示"清除筛选"按钮', async ({ page }) => {
    // mock hall 空结果
    await page.route('**/api/skills/hall*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 12 }),
      })
    })
    await page.goto('/skills/hall')
    const searchInput = page.locator('.filter-bar input[placeholder*="搜索"]').first()
    await searchInput.fill('__zzz_not_match__')
    await searchInput.press('Enter')
    await page.waitForSelector('.sf-empty-state', { timeout: 8000 })
    await expect(page.locator('.sf-empty-hint')).toContainText('试试清除部分筛选条件')
    const clearBtn = page.locator('.sf-empty-state').getByText('清除筛选', { exact: true })
    await expect(clearBtn).toBeVisible({ timeout: 6000 })
    await snap(page, 'T70-hall-empty-clear')
  })

  test('O15 Portal detail 返回链接视觉升级', async ({ page }) => {
    // 先拿一个真实 skill id
    const res = await page.request.get('/api/skills/?page=1&page_size=1')
    const data = await res.json()
    const realId = (data.items || [])[0]?.id
    test.skip(!realId, 'no real skill')

    await page.goto(`/portal/skills/${realId}`)
    const backBtn = page.locator('.portal-back-btn')
    await expect(backBtn).toBeVisible()
    await expect(backBtn).toContainText('返回门户')
    // 字号 >= 13px (原来是 size=small ~ 12px)
    const fontSize = await backBtn.evaluate((el) => {
      return parseFloat(window.getComputedStyle(el).fontSize)
    })
    expect(fontSize).toBeGreaterThanOrEqual(13)
    await snap(page, 'T71-portal-back-btn')
  })
})
