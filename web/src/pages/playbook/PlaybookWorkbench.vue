<template>
  <div class="page-container playbook-workbench-page">
    <a-spin :loading="loading" style="width: 100%">
      <div class="page-header workbench-header">
        <div class="workbench-title-wrap">
          <a-button class="workbench-back-btn" @click="$router.push('/playbooks')"><icon-left /> 返回</a-button>
          <div class="workbench-title-block">
            <div class="page-kicker">Skills · Playbook</div>
            <div class="workbench-title-row">
              <h2 class="page-title">{{ playbook.name || pbName }}</h2>
              <a-space size="small" wrap class="workbench-tag-row">
                <a-tag :color="hasUnsavedChanges ? 'orange' : 'green'">
                  {{ hasUnsavedChanges ? '未保存' : '已保存' }}
                </a-tag>
                <a-tag>{{ scheduleLabel }}</a-tag>
                <a-tag>{{ currentSteps.length }} 步骤</a-tag>
                <a-tag v-if="reviewState" :color="reviewStatusColor(reviewState.status)">
                  审核 {{ reviewStatusLabel(reviewState.status) }}
                </a-tag>
                <a-tag v-if="runId" :color="statusColor(runStatus)">
                  {{ statusLabel(runStatus) }}
                </a-tag>
              </a-space>
              <a-popover
                position="bl"
                trigger="click"
                :popup-visible="guidePopoverVisible"
                @popup-visible-change="onGuidePopoverChange"
              >
                <button
                  type="button"
                  class="guide-trigger"
                  :class="{ 'is-pulse': guideHintActive }"
                  aria-label="工作台使用说明"
                  @click.stop
                >
                  <icon-question-circle />
                </button>
                <template #content>
                  <div class="guide-popover">
                    <div class="guide-popover-title">怎么用这个工作台</div>
                    <ol class="guide-popover-list">
                      <li><strong>看流程</strong>：先确认流程在干什么、步骤顺序、是否可执行。</li>
                      <li><strong>改流程</strong>：左侧拖 Skill、连线决定顺序、点节点改参数，检查后保存。</li>
                      <li><strong>跑一次</strong>：节点颜色和时间线实时显示，失败直接回画布修。</li>
                      <li><strong>高级</strong>：查看文本流程图、原始 YAML 和调试上下文。</li>
                    </ol>
                    <div class="guide-popover-tip">提交审核前务必先保存。</div>
                  </div>
                </template>
              </a-popover>
            </div>
            <p v-if="playbook.description" class="page-subtitle workbench-subtitle">
              {{ playbook.description }}
            </p>
          </div>
        </div>
        <a-space wrap class="workbench-actions">
          <template v-if="activeMode === 'overview'">
            <a-button v-if="canEdit && hasUnsavedChanges" @click="activeMode = 'edit'">去保存</a-button>
            <a-button
              v-if="canEdit"
              :disabled="hasUnsavedChanges"
              :title="hasUnsavedChanges ? '有未保存修改，请先切到「改流程」保存' : ''"
              @click="handlePublish"
              :loading="publishing"
            >
              提交审核
            </a-button>
            <a-button v-if="canRun" type="primary" status="success" @click="handleRunClick" :loading="running">
              <icon-play-arrow /> 开始执行
            </a-button>
          </template>
          <template v-else-if="activeMode === 'edit'">
            <a-button v-if="canValidate" @click="handleValidateDraft" :loading="validating">
              <icon-check-circle /> 检查问题
            </a-button>
            <a-button v-if="canEdit" type="primary" @click="handleSaveClick" :loading="saving">
              <icon-save /> 保存
            </a-button>
            <a-button
              v-if="canEdit"
              :disabled="hasUnsavedChanges"
              :title="hasUnsavedChanges ? '有未保存修改，请先保存' : ''"
              @click="handlePublish"
              :loading="publishing"
            >
              提交审核
            </a-button>
          </template>
          <template v-else-if="activeMode === 'run'">
            <a-button v-if="canRun" type="primary" status="success" @click="handleRunClick" :loading="running">
              <icon-play-arrow /> {{ runId ? '再跑一次' : '开始执行' }}
            </a-button>
          </template>
          <template v-else-if="activeMode === 'advanced'">
            <a-button @click="handleCopyRawConfig"><icon-copy /> 复制 JSON</a-button>
          </template>
        </a-space>
      </div>

      <a-card v-if="reviewState" class="page-section-card review-card">
        <template #title>关联审核单</template>
        <div class="review-banner">
          <div class="review-meta">
            <div class="review-title">#{{ reviewState.id }} · {{ reviewState.skill_id }}</div>
            <div class="review-subtitle">
              提交人 {{ reviewState.submitter || '-' }} · 审核人 {{ reviewState.reviewer || '待分配' }} · 状态 {{ reviewStatusLabel(reviewState.status) }}
            </div>
          </div>
          <a-space wrap>
            <a-tag :color="reviewStatusColor(reviewState.status)">
              {{ reviewStatusLabel(reviewState.status) }}
            </a-tag>
            <a-button
              v-if="canActReview"
              type="primary"
              size="small"
              :loading="reviewActionLoading"
              @click="handleApproveReview"
            >
              通过
            </a-button>
            <a-button
              v-if="canActReview"
              status="danger"
              size="small"
              :loading="reviewActionLoading"
              @click="handleRejectReview"
            >
              驳回
            </a-button>
          </a-space>
        </div>
        <div v-if="reviewState.diff_summary" class="review-copy">{{ reviewState.diff_summary }}</div>
        <div v-if="reviewState.reason" class="review-copy review-reason">说明：{{ reviewState.reason }}</div>
        <pre v-if="reviewState.diff_text" class="review-diff">{{ reviewState.diff_text }}</pre>
      </a-card>

      <a-card class="page-section-card workbench-shell">
        <a-tabs v-model:active-key="activeMode" type="rounded" size="large" class="workbench-tabs">
          <a-tab-pane key="overview" title="看流程" />
          <a-tab-pane key="edit" title="改流程" />
          <a-tab-pane key="run" title="跑一次" />
          <a-tab-pane key="advanced" title="高级" />
        </a-tabs>

        <a-row
          v-if="activeMode !== 'advanced'"
          :gutter="[16, 16]"
          class="workbench-main-grid"
          :class="{ 'is-edit': activeMode === 'edit' }"
        >
          <a-col :xs="24" :xl="activeMode === 'edit' ? 24 : 17">
            <a-card class="canvas-card" :bordered="false">
              <div class="flow-frame-shell" :class="{ 'is-tall': activeMode === 'edit' }">
                <div v-if="showEmptyCanvasCTA" class="canvas-cta-overlay">
                  <div class="canvas-cta-eyebrow">空流程</div>
                  <div class="canvas-cta-title">这条流程还没有步骤</div>
                  <div class="canvas-cta-copy">从左侧拖一个 Skill 到画布，或先插入一个空节点再改参数。</div>
                  <a-space class="canvas-cta-actions">
                    <a-button type="primary" :disabled="!canEdit" @click="handleInsertBlankNode">添加空节点</a-button>
                    <a-button v-if="canEdit && activeMode !== 'edit'" @click="activeMode = 'edit'">去改流程</a-button>
                  </a-space>
                </div>

                <div v-if="activeMode === 'run' && !runId" class="canvas-cta-overlay run-variant">
                  <div class="canvas-cta-eyebrow">未执行</div>
                  <div class="canvas-cta-title">点右上角"开始执行"来跑一次</div>
                  <div class="canvas-cta-copy">节点颜色、执行进度和时间线会实时出现在这里。</div>
                </div>

                <iframe
                  ref="editorFrame"
                  :key="iframeKey"
                  :src="iframeSrc"
                  class="flow-iframe"
                  @load="handleFrameLoad"
                />
              </div>
            </a-card>

            <a-card v-if="activeMode === 'overview'" class="page-section-card section-card">
              <template #title>步骤摘要</template>
              <a-timeline v-if="currentSteps.length">
                <a-timeline-item v-for="(step, index) in currentSteps" :key="step.id || index">
                  <div class="step-line-head">
                    <button class="step-link" type="button" @click="focusNode(step.id)">
                      {{ step.id || `步骤 ${Number(index) + 1}` }}
                    </button>
                    <span class="step-line-skill">{{ step.skill_id || step.skill || step.name || '未绑定 Skill' }}</span>
                  </div>
                  <div v-if="stepDeps(step)" class="step-meta">依赖：{{ stepDeps(step) }}</div>
                  <div v-if="step.timeout" class="step-meta">超时：{{ step.timeout }}s</div>
                  <div v-if="step.on_failure && step.on_failure !== 'terminate'" class="step-meta">失败策略：{{ step.on_failure }}</div>
                  <div v-if="step.params_override && Object.keys(step.params_override).length" class="step-meta mono">
                    参数覆盖：{{ JSON.stringify(step.params_override) }}
                  </div>
                </a-timeline-item>
              </a-timeline>
              <a-empty v-else description="暂无步骤" />
            </a-card>

            <a-card v-if="activeMode === 'run'" class="page-section-card section-card">
              <template #title>执行时间线</template>
              <div v-if="runLogs.length === 0" class="timeline-empty">
                {{ connectionMessage }}
              </div>
              <a-timeline v-else>
                <a-timeline-item
                  v-for="(log, index) in runLogs"
                  :key="`${log.type}-${log.step_id || log.run_id || index}`"
                  :dot-color="statusDotColor(log.status)"
                >
                  <div class="timeline-entry">
                    <div class="timeline-entry-main">
                      <button
                        v-if="log.step_id"
                        type="button"
                        class="timeline-step-link"
                        @click="focusNode(String(log.step_id))"
                      >
                        {{ log.step_id }}
                      </button>
                      <span v-else>运行结束</span>
                      <a-tag size="small" :color="statusColor(String(log.status || 'pending'))">
                        {{ statusLabel(String(log.status || 'pending')) }}
                      </a-tag>
                    </div>
                    <div class="timeline-entry-meta">
                      <span>{{ log.time }}</span>
                      <span v-if="log.message">{{ log.message }}</span>
                    </div>
                  </div>
                </a-timeline-item>
              </a-timeline>
            </a-card>

            <a-card v-if="activeMode === 'edit'" class="page-section-card section-card edit-status-card">
              <template #title>
                <div class="section-title-row">
                  <span>{{ editStatusCardTitle }}</span>
                  <a-space size="small" wrap>
                    <a-tag size="small" :color="hasUnsavedChanges ? 'orange' : 'green'">
                      {{ hasUnsavedChanges ? '未保存' : '已保存' }}
                    </a-tag>
                    <a-tag size="small" :color="validationResult ? (validationResult.valid ? 'green' : 'red') : 'gray'">
                      {{ validationSummaryLabel }}
                    </a-tag>
                  </a-space>
                </div>
              </template>

              <div v-if="validationIssues.length" class="issue-list">
                <button
                  v-for="(issue, index) in validationIssues"
                  :key="`${issue.title}-${index}`"
                  type="button"
                  class="issue-item"
                  @click="focusIssue(issue)"
                >
                  <span class="issue-index">{{ Number(index) + 1 }}</span>
                  <span class="issue-copy">
                    <strong>{{ issue.title }}</strong>
                    <span v-if="issue.detail && issue.detail !== issue.title" class="issue-detail">{{ issue.detail }}</span>
                  </span>
                </button>
              </div>
              <div v-else-if="selectedNode" class="selection-card">
                <div class="selection-title">当前节点：{{ selectedNode.id }}</div>
                <div class="selection-copy">
                  Skill：{{ selectedNode.skill_id || selectedNode.skill || selectedNode.name || '未绑定' }}
                </div>
                <div v-if="stepDeps(selectedNode)" class="selection-copy">依赖：{{ stepDeps(selectedNode) }}</div>
              </div>
              <div v-else-if="selectedEdgeId" class="selection-card">
                <div class="selection-title">当前连线：{{ selectedEdgeId }}</div>
                <div class="selection-copy">右侧面板可以修改这条线的条件。</div>
              </div>
              <div v-else class="sidebar-note">
                {{ validationResult ? '检查通过，可以保存或提交审核。' : '点画布上的节点/连线可以直接改参数；改完点"检查问题"再保存。' }}
              </div>
            </a-card>
          </a-col>

          <a-col v-if="activeMode === 'overview'" :xs="24" :xl="7">
            <a-card class="page-section-card sidebar-card">
              <template #title>流程概要</template>
              <div class="sidebar-section">
                <a-descriptions :column="1" layout="vertical" size="small">
                  <a-descriptions-item label="用途">{{ playbook.summary || playbook.description || '-' }}</a-descriptions-item>
                  <a-descriptions-item label="部门">{{ playbook.department || '-' }}</a-descriptions-item>
                  <a-descriptions-item label="触发">{{ scheduleLabel }}</a-descriptions-item>
                  <a-descriptions-item label="SLA">{{ slaLabel }}</a-descriptions-item>
                  <a-descriptions-item label="最近运行">{{ runId ? `${statusLabel(runStatus)} · ${shortRunId}` : '暂无运行记录' }}</a-descriptions-item>
                  <a-descriptions-item v-if="playbook._meta?.modified_at" label="更新时间">{{ formatTime(playbook._meta.modified_at) }}</a-descriptions-item>
                </a-descriptions>
              </div>
            </a-card>
          </a-col>

          <a-col v-else-if="activeMode === 'run'" :xs="24" :xl="7">
            <a-card class="page-section-card sidebar-card">
              <template #title>运行看板</template>
              <div class="sidebar-section">
                <div class="sidebar-kpi-grid">
                  <div class="sidebar-kpi-card">
                    <div class="sidebar-kpi-label">运行状态</div>
                    <div class="sidebar-kpi-value">{{ statusLabel(runStatus) }}</div>
                  </div>
                  <div class="sidebar-kpi-card">
                    <div class="sidebar-kpi-label">执行进度</div>
                    <div class="sidebar-kpi-value">{{ completedSteps }}/{{ totalSteps || currentSteps.length }}</div>
                  </div>
                </div>

                <div class="sidebar-block">
                  <div class="sidebar-block-title">连接状态</div>
                  <div class="sidebar-note">{{ connectionMessage }}</div>
                </div>

                <div class="sidebar-block">
                  <div class="sidebar-block-title">节点状态</div>
                  <div class="status-list" v-if="stepStatusRows.length">
                    <button
                      v-for="row in stepStatusRows"
                      :key="row.stepId"
                      type="button"
                      class="status-item"
                      @click="focusNode(row.stepId)"
                    >
                      <span class="status-item-step">{{ row.stepId }}</span>
                      <a-tag size="small" :color="statusColor(row.status)">{{ statusLabel(row.status) }}</a-tag>
                    </button>
                  </div>
                  <div v-else class="sidebar-note">运行开始后，节点状态会在这里同步更新。</div>
                </div>
              </div>
            </a-card>
          </a-col>
        </a-row>

        <div v-else class="advanced-layout">
          <a-row :gutter="[16, 16]">
            <a-col :xs="24" :xl="12">
              <a-card class="page-section-card advanced-card">
                <template #title>
                  <div class="section-title-row">
                    <span>文本流程图</span>
                    <a-tag size="small" color="green">高级</a-tag>
                  </div>
                </template>
                <div v-if="mermaidCode" class="mermaid-shell">
                  <div ref="mermaidContainer" class="mermaid-container"></div>
                </div>
                <a-empty
                  v-else
                  description="当前流程暂无文本流程图。你仍可使用上方可视化流程图查看结构。"
                />
              </a-card>
            </a-col>
            <a-col :xs="24" :xl="12">
              <a-card class="page-section-card advanced-card">
                <template #title>
                  <div class="section-title-row">
                    <span>原始配置</span>
                    <a-tag size="small">高级</a-tag>
                  </div>
                </template>
                <a-textarea
                  :model-value="rawConfigPreview"
                  :auto-size="{ minRows: 22, maxRows: 32 }"
                  readonly
                  class="raw-config"
                />
              </a-card>
            </a-col>
          </a-row>

          <a-row :gutter="[16, 16]" class="advanced-footer-grid">
            <a-col :xs="24" :xl="12">
              <a-card class="page-section-card advanced-card">
                <template #title>审核与调试上下文</template>
                <a-descriptions :column="1" layout="vertical" size="small">
                  <a-descriptions-item label="审核单">{{ reviewState ? `#${reviewState.id}` : '未关联' }}</a-descriptions-item>
                  <a-descriptions-item label="当前 Run">{{ runId || '-' }}</a-descriptions-item>
                  <a-descriptions-item label="最后检查">{{ validationResult ? validationSummaryLabel : '尚未检查' }}</a-descriptions-item>
                  <a-descriptions-item label="文件更新时间">{{ playbook._meta?.modified_at ? formatTime(playbook._meta.modified_at) : '-' }}</a-descriptions-item>
                </a-descriptions>
              </a-card>
            </a-col>
            <a-col :xs="24" :xl="12">
              <a-card class="page-section-card advanced-card">
                <template #title>步骤索引</template>
                <div v-if="currentSteps.length" class="advanced-step-list">
                  <button
                    v-for="step in currentSteps"
                    :key="step.id"
                    type="button"
                    class="advanced-step-item"
                    @click="focusNode(step.id)"
                  >
                    <span>{{ step.id }}</span>
                    <span>{{ step.skill_id || step.skill || step.name || '未绑定 Skill' }}</span>
                  </button>
                </div>
                <a-empty v-else description="暂无步骤" />
              </a-card>
            </a-col>
          </a-row>
        </div>
      </a-card>

      <ConditionEditor
        ref="conditionEditorRef"
        :step-ids="availableStepIds"
        :step-outputs="availableStepOutputs"
        @save="onConditionSave"
      />

      <a-modal
        v-model:visible="rejectDialogVisible"
        title="驳回审核"
        ok-text="确认驳回"
        cancel-text="取消"
        :ok-button-props="{ status: 'danger' }"
        :on-before-ok="handleConfirmReject"
      >
        <a-form :model="{}" layout="vertical">
          <a-form-item label="驳回原因" required>
            <a-textarea
              v-model="rejectReasonInput"
              placeholder="请说明驳回理由（会通知提交人）"
              :auto-size="{ minRows: 3, maxRows: 6 }"
            />
          </a-form-item>
        </a-form>
      </a-modal>
    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, onUnmounted, ref, toRaw, watch } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { Message, Modal } from '@arco-design/web-vue'
