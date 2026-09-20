import { expect, test, type Page, type Route } from '@playwright/test'

async function installDashboardRoutes(page: Page) {
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
    if (pathname === '/api/dashboard/overview') {
      await json({
        executions: { total: 128, success: 121 },
        active_skills: 18,
        time_saved_hours: 64,
      })
      return
    }
    if (pathname === '/api/dashboard/adoption') {
      await json([
        { skill_id: 'tmall-link-decline', adoption_rate: 86 },
        { skill_id: 'inventory-rotation', adoption_rate: 73 },
      ])
      return
    }
    if (pathname === '/api/dashboard/impact') {
      await json({
        top_skills: [
          { skill_id: 'tmall-link-decline', total_amount: '¥82,000' },
          { skill_id: 'inventory-rotation', total_amount: '¥21,400' },
        ],
      })
      return
    }
    if (pathname === '/api/dashboard/data-health') {
      await json({
        sources: [
          { name: '天猫商品数据（自动创建）', is_stale: false, department: 'EC', hours_since_update: 2 },
        ],
      })
      return
    }
    if (pathname === '/api/dashboard/trends') {
      await json({
        dates: ['2026-05-20', '2026-05-21', '2026-05-22', '2026-05-23', '2026-05-24'],
        executions: [12, 18, 22, 31, 45],
        adoptions: [8, 12, 18, 24, 39],
      })
      return
    }
    if (pathname === '/api/dashboard/change-heatmap') {
      await json({
        items: [
          { skill_id: 'tmall-link-decline', edit_count: 8, reject_count: 1 },
          { skill_id: 'inventory-rotation', edit_count: 4, reject_count: 0 },
        ],
      })
      return
    }
    if (pathname === '/api/dashboard/health') {
      await json({
        items: [
          { skill_id: 'tmall-link-decline', score: 92, grade: 'A', details: { adoption: 86, success_rate: 95 } },
          { skill_id: 'inventory-rotation', score: 84, grade: 'B', details: { adoption: 73, success_rate: 90 } },
        ],
      })
      return
    }
    await json({})
  })
}

async function expectDashboardShell(page: Page) {
  await expect(page.locator('.dashboard-page.ai-main')).toBeVisible()
  await expect(page.locator('.dashboard-head.ai-pagehead')).toBeVisible()
  await expect(page.locator('.dashboard-body.ai-pagebody')).toBeVisible()
  await expect(page.locator('.dashboard-card.ai-card').first()).toBeVisible()
  await expect(page.locator('.dashboard-page.page-container')).toHaveCount(0)
  await expect(page.locator('.dashboard-page .page-section-card')).toHaveCount(0)
  await expect(page.getByText('AI 成本分析已独立')).toBeVisible()

  const metrics = await page.locator('.dashboard-page').evaluate((root) => ({
    paddingLeft: getComputedStyle(root).paddingLeft,
    pageheadX: document.querySelector('.dashboard-head')?.getBoundingClientRect().x,
    bodyClass: document.querySelector('.dashboard-body')?.className,
    cardCount: document.querySelectorAll('.dashboard-card.ai-card').length,
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  }))
  expect(metrics.paddingLeft).toBe('0px')
  expect(metrics.pageheadX).toBe(0)
  expect(metrics.bodyClass).toContain('ai-pagebody')
  expect(metrics.cardCount).toBeGreaterThanOrEqual(6)
  expect(metrics.overflow).toBe(false)
}

test.describe('Dashboard shell', () => {
  test.beforeEach(async ({ page }) => {
    await installDashboardRoutes(page)
  })

  test('renders effect dashboard shell on desktop and mobile', async ({ page }) => {
    await page.goto('/dashboard')
    await expect(page.getByRole('heading', { name: '效果看板' })).toBeVisible()
    await expect(page.getByText('tmall-link-decline').first()).toBeVisible()
    await expectDashboardShell(page)

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/dashboard')
    await expect(page.getByRole('heading', { name: '效果看板' })).toBeVisible()
    await expectDashboardShell(page)
  })
})
