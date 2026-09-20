<template>
  <div class="execution-detail-page ai-main">
    <!-- 统一加载 / 错误 / 内容 三态 -->
    <div v-if="loading" class="execution-detail-body ai-pagebody">
      <SfLoadingState tip="加载执行详情..." height="400px" />
    </div>
    <div v-else-if="loadError" class="execution-detail-body ai-pagebody">
      <SfErrorState :subtitle="loadError" @retry="loadData" />
    </div>
    <template v-else>
      <div class="execution-detail-head ai-pagehead">
          <button type="button" class="ai-btn" @click="$router.push('/executions')">
            <SfShellIcon name="arrowl" />
            <span>返回</span>
          </button>
          <div class="execution-title-row">
            <h2 class="ai-title">执行详情</h2>
            <a-tag :color="(runStatusColor as Record<string, string>)[run.status]" size="large">{{ (runStatusLabel as Record<string, string>)[run.status] || run.status }}</a-tag>
        </div>
      </div>

      <div class="execution-detail-body ai-pagebody">
      <!-- B5: AI 执行摘要 -->
      <a-alert v-if="run.summary" type="info" style="margin-bottom: 16px">
        <template #title>执行摘要</template>
        {{ run.summary }}
      </a-alert>

      <!-- 基本信息 -->
      <a-card title="执行信息" class="execution-detail-card ai-card" style="margin-bottom: 16px">
        <a-descriptions :column="2" bordered>
          <a-descriptions-item label="执行ID">{{ run.id }}</a-descriptions-item>
          <a-descriptions-item label="Playbook">{{ run.playbook_id || '-' }}</a-descriptions-item>
          <a-descriptions-item label="触发方式">{{ run.trigger_type }}</a-descriptions-item>
          <a-descriptions-item label="步骤">{{ run.completed_steps || 0 }} / {{ run.total_steps || '-' }}</a-descriptions-item>
          <a-descriptions-item label="开始时间">{{ formatTime(run.started_at) }}</a-descriptions-item>
          <a-descriptions-item label="完成时间">{{ formatTime(run.completed_at) }}</a-descriptions-item>
        </a-descriptions>
      </a-card>

      <!-- 执行步骤 -->
      <a-card title="执行步骤" class="execution-detail-card ai-card" style="margin-bottom: 16px">
        <a-timeline v-if="steps.length">
          <a-timeline-item v-for="s in steps" :key="s.id"
            :dot-color="s.status === 'success' ? 'rgb(var(--green-6))' : s.status === 'failed' ? 'rgb(var(--red-6))' : 'rgb(var(--primary-6))'">
            <div>
              <strong>{{ s.skill_id }}</strong>
              <span class="step-order">步骤 {{ s.step_order }}</span>
              — {{ s.status }}
              <span v-if="s.duration_ms" class="step-duration">{{ s.duration_ms }}ms</span>
            </div>
            <div v-if="s.error_message" class="step-error">{{ s.error_message }}</div>
          </a-timeline-item>
        </a-timeline>
        <SfEmptyState v-else description="无步骤记录" />
      </a-card>

      <!-- 决策日志 -->
      <a-card title="决策日志" class="execution-detail-card ai-card">
        <div v-if="decisions.length">
          <div v-for="d in decisions" :key="d.id" class="decision-item">
            <div class="decision-header">
              <a-tag color="arcoblue" size="small">{{ d.skill_id }}</a-tag>
              <span>{{ d.suggested_action }}</span>
              <a-tag :color="d.approval_status === 'approved' ? 'green' : d.approval_status === 'rejected' ? 'red' : 'gray'" size="small" class="approval-tag">
                {{ d.approval_status || 'pending' }}
              </a-tag>
            </div>
            <div v-if="d.user_action" class="decision-action">用户操作: {{ formatDecisionAction(d.user_action) }}</div>
            <div v-if="d.user_feedback" class="decision-feedback">{{ d.user_feedback }}</div>
            <div v-if="canDecide(d)" class="decision-actions">
              <a-space>
                <a-button type="primary" size="small" status="success" @click="askDecision(d, 'confirm')">
                  <template #icon><SfShellIcon name="check" class="execution-button-icon" /></template>
                  确认执行
                </a-button>
                <a-button size="small" status="danger" @click="askDecision(d, 'reject')">
                  <template #icon><SfShellIcon name="x" class="execution-button-icon" /></template>
                  拒绝
                </a-button>
                <a-button type="outline" size="small" @click="openHelpModal(d)">
                  <template #icon><SfShellIcon name="warn" class="execution-button-icon" /></template>
                  求助
                </a-button>
              </a-space>
            </div>
          </div>
        </div>
        <SfEmptyState v-else description="无决策记录" />
      </a-card>
      </div>
    </template>

    <!-- 求助弹窗 -->
    <a-modal
      v-model:visible="showHelp"
      title="发起求助"
      @ok="sendHelp"
      :ok-loading="helpLoading"
      :ok-button-props="{ disabled: !helpReason }"
    >
      <a-form-item label="求助原因">
        <a-select v-model="helpReason" placeholder="请选择">
          <a-option value="unclear">AI推荐不明确，需要解释</a-option>
          <a-option value="data_question">数据有疑问</a-option>
          <a-option value="authority">超出我的权限范围</a-option>
          <a-option value="other">其他</a-option>
        </a-select>
      </a-form-item>
      <a-form-item label="补充说明">
        <a-textarea v-model="helpNote" placeholder="可选" :auto-size="{ minRows: 2 }" />
      </a-form-item>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { executionApi as rawExecutionApi } from '@/api'
