<template>
  <div class="portal-submission">
    <a-spin :loading="loading" style="width: 100%">
      <div class="page-detail-toolbar">
        <a-button size="small" @click="$router.back()">
          <template #icon><icon-left /></template>返回
        </a-button>
      </div>

      <section class="report-hero">
        <div class="report-hero-main">
          <div class="report-kicker">
            <span>Portal · 执行结果</span>
            <span v-if="submission?.org_unit_name || submission?.org_unit_id">{{ submission?.org_unit_name || submission?.org_unit_id }}</span>
            <span v-if="triggerLabel">{{ triggerLabel }}</span>
          </div>
          <h1 class="report-title">{{ submission?.skill_display_name || submission?.skill_id || 'Skill 执行详情' }}</h1>
          <p class="report-summary">{{ heroSummary }}</p>
          <div class="report-meta">
            <span class="mono">{{ submission?.id || route.params.id || '-' }}</span>
            <span class="mono">{{ submission?.skill_id || '-' }}</span>
            <span>{{ submission?.org_unit_name || submission?.org_unit_id || '-' }}</span>
            <span class="mono">{{ submission?.execution_id || '-' }}</span>
          </div>
          <div class="report-tag-row">
            <a-tag :color="submissionStatusColor(submission?.status)">{{ submission?.status || 'loading' }}</a-tag>
            <a-tag v-if="submission?.ui_pref_id" color="arcoblue">个人界面 v{{ submission?.ui_pref_version || 1 }}</a-tag>
            <a-tag v-else-if="submission" color="gray">默认界面</a-tag>
            <a-tag v-if="shortUiHash" color="gray" class="mono">hash {{ shortUiHash }}</a-tag>
          </div>
        </div>
        <div class="report-hero-side">
          <div class="hero-side-label">提交时间</div>
          <div class="hero-side-time">{{ formatTime(submission?.created_at) || '-' }}</div>
          <div class="hero-side-label">完成时间</div>
          <div class="hero-side-time">{{ formatTime(submission?.completed_at) || '-' }}</div>
          <div class="hero-actions">
            <button
              v-if="canInspectRun"
              type="button"
              class="ai-btn"
              :disabled="traceLoading"
              @click="loadRunTrace"
            >
              <a-spin v-if="traceLoading" size="mini" />
              运行追溯
            </button>
            <button
              v-if="canInspectRun"
              type="button"
              class="ai-btn"
              :disabled="analysisLoading"
              @click="analyzeRun"
            >
              <a-spin v-if="analysisLoading" size="mini" />
              <icon-robot v-else />
              AI 复盘
            </button>
            <button
              v-if="canCreateTrainingCandidate"
              type="button"
              class="ai-btn primary"
              :disabled="candidateCreating"
              @click="createTrainingCandidate"
            >
              <a-spin v-if="candidateCreating" size="mini" />
              训练候选
            </button>
          </div>
        </div>
      </section>

      <a-result v-if="loadError" status="error" :title="loadError" class="sf-card" />

      <template v-else>
        <section v-if="isRunning" class="sf-card sf-card-running">
          <a-spin />
          <div class="running-text">Skill 正在执行中，请稍候...</div>
        </section>

        <section v-else-if="submission?.status === 'failed'" class="sf-card">
          <div class="section-eyebrow">执行失败</div>
          <a-result status="error" :title="submission.error_message || '执行失败'" />
        </section>

        <section v-else class="sf-card">
          <div class="section-eyebrow">执行结果</div>
          <template v-if="visibleResultComponents.length">
            <div
              v-for="component in visibleResultComponents"
              :key="component.id"
              class="result-component"
            >
              <div v-if="component.title && !['metric', 'alert'].includes(component.type)" class="result-component-title">
                {{ component.title }}
              </div>

              <template v-if="isChartComponent(component) && chartValuesFor(component).length">
                <div class="result-panel">
                  <TrendChart
                    :labels="chartLabelsFor(component)"
                    :values="chartValuesFor(component)"
                    :kind="chartKindFor(component)"
                  />
                </div>
              </template>
              <a-empty v-else-if="isChartComponent(component)" description="暂无可视化结果" />

              <template v-else-if="component.type === 'table' && resultRowsFor(component).length">
                <a-table :data="resultRowsFor(component)" :pagination="false" row-key="_rowKey" class="sf-table">
                  <template #columns>
                    <a-table-column v-for="column in resultColumnsFor(component)" :key="column.key" :title="column.title">
                      <template #cell="{ record }">
                        {{ formatPortalCell(record[column.key], column.format) }}
                      </template>
                    </a-table-column>
                  </template>
                </a-table>
              </template>
              <a-empty v-else-if="component.type === 'table'" description="暂无结构化结果" />

              <div v-else-if="component.type === 'metric'" class="metric-tile">
                <div class="metric-title">{{ component.title || component.id }}</div>
                <div class="metric-value mono">{{ metricValue(component) }}</div>
                <div v-if="component.description" class="metric-desc">{{ component.description }}</div>
              </div>

              <div v-else-if="component.type === 'metric_group'" class="metric-grid">
                <div
                  v-for="child in childMetricComponents(component)"
                  :key="child.id"
                  class="metric-tile"
                >
                  <div class="metric-title">{{ child.title || child.id }}</div>
                  <div class="metric-value mono">{{ metricValue(child) }}</div>
                  <div v-if="child.description" class="metric-desc">{{ child.description }}</div>
                </div>
              </div>

              <div
                v-else-if="component.type === 'alert'"
                class="result-alert"
              >
                {{ componentText(component) }}
              </div>

              <div v-else class="result-text result-panel">{{ componentText(component) }}</div>
            </div>
          </template>

          <template v-else-if="resultMode === 'chart' && chartValues.length">
            <div class="result-panel">
              <TrendChart :labels="chartLabels" :values="chartValues" :kind="chartKind" />
            </div>
          </template>
          <a-empty v-else-if="resultMode === 'chart'" description="暂无可视化结果" />

          <template v-else-if="resultMode === 'table' && resultRows.length">
            <a-table :data="resultRows" :pagination="false" row-key="_rowKey" class="sf-table">
              <template #columns>
                <a-table-column v-for="column in resultColumns" :key="column.key" :title="column.title">
                  <template #cell="{ record }">
                    {{ formatPortalCell(record[column.key], column.format) }}
                  </template>
                </a-table-column>
              </template>
            </a-table>
          </template>
          <a-empty v-else-if="resultMode === 'table'" description="暂无结构化结果" />

          <template v-else>
            <div class="result-text result-panel">{{ resultText }}</div>
          </template>

          <div v-if="resultAnalysis" class="result-alert result-analysis">{{ resultAnalysis }}</div>
        </section>

        <section
          v-if="canInspectRun && (traceSummary || runAnalysis || traceError || analysisError)"
          class="sf-card"
        >
          <div class="section-eyebrow">运行追溯与 AI 复盘</div>
          <div v-if="traceError" class="result-warn">{{ traceError }}</div>
          <div v-if="analysisError" class="result-warn">{{ analysisError }}</div>
          <div v-if="traceSummary" class="trace-summary-grid">
            <div class="trace-summary-tile">
              <span>采集 Proof</span>
              <strong class="mono">{{ traceSummary.collectionProofs }}</strong>
            </div>
            <div class="trace-summary-tile">
              <span>AI 调用</span>
              <strong class="mono">{{ traceSummary.analyzeRuns }}</strong>
            </div>
            <div class="trace-summary-tile">
              <span>决策记录</span>
              <strong class="mono">{{ traceSummary.decisionLogs }}</strong>
            </div>
            <div class="trace-summary-tile">
              <span>状态</span>
              <strong class="mono">{{ traceSummary.status }}</strong>
            </div>
          </div>
          <template v-if="runAnalysis">
            <div class="analysis-meta">
              <a-tag color="arcoblue">{{ runAnalysis.model || 'platform-ai' }}</a-tag>
              <span v-if="analysisRawCountsText" class="mono">{{ analysisRawCountsText }}</span>
              <span v-if="runAnalysis.prompt_hash" class="mono">prompt {{ runAnalysis.prompt_hash }}</span>
            </div>
            <pre class="analysis-output mono">{{ analysisText }}</pre>
          </template>
        </section>

        <section class="sf-card">
          <div class="section-eyebrow">运行参数</div>
          <a-empty v-if="paramEntries.length === 0" description="暂无参数" />
          <div v-else class="param-list">
            <div v-for="[key, value] in paramEntries" :key="key" class="param-row">
              <div class="param-key mono">{{ key }}</div>
              <div class="param-val">{{ formatParamValue(value) }}</div>
            </div>
          </div>
        </section>

        <section class="sf-card">
          <div class="section-eyebrow">执行信息</div>
          <div class="param-list">
            <div class="param-row">
              <div class="param-key">提交时间</div>
              <div class="param-val mono">{{ formatTime(submission?.created_at) || '-' }}</div>
            </div>
            <div class="param-row">
              <div class="param-key">完成时间</div>
              <div class="param-val mono">{{ formatTime(submission?.completed_at) || '-' }}</div>
            </div>
            <div class="param-row">
              <div class="param-key">Skill</div>
              <div class="param-val">{{ submission?.skill_display_name || submission?.skill_id || '-' }}</div>
            </div>
            <div class="param-row">
              <div class="param-key">执行 ID</div>
              <div class="param-val mono">{{ submission?.execution_id || '-' }}</div>
            </div>
            <div class="param-row">
              <div class="param-key">提交者</div>
              <div class="param-val">{{ submission?.requester_name || submission?.requester_id || '-' }}</div>
            </div>
            <div class="param-row">
              <div class="param-key">组织</div>
              <div class="param-val">{{ submission?.org_unit_name || submission?.org_unit_id || '-' }}</div>
            </div>
            <div class="param-row">
              <div class="param-key">界面版本</div>
              <div class="param-val">{{ uiVersionText }}</div>
            </div>
            <div class="param-row">
              <div class="param-key">Skill Commit</div>
              <div class="param-val mono">{{ submission?.base_skill_commit || '-' }}</div>
            </div>
            <div class="param-row">
              <div class="param-key">界面 Hash</div>
              <div class="param-val mono">{{ shortUiHash || '-' }}</div>
            </div>
          </div>
        </section>
      </template>
    </a-spin>

    <button
      v-if="canOpenResultUiDesigner"
      type="button"
      class="ui-floating-ai"
      :disabled="!canCustomizeResultUi"
      aria-label="AI 调整结果页面"
      title="AI 调整结果页面"
      @click="openResultUiDesigner"
    >
      <icon-robot />
    </button>

    <a-modal
      v-model:visible="resultUiDesignerVisible"
      title="AI 对话调整结果页面"
      :footer="false"
      :width="'min(92vw, 860px)'"
      class="ui-designer-modal"
    >
      <div class="ui-designer">
        <div class="ui-designer-head">
          <strong>结果页面</strong>
          <div class="ui-meta">
            <a-tag :color="resultUiStatusColor">
              {{ resultUiStatusLabel }}
            </a-tag>
            <span v-if="resultUi?.merged_ui_schema_hash" class="ui-hash mono">
              {{ resultUiShortHash }}
            </span>
          </div>
        </div>

        <div class="ui-chat-log">
          <div
            v-for="(message, index) in resultUiChatMessages"
            :key="`${message.role}-${index}-${message.content}`"
            class="ui-chat-message"
            :class="`is-${message.role}`"
          >
            <span class="ui-chat-role">{{ message.role === 'user' ? '你' : 'AI' }}</span>
            <div class="ui-chat-bubble">{{ message.content }}</div>
          </div>
        </div>

        <div class="ui-chat-compose">
          <a-textarea
            v-model="resultUiDesignerInput"
            :auto-size="{ minRows: 3, maxRows: 5 }"
            placeholder="例如：把结果改成指标卡加趋势图，表格只保留日期、花费、ROI"
            :disabled="!canCustomizeResultUi"
          />
          <div class="ui-actions">
            <button
              type="button"
              class="ai-btn primary"
              :disabled="!canCustomizeResultUi || !resultUiDesignerInput.trim() || resultUiPreviewing"
              @click="sendResultUiDesignerMessage"
            >
              <a-spin v-if="resultUiPreviewing" size="mini" />
              <icon-send v-else />
              发送给 AI
            </button>
            <button
              type="button"
              class="ai-btn"
              :disabled="!canSaveResultUiPreference || resultUiSaving"
              @click="saveResultUiDesigner"
            >
              <a-spin v-if="resultUiSaving" size="mini" />
              <icon-save v-else />
              保存到我的页面
            </button>
            <button
              type="button"
              class="ai-btn"
              :disabled="!canResetResultUiPreference || resultUiResetting"
              @click="resetResultUiDesigner"
            >
              <a-spin v-if="resultUiResetting" size="mini" />
              <icon-refresh v-else />
              恢复默认
            </button>
          </div>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { IconLeft, IconRefresh, IconRobot, IconSave, IconSend } from '@arco-design/web-vue/es/icon'
