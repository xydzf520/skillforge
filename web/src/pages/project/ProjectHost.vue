<template>
  <div class="ai-app project-host-page">
    <aside class="ai-sidebar project-sidebar">
      <div class="sidebar-kicker">
        <span class="sidebar-mark"><SfShellIcon name="folder" /></span>
        <div>
          <strong>Project Host</strong>
          <small>平台项目宿主</small>
        </div>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">项目视图</div>
        <button
          v-if="canViewProjectOverview"
          type="button"
          class="ai-side-item side-button"
          :class="{ active: overviewActive }"
          @click="setProjectView('overview')"
        >
          <SfShellIcon name="trend" class="ic" />
          <span>概览</span>
          <span class="count">{{ statNumber('total') }}</span>
        </button>
        <button
          v-for="item in scopeOptions"
          :key="item.value"
          type="button"
          class="ai-side-item side-button"
          :class="{ active: !overviewActive && scope === item.value }"
          @click="setScope(item.value)"
        >
          <SfShellIcon :name="item.icon" class="ic" />
          <span>{{ item.label }}</span>
          <span v-if="scopeCount(item.value) !== null" class="count">{{ scopeCount(item.value) }}</span>
        </button>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">项目类型</div>
        <button
          v-for="item in typeOptions"
          :key="item.value"
          type="button"
          class="ai-side-item side-button"
          :class="{ active: !overviewActive && scope === item.value }"
          @click="setScope(item.value)"
        >
          <SfShellIcon :name="item.icon" class="ic" />
          <span>{{ item.label }}</span>
          <span class="count">{{ typeCount(item.value) }}</span>
        </button>
      </div>

      <div v-if="departmentOptions.length || departmentFilter" class="ai-side-group">
        <div class="ai-side-label">部门</div>
        <button
          type="button"
          class="ai-side-item side-button"
          :class="{ active: !departmentFilter }"
          @click="setDepartmentFilter('')"
        >
          <SfShellIcon name="dept" class="ic" />
          <span>全部部门</span>
          <span class="count">{{ statNumber('total') }}</span>
        </button>
        <button
          v-for="item in departmentOptions"
          :key="item.name"
          type="button"
          class="ai-side-item side-button"
          :class="{ active: departmentFilter === item.name }"
          @click="setDepartmentFilter(item.name)"
        >
          <SfShellIcon name="dept" class="ic" />
          <span>{{ item.name }}</span>
          <span class="count">{{ item.count }}</span>
        </button>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">AI 运行态</div>
        <button
          v-for="item in runtimeOptions"
          :key="item.value"
          type="button"
          class="ai-side-item side-button"
          :class="{ active: runtimeFilter === item.value }"
          @click="setRuntimeFilter(item.value)"
        >
          <SfShellIcon :name="item.icon" class="ic" />
          <span>{{ item.label }}</span>
          <span class="count">{{ runtimeCount(item.value) }}</span>
        </button>
      </div>

      <div class="sidebar-note">
        <SfShellIcon name="shield" />
        <span>项目以浏览器/静态宿主运行；能力调用与输出统一写入平台 Trace。</span>
      </div>
    </aside>

    <main class="ai-main">
      <header class="ai-pagehead project-pagehead">
        <div>
          <div class="ai-crumbs">平台 / 项目 / 宿主</div>
          <h1 class="ai-title">项目</h1>
          <p class="ai-sub">
            按部门承载 Codex 产出的轻量网页与工具；点击即用，不为每个小功能单独开容器。
          </p>
        </div>
        <div class="page-actions">
          <button type="button" class="ai-btn" :disabled="loading" @click="loadProjects()">
            <SfShellIcon name="refresh" />
            刷新
          </button>
          <button type="button" class="ai-btn primary" @click="openUploadDialog">
            <SfShellIcon name="upload" />
            自动接入项目包
          </button>
        </div>
      </header>

      <section class="ai-pagebody project-pagebody">
        <section class="policy-strip ai-card">
          <div class="policy-item">
            <SfShellIcon name="play" />
            <span>点击即用</span>
          </div>
          <div class="policy-item">
            <SfShellIcon name="shield" />
            <span>默认无容器</span>
          </div>
          <div class="policy-item">
            <SfShellIcon name="database" />
            <span>结果进入 AI 分析平台</span>
          </div>
          <div class="policy-item muted mono">
            {{ capabilityGateway }}
          </div>
        </section>

        <template v-if="overviewActive">
          <section v-if="isMaterialDepartment || isProjectAdmin" class="material-workbench-hero ai-card">
            <div class="material-workbench-copy">
              <span class="overview-kicker">示例品牌内容电商运营部 · AI 视频生产</span>
              <h2>素材工作台</h2>
              <p>输入投流需求后，由 deepseek-v4-flash 与已灰度的微调模型同时拆解，自动路由到 5080 或 RTX PRO 6000；原始 H3 片段经人工审核后进入云视频同步 outbox。</p>
              <div class="material-workbench-tags">
                <span>MiniMax H3</span><span>约 5 秒竖屏</span><span>参考复刻</span><span>双模型对比</span><span>训练闭环</span>
              </div>
            </div>
            <div class="material-workbench-actions">
              <span class="ai-pill dot" :class="materialProject ? 'ok' : 'warn'">{{ materialProject ? '项目已就绪' : '待项目包注册' }}</span>
              <button type="button" class="ai-btn primary" :disabled="!materialProject" @click="materialProject && openProject(materialProject)">
                进入素材工作台
                <SfShellIcon name="arrowr" />
              </button>
            </div>
          </section>

          <section class="overview-hero ai-card">
            <div>
              <span class="overview-kicker">{{ isProjectAdmin ? '管理员概览' : '部门概览' }}</span>
              <h2>{{ isProjectAdmin ? '项目宿主运行总览' : '部门项目运行总览' }}</h2>
              <p>这里聚合项目容量、网关运行态、AI 队列与输出闭环数据；每个运行仍遵守部门权限和 Project Gateway 能力声明。</p>
            </div>
            <button type="button" class="ai-btn" @click="setProjectView('list')">
              查看项目目录
              <SfShellIcon name="arrowr" />
            </button>
          </section>

          <section class="kpi-grid">
            <div v-for="metric in metrics" :key="metric.label" class="kpi-card ai-card">
              <div class="kpi-icon"><SfShellIcon :name="metric.icon" /></div>
              <span>{{ metric.label }}</span>
              <strong class="mono">{{ metric.value }}</strong>
              <small>{{ metric.hint }}</small>
            </div>
          </section>

          <section class="runtime-capacity ai-card">
            <div class="capacity-head">
              <div>
                <span class="t">项目运行容量</span>
                <small>平台统一宿主；目标支持 100 并发打开与千级项目目录。</small>
              </div>
              <span class="ai-pill dot" :class="capacityTone">{{ capacityStateLabel }}</span>
            </div>
            <div class="capacity-grid">
              <div v-for="item in capacityMetrics" :key="item.label" class="capacity-item">
                <span>{{ item.label }}</span>
                <strong class="mono">{{ item.value }}</strong>
                <small>{{ item.hint }}</small>
              </div>
            </div>
          </section>

          <section class="overview-guide ai-card">
            <div class="guide-item">
              <SfShellIcon name="folder" />
              <div>
                <strong>左侧切换项目目录</strong>
                <span>按全部、本部门、类型或运行态筛选，点击卡片直接打开新运行页。</span>
              </div>
            </div>
            <div class="guide-item">
              <SfShellIcon name="shield" />
              <div>
                <strong>项目默认无容器</strong>
                <span>静态网页由平台 iframe 宿主运行，能力调用统一走 Project Gateway。</span>
              </div>
            </div>
            <div class="guide-item">
              <SfShellIcon name="spark" />
              <div>
                <strong>输入输出进入 AI 闭环</strong>
                <span>用户输入、AI/数据能力调用、报告和待办都会写入运行轨迹。</span>
              </div>
            </div>
          </section>
        </template>

        <template v-else>
          <section class="project-toolbar ai-card">
            <a-input-search
              v-model="keyword"
              placeholder="搜索项目、部门、描述或项目 ID"
              allow-clear
              class="project-search"
              @search="loadProjects(1)"
            />
            <div class="toolbar-pills">
              <span class="ai-pill">{{ currentScopeLabel }}</span>
              <span v-if="departmentFilter" class="ai-pill">{{ currentDepartmentLabel }}</span>
              <span class="ai-pill dot" :class="runtimeFilter === 'all' ? '' : runTone(runtimeFilter)">
                {{ currentRuntimeLabel }}
              </span>
              <span class="ai-mono-id">{{ total }} total / page {{ page }}</span>
            </div>
          </section>

          <a-spin :loading="loading" class="project-spin">
            <div v-if="filteredProjects.length" class="project-grid">
              <article
                v-for="project in filteredProjects"
                :key="project.id"
                class="project-card ai-card"
                role="button"
                tabindex="0"
                @click="openProject(project)"
                @keydown.enter.prevent="openProject(project)"
              >
                <div class="card-top">
                  <div class="project-mark">
                    <SfShellIcon :name="typeIcon(project.type)" />
                  </div>
                  <div class="project-heading">
                    <h3>{{ project.name || project.id }}</h3>
                    <span class="ai-mono-id">{{ project.id }}</span>
                  </div>
                  <span class="ai-pill" :class="typeTone(project.type)">{{ typeLabel(project.type) }}</span>
                </div>

                <p class="project-desc">{{ project.description || '暂无描述；可通过项目入口回传输出、报告和待办。' }}</p>

                <div class="fact-grid">
                  <div>
                    <span>部门</span>
                    <strong>{{ project.department || '全公司' }}</strong>
                  </div>
                  <div>
                    <span>可见性</span>
                    <strong>{{ visibilityLabel(project.visibility) }}</strong>
                  </div>
                  <div>
                    <span>运行</span>
                    <strong>{{ project.runtime?.containerized ? '容器' : '无容器' }}</strong>
                  </div>
                </div>

                <div class="run-strip" :class="runTone(project.latest_run?.status)">
                  <div>
                    <span>AI 状态</span>
                    <strong>{{ runStatusLabel(project.latest_run?.status) }}</strong>
                  </div>
                  <span class="ai-mono-id">{{ project.latest_run?.id || 'no-run' }}</span>
                </div>

                <div class="integration-strip">
                  <span
                    v-for="chip in integrationChips(project)"
                    :key="chip.label"
                    class="integration-chip"
                    :class="chip.tone"
                    :title="chip.hint"
                  >
                    <SfShellIcon :name="chip.icon" />
                    {{ chip.label }}
                  </span>
                </div>

                <div v-if="githubSource(project)" class="source-strip">
                  <SfShellIcon name="link" />
                  <span>{{ githubSource(project) }}</span>
                </div>

                <footer class="project-footer">
                  <span class="time mono">{{ timeLabel(project.updated_at) }}</span>
                  <div class="project-actions">
                    <button type="button" class="ai-btn sm" @click.stop="shareProject(project)">
                      分享
                    </button>
                    <button type="button" class="ai-btn sm" @click.stop="viewProjectTrace(project)">
                      详情
                    </button>
                    <button type="button" class="ai-btn sm primary" @click.stop="openProject(project)">
                      打开使用
                      <SfShellIcon name="arrowr" />
                    </button>
                  </div>
                </footer>
                <div class="click-hint">
                  <SfShellIcon name="play" />
                  点击卡片直接进入项目运行页
                </div>
              </article>
            </div>
            <div v-else class="project-empty ai-card">
              <SfShellIcon name="folder" />
              <strong>当前范围内暂无项目</strong>
              <span>请通过 Codex/sf 产物包自动注册；包内 manifest 决定项目 ID、部门、入口和能力。</span>
              <button type="button" class="ai-btn primary" @click="openUploadDialog">上传项目包</button>
            </div>
          </a-spin>
          <section v-if="showPagination" class="project-pagination ai-card">
            <button type="button" class="ai-btn" :disabled="page <= 1 || loading" @click="loadProjects(page - 1)">
              <SfShellIcon name="arrowl" />
              上一页
            </button>
            <span class="ai-mono-id">PAGE {{ page }} · {{ pageSize }} / PAGE · TOTAL {{ total }}</span>
            <button type="button" class="ai-btn" :disabled="!hasMore || loading" @click="loadProjects(page + 1)">
              下一页
              <SfShellIcon name="arrowr" />
            </button>
          </section>
        </template>
      </section>
    </main>

    <a-modal v-model:visible="uploadVisible" title="自动接入平台项目包" :footer="false" width="680px" modal-class="project-modal">
      <a-form layout="vertical" class="project-form">
        <a-form-item label="静态包（tar / tar.gz / zip）">
          <input class="file-input" type="file" accept=".zip,.tar,.tgz,.tar.gz,application/zip,application/gzip" @change="onPackageFileChange" />
          <p class="form-help">
            包内必须包含 `projectforge.yaml/json` 或 `manifest.yaml/json`。平台会自动注册项目、生成版本、接管运行和能力调用。
          </p>
        </a-form-item>
        <div class="modal-actions">
          <button type="button" class="ai-btn" @click="uploadVisible = false">取消</button>
          <button type="button" class="ai-btn primary" :disabled="uploading" @click="uploadProjectPackage">
            <SfShellIcon v-if="!uploading" name="upload" />
            {{ uploading ? '上传中…' : '上传并打开' }}
          </button>
        </div>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { useRoute, useRouter } from 'vue-router'
