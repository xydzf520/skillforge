// Reports — list tab + detail page.

const REPORTS = [
  { id: 'r1', tag: '钉钉 Markdown', kind: 'node_scheduler', date: '2026-05-24 08:47',
    title: '2026-05-24 店铺链接下滑诊断日报',
    summary: '扫描全店 117 个商品，分析池 78 个异常，生成 78 张商品诊断卡、467 条整改建议，发现 474 个数据缺口。',
    skill: '天猫店铺链接下滑分析', dept: '示例品牌传统电商运营部', open: 36, total: 36, hot: ['#降级', '#多账号', '#数据过期'] },
  { id: 'r2', tag: '钉钉 Markdown', kind: 'node_scheduler', date: '2026-05-24 08:36',
    title: '2026-05-23 客服对话用户需求分析',
    summary: '识别 7 类用户需求，5 类购买阻碍。会话 42,975 · 用户消息 457,266 · 解析记录 42,975。',
    skill: 'yuyidata-daily-user-need-a…', dept: '客服部', open: 1, total: 1, hot: ['#客服', '#用户需求', '#yuyidata', '#mcp'],
    extra: [{ k: '会话数', v: '42,975' }, { k: '用户消息', v: '457,266' }, { k: '需求类目', v: '7' }, { k: '购买阻碍', v: '5' }, { k: '数据健康', v: '100%', ok: true }] },
  { id: 'r3', tag: '钉钉 Markdown', kind: 'manual:tmp_admin_rerun', date: '2026-05-24 06:42',
    title: '2026-05-24 店铺链接下滑诊断日报',
    summary: '扫描全店 115 个商品，生成 75 张商品诊断卡、456 条整改建议，发现 506 个数据缺口。',
    skill: '天猫店铺链接下滑分析', dept: '示例品牌传统电商运营部', open: 19, total: 19, hot: [] },
  { id: 'r4', tag: '钉钉 Markdown', kind: 'manual:tmp_admin_rerun', date: '2026-05-24 05:39',
    title: '2026-05-24 店铺链接下滑诊断日报',
    summary: '扫描全店 114 个商品，生成 72 张商品诊断卡、437 条整改建议，发现 488 个数据缺口。',
    skill: '天猫店铺链接下滑分析', dept: '示例品牌传统电商运营部', open: 18, total: 0, hot: [] },
  { id: 'r5', tag: '钉钉 Markdown', kind: 'manual', date: '2026-05-23 19:12',
    title: '仓储库存周转复盘 · 第 21 周',
    summary: '识别 12 个超过 60 天周转 SKU · 提出 3 项打包清仓建议',
    skill: 'inventory-rotation', dept: '仓储物流部', open: 4, total: 9, hot: ['#库存', '#滞销'] },
  { id: 'r6', tag: 'PDF', kind: 'workflow', date: '2026-05-23 11:00',
    title: '供应链异常巡检 · 周报',
    summary: '识别 8 个 SKU 出现到货延误，预计影响后 7 天发货 · 给出补货建议',
    skill: 'supplier-anomaly', dept: '供应链部', open: 2, total: 5, hot: ['#供应链'] },
  { id: 'r7', tag: '钉钉 Markdown', kind: 'cron', date: '2026-05-23 09:30',
    title: '销售一部 · 周经营报告',
    summary: '上周成交 ¥820 万 · 完成率 92% · 新增商机 47 条 · TOP 客户 3 家',
    skill: 'sales-weekly', dept: '销售一部', open: 0, total: 3, hot: [] },
  { id: 'r8', tag: '钉钉 Markdown', kind: 'cron', date: '2026-05-23 08:30',
    title: '抖音评论摘要 · 主品 SKU',
    summary: '汇总 1,247 条评论 · 7 类观点 · 主要正向「持久」「轻薄」 · 负面「漏液」3 例',
    skill: 'douyin-review-distill', dept: '市场部', open: 1, total: 1, hot: ['#抖音', '#评论'] },
];

