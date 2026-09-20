import { expect, test } from '@playwright/test'

test.describe('Skill List', () => {
  test('skill list page matches Skills workbench shell', async ({ page }) => {
    await page.route('**/api/auth/me', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({
          user_id: 'u-admin',
          username: 'admin',
          name: '管理员',
          role: 'admin',
          department: 'AI小组',
          can_view_all: true,
          must_change_password: false,
        }),
      })
    })
    await page.route('**/api/todos/stats', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({ pending: 0, dispatch_pending: 0 }),
      })
    })
    await page.route('**/api/changelog**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({ versions: [{ version: '2026.04.13' }] }),
      })
    })
    await page.route('**/api/skills**', async (route) => {
      const url = new URL(route.request().url())
      if (url.pathname.endsWith('/departments')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json; charset=utf-8',
          body: JSON.stringify({ departments: [{ name: 'AI小组', skill_count: 1 }] }),
        })
        return
      }
      if (url.pathname.endsWith('/pinned')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json; charset=utf-8',
          body: JSON.stringify([]),
        })
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({
          items: [{
            id: 'SK-001',
            name: '日报助手',
            department: 'AI小组',
            status: 'active',
            trigger_type: 'cron',
            risk_level: 'R2',
            current_version: 'v1.0',
            usage_today: 7,
            health_score: 94,
            permissions: { edit: true, delete: true, publish: true },
          }],
          total: 1,
          status_counts: { all: 1, active: 1, shadow: 0, draft: 0, deprecated: 0 },
          view_counts: { mine_created: 0, mine_owned: 0, favorited: 0, unhealthy: 0 },
        }),
      })
    })
    await page.route('**/api/reviews**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({ items: [], total: 3 }),
      })
    })
    await page.route('**/api/org/tree', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify([]),
      })
    })

    await page.goto('/skills')
    await expect(page.getByRole('heading', { name: 'Skills' })).toBeVisible()
    await expect(page.locator('.skill-pagehead')).toBeVisible()
    await expect(page.locator('.skill-main')).toHaveCSS('padding-left', '0px')
    await expect(page.locator('.skill-pagebody.page-list-card')).toHaveCount(0)
    await expect(page.locator('.skill-sidebar .ai-side-item.active svg path').first()).toHaveAttribute('d', /M8 1l6 3\.5/)
    await expect(page.locator('.skill-head-actions .header-review-btn svg path').first()).toHaveAttribute('d', /M3 8l3 3 7-7/)
    await expect(page.locator('.skill-head-actions .ai-btn.primary svg path').first()).toHaveAttribute('d', /M8 3v10/)
    await expect(page.locator('.skill-section-tabs.ai-tabs')).toBeVisible()
    await expect(page.locator('.sf-skill-table')).toContainText('日报助手')
  })
})
