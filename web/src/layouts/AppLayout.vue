<template>
  <a-layout class="app-layout">
    <a-layout>
      <a-layout-header class="top-bar">
        <div class="top-bar-left">
          <button type="button" class="top-bar-brand brand-clickable" aria-label="SkillForge 返回能力大厅" @click="goHome">
            <SfBrand size="sm" />
            <span class="brand-role-pill">{{ userStore.isAdmin ? '管理员' : '员工' }}</span>
          </button>
        </div>

        <div class="top-bar-center desktop-only">
          <div class="primary-nav">
            <router-link
              v-for="item in primaryNavItems"
              :key="item.key"
              :to="item.path"
              class="primary-nav-item"
              :class="{ active: currentPrimary === item.key, 'has-alert-badge': item.key === 'inbox' && item.badge }"
            >
              <SfShellIcon :name="item.icon" class="primary-nav-icon" />
              <span>{{ item.label }}</span>
              <span
                v-if="item.badge"
                class="primary-badge"
                :class="{ 'primary-badge-alert': item.key === 'inbox' }"
                :aria-label="item.key === 'inbox' ? `收件新提醒 ${item.badge} 条` : undefined"
              >
                {{ formatNavBadge(item.badge) }}
              </span>
            </router-link>
          </div>
        </div>
        <div class="topbar-spacer desktop-only"></div>
        <button class="topbar-search desktop-only" type="button" @click="openCommandPalette">
          <icon-search />
          <span class="topbar-search-text">搜索能力、Skill、报告…</span>
          <span class="topbar-search-kbd">⌘K</span>
        </button>
        <div v-if="mobileMenuOpen" class="mobile-nav-overlay" @click="mobileMenuOpen = false"></div>
        <div v-if="mobileMenuOpen" class="mobile-nav-menu">
          <div class="mobile-nav-section-label">一级导航</div>
          <router-link
            v-for="item in primaryNavItems"
            :key="item.key"
            :to="item.path"
            class="mobile-nav-item"
            :class="{ active: currentPrimary === item.key }"
            @click="mobileMenuOpen = false"
          >
            <span class="mobile-nav-main">
              <SfShellIcon :name="item.icon" class="mobile-nav-icon" />
              <span>{{ item.label }}</span>
            </span>
            <span
              v-if="item.badge"
              class="primary-badge"
              :class="{ 'primary-badge-alert': item.key === 'inbox' }"
              :aria-label="item.key === 'inbox' ? `收件新提醒 ${item.badge} 条` : undefined"
            >
              {{ formatNavBadge(item.badge) }}
            </span>
          </router-link>
          <template v-if="secondaryNavItems.length">
            <div class="mobile-nav-section-label">{{ secondaryNavSectionLabel }}</div>
            <router-link
              v-for="item in secondaryNavItems"
              :key="item.key"
              :to="item.to"
              class="mobile-nav-item"
              :class="{ active: isSubnavActive(item) }"
              @click="mobileMenuOpen = false"
            >
              <span class="mobile-nav-main">
                <SfShellIcon v-if="item.iconName" :name="item.iconName" class="mobile-nav-icon" />
                <component v-else-if="item.icon" :is="item.icon" class="mobile-nav-icon" />
                <span>{{ item.label }}</span>
              </span>
              <span v-if="item.online === true" class="online-dot online" />
              <span v-else-if="item.online === false" class="online-dot offline" />
            </router-link>
          </template>
        </div>

        <div class="top-bar-right">
          <button class="mobile-menu-btn" @click="mobileMenuOpen = !mobileMenuOpen">
            <icon-menu />
          </button>
          <a-tooltip content="更新日志" position="br">
            <button
              class="topbar-icon-btn changelog-btn"
              type="button"
              :class="{ 'has-unread': hasUnreadChangelog }"
              :aria-label="hasUnreadChangelog ? '更新日志（有新版本）' : '更新日志'"
              @click="goChangelog"
            >
              <SfShellIcon name="doc" />
              <span v-if="hasUnreadChangelog" class="topbar-dot"></span>
            </button>
          </a-tooltip>
          <div class="topbar-divider"></div>
          <a-dropdown
            v-model:popup-visible="userMenuOpen"
            trigger="click"
            position="br"
            :popup-max-height="false"
          >
            <button type="button" class="user-btn">
              <a-avatar :size="26" :image-url="userAvatar" class="user-avatar">
                {{ (userStore.userInfo?.name || '?')[0] }}
              </a-avatar>
              <span class="user-meta">
                <span class="user-name">{{ userStore.userInfo?.name || userStore.userInfo?.username || '账号' }}</span>
                <span class="user-role">{{ (roleLabel as Record<string, unknown>)[userStore.role] || userStore.role }}</span>
              </span>
              <icon-down class="user-arrow" />
            </button>
            <template #content>
              <div class="user-panel">
                <div class="panel-user-info">
                  <a-avatar :size="36" :image-url="userAvatar">{{ (userStore.userInfo?.name || '?')[0] }}</a-avatar>
                  <div class="panel-user-meta">
                    <div class="panel-user-name">{{ userStore.userInfo?.name || userStore.userInfo?.username }}</div>
                    <div class="panel-user-handle">@{{ userStore.userInfo?.username || '-' }}</div>
                    <div class="panel-user-role">{{ (roleLabel as Record<string, unknown>)[userStore.role] || userStore.role }} · {{ userStore.department || '-' }}</div>
                  </div>
                </div>

                <template v-for="group in visibleMenuGroups" :key="group.key">
                  <div class="panel-divider" />
                  <div class="panel-section" :class="{ 'panel-section-danger': group.key === 'logout' }">
                    <div v-if="group.label" class="panel-section-label">{{ group.label }}</div>
                    <div class="panel-menu">
                      <button
                        v-for="item in group.items"
                        :key="item.key"
                        type="button"
                        class="panel-menu-item"
                        :class="{ 'panel-menu-danger': item.danger }"
                        @click="handleMenuItem(item)"
                      >
                        <SfShellIcon v-if="item.iconName" :name="item.iconName" />
                        <component v-else-if="item.icon" :is="item.icon" />
                        <span>{{ item.label }}</span>
                      </button>
                    </div>
                  </div>
                </template>
              </div>
            </template>
          </a-dropdown>
        </div>
      </a-layout-header>

      <div v-if="secondaryNavItems.length && !hideSecondaryNav" class="sub-nav-bar">
        <div class="sub-nav-inner">
          <router-link
            v-for="item in secondaryNavItems"
            :key="item.key"
            :to="item.to"
            class="sub-nav-item"
            :class="{ active: isSubnavActive(item) }"
          >
            <SfShellIcon v-if="item.iconName" :name="item.iconName" class="sub-nav-icon" />
            <component v-else-if="item.icon" :is="item.icon" class="sub-nav-icon" />
            <span>{{ item.label }}</span>
            <span v-if="item.online === true" class="online-dot online" />
            <span v-else-if="item.online === false" class="online-dot offline" />
          </router-link>
        </div>
      </div>

      <a-layout-content class="main-content">
        <router-view v-slot="{ Component, route: slotRoute }">
          <keep-alive :include="cachedPages">
            <component :is="Component" :key="routeViewKeyFor(slotRoute)" />
          </keep-alive>
        </router-view>
      </a-layout-content>
    </a-layout>

    <component
      :is="commandPaletteComponent"
      v-if="commandPaletteMounted"
      ref="commandPaletteRef"
    />
    <UndoToast ref="undoToastRef" />
    <TaskDock />
  </a-layout>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, provide, ref, watch, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { changelogApi as rawChangelogApi, notificationApi as rawNotificationApi, todoApi as rawTodoApi } from '@/api'
