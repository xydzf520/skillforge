import { expect, test, type Page, type Route } from '@playwright/test'

const reportPayload = {
  id: '10001-0',
  decision_log_id: 10001,
  report_index: 0,
  run_id: 'rm-9e2c098f',
  skill_id: 'tmall-link-decline',
  skill_name: '天猫店铺链接下滑分析',
  skill_department: '示例品牌传统电商运营部',
  channel: 'dingtalk_markdown',
  title: '2026-05-24 店铺链接下滑诊断日报',
  summary: '扫描全店 117 个商品，生成 78 张商品诊断卡，发现 474 个数据缺口。',
  content_markdown: '# 今日主线判断\\n\\n- 数据不足为主，付费投放问题为辅。',
  metrics: [
    { label: '扫描商品', value: '117', note: '全店覆盖规模' },
    { label: '成交金额', value: '¥105,707.95', note: '当日成交' },
    { label: '全店转化', value: '6.4%', note: '支付转化率' },
    { label: '数据缺口', value: '474', note: '仍待补采' },
  ],
  tags: ['日报', '诊断'],
  trigger_type: 'node_scheduler',
  related_todos: [{ id: 1, title: '补齐商品级活动数据', priority: 'P1', status: 'pending' }],
  related_todo_count: 36,
  related_pending_request_count: 36,
  created_at: '2026-05-24T08:47:00+08:00',
  payload: {
    store_main_judgement: {
      main_cause: '数据不足',
      secondary_cause: '付费投放问题',
      confidence: '低',
      direction: '今日先补齐关键数据，禁止直接删词、改价、降预算。',
      why: ['付费端 0 个商品缺少计划 / 关键词 / 创意级明细'],
    },
    store_overview: {
      product_count: 117,
      visitor_count: 18800,
      pay_amt: 105707.95,
    },
    product_cards: [{
      item_id: '901',
      item_name: '高弹避孕套组合装',
      priority: 'P1',
      type: '实时下滑',
      data_time: '2026-05-24',
      metrics: [{ name: '下滑系数', value: '0.76', status: 'P1' }],
      analysis_basis: [{ dimension: '访客', basis: '免费访客环比下滑' }],
      top_actions: [{ dimension: '运营', action: '补齐活动与 SKU 到手价' }],
    }],
    _skillforge_meta: {
      degraded: false,
      data_health_ratio: 0.82,
      data_proofs: [{ proof_id: 'proof-1', data_scope: 'item_360', status: 'ok' }],
    },
  },
  debug_context: {
    input_snapshot: { date: '2026-05-24' },
    raw_output_result: { status: 'ok' },
  },
}

async function installReportRoutes(page: Page) {
  await page.route('**/*', async (route: Route) => {
    const url = new URL(route.request().url())
    const { pathname } = url
    if (!pathname.startsWith('/api/')) {
      await route.fallback()
      return
    }
    const json = (body: unknown) => route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(body),
    })
    if (pathname === '/api/auth/me') {
      await json({
        user_id: 'u-admin',
        username: 'admin',
        name: '管理员',
        role: 'admin',
        department: 'EC',
        can_view_all: true,
        must_change_password: false,
      })
      return
    }
    if (pathname === '/api/todos/stats') {
      await json({ pending: 0, dispatch_pending: 0 })
      return
    }
    if (pathname.startsWith('/api/changelog')) {
      await json({ versions: [{ version: '2026.05.24' }] })
      return
    }
    if (pathname === '/api/inbox/reports/10001-0') {
      await json(reportPayload)
      return
    }
    await json({})
  })
}

test.describe('Report detail shell', () => {
  test.beforeEach(async ({ page }) => {
    await installReportRoutes(page)
  })

  test('renders the design-system report shell', async ({ page }) => {
    await page.goto('/inbox/reports/10001-0')

    await expect(page.getByRole('heading', { name: reportPayload.title })).toBeVisible()
    await expect(page.locator('.report-page.ai-main')).toBeVisible()
    await expect(page.locator('.report-detail-body.ai-pagebody')).toBeVisible()
    await expect(page.locator('.report-detail-bar')).toBeVisible()
    await expect(page.locator('.report-hero')).toBeVisible()
    await expect(page.locator('.rd-layout')).toBeVisible()
    await expect(page.locator('.report-page.page-container')).toHaveCount(0)
    await expect(page.locator('.report-detail-bar .ai-btn svg path').first()).toBeVisible()

    const metrics = await page.locator('.report-page').evaluate((el) => ({
      paddingLeft: getComputedStyle(el).paddingLeft,
      bodyClass: document.querySelector('.report-detail-body')?.className,
      pageheadX: document.querySelector('.report-detail-bar')?.getBoundingClientRect().x,
      overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    }))
    expect(metrics.paddingLeft).toBe('0px')
    expect(metrics.bodyClass).toContain('ai-pagebody')
    expect(metrics.pageheadX).toBe(0)
    expect(metrics.overflow).toBe(false)
  })

  test('keeps report detail readable on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/inbox/reports/10001-0')

    await expect(page.getByRole('heading', { name: reportPayload.title })).toBeVisible()
    await expect(page.locator('.report-detail-body.ai-pagebody')).toBeVisible()
    await expect(page.locator('.report-hero')).toBeVisible()
    await expect(page.locator('.report-page.page-container')).toHaveCount(0)

    const metrics = await page.locator('.report-page').evaluate((el) => ({
      paddingLeft: getComputedStyle(el).paddingLeft,
      pageheadX: document.querySelector('.report-detail-bar')?.getBoundingClientRect().x,
      overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    }))
    expect(metrics.paddingLeft).toBe('0px')
    expect(metrics.pageheadX).toBe(0)
    expect(metrics.overflow).toBe(false)
  })
})
