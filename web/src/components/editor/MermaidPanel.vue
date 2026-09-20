<template>
  <div class="mermaid-panel">
    <!-- 拖拽调整宽度（可选：父容器若控制宽度则不生效）-->
    <div class="mermaid-resize-bar" @mousedown="startResize"></div>

    <div class="mermaid-toolbar">
      <div class="toolbar-left">
        <icon-mind-mapping class="toolbar-icon" />
        <span class="panel-title">决策树</span>
      </div>
      <div class="toolbar-right">
        <div class="zoom-controls">
          <a-button size="mini" class="zoom-btn" @click="zoomOut" :disabled="scale <= 0.3">
            <icon-minus />
          </a-button>
          <span class="zoom-level">{{ Math.round(scale * 100) }}%</span>
          <a-button size="mini" class="zoom-btn" @click="zoomIn" :disabled="scale >= 3">
            <icon-plus />
          </a-button>
          <a-button size="mini" class="zoom-btn" @click="fitView" title="适应视图">
            <icon-fullscreen />
          </a-button>
        </div>
        <a-button size="mini" class="zoom-btn close-btn" @click="$emit('close')">
          <icon-close />
        </a-button>
      </div>
    </div>

    <!-- 统计栏 -->
    <div class="mermaid-stats" v-if="stepCount">
      <div class="stat-item">
        <icon-flow-one class="stat-icon" />
        <span class="stat-value">{{ stepCount }}</span>
        <span class="stat-label">步骤</span>
      </div>
      <div class="stat-divider"></div>
      <div class="stat-item">
        <icon-branch class="stat-icon" />
        <span class="stat-value">{{ branchCount }}</span>
        <span class="stat-label">分支</span>
      </div>
    </div>

    <!-- 图表内容 -->
    <div class="mermaid-content" ref="containerRef" @wheel.prevent="onWheel" @mousedown="startPan">
      <div v-if="loading" class="mermaid-placeholder">
        <a-spin size="24" />
        <span>加载决策树...</span>
      </div>
      <div v-else-if="error" class="mermaid-placeholder error">
        <icon-exclamation-circle-fill class="placeholder-icon" />
        <span>{{ error }}</span>
        <a-button size="mini" type="text" @click="loadMermaid">重试</a-button>
      </div>
      <div v-else-if="!svgHtml" class="mermaid-placeholder empty">
        <icon-mind-mapping class="placeholder-icon" />
        <span>暂无决策步骤</span>
      </div>
      <div v-else ref="svgRef" class="mermaid-svg" :style="svgTransform" v-html="svgHtml"></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import type { PropType } from 'vue'
import mermaid from 'mermaid'
import DOMPurify from 'dompurify'
import { skillApi as rawSkillApi } from '@/api'
import {
  IconMinus, IconPlus, IconFullscreen, IconClose,
  IconMindMapping, IconBranch, IconExclamationCircleFill,
} from '@arco-design/web-vue/es/icon'
// IconFlowOne 不存在于 Arco，用 slot 替代
const IconFlowOne = IconMindMapping
const skillApi: any = rawSkillApi

const props = defineProps({
  skillId: { type: String, required: true },
  cursorLine: { type: Number, default: 1 },
  panelWidth: { type: Number, default: 360 },
})

const emit = defineEmits(['jump-to-line', 'close', 'update:panelWidth'])

const containerRef = ref<HTMLElement | null>(null)
const svgRef = ref<HTMLElement | null>(null)
const svgHtml = ref('')
const loading = ref(false)
const error = ref('')
const stepCount = ref(0)
const branchCount = ref(0)
const lineMap = ref<Record<string, number>>({})
const panelWidth = ref(props.panelWidth)

// 缩放 & 平移
const scale = ref(1)
const translateX = ref(0)
const translateY = ref(0)
const svgTransform = ref({})

function updateTransform() {
  svgTransform.value = {
    transform: `translate(${translateX.value}px, ${translateY.value}px) scale(${scale.value})`,
    transformOrigin: '0 0',
  }
}

function zoomIn() { scale.value = Math.min(scale.value + 0.15, 3); updateTransform() }
function zoomOut() { scale.value = Math.max(scale.value - 0.15, 0.3); updateTransform() }
function fitView() { scale.value = 1; translateX.value = 0; translateY.value = 0; updateTransform() }

function onWheel(e: WheelEvent) {
  if (e.ctrlKey || e.metaKey) {
    scale.value = Math.max(0.3, Math.min(3, scale.value - e.deltaY * 0.001))
  } else {
    translateX.value -= e.deltaX
    translateY.value -= e.deltaY
  }
  updateTransform()
}

