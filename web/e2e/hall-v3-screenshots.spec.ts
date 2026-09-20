/**
 * v2.7.3 大厅 4 关键页截图集（用户点检用）。
 * 不 assert 功能，只生成 screenshots/wave5/hall-v273-shot-*.png。
 */
import type { Page, Route } from '@playwright/test'
import { test } from '@playwright/test'
import { snap, snapFull } from './helpers/snapshot'

const CAPS = [
  {
    category: '质检',
    skill_count_total: 8,
    skill_count_active: 6,
    top_departments: [
      { department: '客服部', count: 5 },
      { department: '产品部', count: 2 },
      { department: '销售部', count: 1 },
    ],
    data_sources: [
      { id: 'ds-support-chat', name: '客服对话流水' },
      { id: 'ds-complaint-tags', name: '投诉标签库' },
    ],
    sample_skills: [
      { id: 'SK-complaint-triage', name: '投诉分级 Skill', department: '客服部', usage_count: 1243 },
      { id: 'SK-tone-check', name: '话术质检 Skill', department: '客服部', usage_count: 520 },
    ],
  },
  {
    category: '预警',
    skill_count_total: 5,
    skill_count_active: 4,
    top_departments: [
      { department: '营销部', count: 3 },
      { department: '风控部', count: 2 },
    ],
    data_sources: [{ id: 'ds-roi-daily', name: 'ROI 日表' }],
    sample_skills: [{ id: 'SK-roi-alert', name: 'ROI 异常预警', department: '营销部', usage_count: 301 }],
  },
  {
    category: '派单',
    skill_count_total: 3,
    skill_count_active: 3,
    top_departments: [{ department: '销售部', count: 3 }],
    data_sources: [],
    sample_skills: [{ id: 'SK-lead-assign', name: '线索派单', department: '销售部', usage_count: 88 }],
  },
  {
    category: '内容生成',
    skill_count_total: 4,
    skill_count_active: 3,
    top_departments: [{ department: '市场部', count: 4 }],
    data_sources: [],
    sample_skills: [{ id: 'SK-ads-copy', name: '广告文案', department: '市场部', usage_count: 142 }],
  },
]

const TEAMS = [
  {
    department: '客服部',
    skill_count_total: 10,
    skill_count_active: 8,
    top_categories: [
      { category: '质检', count: 5 },
      { category: '投诉归因', count: 3 },
    ],
    data_sources_count: 3,
    ai_contact: { user_id: 'wang', name: '王二', role: 'ai_engineer' },
  },
  {
    department: '营销部',
    skill_count_total: 6,
    skill_count_active: 5,
    top_categories: [
      { category: '预警', count: 3 },
      { category: '文案', count: 2 },
    ],
    data_sources_count: 2,
    ai_contact: { user_id: 'zhang', name: '张三', role: 'biz_owner' },
  },
  {
    department: '销售部',
    skill_count_total: 3,
    skill_count_active: 3,
    top_categories: [{ category: '派单', count: 3 }],
    data_sources_count: 1,
    ai_contact: null,
  },
  {
    department: '市场部',
    skill_count_total: 4,
    skill_count_active: 3,
    top_categories: [{ category: '内容生成', count: 4 }],
    data_sources_count: 0,
    ai_contact: { user_id: 'chen', name: '陈四', role: 'ai_engineer' },
  },
]

