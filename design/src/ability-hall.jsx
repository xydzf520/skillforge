// 能力大厅 — two directions, both employee-shell.

const HOME_DEPTS = [
  { id: 'all',  name: '全部能力', n: 74 },
  { id: 'ec',   name: '电商运营', n: 23 },
  { id: 'cs',   name: '客服',     n: 12 },
  { id: 'sales',name: '销售',     n: 9  },
  { id: 'pmo',  name: 'PMO',     n: 6  },
  { id: 'supply',name: '供应链',  n: 8  },
  { id: 'data', name: '数据/分析', n: 11 },
  { id: 'it',   name: '信息技术', n: 5  },
];

const HOME_RECO = [
  {
    id: 'tmall-link-decline',
    action: '扫一遍今日店铺异常商品',
    name: '天猫店铺链接下滑分析',
    dept: '电商运营',
    sched: '每日 09:00 自动',
    icon: 'trend',
    preview: {
      type: 'cards',
      hint: '输出示例 · 按下滑系数分级的商品诊断卡',
      items: [
        { tag: 'P1', title: '【热感因子】示例品牌…',  metric: '0.7723', state: 'bad' },
        { tag: 'P1', title: '【超薄持久3只】示例品牌…', metric: '0.7333', state: 'bad' },
        { tag: 'P2', title: '【超薄001套体验】…',    metric: '0.7003', state: 'bad' },
        { tag: 'P3', title: '【双头跳蛋】震动跳蛋…', metric: '0.5000', state: 'warn' },
      ],
    },
    eta: '2 分钟',
    last: { when: '2 小时前 · 14:32', ok: true, line: '78 件诊断卡 · 派发 36 件待办' },
  },
  {
    id: 'yuyidata-daily',
    action: '把昨日客服会话解析成需求报告',
    name: '客服对话用户需求分析',
    dept: '客服',
    sched: '每日 08:30 自动',
    icon: 'users',
    preview: {
      type: 'chart',
      hint: '输出示例 · 7 类需求 / 5 类阻碍 的占比报告',
      bars: [
        { label: '价格疑问', v: 32 },
        { label: '物流',     v: 24 },
        { label: '功效',     v: 18 },
        { label: '使用方式', v: 12 },
        { label: '售后',     v: 8  },
        { label: '隐私',     v: 4  },
        { label: '其他',     v: 2  },
      ],
    },
    eta: '6 分钟',
    last: { when: '昨日 08:36', ok: true, line: '识别 7 类需求 / 5 类购买阻碍 · 解析 42,975 会话' },
  },
  {
    id: 'gpt-imagegen',
    action: '把一段文案变成 4 张图',
    name: 'GPT-Image · 图片生成',
    dept: '通用',
    sched: '手动 · 按需',
    icon: 'spark',
    preview: {
      type: 'images',
      hint: '输出示例 · 4 张图 (800 × 1200, 可改尺寸)',
      items: [
        { tone: '#dccfb6', label: 'product hero' },
        { tone: '#c7c2b3', label: 'detail · A' },
        { tone: '#d6cdb8', label: 'detail · B' },
        { tone: '#bcb6a3', label: 'banner' },
      ],
    },
    eta: '10 – 60 秒',
    last: { when: '昨天 17:22', ok: true, line: '4 张 800×1200 · 已存我的产物' },
  },
  {
    id: 'biz-weekly',
    action: '生成上周本部门经营周报草稿',
    name: '部门经营周报',
    dept: '电商运营',
    sched: '每周一 09:00',
    icon: 'doc',
    preview: {
      type: 'doc',
      hint: '输出示例 · Markdown 格式的周报草稿',
      lines: [
        { kind: 'h',    width: 70 },
        { kind: 'meta', width: 45 },
        { kind: 'p',    width: 96 },
        { kind: 'p',    width: 88 },
        { kind: 'h2',   width: 38 },
        { kind: 'p',    width: 92 },
        { kind: 'p',    width: 80 },
        { kind: 'p',    width: 60 },
      ],
    },
    eta: '90 秒',
    last: { when: '上周一 09:00', ok: true, line: 'Markdown 草稿 · 部门群已推送' },
  },
];

