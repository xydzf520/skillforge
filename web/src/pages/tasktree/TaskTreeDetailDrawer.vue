<template>
  <a-drawer
    :visible="open"
    :width="expanded ? 'min(960px, 92vw)' : 'min(680px, 85vw)'"
    :unmount-on-close="false"
    :footer="false"
    :mask-closable="true"
    class="tt-drawer"
    @cancel="drawerOpen = false"
  >
    <template #title>
      <div class="tt-drawer-header" data-testid="tasktree-detail">
        <div class="tt-drawer-identity">
          <span class="tt-drawer-dot" :class="instance?.node_status || 'offline'" />
          <div class="tt-drawer-copy">
            <strong>{{ instance?.name || '节点详情' }}</strong>
            <span class="tt-drawer-meta">
              {{ instance?.department || '未选择节点' }}
              <template v-if="instance?.bridge_version"> · Bridge {{ bridgeMeta(instance) }}</template>
              <template v-if="instance?.bridge_platform"> · {{ platformText(instance.bridge_platform) }}</template>
            </span>
          </div>
        </div>
        <div class="tt-drawer-actions">
          <a-button size="mini" type="text" @click="expanded = !expanded">
            {{ expanded ? '收窄' : '展开' }}
          </a-button>
        </div>
      </div>
    </template>

    <div v-if="runFilter" class="tt-drawer-filter">
      <span>时间带过滤：{{ formatTime(runFilter.start) }} - {{ formatTime(runFilter.end) }}</span>
      <a-button size="mini" type="text" @click="$emit('clear-run-filter')">清除</a-button>
    </div>

    <a-tabs v-model:active-key="tabModel" class="tt-detail-tabs" size="small">
      <a-tab-pane key="detail" title="节点详情">
        <NodeDetailView :instance="instance" :loading="detailLoading" />
      </a-tab-pane>
      <a-tab-pane key="diagnosis" title="运行诊断">
        <DiagnosisView
          :run="visibleSelectedRun"
          :diagnosis="runDiagnosis"
          :ai-available="diagnosisAiAvailable"
          :loading="diagnosisLoading"
          @diagnose-run="(runId) => $emit('diagnose-run', runId)"
        />
      </a-tab-pane>
      <a-tab-pane key="chain" title="链路">
        <RunChainView
          :instance="filteredInstance"
          :selected-run="visibleSelectedRun"
          :selected-chain="selectedChain"
          :chain-loading="chainLoading"
          :selected-run-id="visibleSelectedRun?.run_id || selectedRunId"
          @select-run="(inst, run) => $emit('select-run', inst, run)"
          @show-chain="(runId) => $emit('show-chain', runId)"
          @edit-skill="(id) => $emit('edit-skill', id)"
          @open-history="(id) => $emit('open-history', id)"
          @retry-skill="(id) => $emit('retry-skill', id)"
        />
      </a-tab-pane>
      <a-tab-pane key="schedules" title="定时任务">
        <ScheduleView
          :instance-id="instance?.instance_id || null"
          :training-jobs="selectedChain?.training_jobs || []"
        />
      </a-tab-pane>
      <a-tab-pane key="value" title="价值">
        <SkillValueView :skill-value="skillValue" :loading="skillValueLoading" />
      </a-tab-pane>
    </a-tabs>
  </a-drawer>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { formatTime, toDate } from '@/utils/format'
import DiagnosisView from './components/DiagnosisView.vue'
import NodeDetailView from './components/NodeDetailView.vue'
import RunChainView from './components/RunChainView.vue'
import ScheduleView from './components/ScheduleView.vue'
import SkillValueView from './components/SkillValueView.vue'
import { isInRange } from './helpers'
import type {
  TaskTreeDetailTab,
  TaskTreeNodeDetailResponse,
  TaskTreeRunChainResponse,
  TaskTreeSkillRunItem,
  TaskTreeSkillValueResponse,
} from './types'