// 向上遍历 DOM 查找 .node 祖先（兼容 foreignObject 跨命名空间）
function findNodeAncestor(el: any) {
  let cur = el
  while (cur && cur !== containerRef.value) {
    if (cur.classList?.contains('node')) return cur
    cur = cur.parentNode || cur.parentElement
  }
  return null
}

// 所有拖拽用 AbortController 统一收口，组件卸载或 mouseup 丢失都能保证监听被清理
const _dragCtrls: AbortController[] = []
function _makeDragCtrl(): AbortController {
  const ctrl = new AbortController()
  ctrl.signal.addEventListener('abort', () => {
    const idx = _dragCtrls.indexOf(ctrl)
    if (idx >= 0) _dragCtrls.splice(idx, 1)
  })
  _dragCtrls.push(ctrl)
  return ctrl
}
onBeforeUnmount(() => _dragCtrls.splice(0).forEach((c) => c.abort()))

// 鼠标拖拽平移（只在空白区触发，节点区不触发）
function startPan(e: MouseEvent) {
  if (e.button !== 0) return
  if (findNodeAncestor(e.target)) return
  const startX = e.clientX, startY = e.clientY
  const origTX = translateX.value, origTY = translateY.value
  const ctrl = _makeDragCtrl()
  function onMove(ev: MouseEvent) {
    translateX.value = origTX + (ev.clientX - startX)
    translateY.value = origTY + (ev.clientY - startY)
    updateTransform()
  }
  function onUp() { ctrl.abort() }
  document.addEventListener('mousemove', onMove, { signal: ctrl.signal })
  document.addEventListener('mouseup', onUp, { signal: ctrl.signal })
}

// 拖拽调整宽度
function startResize(e: MouseEvent) {
  const startX = e.clientX
  const startW = panelWidth.value
  const ctrl = _makeDragCtrl()
  function onMove(ev: MouseEvent) {
    panelWidth.value = Math.max(260, Math.min(600, startW - (ev.clientX - startX)))
    emit('update:panelWidth', panelWidth.value)
  }
  function onUp() { ctrl.abort() }
  document.addEventListener('mousemove', onMove, { signal: ctrl.signal })
  document.addEventListener('mouseup', onUp, { signal: ctrl.signal })
}

// 初始化 Mermaid（对齐 SkillForge 设计系统）
mermaid.initialize({
  startOnLoad: false,
  theme: 'base',
  themeVariables: {
    primaryColor: '#165DFF',
    primaryBorderColor: '#0E42D2',
    primaryTextColor: '#fff',
    lineColor: '#C9CDD4',
    textColor: '#1D2129',
    mainBkg: '#F7F8FA',
    nodeBorder: '#C9CDD4',
    fontFamily: "system-ui, sans-serif",
    fontSize: '13px',
  },
  flowchart: {
    useMaxWidth: false,
    htmlLabels: false,   // 纯 SVG 文本，保证节点 ID 和点击事件正常
    curve: 'basis',
    padding: 16,
    nodeSpacing: 40,
    rankSpacing: 50,
  },
  securityLevel: 'loose',
})

async function loadMermaid() {
  if (!props.skillId) return
  loading.value = true
  error.value = ''
  try {
    const data = await skillApi.mermaid(props.skillId)
    stepCount.value = data.step_count || 0
    branchCount.value = data.branch_count || 0
    lineMap.value = data.line_map || {}

    if (!data.mermaid) {
      svgHtml.value = ''
      return
    }

    // 渲染 Mermaid SVG
    const id = `mermaid-${Date.now()}`
    const { svg } = await mermaid.render(id, data.mermaid)
    // 保留 id / class / data-* 属性
    svgHtml.value = DOMPurify.sanitize(svg, {
      USE_PROFILES: { svg: true, svgFilters: true },
      ADD_TAGS: ['foreignObject', 'style'],
      ADD_ATTR: ['xmlns', 'requiredExtensions', 'id', 'class', 'data-id', 'data-node'],
    })
  } catch (e: any) {
    error.value = e._message || '加载失败'
  } finally {
    loading.value = false
    // 等 v-else 条件渲染完成后再绑定点击
    await nextTick()
    if (svgHtml.value) setupClickDelegation()
  }
}

// ── 从节点提取 step_id ──
// Mermaid v11 实际 ID 格式: mermaid-{timestamp}-flowchart-step_1_b0-{index}
// 直接从 ID 中搜索 step_\d+ 模式即可覆盖步骤/条件/结论三种节点

function extractStepId(nodeEl: any) {
  // 策略1：从 id 属性提取（step_1_b0 / step_1_c2 → step_1）
  const idMatch = nodeEl.id?.match(/step_(\d+)/)
  if (idMatch) return `step_${idMatch[1]}`
  // 策略2：从文本内容提取（"step_1: 检查ROI" → step_1）
  const textMatch = (nodeEl.textContent || '').match(/\bstep_(\d+)\b/)
  if (textMatch) return `step_${textMatch[1]}`
  return null
}

