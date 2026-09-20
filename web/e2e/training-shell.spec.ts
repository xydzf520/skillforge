import { expect, test, type Page, type Route } from '@playwright/test'

const resourcePayload = {
  context: { department: 'EC', can_view_all: true },
  items: [
    {
      id: 'node-train-1',
      name: '训练网关 A',
      department: 'EC',
      online: true,
      gateway_kind: 'OpenClaw',
      training: { gateway: true, supported_tasks: ['lora', 'qlora', 'eval'] },
      gpu_count: 2,
      idle_gpu_count: 1,
      busy_gpu_count: 1,
      vram_total_gb: 48,
      vram_free_gb: 32,
      active_training_jobs_count: 1,
      active_training_jobs: [{
        id: 'train-running',
        title: 'ec.link_decline-cls v4',
        status: 'running',
        job_type: 'lora',
        target_skill_id: 'skill-ec-link',
      }],
    },
    {
      id: 'node-train-2',
      name: '训练网关 B',
      department: 'CS',
      online: true,
      gateway_kind: 'AIClaw',
      training: { gateway: true, supported_tasks: ['lora', 'eval'] },
      gpu_count: 1,
      idle_gpu_count: 1,
      busy_gpu_count: 0,
      vram_total_gb: 24,
      vram_free_gb: 18,
      active_training_jobs_count: 0,
      active_training_jobs: [],
    },
  ],
}

const jobsPayload = {
  items: [
    {
      id: 'train-running',
      title: 'ec.link_decline-cls v4',
      department: 'EC',
      status: 'running',
      job_type: 'lora',
      target_gateway_id: 'node-train-1',
      dataset_ref: 'dataset://ec/actions/v1',
      progress: 55,
      eta_text: '47 分钟',
      parameters: { epochs: 3, learning_rate: '2e-4' },
      created_at: '2026-05-24T10:00:00+08:00',
    },
    {
      id: 'train-review',
      title: 'wh.turnover-reg v1',
      department: 'WH',
      status: 'awaiting_review',
      job_type: 'qlora',
      target_gateway_id: 'node-train-2',
      dataset_ref: 'dataset://wh/actions/v1',
      progress: 0,
      parameters: { epochs: 2 },
      created_at: '2026-05-24T11:00:00+08:00',
    },
    {
      id: 'train-served',
      title: 'cs.user_need-cls v2',
      department: 'CS',
      status: 'completed',
      job_type: 'lora',
      target_gateway_id: 'node-train-2',
      dataset_ref: 'dataset://cs/actions/v1',
      progress: 100,
      artifact_name: 'cs-user-need-v2',
      latest_deployment: { id: 'deploy-1', status: 'active' },
      created_at: '2026-05-24T12:00:00+08:00',
    },
  ],
}

async function installTrainingRoutes(page: Page) {
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
    if (pathname === '/api/training/resources') {
      await json(resourcePayload)
      return
    }
    if (pathname === '/api/training/jobs') {
      await json(jobsPayload)
      return
    }
    await json({})
  })
}

test.describe('Training shell', () => {
  test.beforeEach(async ({ page }) => {
    await installTrainingRoutes(page)
  })

  test('renders the design-system shell and dense training pipeline', async ({ page }) => {
    await page.goto('/training')

    await expect(page.getByRole('heading', { name: '训练流水' })).toBeVisible()
    await expect(page.locator('.training-home.ai-main')).toBeVisible()
    await expect(page.locator('.training-pagehead.ai-pagehead')).toBeVisible()
    await expect(page.locator('.training-pagebody.ai-pagebody')).toBeVisible()
    await expect(page.locator('.training-pipeline-card .pipeline-stage')).toHaveCount(6)
    await expect(page.locator('.job-card.ai-card')).toBeVisible()
    await expect(page.locator('.training-side-card.resource-card')).toBeVisible()
    await expect(page.locator('.training-model-card')).toBeVisible()
    await expect(page.locator('.training-home.page-container')).toHaveCount(0)
    await expect(page.locator('.training-home > .page-list-card')).toHaveCount(0)
    await expect(page.locator('.training-head-actions .ai-btn svg path').first()).toBeVisible()
    await expect(page.locator('.job-extra-btn svg path')).toBeVisible()

    const metrics = await page.locator('.training-home').evaluate((el) => ({
      paddingLeft: getComputedStyle(el).paddingLeft,
      pagebodyX: document.querySelector('.training-pagebody')?.getBoundingClientRect().x,
      pipelineX: document.querySelector('.training-pipeline-card')?.getBoundingClientRect().x,
      jobX: document.querySelector('.job-card')?.getBoundingClientRect().x,
      modelX: document.querySelector('.training-model-card')?.getBoundingClientRect().x,
      overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    }))
    expect(metrics.paddingLeft).toBe('0px')
    expect(metrics.pagebodyX).toBe(0)
    expect(metrics.pipelineX).toBe(0)
    expect(metrics.jobX).toBe(0)
    expect(metrics.modelX).toBe(0)
    expect(metrics.overflow).toBe(false)
  })

  test('keeps the shell usable on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/training')

    await expect(page.getByRole('heading', { name: '训练流水' })).toBeVisible()
    await expect(page.locator('.training-pagehead.ai-pagehead')).toBeVisible()
    await expect(page.locator('.training-pagebody.ai-pagebody')).toBeVisible()
    await expect(page.locator('.training-pipeline-card .pipeline-stage')).toHaveCount(6)
    await expect(page.locator('.job-card.ai-card')).toBeVisible()
    await expect(page.locator('.training-home > .page-list-card')).toHaveCount(0)

    const metrics = await page.locator('.training-home').evaluate((el) => ({
      paddingLeft: getComputedStyle(el).paddingLeft,
      pageheadX: document.querySelector('.training-pagehead')?.getBoundingClientRect().x,
      pagebodyX: document.querySelector('.training-pagebody')?.getBoundingClientRect().x,
      pipelineX: document.querySelector('.training-pipeline-card')?.getBoundingClientRect().x,
      jobX: document.querySelector('.job-card')?.getBoundingClientRect().x,
      overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    }))
    expect(metrics.paddingLeft).toBe('0px')
    expect(metrics.pageheadX).toBe(0)
    expect(metrics.pagebodyX).toBe(0)
    expect(metrics.pipelineX).toBe(0)
    expect(metrics.jobX).toBe(0)
    expect(metrics.overflow).toBe(false)
  })
})
