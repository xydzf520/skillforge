/**
 * SkillForge 设计语义 tone 映射
 *
 * 目的：解决前端 UX 审计报告（2026-04-24）里 V1/V3/V4 的共同根因 ——
 * 同一条语义（角色、状态、趋势）在不同页面用了不同颜色。
 *
 * 本文件把所有"语义 → 颜色 tone"映射集中到一处，保证：
 * - 同一个业务状态（如 pending）在任何页面都走同一个 tone
 * - 换设计系统只改这一处
 * - `.sf-tone-{tone}` CSS 类在 styles/tone.css 中一并定义，提供 bg/fg/border
 */

// ── 六种语义 tone ──────────────────────────────
// danger  → 红（失败 / 离线 / 已驳回 / 高危角色 / 负向 delta）
// warning → 橙（逾期 / 卡住 / 待审批 / 中危）
// success → 绿（成功 / 在线 / 已通过 / 正向 delta）
// info    → 蓝（运行中 / 可执行的品牌色 / 工程师角色）
// brand   → 紫（AI / 会签 / 品牌强调）
// neutral → 灰（默认 / 停用 / 观察员 / 空态）
export type Tone = 'danger' | 'warning' | 'success' | 'info' | 'neutral' | 'brand'

// 所有合法 tone 值，供运行时校验 / CSS 类组合使用
export const TONES: readonly Tone[] = ['danger', 'warning', 'success', 'info', 'neutral', 'brand'] as const

/**
 * 角色 → tone。同时覆盖 v1 legacy 和 v2 权威角色：
 * v1: admin / ai_engineer / operator / director / viewer
 * v2: system_admin / dept_admin / engineer / biz_owner / aibp / member / observer
 *
 * 约束：admin 家族统一红（最高权限，视觉警示）；engineer 家族蓝（技术线）；
 * biz_owner/operator 绿（业务线）；aibp 紫（跨线协作）；member/observer/viewer 灰（只读）。
 */
export function roleTone(role?: string | null): Tone {
  if (!role) return 'neutral'
  const key = String(role).toLowerCase()
  switch (key) {
    // v2 admin 家族
    case 'system_admin':
      return 'danger'
    case 'dept_admin':
      return 'warning'
    // v1 legacy admin
    case 'admin':
      return 'danger'
    case 'director':
      return 'warning'
    // 工程师线
    case 'engineer':
    case 'ai_engineer':
      return 'info'
    // 业务线
    case 'biz_owner':
    case 'operator':
      return 'success'
    // 协作伙伴
    case 'aibp':
      return 'brand'
    // 只读 / 默认
    case 'member':
    case 'observer':
    case 'viewer':
    default:
      return 'neutral'
  }
}

/**
 * 状态 → tone。覆盖常见的审批 / 执行 / 同步 / 数据源状态。
 * 未命中的 status 返回 neutral，保证不会崩。
 */
export function statusTone(status?: string | null): Tone {
  if (!status) return 'neutral'
  const key = String(status).toLowerCase()

  // 审批 / Todo
  if (key === 'pending' || key === 'awaiting_dispatch') return 'warning'
  if (key === 'approved' || key === 'success' || key === 'completed' || key === 'done' || key === 'resolved') return 'success'
  if (key === 'rejected' || key === 'failed' || key === 'error' || key === 'timeout' || key === 'blocked') return 'danger'
  if (key === 'expired' || key === 'overdue' || key === 'stuck' || key === 'high_failure' || key === 'pushed_no_dingtalk') return 'danger'
  if (key === 'resolved_by_peer') return 'brand'

  // 执行 / 节点
  if (key === 'running' || key === 'in_progress' || key === 'dispatched' || key === 'sent' || key === 'online') return 'info'
  if (key === 'queued' || key === 'stale' || key === 'maybe_offline') return 'warning'
  if (key === 'offline') return 'danger'
  if (key === 'healthy') return 'success'

  // Skill / Review 生命周期
  if (key === 'draft') return 'warning'
  if (key === 'shadow' || key === 'ready') return 'info'
  if (key === 'active') return 'success'
  if (key === 'deprecated' || key === 'cancelled' || key === 'skipped' || key === 'idle') return 'neutral'

  return 'neutral'
}

/**
 * 指标 trend + delta → tone。
 * - trend 优先：up → success，down → danger，flat → neutral
 * - 只有 delta（无 trend）时：>0 → success，<0 → danger，=0 → neutral
 *
 * 注：如果调用方的业务语义是反的（比如"逾期数下降是好事"），应该在调用处手动传 trend，
 * 而不是依赖这里的默认判断。
 */
export function metricTone(trend?: 'up' | 'down' | 'flat' | null, delta?: number | null): Tone {
  if (trend === 'up') return 'success'
  if (trend === 'down') return 'danger'
  if (trend === 'flat') return 'neutral'
  if (typeof delta === 'number' && Number.isFinite(delta)) {
    if (delta > 0) return 'success'
    if (delta < 0) return 'danger'
  }
  return 'neutral'
}

/**
 * tone → Arco `<a-tag :color>` 值。
 * 用于把新 tone 语义塞进尚未迁移到 SfTagChip 的旧组件（例如 a-tag）。
 * 对照 utils/constants.ts 的 StatusColor union。
 */
export function toneToArcoColor(tone: Tone): string {
  switch (tone) {
    case 'danger': return 'red'
    case 'warning': return 'orangered'
    case 'success': return 'green'
    case 'info': return 'arcoblue'
    case 'brand': return 'purple'
    case 'neutral':
    default: return 'gray'
  }
}

/**
 * 方便模板里一把梭：返回 `sf-tone-{tone}` 类名字符串。
 */
export function toneClass(tone: Tone): string {
  return `sf-tone-${tone}`
}
