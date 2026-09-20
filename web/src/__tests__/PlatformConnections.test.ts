import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PlatformConnections from '@/pages/connections/PlatformConnections.vue'

const apiMocks = vi.hoisted(() => ({
  list: vi.fn(),
  listConnectorKeys: vi.fn(),
  getLegacyKey: vi.fn(),
  rotateLegacyKey: vi.fn(),
  createConnectorKey: vi.fn(),
  rotateConnectorKey: vi.fn(),
  revokeConnectorKey: vi.fn(),
  cookiePoolSummary: vi.fn(),
  getCookies: vi.fn(),
  update: vi.fn(),
  verifyLogin: vi.fn(),
}))

const messageMocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('@/api', () => ({
  datasourceApi: {
    list: apiMocks.list,
    getCookies: apiMocks.getCookies,
    update: apiMocks.update,
  },
  browserApi: {
    verifyLogin: apiMocks.verifyLogin,
  },
  connectorKeyApi: {
    list: apiMocks.listConnectorKeys,
    getLegacyKey: apiMocks.getLegacyKey,
    rotateLegacyKey: apiMocks.rotateLegacyKey,
    create: apiMocks.createConnectorKey,
    rotate: apiMocks.rotateConnectorKey,
    revoke: apiMocks.revokeConnectorKey,
  },
  cookiePoolApi: {
    summary: apiMocks.cookiePoolSummary,
  },
}))

vi.mock('@arco-design/web-vue', () => ({
  Message: {
    success: messageMocks.success,
    error: messageMocks.error,
    warning: messageMocks.warning,
  },
}))

vi.mock('@arco-design/web-vue/es/icon', () => ({
  IconLink: { template: '<i />' },
  IconSettings: { template: '<i />' },
  IconDown: { template: '<i />' },
  IconRight: { template: '<i />' },
  IconDownload: { template: '<i />' },
  IconUpload: { template: '<i />' },
  IconSync: { template: '<i />' },
  IconLaunch: { template: '<i />' },
}))

vi.mock('@/utils/clipboard', () => ({
  copyText: vi.fn().mockResolvedValue(true),
}))

function mountPage() {
  return mount(PlatformConnections, {
    global: {
      stubs: {
        'a-button': {
          emits: ['click'],
          template: '<button type="button" @click="$emit(\'click\', $event)"><slot /></button>',
        },
        'a-space': {
          template: '<div><slot /></div>',
        },
        'a-tag': {
          template: '<span><slot /></span>',
        },
        'a-tooltip': {
          template: '<div><slot /></div>',
        },
        'a-modal': {
          props: ['visible'],
          template: '<div v-if="visible"><slot /></div>',
        },
        'a-alert': {
          template: '<div><slot /><slot name="title" /></div>',
        },
        'a-input': {
          props: ['modelValue', 'readonly'],
          template: '<input :value="modelValue" :readonly="readonly" />',
        },
        'a-divider': {
          template: '<hr />',
        },
        'a-empty': {
          template: '<div />',
        },
        'a-form': {
          template: '<form><slot /></form>',
        },
        'a-form-item': {
          template: '<label><slot /></label>',
        },
        'a-input-password': {
          props: ['modelValue'],
          template: '<input :value="modelValue" />',
        },
      },
    },
  })
}

describe('PlatformConnections', () => {
  beforeEach(() => {
    apiMocks.list.mockReset()
    apiMocks.listConnectorKeys.mockReset()
    apiMocks.getLegacyKey.mockReset()
    apiMocks.rotateLegacyKey.mockReset()
    apiMocks.createConnectorKey.mockReset()
    apiMocks.rotateConnectorKey.mockReset()
    apiMocks.revokeConnectorKey.mockReset()
    apiMocks.cookiePoolSummary.mockReset()
    apiMocks.getCookies.mockReset()
    apiMocks.update.mockReset()
    apiMocks.verifyLogin.mockReset()
    messageMocks.success.mockReset()
    messageMocks.error.mockReset()
    messageMocks.warning.mockReset()

    apiMocks.list.mockResolvedValue([])
    apiMocks.listConnectorKeys.mockResolvedValue([])
    apiMocks.getLegacyKey.mockResolvedValue({ api_key: 'old-key' })
    apiMocks.rotateLegacyKey.mockResolvedValue({ api_key: 'new-key' })
    apiMocks.cookiePoolSummary.mockResolvedValue([])
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('regenerates connector api key via rotate endpoint', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(apiMocks.getLegacyKey).toHaveBeenCalledTimes(1)

    const openModalButton = wrapper.findAll('button').find((button) => button.text().includes('API Key 管理'))
    expect(openModalButton).toBeTruthy()
    await openModalButton!.trigger('click')
    await flushPromises()

    const regenerateButton = wrapper.findAll('button').find((button) => button.text().includes('重新生成 Legacy Key'))
    expect(regenerateButton).toBeTruthy()
    await regenerateButton!.trigger('click')
    await flushPromises()

    expect(apiMocks.rotateLegacyKey).toHaveBeenCalledTimes(1)
    expect(apiMocks.getLegacyKey).toHaveBeenCalledTimes(1)
    expect(messageMocks.success).toHaveBeenCalledWith('API Key 已重新生成，请更新 Chrome 扩展配置')

    const inputValues = wrapper.findAll('input').map((input) => (input.element as HTMLInputElement).value)
    expect(inputValues).toContain('new-key')
  })

  it('separates cookie sync from uncertain login heartbeat', async () => {
    apiMocks.list.mockResolvedValue([
      {
        id: 'platform-sycm',
        department: '电商',
        config: {
          platform: 'sycm',
          connected: true,
          pushed_at: '2026-04-22T07:59:50.639494',
          verify_status: 'expired',
          verify_detail: '业务 API 返回非常规状态 None',
          last_verified_at: '2026-04-22T08:52:43.417328',
        },
      },
    ])

    const wrapper = mountPage()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('Cookie 已同步')
    expect(text).toContain('登态：待复核')
    expect(text).toContain('业务接口：待复核')
    expect(text).not.toContain('已连接')
  })

  it('shows cookie pool latest sync time when platform config is stale', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-14T10:00:00+08:00'))
    apiMocks.list.mockResolvedValue([
      {
        id: 'platform-alimama',
        department: '电商',
        config: {
          platform: 'alimama',
          connected: true,
          pushed_at: '2026-05-12T18:00:37+08:00',
          verify_status: 'unknown',
        },
      },
    ])
    apiMocks.cookiePoolSummary.mockResolvedValue([
      {
        platform: 'alimama',
        shop_id: 'shop-001',
        active_count: 1,
        total_count: 1,
        latest_sync_at: '2026-05-14T09:16:04+08:00',
      },
    ])

    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.text()).toContain('同步：43 分钟前')
    expect(wrapper.text()).not.toContain('同步：1 天前')
  })
})
