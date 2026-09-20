<template>
  <div class="dc">
    <!-- 工具栏 -->
    <div class="dc-toolbar">
      <a-radio-group v-model="viewType" type="button" size="mini">
        <a-radio value="canvas">画布</a-radio>
        <a-radio value="table">决策表</a-radio>
      </a-radio-group>
      <span class="dc-spacer" />
      <a-button size="mini" type="outline" @click="$emit('add-step')">+ 添加步骤</a-button>
    </div>

    <!-- 画布视图 -->
    <div v-if="viewType === 'canvas'" class="dc-canvas" ref="canvasRef">
      <svg :width="svgWidth" :height="svgHeight" class="dc-svg">
        <defs>
          <marker id="arrowhead" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
            <path d="M0,0 L0,6 L8,3 z" fill="#c9cdd4" />
          </marker>
          <marker id="arrowhead-active" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
            <path d="M0,0 L0,6 L8,3 z" fill="var(--ai-accent-ink)" />
          </marker>
          <marker id="arrowhead-fail" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
            <path d="M0,0 L0,6 L8,3 z" fill="var(--ai-bad)" />
          </marker>
        </defs>

        <!-- 连线 -->
        <g v-for="edge in edges" :key="edge.id">
          <path
            :d="edge.path"
            class="dc-edge"
            :class="{
              'dc-edge--active': highlightedPath.includes(edge.id),
              'dc-edge--fail': failedNodes.includes(edge.target)
            }"
            :marker-end="edgeMarker(edge)"
          />
          <text v-if="edge.label" :x="edge.labelX" :y="edge.labelY" class="dc-edge-label">{{ edge.label }}</text>
        </g>

        <!-- 节点 -->
        <g
          v-for="(node, ni) in nodes" :key="node.id"
          :transform="`translate(${node.x}, ${node.y})`"
          class="dc-node"
          :class="{
            'dc-node--active': selectedNode === node.id,
            'dc-node--highlighted': highlightedPath.includes(node.id),
            'dc-node--failed': failedNodes.includes(node.id)
          }"
          @click="$emit('select-node', node.id)"
        >
          <!-- 节点背景 -->
          <rect :width="NODE_W" :height="node.height" rx="10" class="dc-node-bg" />

          <!-- 顶部色条 + 序号 -->
          <rect :width="NODE_W" height="36" rx="10" class="dc-node-header" />
          <rect :width="NODE_W" y="26" height="10" class="dc-node-header" /><!-- 补底角 -->
          <text x="14" y="23" class="dc-node-seq"># {{ Number(ni) + 1 }}</text>
          <text :x="NODE_W / 2" y="23" class="dc-node-title" text-anchor="middle">{{ node.name }}</text>

          <!-- 分支列表 -->
          <g v-for="(br, bi) in node.branches" :key="bi">
            <!-- 分支区域分隔线（非第一个） -->
            <line
              v-if="Number(bi) > 0"
              :x1="12" :y1="node.branchOffsets[Number(bi)]"
              :x2="NODE_W - 12" :y2="node.branchOffsets[Number(bi)]"
              class="dc-branch-divider"
            />
            <foreignObject
              :x="12"
              :y="node.branchOffsets[Number(bi)] + 6"
              :width="NODE_W - 24"
              :height="node.branchHeights[Number(bi)] - 8"
              overflow="visible"
            >
              <div xmlns="http://www.w3.org/1999/xhtml" class="dc-branch">
                <!-- 条件文本 -->
                <div class="dc-branch-cond">
                  <template v-for="part in tokenizeBranch(br, props.params)" :key="part.key">
                    <span v-if="part.type === 'text'" class="dc-branch-cond-text">{{ part.value }}</span>
                    <span v-else class="dc-param-chip"
                      :class="{ 'dc-param-chip--hover': hoveredParam === part.name }"
                      @mouseenter.stop="hoveredParam = part.name"
                      @mouseleave.stop="hoveredParam = ''"
                      @click.stop="$emit('edit-param', part)"
                    >{{ part.name }}={{ part.value }}</span>
                  </template>
                </div>
                <!-- 结论 + 动作行 -->
                <div class="dc-branch-result">
                  <span class="dc-branch-arrow">→</span>
                  <span class="dc-branch-concl" :class="`dc-concl--${conclusionKind(br.conclusion)}`">
                    {{ br.conclusion }}
                  </span>
                  <span v-if="br.action" class="dc-branch-action">{{ br.action }}</span>
                </div>
              </div>
            </foreignObject>
          </g>

          <!-- 节点底部覆盖率角标 -->
          <text v-if="node.coverage" :x="NODE_W - 10" :y="node.height - 8" class="dc-coverage" text-anchor="end">
            {{ node.coverage }}
          </text>
        </g>
      </svg>
    </div>

    <!-- 决策表视图 -->
    <div v-else class="dc-table">
      <table>
        <thead>
          <tr>
            <th>步骤</th>
            <th>条件</th>
            <th>结论</th>
            <th>动作</th>
            <th>下一步</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="step in steps" :key="step.id">
            <tr
              v-for="(br, bi) in step.branches" :key="step.id + '-' + bi"
              class="dc-table-row"
              :class="{
                'dc-table-row--active': selectedNode === step.id,
                'dc-table-row--highlighted': highlightedPath.includes(step.id)
              }"
              @click="$emit('select-node', step.id)"
            >
              <td v-if="bi === 0" :rowspan="step.branches.length" class="dc-td-step">
                {{ step.name || step.id }}
              </td>
              <td class="dc-td-cond">{{ br.condition }}</td>
              <td>
                <a-tag size="small" :color="conclusionColor(br.conclusion)">{{ br.conclusion }}</a-tag>
              </td>
              <td class="dc-td-action">{{ br.action || '-' }}</td>
              <td>{{ br.next_step || '-' }}</td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { PropType } from 'vue'

