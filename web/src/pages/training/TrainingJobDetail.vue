<template>
  <div class="training-job-page ai-main">
    <div class="page-detail-toolbar ai-pagehead">
      <button class="ai-btn" @click="openTrainingList">
        <SfShellIcon name="arrowl" />
        <span>返回</span>
      </button>
      <span class="toolbar-crumb">/ 训练 / 任务 /</span>
      <span class="toolbar-id mono">{{ job?.id || jobId }}</span>
      <span v-if="job?.title" class="toolbar-title">{{ job.title }}</span>
      <span v-if="job" class="ai-pill" :class="jobStatusPillTone(job.status)">
        {{ jobStatusLabel(job.status) }}
      </span>
      <span v-if="job?.department" class="ai-pill">{{ job.department }}</span>
      <span v-if="targetGatewayId" class="ai-pill">{{ targetGatewayId }}</span>
      <div class="toolbar-spacer" />
      <button v-if="targetGatewayId" class="ai-btn" @click="openGatewayTraining">
        <SfShellIcon name="grid" />
        <span>网关任务</span>
      </button>
      <button v-if="targetGatewayId" class="ai-btn" @click="openAgentDevice">
        <SfShellIcon name="bot" />
        <span>Agent 终端</span>
      </button>
      <button class="ai-btn" :disabled="loading" @click="loadJob">
        <SfShellIcon name="refresh" />
        <span>刷新</span>
      </button>
      <button class="ai-btn" :disabled="!job?.target_gateway_id || Boolean(actionLoading.logs)" @click="openLogs">
        <SfShellIcon name="doc" />
        <span>实时日志</span>
      </button>
      <button class="ai-btn" :disabled="!canViewArtifacts || Boolean(actionLoading.artifacts)" @click="openArtifacts">
        <SfShellIcon name="download" />
        <span>导出 checkpoint</span>
      </button>
      <button
        v-if="canCancelJob"
        class="ai-btn ai-btn-danger"
        :disabled="Boolean(actionLoading.cancel)"
        @click="cancelJob"
      >
        <SfShellIcon name="x" />
        <span>停止训练</span>
      </button>
    </div>

    <div class="training-job-body ai-pagebody">
      <a-spin :loading="loading" style="display: block">
      <a-result v-if="loadError" status="error" :title="loadError" class="job-error-card" />

      <template v-else-if="job">
        <section class="job-status-strip">
          <div v-for="item in trainingMetricCells" :key="item.label" class="job-status-cell">
            <div class="kpi-label">{{ item.label }}</div>
            <div class="kpi-value mono" :class="item.tone">{{ item.value }}</div>
            <div class="kpi-sub">{{ item.sub }}</div>
          </div>
        </section>

        <LearningFlowMini
          class="training-flow-card"
          entity-type="training_job"
          :entity-id="job.id"
          title="训练数据流"
          subtitle="查看样本、训练任务、模型产物、部署和 Skill 回流的上下游血缘。"
        />

        <section class="job-live-grid">
          <div class="job-live-left">
            <section class="ai-card train-curve-card">
              <div class="ai-card-h">
                <span class="t">训练曲线</span>
                <div class="curve-tabs">
                  <span class="ai-pill active">loss</span>
                  <span class="ai-pill">val_loss</span>
                  <span class="ai-pill">acc</span>
                  <span class="ai-pill">lr</span>
                </div>
              </div>
              <svg class="curve-svg" viewBox="0 0 600 220" preserveAspectRatio="none" aria-label="训练曲线">
                <line v-for="index in 5" :key="index" x1="0" :y1="20 + (index - 1) * 45" x2="600" :y2="20 + (index - 1) * 45" class="curve-grid-line" />
                <polyline :points="curvePoints.loss" class="curve-loss" />
                <polyline :points="curvePoints.reference" class="curve-reference" />
                <circle :cx="curvePoints.last.x" :cy="curvePoints.last.y" r="4" class="curve-dot" />
              </svg>
              <div class="curve-axis">
                <span>step 0</span>
                <span>25%</span>
                <span>50%</span>
                <span>75%</span>
                <span>100%</span>
              </div>
            </section>

            <section class="ai-card sample-card">
              <div class="ai-card-h">
                <span class="t">数据采样 · {{ sampleRows.length }} 条</span>
                <span class="s">来自训练任务指标与评估门禁</span>
              </div>
              <table class="ai-table sample-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>样本 / 检查</th>
                    <th>预测</th>
                    <th>实际</th>
                    <th>置信</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, index) in sampleRows" :key="`${row.text}-${index}`" :class="{ mismatch: row.mismatch }">
                    <td class="mono tiny muted">{{ index + 1 }}</td>
                    <td>{{ row.text }}</td>
                    <td><span class="ai-pill" :class="{ bad: row.mismatch }">{{ row.predicted }}</span></td>
                    <td><span class="ai-pill">{{ row.actual }}</span></td>
                    <td class="mono">{{ row.confidence }}</td>
                  </tr>
                </tbody>
              </table>
            </section>
          </div>

          <aside class="job-live-side">
            <section class="ai-card config-card">
              <div class="ai-card-h"><span class="t">训练配置</span></div>
              <div class="config-list">
                <div v-for="item in trainingConfigRows" :key="item.key" class="config-row">
                  <span>{{ item.key }}</span>
                  <b class="mono">{{ item.value }}</b>
                </div>
              </div>
            </section>

            <section class="ai-card log-tail-card">
              <div class="ai-card-h">
                <span class="t">日志 · tail</span>
                <span class="ai-pill ok dot log-follow">follow</span>
              </div>
              <div class="log-tail">
                <div v-for="(line, index) in inlineLogRows" :key="`${line.time}-${index}`" class="log-tail-line">
                  <span class="log-time">{{ line.time }}</span>
                  <span class="log-level" :class="line.level.toLowerCase()">{{ line.level }}</span>
                  <span>{{ line.message }}</span>
                </div>
                <div class="log-cursor">▍</div>
              </div>
            </section>
          </aside>
        </section>

        <!-- Action bar -->
        <div v-if="hasActionButtons" class="job-action-bar">
          <button
            v-if="canApproveJob"
            class="ai-btn primary"
            :disabled="Boolean(actionLoading.approve)"
            @click="approveJob"
          >
            <SfShellIcon name="send" />
            <span>审批训练</span>
          </button>
          <button
            v-if="canDispatchJob"
            class="ai-btn"
            :disabled="Boolean(actionLoading.dispatch)"
            @click="dispatchJob(false)"
          >
            <SfShellIcon name="send" />
            <span>下发网关</span>
          </button>
          <button
            v-if="canRetryJob"
            class="ai-btn"
            :disabled="Boolean(actionLoading.dispatch)"
            @click="dispatchJob(true)"
          >
            <SfShellIcon name="refresh" />
            <span>重试下发</span>
          </button>
          <button
            v-if="canCollectResult"
            class="ai-btn"
            :disabled="Boolean(actionLoading.collect)"
            @click="collectResult"
          >
            <SfShellIcon name="refresh" />
            <span>同步结果</span>
          </button>
          <button
            v-if="canEvaluateJob"
            class="ai-btn"
            :disabled="Boolean(actionLoading.evaluate)"
            @click="evaluateJob"
          >
            <SfShellIcon name="doc" />
            <span>评估</span>
          </button>
          <button
            v-if="canRequestDeployment"
            class="ai-btn primary"
            :disabled="Boolean(actionLoading.deploy)"
            @click="requestDeployment"
          >
            <SfShellIcon name="send" />
            <span>提交部署</span>
          </button>
          <button
            v-if="canApproveDeployment"
            class="ai-btn primary"
            :disabled="Boolean(actionLoading.deployment)"
            @click="approveDeployment"
          >
            <SfShellIcon name="send" />
            <span>审批部署</span>
          </button>
          <button
            v-if="canRejectDeployment"
            class="ai-btn ai-btn-danger"
            :disabled="Boolean(actionLoading.deployment)"
            @click="rejectDeployment"
          >
            <SfShellIcon name="x" />
            <span>驳回部署</span>
          </button>
          <button
            v-if="canActivateDeployment"
            class="ai-btn"
            :disabled="Boolean(actionLoading.deployment)"
            @click="activateDeployment"
          >
            <SfShellIcon name="send" />
            <span>激活部署</span>
          </button>
          <button
            v-if="canCancelJob"
            class="ai-btn ai-btn-danger"
            :disabled="Boolean(actionLoading.cancel)"
            @click="cancelJob"
          >
            <SfShellIcon name="x" />
            <span>取消</span>
          </button>
          <button
            v-if="canForceCancelJob"
            class="ai-btn ai-btn-danger"
            :disabled="Boolean(actionLoading.forceCancel)"
            @click="forceCancelJob"
          >
            <SfShellIcon name="x" />
            <span>强制取消</span>
          </button>
          <button
            v-if="canRollbackDeployment"
            class="ai-btn ai-btn-danger"
            :disabled="Boolean(actionLoading.deployment)"
            @click="rollbackDeployment"
          >
            <SfShellIcon name="x" />
            <span>回滚部署</span>
          </button>
        </div>

        <!-- KPI strip -->
        <div class="job-kpi-strip">
          <div class="kpi-cell">
            <div class="kpi-label">训练类型</div>
            <div class="kpi-value mono">{{ taskLabel(job.job_type) }}</div>
            <div class="kpi-sub">{{ job.training_strategy || '-' }}</div>
          </div>
          <div class="kpi-cell">
            <div class="kpi-label">目标网关</div>
            <div class="kpi-value mono">{{ job.target_gateway_id || '-' }}</div>
            <div class="kpi-sub">{{ job.department || '-' }}</div>
          </div>
          <div class="kpi-cell">
            <div class="kpi-label">Worker Task</div>
            <div class="kpi-value mono">{{ tasks.length }}</div>
            <div class="kpi-sub">{{ latestTaskHint }}</div>
          </div>
          <div class="kpi-cell">
            <div class="kpi-label">部署记录</div>
            <div class="kpi-value mono">{{ deployments.length }}</div>
            <div class="kpi-sub">{{ latestDeploymentHint }}</div>
          </div>
          <div class="kpi-cell">
            <div class="kpi-label">产物</div>
            <div class="kpi-value mono">{{ trainingPlan.artifacts_count || (primaryArtifact ? 1 : 0) }}</div>
            <div class="kpi-sub">{{ artifactSummaryText }}</div>
          </div>
        </div>

        <!-- Cockpit / Fine-tune row -->
        <section class="ai-card cockpit-card">
          <div class="ai-card-h">
            <span class="t">Fine-tune Model</span>
            <span class="s">{{ trainingPlan.base_model || '-' }} · {{ job.dataset_ref || '-' }}</span>
            <span class="ai-pill" :class="jobStatusPillTone(job.status)" style="margin-left: auto">
              {{ jobStatusLabel(job.status) }}
            </span>
          </div>
          <div class="cockpit-body">
            <div class="cockpit-progress">
              <div class="cockpit-progress-row">
                <span class="cockpit-progress-label">训练进度</span>
                <span class="cockpit-progress-num mono">{{ heroProgressPercent }}%</span>
              </div>
              <div class="progress-track">
                <div class="progress-fill" :style="{ width: `${heroProgressPercent}%` }" />
              </div>
              <div class="cockpit-meta">
                <span><em>基座</em> <b class="mono">{{ trainingPlan.base_model || '-' }}</b></span>
                <span><em>数据</em> <b class="mono">{{ job.dataset_ref || '-' }}</b></span>
                <span><em>预计</em> <b class="mono">{{ runtimeSummary.eta_text || durationText(runtimeSummary.estimated_duration_seconds) }}</b></span>
              </div>
            </div>
            <div class="cockpit-side">
              <div class="cockpit-side-row">
                <span class="cockpit-side-label">训练参数</span>
                <b class="cockpit-side-value">{{ parameterSummaryText(trainingPlan.parameters) }}</b>
              </div>
              <div class="cockpit-side-row">
                <span class="cockpit-side-label">产物</span>
                <b class="cockpit-side-value mono">{{ artifactSummaryText }}</b>
              </div>
              <div class="cockpit-side-row">
                <span class="cockpit-side-label">Agent 对话</span>
                <div class="chat-route-line">
                  <span class="ai-pill" :class="chatDisabledReason ? '' : 'ok'">
                    {{ chatDisabledReason ? '未就绪' : '可对话' }}
                  </span>
                  <em>{{ chatDisabledReason || '已具备部署、部门和模型产物上下文' }}</em>
                </div>
              </div>
              <div class="cockpit-actions">
                <button class="ai-btn sm" :disabled="!primaryArtifact" @click="openArtifacts">
                  <SfShellIcon name="folder" />
                  <span>产物</span>
                </button>
                <button
                  class="ai-btn sm"
                  :disabled="!primaryArtifact || Boolean(actionLoading.artifactsDownload)"
                  @click="downloadArtifact(primaryArtifact)"
                >
                  <SfShellIcon name="download" />
                  <span>下载模型</span>
                </button>
                <button class="ai-btn sm" :disabled="Boolean(chatDisabledReason)" @click="chatWithModel">
                  <SfShellIcon name="bot" />
                  <span>和模型对话</span>
                </button>
              </div>
            </div>
          </div>
        </section>

        <!-- 阶段时间线 -->
        <section class="ai-card stage-card">
          <div class="ai-card-h">
            <span class="t">阶段时间线</span>
            <span class="s">审批 → 下发 → 训练 → 评估 → 部署</span>
          </div>
          <div class="stage-grid">
            <div v-for="stage in stageRows" :key="stage.key" class="stage-item" :class="`stage-${stage.state}`">
              <div class="stage-dot" />
              <div class="stage-main">
                <div class="stage-label">{{ stage.label }}</div>
                <div class="stage-value">{{ stage.value }}</div>
              </div>
            </div>
          </div>
        </section>

        <!-- 任务信息 + 评估与部署 -->
        <div class="info-grid">
          <section class="ai-card info-card">
            <div class="ai-card-h"><span class="t">任务信息</span></div>
            <div class="info-rows">
              <div v-for="item in infoItems" :key="item.label" class="info-row" :class="{ 'info-row-wide': item.span && item.span > 1 }">
                <span class="info-key">{{ item.label }}</span>
                <span class="info-value" :class="{ mono: item.mono }">{{ item.value || '-' }}</span>
              </div>
            </div>
          </section>

          <section class="ai-card info-card">
            <div class="ai-card-h"><span class="t">评估与部署</span></div>
            <div class="info-body">
              <div v-if="evaluationTask" class="evaluation-box" :class="{ failed: evaluationTask.status === 'failed' }">
                <div class="evaluation-title">
                  <span>评估门禁</span>
                  <span class="ai-pill" :class="evaluationTask.status === 'completed' ? 'ok' : 'bad'">
                    {{ evaluationTask.status === 'completed' ? '通过' : '未通过' }}
                  </span>
                </div>
                <div class="evaluation-meta">{{ evaluationSummary }}</div>
              </div>
              <div v-if="deployments.length" class="deployment-list">
                <div v-for="deployment in deployments" :key="deployment.id" class="deployment-item">
                  <div class="deployment-row">
                    <span class="deployment-id mono">{{ deployment.id }}</span>
                    <span class="ai-pill" :class="deploymentStatusPillTone(deployment.status)">
                      {{ deploymentStatusLabel(deployment.status) }}
                    </span>
                  </div>
                  <div class="deployment-meta">
                    {{ deployment.model_family || '-' }} · 灰度 <span class="mono">{{ deployment.rollout_percent || 0 }}%</span>
                  </div>
                  <div class="deployment-meta">artifact <span class="mono">{{ deployment.artifact_id || '-' }}</span></div>
                </div>
              </div>
              <SfEmptyState
                v-else
                title="暂无部署记录"
                description="评估通过后可提交模型部署审批"
              />
            </div>
          </section>
        </div>

        <!-- Worker Tasks -->
        <section class="ai-card worker-task-card">
          <div class="ai-card-h"><span class="t">Worker Tasks</span><span class="s">网关执行的训练子任务</span></div>
          <a-table v-if="tasks.length" :data="tasks" :pagination="false" row-key="id" size="small" :bordered="false">
            <template #columns>
              <a-table-column title="Task" :width="90">
                <template #cell="{ record }">
                  <span class="mono">#{{ record.id }}</span>
                </template>
              </a-table-column>
              <a-table-column title="状态" :width="120">
                <template #cell="{ record }">
                  <a-tag :color="taskStatusColor(record.status)">{{ taskStatusLabel(record.status) }}</a-tag>
                </template>
              </a-table-column>
              <a-table-column title="网关 / Worker">
                <template #cell="{ record }">
                  <div class="mono">{{ record.gateway_id || '-' }}</div>
                  <div class="muted-text mono">{{ record.worker_id || '-' }}</div>
                </template>
              </a-table-column>
              <a-table-column title="进度" :width="180">
                <template #cell="{ record }">
                  <div class="task-progress">
                    <div class="progress-track">
                      <div class="progress-fill" :style="{ width: `${Math.round((record.progress || 0) * 100)}%` }" />
                    </div>
                    <span class="mono progress-num">{{ Math.round((record.progress || 0) * 100) }}%</span>
                  </div>
                </template>
              </a-table-column>
              <a-table-column title="指标摘要">
                <template #cell="{ record }">
                  <span class="metric-summary mono">{{ metricsSummary(record.metrics) }}</span>
                </template>
              </a-table-column>
              <a-table-column title="更新时间" :width="170">
                <template #cell="{ record }">
                  <span class="muted-text mono">{{ formatTime(record.updated_at) }}</span>
                </template>
              </a-table-column>
            </template>
          </a-table>
          <div v-else class="empty-padded">
            <SfEmptyState title="暂无 Worker Task" description="审批后下发到训练网关会生成任务记录" />
          </div>
        </section>

        <!-- Payload section -->
        <div class="payload-grid">
          <section class="ai-card payload-card">
            <div class="ai-card-h"><span class="t">训练规格</span></div>
            <pre class="json-panel mono">{{ specText }}</pre>
          </section>
          <section class="ai-card payload-card">
            <div class="ai-card-h"><span class="t">网关 Payload</span></div>
            <pre class="json-panel mono">{{ gatewayPayloadText }}</pre>
          </section>
        </div>
      </template>
      </a-spin>
    </div>

    <a-modal v-model:visible="logsVisible" :title="logsTitle" :footer="false" :width="'min(90vw, 760px)'">
      <a-spin :loading="logsLoadingModal">
        <pre v-if="logsText" class="logs-panel mono">{{ logsText }}</pre>
        <SfEmptyState v-else title="训练日志" description="暂无日志" hint="日志来自训练网关 Bridge，不包含密钥字段。" />
      </a-spin>
    </a-modal>

    <a-modal v-model:visible="artifactsVisible" :title="artifactsTitle" :footer="false" :width="'min(90vw, 820px)'">
      <a-spin :loading="artifactsLoadingModal">
        <div v-if="artifactRows.length" class="artifact-list">
          <div v-for="item in artifactRows" :key="item.id" class="artifact-item">
            <div class="artifact-main">
              <span class="ai-pill accent">{{ item.type }}</span>
              <span class="artifact-name mono">{{ item.name }}</span>
              <span class="muted-text mono">{{ formatBytes(item.size_bytes) }}</span>
              <button class="ai-btn sm" @click="downloadArtifact(item)">下载</button>
            </div>
            <div class="artifact-meta">
              <span class="mono">{{ item.uri || '-' }}</span>
              <span v-if="item.sha256" class="mono">sha256: {{ item.sha256.slice(0, 12) }}...</span>
              <span v-if="!item.downloadable" class="ai-pill">仅元数据</span>
            </div>
          </div>
        </div>
        <SfEmptyState v-else title="训练产物" description="暂无产物" hint="产物页只展示脱敏 manifest，不透传预签名 URL。" />
      </a-spin>
    </a-modal>
    <TrainingDeploymentRejectModal
      v-model:visible="rejectModalVisible"
      :loading="Boolean(actionLoading.deployment)"
      @submit="submitRejectDeployment"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message, Modal } from '@arco-design/web-vue'
