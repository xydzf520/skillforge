/**
 * 一级导航 (primary nav) key 与迁移表。
 *
 * 从 AppLayout.vue 抽出，便于在单测中直接 import 校验。
 * 当增减一级导航或做 key 重命名时，同时更新 AppLayout.vue 中的 primaryNavItems。
 */

export const PRIMARY_NAV_KEYS = ['hall', 'workflow', 'training', 'projects', 'tasktree', 'sf', 'inbox', 'admin'] as const

export type PrimaryNavKey = typeof PRIMARY_NAV_KEYS[number]

export const PRIMARY_NAV_LABELS: Record<PrimaryNavKey, string> = {
  hall: '能力大厅',
  workflow: 'AI Workflow',
  training: '训练',
  projects: '项目',
  tasktree: '任务树',
  sf: '插件',
  inbox: '收件',
  admin: '管理后台',
}

export const WORKFLOW_DEFAULT_PATH = '/learning-flow'

export const WORKFLOW_SUBNAV_KEYS = ['learning', 'agent', 'skills', 'knowledge'] as const

export type WorkflowSubnavKey = typeof WORKFLOW_SUBNAV_KEYS[number]

export const WORKFLOW_SUBNAV_LABELS: Record<WorkflowSubnavKey, string> = {
  skills: 'Skills',
  knowledge: '知识库',
  agent: 'Agent',
  learning: '脉动',
}

/**
 * 已知一级导航迁移：旧 key → 新 key。
 * v1.x：'portal' 曾为独立一级导航，后并入 'skills'，现在按项目入口迁入 'projects'。
 * v2.0.12：'todos' 升级为 'inbox'（inbox-reports-plan §6.3）。
 * v2.2.0（O6）：'hall' 从一级导航并入 Skills（当时暂时折叠）。
 * v2.7.2（回迁）：'hall' 重新提为独立一级导航 —— 按能力（业务场景）分类的
 *                 内部能力资产中心。
 * v2.next：'brain' 按 Agent Runtime 文档改名为 'agent'，训练按 GB10 → Mac236
 *          自动化链路升为独立一级导航。
 * v2.next：'admin' 按 AI Studio Redesign 作为管理员角色的一层入口展示。
 * v2.next：'workflow' 聚合 脉动 / Agent / Skills / 知识库 / 训练；
 *          'projects' 独立承载 Playbook 项目。
 */
export const PRIMARY_NAV_MIGRATIONS: Record<string, PrimaryNavKey> = {
  portal: 'projects',
  playbooks: 'projects',
  skills: 'workflow',
  knowledge: 'workflow',
  agent: 'workflow',
  learning: 'workflow',
  todos: 'inbox',
  brain: 'workflow',
}

export const LAST_PRIMARY_NAV_STORAGE_KEY = 'lastPrimaryNav'

export const DEFAULT_PRIMARY_NAV: PrimaryNavKey = 'hall'

/**
 * 校验并迁移 localStorage 里的 lastPrimaryNav。
 *
 * - 仍在 PRIMARY_NAV_KEYS：保留并返回
 * - 在 PRIMARY_NAV_MIGRATIONS：写回新 key、返回新 key
 * - 其它（含未知 / 空）：清除 storage、返回 DEFAULT_PRIMARY_NAV
 */
export function migratePrimaryNavStorage(
  storage: Storage = safeLocalStorage(),
): PrimaryNavKey {
  try {
    const stored = storage.getItem(LAST_PRIMARY_NAV_STORAGE_KEY)
    if (!stored) return DEFAULT_PRIMARY_NAV
    if ((PRIMARY_NAV_KEYS as readonly string[]).includes(stored)) {
      return stored as PrimaryNavKey
    }
    const migrated = PRIMARY_NAV_MIGRATIONS[stored]
    if (migrated) {
      storage.setItem(LAST_PRIMARY_NAV_STORAGE_KEY, migrated)
      return migrated
    }
    // 未知 key（旧实验 / 手改）→ 清除，fallback。
    storage.removeItem(LAST_PRIMARY_NAV_STORAGE_KEY)
    return DEFAULT_PRIMARY_NAV
  } catch {
    return DEFAULT_PRIMARY_NAV
  }
}

/**
 * 尝试把当前 primary key 写回 storage；非合法 key 或 storage 不可用时静默忽略。
 */
export function persistPrimaryNav(
  key: string | undefined,
  storage: Storage = safeLocalStorage(),
): void {
  if (typeof key !== 'string') return
  if (!(PRIMARY_NAV_KEYS as readonly string[]).includes(key)) return
  try {
    storage.setItem(LAST_PRIMARY_NAV_STORAGE_KEY, key)
  } catch {
    // storage 禁用时忽略
  }
}

function safeLocalStorage(): Storage {
  // 测试环境某些情况下 localStorage 可能被 mock 或不存在
  if (typeof window !== 'undefined' && window.localStorage) {
    return window.localStorage
  }
  // 返回一个 noop 替身，保证签名兼容
  return {
    getItem: () => null,
    setItem: () => undefined,
    removeItem: () => undefined,
    clear: () => undefined,
    key: () => null,
    length: 0,
  }
}
