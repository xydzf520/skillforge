/**
 * 全局状态映射常量
 */

export type StatusColor = 'orangered' | 'arcoblue' | 'green' | 'gray' | 'red' | 'purple'

// Skill 状态
export const skillStatusLabel: Record<'draft' | 'shadow' | 'active' | 'deprecated', string> = { draft: '草稿', shadow: '影子运行', active: '正式运行', deprecated: '已停用' }
export const skillStatusColor: Record<'draft' | 'shadow' | 'active' | 'deprecated', StatusColor> = { draft: 'orangered', shadow: 'arcoblue', active: 'green', deprecated: 'gray' }

// Skill 风险等级
export const riskColor: Record<'R1' | 'R2' | 'R3' | 'R4', StatusColor> = { R1: 'green', R2: 'arcoblue', R3: 'orangered', R4: 'red' }
export const riskLabel: Record<'R1' | 'R2' | 'R3' | 'R4', string> = { R1: 'R1-低', R2: 'R2-中', R3: 'R3-高', R4: 'R4-极高' }

// 审核状态
export const reviewStatusLabel: Record<'pending' | 'approved' | 'rejected', string> = { pending: '待审核', approved: '已通过', rejected: '已驳回' }
export const reviewStatusColor: Record<'pending' | 'approved' | 'rejected', StatusColor> = { pending: 'orangered', approved: 'green', rejected: 'red' }

// 执行状态
export const runStatusLabel: Record<'success' | 'failed' | 'running' | 'pending' | 'completed', string> = { success: '成功', failed: '失败', running: '运行中', pending: '等待中', completed: '已完成' }
export const runStatusColor: Record<'success' | 'failed' | 'running' | 'pending' | 'completed', StatusColor> = { success: 'green', failed: 'red', running: 'arcoblue', pending: 'gray', completed: 'green' }

// 用户角色 —— 同时覆盖 v1（legacy）与 v2 角色体系；使用点用 Record<string, string> 兜底
// v2 权威术语：system_admin / dept_admin / engineer / biz_owner / aibp / member / observer
// v1 legacy 兼容：admin → system_admin、ai_engineer → engineer、operator/director 保留原语
export const roleLabel: Record<string, string> = {
  // v2（推荐使用）
  system_admin: '系统管理员',
  dept_admin: '部门管理员',
  engineer: '工程师',
  biz_owner: '业务负责人',
  aibp: 'AIBP',
  member: '成员',
  observer: '观察员',
  // v1 legacy（读老数据时继续能显示）
  admin: '管理员',
  ai_engineer: 'AI工程师',
  operator: '运营',
  director: '总监',
  viewer: '查看者',
}

export const roleColor: Record<string, StatusColor> = {
  // v2
  system_admin: 'red',
  dept_admin: 'orangered',
  engineer: 'arcoblue',
  biz_owner: 'green',
  aibp: 'purple',
  member: 'gray',
  observer: 'gray',
  // v1 legacy
  admin: 'red',
  ai_engineer: 'arcoblue',
  operator: 'gray',
  director: 'orangered',
  viewer: 'gray',
}

// 数据源状态
export const dsStatusLabel: Record<'active' | 'inactive' | 'error', string> = { active: '正常', inactive: '未激活', error: '异常' }
export const dsStatusColor: Record<'active' | 'inactive' | 'error', StatusColor> = { active: 'green', inactive: 'gray', error: 'red' }