import {
  portalApi as rawPortalApi,
  runTraceApi as rawRunTraceApi,
  trainingApi as rawTrainingApi,
} from '@/api'
import { formatTime } from '@/utils/format'
import {
  formatPortalCell,
  isRecord,
  normalizeListResponse,
  type PortalResultColumn,
  type PortalResultSchema,
  type PortalSkillUI,
  type PortalUIComponent,
} from './shared'

defineOptions({ name: 'PortalSubmissionDetail' })

const TrendChart = defineAsyncComponent(() => import('@/components/charts/TrendChart.vue'))

type Submission = {
  id: string
  skill_id?: string
  skill_display_name?: string
  requester_id?: string
  requester_name?: string
  org_unit_id?: string
  org_unit_name?: string
  status?: string
  params?: Record<string, unknown>
  result_summary?: Record<string, unknown> & { type?: string; data?: any[]; analysis?: string; text?: string; content?: string }
  result_ui_schema?: PortalResultSchema
  execution_id?: string
  created_at?: string
  completed_at?: string
  error_message?: string
  ui_pref_id?: string | null
  ui_pref_version?: number | null
  ui_surface?: string | null
  base_skill_commit?: string | null
  merged_ui_schema_hash?: string | null
  ui_snapshot_json?: Record<string, unknown> | null
  trigger_type?: string | null
}
type ResultUiChatMessage = {
  role: 'assistant' | 'user'
  content: string
}
type PreviewUiOutcome = {
  ok: boolean
  fallback?: boolean
  error?: string
}

