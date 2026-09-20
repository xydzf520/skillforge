<template>
  <div class="page-container admin-agent-device-detail-page">
    <!-- 顶部工具栏：返回 + 标题 + 状态标签 + 右侧操作 -->
    <div class="page-header page-detail-toolbar">
      <div class="toolbar-left">
        <a-button @click="$router.push('/admin/agent-devices')">
          <template #icon><icon-left /></template>返回
        </a-button>
        <div class="header-titles">
          <div class="page-kicker">管理后台 · Agent 终端详情</div>
          <h2 class="page-title">{{ instance?.name || instanceId }}</h2>
          <p class="page-subtitle">{{ instance?.department ? instance.department + ' · ' : '' }}{{ runtimeTypeText(instance) }}{{ instance?.agent_purpose ? ' · ' + agentPurposeLabel(instance.agent_purpose) : '' }}</p>
        </div>
        <a-tag :color="instance?.bridge_online ? 'green' : 'gray'" size="large">
          {{ instance?.bridge_online ? '在线' : '离线' }}
        </a-tag>
        <a-tag
          v-if="instance?.agent_type"
          :color="agentTagColor(instance)"
          size="large"
        >
          {{ runtimeTypeText(instance) }}
        </a-tag>
        <a-tag
          v-if="instance?.agent_purpose"
          :color="agentPurposeColor(instance.agent_purpose)"
          size="large"
        >
          {{ agentPurposeLabel(instance.agent_purpose) }}
        </a-tag>
      </div>
      <a-space>
        <a-button size="small" @click="refreshAll" :loading="loading">
          <template #icon><icon-refresh /></template>刷新
        </a-button>
        <a-button size="small" @click="$router.push('/admin/agent-devices')">
          Agent终端
        </a-button>
        <a-button
          size="small"
          type="primary"
          @click="$router.push(`/aiclaw/instances/${instanceId}`)"
        >
          <template #icon><icon-message /></template>进入对话
        </a-button>
      </a-space>
    </div>

    <!-- 基本信息 -->
    <a-card title="基本信息" class="page-section-card section-card-gap">
      <a-descriptions :column="2" bordered size="small" class="agent-desc">
        <a-descriptions-item v-for="it in infoItems" :key="it.label" :label="it.label">
          <span :class="{ mono: it.mono }">{{ it.value || '-' }}</span>
        </a-descriptions-item>
      </a-descriptions>
    </a-card>

    <!-- 硬件资源 -->
    <a-card
      v-if="capabilities"
      title="硬件资源"
      class="page-section-card section-card-gap"
    >
      <a-descriptions :column="2" bordered size="small" class="agent-desc">
        <!-- 磁盘 -->
        <a-descriptions-item label="磁盘" :span="2">
          <div v-if="diskEntries.length" class="hw-block">
            <div v-for="(entry, idx) in diskEntries" :key="idx" class="hw-line">
              <span class="hw-label mono">{{ entry.mount }}</span>
              <span class="hw-value">
                {{ entry.used_gb }} / {{ entry.total_gb }} GB
                <a-tag
                  :color="entry.use_pct > 90 ? 'red' : entry.use_pct > 70 ? 'orange' : 'green'"
                  size="small"
                  class="hw-tag"
                >{{ entry.use_pct }}%</a-tag>
              </span>
            </div>
          </div>
          <span v-else class="muted">-</span>
        </a-descriptions-item>
        <!-- 内存 -->
        <a-descriptions-item label="内存">
          <span v-if="memInfo">{{ memInfo.used_gb }} / {{ memInfo.total_gb }} GB
            <a-tag
              :color="memInfo.used_pct > 90 ? 'red' : memInfo.used_pct > 70 ? 'orange' : 'green'"
              size="small"
              class="hw-tag"
            >{{ memInfo.used_pct }}%</a-tag>
          </span>
          <span v-else class="muted">-</span>
        </a-descriptions-item>
        <!-- GPU -->
        <a-descriptions-item label="GPU">
          <div v-if="gpuEntries.length" class="hw-block">
            <div v-for="(gpu, idx) in gpuEntries" :key="idx" class="hw-line">
              <span class="hw-label">{{ gpu.name }}</span>
              <span class="hw-value">
                VRAM {{ gpu.vram_used_mb }} / {{ gpu.vram_total_mb }} MB
                <a-tag
                  :color="gpu.gpu_util_pct > 90 ? 'red' : gpu.gpu_util_pct > 70 ? 'orange' : 'arcoblue'"
                  size="small"
                  class="hw-tag"
                >{{ gpu.gpu_util_pct }}%</a-tag>
              </span>
            </div>
          </div>
          <span v-else class="muted">无</span>
        </a-descriptions-item>
        <!-- 训练网关能力 -->
        <a-descriptions-item label="训练能力" :span="2">
          <div v-if="trainingCapability" class="training-capability">
            <div class="training-summary">
              <a-tag :color="trainingGatewayReady ? 'green' : 'gray'" size="small">
                {{ trainingGatewayReady ? '训练网关已启用' : '训练网关未启用' }}
              </a-tag>
              <a-tag size="small" color="arcoblue">{{ trainingWorkerCount }} Worker</a-tag>
              <a-tag size="small" color="purple">{{ trainingGpuCount }} GPU</a-tag>
              <a-button size="mini" type="text" @click="$router.push(trainingConsolePath)">
                <template #icon><icon-storage /></template>
                训练工作台
              </a-button>
            </div>
            <div v-if="trainingSupportedTasks.length" class="training-task-list">
              <a-tag
                v-for="task in trainingSupportedTasks"
                :key="task"
                size="small"
                :color="trainingTaskColor(task)"
              >
                {{ trainingTaskLabel(task) }}
              </a-tag>
            </div>
            <div v-if="activeTrainingJobs.length" class="training-active-list">
              <a-tag size="small" color="orange">运行中 {{ activeTrainingJobCount }}</a-tag>
              <a-link
                v-for="job in activeTrainingJobs.slice(0, 3)"
                :key="job.id"
                @click="$router.push(`/training/jobs/${encodeURIComponent(job.id)}`)"
              >
                {{ job.title }}
              </a-link>
            </div>
          </div>
          <span v-else class="muted">未上报训练能力</span>
        </a-descriptions-item>
      </a-descriptions>
    </a-card>

    <a-card
      v-if="showMediaBootstrap"
      title="MiniMax H3 媒体环境"
      class="page-section-card section-card-gap"
    >
      <template #extra>
        <a-space>
          <a-button size="small" :loading="mediaBootstrapLoading" @click="loadMediaBootstrap()">
            <template #icon><icon-refresh /></template>刷新进度
          </a-button>
          <a-button
            v-if="mediaBootstrapRunning"
            size="small"
            status="danger"
            :loading="mediaBootstrapActionLoading"
            @click="cancelMediaBootstrap"
          >取消安装</a-button>
          <a-button
            v-else-if="mediaBootstrapStatus !== 'succeeded'"
            size="small"
            type="primary"
            :disabled="!instance?.bridge_online"
            :loading="mediaBootstrapActionLoading"
            @click="confirmStartMediaBootstrap"
          >{{ mediaBootstrapPaused || mediaBootstrapStatus === 'failed' || mediaBootstrapStatus === 'cancelled' ? '续传安装' : '安装 H3' }}</a-button>
        </a-space>
      </template>
      <a-alert v-if="mediaBootstrapUnavailable" type="warning" class="media-bootstrap-alert">
        Bridge 正在重连，暂时无法读取安装进度；已下载文件和后台安装不会因此被清除。连接恢复后点击“刷新进度”。
      </a-alert>
      <a-alert v-else-if="mediaBootstrapLoadError" type="warning" class="media-bootstrap-alert">
        {{ mediaBootstrapLoadError }}
      </a-alert>
      <a-alert v-else-if="mediaBootstrapError" type="error" class="media-bootstrap-alert">
        {{ mediaBootstrapError }}
      </a-alert>
      <a-alert v-else-if="mediaBootstrapPaused" type="warning" class="media-bootstrap-alert">
        安装进程已暂停，已下载文件会保留；点击“续传安装”可从断点继续。
      </a-alert>
      <a-descriptions :column="2" bordered size="small" class="agent-desc">
        <a-descriptions-item label="状态">
          <a-tag :color="mediaBootstrapStatusColor" size="small">{{ mediaBootstrapStatusLabel }}</a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="固定配置">
          <span class="mono">h3_all_modes_v1 · 3 MB/s · ComfyUI 0.31.0</span>
        </a-descriptions-item>
        <a-descriptions-item label="当前阶段">
          {{ mediaBootstrap?.current_step || '-' }}
        </a-descriptions-item>
        <a-descriptions-item label="当前文件">
          <span class="mono">{{ mediaBootstrap?.current_file || '-' }}</span>
        </a-descriptions-item>
        <a-descriptions-item label="下载进度" :span="2">
          <span v-if="mediaBootstrapUnavailable" class="muted">等待 Bridge 连接后读取实际进度</span>
          <template v-else>
            <a-progress :percent="mediaBootstrapProgressRatio" :status="mediaBootstrapStatus === 'failed' ? 'danger' : 'normal'" />
            <div class="media-bootstrap-meta">
              <span>进度 {{ mediaBootstrapProgress.toFixed(2) }}%</span>
              <span>{{ formatBytes(mediaBootstrap?.downloaded_bytes) }} / {{ formatBytes(mediaBootstrap?.total_bytes) }}</span>
              <span>速度 {{ formatSpeed(mediaBootstrap?.speed_bytes_per_second) }}</span>
              <span>预计剩余 {{ formatEta(mediaBootstrap?.eta_seconds) }}</span>
            </div>
          </template>
        </a-descriptions-item>
        <a-descriptions-item label="支持模式" :span="2">
          <a-tag v-for="mode in mediaSupportedModes" :key="mode" size="small" color="purple">
            {{ mediaModeLabel(mode) }}
          </a-tag>
          <span v-if="!mediaSupportedModes.length" class="muted">模型、节点和模板自检通过后才会进入调度</span>
        </a-descriptions-item>
      </a-descriptions>
    </a-card>

    <!-- 主体：master-detail（左 agent 列表，右当前 agent 的 skill 列表） -->
    <a-card title="Agents 与 Skills" class="page-section-card agents-card">
      <div class="master-detail">
        <!-- 左：Agent 列表 -->
        <div class="panel master-panel">
          <div class="panel-head">
            <div class="panel-head-title">
              <icon-robot :size="14" />
              <span>Agents</span>
              <a-tag v-if="agents.length" size="small" color="gray">{{ agents.length }}</a-tag>
            </div>
          </div>
          <div class="panel-body">
            <SfLoadingState v-if="loadingAgents" tip="加载 agents..." height="180px" />
            <div v-else-if="loadFailed" class="error-state">
              <div class="error-title">{{ errorTitle }}</div>
              <div class="error-hint">{{ errorHint }}</div>
              <ul v-if="errorHints.length" class="error-hints">
                <li v-for="(h, idx) in errorHints" :key="idx">{{ h }}</li>
              </ul>
              <pre v-if="bridgeErrorText" class="error-bridge-raw">{{ bridgeErrorText }}</pre>
              <a-button size="small" type="outline" @click="loadAgents">重试</a-button>
            </div>
            <SfEmptyState v-else-if="!agents.length" description="无 agent" />
            <div v-else class="agent-list">
              <div
                v-for="a in agents"
                :key="keyOf(a)"
                class="agent-item"
                :class="{ active: selectedKey === keyOf(a) }"
                @click="selectAgent(a)"
              >
                <div class="agent-row1">
                  <span class="agent-name">{{ nameOf(a) }}</span>
                  <a-tooltip
                    v-if="skillCountForAgent(a) !== null"
                    :content="`自建 ${skillCountForAgent(a)} / 全部 ${totalSkillCountForAgent(a)}`"
                    mini
                  >
                    <a-tag size="small" color="arcoblue" class="agent-skill-badge">
                      {{ skillCountForAgent(a) }}
                      <span class="agent-skill-badge-total">/{{ totalSkillCountForAgent(a) }}</span>
                    </a-tag>
                  </a-tooltip>
                </div>
                <div v-if="keyOf(a) !== nameOf(a)" class="agent-row2 mono">{{ keyOf(a) }}</div>
              </div>
            </div>
          </div>
        </div>

        <!-- 右：Skills（当前 agent 的） -->
        <div class="panel detail-panel">
          <div class="panel-head">
            <div class="panel-head-title">
              <icon-storage :size="14" />
              <span>Skills</span>
              <span v-if="selectedAgent" class="muted">— {{ nameOf(selectedAgent) }}</span>
            </div>
            <a-space size="mini">
              <a-radio-group v-model="sourceFilter" type="button" size="small">
                <a-radio value="custom">
                  SkillForge <span class="source-count">{{ sourceCounts.custom }}</span>
                </a-radio>
                <a-radio value="synced">
                  已同步 <span class="source-count">{{ sourceCounts.synced }}</span>
                </a-radio>
                <a-radio value="unsynced">
                  未同步 <span class="source-count warn">{{ sourceCounts.unsynced }}</span>
                </a-radio>
                <a-radio value="system">
                  系统 <span class="source-count">{{ sourceCounts.system }}</span>
                </a-radio>
                <a-radio value="all">
                  全部 <span class="source-count">{{ sourceCounts.all }}</span>
                </a-radio>
              </a-radio-group>
              <a-input-search
                v-model="skillFilter"
                placeholder="搜索"
                allow-clear
                size="small"
                class="status-select"
              />
              <a-button
                size="mini"
                type="text"
                :disabled="!selectedAgent || loadingSkills"
                @click="reloadCurrentSkills"
              >
                <template #icon><icon-refresh /></template>
              </a-button>
            </a-space>
          </div>
          <div class="panel-body">
            <SfEmptyState v-if="!selectedAgent && !loadingAgents" description="请先从左侧选择一个 agent" />
            <SfLoadingState v-else-if="loadingSkills" tip="加载 skills..." height="180px" />
            <SfEmptyState v-else-if="!currentSkills.length && selectedAgent" description="该 agent 下无 skill" />
            <div v-else-if="!filteredSkills.length" class="empty-filter">
              <SfEmptyState :description="emptyDescription" />
              <a-button
                v-if="sourceFilter !== 'all'"
                size="small"
                type="text"
                @click="sourceFilter = 'all'"
              >
                查看全部
              </a-button>
            </div>
            <a-table
              v-else
              :data="filteredSkills"
              :pagination="filteredSkills.length > 20 ? { pageSize: 20, showTotal: true, size: 'small' } : false"
              :bordered="false"
              :row-key="skillRowKey"
              size="small"
            >
              <template #columns>
                <a-table-column title="Skill" ellipsis tooltip>
                  <template #cell="{ record }">
                    <div class="skill-cell">
                      <div class="skill-line-1">
                        <span class="skill-primary">{{ skillPrimaryName(record) }}</span>
                        <a-tag
                          v-if="record._unsynced"
                          size="small"
                          color="orange"
                          class="source-badge"
                        >
                          未同步
                        </a-tag>
                        <a-tag
                          v-else-if="isSkillforgeSkill(record)"
                          size="small"
                          color="arcoblue"
                          class="source-badge"
                        >
                          SkillForge
                        </a-tag>
                      </div>
                      <span v-if="skillSecondary(record) || record._unsynced" class="skill-secondary mono">
                        {{ skillSecondary(record) || record.id }}
                      </span>
                    </div>
                  </template>
                </a-table-column>
                <a-table-column v-if="columnVisibility.version" title="版本" :width="100">
                  <template #cell="{ record }">
                    <span class="mono muted">{{ record.version || record.current_version || '-' }}</span>
                  </template>
                </a-table-column>
                <a-table-column v-if="columnVisibility.git" title="Git版本" :width="128">
                  <template #cell="{ record }">
                    <a-tooltip v-if="skillGitShort(record)" :content="skillGitTooltip(record)" mini position="top">
                      <span class="mono git-version" :class="`git-version--${skillGitState(record)}`">
                        {{ skillGitShort(record) }}
                      </span>
                    </a-tooltip>
                    <span v-else class="muted">-</span>
                  </template>
                </a-table-column>
                <a-table-column v-if="columnVisibility.status" title="状态" :width="110">
                  <template #cell="{ record }">
                    <a-tag v-if="record.status" size="small" :color="(record.status === 'active' || record.status === 'loaded') ? 'green' : 'gray'">
                      {{ record.status }}
                    </a-tag>
                    <span v-else class="muted">-</span>
                  </template>
                </a-table-column>
                <a-table-column title="操作" :width="220" align="right">
                  <template #cell="{ record }">
                    <a-space size="mini">
                      <!-- 自建 skill（已同步或未同步）：直接编辑 -->
                      <a-button
                        v-if="isSkillforgeSkill(record) || record._unsynced"
                        type="text"
                        size="mini"
                        @click="editSkill(record)"
                      >
                        <template #icon><icon-edit /></template>编辑
                      </a-button>
                      <!-- 系统 skill：先导入再编辑 -->
                      <a-button
                        v-else
                        type="text"
                        size="mini"
                        :loading="importingSkills.has(record.id || record.name)"
                        @click="importAndEditSkill(record)"
                      >
                        <template #icon><icon-import /></template>导入并编辑
                      </a-button>

                      <!-- 未同步自建：同步到设备 -->
                      <a-button
                        v-if="record._unsynced"
                        type="text"
                        size="mini"
                        :loading="syncingSkills.has(record.id)"
                        @click="syncSkillToDevice(record)"
                      >
                        <template #icon><icon-upload /></template>同步
                      </a-button>
                      <!-- 已在设备：删除 -->
                      <a-popconfirm
                        v-else
                        :content="`从设备移除 ${skillPrimaryName(record)}？`"
                        type="warning"
                        @ok="removeSkill(record)"
                      >
                        <a-button type="text" status="danger" size="mini">
                          <template #icon><icon-delete /></template>
                        </a-button>
                      </a-popconfirm>
                    </a-space>
                  </template>
                </a-table-column>
              </template>
            </a-table>
          </div>
        </div>
      </div>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Message, Modal } from '@arco-design/web-vue'
