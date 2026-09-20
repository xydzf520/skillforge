<template>
  <div class="task-tree-page ai-main" :class="{ compact: compactView }">
    <TaskTreeHeader
      :dashboard="dashboard"
      :projected-at="projectedAt"
      :etag="etag"
      :refreshing="refreshing"
      :load-error="loadError"
      :loading="treeLoading && !tree"
      :next-refresh-seconds="nextRefreshCountdown"
      :anomaly-count="anomalyInstances.length"
      @refresh="refreshFromHeader"
      @copy-offline="copyOfflineList"
    />

    <div class="tt-status-tabs ai-tabs" role="tablist" aria-label="任务树视图">
      <button
        type="button"
        class="tt-status-tab ai-tab"
        :class="{ active: statusView === 'all' }"
        role="tab"
        :aria-selected="statusView === 'all'"
        @click="selectStatusView('all')"
      >
        全部机器 <span class="tt-tab-count muted">{{ totalMachineCount }}</span>
      </button>
      <button
        type="button"
        class="tt-status-tab ai-tab"
        :class="{ active: statusView === 'running' }"
        role="tab"
        :aria-selected="statusView === 'running'"
        @click="selectStatusView('running')"
      >
        运行中 <span class="tt-tab-count muted">{{ dashboard.totalRunning ?? 0 }}</span>
      </button>
      <button
        type="button"
        class="tt-status-tab ai-tab"
        :class="{ active: statusView === 'anomaly' }"
        role="tab"
        :aria-selected="statusView === 'anomaly'"
        @click="selectStatusView('anomaly')"
      >
        异常 <span class="tt-tab-pill bad">{{ anomalyInstances.length }}</span>
      </button>
      <button
        type="button"
        class="tt-status-tab ai-tab"
        :class="{ active: statusView === 'completed' }"
        role="tab"
        :aria-selected="statusView === 'completed'"
        @click="selectStatusView('completed')"
      >
        已完成 <span class="tt-tab-pill ok">{{ completedCountValue }}</span>
      </button>
    </div>

    <div class="task-tree-body ai-pagebody">
      <div v-if="treeLoading && !tree" class="tt-skeleton">
        <a-skeleton :loading="true" animation>
          <a-card class="tt-skeleton-card" />
        </a-skeleton>
        <a-skeleton :loading="true" animation>
          <a-card class="tt-skeleton-card" />
        </a-skeleton>
      </div>

      <div
        v-else-if="!displayTree?.departments?.length"
        class="tt-empty-state"
        data-testid="tasktree-empty"
      >
        <a-empty :description="emptyDescription">
          <template #image>
            <div class="tt-empty-icon"><SfShellIcon name="tree" /></div>
          </template>
          <template v-if="emptyActionLabel" #default>
            <a-button size="small" type="outline" @click="handleEmptyAction">
              {{ emptyActionLabel }}
            </a-button>
          </template>
        </a-empty>
      </div>

      <div v-else class="tt-split">
      <!-- 左栏：节点列表 -->
      <div class="tt-split-left">
        <TaskTreeFilterBar
          v-model:show-anomaly-only="showAnomalyOnly"
          v-model:window="window"
          v-model:department="departmentFilter"
          v-model:name-query="nameQuery"
          v-model:compact-view="compactView"
          :can-switch-department="canSwitchDepartment"
          :department-options="departmentOptions"
        />
        <div class="tt-dashboard-main">
          <TaskTreeDeptSection
            v-for="department in displayTree?.departments || []"
            :key="department.department_id"
            :department="department"
            :window="window"
            :compact="compactView"
            :collapsed="isCollapsed(department.department_name)"
            :selected-instance-id="selectedInstanceId"
            :selected-range="selectedRange"
            @toggle="toggleDepartment"
            @select-instance="selectInstance"
            @open-diagnosis="openDiagnosis"
            @focus-bucket="focusBucket"
          />
        </div>
      </div>

      <!-- 右栏：详情面板 -->
      <div class="tt-split-right">
        <div class="tt-detail-panel">
          <div v-if="hasSelection" class="tt-panel-header" data-testid="tasktree-detail">
            <div class="tt-panel-identity">
              <span class="tt-panel-dot" :class="selectedInstanceDetail?.node_status || 'offline'" />
              <div class="tt-panel-copy">
                <strong>{{ selectedInstanceDetail?.name || '节点详情' }}</strong>
                <span class="tt-panel-meta">
                  {{ selectedInstanceDetail?.department || '平台节点' }}
                  <template v-if="selectedInstanceDetail?.instance_id"> · {{ selectedInstanceDetail.instance_id }}</template>
                  <template v-if="selectedInstanceDetail"> · {{ runtimeTypeText(selectedInstanceDetail) }}</template>
                  <template v-if="selectedInstanceDetail?.bridge_version"> · Bridge {{ bridgeMeta(selectedInstanceDetail) }}</template>
                  <template v-if="selectedInstanceDetail?.bridge_platform"> · {{ platformText(selectedInstanceDetail.bridge_platform) }}</template>
                </span>
              </div>
            </div>
            <button type="button" class="ai-btn sm" @click="closeDetail">关闭</button>
          </div>

          <div v-if="hasSelection && selectedRange" class="tt-panel-filter">
            <span>时间带过滤：{{ formatTime(selectedRange.start) }} - {{ formatTime(selectedRange.end) }}</span>
            <button type="button" class="ai-btn sm" @click="clearRunFilter">清除</button>
          </div>

          <div v-if="hasSelection" class="tt-panel-tabs">
            <div class="tt-panel-tabbar ai-tabs" role="tablist" aria-label="节点详情视图">
              <button
                v-for="tab in detailTabs"
                :key="tab.key"
                type="button"
                class="tt-detail-tab ai-tab arco-tabs-tab"
                :class="{ active: activeTab === tab.key, 'arco-tabs-tab-active': activeTab === tab.key }"
                role="tab"
                :aria-selected="activeTab === tab.key"
                @click="activeTab = tab.key"
              >
                {{ tab.label }}
              </button>
            </div>

            <div class="tt-panel-content">
              <template v-if="activeTab === 'detail'">
              <NodeDetailView :instance="selectedInstanceDetail" :loading="detailLoading" />
              </template>
              <template v-else-if="activeTab === 'diagnosis'">
              <DiagnosisView
                :run="visibleSelectedRun"
                :diagnosis="runDiagnosis"
                :ai-available="diagnosisAiAvailable"
                :loading="diagnosisLoading"
                @diagnose-run="diagnoseRun"
              />
              </template>
              <template v-else-if="activeTab === 'chain'">
              <RunChainView
                :instance="filteredInstance"
                :selected-run="visibleSelectedRun"
                :selected-chain="selectedChain"
                :chain-loading="chainLoading"
                :selected-run-id="visibleSelectedRun?.run_id || selectedRunId"
                @select-run="selectRun"
                @show-chain="showRunChain"
                @edit-skill="goEditSkill"
                @open-history="goExecutionHistory"
                @retry-skill="retrySkill"
              />
              </template>
              <template v-else-if="activeTab === 'schedules'">
              <ScheduleView
                :instance-id="selectedInstanceDetail?.instance_id || null"
                :training-jobs="selectedChain?.training_jobs || []"
              />
              </template>
              <template v-else-if="activeTab === 'value'">
              <SkillValueView :skill-value="skillValue" :loading="skillValueLoading" />
              </template>
            </div>
          </div>
          <div v-else class="tt-detail-empty">
            <span class="tt-detail-empty-icon"><SfShellIcon name="tree" /></span>
            <div>选择左侧节点查看调度、诊断与链路</div>
          </div>
        </div>
      </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { useRouter } from 'vue-router'