import {
  IconLeft,
  IconPlayArrow,
  IconSave,
  IconCheckCircle,
  IconQuestionCircle,
  IconCopy,
} from '@arco-design/web-vue/es/icon'
import { playbookApi as rawPlaybookApi, reviewApi as rawReviewApi, skillApi as rawSkillApi } from '@/api'
import { useUserStore } from '@/stores/user'
import ConditionEditor from '@/components/ConditionEditor.vue'
import DOMPurify from 'dompurify'
import mermaid from 'mermaid'
import { formatTime as formatBjtTime, formatTimeOnly } from '@/utils/format'

const playbookApi: any = rawPlaybookApi
const reviewApi: any = rawReviewApi
const skillApi: any = rawSkillApi
const userStore = useUserStore()
const route: any = useRoute()
const router: any = useRouter()
const pbName = computed(() => String(route.params.name || ''))

const GUIDE_HINT_KEY = 'sf.playbookWorkbench.guideSeen'

const loading = ref(false)
const saving = ref(false)
const validating = ref(false)
const publishing = ref(false)
const running = ref(false)
const reviewLoading = ref(false)
const reviewActionLoading = ref(false)
const rejectDialogVisible = ref(false)
const rejectReasonInput = ref('')
const guidePopoverVisible = ref(false)
const guideHintActive = ref(false)
let guideHintTimer: ReturnType<typeof setTimeout> | null = null

