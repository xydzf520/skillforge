<template>
  <div class="page-container page-narrow me-page">
    <div class="page-header">
      <div class="page-heading">
        <div class="page-kicker">账号 · 个人设置</div>
        <h2 class="page-title">个人设置</h2>
        <p class="page-subtitle">账户信息、安全与外观偏好</p>
      </div>
    </div>

    <a-card class="page-section-card me-section">
      <template #title>账户</template>
      <div class="me-account">
        <a-avatar :size="72" :image-url="userAvatar">
          {{ (userStore.userInfo?.name || userStore.userInfo?.username || '?')[0] }}
        </a-avatar>
        <div class="me-account-body">
          <div class="me-account-name">{{ userStore.userInfo?.name || userStore.userInfo?.username }}</div>
          <div class="me-account-meta">
            <span class="me-meta-chip">@{{ userStore.userInfo?.username || '-' }}</span>
            <span class="me-meta-chip">{{ roleText }}</span>
            <span class="me-meta-chip">{{ userStore.userInfo?.department || '未归属部门' }}</span>
            <span v-if="userStore.userInfo?.email" class="me-meta-chip me-meta-chip--mono">
              <icon-email /> {{ userStore.userInfo.email }}
            </span>
            <span v-if="userStore.userInfo?.phone" class="me-meta-chip me-meta-chip--mono">
              <icon-phone /> {{ userStore.userInfo.phone }}
            </span>
          </div>
        </div>
      </div>
    </a-card>

    <a-card class="page-section-card me-section">
      <template #title>修改密码</template>
      <a-form :model="pwdForm" layout="vertical" @submit-success="handleChangePassword" class="me-form">
        <a-row :gutter="16">
          <a-col :xs="24" :sm="12" :md="8">
            <a-form-item field="old_password" label="当前密码" :rules="[{ required: true, message: '请输入当前密码' }]">
              <a-input-password v-model="pwdForm.old_password" placeholder="当前密码" :disabled="pwdLoading" />
            </a-form-item>
          </a-col>
          <a-col :xs="24" :sm="12" :md="8">
            <a-form-item
              field="new_password"
              label="新密码"
              :rules="[
                { required: true, message: '请输入新密码' },
                { minLength: 8, message: '密码至少需要 8 个字符' },
              ]"
            >
              <a-input-password v-model="pwdForm.new_password" placeholder="至少 8 个字符，不能与当前密码相同" :disabled="pwdLoading" />
            </a-form-item>
          </a-col>
          <a-col :xs="24" :sm="12" :md="8">
            <a-form-item
              field="confirm_password"
              label="确认新密码"
              :rules="[
                { required: true, message: '请再次输入新密码' },
                { validator: validateConfirm },
              ]"
            >
              <a-input-password v-model="pwdForm.confirm_password" placeholder="再次输入新密码" :disabled="pwdLoading" />
            </a-form-item>
          </a-col>
        </a-row>
        <div class="me-form-actions">
          <a-button type="primary" html-type="submit" :loading="pwdLoading">修改密码</a-button>
        </div>
      </a-form>
    </a-card>

    <a-card class="page-section-card me-section">
      <template #title>外观</template>
      <div class="me-theme">
        <a-radio-group
          :model-value="userStore.theme"
          type="button"
          @change="handleThemeChange"
        >
          <a-radio value="auto">跟随系统</a-radio>
          <a-radio value="light">亮色</a-radio>
          <a-radio value="dark">暗色</a-radio>
        </a-radio-group>
        <span class="me-theme-hint">当前：{{ themeHint }}</span>
      </div>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, computed } from 'vue'
import { useUserStore } from '@/stores/user'
import { authApi as rawAuthApi } from '@/api'
import { userAvatarUrl } from '@/utils/avatar'
import { Message, Modal } from '@arco-design/web-vue'
import { IconEmail, IconPhone } from '@arco-design/web-vue/es/icon'

const authApi: any = rawAuthApi
const userStore: any = useUserStore()
const userAvatar = computed(() => userAvatarUrl(userStore.userInfo))

const ROLE_LABEL: Record<string, string> = {
  system_admin: '系统管理员',
  dept_admin: '部门管理员',
  aibp: 'AIBP',
  observer: '观察员',
  admin: '管理员',
  ai_engineer: 'AI 工程师',
  biz_owner: '业务负责人',
  director: '总监',
  viewer: '查看者',
  operator: '运营',
}

const roleText = computed(() => {
  const role = userStore.role || ''
  return ROLE_LABEL[role] || role || '未分配角色'
})

const themeHint = computed(() => {
  if (userStore.theme === 'auto') return `跟随系统（${userStore.darkMode ? '暗色' : '亮色'}）`
  return userStore.theme === 'dark' ? '暗色' : '亮色'
})

const pwdForm = reactive({
  old_password: '',
  new_password: '',
  confirm_password: '',
})
const pwdLoading = ref(false)

function validateConfirm(value: string, callback: (message?: string) => void) {
  if (value !== pwdForm.new_password) callback('两次输入的密码不一致')
  else callback()
}

function handleChangePassword() {
  Modal.confirm({
    title: '确认修改密码',
    content: '确认修改密码？修改后当前会话保持有效',
    okText: '确认修改',
    cancelText: '取消',
    async onOk() {
      pwdLoading.value = true
      try {
        await authApi.changePassword({
          old_password: pwdForm.old_password,
          new_password: pwdForm.new_password,
        })
        Message.success('密码修改成功')
        pwdForm.old_password = ''
        pwdForm.new_password = ''
        pwdForm.confirm_password = ''
      } catch {
      } finally {
        pwdLoading.value = false
      }
    },
  })
}

function handleThemeChange(value: string | number | boolean) {
  userStore.setTheme(String(value))
}
</script>

<style scoped>
/* 设计稿 page chrome 覆盖 */
.me-page {
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}
.me-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.me-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
}
.me-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.me-page :deep(.page-section-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}
.me-page :deep(.page-section-card .arco-card-header-title) {
  font-weight: 500 !important;
  font-size: 13px !important;
  color: var(--ai-ink-1) !important;
  letter-spacing: -0.005em;
}

.me-section + .me-section {
  margin-top: 14px;
}

.me-account {
  display: flex;
  align-items: center;
  gap: 18px;
}

.me-account-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.me-account-name {
  font-size: 18px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
}

.me-account-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.me-meta-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  color: var(--ai-ink-2);
  font-size: 11.5px;
  font-weight: 500;
}
.me-meta-chip--mono {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

.me-form-actions {
  display: flex;
  justify-content: flex-end;
}

.me-page :deep(.arco-form-item-label) {
  font-weight: 500;
  font-size: 13px;
  color: var(--ai-ink-1);
}
.me-page :deep(.arco-input-wrapper) {
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface);
}
.me-page :deep(.arco-input-wrapper:focus-within) {
  border-color: var(--ai-ink-3);
  box-shadow: none;
}
.me-page :deep(.arco-btn-primary) {
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
  border-radius: 6px;
  height: 30px;
  font-weight: 500;
  box-shadow: none;
}
.me-page :deep(.arco-btn-primary:hover) {
  background: #000;
  border-color: #000;
}
.me-page :deep(.arco-radio-group-button .arco-radio-button) {
  font-size: 12.5px;
  height: 28px;
  line-height: 28px;
}
.me-page :deep(.arco-radio-group-button .arco-radio-button-content) {
  font-weight: 500;
}
.me-page :deep(.arco-radio-group-button .arco-radio-checked .arco-radio-button-content) {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
}

.me-theme {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

.me-theme-hint {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
</style>