import {
  IconDown, IconMenu, IconSearch,
} from '@arco-design/web-vue/es/icon'
import UndoToast from '@/components/UndoToast.vue'
import TaskDock from '@/components/TaskDock.vue'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'
import SfBrand from '@/components/brand/SfBrand.vue'
import {
  PRIMARY_NAV_LABELS,
  WORKFLOW_DEFAULT_PATH,
  WORKFLOW_SUBNAV_LABELS,
  migratePrimaryNavStorage,
  persistPrimaryNav as persistPrimaryNavStorage,
} from './primaryNav'
import { filterUserMenu, type UserMenuItem } from './userMenu'
import { userAvatarUrl } from '@/utils/avatar'

const changelogApi: any = rawChangelogApi
const notificationApi: any = rawNotificationApi
const todoApi: any = rawTodoApi
const route: any = useRoute()
const router: any = useRouter()
const userStore: any = useUserStore()
const cachedPages = ['SkillList', 'ReviewList', 'ExecutionList', 'Dashboard', 'TodoCenter', 'PortalHome', 'PortalMarket', 'PortalSkillDetail', 'PortalSubmissionDetail', 'PortalOverview', 'AgentHome', 'TrainingHome', 'TrainingDatasets', 'TrainingModels', 'KnowledgeHome', 'AdminOrg', 'AdminGovernance', 'TaskTree', 'SfDashboard', 'ProjectHost', 'ProjectDetail']

