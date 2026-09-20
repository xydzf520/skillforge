import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  projectApi: {
    ensureSdkCheckProject: vi.fn(),
    listSdkTokens: vi.fn(),
    createSdkToken: vi.fn(),
    revokeSdkToken: vi.fn(),
    models236: vi.fn(),
    sdkCheckRun: vi.fn(),
  },
  message: {
    success: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    error: vi.fn(),
  },
}))

vi.mock('@/api', () => ({
  projectApi: mocks.projectApi,
}))

vi.mock('@arco-design/web-vue', () => ({
  Message: mocks.message,
}))

import SfSdkCheck from '@/pages/sf/SfSdkCheck.vue'

const modelCatalog = {
  object: 'list',
  gateway: {
    id: 'inference-primary',
    name: 'Mac 236 · M3 Ultra',
    online: true,
    bridge_platform: 'darwin',
    bridge_gateway_kind: 'openclaw',
    credential_location: 'platform_only',
  },
  total: 2,
  callable_count: 2,
  loaded_count: 1,
  data: [
    {
      id: 'skillforge-base-test-236',
      model: 'skillforge-base-test-236',
      gateway_id: 'inference-primary',
      runtime_profile: 'mlx-test',
      deployment_id: 'deploy-active',
      status: 'loaded',
      loaded: true,
      callable: true,
    },
    {
      id: 'qwen-extra-test-236',
      model: 'qwen-extra-test-236',
      gateway_id: 'inference-primary',
      runtime_profile: 'mlx-extra',
      status: 'registered',
      loaded: false,
      callable: true,
    },
  ],
}

function response(body: unknown, status = 200): Response {
  return new Response(typeof body === 'string' ? body : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': typeof body === 'string' ? 'text/javascript' : 'application/json' },
  })
}