const portalApi: any = rawPortalApi
const runTraceApi: any = rawRunTraceApi
const trainingApi: any = rawTrainingApi
const route = useRoute()
const router = useRouter()
const loading = ref(false)
const loadError = ref('')
const submission = ref<Submission | null>(null)
const resultUi = ref<PortalSkillUI | null>(null)
const resultUiPreviewing = ref(false)
const resultUiSaving = ref(false)
const resultUiResetting = ref(false)
const resultUiInstruction = ref('')
const resultUiOverlayDraft = ref<Record<string, unknown> | null>(null)
const resultUiDesignerVisible = ref(false)
const resultUiDesignerInput = ref('')
const resultUiChatMessages = ref<ResultUiChatMessage[]>([])
const traceLoading = ref(false)
const traceError = ref('')
const traceSummary = ref<{ collectionProofs: number; analyzeRuns: number; decisionLogs: number; status: string } | null>(null)
const analysisLoading = ref(false)
const analysisError = ref('')
const runAnalysis = ref<Record<string, any> | null>(null)
const candidateCreating = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

const isRunning = computed(() => ['pending', 'running'].includes(submission.value?.status || ''))
const executionRunId = computed(() => String(submission.value?.execution_id || '').trim())
const canInspectRun = computed(() => Boolean(executionRunId.value && !isRunning.value))
const canCreateTrainingCandidate = computed(() => Boolean(canInspectRun.value && submission.value?.skill_id && submission.value?.status !== 'failed'))
const canOpenResultUiDesigner = computed(() => Boolean(!isRunning.value && submission.value?.status !== 'failed' && submission.value?.skill_id))
const canCustomizeResultUi = computed(() => Boolean(resultUi.value?.permissions?.customize_ui))
const hasUnsavedResultUiPreview = computed(() => Boolean(resultUiOverlayDraft.value))
const hasResultUiPromptChange = computed(() => resultUiInstruction.value.trim() !== (resultUi.value?.saved_prompt || '').trim())
const canSaveResultUiPreference = computed(() => canCustomizeResultUi.value && (hasUnsavedResultUiPreview.value || hasResultUiPromptChange.value))
const canResetResultUiPreference = computed(() => canCustomizeResultUi.value && Boolean(resultUi.value?.ui_pref_id))
const resultUiStatusLabel = computed(() => {
  if (hasUnsavedResultUiPreview.value) return '预览中'
  return resultUi.value?.ui_pref_id ? '个人版' : '默认版'
})
const resultUiStatusColor = computed(() => {
  if (resultUiStatusLabel.value === '预览中') return 'orange'
  if (resultUiStatusLabel.value === '个人版') return 'arcoblue'
  return 'gray'
})
const resultComponents = computed<PortalUIComponent[]>(() => resultUi.value?.merged_schema?.components || [])
const visibleResultComponents = computed(() => flattenVisibleResultComponents(resultComponents.value))
const primaryResultComponent = computed(() => visibleResultComponents.value[0])
const resultMode = computed(() => {
  const componentType = primaryResultComponent.value?.type
  if (componentType === 'table') return 'table'
  if (['line_chart', 'bar_chart', 'pie_chart'].includes(componentType || '')) return 'chart'
  return submission.value?.result_ui_schema?.type || submission.value?.result_summary?.type || 'text'
})
const resultRows = computed(() => {
  const rawRows = submission.value?.result_summary?.data || submission.value?.result_summary?.rows || []
  return normalizeListResponse<Record<string, unknown>>(rawRows)
    .map((row, index) => ({ _rowKey: `${index}`, ...row }) as Record<string, unknown> & { _rowKey: string })
})
const resultColumns = computed<Array<{ key: string; title: string; format?: string }>>(() => {
  const componentColumns = primaryResultComponent.value?.type === 'table' && Array.isArray(primaryResultComponent.value.columns)
    ? primaryResultComponent.value.columns as Array<{ key: string; title: string; format?: string }>
    : []
  if (componentColumns.length) return componentColumns
  const schemaColumns = submission.value?.result_ui_schema?.columns || []
  if (schemaColumns.length) return schemaColumns
  const firstRow = resultRows.value[0]
  if (!firstRow) return []
  return Object.keys(firstRow)
    .filter(key => key !== '_rowKey')
    .map(key => ({ key, title: key }))
})
const chartKind = computed<'line' | 'bar' | 'pie'>(() => {
  const componentType = primaryResultComponent.value?.type
  if (componentType === 'bar_chart') return 'bar'
  if (componentType === 'pie_chart') return 'pie'
  return 'line'
})
const chartKeys = computed(() => {
  const rows = resultRows.value
  const columns = resultColumns.value
  const firstRow = rows[0]
  if (!firstRow) return { labelKey: '', valueKey: '' }
  const keys = columns.length
    ? columns.map(column => column.key).filter(key => key in firstRow)
    : Object.keys(firstRow).filter(key => key !== '_rowKey')
  const valueKey = keys.find(key => rows.some(row => Number.isFinite(Number(row[key])))) || ''
  const labelKey = keys.find(key => key !== valueKey) || valueKey
  return { labelKey, valueKey }
})
const chartLabels = computed(() => {
  const key = chartKeys.value.labelKey
  return resultRows.value.map((row, index) => String(key ? row[key] ?? index + 1 : index + 1))
})
const chartValues = computed(() => {
  const key = chartKeys.value.valueKey
  return resultRows.value
    .map(row => Number(key ? row[key] : 0))
    .filter(value => Number.isFinite(value))
})
const resultText = computed(() => {
  const result = submission.value?.result_summary
  if (!result) return '暂无结果'
  return String(result.analysis || result.text || result.content || JSON.stringify(result, null, 2))
})
const resultAnalysis = computed(() => submission.value?.result_summary?.analysis || '')
const analysisText = computed(() => {
  const value = runAnalysis.value?.analysis
  if (typeof value === 'string') return value
  if (value == null) return ''
  return JSON.stringify(value, null, 2)
})
const analysisRawCountsText = computed(() => {
  const counts = runAnalysis.value?.raw_counts
  if (!isRecord(counts)) return ''
  const parts = Object.entries(counts)
    .filter(([, value]) => Number(value) > 0)
    .map(([key, value]) => `${key}:${value}`)
  return parts.join(' · ')
})
const paramEntries = computed(() => Object.entries(submission.value?.params || {}))
const uiVersionText = computed(() => {
  if (!submission.value?.ui_pref_id) return '默认界面'
  return `个人界面 v${submission.value.ui_pref_version || 1}`
})
const shortUiHash = computed(() => {
  const hash = submission.value?.merged_ui_schema_hash || ''
  return hash.startsWith('sha256:') ? hash.slice(7, 19) : hash.slice(0, 12)
})
const resultUiShortHash = computed(() => {
  const hash = resultUi.value?.merged_ui_schema_hash || ''
  return hash.startsWith('sha256:') ? hash.slice(7, 19) : hash.slice(0, 12)
})
const triggerLabel = computed(() => {
  const trigger = String(submission.value?.trigger_type || '').trim()
  if (!trigger) return ''
  const map: Record<string, string> = {
    manual: '手动触发',
    scheduler: '定时触发',
    'scheduler:fallback': '兜底补偿',
    node_scheduler: '节点定时',
    webhook: 'Webhook',
    api: 'API 触发',
  }
  return map[trigger] || trigger
})
const heroSummary = computed(() => {
  const status = submission.value?.status
  if (!submission.value) return '加载执行详情中...'
  if (isRunning.value) return 'Skill 正在执行中，请稍候。'
  if (status === 'failed') return submission.value?.error_message || '执行失败，请查看运行追溯。'
  if (status === 'completed') return resultAnalysis.value || '执行已完成，可查看结果输出与运行追溯。'
  return '执行详情'
})

