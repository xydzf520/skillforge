import { expect, test } from '@playwright/test'
import { installMockApi } from './mock-api'

test.describe('Inbox design shell and behavior', () => {
  test.beforeEach(async ({ page }) => {
    await installMockApi(page)
  })

  test('TodoCard renders dense row fields in the restored inbox shell', async ({ page }) => {
    await page.goto('/inbox')
    await expect(page.locator('.inbox-pagehead')).toBeVisible()
    await expect(page.locator('.inbox-tabs.ai-tabs')).toBeVisible()
    await expect(page.locator('.inbox-body.ai-pagebody')).toBeVisible()
    await expect(page.locator('.inbox-center .page-list-card')).toHaveCount(0)
    await expect.poll(async () => page.locator('.inbox-main').evaluate((el) => getComputedStyle(el).paddingLeft)).toBe('0px')

    const card = page.getByTestId('todo-card-101')
    await expect(card).toBeVisible()
    await expect(card.getByTestId('todo-primary-metric')).toBeVisible()
    await expect(card.getByText('ROI')).toBeVisible()
    await expect(card.getByText('L2')).toBeVisible() // approval_level chip
    await expect(card.getByText(/由.*Bob/)).toBeVisible() // requester line
  })

  test('InboxHeader shows KPI strip below the pagehead', async ({ page }) => {
    await page.goto('/inbox')
    await expect(page.locator('.inbox-pagehead + [data-testid="inbox-header"]')).toBeVisible()
    await expect(page.locator('.inbox-kpi-strip .kpi-cell')).toHaveCount(5)
    const slaCard = page.getByTestId('inbox-sla-card')
    await expect(slaCard).toBeVisible()
    // sla_hit_rate = 0.833 → 83.3%
    await expect(slaCard.getByText(/83\.3/)).toBeVisible()
  })

  test('Todo list keeps filter row, dense header and footer pagination surface', async ({ page }) => {
    await page.goto('/inbox')
    await expect(page.locator('.filter-bar')).toBeVisible()
    await expect(page.locator('.todo-grid-header')).toBeVisible()
    await expect(page.locator('.footer-bar')).toContainText('共 2 条')
  })

  test('GAP-7/8 TodoBulkBar appears on selection and triggers batch actions', async ({ page }) => {
    await page.goto('/inbox')
    // Arco a-checkbox 渲染为 <label><input display:none/><span></span></label>，
    // 点 label 即可触发 checked
    const firstCell = page.locator('.todo-select-cell').first()
    await firstCell.click()
    await expect(page.getByTestId('todo-bulk-bar')).toBeVisible()
    await expect(page.getByText(/已选 1/)).toBeVisible()
    // 点批量通过，mock 返回 succeeded = 1
    await page.getByRole('button', { name: '批量通过' }).click()
  })
})
