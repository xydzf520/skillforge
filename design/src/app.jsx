// Main app — assembles the design canvas.

function App() {
  return (
    <DesignCanvas>
      <DCSection id="hero" title="SkillForge · 企业级 AI 平台重设计" subtitle="Linear/Vercel-style mono · 双层导航 · 全部 14 屏 · 7 个模块">
        <DCArtboard id="brief" label="设计说明" width={520} height={1200}>
          <Brief />
        </DCArtboard>
      </DCSection>

      <DCSection id="standards" title="00 · 设计标准 · Direction A" subtitle="颜色 / 字体 / 间距 / 组件 / 布局 / 模式">
        <DCArtboard id="ds" label="设计标准 v1.0" width={1440} height={2540}>
          <DesignStandards />
        </DCArtboard>
      </DCSection>

      <DCSection id="hall" title="01 · 能力大厅 (全员入口)" subtitle="带搜索 + 4 维筛选的能力目录，每张卡可直接运行">
        <DCArtboard id="hall-a" label="能力大厅" width={1440} height={880}>
          <AbilityHallA />
        </DCArtboard>
        <DCArtboard id="hall-b" label="参考方向 · Editorial (备选)" width={1440} height={1180}>
          <AbilityHallB />
        </DCArtboard>
      </DCSection>

      <DCSection id="inbox" title="02 · 收件" subtitle="待办处理 / 报告流 / 报告详情 · 三个子视图">
        <DCArtboard id="inbox-1" label="2a · 待办列表" width={1440} height={900}>
          <Inbox />
        </DCArtboard>
        <DCArtboard id="inbox-2" label="2b · 报告 tab" width={1440} height={900}>
          <ReportsList />
        </DCArtboard>
        <DCArtboard id="inbox-3" label="2c · 报告详情 (店铺下滑日报)" width={1440} height={1100}>
          <ReportDetail />
        </DCArtboard>
      </DCSection>

      <DCSection id="skills" title="03 · Skills" subtitle="工作台 / 编辑器 / 应用门户 / 审核中心 · 四个子视图">
        <DCArtboard id="skills-1" label="3a · Skills 工作台" width={1440} height={900}>
          <Skills />
        </DCArtboard>
        <DCArtboard id="skills-2" label="3b · Skill 编辑器" width={1440} height={900}>
          <SkillEditor />
        </DCArtboard>
        <DCArtboard id="skills-3" label="3c · 应用门户 (按部门可执行 Skill)" width={1440} height={900}>
          <SkillsPortal />
        </DCArtboard>
        <DCArtboard id="skills-4" label="3d · 审核中心" width={1440} height={900}>
          <ReviewCenter />
        </DCArtboard>
      </DCSection>

      <DCSection id="tree" title="04 · 任务树" subtitle="节点状态 + 调度诊断">
        <DCArtboard id="tree-1" label="任务树 — 异常视图" width={1440} height={920}>
          <TaskTree />
        </DCArtboard>
      </DCSection>

      <DCSection id="agent" title="05 · Agent (按部门重组)" subtitle="三栏：部门 → 部门内 Agent (按类型) → Skill 调用范围">
        <DCArtboard id="agent-1" label="Agent · 部门视图" width={1440} height={920}>
          <Agent />
        </DCArtboard>
      </DCSection>

      <DCSection id="training" title="06 · 训练 (流水线)" subtitle="部门数据 → 清洗 → 训练 → 模型 → 评估 → 运行">
        <DCArtboard id="training-1" label="训练 — 流水线视图" width={1440} height={1180}>
          <Training />
        </DCArtboard>
      </DCSection>

      <DCSection id="admin" title="07 · 管理后台" subtitle="用户 / Codex 插件 / 系统配置 / Agent 终端 · 四个子视图">
        <DCArtboard id="admin-1" label="7a · 用户管理" width={1440} height={900}>
          <AdminUsers />
        </DCArtboard>
        <DCArtboard id="admin-2" label="7b · Codex 插件" width={1440} height={900}>
          <AdminCodex />
        </DCArtboard>
        <DCArtboard id="admin-3" label="7c · 系统配置" width={1440} height={900}>
          <AdminConfig />
        </DCArtboard>
        <DCArtboard id="admin-4" label="7d · Agent 终端 (按部门覆盖)" width={1440} height={1000}>
          <AdminAgentTerminals />
        </DCArtboard>
      </DCSection>

      <DCSection id="l3" title="08 · 三级页面 (抽屉 / 详情)" subtitle="点击二级页面里的某一行 / 卡片 / 按钮，进入处理状态">
        <DCArtboard id="l3-run"   label="L3-A · 能力运行抽屉 (从能力大厅点 ▶)" width={1440} height={900}>
          <RunDrawer />
        </DCArtboard>
        <DCArtboard id="l3-todo"  label="L3-B · 待办详情 · 商品诊断卡 (从收件点行)" width={1440} height={1100}>
          <TodoDetail />
        </DCArtboard>
        <DCArtboard id="l3-skill" label="L3-C · Skill 模块编辑 · 规则 (从 Skill 编辑器点卡片)" width={1440} height={900}>
          <SkillModuleEdit />
        </DCArtboard>
        <DCArtboard id="l3-train" label="L3-D · 训练任务详情 (从训练点任务)" width={1440} height={1000}>
          <TrainingJobDetail />
        </DCArtboard>
        <DCArtboard id="l3-user"  label="L3-E · 用户详情 (从用户列表点行)" width={1440} height={920}>
          <UserDetail />
        </DCArtboard>
      </DCSection>
    </DesignCanvas>
  );
}