const props = defineProps({
  steps: { type: Array as PropType<any[]>, default: () => [] },
  params: { type: Array as PropType<any[]>, default: () => [] },
  selectedNode: { type: String, default: '' },
  highlightedPath: { type: Array as PropType<string[]>, default: () => [] },
  failedNodes: { type: Array as PropType<string[]>, default: () => [] },
})

defineEmits(['select-node', 'add-step', 'edit-param'])

const viewType = ref('canvas')
const hoveredParam = ref('')
const canvasRef = ref<HTMLElement | null>(null)

// ---------- 布局常量 ----------
const NODE_W = 440          // 节点宽度
const NODE_HEADER_H = 40    // 顶部标题栏高度
const NODE_PADDING_B = 12   // 节点底部留白
const NODE_GAP_Y = 56       // 节点间垂直间距
const BRANCH_PAD_TOP = 8    // 分支顶部留白
const BRANCH_LINE_H = 18    // 文字行高（px）
const CHARS_PER_LINE = 24   // 每行约能放多少个中文字符（NODE_W-48 宽度 / ~16px 字宽）
const RESULT_ROW_H = 24     // 结论行高度
const BRANCH_MIN_H = 56     // 每个分支最小高度
const CANVAS_OFFSET_X = 40  // 画布左边距

// 估算一段文本需要几行（中文字符宽约等于 2 个英文字符）
function estimateLines(text: string) {
  if (!text) return 1
  let weight = 0
  for (const ch of text) {
    weight += ch.charCodeAt(0) > 127 ? 2 : 1
  }
  return Math.ceil(weight / (CHARS_PER_LINE * 2))
}

// 估算单个分支区域高度
function estimateBranchHeight(br: any) {
  const condLines = estimateLines(br.condition || '')
  const condH = Math.max(1, condLines) * BRANCH_LINE_H
  return Math.max(BRANCH_MIN_H, BRANCH_PAD_TOP + condH + RESULT_ROW_H + 8)
}

const nodes = computed(() => {
  let cumY = NODE_GAP_Y / 2
  return props.steps.map((step) => {
    const branches = step.branches || []
    const branchHeights = branches.map(estimateBranchHeight)
    const branchOffsets = []
    let off = NODE_HEADER_H
    for (const h of branchHeights) {
      branchOffsets.push(off)
      off += h
    }
    const totalHeight = NODE_HEADER_H + branchHeights.reduce((a: number, b: number) => a + b, 0) + NODE_PADDING_B

    const node = {
      id: step.id,
      name: step.name || step.id,
      branches: branches.map((b: Record<string, unknown>) => ({
        ...b,
        params: extractParams(String(b.condition), props.params),
      })),
      x: CANVAS_OFFSET_X,
      y: cumY,
      width: NODE_W,
      height: totalHeight,
      branchHeights,
      branchOffsets,
      coverage: '',
    }
    cumY += totalHeight + NODE_GAP_Y
    return node
  })
})

