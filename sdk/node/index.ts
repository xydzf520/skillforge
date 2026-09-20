export type SkillForgeProjectSdkOptions = {
  baseUrl: string
  projectId: string
  token: string
  fetchImpl?: typeof fetch
  timeoutMs?: number
  maxRetries?: number
  retryBaseMs?: number
  allowInsecureHttp?: boolean
}

export type RequestOptions = {
  requestId?: string
  timeoutMs?: number
  maxRetries?: number
}

export type UploadAssetOptions = RequestOptions & {
  fileName?: string
  mimeType?: string
  metadata?: JsonObject
}

type JsonObject = Record<string, unknown>
export type ErrorCategory = 'auth_error' | 'validation_error' | 'quota_error' | 'transient_error' | 'server_error'

const DEFAULT_TIMEOUT_MS = 180_000
const DEFAULT_MAX_RETRIES = 2
const DEFAULT_RETRY_BASE_MS = 500
const TRANSIENT_STATUSES = new Set([502, 503, 504])

function joinUrl(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/+$/, '')}${path}`
}

function requestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `sf_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

function isLocalHttpHost(hostname: string): boolean {
  const value = hostname.replace(/^\[|\]$/g, '').toLowerCase()
  return value === 'localhost' || value === '127.0.0.1' || value === '::1' || value.endsWith('.localhost')
}

function validateBaseUrl(baseUrl: string, allowInsecureHttp: boolean): string {
  let parsed: URL
  try {
    parsed = new URL(baseUrl)
  } catch {
    throw new Error('baseUrl must be an absolute URL')
  }
  if (parsed.protocol !== 'https:' && !(parsed.protocol === 'http:' && (allowInsecureHttp || isLocalHttpHost(parsed.hostname)))) {
    throw new Error('baseUrl must use HTTPS for external Project SDK access')
  }
  return parsed.toString().replace(/\/+$/, '')
}

function errorCategory(status: number | undefined, body: unknown): ErrorCategory {
  const detail = (body as any)?.error?.detail
  const explicit = typeof detail?.error_category === 'string' ? detail.error_category : undefined
  if (explicit === 'auth_error' || explicit === 'validation_error' || explicit === 'quota_error' || explicit === 'transient_error' || explicit === 'server_error') {
    return explicit
  }
  if (status === 401 || status === 403) return 'auth_error'
  if (status === 429) return 'quota_error'
  if (status === 400 || status === 404 || status === 413 || status === 422) return 'validation_error'
  if (status && TRANSIENT_STATUSES.has(status)) return 'transient_error'
  return 'server_error'
}

function retryAfterSeconds(response: Response, body: unknown): number | undefined {
  const detail = (body as any)?.error?.detail
  const fromBody = Number(detail?.retry_after_seconds)
  if (Number.isFinite(fromBody) && fromBody > 0) return fromBody
  const fromHeader = Number(response.headers.get('retry-after'))
  if (Number.isFinite(fromHeader) && fromHeader > 0) return fromHeader
  return undefined
}

export class SkillForgeProjectSdkError extends Error {
  readonly status?: number
  readonly code: string
  readonly category: ErrorCategory
  readonly retryAfterSeconds?: number
  readonly responseBody?: unknown

  constructor(message: string, options: {
    status?: number
    code: string
    category: ErrorCategory
    retryAfterSeconds?: number
    responseBody?: unknown
  }) {
    super(message)
    this.name = 'SkillForgeProjectSdkError'
    this.status = options.status
    this.code = options.code
    this.category = options.category
    this.retryAfterSeconds = options.retryAfterSeconds
    this.responseBody = options.responseBody
  }
}

export class SkillForgeProjectSdk {
  private readonly baseUrl: string
  private readonly projectId: string
  private readonly token: string
  private readonly fetchImpl: typeof fetch
  private readonly timeoutMs: number
  private readonly maxRetries: number
  private readonly retryBaseMs: number

  constructor(options: SkillForgeProjectSdkOptions) {
    if (!options.baseUrl) throw new Error('baseUrl is required')
    if (!options.projectId) throw new Error('projectId is required')
    if (!options.token) throw new Error('token is required')
    this.baseUrl = validateBaseUrl(options.baseUrl, Boolean(options.allowInsecureHttp))
    this.projectId = options.projectId
    this.token = options.token
    this.fetchImpl = options.fetchImpl || fetch
    this.timeoutMs = Math.max(1_000, Number(options.timeoutMs || DEFAULT_TIMEOUT_MS))
    this.maxRetries = Math.max(0, Number(options.maxRetries ?? DEFAULT_MAX_RETRIES))
    this.retryBaseMs = Math.max(50, Number(options.retryBaseMs || DEFAULT_RETRY_BASE_MS))
  }

