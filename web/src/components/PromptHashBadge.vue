<template>
  <a-tooltip :content="`Prompt 版本 ${version}\nHash: ${hash}`">
    <a-tag size="small" color="arcoblue" class="sf-prompt-hash">
      <icon-code :size="11" />
      <span class="hash-text">{{ shortHash }}</span>
    </a-tag>
  </a-tooltip>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { IconCode } from '@arco-design/web-vue/es/icon'

/**
 * Prompt hash 小标签。用于审核详情/审计日志页面，显示"本次结果由 prompt vX (hash) 生成"
 *
 * 用法：
 *   <PromptHashBadge :hash="report.prompt_hash" version="v1" />
 */
const props = defineProps({
  hash: { type: String, default: '' },
  version: { type: String, default: 'v1' },
})

const shortHash = computed(() => (props.hash || '').slice(0, 8))
</script>

<style scoped>
.sf-prompt-hash {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-family: var(--ai-font-mono);
}
.hash-text {
  font-size: 11px;
}
</style>