const DATA_ITEMS = [
  {
    id: 'ds-support-chat',
    name: '客服对话流水',
    description: 'T+1 落盘的客服会话全量，含对话内容、客户 id、坐席、情感标签',
    usage_hint: '适合：质检 / 投诉归因 / 销售话术优化',
    source_type: 'api_pull',
    department: '客服部',
    visibility: 'company',
    owner_contact: 'cs_lead',
    freshness_status: 'fresh',
    related_skills: ['SK-quality', 'SK-escalation'],
    my_access: { status: 'none' },
    updated_at: new Date().toISOString(),
  },
  {
    id: 'ds-private-marketing',
    name: '营销留资明细',
    description: '仅授权成员可见的留资数据，含手机号（脱敏）',
    usage_hint: '精准营销 / 转化漏斗分析',
    source_type: 'csv_upload',
    department: '营销部',
    visibility: 'private',
    owner_contact: 'mkt_owner',
    freshness_status: 'fresh',
    related_skills: [],
    my_access: {
      status: 'granted',
      expires_at: new Date(Date.now() + 3 * 24 * 60 * 60 * 1000).toISOString(),
    },
    updated_at: new Date().toISOString(),
  },
  {
    id: 'ds-roi-daily',
    name: 'ROI 日表',
    description: '按日汇总广告投放效果、点击、下单',
    source_type: 'api_pull',
    department: '营销部',
    visibility: 'department',
    freshness_status: 'stale',
    owner_contact: 'mkt_ops',
    my_access: { status: 'pending', request_id: 42 },
  },
  {
    id: 'ds-sales-daily',
    name: '销售日表',
    description: '按天汇总的销售订单 + 客户归因',
    source_type: 'csv_upload',
    department: '销售部',
    visibility: 'company',
    freshness_status: 'fresh',
    related_skills: ['SK-lead-assign'],
    my_access: { status: 'none' },
  },
]

const CAPABILITY_DETAIL = {
  category: '质检',
  summary: {
    skill_count_total: 8,
    skill_count_active: 6,
    data_sources_count: 2,
    department_count: 3,
  },
  skills: [
    {
      id: 'SK-complaint-triage',
      name: '投诉分级 Skill',
      description: '按关键词 + 情绪分把客服投诉分 P0/P1/P2',
      department: '客服部',
      status: 'active',
      risk_level: 'R2',
      usage_count: 1243,
      success_rate: 0.93,
      last_run_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    },
    {
      id: 'SK-tone-check',
      name: '话术质检 Skill',
      department: '客服部',
      status: 'active',
      usage_count: 520,
      success_rate: 0.87,
      last_run_at: null,
    },
  ],
  data_sources: [
    { id: 'ds-support-chat', name: '客服对话流水', visibility: 'company' },
    { id: 'ds-complaint-tags', name: '投诉标签库', visibility: 'department' },
  ],
  top_departments: [
    {
      department: '客服部',
      skill_count: 5,
      ai_contact: { user_id: 'wang_er', name: '王二', role: 'ai_engineer' },
    },
    {
      department: '产品部',
      skill_count: 2,
      ai_contact: { user_id: 'li_si', name: '李四', role: 'biz_owner' },
    },
    { department: '销售部', skill_count: 1, ai_contact: null },
  ],
}

async function mockAll(page: Page) {
  await page.route(/\/api\/hall\/capabilities(\?.*)?$/, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: CAPS, total: CAPS.length, page: 1, page_size: 60 }),
    })
  })
  await page.route('**/api/hall/capability/*', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(CAPABILITY_DETAIL),
    })
  })
  await page.route('**/api/hall/team', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(TEAMS),
    })
  })
  await page.route(/\/api\/hall\/data(\?.*)?$/, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: DATA_ITEMS, total: DATA_ITEMS.length, page: 1, page_size: 24 }),
    })
  })
  await page.route(/\/api\/skills\/hall(\?.*)?$/, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 24 }),
    })
  })
}

