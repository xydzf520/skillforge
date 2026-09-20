<template>
  <!-- E4: 卡片根 role="button" + aria-label，让屏幕阅读器能说出整张卡的语义 -->
  <div
    class="skill-card-wrap"
    role="button"
    tabindex="0"
    :aria-label="cardAriaLabel"
    @contextmenu.prevent="onContextMenu"
    @keydown.enter="$emit('click', skill)"
  >
  <a-card
    class="skill-card"
    :class="[`variant-${variant}`]"
    hoverable
    @click="$emit('click', skill)"
  >
    <!-- ===== Hall variant ===== -->
    <template v-if="variant === 'hall'">
      <div class="card-top">
        <a-tag size="small" color="arcoblue">{{ skill.department || '-' }}</a-tag>
        <a-tooltip v-if="skill.risk_level" :content="riskTooltip(skill.risk_level)" position="top">
          <a-tag :color="riskColorValue(skill.risk_level)" size="small">{{ skill.risk_level }}</a-tag>
        </a-tooltip>
      </div>
      <div class="card-id">{{ skill.display_name || skill.name || skill.id }}</div>
      <div class="card-desc">{{ skill.description || '暂无描述' }}</div>
      <div class="card-meta">
        <span v-if="skill.owner_name || skill.owner" class="card-meta-item">
          <icon-user />{{ skill.owner_name || skill.owner }}
        </span>
        <span v-if="skill.category" class="card-meta-item">
          <icon-tag />{{ skill.category }}
        </span>
      </div>
      <!-- v2.7 大厅 v3：profile 画像行（仅在 API 返回含 profile 字段时展示） -->
      <div v-if="skill.profile" class="card-profile">
        <div v-if="typeof skill.profile.success_rate === 'number'" class="profile-row">
          <icon-experiment /> 成功率 <b>{{ Math.round(skill.profile.success_rate * 100) }}%</b>
        </div>
        <div v-if="skill.profile.last_run_at" class="profile-row profile-dim">
          <icon-clock-circle /> 最近执行 {{ relativeTime(skill.profile.last_run_at) }}
        </div>
        <div v-if="skill.profile.adoption_departments && skill.profile.adoption_departments.length" class="profile-row">
          <icon-user-group /> 采纳：
          <a-tag
            v-for="dept in skill.profile.adoption_departments.slice(0, 3)"
            :key="dept"
            size="small"
            color="arcoblue"
            class="profile-dept-tag"
          >{{ dept }}</a-tag>
          <span v-if="skill.profile.adoption_departments.length > 3" class="profile-more">
            +{{ skill.profile.adoption_departments.length - 3 }}
          </span>
        </div>
        <div v-if="skill.profile.data_sources && skill.profile.data_sources.length" class="profile-row profile-dim">
          <icon-storage /> 依赖 {{ skill.profile.data_sources.length }} 个数据源：
          <span class="profile-sources">
            {{ skill.profile.data_sources.slice(0, 2).map((d: any) => d.name).join(' / ') }}
            <span v-if="skill.profile.data_sources.length > 2"> 等</span>
          </span>
        </div>
      </div>
      <div class="card-footer">
        <!-- W1-A: 扩大 tooltip 触发区（去 mini + 增 padding） -->
        <a-tooltip content="历史运行次数"><span class="card-stat card-stat-clickable"><icon-fire />{{ skill.usage_count || 0 }}</span></a-tooltip>
        <a-tooltip content="被 Fork 次数"><span class="card-stat card-stat-clickable"><icon-branch />{{ skill.fork_count || 0 }}</span></a-tooltip>
        <span class="card-spacer" />
        <a-tag v-if="skill.is_member" size="small" color="green">已加入</a-tag>
        <a-button
          v-if="skill.can_fork"
          size="mini"
          type="text"
          aria-label="Fork 到我的部门"
          @click.stop="$emit('fork', skill)"
        >
          <template #icon><icon-branch /></template>Fork
        </a-button>
      </div>
    </template>

    <!-- ===== List variant ===== -->
    <template v-else-if="variant === 'list'">
      <div class="card-top">
        <a-tag :color="statusColor" size="small">{{ statusText }}</a-tag>
        <span class="card-version">{{ skill.current_version || 'v0.0' }}</span>
      </div>
      <div class="card-id">{{ skill.name || skill.id }}</div>
      <div v-if="skill.name && skill.name !== skill.id" class="card-name">{{ skill.id }}</div>
      <div class="card-footer">
        <a-tooltip :content="riskTooltip(skill.risk_level)">
          <a-tag size="small" :color="riskColorValue(skill.risk_level)">{{ skill.risk_level || '-' }}</a-tag>
        </a-tooltip>
        <span class="card-dept">{{ skill.department || '-' }}</span>
      </div>
      <div class="card-time">
        <a-tooltip :content="formatTime(skill.updated_at)" mini>
          <span>{{ relativeTime(skill.updated_at) }}</span>
        </a-tooltip>
        <span class="card-actions">
          <a-button
            type="text"
            size="mini"
            class="card-export-btn"
            title="导出 Skill 包"
            aria-label="导出 Skill 包"
            @click.stop="exportSkillFromMenu"
          >
            <icon-download />
          </a-button>
          <a-button
            v-if="showDelete"
            type="text"
            size="mini"
            status="danger"
            class="card-delete-btn"
            title="删除"
            aria-label="删除 Skill"
            @click.stop="$emit('delete', skill)"
          >
            <icon-delete />
          </a-button>
        </span>
      </div>
    </template>

    <!-- ===== Recent variant（SkillsHome "最近访问"紧凑卡）===== -->
    <template v-else>
      <div class="card-id">{{ skill.name || skill.id }}</div>
      <div class="card-meta">
        <a-tag :color="statusColor" size="small">{{ statusText }}</a-tag>
        <span v-if="skill.department" class="card-dept">{{ skill.department }}</span>
      </div>
    </template>
  </a-card>

  <!-- N1: 右键菜单。内嵌在卡片 wrap 里，位置根据 MouseEvent.clientX/Y 相对卡片 -->
  <div
    v-if="menuOpen"
    class="skill-card-menu"
    :style="{ left: menuX + 'px', top: menuY + 'px' }"
    @click.stop
    role="menu"
  >
    <button class="menu-item" @click="copyId">
      <icon-copy /><span>复制 ID</span>
    </button>
    <button class="menu-item" @click="openNewTab">
      <icon-launch /><span>在新标签打开</span>
    </button>
    <!-- v2.3.3: 导出 Skill 包 -->
    <button class="menu-item" @click="exportSkillFromMenu">
      <icon-download /><span>导出 Skill 包</span>
    </button>
    <button v-if="variant === 'hall' && skill.can_fork" class="menu-item" @click="forkFromMenu">
      <icon-branch /><span>Fork 到我的部门</span>
    </button>
    <button v-if="showDelete" class="menu-item menu-item-danger" @click="deleteFromMenu">
      <icon-delete /><span>删除</span>
    </button>
  </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 共享 SkillCard 组件
 *
 * 替代原本 SkillHall.vue / SkillList.vue / SkillsHome.vue "最近访问" 三处分裂的卡片样式，
 * 通过 variant='hall'|'list'|'recent' 切换字段布局：
 *   - hall   : 供大厅发现用，展示 department / risk / 名称 / 描述 / usage/fork 数 / 已加入 tag / Fork 按钮
 *   - list   : 供本部门列表用，展示 status / 版本 / 名称 / risk / department / 更新时间 / 删除按钮
 *   - recent : 供工作中心 "最近访问" 用，紧凑 3 行
 *
 * 根 DOM 保持 class="skill-card" 兼容现有 e2e 选择器。
 * 所有 variant 下 min-height: 172px 且 flex 列向拉伸，解决卡片高度不齐（O3）。
 */
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  IconUser, IconTag, IconFire, IconBranch, IconDelete, IconCopy, IconLaunch, IconDownload,
  IconExperiment, IconClockCircle, IconUserGroup, IconStorage,
} from '@arco-design/web-vue/es/icon'
import { skillBatchApi } from '@/api'
import { skillStatusLabel, skillStatusColor, riskColor } from '@/utils/constants'
import { formatTime, relativeTime } from '@/utils/format'