import {
  IconLeft, IconRefresh, IconMessage, IconRobot, IconStorage, IconDelete, IconUpload, IconEdit, IconImport,
} from '@arco-design/web-vue/es/icon'
import { aiclawApi as rawAiclawApi, skillApi as rawSkillApi } from '@/api'
import { formatTimeFull } from '@/utils/format'
import { SfLoadingState, SfEmptyState } from '@/components/common'

defineOptions({ name: 'AdminAgentDeviceDetail' })

const route = useRoute()
const aiclawApi: any = rawAiclawApi
const skillApi: any = rawSkillApi

const instanceId = computed(() => String(route.params.id || ''))
const instance = ref<any>(null)
const loading = ref(false)
const capabilities = ref<any>(null)
const mediaBootstrap = ref<any>(null)
const mediaBootstrapLoading = ref(false)
const mediaBootstrapActionLoading = ref(false)
const mediaBootstrapLoadError = ref('')
let mediaBootstrapTimer: ReturnType<typeof setInterval> | null = null

const agents = ref<any[]>([])
const loadingAgents = ref(false)
const loadFailed = ref(false)
const loadError = ref<string>('')
const loadErrorCode = ref<string>('')
const loadErrorDetail = ref<Record<string, any>>({})

// 根据错误码和实例在线状态，给更友好的提示文案
const errorTitle = computed(() => {
  if (loadErrorCode.value === 'BRIDGE_OFFLINE' || !instance.value?.bridge_online) {
    return '代理设备未连接'
  }
  if (loadErrorCode.value === 'AICLAW_ERROR') {
    const kind = loadErrorDetail.value?.gateway_kind
    return kind ? `${kind} gateway 调用失败` : 'AIClaw 调用失败'
  }
  return '无法加载 Agent 列表'
})
const errorHint = computed(() => {
  if (loadErrorCode.value === 'BRIDGE_OFFLINE' || !instance.value?.bridge_online) {
    return '请检查内网 bridge 进程是否在运行、能否访问 SkillForge。Bridge 握手成功后将自动上线。'
  }
  const method = loadErrorDetail.value?.method
  if (method) {
    return `转发 method=${method} 时失败：${loadError.value || '未知错误'}`
  }
  return loadError.value || '请稍后重试'
})
const errorHints = computed<string[]>(() => {
  const h = loadErrorDetail.value?.hints
  return Array.isArray(h) ? h : []
})
const bridgeErrorText = computed<string>(() => {
  const be = loadErrorDetail.value?.bridge_error
  if (!be) return ''
  try {
    return typeof be === 'string' ? be : JSON.stringify(be, null, 2)
  } catch {
    return String(be)
  }
})

