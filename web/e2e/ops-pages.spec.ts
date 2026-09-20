import { expect, test, type Page } from '@playwright/test'
import { installMockApi } from './mock-api'

function json(body: unknown, status = 200) {
  return {
    status,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(body),
  }
}

async function installBrowserRoutes(page: Page) {
  const state = {
    status: 'stopped',
    browser: '',
    tasks: [
      {
        id: 1,
        platform: 'sycm',
        task_type: 'check_login',
        status: 'success',
        created_at: '2026-04-16T08:10:00.000Z',
        result_preview: '登录状态正常',
      },
    ] as Array<Record<string, unknown>>,
  }

  await page.route(/\/api\/browser\/novnc\/vnc\.html(\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/html; charset=utf-8',
      body: '<!doctype html><html><body style="margin:0;background:#1a1a2e;color:#fff;font:14px sans-serif;">Mock noVNC</body></html>',
    })
  })

  await page.route(/\/api\/browser\/status$/, async (route) => {
    await route.fulfill(json({
      status: state.status,
      browser: state.browser,
    }))
  })

  await page.route(/\/api\/browser\/start$/, async (route) => {
    state.status = 'running'
    state.browser = 'Chromium 124'
    await route.fulfill(json({
      status: state.status,
      browser: state.browser,
    }))
  })

  await page.route(/\/api\/browser\/stop$/, async (route) => {
    state.status = 'stopped'
    state.browser = ''
    await route.fulfill(json({ status: state.status }))
  })

  await page.route(/\/api\/browser\/collect$/, async (route) => {
    const payload = route.request().postDataJSON() as { platform?: string; task_type?: string }
    state.tasks.unshift({
      id: state.tasks.length + 1,
      platform: payload.platform || 'sycm',
      task_type: payload.task_type || 'check_login',
      status: 'success',
      created_at: '2026-04-16T08:20:00.000Z',
      result_preview: '采集成功',
    })
    await route.fulfill(json({
      status: 'success',
      result: {
        platform: payload.platform,
        task_type: payload.task_type,
        summary: '采集成功',
      },
    }))
  })

  await page.route(/\/api\/browser\/tasks(\?.*)?$/, async (route) => {
    await route.fulfill(json({
      items: state.tasks,
      total: state.tasks.length,
    }))
  })
}

async function installContractDriftRoutes(page: Page) {
  const items = [
    {
      skill_id: 'skill-forecast',
      name: '库存优化助手',
      department: '供应链中心',
      drift_count: 3,
      ack_count: 1,
      latest: {
        drift_id: 101,
        detected_at: '2026-04-16T09:00:00.000Z',
        first_error: 'required 字段缺失',
        error_count: 2,
      },
    },
    {
      skill_id: 'skill-summary',
      name: '会议纪要整理',
      department: '行政中心',
      drift_count: 1,
      ack_count: 0,
      latest: {
        drift_id: 102,
        detected_at: '2026-04-15T16:00:00.000Z',
        first_error: 'type 不匹配',
        error_count: 1,
      },
    },
  ]

  await page.route(/\/api\/dashboard\/contract-drift\/\d+\/acknowledge$/, async (route) => {
    const id = Number(route.request().url().split('/').slice(-2)[0])
    const target = items.find((item) => item.latest.drift_id === id)
    if (target) {
      target.ack_count = target.drift_count
    }
    await route.fulfill(json({ ok: true }))
  })

  await page.route(/\/api\/dashboard\/contract-drift\/[^/]+\/propose-schema$/, async (route) => {
    await route.fulfill(json({ review_id: 201, sample_count: 8 }))
  })

  await page.route(/\/api\/dashboard\/contract-drift\/\d+$/, async (route) => {
    const id = Number(route.request().url().split('/').pop())
    await route.fulfill(json({
      drift_id: id,
      skill_id: id === 101 ? 'skill-forecast' : 'skill-summary',
      run_id: `run-${id}`,
      detected_at: '2026-04-16T09:00:00.000Z',
      acknowledged: id === 101,
      acknowledged_by: id === 101 ? 'admin' : null,
      schema_errors: ['required 字段缺失', 'type 不匹配'],
    }))
  })

  await page.route(/\/api\/dashboard\/contract-drift(\?.*)?$/, async (route) => {
    await route.fulfill(json({ items }))
  })
}

test.describe('Ops pages', () => {
  test('browser connect can start browser and collect data', async ({ page }) => {
    await installMockApi(page)
    await installBrowserRoutes(page)

    await page.goto('/datasources/browser')

    await expect(page.getByRole('heading', { name: '浏览器连接' })).toBeVisible()
    await expect(page.getByText('浏览器尚未启动')).toBeVisible()

    await page.getByRole('button', { name: '启动浏览器' }).click()
    await expect(page.getByText('远程桌面已连接')).toBeVisible()

    await page.getByRole('button', { name: '采集' }).click()
    await expect(page.getByTestId('browser-connect-result')).toContainText('采集成功')
    await expect(page.getByRole('cell', { name: '采集成功' })).toBeVisible()
  })

  test('contract drift page can open detail and acknowledge issue', async ({ page }) => {
    await installMockApi(page)
    await installContractDriftRoutes(page)

    await page.goto('/dashboard/contract-drift')

    await expect(page.getByRole('heading', { name: '契约偏离看板' })).toBeVisible()
    await expect(page.getByText('偏离总数')).toBeVisible()

    await page.getByRole('button', { name: '详情' }).first().click()
    await expect(page.getByText('Schema 错误（2 条）')).toBeVisible()

    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: '标记处理' }).first().click()
    await expect(page.getByText('已处理 3')).toBeVisible()
  })

  test('change password completes first-login flow', async ({ page }) => {
    const state = await installMockApi(page)
    state.user.must_change_password = true

    await page.route(/\/api\/auth\/change-password$/, async (route) => {
      state.user.must_change_password = false
      await route.fulfill(json({ ok: true }))
    })
    await page.route(/\/api\/skills\/hall(\?.*)?$/, async (route) => {
      await route.fulfill(json({ items: [], total: 0 }))
    })
    await page.route(/\/api\/skills\/hall\/stats$/, async (route) => {
      await route.fulfill(json({ total: 0, active: 0, drafts: 0, published: 0 }))
    })
    await page.route(/\/api\/skills\/hall\/filters$/, async (route) => {
      await route.fulfill(json({ categories: [], departments: [], tags: [] }))
    })

    await page.goto('/change-password')

    await expect(page.getByRole('heading', { name: '更新登录密码' })).toBeVisible()
    await page.locator('input[type="password"]').nth(0).fill('old-password')
    await page.locator('input[type="password"]').nth(1).fill('new-password-123')
    await page.locator('input[type="password"]').nth(2).fill('new-password-123')
    await page.getByRole('button', { name: '修改密码' }).click()

    await expect(page).toHaveURL(/\/skills\/hall/)
  })

  test('error page renders consistent error shell', async ({ page }) => {
    await page.goto('/error/403')

    await expect(page.getByText('错误码 403')).toBeVisible()
    await expect(page.getByText('无权访问')).toBeVisible()
    await expect(page.getByRole('button', { name: '返回首页' })).toBeVisible()
  })
})
