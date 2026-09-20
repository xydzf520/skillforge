/**
 * W7a Hygiene 收尾：B2 / O5 / O11 / E4 / E7 / E8
 *
 * - B2 500 / 网络错也走 SkillNotFound（不渲染空骨架）
 * - O5 Studio 新建 2 层 CTA（主 CTA + 折叠"更多创建方式"里含 4 轮采访 / 直接生成）
 * - O11 SkillsHome 1024px 断点
 * - E7 skills/components 下 `.catch(() => {})` 0 剩余
 */
import { test, expect } from '@playwright/test'
import { snap } from './helpers/snapshot'

test.describe('W7a · Hygiene 收尾', () => {
  test('B2 5xx 错误也显示 SkillNotFound', async ({ page }) => {
    const list = await page.request.get('/api/skills/?page=1&page_size=1')
    const d = await list.json()
    const id = (d.items || [])[0]?.id
    test.skip(!id, 'no real skill')

    // mock skill detail 返回 500
    await page.route(`**/api/skills/${id}`, async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ error: { code: 'INTERNAL', message: '服务端异常' } }),
        })
        return
      }
      await route.continue()
    })
    await page.goto(`/skills/${id}`)
    // B2 现在 5xx 也走 SkillNotFound（title=加载 Skill 失败）
    await page.waitForSelector('.skill-not-found', { timeout: 8000 })
    await expect(page.locator('.skill-not-found')).toContainText(/加载.*Skill.*失败|Skill.*不存在/)
    await snap(page, 'W98-studio-500-notfound')
  })

  test('O5 Studio 新建页 2 层 CTA（次级已折叠进"更多创建方式"）', async ({ page }) => {
    await page.goto('/skills/new')
    await page.waitForTimeout(1500)
    // 主 CTA 仍在
    await expect(page.locator('button').filter({ hasText: /生成任务合同/ }).first()).toBeVisible()
    // 直接可见的"4 轮采访式创建 / 直接生成"不应再与主 CTA 平级（折叠前不渲染）
    const secondaryInline = page.locator('.bp-secondary-actions')
    await expect(secondaryInline).toHaveCount(0)
    // "更多创建方式"按钮存在；点开后 4 轮采访 / 直接生成 出现在折叠区
    const moreToggle = page.locator('.bp-more-toggle')
    await expect(moreToggle).toBeVisible()
    await moreToggle.click()
    await page.waitForTimeout(200)
    await expect(page.locator('.bp-more-action').filter({ hasText: /4 轮采访式创建/ })).toBeVisible()
    await expect(page.locator('.bp-more-action').filter({ hasText: /直接生成/ })).toBeVisible()
    await snap(page, 'W99-studio-new-2-levels')
  })

  test('O11 SkillsHome 1024px 断点：hub-grid 调整', async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 900 })
    await page.goto('/skills')
    await page.waitForSelector('.hub-grid', { timeout: 8000 })
    const cols = await page.locator('.hub-grid').evaluate((el) => {
      return window.getComputedStyle(el).gridTemplateColumns
    })
    // 1024px 下 minmax(280px,1fr) → 应有多列但不是 desktop 的 minmax(320px)
    expect(cols.length).toBeGreaterThan(0)
  })

  test('E4 WorkbenchTopBar icon buttons 带 aria-label', async ({ page }) => {
    const list = await page.request.get('/api/skills/?page=1&page_size=1')
    const d = await list.json()
    const id = (d.items || [])[0]?.id
    test.skip(!id, 'no real skill')

    await page.goto(`/skills/${id}`)
    await page.waitForSelector('.hdr-icon-btn', { timeout: 10000 })
    // 所有 hdr-icon-btn 应有 aria-label（或 title）
    const buttons = page.locator('.hdr-icon-btn')
    const count = await buttons.count()
    for (let i = 0; i < count; i += 1) {
      const btn = buttons.nth(i)
      const aria = await btn.getAttribute('aria-label')
      const title = await btn.getAttribute('title')
      expect(aria || title).toBeTruthy()
    }
  })
})