// 事件委托：在 SVG 容器上监听 click，避免 foreignObject 事件冒泡问题
function setupClickDelegation() {
  if (!svgRef.value) return
  svgRef.value.addEventListener('click', (e) => {
    const nodeEl = findNodeAncestor(e.target)
    if (!nodeEl) return
    const stepId = extractStepId(nodeEl)
    const line = stepId ? lineMap.value[stepId] : null
    if (stepId && line) emit('jump-to-line', line)
  })
  svgRef.value.querySelectorAll('.node').forEach((n) => { (n as HTMLElement).style.cursor = 'pointer' })
}

// 光标行 → 高亮对应步骤的所有节点
watch(() => props.cursorLine, (line) => {
  if (!svgRef.value || !lineMap.value) return
  let activeStep = null
  for (const [stepId, stepLine] of Object.entries(lineMap.value)) {
    if (line >= stepLine) activeStep = stepId
  }
  svgRef.value.querySelectorAll('.node').forEach(n => {
    const belongs = activeStep && extractStepId(n) === activeStep
    n.classList.toggle('mermaid-active', !!belongs)
  })
})

onMounted(() => { loadMermaid(); updateTransform() })
watch(() => props.skillId, loadMermaid)
</script>

<style scoped>
.mermaid-panel {
  display: flex;
  flex-direction: column;
  /* 占满父容器（viz-panel 通常是 column flex + 固定宽度），高度由父布局给 */
  flex: 1 1 auto;
  width: 100%;
  height: 100%;
  min-height: 0;
  background: var(--ai-surface-2);
  overflow: hidden;
  position: relative;
}

/* 拖拽调整宽度 */
.mermaid-resize-bar {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  cursor: ew-resize;
  z-index: 10;
  transition: background 0.15s;
}
.mermaid-resize-bar:hover {
  background: var(--ai-accent-ink);
}

/* 工具栏 */
.mermaid-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 12px;
  border-bottom: 1px solid var(--ai-border);
  flex-shrink: 0;
}
.toolbar-left {
  display: flex;
  align-items: center;
  gap: 6px;
}
.toolbar-icon {
  font-size: 14px;
  color: var(--ai-accent-ink);
}
.panel-title {
  font-size: var(--sf-text-caption, 12px);
  font-weight: 600;
  color: var(--ai-ink-1);
}
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 6px;
}
.zoom-controls {
  display: flex;
  align-items: center;
  gap: 2px;
  background: var(--ai-surface-2);
  border-radius: 4px;
  padding: 2px;
}
.zoom-btn {
  width: 24px !important;
  height: 24px !important;
  padding: 0 !important;
  border: none !important;
  background: transparent !important;
  color: var(--ai-ink-2) !important;
  border-radius: 4px !important;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
}
.zoom-btn:hover {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-1) !important;
}
.zoom-btn:disabled {
  opacity: 0.35;
}
.zoom-level {
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-2);
  min-width: 36px;
  text-align: center;
  user-select: none;
}
.close-btn {
  background: transparent !important;
  margin-left: 2px;
}

/* 统计栏 */
.mermaid-stats {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--ai-border);
  flex-shrink: 0;
}
.stat-item {
  display: flex;
  align-items: center;
  gap: 5px;
}
.stat-icon {
  font-size: 13px;
  color: var(--ai-accent-ink);
  opacity: 0.7;
}
.stat-value {
  font-size: 14px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.stat-label {
  font-size: var(--sf-text-tiny, 11px);
  color: var(--ai-ink-3);
}
.stat-divider {
  width: 1px;
  height: 16px;
  background: var(--ai-border);
}

/* 图表内容区 */
.mermaid-content {
  flex: 1;
  overflow: hidden;
  position: relative;
  background: var(--ai-surface);
  cursor: grab;
}
.mermaid-content:active {
  cursor: grabbing;
}
.mermaid-svg {
  position: absolute;
  top: 12px;
  left: 12px;
  padding: 12px;
}

/* 占位状态 */
.mermaid-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 10px;
  color: var(--ai-ink-3);
  font-size: var(--sf-text-body, 13px);
  user-select: none;
}
.mermaid-placeholder .placeholder-icon {
  font-size: 32px;
  opacity: 0.3;
}
.mermaid-placeholder.error .placeholder-icon {
  color: var(--ai-bad);
  opacity: 0.6;
}
</style>

<style>
/* ══ Mermaid 流程图全局样式 ══ */

/* 节点交互 */
.mermaid-svg .node {
  cursor: pointer;
  transition: filter 0.15s, opacity 0.15s;
}
.mermaid-svg .node:hover {
  filter: brightness(1.06) drop-shadow(0 2px 6px rgba(0,0,0,0.1));
}

