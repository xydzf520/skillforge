<template>
  <div class="file-tree">
    <div class="file-tree-header">
      <span class="file-tree-title">{{ title }}</span>
    </div>
    <div class="file-tree-list">
      <TreeNode
        v-for="node in files"
        :key="node.path || node.key"
        :node="node"
        :depth="0"
        :active-key="activeKey"
        :collapsed="collapsed"
        @select="(k) => $emit('select', k)"
        @toggle="toggleDir"
        @contextmenu="(e) => $emit('contextmenu', e)"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive } from 'vue'
import type { PropType } from 'vue'
import TreeNode from './TreeNode.vue'

defineProps({
  title: { type: String, default: '文件' },
  files: { type: Array as PropType<any[]>, default: () => [] },
  activeKey: { type: String, default: '' },
})

defineEmits(['select', 'contextmenu'])

const collapsed = reactive<Record<string, boolean>>({})

function toggleDir(path: string) {
  collapsed[path] = !collapsed[path]
}
</script>

<style scoped>
.file-tree {
  width: 220px;
  min-width: 220px;
  background: var(--ai-surface-2);
  border-right: 1px solid var(--ai-border);
  display: flex;
  flex-direction: column;
  user-select: none;
}

.file-tree-header {
  padding: 14px 16px 10px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  color: var(--ai-ink-3);
  letter-spacing: 0.5px;
  border-bottom: 1px solid var(--ai-border);
}

.file-tree-list {
  flex: 1;
  overflow-y: auto;
  padding-bottom: 8px;
}
</style>
