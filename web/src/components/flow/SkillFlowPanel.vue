<template>
  <div class="skill-flow-panel">
   <div class="skill-flow-main">
    <!-- 工具栏 -->
    <div class="flow-toolbar">
      <div class="toolbar-left">
        <a-button-group size="mini">
          <a-tooltip content="适应画布" mini><a-button @click="fitView"><icon-expand /></a-button></a-tooltip>
          <a-tooltip content="自动布局" mini><a-button @click="doAutoLayout"><icon-mind-mapping /></a-button></a-tooltip>
          <a-tooltip content="放大" mini><a-button @click="zoomIn"><icon-plus /></a-button></a-tooltip>
          <a-tooltip content="缩小" mini><a-button @click="zoomOut"><icon-minus /></a-button></a-tooltip>
        </a-button-group>
        <a-button size="mini" type="outline" @click="addStep"><icon-plus /> 添加步骤</a-button>
        <a-tooltip content="导出 SVG" mini>
          <a-button size="mini" @click="exportImage"><icon-download /></a-button>
        </a-tooltip>
        <a-tooltip content="执行轨迹" mini>
          <a-button size="mini" :type="showTrace ? 'primary' : undefined" @click="showTrace = !showTrace"><icon-history /></a-button>
        </a-tooltip>
      </div>
      <div class="flow-info">
        <span>{{ nodes.length }} 节点</span>
        <span>·</span>
        <span>{{ edges.length }} 连线</span>
      </div>
    </div>

    <!-- 执行轨迹选择器（可折叠）-->
    <ExecutionOverlay v-if="showTrace" :skill-id="skillId" :nodes="nodes" :edges="edges" @highlight="onTraceHighlight" />

    <!-- Vue Flow 画布 -->
    <div class="flow-canvas" ref="canvasRef">
      <VueFlow
        :nodes="nodes"
        :edges="edges"
        :node-types="nodeTypes"
        :edge-types="edgeTypes"
        :default-viewport="{ zoom: 0.8, x: 50, y: 50 }"
        :default-edge-options="{ type: 'condition', animated: false, markerEnd: 'arrowclosed' }"
        :min-zoom="0.15"
        :max-zoom="2.5"
        :snap-to-grid="true"
        :snap-grid="[10, 10]"
        fit-view-on-init
        @node-click="onNodeClick"
      >
        <Background :gap="16" :size="1" pattern-color="#e5e7eb" />
        <MiniMap :position="miniMapPosition" :node-color="miniMapColor" />
      </VueFlow>
    </div>

    <!-- 加载状态 -->
    <div v-if="loading" class="flow-loading">
      <a-spin />
      <span style="color: #667085;">加载流程图...</span>
    </div>
    <!-- 空状态 -->
    <div v-if="!loading && nodes.length === 0" class="flow-empty">
      <a-empty description="该 Skill 无决策步骤，无法生成流程图" />
    </div>
   </div>

    <!-- 右侧属性编辑面板 -->
    <NodePropertyPanel
      v-if="selectedNode"
      :node="selectedNode"
      :all-steps="allStepIds"
      @change="onNodePropChange"
      @close="selectedNode = null"
      @view-script="emit('view-script', $event)"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, markRaw } from 'vue'
import type { PropType } from 'vue'
import { VueFlow, useVueFlow } from '@vue-flow/core'
import { Background, MiniMap, Controls } from '@vue-flow/additional-components'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import { IconExpand, IconMindMapping, IconPlus, IconMinus, IconDownload, IconHistory } from '@arco-design/web-vue/es/icon'

import DataSourceNode from './nodes/DataSourceNode.vue'
import DecisionStepNode from './nodes/DecisionStepNode.vue'
import ConclusionNode from './nodes/ConclusionNode.vue'
import ScriptNode from './nodes/ScriptNode.vue'
import ParamNode from './nodes/ParamNode.vue'
import OutputNode from './nodes/OutputNode.vue'
import ApprovalNode from './nodes/ApprovalNode.vue'
import ConditionEdge from './edges/ConditionEdge.vue'
import './nodes/nodeStyles.css'

