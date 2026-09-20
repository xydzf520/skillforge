import type { IframeMessageMap, ParentMessageMap } from '../types'

type ParentMessageType = keyof ParentMessageMap
type IframeMessageType = keyof IframeMessageMap
type MessageHandler = (payload: unknown) => void

const listeners = new Map<ParentMessageType, Set<MessageHandler>>()

export function onParentMessage<K extends ParentMessageType>(
  type: K,
  handler: (payload: ParentMessageMap[K]) => void,
): () => void {
  if (!listeners.has(type)) listeners.set(type, new Set())
  listeners.get(type)?.add(handler as MessageHandler)
  return () => offParentMessage(type, handler)
}

export function offParentMessage<K extends ParentMessageType>(
  type: K,
  handler: (payload: ParentMessageMap[K]) => void,
): void {
  listeners.get(type)?.delete(handler as MessageHandler)
}

export function sendToParent<K extends IframeMessageType>(type: K, payload: IframeMessageMap[K]): void {
  if (window.parent !== window) {
    // postMessage 本身就做 structured clone, 不需要 JSON round-trip。
    // 之前的 JSON.parse(JSON.stringify(...)) 会吞掉 Date / Map / Set 并在循环引用时
    // 静默变 null, 造成调试困难。保留 try/catch 仅用于日志, 不再改写 payload。
    try {
      window.parent.postMessage({ source: 'playbook-editor', type, payload }, '*')
    } catch (err) {
      console.warn('[playbook-editor] postMessage failed', type, err)
    }
  }
}

window.addEventListener('message', (event) => {
  const data = event.data as { type?: ParentMessageType; payload?: unknown } | null
  if (!data || typeof data !== 'object' || !data.type) return

  listeners.get(data.type)?.forEach((handler) => {
    handler(data.payload)
  })
})