function routeViewKeyFor(r: any) {
  // SkillStudio 与 SkillStudioCreate 共用同一个组件；按 skillId 区分 key，
  // 保证 /skills/new → /skills/:id 切换时强制 remount，避免组件保留创建空态
  if (r?.name === 'SkillStudio') return `studio:${r.params?.id ?? ''}`
  if (r?.name === 'SkillStudioCreate') return 'studio:new'
  return String(r?.name ?? r?.fullPath ?? '')
}
const commandPaletteRef = ref<any>(null)
const commandPaletteComponent = ref<Component | null>(null)
const commandPaletteMounted = ref(false)
const undoToastRef = ref<any>(null)
provide('undoToast', undoToastRef)

const mobileMenuOpen = ref(false)
const userMenuOpen = ref(false)
const currentTheme = ref(userStore.theme)
const todoStats = ref<any>({ pending: 0 })
const notificationUnreadCount = ref(0)
const latestChangelogVersion = ref('')
const lastReadChangelogVersion = ref(localStorage.getItem('sf-changelog-read') || '')
const navBadgeCacheKey = computed(() => `skillforge:nav-badges:${userStore.userInfo?.user_id || userStore.userInfo?.username || 'anon'}:${userStore.userInfo?.permissions_rev || 0}`)
const NAV_REFRESH_TTL_MS = 60_000
let lastNavRefreshAt = 0
let navRefreshPromise: Promise<void> | null = null
let wasInInbox = false

const hasUnreadChangelog = computed(() => {
  return Boolean(latestChangelogVersion.value) && latestChangelogVersion.value !== lastReadChangelogVersion.value
})

function goChangelog() {
  if (latestChangelogVersion.value) {
    lastReadChangelogVersion.value = latestChangelogVersion.value
    localStorage.setItem('sf-changelog-read', latestChangelogVersion.value)
  }
  router.push('/changelog')
}

function openCommandPalette() {
  void openCommandPaletteAsync()
}

async function openCommandPaletteAsync() {
  if (!commandPaletteComponent.value) {
    const mod = await import('@/components/CommandPalette.vue')
    commandPaletteComponent.value = mod.default
  }
  commandPaletteMounted.value = true
  await nextTick()
  commandPaletteRef.value?.open?.()
}

function formatNavBadge(value: unknown) {
  const count = Number(value || 0)
  if (!Number.isFinite(count) || count <= 0) return ''
  return count > 99 ? '99+' : String(Math.floor(count))
}

async function loadLatestChangelog() {
  try {
    const res = await changelogApi.get(1)
    const versions = res?.versions || []
    if (versions.length) {
      latestChangelogVersion.value = versions[0].version || ''
    }
  } catch {
    latestChangelogVersion.value = ''
  }
}

const PRIMARY_NAV_ALIASES: Record<string, string> = {
  skills: 'workflow',
  knowledge: 'workflow',
  agent: 'workflow',
  learning: 'workflow',
  brain: 'workflow',
  portal: 'projects',
  playbooks: 'projects',
}

const currentPrimary = computed(() => {
  if (String(route.path || '').startsWith('/admin')) return 'admin'
  const rawPrimary = String(route.meta?.primary || '')
  return PRIMARY_NAV_ALIASES[rawPrimary] || rawPrimary || 'workflow'
})
// 设计稿角色 pill：员工保留精简入口；其他角色按完整一级导航展示
const roleScope = computed(() => {
  const r = String(userStore.role || '').toLowerCase()
  return ['employee', 'biz_user', 'observer'].includes(r) ? 'employee' : 'admin'
})
// Studio 页面保留一级导航常驻；二级导航仍隐藏，避免编辑区再多占一行。
const hideSecondaryNav = computed(() => ['SkillStudio', 'SkillStudioCreate'].includes(String(route.name || '')))
// G2（2026-04-24 UX 审计）：admin/* 路由下 AdminShell 自带左侧菜单，
// 顶部二级 subnav 对 admin 没有意义且会让用户混淆当前层级。
// 这里判断 admin 区，让 secondaryNavItems 返回空数组，使顶部 subnav 和移动端
// 「二级导航」区都自然隐藏，admin 统一走左侧菜单。
const isAdminRoute = computed(() => String(route.path || '').startsWith('/admin'))
const currentTodoStatus = computed(() => route.query.status || 'pending')
const actionableTodoBadge = computed(() => (
  (todoStats.value.pending || 0) + (todoStats.value.dispatch_pending || 0)
))
const inboxBadge = computed(() => actionableTodoBadge.value + notificationUnreadCount.value)
const secondaryNavSectionLabel = computed(() => {
  if (currentPrimary.value === 'workflow') return PRIMARY_NAV_LABELS.workflow
  if (currentPrimary.value === 'training') return PRIMARY_NAV_LABELS.training
  return PRIMARY_NAV_LABELS.sf
})

