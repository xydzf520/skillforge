<template>
  <section class="tt-schedule-panel" data-testid="tasktree-schedules">
    <TaskTreeTrainingSummary
      v-if="trainingJobs.length"
      :jobs="trainingJobs"
      class="tt-schedule-training"
    />

    <div class="tt-schedule-head">
      <div>
        <span class="t">定时任务</span>
        <span class="s">{{ scheduleSubtitle }}</span>
      </div>
    </div>

    <div v-if="loading" class="tt-schedule-loading">
      <a-spin />
    </div>

    <div v-else-if="!jobs.length" class="tt-schedule-empty">
      <SfShellIcon name="clock" class="tt-schedule-empty-icon" />
      <div class="tt-schedule-empty-title">该节点暂无定时任务</div>
      <div class="tt-schedule-empty-hint">没有已下发的 cron 配置。</div>
    </div>

    <div v-else class="sched-list">
      <article
        v-for="job in jobs"
        :key="job.skill_id"
        class="sched-item"
        :class="{ 'sched-item--failing': job.total_runs > 0 && job.success_count === 0 }"
      >
        <div class="sched-item-head">
          <div class="sched-title-block">
            <div class="sched-title-row">
              <span class="sched-name">{{ job.skill_name || job.skill_id }}</span>
              <span v-if="nameClash" class="sched-skill-id" :title="job.skill_id">{{ skillIdSuffix(job.skill_id) }}</span>
              <span class="sched-status" :class="`sched-status--${statusTone(job.status)}`">{{ statusLabel(job.status) }}</span>
            </div>
            <div class="sched-subline">
              <code v-if="job.cron_expression" class="sched-cron">{{ job.cron_expression }}</code>
              <span v-if="cronHint(job.cron_expression)" class="sched-cron-hint">{{ cronHint(job.cron_expression) }}</span>
              <span v-else class="sched-muted">未配置 cron</span>
            </div>
          </div>

          <div class="sched-actions">
            <template v-if="job.status === 'active' && job.cron_expression">
              <button type="button" class="ai-btn sm sched-action sched-action--warn" @click="doAction(job.skill_id, 'stop')">
                <SfShellIcon name="x" />
                停止
              </button>
              <button type="button" class="ai-btn sm sched-action" @click="openCronEdit(job)">
                <SfShellIcon name="edit" />
                修改
              </button>
            </template>
            <template v-else-if="job.status === 'active' && !job.cron_expression">
              <button type="button" class="ai-btn sm primary sched-action" @click="openCronEdit(job)">
                <SfShellIcon name="edit" />
                设置调度
              </button>
            </template>
            <template v-else-if="canStartSchedule(job)">
              <button type="button" class="ai-btn sm primary sched-action" @click="openCronEdit(job)">
                <SfShellIcon name="play" />
                启动
              </button>
            </template>
          </div>
        </div>

        <div v-if="job.total_runs > 0 && job.success_count === 0" class="sched-alert">
          <span class="sched-status sched-status--bad">全部失败</span>
          <p>该任务 {{ job.total_runs }} 次执行全部失败，建议暂停排查。</p>
        </div>

        <div v-if="nextRuns(job.cron_expression).length" class="sched-info-row">
          <span class="sched-row-label">下次执行</span>
          <span class="sched-next-runs">
            <span
              v-for="(d, idx) in nextRuns(job.cron_expression)"
              :key="idx"
              class="sched-next-chip"
              :class="{ active: idx === 0 }"
            >{{ formatNextRun(d) }}</span>
          </span>
        </div>

        <div v-if="gitCurrentShort(job) || gitDeployedShort(job)" class="sched-info-row">
          <span class="sched-row-label">Git</span>
          <span class="sched-git-cell">
            <a-tooltip :content="gitVersionTooltip(job)" mini>
              <span class="sched-git-row">
                <code class="sched-git" :class="`sched-git--${gitVersionState(job)}`">
                  {{ gitPrimaryLabel(job) }}
                </code>
                <code v-if="gitShowCurrent(job)" class="sched-git sched-git--current">
                  当前 {{ gitCurrentShort(job) }}
                </code>
                <span v-else-if="!gitHasKnownDeployed(job)" class="sched-status sched-status--warn">
                  节点版本未知
                </span>
              </span>
            </a-tooltip>
            <button
              v-if="canSyncVersion(job)"
              type="button"
              class="ai-btn sm sched-action"
              :disabled="isSyncingVersion(job)"
              @click="syncVersionToNode(job)"
            >
              <SfShellIcon name="refresh" />
              {{ isSyncingVersion(job) ? '同步中' : '同步版本' }}
            </button>
            <span v-if="job.sync_version_tag" class="sched-status sched-status--info">
              {{ job.sync_version_tag }}
            </span>
          </span>
        </div>

        <div class="sched-metrics">
          <div class="sched-metric">
            <span>总执行</span>
            <strong>{{ job.total_runs }}</strong>
          </div>
          <div class="sched-metric">
            <span>成功率</span>
            <strong v-if="job.total_runs" :class="rateClass(job)">{{ successRate(job) }}</strong>
            <strong v-else class="sched-muted">--</strong>
          </div>
          <div class="sched-metric">
            <span>成功</span>
            <strong class="sched-ok">{{ job.success_count }}</strong>
          </div>
          <div class="sched-metric">
            <span>失败</span>
            <strong :class="job.failed_count > 0 ? 'sched-fail' : ''">{{ job.failed_count }}</strong>
          </div>
          <div class="sched-metric">
            <span>输出项</span>
            <strong>{{ job.total_output_items }}</strong>
          </div>
          <div class="sched-metric">
            <span>平均耗时</span>
            <strong>{{ formatDur(job.avg_duration_seconds, job.success_count === 0 && job.total_runs > 0) }}</strong>
          </div>
          <div class="sched-metric sched-metric--wide">
            <span>最近执行</span>
            <template v-if="job.last_run_at">
              <strong>{{ formatTime(job.last_run_at) }}</strong>
              <span class="sched-last-status" :class="`sched-status--${lastStatusTone(job.last_status)}`">{{ lastStatusLabel(job.last_status) }}</span>
            </template>
            <strong v-else class="sched-muted">暂无执行记录</strong>
          </div>
        </div>

        <div v-if="job.total_runs > 0" class="sched-bar-wrap">
          <div class="sched-bar">
            <div class="sched-bar-ok" :style="{ width: barWidth(job, 'ok') }" />
            <div v-if="job.failed_count" class="sched-bar-fail" :style="{ width: barWidth(job, 'fail') }" />
          </div>
        </div>
      </article>
    </div>

    <a-modal v-model:visible="cronModalVisible" :title="cronModalTitle" :width="'min(90vw, 480px)'" @ok="submitCronEdit" :ok-loading="actionLoading">
      <a-form :model="cronForm" layout="vertical" size="small">
        <a-form-item label="Cron 表达式" required>
          <a-input v-model="cronForm.cron" placeholder="*/5 * * * *" class="sched-cron-input" />
        </a-form-item>
        <div class="sched-cron-presets">
          <span class="sched-preset-label">快捷设置：</span>
          <a-button v-for="p in PRESETS" :key="p.cron" size="mini" type="text" @click="cronForm.cron = p.cron">{{ p.label }}</a-button>
        </div>
        <div v-if="cronHint(cronForm.cron)" class="sched-cron-preview">
          {{ cronHint(cronForm.cron) }}
        </div>
        <div v-if="editNextRuns.length" class="sched-next-preview">
          <div class="sched-next-preview-title">接下来 {{ editNextRuns.length }} 次执行：</div>
          <div class="sched-next-preview-list">
            <span v-for="(d, idx) in editNextRuns" :key="idx" class="sched-next-preview-item">
              {{ formatNextRun(d) }}
            </span>
          </div>
        </div>
      </a-form>
    </a-modal>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { bjtDateString, formatTime, formatTimeOnly } from '@/utils/format'
