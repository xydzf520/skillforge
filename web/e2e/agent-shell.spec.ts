import { expect, test, type Page, type Route } from '@playwright/test'

const instancesPayload = [
  {
    id: 'node-train',
    name: '训练节点',
    department: 'EC',
    department_name: 'EC',
    agent_purpose: 'training',
    bridge_online: true,
    is_platform_default: true,
    runtime_type: 'aiclaw',
    active_training_jobs_count: 1,
    active_training_jobs: [{ id: 'job-1', name: 'LoRA 微调', status: 'running' }],
    training: {
      gateway: true,
      supported_tasks: ['lora', 'eval'],
      gpu_count: 2,
      worker_count: 2,
    },
  },
  {
    id: 'node-analysis',
    name: '分析节点',
    department: 'EC',
    department_name: 'EC',
    agent_purpose: 'analysis',
    bridge_online: true,
    runtime_type: 'aiclaw',
    analysis: {
      agent: true,
      ops: ['intelligence.analyze'],
    },
  },
]

async function installAgentRoutes(page: Page) {
  await page.addInitScript(() => {
    class MockWebSocket extends EventTarget {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3
      readyState = MockWebSocket.OPEN
      url: string
      constructor(url: string) {
        super()
        this.url = url
        setTimeout(() => this.dispatchEvent(new Event('open')), 0)
      }
      send() {}
      close() {
        this.readyState = MockWebSocket.CLOSED
        this.dispatchEvent(new Event('close'))
      }
    }
    // @ts-expect-error replace browser WebSocket in this mocked shell spec
    window.WebSocket = MockWebSocket
  })
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
    if (pathname === '/api/aiclaw/instances') {
      await json(instancesPayload)
      return
    }
    if (pathname === '/api/aiclaw/departments/agent-coverage') {
      await json({
        items: [{
          department: 'EC',
          status: 'fallback',
          capabilities: {
            skill_runtime: { ready: true, online: 1, count: 1 },
            analysis: { ready: true, online: 1, count: 1 },
            training: { ready: false, fallback_ready: true, fallback_count: 1 },
          },
        }],
      })
      return
    }
    if (pathname === '/api/aiclaw/instances/node-train/agents' || pathname === '/api/aiclaw/instances/node-analysis/agents') {
      await json({ items: [{ id: 'agent-1', name: '默认 Agent' }] })
      return
    }
    if (pathname.endsWith('/agents/agent-1/skills')) {
      await json({
        items: [{
          id: 'tmall-link-decline',
          display_name: '天猫店铺链接下滑分析',
          risk_level: 'R1',
          department: 'EC',
          calls_today: 12,
        }],
      })
      return
    }
    await json({})
  })
}

async function expectAgentShell(page: Page) {
  await expect(page.locator('.brain-page.ai-main')).toBeVisible()
  await expect(page.locator('.brain-dept-rail.ai-sidebar')).toBeVisible()
  await expect(page.locator('.brain-detail')).toBeVisible()
  await expect(page.locator('.brain-page.page-container')).toHaveCount(0)
  await expect(page.locator('.brain-page .page-list-card')).toHaveCount(0)
  await expect(page.locator('.brain-page .ai-btn svg path').first()).toBeVisible()
  await expect(page.locator('.brain-page .brain-detail-avatar svg path')).toBeVisible()

  const metrics = await page.locator('.brain-page').evaluate((root) => ({
    paddingLeft: getComputedStyle(root).paddingLeft,
    sidebarWidth: getComputedStyle(root.querySelector('.brain-dept-rail') as Element).width,
    rootClass: root.className,
    detailText: root.querySelector('.brain-detail')?.textContent?.slice(0, 80),
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  }))
  expect(metrics.paddingLeft).toBe('0px')
  expect(metrics.rootClass).toContain('ai-main')
  expect(metrics.detailText).toContain('训练节点')
  expect(metrics.overflow).toBe(false)
}

test.describe('Agent shell', () => {
  test.beforeEach(async ({ page }) => {
    await installAgentRoutes(page)
  })

  test('renders the department Agent shell on desktop and mobile', async ({ page }) => {
    await page.goto('/agent')
    await expect(page.locator('.brain-detail-title', { hasText: '训练节点' })).toBeVisible()
    await expect(page.getByText('可调用 Skills')).toBeVisible()
    await expectAgentShell(page)

    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/agent')
    await expect(page.locator('.brain-detail-title', { hasText: '训练节点' })).toBeVisible()
    await expectAgentShell(page)
  })
})
