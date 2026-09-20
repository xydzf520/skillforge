import { expect, test, type Page, type Route } from '@playwright/test'

const runsPayload = {
  total: 2,
  items: [
    {
      id: 'run-1-abcdef',
      skill_id: 'tmall-link-decline',
      playbook_id: '',
      trigger_type: 'manual:admin',
      status: 'success',
      started_at: '2026-05-21T10:00:00+08:00',
    },
    {
      id: 'run-2-abcdef',
      skill_id: 'inventory-rotation',
      playbook_id: '',
      trigger_type: 'schedule:daily',
      status: 'failed',
      started_at: '2026-05-21T09:00:00+08:00',
    },
  ],
}

async function installExecutionRoutes(page: Page) {
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
    if (pathname === '/api/executions/runs') {
      await json(runsPayload)
      return
    }
    if (pathname === '/api/executions/runs/stats') {
      await json({ total: 2, success: 1, failed: 1, running: 0, pending: 0 })
      return
    }
    if (pathname === '/api/executions/runs/run-1-abcdef') {
      await json({
        id: 'run-1-abcdef',
        playbook_id: 'daily-review',
        trigger_type: 'manual:admin',
        status: 'success',
        summary: '执行完成，所有步骤成功。',
        completed_steps: 2,
        total_steps: 2,
        started_at: '2026-05-21T10:00:00+08:00',
        completed_at: '2026-05-21T10:02:00+08:00',
      })
      return
    }
    if (pathname === '/api/executions/runs/run-1-abcdef/steps') {
      await json([
        { id: 'step-1', skill_id: 'tmall-link-decline', step_order: 1, status: 'success', duration_ms: 320 },
        { id: 'step-2', skill_id: 'inventory-rotation', step_order: 2, status: 'success', duration_ms: 280 },
      ])
      return
    }
    if (pathname === '/api/executions/runs/run-1-abcdef/decisions') {
      await json([
        { id: 'decision-1', skill_id: 'tmall-link-decline', suggested_action: '继续观察', approval_status: 'approved', approval_level: 1, user_action: 'completed' },
      ])
      return
    }
    await json({})
  })
}

async function expectExecutionListShell(page: Page) {
  await expect(page.locator('.execution-list-page.ai-main')).toBeVisible()
  await expect(page.locator('.execution-list-head.ai-pagehead')).toBeVisible()
  await expect(page.locator('.execution-list-body.ai-pagebody')).toBeVisible()
  await expect(page.locator('.execution-list-card.ai-card')).toBeVisible()
  await expect(page.locator('.execution-list-page.page-container')).toHaveCount(0)
  await expect(page.locator('.execution-list-page .page-list-card')).toHaveCount(0)
  await expect(page.locator('.filter-bar .ai-btn svg path')).toBeVisible()

  const metrics = await page.locator('.execution-list-page').evaluate((root) => ({
    paddingLeft: getComputedStyle(root).paddingLeft,
    pageheadX: document.querySelector('.execution-list-head')?.getBoundingClientRect().x,
    bodyClass: document.querySelector('.execution-list-body')?.className,
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  }))
  expect(metrics.paddingLeft).toBe('0px')
  expect(metrics.pageheadX).toBe(0)
  expect(metrics.bodyClass).toContain('ai-pagebody')
  expect(metrics.overflow).toBe(false)
}

async function expectExecutionDetailShell(page: Page) {
  await expect(page.locator('.execution-detail-page.ai-main')).toBeVisible()
  await expect(page.locator('.execution-detail-head.ai-pagehead')).toBeVisible()
  await expect(page.locator('.execution-detail-body.ai-pagebody')).toBeVisible()
  await expect(page.locator('.execution-detail-card.ai-card').first()).toBeVisible()
  await expect(page.locator('.execution-detail-page.page-container')).toHaveCount(0)
  await expect(page.locator('.execution-detail-page .page-section-card')).toHaveCount(0)
  await expect(page.locator('.execution-detail-head .ai-btn svg path')).toBeVisible()

  const metrics = await page.locator('.execution-detail-page').evaluate((root) => ({
    paddingLeft: getComputedStyle(root).paddingLeft,
    pageheadX: document.querySelector('.execution-detail-head')?.getBoundingClientRect().x,
    bodyClass: document.querySelector('.execution-detail-body')?.className,
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  }))
  expect(metrics.paddingLeft).toBe('0px')
  expect(metrics.pageheadX).toBe(0)
  expect(metrics.bodyClass).toContain('ai-pagebody')
  expect(metrics.overflow).toBe(false)
}

test.describe('Execution shells', () => {
  test.beforeEach(async ({ page }) => {
    await installExecutionRoutes(page)
  })

  test('renders execution list and detail shells on desktop and mobile', async ({ page }) => {
    await page.goto('/executions')
    await expect(page.getByRole('heading', { name: '执行监控' })).toBeVisible()
    await expect(page.getByText('tmall-link-decline')).toBeVisible()
    await expectExecutionListShell(page)

    await page.goto('/execution/run-1-abcdef')
    await expect(page.getByRole('heading', { name: '执行详情' })).toBeVisible()
    await expect(page.getByText('执行完成，所有步骤成功。')).toBeVisible()
    await expectExecutionDetailShell(page)

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/executions')
    await expect(page.getByRole('heading', { name: '执行监控' })).toBeVisible()
    await expectExecutionListShell(page)

    await page.goto('/execution/run-1-abcdef')
    await expect(page.getByRole('heading', { name: '执行详情' })).toBeVisible()
    await expectExecutionDetailShell(page)
  })
})
