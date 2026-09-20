/**
 * v2.7 大厅 v3 · 团队能力 tab e2e + 截图。
 */
import type { Page, Route } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { snap, snapFull } from './helpers/snapshot'

const TEAMS = [
  {
    department: '营销部',
    skill_count_total: 24,
    skill_count_active: 18,
    top_categories: [
      { category: '审批', count: 10 },
      { category: '派单', count: 8 },
      { category: '预警', count: 6 },
    ],
    data_sources_count: 5,
    ai_contact: { user_id: 'wang_ai', name: '王二', role: 'ai_engineer' },
  },
  {
    department: '客服部',
    skill_count_total: 12,
    skill_count_active: 10,
    top_categories: [
      { category: '质检', count: 6 },
      { category: '投诉', count: 4 },
    ],
    data_sources_count: 3,
    ai_contact: { user_id: 'zhao_ai', name: '赵六', role: 'biz_owner' },
  },
  {
    department: '销售部',
    skill_count_total: 7,
    skill_count_active: 4,
    top_categories: [{ category: '对账', count: 3 }],
    data_sources_count: 2,
    ai_contact: null,
  },
]

async function mockTeamRoute(page: Page) {
  // v2.7.5：后端改为分页 {items, total, page, page_size}；glob 不匹配 query，改正则
  await page.route(/\/api\/hall\/team(\?.*)?$/, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: TEAMS, total: TEAMS.length, page: 1, page_size: 60 }),
    })
  })
}

test.describe('Hall v3 · 团队能力', () => {
  test('01 tab 切换到团队 → 显示 3 张部门卡 → snap', async ({ page }) => {
    await mockTeamRoute(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/skills/hall?tab=team')
    await page.waitForSelector('.team-card', { timeout: 8000 })
    const cards = page.locator('.team-card')
    expect(await cards.count()).toBe(3)
    await snap(page, 'hall-team-overview')
  })

  test('02 卡片显示 AI 联系人 + 角色标签', async ({ page }) => {
    await mockTeamRoute(page)
    await page.goto('/skills/hall?tab=team')
    await page.waitForSelector('.team-card', { timeout: 8000 })
    await expect(page.getByText('王二')).toBeVisible()
    await expect(page.getByText('AI 工程师')).toBeVisible()
    await expect(page.getByText('赵六')).toBeVisible()
    await expect(page.getByText('业务 Owner')).toBeVisible()
    // 销售部无 ai_contact → 显示 "暂无 AI 联系人"
    await expect(page.getByText('暂无 AI 联系人')).toBeVisible()
  })

  test('03 top categories 标签云展示（降序）', async ({ page }) => {
    await mockTeamRoute(page)
    await page.goto('/skills/hall?tab=team')
    await page.waitForSelector('.team-card', { timeout: 8000 })
    const firstCard = page.locator('.team-card').first()
    await expect(firstCard.getByText('#审批')).toBeVisible()
    await expect(firstCard.getByText('#派单')).toBeVisible()
    await expect(firstCard.getByText('#预警')).toBeVisible()
  })

  test('04 tab 完整顺序 + url 同步 → 全页截图', async ({ page }) => {
    await page.route('**/api/hall/data**', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 50 }) })
    })
    await mockTeamRoute(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/skills/hall')
    await page.waitForSelector('.hall-tabs', { timeout: 8000 })
    // 三个 tab 都在
    const tabs = page.locator('.arco-tabs-tab')
    await expect(tabs).toHaveCount(3)
    await page.locator('.arco-tabs-tab').filter({ hasText: '团队能力' }).click()
    await page.waitForSelector('.team-card', { timeout: 8000 })
    expect(page.url()).toContain('tab=team')
    await snapFull(page, 'hall-team-detail')
  })
})
