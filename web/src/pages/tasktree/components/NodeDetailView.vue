<template>
  <section class="tt-detail-card ai-card" data-testid="tasktree-detail-node">
    <div class="ai-card-h tt-card-title">
      <span class="t">节点详情</span>
      <span class="s">{{ instance?.instance_id || '未选择节点' }}</span>
    </div>

    <div v-if="loading" class="tt-detail-loading">
      <a-spin />
    </div>

    <template v-else-if="instance">
      <a-descriptions :column="1" size="small" class="tt-desc">
        <a-descriptions-item label="节点">{{ instance.name }}</a-descriptions-item>
        <a-descriptions-item label="状态">
          <span class="tt-status-line">
            <span class="tt-status-dot" :class="instance.node_status || NodeStatus.OFFLINE" />
            {{ nodeStatusLabel(instance.node_status) }}
          </span>
        </a-descriptions-item>
        <a-descriptions-item label="类型">{{ runtimeTypeText(instance) }}</a-descriptions-item>
        <a-descriptions-item label="心跳">{{ heartbeatLabel(instance) }}</a-descriptions-item>
        <a-descriptions-item label="部门">{{ instance.department || '-' }}</a-descriptions-item>
        <a-descriptions-item label="Bridge 版本">
          <span>{{ bridgeVersionText(instance) }}</span>
          <a-tag v-if="instance.bridge_update_available" size="small" color="orange" class="bridge-update-tag">
            自动更新中
          </a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="Bridge 平台">{{ platformText(instance.bridge_platform) || '-' }}</a-descriptions-item>
        <a-descriptions-item label="容量">{{ instance.capacity != null ? `${instance.active_count || 0}/${instance.capacity}` : '-' }}</a-descriptions-item>
        <a-descriptions-item label="技能目录">{{ instance.bridge_skills_dir || '-' }}</a-descriptions-item>
        <a-descriptions-item label="最近同步">{{ formatTime(instance.last_sync_at) }}</a-descriptions-item>
      </a-descriptions>
    </template>

    <SfEmptyState v-else description="点击左侧节点查看详情" />
  </section>
</template>

<script setup lang="ts">
import { formatTime } from '@/utils/format'
import { SfEmptyState } from '@/components/common'
import { heartbeatLabel, nodeStatusLabel } from '../helpers'
import { NodeStatus, type TaskTreeInstanceNode } from '../types'

defineProps<{
  instance: TaskTreeInstanceNode | null
  loading: boolean
}>()

function bridgeVersionText(instance: TaskTreeInstanceNode): string {
  const current = instance.bridge_version || '-'
  if (!instance.bridge_latest_version || current === '-') return current
  return instance.bridge_update_available
    ? `${current} / 自动更新到 ${instance.bridge_latest_version}`
    : `${current} / 已是最新`
}

function runtimeTypeText(instance: TaskTreeInstanceNode): string {
  const kind = (instance.runtime_type || instance.bridge_gateway_kind || instance.agent_type || '').toLowerCase()
  if (kind === 'openclaw') return 'OpenClaw'
  if (kind === 'openclaw-cn') return 'OpenClaw CN'
  if (kind === 'aiclaw') return 'AIClaw'
  if (kind === 'hermes') return 'Hermes'
  return kind || '-'
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
.tt-detail-card {
  min-width: 0;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  box-shadow: none;
  overflow: hidden;
}

.tt-card-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}
.tt-card-title .s {
  font-family: var(--ai-font-mono);
}
.tt-detail-loading {
  display: grid;
  place-items: center;
  min-height: 120px;
}

.tt-desc {
  margin: 12px 16px 16px;
}

.bridge-update-tag {
  margin-left: 6px;
}

.tt-status-line {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.tt-status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--ai-ink-4);
}

.tt-status-dot.online { background: var(--ai-ok); }
.tt-status-dot.maybe_offline { background: var(--ai-warn); }
.tt-status-dot.offline { background: var(--ai-bad); }
</style>
