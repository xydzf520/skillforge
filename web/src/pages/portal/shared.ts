import type { Component } from 'vue'
import {
  IconApps, IconBarChart, IconCalendar, IconCloud, IconCode,
  IconDashboard, IconFile, IconSafe, IconSearch, IconSettings,
  IconThunderbolt,
} from '@arco-design/web-vue/es/icon'
import { bjtDateString, formatTime } from '@/utils/format'

type RecordLike = Record<string, any>

export type PortalSchemaField = {
  type?: string
  format?: string
  title?: string
  description?: string
  default?: unknown
  enum?: Array<string | number>
  items?: PortalSchemaField
  minimum?: number
  maximum?: number
  properties?: Record<string, PortalSchemaField>
  required?: string[]
  dependsOn?: {
    field: string
    value?: unknown
    values?: unknown[]
    operator?: 'eq' | 'ne' | 'in' | 'notEmpty'
  }
}

export type PortalParamSchema = {
  type?: string
  required?: string[]
  properties?: Record<string, PortalSchemaField>
}

export type PortalResultColumn = {
  key: string
  title: string
  format?: string
}

export type PortalResultSchema = {
  type?: string
  columns?: PortalResultColumn[]
}

export type PortalUIComponent = {
  id: string
  type: string
  title?: string
  description?: string
  binding?: string
  bindings?: Record<string, string>
  field?: string
  field_type?: string
  columns?: PortalResultColumn[]
  chart_type?: string
  format?: string
  visible?: boolean
  children?: PortalUIComponent[]
}

export type PortalUISchema = {
  schema_version?: string
  skill_id?: string
  surface?: string
  base_skill_commit?: string
  layout?: Record<string, unknown>
  components?: PortalUIComponent[]
  personal_defaults?: Record<string, unknown>
  default_view?: string
}

export type PortalSkillUI = {
  schema_version?: string
  skill_id?: string
  surface?: string
  base_skill_commit?: string
  ui_pref_id?: string | null
  ui_pref_version?: number | null
  overlay?: Record<string, unknown>
  merged_schema?: PortalUISchema
  merged_ui_schema_hash?: string
  ai_status?: 'llm' | 'fallback'
  ai_reason?: string | null
  generated_by?: string | null
  saved_prompt?: string | null
  ui_pref_invalidated?: {
    ui_pref_id?: string | null
    ui_pref_version?: number | null
    reason?: unknown
  } | null
  permissions?: {
    read?: boolean
    execute?: boolean
    customize_ui?: boolean
  }
}

export type FlatField = {
  path: string
  field: PortalSchemaField
  depth: number
}

export function isRecord(value: unknown): value is RecordLike {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function schemaProperties(schema?: PortalParamSchema | PortalSchemaField): Record<string, PortalSchemaField> {
  return isRecord(schema?.properties) ? schema.properties as Record<string, PortalSchemaField> : {}
}

export function normalizeListResponse<T>(res: unknown): T[] {
  if (Array.isArray(res)) return res as T[]
  if (!isRecord(res)) return []
  if (Array.isArray(res.items)) return res.items as T[]
  if (isRecord(res.data) && Array.isArray(res.data.items)) return res.data.items as T[]
  if (Array.isArray(res.data)) return res.data as T[]
  if (Array.isArray(res.results)) return res.results as T[]
  return []
}

export function normalizePagedResponse<T>(res: unknown): { items: T[]; total: number } {
  const items = normalizeListResponse<T>(res)
  const total = isRecord(res) && typeof res.total === 'number' ? res.total : items.length
  return { items, total }
}

export function formatPercent(value: unknown): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return '-'
  const normalized = num <= 1 ? num * 100 : num
  return `${normalized.toFixed(normalized % 1 === 0 ? 0 : 1)}%`
}

export function formatPortalCell(value: unknown, format?: string): string {
  if (value == null || value === '') return '-'
  if (format === 'currency') {
    const num = Number(value)
    if (!Number.isFinite(num)) return String(value)
    return `¥${num.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`
  }
  if (format === 'percent') return formatPercent(value)
  if (format === 'date') return String(value)
  if (format === 'datetime') return formatTime(String(value))
  if (typeof value === 'number') return value.toLocaleString('zh-CN')
  return String(value)
}

export function yesterdayDate(): string {
  return bjtDateString(Date.now(), -1)
}

export function visibilityLabel(value?: string): string {
  const labels: Record<string, string> = {
    company: '全公司',
    department: '本组织',
    private: '私有',
  }
  return labels[value || ''] || value || '-'
}

