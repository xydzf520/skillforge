<template>
  <div class="pending-page">
    <div class="pending-card">
      <icon-clock-circle class="pending-icon" />
      <div class="pending-eyebrow">PENDING</div>
      <h1>账号待审批</h1>
      <p class="pending-copy">
        你的账号已经创建完成，需由 {{ managerHint }} 激活后才能进入 SkillForge 工作区。
      </p>

      <div class="pending-info">
        <div class="info-row">
          <span>用户</span>
          <strong>{{ userStore.userInfo?.name || userStore.userInfo?.username || '-' }}</strong>
        </div>
        <div class="info-row">
          <span>部门</span>
          <strong>{{ userStore.userInfo?.department || '未关联' }}</strong>
        </div>
        <div class="info-row">
          <span>状态</span>
          <strong>{{ userStore.userInfo?.state || 'pending' }}</strong>
        </div>
        <div class="info-row">
          <span>查看时间</span>
          <strong>{{ viewedAt }}</strong>
        </div>
      </div>

      <a-space size="medium">
        <a-button @click="handleRefresh" :loading="refreshing">刷新状态</a-button>
        <a-button status="danger" @click="handleLogout">退出登录</a-button>
      </a-space>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { IconClockCircle } from '@arco-design/web-vue/es/icon'
import { useUserStore } from '@/stores/user'
import { formatTimeFull } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const refreshing = ref(false)
const viewedAt = ref('')

const managerHint = computed(() => {
  const department = userStore.userInfo?.department
  return department ? `${department} 的管理员` : '系统管理员'
})

async function handleRefresh() {
  refreshing.value = true
  try {
    await userStore.fetchUser()
    // 仅当用户仍停留在 /pending 时才跳首页，避免刷新竞态把别的页面顶掉
    if (userStore.isActive && route.path === '/pending') {
      await router.replace('/')
    }
  } finally {
    refreshing.value = false
  }
}

async function handleLogout() {
  await userStore.logout()
  await router.push('/login')
}

onMounted(() => {
  viewedAt.value = formatTimeFull(Date.now())
})
</script>

<style scoped>
.pending-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background: var(--ai-bg);
  font-family: var(--ai-font-sans);
}

.pending-card {
  width: min(440px, 100%);
  padding: 36px 32px;
  border-radius: var(--ai-radius-l);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  box-shadow: var(--ai-shadow-2);
}

.pending-icon {
  font-size: 32px;
  color: var(--ai-warn);
}

.pending-eyebrow {
  margin-top: 16px;
  font-size: 10.5px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ai-ink-4);
  font-weight: 500;
  font-family: var(--ai-font-mono);
}

h1 {
  margin: 6px 0 10px;
  font-size: 22px;
  line-height: 1.2;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
}

.pending-copy {
  margin: 0;
  color: var(--ai-ink-3);
  line-height: 1.6;
  font-size: 13px;
}

.pending-info {
  margin: 24px 0;
  border-top: 1px solid var(--ai-border);
  border-bottom: 1px solid var(--ai-border);
}

.info-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--ai-border);
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
}

.info-row:last-child {
  border-bottom: 0;
}

.info-row span {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.info-row strong {
  max-width: 60%;
  text-align: right;
  word-break: break-word;
  color: var(--ai-ink-1);
  font-weight: 500;
  font-family: var(--ai-font-mono);
}

.pending-card :deep(.arco-btn) {
  height: 30px;
  border-radius: 6px;
  font-weight: 500;
  font-size: 12.5px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  box-shadow: none;
}
.pending-card :deep(.arco-btn:hover) {
  background: var(--ai-surface-2);
}
.pending-card :deep(.arco-btn-status-danger) {
  border-color: var(--ai-bad);
  color: var(--ai-bad);
  background: transparent;
}
.pending-card :deep(.arco-btn-status-danger:hover) {
  background: var(--ai-bad-soft);
}
</style>