import type { RouteLocationRaw } from 'vue-router'
import { projectApi, type ProjectListResponse, type ProjectRow, type ProjectRuntimeStatus } from '@/api'
import { useUserStore } from '@/stores/user'
import { projectOpenPath, projectRunPath, projectSharePathFor, projectTracePathFor } from './routes'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

type SideOption = {
  value: string
  label: string
  icon: string
}

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const loading = ref(false)
const uploading = ref(false)
const uploadVisible = ref(false)
const keyword = ref('')
const projectView = ref(initialProjectView())
const scope = ref(initialScope())
const runtimeFilter = ref('all')
const departmentFilter = ref(initialDepartment())
const projects = ref<ProjectRow[]>([])
const stats = ref<Record<string, unknown>>({})
const runtimePolicy = ref<Record<string, unknown>>({})
const runtimeStatus = ref<ProjectRuntimeStatus>({})
const page = ref(1)
const pageSize = ref(60)
const total = ref(0)
const hasMore = ref(false)
let searchTimer: ReturnType<typeof setTimeout> | null = null
let initialActivationSkipped = false

const scopeOptions: SideOption[] = [
  { value: 'all', label: '全部项目', icon: 'grid' },
  { value: 'department', label: '本部门', icon: 'dept' },
  { value: 'mine', label: '我创建的', icon: 'user' },
  { value: 'company', label: '全公司', icon: 'users' },
  { value: 'playbook', label: 'Playbook', icon: 'book' },
]