  async startRun(payload: JsonObject = {}, options: RequestOptions = {}) {
    return this.post('/api/projects/sdk/runs', {
      project_id: this.projectId,
      request_id: options.requestId || payload.request_id || requestId(),
      ...payload,
    }, options)
  }

  async input(projectRunId: string, payload: JsonObject, options: RequestOptions = {}) {
    return this.post(`/api/projects/sdk/runs/${encodeURIComponent(projectRunId)}/input`, {
      request_id: options.requestId || payload.request_id || requestId(),
      ...payload,
    }, options)
  }

  async ingest(projectRunId: string, payload: JsonObject, options: RequestOptions = {}) {
    return this.post(`/api/projects/sdk/runs/${encodeURIComponent(projectRunId)}/ingest`, {
      request_id: options.requestId || payload.request_id || requestId(),
      ...payload,
    }, options)
  }

  async capability(projectRunId: string, payload: JsonObject, options: RequestOptions = {}) {
    return this.post(`/api/projects/sdk/runs/${encodeURIComponent(projectRunId)}/capability`, {
      request_id: options.requestId || payload.request_id || requestId(),
      ...payload,
    }, options)
  }

  async generate(projectRunId: string, prompt: string, input: JsonObject = {}, options: RequestOptions & JsonObject = {}) {
    return this.capability(projectRunId, {
      capability: 'ai.generate',
      prompt,
      input,
      json_mode: Boolean(options.jsonMode || options.json_mode),
      max_output_tokens: options.maxOutputTokens || options.max_output_tokens,
      temperature: options.temperature,
    }, options)
  }

  async chat(projectRunId: string, messages: unknown[], input: JsonObject = {}, options: RequestOptions & JsonObject = {}) {
    return this.capability(projectRunId, {
      capability: 'ai.chat',
      input: { ...input, messages },
      max_output_tokens: options.maxOutputTokens || options.max_output_tokens,
      temperature: options.temperature,
    }, options)
  }

  async list236Models(options: RequestOptions = {}) {
    return this.get('/api/projects/openai/236/v1/models', options)
  }

  async chat236(
    projectRunId: string,
    model: string,
    messages: unknown[],
    input: JsonObject = {},
    options: RequestOptions & JsonObject = {},
  ) {
    return this.capability(projectRunId, {
      capability: 'ai.chat',
      target_gateway_id: 'inference-primary',
      model,
      messages,
      input: { ...input, messages },
      max_tokens: options.maxTokens || options.max_tokens,
      max_output_tokens: options.maxOutputTokens || options.max_output_tokens,
      max_input_tokens: options.maxInputTokens || options.max_input_tokens,
      context_window: options.contextWindow || options.context_window,
      truncation: options.truncation,
      temperature: options.temperature,
      timeout_seconds: options.timeoutSeconds || options.timeout_seconds,
    }, options)
  }

  async openai236Chat(payload: JsonObject, options: RequestOptions = {}) {
    return this.post('/api/projects/openai/236/v1/chat/completions', {
      request_id: options.requestId || payload.request_id || requestId(),
      ...payload,
    }, options)
  }

  async analyze(projectRunId: string, input: JsonObject = {}, options: RequestOptions & JsonObject = {}) {
    return this.capability(projectRunId, {
      capability: 'ai.analyze',
      prompt: options.prompt,
      input,
    }, options)
  }

  async trace(projectRunId: string, options: RequestOptions = {}) {
    return this.get(`/api/projects/sdk/runs/${encodeURIComponent(projectRunId)}/trace`, options)
  }

  async trainingSync(projectRunId: string, payload: JsonObject = {}, options: RequestOptions = {}) {
    return this.post(`/api/projects/sdk/runs/${encodeURIComponent(projectRunId)}/training-sync`, {
      request_id: options.requestId || payload.request_id || requestId(),
      ...payload,
    }, options)
  }

  async uploadAsset(projectRunId: string, file: Blob, options: UploadAssetOptions = {}) {
    const form = new FormData()
    form.append('file', file, options.fileName || 'asset')
    if (options.metadata) form.append('metadata_json', JSON.stringify(options.metadata))
    return this.requestForm(`/api/projects/sdk/runs/${encodeURIComponent(projectRunId)}/assets`, form, options)
  }

  async recordTrainingSample(projectRunId: string, content: JsonObject, options: RequestOptions & JsonObject = {}) {
    return this.post(`/api/projects/sdk/runs/${encodeURIComponent(projectRunId)}/training-samples`, {
      request_id: options.requestId || options.request_id || requestId(),
      asset_id: options.assetId || options.asset_id,
      dataset_profile: options.datasetProfile || options.dataset_profile || 'text_sft_v1',
      modality: options.modality,
      content,
      media_refs: options.mediaRefs || options.media_refs || [],
      labels: options.labels || [],
      quality_score: options.qualityScore || options.quality_score,
      metadata: options.metadata || {},
    }, options)
  }

