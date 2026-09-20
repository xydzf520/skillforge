<template>
  <section class="wb-panel">
    <header class="panel-header">
      <div>
        <div class="panel-title">{{ title }}</div>
        <div class="panel-subtitle">{{ subtitle }}</div>
      </div>
      <div class="header-actions">
        <a-tag :color="statusColor" size="small">{{ statusLabel }}</a-tag>
        <a-button size="mini" type="text" :loading="loading" @click="emit('refresh')">刷新</a-button>
      </div>
    </header>

    <div class="summary-grid">
      <div v-for="card in summaryCards" :key="card.label" class="summary-card">
        <div class="summary-label">{{ card.label }}</div>
        <div class="summary-value">{{ card.value }}</div>
      </div>
    </div>

    <div v-if="loading" class="panel-state">
      <a-spin />
      <span>正在校验工作台内容...</span>
    </div>

    <template v-else>
      <a-empty v-if="!hasReport" description="暂无验证结果">
        <a-button type="primary" size="mini" @click="emit('refresh')">开始验证</a-button>
      </a-empty>

      <template v-else>
        <div v-if="data.summary" class="report-summary">{{ data.summary }}</div>

        <div v-for="section in sections" :key="section.key" class="result-section">
          <div class="section-header">
            <div class="section-title">{{ section.label }}</div>
            <a-tag size="small">{{ section.items.length }}</a-tag>
          </div>

          <div v-if="section.items.length" class="result-list">
            <article
              v-for="item in section.items"
              :key="item.key"
              class="result-item"
              :class="`status-${item.status}`"
            >
              <div class="item-badge">
                <icon-check-circle v-if="item.status === 'success'" />
                <icon-exclamation-circle v-else-if="item.status === 'warning'" />
                <icon-close-circle v-else />
              </div>
              <div class="item-body">
                <div class="item-title">{{ item.title }}</div>
                <div v-if="item.message" class="item-message">{{ item.message }}</div>
                <div v-if="item.detail" class="item-detail">{{ item.detail }}</div>
              </div>
            </article>
          </div>
          <div v-else class="section-empty">暂无内容</div>
        </div>

        <div class="impact-card">
          <div class="section-header">
            <div class="section-title">影响摘要</div>
            <a-tag :color="impactTagColor" size="small">{{ impactLabel }}</a-tag>
          </div>
          <div class="impact-grid">
            <div class="impact-item"><span>提升</span><b>{{ impactSummary.improved }}</b></div>
            <div class="impact-item"><span>回退</span><b>{{ impactSummary.regressed }}</b></div>
            <div class="impact-item"><span>不变</span><b>{{ impactSummary.unchanged }}</b></div>
          </div>
        </div>
      </template>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import { IconCheckCircle, IconCloseCircle, IconExclamationCircle } from '@arco-design/web-vue/es/icon'