const typeOptions: SideOption[] = [
  { value: 'external_web', label: '外部网页', icon: 'link' },
  { value: 'web_static', label: '静态网页', icon: 'doc' },
  { value: 'dashboard', label: '仪表盘', icon: 'trend' },
  { value: 'internal_tool', label: '内部工具', icon: 'cube' },
]

const runtimeOptions: SideOption[] = [
  { value: 'all', label: '全部状态', icon: 'list' },
  { value: 'running', label: '使用中', icon: 'play' },
  { value: 'stale', label: '已失活', icon: 'warn' },
  { value: 'waiting_ai', label: '等待 AI', icon: 'clock' },
  { value: 'ai_completed', label: 'AI 已完成', icon: 'check' },
  { value: 'failed', label: '失败', icon: 'warn' },
]

const uploadFile = ref<File | null>(null)

const isProjectAdmin = computed(() => Boolean(userStore.isAdmin))
const isMaterialDepartment = computed(() => {
  const departmentId = String(userStore.userInfo?.department_id || '')
  return departmentId === '931765248' || userStore.department === '示例品牌内容电商运营部'
})
const canViewProjectOverview = computed(() => isProjectAdmin.value || isMaterialDepartment.value)
const overviewActive = computed(() => canViewProjectOverview.value && projectView.value === 'overview')
const materialProject = computed(() => projects.value.find((item) => item.id === 'samplebrand-material-workbench') || null)
const byType = computed(() => numberRecord(stats.value.by_type))
const byDepartment = computed(() => numberRecord(stats.value.by_department))
const capabilityGateway = computed(() => String(runtimePolicy.value.capability_gateway || 'Project Gateway'))
const capacity = computed(() => objectRecord(runtimeStatus.value.capacity))
const runtimeRuns = computed(() => objectRecord(runtimeStatus.value.runs))
const runtimeAi = computed(() => objectRecord(runtimeStatus.value.ai))
const gateway = computed(() => objectRecord(runtimeStatus.value.gateway))

const metrics = computed(() => [
  { label: '项目总数', value: statNumber('total'), hint: '含 Playbook 兼容项目', icon: 'folder' },
  { label: '运行中', value: statNumber('running'), hint: '浏览器 / 网关运行态', icon: 'play' },
  { label: '已失活', value: statNumber('stale'), hint: '心跳超时但历史保留', icon: 'warn' },
  { label: '等待 AI', value: statNumber('waiting_ai'), hint: '待平台 AI 分析', icon: 'spark' },
  { label: '报告 / 待办', value: `${statNumber('report_count')} / ${statNumber('todo_count')}`, hint: '进入收件与执行闭环', icon: 'inbox' },
])

