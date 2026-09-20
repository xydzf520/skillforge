<template>
  <div class="admin-agent-devices-page ai-main">
    <div class="admin-agent-devices-pagehead ai-pagehead">
      <div>
        <div class="ai-crumbs">管理后台 · 数据与服务</div>
        <h1 class="ai-title">Agent 终端</h1>
        <p class="ai-sub">管理代理实例与部署到内网机器的 Python Bridge 脚本</p>
      </div>
      <div class="admin-agent-head-actions">
        <button class="ai-btn primary" type="button" @click="openCreateModal">
          <SfShellIcon name="plus" :size="13" />
          <span>新建实例</span>
        </button>
      </div>
    </div>

    <!-- KPI 条：对应设计稿 admin.jsx AdminAgentTerminals 顶部 5 格 strip -->
    <div class="agent-kpi-strip">
      <div class="kpi-cell">
        <div class="kpi-label">部门总数</div>
        <div class="kpi-value">{{ kpi.department_total }}</div>
        <div class="kpi-sub">&nbsp;</div>
      </div>
      <div class="kpi-cell" :class="kpi.exec_missing > 0 ? 'kpi-warn' : 'kpi-ok'">
        <div class="kpi-label">已配执行</div>
        <div class="kpi-value">{{ kpi.exec_configured }}</div>
        <div class="kpi-sub">{{ kpi.exec_missing > 0 ? `${kpi.exec_missing} 缺` : '齐备' }}</div>
      </div>
      <div class="kpi-cell" :class="kpi.analysis_missing > 0 ? 'kpi-bad' : 'kpi-ok'">
        <div class="kpi-label">已配分析</div>
        <div class="kpi-value">{{ kpi.analysis_configured }}</div>
        <div class="kpi-sub">{{ kpi.analysis_missing > 0 ? `${kpi.analysis_missing} 缺` : '齐备' }}</div>
      </div>
      <div class="kpi-cell" :class="kpi.training_missing > 0 ? 'kpi-bad' : 'kpi-ok'">
        <div class="kpi-label">已配训练</div>
        <div class="kpi-value">{{ kpi.training_configured }}</div>
        <div class="kpi-sub">{{ kpi.training_missing > 0 ? `${kpi.training_missing} 缺` : '齐备' }}</div>
      </div>
      <div class="kpi-cell" :class="kpi.bridge_offline > 0 ? 'kpi-warn' : 'kpi-ok'">
        <div class="kpi-label">Bridge 在线</div>
        <div class="kpi-value">{{ kpi.bridge_online }}</div>
        <div class="kpi-sub">{{ kpi.bridge_offline > 0 ? `${kpi.bridge_offline} 离线` : '全部在线' }}</div>
      </div>
    </div>

    <div class="admin-agent-body ai-pagebody">
      <a-card class="agent-devices-table-card ai-card" :bordered="false">
      <div v-if="coverageRows.length" class="coverage-band">
        <div
          v-for="row in coverageRows"
          :key="row.department"
          class="coverage-item"
        >
          <button class="coverage-head" type="button" @click="filterDepartment = row.department">
            <strong>{{ row.department }}</strong>
            <SfTagChip :label="coverageStatusLabel(row.status)" :tone="coverageStatusTone(row.status)" size="sm" />
          </button>
          <div class="coverage-lanes">
            <button
              type="button"
              class="coverage-lane"
              :class="{ muted: !capabilityReady(row, 'skill_runtime') }"
              title="新建部门执行 Agent"
              @click="openCoverageCreate(row, 'skill_runtime')"
            >
              执行 {{ capabilityText(row, 'skill_runtime') }}<SfShellIcon name="plus" :size="11" />
            </button>
            <button
              type="button"
              class="coverage-lane"
              :class="{ muted: !capabilityReady(row, 'analysis') }"
              title="新建分析 Agent"
              @click="openCoverageCreate(row, 'analysis')"
            >
              分析 {{ capabilityText(row, 'analysis') }}<SfShellIcon name="plus" :size="11" />
            </button>
            <button
              type="button"
              class="coverage-lane"
              :class="{ muted: !capabilityReady(row, 'training') }"
              title="新建训练 Agent"
              @click="openCoverageCreate(row, 'training')"
            >
              训练 {{ capabilityText(row, 'training') }}<SfShellIcon name="plus" :size="11" />
            </button>
          </div>
        </div>
      </div>
      <div class="filter-bar">
        <a-space wrap>
          <a-input-search
            v-model="searchKey"
            placeholder="搜索 ID / 名称"
            allow-clear
            class="filter-search"
          />
          <a-select
            v-model="filterDepartment"
            placeholder="全部部门"
            allow-clear
            class="filter-select-sm"
          >
            <a-option v-for="d in departmentOptions" :key="d.value" :value="d.value">{{ d.label }}</a-option>
          </a-select>
          <a-select
            v-model="filterAgentType"
            placeholder="Agent 类型"
            allow-clear
            class="filter-select-sm"
          >
            <a-option value="aiclaw">AIClaw</a-option>
            <a-option value="hermes">Hermes Agent</a-option>
          </a-select>
          <a-select
            v-model="filterPurpose"
            placeholder="用途"
            allow-clear
            class="filter-select"
          >
            <a-option value="skill_runtime">部门执行</a-option>
            <a-option value="analysis">分析 Agent</a-option>
            <a-option value="training">训练 Agent</a-option>
            <a-option value="mixed">混合</a-option>
          </a-select>
          <a-select
            v-model="filterStatus"
            placeholder="连接状态"
            allow-clear
            class="filter-select"
          >
            <a-option value="online">在线</a-option>
            <a-option value="offline">离线</a-option>
            <a-option value="unbound">未绑定公钥</a-option>
          </a-select>
        </a-space>
      </div>

      <a-table
        class="page-list-table clickable-table"
        :scroll="{ x: '100%' }"
        :data="filteredInstances"
        :loading="loading"
        :pagination="false"
        row-key="id"
        @row-click="goDetail"
      >
        <template #columns>
          <a-table-column title="ID" :width="140">
            <template #cell="{ record }">
              <a-link @click="$router.push(`/admin/agent-devices/${record.id}`)">{{ record.id }}</a-link>
            </template>
          </a-table-column>
          <a-table-column title="名称" :width="180">
            <template #cell="{ record }">
              <a class="instance-name-link" @click="$router.push(`/admin/agent-devices/${record.id}`)">
                {{ record.name }}
              </a>
            </template>
          </a-table-column>
          <a-table-column title="部门" data-index="department" :width="120" />
          <a-table-column title="Agent" :width="100">
            <template #cell="{ record }">
              <a-space size="mini" wrap>
                <a-tag :color="agentTagColor(record)" size="small">{{ runtimeTypeText(record) }}</a-tag>
                <SfTagChip
                  :label="agentPurposeLabel(record.agent_purpose)"
                  :tone="agentPurposeTone(record.agent_purpose)"
                  size="sm"
                />
                <a-tag v-if="record.is_platform_default" color="arcoblue" size="small">平台默认</a-tag>
              </a-space>
            </template>
          </a-table-column>
          <a-table-column title="训练" :width="190">
            <template #cell="{ record }">
              <div v-if="trainingSummary(record)" class="training-stack" @click.stop>
                <div class="training-tags">
                  <SfTagChip
                    :label="trainingGatewayLabel(record)"
                    :tone="trainingSummary(record)?.gateway ? 'success' : 'neutral'"
                    size="sm"
                  />
                  <SfTagChip
                    v-if="trainingGpuCount(record)"
                    :label="`GPU ${trainingGpuCount(record)}`"
                    tone="info"
                    size="sm"
                  />
                  <SfTagChip
                    v-if="trainingWorkerCount(record)"
                    :label="`Worker ${trainingWorkerCount(record)}`"
                    tone="info"
                    size="sm"
                  />
                </div>
                <div v-if="trainingTaskList(record).length" class="training-meta">
                  {{ trainingTaskList(record).map(trainingTaskLabel).join(' / ') }}
                </div>
                <div v-if="trainingVramText(record)" class="training-meta mono">
                  {{ trainingVramText(record) }}
                </div>
                <div v-if="trainingActiveJobs(record).length" class="training-active">
                  <SfTagChip
                    :label="`运行中 ${trainingActiveJobCount(record)}`"
                    tone="warning"
                    size="sm"
                  />
                  <a-link
                    v-if="trainingActiveJobs(record)[0]"
                    @click.stop="goTrainingJob(trainingActiveJobs(record)[0].id)"
                  >
                    {{ trainingActiveJobs(record)[0].title }}
                  </a-link>
                </div>
              </div>
              <span v-else-if="trainingActiveJobs(record).length" class="training-active" @click.stop>
                <SfTagChip
                  :label="`运行中 ${trainingActiveJobCount(record)}`"
                  tone="warning"
                  size="sm"
                />
                <a-link
                  v-if="trainingActiveJobs(record)[0]"
                  @click.stop="goTrainingJob(trainingActiveJobs(record)[0].id)"
                >
                  {{ trainingActiveJobs(record)[0].title }}
                </a-link>
              </span>
              <span v-else class="muted-text">未上报</span>
            </template>
          </a-table-column>
          <a-table-column title="连接状态" :width="180">
            <template #cell="{ record }">
              <div class="status-stack">
                <SfTagChip :label="record.bridge_online ? '在线' : '离线'" :tone="record.bridge_online ? 'success' : 'neutral'" size="sm" />
                <SfTagChip :label="record.has_device_pubkey ? '已绑定' : '未绑定'" :tone="record.has_device_pubkey ? 'info' : 'warning'" size="sm" />
                <SfTagChip
                  v-if="record.bridge_version"
                  :label="bridgeVersionLabel(record)"
                  :tone="record.bridge_update_available ? 'warning' : 'neutral'"
                  size="sm"
                />
              </div>
            </template>
          </a-table-column>
          <a-table-column title="操作" :width="300">
            <template #cell="{ record }">
              <a-space wrap @click.stop>
                <a-button type="text" size="small" @click.stop="goDetail(record)">详情</a-button>
                <a-button type="text" size="small" @click.stop="editInstance(record)">编辑</a-button>
                <a-button type="text" size="small" @click.stop="goWorkspace(record.id)">对话</a-button>
                <a-button v-if="trainingSummary(record)?.gateway" type="text" size="small" @click.stop="goTraining(record.id)">训练</a-button>
                <a-button type="text" size="small" @click.stop="downloadScript(record.id)">下载脚本</a-button>
                <a-button v-if="record.bridge_online" type="text" size="small" status="success" @click.stop="reconnectBridge(record.id)">重连</a-button>
                <a-dropdown trigger="click">
                  <a-button type="text" size="small" @click.stop>
                    更多<SfShellIcon name="chev" :size="12" />
                  </a-button>
                  <template #content>
                    <a-doption @click="regenerate(record.id)">重新注册</a-doption>
                    <a-doption @click="resetBinding(record.id)">重置指纹</a-doption>
                    <a-doption @click="rotate(record.id)">
                      <span class="menu-warn">轮换密钥</span>
                    </a-doption>
                    <a-doption @click="revoke(record.id)">
                      <span class="menu-warn">撤销公钥</span>
                    </a-doption>
                    <a-doption>
                      <a-popconfirm content="确认删除该代理实例？" @ok="deleteInstance(record.id)" position="left">
                        <span class="menu-danger">删除</span>
                      </a-popconfirm>
                    </a-doption>
                  </template>
                </a-dropdown>
              </a-space>
            </template>
          </a-table-column>
        </template>
      </a-table>

      <div class="bottom-bar">
        <div class="stats-inline">
          <span class="stat-chip">总实例 <b>{{ instances.length }}</b></span>
          <span class="stat-chip stat-green">在线 <b>{{ onlineCount }}</b></span>
          <span class="stat-chip stat-blue">已绑定 <b>{{ boundCount }}</b></span>
          <span class="stat-chip stat-orange">待注册 <b>{{ unboundCount }}</b></span>
          <span class="stat-chip stat-blue">分析 <b>{{ analysisAgentCount }}</b></span>
          <span class="stat-chip stat-green">可训练 <b>{{ trainingGatewayCount }}</b></span>
        </div>
      </div>
      </a-card>
    </div>

    <a-modal v-model:visible="showForm" :title="editingId ? '编辑实例' : '新建实例'" @ok="handleSave" :ok-loading="saving" :width="'min(90vw, 520px)'">
      <a-alert type="info" class="modal-alert">
        创建后会弹出一次性 enrollment token 和下载脚本入口。bridge 跟 AIClaw 同机部署是默认模式。
      </a-alert>
      <a-form :model="formData" layout="vertical">
        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="ID" required>
              <a-input v-model="formData.id" placeholder="例如 prod-01" />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="名称" required>
              <a-input v-model="formData.name" placeholder="生产环境主节点" />
            </a-form-item>
          </a-col>
        </a-row>
        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="部门">
              <a-select
                v-model="formData.department"
                placeholder="选择一级部门"
                allow-clear
                allow-search
                :loading="loadingDepartments"
                :disabled="!userStore.isSystemAdmin"
              >
                <a-option v-for="d in departmentOptions" :key="d.value" :value="d.value">{{ d.label }}</a-option>
              </a-select>
              <template #help>
                {{ userStore.isSystemAdmin
                  ? (formData.is_platform_default ? '平台默认节点可不选部门；有部门时仍按任务树一级部门归属。' : '来自任务树一级部门。')
                  : '按当前账号权限自动归属本部门。' }}
              </template>
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="启用">
              <a-switch v-model="formData.is_active" />
            </a-form-item>
          </a-col>
        </a-row>
        <a-form-item v-if="userStore.isSystemAdmin" label="平台默认终端">
          <a-switch v-model="formData.is_platform_default" />
          <template #help>
            当 Skill 所属部门没有可用 Agent 终端时，自动使用该终端兜底下发。
          </template>
        </a-form-item>
        <a-form-item label="Agent 类型">
          <a-radio-group v-model="formData.agent_type" type="button" size="small">
            <a-radio value="aiclaw">AIClaw</a-radio>
            <a-radio value="hermes">Hermes Agent</a-radio>
          </a-radio-group>
          <template #help>
            {{ formData.agent_type === 'hermes'
              ? 'Hermes Agent：通过 HTTP API 通信，Skill 自动转换为 Hermes 格式'
              : 'AIClaw：通过 WebSocket Bridge 通信（默认模式）' }}
          </template>
        </a-form-item>
        <a-form-item label="用途">
          <a-radio-group v-model="formData.agent_purpose" type="button" size="small">
            <a-radio value="skill_runtime">部门执行</a-radio>
            <a-radio value="analysis">分析</a-radio>
            <a-radio value="training">训练</a-radio>
            <a-radio value="media">媒体生成</a-radio>
            <a-radio value="mixed">混合</a-radio>
          </a-radio-group>
          <template #help>
            部门可维护多个 Agent：执行 Skill、承接分析任务、作为训练网关或受控媒体生成节点。媒体专用节点不会接收 Skill 定时与文件同步。
          </template>
        </a-form-item>
        <a-form-item :label="formData.agent_type === 'hermes' ? 'Hermes API URL' : 'AIClaw Gateway URL'">
          <a-input v-model="formData.gateway_url" :placeholder="formData.agent_type === 'hermes' ? 'http://127.0.0.1:3000' : 'ws://127.0.0.1:18789'" />
          <template #help>
            {{ formData.agent_type === 'hermes'
              ? 'Hermes Agent 的 HTTP API 地址。'
              : 'bridge 从这个 URL 连本地 AIClaw。同机部署用 127.0.0.1 即可。' }}
          </template>
        </a-form-item>

        <!-- 高级选项：99% 用户不用展开 -->
        <a-collapse :bordered="false" class="advanced-collapse">
          <a-collapse-item header="高级选项（一般不用动）" :key="1">
            <a-form-item label="AIClaw Auth Token">
              <a-input-password v-model="formData.auth_token" placeholder="留空 — bridge 自动从 /proc 或 ~/.aiclaw/aiclaw.json 发现" />
              <template #help>
                仅当 bridge 跑在容器里且无法访问宿主 /proc 与 ~/.aiclaw 时才填。AIClaw 重启换 token 时不会用到这里。
              </template>
            </a-form-item>
          </a-collapse-item>
        </a-collapse>
      </a-form>
    </a-modal>

    <!-- 一次性 enrollment token 弹窗 -->
    <a-modal
      v-model:visible="enrollmentModal.visible"
      title="Enrollment Token（仅显示一次）"
      :footer="false"
      :mask-closable="false"
      :unmount-on-close="true"
      @cancel="closeEnrollmentModal"
    >
      <a-alert type="warning" class="modal-alert-sm">
        <template #title>请立即下载脚本并部署到内网主机</template>
        关闭弹窗后将无法再次查看完整 token；如错过窗口期请点击「重发 Enrollment」。
      </a-alert>
      <div class="enrollment-meta">
        <div><strong>实例：</strong>{{ enrollmentModal.instanceId }}</div>
        <div>
          <strong>剩余有效期：</strong>
          <span :class="{ 'enroll-expired': enrollmentModal.remaining <= 0 }">
            {{ formatRemaining(enrollmentModal.remaining) }}
          </span>
        </div>
      </div>
      <a-input
        :model-value="enrollmentModal.token"
        readonly
        size="large"
        class="enrollment-token-input"
      />
      <div class="platform-picker">
        <span class="platform-label">目标平台：</span>
        <a-radio-group v-model="enrollmentModal.platform" type="button" size="small">
          <a-radio value="linux">Linux</a-radio>
          <a-radio value="darwin">macOS</a-radio>
        </a-radio-group>
      </div>
      <a-space>
        <a-button @click="copyEnrollmentToken">复制 Token</a-button>
        <a-button type="primary" :loading="enrollmentModal.downloading" @click="downloadFromModal">
          下载脚本
        </a-button>
        <a-button
          v-if="enrollmentModal.platform === 'linux'"
          @click="downloadSystemdFromModal"
        >
          下载 systemd unit
        </a-button>
      </a-space>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { useRoute, useRouter } from 'vue-router'
