<template>
  <div class="admin-shell page-container page-wide">
    <aside class="admin-nav" :class="{ 'admin-nav--open': navOpen }">
      <div class="admin-nav-head">
        <h2 class="admin-nav-title">管理后台</h2>
        <button class="admin-nav-close" @click="navOpen = false" aria-label="关闭导航">
          <icon-close />
        </button>
      </div>
      <a-input-search
        v-model="searchKeyword"
        placeholder="搜索菜单…"
        allow-clear
        class="admin-nav-search"
      />
      <nav class="admin-nav-list">
        <template v-for="group in filteredGroups" :key="group.key">
          <div class="admin-nav-group-label">{{ group.label }}</div>
          <router-link
            v-for="item in group.items"
            :key="item.key"
            :to="item.to"
            class="admin-nav-item"
            :class="{ active: activeKey === item.key }"
            @click="navOpen = false"
          >
            <SfShellIcon :name="item.iconName" class="admin-nav-icon" />
            <span>{{ item.label }}</span>
          </router-link>
        </template>
        <div v-if="!filteredGroups.length" class="admin-nav-empty">没有匹配的菜单</div>
      </nav>
    </aside>

    <button class="admin-nav-toggle" @click="navOpen = !navOpen" aria-label="打开导航">
      <icon-menu />
      <span>{{ activeLabel || '管理' }}</span>
    </button>

    <main class="admin-content">
      <router-view v-slot="{ Component }">
        <component :is="Component" />
      </router-view>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { IconMenu, IconClose } from '@arco-design/web-vue/es/icon'
import { ADMIN_MENU_FLAT, matchAdminMenuKey, filterAdminMenu } from './adminMenu'
import { useUserStore } from '@/stores/user'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

const route = useRoute()
const userStore = useUserStore()

const searchKeyword = ref('')
const navOpen = ref(false)

const activeKey = computed(() => matchAdminMenuKey(route.path))

const activeLabel = computed(() => {
  const item = ADMIN_MENU_FLAT.find((i) => i.key === activeKey.value)
  return item?.label || ''
})

// 先按 role 过滤，再按搜索关键字过滤；最后去掉 items 为空的 group
const filteredGroups = computed(() => {
  const roleFiltered = filterAdminMenu(userStore.role)
  const kw = searchKeyword.value.trim().toLowerCase()
  if (!kw) return roleFiltered
  return roleFiltered
    .map((g) => ({
      ...g,
      items: g.items.filter((i) => i.label.toLowerCase().includes(kw) || i.to.toLowerCase().includes(kw)),
    }))
    .filter((g) => g.items.length > 0)
})
</script>

<style scoped>
.admin-shell {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 0;
  align-items: stretch;
  padding: 0 !important;
  max-width: none !important;
  min-height: calc(100vh - 92px);
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
  background: var(--ai-bg);
}

/* 设计稿 admin.jsx 的 AdminSidebar：flush 左侧 220px，无圆角阴影 */
.admin-nav {
  position: sticky;
  top: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px 12px;
  border-radius: 0;
  background: var(--ai-surface);
  border: 0;
  border-right: 1px solid var(--ai-border);
  max-height: 100vh;
  overflow: hidden;
}

.admin-nav-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 4px 6px;
}

.admin-nav-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
  margin: 0;
  letter-spacing: -0.005em;
}

.admin-nav-close {
  display: none;
  background: transparent;
  border: 0;
  cursor: pointer;
  color: var(--ai-ink-4);
  padding: 4px;
}
.admin-nav-close:hover {
  color: var(--ai-ink-1);
}

.admin-nav-search {
  margin-bottom: 4px;
}
.admin-nav-search :deep(.arco-input-wrapper) {
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  height: 30px;
  font-size: 12.5px;
}
.admin-nav-search :deep(.arco-input-wrapper:focus-within) {
  border-color: var(--ai-ink-3);
  box-shadow: none;
}

.admin-nav-list {
  display: flex;
  flex-direction: column;
  overflow: auto;
  gap: 1px;
  padding-right: 2px;
}

/* group label：11px / 500 / uppercase / ink-4，对齐设计稿 .ai-side-label */
.admin-nav-group-label {
  padding: 14px 8px 4px;
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-4);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

/* item：28px 高、5px 圆角、ink-2 默认、ink-1 active，对齐 .ai-side-item */
.admin-nav-item {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 28px;
  padding: 0 8px;
  border-radius: 5px;
  font-size: 13px;
  font-weight: 450;
  color: var(--ai-ink-2);
  text-decoration: none;
  transition: background 0.15s ease, color 0.15s ease;
  letter-spacing: -0.005em;
}

.admin-nav-item:hover {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}

.admin-nav-item.active {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
  font-weight: 500;
}

.admin-nav-icon {
  width: 13px;
  height: 13px;
  flex: 0 0 13px;
  color: var(--ai-ink-4);
}
.admin-nav-item.active .admin-nav-icon {
  color: var(--ai-ink-1);
}

.admin-nav-empty {
  color: var(--ai-ink-4);
  font-size: 12px;
  padding: 12px 8px;
}

.admin-nav-toggle {
  display: none;
  align-items: center;
  gap: 8px;
  margin: 12px 16px;
  padding: 0 12px;
  height: 30px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ai-ink-1);
  cursor: pointer;
  font-family: var(--ai-font-sans);
}

.admin-content {
  min-width: 0;
  padding: 0;
  background: var(--ai-bg);
}

/* 平板 / 手机：左栏变抽屉 */
@media (max-width: 1024px) {
  .admin-shell {
    grid-template-columns: 1fr;
  }
  .admin-nav {
    position: fixed;
    top: 0;
    left: 0;
    height: 100vh;
    width: min(280px, 80vw);
    max-height: none;
    z-index: 200;
    transform: translateX(-110%);
    transition: transform 0.2s ease;
    box-shadow: var(--ai-shadow-2);
    border-right: 1px solid var(--ai-border);
  }
  .admin-nav--open {
    transform: translateX(0);
  }
  .admin-nav-close {
    display: inline-flex;
  }
  .admin-nav-toggle {
    display: inline-flex;
  }
}
</style>