const props = defineProps({
  title: { type: String, default: '验证结果' },
  subtitle: { type: String, default: '结构验证 / 样例验证 / 历史回放' },
  report: { type: Object as PropType<any>, default: () => ({}) },
  validation: { type: Object as PropType<any>, default: () => ({}) },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['refresh'])

const data = computed(() => {
  const primary = props.report && Object.keys(props.report).length ? props.report : props.validation
  return primary || {}
})

const hasReport = computed(() => {
  const value = data.value
  if (!value) return false
  if (Array.isArray(value.structural_checks) && value.structural_checks.length) return true
  if (Array.isArray(value.sample_case_checks) && value.sample_case_checks.length) return true
  if (Array.isArray(value.historical_replay_checks) && value.historical_replay_checks.length) return true
  if (value.blocks && typeof value.blocks === 'object') return Object.keys(value.blocks).length > 0
  return false
})

const structuralChecks = computed(() => normalizeItems(data.value.structural_checks || data.value.blocks || [], 'structural', '结构验证'))
const sampleChecks = computed(() => normalizeItems(data.value.sample_case_checks || [], 'sample', '样例验证'))
const replayChecks = computed(() => normalizeItems(data.value.historical_replay_checks || [], 'replay', '历史回放'))
const impactSummary = computed(() => data.value.impact_summary || { improved: 0, regressed: 0, unchanged: 0 })

const sections = computed(() => [
  { key: 'structural', label: '结构验证', items: structuralChecks.value },
  { key: 'sample', label: '样例验证', items: sampleChecks.value },
  { key: 'replay', label: '历史回放', items: replayChecks.value },
])

const summaryCards = computed(() => [
  { label: '结构', value: structuralChecks.value.length },
  { label: '样例', value: sampleChecks.value.length },
  { label: '回放', value: replayChecks.value.length },
  { label: '可应用', value: data.value.can_apply === true ? '是' : data.value.can_apply === false ? '否' : '-' },
])

const statusLabel = computed(() => {
  if (props.loading) return '校验中'
  if (!hasReport.value) return '空'
  if (data.value.can_apply === false) return '不可应用'
  if (countByStatus('error') > 0) return '错误'
  if (countByStatus('warning') > 0) return '警告'
  return '通过'
})

const statusColor = computed(() => {
  if (props.loading) return 'gray'
  if (!hasReport.value) return 'gray'
  if (data.value.can_apply === false || countByStatus('error') > 0) return 'red'
  if (countByStatus('warning') > 0) return 'orange'
  return 'green'
})

const impactLabel = computed(() => {
  if ((impactSummary.value.regressed || 0) > 0) return '存在回退'
  if ((impactSummary.value.improved || 0) > 0) return '有提升'
  return '中性'
})

const impactTagColor = computed(() => {
  if ((impactSummary.value.regressed || 0) > 0) return 'red'
  if ((impactSummary.value.improved || 0) > 0) return 'green'
  return 'gray'
})

function normalizeItems(items: any, fallbackPrefix: string, fallbackTitle: string) {
  if (items && typeof items === 'object' && !Array.isArray(items)) {
    return Object.entries(items).map(([key, value], index) => normalizeItem(value, key || `${fallbackPrefix}-${index}`, fallbackPrefix, index, fallbackTitle))
  }
  return (Array.isArray(items) ? items : []).map((item, index) => normalizeItem(item, `${fallbackPrefix}-${index}`, fallbackPrefix, index, fallbackTitle))
}

function normalizeItem(item: any, key: string, fallbackPrefix: string, index: number, fallbackTitle: string) {
  if (typeof item === 'string') {
    return {
      key,
      title: item,
      status: 'info',
      message: '',
      detail: '',
    }
  }

  if (!item || typeof item !== 'object') {
    return {
      key,
      title: fallbackTitle || `检查 ${index + 1}`,
      status: 'info',
      message: '',
      detail: '',
    }
  }

  const status = String(item.status || (item.ok === false ? 'error' : item.warning ? 'warning' : 'success')).toLowerCase()
  return {
    ...item,
    key: item.key || item.id || `${fallbackPrefix}-${index}`,
    title: item.title || item.name || item.block || item.section || fallbackTitle || `检查 ${index + 1}`,
    status,
    message: item.message || item.detail || item.reason || '',
    detail: item.detail || item.explanation || '',
  }
}

function countByStatus(targetStatus: string) {
  const all = [...structuralChecks.value, ...sampleChecks.value, ...replayChecks.value]
  return all.filter(item => String(item.status || '').toLowerCase() === targetStatus).length
}
</script>

<style scoped>
.wb-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--ai-border);
  border-radius: 12px;
  background: var(--ai-surface);
  box-shadow: 0 12px 32px var(--ai-surface-2);
}

.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.panel-title {
  font-size: 15px;
  font-weight: 800;
  color: var(--ai-ink-1);
  letter-spacing: -0.01em;
}

.panel-subtitle {
  margin-top: 4px;
  font-size: 12px;
  color: var(--ai-ink-3);
  font-weight: 600;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.summary-grid,
.impact-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.summary-card,
.impact-item {
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}

.summary-label,
.impact-item span {
  font-size: 11px;
  color: var(--ai-ink-3);
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.summary-value,
.impact-item b {
  display: block;
  margin-top: 4px;
  font-size: 18px;
  line-height: 1.2;
  color: var(--ai-ink-1);
  font-weight: 800;
}

.panel-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 120px;
  color: var(--ai-ink-3);
  font-weight: 700;
}

.report-summary {
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(198, 106, 20, 0.08);
  border: 1px solid rgba(198, 106, 20, 0.20);
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 700;
}

.result-section,
.impact-card {
  padding: 12px;
  border-radius: 8px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
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
  font-weight: 800;
  color: var(--ai-ink-1);
}

.result-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.result-item {
  display: flex;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
}

.result-item.status-success {
  border-color: rgba(15, 143, 111, 0.22);
}

.result-item.status-warning {
  border-color: rgba(198, 106, 20, 0.28);
}

.result-item.status-error {
  border-color: rgba(191, 63, 63, 0.28);
}

.item-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  flex-shrink: 0;
}

.result-item.status-success .item-badge {
  color: var(--ai-ok);
  background: var(--ai-ok-soft);
}

.result-item.status-warning .item-badge {
  color: var(--ai-warn);
  background: var(--ai-warn-soft);
}

.result-item.status-error .item-badge {
  color: var(--ai-bad);
  background: var(--ai-bad-soft);
}

.item-body {
  min-width: 0;
  flex: 1;
}

.item-title {
  font-size: 13px;
  font-weight: 800;
  color: var(--ai-ink-1);
}

.item-message,
.item-detail {
  margin-top: 4px;
  font-size: 12px;
  color: var(--ai-ink-2);
  line-height: 1.5;
  word-break: break-word;
  font-weight: 600;
}

.section-empty {
  padding: 12px 0;
  font-size: 12px;
  color: var(--ai-ink-3);
  font-weight: 700;
}

@media (max-width: 960px) {
  .summary-grid,
  .impact-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
