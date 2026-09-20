<template>
  <div class="training-datasets-page ai-main">
    <div class="training-subpage-head ai-pagehead">
      <div>
        <div class="ai-crumbs">训练 · 多部门过程数据 → 模型</div>
        <h1 class="ai-title">数据资产</h1>
        <p class="ai-sub">可训练样本、反馈覆盖和候选生成门禁</p>
      </div>
      <div class="training-head-actions">
        <button type="button" class="ai-btn" @click="router.push('/training')">
          <SfShellIcon name="arrowl" />
          训练
        </button>
        <button type="button" class="ai-btn" :disabled="loading" @click="loadDatasets">
          <SfShellIcon name="refresh" />
          刷新
        </button>
      </div>
    </div>

    <div class="training-subpage-body ai-pagebody">
      <a-row :gutter="[16, 16]" class="dataset-kpis">
        <a-col :xs="12" :lg="6">
          <SfKpiCard label="可见 Skill" :value="datasetRows.length" hint="统一 Skill 读权限过滤" tone="brand" />
        </a-col>
        <a-col :xs="12" :lg="6">
          <SfKpiCard label="训练资产" :value="trainingAssetKpiValue" hint="历史样本池 / 资产目录" tone="success" />
        </a-col>
        <a-col :xs="12" :lg="6">
          <SfKpiCard label="图文资产" :value="imageTextAssetCount" hint="图片和文字联合样本" tone="info" />
        </a-col>
        <a-col :xs="12" :lg="6">
          <SfKpiCard label="数据集版本" :value="datasetVersionRows.length" hint="可用于训练任务" tone="neutral" />
        </a-col>
      </a-row>

      <section class="dataset-card ai-card">
        <header class="training-subcard-head">
          <span class="training-subcard-title">部门历史样本资产</span>
          <span class="training-subcard-meta">{{ departmentFlowWindowText }}</span>
        </header>
        <a-spin :loading="loading">
          <div v-if="departmentFlowRows.length || historicalSampleAssetCount" class="asset-flow-panel">
            <div class="asset-flow-strip">
              <button
                v-for="stage in assetFlowStages"
                :key="stage.key"
                type="button"
                class="asset-flow-stage"
                :class="stage.status"
                :aria-label="`${stage.label} ${stage.value}`"
              >
                <span>{{ stage.label }}</span>
                <strong class="mono">{{ stage.value }}</strong>
                <em>{{ stage.detail }}</em>
              </button>
            </div>
            <table v-if="departmentFlowRows.length" class="asset-flow-table">
              <thead>
                <tr>
                  <th>部门</th>
                  <th>数据</th>
                  <th>清洗</th>
                  <th>训练</th>
                  <th>模型</th>
                  <th>评估</th>
                  <th>运行</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in visibleDepartmentFlowRows" :key="row.id">
                  <td>
                    <div class="skill-name">{{ row.department }}</div>
                    <div class="skill-meta">
                      <a-tag size="small" :color="row.warn ? 'orange' : 'green'">{{ row.currentStageLabel }}</a-tag>
                      <span class="asset-flow-latest">{{ row.latestText }}</span>
                    </div>
                  </td>
                  <td><span class="mono sample-num">{{ row.dataText }}</span></td>
                  <td><span class="mono sample-num">{{ row.cleanText }}</span></td>
                  <td><span class="mono sample-num">{{ row.trainText }}</span></td>
                  <td><span class="mono sample-num">{{ row.modelText }}</span></td>
                  <td><span class="mono sample-num">{{ row.evalText }}</span></td>
                  <td>
                    <span :class="row.warn ? 'asset-flow-muted' : 'asset-flow-ok'">{{ row.runText }}</span>
                  </td>
                </tr>
              </tbody>
            </table>
            <button
              v-if="departmentFlowOverflowCount > 0"
              type="button"
              class="asset-flow-more"
              @click="showAllDepartmentFlow = true"
            >
              展开 {{ departmentFlowOverflowCount }} 个部门
            </button>
          </div>
          <SfEmptyState
            v-else
            title="暂无历史样本资产"
            description="部门过程数据完成清洗物化后会进入这里"
            hint="这里展示聚合计数，不返回原始输入、输出或反馈正文。"
          />
        </a-spin>
      </section>

      <section class="dataset-card ai-card">
        <header class="training-subcard-head">
          <span class="training-subcard-title">公司可训练资产</span>
        </header>
        <a-spin :loading="loading">
        <a-table
          v-if="assetRows.length"
          :data="assetRows"
          :pagination="false"
          row-key="id"
          size="small"
        >
          <template #columns>
            <a-table-column title="资产">
              <template #cell="{ record }">
                <div class="skill-cell">
                  <div class="skill-name">{{ record.title || record.source_id }}</div>
                  <div class="skill-meta mono">{{ record.source_type }} · {{ record.source_id }}</div>
                  <div class="skill-meta">{{ record.department || '-' }}</div>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="模态" :width="130">
              <template #cell="{ record }">
                <a-tag :color="record.modality === 'image_text' ? 'arcoblue' : 'gray'">
                  {{ modalityLabel(record.modality) }}
                </a-tag>
              </template>
            </a-table-column>
            <a-table-column title="存储">
              <template #cell="{ record }">
                <div class="skill-meta">{{ record.storage_backend || '-' }}</div>
                <div class="skill-meta mono dataset-ref">{{ record.storage_ref || '-' }}</div>
              </template>
            </a-table-column>
            <a-table-column title="样本" :width="100">
              <template #cell="{ record }">
                <span class="mono sample-num">{{ record.sample_count || 0 }}</span>
              </template>
            </a-table-column>
            <a-table-column title="Hash" :width="150">
              <template #cell="{ record }">
                <span class="muted-text mono">{{ shortHash(record.sha256) }}</span>
              </template>
            </a-table-column>
            <a-table-column title="更新时间" :width="170">
              <template #cell="{ record }">
                <span class="muted-text mono">{{ formatTime(record.updated_at || record.created_at) }}</span>
              </template>
            </a-table-column>
          </template>
        </a-table>
        <SfEmptyState
          v-else
          title="暂无训练资产"
          description="SDK 上传、知识库媒体和学习样本会登记到这里"
          hint="资产目录只保存引用、hash、权限和 lineage，大文件留在对应存储。"
        />
        </a-spin>
      </section>

      <section class="dataset-card ai-card">
        <header class="training-subcard-head">
          <span class="training-subcard-title">数据集版本</span>
        </header>
        <a-spin :loading="loading">
        <a-table
          v-if="datasetVersionRows.length"
          :data="datasetVersionRows"
          :pagination="false"
          row-key="id"
          size="small"
        >
          <template #columns>
            <a-table-column title="数据集">
              <template #cell="{ record }">
                <div class="skill-cell">
                  <div class="skill-name">{{ record.name }}</div>
                  <div class="skill-meta mono">{{ record.id }} · {{ record.version }}</div>
                  <div class="skill-meta">{{ record.department || '-' }}</div>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="Profile" :width="180">
              <template #cell="{ record }">
                <a-tag color="arcoblue">{{ profileLabel(record.dataset_profile) }}</a-tag>
                <div class="skill-meta">{{ modalityLabel(record.modality) }}</div>
              </template>
            </a-table-column>
            <a-table-column title="规模" :width="140">
              <template #cell="{ record }">
                <div class="sample-grid compact">
                  <span>样本 <span class="mono sample-num">{{ record.sample_count || 0 }}</span></span>
                  <span>媒体 <span class="mono sample-num">{{ record.media_count || 0 }}</span></span>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="Manifest">
              <template #cell="{ record }">
                <div class="skill-meta mono dataset-ref">{{ record.manifest_uri || '-' }}</div>
                <div class="skill-meta mono">sha256 {{ shortHash(record.manifest_sha256) }}</div>
              </template>
            </a-table-column>
            <a-table-column title="GB10 同步" :width="140">
              <template #cell="{ record }">
                <a-tag :color="syncStatusColor(record.sync_status)">{{ syncStatusLabel(record.sync_status) }}</a-tag>
                <div class="skill-meta">{{ record.target_gateway_id || '-' }}</div>
              </template>
            </a-table-column>
            <a-table-column title="自动流程" :width="260">
              <template #cell="{ record }">
                <div class="dataset-auto-flow">
                  <span
                    v-for="step in datasetAutomationSteps(record)"
                    :key="`${record.id}-${step.key}`"
                    class="dataset-auto-step"
                    :class="`status-${step.status}`"
                    :title="`${step.label}：${step.detail || step.status}`"
                  >
                    <i></i>
                    {{ step.label }}
                  </span>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="操作" :width="130">
              <template #cell="{ record }">
                <a-button
                  type="text"
                  size="mini"
                  :loading="Boolean(operating[record.id])"
                  @click="syncDatasetVersion(record)"
                >
                  {{ record.sync_status === 'completed' ? '重新同步' : '重试同步' }}
                </a-button>
              </template>
            </a-table-column>
          </template>
        </a-table>
        <SfEmptyState
          v-else
          title="暂无数据集版本"
          description="将训练样本 materialize 后即可生成版本化 manifest"
          hint="训练任务引用 dataset_version_id，节点只拿 manifest 和媒体引用。"
        />
        </a-spin>
      </section>

      <section class="dataset-card ai-card">
        <header class="training-subcard-head">
          <span class="training-subcard-title">Skill 数据资产覆盖</span>
        </header>
        <a-spin :loading="loading">
        <a-result v-if="loadError" status="error" :title="loadError" />
        <a-table
          v-else-if="datasetRows.length"
          :data="datasetRows"
          :pagination="false"
          row-key="skill_id"
          size="small"
        >
          <template #columns>
            <a-table-column title="Skill">
              <template #cell="{ record }">
                <div class="skill-cell">
                  <div class="skill-name">{{ record.skill_name }}</div>
                  <div class="skill-meta mono">{{ record.skill_id }}</div>
                  <div class="skill-meta">{{ record.department || '-' }} · {{ record.status || '-' }}</div>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="门禁" :width="120">
              <template #cell="{ record }">
                <a-tag :color="record.passed ? 'green' : 'orange'">
                  {{ record.passed ? '已达标' : '待补齐' }}
                </a-tag>
              </template>
            </a-table-column>
            <a-table-column title="样本覆盖">
              <template #cell="{ record }">
                <div class="sample-grid">
                  <span>SFT <span class="mono sample-num">{{ record.sample_counts.sft_samples }}</span></span>
                  <span>偏好 <span class="mono sample-num">{{ record.sample_counts.preference_samples }}</span></span>
                  <span>动作 <span class="mono sample-num">{{ record.sample_counts.action_outcome_samples }}</span></span>
                  <span>评估 <span class="mono sample-num">{{ record.sample_counts.eval_samples }}</span></span>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="最近失败率" :width="160">
              <template #cell="{ record }">
                <div class="failure-cell">
                  <div class="failure-bar-row">
                    <span class="mono failure-num" :class="failureRateClass(record.recent_failure_rate)">
                      {{ percent(record.recent_failure_rate) }}
                    </span>
                    <div class="failure-track">
                      <div
                        class="failure-fill"
                        :class="failureRateClass(record.recent_failure_rate)"
                        :style="{ width: failureBarWidth(record.recent_failure_rate) }"
                      />
                    </div>
                  </div>
                  <div class="skill-meta mono">
                    {{ record.sample_counts.recent_failed }} / {{ record.sample_counts.recent_total }}
                  </div>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="数据集">
              <template #cell="{ record }">
                <div class="skill-meta mono dataset-ref">{{ record.dataset_ref }}</div>
                <div class="skill-meta">
                  最近样本 <span class="mono">{{ formatTime(record.last_log_at) }}</span>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="缺口">
              <template #cell="{ record }">
                <a-space wrap size="mini">
                  <a-tag
                    v-for="check in failedChecks(record)"
                    :key="`${record.skill_id}-${check.key}`"
                    size="small"
                    color="orange"
                  >
                    {{ check.label }} {{ check.actual }}/{{ check.threshold }}
                  </a-tag>
                  <a-tag v-if="!failedChecks(record).length" size="small" color="green">门禁通过</a-tag>
                </a-space>
              </template>
            </a-table-column>
            <a-table-column title="操作" :width="130">
              <template #cell="{ record }">
                <a-button
                  v-if="record.passed"
                  type="text"
                  size="mini"
                  :disabled="!record.can_create_candidate"
                  :loading="Boolean(operating[record.skill_id])"
                  @click="createCandidate(record)"
                >
                  <SfShellIcon name="send" class="table-action-icon" />
                  生成候选
                </a-button>
                <span v-else class="muted-text">补齐样本</span>
              </template>
            </a-table-column>
          </template>
        </a-table>
        <SfEmptyState
          v-else
          title="暂无数据资产"
          description="当前可见 Skill 还没有可用于训练的 DecisionLog 样本"
          hint="训练候选只会读取脱敏统计，不返回原始输入、输出或人工反馈正文。"
        />
        </a-spin>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { trainingApi as rawTrainingApi } from '@/api'
