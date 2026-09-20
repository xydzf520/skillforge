<template>
  <div class="notification-wrapper" ref="wrapperRef">
    <button
      class="notification-trigger"
      type="button"
      :aria-label="unreadCount > 0 ? `通知，${unreadCount} 条未读` : '通知'"
      :aria-expanded="visible"
      aria-haspopup="true"
      @click="toggle"
    >
      <icon-notification class="trigger-icon" />
      <span v-if="unreadCount > 0" class="unread-badge">{{ unreadCount > 99 ? '99+' : unreadCount }}</span>
    </button>

    <transition name="panel-fade">
      <div v-if="visible" class="notification-panel">
        <div class="panel-header">
          <span class="header-title">通知</span>
          <a-button v-if="unreadCount > 0" type="text" size="mini" @click="handleMarkAllRead">全部已读</a-button>
        </div>
        <div class="panel-body">
          <div v-if="loading" style="text-align: center; padding: 30px"><a-spin /></div>
          <template v-else-if="items.length">
            <!-- W3-E: 按类型折叠（审核 / 执行 / 告警 / 其他） -->
            <div v-for="group in groupedItems" :key="group.key" class="notification-group">
              <div class="group-header" @click="toggleGroup(group.key)">
                <icon-right :class="{ 'rotate-90': !collapsedGroups.has(group.key) }" class="group-toggle" />
                <span class="group-title">{{ group.label }}</span>
                <span class="group-count">{{ group.items.length }}</span>
                <span class="group-spacer" />
                <a-button
                  v-if="group.unread > 0"
                  type="text"
                  size="mini"
                  @click.stop="markGroupRead(group.key)"
                >
                  {{ group.unread }} 条未读 · 标已读
                </a-button>
              </div>
              <div v-if="!collapsedGroups.has(group.key)" class="group-body">
                <div
                  v-for="item in group.items"
                  :key="item.id"
                  class="notification-item"
                  :class="{ unread: !item.read }"
                  @click="handleClick(item)"
                >
                  <div class="item-icon" :class="`type-${item.type}`">
                    <icon-check-circle v-if="item.type === 'review_result'" />
                    <icon-upload v-else-if="item.type === 'review_request'" />
                    <icon-play-arrow v-else-if="item.type?.startsWith('execution')" />
                    <icon-exclamation-circle v-else-if="item.type === 'alert' || item.type === 'sla_warning'" />
                    <icon-notification v-else />
                  </div>
                  <div class="item-content">
                    <div class="item-title">{{ item.title }}</div>
                    <div v-if="item.body" class="item-body">{{ item.body }}</div>
                    <div class="item-time">{{ formatRelative(item.created_at) }}</div>
                  </div>
                </div>
              </div>
            </div>
          </template>
          <div v-else class="panel-empty">暂无通知</div>
        </div>
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { notificationApi as rawNotificationApi } from '@/api'
import { silentWarn } from '@/utils/errorBoundary'
import { relativeTime } from '@/utils/format'
import {
  IconNotification, IconCheckCircle, IconUpload, IconPlayArrow, IconExclamationCircle, IconRight,
} from '@arco-design/web-vue/es/icon'
import { computed } from 'vue'

const notificationApi: any = rawNotificationApi
const router: any = useRouter()
const visible = ref(false)
const loading = ref(false)
const items = ref<any[]>([])
const unreadCount = ref(0)
const wrapperRef = ref<HTMLElement | null>(null)

function formatRelative(iso: string | undefined) {
  if (!iso) return ''
  return relativeTime(iso)
}

async function loadNotifications() {
  loading.value = true
  try {
    const res = await notificationApi.list({ page_size: 15 })
    items.value = res.items || []
    unreadCount.value = res.unread || 0
  } catch { /* ignore */ }
  finally { loading.value = false }
}

function toggle() {
  visible.value = !visible.value
  if (visible.value) loadNotifications()
}

async function handleClick(item: any) {
  if (!item.read) {
    await notificationApi.markRead(item.id).catch((e: unknown) => silentWarn(e, 'notification.markRead'))
    item.read = true
    unreadCount.value = Math.max(0, unreadCount.value - 1)
  }
  if (item.link) {
    visible.value = false
    router.push(item.link)
  }
}