const edges = computed(() => {
  const result = []
  for (let i = 0; i < nodes.value.length - 1; i++) {
    const from = nodes.value[i]
    const to = nodes.value[i + 1]
    const cx = from.x + NODE_W / 2
    const y1 = from.y + from.height
    const y2 = to.y
    const midY = y1 + (y2 - y1) * 0.5
    result.push({
      id: `${from.id}-${to.id}`,
      source: from.id,
      target: to.id,
      path: `M ${cx} ${y1} C ${cx} ${midY}, ${cx} ${midY}, ${cx} ${y2 - 6}`,
      label: '',
      labelX: cx + 8,
      labelY: (y1 + y2) / 2,
    })
  }
  return result
})

const svgWidth = computed(() => NODE_W + CANVAS_OFFSET_X * 2)
const svgHeight = computed(() => {
  if (!nodes.value.length) return 200
  const last = nodes.value[nodes.value.length - 1]
  return last.y + last.height + NODE_GAP_Y
})

function edgeMarker(edge: any) {
  if (props.highlightedPath.includes(edge.id)) return 'url(#arrowhead-active)'
  if (props.failedNodes.includes(edge.target)) return 'url(#arrowhead-fail)'
  return 'url(#arrowhead)'
}

function extractParams(condition: string, allParams: any[]) {
  if (!condition) return []
  const result: any[] = []
  for (const p of allParams) {
    if (condition.includes(p.name) || condition.includes(String(p.default_value ?? p.value ?? ''))) {
      result.push({ name: p.name, value: p.default_value ?? p.value ?? '' })
    }
  }
  return result
}

function tokenizeBranch(br: any, allParams: any[]) {
  const cond = br.condition || ''
  if (!cond) return []
  const parts: any[] = []
  let cursor = 0
  const hits: any[] = []
  for (const p of allParams) {
    const idx = cond.indexOf(p.name)
    if (idx >= 0) hits.push({ start: idx, end: idx + p.name.length, param: p })
  }
  hits.sort((a, b) => a.start - b.start)
  for (const h of hits) {
    if (h.start < cursor) continue
    if (h.start > cursor) parts.push({ type: 'text', key: `t${cursor}`, value: cond.slice(cursor, h.start) })
    parts.push({ type: 'chip', key: `c${h.start}`, name: h.param.name, value: h.param.default_value ?? h.param.value ?? '' })
    cursor = h.end
  }
  if (cursor < cond.length) parts.push({ type: 'text', key: `t${cursor}`, value: cond.slice(cursor) })
  if (!parts.length) parts.push({ type: 'text', key: 't0', value: cond })
  return parts
}

function conclusionColor(c: string) {
  if (!c) return 'gray'
  if (c.includes('绿') || c.includes('通过') || c.includes('成功') || c.includes('完备') || c.includes('完整') || c.includes('正确') || c.includes('完成')) return 'green'
  if (c.includes('红') || c.includes('失败') || c.includes('拒绝') || c.includes('错误')) return 'red'
  if (c.includes('黄') || c.includes('维持') || c.includes('等待') || c.includes('重新') || c.includes('重试')) return 'orange'
  return 'arcoblue'
}

function conclusionKind(c: string) {
  if (!c) return 'default'
  if (c.includes('绿') || c.includes('通过') || c.includes('成功') || c.includes('完备') || c.includes('完整') || c.includes('正确') || c.includes('完成')) return 'green'
  if (c.includes('红') || c.includes('失败') || c.includes('拒绝') || c.includes('错误')) return 'red'
  if (c.includes('黄') || c.includes('维持') || c.includes('等待') || c.includes('重新') || c.includes('重试')) return 'yellow'
  return 'default'
}
</script>

<style scoped>
.dc {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--ai-surface-2);
}

.dc-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  flex-shrink: 0;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
}
.dc-spacer { flex: 1; }

/* ───── 画布 ───── */
.dc-canvas {
  flex: 1;
  overflow: auto;
  padding: 0;
  background: var(--ai-surface-2);
}
.dc-svg { display: block; }

/* 连线 */
.dc-edge {
  fill: none;
  stroke: #c9cdd4;
  stroke-width: 2;
  stroke-dasharray: none;
}
.dc-edge--active { stroke: var(--ai-accent-ink); stroke-width: 2.5; }
.dc-edge--fail   { stroke: var(--ai-bad);     stroke-width: 2.5; }
.dc-edge-label   { font-size: 10px; fill: var(--ai-ink-4); text-anchor: middle; }