const HOME_RECENT = [
  { id: 'tmall-link-decline', name: '天猫店铺链接下滑分析', when: '15 分钟前', last: '生成 78 张诊断卡' },
  { id: 'gpt-imagegen',       name: 'GPT-Image · 图片生成', when: '昨天 17:22', last: '生成 4 张图' },
  { id: 'yuyidata-daily',     name: '客服对话用户需求分析', when: '昨天 09:11', last: '解析 42,975 条' },
];

// Discoverable abilities — new this week or hot in OTHER departments
const HOME_DISCOVER = [
  { id: 'review-distill',   flag: 'new',     name: '抖音评论摘要',         dept: '市场',     desc: '把竞品评论压缩成观点 / 情感 / 槽点三栏', stat: '上线 2 天 · 11 次', icon: 'spark' },
  { id: 'supplier-risk',    flag: 'new',     name: '供应商交付风险评分',   dept: '供应链',   desc: '基于近 30 天到货 / 缺货 / 异常打分',     stat: '上线 5 天 · 28 次', icon: 'shield' },
  { id: 'sku-bundle-reco',  flag: 'cross',   name: 'SKU 搭售组合推荐',     dept: '销售',     desc: '跨品类组合，预测成单提升',               stat: '别的部门 · 今日 14 次', icon: 'cube' },
  { id: 'contract-redline', flag: 'cross',   name: '合同红线识别',         dept: '法务',     desc: '在合同 PDF 中圈出条款、给修改建议',     stat: '别的部门 · 今日 7 次', icon: 'doc' },
  { id: 'gpu-cost-trim',    flag: 'cross',   name: 'GPU 成本月度优化',    dept: '信息技术', desc: '识别低利用率任务，给迁移/合并方案',     stat: '别的部门 · 本周 4 次', icon: 'gpu' },
];

const HOME_SCENARIOS = [
  { id: 'ec',     name: '电商分析',   n: 23, sub: '店铺 / 商品 / 转化', icon: 'trend',  accent: '#3a5bd9' },
  { id: 'report', name: '报表生成',   n: 12, sub: '日报 / 周报 / 月报', icon: 'doc',    accent: '#1d7b48' },
  { id: 'cs',    name: '客服洞察',   n: 9,  sub: '会话 / 需求 / 工单', icon: 'users',  accent: '#9a6b13' },
  { id: 'risk',  name: '风险合规',   n: 6,  sub: '合同 / 审计 / 风控', icon: 'shield', accent: '#b6321b' },
  { id: 'create', name: '内容创作',  n: 8,  sub: '图 / 文 / 短视频脚本', icon: 'spark', accent: '#6a4a2a' },
  { id: 'data',   name: '数据采集',  n: 16, sub: '爬取 / 清洗 / 入仓',   icon: 'database', accent: '#1b5e8a' },
];

const HOME_FEED = [
  { tag: '钉钉 Markdown', kind: 'node_scheduler', date: '2026-05-24 08:47',
    title: '2026-05-24 店铺链接下滑诊断日报',
    summary: '扫描 117 个商品，分析池 78 个异常，生成 78 张诊断卡 · 467 条整改建议',
    skill: '天猫店铺链接下滑分析', dept: '电商运营部', open: 36, total: 36, hot: ['#降级', '#数据过期'] },
  { tag: '钉钉 Markdown', kind: 'node_scheduler', date: '2026-05-24 08:36',
    title: '2026-05-23 客服对话用户需求分析',
    summary: '识别 7 类用户需求，5 类购买阻碍 · 会话 42,975 · 用户消息 457,266',
    skill: '客服需求分析', dept: '客服部', open: 1, total: 1, hot: ['#客服', '#用户需求'] },
  { tag: 'PDF', kind: 'manual', date: '2026-05-24 06:42',
    title: '2026-05-24 仓储库存周转复盘',
    summary: '识别 12 个超过 60 天周转 SKU · 提出 3 项打包清仓建议',
    skill: '库存周转复盘', dept: '仓储物流部', open: 4, total: 9, hot: ['#库存', '#滞销'] },
];