function warnIfUiPreferenceInvalidated(ui: PortalSkillUI | null, label = '个人结果界面') {
  if (!ui?.ui_pref_invalidated?.ui_pref_id) return
  Message.warning(`${label}已因 Skill 界面结构变化失效，已恢复默认界面`)
}

function seedResultUiDesignerMessages(): void {
  if (resultUiChatMessages.value.length) return
  resultUiChatMessages.value.push({
    role: 'assistant',
    content: '告诉我这次执行结果页面要怎么呈现，我会生成个人预览。确认后保存，只影响你当前账号看到的这个 Skill 结果页面。',
  })
}

function openResultUiDesigner(): void {
  seedResultUiDesignerMessages()
  resultUiDesignerVisible.value = true
}

function isChartComponent(component: PortalUIComponent): boolean {
  return ['line_chart', 'bar_chart', 'pie_chart'].includes(component.type)
}

function flattenVisibleResultComponents(components: PortalUIComponent[], parentTitle = ''): PortalUIComponent[] {
  const flattened: PortalUIComponent[] = []
  for (const component of components) {
    if (component.visible === false) continue
    const children = Array.isArray(component.children) ? component.children : []
    if (['report_section', 'tabs'].includes(component.type) && children.length) {
      const sectionTitle = component.title || parentTitle
      flattened.push(...flattenVisibleResultComponents(children, sectionTitle))
      continue
    }
    if (parentTitle && component.title) {
      flattened.push({ ...component, title: `${parentTitle} / ${component.title}` })
    } else if (parentTitle && !component.title) {
      flattened.push({ ...component, title: parentTitle })
    } else {
      flattened.push(component)
    }
  }
  return flattened
}