function ReportsList() {
  return (
    <Screen role="admin" active="inbox">
      <div className="ai-sidebar" style={{ width: 200, flex: '0 0 200px' }}>
        <div className="ai-side-group">
          <div className="ai-side-label">视图</div>
          <div className="ai-side-item"><I name="inbox" size={13} className="ic" /><span>所有待办</span><span className="count">56</span></div>
          <div className="ai-side-item active"><I name="doc" size={13} className="ic" /><span>报告</span><span className="count">8</span></div>
          <div className="ai-side-item"><I name="trend" size={13} className="ic" /><span>Drift / 漂移</span><span className="count">3</span></div>
        </div>
        <div className="ai-side-group">
          <div className="ai-side-label">来源 Skill</div>
          {[
            { n: '店铺下滑诊断', c: 4 },
            { n: '客服需求分析', c: 1 },
            { n: '库存周转',     c: 1 },
            { n: '供应链异常',   c: 1 },
            { n: '销售周报',     c: 1 },
          ].map(s => (
            <div key={s.n} className="ai-side-item"><I name="cube" size={13} className="ic" /><span>{s.n}</span><span className="count">{s.c}</span></div>
          ))}
        </div>
        <div className="ai-side-group">
          <div className="ai-side-label">部门</div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: '#3a5bd9' }} /><span>电商运营</span><span className="count">3</span></div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: '#9a6b13' }} /><span>客服</span><span className="count">1</span></div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: '#1b5e8a' }} /><span>仓储/供应链</span><span className="count">2</span></div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: '#6a4a2a' }} /><span>市场/销售</span><span className="count">2</span></div>
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
            <button className="ai-btn"><I name="filter" size={12} /> 订阅设置</button>
            <button className="ai-btn"><I name="download" size={12} /> 导出</button>
          </div>
        </div>

        <div className="ai-tabs">
          <div className="ai-tab">待办 <span className="ai-pill bad" style={{ marginLeft: 4, fontSize: 10, height: 16, padding: '0 5px' }}>56</span></div>
          <div className="ai-tab active">报告 <span className="ai-pill" style={{ marginLeft: 4, fontSize: 10, height: 16, padding: '0 5px' }}>8</span></div>
          <div className="ai-tab">Drift <span className="ai-pill warn" style={{ marginLeft: 4, fontSize: 10, height: 16, padding: '0 5px' }}>3</span></div>
        </div>

        <div className="ai-pagebody" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="row" style={{ gap: 8 }}>
            <div className="ai-input placeholder" style={{ width: 240 }}><span>Skill ID</span></div>
            <div className="ai-input placeholder"><span>标签</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>渠道</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>触发方式</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>2026-05-17 – 24</span></div>
            <div style={{ flex: 1 }} />
            <button className="ai-btn primary sm">筛选</button>
            <button className="ai-btn sm">重置</button>
          </div>

          <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
            <span className="ai-pill" style={{ background: 'var(--ink-1)', color: 'white', borderColor: 'var(--ink-1)' }}>全部 8</span>
            <span className="ai-pill">日报 5</span>
            <span className="ai-pill">周报 2</span>
            <span className="ai-pill">#降级 3</span>
            <span className="ai-pill">#多账号 1</span>
            <span className="ai-pill">#数据过期 2</span>
            <span className="ai-pill">#客服 1</span>
            <span className="ai-pill">#mcp 1</span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
            {REPORTS.map(f => (
              <div key={f.id} className="ai-card" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div className="row" style={{ justifyContent: 'space-between' }}>
                  <span className="ai-pill" style={{ fontSize: 10.5 }}>{f.tag}</span>
                  <span className="ai-pill mono" style={{ fontSize: 10, height: 17, padding: '0 5px' }}>{f.kind}</span>
                </div>
                <span className="tiny muted">{f.date}</span>
                <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: '-0.01em', lineHeight: 1.35, minHeight: 38 }}>{f.title}</div>
                <div className="muted" style={{ fontSize: 12, lineHeight: 1.5, overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical' }}>{f.summary}</div>
                {f.extra && (
                  <div className="row" style={{ gap: 4, flexWrap: 'wrap' }}>
                    {f.extra.slice(0, 3).map(e => (
                      <span key={e.k} className={'ai-pill ' + (e.ok ? 'ok' : '')} style={{ fontSize: 10 }}>{e.k} {e.v}</span>
                    ))}
                  </div>
                )}
                {f.hot.length > 0 && (
                  <div className="row" style={{ gap: 4, flexWrap: 'wrap' }}>
                    {f.hot.slice(0, 3).map(h => <span key={h} className="ai-pill accent" style={{ fontSize: 10 }}>{h}</span>)}
                  </div>
                )}
                <div className="row" style={{ marginTop: 'auto', paddingTop: 8, borderTop: '1px dashed var(--border)', gap: 6 }}>
                  <div className="col" style={{ gap: 0, flex: 1, minWidth: 0 }}>
                    <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.skill}</span>
                    <span className="tiny muted">{f.dept}</span>
                  </div>
                  <span className="tiny" style={{ color: f.open === 0 ? 'var(--ok)' : 'var(--warn)' }}>{f.open}/{f.total} 待办</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Screen>
  );
}