import NodePropertyPanel from './panels/NodePropertyPanel.vue'
import ExecutionOverlay from './panels/ExecutionOverlay.vue'
import { skillToFlow } from './utils/skillToFlow'
import { flowToSkill } from './utils/flowToSkill'
import { autoLayout } from './utils/layoutEngine'
import request from '@/api/request'

type FlowNode = {
  id: string
  type?: string
  position: { x: number; y: number }
  data?: Record<string, unknown>
  class?: string
  [key: string]: unknown
}

type FlowEdge = {
  id: string
  source: string
  target: string
  type?: string
  data?: Record<string, unknown>
  [key: string]: unknown
}

const props = defineProps({
  skillId: { type: String, required: true },
})

const emit = defineEmits(['node-click', 'node-change', 'view-script', 'md-update'])

const selectedNode = ref<FlowNode | null>(null)
const showTrace = ref(false)

const colorMap: Record<string, string> = { dataSource: '#7ec8e3', decisionStep: '#667085', conclusion: '#12b76a', script: '#f97316', param: '#eab308', output: '#3a5bd9', approval: '#a855f7' }
function miniMapColor(node: { type?: string }) {
  return colorMap[node.type || ''] || '#98a2b3'
}
const allStepIds = ref<string[]>([])
let flowDataCache: Record<string, unknown> | null = null

// H2: 保存版本号，防竞态
let _saveVersion = 0

// M1: 撤销/重做 — 同时保存 nodes + flowDataCache 快照
const undoStack = ref<string[]>([])
const redoStack = ref<string[]>([])
function pushUndo() {
  undoStack.value.push(JSON.stringify({ nodes: nodes.value, cache: flowDataCache }))
  if (undoStack.value.length > 50) undoStack.value.shift()
  redoStack.value = []
}
function undo() {
  if (!undoStack.value.length) return
  redoStack.value.push(JSON.stringify({ nodes: nodes.value, cache: flowDataCache }))
  const snapshot = JSON.parse(undoStack.value.pop()!)
  nodes.value = snapshot.nodes
  flowDataCache = snapshot.cache
}
function redo() {
  if (!redoStack.value.length) return
  undoStack.value.push(JSON.stringify({ nodes: nodes.value, cache: flowDataCache }))
  const snapshot = JSON.parse(redoStack.value.pop()!)
  nodes.value = snapshot.nodes
  flowDataCache = snapshot.cache
}

const nodeTypes: any = {
  dataSource: markRaw(DataSourceNode),
  decisionStep: markRaw(DecisionStepNode),
  conclusion: markRaw(ConclusionNode),
  script: markRaw(ScriptNode),
  param: markRaw(ParamNode),
  output: markRaw(OutputNode),
  approval: markRaw(ApprovalNode),
}

const edgeTypes: any = {
  condition: markRaw(ConditionEdge),
}
const miniMapPosition: any = 'bottom-right'

const nodes = ref<FlowNode[]>([])
const edges = ref<FlowEdge[]>([])
const loading = ref(false)
const canvasRef = ref<HTMLElement | null>(null)

const { fitView: vfFitView, zoomIn: vfZoomIn, zoomOut: vfZoomOut } = useVueFlow()

async function loadFlowData() {
  loading.value = true
  try {
    const data = await request.get(`/skills/${props.skillId}/flow-data`) as Record<string, unknown>
    flowDataCache = data
    allStepIds.value = ((data.steps || []) as Array<{ id: string }>).map(s => s.id)
    const { nodes: n, edges: e } = skillToFlow(data)
    nodes.value = n as FlowNode[]
    edges.value = e as FlowEdge[]
  } catch (e) {
    console.error('加载流程图数据失败', e)
  } finally {
    loading.value = false
  }
}

function fitView() { vfFitView({ padding: 0.2, duration: 300 }) }
function zoomIn() { vfZoomIn() }
function zoomOut() { vfZoomOut() }