const playbook = ref<any>({})
const skills = ref<any[]>([])
const pendingSteps = ref<any[] | null>(null)
const pendingLayout = ref<any>(null)
const hasUnsavedChanges = ref(false)
const hasLoadedPlaybook = ref(false)
// 追踪 iframe 是否已发过首条 playbook-changed: 旧 Playbook 没 _canvas_layout 时,
// 首条消息是 iframe auto-layout 的结果 (非用户操作), 需要吸收为 saved 基线;
// 之后的 playbook-changed 无论布局是否"和基线一致"都视为真实编辑, 不再吞掉。
let hasReceivedInitialLayout = false
const savedStepSignature = ref('')
const savedLayoutSignature = ref('')
const validationResult = ref<any | null>(null)
const reviewState = ref<any | null>(null)
const mermaidCode = ref('')
const activeMode = ref<'overview' | 'edit' | 'run' | 'advanced'>('overview')
const runId = ref('')
const runStatus = ref('pending')
const completedSteps = ref(0)
const totalSteps = ref(0)
const runLogs = ref<any[]>([])
const stepStatuses = ref<Record<string, string>>({})
const selectedNodeId = ref<string | null>(null)
const selectedEdgeId = ref<string | null>(null)
const mermaidContainer = ref<HTMLElement | null>(null)
const editorFrame = ref<any>(null)
const conditionEditorRef = ref<any>(null)

let editingEdgeId: string | null = null
let ws: WebSocket | null = null
let heartbeatTimer: ReturnType<typeof setInterval> | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempts = 0
let isUnmounting = false
const MAX_RECONNECT_ATTEMPTS = 5
const connectionMessage = ref('开始执行后，这里会显示实时连接状态。')

mermaid.initialize({
  startOnLoad: false,
  theme: 'base',
  themeVariables: {
    primaryColor: '#fff7ea',
    primaryBorderColor: '#243249',
    primaryTextColor: '#162033',
    secondaryColor: '#edf2ff',
    tertiaryColor: '#eef7f3',
    lineColor: '#8a96a8',
    textColor: '#162033',
    nodeBorder: '#243249',
    mainBkg: '#fff8ef',
    clusterBkg: '#f5ede0',
    clusterBorder: '#cfbea1',
    edgeLabelBackground: '#fffdf8',
    // v2.0.18 L2：mermaid SVG 下的文字渲染——system-ui 在中文下 fallback 效果不稳，
    // 明确列出 CJK 字体栈优先。全英文环境 system-ui 仍然会被选中。
    fontFamily: "'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', system-ui, sans-serif",
    fontSize: '13px',
  },
  flowchart: {
    useMaxWidth: false,
    htmlLabels: false,
    curve: 'basis',
    padding: 24,
    nodeSpacing: 48,
    rankSpacing: 56,
  },
  securityLevel: 'loose',
})

const reviewId = computed(() => {
  const raw = route.query.review_id
  return typeof raw === 'string' ? raw : ''
})

const currentSteps = computed(() => pendingSteps.value || playbook.value?.steps || [])

const selectedNode = computed(() => {
  if (!selectedNodeId.value) return null
  return currentSteps.value.find((step: any) => step.id === selectedNodeId.value) || null
})

const canEdit = computed(() => userStore.isEngineer || userStore.isAdmin)
const canRun = computed(() => userStore.isEngineer || userStore.isAdmin)
const canValidate = computed(() => userStore.isEngineer || userStore.isAdmin || userStore.canViewAll)

const canActReview = computed(() => {
  const review = reviewState.value
  if (!review || review.status !== 'pending') return false
  if (userStore.isEngineer || userStore.isAdmin || userStore.canViewAll) return true
  return review.reviewer && review.reviewer === userStore.userInfo?.user_id
})

const scheduleLabel = computed(() => playbook.value?.trigger?.schedule || '手动触发')
const slaLabel = computed(() => (playbook.value?.sla_minutes ? `${playbook.value.sla_minutes} 分钟` : '未设置'))
const shortRunId = computed(() => (runId.value ? runId.value.slice(0, 8) : '-'))

const iframeMode = computed(() => {
  if (activeMode.value === 'edit') return 'edit'
  if (activeMode.value === 'run') return 'run'
  return 'overview'
})

const iframeSrc = computed(() => {
  const params = new URLSearchParams({ name: pbName.value })
  if (iframeMode.value === 'overview') params.set('readonly', '1')
  if (iframeMode.value === 'run') {
    params.set('readonly', '1')
    params.set('live', '1')
  }
  return `/playbook-editor/index.html?${params.toString()}`
})

const iframeKey = computed(() => `${iframeMode.value}:${pbName.value}:${runId.value || 'idle'}`)

const canvasCardTitle = computed(() => {
  if (activeMode.value === 'edit') return '画布'
  if (activeMode.value === 'run') return '执行画布'
  return '流程预览'
})

const editStatusCardTitle = computed(() => {
  if (validationIssues.value.length) return `发现 ${validationIssues.value.length} 个问题`
  if (selectedNode.value || selectedEdgeId.value) return '当前选中'
  return '草稿状态'
})

const showEmptyCanvasCTA = computed(() => {
  if (activeMode.value !== 'edit' && activeMode.value !== 'overview') return false
  return currentSteps.value.length === 0 && hasLoadedPlaybook.value
})

const rawConfigPreview = computed(() => {
  const document = buildDocumentFromState()
  return JSON.stringify(document, null, 2)
})

const validationIssues = computed(() => {
  const issues = Array.isArray(validationResult.value?.issues)
    ? validationResult.value.issues
    : Array.isArray(validationResult.value?.errors)
      ? validationResult.value.errors.map((message: string) => ({ message }))
      : []
  return issues.map((issue: any, index: number) => ({
    key: `${issue.step_id || 'issue'}-${index}`,
    title: String(issue.title || issue.message || issue || ''),
    detail: String(issue.message || issue.title || issue || ''),
    nodeId: issue.step_id || extractStepId(String(issue.message || issue.title || issue || '')),
  }))
})

