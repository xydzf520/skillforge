<template>
  <a-card title="Patch 预览" :body-style="{ padding: '12px' }" class="wb-card">
    <div v-if="!patch" class="wb-empty">这里会展示本轮生成的 patch 摘要和 diff 预览。</div>
    <template v-else>
      <div class="wb-section">
        <div class="wb-label">目标模块</div>
        <a-tag color="arcoblue">{{ patch.target_module }}</a-tag>
      </div>
      <div class="wb-section">
        <div class="wb-label">意图</div>
        <div>{{ patch.intent }}</div>
      </div>
      <div class="wb-section">
        <div class="wb-label">摘要</div>
        <div>{{ patch.summary }}</div>
      </div>
      <div class="wb-section">
        <div class="wb-label">预览</div>
        <pre class="wb-pre">{{ JSON.stringify(patch.diff_preview || {}, null, 2) }}</pre>
      </div>
      <div class="wb-actions">
        <a-button size="small" :loading="validating" @click="$emit('validate')">验证</a-button>
        <a-button type="primary" size="small" :loading="applying" :disabled="!canApply" @click="$emit('apply')">应用</a-button>
      </div>
    </template>
  </a-card>
</template>

<script setup lang="ts">
import type { PropType } from 'vue'
defineProps({
  patch: { type: Object as PropType<any>, default: null },
  canApply: { type: Boolean, default: false },
  validating: { type: Boolean, default: false },
  applying: { type: Boolean, default: false },
})

defineEmits(['validate', 'apply'])
</script>

<style scoped>
.wb-card { height: 100%; }
.wb-empty { color: var(--ai-ink-4); padding: 12px 0; }
.wb-section { margin-bottom: 12px; }
.wb-label { font-size: 12px; color: var(--ai-ink-3); margin-bottom: 4px; }
.wb-pre {
  background: var(--ai-surface-2);
  border-radius: 8px;
  padding: 10px 12px;
  white-space: pre-wrap;
  font-size: 12px;
  margin: 0;
}
.wb-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
</style>
