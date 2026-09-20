// Agent — restructured: by department, with Skill-call scope clearly shown.

function Agent() {
  const depts = [
    { id: 'ec',    name: 'EC · 传统电商', online: 4, total: 8, sel: true },
    { id: 'cs',    name: 'CS · 客服',   online: 2, total: 5 },
    { id: 'sales', name: '销售',       online: 1, total: 4 },
    { id: 'pmo',   name: 'PMO',        online: 1, total: 2 },
    { id: 'wh',    name: '仓储物流',    online: 2, total: 3 },
    { id: 'sc',    name: '供应链',      online: 1, total: 3 },
    { id: 'it',    name: '信息技术',    online: 3, total: 4 },
    { id: 'data',  name: '数据/算法',   online: 2, total: 5 },
    { id: 'mkt',   name: '市场',       online: 0, total: 2 },
    { id: 'fin',   name: '财务',       online: 1, total: 2 },
    { id: 'legal', name: '法务',       online: 0, total: 1 },
    { id: 'hr',    name: '人事',       online: 0, total: 1 },
    { id: 'design',name: '设计',       online: 0, total: 1 },
    { id: 'rnd',   name: '研发',       online: 2, total: 3 },
  ];

  const agents = {
    exec: [
      { id: 'ec-runtime-main',  name: 'EC 执行 · Main',        type: 'exec',  status: 'online',  skills: 14, gpu: '—',     last: '8s 前', selected: true },
      { id: 'ec-runtime-1e52',  name: 'EC 执行 · Failover',    type: 'exec',  status: 'online',  skills: 14, gpu: '—',     last: '12s 前' },
    ],
    analyze: [
      { id: 'ec-analyze-d784',  name: 'EC 分析 Agent',         type: 'analyze', status: 'online',  skills: 6,  gpu: '—',     last: '32s 前' },
      { id: 'ec-analyze-8418',  name: 'EC 分析 · 商品诊断',     type: 'analyze', status: 'offline', skills: 4,  gpu: '—',     last: '1 天前' },
    ],
    train: [
      { id: 'ec-train-9d2d',    name: 'EC 训练 Agent',         type: 'train',   status: 'online',  skills: 0,  gpu: 'GPU 2', last: '12 分钟前' },
      { id: 'ec-train-a8ee',    name: 'EC 训练 · Sandbox',     type: 'train',   status: 'offline', skills: 0,  gpu: '—',     last: '昨天 23:00' },
    ],
  };

  const TYPE_META = {
    exec:    { label: '执行', desc: '响应部门 Skill 调用', color: 'var(--accent)' },
    analyze: { label: '分析', desc: '产出报告/诊断/洞察', color: 'var(--info)' },
    train:   { label: '训练', desc: '消耗 GPU 训练部门数据模型', color: 'var(--warn)' },
  };

  const skillsAllowed = [
    { id: 'tmall-link-decline', name: '天猫店铺链接下滑分析', risk: 'R1', src: 'EC',   uses: 12 },
    { id: 'inventory-rotation', name: '库存周转复盘',        risk: 'R2', src: 'EC',   uses: 9 },
    { id: 'order-anomaly',      name: '订单异常巡检',        risk: 'R2', src: 'EC',   uses: 84 },
    { id: 'product-card-gen',   name: '商品诊断卡生成',      risk: 'R1', src: 'EC',   uses: 67 },
    { id: 'biz-weekly',         name: '部门经营周报',        risk: 'R1', src: 'EC',   uses: 1 },
    { id: 'gpt-imagegen',       name: 'GPT-Image 图片生成',  risk: 'R2', src: '通用', uses: 4, shared: true },
    { id: 'gpt-imagegen-dlg',   name: 'GPT-Image 对话生成',  risk: 'R2', src: '通用', uses: 2, shared: true },
  ];

  const AgentRow = ({ a }) => (
    <div className={'row' + (a.selected ? ' sel' : '')} style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)', gap: 10, background: a.selected ? 'var(--surface-2)' : 'transparent', borderLeft: a.selected ? '2px solid var(--ink-1)' : '2px solid transparent', cursor: 'pointer' }}>
      <div style={{ width: 8, height: 8, borderRadius: '50%', background: a.status === 'online' ? 'var(--ok)' : 'var(--ink-5)', flex: '0 0 8px' }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 500, fontSize: 12.5, color: 'var(--ink-1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.name}</div>
        <div className="row" style={{ gap: 6, marginTop: 2 }}>
          <span className="tiny muted">{a.skills} Skills</span>
          <span className="tiny muted">·</span>
          <span className="tiny muted">{a.gpu}</span>
          <span className="tiny muted" style={{ marginLeft: 'auto' }}>{a.last}</span>
        </div>
      </div>
    </div>
  );

  return (
    <Screen role="admin" active="agent" height={920}>
      {/* Sidebar 1: departments */}
      <div className="ai-sidebar" style={{ width: 220 }}>
        <div className="ai-side-group">
          <div className="ai-side-label">按部门</div>
          {depts.map(d => (
            <div key={d.id} className={'ai-side-item' + (d.sel ? ' active' : '')}>
              <I name="dept" size={13} className="ic" />
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
              <span className="tiny" style={{ color: d.online > 0 ? 'var(--ok)' : 'var(--ink-4)' }}>{d.online}/{d.total}</span>
            </div>
          ))}
        </div>
        <div className="ai-side-group">
          <div className="ai-side-label">视图</div>
          <div className="ai-side-item"><I name="layers" size={13} className="ic" /><span>全部 Agent</span><span className="count">66</span></div>
          <div className="ai-side-item"><I name="warn" size={13} className="ic" /><span>离线/异常</span><span className="count" style={{ color: 'var(--bad)' }}>65</span></div>
        </div>
      </div>

      {/* Middle column: agents in selected dept, grouped by type */}
      <div style={{ width: 320, flex: '0 0 320px', borderRight: '1px solid var(--border)', background: 'var(--surface)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '14px 14px 8px', borderBottom: '1px solid var(--border)' }}>
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <div>
              <div style={{ fontWeight: 600, letterSpacing: '-0.01em' }}>EC · 传统电商</div>
              <div className="tiny muted">8 个 Agent · 4 在线</div>
            </div>
            <button className="ai-btn sm"><I name="plus" size={11} /> 新建</button>
          </div>
          <div className="row" style={{ gap: 4, marginTop: 10 }}>
            <span className="ai-pill" style={{ background: 'var(--ink-1)', color: 'white', borderColor: 'var(--ink-1)' }}>全部 6</span>
            <span className="ai-pill">执行 2</span>
            <span className="ai-pill">分析 2</span>
            <span className="ai-pill">训练 2</span>
          </div>
        </div>
        <div style={{ flex: 1, overflow: 'auto' }}>
          {['exec', 'analyze', 'train'].map(t => (
            <div key={t}>
              <div className="row" style={{ padding: '12px 14px 6px', gap: 8 }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: TYPE_META[t].color }} />
                <span style={{ fontSize: 11.5, fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--ink-2)' }}>{TYPE_META[t].label}</span>
                <span className="tiny muted">· {TYPE_META[t].desc}</span>
              </div>
              {agents[t].map(a => <AgentRow key={a.id} a={a} />)}
            </div>
          ))}
        </div>
      </div>

      {/* Right: focused agent */}
      <div className="ai-main" style={{ background: 'var(--bg)' }}>
        <div style={{ padding: '20px 28px 16px', borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
          <div className="row" style={{ gap: 12 }}>
            <div style={{ width: 44, height: 44, borderRadius: 10, background: 'var(--ink-1)', display: 'grid', placeItems: 'center', color: 'white' }}>
              <I name="bolt" size={20} />
            </div>
            <div style={{ flex: 1 }}>
              <div className="row" style={{ gap: 8 }}>
                <span style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.015em' }}>EC 执行 · Main</span>
                <span className="ai-pill ok dot">在线</span>
                <span className="ai-pill" style={{ fontSize: 10.5 }}>执行</span>
                <span className="mono-id">ec-runtime-main · aiclaw</span>
              </div>
              <div className="row" style={{ gap: 12, marginTop: 4 }}>
                <span className="tiny muted">部门 <span style={{ color: 'var(--ink-2)' }}>EC</span></span>
                <span className="tiny muted">分支 <span className="mono" style={{ color: 'var(--ink-2)' }}>main</span></span>
                <span className="tiny muted">运行时 <span style={{ color: 'var(--ink-2)' }}>aiclaw</span></span>
                <span className="tiny muted">最近活跃 <span style={{ color: 'var(--ink-2)' }}>8s 前</span></span>
              </div>
            </div>
            <div className="row" style={{ gap: 6 }}>
              <button className="ai-btn"><I name="hist" size={12} /> 运行历史</button>
              <button className="ai-btn"><I name="refresh" size={12} /> 重启</button>
              <button className="ai-btn primary"><I name="send" size={12} /> 对话</button>
            </div>
          </div>
        </div>

        <div className="ai-pagebody" style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 16 }}>
          {/* Left: skills it can call */}
          <div className="ai-card">
            <div className="ai-card-h">
              <span className="t">可调用 Skills</span>
              <span className="s">{skillsAllowed.length} 个 · 部门授权 5，共享 2</span>
              <div className="row" style={{ marginLeft: 'auto', gap: 6 }}>
                <button className="ai-btn sm">配置范围</button>
              </div>
            </div>
            <table className="ai-table">
              <thead>
                <tr>
                  <th>Skill</th>
                  <th style={{ width: 60 }}>风险</th>
                  <th style={{ width: 70 }}>来源</th>
                  <th style={{ width: 90 }}>今日调用</th>
                  <th style={{ width: 56 }}></th>
                </tr>
              </thead>
              <tbody>
                {skillsAllowed.map(s => (
                  <tr key={s.id}>
                    <td>
                      <div style={{ fontWeight: 500, fontSize: 12.5 }}>{s.name}</div>
                      <div className="mono-id">{s.id}</div>
                    </td>
                    <td><span className="ai-pill" style={{ fontSize: 10.5 }}>{s.risk}</span></td>
                    <td>{s.shared ? <span className="ai-pill accent" style={{ fontSize: 10.5 }}>共享</span> : <span className="tiny muted">{s.src}</span>}</td>
                    <td><span className="mono">{s.uses}</span></td>
                    <td className="row" style={{ justifyContent: 'flex-end' }}><button className="ai-btn sm">试运行</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Right column stack */}
          <div className="col" style={{ gap: 16 }}>
            <div className="ai-card">
              <div className="ai-card-h"><span className="t">运行指标 · 近 24h</span></div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)' }}>
                {[
                  { l: '调用', v: '2,184', d: '↑ 9%' },
                  { l: '成功率', v: '98.6%', d: '↑ 0.4', ok: true },
                  { l: '平均耗时', v: '420ms', d: '↓ 30' },
                ].map((s, i) => (
                  <div key={s.l} style={{ padding: 14, borderRight: i < 2 ? '1px solid var(--border)' : 0 }}>
                    <div className="tiny muted">{s.l}</div>
                    <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.015em' }}>{s.v}</div>
                    <div className="tiny" style={{ color: s.ok ? 'var(--ok)' : 'var(--ink-3)' }}>{s.d}</div>
                  </div>
                ))}
              </div>
              <div style={{ padding: '4px 14px 14px' }}>
                <svg width="100%" height="60" viewBox="0 0 300 60" preserveAspectRatio="none">
                  <polyline points="0,46 25,42 50,38 75,40 100,30 125,32 150,22 175,28 200,18 225,24 250,14 275,18 300,12" fill="none" stroke="var(--accent)" strokeWidth="1.5" />
                  <polyline points="0,50 25,50 50,46 75,50 100,42 125,46 150,38 175,42 200,32 225,38 250,28 275,32 300,26" fill="none" stroke="var(--ink-4)" strokeWidth="1" strokeDasharray="2 3" />
                </svg>
              </div>
            </div>

            <div className="ai-card">
              <div className="ai-card-h">
                <span className="t">部门 Agent 覆盖</span>
                <a className="link" style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--ink-3)' }}>管理</a>
              </div>
              <div style={{ padding: '6px 14px 14px' }}>
                {[
                  { k: '执行 (exec)',  v: '✓ Main + Failover', state: 'ok' },
                  { k: '分析 (analyze)', v: 'd784 在线 · 8418 离线', state: 'mix' },
                  { k: '训练 (train)',  v: '9d2d 在线 · a8ee 离线', state: 'mix' },
                ].map(r => (
                  <div key={r.k} className="row" style={{ padding: '10px 0', borderBottom: '1px solid var(--border)', gap: 10 }}>
                    <span style={{ width: 8, height: 8, borderRadius: 2, background: r.state === 'ok' ? 'var(--ok)' : 'var(--warn)' }} />
                    <span style={{ fontSize: 12.5, color: 'var(--ink-1)', fontWeight: 500, width: 120 }}>{r.k}</span>
                    <span className="tiny muted">{r.v}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="ai-card">
              <div className="ai-card-h">
                <span className="t">最近对话</span>
                <a className="link" style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--ink-3)' }}>查看全部</a>
              </div>
              <div style={{ padding: '6px 14px 14px' }}>
                {[
                  { q: '帮我列出当前所有 Skill 的状态', when: '今天 09:42' },
                  { q: '今天有哪些待审核的变更？',    when: '今天 09:15' },
                  { q: '查询最近一次执行失败的原因', when: '昨天 23:08' },
                ].map((c, i, arr) => (
                  <div key={i} className="row" style={{ padding: '10px 0', borderBottom: i === arr.length - 1 ? '0' : '1px solid var(--border)', gap: 8 }}>
                    <I name="send" size={11} stroke="var(--ink-4)" />
                    <span style={{ fontSize: 12.5, color: 'var(--ink-2)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.q}</span>
                    <span className="tiny muted">{c.when}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </Screen>
  );
}

window.Agent = Agent;