const validationSummaryLabel = computed(() => {
  if (!validationResult.value) return '尚未检查'
  if (validationResult.value.valid) return '检查通过'
  const total = validationIssues.value.length
  return `发现 ${total} 个问题`
})

const availableStepIds = computed(() => {
  return currentSteps.value
    .map((step: any) => step.skill_id || step.id || step.name)
    .filter(Boolean)
})

const availableStepOutputs = computed(() => {
  const outputs: Record<string, string[]> = {}
  for (const step of currentSteps.value) {
    const id = String(step.skill_id || step.id || step.name || '')
    outputs[id] = Array.isArray(step.output_fields) && step.output_fields.length ? step.output_fields : ['result']
  }
  return outputs
})

const stepStatusRows = computed(() => {
  return currentSteps.value
    .map((step: any) => ({ stepId: String(step.id), status: stepStatuses.value[String(step.id)] || 'pending' }))
    .filter((item: any) => item.stepId)
})

function normalizeMode(raw: unknown): 'overview' | 'edit' | 'run' | 'advanced' {
  if (raw === 'edit' || raw === 'run' || raw === 'advanced' || raw === 'overview') return raw
  return route.query.run_id ? 'run' : 'overview'
}

function compareQuery(nextQuery: Record<string, unknown>) {
  const keys = Array.from(new Set([...Object.keys(route.query || {}), ...Object.keys(nextQuery || {})]))
  return keys.every((key) => {
    const current = route.query?.[key]
    const next = nextQuery[key]
    return String(current ?? '') === String(next ?? '')
  })
}

function syncQueryState() {
  const nextQuery: Record<string, unknown> = { ...route.query }
  if (activeMode.value === 'overview') delete nextQuery.mode
  else nextQuery.mode = activeMode.value
  if (runId.value) nextQuery.run_id = runId.value
  else delete nextQuery.run_id
  if (compareQuery(nextQuery)) return
  router.replace({ path: `/playbook/${pbName.value}`, query: nextQuery })
}

function buildDocumentFromState() {
  const draft = { ...(playbook.value || {}) }
  draft.name = draft.name || pbName.value
  draft.steps = pendingSteps.value || draft.steps || []
  if (pendingLayout.value !== null && pendingLayout.value !== undefined) {
    draft._canvas_layout = pendingLayout.value
  }
  delete draft._meta
  return draft
}

function normalizeDependsOn(dependsOn: unknown) {
  if (!dependsOn) return []
  if (typeof dependsOn === 'string') return [dependsOn]
  if (!Array.isArray(dependsOn)) return []
  return dependsOn
    .map((item: any) => (typeof item === 'string' ? item : item?.step_id || item?.id || ''))
    .filter(Boolean)
}

function normalizeStep(step: any) {
  const paramsOverride =
    step?.params_override && typeof step.params_override === 'object' ? step.params_override : {}
  return {
    id: String(step?.id || ''),
    skill_id: String(step?.skill_id || step?.skill || ''),
    name: String(step?.name || step?.skill_id || step?.skill || step?.id || ''),
    depends_on: normalizeDependsOn(step?.depends_on).sort(),
    timeout: Number(step?.timeout || 300),
    on_failure: String(step?.on_failure || 'terminate'),
    params_override: paramsOverride,
  }
}

function normalizeDraftSnapshot(payload: any) {
  const layout = payload?._canvas_layout
  return {
    steps: Array.isArray(payload?.steps) ? payload.steps.map((step: any) => normalizeStep(step)) : [],
    _canvas_layout: layout && typeof layout === 'object' ? layout : {},
  }
}

function stepSignature(payload: any) {
  const steps = normalizeDraftSnapshot(payload).steps
  return JSON.stringify(
    [...steps].sort((left, right) => String(left.id).localeCompare(String(right.id))),
  )
}

function layoutSignature(layout: any) {
  if (!layout || typeof layout !== 'object' || Array.isArray(layout)) return ''
  if (Array.isArray(layout.nodes) || Array.isArray(layout.edges)) return ''

  const positions = Object.entries(layout)
    .filter(([, value]: [string, any]) => value && typeof value === 'object' && 'x' in value && 'y' in value)
    .map(([id, value]: [string, any]) => ({
      id,
      x: Number(value.x || 0),
      y: Number(value.y || 0),
    }))
    .sort((left, right) => left.id.localeCompare(right.id))

  return JSON.stringify(positions)
}

function normalizeSkillList(payload: any) {
  if (Array.isArray(payload)) return payload
  if (Array.isArray(payload?.items)) return payload.items
  return []
}

function stepDeps(step: any) {
  const deps = step?.depends_on
  if (!deps) return ''
  if (typeof deps === 'string') return deps
  if (Array.isArray(deps)) return deps.map((item) => (typeof item === 'string' ? item : item.step_id)).join(', ')
  return ''
}

function extractStepId(message: string) {
  const patterns = [
    /步骤\d+\(([^)]+)\)/,
    /步骤'([^']+)'/,
    /步骤\s+([A-Za-z0-9\-_]+)/,
  ]
  for (const pattern of patterns) {
    const match = message.match(pattern)
    if (match?.[1]) return match[1]
  }
  return ''
}

function statusColor(status: string) {
  const map: Record<string, string> = {
    completed: 'green',
    success: 'green',
    running: 'arcoblue',
    failed: 'red',
    timeout: 'red',
    skipped: 'orange',
    pending: 'gray',
  }
  return map[status] || 'gray'
}

function statusDotColor(status: string) {
  const map: Record<string, string> = {
    completed: 'rgb(var(--green-6))',
    success: 'rgb(var(--green-6))',
    running: 'rgb(var(--arcoblue-6))',
    failed: 'rgb(var(--red-6))',
    timeout: 'rgb(var(--red-6))',
    skipped: 'rgb(var(--orange-6))',
    pending: 'var(--color-text-4)',
  }
  return map[status] || 'var(--color-text-4)'
}

function statusLabel(status: string) {
  const map: Record<string, string> = {
    pending: '待执行',
    running: '执行中',
    success: '成功',
    completed: '已完成',
    failed: '失败',
    timeout: '超时',
    skipped: '已跳过',
  }
  return map[status] || status || '待执行'
}

function reviewStatusColor(status: string) {
  if (status === 'approved') return 'green'
  if (status === 'rejected') return 'red'
  return 'orange'
}

function reviewStatusLabel(status: string) {
  if (status === 'approved') return '已通过'
  if (status === 'rejected') return '已驳回'
  return '待审核'
}

function formatTime(value?: string) {
  return formatBjtTime(value)
}

function handleBeforeUnload(event: BeforeUnloadEvent) {
  if (!hasUnsavedChanges.value) return
  event.preventDefault()
  event.returnValue = ''
}

async function loadSkills() {
  try {
    skills.value = normalizeSkillList(await skillApi.list())
  } catch {
    skills.value = []
  }
}

async function loadPlaybook() {
  loading.value = true
  hasLoadedPlaybook.value = false
  hasReceivedInitialLayout = false
  try {
    playbook.value = await playbookApi.get(pbName.value)
    pendingSteps.value = null
    pendingLayout.value = null
    hasUnsavedChanges.value = false
    savedStepSignature.value = stepSignature(playbook.value)
    savedLayoutSignature.value = layoutSignature(playbook.value?._canvas_layout)
    validationResult.value = null
    totalSteps.value = currentSteps.value.length
    hasLoadedPlaybook.value = true
  } catch (error: any) {
    Message.error(error?._message || '加载流程失败')
  } finally {
    loading.value = false
  }
}

async function loadMermaid() {
  try {
    const result = await playbookApi.mermaid(pbName.value)
    mermaidCode.value = result?.mermaid || ''
  } catch {
    mermaidCode.value = ''
  }
}

async function renderMermaid() {
  if (!mermaidContainer.value || !mermaidCode.value) return
  try {
    const id = `playbook-mermaid-${Date.now()}`
    const { svg } = await mermaid.render(id, mermaidCode.value)
    mermaidContainer.value.innerHTML = DOMPurify.sanitize(svg, {
      USE_PROFILES: { svg: true, svgFilters: true },
      ADD_TAGS: ['style'],
      ADD_ATTR: ['id', 'class', 'style', 'xmlns'],
    })
  } catch {
    mermaidContainer.value.innerHTML = ''
  }
}

