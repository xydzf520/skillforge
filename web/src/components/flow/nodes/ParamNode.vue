<template>
  <div class="flow-node param-node">
    <Handle type="source" :position="Position.Right" />
    <div class="node-icon">⚙</div>
    <div class="node-body">
      <div class="node-title">参数配置</div>
      <div class="param-list">
        <div v-for="(val, key) in limitedParams" :key="key" class="param-row">
          <span class="param-key">{{ key }}</span>
          <span class="param-val">{{ val }}</span>
        </div>
      </div>
      <div v-if="extraCount > 0" class="node-meta">+{{ extraCount }} 项</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import { Handle, Position } from '@vue-flow/core'
const props = defineProps({ data: Object as PropType<any> })
const entries = computed(() => Object.entries(props.data.params || {}))
const limitedParams = computed(() => Object.fromEntries(entries.value.slice(0, 4)))
const extraCount = computed(() => Math.max(0, entries.value.length - 4))
</script>

<style scoped>
.param-node { background: var(--ai-warn-soft); border: 1.5px solid var(--ai-border); min-width: 180px; padding-left: 20px; }
.param-list { margin-top: 4px; display: flex; flex-direction: column; gap: 2px; }
.param-row { font-size: 11px; font-family: var(--ai-font-mono); display: flex; gap: 4px; }
.param-key { color: var(--ai-warn); font-weight: 500; }
.param-key::after { content: ':'; }
.param-val { color: var(--ai-ink-2); }
</style>