export function resolvePortalIcon(name?: string): Component {
  const icons: Record<string, Component> = {
    apps: IconApps,
    'bar-chart': IconBarChart,
    calendar: IconCalendar,
    cloud: IconCloud,
    code: IconCode,
    dashboard: IconDashboard,
    file: IconFile,
    safe: IconSafe,
    search: IconSearch,
    settings: IconSettings,
    thunderbolt: IconThunderbolt,
  }
  return icons[name || ''] || IconApps
}

const SECRET_PARAM_WORDS = new Set(['token', 'secret', 'password', 'credential', 'credentials', 'cookie', 'authorization'])
const SECRET_PARAM_COMPACT_MARKERS = [
  'apikey',
  'appkey',
  'accesskey',
  'accesskeys',
  'accesstoken',
  'refreshtoken',
  'authtoken',
  'authheader',
  'mcpenv',
  'dingtalktoken',
  'privatekey',
  'secretkey',
  'serviceaccountkey',
  'signingkey',
  'sshkey',
]
const SECRET_PARAM_COMPACT_SUFFIXES = ['token', 'secret', 'password', 'credential', 'cookie']

export function isSecretLikeParamPath(path: string): boolean {
  const lowered = String(path || '').toLowerCase()
  const parts = lowered.split(/[._:\-\s]+/).filter(Boolean)
  if (parts.some(part => SECRET_PARAM_WORDS.has(part))) return true
  const compact = lowered.replace(/[^a-z0-9]/g, '')
  return SECRET_PARAM_COMPACT_MARKERS.some(marker => compact.includes(marker))
    || SECRET_PARAM_COMPACT_SUFFIXES.some(suffix => compact.endsWith(suffix))
}

export function getSchemaEntries(schema?: PortalParamSchema): Array<[string, PortalSchemaField]> {
  return Object.entries(schemaProperties(schema)).filter(([key]) => !isSecretLikeParamPath(key))
}

export function isRequiredField(schema: PortalParamSchema | undefined, key: string): boolean {
  return Boolean(schema?.required?.includes(key))
}

export function buildInitialParams(schema?: PortalParamSchema): Record<string, unknown> {
  const params: Record<string, unknown> = {}
  for (const [key, field] of getSchemaEntries(schema)) {
    if (field.type === 'object' && isRecord(field.properties)) {
      // 嵌套对象：递归构建初始值
      params[key] = buildInitialParams({
        type: 'object',
        properties: schemaProperties(field),
        required: field.required,
      })
    } else {
      params[key] = initialFieldValue(field)
    }
  }
  return params
}

export function normalizeParamsForSubmit(
  schema: PortalParamSchema | undefined,
  rawParams: Record<string, unknown>,
): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const [key, field] of getSchemaEntries(schema)) {
    if (isSecretLikeParamPath(key)) continue
    const value = rawParams[key]
    if (value === '' || value === undefined) {
      result[key] = value
      continue
    }
    if (field.type === 'object' && isRecord(field.properties) && isRecord(value)) {
      // 嵌套对象：递归 normalize
      result[key] = normalizeParamsForSubmit(
        { type: 'object', properties: schemaProperties(field), required: field.required },
        value as Record<string, unknown>,
      )
      continue
    }
    if (field.type === 'integer') {
      const num = Number(value)
      result[key] = Number.isFinite(num) ? Math.trunc(num) : value
      continue
    }
    if (field.type === 'number') {
      const num = Number(value)
      result[key] = Number.isFinite(num) ? num : value
      continue
    }
    if (field.type === 'array') {
      if (Array.isArray(value)) {
        result[key] = value
        continue
      }
      if (typeof value === 'string') {
        const trimmed = value.trim()
        if (!trimmed) {
          result[key] = []
          continue
        }
        try {
          const parsed = JSON.parse(trimmed)
          result[key] = Array.isArray(parsed) ? parsed : [parsed]
        } catch {
          result[key] = trimmed
            .split(/\n|,/)
            .map(item => item.trim())
            .filter(Boolean)
        }
        continue
      }
    }
    result[key] = value
  }
  for (const [key, value] of Object.entries(rawParams)) {
    if (isSecretLikeParamPath(key)) continue
    if (!(key in result)) result[key] = value
  }
  return result
}

export function initialFieldValue(field: PortalSchemaField): unknown {
  if (field.default !== undefined) {
    if (field.default === 'yesterday') return yesterdayDate()
    return field.default
  }
  if (field.enum?.length) return field.enum[0]
  if (field.format === 'date') return ''
  if (field.type === 'boolean') return false
  if (field.type === 'number' || field.type === 'integer') return undefined
  return ''
}

// ---------------------------------------------------------------------------
// 条件联动
// ---------------------------------------------------------------------------

/**
 * 判断字段是否可见。
 * 如果字段配置了 dependsOn，根据当前表单值和操作符计算可见性。
 * 无 dependsOn 的字段始终可见。
 */