import { getNextRuns } from '@/utils/cronNext'
import { Message, Modal } from '@arco-design/web-vue'
import request from '@/api/request'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'
import TaskTreeTrainingSummary from '../TaskTreeTrainingSummary.vue'
import type { TaskTreeTrainingJobSummaryItem } from '../types'

type ScheduledJob = {
  skill_id: string
  skill_name?: string
  cron_expression?: string
  status?: string
  total_runs: number
  success_count: number
  failed_count: number
  last_run_at?: string
  last_status?: string
  avg_duration_seconds?: number
  total_output_items: number
  current_version?: string
  git_commit?: string
  git_commit_full?: string
  deployed_git_commit?: string
  deployed_git_commit_full?: string
  docker_git_commit?: string
  docker_git_commit_full?: string
  sync_version_tag?: string
  sync_completed_at?: string
  git_deploy_recorded?: boolean
}

type SyncSkillResponse = {
  job_id?: number
  ok?: boolean
  total?: number
  success?: number
  results?: Array<{
    instance_id?: string
    ok?: boolean
    error?: string | null
  }>
}

const PRESETS = [
  { label: '每 5 分钟', cron: '*/5 * * * *' },
  { label: '每小时', cron: '0 * * * *' },
  { label: '每天 8:00', cron: '0 8 * * *' },
  { label: '每天 20:00', cron: '0 20 * * *' },
  { label: '工作日 9:00', cron: '0 9 * * 1-5' },
]

