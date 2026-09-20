// Shell — TopBar (role-aware), optional left sidebar.
// EMPLOYEE shell shows only 能力大厅 + 收件. ADMIN shell shows the full set.

const { useState } = React;

function TopBar({ role = 'admin', active }) {
  const adminTabs = [
    { id: 'home',     label: '能力大厅', icon: 'spark' },
    { id: 'skills',   label: 'Skills',  icon: 'cube' },
    { id: 'tree',     label: '任务树',  icon: 'tree' },
    { id: 'agent',    label: 'Agent',   icon: 'bot' },
    { id: 'train',    label: '训练',    icon: 'beaker' },
    { id: 'inbox',    label: '收件',    icon: 'inbox', badge: 56 },
    { id: 'admin',    label: '管理后台', icon: 'shield' },
  ];
  const employeeTabs = [
    { id: 'home',     label: '能力大厅', icon: 'spark' },
    { id: 'inbox',    label: '收件',    icon: 'inbox', badge: 3 },
  ];
  const tabs = role === 'employee' ? employeeTabs : adminTabs;

  return (
    <div className="ai-topbar">
      <div className="ai-brand">
        <img className="ai-brand-mark" src="../web/public/brand/mark.svg" alt="" />
        <span>SkillForge</span>
        <span className="ai-pill" style={{ marginLeft: 6, fontSize: 10, height: 17, padding: '0 5px' }}>
          {role === 'employee' ? '员工' : '管理员'}
        </span>
      </div>

      <div className="ai-topnav">
        {tabs.map(t => (
          <div key={t.id} className={'ai-topnav-item' + (active === t.id ? ' active' : '')}>
            <I name={t.icon} size={13} />
            <span>{t.label}</span>
            {t.badge ? <span className="ai-pill" style={{ height: 16, padding: '0 5px', fontSize: 10, marginLeft: 2 }}>{t.badge}</span> : null}
          </div>
        ))}
      </div>

      <div className="ai-topbar-spacer" />

      <div className="ai-topbar-search">
        <I name="search" size={13} />
        <span>搜索能力、Skill、报告…</span>
        <span style={{ marginLeft: 'auto', fontSize: 10.5, color: 'var(--ink-4)', border: '1px solid var(--border)', padding: '1px 4px', borderRadius: 3 }}>⌘K</span>
      </div>

      <div className="ai-iconbtn" title="文档与帮助"><I name="doc" size={14} /></div>
      <div className="ai-iconbtn" title="运行历史"><I name="hist" size={14} /></div>
      <div className="ai-iconbtn" title="通知"><I name="bell" size={14} /><span className="dot" /></div>

      <div className="ai-vr" style={{ height: 20, margin: '0 4px' }} />

      <div className="row" style={{ gap: 6, padding: '4px 6px 4px 4px', borderRadius: 6, cursor: 'pointer' }}>
        <div className="ai-avatar">管</div>
        <div className="col" style={{ gap: 0, lineHeight: 1.1 }}>
          <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--ink-1)' }}>{role === 'employee' ? '张文' : '管理员'}</span>
          <span style={{ fontSize: 10.5, color: 'var(--ink-4)' }}>{role === 'employee' ? '员工' : '系统管理员'}</span>
        </div>
        <I name="chev" size={11} stroke="var(--ink-4)" />
      </div>
    </div>
  );
}

// A 1440x900 screen wrapper used inside DCArtboard.
function Screen({ children, role = 'admin', active, className = '', height = 900 }) {
  return (
    <div className={'ai-app ' + className} style={{ width: 1440, height, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <TopBar role={role} active={active} />
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {children}
      </div>
    </div>
  );
}

Object.assign(window, { TopBar, Screen });