function adminNavPath() {
  return '/admin/users'
}

// 一级导航定义：当增减 key 或做 key 重命名时，同步更新 layouts/primaryNav.ts 中的
// PRIMARY_NAV_KEYS 白名单 / PRIMARY_NAV_MIGRATIONS 迁移表。
// v2.7.2：大厅重新升一级导航 —— 按"能力（业务场景）"分类的内部能力资产中心。
const primaryNavItems = computed<any[]>(() => {
  const hall = { key: 'hall', label: PRIMARY_NAV_LABELS.hall, path: '/hall', icon: 'spark' }
  const workflow = { key: 'workflow', label: PRIMARY_NAV_LABELS.workflow, path: WORKFLOW_DEFAULT_PATH, icon: 'flow' }
  const training = { key: 'training', label: PRIMARY_NAV_LABELS.training, path: '/training', icon: 'beaker' }
  const projects = { key: 'projects', label: PRIMARY_NAV_LABELS.projects, path: '/projects', icon: 'folder' }
  const inbox = { key: 'inbox', label: PRIMARY_NAV_LABELS.inbox, path: '/inbox', icon: 'inbox', badge: inboxBadge.value || 0 }
  const tasktree = { key: 'tasktree', label: PRIMARY_NAV_LABELS.tasktree, path: '/task-tree', icon: 'tree' }
  const sf = { key: 'sf', label: PRIMARY_NAV_LABELS.sf, path: '/sf', icon: 'bolt' }
  if (roleScope.value === 'employee') {
    // Read-only roles can still see company projects and projects inherited
    // from their department.  Keep the entry visible and let the unified
    // backend project permission filter decide which cards are returned.
    return [hall, workflow, training, projects, inbox]
  }
  const items = [hall, workflow, training, projects, tasktree, sf, inbox]
  if (userStore.isAdmin) {
    items.push({ key: 'admin', label: PRIMARY_NAV_LABELS.admin, path: adminNavPath(), icon: 'shield' })
  }
  return items
})

const secondaryNavItems = computed<any[]>(() => {
  // AI Workflow 只承载与 AI 能力生产和学习闭环直接相关的入口；
  // 项目、任务树、插件、收件、管理后台保持一级导航，避免顶部层级混乱。
  if (currentPrimary.value === 'workflow') {
    return [
      { key: 'learning', label: WORKFLOW_SUBNAV_LABELS.learning, to: '/learning-flow', iconName: 'flow', matchPrefixes: ['/learning-flow'] },
      {
        key: 'skills',
        label: WORKFLOW_SUBNAV_LABELS.skills,
        to: '/skills',
        iconName: 'cube',
        matchPrefixes: ['/skills', '/reviews', '/review', '/executions', '/execution', '/dashboard', '/datasources', '/datasource'],
      },
      { key: 'knowledge', label: WORKFLOW_SUBNAV_LABELS.knowledge, to: '/knowledge', iconName: 'book', matchPrefixes: ['/knowledge'] },
      { key: 'agent', label: WORKFLOW_SUBNAV_LABELS.agent, to: '/agent', iconName: 'bot', matchPrefixes: ['/agent', '/brain', '/aiclaw'] },
    ]
  }
  if (currentPrimary.value === 'training') {
    return [
      { key: 'training-flow', label: '流水线', to: '/training', iconName: 'flow', exactPaths: ['/training'], matchPrefixes: ['/training/jobs'] },
      { key: 'training-datasets', label: '数据资产', to: '/training/datasets', iconName: 'database', matchPrefixes: ['/training/datasets'] },
      { key: 'training-models', label: '模型部署', to: '/training/models', iconName: 'cube', matchPrefixes: ['/training/models', '/training/deployments'] },
    ]
  }
  // v2.7.2 大厅独立一级导航，无子导航
  if (currentPrimary.value === 'hall') {
    return []
  }
  if (currentPrimary.value === 'inbox') {
    return []
  }
  if (currentPrimary.value === 'projects') {
    return []
  }
  if (currentPrimary.value === 'tasktree') {
    return []
  }
  if (currentPrimary.value === 'sf') {
    return [
      { key: 'sf', label: 'sf', to: '/sf', iconName: 'bolt', exactPaths: ['/sf'] },
      { key: 'sdk', label: 'sdk', to: '/sf/sdk', iconName: 'check', matchPrefixes: ['/sf/sdk'] },
    ]
  }
  // G2（2026-04-24 UX 审计）：admin/* 路由下隐藏顶部二级 subnav，
  // 因为 AdminShell 自带左侧菜单，三层导航并存会让用户不知道当前在哪一层。
  if (isAdminRoute.value) {
    return []
  }
  return []
})

