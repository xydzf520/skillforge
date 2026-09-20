/**
 * v2.7 大厅 v3 e2e：数据能力 tab + 申请访问 + 管理员审批 + 详情反链。
 *
 * 通过 page.route mock /api/hall/data 与 /api/data-sources/... 的响应，
 * 只验证 UI 行为 + 生成 11 张截图到 screenshots/wave5/。
 */
import type { Page, Route } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { snap, snapFull } from './helpers/snapshot'

// ── 3 档 visibility 的样本数据 ──
const SAMPLE_ITEMS = {
  company: {
    id: 'ds-support-chat',
    name: '客服对话流水',
    description: 'T+1 落盘的客服会话全量，含对话内容、客户 id、坐席、情感标签',
    usage_hint: '适合：质检 / 投诉归因 / 销售话术优化',
    source_type: 'api_pull',
    department: '客服部',
    visibility: 'company',
    owner_contact: 'cs_lead',
    stale_threshold_hours: 24,
    freshness_status: 'fresh',
    related_skills: ['SK-quality', 'SK-escalation'],
    my_access: { status: 'none' },
    updated_at: new Date().toISOString(),
  },
  granted: {
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
      expires_at: new Date(Date.now() + 3 * 24 * 60 * 60 * 1000).toISOString(), // 3 天后过期 → 红字警告
    },
    updated_at: new Date().toISOString(),
  },
  pending: {
    id: 'ds-sales-daily',
    name: '销售日表',
    description: '按天汇总的销售订单 + 客户归因',
    usage_hint: '销售趋势 / 渠道 ROI 分析',
    source_type: 'csv_upload',
    department: '销售部',
    visibility: 'company',
    owner_contact: 'sales_director',
    freshness_status: 'stale',
    related_skills: ['SK-sales-weekly'],
    my_access: { status: 'pending', request_id: 42 },
    updated_at: new Date(Date.now() - 36 * 60 * 60 * 1000).toISOString(),
  },
}

const DETAIL_WITH_CONSUMERS = {
  ...SAMPLE_ITEMS.company,
  consumers: [
    { id: 'SK-quality', name: '客服质检 Skill', department: '客服部', status: 'active' },
    { id: 'SK-escalation', name: '投诉升级 Skill', department: '客服部', status: 'active' },
  ],
  schema_preview: [
    { field: 'session_id', type: 'string', desc: '会话 id' },
    { field: 'user_id', type: 'string', desc: '客户 id' },
    { field: 'content', type: 'text', desc: '对话内容', sensitive: true },
  ],
}

async function mockHallRoutes(page: Page, opts: { items: any[]; detail?: any; overrideRequest?: any } = { items: [] }) {
  await page.route('**/api/hall/data**', async (route: Route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/data') && route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: opts.items, total: opts.items.length, page: 1, page_size: 50 }),
      })
      return
    }
    // /data/:id
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(opts.detail || DETAIL_WITH_CONSUMERS),
    })
  })
  // request-access 幂等 / 首次
  await page.route('**/api/data-sources/*/request-access', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(opts.overrideRequest || {
        id: 99,
        status: 'pending',
        idempotent: false,
        created_at: new Date().toISOString(),
      }),
    })
  })
  // 管理员审批列表
  await page.route('**/api/data-sources/requests/pending', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          id: 42,
          source_id: 'ds-support-chat',
          source_name: '客服对话流水',
          source_department: '客服部',
          requester_id: 'alice',
          reason: '需要客服对话数据做意图识别模型训练，覆盖三个核心场景。',
          status: 'pending',
          created_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
        },
        {
          id: 43,
          source_id: 'ds-sales-daily',
          source_name: '销售日表',
          source_department: '销售部',
          requester_id: 'bob',
          reason: '需要销售日表做季度 ROI 复盘，按渠道拆解。',
          status: 'pending',
          created_at: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
        },
      ]),
    })
  })
  // 审批通过
  await page.route('**/api/data-sources/requests/*/approve', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'approved', request_id: 42, grant_id: 1, expires_at: new Date().toISOString() }),
    })
  })
  // 审批驳回
  await page.route('**/api/data-sources/requests/*/reject', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'rejected', request_id: 42 }),
    })
  })
}

