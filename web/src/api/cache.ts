import axios, { type AxiosInstance, type AxiosRequestConfig, type AxiosResponse } from 'axios'
import { useUserStore } from '@/stores/user'

type CacheOption = boolean | {
  ttl?: number
  refresh?: boolean
  namespace?: string
}

type CachedRequestConfig = AxiosRequestConfig & {
  cache?: CacheOption
}

type ApiCachePolicy = {
  namespace: string
  ttl: number
}

type CachedResponse = {
  expiresAt: number
  data: unknown
  status: number
  statusText: string
  headers: unknown
  persist?: boolean
}

const SIX_HOURS = 6 * 60 * 60
const HOT_ACCESS_THRESHOLD = 3
const HOT_TTL_MULTIPLIER = 3
const ACCESS_WINDOW_MS = 10 * 60 * 1000
const PERSIST_PREFIX = 'skillforge:api-cache:'
const PERSIST_MAX_ITEMS = 80
const apiCache = new Map<string, CachedResponse>()
const inflight = new Map<string, Promise<AxiosResponse>>()
const accessStats = new Map<string, { count: number; windowUntil: number }>()

const CACHE_RULES: Array<[RegExp, ApiCachePolicy]> = [
  [/^\/sf\/overview\b/, { namespace: 'sf', ttl: SIX_HOURS }],
  [/^\/sf\/catalog\b/, { namespace: 'sf', ttl: 300 }],
  [/^\/sf\/trace\b/, { namespace: 'sf', ttl: 60 }],
  [/^\/learning\/(home|summary|events|artifacts|candidates|flow-graph|flow-topology|flow-journeys|bottlenecks|entities|training-manifest|automation-status)\b/, { namespace: 'learning', ttl: 60 }],

  [/^\/knowledge\/(summary|bases|documents|index-jobs|rag\/health)\b/, { namespace: 'knowledge', ttl: 300 }],
  [/^\/knowledge\/(search|context)\b/, { namespace: 'knowledge', ttl: 300 }],
  [/^\/training\/(resources|datasets)\b/, { namespace: 'training', ttl: 300 }],
  [/^\/training\/(jobs|deployments|skills\/[^/]+\/candidate-readiness)\b/, { namespace: 'training', ttl: 60 }],
  [/^\/aiclaw\/(departments|instances|sync-targets)\b/, { namespace: 'agent', ttl: 30 }],
  [/^\/task-tree\b/, { namespace: 'tasktree', ttl: 120 }],

  [/^\/skills\/(templates|departments|pinned|params|conflicts)\b/, { namespace: 'skills', ttl: 300 }],
  [/^\/skills\b/, { namespace: 'skills', ttl: 30 }],
  [/^\/hall\b/, { namespace: 'hall', ttl: 300 }],
  [/^\/portal\b/, { namespace: 'portal', ttl: 120 }],
  [/^\/dashboard\b/, { namespace: 'dashboard', ttl: 120 }],
  [/^\/executions\/runs\/[^/]+\/trace\b/, { namespace: 'runtrace', ttl: 60 }],
  [/^\/executions\b/, { namespace: 'execution', ttl: 30 }],
  [/^\/reviews\b/, { namespace: 'reviews', ttl: 30 }],

  [/^\/users\b/, { namespace: 'admin', ttl: 300 }],
  [/^\/org\b/, { namespace: 'admin', ttl: 300 }],
  [/^\/audit\b/, { namespace: 'admin', ttl: 300 }],
  [/^\/codex\/admin\b/, { namespace: 'admin', ttl: 300 }],
  [/^\/admin\/coding-agent\/mcp-servers\b/, { namespace: 'admin', ttl: 300 }],
  [/^\/system-config\b/, { namespace: 'admin', ttl: 300 }],

  [/^\/data-sources\/(cookie-pools|collection-health|platform-api-drift-alerts|api-discovery|connector-keys)\b/, { namespace: 'connections', ttl: 120 }],
  [/^\/data-sources\b/, { namespace: 'datasources', ttl: 120 }],
  [/^\/browser\/slots\b/, { namespace: 'connections', ttl: 30 }],
  [/^\/playbooks\b/, { namespace: 'playbooks', ttl: 120 }],
  [/^\/compliance\b/, { namespace: 'compliance', ttl: 300 }],
  [/^\/notifications\b/, { namespace: 'notifications', ttl: 30 }],
  [/^\/changelog\b/, { namespace: 'changelog', ttl: 300 }],
]

