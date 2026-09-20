import { expect, test, type Page } from '@playwright/test'

type DiagnoseOverride = {
  diagnosis: string
  ai_available?: boolean
}

type UserOverride = {
  user_id: string
  username: string
  name: string
  role: string
  department: string
  can_view_all?: boolean
  must_change_password?: boolean
}

const baseTreePayload = {
  departments: [
    {
      department_id: 'RD',
      department_name: '研发部',
      node_count: 2,
      online_count: 1,
      offline_count: 1,
      today_executions: 12,
      anomaly_count: 2,
      instances: [
        {
          instance_id: 'inst-rd-1',
          name: 'OpenClaw-研发-1',
          department: '研发部',
          agent_type: 'aiclaw',
          node_status: 'online',
          heartbeat_ago_text: '20s 前',
          last_heartbeat_at: '2026-04-16T11:59:40.000Z',
          bridge_version: '1.3.2',
          bridge_platform: 'linux-x86_64',
          active_count: 1,
          capacity: 4,
          recent_skills: [
            {
              run_id: 'run-rd-active',
              skill_id: 'skill-rd-active',
              skill_name: 'ROI 巡检',
              status: 'running',
              started_at: '2026-04-16T11:48:00.000Z',
              duration_seconds: 720,
              writeback: 'pending',
            },
            {
              run_id: 'run-rd-failed-1',
              skill_id: 'skill-rd-alert',
              skill_name: '异常告警',
              status: 'failed',
              started_at: '2026-04-16T11:20:00.000Z',
              duration_seconds: 42,
              error_message: '参数校验失败',
              writeback: 'rejected',
            },
            {
              run_id: 'run-rd-failed-2',
              skill_id: 'skill-rd-alert',
              skill_name: '异常告警',
              status: 'failed',
              started_at: '2026-04-16T10:50:00.000Z',
              duration_seconds: 35,
              error_message: '上游 503',
              writeback: 'rejected',
            },
            {
              run_id: 'run-rd-ok-1',
              skill_id: 'skill-rd-daily',
              skill_name: '日报汇总',
              status: 'completed',
              started_at: '2026-04-16T10:20:00.000Z',
              duration_seconds: 98,
              writeback: 'dispatched',
            },
            {
              run_id: 'run-rd-ok-2',
              skill_id: 'skill-rd-daily',
              skill_name: '日报汇总',
              status: 'completed',
              started_at: '2026-04-16T09:20:00.000Z',
              duration_seconds: 88,
              writeback: 'dispatched',
            },
          ],
        },
        {
          instance_id: 'inst-rd-2',
          name: 'OpenClaw-研发-2',
          department: '研发部',
          agent_type: 'aiclaw',
          node_status: 'offline',
          heartbeat_ago_text: '8min 前',
          last_heartbeat_at: '2026-04-16T11:52:00.000Z',
          bridge_version: '1.3.0',
          bridge_platform: 'linux-arm64',
          active_count: 0,
          capacity: 2,
          recent_skills: [
            {
              run_id: 'run-rd-offline',
              skill_id: 'skill-rd-alert',
              skill_name: '离线诊断',
              status: 'failed',
              started_at: '2026-04-16T11:10:00.000Z',
              duration_seconds: 31,
              error_message: '网络超时',
              writeback: 'rejected',
            },
          ],
        },
      ],
    },
    {
      department_id: 'MKT',
      department_name: '市场部',
      node_count: 1,
      online_count: 1,
      offline_count: 0,
      today_executions: 3,
      anomaly_count: 0,
      instances: [
        {
          instance_id: 'inst-mkt-1',
          name: 'OpenClaw-市场-1',
          department: '市场部',
          agent_type: 'aiclaw',
          node_status: 'online',
          heartbeat_ago_text: '1min 前',
          last_heartbeat_at: '2026-04-16T11:59:00.000Z',
          bridge_version: '1.3.4',
          bridge_platform: 'linux-x86_64',
          active_count: 0,
          capacity: 3,
          recent_skills: [
            {
              run_id: 'run-mkt-ok',
              skill_id: 'skill-mkt',
              skill_name: '素材同步',
              status: 'completed',
              started_at: '2026-04-16T11:00:00.000Z',
              duration_seconds: 45,
              writeback: 'approved',
            },
          ],
        },
      ],
    },
  ],
  projected_at: '2026-04-16T12:00:00.000Z',
  etag: 'tasktree-v2',
  total_online: 2,
  total_offline: 1,
  total_running: 1,
  today_failed: 3,
  today_executions: 15,
}

