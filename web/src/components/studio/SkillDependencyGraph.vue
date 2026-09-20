<template>
  <div class="dep-graph">
    <div class="graph-toolbar">
      <span class="summary-chip" v-if="summary">
        <SfShellIcon name="grid" />Skill {{ summary.skill_count }} · 数据源 {{ summary.datasource_count }} · Playbook {{ summary.playbook_count }}
      </span>
      <a-input-search
        v-model="searchKeyword"
        allow-clear
        size="mini"
        class="toolbar-search"
        placeholder="搜索节点 / ID"
      />
      <a-checkbox-group v-model="activeTypes" size="small" class="toolbar-types">
        <a-checkbox value="skill">Skill</a-checkbox>
        <a-checkbox value="datasource">数据源</a-checkbox>
        <a-checkbox value="playbook">Playbook</a-checkbox>
      </a-checkbox-group>
      <label class="toolbar-switch">
        <a-switch v-model="onlyMatched" size="small" />
        <span>仅看命中</span>
      </label>
      <a-button size="mini" :loading="loading" @click="load">
        <template #icon><SfShellIcon name="refresh" class="graph-button-icon" /></template>刷新
      </a-button>
      <a-button size="mini" @click="fit" v-if="cyRef">
        <template #icon><SfShellIcon name="grid" class="graph-button-icon" /></template>自适应
      </a-button>
    </div>
    <SfLoadingState v-if="loading" tip="加载依赖图..." height="280px" />
    <SfEmptyState
      v-else-if="loadError"
      :description="loadError"
    />
    <SfEmptyState
      v-else-if="graphEmptyDescription"
      :description="graphEmptyDescription"
    />
    <div v-else ref="containerRef" class="graph-canvas" />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { skillApi } from '@/api'
import { SfLoadingState, SfEmptyState } from '@/components/common'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'
import type {
  SkillDependencyGraphEdge,
  SkillDependencyGraphNode,
  SkillDependencyGraphSummary,
  SkillDependencyNodeType,
} from '@/types/skillstudio'
import { getErrorMessage } from '@/types/skillstudio'

interface CytoscapeNodeData {
  id: string
  routeId: string
  label: string
  type: SkillDependencyNodeType
  status: string
  isSelf: boolean
  canView: boolean
  matched: boolean
  dimmed: boolean
}

interface CytoscapeNodeEvent {
  target: {
    data(): CytoscapeNodeData
  }
}

interface CytoscapeCore {
  destroy(): void
  fit(elements?: unknown, padding?: number): void
  on(event: string, selector: string, callback: (evt: CytoscapeNodeEvent) => void): void
}

type CytoscapeFactory = (options: Record<string, unknown>) => CytoscapeCore

const NODE_COLORS: Record<SkillDependencyNodeType, string> = {
  skill: '#1677ff',
  datasource: '#00b42a',
  playbook: '#f5a623',
}

const EDGE_COLORS: Record<string, string> = {
  'forked-into': '#f5a623',
  consumes: '#00b42a',
  calls: '#1677ff',
}

const props = defineProps<{ skillId: string }>()
const emit = defineEmits<{
  (e: 'open-node', node: { id: string; type: SkillDependencyNodeType }): void
}>()

const loading = ref(false)
const loadError = ref('')
const nodes = ref<SkillDependencyGraphNode[]>([])
const edges = ref<SkillDependencyGraphEdge[]>([])
const summary = ref<SkillDependencyGraphSummary | null>(null)
const containerRef = ref<HTMLElement | null>(null)
const cyRef = ref<CytoscapeCore | null>(null)
const searchKeyword = ref('')
const onlyMatched = ref(false)
const activeTypes = ref<SkillDependencyNodeType[]>(['skill', 'datasource', 'playbook'])

const normalizedSearch = computed(() => searchKeyword.value.trim().toLowerCase())
const activeTypeSet = computed(() => new Set(activeTypes.value))

function nodeMatches(node: SkillDependencyGraphNode): boolean {
  const keyword = normalizedSearch.value
  if (!keyword) return true
  return `${node.label} ${node.id} ${node.department || ''}`.toLowerCase().includes(keyword)
}

const matchedNodeIdSet = computed(() => {
  const ids = new Set<string>()
  if (!normalizedSearch.value) return ids
  for (const node of nodes.value) {
    if (nodeMatches(node)) ids.add(node.id)
  }
  return ids
})

const visibleNodes = computed(() => {
  return nodes.value.filter((node) => {
    if (!node.is_self && !activeTypeSet.value.has(node.type)) {
      return false
    }
    if (!onlyMatched.value || !normalizedSearch.value) {
      return true
    }
    return node.is_self || matchedNodeIdSet.value.has(node.id)
  })
})

const visibleNodeIdSet = computed(() => new Set(visibleNodes.value.map((node) => node.id)))

const visibleEdges = computed(() => {
  return edges.value.filter((edge) => {
    return visibleNodeIdSet.value.has(edge.from) && visibleNodeIdSet.value.has(edge.to)
  })
})

const graphEmptyDescription = computed(() => {
  if (!nodes.value.length) {
    return '此 Skill 没有被 Playbook 调用 / 无数据源依赖 / 无 fork 关系'
  }
  if (!visibleNodes.value.length) {
    return '当前筛选条件下没有可展示的节点'
  }
  if (visibleNodes.value.length <= 1 && visibleEdges.value.length === 0) {
    return normalizedSearch.value ? '当前搜索仅命中当前 Skill' : '此 Skill 没有被 Playbook 调用 / 无数据源依赖 / 无 fork 关系'
  }
  return ''
})

