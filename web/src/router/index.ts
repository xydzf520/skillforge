/**
 * 路由配置：对应规格书27+页面
 */
import { createRouter, createWebHistory, type LocationQueryRaw, type RouteLocationNormalized, type RouteRecordRaw } from 'vue-router'
import { useUserStore } from '@/stores/user'

// D2 W7c: redirectToSkill / redirectToSkillWithQuery 原本服务 20 条 skill/skills 旧 alias，
// alias 已删除，两个 helper 随之退场，减少死代码。

function redirectToPlaybookMode(
  to: RouteLocationNormalized | any,
  mode: 'edit' | 'run',
): { path: string; query: LocationQueryRaw } {
  const name = to.params.name
  return {
    path: `/playbook/${String(Array.isArray(name) ? name[0] ?? '' : name || '')}`,
    query: {
      ...(to.query || {}),
      mode,
    },
  }
}

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/pages/Login.vue'),
    meta: { public: true },
  },
  {
    path: '/change-password',
    name: 'ChangePassword',
    component: () => import('@/pages/ChangePassword.vue'),
    meta: { public: true },
  },
  {
    // v2 pending 用户落地页（role-matrix-v2 §3.6）：
    // 需要登录（不 public），但绕过 AppLayout，展示独立全屏卡片。
    // 守卫逻辑：isPending 用户访问任何非 /pending 路径都会被顶回这里；
    // 非 pending 用户若访问 /pending 会被重定向到 /。
    path: '/pending',
    name: 'Pending',
    component: () => import('@/pages/Pending.vue'),
    meta: { title: '待激活' },
  },
  {
    // 项目独立运行页：不经过 AppLayout / 项目看板包裹，仅保留不可见 Gateway 宿主。
    path: '/project-run/:id',
    name: 'ProjectStandaloneRun',
    component: () => import('@/pages/project/ProjectDetail.vue'),
    meta: { title: '项目运行', standaloneProject: true },
  },
  {
    path: '/',
    component: () => import('@/layouts/AppLayout.vue'),
    children: [
      // Skill 管理
      // /skills        → Skill 列表
      // /skills/list   → 旧列表地址，重定向到 /skills
      // /skills/new    → 新建 Skill
      // /skills/:id    → 编辑 Skill（Studio）
      { path: '', redirect: '/hall' },
      // v2.7.2 大厅提升为一级导航：按"能力（业务场景）"分类的内部能力资产中心
      { path: 'hall', name: 'HallHome', component: () => import('@/pages/hall/HallHome.vue'), meta: { title: '能力大厅', primary: 'hall' } },
      { path: 'hall/finetuned-model-chat', name: 'HallFinetunedModelChat', component: () => import('@/pages/hall/HallFinetunedModelChat.vue'), meta: { title: '全量微调大模型对话', primary: 'hall' } },
      { path: 'hall/abilities/:id', name: 'HallDirectCapabilityDetail', component: () => import('@/pages/hall/HallDirectCapabilityDetail.vue'), meta: { title: '能力详情', primary: 'hall' } },
      { path: 'hall/capability/:category', name: 'HallCapabilityDetail', component: () => import('@/pages/hall/HallCapabilityDetail.vue'), meta: { title: '能力详情', primary: 'hall' } },
      { path: 'hall/data/:id', name: 'HallDataDetail', component: () => import('@/pages/hall/HallDataDetail.vue'), meta: { title: '数据能力详情', primary: 'hall' } },
      { path: 'hall/team/:department', name: 'HallTeamDetail', component: () => import('@/pages/hall/HallTeamDetail.vue'), meta: { title: '团队能力详情', primary: 'hall' } },
      { path: 'knowledge', name: 'KnowledgeHome', component: () => import('@/pages/knowledge/KnowledgeHome.vue'), meta: { title: '知识库', primary: 'workflow', subnav: 'knowledge' } },
      { path: 'skills', name: 'SkillList', component: () => import('@/pages/skill/SkillList.vue'), meta: { title: 'Skills', primary: 'workflow', subnav: 'skills' } },
      // 旧 Skill 大厅入口并入一级「能力大厅」，保留跳转避免书签失效。
      { path: 'skills/hall', redirect: (to) => ({ path: '/hall', query: { view: 'type', tab: to.query.tab || 'skill' } }) },
      { path: 'skills/list', redirect: '/skills' },
      { path: 'skills/templates', redirect: '/hall' },
      { path: 'skills/templates/:id', redirect: '/hall' },
      // v2.8.2 B1：/skills/new 改走独立的 SkillCreationWizard，SkillStudio 不再承担创建模式
      // 需旧行为的可加 ?legacy=1 query 切回老路径
      { path: 'skills/new', name: 'SkillCreationWizard', component: () => import('@/pages/skill/SkillCreationWizard.vue'), meta: { title: '新建 Skill', roles: ['admin', 'ai_engineer', 'aibp'], primary: 'workflow', subnav: 'skills' } },
      { path: 'skills/new-legacy', name: 'SkillStudioCreate', component: () => import('@/pages/skill/SkillStudio.vue'), meta: { title: '新建 Skill（旧）', roles: ['admin', 'ai_engineer', 'aibp'], primary: 'workflow', subnav: 'skills' } },
      { path: 'skills/:id', name: 'SkillStudio', component: () => import('@/pages/skill/SkillStudio.vue'), meta: { title: 'Skill Studio', roles: ['admin', 'ai_engineer', 'aibp', 'biz_owner'], primary: 'workflow', subnav: 'skills' } },
      // v2.10.0 V210-4：依赖图独立视图
      { path: 'skills/:id/deps', name: 'SkillDependencyView', component: () => import('@/pages/skill/SkillDependencyView.vue'), meta: { title: 'Skill 依赖图', roles: ['admin', 'ai_engineer', 'aibp', 'biz_owner'], primary: 'workflow', subnav: 'skills' } },
      { path: 'task-tree', name: 'TaskTree', component: () => import('@/pages/tasktree/TaskTree.vue'), meta: { title: '任务树', requiresAuth: true, primary: 'tasktree', subnav: 'tasktree' } },
      { path: 'sf', name: 'SfDashboard', component: () => import('@/pages/sf/SfDashboard.vue'), meta: { title: 'SF', requiresAuth: true, roles: ['admin', 'system_admin', 'ai_engineer', 'aibp'], primary: 'sf', subnav: 'sf' } },
      { path: 'sf/sdk', name: 'SfSdkCheck', component: () => import('@/pages/sf/SfSdkCheck.vue'), meta: { title: 'SDK', requiresAuth: true, roles: ['admin', 'system_admin', 'ai_engineer', 'aibp'], primary: 'sf', subnav: 'sdk' } },
      { path: 'learning-flow', name: 'LearningFlow', component: () => import('@/pages/learning/LearningFlow.vue'), meta: { title: '脉动', requiresAuth: true, roles: ['admin', 'system_admin', 'dept_admin', 'biz_owner', 'ai_engineer', 'aibp', 'observer', 'operator'], primary: 'workflow', subnav: 'learning' } },

      // 应用门户
      { path: 'portal', name: 'PortalHome', component: () => import('@/pages/portal/PortalHome.vue'), meta: { title: '应用门户', primary: 'projects', subnav: 'portal' } },
      { path: 'portal/market', name: 'PortalMarket', component: () => import('@/pages/portal/PortalMarket.vue'), meta: { title: '企业市场', primary: 'projects', subnav: 'portal' } },
      { path: 'portal/skills/:id', name: 'PortalSkillDetail', component: () => import('@/pages/portal/PortalSkillDetail.vue'), meta: { title: 'Skill 详情', primary: 'projects', subnav: 'portal' } },
      { path: 'portal/submissions/:id', name: 'PortalSubmissionDetail', component: () => import('@/pages/portal/PortalSubmissionDetail.vue'), meta: { title: '执行结果', primary: 'projects', subnav: 'portal' } },
      { path: 'portal/overview', name: 'PortalOverview', component: () => import('@/pages/portal/PortalOverview.vue'), meta: { title: '部门概览', primary: 'projects', subnav: 'portal' } },

      // D2 v2.2.0 W7c: 20 条旧 skill/skills 下的 redirect alias 全部删除
      // （原在 v2.1.x 做 Studio IA 重构时留的过渡路径，标了 meta.deprecated 一版后）。
      // 旧 URL 访问现在会落到全局 /:pathMatch(.*)* → /error/404 页面，由 404 页引导
      // 用户返回 /skills 列表。若下游还有外链，可通过 nginx 层面做 301 兜底。

      // 审核
      { path: 'reviews', name: 'ReviewList', component: () => import('@/pages/review/ReviewList.vue'), meta: { title: '审核中心', primary: 'workflow', subnav: 'skills' } },
      { path: 'review/:id', name: 'ReviewDetail', component: () => import('@/pages/review/ReviewDetailRedirect.vue'), meta: { title: '审核详情', primary: 'workflow', subnav: 'skills' } },

      // 执行
      { path: 'executions', name: 'ExecutionList', component: () => import('@/pages/execution/ExecutionList.vue'), meta: { title: '执行监控', primary: 'workflow', subnav: 'skills' } },
      { path: 'execution/:id', name: 'ExecutionDetail', component: () => import('@/pages/execution/ExecutionDetail.vue'), meta: { title: '执行详情', primary: 'workflow', subnav: 'skills' } },

      // 看板
      { path: 'dashboard', name: 'Dashboard', component: () => import('@/pages/Dashboard.vue'), meta: { title: '效果看板', primary: 'workflow', subnav: 'skills' } },
      { path: 'dashboard/contract-drift', name: 'ContractDrift', component: () => import('@/pages/ContractDrift.vue'), meta: { title: '契约偏离', roles: ['admin', 'ai_engineer'], primary: 'workflow', subnav: 'skills' } },

      // 数据源
      { path: 'datasources', name: 'DatasourceList', component: () => import('@/pages/datasource/DatasourceList.vue'), meta: { title: '数据源', primary: 'workflow', subnav: 'skills' } },
      { path: 'datasource/:id/upload', name: 'DatasourceUpload', component: () => import('@/pages/datasource/DatasourceUpload.vue'), meta: { title: '数据上传', primary: 'workflow', subnav: 'skills' } },
      // 老路径兼容：/datasources/browser → /admin/connections?tab=browser（保护书签与外链）
      { path: 'datasources/browser', redirect: '/admin/connections?tab=browser' },

      // 项目宿主（无容器、多部门小网页 + Project Gateway）
      { path: 'projects', name: 'ProjectHost', component: () => import('@/pages/project/ProjectHost.vue'), meta: { title: '项目宿主', primary: 'projects' } },
      { path: 'projects/:id', name: 'ProjectDetail', component: () => import('@/pages/project/ProjectDetail.vue'), meta: { title: '项目运行', primary: 'projects' } },

      // Playbook
      { path: 'playbooks', name: 'PlaybookList', component: () => import('@/pages/playbook/PlaybookList.vue'), meta: { title: '项目', primary: 'projects', subnav: 'playbooks' } },
      { path: 'playbook/:name', name: 'PlaybookView', component: () => import('@/pages/playbook/PlaybookWorkbench.vue'), meta: { title: '项目工作台', primary: 'projects', subnav: 'playbooks' } },
      { path: 'playbook/:name/edit', name: 'PlaybookEdit', redirect: (to) => redirectToPlaybookMode(to, 'edit'), meta: { title: '项目编辑', primary: 'projects', subnav: 'playbooks' } },
      { path: 'playbook/:name/live', name: 'PlaybookLive', redirect: (to) => redirectToPlaybookMode(to, 'run'), meta: { title: '项目实时执行', primary: 'projects', subnav: 'playbooks' } },

      // Agent（前身：智脑 / AIClaw 总览）
      { path: 'agent', name: 'AgentHome', component: () => import('@/pages/brain/BrainHome.vue'), meta: { title: 'Agent', primary: 'workflow', subnav: 'agent' } },
      { path: 'training', name: 'TrainingHome', component: () => import('@/pages/training/TrainingHome.vue'), meta: { title: '训练', primary: 'training', subnav: 'training-flow' } },
      { path: 'training/models', name: 'TrainingModels', component: () => import('@/pages/training/TrainingModels.vue'), meta: { title: '模型部署', primary: 'training', subnav: 'training-models' } },
      { path: 'training/datasets', name: 'TrainingDatasets', component: () => import('@/pages/training/TrainingDatasets.vue'), meta: { title: '数据资产', primary: 'training', subnav: 'training-datasets' } },
      { path: 'training/deployments/:id', name: 'TrainingDeploymentDetail', component: () => import('@/pages/training/TrainingDeploymentDetail.vue'), meta: { title: '模型部署详情', primary: 'training', subnav: 'training-models' } },
      { path: 'training/jobs/:id', name: 'TrainingJobDetail', component: () => import('@/pages/training/TrainingJobDetail.vue'), meta: { title: '训练任务详情', primary: 'training', subnav: 'training-flow' } },
      // 旧路由 → Agent
      { path: 'brain', redirect: '/agent' },
      { path: 'aiclaw', redirect: '/agent' },
      // AIClaw 实例工作台（仍然保留，从「Agent终端」的对话按钮进入）
      { path: 'aiclaw/instances/:id', name: 'AIClawWorkspace', component: () => import('@/pages/aiclaw/AIClawWorkspace.vue'), meta: { title: 'OpenClaw 会话', primary: 'workflow', subnav: 'agent' } },

      // v2 收件中心（inbox = 待办 + 报告双 tab，plan §6.2）
      { path: 'inbox', name: 'InboxCenter', component: () => import('@/pages/inbox/InboxCenter.vue'), meta: { title: '收件', primary: 'inbox', subnav: 'pending' } },
      { path: 'inbox/dispatch', name: 'MyDispatchTasks', component: () => import('@/pages/inbox/MyDispatchTasks.vue'), meta: { title: '我收到的派发任务', primary: 'inbox', subnav: 'dispatch' } },
      { path: 'inbox/todos/:id/v2', name: 'TodoDetailV2', component: () => import('@/pages/inbox/TodoDetailV2.vue'), meta: { title: '待办详情新版', primary: 'inbox', subnav: 'pending' } },
      { path: 'inbox/todos/:id', name: 'TodoDetail', component: () => import('@/pages/inbox/TodoDetail.vue'), meta: { title: '待办详情', primary: 'inbox', subnav: 'pending' } },
      { path: 'inbox/reports/:id', name: 'ReportDetail', component: () => import('@/pages/inbox/ReportDetail.vue'), meta: { title: '报告详情', primary: 'inbox', subnav: 'reports' } },
      // 向后兼容：旧 /todos* 路径自动跳 /inbox*
      { path: 'todos', redirect: (to) => ({ path: '/inbox', query: to.query }) },
      { path: 'todos/dispatch', redirect: '/inbox/dispatch' },
      { path: 'todos/dispatch/:taskId', redirect: (to) => ({ path: '/inbox/dispatch', query: { focus: String(to.params.taskId) } }) },
      { path: 'todos/:id/v2', redirect: (to) => ({ path: `/inbox/todos/${to.params.id}/v2`, query: to.query }) },
      { path: 'todos/:id', redirect: (to) => ({ path: `/inbox/todos/${to.params.id}`, query: to.query }) },

      { path: 'changelog', name: 'Changelog', component: () => import('@/pages/Changelog.vue'), meta: { title: '更新日志' } },
      { path: 'me', name: 'Me', component: () => import('@/pages/Me.vue'), meta: { title: '个人设置' } },
      // v2.11.2: /connections 收进管理后台，旧路径保留跳转以免书签失效
      { path: 'connections', redirect: (to) => ({ path: '/admin/connections', query: to.query }) },

      // ═══ 管理后台：共享 AdminShell 左栏壳 ═══
      {
        path: 'admin',
        component: () => import('@/layouts/AdminShell.vue'),
        meta: { roles: ['admin', 'system_admin'], primary: 'admin' },
        children: [
          // 裸路径 /admin 自动跳 /admin/users
          { path: '', redirect: '/admin/users' },
          { path: 'users', name: 'AdminUsers', component: () => import('@/pages/admin/AdminUsers.vue'), meta: { title: '用户', roles: ['admin', 'system_admin'], primary: 'admin' } },
          // v2.1.1: 物理合并——/admin/users/pending 现在由 AdminUsers.vue 按 route.path 判断渲染 pending 视图
          { path: 'users/pending', name: 'PendingUsers', component: () => import('@/pages/admin/AdminUsers.vue'), meta: { title: '待激活用户', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'users/:userId', name: 'AdminUserDetail', component: () => import('@/pages/admin/AdminUsers.vue'), meta: { title: '用户详情', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'audit', name: 'AdminAudit', component: () => import('@/pages/admin/AdminAudit.vue'), meta: { title: '审计日志', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'codex-plugin', name: 'AdminCodexPlugin', component: () => import('@/pages/admin/AdminCodexPlugin.vue'), meta: { title: 'Codex 插件', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'agent-devices', name: 'AdminAgentDevices', component: () => import('@/pages/admin/AdminAgentDevices.vue'), meta: { title: 'Agent终端', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'agent-devices/:id', name: 'AdminAgentDeviceDetail', component: () => import('@/pages/admin/AdminAgentDeviceDetail.vue'), meta: { title: 'Agent终端详情', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'ai-config', name: 'AdminAiConfig', component: () => import('@/pages/admin/AiConfig.vue'), meta: { title: 'AI 配置', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'settings', name: 'AdminSettings', component: () => import('@/pages/admin/SystemSettings.vue'), meta: { title: '系统设置', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'compliance', name: 'AdminCompliance', component: () => import('@/pages/admin/AdminCompliance.vue'), meta: { title: '合规规则', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'prompts', name: 'AdminPrompts', component: () => import('@/pages/admin/AdminPrompts.vue'), meta: { title: 'Prompt 库', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'costs', name: 'AdminCosts', component: () => import('@/pages/admin/CostReport.vue'), meta: { title: 'AI 成本', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'runs/trace', name: 'AdminRunTraceSearch', component: () => import('@/pages/admin/AdminRunTrace.vue'), meta: { title: 'Run Trace', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'runs/:runId/trace', name: 'AdminRunTrace', component: () => import('@/pages/admin/AdminRunTrace.vue'), meta: { title: 'Run Trace', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'org', name: 'AdminOrg', component: () => import('@/pages/admin/AdminOrg.vue'), meta: { title: '组织架构', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'governance', name: 'AdminGovernance', component: () => import('@/pages/admin/AdminGovernance.vue'), meta: { title: '治理中心', roles: ['admin', 'system_admin'], primary: 'admin' } },
          // W2-A v2.3.2: 数据卫生面板（扫出异常部门等脏数据让管理员补录）
          { path: 'data-hygiene', name: 'AdminDataHygiene', component: () => import('@/pages/admin/AdminDataHygiene.vue'), meta: { title: '数据卫生', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'data-requests', name: 'AdminDataRequests', component: () => import('@/pages/admin/AdminDataRequests.vue'), meta: { title: '数据访问审批', roles: ['admin', 'system_admin'], primary: 'admin' } },
          // v2.11.2 服务 / 数据独立：MCP Servers、平台 API 注册表、连接管理 从 ai-config / 顶层挪进 admin
          { path: 'connections', name: 'AdminConnections', component: () => import('@/pages/connections/Connections.vue'), meta: { title: '连接管理', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'connections/health', name: 'CollectionHealth', component: () => import('@/pages/connections/CollectionHealth.vue'), meta: { title: '采集健康', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'connections/:platform/:shop_id', name: 'CookiePoolDetailPage', component: () => import('@/pages/connections/CookiePoolPage.vue'), meta: { title: 'Cookie 池', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'connector-keys', name: 'AdminConnectorKeys', component: () => import('@/pages/admin/AdminConnectorKeys.vue'), meta: { title: 'Connector Keys', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'mcp-servers', name: 'AdminMcpServers', component: () => import('@/pages/admin/AdminMcpServers.vue'), meta: { title: 'MCP Servers', roles: ['admin', 'system_admin'], primary: 'admin' } },
          { path: 'platform-apis', name: 'AdminPlatformApis', component: () => import('@/pages/admin/AdminPlatformApis.vue'), meta: { title: '平台 API 注册表', roles: ['admin', 'system_admin'], primary: 'admin' } },
        ],
      },

      // 兼容老 URL（保护书签/外链）：openclaw → agent-devices
      { path: 'admin/openclaw', redirect: '/admin/agent-devices' },
      { path: 'admin/openclaw/:id', redirect: (to: any) => `/admin/agent-devices/${to.params.id}` },
    ],
  },
  // 错误页面
  {
    path: '/error/:code',
    name: 'Error',
    component: () => import('@/pages/ErrorPage.vue'),
    meta: { public: true },
  },
  // 404 catch-all
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/pages/ErrorPage.vue'),
    meta: { public: true },
    props: () => ({ code: '404' }),
  },
]

const router = createRouter({
  history: createWebHistory('/'),
  routes,
})

const CHUNK_RELOAD_KEY = 'skillforge:chunk-load-reload-at'

function isDynamicImportError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error || '')
  return /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Loading chunk .* failed|Unable to preload CSS/i.test(message)
}

router.onError((error) => {
  if (!isDynamicImportError(error)) return

  const now = Date.now()
  const last = Number(window.sessionStorage.getItem(CHUNK_RELOAD_KEY) || 0)
  if (now - last < 10_000) {
    // eslint-disable-next-line no-console
    console.error('[router] dynamic import still failing after reload', error)
    return
  }

  window.sessionStorage.setItem(CHUNK_RELOAD_KEY, String(now))
  window.location.reload()
})

router.afterEach(() => {
  window.sessionStorage.removeItem(CHUNK_RELOAD_KEY)
})

// v2 角色兼容：老 roles meta（admin/ai_engineer/biz_owner/director）→ v2 新角色同时放行
// （role-matrix-v2 §14 老角色迁移映射）
const ROLE_COMPAT: Record<string, string[]> = {
  admin: ['admin', 'system_admin'],
  system_admin: ['admin', 'system_admin'],
  ai_engineer: ['ai_engineer', 'aibp'],
  aibp: ['ai_engineer', 'aibp'],
  biz_owner: ['biz_owner', 'dept_admin'],
  director: ['director', 'dept_admin', 'observer'],
  dept_admin: ['biz_owner', 'director', 'dept_admin'],
  observer: ['director', 'operator', 'observer'],
  operator: ['operator', 'observer'],
}

function hasRequiredRole(userRole: string, requiredRoles: string[]): boolean {
  if (!userRole) return false
  if (requiredRoles.includes(userRole)) return true
  // 检查角色兼容映射：任何 required role 的兼容集合里包含 userRole 即放行
  for (const req of requiredRoles) {
    const compat = ROLE_COMPAT[req] || [req]
    if (compat.includes(userRole)) return true
  }
  return false
}

function routeRoleRequirements(to: RouteLocationNormalized): string[][] {
  return to.matched
    .map((record) => record.meta.roles)
    .filter((roles): roles is string[] => Array.isArray(roles) && roles.length > 0)
}

function isProjectShareRoute(to: RouteLocationNormalized): boolean {
  const marker = Array.isArray(to.query.share) ? to.query.share[0] : to.query.share
  if (marker !== '1' && marker !== 'true') return false
  return to.path.startsWith('/project-run/') || to.path.startsWith('/playbook/') || /^\/projects\/[^/]+/.test(to.path)
}

// 路由守卫：认证 + pending 拦截 + 强制修改密码 + 角色检查
router.beforeEach(async (to, from) => {
  // D2: 记录 deprecated 路由命中（通过 redirectedFrom 反查，因为 redirect 本身在 resolve 阶段就完成）
  const fromRoute = (to as any).redirectedFrom
  if (fromRoute?.meta?.deprecated || to.meta?.deprecated) {
    // eslint-disable-next-line no-console
    console.warn(`[router] Deprecated route hit: ${fromRoute?.fullPath || to.fullPath} → will be removed in next major version`)
  }
  if (to.meta.public) return true

  const userStore = useUserStore()
  if (!userStore.isLoggedIn) {
    await userStore.fetchUser()
  }
  // T18: 权限动态同步。跨页跳转不能阻塞导航；用后台节流同步刷新 role / permissions_rev。
  else if (from && from.path !== to.path) {
    void userStore.syncPermissions({ maxAgeMs: 60_000 })
  }
  if (!userStore.isLoggedIn) {
    return {
      path: '/login',
      query: {
        redirect: to.fullPath,
        ...(isProjectShareRoute(to) ? { scan: '1', reason: 'project_share' } : {}),
      },
    }
  }

  // 强制修改密码：首次登录用户必须先改密码
  if (userStore.userInfo?.must_change_password && to.name !== 'ChangePassword') {
    return { path: '/change-password' }
  }

  // v2: pending 用户除 /pending 本身外一律跳 /pending（role-matrix-v2 §3.6）
  if (userStore.isPending && to.path !== '/pending') {
    return { path: '/pending' }
  }
  // 非 pending 用户不应看到 /pending 页面
  if (!userStore.isPending && to.path === '/pending') {
    return { path: '/' }
  }

  // 角色检查（v2 角色兼容）。必须逐层检查 matched records，避免子路由 meta 覆盖父级 /admin 壳权限。
  for (const requiredRoles of routeRoleRequirements(to)) {
    if (!hasRequiredRole(userStore.role, requiredRoles)) {
      return { path: '/error/403' }
    }
  }

  return true
})

export default router
