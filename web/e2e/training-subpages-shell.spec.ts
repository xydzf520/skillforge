import { expect, test, type Page, type Route } from '@playwright/test'

const datasetsPayload = {
  items: [{
    skill_id: 'skill-recommend',
    skill_name: '商品推荐',
    department: 'EC',
    status: 'active',
    dataset_ref: 'decision-log://skill-recommend/action-outcome/latest',
    passed: true,
    can_create_candidate: true,
    sample_counts: {
      sft_samples: 120,
      preference_samples: 60,
      action_outcome_samples: 120,
      eval_samples: 60,
      recent_total: 120,
      recent_failed: 10,
    },
    checks: [
      { key: 'sft_samples', label: 'SFT 样本', actual: 120, threshold: 100, passed: true },
      { key: 'recent_failure_rate', label: '最近 7 天失败率', actual: 0.0833, threshold: 0.3, passed: true },
    ],
    recent_failure_rate: 0.0833,
    last_log_at: '2026-05-21T11:00:00+08:00',
  }],
}

const deploymentsPayload = {
  items: [{
    id: 'deploy-1',
    job_id: 'train-1',
    department: 'EC',
    model_family: 'item_decline_ranker',
    artifact_id: 'artifact-1',
    artifact_ref: { id: 'artifact-1', sha256: 'a'.repeat(64) },
    target_skill_ids: ['skill-recommend'],
    status: 'awaiting_review',
    rollout_percent: 0,
    rollback_to: '',
    updated_at: '2026-05-21T11:00:00+08:00',
    job: {
      id: 'train-1',
      title: '动作推荐训练',
      status: 'completed',
      target_skill_id: 'skill-recommend',
      target_gateway_id: 'node-1',
    },
  }],
}

async function installTrainingSubpageRoutes(page: Page) {
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
    if (pathname === '/api/training/datasets') {
      await json(datasetsPayload)
      return
    }
    if (pathname === '/api/training/deployments') {
      await json(deploymentsPayload)
      return
    }
    await json({})
  })
}

async function expectNoLegacyShell(page: Page, root: string) {
  await expect(page.locator(`${root}.ai-main`)).toBeVisible()
  await expect(page.locator(`${root} .training-subpage-head.ai-pagehead`)).toBeVisible()
  await expect(page.locator(`${root} .training-subpage-body.ai-pagebody`)).toBeVisible()
  await expect(page.locator(`${root}.page-container`)).toHaveCount(0)
  await expect(page.locator(`${root} > .page-list-card`)).toHaveCount(0)
  await expect(page.locator(`${root} .training-head-actions .ai-btn svg path`).first()).toBeVisible()

  const metrics = await page.locator(root).evaluate((el) => ({
    paddingLeft: getComputedStyle(el).paddingLeft,
    pageheadX: document.querySelector('.training-subpage-head')?.getBoundingClientRect().x,
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  }))
  expect(metrics.paddingLeft).toBe('0px')
  expect(metrics.pageheadX).toBe(0)
  expect(metrics.overflow).toBe(false)
}

test.describe('Training subpage shells', () => {
  test.beforeEach(async ({ page }) => {
    await installTrainingSubpageRoutes(page)
  })

  test('renders datasets shell on desktop and mobile', async ({ page }) => {
    await page.goto('/training/datasets')
    await expect(page.getByRole('heading', { name: '数据资产' })).toBeVisible()
    await expect(page.locator('.dataset-card.ai-card')).toBeVisible()
    await expect(page.locator('.dataset-card .training-subcard-title')).toContainText('Skill 数据资产覆盖')
    await expectNoLegacyShell(page, '.training-datasets-page')

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/training/datasets')
    await expect(page.getByRole('heading', { name: '数据资产' })).toBeVisible()
    await expectNoLegacyShell(page, '.training-datasets-page')
  })

  test('renders models shell on desktop and mobile', async ({ page }) => {
    await page.goto('/training/models')
    await expect(page.getByRole('heading', { name: '模型部署' })).toBeVisible()
    await expect(page.locator('.model-card.ai-card')).toBeVisible()
    await expect(page.locator('.model-card .training-subcard-title')).toContainText('模型部署记录')
    await expectNoLegacyShell(page, '.training-models-page')

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/training/models')
    await expect(page.getByRole('heading', { name: '模型部署' })).toBeVisible()
    await expectNoLegacyShell(page, '.training-models-page')
  })
})
