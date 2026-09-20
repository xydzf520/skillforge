<template>
  <div class="sf-empty">
    <div class="sf-empty-icon">
      <icon-info-circle />
    </div>
    <div class="sf-empty-text">{{ description || '暂无数据' }}</div>
    <slot />
  </div>
</template>

<script setup lang="ts">
import { IconInfoCircle } from '@arco-design/web-vue/es/icon'
defineProps({ description: { type: String, default: '' } })
</script>

<style scoped>
.sf-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 0;
  color: var(--ai-ink-4);
}
.sf-empty-icon {
  font-size: 36px;
  margin-bottom: 12px;
  opacity: 0.4;
}
.sf-empty-text {
  font-size: var(--sf-text-caption);
}
</style>
