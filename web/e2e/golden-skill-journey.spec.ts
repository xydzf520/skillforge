/**
 * v2.8.2 D5 · Skill 创建金链路 e2e：
 *   访问 /skills/new → 描述 → 采访 → 预览 → 命名 → 创建成功 → 跳编辑页
 *
 * 全 mock LLM / DB 响应，只验证前端状态机 + 路由流转。
 */
import type { Page, Route } from '@playwright/test'
import { expect, test } from '@playwright/test'
import { snapFull } from './helpers/snapshot'

const ROUND_1 = {
  id: 'round_1',
  title: '触发与目标',
  intro: '先搞清楚这个 Skill 什么时候跑、为什么跑。',
  questions: [
    { text: '什么时候应该触发这个 Skill？', placeholder: '例：每天早 9 点' },
    { text: '期望的产出是什么？', placeholder: '例：top5 问题链接列表' },
  ],
}

const ROUND_2 = {
  id: 'round_2',
  title: '决策规则',
  intro: '列出判断条件与兜底分支。',
  questions: [
    { text: '什么情况下判为"异常"？', placeholder: '例：流量下滑 > 30%' },
  ],
}

const SYNTH_RESULT = {
  skill: {
    meta: {
      name: '店铺下滑分析',
      department: '营销部',
      trigger_type: 'schedule',
      risk_level: 'R2',
    },
    goal: '每天监控店铺 top5 下滑链接，分析原因并推送负责人',
    rules: [
      {
        id: 'step_1',
        name: '识别下滑',
        branches: [
          { condition: 'drop_rate > 30%', conclusion: '重点关注', action: '推送告警' },
          { condition: '其他', conclusion: '忽略', action: '' },
        ],
      },
    ],
    test_cases: [{ name: '正常下滑告警' }],
    antipatterns: [{ scenario: '误报无流量链接', correct_action: '过滤 UV=0' }],
  },
  lint_report: { passed: true, error_count: 0 },
  summary: '骨架已生成并通过质检',
  can_publish: true,
}

async function mockAll(page: Page) {
  // 1. find-similar
  await page.route('**/api/skills/architect/find-similar', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) })
  })
  // 2. architect/start
  await page.route('**/api/skills/architect/start', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(ROUND_1) })
  })
  // 3. architect/next — 第一次返第二轮，第二次 done=true
  let nextCallCount = 0
  await page.route('**/api/skills/architect/next', async (route: Route) => {
    nextCallCount += 1
    const body = nextCallCount === 1
      ? JSON.stringify(ROUND_2)
      : JSON.stringify({ done: true, message: 'ok' })
    await route.fulfill({ status: 200, contentType: 'application/json', body })
  })
  // 4. architect/synthesize
  await page.route('**/api/skills/architect/synthesize', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SYNTH_RESULT) })
  })
  // 5. skills/departments
  await page.route(/\/api\/skills\/departments(\?.*)?$/, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ departments: [{ name: '营销部', skill_count: 10 }, { name: '客服部', skill_count: 5 }] }),
    })
  })
  // 6. skills/create
  await page.route('**/api/skills/create', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ skill_id: 'dian-pu-xia-hua-fen-xi-a1b2c3', git_commit: 'abc123' }),
    })
  })
  // 7. 创建后跳转的 /skills/{id} /bootstrap 兜底 mock
  await page.route(/\/api\/skills\/dian-pu-xia-hua-fen-xi-a1b2c3(?:\/bootstrap)?$/, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id: 'dian-pu-xia-hua-fen-xi-a1b2c3', name: '店铺下滑分析', department: '营销部' }),
    })
  })
}

test.describe('Skill 创建金链路（v2.8.2 D5）', () => {
  test('01 /skills/new 完整向导流程 → 跳编辑页', async ({ page }) => {
    await mockAll(page)
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.goto('/skills/new')
    await page.waitForSelector('.wizard-steps', { timeout: 8000 })

    // Step 1: 描述
    const desc = page.locator('textarea').first()
    await desc.fill('每天早上 9 点监控店铺核心指标下滑链接')
    await page.getByRole('button', { name: /开始采访/ }).click()

    // Step 2: Round 1 采访
    await page.waitForSelector('.question-block', { timeout: 8000 })
    const answers = page.locator('.question-block textarea')
    await answers.nth(0).fill('每天早 9 点')
    await answers.nth(1).fill('top5 问题链接 + 原因')
    await page.getByRole('button', { name: /下一步/ }).first().click()

    // Step 2 continued: Round 2 采访
    await page.waitForSelector('.round-intro', { timeout: 8000 })
    await expect(page.getByText('决策规则')).toBeVisible()
    await page.locator('.question-block textarea').first().fill('流量下滑 > 30%')
    await page.getByRole('button', { name: /下一步/ }).first().click()

    // Step 3: 预览（done=true 会自动触发 synthesize）
    await page.waitForSelector('.preview-section', { timeout: 10000 })
    await expect(page.getByText('店铺下滑分析').first()).toBeVisible()
    await expect(page.getByText('识别下滑').first()).toBeVisible()
    await snapFull(page, 'golden-journey-preview')

    // Step 4: 命名确认
    await page.getByRole('button', { name: /下一步/ }).first().click()
    await page.waitForSelector('input[placeholder*="例如"]', { timeout: 5000 })
    // 名称已自动预填
    await page.getByRole('button', { name: /创建 Skill/ }).click()

    // 跳到编辑页
    await page.waitForURL(/\/skills\/dian-pu-xia-hua-fen-xi/, { timeout: 8000 })
    expect(page.url()).toContain('/skills/dian-pu-xia-hua-fen-xi')
  })

  test('02 LLM 失败时显示重试按钮', async ({ page }) => {
    await mockAll(page)
    // override synthesize 返回失败
    await page.route('**/api/skills/architect/synthesize', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          skill: { _error: { type: 'LLMTimeout', message: 'upstream 30s timeout' } },
          lint_report: {},
          can_publish: false,
          summary: '骨架生成失败',
        }),
      })
    })
    await page.goto('/skills/new')
    await page.locator('textarea').first().fill('测试 LLM 失败场景')
    await page.getByRole('button', { name: /开始采访/ }).click()
    await page.waitForSelector('.question-block', { timeout: 8000 })
    await page.locator('.question-block textarea').first().fill('ans')
    await page.getByRole('button', { name: /下一步/ }).first().click()
    await page.waitForSelector('.question-block', { timeout: 8000 })
    await page.locator('.question-block textarea').first().fill('ans2')
    await page.getByRole('button', { name: /下一步/ }).first().click()

    await page.waitForSelector('.synth-error', { timeout: 10000 })
    await expect(page.getByText('骨架生成失败').first()).toBeVisible()
    await expect(page.getByRole('button', { name: /重试这一步/ })).toBeVisible()
    await snapFull(page, 'golden-journey-retry')
  })
})