async function load() {
  if (!props.skillId) return
  loading.value = true
  loadError.value = ''
  try {
    const response = await skillApi.dependencyGraph(props.skillId)
    nodes.value = response.nodes || []
    edges.value = response.edges || []
    summary.value = response.summary || null
    await nextTick()
    await renderGraph()
  } catch (error) {
    loadError.value = getErrorMessage(error, '加载失败')
  } finally {
    loading.value = false
  }
}

async function renderGraph() {
  if (cyRef.value) {
    cyRef.value.destroy()
    cyRef.value = null
  }
  if (!containerRef.value || !visibleNodes.value.length || graphEmptyDescription.value) {
    return
  }

  const cytoscapeModule = await import('cytoscape')
  const cytoscape = cytoscapeModule.default as CytoscapeFactory
  const searchActive = normalizedSearch.value.length > 0

  const elements = [
    ...visibleNodes.value.map((node) => ({
      data: {
        id: node.id,
        routeId: node.route_id || node.id,
        label: node.label,
        type: node.type,
        status: node.status || '',
        isSelf: Boolean(node.is_self),
        canView: node.can_view !== false,
        matched: !searchActive || matchedNodeIdSet.value.has(node.id),
        dimmed: searchActive && !matchedNodeIdSet.value.has(node.id),
      },
    })),
    ...visibleEdges.value.map((edge, index) => ({
      data: {
        id: `e-${index}`,
        source: edge.from,
        target: edge.to,
        kind: edge.kind,
        dimmed: searchActive && !(
          matchedNodeIdSet.value.has(edge.from) || matchedNodeIdSet.value.has(edge.to)
        ),
      },
    })),
  ]

  cyRef.value = cytoscape({
    container: containerRef.value,
    elements,
    layout: { name: 'breadthfirst', directed: true, spacingFactor: 1.25 },
    style: [
      {
        selector: 'node',
        style: {
          label: 'data(label)',
          'background-color': (ele: { data(key: string): string }) => NODE_COLORS[(ele.data('type') as SkillDependencyNodeType) || 'skill'] || '#aaa',
          'border-width': (ele: { data(key: string): boolean }) => (ele.data('isSelf') ? 3 : 1),
          'border-color': (ele: { data(key: string): boolean }) => (ele.data('matched') ? '#111827' : '#94a3b8'),
          opacity: (ele: { data(key: string): boolean }) => (ele.data('dimmed') ? 0.38 : 1),
          color: '#fff',
          'text-valign': 'center',
          'text-halign': 'center',
          'font-size': '10px',
          'text-wrap': 'wrap',
          'text-max-width': '80px',
          width: '80px',
          height: '50px',
          shape: (ele: { data(key: string): string }) => (ele.data('type') === 'skill' ? 'round-rectangle' : 'ellipse'),
        },
      },
      {
        selector: 'edge',
        style: {
          'line-color': (ele: { data(key: string): string }) => EDGE_COLORS[ele.data('kind')] || '#999',
          'target-arrow-color': (ele: { data(key: string): string }) => EDGE_COLORS[ele.data('kind')] || '#999',
          opacity: (ele: { data(key: string): boolean }) => (ele.data('dimmed') ? 0.22 : 0.95),
          'target-arrow-shape': 'triangle',
          'curve-style': 'bezier',
          label: 'data(kind)',
          'font-size': '8px',
          color: '#666',
          'text-background-color': '#fff',
          'text-background-opacity': 0.8,
          'text-background-padding': '2px',
          width: '2px',
        },
      },
    ],
  })

  cyRef.value.on('tap', 'node', (evt) => {
    const data = evt.target.data()
    if (data.canView === false) {
      Message.warning('你暂无权限查看该节点')
      return
    }
    const targetId = String(data.routeId || data.id || '')
    if (targetId && targetId !== props.skillId) {
      emit('open-node', { id: targetId, type: data.type })
    }
  })
}

function fit() {
  if (cyRef.value) {
    cyRef.value.fit(undefined, 30)
  }
}

watch(
  () => props.skillId,
  (value) => {
    if (value) {
      void load()
    }
  },
  { immediate: true },
)

watch([searchKeyword, activeTypes, onlyMatched], async () => {
  await nextTick()
  await renderGraph()
})

onUnmounted(() => {
  if (cyRef.value) {
    cyRef.value.destroy()
    cyRef.value = null
  }
})
</script>

<style scoped>
.dep-graph {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  min-height: 340px;
}
.graph-toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 4px 0;
  flex-wrap: wrap;
}
.summary-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--ai-ink-2);
  padding: 2px 8px;
  background: var(--ai-surface-2);
  border-radius: 8px;
  margin-right: auto;
}
.summary-chip svg,
.graph-button-icon {
  width: 13px;
  height: 13px;
  flex: 0 0 auto;
}
.toolbar-search {
  width: 180px;
}
.toolbar-types {
  display: inline-flex;
  gap: 6px;
}
.toolbar-switch {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.graph-canvas {
  flex: 1;
  min-height: 300px;
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  background:
    radial-gradient(circle at top left, rgba(22, 119, 255, 0.06), transparent 28%),
    linear-gradient(180deg, #fafbfc 0%, #f5f7fb 100%);
}
</style>