const props = defineProps<{
  instanceId: string | null
  trainingJobs?: TaskTreeTrainingJobSummaryItem[]
}>()

const loading = ref(false)
const actionLoading = ref(false)
const syncingSkillIds = ref<Set<string>>(new Set())
const jobs = ref<ScheduledJob[]>([])
const trainingJobs = computed(() => props.trainingJobs || [])
const scheduleSubtitle = computed(() => jobs.value.length ? `${jobs.value.length} 个任务` : '该节点暂无定时任务')

const cronModalVisible = ref(false)
const cronModalTitle = ref('')
const cronForm = reactive<{ cron: string }>({ cron: '' })
const cronEditSkillId = ref('')
const cronEditAction = ref<'start' | 'update'>('start')

// 同名 Skill 检测
const nameClash = computed(() => {
  const names = jobs.value.map((j) => j.skill_name || j.skill_id)
  return new Set(names).size < names.length
})

function skillIdSuffix(skillId: string): string {
  // 取 skill_id 末尾有辨识度的部分
  const parts = skillId.split('-')
  if (parts.length > 2) {
    const last = parts[parts.length - 1]
    // 如果是日期后缀（如 20260423）或 v2 这种版本号
    if (/^\d{8}$/.test(last) || /^v\d/.test(last)) return last
    // 取末尾有意义的两段
    return parts.slice(-2).join('-')
  }
  return skillId.length > 20 ? skillId.slice(-16) : skillId
}

function nextRuns(cronExpr?: string): Date[] {
  if (!cronExpr) return []
  try {
    return getNextRuns(cronExpr, 3)
  } catch {
    return []
  }
}

const editNextRuns = computed(() => {
  const v = cronForm.cron.trim()
  if (!v || v.split(/\s+/).length !== 5) return []
  try {
    return getNextRuns(v, 3)
  } catch {
    return []
  }
})

function formatNextRun(d: Date): string {
  const nowKey = bjtDateString(Date.now())
  const targetKey = bjtDateString(d)
  const time = formatTimeOnly(d)
  // 同一天只显示时间
  if (targetKey === nowKey) {
    return `今天 ${time}`
  }
  if (targetKey === bjtDateString(Date.now(), 1)) {
    return `明天 ${time}`
  }
  return `${targetKey.slice(5)} ${time}`
}

function shortCommit(value: unknown): string {
  const text = String(value || '').trim()
  if (!text) return ''
  return text.length > 8 ? text.slice(0, 8) : text
}

