/**
 * W2 v2.3.0 数据 + 功能验证：A/C/D/F 四条（B/E 留后续）
 *
 * - W2-A fix_dept_placeholder.py 脚本独立验证（不在 e2e 里跑，只验输出存在）
 * - W2-C batch-unpublish + batch-set-tags 后端端点存在
 * - W2-D SkillList 搜索改 autocomplete，输入时出下拉
 * - W2-F batch-fork items > 50 返 422
 */
import { test, expect } from '@playwright/test'
import { snap } from './helpers/snapshot'

test.describe('v2.3 W2 · 数据 + 功能', () => {
  test('W2-C batch-unpublish 端点存在且校验长度', async ({ page }) => {
    // 空数组 → 422
    const r1 = await page.request.post('/api/skills/batch-unpublish', {
      data: { skill_ids: [] },
    })
    expect(r1.status()).toBe(422)
    // 51 条 → 422
    const ids = Array.from({ length: 51 }, (_, i) => `x${i}`)
    const r2 = await page.request.post('/api/skills/batch-unpublish', {
      data: { skill_ids: ids },
    })
    expect(r2.status()).toBe(422)
  })

  test('W2-C batch-set-tags 端点存在', async ({ page }) => {
    const r = await page.request.post('/api/skills/batch-set-tags', {
      data: { items: [] },
    })
    expect(r.status()).toBe(422)
  })

  test('W2-D SkillList 搜索改 autocomplete', async ({ page }) => {
    await page.goto('/skills/list')
    await page.waitForTimeout(800)
    // a-auto-complete 根节点有 arco-autocomplete class
    const ac = page.locator('.arco-select-view, .arco-autocomplete, [class*="autocomplete"]').first()
    // fallback: input 仍存在
    const input = page.locator('input[placeholder*="搜索 Skill"]').first()
    await expect(input).toBeVisible()
    // 输入触发下拉
    await input.fill('mei')
    await page.waitForTimeout(400)
    // autocomplete 选项下拉应出现
    const options = page.locator('.arco-select-dropdown .arco-select-option, [class*="autocomplete"] [class*="option"]')
    const count = await options.count()
    expect(count).toBeGreaterThan(0)
    await snap(page, 'V37-autocomplete')
  })

  test('W2-F batch-fork items > 50 → 422', async ({ page }) => {
    const items = Array.from({ length: 51 }, (_, i) => ({
      source_skill_id: 'x',
      new_skill_id: `y${i}`,
    }))
    const r = await page.request.post('/api/skills/batch-fork', {
      data: { department: 'EC', items },
    })
    expect(r.status()).toBe(422)
  })

  test('W2-F batch-fork items = 0 → 422', async ({ page }) => {
    const r = await page.request.post('/api/skills/batch-fork', {
      data: { department: 'EC', items: [] },
    })
    expect(r.status()).toBe(422)
  })

  test('W2-C 前端有批量下线 / 批量 Tag 按钮', async ({ page }) => {
    await page.addInitScript(() => { localStorage.setItem('sf-skill-view', 'table') })
    await page.goto('/skills/list')
    await page.waitForSelector('.arco-table', { timeout: 8000 })
    // 选中第一行
    const firstCheckbox = page.locator('.arco-table .arco-checkbox').nth(1) // skip header
    await firstCheckbox.click()
    await page.waitForTimeout(300)
    // 批量按钮可见
    await expect(page.getByRole('button', { name: /批量下线/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /批量 Tag/ })).toBeVisible()
    await snap(page, 'V36-batch-buttons')
  })
})