async function loadReview() {
  if (!reviewId.value) {
    reviewState.value = null
    return
  }
  reviewLoading.value = true
  try {
    reviewState.value = await reviewApi.get(reviewId.value)
  } catch (error: any) {
    reviewState.value = null
    Message.error(error?._message || '加载审核单失败')
  } finally {
    reviewLoading.value = false
  }
}

function cloneMessageValue(value: unknown, visited = new WeakSet<object>()): any {
  if (value === undefined) return null
  if (value === null || typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return value
  }
  if (typeof value === 'bigint') return String(value)
  if (value instanceof Date) return value.toISOString()

  const rawValue = typeof value === 'object' ? toRaw(value) : value

  if (Array.isArray(rawValue)) {
    return rawValue.map((item) => cloneMessageValue(item, visited))
  }

  if (rawValue && typeof rawValue === 'object') {
    if (visited.has(rawValue)) return null
    visited.add(rawValue)
    const clone: Record<string, unknown> = {}
    for (const [key, item] of Object.entries(rawValue)) {
      if (typeof item === 'function' || typeof item === 'symbol') continue
      clone[key] = cloneMessageValue(item, visited)
    }
    visited.delete(rawValue)
    return clone
  }

  return String(rawValue)
}

function toMessagePayload(payload: unknown) {
  return cloneMessageValue(payload)
}

function postToEditor(type: string, payload: unknown) {
  const frame = editorFrame.value
  if (!frame?.contentWindow) return
  frame.contentWindow.postMessage({ type, payload: toMessagePayload(payload) }, window.location.origin)
}

function pushDocumentToEditor() {
  if (activeMode.value === 'run') {
    postToEditor('load-playbook', { ...playbook.value, steps: playbook.value?.steps || [] })
    return
  }
  postToEditor('load-playbook', buildDocumentFromState())
}

function pushSkillsToEditor() {
  postToEditor('set-skills', skills.value)
}

function pushStatusToCanvas() {
  const nodeColorMap: Record<string, { color: string; animation: string; borderStyle?: string }> = {
    pending: { color: 'rgb(var(--gray-4))', animation: 'none' },
    running: { color: 'rgb(var(--arcoblue-6))', animation: 'pulse' },
    success: { color: 'rgb(var(--green-6))', animation: 'flash' },
    completed: { color: 'rgb(var(--green-6))', animation: 'flash' },
    failed: { color: 'rgb(var(--red-6))', animation: 'shake' },
    timeout: { color: 'rgb(var(--red-6))', animation: 'shake' },
    skipped: { color: 'rgb(var(--orange-6))', animation: 'none', borderStyle: 'dashed' },
  }
  const enriched: Record<string, any> = {}
  for (const [stepId, status] of Object.entries(stepStatuses.value)) {
    enriched[stepId] = {
      status,
      ...(nodeColorMap[status] || nodeColorMap.pending),
    }
  }
  postToEditor('update-node-styles', enriched)
}

function focusNode(nodeId?: string | null) {
  if (!nodeId) return
  selectedNodeId.value = nodeId
  selectedEdgeId.value = null
  postToEditor('focus-element', { nodeId, edgeId: null })
}

function generateBlankStepId(existing: Array<{ id?: string }>): string {
  const used = new Set(existing.map((step) => String(step?.id || '')).filter(Boolean))
  let index = 1
  while (used.has(`step_${index}`)) index += 1
  return `step_${index}`
}

function handleInsertBlankNode() {
  if (!canEdit.value) return
  if (activeMode.value !== 'edit') activeMode.value = 'edit'
  const existing = pendingSteps.value ?? playbook.value?.steps ?? []
  const nextId = generateBlankStepId(existing)
  const nextSteps = [
    ...existing.map((step: any) => ({ ...step })),
    { id: nextId, skill_id: '', name: nextId, depends_on: [], timeout: 300, on_failure: 'terminate', params_override: {} },
  ]
  pendingSteps.value = nextSteps
  const layout = { ...(pendingLayout.value || playbook.value?._canvas_layout || {}) }
  const offset = existing.length * 120
  layout[nextId] = { x: 120 + offset, y: 160 }
  pendingLayout.value = layout
  hasUnsavedChanges.value = true
  pushDocumentToEditor()
  focusNode(nextId)
  Message.info(`已插入空节点 ${nextId}，可在右侧面板改 Skill 和参数`)
}

async function handleCopyRawConfig() {
  const text = rawConfigPreview.value
  // 优先走现代 Clipboard API（HTTPS 或 localhost 才可用）
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      Message.success('原始配置已复制到剪贴板')
      return
    }
  } catch {
    // 走下面的 execCommand 兜底
  }
  // 内网 http 场景兜底：临时 textarea + execCommand('copy')
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.top = '-9999px'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    if (ok) {
      Message.success('原始配置已复制到剪贴板')
    } else {
      Message.warning('复制失败，请手动选中下方 JSON 并 Ctrl+C')
    }
  } catch {
    Message.warning('复制失败，请手动选中下方 JSON 并 Ctrl+C')
  }
}

function onGuidePopoverChange(visible: boolean) {
  guidePopoverVisible.value = visible
  if (visible) {
    guideHintActive.value = false
    try {
      localStorage.setItem(GUIDE_HINT_KEY, '1')
    } catch {
      // localStorage 不可用不影响主流程
    }
  }
}

function focusIssue(issue: { nodeId?: string; title: string; detail: string }) {
  if (issue.nodeId) {
    activeMode.value = activeMode.value === 'advanced' ? 'overview' : activeMode.value
    focusNode(issue.nodeId)
    return
  }
  Message.warning(issue.detail || issue.title)
}

function handleFrameLoad() {
  pushDocumentToEditor()
  pushSkillsToEditor()
  if (activeMode.value === 'run') pushStatusToCanvas()
}

function handleIframeMessage(event: MessageEvent) {
  if (event.origin !== window.location.origin) return
  const data = event.data
  if (!data || data.source !== 'playbook-editor') return

  if (data.type === 'playbook-changed') {
    if (!hasLoadedPlaybook.value) return
    const draftPayload = normalizeDraftSnapshot(data.payload || {})
    const nextStepSignature = stepSignature(draftPayload)
    const nextLayoutSignature = layoutSignature(draftPayload._canvas_layout)
    totalSteps.value = draftPayload.steps.length

    // 只有"iframe 第一次自动 layout 导入" 视为初始化, 吸收为 saved 基线;
    // 之后任何消息都是真实用户改动, 按正常分支处理。
    if (
      !hasReceivedInitialLayout &&
      nextStepSignature === savedStepSignature.value &&
      !savedLayoutSignature.value &&
      nextLayoutSignature
    ) {
      hasReceivedInitialLayout = true
      savedLayoutSignature.value = nextLayoutSignature
      pendingSteps.value = null
      pendingLayout.value = null
      hasUnsavedChanges.value = false
      return
    }
    hasReceivedInitialLayout = true

    if (
      nextStepSignature === savedStepSignature.value &&
      nextLayoutSignature === savedLayoutSignature.value
    ) {
      pendingSteps.value = null
      pendingLayout.value = null
      hasUnsavedChanges.value = false
      return
    }

    pendingSteps.value = draftPayload.steps
    pendingLayout.value = draftPayload._canvas_layout
    hasUnsavedChanges.value = true
    return
  }

  if (data.type === 'editor-ready') {
    pushDocumentToEditor()
    pushSkillsToEditor()
    if (activeMode.value === 'run') pushStatusToCanvas()
    if (selectedNodeId.value || selectedEdgeId.value) {
      postToEditor('focus-element', { nodeId: selectedNodeId.value, edgeId: selectedEdgeId.value })
    }
    return
  }

  if (data.type === 'selection-changed') {
    selectedNodeId.value = data.payload?.nodeId || null
    selectedEdgeId.value = data.payload?.edgeId || null
    return
  }

  if (data.type === 'edit-condition') {
    editingEdgeId = data.payload?.edgeId || null
    conditionEditorRef.value?.open(data.payload?.condition)
  }
}

function onConditionSave(condition: any) {
  postToEditor('update-condition', { edgeId: editingEdgeId, condition })
  editingEdgeId = null
}

async function saveDraft(showMessage = true) {
  if (!canEdit.value) return false
  saving.value = true
  try {
    const payload = buildDocumentFromState()
    await playbookApi.save(pbName.value, payload)
    playbook.value = {
      ...playbook.value,
      ...payload,
      file_name: playbook.value?.file_name || pbName.value,
    }
    pendingSteps.value = null
    pendingLayout.value = null
    hasUnsavedChanges.value = false
    savedStepSignature.value = stepSignature(payload)
    savedLayoutSignature.value = layoutSignature(payload._canvas_layout)
    totalSteps.value = currentSteps.value.length
    if (showMessage) Message.success('保存成功')
    return true
  } catch (error: any) {
    Message.error(error?._message || '保存失败')
    return false
  } finally {
    saving.value = false
  }
}

