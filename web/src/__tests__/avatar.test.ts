import { describe, expect, it } from 'vitest'
import { userAvatarEmoji, userAvatarUrl } from '@/utils/avatar'

describe('avatar helpers', () => {
  it('prefers uploaded or DingTalk avatar URLs', () => {
    expect(userAvatarUrl({ id: 'u1', avatar_url: 'https://example.com/user.png' })).toBe('https://example.com/user.png')
    expect(userAvatarUrl({ id: 'u2', dingtalk_avatar_url: 'https://example.com/dingtalk.png' })).toBe('https://example.com/dingtalk.png')
    expect(userAvatarUrl({
      id: 'u3',
      avatar_url: 'https://example.com/user-custom.png',
      dingtalk_avatar_url: 'https://example.com/dingtalk-custom.png',
    })).toBe('https://example.com/user-custom.png')
  })

  it('falls back to a stable emoji SVG for accounts without avatar', () => {
    const first = userAvatarUrl({ id: 'zhangsan', name: '张三' })
    const second = userAvatarUrl({ id: 'zhangsan', name: '张三' })

    expect(first).toBe(second)
    expect(first).toMatch(/^data:image\/svg\+xml/)
    expect(decodeURIComponent(first)).toContain('<text')
    expect(decodeURIComponent(first)).toContain(userAvatarEmoji({ id: 'zhangsan', name: '张三' }))
  })
})
