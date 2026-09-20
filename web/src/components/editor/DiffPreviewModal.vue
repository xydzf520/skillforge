<template>
  <a-modal
    :visible="visible"
    title="保存前预览变更"
    width="90vw"
    :body-style="{ padding: 0, height: '70vh' }"
    ok-text="确认保存"
    cancel-text="继续编辑"
    @ok="$emit('confirm')"
    @cancel="$emit('cancel')"
    @update:visible="v => $emit('update:visible', v)"
  >
    <div class="diff-header">
      <span class="diff-file">{{ fileName }}</span>
      <span v-if="commitHash" class="diff-commit">HEAD: {{ commitHash }}</span>
      <a-tag v-if="isNew" color="green" size="small">新文件</a-tag>
    </div>
    <div ref="diffContainerRef" class="diff-container"></div>
  </a-modal>
</template>

<script setup lang="ts">
import { ref, watch, nextTick, onBeforeUnmount } from 'vue'
import type { PropType } from 'vue'
import * as monaco from 'monaco-editor'

const props = defineProps({
  visible: { type: Boolean, default: false },
  originalContent: { type: String, default: '' },
  modifiedContent: { type: String, default: '' },
  language: { type: String, default: 'markdown' },
  fileName: { type: String, default: '' },
  commitHash: { type: String, default: '' },
  isNew: { type: Boolean, default: false },
})

const emit = defineEmits(['update:visible', 'confirm', 'cancel'])

const diffContainerRef = ref<HTMLElement | null>(null)
let diffEditor: any = null

watch(() => props.visible, async (val) => {
  if (val) {
    await nextTick()
    // 延迟确保 DOM 已渲染
    await new Promise((r) => setTimeout(r, 100))
    createDiffEditor()
  } else {
    disposeDiffEditor()
  }
})

function createDiffEditor() {
  if (!diffContainerRef.value) return
  disposeDiffEditor()

  const originalModel = monaco.editor.createModel(props.originalContent, props.language)
  const modifiedModel = monaco.editor.createModel(props.modifiedContent, props.language)

  diffEditor = monaco.editor.createDiffEditor(diffContainerRef.value, {
    readOnly: true,
    renderSideBySide: true,
    originalEditable: false,
    automaticLayout: true,
    enableSplitViewResizing: true,
    renderIndicators: true,
    renderMarginRevertIcon: false,
    minimap: { enabled: false },
    theme: 'vs-dark',
  })

  diffEditor.setModel({ original: originalModel, modified: modifiedModel })
}

function disposeDiffEditor() {
  if (diffEditor) {
    const model = diffEditor.getModel()
    model?.original?.dispose()
    model?.modified?.dispose()
    diffEditor.dispose()
    diffEditor = null
  }
}

onBeforeUnmount(() => disposeDiffEditor())
</script>

<style scoped>
.diff-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  background: var(--ai-surface-2);
  border-bottom: 1px solid var(--ai-border);
  font-size: 13px;
}
.diff-file { font-weight: 600; }
.diff-commit { color: var(--ai-ink-3); font-family: var(--ai-font-mono); }
.diff-container { height: calc(70vh - 40px); }
</style>