test.describe('Hall v3 · v2.7.3 关键页截图集', () => {
  test('shot-01 /hall 能力视图（按能力）', async ({ page }) => {
    await mockAll(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/hall')
    await page.waitForSelector('.cap-card', { timeout: 8000 })
    await page.waitForTimeout(400)
    await snapFull(page, 'hall-v273-shot-01-capability-view')
  })

  test('shot-02 /hall/capability/质检 详情页', async ({ page }) => {
    await mockAll(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/hall/capability/%E8%B4%A8%E6%A3%80')
    await page.waitForSelector('.summary-card', { timeout: 8000 })
    await page.waitForTimeout(400)
    await snapFull(page, 'hall-v273-shot-02-capability-detail')
  })

  test('shot-03 /hall?view=type&tab=data 数据能力', async ({ page }) => {
    await mockAll(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/hall?view=type&tab=data')
    await page.waitForSelector('.ds-card', { timeout: 8000 })
    await page.waitForTimeout(400)
    await snapFull(page, 'hall-v273-shot-03-data-tab')
  })

  test('shot-04 /hall?view=type&tab=team 团队能力（带搜索排序）', async ({ page }) => {
    await mockAll(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/hall?view=type&tab=team')
    await page.waitForSelector('.team-card', { timeout: 8000 })
    await page.waitForTimeout(400)
    await snapFull(page, 'hall-v273-shot-04-team-tab')
  })

  test('shot-05 按能力搜索"预警" → 过滤态', async ({ page }) => {
    await mockAll(page)
    // override capabilities for filter demo
    await page.route(/\/api\/hall\/capabilities\?.*q=/, async (route: Route) => {
      const only = CAPS.filter((c) => c.category === '预警')
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: only, total: only.length, page: 1, page_size: 60 }),
      })
    })
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/hall')
    await page.waitForSelector('.cap-card', { timeout: 8000 })
    await page.locator('.filter-bar input[placeholder="搜索能力名"]').fill('预警')
    await page.waitForTimeout(500)
    await snapFull(page, 'hall-v273-shot-05-capability-filter')
  })

  test('shot-06-data-detail 数据详情（面包屑 + 新 header）', async ({ page }) => {
    await mockAll(page)
    await page.route('**/api/hall/data/*', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'ds-support-chat',
          name: '客服对话流水',
          description: 'T+1 落盘的客服会话全量',
          usage_hint: '适合：质检 / 投诉归因 / 销售话术优化',
          source_type: 'api_pull',
          department: '客服部',
          visibility: 'company',
          owner_contact: 'cs_lead',
          freshness_status: 'fresh',
          consumers: [{ id: 'SK-c1', name: '投诉分级', department: '客服部', status: 'active' }],
          my_access: { status: 'granted', expires_at: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString() },
          schema_preview: [
            { field: 'session_id', type: 'string', desc: '会话 ID' },
            { field: 'customer_id', type: 'string', desc: '客户 ID' },
          ],
        }),
      })
    })
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/hall/data/ds-support-chat')
    await page.waitForSelector('.detail-crumb', { timeout: 8000 })
    await page.waitForTimeout(400)
    await snapFull(page, 'hall-v274-shot-06-data-detail-header')
  })

  test('shot-07-team-detail 团队详情（面包屑 + 新 header）', async ({ page }) => {
    await mockAll(page)
    await page.route('**/api/hall/team/*', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          department: '客服部',
          skills: [
            {
              id: 'SK-c1',
              name: '投诉分级',
              category: '质检',
              status: 'active',
              usage_count: 123,
              success_rate: 0.92,
              last_run_at: new Date(Date.now() - 3600 * 1000).toISOString(),
            },
          ],
          data_sources: [{ id: 'ds-1', name: '对话流水', visibility: 'company' }],
          members: [
            { user_id: 'wang', name: '王二', role: 'ai_engineer' },
            { user_id: 'li', name: '李四', role: 'operator' },
          ],
          summary: { skill_count_total: 1, skill_count_active: 1, data_sources_count: 1, member_count: 2 },
        }),
      })
    })
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/hall/team/%E5%AE%A2%E6%9C%8D%E9%83%A8')
    await page.waitForSelector('.detail-crumb', { timeout: 8000 })
    await page.waitForTimeout(400)
    await snapFull(page, 'hall-v274-shot-07-team-detail-header')
  })

  test('shot-08 空态 CTA（admin 看到"创建第一个 Skill"）', async ({ page }) => {
    await page.route(/\/api\/hall\/capabilities(\?.*)?$/, async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 60 }),
      })
    })
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/hall')
    await page.waitForSelector('.arco-empty', { timeout: 8000 })
    await page.waitForTimeout(300)
    await snapFull(page, 'hall-v273-shot-06-empty-cta')
  })
})
