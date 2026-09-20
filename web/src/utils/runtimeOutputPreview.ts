export type RuntimeTaskPreview = {
  executor?: string | null
  content?: string
  deadline?: string | null
}

export type RuntimeTodoPreview = {
  kind?: string
  title?: string
  summary?: string
  reviewer_role?: string
  reviewers?: string[]
  sla_hours?: number | string
  tasks?: RuntimeTaskPreview[]
}

export type RuntimeReportPreview = {
  channel?: string
  title?: string
  summary?: string
}

export type RuntimeOutputPreview = {
  todos: RuntimeTodoPreview[]
  reports: RuntimeReportPreview[]
}

export function normalizeRuntimeOutputPreview(value: unknown): RuntimeOutputPreview {
  const output = findRuntimeOutput(value)
  return {
    todos: normalizeTodoItems(output?.todos),
    reports: normalizeReportItems(output?.reports),
  }
}

export function normalizeTodoItems(value: unknown): RuntimeTodoPreview[] {
  if (!Array.isArray(value)) return []
  return value
    .filter(isRecord)
    .map((item) => {
      const reviewers = Array.isArray(item.reviewers)
        ? item.reviewers.map((r) => String(r || '').trim()).filter(Boolean)
        : []
      const rawTasks = Array.isArray(item.tasks) ? item.tasks : []
      return {
        kind: String(item.kind || 'review'),
        title: String(item.title || '').trim(),
        summary: String(item.summary || '').trim(),
        reviewer_role: String(item.reviewer_role || '').trim(),
        reviewers,
        sla_hours: typeof item.sla_hours === 'number' || typeof item.sla_hours === 'string'
          ? item.sla_hours
          : undefined,
        tasks: rawTasks.filter(isRecord).map((task) => ({
          executor: task.executor == null ? null : String(task.executor),
          content: String(task.content || '').trim(),
          deadline: task.deadline == null ? null : String(task.deadline),
        })),
      }
    })
}

export function normalizeReportItems(value: unknown): RuntimeReportPreview[] {
  if (!Array.isArray(value)) return []
  return value.filter(isRecord).map((item) => ({
    channel: String(item.channel || '').trim(),
    title: String(item.title || '').trim(),
    summary: String(item.summary || '').trim(),
  }))
}

function findRuntimeOutput(value: unknown): Record<string, unknown> | null {
  const parsed = typeof value === 'string' ? parseJsonFromText(value) : value
  return findRuntimeOutputInParsed(parsed)
}

function findRuntimeOutputInParsed(value: unknown): Record<string, unknown> | null {
  return findRuntimeOutputRecursive(value, new WeakSet<object>())
}

function findRuntimeOutputRecursive(value: unknown, seen: WeakSet<object>): Record<string, unknown> | null {
  if (!value || typeof value !== 'object') return null
  if (seen.has(value)) return null
  seen.add(value)

  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findRuntimeOutputRecursive(item, seen)
      if (found) return found
    }
    return null
  }

  const record = value as Record<string, unknown>
  if (Array.isArray(record.todos) || Array.isArray(record.reports)) return record

  const preferredKeys = ['output', 'result', 'data', 'tool_result', 'toolResult', 'content', 'payload', 'message']
  for (const key of preferredKeys) {
    if (!(key in record)) continue
    const child = record[key]
    const found = typeof child === 'string'
      ? findRuntimeOutputRecursive(parseJsonFromText(child), seen)
      : findRuntimeOutputRecursive(child, seen)
    if (found) return found
  }

  for (const [key, child] of Object.entries(record)) {
    if (preferredKeys.includes(key)) continue
    const found = typeof child === 'string'
      ? findRuntimeOutputRecursive(parseJsonFromText(child), seen)
      : findRuntimeOutputRecursive(child, seen)
    if (found) return found
  }

  return null
}

function parseJsonFromText(text: string): unknown {
  const trimmed = text.trim()
  if (!trimmed) return null
  const direct = tryJsonParse(trimmed)
  if (direct !== null) return direct

  const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i)
  if (fenced) {
    const parsed = tryJsonParse(fenced[1].trim())
    if (parsed !== null) return parsed
  }

  for (const line of trimmed.split(/\r?\n/).reverse()) {
    const parsed = tryJsonParse(line.trim())
    if (parsed !== null) return parsed
  }

  const objectText = extractLastBalancedJsonObject(trimmed)
  return objectText ? tryJsonParse(objectText) : null
}

function tryJsonParse(text: string): unknown {
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

function extractLastBalancedJsonObject(text: string): string {
  const candidates: string[] = []
  for (let start = 0; start < text.length; start += 1) {
    if (text[start] !== '{') continue
    let depth = 0
    let inString = false
    let escaped = false
    for (let i = start; i < text.length; i += 1) {
      const ch = text[i]
      if (inString) {
        if (escaped) {
          escaped = false
        } else if (ch === '\\') {
          escaped = true
        } else if (ch === '"') {
          inString = false
        }
        continue
      }
      if (ch === '"') {
        inString = true
      } else if (ch === '{') {
        depth += 1
      } else if (ch === '}') {
        depth -= 1
        if (depth === 0) {
          candidates.push(text.slice(start, i + 1))
          break
        }
      }
    }
  }
  return candidates[candidates.length - 1] || ''
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}
