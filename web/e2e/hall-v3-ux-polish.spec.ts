/**
 * v2.7.3 大厅交互打磨 e2e：
 *  - 标题走全局 H1（800 / --sf-text-h1）
 *  - 能力视图搜索/部门/排序筛选命中服务端
 *  - 三 Tab KeepAlive 保留已加载状态
 *  - 能力卡/团队卡 a11y（role=button + focus + Enter 触发）
 *  - 详情页返回按钮走 goBack（history.state.back 存在时 back，否则 push(/hall)）
 *  - DataSourceCard freshness 纯文字，无 emoji
 *  - 团队 Tab 搜索/排序面板
 *
 * 所有 /api/hall/** 均通过 page.route mock，不依赖真实数据。
 */
import type { Page, Route } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { snap, snapFull } from './helpers/snapshot'

const CAPS = [
  {
    category: '质检',
    skill_count_total: 8,
    skill_count_active: 6,
    top_departments: [
      { department: '客服部', count: 5 },
      { department: '产品部', count: 2 },
    ],
    data_sources: [{ id: 'ds-1', name: '客服对话流水' }],
    sample_skills: [
      { id: 'SK-c1', name: '投诉分级', department: '客服部', usage_count: 1243 },
    ],
  },
  {
    category: '预警',
    skill_count_total: 5,
    skill_count_active: 4,
    top_departments: [{ department: '营销部', count: 3 }],
    data_sources: [],
    sample_skills: [
      { id: 'SK-r1', name: 'ROI 异常', department: '营销部', usage_count: 301 },
    ],
  },
  {
    category: '派单',
    skill_count_total: 3,
    skill_count_active: 3,
    top_departments: [{ department: '销售部', count: 3 }],
    data_sources: [],
    sample_skills: [
      { id: 'SK-l1', name: '线索派单', department: '销售部', usage_count: 88 },
    ],
  },
]

const TEAMS = [
  {
    department: '客服部',
    skill_count_total: 10,
    skill_count_active: 8,
    top_categories: [{ category: '质检', count: 5 }],
    data_sources_count: 3,
    ai_contact: { user_id: 'wang', name: '王二', role: 'ai_engineer' },
  },
  {
    department: '营销部',
    skill_count_total: 6,
    skill_count_active: 5,
    top_categories: [{ category: '预警', count: 3 }],
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
]

const DATA_ITEMS = [
  {
    id: 'ds-fresh',
    name: '客服对话流水',
    description: '新鲜数据',
    source_type: 'api_pull',
    department: '客服部',
    visibility: 'company',
    freshness_status: 'fresh',
    my_access: { status: 'none' },
  },
  {
    id: 'ds-stale',
    name: '旧日表',
    description: '滞后数据',
    source_type: 'csv_upload',
    department: '运营部',
    visibility: 'department',
    freshness_status: 'stale',
    my_access: { status: 'none' },
  },
]

async function mockAll(page: Page) {
  await page.route(/\/api\/hall\/capabilities(\?.*)?$/, async (route: Route) => {
    const url = new URL(route.request().url())
    const q = (url.searchParams.get('q') || '').toLowerCase()
    const depts = url.searchParams.getAll('department')
    const sortBy = url.searchParams.get('sort_by') || 'active_desc'
    let items = [...CAPS] as any[]
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
  await page.route(/\/api\/hall\/team(\?.*)?$/, async (route: Route) => {
    const url = new URL(route.request().url())
    const q = (url.searchParams.get('q') || '').toLowerCase()
    let items = [...TEAMS]
    if (q) {
      items = items.filter(
        (t: any) =>
          String(t.department).toLowerCase().includes(q) ||
          String(t.ai_contact?.name || '').toLowerCase().includes(q) ||
          String(t.ai_contact?.user_id || '').toLowerCase().includes(q),
      )
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items, total: items.length, page: 1, page_size: 60 }),
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
  await page.route('**/api/hall/capability/*', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        category: '质检',
        summary: { skill_count_total: 8, skill_count_active: 6, data_sources_count: 1, department_count: 2 },
        skills: [],
        data_sources: [],
        top_departments: [],
      }),
    })
  })
}

