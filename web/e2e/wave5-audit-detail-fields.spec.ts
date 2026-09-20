import { test, expect } from '@playwright/test'
import { loginAsAdmin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe('T04 audit detail-fields dynamic', () => {
  test('AdminAudit onMounted 拉后端白名单并展示中文 label', async ({ page }) => {
    // 拦截 detail-fields，返回自定义白名单
    await page.route('**/api/audit/detail-fields', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          fields: [
            { key: 'custom_key', label: '自定义' },
            { key: 'username', label: '登录名' },
          ],
        }),
      })
    })
    // 注入一条带 custom_key 的 audit 记录（覆盖 /api/audit/）
    await page.route('**/api/audit/?**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [
            {
              id: 'm1',
              created_at: '2026-04-18T10:00:00Z',
              user_id: 'admin',
              action: 'skill.edit',
              target_type: 'skill',
              target_id: 'ec-test',
              detail: { custom_key: 'hello', username: 'admin' },
              ip_address: '127.0.0.1',
            },
          ],
          total: 1,
        }),
      })
    })

    await loginAsAdmin(page)
    await page.goto('/admin/audit')
    await expect(page.getByRole('heading', { name: '审计日志' })).toBeVisible()

    // 表格里应能看到我们注入的 custom_key → label "自定义"
    const row = page.locator('.detail-inline').first()
    await expect(row).toContainText('自定义')
    await expect(row).toContainText('hello')
    await expect(row).toContainText('登录名')
    await expect(row).toContainText('admin')

    await snap(page, 'T04-audit-tags')
  })
})
