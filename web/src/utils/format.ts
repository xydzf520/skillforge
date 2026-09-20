/**
 * 公共工具函数
 *
 * 时区处理：项目业务时间统一按北京时间（Asia/Shanghai）展示和计算。
 * 后端历史接口里仍有手写 `.isoformat()` 返回的 naive ISO 字符串；
 * `toDate()` 会把这类字符串按北京时间解析，显示函数也固定用北京时间格式化，
 * 避免浏览器所在时区影响页面。
 */

export const APP_TIME_ZONE = 'Asia/Shanghai'
export const APP_TIME_ZONE_LABEL = '北京时间'
const BJT_OFFSET = '+08:00'
const NAIVE_DATE_RE = /^\d{4}-\d{2}-\d{2}$/
const NAIVE_DATETIME_RE = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?$/
const TZ_SUFFIX_RE = /(Z|[+-]\d{2}:\d{2})$/i

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

export type BjtDateParts = {
  year: number
  month: number
  day: number
  hour: number
  minute: number
  second: number
}

const bjtDateTimeFormatter = new Intl.DateTimeFormat('en-GB', {
  timeZone: APP_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

export function bjtParts(val: string | number | Date | null | undefined): BjtDateParts | null {
  const d = toDate(val)
  if (!d) return null
  const parts = bjtDateTimeFormatter.formatToParts(d)
  const get = (type: string) => Number(parts.find((part) => part.type === type)?.value || 0)
  return {
    year: get('year'),
    month: get('month'),
    day: get('day'),
    hour: get('hour'),
    minute: get('minute'),
    second: get('second'),
  }
}

export function dateFromBjtParts(
  year: number,
  month: number,
  day: number,
  hour = 0,
  minute = 0,
  second = 0,
): Date {
  return new Date(Date.UTC(year, month - 1, day, hour - 8, minute, second, 0))
}

/**
 * 统一的时间解析：把 naive ISO 字符串当北京时间处理。
 *
 * 规则：字符串匹配 `YYYY-MM-DD[THH:MM:SS[.fff]]` 且结尾没有 `Z` / `+HH:MM` / `-HH:MM` → 补 `+08:00`
 * 其余情况（Date 对象、数字时间戳、带时区字符串）原样传给 `new Date`
 */
export function toDate(val: string | number | Date | null | undefined): Date | null {
  if (val == null || val === '') return null
  if (val instanceof Date) return isNaN(val.getTime()) ? null : val
  if (typeof val === 'number') {
    const d = new Date(val)
    return isNaN(d.getTime()) ? null : d
  }
  let s = String(val).trim()
  if (!s) return null
  if (!TZ_SUFFIX_RE.test(s)) {
    if (NAIVE_DATE_RE.test(s)) {
      s = `${s}T00:00:00${BJT_OFFSET}`
    } else if (NAIVE_DATETIME_RE.test(s)) {
      s = s.replace(' ', 'T')
      if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(s)) s += ':00'
      s += BJT_OFFSET
    }
  }
  const d = new Date(s)
  return isNaN(d.getTime()) ? null : d
}

export function formatDate(val: string | number | Date | null | undefined): string {
  const p = bjtParts(val)
  if (!p) return '-'
  return `${p.year}-${pad(p.month)}-${pad(p.day)}`
}

export function formatTimeOnly(val: string | number | Date | null | undefined): string {
  const p = bjtParts(val)
  if (!p) return '-'
  return `${pad(p.hour)}:${pad(p.minute)}`
}

/**
 * 格式化时间，统一格式：YYYY-MM-DD HH:MM
 */
export function formatTime(val: string | number | Date | null | undefined): string {
  const p = bjtParts(val)
  if (!p) return '-'
  return `${p.year}-${pad(p.month)}-${pad(p.day)} ${pad(p.hour)}:${pad(p.minute)}`
}

/**
 * 格式化时间（短格式）：MM-DD HH:MM
 */
export function formatTimeShort(val: string | number | Date | null | undefined): string {
  const p = bjtParts(val)
  if (!p) return '-'
  return `${pad(p.month)}-${pad(p.day)} ${pad(p.hour)}:${pad(p.minute)}`
}

/**
 * 格式化时间（含秒）：YYYY-MM-DD HH:MM:SS
 */
export function formatTimeFull(val: string | number | Date | null | undefined): string {
  const p = bjtParts(val)
  if (!p) return '-'
  return `${p.year}-${pad(p.month)}-${pad(p.day)} ${pad(p.hour)}:${pad(p.minute)}:${pad(p.second)}`
}

export function formatBjtDateTimeInput(val: string | number | Date = Date.now()): string {
  const p = bjtParts(val)
  if (!p) return ''
  return `${p.year}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}:${pad(p.second)}`
}

export function bjtDateString(val: string | number | Date = Date.now(), offsetDays = 0): string {
  const d = toDate(val)
  if (!d) return ''
  return formatDate(new Date(d.getTime() + offsetDays * 86400000))
}

/**
 * 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前 / M-D（超过 30 天回退到日期）
 */
export function relativeTime(val: string | number | Date | null | undefined): string {
  const d = toDate(val)
  if (!d) return '-'
  const now = Date.now()
  const diff = Math.max(0, now - d.getTime())
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return '刚刚'
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} 分钟前`
  const hour = Math.floor(min / 60)
  if (hour < 24) return `${hour} 小时前`
  const day = Math.floor(hour / 24)
  if (day < 7) return `${day} 天前`
  if (day < 30) return `${Math.floor(day / 7)} 周前`
  const p = bjtParts(d)
  return p ? `${pad(p.month)}-${pad(p.day)}` : '-'
}