function resultSummary(): Record<string, unknown> {
  return isRecord(submission.value?.result_summary) ? submission.value!.result_summary! : {}
}

function normalizeRows(rawRows: unknown): Array<Record<string, unknown> & { _rowKey: string }> {
  return normalizeListResponse<unknown>(rawRows)
    .map((row, index) => ({
      _rowKey: `${index}`,
      ...(isRecord(row) ? row : { value: row }),
    }))
}

function valueAtPath(source: unknown, path: string[]): unknown {
  let current = source
  for (const segment of path) {
    if (Array.isArray(current)) {
      current = current.map(item => valueAtPath(item, [segment]))
      continue
    }
    if (!isRecord(current)) return undefined
    current = current[segment]
  }
  return current
}

function resultBindingValue(binding?: string): unknown {
  if (!binding) return resultSummary()
  if (!binding.startsWith('result.')) return undefined
  return valueAtPath(resultSummary(), binding.slice('result.'.length).split('.').filter(Boolean))
}

function resultRowsFor(component?: PortalUIComponent): Array<Record<string, unknown> & { _rowKey: string }> {
  const bound = resultBindingValue(component?.binding)
  if (Array.isArray(bound)) return normalizeRows(bound)
  if (isRecord(bound)) {
    const nestedRows = bound.data || bound.rows || bound.items
    if (Array.isArray(nestedRows)) return normalizeRows(nestedRows)
  }
  return resultRows.value
}

function resultColumnsFor(component?: PortalUIComponent): PortalResultColumn[] {
  const componentColumns = Array.isArray(component?.columns) ? component!.columns as PortalResultColumn[] : []
  if (componentColumns.length) return componentColumns
  const schemaColumns = submission.value?.result_ui_schema?.columns || []
  if (schemaColumns.length) return schemaColumns
  const firstRow = resultRowsFor(component)[0]
  if (!firstRow) return []
  return Object.keys(firstRow)
    .filter(key => key !== '_rowKey')
    .map(key => ({ key, title: key }))
}

function rowKeyFromBinding(binding: string | undefined, rowsBinding = 'result.data'): string {
  if (!binding) return ''
  if (binding.startsWith(`${rowsBinding}.`)) return binding.slice(rowsBinding.length + 1)
  if (binding.startsWith('result.data.')) return binding.slice('result.data.'.length)
  if (binding.startsWith('result.rows.')) return binding.slice('result.rows.'.length)
  return ''
}

function chartKeysFor(component?: PortalUIComponent): { labelKey: string; valueKey: string } {
  const rows = resultRowsFor(component)
  const firstRow = rows[0]
  if (!firstRow) return { labelKey: '', valueKey: '' }
  const bindingMap = component?.bindings || {}
  const explicitLabelKey = rowKeyFromBinding(bindingMap.label || bindingMap.x, component?.binding)
  const explicitValueKey = rowKeyFromBinding(bindingMap.value || bindingMap.y, component?.binding)
  const columns = resultColumnsFor(component)
  const keys = columns.length
    ? columns.map(column => column.key).filter(key => key in firstRow)
    : Object.keys(firstRow).filter(key => key !== '_rowKey')
  const valueKey = explicitValueKey || keys.find(key => rows.some(row => Number.isFinite(Number(row[key])))) || ''
  const labelKey = explicitLabelKey || keys.find(key => key !== valueKey) || valueKey
  return { labelKey, valueKey }
}

function chartKindFor(component?: PortalUIComponent): 'line' | 'bar' | 'pie' {
  if (component?.type === 'bar_chart' || component?.chart_type === 'bar') return 'bar'
  if (component?.type === 'pie_chart' || component?.chart_type === 'pie') return 'pie'
  return 'line'
}

function chartLabelsFor(component?: PortalUIComponent): string[] {
  const rows = resultRowsFor(component)
  const key = chartKeysFor(component).labelKey
  return rows.map((row, index) => String(key ? row[key] ?? index + 1 : index + 1))
}

function chartValuesFor(component?: PortalUIComponent): number[] {
  const rows = resultRowsFor(component)
  const key = chartKeysFor(component).valueKey
  return rows
    .map(row => Number(key ? row[key] : 0))
    .filter(value => Number.isFinite(value))
}

function metricValue(component: PortalUIComponent): string {
  const value = resultBindingValue(component.binding)
  if (Array.isArray(value)) return String(value.length)
  if (isRecord(value)) return JSON.stringify(value)
  return formatPortalCell(value, component.format)
}

function childMetricComponents(component: PortalUIComponent): PortalUIComponent[] {
  return (component.children || []).filter(child => child.visible !== false && child.type === 'metric')
}

function componentText(component: PortalUIComponent): string {
  const value = resultBindingValue(component.binding)
  if (typeof value === 'string') return value
  if (value == null) return component.description || resultText.value
  if (isRecord(value) || Array.isArray(value)) return JSON.stringify(value, null, 2)
  return String(value)
}

async function loadSubmission() {
  const id = String(route.params.id || '')
  if (!id) return
  loading.value = true
  loadError.value = ''
  try {
    submission.value = await portalApi.getSubmission(id)
    traceError.value = ''
    analysisError.value = ''
    traceSummary.value = null
    runAnalysis.value = null
    await loadResultUi()
    schedulePolling()
  } catch (error: any) {
    submission.value = null
    loadError.value = error?._message || '加载执行结果失败'
    stopPolling()
  } finally {
    loading.value = false
  }
}

function decisionLogCount(value: unknown): number {
  if (Array.isArray(value)) return value.length
  if (value && typeof value === 'object') return 1
  return 0
}