export function isFieldVisible(
  fieldName: string,
  schema: PortalParamSchema | undefined,
  currentValues: Record<string, unknown>,
): boolean {
  if (!schema?.properties) return true
  const field = schema.properties[fieldName]
  if (!field) return true
  return evaluateDependsOn(field, currentValues)
}

/**
 * 内部：根据 dependsOn 配置和当前值计算可见性。
 */
function evaluateDependsOn(
  field: PortalSchemaField,
  currentValues: Record<string, unknown>,
): boolean {
  const dep = field.dependsOn
  if (!dep) return true

  const depValue = getNestedValue(currentValues, dep.field)
  const op = dep.operator || 'eq'

  switch (op) {
    case 'eq':
      return depValue === dep.value
    case 'ne':
      return depValue !== dep.value
    case 'in':
      return Array.isArray(dep.values) && dep.values.includes(depValue)
    case 'notEmpty':
      return depValue !== null && depValue !== undefined && depValue !== ''
    default:
      return true
  }
}

// ---------------------------------------------------------------------------
// 嵌套结构支持
// ---------------------------------------------------------------------------

/**
 * 展平嵌套 schema 为路径-字段列表，支持 object 和 array+items。
 * 返回的 path 用点分隔，如 "address.city"。
 */
export function flattenNestedSchema(schema: PortalParamSchema): {
  flatFields: FlatField[]
} {
  const flatFields: FlatField[] = []

  function walk(
    properties: Record<string, PortalSchemaField> | undefined,
    prefix: string,
    depth: number,
  ) {
    if (!isRecord(properties)) return
    for (const [key, field] of Object.entries(properties)) {
      const path = prefix ? `${prefix}.${key}` : key
      flatFields.push({ path, field, depth })

      if (field.type === 'object' && isRecord(field.properties)) {
        walk(schemaProperties(field), path, depth + 1)
      }
      if (field.type === 'array' && field.items && typeof field.items === 'object') {
        const items = field.items as PortalSchemaField
        if (items.type === 'object' && isRecord(items.properties)) {
          walk(schemaProperties(items), `${path}[]`, depth + 1)
        }
      }
    }
  }

  walk(schemaProperties(schema), '', 0)
  return { flatFields }
}

/**
 * 在嵌套对象上按点分隔路径设置值。
 * 路径不存在的中间节点会自动创建为空对象。
 */
export function setNestedValue(
  obj: Record<string, unknown>,
  path: string,
  value: unknown,
): void {
  const keys = path.split('.')
  let current: Record<string, unknown> = obj
  for (let i = 0; i < keys.length - 1; i++) {
    const key = keys[i]
    if (!isRecord(current[key])) {
      current[key] = {}
    }
    current = current[key] as Record<string, unknown>
  }
  current[keys[keys.length - 1]] = value
}

/**
 * 在嵌套对象上按点分隔路径读取值。
 * 路径中任一节点不存在返回 undefined。
 */
export function getNestedValue(
  obj: Record<string, unknown>,
  path: string,
): unknown {
  const keys = path.split('.')
  let current: unknown = obj
  for (const key of keys) {
    if (!isRecord(current)) return undefined
    current = (current as Record<string, unknown>)[key]
  }
  return current
}

// ---------------------------------------------------------------------------
// 表单预览数据生成
// ---------------------------------------------------------------------------

/**
 * 根据 schema 的 default / enum / type 生成示例数据。
 * 用于预览模式或文档示例。
 */
export function generatePreviewData(
  schema: PortalParamSchema,
): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  if (!schema.properties) return result

  for (const [key, field] of Object.entries(schema.properties)) {
    result[key] = generateFieldPreview(field)
  }
  return result
}

function generateFieldPreview(field: PortalSchemaField): unknown {
  // 有明确默认值的直接使用
  if (field.default !== undefined) {
    if (field.default === 'yesterday') return yesterdayDate()
    return field.default
  }

  // 枚举取第一个
  if (field.enum?.length) return field.enum[0]

  const t = field.type
  switch (t) {
    case 'string':
      if (field.format === 'date') return yesterdayDate()
      return field.title ? `${field.title}示例` : 'example'

    case 'integer':
      return field.minimum != null ? field.minimum : 1

    case 'number':
      return field.minimum != null ? field.minimum : 0.0

    case 'boolean':
      return false

    case 'array': {
      if (field.items) {
        const items = field.items as PortalSchemaField
        const sample = generateFieldPreview(items)
        return [sample]
      }
      return []
    }

    case 'object': {
      if (field.properties) {
        return generatePreviewData({
          type: 'object',
          properties: field.properties,
        })
      }
      return {}
    }

    default:
      return ''
  }
}
