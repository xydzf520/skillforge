import { describe, expect, it } from 'vitest'
import { ADMIN_SHELL_ROLES, filterAdminMenu } from '@/layouts/adminMenu'
import { filterUserMenu } from '@/layouts/userMenu'

describe('admin backstage visibility', () => {
  const nonAdminRoles = ['dept_admin', 'biz_owner', 'ai_engineer', 'aibp', 'observer', 'operator', 'employee']

  it('only admin and system_admin can enter the admin shell menu', () => {
    expect(ADMIN_SHELL_ROLES).toEqual(['admin', 'system_admin'])
    expect(filterAdminMenu('admin').length).toBeGreaterThan(0)
    expect(filterAdminMenu('system_admin').length).toBeGreaterThan(0)

    for (const role of nonAdminRoles) {
      expect(filterAdminMenu(role), `${role} should not see admin menu`).toEqual([])
    }
  })

  it('does not show admin links in the user menu for non-admin roles', () => {
    const groups = filterUserMenu({ isAdmin: false, isDeptAdmin: true, isEngineer: true })
    const links = groups.flatMap(group => group.items.map(item => item.to || ''))

    expect(links.some(link => link.startsWith('/admin'))).toBe(false)
    expect(filterUserMenu({ isAdmin: true, isDeptAdmin: false, isEngineer: true })
      .flatMap(group => group.items.map(item => item.to || '')))
      .toContain('/admin')
  })
})