const detailPayloads = {
  'inst-rd-1': {
    instance_id: 'inst-rd-1',
    name: 'OpenClaw-研发-1',
    department: '研发部',
    agent_type: 'aiclaw',
    node_status: 'online',
    heartbeat_ago_text: '20s 前',
    bridge_version: '1.3.2',
    bridge_platform: 'linux-x86_64',
    active_runs: [
      {
        run_id: 'run-rd-active',
        skill_id: 'skill-rd-active',
        skill_name: 'ROI 巡检',
        status: 'running',
        started_at: '2026-04-16T11:48:00.000Z',
        duration_seconds: 720,
        writeback: 'pending',
      },
    ],
    recent_completed: [
      {
        run_id: 'run-rd-failed-1',
        skill_id: 'skill-rd-alert',
        skill_name: '异常告警',
        status: 'failed',
        started_at: '2026-04-16T11:20:00.000Z',
        duration_seconds: 42,
        error_message: '参数校验失败',
        writeback: 'rejected',
      },
      {
        run_id: 'run-rd-ok-1',
        skill_id: 'skill-rd-daily',
        skill_name: '日报汇总',
        status: 'completed',
        started_at: '2026-04-16T10:20:00.000Z',
        duration_seconds: 98,
        writeback: 'dispatched',
      },
    ],
  },
  'inst-rd-2': {
    instance_id: 'inst-rd-2',
    name: 'OpenClaw-研发-2',
    department: '研发部',
    agent_type: 'aiclaw',
    node_status: 'offline',
    heartbeat_ago_text: '8min 前',
    bridge_version: '1.3.0',
    bridge_platform: 'linux-arm64',
    active_runs: [],
    recent_completed: [
      {
        run_id: 'run-rd-offline',
        skill_id: 'skill-rd-alert',
        skill_name: '离线诊断',
        status: 'failed',
        started_at: '2026-04-16T11:10:00.000Z',
        duration_seconds: 31,
        error_message: '网络超时',
        writeback: 'rejected',
      },
    ],
  },
  'inst-mkt-1': {
    instance_id: 'inst-mkt-1',
    name: 'OpenClaw-市场-1',
    department: '市场部',
    agent_type: 'aiclaw',
    node_status: 'online',
    heartbeat_ago_text: '1min 前',
    bridge_version: '1.3.4',
    bridge_platform: 'linux-x86_64',
    active_runs: [],
    recent_completed: [
      {
        run_id: 'run-mkt-ok',
        skill_id: 'skill-mkt',
        skill_name: '素材同步',
        status: 'completed',
        started_at: '2026-04-16T11:00:00.000Z',
        duration_seconds: 45,
        writeback: 'approved',
      },
    ],
  },
}

const scheduleJobsPayload = {
  jobs: [
    {
      skill_id: 'skill-rd-active-long-vector-image-promotion-metrics',
      skill_name: '内容电商图片向量与促销指标巡检任务',
      cron_expression: '*/5 * * * *',
      status: 'active',
      total_runs: 5,
      success_count: 4,
      failed_count: 1,
      last_run_at: '2026-04-16T11:55:00.000Z',
      last_status: 'completed',
      avg_duration_seconds: 42,
      total_output_items: 128,
      git_commit: 'f6a7b8c9d0e1',
      git_commit_full: 'f6a7b8c9d0e1f234567890abcdef1234567890abc',
      deployed_git_commit: 'a1b2c3d4e5f6',
      deployed_git_commit_full: 'a1b2c3d4e5f678901234567890abcdef12345678',
      git_deploy_recorded: true,
      sync_version_tag: '节点版本待同步',
    },
  ],
}

