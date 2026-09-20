/**
 * v2.7 大厅 v3 补充 e2e：Skill profile 画像行 + 团队详情页 + 敏感字段 unmask 流程。
 */
import type { Page, Route } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { snap, snapFull } from './helpers/snapshot'

const SKILL_WITH_PROFILE = {
  items: [
    {
      id: 'SK-complaint-triage',
      name: '投诉分级 Skill',
      description: '按关键词 + 情绪分把客服投诉分 P0/P1/P2',
      department: '客服部',
      category: '质检',
      risk_level: 'R2',
      status: 'active',
      visibility: 'company',
      owner: 'cs_owner',
      owner_name: '王二',
      usage_count: 1243,
      fork_count: 2,
      can_fork: true,
      is_member: false,
      profile: {
        usage_count: 1243,
        success_rate: 0.93,
        last_run_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
        adoption_departments: ['营销部', '产品部', '销售部'],
        data_sources: [
          { id: 'ds-support-chat', name: '客服对话流水' },
          { id: 'ds-complaint-tags', name: '投诉标签库' },
        ],
      },
    },
  ],
  total: 1,
  page: 1,
  page_size: 24,
}

const TEAM_DETAIL = {
  department: '客服部',
  summary: {
    skill_count_total: 8,
    skill_count_active: 6,
    data_sources_count: 3,
    member_count: 12,
  },
  skills: [
    {
      id: 'SK-complaint-triage',
      name: '投诉分级 Skill',
      description: '按关键词 + 情绪分把客服投诉分 P0/P1/P2',
      category: '质检',
      status: 'active',
      risk_level: 'R2',
      usage_count: 1243,
      success_rate: 0.93,
      last_run_at: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
    },
    {
      id: 'SK-escalation',
      name: '投诉升级 Skill',
      status: 'active',
      category: '派单',
      usage_count: 56,
      success_rate: null,
      last_run_at: null,
    },
  ],
  data_sources: [
    { id: 'ds-support-chat', name: '客服对话流水', visibility: 'company' },
    { id: 'ds-complaint-tags', name: '投诉标签库', visibility: 'department' },
  ],
  members: [
    { user_id: 'wang_er', name: '王二', role: 'ai_engineer' },
    { user_id: 'li_si', name: '李四', role: 'operator' },
    { user_id: 'lin_da', name: '林大', role: 'dept_admin' },
  ],
}

const DATA_DETAIL_MASKED = {
  id: 'ds-private-marketing',
  name: '营销留资明细',
  description: '仅授权成员可见的留资数据，含手机号',
  usage_hint: '精准营销 / 转化漏斗',
  source_type: 'csv_upload',
  department: '营销部',
  visibility: 'private',
  owner_contact: 'mkt_owner',
  freshness_status: 'fresh',
  related_skills: [],
  consumers: [],
  schema_preview: [
    { field: 'user_id', type: 'string', desc: '用户 ID' },
    { field: 'phone', type: 'string', desc: '[脱敏 · 申请访问后可查看]', sensitive: true },
    { field: 'submitted_at', type: 'datetime', desc: '提交时间' },
  ],
  has_sensitive_fields: true,
  schema_unmasked: false,
  my_access: { status: 'granted', expires_at: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString() },
  updated_at: new Date().toISOString(),
}

const DATA_DETAIL_UNMASKED = {
  ...DATA_DETAIL_MASKED,
  schema_preview: [
    { field: 'user_id', type: 'string', desc: '用户 ID' },
    { field: 'phone', type: 'string', desc: '客户手机号（脱敏前 11 位）', sensitive: true },
    { field: 'submitted_at', type: 'datetime', desc: '提交时间' },
  ],
  schema_unmasked: true,
}


async function mockSkillHall(page: Page) {
  await page.route('**/api/skills/hall**', async (route: Route) => {
    const url = new URL(route.request().url())
    if (url.searchParams.get('include')?.includes('profile')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SKILL_WITH_PROFILE) })
    } else {
      // 不带 include 的情况下返回相同数据但去掉 profile 字段
      const noProfile = {
        ...SKILL_WITH_PROFILE,
        items: SKILL_WITH_PROFILE.items.map(({ profile: _p, ...rest }) => rest),
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(noProfile) })
    }
  })
  await page.route('**/api/skills/hall/stats', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ total_visible: 24, by_department: [], by_category: [], recently_published: [], most_forked: [], trending: [] }) })
  })
  await page.route('**/api/skills/hall/filters', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ departments: [], categories: [] }) })
  })
}

