import { expect, test, type Page } from '@playwright/test'

test.describe.configure({ mode: 'serial' })

async function login(page: Page) {
  let ready = false
  for (let i = 0; i < 20 && !ready; i += 1) {
    const health = await page.request.get('http://127.0.0.1:8000/health')
    ready = health.ok()
    if (!ready) await page.waitForTimeout(300)
  }

  let resp = await page.request.post('http://127.0.0.1:8000/api/auth/login', {
    data: { username: 'admin', password: 'admin123' },
  })
  for (let i = 0; i < 10 && !resp.ok(); i += 1) {
    await page.waitForTimeout(300)
    resp = await page.request.post('http://127.0.0.1:8000/api/auth/login', {
      data: { username: 'admin', password: 'admin123' },
    })
  }
  expect(resp.ok()).toBeTruthy()
  const setCookie = resp.headers()['set-cookie']
  const match = /skillforge_session=([^;]+)/.exec(setCookie || '')
  expect(match).toBeTruthy()
  await page.context().addCookies([
    {
      name: 'skillforge_session',
      value: match![1],
      domain: '127.0.0.1',
      path: '/',
      httpOnly: true,
      sameSite: 'Lax',
    },
  ])
}

test.describe('Portal live backend flow', () => {
  test('login and use portal with real backend', async ({ page }) => {
    await login(page)

    await page.goto('/portal')
    await expect(page.getByRole('heading', { name: '应用门户' })).toBeVisible()
    await expect(page.getByText('库存优化助手')).toBeVisible()

    await page.getByText('库存优化助手').click()
    await expect(page.getByRole('heading', { name: '库存优化助手' })).toBeVisible()
    await page.getByPlaceholder('请输入关键词').fill('断货风险')
    await page.getByRole('button', { name: '运行' }).click()

    await expect(page).toHaveURL(/\/portal\/submissions\//)
    await expect(page.getByRole('heading', { name: '执行结果' })).toBeVisible()
    await expect(page.getByText('补货 120 件')).toBeVisible()
  })
})

test.describe('Admin org live backend flow', () => {
  test('login and manage org with real backend', async ({ page }) => {
    await login(page)

    await page.goto('/admin/org')
    await expect(page.getByRole('heading', { name: '组织管理' })).toBeVisible()
    await expect(page.getByText('总部 (1)', { exact: true })).toBeVisible()

    await page.getByRole('button', { name: '新建组织' }).click()
    await page.getByPlaceholder('例如 618 大促项目组').fill('Live 项目组')
    await page.locator('.arco-modal:visible').getByRole('button', { name: '确定' }).click()
    await expect(page.getByText('Live 项目组 — 成员')).toBeVisible()
  })
})