const capacityMetrics = computed(() => {
  const targetRuns = numberValue(capacity.value.target_concurrent_runs, 100)
  const activeRuns = numberValue(capacity.value.active_runs, statNumber('running'))
  const targetProjects = numberValue(capacity.value.target_visible_projects, 1000)
  const visibleProjects = numberValue(capacity.value.visible_projects, statNumber('total'))
  return [
    { label: '并发运行', value: `${activeRuns}/${targetRuns}`, hint: `剩余槽位 ${numberValue(capacity.value.available_run_slots, Math.max(targetRuns - activeRuns, 0))}`, icon: 'play' },
    { label: '项目容量', value: `${visibleProjects}/${targetProjects}`, hint: `${percentLabel(capacity.value.project_utilization)} 企业目录占用`, icon: 'folder' },
    { label: '等待 AI', value: numberValue(runtimeAi.value.waiting, numberValue(runtimeRuns.value.waiting_ai, 0)), hint: '输出已进入平台 AI 队列', icon: 'spark' },
    { label: 'Gateway', value: gatewayStatusLabel(gateway.value.status), hint: String(gateway.value.mode || 'browser_static_iframe'), icon: 'shield' },
  ]
})

const capacityStateLabel = computed(() => {
  const state = String(capacity.value.concurrency_state || gateway.value.status || 'ok')
  const map: Record<string, string> = {
    ok: '容量正常',
    busy: '运行繁忙',
    saturated: '容量打满',
    degraded: '能力异常',
  }
  return map[state] || state
})

const capacityTone = computed(() => {
  const state = String(capacity.value.concurrency_state || gateway.value.status || 'ok')
  if (state === 'saturated' || state === 'degraded') return 'bad'
  if (state === 'busy') return 'warn'
  return 'ok'
})

const currentScopeLabel = computed(() => {
  const option = [...scopeOptions, ...typeOptions].find((item) => item.value === scope.value)
  return option?.label || '全部项目'
})

const departmentOptions = computed(() => {
  const selected = departmentFilter.value
  const rows = Object.entries(byDepartment.value)
    .filter(([name]) => name && name !== '未分配')
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], 'zh-Hans-CN'))
    .slice(0, 10)
    .map(([name, count]) => ({ name, count }))
  if (selected && !rows.some((item) => item.name === selected)) rows.unshift({ name: selected, count: 0 })
  return rows
})
const currentDepartmentLabel = computed(() => departmentFilter.value ? `部门：${departmentFilter.value}` : '全部部门')
const currentRuntimeLabel = computed(() => runtimeOptions.find((item) => item.value === runtimeFilter.value)?.label || '全部状态')
const filteredProjects = computed(() => projects.value)
const showPagination = computed(() => total.value > pageSize.value)

function initialScope(): string {
  const value = route.query.type || route.query.scope
  return typeof value === 'string' && value.trim() ? value : 'all'
}

function initialProjectView(): string {
  const initialMaterialDepartment = String(userStore.userInfo?.department_id || '') === '931765248'
    || userStore.department === '示例品牌内容电商运营部'
  if (!(Boolean(userStore.isAdmin) || initialMaterialDepartment)) return 'list'
  if (route.query.view === 'overview') return 'overview'
  if (route.query.view === 'list') return 'list'
  return hasExplicitListQuery() ? 'list' : 'overview'
}

function initialDepartment(): string {
  const value = route.query.department
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

function hasExplicitListQuery(): boolean {
  return Boolean(route.query.scope || route.query.type || route.query.department || route.query.search)
}

function routeProjectView(): string {
  if (!canViewProjectOverview.value) return 'list'
  if (route.query.view === 'overview') return 'overview'
  if (route.query.view === 'list') return 'list'
  return hasExplicitListQuery() ? 'list' : 'overview'
}

function numberRecord(value: unknown): Record<string, number> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([key, item]) => [key, typeof item === 'number' ? item : Number(item || 0)]),
  )
}

function objectRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function numberValue(value: unknown, fallback = 0): number {
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function percentLabel(value: unknown): string {
  const numeric = numberValue(value, 0)
  if (!numeric) return '0%'
  return `${Math.round(numeric * 100)}%`
}

function gatewayStatusLabel(value: unknown): string {
  const map: Record<string, string> = {
    ok: '正常',
    busy: '繁忙',
    saturated: '打满',
    degraded: '异常',
  }
  return map[String(value || 'ok')] || String(value || '正常')
}

function statNumber(key: string): number {
  const value = stats.value?.[key]
  return typeof value === 'number' ? value : Number(value || 0)
}

function scopeCount(value: string): number | null {
  if (value === 'all') return statNumber('total')
  if (value === 'playbook') return typeCount('playbook')
  return null
}

function typeCount(type: string): number {
  return byType.value[type] || 0
}

function runtimeCount(value: string): number {
  if (value === 'all') return statNumber('total') || projects.value.length
  const stat = statNumber(value)
  if (stat) return stat
  return projects.value.filter((item) => runtimeBucket(item.latest_run?.status) === value).length
}

function runtimeBucket(status?: string): string {
  const raw = String(status || 'none')
  if (['opening', 'running'].includes(raw)) return 'running'
  if (raw === 'stale') return 'stale'
  if (raw === 'waiting_ai') return 'waiting_ai'
  if (['ai_completed', 'completed'].includes(raw)) return 'ai_completed'
  if (['ai_failed', 'failed'].includes(raw)) return 'failed'
  return 'none'
}

function typeLabel(type?: string): string {
  const map: Record<string, string> = {
    playbook: 'Playbook',
    external_web: '外部网页',
    web_static: '静态网页',
    dashboard: '仪表盘',
    internal_tool: '内部工具',
    playbook_ref: 'Playbook 引用',
  }
  return map[String(type || '')] || String(type || '项目')
}

function typeIcon(type?: string): string {
  const map: Record<string, string> = {
    playbook: 'book',
    external_web: 'link',
    web_static: 'doc',
    dashboard: 'trend',
    internal_tool: 'cube',
    playbook_ref: 'book',
  }
  return map[String(type || '')] || 'folder'
}

function typeTone(type?: string): string {
  const map: Record<string, string> = {
    playbook: 'accent',
    external_web: 'info',
    web_static: 'ok',
    dashboard: 'warn',
    internal_tool: '',
    playbook_ref: 'accent',
  }
  return map[String(type || '')] || ''
}

function visibilityLabel(value?: string): string {
  const map: Record<string, string> = { department: '部门', company: '全公司', private: '仅自己' }
  return map[String(value || '')] || String(value || '未知')
}

function runStatusLabel(status?: string): string {
  const map: Record<string, string> = {
    opening: '打开中',
    running: '使用中',
    stale: '已失活',
    waiting_ai: '等待 AI',
    ai_completed: 'AI 已完成',
    ai_failed: 'AI 失败',
    completed: '已完成',
    failed: '失败',
    none: '未运行',
  }
  return map[String(status || 'none')] || String(status || '未运行')
}

function runTone(status?: string): string {
  const bucket = runtimeBucket(status)
  if (bucket === 'running') return 'info'
  if (bucket === 'stale') return 'warn'
  if (bucket === 'waiting_ai') return 'warn'
  if (bucket === 'ai_completed') return 'ok'
  if (bucket === 'failed') return 'bad'
  return ''
}

function projectMetadata(project: ProjectRow): Record<string, unknown> {
  return objectRecord(project.metadata)
}

function manifestMetadata(project: ProjectRow): Record<string, unknown> {
  return objectRecord(projectMetadata(project).manifest_metadata)
}

function serviceConversion(project: ProjectRow): Record<string, unknown> {
  return objectRecord(projectMetadata(project).service_conversion)
}

function gatewayBootstrap(project: ProjectRow): Record<string, unknown> {
  return objectRecord(projectMetadata(project).gateway_bootstrap)
}

function assetRewrites(project: ProjectRow): Record<string, unknown> {
  return objectRecord(projectMetadata(project).asset_rewrites)
}

function runtimeEvaluation(project: ProjectRow): Record<string, unknown> {
  return objectRecord(projectMetadata(project).runtime_evaluation)
}

function capabilities(project: ProjectRow): string[] {
  const raw = projectMetadata(project).capabilities
  return Array.isArray(raw) ? raw.map((item) => String(item)) : []
}

function githubSource(project: ProjectRow): string {
  const metadata = projectMetadata(project)
  const manifestMeta = manifestMetadata(project)
  const github = objectRecord(metadata.github_corpus || manifestMeta.github_corpus || metadata.github || manifestMeta.github)
  const repo = String(github.repo || manifestMeta.github_repo || metadata.github_repo || '').trim()
  if (!repo) return ''
  const status = String(github.evaluation_status || manifestMeta.evaluation_status || metadata.evaluation_status || '').trim()
  return status ? `GitHub: ${repo} · ${status}` : `GitHub: ${repo}`
}

function integrationChips(project: ProjectRow): Array<{ label: string; icon: string; tone: string; hint: string }> {
  const metadata = projectMetadata(project)
  const service = serviceConversion(project)
  const bootstrap = gatewayBootstrap(project)
  const rewrites = assetRewrites(project)
  const runtimeEval = runtimeEvaluation(project)
  const caps = capabilities(project)
  const chips: Array<{ label: string; icon: string; tone: string; hint: string }> = []
  if (metadata.source === 'sf_project_submit') {
    chips.push({ label: 'sf 上传', icon: 'upload', tone: 'info', hint: '项目由 sf/Codex 包自动注册' })
  }
  if (Number(rewrites.replacement_count || 0) > 0) {
    chips.push({ label: '资源已重写', icon: 'link', tone: 'ok', hint: `根路径静态资源重写 ${Number(rewrites.replacement_count || 0)} 处` })
  }
  if (bootstrap.injected === true || bootstrap.enabled === true) {
    chips.push({ label: bootstrap.injected === true ? 'SDK 已注入' : 'SDK 已接入', icon: 'shield', tone: 'ok', hint: 'Project Gateway SDK / Autowire 已接入' })
  }
  if (service.status === 'converted') {
    chips.push({ label: '已转服务', icon: 'cube', tone: 'ok', hint: '上传后已生成平台 API 服务契约' })
  }
  if (runtimeEval.status) {
    const status = String(runtimeEval.status)
    chips.push({
      label: status === 'pass' ? '运行评估通过' : status === 'fail' ? '运行阻断' : '运行有提醒',
      icon: status === 'fail' ? 'warn' : 'check',
      tone: status === 'pass' ? 'ok' : status === 'fail' ? 'bad' : 'warn',
      hint: String(runtimeEval.summary || '上传后自动评估入口、资源、SDK 和转服务状态'),
    })
  }
  if (service.model_profile || caps.some((item) => item.includes('ai.'))) {
    chips.push({ label: String(service.model_profile || '').includes('cheap') ? '便宜模型' : 'AI 接管', icon: 'spark', tone: 'warn', hint: '项目 AI 调用通过平台后台配置，不暴露前端密钥' })
  }
  if (githubSource(project)) {
    chips.push({ label: 'GitHub 样本', icon: 'book', tone: 'accent', hint: githubSource(project) })
  }
  return chips.slice(0, 7)
}

function timeLabel(value?: string | null): string {
  if (!value) return '未记录更新时间'
  return value.replace('T', ' ').slice(0, 16)
}

function openUploadDialog() {
  uploadVisible.value = true
}

function onPackageFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  uploadFile.value = input.files?.[0] || null
}

async function setProjectView(value: 'overview' | 'list') {
  if (value === 'overview') {
    if (!canViewProjectOverview.value) return
    projectView.value = 'overview'
    scope.value = 'all'
    runtimeFilter.value = 'all'
    departmentFilter.value = ''
    await router.replace({
      query: {
        ...route.query,
        view: 'overview',
        scope: undefined,
        type: undefined,
        department: undefined,
      },
    })
    await loadProjects(1)
    return
  }
  projectView.value = 'list'
  await router.replace({ query: { ...route.query, view: 'list' } })
  await loadProjects(1)
}

async function setScope(value: string) {
  if (scope.value === value && !overviewActive.value) return
  projectView.value = 'list'
  scope.value = value
  await router.replace({ query: { ...route.query, view: 'list', scope: value === 'all' ? undefined : value, type: undefined } })
  await loadProjects(1)
}