watch(() => userStore.theme, (value) => {
  currentTheme.value = value
})

function goHome() {
  router.push('/hall')
}

const userAvatar = computed(() => userAvatarUrl(userStore.userInfo))

function isSubnavActive(item: any) {
  if (currentPrimary.value === 'aiclaw' && item.instanceId) {
    return route.params.id === item.instanceId
  }
  if (currentPrimary.value === 'inbox') {
    if (route.path === '/inbox/dispatch') {
      return item.key === 'dispatch'
    }
    return currentTodoStatus.value === item.key
  }
  const path = String(route.path || '')
  if (Array.isArray(item.exactPaths) && item.exactPaths.includes(path)) {
    return true
  }
  if (Array.isArray(item.matchPrefixes) && item.matchPrefixes.some((prefix: string) => path.startsWith(prefix))) {
    return true
  }
  return route.meta?.subnav === item.key
}

function setTheme(val: string) {
  currentTheme.value = val
  userStore.setTheme(val)
}

const roleLabel = {
  // v2 新角色
  system_admin: '系统管理员', dept_admin: '部门管理员', aibp: 'AIBP', observer: '观察员',
  // legacy 兼容（迁移期保留）
  admin: '管理员', ai_engineer: 'AI工程师', biz_owner: '业务负责人',
  director: '总监', viewer: '查看者', operator: '运营',
}

function goTo(path: string) {
  router.push(path)
}

async function handleLogout() {
  userMenuOpen.value = false
  await userStore.logout()
  router.push('/login')
}

const visibleMenuGroups = computed(() =>
  filterUserMenu({
    isAdmin: userStore.isAdmin,
    isDeptAdmin: userStore.isDeptAdmin,
    isEngineer: userStore.isEngineer,
  }),
)

function handleMenuItem(item: UserMenuItem) {
  if (item.action === 'logout') {
    handleLogout()
    return
  }
  if (item.to) {
    userMenuOpen.value = false
    // 点「更新日志」时也顺便把未读点清掉
    if (item.to === '/changelog' && latestChangelogVersion.value) {
      lastReadChangelogVersion.value = latestChangelogVersion.value
      localStorage.setItem('sf-changelog-read', latestChangelogVersion.value)
    }
    router.push(item.to)
  }
}

async function loadNavData() {
  const [todoResult, notificationResult] = await Promise.allSettled([
    todoApi.stats(),
    notificationApi.list({ unread_only: true, page_size: 1 }),
  ])
  if (todoResult.status === 'fulfilled') {
    todoStats.value = todoResult.value
  } else {
    todoStats.value = { pending: 0 }
  }
  if (notificationResult.status === 'fulfilled') {
    const res = notificationResult.value || {}
    notificationUnreadCount.value = Number(res.unread ?? res.total ?? 0) || 0
  } else {
    notificationUnreadCount.value = 0
  }
}

function loadCachedNavData(): boolean {
  try {
    const raw = sessionStorage.getItem(navBadgeCacheKey.value)
    const parsed = raw ? JSON.parse(raw) : null
    if (!parsed || Date.now() - Number(parsed.at || 0) > NAV_REFRESH_TTL_MS) return false
    todoStats.value = parsed.todoStats || { pending: 0 }
    notificationUnreadCount.value = Number(parsed.notificationUnreadCount || 0)
    lastNavRefreshAt = Number(parsed.at || 0) || lastNavRefreshAt
    return true
  } catch {
    // Ignore broken cache; live refresh will repair it.
    return false
  }
}