function gitCurrentFull(job: ScheduledJob): string {
  return String(job.git_commit_full || (String(job.git_commit || '').length > 8 ? job.git_commit : '') || '').trim()
}

function gitDeployedFull(job: ScheduledJob): string {
  return String(job.deployed_git_commit_full || job.docker_git_commit_full || '').trim()
}

function gitCurrentShort(job: ScheduledJob): string {
  return String(job.git_commit || shortCommit(gitCurrentFull(job)) || '').trim()
}

function gitDeployedShort(job: ScheduledJob): string {
  return String(job.deployed_git_commit || job.docker_git_commit || shortCommit(gitDeployedFull(job)) || '').trim()
}

function gitHasKnownDeployed(job: ScheduledJob): boolean {
  return !!gitDeployedShort(job)
}

function gitPrimaryLabel(job: ScheduledJob): string {
  const deployed = gitDeployedShort(job)
  if (deployed) return `节点 ${deployed}`
  const current = gitCurrentShort(job)
  return current ? `当前 ${current}` : '-'
}

function gitShowCurrent(job: ScheduledJob): boolean {
  const deployedFull = gitDeployedFull(job)
  const currentFull = gitCurrentFull(job)
  const deployed = gitDeployedShort(job)
  const current = gitCurrentShort(job)
  if (!deployed || !current) return false
  if (deployedFull && currentFull) return deployedFull !== currentFull
  return deployed !== current
}

function gitVersionState(job: ScheduledJob): 'ok' | 'stale' | 'unknown' {
  const deployed = gitDeployedFull(job)
  const current = gitCurrentFull(job)
  if (deployed && current && deployed !== current) return 'stale'
  if (!deployed && gitCurrentShort(job)) return 'unknown'
  return 'ok'
}

function gitVersionTooltip(job: ScheduledJob): string {
  const lines: string[] = []
  const deployed = gitDeployedFull(job)
  const current = gitCurrentFull(job)
  if (deployed) lines.push(`节点同步 commit: ${deployed}`)
  else if (current) lines.push(`当前 Skill Git commit: ${current}`)
  if (current && deployed && current !== deployed) lines.push(`当前仓库 commit: ${current}`)
  if (job.sync_version_tag) lines.push(`发布标签: ${job.sync_version_tag}`)
  if (job.sync_completed_at) lines.push(`同步时间: ${formatTime(job.sync_completed_at)}`)
  if (job.current_version) lines.push(`业务版本: ${job.current_version}`)
  if (!deployed) lines.push('旧同步记录未保存节点 commit，不能确认节点实际版本')
  return lines.join('\n')
}

function canSyncVersion(job: ScheduledJob): boolean {
  return !!props.instanceId && !!gitCurrentShort(job) && (!gitHasKnownDeployed(job) || gitShowCurrent(job))
}

function isSyncingVersion(job: ScheduledJob): boolean {
  return syncingSkillIds.value.has(job.skill_id)
}

function setSyncingVersion(skillId: string, syncing: boolean) {
  const next = new Set(syncingSkillIds.value)
  if (syncing) next.add(skillId)
  else next.delete(skillId)
  syncingSkillIds.value = next
}

async function load() {
  if (!props.instanceId) return
  loading.value = true
  try {
    const res = await request.get<{ jobs: ScheduledJob[] }>(`/task-tree/node/${props.instanceId}/schedules`)
    jobs.value = res.jobs || []
  } catch {
    jobs.value = []
  } finally {
    loading.value = false
  }
}

async function syncVersionToNode(job: ScheduledJob) {
  const instanceId = props.instanceId
  if (!instanceId || !job.skill_id) return
  setSyncingVersion(job.skill_id, true)
  try {
    const res = await request.post<SyncSkillResponse>(
      `/aiclaw/instances/${encodeURIComponent(instanceId)}/sync-skill/${encodeURIComponent(job.skill_id)}`,
      {},
      { timeout: 180000 },
    )
    if (res.ok) {
      Message.success(`已同步 ${job.skill_name || job.skill_id} 到节点`)
    } else {
      const failed = res.results?.find((item) => !item.ok)
      Message.error(failed?.error ? `同步失败：${failed.error}` : '同步失败')
    }
    await load()
  } catch (e: any) {
    Message.error(e?._message || '同步失败')
  } finally {
    setSyncingVersion(job.skill_id, false)
  }
}

