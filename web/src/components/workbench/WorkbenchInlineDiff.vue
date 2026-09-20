<template>
  <div class="inline-diff" v-if="patch">
    <div class="diff-header">
      <div class="diff-info">
        <a-tag color="arcoblue" size="small">{{ patch.target_module }}</a-tag>
        <span class="diff-summary">{{ patch.summary || '变更预览' }}</span>
        <span v-if="localHunks.length" class="diff-selection-info">
          {{ acceptedCount }}/{{ localHunks.length }} 已选
        </span>
      </div>
      <div class="diff-actions">
        <a-button
          v-if="hasPartialSelection"
          size="small"
          type="primary"
          :loading="applyingPartial"
          @click="handleApplySelected"
        >
          <icon-check :size="13" style="margin-right:3px" />应用已选 ({{ acceptedCount }})
        </a-button>
        <a-button size="small" type="primary" :loading="applying" @click="$emit('accept-all')">
          <icon-check :size="13" style="margin-right:3px" />全部接受
        </a-button>
        <a-button size="small" status="danger" @click="$emit('reject-all')">
          <icon-close :size="13" style="margin-right:3px" />放弃
        </a-button>
      </div>
    </div>

    <!-- Hunk 列表 -->
    <div class="diff-hunks" v-if="localHunks.length">
      <div
        v-for="(hunk, i) in localHunks"
        :key="i"
        class="diff-hunk"
        :class="hunk.status"
      >
        <div class="hunk-header">
          <span class="hunk-path">{{ hunk.path || `变更 ${Number(i) + 1}` }}</span>
          <span class="hunk-type">{{ hunkTypeLabel(hunk.type || '') }}</span>
          <span class="hunk-spacer" />
          <template v-if="hunk.status === 'pending'">
            <button class="hunk-btn accept" title="接受" @click="setHunkStatus(i, 'accepted')">
              <icon-check :size="12" />
            </button>
            <button class="hunk-btn reject" title="拒绝" @click="setHunkStatus(i, 'rejected')">
              <icon-close :size="12" />
            </button>
          </template>
          <template v-else>
            <a-tag v-if="hunk.status === 'accepted'" color="green" size="small">已接受</a-tag>
            <a-tag v-else-if="hunk.status === 'rejected'" color="red" size="small">已拒绝</a-tag>
            <button class="hunk-btn reset" title="重置" @click="setHunkStatus(i, 'pending')">
              <icon-undo :size="12" />
            </button>
          </template>
        </div>
        <div class="hunk-body" :class="{ 'hunk-body-accepted': hunk.status === 'accepted', 'hunk-body-rejected': hunk.status === 'rejected' }">
          <div v-if="hunk.old" class="hunk-line old">
            <span class="line-marker">-</span>
            <span class="line-text">{{ hunk.old }}</span>
          </div>
          <div v-if="hunk.new" class="hunk-line new">
            <span class="line-marker">+</span>
            <span class="line-text">{{ hunk.new }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 无 hunk 详情时显示简单的 before/after -->
    <div v-else-if="diffPreview" class="diff-simple">
      <div class="diff-col">
        <div class="diff-col-title">变更前</div>
        <pre class="diff-pre old">{{ diffPreview.before || '（空）' }}</pre>
      </div>
      <div class="diff-col">
        <div class="diff-col-title">变更后</div>
        <pre class="diff-pre new">{{ diffPreview.after || '（空）' }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { PropType } from 'vue'
import { IconCheck, IconClose, IconUndo } from '@arco-design/web-vue/es/icon'
import { workbenchApi } from '@/api'

interface HunkItem {
  path?: string
  type?: string
  old?: string
  new?: string
  status: 'pending' | 'accepted' | 'rejected'
  [key: string]: unknown
}

const props = defineProps({
  patch: { type: Object as PropType<any>, default: null },
  applying: { type: Boolean, default: false },
  skillId: { type: String, default: '' },
})

const emit = defineEmits(['accept-all', 'reject-all', 'accept-hunk', 'reject-hunk', 'partial-applied'])

const applyingPartial = ref(false)

// 本地 hunk 状态管理（可独立操作每个 hunk）
const localHunks = ref<HunkItem[]>([])

watch(() => props.patch, (p) => {
  const raw = p?.diff_preview?.hunks
    || p?.diff_preview_json?.hunks
    || p?.hunks_json
    || []
  localHunks.value = raw.map((h: Record<string, unknown>) => ({
    ...h,
    status: (h.status as string) || 'pending',
  }))
}, { immediate: true, deep: true })

const acceptedCount = computed(() => localHunks.value.filter(h => h.status === 'accepted').length)
const hasPartialSelection = computed(() => {
  const accepted = acceptedCount.value
  return accepted > 0 && accepted < localHunks.value.length
})

const diffPreview = computed(() =>
  props.patch?.diff_preview || props.patch?.diff_preview_json || null
)

function setHunkStatus(index: number, status: 'pending' | 'accepted' | 'rejected') {
  localHunks.value[index].status = status
  if (status === 'accepted') {
    emit('accept-hunk', index)
  } else if (status === 'rejected') {
    emit('reject-hunk', index)
  }
}

async function handleApplySelected() {
  if (!props.skillId || !props.patch) return
  applyingPartial.value = true
  try {
    const acceptedIndices = localHunks.value
      .map((h, i) => h.status === 'accepted' ? i : -1)
      .filter(i => i >= 0)
    await workbenchApi.applyPartial(props.skillId, {
      patch_id: props.patch.id || props.patch.patch_id,
      target_module: props.patch.target_module,
      accepted_hunks: acceptedIndices,
      patch: props.patch.patch,
    })
    emit('partial-applied', acceptedIndices)
  } catch (err) {
    console.error('部分应用失败', err)
  } finally {
    applyingPartial.value = false
  }
}

function hunkTypeLabel(type: string) {
  const m: Record<string, string> = { modify: '修改', add: '新增', remove: '删除' }
  return m[type] || type || ''
}
</script>

<style scoped>
.inline-diff {
  border: 1px solid var(--ai-border); border-radius: 10px;
  background: var(--ai-surface-2); margin-bottom: 16px; overflow: hidden;
}

.diff-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px; border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
}
.diff-info { display: flex; align-items: center; gap: 8px; }
.diff-summary { font-size: 12px; color: var(--ai-ink-2); }
.diff-selection-info { font-size: 11px; color: var(--ai-ink-3); font-weight: 600; }
.diff-actions { display: flex; gap: 6px; }

/* Hunks */
.diff-hunks { padding: 8px; }
.diff-hunk {
  border: 1px solid var(--ai-border); border-radius: 8px;
  margin-bottom: 6px; overflow: hidden;
  transition: border-color .15s, opacity .15s;
}
.diff-hunk.accepted { border-color: var(--ai-ok); }
.diff-hunk.rejected { border-color: var(--ai-bad); opacity: .6; }

.hunk-header {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 10px; background: var(--ai-surface-2);
  font-size: 12px; color: var(--ai-ink-3);
}
.hunk-path { font-family: var(--ai-font-mono); font-weight: 500; color: var(--ai-ink-2); }
.hunk-type { font-size: 11px; }
.hunk-spacer { flex: 1; }
.hunk-btn {
  width: 22px; height: 22px; border-radius: 4px; border: none;
  display: flex; align-items: center; justify-content: center; cursor: pointer;
  transition: background .1s;
}
.hunk-btn.accept { background: var(--ai-ok-soft); color: var(--ai-ok); }
.hunk-btn.accept:hover { background: var(--ai-ok-soft); }
.hunk-btn.reject { background: var(--ai-bad-soft); color: var(--ai-bad); }
.hunk-btn.reject:hover { background: var(--ai-bad-soft); }
.hunk-btn.reset { background: var(--ai-surface-2); color: var(--ai-ink-3); }
.hunk-btn.reset:hover { background: var(--ai-surface-2); }

.hunk-body { padding: 4px 0; font-family: var(--ai-font-mono); font-size: 12px; transition: background .15s; }
.hunk-body-accepted { background: var(--ai-ok-soft); }
.hunk-body-rejected { background: var(--ai-bad-soft); }
.hunk-line { display: flex; padding: 2px 10px; }
.hunk-line.old { background: var(--ai-bad-soft); color: var(--ai-bad); }
.hunk-line.new { background: var(--ai-ok-soft); color: var(--ai-ok); }
.line-marker { width: 16px; flex-shrink: 0; font-weight: 600; }
.line-text { white-space: pre-wrap; word-break: break-all; }

/* Simple diff (no hunks) */
.diff-simple { display: flex; gap: 1px; }
.diff-col { flex: 1; }
.diff-col-title { font-size: 11px; font-weight: 600; color: var(--ai-ink-4); padding: 6px 10px; }
.diff-pre {
  margin: 0; padding: 8px 10px; font-size: 12px; line-height: 1.5;
  white-space: pre-wrap; word-break: break-all; min-height: 40px;
}
.diff-pre.old { background: var(--ai-bad-soft); }
.diff-pre.new { background: var(--ai-ok-soft); }
</style>