import { SfKpiCard } from '@/components/sf'
import { SfEmptyState } from '@/components/common'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

defineOptions({ name: 'TrainingDatasets' })

type DatasetCheck = {
  key: string
  label: string
  actual: number
  threshold: number
  passed: boolean
}

type DatasetSampleCounts = {
  sft_samples: number
  preference_samples: number
  action_outcome_samples: number
  eval_samples: number
  recent_total: number
  recent_failed: number
}

type DatasetRow = {
  skill_id: string
  skill_name: string
  department: string
  status: string
  dataset_ref: string
  passed: boolean
  can_create_candidate: boolean
  sample_counts: DatasetSampleCounts
  checks: DatasetCheck[]
  recent_failure_rate: number
  last_log_at: string
}

type TrainingAssetSourceRow = {
  id: string
  source_type: string
  source_id: string
  title: string
  department: string
  modality: string
  storage_backend: string
  storage_ref: string
  sample_count: number
  sha256: string
  created_at: string
  updated_at: string
}

type TrainingDatasetVersionRow = {
  id: string
  name: string
  version: string
  dataset_profile: string
  modality: string
  department: string
  sample_count: number
  media_count: number
  manifest_sha256: string
  manifest_uri: string
  target_gateway_id: string
  sync_status: string
  automation_status: TrainingDatasetAutomationStatus | null
  created_at: string
  updated_at: string
}

