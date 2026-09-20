<template>
  <div class="admin-users-tabs ai-tabs">
    <router-link
      v-for="tab in tabs"
      :key="tab.key"
      :to="tab.to"
      :class="['admin-users-tab', 'ai-tab', { active: activeKey === tab.key }]"
    >
      {{ tab.label }}
      <span class="admin-users-tab-count ai-pill" :class="{ ok: tab.key === 'active' }">{{ tab.count }}</span>
    </router-link>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const props = defineProps<{
  activeCount?: number
  pendingCount?: number
  disabledCount?: number
}>()

const tabs = computed(() => [
  { key: 'active', label: '激活', to: '/admin/users', count: props.activeCount ?? 0 },
  { key: 'pending', label: '待激活', to: '/admin/users/pending', count: props.pendingCount ?? 0 },
  { key: 'disabled', label: '停用', to: '/admin/users?status=disabled', count: props.disabledCount ?? 0 },
] as const)

const activeKey = computed(() => {
  if (route.path === '/admin/users/pending') return 'pending'
  if (route.query?.status === 'disabled') return 'disabled'
  return 'active'
})
</script>

<style scoped>
.admin-users-tabs {
  display: flex;
  gap: 4px;
  width: 100%;
  padding: 0 28px;
  background: var(--ai-surface);
  border-bottom: 1px solid var(--ai-border);
  box-sizing: border-box;
}

.admin-users-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 12px;
  border-bottom: 1.5px solid transparent;
  margin-bottom: -1px;
  font-size: 13px;
  font-weight: 450;
  color: var(--ai-ink-3);
  text-decoration: none;
  transition: color 0.12s, border-color 0.12s;
}

.admin-users-tab:hover {
  color: var(--ai-ink-1);
}

.admin-users-tab.active {
  color: var(--ai-ink-1);
  border-bottom-color: var(--ai-ink-1);
  font-weight: 500;
}

.admin-users-tab-count {
  display: inline-flex;
  align-items: center;
  height: 16px;
  padding: 0 5px;
  border-radius: 4px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 10px;
  font-weight: 500;
  line-height: 16px;
}

.admin-users-tab-count.ok {
  color: var(--ai-ok);
  background: var(--ai-ok-soft);
  border-color: transparent;
}

.admin-users-tab.active .admin-users-tab-count {
  color: var(--ai-ink-1);
  background: var(--ai-surface-2);
  border-color: var(--ai-border);
}

.admin-users-tab.active .admin-users-tab-count.ok {
  color: var(--ai-ok);
  background: var(--ai-ok-soft);
  border-color: transparent;
}
</style>
