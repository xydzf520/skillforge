/**
 * 解析 5-field cron 表达式并计算接下来 N 次执行时间。
 *
 * 字段：minute hour day-of-month month day-of-week
 * 支持：* , - / 以及数字，不支持 L/W/# 等扩展语法。
 * 计算口径固定为北京时间，避免浏览器本地时区影响节点定时预览。
 */

import { bjtParts, dateFromBjtParts } from './format'

type CronField = {
  values: number[] // 有效值集（展开后的所有匹配值）
  min: number
  max: number
}

function parseField(token: string, min: number, max: number): number[] {
  const trimmed = token.trim()
  // */
  if (trimmed === '*') {
    const vals: number[] = []
    for (let i = min; i <= max; i++) vals.push(i)
    return vals
  }
  // */step
  const stepMatch = trimmed.match(/^\*\/(\d+)$/)
  if (stepMatch) {
    const step = parseInt(stepMatch[1], 10)
    if (step < 1) return []
    const vals: number[] = []
    for (let i = min; i <= max; i += step) vals.push(i)
    return vals
  }
  // 逗号分隔的多值
  if (trimmed.includes(',')) {
    const vals = new Set<number>()
    for (const part of trimmed.split(',')) {
      for (const v of parseField(part, min, max)) vals.add(v)
    }
    return [...vals].sort((a, b) => a - b)
  }
  // 范围 N-M 或 N-M/step
  const rangeMatch = trimmed.match(/^(\d+)-(\d+)(?:\/(\d+))?$/)
  if (rangeMatch) {
    const lo = parseInt(rangeMatch[1], 10)
    const hi = parseInt(rangeMatch[2], 10)
    const step = rangeMatch[3] ? parseInt(rangeMatch[3], 10) : 1
    if (lo < min || hi > max || step < 1) return []
    const vals: number[] = []
    for (let i = lo; i <= hi; i += step) vals.push(i)
    return vals
  }
  // 单个数字
  const num = parseInt(trimmed, 10)
  if (isNaN(num) || num < min || num > max) return []
  return [num]
}

function nextMatch(vals: number[], current: number): number | null {
  for (const v of vals) {
    if (v >= current) return v
  }
  return null
}

function firstMatch(vals: number[]): number {
  return vals[0]
}

function normalizeDowValues(values: number[]): number[] {
  return [...new Set(values.map((value) => (value === 7 ? 0 : value)))].sort((a, b) => a - b)
}

const DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

function daysInMonth(year: number, month: number): number {
  if (month === 2) {
    return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28
  }
  return DAYS_IN_MONTH[month - 1]
}

export function getNextRuns(cronExpr: string, count: number, from?: Date): Date[] {
  const tokens = cronExpr.trim().split(/\s+/)
  if (tokens.length !== 5) return []

  const minuteField = parseField(tokens[0], 0, 59)
  const hourField = parseField(tokens[1], 0, 23)
  const domField = parseField(tokens[2], 1, 31)
  const monthField = parseField(tokens[3], 1, 12)
  const dowField = normalizeDowValues(parseField(tokens[4], 0, 7)) // 0和7都表示周日

  if (!minuteField.length || !hourField.length || !domField.length || !monthField.length || !dowField.length) {
    return []
  }

  // 判断 dom / dow 是否被限制（非 *）
  const domIsRestricted = tokens[2].trim() !== '*'
  const dowIsRestricted = tokens[4].trim() !== '*'

  const results: Date[] = []
  // 从当前分钟的下一个分钟开始
  const baseMs = from ? from.getTime() : Date.now()
  const start = new Date(Math.floor(baseMs / 60000) * 60000 + 60000)
  const startParts = bjtParts(start)
  if (!startParts) return []

  let year = startParts.year
  let month = startParts.month
  let day = startParts.day
  let hour = startParts.hour
  let minute = startParts.minute

  // 安全上限
  const maxIter = 366 * 24 * 60
  let iter = 0

  while (results.length < count && iter < maxIter) {
    iter++

    // 月份前进
    const m = nextMatch(monthField, month)
    if (m === null) {
      year++
      month = firstMatch(monthField)
      day = 1
      hour = firstMatch(hourField)
      minute = firstMatch(minuteField)
      continue
    }
    if (m > month) {
      month = m
      day = 1
      hour = firstMatch(hourField)
      minute = firstMatch(minuteField)
      continue
    }
    // month == m，继续

    const maxDay = daysInMonth(year, month)

    // 日期前进：区分仅dom限制、仅dow限制、双限制
    let d: number
    if (dowIsRestricted && domIsRestricted) {
      // 双限制：取并集，任意满足即可（POSIX cron 标准）
      let found = false
      for (let dd = day; dd <= maxDay; dd++) {
        const dow = new Date(Date.UTC(year, month - 1, dd)).getUTCDay()
        if (domField.includes(dd) || dowField.includes(dow)) {
          d = dd
          found = true
          break
        }
      }
      if (!found) {
        month++
        day = 1
        hour = firstMatch(hourField)
        minute = firstMatch(minuteField)
        continue
      }
    } else if (dowIsRestricted) {
      // 仅 dow 限制：只按星期匹配
      let found = false
      for (let dd = day; dd <= maxDay; dd++) {
        const dow = new Date(Date.UTC(year, month - 1, dd)).getUTCDay()
        if (dowField.includes(dow)) {
          d = dd
          found = true
          break
        }
      }
      if (!found) {
        month++
        day = 1
        hour = firstMatch(hourField)
        minute = firstMatch(minuteField)
        continue
      }
    } else {
      const dm = nextMatch(domField, day)
      if (dm === null || dm > maxDay) {
        month++
        day = 1
        hour = firstMatch(hourField)
        minute = firstMatch(minuteField)
        continue
      }
      d = dm
    }

    if (d! > day) {
      day = d!
      hour = firstMatch(hourField)
      minute = firstMatch(minuteField)
      continue
    }

    // d == day，继续

    // 小时前进
    const h = nextMatch(hourField, hour)
    if (h === null) {
      day++
      hour = firstMatch(hourField)
      minute = firstMatch(minuteField)
      continue
    }
    if (h > hour) {
      hour = h
      minute = firstMatch(minuteField)
      continue
    }

    // 分钟前进
    const min = nextMatch(minuteField, minute)
    if (min === null) {
      hour++
      minute = firstMatch(minuteField)
      continue
    }
    minute = min

    // 构建日期
    const date = dateFromBjtParts(year, month, day, hour, minute)
    if (date.getTime() >= start.getTime()) {
      results.push(date)
    }
    minute++
  }

  return results
}
