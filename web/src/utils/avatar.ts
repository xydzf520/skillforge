/**
 * 头像生成工具
 * - 有用户头像则返回用户头像，其次使用钉钉头像
 * - 否则按账号稳定生成 emoji 表情头像
 */
type AvatarLike = {
  avatar_url?: string | null
  avatar?: string | null
  avatarUrl?: string | null
  dingtalk_avatar?: string | null
  dingtalk_avatar_url?: string | null
  dingtalkAvatarUrl?: string | null
  id?: string
  username?: string
  user_id?: string
  name?: string
}

const EMOJI_AVATARS = [
  '😀', '😄', '😊', '😎', '🤓', '🧐', '🙂', '😇',
  '🤠', '🥳', '😉', '😌', '😺', '😸', '😋', '🤩',
  '😮', '😆', '😃', '😁', '😼', '🙃', '🤗', '😏',
]

const EMOJI_BACKGROUNDS = [
  '#f3d9c6', '#d8ead2', '#d9e2f1', '#f4d6dc',
  '#e8ddc8', '#d6eadf', '#ead9ef', '#f0e4bd',
]

/** 返回展示头像的 URL（真实 URL 优先，降级到账号固定 emoji 头像） */
export function userAvatarUrl(user: AvatarLike | null | undefined): string {
  const existing = userAvatarExistingUrl(user)
  if (existing) return existing
  return emojiAvatarUrl(userAvatarSeed(user))
}

/** 返回账号固定的 emoji；用于需要纯文本兜底的地方。 */
export function userAvatarEmoji(user: AvatarLike | null | undefined): string {
  const seed = userAvatarSeed(user)
  return EMOJI_AVATARS[hashSeed(seed) % EMOJI_AVATARS.length]
}

function userAvatarExistingUrl(user: AvatarLike | null | undefined): string {
  if (!user) return ''
  return firstNonEmpty(
    user.avatar_url,
    user.avatar,
    user.avatarUrl,
    user.dingtalk_avatar_url,
    user.dingtalkAvatarUrl,
    user.dingtalk_avatar,
  )
}

function userAvatarSeed(user: AvatarLike | null | undefined): string {
  if (!user) return 'default'
  return firstNonEmpty(user.id, user.user_id, user.username, user.name) || 'default'
}

function firstNonEmpty(...values: Array<string | null | undefined>): string {
  for (const value of values) {
    const text = String(value || '').trim()
    if (text) return text
  }
  return ''
}

function emojiAvatarUrl(seed: string): string {
  const hash = hashSeed(seed)
  const emoji = EMOJI_AVATARS[hash % EMOJI_AVATARS.length]
  const background = EMOJI_BACKGROUNDS[hash % EMOJI_BACKGROUNDS.length]
  const svg = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">`,
    `<rect width="64" height="64" rx="32" fill="${background}"/>`,
    `<text x="32" y="35" text-anchor="middle" dominant-baseline="middle" font-size="34">${emoji}</text>`,
    `</svg>`,
  ].join('')
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
}

function hashSeed(seed: string): number {
  let hash = 2166136261
  for (let index = 0; index < seed.length; index += 1) {
    hash ^= seed.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}
