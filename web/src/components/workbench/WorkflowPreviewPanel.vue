<template>
  <section class="wb-panel">
    <header class="panel-header">
      <div>
        <div class="panel-title">{{ title }}</div>
        <div class="panel-subtitle">{{ subtitle }}</div>
      </div>
      <div class="header-actions">
        <a-tag size="small" :color="statusColor">{{ statusLabel }}</a-tag>
        <a-button size="mini" type="text" @click="emit('refresh')">刷新</a-button>
        <a-button v-if="canOpenCanvas" size="mini" type="outline" @click="emit('open-canvas')">打开完整画布</a-button>
      </div>
    </header>

    <div class="summary-grid">
      <div class="summary-card">
        <div class="summary-label">节点</div>
        <div class="summary-value">{{ nodeCount }}</div>
      </div>
      <div class="summary-card">
        <div class="summary-label">连线</div>
        <div class="summary-value">{{ edgeCount }}</div>
      </div>
      <div class="summary-card">
        <div class="summary-label">绑定</div>
        <div class="summary-value">{{ bindingCount }}</div>
      </div>
      <div class="summary-card">
        <div class="summary-label">变更</div>
        <div class="summary-value">{{ diffCount }}</div>
      </div>
    </div>

    <div v-if="loading" class="panel-state">
      <a-spin />
      <span>正在生成工作流预览...</span>
    </div>

    <template v-else>
      <a-empty v-if="!hasPreview" description="暂无工作流预览">
        <a-button type="primary" size="mini" @click="emit('refresh')">生成预览</a-button>
      </a-empty>

      <template v-else>
        <div v-if="data.summary" class="preview-summary">{{ data.summary }}</div>

        <div class="block">
          <div class="section-header">
            <div class="section-title">工作流结构</div>
            <a-tag size="small" color="blue">{{ data.status || 'draft' }}</a-tag>
          </div>

          <div class="structure-grid">
            <div class="structure-column">
              <div class="column-title">节点</div>
              <div v-if="nodes.length" class="item-list">
                <div v-for="node in nodes" :key="node.key" class="item-card">
                  <div class="item-title">{{ node.label }}</div>
                  <div v-if="node.detail" class="item-detail">{{ node.detail }}</div>
                </div>
              </div>
              <div v-else class="mini-empty">暂无节点</div>
            </div>

            <div class="structure-column">
              <div class="column-title">连线</div>
              <div v-if="edges.length" class="item-list">
                <div v-for="edge in edges" :key="edge.key" class="item-card">
                  <div class="item-title">{{ edge.label }}</div>
                  <div v-if="edge.detail" class="item-detail">{{ edge.detail }}</div>
                </div>
              </div>
              <div v-else class="mini-empty">暂无连线</div>
            </div>

            <div class="structure-column">
              <div class="column-title">字段绑定</div>
              <div v-if="bindings.length" class="item-list">
                <div v-for="binding in bindings" :key="binding.key" class="item-card">
                  <div class="item-title">{{ binding.label }}</div>
                  <div v-if="binding.detail" class="item-detail">{{ binding.detail }}</div>
                </div>
              </div>
              <div v-else class="mini-empty">暂无绑定</div>
            </div>
          </div>
        </div>

        <div class="block">
          <div class="section-header">
            <div class="section-title">变更预览</div>
            <a-tag size="small">{{ diffEntries.length }}</a-tag>
          </div>
          <div v-if="diffEntries.length" class="diff-list">
            <div v-for="entry in diffEntries" :key="entry.key" class="diff-item" :class="`type-${entry.type}`">
              <div class="diff-badge">{{ entry.shortType }}</div>
              <div class="diff-content">
                <div class="item-title">{{ entry.title }}</div>
                <div v-if="entry.detail" class="item-detail">{{ entry.detail }}</div>
              </div>
            </div>
          </div>
          <div v-else class="mini-empty">暂无差异项</div>
        </div>
      </template>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'

const props = defineProps({
  title: { type: String, default: '工作流预览' },
  subtitle: { type: String, default: '用于查看 workflow patch 的结构变化' },
  preview: { type: Object as PropType<any>, default: () => ({}) },
  workflow: { type: Object as PropType<any>, default: () => ({}) },
  loading: { type: Boolean, default: false },
  canOpenCanvas: { type: Boolean, default: true },
})

const emit = defineEmits(['refresh', 'open-canvas'])

const data = computed(() => {
  const primary = props.preview && Object.keys(props.preview).length ? props.preview : props.workflow
  return primary || {}
})

const hasPreview = computed(() => {
  const value = data.value
  const diff = value.diff_preview || value.diffPreview || {}
  return !!(value.summary
    || (Array.isArray(value.nodes) && value.nodes.length)
    || (Array.isArray(value.edges) && value.edges.length)
    || (Array.isArray(value.bindings) && value.bindings.length)
    || (diff && typeof diff === 'object' && Object.keys(diff).length))
})

const nodes = computed(() => normalizeItems(data.value.nodes || [], 'node', '节点'))
const edges = computed(() => normalizeItems(data.value.edges || [], 'edge', '连线'))
const bindings = computed(() => normalizeItems(data.value.bindings || [], 'binding', '绑定'))
const diffEntries = computed(() => normalizeDiffEntries(data.value.diff_preview || data.value.diffPreview || {}))