const runChainPayload = {
  run_id: 'run-rd-active',
  skill_id: 'skill-rd-active',
  skill_name: 'ROI 巡检',
  chain_complete: true,
  chain: [
    {
      type: 'decision_request',
      id: 'req-1',
      status: 'pending',
      title: 'ROI 巡检待审批',
      assignee: '李想',
      created_at: '2026-04-16T11:48:30.000Z',
    },
    {
      type: 'dispatch_task',
      id: 'dispatch-1',
      status: 'dispatched',
      title: '派发执行',
      assignee: 'OpenClaw-研发-1',
      decided_at: '2026-04-16T11:49:00.000Z',
      created_at: '2026-04-16T11:48:40.000Z',
    },
  ],
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function filterTreeByDepartment(payload: typeof baseTreePayload, department: string | null) {
  if (!department) return clone(payload)
  const next = clone(payload)
  next.departments = next.departments.filter((item) => item.department_name === department || item.department_id === department)
  next.total_online = next.departments.reduce((sum, item) => sum + (item.online_count || 0), 0)
  next.total_offline = next.departments.reduce((sum, item) => sum + (item.offline_count || 0), 0)
  next.total_running = next.departments.reduce((sum, item) => {
    return sum + (item.instances || []).filter((instance) =>
      (instance.recent_skills || []).some((run) => run.status === 'running'),
    ).length
  }, 0)
  next.today_executions = next.departments.reduce((sum, item) => sum + (item.today_executions || 0), 0)
  next.today_failed = next.departments.reduce((sum, item) => {
    return sum + (item.instances || []).reduce((instanceSum, instance) => {
      return instanceSum + (instance.recent_skills || []).filter((run) => run.status === 'failed').length
    }, 0)
  }, 0)
  return next
}

function statsFromTree(payload: typeof baseTreePayload) {
  const totalNodes = payload.departments.reduce((sum, item) => sum + (item.node_count || 0), 0)
  return {
    online_nodes: payload.total_online,
    total_nodes: totalNodes,
    today_executions: payload.today_executions,
    today_failed: payload.today_failed,
    today_failed_rate: totalNodes > 0 && payload.today_executions > 0 ? payload.today_failed / payload.today_executions : 0,
    department_count: payload.departments.length,
    instance_count: totalNodes,
    running_count: payload.total_running,
  }
}

async function installTaskTreeRoutes(
  page: Page,
  opts: {
    diagnoseOverride?: DiagnoseOverride
    user?: Partial<UserOverride>
    treePayload?: typeof baseTreePayload
  } = {},
) {
  const user: UserOverride = {
    user_id: 'u-admin',
    username: 'admin',
    name: '管理员',
    role: 'admin',
    department: '研发部',
    can_view_all: true,
    must_change_password: false,
    ...opts.user,
  }
  const treePayload = opts.treePayload ? clone(opts.treePayload) : clone(baseTreePayload)

  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(user),
    })
  })

  await page.route('**/api/todos/stats', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify({ pending: 0, dispatch_pending: 0 }),
    })
  })

  await page.route('**/api/notifications/**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify({ items: [], total: 0 }),
    })
  })

  await page.route('**/api/changelog**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify({ versions: [{ version: '2026.04.16' }] }),
    })
  })

  await page.route('**/api/task-tree**', async (route) => {
    const url = new URL(route.request().url())
    const { pathname, searchParams } = url

    if (pathname.endsWith('/stats')) {
      const scoped = filterTreeByDepartment(treePayload, searchParams.get('department'))
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify(statsFromTree(scoped)),
      })
      return
    }

    if (pathname.endsWith('/dashboard')) {
      const scoped = filterTreeByDepartment(treePayload, searchParams.get('department'))
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({
          totalNodes: scoped.departments.reduce((sum, item) => sum + (item.node_count || 0), 0),
          totalOnline: scoped.total_online,
          totalOffline: scoped.total_offline,
          totalRunning: scoped.total_running,
          todayExecutions: scoped.today_executions,
          todayFailed: scoped.today_failed,
          todayFailedRate: scoped.today_executions ? scoped.today_failed / scoped.today_executions : 0,
          upcomingSchedules: 1,
        }),
      })
      return
    }

    if (pathname.includes('/node/') && pathname.endsWith('/schedules')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify(scheduleJobsPayload),
      })
      return
    }

    if (pathname.includes('/node/')) {
      const instanceId = pathname.split('/').pop() || ''
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify(detailPayloads[instanceId as keyof typeof detailPayloads]),
      })
      return
    }

    if (pathname.includes('/run/') && pathname.endsWith('/chain')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify(runChainPayload),
      })
      return
    }

    if (pathname.includes('/run/') && pathname.endsWith('/diagnose')) {
      const body = opts.diagnoseOverride ?? {
        diagnosis: '网络超时，建议检查上游服务并重试。',
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({ run_id: 'run-rd-offline', ...body }),
      })
      return
    }

    if (pathname.includes('/skill/') && pathname.endsWith('/value')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json; charset=utf-8',
        body: JSON.stringify({
          skill_id: 'skill-rd-active',
          period_days: 30,
          saved_hours: 12.5,
          estimated_cost_saving: 1800,
          risk_events_prevented: 3,
          recommendation: '建议继续复制到高频巡检场景。',
        }),
      })
      return
    }

    const scoped = filterTreeByDepartment(treePayload, searchParams.get('department'))
    await route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      headers: {
        etag: scoped.etag,
      },
      body: JSON.stringify(scoped),
    })
  })
}

