import { expect, test } from '@playwright/test'
import { installMockApi } from './mock-api'

test.describe('Portal form schema', () => {
  test.beforeEach(async ({ page }) => {
    await installMockApi(page)
  })

  test('renders schema-driven field types and can run with generated defaults', async ({ page }) => {
    await page.goto('/portal/skills/skill-forecast')

    await expect(page.getByRole('heading', { name: '库存优化助手' })).toBeVisible()
    await expect(page.getByText('重试次数')).toBeVisible()
    await expect(page.getByText('包含分析')).toBeVisible()
    await expect(page.getByText('标签列表')).toBeVisible()

    await page.getByRole('button', { name: '预览数据' }).click()
    await expect(page.locator('.preview-json')).toContainText('"retries": 2')
    await expect(page.locator('.preview-json')).toContainText('"include_analysis": false')
    await page.getByRole('button', { name: '编辑模式' }).click()
    await page.getByRole('button', { name: '运行' }).click()

    await expect(page).toHaveURL(/\/portal\/submissions\//)
    await expect(page.getByRole('heading', { name: '执行结果' })).toBeVisible()
  })
})
