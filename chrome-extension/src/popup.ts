import { permissionsRequest, runtimeSendMessage, storageGet, storageSet } from './chrome-api'
import { DEFAULT_SERVER_URL, PLATFORM_KEYS, PLATFORMS } from './platforms'
import type {
  PlatformKey,
  ReportApisResult,
  StatusResponse,
  SyncAllResult,
  SyncResult,
} from './types'

const statusBar = getRequiredElement<HTMLDivElement>('statusBar')
const serverUrlInput = getRequiredElement<HTMLInputElement>('serverUrl')
const apiKeyInput = getRequiredElement<HTMLInputElement>('apiKey')
const apiKeyHint = getRequiredElement<HTMLDivElement>('apiKeyHint')
const autoSyncInput = getRequiredElement<HTMLInputElement>('autoSync')
const platformList = getRequiredElement<HTMLDivElement>('platformList')
const saveConfigButton = getRequiredElement<HTMLButtonElement>('saveConfig')
const syncAllButton = getRequiredElement<HTMLButtonElement>('syncAll')
const reportApisButton = getRequiredElement<HTMLButtonElement>('reportApis')

function getRequiredElement<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id)
  if (!element) {
    throw new Error(`Missing DOM element: ${id}`)
  }
  return element as T
}

function showStatus(message: string, isError = false) {
  statusBar.textContent = message
  statusBar.className = `status-bar show${isError ? ' error' : ''}`
  window.setTimeout(() => {
    statusBar.className = 'status-bar'
  }, 3000)
}

function normalizeServerUrl(value: string): string {
  return value.trim().replace(/\/+$/, '')
}

async function hashKeyPreview(value: string): Promise<string> {
  if (!value) return ''
  const data = new TextEncoder().encode(value)
  const digest = await crypto.subtle.digest('SHA-256', data)
  const bytes = Array.from(new Uint8Array(digest))
  return bytes.map((byte) => byte.toString(16).padStart(2, '0')).join('').slice(0, 12)
}

function getServerOriginPattern(serverUrl: string): string {
  const url = new URL(serverUrl)
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error('服务器地址只支持 http/https')
  }
  return `${url.protocol}//${url.host}/*`
}

function timeAgo(isoString?: string): string {
  if (!isoString) return ''
  const diff = Date.now() - new Date(isoString).getTime()
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  return `${Math.floor(diff / 86_400_000)} 天前`
}

async function loadConfig() {
  const data = await storageGet(['serverUrl', 'apiKey', 'apiKeyHash', 'autoSync'])
  serverUrlInput.value = normalizeServerUrl(String(data.serverUrl || DEFAULT_SERVER_URL))
  apiKeyInput.value = String(data.apiKey || '')
  apiKeyHint.textContent = data.apiKeyHash ? `已保存 Key: ${String(data.apiKeyHash)}` : '未保存 Key'
  autoSyncInput.checked = data.autoSync !== false
  updateSyncAvailability()
}

async function updateSyncAvailability() {
  const data = await storageGet(['apiKey'])
  const hasKey = Boolean(String(data.apiKey || '').trim())
  syncAllButton.disabled = !hasKey
  if (!hasKey) {
    syncAllButton.title = '请先保存 Connector Key'
  } else {
    syncAllButton.removeAttribute('title')
  }
}

async function handleSyncPlatform(button: HTMLButtonElement, platform: PlatformKey) {
  const info = PLATFORMS[platform]
  button.textContent = '...'
  button.disabled = true

  try {
    const result = await runtimeSendMessage<SyncResult>({ type: 'sync-platform', platform })
    if (result.ok) {
      showStatus(`${info.name} 同步成功`)
      button.textContent = '刷新'
      await renderPlatforms()
      return
    }

    showStatus(`${info.name} 同步失败: ${result.error || '未知错误'}`, true)
    button.textContent = '重试'
  } catch (error) {
    const message = error instanceof Error ? error.message : '未知错误'
    showStatus(`${info.name} 同步失败: ${message}`, true)
    button.textContent = '重试'
  } finally {
    button.disabled = false
  }
}

