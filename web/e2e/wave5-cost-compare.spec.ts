import { test, expect } from '@playwright/test'
import { loginAsAdmin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe('T12 CostReport real prev-period via offset_days', () => {
  test('打开"对比上一周期"后，查询参数含 offset_days，环比按 key 对齐', async ({ page }) => {
    // 记录 costs/report 请求 URL，断言 offset_days 传递
    const calls: string[] = []

    // 首次（当期）返回 admin=100
    // 二次（offset_days）返回 admin=80
    await page.route(/\/api\/dashboard\/costs\/report.*/, async (route) => {
      const url = route.request().url()
      calls.push(url)
      const isCompare = url.includes('offset_days=7')
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          group_by: 'user_id',
          total_calls: 1,
          total_cost_usd: isCompare ? 80 : 100,
          total_input_tokens: 1,
          total_output_tokens: 1,
          items: [
            { key: 'admin', calls: 1, cost_usd: isCompare ? 80 : 100, input_tokens: 1, output_tokens: 1 },
          ],
          limit: 100,
          total_groups: 1,
          truncated: false,
        }),
      })
    })
    // 其他 costs/* 端点返回空
    await page.route(/\/api\/dashboard\/costs\/(top-users|by-day|call-sources).*/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [] }),
      })
    })

    await loginAsAdmin(page)
    await page.goto('/admin/costs')
    await expect(page.getByRole('heading', { name: /成本报表/ })).toBeVisible()

    // 改 days 到 7（"近 7 天"，Arco select 改动有点笨重，直接用 URL 省事）
    // 其实 default 就是 7，跳过改

    // 点"对比上一周期"复选框
    await page.locator('label', { hasText: '对比上一周期' }).click()
    // 等环比列出现
    const compareHeader = page.locator('.arco-table-th', { hasText: '对比上期' })
    await expect(compareHeader).toBeVisible()

    // 100 vs 80 → (100-80)/80 = +25.0%
    const cell = page.locator('.cost-up').first()
    await expect(cell).toContainText('+25.0%')

    // 必须至少一次请求带 offset_days
    expect(calls.some(u => /offset_days=7/.test(u))).toBeTruthy()

    await snap(page, 'T12-compare-accurate')
  })
})
