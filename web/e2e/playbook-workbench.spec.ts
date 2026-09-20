import { expect, type Locator, type Page, test } from '@playwright/test'
import { installMockApi } from './mock-api'

const workbenchTabs = {
  overview: /看流程|总览/,
  edit: /改流程|编辑流程/,
  run: /跑一次|执行监控/,
  advanced: /高级/,
}

function workbenchControl(page: Page, name: string | RegExp): Locator {
  const byText = typeof name === 'string' ? page.getByText(name, { exact: true }) : page.getByText(name)
  return page
    .getByRole('tab', { name })
    .or(page.getByRole('button', { name }))
    .or(page.getByRole('radio', { name }))
    .or(byText)
    .first()
}

async function clickWorkbenchControl(page: Page, name: string | RegExp) {
  const control = workbenchControl(page, name)
  await expect(control).toBeVisible()
  await control.click()
}

async function emitDraftChange(page: Page, steps: Array<Record<string, unknown>>) {
  await page.evaluate(({ steps: nextSteps }) => {
    window.dispatchEvent(new MessageEvent('message', {
      origin: window.location.origin,
      data: {
        source: 'playbook-editor',
        type: 'playbook-changed',
        payload: {
          steps: nextSteps,
          _canvas_layout: {
            nodes: {
              collect: { x: 160, y: 180 },
              summarize: { x: 460, y: 180 },
              review: { x: 760, y: 180 },
            },
            viewport: { x: 0, y: 0, zoom: 0.92 },
          },
        },
      },
    }))
  }, { steps })
}

async function confirmRunIfNeeded(page: Page) {
  const dialog = page.getByRole('dialog').last()
  if (!(await dialog.isVisible().catch(() => false))) return
  const confirm = dialog.getByRole('button', { name: /确认|开始执行|执行已保存版本|先保存再执行|继续/ }).first()
  if (await confirm.isVisible().catch(() => false)) {
    await confirm.click()
  }
}

test.describe('Playbook workbench', () => {
  test.beforeEach(async ({ page }) => {
    await installMockApi(page)
  })

  test('opens /playbook/:name in unified overview and keeps advanced labels secondary', async ({ page }) => {
    await page.goto('/playbook/replenishment-check')

    await expect(page.getByRole('heading', { name: '补货巡检' })).toBeVisible()
    for (const label of Object.values(workbenchTabs)) {
      await expect(workbenchControl(page, label)).toBeVisible()
    }

    await expect(page.getByText(/Visual Canvas|Mermaid View|Mermaid 流程图|YAML 预览|只读拓扑/)).toHaveCount(0)

    await clickWorkbenchControl(page, workbenchTabs.advanced)
    await expect(workbenchControl(page, /文本流程图|原始配置/)).toBeVisible()
    await expect(page.getByRole('tab', { name: /Mermaid|YAML/i })).toHaveCount(0)
  })

  test('checks current draft in edit mode and saves the updated workbench draft', async ({ page }) => {
    const validationCalls: string[] = []
    let lastValidationPayload: Record<string, unknown> | null = null
    let savePayload: Record<string, unknown> | null = null

    page.on('request', (request) => {
      const url = new URL(request.url())
      if (!url.pathname.startsWith('/api/playbooks')) return

      if (request.method() === 'POST' && /\/validate$/.test(url.pathname)) {
        validationCalls.push(url.pathname)
        lastValidationPayload = JSON.parse(request.postData() || '{}') as Record<string, unknown>
      }

      if (request.method() === 'PUT' && url.pathname === '/api/playbooks/replenishment-check') {
        savePayload = JSON.parse(request.postData() || '{}') as Record<string, unknown>
      }
    })

    await page.goto('/playbook/replenishment-check')
    await clickWorkbenchControl(page, workbenchTabs.edit)

    await expect(page.locator('iframe').first()).toBeVisible()
    await expect(workbenchControl(page, '检查问题')).toBeVisible()
    await expect(workbenchControl(page, '保存')).toBeVisible()

    await emitDraftChange(page, [
      { id: 'collect', name: '收集库存数据', skill_id: 'skill-forecast' },
      { id: 'summarize', name: '生成巡检纪要', skill_id: 'skill-summary', depends_on: ['collect'] },
      { id: 'review', name: '人工复核', depends_on: ['summarize'] },
    ])

    await expect(page.getByText(/未保存|还没保存/).first()).toBeVisible()

    await clickWorkbenchControl(page, '检查问题')
    await expect.poll(() => validationCalls[0] || '').toBe('/api/playbooks/validate')
    expect(lastValidationPayload).toMatchObject({
      steps: expect.arrayContaining([
        expect.objectContaining({ id: 'review' }),
      ]),
    })
    await expect(page.getByText('步骤 review 未配置 Skill')).toBeVisible()

    await emitDraftChange(page, [
      { id: 'collect', name: '收集库存数据', skill_id: 'skill-forecast' },
      { id: 'summarize', name: '生成巡检纪要', skill_id: 'skill-summary', depends_on: ['collect'] },
      { id: 'review', name: '人工复核', skill_id: 'skill-summary', depends_on: ['summarize'] },
    ])

    await clickWorkbenchControl(page, '保存')
    await expect.poll(() => Boolean(savePayload)).toBe(true)
    expect(savePayload).toMatchObject({
      steps: expect.arrayContaining([
        expect.objectContaining({ id: 'review', skill_id: 'skill-summary' }),
      ]),
    })
    await expect(page.getByText(/未保存修改|还没保存/)).toHaveCount(0)
    await expect(
      page.getByText(/已保存/).or(page.locator('.arco-message').filter({ hasText: /已保存|保存成功/ })).first(),
    ).toBeVisible()
  })

  test('starts a run and switches into execution monitoring with run status or logs', async ({ page }) => {
    const runRequests: string[] = []

    page.on('request', (request) => {
      const url = new URL(request.url())
      if (request.method() === 'POST' && url.pathname === '/api/playbooks/replenishment-check/run') {
        runRequests.push(url.pathname)
      }
    })

    await page.goto('/playbook/replenishment-check')

    await clickWorkbenchControl(page, '开始执行')
    await confirmRunIfNeeded(page)

    await expect.poll(() => runRequests.length).toBe(1)
    await expect(page.getByText(/run-pb-\d+/).first()).toBeVisible()
    await expect(page.getByText(/执行中|已完成|running|completed/).first()).toBeVisible()
    await expect(page.getByText(/collect|summarize/).first()).toBeVisible()
  })
})