// 每个 agent 独立缓存 skill 列表（AIClaw 的 skills.status 确实按 agentId 过滤）
const skillsByAgent = ref<Record<string, any[]>>({})
const loadingSkills = ref(false)
const selectedKey = ref<string>('')
const skillFilter = ref('')

// SkillForge 平台的全部自建 skill（完整对象，用于判断"已同步/未同步"并提供一键同步）
const skillforgeSkills = ref<any[]>([])
const skillforgeSkillIds = computed<Set<string>>(
  () => new Set(skillforgeSkills.value.map((s: any) => s.id).filter(Boolean))
)
// 过滤维度：
//   custom    = 全部自建（= synced + unsynced，默认）
//   synced    = 已同步的自建
//   unsynced  = 未同步的自建（SkillForge DB 有但设备没有）
//   system    = 系统/插件自带
//   all       = 全部
const sourceFilter = ref<'custom' | 'synced' | 'unsynced' | 'system' | 'all'>('custom')
// 同步中的 skill id 集合（按钮 loading）
const syncingSkills = ref<Set<string>>(new Set())

function keyOf(a: any): string {
  return String(a?.id || a?.agent_id || a?.agentId || a?.name || '')
}
function nameOf(a: any): string {
  return a?.identity?.name || a?.displayName || a?.display_name || a?.name || a?.id || '(未命名)'
}
function skillPrimaryName(s: any): string {
  return s?.display_name || s?.title || s?.name || s?.id || s?.skill_id || '-'
}
function skillSecondary(s: any): string {
  const primary = skillPrimaryName(s)
  const id = s?.id || s?.skill_id || ''
  if (!id || id === primary) return ''
  return id
}
function isSkillforgeSkill(s: any): boolean {
  const id = s?.id || s?.skill_id || s?.name
  return !!id && skillforgeSkillIds.value.has(id)
}

