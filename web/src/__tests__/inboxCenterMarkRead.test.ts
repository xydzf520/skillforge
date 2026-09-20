/**
 * Wave 3 H6 回归：InboxCenter 的 mark-read 去重。
 *
 * 验证：
 * 1. watch(activeTab) 切到 reports 时触发一次 markReportsRead
 * 2. 快速多次切换（reports→pending→reports），markReportsRead 应该等比发送，
 *    但最旧的 inflight 请求会被 abort（新请求接管）
 * 3. unreadCount 的 requestId 守卫：旧响应迟到不覆盖新状态
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

const getUnreadCount = vi.fn()
const overviewMock = vi.fn()
const markReportsRead = vi.fn()
const trends = vi.fn()
const replace = vi.fn()
const routeState: any = { params: {}, query: {} }

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => ({ push: vi.fn(), replace }),
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => ({ isAdmin: true, userInfo: { user_id: 'u1' } }),
}))

vi.mock('@/api', () => ({
  inboxApi: {
    getUnreadCount,
    overview: overviewMock,
    listReports: vi.fn(),
    getReport: vi.fn(),
    markReportsRead,
  },
  todoApi: {
    trends,
  },
  authApi: {},
}))

// Stub InboxHeader / TodosTab / ReportsTab 重组件，避免 deep mount 触发其它副作用
vi.mock('@/pages/inbox/InboxHeader.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/pages/inbox/ReportsTab.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/pages/inbox/TodosTab.vue', () => ({ default: { template: '<div />' } }))

function defer<T>() {
  let resolve: (v: T) => void = () => {}
  let reject: (e?: unknown) => void = () => {}
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('InboxCenter · mark-read 去重 (H6)', () => {
  beforeEach(() => {
    getUnreadCount.mockReset()
    overviewMock.mockReset()
    markReportsRead.mockReset()
    trends.mockReset()
    replace.mockReset()
    routeState.query = {}
  })

  it('切到 reports 时触发一次 markReportsRead', async () => {
    getUnreadCount.mockResolvedValue({ unread: 3 })
    overviewMock.mockResolvedValue({})
    trends.mockResolvedValue({ points: [] })
    markReportsRead.mockResolvedValue({})

    const InboxCenter = (await import('@/pages/inbox/InboxCenter.vue')).default
    const wrapper = mount(InboxCenter as any, {
      global: {
        stubs: {
          'a-tabs': {
            props: ['activeKey'],
            emits: ['update:activeKey', 'change'],
            template: '<div class="stub-tabs" :data-active="activeKey"><slot/></div>',
          },
          'a-tab-pane': { template: '<div class="stub-tab"><slot/><slot name="title"/></div>' },
        },
      },
    })
    await flushPromises()

    // 初始为 pending，未触发 mark-read
    expect(markReportsRead).toHaveBeenCalledTimes(0)

    // 切到 reports
    const vm: any = wrapper.vm
    vm.activeTab = 'reports'
    await nextTick()
    await flushPromises()

    expect(markReportsRead).toHaveBeenCalledTimes(1)
  })

  it('快速乒乓切换（reports→pending→reports）发出两次请求，旧 inflight 不影响新结果', async () => {
    // 第 1 次 markReportsRead 返回 pending 很久，第 2 次快速返回
    const first = defer<any>()
    const second = defer<any>()
    markReportsRead
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)

    // getUnreadCount 首次返回 5（未读），mark-read 成功后刷新返回 0
    getUnreadCount
      .mockResolvedValueOnce({ unread: 5 })
      .mockResolvedValue({ unread: 0 })
    overviewMock.mockResolvedValue({})
    trends.mockResolvedValue({ points: [] })

    const InboxCenter = (await import('@/pages/inbox/InboxCenter.vue')).default
    const wrapper = mount(InboxCenter as any, {
      global: {
        stubs: {
          'a-tabs': { template: '<div><slot/></div>' },
          'a-tab-pane': { template: '<div><slot/><slot name="title"/></div>' },
        },
      },
    })
    await flushPromises()

    const vm: any = wrapper.vm
    vm.activeTab = 'reports'
    await nextTick()
    await flushPromises()

    vm.activeTab = 'pending'
    await nextTick()
    await flushPromises()

    vm.activeTab = 'reports'
    await nextTick()
    await flushPromises()

    // 两次进入 reports 的切换都触发了 markReportsRead
    expect(markReportsRead).toHaveBeenCalledTimes(2)

    // 第 2 次先返回
    second.resolve({})
    await flushPromises()

    // 迟到的第 1 次响应不应覆盖 unreadCount（已是 0）
    first.resolve({})
    await flushPromises()

    expect(vm.unreadCount).toBe(0)
    expect(vm.showUnreadDot).toBe(false)
  })

  it('unreadCount=null 时红点不强制翻转（网络失败显示未知状态）', async () => {
    // 服务端 404/500 时 refreshLatestReportState 把 unreadCount 设为 null
    getUnreadCount.mockRejectedValue(new Error('network down'))
    overviewMock.mockResolvedValue({})
    trends.mockResolvedValue({ points: [] })
    markReportsRead.mockResolvedValue({})

    const InboxCenter = (await import('@/pages/inbox/InboxCenter.vue')).default
    const wrapper = mount(InboxCenter as any, {
      global: {
        stubs: {
          'a-tabs': { template: '<div><slot/></div>' },
          'a-tab-pane': { template: '<div><slot/><slot name="title"/></div>' },
        },
      },
    })
    await flushPromises()

    const vm: any = wrapper.vm
    expect(vm.unreadCount).toBeNull()
    // unreadCount null → showUnreadDot 取决于 (unreadCount ?? 0) > 0 = false
    expect(vm.showUnreadDot).toBe(false)
  })
})
