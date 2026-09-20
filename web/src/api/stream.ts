import type {
  CodingDonePayload,
  CodingFileChangePayload,
  CodingPermissionRequestPayload,
  CodingSessionReadyPayload,
  CodingToolCallPayload,
  CodingToolResultPayload,
  CodingUsagePayload,
} from '@/types/coding-agent'

const HEARTBEAT_INTERVAL_MS = 30_000

type JsonRecord = Record<string, any>

function buildWsUrl(path: string): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}${path}`
}

type WsLike = {
  readyState: number
  send: (payload: string) => void
  close?: () => void
  onopen?: (() => void) | null
  onerror?: ((event: Event) => void) | null
  onclose?: ((event: CloseEvent) => void) | null
  onmessage?: ((event: MessageEvent<string>) => void) | null
}

/**
 * 多订阅者 dispatcher 接口。
 *
 * 设计目的：解决"共享 client 回调互相覆盖"的致命问题——
 * 旧实现用 `client.onTextDelta = fn` 直接赋值 closure，第二次调用会覆盖
 * 第一次的 handler，导致旧 Promise 永不 resolve、chunk 串台。
 *
 * 正确用法：`client.addSubscriber({ id, onTextDelta, onDone, ... })`；
 * subscriber 完成后调 `client.removeSubscriber(id)`。
 *
 * 为了向后兼容（单测里还在用 `client.onSessionReady = fn` 模式），
 * 旧的直接赋值 handler 仍然会被触发（作为 "legacy default subscriber"），
 * 但新代码一律走 addSubscriber 路径。
 */
export interface StreamSubscriber {
  id: string
  onSessionReady?: (payload: CodingSessionReadyPayload) => void
  onTextDelta?: (chunk: string) => void
  onToolCall?: (call: CodingToolCallPayload) => void
  onToolResult?: (r: CodingToolResultPayload) => void
  onFileChange?: (c: CodingFileChangePayload) => void
  onPermissionRequest?: (p: CodingPermissionRequestPayload) => void
  onUsage?: (u: CodingUsagePayload) => void
  onDone?: (p: CodingDonePayload) => void
  onError?: (e: { code?: string; error?: string }) => void
  onDisconnect?: (code?: number, reason?: string) => void
  onDelta?: (text: string) => void
  onCompact?: (originalCount: number) => void
  onPatch?: (payload: { patch: unknown; message?: string; validationHint?: unknown }) => void
  /** 每收到一个事件都 fire，让 subscriber 跟踪 lastSeq 等上下文 */
  onSeqAdvance?: (seq: number) => void
}

class BaseStreamClient {
  ws: WebSocket | WsLike | null = null
  heartbeatTimer: ReturnType<typeof setInterval> | null = null
  connected = false

  /** 最近一次事件序号。后端带 _seq 时以后端游标为准；旧流无 _seq 时本地递增。 */
  lastSeq = 0

  /** 多订阅者列表；由 addSubscriber / removeSubscriber 管理。 */
  protected subscribers: StreamSubscriber[] = []

  // —— Legacy direct-assignment handlers（向后兼容）——
  //    新代码不要用，全部改走 addSubscriber。
  onDelta: ((text: string) => void) | null = null
  onCompact: ((originalCount: number) => void) | null = null
  onPatch: ((payload: { patch: unknown; message?: string; validationHint?: unknown }) => void) | null = null
  onUsage: ((usage: JsonRecord) => void) | null = null
  onDone: ((payload: JsonRecord) => void) | null = null
  onError: ((payload: { code?: string; error?: string }) => void) | null = null
  onDisconnect: ((code?: number, reason?: string) => void) | null = null

  buildPath(): string {
    throw new Error('subclass must implement buildPath()')
  }

  addSubscriber(sub: StreamSubscriber): void {
    // 同 id 去重：再 add 覆盖旧的（常见于 re-entry 场景）
    this.subscribers = this.subscribers.filter((s) => s.id !== sub.id)
    this.subscribers.push(sub)
  }

  removeSubscriber(id: string): void {
    this.subscribers = this.subscribers.filter((s) => s.id !== id)
  }

  hasSubscribers(): boolean {
    return this.subscribers.length > 0
  }

  /** 对所有订阅者（以及 legacy 直接赋值 handler）安全扇出。 */
  protected _fanout<K extends keyof StreamSubscriber>(
    hook: K,
    ...args: StreamSubscriber[K] extends ((...a: infer A) => any) | undefined ? A : never[]
  ): void {
    for (const sub of [...this.subscribers]) {
      const fn = sub[hook] as ((...a: unknown[]) => void) | undefined
      if (typeof fn === 'function') {
        try {
          fn.apply(sub, args as unknown[])
        } catch (e) {
          console.warn(`[stream] subscriber ${sub.id} ${String(hook)} threw:`, e)
        }
      }
    }
  }

  protected _advanceSeqFromMessage(msg: JsonRecord): void {
    if (msg.type === 'pong' || msg.type === 'heartbeat') return
    const rawSeq = msg._seq
    const serverSeq = typeof rawSeq === 'number' ? rawSeq : Number(rawSeq)
    if (Number.isFinite(serverSeq) && serverSeq > 0) {
      this.lastSeq = Math.floor(serverSeq)
    } else {
      this.lastSeq += 1
    }
    this._fanout('onSeqAdvance', this.lastSeq)
  }

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      try {
        this.ws = new WebSocket(buildWsUrl(this.buildPath()))
      } catch (e) {
        reject(e)
        return
      }

      this.ws.onopen = () => {
        this.connected = true
        this._startHeartbeat()
        resolve()
      }

      this.ws.onerror = () => {
        if (!this.connected) {
          reject(new Error('WebSocket 连接失败'))
        }
      }

      this.ws.onclose = (event) => {
        this.connected = false
        this._stopHeartbeat()
        this.onDisconnect?.(event.code, event.reason)
        this._fanout('onDisconnect', event.code, event.reason)
      }

      this.ws.onmessage = (evt) => this._handleMessage(evt)
    })
  }

  _handleMessage(evt: MessageEvent<string>): void {
    let msg: JsonRecord
    try {
      msg = JSON.parse(evt.data)
    } catch {
      console.warn('[stream] 无法解析消息:', evt.data)
      return
    }
    this._advanceSeqFromMessage(msg)
    switch (msg.type) {
      case 'delta':
        this.onDelta?.(msg.content || '')
        this._fanout('onDelta', msg.content || '')
        break
      case 'compact':
        this.onCompact?.(msg.original_count || 0)
        this._fanout('onCompact', msg.original_count || 0)
        break
      case 'patch':
        {
          const payload = {
            patch: msg.patch,
            message: msg.message,
            validationHint: msg.validation_hint,
          }
          this.onPatch?.(payload)
          this._fanout('onPatch', payload)
        }
        break
      case 'usage':
      case 'usage_summary':
        this.onUsage?.(msg.usage || msg)
        this._fanout('onUsage', (msg.usage || msg) as CodingUsagePayload)
        break
      case 'done':
        {
          const payload = {
            ...msg,
            finish_reason: msg.finish_reason,
            usage: msg.usage,
            total_tokens: msg.total_tokens,
          }
          this.onDone?.(payload)
          this._fanout('onDone', payload as CodingDonePayload)
        }
        break
      case 'error':
        this.onError?.({ code: msg.code, error: msg.error })
        this._fanout('onError', { code: msg.code, error: msg.error })
        break
      case 'pong':
      case 'heartbeat':
        break
      default:
        console.warn('[stream] 未知消息类型:', msg.type, msg)
    }
  }

  _startHeartbeat(): void {
    this._stopHeartbeat()
    this.heartbeatTimer = setInterval(() => {
      if (this.connected && this.ws?.readyState === 1) {
        try { this.ws.send(JSON.stringify({ type: 'ping' })) } catch { /* ignore */ }
      }
    }, HEARTBEAT_INTERVAL_MS)
  }

  _stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }

  stop(): void {
    if (this.connected && this.ws?.readyState === 1) {
      try { this.ws.send(JSON.stringify({ type: 'stop' })) } catch { /* ignore */ }
    }
  }

  close(): void {
    this._stopHeartbeat()
    if (this.ws) {
      try { this.ws.close?.() } catch { /* ignore */ }
      this.ws = null
    }
    this.connected = false
    this.subscribers = []
  }
}

export class ConversationStreamClient extends BaseStreamClient {
  conversationId: string

  constructor(conversationId: string) {
    super()
    this.conversationId = conversationId
  }

  buildPath(): string {
    return `/api/skills/chat/${this.conversationId}/stream`
  }

  sendMessage(content: string): void {
    if (!this.connected || this.ws?.readyState !== 1) {
      throw new Error('WebSocket 未连接')
    }
    this.ws.send(JSON.stringify({ type: 'user_message', content }))
  }
}

export class WorkbenchStreamClient extends BaseStreamClient {
  skillId: string

  constructor(skillId: string) {
    super()
    this.skillId = skillId
  }

  buildPath(): string {
    return `/api/skills/${this.skillId}/workbench/chat/stream`
  }

  sendMessage(content: string, opts: {
    sessionId?: string
    intent?: string | null
    context?: JsonRecord
    referenceIds?: string[]
  } = {}): void {
    if (!this.connected || this.ws?.readyState !== 1) {
      throw new Error('WebSocket 未连接')
    }
    this.ws.send(JSON.stringify({
      type: 'user_message',
      content,
      session_id: opts.sessionId || '',
      intent: opts.intent || null,
      context: opts.context || {},
      reference_ids: opts.referenceIds || [],
    }))
  }
}

export class CodingAgentStreamClient extends BaseStreamClient {
  skillId: string
  /** LRU 最近使用时间戳；由外部调用方在每次复用时更新。 */
  lastUsedAt = Date.now()

  // —— Legacy direct-assignment handlers（向后兼容 / 单测）——
  //    新代码必须走 addSubscriber；多订阅者场景下必定会覆盖彼此。
  onSessionReady: ((payload: CodingSessionReadyPayload) => void) | null = null
  onTextDelta: ((text: string) => void) | null = null
  onToolCall: ((payload: CodingToolCallPayload) => void) | null = null
  onToolResult: ((payload: CodingToolResultPayload) => void) | null = null
  onFileChange: ((payload: CodingFileChangePayload) => void) | null = null
  onPermissionRequest: ((payload: CodingPermissionRequestPayload) => void) | null = null

  constructor(skillId: string) {
    super()
    this.skillId = skillId
  }

  buildPath(): string {
    return `/api/skills/${this.skillId}/workbench/coding/stream`
  }

  _handleMessage(evt: MessageEvent<string>): void {
    let msg: JsonRecord
    try {
      msg = JSON.parse(evt.data)
    } catch {
      console.warn('[coding-stream] 无法解析消息:', evt.data)
      return
    }
    this._advanceSeqFromMessage(msg)
    switch (msg.type) {
      case 'session_ready':
        {
          const payload: CodingSessionReadyPayload = {
            session_id: msg.session_id,
            model: msg.model,
            tools: msg.tools || [],
            work_dir: msg.work_dir,
            prompt_hash: msg.prompt_hash || '',
            config_dir: msg.config_dir || '',
            runtime_profile: msg.runtime_profile || null,
          }
          this.onSessionReady?.(payload)
          this._fanout('onSessionReady', payload)
        }
        break
      case 'text_delta':
        {
          const text = msg.content || ''
          this.onTextDelta?.(text)
          this.onDelta?.(text)
          this._fanout('onTextDelta', text)
          this._fanout('onDelta', text)
        }
        break
      case 'tool_call':
        {
          const payload: CodingToolCallPayload = {
            id: msg.id,
            tool: msg.tool,
            input: msg.input,
            decision: msg.decision,
            decision_reason: msg.decision_reason,
          }
          this.onToolCall?.(payload)
          this._fanout('onToolCall', payload)
        }
        break
      case 'tool_result':
        {
          const payload: CodingToolResultPayload = {
            id: msg.id,
            is_error: !!msg.is_error,
            content: msg.content,
          }
          this.onToolResult?.(payload)
          this._fanout('onToolResult', payload)
        }
        break
      case 'file_change':
        {
          const payload: CodingFileChangePayload = {
            path: msg.path || msg.file_path || msg.file,
            operation: msg.operation,
            old_string: msg.old_string,
            new_string: msg.new_string,
            snippet_preview: msg.snippet_preview,
            edit_count: msg.edit_count,
            edits_preview: msg.edits_preview,
          }
          this.onFileChange?.(payload)
          this._fanout('onFileChange', payload)
        }
        break
      case 'permission_request':
        {
          const payload: CodingPermissionRequestPayload = {
            request_id: msg.request_id,
            tool: msg.tool,
            input: msg.input,
            policy: msg.policy,
            auto_approved: !!msg.auto_approved,
          }
          this.onPermissionRequest?.(payload)
          this._fanout('onPermissionRequest', payload)
        }
        break
      case 'usage':
        {
          const payload: CodingUsagePayload = {
            input_tokens: msg.input_tokens,
            output_tokens: msg.output_tokens,
            cache_creation_input_tokens: msg.cache_creation_input_tokens,
            cache_read_input_tokens: msg.cache_read_input_tokens,
          }
          this.onUsage?.(payload)
          this._fanout('onUsage', payload)
        }
        break
      case 'done':
        {
          const payload: CodingDonePayload = {
            subtype: msg.subtype,
            is_error: !!msg.is_error,
            duration_ms: msg.duration_ms,
            total_cost_usd: msg.total_cost_usd,
            usage: (msg.usage && typeof msg.usage === 'object') ? msg.usage as CodingUsagePayload : undefined,
            stop_reason: msg.stop_reason,
            git_commit: msg.git_commit,
            git_commit_full: msg.git_commit_full,
            git_commit_error: msg.git_commit_error,
            changed_files: Array.isArray(msg.changed_files) ? msg.changed_files : undefined,
            diff_summary: msg.diff_summary,
          }
          this.onDone?.(payload)
          this._fanout('onDone', payload)
        }
        break
      case 'error':
        this.onError?.({ code: msg.code, error: msg.error })
        this._fanout('onError', { code: msg.code, error: msg.error })
        break
      case 'pong':
      case 'heartbeat':
        break
      default:
        console.warn('[coding-stream] 未知消息类型:', msg.type, msg)
    }
  }

  sendMessage(content: string, { images, mode }: { images?: unknown[]; mode?: string } = {}): void {
    if (!this.connected || this.ws?.readyState !== 1) {
      throw new Error('WebSocket 未连接')
    }
    const msg: JsonRecord = { type: 'user_message', content }
    if (images?.length) msg.images = images
    if (mode) msg.mode = mode
    this.ws.send(JSON.stringify(msg))
  }

  /**
   * 重连 / 刷新后恢复：向后端请求把 fe_seq > last_seq 的 history 事件回放 + 续 live。
   * 不发新 user_message，只是订阅后端现有 session 的剩余输出。
   */
  sendResume(lastSeq: number): void {
    if (!this.connected || this.ws?.readyState !== 1) {
      throw new Error('WebSocket 未连接')
    }
    this.ws.send(JSON.stringify({ type: 'resume', last_seq: lastSeq }))
  }

  respondPermission(requestId: string, behavior: string, { updatedInput, message }: { updatedInput?: unknown; message?: string } = {}): void {
    if (!this.connected || this.ws?.readyState !== 1) {
      throw new Error('WebSocket 未连接')
    }
    const payload: JsonRecord = {
      type: 'permission_response',
      request_id: requestId,
      behavior,
    }
    if (updatedInput !== undefined) payload.updated_input = updatedInput
    if (message !== undefined) payload.message = message
    this.ws.send(JSON.stringify(payload))
  }

  interrupt(): void {
    if (this.connected && this.ws?.readyState === 1) {
      try { this.ws.send(JSON.stringify({ type: 'interrupt' })) } catch { /* ignore */ }
    }
  }
}