function saveCachedNavData() {
  try {
    sessionStorage.setItem(navBadgeCacheKey.value, JSON.stringify({
      at: Date.now(),
      todoStats: todoStats.value,
      notificationUnreadCount: notificationUnreadCount.value,
    }))
  } catch {
    // Ignore storage quota or privacy mode.
  }
}

async function refreshNavData() {
  if (navRefreshPromise) return navRefreshPromise
  navRefreshPromise = (async () => {
    await loadNavData()
    lastNavRefreshAt = Date.now()
    saveCachedNavData()
  })()
  try {
    await navRefreshPromise
  } finally {
    navRefreshPromise = null
  }
}

async function refreshNavDataIfStale() {
  if (Date.now() - lastNavRefreshAt < NAV_REFRESH_TTL_MS) return
  await refreshNavData()
}

async function refreshNavDataAndMarkIfNeeded(force = false) {
  if (force) {
    await refreshNavData()
  } else {
    await refreshNavDataIfStale()
  }
  await markNotificationsReadInInbox()
}

async function markNotificationsReadInInbox() {
  if (!String(route.path || '').startsWith('/inbox') || notificationUnreadCount.value <= 0) return
  notificationUnreadCount.value = 0
  try {
    await notificationApi.markAllRead()
  } catch {
    loadNavData()
  }
}

// 路由变化时记住当前一级导航（供下次刷新恢复）。
function persistCurrentPrimary() {
  persistPrimaryNavStorage(currentPrimary.value)
}

watch(() => route.fullPath, async () => {
  mobileMenuOpen.value = false
  const isInboxRoute = String(route.path || '').startsWith('/inbox')
  const enteredInbox = isInboxRoute && !wasInInbox
  wasInInbox = isInboxRoute
  void refreshNavDataAndMarkIfNeeded(enteredInbox)
  persistCurrentPrimary()
})

onMounted(async () => {
  // 挂载时先迁移旧的 lastPrimaryNav（如 'skills' → 'workflow'），避免 currentPrimary 失效。
  migratePrimaryNavStorage()
  persistCurrentPrimary()
  wasInInbox = String(route.path || '').startsWith('/inbox')
  loadCachedNavData()
  void refreshNavDataAndMarkIfNeeded(wasInInbox)
  loadLatestChangelog()
})

onUnmounted(() => {
  navRefreshPromise = null
})
</script>

<style scoped>
.app-layout {
  height: 100vh;
  overflow: hidden;
}
.app-layout :deep(.arco-layout) {
  height: 100vh;
  display: flex;
  flex-direction: column;
}
.app-layout :deep(.arco-layout-content) {
  min-height: 0;
}