async function handleSaveClick() {
  await saveDraft(true)
}

async function handleValidateDraft() {
  validating.value = true
  try {
    validationResult.value = await playbookApi.validateDraft(buildDocumentFromState())
    if (validationResult.value?.valid) Message.success('检查通过')
    else Message.warning(validationResult.value?.message || `发现 ${validationIssues.value.length} 个问题`)
  } catch (error: any) {
    Message.error(error?._message || '检查失败')
  } finally {
    validating.value = false
  }
}

function handlePublish() {
  if (hasUnsavedChanges.value) {
    activeMode.value = 'edit'
    Message.warning('请先保存当前修改，再提交审核。')
    return
  }
  Modal.confirm({
    title: '确认提交审核',
    content: '提交后将生成审核单，审核通过后才会正式发布。',
    okText: '确认提交',
    cancelText: '取消',
    onOk: async () => {
      publishing.value = true
      try {
        const result = await playbookApi.publish(pbName.value)
        Message.success(`已提交审核 (ID: ${result.review_id})`)
        await router.replace({
          path: `/playbook/${pbName.value}`,
          query: {
            ...route.query,
            review_id: String(result.review_id),
          },
        })
        await loadReview()
      } catch (error: any) {
        Message.error(error?._message || '提交失败')
      } finally {
        publishing.value = false
      }
    },
  })
}

async function startRun(saveBeforeRun: boolean) {
  if (saveBeforeRun) {
    const saved = await saveDraft(false)
    if (!saved) return
  }

  running.value = true
  try {
    const result = await playbookApi.run(pbName.value, {})
    runId.value = String(result.run_id || '')
    runStatus.value = String(result.status || 'running')
    completedSteps.value = Number(result.completed_steps || 0)
    totalSteps.value = Number(result.total_steps || currentSteps.value.length)
    runLogs.value = []
    stepStatuses.value = {}
    activeMode.value = 'run'
    connectionMessage.value = '正在连接实时执行流...'
    Message.success(runId.value ? `已开始执行，正在进入监控视角（${runId.value.slice(0, 8)}）` : '已开始执行')
  } catch (error: any) {
    Message.error(error?._message || '执行失败')
  } finally {
    running.value = false
  }
}

async function handleRunClick() {
  if (!canRun.value) return
  if (hasUnsavedChanges.value) {
    Modal.confirm({
      title: '执行前检测到未保存修改',
      content: '推荐先保存再执行。你也可以直接执行已保存版本。',
      okText: '先保存再执行',
      cancelText: '执行已保存版本',
      onOk: () => startRun(true),
      onCancel: () => startRun(false),
    })
    return
  }
  await startRun(false)
}

async function handleApproveReview() {
  if (!reviewState.value?.id) return
  reviewActionLoading.value = true
  try {
    await reviewApi.approve(String(reviewState.value.id), {})
    Message.success('审核已通过')
    await loadReview()
  } catch (error: any) {
    Message.error(error?._message || '审核通过失败')
  } finally {
    reviewActionLoading.value = false
  }
}

function handleRejectReview() {
  if (!reviewState.value?.id) return
  rejectReasonInput.value = ''
  rejectDialogVisible.value = true
}

async function handleConfirmReject(): Promise<boolean> {
  const reason = rejectReasonInput.value.trim()
  if (!reason) {
    Message.warning('请填写驳回原因')
    return false
  }
  if (!reviewState.value?.id) return true
  reviewActionLoading.value = true
  try {
    await reviewApi.reject(String(reviewState.value.id), { reason, reject_reason: 'logic' })
    Message.success('审核已驳回')
    await loadReview()
    return true
  } catch (error: any) {
    Message.error(error?._message || '审核驳回失败')
    return false
  } finally {
    reviewActionLoading.value = false
  }
}

function stopHeartbeat() {
  if (heartbeatTimer) clearInterval(heartbeatTimer)
  heartbeatTimer = null
}

function startHeartbeat() {
  stopHeartbeat()
  heartbeatTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'ping' }))
    }
  }, 30000)
}

function cleanupSocket() {
  stopHeartbeat()
  if (reconnectTimer) clearTimeout(reconnectTimer)
  reconnectTimer = null
  if (ws) {
    ws.close()
    ws = null
  }
}

function handleRunEvent(event: any) {
  const now = formatTimeOnly(Date.now())
  if (event.type === 'step_status' && event.step_id) {
    stepStatuses.value = {
      ...stepStatuses.value,
      [String(event.step_id)]: String(event.status || 'pending'),
    }
    completedSteps.value = Object.values(stepStatuses.value).filter((status) =>
      ['completed', 'success', 'failed', 'timeout', 'skipped'].includes(String(status)),
    ).length
    runLogs.value.push({
      ...event,
      time: now,
    })
    pushStatusToCanvas()
    return
  }

  if (event.type === 'run_complete') {
    runStatus.value = String(event.status || runStatus.value || 'completed')
    completedSteps.value = Number(event.completed_steps || completedSteps.value)
    totalSteps.value = Number(event.total_steps || totalSteps.value || currentSteps.value.length)
    connectionMessage.value = '执行事件已接收完成。'
    runLogs.value.push({
      ...event,
      step_id: '运行结束',
      time: now,
    })
  }
}

function connectWs() {
  // v2.0.17 H7：组件正在卸载时绝不新建连接——防定时器/watch 在 unmount
  // 窗口期把 ws 拉起来、onBeforeUnmount 里 cleanupSocket 还没来得及收场。
  if (isUnmounting) return

  cleanupSocket()

  if (activeMode.value !== 'run' || !runId.value) {
    connectionMessage.value = '开始执行后，这里会显示实时连接状态。'
    return
  }

  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = new URL(`${protocol}//${location.host}/api/playbooks/ws/playbook-live/${runId.value}`)
  const explicitToken = typeof route.query.token === 'string' ? route.query.token : ''
  if (explicitToken) url.searchParams.set('token', explicitToken)

  connectionMessage.value = '正在连接实时执行流...'
  ws = new WebSocket(url.toString())
  ws.onopen = () => {
    reconnectAttempts = 0
    connectionMessage.value = '已连接，正在等待执行事件...'
    startHeartbeat()
  }
  ws.onmessage = (messageEvent: MessageEvent) => {
    try {
      handleRunEvent(JSON.parse(messageEvent.data))
    } catch {
      // ignore malformed event
    }
  }
  ws.onerror = () => {
    connectionMessage.value = '连接失败，正在尝试重连...'
  }
  ws.onclose = (closeEvent) => {
    stopHeartbeat()
    // 组件卸载 / 主动 cleanup / 模式切换后不重连 —— 否则死组件上定时器会
    // 永续 retry 直到 MAX_RECONNECT_ATTEMPTS, 造成"看不见的 background ws"
    if (isUnmounting || !ws) return
    if (activeMode.value !== 'run' || !runId.value) return
    if (closeEvent.code === 4001) {
      connectionMessage.value = '认证失败，请重新登录'
      return
    }
    if (closeEvent.code === 4003) {
      connectionMessage.value = '无权查看该执行记录'
      return
    }
    if (runStatus.value !== 'running' || reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      if (runStatus.value === 'running' && reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
        connectionMessage.value = '重连次数已达上限，请刷新页面重试。'
      }
      return
    }
    reconnectAttempts += 1
    const delay = Math.min(1000 * 2 ** (reconnectAttempts - 1), 10000)
    connectionMessage.value = `连接已断开，${delay / 1000}s 后自动重连...`
    // v2.0.17 H7：定时器回调里再次守卫——unmount 窗口期 setTimeout 已调度但
    // 还没 clearTimeout 时 tick 触发的场景，不让 connectWs 起新 ws
    reconnectTimer = setTimeout(() => {
      if (isUnmounting) return
      connectWs()
    }, delay)
  }
}

watch(
  () => route.query.mode,
  (value) => {
    const nextMode = normalizeMode(value)
    if (activeMode.value !== nextMode) activeMode.value = nextMode
  },
  { immediate: true },
)

watch(
  () => route.query.run_id,
  (value) => {
    const nextRunId = typeof value === 'string' ? value : ''
    if (runId.value !== nextRunId) runId.value = nextRunId
  },
  { immediate: true },
)

watch([activeMode, runId], () => {
  syncQueryState()
})

