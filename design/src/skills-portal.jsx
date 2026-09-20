// Skills sub-pages — 应用门户 (Application Portal) + 审核中心 (Review Center).

// 应用门户 — for regular employees to find runnable skills (per dept)
function SkillsPortal() {
  const apps = [
    { name: 'MCP AI Skill',     owner: 'MCP AI User', uses: 0, success: '—',    last: '暂无执行' },
    { name: 'Timeout Run Skill', owner: '未设置',     uses: 0, success: '—',    last: '暂无执行' },
    { name: 'Agent Runtime',    owner: '未设置',     uses: 0, success: '—',    last: '暂无执行' },
    { name: 'Runtime Purpose',  owner: '未设置',     uses: 0, success: '—',    last: '暂无执行' },
    { name: 'Bridge Script',    owner: '未设置',     uses: 0, success: '—',    last: '暂无执行' },
    { name: '订单异常巡检',     owner: '陈静',       uses: 84, success: '98%', last: '8 分钟前' },
    { name: '商品诊断卡生成',   owner: '张文',       uses: 67, success: '94%', last: '15 分钟前' },
    { name: '库存周转复盘',     owner: '李伟',       uses: 9,  success: '100%', last: '今日 06:42' },
    { name: '客服 KPI 看板',    owner: '王琳',       uses: 41, success: '96%', last: '今日 08:12' },
    { name: '部门经营周报',     owner: '张文',       uses: 1,  success: '100%', last: '上周一' },
    { name: '供应链异常',       owner: '吴强',       uses: 12, success: '92%', last: '今日 09:30' },
    { name: '销售商机评分',     owner: '黄敏',       uses: 28, success: '88%', last: '今日 10:00' },
  ];

  return (
    <Screen role="admin" active="skills">
      <div className="ai-sidebar">
        <div className="ai-side-group">
          <div className="ai-side-label">Skills 模块</div>
          <div className="ai-side-item"><I name="cube" size={13} className="ic" /><span>Skills</span><span className="count">74</span></div>
          <div className="ai-side-item active"><I name="grid" size={13} className="ic" /><span>应用门户</span><span className="count">42</span></div>
          <div className="ai-side-item"><I name="check" size={13} className="ic" /><span>审核中心</span><span className="count">3</span></div>
        </div>
        <div className="ai-side-group">
          <div className="ai-side-label">按部门</div>
          {[
            { n: 'EC · 电商运营', c: 23, active: true },
            { n: 'CS · 客服', c: 12 },
            { n: '仓储物流', c: 6 },
            { n: '供应链', c: 5 },
            { n: 'PMO', c: 4 },
            { n: '其它', c: 24 },
          ].map(d => (
            <div key={d.n} className={'ai-side-item' + (d.active ? ' active' : '')}>
              <I name="dept" size={13} className="ic" />
              <span>{d.n}</span>
              <span className="count">{d.c}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="ai-main">
        <div className="ai-pagehead">
          <div>
            <div className="ai-crumbs">Skills · 应用门户</div>
            <h1 className="ai-title">应用门户 · 执行入口</h1>
            <p className="ai-sub">面向普通员工：浏览本部门已发布、可直接执行的 Skill。新建 / 编辑请去「Skills 工作台」</p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="ai-btn">部门概览</button>
            <button className="ai-btn primary"><I name="plus" size={12} /> 申请新能力</button>
          </div>
        </div>

        <div className="ai-pagebody" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="row" style={{ gap: 8 }}>
            <div className="ai-input placeholder" style={{ width: 320 }}><I name="search" size={12} /><span>搜索 Skill</span></div>
            <div className="ai-input placeholder"><span>状态</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>风险</span><I name="chev" size={11} /></div>
            <div style={{ flex: 1 }} />
            <span className="ai-pill" style={{ background: 'var(--ink-1)', color: 'white', borderColor: 'var(--ink-1)' }}>全部 42</span>
            <span className="ai-pill">通用 8</span>
            <span className="ai-pill">本部门 34</span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
            {apps.map(a => (
              <div key={a.name} className="ai-card" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div className="row" style={{ gap: 10, alignItems: 'flex-start' }}>
                  <div style={{ width: 30, height: 30, borderRadius: 7, background: 'var(--surface-2)', display: 'grid', placeItems: 'center', flex: '0 0 30px' }}>
                    <I name="cube" size={14} stroke="var(--ink-1)" />
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: '-0.01em' }}>{a.name}</div>
                    <div className="tiny muted" style={{ marginTop: 3 }}>负责人 · {a.owner}</div>
                  </div>
                </div>
                <div className="row" style={{ gap: 0, padding: '4px 0', borderTop: '1px dashed var(--border)', borderBottom: '1px dashed var(--border)' }}>
                  <div style={{ flex: 1 }}>
                    <div className="tiny muted" style={{ fontSize: 10 }}>使用次数</div>
                    <span className="mono" style={{ fontSize: 13, fontWeight: 600 }}>{a.uses}</span>
                  </div>
                  <div style={{ flex: 1 }}>
                    <div className="tiny muted" style={{ fontSize: 10 }}>成功率</div>
                    <span className="mono" style={{ fontSize: 13, fontWeight: 600 }}>{a.success}</span>
                  </div>
                  <div style={{ flex: 1.4 }}>
                    <div className="tiny muted" style={{ fontSize: 10 }}>最近执行</div>
                    <span style={{ fontSize: 11.5, color: 'var(--ink-2)' }}>{a.last}</span>
                  </div>
                </div>
                <div className="row" style={{ gap: 6, marginTop: 'auto' }}>
                  <button className="ai-btn primary sm" style={{ flex: 1, justifyContent: 'center' }}><I name="play" size={10} /> 运行</button>
                  <button className="ai-btn sm">界面</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Screen>
  );
}

// 审核中心 — admin review queue
function ReviewCenter() {
  const items = [
    { id: '#7',  skill: 'order-anomaly-detect',     summary: '订单异常巡检 · 添加冷启动规则',     kind: '更新', submitter: 'chen.j',  reviewer: 'admin', state: 'pending', date: '2026-05-24 09:48' },
    { id: '#6',  skill: 'cs-need-analysis-v2',      summary: '客服需求分析 v2 · 调整阈值与输出',   kind: '更新', submitter: 'li.j',    reviewer: '—',     state: 'pending', date: '2026-05-24 08:10' },
    { id: '#5',  skill: 'douyin-review-distill',    summary: '抖音评论摘要 · 新建',               kind: '新建', submitter: 'wang.m',  reviewer: 'admin', state: 'pending', date: '2026-05-24 07:33' },
    { id: '#4',  skill: 'gpu-cost-trim',            summary: 'GPU 成本优化 · 接入 IT 数据源',     kind: '更新', submitter: 'it.team', reviewer: 'admin', state: 'passed',  date: '2026-05-23 22:11' },
    { id: '#3',  skill: 'contract-redline',         summary: '合同红线识别 · 修复条款匹配',       kind: '修复', submitter: 'legal.l', reviewer: 'admin', state: 'passed',  date: '2026-05-23 18:42' },
    { id: '#2',  skill: 'supplier-risk-v1',         summary: '供应商交付风险评分 v1 · 新建',     kind: '新建', submitter: 'wu.q',    reviewer: 'admin', state: 'rejected', date: '2026-05-23 14:20' },
    { id: '#1',  skill: 'tmall-link-decline',       summary: '天猫店铺链接下滑分析 · 提交审核',  kind: '更新', submitter: 'zhang.w', reviewer: 'admin', state: 'passed',  date: '2026-04-26 02:57' },
  ];
  const pill = (s) => s === 'pending' ? <span className="ai-pill warn">待审核</span>
                   : s === 'passed'  ? <span className="ai-pill ok">已通过</span>
                   : <span className="ai-pill bad">已驳回</span>;

  return (
    <Screen role="admin" active="skills">
      <div className="ai-sidebar">
        <div className="ai-side-group">
          <div className="ai-side-label">Skills 模块</div>
          <div className="ai-side-item"><I name="cube" size={13} className="ic" /><span>Skills</span><span className="count">74</span></div>
          <div className="ai-side-item"><I name="grid" size={13} className="ic" /><span>应用门户</span><span className="count">42</span></div>
          <div className="ai-side-item active"><I name="check" size={13} className="ic" /><span>审核中心</span><span className="count">3</span></div>
        </div>
        <div className="ai-side-group">
          <div className="ai-side-label">视图</div>
          <div className="ai-side-item active"><span className="deptdot" style={{ background: 'var(--ink-1)' }} /><span>全部</span><span className="count">7</span></div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: 'var(--warn)' }} /><span>待审核</span><span className="count">3</span></div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: 'var(--ok)' }} /><span>已通过</span><span className="count">3</span></div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: 'var(--bad)' }} /><span>已驳回</span><span className="count">1</span></div>
        </div>
        <div className="ai-side-group">
          <div className="ai-side-label">变更类型</div>
          <div className="ai-side-item"><span>新建</span><span className="count">2</span></div>
          <div className="ai-side-item"><span>更新</span><span className="count">4</span></div>
          <div className="ai-side-item"><span>修复</span><span className="count">1</span></div>
        </div>
      </div>

      <div className="ai-main">
        <div className="ai-pagehead">
          <div>
            <div className="ai-crumbs">Skills · 审核中心</div>
            <h1 className="ai-title">审核中心</h1>
            <p className="ai-sub">处理 Skill / Playbook 变更发布审核</p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="ai-btn"><I name="filter" size={12} /> 批量通过</button>
            <button className="ai-btn primary"><I name="check" size={12} /> 审核策略</button>
          </div>
        </div>

        <div className="ai-pagebody" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="row" style={{ gap: 8 }}>
            <div className="ai-input placeholder" style={{ width: 280 }}><I name="search" size={12} /><span>搜索 Skill ID / 摘要</span></div>
            <div className="ai-input placeholder"><span>提交人</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>审核人</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>2026-05-17 – 24</span></div>
            <div style={{ flex: 1 }} />
            <span className="ai-pill warn">待审核 3</span>
            <span className="ai-pill ok">已通过 3</span>
            <span className="ai-pill bad">已驳回 1</span>
          </div>

          <div className="ai-card" style={{ padding: 0 }}>
            <table className="ai-table">
              <thead>
                <tr>
                  <th style={{ width: 40 }}>ID</th>
                  <th style={{ width: 200 }}>Skill</th>
                  <th>变更摘要</th>
                  <th style={{ width: 70 }}>类型</th>
                  <th style={{ width: 110 }}>提交人</th>
                  <th style={{ width: 110 }}>审核人</th>
                  <th style={{ width: 100 }}>状态</th>
                  <th style={{ width: 140 }}>提交时间</th>
                  <th style={{ width: 180 }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map(it => (
                  <tr key={it.id}>
                    <td className="mono-id">{it.id}</td>
                    <td><span className="mono" style={{ fontSize: 12, fontWeight: 500 }}>{it.skill}</span></td>
                    <td>{it.summary}</td>
                    <td><span className="ai-pill" style={{ fontSize: 10.5 }}>{it.kind}</span></td>
                    <td className="mono" style={{ fontSize: 12 }}>{it.submitter}</td>
                    <td className="mono" style={{ fontSize: 12 }}>{it.reviewer}</td>
                    <td>{pill(it.state)}</td>
                    <td className="tiny muted">{it.date}</td>
                    <td>
                      <div className="row" style={{ gap: 4, justifyContent: 'flex-end' }}>
                        {it.state === 'pending'
                          ? <>
                              <button className="ai-btn sm">驳回</button>
                              <button className="ai-btn sm">查看</button>
                              <button className="ai-btn primary sm">通过</button>
                            </>
                          : <button className="ai-btn sm">查看</button>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="row" style={{ justifyContent: 'space-between', color: 'var(--ink-4)', fontSize: 12 }}>
            <span>共 7 条 · 显示 7 / 7</span>
            <span className="row" style={{ gap: 2 }}>
              <button className="ai-iconbtn"><I name="arrowl" size={12} /></button>
              <span style={{ padding: '0 8px' }}>1</span>
              <button className="ai-iconbtn"><I name="arrowr" size={12} /></button>
            </span>
          </div>
        </div>
      </div>
    </Screen>
  );
}

Object.assign(window, { SkillsPortal, ReviewCenter });