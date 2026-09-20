import {
  api, gatewayAvailable, isCapabilityQuotaError, ready, recordInput, startHeartbeat, subscribeMedia, upload,
} from './api.js'

const $ = id => document.getElementById(id)
const $$ = selector => [...document.querySelectorAll(selector)]
const esc = value => String(value ?? '').replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char])
const activeStatuses = new Set(['queued', 'assigned', 'running', 'collecting'])
const statusNames = { queued: '排队中', assigned: '已分配', running: '生成中', collecting: '收集中', awaiting_review: '待审', approved: '已通过', rejected: '已驳回', syncing: '同步中', synced: '已同步', failed: '失败', cancelled: '已取消' }
const modeNames = { text_to_video: '文生视频', image_to_video: '首帧生成', reference_replay: '参考复刻', continuation: '自动续写', video_enhance: '1080p 高清化' }
const roleNames = { character_first_frame: '人物 / 场景首帧', product_packshot: '真实商品首帧 / 包装', product_detail: '真实商品单片 / 细节', visual_reference: '视觉参考', motion_reference: '原视频 / 结构参考', audio_reference: '参考音频', continuity_anchor: '续写锚点' }
const qualityNames = { prompt_alignment: '提示词一致性', visual_continuity: '视觉连续性', hook_strength: '前 5 秒钩子', shot_boundary_clarity: '镜头清晰度', batch_diversity: '版本差异度', commercial_readiness: '投流可用度', product_fidelity: '商品真实性', selling_point_coverage: '卖点覆盖', silhouette_safety: '轮廓安全' }
const cameraCommands = ['[Static shot]', '[Push in]', '[Pull out]', '[Truck left]', '[Truck right]', '[Tracking shot]', '[Pan left]', '[Pan right]', '[Tilt up]', '[Tilt down]', '[Pedestal up]', '[Pedestal down]', '[Zoom in]', '[Zoom out]', '[Shake]']
const reconnectDelays = [1000, 2000, 5000, 10000, 30000]
const runScope = new URLSearchParams(window.location.search).get('run_id') || window.location.pathname.replace(/[^a-z0-9_-]+/gi, '_')
const draftStorageKey = `sf-material-workbench-v2201-draft:${runScope}`
const reviewListStorageKey = `sf-material-workbench-v21101-reviews:${runScope}`
const reviewManifestStorageKey = `sf-material-workbench-v2150-manifests:${runScope}`
const reviewImageCacheName = `sf-material-workbench-v2150-images-${runScope}`
const reviewManifestCacheTtlMs = 10 * 60 * 1000
const reviewImageObjectUrlLimit = 96
const reviewImageDiskEntryLimit = 240
const exactPromptPolicyVersion = 'material-fde-h3-v1'

const state = {
  connected: false, snapshot: { nodes: [], status_counts: {}, output_presets: { items: [] } },
  assets: [], prepared: null, selectedPreset: 'quick_preview', submitKey: '',
  jobs: [], jobCursor: '', jobHasMore: false, jobFilter: 'active', jobListLoaded: false, jobActiveTotal: 0,
  reviews: [], reviewCursor: '', reviewHasMore: false, reviewJobId: '', reviewManifest: null, reviewRequestToken: 0,
  compareJobId: '', compareSelectionJobId: '', selectedReviewIds: new Set(), eventCursor: '', unsubscribe: null,
  reconnectAttempt: 0, reconnectTimer: null, eventGeneration: 0, eventConnecting: false, heartbeatStop: null,
  assetLibrary: [], assetLibraryLoaded: false, assetLibraryPromise: null,
  assetRecommendation: null, assetRecommendationTimer: null, assetRecommendationRequest: 0,
  lockedRecommendedAssetIds: new Set(), autoAddedRecommendationIds: new Set(),
  autoProduct: '',
  submissionLocked: false, submitting: false, submitPhase: '',
  reviewListLoaded: false, reviewManifestCache: new Map(), reviewImageUrls: new Map(),
  quotaExhausted: false,
  shotEditsDirty: false,
  firstFrameAttempt: null,
  firstFrameAttemptNo: 1,
  productionMode: 'direct',
  strategy: null,
  strategyUi: { sessionId: '', selectedDirectionIds: new Set(), acceptDirectionCScript: false, promptEdits: new Map() },
  strategyAction: { busy: false, message: '', tone: 'info' },
  replay: null,
  replayConfigured: false,
  replayReplacementItems: [],
  directBasePrompt: '',
  directFinalPromptEdited: false,
  promptOptimization: null,
  promptOptimizationBusy: false,
  applyingPromptOptimization: false,
  productionScrollY: 0,
  agents: [],
  agentsLoaded: false,
  selectedAgentKey: 'video_prompt_h3',
  agentDrafts: new Map(),
  dirtyAgents: new Set(),
  materialRequests: [],
  materialRequestCursor: '',
  materialRequestHasMore: false,
  activeMaterialRequest: null,
  candidates: [],
  candidateCursor: '',
  candidateHasMore: false,
  activeCandidateId: '',
  reviewSession: null,
  reviewView: 'pending',
  wallDensity: 'comfortable',
  hoverPreviewTimer: null, activeWallPreviewCard: null, wallPreviewObserver: null, reviewWallScrollY: 0,
  reviewTags: new Set(),
  candidateRefreshTimer: null,
}

function toast(message, kind = '') {
  const node = document.createElement('div')
  node.className = `toast ${kind}`
  node.textContent = message
  $('toastRegion').append(node)
  setTimeout(() => node.remove(), 4500)
}

function requestText({ title, description = '', defaultValue = '', placeholder = '', confirmLabel = '确认', multiline = true }) {
  const dialog = $('textInputDialog')
  const form = $('textInputForm')
  const input = multiline ? $('textInputValue') : $('textInputSingleValue')
  const alternate = multiline ? $('textInputSingleValue') : $('textInputValue')
  $('textInputTitle').textContent = title
  $('textInputDescription').textContent = description
  $('textInputDescription').classList.toggle('hidden', !description)
  $('textInputConfirm').textContent = confirmLabel
  $('textInputError').textContent = ''
  input.classList.remove('hidden')
  alternate.classList.add('hidden')
  input.value = defaultValue
  input.placeholder = placeholder

  return new Promise(resolve => {
    let settled = false
    const finish = value => {
      if (settled) return
      settled = true
      form.onsubmit = null
      $('textInputCancel').onclick = null
      $('textInputCancelFooter').onclick = null
      dialog.onclose = null
      resolve(value)
    }
    form.onsubmit = event => {
      event.preventDefault()
      const value = input.value.trim()
      if (!value) {
        $('textInputError').textContent = '请填写内容后再确认。'
        input.focus()
        return
      }
      finish(value)
      dialog.close('confirm')
    }
    const cancel = () => dialog.close('cancel')
    $('textInputCancel').onclick = cancel
    $('textInputCancelFooter').onclick = cancel
    dialog.onclose = () => finish(null)
    dialog.showModal()
    requestAnimationFrame(() => { input.focus(); input.setSelectionRange(input.value.length, input.value.length) })
  })
}

function errorText(error) {
  if (isCapabilityQuotaError(error)) return '本次工作台运行的操作额度已用完。实时状态和缓存审片仍可查看；请点击“新开工作台”继续生产。'
  const detail = error?.gatewayResponse?.detail || error?.gatewayResponse?.error?.detail || error?.detail
  if (detail?.missing) return `缺少：${detail.missing.join('、')}`
  if (detail?.missing_dialogue) return `优化稿遗漏或改动了原台词：${detail.missing_dialogue.join('、')}`
  if (detail?.missing_preserved_terms) return `优化稿遗漏了保留项：${detail.missing_preserved_terms.join('、')}`
  if (detail?.reason) return detail.reason
  if (typeof detail?.detail === 'string') return detail.detail
  if (typeof detail === 'string') return detail
  return error?.message || '操作失败，请稍后重试'
}

function errorCode(error) {
  return error?.gatewayResponse?.code
    || error?.gatewayResponse?.error?.code
    || error?.code
    || ''
}

function errorDetail(error) {
  return error?.gatewayResponse?.detail
    || error?.gatewayResponse?.error?.detail
    || error?.detail
    || {}
}

function markAssetValidationError(error) {
  if (errorCode(error) !== 'MEDIA_PRODUCT_OVERLAY_TRANSPARENCY_REQUIRED') return false
  const assetId = String(errorDetail(error)?.asset_id || '')
  let matched = false
  for (const item of state.assets) {
    if (!assetId || String(item.asset?.id || '') === assetId) {
      item.validationError = errorText(error)
      matched = true
    }
  }
  if (matched) renderAssets()
  return matched
}

function showRunRecovery() {
  state.quotaExhausted = true
  $('runRecovery').classList.remove('hidden')
  setConnection('offline', '本次运行额度已用完')
  renderQueueLoadError(null, '本次运行额度已用完 · 请重新打开项目')
}

function renderQueueLoadError(error, message = '读取失败 · 点击刷新') {
  $('queueSummary').textContent = message
  if (!state.jobs.length) {
    $('jobTable').innerHTML = `<p class="empty-copy">${esc(message)}</p>`
  }
  if (error && !isCapabilityQuotaError(error)) setConnection('offline', '队列读取失败')
}

async function busy(button, task, success = '') {
  const text = button?.textContent
  if (button) { button.disabled = true; button.textContent = '处理中…' }
  try {
    const result = await task()
    if (success) toast(success, 'success')
    return result
  } catch (error) {
    if (isCapabilityQuotaError(error)) showRunRecovery()
    else toast(errorText(error), 'error')
    throw error
  } finally {
    if (button) { button.disabled = false; button.textContent = text }
  }
}

