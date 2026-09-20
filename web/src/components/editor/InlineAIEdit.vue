<template>
  <div v-if="visible" class="inline-ai-edit" :style="positionStyle">
    <div class="ai-header">
      <span>AI 编辑</span>
      <a-button type="text" size="mini" @click="close"><icon-close /></a-button>
    </div>

    <template v-if="!result">
      <div class="ai-input-row">
        <a-input
          ref="inputRef"
          v-model="instruction"
          placeholder="描述你想要的修改..."
          size="small"
          @keydown.enter="generate"
          @keydown.escape="close"
        />
        <a-button type="primary" size="small" :loading="loading" @click="generate">生成</a-button>
      </div>
    </template>

    <template v-else>
      <div class="ai-diff">
        <div class="diff-line diff-remove">- {{ selectedText }}</div>
        <div class="diff-line diff-add">+ {{ result.modified }}</div>
      </div>
      <div v-if="result.explanation" class="ai-explanation">{{ result.explanation }}</div>
      <div class="ai-actions">
        <a-button size="small" type="primary" @click="apply">应用</a-button>
        <a-button size="small" @click="result = null">重试</a-button>
        <a-button size="small" @click="close">放弃</a-button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick } from 'vue'
import { IconClose } from '@arco-design/web-vue/es/icon'
import { editorApi as rawEditorApi } from '@/api'
import { Message } from '@arco-design/web-vue'

const editorApi: any = rawEditorApi
const visible = ref(false)
const loading = ref(false)
const instruction = ref('')
const selectedText = ref('')
const result = ref<any>(null)
const positionStyle = ref<Record<string, string>>({})
const inputRef = ref<any>(null)

let _editor: any = null
let _selection: any = null
let _skillId = ''

async function open(editor: any, skillId: string) {
  _editor = editor
  _skillId = skillId
  const sel = editor.getSelection()
  if (!sel || sel.isEmpty()) {
    Message.warning('请先选中一段文本')
    return
  }
  _selection = sel
  selectedText.value = editor.getModel().getValueInRange(sel)
  instruction.value = ''
  result.value = null

  // 定位到选区上方
  const coords = editor.getScrolledVisiblePosition(sel.getStartPosition())
  if (coords) {
    positionStyle.value = {
      top: `${coords.top - 10}px`,
      left: `${Math.max(coords.left, 20)}px`,
    }
  }

  visible.value = true
  await nextTick()
  inputRef.value?.focus()
}

async function generate() {
  if (!instruction.value.trim()) return
  loading.value = true
  try {
    const context = _editor.getModel().getValue().substring(0, 2000)
    const res = await editorApi.inlineEdit({
      skill_id: _skillId,
      selected_text: selectedText.value,
      instruction: instruction.value,
      context,
    })
    result.value = res
  } catch (e: any) {
    Message.error('AI 生成失败')
  } finally {
    loading.value = false
  }
}

function apply() {
  if (!_editor || !_selection || !result.value) return
  _editor.executeEdits('inline-ai', [{
    range: _selection,
    text: result.value.modified,
  }])
  close()
}

function close() {
  visible.value = false
  result.value = null
  _selection = null
}

defineExpose({ open, close })
</script>

<style scoped>
.inline-ai-edit {
  position: absolute;
  z-index: 100;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  padding: 8px 12px;
  box-shadow: var(--ai-shadow-2);
  min-width: 360px;
  max-width: 500px;
}
.ai-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  font-weight: 600;
  color: var(--ai-ink-2);
  margin-bottom: 6px;
}
.ai-input-row { display: flex; gap: 8px; }
.ai-input-row :deep(.arco-input-wrapper) { flex: 1; }
.ai-diff {
  font-family: var(--ai-font-mono);
  font-size: 12px;
  background: var(--ai-surface);
  border-radius: 4px;
  padding: 6px 8px;
  margin: 6px 0;
  overflow-x: auto;
}
.diff-line { white-space: pre-wrap; }
.diff-remove { color: var(--ai-bad); }
.diff-add { color: var(--ai-ok); }
.ai-explanation { font-size: 11px; color: var(--ai-ink-3); margin-bottom: 6px; }
.ai-actions { display: flex; gap: 6px; justify-content: flex-end; }
</style>
