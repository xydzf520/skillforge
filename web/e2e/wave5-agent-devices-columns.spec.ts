import { test, expect } from '@playwright/test'
import { loginAsAdmin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe('T07 Agent Devices shell and columns', () => {
  test('主列表保留产品列，外层结构还原设计稿 shell', async ({ page }) => {
    await loginAsAdmin(page)
    await page.goto('/admin/agent-devices')
    await expect(page.getByRole('heading', { name: 'Agent 终端' })).toBeVisible()
    await expect(page.locator('.admin-agent-devices-pagehead')).toBeVisible()
    await expect(page.locator('.admin-agent-body.ai-pagebody')).toBeVisible()
    await expect(page.locator('.admin-agent-devices-page .page-list-card')).toHaveCount(0)
    await expect(page.locator('.agent-kpi-strip .kpi-cell')).toHaveCount(5)
    await expect(page.locator('.arco-table-th').first()).toBeVisible()
    await expect.poll(async () => page.locator('.admin-agent-devices-page').evaluate((el) => getComputedStyle(el).paddingLeft)).toBe('0px')

    // 表头列数：ID / 名称 / 部门 / Agent / 训练 / 连接状态 / 操作 = 7
    const cols = await page.locator('.arco-table-th').count()
    expect(cols).toBe(7)

    // 新的"连接状态"列应含两个小 tag（在线/离线 + 已绑定/未绑定）——
    // 至少第一行有一个 status-stack
    await expect(page.locator('.status-stack').first()).toBeVisible()
    const plusIcon = await page.locator('.admin-agent-head-actions .ai-btn svg path').first().getAttribute('d')
    expect(plusIcon).toContain('M8 3v10')

    await snap(page, 'T07-table')
  })
})