async function setRuntimeFilter(value: string) {
  if (runtimeFilter.value === value && !overviewActive.value) return
  projectView.value = 'list'
  runtimeFilter.value = value
  if (route.query.view === 'overview') await router.replace({ query: { ...route.query, view: 'list' } })
  await loadProjects(1)
}

async function setDepartmentFilter(value: string) {
  if (departmentFilter.value === value && !overviewActive.value) return
  projectView.value = 'list'
  departmentFilter.value = value
  await router.replace({ query: { ...route.query, view: 'list', department: value || undefined } })
  await loadProjects(1)
}

async function loadProjects(targetPage = page.value) {
  loading.value = true
  try {
    const params: Record<string, unknown> = {
      page: targetPage,
      page_size: pageSize.value,
    }
    if (scope.value && scope.value !== 'all') params.scope = scope.value
    if (keyword.value.trim()) params.search = keyword.value.trim()
    if (runtimeFilter.value !== 'all') params.run_status = runtimeFilter.value
    if (departmentFilter.value) params.department = departmentFilter.value
    const res: ProjectListResponse = await projectApi.list(params)
    projects.value = res.items || []
    stats.value = res.stats || {}
    runtimePolicy.value = res.runtime_policy || {}
    page.value = Number(res.pagination?.page || targetPage)
    pageSize.value = Number(res.pagination?.page_size || pageSize.value)
    total.value = Number(res.pagination?.total || projects.value.length)
    hasMore.value = Boolean(res.pagination?.has_more)
    try {
      runtimeStatus.value = await projectApi.runtimeStatus()
    } catch {
      runtimeStatus.value = {}
    }
  } catch (error: any) {
    Message.error(error?._message || '加载项目失败')
  } finally {
    loading.value = false
  }
}

async function uploadProjectPackage() {
  if (!uploadFile.value) {
    Message.warning('请选择项目包')
    return
  }
  const projectWindow = prepareProjectPopup('项目上传中…')
  uploading.value = true
  try {
    const form = new FormData()
    form.append('package', uploadFile.value)
    const uploaded = await projectApi.uploadPackage(form)
    Message.success('项目包已自动注册')
    uploadVisible.value = false
    uploadFile.value = null
    await loadProjects(1)
    if (uploaded?.id) {
      navigateProjectPopup(projectWindow, projectRunPath(uploaded.id))
    } else {
      projectWindow?.close()
    }
  } catch (error: any) {
    projectWindow?.close()
    Message.error(error?._message || '上传项目包失败')
  } finally {
    uploading.value = false
  }
}

function openProject(project: ProjectRow) {
  if (project.type === 'playbook' && project.entry_url) {
    openProjectRunRoute(project.entry_url)
    return
  }
  openProjectRunRoute(projectOpenPath(project))
}

async function shareProject(project: ProjectRow) {
  const url = absoluteProjectUrl(projectSharePathFor(project))
  const copied = await copyToClipboard(url)
  if (copied) {
    Message.success('项目分享链接已复制；访问者需登录，未登录会引导扫码登录')
  } else {
    Message.info(`项目分享链接：${url}`)
  }
}

function openProjectRunRoute(target: RouteLocationRaw | string) {
  const projectWindow = prepareProjectPopup()
  navigateProjectPopup(projectWindow, target)
}

function prepareProjectPopup(title = '项目启动中…'): Window | null {
  const opened = window.open('about:blank', '_blank')
  if (!opened) {
    Message.warning('浏览器拦截了项目运行窗口，请允许弹窗后重试')
    return null
  }
  try {
    opened.opener = null
  } catch {
    // ignore cross-browser opener write failures
  }
  try {
    opened.document.write(`
      <!doctype html>
      <html lang="zh-CN">
        <head>
          <meta charset="utf-8" />
          <title>${title}</title>
          <style>
            body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #0f172a; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
            .box { text-align: center; line-height: 1.8; }
            .dot { width: 10px; height: 10px; border-radius: 999px; background: #38bdf8; display: inline-block; animation: pulse 1s infinite ease-in-out; }
            @keyframes pulse { 0%, 100% { opacity: .35; transform: scale(.8); } 50% { opacity: 1; transform: scale(1.2); } }
          </style>
        </head>
        <body><div class="box"><span class="dot"></span><div>${title}</div></div></body>
      </html>
    `)
    opened.document.close()
  } catch {
    // about:blank should be writable; if not, still navigate below.
  }
  return opened
}

function navigateProjectPopup(projectWindow: Window | null, target: RouteLocationRaw | string) {
  if (!projectWindow) return
  const href = resolveProjectTargetHref(target)
  projectWindow.location.href = href
  try {
    projectWindow.focus()
  } catch {
    // focus is best-effort only
  }
}

function resolveProjectTargetHref(target: RouteLocationRaw | string) {
  if (typeof target !== 'string') return router.resolve(target).href
  const trimmed = target.trim()
  if (/^https?:\/\//i.test(trimmed)) return trimmed
  return router.resolve(trimmed || '/projects').href
}

function absoluteProjectUrl(target: RouteLocationRaw | string) {
  const href = resolveProjectTargetHref(target)
  if (/^https?:\/\//i.test(href)) return href
  return new URL(href, window.location.origin).toString()
}

async function copyToClipboard(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.setAttribute('readonly', 'true')
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(textarea)
      return ok
    } catch {
      return false
    }
  }
}

function viewProjectTrace(project: ProjectRow) {
  if (project.type === 'playbook' && project.entry_url) {
    router.push(project.entry_url)
    return
  }
  router.push(projectTracePathFor(project))
}

watch(() => [route.query.view, route.query.scope, route.query.type, route.query.department, canViewProjectOverview.value] as const, ([, scopeParam, typeParam, departmentParam]) => {
  const nextView = routeProjectView()
  const next = nextView === 'overview' ? 'all' : typeof typeParam === 'string' ? typeParam : typeof scopeParam === 'string' ? scopeParam : 'all'
  const nextDepartment = nextView === 'overview' ? '' : typeof departmentParam === 'string' ? departmentParam.trim() : ''
  if (nextView !== projectView.value || next !== scope.value || nextDepartment !== departmentFilter.value) {
    projectView.value = nextView
    scope.value = next
    departmentFilter.value = nextDepartment
    loadProjects(1)
  }
})

