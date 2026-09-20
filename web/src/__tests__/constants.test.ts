import { describe, it, expect } from 'vitest'
import {
  skillStatusLabel, skillStatusColor,
  riskColor, riskLabel,
  reviewStatusLabel, reviewStatusColor,
  runStatusLabel, runStatusColor,
  roleLabel,
} from '@/utils/constants'

describe('constants', () => {
  it('skill status labels cover all states', () => {
    expect(Object.keys(skillStatusLabel)).toEqual(['draft', 'shadow', 'active', 'deprecated'])
    expect(Object.keys(skillStatusColor)).toEqual(['draft', 'shadow', 'active', 'deprecated'])
  })

  it('risk levels cover R1-R4', () => {
    for (const r of ['R1', 'R2', 'R3', 'R4'] as const) {
      expect(riskColor[r]).toBeDefined()
      expect(riskLabel[r]).toBeDefined()
    }
  })

  it('review status labels cover all states', () => {
    for (const s of ['pending', 'approved', 'rejected'] as const) {
      expect(reviewStatusLabel[s]).toBeDefined()
      expect(reviewStatusColor[s]).toBeDefined()
    }
  })

  it('run status labels cover all states', () => {
    for (const s of ['success', 'failed', 'running', 'pending', 'completed'] as const) {
      expect(runStatusLabel[s]).toBeDefined()
      expect(runStatusColor[s]).toBeDefined()
    }
  })

  it('role labels include admin and ai_engineer', () => {
    expect(roleLabel.admin).toBe('管理员')
    expect(roleLabel.ai_engineer).toBe('AI工程师')
  })
})
