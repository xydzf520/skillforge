<template>
  <div class="confirm-bar">
    <div v-for="item in items" :key="item.key" class="cb-item">
      <a-tag size="small" :color="item.passed ? 'green' : 'orange'">{{ item.passed ? '通过' : '待完成' }}</a-tag>
      <div class="cb-copy">
        <strong>{{ item.label }}</strong>
        <span>{{ item.detail }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { PropType } from 'vue'

defineProps({
  items: { type: Array as PropType<Array<{ key: string; passed?: boolean; label?: string; detail?: string }>>, default: () => [] },
})
</script>

<style scoped>
.confirm-bar {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cb-item {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.cb-copy {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.cb-copy strong {
  font-size: 12px;
  color: var(--ai-ink-1);
}
.cb-copy span {
  font-size: 11px;
  color: var(--ai-ink-3);
  line-height: 1.5;
}
</style>