// Preview renderers — visualize what this run typically produces
function PreviewBlock({ p }) {
  if (!p) return null;
  const Wrap = ({ icon, children }) => (
    <div style={{ background: 'var(--surface-2)', borderRadius: 6, padding: 10 }}>
      <div className="row" style={{ marginBottom: 8, gap: 6, alignItems: 'flex-start' }}>
        <I name="spark" size={11} stroke="var(--ink-4)" style={{ marginTop: 2, flexShrink: 0 }} />
        <span style={{ fontSize: 11.5, color: 'var(--ink-2)', lineHeight: 1.4 }}>{p.hint}</span>
      </div>
      {children}
    </div>
  );
  if (p.type === 'images') {
    return (
      <Wrap icon="spark">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
          {p.items.map((it, i) => (
            <div key={i} style={{
              aspectRatio: '4 / 3',
              borderRadius: 4,
              background: `repeating-linear-gradient(45deg, ${it.tone} 0 6px, color-mix(in oklch, ${it.tone}, white 8%) 6px 12px)`,
              display: 'grid', placeItems: 'center',
              fontFamily: 'var(--font-mono)', fontSize: 9, color: 'rgba(0,0,0,0.55)',
              textAlign: 'center', padding: 4,
              border: '1px solid rgba(0,0,0,0.05)',
            }}>{it.label}</div>
          ))}
        </div>
      </Wrap>
    );
  }
  if (p.type === 'doc') {
    return (
      <Wrap icon="doc">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {p.lines.map((l, i) => {
            const h = l.kind === 'h' ? 8 : l.kind === 'h2' ? 6 : l.kind === 'meta' ? 3 : 4;
            const c = l.kind === 'h' || l.kind === 'h2' ? 'var(--ink-2)' : l.kind === 'meta' ? 'var(--ink-5)' : 'var(--ink-4)';
            return <div key={i} style={{ height: h, width: l.width + '%', background: c, borderRadius: 1.5, opacity: l.kind === 'meta' ? 0.6 : 0.85 }} />;
          })}
        </div>
      </Wrap>
    );
  }
  if (p.type === 'cards') {
    return (
      <Wrap icon="cube">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {p.items.map((it, i) => (
            <div key={i} className="row" style={{ background: 'var(--surface)', borderRadius: 4, padding: '5px 8px', gap: 6, border: '1px solid var(--border)' }}>
              <span className={'ai-pill ' + (it.tag === 'P1' ? 'bad' : it.tag === 'P2' ? 'warn' : '')} style={{ fontSize: 9.5, height: 14, padding: '0 4px' }}>{it.tag}</span>
              <span style={{ flex: 1, fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--ink-2)' }}>{it.title}</span>
              <span className="mono" style={{ fontSize: 11, fontWeight: 600, color: it.state === 'bad' ? 'var(--bad)' : 'var(--warn)' }}>{it.metric}</span>
            </div>
          ))}
        </div>
      </Wrap>
    );
  }
  if (p.type === 'chart') {
    const max = Math.max(...p.bars.map(b => b.v));
    return (
      <Wrap icon="trend">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {p.bars.map((b, i) => (
            <div key={b.label} className="row" style={{ gap: 6 }}>
              <span style={{ width: 56, fontSize: 10.5, color: 'var(--ink-3)', textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.label}</span>
              <div style={{ flex: 1, height: 7, background: 'rgba(0,0,0,0.04)', borderRadius: 2, overflow: 'hidden' }}>
                <div style={{ width: (b.v / max * 100) + '%', height: '100%', background: i === 0 ? 'var(--accent)' : 'var(--ink-3)' }} />
              </div>
              <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)', width: 26, textAlign: 'right' }}>{b.v}%</span>
            </div>
          ))}
        </div>
      </Wrap>
    );
  }
  return null;
}

// Dock tile — like a home-screen app icon. Click to open the run drawer.
function AbilityCard({ a }) {
  return (
    <div className="ai-card" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10, height: '100%', cursor: 'pointer', position: 'relative' }}>
      <div style={{ width: 36, height: 36, borderRadius: 9, background: 'var(--surface-2)', display: 'grid', placeItems: 'center' }}>
        <I name={a.icon || 'bolt'} size={17} stroke="var(--ink-1)" />
      </div>
      <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: '-0.01em', lineHeight: 1.3, color: 'var(--ink-1)' }}>{a.name}</div>
      <div className="row" style={{ gap: 6, marginTop: 'auto' }}>
        <span className="tiny muted">{a.dept}</span>
        <I name="arrowr" size={11} stroke="var(--ink-4)" style={{ marginLeft: 'auto' }} />
      </div>
    </div>
  );
}

