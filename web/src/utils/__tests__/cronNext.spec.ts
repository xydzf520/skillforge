import { describe, it, expect } from 'vitest'
import { getNextRuns } from '@/utils/cronNext'
import { bjtParts, dateFromBjtParts } from '@/utils/format'

function expectBjt(
  value: Date,
  expected: Partial<NonNullable<ReturnType<typeof bjtParts>>>,
) {
  expect(bjtParts(value)).toMatchObject(expected)
}

describe('getNextRuns', () => {
  it('parses "50 10 * * *" (每天 10:50)', () => {
    const from = dateFromBjtParts(2026, 4, 25)
    const runs = getNextRuns('50 10 * * *', 3, from)
    expect(runs).toHaveLength(3)
    expectBjt(runs[0], { day: 25, hour: 10, minute: 50 })
    expectBjt(runs[1], { day: 26, hour: 10, minute: 50 })
    expectBjt(runs[2], { day: 27, hour: 10, minute: 50 })
  })

  it('parses "*/5 * * * *" (每5分钟)', () => {
    const from = dateFromBjtParts(2026, 4, 25, 10)
    const runs = getNextRuns('*/5 * * * *', 3, from)
    expect(runs).toHaveLength(3)
    expectBjt(runs[0], { minute: 5 })
    expectBjt(runs[1], { minute: 10 })
  })

  it('parses "0 * * * *" (每小时整点)', () => {
    const from = dateFromBjtParts(2026, 4, 25, 10, 30)
    const runs = getNextRuns('0 * * * *', 3, from)
    expect(runs).toHaveLength(3)
    expectBjt(runs[0], { hour: 11, minute: 0 })
    expectBjt(runs[1], { hour: 12, minute: 0 })
  })

  it('parses "0 9 * * 1-5" (工作日9:00)', () => {
    // 2026-04-27 is Monday
    const from = dateFromBjtParts(2026, 4, 25, 12) // Saturday
    const runs = getNextRuns('0 9 * * 1-5', 3, from)
    expect(runs).toHaveLength(3)
    // first should be Monday 2026-04-27 09:00
    expectBjt(runs[0], { year: 2026, month: 4, day: 27, hour: 9 })
  })

  it('treats day-of-week 7 as Sunday', () => {
    const from = dateFromBjtParts(2026, 4, 18, 12) // Saturday
    const runs = getNextRuns('0 10 * * 7', 2, from)
    expect(runs).toHaveLength(2)
    expectBjt(runs[0], { year: 2026, month: 4, day: 19, hour: 10, minute: 0 })
    expectBjt(runs[1], { year: 2026, month: 4, day: 26, hour: 10, minute: 0 })
  })

  it('handles comma separated values "0 8,20 * * *"', () => {
    const from = dateFromBjtParts(2026, 4, 25)
    const runs = getNextRuns('0 8,20 * * *', 4, from)
    expect(runs).toHaveLength(4)
    expectBjt(runs[0], { hour: 8 })
    expectBjt(runs[1], { hour: 20 })
    expectBjt(runs[2], { hour: 8 })
  })

  it('returns empty for invalid cron', () => {
    expect(getNextRuns('invalid', 3)).toHaveLength(0)
    expect(getNextRuns('* * * *', 3)).toHaveLength(0)
  })

  it('handles range "0 9-17 * * *"', () => {
    const from = dateFromBjtParts(2026, 4, 25, 8)
    const runs = getNextRuns('0 9-17 * * *', 3, from)
    expectBjt(runs[0], { hour: 9 })
    expectBjt(runs[1], { hour: 10 })
    expectBjt(runs[2], { hour: 11 })
  })

  it('advances the start minute by timestamp, independent of local DST rules', () => {
    const from = new Date('2026-03-08T06:59:30.000Z')
    const runs = getNextRuns('* * * * *', 2, from)
    expectBjt(runs[0], { year: 2026, month: 3, day: 8, hour: 15, minute: 0 })
    expectBjt(runs[1], { year: 2026, month: 3, day: 8, hour: 15, minute: 1 })
  })
})