import { trainingApi as rawTrainingApi } from '@/api'
import { SfEmptyState } from '@/components/common'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'
import LearningFlowMini from '@/components/learning/LearningFlowMini.vue'
import TrainingDeploymentRejectModal from './TrainingDeploymentRejectModal.vue'

defineOptions({ name: 'TrainingJobDetail' })

type TrainingTask = {
  id: number | string
  gateway_id: string
  worker_id: string
  status: string
  progress: number
  metrics: Record<string, unknown>
  error_message: string
  created_at: string
  updated_at: string
}

type TrainingDeployment = {
  id: string
  status: string
  model_family: string
  artifact_id: string
  artifact_ref: Record<string, unknown>
  target_skill_ids: string[]
  rollout_percent: number
  rollback_to: string
  rejected_by?: string
  rejected_at?: string
  reject_reason?: string
  created_at: string
  updated_at: string
}

type TrainingPlan = {
  model_name: string
  base_model: string
  parameters: Record<string, unknown>
  runtime: {
    progress: number
    estimated_duration_seconds: number
    remaining_seconds: number
    eta_text: string
  }
  artifact: ArtifactRow | null
  artifacts_count: number
  chat_target: {
    ready?: boolean
    disabled_reason?: string
    model_deployment_id?: string
    model_family?: string
    training_job_id?: string
    department?: string
    artifact_id?: string
    target_skill_ids?: string[]
    target_gateway_id?: string
    deployment_status?: string
  }
}