import { aiclawApi as rawAiclawApi, hallApi as rawHallApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { formatTime, toDate } from '@/utils/format'
import { copyText } from '@/utils/clipboard'
import { SfTagChip } from '@/components/sf'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

const aiclawApi: any = rawAiclawApi
const hallApi: any = rawHallApi
const router: any = useRouter()
const route: any = useRoute()
const userStore = useUserStore()
const loading = ref(false)
const loadingDepartments = ref(false)
const saving = ref(false)
const instances = ref<any[]>([])
const coverageRows = ref<any[]>([])
const kpi = ref({
  department_total: 0,
  exec_configured: 0,
  exec_missing: 0,
  analysis_configured: 0,
  analysis_missing: 0,
  training_configured: 0,
  training_missing: 0,
  bridge_online: 0,
  bridge_offline: 0,
})
const showForm = ref(false)
const editingId = ref('')
const latestEnrollment = ref<Record<string, string>>({})

// 过滤状态
const searchKey = ref('')
const filterDepartment = ref<string | undefined>(undefined)
const filterAgentType = ref<string | undefined>(undefined)
const filterPurpose = ref<string | undefined>(undefined)
const filterStatus = ref<'online' | 'offline' | 'unbound' | undefined>(undefined)
const appliedRoutePrefill = ref('')

type DepartmentOption = {
  id?: string
  label: string
  value: string
}

const taskTreeDepartments = ref<DepartmentOption[]>([])

const currentUserDepartment = computed(() => userStore.userInfo?.department || '')
const currentUserDepartmentId = computed(() => userStore.userInfo?.department_id || '')

const departmentOptions = computed<DepartmentOption[]>(() => {
  const map = new Map<string, DepartmentOption>()
  for (const d of taskTreeDepartments.value) {
    if (d.value) map.set(d.value, d)
  }
  for (const i of instances.value) {
    if (i?.department && !map.has(i.department)) {
      map.set(i.department, { label: i.department, value: i.department })
    }
  }
  const items = Array.from(map.values()).sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
  if (userStore.isSystemAdmin) return items

  const managed = new Set([
    ...(userStore.userInfo?.managed_departments || []),
    ...(userStore.userInfo?.accessible_departments || []),
  ])
  const allowed = items.filter((item) =>
    item.value === currentUserDepartment.value ||
    item.id === currentUserDepartmentId.value ||
    (!!item.id && managed.has(item.id))
  )
  if (allowed.length) return allowed
  if (currentUserDepartment.value) {
    return [{
      id: currentUserDepartmentId.value || undefined,
      label: currentUserDepartment.value,
      value: currentUserDepartment.value,
    }]
  }
  return []
})

const defaultDepartment = computed(() => {
  if (!userStore.isSystemAdmin) return departmentOptions.value[0]?.value || currentUserDepartment.value || ''
  return ''
})

const filteredInstances = computed(() => {
  let list = instances.value
  if (searchKey.value) {
    const k = searchKey.value.toLowerCase()
    list = list.filter(
      (i: any) =>
        (i.id || '').toLowerCase().includes(k) || (i.name || '').toLowerCase().includes(k)
    )
  }
  if (filterDepartment.value) {
    list = list.filter((i: any) => i.department === filterDepartment.value)
  }
  if (filterAgentType.value) {
    list = list.filter((i: any) => (i.agent_type || 'aiclaw') === filterAgentType.value)
  }
  if (filterPurpose.value) {
    list = list.filter((i: any) => agentPurposeValue(i) === filterPurpose.value)
  }
  if (filterStatus.value === 'online') {
    list = list.filter((i: any) => !!i.bridge_online)
  } else if (filterStatus.value === 'offline') {
    list = list.filter((i: any) => !i.bridge_online)
  } else if (filterStatus.value === 'unbound') {
    list = list.filter((i: any) => !i.has_device_pubkey)
  }
  return list
})

const onlineCount = computed(
  () => instances.value.filter((i: any) => i.bridge_online).length
)
const boundCount = computed(
  () => instances.value.filter((i: any) => i.has_device_pubkey).length
)
const unboundCount = computed(
  () => instances.value.filter((i: any) => !i.has_device_pubkey).length
)
const trainingGatewayCount = computed(
  () => instances.value.filter((i: any) => Boolean(trainingSummary(i)?.gateway)).length
)
const analysisAgentCount = computed(
  () => instances.value.filter((i: any) => ['analysis', 'mixed'].includes(agentPurposeValue(i)) || i?.analysis?.agent).length
)

function coverageStatusLabel(value: string): string {
  if (value === 'ready') return '齐备'
  if (value === 'fallback') return '平台兜底'
  return '缺口'
}

function coverageStatusTone(value: string): 'neutral' | 'info' | 'success' | 'warning' {
  if (value === 'ready') return 'success'
  if (value === 'fallback') return 'warning'
  return 'neutral'
}

function capabilityPayload(row: any, key: string): any {
  return row?.capabilities?.[key] || {}
}

function capabilityReady(row: any, key: string): boolean {
  const payload = capabilityPayload(row, key)
  return Boolean(payload.ready || payload.fallback_ready)
}

function capabilityText(row: any, key: string): string {
  const payload = capabilityPayload(row, key)
  if (payload.ready) return `${Number(payload.online || 0)}/${Number(payload.count || 0)}`
  if (payload.fallback_ready) return `兜底 ${Number(payload.fallback_count || 0)}`
  return '缺'
}

function agentPurposeValue(record: any): string {
  const value = String(record?.agent_purpose || 'skill_runtime').toLowerCase()
  return ['skill_runtime', 'analysis', 'training', 'media', 'mixed'].includes(value) ? value : 'skill_runtime'
}

function agentPurposeLabel(value: any): string {
  const labels: Record<string, string> = {
    skill_runtime: '部门执行',
    analysis: '分析',
    training: '训练',
    media: '媒体生成',
    mixed: '混合',
  }
  return labels[agentPurposeValue({ agent_purpose: value })] || '部门执行'
}

function agentPurposeTone(value: any): 'neutral' | 'info' | 'success' | 'warning' {
  const purpose = agentPurposeValue({ agent_purpose: value })
  if (purpose === 'analysis') return 'info'
  if (purpose === 'training') return 'success'
  if (purpose === 'media') return 'info'
  if (purpose === 'mixed') return 'warning'
  return 'neutral'
}

function trainingSummary(record: any): Record<string, any> | null {
  const training = record?.training
  return training && typeof training === 'object' ? training : null
}

function trainingTaskList(record: any): string[] {
  const tasks = trainingSummary(record)?.supported_tasks
  return Array.isArray(tasks)
    ? tasks.map((item: any) => String(item || '').trim().toLowerCase()).filter(Boolean)
    : []
}

function trainingTaskLabel(value: string): string {
  const labels: Record<string, string> = {
    lora: 'LoRA',
    qlora: 'QLoRA',
    eval: '评估',
    merge: '合并',
    inference: '推理',
  }
  return labels[value] || value
}

function trainingGatewayLabel(record: any): string {
  return trainingSummary(record)?.gateway ? '训练网关' : '仅能力'
}

function trainingGpuCount(record: any): number {
  return Number(trainingSummary(record)?.gpu_count || 0)
}

function trainingWorkerCount(record: any): number {
  return Number(trainingSummary(record)?.worker_count || 0)
}

function trainingVramText(record: any): string {
  const training = trainingSummary(record)
  const total = Number(training?.vram_total_gb || 0)
  const free = Number(training?.vram_free_gb || 0)
  if (!total) return ''
  return `显存 ${free}/${total} GB`
}

function trainingActiveJobs(record: any): any[] {
  const fromTraining = trainingSummary(record)?.active_jobs
  const jobs = Array.isArray(fromTraining) ? fromTraining : record?.active_training_jobs
  return Array.isArray(jobs) ? jobs : []
}

function trainingActiveJobCount(record: any): number {
  const fromTraining = Number(trainingSummary(record)?.active_jobs_count || 0)
  const topLevel = Number(record?.active_training_jobs_count || 0)
  return Math.max(fromTraining, topLevel, trainingActiveJobs(record).length)
}

function bridgeVersionLabel(record: any): string {
  if (!record?.bridge_version) return ''
  if (record.bridge_update_available && record.bridge_latest_version) {
    return `Bridge ${record.bridge_version}->${record.bridge_latest_version} 自动`
  }
  return `Bridge ${record.bridge_version}`
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
  return 'blue'
}

// 一次性 enrollment 弹窗状态
const enrollmentModal = reactive({
  visible: false,
  token: '',
  instanceId: '',
  expiresAt: 0,
  remaining: 0,
  downloading: false,
  platform: 'linux' as 'linux' | 'darwin',
})
let enrollmentTimer: ReturnType<typeof setInterval> | null = null

function formatRemaining(seconds: number) {
  if (!seconds || seconds <= 0) return '已过期'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m} 分 ${String(s).padStart(2, '0')} 秒`
}

function startEnrollmentCountdown() {
  if (enrollmentTimer) clearInterval(enrollmentTimer)
  enrollmentTimer = setInterval(() => {
    enrollmentModal.remaining = Math.max(0, Math.floor((enrollmentModal.expiresAt - Date.now()) / 1000))
    if (enrollmentModal.remaining <= 0 && enrollmentTimer) {
      clearInterval(enrollmentTimer)
      enrollmentTimer = null
    }
  }, 1000)
}

function openEnrollmentModal(id: string, token: string, expiresAtIso: string) {
  enrollmentModal.visible = true
  enrollmentModal.token = token || ''
  enrollmentModal.instanceId = id
  enrollmentModal.platform = 'linux'
  // 项目统一用北京时间解析后端 naive ISO，避免浏览器本地时区影响过期倒计时。
  const expires = expiresAtIso ? toDate(expiresAtIso)?.getTime() : null
  enrollmentModal.expiresAt = expires || Date.now() + 30 * 60 * 1000
  enrollmentModal.remaining = Math.max(0, Math.floor((enrollmentModal.expiresAt - Date.now()) / 1000))
  startEnrollmentCountdown()
}

function closeEnrollmentModal() {
  // 先缓存 id，再清空 state —— 否则 delete latestEnrollment[''] 什么都不做，明文 token 永驻内存
  const id = enrollmentModal.instanceId
  enrollmentModal.visible = false
  enrollmentModal.token = ''
  enrollmentModal.instanceId = ''
  enrollmentModal.remaining = 0
  if (id) {
    delete latestEnrollment.value[id]
  }
  if (enrollmentTimer) {
    clearInterval(enrollmentTimer)
    enrollmentTimer = null
  }
}

async function copyEnrollmentToken() {
  try {
    const ok = await copyText(enrollmentModal.token)
    if (!ok) throw new Error('copy failed')
    Message.success('已复制到剪贴板')
  } catch {
    Message.warning('复制失败，请手动选中复制')
  }
}

async function downloadFromModal() {
  if (!enrollmentModal.token || !enrollmentModal.instanceId) return
  enrollmentModal.downloading = true
  try {
    const platform = enrollmentModal.platform
    const resp = await aiclawApi.downloadBridgeScript(
      enrollmentModal.instanceId,
      enrollmentModal.token,
      platform,
    )
    const blob = new Blob([resp.data], { type: 'text/x-python' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `openclaw_bridge_${enrollmentModal.instanceId}_${platform}.py`
    a.click()
    URL.revokeObjectURL(url)
    Message.success('脚本已下载，请部署后立即运行')
  } catch (e: any) {
    Message.error(e._message || '下载失败')
  } finally {
    enrollmentModal.downloading = false
  }
}

async function downloadSystemdFromModal() {
  if (!enrollmentModal.instanceId) return
  try {
    const resp = await aiclawApi.downloadSystemdUnit(enrollmentModal.instanceId)
    const blob = new Blob([resp.data], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `openclaw-bridge-${enrollmentModal.instanceId}.service`
    a.click()
    URL.revokeObjectURL(url)
    Message.success('systemd unit 已下载')
  } catch (e: any) {
    Message.error(e._message || '下载失败')
  }
}

onBeforeUnmount(() => {
  if (enrollmentTimer) {
    clearInterval(enrollmentTimer)
    enrollmentTimer = null
  }
})
const formData = reactive({
  id: '',
  name: '',
  department: '',
  agent_type: 'aiclaw',
  gateway_url: 'ws://127.0.0.1:18789',
  auth_token: '',
  is_active: true,
  is_platform_default: false,
  agent_purpose: 'skill_runtime',
})

async function loadInstances() {
  loading.value = true
  try {
    const [instanceResult, coverageResult] = await Promise.all([
      aiclawApi.listInstances(),
      aiclawApi.agentCoverage?.(),
    ])
    instances.value = Array.isArray(instanceResult) ? instanceResult : []
    coverageRows.value = Array.isArray(coverageResult?.items) ? coverageResult.items : []
  } catch (e: any) {
    Message.error(e._message || '加载失败')
    instances.value = []
    coverageRows.value = []
  } finally {
    loading.value = false
  }
  // KPI 与列表同源；列表错误不阻断 KPI 拉取
  loadKpi()
}

async function loadKpi() {
  try {
    const data = await aiclawApi.agentKpi?.()
    if (data && typeof data === 'object') {
      kpi.value = {
        department_total: Number(data.department_total || 0),
        exec_configured: Number(data.exec_configured || 0),
        exec_missing: Number(data.exec_missing || 0),
        analysis_configured: Number(data.analysis_configured || 0),
        analysis_missing: Number(data.analysis_missing || 0),
        training_configured: Number(data.training_configured || 0),
        training_missing: Number(data.training_missing || 0),
        bridge_online: Number(data.bridge_online || 0),
        bridge_offline: Number(data.bridge_offline || 0),
      }
    }
  } catch {
    // KPI 拉取失败不打扰用户：保留旧值或初始值
  }
}

async function loadDepartments() {
  loadingDepartments.value = true
  try {
    const res = await hallApi.departments()
    const rows = Array.isArray(res?.departments) ? res.departments : []
    taskTreeDepartments.value = rows
      .map((d: any) => {
        const value = (d?.department || d?.name || '').trim()
        return value ? { id: d?.id, label: value, value } : null
      })
      .filter((d: DepartmentOption | null): d is DepartmentOption => !!d)
  } catch {
    taskTreeDepartments.value = []
  } finally {
    loadingDepartments.value = false
  }
}

function editInstance(record: any) {
  editingId.value = record.id
  Object.assign(formData, {
    id: record.id,
    name: record.name,
    department: record.department || defaultDepartment.value,
    agent_type: record.agent_type || 'aiclaw',
    agent_purpose: agentPurposeValue(record),
    gateway_url: record.gateway_url || 'ws://127.0.0.1:18789',
    auth_token: '',
    is_active: record.is_active,
    is_platform_default: !!record.is_platform_default,
  })
  showForm.value = true
}

function resetForm() {
  editingId.value = ''
  Object.assign(formData, {
    id: '',
    name: '',
    department: defaultDepartment.value,
    agent_type: 'aiclaw',
    agent_purpose: 'skill_runtime',
    gateway_url: 'ws://127.0.0.1:18789',
    auth_token: '',
    is_active: true,
    is_platform_default: false,
  })
}

function openCreateModal() {
  resetForm()
  showForm.value = true
}

function openCoverageCreate(row: any, purpose: string) {
  const department = String(row?.department || defaultDepartment.value || '').trim()
  resetForm()
  Object.assign(formData, {
    department,
    agent_purpose: agentPurposeValue({ agent_purpose: purpose }),
    name: `${department || '部门'}${agentPurposeLabel(purpose)} Agent`,
  })
  if (department) filterDepartment.value = department
  showForm.value = true
}

function routeQueryString(value: unknown): string {
  if (Array.isArray(value)) return String(value[0] || '')
  return String(value || '')
}

function applyRoutePrefill() {
  const query = route?.query || {}
  const department = routeQueryString(query.department || query.dept).trim()
  const purposeValue = routeQueryString(query.purpose || query.agent_purpose).trim()
  const purpose = purposeValue ? agentPurposeValue({ agent_purpose: purposeValue }) : ''
  const create = ['1', 'true', 'yes'].includes(routeQueryString(query.create).trim().toLowerCase())
  const signature = JSON.stringify({ department, purpose, create })
  if (signature === appliedRoutePrefill.value) return
  appliedRoutePrefill.value = signature

  if (department) filterDepartment.value = department
  if (purpose && !create) filterPurpose.value = purpose
  if (!create) return

  resetForm()
  const targetDepartment = department || defaultDepartment.value
  Object.assign(formData, {
    department: targetDepartment,
    agent_purpose: purpose || 'skill_runtime',
    name: `${targetDepartment || '部门'}${agentPurposeLabel(purpose || 'skill_runtime')} Agent`,
  })
  showForm.value = true
}

async function handleSave() {
  saving.value = true
  try {
    const isPlatformScope = userStore.isSystemAdmin && formData.is_platform_default
    const payload = {
      ...formData,
      department: isPlatformScope ? String(formData.department || '').trim() : (formData.department || defaultDepartment.value),
    }
    if (!payload.department && !isPlatformScope) {
      Message.warning('请选择部门')
      return
    }
    let result
    if (editingId.value) {
      result = await aiclawApi.updateInstance(editingId.value, payload)
      Message.success('保存成功')
    } else {
      result = await aiclawApi.createInstance(payload)
      latestEnrollment.value[result.id] = result.enrollment_token
      openEnrollmentModal(result.id, result.enrollment_token, result.expires_at)
    }
    showForm.value = false
    resetForm()
    await loadInstances()
  } catch (e: any) {
    Message.error(e._message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function deleteInstance(id: string) {
  try {
    await aiclawApi.deleteInstance(id)
    Message.success('已删除')
    await loadInstances()
  } catch (e: any) {
    Message.error(e._message || '删除失败')
  }
}

function goDetail(record: any) {
  router.push(`/admin/agent-devices/${record.id}`)
}

function goWorkspace(id: string) {
  router.push(`/aiclaw/instances/${id}`)
}

function goTraining(id: string) {
  router.push(`/training?gateway=${encodeURIComponent(id)}`)
}

function goTrainingJob(id: string) {
  router.push(`/training/jobs/${encodeURIComponent(id)}`)
}

async function regenerate(id: string) {
  try {
    const result = await aiclawApi.regenerateEnrollment(id)
    latestEnrollment.value[id] = result.enrollment_token
    openEnrollmentModal(id, result.enrollment_token, result.expires_at)
  } catch (e: any) {
    Message.error(e._message || '重发失败')
  }
}

async function downloadScript(id: string) {
  // 列表页直接下载：无 token 时重发拿 token，然后让用户在弹窗里选平台下载
  // （平台选择永远在弹窗里发生，列表按钮不直接触发下载）
  const token = latestEnrollment.value[id]
  if (!token) {
    Message.warning('请先点「重发 Enrollment」获取一次性 token，然后在弹窗中选平台下载')
    await regenerate(id)
    return
  }
  // 已经拿到 token 了 —— 也走弹窗让用户选平台，避免列表按钮直出"默认平台"造成误下载
  Message.info('请在弹窗中选择目标平台下载')
  openEnrollmentModal(id, token, '')
}

async function revoke(id: string) {
  try {
    await aiclawApi.revokePubkey(id)
    Message.success('设备公钥已撤销')
    await loadInstances()
  } catch (e: any) {
    Message.error(e._message || '撤销失败')
  }
}

async function resetBinding(id: string) {
  try {
    await aiclawApi.resetBinding(id)
    Message.success('设备指纹已重置')
    await loadInstances()
  } catch (e: any) {
    Message.error(e._message || '重置失败')
  }
}

async function rotate(id: string) {
  try {
    const result = await aiclawApi.rotateKey(id)
    latestEnrollment.value[id] = result.rotation_token
    Message.success(`已进入轮换阶段，有效至 ${result.expires_at}`)
    await loadInstances()
  } catch (e: any) {
    Message.error(e._message || '轮换失败')
  }
}

async function reconnectBridge(id: string) {
  try {
    await aiclawApi.disconnectBridge(id)
    Message.success('已断开连接，bridge 将自动重连')
  } catch (e: any) {
    Message.error(e._message || '重连失败')
  }
}

onMounted(async () => {
  await Promise.all([loadInstances(), loadDepartments()])
  applyRoutePrefill()
})
watch(() => route?.query, applyRoutePrefill, { deep: true })
</script>

<style scoped>
.admin-agent-devices-page {
  gap: 0;
  padding: 0;
  max-width: none;
  margin: 0;
  min-height: calc(100vh - 92px);
  overflow-x: auto;
}
.admin-agent-devices-pagehead {
  flex: 0 0 auto;
}
.admin-agent-head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  flex-shrink: 0;
}
.admin-agent-head-actions .ai-btn {
  cursor: pointer;
  font-family: var(--ai-font-sans);
}
.admin-agent-head-actions svg {
  width: 13px;
  height: 13px;
  flex: 0 0 13px;
}
.admin-agent-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.agent-devices-table-card {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}
.admin-agent-devices-page :deep(.agent-devices-table-card .arco-card-body) {
  padding: 16px !important;
}
.status-stack {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-start;
}

/* KPI 条：参照设计稿 admin.jsx AdminAgentTerminals 顶部 5 格 strip */
.agent-kpi-strip {
  display: flex;
  align-items: stretch;
  padding: 0;
  margin: 0;
  border-radius: 0;
  background: var(--ai-surface);
  border: 0;
  border-bottom: 1px solid var(--ai-border);
  overflow: hidden;
  font-family: var(--ai-font-sans);
}
.kpi-cell {
  flex: 1 1 0;
  min-width: 0;
  padding: 14px 20px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  border-left: 1px solid var(--ai-border);
  background: transparent;
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
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
}
.kpi-sub {
  color: var(--ai-ink-4);
  font-size: 11px;
  line-height: 1.2;
}
.kpi-cell.kpi-ok .kpi-value { color: var(--ai-ok); }
.kpi-cell.kpi-warn .kpi-value { color: var(--ai-warn); }
.kpi-cell.kpi-bad .kpi-value { color: var(--ai-bad); }
@media (max-width: 640px) {
  .admin-agent-devices-pagehead {
    flex-direction: column;
    align-items: flex-start;
    padding: 16px;
  }
  .admin-agent-head-actions {
    justify-content: flex-start;
    width: 100%;
  }
  .admin-agent-body {
    padding: 16px;
  }
  .admin-agent-devices-page :deep(.agent-devices-table-card .arco-card-body) {
    padding: 12px !important;
  }
  .agent-kpi-strip { flex-wrap: wrap; }
  .kpi-cell {
    flex: 1 1 50%;
    border-left: 0;
    border-top: 1px solid var(--ai-border);
  }
  .kpi-cell:nth-child(-n+2) { border-top: 0; }
}

/* 设计稿 admin.jsx AdminAgentTerminals 部分的部门覆盖卡片网格 */
.coverage-band {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.coverage-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  text-align: left;
  font-family: var(--ai-font-sans);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.coverage-item:hover {
  border-color: var(--ai-border-2);
  box-shadow: var(--ai-shadow-1);
}
.coverage-head,
.coverage-lanes {
  display: flex;
  align-items: center;
  gap: 6px;
}
.coverage-head {
  width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  justify-content: space-between;
  text-align: left;
}
.coverage-head strong {
  overflow: hidden;
  font-size: 12.5px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ai-ink-1);
}
.coverage-lanes {
  flex-wrap: wrap;
  color: var(--ai-ink-3);
  font-size: 11px;
}
.coverage-lane {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 20px;
  padding: 0 7px;
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  cursor: pointer;
  font: inherit;
  font-size: 11px;
  font-weight: 500;
  transition: background 0.15s ease, color 0.15s ease;
}
.coverage-lane:hover {
  background: var(--ai-surface-3);
  color: var(--ai-ink-1);
}
.coverage-lane.muted {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
  font-weight: 500;
}
.coverage-lane.muted:hover {
  background: var(--ai-bad);
  color: var(--ai-surface);
}
.training-stack {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.training-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.training-meta {
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.35;
}
.training-active {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font-size: 12px;
}
.training-active :deep(a) {
  max-width: 110px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.muted-text {
  color: var(--ai-ink-3);
}
.mono {
  font-family: var(--ai-font-mono);
}
.enrollment-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--ai-ink-2);
}
.enroll-expired {
  color: var(--ai-bad);
  font-weight: 600;
}
.platform-picker {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.platform-label {
  font-size: 13px;
  color: var(--ai-ink-2);
}
.bottom-bar { display: flex; align-items: center; justify-content: space-between; margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--ai-border); }
.stats-inline { display: flex; gap: 6px; flex-wrap: wrap; }
.stat-chip { display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; border-radius: 14px; font-size: 12px; cursor: default; background: var(--ai-surface-2); color: var(--ai-ink-3); }
.stat-chip b { font-size: 13px; }

.instance-name-link {
  color: var(--ai-ink-1);
  cursor: pointer;
  text-decoration: none;
  transition: color 0.15s ease;
}
.instance-name-link:hover {
  color: var(--ai-accent-ink);
  text-decoration: underline;
}

/* 整行可点击：鼠标指针提示 + 行 hover 加重背景 */
.clickable-table :deep(.arco-table-tr) {
  cursor: pointer;
}
.clickable-table :deep(.arco-table-tr:hover .arco-table-td) {
  background: var(--ai-surface-2);
}
.stat-green  { background: var(--ai-ok-soft);     color: var(--ai-ok); }
.stat-red    { background: var(--ai-bad-soft);    color: var(--ai-bad); }
.stat-orange { background: var(--ai-warn-soft);   color: var(--ai-warn); }
.stat-blue   { background: var(--ai-accent-soft); color: var(--ai-accent-ink); }
.menu-warn   { color: var(--ai-warn); }
.menu-danger { color: var(--ai-bad); }

/* a-tag → ai-pill 映射 */
.admin-agent-devices-page :deep(.arco-tag) {
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
.admin-agent-devices-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
.admin-agent-devices-page :deep(.arco-tag-color-arcoblue),
.admin-agent-devices-page :deep(.arco-tag-color-blue) {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}
.admin-agent-devices-page :deep(.arco-tag-color-orange) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.admin-agent-devices-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}
.admin-agent-devices-page :deep(.arco-tag-color-gray),
.admin-agent-devices-page :deep(.arco-tag-color-grey) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}

/* 表格密集化 */
.admin-agent-devices-page :deep(.arco-table-th) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11px !important;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 8px 10px !important;
}
.admin-agent-devices-page :deep(.arco-table-td) {
  font-size: 12.5px;
  padding: 10px 10px !important;
  border-bottom: 1px solid var(--ai-border) !important;
}

/* Admin sweep utilities */
.filter-search {
  width: 220px;
}
.filter-select-sm {
  width: 140px;
}
.filter-select {
  width: 150px;
}
.modal-alert {
  margin-bottom: 16px;
}
.modal-alert-sm {
  margin-bottom: 12px;
}
.advanced-collapse {
  margin-top: 4px;
}
.enrollment-token-input {
  margin: 12px 0;
  font-family: var(--ai-font-mono);
}
@media (max-width: 900px) {
  .filter-search,
  .filter-select-sm,
  .filter-select {
    width: 100%;
  }
  .coverage-band {
    grid-template-columns: 1fr;
  }
}

</style>
