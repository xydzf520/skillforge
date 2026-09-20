<template>
  <div class="training-models-page ai-main">
    <div class="training-subpage-head ai-pagehead">
      <div>
        <div class="ai-crumbs">训练 · 多部门过程数据 → 模型</div>
        <h1 class="ai-title">模型库</h1>
        <p class="ai-sub">训练产物、评估门禁、部署和运行状态</p>
      </div>
      <div class="training-head-actions">
        <button type="button" class="ai-btn" @click="router.push('/training')">
          <SfShellIcon name="arrowl" />
          训练
        </button>
        <button type="button" class="ai-btn" :disabled="loading" @click="loadModels">
          <SfShellIcon name="refresh" />
          刷新
        </button>
      </div>
    </div>

    <div class="training-subpage-body ai-pagebody">
      <a-row :gutter="[16, 16]" class="model-kpis">
        <a-col :xs="12" :lg="6">
          <SfKpiCard label="模型条目" :value="modelRows.length" hint="训练产物 + 部署记录" tone="brand" />
        </a-col>
        <a-col :xs="12" :lg="6">
          <SfKpiCard label="待部署" :value="modelReadyCount" hint="完成训练尚未运行" tone="neutral" />
        </a-col>
        <a-col :xs="12" :lg="6">
          <SfKpiCard label="灰度中" :value="statusCount('canary')" hint="可激活或回滚" tone="info" />
        </a-col>
        <a-col :xs="12" :lg="6">
          <SfKpiCard label="运行中" :value="runningModelCount" hint="灰度或 active" tone="success" />
        </a-col>
      </a-row>

      <section class="model-card ai-card">
        <header class="training-subcard-head">
          <span class="training-subcard-title">模型 → 评估 → 运行</span>
        </header>
        <a-spin :loading="loading">
        <a-result v-if="loadError" status="error" :title="loadError" />
        <a-table
          v-else-if="modelRows.length"
          :data="modelRows"
          :pagination="false"
          row-key="row_key"
          size="small"
        >
          <template #columns>
            <a-table-column title="模型">
              <template #cell="{ record }">
                <div class="model-cell">
                  <a-link class="model-name" @click="openModelRecord(record)">
                    {{ record.model_family }}
                  </a-link>
                  <div class="model-meta mono">{{ record.id }}</div>
                  <div v-if="record.source_kind === 'job'" class="model-meta">训练产物待部署</div>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="状态" :width="130">
              <template #cell="{ record }">
                <a-tag :color="modelStatusColor(record)">{{ modelStatusLabel(record) }}</a-tag>
              </template>
            </a-table-column>
            <a-table-column title="目标 Skill">
              <template #cell="{ record }">
                <span class="muted-text mono">{{ record.target_skill_ids.join(', ') || '-' }}</span>
              </template>
            </a-table-column>
            <a-table-column title="训练任务">
              <template #cell="{ record }">
                <a-link v-if="record.job?.id" @click="openJob(record.job.id)">
                  {{ record.job.title || record.job.id }}
                </a-link>
                <span v-else class="muted-text">-</span>
                <div v-if="record.job?.status" class="model-meta">{{ jobStatusLabel(record.job.status) }}</div>
              </template>
            </a-table-column>
            <a-table-column title="Artifact">
              <template #cell="{ record }">
                <div class="model-meta mono">{{ record.artifact_id || '-' }}</div>
                <div v-if="record.artifact_ref?.sha256" class="model-meta mono">
                  sha256 {{ String(record.artifact_ref.sha256).slice(0, 12) }}...
                </div>
              </template>
            </a-table-column>
            <a-table-column title="Agent 路由" :width="150">
              <template #cell="{ record }">
                <a-tag :color="chatDisabledReason(record) ? 'gray' : 'green'">
                  {{ chatDisabledReason(record) ? '未就绪' : '可对话' }}
                </a-tag>
                <div class="model-meta">{{ chatDisabledReason(record) || routeTargetLabel(record) }}</div>
              </template>
            </a-table-column>
            <a-table-column title="灰度" :width="90">
              <template #cell="{ record }">
                <span class="mono">{{ record.rollout_percent || 0 }}%</span>
              </template>
            </a-table-column>
            <a-table-column title="更新时间" :width="170">
              <template #cell="{ record }">
                <span class="muted-text mono">{{ formatTime(record.updated_at) }}</span>
              </template>
            </a-table-column>
            <a-table-column title="操作" :width="320">
              <template #cell="{ record }">
                <a-space size="mini">
                  <a-tooltip :content="chatDisabledReason(record)" :disabled="!chatDisabledReason(record)">
                    <a-button
                      type="text"
                      size="mini"
                      :disabled="Boolean(chatDisabledReason(record))"
                      @click="chatWithModel(record)"
                    >
                      对话
                    </a-button>
                  </a-tooltip>
                  <a-button
                    type="text"
                    size="mini"
                    @click="downloadModel(record)"
                  >
                    下载
                  </a-button>
                  <a-button
                    v-if="canRequestDeployment(record)"
                    type="text"
                    size="mini"
                    :loading="Boolean(operating[record.id])"
                    @click="requestDeployment(record)"
                  >
                    部署
                  </a-button>
                  <a-button
                    v-if="['canary', 'active'].includes(record.status)"
                    type="text"
                    size="mini"
                    :loading="Boolean(operating[record.id])"
                    @click="syncOpenWebUI(record)"
                  >
                    同步 OpenWebUI
                  </a-button>
                  <a-button
                    v-if="record.status === 'awaiting_review'"
                    type="text"
                    size="mini"
                    :loading="Boolean(operating[record.id])"
                    @click="approveDeployment(record)"
                  >
                    <SfShellIcon name="send" class="table-action-icon" />
                    审批
                  </a-button>
                  <a-button
                    v-if="record.status === 'awaiting_review'"
                    type="text"
                    status="danger"
                    size="mini"
                    :loading="Boolean(operating[record.id])"
                    @click="rejectDeployment(record)"
                  >
                    <SfShellIcon name="x" class="table-action-icon" />
                    驳回
                  </a-button>
                  <a-button
                    v-if="record.status === 'canary'"
                    type="text"
                    size="mini"
                    :loading="Boolean(operating[record.id])"
                    @click="activateDeployment(record)"
                  >
                    <SfShellIcon name="send" class="table-action-icon" />
                    激活
                  </a-button>
                  <a-button
                    v-if="['canary', 'active'].includes(record.status)"
                    type="text"
                    status="danger"
                    size="mini"
                    :loading="Boolean(operating[record.id])"
                    @click="rollbackDeployment(record)"
                  >
                    <SfShellIcon name="x" class="table-action-icon" />
                    回滚
                  </a-button>
                </a-space>
              </template>
            </a-table-column>
          </template>
        </a-table>
        <SfEmptyState
          v-else
          title="暂无模型产物"
          description="训练完成或进入部署候选后会出现在这里"
          hint="模型库展示聚合状态，不返回训练产物原始文件内容。"
        />
        </a-spin>
      </section>
    </div>
    <TrainingDeploymentRejectModal
      v-model:visible="rejectModalVisible"
      :loading="Boolean(rejectTarget && operating[rejectTarget.id])"
      @submit="submitRejectDeployment"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Message, Modal } from '@arco-design/web-vue'
