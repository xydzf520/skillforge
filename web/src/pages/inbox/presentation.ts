import { bjtParts, dateFromBjtParts, formatBjtDateTimeInput, toDate } from '@/utils/format'
import type {
  InboxDecisionMode,
  InboxTodoKind,
  InboxTodoStatus,
  ReportChannel,
  ReportMetricTrend,
  ReportTriggerType,
} from '@/types/inbox'

// Base prefix（保留旧常量，供测试 / 向后兼容 import）
// 真实读写时必须调用 `reportsLastSeenKey(userId)` 生成按账号隔离的 key
export const REPORTS_LAST_SEEN_KEY = 'sf-inbox-reports-last-seen'

/**
 * 生成按 user_id 隔离的红点 localStorage key。
 * userId 为空时退化到 'anonymous'，保证同一浏览器切换账号时红点状态独立。
 */
export function reportsLastSeenKey(userId: string): string {
  return `${REPORTS_LAST_SEEN_KEY}:${userId || 'anonymous'}`
}

export function readQueryText(value: unknown): string {
  if (Array.isArray(value)) return String(value[0] || '')
  if (value == null) return ''
  return String(value)
}

export function readQueryPositiveInt(value: unknown, fallback = 1): number {
  const parsed = Number.parseInt(readQueryText(value), 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

export function defaultInboxDateRange(days = 7, now: string | number | Date = Date.now()): string[] {
  const parts = bjtParts(now)
  if (!parts) return []
  const windowDays = Math.max(1, Math.floor(days))
  const start = dateFromBjtParts(parts.year, parts.month, parts.day, 0, 0, 0)
  start.setUTCDate(start.getUTCDate() - windowDays + 1)
  const end = dateFromBjtParts(parts.year, parts.month, parts.day, 23, 59, 59)
  return [formatBjtDateTimeInput(start), formatBjtDateTimeInput(end)]
}

export function reportChannelLabel(channel?: ReportChannel | null): string {
  return {
    dingtalk_card: '钉钉卡片',
    dingtalk_markdown: '钉钉 Markdown',
    email: '邮件',
    feishu: '飞书',
  }[channel || ''] || channel || '未声明渠道'
}

export function reportTriggerLabel(triggerType?: ReportTriggerType | null): string {
  return {
    cron: '定时触发',
    manual: '手动执行',
    event: '事件触发',
  }[triggerType || ''] || triggerType || '未知触发'
}

export function reportTrendTone(trend?: ReportMetricTrend | null): string {
  return {
    up: 'up',
    down: 'down',
    flat: 'flat',
  }[trend || ''] || 'flat'
}

export function todoKindLabel(kind?: InboxTodoKind | null): string {
  return (kind || '') === 'dispatch' ? '派发' : '审核'
}

export function todoKindColor(kind?: InboxTodoKind | null): string {
  return (kind || '') === 'dispatch' ? 'orange' : 'blue'
}

export function todoStatusLabel(status?: InboxTodoStatus | null): string {
  return {
    pending: '待处理',
    approved: '已派发',
    rejected: '已驳回',
    done: '已处理',
    expired: '已过期',
    resolved_by_peer: '同事已处理',
  }[status || ''] || status || '-'
}

export function todoStatusColor(status?: InboxTodoStatus | null): string {
  return {
    pending: 'arcoblue',
    approved: 'green',
    rejected: 'red',
    done: 'green',
    expired: 'gray',
    resolved_by_peer: 'purple',
  }[status || ''] || 'gray'
}

export function decisionModeLabel(mode?: InboxDecisionMode | null): string {
  return {
    any_of: '任一处理即可',
    all_of: '全部处理',
    independent: '独立处理',
  }[mode || ''] || mode || '-'
}

export function dispatchStatusLabel(status?: string | null): string {
  return {
    awaiting_dispatch: '待派发',
    sent: '待完成',
    pushed_no_dingtalk: '钉钉未送达',
    done: '已完成',
    cancelled: '已取消',
  }[status || ''] || status || '-'
}

export function dispatchStatusColor(status?: string | null): string {
  return {
    awaiting_dispatch: 'gray',
    sent: 'arcoblue',
    pushed_no_dingtalk: 'red',
    done: 'green',
    cancelled: 'red',
  }[status || ''] || 'gray'
}

export function prettyJson(value: unknown): string {
  if (value == null) return '{}'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

/**
 * 卡片 summary 清洗：源数据常带 markdown 表格/代码块/图片，在 2-3 行卡片里会被腰斩成 `|-----|`，不可读。
 * 这里剥掉典型 markdown 结构，只保留首段可读文本，裁到 maxLen。
 */
export function sanitizeSummaryText(raw?: string | null, maxLen = 120): string {
  if (!raw) return ''
  let text = String(raw)

  const objectLikeSummary = summarizeObjectLikeText(text)
  if (objectLikeSummary) {
    return objectLikeSummary.length > maxLen ? objectLikeSummary.slice(0, maxLen - 1) + '…' : objectLikeSummary
  }

  // 去代码块 / 行内代码
  text = text.replace(/```[\s\S]*?```/g, ' ')
  text = text.replace(/`([^`]+)`/g, '$1')
  // 去图片 / 链接，保留 alt / link 文字
  text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
  text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
  // 按行处理：干掉表格行和分隔行、标题 #
  const lines = text.split(/\r?\n/).map((line) => {
    const trimmed = line.trim()
    if (!trimmed) return ''
    // 表格分隔 / 表格行
    if (/^\|?\s*:?-{2,}/.test(trimmed)) return ''
    if (/^\|.*\|\s*$/.test(trimmed)) return ''
    // 引用 / 列表前缀
    const stripped = trimmed
      .replace(/^#{1,6}\s+/, '')
      .replace(/^[-*+]\s+/, '')
      .replace(/^\d+\.\s+/, '')
      .replace(/^>\s*/, '')
    // markdown 强调
    return stripped.replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\*([^*]+)\*/g, '$1')
  }).filter(Boolean)

  text = lines.join(' · ').replace(/\s+/g, ' ').trim()
  if (text.length > maxLen) text = text.slice(0, maxLen - 1) + '…'
  return text
}

function summarizeObjectLikeText(raw: string): string {
  const text = raw.trim()
  if (!text.startsWith('{') || !text.includes(':')) return ''

  const skillName = text.match(/['"]skill_name['"]\s*:\s*['"]([^'"]+)['"]/)?.[1]
  const runMode = text.match(/['"]run_mode['"]\s*:\s*['"]([^'"]+)['"]/)?.[1]
  const keyMatches = Array.from(text.matchAll(/['"]([^'"]{2,36})['"]\s*:/g))
    .map((m) => m[1])
    .filter((key) => !['metadata', 'run_mode', 'sample_used', 'skill_name', 'skill_id'].includes(key))
  const keys = Array.from(new Set(keyMatches)).slice(0, 4)

  if (skillName) {
    const mode = runMode === 'sandbox_test' ? '沙箱运行' : '执行输出'
    return keys.length ? `${skillName} · ${mode} · ${keys.join('、')}` : `${skillName} · ${mode}`
  }
  if (keys.length) {
    return `执行输出包含 ${keys.join('、')} 等字段，暂无可读摘要`
  }
  return ''
}

/**
 * 相对时间短格式，用于 SLA chip：
 *  - 已过期 → `超期 3h`
 *  - 1 分钟内 → `剩 <1m`
 *  - 小时内 → `剩 42m`
 *  - 2 天内 → `剩 22h 3m`
 *  - 更远 → `剩 3d`
 */
export function formatRelativeDeadline(iso?: string | null, now: number = Date.now()): string {
  if (!iso) return ''
  const target = toDate(iso)?.getTime()
  if (!Number.isFinite(target)) return ''
  const diff = Number(target) - now
  const abs = Math.abs(diff)
  const min = 60 * 1000
  const hour = 60 * min
  const day = 24 * hour
  const prefix = diff < 0 ? '超期' : '剩'

  if (abs < min) return `${prefix} <1m`
  if (abs < hour) return `${prefix} ${Math.floor(abs / min)}m`
  if (abs < 2 * day) {
    const h = Math.floor(abs / hour)
    const m = Math.floor((abs % hour) / min)
    return m > 0 ? `${prefix} ${h}h ${m}m` : `${prefix} ${h}h`
  }
  return `${prefix} ${Math.floor(abs / day)}d`
}

export type DeadlineUrgency = 'overdue' | 'urgent' | 'soon' | 'normal' | 'none'

export function deadlineUrgency(iso?: string | null, now: number = Date.now()): DeadlineUrgency {
  if (!iso) return 'none'
  const target = toDate(iso)?.getTime()
  if (!Number.isFinite(target)) return 'none'
  const diff = Number(target) - now
  if (diff < 0) return 'overdue'
  if (diff < 2 * 60 * 60 * 1000) return 'urgent'    // <2h
  if (diff < 24 * 60 * 60 * 1000) return 'soon'     // <24h
  return 'normal'
}

export function hasUnreadReports(latestCreatedAt?: string | null, lastSeenAt?: string | null): boolean {
  const latest = toDate(latestCreatedAt)
  if (!latest) return false
  const lastSeen = toDate(lastSeenAt)
  return !lastSeen || latest.getTime() > lastSeen.getTime()
}

export function readReportsLastSeen(userId: string): string {
  try {
    return window.localStorage.getItem(reportsLastSeenKey(userId)) || ''
  } catch {
    return ''
  }
}

export function markReportsLastSeen(userId: string, timestamp: string = new Date().toISOString()): void {
  try {
    window.localStorage.setItem(reportsLastSeenKey(userId), timestamp)
  } catch {
    // localStorage 不可用不影响主流程
  }
}