async function loadRunTrace() {
  const runId = executionRunId.value
  if (!runId || traceLoading.value) return
  traceLoading.value = true
  traceError.value = ''
  try {
    const trace = await runTraceApi.getExecutionTrace(runId)
    traceSummary.value = {
      collectionProofs: normalizeListResponse(trace?.collection_proofs).length,
      analyzeRuns: normalizeListResponse(trace?.intelligence_analyze_runs).length,
      decisionLogs: decisionLogCount(trace?.decision_log),
      status: String(trace?.execution_run?.status || trace?._skillforge_meta?.status || '-'),
    }
  } catch (error: any) {
    traceError.value = error?._message || '运行追溯加载失败或无权查看'
    Message.warning(traceError.value)
  } finally {
    traceLoading.value = false
  }
}

async function analyzeRun() {
  const runId = executionRunId.value
  if (!runId || analysisLoading.value) return
  analysisLoading.value = true
  analysisError.value = ''
  try {
    runAnalysis.value = await runTraceApi.analyzeExecutionTrace(runId, {
      include_raw: false,
      max_output_tokens: 4096,
    })
    await loadRunTrace()
    Message.success('AI 复盘已生成')
  } catch (error: any) {
    analysisError.value = error?._message || 'AI 复盘失败或无权分析该运行'
    Message.warning(analysisError.value)
  } finally {
    analysisLoading.value = false
  }
}

async function createTrainingCandidate() {
  const runId = executionRunId.value
  const skillId = submission.value?.skill_id
  if (!runId || !skillId || candidateCreating.value) return
  if (!runAnalysis.value) {
    Message.warning('先完成 AI 复盘后再生成训练候选')
    return
  }
  candidateCreating.value = true
  try {
    const job = await trainingApi.createRunCandidate(runId, {
      model_family: `${skillId}:run_trace_improvement`,
      analysis_summary: analysisText.value.slice(0, 4000),
      analysis_model: runAnalysis.value.model,
      analysis_prompt_hash: runAnalysis.value.prompt_hash,
      raw_counts: runAnalysis.value.raw_counts || {},
    })
    Message.success('训练候选已创建')
    const jobId = String(job?.id || '')
    if (jobId) router.push(`/training/jobs/${encodeURIComponent(jobId)}`)
  } catch (error: any) {
    Message.warning(error?._message || '训练候选创建失败或无权编辑该 Skill')
  } finally {
    candidateCreating.value = false
  }
}

async function loadResultUi() {
  const skillId = submission.value?.skill_id
  if (!skillId) {
    resultUi.value = null
    resultUiInstruction.value = ''
    resultUiOverlayDraft.value = null
    return
  }
  try {
    resultUi.value = await portalApi.getSkillUi(skillId, { surface: 'result' })
    warnIfUiPreferenceInvalidated(resultUi.value)
    resultUiInstruction.value = typeof resultUi.value?.saved_prompt === 'string' ? resultUi.value.saved_prompt : ''
    if (resultUiDesignerVisible.value) {
      resultUiDesignerInput.value = resultUiInstruction.value
    }
    resultUiOverlayDraft.value = null
  } catch {
    resultUi.value = null
    resultUiInstruction.value = ''
    resultUiOverlayDraft.value = null
  }
}

async function previewResultUiInstruction(instruction: string, notify = true): Promise<PreviewUiOutcome> {
  const skillId = submission.value?.skill_id
  if (!skillId || !instruction) return { ok: false }
  resultUiPreviewing.value = true
  try {
    const res = await portalApi.previewSkillUi(skillId, {
      surface: 'result',
      instruction,
      current_overlay: resultUiOverlayDraft.value || resultUi.value?.overlay || {},
    })
    resultUi.value = res || resultUi.value
    resultUiOverlayDraft.value = (res?.overlay || {}) as Record<string, unknown>
    if (res?.ai_status === 'fallback') {
      if (notify) Message.warning('AI 输出未通过安全校验，已使用安全预览')
    } else {
      if (notify) Message.success('已生成结果界面预览')
    }
    return { ok: true, fallback: res?.ai_status === 'fallback' }
  } catch (error: any) {
    const message = error?._message || '生成失败'
    if (notify) Message.error(message)
    return { ok: false, error: message }
  } finally {
    resultUiPreviewing.value = false
  }
}

async function handlePreviewResultUi(): Promise<PreviewUiOutcome> {
  return previewResultUiInstruction(resultUiInstruction.value.trim())
}

async function handleSaveResultUi(): Promise<boolean> {
  const skillId = submission.value?.skill_id
  if (!skillId || !canSaveResultUiPreference.value) return false
  resultUiSaving.value = true
  try {
    const res = await portalApi.saveSkillUiPreference(skillId, {
      surface: 'result',
      overlay: resultUiOverlayDraft.value || resultUi.value?.overlay || {},
      generated_by: resultUiOverlayDraft.value ? 'ai' : resultUi.value?.generated_by || 'manual',
      prompt_summary: resultUiInstruction.value.trim() || undefined,
    })
    resultUi.value = res || resultUi.value
    resultUiInstruction.value = typeof res?.saved_prompt === 'string' ? res.saved_prompt : resultUiInstruction.value
    resultUiOverlayDraft.value = null
    Message.success('已保存个人结果界面')
    return true
  } catch (error: any) {
    if (error?._backendCode === 'UI_PREF_CONFLICT') {
      Message.warning(error?._backendMessage || '个人结果界面已被其他请求更新，请重新确认后保存')
      await loadResultUi()
      return false
    }
    Message.error(error?._message || '保存失败')
    return false
  } finally {
    resultUiSaving.value = false
  }
}

async function handleResetResultUi(): Promise<boolean> {
  const skillId = submission.value?.skill_id
  if (!skillId) return false
  resultUiResetting.value = true
  try {
    const res = await portalApi.deleteSkillUiPreference(skillId, { surface: 'result' })
    resultUi.value = res || null
    resultUiInstruction.value = typeof res?.saved_prompt === 'string' ? res.saved_prompt : ''
    resultUiOverlayDraft.value = null
    Message.success('已恢复默认结果界面')
    return true
  } catch (error: any) {
    Message.error(error?._message || '重置失败')
    return false
  } finally {
    resultUiResetting.value = false
  }
}

