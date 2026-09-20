/**
 * v2.7.2 一级导航 · 大厅"按能力"首页 + 能力详情页 e2e。
 */
import type { Page, Route } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { snap, snapFull } from './helpers/snapshot'

const CAPABILITIES = [
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
    top_departments: [{ department: '营销部', count: 3 }, { department: '风控部', count: 2 }],
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
]

const DIRECT_CAPABILITIES = [
  {
    id: 'gpt-imagegen',
    name: '电商图生成',
    display_name: '电商图生成',
    description: '根据商品卖点生成可直接投放的电商图片',
    department: '营销部',
    category: '内容生成',
    risk_level: 'R1',
    icon: 'image',
    created_at: '2026-05-18T08:00:00Z',
    permissions: { execute: true },
  },
  {
    id: 'roi-report',
    name: 'ROI 日报生成',
    display_name: 'ROI 日报生成',
    description: '汇总投放效果并生成日报',
    department: '营销部',
    category: '报表生成',
    risk_level: 'R1',
    icon: 'doc',
    created_at: '2026-05-10T08:00:00Z',
    permissions: { execute: true },
  },
  {
    id: 'risk-check',
    name: '风险与合规巡检',
    display_name: '风险与合规巡检',
    description: '检查价格和评论风险',
    department: '风控部',
    category: '风险与合规',
    risk_level: 'R2',
    icon: 'safe',
    created_at: '2026-04-22T08:00:00Z',
    permissions: { execute: true },
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
      owner: 'wang_er',
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

async function mockHallApi(page: Page) {
  await page.route(/\/api\/hall\/direct-capabilities(\?.*)?$/, async (route: Route) => {
    const url = new URL(route.request().url())
    const q = (url.searchParams.get('q') || '').toLowerCase()
    const category = url.searchParams.get('category') || ''
    const department = url.searchParams.get('department') || ''
    let items = [...DIRECT_CAPABILITIES]
    if (q) {
      items = items.filter((c) =>
        [c.id, c.name, c.display_name, c.description].some((field) =>
          String(field || '').toLowerCase().includes(q),
        ),
      )
    }
    if (category) items = items.filter((c) => c.category === category)
    if (department) items = items.filter((c) => c.department === department)
    const departments = Array.from(new Set(DIRECT_CAPABILITIES.map((c) => c.department))).map((name) => ({
      name,
      count: DIRECT_CAPABILITIES.filter((c) => c.department === name).length,
    }))
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items,
        total: items.length,
        page: 1,
        page_size: 60,
        categories: Array.from(new Set(DIRECT_CAPABILITIES.map((c) => c.category))),
        departments,
      }),
    })
  })
  await page.route(/\/api\/hall\/direct-artifacts(\?.*)?$/, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 20 }),
    })
  })
  await page.route('**/api/hall/departments', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ departments: [{ name: '客服部' }, { name: '营销部' }, { name: '风控部' }] }),
    })
  })
  await page.route('**/api/data-sources/requests/pending', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) })
  })
  await page.route(/\/api\/skills\/hall(\?.*)?$/, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 8 }),
    })
  })
  // v2.7.3：/api/hall/capabilities 带 page/page_size/q/department/sort_by 查询参数
  await page.route(/\/api\/hall\/capabilities(\?.*)?$/, async (route: Route) => {
    const url = new URL(route.request().url())
    const q = (url.searchParams.get('q') || '').toLowerCase()
    const depts = url.searchParams.getAll('department')
    const sortBy = url.searchParams.get('sort_by') || 'active_desc'
    let items = [...CAPABILITIES] as any[]
    if (q) items = items.filter((c) => String(c.category).toLowerCase().includes(q))
    if (depts.length)
      items = items.filter((c) =>
        (c.top_departments || []).some((d: any) => depts.includes(d.department)),
      )
    const sorters: Record<string, (a: any, b: any) => number> = {
      active_desc: (a, b) =>
        (b.skill_count_active || 0) - (a.skill_count_active || 0) ||
        (b.skill_count_total || 0) - (a.skill_count_total || 0),
      total_desc: (a, b) => (b.skill_count_total || 0) - (a.skill_count_total || 0),
      name_asc: (a, b) => String(a.category).localeCompare(String(b.category)),
    }
    items.sort(sorters[sortBy] || sorters.active_desc)
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items, total: items.length, page: 1, page_size: 60 }),
    })
  })
  await page.route('**/api/hall/capability/*', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CAPABILITY_DETAIL) })
  })
}

