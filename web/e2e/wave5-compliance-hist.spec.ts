import { test, expect } from '@playwright/test'
import { loginAsAdmin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe('T16 Compliance rule version history + rollback', () => {
  test('create update versions rollback', async ({ page, request }) => {
    await loginAsAdmin(page)
    const ctxCookies = await page.context().cookies()
    const cookieHeader = ctxCookies.map(c => `${c.name}=${c.value}`).join('; ')

    const RULE_ID = `wave5-hist-${Date.now().toString(36)}`

    await request.post('http://127.0.0.1:8000/api/compliance/', {
      headers: { cookie: cookieHeader, 'content-type': 'application/json' },
      data: {
        id: RULE_ID,
        platform: 'all',
        surface: 'title',
        trigger_type: 'keyword',
        pattern_value: 'original',
        severity: 'P1',
        decision: 'block',
      },
    })

    await request.put(`http://127.0.0.1:8000/api/compliance/${RULE_ID}`, {
      headers: { cookie: cookieHeader, 'content-type': 'application/json' },
      data: { severity: 'P0' },
    })

    const v1 = await (await request.get(`http://127.0.0.1:8000/api/compliance/${RULE_ID}/versions`, {
      headers: { cookie: cookieHeader },
    })).json()
    expect(v1.items).toHaveLength(1)
    expect(v1.items[0].version_no).toBe(1)

    await page.goto('/admin/compliance')
    await expect(page.getByRole('heading', { name: '合规规则' })).toBeVisible()
    await snap(page, 'T16-history-timeline')

    const back = await request.post(`http://127.0.0.1:8000/api/compliance/${RULE_ID}/rollback/1`, {
      headers: { cookie: cookieHeader },
    })
    expect(back.ok()).toBeTruthy()
    const after = await back.json()
    expect(after.severity).toBe('P1')

    await request.delete(`http://127.0.0.1:8000/api/compliance/${RULE_ID}`, {
      headers: { cookie: cookieHeader },
    })
  })
})
