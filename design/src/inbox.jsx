// 收件 (Inbox) — employee view, cleaner than the original.

function Inbox() {
  const items = [
    { id: 1, prio: 'P1', state: 'todo', sla: '47h 44m', kind: '商品诊断',
      title: '【热感因子】示例品牌热感人体润滑液夫妻情趣用品缓解保湿润滑油剂',
      sku: '888456887276',
      score: 0.7723, scoreState: 'bad',
      metrics: [{ k: '访客排名', v: '#57' }, { k: '支付排名', v: '#75' }, { k: '+3 指标' }],
      skill: 'tmall-link-decline...sis-v2', when: '15 分钟前' },
    { id: 2, prio: 'P1', state: 'todo', sla: '47h 44m', kind: '商品诊断',
      title: '【超薄持久3只】示例品牌避孕套持久装套安全套男用官方旗舰店正品',
      sku: '800687205629',
      score: 0.7333, scoreState: 'bad',
      metrics: [{ k: '访客排名', v: '#85' }, { k: '支付排名', v: '#68' }, { k: '+3 指标' }],
      skill: 'tmall-link-decline...sis-v2', when: '15 分钟前' },
    { id: 3, prio: 'P2', state: 'todo', sla: '23h 11m', kind: '库存预警',
      title: '【超薄001】001 安全套黑金 001 男用超薄旗舰店安全套 5只',
      sku: '800559778378',
      score: 0.5119, scoreState: 'warn',
      metrics: [{ k: '库存天数', v: '78' }, { k: '滞销概率', v: '高' }, { k: '+2 指标' }],
      skill: 'inventory-rotation', when: '1 小时前' },
    { id: 4, prio: 'P3', state: 'todo', sla: '47h 02m', kind: '商品诊断',
      title: '【双头跳蛋】震动跳蛋情趣用品成人用品',
      sku: '936999226294',
      score: 0.50, scoreState: 'warn',
      metrics: [{ k: '访客排名', v: '#92' }, { k: '+2 指标' }],
      skill: 'tmall-link-decline...sis-v2', when: '2 小时前' },
  ];

  return (
    <Screen role="admin" active="inbox">
      <div className="ai-sidebar" style={{ width: 200, flex: '0 0 200px' }}>
        <div className="ai-side-group">
          <div className="ai-side-label">视图</div>
          <div className="ai-side-item active"><I name="inbox" size={13} className="ic" /><span>所有待办</span><span className="count">56</span></div>
          <div className="ai-side-item"><I name="warn" size={13} className="ic" /><span>P1 优先</span><span className="count" style={{ color: 'var(--bad)' }}>8</span></div>
          <div className="ai-side-item"><I name="users" size={13} className="ic" /><span>派给我</span><span className="count">12</span></div>
          <div className="ai-side-item"><I name="check" size={13} className="ic" /><span>我已处理</span><span className="count">132</span></div>
        </div>
        <div className="ai-side-group">
          <div className="ai-side-label">报告流</div>
          <div className="ai-side-item"><I name="doc" size={13} className="ic" /><span>报告</span><span className="count">8</span></div>
          <div className="ai-side-item"><I name="trend" size={13} className="ic" /><span>Drift / 漂移</span><span className="count">3</span></div>
        </div>
        <div className="ai-side-group">
          <div className="ai-side-label">来源 Skill</div>
          {[
            { n: '店铺链接下滑', c: 36 },
            { n: '客服需求分析',  c: 7 },
            { n: '库存周转',     c: 9 },
            { n: '订单异常',     c: 4 },
          ].map(s => (
            <div key={s.n} className="ai-side-item"><I name="cube" size={13} className="ic" /><span>{s.n}</span><span className="count">{s.c}</span></div>
          ))}
        </div>
      </div>

      <div className="ai-main">
        <div className="ai-pagehead">
          <div>
            <div className="ai-crumbs">工作台 · 收件</div>
            <h1 className="ai-title">收件</h1>
            <p className="ai-sub">由能力产出的待办、报告与漂移在此统一处理</p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="ai-btn"><I name="filter" size={12} /> 批量派发</button>
            <button className="ai-btn"><I name="download" size={12} /> 导出</button>
            <button className="ai-btn primary"><I name="check" size={12} /> 全部已读</button>
          </div>
        </div>

        {/* KPI strip — replaces the cluttered right side of the pagehead */}
        <div className="row" style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
          {[
            { l: '待处理', v: '56',  s: '本周 +12 件',  c: 'var(--bad)' },
            { l: '今日新到', v: '24', s: '5 P1 · 11 P2 · 8 P3', c: 'var(--ink-1)' },
            { l: '已过期', v: '0',   s: '24h SLA', c: 'var(--ink-1)' },
            { l: '派发执行中', v: '0', s: '负责人 8 位', c: 'var(--ink-1)' },
            { l: 'SLA 达成率', v: '2.2%', s: '近 7 日', c: 'var(--bad)' },
          ].map((s, i, arr) => (
            <div key={s.l} style={{ flex: 1, padding: '14px 20px', borderRight: i === arr.length - 1 ? '0' : '1px solid var(--border)' }}>
              <div className="tiny muted">{s.l}</div>
              <div className="row" style={{ gap: 8, alignItems: 'baseline', marginTop: 2 }}>
                <span className="mono" style={{ fontSize: 22, fontWeight: 600, color: s.c, letterSpacing: '-0.02em' }}>{s.v}</span>
              </div>
              <div className="tiny muted" style={{ marginTop: 2, fontSize: 11 }}>{s.s}</div>
            </div>
          ))}
        </div>

        <div className="ai-tabs">
          <div className="ai-tab active">待办 <span className="ai-pill bad" style={{ marginLeft: 4, fontSize: 10, height: 16, padding: '0 5px' }}>56</span></div>
          <div className="ai-tab">报告 <span className="ai-pill" style={{ marginLeft: 4, fontSize: 10, height: 16, padding: '0 5px' }}>8</span></div>
          <div className="ai-tab">Drift <span className="ai-pill warn" style={{ marginLeft: 4, fontSize: 10, height: 16, padding: '0 5px' }}>3</span></div>
        </div>

        <div className="ai-pagebody">
          {/* Filter row — separated from tab strip */}
          <div className="row" style={{ gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
            <div className="ai-input placeholder" style={{ width: 280 }}><I name="search" size={12} /><span>标题 / 商品 ID / Skill</span></div>
            <div className="ai-input placeholder"><span>类型</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>优先级</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>2026-05-17 – 24</span></div>
            <div style={{ flex: 1 }} />
            <span className="ai-pill" style={{ background: 'var(--ink-1)', color: 'white', borderColor: 'var(--ink-1)' }}>待处理 56</span>
            <span className="ai-pill">派给我 12</span>
            <span className="ai-pill">即将逾期 4</span>
          </div>
          {/* Compact row-style cards (not loud red+green) */}
          <div className="ai-card" style={{ overflow: 'hidden' }}>
            <div className="row" style={{ padding: '8px 14px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)', fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--ink-4)', gap: 10 }}>
              <input type="checkbox" disabled style={{ accentColor: 'var(--accent)' }} />
              <span style={{ width: 36 }}>优先</span>
              <span style={{ flex: 1 }}>待办</span>
              <span style={{ width: 110 }}>关键指标</span>
              <span style={{ width: 130 }}>来源 Skill</span>
              <span style={{ width: 80 }}>SLA</span>
              <span style={{ width: 200, textAlign: 'right' }}>操作</span>
            </div>
            {items.map((t, i) => (
              <div key={t.id} className="row" style={{ padding: '14px 14px', borderBottom: i === items.length - 1 ? '0' : '1px solid var(--border)', gap: 10 }}>
                <input type="checkbox" disabled style={{ accentColor: 'var(--accent)' }} />
                <div style={{ width: 36 }}>
                  <span className={'ai-pill ' + (t.prio === 'P1' ? 'bad' : t.prio === 'P2' ? 'warn' : '')} style={{ fontSize: 10.5 }}>{t.prio}</span>
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="row" style={{ gap: 6, marginBottom: 3 }}>
                    <span className="ai-pill" style={{ fontSize: 10.5 }}>{t.kind}</span>
                    <span className="ai-pill accent" style={{ fontSize: 10.5 }}>派发</span>
                  </div>
                  <div style={{ fontSize: 13, fontWeight: 500, lineHeight: 1.4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--ink-1)' }}>{t.title}</div>
                  <div className="row" style={{ gap: 8, marginTop: 3 }}>
                    <span className="mono-id">{t.sku}</span>
                    <span className="tiny muted">·</span>
                    <span className="tiny muted">{t.when}</span>
                  </div>
                </div>
                <div style={{ width: 110 }}>
                  <div className="row" style={{ gap: 6 }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 600, color: t.scoreState === 'bad' ? 'var(--bad)' : 'var(--warn)' }}>{t.score.toFixed(4)}</span>
                    <span className="tiny muted">下滑</span>
                  </div>
                  <div className="row" style={{ gap: 5, marginTop: 3 }}>
                    {t.metrics.slice(0, 2).map((m, j) => (
                      <span key={j} className="tiny muted">{m.k}{m.v ? ' ' + m.v : ''}</span>
                    ))}
                  </div>
                </div>
                <div style={{ width: 130 }}>
                  <div className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.skill}</div>
                </div>
                <div style={{ width: 80 }} className="tiny muted">剩 {t.sla}</div>
                <div className="row" style={{ width: 200, justifyContent: 'flex-end', gap: 6 }}>
                  <button className="ai-btn sm">驳回</button>
                  <button className="ai-btn sm">详情</button>
                  <button className="ai-btn primary sm">通过并派发</button>
                </div>
              </div>
            ))}
          </div>

          {/* Footer hint */}
          <div className="row" style={{ marginTop: 14, justifyContent: 'space-between', color: 'var(--ink-4)', fontSize: 12 }}>
            <span>选择多个待办可批量「通过并派发」给同一负责人</span>
            <span className="row" style={{ gap: 8 }}>
              <span>共 56 条 · 显示 4 / 56</span>
              <span className="row" style={{ gap: 2 }}>
                <button className="ai-iconbtn"><I name="arrowl" size={12} /></button>
                <span style={{ padding: '0 8px' }}>1 / 14</span>
                <button className="ai-iconbtn"><I name="arrowr" size={12} /></button>
              </span>
            </span>
          </div>
        </div>
      </div>
    </Screen>
  );
}

window.Inbox = Inbox;
