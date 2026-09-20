// 管理后台 — 4 sub-pages with shared admin sidebar.

const ADMIN_NAV = [
  { label: '账号与组织', items: [
    { id: 'users',   name: '用户',         icon: 'user' },
    { id: 'orgs',    name: '组织架构',     icon: 'tree' },
  ] },
  { label: '治理与合规', items: [
    { id: 'audit',   name: '审计日志',     icon: 'doc' },
    { id: 'rules',   name: '合规规则',     icon: 'shield' },
    { id: 'config',  name: '系统配置',     icon: 'cube' },
  ] },
  { label: '数据与服务', items: [
    { id: 'conn',    name: '连接管理',     icon: 'link' },
    { id: 'keys',    name: 'Connector Keys', icon: 'shield' },
    { id: 'health',  name: '采集健康',     icon: 'trend' },
    { id: 'mcp',     name: 'MCP Servers',  icon: 'database' },
    { id: 'codex',   name: 'Codex 插件',   icon: 'doc' },
    { id: 'aterm',   name: 'Agent 终端',   icon: 'bot' },
    { id: 'api',     name: '平台 API 注册表', icon: 'list' },
    { id: 'hygiene', name: '数据卫生',     icon: 'check' },
    { id: 'cost',    name: 'AI 成本',     icon: 'gpu' },
    { id: 'trace',   name: 'Run Trace',   icon: 'flow' },
  ] },
];