test.describe('Hall v3 · 能力大厅（一级导航 + 按能力分类）', () => {
  test('01 /hall 默认能力视图还原设计稿 shell + 直接运行能力卡 → snap', async ({ page }) => {
    await mockHallApi(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/hall')
    await page.waitForSelector('.direct-capability-card', { timeout: 8000 })

    await expect(page.locator('.hall-pagehead')).toContainText('能力大厅')
    await expect(page.locator('.ai-crumbs').filter({ hasText: '资产中心 · 能力大厅' })).toBeVisible()
    expect(await page.locator('.direct-capability-card').count()).toBe(3)
    await expect(page.getByText('电商图生成')).toBeVisible()
    await expect(page.getByText('ROI 日报生成')).toBeVisible()
    await expect(page.getByText('风险与合规巡检')).toBeVisible()
    await expect(page.locator('.hall-content.page-list-card')).toHaveCount(0)

    const mainPaddingLeft = await page.locator('.hall-main').evaluate((el) =>
      window.getComputedStyle(el).paddingLeft,
    )
    expect(mainPaddingLeft).toBe('0px')
    const activeSideIcon = await page.locator('.hall-sidebar .ai-side-item.active svg path').first().getAttribute('d')
    expect(activeSideIcon).toContain('M8 1l1.6 5.4')
    const inboxIcon = await page.locator('.hall-head-actions .ai-btn svg path').first().getAttribute('d')
    expect(inboxIcon).toContain('M2 9l2-6h8l2 6')
    const runIcon = await page.locator('.direct-run-btn svg path').first().getAttribute('d')
    expect(runIcon).toContain('M4 2l9 6-9 6V2z')

    await snapFull(page, 'hall-home-abilities')
  })

  test('01b /hall?view=capability 显示 3 张能力分类卡', async ({ page }) => {
    await mockHallApi(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/hall?view=capability')
    await page.waitForSelector('.cap-card', { timeout: 8000 })
    expect(await page.locator('.cap-card').count()).toBe(3)
    await expect(page.getByText('#质检').first()).toBeVisible()
    await expect(page.getByText('#预警').first()).toBeVisible()
    await expect(page.getByText('#派单').first()).toBeVisible()
    await expect(page.locator('.cap-card').getByText('客服部').first()).toBeVisible()
    await expect(page.getByText(/依赖.*2.*个数据源/).first()).toBeVisible()
    await expect(page.getByText('投诉分级 Skill')).toBeVisible()
  })

  test('02 一级导航"能力大厅"入口可点击跳转', async ({ page }) => {
    await mockHallApi(page)
    await page.goto('/')
    // 等 SPA 加载 + redirect 到 /hall
    await page.waitForURL('**/hall', { timeout: 8000 })
    await page.waitForSelector('.direct-capability-card', { timeout: 8000 })
    expect(page.url()).toMatch(/\/hall(\?|$)/)
  })

  test('03 点能力卡跳详情页 → snapFull', async ({ page }) => {
    await mockHallApi(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/hall/capability/%E8%B4%A8%E6%A3%80')
    await page.waitForSelector('.summary-card', { timeout: 8000 })
    // v2.7.4：标题简化为 "#质检"（上方面包屑提供上下文），去掉 "· 能力详情" 冗余
    await expect(page.locator('.page-title').filter({ hasText: '#质检' })).toBeVisible()
    // 面包屑可见
    await expect(page.locator('.detail-crumb').getByText('能力大厅')).toBeVisible()
    // summary 4 项
    await expect(page.getByText('Skill 总数')).toBeVisible()
    await expect(page.getByText('关联数据源')).toBeVisible()
    await expect(page.getByText('覆盖部门')).toBeVisible()
    // 提供此能力的部门 + AI 联系人
    await expect(page.getByText('王二')).toBeVisible()
    await expect(page.getByText('AI 工程师')).toBeVisible()
    // 业务 Owner 的角色标签（李四）
    await expect(page.getByText('业务 Owner')).toBeVisible()
    await expect(page.getByText('暂无 AI 联系人')).toBeVisible()
    // skill 实现列表
    await expect(page.getByText('投诉分级 Skill')).toBeVisible()
    // 依赖数据源 tag
    await expect(page.locator('.ds-tag').getByText('客服对话流水')).toBeVisible()
    await snapFull(page, 'hall-capability-detail')
  })

  test('04 首页"按类型"视图切换', async ({ page }) => {
    await mockHallApi(page)
    await page.route('**/api/hall/data**', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 50 }) })
    })
    await page.route('**/api/hall/team', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    })
    await page.route('**/api/skills/hall**', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 24 }) })
    })
    await page.goto('/hall')
    await page.waitForSelector('.direct-capability-card', { timeout: 8000 })
    // 切换到"数据 / 团队"
    await page.getByText('数据 / 团队', { exact: true }).click()
    await page.waitForSelector('.hall-tabs', { timeout: 8000 })
    // 3 个 tab 都在
    const tabs = page.locator('.arco-tabs-tab')
    await expect(tabs).toHaveCount(3)
    expect(page.url()).toContain('view=type')
  })
})
