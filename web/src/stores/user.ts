/**
 * 用户状态管理：登录信息 + 主题切换（自动/亮色/暗色）
 */
import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { authApi as rawAuthApi } from '@/api'

type ThemeMode = 'auto' | 'light' | 'dark'

type UserInfo = {
  user_id?: string
  username?: string
  name?: string
  role?: string
  department?: string
  department_id?: string
  state?: 'active' | 'pending' | 'disabled' | string
  managed_departments?: string[]
  accessible_departments?: string[]
  can_view_all?: boolean
  must_change_password?: boolean
  avatar_url?: string
  email?: string
  phone?: string
  permissions_rev?: number
}

export const useUserStore = defineStore('user', () => {
  const authApi = rawAuthApi
  const userInfo = ref<UserInfo | null>(null)
  // theme: 'auto' | 'light' | 'dark'
  const theme = ref<ThemeMode>((localStorage.getItem('sf-theme') as ThemeMode) || 'auto')

  const isLoggedIn = computed(() => !!userInfo.value)
  const role = computed(() => userInfo.value?.role || '')
  const department = computed(() => userInfo.value?.department || '')
  const isAdmin = computed(() => role.value === 'admin' || role.value === 'system_admin')
  const isEngineer = computed(() => ['admin', 'ai_engineer', 'system_admin', 'aibp'].includes(role.value))
  // v2 角色体系判定
  const isSystemAdmin = computed(() => role.value === 'system_admin' || role.value === 'admin')
  const canViewAll = computed(() => Boolean(userInfo.value?.can_view_all || isSystemAdmin.value))
  const isDeptAdmin = computed(() => role.value === 'dept_admin' || role.value === 'biz_owner')
  const isAibp = computed(() => role.value === 'aibp' || role.value === 'ai_engineer')
  const isObserver = computed(() => role.value === 'observer' || role.value === 'operator')
  // v2 状态机
  const state = computed(() => userInfo.value?.state || 'active')
  const isPending = computed(() => state.value === 'pending')
  const isActive = computed(() => state.value === 'active' || (!state.value && !!userInfo.value))
  const isDisabled = computed(() => state.value === 'disabled')
  // 管理范围检查：dept_admin 只能在自己管辖的部门上操作
  function canManageDepartment(deptId: string | null | undefined): boolean {
    if (!deptId) return false
    if (isSystemAdmin.value) return true
    const managed = userInfo.value?.managed_departments || []
    return managed.includes(deptId)
  }
  // 兼容旧代码
  const darkMode = computed(() => {
    if (theme.value === 'dark') return true
    if (theme.value === 'light') return false
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  })

  async function fetchUser() {
    try {
      userInfo.value = await authApi.me() as UserInfo
    } catch {
      userInfo.value = null
    }
  }

  // T18: permissions_rev 监听
  // 每次拉 /auth/me 时如果 permissions_rev 变了，说明后端权限被撤销/更新，
  // 需要强制再 fetch 一次（保证本地 role / can_view_all / managed_departments 是最新的）。
  // 监听本身就是自我同步——外层路由 guard 检测到当前路由不匹配时会自己重定向到 403。
  let _lastPermissionsRev = 0
  watch(() => userInfo.value?.permissions_rev, (next) => {
    const n = Number(next || 0)
    if (n > 0 && n !== _lastPermissionsRev) {
      _lastPermissionsRev = n
    }
  }, { immediate: true })

  /**
   * 主动触发权限同步：业务场景（收到"权限已撤销"通知 / WebSocket 推送）调此函数，
   * 会重新拉 /auth/me；若 role 变化且当前路由的 meta.roles 不兼容，调用方应自己 router.replace('/error/403')。
   */
  let _lastPermissionSyncAt = 0
  let _permissionSyncPromise: Promise<UserInfo | null> | null = null
  async function syncPermissions(options: { force?: boolean; maxAgeMs?: number } = {}): Promise<UserInfo | null> {
    const now = Date.now()
    const maxAgeMs = Number(options.maxAgeMs ?? 60_000)
    if (!options.force && userInfo.value && now - _lastPermissionSyncAt < maxAgeMs) {
      return userInfo.value
    }
    if (_permissionSyncPromise) return _permissionSyncPromise
    _permissionSyncPromise = (async () => {
      await fetchUser()
      _lastPermissionSyncAt = Date.now()
      return userInfo.value
    })()
    try {
      return await _permissionSyncPromise
    } finally {
      _permissionSyncPromise = null
    }
  }

  async function login(username: string, password: string): Promise<UserInfo> {
    const data = await authApi.login({ username, password }) as UserInfo
    userInfo.value = data
    return data
  }

  async function logout() {
    await authApi.logout()
    userInfo.value = null
  }

  function setTheme(newTheme: ThemeMode) {
    theme.value = newTheme
    localStorage.setItem('sf-theme', newTheme)
    applyTheme()
  }

  function cycleTheme() {
    const order: ThemeMode[] = ['auto', 'light', 'dark']
    const idx = order.indexOf(theme.value)
    setTheme(order[(idx + 1) % 3])
  }

  function applyTheme() {
    const isDark = darkMode.value
    if (isDark) {
      document.body.setAttribute('arco-theme', 'dark')
    } else {
      document.body.removeAttribute('arco-theme')
    }
  }

  function toggleDark() {
    setTheme(darkMode.value ? 'light' : 'dark')
  }

  // 监听系统主题变化（auto 模式下自动响应）
  const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
  mediaQuery.addEventListener('change', () => {
    if (theme.value === 'auto') applyTheme()
  })

  // 启动时应用主题
  applyTheme()

  return {
    userInfo, darkMode, theme, isLoggedIn, role, department,
    isAdmin, isEngineer, canViewAll,
    isSystemAdmin, isDeptAdmin, isAibp, isObserver,
    state, isPending, isActive, isDisabled, canManageDepartment,
    fetchUser, login, logout, toggleDark, setTheme, cycleTheme, applyTheme, syncPermissions,
  }
})