async function sendResultUiDesignerMessage(): Promise<void> {
  const instruction = resultUiDesignerInput.value.trim()
  if (!instruction || !canCustomizeResultUi.value) return
  resultUiInstruction.value = instruction
  resultUiChatMessages.value.push({ role: 'user', content: instruction })
  resultUiDesignerInput.value = ''

  const outcome = await previewResultUiInstruction(instruction, false)
  if (outcome.ok) {
    resultUiChatMessages.value.push({
      role: 'assistant',
      content: outcome.fallback
        ? 'AI 输出没有通过安全校验，已生成安全预览。可以继续补充要求，或保存当前预览。'
        : '已更新结果页面预览。确认没问题后，可以保存到我的页面。',
    })
  } else {
    resultUiChatMessages.value.push({ role: 'assistant', content: outcome.error || '生成失败，请调整描述后再试。' })
  }
}

async function saveResultUiDesigner(): Promise<void> {
  const ok = await handleSaveResultUi()
  if (!ok) return
  resultUiChatMessages.value.push({ role: 'assistant', content: '已保存结果页面结构。' })
}

async function resetResultUiDesigner(): Promise<void> {
  const ok = await handleResetResultUi()
  if (!ok) return
  resultUiChatMessages.value.push({ role: 'assistant', content: '结果页面已恢复默认结构。' })
}

function schedulePolling() {
  stopPolling()
  if (!isRunning.value) return
  pollTimer = setInterval(() => {
    loadSubmission()
  }, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function submissionStatusColor(status?: string): string {
  if (status === 'completed') return 'green'
  if (status === 'running') return 'arcoblue'
  if (status === 'failed') return 'red'
  if (status === 'pending') return 'orange'
  return 'gray'
}

function formatParamValue(value: unknown): string {
  if (value == null || value === '') return '-'
  if (Array.isArray(value)) return value.map(item => String(item)).join(', ')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

watch(() => route.params.id, loadSubmission, { immediate: true })
watch(isRunning, () => {
  schedulePolling()
})

onBeforeUnmount(() => {
  stopPolling()
})

// 防止 TS noUnusedLocals 报错（保留函数以兼容潜在 v-model 外部调用）
void handlePreviewResultUi
</script>

<style scoped>
.portal-submission {
  min-height: 100%;
  padding: 0;
  background: var(--ai-bg);
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}

.page-detail-toolbar {
  margin: 16px 28px 0;
  padding: 8px 12px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}

/* ─────────── hero ─────────── */
.report-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) 240px;
  gap: 16px;
  margin: 16px 28px;
  padding: 22px 28px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  border: 1px solid var(--ai-border);
  box-shadow: none;
}

.report-hero-main {
  min-width: 0;
}

.report-kicker,
.report-meta,
.report-tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.report-kicker {
  font-size: 12px;
  letter-spacing: 0;
  text-transform: none;
  color: var(--ai-ink-4);
  font-weight: 500;
}
.report-kicker > span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.report-kicker > span + span::before {
  content: '·';
  margin-right: 6px;
  color: var(--ai-ink-5);
}

.report-title {
  margin: 6px 0 6px;
  font-size: 22px;
  line-height: 1.25;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
}

.report-summary {
  margin: 0;
  max-width: 920px;
  color: var(--ai-ink-3);
  font-size: 13px;
  line-height: 1.6;
}

.report-meta {
  margin-top: 12px;
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.report-meta > span {
  display: inline-flex;
  align-items: center;
}
.report-meta > span + span::before {
  content: '·';
  margin-right: 8px;
  color: var(--ai-ink-5);
}
.report-meta .mono {
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-3);
}

.report-tag-row {
  margin-top: 10px;
}

.report-hero-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
}
.hero-side-label {
  font-size: 10.5px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--ai-ink-4);
  font-weight: 500;
}
.hero-side-time {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
  margin-bottom: 6px;
}
.hero-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
  margin-top: 8px;
}
.hero-actions .ai-btn {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  font-size: 12.5px;
  cursor: pointer;
}
.hero-actions .ai-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

/* hero 内 Arco a-tag → ai-pill */
.report-hero :deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-family: var(--ai-font-sans);
}
.report-hero :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}
.report-hero :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
.report-hero :deep(.arco-tag-color-orange) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.report-hero :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}
.report-hero :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}

/* ─────────── 通用卡片 ─────────── */
.sf-card {
  margin: 12px 28px;
  padding: 18px 22px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  box-shadow: none;
  color: var(--ai-ink-1);
}

.sf-card-running {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 28px;
}
.sf-card-running .running-text {
  color: var(--ai-ink-3);
  font-size: 13px;
}

.section-eyebrow {
  margin-bottom: 14px;
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

/* ─────────── 结果区 ─────────── */
.result-component + .result-component {
  margin-top: 14px;
}
.result-component-title {
  margin-bottom: 8px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ai-ink-2);
}
.result-panel {
  padding: 14px 16px;
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
}
.result-text {
  white-space: pre-wrap;
  line-height: 1.7;
  color: var(--ai-ink-1);
  font-size: 12.5px;
}
.result-alert {
  padding: 12px 14px;
  margin-top: 16px;
  border-radius: var(--ai-radius-s);
  background: var(--ai-info-soft);
  color: var(--ai-info);
  font-size: 12.5px;
  line-height: 1.6;
  border-left: 2px solid var(--ai-info);
}
.result-analysis {
  margin-top: 16px;
}
.result-warn {
  padding: 10px 14px;
  margin-bottom: 12px;
  border-radius: var(--ai-radius-s);
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  font-size: 12.5px;
  border-left: 2px solid var(--ai-warn);
}
.result-warn + .result-warn {
  margin-top: -4px;
}