import { trainingApi as rawTrainingApi } from '@/api'
import { SfKpiCard } from '@/components/sf'
import { SfEmptyState } from '@/components/common'
import TrainingDeploymentRejectModal from './TrainingDeploymentRejectModal.vue'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

defineOptions({ name: 'TrainingModels' })

type DeploymentJobSummary = {
  id: string
  title: string
  status: string
  target_skill_id: string
  target_gateway_id: string
  dataset_ref?: string
}

type DeploymentRow = {
  id: string
  job_id: string
  department: string
  model_family: string
  artifact_id: string
  artifact_ref: Record<string, unknown>
  target_skill_ids: string[]
  status: string
  rollout_percent: number
  rollback_to: string
  deployment_target_gateway_id: string
  job: DeploymentJobSummary | null
  updated_at: string
}

type TrainingJobModelRow = {
  id: string
  title: string
  department: string
  status: string
  failure_stage: string
  target_skill_id: string
  target_gateway_id: string
  dataset_ref: string
  model_name: string
  artifact_id: string
  artifact_name: string
  artifact_ref: Record<string, unknown>
  artifacts_count: number
  latest_deployment: DeploymentRow | null
  updated_at: string
  created_at: string
}

type ModelRegistryRow = {
  row_key: string
  id: string
  source_kind: 'deployment' | 'job'
  deployment: DeploymentRow | null
  job_model: TrainingJobModelRow | null
  job: DeploymentJobSummary | null
  job_id: string
  department: string
  model_family: string
  artifact_id: string
  artifact_ref: Record<string, unknown>
  target_skill_ids: string[]
  status: string
  rollout_percent: number
  updated_at: string
}

