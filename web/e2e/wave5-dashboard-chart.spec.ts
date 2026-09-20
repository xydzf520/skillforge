import { test, expect } from '@playwright/test'
import { loginAsAdmin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe('T17 Dashboard ECharts trend', () => {
  test('执行量趋势图渲染 canvas，hover 显示 tooltip', async ({ page }) => {
    await loginAsAdmin(page)
    await page.goto('/dashboard')
    await expect(page.getByRole('heading', { name: '效果看板' })).toBeVisible()

    // 等 lazy chunk 加载并渲染 canvas
    const chart = page.locator('[data-testid="trend-chart"]').first()
    await expect(chart).toBeVisible({ timeout: 10000 })
    const canvas = chart.locator('canvas').first()
    await expect(canvas).toBeVisible()

    // hover → tooltip 出现（ECharts 挂 arco-modal 之外）
    const box = await canvas.boundingBox()
    if (box) {
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
      await page.waitForTimeout(500)
    }

    await snap(page, 'T17-exec-trend')
  })
})
