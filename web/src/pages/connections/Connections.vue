<template>
  <div class="page-container page-wide connections-page">
    <div class="page-header">
      <div class="page-heading">
        <div class="page-kicker">管理后台 · 数据与服务</div>
        <h2 class="page-title">连接管理</h2>
        <p class="page-subtitle">数据源以外的对外连接：平台 Cookie 登态、浏览器远程采集</p>
      </div>
    </div>

    <div class="conn-tabs">
      <router-link to="/admin/connections" class="conn-tab" :class="{ active: activeTab === 'platforms' }">
        <icon-link /> 平台连接
      </router-link>
      <router-link to="/admin/connections?tab=browser" class="conn-tab" :class="{ active: activeTab === 'browser' }">
        <icon-desktop /> 浏览器采集
      </router-link>
      <router-link to="/admin/connections/health" class="conn-tab">
        <icon-bar-chart /> 采集健康
      </router-link>
      <router-link to="/admin/connector-keys" class="conn-tab">
        <icon-safe /> Connector Keys
      </router-link>
    </div>

    <div class="conn-body">
      <PlatformConnections v-if="activeTab === 'platforms'" />
      <BrowserConnect v-else-if="activeTab === 'browser'" embedded />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { IconLink, IconDesktop, IconBarChart, IconSafe } from '@arco-design/web-vue/es/icon'
import PlatformConnections from './PlatformConnections.vue'
import BrowserConnect from '@/pages/datasource/BrowserConnect.vue'

const route = useRoute()

const activeTab = computed<'platforms' | 'browser'>(() =>
  route.query?.tab === 'browser' ? 'browser' : 'platforms',
)
</script>

<style scoped>
/* 设计稿 page chrome 覆盖 */
.connections-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.connections-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
}
.connections-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}

/* ai-tabs 风格：36px 高，1.5px ink-1 active 下划线 */
.conn-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 16px;
  padding: 0 0 0;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--ai-border);
  border-radius: 0;
  width: 100%;
}

.conn-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 12px;
  border-radius: 0;
  font-size: 13px;
  font-weight: 450;
  color: var(--ai-ink-3);
  text-decoration: none;
  transition: color 0.15s ease, border-color 0.15s ease;
  border-bottom: 1.5px solid transparent;
  margin-bottom: -1px;
  font-family: var(--ai-font-sans);
  letter-spacing: -0.005em;
}

.conn-tab:hover {
  background: transparent;
  color: var(--ai-ink-2);
}

.conn-tab.active {
  background: transparent;
  color: var(--ai-ink-1);
  font-weight: 500;
  border-bottom-color: var(--ai-ink-1);
}

.conn-body {
  min-height: 320px;
}
</style>
