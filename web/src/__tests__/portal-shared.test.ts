import { describe, expect, it } from 'vitest'
import { buildInitialParams, getSchemaEntries, isSecretLikeParamPath, normalizeParamsForSubmit } from '@/pages/portal/shared'

describe('portal schema helpers', () => {
  const schema = {
    type: 'object',
    properties: {
      date: { type: 'string', format: 'date', default: 'yesterday' },
      retries: { type: 'integer' },
      threshold: { type: 'number' },
      tags: { type: 'array' },
    },
  }

  it('builds default params from schema', () => {
    const params = buildInitialParams(schema)
    expect(typeof params.date).toBe('string')
    expect(params.tags).toBe('')
  })

  it('normalizes numeric and array payloads before submit', () => {
    const normalized = normalizeParamsForSubmit(schema, {
      retries: '3',
      threshold: '1.5',
      tags: 'a, b\nc',
    })
    expect(normalized.retries).toBe(3)
    expect(normalized.threshold).toBe(1.5)
    expect(normalized.tags).toEqual(['a', 'b', 'c'])
  })

  it('ignores malformed properties instead of rendering array indexes as fields', () => {
    const malformedSchema = {
      type: 'object',
      properties: [],
    } as any

    expect(getSchemaEntries(malformedSchema)).toEqual([])
    expect(buildInitialParams(malformedSchema)).toEqual({})
    expect(normalizeParamsForSubmit(malformedSchema, { ghost: 'value' })).toEqual({ ghost: 'value' })
  })

  it('treats private key style params as secret-like fields', () => {
    expect(isSecretLikeParamPath('private_key')).toBe(true)
    expect(isSecretLikeParamPath('serviceAccountKey')).toBe(true)
    expect(getSchemaEntries({
      type: 'object',
      properties: {
        keywords: { type: 'string' },
        private_key: { type: 'string' },
      },
    })).toEqual([['keywords', { type: 'string' }]])
  })
})
