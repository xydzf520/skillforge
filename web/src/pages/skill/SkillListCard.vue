<template>
  <!--
    SkillListCard — Skills 列表视图专用卡片（V5 / P1-3）

    目的：替代原 SkillCard variant="list" 的极简两字段布局，
    在每张卡片补齐"负责人 / 部门 / 使用次数 / 最后运行"等元字段，
    与 PortalHome 的 portal-skill-card 同一视觉体系保持一致。

    约束：
    - 仅在 SkillList.vue 里使用；不复用到 SkillHall / SkillsHome / Portal
    - props / emits 不改已有 SkillCard 的形状，调用方几乎无迁移成本
    - 缺字段一律用 "—" 兜底，避免空白 / undefined 直出
  -->
  <div
    class="skill-list-card-wrap"
    role="button"
    tabindex="0"
    :aria-label="cardAriaLabel"
    @keydown.enter="$emit('click', skill)"
  >
    <a-card
      class="skill-list-card"
      hoverable
      @click="$emit('click', skill)"
    >
      <!-- 头部：状态 tag + 风险 chip + 版本 -->
      <div class="card-header">
        <a-tag :color="statusColor" size="small" class="status-tag">{{ statusText }}</a-tag>
        <span class="card-head-spacer" />
        <a-tooltip v-if="skill.risk_level" :content="riskTooltipText">
          <a-tag size="small" :color="riskColorValue">{{ skill.risk_level }}</a-tag>
        </a-tooltip>
        <span class="card-version">{{ skill.current_version || 'v0.0' }}</span>
      </div>

      <!-- 标题 + ID 副标 -->
      <div class="card-title">{{ skill.name || skill.id }}</div>
      <div v-if="skill.name && skill.name !== skill.id" class="card-id">{{ skill.id }}</div>
      <div v-else class="card-id card-id-placeholder">&nbsp;</div>

      <!-- 元字段 2x2 网格（负责人 / 部门 / 使用次数 / 最后运行） -->
      <div class="card-meta-grid">
        <div class="meta-item">
          <icon-user class="meta-icon" />
          <span class="meta-label">负责人</span>
          <span class="meta-value" :title="ownerText">{{ ownerText }}</span>
        </div>
        <div class="meta-item">
          <icon-apps class="meta-icon" />
          <span class="meta-label">部门</span>
          <span class="meta-value" :title="skill.department || '—'">{{ skill.department || '—' }}</span>
        </div>
        <div class="meta-item">
          <icon-fire class="meta-icon" />
          <span class="meta-label">使用</span>
          <span class="meta-value">{{ usageText }}</span>
        </div>
        <div class="meta-item">
          <icon-clock-circle class="meta-icon" />
          <span class="meta-label">最近</span>
          <a-tooltip :content="lastRunTooltip" mini>
            <span class="meta-value">{{ lastRunText }}</span>
          </a-tooltip>
        </div>
      </div>

      <!-- 底部：触发类型（或审批级别）+ 操作按钮 -->
      <div class="card-footer">
        <span v-if="skill.trigger_type" class="trigger-chip">
          {{ triggerLabel }}
        </span>
        <span v-else class="trigger-chip trigger-chip-muted">手动</span>
        <span class="card-spacer" />
        <span class="card-actions" @click.stop>
          <a-button
            type="text"
            size="mini"
            class="card-icon-btn"
            title="导出 Skill 包"
            aria-label="导出 Skill 包"
            @click.stop="onExport"
          >
            <icon-download />
          </a-button>
          <a-button
            v-if="showDelete"
            type="text"
            size="mini"
            status="danger"
            class="card-icon-btn"
            title="删除 Skill"
            aria-label="删除 Skill"
            @click.stop="$emit('delete', skill)"
          >
            <icon-delete />
          </a-button>
        </span>
      </div>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  IconUser, IconApps, IconFire, IconClockCircle, IconDownload, IconDelete,
} from '@arco-design/web-vue/es/icon'
import { skillBatchApi } from '@/api'
import { skillStatusLabel, skillStatusColor, riskColor } from '@/utils/constants'
import { formatTime, relativeTime } from '@/utils/format'

// 与 SkillCard.vue 的 SkillLike 保持字段超集，允许未来后端新加字段直接读取
interface SkillListItem {
  id: string
  name?: string
  description?: string
  department?: string
  owner?: string
  owner_name?: string
  status?: string
  risk_level?: string
  trigger_type?: string
  current_version?: string
  updated_at?: string
  // 下列字段后端 /api/skills/ 目前不返回，兜底为 "—"
  usage_count?: number
  last_run_at?: string | null
  approval_level?: number
}

const props = withDefaults(
  defineProps<{
    skill: SkillListItem
    showDelete?: boolean
  }>(),
  { showDelete: false },
)

defineEmits<{
  (e: 'click', skill: SkillListItem): void
  (e: 'delete', skill: SkillListItem): void
}>()

// ── 状态 ──
const statusKey = computed(() => (props.skill.status || 'draft') as keyof typeof skillStatusLabel)
const statusColor = computed(() => skillStatusColor[statusKey.value] || 'gray')
const statusText = computed(() => skillStatusLabel[statusKey.value] || props.skill.status || '未知')

// ── 风险 ──
const riskColorValue = computed(() => {
  if (!props.skill.risk_level) return 'gray'
  return (riskColor as Record<string, string>)[props.skill.risk_level] || 'gray'
})

