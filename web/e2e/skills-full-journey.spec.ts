/**
 * v2.2.2 完整用户旅程 e2e + 截图
 *
 * 覆盖所有 v2.2.x 新功能的交互路径，每步截图到 screenshots/wave5/J-*.png，
 * 最后一次性看所有图片对照现状。
 */
import { test, expect } from '@playwright/test'
import { snap } from './helpers/snapshot'

test.describe('Journey · v2.2.x 全功能巡检', () => {
  test('01 Hall：统计 + trend chart + 批量 Fork 入口', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/skills/hall')
    await page.waitForSelector('.stat-value', { timeout: 8000 })
    // 验证三个核心统计数字
    const stats = await page.locator('.stat-item').allInnerTexts()
    expect(stats.join('|')).toMatch(/可见 Skill/)
    expect(stats.join('|')).toMatch(/覆盖部门/)
    expect(stats.join('|')).toMatch(/本周新增/)
    // N2 trend chart 存在（desktop 视图）
    await expect(page.locator('[data-testid="trend-chart"]')).toBeVisible({ timeout: 6000 })
    // N5 批量 Fork 按钮
    await expect(page.getByRole('button', { name: /批量 Fork/ })).toBeVisible()
    await snap(page, 'J01-hall-overview')
  })

  test('02 Hall：点批量 Fork 打开模态框（列出全部 skill）', async ({ page }) => {
    await page.goto('/skills/hall')
    await page.waitForSelector('.skill-card', { timeout: 8000 })
    await page.getByRole('button', { name: /批量 Fork/ }).click()
    // 页面有 2 个 a-modal（单 Fork + 批量 Fork），按文本过滤再等可见
    const modal = page.locator('.arco-modal').filter({ hasText: '批量 Fork Skill' }).first()
    await expect(modal).toBeVisible({ timeout: 6000 })
    // checkbox 列表（至少 1 个 skill 可选）
    const options = modal.locator('.arco-checkbox')
    expect(await options.count()).toBeGreaterThan(0)
    await snap(page, 'J02-hall-batch-fork-modal')
  })

  test('03 SkillsHome：4 张 hub 卡 + 最近访问', async ({ page }) => {
    await page.goto('/skills')
    await page.waitForSelector('.hub-card', { timeout: 8000 })
    const cards = page.locator('.hub-grid .hub-card')
    expect(await cards.count()).toBe(4)
    await snap(page, 'J03-skillshome-4cards')
  })

  test('04 SkillList：SkillCard 卡片视图 + 置顶星 aria', async ({ page }) => {
    await page.addInitScript(() => { localStorage.setItem('sf-skill-view', 'card') })
    await page.goto('/skills/list')
    await page.waitForSelector('.skill-card.variant-list', { timeout: 8000 })
    // 至少 1 张卡，且带 aria-label
    const first = page.locator('.skill-card-wrap').first()
    const aria = await first.getAttribute('aria-label')
    expect(aria).toContain('Skill')
    await snap(page, 'J04-list-cards')
  })

  test('05 SkillList：右键菜单（N1）+ 表格视图 + pin 星', async ({ page }) => {
    await page.addInitScript(() => { localStorage.setItem('sf-skill-view', 'card') })
    await page.goto('/skills/list')
    const firstCard = page.locator('.skill-card-wrap').first()
    await firstCard.click({ button: 'right', force: true })
    await page.waitForSelector('.skill-card-menu', { timeout: 5000 })
    await expect(page.locator('.skill-card-menu')).toContainText('复制 ID')
    await expect(page.locator('.skill-card-menu')).toContainText('在新标签打开')
    await snap(page, 'J05-list-contextmenu')
    // 关菜单
    await page.keyboard.press('Escape')
  })

  test('06 SkillList：表格视图 + pin 星 aria-label', async ({ page }) => {
    await page.addInitScript(() => { localStorage.setItem('sf-skill-view', 'table') })
    await page.goto('/skills/list')
    await page.waitForSelector('.arco-table', { timeout: 8000 })
    // pin 星存在且带 aria-label
    const pinIcons = page.locator('.pin-star')
    const count = await pinIcons.count()
    if (count) {
      const first = pinIcons.first()
      const aria = await first.getAttribute('aria-label')
      expect(aria).toBeTruthy()
    }
    await snap(page, 'J06-list-table')
  })

  test('07 Studio：正常 skill 编辑 + AssistantPane tab（依赖图）', async ({ page }) => {
    const list = await page.request.get('/api/skills/?page=1&page_size=1')
    const d = await list.json()
    const id = (d.items || [])[0]?.id
    test.skip(!id, 'no real skill')

    await page.goto(`/skills/${id}`)
    await page.waitForSelector('.assistant-tab', { timeout: 10000 })
    await snap(page, 'J07-studio-loaded')
    // 切到"依赖图" tab
    await page.locator('.assistant-tab').filter({ hasText: '依赖图' }).click()
    await expect(page.locator('.dep-view')).toBeVisible({ timeout: 6000 })
    await snap(page, 'J08-studio-dep-tab')
    // 切回 AI 对话
    await page.locator('.assistant-tab').filter({ hasText: 'AI 对话' }).click()
    await page.waitForTimeout(500)
    await snap(page, 'J09-studio-chat-tab')
  })

  test('10 Studio 404：不存在 skill 走 SkillNotFound', async ({ page }) => {
    await page.goto('/skills/__journey_nope_42__')
    await page.waitForSelector('.skill-not-found', { timeout: 8000 })
    await expect(page.locator('.skill-not-found')).toContainText('Skill 不存在')
    await snap(page, 'J10-studio-404')
  })

  test('11 Studio 500：mock 5xx 也走 SkillNotFound（B2 扩展）', async ({ page }) => {
    const list = await page.request.get('/api/skills/?page=1&page_size=1')
    const d = await list.json()
    const id = (d.items || [])[0]?.id
    test.skip(!id, 'no real skill')
    await page.route(`**/api/skills/${id}`, async (route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: { code: 'INTERNAL', message: '模拟的 500' } }) })
      } else await route.continue()
    })
    await page.goto(`/skills/${id}`)
    await page.waitForSelector('.skill-not-found', { timeout: 8000 })
    await expect(page.locator('.skill-not-found')).toContainText(/加载.*Skill.*失败|Skill.*不存在/)
    await snap(page, 'J11-studio-500')
  })

  test('12 Studio 新建：2 层 CTA + 折叠"更多创建方式"', async ({ page }) => {
    await page.goto('/skills/new')
    await page.waitForTimeout(1500)
    await snap(page, 'J12-studio-new-closed')
    // 点开"更多创建方式"
    await page.locator('.bp-more-toggle').click()
    await page.waitForTimeout(300)
    await expect(page.locator('.bp-more-action').filter({ hasText: '4 轮采访式创建' })).toBeVisible()
    await snap(page, 'J13-studio-new-more-open')
  })

  test('14 Portal Market：筛选条 + 命中统计', async ({ page }) => {
    await page.goto('/portal/market')
    await page.waitForSelector('.market-filter', { timeout: 8000 })
    await snap(page, 'J14-portal-market')
  })

  test('15 Portal detail：draft skill 的 warning banner + 禁用运行', async ({ page }) => {
    const list = await page.request.get('/api/skills/?page=1&page_size=1')
    const d = await list.json()
    const id = (d.items || [])[0]?.id
    test.skip(!id, 'no real skill')
    await page.goto(`/portal/skills/${id}`)
    await page.waitForSelector('.portal-non-active-banner', { timeout: 8000 })
    await expect(page.locator('.portal-non-active-banner')).toContainText('非正式运行')
    const runBtn = page.getByRole('button', { name: /^运行$/ })
    await expect(runBtn).toBeDisabled()
    await snap(page, 'J15-portal-draft-banner')
  })

  test('16 通知面板：顶栏铃铛 + 抽屉', async ({ page }) => {
    await page.goto('/skills')
    await page.waitForSelector('.topbar-notif-wrap', { timeout: 8000 })
    await page.locator('.notification-trigger').click()
    await page.waitForSelector('.notification-panel', { timeout: 6000 })
    await snap(page, 'J16-notifications')
  })

  test('17 旧路由真删：/skill/new 落 404', async ({ page }) => {
    await page.goto('/skill/new')
    await page.waitForTimeout(800)
    expect(page.url()).not.toContain('/skills/new')
    await snap(page, 'J17-old-route-404')
  })

  test('18 响应式 - 手机视图（768）：4 卡单列、trend chart 隐藏', async ({ page }) => {
    await page.setViewportSize({ width: 500, height: 900 })
    await page.goto('/skills/hall')
    await page.waitForTimeout(1000)
    // < 768 时 useResponsive 应隐藏 trend chart
    await expect(page.locator('[data-testid="trend-chart"]')).toHaveCount(0)
    await snap(page, 'J18-hall-mobile')
    await page.goto('/skills')
    await page.waitForSelector('.hub-card', { timeout: 6000 })
    await snap(page, 'J19-home-mobile')
  })
})
