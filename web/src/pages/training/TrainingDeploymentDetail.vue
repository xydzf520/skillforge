<template>
  <div class="training-deployment-detail training-deployment-detail-page ai-main">
    <div class="training-subpage-head ai-pagehead">
      <div>
        <div class="ai-crumbs">训练 · 模型部署详情</div>
        <h2 class="ai-title">{{ deployment?.model_family || '模型部署详情' }}</h2>
        <p class="ai-sub">部署审批、灰度状态和回滚线索</p>
      </div>
      <div class="training-detail-actions">
        <button class="ai-btn" @click="router.push('/training/models')">
          <SfShellIcon name="arrowl" />
          <span>模型部署</span>
        </button>
        <button class="ai-btn" :disabled="loading" @click="loadDeployment">
          <SfShellIcon name="refresh" />
          <span>刷新</span>
        </button>
        <button
          v-if="deployment"
          class="ai-btn"
          @click="downloadModel"
        >
          <SfShellIcon name="download" />
          <span>下载模型</span>
        </button>
        <button
          v-if="deployment"
          class="ai-btn"
          :disabled="Boolean(chatDisabledReason)"
          @click="chatWithModel"
        >
          <SfShellIcon name="bot" />
          <span>和模型对话</span>
        </button>
        <button
          v-if="deployment && ['canary', 'active'].includes(deployment.status)"
          class="ai-btn"
          :disabled="operating"
          @click="syncOpenWebUI"
        >
          <SfShellIcon name="refresh" />
          <span>同步 OpenWebUI</span>
        </button>
        <button
          v-if="deployment?.status === 'awaiting_review'"
          class="ai-btn primary"
          :disabled="operating"
          @click="approveDeployment"
        >
          <SfShellIcon name="send" />
          <span>审批</span>
        </button>
        <button
          v-if="deployment?.status === 'awaiting_review'"
          class="ai-btn ai-btn-danger"
          :disabled="operating"
          @click="rejectDeployment"
        >
          <SfShellIcon name="x" />
          <span>驳回</span>
        </button>
        <button
          v-if="deployment?.status === 'canary'"
          class="ai-btn primary"
          :disabled="operating"
          @click="activateDeployment"
        >
          <SfShellIcon name="send" />
          <span>激活</span>
        </button>
        <button
          v-if="deployment && ['canary', 'active'].includes(deployment.status)"
          class="ai-btn ai-btn-danger"
          :disabled="operating"
          @click="rollbackDeployment"
        >
          <SfShellIcon name="x" />
          <span>回滚</span>
        </button>
      </div>
    </div>

    <div class="training-detail-body ai-pagebody">
      <a-spin :loading="loading">
        <a-result v-if="loadError" status="error" :title="loadError" />
        <template v-else-if="deployment">
          <a-row :gutter="[16, 16]" class="deployment-kpis">
          <a-col :xs="12" :lg="6">
            <SfKpiCard label="部署状态" :value="deploymentStatusLabel(deployment.status)" :hint="deployment.id" tone="brand" />
          </a-col>
          <a-col :xs="12" :lg="6">
            <SfKpiCard label="灰度比例" :value="deployment.rollout_percent" suffix="%" hint="生产流量占比" tone="info" />
          </a-col>
          <a-col :xs="12" :lg="6">
            <SfKpiCard label="目标 Skill" :value="deployment.target_skill_ids.length" :hint="deployment.target_skill_ids.join(', ') || '-'" tone="neutral" />
          </a-col>
          <a-col :xs="12" :lg="6">
            <SfKpiCard label="训练任务" :value="jobStatusLabel(job?.status || '')" :hint="job?.id || '-'" tone="success" />
          </a-col>
          </a-row>

          <a-card class="detail-card ai-card" title="部署摘要">
          <div class="detail-grid">
            <div class="detail-item">
              <span>模型族</span>
              <strong>{{ deployment.model_family }}</strong>
            </div>
            <div class="detail-item">
              <span>Artifact</span>
              <strong>{{ deployment.artifact_id || '-' }}</strong>
              <small v-if="artifactSha" class="mono">sha256 {{ artifactSha.slice(0, 16) }}...</small>
            </div>
            <div class="detail-item">
              <span>目标 Skill</span>
              <strong>{{ deployment.target_skill_ids.join(', ') || '-' }}</strong>
            </div>
            <div class="detail-item">
              <span>回滚目标</span>
              <strong>{{ deployment.rollback_to || '-' }}</strong>
            </div>
            <div class="detail-item">
              <span>申请人</span>
              <strong>{{ deployment.requested_by || '-' }}</strong>
              <small>{{ formatTime(deployment.created_at) }}</small>
            </div>
            <div class="detail-item">
              <span>审批人</span>
              <strong>{{ deployment.approved_by || '-' }}</strong>
              <small>{{ formatTime(deployment.approved_at) }}</small>
            </div>
            <div class="detail-item">
              <span>驳回人</span>
              <strong>{{ deployment.rejected_by || '-' }}</strong>
              <small>{{ formatTime(deployment.rejected_at) }}</small>
            </div>
          </div>
          <div v-if="deployment.request_reason" class="reason-box">
            {{ deployment.request_reason }}
          </div>
          <div v-if="deployment.reject_reason" class="reason-box rejected">
            {{ deployment.reject_reason }}
          </div>
          </a-card>

          <a-card class="detail-card route-card ai-card" title="Agent 对话路由">
          <div class="route-summary">
            <a-tag :color="chatDisabledReason ? 'gray' : 'green'">
              {{ chatDisabledReason ? '未就绪' : '可对话' }}
            </a-tag>
            <span>{{ chatDisabledReason || '该模型部署已可带上下文进入 Agent 对话。' }}</span>
          </div>
          <div class="route-grid">
            <div class="detail-item">
              <span>部门</span>
              <strong>{{ deployment.department || '-' }}</strong>
            </div>
            <div class="detail-item">
              <span>部署 ID</span>
              <strong class="mono">{{ deployment.id }}</strong>
            </div>
            <div class="detail-item">
              <span>模型名称</span>
              <strong>{{ deployment.model_family || '-' }}</strong>
            </div>
            <div class="detail-item">
              <span>Artifact</span>
              <strong>{{ deploymentArtifactId || '-' }}</strong>
            </div>
            <div class="detail-item">
              <span>目标 Skill</span>
              <strong>{{ deployment.target_skill_ids.join(', ') || job?.target_skill_id || '-' }}</strong>
            </div>
            <div class="detail-item">
              <span>训练网关</span>
              <strong>{{ job?.target_gateway_id || '-' }}</strong>
            </div>
          </div>
          </a-card>

          <a-card class="detail-card ai-card" title="训练任务与评估">
          <div v-if="job" class="job-summary">
            <a-link @click="openJob(job.id)">{{ job.title }}</a-link>
            <span class="muted-text">{{ job.department }} · {{ jobStatusLabel(job.status) }}</span>
            <span class="muted-text mono">{{ job.dataset_ref || '-' }}</span>
          </div>
          <a-space v-if="evalChecks.length" wrap size="mini" class="check-list">
            <a-tag
              v-for="check in evalChecks"
              :key="check.name"
              :color="check.passed ? 'green' : 'red'"
            >
              {{ check.name }} {{ check.actual }} / {{ check.expected }}
            </a-tag>
          </a-space>
          <span v-else class="muted-text">暂无评估门禁记录</span>
          </a-card>

          <a-card v-if="relatedDeployments.length" class="detail-card ai-card" title="同任务部署记录">
          <div class="deployment-list">
            <div v-for="item in relatedDeployments" :key="item.id" class="deployment-row">
              <a-link @click="router.push(`/training/deployments/${item.id}`)">{{ item.id }}</a-link>
              <a-tag :color="deploymentStatusColor(item.status)">{{ deploymentStatusLabel(item.status) }}</a-tag>
              <span class="muted-text">{{ item.rollout_percent }}%</span>
              <span class="muted-text">{{ formatTime(item.updated_at) }}</span>
            </div>
          </div>
          </a-card>
        </template>
        <SfEmptyState
          v-else
          title="模型部署详情"
          description="未找到部署记录"
          hint="部署详情只展示当前账号有权访问的部门范围。"
        />
      </a-spin>
    </div>
    <TrainingDeploymentRejectModal
      v-model:visible="rejectModalVisible"
      :loading="operating"
      @submit="submitRejectDeployment"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message, Modal } from '@arco-design/web-vue'