const trainingApi: any = rawTrainingApi
const router = useRouter()
const loading = ref(false)
const loadError = ref('')
const deployments = ref<DeploymentRow[]>([])
const jobs = ref<TrainingJobModelRow[]>([])
const operating = ref<Record<string, boolean>>({})
const rejectTarget = ref<ModelRegistryRow | null>(null)

const rejectModalVisible = computed({
  get: () => Boolean(rejectTarget.value),
  set: (value: boolean) => {
    if (!value) rejectTarget.value = null
  },
})

const deploymentRows = computed(() => deployments.value)
const modelRows = computed<ModelRegistryRow[]>(() => {
  const rows: ModelRegistryRow[] = deploymentRows.value.map((deployment) => modelRowFromDeployment(deployment))
  const deployedJobIds = new Set(rows.map((item) => item.job_id).filter(Boolean))
  for (const job of jobs.value) {
    if (deployedJobIds.has(job.id)) continue
    if (!isModelVisibleJob(job)) continue
    rows.push(modelRowFromJob(job))
  }
  return rows.sort((left, right) => String(right.updated_at || '').localeCompare(String(left.updated_at || '')))
})
const modelReadyCount = computed(() => modelRows.value.filter(item => item.status === 'model_ready').length)
const canaryModelCount = computed(() => modelRows.value.filter(item => item.status === 'canary').length)
const activeModelCount = computed(() => modelRows.value.filter(item => item.status === 'active').length)
const runningModelCount = computed(() => canaryModelCount.value + activeModelCount.value)

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

function normalizeDeployment(item: Record<string, unknown>, fallbackJob?: DeploymentJobSummary | null): DeploymentRow {
  const job = item.job && typeof item.job === 'object' ? item.job as Record<string, unknown> : null
  const normalizedJob = job ? {
    id: String(job.id || ''),
    title: String(job.title || ''),
    status: String(job.status || ''),
    target_skill_id: String(job.target_skill_id || ''),
    target_gateway_id: String(job.target_gateway_id || ''),
    dataset_ref: String(job.dataset_ref || ''),
  } : fallbackJob || null
  return {
    id: String(item.id || ''),
    job_id: String(item.job_id || ''),
    department: String(item.department || ''),
    model_family: String(item.model_family || ''),
    artifact_id: String(item.artifact_id || ''),
    artifact_ref: item.artifact_ref && typeof item.artifact_ref === 'object'
      ? item.artifact_ref as Record<string, unknown>
      : {},
    target_skill_ids: normalizeList<unknown>(item.target_skill_ids).map((value) => String(value || '')).filter(Boolean),
    status: String(item.status || ''),
    rollout_percent: safeNumber(item.rollout_percent),
    rollback_to: String(item.rollback_to || ''),
    deployment_target_gateway_id: String(item.deployment_target_gateway_id || ''),
    job: normalizedJob,
    updated_at: String(item.updated_at || ''),
  }
}

