/**
 * "最近编辑"列表持久化（O12）：localStorage + 按登录用户 scope，最多 10 条。
 *
 * O13 Studio 拆分的第一步：把 SkillStudio.vue L707-721 里嵌入的 sf-skill-recent
 * 写入块抽出；SkillList.vue 也用同一个 computed key 避免两侧不同步。
 */
import { useUserStore } from '@/stores/user'

export interface RecentItem {
  id: string
  name?: string
  visited_at?: number
}

type RecentUserStore = Pick<ReturnType<typeof useUserStore>, 'userInfo'>

export interface RecentStoreDeps {
  userStore: RecentUserStore
}

const MAX_RECENT = 10

function getRecentScopeKey(userStore: RecentUserStore): string {
  const uid = userStore.userInfo?.user_id || userStore.userInfo?.username || 'anon'
  return `sf-skill-recent:${uid}`
}

function isRecentItem(value: unknown): value is RecentItem {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Record<string, unknown>
  return typeof candidate.id === 'string' && candidate.id.length > 0
}

export function useSkillStudioRecent(deps: RecentStoreDeps) {
  function key(): string {
    return getRecentScopeKey(deps.userStore)
  }

  function read(): RecentItem[] {
    try {
      const raw = localStorage.getItem(key())
      if (!raw) return []
      const list = JSON.parse(raw) as unknown
      return Array.isArray(list) ? list.filter(isRecentItem) : []
    } catch {
      return []
    }
  }

  function markVisited(item: RecentItem): void {
    if (!item?.id) return
    try {
      const list = read().filter((x) => x.id !== item.id)
      list.unshift({
        id: item.id,
        name: item.name || item.id,
        visited_at: Date.now(),
      })
      localStorage.setItem(key(), JSON.stringify(list.slice(0, MAX_RECENT)))
    } catch { /* quota / disabled */ }
  }

  return { key, read, markVisited, MAX_RECENT }
}
