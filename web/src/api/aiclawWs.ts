export type AiclawSocketHandlers = {
  onOpen?: (event: Event) => void
  onClose?: (event: CloseEvent) => void
  onError?: (event: Event) => void
  onMessage?: (message: any) => void
}

export function buildAiclawWsUrl(instanceId: string, agentId: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/api/aiclaw/instances/${encodeURIComponent(instanceId)}/chat/${encodeURIComponent(agentId)}/stream`
}

export function createAiclawChatSocket(instanceId: string, agentId: string, handlers: AiclawSocketHandlers = {}): WebSocket {
  const ws = new WebSocket(buildAiclawWsUrl(instanceId, agentId))
  ws.addEventListener('open', (event) => handlers.onOpen?.(event))
  ws.addEventListener('close', (event) => handlers.onClose?.(event))
  ws.addEventListener('error', (event) => handlers.onError?.(event))
  ws.addEventListener('message', (event) => {
    try {
      handlers.onMessage?.(JSON.parse(String(event.data)))
    } catch {
      handlers.onMessage?.({ type: 'raw', payload: event.data })
    }
  })
  return ws
}
