import { expect, test, type Page, type Route } from '@playwright/test'

async function installDependencyRoutes(page: Page) {
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
    if (pathname === '/api/skills/skill-self/dependency-graph') {
      await json({
        summary: { skill_count: 2, datasource_count: 1, playbook_count: 1 },
        nodes: [
          { id: 'skill-self', route_id: 'skill-self', label: '当前 Skill', type: 'skill', status: 'active', is_self: true, can_view: true },
          { id: 'skill-child', route_id: 'skill-child', label: 'Fork Skill', type: 'skill', status: 'active', can_view: true },
          { id: 'ds-1', route_id: 'ds-1', label: '商品数据源', type: 'datasource', status: 'active', can_view: true },
          { id: 'pb-1', route_id: 'pb-1', label: '日报 Playbook', type: 'playbook', status: 'active', can_view: true },
        ],
        edges: [
          { from: 'skill-self', to: 'skill-child', kind: 'forked-into' },
          { from: 'skill-self', to: 'ds-1', kind: 'consumes' },
          { from: 'pb-1', to: 'skill-self', kind: 'calls' },
        ],
      })
      return
    }
    await json({})
  })
}

async function expectDependencyShell(page: Page) {
  await expect(page.locator('.skill-dep-page.ai-main')).toBeVisible()
  await expect(page.locator('.skill-dep-head.ai-pagehead')).toBeVisible()
  await expect(page.locator('.skill-dep-body.ai-pagebody')).toBeVisible()
  await expect(page.locator('.skill-dep-card.ai-card')).toBeVisible()
  await expect(page.locator('.skill-dep-page.page-container')).toHaveCount(0)
  await expect(page.locator('.skill-dep-page .page-list-card')).toHaveCount(0)
  await expect(page.locator('.skill-dep-page .ai-btn svg path')).toBeVisible()
  await expect(page.locator('.summary-chip svg path')).toBeVisible()

  const metrics = await page.locator('.skill-dep-page').evaluate((root) => ({
    paddingLeft: getComputedStyle(root).paddingLeft,
    pageheadX: document.querySelector('.skill-dep-head')?.getBoundingClientRect().x,
    bodyClass: document.querySelector('.skill-dep-body')?.className,
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  }))
  expect(metrics.paddingLeft).toBe('0px')
  expect(metrics.pageheadX).toBe(0)
  expect(metrics.bodyClass).toContain('ai-pagebody')
  expect(metrics.overflow).toBe(false)
}

test.describe('Skill dependency shell', () => {
  test.beforeEach(async ({ page }) => {
    await installDependencyRoutes(page)
  })

  test('renders dependency graph shell on desktop and mobile', async ({ page }) => {
    await page.goto('/skills/skill-self/deps')
    await expect(page.getByRole('heading', { name: /skill-self/ })).toBeVisible()
    await expect(page.getByText('Skill 2 · 数据源 1 · Playbook 1')).toBeVisible()
    await expectDependencyShell(page)

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/skills/skill-self/deps')
    await expect(page.getByRole('heading', { name: /skill-self/ })).toBeVisible()
    await expectDependencyShell(page)
  })
})
