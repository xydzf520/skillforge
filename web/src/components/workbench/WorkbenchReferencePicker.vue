<template>
  <section class="wb-panel">
    <header class="panel-header">
      <div>
        <div class="panel-title">{{ title }}</div>
        <div class="panel-subtitle">{{ subtitle }}</div>
      </div>
      <div class="header-actions">
        <a-tag size="small" color="blue">{{ filteredReferences.length }} 条</a-tag>
        <a-tag v-if="selectedReferences.length" size="small">{{ selectedReferences.length }} 已选</a-tag>
      </div>
    </header>

    <div class="panel-body">
      <a-input
        v-model="queryText"
        allow-clear
        size="small"
        placeholder="搜索引用名称、模块、摘要"
      />

      <div class="filter-row">
        <a-select v-model="sourceTypeValue" size="small" allow-clear placeholder="来源类型" @change="emit('update:sourceType', $event || 'all')">
          <a-option v-for="option in sourceTypeOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </a-option>
        </a-select>

        <a-select v-model="moduleValue" size="small" allow-clear placeholder="模块" @change="emit('update:module', $event || 'all')">
          <a-option v-for="option in moduleOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </a-option>
        </a-select>

        <a-select v-model="modeValue" size="small" allow-clear placeholder="引用方式" @change="emit('update:referenceMode', $event || 'all')">
          <a-option v-for="option in modeOptions" :key="option.value" :value="option.value">
            {{ option.label }}
          </a-option>
        </a-select>
      </div>

      <div class="toolbar-row">
        <a-button size="mini" type="text" @click="selectAllVisible">选中当前筛选</a-button>
        <a-button size="mini" type="text" status="danger" @click="clearSelection">清空</a-button>
      </div>

      <div v-if="loading" class="panel-state">
        <a-spin />
        <span>加载引用中...</span>
      </div>

      <a-empty v-else-if="!filteredReferences.length" description="没有可用引用" />

      <div v-else class="reference-list">
        <article
          v-for="item in filteredReferences"
          :key="item.id"
          class="reference-card"
          :class="{ selected: isSelected(item.id) }"
          @click="toggleItem(item.id)"
        >
          <div class="reference-main">
            <a-checkbox :model-value="isSelected(item.id)" />
            <div class="reference-content">
              <div class="reference-title-row">
                <div class="reference-title">{{ item.title }}</div>
                <a-tag size="small" color="arcoblue">{{ item.reference_mode }}</a-tag>
              </div>
              <div v-if="item.summary" class="reference-summary">{{ item.summary }}</div>
              <div class="reference-meta">
                <a-tag size="small" color="gray">{{ item.source_type }}</a-tag>
                <a-tag v-if="item.source_module" size="small" color="gray">{{ item.source_module }}</a-tag>
                <a-tag v-if="item.source_id" size="small" color="gray">{{ item.source_id }}</a-tag>
              </div>
            </div>
          </div>

          <div v-if="item.tags?.length" class="reference-tags">
            <a-tag v-for="tag in item.tags" :key="tag" size="small">{{ tag }}</a-tag>
          </div>
        </article>
      </div>

      <div v-if="selectedReferences.length" class="selected-strip">
        <span class="selected-label">已选引用</span>
        <a-tag
          v-for="item in selectedReferences.slice(0, 6)"
          :key="item.id"
          size="small"
          closable
          @close.stop="toggleItem(item.id)"
        >
          {{ item.title }}
        </a-tag>
      </div>

      <div class="footer-row">
        <span>用于 patch 生成前的上下文补充</span>
        <a-button size="small" type="primary" @click="emit('confirm', selectedReferences)">确认引用</a-button>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { PropType } from 'vue'

type ReferenceItem = {
  id: string
  title: string
  summary?: string
  source_type?: string
  source_module?: string
  source_id?: string
  reference_mode?: string
  tags?: string[]
  [key: string]: any
}

const props = defineProps({
  title: { type: String, default: '引用选择器' },
  subtitle: { type: String, default: '从历史 Skill、工作流节点或模板中选择上下文' },
  modelValue: { type: Array as PropType<Array<string | number | ReferenceItem>>, default: () => [] },
  references: { type: Array as PropType<Array<string | ReferenceItem>>, default: () => [] },
  loading: { type: Boolean, default: false },
  query: { type: String, default: '' },
  sourceType: { type: String, default: 'all' },
  module: { type: String, default: 'all' },
  referenceMode: { type: String, default: 'all' },
})

const emit = defineEmits([
  'update:modelValue',
  'update:query',
  'update:sourceType',
  'update:module',
  'update:referenceMode',
  'confirm',
  'clear',
])

const queryText = ref<string>(props.query || '')
const sourceTypeValue = ref<string>(props.sourceType || 'all')
const moduleValue = ref<string>(props.module || 'all')
const modeValue = ref<string>(props.referenceMode || 'all')

watch(queryText, value => emit('update:query', value || ''), { immediate: true })
watch(() => props.query, value => { if (value !== queryText.value) queryText.value = value || '' })
watch(sourceTypeValue, value => { if (!value) sourceTypeValue.value = 'all' })
watch(moduleValue, value => { if (!value) moduleValue.value = 'all' })
watch(modeValue, value => { if (!value) modeValue.value = 'all' })
watch(() => props.sourceType, value => { sourceTypeValue.value = value || 'all' })
watch(() => props.module, value => { moduleValue.value = value || 'all' })
watch(() => props.referenceMode, value => { modeValue.value = value || 'all' })