test.describe('Hall v3 · 数据能力', () => {
  test('01 company 可见 + 卡片渲染（普通用户）→ snap', async ({ page }) => {
    await mockHallRoutes(page, { items: [SAMPLE_ITEMS.company] })
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/skills/hall?tab=data')
    await page.waitForSelector('.ds-card', { timeout: 8000 })
    await expect(page.locator('.ds-card').first()).toBeVisible()
    await expect(page.getByText('客服对话流水')).toBeVisible()
    await expect(page.getByText('全公司').first()).toBeVisible()
    await expect(page.getByRole('button', { name: /申请访问/ }).first()).toBeVisible()
    await snap(page, 'hall-data-company-visible')
  })

  test('02 granted 状态卡片 + 过期警告（<7 天）→ snap', async ({ page }) => {
    await mockHallRoutes(page, { items: [SAMPLE_ITEMS.granted] })
    await page.goto('/skills/hall?tab=data')
    await page.waitForSelector('.ds-card', { timeout: 8000 })
    await expect(page.getByRole('button', { name: /查看数据/ }).first()).toBeVisible()
    await expect(page.locator('.expire-warn').first()).toBeVisible()
    await snap(page, 'hall-data-granted-state')
  })

  test('03 pending 状态按钮禁用', async ({ page }) => {
    await mockHallRoutes(page, { items: [SAMPLE_ITEMS.pending] })
    await page.goto('/skills/hall?tab=data')
    await page.waitForSelector('.ds-card', { timeout: 8000 })
    const pendingBtn = page.getByRole('button', { name: /审批中/ }).first()
    await expect(pendingBtn).toBeVisible()
    await expect(pendingBtn).toBeDisabled()
    await snap(page, 'hall-data-pending-state')
  })

  test('04 申请访问 modal → 提交（reason ≥ 20 字）', async ({ page }) => {
    await mockHallRoutes(page, { items: [SAMPLE_ITEMS.company] })
    await page.goto('/skills/hall?tab=data')
    await page.waitForSelector('.ds-card', { timeout: 8000 })
    await page.getByRole('button', { name: /申请访问/ }).first().click()
    const modal = page.locator('.arco-modal').filter({ hasText: '申请访问' }).first()
    await expect(modal).toBeVisible({ timeout: 6000 })
    await modal.locator('textarea').first().fill('需要客服对话数据做质检分析，用于覆盖投诉情绪识别、升级触发、话术优化三个场景。')
    await snap(page, 'hall-data-request-modal')
    // 提交
    await modal.getByRole('button', { name: /提交申请/ }).click()
    // toast 出现
    await expect(page.getByText(/申请已提交/)).toBeVisible({ timeout: 5000 })
  })

  test('05 详情页：反链 consumers + 字段 schema（含敏感字段标记）→ snap', async ({ page }) => {
    await mockHallRoutes(page, { items: [SAMPLE_ITEMS.company], detail: DETAIL_WITH_CONSUMERS })
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/hall/data/ds-support-chat')
    await expect(page.getByText('基础信息')).toBeVisible({ timeout: 8000 })
    await expect(page.getByText('客服质检 Skill')).toBeVisible()
    await expect(page.getByText('投诉升级 Skill')).toBeVisible()
    await expect(page.locator('.arco-tag').filter({ hasText: '敏感' })).toBeVisible()
    await snapFull(page, 'hall-data-detail-consumers')
  })

  test('06 管理员审批列表 → snap', async ({ page }) => {
    await mockHallRoutes(page, { items: [] })
    await page.goto('/admin/data-requests')
    await page.waitForSelector('.arco-table-tr', { timeout: 8000 })
    await expect(page.getByText('客服对话流水').first()).toBeVisible()
    await expect(page.locator('strong').filter({ hasText: '销售日表' })).toBeVisible()
    await expect(page.getByRole('button', { name: /通过/ }).first()).toBeVisible()
    await snapFull(page, 'hall-admin-request-list')
  })

  test('07 tab 切换同步到 URL query', async ({ page }) => {
    await mockHallRoutes(page, { items: [SAMPLE_ITEMS.company] })
    await page.goto('/skills/hall')
    await page.waitForSelector('.hall-tabs', { timeout: 8000 })
    // 点 "数据能力" tab
    await page.locator('.arco-tabs-tab').filter({ hasText: '数据能力' }).click()
    await page.waitForTimeout(300)
    expect(page.url()).toContain('tab=data')
    // 切回 Skill 能力
    await page.locator('.arco-tabs-tab').filter({ hasText: 'Skill 能力' }).click()
    await page.waitForTimeout(300)
    expect(page.url()).not.toContain('tab=data')
  })
})