import request from '@/api/request'
import { formatTime } from '@/utils/format'
import { runStatusLabel, runStatusColor } from '@/utils/constants'
import { SfLoadingState, SfEmptyState, SfErrorState } from '@/components/common'
import { confirmAction } from '@/utils/confirmDelete'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

const executionApi: any = rawExecutionApi
const route: any = useRoute()
const runId: string = route.params.id

const loading = ref(false)
const loadError = ref('')
const run = ref<any>({})
const steps = ref<any[]>([])
const decisions = ref<any[]>([])

function formatDecisionAction(action: string) {
  const labels: Record<string, string> = {
    completed: '已确认执行',
    rejected: '已拒绝',
    help_requested: '已发起求助',
    pending: '待处理',
  }
  return labels[action] || action
}

async function loadData() {
  loading.value = true
  loadError.value = ''
  try {
    const [runData, stepsData, decisionsData] = await Promise.all([
      executionApi.getRun(runId),
      executionApi.getSteps(runId),
      executionApi.getDecisions(runId),
    ])
    run.value = runData
    steps.value = stepsData || []
    decisions.value = decisionsData || []
  } catch (e: any) {
    loadError.value = e._message || '加载执行详情失败'
    Message.error(loadError.value)
  } finally {
    loading.value = false
  }
}

function canDecide(d: any): boolean {
  // 执行已完结时，pending 决策不再允许操作（避免和已完结的 run 状态冲突）
  const runClosed = run.value?.status && ['success', 'completed', 'failed', 'cancelled'].includes(run.value.status)
  if (runClosed) return false
  return d.approval_level === 1 && (!d.user_action || d.user_action === 'pending')
}

async function handleDecisionAction(decision: any, action: string) {
  try {
    await request.post(`/executions/runs/${runId}/decisions/${decision.id}/action`, { action })
    Message.success(action === 'confirm' ? '已确认' : '已拒绝')
    await loadData()
  } catch (e: any) {
    Message.error(e._message || '操作失败')
  }
}

function askDecision(d: any, action: 'confirm' | 'reject') {
  if (action === 'confirm') {
    confirmAction({
      title: '确认执行该决策？',
      content: `Skill: ${d.skill_id}\n建议: ${d.suggested_action || '(无)'}\n确认后系统将按建议执行。`,
      okText: '确认执行',
      okStatus: 'success',
      onOk: () => handleDecisionAction(d, 'confirm'),
    })
  } else {
    confirmAction({
      title: '拒绝该决策？',
      content: `Skill: ${d.skill_id}\n拒绝后将不会执行本次建议。`,
      okText: '拒绝',
      okStatus: 'danger',
      onOk: () => handleDecisionAction(d, 'reject'),
    })
  }
}

// 求助功能
const showHelp = ref(false)
const helpReason = ref('')
const helpNote = ref('')
const helpLoading = ref(false)
const helpDecision = ref<any>(null)

function openHelpModal(d: any) {
  helpDecision.value = d
  helpReason.value = ''
  helpNote.value = ''
  showHelp.value = true
}

async function sendHelp() {
  if (!helpReason.value) {
    Message.warning('请选择求助原因')
    return
  }
  helpLoading.value = true
  try {
    await request.post(`/executions/runs/${runId}/decisions/${helpDecision.value.id}/action`, {
      action: 'help',
      reason: helpReason.value,
      note: helpNote.value,
    })
    Message.success('求助已发送给业务负责人')
    showHelp.value = false
    await loadData()
  } catch (e: any) {
    Message.error(e._message || '发送失败')
  } finally {
    helpLoading.value = false
  }
}

onMounted(loadData)
</script>

<style scoped>
.execution-detail-page {
  min-height: 100%;
}
.execution-detail-head {
  align-items: center;
  justify-content: flex-start;
}
.execution-detail-head .ai-btn svg,
.execution-button-icon {
  width: 14px;
  height: 14px;
  flex: 0 0 auto;
}
.execution-title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.execution-detail-body {
  min-width: 0;
}
.execution-detail-card {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}
.step-order { margin-left: 8px; color: var(--ai-ink-3); }
.step-duration { margin-left: 8px; color: var(--ai-ink-4); }
.step-error { font-size: 13px; color: var(--ai-bad); margin-top: 4px; }
.approval-tag { margin-left: 6px; }
/* webkit 1px dashed → solid 渲染 bug，用 background gradient pattern 代替 */
.decision-item {
  padding: 9px 0;
  background-image: linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%);
  background-position: bottom;
  background-size: 6px 1px;
  background-repeat: repeat-x;
}
.decision-header { display: flex; align-items: center; gap: 6px; }
.decision-action { font-size: 13px; color: var(--ai-ink-3); margin-top: 4px; padding-left: 8px; }
.decision-actions { margin-top: 8px; }
.decision-feedback {
  white-space: pre-wrap;
  font-size: 13px;
  color: var(--ai-ink-2);
  margin-top: 4px;
  padding-left: 8px;
}
</style>
