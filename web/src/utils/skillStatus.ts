/**
 * Skill 状态 i18n bridge
 *
 * 集中 `skill.status` 的中文 label / 颜色映射；包装 utils/constants 的 skillStatusLabel / skillStatusColor
 * 并额外提供 `getStatusLabel(raw, fallback)`，在后端返回异常/自定义 status 值时不至于直接展示英文原文。
 *
 * 使用场景：
 *   - PortalSkillDetail / SkillCard / SkillsHome / SkillList 的 status tag 文案统一走这里
 *   - 避免把 "draft/shadow/active/deprecated" 英文字段直接喷给最终用户
 */
import { skillStatusLabel, skillStatusColor, riskColor } from './constants'

export { skillStatusLabel, skillStatusColor, riskColor }

type SkillStatusKey = keyof typeof skillStatusLabel

/** 安全获取 skill 状态的中文 label；未知 status 返回 fallback。 */
export function getStatusLabel(status?: string | null, fallback = '未知'): string {
  if (!status) return fallback
  return (skillStatusLabel as Record<string, string>)[status] || fallback
}

/** 安全获取 skill 状态的 tag 颜色；未知 status 返回 'gray'。 */
export function getStatusColor(status?: string | null): string {
  if (!status) return 'gray'
  return (skillStatusColor as Record<string, string>)[status] || 'gray'
}

/** 风险等级颜色（R1-R4）；未知返回 'gray'。 */
export function getRiskColor(level?: string | null): string {
  if (!level) return 'gray'
  return (riskColor as Record<string, string>)[level] || 'gray'
}

/** 风险等级人话 tooltip 文案 */
const RISK_TOOLTIPS: Record<string, string> = {
  R1: 'R1（低）：自动通过，无需人工复核',
  R2: 'R2（中）：可编辑，发布需部门管理员审核',
  R3: 'R3（高）：涉及对外信息/金额，需系统管理员审批',
  R4: 'R4（极高）：涉及决策/结算，需合规 + 系统双审批',
}
export function getRiskTooltip(level?: string | null): string {
  if (!level) return '风险等级未指定'
  return RISK_TOOLTIPS[level] || `风险等级 ${level}`
}

export type { SkillStatusKey }
