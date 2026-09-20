/**
 * 头像下拉菜单 —— 数据驱动的菜单定义
 *
 * 每个 group 给 visible(userStore) 动态判定是否渲染；
 * 每个 item 可以是 link（to）或 action（click）。
 */
import type { Component } from 'vue'

export type UserStoreShape = {
  isAdmin: boolean
  isDeptAdmin: boolean
  isEngineer: boolean
}

export type UserMenuItem = {
  key: string
  label: string
  icon?: Component
  iconName?: string
  to?: string
  action?: 'logout'
  danger?: boolean
}

export type UserMenuGroup = {
  key: string
  label?: string
  visible: (u: UserStoreShape) => boolean
  items: UserMenuItem[]
}

export const USER_MENU_GROUPS: UserMenuGroup[] = [
  {
    key: 'me',
    visible: () => true,
    items: [
      { key: 'me', label: '个人设置', iconName: 'user', to: '/me' },
      { key: 'changelog', label: '更新日志', iconName: 'doc', to: '/changelog' },
    ],
  },
  {
    key: 'admin',
    visible: (u) => u.isAdmin,
    items: [
      { key: 'admin', label: '进入管理后台', iconName: 'shield', to: '/admin' },
    ],
  },
  {
    key: 'logout',
    visible: () => true,
    items: [
      { key: 'logout', label: '退出登录', iconName: 'x', action: 'logout', danger: true },
    ],
  },
]

export function filterUserMenu(u: UserStoreShape): UserMenuGroup[] {
  return USER_MENU_GROUPS.filter((g) => g.visible(u))
}