function shortCommit(value: any): string {
  const text = String(value || '').trim()
  if (!text) return ''
  return text.length > 8 ? text.slice(0, 8) : text
}

function skillGitFull(s: any): string {
  return String(
    s?.deployed_git_commit_full ||
    s?.docker_git_commit_full ||
    s?.skill_git_commit_full ||
    s?.git_commit_full ||
    s?.db_git_commit ||
    (String(s?.git_commit || '').length > 8 ? s.git_commit : '') ||
    ''
  ).trim()
}

function skillGitShort(s: any): string {
  return String(
    s?.deployed_git_commit ||
    s?.docker_git_commit ||
    s?.skill_git_commit ||
    s?.git_commit ||
    shortCommit(skillGitFull(s)) ||
    ''
  ).trim()
}

function skillCurrentGitFull(s: any): string {
  return String(s?.skill_git_commit_full || s?.git_commit_full || s?.db_git_commit || '').trim()
}

function skillDeployedGitFull(s: any): string {
  return String(s?.deployed_git_commit_full || '').trim()
}

function skillGitState(s: any): 'ok' | 'stale' | 'unknown' | 'unsynced' {
  if (s?._unsynced) return 'unsynced'
  const deployed = skillDeployedGitFull(s)
  const current = skillCurrentGitFull(s)
  if (deployed && current && deployed !== current) return 'stale'
  if (isSkillforgeSkill(s) && !deployed && !s?.sync_completed_at) return 'unknown'
  return 'ok'
}

function skillGitTooltip(s: any): string {
  const lines: string[] = []
  const full = skillGitFull(s)
  const deployed = skillDeployedGitFull(s)
  const current = skillCurrentGitFull(s)
  if (deployed) lines.push(`节点同步 commit: ${deployed}`)
  else if (full) lines.push(`Skill Git commit: ${full}`)
  if (current && deployed && current !== deployed) lines.push(`当前仓库 commit: ${current}`)
  if (s?.sync_version_tag) lines.push(`发布标签: ${s.sync_version_tag}`)
  if (s?.sync_completed_at) lines.push(`同步时间: ${formatTimeFull(s.sync_completed_at)}`)
  if (s?.current_version) lines.push(`业务版本: ${s.current_version}`)
  if (s?._unsynced) lines.push('当前 agent 未同步')
  if (!deployed && isSkillforgeSkill(s) && !s?._unsynced) lines.push('旧记录没有保存节点 commit，显示当前仓库版本')
  return lines.join('\n')
}

function versionTuple(value: any): number[] {
  return String(value || '')
    .trim()
    .replace(/^v/i, '')
    .split('.')
    .map((item) => Number.parseInt(item, 10))
    .filter((item) => Number.isFinite(item))
}

function compareVersions(a: any, b: any): number {
  const av = versionTuple(a)
  const bv = versionTuple(b)
  const len = Math.max(av.length, bv.length)
  for (let i = 0; i < len; i += 1) {
    const left = av[i] || 0
    const right = bv[i] || 0
    if (left !== right) return left - right
  }
  return 0
}

function bridgeNeedsUpdate(record: any): boolean {
  if (!record?.bridge_version || !record?.bridge_latest_version) return false
  return Boolean(record.bridge_update_available) || compareVersions(record.bridge_version, record.bridge_latest_version) < 0
}

function bridgeVersionText(record: any): string {
  const current = record?.bridge_version || '-'
  const latest = record?.bridge_latest_version
  if (!latest || current === '-') return current
  return bridgeNeedsUpdate(record) ? `${current} / 自动更新到 ${latest}` : `${current} / 已是最新`
}

function runtimeTypeText(record: any): string {
  const kind = String(record?.runtime_type || record?.bridge_gateway_kind || record?.agent_type || '').toLowerCase()
  if (kind === 'openclaw') return 'OpenClaw'
  if (kind === 'openclaw-cn') return 'OpenClaw CN'
  if (kind === 'aiclaw') return 'AIClaw'
  if (kind === 'hermes') return 'Hermes'
  return kind || '-'
}

function agentTagColor(record: any): string {
  const kind = String(record?.runtime_type || record?.bridge_gateway_kind || record?.agent_type || '').toLowerCase()
  if (kind === 'hermes') return 'purple'
  if (kind.startsWith('openclaw')) return 'green'
  return 'arcoblue'
}

function platformText(value?: string | null): string {
  const platform = String(value || '').toLowerCase()
  if (platform === 'linux') return 'Linux'
  if (platform === 'darwin') return 'macOS'
  if (platform === 'win32' || platform === 'windows') return 'Windows'
  return value || ''
}

// 表格 row-key：区分已同步（设备上的 skill）vs 未同步（SkillForge 伪对象），
// 避免两者同名导致 Vue 渲染 key 冲突
function skillRowKey(record: any): string {
  const id = record?.id || record?.skill_id || record?.name || ''
  return record?._unsynced ? `draft:${id}` : `dev:${id}`
}

const selectedAgent = computed(() => agents.value.find((a) => keyOf(a) === selectedKey.value) || null)
const currentSkills = computed(() => (selectedKey.value ? skillsByAgent.value[selectedKey.value] || [] : []))
const visibleSkillPool = computed(() => [...currentSkills.value, ...unsyncedSkillforgeSkills.value])

// 当前设备上已加载的 skill 名字集合（用于判断哪些 SkillForge skill 尚未同步到此 agent）
const currentSkillNames = computed<Set<string>>(() => {
  return new Set(
    currentSkills.value
      .map((s: any) => s?.id || s?.skill_id || s?.name)
      .filter(Boolean)
  )
})

// 未同步的 SkillForge skill（SkillForge DB 有但设备没有）
// 合成伪 skill 对象，带 `_unsynced: true` 标志供渲染判断
const unsyncedSkillforgeSkills = computed<any[]>(() => {
  return skillforgeSkills.value
    .filter((s: any) => !currentSkillNames.value.has(s.id))
    .map((s: any) => ({ ...s, _unsynced: true }))
})

// 已同步的自建 skill（在设备上 + SkillForge 认领）
const syncedCustomSkills = computed<any[]>(() =>
  currentSkills.value.filter(isSkillforgeSkill)
)
// 按来源分类的计数（用于 tabs 显示）
const sourceCounts = computed(() => {
  const synced = syncedCustomSkills.value.length
  const unsynced = unsyncedSkillforgeSkills.value.length
  const system = currentSkills.value.length - synced
  return {
    custom: synced + unsynced,     // 全部自建（= 已同步 + 未同步）
    synced,                         // 已同步自建
    unsynced,                       // 未同步自建
    system,                         // 系统
    all: currentSkills.value.length + unsynced,  // 全部（含未同步）
  }
})

