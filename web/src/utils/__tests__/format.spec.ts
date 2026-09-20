import { describe, expect, it } from 'vitest'
import { formatBjtDateTimeInput, formatDate, formatTime, formatTimeFull, toDate } from '@/utils/format'

describe('Beijing time formatting', () => {
  it('parses naive ISO as Beijing time', () => {
    expect(toDate('2026-05-06T10:30:00')?.toISOString()).toBe('2026-05-06T02:30:00.000Z')
    expect(toDate('2026-05-06')?.toISOString()).toBe('2026-05-05T16:00:00.000Z')
  })

  it('formats timestamps in Beijing time regardless of source offset', () => {
    expect(formatTime('2026-05-06T02:30:00Z')).toBe('2026-05-06 10:30')
    expect(formatTimeFull('2026-05-06T02:30:05Z')).toBe('2026-05-06 10:30:05')
    expect(formatDate('2026-05-05T16:00:00Z')).toBe('2026-05-06')
  })

  it('serializes picker defaults with Beijing wall-clock values', () => {
    expect(formatBjtDateTimeInput(new Date('2026-05-06T02:30:05Z'))).toBe('2026-05-06T10:30:05')
  })
})
