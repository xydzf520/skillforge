<template>
  <div class="sf-loading-state" :style="{ minHeight: height }">
    <a-spin :size="spinSize" :tip="tip" />
  </div>
</template>

<script setup lang="ts">
/**
 * 统一加载状态组件
 *
 * 替代各页面中重复的 <a-spin :loading="..." style="width:100%"> 写法，
 * 提供居中、最小高度、提示文字等标准化展示。
 */

withDefaults(
  defineProps<{
    /** 提示文字 */
    tip?: string
    /** 容器最小高度 */
    height?: string
    /** 旋转图标尺寸（像素） */
    spinSize?: number
  }>(),
  {
    tip: '加载中...',
    height: '200px',
    spinSize: 24,
  },
)
</script>

<style scoped>
.sf-loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  padding: var(--sf-spacing-xl, 24px) 0;
}
</style>