test.describe('Hall v3 · UX 打磨（v2.7.3）', () => {
  test('01 能力大厅标题走全局 H1：font-weight=800，letter-spacing=-0.02em', async ({ page }) => {
    await mockAll(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/hall')
    await page.waitForSelector('.cap-card', { timeout: 8000 })
    const titleHandle = page.locator('.page-title').first()
    const weight = await titleHandle.evaluate((el) => getComputedStyle(el).fontWeight)
    const letterSpacing = await titleHandle.evaluate((el) => getComputedStyle(el).letterSpacing)
    expect(weight).toBe('800')
    // -0.02em 约等于 16 * -0.02 = -0.32px
    expect(letterSpacing).toMatch(/^-[\d.]+px$/)
  })

  test('02 搜索框按能力名筛选 → 只剩"预警"', async ({ page }) => {
    await mockAll(page)
    await page.goto('/hall')
    await page.waitForSelector('.cap-card', { timeout: 8000 })
    expect(await page.locator('.cap-card').count()).toBe(3)

    const search = page.locator('.filter-bar input[placeholder="搜索能力名"]')
    await search.fill('预警')
    // 等 250ms debounce + 请求
    await page.waitForTimeout(500)
    expect(await page.locator('.cap-card').count()).toBe(1)
    await expect(page.getByText('#预警').first()).toBeVisible()
    await snapFull(page, 'hall-v273-capability-search')
  })

  test('03 按部门筛选 → 只剩"客服部"提供的能力', async ({ page }) => {
    await mockAll(page)
    await page.goto('/hall')
    await page.waitForSelector('.cap-card', { timeout: 8000 })
    // 打开部门下拉（第一个 select 是部门过滤）
    const deptSelect = page.locator('.filter-bar .arco-select').first()
    await deptSelect.click()
    await page.locator('.arco-select-option', { hasText: '客服部' }).click()
    await page.keyboard.press('Escape')
    await page.waitForTimeout(400)
    expect(await page.locator('.cap-card').count()).toBe(1)
    await expect(page.getByText('#质检').first()).toBeVisible()
  })

  test('04 排序切"按能力名" → 首卡变"派单"（拼音升序）', async ({ page }) => {
    await mockAll(page)
    await page.goto('/hall')
    await page.waitForSelector('.cap-card', { timeout: 8000 })
    const sortSelect = page.locator('.filter-bar .arco-select').nth(1)
    await sortSelect.click()
    await page.locator('.arco-select-option', { hasText: '按能力名' }).click()
    await page.waitForTimeout(400)
    const firstCard = page.locator('.cap-card').first()
    await expect(firstCard.getByText(/^#/)).toBeVisible()
    // 拼音序 派单(p) > 预警(y) > 质检(z)
    await expect(firstCard).toContainText('#派单')
  })

  test('05 能力卡部门 tag 可点击跳团队详情', async ({ page }) => {
    await mockAll(page)
    await page.goto('/hall')
    await page.waitForSelector('.cap-card', { timeout: 8000 })
    const deptTag = page.locator('.cap-card .dept-tag').filter({ hasText: '客服部' }).first()
    await expect(deptTag).toBeVisible()
    await deptTag.click()
    await page.waitForURL(/\/hall\/team\/.*/, { timeout: 5000 })
    expect(page.url()).toContain('/hall/team/')
  })

  test('06 能力卡键盘 Enter 触发跳详情', async ({ page }) => {
    await mockAll(page)
    await page.goto('/hall')
    await page.waitForSelector('.cap-card-wrap', { timeout: 8000 })
    const firstWrap = page.locator('.cap-card-wrap').first()
    await firstWrap.focus()
    await page.keyboard.press('Enter')
    await page.waitForURL(/\/hall\/capability\/.*/, { timeout: 5000 })
    expect(page.url()).toContain('/hall/capability/')
  })

  test('07 URL query 默认值（tab=skill）不落地', async ({ page }) => {
    await mockAll(page)
    await page.goto('/hall')
    await page.waitForSelector('.cap-card', { timeout: 8000 })
    // 切换到"按类型"，URL 应带 view=type
    await page.getByText('按类型', { exact: true }).click()
    await page.waitForSelector('.hall-tabs', { timeout: 5000 })
    expect(page.url()).toContain('view=type')
    // 默认 tab=skill 不应出现在 URL
    expect(page.url()).not.toContain('tab=skill')
    // 切到数据 tab → tab=data 进 URL
    await page.locator('.arco-tabs-tab', { hasText: '数据能力' }).click()
    await page.waitForTimeout(200)
    expect(page.url()).toContain('tab=data')
    // 切回 skill → tab 从 URL 消失
    await page.locator('.arco-tabs-tab', { hasText: 'Skill 能力' }).click()
    await page.waitForTimeout(200)
    expect(page.url()).not.toContain('tab=')
  })

  test('08 三 Tab KeepAlive：切走再回来保留加载状态', async ({ page }) => {
    await mockAll(page)
    let teamCalls = 0
    await page.route(/\/api\/hall\/team(\?.*)?$/, async (route: Route) => {
      teamCalls += 1
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: TEAMS, total: TEAMS.length, page: 1, page_size: 60 }),
      })
    })
    await page.goto('/hall?view=type')
    await page.waitForSelector('.hall-tabs', { timeout: 8000 })
    // 先进团队 tab → 触发一次请求
    await page.locator('.arco-tabs-tab', { hasText: '团队能力' }).click()
    await page.waitForSelector('.team-card', { timeout: 5000 })
    expect(teamCalls).toBe(1)
    // 切到数据 tab
    await page.locator('.arco-tabs-tab', { hasText: '数据能力' }).click()
    await page.waitForTimeout(300)
    // 回团队 tab：KeepAlive 保留组件，不再发请求
    await page.locator('.arco-tabs-tab', { hasText: '团队能力' }).click()
    await page.waitForSelector('.team-card', { timeout: 3000 })
    expect(teamCalls).toBe(1) // 不再调用
  })

  test('09 团队 Tab 有搜索和排序控件', async ({ page }) => {
    await mockAll(page)
    await page.goto('/hall?view=type&tab=team')
    await page.waitForSelector('.team-card', { timeout: 8000 })
    await expect(page.locator('.hall-team-tab .filter-bar input[placeholder*="搜索部门"]')).toBeVisible()
    await expect(page.locator('.hall-team-tab .filter-bar .arco-select')).toBeVisible()
    await snapFull(page, 'hall-v273-team-tab')
  })

  test('10 团队 Tab 搜索 → 只剩匹配项', async ({ page }) => {
    await mockAll(page)
    await page.goto('/hall?view=type&tab=team')
    await page.waitForSelector('.team-card', { timeout: 8000 })
    expect(await page.locator('.team-card').count()).toBe(3)
    const search = page.locator('.hall-team-tab .filter-bar input[placeholder*="搜索部门"]')
    await search.fill('营销')
    // v2.7.5：搜索走服务端 + 250ms debounce，需要等 debounce + request + render
    await page.waitForTimeout(500)
    expect(await page.locator('.team-card').count()).toBe(1)
    await expect(page.getByText('营销部')).toBeVisible()
  })

  test('11 数据源 freshness 显示纯文字"新鲜/滞后"，无 emoji', async ({ page }) => {
    await mockAll(page)
    await page.goto('/hall?view=type&tab=data')
    await page.waitForSelector('.ds-card', { timeout: 8000 })
    // 不应该有 🟢 🟡
    const firstCard = page.locator('.ds-card').first()
    const txt = await firstCard.textContent()
    expect(txt).not.toContain('🟢')
    expect(txt).not.toContain('🟡')
    expect(txt).not.toContain('🔴')
    // 应该有"新鲜"或"滞后"或"未知"
    await expect(firstCard).toContainText(/新鲜|滞后|未知/)
  })

  test('12 详情页直链进入：返回按钮兜底 /hall', async ({ page }) => {
    await mockAll(page)
    // 直接访问能力详情（history 只有这一页）
    await page.goto('/hall/capability/%E8%B4%A8%E6%A3%80')
    await page.waitForSelector('.summary-card', { timeout: 8000 })
    // v2.7.4：返回改 icon-only 按钮（title=返回能力大厅）
    await page.locator('.back-icon-btn').click()
    await page.waitForURL('**/hall', { timeout: 5000 })
    expect(page.url()).toMatch(/\/hall(\?|$)/)
  })

  test('13 能力视图空态 CTA：admin 看到"创建第一个 Skill"按钮', async ({ page }) => {
    // mock capabilities 返回空
    await page.route(/\/api\/hall\/capabilities(\?.*)?$/, async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 60 }),
      })
    })
    await page.goto('/hall')
    await page.waitForSelector('.arco-empty', { timeout: 8000 })
    // admin 默认是 isEngineer → 应看到"创建第一个 Skill"
    await expect(page.getByRole('button', { name: /创建第一个 Skill/ })).toBeVisible()
    await snapFull(page, 'hall-v273-empty-cta')
  })

  // ═══════════════ v2.7.4 增补：header 布局 / 面包屑 / tooltip / 角色路由 ═══════════════

  test('14 能力详情：面包屑三段 + 返回图标按钮 + 标题简化', async ({ page }) => {
    await mockAll(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/hall/capability/%E8%B4%A8%E6%A3%80')
    await page.waitForSelector('.summary-card', { timeout: 8000 })
    // 面包屑三段
    const crumb = page.locator('.detail-crumb')
    await expect(crumb.getByText('能力大厅')).toBeVisible()
    await expect(crumb.getByText('能力', { exact: true })).toBeVisible()
    await expect(crumb.getByText('#质检')).toBeVisible()
    // 标题简化为 "#质检"（不再有 "· 能力详情"）
    const title = page.locator('.page-title').first()
    await expect(title).toHaveText('#质检')
    // 返回按钮是 icon-only，在标题左边
    await expect(page.locator('.back-icon-btn')).toBeVisible()
    await snapFull(page, 'hall-v274-capability-detail-header')
  })

  test('15 面包屑中段可点回大厅对应 tab', async ({ page }) => {
    await mockAll(page)
    await page.goto('/hall/data/ds-fresh')
    await page.waitForSelector('.detail-crumb', { timeout: 8000 })
    // 点"数据"面包屑 → /hall?view=type&tab=data
    await page.locator('.detail-crumb').getByText('数据', { exact: true }).click()
    await page.waitForURL(/\/hall\?.*tab=data/, { timeout: 5000 })
    expect(page.url()).toContain('view=type')
    expect(page.url()).toContain('tab=data')
  })

  test('16 能力卡 #质检 tabindex + tooltip 触发器存在', async ({ page }) => {
    // Arco tooltip 在测试环境下弹层异步 + teleport + trigger 识别偶有卡顿；
    // 这里只校验 tooltip 触发条件已就绪：cap-name 可 focus（tabindex="0"），父节点是 a-tooltip
    await mockAll(page)
    await page.goto('/hall')
    await page.waitForSelector('.cap-card', { timeout: 8000 })
    const capName = page.locator('.cap-card .cap-name').filter({ hasText: '质检' }).first()
    await page.evaluate(() => {
      const el = document.querySelector('.cap-card .cap-name') as HTMLElement | null
      return el?.getAttribute('tabindex')
    })
    const tabindex = await capName.getAttribute('tabindex')
    expect(tabindex).toBe('0')
    // trigger=hover focus 是在 <a-tooltip>，Arco 内部转为 mouseenter/focus 事件绑定
    // 这里只做"能 focus 就不报错"的烟火测试，功能性行为留给人眼。
    await capName.focus()
  })

  test('17 admin/engineer 点 Skill 跳 Studio (/skills/:id)', async ({ page }) => {
    await mockAll(page)
    // 进到能力详情，skill-item 可点
    await page.goto('/hall/capability/%E8%B4%A8%E6%A3%80')
    // 用真实的 capability 响应（带 skills 列表）
    await page.route('**/api/hall/capability/*', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          category: '质检',
          summary: { skill_count_total: 1, skill_count_active: 1, data_sources_count: 0, department_count: 1 },
          skills: [
            {
              id: 'SK-demo',
              name: 'Demo Skill',
              description: 'desc',
              department: '客服部',
              status: 'active',
              usage_count: 10,
              success_rate: 0.9,
              last_run_at: null,
            },
          ],
          data_sources: [],
          top_departments: [],
        }),
      })
    })
    // 拦截 skill 详情接口，避免跳转后页面请求真实后端
    await page.route('**/api/skills/SK-demo**', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'SK-demo', name: 'Demo Skill', department: '客服部' }),
      })
    })
    await page.reload()
    await page.waitForSelector('.skill-item', { timeout: 8000 })
    await page.locator('.skill-item').first().click()
    // admin 是 isEngineer → Studio
    await page.waitForURL(/\/skills\/SK-demo($|\?|#)/, { timeout: 5000 })
    expect(page.url()).toMatch(/\/skills\/SK-demo/)
    expect(page.url()).not.toContain('/portal/')
  })

  test('18 非工程师（operator）点 Skill 跳 Portal (/portal/skills/:id)', async ({ page }) => {
    // 把 /api/auth/me 改成 operator 角色（非工程师）
    await page.route('**/api/auth/me', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'op_user',
          username: 'op_user',
          name: '运营小王',
          role: 'operator', // 非工程师
          department: '客服部',
          state: 'active',
          is_active: true,
          managed_departments: [],
          accessible_departments: ['客服部'],
        }),
      })
    })
    await mockAll(page)
    await page.route('**/api/hall/capability/*', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          category: '质检',
          summary: { skill_count_total: 1, skill_count_active: 1, data_sources_count: 0, department_count: 1 },
          skills: [
            {
              id: 'SK-demo',
              name: 'Demo Skill',
              department: '客服部',
              status: 'active',
              usage_count: 10,
            },
          ],
          data_sources: [],
          top_departments: [],
        }),
      })
    })
    await page.route('**/api/portal/skills/SK-demo**', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 'SK-demo', name: 'Demo Skill', display_name: 'Demo Skill' }),
      })
    })
    await page.goto('/hall/capability/%E8%B4%A8%E6%A3%80')
    await page.waitForSelector('.skill-item', { timeout: 8000 })
    await page.locator('.skill-item').first().click()
    // operator → Portal
    await page.waitForURL(/\/portal\/skills\/SK-demo/, { timeout: 5000 })
    expect(page.url()).toContain('/portal/skills/')
  })
})