import { formatTime, toDate } from '@/utils/format'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'
import TaskTreeDeptSection from './TaskTreeDeptSection.vue'
import TaskTreeFilterBar from './TaskTreeFilterBar.vue'
import TaskTreeHeader from './TaskTreeHeader.vue'
import DiagnosisView from './components/DiagnosisView.vue'
import NodeDetailView from './components/NodeDetailView.vue'
import RunChainView from './components/RunChainView.vue'
import ScheduleView from './components/ScheduleView.vue'
import SkillValueView from './components/SkillValueView.vue'
import { computeInstanceMeta, isInRange } from './helpers'
import { useTaskTree } from './composables/useTaskTree'
import { SkillRunStatus } from './types'
import type { TaskTreeInstanceNode, TaskTreeNodeDetailResponse } from './types'

defineOptions({ name: 'TaskTree' })

const router = useRouter()

const {
  activeTab,
  anomalyInstances,
  canSwitchDepartment,
  chainLoading,
  clearRunFilter,
  compactView,
  departmentFilter,
  departmentOptions,
  detailLoading,
  diagnosisAiAvailable,
  diagnosisLoading,
  drawerOpen,
  etag,
  focusBucket,
  isCollapsed,
  loadError,
  nameQuery,
  nextRefreshCountdown,
  openDiagnosis,
  projectedAt,
  refreshAll,
  resetFilters,
  retrySkill,
  runDiagnosis,
  selectedChain,
  selectedInstanceDetail,
  selectedInstanceId,
  selectedRange,
  selectedRun,
  selectedRunId,
  selectInstance,
  selectRun,
  showAnomalyOnly,
  showRunChain,
  skillValue,
  skillValueLoading,
  toggleDepartment,
  tree,
  treeLoading,
  visibleTree,
  window,
  dashboard,
  diagnoseRun,
  refreshing,
} = useTaskTree()

