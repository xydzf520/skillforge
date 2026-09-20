<template>
  <div class="sf-empty-state">
    <a-empty :description="description">
      <template #image>
        <div class="sf-empty-illustration">
          <SfShellIcon :name="icon" />
        </div>
      </template>
      <template #default>
        <div class="sf-empty-body">
          <div v-if="title" class="sf-empty-title">{{ title }}</div>
          <div class="sf-empty-desc">{{ description }}</div>
          <div v-if="hint" class="sf-empty-hint">{{ hint }}</div>
          <!-- 按钮渲染在 default 插槽内：arco <a-empty> 并没有 #extra 插槽，之前的写法永远不触发 -->
          <div v-if="$slots.action || actionLabel" class="sf-empty-action">
            <slot name="action">
              <a-button v-if="actionLabel" type="primary" size="small" @click="$emit('action')">{{ actionLabel }}</a-button>
            </slot>
          </div>
        </div>
      </template>
    </a-empty>
  </div>
</template>

<script setup lang="ts">
/**
 * 统一空状态组件
 *
 * 包装 Arco 的 <a-empty>，在 description 之外额外提供：
 * - title：页面作用说明
 * - hint：为何为空 / 下一步提示
 * - actionLabel + @action：主 CTA 按钮
 * 原有 action 插槽保持兼容。
 */
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

withDefaults(
  defineProps<{
    /** 描述文案（主文案） */
    description?: string
    /** 空状态图标，来自 SfShellIcon 名称；按业务语义传入 inbox/doc/database 等 */
    icon?: string
    /** 页面作用说明（在 description 之上以较小字体显示） */
    title?: string
    /** 提示文案（在 description 之下，说明为何为空 / 下一步） */
    hint?: string
    /** 主 CTA 按钮文案，传了才显示 */
    actionLabel?: string
  }>(),
  {
    description: '暂无数据',
    icon: 'archive',
    title: '',
    hint: '',
    actionLabel: '',
  },
)

defineEmits<{ (e: 'action'): void }>()
</script>

<style scoped>
.sf-empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--sf-spacing-xxl, 32px) 0;
}
.sf-empty-illustration {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  margin: 0 auto;
  border-radius: 12px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  color: var(--ai-ink-4);
}
.sf-empty-illustration :deep(svg) {
  width: 22px;
  height: 22px;
  stroke-width: 1.5;
}
.sf-empty-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}
.sf-empty-title {
  font-size: 13px;
  color: var(--ai-ink-3);
  font-weight: 700;
  letter-spacing: 0.02em;
}
.sf-empty-desc {
  font-size: 14px;
  color: var(--ai-ink-2);
}
.sf-empty-hint {
  font-size: 12px;
  color: var(--ai-ink-3);
  max-width: 360px;
  line-height: 1.5;
  text-align: center;
  margin-top: 4px;
}
.sf-empty-action {
  margin-top: 12px;
  display: flex;
  justify-content: center;
}
</style>