async function renderPlatforms() {
  const status = await runtimeSendMessage<StatusResponse>({ type: 'get-status' })
  platformList.innerHTML = ''

  PLATFORM_KEYS.forEach((key) => {
    const info = PLATFORMS[key]
    const platformStatus = status[key] || { connected: false }
    const item = document.createElement('div')
    item.className = 'platform'
    item.innerHTML = `
    <div class="platform-info">
        <div class="platform-dot ${platformStatus.connected ? 'dot-connected' : 'dot-disconnected'}"></div>
        <div>
          <div class="platform-name">${info.name}</div>
          <div class="platform-time">${platformStatus.connected ? timeAgo(platformStatus.pushedAt) : (platformStatus.message || '未连接')}</div>
        </div>
      </div>
      <button class="btn btn-outline btn-sm" data-platform="${key}">
        ${platformStatus.connected ? '刷新' : '同步'}
      </button>
    `

    const button = item.querySelector<HTMLButtonElement>('button')
    if (button) {
      button.addEventListener('click', () => {
        handleSyncPlatform(button, key)
      })
    }

    platformList.appendChild(item)
  })
}

saveConfigButton.addEventListener('click', async () => {
  const serverUrl = normalizeServerUrl(serverUrlInput.value)
  const inputApiKey = apiKeyInput.value.trim()
  const autoSync = autoSyncInput.checked
  if (!serverUrl) {
    showStatus('请先填写 SkillForge 服务器地址', true)
    return
  }

  try {
    const existing = await storageGet(['apiKey'])
    const apiKey = inputApiKey || String(existing.apiKey || '')
    if (!apiKey) {
      showStatus('请填写 Connector Key', true)
      return
    }
    const origin = getServerOriginPattern(serverUrl)
    const granted = await permissionsRequest({ origins: [origin] })
    if (!granted) {
      showStatus('需要允许插件访问该 SkillForge 地址', true)
      return
    }
    const apiKeyHash = await hashKeyPreview(apiKey)
    await storageSet({ serverUrl, apiKey, apiKeyHash, autoSync })
    apiKeyInput.value = apiKey
    apiKeyHint.textContent = apiKeyHash ? `已保存 Key: ${apiKeyHash}` : '未保存 Key'
    await updateSyncAvailability()
    showStatus('配置已保存')
    await renderPlatforms()
  } catch (error) {
    const message = error instanceof Error ? error.message : '服务器地址格式不正确'
    showStatus(message, true)
  }
})

autoSyncInput.addEventListener('change', async () => {
  await storageSet({ autoSync: autoSyncInput.checked })
  showStatus(autoSyncInput.checked ? '自动同步已开启' : '自动同步已关闭')
})

syncAllButton.addEventListener('click', async () => {
  showStatus('正在同步全部平台...')
  try {
    const results = await runtimeSendMessage<SyncAllResult>({ type: 'sync-all' })
    const okCount = Object.values(results || {}).filter((result) => result?.ok).length
    showStatus(`同步完成: ${okCount}/${PLATFORM_KEYS.length} 个平台成功`)
    await renderPlatforms()
  } catch (error) {
    const message = error instanceof Error ? error.message : '未知错误'
    showStatus(`同步失败: ${message}`, true)
  }
})

reportApisButton.addEventListener('click', async () => {
  showStatus('正在扫描当前页面 API...')
  try {
    const result = await runtimeSendMessage<ReportApisResult>({ type: 'report-apis' })
    if (result.ok) {
      showStatus(`已上报 ${result.apiCount || 0} 个 API 端点到 SkillForge`)
    } else {
      showStatus(result.error || '扫描失败', true)
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : '未知错误'
    showStatus(message, true)
  }
})

void loadConfig().then(renderPlatforms)