import { trainingApi as rawTrainingApi } from '@/api'
import { SfKpiCard } from '@/components/sf'
import { SfEmptyState } from '@/components/common'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'
import TrainingDeploymentRejectModal from './TrainingDeploymentRejectModal.vue'

defineOptions({ name: 'TrainingDeploymentDetail' })

type DeploymentRow = {
  id: string
  job_id: string
  department: string
  model_family: string
  artifact_id: string
  artifact_ref: Record<string, unknown>
  eval_task_id: number
  target_skill_ids: string[]
  status: string
  rollout_percent: number
  rollback_to: string
  requested_by: string
  request_reason: string
  approved_by: string
  approved_at: string
  rejected_by: string
  rejected_at: string
  reject_reason: string
  activated_at: string
  created_at: string
  updated_at: string
}

type TrainingTask = {
  id: number
  status: string
  metrics: Record<string, unknown>
}

type TrainingJob = {
  id: string
  title: string
  department: string
  status: string
  job_type: string
  target_skill_id: string
  target_gateway_id: string
  dataset_ref: string
  tasks: TrainingTask[]
  deployments: DeploymentRow[]
}

type EvalCheck = {
  name: string
  actual: unknown
  expected: unknown
  passed: boolean
}

const trainingApi: any = rawTrainingApi
const route = useRoute()
const router = useRouter()
const loading = ref(false)
const operating = ref(false)
const loadError = ref('')
const deployment = ref<DeploymentRow | null>(null)
const job = ref<TrainingJob | null>(null)
const rejectModalVisible = ref(false)

