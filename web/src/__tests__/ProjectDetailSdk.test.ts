import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  projectApi: {
    get: vi.fn(),
    listSdkTokens: vi.fn(),
    createSdkToken: vi.fn(),
    revokeSdkToken: vi.fn(),
    models236: vi.fn(),
    getRun: vi.fn(),
    getRunTrace: vi.fn(),
    listRunAssets: vi.fn(),
    createRun: vi.fn(),
    heartbeatRun: vi.fn(),
    closeRun: vi.fn(),
    ingest: vi.fn(),
    recordInput: vi.fn(),
    analyze: vi.fn(),
    callCapability: vi.fn(),
    uploadRunAsset: vi.fn(),
  },
  trainingApi: {
    resources: vi.fn(),
    deployments: vi.fn(),
  },
  route: {
    params: { id: 'sdk-page' },
    query: {} as Record<string, unknown>,
    meta: {} as Record<string, unknown>,
    path: '/projects/sdk-page',
  },
  router: {
    push: vi.fn(),
    replace: vi.fn(),
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
  trainingApi: mocks.trainingApi,
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => mocks.router,
}))

vi.mock('@arco-design/web-vue', () => ({
  Message: mocks.message,
}))

import ProjectDetail from '@/pages/project/ProjectDetail.vue'
import projectDetailSource from '@/pages/project/ProjectDetail.vue?raw'

function stubs() {
  return {
    'a-spin': { props: ['loading'], template: '<div><slot /></div>' },
  }
}

