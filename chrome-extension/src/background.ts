import { cookiesGetAll, storageGet, storageSet, tabsQuery, tabsSendMessage } from './chrome-api'
import { DEFAULT_SERVER_URL, PLATFORM_KEYS, PLATFORMS } from './platforms'
import type {
  ApiLogResponse,
  PlatformConfig,
  PlatformKey,
  ReportApisResult,
  RuntimeMessage,
  StatusResponse,
  StorageConfig,
  SyncAllResult,
  SyncResult,
  CookieDetail,
} from './types'

async function getConfig(): Promise<StorageConfig> {
  const data = await storageGet(['serverUrl', 'apiKey', 'autoSync'])
  return {
    serverUrl: String(data.serverUrl || DEFAULT_SERVER_URL).trim().replace(/\/+$/, ''),
    apiKey: String(data.apiKey || ''),
    autoSync: data.autoSync !== false,
  }
}

const ERROR_MESSAGES: Record<string, string> = {
  MISSING_SHOP_CONTEXT: '请重新绑定 Connector Key',
  CONNECTOR_KEY_REQUIRED: '请重新绑定 API key（旧 key 已不再支持）',
  CONNECTOR_KEY_REVOKED: 'key 已被撤销，请联系管理员或重新申请',
  LEGACY_BLOCKED: '兼容期已结束，请改用个人 API key',
  VERIFY_FAILED: '服务端验证未通过',
  COOKIE_PERMISSION_DENIED: '您没有权限操作该平台 cookie',
  CONNECTOR_SCOPE_DENIED: '无权推送该平台 cookie，请联系管理员核对绑定关系',
  CONNECTOR_SOURCE_MISMATCH: 'Connector Key 与平台不匹配，请检查后台 Key 配置',
}

function friendlyError(code?: string, fallback?: string): string {
  if (!code) return fallback || '未知错误'
  const base = ERROR_MESSAGES[code] || code
  if (fallback && !fallback.includes(code)) return `${base}: ${fallback}`
  return base
}

function toCookieDetail(cookie: chrome.cookies.Cookie): CookieDetail {
  return {
    name: cookie.name,
    value: cookie.value,
    domain: cookie.domain,
    path: cookie.path || '/',
    secure: cookie.secure,
    httpOnly: cookie.httpOnly,
    sameSite: cookie.sameSite,
    expirationDate: cookie.expirationDate,
  }
}

async function pushCookies(platform: PlatformConfig, cookies: chrome.cookies.Cookie[]): Promise<SyncResult> {
  const config = await getConfig()
  if (!config.serverUrl) {
    return { ok: false, error: 'SkillForge 服务器地址未设置' }
  }
  if (!config.apiKey) {
    console.warn('[SF] API Key not configured, skip push')
    return { ok: false, error: 'API Key 未设置' }
  }
  const cookieDetails = cookies.map(toCookieDetail)
  const cookieHeader = cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join('; ')
  const platformKey = Object.entries(PLATFORMS).find(([, value]) => value.sourceId === platform.sourceId)?.[0] || ''

  try {
    const response = await fetch(`${config.serverUrl}/api/data-sources/${platform.sourceId}/push-cookies`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-SF-API-Key': config.apiKey,
      },
      body: JSON.stringify({
        cookies: cookieHeader,
        platform: platformKey,
        domain: platform.domains[0],
        user_agent: navigator.userAgent,
        cookie_details: cookieDetails,
      }),
    })

    const data = (await response.json()) as { message?: string; error?: { code?: string; message?: string }; code?: string; detail?: string }
    const errorCode = data.error?.code || data.code
    const backendMessage = data.message || data.error?.message || data.detail || (response.ok ? '' : `HTTP ${response.status}`)
    const errorMessage = friendlyError(errorCode, backendMessage)
    await storageSet({
      [`status_${platform.sourceId}`]: {
        connected: response.ok,
        pushedAt: new Date().toISOString(),
        message: errorMessage,
        autoSync: config.autoSync,
      },
    })

    if (!response.ok) {
      return { ok: false, error: errorMessage || `HTTP ${response.status}` }
    }
    return { ok: true, data }
  } catch (error) {
    const message = error instanceof Error ? error.message : 'unknown error'
    console.error(`[SF] push ${platform.name} failed:`, error)
    return { ok: false, error: message }
  }
}