const props = defineProps<{
  open: boolean
  activeTab: TaskTreeDetailTab
  instance: TaskTreeNodeDetailResponse | null
  selectedRun: TaskTreeSkillRunItem | null
  selectedChain: TaskTreeRunChainResponse | null
  runDiagnosis: string
  diagnosisAiAvailable?: boolean
  skillValue: TaskTreeSkillValueResponse | null
  detailLoading: boolean
  chainLoading: boolean
  diagnosisLoading: boolean
  skillValueLoading: boolean
  selectedRunId: string
  runFilter?: { start: string; end: string } | null
}>()

const emit = defineEmits<{
  'update:open': [value: boolean]
  'update:activeTab': [value: TaskTreeDetailTab]
  'select-run': [instance: TaskTreeNodeDetailResponse, run: TaskTreeSkillRunItem]
  'show-chain': [runId: string]
  'edit-skill': [skillId: string]
  'open-history': [skillId: string]
  'diagnose-run': [runId: string]
  'retry-skill': [skillId: string]
  'clear-run-filter': []
}>()

const expanded = ref(false)

const drawerOpen = computed({
  get: () => props.open,
  set: (value: boolean) => emit('update:open', value),
})

const tabModel = computed({
  get: () => props.activeTab,
  set: (value: string | number) => emit('update:activeTab', value as TaskTreeDetailTab),
})

const filteredInstance = computed<TaskTreeNodeDetailResponse | null>(() => {
  if (!props.instance || !props.runFilter) return props.instance
  const startMs = toDate(props.runFilter.start)?.getTime()
  const endMs = toDate(props.runFilter.end)?.getTime()
  if (startMs == null || endMs == null) return props.instance
  return {
    ...props.instance,
    active_runs: (props.instance.active_runs || []).filter((run) => isInRange(run.started_at, startMs, endMs)),
    recent_completed: (props.instance.recent_completed || []).filter((run) => isInRange(run.started_at, startMs, endMs)),
  }
})

const visibleSelectedRun = computed(() => {
  if (!props.runFilter) return props.selectedRun
  const runs = [
    ...(filteredInstance.value?.active_runs || []),
    ...(filteredInstance.value?.recent_completed || []),
  ]
  if (!runs.length) return null
  if (props.selectedRun && runs.some((run) => run.run_id === props.selectedRun?.run_id)) {
    return props.selectedRun
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
</script>

<style scoped>
.tt-drawer :deep(.arco-drawer) {
  transition: width 0.25s ease;
}

.tt-drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
}

.tt-drawer-identity {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.tt-drawer-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ai-ink-4);
  flex-shrink: 0;
}
.tt-drawer-dot.online { background: var(--ai-ok); }
.tt-drawer-dot.maybe_offline { background: var(--ai-warn); }
.tt-drawer-dot.offline { background: var(--ai-bad); }

.tt-drawer-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.tt-drawer-copy strong {
  color: var(--ai-ink-1);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.015em;
}

.tt-drawer-meta {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 450;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

.tt-drawer-actions {
  flex-shrink: 0;
}

.tt-drawer-filter {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  padding: 8px 10px;
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  color: var(--ai-ink-2);
  font-size: 12px;
  font-weight: 500;
}

.tt-detail-tabs :deep(.arco-tabs-nav) {
  border-bottom: 1px solid var(--ai-border);
}
.tt-detail-tabs :deep(.arco-tabs-tab) {
  padding: 0 12px;
  height: 36px;
  font-weight: 450;
  font-size: 13px;
  color: var(--ai-ink-3);
}
.tt-detail-tabs :deep(.arco-tabs-tab-active) {
  color: var(--ai-ink-1);
  font-weight: 500;
}
.tt-detail-tabs :deep(.arco-tabs-nav-ink) {
  background: var(--ai-ink-1);
  height: 1.5px;
}
.tt-detail-tabs :deep(.arco-tabs-content) {
  padding-top: 12px;
}
</style>