type AutomationStep = {
  key: string
  label: string
  status: string
  detail: string
  [key: string]: unknown
}

type TrainingDatasetAutomationStatus = {
  status: string
  steps: AutomationStep[]
}

type TrainingDepartmentFlowStage = {
  key: string
  label: string
  count: number
  status: string
  detail: string
}

type TrainingDepartmentFlowItem = {
  id: string
  department: string
  metrics: Record<string, unknown>
  stages: TrainingDepartmentFlowStage[]
  current_stage: string
  current_stage_label: string
  coverage: number
  latest_at?: string | null
  blockers?: Array<Record<string, unknown>>
}

type TrainingDepartmentFlow = {
  window_days?: number
  stage_order?: Array<{ key: string; label: string }>
  summary?: Record<string, unknown>
  departments?: TrainingDepartmentFlowItem[]
}

type AssetFlowStage = {
  key: string
  label: string
  value: string
  detail: string
  status: 'ok' | 'warn' | 'muted'
}

type DepartmentFlowRow = {
  id: string
  department: string
  currentStageLabel: string
  latestText: string
  dataText: string
  cleanText: string
  trainText: string
  modelText: string
  evalText: string
  runText: string
  warn: boolean
}

const trainingApi: any = rawTrainingApi
const router = useRouter()
const loading = ref(false)
const loadError = ref('')
const datasets = ref<DatasetRow[]>([])
const assetSources = ref<TrainingAssetSourceRow[]>([])
const datasetVersions = ref<TrainingDatasetVersionRow[]>([])
const automationStatus = ref<Record<string, unknown> | null>(null)
const operating = ref<Record<string, boolean>>({})
const showAllDepartmentFlow = ref(false)

