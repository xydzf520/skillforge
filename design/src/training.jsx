// 训练 — restructured as a multi-dept process-data → model pipeline.
// Stages: 部门数据 → 清洗 → 训练 → 模型 → 评估 → 运行

function Training() {
  const stages = [
    { id: 'data',  label: '部门数据', desc: '17 部门 · 过程数据接入', n: '12.4M', sub: '本周 +840k 条' },
    { id: 'clean', label: '清洗',    desc: 'PII / 去重 / 校验',      n: '8.1M',  sub: '保留 65.3%', warn: true },
    { id: 'train', label: '训练',    desc: '2 任务进行中',          n: '2',     sub: '2 任务待审' },
    { id: 'model', label: '模型',    desc: '产出 LoRA / 全量',      n: '12',    sub: '本月 +3 个' },
    { id: 'eval',  label: '评估',    desc: '门禁通过 / 离线指标',    n: '9',     sub: '3 个不达标', warn: true },
    { id: 'serve', label: '运行',    desc: '部署到 Agent / 服务',    n: '7',     sub: '7 在线 · 0 故障', ok: true },
  ];

  const deptData = [
    { dept: 'EC · 电商运营',  rows: '4.2M', fresh: '5 分钟', cov: 92, top: '订单 / 商品 / 站内行为' },
    { dept: 'CS · 客服',     rows: '2.6M', fresh: '即时',   cov: 88, top: '会话 / 工单 / 评分' },
    { dept: '仓储物流',       rows: '1.1M', fresh: '1 小时', cov: 71, top: '出入库 / 周转 / 异常' },
    { dept: '供应链',         rows: '720k', fresh: '4 小时', cov: 64, top: '采购 / 缺货 / 在途' },
    { dept: '销售',           rows: '1.6M', fresh: '15 分钟', cov: 79, top: '商机 / 跟进 / 成交' },
    { dept: 'PMO',           rows: '210k', fresh: '即时',   cov: 84, top: '项目 / 里程碑 / 风险' },
    { dept: '信息技术',       rows: '480k', fresh: '即时',   cov: 95, top: '工单 / 故障 / 上线' },
    { dept: '财务',           rows: '90k',  fresh: '日',     cov: 52, top: '账单 / 报销', warn: true },
  ];

  const jobs = [
    { id: 'job-tmall-decline-0524', name: 'ec.link_decline-cls v4', stage: '训练中', step: '2,180 / 4,000', loss: 0.182, acc: 89.4, gpu: 'GPU 2 · 0.84', eta: '47 分钟', dept: 'EC' },
    { id: 'job-cs-need-0524',       name: 'cs.user_need-cls v2',   stage: '训练中', step:   '912 / 3,000', loss: 0.331, acc: 81.2, gpu: 'GPU 1 · 0.71', eta: '1 小时 12 分', dept: 'CS' },
    { id: 'job-warehouse-0523',     name: 'wh.turnover-reg v1',    stage: '待审',   step: '—',             loss: '—',   acc: '—',   gpu: '—',          eta: '—',           dept: '仓储' },
  ];

  const models = [
    { name: 'ec.link_decline-cls', v: 'v3', acc: 88.2, f1: 0.86, status: '运行中', serves: 7, lastEval: '昨天', dept: 'EC' },
    { name: 'cs.user_need-cls',   v: 'v1', acc: 79.5, f1: 0.77, status: '运行中', serves: 4, lastEval: '昨天', dept: 'CS' },
    { name: 'wh.anomaly-det',     v: 'v2', acc: 84.0, f1: 0.82, status: '灰度',   serves: 1, lastEval: '今天', dept: '仓储' },
    { name: 'sales.lead-rank',    v: 'v1', acc: 71.3, f1: 0.69, status: '不达标', serves: 0, lastEval: '今天', dept: '销售', warn: true },
  ];

  return (
    <Screen role="admin" active="train" height={1180}>
      <div className="ai-main">
        <div className="ai-pagehead">
          <div>
            <div className="ai-crumbs">训练 · 多部门过程数据 → 模型</div>
            <h1 className="ai-title">训练流水</h1>
            <p className="ai-sub">把各部门跑出的过程数据，按 数据 → 清洗 → 训练 → 模型 → 评估 → 运行 流转</p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="ai-btn"><I name="database" size={12} /> 数据资产</button>
            <button className="ai-btn"><I name="cube" size={12} /> 模型库</button>
            <button className="ai-btn"><I name="gpu" size={12} /> GPU 状态 <span className="ai-pill ok" style={{ marginLeft: 4 }}>2 / 2</span></button>
            <button className="ai-btn primary"><I name="plus" size={12} /> 新建训练</button>
          </div>
        </div>

        <div className="ai-pagebody" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          {/* Pipeline */}
          <div className="ai-card" style={{ padding: 0, overflow: 'hidden' }}>
            <div className="row" style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', justifyContent: 'space-between' }}>
              <div className="row" style={{ gap: 10 }}>
                <I name="flow" size={13} stroke="var(--ink-3)" />
                <span style={{ fontWeight: 600, fontSize: 13 }}>训练流水线</span>
                <span className="tiny muted">点击任一阶段查看明细</span>
              </div>
              <div className="row" style={{ gap: 6 }}>
                <span className="ai-pill">本日</span>
                <span className="ai-pill">本周</span>
                <span className="ai-pill" style={{ background: 'var(--ink-1)', color: 'white', borderColor: 'var(--ink-1)' }}>本月</span>
              </div>
            </div>
            <div style={{ padding: '20px 12px', display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 0, position: 'relative' }}>
              {stages.map((s, i) => (
                <div key={s.id} style={{ padding: '0 12px', borderRight: i < stages.length - 1 ? '1px dashed var(--border)' : 0, position: 'relative' }}>
                  <div className="row" style={{ gap: 6, marginBottom: 6 }}>
                    <span style={{ width: 18, height: 18, borderRadius: 4, background: i === 2 ? 'var(--ink-1)' : 'var(--surface-2)', color: i === 2 ? 'white' : 'var(--ink-3)', fontSize: 10, fontWeight: 600, display: 'grid', placeItems: 'center', fontFamily: 'var(--font-mono)' }}>{i + 1}</span>
                    <span style={{ fontSize: 13, fontWeight: 600, letterSpacing: '-0.01em' }}>{s.label}</span>
                  </div>
                  <div style={{ fontSize: 30, fontWeight: 600, letterSpacing: '-0.025em', lineHeight: 1, color: s.warn ? 'var(--warn)' : s.ok ? 'var(--ok)' : 'var(--ink-1)' }}>
                    {s.n}
                  </div>
                  <div className="tiny muted" style={{ marginTop: 4 }}>{s.desc}</div>
                  <div className="tiny" style={{ marginTop: 2, color: s.warn ? 'var(--warn)' : s.ok ? 'var(--ok)' : 'var(--ink-3)' }}>{s.sub}</div>

                  {/* arrow */}
                  {i < stages.length - 1 && (
                    <div style={{ position: 'absolute', right: -7, top: 9, width: 14, height: 14, background: 'var(--surface)', display: 'grid', placeItems: 'center' }}>
                      <I name="arrowr" size={11} stroke="var(--ink-4)" />
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Two-column: training jobs + dept data */}
          <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: 16 }}>
            <div className="ai-card">
              <div className="ai-card-h">
                <span className="t">训练任务 · 进行中</span>
                <span className="s">{jobs.filter(j => j.stage === '训练中').length} 进行 / {jobs.length} 共</span>
                <div className="row" style={{ marginLeft: 'auto', gap: 6 }}>
                  <button className="ai-btn sm">全部任务</button>
                </div>
              </div>
              <div>
                {jobs.map((j, i) => (
                  <div key={j.id} style={{ padding: '14px 16px', borderBottom: i === jobs.length - 1 ? '0' : '1px solid var(--border)' }}>
                    <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
                      <div className="row" style={{ gap: 8 }}>
                        <span className="ai-pill" style={{ background: j.dept === 'EC' ? 'var(--accent-soft)' : 'var(--surface-2)', color: j.dept === 'EC' ? 'var(--accent-ink)' : 'var(--ink-2)' }}>{j.dept}</span>
                        <span style={{ fontWeight: 600, fontSize: 13 }} className="mono">{j.name}</span>
                        <span className={'ai-pill ' + (j.stage === '训练中' ? 'ok dot' : 'warn')} style={{ fontSize: 10.5 }}>{j.stage}</span>
                      </div>
                      <span className="mono-id">{j.id}</span>
                    </div>
                    {j.stage === '训练中' ? (
                      <>
                        <div className="row" style={{ gap: 24, fontSize: 12, color: 'var(--ink-2)', marginBottom: 8 }}>
                          <span><span className="muted">step </span><span className="mono" style={{ fontWeight: 500 }}>{j.step}</span></span>
                          <span><span className="muted">loss </span><span className="mono" style={{ fontWeight: 500 }}>{j.loss}</span></span>
                          <span><span className="muted">acc  </span><span className="mono" style={{ fontWeight: 500 }}>{j.acc}%</span></span>
                          <span><span className="muted">{j.gpu}</span></span>
                          <span style={{ marginLeft: 'auto' }}><span className="muted">ETA </span><span className="mono">{j.eta}</span></span>
                        </div>
                        {/* loss curve */}
                        <svg width="100%" height="34" viewBox="0 0 600 34" preserveAspectRatio="none" style={{ display: 'block' }}>
                          <polyline points="0,8 60,12 120,10 180,16 240,14 300,18 360,16 420,22 480,20 540,26 600,24" fill="none" stroke="var(--ink-3)" strokeWidth="1" />
                          <polyline points="0,2 60,6 120,8 180,7 240,12 300,10 360,15 420,13 480,18 540,16 600,21" fill="none" stroke="var(--accent)" strokeWidth="1.5" />
                          <line x1="0" y1="32" x2="600" y2="32" stroke="var(--border)" />
                        </svg>
                        <div className="row" style={{ gap: 6, marginTop: 6 }}>
                          <button className="ai-btn sm">实时日志</button>
                          <button className="ai-btn sm">查看曲线</button>
                          <button className="ai-btn sm" style={{ marginLeft: 'auto' }}>暂停</button>
                          <button className="ai-btn sm">停止</button>
                        </div>
                      </>
                    ) : (
                      <div className="row" style={{ gap: 6, color: 'var(--ink-3)', fontSize: 12, padding: '4px 0' }}>
                        <span>等待审批 · 由 张文 提交 · 估算 1 小时 22 分</span>
                        <button className="ai-btn sm" style={{ marginLeft: 'auto' }}>查看</button>
                        <button className="ai-btn sm">驳回</button>
                        <button className="ai-btn primary sm">通过</button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>

            <div className="ai-card">
              <div className="ai-card-h">
                <span className="t">部门过程数据 · 本周</span>
                <a className="link" style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--ink-3)' }}>全部 17 个</a>
              </div>
              <table className="ai-table">
                <thead>
                  <tr>
                    <th>部门 / 数据</th>
                    <th style={{ width: 70 }}>条数</th>
                    <th style={{ width: 70 }}>新鲜度</th>
                    <th style={{ width: 90 }}>覆盖</th>
                  </tr>
                </thead>
                <tbody>
                  {deptData.map(d => (
                    <tr key={d.dept}>
                      <td>
                        <div style={{ fontWeight: 500, fontSize: 12.5 }}>{d.dept}</div>
                        <div className="tiny muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.top}</div>
                      </td>
                      <td><span className="mono" style={{ fontSize: 12 }}>{d.rows}</span></td>
                      <td className="tiny muted">{d.fresh}</td>
                      <td><HealthBar v={d.cov} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Models + Eval gate */}
          <div className="ai-card">
            <div className="ai-card-h">
              <span className="t">模型 → 评估 → 运行</span>
              <span className="s">部门产出模型与门禁结果</span>
              <div className="row" style={{ marginLeft: 'auto', gap: 6 }}>
                <button className="ai-btn sm">模型库</button>
                <button className="ai-btn sm">评估门禁</button>
              </div>
            </div>
            <table className="ai-table">
              <thead>
                <tr>
                  <th>模型</th>
                  <th style={{ width: 70 }}>部门</th>
                  <th style={{ width: 80 }}>版本</th>
                  <th style={{ width: 110 }}>离线 Acc</th>
                  <th style={{ width: 110 }}>F1</th>
                  <th style={{ width: 110 }}>评估状态</th>
                  <th style={{ width: 90 }}>已部署</th>
                  <th style={{ width: 80 }}>评估</th>
                  <th style={{ width: 200 }}></th>
                </tr>
              </thead>
              <tbody>
                {models.map(m => (
                  <tr key={m.name}>
                    <td><span className="mono" style={{ fontWeight: 500 }}>{m.name}</span></td>
                    <td><span className="ai-pill" style={{ fontSize: 10.5 }}>{m.dept}</span></td>
                    <td><span className="mono" style={{ fontSize: 12 }}>{m.v}</span></td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        <span className="mono" style={{ fontWeight: 500, color: m.acc >= 80 ? 'var(--ink-1)' : 'var(--warn)' }}>{m.acc}%</span>
                        <div style={{ width: 50, height: 4, background: 'var(--surface-3)', borderRadius: 2, overflow: 'hidden' }}>
                          <div style={{ width: m.acc + '%', height: '100%', background: m.acc >= 80 ? 'var(--ok)' : 'var(--warn)' }} />
                        </div>
                      </div>
                    </td>
                    <td><span className="mono">{m.f1}</span></td>
                    <td>
                      <span className={'ai-pill ' + (m.status === '运行中' ? 'ok dot' : m.status === '灰度' ? 'warn' : 'bad')} style={{ fontSize: 10.5 }}>
                        {m.status}
                      </span>
                    </td>
                    <td><span className="mono">{m.serves}</span> <span className="tiny muted">Agent</span></td>
                    <td className="tiny muted">{m.lastEval}</td>
                    <td className="row" style={{ gap: 4, justifyContent: 'flex-end' }}>
                      <button className="ai-btn sm">查看曲线</button>
                      <button className="ai-btn sm" disabled={m.warn}>部署 / 升级</button>
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

window.Training = Training;