const refreshFromHeader = () => refreshAll({ refresh: true })
const hasSelection = computed(() => Boolean(selectedInstanceId.value))
type StatusView = 'all' | 'running' | 'anomaly' | 'completed'
const statusView = ref<StatusView>(showAnomalyOnly.value ? 'anomaly' : 'all')
const detailTabs = [
  { key: 'detail', label: '节点详情' },
  { key: 'diagnosis', label: '运行诊断' },
  { key: 'chain', label: '链路' },
  { key: 'schedules', label: '定时任务' },
  { key: 'value', label: '价值' },
] as const

// P0-3：三组 summary 用的"已完成"计数 = 今日执行 − 今日失败。
// 没有更精确的服务端字段时先用这个估算，后续可替换为专用 metric。
const completedCountValue = computed(() => {
  const total = dashboard.value.todayExecutions || 0
  const failed = dashboard.value.todayFailed || 0
  return Math.max(0, total - failed)
})

const totalMachineCount = computed(() => (
  (visibleTree.value?.departments || []).reduce(
    (total, department) => total + (department.instances || []).length,
    0,
  )
))

function hasRunningRun(instance: TaskTreeInstanceNode): boolean {
  return Boolean(
    (instance.active_count || 0) > 0
    || (instance.recent_skills || []).some((run) => run.status === SkillRunStatus.RUNNING),
  )
}

function hasCompletedRun(instance: TaskTreeInstanceNode): boolean {
  return (instance.recent_skills || []).some((run) => (
    run.status === SkillRunStatus.COMPLETED || run.status === SkillRunStatus.FAILED
  ))
}

const displayTree = computed(() => {
  if (!visibleTree.value) return null
  if (statusView.value === 'all') return visibleTree.value
  if (statusView.value === 'anomaly') return visibleTree.value
  const departments = (visibleTree.value.departments || [])
    .map((department) => {
      const instances = (department.instances || []).filter((instance) => {
        if (statusView.value === 'running') return hasRunningRun(instance)
        return !hasRunningRun(instance) && hasCompletedRun(instance)
      })
      return {
        ...department,
        instances,
        node_count: instances.length,
        online_count: instances.filter((instance) => computeInstanceMeta(instance, window.value).nodeStatus === 'online').length,
        offline_count: instances.filter((instance) => computeInstanceMeta(instance, window.value).nodeStatus !== 'online').length,
      }
    })
    .filter((department) => (department.instances || []).length > 0)
  return { ...visibleTree.value, departments }
})

function selectStatusView(view: StatusView) {
  statusView.value = view
  showAnomalyOnly.value = view === 'anomaly'
  if (view !== 'anomaly') clearRunFilter()
}

watch(showAnomalyOnly, (value) => {
  if (value) statusView.value = 'anomaly'
  else if (statusView.value === 'anomaly') statusView.value = 'all'
})

watch(displayTree, async (next) => {
  if (!next?.departments?.length) {
    if (selectedInstanceId.value) closeDetail()
    return
  }

  const instances = next.departments.flatMap((department) => department.instances || [])
  if (!instances.length) {
    if (selectedInstanceId.value) closeDetail()
    return
  }

  const selectedVisible = selectedInstanceId.value
    ? instances.some((instance) => instance.instance_id === selectedInstanceId.value)
    : false
  if (selectedVisible) return

  await selectInstance(instances[0])
}, { flush: 'post' })

const filteredInstance = computed<TaskTreeNodeDetailResponse | null>(() => {
  if (!selectedInstanceDetail.value || !selectedRange.value) return selectedInstanceDetail.value
  const startMs = toDate(selectedRange.value.start)?.getTime()
  const endMs = toDate(selectedRange.value.end)?.getTime()
  if (startMs == null || endMs == null) return selectedInstanceDetail.value
  return {
    ...selectedInstanceDetail.value,
    active_runs: (selectedInstanceDetail.value.active_runs || []).filter((run) => isInRange(run.started_at, startMs, endMs)),
    recent_completed: (selectedInstanceDetail.value.recent_completed || []).filter((run) => isInRange(run.started_at, startMs, endMs)),
  }
})

