/**
 * /admin 左栏菜单 —— 数据驱动
 *
 * v2.11.2：按"域"分组为 5 组：
 *   账号与组织 / 治理与合规 / 数据与服务 / AI 配置与模型 / 系统
 *
 * 变化点：原"执行与成本"已下线，Agent 终端 + AI 成本 并入「数据与服务」。
 * 原 AI 配置下的 MCP Servers 与 平台 API 注册表 独立为子域，也并入「数据与服务」。
 * /connections 独立为管理后台「连接管理」，同样归「数据与服务」。
 */

export type AdminMenuItem = {
  key: string
  label: string
  iconName: string
  to: string
  /** 匹配额外 path 前缀用于高亮（比如 /admin/users/pending 归到 users） */
  matchPrefix?: string[]
  /** 可见角色白名单；不填 = 全部 admin-like 角色均可见 */
  visibleRoles?: string[]
}

export type AdminMenuGroup = {
  key: string
  label: string
  items: AdminMenuItem[]
}

/** 所有 admin 菜单都要求的基础角色集合（任一即可进 /admin 壳） */
export const ADMIN_SHELL_ROLES = ['admin', 'system_admin']

const ADMIN_ONLY = ['admin', 'system_admin']


export const ADMIN_MENU_GROUPS: AdminMenuGroup[] = [
  {
    key: 'identity',
    label: '账号与组织',
    items: [
      {
        key: 'users',
        label: '用户',
        iconName: 'user',
        to: '/admin/users',
        matchPrefix: ['/admin/users'],
        visibleRoles: ADMIN_ONLY,
      },
      {
        key: 'org',
        label: '组织架构',
        iconName: 'tree',
        to: '/admin/org',
        visibleRoles: ADMIN_ONLY,
      },
    ],
  },
  {
    key: 'governance',
    label: '治理与合规',
    items: [
      {
        key: 'audit',
        label: '审计日志',
        iconName: 'doc',
        to: '/admin/audit',
        visibleRoles: ADMIN_ONLY,
      },
      {
        key: 'compliance',
        label: '合规规则',
        iconName: 'shield',
        to: '/admin/compliance',
        visibleRoles: ADMIN_ONLY,
      },
      {
        key: 'data-requests',
        label: '数据访问审批',
        iconName: 'doc',
        to: '/admin/data-requests',
        visibleRoles: ADMIN_ONLY,
      },
    ],
  },
  {
    key: 'data-services',
    label: '数据与服务',
    items: [
      {
        key: 'connections',
        label: '连接管理',
        iconName: 'link',
        to: '/admin/connections',
        visibleRoles: ADMIN_ONLY,
      },
      {
        key: 'connector-keys',
        label: 'Connector Keys',
        iconName: 'shield',
        to: '/admin/connector-keys',
        visibleRoles: ADMIN_ONLY,
      },
      {
        key: 'collection-health',
        label: '采集健康',
        iconName: 'trend',
        to: '/admin/connections/health',
        visibleRoles: ADMIN_ONLY,
      },
      {
        key: 'mcp-servers',
        label: 'MCP Servers',
        iconName: 'database',
        to: '/admin/mcp-servers',
        visibleRoles: ADMIN_ONLY,
      },
      {
        key: 'codex-plugin',
        label: 'Codex 插件',
        iconName: 'doc',
        to: '/admin/codex-plugin',
        visibleRoles: ADMIN_ONLY,
      },
      {
        key: 'agent-devices',
        label: 'Agent终端',
        iconName: 'bot',
        to: '/admin/agent-devices',
        matchPrefix: ['/admin/agent-devices'],
        visibleRoles: ADMIN_ONLY,
      },
      {
        key: 'platform-apis',
        label: '平台 API 注册表',
        iconName: 'list',
        to: '/admin/platform-apis',
        visibleRoles: ADMIN_ONLY,
      },
      {
        key: 'data-hygiene',
        label: '数据卫生',
        iconName: 'check',
        to: '/admin/data-hygiene',
        visibleRoles: ADMIN_ONLY,
      },
      {
        key: 'costs',
        label: 'AI 成本',
        iconName: 'gpu',
        to: '/admin/costs',
        visibleRoles: ADMIN_ONLY,
      },
      {
        key: 'run-trace',
        label: 'Run Trace',
        iconName: 'flow',
        to: '/admin/runs/trace',
        matchPrefix: ['/admin/runs/'],
        visibleRoles: ADMIN_ONLY,
      },
    ],
  },
  {
    key: 'ai',
    label: 'AI 配置与模型',
    items: [
      {
        key: 'ai-config',
        label: 'AI 配置',
        iconName: 'cube',
        to: '/admin/ai-config',
        visibleRoles: ADMIN_ONLY,
      },
      {
        key: 'prompts',
        label: 'Prompt 库',
        iconName: 'doc',
        to: '/admin/prompts',
        visibleRoles: ADMIN_ONLY,
      },
    ],
  },
  {
    key: 'system',
    label: '系统',
    items: [
      {
        key: 'settings',
        label: '系统设置',
        iconName: 'cube',
        to: '/admin/settings',
        visibleRoles: ADMIN_ONLY,
      },
    ],
  },
]

/** 根据用户角色过滤菜单 */
export function filterAdminMenu(role: string | undefined): AdminMenuGroup[] {
  if (!role || !ADMIN_ONLY.includes(role)) return []
  return ADMIN_MENU_GROUPS
    .map((g) => ({
      ...g,
      items: g.items.filter((i) => !i.visibleRoles || i.visibleRoles.includes(role)),
    }))
    .filter((g) => g.items.length > 0)
}

/** 展平为线性 items 列表，用于搜索 */
export const ADMIN_MENU_FLAT: AdminMenuItem[] = ADMIN_MENU_GROUPS.flatMap((g) => g.items)

/** 给定当前路径，返回匹配的 item key（用于高亮）。支持 matchPrefix 兜底。 */
export function matchAdminMenuKey(path: string): string | null {
  for (const item of ADMIN_MENU_FLAT) {
    const prefixes = item.matchPrefix || [item.to]
    if (prefixes.some((p) => path === p || path.startsWith(`${p}/`))) return item.key
  }
  return null
}
