import { expect, test, type Page } from '@playwright/test'
import { installMockApi } from './mock-api'

const summary = {
  scope: 'global',
  events: { total: 18 },
  artifacts: {
    by_kind: { report_summary: 4, training_sample: 3, agent_memory: 2 },
    by_status: { ready: 3, materialized: 6 },
  },
  knowledge: { indexed: 4 },
  training: { samples: 3 },
  agent: { used_context_edges: 5, memories: 2 },
  candidates: { by_target: { skill: 2, sf: 1 }, by_status: { open: 2, reviewing: 1 } },
}

const topology = {
  stages: [
    { key: 'source', title: '数据源', caption: '运行 / SF / Agent / 训练', count: 18, tone: 'source', nodes: [{ id: 'source:execution_run', label: 'Skill 运行', meta: '8 条 · 最新 2026-06-01 15:00', icon: 'play', heat: 0.7, tone: 'source', entity_type: 'source_type', entity_id: 'execution_run', payload: { count: 8 } }] },
    { key: 'event', title: '标准记录', caption: '统一脱敏、去重、打分', count: 18, tone: 'event', nodes: [{ id: 'event:e1', label: 'decision.completed', meta: '决策反馈 · 2026-06-01 15:01', icon: 'spark', heat: 0.8, tone: 'event', entity_type: 'learning_event', entity_id: 'e1', payload: { id: 'e1', source_type: 'execution_run', source_id: 'run-learning-1' } }] },
    { key: 'artifact', title: '学习资产', caption: '知识 / 样本 / 记忆 / 建议', count: 9, tone: 'artifact', nodes: [{ id: 'artifact:a1', label: '链接下滑结果复核', meta: '报告摘要 · 已生成', icon: 'doc', heat: 0.9, tone: 'artifact', payload: { id: 'a1' } }] },
    { key: 'sink', title: '自动生成知识', caption: '知识库 / 训练池 / Agent 记忆', count: 9, tone: 'sink', nodes: [{ id: 'sink:knowledge_document:a1', label: '已生成', meta: 'knowledge_document · doc1', icon: 'database', heat: 0.8, tone: 'sink', payload: { artifact_id: 'a1' } }] },
    { key: 'candidate', title: '迭代建议', caption: 'Skill / SF / Agent 自动建议', count: 3, tone: 'candidate', nodes: [{ id: 'candidate:c1', label: '优化关键词诊断 Skill', meta: 'skill · 开放 · R2', icon: 'edit', heat: 0.75, tone: 'candidate', payload: { id: 'c1' } }] },
    { key: 'review', title: '审核流程', caption: '待办评审 / 训练 / 发布控制', count: 1, tone: 'review', nodes: [{ id: 'review:c1', label: '评审中', meta: '优化关键词诊断 Skill · ldr1', icon: 'inbox', heat: 0.6, tone: 'review', payload: { id: 'c1' } }] },
  ],
  connectors: [
    { from: 'source', to: 'event', count: 18, label: '同步', relation: 'captured_as_event', intensity: 0.9, animated: true },
    { from: 'event', to: 'artifact', count: 9, label: '提取', relation: 'produced', intensity: 0.45, animated: true },
    { from: 'artifact', to: 'sink', count: 9, label: '生成知识', relation: 'materialized_to_sink', intensity: 0.45, animated: true },
    { from: 'sink', to: 'candidate', count: 3, label: '改进建议', relation: 'fed_back_to_candidate', intensity: 0.25, animated: true },
    { from: 'candidate', to: 'review', count: 1, label: '审核流程', relation: 'governed_by', intensity: 0.1, animated: true },
  ],
  governance: { raw_payload_returned: false, scope: 'global' },
}

