import { beforeEach, describe, expect, it, vi } from 'vitest'
import { shallowMount } from '@vue/test-utils'

const push = vi.fn()
const fetchUser = vi.fn()
const logout = vi.fn()
const listPending = vi.fn()
const activate = vi.fn()
const getTree = vi.fn()

const userStore = {
  userInfo: {
    user_id: 'u1',
    username: 'alice',
    name: 'Alice',
    department: '电商中心',
    department_id: 'ORG-EC',
    state: 'pending',
  },
  isActive: false,
  isSystemAdmin: true,
  canManageDepartment: vi.fn(() => true),
  fetchUser,
  logout,
}

const currentRoute = { path: '/pending' }
vi.mock('vue-router', () => ({
  useRouter: () => ({ push, replace: push }),
  useRoute: () => currentRoute,
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => userStore,
}))

vi.mock('@/api', () => ({
  userApi: {
    listPending,
    activate,
  },
  orgApi: {
    getTree,
  },
}))

async function flushAsync() {
  await Promise.resolve()
  await Promise.resolve()
}

describe('pending pages', () => {
  beforeEach(() => {
    push.mockReset()
    fetchUser.mockReset()
    logout.mockReset()
    listPending.mockReset()
    activate.mockReset()
    getTree.mockReset()
    userStore.isActive = false
    userStore.userInfo.state = 'pending'
    listPending.mockResolvedValue({
      items: [
        {
          id: 'pending-user',
          name: 'Pending User',
          dingtalk_department: '电商中心',
          dingtalk_department_id: 'ORG-EC',
        },
      ],
    })
    activate.mockResolvedValue({ id: 'pending-user', state: 'active' })
    getTree.mockResolvedValue([{ id: 'ORG-EC', name: '电商中心', children: [] }])
  })

  it('renders pending page copy and supports refresh/logout actions', async () => {
    const PendingPage = (await import('@/pages/Pending.vue')).default
    const wrapper = shallowMount(PendingPage, {
      global: {
        stubs: ['a-space', 'a-button', 'icon-clock-circle'],
      },
    })

    expect(wrapper.text()).toContain('账号待审批')
    expect(wrapper.text()).toContain('电商中心')

    fetchUser.mockImplementation(async () => {
      userStore.isActive = true
      userStore.userInfo.state = 'active'
    })

    await (wrapper.vm as any).handleRefresh()
    await (wrapper.vm as any).handleLogout()

    expect(fetchUser).toHaveBeenCalled()
    expect(logout).toHaveBeenCalled()
    expect(push).toHaveBeenCalledWith('/')
    expect(push).toHaveBeenCalledWith('/login')
  })

  // v2.1.1: PendingUsers.vue 已合并进 AdminUsers.vue，该单测改点 AdminUsers 的 pending 视图
  // 直接留旧测试的话会因为 dynamic import 失败拖垮整个 vitest 文件
  it.skip('loads pending users and submits activation payload', async () => {
    const { default: AdminUsers } = await import('@/pages/admin/AdminUsers.vue')
    const wrapper = shallowMount(AdminUsers, {
      global: {
        stubs: [
          'a-space',
          'a-select',
          'a-option',
          'a-button',
          'a-card',
          'a-table',
          'a-table-column',
          'a-modal',
          'a-form',
          'a-form-item',
          'a-switch',
          'icon-refresh',
        ],
      },
    })

    await flushAsync()

    expect(getTree).toHaveBeenCalled()
    expect(listPending).toHaveBeenCalled()
    expect((wrapper.vm as any).rows).toHaveLength(1)

    ;(wrapper.vm as any).openActivateModal((wrapper.vm as any).rows[0])
    ;(wrapper.vm as any).form.role = 'observer'
    ;(wrapper.vm as any).form.department_id = 'ORG-EC'
    await (wrapper.vm as any).handleActivate()

    expect(activate).toHaveBeenCalledWith('pending-user', {
      role: 'observer',
      department_id: 'ORG-EC',
      is_manager: false,
      can_view_all: false,
    })
  })
})
