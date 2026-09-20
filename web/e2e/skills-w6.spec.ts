/**
 * W6 最终补完：E3 / N5 / N6 / O13
 *
 * - E3 后端 endpoint 标注（源码注释，不做运行时检查）
 * - N5 Hall "批量 Fork" 按钮 + 模态框
 * - N6 Studio 浮动 "依赖图" 按钮 + 抽屉
 * - O13 useStudioDraft 挂进 Studio 作为 draft 的唯一实现（通过行为验证）
 */
import { test, expect } from '@playwright/test'
import { snap } from './helpers/snapshot'

test.describe('W6 · 最终补完', () => {
  test('N5 Hall 显示"批量 Fork"按钮', async ({ page }) => {
    await page.goto('/skills/hall')
    await page.waitForSelector('.skill-card', { timeout: 8000 })
    const btn = page.getByRole('button', { name: /批量 Fork/ })
    await expect(btn).toBeVisible()
    // 点击打开批量 fork 模态框
    await btn.click()
    await expect(page.locator('.arco-modal').filter({ hasText: '批量 Fork Skill' })).toBeVisible()
    await snap(page, 'W96-hall-batch-fork')
  })

  test('N6 W7c: Studio AssistantPane 有"依赖图"tab（FAB 已移除）', async ({ page }) => {
    const list = await page.request.get('/api/skills/?page=1&page_size=1')
    const d = await list.json()
    const id = (d.items || [])[0]?.id
    test.skip(!id, 'no real skill')

    await page.goto(`/skills/${id}`)
    await page.waitForTimeout(2500)
    // FAB 已移除
    await expect(page.locator('.studio-dep-fab')).toHaveCount(0)
    // 助手 tab 栏有"依赖图"tab
    const depTab = page.locator('.assistant-tab').filter({ hasText: '依赖图' })
    await expect(depTab).toBeVisible({ timeout: 6000 })
    await depTab.click()
    // 依赖视图渲染
    await expect(page.locator('.dep-view')).toBeVisible({ timeout: 6000 })
    await snap(page, 'W97-studio-dep-tab')
  })

  test('O13 useStudioDraft 集成：访问 Studio 不报错', async ({ page }) => {
    const list = await page.request.get('/api/skills/?page=1&page_size=1')
    const d = await list.json()
    const id = (d.items || [])[0]?.id
    test.skip(!id, 'no real skill')

    const errors: string[] = []
    page.on('pageerror', (err) => errors.push(err.message))
    await page.goto(`/skills/${id}`)
    await page.waitForTimeout(2500)
    // 加载 Studio 过程不应抛 JS error
    expect(errors).toHaveLength(0)
  })
})