function openCronEdit(job: ScheduledJob) {
  cronEditSkillId.value = job.skill_id
  cronForm.cron = job.cron_expression || ''
  cronEditAction.value = job.cron_expression ? 'update' : 'start'
  cronModalTitle.value = job.cron_expression
    ? `修改调度 - ${job.skill_name || job.skill_id}`
    : `启动调度 - ${job.skill_name || job.skill_id}`
  cronModalVisible.value = true
}

async function submitCronEdit() {
  if (!cronForm.cron.trim()) {
    Message.warning('请输入 cron 表达式')
    return
  }
  actionLoading.value = true
  try {
    const res = await request.put<{ message: string }>(`/task-tree/skill/${cronEditSkillId.value}/schedule`, {
      action: cronEditAction.value,
      cron_expression: cronForm.cron.trim(),
    })
    Message.success(res.message || '操作成功')
    cronModalVisible.value = false
    await load()
  } catch (e: any) {
    Message.error(e?._message || '操作失败')
  } finally {
    actionLoading.value = false
  }
}

function doAction(skillId: string, action: 'stop') {
  Modal.confirm({
    title: '确认停止定时任务',
    content: '停止后该 Skill 不再自动执行，可随时重新启动',
    okText: '停止',
    okButtonProps: { status: 'warning' } as any,
    async onOk() {
      try {
        const res = await request.put<{ message: string }>(`/task-tree/skill/${skillId}/schedule`, { action })
        Message.success(res.message || '已停止')
        await load()
      } catch (e: any) {
        Message.error(e?._message || '操作失败')
      }
    },
  })
}

function canStartSchedule(job: ScheduledJob): boolean {
  return ['stopped', 'not_scheduled', 'sync_failed'].includes(String(job.status || ''))
}

function statusTone(s?: string) {
  if (s === 'active') return 'ok'
  if (s === 'sync_failed') return 'bad'
  if (s === 'pending' || s === 'not_scheduled') return 'warn'
  if (s === 'stopped' || s === 'deleted' || s === 'archived') return 'neutral'
  return 'warn'
}
function statusLabel(s?: string) {
  if (s === 'active') return '已启用'
  if (s === 'deleted') return '已删除'
  if (s === 'stopped') return '已停止'
  if (s === 'archived') return '已归档'
  if (s === 'draft') return '草稿'
  if (s === 'pending') return '下发中'
  if (s === 'sync_failed') return '下发失败'
  if (s === 'not_scheduled') return '未下发'
  return s || '未知'
}
function lastStatusTone(s?: string) {
  if (s === 'completed') return 'ok'
  if (s === 'failed' || s === 'timeout') return 'bad'
  if (s === 'running') return 'info'
  return 'neutral'
}
function lastStatusLabel(s?: string) {
  if (s === 'completed') return '成功'
  if (s === 'failed') return '失败'
  if (s === 'timeout') return '超时'
  if (s === 'running') return '运行中'
  return s || '-'
}
function formatDur(sec?: number, allFailed = false) {
  if (sec == null) return '--'
  if (allFailed) return `${sec < 1 ? '<1s' : sec < 60 ? `${Math.round(sec)}s` : `${Math.floor(sec / 60)}m${Math.round(sec % 60)}s`}（全部失败）`
  if (sec < 1) return '<1s'
  if (sec < 60) return `${Math.round(sec)}s`
  return `${Math.floor(sec / 60)}m${Math.round(sec % 60)}s`
}
function successRate(job: ScheduledJob) {
  if (!job.total_runs) return '--'
  return `${Math.round((job.success_count / job.total_runs) * 100)}%`
}
function rateClass(job: ScheduledJob) {
  if (!job.total_runs) return ''
  const rate = job.success_count / job.total_runs
  if (rate >= 0.9) return 'sched-ok'
  if (rate >= 0.6) return 'sched-warn'
  return 'sched-fail'
}
function barWidth(job: ScheduledJob, type: 'ok' | 'fail') {
  if (!job.total_runs) return '0%'
  const n = type === 'ok' ? job.success_count : job.failed_count
  return `${Math.round((n / job.total_runs) * 100)}%`
}
function cronHint(expr?: string) {
  if (!expr) return ''
  const p = expr.trim().split(/\s+/)
  if (p.length < 5) return ''
  const [min, hour, dom, mon, dow] = p
  if (min === '*' && hour === '*') return '每分钟'
  if (min.includes('/')) return `每 ${min.split('/')[1]} 分钟`
  if (hour === '*') return `每小时第 ${min} 分`
  if (dom === '*' && mon === '*' && dow === '*') return `每天 ${hour}:${min.padStart(2, '0')}`
  if (dow === '1-5') return `工作日 ${hour}:${min.padStart(2, '0')}`
  if (dow !== '*' && dom === '*' && mon === '*') {
    const dowMap: Record<string, string> = { '0': '周日', '1': '周一', '2': '周二', '3': '周三', '4': '周四', '5': '周五', '6': '周六', '7': '周日' }
    const days = dow.split(',').map((d) => dowMap[d.trim()] || d).join(',')
    return `${days} ${hour}:${min.padStart(2, '0')}`
  }
  return ''
}