/* metric tile */
.metric-tile {
  padding: 14px 16px;
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.metric-title {
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-4);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.metric-value {
  font-size: 20px;
  line-height: 1.2;
  font-weight: 600;
  color: var(--ai-ink-1);
  font-variant-numeric: tabular-nums;
}
.metric-desc {
  font-size: 11.5px;
  color: var(--ai-ink-3);
  line-height: 1.5;
}
.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 10px;
}

/* ─────────── 表格 ─────────── */
.sf-table :deep(.arco-table-th) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-4) !important;
  font-weight: 500 !important;
  font-size: 11.5px !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 8px 12px !important;
  border-bottom: 1px solid var(--ai-border) !important;
}
.sf-table :deep(.arco-table-td) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-1) !important;
  font-size: 12.5px !important;
  padding: 10px 12px !important;
  border-bottom: 1px solid var(--ai-border) !important;
  font-variant-numeric: tabular-nums;
}
.sf-table :deep(.arco-table-tr:hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}
.sf-table :deep(.arco-table-border .arco-table-cell) {
  border-right-color: var(--ai-border) !important;
}

/* ─────────── 追溯摘要 ─────────── */
.trace-summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(120px, 1fr));
  gap: 10px;
}
.trace-summary-tile {
  padding: 12px 14px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
}
.trace-summary-tile span {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.trace-summary-tile strong {
  display: block;
  margin-top: 4px;
  color: var(--ai-ink-1);
  font-size: 18px;
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
  font-weight: 600;
}

.analysis-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 14px;
}
.analysis-meta span {
  color: var(--ai-ink-4);
  font-size: 11.5px;
}
.analysis-meta .mono {
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-3);
  font-variant-numeric: tabular-nums;
}
.analysis-output {
  margin: 12px 0 0;
  max-height: 360px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
  padding: 12px 14px;
  color: var(--ai-ink-1);
  font-size: 12px;
  font-family: var(--ai-font-mono);
  line-height: 1.55;
}

/* analysis-meta arco-tag */
.analysis-meta :deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  font-family: var(--ai-font-sans);
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.analysis-meta :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}

/* ─────────── 参数 / 执行信息 ─────────── */
.param-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  overflow: hidden;
}
.param-row {
  display: grid;
  grid-template-columns: minmax(120px, 0.4fr) minmax(0, 1fr);
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
}
.param-row:nth-last-child(-n+2) {
  border-bottom: none;
}
.param-row:nth-child(odd) {
  border-right: 1px solid var(--ai-border);
}
.param-key {
  padding: 9px 12px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 12px;
  font-weight: 500;
  border-right: 1px solid var(--ai-border);
}
.param-key.mono {
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-2);
}
.param-val {
  padding: 9px 12px;
  color: var(--ai-ink-1);
  font-size: 12.5px;
  word-break: break-all;
}
.param-val.mono {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--ai-ink-2);
}

/* ─────────── ai 调用浮动按钮 ─────────── */
.ui-floating-ai {
  position: fixed;
  right: 24px;
  bottom: 28px;
  z-index: 20;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  border: 1px solid var(--ai-ink-1);
  border-radius: 50%;
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  box-shadow: var(--ai-shadow-2);
  cursor: pointer;
}
.ui-floating-ai:disabled {
  border-color: var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-4);
  cursor: not-allowed;
  box-shadow: none;
}
.ui-floating-ai:not(:disabled):hover {
  background: #000;
}

/* ─────────── ui designer modal ─────────── */
.ui-designer {
  display: flex;
  flex-direction: column;
  gap: 14px;
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}
.ui-designer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.ui-designer-head strong {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.ui-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ui-hash {
  color: var(--ai-ink-4);
  font-size: 11.5px;
}
.ui-meta :deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-family: var(--ai-font-sans);
}
.ui-meta :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}
.ui-meta :deep(.arco-tag-color-orange) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.ui-meta :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}

.ui-chat-log {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 220px;
  max-height: 380px;
  padding: 14px;
  overflow-y: auto;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
}
.ui-chat-message {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.ui-chat-message.is-user {
  flex-direction: row-reverse;
}
.ui-chat-role {
  flex: 0 0 auto;
  min-width: 28px;
  padding-top: 7px;
  color: var(--ai-ink-4);
  font-size: 11.5px;
  text-align: center;
}
.ui-chat-bubble {
  max-width: min(620px, 82%);
  padding: 9px 11px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  line-height: 1.6;
  font-size: 12.5px;
  white-space: pre-wrap;
  word-break: break-word;
}
.ui-chat-message.is-user .ui-chat-bubble {
  border-color: transparent;
  background: var(--ai-info-soft);
  color: var(--ai-info);
}
.ui-chat-compose {
  display: flex;
  flex-direction: column;
}
.ui-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  flex-wrap: wrap;
}
.ui-actions .ai-btn {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  font-size: 12.5px;
  cursor: pointer;
}
.ui-actions .ai-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

/* ─────────── 通用 mono helper ─────────── */
.mono {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

@media (max-width: 1080px) {
  .report-hero {
    grid-template-columns: 1fr;
  }
  .report-hero-side {
    align-items: flex-start;
  }
  .hero-actions {
    justify-content: flex-start;
  }
}

@media (max-width: 768px) {
  .page-detail-toolbar,
  .report-hero,
  .sf-card {
    margin-left: 16px;
    margin-right: 16px;
  }
  .report-hero {
    padding: 18px 18px;
  }
  .trace-summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .param-list {
    grid-template-columns: 1fr;
  }
  .param-row {
    grid-template-columns: minmax(110px, 0.4fr) minmax(0, 1fr);
  }
  .param-row:nth-child(odd) {
    border-right: none;
  }
  .param-row:nth-last-child(-n+2) {
    border-bottom: 1px solid var(--ai-border);
  }
  .param-row:last-child {
    border-bottom: none;
  }
  .ui-designer-head {
    align-items: stretch;
    flex-direction: column;
  }
  .ui-chat-bubble {
    max-width: 88%;
  }
  .ui-floating-ai {
    right: 16px;
    bottom: 18px;
  }
}
</style>