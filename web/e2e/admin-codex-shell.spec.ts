import { expect, test } from '@playwright/test'

async function mockCodexAdmin(page: any) {
  await page.route(/\/api\/codex\/admin\/usage-summary(\?.*)?$/, async (route: any) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        plugin_users: 1,
        active_sessions: 2,
        api_calls: 3,
        mcp_calls: 4,
        failed_mcp_calls: 1,
      }),
    })
  })
  await page.route(/\/api\/codex\/admin\/users(\?.*)?$/, async (route: any) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [
          {
            user_id: 'u-admin',
            user_name: '管理员',
            username: 'admin',
            department: 'EC',
            open_sessions: 2,
            session_count: 5,
            mcp_call_count: 4,
            last_codex_activity_at: '2026-05-26T08:00:00Z',
          },
        ],
      }),
    })
  })
  await page.route(/\/api\/codex\/admin\/usage-events(\?.*)?$/, async (route: any) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total: 2,
        page: 1,
        page_size: 50,
        items: [
          {
            id: 'evt-1',
            created_at: '2026-05-26T08:30:00Z',
            user_id: 'u-admin',
            user_name: '管理员',
            username: 'admin',
            department: 'EC',
            kind: 'api',
            action: 'codex.mcp.catalog',
            ok: true,
            run_mode: null,
          },
          {
            id: 'evt-2',
            created_at: '2026-05-26T08:35:00Z',
            user_id: 'u-admin',
            user_name: '管理员',
            username: 'admin',
            department: 'EC',
            kind: 'mcp',
            tool: 'tmall_sycm_market_rank',
            skill_id: 'skill-market',
            ok: false,
            run_mode: 'dry',
            dry_run: true,
          },
        ],
      }),
    })
  })
}

test.describe('Admin Codex plugin shell', () => {
  test('restores admin.jsx shell and keeps Codex data visible', async ({ page }) => {
    await mockCodexAdmin(page)
    await page.goto('/admin/codex-plugin')

    await expect(page.getByRole('heading', { name: 'Codex 插件' })).toBeVisible()
    await expect(page.locator('.admin-codex-pagehead')).toBeVisible()
    await expect(page.locator('.admin-codex-body.ai-pagebody')).toBeVisible()
    await expect(page.locator('.admin-codex-page .page-list-card')).toHaveCount(0)
    await expect(page.locator('.codex-kpi-strip')).toContainText('CLI/API 调用')
    await expect(page.locator('.codex-table-card')).toHaveCount(2)
    await expect(page.getByText('codex.mcp.catalog')).toBeVisible()
    await expect(page.getByText('tmall_sycm_market_rank')).toBeVisible()

    await expect.poll(async () => page.locator('.admin-codex-page').evaluate((el) => getComputedStyle(el).paddingLeft)).toBe('0px')
    await expect.poll(async () => page.locator('.codex-table-card').first().evaluate((el) => {
      const body = el.querySelector('.arco-card-body')
      return body ? getComputedStyle(body).paddingLeft : ''
    })).toBe('0px')
    const refreshIcon = await page.locator('.admin-codex-head-actions .ai-btn svg path').first().getAttribute('d')
    expect(refreshIcon).toContain('M2 8a6 6')
  })
})