interface SkillLike {
  id: string
  name?: string
  display_name?: string
  description?: string
  department?: string
  owner?: string
  owner_name?: string
  category?: string
  status?: string
  risk_level?: string
  current_version?: string
  usage_count?: number
  fork_count?: number
  is_member?: boolean
  can_fork?: boolean
  updated_at?: string
  route_path?: string
  // v2.7 大厅 v3：?include=profile 附加字段
  profile?: {
    usage_count?: number
    success_rate?: number | null
    last_run_at?: string | null
    adoption_departments?: string[]
    data_sources?: Array<{ id: string; name?: string }>
  }
}

const props = withDefaults(
  defineProps<{
    skill: SkillLike
    variant?: 'list' | 'hall' | 'recent'
    /** 是否显示右下角删除按钮（list variant，通常绑定 admin 权限） */
    showDelete?: boolean
  }>(),
  { variant: 'list', showDelete: false },
)

const emit = defineEmits<{
  (e: 'click', skill: SkillLike): void
  (e: 'fork', skill: SkillLike): void
  (e: 'delete', skill: SkillLike): void
  (e: 'context-menu', skill: SkillLike, ev: MouseEvent): void
}>()

const statusKey = computed(() => (props.skill.status || 'draft') as keyof typeof skillStatusLabel)
const statusColor = computed(() => skillStatusColor[statusKey.value] || 'gray')
const statusText = computed(() => skillStatusLabel[statusKey.value] || props.skill.status || '未知')