type TrainingJobDetail = {
  id: string
  title: string
  department: string
  created_by: string
  status: string
  job_type: string
  training_strategy: string
  target_skill_id: string
  target_gateway_id: string
  dataset_ref: string
  objective: string
  risk_level: string
  failure_stage: string
  spec: Record<string, unknown>
  gateway_payload: Record<string, unknown>
  approval_required: boolean
  approved_by: string
  approved_at: string
  cancelled_by: string
  cancelled_at: string
  created_at: string
  updated_at: string
  tasks: TrainingTask[]
  deployments: TrainingDeployment[]
  latest_deployment: TrainingDeployment | null
  training_plan: TrainingPlan
}

type ArtifactRow = {
  id: string
  type: string
  name: string
  uri: string
  sha256: string
  size_bytes: number
  downloadable: boolean
  download_url: string
}

const FINAL_JOB_STATUSES = ['completed', 'failed', 'cancelled']
const trainingApi: any = rawTrainingApi
const route = useRoute()
const router = useRouter()
const jobId = computed(() => String(route.params.id || ''))
const loading = ref(false)
const loadError = ref('')
const job = ref<TrainingJobDetail | null>(null)
const actionLoading = ref<Record<string, boolean>>({})
const logsVisible = ref(false)
const logsLoadingModal = ref(false)
const logsTitle = ref('训练日志')
const logsText = ref('')
const artifactsVisible = ref(false)
const artifactsLoadingModal = ref(false)
const artifactsTitle = ref('训练产物')
const artifactRows = ref<ArtifactRow[]>([])
const rejectModalVisible = ref(false)

const tasks = computed(() => job.value?.tasks || [])
const deployments = computed(() => job.value?.deployments || [])
const latestDeployment = computed(() => job.value?.latest_deployment || deployments.value[0] || null)
const latestTask = computed(() => tasks.value[tasks.value.length - 1] || null)
const trainingPlan = computed<TrainingPlan>(() => job.value?.training_plan || emptyTrainingPlan())
const runtimeSummary = computed(() => trainingPlan.value.runtime || emptyTrainingPlan().runtime)
const chatTarget = computed(() => trainingPlan.value.chat_target || {})
const primaryArtifact = computed(() => trainingPlan.value.artifact || artifactRows.value[0] || null)
const chatArtifactId = computed(() => String(
  chatTarget.value.artifact_id
  || (latestDeployment.value?.artifact_ref || {}).id
  || latestDeployment.value?.artifact_id
  || primaryArtifact.value?.id
  || '',
))
const artifactSummaryText = computed(() => {
  const artifact = primaryArtifact.value
  if (!artifact) return '等待产物'
  const hash = artifact.sha256 ? ` · sha256 ${artifact.sha256.slice(0, 12)}...` : ''
  return `${artifact.name || artifact.type}${hash}`
})
const targetGatewayId = computed(() => job.value?.target_gateway_id || '')
const gatewayTrainingPath = computed(() => targetGatewayId.value ? `/training?gateway=${encodeURIComponent(targetGatewayId.value)}` : '/training')
const agentDevicePath = computed(() => targetGatewayId.value ? `/admin/agent-devices/${encodeURIComponent(targetGatewayId.value)}` : '')
const evaluationTask = computed(() => {
  return [...tasks.value].reverse().find((task) => String((task.metrics || {}).op || '') === 'training.evaluate') || null
})
const latestTaskHint = computed(() => latestTask.value ? taskStatusLabel(latestTask.value.status) : '尚未下发')
const latestDeploymentHint = computed(() => latestDeployment.value ? deploymentStatusLabel(latestDeployment.value.status) : '暂无部署')
const canApproveJob = computed(() => job.value?.status === 'awaiting_review')
const canDispatchJob = computed(() => job.value?.status === 'queued' && job.value?.failure_stage !== 'dispatch')
const canRetryJob = computed(() => job.value?.status === 'queued' && job.value?.failure_stage === 'dispatch')
const canCollectResult = computed(() => Boolean(job.value?.target_gateway_id)
  && ['queued', 'running', 'evaluating', 'unknown'].includes(job.value?.status || '')
  && job.value?.failure_stage !== 'dispatch')
const canEvaluateJob = computed(() => job.value?.status === 'completed')
const canCancelJob = computed(() => Boolean(job.value)
  && job.value?.failure_stage !== 'cancel'
  && !FINAL_JOB_STATUSES.includes(job.value?.status || ''))
const canForceCancelJob = computed(() => job.value?.failure_stage === 'cancel'
  && ['running', 'evaluating', 'unknown'].includes(job.value?.status || ''))
const canViewArtifacts = computed(() => Boolean(job.value)
  && (['completed', 'failed', 'evaluating'].includes(job.value?.status || '') || tasks.value.length > 0))
