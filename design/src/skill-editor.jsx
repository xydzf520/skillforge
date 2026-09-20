// Skill Editor — the IDE-like edit view for a single Skill (admin).

function SkillEditor() {
  const modules = [
    { id: 'overview', name: '概览',     sub: 'Skill 概览',  count: 0,  active: false },
    { id: 'basic',    name: '基础信息', sub: '天猫店铺链接下滑…', count: 1,  active: false },
    { id: 'goal',     name: '目标',     sub: '未填写', count: 0, active: true },
    { id: 'rules',    name: '规则',     sub: '0 条规则', count: 0 },
    { id: 'params',   name: '参数',     sub: '0 个参数', count: 0 },
    { id: 'output',   name: '输出',     sub: '0 个字段', count: 0 },
    { id: 'todos',    name: '待办',     sub: '未配置待办', count: 0 },
    { id: 'tests',    name: '测试',     sub: '0 个样例',  count: 0 },
    { id: 'workflow', name: '工作流',   sub: '暂无',     count: 0 },
  ];

  const tiles = [
    { id: 'goal',     name: '目标',   icon: 'star',  state: '未填写', hint: '点击编写 Skill 目标…' },
    { id: 'rules',    name: '规则',   icon: 'list',  state: '未填写', hint: '点击添加决策规则…' },
    { id: 'params',   name: '参数',   icon: 'cube',  state: '未填写', hint: '点击配置参数阈值…' },
    { id: 'output',   name: '输出',   icon: 'doc',   state: '未填写', hint: '点击定义输出字段…' },
    { id: 'todos',    name: '待办',   icon: 'inbox', state: '未配置', hint: '点击配置收件中心待办…' },
    { id: 'tests',    name: '测试',   icon: 'beaker',state: '未填写', hint: '点击添加测试用例…' },
    { id: 'workflow', name: '工作流', icon: 'flow',  state: '未填写', hint: '点击配置工作流…', wide: true },
  ];

  const aiSuggestions = ['优化目标', '补全异常分支', '提取参数', '生成测试用例', '解释执行逻辑'];

  return (
    <Screen role="admin" active="skills" height={900}>
      <div className="ai-main" style={{ display: 'flex', flexDirection: 'column' }}>
        {/* Top crumb + action bar (single compact row) */}
        <div style={{ height: 44, display: 'flex', alignItems: 'center', borderBottom: '1px solid var(--border)', background: 'var(--surface)', padding: '0 16px', gap: 10 }}>
          <I name="arrowl" size={13} stroke="var(--ink-3)" />
          <span className="tiny muted">Skills</span>
          <span className="tiny muted">/</span>
          <span style={{ fontSize: 13, fontWeight: 500 }}>天猫店铺链接下滑分析</span>
          <span className="ai-pill ok dot">健康 96</span>
          <span className="ai-pill" style={{ fontSize: 10.5 }}>R1</span>
          <span className="ai-pill" style={{ fontSize: 10.5 }}>v0.4</span>
          <span className="ai-pill" style={{ fontSize: 10.5, background: 'var(--ink-1)', color: 'white', borderColor: 'var(--ink-1)' }}>编辑中</span>
          <div style={{ flex: 1 }} />
          {/* View mode segmented */}
          <div className="row" style={{ gap: 0, border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
            <span className="ai-tab" style={{ height: 26, padding: '0 10px', border: 0, background: 'var(--surface-2)', color: 'var(--ink-1)' }}>模块</span>
            <span className="ai-tab" style={{ height: 26, padding: '0 10px', border: 0 }}>九宫格</span>
            <span className="ai-tab" style={{ height: 26, padding: '0 10px', border: 0 }}>源码</span>
          </div>
          <button className="ai-btn sm"><I name="check" size={11} /> 提交审核</button>
          <button className="ai-btn sm">AI 调整界面</button>
          <button className="ai-btn primary sm"><I name="play" size={11} /> 运行</button>
        </div>

        {/* 3-column body */}
        <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
          {/* Left: module nav */}
          <div style={{ width: 220, flex: '0 0 220px', borderRight: '1px solid var(--border)', background: 'var(--surface)', display: 'flex', flexDirection: 'column' }}>
            <div className="row" style={{ padding: '10px 12px', borderBottom: '1px solid var(--border)', gap: 6 }}>
              <span style={{ flex: 1, fontSize: 11.5, color: 'var(--ink-3)', fontWeight: 600, letterSpacing: '0.04em', textTransform: 'uppercase' }}>模块</span>
              <I name="cube" size={12} stroke="var(--ink-4)" />
              <I name="folder" size={12} stroke="var(--ink-4)" />
              <I name="link" size={12} stroke="var(--ink-4)" />
            </div>
            <div style={{ flex: 1, overflow: 'auto', padding: '8px 8px 12px' }}>
              {modules.map(m => (
                <div key={m.id} className={'ai-side-item' + (m.active ? ' active' : '')} style={{ height: 'auto', padding: '8px 10px', alignItems: 'flex-start' }}>
                  <div className="col" style={{ gap: 2, flex: 1, minWidth: 0 }}>
                    <span style={{ fontSize: 12.5, fontWeight: 500 }}>{m.name}</span>
                    <span className="tiny muted" style={{ fontSize: 10.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.sub}</span>
                  </div>
                  {m.count > 0 && <span className="count">{m.count}</span>}
                </div>
              ))}
            </div>
          </div>

          {/* Center: module tiles */}
          <div style={{ flex: 1, background: 'var(--bg)', overflow: 'auto' }}>
            {/* Hero info */}
            <div style={{ padding: '24px 28px 18px', background: 'var(--surface)', borderBottom: '1px solid var(--border)' }}>
              <div className="row" style={{ gap: 14, alignItems: 'flex-start' }}>
                <div style={{ width: 44, height: 44, borderRadius: 10, background: 'var(--surface-2)', display: 'grid', placeItems: 'center' }}>
                  <I name="trend" size={20} />
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 22, fontWeight: 600, letterSpacing: '-0.02em' }}>天猫店铺链接下滑分析</div>
                  <div className="tiny muted" style={{ marginTop: 4 }}>tmall-link-decline · 示例品牌传统电商运营部</div>
                </div>
                <div style={{ borderLeft: '1px solid var(--border)', paddingLeft: 16 }}>
                  <div className="tiny muted">可发布性</div>
                  <div className="row" style={{ gap: 6, marginTop: 4 }}>
                    <span className="ai-pill" style={{ fontSize: 10.5 }}>未检查</span>
                    <button className="ai-btn sm">运行质检</button>
                  </div>
                </div>
              </div>
            </div>

            {/* Module tiles grid */}
            <div style={{ padding: 24, display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12 }}>
              {tiles.map(t => (
                <div key={t.id} className="ai-card" style={{ padding: 16, gridColumn: t.wide ? 'span 2' : 'auto', cursor: 'pointer' }}>
                  <div className="row" style={{ gap: 8 }}>
                    <I name={t.icon} size={14} stroke="var(--ink-2)" />
                    <span style={{ fontSize: 13.5, fontWeight: 600, flex: 1 }}>{t.name}</span>
                    <span className="ai-pill" style={{ fontSize: 10.5 }}>{t.state}</span>
                  </div>
                  <div className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>{t.hint}</div>
                </div>
              ))}
            </div>

            <div style={{ padding: '0 24px 24px', textAlign: 'center', color: 'var(--ink-4)', fontSize: 12 }}>
              点击卡片进入模块编辑 · 右侧 AI 助手可协助优化 · 切换「源码」直接编辑 SKILL.md
            </div>
          </div>

          {/* Right: AI assistant */}
          <div style={{ width: 320, flex: '0 0 320px', borderLeft: '1px solid var(--border)', background: 'var(--surface)', display: 'flex', flexDirection: 'column' }}>
            <div className="row" style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', gap: 6 }}>
              <span className="ai-pill" style={{ background: 'var(--ink-1)', color: 'white', borderColor: 'var(--ink-1)' }}><I name="bolt" size={10} /> AI 对话</span>
              <span className="ai-pill">依赖图</span>
              <div style={{ flex: 1 }} />
              <I name="x" size={12} stroke="var(--ink-4)" />
            </div>

            <div style={{ padding: 14, flex: 1, overflow: 'auto' }}>
              <div className="row" style={{ gap: 6, marginBottom: 10 }}>
                <span className="ai-pill accent">@overview</span>
                <span className="ai-pill ok dot">跟踪中</span>
                <span style={{ marginLeft: 'auto' }}><I name="plus" size={11} stroke="var(--ink-4)" /></span>
              </div>

              <div className="ai-card" style={{ padding: 12, marginBottom: 12 }}>
                <div className="row" style={{ gap: 6, marginBottom: 6 }}>
                  <I name="check" size={12} stroke="var(--ok)" />
                  <span style={{ fontSize: 12, fontWeight: 500 }}>已分析 · 天猫店铺链接下滑分析</span>
                </div>
                <div className="row" style={{ gap: 4, flexWrap: 'wrap' }}>
                  <span className="ai-pill" style={{ fontSize: 10 }}>目标 0 字</span>
                  <span className="ai-pill" style={{ fontSize: 10 }}>规则 0 条</span>
                  <span className="ai-pill" style={{ fontSize: 10 }}>参数 0 个</span>
                  <span className="ai-pill" style={{ fontSize: 10 }}>测试 0 个</span>
                </div>
              </div>

              <div className="tiny muted" style={{ marginBottom: 8 }}>建议动作</div>
              <div className="col" style={{ gap: 4 }}>
                {aiSuggestions.map(s => (
                  <div key={s} className="row" style={{ padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 12.5, cursor: 'pointer', gap: 6 }}>
                    <span style={{ flex: 1 }}>{s}</span>
                    <I name="arrowr" size={11} stroke="var(--ink-4)" />
                  </div>
                ))}
              </div>

              <div className="tiny muted" style={{ marginTop: 16, textAlign: 'center' }}>或直接输入你想改的内容</div>
            </div>

            <div style={{ borderTop: '1px solid var(--border)', padding: 10 }}>
              <div className="row" style={{ gap: 6, padding: '6px 10px', border: '1px solid var(--border)', borderRadius: 8, background: 'var(--surface)' }}>
                <I name="attach" size={12} stroke="var(--ink-4)" />
                <span className="tiny muted" style={{ flex: 1 }}>输入指令或 /command…</span>
                <I name="upload" size={12} stroke="var(--ink-4)" />
                <I name="send" size={12} stroke="var(--ink-1)" />
              </div>
            </div>
          </div>
        </div>

        {/* Bottom status bar */}
        <div className="row" style={{ height: 24, padding: '0 14px', borderTop: '1px solid var(--border)', background: 'var(--surface-2)', fontSize: 11, color: 'var(--ink-3)', gap: 16 }}>
          <span className="row" style={{ gap: 4 }}><I name="check" size={10} stroke="var(--ok)" /> 无问题</span>
          <div style={{ flex: 1 }} />
          <span className="mono">SKILL.md</span>
          <span>只读</span>
          <span><span className="mono">⌃J</span> 面板</span>
          <span><span className="mono">⌃P</span> 命令</span>
        </div>
      </div>
    </Screen>
  );
}

window.SkillEditor = SkillEditor;