const datasetRows = computed(() => datasets.value)
const assetRows = computed(() => assetSources.value)
const datasetVersionRows = computed(() => datasetVersions.value)
const imageTextAssetCount = computed(() => assetRows.value.filter(item => ['image_text', 'image', 'video_frame', 'mixed'].includes(item.modality)).length)
const departmentFlow = computed<TrainingDepartmentFlow>(() => {
  const flow = automationStatus.value?.department_flow
  return flow && typeof flow === 'object' ? flow as TrainingDepartmentFlow : {}
})
const departmentFlowSummary = computed(() =>
  departmentFlow.value.summary && typeof departmentFlow.value.summary === 'object'
    ? departmentFlow.value.summary as Record<string, unknown>
    : {})
const departmentFlowStageCounts = computed(() => {
  const counts = departmentFlowSummary.value.stage_counts
  return counts && typeof counts === 'object' ? counts as Record<string, unknown> : {}
})
const departmentFlowItems = computed(() => normalizeList<TrainingDepartmentFlowItem>(departmentFlow.value.departments))
const historicalSampleAssetCount = computed(() =>
  safeNumber(departmentFlowSummary.value.cleaned_samples)
  || safeNumber(departmentFlowStageCounts.value.clean)
  || assetRows.value.reduce((sum, item) => sum + safeNumber(item.sample_count), 0))
const trainingAssetKpiValue = computed(() =>
  historicalSampleAssetCount.value ? formatCompactCount(historicalSampleAssetCount.value) : assetRows.value.length)
