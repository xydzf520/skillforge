/**
 * W2 后端字段 + 一致性：B5 / O10 / B4 / D4
 *
 * - B5/O10 Hall stats 后端新增 dept_count / weekly_new，前端优先读
 * - B4/D4 Portal detail 对非 active Skill 挂 warning banner + 禁用运行按钮
 */
import { test, expect } from '@playwright/test'
import { snap } from './helpers/snapshot'

test.describe('W2 · 后端字段 + 一致性', () => {
  test('B5/O10 Hall stats 新字段存在', async ({ page }) => {
    const res = await page.request.get('/api/skills/hall/stats')
    expect(res.ok()).toBeTruthy()
    const data = await res.json()
    // 两个新字段必须存在且是 number
    expect(typeof data.dept_count).toBe('number')
    expect(typeof data.weekly_new).toBe('number')
    expect(data.dept_count).toBeGreaterThanOrEqual(0)
    expect(data.weekly_new).toBeGreaterThanOrEqual(0)
  })

  test('B4/D4 Portal detail 对 draft Skill 挂 warning + 禁用运行', async ({ page }) => {
    // 从真实 API 拿任一 draft skill
    const list = await page.request.get('/api/skills/?page=1&page_size=10&status=draft')
    const data = await list.json()
    const draftId = (data.items || [])[0]?.id
    test.skip(!draftId, 'no draft skill in current DB')

    await page.goto(`/portal/skills/${draftId}`)
    await page.waitForSelector('.portal-non-active-banner', { timeout: 8000 })
    const banner = page.locator('.portal-non-active-banner')
    await expect(banner).toContainText('非正式运行')
    // 运行按钮 disabled
    const runBtn = page.getByRole('button', { name: /^运行$/ })
    await expect(runBtn).toBeDisabled()
    await snap(page, 'W91-portal-draft-banner')
  })
})