const canRequestDeployment = computed(() => {
  const blocked = ['candidate', 'evaluated', 'awaiting_review', 'canary', 'active']
  return job.value?.status === 'completed'
    && !job.value?.failure_stage
    && !blocked.includes(latestDeployment.value?.status || '')
})
const canApproveDeployment = computed(() => latestDeployment.value?.status === 'awaiting_review')
const canRejectDeployment = computed(() => latestDeployment.value?.status === 'awaiting_review')
const canActivateDeployment = computed(() => latestDeployment.value?.status === 'canary')
const canRollbackDeployment = computed(() => ['canary', 'active'].includes(latestDeployment.value?.status || ''))
const chatDisabledReason = computed(() => {
  const target = chatTarget.value
  if (target.ready === true) return ''
  if (target.disabled_reason) return String(target.disabled_reason)
  if (!latestDeployment.value) return '模型尚未提交部署审批'
  if (!['canary', 'active'].includes(latestDeployment.value.status || '')) return `部署状态为 ${latestDeployment.value.status || 'unknown'}，需进入灰度或已部署`
  if (!latestDeployment.value.id) return '缺少模型部署 ID'
  if (!(target.model_family || latestDeployment.value.model_family || trainingPlan.value.model_name)) return '缺少模型名称'
  if (!(target.department || job.value?.department)) return '缺少部门路由'
  if (!chatArtifactId.value) return '缺少模型产物'
  return ''
})
const heroProgressPercent = computed(() => {
  const raw = safeNumber(runtimeSummary.value.progress)
  // backend may emit 0~1 ratio OR 0~100 percent — normalize to percent
  const percent = raw > 1 ? raw : raw * 100
  return Math.max(0, Math.min(100, Math.round(percent)))
})
const hasActionButtons = computed(() => {
  return canApproveJob.value
    || canDispatchJob.value
    || canRetryJob.value
    || canCollectResult.value
    || canEvaluateJob.value
    || canRequestDeployment.value
    || canApproveDeployment.value
    || canRejectDeployment.value
    || canActivateDeployment.value
    || canCancelJob.value
    || canForceCancelJob.value
    || canRollbackDeployment.value
})
const evaluationSummary = computed(() => {
  const metrics = evaluationTask.value?.metrics || {}
  const checks = normalizeList<Record<string, unknown>>(metrics.checks)
  if (!checks.length) return metricsSummary(metrics)
  return checks
    .map((item) => `${String(item.name || item.metric || 'check')}: ${item.passed ? '通过' : '未通过'}`)
    .join(' · ')
})
const infoItems = computed(() => {
  const current = job.value
  if (!current) return []
  return [
    { label: '任务 ID', value: current.id, mono: true },
    { label: '部门', value: current.department },
    { label: '目标 Skill', value: current.target_skill_id, mono: true },
    { label: '目标网关', value: current.target_gateway_id, mono: true },
    { label: '数据集', value: current.dataset_ref, mono: true, span: 2 },
    { label: '训练目标', value: current.objective, span: 2 },
    { label: '风险等级', value: current.risk_level },
    { label: '创建人', value: current.created_by },
    { label: '审批人', value: current.approved_by },
    { label: '审批时间', value: formatTime(current.approved_at) },
    { label: '创建时间', value: formatTime(current.created_at) },
    { label: '更新时间', value: formatTime(current.updated_at) },
  ]
})
const specText = computed(() => safeJson(job.value?.spec || {}))
const gatewayPayloadText = computed(() => safeJson(job.value?.gateway_payload || {}))
const stageRows = computed(() => {
  const current = job.value
  if (!current) return []
  const status = current.status
  const failure = current.failure_stage
  return [
    {
      key: 'review',
      label: '审批',
      value: current.approved_at ? `已审批 ${formatTime(current.approved_at)}` : '等待训练审批',
      state: status === 'awaiting_review' ? 'active' : 'done',
    },
    {
      key: 'dispatch',
      label: '下发',
      value: current.target_gateway_id || '未指定网关',
      state: failure === 'dispatch' ? 'failed' : ['queued'].includes(status) ? 'active' : stageReached(['running', 'evaluating', 'completed', 'failed', 'cancelled', 'unknown']) ? 'done' : 'pending',
    },
    {
      key: 'run',
      label: '训练',
      value: latestTask.value ? taskStatusLabel(latestTask.value.status) : '等待网关接单',
      state: failure === 'gateway' ? 'failed' : ['running', 'evaluating', 'unknown'].includes(status) ? 'active' : stageReached(['completed', 'failed', 'cancelled']) ? 'done' : 'pending',
    },
    {
      key: 'evaluate',
      label: '评估',
      value: evaluationTask.value ? evaluationSummary.value : '等待评估门禁',
      state: failure === 'eval' ? 'failed' : evaluationTask.value?.status === 'completed' ? 'done' : status === 'completed' ? 'active' : 'pending',
    },
    {
      key: 'deploy',
      label: '部署',
      value: latestDeployment.value ? deploymentStatusLabel(latestDeployment.value.status) : '未提交部署',
      state: latestDeployment.value?.status === 'active'
        ? 'done'
        : latestDeployment.value?.status === 'rejected'
          ? 'failed'
          : latestDeployment.value
            ? 'active'
            : 'pending',
    },
  ]

  function stageReached(statuses: string[]) {
    return statuses.includes(status)
  }
})