const bottlenecks = {
  summary: { total: 2, ready_artifacts: 1, open_candidates: 1 },
  sections: [
    { key: 'ready_artifacts', title: '待生成知识内容', count: 1, severity: 'warning', action: 'materialize', items: [{ id: 'a1', artifact_kind: 'report_summary', title: '链接下滑结果复核', status: 'ready' }] },
    { key: 'open_candidates', title: '待审核建议', count: 1, severity: 'warning', action: 'create_review', items: [{ id: 'c1', target_type: 'skill', title: '优化关键词诊断 Skill', status: 'open' }] },
  ],
  governance: { raw_payload_returned: false },
}

const journeys = {
  items: [
    {
      id: 'journey:e1',
      event_id: 'e1',
      title: '搜索访客下降，建议优化关键词和价格力。',
      source_type: 'execution_run',
      source_id: 'run-learning-1',
      skill_id: 'learn-skill',
      state: 'flowing',
      progress: 0.72,
      blockers: ['candidate_open'],
      last_at: '2026-06-01T15:08:00',
      steps: [
        { stage: 'source', label: 'Skill 运行', entity_type: 'run', entity_id: 'run-learning-1', status: 'completed', meta: 'learn-skill' },
        { stage: 'event', label: 'skill.run.completed', entity_type: 'learning_event', entity_id: 'e1', status: 'captured', meta: 'Q 0.9' },
        { stage: 'artifact', label: '链接下滑结果复核', entity_type: 'learning_artifact', entity_id: 'a1', status: 'materialized', meta: '报告摘要 · auto_index' },
        { stage: 'sink', label: 'knowledge_document', entity_type: 'knowledge_document', entity_id: 'doc1', status: 'materialized', meta: '知识库' },
        { stage: 'candidate', label: '优化关键词诊断 Skill', entity_type: 'improvement_candidate', entity_id: 'c1', status: 'open', meta: 'skill · R2' },
      ],
    },
  ],
  total: 1,
  state_counts: { flowing: 1 },
  blocker_counts: { candidate_open: 1 },
  governance: { raw_payload_returned: false },
}

