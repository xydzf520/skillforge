const gateway = window.SkillForgeProject || window.SFProjectGateway || window.PlatformProjectGateway

function unwrap(response) {
  if (response?.ok === false) throw new Error(response.error || '项目能力调用失败')
  return response?.result ?? response
}

export function isCapabilityQuotaError(error) {
  const text = [error?.message, error?.detail, error?.gatewayResponse?.detail, error?.gatewayResponse?.error?.detail]
    .filter(Boolean).map(value => typeof value === 'string' ? value : JSON.stringify(value)).join(' ')
  return /单次项目运行能力调用已达到平台上限|capability_quota_exceeded|PROJECT_CAPABILITY_QUOTA/i.test(text)
}

export function gatewayAvailable() {
  return Boolean(gateway?.gatewayAvailable?.())
}

export async function ready() {
  if (!gatewayAvailable()) throw new Error('当前为离线预览，未创建真实项目运行。')
  return gateway.ready()
}

export async function capability(capabilityName, input = {}, timeoutMs = 300000) {
  if (!gatewayAvailable()) throw new Error('Project Gateway 未连接')
  const requestId = `${capabilityName}-${Date.now()}-${Math.random().toString(16).slice(2)}`
  let watchdogId
  const watchdog = new Promise((_, reject) => {
    watchdogId = window.setTimeout(() => {
      const error = new Error(`项目能力 ${capabilityName} 响应超时，请重试；本次不会重复提交任务。`)
      error.code = 'PROJECT_CAPABILITY_CLIENT_TIMEOUT'
      reject(error)
    }, Math.max(1000, timeoutMs + 5000))
  })
  try {
    const request = gateway.capability({ capability: capabilityName, input }, { timeoutMs, requestId })
    return unwrap(await Promise.race([request, watchdog]))
  } catch (error) {
    if (isCapabilityQuotaError(error)) error.code = 'PROJECT_CAPABILITY_QUOTA_EXCEEDED'
    throw error
  } finally {
    window.clearTimeout(watchdogId)
  }
}

export async function upload(file, metadata = {}, onProgress) {
  return unwrap(await gateway.assets.upload({ file, metadata }, { timeoutMs: 300000, onProgress }))
}

export async function recordInput(input) {
  return gateway.input({ input }, { timeoutMs: 30000 })
}

export function startHeartbeat() {
  return gateway.startHeartbeat({ intervalMs: 25000, payload: { page: 'material-workbench-v40', visible: !document.hidden } })
}

export async function subscribeMedia(listener, afterCursor = '') {
  if (!gateway?.media?.subscribe) throw new Error('当前 Project Gateway 不支持媒体事件订阅，请刷新平台页面。')
  return gateway.media.subscribe(listener, { afterCursor, timeoutMs: 30000 })
}