watch(keyword, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => loadProjects(1), 320)
})

onMounted(async () => {
  if (canViewProjectOverview.value && routeProjectView() === 'overview' && route.query.view !== 'overview') {
    projectView.value = 'overview'
    await router.replace({ query: { ...route.query, view: 'overview' } })
  }
  await loadProjects()
})

onActivated(() => {
  if (!initialActivationSkipped) {
    initialActivationSkipped = true
    return
  }
  void loadProjects()
})

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
})
</script>

<style scoped>
.project-host-page {
  min-height: calc(100vh - 104px);
  display: flex;
  background: var(--ai-bg);
}

.project-sidebar {
  position: sticky;
  top: 0;
  height: calc(100vh - 104px);
  overflow: auto;
}

.sidebar-kicker {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 8px 16px;
  border-bottom: 1px solid var(--ai-border);
  margin-bottom: 16px;
}

.sidebar-kicker strong,
.sidebar-kicker small {
  display: block;
}

.sidebar-kicker strong {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 600;
}

.sidebar-kicker small {
  color: var(--ai-ink-4);
  font-size: 11.5px;
}

.sidebar-mark,
.project-mark,
.kpi-icon {
  display: grid;
  place-items: center;
  color: var(--ai-ink-1);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
}

.sidebar-mark {
  width: 30px;
  height: 30px;
  border-radius: 7px;
}

.side-button {
  width: 100%;
  border: 0;
  background: transparent;
  text-align: left;
}

.sidebar-note {
  display: flex;
  gap: 8px;
  margin: 22px 4px 0;
  padding: 10px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  color: var(--ai-ink-3);
  background: var(--ai-surface-2);
  font-size: 12px;
  line-height: 1.55;
}

.sidebar-note svg {
  margin-top: 2px;
  color: var(--ai-ink-4);
}

.project-pagehead {
  align-items: flex-start;
}

.page-actions,
.toolbar-pills,
.project-footer,
.card-top,
.policy-strip,
.policy-item,
.capacity-head,
.modal-actions,
.form-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.page-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
}

.page-actions .ai-btn {
  flex: 0 0 auto;
  white-space: nowrap;
}

.project-pagebody {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.policy-strip {
  min-height: 42px;
  padding: 0 12px;
  flex-wrap: wrap;
}

.policy-item {
  color: var(--ai-ink-2);
  font-size: 12.5px;
}

.policy-item svg {
  color: var(--ai-ink-4);
}

.policy-item.muted {
  margin-left: auto;
  color: var(--ai-ink-4);
  font-size: 11.5px;
}

.material-workbench-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 24px;
  padding: 22px;
  color: var(--ai-ink-1);
  border-color: var(--ai-border);
  background: var(--ai-surface);
}

.material-workbench-hero h2 {
  margin: 6px 0 7px;
  color: var(--ai-ink-1);
  font-size: 25px;
  letter-spacing: -0.035em;
}

.material-workbench-hero p {
  max-width: 820px;
  margin: 0;
  color: var(--ai-ink-3);
  font-size: 13px;
  line-height: 1.7;
}

.material-workbench-hero .overview-kicker {
  color: var(--ai-accent);
}

.material-workbench-tags,
.material-workbench-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.material-workbench-tags {
  margin-top: 14px;
}

.material-workbench-tags span {
  padding: 4px 8px;
  color: var(--ai-accent);
  border: 1px solid var(--ai-border);
  border-radius: 999px;
  background: var(--ai-accent-soft);
  font-size: 11px;
}

.material-workbench-actions {
  max-width: 210px;
  justify-content: flex-end;
}

.overview-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 18px;
  background: var(--ai-surface);
}

.overview-hero h2 {
  margin: 5px 0 6px;
  color: var(--ai-ink-1);
  font-size: 20px;
  font-weight: 650;
  letter-spacing: -0.03em;
}

.overview-hero p {
  max-width: 680px;
  margin: 0;
  color: var(--ai-ink-3);
  font-size: 13px;
  line-height: 1.65;
}

.overview-kicker {
  color: var(--ai-accent);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.kpi-card {
  position: relative;
  min-height: 118px;
  padding: 14px;
}

.kpi-icon {
  width: 28px;
  height: 28px;
  margin-bottom: 16px;
  border-radius: 7px;
  color: var(--ai-ink-3);
}

.kpi-card span,
.kpi-card small {
  display: block;
  color: var(--ai-ink-4);
}

.kpi-card span {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.kpi-card strong {
  display: block;
  margin: 4px 0 2px;
  color: var(--ai-ink-1);
  font-size: 24px;
  font-weight: 500;
  letter-spacing: -0.04em;
}

.kpi-card small {
  font-size: 12px;
}

.runtime-capacity {
  padding: 14px;
}

.capacity-head {
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--ai-border);
}

.capacity-head > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.capacity-head .t {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 600;
}

.capacity-head small {
  color: var(--ai-ink-4);
  font-size: 12px;
}

.capacity-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  padding-top: 12px;
}

.capacity-item {
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
}

.capacity-item span,
.capacity-item small {
  display: block;
  color: var(--ai-ink-4);
}

.capacity-item span {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.05em;
}

.capacity-item strong {
  display: block;
  margin: 4px 0 2px;
  color: var(--ai-ink-1);
  font-size: 20px;
  font-weight: 500;
  letter-spacing: -0.03em;
}

.capacity-item small {
  overflow: hidden;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.overview-guide {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  padding: 12px;
}

.guide-item {
  display: flex;
  min-width: 0;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: 10px;
  background: var(--ai-surface-2);
}

.guide-item svg {
  width: 18px;
  height: 18px;
  flex: 0 0 18px;
  color: var(--ai-ink-4);
}

.guide-item strong,
.guide-item span {
  display: block;
}

.guide-item strong {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 600;
}

.guide-item span {
  margin-top: 4px;
  color: var(--ai-ink-4);
  font-size: 12px;
  line-height: 1.55;
}

.project-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
}

