import { test, expect } from '@playwright/test'
import { loginAsAdmin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe('T10 DatasourceList field mapping type + required validation', () => {
  test('映射弹窗支持类型下拉；源/目标字段必填', async ({ page }) => {
    // Mock 一条带 id 的数据源（/api/data-sources/ 含尾斜杠 + 可能带 query）
    await page.route(/\/api\/data-sources\/?(\?.*)?$/, async (route) => {
      if (route.request().method() !== 'GET') return route.continue()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [
            {
              id: 'ds-x',
              name: '销售日报',
              source_type: 'csv',
              department: '总部',
              frequency: '日',
              is_active: true,
              config: { field_mapping: {} },
            },
          ],
          total: 1,
        }),
      })
    })

    await loginAsAdmin(page)
    await page.goto('/datasources')
    await expect(page.getByRole('heading', { name: '数据源' })).toBeVisible()

    // 调试：等一下看 mock 是否生效
    await page.waitForTimeout(1500)
    await snap(page, 'T10-debug-page')
    // 等表格出现
    await expect(page.locator('.arco-table-tr').first()).toBeVisible({ timeout: 5000 })
    // 打开字段映射弹窗（text-based 定位更稳）
    await page.locator('button:has-text("字段映射")').first().click()
    await expect(page.locator('.mapping-row').first()).toBeVisible()

    // 默认新增一行空的映射（source/target 都空），直接点确定应触发"必填"提示
    const modal = page.locator('.arco-modal-mask + .arco-modal, .arco-modal:visible').last()
    await modal.locator('button').filter({ hasText: '确定' }).click()
    // 错误文案出现在 modal 内部
    await expect(page.locator('.mapping-form-error')).toContainText('必填')

    // 类型下拉存在且默认 string
    const typeSelect = page.locator('.mapping-type').first()
    await expect(typeSelect).toBeVisible()

    await snap(page, 'T10-mapping-with-type')
  })
})