const filteredSkills = computed(() => {
  // 先根据来源选基础列表
  let list: any[]
  if (sourceFilter.value === 'custom') {
    // 自建 = 已同步（来自 AIClaw）+ 未同步（仅存于 SkillForge）
    list = [...syncedCustomSkills.value, ...unsyncedSkillforgeSkills.value]
  } else if (sourceFilter.value === 'synced') {
    list = syncedCustomSkills.value
  } else if (sourceFilter.value === 'unsynced') {
    list = unsyncedSkillforgeSkills.value
  } else if (sourceFilter.value === 'system') {
    list = currentSkills.value.filter((s) => !isSkillforgeSkill(s))
  } else {
    // all：当前 agent 的全部 + 未同步（给完整视图）
    list = [...currentSkills.value, ...unsyncedSkillforgeSkills.value]
  }
  // 搜索过滤
  if (skillFilter.value) {
    const q = skillFilter.value.toLowerCase()
    list = list.filter((s: any) => {
      const p = skillPrimaryName(s).toLowerCase()
      const sub = skillSecondary(s).toLowerCase()
      return p.includes(q) || sub.includes(q)
    })
  }
  return list
})

// 左侧 agent 的 badge：显示"自建/全部"，更贴合业务关心
function skillCountForAgent(a: any): number | null {
  const key = keyOf(a)
  if (!key) return null
  const list = skillsByAgent.value[key]
  if (!Array.isArray(list)) return null
  return list.filter(isSkillforgeSkill).length
}
function totalSkillCountForAgent(a: any): number | null {
  const key = keyOf(a)
  if (!key) return null
  const list = skillsByAgent.value[key]
  return Array.isArray(list) ? list.length : null
}

const emptyDescription = computed(() => {
  const search = skillFilter.value ? `（且无匹配 "${skillFilter.value}"）` : ''
  switch (sourceFilter.value) {
    case 'custom':
      return `SkillForge 平台暂无 skill${search}`
    case 'synced':
      return `该 agent 未加载任何 SkillForge skill${search}`
    case 'unsynced':
      return `SkillForge skill 都已同步到设备${search}`
    case 'system':
      return `该 agent 无系统 skill${search}`
    default:
      return `无匹配 "${skillFilter.value}" 的 skill`
  }
})

const columnVisibility = computed(() => ({
  version: visibleSkillPool.value.some((s: any) => s.version || s.current_version),
  git: visibleSkillPool.value.some((s: any) => skillGitShort(s)),
  status: currentSkills.value.some((s: any) => s.status),
}))

const infoItems = computed(() => {
  const i: any = instance.value || {}
  return [
    { label: '实例 ID', value: i.id || instanceId.value, mono: true },
    { label: '部门', value: i.department || '-' },
    { label: 'Agent 类型', value: runtimeTypeText(i) },
    { label: '用途', value: agentPurposeLabel(i.agent_purpose) },
    { label: 'Gateway', value: i.bridge_gateway_kind ? `${i.bridge_gateway_kind}${i.bridge_gateway_version ? ' ' + i.bridge_gateway_version : ''}` : '-' },
    { label: '设备平台', value: platformText(i.bridge_platform) || '-' },
    { label: 'Bridge 版本', value: bridgeVersionText(i), mono: true },
    { label: '设备指纹', value: i.bridge_fingerprint || '-', mono: true },
    { label: 'Skills 目录', value: i.bridge_skills_dir || '-', mono: true },
    { label: 'Enrollment 过期', value: i.enrollment_expires_at ? formatTimeFull(i.enrollment_expires_at) : '-' },
    { label: '最近心跳', value: i.last_heartbeat ? formatTimeFull(i.last_heartbeat) : '-' },
  ]
})

// 硬件资源：从 capabilities 接口取 disk/memory/gpu
interface DiskEntry { mount: string; total_gb: number; used_gb: number; avail_gb: number; use_pct: number }
interface GpuEntry { name: string; vram_total_mb: number; vram_used_mb: number; vram_free_mb: number; gpu_util_pct: number }
const TRAINING_TASK_LABELS: Record<string, string> = {
  lora: 'LoRA',
  qlora: 'QLoRA',
  eval: '评估',
  merge: '合并',
  inference: '推理',
}

function agentPurposeValue(value: any): string {
  const purpose = String(value || 'skill_runtime').toLowerCase()
  return ['skill_runtime', 'analysis', 'training', 'media', 'mixed'].includes(purpose) ? purpose : 'skill_runtime'
}

function agentPurposeLabel(value: any): string {
  const labels: Record<string, string> = {
    skill_runtime: '部门执行',
    analysis: '分析 Agent',
    training: '训练 Agent',
    media: '媒体生成节点',
    mixed: '混合 Agent',
  }
  return labels[agentPurposeValue(value)] || '部门执行'
}

function agentPurposeColor(value: any): string {
  const purpose = agentPurposeValue(value)
  if (purpose === 'analysis') return 'arcoblue'
  if (purpose === 'training') return 'green'
  if (purpose === 'media') return 'purple'
  if (purpose === 'mixed') return 'orange'
  return 'gray'
}

const diskEntries = computed<DiskEntry[]>(() => {
  const disk = capabilities.value?.disk
  if (!disk || typeof disk !== 'object') return []
  return Object.entries(disk).map(([mount, info]: [string, any]) => ({
    mount,
    total_gb: info?.total_gb ?? 0,
    used_gb: info?.used_gb ?? 0,
    avail_gb: info?.avail_gb ?? 0,
    use_pct: info?.use_pct ?? 0,
  }))
})

const memInfo = computed(() => {
  const mem = capabilities.value?.memory
  if (!mem || typeof mem !== 'object') return null
  const total = mem.total_gb ?? 0
  const avail = mem.avail_gb ?? 0
  return {
    total_gb: total,
    used_gb: +(total - avail).toFixed(1),
    used_pct: mem.used_pct ?? 0,
  }
})

const gpuEntries = computed<GpuEntry[]>(() => {
  const gpus = capabilities.value?.gpu
  if (!Array.isArray(gpus)) return []
  return gpus.map((g: any) => ({
    name: g?.name || 'Unknown GPU',
    vram_total_mb: g?.vram_total_mb ?? 0,
    vram_used_mb: g?.vram_used_mb ?? 0,
    vram_free_mb: g?.vram_free_mb ?? 0,
    gpu_util_pct: g?.gpu_util_pct ?? 0,
  }))
})

function safeCount(value: any): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.max(0, Math.floor(parsed)) : 0
}

const trainingCapability = computed<Record<string, any> | null>(() => {
  const raw = capabilities.value?.training
  return raw && typeof raw === 'object' ? raw : null
})
const activeTrainingJobs = computed<any[]>(() => {
  const fromTraining = instance.value?.training?.active_jobs
  const jobs = Array.isArray(fromTraining) ? fromTraining : instance.value?.active_training_jobs
  return Array.isArray(jobs) ? jobs : []
})
const activeTrainingJobCount = computed(() => {
  const fromTraining = Number(instance.value?.training?.active_jobs_count || 0)
  const topLevel = Number(instance.value?.active_training_jobs_count || 0)
  return Math.max(fromTraining, topLevel, activeTrainingJobs.value.length)
})

const trainingSupportedTasks = computed<string[]>(() => {
  const raw = trainingCapability.value?.supported_tasks
  if (!Array.isArray(raw)) return []
  return raw
    .map((item: any) => String(item || '').trim().toLowerCase())
    .filter((item: string) => Boolean(TRAINING_TASK_LABELS[item]))
})

const trainingGatewayReady = computed(() => Boolean(trainingCapability.value?.gateway || gpuEntries.value.length))
const trainingGpuCount = computed(() => Math.max(safeCount(trainingCapability.value?.gpu_count), gpuEntries.value.length))
const trainingWorkerCount = computed(() => Math.max(safeCount(trainingCapability.value?.worker_count), trainingGpuCount.value))
const trainingConsolePath = computed(() => `/training?gateway=${encodeURIComponent(instanceId.value)}`)

