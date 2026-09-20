/**
 * stream-dispatcher.test.ts
 *
 * 覆盖 BaseStreamClient / CodingAgentStreamClient 的多订阅者 dispatcher 模式：
 *   - addSubscriber / removeSubscriber / hasSubscribers
 *   - _fanout：同一事件扇出到所有 subscriber，单个 subscriber 抛异常不影响其它 subscriber
 *   - 同 id 覆盖语义
 *   - lastSeq 优先使用后端 _seq；无 _seq 时非 heartbeat/pong 事件递增 + 所有 subscriber 都收到 onSeqAdvance
 *   - legacy direct-assignment handlers 仍然 fire（向后兼容）
 *   - CodingAgentStreamClient.sendResume：连接 OK 则发 resume，连接未就绪抛错
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { CodingAgentStreamClient, type StreamSubscriber } from '@/api/stream'

type MockWs = {
  readyState: number
  send: ReturnType<typeof vi.fn>
  close: ReturnType<typeof vi.fn>
  onopen: (() => void) | null
  onerror: ((e: Event) => void) | null
  onclose: ((e: CloseEvent) => void) | null
  onmessage: ((e: MessageEvent<string>) => void) | null
}

// ---- 通用辅助 ----

function makeClient(): { client: CodingAgentStreamClient; ws: MockWs } {
  const client = new CodingAgentStreamClient('EC-100')
  const ws: MockWs = {
    readyState: 1, // OPEN
    send: vi.fn(),
    close: vi.fn(),
    onopen: null,
    onerror: null,
    onclose: null,
    onmessage: null,
  }
  // 跳过真实 connect()：直接注入 ws 并标记已连
  client.ws = ws as unknown as WebSocket
  client.connected = true
  return { client, ws }
}

function feed(client: CodingAgentStreamClient, msg: Record<string, unknown>) {
  client._handleMessage({ data: JSON.stringify(msg) } as MessageEvent<string>)
}

function makeSub(id: string, overrides: Partial<StreamSubscriber> = {}): StreamSubscriber {
  return { id, ...overrides }
}

describe('BaseStreamClient: 订阅者管理', () => {
  let client: CodingAgentStreamClient

  beforeEach(() => {
    client = makeClient().client
  })

  afterEach(() => {
    client.close()
  })

  it('addSubscriber 追加后 hasSubscribers 返回 true', () => {
    expect(client.hasSubscribers()).toBe(false)
    client.addSubscriber(makeSub('a'))
    expect(client.hasSubscribers()).toBe(true)
  })

  it('同 id 再 addSubscriber 是"覆盖"而不是重复', () => {
    const first = vi.fn()
    const second = vi.fn()
    client.addSubscriber(makeSub('a', { onTextDelta: first }))
    client.addSubscriber(makeSub('a', { onTextDelta: second }))
    feed(client, { type: 'text_delta', content: 'hi' })
    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledWith('hi')
  })

  it('removeSubscriber 只移除指定 id，其它保留', () => {
    const a = vi.fn()
    const b = vi.fn()
    client.addSubscriber(makeSub('a', { onTextDelta: a }))
    client.addSubscriber(makeSub('b', { onTextDelta: b }))
    client.removeSubscriber('a')
    expect(client.hasSubscribers()).toBe(true)
    feed(client, { type: 'text_delta', content: 'x' })
    expect(a).not.toHaveBeenCalled()
    expect(b).toHaveBeenCalledWith('x')
  })

  it('移除最后一个 subscriber 后 hasSubscribers=false', () => {
    client.addSubscriber(makeSub('a'))
    client.removeSubscriber('a')
    expect(client.hasSubscribers()).toBe(false)
  })
})

describe('BaseStreamClient: _fanout 多订阅者扇出', () => {
  let client: CodingAgentStreamClient

  beforeEach(() => {
    client = makeClient().client
  })

  afterEach(() => {
    client.close()
  })

  it('text_delta 扇出到所有 subscriber', () => {
    const a = vi.fn()
    const b = vi.fn()
    const c = vi.fn()
    client.addSubscriber(makeSub('a', { onTextDelta: a }))
    client.addSubscriber(makeSub('b', { onTextDelta: b }))
    client.addSubscriber(makeSub('c', { onTextDelta: c }))
    feed(client, { type: 'text_delta', content: 'hello' })
    expect(a).toHaveBeenCalledWith('hello')
    expect(b).toHaveBeenCalledWith('hello')
    expect(c).toHaveBeenCalledWith('hello')
  })

  it('单个 subscriber 抛异常不影响其它 subscriber', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const bad = vi.fn(() => { throw new Error('boom') })
    const good1 = vi.fn()
    const good2 = vi.fn()
    client.addSubscriber(makeSub('bad', { onTextDelta: bad }))
    client.addSubscriber(makeSub('g1', { onTextDelta: good1 }))
    client.addSubscriber(makeSub('g2', { onTextDelta: good2 }))
    feed(client, { type: 'text_delta', content: 'x' })
    expect(bad).toHaveBeenCalled()
    expect(good1).toHaveBeenCalledWith('x')
    expect(good2).toHaveBeenCalledWith('x')
    warn.mockRestore()
  })

  it('done / error / session_ready / tool_call / tool_result / file_change / usage 都扇出', () => {
    const events: string[] = []
    const fileChange = vi.fn()
    const usage = vi.fn()
    const sub: StreamSubscriber = {
      id: 'all',
      onSessionReady: () => events.push('session_ready'),
      onToolCall: () => events.push('tool_call'),
      onToolResult: () => events.push('tool_result'),
      onFileChange: (payload) => {
        events.push('file_change')
        fileChange(payload)
      },
      onUsage: (payload) => {
        events.push('usage')
        usage(payload)
      },
      onDone: () => events.push('done'),
      onError: () => events.push('error'),
    }
    client.addSubscriber(sub)
    feed(client, { type: 'session_ready', session_id: 's1', model: 'm', tools: [], work_dir: '/' })
    feed(client, { type: 'tool_call', id: 't1', tool: 'Read', input: {} })
    feed(client, { type: 'tool_result', id: 't1', is_error: false, content: 'ok' })
    feed(client, { type: 'file_change', path: 'a', operation: 'edit' })
    feed(client, { type: 'usage', input_tokens: 11, output_tokens: 7 })
    feed(client, { type: 'done', subtype: 'end', is_error: false })
    feed(client, { type: 'error', code: 'X', error: 'y' })
    expect(events).toEqual([
      'session_ready', 'tool_call', 'tool_result', 'file_change', 'usage', 'done', 'error',
    ])
    expect(fileChange).toHaveBeenCalledWith(expect.objectContaining({ path: 'a' }))
    expect(usage).toHaveBeenCalledWith(expect.objectContaining({ input_tokens: 11, output_tokens: 7 }))
  })

  it('file_change 兼容后端旧字段 file_path', () => {
    const fileChange = vi.fn()
    client.addSubscriber(makeSub('fc', { onFileChange: fileChange }))
    feed(client, { type: 'file_change', file_path: 'scripts/main.py', operation: 'edit' })
    expect(fileChange).toHaveBeenCalledWith(expect.objectContaining({
      path: 'scripts/main.py',
      operation: 'edit',
    }))
  })

  it('removeSubscriber 在 onTextDelta 回调中调用自己不炸（数组快照）', () => {
    const a = vi.fn(() => { client.removeSubscriber('a') })
    const b = vi.fn()
    client.addSubscriber(makeSub('a', { onTextDelta: a }))
    client.addSubscriber(makeSub('b', { onTextDelta: b }))
    feed(client, { type: 'text_delta', content: 'x' })
    // a 自己退订，b 仍应被调用（_fanout 内用的是 subscribers 的快照）
    expect(a).toHaveBeenCalled()
    expect(b).toHaveBeenCalledWith('x')
    expect(client.hasSubscribers()).toBe(true) // 只剩 b
  })
})

describe('BaseStreamClient: lastSeq 与 onSeqAdvance', () => {
  let client: CodingAgentStreamClient

  beforeEach(() => {
    client = makeClient().client
  })

  afterEach(() => {
    client.close()
  })

  it('初始 lastSeq=0', () => {
    expect(client.lastSeq).toBe(0)
  })

  it('每条非 pong 消息 +1', () => {
    feed(client, { type: 'text_delta', content: 'a' })
    expect(client.lastSeq).toBe(1)
    feed(client, { type: 'text_delta', content: 'b' })
    expect(client.lastSeq).toBe(2)
    feed(client, { type: 'tool_call', id: 't1', tool: 'Read', input: {} })
    expect(client.lastSeq).toBe(3)
  })

  it('存在后端 _seq 时以后端游标为准', () => {
    feed(client, { type: 'text_delta', content: 'a', _seq: 10 })
    expect(client.lastSeq).toBe(10)
    feed(client, { type: 'tool_call', id: 't1', tool: 'Read', input: {}, _seq: '11' })
    expect(client.lastSeq).toBe(11)
  })

  it('后端 _seq 可重置本地游标，避免新 session 沿用旧游标', () => {
    feed(client, { type: 'text_delta', content: 'old', _seq: 42 })
    expect(client.lastSeq).toBe(42)
    feed(client, { type: 'session_ready', session_id: 'new', model: 'm', tools: [], work_dir: '/', _seq: 1 })
    expect(client.lastSeq).toBe(1)
  })

  it('pong / heartbeat 不计数', () => {
    feed(client, { type: 'text_delta', content: 'x' })
    feed(client, { type: 'pong' })
    feed(client, { type: 'heartbeat' })
    feed(client, { type: 'pong' })
    feed(client, { type: 'text_delta', content: 'y' })
    expect(client.lastSeq).toBe(2)
  })

  it('onSeqAdvance 广播到所有 subscriber（同步序号）', () => {
    const seqsA: number[] = []
    const seqsB: number[] = []
    client.addSubscriber(makeSub('a', { onSeqAdvance: (n) => seqsA.push(n) }))
    client.addSubscriber(makeSub('b', { onSeqAdvance: (n) => seqsB.push(n) }))
    feed(client, { type: 'text_delta', content: 'x' })
    feed(client, { type: 'tool_call', id: 't1', tool: 'Read', input: {} })
    feed(client, { type: 'heartbeat' })
    feed(client, { type: 'pong' })
    feed(client, { type: 'done', subtype: 'end', is_error: false })
    expect(seqsA).toEqual([1, 2, 3])
    expect(seqsB).toEqual([1, 2, 3])
  })
})

describe('BaseStreamClient: legacy direct-assignment 向后兼容', () => {
  let client: CodingAgentStreamClient

  beforeEach(() => {
    client = makeClient().client
  })

  afterEach(() => {
    client.close()
  })

  it('client.onTextDelta = fn 直接赋值仍然触发', () => {
    const legacy = vi.fn()
    client.onTextDelta = legacy
    feed(client, { type: 'text_delta', content: 'legacy' })
    expect(legacy).toHaveBeenCalledWith('legacy')
  })

  it('legacy onDelta 和 onTextDelta 同时触发', () => {
    const legacyTextDelta = vi.fn()
    const legacyDelta = vi.fn()
    client.onTextDelta = legacyTextDelta
    client.onDelta = legacyDelta
    feed(client, { type: 'text_delta', content: 'x' })
    expect(legacyTextDelta).toHaveBeenCalledWith('x')
    expect(legacyDelta).toHaveBeenCalledWith('x')
  })

  it('legacy 和 subscriber 同时 fire', () => {
    const legacy = vi.fn()
    const sub = vi.fn()
    client.onDone = legacy
    client.addSubscriber(makeSub('s', { onDone: sub }))
    feed(client, {
      type: 'done',
      subtype: 'end',
      is_error: false,
      git_commit: 'deadbeef',
      changed_files: ['SKILL.md'],
      diff_summary: { total_files: 1 },
    })
    expect(legacy).toHaveBeenCalledTimes(1)
    expect(sub).toHaveBeenCalledTimes(1)
    expect(sub.mock.calls[0][0]).toMatchObject({
      git_commit: 'deadbeef',
      changed_files: ['SKILL.md'],
      diff_summary: { total_files: 1 },
    })
  })

  it('done payload 保留耗时费用和 usage 字段', () => {
    const sub = vi.fn()
    client.addSubscriber(makeSub('s', { onDone: sub }))
    feed(client, {
      type: 'done',
      subtype: 'success',
      is_error: false,
      duration_ms: 12345,
      total_cost_usd: 0.0123,
      usage: { input_tokens: 100, output_tokens: 20 },
    })
    expect(sub).toHaveBeenCalledWith(expect.objectContaining({
      duration_ms: 12345,
      total_cost_usd: 0.0123,
      usage: { input_tokens: 100, output_tokens: 20 },
    }))
  })

  it('legacy onSessionReady 赋值仍 fire', () => {
    const legacy = vi.fn()
    client.onSessionReady = legacy
    feed(client, {
      type: 'session_ready', session_id: 'sid', model: 'm',
      tools: ['Read'], work_dir: '/x',
    })
    expect(legacy).toHaveBeenCalledTimes(1)
    expect(legacy.mock.calls[0][0]).toMatchObject({ session_id: 'sid', model: 'm' })
  })
})

describe('CodingAgentStreamClient.sendResume', () => {
  it('连接正常时发送 {type:"resume", last_seq:N}', () => {
    const { client, ws } = makeClient()
    client.sendResume(42)
    expect(ws.send).toHaveBeenCalledTimes(1)
    const sent = JSON.parse((ws.send.mock.calls[0] as [string])[0])
    expect(sent).toEqual({ type: 'resume', last_seq: 42 })
    client.close()
  })

  it('connected=false 时抛错', () => {
    const { client } = makeClient()
    client.connected = false
    expect(() => client.sendResume(5)).toThrow(/未连接/)
    client.close()
  })

  it('ws.readyState !== 1 时抛错', () => {
    const { client, ws } = makeClient()
    ws.readyState = 3 // CLOSED
    expect(() => client.sendResume(5)).toThrow(/未连接/)
    client.close()
  })

  it('sendResume 不会增加 lastSeq（只在收消息时增加）', () => {
    const { client } = makeClient()
    const before = client.lastSeq
    client.sendResume(10)
    expect(client.lastSeq).toBe(before)
    client.close()
  })
})

describe('CodingAgentStreamClient: 消息解析鲁棒性', () => {
  it('无法解析的 JSON 不崩溃且不计数', () => {
    const { client } = makeClient()
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    client._handleMessage({ data: '{not json' } as MessageEvent<string>)
    expect(client.lastSeq).toBe(0)
    warn.mockRestore()
    client.close()
  })

  it('未知 type 仍然递增 lastSeq（只要不是 heartbeat/pong）', () => {
    const { client } = makeClient()
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    feed(client, { type: 'xyz_unknown' })
    expect(client.lastSeq).toBe(1)
    warn.mockRestore()
    client.close()
  })
})
