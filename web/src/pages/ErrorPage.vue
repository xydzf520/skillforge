<template>
  <div class="auth-shell">
    <div class="auth-grid error-layout">
      <section class="auth-panel auth-brand-panel">
        <span class="auth-brand-badge">导航异常</span>
        <div class="auth-brand-mark">!</div>
        <h1 class="auth-brand-title">当前请求没有命中可访问页面</h1>
        <p class="auth-brand-desc">
          这通常意味着地址不存在、当前账号没有访问权限，或者系统正在处理异常响应。
        </p>
        <div class="page-chip-row">
          <span class="page-chip is-danger">错误码 {{ code }}</span>
          <span class="page-chip is-info">请检查路径与权限</span>
        </div>
        <div class="auth-highlight-list">
          <div class="auth-highlight-item">
            <strong>先确认访问路径</strong>
            <span>如果是外部链接或旧书签，页面可能已经迁移到新的路由结构。</span>
          </div>
          <div class="auth-highlight-item">
            <strong>再确认当前权限</strong>
            <span>部分页面只对指定角色开放，没有权限时会直接跳到错误页。</span>
          </div>
          <div class="auth-highlight-item">
            <strong>仍有问题可返回主站</strong>
            <span>你可以先回到首页继续工作，再从主导航重新进入对应模块。</span>
          </div>
        </div>
      </section>

      <section class="auth-panel error-panel">
        <div class="error-badge">SkillForge</div>
        <div class="error-code">{{ code }}</div>
        <div class="error-title">{{ title }}</div>
        <div class="error-desc">{{ desc }}</div>
        <a-space class="error-actions" wrap>
          <a-button type="primary" @click="$router.push('/')">返回首页</a-button>
          <a-button @click="$router.back()">返回上页</a-button>
        </a-space>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route: any = useRoute()

const messages: Record<string, { title: string; desc: string }> = {
  403: { title: '无权访问', desc: '你没有权限访问该页面，请联系管理员。' },
  404: { title: '页面未找到', desc: '你访问的页面不存在或已被移除。' },
  500: { title: '服务异常', desc: '服务器出现问题，请稍后重试。' },
}

const code = computed(() => route.params.code || '404')
const title = computed(() => messages[code.value]?.title || '未知错误')
const desc = computed(() => messages[code.value]?.desc || '发生了未知错误。')
</script>

<style scoped>
.error-badge {
  display: inline-flex;
  margin-bottom: 18px;
  align-self: center;
  padding: 0 7px;
  height: 20px;
  align-items: center;
  border-radius: 4px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  color: var(--ai-ink-2);
  font-size: 11px;
  font-weight: 500;
  font-family: var(--ai-font-sans);
}
.error-code {
  font-size: clamp(96px, 18vw, 132px);
  font-weight: 600;
  line-height: 1;
  color: var(--ai-ink-5);
  font-family: var(--ai-font-mono);
  letter-spacing: -0.04em;
}
.error-title {
  font-size: 22px;
  font-weight: 600;
  color: var(--ai-ink-1);
  margin-top: 16px;
  letter-spacing: -0.02em;
}
.error-desc {
  font-size: 13px;
  color: var(--ai-ink-3);
  margin-top: 6px;
  font-weight: 400;
}
.error-actions {
  margin-top: 24px;
  justify-content: center;
}
.error-actions :deep(.arco-btn-primary) {
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
  border-radius: 6px;
  height: 30px;
  font-weight: 500;
  box-shadow: none;
}
.error-actions :deep(.arco-btn-primary:hover) {
  background: #000;
  border-color: #000;
}
.error-actions :deep(.arco-btn:not(.arco-btn-primary)) {
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  color: var(--ai-ink-1);
  border-radius: 6px;
  height: 30px;
  font-weight: 500;
}

@media (max-width: 768px) {
  .error-layout {
    grid-template-columns: 1fr;
  }

  .error-code {
    font-size: clamp(72px, 14vw, 96px);
  }
}
</style>
