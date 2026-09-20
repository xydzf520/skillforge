export const state = {
  ready: false,
  context: null,
  snapshot: { jobs: [], batches: [], continuations: [], nodes: [], status_counts: {}, request_counts: {}, candidate_counts: {}, north_star: {}, training_status: {} },
  materialRequests: [],
  materialRequestCursor: '',
  activeMaterialRequestId: '',
  materialCandidates: [],
  materialCandidateCursor: '',
  activeCandidateId: '',
  reviewMode: 'wall',
  reviewPreference: { sort: 'selection_newest', filters: {}, density: 'comfortable' },
  planComparison: null,
  selectedPlanSource: 'baseline',
  assetLibrary: [],
  assetGroups: [],
  selectedAssets: [],
  selectedAssetGroupId: '',
  workflows: [],
  directions: [],
  continuation: null,
  reviewManifest: null,
  selectedReviewJobId: '',
  compareReviewJobId: '',
  jobFilter: 'all',
  assetFilter: 'all',
  reviewStatusFilter: 'awaiting_review',
  eventCursor: '',
  unsubscribeMedia: null,
}

export function updateSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== 'object') return
  state.snapshot = {
    ...state.snapshot,
    ...snapshot,
    jobs: Array.isArray(snapshot.jobs) ? snapshot.jobs : state.snapshot.jobs,
    batches: Array.isArray(snapshot.batches) ? snapshot.batches : state.snapshot.batches,
    continuations: Array.isArray(snapshot.continuations) ? snapshot.continuations : state.snapshot.continuations,
    nodes: Array.isArray(snapshot.nodes) ? snapshot.nodes : state.snapshot.nodes,
  }
  state.eventCursor = String(snapshot.cursor || state.eventCursor || '')
}

export function activeJobs() {
  return state.snapshot.jobs.filter(job => ['queued', 'assigned', 'running', 'collecting', 'syncing'].includes(job.status))
}

export function reviewJobs() {
  return state.snapshot.jobs.filter(job => state.reviewStatusFilter === 'all' || job.status === state.reviewStatusFilter)
}

export function selectedPlan() {
  const comparison = state.planComparison || {}
  if (state.selectedPlanSource === 'candidate' && comparison.candidate?.available) return comparison.candidate.output
  if (state.selectedPlanSource === 'merged' && comparison.final_output) return comparison.final_output
  return comparison.baseline?.output || comparison.final_output || null
}
