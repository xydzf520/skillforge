<template>
  <div v-if="patch" class="patch-overlay">
    <div class="po-bar">
      <icon-swap :size="14" />
      <span class="po-title">AI 变更预览 · {{ patch.target_module }}</span>
      <span class="po-summary">{{ patch.summary }}</span>
      <span v-if="diffItems.length" class="po-hunk-count">
        {{ acceptedCount }}/{{ diffItems.length }} 项已选
      </span>
      <div class="po-actions">
        <a-button size="mini" status="danger" @click="rejectAll">全部拒绝</a-button>
        <a-button
          v-if="hasPartialSelection"
          size="mini"
          type="primary"
          :loading="applying"
          @click="emit('apply-selected', acceptedKeys)"
        >应用已选 ({{ acceptedCount }})</a-button>
        <a-button size="mini" type="primary" :loading="applying" @click="emit('apply')">应用全部</a-button>
      </div>
    </div>
    <!-- diff 内容（带逐项选择） -->
    <div class="po-diff" v-if="diffItems.length">
      <div
        v-for="(d, i) in diffItems"
        :key="i"
        class="diff-line"
        :class="[d.type, d.selected]"
      >
        <button
          v-if="d.selected === 'pending'"
          class="hunk-toggle accept-toggle"
          title="接受此项"
          @click="toggleItem(i, 'accepted')"
        >
          <icon-check :size="11" />
        </button>
        <button
          v-if="d.selected === 'pending'"
          class="hunk-toggle reject-toggle"
          title="拒绝此项"
          @click="toggleItem(i, 'rejected')"
        >
          <icon-close :size="11" />
        </button>
        <a-tag v-if="d.selected === 'accepted'" color="green" size="small" class="hunk-status-tag" @click="toggleItem(i, 'pending')">已选</a-tag>
        <a-tag v-if="d.selected === 'rejected'" color="red" size="small" class="hunk-status-tag" @click="toggleItem(i, 'pending')">已拒</a-tag>
        <span class="diff-marker">{{ d.type === 'add' ? '+' : d.type === 'remove' ? '-' : ' ' }}</span>
        <span class="diff-key">{{ d.key }}</span>
        <span class="diff-val">{{ d.display }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { PropType } from 'vue'
import { IconSwap, IconCheck, IconClose } from '@arco-design/web-vue/es/icon'

interface DiffItem {
  key: string
  display: string
  type: string
  selected: 'pending' | 'accepted' | 'rejected'
}

const props = defineProps({
  patch: { type: Object as PropType<any>, default: null },
  applying: { type: Boolean, default: false },
  previousValue: { type: [Object, Array, String] as PropType<any>, default: null },
})
const emit = defineEmits(['apply', 'reject', 'apply-selected'])

const diffItems = ref<DiffItem[]>([])

watch(() => props.patch, (p) => {
  if (!p?.patch) { diffItems.value = []; return }
  const items: DiffItem[] = []
  const patchData = p.patch || {}
  for (const [key, val] of Object.entries(patchData)) {
    if (Array.isArray(val)) {
      items.push({ key, display: `${val.length} 项`, type: 'add', selected: 'pending' })
    } else if (typeof val === 'object' && val !== null) {
      items.push({ key, display: `${Object.keys(val as object).length} 个字段`, type: 'add', selected: 'pending' })
    } else {
      items.push({ key, display: String(val).slice(0, 80), type: 'add', selected: 'pending' })
    }
  }
  diffItems.value = items
}, { immediate: true, deep: true })

const acceptedCount = computed(() => diffItems.value.filter(d => d.selected === 'accepted').length)
const hasPartialSelection = computed(() => {
  const count = acceptedCount.value
  return count > 0 && count < diffItems.value.length
})
const acceptedKeys = computed(() =>
  diffItems.value.filter(d => d.selected === 'accepted').map(d => d.key)
)

function toggleItem(index: number, status: 'pending' | 'accepted' | 'rejected') {
  diffItems.value[index].selected = status
}

function rejectAll() {
  diffItems.value.forEach(d => { d.selected = 'rejected' })
  emit('reject')
}
</script>

<style scoped>
.patch-overlay {
  border: 2px solid var(--ai-accent-ink);
  border-radius: 10px;
  background: var(--ai-accent-soft);
  margin: 12px 0;
  overflow: hidden;
  animation: slideIn .2s ease;
}
@keyframes slideIn { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: none; } }

.po-bar {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; background: rgba(var(--primary-6), .08);
  font-size: 12px; color: var(--ai-accent-ink);
}
.po-title { font-weight: 600; }
.po-summary { flex: 1; color: var(--ai-ink-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.po-hunk-count { font-size: 11px; font-weight: 600; color: var(--ai-ink-2); white-space: nowrap; }
.po-actions { display: flex; gap: 6px; flex-shrink: 0; }

.po-diff { padding: 8px 12px; }
.diff-line {
  display: flex; align-items: center; gap: 8px; padding: 3px 6px; border-radius: 4px;
  font-size: 12px; font-family: var(--ai-font-mono); line-height: 1.5;
  transition: opacity .15s, background .15s;
}
.diff-line.add { background: rgba(var(--green-6), .08); color: var(--ai-ok); }
.diff-line.remove { background: rgba(var(--red-6), .08); color: var(--ai-bad); text-decoration: line-through; }
.diff-line.accepted { background: rgba(var(--green-6), .15); }
.diff-line.rejected { opacity: .45; }

.hunk-toggle {
  width: 20px; height: 20px; border-radius: 4px; border: none;
  display: flex; align-items: center; justify-content: center; cursor: pointer;
  flex-shrink: 0; transition: background .1s;
}
.accept-toggle { background: var(--ai-ok-soft); color: var(--ai-ok); }
.accept-toggle:hover { background: var(--ai-ok-soft); }
.reject-toggle { background: var(--ai-bad-soft); color: var(--ai-bad); }
.reject-toggle:hover { background: var(--ai-bad-soft); }
.hunk-status-tag { cursor: pointer; flex-shrink: 0; }

.diff-marker { width: 12px; font-weight: 700; flex-shrink: 0; }
.diff-key { font-weight: 600; min-width: 60px; }
.diff-val { flex: 1; word-break: break-word; }
</style>
