<template>
  <div class="auth-shell">
    <section class="auth-panel auth-single">
      <header class="auth-single-head">
        <SfBrand size="lg" icon-only />
        <h1 class="auth-brand-title">{{ shareLogin ? '登录后打开项目' : 'SkillForge' }}</h1>
        <p class="login-brand-description">企业 AI 工作台 · 技能协作平台</p>
      </header>

      <section v-if="shareLogin" class="share-login-card">
        <div class="share-login-icon"><icon-scan /></div>
        <div>
          <strong>分享项目需要登录</strong>
          <span>请先使用钉钉扫码登录；登录成功后会自动打开分享的项目运行页。</span>
        </div>
        <a-button
          v-if="dingtalkEnabled"
          long
          type="primary"
          size="large"
          :disabled="loading"
          class="share-scan-btn"
          @click="dingtalkLogin"
        >
          <template #icon><icon-scan /></template>
          钉钉扫码登录并打开项目
        </a-button>
        <span v-else class="share-login-fallback">当前环境未启用钉钉扫码，可使用账号密码登录。</span>
      </section>

      <a-form class="auth-form" :model="form" @submit-success="handleLogin" layout="vertical">
        <a-divider v-if="shareLogin && dingtalkEnabled" orientation="center">或使用账号密码</a-divider>
        <a-form-item field="username" label="用户名" :rules="[{ required: true, message: '请输入用户名' }]">
          <a-input
            v-model="form.username"
            placeholder="请输入用户名"
            size="large"
            allow-clear
            :disabled="loading"
          >
            <template #prefix><icon-user /></template>
          </a-input>
        </a-form-item>

        <a-form-item field="password" label="密码" :rules="[{ required: true, message: '请输入密码' }]">
          <a-input-password
            v-model="form.password"
            placeholder="请输入密码"
            size="large"
            :disabled="loading"
          >
            <template #prefix><icon-lock /></template>
          </a-input-password>
        </a-form-item>

        <a-button type="primary" html-type="submit" long size="large" :loading="loading">
          登 录
        </a-button>
      </a-form>

      <template v-if="dingtalkEnabled && !shareLogin">
        <a-divider orientation="center">其他登录方式</a-divider>
        <a-button long :disabled="loading" @click="dingtalkLogin" class="dingtalk-btn">
          <template #icon><icon-scan /></template>
          钉钉扫码登录
        </a-button>
      </template>

      <div class="auth-footer auth-footer--center">
        <span class="auth-footer-version">SkillForge · {{ appVersion }}</span>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, onMounted } from 'vue'
import type { Router } from 'vue-router'
import { useRouter, useRoute } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { authApi as rawAuthApi } from '@/api'
import { Message } from '@arco-design/web-vue'
import { IconUser, IconLock, IconScan } from '@arco-design/web-vue/es/icon'
import SfBrand from '@/components/brand/SfBrand.vue'

const authApi: any = rawAuthApi
const router = useRouter() as Router
const route = useRoute()
const userStore = useUserStore()
const loading = ref(false)
const dingtalkEnabled = ref(false)
const form = reactive({ username: '', password: '' })
const appVersion = __APP_VERSION__
const shareLogin = computed(() => route.query.reason === 'project_share' || route.query.scan === '1')

function safeRedirect(): string {
  // 只允许站内路径（以 `/` 开头但不是 `//`，防 open redirect）
  const raw = route.query.redirect ?? route.query.next
  const value = Array.isArray(raw) ? raw[0] : raw
  if (typeof value === 'string' && value.startsWith('/') && !value.startsWith('//')) {
    return value
  }
  return '/'
}

async function handleLogin() {
  loading.value = true
  try {
    const data = await userStore.login(form.username, form.password)
    Message.success('登录成功')
    if (data.must_change_password) {
      router.push('/change-password')
    } else if (data.state === 'pending') {
      router.push('/pending')
    } else {
      router.push(safeRedirect())
    }
  } catch {
    // 错误已在 request 拦截器中处理
  } finally {
    loading.value = false
  }
}

function dingtalkLogin() {
  window.location.href = `/api/auth/dingtalk/login?next=${encodeURIComponent(safeRedirect())}`
}

onMounted(async () => {
  try {
    const data = await authApi.providers()
    dingtalkEnabled.value = Boolean(data?.dingtalk)
  } catch {
    dingtalkEnabled.value = false
  }
})
</script>

<style scoped>
.auth-single {
  width: min(420px, 100%);
  padding: 36px 32px 24px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-family: var(--ai-font-sans);
}

.auth-single-head {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
}

.auth-single-head .auth-brand-title {
  font-size: 22px;
  margin: 0;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
}

.login-brand-description {
  margin: 0;
  color: var(--ai-ink-3);
  font-size: 13px;
  line-height: 1.6;
  text-align: center;
}

.auth-footer--center {
  justify-content: center;
}

.auth-footer-version {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-family: var(--ai-font-mono);
}

.dingtalk-btn {
  color: var(--ai-ink-1);
  border-color: var(--ai-border);
  font-weight: 500;
  border-radius: 6px;
  height: 38px;
}

.share-login-card {
  display: grid;
  gap: 12px;
  padding: 16px;
  margin-bottom: 18px;
  border: 1px solid var(--ai-border);
  border-radius: var(--sf-panel-radius);
  background: var(--ai-accent-soft);
  color: var(--ai-ink-1);
}

.share-login-card > div:not(.share-login-icon) {
  display: grid;
  gap: 4px;
}

.share-login-card strong {
  font-size: 15px;
  font-weight: 700;
}

.share-login-card span {
  color: var(--ai-ink-3);
  font-size: 12.5px;
  line-height: 1.7;
}

.share-login-icon {
  width: 34px;
  height: 34px;
  display: inline-grid;
  place-items: center;
  border-radius: 12px;
  background: var(--ai-accent-soft);
  color: var(--ai-accent);
}

.share-scan-btn {
  border-radius: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.share-login-fallback {
  display: block;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--ai-surface);
}

.auth-form :deep(.arco-form-item-label) {
  font-weight: 500;
  color: var(--ai-ink-1);
  font-size: 13px;
  letter-spacing: -0.005em;
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
  background: var(--sf-brand-action);
  border-color: var(--sf-brand-action);
  color: var(--sf-on-action);
  border-radius: 6px;
  font-weight: 500;
  letter-spacing: 0.2em;
  box-shadow: none;
}
.auth-form :deep(.arco-btn-primary:hover) {
  background: var(--sf-brand-action-hover);
  border-color: var(--sf-brand-action-hover);
}

.auth-form :deep(.arco-input-prefix),
.auth-form :deep(.arco-input-suffix),
.auth-single :deep(.arco-btn-icon) {
  color: var(--ai-ink-3);
}

.auth-single :deep(.arco-divider-text) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 400;
}
.auth-single :deep(.arco-divider-horizontal.arco-divider-with-text) {
  border-color: var(--ai-border);
}

.auth-form :deep(.arco-input-prefix .arco-icon),
.auth-form :deep(.arco-input-suffix .arco-icon),
.auth-single :deep(.arco-btn-icon .arco-icon) {
  /* 特殊用途：icon 字形尺寸，不是正文字号，不走 --sf-text-* token */
  font-size: 18px;
  stroke-width: 5;
}

@media (max-width: 768px) {
  .auth-single {
    padding: 32px 24px 22px;
  }

  .auth-single-head .auth-brand-title {
    font-size: 22px;
  }
}

@media (max-width: 480px) {
  .auth-single {
    padding: 28px 20px 20px;
  }
}
</style>
