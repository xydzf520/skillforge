// L3 — third-level pages (drawers / detail panes) for the most important flows.

// ============ L3-A · 运行抽屉 ============
// 从能力大厅点击「运行」→ 右侧抽屉打开 (覆盖左半屏)
function RunDrawer() {
  const steps = [
    { id: 'fetch',   name: '拉取店铺数据',     state: 'done', t: '0:12', meta: '从天猫数据银行抽取 117 条' },
    { id: 'clean',   name: '清洗与归一化',     state: 'done', t: '0:23', meta: '剔除停售 0 条 / 合并多 SKU' },
    { id: 'analyze', name: '识别下滑商品',     state: 'doing', t: '1:08', meta: '78 / 117 商品已分析' },
    { id: 'card',    name: '生成商品诊断卡',   state: 'queued', t: '—', meta: '等待中' },
    { id: 'push',    name: '派发到收件中心',   state: 'queued', t: '—', meta: '等待中' },
  ];
  return (
    <Screen role="employee" active="home">
      {/* dimmed underlay (the home page behind) */}
      <div className="ai-sidebar" style={{ opacity: 0.45 }}>
        <div className="ai-side-group">
          <div className="ai-side-label">我的</div>
          <div className="ai-side-item active"><I name="spark" size={13} className="ic" /><span>为你推荐</span><span className="count">14</span></div>
          <div className="ai-side-item"><I name="clock" size={13} className="ic" /><span>最近用过</span><span className="count">8</span></div>
        </div>
      </div>
      <div className="ai-main" style={{ position: 'relative' }}>
        {/* dimmed background page */}
        <div style={{ opacity: 0.35, pointerEvents: 'none', filter: 'saturate(0.6)' }}>
          <div className="ai-pagehead">
            <div>
              <div className="ai-crumbs">能力大厅 · 全员入口</div>
              <h1 className="ai-title">能力大厅</h1>
            </div>
          </div>
          <div className="ai-pagebody" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="ai-card" style={{ padding: 14, height: 160 }}>
                <div style={{ width: 30, height: 30, borderRadius: 7, background: 'var(--surface-2)' }} />
                <div style={{ width: '80%', height: 12, background: 'var(--surface-2)', marginTop: 12, borderRadius: 3 }} />
                <div style={{ width: '60%', height: 8, background: 'var(--surface-3)', marginTop: 10, borderRadius: 3 }} />
              </div>
            ))}
          </div>
        </div>

        {/* scrim */}
        <div style={{ position: 'absolute', inset: 0, background: 'rgba(20,19,15,0.34)' }} />

        {/* drawer */}
        <div style={{ position: 'absolute', top: 0, right: 0, bottom: 0, width: 720, background: 'var(--surface)', borderLeft: '1px solid var(--border)', boxShadow: '-12px 0 32px -8px rgba(20,19,15,0.18)', display: 'flex', flexDirection: 'column' }}>
          {/* head */}
          <div className="row" style={{ padding: '16px 24px', borderBottom: '1px solid var(--border)', gap: 12 }}>
            <div style={{ width: 38, height: 38, borderRadius: 9, background: 'var(--surface-2)', display: 'grid', placeItems: 'center' }}>
              <I name="trend" size={17} />
            </div>
            <div style={{ flex: 1 }}>
              <div className="row" style={{ gap: 6 }}>
                <span style={{ fontSize: 17, fontWeight: 600, letterSpacing: '-0.015em' }}>天猫店铺链接下滑分析</span>
                <span className="ai-pill" style={{ fontSize: 10.5 }}>电商运营</span>
                <span className="ai-pill" style={{ fontSize: 10.5 }}>R1</span>
              </div>
              <div className="tiny muted" style={{ marginTop: 3 }}>tmall-link-decline · v0.4 · 运行 ID rm-9e2c098f</div>
            </div>
            <button className="ai-iconbtn"><I name="x" size={14} /></button>
          </div>

          {/* tabs */}
          <div className="ai-tabs" style={{ padding: '0 24px' }}>
            <div className="ai-tab">参数</div>
            <div className="ai-tab active">运行中 <span className="ai-pill" style={{ marginLeft: 4, fontSize: 10, height: 16, padding: '0 5px', background: 'var(--ok-soft)', color: 'var(--ok)', borderColor: 'transparent' }}>1:08</span></div>
            <div className="ai-tab">日志</div>
            <div className="ai-tab">结果</div>
          </div>

          <div style={{ flex: 1, overflow: 'auto', padding: 24, background: 'var(--bg)' }}>
            {/* params summary */}
            <div className="ai-card" style={{ padding: '12px 16px', marginBottom: 16 }}>
              <div className="row" style={{ gap: 16, fontSize: 12, color: 'var(--ink-3)' }}>
                <span><span className="muted">店铺</span> 示例品牌天猫旗舰店</span>
                <span><span className="muted">时间窗</span> 近 7 日</span>
                <span><span className="muted">下滑阈值</span> 0.5</span>
                <span><span className="muted">派发</span> 自动 + 仅 P1/P2</span>
                <span style={{ marginLeft: 'auto' }} className="link">修改参数</span>
              </div>
            </div>

            {/* progress */}
            <div className="ai-card" style={{ padding: 0, marginBottom: 16 }}>
              <div className="ai-card-h">
                <span className="t">执行进度</span>
                <span className="s">2 / 5 步 · 预计还需 0:54</span>
                <span className="row" style={{ marginLeft: 'auto', gap: 6 }}>
                  <button className="ai-btn sm">暂停</button>
                  <button className="ai-btn sm" style={{ color: 'var(--bad)' }}>中止</button>
                </span>
              </div>
              <div style={{ height: 4, background: 'var(--surface-3)', overflow: 'hidden' }}>
                <div style={{ width: '46%', height: '100%', background: 'var(--accent)', transition: 'width 1s' }} />
              </div>
              <div>
                {steps.map((s, i, arr) => (
                  <div key={s.id} className="row" style={{ padding: '12px 16px', borderBottom: i === arr.length - 1 ? '0' : '1px solid var(--border)', gap: 12 }}>
                    <span style={{ width: 22, height: 22, borderRadius: '50%', display: 'grid', placeItems: 'center',
                      background: s.state === 'done' ? 'var(--ok)' : s.state === 'doing' ? 'var(--accent)' : 'var(--surface-3)',
                      color: 'white', fontSize: 11, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>
                      {s.state === 'done' ? '✓' : i + 1}
                    </span>
                    <div className="col" style={{ flex: 1, gap: 2 }}>
                      <span style={{ fontSize: 13, fontWeight: 500 }}>{s.name}</span>
                      <span className="tiny muted">{s.meta}</span>
                    </div>
                    {s.state === 'doing' && (
                      <div style={{ width: 80, height: 4, background: 'var(--surface-3)', borderRadius: 2, overflow: 'hidden' }}>
                        <div style={{ width: '67%', height: '100%', background: 'var(--accent)' }} />
                      </div>
                    )}
                    <span className="mono tiny" style={{ color: 'var(--ink-3)', width: 50, textAlign: 'right' }}>{s.t}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* live log preview */}
            <div className="ai-card" style={{ padding: 0 }}>
              <div className="ai-card-h">
                <span className="t">实时日志 (近 20 行)</span>
                <span className="row" style={{ marginLeft: 'auto', gap: 6 }}>
                  <span className="ai-pill ok dot" style={{ fontSize: 10.5 }}>跟随</span>
                  <button className="ai-btn sm"><I name="download" size={11} /> 导出</button>
                </span>
              </div>
              <div style={{ padding: 14, background: '#13130f', color: '#c0bbae', fontFamily: 'var(--font-mono)', fontSize: 11.5, lineHeight: 1.7, maxHeight: 200, overflow: 'auto', borderRadius: '0 0 8px 8px' }}>
                <div><span style={{ color: '#6c685e' }}>10:50:32</span> [INFO] connect tmall.databank ok · 12ms</div>
                <div><span style={{ color: '#6c685e' }}>10:50:33</span> [INFO] fetch shop_metrics range=7d · 117 items</div>
                <div><span style={{ color: '#6c685e' }}>10:50:45</span> [INFO] clean: dedup 0 · normalize sku 117</div>
                <div><span style={{ color: '#6c685e' }}>10:50:55</span> [INFO] analyze: scan 117 · candidate 92</div>
                <div><span style={{ color: '#6c685e' }}>10:51:08</span> [INFO] analyze: scored 78 / 117 (66.7%)</div>
                <div><span style={{ color: '#fbbf24' }}>10:51:09</span> [WARN] sku 800687205629 缺少历史 14d 数据 · 跳过</div>
                <div><span style={{ color: '#6c685e' }}>10:51:12</span> [INFO] analyze: P1=8 · P2=18 · P3=52 (running…)</div>
                <div style={{ color: '#e4e1da' }}>▍</div>
              </div>
            </div>
          </div>

          {/* footer */}
          <div className="row" style={{ padding: '12px 24px', borderTop: '1px solid var(--border)', background: 'var(--surface)', gap: 8 }}>
            <span className="tiny muted">运行完成后会推送钉钉 · 同时在收件中心生成 36 条待办</span>
            <div style={{ flex: 1 }} />
            <button className="ai-btn sm">最小化</button>
            <button className="ai-btn sm">在后台运行</button>
          </div>
        </div>
      </div>
    </Screen>
  );
}

// ============ L3-B · 待办详情 ============
// 从收件点击待办行 → 全屏处理详情 (商品诊断卡)
function TodoDetail() {
  return (
    <Screen role="admin" active="inbox" height={1100}>
      <div className="ai-main">
        {/* breadcrumb back row */}
        <div className="row" style={{ height: 44, borderBottom: '1px solid var(--border)', background: 'var(--surface)', padding: '0 24px', gap: 10 }}>
          <button className="ai-btn sm"><I name="arrowl" size={11} /> 返回</button>
          <span className="tiny muted">/ 收件 / 待办 /</span>
          <span style={{ fontSize: 12.5, fontWeight: 500 }}>商品诊断卡 #DC-2026-05-24-007</span>
          <span className="ai-pill bad">P1 · 实时下滑系数 0.7723</span>
          <span className="ai-pill warn">SLA 剩 47h 44m</span>
          <div style={{ flex: 1 }} />
          <button className="ai-btn sm">上一条</button>
          <button className="ai-btn sm">下一条 →</button>
          <span className="tiny muted">7 / 36</span>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: 24, background: 'var(--bg)' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 18 }}>
            {/* LEFT */}
            <div className="col" style={{ gap: 16 }}>
              {/* product hero */}
              <div className="ai-card" style={{ padding: 20 }}>
                <div className="row" style={{ gap: 16, alignItems: 'flex-start' }}>
                  <div style={{ width: 88, height: 88, borderRadius: 10, background: 'repeating-linear-gradient(45deg, #dccfb6 0 8px, #d6cdb8 8px 16px)', flex: '0 0 88px' }} />
                  <div style={{ flex: 1 }}>
                    <div className="row" style={{ gap: 6, marginBottom: 6 }}>
                      <span className="ai-pill" style={{ fontSize: 10.5 }}>商品诊断</span>
                      <span className="ai-pill bad">P1</span>
                    </div>
                    <div style={{ fontSize: 17, fontWeight: 600, letterSpacing: '-0.015em', lineHeight: 1.35 }}>【热感因子】示例品牌热感人体润滑液夫妻情趣用品缓解保湿润滑油剂</div>
                    <div className="row" style={{ gap: 14, marginTop: 8, fontSize: 12, color: 'var(--ink-3)' }}>
                      <span><span className="muted">SKU</span> <span className="mono">888456887276</span></span>
                      <span><span className="muted">店铺</span> 示例品牌天猫旗舰店</span>
                      <span><span className="muted">类目</span> 计生用品</span>
                      <span><span className="muted">上架</span> 2024-08-12</span>
                    </div>
                  </div>
                </div>

                {/* metrics grid */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 0, marginTop: 16, borderTop: '1px solid var(--border)', paddingTop: 14 }}>
                  {[
                    { l: '实时下滑',     v: '0.7723', s: '阈值 0.50', c: 'var(--bad)' },
                    { l: '7 日访客',     v: '342',    s: '昨日 −24%', c: 'var(--bad)' },
                    { l: '7 日支付',     v: '18',     s: '昨日 −38%', c: 'var(--bad)' },
                    { l: '客单',         v: '¥89.5',  s: '持平',     c: 'var(--ink-1)' },
                  ].map((s, i) => (
                    <div key={s.l} style={{ borderRight: i < 3 ? '1px solid var(--border)' : 0, padding: '0 14px' }}>
                      <div className="tiny muted">{s.l}</div>
                      <div className="mono" style={{ fontSize: 22, fontWeight: 600, color: s.c, letterSpacing: '-0.02em' }}>{s.v}</div>
                      <div className="tiny muted">{s.s}</div>
                    </div>
                  ))}
                </div>
              </div>

              {/* trend chart */}
              <div className="ai-card" style={{ padding: 16 }}>
                <div className="row" style={{ marginBottom: 12 }}>
                  <span style={{ fontSize: 13, fontWeight: 600 }}>关键指标 · 近 14 日</span>
                  <div className="row" style={{ marginLeft: 'auto', gap: 4 }}>
                    <span className="ai-pill" style={{ background: 'var(--ink-1)', color: 'white', borderColor: 'var(--ink-1)' }}>访客</span>
                    <span className="ai-pill">支付</span>
                    <span className="ai-pill">转化</span>
                  </div>
                </div>
                <svg width="100%" height="160" viewBox="0 0 600 160" preserveAspectRatio="none">
                  <line x1="0" y1="158" x2="600" y2="158" stroke="var(--border)" />
                  <line x1="0" y1="80"  x2="600" y2="80"  stroke="var(--border)" strokeDasharray="3 4" />
                  <line x1="0" y1="40"  x2="600" y2="40"  stroke="var(--border)" strokeDasharray="3 4" />
                  <polyline points="0,30 43,38 86,32 129,36 172,28 215,40 258,55 301,60 344,70 387,92 430,115 473,118 516,134 559,140 600,148" fill="none" stroke="var(--accent)" strokeWidth="2" />
                  {/* dots for last few */}
                  {[{x:430,y:115},{x:473,y:118},{x:516,y:134},{x:559,y:140},{x:600,y:148}].map((p,i)=>(<circle key={i} cx={p.x} cy={p.y} r="3" fill="var(--accent)" />))}
                  <rect x="345" y="0" width="255" height="158" fill="var(--bad)" opacity="0.06" />
                  <text x="350" y="14" fontSize="10" fill="var(--bad)">下滑窗口</text>
                </svg>
                <div className="row" style={{ marginTop: 6, fontSize: 10, color: 'var(--ink-4)' }}>
                  <span>5-11</span><span style={{ marginLeft: 'auto' }}>5-15</span><span style={{ marginLeft: 'auto' }}>5-18</span><span style={{ marginLeft: 'auto' }}>5-21</span><span style={{ marginLeft: 'auto' }}>5-24</span>
                </div>
              </div>

              {/* AI reasoning */}
              <div className="ai-card" style={{ padding: 0 }}>
                <div className="ai-card-h"><span className="t">AI 诊断推理</span><span className="s">基于 4 个数据源 · 置信度 中</span></div>
                <div style={{ padding: '14px 16px' }}>
                  <div style={{ background: 'var(--surface-2)', padding: '10px 12px', borderRadius: 6, fontSize: 13, lineHeight: 1.55, marginBottom: 12 }}>
                    <b>主因</b>：付费投放 · 计划「618 蓄水-PC1」昨日 22:00 暂停，导致访客回落。 <b>次因</b>：竞品「示例品牌B冰感」5-22 起 -15% 促销引流。
                  </div>
                  <div className="tiny muted" style={{ marginBottom: 8 }}>建议动作 · 按优先级</div>
                  <div className="col" style={{ gap: 6 }}>
                    {[
                      { p: 1, t: '恢复 "618 蓄水-PC1" 计划，预算可降至 70%', risk: '中' },
                      { p: 2, t: '增加冰感 / 热感 对比直播片段在主图',         risk: '低' },
                      { p: 3, t: '调整推广人群至 "竞品观看 7 日"',           risk: '中' },
                      { p: 4, t: '价格活动暂不调整，等待数据回升验证',         risk: '—' },
                    ].map(a => (
                      <div key={a.p} className="row" style={{ padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 6, gap: 10 }}>
                        <span className="mono" style={{ width: 18, fontSize: 11, color: 'var(--ink-4)' }}>0{a.p}</span>
                        <span style={{ flex: 1, fontSize: 12.5 }}>{a.t}</span>
                        <span className="ai-pill" style={{ fontSize: 10.5 }}>风险 {a.risk}</span>
                        <input type="checkbox" defaultChecked={a.p < 3} style={{ accentColor: 'var(--accent)' }} />
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            {/* RIGHT */}
            <div className="col" style={{ gap: 16 }}>
              <div className="ai-card" style={{ padding: 16 }}>
                <div className="tiny muted">来源</div>
                <div className="row" style={{ gap: 8, marginTop: 6 }}>
                  <I name="cube" size={13} stroke="var(--ink-2)" />
                  <span style={{ fontSize: 13, fontWeight: 500 }}>天猫店铺链接下滑分析</span>
                </div>
                <div className="mono-id" style={{ marginTop: 4 }}>tmall-link-decline-v2 · v0.4</div>
                <div className="tiny muted" style={{ marginTop: 14 }}>产出于</div>
                <div className="row" style={{ gap: 8, marginTop: 4, fontSize: 12.5 }}><I name="doc" size={11} /><span>2026-05-24 店铺链接下滑诊断日报</span></div>
                <div className="tiny muted" style={{ marginTop: 14 }}>分配给</div>
                <div className="row" style={{ gap: 8, marginTop: 4 }}>
                  <div className="ai-avatar" style={{ width: 22, height: 22, fontSize: 10 }}>张</div>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>张文</span>
                  <span className="tiny muted">· EC 电商运营 · 主理人</span>
                </div>
              </div>

              <div className="ai-card" style={{ padding: 0 }}>
                <div className="ai-card-h"><span className="t">处理时间线</span></div>
                <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
                  {[
                    { who: 'AI', t: '生成诊断卡 + 4 条建议', when: '10:50', state: 'bot' },
                    { who: 'AI', t: '自动派发给 EC 主理人',     when: '10:50', state: 'bot' },
                    { who: '张文', t: '查看了诊断卡',            when: '15 分前', state: 'user' },
                  ].map((e, i) => (
                    <div key={i} className="row" style={{ gap: 10, alignItems: 'flex-start' }}>
                      <div style={{ width: 22, height: 22, borderRadius: '50%', background: e.state === 'bot' ? 'var(--ink-1)' : 'var(--accent)', display: 'grid', placeItems: 'center', color: 'white', fontSize: 11, fontWeight: 600 }}>{e.who[0]}</div>
                      <div className="col" style={{ flex: 1, gap: 1 }}>
                        <span style={{ fontSize: 12.5 }}><b>{e.who}</b> {e.t}</span>
                        <span className="tiny muted">{e.when}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* decision panel */}
              <div className="ai-card" style={{ padding: 16 }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>处理决定</div>
                <div className="col" style={{ gap: 6 }}>
                  <label className="row" style={{ gap: 8, padding: '8px 10px', border: '1px solid var(--ink-1)', borderRadius: 6, background: 'var(--surface-2)' }}>
                    <input type="radio" defaultChecked style={{ accentColor: 'var(--ink-1)' }} />
                    <div className="col" style={{ flex: 1, gap: 2 }}>
                      <span style={{ fontSize: 12.5, fontWeight: 500 }}>采纳建议 1 + 2，派发执行</span>
                      <span className="tiny muted">将生成 2 个执行子任务并派给运营 · 张文</span>
                    </div>
                  </label>
                  <label className="row" style={{ gap: 8, padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 6 }}>
                    <input type="radio" />
                    <span style={{ fontSize: 12.5 }}>仅记录，本周内复盘</span>
                  </label>
                  <label className="row" style={{ gap: 8, padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 6 }}>
                    <input type="radio" />
                    <span style={{ fontSize: 12.5 }}>驳回 · 数据不可信</span>
                  </label>
                </div>
                <textarea placeholder="补充处理说明 (可选)" style={{ width: '100%', height: 64, padding: 10, fontSize: 12.5, border: '1px solid var(--border)', borderRadius: 6, marginTop: 10, fontFamily: 'inherit', resize: 'none', background: 'var(--surface)' }} />
                <div className="row" style={{ gap: 6, marginTop: 10 }}>
                  <button className="ai-btn sm" style={{ flex: 1, justifyContent: 'center' }}>稍后处理</button>
                  <button className="ai-btn primary sm" style={{ flex: 2, justifyContent: 'center' }}><I name="check" size={11} /> 确认并派发</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Screen>
  );
}

// ============ L3-C · Skill 模块编辑 ============
// 从 Skill 编辑器点击「规则」模块卡 → 全屏表单
function SkillModuleEdit() {
  const rules = [
    { id: 'R-01', cond: '当 下滑系数 ≥ 0.7 且 7 日访客 −20%', then: '标记 P1 · 派发主理人', risk: 'L1', on: true },
    { id: 'R-02', cond: '当 下滑系数 ≥ 0.5 且 < 0.7',           then: '标记 P2 · 仅生成诊断卡',  risk: 'L2', on: true },
    { id: 'R-03', cond: '当 7 日支付件数 = 0 且 价格变化 = 0',   then: '触发数据缺口告警',        risk: 'L1', on: true },
    { id: 'R-04', cond: '当 竞品促销 = true 且 价格差 > 10%',    then: '次因 · 加入推理摘要',     risk: 'L2', on: false },
    { id: 'R-05', cond: '当 商品上架 < 7 日',                     then: '冷启动豁免 · 不评分',     risk: '—',  on: true },
  ];

  return (
    <Screen role="admin" active="skills" height={900}>
      <div className="ai-main" style={{ display: 'flex', flexDirection: 'column' }}>
        <div className="row" style={{ height: 44, borderBottom: '1px solid var(--border)', background: 'var(--surface)', padding: '0 24px', gap: 10 }}>
          <I name="arrowl" size={13} stroke="var(--ink-3)" />
          <span className="tiny muted">Skills / 天猫店铺链接下滑分析 /</span>
          <span style={{ fontSize: 12.5, fontWeight: 500 }}>规则 · 5 条</span>
          <span className="ai-pill warn" style={{ fontSize: 10.5 }}>有未保存</span>
          <div style={{ flex: 1 }} />
          <button className="ai-btn sm">取消</button>
          <button className="ai-btn sm">预览影响</button>
          <button className="ai-btn primary sm"><I name="check" size={11} /> 保存为草稿 v0.5</button>
        </div>

        <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
          {/* mini module nav (collapsed) */}
          <div style={{ width: 56, borderRight: '1px solid var(--border)', background: 'var(--surface)', padding: '12px 0' }}>
            {['star', 'list', 'cube', 'doc', 'inbox', 'beaker', 'flow'].map((ic, i) => (
              <div key={ic} className="ai-iconbtn" style={{ width: 40, height: 40, margin: '4px auto', background: i === 1 ? 'var(--surface-2)' : 'transparent', color: i === 1 ? 'var(--ink-1)' : 'var(--ink-3)' }}>
                <I name={ic} size={14} />
              </div>
            ))}
          </div>

          {/* main */}
          <div style={{ flex: 1, overflow: 'auto', background: 'var(--bg)', padding: 28 }}>
            <div className="row" style={{ marginBottom: 18 }}>
              <div>
                <div className="row" style={{ gap: 8 }}>
                  <I name="list" size={16} />
                  <span style={{ fontSize: 20, fontWeight: 600, letterSpacing: '-0.02em' }}>规则</span>
                  <span className="ai-pill" style={{ fontSize: 10.5 }}>顺序敏感</span>
                </div>
                <div className="tiny muted" style={{ marginTop: 4 }}>规则按从上到下评估，首个匹配的「Then」生效。第 R-05 行为豁免，永远优先。</div>
              </div>
              <div className="row" style={{ marginLeft: 'auto', gap: 6 }}>
                <button className="ai-btn sm"><I name="upload" size={11} /> 从 YAML 导入</button>
                <button className="ai-btn primary sm"><I name="plus" size={11} /> 新增规则</button>
              </div>
            </div>

            {/* rule rows */}
            <div className="ai-card" style={{ padding: 0 }}>
              <div className="row" style={{ padding: '8px 14px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)', fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--ink-4)', gap: 10 }}>
                <span style={{ width: 16 }}></span>
                <span style={{ width: 42 }}>顺序</span>
                <span style={{ width: 50 }}>ID</span>
                <span style={{ flex: 1 }}>WHEN · 条件</span>
                <span style={{ flex: 1 }}>THEN · 动作</span>
                <span style={{ width: 50 }}>风险</span>
                <span style={{ width: 80 }}>启用</span>
                <span style={{ width: 80 }}></span>
              </div>
              {rules.map((r, i, arr) => (
                <div key={r.id} className="row" style={{ padding: '12px 14px', borderBottom: i === arr.length - 1 ? '0' : '1px solid var(--border)', gap: 10, background: i === 0 ? 'var(--accent-soft)' : 'transparent' }}>
                  <I name="more" size={13} stroke="var(--ink-4)" style={{ cursor: 'grab' }} />
                  <span className="mono" style={{ width: 42, fontSize: 12, color: 'var(--ink-3)' }}>#{String(i+1).padStart(2,'0')}</span>
                  <span className="mono" style={{ width: 50, fontSize: 11.5, fontWeight: 500 }}>{r.id}</span>
                  <span style={{ flex: 1, fontSize: 12.5, lineHeight: 1.5 }}>
                    {r.cond.split(/(下滑系数|7 日访客|7 日支付件数|价格变化|竞品促销|价格差|商品上架)/).map((p, j) => {
                      const isToken = /^(下滑系数|7 日访客|7 日支付件数|价格变化|竞品促销|价格差|商品上架)$/.test(p);
                      return isToken ? <span key={j} className="mono" style={{ background: 'var(--surface-2)', padding: '0 4px', borderRadius: 3, fontSize: 11.5 }}>{p}</span> : <span key={j}>{p}</span>;
                    })}
                  </span>
                  <span style={{ flex: 1, fontSize: 12.5, lineHeight: 1.5 }}>{r.then}</span>
                  <span style={{ width: 50 }}>{r.risk === '—' ? <span className="tiny muted">—</span> : <span className={'ai-pill ' + (r.risk === 'L1' ? 'bad' : 'warn')} style={{ fontSize: 10.5 }}>{r.risk}</span>}</span>
                  <span style={{ width: 80 }}>
                    <div style={{ width: 28, height: 16, borderRadius: 8, background: r.on ? 'var(--ink-1)' : 'var(--ink-5)', position: 'relative' }}>
                      <div style={{ position: 'absolute', top: 2, left: r.on ? 14 : 2, width: 12, height: 12, borderRadius: '50%', background: 'white' }} />
                    </div>
                  </span>
                  <span className="row" style={{ width: 80, gap: 4, justifyContent: 'flex-end' }}>
                    <button className="ai-btn sm">编辑</button>
                    <button className="ai-iconbtn"><I name="more" size={12} /></button>
                  </span>
                </div>
              ))}
            </div>

            {/* preview impact */}
            <div className="ai-card" style={{ padding: 16, marginTop: 16 }}>
              <div className="row" style={{ gap: 8, marginBottom: 8 }}>
                <I name="beaker" size={13} stroke="var(--ink-2)" />
                <span style={{ fontSize: 13, fontWeight: 600 }}>预览影响</span>
                <span className="tiny muted">基于近 7 日 117 件商品回放</span>
                <button className="ai-btn sm" style={{ marginLeft: 'auto' }}>重新计算</button>
              </div>
              <div className="row" style={{ gap: 12 }}>
                {[
                  { l: '会被派发为 P1', v: '8',  d: '+ 2' },
                  { l: '会被派发为 P2', v: '18', d: '+ 5' },
                  { l: '冷启动豁免', v: '6',  d: '+ 1' },
                  { l: '数据缺口告警', v: '474', d: '不变' },
                ].map(s => (
                  <div key={s.l} style={{ flex: 1, padding: 12, background: 'var(--surface-2)', borderRadius: 6 }}>
                    <div className="tiny muted">{s.l}</div>
                    <div className="row" style={{ gap: 6, alignItems: 'baseline' }}>
                      <span className="mono" style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em' }}>{s.v}</span>
                      <span className="tiny" style={{ color: s.d.includes('+') ? 'var(--ok)' : 'var(--ink-3)' }}>{s.d}</span>
                    </div>
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

// ============ L3-D · 训练任务详情 ============
function TrainingJobDetail() {
  const losses = [0.92, 0.74, 0.62, 0.55, 0.48, 0.42, 0.38, 0.34, 0.30, 0.27, 0.24, 0.22, 0.20, 0.19, 0.182];
  const max = Math.max(...losses);
  const points = losses.map((v, i) => `${(i / (losses.length - 1)) * 600},${20 + (1 - v / max) * 90}`).join(' ');

  return (
    <Screen role="admin" active="train" height={1000}>
      <div className="ai-main">
        <div className="row" style={{ height: 44, borderBottom: '1px solid var(--border)', background: 'var(--surface)', padding: '0 24px', gap: 10 }}>
          <button className="ai-btn sm"><I name="arrowl" size={11} /> 返回</button>
          <span className="tiny muted">/ 训练 / 任务 /</span>
          <span style={{ fontSize: 12.5, fontWeight: 500 }} className="mono">ec.link_decline-cls v4</span>
          <span className="ai-pill ok dot">训练中</span>
          <span className="ai-pill" style={{ fontSize: 10.5 }}>EC</span>
          <span className="ai-pill" style={{ fontSize: 10.5 }}>GPU 2 · 0.84</span>
          <div style={{ flex: 1 }} />
          <button className="ai-btn sm">实时日志</button>
          <button className="ai-btn sm">导出 checkpoint</button>
          <button className="ai-btn sm" style={{ color: 'var(--bad)' }}>停止训练</button>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: 24, background: 'var(--bg)' }}>
          {/* status strip */}
          <div className="row" style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, marginBottom: 16 }}>
            {[
              { l: 'step',     v: '2,180 / 4,000', s: '54.5%' },
              { l: 'loss',     v: '0.182',         s: '↓ 0.012 (10 min)' },
              { l: 'acc',      v: '89.4%',         s: '↑ 1.1% (10 min)', c: 'var(--ok)' },
              { l: 'lr',       v: '3.2e-5',        s: 'cosine 衰减' },
              { l: 'GPU util', v: '0.84',          s: 'mem 18.6 / 24 GB' },
              { l: 'ETA',      v: '47 分钟',       s: '总 1h 38m' },
            ].map((s, i, arr) => (
              <div key={s.l} style={{ flex: 1, padding: '14px 18px', borderRight: i === arr.length - 1 ? '0' : '1px solid var(--border)' }}>
                <div className="tiny muted">{s.l}</div>
                <div className="mono" style={{ fontSize: 20, fontWeight: 600, color: s.c || 'var(--ink-1)', letterSpacing: '-0.02em' }}>{s.v}</div>
                <div className="tiny muted">{s.s}</div>
              </div>
            ))}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1.7fr 1fr', gap: 16 }}>
            {/* charts */}
            <div className="col" style={{ gap: 16 }}>
              <div className="ai-card" style={{ padding: 16 }}>
                <div className="row" style={{ marginBottom: 12 }}>
                  <span style={{ fontSize: 13, fontWeight: 600 }}>训练曲线</span>
                  <div className="row" style={{ marginLeft: 'auto', gap: 4 }}>
                    <span className="ai-pill" style={{ background: 'var(--ink-1)', color: 'white', borderColor: 'var(--ink-1)' }}>loss</span>
                    <span className="ai-pill">val_loss</span>
                    <span className="ai-pill">acc</span>
                    <span className="ai-pill">lr</span>
                  </div>
                </div>
                <svg width="100%" height="220" viewBox="0 0 600 220" preserveAspectRatio="none">
                  {/* grid */}
                  {[0,1,2,3,4].map(i => <line key={i} x1="0" y1={20 + i * 45} x2="600" y2={20 + i * 45} stroke="var(--border)" strokeDasharray="3 4" />)}
                  <polyline points={points} fill="none" stroke="var(--accent)" strokeWidth="2" />
                  {/* val_loss reference dashed */}
                  <polyline points="0,28 40,40 80,52 120,62 160,72 200,82 240,90 280,96 320,100 360,104 400,107 440,110 480,113 520,116 560,118 600,121" fill="none" stroke="var(--ink-3)" strokeWidth="1" strokeDasharray="4 3" />
                  <circle cx="600" cy={20 + (1 - 0.182 / max) * 90} r="4" fill="var(--accent)" />
                </svg>
                <div className="row" style={{ marginTop: 8, fontSize: 10, color: 'var(--ink-4)' }}>
                  <span>step 0</span><span style={{ marginLeft: 'auto' }}>500</span><span style={{ marginLeft: 'auto' }}>1000</span><span style={{ marginLeft: 'auto' }}>1500</span><span style={{ marginLeft: 'auto' }}>2000</span>
                </div>
              </div>

              <div className="ai-card" style={{ padding: 0 }}>
                <div className="ai-card-h"><span className="t">数据采样 · 10 条</span><span className="s">来自训练集 + 噪声样本</span></div>
                <table className="ai-table">
                  <thead>
                    <tr><th style={{ width: 40 }}>#</th><th>原文 (截断)</th><th style={{ width: 80 }}>预测</th><th style={{ width: 80 }}>实际</th><th style={{ width: 100 }}>置信</th></tr>
                  </thead>
                  <tbody>
                    {[
                      { o: '热感润滑液销售下滑 38%，竞品 -15% 促销中', p: 'P1', t: 'P1', c: 0.97 },
                      { o: '超薄持久访客 -22%，转化率持平',           p: 'P2', t: 'P1', c: 0.61, mis: true },
                      { o: '冷启动新品上架 3 天，数据缺失',           p: '豁免', t: '豁免', c: 0.94 },
                      { o: '客单价上涨 12%，订单数下降 8%',           p: 'P3', t: 'P3', c: 0.78 },
                    ].map((r, i) => (
                      <tr key={i} style={{ background: r.mis ? 'var(--bad-soft)' : 'transparent' }}>
                        <td className="mono tiny muted">{i + 1}</td>
                        <td style={{ fontSize: 12.5 }}>{r.o}</td>
                        <td><span className={'ai-pill ' + (r.mis ? 'bad' : '')} style={{ fontSize: 10.5 }}>{r.p}</span></td>
                        <td><span className="ai-pill" style={{ fontSize: 10.5 }}>{r.t}</span></td>
                        <td className="mono" style={{ fontSize: 12 }}>{r.c.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* config + log */}
            <div className="col" style={{ gap: 16 }}>
              <div className="ai-card" style={{ padding: 0 }}>
                <div className="ai-card-h"><span className="t">训练配置</span></div>
                <div style={{ padding: '4px 16px' }}>
                  {[
                    ['任务',        'ec.link_decline-cls'],
                    ['基础模型',    'Qwen2.5-7B'],
                    ['训练方式',    'LoRA · r=16 / α=32'],
                    ['数据集',      'ec.link_decline / 2026-05'],
                    ['训练样本',    '12,840'],
                    ['验证样本',    '1,604'],
                    ['Batch',       '16 · grad-accum 2'],
                    ['优化器',      'AdamW · wd 0.01'],
                    ['LR · 调度',    '3e-4 → 3e-5 · cosine'],
                    ['Seed',        '20260524'],
                  ].map(([k, v], i, arr) => (
                    <div key={k} className="row" style={{ padding: '8px 0', borderBottom: i === arr.length - 1 ? '0' : '1px solid var(--border)' }}>
                      <span style={{ flex: 1, fontSize: 12, color: 'var(--ink-3)' }}>{k}</span>
                      <span className="mono" style={{ fontSize: 12, fontWeight: 500 }}>{v}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="ai-card" style={{ padding: 0 }}>
                <div className="ai-card-h">
                  <span className="t">日志 · tail</span>
                  <span className="ai-pill ok dot" style={{ marginLeft: 'auto', fontSize: 10 }}>follow</span>
                </div>
                <div style={{ padding: 12, background: '#13130f', color: '#c0bbae', fontFamily: 'var(--font-mono)', fontSize: 10.5, lineHeight: 1.7, maxHeight: 240, overflow: 'auto', borderRadius: '0 0 8px 8px' }}>
                  <div><span style={{ color: '#6c685e' }}>step=2178</span> loss=0.184 lr=3.21e-5 acc=89.3%</div>
                  <div><span style={{ color: '#6c685e' }}>step=2179</span> loss=0.183 lr=3.21e-5 acc=89.4%</div>
                  <div><span style={{ color: '#6c685e' }}>step=2180</span> loss=0.182 lr=3.20e-5 acc=89.4%</div>
                  <div><span style={{ color: '#fbbf24' }}>WARN  </span>val_loss 微升 0.001 — 仍在容忍范围</div>
                  <div><span style={{ color: '#6c685e' }}>eval  </span>P/R/F1 = 0.91/0.86/0.88</div>
                  <div style={{ color: '#e4e1da' }}>▍</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Screen>
  );
}

// ============ L3-E · 用户详情 ============
function UserDetail() {
  return (
    <Screen role="admin" active="admin" height={920}>
      <div className="ai-main">
        <div className="row" style={{ height: 44, borderBottom: '1px solid var(--border)', background: 'var(--surface)', padding: '0 24px', gap: 10 }}>
          <button className="ai-btn sm"><I name="arrowl" size={11} /> 用户列表</button>
          <span className="tiny muted">/ 管理后台 / 用户 /</span>
          <span style={{ fontSize: 12.5, fontWeight: 500 }}>张文</span>
          <span className="ai-pill ok dot">启用</span>
          <div style={{ flex: 1 }} />
          <button className="ai-btn sm">重置密码</button>
          <button className="ai-btn sm" style={{ color: 'var(--bad)' }}>停用</button>
          <button className="ai-btn primary sm"><I name="check" size={11} /> 保存</button>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: 24, background: 'var(--bg)' }}>
          {/* hero */}
          <div className="ai-card" style={{ padding: 20, marginBottom: 16 }}>
            <div className="row" style={{ gap: 18 }}>
              <div style={{ width: 64, height: 64, borderRadius: '50%', background: 'linear-gradient(135deg, #d8d2c0, #a6a18f)', display: 'grid', placeItems: 'center', fontSize: 24, fontWeight: 600, color: 'var(--ink-1)' }}>张</div>
              <div style={{ flex: 1 }}>
                <div className="row" style={{ gap: 8 }}>
                  <span style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em' }}>张文</span>
                  <span className="ai-pill" style={{ fontSize: 10.5 }}>员工</span>
                  <span className="ai-pill accent" style={{ fontSize: 10.5 }}>EC · 示例品牌传统电商运营部</span>
                </div>
                <div className="row" style={{ gap: 14, marginTop: 8, fontSize: 12, color: 'var(--ink-3)' }}>
                  <span className="mono">zhang.w</span>
                  <span><span className="muted">邮箱</span> user@example.com</span>
                  <span><span className="muted">手机</span> 158-****-4218</span>
                  <span><span className="muted">入职</span> 2024-03-12</span>
                  <span><span className="muted">直属</span> 王琳 · EC 总监</span>
                </div>
              </div>
              <div className="row" style={{ gap: 24, paddingLeft: 24, borderLeft: '1px solid var(--border)' }}>
                {[
                  { l: '近 30 天调用',  v: '342' },
                  { l: '主理 Skill', v: '4' },
                  { l: '收件处理',    v: '128' },
                  { l: 'SLA',         v: '94%', c: 'var(--ok)' },
                ].map(s => (
                  <div key={s.l}>
                    <div className="tiny muted">{s.l}</div>
                    <div className="mono" style={{ fontSize: 20, fontWeight: 600, color: s.c || 'var(--ink-1)' }}>{s.v}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="ai-tabs" style={{ padding: 0, marginBottom: 16, borderBottom: '1px solid var(--border)' }}>
            <div className="ai-tab">概览</div>
            <div className="ai-tab active">权限</div>
            <div className="ai-tab">活动 (近 30d)</div>
            <div className="ai-tab">设备 · 会话</div>
            <div className="ai-tab">操作日志</div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 16 }}>
            {/* roles + permissions */}
            <div className="ai-card" style={{ padding: 0 }}>
              <div className="ai-card-h"><span className="t">角色 & 权限</span><span className="s">RBAC + ABAC · 来自钉钉部门 + 显式分配</span></div>
              <div style={{ padding: '14px 16px' }}>
                <div className="tiny muted" style={{ marginBottom: 6 }}>角色</div>
                <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginBottom: 14 }}>
                  <span className="ai-pill accent">员工 · 默认</span>
                  <span className="ai-pill">EC.主理人</span>
                  <span className="ai-pill">EC.Skill 维护者</span>
                  <button className="ai-btn sm"><I name="plus" size={11} /> 分配角色</button>
                </div>

                <div className="tiny muted" style={{ marginBottom: 6 }}>显式权限 · 12 项</div>
                <table className="ai-table" style={{ marginTop: 6 }}>
                  <thead>
                    <tr><th>资源</th><th style={{ width: 80 }}>操作</th><th style={{ width: 120 }}>范围</th><th style={{ width: 90 }}>来源</th><th style={{ width: 60 }}></th></tr>
                  </thead>
                  <tbody>
                    {[
                      { r: 'skill.tmall-link-decline',  a: '编辑 / 发布', s: 'EC', src: '主理人' },
                      { r: 'skill.inventory-rotation',  a: '运行 / 查看', s: 'EC', src: '部门默认' },
                      { r: 'inbox.todo',                a: '处理 / 派发', s: 'EC', src: '部门默认' },
                      { r: 'agent.ec-runtime-main',     a: '运行 / 查看', s: 'EC', src: '主理人' },
                      { r: 'data.tmall.databank',       a: '读取',       s: '本店', src: '钉钉' },
                    ].map(p => (
                      <tr key={p.r}>
                        <td className="mono" style={{ fontSize: 12 }}>{p.r}</td>
                        <td><span className="ai-pill" style={{ fontSize: 10.5 }}>{p.a}</span></td>
                        <td className="tiny">{p.s}</td>
                        <td className="tiny muted">{p.src}</td>
                        <td><I name="x" size={11} stroke="var(--ink-4)" /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* activity */}
            <div className="col" style={{ gap: 16 }}>
              <div className="ai-card" style={{ padding: 16 }}>
                <div className="row" style={{ marginBottom: 8 }}>
                  <span style={{ fontSize: 13, fontWeight: 600 }}>近 30 天活跃热图</span>
                  <span className="tiny muted" style={{ marginLeft: 'auto' }}>1 格 = 1 天</span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(15, 1fr)', gap: 3 }}>
                  {Array.from({ length: 30 }).map((_, i) => {
                    const intensity = [0, 1, 2, 2, 3, 4, 3, 2, 1, 0, 0, 2, 3, 4, 4, 3, 2, 1, 1, 2, 3, 4, 3, 2, 0, 1, 2, 3, 4, 3][i] || 0;
                    const colors = ['var(--surface-3)', 'rgba(58,91,217,.2)', 'rgba(58,91,217,.45)', 'rgba(58,91,217,.7)', 'var(--accent)'];
                    return <div key={i} style={{ aspectRatio: 1, background: colors[intensity], borderRadius: 2 }} />;
                  })}
                </div>
                <div className="row" style={{ marginTop: 8, fontSize: 10, color: 'var(--ink-4)' }}>
                  <span>少</span>
                  <div className="row" style={{ gap: 2, marginLeft: 'auto' }}>
                    {['var(--surface-3)', 'rgba(58,91,217,.2)', 'rgba(58,91,217,.45)', 'rgba(58,91,217,.7)', 'var(--accent)'].map((c, i) => (
                      <div key={i} style={{ width: 8, height: 8, background: c, borderRadius: 1 }} />
                    ))}
                  </div>
                  <span style={{ marginLeft: 4 }}>多</span>
                </div>
              </div>

              <div className="ai-card" style={{ padding: 0 }}>
                <div className="ai-card-h"><span className="t">最近操作</span><a className="link" style={{ marginLeft: 'auto', fontSize: 12 }}>全部 →</a></div>
                <div>
                  {[
                    { t: '运行 tmall-link-decline',         when: '15 分前', kind: 'run',  ok: true },
                    { t: '通过 商品诊断卡 #DC-007',          when: '30 分前', kind: 'todo', ok: true },
                    { t: '编辑 skill: 规则 R-04 → 关闭',     when: '1 小时前', kind: 'edit' },
                    { t: '登录 · macOS · Safari',           when: '今早 09:02', kind: 'auth' },
                    { t: '导出 报告 PDF',                   when: '昨日',     kind: 'export' },
                  ].map((e, i, arr) => (
                    <div key={i} className="row" style={{ padding: '12px 16px', borderBottom: i === arr.length - 1 ? '0' : '1px solid var(--border)', gap: 10 }}>
                      <I name={e.kind === 'run' ? 'play' : e.kind === 'todo' ? 'check' : e.kind === 'edit' ? 'doc' : e.kind === 'auth' ? 'shield' : 'download'} size={12} stroke="var(--ink-3)" />
                      <span style={{ flex: 1, fontSize: 12.5 }}>{e.t}</span>
                      <span className="tiny muted">{e.when}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Screen>
  );
}

Object.assign(window, { RunDrawer, TodoDetail, SkillModuleEdit, TrainingJobDetail, UserDetail });