  async listTrainableAssets(options: RequestOptions & { limit?: number } = {}) {
    const limit = Math.max(1, Math.min(200, Number(options.limit || 200)))
    return this.get(`/api/projects/sdk/training/assets?limit=${limit}`, options)
  }

  async createDatasetVersion(payload: JsonObject, options: RequestOptions = {}) {
    return this.post('/api/projects/sdk/training/datasets', payload, options)
  }

  async syncTrainingDataset(datasetVersionId: string, payload: JsonObject = {}, options: RequestOptions = {}) {
    return this.post(`/api/projects/sdk/training/datasets/${encodeURIComponent(datasetVersionId)}/sync`, payload, options)
  }

  private async get(path: string, options: RequestOptions = {}) {
    return this.request('GET', path, undefined, options)
  }

  private async post(path: string, payload: JsonObject, options: RequestOptions) {
    return this.request('POST', path, payload, options)
  }

  private async requestForm(path: string, form: FormData, options: RequestOptions) {
    const maxRetries = Math.max(0, Number(options.maxRetries ?? this.maxRetries))
    let attempt = 0
    for (;;) {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), Math.max(1_000, Number(options.timeoutMs || this.timeoutMs)))
      try {
        const response = await this.fetchImpl(joinUrl(this.baseUrl, path), {
          method: 'POST',
          headers: { Authorization: `Bearer ${this.token}` },
          body: form,
          signal: controller.signal,
        })
        return await this.parse(response)
      } catch (error) {
        if (error instanceof SkillForgeProjectSdkError) {
          if (TRANSIENT_STATUSES.has(error.status || 0) && attempt < maxRetries) {
            await sleep(this.retryDelayMs(attempt))
            attempt += 1
            continue
          }
          throw error
        }
        if (attempt < maxRetries) {
          await sleep(this.retryDelayMs(attempt))
          attempt += 1
          continue
        }
        const isAbort = (error as any)?.name === 'AbortError'
        throw new SkillForgeProjectSdkError(
          isAbort ? 'SkillForge request timed out' : ((error as Error)?.message || 'SkillForge network request failed'),
          {
            code: isAbort ? 'REQUEST_TIMEOUT' : 'NETWORK_ERROR',
            category: 'transient_error',
          },
        )
      } finally {
        clearTimeout(timer)
      }
    }
  }

  private async request(method: 'GET' | 'POST', path: string, payload: JsonObject | undefined, options: RequestOptions) {
    const maxRetries = Math.max(0, Number(options.maxRetries ?? this.maxRetries))
    const body = payload ? JSON.stringify(payload) : undefined
    let attempt = 0
    for (;;) {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), Math.max(1_000, Number(options.timeoutMs || this.timeoutMs)))
      try {
        const response = await this.fetchImpl(joinUrl(this.baseUrl, path), {
          method,
          headers: {
            Authorization: `Bearer ${this.token}`,
            ...(payload ? { 'Content-Type': 'application/json' } : {}),
          },
          body,
          signal: controller.signal,
        })
        const parsed = await this.parse(response)
        return parsed
      } catch (error) {
        if (error instanceof SkillForgeProjectSdkError) {
          if (TRANSIENT_STATUSES.has(error.status || 0) && attempt < maxRetries) {
            await sleep(this.retryDelayMs(attempt))
            attempt += 1
            continue
          }
          throw error
        }
        if (attempt < maxRetries) {
          await sleep(this.retryDelayMs(attempt))
          attempt += 1
          continue
        }
        const isAbort = (error as any)?.name === 'AbortError'
        throw new SkillForgeProjectSdkError(
          isAbort ? 'SkillForge request timed out' : ((error as Error)?.message || 'SkillForge network request failed'),
          {
            code: isAbort ? 'REQUEST_TIMEOUT' : 'NETWORK_ERROR',
            category: 'transient_error',
          },
        )
      } finally {
        clearTimeout(timer)
      }
    }
  }

  private async parse(response: Response) {
    const body = await response.json().catch(() => ({}))
    if (!response.ok) {
      const detail = (body as any)?.error?.detail
      const message = (
        typeof detail === 'string'
          ? detail
          : (body as any)?.error?.message || (body as any)?.detail || `SkillForge request failed: ${response.status}`
      )
      throw new SkillForgeProjectSdkError(message, {
        status: response.status,
        code: (body as any)?.error?.code || `HTTP_${response.status}`,
        category: errorCategory(response.status, body),
        retryAfterSeconds: retryAfterSeconds(response, body),
        responseBody: body,
      })
    }
    return body
  }

  private retryDelayMs(attempt: number): number {
    const jitter = Math.floor(Math.random() * 100)
    return this.retryBaseMs * Math.pow(2, attempt) + jitter
  }
}

export function createSkillForgeProjectSdk(options: SkillForgeProjectSdkOptions) {
  return new SkillForgeProjectSdk(options)
}
