import type { RouteLocationRaw } from 'vue-router'
import type { ProjectRow, ProjectRunRow } from '@/api'

const RESUMABLE_PROJECT_RUN_STATUSES = new Set(['opening', 'running', 'stale', 'waiting_ai'])

export function resumableProjectRunId(run?: ProjectRunRow | null): string {
  const id = String(run?.id || '')
  const status = String(run?.status || '')
  if (!id || !RESUMABLE_PROJECT_RUN_STATUSES.has(status)) return ''
  return id
}

export function projectLatestRunId(project?: ProjectRow | null): string {
  return String(project?.latest_run?.id || '')
}

export function projectRunPath(projectId: string, runId?: string | null): RouteLocationRaw {
  const query: Record<string, string> = {}
  const normalizedRunId = String(runId || '')
  if (normalizedRunId) query.run_id = normalizedRunId
  return { path: `/project-run/${encodeURIComponent(projectId)}`, query }
}

export function projectSharePath(projectId: string): RouteLocationRaw {
  return { path: `/project-run/${encodeURIComponent(projectId)}`, query: { share: '1' } }
}

export function routeWithShareQuery(entryUrl: string): RouteLocationRaw {
  try {
    const url = new URL(entryUrl, 'http://skillforge.local')
    if (url.origin === 'http://skillforge.local') {
      const query: Record<string, string> = Object.fromEntries(url.searchParams.entries())
      query.share = '1'
      return { path: url.pathname || '/', query }
    }
  } catch {
    // fallback below
  }
  return { path: entryUrl || '/', query: { share: '1' } }
}

export function projectSharePathFor(project: ProjectRow): RouteLocationRaw {
  if (project.type === 'playbook' && project.entry_url) return routeWithShareQuery(project.entry_url)
  return projectSharePath(project.id)
}

export function projectOpenPath(project: ProjectRow): RouteLocationRaw {
  return { path: `/project-run/${encodeURIComponent(project.id)}`, query: { view: 'app' } }
}

export function projectTracePath(projectId: string, runId?: string | null): RouteLocationRaw {
  const query: Record<string, string> = {}
  const normalizedRunId = String(runId || '')
  if (normalizedRunId) query.run_id = normalizedRunId
  return { path: `/projects/${encodeURIComponent(projectId)}`, query }
}

export function projectTracePathFor(project: ProjectRow): RouteLocationRaw {
  return projectTracePath(project.id, projectLatestRunId(project))
}