const departmentFlowWindowText = computed(() => {
  const days = safeNumber(departmentFlow.value.window_days)
  return days ? `近 ${days} 天入口 / 全量已物化样本` : '历史样本池'
})
const assetFlowStages = computed<AssetFlowStage[]>(() => {
  const counts = departmentFlowStageCounts.value
  const jobsRunning = safeNumber(departmentFlowSummary.value.jobs_running)
  const evalFailed = safeNumber(departmentFlowSummary.value.eval_failed)
  const deploymentsActive = safeNumber(departmentFlowSummary.value.deployments_active)
  return [
    {
      key: 'data',
      label: '数据',
      value: formatCompactCount(counts.data),
      detail: `${formatCompactCount(departmentFlowSummary.value.raw_events)} 原始入口`,
      status: safeNumber(counts.data) > 0 ? 'ok' : 'muted',
    },
    {
      key: 'clean',
      label: '清洗',
      value: formatCompactCount(counts.clean),
      detail: `${formatCompactCount(departmentFlowSummary.value.eval_samples)} 评估样本`,
      status: safeNumber(counts.clean) > 0 ? 'ok' : 'warn',
    },
    {
      key: 'train',
      label: '训练',
      value: formatCompactCount(counts.train),
      detail: `${jobsRunning} 进行中`,
      status: jobsRunning > 0 ? 'warn' : (safeNumber(counts.train) > 0 ? 'ok' : 'muted'),
    },
    {
      key: 'model',
      label: '模型',
      value: formatCompactCount(counts.model),
      detail: '训练产物/候选',
      status: safeNumber(counts.model) > 0 ? 'ok' : 'muted',
    },
    {
      key: 'eval',
      label: '评估',
      value: formatCompactCount(counts.eval),
      detail: evalFailed ? `${evalFailed} 失败` : '门禁通过数',
      status: evalFailed ? 'warn' : (safeNumber(counts.eval) > 0 ? 'ok' : 'muted'),
    },
    {
      key: 'run',
      label: '运行',
      value: formatCompactCount(counts.run),
      detail: `${deploymentsActive} active`,
      status: deploymentsActive > 0 ? 'ok' : 'muted',
    },
  ]
})
const departmentFlowRows = computed<DepartmentFlowRow[]>(() => departmentFlowItems.value.map((item) => {
  const metrics = item.metrics || {}
  const rawEvents = safeNumber(metrics.raw_events)
  const artifactTotal = safeNumber(metrics.artifact_total)
  const cleanedSamples = safeNumber(metrics.cleaned_samples)
  const trainingSamples = safeNumber(metrics.training_samples)
  const evalSamples = safeNumber(metrics.eval_samples)
  const jobsRunning = safeNumber(metrics.jobs_running)
  const jobsCompleted = safeNumber(metrics.jobs_completed)
  const jobsFailed = safeNumber(metrics.jobs_failed)
  const models = safeNumber(metrics.models)
  const evalCompleted = safeNumber(metrics.eval_completed)
  const evalFailed = safeNumber(metrics.eval_failed)
  const active = safeNumber(metrics.deployments_active)
  const canary = safeNumber(metrics.deployments_canary)
  return {
    id: item.id || `dept:${item.department}`,
    department: item.department || '未归属',
    currentStageLabel: item.current_stage_label || '数据',
    latestText: item.latest_at ? formatTime(String(item.latest_at)) : '-',
    dataText: formatCompactCount(Math.max(rawEvents, artifactTotal)),
    cleanText: `${formatCompactCount(cleanedSamples)} / ${formatCompactCount(trainingSamples + evalSamples || artifactTotal)}`,
    trainText: `${jobsRunning} / ${jobsCompleted}${jobsFailed ? ` / ${jobsFailed}` : ''}`,
    modelText: formatCompactCount(models),
    evalText: `${formatCompactCount(evalCompleted)}${evalFailed ? ` / ${evalFailed}` : ''}`,
    runText: active > 0 ? `${active} active` : (canary > 0 ? `${canary} 灰度` : '-'),
    warn: safeNumber(item.coverage) < 80 || jobsFailed > 0 || evalFailed > 0,
  }
}))
const visibleDepartmentFlowRows = computed(() => showAllDepartmentFlow.value ? departmentFlowRows.value : departmentFlowRows.value.slice(0, 8))
const departmentFlowOverflowCount = computed(() => Math.max(0, departmentFlowRows.value.length - visibleDepartmentFlowRows.value.length))

function normalizeList<T>(value: unknown): T[] {
  if (Array.isArray(value)) return value as T[]
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    if (Array.isArray(record.items)) return record.items as T[]
    if (Array.isArray(record.data)) return record.data as T[]
  }
  return []
}

function safeNumber(value: unknown): number {
  const num = Number(value)
  return Number.isFinite(num) ? num : 0
}

function roundOne(value: number): number {
  return Math.round(value * 10) / 10
}

function formatCompactCount(value: unknown): string {
  const count = safeNumber(value)
  if (count >= 10000) return `${roundOne(count / 10000)} 万`
  if (count >= 1000) return `${roundOne(count / 1000)}k`
  return String(Math.round(count))
}

function normalizeSampleCounts(value: unknown): DatasetSampleCounts {
  const record = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  return {
    sft_samples: safeNumber(record.sft_samples),
    preference_samples: safeNumber(record.preference_samples),
    action_outcome_samples: safeNumber(record.action_outcome_samples),
    eval_samples: safeNumber(record.eval_samples),
    recent_total: safeNumber(record.recent_total),
    recent_failed: safeNumber(record.recent_failed),
  }
}