function normalizeTrainingJobModel(item: Record<string, unknown>): TrainingJobModelRow {
  const trainingPlan = item.training_plan && typeof item.training_plan === 'object'
    ? item.training_plan as Record<string, unknown>
    : {}
  const artifact = trainingPlan.artifact && typeof trainingPlan.artifact === 'object'
    ? trainingPlan.artifact as Record<string, unknown>
    : null
  const latestDeployment = item.latest_deployment && typeof item.latest_deployment === 'object'
    ? item.latest_deployment as Record<string, unknown>
    : null
  const jobSummary: DeploymentJobSummary = {
    id: String(item.id || ''),
    title: String(item.title || item.id || ''),
    status: String(item.status || ''),
    target_skill_id: String(item.target_skill_id || ''),
    target_gateway_id: String(item.target_gateway_id || ''),
    dataset_ref: String(item.dataset_ref || ''),
  }
  return {
    id: jobSummary.id,
    title: jobSummary.title,
    department: String(item.department || ''),
    status: jobSummary.status || 'unknown',
    failure_stage: String(item.failure_stage || ''),
    target_skill_id: jobSummary.target_skill_id,
    target_gateway_id: jobSummary.target_gateway_id,
    dataset_ref: jobSummary.dataset_ref || '',
    model_name: String(trainingPlan.model_name || latestDeployment?.model_family || item.target_skill_id || item.title || item.id || ''),
    artifact_id: String(artifact?.id || latestDeployment?.artifact_id || ''),
    artifact_name: String(artifact?.name || artifact?.id || latestDeployment?.artifact_id || ''),
    artifact_ref: artifact || {},
    artifacts_count: safeNumber(trainingPlan.artifacts_count),
    latest_deployment: latestDeployment ? normalizeDeployment(latestDeployment, jobSummary) : null,
    updated_at: String(item.updated_at || item.created_at || ''),
    created_at: String(item.created_at || ''),
  }
}

function modelRowFromDeployment(deployment: DeploymentRow): ModelRegistryRow {
  return {
    row_key: `deployment:${deployment.id}`,
    id: deployment.id,
    source_kind: 'deployment',
    deployment,
    job_model: null,
    job: deployment.job,
    job_id: deployment.job_id,
    department: deployment.department,
    model_family: deployment.model_family || deployment.job?.title || deployment.id,
    artifact_id: deploymentArtifactId(deployment),
    artifact_ref: deployment.artifact_ref,
    target_skill_ids: deployment.target_skill_ids,
    status: deployment.status,
    rollout_percent: deployment.rollout_percent,
    updated_at: deployment.updated_at,
  }
}

function modelRowFromJob(job: TrainingJobModelRow): ModelRegistryRow {
  const status = job.status === 'completed'
    ? 'model_ready'
    : (job.status === 'failed' ? 'failed' : (job.status === 'evaluating' ? 'evaluating' : 'training'))
  return {
    row_key: `job:${job.id}`,
    id: job.id,
    source_kind: 'job',
    deployment: null,
    job_model: job,
    job: {
      id: job.id,
      title: job.title,
      status: job.status,
      target_skill_id: job.target_skill_id,
      target_gateway_id: job.target_gateway_id,
      dataset_ref: job.dataset_ref,
    },
    job_id: job.id,
    department: job.department,
    model_family: job.model_name || job.title || job.id,
    artifact_id: job.artifact_id,
    artifact_ref: job.artifact_ref,
    target_skill_ids: job.target_skill_id ? [job.target_skill_id] : [],
    status,
    rollout_percent: 0,
    updated_at: job.updated_at || job.created_at,
  }
}

function isModelVisibleJob(job: TrainingJobModelRow): boolean {
  return Boolean(
    job.latest_deployment
    || job.model_name
    || job.artifact_id
    || job.artifacts_count > 0
    || ['completed', 'evaluating', 'running', 'failed'].includes(job.status),
  )
}

function deploymentStatusLabel(value: string): string {
  const labels: Record<string, string> = {
    candidate: '部署候选',
    evaluated: '部署已评估',
    awaiting_review: '部署待审',
    canary: '灰度中',
    active: '已部署',
    rejected: '已驳回',
    failed: '部署失败',
    rolled_back: '已回滚',
  }
  return labels[value] || value || '部署'
}