/* 连线样式 */
.mermaid-svg .flowchart-link {
  stroke: #C9CDD4 !important;
  stroke-width: 1.5px !important;
}
.mermaid-svg .marker {
  fill: #C9CDD4 !important;
  stroke: #C9CDD4 !important;
}
/* 连线标签 */
.mermaid-svg .edgeLabel {
  font-size: 11px !important;
}
.mermaid-svg .edgeLabel .edgeLabel {
  background: var(--color-bg-1, #fff) !important;
  padding: 2px 6px !important;
  border-radius: 3px !important;
}

/* 活动节点高亮 */
.mermaid-active rect,
.mermaid-active polygon,
.mermaid-active .label-container {
  stroke: #165DFF !important;
  stroke-width: 2.5px !important;
  filter: drop-shadow(0 0 6px rgba(22, 93, 255, 0.3));
}

/* ══ 编辑器暗色模式 ══ */
.editor-dark .mermaid-panel {
  background: #252526;
  border-left-color: #333;
}
.editor-dark .mermaid-toolbar {
  border-bottom-color: #333;
}
.editor-dark .panel-title {
  color: #e0e0e0;
}
.editor-dark .toolbar-icon {
  color: #4fc1ff;
}
.editor-dark .zoom-controls {
  background: #333;
}
.editor-dark .zoom-btn {
  color: #999 !important;
}
.editor-dark .zoom-btn:hover {
  background: #444 !important;
  color: #e0e0e0 !important;
}
.editor-dark .zoom-level {
  color: #999;
}
.editor-dark .mermaid-stats {
  border-bottom-color: #333;
}
.editor-dark .stat-icon {
  color: #4fc1ff;
}
.editor-dark .stat-value {
  color: #e0e0e0;
}
.editor-dark .stat-label {
  color: #888;
}
.editor-dark .stat-divider {
  background: #444;
}
.editor-dark .mermaid-content {
  background: #1e1e1e;
}
.editor-dark .mermaid-placeholder {
  color: #888;
}
.editor-dark .mermaid-resize-bar:hover {
  background: #4fc1ff;
}

/* 暗色 SVG：连线 */
.editor-dark .mermaid-svg .flowchart-link {
  stroke: #555 !important;
}
.editor-dark .mermaid-svg .marker {
  fill: #555 !important;
  stroke: #555 !important;
}
/* 暗色 SVG：条件节点文字（深色填充的不改，浅色填充的改白） */
.editor-dark .mermaid-svg .node.condNode text,
.editor-dark .mermaid-svg .node.condNode tspan {
  fill: #e0e0e0 !important;
}
.editor-dark .mermaid-svg .node.condNode polygon {
  fill: #333 !important;
  stroke: #555 !important;
}
/* 暗色 SVG：连线标签 */
.editor-dark .mermaid-svg .edgeLabel .edgeLabel {
  background: #1e1e1e !important;
  color: #bbb !important;
}
.editor-dark .mermaid-svg .edgeLabel span {
  color: #bbb !important;
}
/* 暗色 SVG：步骤节点（保持主色但调暗） */
.editor-dark .mermaid-svg .node.stepNode rect {
  fill: #1A4FCC !important;
  stroke: #3370FF !important;
}
/* 暗色 SVG：结论节点语义色调暗 */
.editor-dark .mermaid-svg .node.green rect { fill: #1A7A2E !important; stroke: #27AE3B !important; }
.editor-dark .mermaid-svg .node.yellow rect { fill: #B85C00 !important; stroke: #E67700 !important; }
.editor-dark .mermaid-svg .node.red rect { fill: #B72630 !important; stroke: #E63F3F !important; }
.editor-dark .mermaid-svg .node.defaultConc rect { fill: #1A3360 !important; stroke: #3370FF !important; }
.editor-dark .mermaid-svg .node.defaultConc text,
.editor-dark .mermaid-svg .node.defaultConc tspan { fill: #8ABAFF !important; }
/* 暗色 SVG：活动节点高亮 */
.editor-dark .mermaid-active rect,
.editor-dark .mermaid-active polygon {
  stroke: #4fc1ff !important;
  filter: drop-shadow(0 0 6px rgba(79, 193, 255, 0.4));
}

/* ══ 编辑器亮色模式 ══ */
.editor-light .mermaid-panel {
  background: #f8f8f8;
  border-left-color: #ddd;
}
.editor-light .mermaid-toolbar {
  border-bottom-color: #ddd;
}
.editor-light .mermaid-stats {
  border-bottom-color: #ddd;
}
.editor-light .stat-divider {
  background: #ddd;
}
.editor-light .mermaid-content {
  background: #fff;
}
.editor-light .mermaid-resize-bar:hover {
  background: #005fb8;
}
</style>