describe('ProjectDetail SDK panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.params = { id: 'sdk-page' }
    mocks.route.query = {}
    mocks.route.meta = {}
    mocks.route.path = '/projects/sdk-page'
    mocks.router.replace.mockResolvedValue(undefined)
    mocks.router.push.mockResolvedValue(undefined)
    mocks.projectApi.get.mockResolvedValue({
      id: 'sdk-page',
      name: 'SDK Page',
      type: 'external_web',
      entry_url: 'https://example.com/app',
      department: 'EC',
      metadata: { capabilities: ['ai.generate'] },
      runtime: { gateway: 'project_gateway', sdk_path: '/project-gateway-sdk.js' },
      permissions: { read: true, launch: true, edit: true },
    })
    mocks.projectApi.listSdkTokens.mockResolvedValue({
      items: [{
        id: 'psdk-1',
        name: 'orders-api',
        token_prefix: 'sfproj_orders',
        status: 'active',
        scopes: ['runs:create', 'runs:capability'],
      }],
    })
    mocks.projectApi.createSdkToken.mockResolvedValue({
      id: 'psdk-2',
      name: 'company-backend',
      token_prefix: 'sfproj_new',
      status: 'active',
      token: 'sfproj_new_secret_once',
      token_returned_once: true,
    })
    mocks.projectApi.revokeSdkToken.mockResolvedValue({
      id: 'psdk-1',
      name: 'orders-api',
      token_prefix: 'sfproj_orders',
      status: 'revoked',
    })
    mocks.projectApi.models236.mockResolvedValue({
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
    })
    mocks.trainingApi.resources.mockResolvedValue({
      items: [{
        id: 'inference-primary',
        name: 'macOS 常驻模型节点',
        online: true,
        bridge_platform: 'darwin',
        resident_models: [{
          model: 'qwen3.5-4b-lora',
          deployment_id: 'deploy-active',
          runtime_profile: 'mlx-qwen3.5-4b-lora',
          status: 'loaded',
          loaded: true,
        }],
      }],
    })
    mocks.trainingApi.deployments.mockResolvedValue({
      items: [{
        id: 'deploy-active',
        status: 'active',
        model_family: 'qwen3.5-4b-lora',
        deployment_target_gateway_id: 'inference-primary',
        deployment_runtime_profile: 'mlx-qwen3.5-4b-lora',
      }],
    })
  })

  it('shows token management, GB10 storage, and macOS resident model status', async () => {
    const wrapper = mount(ProjectDetail, { global: { stubs: stubs() } })
    await flushPromises()

    expect(mocks.projectApi.listSdkTokens).toHaveBeenCalledWith('sdk-page')
    expect(mocks.projectApi.models236).toHaveBeenCalled()
    expect(mocks.trainingApi.resources).toHaveBeenCalled()
    expect(mocks.trainingApi.deployments).toHaveBeenCalled()
    expect(wrapper.text()).toContain('公司后端 SDK token')
    expect(wrapper.text()).toContain('sdk/node')
    expect(wrapper.text()).toContain('GB10 237')
    expect(wrapper.text()).toContain('sfproj_orders')
    expect(wrapper.text()).toContain('macOS')
    expect(wrapper.text()).toContain('skillforge-base-test-236')
    expect(wrapper.text()).toContain('qwen-extra-test-236')
    expect(wrapper.text()).toContain('2 个 236 模型')
    expect(wrapper.text()).toContain('可调用')
    expect(wrapper.text()).toContain('inference-primary')
  })

  it('closes orphaned media streams whenever the hosted project iframe reloads', () => {
    expect(projectDetailSource).toContain('@load="handleProjectFrameLoad"')
    expect(projectDetailSource).toContain('function closeMediaEventStreams()')
    expect(projectDetailSource).toContain('closeMediaEventStreams()\n  postRuntimeContext(true)')
  })

  it('forwards real multipart upload progress to the hosted project', () => {
    expect(projectDetailSource).toContain("type: 'skillforge.project.assets.upload.progress'")
    expect(projectDetailSource).toContain("progress: { stage: 'uploading', loaded_bytes: loaded, total_bytes: total, percent }")
    expect(projectDetailSource).toContain('projectApi.uploadRunAsset(activeRun.value.id, form, (event) =>')
  })

  it('does not block the standalone project host on detail-only admin requests', async () => {
    mocks.route.meta = { standaloneProject: true }
    mocks.route.query = { run_id: 'prun-app' }
    mocks.route.path = '/project-run/sdk-page'
    mocks.projectApi.getRun.mockResolvedValue({
      id: 'prun-app',
      project_id: 'sdk-page',
      status: 'running',
      input_snapshot: {},
      output_result: {},
    })
    mocks.projectApi.getRunTrace.mockResolvedValue({
      run: { id: 'prun-app' },
      ingress: [],
      capability_calls: [],
      pagination: {},
    })
    mocks.projectApi.listRunAssets.mockResolvedValue({ items: [] })
    mocks.projectApi.heartbeatRun.mockResolvedValue({
      id: 'prun-app',
      project_id: 'sdk-page',
      status: 'running',
    })

    mount(ProjectDetail, { global: { stubs: stubs() } })
    await flushPromises()

    expect(mocks.projectApi.get).toHaveBeenCalledWith('sdk-page')
    expect(mocks.projectApi.getRun).toHaveBeenCalledWith('prun-app')
    expect(mocks.projectApi.listSdkTokens).not.toHaveBeenCalled()
    expect(mocks.projectApi.models236).not.toHaveBeenCalled()
    expect(mocks.trainingApi.resources).not.toHaveBeenCalled()
    expect(mocks.trainingApi.deployments).not.toHaveBeenCalled()
  })

  it('creates one-time SDK tokens and revokes existing tokens', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mount(ProjectDetail, { global: { stubs: stubs() } })
    await flushPromises()

    const createButton = wrapper.findAll('button').find(item => item.text().includes('创建 token'))
    expect(createButton?.exists()).toBe(true)
    await createButton?.trigger('click')
    await flushPromises()

    expect(mocks.projectApi.createSdkToken).toHaveBeenCalledWith('sdk-page', expect.objectContaining({
      name: 'company-backend',
      expires_in_days: 365,
    }))
    expect(wrapper.text()).toContain('sfproj_new_secret_once')

    const revokeButton = wrapper.findAll('button').find(item => item.text().trim() === '吊销')
    expect(revokeButton?.exists()).toBe(true)
    await revokeButton?.trigger('click')
    await flushPromises()

    expect(mocks.projectApi.revokeSdkToken).toHaveBeenCalledWith('sdk-page', 'psdk-1')
    expect(wrapper.text()).toContain('revoked')
  })
})
