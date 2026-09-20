<template>
  <!-- 目录节点 -->
  <div v-if="node.type === 'dir'">
    <div
      class="tree-item dir"
      :style="{ paddingLeft: depth * 16 + 8 + 'px' }"
      @click="$emit('toggle', node.path)"
      @contextmenu.prevent="$emit('contextmenu', { event: $event, node, type: 'dir' })"
    >
      <span class="chevron" :class="{ collapsed: collapsed[node.path] }">
        <svg width="10" height="10" viewBox="0 0 16 16"><path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </span>
      <span class="file-icon dir-icon">
        <svg v-if="collapsed[node.path]" width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M2 6a2 2 0 012-2h5l2 2h9a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" fill="rgb(var(--orange-5))" opacity="0.85"/></svg>
        <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M2 6a2 2 0 012-2h5l2 2h9a2 2 0 012 2v1H7.5a2 2 0 00-1.9 1.37L2 20V6z" fill="rgb(var(--orange-5))" opacity="0.85"/><path d="M5.5 11h15.09a2 2 0 011.9 2.63l-1.8 5.74A2 2 0 0118.8 21H4a2 2 0 01-2-2v-1l3.5-7z" fill="rgb(var(--orange-4))" opacity="0.7"/></svg>
      </span>
      <span class="name">{{ node.name }}</span>
    </div>
    <div v-show="!collapsed[node.path]">
      <TreeNode
        v-for="child in node.children"
        :key="child.path"
        :node="child"
        :depth="depth + 1"
        :active-key="activeKey"
        :collapsed="collapsed"
        @select="(k) => $emit('select', k)"
        @toggle="(p) => $emit('toggle', p)"
        @contextmenu="(e) => $emit('contextmenu', e)"
      />
    </div>
  </div>
  <!-- 文件节点 -->
  <div
    v-else
    class="tree-item file"
    :class="{ active: fileKey === activeKey }"
    :style="{ paddingLeft: depth * 16 + 8 + 'px' }"
    @click="$emit('select', fileKey)"
    @contextmenu.prevent="$emit('contextmenu', { event: $event, node, type: 'file' })"
  >
    <span class="chevron-spacer"></span>
    <span class="file-icon" :style="{ color: iconColor(node.name) }">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6z" stroke="currentColor" stroke-width="1.5"/><path d="M14 2v6h6" stroke="currentColor" stroke-width="1.5"/></svg>
    </span>
    <span class="name">{{ node.name }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'

const props = defineProps({
  node: { type: Object as PropType<any>, required: true },
  depth: { type: Number, default: 0 },
  activeKey: { type: String, default: '' },
  collapsed: { type: Object as PropType<Record<string, boolean>>, default: () => ({}) },
})

defineEmits(['select', 'toggle', 'contextmenu'])

const fileKey = computed(() => {
  const p = props.node.path
  if (p === 'SKILL.md') return 'skill_md'
  if (p === 'policy_pack.yaml') return 'policy_pack'
  if (p.startsWith('scripts/')) return `script_${p}`
  if (p.startsWith('references/')) return `ref_${p}`
  return `file_${p}`
})

function iconColor(name: string) {
  if (name === 'SKILL.md') return 'var(--ai-accent-ink)'
  if (name.endsWith('.yaml') || name.endsWith('.yml')) return 'var(--ai-bad)'
  if (name.endsWith('.py')) return 'var(--ai-ok)'
  if (name.endsWith('.md')) return 'var(--ai-accent-ink)'
  if (name.endsWith('.json')) return 'var(--ai-warn)'
  if (name.endsWith('.sh')) return 'var(--ai-ok)'
  if (name.endsWith('.csv')) return 'var(--ai-ok)'
  return 'var(--ai-ink-3)'
}
</script>

<style scoped>
.tree-item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  margin: 0 4px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  color: var(--ai-ink-2);
  transition: background var(--sf-transition);
  white-space: nowrap;
  height: 26px;
}
.tree-item:hover {
  background: var(--ai-surface-2);
}
.tree-item.active {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}

/* VSCode 风格箭头 */
.chevron {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: var(--ai-ink-4);
  transition: transform 0.15s ease;
  transform: rotate(90deg);
}
.chevron.collapsed {
  transform: rotate(0deg);
}
.chevron-spacer {
  width: 16px;
  flex-shrink: 0;
}

/* 文件图标 */
.file-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.name {
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
