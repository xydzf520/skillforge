import { describe, expect, it } from 'vitest'

import { normalizeGptImageResolution } from '@/pages/hall/gptImageParams'

describe('GPT ImageGen parameter compatibility', () => {
  it('keeps supported ratio and resolution pairs', () => {
    expect(normalizeGptImageResolution('1:1', '1K')).toBe('1K')
    expect(normalizeGptImageResolution('4:5', '2K')).toBe('2K')
    expect(normalizeGptImageResolution('16:9', '4K')).toBe('4K')
  })

  it('falls back to 2K for unsupported 1K and 4K pairs', () => {
    expect(normalizeGptImageResolution('16:9', '1K')).toBe('2K')
    expect(normalizeGptImageResolution('1:1', '4K')).toBe('2K')
    expect(normalizeGptImageResolution('4:5', '4K')).toBe('2K')
  })

  it('falls back to 2K for an unknown resolution', () => {
    expect(normalizeGptImageResolution('1:1', '8K')).toBe('2K')
  })
})
