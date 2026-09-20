import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'

const AUTOWIRE_CODE = readFileSync(join(process.cwd(), 'public/project-autowire.js'), 'utf-8')

type GatewayMock = {
  ready: ReturnType<typeof vi.fn>
  input: ReturnType<typeof vi.fn>
  ingest: ReturnType<typeof vi.fn>
  capability: ReturnType<typeof vi.fn>
  ai: ReturnType<typeof vi.fn>
  capabilities: ReturnType<typeof vi.fn>
  startHeartbeat: ReturnType<typeof vi.fn>
  close: ReturnType<typeof vi.fn>
}

function tick() {
  return new Promise(resolve => setTimeout(resolve, 0))
}

async function bootAutowire(extraWindow: Record<string, unknown> = {}): Promise<GatewayMock> {
  document.body.innerHTML = '<main><h1>项目页面</h1></main>'
  document.title = 'Codex 项目'
  const gateway: GatewayMock = {
    ready: vi.fn().mockResolvedValue({ project_run_id: 'run-1' }),
    input: vi.fn().mockResolvedValue({ ok: true }),
    ingest: vi.fn().mockResolvedValue({ ok: true }),
    capability: vi.fn().mockResolvedValue({ ok: true, result: { output: '平台 AI 代理回复', model: 'cheap-proxy-model' } }),
    ai: vi.fn().mockResolvedValue({ ok: true }),
    capabilities: vi.fn(() => ['ai.cheap.chat', 'ai.generate']),
    startHeartbeat: vi.fn(() => vi.fn()),
    close: vi.fn().mockResolvedValue({ ok: true }),
  }
  Object.assign(window, {
    __SKILLFORGE_PROJECT_AUTOWIRE__: undefined,
    PlatformProjectGateway: gateway,
    SkillForgeProject: undefined,
    SFProjectGateway: undefined,
    ...extraWindow,
  })
  new Function('window', AUTOWIRE_CODE)(window)
  await tick()
  await tick()
  gateway.input.mockClear()
  gateway.ingest.mockClear()
  return gateway
}