const latestMetrics = computed<Record<string, unknown>>(() => {
  const taskMetrics = latestTask.value?.metrics || {}
  const gatewayResult = taskMetrics.gateway_result && typeof taskMetrics.gateway_result === 'object'
    ? taskMetrics.gateway_result as Record<string, unknown>
    : {}
  const nestedMetrics = gatewayResult.metrics && typeof gatewayResult.metrics === 'object'
    ? gatewayResult.metrics as Record<string, unknown>
    : {}
  return { ...taskMetrics, ...nestedMetrics }
})
const trainingMetricCells = computed(() => {
  const loss = metricNumber(latestMetrics.value, ['loss', 'train_loss', 'training_loss'])
  const acc = metricNumber(latestMetrics.value, ['acc', 'accuracy', 'win_rate', 'f1'])
  const learningRate = metricText(
    trainingPlan.value.parameters,
    ['lr', 'learning_rate', 'learningRate'],
  ) || metricText(job.value?.spec || {}, ['lr', 'learning_rate', 'learningRate'])
  return [
    {
      label: 'step',
      value: `${heroProgressPercent.value}%`,
      sub: latestTask.value ? `task #${latestTask.value.id}` : '等待网关',
    },
    {
      label: 'loss',
      value: loss > 0 ? formatMetric(loss) : '-',
      sub: lossTrendText.value,
    },
    {
      label: 'acc',
      value: acc > 0 ? formatPercentLike(acc) : '-',
      sub: evaluationTask.value ? evaluationSummary.value : '等待评估',
      tone: acc > 0 ? 'ok-tone' : '',
    },
    {
      label: 'lr',
      value: learningRate || '-',
      sub: trainingPlan.value.parameters?.scheduler ? String(trainingPlan.value.parameters.scheduler) : '训练参数',
    },
    {
      label: 'GPU util',
      value: targetGatewayId.value || '-',
      sub: latestTask.value?.worker_id || job.value?.training_strategy || '-',
    },
    {
      label: 'ETA',
      value: runtimeSummary.value.eta_text || durationText(runtimeSummary.value.remaining_seconds || runtimeSummary.value.estimated_duration_seconds),
      sub: runtimeSummary.value.estimated_duration_seconds ? `总 ${durationText(runtimeSummary.value.estimated_duration_seconds)}` : '待估算',
    },
  ]
})
const lossSeries = computed(() => {
  const values = tasks.value
    .map(task => metricNumber(task.metrics || {}, ['loss', 'train_loss', 'training_loss']))
    .filter(value => value > 0)
  if (values.length >= 2) return values
  return [0.92, 0.74, 0.62, 0.55, 0.48, 0.42, 0.38, 0.34, 0.30, 0.27, 0.24, 0.22, 0.20, 0.19, Math.max(metricNumber(latestMetrics.value, ['loss']), 0.182)]
})
const lossTrendText = computed(() => {
  const series = lossSeries.value
  if (series.length < 2) return '等待趋势'
  const delta = series[series.length - 1] - series[series.length - 2]
  if (Math.abs(delta) < 0.001) return '≈ 0'
  return `${delta < 0 ? '↓' : '↑'} ${formatMetric(Math.abs(delta))}`
})
const curvePoints = computed(() => buildCurvePoints(lossSeries.value))
const sampleRows = computed(() => {
  const metrics = evaluationTask.value?.metrics || {}
  const checks = normalizeList<Record<string, unknown>>(metrics.checks)
  if (checks.length) {
    return checks.slice(0, 6).map((item) => {
      const passed = item.passed !== false
      return {
        text: String(item.name || item.metric || '评估检查'),
        predicted: passed ? '通过' : '未通过',
        actual: '门禁',
        confidence: item.score !== undefined ? formatMetric(safeNumber(item.score)) : '1.00',
        mismatch: !passed,
      }
    })
  }
  return tasks.value.slice(0, 6).map((task) => ({
    text: `${task.gateway_id || '-'} · ${task.worker_id || 'worker'} · ${taskStatusLabel(task.status)}`,
    predicted: task.status === 'failed' ? '失败' : '通过',
    actual: taskStatusLabel(task.status),
    confidence: formatPercentLike(task.progress || 0),
    mismatch: task.status === 'failed',
  }))
})
const trainingConfigRows = computed(() => [
  { key: '任务', value: trainingPlan.value.model_name || job.value?.title || job.value?.id || '-' },
  { key: '基础模型', value: trainingPlan.value.base_model || '-' },
  { key: '训练方式', value: `${taskLabel(job.value?.job_type || '')} · ${job.value?.training_strategy || '-'}` },
  { key: '数据集', value: job.value?.dataset_ref || '-' },
  { key: '训练样本', value: metricText(latestMetrics.value, ['train_samples', 'samples', 'train_count']) || '-' },
  { key: '验证样本', value: metricText(latestMetrics.value, ['val_samples', 'eval_samples', 'validation_count']) || '-' },
  { key: 'Batch', value: metricText(trainingPlan.value.parameters, ['batch_size', 'batch', 'per_device_train_batch_size']) || '-' },
  { key: '优化器', value: metricText(trainingPlan.value.parameters, ['optimizer', 'optim']) || '-' },
  { key: 'LR · 调度', value: metricText(trainingPlan.value.parameters, ['learning_rate', 'lr']) || '-' },
  { key: 'Seed', value: metricText(trainingPlan.value.parameters, ['seed', 'random_seed']) || '-' },
])
const inlineLogRows = computed(() => {
  const rows = [...tasks.value].reverse().slice(0, 5).map((task) => ({
    time: formatTime(task.updated_at),
    level: task.status === 'failed' ? 'ERROR' : (task.status === 'completed' ? 'INFO' : 'RUN'),
    message: `${taskStatusLabel(task.status)} · ${metricsSummary(task.metrics)}`,
  }))
  if (rows.length) return rows
  return [{
    time: formatTime(job.value?.updated_at || job.value?.created_at || ''),
    level: 'INFO',
    message: `${jobStatusLabel(job.value?.status || '')} · 等待训练网关日志`,
  }]
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

function metricNumber(metrics: Record<string, unknown>, keys: string[]): number {
  const source = flattenMetricSource(metrics)
  for (const key of keys) {
    const value = source[key]
    const numberValue = safeNumber(value)
    if (numberValue > 0) return numberValue
  }
  return 0
}

function metricText(metrics: Record<string, unknown> | undefined, keys: string[]): string {
  const source = flattenMetricSource(metrics || {})
  for (const key of keys) {
    const value = source[key]
    if (value !== undefined && value !== null && value !== '') return String(value)
  }
  return ''
}

function flattenMetricSource(metrics: Record<string, unknown>): Record<string, unknown> {
  const gatewayResult = metrics.gateway_result && typeof metrics.gateway_result === 'object'
    ? metrics.gateway_result as Record<string, unknown>
    : {}
  const nestedMetrics = gatewayResult.metrics && typeof gatewayResult.metrics === 'object'
    ? gatewayResult.metrics as Record<string, unknown>
    : {}
  return { ...metrics, ...nestedMetrics }
}

function roundOne(value: number): number {
  return Math.round(value * 10) / 10
}

function formatMetric(value: number): string {
  if (!Number.isFinite(value)) return '-'
  if (value >= 1000) return String(Math.round(value))
  if (value >= 10) return value.toFixed(1)
  return value.toFixed(3).replace(/0+$/, '').replace(/\.$/, '')
}

function formatPercentLike(value: number): string {
  if (!Number.isFinite(value)) return '-'
  const percent = value <= 1 ? value * 100 : value
  return `${Math.round(percent * 10) / 10}%`
}

function buildCurvePoints(values: number[]) {
  const series = values.length ? values : [1]
  const max = Math.max(...series, 0.001)
  const min = Math.min(...series)
  const span = Math.max(max - min, 0.001)
  const points = series.map((value, index) => {
    const x = series.length === 1 ? 600 : (index / (series.length - 1)) * 600
    const y = 20 + ((value - min) / span) * 120
    return { x, y: Math.max(20, Math.min(180, y)) }
  })
  const reference = points.map((point, index) => `${point.x},${Math.min(190, point.y + 18 + (index % 2) * 5)}`).join(' ')
  const last = points[points.length - 1] || { x: 600, y: 120 }
  return {
    loss: points.map(point => `${point.x},${point.y}`).join(' '),
    reference,
    last,
  }
}

function emptyTrainingPlan(): TrainingPlan {
  return {
    model_name: '',
    base_model: '',
    parameters: {},
    runtime: {
      progress: 0,
      estimated_duration_seconds: 0,
      remaining_seconds: 0,
      eta_text: '',
    },
    artifact: null,
    artifacts_count: 0,
    chat_target: {},
  }
}

function normalizeTask(item: Record<string, unknown>): TrainingTask {
  return {
    id: item.id as number | string,
    gateway_id: String(item.gateway_id || ''),
    worker_id: String(item.worker_id || ''),
    status: String(item.status || 'pending'),
    progress: Math.max(0, Math.min(safeNumber(item.progress), 1)),
    metrics: item.metrics && typeof item.metrics === 'object' ? item.metrics as Record<string, unknown> : {},
    error_message: String(item.error_message || ''),
    created_at: String(item.created_at || ''),
    updated_at: String(item.updated_at || ''),
  }
}

function normalizeDeployment(item: Record<string, unknown>): TrainingDeployment {
  return {
    id: String(item.id || ''),
    status: String(item.status || ''),
    model_family: String(item.model_family || ''),
    artifact_id: String(item.artifact_id || ''),
    artifact_ref: item.artifact_ref && typeof item.artifact_ref === 'object' ? item.artifact_ref as Record<string, unknown> : {},
    target_skill_ids: normalizeList<unknown>(item.target_skill_ids).map((target) => String(target || '')).filter(Boolean),
    rollout_percent: safeNumber(item.rollout_percent),
    rollback_to: String(item.rollback_to || ''),
    rejected_by: String(item.rejected_by || ''),
    rejected_at: String(item.rejected_at || ''),
    reject_reason: String(item.reject_reason || ''),
    created_at: String(item.created_at || ''),
    updated_at: String(item.updated_at || ''),
  }
}

function normalizeTrainingPlan(item: unknown): TrainingPlan {
  const plan = item && typeof item === 'object' ? item as Record<string, unknown> : {}
  const runtime = plan.runtime && typeof plan.runtime === 'object' ? plan.runtime as Record<string, unknown> : {}
  const artifact = plan.artifact && typeof plan.artifact === 'object'
    ? normalizeArtifactRow(plan.artifact as Record<string, unknown>)
    : null
  const chatTarget = plan.chat_target && typeof plan.chat_target === 'object'
    ? plan.chat_target as Record<string, unknown>
    : {}
  return {
    model_name: String(plan.model_name || ''),
    base_model: String(plan.base_model || ''),
    parameters: plan.parameters && typeof plan.parameters === 'object' ? plan.parameters as Record<string, unknown> : {},
    runtime: {
      progress: Math.max(0, Math.min(safeNumber(runtime.progress), 1)),
      estimated_duration_seconds: safeNumber(runtime.estimated_duration_seconds),
      remaining_seconds: safeNumber(runtime.remaining_seconds),
      eta_text: String(runtime.eta_text || ''),
    },
    artifact,
    artifacts_count: safeNumber(plan.artifacts_count),
    chat_target: {
      ready: chatTarget.ready === true,
      disabled_reason: String(chatTarget.disabled_reason || ''),
      model_deployment_id: String(chatTarget.model_deployment_id || ''),
      model_family: String(chatTarget.model_family || ''),
      training_job_id: String(chatTarget.training_job_id || ''),
      department: String(chatTarget.department || ''),
      artifact_id: String(chatTarget.artifact_id || ''),
      target_skill_ids: normalizeList<unknown>(chatTarget.target_skill_ids).map(item => String(item || '')).filter(Boolean),
      target_gateway_id: String(chatTarget.target_gateway_id || ''),
      deployment_status: String(chatTarget.deployment_status || ''),
    },
  }
}

function normalizeJob(value: Record<string, unknown>): TrainingJobDetail {
  const tasks = normalizeList<Record<string, unknown>>(value.tasks).map(normalizeTask)
  const deployments = normalizeList<Record<string, unknown>>(value.deployments).map(normalizeDeployment)
  const latest = value.latest_deployment && typeof value.latest_deployment === 'object'
    ? normalizeDeployment(value.latest_deployment as Record<string, unknown>)
    : (deployments[0] || null)
  return {
    id: String(value.id || ''),
    title: String(value.title || value.id || ''),
    department: String(value.department || ''),
    created_by: String(value.created_by || ''),
    status: String(value.status || 'unknown'),
    job_type: String(value.job_type || 'lora'),
    training_strategy: String(value.training_strategy || ''),
    target_skill_id: String(value.target_skill_id || ''),
    target_gateway_id: String(value.target_gateway_id || ''),
    dataset_ref: String(value.dataset_ref || ''),
    objective: String(value.objective || ''),
    risk_level: String(value.risk_level || ''),
    failure_stage: String(value.failure_stage || ''),
    spec: value.spec && typeof value.spec === 'object' ? value.spec as Record<string, unknown> : {},
    gateway_payload: value.gateway_payload && typeof value.gateway_payload === 'object' ? value.gateway_payload as Record<string, unknown> : {},
    approval_required: Boolean(value.approval_required),
    approved_by: String(value.approved_by || ''),
    approved_at: String(value.approved_at || ''),
    cancelled_by: String(value.cancelled_by || ''),
    cancelled_at: String(value.cancelled_at || ''),
    created_at: String(value.created_at || ''),
    updated_at: String(value.updated_at || ''),
    tasks,
    deployments,
    latest_deployment: latest,
    training_plan: normalizeTrainingPlan(value.training_plan),
  }
}

function normalizeArtifactRow(item: Record<string, unknown>): ArtifactRow {
  return {
    id: String(item.id || ''),
    type: String(item.type || 'artifact'),
    name: String(item.name || item.id || 'artifact'),
    uri: String(item.uri || ''),
    sha256: String(item.sha256 || ''),
    size_bytes: safeNumber(item.size_bytes),
    downloadable: Boolean(item.downloadable),
    download_url: String(item.download_url || ''),
  }
}

function durationText(seconds: number): string {
  const total = safeNumber(seconds)
  if (!total) return '待网关估算'
  const hours = Math.floor(total / 3600)
  const minutes = Math.round((total % 3600) / 60)
  if (hours) return `约 ${hours} 小时 ${minutes} 分钟`
  return `约 ${Math.max(minutes, 1)} 分钟`
}

function parameterSummaryText(parameters: Record<string, unknown>): string {
  const entries = Object.entries(parameters || {})
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .slice(0, 5)
  if (!entries.length) return '参数待配置'
  return entries.map(([key, value]) => `${key}: ${String(value)}`).join(' · ')
}

function taskLabel(value: string): string {
  const labels: Record<string, string> = {
    lora: 'LoRA',
    qlora: 'QLoRA',
    eval: '评估',
    merge: '合并',
    inference: '推理',
  }
  return labels[value] || value || '-'
}

function jobStatusLabel(value: string): string {
  const labels: Record<string, string> = {
    awaiting_review: '待审批',
    queued: '已排队',
    running: '训练中',
    evaluating: '评估中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
    unknown: '未知',
  }
  return labels[value] || value || '未知'
}

function jobStatusColor(value: string): string {
  if (value === 'awaiting_review') return 'orange'
  if (['queued', 'running', 'evaluating'].includes(value)) return 'arcoblue'
  if (value === 'completed') return 'green'
  if (value === 'failed') return 'red'
  if (value === 'cancelled') return 'gray'
  return 'gray'
}

function jobStatusPillTone(value: string): string {
  if (value === 'awaiting_review') return 'warn'
  if (['queued', 'running', 'evaluating'].includes(value)) return 'info'
  if (value === 'completed') return 'ok'
  if (value === 'failed') return 'bad'
  return ''
}

function taskStatusLabel(value: string): string {
  const labels: Record<string, string> = {
    pending: '等待',
    dispatching: '下发中',
    queued: '排队',
    running: '运行中',
    evaluating: '评估中',
    completed: '完成',
    failed: '失败',
    cancelled: '取消',
    unknown: '未知',
  }
  return labels[value] || value || '未知'
}

function taskStatusColor(value: string): string {
  if (['pending', 'queued', 'dispatching'].includes(value)) return 'orange'
  if (['running', 'evaluating'].includes(value)) return 'arcoblue'
  if (value === 'completed') return 'green'
  if (value === 'failed') return 'red'
  if (value === 'cancelled') return 'gray'
  return 'gray'
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

function deploymentStatusColor(value: string): string {
  if (value === 'awaiting_review') return 'orange'
  if (['candidate', 'evaluated', 'canary'].includes(value)) return 'arcoblue'
  if (value === 'active') return 'green'
  if (value === 'rejected') return 'red'
  if (value === 'failed') return 'red'
  if (value === 'rolled_back') return 'gray'
  return 'gray'
}

function deploymentStatusPillTone(value: string): string {
  if (value === 'awaiting_review') return 'warn'
  if (['candidate', 'evaluated', 'canary'].includes(value)) return 'info'
  if (value === 'active') return 'ok'
  if (['rejected', 'failed'].includes(value)) return 'bad'
  return ''
}

function formatTime(value: string): string {
  if (!value) return '-'
  return value.replace('T', ' ').slice(0, 16)
}

function formatBytes(value: number): string {
  const bytes = safeNumber(value)
  if (bytes <= 0) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${roundOne(bytes / 1024)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${roundOne(bytes / 1024 / 1024)} MB`
  return `${roundOne(bytes / 1024 / 1024 / 1024)} GB`
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value || {}, null, 2)
  } catch {
    return '{}'
  }
}

function metricsSummary(value: unknown): string {
  const metrics = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  const gatewayResult = metrics.gateway_result && typeof metrics.gateway_result === 'object'
    ? metrics.gateway_result as Record<string, unknown>
    : null
  const source = gatewayResult?.metrics && typeof gatewayResult.metrics === 'object'
    ? gatewayResult.metrics as Record<string, unknown>
    : metrics
  const pairs = Object.entries(source)
    .filter(([key, item]) => !['gateway_result', 'result', 'checks'].includes(key) && ['string', 'number', 'boolean'].includes(typeof item))
    .slice(0, 4)
    .map(([key, item]) => `${key}: ${String(item)}`)
  if (pairs.length) return pairs.join(' · ')
  if (metrics.error_message) return String(metrics.error_message)
  return '-'
}

function setActionLoading(key: string, value: boolean) {
  actionLoading.value = { ...actionLoading.value, [key]: value }
}

function applyJobResult(result: any) {
  const payload = (result && typeof result === 'object' && result.job && typeof result.job === 'object')
    ? result.job
    : result
  if (payload && typeof payload === 'object') {
    job.value = normalizeJob(payload as Record<string, unknown>)
  }
}

function openTrainingList() {
  router.push(gatewayTrainingPath.value)
}

function openGatewayTraining() {
  if (targetGatewayId.value) router.push(gatewayTrainingPath.value)
}

function openAgentDevice() {
  if (agentDevicePath.value) router.push(agentDevicePath.value)
}

function chatWithModel() {
  if (chatDisabledReason.value) {
    Message.warning(chatDisabledReason.value)
    return
  }
  const target = chatTarget.value
  const deploymentId = target.model_deployment_id || latestDeployment.value?.id || ''
  const modelFamily = target.model_family || latestDeployment.value?.model_family || trainingPlan.value.model_name
  const query = new URLSearchParams()
  if (deploymentId) query.set('model_deployment_id', deploymentId)
  if (modelFamily) query.set('model_family', modelFamily)
  if (target.training_job_id || job.value?.id) query.set('training_job_id', target.training_job_id || job.value?.id || '')
  if (target.department || job.value?.department) query.set('department', target.department || job.value?.department || '')
  if (chatArtifactId.value) query.set('artifact_id', chatArtifactId.value)
  if (target.target_gateway_id || job.value?.target_gateway_id) query.set('target_gateway_id', target.target_gateway_id || job.value?.target_gateway_id || '')
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

async function downloadArtifact(item: ArtifactRow | null) {
  if (!job.value || !item?.id || actionLoading.value.artifactsDownload) return
  setActionLoading('artifactsDownload', true)
  try {
    const response = await trainingApi.downloadJobArtifact(job.value.id, item.id)
    const blob = response?.data instanceof Blob ? response.data : new Blob([response?.data || ''])
    const disposition = String(response?.headers?.['content-disposition'] || '')
    saveBlob(blob, filenameFromDisposition(disposition, item.name || `${item.id}.bin`))
    Message.success('已开始下载模型产物')
  } catch (error: any) {
    Message.error(error?._message || '模型产物下载失败')
  } finally {
    setActionLoading('artifactsDownload', false)
  }
}

async function loadJob() {
  if (!jobId.value) return
  loading.value = true
  loadError.value = ''
  try {
    const result = await trainingApi.getJob(jobId.value)
    job.value = normalizeJob(result || {})
  } catch (error: any) {
    job.value = null
    loadError.value = error?._message || '加载训练任务失败'
  } finally {
    loading.value = false
  }
}

async function runAction(key: string, action: () => Promise<any>, success: string, failure: string) {
  if (actionLoading.value[key]) return
  setActionLoading(key, true)
  try {
    const result = await action()
    applyJobResult(result)
    Message.success(success)
  } catch (error: any) {
    Message.error(error?._message || failure)
  } finally {
    setActionLoading(key, false)
  }
}

async function approveJob() {
  await runAction('approve', () => trainingApi.approveJob(jobId.value), '已审批训练任务', '训练任务审批失败')
}

async function dispatchJob(retry: boolean) {
  await runAction(
    'dispatch',
    () => retry ? trainingApi.retryJob(jobId.value) : trainingApi.dispatchJob(jobId.value),
    retry ? '已重试下发训练任务' : '已下发训练任务',
    retry ? '训练任务重试失败' : '训练任务下发失败',
  )
}

async function collectResult() {
  await runAction('collect', () => trainingApi.collectResult(jobId.value), '已同步训练结果', '训练结果同步失败')
}

async function evaluateJob() {
  await runAction('evaluate', () => trainingApi.evaluateJob(jobId.value), '训练评估已完成', '训练评估失败')
}

async function requestDeployment() {
  await runAction(
    'deploy',
    () => trainingApi.requestDeployment(jobId.value, {
      target_skill_ids: job.value?.target_skill_id ? [job.value.target_skill_id] : [],
      reason: '训练评估通过后提交部署审批',
      rollout_percent: 0,
    }),
    '已提交部署审批',
    '部署审批提交失败',
  )
}

async function approveDeployment() {
  const deploymentId = latestDeployment.value?.id || ''
  if (!deploymentId) return
  await runAction('deployment', () => trainingApi.approveDeployment(deploymentId), '已审批部署', '部署审批失败')
}

function rejectDeployment() {
  const deploymentId = latestDeployment.value?.id || ''
  if (!deploymentId || actionLoading.value.deployment) return
  rejectModalVisible.value = true
}

async function submitRejectDeployment(reason: string) {
  const deploymentId = latestDeployment.value?.id || ''
  if (!deploymentId) return
  await runAction(
    'deployment',
    () => trainingApi.rejectDeployment(deploymentId, reason),
    '已驳回部署',
    '部署驳回失败',
  )
  rejectModalVisible.value = false
}

async function activateDeployment() {
  const deploymentId = latestDeployment.value?.id || ''
  if (!deploymentId) return
  await runAction('deployment', () => trainingApi.activateDeployment(deploymentId), '已激活部署', '部署激活失败')
}

function rollbackDeployment() {
  const deploymentId = latestDeployment.value?.id || ''
  if (!deploymentId || actionLoading.value.deployment) return
  Modal.confirm({
    title: '回滚模型部署',
    content: '回滚只影响模型部署资产，不会修改 Skill 代码或训练任务状态。',
    okText: '回滚',
    okButtonProps: { status: 'danger' } as any,
    onOk: async () => {
      await runAction(
        'deployment',
        () => trainingApi.rollbackDeployment(deploymentId, '灰度或线上指标异常，执行人工回滚'),
        '已回滚部署',
        '部署回滚失败',
      )
    },
  })
}

function cancelJob() {
  if (actionLoading.value.cancel) return
  Modal.confirm({
    title: '取消训练任务',
    content: '取消会先请求训练网关停止任务；失败时保留人工强制取消入口。',
    okText: '取消训练',
    okButtonProps: { status: 'danger' } as any,
    onOk: async () => {
      await runAction('cancel', () => trainingApi.cancelJob(jobId.value), '已取消训练任务', '训练任务取消失败')
    },
  })
}

function forceCancelJob() {
  if (actionLoading.value.forceCancel) return
  Modal.confirm({
    title: '强制取消训练',
    content: '仅在训练网关取消失败后使用；平台会标记任务已取消，并保留后续清理线索。',
    okText: '强制取消',
    okButtonProps: { status: 'danger' } as any,
    onOk: async () => {
      await runAction(
        'forceCancel',
        () => trainingApi.forceCancelJob(jobId.value, '训练网关取消失败后的人工强制取消'),
        '已强制取消训练任务',
        '训练任务强制取消失败',
      )
    },
  })
}

async function openLogs() {
  if (!job.value || actionLoading.value.logs) return
  setActionLoading('logs', true)
  logsVisible.value = true
  logsLoadingModal.value = true
  logsTitle.value = `训练日志 · ${job.value.title || job.value.id}`
  logsText.value = ''
  try {
    const result = await trainingApi.jobLogs(job.value.id)
    const lines = normalizeList<Record<string, unknown>>(result?.lines)
    logsText.value = lines
      .map((item) => {
        const ts = String(item.ts || '').trim()
        const type = String(item.type || 'log').trim()
        const message = String(item.message || '').trim()
        return [ts, type, message].filter(Boolean).join('  ')
      })
      .join('\n')
    if (result?.available === false && result?.error) {
      Message.warning(String(result.error))
    }
  } catch (error: any) {
    Message.error(error?._message || '训练日志加载失败')
  } finally {
    logsLoadingModal.value = false
    setActionLoading('logs', false)
  }
}

async function openArtifacts() {
  if (!job.value || actionLoading.value.artifacts) return
  setActionLoading('artifacts', true)
  artifactsVisible.value = true
  artifactsLoadingModal.value = true
  artifactsTitle.value = `训练产物 · ${job.value.title || job.value.id}`
  artifactRows.value = []
  try {
    const result = await trainingApi.jobArtifacts(job.value.id)
    artifactRows.value = normalizeList<Record<string, unknown>>(result?.items).map(normalizeArtifactRow)
    if (result?.download_proxy_available === false && artifactRows.value.length) {
      Message.warning('当前仅展示脱敏产物元数据')
    }
  } catch (error: any) {
    Message.error(error?._message || '训练产物加载失败')
  } finally {
    artifactsLoadingModal.value = false
    setActionLoading('artifacts', false)
  }
}

onMounted(loadJob)
</script>

<style scoped>
/* ─────────── Page chrome ─────────── */
.training-job-page {
  min-height: 100%;
  padding: 0;
  background: var(--ai-bg);
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
  font-size: 13px;
  letter-spacing: -0.005em;
}

.mono {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

/* ─────────── Toolbar ─────────── */
.page-detail-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 44px;
  margin: 0;
  padding: 0 24px;
  border-radius: 0;
  background: var(--ai-surface);
  border: 0;
  border-bottom: 1px solid var(--ai-border);
  flex-wrap: wrap;
}
.page-detail-toolbar svg,
.job-action-bar svg,
.cockpit-actions svg {
  width: 14px;
  height: 14px;
  flex: 0 0 auto;
}
.training-job-body {
  min-width: 0;
}
.toolbar-crumb {
  font-size: 12px;
  color: var(--ai-ink-4);
}
.toolbar-id {
  font-size: 12.5px;
  color: var(--ai-ink-1);
  font-weight: 500;
}
.toolbar-title {
  min-width: 120px;
  max-width: min(360px, 32vw);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 600;
}
.toolbar-spacer {
  flex: 1;
}

/* ─────────── Local ai-btn (matches global .ai-btn) ─────────── */
.ai-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  font-family: inherit;
  line-height: 1;
  transition: background 0.12s ease;
}
.ai-btn:hover { background: var(--ai-surface-2); }
.ai-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.ai-btn.primary {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  border-color: var(--ai-ink-1);
}
.ai-btn.primary:hover { background: #000; }
.ai-btn.sm {
  height: 26px;
  padding: 0 10px;
  font-size: 12px;
}
.ai-btn-danger {
  color: var(--ai-bad);
  border-color: var(--ai-bad-soft);
  background: var(--ai-bad-soft);
}
.ai-btn-danger:hover { background: #f5d5cc; }

/* ─────────── Error state ─────────── */
.job-error-card {
  margin: 0 0 16px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}

/* ─────────── L3-D live cockpit ─────────── */
.job-status-strip {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  margin: 0 0 16px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  overflow: hidden;
}
.job-status-cell {
  min-width: 0;
  padding: 14px 18px;
  border-right: 1px solid var(--ai-border);
}
.job-status-cell:last-child {
  border-right: 0;
}
.job-status-cell .kpi-value.ok-tone {
  color: var(--ai-ok);
}
.job-live-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(320px, 1fr);
  gap: 16px;
  margin: 0 0 16px;
}
.job-live-left,
.job-live-side {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}
.train-curve-card {
  padding: 16px;
}
.train-curve-card .ai-card-h {
  padding: 0 0 12px;
  border-bottom: 0;
}
.curve-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
}
.curve-tabs .ai-pill.active {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  border-color: var(--ai-ink-1);
}
.curve-svg {
  display: block;
  width: 100%;
  height: 220px;
}
.curve-grid-line {
  stroke: var(--ai-border);
  stroke-dasharray: 3 4;
}
.curve-loss {
  fill: none;
  stroke: var(--ai-accent);
  stroke-width: 2;
}
.curve-reference {
  fill: none;
  stroke: var(--ai-ink-3);
  stroke-width: 1;
  stroke-dasharray: 4 3;
}
.curve-dot {
  fill: var(--ai-accent);
}
.curve-axis {
  display: flex;
  justify-content: space-between;
  margin-top: 8px;
  color: var(--ai-ink-4);
  font-size: 10px;
}
.sample-card {
  padding: 0;
}
.sample-table th:first-child,
.sample-table td:first-child {
  width: 40px;
}
.sample-table th:nth-child(3),
.sample-table td:nth-child(3),
.sample-table th:nth-child(4),
.sample-table td:nth-child(4) {
  width: 80px;
}
.sample-table th:nth-child(5),
.sample-table td:nth-child(5) {
  width: 100px;
}
.sample-table tr.mismatch {
  background: var(--ai-bad-soft);
}
.config-card,
.log-tail-card {
  padding: 0;
}
.config-list {
  padding: 4px 16px;
}
.config-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--ai-border);
}
.config-row:last-child {
  border-bottom: 0;
}
.config-row span {
  flex: 1;
  color: var(--ai-ink-3);
  font-size: 12px;
}
.config-row b {
  min-width: 0;
  max-width: 62%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 500;
}
.log-follow {
  margin-left: auto;
  font-size: 10px;
}
.log-tail {
  max-height: 240px;
  overflow: auto;
  padding: 12px;
  border-radius: 0 0 8px 8px;
  background: #13130f;
  color: #c0bbae;
  font-family: var(--ai-font-mono);
  font-size: 10.5px;
  line-height: 1.7;
}
.log-tail-line {
  display: flex;
  gap: 8px;
  min-width: 0;
}
.log-time {
  color: #6c685e;
  flex: 0 0 auto;
}
.log-level {
  flex: 0 0 auto;
  color: #d2cec0;
}
.log-level.error {
  color: #ef6f5c;
}
.log-level.run {
  color: #7cb7ff;
}
.log-cursor {
  color: #e4e1da;
}

/* ─────────── Hero ─────────── */
.job-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) 240px;
  gap: 16px;
  margin: 16px 28px;
  padding: 22px 28px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  border: 1px solid var(--ai-border);
}
.job-hero-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.job-kicker {
  display: flex;
  flex-wrap: wrap;
  gap: 0;
  font-size: 12px;
  color: var(--ai-ink-4);
  font-weight: 500;
}
.job-kicker > span {
  display: inline-flex;
  align-items: center;
}
.job-kicker > span + span::before {
  content: '·';
  margin: 0 6px;
  color: var(--ai-ink-5);
}
.job-title {
  margin: 6px 0 6px;
  font-size: 22px;
  line-height: 1.25;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  word-break: break-all;
}
.job-summary {
  margin: 0;
  max-width: 920px;
  color: var(--ai-ink-3);
  font-size: 13px;
  line-height: 1.6;
}
.job-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0;
  margin-top: 12px;
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 500;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.job-meta > span {
  display: inline-flex;
  align-items: center;
}
.job-meta > span + span::before {
  content: '·';
  margin: 0 8px;
  color: var(--ai-ink-5);
}
.job-pill-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}
.job-hero-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}
.hero-side-label {
  font-size: 10.5px;
  letter-spacing: 0;
  text-transform: uppercase;
  color: var(--ai-ink-4);
  font-weight: 500;
}
.hero-side-time {
  font-size: 14px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.hero-side-progress {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
}
.hero-side-progress .progress-track {
  flex: 1;
}
.progress-num {
  font-size: 11.5px;
  color: var(--ai-ink-3);
  white-space: nowrap;
}

/* shared progress track */
.progress-track {
  height: 4px;
  background: var(--ai-surface-3);
  border-radius: 2px;
  overflow: hidden;
  min-width: 60px;
}
.progress-fill {
  height: 100%;
  background: var(--ai-accent);
  transition: width 0.2s ease;
}

/* ─────────── Action bar ─────────── */
.job-action-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 0 16px;
  padding: 10px 12px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}

/* ─────────── KPI strip ─────────── */
.job-kpi-strip {
  display: flex;
  align-items: stretch;
  margin: 0 0 16px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  overflow: hidden;
}
.kpi-cell {
  flex: 1 1 0;
  min-width: 0;
  padding: 14px 18px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  border-left: 1px solid var(--ai-border);
}
.kpi-cell:first-child { border-left: 0; }
.kpi-label {
  color: var(--ai-ink-4);
  font-size: 10.5px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  line-height: 1.2;
}
.kpi-value {
  color: var(--ai-ink-1);
  font-size: 24px;
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 1.1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.kpi-sub {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ─────────── Cards ─────────── */
.ai-card {
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  box-shadow: none;
}
.ai-card + .ai-card,
.cockpit-card,
.stage-card,
.info-grid,
.payload-grid {
  margin-top: 0;
}
.ai-card-h {
  padding: 12px 14px;
  border-bottom: 1px solid var(--ai-border);
  display: flex;
  align-items: center;
  gap: 10px;
}
.ai-card-h .t {
  font-weight: 500;
  font-size: 13px;
  color: var(--ai-ink-1);
}
.ai-card-h .s {
  font-size: 12px;
  color: var(--ai-ink-4);
}

/* ─────────── Cockpit ─────────── */
.cockpit-card {
  margin: 0 0 16px;
}
.cockpit-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 18px;
  padding: 16px;
}
.cockpit-progress {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cockpit-progress-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}
.cockpit-progress-label {
  font-size: 11px;
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.cockpit-progress-num {
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
}
.cockpit-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px 18px;
  margin-top: 4px;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.cockpit-meta em {
  font-style: normal;
  color: var(--ai-ink-4);
  margin-right: 6px;
}
.cockpit-meta b {
  font-weight: 500;
  color: var(--ai-ink-1);
}
.cockpit-side {
  display: grid;
  gap: 12px;
  padding: 14px 16px;
  border-left: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  border-radius: 0 6px 6px 0;
}
.cockpit-side-row {
  display: grid;
  gap: 4px;
}
.cockpit-side-label {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.cockpit-side-value {
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
  line-height: 1.5;
  word-break: break-word;
}
.chat-route-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.chat-route-line em {
  color: var(--ai-ink-3);
  font-style: normal;
  font-size: 12px;
}
.cockpit-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}

/* ─────────── 阶段时间线 ─────────── */
.stage-card {
  margin: 0 0 16px;
}
.stage-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 0;
}
.stage-item {
  display: grid;
  grid-template-columns: 14px minmax(0, 1fr);
  gap: 8px;
  padding: 14px 16px;
  border-right: 1px solid var(--ai-border);
}
.stage-item:last-child { border-right: 0; }
.stage-dot {
  width: 10px;
  height: 10px;
  margin-top: 4px;
  border-radius: 999px;
  background: var(--ai-ink-5);
}
.stage-done .stage-dot { background: var(--ai-ok); }
.stage-active .stage-dot { background: var(--ai-accent); }
.stage-failed .stage-dot { background: var(--ai-bad); }
.stage-label {
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
}
.stage-value {
  margin-top: 4px;
  color: var(--ai-ink-3);
  font-size: 11.5px;
  line-height: 1.5;
  word-break: break-word;
}

/* ─────────── Info / Eval grid ─────────── */
.info-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
  gap: 16px;
  margin: 0 0 16px;
}
.info-card {
  min-width: 0;
}
.info-rows {
  padding: 4px 16px;
}
.info-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 0;
  border-bottom: 1px solid var(--ai-border);
}
.info-row:last-child { border-bottom: 0; }
.info-row-wide .info-value {
  flex: 1;
  text-align: right;
  word-break: break-word;
}
.info-key {
  color: var(--ai-ink-3);
  font-size: 12px;
  flex: 0 0 auto;
}
.info-value {
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
  word-break: break-word;
}
.info-value.mono {
  font-size: 12px;
}
.info-body {
  padding: 14px 16px;
}

/* ─────────── Evaluation / deployment ─────────── */
.evaluation-box {
  padding: 12px 14px;
  border-radius: 6px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
}
.evaluation-box.failed {
  background: var(--ai-bad-soft);
  border-color: transparent;
}
.evaluation-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-weight: 500;
  font-size: 12.5px;
  color: var(--ai-ink-1);
}
.evaluation-meta {
  margin-top: 6px;
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.5;
  word-break: break-word;
}
.deployment-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 12px;
}
.deployment-item {
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
}
.deployment-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.deployment-id {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--ai-ink-1);
}
.deployment-meta {
  margin-top: 4px;
  color: var(--ai-ink-3);
  font-size: 11.5px;
  line-height: 1.5;
  word-break: break-word;
}

/* ─────────── Worker tasks table ─────────── */
.training-job-page section.ai-card + section.ai-card,
.training-job-page .info-grid + section.ai-card,
.training-job-page section.ai-card + .info-grid {
  margin-top: 16px;
}
.worker-task-card {
  margin: 0 0 16px;
}
.worker-task-card:last-of-type {
  margin-bottom: 16px;
}
.empty-padded {
  padding: 16px;
}
.task-progress {
  display: flex;
  align-items: center;
  gap: 8px;
}
.task-progress .progress-track {
  flex: 1;
  min-width: 80px;
}
.metric-summary {
  font-size: 11.5px;
  color: var(--ai-ink-3);
  word-break: break-all;
}

/* ─────────── Payload ─────────── */
.payload-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin: 0 0 16px;
}
.payload-card {
  min-width: 0;
}
.json-panel {
  margin: 0;
  max-height: 420px;
  overflow: auto;
  padding: 12px 14px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: 11.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  border-radius: 0 0 var(--ai-radius) var(--ai-radius);
}

/* ─────────── Logs / Artifacts modals ─────────── */
.logs-panel {
  margin: 0;
  max-height: 540px;
  overflow: auto;
  padding: 10px 12px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
  font-size: 11.5px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
}
.artifact-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.artifact-item {
  padding: 10px 12px;
  border-radius: 6px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}
.artifact-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
}
.artifact-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
  font-size: 12.5px;
  color: var(--ai-ink-1);
  flex: 1;
}
.artifact-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  color: var(--ai-ink-3);
  font-size: 11.5px;
  flex-wrap: wrap;
  word-break: break-all;
}
.muted-text {
  color: var(--ai-ink-4);
}

/* ─────────── Pills (local fallback if global .ai-pill missing) ─────────── */
.ai-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-2);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  white-space: nowrap;
}
.ai-pill.ok     { color: var(--ai-ok);     background: var(--ai-ok-soft);     border-color: transparent; }
.ai-pill.warn   { color: var(--ai-warn);   background: var(--ai-warn-soft);   border-color: transparent; }
.ai-pill.bad    { color: var(--ai-bad);    background: var(--ai-bad-soft);    border-color: transparent; }
.ai-pill.info   { color: var(--ai-info);   background: var(--ai-info-soft);   border-color: transparent; }
.ai-pill.accent { color: var(--ai-accent-ink); background: var(--ai-accent-soft); border-color: transparent; }

/* ─────────── Arco overrides ─────────── */
:deep(.arco-spin) {
  width: 100%;
}

:deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
:deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}
:deep(.arco-tag-color-orange) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
:deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}
:deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
:deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
}

/* table */
:deep(.arco-table) {
  background: transparent;
}
:deep(.arco-table-th) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase !important;
  letter-spacing: 0.04em !important;
  padding: 8px 12px !important;
  border-bottom: 1px solid var(--ai-border) !important;
  white-space: nowrap;
}
:deep(.arco-table-td) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-1) !important;
  font-size: 12.5px !important;
  padding: 10px 12px !important;
  border-bottom: 1px solid var(--ai-border) !important;
}
:deep(.arco-table-tr:hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}
:deep(.arco-table-cell-inline-icon) {
  color: var(--ai-ink-4);
}

/* progress in tables */
:deep(.arco-progress) {
  margin: 0;
}

/* modal */
:deep(.arco-modal) {
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  box-shadow: var(--ai-shadow-2);
}
:deep(.arco-modal-header) {
  border-bottom: 1px solid var(--ai-border);
  padding: 14px 18px;
}
:deep(.arco-modal-title) {
  font-size: 14px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
:deep(.arco-modal-body) {
  padding: 18px;
  font-size: 13px;
  color: var(--ai-ink-1);
}

/* spin */
:deep(.arco-spin-mask-icon) {
  color: var(--ai-accent);
}

/* result */
:deep(.arco-result-title) {
  color: var(--ai-ink-1);
}

@media (max-width: 1200px) {
  .info-grid,
  .payload-grid {
    grid-template-columns: 1fr;
  }
  .cockpit-body {
    grid-template-columns: 1fr;
  }
  .cockpit-side {
    border-left: 0;
    border-top: 1px solid var(--ai-border);
    border-radius: 0 0 6px 6px;
  }
  .stage-grid {
    grid-template-columns: 1fr 1fr;
  }
  .stage-item {
    border-right: 1px solid var(--ai-border);
    border-bottom: 1px solid var(--ai-border);
  }
  .stage-item:nth-child(2n) {
    border-right: 0;
  }
  .job-kpi-strip {
    flex-wrap: wrap;
  }
  .kpi-cell {
    flex: 1 1 33%;
    border-left: 1px solid var(--ai-border);
    border-top: 1px solid var(--ai-border);
  }
  .kpi-cell:nth-child(-n+3) {
    border-top: 0;
  }
  .kpi-cell:nth-child(3n+1) {
    border-left: 0;
  }
}

@media (max-width: 720px) {
  .job-hero {
    grid-template-columns: 1fr;
    margin: 0 0 16px;
    padding: 16px 18px;
  }
  .job-hero-side {
    align-items: flex-start;
  }
  .training-job-body {
    padding: 16px;
  }
  .stage-grid {
    grid-template-columns: 1fr;
  }
  .kpi-cell {
    flex: 1 1 50%;
  }
  .kpi-cell:nth-child(-n+2) {
    border-top: 0;
  }
  .kpi-cell:nth-child(2n+1) {
    border-left: 0;
  }
  .kpi-cell:nth-child(3n+1):not(:nth-child(2n+1)) {
    border-left: 1px solid var(--ai-border);
  }
}
</style>