test.describe('Task Tree', () => {
  test.beforeEach(async ({ page }) => {
    await installTaskTreeRoutes(page)
  })

  test('renders anomaly-first dashboard shell and switches status tabs', async ({ page }) => {
    await page.goto('/task-tree')

    await expect(page.getByTestId('tasktree-header')).toBeVisible()
    await expect(page.getByTestId('tasktree-filter')).toBeVisible()
    await expect(page.getByText('节点总数')).toBeVisible()
    await expect(page.getByText('今日失败率')).toBeVisible()

    const sectionTitles = await page.locator('.tt-dept-section h3').allTextContents()
    expect(sectionTitles[0]).toBe('研发部')

    await expect(page.getByTestId('tasktree-instance-inst-rd-1')).toBeVisible()
    await expect(page.getByTestId('tasktree-instance-inst-mkt-1')).toBeHidden()

    await page.getByRole('tab', { name: /已完成/ }).click()
    await expect(page.getByTestId('tasktree-department-MKT')).toBeVisible()
    await page.getByTestId('tasktree-department-MKT').getByRole('button', { name: /市场部/ }).click()
    await expect(page.getByTestId('tasktree-instance-inst-mkt-1')).toBeVisible()
    await page.getByRole('tab', { name: /运行中/ }).click()
    await expect(page.getByTestId('tasktree-instance-inst-rd-1')).toBeVisible()
    await expect(page.getByTestId('tasktree-instance-inst-mkt-1')).toBeHidden()
    await expect(page.getByTestId('tasktree-department-RD')).toBeVisible()
  })

  test('clicking an instance card opens detail tabs and syncs URL', async ({ page }) => {
    await page.goto('/task-tree')

    await page.getByTestId('tasktree-instance-inst-rd-1').click()
    await expect(page.getByTestId('tasktree-detail')).toContainText('OpenClaw-研发-1')
    await expect(page).toHaveURL(/inst=inst-rd-1/)

    await page.locator('.tt-panel-tabs .arco-tabs-tab').filter({ hasText: '链路' }).click()
    await expect(page).toHaveURL(/tab=chain/)
    await expect(page.getByTestId('tasktree-detail-runchain')).toContainText('ROI 巡检')
    await page.getByRole('button', { name: '回写链路' }).last().click()
    await expect(page.getByTestId('tasktree-chain')).toContainText('链路闭环')

    await page.locator('.tt-panel-tabs .arco-tabs-tab').filter({ hasText: '价值' }).click()
    await expect(page.getByTestId('tasktree-detail-value')).toContainText('节省工时')

    await page.getByRole('button', { name: '关闭' }).click()
    await expect(page.getByTestId('tasktree-detail')).not.toBeVisible()
    await expect(page).not.toHaveURL(/inst=/)
  })

  test('schedule tab remains readable after reload in narrow layout', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 780 })
    await page.goto('/task-tree?anomaly=0&inst=inst-rd-1&tab=schedules')

    await expect(page.getByTestId('tasktree-schedules')).toBeVisible()
    await expect(page.getByText('定时任务').last()).toBeVisible()
    await expect(page.getByText('内容电商图片向量与促销指标巡检任务')).toBeVisible()
    await expect(page.getByText('停止')).toBeVisible()
    await expect(page.getByText('修改')).toBeVisible()
    await expect(page.getByText('同步版本')).toBeVisible()

    await page.reload()
    await expect(page.getByTestId('tasktree-schedules')).toBeVisible()
    await expect(page.getByText('内容电商图片向量与促销指标巡检任务')).toBeVisible()
    await expect(page.getByText('停止')).toBeVisible()
    await expect(page.getByText('修改')).toBeVisible()
    await expect(page.getByText('同步版本')).toBeVisible()

    const layout = await page.evaluate(() => {
      const panel = document.querySelector('[data-testid="tasktree-schedules"]') as HTMLElement | null
      const item = document.querySelector('.sched-item') as HTMLElement | null
      const metrics = document.querySelector('.sched-metrics') as HTMLElement | null
      const tabs = document.querySelector('.tt-panel-tabbar') as HTMLElement | null
      return {
        panelOverflow: panel ? panel.scrollWidth - panel.clientWidth : null,
        itemOverflow: item ? item.scrollWidth - item.clientWidth : null,
        metricColumns: metrics ? getComputedStyle(metrics).gridTemplateColumns : '',
        tabOverflowX: tabs ? getComputedStyle(tabs).overflowX : '',
      }
    })
    expect(layout.panelOverflow ?? 0).toBeLessThanOrEqual(2)
    expect(layout.itemOverflow ?? 0).toBeLessThanOrEqual(2)
    expect(layout.metricColumns).toContain('px')
    expect(layout.tabOverflowX).toBe('auto')
  })

  test('diagnosis tab shows AI tag when ai_available is true', async ({ page }) => {
    await page.goto('/task-tree')

    await page.getByTestId('tasktree-instance-inst-rd-2').click()
    await page.locator('.tt-panel-tabs .arco-tabs-tab').filter({ hasText: '运行诊断' }).click()
    await page.getByTestId('tasktree-diagnose-btn').click()

    await expect(page.getByTestId('tasktree-diagnosis-source-tag')).toContainText('AI 诊断')
    await expect(page.getByTestId('tasktree-detail-diagnosis')).toContainText('网络超时')
    await expect(page.getByTestId('tasktree-diagnosis-fallback-tip')).toHaveCount(0)
  })

  test('diagnosis tab shows rule-engine fallback when ai_available=false', async ({ page }) => {
    await page.unroute('**/api/task-tree**').catch(() => undefined)
    await installTaskTreeRoutes(page, {
      diagnoseOverride: {
        diagnosis: '规则引擎：连续 3 次失败，建议检查上游服务。',
        ai_available: false,
      },
    })

    await page.goto('/task-tree')
    await page.getByTestId('tasktree-instance-inst-rd-2').click()
    await page.locator('.tt-panel-tabs .arco-tabs-tab').filter({ hasText: '运行诊断' }).click()
    await page.getByTestId('tasktree-diagnose-btn').click()

    await expect(page.getByTestId('tasktree-diagnosis-source-tag')).toContainText('规则引擎')
    await expect(page.getByTestId('tasktree-diagnosis-fallback-tip')).toContainText('AI 服务不可用')
    await expect(page.getByTestId('tasktree-detail-diagnosis')).toContainText('规则引擎')
  })

  test('AIBP only sees own Lv1 section and no department switcher', async ({ page }) => {
    await page.unroute('**/api/task-tree**').catch(() => undefined)
    await page.unroute('**/api/auth/me').catch(() => undefined)
    await installTaskTreeRoutes(page, {
      user: {
        user_id: 'u-aibp',
        username: 'aibp',
        name: '研发 AIBP',
        role: 'aibp',
        department: '研发部',
        can_view_all: false,
      },
      treePayload: filterTreeByDepartment(baseTreePayload, '研发部'),
    })

    await page.goto('/task-tree')
    await expect(page.locator('.tt-filter-side')).toHaveCount(0)
    await expect(page.getByTestId('tasktree-department-RD')).toBeVisible()
    await expect(page.getByTestId('tasktree-department-MKT')).toHaveCount(0)
  })

  test('poll backoff: consecutive failures trigger ≥ 25s interval', async ({ page }) => {
    test.setTimeout(120_000)

    const abortCalls: number[] = []
    await page.route('**/api/task-tree**', async (route) => {
      const url = new URL(route.request().url())
      const { pathname } = url
      if (pathname === '/api/task-tree' || pathname.endsWith('/api/task-tree/')) {
        abortCalls.push(Date.now())
        await route.abort()
        return
      }
      if (pathname.endsWith('/stats')) {
        await route.fulfill({
          status: 500,
          contentType: 'application/json; charset=utf-8',
          body: JSON.stringify({ error: 'boom' }),
        })
        return
      }
      await route.fallback()
    })

    await page.goto('/task-tree')

    await expect.poll(() => abortCalls.length, { timeout: 80_000 }).toBeGreaterThanOrEqual(5)

    const deltas: number[] = []
    for (let i = 1; i < abortCalls.length; i++) {
      deltas.push(abortCalls[i] - abortCalls[i - 1])
    }
    expect(deltas[3]).toBeGreaterThanOrEqual(25_000)
  })

  test('lastPrimaryNav migration: portal -> projects, then current tasktree after mount', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('lastPrimaryNav', 'portal')
    })

    await page.goto('/task-tree')
    await expect(page.getByTestId('tasktree-header')).toBeVisible()

    await expect.poll(async () => {
      return page.evaluate(() => localStorage.getItem('lastPrimaryNav'))
    }, { timeout: 5000 }).not.toBe('portal')

    const stored = await page.evaluate(() => localStorage.getItem('lastPrimaryNav'))
    expect(stored).not.toBe('portal')
    expect(['projects', 'tasktree', 'hall', 'workflow', 'inbox']).toContain(stored)
  })
})
