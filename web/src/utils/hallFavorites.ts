/**
 * 大厅收藏 + 最近浏览 —— 纯 localStorage 实现，无需登录态即生效。
 *
 * - 收藏：ID 列表（category 字符串），置顶 / 排序规则由调用方自行决定
 * - 最近浏览：最多 10 条，FIFO，最新插入在前；已存在则移到首位
 * - v2.7.5：为以后按用户隔离预留 key 后缀（userId），当前用"anon"作为缺省 scope
 */
import { ref, onMounted, onUnmounted } from 'vue'

const FAV_KEY_PREFIX = 'sf.hall.favorites'
const RECENT_KEY_PREFIX = 'sf.hall.recent'
const RECENT_MAX = 10

type Scope = string

function safeRead(key: string): string[] {
  try {
    const raw = window.localStorage.getItem(key)
    if (!raw) return []
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === 'string') : []
  } catch {
    return []
  }
}

function safeWrite(key: string, list: string[]): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(list))
  } catch {
    // 存储配额用尽或隐私模式；静默忽略
  }
}

function favKey(scope: Scope): string {
  return `${FAV_KEY_PREFIX}.${scope}`
}
function recentKey(scope: Scope): string {
  return `${RECENT_KEY_PREFIX}.${scope}`
}

export function listFavorites(scope: Scope = 'anon'): string[] {
  return safeRead(favKey(scope))
}

export function isFavorite(category: string, scope: Scope = 'anon'): boolean {
  return listFavorites(scope).includes(category)
}

export function toggleFavorite(category: string, scope: Scope = 'anon'): boolean {
  const key = favKey(scope)
  const list = safeRead(key)
  const idx = list.indexOf(category)
  if (idx >= 0) {
    list.splice(idx, 1)
  } else {
    list.unshift(category)
  }
  safeWrite(key, list)
  window.dispatchEvent(new CustomEvent('hall-favorites-changed', { detail: { scope } }))
  return idx < 0 // true = 新加入收藏
}

export function listRecent(scope: Scope = 'anon'): string[] {
  return safeRead(recentKey(scope))
}

export function pushRecent(category: string, scope: Scope = 'anon'): void {
  if (!category) return
  const key = recentKey(scope)
  const list = safeRead(key).filter((x) => x !== category)
  list.unshift(category)
  safeWrite(key, list.slice(0, RECENT_MAX))
  window.dispatchEvent(new CustomEvent('hall-recent-changed', { detail: { scope } }))
}

export function clearRecent(scope: Scope = 'anon'): void {
  safeWrite(recentKey(scope), [])
  window.dispatchEvent(new CustomEvent('hall-recent-changed', { detail: { scope } }))
}

/** Composable：响应 favorites / recent 变化的响应式数据。 */
export function useHallMemory(scope: Scope = 'anon') {
  const favorites = ref<string[]>(listFavorites(scope))
  const recent = ref<string[]>(listRecent(scope))

  function onFavChange(e: Event) {
    const detail = (e as CustomEvent).detail
    if (detail?.scope === scope) favorites.value = listFavorites(scope)
  }
  function onRecentChange(e: Event) {
    const detail = (e as CustomEvent).detail
    if (detail?.scope === scope) recent.value = listRecent(scope)
  }

  onMounted(() => {
    window.addEventListener('hall-favorites-changed', onFavChange)
    window.addEventListener('hall-recent-changed', onRecentChange)
  })
  onUnmounted(() => {
    window.removeEventListener('hall-favorites-changed', onFavChange)
    window.removeEventListener('hall-recent-changed', onRecentChange)
  })

  return { favorites, recent }
}
