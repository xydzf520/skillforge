/**
 * v2.7.5 大厅移动端截图集（iPhone 12：375 × 812）。
 */
import type { Page, Route } from '@playwright/test'
import { test } from '@playwright/test'
import { snap, snapFull } from './helpers/snapshot'

const MOBILE = { width: 375, height: 812 }

const CAPS = [
  {
    category: '质检',
    domain: '客服',
    skill_count_total: 8,
    skill_count_active: 6,
    top_departments: [{ department: '客服部', count: 5 }, { department: '产品部', count: 2 }],
    data_sources: [{ id: 'ds-1', name: '客服对话流水' }],
    sample_skills: [{ id: 'SK-c1', name: '投诉分级', department: '客服部', usage_count: 123 }],
    new_skills_7d: 1,
    last_run_at: new Date(Date.now() - 3600 * 1000 * 2).toISOString(),
    last_updated_at: new Date().toISOString(),
  },
  {
    category: '预警',
    domain: '风控',
    skill_count_total: 5,
    skill_count_active: 4,
    top_departments: [{ department: '营销部', count: 3 }],
    data_sources: [],
    sample_skills: [],
    new_skills_7d: 0,
    last_run_at: null,
    last_updated_at: new Date(Date.now() - 86400 * 1000 * 3).toISOString(),
  },
]

async function mockAll(page: Page) {
  await page.route(/\/api\/hall\/capabilities(\?.*)?$/, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: CAPS, total: CAPS.length, page: 1, page_size: 60 }),
    })
  })
  await page.route(/\/api\/hall\/team(\?.*)?$/, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [
          {
            department: '客服部',
            skill_count_total: 10,
            skill_count_active: 8,
            top_categories: [{ category: '质检', count: 5 }],
            data_sources_count: 3,
            ai_contact: { user_id: 'wang', name: '王二', role: 'ai_engineer' },
          },
        ],
        total: 1, page: 1, page_size: 60,
      }),
    })
  })
  await page.route(/\/api\/hall\/data(\?.*)?$/, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 24 }),
    })
  })
  await page.route(/\/api\/skills\/hall(\?.*)?$/, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 24 }),
    })
  })
  await page.route('**/api/data-sources/requests/pending', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
  })
  await page.route('**/api/hall/capability/*', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        category: '质检',
        summary: { skill_count_total: 8, skill_count_active: 6, data_sources_count: 1, department_count: 2 },
        skills: [{ id: 'SK-c1', name: '投诉分级', department: '客服部', status: 'active', usage_count: 123 }],
        data_sources: [],
        top_departments: [],
      }),
    })
  })
}

test.describe('Hall v3 · 移动端（375×812）', () => {
  test('mobile-01 /hall 能力视图', async ({ page }) => {
    await mockAll(page)
    await page.setViewportSize(MOBILE)
    await page.goto('/hall')
    await page.waitForSelector('.cap-card', { timeout: 8000 })
    await page.waitForTimeout(400)
    await snapFull(page, 'hall-v275-mobile-01-capability')
  })

  test('mobile-02 /hall 分组模式', async ({ page }) => {
    await mockAll(page)
    await page.setViewportSize(MOBILE)
    await page.goto('/hall')
    await page.waitForSelector('.cap-card', { timeout: 8000 })
    await page.locator('.filter-bar label', { hasText: '按业务领域分组' }).click()
    await page.waitForSelector('.domain-groups', { timeout: 5000 })
    await page.waitForTimeout(400)
    await snapFull(page, 'hall-v275-mobile-02-group')
  })

  test('mobile-03 /hall/capability/质检 详情', async ({ page }) => {
    await mockAll(page)
    await page.setViewportSize(MOBILE)
    await page.goto('/hall/capability/%E8%B4%A8%E6%A3%80')
    await page.waitForSelector('.summary-card', { timeout: 8000 })
    await page.waitForTimeout(400)
    await snapFull(page, 'hall-v275-mobile-03-capability-detail')
  })

  test('mobile-04 /hall 团队 tab', async ({ page }) => {
    await mockAll(page)
    await page.setViewportSize(MOBILE)
    await page.goto('/hall?view=type&tab=team')
    await page.waitForSelector('.team-card', { timeout: 8000 })
    await page.waitForTimeout(400)
    await snapFull(page, 'hall-v275-mobile-04-team')
  })
})