function modelStatusLabel(record: ModelRegistryRow): string {
  if (record.source_kind === 'deployment') return deploymentStatusLabel(record.status)
  const labels: Record<string, string> = {
    training: '训练中',
    evaluating: '评估中',
    model_ready: '模型待部署',
    failed: '训练失败',
  }
  return labels[record.status] || jobStatusLabel(record.job?.status || '')
}

function deploymentStatusColor(value: string): string {
  if (value === 'awaiting_review') return 'orange'
  if (['candidate', 'evaluated', 'canary'].includes(value)) return 'arcoblue'
  if (value === 'active') return 'green'
  if (value === 'rejected') return 'red'
  if (value === 'failed') return 'red'
  if (value === 'rolled_back') return 'gray'
  return 'gray'
}

function modelStatusColor(record: ModelRegistryRow): string {
  if (record.source_kind === 'deployment') return deploymentStatusColor(record.status)
  if (record.status === 'model_ready') return 'arcoblue'
  if (record.status === 'training' || record.status === 'evaluating') return 'orange'
  if (record.status === 'failed') return 'red'
  return 'gray'
}

function jobStatusLabel(value: string): string {
  const labels: Record<string, string> = {
    awaiting_review: '训练待审批',
    queued: '训练排队',
    running: '训练中',
    evaluating: '评估中',
    completed: '训练完成',
    failed: '训练失败',
    cancelled: '训练取消',
    unknown: '训练未知',
  }
  return labels[value] || value || '训练未知'
}

function formatTime(value: string): string {
  if (!value) return '-'
  return value.replace('T', ' ').slice(0, 16)
}

function statusCount(status: string): number {
  return modelRows.value.filter((item) => item.status === status).length
}

function deploymentArtifactId(record: DeploymentRow | ModelRegistryRow): string {
  return String((record.artifact_ref || {}).id || record.artifact_id || '')
}

function routeTargetLabel(record: ModelRegistryRow): string {
  return [record.department || '-', record.target_skill_ids[0] || record.job?.target_skill_id || '-']
    .filter(Boolean)
    .join(' / ')
}

function chatDisabledReason(record: ModelRegistryRow): string {
  if (record.source_kind !== 'deployment') return '模型尚未部署'
  if (!record.deployment?.id) return '缺少部署 ID'
  if (!['canary', 'active'].includes(record.status)) return '部署未进入灰度或已部署'
  if (!record.model_family) return '缺少模型名称'
  if (!record.department) return '缺少部门路由'
  if (!deploymentArtifactId(record)) return '缺少模型产物'
  return ''
}

function setOperating(id: string, value: boolean) {
  const next = { ...operating.value }
  if (value) next[id] = true
  else delete next[id]
  operating.value = next
}

function applyDeploymentResult(record: ModelRegistryRow, result: any) {
  const deployment = result?.deployment && typeof result.deployment === 'object'
    ? normalizeDeployment({ ...result.deployment, job: result?.job || record.job })
    : null
  const updatedJob = result?.job && typeof result.job === 'object' ? result.job as Record<string, unknown> : null
  if (!deployment) return
  if (updatedJob) {
    deployment.job = {
      id: String(updatedJob.id || deployment.job_id),
      title: String(updatedJob.title || record.job?.title || ''),
      status: String(updatedJob.status || record.job?.status || ''),
      target_skill_id: String(updatedJob.target_skill_id || record.job?.target_skill_id || ''),
      target_gateway_id: String(updatedJob.target_gateway_id || record.job?.target_gateway_id || ''),
      dataset_ref: String(updatedJob.dataset_ref || record.job?.dataset_ref || ''),
    }
  }
  deployments.value = deployments.value.some((item) => item.id === deployment.id)
    ? deployments.value.map((item) => item.id === deployment.id ? deployment : item)
    : [deployment, ...deployments.value]
  if (updatedJob) {
    const normalizedJob = normalizeTrainingJobModel(updatedJob)
    jobs.value = jobs.value.some((item) => item.id === normalizedJob.id)
      ? jobs.value.map((item) => item.id === normalizedJob.id ? normalizedJob : item)
      : [normalizedJob, ...jobs.value]
  }
}