const NO_CACHE_PATHS = [
  /^\/auth\//,
  // AI Chat has its own cursor pagination and server persistence.  Generic Hall
  // caching would make freshly streamed messages appear stale after reload.
  /^\/hall\/ai-chat\b/,
  // Inbox/todo data can be produced by background Skill runs outside this browser.
  // Server-side Redis has coherent invalidation; browser memory cache would go stale.
  /^\/inbox\/(overview|reports)\b/,
  /^\/todos(?!\/stats\b)\b/,
  /^\/browser\/(status|status-local|novnc)\b/,
  /\/(download|export|bridge-script|systemd-unit)\b/,
  /\/connector-api-key\b/,
  /\/cookies\b/,
  /\/rag\/test-embedding\b/,
]

function stableStringify(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value !== 'object') return String(value)
  if (value instanceof URLSearchParams) return value.toString()
  if (Array.isArray(value)) return `[${value.map((item) => stableStringify(item)).join(',')}]`
  return `{${Object.keys(value as Record<string, unknown>).sort().map((key) => {
    return `${JSON.stringify(key)}:${stableStringify((value as Record<string, unknown>)[key])}`
  }).join(',')}}`
}

function normalizePath(url?: string, baseURL?: string): string {
  const raw = String(url || '')
  if (!raw) return ''
  try {
    const absolute = new URL(raw, 'http://skillforge.local')
    return absolute.pathname.replace(/^\/api(?=\/)/, '') || '/'
  } catch {
    const combined = `${baseURL || ''}${raw}`
    return combined.replace(/^\/api(?=\/)/, '').split('?')[0] || raw.split('?')[0]
  }
}

function isCacheDisabled(config: CachedRequestConfig): boolean {
  return config.cache === false
}

function shouldBypassCacheRead(config: CachedRequestConfig): boolean {
  const option = config.cache
  return Boolean(
    option === false ||
    (typeof option === 'object' && option.refresh) ||
    String((config.headers as Record<string, unknown> | undefined)?.['X-SF-Cache-Bypass'] || '') === '1',
  )
}

export function resolveApiCachePolicy(config: CachedRequestConfig): ApiCachePolicy | null {
  const method = String(config.method || 'get').toLowerCase()
  if (config.responseType === 'blob' || isCacheDisabled(config)) return null
  const path = normalizePath(config.url, config.baseURL)
  const safeQueryPost = method === 'post' && /^\/knowledge\/(search|context)\b/.test(path)
  if (method !== 'get' && !safeQueryPost) return null
  if (!path || NO_CACHE_PATHS.some((rule) => rule.test(path))) return null

  const option = config.cache
  if (typeof option === 'object' && option.ttl) {
    return {
      namespace: option.namespace || namespaceForPath(path) || 'api',
      ttl: option.ttl,
    }
  }
  for (const [rule, policy] of CACHE_RULES) {
    if (rule.test(path)) return policy
  }
  return null
}

function namespaceForPath(path: string): string | null {
  for (const [rule, policy] of CACHE_RULES) {
    if (rule.test(path)) return policy.namespace
  }
  return null
}

function currentUserScope(): string {
  try {
    const store = useUserStore()
    const info = (store.userInfo || {}) as Record<string, unknown>
    return [
      String(info.user_id || info.id || info.username || 'anonymous'),
      String(info.permissions_rev || 0),
      String(store.role || ''),
    ].join('@')
  } catch {
    return 'anonymous'
  }
}

export function buildApiCacheKey(config: CachedRequestConfig, policy: ApiCachePolicy): string {
  const path = normalizePath(config.url, config.baseURL)
  const method = String(config.method || 'get').toLowerCase()
  return [
    policy.namespace,
    currentUserScope(),
    method,
    path,
    stableStringify(config.params),
    stableStringify(config.data),
  ].join(':')
}

function recordApiCacheAccess(key: string): number {
  const now = Date.now()
  const stat = accessStats.get(key)
  if (!stat || stat.windowUntil <= now) {
    accessStats.set(key, { count: 1, windowUntil: now + ACCESS_WINDOW_MS })
    return 1
  }
  stat.count += 1
  return stat.count
}