const nodeCount = computed(() => nodes.value.length)
const edgeCount = computed(() => edges.value.length)
const bindingCount = computed(() => bindings.value.length)
const diffCount = computed(() => diffEntries.value.length)

const statusLabel = computed(() => {
  if (props.loading) return '生成中'
  if (!hasPreview.value) return '空'
  if (diffEntries.value.length > 0) return '有变更'
  return '就绪'
})

const statusColor = computed(() => {
  if (props.loading) return 'gray'
  if (!hasPreview.value) return 'gray'
  if (diffEntries.value.length > 0) return 'arcoblue'
  return 'green'
})

function normalizeItems(items: any, type: string, fallbackLabel: string) {
  return (Array.isArray(items) ? items : []).map((item, index) => {
    if (typeof item === 'string') {
      return {
        key: `${type}-${index}`,
        label: item,
        detail: '',
      }
    }

    if (!item || typeof item !== 'object') {
      return {
        key: `${type}-${index}`,
        label: `${fallbackLabel} ${index + 1}`,
        detail: '',
      }
    }

    return {
      key: item.key || item.id || `${type}-${index}`,
      label: item.label || item.title || item.name || item.id || `${fallbackLabel} ${index + 1}`,
      detail: item.detail || item.summary || item.description || item.condition || item.binding || '',
    }
  })
}

function normalizeDiffEntries(diffPreview: any) {
  if (!diffPreview || typeof diffPreview !== 'object') return []

  const buckets: { key: string; type: string; shortType: string; title: string; detail: string }[] = []
  const addBucket = (value: unknown, type: string, title: string) => {
    const items = Array.isArray(value) ? value : (value ? [value] : [])
    items.forEach((item: any, index: number) => {
      if (typeof item === 'string') {
        buckets.push({
          key: `${type}-${index}`,
          type,
          shortType: title.slice(0, 2),
          title: item,
          detail: '',
        })
        return
      }

      buckets.push({
        key: item.key || item.id || `${type}-${index}`,
        type,
        shortType: title.slice(0, 2),
        title: item.title || item.name || item.label || title,
        detail: item.detail || item.summary || item.description || '',
      })
    })
  }

  addBucket(diffPreview.added_nodes || diffPreview.node_additions || diffPreview.nodes_added, 'add', '新增节点')
  addBucket(diffPreview.removed_nodes || diffPreview.node_removals || diffPreview.nodes_removed, 'remove', '移除节点')
  addBucket(diffPreview.updated_nodes || diffPreview.node_updates, 'update', '更新节点')
  addBucket(diffPreview.added_edges || diffPreview.edge_additions || diffPreview.edges_added, 'add', '新增连线')
  addBucket(diffPreview.removed_edges || diffPreview.edge_removals || diffPreview.edges_removed, 'remove', '移除连线')
  addBucket(diffPreview.bindings_changed || diffPreview.binding_changes, 'update', '绑定变更')
  addBucket(diffPreview.notes || diffPreview.summary, 'note', '说明')

  return buckets
}
</script>

<style scoped>
.wb-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--ai-border);
  border-radius: 14px;
  background: var(--ai-surface);
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.05);
}

.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.panel-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--ai-ink-1);
}

.panel-subtitle {
  margin-top: 4px;
  font-size: 12px;
  color: var(--ai-ink-3);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.summary-card {
  padding: 10px 12px;
  border-radius: 12px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
}

.summary-label,
.column-title {
  font-size: 12px;
  color: var(--ai-ink-3);
}

.summary-value {
  margin-top: 4px;
  font-size: 18px;
  font-weight: 700;
  color: var(--ai-ink-1);
}

.panel-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 120px;
  color: var(--ai-ink-3);
}

.preview-summary {
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(22, 93, 255, 0.08);
  color: var(--ai-ink-1);
  font-size: 13px;
}

.block {
  padding: 12px;
  border-radius: 12px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
}

.structure-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.structure-column {
  padding: 10px;
  border-radius: 12px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
}

.item-list,
.diff-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
}

.item-card,
.diff-item {
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
}

.item-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
}

.item-detail {
  margin-top: 3px;
  font-size: 12px;
  color: var(--ai-ink-3);
  line-height: 1.5;
}

.mini-empty {
  padding: 12px 0 6px;
  font-size: 12px;
  color: var(--ai-ink-4);
}

.diff-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.diff-badge {
  flex-shrink: 0;
  min-width: 40px;
  height: 28px;
  padding: 0 8px;
  border-radius: 8px;
  background: rgba(22, 93, 255, 0.1);
  color: var(--ai-accent-ink);
  font-size: 11px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.type-add .diff-badge {
  background: rgba(0, 180, 42, 0.12);
  color: var(--ai-ok);
}

.type-remove .diff-badge {
  background: rgba(245, 63, 63, 0.12);
  color: var(--ai-bad);
}

.type-update .diff-badge {
  background: rgba(250, 173, 20, 0.12);
  color: var(--ai-warn);
}

@media (max-width: 960px) {
  .summary-grid,
  .structure-grid {
    grid-template-columns: 1fr;
  }
}
</style>
