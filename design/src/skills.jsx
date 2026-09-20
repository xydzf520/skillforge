// Skills 工作台 — admin view.

const SKILLS = [
  { id: 'tmall-link-decline', name: '天猫店铺链接下滑分析', owner: '张文', dept: 'EC', trig: 'cron', ver: 'v0.4', state: 'active', risk: 'R1', uses: 12, lastRun: '15 分钟前', health: 96 },
  { id: 'yuyidata-daily',     name: '客服对话用户需求分析', owner: '李建',  dept: 'CS', trig: 'cron', ver: 'v0.2', state: 'active', risk: 'R2', uses: 4,  lastRun: '昨天 17:22', health: 88 },
  { id: 'mcp-ai-skill',       name: 'MCP AI Skill',         owner: 'MCP AI', dept: 'AI', trig: 'manual', ver: 'v0.0', state: 'active', risk: 'R1', uses: 0, lastRun: '1 天前',  health: 92 },
  { id: 'timeout-run-skill',  name: 'Timeout Run Skill',    owner: '—',    dept: 'EC', trig: 'manual', ver: 'v0.0', state: 'active', risk: 'R2', uses: 1, lastRun: '1 天前', health: 78 },
  { id: 'agent-runtime-1af',  name: 'Agent Runtime',        owner: '—',    dept: 'EC', trig: 'manual', ver: 'v0.0', state: 'active', risk: 'R1', uses: 1, lastRun: '1 天前', health: 84 },
  { id: 'runtime-purpose-d78',name: 'Runtime Purpose',      owner: '—',    dept: 'EC-Runtime', trig: 'manual', ver: 'v0.0', state: 'active', risk: 'R2', uses: 1, lastRun: '1 天前', health: 70 },
  { id: 'bridge-script-efea', name: 'Bridge Script',        owner: '—',    dept: 'EC', trig: 'manual', ver: 'v0.0', state: 'active', risk: 'R2', uses: 1, lastRun: '1 天前', health: 90 },
  { id: 'inventory-rotation', name: '库存周转复盘',         owner: '陈静',  dept: '仓储', trig: 'cron', ver: 'v0.3', state: 'active', risk: 'R2', uses: 9, lastRun: '今日 06:42', health: 94 },
];

function HealthBar({ v }) {
  const c = v >= 90 ? 'var(--ok)' : v >= 80 ? 'var(--ink-3)' : v >= 70 ? 'var(--warn)' : 'var(--bad)';
  return (
    <div className="row" style={{ gap: 6 }}>
      <div style={{ width: 44, height: 4, background: 'var(--surface-3)', borderRadius: 2, overflow: 'hidden' }}>
        <div style={{ width: v + '%', height: '100%', background: c }} />
      </div>
      <span className="mono" style={{ fontSize: 11.5, color: c }}>{v}</span>
    </div>
  );
}

