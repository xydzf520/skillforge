(function (global) {
  'use strict'

  if (global.__SKILLFORGE_PROJECT_AUTOWIRE__) return
  global.__SKILLFORGE_PROJECT_AUTOWIRE__ = true

  var gatewayReady = null
  var heartbeatStop = null
  var lastOutputHash = ''
  var REDACT_RE = /(password|passwd|secret|token|cookie|authorization|api[_-]?key|key)$/i
  var STATIC_ASSET_RE = /\.(?:js|mjs|css|map|png|jpg|jpeg|gif|webp|svg|ico|woff2?|ttf|otf|mp4|webm|mp3|wav)(?:[?#].*)?$/i
  var MAX_CAPTURE_CHARS = 4000
  var MAX_CAPTURE_LIST = 20
  var MAX_API_CAPTURE_EVENTS = 40
  var MAX_RUNTIME_CAPTURE_EVENTS = 40
  var apiCaptureCount = 0
  var runtimeCaptureCount = 0
  var lastStorageHash = ''
  var AI_PROVIDER_URL_RE = /(?:https?|wss?):\/\/(?:api\.openai\.com\/v1\/(?:chat\/completions|responses|completions|realtime)|api\.deepseek\.com\/v1\/chat\/completions|api\.anthropic\.com\/v1\/messages|generativelanguage\.googleapis\.com\/v1|dashscope\.aliyuncs\.com\/(?:api\/)?v1)/i
  var LOCAL_AI_API_PATH_RE = /^\/api\/(?:(?:v1\/)?(?:chat|messages|responses|completions)(?:\/|$)|(?:ai|llm|openai|anthropic|gemini|deepseek|dashscope|assistant|agent)(?:\/|$)|(?:generate|complete|completion|ask|analyze|analyse|summarize|summary|report)(?:\/|$))/i
  var LOCAL_SOCKET_PATH_RE = /^\/(?:(?:api\/)?(?:ws|websocket|socket|socket\.io|realtime|events|stream|chat|assistant|agent)(?:\/|$)|api\/)/i
  var PLATFORM_API_PATH_RE = /^\/api\/(?:projects|codex|auth|admin|system|settings|health|me)(?:\/|$)/i

  function gateway() {
    return global.PlatformProjectGateway || global.SkillForgeProject || global.SFProjectGateway || null
  }

  function requestId(prefix) {
    if (global.crypto && typeof global.crypto.randomUUID === 'function') return prefix + ':' + global.crypto.randomUUID()
    return prefix + ':' + Date.now().toString(36) + ':' + Math.random().toString(36).slice(2)
  }

  function text(value, limit) {
    var raw = String(value == null ? '' : value).replace(/\s+/g, ' ').trim()
    return raw.slice(0, limit || 1000)
  }

  function hashText(value) {
    var textValue = String(value == null ? '' : value)
    var hash = 0
    for (var i = 0; i < textValue.length; i += 1) {
      hash = ((hash << 5) - hash + textValue.charCodeAt(i)) | 0
    }
    return String(hash)
  }

  function safeValue(name, value) {
    if (REDACT_RE.test(String(name || ''))) return '[REDACTED]'
    return value
  }


  function compactValue(value, limit) {
    var raw = value
    try {
      if (typeof value !== 'string') raw = JSON.stringify(value)
    } catch (_) {
      raw = String(value == null ? '' : value)
    }
    raw = String(raw == null ? '' : raw)
    var max = limit || MAX_CAPTURE_CHARS
    return raw.length > max ? raw.slice(0, max) + '…' : raw
  }

  function redactDeep(value, depth, keyName) {
    if (REDACT_RE.test(String(keyName || ''))) return '[REDACTED]'
    if (value == null) return value
    if (typeof value === 'string') return compactValue(value, MAX_CAPTURE_CHARS)
    if (typeof value === 'number' || typeof value === 'boolean') return value
    if (typeof File !== 'undefined' && value instanceof File) {
      return { type: 'File', name: value.name, size: value.size, media_type: value.type || '' }
    }
    if (typeof Blob !== 'undefined' && value instanceof Blob) {
      return { type: 'Blob', size: value.size, media_type: value.type || '' }
    }
    if (depth <= 0) return compactValue(value, 600)
    if (Array.isArray(value)) return value.slice(0, MAX_CAPTURE_LIST).map(function (item) { return redactDeep(item, depth - 1) })
    if (typeof FormData !== 'undefined' && value instanceof FormData) {
      var form = {}
      try {
        value.forEach(function (item, key) { form[key] = safeValue(key, redactDeep(item, depth - 1, key)) })
      } catch (_) {}
      return form
    }
    if (typeof URLSearchParams !== 'undefined' && value instanceof URLSearchParams) {
      var params = {}
      value.forEach(function (item, key) { params[key] = safeValue(key, redactDeep(item, depth - 1, key)) })
      return params
    }
    if (typeof value === 'object') {
      var out = {}
      Object.keys(value).slice(0, MAX_CAPTURE_LIST).forEach(function (key) {
        out[key] = safeValue(key, redactDeep(value[key], depth - 1, key))
      })
      return out
    }
    return compactValue(value, 600)
  }

  function parseMaybeJson(value) {
    if (typeof value !== 'string') return redactDeep(value, 4)
    var textValue = value.trim()
    if (!textValue) return ''
    if ((textValue[0] === '{' && textValue[textValue.length - 1] === '}') || (textValue[0] === '[' && textValue[textValue.length - 1] === ']')) {
      try { return redactDeep(JSON.parse(textValue), 4) } catch (_) {}
    }
    return compactValue(textValue, MAX_CAPTURE_CHARS)
  }

  function safeUrl(value) {
    var raw = String(value == null ? '' : value)
    if (!raw) return ''
    if (/^(?:data|blob|javascript):/i.test(raw)) return raw.split(':', 1)[0] + ':...'
    try {
      var parsed = new URL(raw, global.location && global.location.href ? global.location.href : 'http://skillforge.local/')
      var path = parsed.pathname || '/'
      if (parsed.origin && global.location && parsed.origin !== global.location.origin) return parsed.origin + path
      return path
    } catch (_) {
      return raw.split(/[?#]/, 1)[0]
    }
  }

  function parsedUrl(value) {
    try {
      return new URL(String(value == null ? '' : value), global.location && global.location.href ? global.location.href : 'http://skillforge.local/')
    } catch (_) {
      return null
    }
  }

  function localAiApiInfo(value) {
    var raw = String(value == null ? '' : value)
    if (!raw || /^(?:data|blob|javascript):/i.test(raw)) return null
    var parsed = parsedUrl(raw)
    if (!parsed) return null
    var host = String(parsed.hostname || '').toLowerCase()
    var isLocalDev = host === 'localhost' || host === '0.0.0.0' || /^127\./.test(host)
    var isSameOrigin = !global.location || parsed.origin === global.location.origin
    if (!isSameOrigin && !isLocalDev) return null
    if (!LOCAL_AI_API_PATH_RE.test(parsed.pathname || '')) return null
    return { path: parsed.pathname || '/', origin: parsed.origin, host: host }
  }

  function localBackendApiInfo(value) {
    var raw = String(value == null ? '' : value)
    if (!raw || /^(?:data|blob|javascript):/i.test(raw)) return null
    var parsed = parsedUrl(raw)
    if (!parsed) return null
    var host = String(parsed.hostname || '').toLowerCase()
    var isLocalDev = host === 'localhost' || host === '0.0.0.0' || /^127\./.test(host)
    var isSameOrigin = !global.location || parsed.origin === global.location.origin
    var path = parsed.pathname || '/'
    if (!isSameOrigin && !isLocalDev) return null
    if (!/^\/api\//i.test(path)) return null
    if (PLATFORM_API_PATH_RE.test(path)) return null
    if (/project-gateway-sdk\.js|project-autowire\.js/i.test(path)) return null
    return { path: path, origin: parsed.origin, host: host }
  }

  function localSocketApiInfo(value) {
    var raw = String(value == null ? '' : value)
    if (!raw || /^(?:data|blob|javascript):/i.test(raw)) return null
    var parsed = parsedUrl(raw)
    if (!parsed) return null
    var host = String(parsed.hostname || '').toLowerCase()
    var isLocalDev = host === 'localhost' || host === '0.0.0.0' || /^127\./.test(host)
    var loc = global.location || {}
    var isSameOrigin = parsed.origin && loc.origin && parsed.origin === loc.origin
    var isSameHost = parsed.host && loc.host && parsed.host === loc.host
    var path = parsed.pathname || '/'
    if (!isSameOrigin && !isSameHost && !isLocalDev) return null
    if (!LOCAL_SOCKET_PATH_RE.test(path)) return null
    if (PLATFORM_API_PATH_RE.test(path)) return null
    if (/project-gateway-sdk\.js|project-autowire\.js/i.test(path)) return null
    return { path: path, origin: parsed.origin, host: host }
  }

  function shouldCaptureApi(url, method) {
    if (!url) return false
    var clean = safeUrl(url)
    if (!clean || /project-gateway-sdk\.js|project-autowire\.js/i.test(clean)) return false
    if (STATIC_ASSET_RE.test(clean)) return false
    if (apiCaptureCount >= MAX_API_CAPTURE_EVENTS) return false
    method = String(method || 'GET').toUpperCase()
    return method !== 'GET' || /\/api\//i.test(clean) || /^https?:\/\//i.test(clean)
  }

  function nextApiCaptureId(prefix) {
    apiCaptureCount += 1
    return requestId(prefix || 'auto-api')
  }

  function nextRuntimeCaptureId(prefix) {
    runtimeCaptureCount += 1
    return requestId(prefix || 'auto-runtime')
  }

  function requestBodySnapshot(body) {
    if (body == null) return undefined
    if (typeof body === 'string') return parseMaybeJson(body)
    return redactDeep(body, 3)
  }

  function responseBodySnapshot(body, contentType) {
    if (body == null || body === '') return ''
    if (/json|javascript/i.test(String(contentType || ''))) return parseMaybeJson(String(body))
    return compactValue(String(body), MAX_CAPTURE_CHARS)
  }

  function shouldProxyAiProvider(url, method) {
    method = String(method || 'GET').toUpperCase()
    return method !== 'GET' && AI_PROVIDER_URL_RE.test(String(url || '')) && gateway() && (typeof gateway().capability === 'function' || typeof gateway().ai === 'function')
  }

  function shouldProxyGeneratedAiApi(url, method) {
    method = String(method || 'GET').toUpperCase()
    return method !== 'GET' && !!localAiApiInfo(url) && gateway() && (typeof gateway().capability === 'function' || typeof gateway().ai === 'function')
  }

  function shouldFallbackGeneratedBackendApi(url, method) {
    method = String(method || 'GET').toUpperCase()
    return method !== 'HEAD' && !!localBackendApiInfo(url) && gateway() && typeof gateway().ingest === 'function'
  }

  function shouldFallbackGeneratedSocketApi(url) {
    return !!localSocketApiInfo(url) && gateway() && typeof gateway().ingest === 'function'
  }

  function shouldProxyEventStreamAi(url) {
    return (AI_PROVIDER_URL_RE.test(String(url || '')) || !!localAiApiInfo(url)) && gateway() && (typeof gateway().capability === 'function' || typeof gateway().ai === 'function')
  }

  function shouldWrapGeneratedWorker(url) {
    var raw = String(url == null ? '' : url)
    if (!raw || /^(?:data|javascript):/i.test(raw)) return false
    if (/^blob:/i.test(raw)) return true
    var parsed = parsedUrl(raw)
    if (!parsed) return false
    var host = String(parsed.hostname || '').toLowerCase()
    var isLocalDev = host === 'localhost' || host === '0.0.0.0' || /^127\./.test(host)
    var isSameOrigin = !global.location || parsed.origin === global.location.origin
    if (!isSameOrigin && !isLocalDev) return false
    var path = parsed.pathname || ''
    if (/project-gateway-sdk\.js|project-autowire\.js/i.test(path)) return false
    return /\.(?:js|mjs|cjs)(?:[?#].*)?$/i.test(path) || /(?:worker|sw|service-worker)/i.test(path)
  }

  function shouldCaptureGeneratedServiceWorker(url) {
    var raw = String(url == null ? '' : url)
    if (!raw || /^(?:data|blob|javascript):/i.test(raw)) return false
    var parsed = parsedUrl(raw)
    if (!parsed) return false
    var host = String(parsed.hostname || '').toLowerCase()
    var isLocalDev = host === 'localhost' || host === '0.0.0.0' || /^127\./.test(host)
    var isSameOrigin = !global.location || parsed.origin === global.location.origin
    if (!isSameOrigin && !isLocalDev) return false
    var path = parsed.pathname || ''
    if (/project-gateway-sdk\.js|project-autowire\.js/i.test(path)) return false
    return /(?:^|\/)(?:service-worker|sw|worker)(?:[.-][^/]*)?\.(?:js|mjs|cjs)$/i.test(path) || /\/(?:sw|service-worker)(?:\/|$)/i.test(path)
  }

  function providerName(url) {
    if (localAiApiInfo(url)) return 'generated_api'
    var raw = String(url || '').toLowerCase()
    if (raw.indexOf('anthropic.com') >= 0) return 'anthropic'
    if (raw.indexOf('generativelanguage.googleapis.com') >= 0) return 'gemini'
    if (raw.indexOf('dashscope.aliyuncs.com') >= 0) return 'dashscope'
    if (raw.indexOf('deepseek.com') >= 0) return 'deepseek'
    return 'openai'
  }

  function messageText(content) {
    if (content == null) return ''
    if (typeof content === 'string') return content
    if (Array.isArray(content)) {
      return content.map(function (item) {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object') return item.text || item.content || item.value || ''
        return ''
      }).filter(Boolean).join('\n')
    }
    if (typeof content === 'object') return content.text || content.content || JSON.stringify(redactDeep(content, 2))
    return String(content)
  }

  function aiPromptFromProviderPayload(payload) {
    payload = payload && typeof payload === 'object' ? payload : {}
    if (Array.isArray(payload.messages) && payload.messages.length) {
      var lastUser = null
      for (var i = payload.messages.length - 1; i >= 0; i -= 1) {
        if (payload.messages[i] && payload.messages[i].role === 'user') {
          lastUser = payload.messages[i]
          break
        }
      }
      return messageText((lastUser || payload.messages[payload.messages.length - 1]).content) || compactValue(payload.messages, MAX_CAPTURE_CHARS)
    }
    if (payload.prompt) return messageText(payload.prompt)
    if (payload.input) return messageText(payload.input)
    if (payload.message) return messageText(payload.message)
    if (payload.text) return messageText(payload.text)
    if (payload.query) return messageText(payload.query)
    if (payload.question) return messageText(payload.question)
    if (Array.isArray(payload.contents) && payload.contents.length) {
      return payload.contents.map(function (item) { return messageText(item.parts || item.content || item.text || item) }).join('\n')
    }
    return compactValue(payload, MAX_CAPTURE_CHARS)
  }

  function promptFromUrlQuery(url) {
    var parsed = parsedUrl(url)
    if (parsed && parsed.searchParams) {
      var keys = ['prompt', 'q', 'query', 'message', 'input', 'text', 'question']
      for (var i = 0; i < keys.length; i += 1) {
        var value = parsed.searchParams.get(keys[i])
        if (value) return text(value, MAX_CAPTURE_CHARS)
      }
    }
    var snapshot = pageSnapshot()
    return snapshot.visible_text || snapshot.title || '请基于项目 EventSource 流生成回复。'
  }

  function preferredAiCapability(url) {
    var caps = []
    try {
      if (gateway() && typeof gateway().capabilities === 'function') caps = gateway().capabilities() || []
    } catch (_) {}
    var wantsChat = /chat\/completions|\/messages|\/realtime|\/api\/(?:v1\/)?(?:chat|messages)|\/api\/(?:assistant|agent)/i.test(String(url || ''))
    var order = wantsChat
      ? ['ai.cheap.chat', 'platform.ai.cheap.chat', 'ai.chat', 'platform.ai.chat', 'ai.cheap.generate', 'platform.ai.cheap.generate', 'ai.generate', 'platform.ai.generate']
      : ['ai.cheap.generate', 'platform.ai.cheap.generate', 'ai.generate', 'platform.ai.generate', 'ai.cheap.chat', 'ai.chat']
    for (var i = 0; i < order.length; i += 1) {
      if (caps.indexOf(order[i]) >= 0) return order[i]
    }
    return wantsChat ? 'ai.cheap.chat' : 'ai.cheap.generate'
  }

  function aiOutputText(result) {
    var value = result
    if (value && value.result) value = value.result
    if (value && value.output != null) return typeof value.output === 'string' ? value.output : compactValue(value.output, MAX_CAPTURE_CHARS)
    if (value && value.text != null) return String(value.text)
    if (value && value.content != null) return messageText(value.content)
    return compactValue(result, MAX_CAPTURE_CHARS)
  }

  function providerResponseBody(url, capabilityResult) {
    var provider = providerName(url)
    var output = aiOutputText(capabilityResult)
    var model = (capabilityResult && capabilityResult.result && capabilityResult.result.model) || (capabilityResult && capabilityResult.model) || 'skillforge-platform-ai'
    if (provider === 'generated_api') {
      return {
        ok: true,
        output: output,
        text: output,
        message: output,
        summary: output,
        response: output,
        result: { output: output, text: output, model: model },
        data: { output: output, text: output, model: model },
        choices: [{ index: 0, message: { role: 'assistant', content: output }, finish_reason: 'stop' }],
        skillforge: { proxied_by: 'Project Gateway', credential_location: 'platform_only', generated_api_proxy: true },
      }
    }
    if (provider === 'anthropic') {
      return { id: requestId('sf-anthropic'), type: 'message', role: 'assistant', model: model, content: [{ type: 'text', text: output }], stop_reason: 'end_turn' }
    }
    if (provider === 'gemini') {
      return { candidates: [{ content: { role: 'model', parts: [{ text: output }] }, finishReason: 'STOP' }], modelVersion: model }
    }
    return {
      id: requestId('sf-openai'),
      object: /responses/i.test(String(url || '')) ? 'response' : 'chat.completion',
      created: Math.floor(Date.now() / 1000),
      model: model,
      choices: [{ index: 0, message: { role: 'assistant', content: output }, finish_reason: 'stop' }],
      output_text: output,
      output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: output }] }],
      skillforge: { proxied_by: 'Project Gateway', credential_location: 'platform_only' },
    }
  }

  function providerWantsStream(url, payload) {
    payload = payload && typeof payload === 'object' ? payload : {}
    return payload.stream === true || /stream/i.test(String(url || ''))
  }

  function providerStreamResponseText(url, capabilityResult) {
    var provider = providerName(url)
    var output = aiOutputText(capabilityResult)
    var model = (capabilityResult && capabilityResult.result && capabilityResult.result.model) || (capabilityResult && capabilityResult.model) || 'skillforge-platform-ai'
    if (provider === 'anthropic') {
      return [
        'event: message_start',
        'data: ' + JSON.stringify({ type: 'message_start', message: { id: requestId('sf-anthropic'), type: 'message', role: 'assistant', model: model, content: [] } }),
        '',
        'event: content_block_delta',
        'data: ' + JSON.stringify({ type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: output } }),
        '',
        'event: message_stop',
        'data: ' + JSON.stringify({ type: 'message_stop' }),
        '',
        '',
      ].join('\n')
    }
    var id = requestId('sf-openai-chunk')
    return [
      'data: ' + JSON.stringify({ id: id, object: 'chat.completion.chunk', created: Math.floor(Date.now() / 1000), model: model, choices: [{ index: 0, delta: { role: 'assistant' }, finish_reason: null }] }),
      '',
      'data: ' + JSON.stringify({ id: id, object: 'chat.completion.chunk', created: Math.floor(Date.now() / 1000), model: model, choices: [{ index: 0, delta: { content: output }, finish_reason: null }] }),
      '',
      'data: ' + JSON.stringify({ id: id, object: 'chat.completion.chunk', created: Math.floor(Date.now() / 1000), model: model, choices: [{ index: 0, delta: {}, finish_reason: 'stop' }], skillforge: { proxied_by: 'Project Gateway', credential_location: 'platform_only' } }),
      '',
      'data: [DONE]',
      '',
      '',
    ].join('\n')
  }

  function providerErrorBody(error) {
    return {
      error: {
        message: String((error && (error.message || error.error)) || 'Project Gateway AI 代理失败'),
        type: 'skillforge_project_gateway_error',
      },
      skillforge: { proxied_by: 'Project Gateway', credential_location: 'platform_only' },
    }
  }

  function eventSourceDataFromProxyPayload(url, payload) {
    if (!payload || !/text\/event-stream/i.test(String(payload.contentType || ''))) return payload && payload.body
    var output = ''
    var lastJson = null
    String(payload.body || '').split(/\r?\n/).forEach(function (line) {
      var textLine = line.trim()
      if (textLine.indexOf('data:') !== 0) return
      var data = textLine.slice(5).trim()
      if (!data || data === '[DONE]') return
      try {
        var parsed = JSON.parse(data)
        lastJson = parsed
        var delta = parsed && parsed.choices && parsed.choices[0] && parsed.choices[0].delta
        if (delta && delta.content) output += String(delta.content)
        var content = parsed && parsed.content && parsed.content[0]
        if (content && content.delta && content.delta.text) output += String(content.delta.text)
      } catch (_) {
        output += data
      }
    })
    if (output) {
      return JSON.stringify({
        ok: true,
        output: output,
        text: output,
        message: output,
        choices: [{ index: 0, message: { role: 'assistant', content: output }, finish_reason: 'stop' }],
        skillforge: {
          proxied_by: 'Project Gateway',
          credential_location: 'platform_only',
          generated_api_proxy: !!localAiApiInfo(url),
          eventsource_proxy: true,
        },
      })
    }
    return JSON.stringify(lastJson || { ok: true, raw: String(payload.body || ''), skillforge: { eventsource_proxy: true } })
  }

  function callAiProviderGateway(url, method, body, captureId, transport) {
    var payload = parseMaybeJson(typeof body === 'string' ? body : (body == null ? '{}' : body))
    var prompt = aiPromptFromProviderPayload(payload)
    var provider = providerName(url)
    var capability = preferredAiCapability(url)
    transport = transport || 'fetch_ai_proxy'
    captureApiInput(captureId, transport, method, url, body)
    return ready().then(function () {
      var g = gateway()
      var capabilityPayload = {
        capability: capability,
        prompt: prompt || '请根据项目前端的模型请求生成回复。',
        input: {
          provider_proxy: {
            provider: provider,
            url: safeUrl(url),
            original_payload: redactDeep(payload, 3),
          },
        },
        json_mode: false,
      }
      var call = typeof g.capability === 'function'
        ? g.capability(capabilityPayload, { requestId: captureId + ':capability' })
        : g.ai(capabilityPayload, null, { requestId: captureId + ':capability' })
      return call.then(function (result) {
        if (providerWantsStream(url, payload)) {
          var streamText = providerStreamResponseText(url, result)
          captureApiOutput(captureId, transport, method, url, 200, 'text/event-stream', streamText, true)
          return { status: 200, ok: true, contentType: 'text/event-stream; charset=utf-8', body: streamText }
        }
        var responsePayload = providerResponseBody(url, result)
        var responseText = JSON.stringify(responsePayload)
        captureApiOutput(captureId, transport, method, url, 200, 'application/json', responseText, true)
        return { status: 200, ok: true, contentType: 'application/json', body: responseText }
      })
    }).catch(function (error) {
      var errorPayload = providerErrorBody(error)
      var errorText = JSON.stringify(errorPayload)
      captureApiOutput(captureId, transport, method, url, 502, 'application/json', errorText, false)
      return { status: 502, ok: false, contentType: 'application/json', body: errorText }
    })
  }

  function proxyAiProviderFetch(url, method, body, captureId) {
    return callAiProviderGateway(url, method, body, captureId, 'fetch_ai_proxy').then(function (payload) {
      return new Response(payload.body, { status: payload.status, headers: { 'content-type': payload.contentType, 'x-skillforge-ai-proxy': 'project-gateway' } })
    })
  }

  function generatedBackendFallbackPayload(url, method, body, nativeStatus, error) {
    var requestBody = requestBodySnapshot(body)
    var page = pageSnapshot()
    var path = safeUrl(url)
    var summary = '项目本地后端 API 已由 Project Gateway 降级接管：' + method + ' ' + path
    var items = [
      {
        id: 'sf-generated-api-1',
        title: page.title || 'SkillForge 项目数据',
        name: page.title || 'SkillForge 项目数据',
        status: 'ready',
        summary: summary,
      },
    ]
    return {
      ok: true,
      summary: summary,
      message: summary,
      data: {
        summary: summary,
        items: items,
        request: { method: method, url: path, body: requestBody },
        page: page,
      },
      items: items,
      rows: items,
      results: items,
      total: items.length,
      count: items.length,
      skillforge: {
        proxied_by: 'Project Gateway',
        credential_location: 'platform_only',
        generated_backend_fallback: true,
        native_status: nativeStatus == null ? null : nativeStatus,
        error: error ? String(error && (error.message || error)).slice(0, 300) : null,
      },
    }
  }

  function fallbackGeneratedBackendFetch(url, method, body, captureId, nativeStatus, error) {
    var payload = generatedBackendFallbackPayload(url, method, body, nativeStatus, error)
    var bodyText = JSON.stringify(payload)
    captureApiOutput(captureId, 'fetch_backend_fallback', method, url, 200, 'application/json', bodyText, true)
    return new Response(bodyText, { status: 200, headers: { 'content-type': 'application/json', 'x-skillforge-backend-fallback': 'project-gateway' } })
  }

  function captureApiInput(captureId, transport, method, url, body) {
    return recordInput(
      { event: 'api_request', transport: transport, method: method, url: safeUrl(url), body: requestBodySnapshot(body), page: pageSnapshot() },
      { event_type: 'api_request', transport: transport, url: safeUrl(url) },
      { request_id: captureId + ':input' }
    ).catch(function () {})
  }

  function captureApiOutput(captureId, transport, method, url, status, contentType, body, ok) {
    return ingest(
      {
        summary: '项目 API 输出：' + method + ' ' + safeUrl(url) + ' -> ' + status,
        event: 'api_response',
        api_response: {
          transport: transport,
          method: method,
          url: safeUrl(url),
          status: status,
          ok: ok !== false,
          content_type: String(contentType || '').slice(0, 120),
          body: responseBodySnapshot(body, contentType),
        },
      },
      {
        request_id: captureId + ':output',
        metadata: { source: 'project_autowire', event_type: 'api_response', transport: transport, url: safeUrl(url) },
        auto_analyze: false,
      }
    ).catch(function () {})
  }

  function installFetchCapture() {
    if (typeof global.fetch !== 'function' || global.fetch.__skillforgeAutowireWrapped) return
    var nativeFetch = global.fetch.bind(global)
    function wrappedFetch(resource, init) {
      var url = resource && resource.url ? resource.url : resource
      var method = String((init && init.method) || (resource && resource.method) || 'GET').toUpperCase()
      if (shouldProxyAiProvider(url, method) || shouldProxyGeneratedAiApi(url, method)) {
        var proxyCaptureId = nextApiCaptureId('auto-ai-fetch')
        if (init && Object.prototype.hasOwnProperty.call(init, 'body')) {
          return proxyAiProviderFetch(url, method, init.body, proxyCaptureId)
        }
        if (resource && typeof resource.clone === 'function' && typeof resource.text === 'function') {
          try {
            return resource.clone().text()
              .then(function (requestBody) { return proxyAiProviderFetch(url, method, requestBody, proxyCaptureId) })
              .catch(function () { return proxyAiProviderFetch(url, method, undefined, proxyCaptureId) })
          } catch (_) {
            return proxyAiProviderFetch(url, method, undefined, proxyCaptureId)
          }
        }
        return proxyAiProviderFetch(url, method, undefined, proxyCaptureId)
      }
      if (!shouldCaptureApi(url, method)) return nativeFetch(resource, init)
      var captureId = nextApiCaptureId('auto-fetch')
      var body = init && Object.prototype.hasOwnProperty.call(init, 'body') ? init.body : undefined
      captureApiInput(captureId, 'fetch', method, url, body)
      return nativeFetch(resource, init).then(function (response) {
        if (shouldFallbackGeneratedBackendApi(url, method) && response && !response.ok && response.status !== 401 && response.status !== 403) {
          return fallbackGeneratedBackendFetch(url, method, body, captureId, response.status, null)
        }
        try {
          var clone = response && typeof response.clone === 'function' ? response.clone() : response
          var contentType = clone && clone.headers && typeof clone.headers.get === 'function' ? clone.headers.get('content-type') : ''
          if (clone && typeof clone.text === 'function') {
            clone.text().then(function (bodyText) {
              captureApiOutput(captureId, 'fetch', method, url, response.status, contentType, bodyText, response.ok)
            }).catch(function () {
              captureApiOutput(captureId, 'fetch', method, url, response.status, contentType, '', response.ok)
            })
          } else {
            captureApiOutput(captureId, 'fetch', method, url, response && response.status, contentType, '', response && response.ok)
          }
        } catch (_) {}
        return response
      }).catch(function (error) {
        if (shouldFallbackGeneratedBackendApi(url, method)) {
          return fallbackGeneratedBackendFetch(url, method, body, captureId, null, error)
        }
        throw error
      })
    }
    wrappedFetch.__skillforgeAutowireWrapped = true
    wrappedFetch.__skillforgeNativeFetch = nativeFetch
    global.fetch = wrappedFetch
  }

  function installXhrCapture() {
    if (!global.XMLHttpRequest || !global.XMLHttpRequest.prototype || global.XMLHttpRequest.prototype.__skillforgeAutowireWrapped) return
    var proto = global.XMLHttpRequest.prototype
    var nativeOpen = proto.open
    var nativeSend = proto.send
    var nativeSetRequestHeader = proto.setRequestHeader
    proto.open = function (method, url) {
      this.__sfAutowireMethod = String(method || 'GET').toUpperCase()
      this.__sfAutowireUrl = url
      this.__sfAutowireRequestHeaders = {}
      return nativeOpen.apply(this, arguments)
    }
    if (nativeSetRequestHeader) {
      proto.setRequestHeader = function (name, value) {
        this.__sfAutowireRequestHeaders = this.__sfAutowireRequestHeaders || {}
        this.__sfAutowireRequestHeaders[String(name || '').toLowerCase()] = String(value == null ? '' : value)
        return nativeSetRequestHeader.apply(this, arguments)
      }
    }
    proto.send = function (body) {
      var method = this.__sfAutowireMethod || 'GET'
      var url = this.__sfAutowireUrl || ''
      if (shouldProxyAiProvider(url, method) || shouldProxyGeneratedAiApi(url, method)) {
        return proxyAiProviderXhr(this, method, url, body, nextApiCaptureId('auto-ai-xhr'))
      }
      if (shouldFallbackGeneratedBackendApi(url, method)) {
        return proxyGeneratedBackendXhr(this, method, url, body, nextApiCaptureId('auto-backend-xhr'))
      }
      var capture = shouldCaptureApi(url, method)
      var captureId = capture ? nextApiCaptureId('auto-xhr') : ''
      if (capture) {
        captureApiInput(captureId, 'xhr', method, url, body)
        try {
          this.addEventListener('loadend', function () {
            var contentType = ''
            try { contentType = this.getResponseHeader && this.getResponseHeader('content-type') } catch (_) {}
            captureApiOutput(captureId, 'xhr', method, url, this.status, contentType, this.responseText || '', this.status >= 200 && this.status < 400)
          })
        } catch (_) {}
      }
      return nativeSend.apply(this, arguments)
    }
    proto.__skillforgeAutowireWrapped = true
  }

  function setXhrProp(xhr, key, value) {
    try {
      Object.defineProperty(xhr, key, { configurable: true, value: value })
    } catch (_) {
      try { xhr[key] = value } catch (_) {}
    }
  }

  function dispatchXhrEvent(xhr, type) {
    var event = null
    try {
      event = new Event(type)
      xhr.dispatchEvent(event)
    } catch (_) {
      try {
        event = global.document && global.document.createEvent ? global.document.createEvent('Event') : null
        if (event && event.initEvent) {
          event.initEvent(type, false, false)
          xhr.dispatchEvent(event)
        }
      } catch (_) {}
    }
    try {
      var handler = xhr && xhr['on' + type]
      if (typeof handler === 'function') handler.call(xhr, event || { type: type })
    } catch (_) {}
  }

  function completeProxyXhr(xhr, status, contentType, bodyText, extraHeaders) {
    var headers = { 'content-type': contentType }
    var headerExtras = extraHeaders || { 'x-skillforge-ai-proxy': 'project-gateway' }
    Object.keys(headerExtras).forEach(function (key) { headers[String(key || '').toLowerCase()] = headerExtras[key] })
    var responseValue = bodyText
    try {
      if (xhr.responseType === 'json') responseValue = JSON.parse(bodyText)
    } catch (_) {}
    setXhrProp(xhr, 'readyState', 4)
    setXhrProp(xhr, 'status', status)
    setXhrProp(xhr, 'statusText', status >= 200 && status < 400 ? 'OK' : 'Bad Gateway')
    setXhrProp(xhr, 'responseURL', safeUrl(xhr.__sfAutowireUrl || ''))
    setXhrProp(xhr, 'responseText', bodyText)
    setXhrProp(xhr, 'response', responseValue)
    setXhrProp(xhr, 'getResponseHeader', function (name) {
      return headers[String(name || '').toLowerCase()] || null
    })
    setXhrProp(xhr, 'getAllResponseHeaders', function () {
      return Object.keys(headers).map(function (key) { return key + ': ' + headers[key] }).join('\r\n')
    })
    dispatchXhrEvent(xhr, 'readystatechange')
    dispatchXhrEvent(xhr, 'load')
    dispatchXhrEvent(xhr, 'loadend')
  }

  function proxyAiProviderXhr(xhr, method, url, body, captureId) {
    callAiProviderGateway(url, method, body, captureId, 'xhr_ai_proxy')
      .then(function (payload) {
        completeProxyXhr(xhr, payload.status, payload.contentType, payload.body)
      })
      .catch(function (error) {
        var errorText = JSON.stringify(providerErrorBody(error))
        captureApiOutput(captureId, 'xhr_ai_proxy', method, url, 502, 'application/json', errorText, false)
        completeProxyXhr(xhr, 502, 'application/json', errorText)
      })
    return undefined
  }

  function proxyGeneratedBackendXhr(xhr, method, url, body, captureId) {
    captureApiInput(captureId, 'xhr_backend_fallback', method, url, body)
    global.setTimeout(function () {
      var payload = generatedBackendFallbackPayload(url, method, body, null, null)
      var bodyText = JSON.stringify(payload)
      captureApiOutput(captureId, 'xhr_backend_fallback', method, url, 200, 'application/json', bodyText, true)
      completeProxyXhr(xhr, 200, 'application/json', bodyText, { 'x-skillforge-backend-fallback': 'project-gateway' })
    }, 0)
    return undefined
  }

  function installApiCapture() {
    installFetchCapture()
    installXhrCapture()
    installBeaconCapture()
    installWebSocketCapture()
    installEventSourceCapture()
    installWorkerCapture()
    installServiceWorkerCapture()
    installStorageCapture()
    installIndexedDbCapture()
    installRuntimeErrorCapture()
  }

  function installBeaconCapture() {
    var nav = global.navigator
    if (!nav || typeof nav.sendBeacon !== 'function' || nav.sendBeacon.__skillforgeAutowireWrapped) return
    var nativeBeacon = nav.sendBeacon.bind(nav)
    function wrappedBeacon(url, data) {
      var method = 'POST'
      if (shouldProxyAiProvider(url, method) || shouldProxyGeneratedAiApi(url, method)) {
        callAiProviderGateway(url, method, data, nextApiCaptureId('auto-ai-beacon'), 'beacon_ai_proxy')
        return true
      }
      if (shouldFallbackGeneratedBackendApi(url, method)) {
        var fallbackId = nextApiCaptureId('auto-backend-beacon')
        captureApiInput(fallbackId, 'beacon_backend_fallback', method, url, data)
        global.setTimeout(function () {
          var payload = generatedBackendFallbackPayload(url, method, data, null, null)
          captureApiOutput(fallbackId, 'beacon_backend_fallback', method, url, 202, 'application/json', JSON.stringify(payload), true)
        }, 0)
        return true
      }
      if (!shouldCaptureApi(url, method)) return nativeBeacon(url, data)
      var captureId = nextApiCaptureId('auto-beacon')
      captureApiInput(captureId, 'beacon', method, url, data)
      var queued = false
      try {
        queued = nativeBeacon(url, data)
      } catch (error) {
        captureApiOutput(captureId, 'beacon', method, url, 0, 'text/plain', String(error && (error.message || error)), false)
        throw error
      }
      captureApiOutput(captureId, 'beacon', method, url, queued ? 204 : 0, 'text/plain', queued ? 'queued' : 'rejected', queued)
      return queued
    }
    wrappedBeacon.__skillforgeAutowireWrapped = true
    wrappedBeacon.__skillforgeNativeBeacon = nativeBeacon
    try {
      nav.sendBeacon = wrappedBeacon
    } catch (_) {
      try { Object.defineProperty(nav, 'sendBeacon', { configurable: true, writable: true, value: wrappedBeacon }) } catch (_) {}
    }
  }

  function emitProxyWebSocketEvent(ws, type, props) {
    props = props || {}
    var event = null
    try {
      if (type === 'message' && typeof global.MessageEvent === 'function') {
        event = new global.MessageEvent('message', { data: props.data, origin: props.origin || '' })
      } else if (type === 'close' && typeof global.CloseEvent === 'function') {
        event = new global.CloseEvent('close', { code: props.code || 1000, reason: props.reason || '', wasClean: props.wasClean !== false })
      } else {
        event = new Event(type)
      }
    } catch (_) {
      try {
        event = global.document && global.document.createEvent ? global.document.createEvent('Event') : null
        if (event && event.initEvent) event.initEvent(type, false, false)
      } catch (_) {}
    }
    if (!event) event = { type: type }
    Object.keys(props).forEach(function (key) {
      try { Object.defineProperty(event, key, { configurable: true, value: props[key] }) } catch (_) {
        try { event[key] = props[key] } catch (_) {}
      }
    })
    try {
      if (ws.dispatchEvent && event instanceof Event) ws.dispatchEvent(event)
    } catch (_) {}
    try {
      var handler = ws && ws['on' + type]
      if (typeof handler === 'function') handler.call(ws, event)
    } catch (_) {}
  }

  function createProxyWebSocket(url, protocols, transport) {
    var base = null
    try { base = typeof EventTarget === 'function' ? new EventTarget() : null } catch (_) {}
    var listeners = {}
    var ws = base || {}
    if (!base) {
      ws.addEventListener = function (type, listener) {
        if (typeof listener !== 'function') return
        listeners[type] = listeners[type] || []
        listeners[type].push(listener)
      }
      ws.removeEventListener = function (type, listener) {
        listeners[type] = (listeners[type] || []).filter(function (item) { return item !== listener })
      }
      ws.dispatchEvent = function (event) {
        ;(listeners[event.type] || []).slice().forEach(function (listener) {
          try { listener.call(ws, event) } catch (error) { global.setTimeout(function () { throw error }, 0) }
        })
        return true
      }
    }
    setXhrProp(ws, 'url', String(url || ''))
    setXhrProp(ws, 'protocol', Array.isArray(protocols) ? protocols[0] || '' : typeof protocols === 'string' ? protocols : '')
    setXhrProp(ws, 'extensions', '')
    setXhrProp(ws, 'bufferedAmount', 0)
    setXhrProp(ws, 'binaryType', 'blob')
    setXhrProp(ws, 'readyState', 0)
    setXhrProp(ws, 'send', function (data) {
      if (ws.readyState !== 1) throw new Error('WebSocket is not open')
      var captureId = nextApiCaptureId(transport === 'websocket_ai_proxy' ? 'auto-ai-ws' : 'auto-backend-ws')
      if (transport === 'websocket_ai_proxy') {
        callAiProviderGateway(url, 'POST', data, captureId, transport).then(function (payload) {
          emitProxyWebSocketEvent(ws, 'message', { data: payload.body, origin: safeUrl(url) })
        }).catch(function (error) {
          var errorText = JSON.stringify(providerErrorBody(error))
          captureApiOutput(captureId, transport, 'POST', url, 502, 'application/json', errorText, false)
          emitProxyWebSocketEvent(ws, 'message', { data: errorText, origin: safeUrl(url) })
        })
        return undefined
      }
      captureApiInput(captureId, transport, 'POST', url, data)
      global.setTimeout(function () {
        var payload = generatedBackendFallbackPayload(url, 'POST', data, null, null)
        var bodyText = JSON.stringify(payload)
        captureApiOutput(captureId, transport, 'POST', url, 200, 'application/json', bodyText, true)
        emitProxyWebSocketEvent(ws, 'message', { data: bodyText, origin: safeUrl(url) })
      }, 0)
      return undefined
    })
    setXhrProp(ws, 'close', function (code, reason) {
      if (ws.readyState === 3) return
      setXhrProp(ws, 'readyState', 3)
      emitProxyWebSocketEvent(ws, 'close', { code: code || 1000, reason: reason || 'skillforge_project_gateway_proxy', wasClean: true })
    })
    global.setTimeout(function () {
      if (ws.readyState === 3) return
      setXhrProp(ws, 'readyState', 1)
      recordInput(
        { event: 'websocket_open', transport: transport, url: safeUrl(url), page: pageSnapshot() },
        { event_type: 'websocket_open', transport: transport, url: safeUrl(url) },
        { request_id: requestId('auto-ws-open') }
      ).catch(function () {})
      emitProxyWebSocketEvent(ws, 'open', {})
    }, 0)
    return ws
  }

  function installWebSocketCapture() {
    if (typeof global.WebSocket !== 'function' || global.WebSocket.__skillforgeAutowireWrapped) return
    var NativeWebSocket = global.WebSocket
    function WrappedWebSocket(url, protocols) {
      if (shouldProxyAiProvider(url, 'POST')) {
        return createProxyWebSocket(url, protocols, 'websocket_ai_proxy')
      }
      if (shouldFallbackGeneratedSocketApi(url)) {
        return createProxyWebSocket(url, protocols, 'websocket_backend_fallback')
      }
      return protocols === undefined ? new NativeWebSocket(url) : new NativeWebSocket(url, protocols)
    }
    WrappedWebSocket.CONNECTING = NativeWebSocket.CONNECTING == null ? 0 : NativeWebSocket.CONNECTING
    WrappedWebSocket.OPEN = NativeWebSocket.OPEN == null ? 1 : NativeWebSocket.OPEN
    WrappedWebSocket.CLOSING = NativeWebSocket.CLOSING == null ? 2 : NativeWebSocket.CLOSING
    WrappedWebSocket.CLOSED = NativeWebSocket.CLOSED == null ? 3 : NativeWebSocket.CLOSED
    WrappedWebSocket.prototype = NativeWebSocket.prototype
    WrappedWebSocket.__skillforgeAutowireWrapped = true
    WrappedWebSocket.__skillforgeNativeWebSocket = NativeWebSocket
    global.WebSocket = WrappedWebSocket
  }

  function createProxyEventSource(url, options, transport) {
    var base = null
    try { base = typeof EventTarget === 'function' ? new EventTarget() : null } catch (_) {}
    var listeners = {}
    var es = base || {}
    if (!base) {
      es.addEventListener = function (type, listener) {
        if (typeof listener !== 'function') return
        listeners[type] = listeners[type] || []
        listeners[type].push(listener)
      }
      es.removeEventListener = function (type, listener) {
        listeners[type] = (listeners[type] || []).filter(function (item) { return item !== listener })
      }
      es.dispatchEvent = function (event) {
        ;(listeners[event.type] || []).slice().forEach(function (listener) {
          try { listener.call(es, event) } catch (error) { global.setTimeout(function () { throw error }, 0) }
        })
        return true
      }
    }
    setXhrProp(es, 'url', String(url || ''))
    setXhrProp(es, 'withCredentials', !!(options && options.withCredentials))
    setXhrProp(es, 'readyState', 0)
    setXhrProp(es, 'close', function () {
      if (es.readyState === 2) return
      setXhrProp(es, 'readyState', 2)
      emitProxyEventSourceEvent(es, 'error', { data: '', reason: 'closed' })
    })
    global.setTimeout(function () {
      if (es.readyState === 2) return
      setXhrProp(es, 'readyState', 1)
      emitProxyEventSourceEvent(es, 'open', {})
      if (transport === 'eventsource_ai_proxy') {
        var aiCaptureId = nextApiCaptureId('auto-ai-eventsource')
        var body = JSON.stringify({ prompt: promptFromUrlQuery(url), eventsource_url: safeUrl(url) })
        callAiProviderGateway(url, 'POST', body, aiCaptureId, transport).then(function (payload) {
          if (es.readyState === 2) return
          emitProxyEventSourceEvent(es, 'message', { data: eventSourceDataFromProxyPayload(url, payload), origin: safeUrl(url) })
        }).catch(function (error) {
          if (es.readyState === 2) return
          var errorText = JSON.stringify(providerErrorBody(error))
          captureApiOutput(aiCaptureId, transport, 'POST', url, 502, 'application/json', errorText, false)
          emitProxyEventSourceEvent(es, 'message', { data: errorText, origin: safeUrl(url) })
        })
        return
      }
      var captureId = nextApiCaptureId('auto-backend-eventsource')
      captureApiInput(captureId, transport, 'GET', url, null)
      global.setTimeout(function () {
        if (es.readyState === 2) return
        var payload = generatedBackendFallbackPayload(url, 'GET', null, null, null)
        var bodyText = JSON.stringify(payload)
        captureApiOutput(captureId, transport, 'GET', url, 200, 'application/json', bodyText, true)
        emitProxyEventSourceEvent(es, 'message', { data: bodyText, origin: safeUrl(url) })
      }, 0)
    }, 0)
    return es
  }

  function emitProxyEventSourceEvent(es, type, props) {
    props = props || {}
    var event = null
    try {
      if (type === 'message' && typeof global.MessageEvent === 'function') {
        event = new global.MessageEvent('message', { data: props.data || '', origin: props.origin || '' })
      } else {
        event = new Event(type)
      }
    } catch (_) {
      try {
        event = global.document && global.document.createEvent ? global.document.createEvent('Event') : null
        if (event && event.initEvent) event.initEvent(type, false, false)
      } catch (_) {}
    }
    if (!event) event = { type: type }
    Object.keys(props).forEach(function (key) {
      try { Object.defineProperty(event, key, { configurable: true, value: props[key] }) } catch (_) {
        try { event[key] = props[key] } catch (_) {}
      }
    })
    try {
      if (es.dispatchEvent && event instanceof Event) es.dispatchEvent(event)
    } catch (_) {}
    try {
      var handler = es && es['on' + type]
      if (typeof handler === 'function') handler.call(es, event)
    } catch (_) {}
  }

  function installEventSourceCapture() {
    if (typeof global.EventSource !== 'function' || global.EventSource.__skillforgeAutowireWrapped) return
    var NativeEventSource = global.EventSource
    function WrappedEventSource(url, options) {
      if (shouldProxyEventStreamAi(url)) {
        return createProxyEventSource(url, options, 'eventsource_ai_proxy')
      }
      if (shouldFallbackGeneratedBackendApi(url, 'GET') || shouldFallbackGeneratedSocketApi(url)) {
        return createProxyEventSource(url, options, 'eventsource_backend_fallback')
      }
      return new NativeEventSource(url, options)
    }
    WrappedEventSource.CONNECTING = NativeEventSource.CONNECTING == null ? 0 : NativeEventSource.CONNECTING
    WrappedEventSource.OPEN = NativeEventSource.OPEN == null ? 1 : NativeEventSource.OPEN
    WrappedEventSource.CLOSED = NativeEventSource.CLOSED == null ? 2 : NativeEventSource.CLOSED
    WrappedEventSource.prototype = NativeEventSource.prototype
    WrappedEventSource.__skillforgeAutowireWrapped = true
    WrappedEventSource.__skillforgeNativeEventSource = NativeEventSource
    global.EventSource = WrappedEventSource
  }

  function workerBootstrapSource(sourceUrl) {
    return [
      ';(function(){',
      'var AI_PROVIDER_URL_RE=/(?:https?|wss?):\\/\\/(?:api\\.openai\\.com\\/v1\\/(?:chat\\/completions|responses|completions|realtime)|api\\.deepseek\\.com\\/v1\\/chat\\/completions|api\\.anthropic\\.com\\/v1\\/messages|generativelanguage\\.googleapis\\.com\\/v1|dashscope\\.aliyuncs\\.com\\/(?:api\\/)?v1)/i;',
      'var LOCAL_AI_API_PATH_RE=/^\\/api\\/(?:(?:v1\\/)?(?:chat|messages|responses|completions)(?:\\/|$)|(?:ai|llm|openai|anthropic|gemini|deepseek|dashscope|assistant|agent)(?:\\/|$)|(?:generate|complete|completion|ask|analyze|analyse|summarize|summary|report)(?:\\/|$))/i;',
      'var PLATFORM_API_PATH_RE=/^\\/api\\/(?:projects|codex|auth|admin|system|settings|health|me)(?:\\/|$)/i;',
      'var pending={};',
      'function parseUrl(value){try{return new URL(String(value||""), ' + JSON.stringify((global.location && global.location.origin ? global.location.origin : '') + '/') + ')}catch(_){return null}}',
      'function shouldProxy(value,method){method=String(method||"GET").toUpperCase();var raw=String(value||"");if(method==="HEAD")return false;if(AI_PROVIDER_URL_RE.test(raw))return true;var u=parseUrl(raw);if(!u)return false;var path=u.pathname||"/";if(PLATFORM_API_PATH_RE.test(path))return false;if(LOCAL_AI_API_PATH_RE.test(path)&&method!=="GET")return true;if(/^\\/api\\//i.test(path))return true;return false}',
      'function rid(){return "sfw:"+Date.now().toString(36)+":"+Math.random().toString(36).slice(2)}',
      'function bodyText(body){if(body==null)return Promise.resolve(undefined);if(typeof body==="string")return Promise.resolve(body);try{if(typeof URLSearchParams!=="undefined"&&body instanceof URLSearchParams)return Promise.resolve(String(body));if(typeof FormData!=="undefined"&&body instanceof FormData){var o={};body.forEach(function(v,k){o[k]=v&&v.name?v.name:v});return Promise.resolve(JSON.stringify(o))}if(typeof Blob!=="undefined"&&body instanceof Blob)return body.text()}catch(_){}try{return Promise.resolve(JSON.stringify(body))}catch(_){return Promise.resolve(String(body))}}',
      'if(typeof self.fetch==="function"){var nativeFetch=self.fetch.bind(self);self.fetch=function(resource,init){var url=resource&&resource.url?resource.url:resource;var method=String((init&&init.method)||(resource&&resource.method)||"GET").toUpperCase();if(!shouldProxy(url,method))return nativeFetch(resource,init);var body=init&&Object.prototype.hasOwnProperty.call(init,"body")?init.body:undefined;return bodyText(body).then(function(text){var id=rid();return new Promise(function(resolve,reject){var timer=setTimeout(function(){delete pending[id];reject(new Error("SkillForge Worker fetch proxy timeout"))},180000);pending[id]={resolve:resolve,reject:reject,timer:timer};self.postMessage({__skillforge_worker_proxy:true,request_id:id,url:String(url||""),method:method,body:text})}).then(function(result){return new Response(result.body||"",{status:result.status||200,headers:result.headers||{"content-type":"application/json"}})})})}};',
      'self.addEventListener("message",function(event){var data=event.data||{};if(!data.__skillforge_worker_proxy_result)return;var item=pending[data.request_id];if(!item)return;if(event.stopImmediatePropagation)event.stopImmediatePropagation();clearTimeout(item.timer);delete pending[data.request_id];if(data.error){item.reject(new Error(String(data.error)))}else{item.resolve(data)}},true);',
      '})();',
    ].join('')
  }

  function workerWrapperUrl(sourceUrl, options) {
    if (!global.Blob || !global.URL || typeof global.URL.createObjectURL !== 'function') return ''
    var absolute = String(sourceUrl || '')
    try { if (!/^blob:/i.test(absolute)) absolute = new URL(absolute, global.location && global.location.href ? global.location.href : 'http://skillforge.local/').href } catch (_) {}
    var prefix = workerBootstrapSource(absolute)
    var isModule = options && String(options.type || '').toLowerCase() === 'module'
    var body = isModule
      ? prefix + '\nimport ' + JSON.stringify(absolute) + ';'
      : prefix + '\ntry{importScripts(' + JSON.stringify(absolute) + ')}catch(error){self.postMessage({__skillforge_worker_import_error:true,error:String(error&&error.message||error)})}'
    return global.URL.createObjectURL(new Blob([body], { type: 'text/javascript' }))
  }

  function postWorkerProxyResult(worker, requestId, status, headers, body, error) {
    try {
      worker.postMessage({
        __skillforge_worker_proxy_result: true,
        request_id: requestId,
        status: status,
        headers: headers || { 'content-type': 'application/json' },
        body: body || '',
        error: error || '',
      })
    } catch (_) {}
  }

  function handleWorkerProxyMessage(worker, event) {
    var data = event && event.data
    if (!data || data.__skillforge_worker_proxy !== true) return
    try { if (event.stopImmediatePropagation) event.stopImmediatePropagation() } catch (_) {}
    var workerRequestId = data.request_id || requestId('worker-fetch')
    var method = String(data.method || 'GET').toUpperCase()
    var url = data.url || ''
    var body = data.body
    if (shouldProxyAiProvider(url, method) || shouldProxyGeneratedAiApi(url, method)) {
      callAiProviderGateway(url, method, body, workerRequestId, 'worker_fetch_ai_proxy')
        .then(function (payload) {
          postWorkerProxyResult(worker, workerRequestId, payload.status, { 'content-type': payload.contentType, 'x-skillforge-ai-proxy': 'project-gateway' }, payload.body)
        })
        .catch(function (error) {
          var errorText = JSON.stringify(providerErrorBody(error))
          captureApiOutput(workerRequestId, 'worker_fetch_ai_proxy', method, url, 502, 'application/json', errorText, false)
          postWorkerProxyResult(worker, workerRequestId, 502, { 'content-type': 'application/json' }, errorText)
        })
      return
    }
    if (shouldFallbackGeneratedBackendApi(url, method)) {
      captureApiInput(workerRequestId, 'worker_fetch_backend_fallback', method, url, body)
      var payload = generatedBackendFallbackPayload(url, method, body, null, null)
      var bodyText = JSON.stringify(payload)
      captureApiOutput(workerRequestId, 'worker_fetch_backend_fallback', method, url, 200, 'application/json', bodyText, true)
      postWorkerProxyResult(worker, workerRequestId, 200, { 'content-type': 'application/json', 'x-skillforge-backend-fallback': 'project-gateway' }, bodyText)
      return
    }
    postWorkerProxyResult(worker, workerRequestId, 403, { 'content-type': 'application/json' }, JSON.stringify({ error: 'Worker proxy denied by SkillForge Project Gateway' }), 'denied')
  }

  function installWorkerCapture() {
    if (typeof global.Worker !== 'function' || global.Worker.__skillforgeAutowireWrapped) return
    var NativeWorker = global.Worker
    function WrappedWorker(url, options) {
      if (!shouldWrapGeneratedWorker(url)) {
        return new NativeWorker(url, options)
      }
      var wrappedUrl = workerWrapperUrl(url, options)
      var worker = wrappedUrl ? new NativeWorker(wrappedUrl, options) : new NativeWorker(url, options)
      try {
        worker.addEventListener('message', function (event) { handleWorkerProxyMessage(worker, event) }, true)
      } catch (_) {}
      return worker
    }
    WrappedWorker.prototype = NativeWorker.prototype
    WrappedWorker.__skillforgeAutowireWrapped = true
    WrappedWorker.__skillforgeNativeWorker = NativeWorker
    global.Worker = WrappedWorker
  }

  function serviceWorkerFallbackRegistration(url, options) {
    var scope = ''
    try {
      if (options && options.scope) scope = new URL(options.scope, global.location && global.location.href ? global.location.href : 'http://skillforge.local/').href
      else {
        var parsed = new URL(String(url || ''), global.location && global.location.href ? global.location.href : 'http://skillforge.local/')
        scope = parsed.href.slice(0, parsed.href.lastIndexOf('/') + 1)
      }
    } catch (_) {
      scope = safeUrl(url)
    }
    var target = null
    try { target = typeof EventTarget === 'function' ? new EventTarget() : null } catch (_) {}
    var registration = target || {}
    if (!target) {
      var listeners = {}
      registration.addEventListener = function (type, listener) {
        if (typeof listener !== 'function') return
        listeners[type] = listeners[type] || []
        listeners[type].push(listener)
      }
      registration.removeEventListener = function (type, listener) {
        listeners[type] = (listeners[type] || []).filter(function (item) { return item !== listener })
      }
      registration.dispatchEvent = function (event) {
        ;(listeners[event.type] || []).slice().forEach(function (listener) {
          try { listener.call(registration, event) } catch (error) { global.setTimeout(function () { throw error }, 0) }
        })
        return true
      }
    }
    setXhrProp(registration, 'installing', null)
    setXhrProp(registration, 'waiting', null)
    setXhrProp(registration, 'active', null)
    setXhrProp(registration, 'scope', scope)
    setXhrProp(registration, 'updateViaCache', 'none')
    setXhrProp(registration, 'navigationPreload', {
      enable: function () { return Promise.resolve() },
      disable: function () { return Promise.resolve() },
      setHeaderValue: function () { return Promise.resolve() },
      getState: function () { return Promise.resolve({ enabled: false, headerValue: '' }) },
    })
    setXhrProp(registration, 'unregister', function () { return Promise.resolve(true) })
    setXhrProp(registration, 'update', function () { return Promise.resolve(registration) })
    return registration
  }

  function serviceWorkerRegistrationPayload(url, options, registration) {
    return {
      ok: true,
      summary: '项目 Service Worker 已由 SkillForge 安全接管，不允许控制平台宿主页面。',
      script_url: safeUrl(url),
      scope: registration && registration.scope,
      options: redactDeep(options || {}, 3),
      skillforge: {
        proxied_by: 'Project Gateway',
        generated_service_worker_fallback: true,
        reason: 'sandboxed_project_runtime',
      },
    }
  }

  function installServiceWorkerCapture() {
    var nav = global.navigator
    var container = nav && nav.serviceWorker
    if (!container || typeof container.register !== 'function' || container.register.__skillforgeAutowireWrapped) return
    var nativeRegister = container.register.bind(container)
    var nativeGetRegistration = typeof container.getRegistration === 'function' ? container.getRegistration.bind(container) : null
    var nativeGetRegistrations = typeof container.getRegistrations === 'function' ? container.getRegistrations.bind(container) : null
    var fallbackRegistrations = []
    function wrappedRegister(url, options) {
      if (!shouldCaptureGeneratedServiceWorker(url)) return nativeRegister(url, options)
      var captureId = nextApiCaptureId('auto-service-worker')
      captureApiInput(captureId, 'serviceworker_fallback', 'REGISTER', url, { options: options || {} })
      var registration = serviceWorkerFallbackRegistration(url, options || {})
      fallbackRegistrations.push(registration)
      var bodyText = JSON.stringify(serviceWorkerRegistrationPayload(url, options || {}, registration))
      captureApiOutput(captureId, 'serviceworker_fallback', 'REGISTER', url, 200, 'application/json', bodyText, true)
      return Promise.resolve(registration)
    }
    wrappedRegister.__skillforgeAutowireWrapped = true
    wrappedRegister.__skillforgeNativeRegister = nativeRegister
    try { container.register = wrappedRegister } catch (_) {
      try { Object.defineProperty(container, 'register', { configurable: true, writable: true, value: wrappedRegister }) } catch (_) {}
    }
    if (nativeGetRegistration) {
      try {
        container.getRegistration = function (scope) {
          if (fallbackRegistrations.length) return Promise.resolve(fallbackRegistrations[fallbackRegistrations.length - 1])
          return nativeGetRegistration(scope)
        }
      } catch (_) {}
    }
    if (nativeGetRegistrations) {
      try {
        container.getRegistrations = function () {
          if (fallbackRegistrations.length) return Promise.resolve(fallbackRegistrations.slice())
          return nativeGetRegistrations()
        }
      } catch (_) {}
    }
  }

  function storageAreaName(storage) {
    try {
      if (storage === global.localStorage) return 'localStorage'
      if (storage === global.sessionStorage) return 'sessionStorage'
    } catch (_) {}
    return 'Storage'
  }

  function storagePayload(area, op, key, value) {
    return {
      event: 'storage_mutation',
      storage: area,
      operation: op,
      key: compactValue(key, 240),
      value: op === 'setItem' ? safeValue(String(key || ''), parseMaybeJson(String(value == null ? '' : value))) : undefined,
      page: pageSnapshot(),
    }
  }

  function shouldCaptureStorageMutation(area, op, key, value) {
    if (runtimeCaptureCount >= MAX_RUNTIME_CAPTURE_EVENTS) return false
    var textValue = String(key || '') + ' ' + String(value == null ? '' : value).slice(0, 1200)
    if (!textValue.trim()) return false
    if (/token|cookie|password|secret|api[_-]?key/i.test(String(key || ''))) return false
    var fingerprint = hashText(area + ':' + op + ':' + textValue)
    if (fingerprint === lastStorageHash) return false
    lastStorageHash = fingerprint
    return true
  }

  function captureStorageMutation(area, op, key, value) {
    if (!shouldCaptureStorageMutation(area, op, key, value)) return
    var captureId = nextRuntimeCaptureId('auto-storage')
    var payload = storagePayload(area, op, key, value)
    recordInput(
      payload,
      { event_type: 'storage_mutation', transport: area, operation: op, key: compactValue(key, 160) },
      { request_id: captureId + ':input' }
    ).catch(function () {})
    if (op === 'setItem') {
      ingest(
        {
          summary: '项目本地状态写入：' + area + '.' + op + '(' + String(key || '').slice(0, 80) + ')',
          event: 'storage_output',
          storage: payload,
        },
        {
          request_id: captureId + ':output',
          metadata: { source: 'project_autowire', event_type: 'storage_output', transport: area, operation: op },
          auto_analyze: false,
        }
      ).catch(function () {})
    }
  }

  function installStorageCapture() {
    if (!global.Storage || !global.Storage.prototype || global.Storage.prototype.__skillforgeAutowireWrapped) return
    var proto = global.Storage.prototype
    var nativeSetItem = proto.setItem
    var nativeRemoveItem = proto.removeItem
    var nativeClear = proto.clear
    if (typeof nativeSetItem === 'function') {
      proto.setItem = function (key, value) {
        var result = nativeSetItem.apply(this, arguments)
        try { captureStorageMutation(storageAreaName(this), 'setItem', key, value) } catch (_) {}
        return result
      }
    }
    if (typeof nativeRemoveItem === 'function') {
      proto.removeItem = function (key) {
        var result = nativeRemoveItem.apply(this, arguments)
        try { captureStorageMutation(storageAreaName(this), 'removeItem', key, null) } catch (_) {}
        return result
      }
    }
    if (typeof nativeClear === 'function') {
      proto.clear = function () {
        var area = storageAreaName(this)
        var result = nativeClear.apply(this, arguments)
        try { captureStorageMutation(area, 'clear', '*', null) } catch (_) {}
        return result
      }
    }
    proto.__skillforgeAutowireWrapped = true
  }

  function indexedDbStoreName(store) {
    try { return compactValue(store && store.name, 160) } catch (_) {}
    return ''
  }

  function indexedDbPayload(store, op, value, key) {
    return {
      event: 'indexeddb_mutation',
      storage: 'IndexedDB',
      operation: op,
      store: indexedDbStoreName(store),
      key: key == null ? undefined : redactDeep(key, 2),
      value: value === undefined ? undefined : redactDeep(value, 4),
      page: pageSnapshot(),
    }
  }

  function captureIndexedDbMutation(store, op, value, key) {
    if (runtimeCaptureCount >= MAX_RUNTIME_CAPTURE_EVENTS) return
    var captureId = nextRuntimeCaptureId('auto-indexeddb')
    var payload = indexedDbPayload(store, op, value, key)
    recordInput(
      payload,
      { event_type: 'indexeddb_mutation', transport: 'IndexedDB', operation: op, store: payload.store },
      { request_id: captureId + ':input' }
    ).catch(function () {})
    if (op === 'put' || op === 'add') {
      ingest(
        {
          summary: '项目 IndexedDB 输出写入：' + (payload.store || 'objectStore') + '.' + op,
          event: 'indexeddb_output',
          indexeddb: payload,
        },
        {
          request_id: captureId + ':output',
          metadata: { source: 'project_autowire', event_type: 'indexeddb_output', transport: 'IndexedDB', operation: op, store: payload.store },
          auto_analyze: false,
        }
      ).catch(function () {})
    }
  }

  function installIndexedDbCapture() {
    if (!global.IDBObjectStore || !global.IDBObjectStore.prototype || global.IDBObjectStore.prototype.__skillforgeAutowireWrapped) return
    var proto = global.IDBObjectStore.prototype
    var nativePut = proto.put
    var nativeAdd = proto.add
    var nativeDelete = proto.delete
    var nativeClear = proto.clear
    if (typeof nativePut === 'function') {
      proto.put = function (value, key) {
        var result = nativePut.apply(this, arguments)
        try { captureIndexedDbMutation(this, 'put', value, key) } catch (_) {}
        return result
      }
    }
    if (typeof nativeAdd === 'function') {
      proto.add = function (value, key) {
        var result = nativeAdd.apply(this, arguments)
        try { captureIndexedDbMutation(this, 'add', value, key) } catch (_) {}
        return result
      }
    }
    if (typeof nativeDelete === 'function') {
      proto.delete = function (key) {
        var result = nativeDelete.apply(this, arguments)
        try { captureIndexedDbMutation(this, 'delete', undefined, key) } catch (_) {}
        return result
      }
    }
    if (typeof nativeClear === 'function') {
      proto.clear = function () {
        var result = nativeClear.apply(this, arguments)
        try { captureIndexedDbMutation(this, 'clear', undefined, undefined) } catch (_) {}
        return result
      }
    }
    proto.__skillforgeAutowireWrapped = true
  }

  function runtimeErrorPayload(kind, error, event) {
    var message = ''
    var stack = ''
    try {
      if (error && typeof error === 'object') {
        message = String(error.message || error.reason || '')
        stack = compactValue(error.stack || '', 2000)
      }
      if (!message && event) message = String(event.message || event.reason || event.error || '')
    } catch (_) {}
    return {
      event: kind,
      message: compactValue(message || '项目运行错误', 1000),
      stack: stack,
      page: pageSnapshot(),
    }
  }

  function captureRuntimeError(kind, error, event) {
    if (runtimeCaptureCount >= MAX_RUNTIME_CAPTURE_EVENTS) return
    var captureId = nextRuntimeCaptureId('auto-runtime-error')
    var payload = runtimeErrorPayload(kind, error, event)
    ingest(
      {
        summary: '项目运行异常：' + payload.message,
        event: kind,
        runtime_error: payload,
      },
      {
        request_id: captureId,
        metadata: { source: 'project_autowire', event_type: kind },
        auto_analyze: false,
      }
    ).catch(function () {})
  }

  function installRuntimeErrorCapture() {
    if (global.__SKILLFORGE_PROJECT_RUNTIME_ERROR_CAPTURE__) return
    global.__SKILLFORGE_PROJECT_RUNTIME_ERROR_CAPTURE__ = true
    try {
      global.addEventListener('error', function (event) {
        captureRuntimeError('runtime_error', event && (event.error || event.message), event)
      }, true)
      global.addEventListener('unhandledrejection', function (event) {
        captureRuntimeError('runtime_unhandled_rejection', event && event.reason, event)
      }, true)
    } catch (_) {}
  }

  function pageSnapshot() {
    var bodyText = ''
    try {
      if (global.document && global.document.body) {
        bodyText = text(global.document.body.innerText || global.document.body.textContent || '', 1200)
      }
    } catch (_) {}
    return {
      title: global.document ? text(global.document.title, 160) : '',
      url: global.location ? String(global.location.pathname || '') + String(global.location.search || '') : '',
      visible_text: bodyText,
    }
  }

  function formSnapshot(form) {
    var data = {}
    if (!form || typeof global.FormData !== 'function') return data
    try {
      var fd = new global.FormData(form)
      fd.forEach(function (value, key) {
        var safe = safeValue(key, value && value.name ? value.name : value)
        if (Object.prototype.hasOwnProperty.call(data, key)) {
          if (!Array.isArray(data[key])) data[key] = [data[key]]
          data[key].push(safe)
        } else {
          data[key] = safe
        }
      })
    } catch (_) {}
    return data
  }

  function ready() {
    if (gatewayReady) return gatewayReady
    gatewayReady = new Promise(function (resolve, reject) {
      var started = Date.now()
      function tryReady() {
        var g = gateway()
        if (!g || typeof g.ready !== 'function') {
          if (Date.now() - started > 15000) return reject(new Error('Project Gateway SDK 未加载'))
          global.setTimeout(tryReady, 80)
          return
        }
        g.ready({ payload: { source: 'project_autowire' } })
          .then(function (ctx) {
            if (!heartbeatStop && typeof g.startHeartbeat === 'function') {
              heartbeatStop = g.startHeartbeat({
                intervalMs: 25000,
                payload: function () {
                  return { source: 'project_autowire', visible: !global.document || !global.document.hidden }
                },
              })
            }
            resolve(ctx)
          })
          .catch(reject)
      }
      tryReady()
    })
    return gatewayReady
  }

  function recordInput(input, meta, options) {
    options = options || {}
    return ready().then(function () {
      return gateway().input({
        request_id: options.request_id,
        input: input || {},
        metadata: Object.assign({ source: 'project_autowire' }, meta || {}),
      }, { requestId: options.request_id || requestId('auto-input') })
    })
  }

  function ingest(output, options) {
    options = options || {}
    return ready().then(function () {
      return gateway().ingest({
        request_id: options.request_id,
        input: options.input || {},
        output: output || {},
        reports: Array.isArray(options.reports) ? options.reports : [],
        todos: Array.isArray(options.todos) ? options.todos : [],
        proofs: Array.isArray(options.proofs) ? options.proofs : [],
        metadata: Object.assign({ source: 'project_autowire' }, options.metadata || {}),
        auto_analyze: options.auto_analyze !== false,
      }, { requestId: options.request_id || requestId('auto-output') })
    })
  }

  function scheduleOutputSnapshot(reason, input, delayMs) {
    var eventId = requestId('auto-output-snapshot')
    global.setTimeout(function () {
      var snapshot = pageSnapshot()
      var fingerprint = hashText(reason + ':' + snapshot.title + ':' + snapshot.visible_text)
      if (fingerprint === lastOutputHash) return
      lastOutputHash = fingerprint
      ingest(
        {
          summary: snapshot.visible_text || snapshot.title || '项目页面输出快照',
          event: reason || 'page_snapshot',
          page: snapshot,
        },
        {
          request_id: eventId,
          input: input || {},
          metadata: { source: 'project_autowire', event_type: reason || 'page_snapshot' },
          auto_analyze: true,
        }
      ).catch(function () {})
    }, typeof delayMs === 'number' ? delayMs : 700)
  }

  function report(title, summary, extra) {
    extra = extra || {}
    return ingest(
      Object.assign({ summary: summary || title || '项目报告' }, extra.output || {}),
      Object.assign({}, extra, {
        reports: [{
          title: title || '项目报告',
          summary: summary || '',
          metadata: { source: 'project_autowire' },
        }].concat(extra.reports || []),
      })
    )
  }

  function todo(title, detail, extra) {
    extra = extra || {}
    return ingest(
      Object.assign({ summary: detail || title || '项目待办' }, extra.output || {}),
      Object.assign({}, extra, {
        todos: [{
          title: title || '项目待办',
          detail: detail || '',
          metadata: { source: 'project_autowire' },
        }].concat(extra.todos || []),
      })
    )
  }

  function ai(prompt, input, options) {
    options = options || {}
    return ready().then(function () {
      return gateway().ai(prompt || '请分析当前项目页面。', input || pageSnapshot(), options)
    })
  }

  function handleSubmit(event) {
    var form = event.target
    var input = { event: 'form_submit', form: formSnapshot(form), page: pageSnapshot() }
    recordInput(
      input,
      { event_type: 'form_submit' },
      { request_id: requestId('auto-form') }
    ).catch(function () {})
    scheduleOutputSnapshot('form_submit_output', input, 900)
  }

  function closestDataElement(target) {
    var el = target && target.nodeType === 1 ? target : target && target.parentElement
    while (el && el !== global.document.documentElement) {
      if (
        el.hasAttribute &&
        (el.hasAttribute('data-sf-input') || el.hasAttribute('data-sf-output') ||
          el.hasAttribute('data-sf-report') || el.hasAttribute('data-sf-todo') ||
          el.hasAttribute('data-sf-ai'))
      ) return el
      el = el.parentElement
    }
    return null
  }

  function closestActionElement(target) {
    var el = target && target.nodeType === 1 ? target : target && target.parentElement
    while (el && el !== global.document.documentElement) {
      if (el.matches && el.matches('button,input[type="button"],input[type="submit"],input[type="reset"],[role="button"],a[role="button"]')) return el
      el = el.parentElement
    }
    return null
  }

  function handleClick(event) {
    var el = closestDataElement(event.target)
    var isDataAction = !!el
    if (!el) el = closestActionElement(event.target)
    if (!el) return
    var label = text(el.getAttribute('data-sf-label') || el.textContent || el.value || el.getAttribute('aria-label') || '项目操作', 160)
    var input = { event: 'click', label: label, page: pageSnapshot() }
    if (isDataAction && el.hasAttribute('data-sf-ai')) {
      ai(el.getAttribute('data-sf-ai') || ('请分析项目操作：' + label), input).catch(function () {})
    } else if (isDataAction && el.hasAttribute('data-sf-report')) {
      report(label, el.getAttribute('data-sf-report') || label, { input: input }).catch(function () {})
    } else if (isDataAction && el.hasAttribute('data-sf-todo')) {
      todo(label, el.getAttribute('data-sf-todo') || label, { input: input }).catch(function () {})
    } else if (isDataAction && el.hasAttribute('data-sf-output')) {
      ingest({ summary: el.getAttribute('data-sf-output') || label, page: pageSnapshot() }, { input: input }).catch(function () {})
    } else {
      recordInput(input, { event_type: isDataAction ? 'click' : 'action_click' }, { request_id: requestId('auto-click') }).catch(function () {})
      scheduleOutputSnapshot(isDataAction ? 'click_output' : 'action_click_output', input, 900)
    }
  }

  function boot() {
    installApiCapture()
    ready()
      .then(function () {
        var input = { event: 'page_loaded', page: pageSnapshot() }
        scheduleOutputSnapshot('page_loaded_output', input, 900)
        return recordInput(input, { event_type: 'page_loaded' }, { request_id: requestId('auto-load') })
      })
      .catch(function () {})
    if (global.document) {
      global.document.addEventListener('submit', handleSubmit, true)
      global.document.addEventListener('click', handleClick, true)
    }
    global.addEventListener('pagehide', function () {
      try {
        if (gateway() && typeof gateway().close === 'function') gateway().close({ source: 'project_autowire_pagehide' }, { timeoutMs: 0 })
      } catch (_) {}
    })
  }

  installApiCapture()

  global.SkillForgeProjectBridge = {
    ready: ready,
    input: recordInput,
    recordInput: recordInput,
    output: ingest,
    ingest: ingest,
    report: report,
    todo: todo,
    ai: ai,
    autoOutput: scheduleOutputSnapshot,
    flushOutput: scheduleOutputSnapshot,
    snapshot: pageSnapshot,
    installApiCapture: installApiCapture,
  }

  if (global.document && global.document.readyState === 'loading') {
    global.document.addEventListener('DOMContentLoaded', boot, { once: true })
  } else {
    global.setTimeout(boot, 0)
  }
})(window)
