import { expect, test } from '@playwright/test'
import { installMockApi } from './mock-api'

test.describe('Template market', () => {
  test.beforeEach(async ({ page }) => {
    await installMockApi(page)
  })

  test('can browse template detail and fork into a new skill', async ({ page }) => {
    await page.goto('/skills/templates')
    await expect(page.getByRole('heading', { name: '模板中心' })).toBeVisible()
    await expect(page.getByText('ROI 投放检查')).toBeVisible()

    await page.getByText('ROI 投放检查').click()
    await expect(page.getByRole('heading', { name: 'ROI 投放检查' })).toBeVisible()
    await page.getByPlaceholder('新 Skill ID').fill('EC-TPL-FORK')
    await page.getByPlaceholder('部门').fill('总部')
    await page.getByRole('button', { name: 'Fork 创建' }).click()

    await expect(page).toHaveURL(/\/skills\/EC-TPL-FORK$/)
  })
})