function adaptiveApiCacheTtl(baseTtl: number, accessCount: number): number {
  const ttl = Number.isFinite(baseTtl) && baseTtl > 0 ? baseTtl : 60
  if (accessCount >= HOT_ACCESS_THRESHOLD) {
    return Math.min(ttl * HOT_TTL_MULTIPLIER, SIX_HOURS)
  }
  return Math.min(ttl, SIX_HOURS)
}

function persistKey(key: string) {
  return `${PERSIST_PREFIX}${key}`
}

function canPersistPolicy(policy: ApiCachePolicy): boolean {
  return policy.ttl >= 60 && !['notifications', 'todos'].includes(policy.namespace)
}

function readPersistedCache(key: string): CachedResponse | null {
  if (typeof sessionStorage === 'undefined') return null
  try {
    const raw = sessionStorage.getItem(persistKey(key))
    if (!raw) return null
    const parsed = JSON.parse(raw) as CachedResponse
    if (!parsed || parsed.expiresAt <= Date.now()) {
      sessionStorage.removeItem(persistKey(key))
      return null
    }
    return parsed
  } catch {
    return null
  }
}

function trimPersistedCache() {
  if (typeof sessionStorage === 'undefined') return
  const keys = Object.keys(sessionStorage).filter((key) => key.startsWith(PERSIST_PREFIX))
  if (keys.length <= PERSIST_MAX_ITEMS) return
  keys.slice(0, keys.length - PERSIST_MAX_ITEMS).forEach((key) => sessionStorage.removeItem(key))
}

function writePersistedCache(key: string, value: CachedResponse) {
  if (typeof sessionStorage === 'undefined') return
  try {
    sessionStorage.setItem(persistKey(key), JSON.stringify(value))
    trimPersistedCache()
  } catch {
    // storage quota or privacy mode; memory cache still works.
  }
}

function deletePersistedCache(key: string) {
  if (typeof sessionStorage === 'undefined') return
  sessionStorage.removeItem(persistKey(key))
}

export function invalidateApiCache(namespace?: string | RegExp): number {
  if (!namespace) {
    const count = apiCache.size
    apiCache.clear()
    inflight.clear()
    accessStats.clear()
    if (typeof sessionStorage !== 'undefined') {
      Object.keys(sessionStorage)
        .filter((key) => key.startsWith(PERSIST_PREFIX))
        .forEach((key) => sessionStorage.removeItem(key))
    }
    return count
  }
  let deleted = 0
  for (const key of Array.from(apiCache.keys())) {
    const matched = typeof namespace === 'string' ? key.startsWith(`${namespace}:`) : namespace.test(key)
    if (matched) {
      apiCache.delete(key)
      deletePersistedCache(key)
      deleted += 1
    }
  }
  for (const key of Array.from(inflight.keys())) {
    const matched = typeof namespace === 'string' ? key.startsWith(`${namespace}:`) : namespace.test(key)
    if (matched) inflight.delete(key)
  }
  for (const key of Array.from(accessStats.keys())) {
    const matched = typeof namespace === 'string' ? key.startsWith(`${namespace}:`) : namespace.test(key)
    if (matched) accessStats.delete(key)
  }
  return deleted
}