const showMediaBootstrap = computed(() => {
  const purpose = agentPurposeValue(instance.value?.agent_purpose)
  return purpose === 'media' || purpose === 'mixed' || Boolean(capabilities.value?.media)
})
const mediaBootstrapUnavailable = computed(() => !mediaBootstrap.value && !Boolean(instance.value?.bridge_online))
const mediaBootstrapStatus = computed(() => mediaBootstrapUnavailable.value
  ? 'unavailable'
  : String(mediaBootstrap.value?.status || 'not_started').toLowerCase())
const MEDIA_BOOTSTRAP_ACTIVE_STATUSES = [
  'preflighting', 'preparing_comfyui', 'installing', 'downloading', 'verifying', 'configuring', 'starting', 'self_testing',
]
const mediaBootstrapHasWorkerState = computed(() => Object.prototype.hasOwnProperty.call(mediaBootstrap.value || {}, 'worker_active'))
const mediaBootstrapRunning = computed(() => mediaBootstrapHasWorkerState.value
  ? Boolean(mediaBootstrap.value?.worker_active)
  : MEDIA_BOOTSTRAP_ACTIVE_STATUSES.includes(mediaBootstrapStatus.value))
const mediaBootstrapPaused = computed(() => mediaBootstrapHasWorkerState.value
  && !Boolean(mediaBootstrap.value?.worker_active)
  && MEDIA_BOOTSTRAP_ACTIVE_STATUSES.includes(mediaBootstrapStatus.value))
const mediaBootstrapProgress = computed(() => {
  const value = Number(mediaBootstrap.value?.progress_percent || 0)
  return Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : 0
})
const mediaBootstrapProgressRatio = computed(() => Number((mediaBootstrapProgress.value / 100).toFixed(4)))
const mediaBootstrapError = computed(() => String(mediaBootstrap.value?.error || '').trim())
const mediaSupportedModes = computed<string[]>(() => {
  const modes = capabilities.value?.media?.supported_modes
  return Array.isArray(modes) ? modes.map((item: any) => String(item || '')).filter(Boolean) : []
})
const mediaBootstrapStatusLabel = computed(() => ({
  not_started: '未安装',
  preflighting: '环境检查',
  preparing_comfyui: '安装 ComfyUI',
  installing: '安装依赖',
  downloading: '下载模型',
  verifying: '校验模型',
  configuring: '写入配置',
  starting: '启动服务',
  self_testing: '能力自检',
  succeeded: '已就绪',
  failed: '安装失败',
  cancelled: '已取消',
  unavailable: '等待连接',
}[mediaBootstrapStatus.value] || mediaBootstrapStatus.value) + (mediaBootstrapPaused.value ? '（已暂停）' : ''))
const mediaBootstrapStatusColor = computed(() => {
  if (mediaBootstrapStatus.value === 'succeeded') return 'green'
  if (mediaBootstrapStatus.value === 'failed') return 'red'
  if (mediaBootstrapStatus.value === 'cancelled') return 'gray'
  if (mediaBootstrapRunning.value) return 'orange'
  return 'gray'
})

