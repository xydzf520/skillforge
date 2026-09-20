<template>
  <section v-if="jobs.length" class="tt-training-card ai-card" data-testid="tasktree-training-summary">
    <div class="ai-card-h tt-training-title">
      <span class="t">训练任务</span>
      <span class="s">{{ jobs.length }} 条</span>
    </div>

    <div class="tt-training-list">
      <article v-for="job in jobs" :key="job.job_id || `${job.status}-${job.source_run_id}`" class="tt-training-item">
        <div class="tt-training-head">
          <div class="tt-training-name">
            <strong>{{ job.title || job.model_family || job.target_skill_id || '训练任务' }}</strong>
            <a-tag size="small" :color="trainingStatusColor(job.status)">{{ trainingStatusLabel(job.status) }}</a-tag>
          </div>
          <code v-if="job.job_id" class="tt-training-id">{{ job.job_id }}</code>
        </div>

        <div class="tt-training-meta">
          <span v-if="job.target_skill_id">Skill {{ job.target_skill_id }}</span>
          <span v-if="job.target_gateway_id">节点 {{ job.target_gateway_id }}</span>
          <span v-if="job.model_family">模型 {{ job.model_family }}</span>
          <span v-if="job.training_mode">{{ modeLabel(job.training_mode) }}</span>
          <span v-if="job.full_history_cycle_index">全量三轮 {{ job.full_history_cycle_index }}/{{ job.full_history_cycle_count || 3 }}</span>
          <span v-if="job.automation_step">自动化 {{ job.automation_step }}</span>
        </div>

        <div class="tt-training-grid">
          <div>
            <span>样本</span>
            <strong>{{ job.sample_count ?? job.source_sample_count ?? 0 }}</strong>
          </div>
          <div>
            <span>训练/评估</span>
            <strong>{{ job.train_count ?? '-' }}/{{ job.eval_count ?? 0 }}</strong>
          </div>
          <div>
            <span>进度</span>
            <strong>{{ formatProgress(job.latest_task_progress) }}</strong>
          </div>
          <div>
            <span>部署</span>
            <strong>{{ deploymentLabel(job) }}</strong>
          </div>
        </div>

        <div class="tt-training-detail">
          <span v-if="job.dataset_window_date">数据窗口 {{ job.dataset_window_date }}</span>
          <span v-if="job.planned_training_after">计划训练 {{ formatTime(job.planned_training_after) }}</span>
          <span v-if="job.dataset_ref" class="tt-training-ref">{{ job.dataset_ref }}</span>
          <span v-if="job.parent_model_deployment_id">基于 {{ job.parent_model_deployment_id }}</span>
          <span v-if="job.approved_by">审核 {{ job.approved_by }}</span>
          <span v-if="job.updated_at">更新 {{ formatTime(job.updated_at) }}</span>
        </div>

        <div v-if="job.failure_stage || job.latest_task_error" class="tt-training-error">
          <span v-if="job.failure_stage">阶段 {{ job.failure_stage }}</span>
          <span>{{ job.latest_task_error || '-' }}</span>
        </div>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { formatTime } from '@/utils/format'
import type { TaskTreeTrainingJobSummaryItem } from './types'

defineProps<{
  jobs: TaskTreeTrainingJobSummaryItem[]
}>()

function trainingStatusLabel(status?: string): string {
  if (status === 'pending_daily_training') return '待日批训练'
  if (status === 'awaiting_review') return '待审核'
  if (status === 'queued') return '排队中'
  if (status === 'running') return '训练中'
  if (status === 'evaluating') return '评估中'
  if (status === 'completed') return '已完成'
  if (status === 'failed') return '失败'
  if (status === 'cancelled') return '已取消'
  return status || '-'
}

function trainingStatusColor(status?: string): string {
  if (status === 'pending_daily_training' || status === 'awaiting_review' || status === 'queued') return 'orange'
  if (status === 'running' || status === 'evaluating') return 'blue'
  if (status === 'completed') return 'green'
  if (status === 'failed' || status === 'cancelled') return 'red'
  return 'gray'
}

function modeLabel(mode?: string | null): string {
  if (mode === 'daily_incremental') return '每日增量'
  if (mode === 'full_history_incremental') return '全量微调'
  return mode || ''
}

function formatProgress(value?: number | null): string {
  if (value == null) return '-'
  const numeric = value <= 1 ? value * 100 : value
  return `${Math.max(0, Math.min(100, numeric)).toFixed(0)}%`
}

function deploymentLabel(job: TaskTreeTrainingJobSummaryItem): string {
  if (job.deployment_id) {
    return `${job.deployment_status || 'review'} ${job.rollout_percent ?? 0}%`
  }
  return '-'
}
</script>

<style scoped>
.tt-training-card {
  min-width: 0;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  box-shadow: none;
  overflow: hidden;
}

.tt-training-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 500;
}
.tt-training-title .s {
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-4);
}

.tt-training-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
}

.tt-training-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
}

.tt-training-head,
.tt-training-name,
.tt-training-meta,
.tt-training-detail,
.tt-training-error {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
}

.tt-training-head {
  justify-content: space-between;
}

.tt-training-name strong {
  max-width: 520px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 500;
}

.tt-training-id,
.tt-training-ref {
  font-family: var(--ai-font-mono);
  font-size: 11px;
  color: var(--ai-ink-4);
}

.tt-training-meta,
.tt-training-detail {
  color: var(--ai-ink-4);
  font-size: 11.5px;
}

.tt-training-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.tt-training-grid div {
  min-width: 0;
  padding: 8px;
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}

.tt-training-grid span {
  display: block;
  margin-bottom: 3px;
  color: var(--ai-ink-4);
  font-size: 11px;
}

.tt-training-grid strong {
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 12px;
  font-weight: 600;
}

.tt-training-error {
  padding: 8px 10px;
  border-radius: var(--ai-radius-s);
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  font-size: 12px;
}

@media (max-width: 720px) {
  .tt-training-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
