/**
 * P0 Hotfix 验证：B1/B2/B3/B6/B7
 *
 * 覆盖：
 *  - B1 admin (system_admin 角色) 在 /skills 能看到"通过对话创建 Skill"卡片
 *  - B2 访问不存在的 /skills/__not_exists__ 显示 SkillNotFound 落地页，非空骨架
 *  - B3 /skills 在最近访问为空时不抛 Invalid Date，显示"—"
 *  - B6 非 Mac 平台顶栏/底栏快捷键提示显示 Ctrl+J / Ctrl+P，不再是 ⌘
 *  - B7 SkillList 状态 tab 计数为 0 时自动隐藏（"全部"除外）
 */
import { test, expect } from '@playwright/test'
import { snap } from './helpers/snapshot'

test.describe('P0 Hotfix · /skills', () => {
  test('B1 admin 在 /skills 看到"通过对话创建 Skill"卡', async ({ page }) => {
    await page.goto('/skills')
    await expect(page.locator('.page-title, h2')).toContainText('Skills 工作中心')
    // canCreate 修好后，system_admin 会看到 hub-card-primary 卡
    await expect(page.locator('.hub-card-primary')).toBeVisible()
    await expect(page.locator('.hub-card-primary')).toContainText('通过对话创建 Skill')
    await snap(page, 'T60-home-admin-canCreate')
  })

  test('B2 访问不存在的 Skill 显示 SkillNotFound', async ({ page }) => {
    await page.goto('/skills/__absolutely-not-exists-zzyx__')
    // 不应渲染主编辑器
    await expect(page.locator('.skill-not-found, [class*="not-found"]')).toBeVisible({ timeout: 8000 })
    await expect(page.locator('.skill-not-found')).toContainText('Skill 不存在或已删除')
    await expect(page.locator('.skill-not-found')).toContainText('__absolutely-not-exists-zzyx__')
    // "返回 Skill 列表" / "新建 Skill" 两个按钮在
    await expect(page.getByRole('button', { name: /返回 Skill 列表/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /新建 Skill/ })).toBeVisible()
    await snap(page, 'T61-studio-404-notfound')
  })

  test('B3 /skills 在最近访问为空时显示"—"不抛错', async ({ page }) => {
    // mock 空 list 响应
    await page.route('**/api/skills/?page=1&page_size=6', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 6 }),
      })
    })
    await page.goto('/skills')
    // meta 里"最近编辑"应该是"—"，不应出现 Invalid Date 字样
    const homeMeta = page.locator('.card-meta').first()
    await expect(homeMeta).toContainText('最近编辑：—')
    await expect(page.locator('body')).not.toContainText('Invalid Date')
    await snap(page, 'T62-home-empty-no-invalid-date')
  })

  test('B6 非 Mac 平台快捷键 hint 显示 Ctrl+', async ({ page }) => {
    // playwright 默认在 linux，navigator.platform = "Linux x86_64"
    // 先进入任一 skill 验证底栏 hint
    const res = await page.request.get('/api/skills/?page=1&page_size=1')
    const data = await res.json()
    const realId = (data.items || [])[0]?.id
    test.skip(!realId, 'no real skill to open Studio')

    await page.goto(`/skills/${realId}`)
    // 底栏等一下渲染
    await page.waitForSelector('.sb-hint', { timeout: 8000 })
    const hintText = await page.locator('.sb-hint').allInnerTexts()
    const joined = hintText.join(' | ')
    expect(joined).toContain('Ctrl+J')
    expect(joined).toContain('Ctrl+P')
    expect(joined).not.toContain('⌘')
    await snap(page, 'T63-ctrl-shortcut')
  })

  test('B7 SkillList 0 态 tab 不渲染（全部除外）', async ({ page }) => {
    // 模拟 status_counts 只有 all=3，其余为 0 的场景
    await page.route('**/api/skills/**', async (route) => {
      const url = route.request().url()
      if (url.includes('page_size=') && !url.includes('members')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json; charset=utf-8',
          body: JSON.stringify({
            items: [
              { id: 'a', name: 'A', status: 'active', department: 'EC', risk_level: 'R2' },
              { id: 'b', name: 'B', status: 'active', department: 'EC', risk_level: 'R2' },
              { id: 'c', name: 'C', status: 'active', department: 'EC', risk_level: 'R2' },
            ],
            total: 3,
            status_counts: { all: 3, active: 3, shadow: 0, draft: 0, deprecated: 0 },
          }),
        })
        return
      }
      await route.continue()
    })
    await page.goto('/skills/list')
    await page.waitForSelector('.status-tabs', { timeout: 8000 })
    // 全部 + 运行（3）应在；影子 / 草稿 chip 不渲染
    await expect(page.locator('.status-tabs .stat-chip').filter({ hasText: /全部/ })).toBeVisible()
    await expect(page.locator('.status-tabs .stat-chip').filter({ hasText: /运行/ })).toBeVisible()
    const shadowChip = page.locator('.status-tabs .stat-chip.stat-shadow')
    const draftChip = page.locator('.status-tabs .stat-chip.stat-draft')
    await expect(shadowChip).toHaveCount(0)
    await expect(draftChip).toHaveCount(0)
    await snap(page, 'T64-list-zero-tab-hidden')
  })
})