const normalizedReferences = computed<ReferenceItem[]>(() => (Array.isArray(props.references) ? props.references : []).map(normalizeReference).filter(Boolean) as ReferenceItem[])

const filteredReferences = computed(() => {
  const q = queryText.value.trim().toLowerCase()
  return normalizedReferences.value.filter(item => {
    if (sourceTypeValue.value !== 'all' && item.source_type !== sourceTypeValue.value) return false
    if (moduleValue.value !== 'all' && item.source_module !== moduleValue.value) return false
    if (modeValue.value !== 'all' && item.reference_mode !== modeValue.value) return false
    if (!q) return true
    const haystack = [
      item.title,
      item.summary,
      item.source_type,
      item.source_module,
      item.source_id,
      item.reference_mode,
      ...(item.tags || []),
    ].filter(Boolean).join(' ').toLowerCase()
    return haystack.includes(q)
  })
})

const selectedReferences = computed(() => {
  const selected = new Set((Array.isArray(props.modelValue) ? props.modelValue : []).map(getReferenceId))
  return normalizedReferences.value.filter(item => selected.has(item.id))
})

const sourceTypeOptions = computed(() => [
  { label: '全部来源', value: 'all' },
  ...uniqueOptions(normalizedReferences.value.map(item => item.source_type).filter((v): v is string => !!v)),
])

const moduleOptions = computed(() => [
  { label: '全部模块', value: 'all' },
  ...uniqueOptions(normalizedReferences.value.map(item => item.source_module).filter((v): v is string => !!v)),
])

const modeOptions = computed(() => [
  { label: '全部方式', value: 'all' },
  ...uniqueOptions(normalizedReferences.value.map(item => item.reference_mode).filter((v): v is string => !!v)),
])

function normalizeReference(item: string | ReferenceItem | null | undefined): ReferenceItem | null {
  if (!item) return null
  if (typeof item === 'string') {
    return {
      id: item,
      title: item,
      summary: '',
      source_type: 'skill_module',
      source_module: '',
      source_id: item,
      reference_mode: 'copy_structure',
      tags: [],
    }
  }

  const sourceType = item.source_type || item.sourceType || 'skill_module'
  const sourceModule = item.source_module || item.sourceModule || item.module || ''
  const sourceId = item.source_id || item.sourceId || item.id || ''
  const referenceMode = item.reference_mode || item.referenceMode || 'copy_structure'
  const title = item.title || item.name || item.label || sourceId || sourceModule || '引用项'

  return {
    ...item,
    id: item.id || `${sourceType}:${sourceId || sourceModule || title}`,
    title,
    summary: item.summary || item.description || '',
    source_type: sourceType,
    source_module: sourceModule,
    source_id: sourceId,
    reference_mode: referenceMode,
    tags: Array.isArray(item.tags) ? item.tags : [],
  }
}

function getReferenceId(item: string | number | ReferenceItem | null | undefined): string {
  if (!item) return ''
  if (typeof item === 'string') return item
  if (typeof item === 'number') return String(item)
  return item.id || item.source_id || item.sourceId || ''
}

function uniqueOptions(values: string[]) {
  return Array.from(new Set(values.filter(Boolean))).map(value => ({ label: value, value }))
}

function isSelected(id: string) {
  return (Array.isArray(props.modelValue) ? props.modelValue : []).map(getReferenceId).includes(id)
}

function toggleItem(id: string) {
  const current = new Set((Array.isArray(props.modelValue) ? props.modelValue : []).map(getReferenceId))
  if (current.has(id)) current.delete(id)
  else current.add(id)
  emit('update:modelValue', Array.from(current))
}

function selectAllVisible() {
  const current = new Set((Array.isArray(props.modelValue) ? props.modelValue : []).map(getReferenceId))
  filteredReferences.value.forEach(item => current.add(item.id))
  emit('update:modelValue', Array.from(current))
}

function clearSelection() {
  emit('update:modelValue', [])
  emit('clear')
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

.panel-header,
.footer-row,
.toolbar-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
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
  flex-shrink: 0;
}

.panel-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.filter-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.panel-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 120px;
  color: var(--ai-ink-3);
}

.reference-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.reference-card {
  padding: 12px;
  border-radius: 12px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  cursor: pointer;
  transition: border-color 0.15s ease, transform 0.15s ease, box-shadow 0.15s ease;
}

.reference-card:hover {
  border-color: rgba(22, 93, 255, 0.28);
  transform: translateY(-1px);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.05);
}

.reference-card.selected {
  border-color: rgba(22, 93, 255, 0.45);
  background: rgba(22, 93, 255, 0.05);
}

.reference-main {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.reference-content {
  min-width: 0;
  flex: 1;
}

.reference-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.reference-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
}

.reference-summary {
  margin-top: 4px;
  font-size: 12px;
  color: var(--ai-ink-3);
  line-height: 1.5;
  word-break: break-word;
}

.reference-meta,
.reference-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.selected-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 10px 12px;
  border-radius: 12px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
}

.selected-label {
  font-size: 12px;
  color: var(--ai-ink-3);
  margin-right: 4px;
}

.footer-row {
  padding-top: 2px;
  border-top: 1px solid var(--ai-border);
  font-size: 12px;
  color: var(--ai-ink-3);
}

@media (max-width: 960px) {
  .filter-row {
    grid-template-columns: 1fr;
  }
}
</style>
