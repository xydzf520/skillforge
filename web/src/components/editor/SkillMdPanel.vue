<template>
  <div class="skill-md-panel">
    <div class="smp-tabs">
      <a-tabs v-model:active-key="activeTab" size="small" type="line">
        <a-tab-pane key="editor" title="Monaco 编辑">
          <MonacoEditor
            :model-value="modelValue"
            language="skill-md"
            :theme="theme"
            :read-only="readOnly"
            :enableAI="enableAI"
            :font-size="fontSize"
            filename="SKILL.md"
            :height="height"
            @update:modelValue="$emit('update:modelValue', $event)"
            @save="$emit('save')"
            @editorReady="onEditorReady"
          />
        </a-tab-pane>
        <a-tab-pane key="mermaid" title="决策树预览">
          <!-- v7 B1：Mermaid 决策树预览 -->
          <div class="smp-mermaid">
            <pre v-if="mermaidSource" class="smp-mermaid-source">{{ mermaidSource }}</pre>
            <div v-else class="smp-empty">
              当前 SKILL.md 未包含决策步骤，无法生成 Mermaid 图。
            </div>
            <div v-if="lineMap.length" class="smp-line-map">
              <div class="smp-line-map-title">行号映射</div>
              <div v-for="row in lineMap" :key="row.step" class="smp-line-row">
                <span class="smp-line-step">{{ row.step }}</span>
                <span class="smp-line-name">{{ row.name }}</span>
                <a-button size="mini" type="text" @click="jumpToLine(row.line)">L{{ row.line }}</a-button>
              </div>
            </div>
          </div>
        </a-tab-pane>
      </a-tabs>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import MonacoEditor from '@/components/editor/MonacoEditor.vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  readOnly: { type: Boolean, default: false },
  theme: { type: String, default: 'vs' },
  fontSize: { type: Number, default: 14 },
  height: { type: String, default: '100%' },
  enableAI: { type: Boolean, default: true },
})

const emit = defineEmits(['update:modelValue', 'save', 'editorReady'])

const activeTab = ref('editor')
const editorInstance = ref<any>(null)

function onEditorReady(editor: any) {
  editorInstance.value = editor
  emit('editorReady', editor)
}

// 解析 SKILL.md 决策步骤 → mermaid + 行号映射
const parsed = computed(() => {
  const text = props.modelValue || ''
  if (!text.trim()) return { mermaid: '', lineMap: [] }
  const lines = text.split('\n')
  const steps: any[] = []
  let currentStep: any = null
  lines.forEach((line, idx) => {
    const heading = line.match(/^###\s*(step_\d+):?\s*(.*)$/)
    if (heading) {
      if (currentStep) steps.push(currentStep)
      currentStep = { id: heading[1], name: heading[2] || heading[1], line: idx + 1, branches: [] }
      return
    }
    const branch = line.match(/^-\s*条件\s*[：:]\s*(.*)$/)
    if (branch && currentStep) {
      currentStep.branches.push({ condition: branch[1] })
    }
  })
  if (currentStep) steps.push(currentStep)

  if (!steps.length) return { mermaid: '', lineMap: [] }

  const mermaidLines = ['graph TD']
  steps.forEach((s, i) => {
    mermaidLines.push(`  ${s.id}["${s.id}: ${s.name}"]`)
    if (i < steps.length - 1) mermaidLines.push(`  ${s.id} --> ${steps[i + 1].id}`)
    s.branches.forEach((b: { condition: string }, bi: number) => {
      mermaidLines.push(`  ${s.id} -.${b.condition.slice(0, 20)}.-> end_${s.id}_${bi}["..."]`)
    })
  })

  return {
    mermaid: mermaidLines.join('\n'),
    lineMap: steps.map((s) => ({ step: s.id, name: s.name, line: s.line })),
  }
})

const mermaidSource = computed(() => parsed.value.mermaid)
const lineMap = computed(() => parsed.value.lineMap)

function jumpToLine(line: number) {
  if (!editorInstance.value) {
    activeTab.value = 'editor'
    return
  }
  activeTab.value = 'editor'
  setTimeout(() => {
    editorInstance.value.revealLineInCenter(line)
    editorInstance.value.setPosition({ lineNumber: line, column: 1 })
    editorInstance.value.focus()
  }, 50)
}
</script>

<style scoped>
.skill-md-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
/* 让 .smp-tabs 占满 .skill-md-panel 剩余空间，并把 column flex 一路传下去 */
.smp-tabs {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
/* Arco a-tabs 内部多层 div 默认 height: auto，要逐层补 100% 才能让 Monaco 撑满 */
.skill-md-panel :deep(.arco-tabs) {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.skill-md-panel :deep(.arco-tabs-content) {
  flex: 1 1 auto;
  min-height: 0;
  height: auto;
}
.skill-md-panel :deep(.arco-tabs-content-list) {
  height: 100%;
}
.skill-md-panel :deep(.arco-tabs-content-item) {
  height: 100%;
  display: flex;
  flex-direction: column;
}
/* a-tab-pane 渲染的 .arco-tabs-pane 默认 display:block + height:auto，会坍塌 */
.skill-md-panel :deep(.arco-tabs-pane) {
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
/* MonacoEditor 根容器需要 flex 拉伸（class 来自 MonacoEditor.vue 的 .monaco-editor-container） */
.skill-md-panel :deep(.monaco-editor-container) {
  flex: 1 1 auto;
  min-height: 0;
  /* 覆盖 inline style height:100%，让 flex 拉伸生效 */
  height: auto !important;
}
.smp-mermaid {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow-y: auto;
  max-height: 70vh;
}
.smp-mermaid-source {
  background: var(--ai-surface-2);
  padding: 12px;
  border-radius: 6px;
  font-size: 12px;
  font-family: var(--ai-font-mono);
  white-space: pre-wrap;
  border: 1px solid var(--ai-border);
}
.smp-empty {
  color: var(--ai-ink-3);
  font-size: 12px;
  padding: 16px 0;
  text-align: center;
}
.smp-line-map {
  border-top: 1px solid var(--ai-border);
  padding-top: 12px;
}
.smp-line-map-title {
  font-size: 12px;
  color: var(--ai-ink-2);
  font-weight: 600;
  margin-bottom: 8px;
}
.smp-line-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 12px;
}
.smp-line-step {
  font-family: var(--ai-font-mono);
  color: var(--ai-info);
  min-width: 50px;
}
.smp-line-name {
  flex: 1;
  color: var(--ai-ink-1);
}
</style>