const deploymentId = computed(() => String(route.params.id || ''))
const artifactSha = computed(() => String(deployment.value?.artifact_ref?.sha256 || ''))
const deploymentArtifactId = computed(() => String((deployment.value?.artifact_ref || {}).id || deployment.value?.artifact_id || ''))
const relatedDeployments = computed(() => (job.value?.deployments || []).filter(item => item.id !== deployment.value?.id))
const evalTask = computed(() => {
  const tasks = job.value?.tasks || []
  return [...tasks].reverse().find((task) => String(task.metrics?.op || '') === 'training.evaluate') || null
})
const evalChecks = computed<EvalCheck[]>(() => normalizeList<Record<string, unknown>>(evalTask.value?.metrics?.checks).map(item => ({
  name: String(item.name || item.metric || ''),
  actual: item.actual ?? '-',
  expected: item.expected ?? item.threshold ?? '-',
  passed: Boolean(item.passed),
})))
const chatDisabledReason = computed(() => {
  const item = deployment.value
  if (!item) return '部署记录未加载'
  if (!item.id) return '缺少部署 ID'
  if (!['canary', 'active'].includes(item.status)) return '部署未进入灰度或已部署'
  if (!item.model_family) return '缺少模型名称'
  if (!item.department) return '缺少部门路由'
  if (!deploymentArtifactId.value) return '缺少模型产物'
  return ''
})

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

function normalizeDeployment(item: Record<string, unknown>): DeploymentRow {
  return {
    id: String(item.id || ''),
    job_id: String(item.job_id || ''),
    department: String(item.department || ''),
    model_family: String(item.model_family || ''),
    artifact_id: String(item.artifact_id || ''),
    artifact_ref: item.artifact_ref && typeof item.artifact_ref === 'object'
      ? item.artifact_ref as Record<string, unknown>
      : {},
    eval_task_id: safeNumber(item.eval_task_id),
    target_skill_ids: normalizeList<unknown>(item.target_skill_ids).map(value => String(value || '')).filter(Boolean),
    status: String(item.status || ''),
    rollout_percent: safeNumber(item.rollout_percent),
    rollback_to: String(item.rollback_to || ''),
    requested_by: String(item.requested_by || ''),
    request_reason: String(item.request_reason || ''),
    approved_by: String(item.approved_by || ''),
    approved_at: String(item.approved_at || ''),
    rejected_by: String(item.rejected_by || ''),
    rejected_at: String(item.rejected_at || ''),
    reject_reason: String(item.reject_reason || ''),
    activated_at: String(item.activated_at || ''),
    created_at: String(item.created_at || ''),
    updated_at: String(item.updated_at || ''),
  }
}