.top-bar {
  height: 52px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 16px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
  box-shadow: none;
}
.top-bar-left {
  display: flex;
  align-items: center;
  min-width: 0;
  flex: 0 0 auto;
}
.top-bar-center {
  display: flex;
  align-items: center;
  min-width: 0;
  flex: 1 1 auto;
  overflow: hidden;
}
.top-bar-brand {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.top-bar-brand.brand-clickable {
  appearance: none;
  border: 0;
  background: transparent;
  color: var(--ai-ink-1);
  font: inherit;
  cursor: pointer;
  user-select: none;
  padding: 4px 8px;
  margin-left: -8px;
  border-radius: 4px;
  transition: background 0.15s;
}
.top-bar-brand.brand-clickable:hover {
  background: var(--ai-surface-2);
}
.top-bar-brand:focus-visible {
  outline: 2px solid var(--sf-focus-ring);
  outline-offset: 3px;
}
.brand-role-pill {
  display: inline-flex;
  align-items: center;
  height: 17px;
  padding: 0 5px;
  margin-left: 6px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  color: var(--ai-ink-3);
  font-size: 10px;
  font-weight: 500;
}

.primary-nav {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: 2px;
  padding: 0;
  background: transparent;
  border: 0;
  border-radius: 0;
  box-shadow: none;
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}
.primary-nav::-webkit-scrollbar {
  display: none;
}
.primary-nav-item {
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
  gap: 6px;
  height: 30px;
  padding: 0 9px;
  border-radius: 6px;
  color: var(--ai-ink-3);
  text-decoration: none;
  transition: background 0.15s ease, color 0.15s ease;
  font-family: var(--ai-font-sans);
  font-weight: 450;
  font-size: 13px;
  letter-spacing: -0.005em;
  white-space: nowrap;
}
.primary-nav-item:hover {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.primary-nav-item.active {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
  font-weight: 500;
  box-shadow: none;
}
.primary-nav-item.has-alert-badge {
  padding-right: 6px;
}
.primary-nav-icon {
  width: 13px;
  height: 13px;
  flex: 0 0 13px;
}
.primary-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  padding: 0 5px;
  margin-left: 2px;
  border-radius: 4px;
  background: var(--ai-surface-3);
  color: var(--ai-ink-2);
  font-size: 10px;
  line-height: 1;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.primary-badge-alert {
  min-width: 18px;
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--ai-bad);
  color: #fff;
  font-size: 10.5px;
  font-weight: 700;
  box-shadow:
    0 0 0 2px var(--ai-surface),
    0 6px 14px rgba(182, 50, 27, 0.24);
}
.primary-nav-item.active .primary-badge-alert {
  box-shadow:
    0 0 0 2px var(--ai-surface-2),
    0 6px 14px rgba(182, 50, 27, 0.24);
}

.sub-nav-bar {
  background: var(--ai-surface);
  border-bottom: 1px solid var(--ai-border);
  padding: 0 28px;
  display: flex;
  align-items: stretch;
  height: 36px;
}
.sub-nav-inner {
  display: flex;
  align-items: stretch;
  gap: 4px;
  flex: 1;
  overflow-x: auto;
}
.sub-nav-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 12px;
  font-size: 13px;
  color: var(--ai-ink-3);
  text-decoration: none;
  white-space: nowrap;
  position: relative;
  border-bottom: 1.5px solid transparent;
  transition: color 0.15s ease, border-color 0.15s ease;
  margin-bottom: -1px;
  font-family: var(--ai-font-sans);
}
.sub-nav-item:hover {
  color: var(--ai-ink-2);
}
.sub-nav-item.active {
  color: var(--ai-ink-1);
  font-weight: 500;
  border-bottom-color: var(--ai-ink-1);
}
.sub-nav-icon {
  width: 13px;
  height: 13px;
  opacity: 1;
  color: currentColor;
}
.online-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.online-dot.online {
  background: var(--ai-ok);
}
.online-dot.offline {
  background: var(--ai-ink-5);
}

.top-bar-right {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
}
.topbar-spacer {
  flex: 0 0 4px;
  min-width: 4px;
}
.topbar-search {
  height: 30px;
  width: 220px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface);
  color: var(--ai-ink-4);
  font-size: 12.5px;
  font-family: var(--ai-font-sans);
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s, color 0.15s;
}
.topbar-search:hover {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  border-color: var(--ai-border-2);
}
.topbar-search svg {
  width: 13px;
  height: 13px;
  flex: 0 0 13px;
}
.topbar-search-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.topbar-search-kbd {
  margin-left: auto;
  height: 17px;
  display: inline-flex;
  align-items: center;
  padding: 0 4px;
  border: 1px solid var(--ai-border);
  border-radius: 3px;
  color: var(--ai-ink-4);
  font-size: 10.5px;
  font-family: var(--ai-font-mono);
  line-height: 1;
  background: var(--ai-surface);
}
.topbar-icon-btn {
  position: relative;
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  background: transparent;
  color: var(--ai-ink-3);
  cursor: pointer;
  border-radius: 6px;
  transition: background 0.15s, color 0.15s;
  font-size: 14px;
}
.topbar-icon-btn:hover {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}
.topbar-icon-btn.has-unread {
  color: var(--ai-ink-3);
}
.topbar-divider {
  width: 1px;
  height: 20px;
  margin: 0 4px;
  background: var(--ai-border);
}