function AdminSidebar({ active }) {
  return (
    <div className="ai-sidebar" style={{ width: 220 }}>
      <div className="row" style={{ padding: '4px 8px 14px', gap: 6 }}>
        <span style={{ fontSize: 13, fontWeight: 600, letterSpacing: '-0.005em' }}>管理后台</span>
      </div>
      <div className="ai-input placeholder" style={{ width: '100%', marginBottom: 12 }}>
        <I name="search" size={12} />
        <span>搜索菜单…</span>
      </div>
      {ADMIN_NAV.map(g => (
        <div key={g.label} className="ai-side-group">
          <div className="ai-side-label">{g.label}</div>
          {g.items.map(it => (
            <div key={it.id} className={'ai-side-item' + (active === it.id ? ' active' : '')}>
              <I name={it.icon} size={13} className="ic" />
              <span>{it.name}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

// 1. 用户管理
function AdminUsers() {
  const rows = [
    { name: 'MCP AI User',     id: 'mcp-ai-user',  role: '系统管理员', dept: '—',     state: '启用', last: '1 天前',  codex: '—' },
    { name: 'Codex Smoke Admin', id: 'codex-smoke-admin-995989', role: '管理员', dept: 'EC', state: '启用', last: '—', codex: '—' },
    { name: '观察员测试',       id: 'observer_test', role: '观察员', dept: 'EC', state: '启用', last: '—', codex: '—' },
    { name: 'AIBP 测试',        id: 'aibp_test',   role: 'AIBP',    dept: 'EC',     state: '启用', last: '—',     codex: '—' },
    { name: 'e2e_admin',       id: 'e2e_admin',   role: '系统管理员', dept: 'ADMIN', state: '启用', last: '04-15', codex: '—' },
    { name: 'BD 部门管理员',    id: 'bd_tester',   role: '部门管理员', dept: 'BD',    state: '启用', last: '04-19', codex: '—' },
    { name: '张文',             id: 'zhang.w',     role: '员工',      dept: 'EC',    state: '启用', last: '8m 前', codex: 'v0.2 · 在用' },
    { name: '李建',             id: 'li.j',        role: '员工',      dept: 'CS',    state: '启用', last: '12m 前', codex: '—' },
    { name: '陈静',             id: 'chen.j',      role: '员工',      dept: '仓储',  state: '启用', last: '3h 前',  codex: '—' },
  ];

  return (
    <Screen role="admin" active="admin">
      <AdminSidebar active="users" />
      <div className="ai-main">
        <div className="ai-pagehead">
          <div>
            <div className="ai-crumbs">管理后台 · 账号与组织</div>
            <h1 className="ai-title">用户管理</h1>
            <p className="ai-sub">人员、角色、部门归属 · 钉钉同步 · Codex 插件签名</p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="ai-btn"><I name="refresh" size={12} /> 同步钉钉</button>
            <button className="ai-btn primary"><I name="plus" size={12} /> 新建用户</button>
          </div>
        </div>

        <div className="ai-tabs">
          <div className="ai-tab active">激活 <span className="ai-pill ok" style={{ marginLeft: 4, fontSize: 10, height: 16, padding: '0 5px' }}>32</span></div>
          <div className="ai-tab">待激活 <span className="ai-pill" style={{ marginLeft: 4, fontSize: 10, height: 16, padding: '0 5px' }}>3</span></div>
          <div className="ai-tab">停用 <span className="ai-pill" style={{ marginLeft: 4, fontSize: 10, height: 16, padding: '0 5px' }}>5</span></div>
        </div>

        <div className="ai-pagebody" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="row" style={{ gap: 8 }}>
            <div className="ai-input placeholder" style={{ width: 280 }}><I name="search" size={12} /><span>搜索用户 ID / 姓名</span></div>
            <div className="ai-input placeholder"><span>角色</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>部门</span><I name="chev" size={11} /></div>
            <div style={{ flex: 1 }} />
            <span className="ai-pill" style={{ background: 'var(--ink-1)', color: 'white', borderColor: 'var(--ink-1)' }}>全部 32</span>
            <span className="ai-pill">系统管理员 2</span>
            <span className="ai-pill">部门管理员 6</span>
            <span className="ai-pill">员工 24</span>
          </div>

          <div className="ai-card" style={{ padding: 0 }}>
            <table className="ai-table">
              <thead>
                <tr>
                  <th>用户</th>
                  <th style={{ width: 110 }}>角色</th>
                  <th style={{ width: 80 }}>部门</th>
                  <th style={{ width: 70 }}>状态</th>
                  <th style={{ width: 100 }}>最近活跃</th>
                  <th style={{ width: 130 }}>Codex 插件</th>
                  <th style={{ width: 220 }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.id}>
                    <td>
                      <div className="row" style={{ gap: 10 }}>
                        <div style={{ width: 28, height: 28, borderRadius: '50%', background: 'linear-gradient(135deg, #d8d2c0, #a6a18f)', display: 'grid', placeItems: 'center', fontSize: 11, fontWeight: 600 }}>{r.name[0]}</div>
                        <div className="col" style={{ gap: 2 }}>
                          <span style={{ fontSize: 13, fontWeight: 500 }}>{r.name}</span>
                          <span className="mono-id">{r.id}</span>
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className={'ai-pill ' + (r.role.includes('系统') ? 'bad' : r.role.includes('管理员') ? 'warn' : r.role === 'AIBP' ? 'accent' : '')} style={{ fontSize: 10.5 }}>{r.role}</span>
                    </td>
                    <td className="tiny" style={{ color: 'var(--ink-2)' }}>{r.dept}</td>
                    <td><span className="ai-pill ok dot">{r.state}</span></td>
                    <td className="tiny muted">{r.last}</td>
                    <td className="tiny" style={{ color: r.codex !== '—' ? 'var(--ink-1)' : 'var(--ink-4)' }}>{r.codex}</td>
                    <td className="row" style={{ gap: 4, justifyContent: 'flex-end' }}>
                      <button className="ai-btn sm">编辑</button>
                      <button className="ai-btn sm">重置密码</button>
                      <button className="ai-btn sm" style={{ color: 'var(--bad)' }}>停用</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Screen>
  );
}

// 2. Codex 插件
function AdminCodex() {
  return (
    <Screen role="admin" active="admin">
      <AdminSidebar active="codex" />
      <div className="ai-main">
        <div className="ai-pagehead">
          <div>
            <div className="ai-crumbs">管理后台 · 数据与服务</div>
            <h1 className="ai-title">Codex 插件</h1>
            <p className="ai-sub">统计 Codex 插件登录、CLI 使用和 MCP 能力调用</p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <div className="ai-input placeholder"><span>近 30 天</span><I name="chev" size={11} /></div>
            <button className="ai-btn"><I name="refresh" size={12} /> 刷新</button>
          </div>
        </div>

        {/* KPI strip */}
        <div className="row" style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
          {[
            { l: '插件用户', v: '0', c: 'var(--ink-4)' },
            { l: '活跃会话', v: '0', c: 'var(--ok)' },
            { l: 'CLI/API 调用', v: '3', c: 'var(--accent-ink)' },
            { l: 'MCP 调用', v: '0', c: 'var(--warn)' },
            { l: '失败 MCP', v: '0', c: 'var(--bad)' },
          ].map((s, i, arr) => (
            <div key={s.l} style={{ flex: 1, padding: '14px 20px', borderRight: i === arr.length - 1 ? '0' : '1px solid var(--border)' }}>
              <div className="tiny muted">{s.l}</div>
              <div className="mono" style={{ fontSize: 22, fontWeight: 600, color: s.c, letterSpacing: '-0.02em' }}>{s.v}</div>
            </div>
          ))}
        </div>

        <div className="ai-pagebody" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="ai-card" style={{ padding: 0 }}>
            <div className="ai-card-h">
              <span className="t">插件用户</span>
              <span className="s">钉钉登录后绑定 Codex CLI 的用户</span>
            </div>
            <table className="ai-table">
              <thead><tr><th>用户</th><th style={{ width: 100 }}>会话</th><th style={{ width: 110 }}>MCP 调用</th><th style={{ width: 110 }}>最近活跃</th></tr></thead>
              <tbody>
                <tr><td colSpan={4} style={{ padding: '60px 0', textAlign: 'center' }}>
                  <I name="inbox" size={24} stroke="var(--ink-4)" />
                  <div style={{ fontSize: 13, color: 'var(--ink-3)', marginTop: 8 }}>暂无数据</div>
                </td></tr>
              </tbody>
            </table>
          </div>

          <div className="ai-card" style={{ padding: 0 }}>
            <div className="ai-card-h">
              <span className="t">调用记录</span>
              <span className="s">最近 30 天 · 3 条</span>
              <div className="row" style={{ marginLeft: 'auto', gap: 6 }}>
                <div className="ai-input placeholder" style={{ height: 24 }}><span>类型</span><I name="chev" size={10} /></div>
                <div className="ai-input placeholder" style={{ height: 24 }}><span>用户 ID</span><I name="search" size={10} /></div>
              </div>
            </div>
            <table className="ai-table">
              <thead>
                <tr>
                  <th style={{ width: 150 }}>时间</th>
                  <th style={{ width: 160 }}>用户</th>
                  <th style={{ width: 70 }}>类型</th>
                  <th>动作 / 能力</th>
                  <th style={{ width: 90 }}>结果</th>
                  <th style={{ width: 60 }}>模式</th>
                </tr>
              </thead>
              <tbody>
                {[
                  { t: '2026-05-22 13:39', u: '管理员', m: 'admin · admin · EC', k: 'CLI', a: 'codex.mcp.catalog', r: '已记录' },
                  { t: '2026-05-22 13:32', u: '管理员', m: 'admin · admin · EC', k: 'CLI', a: 'codex.mcp.catalog', r: '已记录' },
                  { t: '2026-05-22 13:30', u: '管理员', m: 'admin · admin · EC', k: 'CLI', a: 'codex.mcp.catalog', r: '已记录' },
                ].map((c, i) => (
                  <tr key={i}>
                    <td className="mono" style={{ fontSize: 12 }}>{c.t}</td>
                    <td><div className="col" style={{ gap: 1 }}><span style={{ fontWeight: 500 }}>{c.u}</span><span className="mono-id">{c.m}</span></div></td>
                    <td><span className="ai-pill accent" style={{ fontSize: 10.5 }}>{c.k}</span></td>
                    <td className="mono">{c.a}</td>
                    <td><span className="ai-pill ok dot">{c.r}</span></td>
                    <td className="tiny muted">—</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Screen>
  );
}

// 3. 系统配置
function AdminConfig() {
  const tabs = ['安全设置', 'Connector Keys', 'Collection', 'SYCM', 'Intelligence', 'AI', 'Monitoring'];
  const flags = [
    { k: 'security.bypass_review_direct_publish', name: '免审核直接发布', cur: 'false', def: 'false', hint: '关闭=提交后进入审核；开启=提交后自动发布', updated: '—' },
    { k: 'security.platform_node_fallback_enabled', name: '平台节点兜底运行', cur: 'true',  def: 'true',  hint: '部门无运行节点时使用平台默认节点', updated: '—' },
    { k: 'security.skill_run_max_concurrency',     name: 'Skill 运行最大并发', cur: '8',     def: '4',     hint: '同一 Skill 允许的并发运行数', updated: '2026-05-20 14:11' },
    { k: 'security.export_audit_required',         name: '数据导出强制审计', cur: 'true', def: 'true', hint: '导出任何 Skill 产物都需要审计签字', updated: '2026-04-30 09:42' },
  ];
  return (
    <Screen role="admin" active="admin" height={900}>
      <AdminSidebar active="config" />
      <div className="ai-main">
        <div className="ai-pagehead">
          <div>
            <div className="ai-crumbs">管理后台 · 治理与合规</div>
            <h1 className="ai-title">系统配置</h1>
            <p className="ai-sub">采集、AI 与监控治理参数 · 修改即生效，敏感项需 2 位管理员复签</p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="ai-btn"><I name="hist" size={12} /> 变更历史</button>
            <button className="ai-btn"><I name="download" size={12} /> 导出</button>
          </div>
        </div>

        <div className="ai-tabs">
          {tabs.map((t, i) => <div key={t} className={'ai-tab' + (i === 0 ? ' active' : '')}>{t}</div>)}
        </div>

        <div className="ai-pagebody" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {flags.map(f => (
            <div key={f.k} className="ai-card" style={{ padding: '16px 20px' }}>
              <div className="row" style={{ alignItems: 'flex-start', gap: 16 }}>
                <div style={{ flex: 1 }}>
                  <div className="row" style={{ gap: 8 }}>
                    <span style={{ fontSize: 14, fontWeight: 600 }}>{f.name}</span>
                    <span className="mono-id">{f.k}</span>
                  </div>
                  <div className="row" style={{ gap: 14, marginTop: 8, fontSize: 12 }}>
                    <span><span className="muted">当前</span> <span className="mono" style={{ fontWeight: 600, color: f.cur === f.def ? 'var(--ink-1)' : 'var(--warn)' }}>{f.cur}</span></span>
                    <span><span className="muted">默认</span> <span className="mono" style={{ color: 'var(--ink-3)' }}>{f.def}</span></span>
                    <span className="muted">·</span>
                    <span className="tiny muted">{f.hint}</span>
                    <span style={{ marginLeft: 'auto' }} className="tiny muted">更新 {f.updated}</span>
                  </div>
                </div>
                <button className="ai-btn sm">编辑</button>
                <button className="ai-btn sm">还原默认</button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </Screen>
  );
}

// 4. Agent 终端
function AdminAgentTerminals() {
  const terminals = [
    { name: 'AI',              exec: '1/1', anal: '缺', train: '缺' },
    { name: 'EC',              exec: '缺',  anal: '缺', train: '缺' },
    { name: 'EC-Runtime-1e52', exec: '兜底 1', anal: '缺', train: '缺' },
    { name: 'EC-Runtime-8418', exec: '兜底 1', anal: '缺', train: '缺' },
    { name: 'EC-Runtime-9d2d', exec: '兜底 1', anal: '缺', train: '缺' },
    { name: 'EC-Runtime-a8ee', exec: '兜底 1', anal: '缺', train: '缺' },
    { name: 'EC-Runtime-d784', exec: '兜底 1', anal: '缺', train: '缺' },
    { name: 'PMO',             exec: '兜底 1', anal: '缺', train: '缺' },
    { name: '仓储物流部',       exec: '兜底 1', anal: '缺', train: '缺' },
    { name: '供应链部',         exec: '兜底 1', anal: '缺', train: '缺' },
    { name: '信息技术部',       exec: '兜底 1', anal: '缺', train: '缺' },
    { name: '办公室',           exec: '兜底 1', anal: '缺', train: '缺' },
    { name: '即时零售业务部',   exec: '兜底 1', anal: '缺', train: '缺' },
    { name: '客服部',           exec: '兜底 1', anal: '缺', train: '缺' },
    { name: '市场部',           exec: '兜底 1', anal: '缺', train: '缺' },
    { name: '法务部',           exec: '兜底 1', anal: '缺', train: '缺' },
    { name: '研发部',           exec: '兜底 1', anal: '缺', train: '缺' },
    { name: '组织发展部',       exec: '兜底 1', anal: '缺', train: '缺' },
    { name: '设计部',           exec: '兜底 1', anal: '缺', train: '缺' },
    { name: '财务部',           exec: '兜底 1', anal: '缺', train: '缺' },
  ];

  const renderState = v => {
    if (v.includes('1/1')) return <span className="ai-pill ok dot" style={{ fontSize: 10.5 }}>执行 {v}</span>;
    if (v.includes('兜底')) return <span className="ai-pill" style={{ fontSize: 10.5 }}>执行 {v}</span>;
    return <span className="ai-pill bad" style={{ fontSize: 10.5 }}>执行 缺 +</span>;
  };

  return (
    <Screen role="admin" active="admin" height={1000}>
      <AdminSidebar active="aterm" />
      <div className="ai-main">
        <div className="ai-pagehead">
          <div>
            <div className="ai-crumbs">管理后台 · 数据与服务</div>
            <h1 className="ai-title">Agent 终端</h1>
            <p className="ai-sub">管理代理实例与部署到内网机器的 Python Bridge 脚本</p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="ai-btn"><I name="download" size={12} /> 下载 Bridge</button>
            <button className="ai-btn primary"><I name="plus" size={12} /> 新建实例</button>
          </div>
        </div>

        <div className="row" style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
          {[
            { l: '部门总数', v: '17' },
            { l: '已配执行', v: '16', s: '1 缺', c: 'var(--ok)' },
            { l: '已配分析', v: '0',  s: '17 缺', c: 'var(--bad)' },
            { l: '已配训练', v: '0',  s: '17 缺', c: 'var(--bad)' },
            { l: 'Bridge 在线', v: '14', s: '3 离线', c: 'var(--warn)' },
          ].map((s, i, arr) => (
            <div key={s.l} style={{ flex: 1, padding: '14px 20px', borderRight: i === arr.length - 1 ? '0' : '1px solid var(--border)' }}>
              <div className="tiny muted">{s.l}</div>
              <div className="mono" style={{ fontSize: 22, fontWeight: 600, color: s.c || 'var(--ink-1)', letterSpacing: '-0.02em' }}>{s.v}</div>
              <div className="tiny muted" style={{ fontSize: 11 }}>{s.s}</div>
            </div>
          ))}
        </div>

        <div className="ai-pagebody" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div className="row" style={{ gap: 8 }}>
            <div className="ai-input placeholder" style={{ width: 260 }}><I name="search" size={12} /><span>搜索 ID / 名称</span></div>
            <div className="ai-input placeholder"><span>全部部门</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>Agent 类型</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>用途</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>连接状态</span><I name="chev" size={11} /></div>
            <div style={{ flex: 1 }} />
            <span className="ai-pill bad">17 部门缺分析 Agent</span>
            <span className="ai-pill bad">17 部门缺训练 Agent</span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10 }}>
            {terminals.map(t => {
              const hasExec = !t.exec.includes('缺');
              const hasAnal = !t.anal.includes('缺');
              const hasTrain = !t.train.includes('缺');
              const gaps = [!hasExec, !hasAnal, !hasTrain].filter(Boolean).length;
              return (
                <div key={t.name} className="ai-card" style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <div className="row" style={{ gap: 6 }}>
                    <I name="dept" size={12} stroke="var(--ink-3)" />
                    <span style={{ fontSize: 12.5, fontWeight: 600, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.name}</span>
                    {gaps > 0 && <span className="ai-pill bad" style={{ fontSize: 9.5, height: 14, padding: '0 4px' }}>{gaps} 缺口</span>}
                  </div>
                  <div className="row" style={{ gap: 4, flexWrap: 'wrap' }}>
                    <span className={'ai-pill ' + (hasExec ? (t.exec.includes('1/1') ? 'ok' : '') : 'bad')} style={{ fontSize: 10 }}>执行 {t.exec}</span>
                    <span className={'ai-pill ' + (hasAnal ? 'ok' : 'bad')} style={{ fontSize: 10 }}>分析 {t.anal} {!hasAnal && '+'}</span>
                    <span className={'ai-pill ' + (hasTrain ? 'ok' : 'bad')} style={{ fontSize: 10 }}>训练 {t.train} {!hasTrain && '+'}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </Screen>
  );
}

Object.assign(window, { AdminUsers, AdminCodex, AdminConfig, AdminAgentTerminals });