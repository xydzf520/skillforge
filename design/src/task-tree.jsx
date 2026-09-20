// 任务树 — node tree monitoring view.

function TaskTree() {
  const nodes = [
    { id: 'main',     name: '主节点',         status: 'offline', last: '766h 前', success: 0, fail: 0, avg: '—',     runs: [0,0,0,0,0,0,0,0,0,0,0,0] },
    { id: 'ec-anal-d784', name: 'EC 分析 Agent', status: 'offline', last: '1 天前', success: 0, fail: 0, avg: '—',  runs: [0,0,0,0,0,0,0,0,0,0,0,0], group: 'EC', sel: true },
    { id: 'ec-anal-8418', name: 'EC 分析 Agent', status: 'offline', last: '1 天前', success: 0, fail: 0, avg: '—',  runs: [0,0,0,0,0,0,0,0,0,0,0,0], group: 'EC' },
    { id: 'ec-run-main',  name: 'EC 执行 · Main', status: 'online',  last: '8s 前',  success: 184, fail: 2, avg: '420ms', runs: [3,5,4,6,8,7,9,7,8,9,7,8], group: 'EC' },
    { id: 'cs-run-1',  name: 'CS 客服 Agent', status: 'online',  last: '12s 前', success: 56,  fail: 0, avg: '380ms', runs: [2,3,4,5,4,3,5,6,4,5,3,4], group: 'CS' },
    { id: 'cs-anal-1', name: 'CS 需求分析', status: 'offline', last: '2 天前', success: 0, fail: 1, avg: '—',  runs: [0,0,0,0,0,0,0,0,0,0,0,0], group: 'CS' },
    { id: 'wh-anom',   name: '仓储异常巡检', status: 'offline', last: '12h 前', success: 0, fail: 0, avg: '—', runs: [0,0,0,0,0,0,0,0,0,0,0,0], group: '仓储' },
    { id: 'sales-rank', name: '销售商机排名', status: 'offline', last: '5h 前', success: 0, fail: 2, avg: '—', runs: [0,0,0,0,0,0,0,0,0,0,0,0], group: '销售' },
  ];

  const SpriteBar = ({ runs }) => (
    <div className="row" style={{ gap: 1, alignItems: 'flex-end', height: 16 }}>
      {runs.map((r, i) => (
        <div key={i} style={{ flex: 1, height: Math.max(1, r * 1.7) + 'px', background: r > 0 ? 'var(--ok)' : 'var(--ink-5)', opacity: r > 0 ? 1 : 0.3, borderRadius: 1 }} />
      ))}
    </div>
  );

  return (
    <Screen role="admin" active="tree" height={920}>
      <div className="ai-main">
        <div className="ai-pagehead">
          <div>
            <div className="ai-crumbs">监控 · 任务树</div>
            <h1 className="ai-title">任务树</h1>
            <p className="ai-sub">所有部门 Agent 节点的运行状态、调度与诊断 · 刷新 2026-05-24 10:50</p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <span className="tiny muted">下次 6s</span>
            <span className="ai-pill">版本 db4a355d</span>
            <button className="ai-btn"><I name="warn" size={12} /> 批量操作</button>
            <button className="ai-btn primary"><I name="refresh" size={12} /> 刷新</button>
          </div>
        </div>

        {/* KPI strip */}
        <div className="row" style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
          {[
            { l: '节点总数', v: '66', s: 'online 1 · offline 65' },
            { l: '离线', v: '65', s: '占比 98.5%', c: 'var(--bad)' },
            { l: '运行中', v: '0', s: '当前活跃 0' },
            { l: '今日失败率', v: '0.0%', s: '失败 0 / 总 0', c: 'var(--ok)' },
            { l: '即将到期定时', v: '4', s: '未来 1 小时内' },
          ].map((s, i, arr) => (
            <div key={s.l} style={{ flex: 1, padding: '14px 20px', borderRight: i === arr.length - 1 ? '0' : '1px solid var(--border)' }}>
              <div className="tiny muted">{s.l}</div>
              <div className="mono" style={{ fontSize: 22, fontWeight: 600, color: s.c || 'var(--ink-1)', letterSpacing: '-0.02em' }}>{s.v}</div>
              <div className="tiny muted" style={{ fontSize: 11 }}>{s.s}</div>
            </div>
          ))}
        </div>

        <div className="ai-tabs">
          <div className="ai-tab">运行中 <span className="muted" style={{ marginLeft: 4 }}>0</span></div>
          <div className="ai-tab active">异常 <span className="ai-pill bad" style={{ marginLeft: 4, fontSize: 10, height: 16, padding: '0 5px' }}>65</span></div>
          <div className="ai-tab">已完成 <span className="ai-pill ok" style={{ marginLeft: 4, fontSize: 10, height: 16, padding: '0 5px' }}>0</span></div>
        </div>

        <div className="ai-pagebody" style={{ padding: 0, display: 'flex', flex: 1, minHeight: 0 }}>
          {/* Left: filter + node list */}
          <div style={{ width: 480, flex: '0 0 480px', borderRight: '1px solid var(--border)', background: 'var(--surface)', display: 'flex', flexDirection: 'column' }}>
            <div className="col" style={{ gap: 8, padding: '14px 16px', borderBottom: '1px solid var(--border)' }}>
              <div className="row" style={{ gap: 8 }}>
                <div className="ai-input placeholder" style={{ flex: 1 }}><I name="search" size={12} /><span>节点名 / ID (空格分多词)</span></div>
              </div>
              <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                <span className="tiny muted" style={{ marginRight: 4 }}>时间窗</span>
                <span className="ai-pill">1h</span>
                <span className="ai-pill" style={{ background: 'var(--ink-1)', color: 'white', borderColor: 'var(--ink-1)' }}>24h</span>
                <span className="ai-pill">7d</span>
                <span style={{ width: 12 }} />
                <span className="tiny muted" style={{ marginRight: 4 }}>密度</span>
                <span className="ai-pill">紧凑</span>
                <span className="ai-pill" style={{ background: 'var(--ink-1)', color: 'white', borderColor: 'var(--ink-1)' }}>宽松</span>
                <div style={{ flex: 1 }} />
                <div className="ai-input placeholder"><span>部门 全部</span><I name="chev" size={11} /></div>
              </div>
            </div>

            <div style={{ flex: 1, overflow: 'auto', padding: '12px 16px' }}>
              <div className="row" style={{ gap: 6, padding: '0 0 10px', marginBottom: 8, borderBottom: '1px dashed var(--border)' }}>
                <I name="dept" size={12} stroke="var(--ink-3)" />
                <span style={{ fontSize: 11.5, fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--ink-2)' }}>平台节点</span>
                <span className="ai-pill bad" style={{ fontSize: 10, height: 16, padding: '0 5px' }}>异常 65</span>
                <span className="tiny muted" style={{ marginLeft: 'auto' }}>● 0 · ● 65</span>
              </div>

              {nodes.map(n => {
                const isOnline = n.status === 'online';
                return (
                  <div key={n.id} className="ai-card" style={{ padding: '12px 14px', marginBottom: 8, borderColor: n.sel ? 'var(--ink-1)' : 'var(--border)', borderWidth: n.sel ? '1.5px' : '1px', cursor: 'pointer' }}>
                    <div className="row" style={{ gap: 8, marginBottom: 6 }}>
                      <span style={{ width: 7, height: 7, borderRadius: '50%', background: isOnline ? 'var(--ok)' : 'var(--bad)' }} />
                      <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>{n.name}</span>
                      <span className={'ai-pill ' + (isOnline ? 'ok dot' : 'bad')} style={{ fontSize: 10, height: 16, padding: '0 5px' }}>{isOnline ? '在线' : '离线'}</span>
                      <span className="tiny muted">{n.last}</span>
                    </div>
                    <div className="row" style={{ gap: 14, fontSize: 11, color: 'var(--ink-3)' }}>
                      <span>最近成功 <span className="mono" style={{ color: 'var(--ink-1)' }}>{n.success}</span></span>
                      <span>连续失败 <span className="mono" style={{ color: n.fail > 0 ? 'var(--bad)' : 'var(--ink-1)' }}>{n.fail}</span></span>
                      <span>平均耗时 <span className="mono" style={{ color: 'var(--ink-1)' }}>{n.avg}</span></span>
                    </div>
                    <div className="row" style={{ marginTop: 8, gap: 8 }}>
                      <span className="tiny muted" style={{ width: 40 }}>近 24h</span>
                      <div style={{ flex: 1 }}><SpriteBar runs={n.runs} /></div>
                      <span className="tiny muted">{n.runs.reduce((a,b)=>a+b,0)} 次</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Right: detail panel for selected node */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <div className="row" style={{ padding: '16px 24px', borderBottom: '1px solid var(--border)', background: 'var(--surface)', gap: 12 }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--bad)' }} />
              <div className="col" style={{ flex: 1, gap: 2 }}>
                <span style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.015em' }}>EC 分析 Agent</span>
                <span className="tiny muted">平台节点 · ec-analyze-d784 · aiclaw</span>
              </div>
              <span className="ai-pill bad">离线</span>
              <button className="ai-btn sm">关闭</button>
            </div>

            <div className="ai-tabs" style={{ padding: '0 24px' }}>
              <div className="ai-tab">节点详情</div>
              <div className="ai-tab">运行诊断</div>
              <div className="ai-tab">链路</div>
              <div className="ai-tab active">定时任务</div>
              <div className="ai-tab">价值</div>
            </div>

            <div style={{ flex: 1, overflow: 'auto', padding: 24, background: 'var(--bg)' }}>
              <div className="ai-card" style={{ padding: 0, marginBottom: 16 }}>
                <div className="ai-card-h">
                  <span className="t">定时任务</span>
                  <span className="s">该节点暂无定时任务</span>
                  <div className="row" style={{ marginLeft: 'auto', gap: 6 }}>
                    <button className="ai-btn sm"><I name="plus" size={11} /> 新建定时</button>
                  </div>
                </div>
                <div style={{ padding: '32px 24px', textAlign: 'center', color: 'var(--ink-3)' }}>
                  <I name="inbox" size={24} stroke="var(--ink-4)" />
                  <div style={{ fontSize: 13, marginTop: 8 }}>该节点暂无定时任务</div>
                  <div className="tiny muted" style={{ marginTop: 4 }}>在 Skill 编辑器中设置 trigger_type=cron 即可创建定时任务</div>
                </div>
              </div>

              <div className="ai-card" style={{ padding: 0 }}>
                <div className="ai-card-h">
                  <span className="t">最近运行 (近 24h)</span>
                  <span className="s">0 次 / 0 失败</span>
                </div>
                <div style={{ padding: '24px 24px 16px' }}>
                  <svg width="100%" height="80" viewBox="0 0 800 80" preserveAspectRatio="none">
                    <line x1="0" y1="78" x2="800" y2="78" stroke="var(--border)" />
                    <line x1="0" y1="40" x2="800" y2="40" stroke="var(--border)" strokeDasharray="3 4" />
                    {Array.from({ length: 24 }).map((_, i) => (
                      <line key={i} x1={i * 800 / 24} y1="74" x2={i * 800 / 24} y2="78" stroke="var(--ink-4)" />
                    ))}
                    <text x="0" y="14" fontSize="9" fill="var(--ink-4)">运行次数</text>
                    <text x="0" y="68" fontSize="9" fill="var(--ink-4)">0</text>
                  </svg>
                  <div className="row" style={{ marginTop: 8, fontSize: 10, color: 'var(--ink-4)' }}>
                    <span>10:50</span>
                    <span style={{ marginLeft: 'auto' }}>16:50</span>
                    <span style={{ marginLeft: 'auto' }}>22:50</span>
                    <span style={{ marginLeft: 'auto' }}>04:50</span>
                    <span style={{ marginLeft: 'auto' }}>now</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Screen>
  );
}

window.TaskTree = TaskTree;