function normalizeJob(item: Record<string, unknown>): TrainingJob {
  return {
    id: String(item.id || ''),
    title: String(item.title || ''),
    department: String(item.department || ''),
    status: String(item.status || ''),
    job_type: String(item.job_type || ''),
    target_skill_id: String(item.target_skill_id || ''),
    target_gateway_id: String(item.target_gateway_id || ''),
    dataset_ref: String(item.dataset_ref || ''),
    tasks: normalizeList<Record<string, unknown>>(item.tasks).map(task => ({
      id: safeNumber(task.id),
      status: String(task.status || ''),
      metrics: task.metrics && typeof task.metrics === 'object' ? task.metrics as Record<string, unknown> : {},
    })),
    deployments: normalizeList<Record<string, unknown>>(item.deployments).map(normalizeDeployment),
  }
}

function applyPayload(payload: any) {
  deployment.value = payload?.deployment && typeof payload.deployment === 'object'
    ? normalizeDeployment(payload.deployment)
    : null
  job.value = payload?.job && typeof payload.job === 'object'
    ? normalizeJob(payload.job)
    : null
}

function deploymentStatusLabel(value: string): string {
  const labels: Record<string, string> = {
    awaiting_review: '部署待审',
    canary: '灰度中',
    active: '已部署',
    rejected: '已驳回',
    failed: '部署失败',
    rolled_back: '已回滚',
    candidate: '候选',
    evaluated: '已评估',
  }
  return labels[value] || value || '部署未知'
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

async function loadDeployment() {
  if (!deploymentId.value) return
  loading.value = true
  loadError.value = ''
  try {
    const result = await trainingApi.getDeployment(deploymentId.value)
    applyPayload(result)
  } catch (error: any) {
    deployment.value = null
    job.value = null
    loadError.value = error?._message || '加载模型部署详情失败'
  } finally {
    loading.value = false
  }
}

async function runDeploymentAction(action: () => Promise<any>, success: string, failure: string) {
  if (operating.value) return
  operating.value = true
  try {
    const result = await action()
    applyPayload(result)
    Message.success(success)
  } catch (error: any) {
    Message.error(error?._message || failure)
  } finally {
    operating.value = false
  }
}

async function approveDeployment() {
  if (!deployment.value?.id) return
  await runDeploymentAction(
    () => trainingApi.approveDeployment(deployment.value?.id),
    '已审批部署',
    '部署审批失败',
  )
}

function rejectDeployment() {
  if (!deployment.value?.id || operating.value) return
  rejectModalVisible.value = true
}

async function submitRejectDeployment(reason: string) {
  const id = deployment.value?.id
  if (!id) return
  await runDeploymentAction(
    () => trainingApi.rejectDeployment(id, reason),
    '已驳回部署',
    '部署驳回失败',
  )
  rejectModalVisible.value = false
}

async function activateDeployment() {
  if (!deployment.value?.id) return
  await runDeploymentAction(
    () => trainingApi.activateDeployment(deployment.value?.id),
    '已激活部署',
    '部署激活失败',
  )
}

async function syncOpenWebUI() {
  if (!deployment.value?.id) return
  await runDeploymentAction(
    () => trainingApi.syncDeploymentOpenWebUI(deployment.value?.id),
    '已同步到 OpenWebUI',
    '同步 OpenWebUI 失败',
  )
}

function rollbackDeployment() {
  if (!deployment.value?.id || operating.value) return
  const id = deployment.value.id
  Modal.confirm({
    title: '回滚模型部署',
    content: '回滚只影响模型部署资产，不会修改 Skill 代码或训练任务状态。',
    okText: '回滚',
    okButtonProps: { status: 'danger' } as any,
    onOk: async () => {
      await runDeploymentAction(
        () => trainingApi.rollbackDeployment(id, '灰度或线上指标异常，执行人工回滚'),
        '已回滚部署',
        '部署回滚失败',
      )
    },
  })
}

function openJob(id: string) {
  if (id) router.push(`/training/jobs/${id}`)
}

function chatWithModel() {
  if (!deployment.value) return
  if (chatDisabledReason.value) {
    Message.warning(chatDisabledReason.value)
    return
  }
  const query = new URLSearchParams()
  query.set('model_deployment_id', deployment.value.id)
  if (deployment.value.model_family) query.set('model_family', deployment.value.model_family)
  if (deployment.value.job_id) query.set('training_job_id', deployment.value.job_id)
  if (deployment.value.department) query.set('department', deployment.value.department)
  const artifactId = deploymentArtifactId.value
  if (artifactId) query.set('artifact_id', artifactId)
  if (artifactSha.value) query.set('artifact_sha256', artifactSha.value)
  if (job.value?.target_gateway_id) query.set('target_gateway_id', job.value.target_gateway_id)
  router.push(`/agent?${query.toString()}`)
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

async function downloadModel() {
  const artifactId = String((deployment.value?.artifact_ref || {}).id || deployment.value?.artifact_id || '')
  if (!deployment.value?.job_id || !artifactId) {
    Message.warning('当前部署缺少可下载的训练产物索引')
    return
  }
  try {
    const response = await trainingApi.downloadJobArtifact(deployment.value.job_id, artifactId)
    const blob = response?.data instanceof Blob ? response.data : new Blob([response?.data || ''])
    const disposition = String(response?.headers?.['content-disposition'] || '')
    saveBlob(blob, filenameFromDisposition(disposition, `${deployment.value.model_family || artifactId}.bin`))
    Message.success('已开始下载模型产物')
  } catch (error: any) {
    Message.error(error?._message || '模型产物下载失败')
  }
}

watch(deploymentId, loadDeployment)
onMounted(loadDeployment)
</script>

<style scoped>
.training-deployment-detail-page {
  min-height: 100%;
}
.training-subpage-head {
  align-items: center;
}
.training-detail-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}
.training-detail-actions .ai-btn {
  flex: 0 0 auto;
}
.training-detail-actions .ai-btn svg {
  width: 14px;
  height: 14px;
}
.ai-btn-danger {
  color: var(--ai-bad);
  border-color: var(--ai-bad-soft);
  background: var(--ai-bad-soft);
}
.training-detail-body {
  min-width: 0;
}
.training-deployment-detail-page :deep(.ai-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}
.training-deployment-detail-page :deep(.arco-card-header) {
  border-bottom: 1px solid var(--ai-border);
  min-height: 0;
  padding: 12px 14px;
}
.training-deployment-detail-page :deep(.arco-card-header-title) {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 500;
}
.training-deployment-detail-page :deep(.arco-card-body) {
  padding: 14px;
}

.deployment-kpis {
  margin-bottom: 16px;
}
.detail-card {
  margin-top: 0;
}
.detail-card + .detail-card {
  margin-top: 16px;
}
.detail-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(180px, 1fr));
  gap: 16px;
}
.detail-item {
  min-width: 0;
}
.detail-item span,
.detail-item small {
  display: block;
  color: var(--ai-ink-3);
  font-size: 12px;
}
.detail-item strong {
  display: block;
  margin-top: 4px;
  color: var(--ai-ink-1);
  word-break: break-word;
}
.reason-box {
  margin-top: 16px;
  padding: 12px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  border-radius: 6px;
}
.reason-box.rejected {
  border-color: rgba(var(--red-6), 0.28);
  background: rgba(var(--red-1), 0.55);
  color: rgb(var(--red-7));
}
.job-summary {
  display: grid;
  grid-template-columns: minmax(160px, 1fr) minmax(120px, auto) minmax(180px, 1fr);
  gap: 12px;
  align-items: center;
}
.check-list {
  margin-top: 12px;
}
.deployment-list {
  display: grid;
  gap: 10px;
}
.deployment-row {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) 100px 70px 150px;
  gap: 12px;
  align-items: center;
}
.route-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--ai-ink-2);
}
.route-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(180px, 1fr));
  gap: 16px;
  margin-top: 16px;
}
.muted-text {
  color: var(--ai-ink-3);
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
}
@media (max-width: 720px) {
  .detail-grid,
  .route-grid,
  .job-summary,
  .deployment-row {
    grid-template-columns: 1fr;
  }
}
</style>
