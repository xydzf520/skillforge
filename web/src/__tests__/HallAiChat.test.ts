import { flushPromises, shallowMount } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  models: vi.fn(),
  threads: vi.fn(),
  messages: vi.fn(),
  updateThread: vi.fn(),
  updatePreference: vi.fn(),
  deleteThread: vi.fn(),
  uploadAttachment: vi.fn(),
}))

vi.mock('@/api', () => ({
  hallApi: {
    aiChatModels: mocks.models,
    aiChatThreads: mocks.threads,
    aiChatMessages: mocks.messages,
    createAiChatThread: vi.fn(),
    updateAiChatThread: mocks.updateThread,
    updateAiChatPreference: mocks.updatePreference,
    deleteAiChatThread: mocks.deleteThread,
    uploadAiChatAttachment: mocks.uploadAttachment,
    cancelAiChatMessage: vi.fn(),
  },
}))

vi.mock('@/utils/renderMd', () => ({ renderMd: (value: string) => value }))

import HallAiChat from '@/pages/hall/HallAiChat.vue'

describe('HallAiChat', () => {
  it('keeps long conversations inside the message viewport instead of pushing the composer off-screen', () => {
    const source = readFileSync('src/pages/hall/HallAiChat.vue', 'utf8')

    expect(source).toContain('.ai-chat-page { width: 100%; max-width: 100vw; height: 100%; min-height: 0;')
    expect(source).toContain('.chat-main { min-width: 0; min-height: 0; height: 100%;')
    expect(source).toContain('overflow: hidden; background: #fff; }')
  })

  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
    mocks.models.mockResolvedValue({
      default_model: 'deepseek-v4-pro',
      preferred_model: 'gpt-5.4-mini',
      vision: { ready: true, model: 'gpt-5.4-mini' },
      groups: [
        {
          id: 'domestic', name: '国产模型', configured: true,
          models: [{
            id: 'deepseek-v4-pro', name: 'deepseek-v4-pro', available: true, status: 'available',
            context_window_tokens: 1_000_000, chat_context_tokens: 800_000, max_output_tokens: 384_000, chat_max_output_tokens: 32_768,
            reasoning_options: ['auto', 'none', 'high'],
          }],
        },
        {
          id: 'claude_max', name: 'Claude Max', configured: true,
          models: [{ id: 'claude-opus-4-8', name: 'claude-opus-4-8', available: false, status: 'unavailable', reason: '模型不存在' }],
        },
        {
          id: 'gpt_pro', name: 'GPT Pro', configured: true,
          models: [{
            id: 'gpt-5.4-mini', name: 'gpt-5.4-mini', available: true, status: 'available', supports_vision: true,
            context_window_tokens: 400_000, chat_context_tokens: 256_000, max_output_tokens: 128_000, chat_max_output_tokens: 128_000,
            reasoning_options: ['auto', 'none', 'low', 'medium', 'high', 'xhigh'],
          }],
        },
      ],
    })
    mocks.threads.mockResolvedValue({ items: [], next_cursor: null, has_more: false })
    mocks.messages.mockResolvedValue({ items: [], next_cursor: null, has_more: false })
    mocks.uploadAttachment.mockResolvedValue({
      id: 'asset-1', name: 'clipboard.png', mime_type: 'image/png',
      thumbnail_url: '/api/hall/ai-chat/attachments/asset-1/content?thumbnail=true',
    })
  })

  it('uses a compact grouped model menu and hides unhealthy models', async () => {
    const wrapper = shallowMount(HallAiChat, {
      global: { mocks: { $router: { push: vi.fn() } } },
    })
    await flushPromises()

    expect(wrapper.find('.model-trigger').exists()).toBe(true)
    expect(wrapper.find('.model-trigger').element.closest('.composer-card')).not.toBeNull()
    expect(wrapper.find('.attach-button').text()).toBe('＋')
    expect(wrapper.text()).not.toContain('claude-opus-4-8')
    expect(wrapper.find('.model-trigger').text()).toContain('gpt-5.4-mini')
    expect(wrapper.text()).toContain('连续记忆已开启 · 仅自己可见')
    expect(wrapper.find('.model-trigger').text()).not.toContain('官方上限')
    expect(wrapper.find('.reasoning-row').exists()).toBe(false)
    expect(wrapper.find('.reasoning-trigger').text()).toContain('自动')
    expect(wrapper.find('.vision-indicator').attributes('aria-label')).toBe('图片识别状态')
    expect(wrapper.find('.send-button').attributes('aria-label')).toBe('发送')
    expect(wrapper.find('.send-button').text()).not.toContain('发送')
    expect(wrapper.find('.send-arrow-icon').exists()).toBe(true)
    expect((wrapper.vm as any).modelGroups.flatMap((group: any) => group.models).find((model: any) => model.id === 'gpt-5.4-mini').supports_vision).toBe(true)
    wrapper.unmount()
  })

  it('renders the built-in model catalog immediately without waiting for metadata refresh', async () => {
    mocks.models.mockReturnValue(new Promise(() => undefined))
    const wrapper = shallowMount(HallAiChat, {
      global: { mocks: { $router: { push: vi.fn() } } },
    })
    await Promise.resolve()

    expect(wrapper.find('.model-trigger').text()).toContain('deepseek-v4-pro')
    expect(wrapper.find('.attach-button').text()).toBe('＋')
    expect(wrapper.text()).not.toContain('正在检查图片')
    expect((wrapper.vm as any).modelGroups).toHaveLength(3)
    expect((wrapper.vm as any).visionReady).toBe(true)
    wrapper.unmount()
  })

  it('remembers reasoning depth separately for each model', async () => {
    window.localStorage.setItem('skillforge.ai-chat.reasoning.gpt-5.4-mini', 'high')
    const wrapper = shallowMount(HallAiChat, {
      global: { mocks: { $router: { push: vi.fn() } } },
    })
    await flushPromises()

    expect((wrapper.vm as any).reasoningEffort).toBe('high')
    ;(wrapper.vm as any).chooseReasoningEffort('low')
    await flushPromises()
    expect(window.localStorage.getItem('skillforge.ai-chat.reasoning.gpt-5.4-mini')).toBe('low')
    wrapper.unmount()
  })

  it('makes conversation renaming discoverable from the title and action menu', async () => {
    mocks.threads.mockResolvedValue({
      items: [{ id: 'thread-1', title: '项目复盘', default_model: 'gpt-5.4-mini', message_count: 2, archived: false }],
      next_cursor: null,
      has_more: false,
    })
    const wrapper = shallowMount(HallAiChat, {
      global: { mocks: { $router: { push: vi.fn() } } },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('点击修改名称')
    await wrapper.find('.chat-title').trigger('click')
    expect((wrapper.vm as any).renaming).toBe(true)
    wrapper.unmount()
  })

  it('captures plain text pasted outside the textarea into the composer', async () => {
    const wrapper = shallowMount(HallAiChat, {
      global: { mocks: { $router: { push: vi.fn() } } },
    })
    await flushPromises()
    await wrapper.find('.ai-chat-page').trigger('paste', {
      clipboardData: { files: [], getData: () => '从剪贴板粘贴的内容' },
    })

    expect((wrapper.vm as any).draft).toBe('从剪贴板粘贴的内容')
    wrapper.unmount()
  })

  it('uploads a mirrored clipboard screenshot only once', async () => {
    const wrapper = shallowMount(HallAiChat, {
      global: { mocks: { $router: { push: vi.fn() } } },
    })
    await flushPromises()
    const image = new File(['png'], 'clipboard.png', { type: 'image/png' })
    const mirroredFile = new File(['png'], 'image.png', { type: 'image/png', lastModified: image.lastModified + 1 })
    await wrapper.find('.ai-chat-page').trigger('paste', {
      clipboardData: {
        files: [mirroredFile],
        items: [{ kind: 'file', type: 'image/png', getAsFile: () => image }],
        getData: () => '',
      },
    })
    await flushPromises()

    expect(mocks.uploadAttachment).toHaveBeenCalledTimes(1)
    expect(mocks.uploadAttachment).toHaveBeenCalledWith(image)
    expect(wrapper.findAll('.pending-image')).toHaveLength(1)
    expect(wrapper.find('.attachment-status').text()).toContain('已添加 1 张图片')
    wrapper.unmount()
  })

  it('normalizes images selected from the plus button before uploading', async () => {
    const close = vi.fn()
    vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({ width: 40, height: 20, close }))
    const contextSpy = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage: vi.fn() } as any)
    const blobSpy = vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation((callback: BlobCallback) => {
      callback(new Blob(['normalized-png'], { type: 'image/png' }))
    })
    const wrapper = shallowMount(HallAiChat, {
      global: { mocks: { $router: { push: vi.fn() } } },
    })
    await flushPromises()
    const picker = wrapper.find<HTMLInputElement>('input[type="file"]')
    const selected = new File(['jpeg-source'], 'photo.jpg', { type: 'image/jpeg' })
    Object.defineProperty(picker.element, 'files', { value: [selected], configurable: true })
    await picker.trigger('change')
    await flushPromises()

    const uploaded = mocks.uploadAttachment.mock.calls[0][0] as File
    expect(uploaded.type).toBe('image/png')
    expect(uploaded.name).toMatch(/^clipboard-.*\.png$/)
    expect(close).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.attachment-status').text()).toContain('已添加 1 张图片')
    wrapper.unmount()
    contextSpy.mockRestore()
    blobSpy.mockRestore()
    vi.unstubAllGlobals()
  })

  it('captures image paste from the window and suppresses a duplicate browser event', async () => {
    const wrapper = shallowMount(HallAiChat, {
      attachTo: document.body,
      global: { mocks: { $router: { push: vi.fn() } } },
    })
    await flushPromises()
    const image = new File(['png'], 'clipboard.png', { type: 'image/png', lastModified: 123 })
    const clipboardData = {
      files: [image],
      items: [{ kind: 'file', type: 'image/png', getAsFile: () => image }],
      getData: () => '',
    }
    for (let index = 0; index < 2; index += 1) {
      const event = new Event('paste', { bubbles: true, cancelable: true }) as ClipboardEvent
      Object.defineProperty(event, 'clipboardData', { value: clipboardData })
      window.dispatchEvent(event)
    }
    await flushPromises()

    expect(mocks.uploadAttachment).toHaveBeenCalledTimes(1)
    expect(wrapper.findAll('.pending-image')).toHaveLength(1)
    wrapper.unmount()
  })

  it('switches to archived history without deleting conversations', async () => {
    const wrapper = shallowMount(HallAiChat, {
      global: { mocks: { $router: { push: vi.fn() } } },
    })
    await flushPromises()
    const archivedTab = wrapper.findAll('.history-tabs button').find(node => node.text() === '已归档')
    expect(archivedTab).toBeTruthy()
    await archivedTab!.trigger('click')
    await flushPromises()

    expect(mocks.threads).toHaveBeenLastCalledWith(expect.objectContaining({ archived: true, limit: 30 }))
    expect(wrapper.text()).toContain('暂无归档对话')
    wrapper.unmount()
  })

  it('reconciles an SSE close without a terminal event from durable history', async () => {
    const wrapper = shallowMount(HallAiChat, {
      global: { mocks: { $router: { push: vi.fn() } } },
    })
    await flushPromises()
    const assistant: any = {
      id: 'assistant-closed-stream',
      thread_id: 'thread-closed-stream',
      role: 'assistant',
      content: '',
      status: 'streaming',
      model: 'deepseek-v4-pro',
      attachments: [],
    }
    mocks.messages.mockResolvedValue({
      items: [{ ...assistant, status: 'failed', error_message: '生成连接已超时中断，可重新生成' }],
      next_cursor: null,
      has_more: false,
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      'event: message.started\ndata: {"type":"message.started","message":{"id":"assistant-closed-stream","thread_id":"thread-closed-stream"}}\n\n',
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    )))

    await (wrapper.vm as any).runStream('/fake-stream', {}, assistant)

    expect(assistant.status).toBe('failed')
    expect(assistant.error_message).toContain('可重新生成')
    expect(mocks.messages).toHaveBeenCalledWith('thread-closed-stream', { limit: 50 })
    wrapper.unmount()
    vi.unstubAllGlobals()
  })
})