async function loadModels() {
  loading.value = true
  loadError.value = ''
  try {
    const [deploymentResult, jobResult] = await Promise.all([
      trainingApi.deployments(),
      trainingApi.jobs({}),
    ])
    deployments.value = normalizeList<Record<string, unknown>>(deploymentResult?.items).map((item) => normalizeDeployment(item))
    jobs.value = normalizeList<Record<string, unknown>>(jobResult?.items).map(normalizeTrainingJobModel)
  } catch (error: any) {
    deployments.value = []
    jobs.value = []
    loadError.value = error?._message || '加载模型库失败'
  } finally {
    loading.value = false
  }
}

async function runDeploymentAction(
  record: ModelRegistryRow,
  action: () => Promise<any>,
  success: string,
  failure: string,
) {
  const deploymentId = record.deployment?.id || ''
  if (!deploymentId || operating.value[deploymentId]) return
  setOperating(deploymentId, true)
  try {
    const result = await action()
    applyDeploymentResult(record, result)
    Message.success(success)
  } catch (error: any) {
    Message.error(error?._message || failure)
  } finally {
    setOperating(deploymentId, false)
  }
}

async function approveDeployment(record: ModelRegistryRow) {
  const deploymentId = record.deployment?.id || ''
  await runDeploymentAction(
    record,
    () => trainingApi.approveDeployment(deploymentId),
    '已审批部署',
    '部署审批失败',
  )
}

function rejectDeployment(record: ModelRegistryRow) {
  const deploymentId = record.deployment?.id || ''
  if (!deploymentId || operating.value[deploymentId]) return
  rejectTarget.value = record
}

async function submitRejectDeployment(reason: string) {
  const record = rejectTarget.value
  if (!record) return
  await runDeploymentAction(
    record,
    () => trainingApi.rejectDeployment(record.deployment?.id || '', reason),
    '已驳回部署',
    '部署驳回失败',
  )
  rejectTarget.value = null
}

async function activateDeployment(record: ModelRegistryRow) {
  const deploymentId = record.deployment?.id || ''
  await runDeploymentAction(
    record,
    () => trainingApi.activateDeployment(deploymentId),
    '已激活部署',
    '部署激活失败',
  )
}

async function syncOpenWebUI(record: ModelRegistryRow) {
  const deploymentId = record.deployment?.id || ''
  await runDeploymentAction(
    record,
    () => trainingApi.syncDeploymentOpenWebUI(deploymentId),
    '已同步到 OpenWebUI',
    '同步 OpenWebUI 失败',
  )
}

function rollbackDeployment(record: ModelRegistryRow) {
  const deploymentId = record.deployment?.id || ''
  if (!deploymentId || operating.value[deploymentId]) return
  Modal.confirm({
    title: '回滚模型部署',
    content: '回滚只影响模型部署资产，不会修改 Skill 代码或训练任务状态。',
    okText: '回滚',
    okButtonProps: { status: 'danger' } as any,
    onOk: async () => {
      await runDeploymentAction(
        record,
        () => trainingApi.rollbackDeployment(deploymentId, '灰度或线上指标异常，执行人工回滚'),
        '已回滚部署',
        '部署回滚失败',
      )
    },
  })
}

function openJob(id: string) {
  if (id) router.push(`/training/jobs/${id}`)
}

function openDeployment(id: string) {
  if (id) router.push(`/training/deployments/${id}`)
}

function openModelRecord(record: ModelRegistryRow) {
  if (record.deployment?.id) openDeployment(record.deployment.id)
  else if (record.job_id) openJob(record.job_id)
}

function canRequestDeployment(record: ModelRegistryRow): boolean {
  return record.source_kind === 'job'
    && record.status === 'model_ready'
    && record.job_model?.status === 'completed'
    && !record.job_model?.failure_stage
}

async function requestDeployment(record: ModelRegistryRow) {
  if (!canRequestDeployment(record) || operating.value[record.id]) return
  setOperating(record.id, true)
  try {
    const result = await trainingApi.requestDeployment(record.job_id, {
      target_skill_ids: record.target_skill_ids,
      reason: '模型库提交部署审批',
      rollout_percent: 0,
    })
    applyDeploymentResult(record, result)
    Message.success('已提交部署审批')
  } catch (error: any) {
    Message.error(error?._message || '部署审批提交失败')
  } finally {
    setOperating(record.id, false)
  }
}