const RISK_TOOLTIPS: Record<string, string> = {
  R1: 'R1（低）：自动通过，无需人工复核',
  R2: 'R2（中）：可编辑，发布需部门管理员审核',
  R3: 'R3（高）：涉及对外信息/金额，需系统管理员审批',
  R4: 'R4（极高）：涉及决策/结算，需合规 + 系统双审批',
}
const riskTooltipText = computed(() => {
  const lvl = props.skill.risk_level
  if (!lvl) return '风险等级未指定'
  return RISK_TOOLTIPS[lvl] || `风险等级 ${lvl}`
})

// ── 元字段文案 ──
const ownerText = computed(() => {
  const raw = props.skill.owner_name || props.skill.owner
  return raw && String(raw).trim() ? String(raw) : '—'
})

const usageText = computed(() => {
  const n = props.skill.usage_count
  if (typeof n !== 'number' || !Number.isFinite(n)) return '—'
  if (n >= 10000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
})

const lastRunText = computed(() => {
  // 没有 last_run_at 字段时回落到 updated_at，并在 tooltip 里说明
  const ts = props.skill.last_run_at || props.skill.updated_at
  return ts ? relativeTime(ts) : '—'
})

const lastRunTooltip = computed(() => {
  if (props.skill.last_run_at) {
    return `最后运行：${formatTime(props.skill.last_run_at)}`
  }
  if (props.skill.updated_at) {
    return `暂无运行记录，显示最近更新时间：${formatTime(props.skill.updated_at)}`
  }
  return '暂无运行记录'
})

// ── 触发类型 ──
const TRIGGER_LABEL: Record<string, string> = {
  manual: '手动',
  cron: '定时',
  event: '事件',
}
const triggerLabel = computed(() => {
  const t = props.skill.trigger_type
  if (!t) return '手动'
  return TRIGGER_LABEL[t] || t
})

// ── a11y ──
const cardAriaLabel = computed(() => {
  const name = props.skill.name || props.skill.id
  const dept = props.skill.department || '未指定部门'
  return `Skill ${name}，状态 ${statusText.value}，部门 ${dept}`
})

// ── 导出动作（复制自 SkillCard 的逻辑，保持行为一致） ──
function normalizeExportBlob(payload: unknown): Blob {
  const candidate = (payload as { data?: unknown } | null)?.data ?? payload
  if (candidate instanceof Blob) return candidate
  if (candidate instanceof ArrayBuffer) return new Blob([candidate], { type: 'application/zip' })
  throw new TypeError('导出响应不是有效的文件数据')
}

function archiveFileName(): string {
  const version = String(props.skill.current_version || '').trim()
  return version ? `${props.skill.id}-${version}.zip` : `${props.skill.id}.zip`
}

async function onExport() {
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
</script>

<style scoped>
.skill-list-card-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
}

.skill-list-card {
  width: 100%;
  min-height: 228px;
  display: flex;
  flex-direction: column;
  cursor: pointer;
  transition: box-shadow var(--sf-transition-fast), transform var(--sf-transition-fast);
}

.skill-list-card :deep(.arco-card-body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
  padding: 16px;
}

.skill-list-card:hover {
  transform: translateY(-1px);
  box-shadow: var(--sf-shadow-hover, 0 10px 24px rgba(0, 0, 0, 0.08));
}

/* ── 头部 ── */
.card-header {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 22px;
}
.status-tag {
  flex-shrink: 0;
}
.card-head-spacer {
  flex: 1;
}
.card-version {
  font-size: var(--sf-text-tiny, 11px);
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  white-space: nowrap;
}

/* ── 标题 ── */
.card-title {
  font-size: var(--sf-font-md, 14px);
  font-weight: 600;
  color: var(--ai-ink-1);
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  min-height: 20px;
}
.card-id {
  font-size: var(--sf-text-tiny, 11px);
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-height: 14px;
}
.card-id-placeholder {
  visibility: hidden;
}

/* ── 元字段网格 ── */
.card-meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px 12px;
  padding: 8px 10px;
  background: var(--ai-surface-2);
  border-radius: 6px;
  border-left: 2px solid rgb(var(--primary-5));
}
.meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: var(--sf-text-caption, 12px);
  color: var(--ai-ink-3);
  min-width: 0;
}
.meta-icon {
  font-size: 12px;
  color: var(--ai-ink-4);
  flex-shrink: 0;
}
.meta-label {
  color: var(--ai-ink-4);
  flex-shrink: 0;
  letter-spacing: 0.02em;
}
.meta-value {
  color: var(--ai-ink-1);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}

/* ── 底部：触发 chip + 操作按钮 ── */
.card-footer {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: auto;
  min-height: 24px;
}
.trigger-chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: var(--sf-radius-pill, 12px);
  font-size: var(--sf-text-tiny, 11px);
  font-weight: 600;
  background: rgba(var(--arcoblue-6), 0.08);
  color: var(--ai-info);
  line-height: 1.5;
}
.trigger-chip-muted {
  background: var(--ai-surface-2);
  color: var(--ai-ink-4);
}
.card-spacer {
  flex: 1;
}
.card-actions {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
.card-icon-btn {
  opacity: 0;
  transition: opacity var(--sf-transition-fast);
}
.skill-list-card:hover .card-icon-btn {
  opacity: 1;
}
</style>