function normalizeCheck(item: Record<string, unknown>): DatasetCheck {
  return {
    key: String(item.key || ''),
    label: String(item.label || item.key || ''),
    actual: safeNumber(item.actual),
    threshold: safeNumber(item.threshold),
    passed: Boolean(item.passed),
  }
}

function normalizeDataset(item: Record<string, unknown>): DatasetRow {
  return {
    skill_id: String(item.skill_id || ''),
    skill_name: String(item.skill_name || ''),
    department: String(item.department || ''),
    status: String(item.status || ''),
    dataset_ref: String(item.dataset_ref || ''),
    passed: Boolean(item.passed),
    can_create_candidate: Boolean(item.can_create_candidate),
    sample_counts: normalizeSampleCounts(item.sample_counts),
    checks: normalizeList<Record<string, unknown>>(item.checks).map(normalizeCheck),
    recent_failure_rate: safeNumber(item.recent_failure_rate),
    last_log_at: String(item.last_log_at || ''),
  }
}

function normalizeAssetSource(item: Record<string, unknown>): TrainingAssetSourceRow {
  return {
    id: String(item.id || ''),
    source_type: String(item.source_type || ''),
    source_id: String(item.source_id || ''),
    title: String(item.title || ''),
    department: String(item.department || ''),
    modality: String(item.modality || 'text'),
    storage_backend: String(item.storage_backend || ''),
    storage_ref: String(item.storage_ref || ''),
    sample_count: safeNumber(item.sample_count),
    sha256: String(item.sha256 || ''),
    created_at: String(item.created_at || ''),
    updated_at: String(item.updated_at || ''),
  }
}

function normalizeDatasetVersion(item: Record<string, unknown>): TrainingDatasetVersionRow {
  return {
    id: String(item.id || ''),
    name: String(item.name || ''),
    version: String(item.version || ''),
    dataset_profile: String(item.dataset_profile || ''),
    modality: String(item.modality || 'text'),
    department: String(item.department || ''),
    sample_count: safeNumber(item.sample_count),
    media_count: safeNumber(item.media_count),
    manifest_sha256: String(item.manifest_sha256 || ''),
    manifest_uri: String(item.manifest_uri || ''),
    target_gateway_id: String(item.target_gateway_id || ''),
    sync_status: String(item.sync_status || 'not_synced'),
    automation_status: normalizeDatasetAutomationStatus(item.automation_status),
    created_at: String(item.created_at || ''),
    updated_at: String(item.updated_at || ''),
  }
}

function normalizeAutomationStep(item: Record<string, unknown>): AutomationStep {
  return {
    ...item,
    key: String(item.key || ''),
    label: String(item.label || item.key || ''),
    status: String(item.status || 'pending'),
    detail: String(item.detail || ''),
  }
}

function normalizeDatasetAutomationStatus(value: unknown): TrainingDatasetAutomationStatus | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  return {
    status: String(record.status || ''),
    steps: normalizeList<Record<string, unknown>>(record.steps).map(normalizeAutomationStep),
  }
}

function datasetAutomationSteps(record: TrainingDatasetVersionRow): AutomationStep[] {
  return normalizeList<AutomationStep>(record.automation_status?.steps)
}

function failedChecks(record: DatasetRow): DatasetCheck[] {
  return record.checks.filter(item => !item.passed)
}

function modalityLabel(value: string): string {
  const labels: Record<string, string> = {
    text: '文字',
    image: '图片',
    image_text: '图文',
    video_frame: '视频帧',
    mixed: '混合',
  }
  return labels[value] || value || '-'
}

function profileLabel(value: string): string {
  const labels: Record<string, string> = {
    text_sft_v1: '文字 SFT',
    vision_instruction_v1: '图文指令',
    image_caption_v1: '图片描述',
    vision_eval_case_v1: '图文评测',
    preference_v1: '偏好',
  }
  return labels[value] || value || '-'
}

function syncStatusLabel(value: string): string {
  const labels: Record<string, string> = {
    not_synced: '未同步',
    pending: '待同步',
    running: '同步中',
    completed: '已同步',
    failed: '失败',
  }
  return labels[value] || value || '-'
}

function syncStatusColor(value: string): string {
  if (value === 'completed') return 'green'
  if (value === 'pending' || value === 'running') return 'arcoblue'
  if (value === 'failed') return 'red'
  return 'gray'
}

function shortHash(value: string): string {
  return value ? `${value.slice(0, 12)}...` : '-'
}

function percent(value: number): string {
  return `${Math.round(value * 1000) / 10}%`
}