// W3-E: 按类型折叠（审核 / 执行 / 告警 / 其他）
function groupKeyOf(type?: string): 'review' | 'execution' | 'alert' | 'other' {
  if (!type) return 'other'
  if (type.startsWith('review')) return 'review'
  if (type.startsWith('execution')) return 'execution'
  if (type === 'alert' || type === 'sla_warning') return 'alert'
  return 'other'
}
const GROUP_LABELS = { review: '审核', execution: '执行', alert: '告警', other: '其他' } as const
const collapsedGroups = ref<Set<string>>(new Set())
function toggleGroup(key: string) {
  if (collapsedGroups.value.has(key)) collapsedGroups.value.delete(key)
  else collapsedGroups.value.add(key)
  collapsedGroups.value = new Set(collapsedGroups.value)
}
const groupedItems = computed(() => {
  const map: Record<string, any[]> = { review: [], execution: [], alert: [], other: [] }
  for (const it of items.value) {
    map[groupKeyOf(it.type)].push(it)
  }
  return (['review', 'execution', 'alert', 'other'] as const)
    .filter((k) => map[k].length > 0)
    .map((k) => ({
      key: k,
      label: GROUP_LABELS[k],
      items: map[k],
      unread: map[k].filter((i) => !i.read).length,
    }))
})
async function markGroupRead(groupKey: string) {
  const group = groupedItems.value.find((g) => g.key === groupKey)
  if (!group) return
  for (const it of group.items) {
    if (!it.read) {
      await notificationApi.markRead(it.id).catch((e: unknown) => silentWarn(e, `notification.markRead.${groupKey}`))
      it.read = true
    }
  }
  unreadCount.value = items.value.filter((i) => !i.read).length
}

async function handleMarkAllRead() {
  await notificationApi.markAllRead().catch((e: unknown) => silentWarn(e, 'notification.markAllRead'))
  items.value.forEach(i => { i.read = true })
  unreadCount.value = 0
}

// 点击外部关闭
function onClickOutside(e: MouseEvent) {
  if (wrapperRef.value && !wrapperRef.value.contains(e.target as Node | null)) {
    visible.value = false
  }
}
onMounted(() => {
  document.addEventListener('click', onClickOutside)
  loadNotifications()
})
onUnmounted(() => document.removeEventListener('click', onClickOutside))
</script>

<style scoped>
.notification-wrapper { position: relative; }
.notification-trigger {
  background: none;
  border: none;
  cursor: pointer;
  position: relative;
  padding: 6px;
  color: var(--ai-ink-2);
  display: flex;
  align-items: center;
}
.notification-trigger:hover { color: var(--ai-ink-1); }
.trigger-icon { font-size: 18px; }
.unread-badge {
  position: absolute;
  top: 2px;
  right: 0;
  background: var(--ai-bad);
  color: var(--ai-surface);
  font-size: 10px;
  line-height: 1;
  padding: 2px 4px;
  border-radius: 8px;
  min-width: 14px;
  text-align: center;
}
.notification-panel {
  position: absolute;
  top: 40px;
  right: 0;
  width: 360px;
  max-height: 480px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  box-shadow: var(--ai-shadow-2);
  z-index: 1000;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid var(--ai-border);
}
.header-title { font-weight: 600; font-size: 14px; }
.panel-body { overflow-y: auto; max-height: 400px; }
.notification-item {
  display: flex;
  gap: 10px;
  padding: 10px 16px;
  cursor: pointer;
  border-bottom: 1px solid var(--ai-border);
}
.notification-item:hover { background: var(--ai-surface-2); }
.notification-item.unread { background: var(--ai-accent-soft); }
.item-icon { font-size: 16px; padding-top: 2px; flex-shrink: 0; }
.type-review_result { color: var(--ai-ok); }
.type-review_request { color: var(--ai-accent-ink); }
.type-execution_done { color: var(--ai-ok); }
.type-execution_error { color: var(--ai-bad); }
.type-alert, .type-sla_warning { color: var(--ai-warn); }
.item-content { flex: 1; min-width: 0; }
.item-title { font-size: 13px; font-weight: 500; }
.item-body { font-size: 12px; color: var(--ai-ink-3); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.item-time { font-size: 11px; color: var(--ai-ink-4); margin-top: 4px; }
.panel-empty { text-align: center; color: var(--ai-ink-3); padding: 40px; }
/* W3-E 分组折叠 */
.notification-group + .notification-group { border-top: 1px solid var(--ai-border); }
.group-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  cursor: pointer;
  background: var(--ai-surface-2);
  font-size: 12px;
}
.group-header:hover { background: var(--ai-surface-2); }
.group-toggle { transition: transform 0.15s; font-size: 11px; color: var(--ai-ink-3); }
.group-toggle.rotate-90 { transform: rotate(90deg); }
.group-title { font-weight: 600; color: var(--ai-ink-1); }
.group-count {
  background: var(--ai-surface-2);
  border-radius: 10px;
  padding: 1px 6px;
  font-size: 10px;
  color: var(--ai-ink-3);
}
.group-spacer { flex: 1; }
.panel-fade-enter-active, .panel-fade-leave-active { transition: all 0.2s ease; }
.panel-fade-enter-from, .panel-fade-leave-to { opacity: 0; transform: translateY(-8px); }
</style>