function doAutoLayout() {
  const { nodes: layouted } = autoLayout(nodes.value as FlowNode[], edges.value as FlowEdge[], { direction: 'LR', rankSep: 100, nodeSep: 50 })
  nodes.value = layouted as FlowNode[]
  setTimeout(() => fitView(), 100)
}

function onNodeClick(event: any) {
  const node = (event.node || event) as FlowNode
  selectedNode.value = node
  emit('node-click', node)
}

let _syncTimer: ReturnType<typeof setTimeout> | null = null
function onNodePropChange({ nodeId, type, data }: { nodeId: string; type: string; data: Record<string, unknown> }) {
  pushUndo()
  const idx = nodes.value.findIndex(n => n.id === nodeId)
  if (idx >= 0) {
    nodes.value[idx] = { ...nodes.value[idx], data: { ...nodes.value[idx].data, ...data } }
  }
  emit('node-change', { nodeId, type, data })

  // H2: debounce + 版本号防竞态
  if (_syncTimer) clearTimeout(_syncTimer)
  const version = ++_saveVersion
  _syncTimer = setTimeout(async () => {
    if (!flowDataCache) return
    try {
      const { ast, policy_pack } = flowToSkill(nodes.value, flowDataCache)
      // B2: SKILL.md 和 policy_pack 分开保存
      const res: any = await request.post(`/skills/${props.skillId}/render-ast`, ast)
      // H2: 检查版本号，旧响应不覆盖新编辑
      if (version !== _saveVersion) return
      if (res.skill_md) {
        emit('md-update', res.skill_md)
      }
      // B2: 参数变更单独保存 policy_pack.yaml
      if (policy_pack) {
        try {
          const yamlStr = Object.entries(policy_pack).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join('\n')
          await request.post(`/skills/${props.skillId}/save-file`, {
            path: 'policy_pack.yaml',
            content: yamlStr,
          })
        } catch (e) {
          console.warn('保存 policy_pack 失败', e)
        }
      }
    } catch (e) {
      console.warn('双向同步失败', e)
    }
  }, 500)
}

function onTraceHighlight(data: { highlights?: { output?: boolean } } | null) {
  // 执行轨迹叠加：高亮走过的节点
  if (!data) {
    // 清除高亮 — 恢复所有节点样式
    nodes.value = nodes.value.map(n => ({ ...n, class: '' }))
    return
  }
  // 简单高亮：有 output 的标绿，其他标灰
  nodes.value = nodes.value.map(n => {
    if (n.type === 'decisionStep') return { ...n, class: 'trace-evaluated' }
    if (n.type === 'conclusion' && data.highlights?.output) return { ...n, class: 'trace-taken' }
    return { ...n, class: 'trace-dim' }
  })
}

function addStep() {
  pushUndo()
  const maxId = allStepIds.value.reduce((max: number, id: string) => Math.max(max, parseInt(id) || 0), 0)
  const newId = String(maxId + 1)
  const newNode: FlowNode = {
    id: `step-${newId}`,
    type: 'decisionStep',
    position: { x: 400, y: nodes.value.length * 100 },
    data: {
      id: newId, name: '新步骤', description: '',
      branches: [{ condition: '条件', conclusion: '结论', action: '动作', next_step: null }],
    },
  }
  nodes.value = [...nodes.value, newNode]
  allStepIds.value.push(newId)
  if (flowDataCache) {
    flowDataCache.steps = [...((flowDataCache.steps as Array<Record<string, unknown>>) || []), {
      id: newId, name: '新步骤', description: '',
      branches: [{ condition: '条件', conclusion: '结论', action: '动作', next_step: null }],
    }]
  }
  selectedNode.value = newNode
}

// 外部调用：文本变了 → 重新加载流程图
function refreshFromText() {
  loadFlowData()
}

// #48: 优化器候选 Diff 叠加
function applyOptimizerDiff(changes: Array<{ step?: string }>) {
  if (!changes?.length) {
    nodes.value = nodes.value.map(n => ({ ...n, class: '' }))
    return
  }
  const changedSteps = new Set(changes.map(c => c.step).filter(Boolean))
  nodes.value = nodes.value.map(n => {
    if (n.type === 'decisionStep' && changedSteps.has(n.data?.id as string)) {
      return { ...n, class: 'diff-modified' }
    }
    return { ...n, class: '' }
  })
}

