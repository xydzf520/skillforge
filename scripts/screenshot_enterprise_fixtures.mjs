/** Synthetic, read-only enterprise examples for README screenshots. No production data. */
export function enterpriseFixtures(state) {
  const timestamp = '2026-09-20T09:00:00';
  const users = [
    { id: 'demo-admin', username: 'demo_admin', name: '示例管理员', role: 'system_admin', department: '示例企业', permissions: ['user.manage_all', 'org.manage_all', 'skill.review_all', 'audit.read_all', 'system.configure'] },
    { id: 'demo-cs-lead', username: 'demo_cs_lead', name: '客服负责人', role: 'dept_admin', department: '客服部', permissions: ['user.activate_in_dept', 'user.view_dept', 'skill.review_in_dept', 'approval.handle_in_dept'] },
    { id: 'demo-ai', username: 'demo_ai', name: 'AI 协作成员', role: 'aibp', department: '产品部', permissions: ['skill.edit_authorized', 'skill.submit_review', 'skill.execute_authorized', 'datasource.upload'] },
    { id: 'demo-observer', username: 'demo_observer', name: '业务观察员', role: 'observer', department: '运营部', permissions: ['skill.view_authorized', 'execution.view_history', 'report.view'] },
  ].map(user => ({ ...user, state: 'active', is_active: true, can_view_all: false, created_at: timestamp, last_activity_at: timestamp, avatar_url: '' }));
  const member = (id, primary, manager = false) => {
    const user = users.find(item => item.id === id);
    return { user_id: id, username: user.username, name: user.name, membership_type: primary ? 'primary' : 'secondary', is_manager: manager, joined_at: timestamp };
  };
  state.membersByOrg = {
    'demo-company': [member('demo-admin', true, true)],
    'demo-product': [member('demo-ai', true)],
    'demo-customer': [member('demo-cs-lead', true, true), member('demo-ai', false), member('demo-observer', false)],
    'demo-operations': [member('demo-observer', true)],
    'demo-ai-group': [member('demo-cs-lead', false, true), member('demo-ai', false)],
  };
  state.orgTree = [{ id: 'demo-company', name: '示例企业', type: 'department', parent_id: null, member_count: 1, total_member_count: 8,
    children: [['demo-product', '产品部', 'department'], ['demo-customer', '客服部', 'department'], ['demo-operations', '运营部', 'department'], ['demo-ai-group', 'AI 项目协作组', 'project']]
      .map(([id, name, type]) => ({ id, name, type, parent_id: 'demo-company', member_count: state.membersByOrg[id].length, children: [] })) }];
  const projects = [
    { id: 'demo-customer-quality', name: '客服质检工作台', type: 'internal_tool', department_id: 'demo-customer', department: '客服部', description: '合成示例：按业务标准检查对话，将待复核的问题交给负责人处理。', visibility: 'department' },
    { id: 'demo-operations-report', name: '经营分析看板', type: 'dashboard', department_id: 'demo-operations', department: '运营部', description: '合成示例：集中呈现经营摘要、异常线索和待办，支持团队复核。', visibility: 'department' },
    { id: 'demo-knowledge', name: '知识整理助手', type: 'web_static', department_id: 'demo-product', department: '产品部', description: '合成示例：整理经授权的资料与反馈，形成可复用的知识条目。', visibility: 'company' },
  ].map(project => ({ ...project, owner_user_id: 'demo-ai', status: 'active', entry_url: '', current_version_id: 'demo-v1', metadata: {}, runtime: { containerized: false }, latest_run: null, permissions: { can_view: true }, created_at: timestamp, updated_at: timestamp }));
  const events = [
    ['review.approve', 'demo-cs-lead', 'skill', 'demo-quality', { reason: '示例：人工复核通过，保留版本依据' }],
    ['auth.permission_denied', 'demo-observer', 'skill', 'demo-quality', { reason: '示例：不在授权编辑范围内' }],
    ['skill.edit', 'demo-ai', 'skill', 'demo-quality', { reason: '示例：补充质检标准与输出约定' }],
    ['org.membership.add', 'demo-admin', 'org', 'demo-customer', { reason: '示例：加入跨部门协作成员' }],
    ['user.login', 'demo-cs-lead', 'user', 'demo-cs-lead', { username: 'demo_cs_lead' }],
  ].map(([action, user_id, target_type, target_id, detail], i) => ({ id: `demo-audit-${i + 1}`, created_at: `2026-09-20T09:${String(30 - i * 5).padStart(2, '0')}:00`, action, user_id, target_type, target_id, detail, ip_address: null }));
  const responses = {
    '/api/projects/': { items: projects, stats: { total: 3, running: 0, stale: 0, waiting_ai: 0, report_count: 0, todo_count: 0, by_type: { internal_tool: 1, dashboard: 1, web_static: 1 }, by_department: { 客服部: 1, 运营部: 1, 产品部: 1 } }, pagination: { page: 1, page_size: 20, total: 3, has_more: false }, runtime_policy: { capability_gateway: 'Project Gateway' } },
    '/api/projects/runtime/status': {},
    '/api/users/': { items: users, total: users.length },
    '/api/users/pending': { items: [], total: 0 },
    '/api/audit/': url => {
      const userId = url.searchParams.get('user_id');
      const items = userId ? events.filter(event => event.user_id === userId) : events;
      return { items, total: items.length };
    },
    '/api/audit/stats': { total: events.length, security: { login_failures: 0, permission_denials: 1 } },
    '/api/audit/actions': { items: [['review.approve', '审核通过'], ['auth.permission_denied', '权限拒绝'], ['skill.edit', '编辑技能'], ['org.membership.add', '加入组织'], ['user.login', '用户登录']].map(([action, label]) => ({ action, label })) },
    '/api/audit/detail-fields': { fields: [{ key: 'reason', label: '原因' }, { key: 'username', label: '用户名' }] },
  };
  for (const user of users) responses[`/api/users/${user.id}/detail`] = user;
  return responses;
}