watch(() => props.instanceId, load)
onMounted(load)
</script>

<style scoped>
.tt-schedule-panel {
  box-sizing: border-box;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tt-schedule-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 32px;
}

.tt-schedule-head > div {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.tt-schedule-head .t {
  color: var(--ai-ink-1);
  font-size: 15px;
  font-weight: 600;
}

.tt-schedule-head .s {
  color: var(--ai-ink-4);
  font-size: 12px;
}

.tt-schedule-loading {
  display: grid;
  place-items: center;
  min-height: 120px;
}

.tt-schedule-empty {
  display: flex;
  min-height: 160px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 1px dashed var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  color: var(--ai-ink-3);
  text-align: center;
}

.tt-schedule-empty-icon {
  width: 22px;
  height: 22px;
  color: var(--ai-ink-4);
}

.tt-schedule-empty-title {
  color: var(--ai-ink-2);
  font-size: 13px;
  font-weight: 600;
}

.tt-schedule-empty-hint {
  color: var(--ai-ink-4);
  font-size: 12px;
}

.sched-list {
  box-sizing: border-box;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.sched-item {
  box-sizing: border-box;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
}

.sched-item--failing {
  border-color: color-mix(in srgb, var(--ai-bad) 45%, var(--ai-border));
  background: var(--ai-bad-soft);
}

.sched-item-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
  flex-wrap: wrap;
}

.sched-title-block {
  flex: 1 1 260px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.sched-title-row,
.sched-subline,
.sched-actions,
.sched-info-row,
.sched-git-cell,
.sched-git-row,
.sched-next-runs {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  flex-wrap: wrap;
}

.sched-name {
  min-width: 0;
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.sched-skill-id {
  max-width: 160px;
  overflow: hidden;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-family: var(--ai-font-mono);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sched-status,
.sched-next-chip,
.sched-last-status {
  display: inline-flex;
  align-items: center;
  min-height: 20px;
  padding: 2px 7px;
  border: 1px solid transparent;
  border-radius: 4px;
  font-size: 11.5px;
  font-weight: 500;
  line-height: 1.2;
  white-space: nowrap;
}

.sched-status--neutral,
.sched-next-chip,
.sched-last-status {
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
}

.sched-status--ok,
.sched-next-chip.active,
.sched-last-status.sched-status--ok {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
}

.sched-status--bad,
.sched-last-status.sched-status--bad {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}

.sched-status--warn,
.sched-last-status.sched-status--warn {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
}

.sched-status--info,
.sched-last-status.sched-status--info {
  background: var(--ai-info-soft);
  color: var(--ai-info);
}

.sched-cron,
.sched-git {
  padding: 2px 7px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.sched-cron-hint,
.sched-row-label,
.sched-muted {
  color: var(--ai-ink-4);
  font-size: 12px;
}

.sched-row-label {
  width: 64px;
  flex: 0 0 64px;
}

.sched-actions {
  flex: 0 1 auto;
  justify-content: flex-end;
}

.sched-action {
  flex: 0 0 auto;
  white-space: nowrap;
}

.sched-action svg {
  width: 12px;
  height: 12px;
}

.sched-action--warn {
  color: var(--ai-warn);
}

.sched-alert {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding: 8px 10px;
  border: 1px solid color-mix(in srgb, var(--ai-bad) 35%, var(--ai-border));
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface);
}

.sched-alert p {
  margin: 0;
  color: var(--ai-ink-2);
  font-size: 12px;
}

.sched-info-row {
  align-items: flex-start;
  margin-top: 10px;
}

.sched-git-cell,
.sched-next-runs {
  flex: 1 1 0;
}

.sched-git-row {
  flex: 1 1 auto;
}

.sched-git--stale {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
}

.sched-git--unknown {
  color: var(--ai-ink-4);
}

.sched-git--current {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
}

.sched-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));
  gap: 8px;
  margin-top: 12px;
}

.sched-metric {
  min-width: 0;
  padding: 8px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
}

.sched-metric--wide {
  grid-column: span 2;
}

.sched-metric span {
  display: block;
  color: var(--ai-ink-4);
  font-size: 11px;
  line-height: 1.2;
}

.sched-metric strong {
  display: inline-block;
  max-width: 100%;
  margin-top: 4px;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  overflow-wrap: anywhere;
}

.sched-metric strong.sched-muted {
  color: var(--ai-ink-4);
}

.sched-last-status {
  margin-top: 5px;
}

.sched-ok { color: var(--ai-ok) !important; }
.sched-fail { color: var(--ai-bad) !important; }
.sched-warn { color: var(--ai-warn) !important; }

.sched-bar-wrap {
  margin-top: 10px;
}

.sched-bar {
  display: flex;
  height: 5px;
  overflow: hidden;
  border-radius: 99px;
  background: var(--ai-surface-3);
}

.sched-bar-ok {
  border-radius: inherit;
  background: var(--ai-ok);
}

.sched-bar-fail {
  background: var(--ai-bad);
}

.sched-cron-input {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

.sched-cron-presets {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  margin-top: 4px;
}

.sched-preset-label {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 450;
}

.sched-cron-preview {
  margin-top: 8px;
  padding: 8px 12px;
  border-radius: var(--ai-radius-s);
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  font-size: 12.5px;
  font-weight: 500;
}

.sched-next-preview {
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
}

.sched-next-preview-title {
  margin-bottom: 6px;
  color: var(--ai-ink-3);
  font-size: 11.5px;
  font-weight: 500;
}

.sched-next-preview-list {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.sched-next-preview-item {
  padding: 2px 8px;
  border: 1px solid var(--ai-border);
  border-radius: 3px;
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}

@media (max-width: 1180px) {
  .sched-metrics {
    grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));
  }
}

@media (max-width: 720px) {
  .sched-item-head {
    flex-direction: column;
  }
  .sched-actions {
    width: 100%;
    justify-content: flex-start;
  }
  .sched-row-label {
    width: 100%;
    flex-basis: 100%;
  }
  .sched-metrics {
    grid-template-columns: repeat(auto-fit, minmax(126px, 1fr));
  }
  .sched-metric--wide {
    grid-column: 1 / -1;
  }
}

@media (max-width: 420px) {
  .sched-metrics {
    grid-template-columns: 1fr;
  }
}
</style>