async function installLearningFlowMocks(page: Page) {
  await page.route('**/api/learning/summary**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(summary) }))
  await page.route('**/api/learning/flow-topology**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(topology) }))
  await page.route('**/api/learning/flow-journeys**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(journeys) }))
  await page.route('**/api/learning/bottlenecks**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(bottlenecks) }))
  await page.route('**/api/learning/flow-graph**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ nodes: [], edges: [{ id: 1, source: 'run:r1', target: 'learning_artifact:a1', relation: 'produced' }] }) }))
  await page.route('**/api/learning/events**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{ id: 'e1', event_type: 'decision.completed', source_type: 'decision_log', source_id: 'd1', redacted_summary: '报告有效。', status: 'captured', quality_score: 0.9, created_at: '2026-06-01T15:00:00' }], total: 1 }) }))
  await page.route('**/api/learning/artifacts**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{ id: 'a1', artifact_kind: 'report_summary', title: '链接下滑结果复核', summary: '搜索访客下降。', status: 'materialized', quality_score: 0.9 }], total: 1 }) }))
  await page.route('**/api/learning/candidates**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{ id: 'c1', target_type: 'skill', title: '优化关键词诊断 Skill', proposal: '整理为标准 Skill。', status: 'open', risk_level: 'R2', priority_score: 0.8 }], total: 1 }) }))
  await page.route('**/api/learning/automation-status**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ automation: { enabled: true, status: 'succeeded', interval_seconds: 300, finished_at: '2026-06-01T15:10:00' }, backlog: { auto_materializable: 2, review_required: 1 }, jobs: { by_status: { failed: 0 } }, latest: { event_at: '2026-06-01T15:05:00', automation_run: { id: 'lar1', status: 'succeeded' } }, automation_runs: [{ id: 'lar1', trigger_type: 'scheduled', status: 'succeeded', finished_at: '2026-06-01T15:10:00', result: { materialized: 2 } }], governance: { raw_payload_returned: false } }) }))
  await page.route('**/api/learning/training-manifest**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ dataset_ref: 'learning-artifacts://visible/training/latest', sample_total: 3, skills: [{ skill_id: 'learn-skill', sample_count: 3 }], governance: { raw_payload_returned: false } }) }))
}

test.describe('智能流程数据流', () => {
  test.beforeEach(async ({ page }) => {
    await installMockApi(page)
    await installLearningFlowMocks(page)
  })

  test('处理式拓扑画布按真实阶段和连接器渲染', async ({ page }) => {
    const pageErrors: string[] = []
    page.on('pageerror', (error) => pageErrors.push(error.message))
    page.on('console', (message) => {
      if (message.type() === 'error') pageErrors.push(message.text())
    })

    await page.goto('/learning-flow')

    await expect(page.locator('.pulse-overview-board')).toBeVisible()
    await expect(page.locator('.learning-workspace')).toHaveCSS('padding-left', '28px')
    await expect(page.locator('.learning-workspace')).toHaveCSS('padding-right', '28px')
    await expect(page.locator('.pulse-overview-board')).toHaveCSS('padding-top', '16px')
    await expect(page.locator('.pulse-overview-board')).toHaveCSS('border-top-left-radius', '8px')
    await expect(page.locator('.ai-side-menu')).toHaveCSS('padding-left', '16px')
    await expect(page.locator('.pulse-overview-board')).toContainText('概览')
    await expect(page.locator('.pulse-decision-strip')).toContainText('建议操作')
    await expect(page.locator('.pulse-decision-item')).toHaveCount(4)
    await expect(page.locator('.pulse-automation-strip')).toBeVisible()
    await expect(page.locator('.pulse-automation-strip')).toContainText('自动化运行态')
    await expect(page.locator('.pulse-automation-strip')).toContainText('待生成知识')
    await expect(page.locator('.pulse-automation-card')).toHaveCount(4)
    await expect(page.locator('.pulse-value-panel')).toContainText('关键有效信息')
    await expect(page.locator('.pulse-value-panel')).toContainText('价值评估')
    await expect(page.locator('.pulse-value-panel')).toContainText('建议立即处理')
    await expect(page.locator('.pulse-overview-card')).toHaveCount(4)
    await expect(page.locator('.pulse-overview-step')).toHaveCount(4)
    await expect(page.locator('.global-flow-board')).toBeHidden()
    await expect(page.locator('#panel-run-brief')).toBeHidden()
    await expect(page.locator('.ai-side-hud')).toBeHidden()
    await expect(page.locator('.ai-operation-center')).toBeVisible()
    await expect(page.locator('.ai-operation-center')).toContainText('自动处理建议')
    await expect(page.locator('.ai-operation-center')).toContainText('等待人工确认')
    await expect(page.locator('.ai-operation-center')).toContainText('处理依据')
    await expect(page.locator('.ai-operation-center')).toContainText('权限与安全')
    await expect(page.locator('.learning-page')).toHaveClass(/motion-auto/)
    await expect(page.locator('.ai-side-quick-controls')).toBeVisible()
    await expect(page.locator('.ai-side-quick-controls button')).toHaveCount(4)
    await expect(page.locator('.ai-side-quick-controls')).toContainText('标准')
    await expect(page.locator('.ai-side-quick-controls')).toContainText('阅读')
    await expect(page.locator('.ai-side-quick-controls')).toContainText('追踪')
    await expect(page.locator('.ai-side-quick-controls')).toContainText('详细视图')
    await expect(page.locator('.ai-operation-status')).toContainText('上次检查')
    await expect(page.locator('.ai-operation-status')).toContainText('下次检查')
    await expect(page.locator('.ai-operation-status')).toContainText('当前压力')
    await expect(page.locator('.ai-operation-status-item')).toHaveCount(3)
    await expect(page.locator('.ai-operation-rail')).toHaveCount(0)
    await expect(page.locator('.ai-operation-decision-card')).toHaveCount(3)
    await expect(page.locator('.ai-operation-decision')).toContainText('下一步操作')
    await expect(page.locator('.ai-operation-decision')).toContainText('主要依据')
    await expect(page.locator('.ai-operation-decision')).toContainText('影响范围')
    await expect(page.locator('.ai-operation-queue')).toContainText('待生成知识')
    await expect(page.locator('.ai-operation-queue')).toContainText('待审核')
    await expect(page.locator('.ai-operation-queue-chip')).toHaveCount(3)
    await expect(page.locator('.ai-simple-metrics')).toHaveCount(0)
    await expect(page.locator('.ai-simple-priority')).toBeVisible()
    await expect(page.locator('.ai-simple-priority')).toContainText('处理清单')
    await expect(page.locator('.ai-simple-priority-item')).toHaveCount(3)
    await expect(page.locator('.ai-simple-priority-item.primary')).toHaveCount(1)
    await expect(page.locator('.ai-mission-steps')).toBeHidden()
    await expect(page.locator('.ai-simple-rationale')).toHaveCount(0)
    await expect(page.locator('.ai-simple-plan')).toHaveCount(0)
    await expect(page.locator('.ai-sticky-command')).toBeHidden()
    await page.locator('.pulse-overview-actions button').filter({ hasText: '查看详细视图' }).click()
    await expect(page.locator('.global-flow-board')).toBeVisible()
    await expect(page.locator('#panel-run-brief')).toBeVisible()
    for (const selector of ['#panel-global-map', '#panel-run-brief', '#panel-diagnostics', '#panel-filters', '#panel-automation', '#panel-bottleneck', '#panel-flow-stage', '#panel-journey', '#panel-river']) {
      await expect(page.locator(selector)).toHaveCSS('padding-left', '16px')
      await expect(page.locator(selector)).toHaveCSS('padding-right', '16px')
    }
    await expect(page.locator('.ai-side-hud')).toBeVisible()
    await expect(page.locator('.ai-operation-center')).toBeHidden()
    await expect(page.locator('.ai-simple-priority')).toBeHidden()
    await expect(page.getByText('自动化工作台')).toBeVisible()
    await expect(page.locator('.ai-side-quick-controls')).toContainText('返回概览')
    await expect(page.locator('.ai-side-orbit')).toBeHidden()
    await page.locator('.ai-side-quick-controls button').filter({ hasText: '追踪' }).click()
    await expect(page.locator('.learning-page')).toHaveClass(/motion-trace/)
    await expect(page.locator('.ai-side-orbit')).toBeVisible()
    await page.locator('.ai-side-quick-controls button').filter({ hasText: '标准' }).click()
    await expect(page.locator('.learning-page')).toHaveClass(/motion-auto/)
    await expect(page.locator('.ai-side-orbit')).toBeHidden()
    await expect(page.locator('.ai-side-more-tools')).toContainText('展开更多工具')
    await expect(page.locator('#ai-side-orchestrator')).toBeHidden()
    await expect(page.locator('#ai-side-motion')).toBeHidden()
    await page.locator('.ai-side-more-tools button').click()
    await expect(page.locator('.ai-side-menu')).toHaveClass(/tools-expanded/)
    await expect(page.locator('.ai-side-more-tools')).toContainText('收起辅助工具')
    await expect(page.locator('#ai-side-orchestrator')).toBeVisible()
    await expect(page.locator('.ai-side-scroll-progress')).toBeVisible()
    await expect(page.locator('.ai-side-hud-card')).toHaveCount(4)
    await expect(page.locator('.ai-side-hud-card').first()).toHaveCSS('padding-left', '8px')
    await expect(page.locator('.ai-side-hud-card').first()).toHaveCSS('border-top-left-radius', '8px')
    await expect(page.locator('.ai-autopilot-stage').first()).toHaveCSS('animation-name', 'none')
    await expect(page.locator('.ai-side-hud')).toContainText('最优下一步')
    await page.locator('.ai-side-hud-card').filter({ hasText: '最优下一步' }).click()
    await expect(page.locator('.arco-drawer')).toContainText('ai_side_hud')
    await page.keyboard.press('Escape')
    await expect(page.locator('.arco-drawer')).toBeHidden()
    await expect(page.locator('.ai-mission-brief')).toContainText('当前建议')
    await expect(page.locator('.ai-mission-brief')).toContainText('数据同步')
    await expect(page.locator('.ai-mission-brief')).toContainText('风险判断')
    await expect(page.locator('.ai-mission-brief')).toContainText('建议操作')
    await expect(page.locator('.ai-mission-brief')).toContainText('结果复核')
    await expect(page.locator('.ai-mission-step')).toHaveCount(4)
    await expect(page.locator('#ai-side-orchestrator')).toContainText('执行步骤')
    await expect(page.locator('.ai-command-step')).toHaveCount(4)
    await page.locator('.ai-mission-summary').click()
    await expect(page.locator('.arco-drawer')).toContainText('ai_mission_brief')
    await page.keyboard.press('Escape')
    await expect(page.locator('.arco-drawer')).toBeHidden()
    await expect(page.locator('.ai-sticky-command')).toBeVisible()
    await expect(page.locator('.ai-sticky-command')).toContainText('建议操作')
    await expect(page.locator('.ai-sticky-command-confidence')).toContainText('可信度')
    await expect(page.locator('.ai-sticky-command-confidence')).toContainText('%')
    await expect(page.locator('.ai-sticky-command-preview')).toContainText('操作预览')
    await expect(page.locator('.ai-sticky-command-preview')).toContainText('定位')
    await expect(page.locator('.ai-sticky-command-preview')).toContainText('聚焦')
    await expect(page.locator('.ai-sticky-command-preview')).toContainText('安全说明')
    await expect(page.locator('.ai-sticky-command-reason')).toContainText('建议依据')
    await expect(page.locator('.ai-sticky-command-reason')).toContainText('证据')
    await expect(page.locator('.ai-sticky-command-reason')).toContainText('收益')
    await expect(page.locator('.ai-execution-guardrail')).toContainText('执行前说明')
    await expect(page.locator('.ai-guardrail-card')).toHaveCount(4)
    await expect(page.locator('.ai-execution-guardrail')).toContainText('影响范围')
    await expect(page.locator('.ai-execution-guardrail')).toContainText('不会自动发布或变更上线状态')
    await page.locator('.ai-guardrail-card').filter({ hasText: '影响范围' }).click()
    await expect(page.locator('.arco-drawer')).toContainText('ai_execution_guardrail')
    await expect(page.locator('.ai-action-trail')).toContainText('检查 · 影响范围')
    await page.keyboard.press('Escape')
    await expect(page.locator('.arco-drawer')).toBeHidden()
    await page.locator('.ai-sticky-command').click()
    await expect(page.locator('.arco-drawer')).toContainText('ai_decision_queue')
    await expect(page.locator('.ai-action-trail')).toContainText('待生成知识')
    await page.keyboard.press('Escape')
    await expect(page.locator('.arco-drawer')).toBeHidden()
    await expect(page.locator('.ai-side-index-button')).toHaveCount(8)
    const rootCauseIndex = page.locator('.ai-side-index-button').filter({ hasText: '根因' })
    await expect(rootCauseIndex).toBeVisible()
    await rootCauseIndex.click()
    await expect(rootCauseIndex).toHaveClass(/active/)
    await expect(page.getByText('原因分析')).toBeVisible()
    const focusIndex = page.locator('.ai-side-index-button').filter({ hasText: '聚焦' })
    await focusIndex.click()
    await expect(focusIndex).toHaveClass(/active/)
    await expect(page.locator('.ai-command-plan')).toHaveCount(1)
    await expect(page.locator('.ai-command-step')).toHaveCount(4)
    await page.locator('.ai-command-step').filter({ hasText: '安全说明' }).click()
    await expect(page.locator('.ai-action-trail')).toContainText('安全说明')
    await expect(page.locator('.arco-drawer')).toContainText('ai_command_plan')
    await page.keyboard.press('Escape')
    await expect(page.locator('.arco-drawer')).toBeHidden()
    await expect(page.getByRole('navigation', { name: '学习页面面板跳转' })).toBeVisible()
    await expect(page.locator('#ai-side-autopilot').getByText('自动处理建议')).toBeVisible()
    await expect(page.getByText('系统状态')).toBeVisible()
    await expect(page.locator('.ai-heartbeat-step')).toHaveCount(3)
    await page.locator('.ai-heartbeat-core').click()
    await expect(page.locator('.ai-action-trail')).toContainText('自动处理状态')
    await expect(page.locator('.arco-drawer')).toContainText('ai_heartbeat')
    await page.keyboard.press('Escape')
    await expect(page.locator('.arco-drawer')).toBeHidden()
    await expect(page.locator('.ai-autopilot-stage')).toHaveCount(4)
    await expect(page.locator('.ai-autopilot-stage').first()).toHaveCSS('border-top-left-radius', '8px')
    await expect(page.locator('.ai-autopilot-stage').first()).toHaveCSS('padding-left', '8px')
    await expect(page.locator('.ai-autopilot-primary')).toContainText('查看待处理项')
    await expect(page.getByText('风险判断证据')).toBeVisible()
    await expect(page.locator('.ai-evidence-card')).toHaveCount(4)
    await expect(page.locator('.ai-evidence-card').filter({ hasText: '审核待办' })).toBeVisible()
    await expect(page.getByText('原因分析')).toBeVisible()
    await expect(page.locator('.ai-rootcause-card')).toHaveCount(2)
    await page.locator('.ai-rootcause-card').filter({ hasText: '审核未完成' }).click()
    await expect(page.locator('.ai-action-trail')).toContainText('审核未完成')
    await expect(page.locator('.arco-drawer')).toContainText('ai_root_cause')
    await page.keyboard.press('Escape')
    await expect(page.locator('.arco-drawer')).toBeHidden()
    await expect(page.getByText('影响评估')).toBeVisible()
    await expect(page.locator('.ai-impact-card')).toHaveCount(3)
    await page.locator('.ai-impact-card').filter({ hasText: '知识生成收益' }).click()
    await expect(page.locator('.ai-action-trail')).toContainText('知识生成收益')
    await expect(page.locator('.arco-drawer')).toContainText('ai_impact_forecast')
    await page.keyboard.press('Escape')
    await expect(page.locator('.arco-drawer')).toBeHidden()
    await expect(page.getByText('优先级排序')).toBeVisible()
    await expect(page.locator('.ai-heat-card')).toHaveCount(4)
    await expect(page.locator('.ai-heat-card').filter({ hasText: '审核风险' })).toBeVisible()
    await expect(page.getByText('建议优先级')).toBeVisible()
    await expect(page.locator('.ai-decision-item')).toHaveCount(4)
    await page.locator('.ai-decision-item').filter({ hasText: '优先处理项' }).click()
    await expect(page.locator('.ai-action-trail')).toContainText('优先处理项')
    await expect(page.locator('.arco-drawer')).toContainText('ai_decision_queue')
    await page.keyboard.press('Escape')
    await expect(page.locator('.arco-drawer')).toBeHidden()
    await expect(page.getByText('快捷处理方案')).toBeVisible()
    await expect(page.locator('.ai-playbook')).toHaveCount(4)
    await expect(page.locator('.ai-playbook').filter({ hasText: '优先处理风险' })).toBeVisible()
    await page.locator('.ai-playbook').filter({ hasText: '优先处理风险' }).click()
    await expect(page.locator('.ai-action-trail')).toContainText('优先处理风险')
    await expect(page.locator('.learning-page.motion-trace')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.locator('.arco-drawer')).toBeHidden()
    await expect(page.getByText('显示模式')).toBeVisible()
    await expect(page.locator('.ai-motion-mode button')).toHaveCount(3)
    await expect(page.locator('.ai-motion-mode')).toContainText('标准')
    await expect(page.locator('.ai-motion-mode')).toContainText('阅读')
    await expect(page.locator('.ai-motion-mode')).toContainText('追踪')
    await expect(page.locator('.ai-motion-mode button').first()).toHaveCSS('padding-left', '8px')
    await expect(page.locator('.ai-motion-mode button').first()).toHaveCSS('border-top-left-radius', '8px')
    await expect(page.getByText('查看视角')).toBeVisible()
    await expect(page.locator('.ai-perspective')).toHaveCount(5)
    await expect(page.locator('.ai-perspective').first()).toHaveCSS('padding-left', '8px')
    await expect(page.locator('.ai-perspective').first()).toHaveCSS('border-top-left-radius', '8px')
    await expect(page.getByText('聚焦控制')).toBeVisible()
    await expect(page.locator('.ai-focus-shortcut')).toHaveCount(4)
    await page.locator('.ai-focus-shortcut').filter({ hasText: '待审核' }).click()
    await expect(page.locator('.ai-focus-chips')).toContainText('建议状态')
    await expect(page.locator('.ai-action-trail')).toContainText('待审核')
    await page.keyboard.press('Escape')
    await expect(page.locator('.arco-drawer')).toBeHidden()
    await expect(page.locator('.ai-motion-meter')).toHaveCount(3)
    await expect(page.locator('#ai-side-meter')).toContainText('显示状态')
    await expect(page.locator('.ai-side-route')).toBeVisible()
    await expect(page.locator('.ai-side-route-step')).toHaveCount(5)
    await page.locator('.ai-side-nav button').filter({ hasText: '处理流程' }).click()
    await expect(page.getByText('数据处理流程')).toBeVisible()
    await expect(page.getByText('自动处理引擎')).toBeVisible()
    await expect(page.getByText('最近自动处理')).toBeVisible()
    await expect(page.getByText('待处理问题')).toBeVisible()
    await expect(page.getByRole('heading', { name: '处理记录' })).toBeVisible()
    await expect(page.getByText('建议待审核', { exact: true })).toBeVisible()
    await expect(page.locator('.flow-stage')).toHaveCount(6)
    await expect(page.locator('.flow-loop-label')).toHaveText([
      '同步 · 18',
      '提取 · 9',
      '生成知识 · 9',
      '改进建议 · 3',
      '审核流程 · 1',
    ])
    await expect(page.locator('.flow-stage').filter({ hasText: '审核流程' })).toBeVisible()

    await page.locator('.flow-node').first().click()
    await expect(page.locator('.detail-json')).toContainText('source:execution_run')
    await expect(page.locator('.ai-side-context')).toContainText('当前观察对象')
    await expect(page.locator('.ai-side-context')).toContainText('Skill 运行')
    expect(pageErrors).toEqual([])
  })

  test('系统减少动态效果时关闭装饰动画', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/learning-flow')

    await expect(page.locator('.pulse-overview-board')).toBeVisible()
    await expect(page.locator('.pulse-overview-step').first()).toHaveCSS('animation-name', 'none')
    await expect(page.locator('.pulse-decision-item').first()).toHaveCSS('transition-duration', '0s')
    await page.locator('.pulse-overview-actions button').filter({ hasText: '查看详细视图' }).click()
    await expect(page.locator('.global-edge-dot').first()).toHaveCSS('display', 'none')
  })
})
