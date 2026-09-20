import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it, vi } from 'vitest'

describe('public Project Gateway SDK', () => {
  it('requests runtime context and resolves gateway calls by request_id', async () => {
    vi.useFakeTimers()
    try {
      const posted: Array<Record<string, any>> = []
      ;(window as any).__PROJECT_GATEWAY_PARENT__ = {
        postMessage(message: Record<string, any>) {
          posted.push(message)
        },
      }

      const sdkPath = resolve(process.cwd(), 'public/project-gateway-sdk.js')
      const code = readFileSync(sdkPath, 'utf8')
      ;(0, eval)(code)
      const sdk = (window as any).PlatformProjectGateway

      expect(sdk.gatewayAvailable()).toBe(true)
      vi.runOnlyPendingTimers()
      expect(posted.at(-1)).toMatchObject({ type: 'skillforge.project.ready' })

      const readyPromise = sdk.ready({ timeoutMs: 1000 })
      expect(posted.at(-1)).toMatchObject({ type: 'skillforge.project.context.request' })
      window.dispatchEvent(new MessageEvent('message', {
        data: {
          type: 'skillforge.project.context',
          gateway_token: 'gw-token-1',
          project: { id: 'project-a', capabilities: ['ai.generate'] },
          run: { id: 'run-a' },
          gateway: {
            sdk_url: 'https://platform.example/project-gateway-sdk.js',
            gateway_limits: { max_ingest_bytes: 1024 },
            session_required: true,
            session_token: 'gw-token-1',
          },
        },
      }))
      await expect(readyPromise).resolves.toMatchObject({ run: { id: 'run-a' } })
      expect(sdk.context.project.id).toBe('project-a')
      expect(sdk.gatewayInfo().sdk_url).toBe('https://platform.example/project-gateway-sdk.js')
      expect(sdk.capabilities()).toEqual(['ai.generate'])
      expect(sdk.limits()).toEqual({ max_ingest_bytes: 1024 })
      expect(sdk.gatewayTokenAvailable()).toBe(true)

      const inputPromise = sdk.input({ input: { keyword: 'today' } }, { requestId: 'inp-1', timeoutMs: 1000 })
      expect(posted.at(-1)).toMatchObject({
        type: 'skillforge.project.input',
        request_id: 'inp-1',
        payload: { input: { keyword: 'today' } },
        gateway_token: 'gw-token-1',
      })
      window.dispatchEvent(new MessageEvent('message', {
        data: {
          type: 'skillforge.project.input.result',
          request_id: 'inp-1',
          ok: true,
          result: { status: 'running', input_snapshot: { keyword: 'today' } },
        },
      }))
      await expect(inputPromise).resolves.toEqual({ status: 'running', input_snapshot: { keyword: 'today' } })
      expect(sdk.recordInput).toBe(sdk.input)

      const capabilityPromise = sdk.capability(
        { capability: 'ai.generate', prompt: 'hello' },
        { requestId: 'cap-1', timeoutMs: 1000 },
      )
      expect(posted.at(-1)).toMatchObject({
        type: 'skillforge.project.capability',
        request_id: 'cap-1',
        payload: { capability: 'ai.generate', prompt: 'hello' },
        gateway_token: 'gw-token-1',
      })
      window.dispatchEvent(new MessageEvent('message', {
        data: {
          type: 'skillforge.project.capability.result',
          request_id: 'cap-1',
          ok: true,
          result: { text: 'ok' },
        },
      }))
      await expect(capabilityPromise).resolves.toEqual({ text: 'ok' })

      const uploadFile = new File(['video_name,score\nA,90\n'], 'spend.csv', { type: 'text/csv' })
      const onUploadProgress = vi.fn()
      const uploadPromise = sdk.assets.upload(
        { file: uploadFile, metadata: { role: 'data' } },
        { requestId: 'asset-upload-1', timeoutMs: 1000, onProgress: onUploadProgress },
      )
      expect(posted.at(-1)).toMatchObject({
        type: 'skillforge.project.assets.upload',
        request_id: 'asset-upload-1',
        gateway_token: 'gw-token-1',
        payload: {
          file: uploadFile,
          file_name: 'spend.csv',
          mime_type: 'text/csv',
          metadata: { role: 'data' },
        },
      })
      window.dispatchEvent(new MessageEvent('message', {
        data: {
          type: 'skillforge.project.assets.upload.progress',
          request_id: 'asset-upload-1',
          progress: { loaded_bytes: 10, total_bytes: 20, percent: 50 },
        },
      }))
      expect(onUploadProgress).toHaveBeenCalledWith({ loaded_bytes: 10, total_bytes: 20, percent: 50 })
      window.dispatchEvent(new MessageEvent('message', {
        data: {
          type: 'skillforge.project.assets.upload.result',
          request_id: 'asset-upload-1',
          ok: true,
          result: { ok: true, asset: { id: 'asset-1', file_name: 'spend.csv' } },
        },
      }))
      await expect(uploadPromise).resolves.toEqual({ ok: true, asset: { id: 'asset-1', file_name: 'spend.csv' } })

      const listPromise = sdk.assets.list({}, { requestId: 'asset-list-1', timeoutMs: 1000 })
      expect(posted.at(-1)).toMatchObject({
        type: 'skillforge.project.assets.list',
        request_id: 'asset-list-1',
        gateway_token: 'gw-token-1',
      })
      window.dispatchEvent(new MessageEvent('message', {
        data: {
          type: 'skillforge.project.assets.list.result',
          request_id: 'asset-list-1',
          ok: true,
          result: { total: 1, items: [{ id: 'asset-1' }] },
        },
      }))
      await expect(listPromise).resolves.toEqual({ total: 1, items: [{ id: 'asset-1' }] })

      const mediaEvents: Array<Record<string, any>> = []
      const subscribePromise = sdk.media.subscribe(
        (event: string, payload: Record<string, any>) => mediaEvents.push({ event, payload }),
        { requestId: 'media-subscribe-1', afterCursor: 'cursor-0', timeoutMs: 1000 },
      )
      expect(posted.at(-1)).toMatchObject({
        type: 'skillforge.project.media.subscribe',
        request_id: 'media-subscribe-1',
        payload: { after_cursor: 'cursor-0' },
        gateway_token: 'gw-token-1',
      })
      window.dispatchEvent(new MessageEvent('message', {
        data: {
          type: 'skillforge.project.media.event',
          subscription_id: 'media-sub-1',
          event: 'snapshot',
          data: { cursor: 'cursor-1', jobs: [{ id: 'job-1' }] },
        },
      }))
      window.dispatchEvent(new MessageEvent('message', {
        data: {
          type: 'skillforge.project.media.subscribe.result',
          request_id: 'media-subscribe-1',
          ok: true,
          result: { subscription_id: 'media-sub-1', capability_quota_counted: false },
        },
      }))
      const unsubscribe = await subscribePromise
      expect(mediaEvents).toEqual([{ event: 'snapshot', payload: { cursor: 'cursor-1', jobs: [{ id: 'job-1' }] } }])
      const unsubscribePromise = unsubscribe()
      expect(posted.at(-1)).toMatchObject({
        type: 'skillforge.project.media.unsubscribe',
        payload: { subscription_id: 'media-sub-1' },
      })
      window.dispatchEvent(new MessageEvent('message', {
        data: {
          type: 'skillforge.project.media.unsubscribe.result',
          request_id: posted.at(-1)?.request_id,
          ok: true,
          result: { closed: true },
        },
      }))
      await unsubscribePromise

      const ingestPromise = sdk.ingest({ output: { summary: 'done' } }, { requestId: 'ing-1', timeoutMs: 1000 })
      expect(posted.at(-1)).toMatchObject({ type: 'skillforge.project.ingest', request_id: 'ing-1', gateway_token: 'gw-token-1' })
      window.dispatchEvent(new MessageEvent('message', {
        data: {
          type: 'skillforge.project.ingest.result',
          request_id: 'ing-1',
          ok: true,
          result: { status: 'completed' },
        },
      }))
      await expect(ingestPromise).resolves.toEqual({ status: 'completed' })

      const stopHeartbeat = sdk.startHeartbeat({ intervalMs: 5000, payload: { visible: true }, timeoutMs: 1000 })
      expect(posted.at(-1)).toMatchObject({ type: 'skillforge.project.heartbeat', payload: { visible: true }, gateway_token: 'gw-token-1' })
      stopHeartbeat()

      const closePromise = sdk.close({ reason: 'done' }, { requestId: 'close-1', timeoutMs: 1000 })
      expect(posted.at(-1)).toMatchObject({
        type: 'skillforge.project.close',
        request_id: 'close-1',
        payload: { reason: 'done' },
        gateway_token: 'gw-token-1',
      })
      window.dispatchEvent(new MessageEvent('message', {
        data: {
          type: 'skillforge.project.close.result',
          request_id: 'close-1',
          ok: true,
          result: { status: 'completed', closed: true },
        },
      }))
      await expect(closePromise).resolves.toEqual({ status: 'completed', closed: true })
      expect(sdk.end).toBe(sdk.close)
    } finally {
      delete (window as any).__PROJECT_GATEWAY_PARENT__
      delete (window as any).PlatformProjectGateway
      delete (window as any).SkillForgeProject
      delete (window as any).SFProjectGateway
      vi.useRealTimers()
    }
  })

  it('bridges direct project asset fetches through postMessage gateway', async () => {
    vi.useFakeTimers()
    try {
      const nativeFetch = vi.fn(async () => new Response('native should not be called', { status: 418 }))
      Object.defineProperty(window, 'fetch', { value: nativeFetch, writable: true, configurable: true })
      const posted: Array<Record<string, any>> = []
      ;(window as any).__PROJECT_GATEWAY_PARENT__ = {
        postMessage(message: Record<string, any>) {
          posted.push(message)
        },
      }

      const sdkCode = readFileSync(resolve(process.cwd(), 'public/project-gateway-sdk.js'), 'utf8')
      ;(0, eval)(sdkCode)
      vi.runOnlyPendingTimers()
      window.dispatchEvent(new MessageEvent('message', {
        data: {
          type: 'skillforge.project.context',
          gateway_token: 'gw-token-fetch',
          project: { id: 'project-a', capabilities: ['ai.analyze'] },
          run: { id: 'run-a' },
          gateway: { session_token: 'gw-token-fetch' },
        },
      }))

      const form = new FormData()
      const file = new File(['name,score\nA,90\n'], 'score.csv', { type: 'text/csv' })
      form.append('file', file)
      form.append('metadata_json', JSON.stringify({ role: 'data' }))
      const responsePromise = window.fetch('/api/projects/runs/run-a/assets', {
        method: 'POST',
        body: form,
        credentials: 'same-origin',
      })
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
      const uploadMessage = posted.find((message) => message.type === 'skillforge.project.assets.upload')
      expect(uploadMessage).toMatchObject({
        type: 'skillforge.project.assets.upload',
        gateway_token: 'gw-token-fetch',
        payload: {
          file,
          file_name: 'score.csv',
          mime_type: 'text/csv',
          metadata: { role: 'data' },
        },
      })
      expect(nativeFetch).not.toHaveBeenCalled()
      window.dispatchEvent(new MessageEvent('message', {
        data: {
          type: 'skillforge.project.assets.upload.result',
          request_id: uploadMessage?.request_id,
          ok: true,
          result: { ok: true, asset: { id: 'asset-fetch-1', file_name: 'score.csv' } },
        },
      }))
      const response = await responsePromise
      expect(response.status).toBe(200)
      expect(response.headers.get('x-skillforge-asset-upload')).toBe('project-gateway')
      await expect(response.json()).resolves.toEqual({ ok: true, asset: { id: 'asset-fetch-1', file_name: 'score.csv' } })
    } finally {
      delete (window as any).__PROJECT_GATEWAY_PARENT__
      delete (window as any).PlatformProjectGateway
      delete (window as any).SkillForgeProject
      delete (window as any).SFProjectGateway
      vi.useRealTimers()
    }
  })

  it('returns bridged asset upload errors as failed fetch responses', async () => {
    vi.useFakeTimers()
    try {
      Object.defineProperty(window, 'fetch', {
        value: vi.fn(async () => new Response('native should not be called', { status: 418 })),
        writable: true,
        configurable: true,
      })
      const posted: Array<Record<string, any>> = []
      ;(window as any).__PROJECT_GATEWAY_PARENT__ = {
        postMessage(message: Record<string, any>) {
          posted.push(message)
        },
      }

      const sdkCode = readFileSync(resolve(process.cwd(), 'public/project-gateway-sdk.js'), 'utf8')
      ;(0, eval)(sdkCode)
      vi.runOnlyPendingTimers()
      window.dispatchEvent(new MessageEvent('message', {
        data: {
          type: 'skillforge.project.context',
          gateway_token: 'gw-token-fetch-error',
          project: { id: 'project-a', capabilities: ['ai.analyze'] },
          run: { id: 'run-a' },
          gateway: { session_token: 'gw-token-fetch-error' },
        },
      }))

      const form = new FormData()
      const file = new File(['x'], 'too-large.mp4', { type: 'video/mp4' })
      form.append('file', file)
      const responsePromise = window.fetch('/api/projects/runs/run-a/assets', {
        method: 'POST',
        body: form,
        credentials: 'same-origin',
      })
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
      const uploadMessage = posted.find((message) => message.type === 'skillforge.project.assets.upload')
      window.dispatchEvent(new MessageEvent('message', {
        data: {
          type: 'skillforge.project.assets.upload.result',
          request_id: uploadMessage?.request_id,
          ok: false,
          error: '上传运行资产失败（too-large.mp4 · 80.0 MB）：文件大小 80.0 MB，上限 50.0 MB',
        },
      }))

      const response = await responsePromise
      expect(response.status).toBe(502)
      expect(response.headers.get('x-skillforge-asset-upload')).toBe('project-gateway')
      await expect(response.json()).resolves.toEqual({
        ok: false,
        error: '上传运行资产失败（too-large.mp4 · 80.0 MB）：文件大小 80.0 MB，上限 50.0 MB',
      })
    } finally {
      delete (window as any).__PROJECT_GATEWAY_PARENT__
      delete (window as any).PlatformProjectGateway
      delete (window as any).SkillForgeProject
      delete (window as any).SFProjectGateway
      vi.useRealTimers()
    }
  })

  it('autowire bridge exposes output helpers for generated pages', async () => {
    const posted: Array<Record<string, any>> = []
    ;(window as any).__PROJECT_GATEWAY_PARENT__ = {
      postMessage(message: Record<string, any>) {
        posted.push(message)
      },
    }

    const sdkCode = readFileSync(resolve(process.cwd(), 'public/project-gateway-sdk.js'), 'utf8')
    const autowireCode = readFileSync(resolve(process.cwd(), 'public/project-autowire.js'), 'utf8')
    ;(0, eval)(sdkCode)
    window.dispatchEvent(new MessageEvent('message', {
      data: {
        type: 'skillforge.project.context',
        gateway_token: 'gw-token-autowire',
        project: { id: 'generated-project', capabilities: ['ai.generate'] },
        run: { id: 'generated-run' },
        gateway: { session_token: 'gw-token-autowire' },
      },
    }))
    ;(0, eval)(autowireCode)

    const bridge = (window as any).SkillForgeProjectBridge
    expect(bridge).toBeTruthy()
    const outputPromise = bridge.output(
      { summary: 'generated output' },
      { request_id: 'generated-output-1', reports: [{ title: '报告', summary: '已生成' }] },
    )
    await Promise.resolve()
    await Promise.resolve()
    expect(posted.at(-1)).toMatchObject({
      type: 'skillforge.project.ingest',
      request_id: 'generated-output-1',
      gateway_token: 'gw-token-autowire',
      payload: {
        output: { summary: 'generated output' },
        reports: [{ title: '报告', summary: '已生成' }],
        auto_analyze: true,
      },
    })
    window.dispatchEvent(new MessageEvent('message', {
      data: {
        type: 'skillforge.project.ingest.result',
        request_id: 'generated-output-1',
        ok: true,
        result: { status: 'ai_completed' },
      },
    }))
    await expect(outputPromise).resolves.toEqual({ status: 'ai_completed' })

    delete (window as any).__PROJECT_GATEWAY_PARENT__
    delete (window as any).__SKILLFORGE_PROJECT_AUTOWIRE__
    delete (window as any).PlatformProjectGateway
    delete (window as any).SkillForgeProject
    delete (window as any).SFProjectGateway
    delete (window as any).SkillForgeProjectBridge
  })

  it('autowire records plain generated-page actions and sends output snapshots', async () => {
    vi.useFakeTimers()
    try {
      document.body.innerHTML = '<button id="generate">生成报告</button><section id="result">等待</section>'
      const posted: Array<Record<string, any>> = []
      ;(window as any).__PROJECT_GATEWAY_PARENT__ = {
        postMessage(message: Record<string, any>) {
          posted.push(message)
        },
      }

      const sdkCode = readFileSync(resolve(process.cwd(), 'public/project-gateway-sdk.js'), 'utf8')
      const autowireCode = readFileSync(resolve(process.cwd(), 'public/project-autowire.js'), 'utf8')
      ;(0, eval)(sdkCode)
      window.dispatchEvent(new MessageEvent('message', {
        data: {
          type: 'skillforge.project.context',
          gateway_token: 'gw-token-auto-action',
          project: { id: 'generated-action-project', capabilities: ['ai.cheap.generate'] },
          run: { id: 'generated-action-run' },
          gateway: { session_token: 'gw-token-auto-action' },
        },
      }))
      ;(0, eval)(autowireCode)

      await vi.advanceTimersByTimeAsync(0)
      document.getElementById('generate')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      const result = document.getElementById('result')
      if (result) result.textContent = '报告已生成，包含 3 个机会点'
      await vi.advanceTimersByTimeAsync(1000)

      const actionInput = posted.find((message) => (
        message.type === 'skillforge.project.input' &&
        message.payload?.metadata?.event_type === 'action_click'
      ))
      expect(actionInput).toMatchObject({
        type: 'skillforge.project.input',
        gateway_token: 'gw-token-auto-action',
        payload: {
          input: { event: 'click', label: '生成报告' },
          metadata: { event_type: 'action_click' },
        },
      })

      const outputSnapshot = posted.find((message) => (
        message.type === 'skillforge.project.ingest' &&
        message.payload?.metadata?.event_type === 'action_click_output'
      ))
      expect(outputSnapshot).toMatchObject({
        type: 'skillforge.project.ingest',
        gateway_token: 'gw-token-auto-action',
        payload: {
          output: {
            event: 'action_click_output',
            page: { visible_text: expect.stringContaining('报告已生成') },
          },
          auto_analyze: true,
        },
      })
    } finally {
      document.body.innerHTML = ''
      delete (window as any).__PROJECT_GATEWAY_PARENT__
      delete (window as any).__SKILLFORGE_PROJECT_AUTOWIRE__
      delete (window as any).PlatformProjectGateway
      delete (window as any).SkillForgeProject
      delete (window as any).SFProjectGateway
      delete (window as any).SkillForgeProjectBridge
      vi.useRealTimers()
    }
  })
})