.topbar-dot {
  position: absolute;
  top: 7px;
  right: 7px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ai-bad);
}
.user-btn {
  height: 34px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px 4px 4px;
  border: 0;
  background: transparent;
  border-radius: 6px;
  color: var(--ai-ink-2);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  font-family: var(--ai-font-sans);
}
.user-btn:hover {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}
.user-avatar {
  flex: 0 0 auto;
}
.user-meta {
  display: flex;
  flex-direction: column;
  gap: 0;
  line-height: 1.1;
  min-width: 0;
  text-align: left;
}
.user-name {
  max-width: 78px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.user-role {
  max-width: 78px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10.5px;
  color: var(--ai-ink-4);
  font-weight: 400;
}
.user-arrow {
  margin-left: 4px;
  font-size: 12px;
  color: var(--ai-ink-3);
  transition: color 0.15s;
}
.user-btn:hover .user-arrow {
  color: var(--ai-ink-1);
}
.user-panel {
  min-width: 240px;
  padding: 0;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  box-shadow: var(--ai-shadow-2);
  overflow: hidden;
  font-family: var(--ai-font-sans);
}
.panel-user-info {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px 12px;
  background: var(--ai-surface);
}
.panel-user-meta { flex: 1; min-width: 0; }
.panel-user-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--ai-ink-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.panel-user-handle {
  font-size: 12px;
  color: var(--ai-ink-3);
  margin-top: 2px;
  font-family: var(--ai-font-mono, monospace);
}
.panel-user-role {
  font-size: 12px;
  color: var(--ai-ink-3);
  margin-top: 1px;
}
.panel-section {
  padding: 7px 8px;
}
.panel-section-danger {
  /* 让"退出登录"分组视觉和其他分组明显分开 */
  padding-top: 7px;
}
.panel-divider {
  height: 1px;
  background: var(--ai-border);
  margin: 0 8px;
}
/* 头像信息块和第一条 divider 之间去掉多余空隙 */
.panel-user-info + .panel-divider {
  margin: 0;
}
.panel-section-bottom {
  border-bottom: none;
}
.panel-section-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--ai-ink-3);
  text-transform: uppercase;
  margin-bottom: 8px;
}
.theme-switcher {
  display: flex;
  gap: 4px;
  background: var(--ai-surface-2);
  border-radius: 4px;
  padding: 3px;
}
.theme-option {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 7px 10px;
  border-radius: 4px;
  font-size: 13px;
  color: var(--ai-ink-3);
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}
.theme-option:hover {
  color: var(--ai-ink-1);
  background: var(--ai-surface-3);
}
.theme-option.active {
  background: var(--ai-surface);
  color: var(--ai-accent-ink);
  font-weight: 600;
}
.panel-menu {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.panel-menu-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  padding: 0 10px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  font-size: 13px;
  color: var(--ai-ink-2);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
  font-family: var(--ai-font-sans);
  text-align: left;
}
.panel-menu-item:hover {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}
.panel-menu-item svg {
  width: 13px;
  height: 13px;
  flex: 0 0 13px;
  color: var(--ai-ink-4);
}
.panel-menu-item:hover svg {
  color: var(--ai-ink-1);
}
.panel-menu-danger:hover {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}
.panel-menu-danger:hover svg {
  color: var(--ai-bad);
}

.main-content {
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: var(--ai-bg);
}

.mobile-menu-btn {
  display: none !important;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: none;
  background: none;
  color: var(--ai-ink-2);
  font-size: 20px;
  cursor: pointer;
  border-radius: 4px;
}
.mobile-menu-btn:hover {
  background: var(--ai-surface-2);
}
.mobile-nav-overlay {
  position: fixed;
  inset: 0;
  z-index: 199;
  background: rgba(0, 0, 0, 0.3);
}
.mobile-nav-menu {
  position: fixed;
  top: 52px;
  left: 0;
  right: 0;
  z-index: 200;
  background: var(--ai-surface);
  border-bottom: 1px solid var(--ai-border);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  display: flex;
  flex-direction: column;
  padding: 8px;
}
.mobile-nav-section-label {
  font-size: 11px;
  color: var(--ai-ink-3);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 8px 16px 4px;
}
.mobile-nav-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 12px 16px;
  font-size: 14px;
  color: var(--ai-ink-2);
  text-decoration: none;
  border-radius: 4px;
  transition: all 0.15s;
}
.mobile-nav-main {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}
.mobile-nav-item:hover,
.mobile-nav-item.active {
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
}
.mobile-nav-icon {
  font-size: 16px;
  color: currentColor;
}

.desktop-only {
  display: flex;
}

@media (max-width: 960px) {
  .desktop-only {
    display: none;
  }
  .top-bar-right {
    margin-left: auto;
  }
}

@media (max-width: 1360px) {
  .topbar-search {
    display: none;
  }
}

@media (max-width: 768px) {
  .top-bar {
    padding: 0 12px;
    gap: 10px;
  }
  .sub-nav-bar {
    padding: 0 12px;
  }
  .mobile-menu-btn {
    display: inline-flex !important;
  }
}

@media (max-width: 520px) {
  .topbar-divider,
  .user-meta {
    display: none;
  }
  .user-btn {
    padding-right: 4px;
  }
}
</style>
