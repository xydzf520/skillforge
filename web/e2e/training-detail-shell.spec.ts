import { expect, test, type Page, type Route } from '@playwright/test'

const jobPayload = {
  id: 'train-1',
  title: '训练商品下滑动作推荐模型',
  department: 'EC',
  created_by: 'admin',
  status: 'completed',
  job_type: 'lora',
  training_strategy: 'lora_task_parallel',
  target_skill_id: 'skill-recommend',
  target_gateway_id: 'node-1',
  dataset_ref: 'dataset://ec/actions/v1',
  objective: '提升动作推荐准确率',
  risk_level: 'R2',
  failure_stage: '',
  spec: { eval_gate: { min_win_rate: 0.55 } },
  gateway_payload: { job_id: 'train-1', control: { callback_token: '***' } },
  approved_by: 'admin',
  approved_at: '2026-05-21T10:00:00+08:00',
  created_at: '2026-05-21T09:00:00+08:00',
  updated_at: '2026-05-21T11:00:00+08:00',
  tasks: [
    {
      id: 1,
      gateway_id: 'node-1',
      worker_id: 'worker-1',
      status: 'completed',
      progress: 1,
      metrics: { gateway_result: { metrics: { win_rate: 0.61, loss: 0.21 }, artifacts: [] } },
      updated_at: '2026-05-21T10:30:00+08:00',
    },
    {
      id: 2,
      gateway_id: 'node-1',
      worker_id: 'worker-1',
      status: 'completed',
      progress: 1,
      metrics: { op: 'training.evaluate', passed: true, checks: [{ name: 'min_win_rate', actual: 0.61, expected: 0.55, passed: true }] },
      updated_at: '2026-05-21T11:00:00+08:00',
    },
  ],
  deployments: [{
    id: 'deploy-1',
    status: 'awaiting_review',
    model_family: 'item_decline_ranker',
    artifact_id: 'artifact-1',
    artifact_ref: { id: 'artifact-1', sha256: 'a'.repeat(64) },
    target_skill_ids: ['skill-recommend'],
    rollout_percent: 0,
    rollback_to: '',
    created_at: '2026-05-21T11:00:00+08:00',
    updated_at: '2026-05-21T11:00:00+08:00',
  }],
  latest_deployment: {
    id: 'deploy-1',
    status: 'awaiting_review',
    model_family: 'item_decline_ranker',
    artifact_id: 'artifact-1',
    artifact_ref: { id: 'artifact-1', sha256: 'a'.repeat(64) },
    target_skill_ids: ['skill-recommend'],
    rollout_percent: 0,
  },
  training_plan: {
    model_name: 'item_decline_ranker',
    base_model: 'qwen2.5-7b',
    parameters: { learning_rate: '2e-4', batch_size: 16 },
    runtime: { progress: 1, estimated_duration_seconds: 3600, remaining_seconds: 0, eta_text: '完成' },
    artifact: { id: 'artifact-1', type: 'adapter', name: 'model.bin', uri: 'oss://models/model.bin', sha256: 'a'.repeat(64), size_bytes: 1024, downloadable: true },
    artifacts_count: 1,
    chat_target: {},
  },
}

const deploymentPayload = {
  id: 'deploy-1',
  job_id: 'train-1',
  department: 'EC',
  model_family: 'item_decline_ranker',
  artifact_id: 'artifact-1',
  artifact_ref: { id: 'artifact-1', sha256: 'a'.repeat(64) },
  eval_task_id: 2,
  target_skill_ids: ['skill-recommend'],
  status: 'awaiting_review',
  rollout_percent: 10,
  rollback_to: '',
  requested_by: 'admin',
  request_reason: '评估通过，提交部署审批',
  approved_by: '',
  approved_at: '',
  rejected_by: '',
  rejected_at: '',
  reject_reason: '',
  activated_at: '',
  created_at: '2026-05-21T11:00:00+08:00',
  updated_at: '2026-05-21T11:00:00+08:00',
}