function mutationNamespaces(path: string): string[] {
  const namespaces = new Set<string>()
  const add = (...items: string[]) => items.forEach((item) => namespaces.add(item))
  if (/^\/knowledge\b/.test(path)) add('knowledge', 'learning')
  if (/^\/training\b/.test(path)) add('training', 'dashboard', 'learning')
  if (/^\/aiclaw\b/.test(path)) add('agent', 'tasktree', 'learning')
  if (/^\/task-tree\b/.test(path)) add('tasktree')
  if (/^\/sf\b|^\/codex\/mcp\/call\b/.test(path)) add('sf', 'learning')
  if (/^\/learning\b/.test(path)) add('learning', 'knowledge', 'training', 'sf', 'agent')
  if (/^\/todos\b|^\/inbox\b/.test(path)) add('todos', 'inbox', 'sf', 'learning')
  if (/^\/executions\b/.test(path)) add('execution', 'runtrace', 'tasktree', 'todos', 'inbox', 'sf', 'learning')
  if (/^\/skills\b/.test(path)) add('skills', 'hall', 'portal', 'training', 'agent', 'tasktree', 'learning')
  if (/^\/hall\b/.test(path)) add('hall')
  if (/^\/portal\b/.test(path)) add('portal', 'skills')
  if (/^\/reviews\b/.test(path)) add('reviews', 'skills', 'portal')
  if (/^\/users\b|^\/org\b/.test(path)) add('admin', 'tasktree', 'todos', 'inbox')
  if (/^\/data-sources\b|^\/browser\b/.test(path)) add('datasources', 'connections', 'hall')
  if (/^\/audit\b|^\/codex\/admin\b|^\/admin\/coding-agent\b|^\/system-config\b/.test(path)) add('admin')
  if (/^\/playbooks\b/.test(path)) add('playbooks')
  if (/^\/compliance\b/.test(path)) add('compliance')
  if (/^\/notifications\b/.test(path)) add('notifications')
  return Array.from(namespaces)
}

export function invalidateApiCacheForMutation(config?: AxiosRequestConfig): void {
  const method = String(config?.method || 'get').toLowerCase()
  if (['get', 'head', 'options'].includes(method)) return
  const path = normalizePath(config?.url, config?.baseURL)
  if (method === 'post' && /^\/knowledge\/(search|context)\b/.test(path)) return
  for (const namespace of mutationNamespaces(path)) invalidateApiCache(namespace)
}

export function installApiCache(instance: AxiosInstance): void {
  const adapter = (axios as unknown as { getAdapter?: (value: unknown) => any }).getAdapter?.(
    instance.defaults.adapter || axios.defaults.adapter,
  )
  if (!adapter) return

  instance.interceptors.request.use((config) => {
    const cachedConfig = config as CachedRequestConfig
    const policy = resolveApiCachePolicy(cachedConfig)
    if (!policy) return config

    const key = buildApiCacheKey(cachedConfig, policy)
    const accessCount = recordApiCacheAccess(key)
    const effectiveTtl = adaptiveApiCacheTtl(policy.ttl, accessCount)
    const bypassRead = shouldBypassCacheRead(cachedConfig)
    const persistedHit = bypassRead ? null : readPersistedCache(key)
    if (persistedHit && !apiCache.has(key)) apiCache.set(key, persistedHit)
    const hit = bypassRead ? null : apiCache.get(key)
    if (hit && hit.expiresAt > Date.now()) {
      if (effectiveTtl > policy.ttl) {
        hit.expiresAt = Math.max(hit.expiresAt, Date.now() + effectiveTtl * 1000)
      }
      cachedConfig.adapter = async () => ({
        data: hit.data,
        status: hit.status,
        statusText: hit.statusText,
        headers: hit.headers as any,
        config,
        request: { fromApiCache: true },
      })
      return cachedConfig as any
    }
    if (bypassRead) {
      apiCache.delete(key)
      deletePersistedCache(key)
      inflight.delete(key)
    } else if (hit) {
      apiCache.delete(key)
      deletePersistedCache(key)
    }

    cachedConfig.adapter = async (nextConfig) => {
      const existing = bypassRead ? null : inflight.get(key)
      if (existing) return existing
      const promise = adapter(nextConfig).then((response: AxiosResponse) => {
        if (response.status >= 200 && response.status < 300) {
          const cachedValue = {
            data: response.data,
            status: response.status,
            statusText: response.statusText,
            headers: response.headers,
            expiresAt: Date.now() + effectiveTtl * 1000,
            persist: canPersistPolicy(policy),
          }
          apiCache.set(key, cachedValue)
          if (cachedValue.persist) writePersistedCache(key, cachedValue)
        }
        return response
      }).finally(() => {
        inflight.delete(key)
      })
      inflight.set(key, promise)
      return promise
    }
    return cachedConfig as any
  })
}

export const __apiCacheForTest = {
  size: () => apiCache.size,
  clear: () => invalidateApiCache(),
  normalizePath,
  mutationNamespaces,
  recordApiCacheAccess,
  adaptiveApiCacheTtl,
  canPersistPolicy,
}
