<template>
  <div class="sf-error-state">
    <a-result status="error" :title="title" :subtitle="subtitle">
      <template #extra>
        <a-button type="primary" @click="$emit('retry')">
          {{ retryText }}
        </a-button>
      </template>
    </a-result>
  </div>
</template>

<script setup lang="ts">
/**
 * 统一错误状态组件
 *
 * 展示错误标题 + 详情，附带"重试"按钮。
 * 父组件监听 @retry 事件即可触发重新加载。
 */

withDefaults(
  defineProps<{
    /** 错误标题 */
    title?: string
    /** 错误详情 */
    subtitle?: string
    /** 重试按钮文案 */
    retryText?: string
  }>(),
  {
    title: '加载失败',
    subtitle: '请稍后再试',
    retryText: '重试',
  },
)

defineEmits<{
  (e: 'retry'): void
}>()
</script>

<style scoped>
.sf-error-state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--sf-spacing-xl, 24px) 0;
}
</style>