async function installTrainingDetailRoutes(page: Page) {
  await page.route('**/*', async (route: Route) => {
    const url = new URL(route.request().url())
    const { pathname } = url
    if (!pathname.startsWith('/api/')) {
      await route.fallback()
      return
    }
    const json = (body: unknown) => route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(body),
    })
    if (pathname === '/api/auth/me') {
      await json({
        user_id: 'u-admin',
        username: 'admin',
        name: '管理员',
        role: 'admin',
        department: 'EC',
        can_view_all: true,
        must_change_password: false,
      })
      return
    }
    if (pathname === '/api/todos/stats') {
      await json({ pending: 0, dispatch_pending: 0 })
      return
    }
    if (pathname.startsWith('/api/changelog')) {
      await json({ versions: [{ version: '2026.05.24' }] })
      return
    }
    if (pathname === '/api/training/jobs/train-1') {
      await json(jobPayload)
      return
    }
    if (pathname === '/api/training/deployments/deploy-1') {
      await json({
        deployment: deploymentPayload,
        job: {
          id: 'train-1',
          title: '动作推荐训练',
          department: 'EC',
          status: 'completed',
          job_type: 'lora',
          target_skill_id: 'skill-recommend',
          target_gateway_id: 'node-1',
          dataset_ref: 'decision-log://skill-recommend/action-outcome/latest',
          tasks: jobPayload.tasks,
          deployments: [deploymentPayload],
        },
      })
      return
    }
    await json({})
  })
}

async function expectJobShell(page: Page) {
  await expect(page.locator('.training-job-page.ai-main')).toBeVisible()
  await expect(page.locator('.page-detail-toolbar.ai-pagehead')).toBeVisible()
  await expect(page.locator('.training-job-body.ai-pagebody')).toBeVisible()
  await expect(page.locator('.training-job-page.page-container')).toHaveCount(0)
  await expect(page.locator('.training-job-page .page-list-card')).toHaveCount(0)
  await expect(page.locator('.page-detail-toolbar .ai-btn svg path').first()).toBeVisible()
  await expect(page.locator('.worker-task-card.ai-card')).toBeVisible()

  const metrics = await page.locator('.training-job-page').evaluate((el) => ({
    paddingLeft: getComputedStyle(el).paddingLeft,
    toolbarX: document.querySelector('.page-detail-toolbar')?.getBoundingClientRect().x,
    bodyClass: document.querySelector('.training-job-body')?.className,
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  }))
  expect(metrics.paddingLeft).toBe('0px')
  expect(metrics.toolbarX).toBe(0)
  expect(metrics.bodyClass).toContain('ai-pagebody')
  expect(metrics.overflow).toBe(false)
}

async function expectDeploymentShell(page: Page) {
  await expect(page.locator('.training-deployment-detail-page.ai-main')).toBeVisible()
  await expect(page.locator('.training-subpage-head.ai-pagehead')).toBeVisible()
  await expect(page.locator('.training-detail-body.ai-pagebody')).toBeVisible()
  await expect(page.locator('.training-deployment-detail-page.page-container')).toHaveCount(0)
  await expect(page.locator('.training-deployment-detail-page .page-list-card')).toHaveCount(0)
  await expect(page.locator('.training-detail-actions .ai-btn svg path').first()).toBeVisible()
  await expect(page.locator('.detail-card.ai-card').first()).toBeVisible()

  const metrics = await page.locator('.training-deployment-detail-page').evaluate((el) => ({
    paddingLeft: getComputedStyle(el).paddingLeft,
    pageheadX: document.querySelector('.training-subpage-head')?.getBoundingClientRect().x,
    bodyClass: document.querySelector('.training-detail-body')?.className,
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  }))
  expect(metrics.paddingLeft).toBe('0px')
  expect(metrics.pageheadX).toBe(0)
  expect(metrics.bodyClass).toContain('ai-pagebody')
  expect(metrics.overflow).toBe(false)
}

test.describe('Training detail shells', () => {
  test.beforeEach(async ({ page }) => {
    await installTrainingDetailRoutes(page)
  })

  test('renders job detail shell on desktop and mobile', async ({ page }) => {
    await page.goto('/training/jobs/train-1')
    await expect(page.getByText('训练商品下滑动作推荐模型')).toBeVisible()
    await expect(page.getByText('Fine-tune Model')).toBeVisible()
    await expectJobShell(page)

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/training/jobs/train-1')
    await expect(page.getByText('训练商品下滑动作推荐模型')).toBeVisible()
    await expectJobShell(page)
  })

  test('renders deployment detail shell on desktop and mobile', async ({ page }) => {
    await page.goto('/training/deployments/deploy-1')
    await expect(page.getByRole('heading', { name: 'item_decline_ranker' })).toBeVisible()
    await expect(page.getByText('Agent 对话路由')).toBeVisible()
    await expectDeploymentShell(page)

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/training/deployments/deploy-1')
    await expect(page.getByRole('heading', { name: 'item_decline_ranker' })).toBeVisible()
    await expectDeploymentShell(page)
  })
})