watch(
  () => reviewId.value,
  () => {
    loadReview()
  },
  { immediate: true },
)

watch(
  () => activeMode.value,
  (mode) => {
    if (mode === 'run') connectWs()
    else cleanupSocket()
    if (mode === 'advanced' && mermaidCode.value) {
      nextTick(() => renderMermaid())
    }
  },
)

watch(
  () => mermaidCode.value,
  () => {
    if (activeMode.value === 'advanced' && mermaidCode.value) {
      nextTick(() => renderMermaid())
    }
  },
)

onBeforeRouteLeave((to, from, next) => {
  if (!hasUnsavedChanges.value) return next()
  Modal.confirm({
    title: '当前有未保存修改',
    content: '离开后将丢失当前草稿，是否继续？',
    okText: '放弃并离开',
    cancelText: '继续编辑',
    okButtonProps: { status: 'danger' },
    onOk: () => next(),
    onCancel: () => next(false),
  })
})

onMounted(async () => {
  window.addEventListener('message', handleIframeMessage)
  window.addEventListener('beforeunload', handleBeforeUnload)
  try {
    if (!localStorage.getItem(GUIDE_HINT_KEY)) {
      guideHintActive.value = true
      guideHintTimer = setTimeout(() => {
        guideHintTimer = null
        if (isUnmounting) return
        if (guideHintActive.value) guidePopoverVisible.value = true
      }, 600)
    }
  } catch {
    // localStorage 不可用则不弹首次引导, 不阻塞
  }
  await Promise.all([loadPlaybook(), loadMermaid(), loadSkills()])
})

onBeforeUnmount(() => {
  isUnmounting = true
  cleanupSocket()
  if (guideHintTimer !== null) {
    clearTimeout(guideHintTimer)
    guideHintTimer = null
  }
})

onUnmounted(() => {
  window.removeEventListener('message', handleIframeMessage)
  window.removeEventListener('beforeunload', handleBeforeUnload)
  // cleanupSocket 已在 onBeforeUnmount 执行, 这里只补监听器移除
})
</script>

<style scoped>
.playbook-workbench-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  background: var(--ai-bg);
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}

/* 设计稿 page chrome 覆盖 —— 与 SkillList / AdminUsers 同模式 */
.playbook-workbench-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.playbook-workbench-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
}
.playbook-workbench-page :deep(.page-subtitle) {
  margin: 6px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
  line-height: 1.6;
}
.playbook-workbench-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

.workbench-header {
  gap: 16px;
  align-items: flex-start;
}

.workbench-title-wrap {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  min-width: 0;
}

.workbench-title-block {
  min-width: 0;
}

.workbench-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.workbench-tag-row {
  font-variant-numeric: tabular-nums;
}

.workbench-subtitle {
  max-width: 72ch;
}

.workbench-actions {
  flex-shrink: 0;
}

/* 顶部"返回"按钮：扁平 ai-btn 外形 */
.playbook-workbench-page :deep(.workbench-back-btn) {
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
.playbook-workbench-page :deep(.workbench-back-btn:hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}

.guide-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-3);
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
}

.guide-trigger:hover {
  border-color: var(--ai-border-2);
  color: var(--ai-ink-1);
  background: var(--ai-surface-2);
}

.guide-trigger.is-pulse {
  border-color: var(--ai-ink-3);
  color: var(--ai-ink-1);
  box-shadow: 0 0 0 0 rgba(20, 19, 15, 0.25);
  animation: guidePulse 1.8s ease-out infinite;
}

@keyframes guidePulse {
  0% { box-shadow: 0 0 0 0 rgba(20, 19, 15, 0.25); }
  70% { box-shadow: 0 0 0 8px rgba(20, 19, 15, 0); }
  100% { box-shadow: 0 0 0 0 rgba(20, 19, 15, 0); }
}

.guide-popover {
  max-width: 320px;
  line-height: 1.7;
  font-family: var(--ai-font-sans);
}

.guide-popover-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--ai-ink-1);
  margin-bottom: 8px;
}

.guide-popover-list {
  margin: 0;
  padding-left: 18px;
  color: var(--ai-ink-3);
  font-size: 12.5px;
}

.guide-popover-list li + li {
  margin-top: 4px;
}

.guide-popover-list strong {
  color: var(--ai-ink-1);
  font-weight: 500;
}

.guide-popover-tip {
  margin-top: 10px;
  padding: 6px 10px;
  border-radius: 4px;
  background: var(--ai-info-soft);
  color: var(--ai-info);
  font-size: 11.5px;
  font-weight: 500;
}

/* 卡片：扁平 ai-card —— surface + border + 8px radius，无 shadow */
.playbook-workbench-page :deep(.page-section-card),
.playbook-workbench-page :deep(.review-card),
.playbook-workbench-page :deep(.workbench-shell),
.playbook-workbench-page :deep(.section-card),
.playbook-workbench-page :deep(.advanced-card),
.playbook-workbench-page :deep(.sidebar-card),
.playbook-workbench-page :deep(.canvas-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

.playbook-workbench-page :deep(.arco-card-header) {
  border-bottom: 1px solid var(--ai-border);
}

.playbook-workbench-page :deep(.arco-card-header-title) {
  font-size: 13px !important;
  font-weight: 600 !important;
  color: var(--ai-ink-1) !important;
  letter-spacing: -0.005em;
}

.review-banner {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.review-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

.review-subtitle,
.review-copy,
.review-reason {
  color: var(--ai-ink-3);
  font-size: 12.5px;
  line-height: 1.6;
}

.review-copy,
.review-reason,
.review-diff {
  margin-top: 12px;
}

.review-diff {
  padding: 12px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  color: var(--ai-ink-2);
  white-space: pre-wrap;
  overflow: auto;
  font-size: 11.5px;
  font-family: var(--ai-font-mono);
}

/* 模式切换 tabs —— 36px / 1.5px ink-1 active underline */
.playbook-workbench-page :deep(.workbench-tabs .arco-tabs-nav) {
  background: var(--ai-surface);
  border-bottom: 1px solid var(--ai-border);
  padding: 0;
}
.playbook-workbench-page :deep(.workbench-tabs .arco-tabs-nav::before),
.playbook-workbench-page :deep(.workbench-tabs .arco-tabs-nav-ink) {
  display: none !important;
}
.playbook-workbench-page :deep(.workbench-tabs .arco-tabs-tab) {
  height: 36px;
  padding: 0 12px;
  margin: 0 2px;
  font-size: 13px;
  font-weight: 450;
  color: var(--ai-ink-3);
  border-bottom: 1.5px solid transparent;
  border-radius: 0;
  background: transparent;
  transition: color 0.15s ease, border-color 0.15s ease;
}
.playbook-workbench-page :deep(.workbench-tabs .arco-tabs-tab:hover) {
  color: var(--ai-ink-2);
  background: transparent;
}
.playbook-workbench-page :deep(.workbench-tabs .arco-tabs-tab-active) {
  color: var(--ai-ink-1);
  font-weight: 500;
  border-bottom-color: var(--ai-ink-1);
}
.playbook-workbench-page :deep(.workbench-tabs .arco-tabs-tab-active .arco-tabs-tab-title) {
  color: var(--ai-ink-1);
  font-weight: 500;
}

.workbench-main-grid {
  margin-top: 16px;
}

.section-title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  width: 100%;
}

.canvas-card :deep(.arco-card-body) {
  padding: 0;
}

/* 画布外框：去 shadow，扁平 ai-surface-2 容器 */
.flow-frame-shell,
.mermaid-shell {
  position: relative;
  overflow: hidden;
  border-radius: var(--ai-radius);
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  box-shadow: none;
}

.flow-iframe {
  position: relative;
  z-index: 1;
  width: 100%;
  height: 640px;
  border: none;
  display: block;
  background: transparent;
}

.flow-frame-shell.is-tall .flow-iframe {
  height: 720px;
}

.canvas-cta-overlay {
  position: absolute;
  inset: 0;
  z-index: 3;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 28px 32px;
  background: var(--ai-surface);
  text-align: center;
  pointer-events: auto;
}

.canvas-cta-overlay.run-variant {
  background: var(--ai-surface);
}

.canvas-cta-eyebrow {
  font-size: 11px;
  letter-spacing: 0.06em;
  font-weight: 500;
  text-transform: uppercase;
  color: var(--ai-ink-4);
}

.canvas-cta-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}

.canvas-cta-copy {
  max-width: 460px;
  color: var(--ai-ink-3);
  font-size: 12.5px;
  line-height: 1.6;
}

.canvas-cta-actions {
  margin-top: 6px;
}

