import { describe, expect, it } from 'vitest'
import { projectOpenPath, projectRunPath, projectSharePath, projectSharePathFor, projectTracePathFor, resumableProjectRunId } from '@/pages/project/routes'
import type { ProjectRow, ProjectRunRow } from '@/api'

describe('project route helpers', () => {
  it('opens a project directly in app view without reusing the latest run', () => {
    const project: ProjectRow = {
      id: 'dept-tool',
      latest_run: { id: 'prun_active', status: 'running' },
    }

    expect(projectOpenPath(project)).toEqual({
      path: '/project-run/dept-tool',
      query: { view: 'app' },
    })
  })

  it('creates a fresh app run for terminal latest runs', () => {
    const run: ProjectRunRow = { id: 'prun_done', status: 'ai_completed' }
    expect(resumableProjectRunId(run)).toBe('')
    expect(projectRunPath('dept/tool')).toEqual({
      path: '/project-run/dept%2Ftool',
      query: {},
    })
  })

  it('routes trace view to the latest run when available', () => {
    const project: ProjectRow = {
      id: 'trace-tool',
      latest_run: { id: 'prun_trace', status: 'completed' },
    }

    expect(projectTracePathFor(project)).toEqual({
      path: '/projects/trace-tool',
      query: { run_id: 'prun_trace' },
    })
  })

  it('builds login-protected share links without leaking a user run', () => {
    expect(projectSharePath('dept-tool')).toEqual({
      path: '/project-run/dept-tool',
      query: { share: '1' },
    })
  })

  it('shares playbook project entries without routing through project-run', () => {
    const project: ProjectRow = {
      id: 'playbook:daily_ops',
      type: 'playbook',
      entry_url: '/playbook/daily_ops?mode=run',
    }

    expect(projectSharePathFor(project)).toEqual({
      path: '/playbook/daily_ops',
      query: { mode: 'run', share: '1' },
    })
  })
})