.project-search {
  flex: 1;
  min-width: 240px;
}

.toolbar-pills {
  flex-wrap: wrap;
  justify-content: flex-end;
}

.project-spin {
  min-height: 320px;
}

.project-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 12px;
}

.project-card {
  display: flex;
  min-height: 280px;
  flex-direction: column;
  padding: 14px;
  cursor: pointer;
  transition: border-color 0.16s ease, background 0.16s ease;
}

.project-card:hover {
  border-color: var(--ai-border-2);
  background: #fffefa;
}

.project-mark {
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  border-radius: 7px;
  color: var(--ai-ink-3);
}

.project-heading {
  min-width: 0;
  flex: 1;
}

.project-heading h3 {
  margin: 0 0 3px;
  overflow: hidden;
  color: var(--ai-ink-1);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.015em;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-desc {
  min-height: 42px;
  margin: 14px 0;
  color: var(--ai-ink-3);
  font-size: 12.5px;
  line-height: 1.65;
}

.fact-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.fact-grid > div {
  min-width: 0;
  padding: 9px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-2);
}

.fact-grid span,
.fact-grid strong {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fact-grid span,
.run-strip span {
  color: var(--ai-ink-4);
  font-size: 11px;
}

.fact-grid strong,
.run-strip strong {
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
}

.run-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 12px 0 0;
  padding: 10px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface);
}

.run-strip.info {
  border-color: transparent;
  background: var(--ai-info-soft);
}

.run-strip.warn {
  border-color: transparent;
  background: var(--ai-warn-soft);
}

.run-strip.ok {
  border-color: transparent;
  background: var(--ai-ok-soft);
}

.run-strip.bad {
  border-color: transparent;
  background: var(--ai-bad-soft);
}

.integration-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.integration-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 148px;
  padding: 4px 7px;
  border: 1px solid var(--ai-border);
  border-radius: 999px;
  color: var(--ai-ink-3);
  background: var(--ai-surface-2);
  font-size: 11px;
  line-height: 1;
}

.integration-chip svg {
  width: 12px;
  height: 12px;
  flex: 0 0 12px;
}

.integration-chip.info {
  border-color: transparent;
  background: var(--ai-info-soft);
  color: var(--ai-info);
}

.integration-chip.ok {
  border-color: transparent;
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
}

.integration-chip.warn {
  border-color: transparent;
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
}

.integration-chip.accent {
  border-color: transparent;
  background: rgba(99, 102, 241, 0.1);
  color: #4f46e5;
}

.source-strip {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  margin-top: 8px;
  padding: 7px 8px;
  border: 1px dashed var(--ai-border);
  border-radius: 7px;
  color: var(--ai-ink-4);
  background: var(--ai-surface-2);
  font-size: 11px;
}

.source-strip span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-strip svg {
  width: 13px;
  height: 13px;
  flex: 0 0 13px;
}

.project-footer {
  align-items: flex-start;
  flex-wrap: wrap;
  justify-content: space-between;
  margin-top: auto;
  padding-top: 12px;
  border-top: 1px dashed var(--ai-border);
}

.project-footer .time {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-actions {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
  margin-left: auto;
}

.project-actions .ai-btn {
  flex: 0 0 auto;
  white-space: nowrap;
}

.click-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  color: var(--ai-ink-4);
  font-size: 11px;
}

.click-hint svg {
  width: 13px;
  height: 13px;
}

.project-pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
}

.time {
  color: var(--ai-ink-4);
  font-size: 11.5px;
}

.project-empty {
  display: grid;
  min-height: 300px;
  place-items: center;
  align-content: center;
  gap: 10px;
  color: var(--ai-ink-3);
  text-align: center;
}

.project-empty svg {
  width: 28px;
  height: 28px;
  color: var(--ai-ink-4);
}

.project-empty strong {
  color: var(--ai-ink-1);
  font-size: 14px;
}

.project-empty span {
  max-width: 360px;
  font-size: 12.5px;
}

.form-row > * {
  flex: 1;
}

.modal-actions {
  justify-content: flex-end;
  margin-top: 8px;
}

.file-input {
  width: 100%;
  padding: 9px 10px;
  border: 1px dashed var(--ai-border-2);
  border-radius: var(--ai-radius);
  color: var(--ai-ink-2);
  background: var(--ai-surface-2);
}

.form-help {
  margin: 8px 0 0;
  color: var(--ai-ink-4);
  font-size: 12px;
  line-height: 1.5;
}

.ai-btn:disabled,
.side-button:disabled {
  cursor: not-allowed;
  opacity: 0.56;
}

:deep(.project-form .arco-form-item-label) {
  color: var(--ai-ink-3);
  font-size: 12px;
  font-weight: 500;
}

@media (max-width: 1120px) {
  .kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .capacity-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .overview-guide {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 980px) {
  .project-pagehead {
    flex-direction: column;
    align-items: stretch;
  }

  .page-actions {
    justify-content: flex-start;
  }
}

@media (max-width: 820px) {
  .project-host-page {
    flex-direction: column;
  }

  .project-sidebar {
    position: static;
    width: 100%;
    height: auto;
    flex-basis: auto;
    border-right: 0;
    border-bottom: 1px solid var(--ai-border);
  }
}

@media (max-width: 720px) {
  .material-workbench-hero {
    grid-template-columns: 1fr;
  }

  .material-workbench-actions {
    max-width: none;
    justify-content: flex-start;
  }

  .project-pagehead,
  .project-toolbar,
  .project-pagination,
  .capacity-head,
  .overview-hero,
  .form-row {
    flex-direction: column;
    align-items: stretch;
  }

  .page-actions,
  .toolbar-pills {
    justify-content: flex-start;
  }

  .kpi-grid,
  .capacity-grid,
  .project-grid,
  .fact-grid {
    grid-template-columns: 1fr;
  }

  .policy-item.muted {
    margin-left: 0;
  }
}
</style>