function Brief() {
  const Box = ({ title, children }) => (
    <div style={{ borderTop: '1px solid var(--border)', paddingTop: 14, marginTop: 14 }}>
      <div style={{ fontSize: 10.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--ink-4)', marginBottom: 6 }}>{title}</div>
      {children}
    </div>
  );

  return (
    <div className="ai-app" style={{ width: 520, height: 1180, padding: 36, background: 'var(--surface)', borderRadius: 14, border: '1px solid var(--border)', overflow: 'hidden' }}>
      <div className="row" style={{ gap: 8, marginBottom: 24 }}>
        <img className="ai-brand-mark" src="../web/public/brand/mark.svg" alt="" style={{ width: 28, height: 28 }} />
        <div>
          <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.02em' }}>SkillForge</div>
          <div className="tiny muted">企业 AI 工作台与技能协作平台</div>
        </div>
      </div>

      <h2 style={{ fontSize: 28, fontWeight: 600, letterSpacing: '-0.025em', lineHeight: 1.15, margin: 0 }}>
        从 "什么都能点开" 到<br />
        <span style={{ color: 'var(--ink-3)' }}>"每个角色看到自己的那一面"</span>
      </h2>

      <Box title="问题诊断 (来自旧版)">
        <ul style={{ margin: 0, paddingLeft: 16, fontSize: 13, lineHeight: 1.7, color: 'var(--ink-2)' }}>
          <li>顶部 6 个 Tab 平铺，员工和管理员看同一套，能力大厅 ≈ 被压成最小入口</li>
          <li>Agent 把不同部门混在一栏，看不出谁属于谁、能调哪些 Skill</li>
          <li>训练页围绕 GPU/资源池展示，没有讲清「过程数据 → 模型 → 运行」的主线</li>
          <li>收件用彩色大按钮 + 视觉拥挤的数据卡，处理 56 条就疲劳</li>
          <li>整体配色饱和，缺乏层级，不利于密集运营场景</li>
        </ul>
      </Box>

      <Box title="设计原则">
        <div className="col" style={{ gap: 10, fontSize: 13 }}>
          <div className="row" style={{ gap: 8, alignItems: 'flex-start' }}><span style={{ color: 'var(--ink-4)', fontFamily: 'var(--font-mono)', fontSize: 11, marginTop: 2 }}>01</span><span><b>角色分流</b> — 员工只看 能力大厅 + 收件；管理员看完整管线</span></div>
          <div className="row" style={{ gap: 8, alignItems: 'flex-start' }}><span style={{ color: 'var(--ink-4)', fontFamily: 'var(--font-mono)', fontSize: 11, marginTop: 2 }}>02</span><span><b>双层导航</b> — 顶部模块 + 左侧二级，部门/视图/状态在左侧自然展开</span></div>
          <div className="row" style={{ gap: 8, alignItems: 'flex-start' }}><span style={{ color: 'var(--ink-4)', fontFamily: 'var(--font-mono)', fontSize: 11, marginTop: 2 }}>03</span><span><b>单色克制</b> — 一种暖中性灰白，仅在状态/部门点缀克制色，避免数据 slop</span></div>
          <div className="row" style={{ gap: 8, alignItems: 'flex-start' }}><span style={{ color: 'var(--ink-4)', fontFamily: 'var(--font-mono)', fontSize: 11, marginTop: 2 }}>04</span><span><b>密集但可读</b> — 13px 主字、紧表格、Geist + PingFang 数字等宽对齐</span></div>
        </div>
      </Box>

      <Box title="重构的两个模块">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <div style={{ padding: 12, background: 'var(--surface-2)', borderRadius: 8 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>Agent</div>
            <div className="tiny muted" style={{ lineHeight: 1.6 }}>三栏：部门 → Agent (按类型分组) → 该 Agent 可调用的 Skill。让"谁属于谁"和"能干什么"一眼可见</div>
          </div>
          <div style={{ padding: 12, background: 'var(--surface-2)', borderRadius: 8 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 4 }}>训练</div>
            <div className="tiny muted" style={{ lineHeight: 1.6 }}>把页面骨架改成 6 段流水：部门数据 → 清洗 → 训练 → 模型 → 评估 → 运行。资源池退为辅助</div>
          </div>
        </div>
      </Box>

      <Box title="本画布 · 14 屏 / 7 模块">
        <ul style={{ margin: 0, paddingLeft: 16, fontSize: 12.5, lineHeight: 1.7, color: 'var(--ink-2)' }}>
          <li>00 设计标准 v1.0</li>
          <li>01 能力大厅 · A 主方向 + B 备选</li>
          <li>02 收件 · 待办 / 报告 tab / 报告详情</li>
          <li>03 Skills · 工作台 / 编辑器 / 应用门户 / 审核中心</li>
          <li>04 任务树</li>
          <li>05 Agent · 按部门重组</li>
          <li>06 训练 · 流水线</li>
          <li>07 管理后台 · 用户 / Codex / 系统配置 / Agent 终端</li>
        </ul>
        <div className="tiny muted" style={{ marginTop: 10 }}>每个画板 1:1 高保真静态稿，右上角可全屏。</div>
      </Box>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
