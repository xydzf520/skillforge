<template>
  <div class="preview-card">
    <div class="pc-head">
      <span class="pc-title">{{ title }}</span>
      <a-tag size="small" color="arcoblue">{{ adapterLabel }}</a-tag>
    </div>
    <pre class="pc-body">{{ content }}</pre>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps({
  adapter: { type: String, default: 'preview' },
  content: { type: String, default: '' },
  title: { type: String, default: '预演结果' },
})

const adapterLabel = computed(() => props.adapter || 'preview')
</script>

<style scoped>
.preview-card {
  padding: 14px 16px;
  border-radius: 12px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
}
.pc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.pc-title {
  font-size: 13px;
  font-weight: 800;
  color: var(--ai-ink-1);
}
.pc-body {
  margin: 0;
  white-space: pre-wrap;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ai-ink-2);
}
</style>