export const api = {
  // The first snapshot must hydrate the queue as well as counters.  Waiting
  // for a later SSE state transition leaves an unchanged queue at “正在读取”.
  snapshot: () => capability('material.workbench.snapshot', { include_recent_jobs: true, limit: 60 }, 30000),
  materialRequestCreate: payload => capability('material.request.create', payload, 60000),
  materialRequestUpdate: payload => capability('material.request.update', payload, 60000),
  materialRequestList: payload => capability('material.request.list', payload, 60000),
  materialRequestGet: requestId => capability('material.request.get', { request_id: requestId }, 60000),
  materialRequestSubmit: requestId => capability('material.request.submit', { request_id: requestId }, 60000),
  materialAssetRecommend: payload => capability('material.asset.recommend', payload, 60000),
  materialCandidateGenerate: payload => capability('material.candidate.generate', payload, 600000),
  materialCandidateList: payload => capability('material.candidate.list', payload, 60000),
  materialCandidateGet: candidateId => capability('material.candidate.get', { candidate_id: candidateId }, 60000),
  materialCandidateManifest: (candidateId, compareCandidateId = '') => capability('material.candidate.manifest', { candidate_id: candidateId, compare_candidate_id: compareCandidateId || undefined }, 120000),
  materialCandidateBatchTag: payload => capability('material.candidate.batch_tag', payload, 60000),
  materialCandidateTagUpdate: payload => capability('material.candidate.tag.update', payload, 60000),
  materialReviewSession: (candidateId, filters = {}) => capability('material.review.session', { candidate_id: candidateId, ...filters }, 120000),
  materialAnnotationSave: payload => capability('material.review.annotation.save', payload, 60000),
  materialDecisionSubmit: payload => capability('material.decision.submit', payload, 60000),
  materialBatchReject: payload => capability('material.decision.batch_reject', payload, 120000),
  materialBatchHold: payload => capability('material.decision.batch_hold', payload, 120000),
  materialDeliverySubmit: payload => capability('material.delivery.submit', payload, 120000),
  materialDeliveryGet: candidateId => capability('material.delivery.get', { candidate_id: candidateId }, 60000),
  materialPerformanceSummary: payload => capability('material.performance.summary', payload, 60000),
  materialPerformanceList: payload => capability('material.performance.list', payload, 60000),
  materialWeeklyReport: () => capability('material.weekly_report.get', {}, 60000),
  materialReviewPreferenceSave: preference => capability('material.review.preference.save', { preference }, 60000),
  directSubmit: payload => capability('video.create.direct_submit', payload, 180000),
  optimizePrompt: payload => capability('video.prompt.optimize', payload, 180000),
  agentList: () => capability('video.agent.list', {}, 60000),
  agentGet: agentKey => capability('video.agent.get', { agent_key: agentKey }, 60000),
  agentSave: payload => capability('video.agent.save', payload, 120000),
  agentRestore: agentKey => capability('video.agent.restore', { agent_key: agentKey }, 120000),
  agentInvoke: payload => capability('video.agent.invoke', payload, 300000),
  strategyStart: payload => capability('video.strategy.start', payload, 180000),
  strategyAnswer: payload => capability('video.strategy.answer', payload, 180000),
  strategyCompile: payload => capability('video.strategy.compile', payload, 180000),
  strategySubmit: payload => capability('video.strategy.submit', payload, 180000),
  replayAnalyze: payload => capability('video.replay.analyze', payload, 300000),
  replayUpdate: payload => capability('video.replay.update', payload, 120000),
  replaySubmit: payload => capability('video.replay.submit', payload, 600000),
  replayGet: replayProjectId => capability('video.replay.get', { replay_project_id: replayProjectId }, 120000),
  prepareProduction: payload => capability('video.production.prepare', payload, 180000),
  compileProduction: payload => capability('video.production.compile', payload, 30000),
  submitProduction: payload => capability('video.production.submit', payload, 180000),
  generateFirstFrame: payload => capability('video.first_frame.generate', payload, 600000),
  reviewList: payload => capability('video.review.list', payload),
  cloudReferenceSearch: payload => capability('video.cloud_reference.search', payload),
  cloudReferenceImport: payload => capability('video.cloud_reference.import', payload, 600000),
  planCompare: brief => capability('video.plan_compare', { brief }),
  submitJob: payload => capability('video.job.submit', payload),
  submitLegacyBatch: payload => capability('video.job.batch_submit', payload, 600000),
  cancelLegacyBatch: payload => capability('video.job.batch_cancel', payload),
  jobList: payload => capability('video.job.list', payload),
  jobGet: jobId => capability('video.job.get', { job_id: jobId }),
  themeDiverge: payload => capability('video.theme.diverge', payload),
  libraryList: () => capability('video.asset.library.list', {}),
  libraryUpsert: payload => capability('video.asset.library.upsert', payload),
  libraryArchive: id => capability('video.asset.library.archive', { id }),
  groupList: () => capability('video.asset.group.list', {}),
  groupGet: id => capability('video.asset.group.get', { id }),
  groupUpsert: payload => capability('video.asset.group.upsert', payload),
  groupArchive: id => capability('video.asset.group.archive', { id }),
  workflowList: () => capability('video.workflow.list', {}),
  workflowGet: id => capability('video.workflow.get', { id }),
  workflowUpsert: payload => capability('video.workflow.upsert', payload),
  workflowPublish: id => capability('video.workflow.publish', { id }),
  workflowArchive: id => capability('video.workflow.archive', { id }),
  batchCreate: payload => capability('video.production_batch.create', payload, 600000),
  batchList: () => capability('video.production_batch.list', {}),
  batchGet: batchId => capability('video.production_batch.get', { batch_id: batchId }),
  batchUpdate: payload => capability('video.production_batch.update', payload),
  batchCancel: batchId => capability('video.production_batch.cancel', { batch_id: batchId }),
  continuationPlan: payload => capability('video.continuation.plan', payload, 300000),
  continuationSubmit: payload => capability('video.continuation.submit', payload, 300000),
  continuationGet: chainId => capability('video.continuation.get', { chain_id: chainId }),
  continuationCancel: chainId => capability('video.continuation.cancel', { chain_id: chainId }),
  continuationRetry: (segmentId, confirmInvalidateDownstream = false) => capability('video.continuation.retry_segment', { segment_id: segmentId, confirm_invalidate_downstream: confirmInvalidateDownstream }),
  reviewManifest: (jobId, compareJobId = '') => capability('video.review.manifest', { job_id: jobId, compare_job_id: compareJobId || undefined }),
  retryDialogueDelivery: jobId => capability('video.dialogue_delivery.retry', { job_id: jobId }, 600000),
  enhance1080p: sourceJobId => capability('video.enhance.submit', { source_job_id: sourceJobId, target: '1080p' }, 600000),
  annotationList: jobId => capability('video.review.annotation.list', { job_id: jobId }),
  annotationSave: payload => capability('video.review.annotation.save', payload),
  annotationResolve: annotationId => capability('video.review.annotation.resolve', { annotation_id: annotationId }),
  batchReject: payload => capability('video.review.batch_reject', payload),
  batchReviewUpdate: payload => capability('video.review.batch_update', payload),
  review: payload => capability('video.review', payload),
  qualityAnalyze: jobId => capability('video.quality.analyze', { job_id: jobId, force: true }),
  cancelJob: jobId => capability('video.job.cancel', { job_id: jobId }),
  retryJob: jobId => capability('video.job.retry', { job_id: jobId }),
  cloneJob: payload => capability('video.job.clone', payload),
  cloudSync: jobId => capability('cloud_video.sync', { job_id: jobId }),
}
