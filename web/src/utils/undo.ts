/**
 * undoableAction — 通用"延迟执行 + 可撤销"工具
 *
 * 典型用法（列表删除）：
 *   const handle = undoableAction({
 *     execute: () => skillApi.delete(skillId),        // 真正的副作用，延迟后执行
 *     undoLabel: `已删除 ${name}`,                    // Toast 主文案
 *     onUndo: () => { skills.value.splice(idx, 0, snapshot) },  // 撤回时恢复 UI
 *     delay: 5000,                                     // 毫秒；默认 5000
 *   })
 *   // handle.cancel() 可以主动撤销
 *   // handle.flush() 立刻执行
 *
 * 页面一般不需要手动调 cancel/flush —— Message 上的"撤回"按钮会触发 onUndo + cancel。
 */
import { Message } from '@arco-design/web-vue'
import { h } from 'vue'

type UndoableActionOptions = {
  execute?: (() => Promise<unknown> | unknown) | null
  undoLabel?: string
  onUndo?: (() => void) | null
  delay?: number
  onSuccess?: ((result: Record<string, unknown>) => void) | null
  onError?: ((error: Record<string, unknown>) => void) | null
}

type UndoableActionHandle = {
  cancel: () => boolean
  flush: () => Promise<void>
}

export function undoableAction({
  execute,
  undoLabel = '操作已执行',
  onUndo = null,
  delay = 5000,
  onSuccess = null,
  onError = null,
}: UndoableActionOptions = {}): UndoableActionHandle {
  let cancelled = false
  let fired = false

  // Arco Message 的 close 句柄
  let messageHandle: { close?: () => void } | null = null

  const fire = async () => {
    if (cancelled || fired) return
    fired = true
    try {
      const r = await execute?.()
      if (onSuccess) onSuccess((r ?? {}) as Record<string, unknown>)
    } catch (e: any) {
      if (onError) onError(e)
      else Message.error(e?._message || '操作失败')
    } finally {
      if (messageHandle?.close) {
        try { messageHandle.close() } catch {}
      }
    }
  }

  const cancel = (): boolean => {
    if (fired) return false
    cancelled = true
    clearTimeout(timer)
    if (onUndo) {
      try { onUndo() } catch {}
    }
    if (messageHandle?.close) {
      try { messageHandle.close() } catch {}
    }
    return true
  }

  const flush = async (): Promise<void> => {
    clearTimeout(timer)
    await fire()
  }

  // 使用 Arco Message.info 的 JSX content：包含"撤回"按钮
  messageHandle = Message.info({
    content: () => h('span', { style: 'display:inline-flex;align-items:center;gap:12px' }, [
      h('span', undoLabel),
      h('a', {
        style: 'color:rgb(var(--primary-6));font-weight:600;cursor:pointer;text-decoration:underline',
        onClick: (e) => { e.preventDefault(); cancel() },
      }, '撤回'),
    ]),
    duration: delay,
    closable: true,
  })

  const timer = setTimeout(fire, delay)

  return { cancel, flush }
}

export default undoableAction
