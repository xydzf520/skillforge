import type { ConditionValue, StructuredCondition } from '../types'

function isStructuredCondition(value: unknown): value is StructuredCondition {
  return Boolean(value) && typeof value === 'object' && Array.isArray((value as StructuredCondition).children)
}

export function getConditionLabel(condition: ConditionValue): string {
  if (!condition) return ''
  if (typeof condition === 'string') return condition
  if (typeof condition._preview === 'string' && condition._preview.trim()) return condition._preview

  try {
    return JSON.stringify(condition)
  } catch {
    return ''
  }
}

export function serializeConditionForEditor(condition: ConditionValue): string {
  if (!condition) return ''
  if (typeof condition === 'string') return condition

  try {
    return JSON.stringify(condition, null, 2)
  } catch {
    return ''
  }
}

export function parseConditionInput(input: string): ConditionValue {
  const trimmed = input.trim()
  if (!trimmed) return ''

  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try {
      const parsed = JSON.parse(trimmed) as unknown
      if (isStructuredCondition(parsed)) {
        return parsed
      }
    } catch {
      return trimmed
    }
  }

  return trimmed
}