// 失败率 → 颜色档：>30% = bad，>10% = warn，否则 ink-3（ok 的反向语义）
function failureRateClass(value: number): string {
  if (value > 0.3) return 'failure-bad'
  if (value > 0.1) return 'failure-warn'
  return 'failure-ok'
}

function failureBarWidth(value: number): string {
  const pct = Math.max(0, Math.min(1, safeNumber(value))) * 100
  return `${pct}%`
}

function formatTime(value: string): string {
  if (!value) return '-'
  return value.replace('T', ' ').slice(0, 16)
}

function setOperating(id: string, value: boolean) {
  const next = { ...operating.value }
  if (value) next[id] = true
  else delete next[id]
  operating.value = next
}

async function loadDatasets() {
  loading.value = true
  loadError.value = ''
  try {
    const [result, assetsResult, versionsResult, automationResult] = await Promise.all([
      trainingApi.datasets(),
      trainingApi.assetSources({ limit: 200 }),
      trainingApi.datasetVersions({ limit: 200 }),
      trainingApi.automationStatus().catch(() => null),
    ])
    datasets.value = normalizeList<Record<string, unknown>>(result?.items).map(normalizeDataset)
    assetSources.value = normalizeList<Record<string, unknown>>(assetsResult?.items).map(normalizeAssetSource)
    datasetVersions.value = normalizeList<Record<string, unknown>>(versionsResult?.items).map(normalizeDatasetVersion)
    automationStatus.value = automationResult && typeof automationResult === 'object'
      ? automationResult as Record<string, unknown>
      : null
  } catch (error: any) {
    datasets.value = []
    assetSources.value = []
    datasetVersions.value = []
    automationStatus.value = null
    loadError.value = error?._message || '加载数据资产失败'
  } finally {
    loading.value = false
  }
}

async function createCandidate(record: DatasetRow) {
  if (!record.skill_id || !record.can_create_candidate || operating.value[record.skill_id]) return
  setOperating(record.skill_id, true)
  try {
    const result = await trainingApi.createSkillCandidate(record.skill_id, {
      dataset_ref: record.dataset_ref,
    })
    Message.success('已生成训练候选')
    if (result?.id) router.push(`/training/jobs/${result.id}`)
  } catch (error: any) {
    Message.error(error?._message || '生成训练候选失败')
  } finally {
    setOperating(record.skill_id, false)
  }
}

async function syncDatasetVersion(record: TrainingDatasetVersionRow) {
  if (!record.id || operating.value[record.id]) return
  setOperating(record.id, true)
  try {
    await trainingApi.syncDatasetVersion(record.id, { target_gateway_id: record.target_gateway_id || 'training-primary' })
    Message.success('已提交 GB10 同步')
    await loadDatasets()
  } catch (error: any) {
    Message.error(error?._message || '同步数据集失败')
  } finally {
    setOperating(record.id, false)
  }
}

onMounted(loadDatasets)
</script>

<style scoped>
/* ─────────── design page chrome ─────────── */
.training-datasets-page {
  min-height: calc(100vh - 52px);
  max-width: none;
  padding: 0;
  margin: 0;
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}
.training-subpage-head {
  flex: 0 0 auto;
}
.training-head-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}
.training-head-actions .ai-btn svg,
.table-action-icon {
  width: 12px;
  height: 12px;
  flex: 0 0 12px;
}
.training-head-actions .ai-btn:disabled {
  opacity: 0.55;
  cursor: wait;
}
.training-subpage-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}
.training-subcard-head {
  min-height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--ai-border);
}
.training-subcard-title {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.2;
  letter-spacing: 0;
}
.training-subcard-meta {
  color: var(--ai-ink-4);
  font-size: 12px;
  white-space: nowrap;
}

/* ─────────── tables ─────────── */
.training-datasets-page :deep(.arco-table-th) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 8px 12px !important;
}
.training-datasets-page :deep(.arco-table-th .arco-table-th-title) {
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.training-datasets-page :deep(.arco-table-td) {
  font-size: 12.5px !important;
  color: var(--ai-ink-1) !important;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 10px 12px !important;
  background: var(--ai-surface) !important;
}
.training-datasets-page :deep(.arco-table-tr:hover .arco-table-td),
.training-datasets-page :deep(.arco-table-tr-hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

/* ─────────── tag pills ─────────── */
.training-datasets-page :deep(.arco-tag.arco-tag-size-small),
.training-datasets-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
}
.training-datasets-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.training-datasets-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.training-datasets-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.training-datasets-page :deep(.arco-tag-color-orange),
.training-datasets-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.training-datasets-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-2) !important;
  border-color: transparent !important;
}

