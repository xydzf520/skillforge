import { shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  route: {
    path: '/hall/abilities/gpt-imagegen',
    params: { id: 'gpt-imagegen' } as Record<string, string>,
  },
  router: {
    replace: vi.fn(),
  },
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => mocks.router,
}))

vi.mock('@/api', () => ({
  trainingApi: {
    fullHistoryFinetuneStatus: vi.fn().mockResolvedValue({}),
    runFullHistoryFinetune: vi.fn(),
    deployments: vi.fn().mockResolvedValue({ items: [] }),
    chatDeploymentModel: vi.fn(),
  },
  hallApi: {
    directCapability: vi.fn(),
    directCapabilityUi: vi.fn(),
    directCapabilityImageHistory: vi.fn(),
    directCapabilityTasks: vi.fn(),
  },
}))

import HallDirectCapabilityDetail from '@/pages/hall/HallDirectCapabilityDetail.vue'

describe('Hall direct capability detail routing', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.route.path = '/hall/abilities/gpt-imagegen'
    mocks.route.params = { id: 'gpt-imagegen' }
  })

  it('routes legacy finetuned model ability URLs to the model chat page', () => {
    mocks.route.path = '/hall/abilities/skillforge-finetuned-model-chat'
    mocks.route.params = { id: 'skillforge-finetuned-model-chat' }

    const wrapper = shallowMount(HallDirectCapabilityDetail, {
      global: {
        stubs: {
          HallFinetunedModelChat: { name: 'HallFinetunedModelChat', template: '<section data-testid="finetuned-chat" />' },
          HallGptImageGenForm: { name: 'HallGptImageGenForm', template: '<section data-testid="imagegen-form" />' },
          HallGptImageGenChat: { name: 'HallGptImageGenChat', template: '<section data-testid="imagegen-chat" />' },
        },
      },
    })

    expect(mocks.router.replace).toHaveBeenCalledWith('/hall/finetuned-model-chat')
    expect(wrapper.find('[data-testid="finetuned-chat"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="imagegen-form"]').exists()).toBe(false)
  })

  it('routes ai-chat to its durable native chat page', () => {
    mocks.route.path = '/hall/abilities/ai-chat'
    mocks.route.params = { id: 'ai-chat' }

    const wrapper = shallowMount(HallDirectCapabilityDetail, {
      global: {
        stubs: {
          HallAiChat: { name: 'HallAiChat', template: '<section data-testid="ai-chat" />' },
          HallGptImageGenForm: { name: 'HallGptImageGenForm', template: '<section data-testid="imagegen-form" />' },
          HallGptImageGenChat: { name: 'HallGptImageGenChat', template: '<section data-testid="imagegen-chat" />' },
        },
      },
    })

    expect(wrapper.find('[data-testid="ai-chat"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="imagegen-form"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="imagegen-chat"]').exists()).toBe(false)
  })
})
