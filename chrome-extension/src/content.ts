import type { ApiLogEntry, ApiLogResponse, RuntimeMessage } from './types'

const apiLog: ApiLogEntry[] = []
const MAX_APIS = 200
const DATA_SCRIPT_HINTS = [
  'mtop',
  'h5api',
  '/api/',
  '/rest/',
  '/openapi/',
  '/alimama/',
  'onebp',
  'report',
  'rpt',
  'json',
  'jsonp',
  'callback=',
  '.do?',
]

function appendApiLog(entry: ApiLogEntry) {
  if (apiLog.length < MAX_APIS) {
    apiLog.push(entry)
  }
}

function shouldCaptureResource(entry: PerformanceResourceTiming): boolean {
  if (entry.initiatorType === 'xmlhttprequest' || entry.initiatorType === 'fetch') {
    return true
  }
  if (entry.initiatorType !== 'script') {
    return false
  }
  const url = entry.name.toLowerCase()
  return DATA_SCRIPT_HINTS.some((hint) => url.includes(hint))
}

function injectNetworkHook() {
  const code = `
    (function() {
      if (window.__SF_HOOKED) return;
      window.__SF_HOOKED = true;
      const log = [];
      window.__SF_API_LOG = log;

      function extractKeys(obj, depth) {
        depth = depth || 0;
        if (depth > 2) return typeof obj;
        if (Array.isArray(obj)) return obj.length > 0 ? ['Array<' + extractKeys(obj[0], depth + 1) + '>'] : ['Array'];
        if (obj && typeof obj === 'object') {
          const result = {};
          for (const key of Object.keys(obj).slice(0, 15)) result[key] = extractKeys(obj[key], depth + 1);
          return result;
        }
        return typeof obj;
      }

      const originalOpen = XMLHttpRequest.prototype.open;
      const originalSend = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.open = function(method, url) {
        this._sfMethod = method;
        this._sfUrl = url;
        return originalOpen.apply(this, arguments);
      };
      XMLHttpRequest.prototype.send = function() {
        this.addEventListener('load', function() {
          try {
            if (!this._sfUrl || this._sfUrl.includes('SkillForge')) return;
            const entry = {
              type: 'xhr',
              method: this._sfMethod,
              url: this._sfUrl,
              status: this.status,
              timestamp: Date.now(),
            };
            const contentType = this.getResponseHeader('content-type') || '';
            if (contentType.includes('json') && this.responseText) {
              try { entry.responseKeys = extractKeys(JSON.parse(this.responseText)); } catch {}
            }
            if (log.length < 200) log.push(entry);
          } catch {}
        });
        return originalSend.apply(this, arguments);
      };

      const originalFetch = window.fetch;
      window.fetch = async function(input, init) {
        const url = typeof input === 'string' ? input : input?.url || '';
        const method = init?.method || 'GET';
        const response = await originalFetch.apply(this, arguments);
        try {
          if (url && !url.includes('SkillForge')) {
            const clone = response.clone();
            const contentType = clone.headers.get('content-type') || '';
            const entry = {
              type: 'fetch',
              method,
              url,
              status: clone.status,
              timestamp: Date.now(),
            };
            if (contentType.includes('json')) {
              try {
                const text = await clone.text();
                entry.responseKeys = extractKeys(JSON.parse(text));
              } catch {}
            }
            if (log.length < 200) log.push(entry);
          }
        } catch {}
        return response;
      };
    })();
  `

  const blob = new Blob([code], { type: 'text/javascript' })
  const url = URL.createObjectURL(blob)
  const script = document.createElement('script')
  script.src = url
  document.documentElement.appendChild(script)
  script.onload = () => {
    URL.revokeObjectURL(url)
    script.remove()
  }
}

try {
  const observer = new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      if (entry.entryType !== 'resource') continue
      const resourceEntry = entry as PerformanceResourceTiming
      if (shouldCaptureResource(resourceEntry)) {
        appendApiLog({
          type: resourceEntry.initiatorType,
          method: 'GET',
          url: resourceEntry.name,
          duration: Math.round(resourceEntry.duration),
          size: resourceEntry.transferSize || 0,
          timestamp: Date.now(),
        })
      }
    }
  })

  observer.observe({ entryTypes: ['resource'] })
} catch (error) {
  console.warn('[SF] PerformanceObserver failed:', error)
}

try {
  injectNetworkHook()
} catch (error) {
  console.warn('[SF] script injection failed:', error)
}

try {
  performance.getEntriesByType('resource').forEach((entry) => {
    const resourceEntry = entry as PerformanceResourceTiming
    if (shouldCaptureResource(resourceEntry)) {
      appendApiLog({
        type: resourceEntry.initiatorType,
        method: 'GET',
        url: resourceEntry.name,
        duration: Math.round(resourceEntry.duration),
        size: resourceEntry.transferSize || 0,
        timestamp: Date.now(),
      })
    }
  })
} catch {
  // Ignore history read errors.
}

chrome.runtime.onMessage.addListener((message: RuntimeMessage, _sender, sendResponse) => {
  if (message.type !== 'get-api-log') {
    return false
  }

  const combined = [...apiLog]
  let responded = false

  const fallbackTimer = window.setTimeout(() => {
    window.removeEventListener('message', handleMessage)
    if (responded) return
    responded = true
    sendResponse({
      apis: combined,
      pageUrl: window.location.href,
      pageTitle: document.title,
    } satisfies ApiLogResponse)
  }, 500)

  function handleMessage(event: MessageEvent<{ type?: string; data?: ApiLogEntry[] }>) {
    if (event.source !== window) return
    if (event.data?.type !== '__SF_LOG') return

    window.removeEventListener('message', handleMessage)
    if (responded) return
    responded = true
    window.clearTimeout(fallbackTimer)

    const injected = event.data.data || []
    const seen = new Set(combined.map((entry) => `${entry.method} ${(entry.url || '').split('?')[0]}`))

    injected.forEach((entry) => {
      const dedupeKey = `${entry.method} ${(entry.url || '').split('?')[0]}`
      if (!seen.has(dedupeKey)) {
        combined.push(entry)
        seen.add(dedupeKey)
      }
    })

    sendResponse({
      apis: combined,
      pageUrl: window.location.href,
      pageTitle: document.title,
    } satisfies ApiLogResponse)
  }

  window.addEventListener('message', handleMessage)

  try {
    const script = document.createElement('script')
    const blob = new Blob(
      ["window.postMessage({ type: '__SF_LOG', data: window.__SF_API_LOG || [] }, '*');"],
      { type: 'text/javascript' },
    )
    script.src = URL.createObjectURL(blob)
    document.documentElement.appendChild(script)
    script.onload = () => {
      URL.revokeObjectURL(script.src)
      script.remove()
    }
  } catch {
    // Fallback timer will return the performance observer log only.
  }

  return true
})
