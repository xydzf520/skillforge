import { describe, it, expect } from 'vitest'
import { formatTime, formatTimeShort, formatTimeFull } from '@/utils/format'

describe('formatTime', () => {
  it('formats valid ISO date', () => {
    const result = formatTime('2026-04-01T10:30:00Z')
    expect(result).toMatch(/2026-04-01/)
    expect(result).toMatch(/\d{2}:\d{2}$/)
  })

  it('returns "-" for null/undefined', () => {
    expect(formatTime(null)).toBe('-')
    expect(formatTime(undefined)).toBe('-')
    expect(formatTime('')).toBe('-')
  })

  it('returns "-" for invalid date', () => {
    expect(formatTime('not-a-date')).toBe('-')
  })
})

describe('formatTimeShort', () => {
  it('formats as MM-DD HH:MM', () => {
    const result = formatTimeShort('2026-04-01T10:30:00Z')
    expect(result).toMatch(/^\d{2}-\d{2} \d{2}:\d{2}$/)
  })

  it('returns "-" for falsy values', () => {
    expect(formatTimeShort(null)).toBe('-')
  })
})

describe('formatTimeFull', () => {
  it('includes seconds', () => {
    const result = formatTimeFull('2026-04-01T10:30:45Z')
    expect(result).toMatch(/\d{2}:\d{2}:\d{2}$/)
  })

  it('returns "-" for falsy values', () => {
    expect(formatTimeFull(null)).toBe('-')
  })
})
