<template>
  <div ref="containerRef" class="monaco-editor-container" :style="{ height }"></div>
</template>

<script setup lang="ts">
/**
 * Monaco Editor Vue 3 封装组件
 *
 * Props:
 *   modelValue - 编辑器内容（v-model）
 *   language   - 语言模式（skill-md / yaml / python / json）
 *   theme      - 主题（skill-dark / vs-dark / vs）
 *   readOnly   - 是否只读
 *   height     - 容器高度
 *   options    - Monaco 额外配置
 *
 * Events:
 *   update:modelValue - 内容变更
 *   save              - Ctrl+S
 *   editorReady       - 编辑器实例与 monaco 命名空间就绪
 */
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import type { PropType } from 'vue'
import { monaco, ensureMonacoLanguageSupport } from './monaco-env'

// 注册自定义语言（仅首次）
import { registerSkillMdLanguage } from './skill-md-language'
import { registerSkillMdCompletions } from './skill-md-completions'
import { registerSkillMdTheme } from './skill-md-theme'
import { setupAICompletion, teardownAICompletion } from './ai-completion'
import { validateSkillMd } from './skill-md-validator'

let _registration: Promise<void> | null = null
function ensureRegistered() {
  if (_registration) return _registration
  _registration = Promise.resolve().then(() => {
    registerSkillMdLanguage(monaco)
    registerSkillMdCompletions(monaco)
    registerSkillMdTheme(monaco)
  })
  return _registration
}

const registrationReady = ensureRegistered()

const props = defineProps({
  modelValue: { type: String, default: '' },
  language: { type: String, default: 'skill-md' },
  theme: { type: String, default: 'skill-dark' },
  readOnly: { type: Boolean, default: false },
  height: { type: String, default: '600px' },
  filename: { type: String, default: 'SKILL.md' },
  enableAI: { type: Boolean, default: true },
  fontSize: { type: Number, default: 13 },
  options: { type: Object as PropType<any>, default: () => ({}) },
})

const emit = defineEmits(['update:modelValue', 'save', 'editorReady'])

const containerRef = ref<HTMLElement | null>(null)
let editor: any = null
let model: any = null
let resizeObserver: ResizeObserver | null = null
let aiCompletion: any = null
let validateTimer: ReturnType<typeof setTimeout> | null = null

// 避免 watch 循环：内部修改标记
let _ignoreChange = false

// 防抖校验（500ms）
function scheduleValidation() {
  if (props.language !== 'skill-md' || !editor) return
  if (validateTimer) clearTimeout(validateTimer)
  validateTimer = setTimeout(() => {
    const model = editor?.getModel()
    if (!model) return
    const markers = validateSkillMd(editor.getValue(), monaco)
    monaco.editor.setModelMarkers(model, 'skill-md', markers)
  }, 500)
}

onMounted(async () => {
  await Promise.all([
    registrationReady,
    ensureMonacoLanguageSupport(props.language),
  ])
  await nextTick()

  model = monaco.editor.createModel(props.modelValue, props.language)
  monaco.editor.setTheme(props.theme)

  editor = monaco.editor.create(containerRef.value, {
    model,
    theme: props.theme,
    readOnly: props.readOnly,
    automaticLayout: false,
    minimap: { enabled: true },
    fontSize: props.fontSize,
    fontFamily: "ui-monospace, monospace",
    fontLigatures: true,
    lineNumbers: 'on',
    renderWhitespace: 'selection',
    tabSize: 2,
    wordWrap: 'on',
    scrollBeyondLastLine: false,
    folding: true,
    bracketPairColorization: { enabled: true },
    suggest: { showWords: false },
    quickSuggestions: { other: true, strings: true, comments: false },
    padding: { top: 8 },
    ...props.options,
  })

  // 内容变更 → emit + 校验
  editor.onDidChangeModelContent(() => {
    if (_ignoreChange) return
    emit('update:modelValue', editor.getValue())
    scheduleValidation()
  })

  // 初始校验
  scheduleValidation()

  // Ctrl+S → save 事件
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
    emit('save')
  })

  // 自动 resize
  resizeObserver = new ResizeObserver(() => {
    editor?.layout()
  })
  if (containerRef.value) resizeObserver.observe(containerRef.value)

  // AI 补全（非只读模式下启用）
  if (props.enableAI && !props.readOnly) {
    aiCompletion = setupAICompletion(monaco, editor, {
      filename: props.filename,
      language: props.language,
    })
  }

  emit('editorReady', { editor, monaco })
})

// 外部 v-model 变更 → 更新编辑器 + 重新校验
watch(() => props.modelValue, (newVal) => {
  if (!editor) return
  const current = editor.getValue()
  if (newVal !== current) {
    const scrollTop = editor.getScrollTop()
    const scrollLeft = editor.getScrollLeft()
    const position = editor.getPosition()
    const selections = editor.getSelections()
    _ignoreChange = true
    editor.setValue(newVal)
    _ignoreChange = false
    const restoreViewport = () => {
      if (!editor) return
      if (position) editor.setPosition(position)
      if (selections) editor.setSelections(selections)
      editor.setScrollTop(scrollTop)
      editor.setScrollLeft(scrollLeft)
    }
    nextTick(() => {
      if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(restoreViewport)
      } else {
        restoreViewport()
      }
    })
    // 切文件后重新校验或清除旧 marker
    if (props.language === 'skill-md') {
      scheduleValidation()
    } else {
      const model = editor.getModel()
      if (model) monaco.editor.setModelMarkers(model, 'skill-md', [])
    }
  }
})

// 语言切换
watch(() => props.language, async (lang) => {
  if (!editor) return
  await ensureMonacoLanguageSupport(lang)
  const model = editor.getModel()
  if (model) {
    monaco.editor.setModelLanguage(model, lang)
  }
})

// 主题切换
watch(() => props.theme, (theme) => {
  monaco.editor.setTheme(theme)
})

// 只读切换
watch(() => props.fontSize, (fontSize) => {
  editor?.updateOptions({ fontSize })
})

watch(() => props.readOnly, (readOnly) => {
  editor?.updateOptions({ readOnly })
  // 切换到编辑模式时启动 AI 补全，切换到只读时关闭
  if (!readOnly && props.enableAI && !aiCompletion && editor) {
    aiCompletion = setupAICompletion(monaco, editor, {
      filename: props.filename,
      language: props.language,
    })
  } else if (readOnly && aiCompletion) {
    teardownAICompletion(aiCompletion)
    aiCompletion = null
  }
})

onBeforeUnmount(() => {
  if (validateTimer) clearTimeout(validateTimer)
  teardownAICompletion(aiCompletion)
  aiCompletion = null
  resizeObserver?.disconnect()
  const currentModel = model || editor?.getModel()
  editor?.dispose()
  editor = null
  if (currentModel && !currentModel.isDisposed()) {
    currentModel.dispose()
  }
  model = null
})

// 暴露编辑器实例供父组件调用
defineExpose({
  getEditor: () => editor,
  focus: () => editor?.focus(),
  setPosition: (line: number, column: number) => {
    editor?.setPosition({ lineNumber: line, column })
    editor?.revealLineInCenter(line)
  },
  getModel: () => editor?.getModel(),
})
</script>

<style scoped>
.monaco-editor-container {
  box-sizing: border-box;
  width: 100%;
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  overflow: hidden;
}
</style>
