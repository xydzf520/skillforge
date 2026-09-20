// Design Standards — atomic showcase of the SkillForge design system (Direction A).

function DesignStandards() {
  const Section = ({ title, subtitle, children }) => (
    <div style={{ borderTop: '1px solid var(--border)', padding: '32px 0' }}>
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 11, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--ink-4)', marginBottom: 6 }}>{title}</div>
        {subtitle && <div style={{ fontSize: 13, color: 'var(--ink-3)', maxWidth: 600 }}>{subtitle}</div>}
      </div>
      {children}
    </div>
  );

  const Swatch = ({ name, token, val, label }) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ width: '100%', height: 64, background: val, borderRadius: 6, border: '1px solid rgba(0,0,0,0.06)' }} />
      <div style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--ink-1)' }}>{name}</div>
      <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)' }}>{token}</div>
      {label && <div className="tiny muted" style={{ fontSize: 10.5 }}>{label}</div>}
    </div>
  );

  const TypeRow = ({ label, sizePx, weight, sample = '一字千金 The quick brown fox · 2026', mono = false }) => (
    <div className="row" style={{ padding: '12px 0', borderBottom: '1px solid var(--border)', gap: 16 }}>
      <div className="mono" style={{ fontSize: 11, color: 'var(--ink-4)', width: 100 }}>{label}</div>
      <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)', width: 90 }}>{sizePx}px / {weight}</div>
      <div style={{ flex: 1, fontSize: sizePx, fontWeight: weight, letterSpacing: sizePx >= 18 ? '-0.02em' : '-0.005em', color: 'var(--ink-1)', fontFamily: mono ? 'var(--font-mono)' : 'var(--font-sans)', lineHeight: 1.3 }}>{sample}</div>
    </div>
  );

  return (
    <div className="ai-app" style={{ width: 1440, background: 'var(--surface)', padding: '40px 56px 60px', minHeight: 100 }}>
      {/* Header */}
      <div className="row" style={{ gap: 16, alignItems: 'flex-end', marginBottom: 24 }}>
        <div className="ai-brand-mark" style={{ width: 40, height: 40, fontSize: 18, borderRadius: 9 }}>A</div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: 'var(--ink-4)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>SkillForge · Direction A</div>
          <h1 style={{ fontSize: 36, fontWeight: 600, letterSpacing: '-0.025em', margin: '4px 0 0' }}>设计标准</h1>
          <div style={{ fontSize: 14, color: 'var(--ink-3)', marginTop: 6 }}>Linear / Vercel 风格的暖中性单色系统 · 中等密度 · Geist + PingFang SC</div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <span className="ai-pill ok dot">v1.0</span>
          <span className="ai-pill">2026-05-24</span>
        </div>
      </div>

      {/* 01 Foundations — colors */}
      <Section title="01 · Color · 颜色系统" subtitle="暖中性灰白为底，单一克制 accent（iris），仅状态色（ok/warn/bad/info）用饱和度。其它一律走中性。">
        <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--ink-3)', marginBottom: 10, letterSpacing: '0.04em', textTransform: 'uppercase' }}>表面 · Surface</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 12, marginBottom: 24 }}>
          <Swatch name="bg"         token="--bg"        val="#f7f6f3" />
          <Swatch name="surface"    token="--surface"   val="#ffffff" label="card / topbar" />
          <Swatch name="surface-2"  token="--surface-2" val="#f3f2ee" label="filter / hover" />
          <Swatch name="surface-3"  token="--surface-3" val="#ebe9e3" />
          <Swatch name="border"     token="--border"    val="#e4e1da" />
          <Swatch name="border-2"   token="--border-2"  val="#d4d0c6" />
        </div>

        <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--ink-3)', marginBottom: 10, letterSpacing: '0.04em', textTransform: 'uppercase' }}>墨色 · Ink (text)</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12, marginBottom: 24 }}>
          <Swatch name="ink-1" token="--ink-1" val="#14130f" label="标题 / 主文" />
          <Swatch name="ink-2" token="--ink-2" val="#3a3833" label="次主文" />
          <Swatch name="ink-3" token="--ink-3" val="#6c685e" label="说明 / muted" />
          <Swatch name="ink-4" token="--ink-4" val="#9a958a" label="副本 / placeholder" />
          <Swatch name="ink-5" token="--ink-5" val="#c0bbae" label="disabled" />
        </div>

        <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--ink-3)', marginBottom: 10, letterSpacing: '0.04em', textTransform: 'uppercase' }}>Accent + 语义色</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12 }}>
          <Swatch name="accent" token="--accent" val="#3a5bd9" label="唯一品牌强调色 · iris" />
          <Swatch name="ok"     token="--ok"     val="#1d7b48" label="成功 / 健康" />
          <Swatch name="warn"   token="--warn"   val="#9a6b13" label="预警" />
          <Swatch name="bad"    token="--bad"    val="#b6321b" label="错误 / 高优先" />
          <Swatch name="info"   token="--info"   val="#1b5e8a" label="信息 / 中性" />
        </div>
      </Section>

      {/* 02 Typography */}
      <Section title="02 · Typography · 字体" subtitle="Geist 用于拉丁文与数字，PingFang SC 处理中文。数字 tabular-nums 等宽对齐。字间距小幅负偏移让标题更紧致。">
        <div className="row" style={{ gap: 24, marginBottom: 20 }}>
          <div className="ai-card" style={{ flex: 1, padding: 16 }}>
            <div className="tiny muted">SANS · 主字</div>
            <div style={{ fontSize: 40, fontWeight: 600, letterSpacing: '-0.025em', lineHeight: 1, marginTop: 6 }}>Geist</div>
            <div style={{ fontSize: 14, color: 'var(--ink-3)' }}>Geist Sans · vercel.com/fonts</div>
            <div style={{ fontSize: 12, color: 'var(--ink-4)', marginTop: 6 }}>weights 400 · 500 · 600 · 700</div>
          </div>
          <div className="ai-card" style={{ flex: 1, padding: 16 }}>
            <div className="tiny muted">CHINESE · 中文</div>
            <div style={{ fontSize: 32, fontWeight: 600, letterSpacing: '-0.02em', lineHeight: 1, marginTop: 6 }}>苹方 SC</div>
            <div style={{ fontSize: 14, color: 'var(--ink-3)' }}>PingFang SC · 系统</div>
            <div style={{ fontSize: 12, color: 'var(--ink-4)', marginTop: 6 }}>fallback: Hiragino Sans GB</div>
          </div>
          <div className="ai-card" style={{ flex: 1, padding: 16 }}>
            <div className="tiny muted">MONO · 等宽</div>
            <div className="mono" style={{ fontSize: 30, fontWeight: 500, letterSpacing: '-0.02em', lineHeight: 1, marginTop: 6 }}>Geist Mono</div>
            <div style={{ fontSize: 14, color: 'var(--ink-3)' }}>Geist Mono · 数字 / ID / code</div>
            <div className="mono" style={{ fontSize: 12, color: 'var(--ink-4)', marginTop: 6 }}>0123 · ec-runtime-1af2fb</div>
          </div>
        </div>

        <div style={{ background: 'var(--surface-2)', borderRadius: 8, padding: '12px 20px' }}>
          <TypeRow label="title-xl"   sizePx={36} weight={600} sample="设计标准 · Design Standards" />
          <TypeRow label="title-l"    sizePx={22} weight={600} sample="能力大厅 — 浏览、搜索、直接运行" />
          <TypeRow label="title-m"    sizePx={16} weight={600} sample="天猫店铺链接下滑分析" />
          <TypeRow label="body"       sizePx={13} weight={400} sample="扫描全店商品，定位下滑原因并生成诊断卡" />
          <TypeRow label="body-sm"    sizePx={12} weight={400} sample="基于你的部门、角色、近 30 天使用 · supplementary copy" />
          <TypeRow label="caption"    sizePx={11} weight={500} sample="UPPERCASE · 0.06EM · 分组标签" />
          <TypeRow label="mono-num"   sizePx={14} weight={500} sample="0.7723 · 117 件 · 2.4% · 2026-05-24" mono />
        </div>
      </Section>

      {/* 03 Spacing + radii */}
      <Section title="03 · Spacing & Radius · 间距与圆角" subtitle="4px 网格。Card 8px / 控件 6px / 小元素 4-5px。竖向气息走 24/20/16/12/10/8。">
        <div className="row" style={{ gap: 30, flexWrap: 'wrap' }}>
          <div>
            <div className="tiny muted" style={{ marginBottom: 8 }}>SPACING</div>
            <div className="row" style={{ gap: 8, alignItems: 'flex-end' }}>
              {[4, 8, 12, 16, 20, 24, 32].map(s => (
                <div key={s} style={{ textAlign: 'center' }}>
                  <div style={{ width: s, height: s, background: 'var(--accent)', borderRadius: 2 }} />
                  <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 6 }}>{s}</div>
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="tiny muted" style={{ marginBottom: 8 }}>RADIUS</div>
            <div className="row" style={{ gap: 12 }}>
              {[{ r: 4, l: 'sm' }, { r: 6, l: 'ctrl' }, { r: 8, l: 'card' }, { r: 12, l: 'lg' }].map(o => (
                <div key={o.r} style={{ textAlign: 'center' }}>
                  <div style={{ width: 56, height: 40, background: 'var(--surface-2)', borderRadius: o.r, border: '1px solid var(--border)' }} />
                  <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 6 }}>{o.r}px · {o.l}</div>
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="tiny muted" style={{ marginBottom: 8 }}>CONTROL HEIGHT</div>
            <div className="row" style={{ gap: 12, alignItems: 'flex-end' }}>
              {[
                { h: 26, l: 'sm' },
                { h: 30, l: 'default' },
                { h: 34, l: 'lg' },
                { h: 46, l: 'hero' },
              ].map(o => (
                <div key={o.h} style={{ textAlign: 'center' }}>
                  <div style={{ width: 84, height: o.h, background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 6 }} />
                  <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)', marginTop: 6 }}>{o.h}px · {o.l}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </Section>

      {/* 04 Components */}
      <Section title="04 · Components · 组件原子" subtitle="所有页面都从这套原子搭出来。粒度小、组合灵活、状态可预测。">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
          {/* Buttons */}
          <div className="ai-card" style={{ padding: 16 }}>
            <div className="tiny muted" style={{ marginBottom: 10 }}>BUTTONS</div>
            <div className="row" style={{ gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
              <button className="ai-btn primary"><I name="play" size={11} /> 主操作</button>
              <button className="ai-btn">默认</button>
              <button className="ai-btn ghost">幽灵</button>
              <button className="ai-btn primary sm"><I name="play" size={10} /> 小尺寸</button>
              <button className="ai-btn sm">默认 sm</button>
              <div className="ai-iconbtn" style={{ border: '1px solid var(--border)' }}><I name="more" size={13} /></div>
              <div className="ai-iconbtn" style={{ border: '1px solid var(--border)' }}><I name="filter" size={13} /></div>
            </div>
            <div className="tiny muted">高度 30 · padding 0/12 · radius 6 · primary 背景 ink-1</div>
          </div>

          {/* Pills */}
          <div className="ai-card" style={{ padding: 16 }}>
            <div className="tiny muted" style={{ marginBottom: 10 }}>PILLS / BADGES</div>
            <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
              <span className="ai-pill">默认</span>
              <span className="ai-pill ok dot">就绪</span>
              <span className="ai-pill warn">预警</span>
              <span className="ai-pill bad">P1</span>
              <span className="ai-pill info">信息</span>
              <span className="ai-pill accent">跨部门</span>
              <span className="ai-pill" style={{ background: 'var(--ink-1)', color: 'white', borderColor: 'var(--ink-1)' }}>选中</span>
              <span className="ai-pill ok" style={{ fontSize: 10, height: 17, padding: '0 6px' }}>NEW</span>
            </div>
            <div className="tiny muted">高度 20 · padding 0/7 · radius 4 · 状态色用 soft 背景</div>
          </div>

          {/* Inputs */}
          <div className="ai-card" style={{ padding: 16 }}>
            <div className="tiny muted" style={{ marginBottom: 10 }}>INPUTS</div>
            <div className="row" style={{ gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
              <div className="ai-input placeholder" style={{ width: 200 }}>
                <I name="search" size={12} />
                <span>搜索能力</span>
              </div>
              <div className="ai-input placeholder">
                <span>部门</span>
                <I name="chev" size={11} />
              </div>
              <div className="ai-input" style={{ background: 'var(--surface)', color: 'var(--ink-1)' }}>
                <span>已选</span>
                <I name="chev" size={11} />
              </div>
            </div>
            <div className="tiny muted">高度 30 · radius 6 · placeholder 用 ink-4</div>
          </div>

          {/* Sidebar item */}
          <div className="ai-card" style={{ padding: 16 }}>
            <div className="tiny muted" style={{ marginBottom: 10 }}>SIDEBAR ITEM</div>
            <div style={{ width: '100%', background: 'var(--surface)' }}>
              <div className="ai-side-label" style={{ paddingLeft: 0 }}>分组标题</div>
              <div className="ai-side-item active"><I name="spark" size={13} className="ic" /><span>为你推荐</span><span className="count">14</span></div>
              <div className="ai-side-item"><I name="clock" size={13} className="ic" /><span>最近用过</span><span className="count">8</span></div>
              <div className="ai-side-item"><span className="deptdot" style={{ background: 'var(--accent)' }} /><span>用户对话能力</span><span className="count">8</span></div>
            </div>
          </div>
        </div>

        {/* Page head */}
        <div className="ai-card" style={{ padding: 0, marginTop: 16, overflow: 'hidden' }}>
          <div className="tiny muted" style={{ padding: '12px 16px 0' }}>PAGE HEAD</div>
          <div className="ai-pagehead" style={{ borderBottom: 0 }}>
            <div>
              <div className="ai-crumbs">能力大厅 · 全员入口</div>
              <h1 className="ai-title">能力大厅</h1>
              <p className="ai-sub">浏览、搜索、直接运行 — 14 个常用 · 全部 74 个能力</p>
            </div>
            <div className="row" style={{ gap: 8 }}>
              <button className="ai-btn"><I name="inbox" size={13} /> 我的收件 <span className="ai-pill bad" style={{ marginLeft: 4 }}>3</span></button>
              <button className="ai-btn primary"><I name="plus" size={12} /> 新建</button>
            </div>
          </div>
          <div className="tiny muted" style={{ padding: '8px 16px 12px' }}>面包屑 12px ink-4 · 标题 22px 600 · 副标 13px ink-3 · 右上为页级操作</div>
        </div>

        {/* Tabs */}
        <div className="ai-card" style={{ padding: 0, marginTop: 16, overflow: 'hidden' }}>
          <div className="tiny muted" style={{ padding: '12px 16px 0' }}>TABS</div>
          <div className="ai-tabs" style={{ marginTop: 8 }}>
            <div className="ai-tab active">列表 <span className="muted" style={{ marginLeft: 4 }}>74</span></div>
            <div className="ai-tab">监控 <span className="ai-pill warn" style={{ marginLeft: 4, fontSize: 10, height: 16, padding: '0 5px' }}>9</span></div>
            <div className="ai-tab">审核 <span className="ai-pill" style={{ marginLeft: 4, fontSize: 10, height: 16, padding: '0 5px' }}>3</span></div>
            <div className="ai-tab">变更日志</div>
          </div>
          <div className="tiny muted" style={{ padding: '8px 16px 12px' }}>下划线选中态 · ink-1 · 1.5px · 内联计数用 muted 或 pill</div>
        </div>

        {/* Table */}
        <div className="ai-card" style={{ padding: 0, marginTop: 16, overflow: 'hidden' }}>
          <div className="tiny muted" style={{ padding: '12px 16px 0' }}>TABLE</div>
          <table className="ai-table">
            <thead>
              <tr>
                <th>Skill</th>
                <th style={{ width: 90 }}>状态</th>
                <th style={{ width: 110 }}>风险/版本</th>
                <th style={{ width: 130 }}>健康度</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <div style={{ fontWeight: 500, fontSize: 13 }}>天猫店铺链接下滑分析</div>
                  <div className="mono-id">tmall-link-decline</div>
                </td>
                <td><span className="ai-pill ok dot">正式运行</span></td>
                <td><span className="ai-pill" style={{ fontSize: 10.5 }}>R1</span> <span className="mono" style={{ fontSize: 11.5, color: 'var(--ink-3)' }}>v0.4</span></td>
                <td>
                  <div className="row" style={{ gap: 6 }}>
                    <div style={{ width: 44, height: 4, background: 'var(--surface-3)', borderRadius: 2, overflow: 'hidden' }}>
                      <div style={{ width: '96%', height: '100%', background: 'var(--ok)' }} />
                    </div>
                    <span className="mono" style={{ fontSize: 11.5, color: 'var(--ok)' }}>96</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
          <div className="tiny muted" style={{ padding: '8px 16px 12px' }}>表头 11.5px uppercase muted · 行 12.5px · 行间 12px · hover surface-2</div>
        </div>

        {/* Card */}
        <div style={{ marginTop: 16, display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
          <div className="ai-card" style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div className="row" style={{ gap: 10, alignItems: 'flex-start' }}>
              <div style={{ width: 30, height: 30, borderRadius: 7, background: 'var(--surface-2)', display: 'grid', placeItems: 'center' }}>
                <I name="trend" size={14} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 14, fontWeight: 600 }}>能力卡片</div>
                <div className="row" style={{ gap: 4, marginTop: 4 }}>
                  <span className="ai-pill" style={{ fontSize: 10.5 }}>电商运营</span>
                  <span className="ai-pill ok" style={{ fontSize: 10, height: 17, padding: '0 5px' }}>NEW</span>
                </div>
              </div>
            </div>
            <div className="muted" style={{ fontSize: 12.5, lineHeight: 1.5 }}>图标 + 名字 + 部门 pill + 一句话 + 主操作</div>
            <div className="row" style={{ gap: 6, marginTop: 'auto', paddingTop: 6, borderTop: '1px dashed var(--border)' }}>
              <button className="ai-btn primary" style={{ flex: 1, justifyContent: 'center', height: 30 }}><I name="play" size={11} /> 运行</button>
              <button className="ai-iconbtn" style={{ width: 30, height: 30, border: '1px solid var(--border)', borderRadius: 6 }}><I name="more" size={13} /></button>
            </div>
          </div>

          <div className="ai-card" style={{ padding: 0 }}>
            <div className="ai-card-h"><span className="t">列表卡片</span><span className="s">带表头</span></div>
            <div style={{ padding: '8px 14px', borderBottom: '1px solid var(--border)' }}>
              <div style={{ fontSize: 12.5, fontWeight: 500 }}>项目 A</div>
              <div className="tiny muted">附属说明</div>
            </div>
            <div style={{ padding: '8px 14px' }}>
              <div style={{ fontSize: 12.5, fontWeight: 500 }}>项目 B</div>
              <div className="tiny muted">附属说明</div>
            </div>
          </div>

          <div className="ai-card" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <span className="ai-pill ok">NEW</span>
              <I name="arrowr" size={12} stroke="var(--ink-4)" />
            </div>
            <div style={{ fontSize: 26, fontWeight: 600, letterSpacing: '-0.025em', lineHeight: 1 }}>74</div>
            <div style={{ fontSize: 13, fontWeight: 600 }}>数字桶 / 入口卡</div>
            <div className="tiny muted">用于"点击进入子列表"的视觉锚点</div>
          </div>
        </div>
      </Section>

      {/* 05 Density / Patterns */}
      <Section title="05 · Patterns · 反复出现的组合" subtitle="把组件按「使命」组合：筛选条 / 健康条 / sparkline / 状态指示 / 部门点。直接复用，不再造新形状。">
        <div className="ai-card" style={{ padding: 16, marginBottom: 12 }}>
          <div className="tiny muted" style={{ marginBottom: 10 }}>FILTER ROW · 筛选条</div>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
            <div className="ai-input placeholder" style={{ width: 240 }}><I name="search" size={12} /><span>搜索能力 / Skill ID</span></div>
            <div className="ai-input placeholder"><span>部门</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>场景</span><I name="chev" size={11} /></div>
            <div className="ai-input placeholder"><span>触发</span><I name="chev" size={11} /></div>
            <div style={{ flex: 1 }} />
            <span className="ai-pill" style={{ background: 'var(--ink-1)', color: 'white', borderColor: 'var(--ink-1)' }}>推荐 14</span>
            <span className="ai-pill">最近 8</span>
            <span className="ai-pill">收藏 3</span>
            <span className="ai-pill">NEW 5</span>
          </div>
        </div>

        <div className="row" style={{ gap: 12, alignItems: 'stretch' }}>
          <div className="ai-card" style={{ padding: 16, flex: 1 }}>
            <div className="tiny muted" style={{ marginBottom: 10 }}>HEALTH BAR · 健康度</div>
            <div className="col" style={{ gap: 8 }}>
              {[96, 88, 72, 54].map(v => {
                const c = v >= 90 ? 'var(--ok)' : v >= 80 ? 'var(--ink-3)' : v >= 70 ? 'var(--warn)' : 'var(--bad)';
                return (
                  <div key={v} className="row" style={{ gap: 8 }}>
                    <div style={{ width: 120, height: 4, background: 'var(--surface-3)', borderRadius: 2, overflow: 'hidden' }}>
                      <div style={{ width: v + '%', height: '100%', background: c }} />
                    </div>
                    <span className="mono" style={{ fontSize: 11.5, color: c }}>{v}</span>
                  </div>
                );
              })}
            </div>
          </div>
          <div className="ai-card" style={{ padding: 16, flex: 1 }}>
            <div className="tiny muted" style={{ marginBottom: 10 }}>SPARKLINE · 趋势条</div>
            <div className="col" style={{ gap: 8 }}>
              {[
                [3, 5, 2, 6, 4, 7, 8],
                [2, 3, 5, 4, 6, 5, 3],
                [4, 6, 8, 9, 7, 5, 4],
              ].map((set, i) => (
                <div key={i} className="row" style={{ gap: 8 }}>
                  <span className="mono" style={{ fontSize: 11.5, color: 'var(--ink-3)', width: 30 }}>{set[set.length - 1] * 12}</span>
                  <span className="spark">
                    {set.map((h, j) => <i key={j} style={{ height: 2 + h * 1.4 + 'px' }} />)}
                  </span>
                </div>
              ))}
            </div>
          </div>
          <div className="ai-card" style={{ padding: 16, flex: 1 }}>
            <div className="tiny muted" style={{ marginBottom: 10 }}>STATUS · 状态指示</div>
            <div className="col" style={{ gap: 8 }}>
              <div className="row" style={{ gap: 8 }}><span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--ok)' }} /><span style={{ fontSize: 12.5 }}>在线</span></div>
              <div className="row" style={{ gap: 8 }}><span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--warn)' }} /><span style={{ fontSize: 12.5 }}>预警</span></div>
              <div className="row" style={{ gap: 8 }}><span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--bad)' }} /><span style={{ fontSize: 12.5 }}>离线 / 失败</span></div>
              <div className="row" style={{ gap: 8 }}><span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--ink-5)' }} /><span style={{ fontSize: 12.5 }}>未知 / 静默</span></div>
            </div>
          </div>
          <div className="ai-card" style={{ padding: 16, flex: 1 }}>
            <div className="tiny muted" style={{ marginBottom: 10 }}>DEPT DOT · 部门 / 能力色</div>
            <div className="col" style={{ gap: 6 }}>
              <div className="row" style={{ gap: 8 }}><span className="deptdot" style={{ background: 'var(--accent)' }} /><span style={{ fontSize: 12.5 }}>用户对话能力</span></div>
              <div className="row" style={{ gap: 8 }}><span className="deptdot" style={{ background: 'var(--ok)' }} /><span style={{ fontSize: 12.5 }}>内容生成</span></div>
              <div className="row" style={{ gap: 8 }}><span className="deptdot" style={{ background: 'var(--info)' }} /><span style={{ fontSize: 12.5 }}>报表生成</span></div>
              <div className="row" style={{ gap: 8 }}><span className="deptdot" style={{ background: 'var(--warn)' }} /><span style={{ fontSize: 12.5 }}>监控巡检</span></div>
              <div className="row" style={{ gap: 8 }}><span className="deptdot" style={{ background: 'var(--bad)' }} /><span style={{ fontSize: 12.5 }}>工作流</span></div>
            </div>
          </div>
        </div>
      </Section>

      {/* 06 Layout rules */}
      <Section title="06 · Layout · 布局规则" subtitle="所有页面遵守同一个 shell：顶栏 + 左侧 sub-nav + 主区域 (page head + 内容)。模块切换永远在顶栏，过滤在左 + 顶部条。">
        <div className="ai-card" style={{ padding: 0, overflow: 'hidden' }}>
          <svg viewBox="0 0 1300 320" width="100%" style={{ display: 'block' }}>
            {/* TopBar */}
            <rect x="0" y="0" width="1300" height="42" fill="#fff" stroke="#e4e1da" />
            <rect x="20" y="13" width="100" height="16" rx="3" fill="#14130f" />
            <rect x="160" y="13" width="62" height="16" rx="3" fill="#3a5bd9" />
            <rect x="234" y="13" width="62" height="16" rx="3" fill="#ebe9e3" />
            <rect x="308" y="13" width="62" height="16" rx="3" fill="#ebe9e3" />
            <rect x="382" y="13" width="62" height="16" rx="3" fill="#ebe9e3" />
            <rect x="456" y="13" width="62" height="16" rx="3" fill="#ebe9e3" />
            <rect x="530" y="13" width="62" height="16" rx="3" fill="#ebe9e3" />
            <rect x="950" y="11" width="220" height="20" rx="4" fill="#ebe9e3" />
            <rect x="1184" y="11" width="20" height="20" rx="4" fill="#ebe9e3" />
            <rect x="1212" y="11" width="20" height="20" rx="4" fill="#ebe9e3" />
            <rect x="1248" y="11" width="34" height="20" rx="4" fill="#ebe9e3" />

            {/* Sidebar */}
            <rect x="0" y="42" width="200" height="278" fill="#fff" stroke="#e4e1da" />
            <rect x="20" y="62" width="50" height="9" rx="2" fill="#c0bbae" />
            {[0,1,2,3].map(i => (<rect key={i} x="20" y={84 + i * 22} width={140 - i*8} height="9" rx="2" fill="#3a3833" />))}
            <rect x="20" y="194" width="50" height="9" rx="2" fill="#c0bbae" />
            {[0,1,2,3,4].map(i => (<rect key={i} x="20" y={216 + i * 18} width={120 - i*10} height="7" rx="2" fill="#6c685e" />))}

            {/* Page head */}
            <rect x="200" y="42" width="1100" height="86" fill="#fff" stroke="#e4e1da" />
            <rect x="228" y="60" width="100" height="8" rx="2" fill="#c0bbae" />
            <rect x="228" y="76" width="170" height="20" rx="3" fill="#14130f" />
            <rect x="228" y="106" width="260" height="10" rx="2" fill="#9a958a" />
            <rect x="1160" y="84" width="100" height="22" rx="4" fill="#14130f" />

            {/* Body cards */}
            <rect x="200" y="128" width="1100" height="192" fill="#f7f6f3" stroke="#e4e1da" />
            <rect x="228" y="146" width="240" height="22" rx="4" fill="#ebe9e3" />
            <rect x="478" y="146" width="120" height="22" rx="4" fill="#ebe9e3" />
            <rect x="608" y="146" width="120" height="22" rx="4" fill="#ebe9e3" />
            {[0,1,2,3].map(i => (
              <g key={i}>
                <rect x={228 + i * 264} y={180} width="248" height="120" rx="8" fill="#fff" stroke="#e4e1da" />
                <rect x={244 + i * 264} y={196} width="28" height="28" rx="6" fill="#f3f2ee" />
                <rect x={282 + i * 264} y={200} width="140" height="10" rx="2" fill="#14130f" />
                <rect x={282 + i * 264} y={218} width="60" height="14" rx="3" fill="#3a5bd9" />
                <rect x={244 + i * 264} y={244} width={220} height="8" rx="2" fill="#9a958a" />
                <rect x={244 + i * 264} y={260} width={140} height="8" rx="2" fill="#c0bbae" />
                <rect x={244 + i * 264} y={278} width={150} height="16" rx="4" fill="#14130f" />
                <rect x={400 + i * 264} y={278} width="64" height="16" rx="4" fill="#fff" stroke="#e4e1da" />
              </g>
            ))}
          </svg>
        </div>
      </Section>

      {/* Footer */}
      <div style={{ borderTop: '1px solid var(--border)', marginTop: 32, paddingTop: 18, display: 'flex', justifyContent: 'space-between', color: 'var(--ink-4)', fontSize: 12 }}>
        <span>SkillForge · 设计标准 v1.0 · Direction A — Crisp Mono</span>
        <span className="mono">tokens 见 src/tokens.css</span>
      </div>
    </div>
  );
}

window.DesignStandards = DesignStandards;