// ---------- DIRECTION A: Crisp Mono ----------
function AbilityHallA() {
  return (
    <Screen role="admin" active="home" height={880}>
      {/* Sidebar */}
      <div className="ai-sidebar">
        <div className="ai-side-group">
          <div className="ai-side-label">我的</div>
          <div className="ai-side-item active"><I name="spark" size={13} className="ic" /><span>为你推荐</span><span className="count">14</span></div>
          <div className="ai-side-item"><I name="clock" size={13} className="ic" /><span>最近用过</span><span className="count">8</span></div>
          <div className="ai-side-item"><I name="star" size={13} className="ic" /><span>我收藏的</span><span className="count">3</span></div>
          <div className="ai-side-item"><I name="doc" size={13} className="ic" /><span>我的产物</span><span className="count">21</span></div>
        </div>
        <div className="ai-side-group">
          <div className="ai-side-label">按部门</div>
          {HOME_DEPTS.map(d => (
            <div key={d.id} className="ai-side-item">
              <I name="dept" size={13} className="ic" />
              <span>{d.name}</span>
              <span className="count">{d.n}</span>
            </div>
          ))}
        </div>
        <div className="ai-side-group">
          <div className="ai-side-label">按能力</div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: 'var(--accent)' }} /><span>用户对话能力</span><span className="count">8</span></div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: 'var(--ink-2)' }} /><span>数据分析能力</span><span className="count">17</span></div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: 'var(--ok)' }} /><span>内容生成能力</span><span className="count">11</span></div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: 'var(--info)' }} /><span>报表生成能力</span><span className="count">14</span></div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: 'var(--warn)' }} /><span>监控巡检能力</span><span className="count">9</span></div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: 'var(--bad)' }} /><span>工作流编排</span><span className="count">6</span></div>
        </div>
        <div className="ai-side-group">
          <div className="ai-side-label">场景</div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: '#3a5bd9' }} /><span>电商分析</span><span className="count">9</span></div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: '#1d7b48' }} /><span>报表生成</span><span className="count">12</span></div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: '#9a6b13' }} /><span>客服洞察</span><span className="count">5</span></div>
          <div className="ai-side-item"><span className="deptdot" style={{ background: '#b6321b' }} /><span>风险与合规</span><span className="count">4</span></div>
        </div>
      </div>

      {/* Main */}
      <div className="ai-main">
        <div className="ai-pagehead">
          <div>
            <div className="ai-crumbs">能力大厅 · 全员入口</div>
            <h1 className="ai-title">能力大厅</h1>
            <p className="ai-sub">浏览、搜索、直接运行 — 14 个常用 · 全部 74 个能力</p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="ai-btn"><I name="inbox" size={13} /> 我的收件 <span className="ai-pill bad" style={{ marginLeft: 4 }}>3</span></button>
            <button className="ai-btn"><I name="hist" size={13} /> 运行历史</button>
          </div>
        </div>

        <div className="ai-pagebody" style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {/* Filter bar: search + chip filters */}
          <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
            <div className="ai-input placeholder" style={{ width: 320 }}>
              <I name="search" size={13} />
              <span>搜索能力 / Skill ID</span>
            </div>
            <div className="ai-input placeholder"><span>部门</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>场景</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>触发</span><I name="chev" size={11} /></div>
            <div style={{ flex: 1 }} />
            <div className="row" style={{ gap: 4 }}>
              <span className="ai-pill" style={{ background: 'var(--ink-1)', color: 'white', borderColor: 'var(--ink-1)' }}>推荐 14</span>
              <span className="ai-pill">最近 8</span>
              <span className="ai-pill">收藏 3</span>
              <span className="ai-pill">NEW 5</span>
              <span className="ai-pill">全部 74</span>
            </div>
          </div>

          {/* Card grid — the catalog. Each card is directly runnable. */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
            {HOME_RECO.concat(HOME_DISCOVER).slice(0, 12).map((a, i) => {
              const isPinned = i < 4;
              const isNew = a.flag === 'new';
              const isCross = a.flag === 'cross';
              const desc = a.action || a.desc;
              return (
                <div key={a.id} className="ai-card" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10, position: 'relative' }}>
                  <div className="row" style={{ gap: 10, alignItems: 'flex-start' }}>
                    <div style={{ width: 30, height: 30, borderRadius: 7, background: 'var(--surface-2)', display: 'grid', placeItems: 'center', flex: '0 0 30px' }}>
                      <I name={a.icon || 'bolt'} size={14} stroke="var(--ink-1)" />
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="row" style={{ gap: 4 }}>
                        <span style={{ fontSize: 14, fontWeight: 600, letterSpacing: '-0.01em', lineHeight: 1.3, color: 'var(--ink-1)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.name}</span>
                        {isPinned && <I name="star" size={11} stroke="var(--warn)" />}
                      </div>
                      <div className="row" style={{ gap: 4, marginTop: 4 }}>
                        <span className="ai-pill" style={{ fontSize: 10.5 }}>{a.dept}</span>
                        {isNew && <span className="ai-pill ok" style={{ fontSize: 10, height: 17, padding: '0 5px' }}>NEW</span>}
                        {isCross && <span className="ai-pill accent" style={{ fontSize: 10, height: 17, padding: '0 5px' }}>跨部门</span>}
                      </div>
                    </div>
                  </div>

                  <div className="muted" style={{ fontSize: 12.5, lineHeight: 1.5, minHeight: 36, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{desc}</div>

                  <div className="row" style={{ gap: 6, marginTop: 'auto', paddingTop: 6, borderTop: '1px dashed var(--border)' }}>
                    <button className="ai-btn primary" style={{ flex: 1, justifyContent: 'center', height: 30 }}><I name="play" size={11} /> 运行</button>
                    <button className="ai-iconbtn" style={{ width: 30, height: 30, border: '1px solid var(--border)', borderRadius: 6 }} title="详情"><I name="more" size={13} /></button>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Footer hint + load more */}
          <div className="row" style={{ marginTop: 4, justifyContent: 'space-between', color: 'var(--ink-4)', fontSize: 12 }}>
            <span>显示 12 / 74 · 按你的部门和角色排序</span>
            <button className="ai-btn sm">加载更多</button>
          </div>
        </div>
      </div>
    </Screen>
  );
}

// ---------- DIRECTION B: Editorial / Content-led ----------
function AbilityHallB() {
  return (
    <Screen role="employee" active="home" className="ed" height={1180}>
      <div className="ai-main">
        {/* Editorial hero */}
        <div style={{ padding: '48px 80px 32px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ fontSize: 12, color: 'var(--ink-4)', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 14 }}>
            SkillForge · 2026 第 21 周 · 周日
          </div>
          <h1 style={{ fontSize: 44, lineHeight: 1.05, fontWeight: 600, letterSpacing: '-0.03em', margin: 0, maxWidth: 1000, color: 'var(--ink-1)' }}>
            今天，部门已为你产出 <span style={{ color: 'var(--accent)' }}>8 份报告</span>，<br />
            14 个能力等待你直接运行。
          </h1>
          <div className="row" style={{ marginTop: 28, gap: 10 }}>
            <div style={{ flex: 1, maxWidth: 560, height: 46, borderRadius: 10, border: '1px solid var(--border-2)', background: 'var(--surface)', display: 'flex', alignItems: 'center', padding: '0 16px', gap: 10 }}>
              <I name="search" size={16} stroke="var(--ink-3)" />
              <span style={{ color: 'var(--ink-4)', fontSize: 14 }}>试试「分析昨天店铺下滑原因」或「跑客服日报」…</span>
              <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--ink-4)', border: '1px solid var(--border)', padding: '2px 6px', borderRadius: 4 }}>⌘K</span>
            </div>
            <button className="ai-btn primary" style={{ height: 46, padding: '0 20px', borderRadius: 10, fontSize: 14 }}>
              <I name="bolt" size={14} /> AI 帮我选
            </button>
          </div>
          <div className="row" style={{ marginTop: 16, gap: 6, flexWrap: 'wrap' }}>
            {['店铺下滑诊断', '客服需求分析', '库存周转', '订单异常巡检', '商品诊断卡', '经营周报', 'GPT-Image'].map(t => (
              <span key={t} style={{ padding: '5px 10px', border: '1px solid var(--border)', borderRadius: 20, fontSize: 12, background: 'var(--surface)', color: 'var(--ink-2)' }}>{t}</span>
            ))}
          </div>
        </div>

        <div style={{ padding: '32px 80px 40px', display: 'grid', gridTemplateColumns: '1.6fr 1fr', gap: 40 }}>
          {/* Left column: featured report */}
          <div>
            <div style={{ fontSize: 11, letterSpacing: '0.08em', color: 'var(--ink-4)', textTransform: 'uppercase', marginBottom: 12 }}>头条 · 今日主报告</div>
            <div className="ai-card" style={{ padding: 28, background: 'var(--surface)', borderRadius: 14 }}>
              <div className="row" style={{ gap: 8, marginBottom: 16 }}>
                <span className="ai-pill accent">日报</span>
                <span className="ai-pill">钉钉 Markdown</span>
                <span className="ai-pill ok">健康 100%</span>
                <span className="tiny muted" style={{ marginLeft: 'auto' }}>2026-05-24 08:47 · 自动</span>
              </div>
              <div style={{ fontSize: 28, fontWeight: 600, letterSpacing: '-0.025em', lineHeight: 1.15, marginBottom: 10 }}>
                店铺链接下滑诊断日报
              </div>
              <div style={{ fontSize: 14.5, color: 'var(--ink-3)', lineHeight: 1.55, marginBottom: 18 }}>
                扫描全店 117 个商品，分析池 78 个异常，生成 78 张商品诊断卡、467 条整改建议，发现 474 个数据缺口。
                今日先补齐关键数据，禁止直接删词、改价、降预算。
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 0, borderTop: '1px solid var(--border)', paddingTop: 16 }}>
                {[
                  { l: '扫描商品', v: '117' },
                  { l: '成交金额', v: '¥105,707' },
                  { l: '支付转化', v: '6.4%' },
                  { l: '数据缺口', v: '474', warn: true },
                ].map(s => (
                  <div key={s.l} style={{ borderRight: '1px solid var(--border)', paddingLeft: 4 }}>
                    <div className="tiny muted">{s.l}</div>
                    <div style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em', color: s.warn ? 'var(--warn)' : 'var(--ink-1)' }}>{s.v}</div>
                  </div>
                ))}
              </div>
              <div className="row" style={{ marginTop: 20, gap: 8 }}>
                <button className="ai-btn primary"><I name="arrowr" size={12} /> 阅读完整报告</button>
                <button className="ai-btn">订阅这份报告</button>
                <span className="tiny muted" style={{ marginLeft: 'auto' }}>关联 36 个待办 · 36 个未决</span>
              </div>
            </div>

            {/* Stack of two more */}
            <div style={{ marginTop: 20, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              {HOME_FEED.slice(1).map((f, i) => (
                <div key={i} className="ai-card" style={{ padding: 18 }}>
                  <div className="row" style={{ gap: 6, marginBottom: 10 }}>
                    <span className="ai-pill">{f.tag}</span>
                    <span className="tiny muted" style={{ marginLeft: 'auto' }}>{f.date.split(' ')[1]}</span>
                  </div>
                  <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: '-0.015em', marginBottom: 6 }}>{f.title}</div>
                  <div className="muted" style={{ fontSize: 12.5, lineHeight: 1.5 }}>{f.summary}</div>
                  <div className="tiny muted" style={{ marginTop: 12, paddingTop: 10, borderTop: '1px dashed var(--border)' }}>{f.skill} · {f.dept}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Right column: directly-runnable + popular */}
          <div className="col" style={{ gap: 24 }}>
            <div>
              <div style={{ fontSize: 11, letterSpacing: '0.08em', color: 'var(--ink-4)', textTransform: 'uppercase', marginBottom: 12 }}>为你推荐 · 可直接运行</div>
              <div className="col" style={{ gap: 10 }}>
                {HOME_RECO.slice(0, 4).map((a, i) => (
                  <div key={a.id} className="ai-card" style={{ padding: 14, display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                    <div style={{ width: 32, height: 32, borderRadius: 8, background: 'var(--surface-2)', display: 'grid', placeItems: 'center', flex: '0 0 32px' }}>
                      <I name={['spark', 'users', 'cube', 'doc'][i]} size={14} />
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="row" style={{ justifyContent: 'space-between' }}>
                        <span style={{ fontWeight: 600, fontSize: 13.5 }}>{a.name}</span>
                        <span className="tiny muted">{a.runs}</span>
                      </div>
                      <div className="muted" style={{ fontSize: 12, marginTop: 3, lineHeight: 1.45 }}>{a.desc}</div>
                      <div className="row" style={{ marginTop: 8, gap: 6 }}>
                        <span className="ai-pill" style={{ fontSize: 10.5 }}>{a.dept}</span>
                        <span className="ai-pill mono" style={{ fontSize: 10 }}>{a.tag}</span>
                        <button className="ai-btn sm" style={{ marginLeft: 'auto' }}>运行 →</button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <div style={{ fontSize: 11, letterSpacing: '0.08em', color: 'var(--ink-4)', textTransform: 'uppercase', marginBottom: 12 }}>团队在用</div>
              <div className="ai-card">
                {[
                  { n: '订单异常巡检', s: '电商运营 · 今日 84 次' },
                  { n: '商品诊断卡生成', s: '电商运营 · 今日 67 次' },
                  { n: '客服 KPI 看板', s: '客服 · 今日 41 次' },
                ].map((r, i, arr) => (
                  <div key={r.n} className="row" style={{ padding: '12px 14px', borderBottom: i === arr.length - 1 ? '0' : '1px solid var(--border)', gap: 10 }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 500, fontSize: 13 }}>{r.n}</div>
                      <div className="tiny muted">{r.s}</div>
                    </div>
                    <button className="ai-btn sm">运行</button>
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

Object.assign(window, { AbilityHallA, AbilityHallB });
