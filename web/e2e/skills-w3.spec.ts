/**
 * W3 IA 验证：一级导航顺序 + O5 Studio 创建入口
 *
 * - 顶部一级导航按产品信息架构排序：能力大厅 / AI Workflow / 项目 / 任务树 / SF / 收件 / 管理后台
 * - O5 Studio new 页主 CTA 明显，次级仅 4 轮采访 + 直接生成（Swarm 在 P4 已移除）
 */
import { test, expect } from '@playwright/test'
import { snap } from './helpers/snapshot'

test.describe('W3 · IA + Studio 创建入口', () => {
  test('顶部一级导航按指定顺序展示', async ({ page }) => {
    await page.goto('/skills')
    const primaryNav = page.locator('.primary-nav')
    await expect(primaryNav).toBeVisible()
    const labels = (await primaryNav.locator('.primary-nav-item').allTextContents())
      .map((text) => text.replace(/\d+$/, '').trim())
    expect(labels).toEqual(['能力大厅', 'AI Workflow', '项目', '任务树', 'SF', '收件', '管理后台'])
    await snap(page, 'W92-primary-nav-order')
  })

  test('/skills/hall 旧直链跳转到能力大厅并高亮大厅', async ({ page }) => {
    await page.goto('/skills/hall')
    await expect(page).toHaveURL(/\/hall/)
    await expect(page.locator('.hall-pagehead, .page-title')).toContainText('能力大厅')
    const active = page.locator('.primary-nav-item.active')
    await expect(active).toContainText('能力大厅')
  })

  test('O5 Studio new 创建入口 2 层 CTA', async ({ page }) => {
    await page.goto('/skills/new')
    await page.waitForTimeout(1200)
    // 主 CTA 存在
    await expect(page.locator('.bp-primary-btn, button').filter({ hasText: /生成任务合同/ }).first()).toBeVisible()
    // W7a 起：4 轮采访 / 直接生成 已折叠到"更多创建方式"里，默认不可见
    await expect(page.locator('.bp-secondary-actions')).toHaveCount(0)
    // 点开"更多创建方式"折叠区
    await page.locator('.bp-more-toggle').click()
    await page.waitForTimeout(200)
    await expect(page.locator('.bp-more-action').filter({ hasText: /4 轮采访式创建/ })).toBeVisible()
    await expect(page.locator('.bp-more-action').filter({ hasText: /直接生成/ })).toBeVisible()
    // Swarm 已移除（W4 之前的 D3）
    await expect(page.getByText('Swarm 协作', { exact: true })).toHaveCount(0)
    await snap(page, 'W93-studio-new-ctas')
  })
})