// #49: 导出为 SVG
function exportImage() {
  const el = canvasRef.value as HTMLElement | null
  if (!el) return
  const svgEl = el.querySelector('.vue-flow__viewport') as Element | null
  if (svgEl) {
    // 克隆 SVG 并内联样式
    const clone = svgEl.cloneNode(true) as HTMLElement
    const blob = new Blob([`<svg xmlns="http://www.w3.org/2000/svg">${clone.innerHTML}</svg>`], { type: 'image/svg+xml' })
    const link = document.createElement('a')
    link.download = `skill-flow-${props.skillId}.svg`
    link.href = URL.createObjectURL(blob)
    link.click()
    URL.revokeObjectURL(link.href)
  }
}

// #50: 测试覆盖率标记
function applyTestCoverage(testCaseCount: number) {
  if (!testCaseCount) return
  // 简单逻辑：有测试的步骤标绿，无测试的标红
  nodes.value = nodes.value.map(n => {
    if (n.type === 'decisionStep') {
      const branches = n.data?.branches as unknown[] | undefined
      const hasBranches = (branches?.length || 0) > 0
      return { ...n, data: { ...n.data, _coverageIcon: hasBranches && testCaseCount > 0 ? '✓' : '✗' } }
    }
    return n
  })
}

function onKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) { e.preventDefault(); undo() }
  if ((e.ctrlKey || e.metaKey) && e.key === 'z' && e.shiftKey) { e.preventDefault(); redo() }
}

onMounted(() => {
  loadFlowData()
  document.addEventListener('keydown', onKeydown)
})

import { onBeforeUnmount } from 'vue'
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))

defineExpose({ loadFlowData, fitView, doAutoLayout, refreshFromText, applyOptimizerDiff, applyTestCoverage })
</script>

<style scoped>
.skill-flow-panel {
  display: flex;
  flex-direction: row;
  height: 100%;
  background: #f8f9fa;
  border-radius: 8px;
  overflow: hidden;
}
.skill-flow-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

/* 工具栏 */
.flow-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 12px;
  background: #fff;
  border-bottom: 1px solid #f0f0f0;
  flex-shrink: 0;
}
.toolbar-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.flow-info {
  font-size: 11px;
  color: #98a2b3;
  display: flex;
  gap: 4px;
}

/* 画布 */
.flow-canvas {
  flex: 1;
  min-height: 0;
}

/* 加载 */
.flow-loading, .flow-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: rgba(248,249,250,.9);
  z-index: 10;
}

/* Vue Flow 全局覆写 */
:deep(.vue-flow) {
  background: #f8f9fa;
}
:deep(.vue-flow__minimap) {
  border-radius: 8px;
  border: 1px solid #e5e7eb;
  box-shadow: 0 2px 8px rgba(0,0,0,.08);
}
/* 连线箭头 */
:deep(.vue-flow__edge-path) {
  stroke-linecap: round;
}
:deep(.vue-flow__arrowhead) {
  fill: #667085;
}
/* 选中节点 — 用 ai-ink-1 黑 ring 替代 arcoblue */
:deep(.vue-flow__node.selected .flow-node) {
  box-shadow: 0 0 0 2px var(--ai-ink-1), 0 6px 16px rgba(15,16,17,.12) !important;
}

/* 执行轨迹叠加 */
:deep(.trace-evaluated .flow-node) { box-shadow: 0 0 0 2px #12b76a, 0 4px 12px rgba(18,183,106,.15) !important; }
:deep(.trace-taken .flow-node) { box-shadow: 0 0 0 2px #12b76a !important; }
:deep(.trace-dim .flow-node) { opacity: 0.3; filter: grayscale(0.5); }
/* 优化器 Diff */
:deep(.diff-modified .flow-node) { box-shadow: 0 0 0 2px #f97316, 0 4px 12px rgba(249,115,22,.15) !important; }
</style>
