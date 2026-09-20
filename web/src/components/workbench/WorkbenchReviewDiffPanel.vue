<template>
  <div class="review-diff-panel">
    <div class="rdp-toolbar">
      <div class="rdp-title">变更详情</div>
      <div class="rdp-meta">
        <span>{{ lineCount }} 行</span>
        <span v-if="selectedLine">当前定位 L{{ selectedLine }}</span>
      </div>
      <div class="rdp-actions">
        <a-button
          size="mini"
          :type="showInlineDiff ? 'primary' : 'outline'"
          :loading="loadingDiff"
          @click="toggleInlineDiff"
        >
          <icon-code :size="12" style="margin-right:3px" />
          {{ showInlineDiff ? '隐藏 Diff' : '显示 Diff' }}
        </a-button>
      </div>
    </div>

    <!-- 内联 diff 预览 -->
    <div v-if="showInlineDiff && inlineDiffLines.length" class="rdp-inline-diff">
      <div class="rdp-inline-diff-header">
        <span class="rdp-inline-diff-title">当前内容 vs HEAD</span>
        <span class="rdp-inline-diff-stats">
          <span class="rdp-stat-add">+{{ addedLines }}</span>
          <span class="rdp-stat-del">-{{ removedLines }}</span>
        </span>
      </div>
      <div class="rdp-inline-diff-body">
        <div
          v-for="(line, i) in inlineDiffLines"
          :key="i"
          class="rdp-diff-line"
          :class="line.type"
        >
          <span class="rdp-diff-ln">{{ line.lineNo || '' }}</span>
          <span class="rdp-diff-marker">{{ line.type === 'add' ? '+' : line.type === 'del' ? '-' : ' ' }}</span>
          <span class="rdp-diff-text">{{ line.text }}</span>
        </div>
      </div>
    </div>

    <div v-if="showInlineDiff && !inlineDiffLines.length && !loadingDiff" class="rdp-no-diff">
      无差异 -- 当前内容与 HEAD 一致
    </div>

    <!-- Monaco 编辑器 -->
    <MonacoEditor
      ref="editorRef"
      :model-value="diffText"
      language="markdown"
      :theme="theme"
      :read-only="true"
      :enableAI="false"
      :height="height"
      @editorReady="handleEditorReady"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch, type PropType } from 'vue'
import { IconCode } from '@arco-design/web-vue/es/icon'
import MonacoEditor from '@/components/editor/MonacoEditor.vue'
import { skillApi } from '@/api'

interface DiffLine {
  type: 'add' | 'del' | 'ctx'
  text: string
  lineNo: string
}

const props = defineProps({
  diffText: { type: String, default: '' },
  comments: { type: Array as PropType<any[]>, default: () => [] },
  selectedLine: { type: Number as PropType<number | null>, default: null },
  theme: { type: String, default: 'vs' },
  height: { type: String, default: '360px' },
  skillId: { type: String, default: '' },
})

const emit = defineEmits(['line-select'])

const editorRef = ref<any>(null)
const editorInstance = ref<any>(null)
const monacoNs = ref<any>(null)
const decorationCollection = ref<any>(null)

// 内联 diff 状态
const showInlineDiff = ref(false)
const loadingDiff = ref(false)
const inlineDiffLines = ref<DiffLine[]>([])

const lineCount = computed(() => (props.diffText ? props.diffText.split('\n').length : 0))
const addedLines = computed(() => inlineDiffLines.value.filter(l => l.type === 'add').length)
const removedLines = computed(() => inlineDiffLines.value.filter(l => l.type === 'del').length)

async function toggleInlineDiff() {
  if (showInlineDiff.value) {
    showInlineDiff.value = false
    inlineDiffLines.value = []
    return
  }

  if (!props.skillId) return
  loadingDiff.value = true
  try {
    const res = await skillApi.diff(props.skillId, { target: 'working', base: 'HEAD' })
    const rawDiff = (res as any)?.data?.diff || (res as any)?.data?.content || ''
    inlineDiffLines.value = parseDiffText(rawDiff)
    showInlineDiff.value = true
  } catch (err) {
    console.error('获取 diff 失败', err)
    inlineDiffLines.value = []
    showInlineDiff.value = true
  } finally {
    loadingDiff.value = false
  }
}

