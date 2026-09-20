<template>
  <div class="auth-shell">
    <div class="auth-grid">
      <section class="auth-panel auth-brand-panel">
        <span class="auth-brand-badge">安全初始化</span>
        <SfBrand size="lg" icon-only />
        <div class="auth-brand-title">首次登录需要完成密码更新</div>
        <p class="auth-brand-desc">
          这是账号初始化流程的一部分。更新密码后，才能进入主工作区继续编辑、审核与执行。
        </p>
        <div class="page-chip-row">
          <span class="page-chip is-warning">首次登录</span>
          <span class="page-chip is-info">账号安全</span>
          <span class="page-chip is-success">完成后自动放行</span>
        </div>
        <div class="auth-highlight-list">
          <div class="auth-highlight-item">
            <strong>密码至少 8 个字符</strong>
            <span>建议混合字母和数字，不能与当前密码相同。</span>
          </div>
          <div class="auth-highlight-item">
            <strong>更新后即时生效</strong>
            <span>修改成功后会直接返回主站，不需要再次刷新或重新进入。</span>
          </div>
          <div class="auth-highlight-item">
            <strong>后续可在设置中继续维护</strong>
            <span>这里只是首次登录强制校验，后续仍可按权限自行更新。</span>
          </div>
        </div>
      </section>

      <section class="auth-panel auth-form-panel">
        <div class="auth-form-head">
          <div class="page-kicker">安全校验</div>
          <h1 class="page-title">更新登录密码</h1>
          <p class="page-subtitle">完成密码初始化后，系统会自动放行到主工作区。</p>
        </div>

        <a-form class="auth-form" :model="form" @submit-success="handleSubmit" layout="vertical">
          <a-form-item field="old_password" label="当前密码" :rules="[{ required: true, message: '请输入当前密码' }]">
            <a-input-password v-model="form.old_password" placeholder="请输入当前密码" size="large">
              <template #prefix><icon-lock /></template>
            </a-input-password>
          </a-form-item>

          <a-form-item
            field="new_password"
            label="新密码"
            :rules="[
              { required: true, message: '请输入新密码' },
              { minLength: 8, message: '密码至少8个字符' },
            ]"
          >
            <a-input-password v-model="form.new_password" placeholder="请输入新密码（至少8个字符）" size="large">
              <template #prefix><icon-lock /></template>
            </a-input-password>
          </a-form-item>

          <a-form-item
            field="confirm_password"
            label="确认新密码"
            :rules="[
              { required: true, message: '请确认新密码' },
              { validator: validateConfirm },
            ]"
          >
            <a-input-password v-model="form.confirm_password" placeholder="请再次输入新密码" size="large">
              <template #prefix><icon-lock /></template>
            </a-input-password>
          </a-form-item>

          <a-button type="primary" html-type="submit" long size="large" :loading="loading">
            修改密码
          </a-button>
        </a-form>

        <div class="auth-footer">
          <a-button type="text" size="small" @click="userStore.toggleDark">
            <icon-moon-fill v-if="!userStore.darkMode" />
            <icon-sun-fill v-else />
            {{ userStore.darkMode ? '亮色模式' : '暗色模式' }}
          </a-button>
          <span class="auth-footer-version">SkillForge · {{ appVersion }}</span>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import SfBrand from '@/components/brand/SfBrand.vue'
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { authApi as rawAuthApi } from '@/api'
import { Message, Modal } from '@arco-design/web-vue'
import { IconLock, IconMoonFill, IconSunFill } from '@arco-design/web-vue/es/icon'

const authApi: any = rawAuthApi
const router: any = useRouter()
const userStore = useUserStore()
const loading = ref(false)
const appVersion = __APP_VERSION__
const form = reactive({
  old_password: '',
  new_password: '',
  confirm_password: '',
})

function validateConfirm(value: string, callback: (message?: string) => void) {
  if (value !== form.new_password) {
    callback('两次输入的密码不一致')
  } else {
    callback()
  }
}

function handleSubmit() {
  Modal.confirm({
    title: '确认修改密码',
    content: '修改成功后将直接进入主工作区',
    onOk: async () => {
      loading.value = true
      try {
        await authApi.changePassword({
          old_password: form.old_password,
          new_password: form.new_password,
        })
        if (userStore.userInfo) {
          userStore.userInfo.must_change_password = false
        }
        Message.success('密码修改成功')
        router.push('/')
      } catch {
        // 错误已在 request 拦截器中处理
      } finally {
        loading.value = false
      }
    },
  })
}
</script>

<style scoped>
.auth-form :deep(.arco-form-item-label) {
  font-weight: 500;
  color: var(--ai-ink-1);
  font-size: 13px;
}

.auth-form :deep(.arco-input-wrapper) {
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 6px;
}
.auth-form :deep(.arco-input-wrapper:focus-within) {
  border-color: var(--ai-ink-3);
  box-shadow: none;
}
.auth-form :deep(.arco-btn-primary) {
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
  border-radius: 6px;
  font-weight: 500;
  box-shadow: none;
}
.auth-form :deep(.arco-btn-primary:hover) {
  background: #000;
  border-color: #000;
}

.auth-form :deep(.arco-input-prefix),
.auth-form :deep(.arco-input-suffix) {
  color: var(--ai-ink-3);
}

.auth-form :deep(.arco-input-prefix .arco-icon),
.auth-form :deep(.arco-input-suffix .arco-icon) {
  font-size: 14px;
  stroke-width: 5;
}

.auth-form-head .page-kicker {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.auth-form-head .page-title {
  font-size: 22px;
  font-weight: 600;
  color: var(--ai-ink-1);
  letter-spacing: -0.02em;
  margin: 0 0 4px;
}
.auth-form-head .page-subtitle {
  font-size: 13px;
  color: var(--ai-ink-3);
  font-weight: 400;
  margin: 0;
}

@media (max-width: 768px) {
  .auth-form-head .page-title {
    font-size: 18px;
  }

  .auth-form-head .page-subtitle {
    font-size: 12.5px;
  }
}
</style>