describe('project-autowire API capture', () => {
  const originalFetch = window.fetch
  const originalXhr = window.XMLHttpRequest
  const originalBeacon = window.navigator.sendBeacon
  const originalWebSocket = window.WebSocket
  const originalEventSource = window.EventSource
  const originalWorker = window.Worker
  const originalCreateObjectUrl = (window.URL as any).createObjectURL
  const originalServiceWorkerDescriptor = Object.getOwnPropertyDescriptor(window.navigator, 'serviceWorker')
  const originalIdbObjectStore = (window as any).IDBObjectStore

  beforeEach(() => {
    vi.restoreAllMocks()
    delete (window as any).__SKILLFORGE_PROJECT_AUTOWIRE__
    delete (window as any).SkillForgeProjectBridge
  })

  afterEach(() => {
    Object.defineProperty(window, 'fetch', { value: originalFetch, writable: true, configurable: true })
    Object.defineProperty(window, 'XMLHttpRequest', { value: originalXhr, writable: true, configurable: true })
    Object.defineProperty(window, 'WebSocket', { value: originalWebSocket, writable: true, configurable: true })
    Object.defineProperty(window, 'Worker', { value: originalWorker, writable: true, configurable: true })
    if (originalCreateObjectUrl) {
      Object.defineProperty(window.URL, 'createObjectURL', { value: originalCreateObjectUrl, writable: true, configurable: true })
    } else {
      delete (window.URL as any).createObjectURL
    }
    if (originalServiceWorkerDescriptor) {
      Object.defineProperty(window.navigator, 'serviceWorker', originalServiceWorkerDescriptor)
    } else {
      delete (window.navigator as any).serviceWorker
    }
    if (originalIdbObjectStore) {
      Object.defineProperty(window, 'IDBObjectStore', { value: originalIdbObjectStore, writable: true, configurable: true })
    } else {
      delete (window as any).IDBObjectStore
    }
    if (originalEventSource) {
      Object.defineProperty(window, 'EventSource', { value: originalEventSource, writable: true, configurable: true })
    } else {
      delete (window as any).EventSource
    }
    if (originalBeacon) {
      Object.defineProperty(window.navigator, 'sendBeacon', { value: originalBeacon, writable: true, configurable: true })
    } else {
      delete (window.navigator as any).sendBeacon
    }
    delete (window as any).__SKILLFORGE_PROJECT_AUTOWIRE__
    delete (window as any).SkillForgeProjectBridge
    delete (window as any).PlatformProjectGateway
  })

  it('captures fetch request and response through the project gateway with redaction', async () => {
    const nativeFetch = vi.fn(async () => new Response(JSON.stringify({ summary: 'ok', token: 'secret-token' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }))
    const gateway = await bootAutowire({ fetch: nativeFetch })

    await window.fetch('/api/orders?token=secret', {
      method: 'POST',
      body: JSON.stringify({ api_key: 'sk-secret', nested: { token: 'secret' }, value: 42 }),
    })
    await tick()
    await tick()

    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.event === 'api_request' && payload.input?.transport === 'fetch')?.[0]
    expect(apiInput).toBeTruthy()
    expect(apiInput.input.url).toBe('/api/orders')
    expect(apiInput.input.body.api_key).toBe('[REDACTED]')
    expect(apiInput.input.body.nested.token).toBe('[REDACTED]')

    const apiOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.api_response?.transport === 'fetch')?.[0]
    expect(apiOutput).toBeTruthy()
    expect(apiOutput.auto_analyze).toBe(false)
    expect(apiOutput.output.api_response.url).toBe('/api/orders')
    expect(apiOutput.output.api_response.body.token).toBe('[REDACTED]')
  })

  it('captures XHR request and response without leaking sensitive query/body fields', async () => {
    class FakeXhr extends EventTarget {
      status = 200
      responseText = JSON.stringify({ message: 'ok', authorization: 'bearer secret' })
      open(_method: string, _url: string) {}
      send(_body?: Document | XMLHttpRequestBodyInit | null) {
        setTimeout(() => this.dispatchEvent(new Event('loadend')), 0)
      }
      getResponseHeader(name: string) {
        return name.toLowerCase() === 'content-type' ? 'application/json' : ''
      }
    }
    const gateway = await bootAutowire({ XMLHttpRequest: FakeXhr })

    const xhr = new window.XMLHttpRequest()
    xhr.open('POST', '/api/projects/runs/prun_1/ingest?authorization=secret')
    xhr.send(JSON.stringify({ password: 'secret', value: 'safe' }))
    await tick()
    await tick()

    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.transport === 'xhr')?.[0]
    expect(apiInput).toBeTruthy()
    expect(apiInput.input.url).toBe('/api/projects/runs/prun_1/ingest')
    expect(apiInput.input.body.password).toBe('[REDACTED]')

    const apiOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.api_response?.transport === 'xhr')?.[0]
    expect(apiOutput).toBeTruthy()
    expect(apiOutput.auto_analyze).toBe(false)
    expect(apiOutput.output.api_response.body.authorization).toBe('[REDACTED]')
  })

  it('proxies direct model provider fetches through platform AI gateway', async () => {
    const nativeFetch = vi.fn(async () => new Response('should not call provider', { status: 418 }))
    const gateway = await bootAutowire({ fetch: nativeFetch })

    const response = await window.fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      body: JSON.stringify({
        model: 'gpt-4o',
        messages: [
          { role: 'system', content: '你是助手' },
          { role: 'user', content: '生成一个项目报告' },
        ],
      }),
    })
    const body = await response.json()
    await tick()
    await tick()

    expect(nativeFetch).not.toHaveBeenCalled()
    expect(response.status).toBe(200)
    expect(response.headers.get('x-skillforge-ai-proxy')).toBe('project-gateway')
    expect(body.choices[0].message.content).toContain('平台 AI 代理回复')
    expect(body.skillforge.credential_location).toBe('platform_only')
    expect(gateway.capability).toHaveBeenCalledWith(
      expect.objectContaining({
        capability: 'ai.cheap.chat',
        prompt: '生成一个项目报告',
        input: expect.objectContaining({
          provider_proxy: expect.objectContaining({
            provider: 'openai',
            url: 'https://api.openai.com/v1/chat/completions',
          }),
        }),
      }),
      expect.objectContaining({ requestId: expect.stringContaining(':capability') }),
    )
    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.transport === 'fetch_ai_proxy')?.[0]
    expect(apiInput.input.body.messages[1].content).toBe('生成一个项目报告')
    const apiOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.api_response?.transport === 'fetch_ai_proxy')?.[0]
    expect(apiOutput.output.api_response.status).toBe(200)
    expect(apiOutput.output.api_response.body.skillforge.credential_location).toBe('platform_only')
  })

  it('proxies provider Request objects without consuming the original provider fetch path', async () => {
    const nativeFetch = vi.fn(async () => new Response('provider should be bypassed', { status: 418 }))
    const gateway = await bootAutowire({ fetch: nativeFetch })

    const request = new Request('https://api.openai.com/v1/responses', {
      method: 'POST',
      body: JSON.stringify({ model: 'gpt-4o-mini', input: '把上传项目生成服务调用说明' }),
      headers: { 'content-type': 'application/json' },
    })
    const response = await window.fetch(request)
    const body = await response.json()
    await tick()
    await tick()

    expect(nativeFetch).not.toHaveBeenCalled()
    expect(response.status).toBe(200)
    expect(body.output_text).toContain('平台 AI 代理回复')
    expect(gateway.capability).toHaveBeenCalledWith(
      expect.objectContaining({
        capability: 'ai.generate',
        prompt: '把上传项目生成服务调用说明',
      }),
      expect.objectContaining({ requestId: expect.stringContaining(':capability') }),
    )
  })

  it('proxies generated local AI API routes that would otherwise need a backend', async () => {
    const nativeFetch = vi.fn(async () => new Response('local backend should be bypassed', { status: 404 }))
    const gateway = await bootAutowire({ fetch: nativeFetch })

    const response = await window.fetch('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ message: '把 Codex 页面转成可运行服务', api_key: 'sk-local-secret' }),
    })
    const body = await response.json()
    await tick()
    await tick()

    expect(nativeFetch).not.toHaveBeenCalled()
    expect(response.status).toBe(200)
    expect(body.ok).toBe(true)
    expect(body.output).toContain('平台 AI 代理回复')
    expect(body.data.output).toContain('平台 AI 代理回复')
    expect(body.skillforge.generated_api_proxy).toBe(true)
    expect(gateway.capability).toHaveBeenCalledWith(
      expect.objectContaining({
        capability: 'ai.cheap.chat',
        prompt: '把 Codex 页面转成可运行服务',
        input: expect.objectContaining({
          provider_proxy: expect.objectContaining({
            provider: 'generated_api',
            url: '/api/chat',
          }),
        }),
      }),
      expect.objectContaining({ requestId: expect.stringContaining(':capability') }),
    )
    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.transport === 'fetch_ai_proxy')?.[0]
    expect(apiInput.input.body.api_key).toBe('[REDACTED]')
  })

  it('fallbacks missing generated business APIs to runnable Project Gateway data', async () => {
    const nativeFetch = vi.fn(async () => new Response(JSON.stringify({ error: 'not found' }), {
      status: 404,
      headers: { 'content-type': 'application/json' },
    }))
    const gateway = await bootAutowire({ fetch: nativeFetch })

    const response = await window.fetch('/api/products?token=secret', { method: 'GET' })
    const body = await response.json()
    await tick()
    await tick()

    expect(nativeFetch).toHaveBeenCalledOnce()
    expect(response.status).toBe(200)
    expect(response.headers.get('x-skillforge-backend-fallback')).toBe('project-gateway')
    expect(body.ok).toBe(true)
    expect(body.items).toHaveLength(1)
    expect(body.data.items[0].status).toBe('ready')
    expect(body.skillforge.generated_backend_fallback).toBe(true)
    expect(body.skillforge.native_status).toBe(404)

    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.transport === 'fetch')?.[0]
    expect(apiInput.input.url).toBe('/api/products')
    const apiOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.api_response?.transport === 'fetch_backend_fallback')?.[0]
    expect(apiOutput.output.api_response.status).toBe(200)
    expect(apiOutput.output.api_response.body.skillforge.generated_backend_fallback).toBe(true)
  })

  it('does not hide auth failures from real platform APIs during backend fallback', async () => {
    const nativeFetch = vi.fn(async () => new Response(JSON.stringify({ error: 'unauthorized' }), {
      status: 401,
      headers: { 'content-type': 'application/json' },
    }))
    await bootAutowire({ fetch: nativeFetch })

    const response = await window.fetch('/api/products', { method: 'GET' })
    const body = await response.json()
    await tick()
    await tick()

    expect(nativeFetch).toHaveBeenCalledOnce()
    expect(response.status).toBe(401)
    expect(body.error).toBe('unauthorized')
  })

  it('returns SSE-shaped chunks when a provider request asks for streaming', async () => {
    const nativeFetch = vi.fn(async () => new Response('provider should be bypassed', { status: 418 }))
    await bootAutowire({ fetch: nativeFetch })

    const response = await window.fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      body: JSON.stringify({
        stream: true,
        messages: [{ role: 'user', content: '流式生成项目执行结论' }],
      }),
    })
    const streamText = await response.text()
    await tick()
    await tick()

    expect(nativeFetch).not.toHaveBeenCalled()
    expect(response.status).toBe(200)
    expect(response.headers.get('content-type')).toContain('text/event-stream')
    expect(streamText).toContain('data:')
    expect(streamText).toContain('平台 AI 代理回复')
    expect(streamText).toContain('data: [DONE]')
  })

  it('proxies axios-style XHR model provider calls through the project gateway', async () => {
    const nativeSend = vi.fn()
    class ProviderXhr extends EventTarget {
      readyState = 0
      status = 0
      responseText = ''
      response: unknown = ''
      responseType = ''
      onloadend: ((event: Event) => void) | null = null
      open(_method: string, _url: string) {}
      setRequestHeader(_name: string, _value: string) {}
      send(body?: Document | XMLHttpRequestBodyInit | null) {
        nativeSend(body)
      }
      getResponseHeader(_name: string) {
        return ''
      }
      getAllResponseHeaders() {
        return ''
      }
    }
    const gateway = await bootAutowire({ XMLHttpRequest: ProviderXhr })

    const xhr = new window.XMLHttpRequest()
    const done = new Promise<void>((resolve) => xhr.addEventListener('loadend', () => resolve()))
    xhr.open('POST', 'https://api.openai.com/v1/chat/completions')
    xhr.setRequestHeader('content-type', 'application/json')
    xhr.send(JSON.stringify({
      messages: [{ role: 'user', content: 'Axios 生成项目周报' }],
    }))
    await done
    await tick()
    await tick()

    expect(nativeSend).not.toHaveBeenCalled()
    expect(xhr.status).toBe(200)
    expect(xhr.getResponseHeader('x-skillforge-ai-proxy')).toBe('project-gateway')
    expect(JSON.parse(xhr.responseText).choices[0].message.content).toContain('平台 AI 代理回复')
    expect(gateway.capability).toHaveBeenCalledWith(
      expect.objectContaining({
        capability: 'ai.cheap.chat',
        prompt: 'Axios 生成项目周报',
      }),
      expect.objectContaining({ requestId: expect.stringContaining(':capability') }),
    )
    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.transport === 'xhr_ai_proxy')?.[0]
    expect(apiInput.input.body.messages[0].content).toBe('Axios 生成项目周报')
  })

  it('fallbacks axios-style XHR business APIs without requiring a generated backend', async () => {
    const nativeSend = vi.fn()
    class BackendXhr extends EventTarget {
      readyState = 0
      status = 0
      responseText = ''
      response: unknown = ''
      responseType = ''
      open(_method: string, _url: string) {}
      setRequestHeader(_name: string, _value: string) {}
      send(body?: Document | XMLHttpRequestBodyInit | null) {
        nativeSend(body)
      }
      getResponseHeader(_name: string) {
        return ''
      }
      getAllResponseHeaders() {
        return ''
      }
    }
    const gateway = await bootAutowire({ XMLHttpRequest: BackendXhr })

    const xhr = new window.XMLHttpRequest()
    const done = new Promise<void>((resolve) => xhr.addEventListener('loadend', () => resolve()))
    xhr.open('GET', '/api/orders?token=secret')
    xhr.send()
    await done
    await tick()
    await tick()

    expect(nativeSend).not.toHaveBeenCalled()
    expect(xhr.status).toBe(200)
    expect(xhr.getResponseHeader('x-skillforge-backend-fallback')).toBe('project-gateway')
    const body = JSON.parse(xhr.responseText)
    expect(body.ok).toBe(true)
    expect(body.items[0].status).toBe('ready')
    expect(body.skillforge.generated_backend_fallback).toBe(true)

    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.transport === 'xhr_backend_fallback')?.[0]
    expect(apiInput.input.url).toBe('/api/orders')
    const apiOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.api_response?.transport === 'xhr_backend_fallback')?.[0]
    expect(apiOutput.output.api_response.body.skillforge.generated_backend_fallback).toBe(true)
  })

  it('does not fallback known platform XHR APIs', async () => {
    const nativeSend = vi.fn()
    class PlatformXhr extends EventTarget {
      open(_method: string, _url: string) {}
      send(body?: Document | XMLHttpRequestBodyInit | null) {
        nativeSend(body)
      }
    }
    await bootAutowire({ XMLHttpRequest: PlatformXhr })

    const xhr = new window.XMLHttpRequest()
    xhr.open('POST', '/api/projects/runs/prun_1/ingest')
    xhr.send(JSON.stringify({ output: { summary: '平台真实接口' } }))

    expect(nativeSend).toHaveBeenCalledOnce()
  })

  it('captures sendBeacon API traffic and synthetic queue result', async () => {
    const nativeBeacon = vi.fn(() => true)
    Object.defineProperty(window.navigator, 'sendBeacon', { value: nativeBeacon, writable: true, configurable: true })
    const gateway = await bootAutowire()

    const ok = window.navigator.sendBeacon('/api/projects/runs/prun_1/ingest?token=secret', JSON.stringify({
      password: 'secret',
      value: 'queued',
    }))
    await tick()
    await tick()

    expect(ok).toBe(true)
    expect(nativeBeacon).toHaveBeenCalledOnce()
    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.transport === 'beacon')?.[0]
    expect(apiInput.input.url).toBe('/api/projects/runs/prun_1/ingest')
    expect(apiInput.input.body.password).toBe('[REDACTED]')
    const apiOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.api_response?.transport === 'beacon')?.[0]
    expect(apiOutput.auto_analyze).toBe(false)
    expect(apiOutput.output.api_response.status).toBe(204)
    expect(apiOutput.output.api_response.body).toBe('queued')
  })

  it('fallbacks generated sendBeacon business APIs without requiring a backend', async () => {
    const nativeBeacon = vi.fn(() => false)
    Object.defineProperty(window.navigator, 'sendBeacon', { value: nativeBeacon, writable: true, configurable: true })
    const gateway = await bootAutowire()

    const ok = window.navigator.sendBeacon('/api/analytics/events?token=secret', JSON.stringify({
      event: 'codex_project_output',
      secret: 'should-hide',
    }))
    await tick()
    await tick()

    expect(ok).toBe(true)
    expect(nativeBeacon).not.toHaveBeenCalled()
    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.transport === 'beacon_backend_fallback')?.[0]
    expect(apiInput.input.url).toBe('/api/analytics/events')
    expect(apiInput.input.body.secret).toBe('[REDACTED]')
    const apiOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.api_response?.transport === 'beacon_backend_fallback')?.[0]
    expect(apiOutput.output.api_response.status).toBe(202)
    expect(apiOutput.output.api_response.body.skillforge.generated_backend_fallback).toBe(true)
  })

  it('proxies sendBeacon model provider calls through platform AI without leaking to the browser network', async () => {
    const nativeBeacon = vi.fn(() => true)
    Object.defineProperty(window.navigator, 'sendBeacon', { value: nativeBeacon, writable: true, configurable: true })
    const gateway = await bootAutowire()

    const ok = window.navigator.sendBeacon('https://api.openai.com/v1/chat/completions', JSON.stringify({
      messages: [{ role: 'user', content: 'Beacon 生成运营日报' }],
    }))
    await tick()
    await tick()

    expect(ok).toBe(true)
    expect(nativeBeacon).not.toHaveBeenCalled()
    expect(gateway.capability).toHaveBeenCalledWith(
      expect.objectContaining({
        capability: 'ai.cheap.chat',
        prompt: 'Beacon 生成运营日报',
      }),
      expect.objectContaining({ requestId: expect.stringContaining(':capability') }),
    )
    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.transport === 'beacon_ai_proxy')?.[0]
    expect(apiInput.input.body.messages[0].content).toBe('Beacon 生成运营日报')
    const apiOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.api_response?.transport === 'beacon_ai_proxy')?.[0]
    expect(apiOutput.output.api_response.body.skillforge.credential_location).toBe('platform_only')
  })

  it('fallbacks generated local WebSocket backends and records messages through the gateway', async () => {
    const nativeConstruct = vi.fn()
    class NativeWs extends EventTarget {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3
      readyState = 0
      constructor(url: string) {
        super()
        nativeConstruct(url)
      }
      send(_data: string) {}
      close() {}
    }
    Object.defineProperty(window, 'WebSocket', { value: NativeWs, writable: true, configurable: true })
    const gateway = await bootAutowire()

    const ws = new window.WebSocket('/ws/chat?token=secret')
    await new Promise<void>((resolve) => ws.addEventListener('open', () => resolve()))
    const message = new Promise<MessageEvent>((resolve) => ws.addEventListener('message', (event) => resolve(event as MessageEvent)))
    ws.send(JSON.stringify({ prompt: 'WebSocket 生成部门看板', secret: 'hide-me' }))
    const event = await message
    await tick()
    await tick()

    expect(nativeConstruct).not.toHaveBeenCalled()
    expect(ws.readyState).toBe(1)
    const body = JSON.parse(String(event.data))
    expect(body.ok).toBe(true)
    expect(body.skillforge.generated_backend_fallback).toBe(true)
    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.transport === 'websocket_backend_fallback' && payload.input?.event === 'api_request')?.[0]
    expect(apiInput.input.url).toBe('/ws/chat')
    expect(apiInput.input.body.secret).toBe('[REDACTED]')
    const apiOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.api_response?.transport === 'websocket_backend_fallback')?.[0]
    expect(apiOutput.output.api_response.body.skillforge.generated_backend_fallback).toBe(true)
  })

  it('proxies WebSocket model provider traffic through platform AI', async () => {
    const nativeConstruct = vi.fn()
    class NativeWs extends EventTarget {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3
      readyState = 0
      constructor(url: string) {
        super()
        nativeConstruct(url)
      }
      send(_data: string) {}
      close() {}
    }
    Object.defineProperty(window, 'WebSocket', { value: NativeWs, writable: true, configurable: true })
    const gateway = await bootAutowire()

    const ws = new window.WebSocket('wss://api.openai.com/v1/realtime?model=gpt-realtime')
    await new Promise<void>((resolve) => ws.addEventListener('open', () => resolve()))
    const message = new Promise<MessageEvent>((resolve) => ws.addEventListener('message', (event) => resolve(event as MessageEvent)))
    ws.send(JSON.stringify({ messages: [{ role: 'user', content: 'Realtime 生成项目复盘' }] }))
    const event = await message
    await tick()
    await tick()

    expect(nativeConstruct).not.toHaveBeenCalled()
    const body = JSON.parse(String(event.data))
    expect(body.choices[0].message.content).toContain('平台 AI 代理回复')
    expect(gateway.capability).toHaveBeenCalledWith(
      expect.objectContaining({
        capability: 'ai.cheap.chat',
        prompt: 'Realtime 生成项目复盘',
      }),
      expect.objectContaining({ requestId: expect.stringContaining(':capability') }),
    )
    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.transport === 'websocket_ai_proxy')?.[0]
    expect(apiInput.input.url).toBe('wss://api.openai.com/v1/realtime')
    const apiOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.api_response?.transport === 'websocket_ai_proxy')?.[0]
    expect(apiOutput.output.api_response.body.skillforge.credential_location).toBe('platform_only')
  })

  it('keeps unrelated external WebSocket connections on the native path', async () => {
    const nativeConstruct = vi.fn()
    const nativeSend = vi.fn()
    class NativeWs extends EventTarget {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3
      readyState = 1
      constructor(url: string) {
        super()
        nativeConstruct(url)
      }
      send(data: string) {
        nativeSend(data)
      }
      close() {}
    }
    Object.defineProperty(window, 'WebSocket', { value: NativeWs, writable: true, configurable: true })
    await bootAutowire()

    const ws = new window.WebSocket('wss://example.com/live')
    ws.send('native')

    expect(nativeConstruct).toHaveBeenCalledWith('wss://example.com/live')
    expect(nativeSend).toHaveBeenCalledWith('native')
  })

  it('fallbacks generated EventSource backends and records stream output through the gateway', async () => {
    const nativeConstruct = vi.fn()
    class NativeEs extends EventTarget {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSED = 2
      readyState = 0
      url = ''
      constructor(url: string) {
        super()
        this.url = url
        nativeConstruct(url)
      }
      close() {}
    }
    Object.defineProperty(window, 'EventSource', { value: NativeEs, writable: true, configurable: true })
    const gateway = await bootAutowire()

    const es = new window.EventSource('/api/events?token=secret')
    await new Promise<void>((resolve) => es.addEventListener('open', () => resolve()))
    const event = await new Promise<MessageEvent>((resolve) => es.addEventListener('message', (message) => resolve(message as MessageEvent)))
    await tick()
    await tick()

    expect(nativeConstruct).not.toHaveBeenCalled()
    expect(es.readyState).toBe(1)
    const body = JSON.parse(String(event.data))
    expect(body.ok).toBe(true)
    expect(body.skillforge.generated_backend_fallback).toBe(true)
    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.transport === 'eventsource_backend_fallback')?.[0]
    expect(apiInput.input.url).toBe('/api/events')
    const apiOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.api_response?.transport === 'eventsource_backend_fallback')?.[0]
    expect(apiOutput.output.api_response.body.skillforge.generated_backend_fallback).toBe(true)
  })

  it('proxies generated local EventSource AI streams through platform AI', async () => {
    const nativeConstruct = vi.fn()
    class NativeEs extends EventTarget {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSED = 2
      readyState = 0
      url = ''
      constructor(url: string) {
        super()
        this.url = url
        nativeConstruct(url)
      }
      close() {}
    }
    Object.defineProperty(window, 'EventSource', { value: NativeEs, writable: true, configurable: true })
    const gateway = await bootAutowire()

    const es = new window.EventSource('/api/chat/stream?prompt=EventSource%20%E7%94%9F%E6%88%90%E9%A1%B9%E7%9B%AE%E5%A4%8D%E7%9B%98')
    await new Promise<void>((resolve) => es.addEventListener('open', () => resolve()))
    const event = await new Promise<MessageEvent>((resolve) => es.addEventListener('message', (message) => resolve(message as MessageEvent)))
    await tick()
    await tick()

    expect(nativeConstruct).not.toHaveBeenCalled()
    const body = JSON.parse(String(event.data))
    expect(body.output).toContain('平台 AI 代理回复')
    expect(body.skillforge.generated_api_proxy).toBe(true)
    expect(gateway.capability).toHaveBeenCalledWith(
      expect.objectContaining({
        capability: 'ai.cheap.chat',
        prompt: 'EventSource 生成项目复盘',
      }),
      expect.objectContaining({ requestId: expect.stringContaining(':capability') }),
    )
    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.transport === 'eventsource_ai_proxy')?.[0]
    expect(apiInput.input.body.prompt).toBe('EventSource 生成项目复盘')
    const apiOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.api_response?.transport === 'eventsource_ai_proxy')?.[0]
    expect(apiOutput.output.api_response.status).toBe(200)
    expect(String(apiOutput.output.api_response.body)).toContain('平台 AI 代理回复')
  })

  it('keeps unrelated external EventSource connections on the native path', async () => {
    const nativeConstruct = vi.fn()
    class NativeEs extends EventTarget {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSED = 2
      constructor(url: string) {
        super()
        nativeConstruct(url)
      }
      close() {}
    }
    Object.defineProperty(window, 'EventSource', { value: NativeEs, writable: true, configurable: true })
    await bootAutowire()

    new window.EventSource('https://example.com/events')

    expect(nativeConstruct).toHaveBeenCalledWith('https://example.com/events')
  })

  it('proxies generated worker backend fetches through Project Gateway fallback', async () => {
    const nativeConstruct = vi.fn()
    const createObjectUrl = vi.fn(() => 'blob:skillforge-worker-wrapper')
    class NativeWorker extends EventTarget {
      postMessage = vi.fn()
      terminate = vi.fn()
      constructor(url: string, options?: WorkerOptions) {
        super()
        nativeConstruct(url, options)
      }
    }
    Object.defineProperty(window.URL, 'createObjectURL', { value: createObjectUrl, writable: true, configurable: true })
    Object.defineProperty(window, 'Worker', { value: NativeWorker, writable: true, configurable: true })
    const gateway = await bootAutowire()

    const worker = new window.Worker('/assets/generated-worker.js') as unknown as NativeWorker
    worker.dispatchEvent(new MessageEvent('message', {
      data: {
        __skillforge_worker_proxy: true,
        request_id: 'worker-backend-1',
        url: '/api/orders?token=secret',
        method: 'GET',
        body: JSON.stringify({ secret: 'hide-me' }),
      },
    }))
    await tick()
    await tick()

    expect(createObjectUrl).toHaveBeenCalledOnce()
    const wrapperBlob = (createObjectUrl.mock.calls as unknown as [[Blob]])[0][0]
    const wrapperText = await wrapperBlob.text()
    expect(wrapperText).toContain('self.fetch=function')
    expect(() => new Function(wrapperText)).not.toThrow()
    expect(nativeConstruct.mock.calls[0][0]).toBe('blob:skillforge-worker-wrapper')
    const response = worker.postMessage.mock.calls.find(([payload]) => payload.__skillforge_worker_proxy_result)?.[0]
    expect(response.status).toBe(200)
    expect(response.headers['x-skillforge-backend-fallback']).toBe('project-gateway')
    expect(JSON.parse(response.body).skillforge.generated_backend_fallback).toBe(true)
    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.transport === 'worker_fetch_backend_fallback')?.[0]
    expect(apiInput.input.url).toBe('/api/orders')
    expect(apiInput.input.body.secret).toBe('[REDACTED]')
    const apiOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.api_response?.transport === 'worker_fetch_backend_fallback')?.[0]
    expect(apiOutput.output.api_response.body.skillforge.generated_backend_fallback).toBe(true)
  })

  it('proxies generated worker AI fetches through platform AI', async () => {
    const nativeConstruct = vi.fn()
    class NativeWorker extends EventTarget {
      postMessage = vi.fn()
      terminate = vi.fn()
      constructor(url: string, options?: WorkerOptions) {
        super()
        nativeConstruct(url, options)
      }
    }
    Object.defineProperty(window.URL, 'createObjectURL', { value: vi.fn(() => 'blob:skillforge-worker-ai-wrapper'), writable: true, configurable: true })
    Object.defineProperty(window, 'Worker', { value: NativeWorker, writable: true, configurable: true })
    const gateway = await bootAutowire()

    const worker = new window.Worker('/assets/ai-worker.js') as unknown as NativeWorker
    worker.dispatchEvent(new MessageEvent('message', {
      data: {
        __skillforge_worker_proxy: true,
        request_id: 'worker-ai-1',
        url: 'https://api.openai.com/v1/chat/completions',
        method: 'POST',
        body: JSON.stringify({ messages: [{ role: 'user', content: 'Worker 生成项目报告' }] }),
      },
    }))
    await tick()
    await tick()
    await tick()

    expect(nativeConstruct.mock.calls[0][0]).toBe('blob:skillforge-worker-ai-wrapper')
    expect(gateway.capability).toHaveBeenCalledWith(
      expect.objectContaining({
        capability: 'ai.cheap.chat',
        prompt: 'Worker 生成项目报告',
      }),
      expect.objectContaining({ requestId: 'worker-ai-1:capability' }),
    )
    const response = worker.postMessage.mock.calls.find(([payload]) => payload.__skillforge_worker_proxy_result)?.[0]
    expect(response.status).toBe(200)
    expect(response.headers['x-skillforge-ai-proxy']).toBe('project-gateway')
    expect(JSON.parse(response.body).skillforge.credential_location).toBe('platform_only')
    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.transport === 'worker_fetch_ai_proxy')?.[0]
    expect(apiInput.input.body.messages[0].content).toBe('Worker 生成项目报告')
  })

  it('keeps unrelated external workers on the native path', async () => {
    const nativeConstruct = vi.fn()
    const createObjectUrl = vi.fn(() => 'blob:should-not-use')
    class NativeWorker extends EventTarget {
      postMessage = vi.fn()
      terminate = vi.fn()
      constructor(url: string, options?: WorkerOptions) {
        super()
        nativeConstruct(url, options)
      }
    }
    Object.defineProperty(window.URL, 'createObjectURL', { value: createObjectUrl, writable: true, configurable: true })
    Object.defineProperty(window, 'Worker', { value: NativeWorker, writable: true, configurable: true })
    await bootAutowire()

    new window.Worker('https://example.com/external-worker.js')

    expect(createObjectUrl).not.toHaveBeenCalled()
    expect(nativeConstruct).toHaveBeenCalledWith('https://example.com/external-worker.js', undefined)
  })

  it('safely captures generated service worker registration instead of letting it control the platform host', async () => {
    const nativeRegister = vi.fn(async () => ({ scope: 'native' }))
    const container = {
      register: nativeRegister,
      getRegistration: vi.fn(async () => undefined),
      getRegistrations: vi.fn(async () => []),
    }
    Object.defineProperty(window.navigator, 'serviceWorker', { value: container, writable: true, configurable: true })
    const gateway = await bootAutowire()

    const registration = await window.navigator.serviceWorker.register('/service-worker.js?token=secret', {
      scope: '/project-app/',
      type: 'module',
    } as RegistrationOptions)
    await tick()
    await tick()

    expect(nativeRegister).not.toHaveBeenCalled()
    expect(registration.scope).toContain('/project-app/')
    await expect(registration.unregister()).resolves.toBe(true)
    await expect(registration.update()).resolves.toBe(registration)
    await expect(window.navigator.serviceWorker.getRegistration('/project-app/')).resolves.toBe(registration)
    await expect(window.navigator.serviceWorker.getRegistrations()).resolves.toEqual([registration])
    const apiInput = gateway.input.mock.calls.find(([payload]) => payload.input?.transport === 'serviceworker_fallback')?.[0]
    expect(apiInput.input.url).toBe('/service-worker.js')
    const apiOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.api_response?.transport === 'serviceworker_fallback')?.[0]
    expect(apiOutput.output.api_response.body.skillforge.generated_service_worker_fallback).toBe(true)
  })

  it('keeps unrelated external service worker registrations on the native path', async () => {
    const nativeRegister = vi.fn(async () => ({ scope: 'native' }))
    Object.defineProperty(window.navigator, 'serviceWorker', {
      value: {
        register: nativeRegister,
        getRegistration: vi.fn(),
        getRegistrations: vi.fn(),
      },
      writable: true,
      configurable: true,
    })
    await bootAutowire()

    await window.navigator.serviceWorker.register('https://example.com/service-worker.js')

    expect(nativeRegister).toHaveBeenCalledWith('https://example.com/service-worker.js', undefined)
  })

  it('captures generated app localStorage outputs as platform trace events', async () => {
    localStorage.clear()
    const gateway = await bootAutowire()

    localStorage.setItem('codex_report_output', JSON.stringify({
      summary: '本地状态里的报告输出',
      token: 'secret-token',
    }))
    await tick()
    await tick()

    const storageInput = gateway.input.mock.calls.find(([payload]) => payload.input?.event === 'storage_mutation')?.[0]
    expect(storageInput.input.storage).toBe('localStorage')
    expect(storageInput.input.operation).toBe('setItem')
    expect(storageInput.input.key).toBe('codex_report_output')
    expect(storageInput.input.value.token).toBe('[REDACTED]')
    const storageOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.event === 'storage_output')?.[0]
    expect(storageOutput.auto_analyze).toBe(false)
    expect(storageOutput.output.storage.value.summary).toBe('本地状态里的报告输出')
  })

  it('captures generated app IndexedDB writes as platform trace outputs', async () => {
    const nativePut = vi.fn((_value: unknown, _key?: IDBValidKey) => ({ readyState: 'done' }))
    class MockIDBObjectStore {
      name: string
      constructor(name: string) {
        this.name = name
      }
      put(value: unknown, key?: IDBValidKey) {
        return nativePut(value, key)
      }
      add(value: unknown, key?: IDBValidKey) {
        return nativePut(value, key)
      }
    }
    Object.defineProperty(window, 'IDBObjectStore', { value: MockIDBObjectStore, writable: true, configurable: true })
    const gateway = await bootAutowire()

    const store = new (window as any).IDBObjectStore('codex_reports')
    store.put({ summary: 'IndexedDB 里的报告输出', api_key: 'sk-secret' }, 'report-1')
    await tick()
    await tick()

    expect(nativePut).toHaveBeenCalledWith(expect.objectContaining({ summary: 'IndexedDB 里的报告输出' }), 'report-1')
    const idbInput = gateway.input.mock.calls.find(([payload]) => payload.input?.event === 'indexeddb_mutation')?.[0]
    expect(idbInput.input.storage).toBe('IndexedDB')
    expect(idbInput.input.operation).toBe('put')
    expect(idbInput.input.store).toBe('codex_reports')
    expect(idbInput.input.value.api_key).toBe('[REDACTED]')
    const idbOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.event === 'indexeddb_output')?.[0]
    expect(idbOutput.auto_analyze).toBe(false)
    expect(idbOutput.output.indexeddb.value.summary).toBe('IndexedDB 里的报告输出')
  })

  it('captures generated app runtime errors for platform improvement loop', async () => {
    const gateway = await bootAutowire()

    window.dispatchEvent(new ErrorEvent('error', {
      message: 'Codex 生成页面组件崩溃',
      error: new Error('Codex 生成页面组件崩溃'),
    }))
    await tick()
    await tick()

    const runtimeOutput = gateway.ingest.mock.calls.find(([payload]) => payload.output?.event === 'runtime_error')?.[0]
    expect(runtimeOutput).toBeTruthy()
    expect(runtimeOutput.auto_analyze).toBe(false)
    expect(runtimeOutput.output.runtime_error.message).toContain('Codex 生成页面组件崩溃')
    expect(runtimeOutput.metadata.event_type).toBe('runtime_error')
  })
})
