import { describe, it, expect, vi } from 'vitest'

describe('API module', () => {
  it('exports all API groups', async () => {
    const api = await import('@/api')
    expect(api.authApi).toBeDefined()
    expect(api.skillApi).toBeDefined()
    expect(api.reviewApi).toBeDefined()
    expect(api.executionApi).toBeDefined()
    expect(api.datasourceApi).toBeDefined()
    expect(api.dashboardApi).toBeDefined()
    expect(api.playbookApi).toBeDefined()
    expect(api.userApi).toBeDefined()
    expect(api.auditApi).toBeDefined()
    expect(api.complianceApi).toBeDefined()
    expect(api.aiclawApi).toBeDefined()
    expect(api.todoApi).toBeDefined()
    expect(api.agentCoreApi).toBeDefined()
    expect(api.sfApi).toBeDefined()
    expect(typeof api.sfApi.overview).toBe('function')
    expect(typeof api.sfApi.catalog).toBe('function')
    expect(typeof api.sfApi.trace).toBe('function')
    expect(typeof api.sfApi.dryRunMcp).toBe('function')
    expect(api.learningApi).toBeDefined()
    expect(typeof api.learningApi.summary).toBe('function')
    expect(typeof api.learningApi.flowTopology).toBe('function')
    expect(typeof api.learningApi.flowJourneys).toBe('function')
    expect(typeof api.learningApi.bottlenecks).toBe('function')
    expect(typeof api.learningApi.governanceTasks).toBe('function')
    expect(typeof api.learningApi.updateGovernanceTaskStatus).toBe('function')
    expect(typeof api.learningApi.trainingManifest).toBe('function')
    expect(typeof api.learningApi.automationStatus).toBe('function')
    expect(typeof api.learningApi.runAutomation).toBe('function')
    expect(typeof api.learningApi.backfill).toBe('function')
    expect(typeof api.projectApi.listSdkTokens).toBe('function')
    expect(typeof api.projectApi.createSdkToken).toBe('function')
    expect(typeof api.projectApi.revokeSdkToken).toBe('function')
    expect(typeof api.projectApi.ensureSdkCheckProject).toBe('function')
    expect(typeof api.projectApi.sdkCheckRun).toBe('function')
    expect(typeof api.projectApi.models236).toBe('function')
    expect(typeof api.trainingApi.automationStatus).toBe('function')
    expect(typeof api.trainingApi.datasetVersionAutomation).toBe('function')
    expect(typeof api.trainingApi.autoRunDatasetVersion).toBe('function')
    expect(typeof api.trainingApi.autoAdvanceJob).toBe('function')
    expect(typeof api.trainingApi.runAutomation).toBe('function')
    expect(typeof api.trainingApi.simulateJobResult).toBe('function')
  })

  it('skillApi has all required methods', async () => {
    const { skillApi } = await import('@/api')
    const methods = ['list', 'get', 'create', 'saveContent', 'saveStructured',
      'updateParams', 'history', 'diff', 'validate', 'compareParams',
      'batchPublish', 'healthScore', 'shadowComparisons', 'shadowDivergenceRate',
      'validateAntipattern', 'previewOutput']
    methods.forEach(m => expect(typeof (skillApi as Record<string, unknown>)[m]).toBe('function'))
  })

  it('workbenchApi exposes task contract methods', async () => {
    const { workbenchApi } = await import('@/api')
    expect(typeof workbenchApi.generateTaskContract).toBe('function')
    expect(typeof workbenchApi.reviewTaskContract).toBe('function')
    expect(typeof workbenchApi.bindTaskContract).toBe('function')
  })

  it('complianceApi exposes checkContent and not the broken validate', async () => {
    const { complianceApi } = await import('@/api')
    expect(typeof complianceApi.checkContent).toBe('function')
    expect('validate' in complianceApi).toBe(false)
  })

  it('CodingAgentStreamClient forwards runtime metadata in session_ready', async () => {
    const { CodingAgentStreamClient } = await import('@/api/stream')
    const client = new CodingAgentStreamClient('skill-1')
    let payload: unknown = null
    client.onSessionReady = (msg) => { payload = msg }
    client._handleMessage({
      data: JSON.stringify({
        type: 'session_ready',
        session_id: 'sess-1',
        model: 'glm-5',
        tools: ['Read', 'Edit'],
        work_dir: '/tmp/skill-1',
        prompt_hash: 'abc123def456',
        config_dir: '/tmp/cfg',
        runtime_profile: { provider: 'glm', permission_strategy: 'balanced', bare_mode: true },
      }),
    } as MessageEvent<string>)
    expect(payload).toEqual({
      session_id: 'sess-1',
      model: 'glm-5',
      tools: ['Read', 'Edit'],
      work_dir: '/tmp/skill-1',
      prompt_hash: 'abc123def456',
      config_dir: '/tmp/cfg',
      runtime_profile: { provider: 'glm', permission_strategy: 'balanced', bare_mode: true },
    })
  })

  it('CodingAgentStreamClient includes mode when sending message', async () => {
    const { CodingAgentStreamClient } = await import('@/api/stream')
    const client = new CodingAgentStreamClient('skill-1')
    client.connected = true
    let sent: Record<string, unknown> | null = null
    ;(client as unknown as Record<string, unknown>).ws = {
      readyState: 1,
      send(payload: string) { sent = JSON.parse(payload) as Record<string, unknown> },
    }
    client.sendMessage('hello', { mode: 'review' } as Record<string, unknown>)
    expect(sent?.['mode']).toBe('review')
    expect(sent?.['content']).toBe('hello')
  })

  it('buildAiclawWsUrl builds ws url from current location', async () => {
    const { buildAiclawWsUrl } = await import('@/api/aiclawWs')
    expect(buildAiclawWsUrl('inst 1', 'agent/2')).toContain('/api/aiclaw/instances/inst%201/chat/agent%2F2/stream')
  })

  it('createAiclawChatSocket parses json messages and falls back to raw payload', async () => {
    const addEventListener: Record<string, (event: { data: string }) => void> = {}
    class MockWebSocket {
      addEventListener(name: string, cb: (event: { data: string }) => void) { addEventListener[name] = cb }
    }
    const originalWs = globalThis.WebSocket
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket
    try {
      const { createAiclawChatSocket } = await import('@/api/aiclawWs')
      const messages: Array<Record<string, string>> = []
      createAiclawChatSocket('inst', 'agent', {
        onMessage: (msg) => messages.push(msg as Record<string, string>),
      })
      addEventListener.message?.({ data: '{"type":"delta","content":"ok"}' } as MessageEvent<string>)
      addEventListener.message?.({ data: 'not-json' } as MessageEvent<string>)
      expect(messages).toEqual([
        { type: 'delta', content: 'ok' },
        { type: 'raw', payload: 'not-json' },
      ])
    } finally {
      globalThis.WebSocket = originalWs
    }
  })
})
