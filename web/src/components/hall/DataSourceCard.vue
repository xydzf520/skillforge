<template>
  <div class="ds-card-wrap" role="button" tabindex="0" :aria-label="cardAriaLabel">
    <a-card class="ds-card" hoverable @click="$emit('click', source)">
      <div class="card-top">
        <a-tag size="small" :color="visColor">{{ visLabel }}</a-tag>
        <a-tooltip :content="freshnessTooltip" position="top">
          <a-tag size="small" :color="freshnessColor">{{ freshnessLabel }}</a-tag>
        </a-tooltip>
      </div>
      <div class="card-title">{{ source.name || source.id }}</div>
      <div class="card-desc">{{ source.description || '暂无说明' }}</div>
      <div class="card-meta">
        <span v-if="source.department" class="meta-item">
          <icon-idcard />{{ source.department }}
        </span>
        <span v-if="source.owner_contact" class="meta-item">
          <icon-user />{{ source.owner_contact }}
        </span>
        <span v-if="source.source_type" class="meta-item">
          <icon-storage />{{ sourceTypeLabel }}
        </span>
      </div>
      <div v-if="source.usage_hint" class="card-hint">
        <icon-bulb />{{ source.usage_hint }}
      </div>
      <div v-if="consumersCount" class="card-consumers">
        <icon-branch />被 {{ consumersCount }} 个 Skill 使用
      </div>
      <div class="card-footer">
        <span v-if="expiringWarning" class="expire-warn">
          <icon-exclamation-circle />{{ expiringWarning }}
        </span>
        <span class="card-spacer" />
        <a-button
          v-if="accessStatus === 'granted'"
          size="mini"
          type="primary"
          status="success"
          @click.stop="$emit('view', source)"
        >
          <template #icon><icon-eye /></template>查看数据
        </a-button>
        <a-button
          v-else-if="accessStatus === 'pending'"
          size="mini"
          type="outline"
          disabled
        >
          <template #icon><icon-clock-circle /></template>审批中
        </a-button>
        <a-button
          v-else
          size="mini"
          type="outline"
          @click.stop="$emit('request', source)"
        >
          <template #icon><icon-plus /></template>申请访问
        </a-button>
      </div>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  IconBranch,
  IconBulb,
  IconClockCircle,
  IconExclamationCircle,
  IconEye,
  IconIdcard,
  IconPlus,
  IconStorage,
  IconUser,
} from '@arco-design/web-vue/es/icon'
import { toDate } from '@/utils/format'

type DataSourceLike = {
  id: string
  name?: string
  description?: string
  usage_hint?: string
  department?: string
  owner_contact?: string
  source_type?: string
  visibility?: string
  freshness_status?: string
  consumers?: unknown[]
  related_skills?: string[]
  my_access?: { status?: string; expires_at?: string | null; request_id?: number }
}

const props = defineProps<{ source: DataSourceLike }>()
defineEmits<{
  (e: 'click', s: DataSourceLike): void
  (e: 'view', s: DataSourceLike): void
  (e: 'request', s: DataSourceLike): void
}>()

const visLabel = computed(() => {
  const map: Record<string, string> = { company: '全公司', department: '部门', private: '私有' }
  return map[props.source.visibility || 'department']
})
const visColor = computed(() => {
  const map: Record<string, string> = { company: 'arcoblue', department: 'gray', private: 'red' }
  return map[props.source.visibility || 'department']
})

const sourceTypeLabel = computed(() => {
  const map: Record<string, string> = {
    csv_upload: 'CSV',
    api_pull: 'API',
    crawler: '爬虫',
  }
  return map[props.source.source_type || ''] || props.source.source_type
})

const freshnessLabel = computed(() => {
  const m: Record<string, string> = { fresh: '新鲜', stale: '滞后', unknown: '未知' }
  return m[props.source.freshness_status || 'unknown']
})
const freshnessColor = computed(() => {
  const m: Record<string, string> = { fresh: 'green', stale: 'orange', unknown: 'gray' }
  return m[props.source.freshness_status || 'unknown']
})
const freshnessTooltip = computed(() => {
  const m: Record<string, string> = {
    fresh: '数据在时效窗口内',
    stale: '超过 stale_threshold_hours 未更新',
    unknown: '尚未记录新鲜度（无 ingestion 日志）',
  }
  return m[props.source.freshness_status || 'unknown']
})

const accessStatus = computed(() => props.source.my_access?.status || 'none')

const expiringWarning = computed(() => {
  const ex = props.source.my_access?.expires_at
  if (!ex || accessStatus.value !== 'granted') return ''
  const expDate = toDate(ex)
  if (!expDate) return ''
  const days = Math.ceil((expDate.getTime() - Date.now()) / (1000 * 60 * 60 * 24))
  if (days >= 7) return ''
  if (days < 0) return '授权已过期'
  return `将于 ${days} 天后过期`
})

const consumersCount = computed(() => {
  if (Array.isArray(props.source.consumers)) return props.source.consumers.length
  if (Array.isArray(props.source.related_skills)) return props.source.related_skills.length
  return 0
})

const cardAriaLabel = computed(() =>
  `数据源 ${props.source.name || props.source.id}，可见性 ${visLabel.value}，访问状态 ${accessStatus.value}`
)
</script>

<style scoped>
.ds-card-wrap {
  cursor: pointer;
}
.ds-card {
  border-radius: 8px;
  transition: box-shadow 0.2s;
}
.ds-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
}
.card-top {
  display: flex;
  gap: 6px;
  margin-bottom: 8px;
}
.card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ai-ink-1);
  margin-bottom: 6px;
}
.card-desc {
  font-size: 12px;
  color: var(--ai-ink-2);
  margin-bottom: 8px;
  line-height: 1.5;
  min-height: 36px;
}
.card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.meta-item {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.card-hint {
  font-size: 12px;
  color: var(--ai-ink-2);
  background: var(--ai-surface-2);
  padding: 6px 8px;
  border-radius: 4px;
  margin: 6px 0;
  display: flex;
  gap: 5px;
  align-items: start;
}
.card-consumers {
  font-size: 12px;
  color: var(--ai-accent-ink);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin: 4px 0;
}
.card-footer {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--ai-border);
}
.expire-warn {
  font-size: 11px;
  color: var(--ai-bad);
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.card-spacer {
  flex: 1;
}
</style>