/* ─────────── KPI row + page spacing ─────────── */
.dataset-kpis {
  margin-bottom: 0;
}
.dataset-card {
  margin-top: 0;
  overflow: hidden;
}

/* ─────────── historical sample asset flow ─────────── */
.asset-flow-panel {
  display: flex;
  flex-direction: column;
  gap: 0;
}
.asset-flow-strip {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  border-bottom: 1px solid var(--ai-border);
}
.asset-flow-stage {
  appearance: none;
  border: 0;
  border-right: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-2);
  min-height: 82px;
  padding: 12px;
  text-align: left;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.asset-flow-stage:last-child {
  border-right: 0;
}
.asset-flow-stage span {
  font-size: 12px;
  font-weight: 600;
  color: var(--ai-ink-2);
}
.asset-flow-stage strong {
  font-size: 22px;
  line-height: 1;
  color: var(--ai-ink-1);
}
.asset-flow-stage em {
  font-style: normal;
  color: var(--ai-ink-4);
  font-size: 11.5px;
}
.asset-flow-stage.ok strong {
  color: var(--ai-ok);
}
.asset-flow-stage.warn strong {
  color: var(--ai-warn);
}
.asset-flow-stage.muted strong {
  color: var(--ai-ink-4);
}
.asset-flow-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}
.asset-flow-table th,
.asset-flow-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--ai-border);
  text-align: left;
  vertical-align: top;
  font-size: 12px;
}
.asset-flow-table th {
  color: var(--ai-ink-4);
  font-weight: 500;
}
.asset-flow-table th:first-child,
.asset-flow-table td:first-child {
  width: 26%;
}
.asset-flow-latest {
  margin-left: 6px;
  color: var(--ai-ink-4);
}
.asset-flow-ok {
  color: var(--ai-ok);
  font-weight: 500;
}
.asset-flow-muted {
  color: var(--ai-warn);
  font-weight: 500;
}
.asset-flow-more {
  appearance: none;
  border: 0;
  background: var(--ai-surface);
  color: var(--ai-ink-3);
  padding: 10px 12px;
  text-align: left;
  font-size: 12px;
  cursor: pointer;
}
.asset-flow-more:hover {
  color: var(--ai-ink-1);
  background: var(--ai-surface-2);
}

/* ─────────── table cells ─────────── */
.skill-cell {
  min-width: 0;
}
.skill-name {
  color: var(--ai-ink-1);
  font-weight: 600;
  font-size: 13px;
  letter-spacing: 0;
}
.skill-meta {
  margin-top: 2px;
  color: var(--ai-ink-4);
  font-size: 12px;
  word-break: break-word;
}
.dataset-ref {
  color: var(--ai-ink-3);
  font-size: 11.5px;
}
.dataset-auto-flow {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  flex-wrap: wrap;
}
.dataset-auto-step {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 20px;
  padding: 0 6px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 11px;
  white-space: nowrap;
}
.dataset-auto-step i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ai-ink-4);
}
.dataset-auto-step.status-completed {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
}
.dataset-auto-step.status-completed i {
  background: var(--ai-ok);
}
.dataset-auto-step.status-pending,
.dataset-auto-step.status-running {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
}
.dataset-auto-step.status-pending i,
.dataset-auto-step.status-running i {
  background: var(--ai-warn);
}
.dataset-auto-step.status-blocked {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}
.dataset-auto-step.status-blocked i {
  background: var(--ai-bad);
}
.sample-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(80px, 1fr));
  gap: 4px 12px;
  color: var(--ai-ink-2);
  font-size: 12px;
}
.sample-num {
  color: var(--ai-ink-1);
  font-weight: 500;
}
.muted-text {
  color: var(--ai-ink-4);
  font-size: 12px;
}
.mono {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

/* ─────────── failure rate cell (HealthBar pattern) ─────────── */
.failure-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 140px;
}
.failure-bar-row {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.failure-num {
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
}
.failure-num.failure-ok {
  color: var(--ai-ink-3);
}
.failure-num.failure-warn {
  color: var(--ai-warn);
}
.failure-num.failure-bad {
  color: var(--ai-bad);
  font-weight: 600;
}
.failure-track {
  width: 44px;
  height: 4px;
  background: var(--ai-surface-3);
  border-radius: 2px;
  overflow: hidden;
  flex: 0 0 auto;
}
.failure-fill {
  height: 100%;
  transition: width 0.2s ease, background 0.2s ease;
}
.failure-fill.failure-ok {
  background: var(--ai-ok);
}
.failure-fill.failure-warn {
  background: var(--ai-warn);
}
.failure-fill.failure-bad {
  background: var(--ai-bad);
}
</style>