.step-meta,
.timeline-entry-meta,
.sidebar-note,
.selection-copy {
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.6;
}

.step-meta.mono,
.timeline-entry-meta {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 11.5px;
}

.step-line-head,
.timeline-entry-main,
.status-item,
.advanced-step-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.step-link,
.timeline-step-link {
  padding: 0;
  border: none;
  background: transparent;
  color: var(--ai-ink-1);
  font-weight: 500;
  font-size: 12.5px;
  font-family: var(--ai-font-mono);
  cursor: pointer;
}
.step-link:hover,
.timeline-step-link:hover {
  color: var(--ai-accent-ink);
}

.step-line-skill {
  color: var(--ai-ink-3);
  font-size: 12px;
}

.timeline-empty {
  padding: 18px 0;
  color: var(--ai-ink-4);
  font-size: 12.5px;
  text-align: center;
}

.timeline-entry {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.sidebar-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.edit-status-card {
  margin-top: 16px;
}

.sidebar-kpi-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.sidebar-kpi-card,
.selection-card {
  padding: 12px 14px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  transition: border-color 0.15s ease;
}
.sidebar-kpi-card:hover,
.selection-card:hover {
  border-color: var(--ai-border-2);
}

.sidebar-kpi-label {
  font-size: 11px;
  color: var(--ai-ink-4);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-weight: 500;
}

.sidebar-kpi-value {
  margin-top: 4px;
  font-weight: 600;
  font-size: 16px;
  color: var(--ai-ink-1);
  font-variant-numeric: tabular-nums;
}

.selection-title,
.sidebar-block-title {
  font-weight: 600;
  font-size: 12.5px;
  color: var(--ai-ink-1);
}

.sidebar-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.issue-list,
.status-list,
.advanced-step-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* 步骤/节点卡片：ai-card hover border-2，选中态 2px ink-1 左边框 */
.issue-item,
.status-item,
.advanced-step-item {
  width: 100%;
  padding: 10px 12px;
  border-radius: var(--ai-radius);
  border: 1px solid var(--ai-border);
  border-left: 2px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  cursor: pointer;
  text-align: left;
  font-size: 12.5px;
  transition: border-color 0.15s ease, background 0.15s ease;
}

.issue-item {
  align-items: flex-start;
}

.issue-item:hover,
.status-item:hover,
.advanced-step-item:hover {
  border-color: var(--ai-border-2);
  background: var(--ai-surface-2);
}

.issue-item.is-selected,
.status-item.is-selected,
.advanced-step-item.is-selected {
  border-left-color: var(--ai-ink-1);
  border-left-width: 2px;
}

.issue-index {
  display: inline-flex;
  width: 20px;
  height: 20px;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  font-size: 11px;
  font-weight: 500;
  flex: none;
  font-variant-numeric: tabular-nums;
}

.issue-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  color: var(--ai-ink-1);
  font-size: 12.5px;
  line-height: 1.5;
}

.issue-copy strong {
  font-weight: 500;
}

.issue-detail {
  font-size: 11.5px;
  color: var(--ai-ink-3);
  font-weight: 400;
}

.status-item-step {
  font-weight: 500;
  font-size: 12px;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

.advanced-step-item span {
  font-size: 12px;
  color: var(--ai-ink-2);
}
.advanced-step-item span:first-child {
  font-family: var(--ai-font-mono);
  font-weight: 500;
  color: var(--ai-ink-1);
}

.advanced-layout,
.advanced-footer-grid {
  margin-top: 8px;
}

/* 原始配置 textarea —— mono / 11.5px */
.raw-config :deep(textarea) {
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-2);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  line-height: 1.5;
}
.raw-config :deep(textarea:focus),
.raw-config :deep(.arco-textarea-focus) {
  border-color: var(--ai-ink-3);
  box-shadow: none;
}

.mermaid-container {
  min-height: 180px;
  overflow: auto;
  padding: 18px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
}

.mermaid-container :deep(svg) {
  max-width: none;
  min-width: 100%;
  height: auto;
}

.mermaid-container :deep(.flowchart-link) {
  stroke: var(--ai-ink-4) !important;
  stroke-width: 1.5px !important;
}

.mermaid-container :deep(.marker) {
  fill: var(--ai-ink-4) !important;
  stroke: var(--ai-ink-4) !important;
}

.mono {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

/* 标签 pill：20px 高 / 4px 方角 / 11px / 500，按 5 档配色 */
.playbook-workbench-page :deep(.arco-tag.arco-tag-size-small),
.playbook-workbench-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.playbook-workbench-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.playbook-workbench-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.playbook-workbench-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.playbook-workbench-page :deep(.arco-tag-color-orange),
.playbook-workbench-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.playbook-workbench-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-3) !important;
  border-color: transparent !important;
}

/* Action buttons：primary ink-1，普通扁平 ai-btn */
.playbook-workbench-page :deep(.arco-btn-primary) {
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
.playbook-workbench-page :deep(.arco-btn-primary:hover) {
  background: #000;
  border-color: #000;
}
.playbook-workbench-page :deep(.arco-btn-primary.arco-btn-status-success) {
  background: var(--ai-ok);
  border-color: var(--ai-ok);
  color: var(--ai-surface);
}
.playbook-workbench-page :deep(.arco-btn-primary.arco-btn-status-success:hover) {
  background: #155f37;
  border-color: #155f37;
}
.playbook-workbench-page :deep(.arco-btn-primary.arco-btn-status-danger) {
  background: var(--ai-bad);
  border-color: var(--ai-bad);
  color: var(--ai-surface);
}
.playbook-workbench-page :deep(.arco-btn-primary.arco-btn-status-danger:hover) {
  background: #962614;
  border-color: #962614;
}

.playbook-workbench-page :deep(.arco-btn:not(.arco-btn-primary):not(.arco-btn-text)) {
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
.playbook-workbench-page :deep(.arco-btn:not(.arco-btn-primary):not(.arco-btn-text):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.playbook-workbench-page :deep(.arco-btn-status-danger:not(.arco-btn-primary)) {
  color: var(--ai-bad);
  border-color: var(--ai-border);
}
.playbook-workbench-page :deep(.arco-btn-size-small) {
  height: 26px;
  padding: 0 10px;
  font-size: 12px;
}

/* 表单 / inputs / textarea —— 6px radius，ai-border，focus → ink-3 */
.playbook-workbench-page :deep(.arco-input-wrapper),
.playbook-workbench-page :deep(.arco-textarea-wrapper),
.playbook-workbench-page :deep(.arco-select-view) {
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface);
  box-shadow: none;
}
.playbook-workbench-page :deep(.arco-input-wrapper:hover),
.playbook-workbench-page :deep(.arco-textarea-wrapper:hover),
.playbook-workbench-page :deep(.arco-select-view:hover) {
  border-color: var(--ai-border-2);
  background: var(--ai-surface);
}
.playbook-workbench-page :deep(.arco-input-focus),
.playbook-workbench-page :deep(.arco-textarea-focus),
.playbook-workbench-page :deep(.arco-select-view-focus) {
  border-color: var(--ai-ink-3) !important;
  box-shadow: none !important;
  background: var(--ai-surface) !important;
}
.playbook-workbench-page :deep(.arco-input),
.playbook-workbench-page :deep(.arco-textarea) {
  font-size: 12.5px;
  color: var(--ai-ink-1);
  background: transparent;
}

/* Timeline 节点 / 时间线 */
.playbook-workbench-page :deep(.arco-timeline-item-dot) {
  background: var(--ai-surface);
}
.playbook-workbench-page :deep(.arco-timeline-item-line) {
  background: var(--ai-border);
}

/* Descriptions —— 标签 ink-4 / 值 ink-1 */
.playbook-workbench-page :deep(.arco-descriptions-item-label) {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.playbook-workbench-page :deep(.arco-descriptions-item-value) {
  color: var(--ai-ink-1);
  font-size: 12.5px;
}

/* 已废弃的 page-section-card 圆角覆盖（旧 20px → 8px 已由扁平规则覆盖） */

@media (max-width: 1280px) {
  .flow-iframe {
    height: 560px;
  }
  .flow-frame-shell.is-tall .flow-iframe {
    height: 640px;
  }
}

@media (max-width: 768px) {
  .workbench-title-wrap,
  .review-banner {
    flex-direction: column;
  }

  .workbench-header {
    align-items: flex-start;
  }

  .sidebar-kpi-grid {
    grid-template-columns: 1fr;
  }

  .flow-iframe,
  .flow-frame-shell.is-tall .flow-iframe {
    height: 480px;
  }

  .canvas-cta-overlay {
    padding: 20px 18px;
  }
}
</style>