test.describe('Hall v3 补充 · profile + team detail + unmask', () => {
  test('01 SkillCard 带 profile 画像行（成功率/采纳部门/依赖数据）→ snap', async ({ page }) => {
    await mockSkillHall(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/skills/hall?tab=skill')
    await page.waitForSelector('.card-profile', { timeout: 8000 })
    // 成功率
    await expect(page.getByText(/成功率.*93%/).first()).toBeVisible()
    // 采纳部门 tag（营销 / 产品 / 销售）
    await expect(page.locator('.card-profile').getByText('营销部')).toBeVisible()
    // 依赖数据源
    await expect(page.getByText(/依赖.*2.*个数据源/).first()).toBeVisible()
    await snap(page, 'skill-card-profile-enhanced')
  })

  test('02 profile 字段缺失时降级隐藏（不破坏旧卡片）', async ({ page }) => {
    // mock 返回不带 profile 的响应
    await page.route('**/api/skills/hall**', async (route: Route) => {
      const noProfile = {
        ...SKILL_WITH_PROFILE,
        items: SKILL_WITH_PROFILE.items.map(({ profile: _p, ...rest }) => rest),
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(noProfile) })
    })
    await page.route('**/api/skills/hall/stats', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ total_visible: 24, by_department: [], by_category: [], recently_published: [], most_forked: [], trending: [] }) })
    })
    await page.route('**/api/skills/hall/filters', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ departments: [], categories: [] }) })
    })
    await page.goto('/skills/hall?tab=skill')
    await page.waitForSelector('.skill-card', { timeout: 8000 })
    // 画像行不应出现
    expect(await page.locator('.card-profile').count()).toBe(0)
    await snap(page, 'skill-card-no-profile-fallback')
  })

  test('03 团队详情页：summary + skills + ds + members → snapFull', async ({ page }) => {
    await page.route('**/api/hall/team/*', async (route: Route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(TEAM_DETAIL) })
    })
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/hall/team/%E5%AE%A2%E6%9C%8D%E9%83%A8')
    await page.waitForSelector('.summary-card', { timeout: 8000 })
    // 4 个 summary 数字
    await expect(page.getByText('Skill 总数')).toBeVisible()
    await expect(page.getByText('投诉分级 Skill')).toBeVisible()
    await expect(page.locator('.ds-tag').filter({ hasText: '客服对话流水' })).toBeVisible()
    await expect(page.getByText('王二')).toBeVisible()
    await expect(page.getByText('AI 工程师')).toBeVisible()
    await snapFull(page, 'hall-team-detail-page')
  })

  test('04 数据详情页：敏感字段脱敏 → 点击解除 → 显示原值 + 已审计 tag', async ({ page }) => {
    let unmaskCalled = false
    await page.route('**/api/hall/data/*', async (route: Route) => {
      const url = new URL(route.request().url())
      const isUnmask = url.searchParams.get('unmask') === 'true'
      if (isUnmask) unmaskCalled = true
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(isUnmask ? DATA_DETAIL_UNMASKED : DATA_DETAIL_MASKED),
      })
    })
    await page.goto('/hall/data/ds-private-marketing')
    await page.waitForSelector('.arco-table-tr', { timeout: 8000 })
    // 初始：脱敏占位
    await expect(page.getByText('[脱敏 · 申请访问后可查看]')).toBeVisible()
    await expect(page.getByRole('button', { name: /显示敏感字段/ })).toBeVisible()
    // 点击解除
    await page.getByRole('button', { name: /显示敏感字段/ }).click()
    await expect(page.getByText('客户手机号（脱敏前 11 位）')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('已解除脱敏 · 已审计')).toBeVisible()
    expect(unmaskCalled).toBe(true)
    await snap(page, 'hall-data-unmasked')
  })
})