function formatBytes(value: any): string {
  const bytes = Number(value || 0)
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / (1024 ** index)).toFixed(index >= 3 ? 2 : 1)} ${units[index]}`
}

function formatSpeed(value: any): string {
  const speed = Number(value || 0)
  return speed > 0 ? `${formatBytes(speed)}/s` : '-'
}

function formatEta(value: any): string {
  if (value === null || value === undefined || value === '') return '-'
  const seconds = Number(value)
  if (!Number.isFinite(seconds) || seconds < 0) return '-'
  if (seconds < 60) return `${Math.ceil(seconds)} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${Math.ceil(seconds % 60)} 秒`
  const hours = Math.floor(seconds / 3600)
  return `${hours} 小时 ${Math.ceil((seconds % 3600) / 60)} 分`
}

function mediaModeLabel(value: string): string {
  return ({ text_to_video: '文生视频', image_to_video: '图生视频', reference_to_video: '参考复刻' } as Record<string, string>)[value] || value
}

async function loadMediaBootstrap(silent = false) {
  if (!showMediaBootstrap.value || !instance.value?.bridge_online) {
    mediaBootstrapLoadError.value = ''
    return
  }
  if (!silent) mediaBootstrapLoading.value = true
  try {
    mediaBootstrap.value = await aiclawApi.getMediaBootstrap(instanceId.value)
    mediaBootstrapLoadError.value = ''
    if (mediaBootstrapRunning.value) startMediaBootstrapPolling()
    else stopMediaBootstrapPolling()
  } catch (e: any) {
    mediaBootstrapLoadError.value = e?._message || 'Bridge 暂时不可用，无法读取 H3 安装状态'
    if (!silent) Message.error(e?._message || '读取 H3 安装状态失败')
  } finally {
    mediaBootstrapLoading.value = false
  }
}

function startMediaBootstrapPolling() {
  if (mediaBootstrapTimer) return
  mediaBootstrapTimer = setInterval(() => loadMediaBootstrap(true), 5000)
}

function stopMediaBootstrapPolling() {
  if (!mediaBootstrapTimer) return
  clearInterval(mediaBootstrapTimer)
  mediaBootstrapTimer = null
}

function confirmStartMediaBootstrap() {
  Modal.confirm({
    title: '安装 MiniMax H3 全模式环境',
    content: '将从官方白名单源下载约 63.4 GB 模型，固定限速 3 MB/s，预计 6 小时以上。继续表示已确认模型许可和使用条件。',
    okText: '确认并开始',
    cancelText: '取消',
    onOk: startMediaBootstrap,
  })
}

async function startMediaBootstrap() {
  mediaBootstrapActionLoading.value = true
  try {
    mediaBootstrap.value = await aiclawApi.startMediaBootstrap(instanceId.value, {
      profile: 'h3_all_modes_v1',
      bandwidth_limit_mbps: 3,
      accept_license: true,
    })
    Message.success('H3 安装任务已启动，可中断续传')
    startMediaBootstrapPolling()
  } catch (e: any) {
    Message.error(e?._message || '启动 H3 安装失败')
  } finally {
    mediaBootstrapActionLoading.value = false
  }
}

async function cancelMediaBootstrap() {
  mediaBootstrapActionLoading.value = true
  try {
    mediaBootstrap.value = await aiclawApi.cancelMediaBootstrap(instanceId.value)
    Message.success('已请求取消 H3 安装')
    await loadMediaBootstrap(true)
  } catch (e: any) {
    Message.error(e?._message || '取消 H3 安装失败')
  } finally {
    mediaBootstrapActionLoading.value = false
  }
}

function trainingTaskLabel(value: string): string {
  return TRAINING_TASK_LABELS[value] || value
}

function trainingTaskColor(value: string): string {
  if (value === 'eval') return 'green'
  if (value === 'merge') return 'purple'
  if (value === 'inference') return 'orange'
  return 'arcoblue'
}

async function loadCapabilities() {
  try {
    capabilities.value = await aiclawApi.capabilities(instanceId.value)
  } catch {
    capabilities.value = null
  }
}

async function loadInstance() {
  loading.value = true
  try {
    instance.value = await aiclawApi.getInstance(instanceId.value)
  } catch (e: any) {
    Message.error(e?._message || '加载实例信息失败')
  } finally {
    loading.value = false
  }
}

async function loadSkillforgeSkills() {
  // 后端 /api/skills/ 的 page_size 上限是 200；如果将来 SkillForge skill 超过 200，
  // 循环翻页把所有页拉完
  const all: any[] = []
  let page = 1
  const pageSize = 200
  try {
    while (true) {
      const res = await skillApi.list({ page, page_size: pageSize })
      const items = res?.items || []
      all.push(...items)
      const total = res?.total ?? items.length
      if (items.length < pageSize || all.length >= total) break
      page += 1
      if (page > 50) break  // 保险：最多翻 50 页 = 10000 条
    }
    skillforgeSkills.value = all
  } catch (e) {
    console.warn('[AgentDeviceDetail] 加载 SkillForge skill 列表失败', e)
    skillforgeSkills.value = []
  }
}

async function loadAgents() {
  loadingAgents.value = true
  loadFailed.value = false
  loadError.value = ''
  loadErrorCode.value = ''
  loadErrorDetail.value = {}
  try {
    const res = await aiclawApi.listAgents(instanceId.value)
    agents.value = res?.items || []
    // 默认选中第一个
    if (agents.value.length && !selectedKey.value) {
      await selectAgent(agents.value[0])
    }
    // 后台预加载其他 agent 的 skill 计数（用于左侧 badge）
    for (const a of agents.value) {
      const k = keyOf(a)
      if (k && skillsByAgent.value[k] === undefined) {
        // 不 await，让它后台跑
        loadSkills(k, true)
      }
    }
  } catch (e: any) {
    loadFailed.value = true
    loadError.value = e?._message || '未知错误'
    loadErrorCode.value = e?._code || e?.response?.data?.error?.code || ''
    loadErrorDetail.value = e?.response?.data?.error?.detail || e?._detail || {}
    agents.value = []
  } finally {
    loadingAgents.value = false
  }
}

async function selectAgent(agent: any) {
  const key = keyOf(agent)
  if (!key) return
  selectedKey.value = key
  skillFilter.value = ''  // 切换 agent 清搜索框
  if (skillsByAgent.value[key] === undefined) {
    await loadSkills(key)
  }
}

async function loadSkills(agentKey: string, silent = false) {
  if (!silent) loadingSkills.value = true
  try {
    const res = await aiclawApi.listSkills(instanceId.value, agentKey)
    skillsByAgent.value = { ...skillsByAgent.value, [agentKey]: res?.items || [] }
  } catch (e: any) {
    if (!silent) Message.error(e?._message || '加载 skill 失败')
    skillsByAgent.value = { ...skillsByAgent.value, [agentKey]: [] }
  } finally {
    if (!silent) loadingSkills.value = false
  }
}

async function reloadCurrentSkills() {
  if (!selectedKey.value) return
  delete skillsByAgent.value[selectedKey.value]
  await loadSkills(selectedKey.value)
}

async function removeSkill(record: any) {
  const skillId = record.id || record.skill_id || record.name
  if (!skillId) return
  try {
    await aiclawApi.removeSkillFromDevice(instanceId.value, skillId)
    Message.success(`已从设备移除 ${skillId}`)
    // 删 skill 影响所有 agent 的 skill 集合（共享 skills dir），清全部缓存重新拉
    const selected = selectedKey.value
    skillsByAgent.value = {}
    for (const a of agents.value) {
      const k = keyOf(a)
      if (k) loadSkills(k, k !== selected)
    }
  } catch (e: any) {
    Message.error(e?._message || '删除失败')
  }
}

// 自建 skill 直接跳 SkillStudio 编辑（新标签）
// 注意：window.open 必须在用户点击的同步栈里调用，否则浏览器会拦截
function buildEditUrl(skillId: string): string {
  return `/skills/${encodeURIComponent(skillId)}?from=agent-device&instance=${encodeURIComponent(instanceId.value)}`
}

function editSkill(record: any) {
  const skillId = record.id || record.skill_id || record.name
  if (!skillId) return
  window.open(buildEditUrl(skillId), '_blank')
}

// 系统 skill → 先从设备导入到 SkillForge，再跳编辑
const importingSkills = ref<Set<string>>(new Set())

function importAndEditSkill(record: any) {
  const skillId = record.id || record.name
  if (!skillId) return
  Modal.confirm({
    title: `从设备导入 ${skillId} 到 SkillForge？`,
    content:
      '导入后会：\n' +
      '1. 从设备拉完整 skill 目录（所有文件）\n' +
      '2. 写入 skills-repo 并 git commit（初始版本）\n' +
      '3. 在 SkillForge 创建 draft 状态的 skill 记录\n' +
      '4. 新标签打开 SkillStudio 让你 AI 辅助编辑\n\n' +
      '注意：部分系统 skill 是 AIClaw 内置元数据（没有源文件），可能导入失败；' +
      '失败时可以在 SkillForge 基于同名从零创建。',
    width: 500,
    okText: '导入并编辑',
    cancelText: '取消',
    onOk: async () => {
      // P0: 异步回调里 window.open 会被浏览器拦截 → 预先打开占位窗口再跳转
      const placeholder = window.open('', '_blank')
      if (placeholder) {
        placeholder.document.write(
          `<html><head><title>导入 ${skillId}...</title></head>` +
          `<body class="skillforge-export-body">` +
          `<h2>导入中</h2><p>正在从设备拉取 skill <code>${skillId}</code> 的完整文件，请稍候...</p>` +
          `</body></html>`
        )
      }
      const set = new Set(importingSkills.value)
      set.add(skillId)
      importingSkills.value = set
      try {
        await aiclawApi.importSkillFromDevice(instanceId.value, skillId)
        Message.success(`已导入 ${skillId} 到 SkillForge`)
        loadSkillforgeSkills()
        const url = buildEditUrl(skillId)
        if (placeholder) placeholder.location.href = url
        else window.open(url, '_blank')  // fallback：占位窗口被拦截时直接开
      } catch (e: any) {
        // 清理占位窗口
        if (placeholder) try { placeholder.close() } catch { /* ignore */ }
        const code = e?._code || e?.response?.data?.error?.code
        if (code === 'SKILL_FILE_NOT_FOUND') {
          // AIClaw 内置/插件 skill，bridge 读不到源文件
          Modal.warning({
            title: '无法导入此 skill',
            content:
              `"${skillId}" 是 AIClaw 内置或插件提供的 skill，只有元数据，` +
              `bridge 无法从设备读取源文件。\n\n` +
              `建议：在 SkillForge 基于同名从零创建一个新 skill，然后同步到设备使用。`,
            width: 460,
          })
        } else {
          Message.error(e?._message || `导入失败: ${e?.message || e}`)
        }
      } finally {
        const after = new Set(importingSkills.value)
        after.delete(skillId)
        importingSkills.value = after
      }
    },
  })
}

async function syncSkillToDevice(record: any) {
  const skillId = record.id
  if (!skillId) return
  const next = new Set(syncingSkills.value)
  next.add(skillId)
  syncingSkills.value = next
  try {
    await aiclawApi.syncSkill(instanceId.value, skillId)
    Message.success(`已同步 ${skillId} 到设备`)
    // 同步后清所有 agent 的 skill 缓存，重新拉看到新增的 skill
    const selected = selectedKey.value
    skillsByAgent.value = {}
    for (const a of agents.value) {
      const k = keyOf(a)
      if (k) loadSkills(k, k !== selected)
    }
  } catch (e: any) {
    Message.error(e?._message || '同步失败')
  } finally {
    const after = new Set(syncingSkills.value)
    after.delete(skillId)
    syncingSkills.value = after
  }
}

async function refreshAll() {
  selectedKey.value = ''
  skillsByAgent.value = {}
  await Promise.all([loadInstance(), loadAgents(), loadCapabilities()])
  await loadMediaBootstrap(true)
}

onMounted(async () => {
  await Promise.all([loadInstance(), loadSkillforgeSkills(), loadAgents(), loadCapabilities()])
  await loadMediaBootstrap(true)
})

onUnmounted(stopMediaBootstrapPolling)
</script>

<style scoped>
/* ─── page chrome ─── */
.admin-agent-device-detail-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.admin-agent-device-detail-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
}
.admin-agent-device-detail-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.admin-agent-device-detail-page :deep(.page-section-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

/* a-tag → ai-pill 映射 */
.admin-agent-device-detail-page :deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.admin-agent-device-detail-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
.admin-agent-device-detail-page :deep(.arco-tag-color-arcoblue),
.admin-agent-device-detail-page :deep(.arco-tag-color-blue) {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}
.admin-agent-device-detail-page :deep(.arco-tag-color-orange) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.admin-agent-device-detail-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}
.admin-agent-device-detail-page :deep(.arco-tag-color-purple) {
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
  border-color: transparent;
}
.admin-agent-device-detail-page :deep(.arco-tag-color-gray),
.admin-agent-device-detail-page :deep(.arco-tag-color-grey) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}

/* 表格密集化 */
.admin-agent-device-detail-page :deep(.arco-table-th) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 8px 10px !important;
}
.admin-agent-device-detail-page :deep(.arco-table-td) {
  font-size: 12.5px !important;
  color: var(--ai-ink-1) !important;
  padding: 10px 10px !important;
  border-bottom: 1px solid var(--ai-border) !important;
  background: var(--ai-surface) !important;
}
.admin-agent-device-detail-page :deep(.arco-table-tr:hover .arco-table-td),
.admin-agent-device-detail-page :deep(.arco-table-tr-hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

/* descriptions: label/value 对齐 ai 色系 */
.admin-agent-device-detail-page :deep(.arco-descriptions-item-label) {
  color: var(--ai-ink-4) !important;
  font-size: 12px !important;
  font-weight: 500;
  background: var(--ai-surface-2) !important;
  border-color: var(--ai-border) !important;
}
.admin-agent-device-detail-page :deep(.arco-descriptions-item-value) {
  color: var(--ai-ink-1) !important;
  font-size: 12.5px !important;
  background: var(--ai-surface) !important;
  border-color: var(--ai-border) !important;
}

/* 硬件资源 */
.hw-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.hw-line {
  display: flex;
  align-items: center;
  gap: 8px;
}
.hw-label {
  min-width: 60px;
  color: var(--ai-ink-2);
}
.hw-value {
  color: var(--ai-ink-1);
}
.hw-tag {
  margin-left: 4px;
}
.training-capability {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.training-summary,
.training-task-list,
.training-active-list {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.training-active-list :deep(a) {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.media-bootstrap-alert {
  margin-bottom: 12px;
}
.media-bootstrap-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 6px;
  color: var(--ai-ink-3);
  font-size: 12px;
}

/* 工具栏：返回按钮 + 标题 + 状态标签横向排列（与 ExecutionDetail 一致） */
.toolbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  min-width: 0;
}
.toolbar-left .header-titles {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.toolbar-left .page-title {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 420px;
}
.mono {
  font-family: var(--ai-font-mono);
}

.muted {
  color: var(--ai-ink-4);
}

/* master-detail */
.master-detail {
  display: grid;
  grid-template-columns: 300px 1fr;
  min-height: 480px;
}
@media (max-width: 960px) {
  .master-detail {
    grid-template-columns: 1fr;
  }
  .master-panel {
    border-right: none !important;
    border-bottom: 1px solid var(--ai-border);
  }
}

.panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.master-panel {
  border-right: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
}
.detail-panel {
  background: var(--ai-surface);
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--ai-border);
  font-size: 13px;
  font-weight: 600;
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  gap: 8px;
}
.master-panel .panel-head {
  background: var(--ai-surface-2);
}
.panel-head-title {
  display: flex;
  align-items: center;
  gap: 6px;
}
.panel-body {
  flex: 1;
  overflow: auto;
  padding: 8px;
}

/* Agent 列表项 */
.agent-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.agent-item {
  padding: 10px 12px;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
  border: 1px solid transparent;
}
.agent-item:hover {
  background: var(--ai-surface-3);
}
.agent-item.active {
  background: var(--ai-accent-soft);
  border-color: var(--ai-border-2);
}
.agent-row1 {
  display: flex;
  align-items: center;
  gap: 6px;
  justify-content: space-between;
}
.agent-name {
  font-weight: 600;
  color: var(--ai-ink-1);
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.agent-skill-badge {
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.agent-row2 {
  margin-top: 2px;
  font-size: 11px;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 错误态 */
.error-state {
  padding: 32px 20px;
  text-align: left;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 10px;
  max-width: 520px;
  margin: 0 auto;
}
.error-title {
  font-weight: 600;
  color: var(--ai-ink-2);
  text-align: center;
}
.error-hint {
  color: var(--ai-ink-3);
  font-size: 12px;
  text-align: center;
  line-height: 1.5;
}
.error-hints {
  list-style: disc inside;
  margin: 0;
  padding: 8px 12px;
  color: var(--ai-ink-2);
  font-size: 12px;
  background: var(--ai-surface-2);
  border-radius: 4px;
}
.error-hints li {
  margin: 2px 0;
}
.error-bridge-raw {
  max-height: 160px;
  overflow: auto;
  margin: 0;
  padding: 8px 10px;
  background: var(--ai-surface-3);
  border-radius: 4px;
  font-family: var(--ai-font-mono);
  font-size: 11px;
  color: var(--ai-ink-2);
  white-space: pre-wrap;
  word-break: break-word;
}

/* Skill 单元格 */
.skill-cell {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.skill-line-1 {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.skill-primary {
  font-size: 13px;
  color: var(--ai-ink-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.source-badge {
  flex-shrink: 0;
}
.skill-secondary {
  font-size: 11px;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.git-version {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 11px;
  line-height: 18px;
}
.git-version--stale {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
}
.git-version--unknown,
.git-version--unsynced {
  color: var(--ai-ink-4);
}

/* 来源过滤器里的计数样式 */
.source-count {
  display: inline-block;
  margin-left: 4px;
  padding: 0 6px;
  border-radius: 9px;
  background: var(--ai-surface-3);
  font-size: 11px;
  color: var(--ai-ink-2);
  font-weight: 400;
  min-width: 18px;
  text-align: center;
}
.source-count.warn {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
}
.arco-radio-button-checked .source-count {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.arco-radio-button-checked .source-count.warn {
  background: var(--ai-surface-3);
  color: var(--ai-ink-1);
}

/* 左 agent 列表 badge 中的总数样式 */
.agent-skill-badge-total {
  opacity: 0.6;
  margin-left: 2px;
  font-size: 0.9em;
}

/* 过滤器空状态 */
.empty-filter {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 16px 0;
}

/* Admin sweep utilities */
.section-card-gap {
  margin-bottom: 16px;
}
.agent-desc :deep(.arco-descriptions-label) {
  width: 140px;
}
.agents-card :deep(.arco-card-body) {
  padding: 0;
}
.status-select {
  width: 160px;
}
@media (max-width: 900px) {
  .agent-desc :deep(.arco-descriptions-label),
  .status-select {
    width: 100%;
  }
  .master-detail {
    grid-template-columns: 1fr;
  }
}

</style>