function parseDiffText(raw: string): DiffLine[] {
  if (!raw) return []
  const lines = raw.split('\n')
  const result: DiffLine[] = []
  let lineNo = 0

  for (const line of lines) {
    // 跳过 diff 元数据行
    if (line.startsWith('diff ') || line.startsWith('index ') || line.startsWith('---') || line.startsWith('+++')) {
      continue
    }
    // 解析 hunk 头 @@ -a,b +c,d @@
    const hunkMatch = line.match(/^@@\s+-\d+(?:,\d+)?\s+\+(\d+)/)
    if (hunkMatch) {
      lineNo = parseInt(hunkMatch[1], 10) - 1
      result.push({ type: 'ctx', text: line, lineNo: '...' })
      continue
    }

    if (line.startsWith('+')) {
      lineNo++
      result.push({ type: 'add', text: line.slice(1), lineNo: String(lineNo) })
    } else if (line.startsWith('-')) {
      result.push({ type: 'del', text: line.slice(1), lineNo: '' })
    } else {
      lineNo++
      result.push({ type: 'ctx', text: line.startsWith(' ') ? line.slice(1) : line, lineNo: String(lineNo) })
    }
  }
  return result
}

function handleEditorReady(payload: { editor: any, monaco: any }) {
  editorInstance.value = payload.editor
  monacoNs.value = payload.monaco

  payload.editor.onMouseDown((e: any) => {
    if (e.target.type === payload.monaco.editor.MouseTargetType.GUTTER_LINE_NUMBERS && e.target.position?.lineNumber) {
      emit('line-select', e.target.position.lineNumber)
    }
  })

  applyCommentDecorations()
}

function applyCommentDecorations() {
  const editor = editorInstance.value
  const monaco = monacoNs.value
  if (!editor || !monaco) return

  const lineComments = (props.comments || []).filter((item: any) => item?.line_number)
  const decorations = lineComments.map((comment: any) => ({
    range: new monaco.Range(comment.line_number, 1, comment.line_number, 1),
    options: {
      isWholeLine: true,
      className: 'rdp-comment-line',
      glyphMarginClassName: 'rdp-comment-glyph',
      glyphMarginHoverMessage: { value: `**${comment.author || '评论'}**: ${comment.content || ''}` },
    },
  }))

  decorationCollection.value?.clear()
  decorationCollection.value = editor.createDecorationsCollection(decorations)
}

watch(() => props.comments, applyCommentDecorations, { deep: true })

watch(() => props.selectedLine, async (line) => {
  if (!line || !editorRef.value) return
  await nextTick()
  editorRef.value.setPosition(line, 1)
})
</script>

<style scoped>
.review-diff-panel {
  border: 1px solid var(--ai-border);
  border-radius: 14px;
  overflow: hidden;
  background: var(--ai-surface);
}
.rdp-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
}
.rdp-title {
  font-size: 13px;
  font-weight: 800;
  color: var(--ai-ink-1);
}
.rdp-meta {
  display: flex;
  gap: 10px;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.rdp-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

/* 内联 diff 预览 */
.rdp-inline-diff {
  border-bottom: 1px solid var(--ai-border);
}
.rdp-inline-diff-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 12px;
  background: var(--ai-surface-2);
  font-size: 12px;
}
.rdp-inline-diff-title { font-weight: 700; color: var(--ai-ink-1); }
.rdp-inline-diff-stats { display: flex; gap: 8px; }
.rdp-stat-add { color: var(--ai-ok); font-weight: 600; }
.rdp-stat-del { color: var(--ai-bad); font-weight: 600; }

.rdp-inline-diff-body {
  max-height: 280px;
  overflow-y: auto;
  font-family: var(--ai-font-mono);
  font-size: 12px;
  line-height: 1.6;
}
.rdp-diff-line {
  display: flex;
  padding: 0 12px;
}
.rdp-diff-line.add { background: var(--ai-ok-soft); }
.rdp-diff-line.del { background: var(--ai-bad-soft); }
.rdp-diff-ln {
  width: 36px; flex-shrink: 0; text-align: right;
  color: var(--ai-ink-4); padding-right: 8px; user-select: none;
}
.rdp-diff-marker {
  width: 14px; flex-shrink: 0; font-weight: 700;
  color: inherit;
}
.rdp-diff-line.add .rdp-diff-marker { color: var(--ai-ok); }
.rdp-diff-line.del .rdp-diff-marker { color: var(--ai-bad); }
.rdp-diff-text { flex: 1; white-space: pre-wrap; word-break: break-all; }

.rdp-no-diff {
  padding: 16px 12px;
  text-align: center;
  font-size: 12px;
  color: var(--ai-ink-3);
  border-bottom: 1px solid var(--ai-border);
}
</style>

<style>
.rdp-comment-line {
  background: rgba(49, 86, 163, 0.08);
}
.rdp-comment-glyph {
  background: var(--sf-brand-action);
  width: 8px !important;
  margin-left: 4px;
  border-radius: 999px;
}
</style>