const visibleSelectedRun = computed(() => {
  if (!selectedRange.value) return selectedRun.value
  const runs = [
    ...(filteredInstance.value?.active_runs || []),
    ...(filteredInstance.value?.recent_completed || []),
  ]
  if (!runs.length) return null
  if (selectedRun.value && runs.some((run) => run.run_id === selectedRun.value?.run_id)) {
    return selectedRun.value
  }
  return runs[0]
})

function bridgeMeta(instance: TaskTreeNodeDetailResponse): string {
  const current = instance.bridge_version || ''
  const latest = instance.bridge_latest_version || ''
  if (instance.bridge_update_available && latest) return `${current} -> ${latest}`
  return current
}

function platformText(value?: string | null): string {
  const platform = String(value || '').toLowerCase()
  if (platform === 'linux') return 'Linux'
  if (platform === 'darwin') return 'macOS'
  if (platform === 'win32' || platform === 'windows') return 'Windows'
  return value || ''
}

function runtimeTypeText(instance: TaskTreeNodeDetailResponse): string {
  const kind = (instance.runtime_type || instance.bridge_gateway_kind || instance.agent_type || '').toLowerCase()
  if (kind === 'openclaw') return 'OpenClaw'
  if (kind === 'openclaw-cn') return 'OpenClaw CN'
  if (kind === 'aiclaw') return 'AIClaw'
  if (kind === 'hermes') return 'Hermes'
  return kind || '-'
}

function closeDetail() {
  selectedInstanceId.value = ''
  drawerOpen.value = false
}

const emptyDescription = computed(() => {
  if (nameQuery.value.trim()) return `未找到与「${nameQuery.value.trim()}」匹配的节点`
  if (showAnomalyOnly.value) return '当前时间窗内全部节点健康'
  if (statusView.value === 'running') return '当前没有运行中的节点'
  if (statusView.value === 'completed') return '当前时间窗内没有已完成运行的节点'
  return '你所在部门尚未纳入任务树观测范围，请联系管理员'
})

const emptyActionLabel = computed(() => {
  if (nameQuery.value.trim() || showAnomalyOnly.value) return '清空筛选'
  return ''
})

function handleEmptyAction() {
  resetFilters()
}

async function copyOfflineList() {
  const offline = anomalyInstances.value
    .filter((item) => item.anomalyKind === 'offline')
    .map((item) => `${item.departmentName}\t${item.instance.name}\t${item.instance.instance_id}`)
  if (!offline.length) {
    Message.info('当前没有离线节点')
    return
  }
  const text = ['部门\t节点名\t实例ID', ...offline].join('\n')
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      Message.success(`已复制 ${offline.length} 个离线节点到剪贴板`)
    } else {
      throw new Error('no clipboard api')
    }
  } catch {
    Message.error('浏览器不支持剪贴板，请手动查看')
  }
}

function goEditSkill(skillId: string) {
  router.push(`/skills/${skillId}`)
}

function goExecutionHistory(skillId: string) {
  router.push(`/executions?skill_id=${skillId}`)
}
</script>

<style scoped>
.task-tree-page {
  display: flex;
  flex-direction: column;
  gap: 0;
  padding: 0;
  max-width: none;
  margin: 0;
  min-width: 0;
  min-height: 0;
  height: 100%;
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
  background: var(--ai-bg);
  border: 0;
  border-radius: 0;
  overflow: hidden;
}

.task-tree-body {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  padding: 0;
  overflow: hidden;
}

.tt-status-tabs {
  flex: 0 0 auto;
}

.tt-status-tab {
  gap: 4px;
  border: 0;
  background: transparent;
  cursor: pointer;
  font-family: var(--ai-font-sans);
}
.tt-tab-count {
  margin-left: 2px;
  font-family: var(--ai-font-mono);
  font-size: 11px;
}
.tt-tab-pill {
  display: inline-flex;
  align-items: center;
  height: 16px;
  padding: 0 5px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
  font-family: var(--ai-font-mono);
}
.tt-tab-pill.bad {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}
.tt-tab-pill.ok {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
}

/* ── 左右分栏 ── */
.tt-split {
  display: flex;
  flex: 1 1 auto;
  align-items: stretch;
  min-width: 0;
  min-height: 0;
  width: 100%;
  background: var(--ai-bg);
}