/* 节点外框 */
.dc-node { cursor: pointer; }
.dc-node-bg {
  fill: var(--ai-surface);
  stroke: var(--ai-border);
  stroke-width: 1.5;
  filter: drop-shadow(0 2px 6px rgba(0,0,0,.06));
  transition: stroke .15s, filter .15s;
}
.dc-node:hover .dc-node-bg {
  stroke: var(--ai-accent-ink);
  filter: drop-shadow(0 4px 12px rgba(22,93,255,.12));
}
.dc-node--active .dc-node-bg {
  stroke: var(--ai-accent-ink);
  stroke-width: 2;
  fill: var(--ai-accent-soft);
}
.dc-node--highlighted .dc-node-bg { stroke: var(--ai-accent-ink); stroke-width: 2.5; }
.dc-node--failed .dc-node-bg       { stroke: var(--ai-bad);     stroke-width: 2.5; fill: var(--ai-bad-soft); }

/* 顶部标题条 */
.dc-node-header { fill: var(--ai-accent-ink); opacity: 0.08; }
.dc-node--active .dc-node-header  { opacity: 0.15; }
.dc-node--failed .dc-node-header  { fill: var(--ai-bad); opacity: 0.12; }

.dc-node-seq {
  font-size: 11px;
  font-weight: 700;
  fill: var(--ai-accent-ink);
  opacity: 0.7;
  font-family: var(--ai-font-mono);
}
.dc-node-title {
  font-size: 13px;
  font-weight: 600;
  fill: var(--ai-ink-1);
  font-family: var(--sf-font-sans);
}
.dc-node--failed .dc-node-title { fill: var(--ai-bad); }

/* 分支区域 */
.dc-branch-divider { stroke: var(--ai-border); stroke-width: 1; }
.dc-coverage { font-size: 10px; fill: var(--ai-ink-4); }

/* ───── foreignObject 内 HTML ───── */
:deep(.dc-branch) {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-family: var(--sf-font-sans);
  height: 100%;
}

:deep(.dc-branch-cond) {
  font-size: 12px;
  line-height: 1.6;
  color: var(--ai-ink-2);
  word-break: break-all;
  white-space: normal;
  flex: 1;
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 2px;
}
:deep(.dc-branch-cond-text) {
  color: var(--ai-ink-2);
}

:deep(.dc-param-chip) {
  display: inline-block;
  padding: 0 5px;
  border-radius: 4px;
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
  font-family: var(--ai-font-mono);
  font-size: 11px;
  border: 1px solid var(--ai-accent);
  cursor: pointer;
  transition: background .12s, border-color .12s;
  white-space: nowrap;
  line-height: 1.6;
}
:deep(.dc-param-chip:hover),
:deep(.dc-param-chip--hover) {
  background: var(--ai-accent-soft);
  border-color: var(--ai-accent-ink);
}

:deep(.dc-branch-result) {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  flex-shrink: 0;
}
:deep(.dc-branch-arrow) {
  color: var(--ai-ink-4);
  font-size: 12px;
  flex-shrink: 0;
}
:deep(.dc-branch-concl) {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
:deep(.dc-concl--green)   { background: var(--ai-ok-soft);  color: var(--ai-ok);  }
:deep(.dc-concl--red)     { background: var(--ai-bad-soft);    color: var(--ai-bad);    }
:deep(.dc-concl--yellow)  { background: var(--ai-warn-soft); color: var(--ai-warn); }
:deep(.dc-concl--default) { background: var(--ai-surface-2); color: var(--ai-ink-2);  }

:deep(.dc-branch-action) {
  font-size: 11px;
  color: var(--ai-ink-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 160px;
}

/* ───── 决策表 ───── */
.dc-table {
  flex: 1;
  overflow: auto;
  padding: 12px 16px;
  background: var(--ai-surface);
}
.dc-table table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.dc-table th {
  padding: 10px 12px;
  text-align: left;
  font-weight: 600;
  font-size: 12px;
  color: var(--ai-ink-3);
  border-bottom: 2px solid var(--ai-border);
}
.dc-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--ai-border);
  vertical-align: top;
  line-height: 1.6;
}
.dc-table-row { cursor: pointer; transition: background .1s; }
.dc-table-row:hover { background: var(--ai-surface-2); }
.dc-table-row--active      { background: var(--ai-accent-soft); }
.dc-table-row--highlighted { background: var(--ai-accent-soft); }
.dc-td-step {
  font-weight: 600;
  color: var(--ai-ink-1);
  border-right: 1px solid var(--ai-border);
  white-space: nowrap;
  min-width: 80px;
}
.dc-td-cond   { color: var(--ai-ink-2); max-width: 320px; }
.dc-td-action { color: var(--ai-ink-3); max-width: 200px; }
</style>
