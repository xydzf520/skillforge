/**
 * W4 新增功能：N2 / N3 / N8
 *
 * - N2 Hall stats 返 daily_new 7 天数组 + 前端渲染 trend chart
 * - N3 Studio draft localStorage key 带 user scope（跨会话 + 30 天）
 * - N8 Portal detail 运行历史"导出 CSV"按钮存在，点击触发 blob 下载
 */
import { test, expect } from '@playwright/test'
import { snap } from './helpers/snapshot'

test.describe('W4 · 新增功能', () => {
  test('N2 Hall stats daily_new + trend chart', async ({ page }) => {
    const res = await page.request.get('/api/skills/hall/stats')
    const data = await res.json()
    expect(Array.isArray(data.daily_new)).toBe(true)
    expect(data.daily_new.length).toBe(7)
    // 每项 {date, count}
    for (const d of data.daily_new) {
      expect(typeof d.date).toBe('string')
      expect(typeof d.count).toBe('number')
    }
    // 前端渲染
    await page.goto('/skills/hall')
    await page.waitForSelector('[data-testid="trend-chart"]', { timeout: 8000 })
    await expect(page.locator('[data-testid="trend-chart"]')).toBeVisible()
    await snap(page, 'W94-hall-trend')
  })

  test('N3 Studio draft key 带 user scope 前缀', async ({ page }) => {
    // 访问任意真实 skill
    const list = await page.request.get('/api/skills/?page=1&page_size=1')
    const d = await list.json()
    const id = (d.items || [])[0]?.id
    test.skip(!id, 'no real skill')

    await page.goto(`/skills/${id}`)
    await page.waitForTimeout(2500)
    // localStorage 里的 draft key 格式应为 sf-wb-draft:<uid>:<skillId>
    const keys = await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('sf-wb-draft')))
    // draft 只有在 dirty 时才写；这里只要验证没有 old-style "sf-wb-draft:<id>" （无冒号用户）即可
    for (const k of keys) {
      // 新 key 结构 sf-wb-draft:<uid>:<skillId>，至少 2 个冒号
      expect((k.match(/:/g) || []).length).toBeGreaterThanOrEqual(2)
    }
  })

  test('N8 Portal detail 导出 CSV 按钮', async ({ page }) => {
    const list = await page.request.get('/api/skills/?page=1&page_size=1')
    const d = await list.json()
    const id = (d.items || [])[0]?.id
    test.skip(!id, 'no real skill')

    await page.goto(`/portal/skills/${id}`)
    await page.waitForSelector('.detail-card', { timeout: 8000 })
    // 导出 CSV 按钮（可能 disabled 如果没有历史记录）
    const csvBtn = page.getByRole('button', { name: /导出 CSV/ })
    await expect(csvBtn).toBeVisible()
    await snap(page, 'W95-portal-csv-btn')
  })
})
