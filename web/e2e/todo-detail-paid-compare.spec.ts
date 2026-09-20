import { expect, test } from '@playwright/test'
import { installMockApi } from './mock-api'

test.describe('TodoDetail paid compare promotion', () => {
  test.beforeEach(async ({ page }) => {
    await installMockApi(page)
  })

  test('promotes paid compare rows into tiles without duplicate extra rows', async ({ page }) => {
    await page.goto('/inbox/todos/3750')

    const visitorTile = page.locator('.promoted-metric-tile').filter({ hasText: '付费访客环比' })
    const conversionTile = page.locator('.promoted-metric-tile').filter({ hasText: '付费转化环比' })

    await expect(visitorTile).toBeVisible()
    await expect(visitorTile).toContainText('0.0%')
    await expect(visitorTile).toContainText('当前 100 / 对比 100')

    await expect(conversionTile).toBeVisible()
    await expect(conversionTile).toContainText('0.0%')
    await expect(conversionTile).toContainText('当前 2.0% / 对比 2.0%')

    await expect(page.locator('.row-extra').filter({ hasText: '付费访客环比' })).toHaveCount(0)
    await expect(page.locator('.row-extra').filter({ hasText: '付费转化环比' })).toHaveCount(0)
    await expect(page.locator('.row-extra').filter({ hasText: '市场Top300状态' })).toHaveCount(1)
  })

  test('renders paid and free flow basis modules from todo payload', async ({ page }) => {
    await page.goto('/inbox/todos/3750')

    const paidCard = page.locator('.detail-card').filter({ hasText: '付费实时数据' })
    await expect(paidCard).toBeVisible()
    await expect(paidCard).toContainText('实时花费金额 ¥3836.42')
    await expect(paidCard).toContainText('实时直接ROI 1.4386')
    await expect(paidCard).toContainText('实时CPC ¥9.89')
    await expect(paidCard).toContainText('测试计划A')
    await expect(paidCard).toContainText('CPC升高')
    await expect(paidCard).not.toContainText('零值缺采计划')

    const freeCard = page.locator('.detail-card').filter({ hasText: '免费流分析依据' })
    await expect(freeCard).toBeVisible()
    await expect(freeCard).toContainText('流量来源-搜索访客当前24h环比')
    await expect(freeCard).toContainText('待补采')
    await expect(freeCard).toContainText('不能使用商品整体转化率替代')
  })

  test('formats decline coefficient hero KPI as payment change percentage', async ({ page }) => {
    await page.goto('/inbox/todos/3750')

    const heroKpi = page.locator('.hero-kpi')
    await expect(heroKpi).toContainText('支付金额变化率')
    await expect(heroKpi).toContainText('-16.7%')
    await expect(heroKpi).not.toContainText('实时下滑系数')
  })
})