// Report Detail — the big "店铺链接下滑诊断日报" page
function ReportDetail() {
  const overviewStats = [
    { k: '商品数',      v: '117' },
    { k: '全店访客',    v: '18,800' },
    { k: '成交金额',    v: '¥105,707.95' },
    { k: '支付件数',    v: '1,199' },
    { k: '全店转化率',  v: '6.4%' },
    { k: '转化中位数',  v: '6.7%' },
    { k: '转化均值',    v: '8.9%' },
    { k: '访客中位数',  v: '36' },
  ];
  const reasoning = [
    '支付金额 ±10% 分析池 78 个商品中，0 个免费转化率下滑超过 10%',
    '支付金额 ±10% 分析池 78 个商品中，0 个免费访客下滑超过 10%',
    '付费端 0 个商品缺少计划 / 关键词 / 创意级明细',
    '商品级活动或 SKU 到手价 78 个商品待核对',
    '竞品来源渠道缺口 78 个，竞品活动缺口 78 个',
  ];

  return (
    <Screen role="admin" active="inbox" height={1100}>
      <div className="ai-main">
        {/* Back bar */}
        <div className="row" style={{ height: 44, borderBottom: '1px solid var(--border)', background: 'var(--surface)', padding: '0 24px', gap: 10 }}>
          <button className="ai-btn sm"><I name="arrowl" size={11} /> 返回</button>
          <span className="tiny muted">/ 收件 / 报告 /</span>
          <span style={{ fontSize: 12.5, fontWeight: 500 }}>2026-05-24 店铺链接下滑诊断日报</span>
          <div style={{ flex: 1 }} />
          <button className="ai-btn sm"><I name="download" size={11} /> 导出</button>
          <button className="ai-btn sm"><I name="link" size={11} /> 分享</button>
          <button className="ai-btn primary sm">查看全部 36 个待办</button>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: 28, background: 'var(--bg)' }}>
          {/* Hero card */}
          <div style={{ background: '#1f2840', color: 'white', borderRadius: 12, padding: 28, marginBottom: 18 }}>
            <div className="row" style={{ gap: 8, marginBottom: 14, opacity: 0.75 }}>
              <span style={{ padding: '2px 6px', borderRadius: 3, background: 'rgba(255,255,255,0.1)', fontSize: 10.5, letterSpacing: '0.05em' }}>钉钉 MARKDOWN</span>
              <span style={{ padding: '2px 6px', borderRadius: 3, background: 'rgba(255,255,255,0.1)', fontSize: 10.5, letterSpacing: '0.05em' }}>NODE_SCHEDULER</span>
              <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.65)' }}>示例品牌传统电商运营部</span>
              <span style={{ marginLeft: 'auto', fontSize: 11, color: 'rgba(255,255,255,0.5)' }}>生成时间 2026-05-24 08:47</span>
            </div>
            <h2 style={{ fontSize: 28, fontWeight: 600, letterSpacing: '-0.02em', margin: 0 }}>2026-05-24 店铺链接下滑诊断日报</h2>
            <p style={{ fontSize: 14, color: 'rgba(255,255,255,0.75)', marginTop: 10, maxWidth: 920, lineHeight: 1.55 }}>
              扫描全店 117 个商品，支付金额 ±10% 分析池中 78 个异常商品，生成 78 张商品诊断卡、467 条整改建议，发现 474 个数据缺口。
            </p>
            <div className="row" style={{ marginTop: 12, gap: 14, opacity: 0.7, fontSize: 11.5 }}>
              <span>天猫店铺链接下滑分析</span>
              <span>运行 ID <span className="mono">rm-9e2c098f</span></span>
              <span>36 个关联待办，其中 36 个未决请求</span>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14, marginTop: 24 }}>
              {[
                { l: '扫描商品',  v: '117',         s: '全店覆盖规模' },
                { l: '成交金额',  v: '¥105,707.95', s: '当日成交' },
                { l: '全店转化',  v: '6.4%',        s: '支付转化率' },
                { l: '数据缺口',  v: '474',         s: '仍待补采', warn: true },
              ].map((s, i) => (
                <div key={s.l} style={{ background: 'rgba(255,255,255,0.05)', borderRadius: 8, padding: 16 }}>
                  <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.55)', letterSpacing: '0.04em', textTransform: 'uppercase' }}>{s.l}</div>
                  <div style={{ fontSize: 28, fontWeight: 600, letterSpacing: '-0.02em', marginTop: 4, color: s.warn ? '#fbbf24' : 'white' }}>{s.v}</div>
                  <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.5)', marginTop: 2 }}>{s.s}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Two-column body */}
          <div style={{ display: 'grid', gridTemplateColumns: '1.7fr 1fr', gap: 18 }}>
            <div>
              <div className="ai-card" style={{ padding: 24, marginBottom: 16 }}>
                <div style={{ fontSize: 13, color: 'var(--ink-3)', letterSpacing: '0.04em', textTransform: 'uppercase', marginBottom: 12 }}>今日主线判断</div>
                <div className="row" style={{ gap: 12, alignItems: 'flex-start', marginBottom: 8 }}>
                  <div style={{ fontSize: 32, fontWeight: 700, letterSpacing: '-0.025em', color: 'var(--warn)' }}>数据不足</div>
                  <span className="ai-pill accent" style={{ marginTop: 12 }}>规则判断</span>
                  <span className="tiny" style={{ marginTop: 12, color: 'var(--warn)' }}>数据不足为主，付费投放问题为辅，置信度低</span>
                </div>
                <div className="tiny muted" style={{ marginBottom: 14 }}>次因 付费投放问题 · 置信度 低</div>

                <div style={{ background: 'var(--surface-2)', borderRadius: 8, padding: 16, marginBottom: 16 }}>
                  <span style={{ fontWeight: 600 }}>今日先补齐关键数据，禁止直接删词、改价、降预算；补采完成后再派发强运营动作。</span>
                </div>

                <div className="tiny muted" style={{ marginBottom: 8 }}>为什么这样判断</div>
                <ul style={{ margin: 0, paddingLeft: 16, fontSize: 13, lineHeight: 1.8, color: 'var(--ink-2)' }}>
                  {reasoning.map(r => <li key={r}>{r}</li>)}
                </ul>
                <div style={{ marginTop: 14, padding: '10px 12px', background: 'var(--warn-soft)', borderRadius: 6, color: 'var(--warn)', fontSize: 12.5 }}>
                  <I name="warn" size={11} /> 当前仍有 474 个数据缺口，预期数据补全后再下结论。
                </div>
              </div>

              <div className="ai-card" style={{ padding: 0 }}>
                <div className="ai-card-h">
                  <span className="t">商品诊断卡 · 派发 36 件</span>
                  <span className="s">P1 · 实时下滑系数 ≥ 0.7</span>
                  <div className="row" style={{ marginLeft: 'auto', gap: 6 }}>
                    <button className="ai-btn sm">全部</button>
                    <button className="ai-btn sm">仅 P1</button>
                  </div>
                </div>
                <table className="ai-table">
                  <thead>
                    <tr>
                      <th>商品</th>
                      <th style={{ width: 80 }}>商品 ID</th>
                      <th style={{ width: 90 }}>下滑系数</th>
                      <th style={{ width: 80 }}>访客排名</th>
                      <th style={{ width: 80 }}>支付排名</th>
                      <th style={{ width: 100 }}>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      { n: '【热感因子】示例品牌热感人体润滑液',     s: 0.7723, va: '#57', pa: '#75', st: 'P1' },
                      { n: '【超薄持久3只】示例品牌避孕套持久装',   s: 0.7333, va: '#85', pa: '#68', st: 'P1' },
                      { n: '【超薄001套体验】黑金 001 男用超薄',  s: 0.7003, va: '#54', pa: '#65', st: 'P1' },
                      { n: '【双头跳蛋】震动跳蛋女性跳蛋',         s: 0.5000, va: '#92', pa: '—',  st: 'P3' },
                    ].map((p, i) => (
                      <tr key={i}>
                        <td>{p.n}</td>
                        <td className="mono-id">8004{i + 1}1287</td>
                        <td><span className="mono" style={{ color: 'var(--bad)', fontWeight: 600 }}>{p.s.toFixed(4)}</span></td>
                        <td className="mono" style={{ fontSize: 12 }}>{p.va}</td>
                        <td className="mono" style={{ fontSize: 12 }}>{p.pa}</td>
                        <td><span className={'ai-pill ' + (p.st === 'P1' ? 'bad' : p.st === 'P2' ? 'warn' : '')} style={{ fontSize: 10.5 }}>{p.st}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div>
              <div className="ai-card" style={{ padding: 0 }}>
                <div className="ai-card-h"><span className="t">全店概览</span></div>
                <div>
                  {overviewStats.map((s, i, arr) => (
                    <div key={s.k} className="row" style={{ padding: '11px 16px', borderBottom: i === arr.length - 1 ? '0' : '1px solid var(--border)', gap: 12 }}>
                      <span style={{ flex: 1, fontSize: 12.5, color: 'var(--ink-3)' }}>{s.k}</span>
                      <span className="mono" style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink-1)' }}>{s.v}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="ai-card" style={{ padding: 16, marginTop: 16 }}>
                <div className="tiny muted" style={{ marginBottom: 8 }}>派发追踪</div>
                <div className="row" style={{ alignItems: 'flex-end', gap: 4, height: 40 }}>
                  {[12, 18, 24, 22, 28, 36, 36].map((h, i) => (
                    <div key={i} style={{ flex: 1, background: i === 6 ? 'var(--accent)' : 'var(--ink-5)', height: h + 'px', borderRadius: 2 }} />
                  ))}
                </div>
                <div className="row" style={{ marginTop: 6, fontSize: 10, color: 'var(--ink-4)' }}>
                  <span>5-18</span><span style={{ marginLeft: 'auto' }}>5-24</span>
                </div>
                <div className="tiny muted" style={{ marginTop: 10 }}>近 7 日累计派发 176 件 · 完成 92 件 · 已驳回 14 件</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Screen>
  );
}

Object.assign(window, { ReportsList, ReportDetail });