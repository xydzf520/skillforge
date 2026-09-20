import { describe, expect, it } from 'vitest'
import { normalizeGatewayIngestPayload, truthyAutoAnalyze } from '@/pages/project/gatewayPayload'

describe('Project Gateway ingest payload defaults', () => {
  it('defaults project output ingress to platform AI analysis', () => {
    expect(normalizeGatewayIngestPayload({ output: { summary: 'done' } }, 'ing-1')).toEqual({
      request_id: 'ing-1',
      auto_analyze: true,
      output: { summary: 'done' },
    })
  })

  it('keeps explicit opt-out for non-AI sample or batch writes', () => {
    expect(normalizeGatewayIngestPayload({ auto_analyze: false, output: { summary: 'done' } }, 'ing-2')).toMatchObject({
      request_id: 'ing-2',
      auto_analyze: false,
    })
    expect(normalizeGatewayIngestPayload({ autoAnalyze: 'false', output: {} }, 'ing-3')).toMatchObject({
      request_id: 'ing-3',
      auto_analyze: false,
    })
  })

  it('normalizes common auto analyze flags', () => {
    expect(truthyAutoAnalyze(undefined)).toBe(true)
    expect(truthyAutoAnalyze('off')).toBe(false)
    expect(truthyAutoAnalyze('on')).toBe(true)
  })
})
