(function (global) {
  'use strict'

  var REQUEST_TYPES = {
    context: 'skillforge.project.context.request',
    ready: 'skillforge.project.ready',
    heartbeat: 'skillforge.project.heartbeat',
    close: 'skillforge.project.close',
    input: 'skillforge.project.input',
    ingest: 'skillforge.project.ingest',
    analyze: 'skillforge.project.analyze',
    capability: 'skillforge.project.capability',
    assetsList: 'skillforge.project.assets.list',
    assetsUpload: 'skillforge.project.assets.upload',
    mediaSubscribe: 'skillforge.project.media.subscribe',
    mediaUnsubscribe: 'skillforge.project.media.unsubscribe',
    ai: 'skillforge.project.ai',
  }

  var RESPONSE_TYPES = {
    heartbeat: 'skillforge.project.heartbeat.result',
    close: 'skillforge.project.close.result',
    input: 'skillforge.project.input.result',
    ingest: 'skillforge.project.ingest.result',
    analyze: 'skillforge.project.analyze.result',
    capability: 'skillforge.project.capability.result',
    assetsList: 'skillforge.project.assets.list.result',
    assetsUpload: 'skillforge.project.assets.upload.result',
    mediaSubscribe: 'skillforge.project.media.subscribe.result',
    mediaUnsubscribe: 'skillforge.project.media.unsubscribe.result',
    ai: 'skillforge.project.capability.result',
  }

  var DEFAULT_TIMEOUT_MS = 180000
  var DEFAULT_READY_TIMEOUT_MS = 15000
  var DEFAULT_HEARTBEAT_INTERVAL_MS = 25000
  var pending = new Map()
  var contextWaiters = []
  var contextListeners = new Set()
  var mediaListeners = new Map()
  var mediaEventBacklog = new Map()
  var currentContext = null
  var currentGatewayToken = ''
  var heartbeatTimer = null

  function nowId() {
    if (global.crypto && typeof global.crypto.randomUUID === 'function') return global.crypto.randomUUID()
    return 'pgw_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2)
  }

  function getParentWindow() {
    if (global.__PROJECT_GATEWAY_PARENT__ && typeof global.__PROJECT_GATEWAY_PARENT__.postMessage === 'function') {
      return global.__PROJECT_GATEWAY_PARENT__
    }
    if (global.parent && global.parent !== global && typeof global.parent.postMessage === 'function') {
      return global.parent
    }
    return null
  }

  function postToGateway(message) {
    var parentWindow = getParentWindow()
    if (!parentWindow) {
      throw new Error('Project Gateway 只在平台项目运行框架内可用')
    }
    parentWindow.postMessage(message, '*')
  }

  function normalizeTimeout(value, fallback) {
    if (value === false || value === 0) return 0
    var parsed = Number(value)
    if (Number.isFinite(parsed) && parsed > 0) return parsed
    return fallback
  }

  function settlePending(requestId, data) {
    var waiter = pending.get(requestId)
    if (!waiter) return false
    if (waiter.responseType && data.type !== waiter.responseType) return false
    pending.delete(requestId)
    if (waiter.timer) global.clearTimeout(waiter.timer)
    if (data.ok === false) {
      var error = new Error(String(data.error || 'Project Gateway 调用失败'))
      error.gatewayResponse = data
      waiter.reject(error)
      return true
    }
    waiter.resolve(data.result !== undefined ? data.result : data)
    return true
  }

  function pickGatewayToken(data, payload) {
    data = data || {}
    payload = payload || data.payload || {}
    var gateway = data.gateway || {}
    return String(
      data.gateway_token ||
      data.gatewayToken ||
      gateway.session_token ||
      gateway.gateway_token ||
      gateway.gatewayToken ||
      payload.gateway_token ||
      payload.gatewayToken ||
      ''
    )
  }

  function notifyContext(data) {
    currentContext = data
    currentGatewayToken = pickGatewayToken(data) || currentGatewayToken
    contextListeners.forEach(function (listener) {
      try {
        listener(data)
      } catch (error) {
        global.setTimeout(function () { throw error }, 0)
      }
    })
    var waiters = contextWaiters.splice(0)
    waiters.forEach(function (waiter) {
      if (waiter.timer) global.clearTimeout(waiter.timer)
      waiter.resolve(data)
    })
  }

  function isGatewayMessage(event) {
    var parentWindow = getParentWindow()
    if (!parentWindow || !event.source) return true
    return event.source === parentWindow
  }

  function handleMessage(event) {
    if (!isGatewayMessage(event)) return
    var data = event.data || {}
    if (!data || typeof data !== 'object' || !data.type) return
    if (data.type === 'skillforge.project.context') {
      notifyContext(data)
      return
    }
    if (data.type === 'skillforge.project.media.event') {
      var mediaListener = mediaListeners.get(String(data.subscription_id || ''))
      if (mediaListener) {
        try { mediaListener(data.event || 'message', data.data, data) } catch (error) {
          global.setTimeout(function () { throw error }, 0)
        }
      } else if (data.subscription_id) {
        var backlogId = String(data.subscription_id)
        var backlog = mediaEventBacklog.get(backlogId) || []
        backlog.push(data)
        mediaEventBacklog.set(backlogId, backlog.slice(-20))
      }
      return
    }
    var requestId = data.request_id || data.requestId
    if (data.type === 'skillforge.project.assets.upload.progress' && requestId) {
      var uploadWaiter = pending.get(requestId)
      if (uploadWaiter && typeof uploadWaiter.onProgress === 'function') {
        try { uploadWaiter.onProgress(data.progress || data) } catch (error) {
          global.setTimeout(function () { throw error }, 0)
        }
      }
      return
    }
    if (requestId) settlePending(requestId, data)
  }

  function requestContext(options) {
    options = options || {}
    var requestId = options.requestId || options.request_id || nowId()
    var payload = options.payload || { source: 'project_gateway_sdk' }
    var message = {
      type: options.ready ? REQUEST_TYPES.ready : REQUEST_TYPES.context,
      request_id: requestId,
      payload: payload,
    }
    var token = pickGatewayToken(options, payload) || currentGatewayToken
    if (token) message.gateway_token = token
    postToGateway(message)
    return requestId
  }

  function ready(options) {
    options = options || {}
    if (currentContext && !options.force) return Promise.resolve(currentContext)
    return new Promise(function (resolve, reject) {
      var timeoutMs = normalizeTimeout(options.timeoutMs, DEFAULT_READY_TIMEOUT_MS)
      var waiter = { resolve: resolve, reject: reject, timer: null }
      if (timeoutMs) {
        waiter.timer = global.setTimeout(function () {
          contextWaiters = contextWaiters.filter(function (item) { return item !== waiter })
          reject(new Error('等待 Project Gateway 上下文超时'))
        }, timeoutMs)
      }
      contextWaiters.push(waiter)
      try {
        requestContext(options)
      } catch (error) {
        if (waiter.timer) global.clearTimeout(waiter.timer)
        contextWaiters = contextWaiters.filter(function (item) { return item !== waiter })
        reject(error)
      }
    })
  }

  function send(kind, payload, options) {
    payload = payload || {}
    options = options || {}
    var type = REQUEST_TYPES[kind] || kind
    var responseType = options.responseType || RESPONSE_TYPES[kind] || (type + '.result')
    var requestId = options.requestId || options.request_id || payload.request_id || payload.requestId || nowId()
    var timeoutMs = normalizeTimeout(options.timeoutMs, DEFAULT_TIMEOUT_MS)
    var message = {
      type: type,
      request_id: requestId,
      payload: payload,
    }
    var gatewayToken = pickGatewayToken(options, payload) || currentGatewayToken
    if (gatewayToken) message.gateway_token = gatewayToken
    return new Promise(function (resolve, reject) {
      var timer = timeoutMs ? global.setTimeout(function () {
        pending.delete(requestId)
        reject(new Error('Project Gateway 调用超时: ' + type))
      }, timeoutMs) : null
      pending.set(requestId, {
        resolve: resolve,
        reject: reject,
        timer: timer,
        responseType: responseType,
        onProgress: options.onProgress,
      })
      try {
        postToGateway(message)
      } catch (error) {
        pending.delete(requestId)
        if (timer) global.clearTimeout(timer)
        reject(error)
      }
    })
  }

  function heartbeat(payload, options) {
    return send('heartbeat', payload || { source: 'project_gateway_sdk', visible: !global.document || !global.document.hidden }, options)
  }

  function close(payload, options) {
    stopHeartbeat()
    return send('close', payload || { source: 'project_gateway_sdk' }, options)
  }

  function ingest(payload, options) {
    return send('ingest', payload, options)
  }

  function input(payload, options) {
    return send('input', payload, options)
  }

  function analyze(payload, options) {
    return send('analyze', payload || {}, options)
  }

  function capability(payload, options) {
    return send('capability', payload, options)
  }

  function listAssets(payload, options) {
    return send('assetsList', payload || {}, options)
  }

  function normalizeAssetUploadPayload(payload) {
    payload = payload || {}
    if (typeof File !== 'undefined' && payload instanceof File) payload = { file: payload }
    else if (typeof Blob !== 'undefined' && payload instanceof Blob) payload = { file: payload }
    else payload = Object.assign({}, payload)
    var file = payload.file || payload.asset || payload.blob || null
    if (file) payload.file = file
    if (!payload.file_name && file && file.name) payload.file_name = file.name
    if (!payload.mime_type && file && file.type) payload.mime_type = file.type
    return payload
  }

  function uploadAsset(payload, options) {
    return send('assetsUpload', normalizeAssetUploadPayload(payload), options)
  }

  function subscribeMedia(listener, options) {
    options = options || {}
    if (typeof listener !== 'function') return Promise.reject(new Error('media.subscribe 需要事件监听函数'))
    var payload = { after_cursor: String(options.afterCursor || options.after_cursor || '') }
    return send('mediaSubscribe', payload, options).then(function (result) {
      var subscriptionId = String(result && result.subscription_id || '')
      if (!subscriptionId) throw new Error('媒体事件订阅未返回 subscription_id')
      mediaListeners.set(subscriptionId, listener)
      ;(mediaEventBacklog.get(subscriptionId) || []).forEach(function (data) {
        listener(data.event || 'message', data.data, data)
      })
      mediaEventBacklog.delete(subscriptionId)
      return function unsubscribe() {
        mediaListeners.delete(subscriptionId)
        mediaEventBacklog.delete(subscriptionId)
        return send('mediaUnsubscribe', { subscription_id: subscriptionId }, { timeoutMs: 10000 }).catch(function () {})
      }
    })
  }

  function runAssetEndpointInfo(resource) {
    var raw = resource && resource.url ? resource.url : resource
    if (!raw) return null
    try {
      var parsed = new URL(String(raw), global.location && global.location.href ? global.location.href : 'http://skillforge.local/')
      var match = (parsed.pathname || '').match(/^\/api\/projects\/runs\/([^/]+)\/assets\/?$/)
      if (!match) return null
      return { runId: decodeURIComponent(match[1]), path: parsed.pathname || '' }
    } catch (_) {
      return null
    }
  }

  function parseAssetMetadata(value) {
    if (!value) return {}
    if (typeof value === 'object') return value
    try {
      var parsed = JSON.parse(String(value))
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
    } catch (_) {
      return { raw_metadata_json: String(value).slice(0, 1000) }
    }
  }

  function assetUploadPayloadFromBody(body, runId) {
    if (typeof FormData !== 'undefined' && body instanceof FormData) {
      var file = null
      var metadata = {}
      try { file = body.get('file') || body.get('asset') || body.get('blob') || null } catch (_) {}
      try { metadata = parseAssetMetadata(body.get('metadata_json') || body.get('metadataJson') || body.get('metadata')) } catch (_) {}
      return normalizeAssetUploadPayload({ run_id: runId, file: file, metadata: metadata })
    }
    if (typeof File !== 'undefined' && body instanceof File) return normalizeAssetUploadPayload({ run_id: runId, file: body })
    if (typeof Blob !== 'undefined' && body instanceof Blob) return normalizeAssetUploadPayload({ run_id: runId, file: body })
    if (body && typeof body === 'object') return normalizeAssetUploadPayload(Object.assign({ run_id: runId }, body))
    return normalizeAssetUploadPayload({ run_id: runId })
  }

  function assetUploadPayloadFromFetch(resource, init, info) {
    if (init && Object.prototype.hasOwnProperty.call(init, 'body')) {
      return Promise.resolve(assetUploadPayloadFromBody(init.body, info.runId))
    }
    if (resource && typeof resource.clone === 'function') {
      try {
        var clone = resource.clone()
        if (clone && typeof clone.formData === 'function') {
          return clone.formData().then(function (form) { return assetUploadPayloadFromBody(form, info.runId) })
        }
      } catch (_) {}
    }
    return Promise.resolve(assetUploadPayloadFromBody(null, info.runId))
  }

  function jsonFetchResponse(payload, status, headers) {
    return new Response(JSON.stringify(payload || {}), {
      status: status || 200,
      headers: Object.assign({ 'content-type': 'application/json' }, headers || {}),
    })
  }

  function ensureReadyForFetchBridge() {
    if (currentContext) return Promise.resolve(currentContext)
    return ready({ timeoutMs: 10000 }).catch(function () { return null })
  }

  function proxyAssetUploadFetch(resource, init, info) {
    return assetUploadPayloadFromFetch(resource, init, info).then(function (payload) {
      if (!payload.file) throw new Error('Project Gateway 资产上传缺少 file 字段')
      return ensureReadyForFetchBridge().then(function () {
        return uploadAsset(payload, { requestId: nowId(), timeoutMs: DEFAULT_TIMEOUT_MS })
      })
    }).then(function (result) {
      return jsonFetchResponse(result, result && result.ok === false ? 400 : 200, { 'x-skillforge-asset-upload': 'project-gateway' })
    }).catch(function (error) {
      return jsonFetchResponse(
        { ok: false, error: String(error && (error.message || error) || 'Project Gateway 资产上传失败') },
        502,
        { 'x-skillforge-asset-upload': 'project-gateway' }
      )
    })
  }

  function proxyAssetListFetch(info) {
    return ensureReadyForFetchBridge().then(function () {
      return listAssets({ run_id: info.runId }, { requestId: nowId(), timeoutMs: DEFAULT_TIMEOUT_MS })
    }).then(function (result) {
      return jsonFetchResponse(result, result && result.ok === false ? 400 : 200, { 'x-skillforge-assets-list': 'project-gateway' })
    }).catch(function (error) {
      return jsonFetchResponse(
        { ok: false, error: String(error && (error.message || error) || 'Project Gateway 资产列表读取失败') },
        502,
        { 'x-skillforge-assets-list': 'project-gateway' }
      )
    })
  }

  function installAssetFetchBridge() {
    if (typeof global.fetch !== 'function' || global.fetch.__skillforgeAssetFetchBridge) return
    var nativeFetch = global.fetch.bind(global)
    function wrappedFetch(resource, init) {
      var info = runAssetEndpointInfo(resource)
      if (!info || !getParentWindow()) return nativeFetch(resource, init)
      var method = String((init && init.method) || (resource && resource.method) || 'GET').toUpperCase()
      if (method === 'POST') return proxyAssetUploadFetch(resource, init, info)
      if (method === 'GET') return proxyAssetListFetch(info)
      return nativeFetch(resource, init)
    }
    wrappedFetch.__skillforgeAssetFetchBridge = true
    wrappedFetch.__skillforgeNativeFetch = nativeFetch
    global.fetch = wrappedFetch
  }

  function ai(promptOrPayload, input, options) {
    var payload = typeof promptOrPayload === 'object' && promptOrPayload !== null
      ? Object.assign({ capability: 'ai.generate' }, promptOrPayload)
      : { capability: 'ai.generate', prompt: String(promptOrPayload || ''), input: input || {} }
    return capability(payload, options)
  }

  function startHeartbeat(options) {
    options = options || {}
    stopHeartbeat()
    var intervalMs = Math.max(5000, Number(options.intervalMs || DEFAULT_HEARTBEAT_INTERVAL_MS))
    var payload = options.payload || { source: 'project_gateway_sdk', visible: !global.document || !global.document.hidden }
    var sendOnce = function () {
      heartbeat(typeof payload === 'function' ? payload() : payload, { timeoutMs: options.timeoutMs || 10000 }).catch(function () {})
    }
    sendOnce()
    heartbeatTimer = global.setInterval(sendOnce, intervalMs)
    return stopHeartbeat
  }

  function stopHeartbeat() {
    if (!heartbeatTimer) return
    global.clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }

  function onContext(listener) {
    if (typeof listener !== 'function') return function () {}
    contextListeners.add(listener)
    if (currentContext) listener(currentContext)
    return function () { contextListeners.delete(listener) }
  }

  function gatewayAvailable() {
    return Boolean(getParentWindow())
  }

  function gatewayInfo() {
    return (currentContext && currentContext.gateway) || {}
  }

  function gatewayTokenAvailable() {
    return Boolean(currentGatewayToken)
  }

  function capabilities() {
    if (!currentContext) return []
    if (Array.isArray(currentContext.capabilities)) return currentContext.capabilities
    if (currentContext.project && Array.isArray(currentContext.project.capabilities)) return currentContext.project.capabilities
    return []
  }

  function limits() {
    if (!currentContext) return {}
    var gateway = gatewayInfo()
    return gateway.gateway_limits || gateway.limits || currentContext.limits || {}
  }

  installAssetFetchBridge()

  var api = {
    version: '0.2.0',
    requestTypes: REQUEST_TYPES,
    responseTypes: RESPONSE_TYPES,
    get context() { return currentContext },
    gatewayAvailable: gatewayAvailable,
    gatewayInfo: gatewayInfo,
    gatewayTokenAvailable: gatewayTokenAvailable,
    capabilities: capabilities,
    limits: limits,
    requestContext: requestContext,
    ready: ready,
    onContext: onContext,
    send: send,
    heartbeat: heartbeat,
    close: close,
    end: close,
    startHeartbeat: startHeartbeat,
    stopHeartbeat: stopHeartbeat,
    input: input,
    recordInput: input,
    ingest: ingest,
    analyze: analyze,
    capability: capability,
    assets: {
      list: listAssets,
      upload: uploadAsset,
    },
    media: {
      subscribe: subscribeMedia,
    },
    ai: ai,
  }

  global.PlatformProjectGateway = api
  global.SkillForgeProject = api
  global.SFProjectGateway = api
  global.addEventListener('message', handleMessage)

  function autoRequestContext() {
    if (!gatewayAvailable()) return
    try {
      requestContext({ ready: true, payload: { source: 'project_gateway_sdk_auto' } })
    } catch (_) {}
  }

  if (global.document && global.document.readyState === 'loading') {
    global.document.addEventListener('DOMContentLoaded', autoRequestContext, { once: true })
  } else {
    global.setTimeout(autoRequestContext, 0)
  }
})(window)