// E4: 给屏幕阅读器一句完整描述
const cardAriaLabel = computed(() => {
  const name = props.skill.display_name || props.skill.name || props.skill.id
  const dept = props.skill.department || '未指定部门'
  const status = statusText.value
  return `Skill ${name}，状态 ${status}，部门 ${dept}`
})

function riskColorValue(level?: string): string {
  if (!level) return 'gray'
  return (riskColor as Record<string, string>)[level] || 'gray'
}
function riskTooltip(level?: string): string {
  if (!level) return '风险等级未指定'
  const desc: Record<string, string> = {
    R1: 'R1（低）：自动通过，无需人工复核',
    R2: 'R2（中）：可编辑，发布需部门管理员审核',
    R3: 'R3（高）：涉及对外信息/金额，需系统管理员审批',
    R4: 'R4（极高）：涉及决策/结算，需合规 + 系统双审批',
  }
  return desc[level] || `风险等级 ${level}`
}

// N1: 内嵌右键菜单
const menuOpen = ref(false)
const menuX = ref(0)
const menuY = ref(0)

function onContextMenu(ev: MouseEvent) {
  emit('context-menu', props.skill, ev)
  const wrap = (ev.currentTarget as HTMLElement).getBoundingClientRect()
  menuX.value = ev.clientX - wrap.left
  menuY.value = ev.clientY - wrap.top
  menuOpen.value = true
}

function closeMenu() {
  menuOpen.value = false
}

async function copyId() {
  try {
    await navigator.clipboard.writeText(props.skill.id)
    Message.success(`已复制 Skill ID: ${props.skill.id}`)
  } catch {
    Message.error('复制失败，请手动复制')
  }
  closeMenu()
}

function openNewTab() {
  window.open(props.skill.route_path || `/skills/${props.skill.id}`, '_blank')
  closeMenu()
}

function forkFromMenu() {
  emit('fork', props.skill)
  closeMenu()
}

function deleteFromMenu() {
  emit('delete', props.skill)
  closeMenu()
}

function normalizeExportBlob(payload: unknown): Blob {
  const candidate = (payload as { data?: unknown } | null)?.data ?? payload
  if (candidate instanceof Blob) return candidate
  if (candidate instanceof ArrayBuffer) return new Blob([candidate], { type: 'application/zip' })
  throw new TypeError('导出响应不是有效的文件数据')
}

// v2.3.3: 导出 Skill 包（走 skillBatchApi.exportSkill，触发浏览器下载）
async function exportSkillFromMenu() {
  closeMenu()
  try {
    const blob = normalizeExportBlob(await skillBatchApi.exportSkill(props.skill.id))
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = archiveFileName()
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    Message.success(`已下载：${archiveFileName()}`)
  } catch (e: any) {
    Message.error(e?._message || e?.message || '导出失败')
  }
}

function archiveFileName(): string {
  const version = String(props.skill.current_version || '').trim()
  return version ? `${props.skill.id}-${version}.zip` : `${props.skill.id}.zip`
}

function onDocClick(ev: MouseEvent) {
  if (!menuOpen.value) return
  const t = ev.target as HTMLElement
  if (!t.closest('.skill-card-menu')) closeMenu()
}

onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('contextmenu', onDocClick, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('contextmenu', onDocClick, true)
})
</script>

<style scoped>
.skill-card-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
}
.skill-card {
  min-height: 172px;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  cursor: pointer;
  transition: box-shadow var(--sf-transition-fast), transform var(--sf-transition-fast);
}
.skill-card.variant-hall {
  min-height: 272px;
}
.skill-card.variant-list {
  height: 228px;
  min-height: 228px;
}
.skill-card.variant-list .card-id {
  min-height: 40px;
  line-height: 20px;
}
.skill-card.variant-list .card-name {
  min-height: 16px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.skill-card.variant-list .card-footer {
  min-height: 24px;
}
.skill-card.variant-list .card-time {
  min-height: 24px;
}
/* N1: 右键菜单浮窗 */
.skill-card-menu {
  position: absolute;
  min-width: 180px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.12);
  padding: var(--sf-spacing-xs);
  z-index: 1100;
}
.menu-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: var(--sf-spacing-sm) var(--sf-spacing-md);
  font-size: var(--sf-font-sm);
  color: var(--ai-ink-1);
  background: transparent;
  border: none;
  cursor: pointer;
  border-radius: 4px;
  text-align: left;
}
.menu-item:hover {
  background: var(--ai-surface-2);
}
.menu-item-danger {
  color: var(--ai-bad);
}
.menu-item-danger:hover {
  background: var(--ai-bad-soft);
}
.skill-card :deep(.arco-card-body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
}
.skill-card.variant-recent {
  min-height: 72px;
  padding: 4px 0;
}

.card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 24px;
}
.card-id {
  font-size: var(--sf-font-md);
  font-weight: 600;
  color: var(--ai-ink-1);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.variant-hall .card-id {
  min-height: 38px;
}
.card-name {
  font-size: var(--sf-text-tiny);
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono, monospace);
}
.card-desc {
  font-size: var(--sf-text-caption);
  color: var(--ai-ink-3);
  line-height: 1.5;
  min-height: 36px;
  max-height: 36px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.card-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: var(--sf-text-caption);
  color: var(--ai-ink-3);
  flex-wrap: wrap;
  min-height: 22px;
  max-height: 44px;
  overflow: hidden;
}
.card-meta-item {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  min-width: 0;
  max-width: 100%;
}
.card-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: auto;
  min-height: 28px;
  padding-top: 4px;
}
.card-stat {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: var(--sf-text-caption);
  color: var(--ai-ink-3);
}
/* W1-A: 扩大触发区让 tooltip 更易触发 */
.card-stat-clickable {
  padding: 2px 6px;
  border-radius: 4px;
  cursor: help;
  transition: background var(--sf-transition-fast);
}
.card-stat-clickable:hover {
  background: var(--ai-surface-2);
}
.card-spacer { flex: 1; }
.card-version {
  font-size: var(--sf-text-tiny);
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono, monospace);
}
.card-dept {
  font-size: var(--sf-text-caption);
  color: var(--ai-ink-3);
}
.card-time {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--sf-text-tiny);
  color: var(--ai-ink-4);
  margin-top: 4px;
}
.card-actions {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
.card-export-btn,
.card-delete-btn {
  opacity: 0;
  transition: opacity var(--sf-transition-fast);
}
.skill-card:hover .card-export-btn,
.skill-card:hover .card-delete-btn {
  opacity: 1;
}

/* v2.7 大厅 v3：Skill 画像行 */
.card-profile {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 8px 0;
  padding: var(--sf-spacing-sm) var(--sf-spacing-md);
  background: var(--ai-surface-2);
  border-radius: 8px;
  border-left: 2px solid var(--ai-accent);
  min-height: 78px;
  max-height: 78px;
  overflow: hidden;
}
.profile-dept-tag {
  margin-right: var(--sf-spacing-xs);
}
.profile-row {
  font-size: var(--sf-text-caption);
  color: var(--ai-ink-2);
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: nowrap;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.profile-row b {
  color: var(--ai-accent-ink);
  font-weight: 600;
}
.profile-dim {
  color: var(--ai-ink-3);
  opacity: 0.85;
}
.profile-more {
  font-size: var(--sf-text-tiny);
  color: var(--ai-ink-3);
}
.profile-sources {
  color: var(--ai-ink-3);
  font-size: var(--sf-text-tiny);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