.tt-split-left {
  flex: 0 0 480px;
  width: 480px;
  min-width: 0;
  border-right: 1px solid var(--ai-border);
  background: var(--ai-surface);
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.tt-split-right {
  flex: 1;
  min-width: 0;
  background: var(--ai-bg);
  display: flex;
  min-height: 0;
}

.tt-dashboard-main {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  gap: 0;
  min-height: 0;
  overflow: auto;
  padding: 12px 16px;
}

.tt-detail-panel {
  width: 100%;
  min-width: 0;
  background: var(--ai-bg);
  border: 0;
  border-radius: 0;
  box-shadow: none;
  padding: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.tt-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 0;
  padding: 16px 24px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
}

.tt-panel-identity {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.tt-panel-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ai-ink-4);
  flex-shrink: 0;
}
.tt-panel-dot.online { background: var(--ai-ok); }
.tt-panel-dot.maybe_offline { background: var(--ai-warn); }
.tt-panel-dot.offline { background: var(--ai-bad); }

.tt-panel-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.tt-panel-copy strong {
  color: var(--ai-ink-1);
  font-size: 18px;
  font-weight: 600;
  letter-spacing: -0.015em;
}
.tt-panel-meta {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

.tt-panel-filter {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 0;
  padding: 8px 24px;
  border-radius: 0;
  background: var(--ai-surface-2);
  border: 0;
  border-bottom: 1px solid var(--ai-border);
  color: var(--ai-ink-2);
  font-size: 12px;
  font-weight: 500;
}

.tt-panel-tabs {
  min-height: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.tt-panel-tabbar {
  flex: 0 0 auto;
  display: flex;
  gap: 4px;
  padding: 0 24px;
  overflow-x: auto;
  scrollbar-gutter: stable;
}
.tt-detail-tab {
  flex: 0 0 auto;
  min-width: max-content;
  border-top: 0;
  border-right: 0;
  border-left: 0;
  background: transparent;
  font-family: var(--ai-font-sans);
  white-space: nowrap;
}
.tt-detail-tab:hover {
  color: var(--ai-ink-2);
}

.tt-panel-content {
  padding: 24px;
  overflow: auto;
  flex: 1;
  min-height: 0;
  background: var(--ai-bg);
}

.tt-detail-empty {
  flex: 1;
  min-height: 320px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  justify-content: center;
  color: var(--ai-ink-3);
  font-size: 13px;
}
.tt-detail-empty-icon {
  display: grid;
  place-items: center;
  color: var(--ai-ink-4);
  font-size: 28px;
}

/* ── 空状态 / 骨架 (设计稿 .ai-card) ── */
.tt-empty-state {
  min-height: 280px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--ai-surface);
  border: 0;
  border-top: 1px solid var(--ai-border);
  border-radius: 0;
  box-shadow: none;
}

.tt-skeleton {
  display: grid;
  gap: 12px;
}
.tt-skeleton-card {
  min-height: 160px;
  border-radius: var(--ai-radius);
}
.tt-empty-icon {
  display: grid;
  place-items: center;
  color: var(--ai-ink-4);
  font-size: 32px;
  opacity: 0.6;
}

.tt-empty-icon svg,
.tt-detail-empty-icon svg {
  width: 28px;
  height: 28px;
}

.task-tree-page.compact { gap: 0; }

/* ── 响应式 ── */
@media (max-width: 1024px) {
  .task-tree-page {
    border-radius: var(--ai-radius);
  }
  .tt-split {
    flex-direction: column;
    align-items: stretch;
    gap: 0;
    width: 100%;
    max-width: 100%;
    overflow-x: hidden;
  }
  .tt-split-left {
    flex: 0 0 auto;
    width: 100%;
    max-height: none;
    border-right: 0;
    border-bottom: 1px solid var(--ai-border);
  }
  .tt-split-right {
    max-height: none;
    width: 100%;
    max-width: 100%;
    overflow-x: hidden;
  }
  .tt-detail-panel {
    width: 100%;
    max-width: 100%;
    box-sizing: border-box;
  }
  .tt-panel-header {
    align-items: flex-start;
  }
  .tt-panel-tabs {
    min-width: 0;
  }
  .tt-panel-tabbar {
    min-width: 0;
    overflow-x: auto;
  }
  .tt-panel-content {
    min-width: 0;
    overflow-x: auto;
    padding: 16px;
  }
}

@media (max-width: 640px) {
  .tt-status-tabs,
  .tt-panel-tabbar {
    padding: 0 10px;
    overflow-x: auto;
  }
  .tt-status-tab {
    flex: 0 0 auto;
    padding: 0 10px;
  }
  .tt-dashboard-main {
    padding: 10px;
  }
  .tt-panel-header {
    padding: 14px 16px;
  }
}
</style>