function newIdempotencyKey() {
  return `material-v21:${crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`}`
}

function selectedAgent() {
  return state.agents.find(item => item.agent_key === state.selectedAgentKey) || null
}

function renderAgentList() {
  const list = $('agentList')
  if (!state.agents.length) {
    list.innerHTML = '<p class="empty-copy">尚未读取到 Agent 配置</p>'
    return
  }
  list.innerHTML = state.agents.map(agent => `
    <button type="button" class="agent-list-item ${agent.agent_key === state.selectedAgentKey ? 'active' : ''}" data-agent-key="${esc(agent.agent_key)}">
      <strong>${esc(agent.display_name)}</strong>
      <small>${esc(agent.description || '')}</small>
      <span>${esc(agent.model || 'deepseek-v4-flash')} · v${Number(agent.version || 1)} · ${agent.is_custom ? '个人版本' : '默认版本'}</span>
    </button>`).join('')
  $$('[data-agent-key]').forEach(button => { button.onclick = () => selectAgent(button.dataset.agentKey) })
}

function renderAgentEditor() {
  const agent = selectedAgent()
  const disabled = !agent
  for (const id of ['agentSystemPrompt', 'saveAgent', 'restoreAgent', 'agentTestRequest', 'invokeAgent']) $(id).disabled = disabled
  if (!agent) return
  const draft = state.agentDrafts.has(agent.agent_key)
    ? state.agentDrafts.get(agent.agent_key)
    : agent.system_prompt || ''
  $('agentEditorTitle').textContent = agent.display_name
  $('agentEditorDescription').textContent = `${agent.description || ''} 编辑只影响你在当前素材工作台中的调用。`
  $('agentModel').textContent = agent.model || 'deepseek-v4-flash'
  $('agentVersion').textContent = `v${Number(agent.version || 1)}`
  $('agentCustomBadge').textContent = agent.is_custom ? '个人版本' : '默认版本'
  $('agentSystemPrompt').value = draft
  $('agentSaveStatus').textContent = state.dirtyAgents.has(agent.agent_key)
    ? '有未保存修改；实际调用仍使用已保存版本'
    : `已保存 · ${String(agent.prompt_sha256 || '').slice(0, 10)}`
  renderAgentList()
}

function selectAgent(agentKey) {
  if (!state.agents.some(item => item.agent_key === agentKey)) return
  state.selectedAgentKey = agentKey
  $('agentTestResult').textContent = '等待试运行'
  renderAgentEditor()
}

async function loadAgents({ force = false } = {}) {
  if (state.agentsLoaded && !force) return
  $('agentLoadStatus').textContent = '正在读取个人 Agent 配置…'
  const result = await api.agentList()
  state.agents = Array.isArray(result.agents) ? result.agents : []
  state.agentsLoaded = true
  if (!state.agents.some(item => item.agent_key === state.selectedAgentKey)) {
    state.selectedAgentKey = state.agents[0]?.agent_key || ''
  }
  for (const agent of state.agents) {
    if (!state.dirtyAgents.has(agent.agent_key)) state.agentDrafts.set(agent.agent_key, agent.system_prompt || '')
  }
  $('agentLoadStatus').textContent = `已加载 ${state.agents.length} 个 Agent · 个人配置自动持久化`
  renderAgentEditor()
}

function replaceAgent(updated) {
  const index = state.agents.findIndex(item => item.agent_key === updated.agent_key)
  if (index >= 0) state.agents.splice(index, 1, updated)
  else state.agents.push(updated)
  state.agentDrafts.set(updated.agent_key, updated.system_prompt || '')
  state.dirtyAgents.delete(updated.agent_key)
  renderAgentEditor()
}

async function saveCurrentAgent() {
  const agent = selectedAgent()
  if (!agent) return
  const prompt = $('agentSystemPrompt').value.trim()
  if (!prompt) return toast('Agent 指令不能为空', 'error')
  const result = await busy($('saveAgent'), () => api.agentSave({ agent_key: agent.agent_key, system_prompt: prompt }), 'Agent 新版本已保存')
  replaceAgent(result.agent)
}

async function restoreCurrentAgent() {
  const agent = selectedAgent()
  if (!agent) return
  if (!window.confirm(`恢复“${agent.display_name}”的系统默认指令？当前个人修改会被替换，并保留新的版本号。`)) return
  const result = await busy($('restoreAgent'), () => api.agentRestore(agent.agent_key), '已恢复系统默认 Agent')
  replaceAgent(result.agent)
}

async function invokeCurrentAgent() {
  const agent = selectedAgent()
  if (!agent) return
  if (state.dirtyAgents.has(agent.agent_key)) return toast('请先保存当前 Agent 修改，再试运行，避免预览与实际版本不一致', 'error')
  const request = $('agentTestRequest').value.trim()
  if (!request) return toast('先填写一段试运行需求', 'error')
  $('agentTestResult').textContent = 'Agent 正在生成预览…'
  const result = await busy($('invokeAgent'), () => api.agentInvoke({ agent_key: agent.agent_key, request }), 'Agent 试运行完成')
  $('agentTestResult').textContent = JSON.stringify({ agent: result.agent, actual_model: result.actual_model, result: result.result }, null, 2)
}

function isoLocal(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—' }
function seconds(value, fallback = '等待同规格实测') { return value ? `${Math.round(Number(value))} 秒` : fallback }
function elapsed(value) {
  const total = Math.max(0, Number(value || 0))
  if (!total) return '尚无心跳'
  if (total >= 3600) return `${Math.floor(total / 3600)} 小时 ${Math.floor((total % 3600) / 60)} 分钟`
  if (total >= 60) return `${Math.floor(total / 60)} 分钟`
  return `${Math.round(total)} 秒`
}
function assetUrl(asset) { return asset?.download_url || asset?.asset?.download_url || '' }
function orientedPresetParams(params = {}, ratio = $('ratio')?.value || '9:16') {
  let width = Number(params.width || 480)
  let height = Number(params.height || 864)
  if (ratio === '16:9' && width < height) [width, height] = [height, width]
  if (ratio === '9:16' && width > height) [width, height] = [height, width]
  return { ...params, width, height, ratio }
}

function estimateScriptSeconds(value) {
  const lines = String(value || '').split(/\r?\n/).map(item => item.trim()).filter(Boolean)
  const spoken = lines.map(item => item.replace(/^[^：:\n]{1,8}[：:]\s*/, '')).join('\n')
  const chineseOrDigits = (spoken.match(/[\u3400-\u9fff0-9]/g) || []).length
  const latinWords = (spoken.match(/[A-Za-z]+(?:['’-][A-Za-z]+)?/g) || []).length
  const punctuation = (spoken.match(/[，。！？!?；;、…]/g) || []).length
  return Math.round((chineseOrDigits / 4 + latinWords / 2.5 + punctuation * .08 + Math.max(0, lines.length - 1) * .25) * 10) / 10
}

function renderScriptTimingHint() {
  const value = $('script').value.trim()
  const node = $('scriptTimingHint')
  if (!$('audioEnabled').checked) {
    node.textContent = '音频已关闭；本镜头不会使用口播台词，输入内容会保留供再次启用音频时使用。'
    node.classList.remove('warning')
    return
  }
  if (!value) { node.textContent = '未填写台词；可直接生成纯画面镜头。'; node.classList.remove('warning'); return }
  const estimated = estimateScriptSeconds(value)
  const duration = Number($('duration').value || 5)
  const fits = estimated <= duration * .9
  node.textContent = fits
    ? `口播预计约 ${estimated} 秒，适合当前 ${duration} 秒镜头。`
    : `完整台词预计约 ${estimated} 秒；系统提交时会按完整语义回合适配当前镜头，完整口播请改用更长时长或 30 秒自动续写。`
  node.classList.toggle('warning', !fits)
}

function selectedSchedule() { return document.querySelector('input[name="schedule"]:checked')?.value || 'immediate' }
function selectedPreset() {
  return state.snapshot.output_presets?.items?.find(item => item.id === state.selectedPreset)
    || { id: 'quick_preview', label: '极速试片', params: { width: 480, height: 864 } }
}

function defaultRole(file) {
  if (file.type.startsWith('video/')) return 'motion_reference'
  if (file.type.startsWith('audio/')) return 'audio_reference'
  return 'visual_reference'
}

function productFromAssetHints() {
  const hints = state.assets.map(item => [item.file?.name, item.asset?.metadata?.cloud_video_title, item.asset?.metadata?.cloud_video_category].filter(Boolean).join(' ')).join(' ').toLowerCase()
  const rules = [
    [['魔力玻玻', '玻尿酸', '水感'], '示例品牌 魔力玻玻'],
    [['超快感', '快感套', '酥麻'], '示例品牌 超快感'],
    [['持久', '延时'], '示例品牌 持久'],
    [['001', '隐形套'], '示例品牌 001 隐形系列'],
    [['air', '空气套', '铂金'], '示例品牌 AIR / 铂金'],
  ]
  return rules.find(([keywords]) => keywords.some(keyword => hints.includes(keyword)))?.[1] || ''
}

function productFromBusinessHints() {
  const text = `${$('script').value} ${$('requirement').value}`.toLowerCase()
  const rules = [
    [['魔力玻玻', '玻尿酸', '水感'], '示例品牌 魔力玻玻'],
    [['超快感', '快感套', '酥麻'], '示例品牌 超快感'],
    [['持久', '延时'], '示例品牌 持久'],
    [['001', '隐形套'], '示例品牌 001 隐形系列'],
    [['air', '空气套', '铂金'], '示例品牌 AIR / 铂金'],
  ]
  return rules.find(([keywords]) => keywords.some(keyword => text.includes(keyword)))?.[1] || ''
}

function applyDetectedContentFlags() {
  const script = $('script').value.trim()
  const visualPrompt = $('requirement').value.trim()
  const speakerTurns = script.split(/\r?\n/).filter(line => /^[^：:\n]{1,8}[：:]/.test(line.trim())).length
  const visiblePersonCue = /(人物|人像|真人|男人|女人|男性|女性|男生|女生|男士|女士|情侣|夫妻|闺蜜|朋友|模特|演员|医生|主播|口播|双人|单人|person|people|human|man|woman|couple|model|actor|doctor)/i.test(visualPrompt)
  if (state.assets.some(item => item.role === 'character_first_frame') || speakerTurns >= 1 || visiblePersonCue) {
    $('containsPerson').checked = true
  }
  if ($('audioEnabled').checked && script) $('containsVoice').checked = true
}

function applySafeDefaults() {
  applyDetectedContentFlags()
}

function assetDisplayName(item = {}) {
  return item.file?.name || item.libraryRecord?.name || item.asset?.file_name || '素材'
}

function assetMentionToken(item = {}, index = 0) {
  const name = assetDisplayName(item).replace(/\.[^.]+$/, '').normalize('NFKC')
  const stem = name.replace(/\s+/g, '-').replace(/[^\p{L}\p{N}_-]+/gu, '').slice(0, 48)
  const base = stem || `素材${index + 1}`
  const selectedIndex = state.assets.indexOf(item)
  if (selectedIndex < 0) return `@${base}`
  const duplicateNumber = state.assets.slice(0, selectedIndex).filter(candidate => {
    const candidateName = assetDisplayName(candidate).replace(/\.[^.]+$/, '').normalize('NFKC')
    return candidateName.replace(/\s+/g, '-').replace(/[^\p{L}\p{N}_-]+/gu, '').slice(0, 48) === base
  }).length + 1
  return `@${base}${duplicateNumber > 1 ? `-${duplicateNumber}` : ''}`
}

function sourceRoles() {
  return state.assets.map((item, index) => ({
    asset_id: item.asset.id,
    role: item.role,
    purpose: roleNames[item.role],
    role_locked: item.roleLocked === true,
    mention_token: assetMentionToken(item, index),
  }))
}

function exactSourceBindings() {
  const counters = { Picture: 0, Video: 0, Audio: 0 }
  return state.assets.map(item => {
    const pictureRoles = new Set(['character_first_frame', 'product_packshot', 'product_detail', 'visual_reference'])
    const kind = pictureRoles.has(item.role) ? 'Picture' : (item.role === 'audio_reference' ? 'Audio' : 'Video')
    counters[kind] += 1
    return { kind, token: `<${kind} ${counters[kind]}>`, purpose: roleNames[item.role] || item.role }
  })
}

function exactPromptPreview() {
  const visual = String($('requirement').value || '').trim()
  const script = String($('script').value || '').trim()
  const parts = visual ? [visual] : []
  if (script) parts.push(`台词（原文，逐字保持）：\n${script}`)
  if ($('containsPerson').checked && $('commercialAppearance').checked) {
    parts.push('人物商业质感（用户已开启）：成年人物具有上镜且有辨识度的真实五官；女性采用精致自然的商业妆容、清晰眉眼和自然唇色，男性采用整洁发型、干净修容和上镜仪态；保留真实皮肤纹理、轻微不对称、真实毛发和发丝，避免蜡像皮、过度磨皮、网红滤镜脸或合成式 AI 脸。')
  }
  const references = exactSourceBindings().map(item => `${item.token}${item.purpose ? `：${item.purpose}` : ''}`)
  if (references.length) parts.push(`素材引用：\n${references.join('\n')}`)
  return parts.join('\n\n')
}

function exactReferenceTokenValidation(prompt) {
  const expected = [...new Set(exactSourceBindings().map(item => item.token))]
  const actual = [...new Set(String(prompt || '').match(/<(?:Picture|Video|Audio)\s+[1-9][0-9]*>/g) || [])]
  const expectedSet = new Set(expected)
  const actualSet = new Set(actual)
  const missing = expected.filter(token => !actualSet.has(token))
  const unexpected = actual.filter(token => !expectedSet.has(token))
  if (!missing.length && !unexpected.length) return { valid: true, message: '' }
  const messages = []
  if (unexpected.length) {
    messages.push(`提示词引用了 ${unexpected.join('、')}，但没有对应的已上传素材。请上传素材并确认用途，或删除这些占位符。`)
  }
  if (missing.length) {
    messages.push(`最终提示词缺少已确认素材的占位符：${missing.join('、')}。请恢复占位符，或移除该素材。`)
  }
  return { valid: false, message: messages.join(' ') }
}

function sha256TextFallback(value) {
  // Project pages can still be served over an internal HTTP origin where the
  // Web Crypto subtle API is intentionally unavailable.  Keep the preview
  // hash deterministic in that environment instead of making the primary CTA
  // fail before it can show an error.
  const bytes = new TextEncoder().encode(value)
  const bitLength = bytes.length * 8
  const paddedLength = Math.ceil((bytes.length + 9) / 64) * 64
  const padded = new Uint8Array(paddedLength)
  padded.set(bytes)
  padded[bytes.length] = 0x80
  const view = new DataView(padded.buffer)
  const high = Math.floor(bitLength / 0x100000000)
  const low = bitLength >>> 0
  view.setUint32(paddedLength - 8, high)
  view.setUint32(paddedLength - 4, low)

  const constants = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]
  const hash = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]
  const rotateRight = (word, amount) => (word >>> amount) | (word << (32 - amount))
  const words = new Uint32Array(64)
  for (let offset = 0; offset < paddedLength; offset += 64) {
    for (let index = 0; index < 16; index += 1) words[index] = view.getUint32(offset + index * 4)
    for (let index = 16; index < 64; index += 1) {
      const s0 = rotateRight(words[index - 15], 7) ^ rotateRight(words[index - 15], 18) ^ (words[index - 15] >>> 3)
      const s1 = rotateRight(words[index - 2], 17) ^ rotateRight(words[index - 2], 19) ^ (words[index - 2] >>> 10)
      words[index] = (words[index - 16] + s0 + words[index - 7] + s1) >>> 0
    }
    let [a, b, c, d, e, f, g, h] = hash
    for (let index = 0; index < 64; index += 1) {
      const sum1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25)
      const choose = (e & f) ^ (~e & g)
      const temp1 = (h + sum1 + choose + constants[index] + words[index]) >>> 0
      const sum0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22)
      const majority = (a & b) ^ (a & c) ^ (b & c)
      const temp2 = (sum0 + majority) >>> 0
      h = g; g = f; f = e; e = (d + temp1) >>> 0
      d = c; c = b; b = a; a = (temp1 + temp2) >>> 0
    }
    hash[0] = (hash[0] + a) >>> 0; hash[1] = (hash[1] + b) >>> 0
    hash[2] = (hash[2] + c) >>> 0; hash[3] = (hash[3] + d) >>> 0
    hash[4] = (hash[4] + e) >>> 0; hash[5] = (hash[5] + f) >>> 0
    hash[6] = (hash[6] + g) >>> 0; hash[7] = (hash[7] + h) >>> 0
  }
  return hash.map(word => word.toString(16).padStart(8, '0')).join('')
}

async function sha256Text(value) {
  if (globalThis.crypto?.subtle) {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
    return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('')
  }
  return sha256TextFallback(value)
}

function businessInfoPayload() {
  return {
    product: $('product').value.trim(),
    platform: $('platform').value,
    selling_points: $('sellingPoints').value.trim(),
    promotion: $('promotion').value.trim(),
  }
}

function businessParticipationPayload() {
  return {
    product: $('productParticipation').value,
    platform: $('platformParticipation').value,
    selling_points: $('sellingPointsParticipation').value,
    promotion: $('promotionParticipation').value,
  }
}

function materialProductionMode() {
  return state.productionMode === 'strategy' ? 'ai_strategy' : state.productionMode
}

function materialRequestPayload() {
  const business = businessInfoPayload()
  const participation = businessParticipationPayload()
  const fallbackTitle = business.product || String($('requirement').value || '').trim().slice(0, 42) || '未命名素材需求'
  return {
    source: $('requestSource').value,
    title: $('requestTitle').value.trim() || fallbackTitle,
    priority: Number($('requestPriority').value || 100),
    deadline_at: $('requestDeadline').value || undefined,
    business,
    generation_participation: {
      product: participation.product === 'ai_strategy',
      sku: participation.product === 'ai_strategy',
      platform: participation.platform === 'ai_strategy',
      selling_points: participation.selling_points === 'ai_strategy',
      promotion: participation.promotion === 'ai_strategy',
    },
    visual_prompt: $('requirement').value.trim(),
    script: $('script').value.trim(),
    source_roles: sourceRoles(),
    production_mode: materialProductionMode(),
    quantity: Number($('quantity').value || 1),
    duration_seconds: Number($('duration').value || 5),
    ratio: $('ratio').value,
    output_preset_id: state.selectedPreset,
    allow_ai_optimization: state.productionMode === 'strategy',
    allow_script_changes: state.productionMode === 'strategy' && state.strategyUi.acceptDirectionCScript === true,
    generation_options: {
      commercial_appearance: $('commercialAppearance').checked,
    },
  }
}

function renderCurrentMaterialRequest() {
  const request = state.activeMaterialRequest
  $('currentRequestState').textContent = request
    ? `${request.id} · ${request.title} · ${{ draft: '草稿', pending: '待处理', producing: '生产中', awaiting_selection: '待选片', partially_selected: '部分选用', selected: '已选用', rejected: '已驳回', delivered: '已交付', spending: '已产生消耗' }[request.status] || request.status}`
    : '尚未保存为需求'
}

function renderMaterialRequestPool() {
  const statusLabels = { draft: '草稿', pending: '待处理', producing: '生产中', awaiting_selection: '待选片', partially_selected: '部分选用', selected: '已选用', rejected: '已驳回', delivered: '已交付', spending: '已产生消耗' }
  $('materialRequestPool').innerHTML = state.materialRequests.length
    ? state.materialRequests.map(item => `<button type="button" class="request-pool-item ${item.id === state.activeMaterialRequest?.id ? 'active' : ''}" data-material-request="${esc(item.id)}"><span><strong>${esc(item.title)}</strong><small>${esc(item.source)} · ${item.deadline_at ? `截止 ${esc(isoLocal(item.deadline_at))}` : '未设截止时间'}</small></span><b>${esc(statusLabels[item.status] || item.status)}</b></button>`).join('')
    : '<p class="empty-copy">当前没有可见需求；填写表单后点击“保存需求”。</p>'
  $$('[data-material-request]').forEach(button => button.onclick = () => selectMaterialRequest(button.dataset.materialRequest))
}

function applyMaterialRequest(request) {
  if (!request) return
  state.activeMaterialRequest = request
  $('requestSource').value = request.source || 'editorial'
  $('requestPriority').value = String(request.priority || 100)
  $('requestDeadline').value = request.deadline_at ? String(request.deadline_at).slice(0, 16) : ''
  $('requestTitle').value = request.title || ''
  $('requirement').value = request.visual_prompt || ''
  $('script').value = request.script || ''
  $('product').value = request.business?.product || ''
  if (request.business?.platform && [...$('platform').options].some(option => option.value === request.business.platform)) $('platform').value = request.business.platform
  $('sellingPoints').value = request.business?.selling_points || ''
  $('promotion').value = request.business?.promotion || ''
  const participation = request.generation_participation || {}
  $('productParticipation').value = participation.product ? 'ai_strategy' : 'task_management'
  $('platformParticipation').value = participation.platform ? 'ai_strategy' : 'task_management'
  $('sellingPointsParticipation').value = participation.selling_points ? 'ai_strategy' : 'task_management'
  $('promotionParticipation').value = participation.promotion ? 'ai_strategy' : 'task_management'
  if (['direct', 'ai_strategy', 'replay'].includes(request.production_mode)) setProductionMode(request.production_mode === 'ai_strategy' ? 'strategy' : request.production_mode)
  if (request.duration_seconds) $('duration').value = String(Math.min(15, request.duration_seconds))
  if (request.ratio && [...$('ratio').options].some(option => option.value === request.ratio)) $('ratio').value = request.ratio
  if (request.quantity) $('quantity').value = String(request.quantity)
  if (request.output_preset_id) state.selectedPreset = request.output_preset_id
  $('commercialAppearance').checked = request.generation_options?.commercial_appearance !== false
  renderCurrentMaterialRequest(); renderMaterialRequestPool(); updateFormSummary()
}

async function selectMaterialRequest(requestId) {
  const result = await api.materialRequestGet(requestId)
  applyMaterialRequest(result.request)
  $('materialRequestPool').classList.add('hidden')
}

async function loadMaterialRequests() {
  const result = await api.materialRequestList({ limit: 30 })
  state.materialRequests = result.items || []
  state.materialRequestCursor = result.next_cursor || ''
  state.materialRequestHasMore = Boolean(result.has_more)
  renderMaterialRequestPool()
  return result
}

async function saveMaterialRequest({ submit = false, forceNew = false } = {}) {
  const payload = materialRequestPayload()
  if (!payload.visual_prompt && payload.production_mode !== 'replay') throw new Error('请先填写画面提示词')
  let result
  const current = state.activeMaterialRequest
  const unchangedLockedRequest = current && current.status === 'producing'
    && current.production_mode === payload.production_mode
    && String(current.visual_prompt || '').trim() === payload.visual_prompt
    && String(current.script || '').trim() === payload.script
    && JSON.stringify(current.source_roles || []) === JSON.stringify(payload.source_roles || [])
    && Number(current.quantity || 1) === payload.quantity
    && Number(current.duration_seconds || 5) === payload.duration_seconds
    && String(current.ratio || '') === payload.ratio
    && String(current.output_preset_id || '') === payload.output_preset_id
  if (!forceNew && current && ['draft', 'pending', 'rejected'].includes(current.status)) {
    result = await api.materialRequestUpdate({ request_id: current.id, ...payload })
  } else if (!forceNew && unchangedLockedRequest) {
    result = { request: current }
  } else {
    result = await api.materialRequestCreate({
      ...payload,
      idempotency_key: `material-request:${runScope}:${state.submitKey || newIdempotencyKey()}`,
    })
  }
  state.activeMaterialRequest = result.request
  if (submit && state.activeMaterialRequest.status === 'draft') {
    result = await api.materialRequestSubmit(state.activeMaterialRequest.id)
    state.activeMaterialRequest = result.request
  }
  const index = state.materialRequests.findIndex(item => item.id === state.activeMaterialRequest.id)
  if (index >= 0) state.materialRequests[index] = state.activeMaterialRequest
  else state.materialRequests.unshift(state.activeMaterialRequest)
  renderCurrentMaterialRequest(); renderMaterialRequestPool()
  return state.activeMaterialRequest
}

function resetMaterialRequest() {
  state.activeMaterialRequest = null
  state.strategy = null
  state.replay = null
  state.replayConfigured = false
  state.submissionLocked = false
  state.submitKey = newIdempotencyKey()
  for (const id of ['requestTitle', 'requestDeadline', 'product', 'sellingPoints', 'promotion', 'script', 'requirement']) $(id).value = ''
  $('requestSource').value = 'editorial'
  $('requestPriority').value = '100'
  state.assets = []
  state.assetRecommendation = null
  state.lockedRecommendedAssetIds.clear()
  state.autoAddedRecommendationIds.clear()
  $('commercialAppearance').checked = true
  renderAssets(); renderAssetRecommendation(); invalidatePlan(); renderCurrentMaterialRequest(); renderMaterialRequestPool(); updateFormSummary()
  toast('已新建空白素材需求', 'success')
}

function renderExactPromptPreview() {
  if (state.productionMode !== 'direct') return
  const prompt = exactPromptPreview()
  $('planResult').classList.toggle('hidden', !prompt)
  $('planResultTitle').textContent = 'Bridge 执行提示词预览'
  const optimized = state.promptOptimization?.adopted === true
  $('plannerModel').textContent = optimized ? `${state.promptOptimization.model || 'deepseek-v4-flash'} 优化` : '原文直出'
  $('visionModel').textContent = optimized ? '用户已确认采用' : '无 AI 改写'
  $('policyVersion').textContent = optimized ? state.promptOptimization.policy_version : exactPromptPolicyVersion
  if (prompt !== state.directBasePrompt) {
    state.directBasePrompt = prompt
    state.directFinalPromptEdited = false
    $('compiledPrompt').value = prompt
  } else if (!state.directFinalPromptEdited) {
    $('compiledPrompt').value = prompt
  }
  $('compiledPrompt').readOnly = false
  $('promptEditStatus').textContent = state.directFinalPromptEdited
    ? '已手工修改；当前文本会原样发送到 Bridge。'
    : optimized
      ? '已采用 H3 优化稿；这里看到的内容会原样发送到 Bridge。'
      : '这里看到的内容会原样发送到 Bridge；业务字段默认不参与生成。'
  $('shotList').innerHTML = ''
  const warnings = []
  if (state.assets.some(item => !item.roleLocked)) {
    warnings.push('素材用途尚未确认。点击每个素材右侧的“确认用途”后才能生成。')
  }
  const tokenValidation = exactReferenceTokenValidation($('compiledPrompt').value)
  if (!tokenValidation.valid) warnings.push(tokenValidation.message)
  $('planWarnings').innerHTML = warnings.map(message => `<div class="warning">${escapeHtml(message)}</div>`).join('')
  $('directionGrid').innerHTML = ''
}

function renderPromptOptimization() {
  const item = state.promptOptimization
  const panel = $('promptOptimizationPanel')
  const visible = Boolean(item) && state.productionMode === 'direct'
  panel.classList.toggle('hidden', !visible)
  $('optimizePromptButton').classList.toggle('hidden', state.productionMode !== 'direct')
  if (!visible) return
  const candidate = item.edited_candidate_visual_prompt ?? item.optimized_visual_prompt ?? ''
  $('promptOptimizationTitle').textContent = item.adopted
    ? '已采用优化稿，可继续修改或恢复原文'
    : '是否使用优化后的提示词？'
  const agent = item.agent || {}
  $('promptOptimizationMeta').textContent = `${agent.display_name || '视频提示词 Agent'} v${Number(agent.version || 1)} · ${item.model || agent.model || 'deepseek-v4-flash'}`
  $('promptOptimizationSummary').textContent = item.summary || '只整理时间线、动作、镜头、声音和约束；原台词与明确保留项不变。'
  $('promptOptimizationOriginal').textContent = item.original_visual_prompt || ''
  if ($('promptOptimizationCandidate').value !== candidate) $('promptOptimizationCandidate').value = candidate
  const details = [
    ...(item.modifications || []).map(change => ({
      title: change.field || '表达优化',
      text: [change.before ? `原文：${change.before}` : '', change.after ? `优化：${change.after}` : '', change.reason ? `原因：${change.reason}` : ''].filter(Boolean).join('\n'),
    })),
    ...(item.preserved_items || []).map(value => ({ title: '明确保留', text: value })),
    ...(item.assumptions || []).map(value => ({ title: '最小必要假设', text: value })),
    ...(item.warnings || []).map(value => ({ title: '需要注意', text: value })),
  ]
  $('promptOptimizationDetails').innerHTML = details.length
    ? details.map(detail => `<div class="prompt-optimization-detail"><strong>${esc(detail.title)}</strong>${esc(detail.text).replace(/\n/g, '<br>')}</div>`).join('')
    : '<p class="empty-copy">没有额外假设；只调整了提示词结构。</p>'
  panel.classList.toggle('adopted', Boolean(item.adopted))
  $('adoptOptimizedPrompt').textContent = item.adopted ? '已采用优化稿' : '采用优化稿'
  $('keepOriginalPrompt').textContent = item.adopted ? '恢复原文' : '保留原文'
  $('optimizePromptButton').innerHTML = item.adopted ? '<span aria-hidden="true">✓</span> 已优化' : '<span aria-hidden="true">✦</span> 优化提示词'
}

async function optimizePrompt() {
  if (state.promptOptimizationBusy) return
  const visualPrompt = $('requirement').value.trim()
  if (!visualPrompt) return toast('先填写画面提示词，再进行优化', 'error')
  if (state.assets.some(item => !item.roleLocked)) return toast('请先确认每个素材的用途，再优化提示词', 'error')
  const button = $('optimizePromptButton')
  state.promptOptimizationBusy = true
  button.setAttribute('aria-busy', 'true')
  try {
    const result = await busy(button, () => api.optimizePrompt({
      visual_prompt: visualPrompt,
      script: $('script').value,
      source_roles: sourceRoles(),
      duration_seconds: Number($('duration').value || 5),
      ratio: $('ratio').value,
    }))
    state.promptOptimization = {
      ...(result.optimization || {}),
      adopted: false,
      edited_candidate_visual_prompt: result.optimization?.optimized_visual_prompt || '',
    }
    renderPromptOptimization()
    $('promptOptimizationPanel').scrollIntoView({ behavior: 'smooth', block: 'center' })
    toast('优化稿已生成，请比较后选择是否采用', 'success')
    saveDraft()
  } catch (error) {
    toast(`提示词优化失败：${errorText(error)}`, 'error')
    return null
  } finally {
    state.promptOptimizationBusy = false
    button.removeAttribute('aria-busy')
  }
}

function keepOriginalPrompt() {
  const item = state.promptOptimization
  if (!item) return
  if (item.adopted) {
    state.applyingPromptOptimization = true
    $('requirement').value = item.original_visual_prompt || ''
    state.applyingPromptOptimization = false
    invalidatePlan()
  }
  state.promptOptimization = null
  renderPromptOptimization()
  renderExactPromptPreview()
  saveDraft()
  toast('已保留原文，生成时不会使用 AI 优化稿', 'success')
}

function adoptOptimizedPrompt() {
  const item = state.promptOptimization
  if (!item) return
  const candidate = $('promptOptimizationCandidate').value.trim()
  if (!candidate) return toast('优化稿不能为空', 'error')
  if (candidate.length > 7000) return toast('优化稿不能超过 7000 个字符', 'error')
  state.applyingPromptOptimization = true
  $('requirement').value = candidate
  state.applyingPromptOptimization = false
  item.edited_candidate_visual_prompt = candidate
  item.adopted = true
  item.user_edited_after_optimization = candidate !== item.optimized_visual_prompt
  invalidatePlan()
  renderPromptOptimization()
  renderExactPromptPreview()
  saveDraft()
  toast('已采用优化稿；生成前仍可在画面提示词中继续修改', 'success')
}

function setProductionMode(mode) {
  if (!['direct', 'strategy', 'replay'].includes(mode)) return
  state.productionMode = mode
  $$('[data-production-mode]').forEach(button => button.classList.toggle('active', button.dataset.productionMode === mode))
  $('productionForm').classList.remove('mode-direct', 'mode-strategy', 'mode-replay')
  $('productionForm').classList.add(`mode-${mode}`)
  $('strategyPanel').classList.toggle('hidden', mode !== 'strategy')
  $('replayPanel').classList.toggle('hidden', mode !== 'replay')
  $('prepareButton').classList.toggle('hidden', mode !== 'strategy')
  $('submitButton').classList.toggle('hidden', mode === 'replay')
  $('submitButton').textContent = mode === 'replay'
    ? (state.replayConfigured ? '确认并提交复刻' : '预览复刻提示词')
    : mode === 'strategy' ? '预览所选方向提示词' : '直接生成'
  if (mode === 'direct') renderExactPromptPreview()
  else if (mode === 'strategy') {
    $('planResult').classList.add('hidden')
    renderStrategy()
  } else if (mode === 'replay') {
    $('planResult').classList.add('hidden')
    renderReplay()
    syncReplayModeControls()
  }
  renderPromptOptimization()
  updateFormSummary()
}

function selectedStrategyDirections() {
  const checked = $$('[data-strategy-direction]:checked').map(input => input.value)
  return checked.length ? checked : [...state.strategyUi.selectedDirectionIds]
}

function strategyCompiledDirectionIds() {
  return (state.strategy?.compiled_plans || []).map(item => String(item.direction_id || '')).filter(Boolean)
}

function selectedCompiledStrategyDirections() {
  const compiled = new Set(strategyCompiledDirectionIds())
  const selected = selectedStrategyDirections().filter(directionId => compiled.has(directionId))
  return selected.length ? selected : [...compiled]
}

function clearStrategySubmissionFeedback() {
  state.submissionLocked = false
  $('submitReceipt').classList.add('hidden')
  $('submitReceipt').textContent = ''
  $('prepareError').classList.add('hidden')
  $('prepareError').textContent = ''
}

function strategySubmitButtonLabel() {
  if (state.submissionLocked) return '已加入生产'
  if (state.submitting) return state.submitPhase || '正在提交…'
  if (!state.strategy?.compiled_plans?.length) return '预览所选方向提示词'
  const count = selectedCompiledStrategyDirections().length
  return count ? `提交 ${count} 个方向生成` : '请选择要生成的方向'
}

function ensureStrategyUi(strategy) {
  const sessionId = String(strategy?.id || '')
  if (state.strategyUi.sessionId === sessionId) return state.strategyUi
  state.strategyUi = {
    sessionId,
    selectedDirectionIds: new Set(strategy?.selected_direction_ids || []),
    acceptDirectionCScript: false,
    promptEdits: new Map(),
  }
  return state.strategyUi
}

function strategyDirectionTitle(item = {}) {
  const id = String(item.id || '').trim()
  const title = String(item.title || '').trim()
  return id ? title.replace(new RegExp(`^${id}\\s*[·.、:：-]+\\s*`, 'i'), '').trim() || title : title
}

function renderStrategyActionState() {
  const action = state.strategyAction || { busy: false, message: '', tone: 'info' }
  const feedback = $('strategyActionFeedback')
  feedback.textContent = action.message || ''
  feedback.className = `strategy-action-feedback ${action.message ? '' : 'hidden'} ${action.tone || 'info'} ${action.busy ? 'busy' : ''}`.trim()
  feedback.setAttribute('aria-busy', action.busy ? 'true' : 'false')
  const controls = [...$$('[data-strategy-action]'), ...$$('[data-regenerate-strategy]'), $('prepareButton')].filter(Boolean)
  controls.forEach(button => {
    button.disabled = Boolean(action.busy)
    button.setAttribute('aria-busy', action.busy ? 'true' : 'false')
  })
}

async function runStrategyAction(pendingMessage, successMessage, task) {
  if (state.strategyAction.busy) {
    toast('上一项 AI 操作仍在处理中，请稍候', 'error')
    return null
  }
  state.strategyAction = { busy: true, message: pendingMessage, tone: 'info' }
  renderStrategyActionState()
  try {
    const result = await task()
    state.strategyAction = { busy: false, message: successMessage, tone: 'success' }
    renderStrategyActionState()
    if (successMessage) toast(successMessage, 'success')
    return result
  } catch (error) {
    if (isCapabilityQuotaError(error)) showRunRecovery()
    const message = `操作失败：${errorText(error)}`
    state.strategyAction = { busy: false, message, tone: 'error' }
    renderStrategyActionState()
    toast(message, 'error')
    throw error
  }
}

function renderStrategy() {
  const strategy = state.strategy
  if (!strategy) {
    $('strategyDirectionGrid').innerHTML = ''
    $('strategyStatus').textContent = '点击“生成三个方向”后在这里选择一个或多个方案。'
    renderStrategyActionState()
    return
  }
  const strategyUi = ensureStrategyUi(strategy)
  $('strategyStatus').textContent = `实际规划模型：${strategy.model || 'deepseek-v4-flash'} · 第 ${strategy.round_no || 0}/${strategy.max_rounds || 5} 轮`
  const selected = strategyUi.selectedDirectionIds
  $('strategyDirectionGrid').innerHTML = (strategy.directions || []).map(item => `
    <article class="strategy-card ${selected.has(item.id) ? 'selected' : ''}">
      <label class="strategy-card-select"><input type="checkbox" data-strategy-direction value="${esc(item.id)}" ${selected.has(item.id) ? 'checked' : ''}><span>选择方向 ${esc(item.id)}</span></label>
      <h3><span>${esc(item.id)}</span>${esc(strategyDirectionTitle(item))}</h3>
      <div class="strategy-card-body">
        <p class="strategy-card-summary">${esc(item.summary)}</p>
        <dl><div><dt>场景</dt><dd>${esc(item.scene)}</dd></div><div><dt>表演</dt><dd>${esc(item.performance)}</dd></div></dl>
        ${item.id === 'C' && item.suggested_script && item.suggested_script !== item.original_script
          ? `<details class="script-diff"><summary>查看 C 的台词建议（默认不采用）</summary><strong>原台词与建议台词差异</strong>${(item.script_diff || []).filter(line => line.changed).map(line => `<div><del>${esc(line.original || '（无）')}</del><ins>${esc(line.suggested || '（删除）')}</ins></div>`).join('') || `<p>${esc(item.suggested_script)}</p>`}</details><label class="strategy-script-accept"><input id="acceptDirectionCScript" type="checkbox" ${strategyUi.acceptDirectionCScript ? 'checked' : ''}> <span>确认采用 C 的新台词</span></label>`
          : '<small class="strategy-script-preserved">原台词逐字保持</small>'}
      </div>
      <div class="strategy-card-footer"><button type="button" class="button text" data-regenerate-strategy="${esc(item.id)}">重新生成当前方向</button></div>
    </article>`).join('') + '<div class="strategy-actions"><button id="askStrategyQuestions" type="button" class="button secondary" data-strategy-action>让 AI 追问缺失信息</button><button id="compileStrategyNow" type="button" class="button primary" data-strategy-action>跳过追问，生成最终方案</button></div>'
  $$('[data-strategy-direction]').forEach(input => input.onchange = () => {
    if (input.checked) strategyUi.selectedDirectionIds.add(input.value)
    else strategyUi.selectedDirectionIds.delete(input.value)
    input.closest('.strategy-card').classList.toggle('selected', input.checked)
    clearStrategySubmissionFeedback()
    updateFormSummary()
  })
  $('acceptDirectionCScript')?.addEventListener('change', event => {
    strategyUi.acceptDirectionCScript = event.target.checked
    clearStrategySubmissionFeedback()
    updateFormSummary()
  })
  $$('[data-regenerate-strategy]').forEach(button => button.onclick = () => startStrategy({ refreshDirectionId: button.dataset.regenerateStrategy }).catch(() => {}))
  $('askStrategyQuestions')?.addEventListener('click', () => answerStrategyQuestions(false).catch(() => {}))
  $('compileStrategyNow')?.addEventListener('click', () => compileStrategyPlans().catch(() => {}))
  $('strategyQuestions').innerHTML = strategy.questions?.length ? `
    <h3>AI 需要确认</h3>${strategy.questions.map(question => `
      <fieldset class="strategy-question" data-strategy-question="${esc(question.id)}"><legend>${esc(question.label)}</legend>
      ${(question.options || []).map(option => `<label><input type="${question.multiple ? 'checkbox' : 'radio'}" name="question-${esc(question.id)}" value="${esc(typeof option === 'string' ? option : option.value)}"> ${esc(typeof option === 'string' ? option : option.label)}</label>`).join('')}
    </fieldset>`).join('')}
    <div><button id="submitStrategyAnswers" type="button" class="button primary" data-strategy-action>提交答案</button><button id="skipStrategyQuestions" type="button" class="button secondary" data-strategy-action>跳过并生成方案</button></div>` : ''
  $('strategyQuestions').classList.toggle('hidden', !strategy.questions?.length)
  $('submitStrategyAnswers')?.addEventListener('click', () => answerStrategyQuestions(false).catch(() => {}))
  $('skipStrategyQuestions')?.addEventListener('click', () => compileStrategyPlans().catch(() => {}))
  renderStrategyCompiledPlans()
  renderStrategyActionState()
}

function renderStrategyCompiledPlans() {
  const plans = state.strategy?.compiled_plans || []
  const pendingNotice = state.submissionLocked ? '' : `<div class="strategy-compile-notice"><strong>最终提示词已生成，但还没有提交生产</strong><span>确认或修改下方内容后，点击页面底部“${esc(strategySubmitButtonLabel())}”。</span></div>`
  $('strategyCompiledPlans').innerHTML = plans.length ? `${pendingNotice}<h3>最终 H3 提示词（可直接修改）</h3>${plans.map(item => `
    <article class="strategy-compiled-plan"><strong>方向 ${esc(item.direction_id)} · ${esc(item.title)}</strong>
      <details class="strategy-change-summary"><summary>查看 AI 修改与假设</summary>
        ${(item.plan?.modifications || []).map(change => `<div><b>${esc(change.field)}</b><p>原文：${esc(change.before || '（空）')}</p><p>方案：${esc(change.after || '（空）')}</p></div>`).join('')}
        ${(item.plan?.assumptions || []).length ? `<div><b>AI 使用的假设</b><ul>${item.plan.assumptions.map(value => `<li>${esc(value)}</li>`).join('')}</ul></div>` : '<p>未使用额外假设。</p>'}
      </details>
      <textarea data-strategy-prompt="${esc(item.direction_id)}" maxlength="7000">${esc(state.strategyUi.promptEdits.get(item.direction_id) ?? item.final_prompt)}</textarea>
    </article>`).join('')}` : ''
  $$('[data-strategy-prompt]').forEach(textarea => textarea.oninput = () => {
    state.strategyUi.promptEdits.set(textarea.dataset.strategyPrompt, textarea.value)
    clearStrategySubmissionFeedback()
    updateFormSummary()
  })
}

async function startStrategy({ refresh = false, refreshDirectionId = '' } = {}) {
  if (!$('requirement').value.trim()) return toast('先填写画面提示词', 'error')
  clearStrategySubmissionFeedback()
  const pendingMessage = refreshDirectionId
    ? `正在重新生成方向 ${refreshDirectionId}，完成前不会重复提交…`
    : refresh ? '正在重新生成三个场景方向，完成前不会重复提交…' : '正在生成三个场景方向，请稍候…'
  const successMessage = refreshDirectionId ? `方向 ${refreshDirectionId} 已更新` : '三个场景方向已生成'
  return runStrategyAction(pendingMessage, successMessage, async () => {
    const request = await saveMaterialRequest({ submit: true })
    const result = await api.materialCandidateGenerate({
      request_id: request.id,
      mode: 'ai_strategy',
      action: 'start',
      generation: {
        refresh,
        ...(refreshDirectionId ? {
          strategy_session_id: state.strategy?.id,
          refresh_direction_id: refreshDirectionId,
        } : {}),
      },
    })
    state.strategy = result.strategy
    ensureStrategyUi(state.strategy)
    renderStrategy()
    return result
  })
}

function strategyAnswerPayload() {
  const answers = {}
  $$('[data-strategy-question]').forEach(fieldset => {
    answers[fieldset.dataset.strategyQuestion] = [...fieldset.querySelectorAll('input:checked')].map(input => input.value)
  })
  return answers
}

async function answerStrategyQuestions(skip = false) {
  if (!state.strategy) return startStrategy()
  const selected = selectedStrategyDirections()
  if (!selected.length) return toast('至少选择一个场景方向', 'error')
  return runStrategyAction(skip ? '正在记录跳过追问的选择…' : '正在提交答案并完善场景方向…', skip ? '已跳过追问' : '答案已提交，场景方向已更新', async () => {
    const request = await saveMaterialRequest({ submit: true })
    const result = await api.materialCandidateGenerate({ request_id: request.id, mode: 'ai_strategy', action: 'answer', generation: {
      strategy_session_id: state.strategy.id, selected_direction_ids: selected,
      answers: strategyAnswerPayload(), skip,
    } })
    state.strategy = result.strategy
    renderStrategy()
    return result
  })
}

async function compileStrategyPlans() {
  if (!state.strategy) await startStrategy()
  const selected = selectedStrategyDirections()
  if (!selected.length) return toast('至少选择一个场景方向', 'error')
  clearStrategySubmissionFeedback()
  const acceptDirectionCScript = state.strategyUi.acceptDirectionCScript || $('acceptDirectionCScript')?.checked === true
  return runStrategyAction('正在确认所选方向并生成最终 H3 提示词…', '最终提示词已生成，可修改后提交', async () => {
    const request = await saveMaterialRequest({ submit: true })
    const answerResult = await api.materialCandidateGenerate({ request_id: request.id, mode: 'ai_strategy', action: 'answer', generation: {
      strategy_session_id: state.strategy.id, selected_direction_ids: selected,
      answers: strategyAnswerPayload(), skip: true,
    } })
    state.strategy = answerResult.strategy
    const result = await api.materialCandidateGenerate({ request_id: request.id, mode: 'ai_strategy', action: 'compile', generation: {
      strategy_session_id: state.strategy.id, selected_direction_ids: selected,
      accept_direction_c_script: acceptDirectionCScript, output_preset_id: state.selectedPreset,
      params: { seed: Number($('seed').value), audio_enabled: $('audioEnabled').checked },
    } })
    state.strategy = result.strategy
    state.strategyUi.promptEdits = new Map()
    renderStrategy()
    updateFormSummary()
    $('strategyCompiledPlans').scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    return result
  })
}

async function submitStrategyPlans() {
  if (state.submitting || state.submissionLocked) return
  if (!state.strategy?.compiled_plans?.length) {
    await compileStrategyPlans()
    return
  }
  const selected = selectedCompiledStrategyDirections()
  if (!selected.length) return toast('至少选择一个已经预览的场景方向', 'error')
  const selectedSet = new Set(selected)
  const plans = $$('[data-strategy-prompt]')
    .filter(textarea => selectedSet.has(textarea.dataset.strategyPrompt))
    .map(textarea => ({ direction_id: textarea.dataset.strategyPrompt, final_prompt: textarea.value.trim() }))
  if (plans.some(item => !item.final_prompt)) return toast('最终 H3 提示词不能为空', 'error')

  state.submitting = true
  state.submitPhase = `正在提交 ${selected.length} 个方向…`
  $('prepareError').classList.add('hidden')
  $('prepareError').textContent = ''
  updateFormSummary()
  try {
    const request = await saveMaterialRequest({ submit: true })
    const result = await api.materialCandidateGenerate({ request_id: request.id, mode: 'ai_strategy', action: 'submit', generation: {
      strategy_session_id: state.strategy.id, selected_direction_ids: selected, plans,
    } })
    const jobs = result.jobs || []
    if (!jobs.length) throw new Error('平台没有返回生产任务，未进行重复提交。')
    mergeJobs(jobs)
    renderJobs()
    state.submissionLocked = true
    renderStrategyCompiledPlans()
    const ids = jobs.map(job => job.id).filter(Boolean)
    const running = jobs.filter(job => activeStatuses.has(job.status)).length
    $('submitReceipt').innerHTML = `<strong>已提交 ${jobs.length} 个场景方向</strong><span>${ids.length ? `任务 ${ids.map(esc).join('、')}` : '任务已进入队列'}；${running ? `${running} 条正在排队或生成。` : `当前状态：${jobs.map(job => statusNames[job.status] || job.status).join('、')}。`}</span><button id="openSubmittedJobs" type="button" class="button secondary">查看生产队列</button>`
    $('submitReceipt').classList.remove('hidden')
    $('openSubmittedJobs').onclick = () => setQueueOpen(true)
    toast(`已提交 ${jobs.length} 个场景方向`, 'success')
    $('submitReceipt').scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  } catch (error) {
    if (isCapabilityQuotaError(error)) showRunRecovery()
    else toast(errorText(error), 'error')
    $('prepareError').textContent = `提交失败：${errorText(error)}。最终提示词仍保留，可以修改后重试；系统不会重复创建任务。`
    $('prepareError').classList.remove('hidden')
    $('prepareError').scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    throw error
  } finally {
    state.submitting = false
    state.submitPhase = ''
    updateFormSummary()
  }
}

function replaySourceAsset() {
  return state.assets.find(item => String(item.asset?.mime_type || item.file?.type || '').startsWith('video/'))
}

function newReplayReplacement() {
  return {
    id: `replace-${crypto.randomUUID?.() || Date.now()}`,
    scope: 'visual_audio', original_text: '', replacement_text: '',
    start_seconds: 0, end_seconds: '', region_hint: '',
    preserve_visual_style: true, preserve_voice: true,
  }
}

function readReplayReplacementItems({ includeEmpty = false } = {}) {
  return $$('[data-replay-replacement]').map(node => {
    const start = Number(node.querySelector('[data-replacement-start]').value || 0)
    const endValue = node.querySelector('[data-replacement-end]').value
    return {
      id: node.dataset.replayReplacement,
      scope: node.querySelector('[data-replacement-scope]').value,
      original_text: node.querySelector('[data-replacement-original]').value.trim(),
      replacement_text: node.querySelector('[data-replacement-new]').value.trim(),
      start_seconds: Number.isFinite(start) ? start : 0,
      end_seconds: endValue === '' ? null : Number(endValue),
      region_hint: node.querySelector('[data-replacement-region]').value.trim(),
      preserve_visual_style: node.querySelector('[data-replacement-visual-style]').checked,
      preserve_voice: node.querySelector('[data-replacement-voice]').checked,
    }
  }).filter(item => includeEmpty || item.original_text || item.replacement_text)
}

function syncReplayReplacementControls() {
  const items = readReplayReplacementItems({ includeEmpty: true })
  state.replayReplacementItems = items
  const complete = items.filter(item => item.original_text && item.replacement_text)
  const hasAudioTarget = complete.some(item => ['audio_only', 'visual_audio'].includes(item.scope))
  const hasVisualTarget = complete.some(item => ['visual_only', 'visual_audio'].includes(item.scope))
  if (hasAudioTarget && $('replayAudio').value === 'original') $('replayAudio').value = 'partial_redub'
  if (hasVisualTarget && $('replayText').value === 'keep') $('replayText').value = 'targeted'
  syncReplayActionState()
}

function renderLocalReplacementEditor() {
  const localReplace = document.querySelector('input[name="replayMode"]:checked')?.value === 'local_replace'
  $('localReplacementPanel').classList.toggle('hidden', !localReplace)
  if (!localReplace) return
  if (!state.replayReplacementItems.length) {
    const saved = Array.isArray(state.replay?.config?.replacement_items) ? state.replay.config.replacement_items : []
    state.replayReplacementItems = saved.length ? saved : [newReplayReplacement()]
  }
  $('localReplacementList').innerHTML = state.replayReplacementItems.map((item, index) => `
    <article class="local-replacement-row" data-replay-replacement="${esc(item.id || `replace-${index + 1}`)}">
      <div class="local-replacement-row-head"><strong>替换项 ${index + 1}</strong>${state.replayReplacementItems.length > 1 ? '<button type="button" class="button text" data-remove-replacement>移除</button>' : ''}</div>
      <div class="local-replacement-grid">
        <label>替换范围<select data-replacement-scope>
          <option value="visual_audio" ${item.scope === 'visual_audio' ? 'selected' : ''}>画面与口播同步替换</option>
          <option value="visual_only" ${item.scope === 'visual_only' ? 'selected' : ''}>只改画面文字</option>
          <option value="audio_only" ${item.scope === 'audio_only' ? 'selected' : ''}>只改口播</option>
        </select></label>
        <label>原内容<input data-replacement-original value="${esc(item.original_text || '')}" placeholder="例如：已抢购 3000 单"></label>
        <label>替换为<input data-replacement-new value="${esc(item.replacement_text || '')}" placeholder="例如：已抢购 5000 单"></label>
        <label>开始秒<input data-replacement-start type="number" min="0" step="0.1" value="${Number(item.start_seconds || 0)}"></label>
        <label>结束秒<input data-replacement-end type="number" min="0" step="0.1" value="${item.end_seconds == null ? '' : Number(item.end_seconds)}" placeholder="默认到视频结尾"></label>
        <label>位置提示<input data-replacement-region value="${esc(item.region_hint || '')}" placeholder="可不填，如：画面下方 / 商品堆头"></label>
      </div>
      <div class="local-replacement-options">
        <label><input data-replacement-visual-style type="checkbox" ${item.preserve_visual_style !== false ? 'checked' : ''}> 画面文字沿用原字号、颜色、位置和运动</label>
        <label><input data-replacement-voice type="checkbox" ${item.preserve_voice !== false ? 'checked' : ''}> 口播沿用原说话人声线、语气和节奏</label>
      </div>
    </article>`).join('')
  $$('[data-replay-replacement] input, [data-replay-replacement] select').forEach(input => {
    input.oninput = syncReplayReplacementControls
    input.onchange = syncReplayReplacementControls
  })
  $$('[data-remove-replacement]').forEach(button => button.onclick = () => {
    const row = button.closest('[data-replay-replacement]')
    state.replayReplacementItems = readReplayReplacementItems({ includeEmpty: true }).filter(item => item.id !== row.dataset.replayReplacement)
    renderLocalReplacementEditor()
    syncReplayActionState()
  })
  syncReplayActionState()
}

function syncReplayActionState() {
  const mode = document.querySelector('input[name="replayMode"]:checked')?.value || 'full_structure'
  const localReplace = mode === 'local_replace'
  const hasReplay = Boolean(state.replay)
  const items = localReplace ? readReplayReplacementItems({ includeEmpty: true }) : []
  const incomplete = items.some(item => Boolean(item.original_text) !== Boolean(item.replacement_text))
  const hasTarget = items.some(item => item.original_text && item.replacement_text)
  const hasOtherLocalEdit = $('replayText').value === 'remove' || $('replayProduct').value !== 'keep'
  const blocked = !hasReplay || (localReplace && (incomplete || (!hasTarget && !hasOtherLocalEdit)))
  $('previewReplay').disabled = blocked
  $('generateReplay').disabled = blocked
  $('generateReplay').textContent = localReplace ? '生成替换视频' : '生成完整复刻'
  $('generateReplay').title = !hasReplay
    ? '先分析完整源视频'
    : incomplete
      ? '每个替换项都要同时填写原内容和新内容'
      : localReplace && !hasTarget && !hasOtherLocalEdit
        ? '填写至少一个替换项，或选择去除文字 / 替换商品'
        : ''
}

function renderReplay() {
  const replay = state.replay
  if (!replay) {
    $('replayTimeline').innerHTML = ''
    $('replayStatus').textContent = '尚未分析'
    renderLocalReplacementEditor()
    syncReplayActionState()
    return
  }
  const localReplace = replay.mode === 'local_replace'
  const matchingMode = document.querySelector(`input[name="replayMode"][value="${replay.mode}"]`)
  if (matchingMode) matchingMode.checked = true
  for (const id of ['replayPeople', 'replayScene', 'replayCamera']) $(id).disabled = localReplace
  const replayAgent = replay.analysis?.agent || {}
  const agentLabel = replayAgent.display_name
    ? ` · ${replayAgent.display_name} v${Number(replayAgent.version || 1)}`
    : ''
  $('replayStatus').textContent = localReplace
    ? `${Number(replay.source_duration_seconds || 0).toFixed(1)} 秒 · 原片遮罩跟踪与局部替换${agentLabel}`
    : `${Number(replay.source_duration_seconds || 0).toFixed(1)} 秒 · ${(replay.segments || []).length} 段 · 完整覆盖${agentLabel}`
  const executionPreview = replay.analysis?.execution_prompt_preview
    ? `<article class="replay-execution-preview"><strong>Bridge 局部替换指令预览</strong><pre>${esc(replay.analysis.execution_prompt_preview)}</pre></article>`
    : ''
  $('replayTimeline').innerHTML = executionPreview + (localReplace ? `
    <article class="replay-local-summary"><strong>只改你确认的目标</strong><p>Bridge 将按原内容、时间范围和位置提示定位；遮罩外像素保持不变。口播替换只改匹配片段，不重做整条视频。</p></article>` : (replay.segments || []).map(item => `
    <article class="replay-segment" data-replay-segment="${esc(item.id)}">
      <strong>${Number(item.start_seconds).toFixed(2)}–${Number(item.end_seconds).toFixed(2)}s</strong>
      <div><label>画面要求<textarea data-replay-visual>${esc(item.prompt?.editable_visual_prompt || '')}</textarea></label>
      <label>说话人<input data-replay-speaker value="${esc(item.dialogue?.speaker || '')}" placeholder="可不填"></label>
      <label>新台词<textarea data-replay-dialogue placeholder="空白即静音">${esc(item.dialogue?.text || '')}</textarea></label>
       ${item.prompt?.final_prompt_preview ? `<details><summary>最终 H3 提示词（可直接修改）</summary><textarea data-replay-final-prompt maxlength="7000">${esc(item.prompt.final_prompt_preview)}</textarea></details>` : ''}</div>
    </article>`).join(''))
  renderLocalReplacementEditor()
  syncReplayActionState()
}

function syncReplayModeControls() {
  const localReplace = document.querySelector('input[name="replayMode"]:checked')?.value === 'local_replace'
  for (const id of ['replayPeople', 'replayScene', 'replayCamera']) $(id).disabled = localReplace
  if (localReplace) {
    $('replayPeople').value = 'preserve'
    $('replayScene').value = 'preserve'
    $('replayCamera').value = 'preserve'
  }
  $('replayStatus').textContent = localReplace
    ? '局部替换会固定保留原人物、场景和镜头运动'
    : (state.replay ? $('replayStatus').textContent : '尚未分析')
  renderLocalReplacementEditor()
  syncReplayActionState()
}

async function analyzeReplay() {
  const source = replaySourceAsset()
  if (!source) return toast('先上传或选择一条 2–60 秒源视频', 'error')
  if (!source.roleLocked) return toast('请先确认源视频用途', 'error')
  const mode = document.querySelector('input[name="replayMode"]:checked')?.value || 'full_structure'
  const request = await saveMaterialRequest({ submit: true })
  const result = await busy($('analyzeReplay'), () => api.materialCandidateGenerate({
    request_id: request.id,
    mode: 'replay',
    action: 'analyze',
    generation: {
      source_asset_id: source.asset.id,
      mode,
      request: $('requirement').value.trim(),
      replace_people: $('replayPeople').value === 'replace',
      remove_text: $('replayText').value === 'remove',
      product_mode: $('replayProduct').value,
      audio_mode: $('replayAudio').value,
      keep_scene: $('replayScene').value === 'preserve',
      keep_camera_motion: $('replayCamera').value === 'preserve',
    },
  }), '视频复刻 Agent 已完成完整源视频分析')
  state.replay = result.replay
  state.replayConfigured = false
  state.replayReplacementItems = Array.isArray(result.replay?.config?.replacement_items)
    ? result.replay.config.replacement_items
    : state.replayReplacementItems
  renderReplay()
}

function replayUpdatePayload() {
  return {
    replay_project_id: state.replay?.id,
    config: {
      replace_people: $('replayPeople').value === 'replace',
      remove_text: $('replayText').value === 'remove',
      product_mode: $('replayProduct').value,
      product_asset_ids: state.assets
        .filter(item => ['product_packshot', 'product_detail'].includes(item.role) && item.roleLocked)
        .map(item => item.asset.id)
        .slice(0, 2),
      audio_mode: $('replayAudio').value,
      keep_scene: $('replayScene').value === 'preserve',
      keep_camera_motion: $('replayCamera').value === 'preserve',
      replacement_items: readReplayReplacementItems(),
    },
    segments: $$('[data-replay-segment]').map(node => {
      const current = state.replay?.segments?.find(item => item.id === node.dataset.replaySegment)
      const promptInput = node.querySelector('[data-replay-final-prompt]')
      const priorPrompt = String(current?.prompt?.final_prompt_preview || '')
      const editedPrompt = String(promptInput?.value || '')
      return {
        segment_id: node.dataset.replaySegment,
        visual_prompt: node.querySelector('[data-replay-visual]').value,
        speaker: node.querySelector('[data-replay-speaker]').value,
        dialogue: node.querySelector('[data-replay-dialogue]').value,
        ...(promptInput && editedPrompt !== priorPrompt ? { final_prompt: editedPrompt } : {}),
      }
    }),
  }
}

async function previewReplay() {
  if (!state.replay) return analyzeReplay()
  const request = await saveMaterialRequest({ submit: true })
  const updated = await busy($('previewReplay'), () => api.materialCandidateGenerate({
    request_id: request.id,
    mode: 'replay',
    action: 'update',
    generation: replayUpdatePayload(),
  }))
  state.replay = updated.replay
  state.replayConfigured = true
  state.replayReplacementItems = Array.isArray(updated.replay?.config?.replacement_items)
    ? updated.replay.config.replacement_items
    : state.replayReplacementItems
  renderReplay()
  updateFormSummary()
  toast('执行预览已更新；确认无误后点击生成', 'success')
}

async function generateReplay() {
  if (!state.replay) return analyzeReplay()
  const request = await saveMaterialRequest({ submit: true })
  const updated = await busy($('generateReplay'), () => api.materialCandidateGenerate({
    request_id: request.id,
    mode: 'replay',
    action: 'update',
    generation: replayUpdatePayload(),
  }))
  state.replay = updated.replay
  state.replayConfigured = true
  renderReplay()
  const result = await busy($('generateReplay'), () => api.materialCandidateGenerate({
    request_id: request.id,
    mode: 'replay',
    action: 'submit',
    generation: { replay_project_id: state.replay.id },
  }))
  mergeJobs(result.jobs || [])
  renderJobs()
  setQueueOpen(true)
  state.replayConfigured = false
  const localReplace = state.replay.mode === 'local_replace'
  toast(localReplace ? '替换视频已进入生产队列' : `已提交 ${result.count || 0} 个完整覆盖片段`, 'success')
}

async function submitReplay() {
  return generateReplay()
}

function inferMode() {
  if (state.prepared?.inferred_mode) return {
    mode: state.prepared.inferred_mode,
    reason: state.prepared.ad_material_contract?.warnings?.[0] || '已按素材视觉取证与投流需求确定生产模式',
  }
  if ($('creativeOption').value === 'continuation') return { mode: 'continuation', reason: '源视频开启自动续写，将按片段依赖生成' }
  const roles = new Set(state.assets.map(item => item.role))
  if (Number($('duration')?.value || 5) === 30) {
    const hasSource = roles.has('continuity_anchor') || roles.has('motion_reference')
    return {
      mode: 'continuation',
      reason: hasSource ? '30 秒由两个 15 秒片段接力生成，后段继承前段人物、场景与对话' : '30 秒需要先添加 2–15 秒原视频作为第一段参考',
    }
  }
  const reference = ['motion_reference', 'audio_reference', 'visual_reference'].some(role => roles.has(role))
  if (reference) return { mode: 'reference_replay', reason: '包含视频、音频或多参考图片，使用参考复刻' }
  const dialogueTurns = $('script').value.split(/\r?\n/).filter(line => /^[^：:\n]{1,8}[：:]/.test(line.trim())).length
  const productOnlyImage = roles.has('product_packshot') && !roles.has('character_first_frame')
  if (productOnlyImage && (dialogueTurns >= 2 || $('containsPerson').checked)) {
    return { mode: 'text_to_video', reason: '先生成无商品人物底片，再由 Bridge 植入真实商品 PNG，避免 H3 重绘包装' }
  }
  if (roles.has('product_packshot') || roles.has('character_first_frame')) return { mode: 'image_to_video', reason: '所选图片作为正确首帧，使用图生视频' }
  return { mode: 'text_to_video', reason: '未添加素材，使用纯文本生成' }
}

function brief() {
  const audioEnabled = $('audioEnabled').checked
  return {
    product: $('product').value.trim(), platform: $('platform').value,
    // The script is an operator-owned performance contract. Keep it even when
    // the final delivery is intentionally silent; audio_enabled only controls
    // the governed voice/mux stage after the visual gate.
    script: $('script').value.trim(), request: $('requirement').value.trim(),
    audience: '成年人', adult_audience: true, ratio: $('ratio')?.value || '9:16',
    duration_seconds: Number($('duration').value || 5), audio_enabled: audioEnabled,
    contains_person: $('containsPerson').checked,
    strip_reference_text: $('stripReferenceText')?.checked !== false,
    reference_identity_policy: $('referenceIdentityPolicy')?.value || 'replace_actor',
    generation_options: { commercial_appearance: $('commercialAppearance').checked },
  }
}

function isDirectPromptMode() { return $('creativeOption').value === 'direct_prompt' }

function hasProductionInput() {
  return Boolean(
    (state.productionMode === 'replay' && state.replay)
    ||
    String($('requirement').value || '').trim()
    || String($('script').value || '').trim()
    || state.assets.length
    || (isDirectPromptMode() && String($('directH3Prompt').value || '').trim())
  )
}

function requestedSubmitPlanningMode() {
  return $('themeEnabled').checked ? 'ai_optimize' : 'operator_brief'
}

function syncDirectPromptUi() {
  if (state.productionMode) {
    $('directPromptPanel').classList.add('hidden')
    $('themeEnabled').disabled = true
    $('submitButton').textContent = state.submissionLocked
      ? '已加入生产'
      : state.submitting
        ? (state.submitPhase || '正在加入生产…')
        : state.productionMode === 'replay'
          ? (state.replayConfigured ? '确认并提交复刻' : '预览复刻提示词')
          : state.productionMode === 'strategy'
            ? strategySubmitButtonLabel()
            : '直接生成'
    $('prepareButton').textContent = state.strategy ? '重新生成三个方向' : '生成三个方向'
    return
  }
  const direct = isDirectPromptMode()
  $('directPromptPanel').classList.toggle('hidden', !direct)
  $('prepareButton').textContent = direct ? '校验 H3 提示词' : '可选：AI 帮我优化'
  $('submitButton').textContent = state.submissionLocked
    ? '已加入生产'
    : state.submitting
      ? (state.submitPhase || '正在加入生产…')
      : (!direct && state.prepared?.planning_mode === 'ai_planned'
          ? '使用 AI 优化结果生成'
          : '直接生成')
  $('themeEnabled').disabled = direct
  if (direct && $('themeEnabled').checked) {
    $('themeEnabled').checked = false
    $('themeSettings').classList.add('hidden')
  }
  if (direct) {
    $('intentLabel').textContent = '操作员直接控制画面'
    $('routingReason').textContent = '不调用 DeepSeek；只校验 H3 参数、素材角色和白名单工作流。文案与生成要求继续生效。'
  } else if (!state.prepared) {
    $('intentLabel').textContent = '用户提示词直接生产'
    $('routingReason').textContent = 'AI 优化可选；系统仅做素材校验、H3 确定性编译和节点路由，不改写你的原始要求。'
  }
}

function totalQuantity() {
  if (!$('themeEnabled').checked) return Math.min(600, Math.max(1, Number($('quantity').value || 1)))
  return Math.min(600, Math.max(3, Number($('directionCount').value || 6)) * Math.max(1, Number($('variantsPerDirection').value || 1)))
}

function updateFormSummary() {
  syncDirectPromptUi()
  const inference = inferMode()
  const productOverlay = state.prepared?.prepared_plan?.product_overlay || {}
  const overlayEnabled = productOverlay.enabled === true
  const dynamicProductPlate = overlayEnabled
    && productOverlay.anchor === 'center'
    && productOverlay.motion_profile === 'static_verified_product_dynamic_background'
  const modeLabel = dynamicProductPlate
    ? '动态商品底片 + 真实商品植入'
    : (overlayEnabled ? '人物底片 + 真实商品植入' : modeNames[inference.mode])
  const preset = selectedPreset()
  const oriented = orientedPresetParams(preset.params)
  const quantity = totalQuantity()
  $('inferredMode').textContent = modeLabel
  $('modeReason').textContent = inference.reason
  $('asideProduct').textContent = $('product').value.trim() || '未填写产品'
  $('asideMode').textContent = modeLabel
  $('asidePreset').textContent = `${oriented.width} × ${oriented.height} · ${$('duration').value} 秒`
  $('asideQuantity').textContent = `${quantity} 条`
  $('submitSummary').textContent = `${quantity} 条 · ${preset.label || '极速试片'} · ${{ immediate: '立即生成', night: '今晚空闲时', custom: '自定义时间' }[selectedSchedule()]}`
  const online = state.snapshot.nodes?.filter(node => node.online) || []
  const eta = online.map(node => Number(node.rolling_eta_seconds || 0)).filter(Boolean)
  $('submitEta').textContent = eta.length
    ? `排队前参考：最快节点近期混合任务中位耗时约 ${seconds(Math.min(...eta) * Math.ceil(quantity / online.length))}；派单后显示本任务同规格 ETA`
    : '等待节点实测 ETA；派单后显示本任务同规格 ETA'
  const hasVisualPrompt = state.productionMode === 'replay' || Boolean(String($('requirement').value || '').trim())
  const unconfirmedAssetCount = state.assets.filter(item => !item.roleLocked).length
  const blockingReason = state.submissionLocked
    ? '本次需求已加入生产，避免重复提交'
    : state.submitting
      ? (state.submitPhase || '正在加入生产…')
      : !hasProductionInput() || !hasVisualPrompt
        ? '填写画面提示词后可直接生成'
        : unconfirmedAssetCount
          ? `还差一步：确认 ${unconfirmedAssetCount} 个素材用途；确认后即可生成`
          : state.assets.length
            ? `已确认 ${state.assets.length} 个素材，都会参与本次生成`
            : '当前为文生视频，可直接生成'
  $('submitBlockReason').textContent = blockingReason
  $('submitBlockReason').classList.toggle('ready', !state.submissionLocked && !state.submitting && hasVisualPrompt && !unconfirmedAssetCount)
  $('submitButton').title = blockingReason
  // Unconfirmed material is an actionable validation state. Keep the CTA
  // clickable so it can lead the operator to the exact card that needs work.
  $('submitButton').disabled = state.submissionLocked
    || state.submitting
    || !hasProductionInput()
    || !hasVisualPrompt
  renderScriptTimingHint()
  renderExactPromptPreview()
  saveDraft()
}

function localFileFingerprint(file = {}) {
  const size = Number(file.size || 0)
  if (!file.name || !Number.isFinite(size) || size <= 0) return ''
  return `file:${file.name}:${size}:${Number(file.lastModified || 0)}`
}

function selectedAssetFingerprints(item) {
  const asset = item?.asset || {}
  return [
    asset.sha256 ? `sha256:${asset.sha256}` : '',
    asset.id ? `asset:${asset.id}` : '',
    item?.localFingerprint || localFileFingerprint(item?.file),
  ].filter(Boolean)
}

function findSelectedAssetIndex(candidate) {
  const fingerprints = new Set(selectedAssetFingerprints(candidate))
  if (!fingerprints.size) return -1
  return state.assets.findIndex(item => selectedAssetFingerprints(item).some(value => fingerprints.has(value)))
}

function dedupeSelectedAssets(items) {
  const seen = new Set()
  return (items || []).filter(item => {
    const fingerprints = selectedAssetFingerprints(item)
    if (fingerprints.some(value => seen.has(value))) return false
    fingerprints.forEach(value => seen.add(value))
    return true
  })
}

function assetMentionContext() {
  const input = $('requirement')
  const caret = Number.isInteger(input.selectionStart) ? input.selectionStart : input.value.length
  const before = input.value.slice(0, caret)
  const match = before.match(/(^|[\s，。；、：:])@([\p{L}\p{N}_-]{0,48})$/u)
  if (!match) return null
  return {
    start: caret - match[2].length - 1,
    end: caret,
    query: match[2].toLocaleLowerCase('zh-CN'),
  }
}

function hideAssetMentionMenu() {
  $('assetMentionMenu').classList.add('hidden')
  $('assetMentionMenu').innerHTML = ''
}

async function ensureAssetLibrary() {
  if (state.assetLibraryLoaded) return state.assetLibrary
  if (!state.assetLibraryPromise) {
    state.assetLibraryPromise = api.libraryList()
      .then(response => {
        state.assetLibrary = response.items || []
        state.assetLibraryLoaded = true
        return state.assetLibrary
      })
      .finally(() => { state.assetLibraryPromise = null })
  }
  return state.assetLibraryPromise
}

function assetMentionCandidates(query = '') {
  const selectedIds = new Set(state.assets.map(item => item.asset?.id).filter(Boolean))
  const selected = state.assets.map((item, index) => ({
    kind: 'selected', index, token: assetMentionToken(item, index), name: assetDisplayName(item),
    mime: item.asset?.mime_type || item.file?.type || '', source: item.asset || {},
  }))
  const library = state.assetLibrary
    .filter(item => item.source?.id && !selectedIds.has(item.source.id))
    .map((item, index) => ({
      kind: 'library', libraryId: item.id,
      token: assetMentionToken({ file: { name: item.name || item.source.file_name } }, index),
      name: item.name || item.source.file_name || '素材', mime: item.source.mime_type || '', source: item.source,
    }))
  const normalized = query.toLocaleLowerCase('zh-CN')
  return [...selected, ...library]
    .filter(item => !normalized || `${item.name} ${item.token}`.toLocaleLowerCase('zh-CN').includes(normalized))
    .slice(0, 12)
}

function renderAssetMentionMenu(context) {
  const current = assetMentionContext()
  if (!current || current.start !== context.start) return hideAssetMentionMenu()
  const candidates = assetMentionCandidates(current.query)
  const menu = $('assetMentionMenu')
  menu.innerHTML = candidates.length ? candidates.map((item, index) => {
    const preview = item.mime.startsWith('image/') && item.source.download_url
      ? `<img src="${esc(item.source.download_url)}" alt="">`
      : `<span class="asset-thumb">${item.mime.startsWith('video/') ? '▶' : item.mime.startsWith('audio/') ? '♫' : '图'}</span>`
    const locator = item.kind === 'selected' ? `data-mention-selected="${item.index}"` : `data-mention-library="${esc(item.libraryId)}"`
    return `<button type="button" class="asset-mention-option" role="option" ${locator} data-mention-token="${esc(item.token)}">${preview}<span><strong>${esc(item.name)}</strong><small>${item.kind === 'selected' ? '本次已选素材' : '部门素材库'}</small></span><code>${esc(item.token)}</code></button>`
  }).join('') : '<div class="asset-mention-empty">没有匹配素材；可先上传或保存到部门素材库。</div>'
  menu.classList.remove('hidden')
  $$('[data-mention-token]').forEach(button => button.onclick = () => selectAssetMention(button, current).catch(error => toast(errorText(error), 'error')))
}

async function showAssetMentionMenu() {
  const context = assetMentionContext()
  if (!context) return hideAssetMentionMenu()
  const menu = $('assetMentionMenu')
  menu.innerHTML = '<div class="asset-mention-empty">正在读取本次素材和部门素材库…</div>'
  menu.classList.remove('hidden')
  try {
    await ensureAssetLibrary()
    renderAssetMentionMenu(context)
  } catch (error) {
    state.assetLibraryLoaded = false
    const selected = state.assets.length
    if (selected) renderAssetMentionMenu(context)
    else menu.innerHTML = `<div class="asset-mention-empty">素材库读取失败，请稍后重试。${esc(errorText(error))}</div>`
  }
}

function replaceMentionContext(context, token) {
  const input = $('requirement')
  input.setRangeText(`${token} `, context.start, context.end, 'end')
  input.focus()
  hideAssetMentionMenu()
  invalidatePlan()
  updateFormSummary()
}

async function selectAssetMention(button, context) {
  let token = button.dataset.mentionToken
  if (button.dataset.mentionLibrary) {
    const added = await useLibraryAsset(button.dataset.mentionLibrary, { closeDialog: false, silent: true })
    if (added) token = assetMentionToken(added, state.assets.indexOf(added))
  }
  replaceMentionContext(context, token)
}

function appendAssetMention(index) {
  const input = $('requirement')
  const token = assetMentionToken(state.assets[index], index)
  const caret = Number.isInteger(input.selectionStart) ? input.selectionStart : input.value.length
  const prefix = caret > 0 && !/[\s，。；、：:]$/.test(input.value.slice(0, caret)) ? ' ' : ''
  input.setRangeText(`${prefix}${token} `, caret, caret, 'end')
  input.focus()
  invalidatePlan()
  updateFormSummary()
}

function renderAssets({ focusIndex = -1 } = {}) {
  state.assets = dedupeSelectedAssets(state.assets)
  const pending = state.assets.filter(item => !item.roleLocked).length
  $('assetCountBadge').textContent = `${state.assets.length} 个素材`
  $('assetCountBadge').classList.toggle('has-assets', Boolean(state.assets.length))
  $('assetSelectionSummary').className = `asset-selection-summary ${pending ? 'needs-action' : state.assets.length ? 'ready' : ''}`
  $('assetSelectionSummary').innerHTML = !state.assets.length
    ? '<strong>尚未添加素材</strong><span>可直接文生视频</span>'
    : pending
      ? `<strong>已上传 ${state.assets.length} 个素材</strong><span>请确认 ${pending} 个用途，确认后即参与生成</span>`
      : `<strong>${state.assets.length} 个素材已参与生成</strong><span>“插入 @素材名”仅用于在画面描述中点名</span>`
  if (!state.assets.length) {
    $('selectedAssets').innerHTML = '<p class="empty-copy">不添加素材也可以直接文生视频。</p>'
    updateFormSummary(); return
  }
  $('selectedAssets').innerHTML = state.assets.map((item, index) => {
    const mime = item.asset.mime_type || item.file.type
    const url = assetUrl(item.asset)
    const preview = mime.startsWith('image/')
      ? `<button type="button" class="asset-preview-trigger" data-preview-asset="${index}" aria-label="预览 ${esc(item.file.name)}"><img class="asset-thumb" src="${esc(url)}" alt=""></button>`
      : `<button type="button" class="asset-preview-trigger" data-preview-asset="${index}" aria-label="预览 ${esc(item.file.name)}"><span class="asset-thumb">${mime.startsWith('video/') ? '▶' : '♫'}</span></button>`
    const options = Object.entries(roleNames).map(([value, label]) => `<option value="${value}" ${item.role === value ? 'selected' : ''}>${label}</option>`).join('')
    const validation = item.validationError
      ? `<small class="asset-validation" role="alert">${esc(item.validationError)}</small>`
      : ''
    const participation = item.roleLocked
      ? `<small class="asset-participation confirmed">已参与生成 · ${esc(exactSourceBindings()[index]?.token || '')}</small>`
      : '<small class="asset-participation">待确认用途，尚未参与生成</small>'
    return `<div class="asset-row ${item.validationError ? 'invalid' : ''} ${item.roleLocked ? '' : 'needs-action'}" data-selected-asset="${index}">${preview}<div class="asset-name"><strong>${esc(item.file.name)}</strong><small>${esc(mime)}</small>${participation}${validation}</div><select data-asset-role="${index}" aria-label="素材角色">${options}</select><button type="button" class="button secondary asset-confirm ${item.roleLocked ? 'confirmed' : ''}" data-confirm-asset="${index}">${item.roleLocked ? '已确认并参与' : '确认并参与'}</button><button type="button" class="button secondary asset-save" data-save-asset="${index}" ${item.libraryAssetId ? 'disabled' : ''}>${item.libraryAssetId ? '已保存' : '保存素材'}</button><button type="button" class="button secondary asset-mention" data-append-mention="${index}">插入 @素材名</button><button type="button" class="remove-asset" data-remove-asset="${index}" aria-label="移除">×</button></div>`
  }).join('')
  $$('[data-asset-role]').forEach(select => select.onchange = () => {
    state.assets[Number(select.dataset.assetRole)].role = select.value
    state.assets[Number(select.dataset.assetRole)].roleLocked = true
    state.assets[Number(select.dataset.assetRole)].validationError = ''
    invalidatePlan()
    renderAssets({ focusIndex: Number(select.dataset.assetRole) })
    updateFormSummary()
  })
  $$('[data-remove-asset]').forEach(button => button.onclick = () => {
    const [removed] = state.assets.splice(Number(button.dataset.removeAsset), 1)
    if (removed?.libraryAssetId) state.autoAddedRecommendationIds.delete(removed.libraryAssetId)
    invalidatePlan(); renderAssets(); renderAssetRecommendation(); updateFormSummary()
  })
  $$('[data-confirm-asset]').forEach(button => button.onclick = () => {
    const item = state.assets[Number(button.dataset.confirmAsset)]
    item.roleLocked = true
    item.validationError = ''
    renderAssets()
    renderExactPromptPreview()
  })
  $$('[data-save-asset]').forEach(button => button.onclick = () => saveAssetToLibrary(Number(button.dataset.saveAsset), button).catch(() => {}))
  $$('[data-append-mention]').forEach(button => button.onclick = () => appendAssetMention(Number(button.dataset.appendMention)))
  $$('[data-preview-asset]').forEach(button => button.onclick = () => openAssetPreview(Number(button.dataset.previewAsset)))
  updateFormSummary()
  if (focusIndex >= 0) {
    const row = document.querySelector(`[data-selected-asset="${focusIndex}"]`)
    requestAnimationFrame(() => row?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
  }
}

function focusUnconfirmedAsset() {
  const index = state.assets.findIndex(item => !item.roleLocked)
  if (index < 0) return
  const row = document.querySelector(`[data-selected-asset="${index}"]`)
  row?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  window.setTimeout(() => row?.querySelector('[data-confirm-asset]')?.focus(), 260)
}

function revealExistingAsset(index, fileName) {
  renderAssets()
  const row = document.querySelector(`[data-selected-asset="${index}"]`)
  if (row) {
    row.classList.add('duplicate-highlight')
    row.scrollIntoView({ behavior: 'smooth', block: 'center' })
    window.setTimeout(() => row.classList.remove('duplicate-highlight'), 1800)
  }
  $('assetUploadStatus').textContent = `${fileName} 已在下方素材列表中，本次未重复添加`
}

function openAssetPreview(index) {
  const item = state.assets[index]
  if (!item) return
  const mime = item.asset?.mime_type || item.file?.type || ''
  const url = assetUrl(item.asset)
  if (!url) return toast('该素材暂时没有可用预览地址', 'error')
  $('assetPreviewTitle').textContent = item.file?.name || '素材预览'
  $('assetPreviewBody').innerHTML = mime.startsWith('video/')
    ? `<video src="${esc(url)}" controls playsinline preload="metadata"></video>`
    : mime.startsWith('audio/')
      ? `<audio src="${esc(url)}" controls preload="metadata"></audio>`
      : `<img src="${esc(url)}" alt="${esc(item.file?.name || '素材预览')}">`
  const dialog = $('assetPreviewDialog')
  dialog.onclose = () => { $('assetPreviewBody').innerHTML = '' }
  dialog.showModal()
}

async function uploadAssets(files) {
  if (!files.length || $('assetDrop').classList.contains('uploading')) return
  $('assetDrop').classList.add('uploading')
  $('assetDrop').setAttribute('aria-busy', 'true')
  $('assetUpload').disabled = true
  const uploadStatus = $('assetUploadStatus')
  const defaultStatus = '普通生成最多 9 图 / 3 视频 / 3 音频；复刻源视频支持 2–60 秒。'
  try {
    for (const [fileIndex, file] of files.entries()) {
      const localFingerprint = localFileFingerprint(file)
      const existingLocalIndex = findSelectedAssetIndex({ file, localFingerprint })
      if (existingLocalIndex >= 0) {
        revealExistingAsset(existingLocalIndex, file.name)
        continue
      }
      try {
        uploadStatus.textContent = `正在上传 ${file.name} · ${fileIndex + 1}/${files.length} · 0%`
        const response = await upload(
          file,
          { source: 'material_workbench_v2150', rights_status: 'department_owned_material_policy' },
          progress => {
            const percent = Math.max(0, Math.min(99, Number(progress?.percent || 0)))
            uploadStatus.textContent = `正在上传 ${file.name} · ${fileIndex + 1}/${files.length} · ${percent}%`
            if (percent >= 99) uploadStatus.textContent = `${file.name} 已上传，正在校验时长、画面和音频…`
          },
        )
        const asset = response.asset || response
        const duplicateIndex = findSelectedAssetIndex({ file, asset, localFingerprint })
        if (duplicateIndex >= 0) {
          state.assets[duplicateIndex].localFingerprint ||= localFingerprint
          revealExistingAsset(duplicateIndex, file.name)
          continue
        }
        state.assets.push({ file, asset, localFingerprint, role: defaultRole(file), roleLocked: false })
        renderAssets({ focusIndex: state.assets.length - 1 })
        uploadStatus.textContent = `${file.name} 已完成上传与校验`
      } catch (error) {
        uploadStatus.textContent = `${file.name} 上传失败，可保留当前表单后重试`
        toast(`${file.name}：${errorText(error)}`, 'error')
      }
    }
  } finally {
    $('assetUpload').value = ''
    $('assetUpload').disabled = false
    $('assetDrop').classList.remove('uploading')
    $('assetDrop').setAttribute('aria-busy', 'false')
    window.setTimeout(() => { uploadStatus.textContent = defaultStatus }, 1800)
  }
  applySafeDefaults(); invalidatePlan(); renderAssets()
}

async function saveAssetToLibrary(index, button) {
  const item = state.assets[index]
  if (!item?.asset?.id || item.libraryAssetId) return
  const response = await busy(button, () => api.libraryUpsert({
    source_asset_id: item.asset.id,
    name: item.file.name,
    tags: ['投流素材', roleNames[item.role]],
    rights: { ownership: 'department_owned', policy: 'department_owned_material_policy' },
    metadata: { source: 'material_workbench_v2150', default_role: item.role, business_role: item.role, synthetic_test: /synthetic|合成/i.test(item.file.name) },
  }), '已保存到部门素材库')
  item.libraryAssetId = response.asset?.id
  renderAssets()
}

function renderAssetLibrary() {
  const keyword = $('assetLibrarySearch').value.trim().toLowerCase()
  const rows = state.assetLibrary.filter(item => !keyword || `${item.name} ${(item.tags || []).join(' ')}`.toLowerCase().includes(keyword))
  $('assetLibraryNotice').textContent = `部门素材 ${state.assetLibrary.length} 条 · 当前显示 ${rows.length} 条`
  $('assetLibraryResults').innerHTML = rows.length ? rows.map(item => {
    const source = item.source || {}
    const mime = source.mime_type || `${item.media_type || ''}/unknown`
    const preview = mime.startsWith('image/') && source.download_url ? `<img class="asset-thumb" src="${esc(source.download_url)}" alt="">` : `<span class="asset-thumb">${mime.startsWith('video/') ? '▶' : mime.startsWith('audio/') ? '♫' : '图'}</span>`
    const alreadyUsed = state.assets.some(asset => asset.asset.id === source.id)
    return `<article class="cloud-card">${preview}<strong>${esc(item.name)}</strong><span>${esc((item.tags || []).join(' · ') || mime)}</span><button type="button" class="button secondary full" data-use-library="${esc(item.id)}" ${!source.id || alreadyUsed ? 'disabled' : ''}>${alreadyUsed ? '已添加' : '使用素材'}</button></article>`
  }).join('') : '<p class="empty-copy">素材库暂无匹配内容；可先上传素材并点击“保存素材”。</p>'
  $$('[data-use-library]').forEach(button => button.onclick = () => useLibraryAsset(button.dataset.useLibrary).catch(error => toast(errorText(error), 'error')))
}

async function useLibraryAsset(libraryId, { closeDialog = true, silent = false } = {}) {
  const item = state.assetLibrary.find(row => row.id === libraryId)
  const source = item?.source
  const existing = state.assets.find(asset => asset.asset.id === source?.id)
  if (!source?.id || existing) return existing || null
  const file = { name: item.name || source.file_name, type: source.mime_type || '' }
  const savedRole = item.metadata?.default_role
  const hints = `${item.name || ''} ${(item.tags || []).join(' ')}`
  const peopleHints = /人物|双人|情侣|场景|约会|医生|口播/.test(hints)
  const productHints = /商品透明图|产品透明图|商品首帧|商品包装|真实商品|packshot|packaging|包装|单片|散片|侧面|正面/.test(hints)
  const correctedLegacyRole = savedRole === 'product_packshot' && peopleHints
    ? 'character_first_frame'
    : savedRole === 'character_first_frame' && productHints && !peopleHints
      ? 'product_packshot'
      : savedRole
  const role = roleNames[correctedLegacyRole] ? correctedLegacyRole : defaultRole(file)
  if (savedRole && correctedLegacyRole !== savedRole) {
    const repaired = await api.libraryUpsert({
      id: item.id,
      source_asset_id: source.id,
      name: item.name,
      tags: item.tags || [],
      rights: item.rights || {},
      scan: item.scan || {},
      metadata: {
        ...(item.metadata || {}),
        default_role: correctedLegacyRole,
        business_role: correctedLegacyRole,
        role_repaired_from: savedRole,
        role_repaired_by: 'material_workbench_v2150',
      },
    })
    item.metadata = repaired.asset?.metadata || item.metadata
  }
  const selected = { file, asset: source, role, roleLocked: Boolean(correctedLegacyRole), libraryAssetId: item.id, libraryRecord: item }
  state.assets.push(selected)
  if (closeDialog && $('assetLibraryDialog').open) $('assetLibraryDialog').close()
  applySafeDefaults(); invalidatePlan(); renderAssets()
  if (!silent) toast('已从部门素材库添加', 'success')
  return selected
}

function selectedRecommendation(item) {
  return state.assets.find(asset => asset.libraryAssetId === item.id || asset.asset?.id === item.source_asset_id)
}

function useRecommendedAsset(item) {
  if (!item?.source?.id) return null
  const existing = selectedRecommendation(item)
  if (existing) return existing
  const file = { name: item.name || item.source.file_name, type: item.source.mime_type || 'image/png' }
  const selected = {
    file,
    asset: item.source,
    role: item.role || 'product_packshot',
    roleLocked: true,
    libraryAssetId: item.id,
    libraryRecord: item,
    recommendedBy: 'material-product-reference-v1',
  }
  state.assets.push(selected)
  applySafeDefaults(); invalidatePlan(); renderAssets(); renderAssetRecommendation(); updateFormSummary()
  return selected
}

function renderAssetRecommendation() {
  const panel = $('assetRecommendation')
  const result = state.assetRecommendation
  const items = result?.items || []
  panel.classList.toggle('hidden', !items.length)
  if (!items.length) return
  const summary = result.conflict
    ? `检测到多个产品：${(result.product_matches || []).join('、')}，请手动选择，不会自动绑定`
    : result.auto_selected_asset_id
      ? `已按“${(result.product_matches || []).join('、')} · ${result.view_intent || '正面优先'}”自动选中最高置信素材`
      : '匹配置信度不足，仅提供候选，不会擅自参与生成'
  $('assetRecommendationSummary').textContent = summary
  $('assetRecommendationItems').innerHTML = items.map(item => {
    const selected = Boolean(selectedRecommendation(item))
    const locked = state.lockedRecommendedAssetIds.has(item.id)
    const preview = item.thumbnail?.download_url || item.source?.download_url || ''
    return `<article class="asset-recommendation-card ${selected ? 'selected' : ''}" data-recommended-asset="${esc(item.id)}">
      ${preview ? `<img src="${esc(preview)}" alt="${esc(item.name)}">` : '<span class="asset-thumb">图</span>'}
      <div><strong>${esc(item.name)}</strong><small>${esc(`${item.product_key || '待确认'} · ${item.view_type || '未分类'} · 置信度 ${Math.round(Number(item.confidence || 0) * 100)}%`)}</small><small>${esc(item.reason || '等待用户确认')}</small>
      <div class="asset-recommendation-actions"><button type="button" class="button text" data-adopt-recommendation="${esc(item.id)}">${selected ? '已参与生成' : '使用这张'}</button><button type="button" class="button text" data-lock-recommendation="${esc(item.id)}">${locked ? '已锁定' : '锁定推荐'}</button>${selected ? `<button type="button" class="button text" data-remove-recommendation="${esc(item.id)}">移除</button>` : ''}</div></div>
    </article>`
  }).join('')
  $$('[data-adopt-recommendation]').forEach(button => button.onclick = () => {
    const item = items.find(row => row.id === button.dataset.adoptRecommendation)
    useRecommendedAsset(item)
  })
  $$('[data-lock-recommendation]').forEach(button => button.onclick = () => {
    const id = button.dataset.lockRecommendation
    state.lockedRecommendedAssetIds.has(id) ? state.lockedRecommendedAssetIds.delete(id) : state.lockedRecommendedAssetIds.add(id)
    renderAssetRecommendation(); scheduleAssetRecommendation(true)
  })
  $$('[data-remove-recommendation]').forEach(button => button.onclick = () => {
    const id = button.dataset.removeRecommendation
    const item = items.find(row => row.id === id)
    const index = state.assets.findIndex(asset => asset.libraryAssetId === id || asset.asset?.id === item?.source_asset_id)
    if (index >= 0) state.assets.splice(index, 1)
    state.autoAddedRecommendationIds.delete(id)
    invalidatePlan(); renderAssets(); renderAssetRecommendation(); updateFormSummary()
  })
}

async function loadAssetRecommendation() {
  const requestId = ++state.assetRecommendationRequest
  const result = await api.materialAssetRecommend({
    visual_prompt: $('requirement').value.trim(),
    script: $('script').value.trim(),
    business: businessInfoPayload(),
    locked_asset_ids: [...state.lockedRecommendedAssetIds],
  })
  if (requestId !== state.assetRecommendationRequest) return
  state.assetRecommendation = result
  const automatic = (result.items || []).find(item => item.id === result.auto_selected_asset_id)
  if (automatic && !state.autoAddedRecommendationIds.has(automatic.id)) {
    useRecommendedAsset(automatic)
    state.autoAddedRecommendationIds.add(automatic.id)
    toast(`已自动引用 ${automatic.name}；可替换、移除或锁定`, 'success')
  }
  renderAssetRecommendation()
}

function scheduleAssetRecommendation(immediate = false) {
  window.clearTimeout(state.assetRecommendationTimer)
  const hasRetrievalInput = Boolean($('product').value.trim() || $('requirement').value.trim() || $('script').value.trim())
  if (!hasRetrievalInput) {
    state.assetRecommendation = null
    renderAssetRecommendation()
    return
  }
  state.assetRecommendationTimer = window.setTimeout(() => {
    loadAssetRecommendation().catch(error => {
      if (!isCapabilityQuotaError(error)) console.warn('asset recommendation failed', error)
    })
  }, immediate ? 0 : 350)
}

async function openAssetLibrary() {
  if (!$('assetLibraryDialog').open) $('assetLibraryDialog').showModal()
  $('assetLibraryResults').innerHTML = '<p class="empty-copy">正在读取部门素材…</p>'
  state.assetLibraryLoaded = false
  await ensureAssetLibrary()
  renderAssetLibrary()
}

function renderPresets(registry = state.snapshot.output_presets) {
  const items = registry?.items?.length ? registry.items : [{ id: 'quick_preview', label: '极速试片', description: '已验证的稳定默认档', params: { width: 480, height: 864 }, enabled: true }]
  if (!items.some(item => item.id === state.selectedPreset && item.enabled)) state.selectedPreset = registry?.recommended_id || 'quick_preview'
  $('presetCards').innerHTML = items.map(item => {
    const benchmarkPending = item.benchmark_required && item.gate?.benchmark_passed === false
    const reason = benchmarkPending ? `${item.description} ${item.gate?.reason || ''}`.trim() : item.description
    const oriented = orientedPresetParams(item.params)
    return `<button type="button" class="preset-card ${state.selectedPreset === item.id ? 'selected' : ''} ${benchmarkPending ? 'benchmark-pending' : ''} ${item.enabled ? '' : 'locked'}" data-preset="${item.id}" ${item.enabled ? '' : 'disabled'} title="${esc(reason)}"><strong>${esc(item.label)}</strong><span>${oriented.width} × ${oriented.height}</span><small>${esc(reason)}</small>${item.id === (registry?.recommended_id || 'quick_preview') ? '<em class="recommended">推荐</em>' : ''}</button>`
  }).join('')
  $$('[data-preset]').forEach(button => button.onclick = () => { state.selectedPreset = button.dataset.preset; renderPresets(); updateFormSummary() })
}

function renderNodes() {
  const nodes = state.snapshot.nodes || []
  const online = nodes.filter(node => node.online)
  const offline = nodes.filter(node => !node.online)
  $('onlineNodeCount').textContent = `${online.length} 在线`
  const onlineCards = online.map(node => {
    const gpu = node.gpu?.map(item => item.name || item.model).filter(Boolean).join(' / ') || node.media?.gpu_name || 'GPU 节点'
    return `<div class="node-card"><div><strong>${esc(node.name)}</strong><span>在线</span></div><small>${esc(gpu)} · 近期混合任务中位耗时 ${seconds(node.rolling_eta_seconds)} · 队列 ${node.media?.queue_depth ?? '—'}</small></div>`
  }).join('')
  const offlineCards = offline.map(node => {
    const gpu = node.gpu?.map(item => item.name || item.model).filter(Boolean).join(' / ') || node.media?.gpu_name || 'GPU 节点'
    const reason = node.offline_reason === 'heartbeat_stale'
      ? `心跳已中断 ${elapsed(node.heartbeat_age_seconds)}`
      : (node.offline_reason === 'heartbeat_missing' ? '尚未收到 Bridge 心跳' : '节点已停用')
    return `<div class="node-card offline"><div><strong>${esc(node.name)}</strong><span>离线</span></div><small>${esc(gpu)} · ${esc(reason)} · 不参与派单</small></div>`
  }).join('')
  $('nodeStatusList').innerHTML = `${onlineCards}${offlineCards}` || '<p class="empty-copy">当前没有已登记媒体节点；任务可提交，节点恢复后自动派单。</p>'
}

function renderSnapshot(snapshot) {
  state.snapshot = { ...state.snapshot, ...snapshot }
  if (snapshot.cursor) state.eventCursor = String(snapshot.cursor)
  if (Array.isArray(snapshot.jobs)) {
    applySnapshotJobs(snapshot.jobs)
  }
  const latestStrategy = snapshot.strategies?.[0]
  if (latestStrategy && (!state.strategy || state.strategy.id === latestStrategy.id)) {
    state.strategy = latestStrategy
    if (state.productionMode === 'strategy') renderStrategy()
  }
  const latestReplay = snapshot.replays?.[0]
  if (latestReplay && (!state.replay || state.replay.id === latestReplay.id)) {
    state.replay = latestReplay
    if (state.productionMode === 'replay') renderReplay()
  }
  const pendingRequests = Number(snapshot.request_counts?.pending || 0)
  const awaitingSelection = Number(snapshot.candidate_counts?.editorial?.pending || 0)
  const selected = Number(snapshot.north_star?.selected_candidates || 0)
  state.jobActiveTotal = Number(snapshot.job_counts?.active ?? state.jobActiveTotal ?? 0)
  $('metricPendingRequests').textContent = String(pendingRequests)
  $('metricProducing').textContent = String(state.jobActiveTotal)
  $('metricAwaitingSelection').textContent = String(awaitingSelection)
  $('metricWeeklySelected').textContent = String(selected)
  $('reviewCount').textContent = awaitingSelection
  $('wallPendingCount').textContent = awaitingSelection
  const preference = snapshot.review_preference || {}
  if (preference.density && ['comfortable', 'compact'].includes(preference.density)) state.wallDensity = preference.density
  if (preference.sort && [...$('reviewSort').options].some(option => option.value === preference.sort)) $('reviewSort').value = preference.sort
  const reviewFilters = preference.filters || {}
  if (reviewFilters.status) state.reviewView = reviewFilters.status
  if (reviewFilters.generation_method && [...$('reviewMethod').options].some(option => option.value === reviewFilters.generation_method)) $('reviewMethod').value = reviewFilters.generation_method
  if (reviewFilters.source && [...$('reviewSource').options].some(option => option.value === reviewFilters.source)) $('reviewSource').value = reviewFilters.source
  if (reviewFilters.product) $('reviewProduct').value = reviewFilters.product
  if (reviewFilters.batch_id) $('reviewBatch').value = reviewFilters.batch_id
  if (reviewFilters.assignee_id) $('reviewAssignee').value = reviewFilters.assignee_id
  $('reviewStatus').value = ['pending', 'selected', 'held', 'rejected'].includes(state.reviewView) ? state.reviewView : 'all'
  $$('[data-review-view]').forEach(button => button.classList.toggle('active', button.dataset.reviewView === state.reviewView))
  $$('[data-wall-density]').forEach(button => button.classList.toggle('active', button.dataset.wallDensity === state.wallDensity))
  renderPresets(snapshot.output_presets); renderNodes(); updateFormSummary()
}

function invalidatePlan() {
  state.prepared = null; state.submitKey = ''; state.submissionLocked = false
  state.shotEditsDirty = false
  state.firstFrameAttempt = null
  state.firstFrameAttemptNo = 1
  $('planResult').classList.add('hidden')
  $('prepareError').classList.add('hidden'); $('prepareError').textContent = ''
  $('submitReceipt').classList.add('hidden'); $('submitReceipt').textContent = ''
  updateFormSummary()
}

function renderFirstFrameRecommendation(result = state.prepared) {
  const anchor = result?.ad_material_contract?.first_frame_anchor || {}
  const alreadyAnchored = state.assets.some(item => item.role === 'character_first_frame')
  const visible = anchor.recommended === true && !alreadyAnchored && result?.planning_mode !== 'direct_h3_prompt'
  $('firstFrameRecommendation').classList.toggle('hidden', !visible)
  if (!visible) return
  $('firstFrameReason').textContent = anchor.reason || '先生成并检查人物首帧，再用 H3 图生视频锁定人物与中景构图。'
  const attempt = state.firstFrameAttempt
  const preview = $('firstFramePreview')
  preview.classList.toggle('hidden', !attempt?.asset)
  if (attempt?.asset) {
    const issues = attempt.quality_gate?.issues || []
    preview.innerHTML = `<img src="${esc(assetUrl(attempt.asset))}" alt="人物首帧预览"><div><strong>${attempt.status === 'completed' ? '首帧已通过视觉门禁' : '首帧未通过视觉门禁'}</strong><span>${esc(issues.join('；') || '人物数量、构图、视线和干净画面检查通过。')}</span></div>`
  } else preview.innerHTML = ''
  $('generateFirstFrameButton').textContent = attempt?.status === 'needs_review' ? '重新生成人物首帧' : '生成人物首帧'
}

async function generateCharacterFirstFrame({ automatic = false } = {}) {
  if (!state.prepared?.prepared_plan) return toast('请先生成或优化一次执行提示词', 'error')
  const planningMode = state.prepared.planning_mode === 'ai_planned' ? 'ai_optimize' : 'operator_brief'
  const result = await busy($('generateFirstFrameButton'), () => api.generateFirstFrame({
    prepared_plan: state.prepared.prepared_plan,
    brief: state.prepared.normalized_brief || brief(),
    idempotency_key: `${state.submitKey || newIdempotencyKey()}:first-frame:${state.firstFrameAttemptNo}`,
  }))
  state.firstFrameAttempt = result
  renderFirstFrameRecommendation()
  if (result.status !== 'completed' || !result.quality_gate?.passed) {
    state.firstFrameAttemptNo += 1
    const message = `首帧未通过视觉门禁：${(result.quality_gate?.issues || ['请重新生成']).join('；')}`
    toast(message, 'error')
    if (automatic) throw new Error(`${message}；本次没有创建 H3 视频任务。`)
    return false
  }
  const asset = result.asset || {}
  if (!asset.id) throw new Error('平台未返回人物首帧资产')
  if (!state.assets.some(item => item.asset?.id === asset.id)) {
    state.assets.push({
      file: { name: asset.file_name || '人物场景首帧.png', type: asset.mime_type || 'image/png' },
      asset,
      role: 'character_first_frame',
      roleLocked: true,
    })
  }
  applySafeDefaults(); renderAssets(); invalidatePlan()
  toast('人物首帧已通过视觉门禁，正在按图生视频重新编译', 'success')
  await prepareProduction({ planningMode })
  return true
}

function shotEditor(plan, direct = false) {
  const shots = plan.shots || []
  if (!shots.length) return '<p class="empty-copy">模型未返回逐镜头字段，请在提交前检查最终提示词。</p>'
  if (direct) return shots.map(shot => `<div class="shot"><div class="shot-head"><strong>${esc(shot.start_seconds ?? 0)}–${esc(shot.end_seconds ?? '')}s</strong><span>直接提示词模式</span></div><p>${esc([shot.framing, shot.subject, shot.action, shot.camera_command, shot.audio].filter(Boolean).join(' · '))}</p></div>`).join('')
  return shots.map((shot, index) => {
    const camera = shot.camera_command || '[Static shot]'
    const options = cameraCommands.map(item => `<option value="${esc(item)}" ${item === camera ? 'selected' : ''}>${esc(item)}</option>`).join('')
    return `<article class="shot" data-shot-index="${index}">
      <div class="shot-head"><strong>镜头 ${String(index + 1).padStart(2, '0')}</strong><div class="shot-time"><label>开始秒<input type="number" step="0.01" min="0" data-shot-field="start_seconds" value="${esc(shot.start_seconds ?? 0)}"></label><label>结束秒<input type="number" step="0.01" min="0.01" data-shot-field="end_seconds" value="${esc(shot.end_seconds ?? '')}"></label></div></div>
      <div class="shot-grid">
        <div class="shot-field"><label>景别<input data-shot-field="framing" value="${esc(shot.framing || '')}"></label></div>
        <div class="shot-field"><label>运镜<select data-shot-field="camera_command">${options}</select></label></div>
        <div class="shot-field wide"><label>人物 / 主体<textarea data-shot-field="subject">${esc(shot.subject || '')}</textarea></label></div>
        <div class="shot-field wide"><label>动作<textarea data-shot-field="action">${esc(shot.action || '')}</textarea></label></div>
        <div class="shot-field"><label>场景<textarea data-shot-field="scene">${esc(shot.scene || '')}</textarea></label></div>
        <div class="shot-field"><label>光线<textarea data-shot-field="lighting">${esc(shot.lighting || '')}</textarea></label></div>
        <div class="shot-field"><label>情绪<textarea data-shot-field="mood">${esc(shot.mood || '')}</textarea></label></div>
        <div class="shot-field"><label>台词 / 声音<textarea data-shot-field="audio">${esc(shot.audio || '')}</textarea></label></div>
      </div>
    </article>`
  }).join('')
}

function collectEditedShots() {
  const originals = state.prepared?.prepared_plan?.shots || []
  return $$('#shotList [data-shot-index]').map(card => {
    const index = Number(card.dataset.shotIndex)
    const shot = { ...(originals[index] || {}) }
    card.querySelectorAll('[data-shot-field]').forEach(input => {
      const field = input.dataset.shotField
      shot[field] = ['start_seconds', 'end_seconds'].includes(field) ? Number(input.value) : input.value.trim()
    })
    return shot
  })
}

async function recompileShotPlan({ silent = false } = {}) {
  if (!state.prepared || !state.shotEditsDirty) return state.prepared?.prepared_plan
  const task = () => api.compileProduction({
    final_plan: state.prepared.prepared_plan,
    brief: state.prepared.normalized_brief || brief(),
    shots: collectEditedShots(),
  })
  const result = silent ? await task() : await busy($('recompileShots'), task, '分镜已校验并重新编译')
  state.prepared = { ...state.prepared, ...result }
  state.shotEditsDirty = false
  renderPlan(state.prepared)
  return result.prepared_plan
}

function renderPlan(result) {
  const plan = result.prepared_plan || {}
  const direct = result.planning_mode === 'direct_h3_prompt' || plan.planning_mode === 'direct_h3_prompt'
  const operator = result.planning_mode === 'operator_brief' || plan.planning_mode === 'operator_brief'
  const productionPolicy = result.production_policy || {}
  const timing = result.script_timing || plan.script_timing
  const scriptNormalization = result.script_normalization || result.ad_material_contract?.script_normalization || {}
  const durationRecommendation = Number(result.duration_recommendation_seconds || 0)
  $('planResult').classList.remove('hidden')
  $('policyVersion').textContent = result.prompt_policy_version || plan.prompt_policy_version || 'minimax-h3-context-ir-v60'
  $('plannerModel').textContent = direct
    ? '未调用 DeepSeek · H3 直控'
    : operator
      ? '未调用 AI · 用户原始提示词'
      : 'deepseek-v4-flash 优化'
  $('planResultTitle').textContent = direct ? 'H3 提示词确认' : (operator ? '执行提示词预览' : 'AI 优化结果')
  $('useOperatorPromptButton').classList.toggle('hidden', direct || operator)
  const pipeline = result.source_understanding_pipeline || {}
  $('visionModel').textContent = direct
    ? '系统参数校验'
    : pipeline.vision_model ? `${pipeline.vision_model} 素材取证${pipeline.cache_hit ? ' · 缓存' : ''}` : 'Qwen-VL 素材取证'
  const understanding = result.source_understanding || {}
  const facts = (understanding.assets || []).map(item => {
    const people = { none: '无人', partial_hands: '仅局部手部', full_person: '含完整人物', multiple_people: '含多个人物', unknown: '人物待确认' }[item.people_presence] || '人物待确认'
    const role = roleNames[item.suggested_business_role] || item.semantic_role || '视觉参考'
    return `<div><strong>${esc(role)}</strong><span>${esc(people)} · ${esc(item.visual_summary || item.product_kind || '已完成视觉识别')}</span></div>`
  })
  $('sourceUnderstanding').classList.toggle('hidden', !facts.length)
  $('sourceUnderstanding').innerHTML = facts.length
    ? `<header><strong>素材视觉清单</strong><span>${operator ? '视觉事实仅用于素材角色和安全校验，不改写用户提示词' : '视觉事实 → 文本规划；DeepSeek 不直接看图'}</span></header>${facts.join('')}`
    : ''
  $('intentLabel').textContent = [productionPolicy.intent_label, productionPolicy.routing_label].filter(Boolean).join(' · ') || '系统已自动选择生产策略'
  $('routingReason').textContent = productionPolicy.reason || '系统将按同类任务的质量、成功率和速度自动选择节点。'
  const preprocessing = (result.reference_preprocessing || []).map(item => item.strip_reference_text
    ? `已将“${item.file_name || '源素材'}”处理为无文字低频结构参考：只保留可见主体、商品槽位、局部动作、构图和节奏；原文件完整保留。`
    : `已自动截取“${item.file_name || '源素材'}”开场 ${item.clip_duration_seconds} 秒作为 H3 参考；原文件完整保留。`)
  const mentionBindings = (result.source_mentions || []).map(item => `已绑定 ${item.mention_token} → ${roleNames[item.business_role] || item.business_role || '指定素材'}；执行时按真实素材 ID 引用。`)
  const contractWarnings = result.ad_material_contract?.warnings || []
  const warningMessages = [...mentionBindings, ...preprocessing, ...contractWarnings, ...(plan.warnings || [])]
    .map(item => typeof item === 'string' ? item : item.message || JSON.stringify(item))
    .map(item => String(item || '').replace(/\s+/g, ' ').trim())
    .filter((item, index, items) => item && items.indexOf(item) === index)
  $('planWarnings').innerHTML = warningMessages.map(item => `<div class="warning">${esc(item)}</div>`).join('')
  renderFirstFrameRecommendation(result)
  $('scriptTimingResult').classList.toggle('hidden', !(scriptNormalization.changed || (timing && timing.fits === false)))
  $('scriptTimingResult').innerHTML = timing?.fits === false
    ? `<strong>当前镜头时长保持不变</strong><span>${scriptNormalization.changed ? `已从连续文本识别 ${esc(scriptNormalization.turn_count)} 个说话轮次、${esc(scriptNormalization.speaker_count)} 个角色；` : ''}完整台词约 ${esc(timing.estimated_seconds)} 秒；当前 ${esc(timing.clip_duration_seconds)} 秒只使用：${esc(String(timing.shot_script || '一句钩子').replace(/\r?\n/g, ' / '))}${durationRecommendation > Number(timing.clip_duration_seconds || 0) ? `。如需完整说完，建议手动选择 ${esc(durationRecommendation)} 秒` : ''}</span>`
    : scriptNormalization.changed
    ? `<strong>已识别连续台词</strong><span>已拆成 ${esc(scriptNormalization.turn_count)} 个说话轮次、${esc(scriptNormalization.speaker_count)} 个角色，规划、动作和配音将使用同一份标准化脚本。</span>`
    : ''
  $('directionGrid').innerHTML = (result.theme_directions || []).map((item, index) => `<article class="direction-card"><strong>${String(index + 1).padStart(2, '0')} · ${esc(item.title)}</strong><p>${esc(item.hook || item.angle)}</p></article>`).join('')
  $('shotList').innerHTML = shotEditor(plan, direct)
  $('compiledPrompt').value = plan.integrated_multimodal_description || plan.video_prompt || ''
  $('compiledPrompt').readOnly = direct
  state.shotEditsDirty = false
  $('recompileShots').classList.toggle('hidden', direct)
  $('recompileShots').disabled = true
  $('promptEditStatus').textContent = direct
    ? plan.direct_prompt_transform?.applied
      ? '操作员画面意图 · 已跳过 DeepSeek · 已确定性编译为无字静音底片；原始提示词完整留痕'
      : '操作员直接提示词 · 已跳过 DeepSeek · 如需修改请编辑上方输入框后重新校验'
    : operator
      ? '用户原始提示词已确定性编译 · 可编辑'
      : 'AI 优化结果已确定性编译 · 可编辑'
  if (plan.dialogue_delivery?.visual_audio_enabled === false) {
    // This flag controls only the H3 clean plate. Keep the user's final-audio
    // intent selected: the server deterministically disables H3 audio and
    // later requires the governed renderer before review can pass.
    $('audioEnabled').checked = true
    $('containsVoice').checked = true
    renderScriptTimingHint()
  }
  state.selectedPreset = result.recommended_preset_id || state.selectedPreset
  if (result.output_presets) { state.snapshot.output_presets = result.output_presets; renderPresets(result.output_presets) }
  $('submitButton').disabled = false
  updateFormSummary()
}

async function prepareProduction({ planningMode = 'operator_brief', button = $('prepareButton'), showSuccess = true } = {}) {
  applySafeDefaults()
  if (state.assets.some(item => item.validationError)) {
    state.assets.forEach(item => { item.validationError = '' })
    renderAssets()
  }
  $('prepareError').classList.add('hidden'); $('prepareError').textContent = ''
  let result
  try {
    result = await busy(button, () => api.prepareProduction({
      brief: brief(), source_roles: sourceRoles(), creative_option: $('creativeOption').value,
      direct_h3_prompt: isDirectPromptMode() ? $('directH3Prompt').value : undefined,
      planning_mode: isDirectPromptMode() ? 'direct_h3_prompt' : planningMode,
      theme_divergence: { enabled: $('themeEnabled').checked, direction_count: Number($('directionCount').value || 6) },
    }), showSuccess ? (isDirectPromptMode() ? 'H3 提示词已校验' : planningMode === 'operator_brief' ? '执行提示词已生成' : 'AI 已优化提示词') : '')
  } catch (error) {
    markAssetValidationError(error)
    const prefix = isDirectPromptMode() ? 'H3 提示词校验失败：' : planningMode === 'operator_brief' ? '提示词编译失败：' : 'AI 优化失败：'
    $('prepareError').textContent = `${prefix}${errorText(error)}。可以修改后重试；本次不会创建或重复提交生成任务。`
    $('prepareError').classList.remove('hidden')
    throw error
  }
  // Product, script and request are operator-owned inputs.  Normalized server
  // defaults stay in lineage and preview, but never overwrite the form.
  const intent = result.production_policy?.production_intent
  const sourcePeople = result.normalized_brief?.source_replication_contract?.source_people_presence
  if (['none', 'partial_hands'].includes(sourcePeople)) $('containsPerson').checked = false
  else if (typeof result.normalized_brief?.contains_person === 'boolean') {
    $('containsPerson').checked = result.normalized_brief.contains_person
  }
  else if (['people_dialogue', 'people_lifestyle'].includes(intent)) $('containsPerson').checked = true
  if (result.normalized_brief?.script && $('audioEnabled').checked) $('containsVoice').checked = true
  if (result.normalized_brief?.reference_source_audio_isolated) {
    toast('已移除原视频声音和文字；当前填写的新台词音频仍会生成', 'success')
  }
  if (result.source_roles?.length) {
    const byAsset = new Map(result.source_roles.map(item => [item.asset_id, item]))
    for (const item of state.assets) {
      const resolved = byAsset.get(item.asset?.id)
      if (resolved?.role && roleNames[resolved.role]) item.role = resolved.role
    }
    renderAssets()
  }
  state.prepared = result; state.submitKey = newIdempotencyKey(); renderPlan(result)
}

function rightsPayload() {
  return {
    contains_person: $('containsPerson').checked,
    contains_voice: $('containsVoice').checked,
    malware_scan: 'pending', sensitive_data_scan: 'pending', redaction: 'pending',
  }
}

async function submitDirectOriginal() {
  if (state.submitting) return
  const visualPrompt = $('requirement').value.trim()
  if (!visualPrompt) return toast('请填写画面提示词', 'error')
  const unconfirmed = state.assets.filter(item => !item.roleLocked)
  if (unconfirmed.length) {
    focusUnconfirmedAsset()
    return toast(`还差一步：请确认 ${unconfirmed.length} 个素材用途；确认后素材会自动参与生成`, 'error')
  }
  const prompt = $('compiledPrompt').value.trim() || exactPromptPreview()
  const tokenValidation = exactReferenceTokenValidation(prompt)
  if (!tokenValidation.valid) return toast(tokenValidation.message, 'error')
  state.submitting = true
  state.submitPhase = '正在原子提交…'
  updateFormSummary()
  try {
    const promptHash = await sha256Text(prompt)
    const optimization = state.promptOptimization?.adopted ? state.promptOptimization : null
    const adoptedVisualHash = optimization ? await sha256Text(visualPrompt) : ''
    const request = await saveMaterialRequest({ submit: true })
    const result = await api.materialCandidateGenerate({
      request_id: request.id,
      mode: 'direct',
      action: 'submit',
      generation: {
        final_prompt: prompt,
        params: { seed: Number($('seed').value), audio_enabled: $('audioEnabled').checked },
        prompt_preview_sha256: promptHash,
        prompt_optimization: optimization ? {
        applied: true,
        policy_version: optimization.policy_version,
        model: optimization.model,
        original_visual_prompt_sha256: optimization.original_visual_prompt_sha256,
        candidate_visual_prompt_sha256: optimization.optimized_visual_prompt_sha256,
        adopted_visual_prompt_sha256: adoptedVisualHash,
        user_edited_after_optimization: Boolean(optimization.user_edited_after_optimization || state.directFinalPromptEdited || visualPrompt !== optimization.edited_candidate_visual_prompt),
        agent: optimization.agent || {},
        } : { applied: false },
        idempotency_key: state.submitKey || newIdempotencyKey(),
      },
    })
    if (result.preview?.prompt_sha256 !== result.preview?.bridge_prompt_sha256) {
      throw new Error('页面提示词与 Bridge 执行提示词哈希不一致，任务已被系统阻止。')
    }
    const jobs = result.jobs || (result.job ? [result.job] : [])
    mergeJobs(jobs)
    renderJobs()
    state.submitKey = newIdempotencyKey()
    setQueueOpen(true)
    toast(optimization
      ? `已按你确认的优化稿提交 ${jobs.length} 条素材`
      : `已按原文提交 ${jobs.length} 条素材，未调用 AI 改写`, 'success')
  } catch (error) {
    toast(errorText(error), 'error')
    throw error
  } finally {
    state.submitting = false
    state.submitPhase = ''
    updateFormSummary()
  }
}

async function submitProduction() {
  if (state.productionMode === 'direct') return submitDirectOriginal()
  if (state.productionMode === 'strategy') return submitStrategyPlans()
  if (state.productionMode === 'replay') return submitReplay()
  if (state.submissionLocked || state.submitting) return
  state.submitting = true
  state.submitPhase = '正在解析生产要求…'
  updateFormSummary()
  try {
    if (!state.prepared) {
      await prepareProduction({
        planningMode: isDirectPromptMode() ? 'direct_h3_prompt' : requestedSubmitPlanningMode(),
        button: null,
        showSuccess: false,
      })
    }
    if (!state.prepared) return
    const firstFrameAnchor = state.prepared?.ad_material_contract?.first_frame_anchor || {}
    const hasCharacterAnchor = state.assets.some(item => item.role === 'character_first_frame')
    if (firstFrameAnchor.recommended === true && !hasCharacterAnchor && state.prepared.planning_mode !== 'direct_h3_prompt') {
      state.submitPhase = '正在锁定人物首帧…'
      updateFormSummary()
      const anchored = await generateCharacterFirstFrame({ automatic: true })
      if (!anchored || !state.assets.some(item => item.role === 'character_first_frame')) {
        throw new Error('人物首帧没有通过视觉门禁；本次没有创建 H3 视频任务。')
      }
    }
    state.submitPhase = '正在加入生产…'
    updateFormSummary()
    if (state.shotEditsDirty) await recompileShotPlan({ silent: true })
    const scheduleMode = selectedSchedule()
    const deterministicPrompt = String(state.prepared?.prepared_plan?.integrated_multimodal_description || state.prepared?.prepared_plan?.video_prompt || '').trim()
    const editedPrompt = $('compiledPrompt').value.trim()
    const directions = (state.prepared.theme_directions || []).map(item => ({ ...item, variant_count: Number($('variantsPerDirection').value || 1), final_plan: item.final_plan || state.prepared.prepared_plan }))
    const payload = {
      idempotency_key: state.submitKey || newIdempotencyKey(), brief: state.prepared.normalized_brief || brief(),
      final_plan: { ...state.prepared.prepared_plan, integrated_multimodal_description: $('compiledPrompt').value.trim() },
      user_compiled_prompt: editedPrompt && editedPrompt !== deterministicPrompt ? editedPrompt : undefined,
      plan_comparison_id: state.prepared.plan_comparison?.id,
      planning_mode: state.prepared.planning_mode,
      source_roles: state.prepared.source_roles || sourceRoles(), creative_option: $('creativeOption').value,
      output_preset_id: state.selectedPreset, duration_seconds: Number($('duration').value), quantity: Number($('quantity').value),
      theme_directions: $('themeEnabled').checked ? directions : [], schedule_mode: scheduleMode,
      not_before_at: scheduleMode === 'custom' ? $('notBefore').value : undefined,
      deadline_at: scheduleMode === 'custom' ? $('deadline').value : undefined,
      seed: Number($('seed').value), rights: rightsPayload(),
      audio_enabled: $('audioEnabled').checked,
      target_duration_seconds: Number($('duration').value) === 30 ? 30 : ($('creativeOption').value === 'continuation' ? 20 : undefined),
    }
    const result = await busy(null, () => api.submitProduction(payload), '已加入生产队列')
    state.submissionLocked = true
    state.submitting = false
    state.submitPhase = ''
    state.continuation = result.chain || null
    const returned = result.job ? [result.job] : result.jobs || []
    mergeJobs(returned); renderJobs()
    const ids = returned.map(job => job.id).filter(Boolean)
    const timelineAction = state.continuation?.timeline_handoff
      ? '<button id="downloadContinuationTimeline" type="button" class="button secondary">下载剪辑时间线 JSON</button>'
      : ''
    $('submitReceipt').innerHTML = `<strong>已提交 ${returned.length || totalQuantity()} 条生产任务</strong><span>${ids.length ? `任务 ${ids.slice(0, 3).map(esc).join('、')}${ids.length > 3 ? '…' : ''}` : '批次已进入队列'}；本次需求已锁定，防止重复点击。</span>${timelineAction}`
    $('submitReceipt').classList.remove('hidden')
    $('downloadContinuationTimeline')?.addEventListener('click', () => {
      downloadContinuationTimeline().catch(error => toast(errorText(error), 'error'))
    })
    $('planResult').classList.add('hidden')
    setQueueOpen(true)
    state.prepared = null
    state.submitKey = ''
    updateFormSummary()
    $('submitReceipt').scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  } finally {
    if (!state.submissionLocked) state.submitting = false
    state.submitPhase = ''
    updateFormSummary()
  }
}

function mergeJobs(items) {
  for (const job of items || []) {
    const index = state.jobs.findIndex(item => item.id === job.id)
    if (index >= 0) state.jobs[index] = { ...state.jobs[index], ...job }
    else state.jobs.unshift(job)
  }
}

function jobMatchesCurrentFilter(job) {
  if (state.jobFilter === 'all') return true
  if (state.jobFilter === 'active') return activeStatuses.has(job.status)
  if (state.jobFilter === 'failed') return ['failed', 'cancelled', 'rejected'].includes(job.status)
  return job.status === state.jobFilter
}

function applySnapshotJobs(items) {
  const incoming = (items || []).filter(jobMatchesCurrentFilter)
  if (!state.jobListLoaded) {
    state.jobs = incoming
  } else {
    const incomingById = new Map((items || []).map(job => [job.id, job]))
    state.jobs = state.jobs
      .map(job => incomingById.has(job.id) ? { ...job, ...incomingById.get(job.id) } : job)
      .filter(jobMatchesCurrentFilter)
    const existingIds = new Set(state.jobs.map(job => job.id))
    state.jobs.unshift(...incoming.filter(job => !existingIds.has(job.id)))
  }
  renderJobs()
}

async function loadJobs({ append = false } = {}) {
  $('queueSummary').textContent = '正在读取'
  try {
    const result = await api.jobList({ status: state.jobFilter === 'all' ? undefined : state.jobFilter, cursor: append ? state.jobCursor : undefined, limit: 30 })
    state.jobs = append ? [...state.jobs, ...(result.items || [])] : result.items || []
    const statusCounts = result.status_counts || {}
    state.jobActiveTotal = [...activeStatuses].reduce((total, status) => total + Number(statusCounts[status] || 0), 0)
    $('metricProducing').textContent = String(state.jobActiveTotal)
    state.jobCursor = result.next_cursor || ''; state.jobHasMore = Boolean(result.has_more)
    state.jobListLoaded = true
    renderJobs()
  } catch (error) {
    if (isCapabilityQuotaError(error)) showRunRecovery()
    else renderQueueLoadError(error)
    throw error
  }
}

function renderJobs() {
  $('jobTable').innerHTML = state.jobs.length ? state.jobs.map(job => {
    const title = job.business_title || job.prompt?.creative_goal || job.id
    const attempt = job.latest_attempt || job.attempt || {}
    const progress = jobProgressLabel(job)
    const eta = attempt.eta_seconds || job.result?.eta?.seconds || attempt.metrics?.total_seconds
    const etaFallback = job.mode === 'video_enhance' ? '等待高清实测' : '等待同规格实测'
    const diagnostic = job.error ? ` · ${job.error}` : ''
    return `<div class="job-row" title="${esc(job.error || '')}"><div><strong>${esc(title)}</strong><small>${esc(job.id)} · ${isoLocal(job.created_at)}${esc(diagnostic)}</small></div><span class="status-chip ${esc(job.status)}">${statusNames[job.status] || esc(job.status)}</span><span>${esc(attempt.instance_name || job.assigned_instance_name || attempt.instance_id || job.assigned_instance_id || '等待分配')}</span><span>${esc(progress)}</span><span>${seconds(eta, etaFallback)}</span></div>`
  }).join('') : '<p class="empty-copy">当前筛选下没有任务</p>'
  const visibleActive = state.jobs.filter(job => activeStatuses.has(job.status)).length
  const active = Math.max(Number(state.jobActiveTotal || 0), visibleActive)
  $('queueSummary').textContent = `${active} 个运行中 · 本页 ${state.jobs.length} 条`
  $('loadMoreJobs').classList.toggle('hidden', !state.jobHasMore)
}

function jobProgressLabel(job) {
  if (['awaiting_review', 'approved', 'rejected', 'syncing', 'synced'].includes(job.status)) return '100%'
  const raw = Number(job.progress_percent)
  if (Number.isFinite(raw) && raw > 0) return `${Math.min(99, Math.round(raw))}%`
  if (String(job.error || '').includes('连接短暂中断')) return '节点重连中'
  if (String(job.error || '').includes('自动重派')) return '任务恢复中'
  if (job.mode === 'video_enhance' && job.status === 'running') return '1080p 高清处理中'
  if (job.mode === 'video_enhance' && job.status === 'collecting') return '高清结果回收中'
  if (job.status === 'running') return 'GPU 生成中'
  if (job.status === 'collecting') return '结果回收中'
  if (job.status === 'assigned') return '准备中'
  if (job.status === 'queued') return '排队'
  return '—'
}

function rawPosterUrl(item) { return item.poster?.download_url || item.poster?.asset?.download_url || '' }
function reviewImageKey(value = {}) {
  const poster = value.poster || value
  return String(poster?.sha256 || poster?.id || poster?.asset_id || poster?.download_url || '')
}
function posterUrl(item) { return state.reviewImageUrls.get(reviewImageKey(item)) || rawPosterUrl(item) }

function compactReviewItem(item = {}) {
  const job = item.job || {}
  return {
    business_title: item.business_title, prompt_summary: item.prompt_summary,
    poster: item.poster, quality: item.quality, technical_validation: item.technical_validation,
    cloud_sync: item.cloud_sync, review_ready_at: item.review_ready_at,
    job: {
      id: job.id, status: job.status, mode: job.mode, params: job.params,
      business_title: job.business_title, created_at: job.created_at, updated_at: job.updated_at,
      completed_at: job.completed_at,
      output_preset_id: job.output_preset_id, model_version: job.model_version,
      assigned_instance_id: job.assigned_instance_id, assigned_instance_name: job.assigned_instance_name,
      latest_attempt: job.latest_attempt, attempt: job.attempt,
    },
  }
}

function reviewReadyAt(item = {}) {
  return item.review_ready_at || item.job?.completed_at || item.job?.created_at || ''
}

function sortReviewsNewestFirst(items = []) {
  return [...items].sort((left, right) => {
    const leftTime = Date.parse(reviewReadyAt(left)) || 0
    const rightTime = Date.parse(reviewReadyAt(right)) || 0
    if (rightTime !== leftTime) return rightTime - leftTime
    return String(right?.job?.id || '').localeCompare(String(left?.job?.id || ''))
  })
}

function reviewCreatedLabel(value) {
  const date = new Date(value || 0)
  if (Number.isNaN(date.getTime())) return '时间待同步'
  const pad = number => String(number).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function persistReviewList() {
  try {
    sessionStorage.setItem(reviewListStorageKey, JSON.stringify({
      saved_at: Date.now(), items: sortReviewsNewestFirst(state.reviews).slice(0, 60).map(compactReviewItem),
      cursor: state.reviewCursor, has_more: state.reviewHasMore,
      status: $('reviewStatus')?.value || 'awaiting_review', search: $('reviewSearch')?.value || '',
    }))
  } catch { /* storage may be disabled or full */ }
}

function restoreReviewList() {
  try {
    const cached = JSON.parse(sessionStorage.getItem(reviewListStorageKey) || 'null')
    if (!cached?.items?.length || Date.now() - Number(cached.saved_at || 0) > 12 * 60 * 60 * 1000) return
    state.reviews = sortReviewsNewestFirst(cached.items)
    state.reviewCursor = cached.cursor || ''
    state.reviewHasMore = Boolean(cached.has_more)
    if (cached.status && [...$('reviewStatus').options].some(option => option.value === cached.status)) $('reviewStatus').value = cached.status
    $('reviewSearch').value = cached.search || ''
    // 缓存只负责首屏秒开，不能冒充实时列表。进入审片后仍只调用一次
    // review.list，用服务端 created_at DESC 顺序和最新签名封面替换缓存。
    state.reviewListLoaded = false
    renderReviewList()
    warmReviewImages(state.reviews).catch(() => {})
  } catch { /* discard corrupt cache */ }
}

async function cacheReviewImage(url, key) {
  if (!url || !key || state.reviewImageUrls.has(key) || !('caches' in window)) return
  const cache = await caches.open(reviewImageCacheName)
  const stableCacheUrl = new URL(`/__skillforge_media_review_cache__/${encodeURIComponent(key)}`, window.location.origin).href
  let response = await cache.match(stableCacheUrl)
  if (!response) {
    response = await fetch(url, { credentials: 'same-origin' })
    if (!response.ok || !String(response.headers.get('content-type') || '').startsWith('image/')) return
    await cache.put(stableCacheUrl, response.clone())
    const cacheKeys = await cache.keys()
    const expiredKeys = cacheKeys.slice(0, Math.max(0, cacheKeys.length - reviewImageDiskEntryLimit))
    await Promise.allSettled(expiredKeys.map(request => cache.delete(request)))
  }
  const objectUrl = URL.createObjectURL(await response.blob())
  while (state.reviewImageUrls.size >= reviewImageObjectUrlLimit) {
    const oldestKey = state.reviewImageUrls.keys().next().value
    const oldestUrl = state.reviewImageUrls.get(oldestKey)
    if (oldestUrl) URL.revokeObjectURL(oldestUrl)
    state.reviewImageUrls.delete(oldestKey)
  }
  state.reviewImageUrls.set(key, objectUrl)
}

async function warmReviewImages(items) {
  const visible = (items || []).slice(0, 12)
  await Promise.allSettled(visible.map(item => cacheReviewImage(rawPosterUrl(item), reviewImageKey(item))))
  renderReviewList()
}

function manifestCacheKey(jobId, compareJobId = '') { return `${jobId}:${compareJobId || ''}` }
function persistManifestCache() {
  try {
    const entries = [...state.reviewManifestCache.entries()].slice(-8)
    sessionStorage.setItem(reviewManifestStorageKey, JSON.stringify(entries))
  } catch { /* storage may be disabled or full */ }
}
function restoreManifestCache() {
  try {
    const entries = JSON.parse(sessionStorage.getItem(reviewManifestStorageKey) || '[]')
    const freshEntries = (Array.isArray(entries) ? entries : []).filter(([, value]) => (
      value?.manifest && Date.now() - Number(value.saved_at || 0) < reviewManifestCacheTtlMs
    ))
    state.reviewManifestCache = new Map(freshEntries)
  } catch { state.reviewManifestCache = new Map() }
}
function invalidateManifestCache(jobId) {
  for (const key of [...state.reviewManifestCache.keys()]) if (key.startsWith(`${jobId}:`) || key.includes(`:${jobId}`)) state.reviewManifestCache.delete(key)
  persistManifestCache()
}
function reviewNodeLabel(job = {}) {
  const attempt = job.latest_attempt || job.attempt || {}
  const raw = String(attempt.instance_name || job.assigned_instance_name || attempt.instance_id || job.assigned_instance_id || '').trim()
  if (/5080/i.test(raw)) return 'RTX 5080'
  if (/pro\s*6000|ai[ -]?235/i.test(raw)) return 'RTX PRO 6000'
  return raw || '节点待上报'
}
function reviewVersionLabel(job = {}) {
  const shortId = String(job.id || '').replace(/^mvj-/, '').slice(-6)
  return `${reviewNodeLabel(job)}${shortId ? ` · ${shortId}` : ''}`
}

function reviewCardLevel(job = {}, quality = {}) {
  if (job.status === 'rejected') return 'low'
  return quality.level || 'pending'
}

function reviewCardLevelTitle(job = {}, quality = {}) {
  if (job.status === 'rejected') return '已人工驳回，不计为可用素材'
  const labels = { high: 'AI 质检：高', medium: 'AI 质检：中', low: 'AI 质检：低', pending: 'AI 质检：待分析' }
  return labels[quality.level] || labels.pending
}

function materialStageFor(job = {}, quality = {}) {
  const supplied = quality.material_stage || {}
  if (supplied.id) return supplied
  const prompt = job.prompt || {}
  const contract = prompt.ad_material_contract || {}
  const brief = prompt.production_brief || {}
  const commercialText = `${brief.script || ''} ${brief.request || ''} ${brief.operator_input || ''} ${contract.executable_script || ''}`.toLowerCase()
  const completeDelivery = /完整投流成片|可直接投放|直接投流|点击购买|立即下单|赶紧下单|两位数到手|活动价|优惠价|限时优惠|cta/.test(commercialText)
  const productComponent = prompt.product_overlay?.enabled === true
    && contract.assembly_plan?.commercial_preference === 'dynamic_clean_plate_then_deterministic_product_overlay'
    && !String(brief.script || contract.executable_script || '').trim()
    && !completeDelivery
  if (productComponent) return {
    id: 'assembled_product_component', label: '商品镜头', approval_scope: 'component_quality_only',
    requires_assembly: true, commercial_positive_eligible: false,
    description: '真实商品像素已完成受控合成；当前只评价商品镜头，仍需与钩子、卖点或转化动作组装。',
  }
  if (prompt.product_overlay?.enabled === true) return {
    id: 'assembled_product_material', label: '商品合成版', approval_scope: 'assembled_material_quality',
    requires_assembly: false, commercial_positive_eligible: true,
    description: '已合成受控商品素材；仍需核验商品真实性、卖点和投放合规。',
  }
  const profile = String(prompt.h3_execution_profile || '')
  const brandGuardrails = prompt.brand_guardrails || {}
  const excludesProduct = [
    'clean_dialogue_plate_silent_v1', 'clean_people_plate_silent_v4',
    'direct_clean_people_plate_silent_v1', 'motion_only_no_text_ascii_v3',
  ].includes(profile)
    || brandGuardrails.require_reference_image === false
      && /不展示品牌|无品牌|不要求产品保真|合成/.test(String(brandGuardrails.reason || ''))
  if (excludesProduct) return {
    id: 'component_plate', label: /dialogue|people|person/.test(profile) ? '人物底片' : '视觉底片',
    approval_scope: 'component_quality_only', requires_assembly: true, commercial_positive_eligible: false,
    description: '当前只评价底片本身；需与已审核商品、卖点或活动信息组装后再评价投流可用度。',
  }
  const productReference = (job.reference_assets || []).some(item => ['product_packshot', 'product_detail'].includes(item.business_role || item.role))
  if (productReference) return {
    id: 'generated_product_candidate', label: '商品生成候选', approval_scope: 'candidate_quality',
    requires_assembly: false, commercial_positive_eligible: true,
    description: '已提供商品参考，但包装、Logo、文字和商品几何真实性仍需人工核验。',
  }
  return {
    id: 'generated_candidate', label: '生成候选', approval_scope: 'candidate_quality',
    requires_assembly: false, commercial_positive_eligible: false,
    description: '当前没有可验证的真实商品依据；可以作为创意候选，但不能直接作为商业正样本。',
  }
}

function reviewItemFromEventJob(job = {}) {
  const manifest = job.result?.review_manifest || {}
  const quality = job.result?.quality_analysis || {}
  const score = Number(quality.applicable_overall_score || quality.overall_score || quality.scores?.overall_score || 0)
  const signedExpiry = Number(new URL(manifest.poster?.download_url || '', window.location.origin).searchParams.get('expires') || 0)
  const poster = manifest.poster?.delivery === 'signed_project_review'
    && signedExpiry > Math.floor(Date.now() / 1000) + 60
    ? manifest.poster
    : undefined
  return {
    job, business_title: job.business_title || job.id,
    review_ready_at: job.completed_at || job.created_at,
    prompt_summary: String(job.prompt?.creative_goal || job.prompt?.video_prompt || '').slice(0, 240),
    ...(poster ? { poster } : {}),
    quality: { level: score >= .8 ? 'high' : score >= .6 ? 'medium' : score ? 'low' : 'pending', score, summary: quality.summary || quality.notes || '', material_stage: quality.material_stage },
    technical_validation: job.result?.technical_validation || {},
  }
}

function mergeReviewJobsFromSnapshot(jobs) {
  const status = $('reviewStatus')?.value || 'awaiting_review'
  const search = ($('reviewSearch')?.value || '').trim().toLowerCase()
  let changed = false
  for (const job of jobs || []) {
    mergeSelectedQualityFromJob(job)
    const index = state.reviews.findIndex(item => item.job?.id === job.id)
    const matchesStatus = status === 'all' || job.status === status
    const haystack = `${job.business_title || ''} ${job.id || ''}`.toLowerCase()
    const matches = matchesStatus && (!search || haystack.includes(search))
    if (!matches && index >= 0) { state.reviews.splice(index, 1); changed = true; continue }
    if (!matches) continue
    // The authoritative review.list owns pagination.  Initial snapshots also
    // contain historical jobs ordered for queue observation, not review
    // browsing; appending those rows created out-of-page cards whose old,
    // owner-scoped poster links could not be displayed.  Existing rows may be
    // updated, and genuinely newer completions may be inserted at the top.
    if (index < 0 && !state.reviewListLoaded) continue
    const oldestVisibleReviewAt = reviewReadyAt(state.reviews[state.reviews.length - 1])
    if (index < 0 && state.reviews.length >= 30
      && (Date.parse(job.completed_at || job.created_at || '') || 0) <= (Date.parse(oldestVisibleReviewAt) || 0)) continue
    const previous = index >= 0 ? state.reviews[index] : null
    if (previous?.job?.updated_at !== job.updated_at) invalidateManifestCache(job.id)
    const eventItem = reviewItemFromEventJob(job)
    // Snapshot/event jobs contain the immutable DB manifest, whose historical
    // signed URL may already be expired.  Never let it replace the fresh URL
    // returned by video.review.list.
    const next = {
      ...previous, ...eventItem,
      ...(previous?.poster ? { poster: previous.poster } : {}),
      cloud_sync: previous?.cloud_sync,
      job: { ...(previous?.job || {}), ...job },
    }
    if (index >= 0) state.reviews[index] = next
    else state.reviews.unshift(next)
    changed = true
  }
  if (!changed) return
  state.reviews = sortReviewsNewestFirst(state.reviews).slice(0, 60)
  renderReviewList(); persistReviewList(); warmReviewImages(state.reviews).catch(() => {})
}
const candidateMethodNames = { direct: '原文直出', ai_strategy: 'AI 策略', replay: '视频复刻', historical: '历史候选' }
const editorialNames = { pending: '待选片', selected: '已选用', held: '暂缓', rejected: '已驳回' }

function candidateListFilters() {
  const view = state.reviewView || $('reviewStatus').value || 'pending'
  const filters = {
    technical_status: 'passed',
    generation_method: $('reviewMethod').value,
    query: $('reviewSearch').value.trim(),
    tags: [...state.reviewTags],
    tag_match: 'all',
    sort: $('reviewSort').value,
    limit: 30,
  }
  if (view === 'new') filters.entered_after = new Date(Date.now() - 7 * 86400000).toISOString()
  if ($('reviewSource')?.value && $('reviewSource').value !== 'all') filters.source = $('reviewSource').value
  if ($('reviewProduct')?.value.trim()) filters.product = $('reviewProduct').value.trim()
  if ($('reviewBatch')?.value.trim()) filters.batch_id = $('reviewBatch').value.trim()
  if ($('reviewAssignee')?.value.trim()) filters.assignee_id = $('reviewAssignee').value.trim()
  if (['pending', 'selected', 'held', 'rejected'].includes(view)) filters.editorial_status = view
  if (view === 'delivered') filters.delivery_status = 'delivered'
  if (view === 'unlinked') { filters.editorial_status = 'selected'; filters.attribution_status = 'unlinked' }
  if (view === 'spending') filters.attribution_status = 'spending'
  return filters
}

function currentReviewPreference() {
  return {
    sort: $('reviewSort').value,
    density: state.wallDensity,
    filters: {
      status: state.reviewView,
      generation_method: $('reviewMethod').value,
      source: $('reviewSource').value,
      product: $('reviewProduct').value.trim(),
      batch_id: $('reviewBatch').value.trim(),
      assignee_id: $('reviewAssignee').value.trim(),
      tags: [...state.reviewTags],
    },
  }
}

function saveReviewPreference() {
  return api.materialReviewPreferenceSave(currentReviewPreference()).catch(() => {})
}

function candidateToReviewItem(candidate) {
  const job = candidate.job || {}
  const editorialJobStatus = {
    pending: 'awaiting_review', selected: 'approved', held: 'awaiting_review', rejected: 'rejected',
  }[candidate.editorial_status] || job.status
  return {
    candidate_id: candidate.id,
    business_title: candidate.business_title || candidate.request?.title || candidate.id,
    prompt_summary: candidate.request?.title || candidateMethodNames[candidate.generation_method] || '生成候选素材',
    review_ready_at: candidate.entered_selection_at || candidate.created_at,
    poster: candidate.poster || undefined,
    preview: candidate.preview_asset || undefined,
    tags: candidate.tags || [],
    quality: candidate.technical?.quality || { level: candidate.technical_status === 'passed' ? 'medium' : 'pending' },
    technical_validation: candidate.technical?.technical_validation || candidate.technical || {},
    job: {
      id: candidate.media_job_id,
      status: editorialJobStatus,
      mode: job.mode,
      business_title: candidate.business_title,
      output_preset_id: job.output_preset_id,
      assigned_instance_id: job.assigned_instance_id,
      created_at: candidate.created_at,
      updated_at: candidate.updated_at,
      params: {
        duration_seconds: job.duration_seconds,
        delivery_duration_seconds: job.duration_seconds,
        width: job.width,
        height: job.height,
      },
      result: { technical_validation: candidate.technical?.technical_validation || candidate.technical || {} },
    },
  }
}

function wallPoster(candidate) { return candidate.poster?.download_url || '' }
function wallAspect(candidate) {
  const width = Number(candidate.job?.width || 0); const height = Number(candidate.job?.height || 0)
  if (width > 0 && height > 0) return `${width} / ${height}`
  return '9 / 16'
}

function releaseWallPreview(card) {
  if (!card) return
  const video = card.querySelector('video')
  if (video) { video.pause(); video.removeAttribute('src'); video.load(); video.remove() }
  card.querySelector('img')?.classList.remove('preview-hidden')
  card.classList.remove('preview-loading', 'preview-playing', 'preview-failed')
  const status = card.querySelector('.wall-preview-status')
  if (status) { status.className = 'wall-preview-status hidden'; status.textContent = '' }
  if (state.activeWallPreviewCard === card) state.activeWallPreviewCard = null
}

function startWallPreview(card, candidate) {
  window.clearTimeout(state.hoverPreviewTimer)
  state.hoverPreviewTimer = window.setTimeout(() => {
    if (!card.matches(':hover') || card.querySelector('video')) return
    if (state.activeWallPreviewCard && state.activeWallPreviewCard !== card) releaseWallPreview(state.activeWallPreviewCard)
    const url = candidate.preview_asset?.download_url || candidate.result_asset?.download_url
    const status = card.querySelector('.wall-preview-status')
    const updateStatus = (mode, message = '') => {
      card.classList.remove('preview-loading', 'preview-playing', 'preview-failed')
      if (mode) card.classList.add(`preview-${mode}`)
      if (status) {
        status.className = `wall-preview-status${mode ? ` ${mode}` : ' hidden'}`
        status.textContent = message
      }
    }
    if (!url) { updateStatus('failed', '暂无预览，点击打开'); return }
    const video = document.createElement('video')
    video.muted = true; video.defaultMuted = true; video.loop = true; video.autoplay = true
    video.playsInline = true; video.preload = 'auto'; video.src = url
    video.setAttribute('aria-label', `${candidate.business_title || '候选素材'}静音预览`)
    video.setAttribute('aria-hidden', 'true')
    updateStatus('loading', '预览加载中')
    card.querySelector('.material-wall-cover').append(video)
    state.activeWallPreviewCard = card
    const tryPlay = () => {
      if (!card.matches(':hover') || !video.isConnected) return
      video.play().catch(() => updateStatus('failed', '自动播放受限，点击打开'))
    }
    video.addEventListener('loadeddata', tryPlay, { once: true })
    video.addEventListener('canplay', tryPlay, { once: true })
    video.addEventListener('playing', () => {
      if (!card.matches(':hover')) { releaseWallPreview(card); return }
      card.querySelector('img')?.classList.add('preview-hidden')
      updateStatus('playing')
    })
    video.addEventListener('waiting', () => updateStatus('loading', '预览缓冲中'))
    video.addEventListener('stalled', () => updateStatus('loading', '网络较慢，正在重试'))
    video.addEventListener('error', () => updateStatus('failed', '预览加载失败，点击打开'))
    video.load()
    tryPlay()
  }, 300)
}

function resizeWallCard(card) {
  const wall = $('materialWall')
  const row = Number.parseFloat(getComputedStyle(wall).gridAutoRows) || 8
  const gap = Number.parseFloat(getComputedStyle(wall).rowGap) || 16
  card.style.gridRowEnd = `span ${Math.max(1, Math.ceil((card.getBoundingClientRect().height + gap) / (row + gap)))}`
}

function renderReviewTagFilters() {
  $('reviewTagChips').innerHTML = [...state.reviewTags].map(tag => `<button type="button" class="review-tag-chip" data-remove-review-tag="${esc(tag)}" title="移除标签筛选">${esc(tag)} ×</button>`).join('')
  const suggestions = [...new Set(state.candidates.flatMap(candidate => candidate.tags || []))].sort((a, b) => a.localeCompare(b, 'zh-CN'))
  $('reviewTagSuggestions').innerHTML = suggestions.map(tag => `<option value="${esc(tag)}"></option>`).join('')
  $$('[data-remove-review-tag]').forEach(button => button.onclick = () => {
    state.reviewTags.delete(button.dataset.removeReviewTag)
    renderReviewTagFilters(); loadReviews().catch(error => toast(errorText(error), 'error')); saveReviewPreference()
  })
}

function addReviewTag(tag) {
  const normalized = String(tag || '').trim()
  if (!normalized || state.reviewTags.has(normalized)) return false
  state.reviewTags.add(normalized)
  renderReviewTagFilters()
  return true
}

function renderMaterialWall() {
  const wall = $('materialWall')
  wall.dataset.density = state.wallDensity
  wall.classList.toggle('compact', state.wallDensity === 'compact')
  $('materialWallSummary').textContent = `${state.candidates.length} 条已加载${state.candidateHasMore ? ' · 还有更多' : ''}`
  $('materialWallTitle').textContent = ({ pending: '待我选片', new: '本周新素材', selected: '已选用', held: '暂缓', rejected: '已驳回', delivered: '已入库', unlinked: '待归因', spending: '已产生消耗' })[state.reviewView] || '候选素材'
  wall.innerHTML = state.candidates.length ? state.candidates.map((candidate, index) => {
    const poster = wallPoster(candidate)
    const duration = Number(candidate.job?.duration_seconds || 0)
    const product = candidate.request?.business?.product || candidate.business_title || '未命名素材'
    const stateLabel = editorialNames[candidate.editorial_status] || candidate.editorial_status
    const spend = Number(candidate.cumulative_spend || 0)
    const tags = (candidate.tags || []).slice(0, state.wallDensity === 'compact' ? 3 : 6)
    return `<article class="material-wall-card" tabindex="0" data-candidate-id="${esc(candidate.id)}">
      <div class="material-wall-cover" style="aspect-ratio:${esc(wallAspect(candidate))}">${poster
        ? `<img loading="${index < 8 ? 'eager' : 'lazy'}" fetchpriority="${index < 8 ? 'high' : 'auto'}" decoding="async" src="${esc(poster)}" alt="${esc(candidate.business_title || product)}">`
        : '<div class="wall-poster-placeholder"><b>封面生成中</b><span>系统将自动重试非黑帧</span></div>'}
        <span class="wall-preview-status hidden" aria-live="polite"></span>
        <span class="wall-duration">${duration ? `${duration}s` : '时长待检'}</span>
      </div>
      <div class="material-wall-copy"><span>${esc(candidateMethodNames[candidate.generation_method] || candidate.generation_method)}</span><strong>${esc(product)}</strong><p>${esc(candidate.request?.title || candidate.business_title || candidate.id)}</p><div class="material-wall-tags">${tags.map(tag => `<button type="button" class="material-tag" data-wall-tag="${esc(tag)}">${esc(tag)}</button>`).join('')}</div><footer><small>${esc(stateLabel)}</small>${spend > 0 ? `<b>消耗 ¥${spend.toFixed(2)}</b>` : '<small>候选素材</small>'}</footer></div>
    </article>`
  }).join('') : '<p class="empty-copy">当前筛选下没有通过技术门禁的候选素材</p>'
  for (const card of $$('[data-candidate-id]')) {
    const candidate = state.candidates.find(item => item.id === card.dataset.candidateId)
    card.onmouseenter = () => startWallPreview(card, candidate)
    card.onmouseleave = () => { window.clearTimeout(state.hoverPreviewTimer); releaseWallPreview(card) }
    card.onclick = () => openCandidateReview(candidate.id).catch(error => toast(errorText(error), 'error'))
    card.onkeydown = event => { if (event.key === 'Enter') card.click() }
    card.querySelectorAll('img').forEach(image => image.addEventListener('load', () => resizeWallCard(card), { once: true }))
    requestAnimationFrame(() => resizeWallCard(card))
  }
  $$('[data-wall-tag]').forEach(button => button.onclick = event => {
    event.stopPropagation()
    if (addReviewTag(button.dataset.wallTag)) loadReviews().catch(error => toast(errorText(error), 'error'))
  })
  state.wallPreviewObserver?.disconnect()
  state.wallPreviewObserver = new IntersectionObserver(entries => {
    entries.filter(entry => !entry.isIntersecting).forEach(entry => releaseWallPreview(entry.target))
  }, { rootMargin: '20px' })
  $$('[data-candidate-id]').forEach(card => state.wallPreviewObserver.observe(card))
  renderReviewTagFilters()
  $('loadMoreCandidates').classList.toggle('hidden', !state.candidateHasMore)
}

async function loadReviews({ append = false } = {}) {
  const result = await api.materialCandidateList({
    ...candidateListFilters(),
    cursor: append ? state.candidateCursor : undefined,
  })
  const combined = append ? [...state.candidates, ...(result.items || [])] : result.items || []
  state.candidates = [...new Map(combined.map(item => [item.id, item])).values()]
  state.candidateCursor = result.next_cursor || ''
  state.candidateHasMore = Boolean(result.has_more && result.next_cursor)
  state.reviews = state.candidates.map(candidateToReviewItem)
  state.reviewCursor = state.candidateCursor
  state.reviewHasMore = state.candidateHasMore
  state.reviewListLoaded = true
  renderMaterialWall(); renderReviewList()
  return result
}

async function openCandidateReview(candidateId, compareCandidateId = '') {
  const requestToken = ++state.reviewRequestToken
  state.activeCandidateId = candidateId
  state.reviewView = state.reviewView || 'pending'
  clearViewer()
  state.reviewWallScrollY = window.scrollY
  if (!$('reviewDialog').open) $('reviewDialog').showModal()
  const result = await api.materialReviewSession(candidateId, { compare_candidate_id: compareCandidateId || undefined })
  if (requestToken !== state.reviewRequestToken || candidateId !== state.activeCandidateId) return
  state.reviewSession = result
  state.reviewManifest = result.manifest
  state.reviewJobId = result.candidate?.media_job_id || ''
  state.compareJobId = compareCandidateId
  $('reviewBatchProgress').textContent = `候选 ${result.position || '—'}/${result.total || '—'}`
  $('previousCandidate').disabled = !result.previous_candidate_id
  $('nextCandidate').disabled = !result.next_candidate_id
  $('deliveryButton').classList.toggle('hidden', result.candidate?.editorial_status !== 'selected')
  renderCandidateTags(result.candidate)
  renderManifest(result.manifest)
  renderReviewList()
}

function showMaterialWall() {
  for (const video of $$('[data-candidate-id] video')) releaseWallPreview(video.closest('[data-candidate-id]'))
  if ($('reviewDialog').open) $('reviewDialog').close()
  state.reviewJobId = ''; state.compareJobId = ''; state.reviewManifest = null; state.reviewSession = null
  renderMaterialWall()
  requestAnimationFrame(() => window.scrollTo({ top: state.reviewWallScrollY, behavior: 'instant' }))
}

function renderCandidateTags(candidate = state.reviewSession?.candidate) {
  const tags = candidate?.tags || []
  $('reviewCandidateTags').innerHTML = tags.length
    ? tags.map(tag => `<button type="button" class="material-tag" data-review-detail-tag="${esc(tag)}">${esc(tag)}</button>`).join('')
    : '<span class="empty-copy">暂无标签</span>'
  $$('[data-review-detail-tag]').forEach(button => button.onclick = () => {
    addReviewTag(button.dataset.reviewDetailTag)
    showMaterialWall()
    loadReviews().catch(error => toast(errorText(error), 'error'))
  })
}

async function editActiveCandidateTags() {
  const candidate = state.reviewSession?.candidate
  if (!candidate) return toast('请先选择候选素材', 'error')
  const value = await requestText({
    title: '编辑候选标签',
    description: '使用中文逗号或英文逗号分隔；保存后可直接用于顶部搜索。',
    defaultValue: (candidate.tags || []).join('，'),
    placeholder: '例如：超快感，双人对话，9:16',
    confirmLabel: '保存标签',
  })
  if (value === null) return
  const tags = [...new Set(value.split(/[，,\n]/).map(item => item.trim()).filter(Boolean))].slice(0, 50)
  const result = await api.materialCandidateTagUpdate({ candidate_id: candidate.id, operation: 'replace', tags })
  candidate.tags = result.tags || tags
  const wallCandidate = state.candidates.find(item => item.id === candidate.id)
  if (wallCandidate) wallCandidate.tags = candidate.tags
  renderCandidateTags(candidate); renderMaterialWall(); toast('标签已保存', 'success')
}

function renderReviewList() {
  state.reviews = sortReviewsNewestFirst(state.reviews)
  $('reviewList').innerHTML = state.reviews.length ? state.reviews.map((item, index) => {
    const job = item.job || {}; const url = posterUrl(item)
    const materialStage = materialStageFor(job, item.quality || {})
    const priority = index < 8 ? 'eager' : 'lazy'
    const duration = Number(job.params?.delivery_duration_seconds || job.params?.target_duration_seconds)
      || (job.params?.frames ? Math.round(job.params.frames / (job.params.fps || 24)) : 0)
    const status = statusNames[job.status] || job.status
    const readyAt = reviewReadyAt(item)
    const selectionId = item.candidate_id || job.id
    return `<label class="review-card ${job.id === state.reviewJobId ? 'active' : ''}" data-review-job="${esc(job.id)}" data-review-candidate="${esc(item.candidate_id || '')}"><input class="review-checkbox" type="checkbox" data-review-check="${esc(selectionId)}" ${state.selectedReviewIds.has(selectionId) ? 'checked' : ''}><span class="review-cover">${url ? `<img loading="${priority}" fetchpriority="${index < 4 ? 'high' : 'auto'}" decoding="async" src="${esc(url)}" alt="${esc(item.business_title)}">` : '<b>封面待加载</b>'}<span>${duration ? `${duration}s` : ''}</span></span><span class="review-copy"><span class="material-stage ${esc(materialStage.id)}">${esc(materialStage.label)}</span><strong>${esc(item.business_title || job.id)}</strong><p>${esc(item.prompt_summary || '暂无提示词摘要')}</p><span class="review-meta"><span><i class="quality-dot ${esc(reviewCardLevel(job, item.quality))}" title="${esc(reviewCardLevelTitle(job, item.quality))}"></i><small>${esc(reviewVersionLabel(job))}</small></span><small title="进入审片：${esc(isoLocal(readyAt))}">${esc(reviewCreatedLabel(readyAt))} · ${esc(status)}</small></span></span></label>`
  }).join('') : '<p class="empty-copy">当前筛选下没有审片任务</p>'
  $$('[data-review-job]').forEach(card => card.onclick = event => {
    if (event.target.matches('[data-review-check]')) { const id = event.target.dataset.reviewCheck; event.target.checked ? state.selectedReviewIds.add(id) : state.selectedReviewIds.delete(id); return }
    if (card.dataset.reviewCandidate) openCandidateReview(card.dataset.reviewCandidate).catch(error => toast(errorText(error), 'error'))
    else openReview(card.dataset.reviewJob)
  })
  $('loadMoreReviews').classList.toggle('hidden', !state.reviewHasMore)
  const compareSelect = $('compareJobSelect')
  compareSelect.innerHTML = '<option value="">选择 B 片</option>' + state.reviews.filter(item => item.candidate_id !== state.activeCandidateId).map(item => `<option value="${esc(item.candidate_id || item.job.id)}">${esc(`${item.business_title} · ${reviewVersionLabel(item.job)}`)}</option>`).join('')
  const preferredCompareId = state.compareSelectionJobId || state.compareJobId
  compareSelect.value = [...compareSelect.options].some(option => option.value === preferredCompareId)
    ? preferredCompareId
    : ''
}

function clearViewer() {
  $('viewerShell').classList.add('loading')
  for (const video of [$('reviewVideoA'), $('reviewVideoB')]) releaseVideo(video)
  $('noVideo').classList.remove('hidden')
  $('reviewJobTitle').textContent = '正在加载审片素材…'
  $('reviewJobMeta').textContent = '正在读取封面、关键帧和质检结果'
  $('reviewAudioStatus').textContent = '音轨状态待读取'
  $('reviewAudioStatus').className = 'audio-status loading'
  $('reviewAudioStatus').title = '正在检查当前成片是否包含可播放音轨'
  $('reviewPlayToggle').disabled = true
  $('reviewPlayToggle').textContent = '▶ 播放'
  $('reviewPlayToggle').setAttribute('aria-pressed', 'false')
  $('reviewPlayToggle').title = '选择成片后播放'
  $('reviewSoundToggle').disabled = true
  $('reviewSoundToggle').textContent = '检查音轨…'
  $('reviewSoundToggle').setAttribute('aria-pressed', 'false')
  $('reviewSoundToggle').title = '正在检查当前成片是否包含可播放音轨'
  $('filmstrip').innerHTML = ''
  $('sourceStrip').innerHTML = ''
  $('qualityRuntime').innerHTML = '<span>正在读取质检模型来源…</span>'
  $('materialStageNotice').classList.add('hidden')
  $('materialStageNotice').innerHTML = ''
  $('qualitySummary').innerHTML = '<p class="empty-copy">正在读取 AI 视觉质检…</p>'
  $('criticalIssues').innerHTML = '<p class="empty-copy">正在读取问题标注…</p>'
  $('repairAdvice').textContent = '正在读取'
  $('gateList').innerHTML = ''
  $('lineageData').textContent = '{}'
  $('downloadProvenance').disabled = true
  $('enhance1080Button').disabled = true
  $('enhance1080Button').textContent = '生成 1080p 高清版'
  $('dialogueDeliveryButton').classList.add('hidden')
  $('approveButton').textContent = '编导选用'
  drawWaveform([])
}

async function openReview(jobId, compareJobId = '') {
  const requestToken = ++state.reviewRequestToken
  const previousReviewJobId = state.reviewJobId
  state.reviewJobId = jobId; state.compareJobId = compareJobId
  if (jobId !== previousReviewJobId || compareJobId) state.compareSelectionJobId = compareJobId
  renderReviewList()
  const key = manifestCacheKey(jobId, compareJobId)
  const cached = state.reviewManifestCache.get(key)
  const currentUpdatedAt = state.reviews.find(item => item.job?.id === jobId)?.job?.updated_at || ''
  if (cached?.manifest && cached.job_updated_at === currentUpdatedAt && Date.now() - Number(cached.saved_at || 0) < reviewManifestCacheTtlMs) {
    state.reviewManifest = cached.manifest
    renderManifest(cached.manifest)
    return cached.manifest
  }
  if (cached) {
    state.reviewManifestCache.delete(key)
    persistManifestCache()
  }
  clearViewer()
  const manifest = await busy(null, () => api.reviewManifest(jobId, compareJobId))
  if (requestToken !== state.reviewRequestToken || jobId !== state.reviewJobId || compareJobId !== state.compareJobId) return null
  state.reviewManifest = manifest; renderManifest(manifest)
  const reviewIndex = state.reviews.findIndex(item => item.job?.id === jobId)
  if (reviewIndex >= 0 && manifest.primary) {
    state.reviews[reviewIndex] = {
      ...state.reviews[reviewIndex],
      ...(manifest.primary.poster?.download_url ? { poster: manifest.primary.poster } : {}),
      technical_validation: manifest.primary.technical_validation || state.reviews[reviewIndex].technical_validation,
      job: { ...state.reviews[reviewIndex].job, ...(manifest.primary.job || {}) },
    }
    renderReviewList()
    persistReviewList()
  }
  state.reviewManifestCache.set(key, { job_updated_at: manifest.primary?.job?.updated_at || currentUpdatedAt, saved_at: Date.now(), manifest })
  persistManifestCache()
  const images = [manifest.primary?.poster, ...(manifest.primary?.keyframes || []), manifest.compare?.poster, ...(manifest.compare?.keyframes || [])].filter(Boolean)
  Promise.allSettled(images.map(item => cacheReviewImage(item.download_url, reviewImageKey(item)))).catch(() => {})
  return manifest
}

function releaseVideo(video) {
  video.pause()
  video.removeAttribute('src')
  video.removeAttribute('poster')
  video.load()
  video.classList.add('hidden')
}

function setVideo(video, item) {
  const url = item?.asset?.download_url
  if (!url) { releaseVideo(video); return }
  const poster = item.poster?.download_url || ''
  if (video.getAttribute('src') !== url || (video.getAttribute('poster') || '') !== poster) {
    releaseVideo(video)
    video.src = url
    video.poster = poster
    video.load()
  }
  video.classList.remove('hidden')
}

function reviewPlaybackItem(item = {}) {
  const preview = item.preview || item.preview_asset
  if (!preview?.download_url) return item
  return { ...item, asset: preview, original_asset: item.asset }
}

function reviewAudioState(primary = {}) {
  const gate = primary.dialogue_delivery_gate || {}
  const transcription = gate.transcription || {}
  const technical = primary.technical_validation || {}
  const streams = primary.asset?.metadata?.media_probe?.streams || []
  const hasAudio = Boolean(technical.audio_codec)
    || streams.some(stream => stream?.codec_type === 'audio')
    || (gate.required === true && gate.passed === true)
  if (hasAudio) {
    const codec = technical.audio_codec ? ` · ${String(technical.audio_codec).toUpperCase()}` : ''
    const coverage = Number(transcription.coverage)
    if (transcription.passed === true) {
      const score = Number.isFinite(coverage) ? ` · 台词 ${Math.round(coverage * 100)}%` : ' · 台词已核验'
      return {
        label: `含音轨${codec}${score}`,
        tone: 'ready',
        title: `${transcription.model || '受控语音识别'}已核验台词完整性；音色、情绪、停顿和口型仍需人工听审`,
      }
    }
    return {
      label: `含音轨${codec} · 台词待校验`,
      tone: 'ready',
      title: gate.reason || '音轨可播放，但尚未证明台词与原脚本一致',
    }
  }
  if (gate.required) {
    const failed = ['failed', 'configuration_required'].includes(gate.status)
    if (failed) return { label: '无声底片 · 配音失败', tone: 'failed', title: gate.reason || '独立配音失败，可使用右侧按钮重试' }
    if (['voiceover_renderer_required', 'awaiting_visual_gate', 'auto_rendering', 'rendering'].includes(gate.status) && !gate.retryable) {
      return { label: '无声底片 · 自动配音中', tone: 'pending', title: gate.reason || '系统将在画面门禁通过后自动生成并校验最终音轨' }
    }
    return { label: gate.retryable ? '无声底片 · 可生成对白' : '无声底片 · 等待对白', tone: 'pending', title: gate.reason || '当前是无声人物底片，完成独立配音后声音按钮才会启用' }
  }
  return { label: '无音轨', tone: 'silent', title: '当前视频文件没有音频流，因此浏览器会禁用声音按钮' }
}

function dialogueDeliveryButtonLabel(gate = {}) {
  if (gate.status === 'transcription_required') return '校验台词完整性'
  if (['failed', 'configuration_required'].includes(gate.status)) return '重试独立配音成片'
  return '生成独立配音成片'
}

function updateReviewSoundToggle(primary = {}) {
  const button = $('reviewSoundToggle')
  const video = $('reviewVideoA')
  const audioState = reviewAudioState(primary)
  const playable = audioState.tone === 'ready'
  button.disabled = !playable
  if (!playable) {
    button.textContent = audioState.tone === 'loading' ? '检查音轨…' : '无音轨'
    button.setAttribute('aria-pressed', 'false')
    button.title = audioState.title
    return
  }
  const enabled = !video.muted && Number(video.volume) > 0
  button.textContent = enabled ? '声音：开' : '声音：关'
  button.setAttribute('aria-pressed', String(enabled))
  button.title = state.compareJobId
    ? '控制 A 片声音；B 片保持静音，避免 A/B 对比时双声叠加'
    : '打开或关闭当前审片视频声音'
}

function renderDialogueDeliveryState(primary = {}) {
  const dialogueGate = primary.dialogue_delivery_gate || {}
  const audioState = reviewAudioState(primary)
  $('reviewAudioStatus').textContent = audioState.label
  $('reviewAudioStatus').className = `audio-status ${audioState.tone}`
  $('reviewAudioStatus').title = audioState.title
  updateReviewSoundToggle(primary)
  $('dialogueDeliveryButton').classList.toggle('hidden', !dialogueGate.required || dialogueGate.passed || !dialogueGate.retryable)
  $('dialogueDeliveryButton').textContent = dialogueDeliveryButtonLabel(dialogueGate)
  const job = primary.job || {}
  $('lineageData').textContent = JSON.stringify({
    lineage: primary.lineage,
    technical_validation: primary.technical_validation,
    dialogue_delivery_gate: dialogueGate,
    annotations: primary.annotations,
    cloud_sync: state.reviews.find(item => item.job.id === job.id)?.cloud_sync,
    provenance: primary.provenance,
  }, null, 2)
  $('downloadProvenance').disabled = !primary.provenance
}

function downloadProvenance() {
  const primary = state.reviewManifest?.primary || {}
  if (!primary.provenance) return toast('当前成片尚无制作凭证', 'error')
  downloadJson(`${primary.job?.id || 'media'}-provenance.json`, primary.provenance)
}

function downloadJson(fileName, value) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  document.body.append(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

async function downloadContinuationTimeline() {
  const chainId = state.continuation?.id
  if (!chainId) return toast('当前没有自动续时时间线', 'error')
  // Fetch once on explicit user action so the export contains the newest
  // segment assets, hashes and seam gates instead of the submit-time draft.
  const result = await api.continuationGet(chainId)
  state.continuation = result.chain || state.continuation
  const timeline = state.continuation?.timeline_handoff
  if (!timeline) return toast('时间线尚未生成，请稍后重试', 'error')
  downloadJson(`${chainId}-timeline.json`, timeline)
}

function updateReviewPlayToggle() {
  const button = $('reviewPlayToggle')
  const video = $('reviewVideoA')
  const playable = Boolean(video.currentSrc || video.getAttribute('src'))
  const playing = playable && !video.paused && !video.ended
  button.disabled = !playable
  button.textContent = playing ? '❚❚ 暂停' : '▶ 播放'
  button.setAttribute('aria-pressed', String(playing))
  button.title = playable ? (playing ? '暂停当前审片视频' : '播放当前审片视频') : '选择成片后播放'
}

async function toggleReviewPlayback() {
  const primary = $('reviewVideoA')
  const compare = $('reviewVideoB')
  if (!primary.currentSrc && !primary.getAttribute('src')) return
  if (!primary.paused && !primary.ended) {
    primary.pause()
    compare.pause()
    updateReviewPlayToggle()
    return
  }
  if (primary.ended) primary.currentTime = 0
  try {
    await primary.play()
    if (state.compareJobId && (compare.currentSrc || compare.getAttribute('src'))) {
      compare.currentTime = primary.currentTime
      compare.muted = true
      await compare.play()
    }
  } catch (error) {
    toast(`播放器启动失败：${errorText(error)}。可刷新签名地址后重试。`, 'error')
  }
  updateReviewPlayToggle()
}

function renderManifest(manifest) {
  const primary = manifest.primary || {}; const job = primary.job || {}
  const primaryPlayback = reviewPlaybackItem(primary)
  const comparePlayback = reviewPlaybackItem(manifest.compare || {})
  $('viewerShell').classList.remove('loading')
  $('noVideo').classList.toggle('hidden', Boolean(primaryPlayback.asset?.download_url))
  setVideo($('reviewVideoA'), primaryPlayback); setVideo($('reviewVideoB'), comparePlayback)
  updateReviewPlayToggle()
  $('viewerShell').classList.toggle('comparing', Boolean(manifest.compare))
  $('reviewJobTitle').textContent = job.business_title || state.reviews.find(item => item.job.id === job.id)?.business_title || job.id
  $('reviewJobMeta').textContent = `${reviewVersionLabel(job)} · ${modeNames[job.mode] || job.mode} · ${job.params?.width || '—'} × ${job.params?.height || '—'} · ${job.model_version || '模型版本待上报'}`
  renderDialogueDeliveryState(primary)
  $('enhance1080Button').disabled = !primary.asset?.download_url || job.mode === 'video_enhance'
  $('enhance1080Button').textContent = job.mode === 'video_enhance' ? '当前已是 1080p 高清版' : '生成 1080p 高清版'
  $('filmstrip').innerHTML = (primary.keyframes || []).map(frame => `<button data-seek="${Number(frame.time_seconds || 0)}"><img loading="lazy" src="${esc(frame.download_url)}" alt="${frame.time_seconds}s"></button>`).join('')
  $$('[data-seek]').forEach(button => button.onclick = () => { $('reviewVideoA').currentTime = Number(button.dataset.seek); if (manifest.compare) $('reviewVideoB').currentTime = Number(button.dataset.seek) })
  drawWaveform(primary.waveform || []); renderQuality(primary); renderSources(primary); renderGates(primary)
}

function drawWaveform(points) {
  const canvas = $('waveform'); const context = canvas.getContext('2d'); const width = canvas.width; const height = canvas.height
  context.clearRect(0, 0, width, height); context.strokeStyle = '#7ca9ff'; context.lineWidth = 2; context.beginPath()
  if (!points.length) { context.strokeStyle = '#28313c'; context.moveTo(0, height / 2); context.lineTo(width, height / 2); context.stroke(); return }
  points.forEach((value, index) => { const x = index / Math.max(1, points.length - 1) * width; const y = height / 2 - Number(value) * height * .43; index ? context.lineTo(x, y) : context.moveTo(x, y) }); context.stroke()
}

function qualityForDisplay(primary) {
  const aggregate = primary.quality || {}
  const analyzedAsset = (aggregate.assets || []).find(item => item?.status === 'completed' && item?.analysis)?.analysis || {}
  const recommendation = aggregate.recommendation || analyzedAsset.recommendation || {}
  return {
    ...analyzedAsset,
    ...aggregate,
    scores: aggregate.scores || analyzedAsset.scores || {},
    applicable_overall_score: aggregate.applicable_overall_score || analyzedAsset.applicable_overall_score || aggregate.overall_score || analyzedAsset.overall_score || 0,
    dimension_applicability: aggregate.dimension_applicability || analyzedAsset.dimension_applicability || {},
    dimension_applicability_reasons: aggregate.dimension_applicability_reasons || analyzedAsset.dimension_applicability_reasons || {},
    material_stage: aggregate.material_stage || analyzedAsset.material_stage || {},
    issues: [...new Set([...(analyzedAsset.issues || []), ...(aggregate.issues || [])])],
    repair_plan: aggregate.repair_plan || analyzedAsset.repair_plan || {},
    repair_advice: aggregate.repair_advice || analyzedAsset.repair_advice || recommendation.reason || '',
  }
}

function mergeSelectedQualityFromJob(job = {}) {
  if (!state.reviewManifest?.primary || job.id !== state.reviewJobId) return
  const quality = job.result?.quality_analysis
  if (!quality || typeof quality !== 'object') return
  state.reviewManifest.primary.quality = quality
  state.reviewManifest.primary.job = { ...state.reviewManifest.primary.job, ...job }
  const primary = state.reviewManifest.primary
  const dialogueGate = primary.dialogue_delivery_gate || {}
  const visualGate = qualityForDisplay(primary).visual_policy_gate || {}
  if (dialogueGate.required && !dialogueGate.passed && visualGate.passed === true) {
    const awaitingTranscription = dialogueGate.status === 'transcription_required'
    primary.dialogue_delivery_gate = {
      ...dialogueGate,
      visual_policy_gate: visualGate,
      retryable: true,
      status: awaitingTranscription
        ? 'transcription_required'
        : job.result?.dialogue_delivery?.status || dialogueGate.status || 'awaiting_visual_gate',
      reason: awaitingTranscription
        ? dialogueGate.reason
        : '画面硬门禁已通过；可以生成受控独立配音成片，完成音频流验证后再审核。',
    }
  }
  renderQuality(state.reviewManifest.primary)
  // Quality finishes asynchronously after the review manifest is opened.
  // Keep the immutable visual gate in lockstep with the newly delivered
  // quality payload; otherwise the scores update while the gate stays stuck
  // on "detecting" until the reviewer leaves and reopens the item.
  renderGates(state.reviewManifest.primary)
  renderDialogueDeliveryState(state.reviewManifest.primary)
}

function qualityIssueText(item) {
  if (typeof item === 'string') return item
  if (!item || typeof item !== 'object') return String(item || '')
  const message = item.issue || item.message || item.summary || item.description || item.observation || ''
  const details = [message]
  if (item.evidence && !String(message).includes(String(item.evidence))) details.push(`证据：${item.evidence}`)
  if (item.impact && !String(message).includes(String(item.impact))) details.push(`影响：${item.impact}`)
  return details.filter(Boolean).join('；')
}

function renderQuality(primary) {
  const quality = qualityForDisplay(primary); const scores = quality.scores || {}
  const materialStage = materialStageFor(primary.job || {}, quality)
  $('materialStageNotice').classList.remove('hidden')
  $('materialStageNotice').innerHTML = `<strong>${esc(materialStage.label)}</strong><span>${esc(materialStage.description || '')}</span>${materialStage.requires_assembly ? '<small>通过只代表当前素材质检通过，不代表完整投流成片。</small>' : ''}`
  $('approveButton').textContent = '编导选用'
  const dialogueTranscription = primary.dialogue_delivery_gate?.transcription || {}
  const audioReviewCopy = dialogueTranscription.passed === true
    ? '台词已自动对齐；音色、情绪、停顿和口型仍需人工听审。'
    : '音频仍需人工听审。'
  const runtime = quality.runtime || {}
  const model = quality.model || runtime.model || ''
  const provider = quality.provider || runtime.provider || ''
  const projectFineTuned = quality.project_fine_tuned === true || runtime.project_fine_tuned === true
  const deployment = quality.deployment_id || runtime.deployment_id || ''
  const artifact = quality.artifact_id || runtime.artifact_id || ''
  const modelKind = projectFineTuned
    ? `项目微调${deployment ? ` · ${deployment}` : ''}${artifact ? ` · ${artifact}` : ''}`
    : '平台基础视觉模型 · 非项目微调'
  const statusLabel = { queued: '已进入质检队列', running: '正在读取关键帧并分析', failed: '质检失败', completed: '质检完成', partial: '部分质检完成' }[quality.status] || ''
  $('qualityRuntime').innerHTML = model
    ? `<strong title="${esc(model)}">${esc(model)}</strong><span>${esc(provider || '服务来源未记录')} · ${esc(modelKind)}</span>`
    : `<span>${esc(statusLabel || '质检模型尚未上报；重新分析后将记录模型与服务来源')}</span>`
  const applicability = quality.dimension_applicability || {}
  const applicabilityReasons = quality.dimension_applicability_reasons || {}
  const rows = Object.entries(qualityNames).filter(([key]) => applicability[key] === false || Number(scores[key]) > 0)
  const emptyMessage = quality.status === 'failed'
    ? `视觉质检失败：${quality.error || '服务未返回错误详情'}。可重新分析；${audioReviewCopy}`
    : quality.status === 'queued'
    ? `视觉质检已进入后台队列，完成后由实时事件自动回写；${audioReviewCopy}`
    : quality.status === 'running'
    ? '正在提取关键帧并调用视觉模型；完成后由实时事件自动回写。'
    : ['completed', 'partial'].includes(quality.status)
    ? `视觉质检已完成，但模型未返回完整九维分数；${audioReviewCopy}`
    : `尚未执行 AI 视觉质检；${audioReviewCopy}`
  $('qualitySummary').innerHTML = rows.length ? rows.map(([key, name]) => {
    if (applicability[key] === false) {
      const reason = applicabilityReasons[key] || '当前镜头不评价此维度'
      return `<div class="quality-row na" title="${esc(reason)}"><span>${name}</span><span class="quality-bar na"><i></i></span><b>不适用</b></div>`
    }
    const score = Number(scores[key])
    return `<div class="quality-row"><span>${name}</span><span class="quality-bar"><i style="width:${Math.min(100, score <= 5 ? score * 20 : score)}%"></i></span><b>${score}</b></div>`
  }).join('') : `<p class="empty-copy">${emptyMessage}</p>`
  const dialogueGate = primary.dialogue_delivery_gate || {}
  const dialogueIssues = dialogueGate.required && !dialogueGate.passed ? [dialogueGate.reason || '对白尚未完成独立配音交付。'] : []
  const openAnnotations = (primary.annotations || [])
    .filter(item => item.status === 'open')
    .map(item => `人工未解决${item.severity === 'critical' ? '严重问题' : '问题'}${item.start_seconds != null ? `（${item.start_seconds}${item.end_seconds != null ? `–${item.end_seconds}` : ''} 秒）` : ''}：${item.note || '未填写说明'}`)
  const issues = [...new Set([...dialogueIssues, ...openAnnotations, ...(quality.issues || []).map(qualityIssueText)].filter(Boolean))]
  $('criticalIssues').innerHTML = issues.length ? issues.map(item => `<div class="issue">${esc(item)}</div>`).join('') : '<p class="empty-copy">未发现已记录的问题</p>'
  $('repairAdvice').textContent = quality.repair_advice || quality.summary || quality.notes || '请重点听审音频、检查商品真实性、首 5 秒钩子和价格活动依据。'
}

function renderSources(primary) {
  $('sourceStrip').innerHTML = (primary.sources || []).map(item => {
    const url = item.asset?.download_url
    return url && item.asset?.mime_type?.startsWith('image/') ? `<img src="${esc(url)}" title="${esc(item.purpose || item.role)}" alt="">` : `<span class="source-placeholder" title="${esc(item.purpose || item.role)}">${item.asset?.mime_type?.startsWith('video/') ? '▶' : '♫'}</span>`
  }).join('') || '<p class="empty-copy">无指定素材</p>'
}

function renderGates(primary) {
  const rights = primary.job?.rights || {}; const technical = primary.technical_validation || {}
  const passedTechnical = technical.status === 'passed' || technical.passed === true
  const dialogueGate = primary.dialogue_delivery_gate || { required: false, passed: true, label: '无需对白交付' }
  const quality = qualityForDisplay(primary)
  const profile = primary.job?.prompt?.h3_execution_profile || ''
  const textGateRequired = ['clean_dialogue_plate_silent_v1', 'clean_people_plate_silent_v4', 'direct_clean_people_plate_silent_v1', 'motion_only_no_text_ascii_v3', 'product_replacement_no_text_ascii_v1'].includes(profile)
    || primary.job?.prompt?.brand_guardrails?.reference_text_policy === 'suppress_all_source_text'
  const visualGate = quality.visual_policy_gate || { required: textGateRequired, passed: false, status: 'pending' }
  const gates = [
    ['technical', '技术检查', passedTechnical, true], ['copyright_authorized', '版权授权', rights.copyright_authorized, false],
    ['malware_scan', '恶意文件扫描', rights.malware_scan === 'passed', false], ['sensitive_data_scan', '敏感信息扫描', rights.sensitive_data_scan === 'passed', false],
    ['redaction', '脱敏检查', rights.redaction === 'passed', false], ['adult_audience_confirmed', '成人受众声明', true, false],
    ['commercial_claim_verified', '价格/活动真实性', false, false],
  ]
  if (dialogueGate.required) gates.splice(1, 0, ['dialogue_delivery', dialogueGate.label || '对白交付', dialogueGate.passed === true, true])
  if (visualGate.required || textGateRequired) {
    const findingCodes = (visualGate.findings || []).map(item => item.code)
    const failedLabel = findingCodes.includes('interpersonal_contact_detected')
      ? '检测到人物接触，禁止通过'
      : '检测到烧字，禁止通过'
    gates.splice(1, 0, ['visual_policy_gate', visualGate.passed ? '画面硬门禁通过' : visualGate.status === 'failed' ? failedLabel : '画面硬门禁检测中', visualGate.passed === true, true])
  }
  $('gateList').innerHTML = gates.map(([key, label, checked, disabled]) => `<label class="gate-item"><input type="checkbox" data-gate="${key}" ${checked ? 'checked' : ''} ${disabled ? 'disabled' : ''}><span><strong>${label}</strong><small>${disabled ? '由系统检测，不可人工覆盖' : '审核人确认并留痕'}</small></span></label>`).join('')
}

function gateChecked(key) { return Boolean(document.querySelector(`[data-gate="${key}"]`)?.checked) }
function reviewPayload(decision, reason = '') {
  const primary = state.reviewManifest?.primary || {}; const job = primary.job || {}; const containsVoice = job.rights?.contains_voice; const containsPerson = job.rights?.contains_person
  const quality = qualityForDisplay(primary)
  return {
    job_id: state.reviewJobId, decision, reason, category: '示例品牌内容电商运营部/AI生成素材',
    rights: { ...job.rights, copyright_authorized: gateChecked('copyright_authorized'), malware_scan: gateChecked('malware_scan') ? 'passed' : 'pending', sensitive_data_scan: gateChecked('sensitive_data_scan') ? 'passed' : 'pending', redaction: gateChecked('redaction') ? 'passed' : 'pending', portrait_authorized: !containsPerson || gateChecked('copyright_authorized'), voice_authorized: !containsVoice || gateChecked('copyright_authorized') },
    compliance: { adult_audience_confirmed: gateChecked('adult_audience_confirmed'), commercial_claim_verified: gateChecked('commercial_claim_verified') },
    quality_evaluation: { material_stage: materialStageFor(job, quality) },
  }
}

async function approveReview() {
  if (!state.activeCandidateId) return toast('请先选择候选素材', 'error')
  const usage = await requestText({
    title: '选用这条素材',
    description: '选用表示编导认可素材可进入后续剪辑；不会自动入库或伪报已投放。',
    placeholder: '可填写使用方向或剪辑说明',
    defaultValue: '用于后续剪辑',
    confirmLabel: '确认选用',
  })
  const result = await busy($('approveButton'), () => api.materialDecisionSubmit({
    candidate_id: state.activeCandidateId,
    decision: 'selected',
    usage_direction: usage || '',
    edit_notes: usage || '',
  }), '已选用；需要时可单独同步到部门素材库')
  if (state.reviewSession) state.reviewSession.candidate = result.candidate
  $('deliveryButton').classList.remove('hidden')
  await loadReviews()
}

async function holdReview() {
  if (!state.activeCandidateId) return toast('请先选择候选素材', 'error')
  const note = await requestText({ title: '暂缓这条素材', description: '暂缓不会进入素材库，也不会被计入已选用。', placeholder: '可填写暂缓原因', defaultValue: '等待补充业务判断', confirmLabel: '确认暂缓' })
  await busy($('holdButton'), () => api.materialDecisionSubmit({
    candidate_id: state.activeCandidateId, decision: 'held', edit_notes: note || '',
  }), '已暂缓')
  await loadReviews()
  showMaterialWall()
}

async function deliverSelectedCandidate() {
  if (!state.activeCandidateId) return toast('请先选择已选用素材', 'error')
  const result = await busy($('deliveryButton'), () => api.materialDeliverySubmit({
    candidate_id: state.activeCandidateId,
    team: '示例品牌内容电商运营部',
    category: '示例品牌内容电商运营部/AI生成素材',
  }), '已创建受控素材库同步任务')
  $('deliveryButton').textContent = result.delivery?.status === 'delivered' ? '已入库' : '入库处理中'
  $('deliveryButton').disabled = true
}

async function retryDialogueDelivery() {
  if (!state.reviewJobId) return toast('请先选择视频', 'error')
  const gate = state.reviewManifest?.primary?.dialogue_delivery_gate || {}
  const result = await busy(
    $('dialogueDeliveryButton'),
    () => api.retryDialogueDelivery(state.reviewJobId),
    gate.status === 'transcription_required'
      ? '台词完整性校验已完成，正在重新加载审片结果'
      : '独立配音和最终混音已完成，正在重新加载审片成片',
  )
  invalidateManifestCache(state.reviewJobId)
  if (result.job) mergeReviewJob(result.job)
  await openReview(state.reviewJobId)
  const authoritativeGate = result.dialogue_delivery_gate || {}
  if (authoritativeGate.required && state.reviewManifest?.primary?.job?.id === state.reviewJobId) {
    state.reviewManifest.primary.dialogue_delivery_gate = authoritativeGate
    renderDialogueDeliveryState(state.reviewManifest.primary)
    const key = manifestCacheKey(state.reviewJobId, state.compareJobId)
    state.reviewManifestCache.set(key, {
      job_updated_at: state.reviewManifest.primary.job?.updated_at || result.job?.updated_at || '',
      saved_at: Date.now(),
      manifest: state.reviewManifest,
    })
    persistManifestCache()
  }
}

async function enhanceCurrent1080p() {
  if (!state.reviewJobId) return toast('请先选择需要高清化的视频', 'error')
  const result = await busy(
    $('enhance1080Button'),
    () => api.enhance1080p(state.reviewJobId),
    '1080p 高清化任务已加入生产队列；完成后会自动进入审片中心',
  )
  if (result.job) {
    mergeJobs([result.job]); renderJobs()
  }
}

function returnReviewToProduction(reason) {
  const primary = state.reviewManifest?.primary || {}
  const job = primary.job || {}
  const prompt = job.prompt || {}
  const originalBrief = prompt.production_brief || {}
  const engineRoleToBusinessRole = {
    first_frame: 'character_first_frame',
    reference_image: 'visual_reference',
    reference_video: 'motion_reference',
    reference_audio: 'audio_reference',
  }
  state.assets = (primary.sources || []).flatMap(source => {
    const asset = source.asset
    const role = source.business_role || engineRoleToBusinessRole[source.role] || source.role
    if (!asset?.id || !roleNames[role]) return []
    return [{
      asset,
      file: { name: asset.file_name || asset.name || asset.id, type: asset.mime_type || '' },
      role,
      roleLocked: true,
    }]
  })
  const titleProduct = String(job.business_title || '').split(' · ')[0].trim()
  if (originalBrief.product || titleProduct) $('product').value = originalBrief.product || titleProduct
  // Shot sound descriptions are production metadata, not operator dialogue.
  // Reusing "environment sound fades in" as a script changes a silent product
  // brief into a spoken task on the next preparation pass.
  $('script').value = String(originalBrief.script || '')
  const isDirectSource = String(job.creative_option || '') === 'direct_prompt'
  const originalGoal = isDirectSource ? '' : String(prompt.creative_goal || prompt.video_prompt || '').trim()
  const repairPlan = qualityForDisplay(primary).repair_plan || {}
  const repairConstraints = [...new Set(repairPlan.constraints || [])]
  $('requirement').value = [
    `驳回修改要求：${reason}`,
    ...repairConstraints.map(item => `系统质检修复约束：${item}`),
    originalBrief.request ? `原始生成要求：${originalBrief.request}` : '',
    originalGoal ? `原创意目标：${originalGoal}` : '',
  ].filter(Boolean).join('\n')
  const creativeOption = String(job.creative_option || '')
  if ([...$('creativeOption').options].some(option => option.value === creativeOption)) $('creativeOption').value = creativeOption
  if (creativeOption === 'direct_prompt') {
    $('directH3Prompt').value = String(prompt.operator_direct_h3_prompt || prompt.direct_h3_prompt || '').trim()
  }
  const fps = Number(job.params?.fps || 24)
  const duration = Number(job.params?.delivery_duration_seconds || job.params?.target_duration_seconds)
    || Math.round(Number(job.params?.frames || 0) / fps)
  if ([3, ...Array.from({ length: 11 }, (_, index) => index + 5), 30].includes(duration)) $('duration').value = String(duration)
  if (['9:16', '16:9'].includes(originalBrief.ratio)) $('ratio').value = originalBrief.ratio
  $('quantity').value = '1'
  $('seed').value = '-1'
  $('audioEnabled').checked = job.prompt?.dialogue_delivery?.requested === true || job.params?.audio_enabled !== false
  const productionIntent = String(originalBrief.production_intent || job.production_intent || '')
  if (['product_packshot', 'abstract_broll'].includes(productionIntent)) $('containsPerson').checked = false
  else if (['people_dialogue', 'people_lifestyle'].includes(productionIntent)) $('containsPerson').checked = true
  else $('containsPerson').checked = Boolean(job.rights?.contains_person)
  $('containsVoice').checked = Boolean(job.rights?.contains_voice)
  if (job.output_preset_id) state.selectedPreset = job.output_preset_id
  state.prepared = null
  state.submitKey = ''
  state.autoProduct = ''
  $('planResult').classList.add('hidden')
  $('prepareError').classList.add('hidden')
  renderAssets()
  renderPresets(state.snapshot.output_presets || { items: [] })
  invalidatePlan()
  switchWorkspace('production')
  $('productionWorkspace').scrollIntoView({ behavior: 'smooth', block: 'start' })
}

async function rejectReview() {
  if (!state.activeCandidateId) return toast('请先选择候选素材', 'error')
  const quality = qualityForDisplay(state.reviewManifest?.primary || {})
  const repairDefaults = quality.repair_plan?.constraints || []
  const reason = await requestText({
    title: '驳回并返回生产',
    description: '请写清问题与修改方向，系统会把当前素材、分镜和标注一并带回生产。',
    defaultValue: $('annotationNote').value.trim() || repairDefaults.join('；') || quality.repair_advice || '',
    placeholder: '例如：商品包装在 2.2 秒后变形，请保持外形、尺寸和位置不变。',
    confirmLabel: '确认驳回',
  })
  if (!reason) return
  await busy($('rejectButton'), async () => {
    await api.materialDecisionSubmit({
      candidate_id: state.activeCandidateId,
      decision: 'rejected',
      problem_types: [$('annotationCategory').value || 'other'],
      edit_notes: reason,
    })
    returnReviewToProduction(reason)
  }, '已驳回；素材、分镜和修改要求已返回生产，请确认后再生成')
  await loadReviews()
}

function mergeReviewJob(job) {
  const item = state.reviews.find(row => row.job.id === job?.id)
  if (item) item.job = { ...item.job, ...job }
}

async function saveAnnotation() {
  if (!state.activeCandidateId) return toast('请先选择候选素材', 'error')
  const note = $('annotationNote').value.trim(); if (!note) return toast('请填写问题和修改建议', 'error')
  const result = await busy($('saveAnnotation'), () => api.materialAnnotationSave({ candidate_id: state.activeCandidateId, category: $('annotationCategory').value, severity: $('annotationSeverity').value, start_seconds: $('reviewVideoA').currentTime || 0, end_seconds: $('annotationEnd').value ? Number($('annotationEnd').value) : undefined, note }), '问题标注已保存')
  state.reviewManifest.primary.annotations = [...(state.reviewManifest.primary.annotations || []), result.annotation]
  invalidateManifestCache(state.reviewJobId)
  $('annotationNote').value = ''; renderQuality(state.reviewManifest.primary)
}

async function batchReject() {
  const ids = [...state.selectedReviewIds]; if (!ids.length) return toast('请先勾选视频', 'error')
  const reason = await requestText({
    title: `批量驳回 ${ids.length} 条素材`,
    description: '系统不提供批量通过。该原因会写入每条审片记录。',
    placeholder: '填写共同问题与明确的重做要求',
    confirmLabel: '确认批量驳回',
  }); if (!reason) return
  await busy($('batchReject'), () => api.materialBatchReject({ candidate_ids: ids, problem_types: ['batch_reject'], edit_notes: reason }), `已驳回 ${ids.length} 条`)
  state.selectedReviewIds.clear(); await loadReviews()
}

async function batchHold() {
  const ids = [...state.selectedReviewIds]; if (!ids.length) return toast('请先勾选候选素材', 'error')
  const note = await requestText({ title: `批量暂缓 ${ids.length} 条素材`, placeholder: '可填写共同原因', confirmLabel: '确认暂缓' })
  await busy($('batchHold'), () => api.materialBatchHold({ candidate_ids: ids, edit_notes: note || '' }), `已暂缓 ${ids.length} 条`)
  state.selectedReviewIds.clear(); await loadReviews()
}

async function batchAssign() {
  const ids = [...state.selectedReviewIds]; if (!ids.length) return toast('请先勾选视频', 'error')
  const raw = await requestText({
    title: `为 ${ids.length} 条素材添加标签`,
    description: '多个标签请用逗号分隔。',
    defaultValue: '待优化',
    placeholder: '待优化, 商品保真',
    confirmLabel: '保存标签',
    multiline: false,
  })
  if (!raw) return
  await busy($('batchAssign'), () => api.materialCandidateBatchTag({ candidate_ids: ids, tags: raw.split(',').map(item => item.trim()).filter(Boolean) }), '标签已更新')
}

async function searchCloud() {
  const result = await busy($('cloudSearchButton'), () => api.cloudReferenceSearch({ search: $('cloudSearch').value.trim(), limit: 24 }))
  $('cloudNotice').textContent = `${result.notice || ''}${result.as_of ? ` 数据时间：${isoLocal(result.as_of)}` : ''}`
  $('cloudResults').innerHTML = (result.items || []).map(item => {
    const metricBits = []
    if (Number.isFinite(Number(item.metrics?.roi))) metricBits.push(`ROI ${Number(item.metrics.roi).toFixed(2)}`)
    if (Number.isFinite(Number(item.metrics?.stat_cost))) metricBits.push(`消耗 ¥${Number(item.metrics.stat_cost).toFixed(0)}`)
    if (Number.isFinite(Number(item.metrics?.views)) && Number(item.metrics.views) > 0) metricBits.push(`播放 ${Number(item.metrics.views).toLocaleString('zh-CN')}`)
    const meta = [item.category_name, item.state_label, item.duration_seconds ? `${item.duration_seconds} 秒` : '时长待平台解析', ...metricBits].filter(Boolean)
    const action = item.selectable
      ? `<button type="button" class="cloud-import" data-cloud-import="${esc(item.video_id)}">导入并使用</button>`
      : `<small>${item.import_status === 'media_unavailable' ? '源文件当前不可播放' : '仅查看元数据'}</small>`
    return `<article class="cloud-card"><strong>${esc(item.title || item.video_name || item.name || item.video_id || '未命名云视频')}</strong><span>${meta.map(esc).join(' · ')}</span><footer>${action}</footer></article>`
  }).join('') || '<p class="empty-copy">没有匹配结果</p>'
  $$('[data-cloud-import]').forEach(button => button.onclick = () => importCloudReference(button.dataset.cloudImport, button).catch(() => {}))
}

async function importCloudReference(videoId, button) {
  const result = await busy(button, () => api.cloudReferenceImport({ video_id: videoId, role: 'motion_reference' }))
  const asset = result.asset || {}
  if (!asset.id) throw new Error('平台未返回导入后的素材')
  const existing = state.assets.find(item => item.asset?.id === asset.id)
  if (!existing) {
    state.assets.push({
      file: { name: asset.file_name || `cloud-video-${videoId}.mp4`, type: asset.mime_type || 'video/mp4' },
      asset,
      role: result.source_role?.role || 'motion_reference',
    })
  }
  applySafeDefaults(); invalidatePlan(); renderAssets()
  button.disabled = true; button.textContent = existing ? '已在需求中' : '已导入需求'
  toast(existing ? '该云视频已在当前需求中' : '云视频已安全导入并设为原视频 / 动作参考', 'success')
}

function saveDraft() {
  const draft = {
    productionMode: state.productionMode,
    product: $('product').value,
    productParticipation: $('productParticipation').value,
    platform: $('platform').value,
    platformParticipation: $('platformParticipation').value,
    sellingPoints: $('sellingPoints').value,
    sellingPointsParticipation: $('sellingPointsParticipation').value,
    promotion: $('promotion').value,
    promotionParticipation: $('promotionParticipation').value,
    script: $('script').value,
    requirement: $('requirement').value,
    creativeOption: $('creativeOption').value,
    directH3Prompt: $('directH3Prompt').value,
    duration: $('duration').value,
    ratio: $('ratio').value,
    quantity: $('quantity').value,
    preset: state.selectedPreset,
    theme: $('themeEnabled').checked,
    directionCount: $('directionCount').value,
    variants: $('variantsPerDirection').value,
    stripReferenceText: $('stripReferenceText')?.checked !== false,
    referenceIdentityPolicy: $('referenceIdentityPolicy')?.value || 'replace_actor',
    commercialAppearance: $('commercialAppearance').checked,
    lockedRecommendedAssetIds: [...state.lockedRecommendedAssetIds],
    promptOptimization: state.promptOptimization,
    assets: state.assets.filter(item => item.asset?.id).map(item => ({
      file: {
        name: assetDisplayName(item),
        type: item.file?.type || item.asset?.mime_type || '',
        size: Number(item.file?.size || item.asset?.size_bytes || 0),
        lastModified: Number(item.file?.lastModified || 0),
      },
      asset: Object.fromEntries([
        'id', 'file_name', 'mime_type', 'download_url', 'sha256', 'size_bytes',
        'duration_seconds', 'width', 'height',
      ].filter(key => item.asset?.[key] != null).map(key => [key, item.asset[key]])),
      localFingerprint: item.localFingerprint || '',
      role: item.role,
      roleLocked: item.roleLocked === true,
      libraryAssetId: item.libraryAssetId || '',
    })),
  }
  localStorage.setItem(draftStorageKey, JSON.stringify(draft)); $('draftState').textContent = '已自动保存'
}

function restoreDraft() {
  try {
    const draft = JSON.parse(localStorage.getItem(draftStorageKey) || '{}')
    for (const [id, key] of [
      ['product', 'product'], ['productParticipation', 'productParticipation'],
      ['platform', 'platform'], ['platformParticipation', 'platformParticipation'],
      ['sellingPoints', 'sellingPoints'], ['sellingPointsParticipation', 'sellingPointsParticipation'],
      ['promotion', 'promotion'], ['promotionParticipation', 'promotionParticipation'],
      ['script', 'script'], ['requirement', 'requirement'], ['creativeOption', 'creativeOption'],
      ['directH3Prompt', 'directH3Prompt'], ['duration', 'duration'], ['ratio', 'ratio'],
      ['quantity', 'quantity'], ['directionCount', 'directionCount'], ['variantsPerDirection', 'variants'],
    ]) if (draft[key] != null) $(id).value = draft[key]
    if (draft.preset) state.selectedPreset = draft.preset
    $('themeEnabled').checked = Boolean(draft.theme); $('themeSettings').classList.toggle('hidden', !draft.theme)
    $('stripReferenceText').checked = draft.stripReferenceText !== false
    $('referenceIdentityPolicy').value = draft.referenceIdentityPolicy || 'replace_actor'
    $('commercialAppearance').checked = draft.commercialAppearance !== false
    state.lockedRecommendedAssetIds = new Set(Array.isArray(draft.lockedRecommendedAssetIds) ? draft.lockedRecommendedAssetIds : [])
    if (['direct', 'strategy', 'replay'].includes(draft.productionMode)) state.productionMode = draft.productionMode
    if (Array.isArray(draft.assets)) {
      state.assets = dedupeSelectedAssets(draft.assets.filter(item => (
        item?.asset?.id && roleNames[item.role]
      )).map(item => ({
        ...item,
        file: item.file || {
          name: item.asset.file_name || '素材',
          type: item.asset.mime_type || '',
          size: Number(item.asset.size_bytes || 0),
          lastModified: 0,
        },
        roleLocked: item.roleLocked === true,
      })))
    }
    if (draft.promptOptimization && typeof draft.promptOptimization === 'object') {
      state.promptOptimization = draft.promptOptimization
    }
  } catch { /* discard corrupt local draft */ }
}

function resetReviewViewportScroll() {
  const scrollingElement = document.scrollingElement || document.documentElement
  scrollingElement.scrollTop = 0
  document.body.scrollTop = 0
  window.scrollTo(0, 0)
}

function switchWorkspace(name) {
  setQueueOpen(false)
  if (!['production', 'review', 'agent'].includes(name)) return
  const reviewMode = name === 'review'
  const wasReviewMode = document.documentElement.classList.contains('review-mode')
  if (reviewMode && !wasReviewMode) state.productionScrollY = Math.max(0, window.scrollY || document.scrollingElement?.scrollTop || 0)
  $$('[data-workspace]').forEach(button => button.classList.toggle('active', button.dataset.workspace === name))
  $('productionWorkspace').classList.toggle('hidden', name !== 'production')
  $('reviewWorkspace').classList.toggle('hidden', name !== 'review')
  $('agentWorkspace').classList.toggle('hidden', name !== 'agent')
  document.documentElement.classList.toggle('review-mode', reviewMode)
  document.body.classList.toggle('review-mode', reviewMode)
  if (reviewMode) {
    // The production form is much taller than the viewport.  Browsers retain
    // that document scroll offset when the review workspace replaces it.  The
    // review inbox itself is also tall, so the stale offset remains valid and
    // shifts the entire three-pane review UI above the viewport.  Reset both
    // immediately and after layout so async review rendering cannot restore it.
    resetReviewViewportScroll()
    requestAnimationFrame(resetReviewViewportScroll)
    if (!state.activeCandidateId) showMaterialWall()
  } else if (wasReviewMode) {
    const productionScrollY = state.productionScrollY
    requestAnimationFrame(() => {
      window.scrollTo(0, productionScrollY)
      requestAnimationFrame(() => window.scrollTo(0, productionScrollY))
    })
  }
  if (name === 'review' && !state.reviewListLoaded && state.connected && !state.quotaExhausted) {
    loadReviews().catch(error => { if (!isCapabilityQuotaError(error)) toast(errorText(error), 'error') })
  }
  if (name === 'agent' && !state.agentsLoaded && state.connected && !state.quotaExhausted) {
    loadAgents().catch(error => { if (!isCapabilityQuotaError(error)) toast(errorText(error), 'error') })
  }
}

function setQueueOpen(open) {
  const queue = $('globalQueue')
  queue.classList.toggle('collapsed', !open)
  queue.setAttribute('aria-modal', open ? 'true' : 'false')
  $('queueToggle').setAttribute('aria-expanded', open ? 'true' : 'false')
  document.body.classList.toggle('queue-open', open)
  document.querySelector('.submit-bar')?.classList.toggle('queue-obscured', open)
}

function setConnection(status, message) { $('connectionDot').className = `status-dot ${status}`; $('connectionText').textContent = message }
function clearReconnectTimer() {
  if (state.reconnectTimer !== null) clearTimeout(state.reconnectTimer)
  state.reconnectTimer = null
}

function stopEventStream({ resetCursor = false } = {}) {
  state.eventGeneration += 1
  state.unsubscribe?.()
  state.unsubscribe = null
  state.eventConnecting = false
  clearReconnectTimer()
  if (resetCursor) state.eventCursor = ''
}

async function resyncEventStream() {
  stopEventStream({ resetCursor: true })
  renderSnapshot(await api.snapshot())
  await connectEvents()
}

function scheduleReconnect(generation = state.eventGeneration) {
  if (generation !== state.eventGeneration || !state.connected || document.hidden) return
  const nextGeneration = generation + 1
  state.eventGeneration = nextGeneration
  state.unsubscribe?.(); state.unsubscribe = null
  state.eventConnecting = false
  clearReconnectTimer()
  const delay = reconnectDelays[Math.min(state.reconnectAttempt, reconnectDelays.length - 1)]
  state.reconnectAttempt += 1; setConnection('', `事件流重连中 · ${delay / 1000}s`)
  state.reconnectTimer = setTimeout(() => {
    state.reconnectTimer = null
    connectEvents(nextGeneration)
  }, delay)
}

async function connectEvents(generation = state.eventGeneration) {
  if (generation !== state.eventGeneration || !state.connected || document.hidden || state.unsubscribe || state.eventConnecting) return
  state.eventConnecting = true
  clearReconnectTimer()
  try {
    const unsubscribe = await subscribeMedia((eventName, payload) => {
      if (generation !== state.eventGeneration) return
      if (eventName === 'error') return scheduleReconnect(generation)
      if (eventName === 'heartbeat') {
        state.eventCursor = String(payload?.cursor || state.eventCursor)
        setConnection('online', '实时事件已连接')
        return
      }
      const materialEvents = new Set(['request.updated', 'candidate.updated', 'decision.updated', 'delivery.updated', 'performance.updated', 'node.updated'])
      if (eventName !== 'snapshot' && !materialEvents.has(eventName)) return
      const cursor = String(payload?.cursor || '')
      if (cursor && state.eventCursor && Number(cursor) <= Number(state.eventCursor)) return
      const snapshot = payload?.data || payload
      if (snapshot?.nodes || snapshot?.job_counts || snapshot?.status_counts || Array.isArray(snapshot?.jobs)) renderSnapshot(snapshot)
      if (materialEvents.has(eventName) && !$('reviewWorkspace').classList.contains('hidden')) {
        window.clearTimeout(state.candidateRefreshTimer)
        state.candidateRefreshTimer = window.setTimeout(() => loadReviews().catch(() => {}), 350)
      }
      state.eventCursor = cursor || state.eventCursor; state.reconnectAttempt = 0; setConnection('online', '实时事件已连接')
    }, state.eventCursor)
    if (generation !== state.eventGeneration || document.hidden || !state.connected) {
      unsubscribe?.()
      return
    }
    state.unsubscribe = unsubscribe
    state.reconnectAttempt = 0; setConnection('online', '实时事件已连接')
  } catch { scheduleReconnect(generation) }
  finally {
    if (generation === state.eventGeneration) state.eventConnecting = false
  }
}

function bindEvents() {
  $$('[data-workspace]').forEach(button => button.onclick = () => switchWorkspace(button.dataset.workspace))
  $$('[data-production-mode]').forEach(button => button.onclick = () => setProductionMode(button.dataset.productionMode))
  $$('input[name="replayMode"]').forEach(input => input.onchange = () => {
    if (input.value === 'local_replace' && input.checked) {
      $('replayPeople').value = 'preserve'
      $('replayText').value = 'keep'
      $('replayProduct').value = 'keep'
      $('replayAudio').value = 'original'
      $('replayScene').value = 'preserve'
      $('replayCamera').value = 'preserve'
      if (!state.replayReplacementItems.length) state.replayReplacementItems = [newReplayReplacement()]
    }
    if (input.value === 'full_structure' && input.checked && $('replayAudio').value === 'partial_redub') {
      $('replayAudio').value = 'redub'
    }
    state.replay = null
    state.replayConfigured = false
    renderReplay()
    syncReplayModeControls()
    updateFormSummary()
  })
  $('manualRefresh').onclick = () => busy($('manualRefresh'), () => resyncEventStream(), '实时状态已重新同步')
  $('projectAdminSettings').onclick = () => switchWorkspace('agent')
  $('newMaterialRequest').onclick = resetMaterialRequest
  $('saveMaterialRequest').onclick = () => busy($('saveMaterialRequest'), () => saveMaterialRequest(), '需求已保存').catch(() => {})
  $('toggleRequestPool').onclick = () => {
    $('materialRequestPool').classList.toggle('hidden')
    if (!$('materialRequestPool').classList.contains('hidden')) loadMaterialRequests().catch(error => toast(errorText(error), 'error'))
  }
  const planInputs = new Set(['product', 'productParticipation', 'platform', 'platformParticipation', 'sellingPoints', 'sellingPointsParticipation', 'promotion', 'promotionParticipation', 'script', 'requirement', 'creativeOption', 'directH3Prompt', 'duration', 'ratio', 'audioEnabled', 'containsPerson', 'containsVoice', 'stripReferenceText', 'referenceIdentityPolicy', 'commercialAppearance', 'directionCount'])
  $('productionForm').oninput = event => {
    // Editing a brief is the primary task.  Hide the bottom-sheet queue so it
    // cannot intercept the sticky validation and submission controls.
    setQueueOpen(false)
    if (event.target.id === 'promptOptimizationCandidate') {
      if (state.promptOptimization) {
        state.promptOptimization.edited_candidate_visual_prompt = event.target.value
        state.promptOptimization.user_edited_after_optimization = event.target.value !== state.promptOptimization.optimized_visual_prompt
        saveDraft()
      }
      return
    }
    if (event.target.id === 'requirement' && state.promptOptimization && !state.applyingPromptOptimization) {
      if (state.promptOptimization.adopted) {
        state.promptOptimization.edited_candidate_visual_prompt = event.target.value
        state.promptOptimization.user_edited_after_optimization = event.target.value !== state.promptOptimization.optimized_visual_prompt
        renderPromptOptimization()
      } else {
        state.promptOptimization = null
        renderPromptOptimization()
      }
    }
    if (state.productionMode === 'replay' && event.target.closest('#replayPanel')) {
      state.replayConfigured = false
    }
    if (event.target.matches('[data-shot-field]')) {
      state.shotEditsDirty = true
      $('recompileShots').disabled = false
      $('promptEditStatus').textContent = '逐镜头已修改 · 提交前将由服务端重新校验和编译'
      return
    }
    if (event.target.id === 'script' || event.target.id === 'audioEnabled') applyDetectedContentFlags()
    if (event.target.id === 'requirement') showAssetMentionMenu().catch(() => {})
    if (['product', 'requirement', 'script'].includes(event.target.id)) scheduleAssetRecommendation()
    if (event.target.id === 'creativeOption') syncDirectPromptUi()
    if (event.target.id === 'ratio') renderPresets()
    if (planInputs.has(event.target.id)) invalidatePlan()
    updateFormSummary()
  }
  const handleProductionSubmit = event => {
    event.preventDefault()
    setQueueOpen(false)
    submitProduction().catch(() => {})
  }
  // Browser-hosted projects run inside a managed iframe.  Some hosts do not
  // promote a submit-button click into the form's native submit event, so the
  // primary CTA owns an explicit handler while Enter keeps the form handler.
  $('productionForm').onsubmit = handleProductionSubmit
  $('submitButton').onclick = handleProductionSubmit
  $('optimizePromptButton').onclick = () => optimizePrompt().catch(() => {})
  $('keepOriginalPrompt').onclick = keepOriginalPrompt
  $('retryPromptOptimization').onclick = () => optimizePrompt().catch(() => {})
  $('adoptOptimizedPrompt').onclick = adoptOptimizedPrompt
  $('prepareButton').onclick = () => startStrategy({ refresh: Boolean(state.strategy) }).catch(() => {})
  $('regenerateStrategy').onclick = () => startStrategy({ refresh: true }).catch(() => {})
  $('analyzeReplay').onclick = () => analyzeReplay().catch(() => {})
  $('previewReplay').onclick = () => previewReplay().catch(() => {})
  $('generateReplay').onclick = () => generateReplay().catch(() => {})
  $('addReplayReplacement').onclick = () => {
    state.replayReplacementItems = [
      ...readReplayReplacementItems({ includeEmpty: true }),
      newReplayReplacement(),
    ]
    renderLocalReplacementEditor()
    state.replayConfigured = false
  }
  for (const id of ['replayPeople', 'replayText', 'replayProduct', 'replayAudio', 'replayScene', 'replayCamera']) {
    $(id).addEventListener('change', () => {
      state.replayConfigured = false
      syncReplayActionState()
      updateFormSummary()
    })
  }
  $('agentSystemPrompt').oninput = event => {
    const agent = selectedAgent()
    if (!agent) return
    state.agentDrafts.set(agent.agent_key, event.target.value)
    state.dirtyAgents.add(agent.agent_key)
    $('agentSaveStatus').textContent = '有未保存修改；实际调用仍使用已保存版本'
  }
  $('saveAgent').onclick = () => saveCurrentAgent().catch(() => {})
  $('restoreAgent').onclick = () => restoreCurrentAgent().catch(() => {})
  $('invokeAgent').onclick = () => invokeCurrentAgent().catch(() => {})
  $('useOperatorPromptButton').onclick = () => prepareProduction({
    planningMode: 'operator_brief',
    button: $('useOperatorPromptButton'),
  }).catch(() => {})
  $('generateFirstFrameButton').onclick = () => generateCharacterFirstFrame().catch(() => {})
  $('recompileShots').onclick = () => recompileShotPlan().catch(() => {})
  $('compiledPrompt').oninput = () => {
    if (state.productionMode === 'direct') {
      state.directFinalPromptEdited = $('compiledPrompt').value !== state.directBasePrompt
      $('promptEditStatus').textContent = state.directFinalPromptEdited
        ? '已手工修改；当前文本会原样发送到 Bridge。'
        : '这里看到的内容会原样发送到 Bridge；业务字段默认不参与生成。'
      return
    }
    const deterministic = String(state.prepared?.prepared_plan?.integrated_multimodal_description || state.prepared?.prepared_plan?.video_prompt || '').trim()
    $('promptEditStatus').textContent = $('compiledPrompt').value.trim() === deterministic
      ? '服务端确定性编译结果 · 可编辑'
      : '已手工修改 · 提交时经服务端校验后生效'
  }
  $('assetUpload').onchange = event => uploadAssets([...event.target.files])
  for (const type of ['dragenter', 'dragover']) $('assetDrop').addEventListener(type, event => { event.preventDefault(); $('assetDrop').classList.add('dragover') })
  for (const type of ['dragleave', 'drop']) $('assetDrop').addEventListener(type, event => { event.preventDefault(); $('assetDrop').classList.remove('dragover'); if (type === 'drop') uploadAssets([...event.dataTransfer.files]) })
  $('refreshAssetRecommendation').onclick = () => busy($('refreshAssetRecommendation'), () => loadAssetRecommendation(), '素材推荐已更新').catch(() => {})
  $('themeEnabled').onchange = () => { $('themeSettings').classList.toggle('hidden', !$('themeEnabled').checked); invalidatePlan(); updateFormSummary() }
  $$('input[name="schedule"]').forEach(input => input.onchange = () => { $('customSchedule').classList.toggle('hidden', selectedSchedule() !== 'custom'); updateFormSummary() })
  $('queueToggle').onclick = () => setQueueOpen($('globalQueue').classList.contains('collapsed'))
  $('queueRefresh').onclick = () => busy($('queueRefresh'), () => loadJobs(), '队列已刷新')
  $$('[data-job-filter]').forEach(button => button.onclick = () => { $$('[data-job-filter]').forEach(item => item.classList.remove('active')); button.classList.add('active'); state.jobFilter = button.dataset.jobFilter; state.jobListLoaded = false; loadJobs().catch(error => toast(errorText(error), 'error')) })
  $('loadMoreJobs').onclick = () => busy($('loadMoreJobs'), () => loadJobs({ append: true }))
  $('reviewRefresh').onclick = () => busy($('reviewRefresh'), () => loadReviews(), '审片箱已刷新')
  $('reviewStatus').onchange = () => {
    state.reviewView = $('reviewStatus').value
    $$('[data-review-view]').forEach(button => button.classList.toggle('active', button.dataset.reviewView === state.reviewView))
    saveReviewPreference()
    loadReviews().catch(error => toast(errorText(error), 'error'))
  }
  $('reviewMethod').onchange = () => { saveReviewPreference(); loadReviews().catch(error => toast(errorText(error), 'error')) }
  $('reviewSource').onchange = () => { saveReviewPreference(); loadReviews().catch(error => toast(errorText(error), 'error')) }
  $('reviewSort').onchange = () => {
    saveReviewPreference()
    loadReviews().catch(error => toast(errorText(error), 'error'))
  }
  $('reviewSearch').onkeydown = event => { if (event.key === 'Enter') loadReviews().catch(error => toast(errorText(error), 'error')) }
  $('reviewTagInput').onkeydown = event => {
    if (event.key !== 'Enter') return
    event.preventDefault()
    if (addReviewTag(event.target.value)) {
      event.target.value = ''
      saveReviewPreference()
      loadReviews().catch(error => toast(errorText(error), 'error'))
    }
  }
  for (const id of ['reviewProduct', 'reviewBatch', 'reviewAssignee']) $(id).onkeydown = event => { if (event.key === 'Enter') { saveReviewPreference(); loadReviews().catch(error => toast(errorText(error), 'error')) } }
  $('loadMoreReviews').onclick = () => busy($('loadMoreReviews'), () => loadReviews({ append: true }))
  $('loadMoreCandidates').onclick = () => busy($('loadMoreCandidates'), () => loadReviews({ append: true }))
  $$('[data-review-view]').forEach(button => button.onclick = () => {
    state.reviewView = button.dataset.reviewView
    $$('[data-review-view]').forEach(item => item.classList.toggle('active', item === button))
    $('reviewStatus').value = ['pending', 'selected', 'held', 'rejected'].includes(state.reviewView) ? state.reviewView : 'all'
    saveReviewPreference()
    loadReviews().catch(error => toast(errorText(error), 'error'))
  })
  $$('[data-wall-density]').forEach(button => button.onclick = () => {
    state.wallDensity = button.dataset.wallDensity
    $$('[data-wall-density]').forEach(item => item.classList.toggle('active', item === button))
    saveReviewPreference()
    renderMaterialWall()
  })
  $('backToMaterialWall').onclick = showMaterialWall
  $('closeReviewDialog').onclick = showMaterialWall
  $('reviewDialog').addEventListener('cancel', event => { event.preventDefault(); showMaterialWall() })
  $('reviewDialog').addEventListener('close', () => {
    if (state.reviewSession || state.reviewManifest) {
      state.reviewJobId = ''; state.compareJobId = ''; state.reviewManifest = null; state.reviewSession = null
      renderMaterialWall()
      requestAnimationFrame(() => window.scrollTo({ top: state.reviewWallScrollY, behavior: 'instant' }))
    }
  })
  $('editCandidateTags').onclick = () => editActiveCandidateTags().catch(error => toast(errorText(error), 'error'))
  $('previousCandidate').onclick = () => {
    const id = state.reviewSession?.previous_candidate_id
    if (id) openCandidateReview(id).catch(error => toast(errorText(error), 'error'))
  }
  $('nextCandidate').onclick = () => {
    const id = state.reviewSession?.next_candidate_id
    if (id) openCandidateReview(id).catch(error => toast(errorText(error), 'error'))
  }
  $('reviewSelectAll').onchange = event => { for (const item of state.reviews) { const id = item.candidate_id || item.job.id; event.target.checked ? state.selectedReviewIds.add(id) : state.selectedReviewIds.delete(id) } renderReviewList() }
  $('batchReject').onclick = () => batchReject().catch(() => {}); $('batchHold').onclick = () => batchHold().catch(() => {}); $('batchAssign').onclick = () => batchAssign().catch(() => {})
  $('compareJobSelect').onchange = event => { state.compareSelectionJobId = event.target.value }
  $('compareToggle').onclick = () => { const id = $('compareJobSelect').value; if (!state.activeCandidateId || !id) return toast('先选择 A 片和 B 片', 'error'); openCandidateReview(state.activeCandidateId, id).catch(error => toast(errorText(error), 'error')) }
  $('enhance1080Button').onclick = () => enhanceCurrent1080p().catch(() => {})
  $('frameBack').onclick = () => { $('reviewVideoA').currentTime = Math.max(0, $('reviewVideoA').currentTime - 1 / 24) }
  $('frameForward').onclick = () => { $('reviewVideoA').currentTime = Math.min($('reviewVideoA').duration || Infinity, $('reviewVideoA').currentTime + 1 / 24) }
  $('playbackRate').onchange = () => { $('reviewVideoA').playbackRate = Number($('playbackRate').value); $('reviewVideoB').playbackRate = Number($('playbackRate').value) }
  $('reviewPlayToggle').onclick = () => toggleReviewPlayback()
  $('reviewSoundToggle').onclick = () => {
    const primary = state.reviewManifest?.primary || {}
    if (reviewAudioState(primary).tone !== 'ready') return
    const video = $('reviewVideoA')
    video.muted = !video.muted
    // A/B 对比只监听 A 片，避免两路相似对白同时播放形成回声。
    $('reviewVideoB').muted = true
    updateReviewSoundToggle(primary)
  }
  $('downloadProvenance').onclick = downloadProvenance
  $('reviewVideoA').onplay = updateReviewPlayToggle
  $('reviewVideoA').onpause = updateReviewPlayToggle
  $('reviewVideoA').onended = updateReviewPlayToggle
  $('reviewVideoA').onvolumechange = () => updateReviewSoundToggle(state.reviewManifest?.primary || {})
  $('reviewVideoA').ontimeupdate = () => { const time = $('reviewVideoA').currentTime || 0; $('timecode').textContent = `${String(Math.floor(time / 60)).padStart(2, '0')}:${String(Math.floor(time % 60)).padStart(2, '0')}.${String(Math.floor(time % 1 * 1000)).padStart(3, '0')}`; if (state.compareJobId && Math.abs(($('reviewVideoB').currentTime || 0) - time) > .08) $('reviewVideoB').currentTime = time }
  $('saveAnnotation').onclick = () => saveAnnotation().catch(() => {}); $('approveButton').onclick = () => approveReview().catch(() => {}); $('holdButton').onclick = () => holdReview().catch(() => {}); $('rejectButton').onclick = () => rejectReview().catch(() => {}); $('deliveryButton').onclick = () => deliverSelectedCandidate().catch(() => {}); $('dialogueDeliveryButton').onclick = () => retryDialogueDelivery().catch(() => {})
  $('runQualityAnalysis').onclick = async () => {
    if (!state.reviewJobId) return toast('请先选择视频', 'error')
    try {
      const result = await busy($('runQualityAnalysis'), () => api.qualityAnalyze(state.reviewJobId), '视觉质检已进入队列；完成后会实时回写')
      const job = (result.jobs || [])[0]
      if (job) mergeSelectedQualityFromJob(job)
    } catch { /* busy already reports the governed capability error */ }
  }
  $('cloudReferenceOpen').onclick = () => { $('cloudReferenceDialog').showModal(); searchCloud().catch(() => {}) }; $('cloudSearchButton').onclick = () => searchCloud().catch(() => {})
  $('assetLibraryOpen').onclick = () => openAssetLibrary().catch(error => toast(errorText(error), 'error'))
  $('assetLibraryRefresh').onclick = () => openAssetLibrary().catch(error => toast(errorText(error), 'error'))
  $('assetLibrarySearch').oninput = renderAssetLibrary
  $('requirement').onfocus = () => { if (assetMentionContext()) showAssetMentionMenu().catch(() => {}) }
  document.addEventListener('pointerdown', event => { if (!event.target.closest('.asset-mention-field')) hideAssetMentionMenu() })
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !$('assetMentionMenu').classList.contains('hidden')) { hideAssetMentionMenu(); return } if (event.key === 'Escape' && !$('globalQueue').classList.contains('collapsed')) { setQueueOpen(false); return } if (event.target.matches('input,textarea,select,[contenteditable="true"]')) return; if (event.code === 'Space' && state.reviewJobId) { event.preventDefault(); toggleReviewPlayback() } if (event.key === 'ArrowLeft' && state.reviewJobId) $('previousCandidate').click(); if (event.key === 'ArrowRight' && state.reviewJobId) $('nextCandidate').click(); if (event.key.toLowerCase() === 'a' && state.reviewJobId) $('approveButton').click(); if (event.key.toLowerCase() === 'h' && state.reviewJobId) $('holdButton').click(); if (event.key.toLowerCase() === 'r' && state.reviewJobId) $('rejectButton').click(); if (event.key.toLowerCase() === 'm' && state.reviewJobId) $('reviewSoundToggle').click(); if (event.key.toLowerCase() === 'f' && state.reviewJobId) $('reviewVideoA').requestFullscreen?.() })
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopEventStream(); else if (state.connected) connectEvents() })
}

async function bootstrap() {
  restoreDraft(); restoreManifestCache(); bindEvents(); setProductionMode(state.productionMode); syncReplayModeControls(); renderAssets(); renderPresets(); updateFormSummary(); clearViewer(); renderAgentEditor(); renderCurrentMaterialRequest(); renderMaterialRequestPool(); renderMaterialWall()
  const activeWorkspace = $('[data-workspace].active')?.dataset.workspace || 'production'
  document.documentElement.classList.toggle('review-mode', activeWorkspace === 'review')
  document.body.classList.toggle('review-mode', activeWorkspace === 'review')
  if (activeWorkspace === 'review') resetReviewViewportScroll()
  if (!gatewayAvailable()) { setConnection('offline', '离线视觉预览'); renderNodes(); renderJobs(); renderReviewList(); return }
  try {
    await ready(); state.connected = true; setConnection('online', 'Project Gateway 已连接')
    state.heartbeatStop = startHeartbeat()
    // One authoritative snapshot hydrates nodes, presets and counters.  From
    // this point onward the event cursor owns updates; there is no polling.
    renderSnapshot(await api.snapshot())
    // Read the first queue page once so the headline and drawer always use the
    // same authoritative status counts. Further updates arrive via the event
    // stream; this is not a polling loop.
    try { await loadJobs() } catch (error) { if (isCapabilityQuotaError(error)) throw error }
    loadMaterialRequests().catch(() => {})
    await connectEvents()
    // 用户可能在 Gateway 尚未就绪时已经切到审片页。连接建立后必须
    // 补一次权威列表，不能只显示事件快照中的无封面占位数据。
    if (!$('reviewWorkspace').classList.contains('hidden') && !state.reviewListLoaded && !state.quotaExhausted) {
      await loadReviews()
    }
    if (!$('agentWorkspace').classList.contains('hidden') && !state.agentsLoaded && !state.quotaExhausted) {
      await loadAgents()
    }
    recordInput({ surface: 'material_workbench_v40', transport: 'cursor_event_stream', defaults: { preset: state.selectedPreset, ratio: $('ratio').value, duration_seconds: 5 } }).catch(() => {})
  } catch (error) {
    if (isCapabilityQuotaError(error)) showRunRecovery()
    else { setConnection('offline', '连接失败'); renderQueueLoadError(error); toast(errorText(error), 'error') }
  }
}

window.addEventListener('pagehide', () => {
  stopEventStream(); state.heartbeatStop?.()
  for (const url of state.reviewImageUrls.values()) URL.revokeObjectURL(url)
}, { once: true })
bootstrap()
