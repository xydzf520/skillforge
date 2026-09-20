import { expect, test } from '@playwright/test'
import { installMockApi } from './mock-api'

test.describe('Portal flows', () => {
  test.beforeEach(async ({ page }) => {
    await installMockApi(page)
  })

  test('can search a skill, submit it, and open the result page', async ({ page }) => {
    await page.goto('/portal')

    await expect(page.getByRole('heading', { name: '应用门户' })).toBeVisible()
    await expect(page.locator('.portal-pagehead')).toBeVisible()
    await expect(page.locator('.portal-shell.page-list-card')).toHaveCount(0)
    await expect(page.locator('.portal-skill-card').first().locator('.portal-card-icon svg path')).toHaveAttribute('d', 'M2 12l4-4 3 3 5-7 M10 4h4v4')
    await expect.poll(async () => page.locator('.portal-main').evaluate((el) => getComputedStyle(el).paddingLeft)).toBe('0px')
    await page.getByPlaceholder('搜索 Skill').fill('库存')
    await expect(page.locator('.portal-skill-card')).toHaveCount(1)

    await page.locator('.portal-skill-card').click()
    await expect(page).toHaveURL(/\/portal\/skills\/skill-forecast$/)
    await expect(page.getByRole('heading', { name: '库存优化助手' })).toBeVisible()

    await page.getByRole('button', { name: '运行', exact: true }).click()

    await expect(page).toHaveURL(/\/portal\/submissions\/submission-forecast-2$/)
    await expect(page.getByText('Portal · 执行结果')).toBeVisible()
    await expect(page.getByText('执行结果').first()).toBeVisible()
    await expect(page.getByText('补货 120 件')).toBeVisible()
    await expect(page.getByText('最近 7 天需求增长 18%')).toBeVisible()
  })

  test('can open the overview page and jump to a submission detail', async ({ page }) => {
    await page.goto('/portal/overview')

    await expect(page.getByRole('heading', { name: /部门概览/ })).toBeVisible()
    await expect(page.getByText('可用 Skill')).toBeVisible()
    await expect(page.getByText('今日执行')).toBeVisible()

    await page.getByText('详情', { exact: true }).click()
    await expect(page).toHaveURL(/\/portal\/submissions\/submission-overview-1$/)
    await expect(page.getByRole('heading', { name: '执行结果' })).toBeVisible()
  })
})

test.describe('Admin org flows', () => {
  test.beforeEach(async ({ page }) => {
    await installMockApi(page)
  })

  test('can create an org, add a member, remove it, and sync dingtalk', async ({ page }) => {
    await page.goto('/admin/org')

    await expect(page.getByRole('heading', { name: '组织管理' })).toBeVisible()
    await expect(page.getByText('组织架构')).toBeVisible()
    await expect(page.getByText('总部 (2)', { exact: true })).toBeVisible()

    await page.getByRole('button', { name: '新建组织' }).click()
    await page.getByPlaceholder('例如 618 大促项目组').fill('增长协同组')
    await page.locator('.arco-modal:visible').getByRole('button', { name: '确定' }).click()
    await expect(page.getByText('增长协同组 — 成员')).toBeVisible()

    await page.getByRole('button', { name: '添加成员' }).click()
    await page.getByPlaceholder('例如 zhangsan').fill('zhangsan')
    await page.locator('.arco-modal:visible').getByRole('button', { name: '确定' }).click()
    await expect(page.getByText('张三')).toBeVisible()

    await page.getByRole('button', { name: '移除' }).click()
    await page.getByRole('button', { name: '确定' }).last().click()
    await expect(page.getByText('张三')).toHaveCount(0)

    await page.getByRole('button', { name: '从钉钉导入' }).click()
    await expect(page.getByText('钉钉导入结果')).toBeVisible()
  })
})
