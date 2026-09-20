import { test, expect } from '@playwright/test'
import { loginAsAdmin } from './helpers/login'
import { snap } from './helpers/snapshot'

test.describe('T14 Org member change audit logs', () => {
  test('add/remove membership 写入 audit_logs 且审计页可查', async ({ page, request }) => {
    await loginAsAdmin(page)
    // 使用 cookie 做后端请求（loginAsAdmin 已经 setCookie 到 context）
    const cookies = await page.context().cookies()
    const cookieHeader = cookies.map(c => `${c.name}=${c.value}`).join('; ')

    // 直接调后端：先建一个临时组织，再加/移除成员
    const unit = await (await request.post('http://127.0.0.1:8000/api/org/units', {
      headers: { cookie: cookieHeader },
      data: { name: 'wave5-audit-probe', parent_id: null, type: 'department' },
    })).json()
    expect(unit.id).toBeTruthy()

    // 加成员
    const add = await request.post('http://127.0.0.1:8000/api/org/memberships', {
      headers: { cookie: cookieHeader },
      data: { user_id: 'admin', org_unit_id: unit.id, membership_type: 'primary', is_manager: false },
    })
    expect(add.ok()).toBeTruthy()

    // 查 audit log 里有无 org.membership.add 事件
    const logs = await (await request.get(
      `http://127.0.0.1:8000/api/audit/?action=org.membership.add&page=1&page_size=5`,
      { headers: { cookie: cookieHeader } },
    )).json()
    const hit = (logs.items || []).find((x: any) => x.target_id === unit.id)
    expect(hit).toBeTruthy()

    // 去前端审计页看一下
    await page.goto(`/admin/audit`)
    await expect(page.getByRole('heading', { name: '审计日志' })).toBeVisible()
    await snap(page, 'T14-audit-log-has-entry')

    // 清理：删除 membership + 组织
    await request.delete(`http://127.0.0.1:8000/api/org/memberships/admin/${unit.id}`, {
      headers: { cookie: cookieHeader },
    })
    await request.delete(`http://127.0.0.1:8000/api/org/${unit.id}`, {
      headers: { cookie: cookieHeader },
    })
  })
})
