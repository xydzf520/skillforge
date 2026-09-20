<template>
  <div class="status-bar">
    <div class="status-left">
      <span v-if="branch" class="status-item" title="Git 分支">
        <span class="status-icon">⎇</span> {{ branch }}
      </span>
      <span v-if="errors > 0" class="status-item status-error" title="错误">
        ✕ {{ errors }}
      </span>
      <span v-if="warnings > 0" class="status-item status-warning" title="警告">
        ⚠ {{ warnings }}
      </span>
      <span v-if="errors === 0 && warnings === 0" class="status-item">
        ✓ 无问题
      </span>
    </div>
    <div class="status-right">
      <span class="status-item" title="光标位置">
        行 {{ line }}, 列 {{ column }}
      </span>
      <span class="status-item" title="语言模式">
        {{ languageLabel }}
      </span>
      <span class="status-item">UTF-8</span>
      <span v-if="aiEnabled" class="status-item status-ai" title="AI 补全已启用">
        AI ✓
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps({
  line: { type: Number, default: 1 },
  column: { type: Number, default: 1 },
  language: { type: String, default: 'skill-md' },
  branch: { type: String, default: '' },
  errors: { type: Number, default: 0 },
  warnings: { type: Number, default: 0 },
  aiEnabled: { type: Boolean, default: true },
})

const languageLabel = computed(() => {
  const labels: Record<string, string> = {
    'skill-md': 'Skill Markdown',
    'yaml': 'YAML',
    'python': 'Python',
    'json': 'JSON',
    'javascript': 'JavaScript',
  }
  return labels[props.language] || props.language
})
</script>

<style scoped>
.status-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 24px;
  padding: 0 12px;
  font-size: 12px;
  user-select: none;
  flex-shrink: 0;
  /* 默认颜色由父组件 :deep 覆盖，这里给安全兜底 */
  background: #007acc;
  color: rgba(255, 255, 255, 0.9);
}

.status-left,
.status-right {
  display: flex;
  align-items: center;
  gap: 14px;
}

.status-item {
  display: flex;
  align-items: center;
  gap: 3px;
  white-space: nowrap;
}

.status-icon { font-size: 13px; }

.status-error { color: #f48771; }
.status-warning { color: #cca700; }
.status-ai { font-weight: 600; }
</style>