function chatWithModel(record: ModelRegistryRow) {
  const disabledReason = chatDisabledReason(record)
  if (disabledReason) {
    Message.warning(disabledReason)
    return
  }
  const query = new URLSearchParams()
  if (record.deployment?.id) query.set('model_deployment_id', record.deployment.id)
  if (record.model_family) query.set('model_family', record.model_family)
  if (record.job_id) query.set('training_job_id', record.job_id)
  if (record.department) query.set('department', record.department)
  const artifactId = deploymentArtifactId(record)
  if (artifactId) query.set('artifact_id', artifactId)
  router.push(query.toString() ? `/agent?${query.toString()}` : '/agent')
}

function filenameFromDisposition(value: string, fallback: string) {
  const match = value.match(/filename="?([^";]+)"?/i)
  return match?.[1] || fallback
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

async function downloadModel(record: ModelRegistryRow) {
  const artifactId = String((record.artifact_ref || {}).id || record.artifact_id || '')
  if (!record.job_id || !artifactId) {
    Message.warning('当前模型缺少可下载的训练产物索引')
    return
  }
  try {
    const response = await trainingApi.downloadJobArtifact(record.job_id, artifactId)
    const blob = response?.data instanceof Blob ? response.data : new Blob([response?.data || ''])
    const disposition = String(response?.headers?.['content-disposition'] || '')
    saveBlob(blob, filenameFromDisposition(disposition, `${record.model_family || artifactId}.bin`))
    Message.success('已开始下载模型产物')
  } catch (error: any) {
    Message.error(error?._message || '模型产物下载失败')
  }
}

onMounted(loadModels)
</script>

<style scoped>
/* ─────────── design page chrome ─────────── */
.training-models-page {
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

/* ─────────── buttons —— 30px / 6px / 12.5px ─────────── */
.training-models-page :deep(.arco-btn:not(.arco-btn-primary):not(.arco-btn-text)) {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
  box-shadow: none;
}
.training-models-page :deep(.arco-btn:not(.arco-btn-primary):not(.arco-btn-text):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.training-models-page :deep(.arco-btn-primary) {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
  font-size: 12.5px;
  font-weight: 500;
  box-shadow: none;
}
.training-models-page :deep(.arco-btn-primary:hover) {
  background: #000;
  border-color: #000;
}

/* ─────────── table header —— 11.5px / uppercase / ink-4 ─────────── */
.training-models-page :deep(.arco-table-th) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 8px 12px !important;
}
.training-models-page :deep(.arco-table-th .arco-table-th-title) {
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* ─────────── table cell —— 12.5px / ink-1 ─────────── */
.training-models-page :deep(.arco-table-td) {
  font-size: 12.5px !important;
  color: var(--ai-ink-1) !important;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 10px 12px !important;
  background: var(--ai-surface) !important;
}

/* row hover：surface-2 */
.training-models-page :deep(.arco-table-tr:hover .arco-table-td),
.training-models-page :deep(.arco-table-tr-hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

/* ─────────── a-tag → ai-pill (20px / 4px / 11px / 500) ─────────── */
.training-models-page :deep(.arco-tag.arco-tag-size-small),
.training-models-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
}
.training-models-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.training-models-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.training-models-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.training-models-page :deep(.arco-tag-color-orange),
.training-models-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.training-models-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-2) !important;
  border-color: transparent !important;
}

/* ─────────── KPI row ─────────── */
.model-kpis {
  margin-bottom: 0;
}
.model-card {
  margin-top: 0;
  overflow: hidden;
}

/* ─────────── cell typography ─────────── */
.model-cell {
  min-width: 0;
}
.model-name {
  color: var(--ai-ink-1);
  font-weight: 600;
  font-size: 13px;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.model-meta {
  margin-top: 2px;
  color: var(--ai-ink-4);
  font-size: 11.5px;
  word-break: break-word;
}
.muted-text {
  color: var(--ai-ink-4);
}
.mono {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
</style>
