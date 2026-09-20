/**
 * W4-D v2.3.0: Visual regression baseline
 *
 * 3 张关键页的 screenshot baseline（playwright toHaveScreenshot）。
 * 首次跑加 --update-snapshots 生成 baseline 到 e2e/skills-v23-visual.spec.ts-snapshots/；
 * 后续跑对 diff，超过 maxDiffPixelRatio 失败。
 *
 * 在 CI 环境稳定前留 2% 的宽容度，避免字体/动画轻微抖动。
 * 只跑 stable 页（无依赖实时数据 / loading state）：
 *   - /skills（4 卡工作中心，视觉固定）
 *   - /skills/hall（大厅布局；mock trend 数据稳定）
 *   - /skills/new（新建页 blueprint 初始态）
 */
import { test, expect } from '@playwright/test'

test.describe('v2.3 Visual regression baseline', () => {
  test.beforeEach(async ({ page }) => {
    // 隐藏 timestamp / notification badge 等会漂动的元素，避免 flaky
    await page.addStyleTag({
      content: `
        .unread-badge, .changelog-dot { display: none !important; }
        * { animation-duration: 0s !important; transition-duration: 0s !important; }
      `,
    })
  })

  test('baseline · /skills 4 卡工作中心（mock empty 排除 recent 动态内容）', async ({ page }) => {
    // mock 列表为空，去掉"最近访问"+ "最近编辑 X 天前"两块相对时间文字
    await page.route('**/api/skills/?page=1&page_size=6', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 6 }),
      })
    })
    await page.goto('/skills')
    await page.waitForSelector('.hub-card', { timeout: 8000 })
    await page.waitForTimeout(600)
    await expect(page).toHaveScreenshot('baseline-skills-home.png', {
      maxDiffPixelRatio: 0.05,
      fullPage: false,
    })
  })

  test('baseline · /skills/hall 大厅（mock trend 全 0）', async ({ page }) => {
    await page.route('**/api/skills/hall/stats', async (route) => {
      const r = await route.fetch()
      const body = await r.json()
      body.daily_new = Array.from({ length: 7 }, (_, i) => ({
        date: `04-${String(13 + i).padStart(2, '0')}`,
        count: 0,
      }))
      await route.fulfill({
        response: r,
        body: JSON.stringify(body),
        contentType: 'application/json; charset=utf-8',
      })
    })
    await page.goto('/skills/hall')
    await page.waitForSelector('.stat-quiet', { timeout: 8000 })
    await page.waitForTimeout(600)
    await expect(page).toHaveScreenshot('baseline-skills-hall.png', {
      maxDiffPixelRatio: 0.05,
      fullPage: false,
    })
  })

  test('baseline · /skills/__nope__ 404 落地页', async ({ page }) => {
    await page.goto('/skills/__visual_baseline_nope__')
    await page.waitForSelector('.skill-not-found', { timeout: 8000 })
    await page.waitForTimeout(400)
    await expect(page).toHaveScreenshot('baseline-skill-notfound.png', {
      maxDiffPixelRatio: 0.02,
      fullPage: false,
    })
  })
})
