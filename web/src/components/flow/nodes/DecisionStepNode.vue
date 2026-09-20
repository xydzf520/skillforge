<template>
  <div class="flow-node step-node">
    <Handle type="target" :position="Position.Left" />
    <Handle type="source" :position="Position.Right" />
    <div class="node-icon">🔀</div>
    <div class="node-body">
      <div class="node-title">Step {{ data.id }}: {{ data.name }}</div>
      <div class="branch-list">
        <div v-for="(b, i) in (data.branches || [])" :key="i" class="branch-row">
          <span class="branch-dot" :class="`dot-${conclusionColor(b.conclusion)}`"></span>
          <span class="branch-cond">{{ truncate(b.condition, 30) }}</span>
          <span class="branch-result" :class="`c-${conclusionColor(b.conclusion)}`">{{ b.conclusion }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { PropType } from 'vue'
import { Handle, Position } from '@vue-flow/core'
defineProps({ data: Object as PropType<any> })

function truncate(s: string, n: number) { return s && s.length > n ? s.slice(0, n) + '…' : s || '' }
function conclusionColor(t: string) {
  const s = (t || '').toLowerCase()
  if (s.includes('绿') || s.includes('适用') || s.includes('完备') || s.includes('成功') || s.includes('完成')) return 'green'
  if (s.includes('黄') || s.includes('维持')) return 'yellow'
  if (s.includes('红') || s.includes('暂停') || s.includes('失败')) return 'red'
  return 'blue'
}
</script>

<style scoped>
.step-node { background: var(--ai-surface); border: 1.5px solid var(--ai-border); min-width: 260px; padding-left: 20px; }
.branch-list { margin-top: 6px; display: flex; flex-direction: column; gap: 4px; }
.branch-row {
  display: flex; align-items: center; gap: 6px;
  font-size: 11px; padding: 3px 8px;
  background: var(--ai-surface-2); border-radius: 6px;
}
.branch-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.dot-green { background: var(--ai-ok); }
.dot-yellow { background: var(--ai-warn); }
.dot-red { background: var(--ai-bad); }
.dot-blue { background: var(--ai-info); }
.branch-cond { color: var(--ai-ink-3); font-family: var(--ai-font-mono); font-size: 10px; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.branch-result { font-weight: 600; font-size: 11px; flex-shrink: 0; }
.c-green { color: var(--ai-ok); }
.c-yellow { color: var(--ai-warn); }
.c-red { color: var(--ai-bad); }
.c-blue { color: var(--ai-info); }
</style>
