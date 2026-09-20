import { expect, type Locator, type Page, test } from '@playwright/test'
import { installMockApi } from './mock-api'

const workbenchTabs = {
  overview: /看流程|总览/,
  edit: /改流程|编辑流程/,
}

function workbenchAction(page: Page, name: string | RegExp): Locator {
  const byText = typeof name === 'string' ? page.getByText(name, { exact: true }) : page.getByText(name)
  return page
    .getByRole('tab', { name })
    .or(page.getByRole('button', { name }))
    .or(page.getByRole('radio', { name }))
    .or(byText)
    .first()
}

test.describe('Review flows', () => {
  test.beforeEach(async ({ page }) => {
    await installMockApi(page)
  })

  test('SkillStudio submit review runs validation and readiness checks before create', async ({ page }) => {
    const calls: string[] = []
    let reviewPayload: Record<string, unknown> | null = null

    page.on('request', (request) => {
      const url = new URL(request.url())
      if (!url.pathname.startsWith('/api/')) return
      if (request.method() !== 'POST') return

      if (url.pathname.endsWith('/validate-all')) calls.push('validate')
      if (url.pathname.endsWith('/publish-readiness')) calls.push('readiness')
      if (url.pathname === '/api/reviews/') {
        calls.push('create')
        reviewPayload = JSON.parse(request.postData() || '{}') as Record<string, unknown>
      }
    })

    await page.goto('/skills/skill-forecast')

    await expect(page.getByRole('heading', { name: '库存优化助手' })).toBeVisible()
    const lockRequest = page.waitForRequest((request) => request.url().endsWith('/skills/skill-forecast/lock'))
    await page.getByTestId('enter-edit-btn').evaluate((element: HTMLElement) => element.click())
    await lockRequest
    await expect(page.getByTestId('save-menu-btn')).toBeVisible()
    await page.getByTestId('save-menu-btn').evaluate((element: HTMLElement) => element.click())
    const submitReviewButton = page.locator('.submit-review-btn').first()
    await expect(submitReviewButton).toBeVisible()
    await submitReviewButton.evaluate((element: HTMLElement) => element.click())

    await expect(page.locator('.arco-message').filter({ hasText: '已提交审核' })).toBeVisible()
    expect(calls.slice(0, 3)).toEqual(['validate', 'readiness', 'create'])
    expect(reviewPayload).toMatchObject({
      skill_id: 'skill-forecast',
      change_type: 'update',
      reason: 'SkillStudio 提交审核',
    })
  })

  test('review list routes playbook reviews to playbook detail', async ({ page }) => {
    await page.goto('/reviews')

    await expect(page.getByRole('heading', { name: '审核中心' })).toBeVisible()
    await expect(page.locator('.review-pagehead')).toBeVisible()
    await expect(page.locator('.review-shell.page-list-card')).toHaveCount(0)
    await expect(page.locator('.review-main')).toHaveCSS('padding-left', '0px')
    await expect(page.locator('.review-sidebar .ai-side-item.active svg path').first()).toHaveAttribute('d', /M3 8l3 3 7-7/)
    await expect(page.locator('.review-head-actions .ai-btn svg path').first()).toHaveAttribute('d', /M2 3h12/)
    const row = page.locator('tr').filter({ hasText: 'playbook:replenishment-check' }).first()
    await expect(row).toBeVisible()
    await row.getByRole('button', { name: '查看' }).click()

    await expect(page).toHaveURL(/\/playbook\/replenishment-check\?review_id=301$/)
    await expect(page.getByText('关联审核单')).toBeVisible()
    await expect(page.getByText('#301 · playbook:replenishment-check')).toBeVisible()
  })

  test('playbook publish creates review and can approve it', async ({ page }) => {
    await page.goto('/playbook/replenishment-check')

    await expect(page.getByRole('heading', { name: '补货巡检' })).toBeVisible()
    await expect(workbenchAction(page, workbenchTabs.overview)).toBeVisible()
    await expect(workbenchAction(page, workbenchTabs.edit)).toBeVisible()
    await expect(workbenchAction(page, '提交审核')).toBeVisible()
    await workbenchAction(page, '提交审核').click()
    await expect(page.getByText('确认提交审核')).toBeVisible()
    await page.getByRole('button', { name: '确认提交' }).click()

    await expect(page).toHaveURL(/\/playbook\/replenishment-check\?review_id=302$/)
    await expect(page.getByText('#302 · playbook:replenishment-check')).toBeVisible()
    await expect(page.getByText(/状态 pending|pending|待审核/).first()).toBeVisible()

    await page.getByRole('button', { name: '通过' }).click()

    await expect(page.locator('.arco-message').filter({ hasText: '审核已通过' })).toBeVisible()
    await expect(page.getByText(/已通过|approved|审核通过/).first()).toBeVisible()
  })

  test('review detail redirect opens playbook review page', async ({ page }) => {
    await page.goto('/review/301')

    await expect(page).toHaveURL(/\/playbook\/replenishment-check\?review_id=301$/)
    await expect(page.getByText('#301 · playbook:replenishment-check')).toBeVisible()
  })
})