describe('SfSdkCheck page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.projectApi.ensureSdkCheckProject.mockResolvedValue({
      id: 'skillforge-company-sdk',
      name: 'SkillForge Company SDK',
      type: 'internal_tool',
      visibility: 'company',
      status: 'published',
      department_id: null,
      department: null,
      metadata: {
        capabilities: ['ai.analyze', 'ai.chat', 'ai.generate'],
        sdk_check: {
          enabled: true,
          openai_236_models_url: '/api/projects/openai/236/v1/models',
          openai_236_chat_url: '/api/projects/openai/236/v1/chat/completions',
        },
        training_sink: {
          target_gateway_id: 'GB10 237',
          mode: 'async_dataset',
        },
      },
      permissions: { read: true, launch: true, edit: true },
      runtime: { gateway: 'skillforge_project_gateway', sdk_path: '/project-gateway-sdk.js' },
    })
    mocks.projectApi.listSdkTokens.mockResolvedValue({
      items: [{
        id: 'psdk-old',
        name: 'company-backend',
        token_prefix: 'sfproj_oldprefix',
        status: 'active',
        expires_at: '2026-12-31T00:00:00+08:00',
      }],
    })
    mocks.projectApi.createSdkToken.mockResolvedValue({
      id: 'psdk-new',
      name: 'company-backend',
      token_prefix: 'sfproj_newprefix',
      status: 'active',
      token: 'sfproj_new_secret_once',
      token_returned_once: true,
      expires_at: '2026-12-31T00:00:00+08:00',
    })
    mocks.projectApi.revokeSdkToken.mockResolvedValue({
      id: 'psdk-old',
      name: 'company-backend',
      token_prefix: 'sfproj_oldprefix',
      status: 'revoked',
    })
    mocks.projectApi.models236.mockResolvedValue(modelCatalog)
    mocks.projectApi.sdkCheckRun.mockResolvedValue({
      ok: true,
      project: { id: 'skillforge-company-sdk', name: 'SkillForge Company SDK' },
      run: { id: 'prun-sdk-check-1', status: 'completed', output_model: 'skillforge-base-test-236', output_data_sink: 'GB10 237' },
      capability_calls: [{ id: 'pcall-sdk-check-1', status: 'completed', model: 'skillforge-base-test-236', data_sink: 'GB10 237' }],
      training_sink_jobs: [{ id: 1, status: 'pending', target_gateway_id: 'GB10 237' }],
      counts: { capability_calls: 1, completed_capability_calls: 1, gb10_237_jobs: 1 },
    })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/project-gateway-sdk.js')) {
        return response('window.PlatformProjectGateway = {}')
      }
      if (url.includes('/api/projects/openai/236/v1/models')) {
        expect((init?.headers as Record<string, string>)?.Authorization).toBe('Bearer sfproj_new_secret_once')
        return response(modelCatalog)
      }
      if (url.includes('/api/projects/openai/236/v1/chat/completions')) {
        expect((init?.headers as Record<string, string>)?.Authorization).toBe('Bearer sfproj_new_secret_once')
        return response({
          id: 'chatcmpl-sdk-check-1',
          object: 'chat.completion',
          model: 'skillforge-base-test-236',
          choices: [{ index: 0, message: { role: 'assistant', content: 'ok' }, finish_reason: 'stop' }],
          skillforge: {
            project_run_id: 'prun-sdk-check-1',
            capability_call_id: 'pcall-sdk-check-1',
            training_sink: { target_gateway_id: 'GB10 237', status: 'pending' },
          },
        })
      }
      return response({ error: { code: 'NOT_FOUND' } }, 404)
    }))
  })

  it('shows the SDK key panel, 236 model catalog, URLs, and GB10 sink target', async () => {
    const wrapper = mount(SfSdkCheck, { global: { stubs: { SfShellIcon: true } } })
    await flushPromises()

    expect(mocks.projectApi.ensureSdkCheckProject).toHaveBeenCalled()
    expect(mocks.projectApi.listSdkTokens).toHaveBeenCalledWith('skillforge-company-sdk')
    expect(mocks.projectApi.models236).toHaveBeenCalled()
    expect(wrapper.text()).toContain('skillforge-company-sdk')
    expect(wrapper.text()).toContain('API Key')
    expect(wrapper.text()).toContain('236 Models')
    expect(wrapper.text()).toContain('/api/projects/openai/236/v1/chat/completions')
    expect(wrapper.text()).toContain('GB10 237')
    expect(wrapper.text()).toContain('skillforge-base-test-236')
    expect(wrapper.text()).toContain('qwen-extra-test-236')
    expect(wrapper.text()).toContain('sfproj_oldprefix')
  })

  it('creates a one-time API key and verifies a 236 chat completion through the external SDK URL path', async () => {
    const wrapper = mount(SfSdkCheck, { global: { stubs: { SfShellIcon: true } } })
    await flushPromises()

    const createButton = wrapper.findAll('button').find(item => item.text().includes('创建 API Key'))
    expect(createButton?.exists()).toBe(true)
    await createButton?.trigger('click')
    await flushPromises()

    expect(mocks.projectApi.createSdkToken).toHaveBeenCalledWith('skillforge-company-sdk', expect.objectContaining({
      name: 'company-backend',
      expires_in_days: 365,
      metadata: { source: 'sf_sdk_check_page' },
    }))
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('sfproj_new_secret_once')

    const verifyButton = wrapper.findAll('button').find(item => item.text().trim() === '验证')
    expect(verifyButton?.exists()).toBe(true)
    await verifyButton?.trigger('click')
    await flushPromises()
    await flushPromises()

    expect(globalThis.fetch).toHaveBeenCalledWith(expect.stringContaining('/project-gateway-sdk.js'), expect.any(Object))
    expect(globalThis.fetch).toHaveBeenCalledWith(expect.stringContaining('/api/projects/openai/236/v1/models'), expect.any(Object))
    expect(globalThis.fetch).toHaveBeenCalledWith(expect.stringContaining('/api/projects/openai/236/v1/chat/completions'), expect.any(Object))
    expect(mocks.projectApi.sdkCheckRun).toHaveBeenCalledWith('prun-sdk-check-1')
    expect(wrapper.text()).toContain('completed')
    expect(wrapper.text()).toContain('pending')
    expect(wrapper.text()).toContain('ok')
  })
})
