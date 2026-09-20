import { test, expect } from '@playwright/test'
import { loginAsAdmin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe('T05 Prompts preview not truncated', () => {
  test('列表预览取 content（完整文本），靠 ellipsis 视觉控制', async ({ page }) => {
    // 拦截 prompts list，使 preview 是短截断（100 字）但 content 是长文本（>200 字）
    const longContent = '这是一段很长的 prompt 内容'.repeat(20)
    await page.route('**/api/admin/prompts**', async (route) => {
      if (route.request().method() !== 'GET') return route.continue()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [
            {
              name: 'test_prompt',
              default_version: 'v1',
              versions: [
                {
                  version: 'v1',
                  hash: 'abc12345abc12345',
                  content: longContent,
                  content_preview: longContent.slice(0, 80) + '...',
                },
              ],
            },
          ],
        }),
      })
    })

    await loginAsAdmin(page)
    await page.goto('/admin/prompts')
    await expect(page.getByRole('heading', { name: 'Prompt 管理' })).toBeVisible()

    // 预览单元格的 textContent 长度应与 content 一致（而非 content_preview 的 83）
    const cell = page.locator('.content-preview').first()
    await expect(cell).toBeVisible()
    const text = await cell.textContent()
    expect(text!.length).toBeGreaterThan(120)

    await snap(page, 'T05-prompts-list')
  })
})
