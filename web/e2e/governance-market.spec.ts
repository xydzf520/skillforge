import { expect, test } from '@playwright/test'
import { installMockApi } from './mock-api'

test.describe('Governance and market', () => {
  test.beforeEach(async ({ page }) => {
    await installMockApi(page)
  })

  test('can open portal market', async ({ page }) => {
    await page.goto('/portal/market')
    await expect(page.getByRole('heading', { name: '企业市场' })).toBeVisible()
  })

  test('can open admin governance', async ({ page }) => {
    await page.goto('/admin/governance')
    await expect(page.getByRole('heading', { name: '治理看板' })).toBeVisible()
  })
})