async function syncPlatform(platformKey: PlatformKey): Promise<SyncResult> {
  const platform = PLATFORMS[platformKey]
  if (!platform) {
    return { ok: false, error: 'unknown platform' }
  }

  const allCookies: chrome.cookies.Cookie[] = []
  for (const domain of platform.domains) {
    const cookies = await cookiesGetAll({ domain })
    allCookies.push(...cookies)
  }

  if (allCookies.length === 0) {
    return { ok: false, error: '未检测到登录 cookies' }
  }

  const dedupedCookies = new Map<string, chrome.cookies.Cookie>()
  allCookies.forEach((cookie) => {
    dedupedCookies.set(`${cookie.domain}|${cookie.path}|${cookie.name}`, cookie)
  })

  return pushCookies(platform, [...dedupedCookies.values()])
}

async function syncAll(): Promise<SyncAllResult> {
  const results = {} as SyncAllResult
  for (const key of PLATFORM_KEYS) {
    results[key] = await syncPlatform(key)
  }
  return results
}

async function reportApis(tabId: number): Promise<ReportApisResult> {
  const response = await tabsSendMessage<ApiLogResponse>(tabId, { type: 'get-api-log' })
  if (!response || !response.apis?.length) {
    return { ok: false, error: '未捕获到 API 请求' }
  }

  const config = await getConfig()
  if (!config.serverUrl) {
    return { ok: false, error: 'SkillForge 服务器地址未设置' }
  }
  if (!config.apiKey) {
    return { ok: false, error: 'API Key 未设置' }
  }

  const payload = {
    page_url: response.pageUrl,
    page_title: response.pageTitle,
    apis: response.apis.map((api) => ({
      method: api.method,
      url: api.url,
      status: api.status,
      content_type: api.contentType,
      response_keys: api.responseKeys,
    })),
    reported_at: new Date().toISOString(),
  }

  try {
    const result = await fetch(`${config.serverUrl}/api/data-sources/api-discovery`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-SF-API-Key': config.apiKey,
      },
      body: JSON.stringify(payload),
    })

    const data = await result.json()
    return { ok: result.ok, apiCount: response.apis.length, data }
  } catch (error) {
    const message = error instanceof Error ? error.message : 'unknown error'
    return { ok: false, error: message }
  }
}

chrome.cookies.onChanged.addListener(async ({ cookie }) => {
  const config = await getConfig()
  if (!config.autoSync || !config.apiKey) return

  for (const key of PLATFORM_KEYS) {
    const platform = PLATFORMS[key]
    const matched = platform.domains.some((domain) => cookie.domain.endsWith(domain.replace(/^\./, '')))
    if (!matched) continue

    const throttleKey = `_throttle_${key}`
    const data = await storageGet([throttleKey])
    const lastSyncedAt = Number(data[throttleKey] || 0)
    if (Date.now() - lastSyncedAt < 5000) return

    await storageSet({ [throttleKey]: Date.now() })
    console.log(`[SF] cookie changed for ${platform.name}, syncing...`)
    await syncPlatform(key)
    return
  }
})

chrome.alarms.create('sync-cookies', { periodInMinutes: 30 })
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === 'sync-cookies') {
    const config = await getConfig()
    if (!config.autoSync || !config.apiKey) return
    console.log('[SF] scheduled sync')
    await syncAll()
  }
})

chrome.runtime.onInstalled.addListener(() => {
  console.log('[SF] extension installed, initial sync')
  setTimeout(async () => {
    const config = await getConfig()
    if (config.apiKey && config.autoSync) {
      await syncAll()
    }
  }, 2000)
})

chrome.runtime.onMessage.addListener((message: RuntimeMessage, _sender, sendResponse) => {
  if (message.type === 'sync-all') {
    syncAll().then(sendResponse)
    return true
  }

  if (message.type === 'sync-platform') {
    syncPlatform(message.platform).then(sendResponse)
    return true
  }

  if (message.type === 'get-status') {
    storageGet(null).then((data) => {
      const status = {} as StatusResponse
      PLATFORM_KEYS.forEach((key) => {
        status[key] = (data[`status_${PLATFORMS[key].sourceId}`] as StatusResponse[PlatformKey]) || {
          connected: false,
          message: data.apiKey ? undefined : '未配置 Connector Key',
        }
      })
      sendResponse(status)
    })
    return true
  }

  if (message.type === 'report-apis') {
    tabsQuery({ active: true, currentWindow: true }).then(async (tabs) => {
      if (!tabs[0]?.id) {
        sendResponse({ ok: false, error: '无活跃 tab' } satisfies ReportApisResult)
        return
      }
      sendResponse(await reportApis(tabs[0].id))
    })
    return true
  }

  return false
})