function Skills() {
  return (
    <Screen role="admin" active="skills">
      <div className="ai-sidebar">
        <div className="ai-side-group">
          <div className="ai-side-label">Skills</div>
          <div className="ai-side-item active"><I name="cube" size={13} className="ic" /><span>全部</span><span className="count">74</span></div>
          <div className="ai-side-item"><I name="user" size={13} className="ic" /><span>我创建</span><span className="count">8</span></div>
          <div className="ai-side-item"><I name="users" size={13} className="ic" /><span>我负责</span><span className="count">3</span></div>
          <div className="ai-side-item"><I name="star" size={13} className="ic" /><span>收藏</span><span className="count">5</span></div>
          <div className="ai-side-item"><I name="warn" size={13} className="ic" /><span>不健康</span><span className="count" style={{ color: 'var(--bad)' }}>9</span></div>
        </div>
        <div className="ai-side-group">
          <div className="ai-side-label">按部门</div>
          {[
            { n: 'EC · 电商运营', c: 23 },
            { n: 'CS · 客服', c: 12 },
            { n: 'AI', c: 4 },
            { n: '仓储物流', c: 6 },
            { n: '供应链', c: 5 },
            { n: 'PMO', c: 4 },
            { n: '信息技术', c: 7 },
            { n: '其它', c: 13 },
          ].map(d => (
            <div key={d.n} className="ai-side-item"><I name="dept" size={13} className="ic" /><span>{d.n}</span><span className="count">{d.c}</span></div>
          ))}
        </div>
        <div className="ai-side-group">
          <div className="ai-side-label">状态</div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: 'var(--ok)' }} /><span>正式运行</span><span className="count">74</span></div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: 'var(--warn)' }} /><span>编辑中</span><span className="count">12</span></div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: 'var(--ink-4)' }} /><span>已下线</span><span className="count">3</span></div>
        </div>
      </div>

      <div className="ai-main">
        <div className="ai-pagehead">
          <div>
            <div className="ai-crumbs">Skills 工作台 · 管理员</div>
            <h1 className="ai-title">Skills</h1>
            <p className="ai-sub">编辑、发布、监控部门 Skill；普通员工请前往「能力大厅」运行</p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="ai-btn"><I name="check" size={12} /> 审核中心 <span className="ai-pill warn" style={{ marginLeft: 4 }}>3 待审</span></button>
            <button className="ai-btn"><I name="upload" size={12} /> 导入</button>
            <button className="ai-btn primary"><I name="plus" size={12} /> 新建 Skill</button>
          </div>
        </div>

        <div className="ai-tabs">
          <div className="ai-tab active">列表 <span className="muted" style={{ marginLeft: 4 }}>74</span></div>
          <div className="ai-tab">监控 <span className="ai-pill warn" style={{ marginLeft: 4, fontSize: 10, height: 16, padding: '0 5px' }}>9</span></div>
          <div className="ai-tab">审核 <span className="ai-pill" style={{ marginLeft: 4, fontSize: 10, height: 16, padding: '0 5px' }}>3</span></div>
          <div className="ai-tab">变更日志</div>
          <div style={{ flex: 1 }} />
          <div className="row" style={{ gap: 6, padding: '6px 0' }}>
            <div className="ai-input placeholder" style={{ width: 220 }}><I name="search" size={12} /><span>Skill ID / 名称</span></div>
            <div className="ai-input placeholder"><span>触发</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>风险</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>最近更新</span><I name="chev" size={11} /></div>
            <div className="ai-iconbtn"><I name="list" size={13} /></div>
            <div className="ai-iconbtn" style={{ background: 'var(--surface-2)' }}><I name="grid" size={13} /></div>
          </div>
        </div>

        <div className="ai-pagebody" style={{ padding: 0 }}>
          <table className="ai-table">
            <thead>
              <tr>
                <th style={{ width: 32 }}></th>
                <th>Skill</th>
                <th style={{ width: 90 }}>状态</th>
                <th style={{ width: 100 }}>风险/版本</th>
                <th style={{ width: 120 }}>负责人 / 部门</th>
                <th style={{ width: 100 }}>触发</th>
                <th style={{ width: 110 }}>今日使用</th>
                <th style={{ width: 110 }}>最近运行</th>
                <th style={{ width: 130 }}>健康度</th>
                <th style={{ width: 80 }}></th>
              </tr>
            </thead>
            <tbody>
              {SKILLS.map(s => (
                <tr key={s.id}>
                  <td><input type="checkbox" disabled style={{ accentColor: 'var(--accent)' }} /></td>
                  <td>
                    <div style={{ fontWeight: 500, color: 'var(--ink-1)', fontSize: 13 }}>{s.name}</div>
                    <div className="mono-id">{s.id}</div>
                  </td>
                  <td><span className="ai-pill ok dot">{s.state === 'active' ? '正式运行' : '编辑中'}</span></td>
                  <td>
                    <div className="row" style={{ gap: 6 }}>
                      <span className="ai-pill" style={{ fontSize: 10.5 }}>{s.risk}</span>
                      <span className="mono" style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>{s.ver}</span>
                    </div>
                  </td>
                  <td>
                    <div>{s.owner}</div>
                    <div className="tiny muted">{s.dept}</div>
                  </td>
                  <td><span className="ai-pill" style={{ fontSize: 10.5 }}>{s.trig === 'cron' ? '定时 cron' : '手动'}</span></td>
                  <td>
                    <div className="row" style={{ gap: 6 }}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 500 }}>{s.uses}</span>
                      <span className="spark">
                        {[3, 5, 2, 6, 4, 7, s.uses].map((h, i) => <i key={i} style={{ height: 2 + h * 1.2 + 'px' }} />)}
                      </span>
                    </div>
                  </td>
                  <td className="tiny muted">{s.lastRun}</td>
                  <td><HealthBar v={s.health} /></td>
                  <td>
                    <div className="row" style={{ gap: 4, justifyContent: 'flex-end' }}>
                      <button className="ai-btn sm">编辑</button>
                      <button className="ai-iconbtn"><I name="more" size={13} /></button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Screen>
  );
}

window.Skills = Skills;
window.HealthBar = HealthBar;
