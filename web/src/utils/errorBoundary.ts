/**
 * 错误处理小工具：统一 `.catch(() => {})` / `Message.error(e)` 两种散落写法。
 *
 * 目的：
 *   - 代替 `.catch(() => {})` 吞异常的反模式——至少把错误打印到 console 方便定位
 *   - 统一 Message.error 拼接业务 context 文案，避免大家写法不一
 *
 * 用法：
 *   import { silentWarn, showError } from '@/utils/errorBoundary'
 *
 *   // 非关键后台动作：失败不打扰用户，但控制台要能查
 *   triggerCoachEvent('branch_changed').catch((e) => silentWarn(e, 'coach.branch_changed'))
 *
 *   // 用户主动操作失败：弹 Message 明确告知
 *   try { await skillApi.save(...) } catch (e) { showError(e, '保存 Skill') }
 */
import { Message } from '@arco-design/web-vue'

type AnyError = {
  _message?: string
  _backendMessage?: string
  _backendCode?: string
  response?: { status?: number; data?: { error?: { message?: string } } }
  message?: string
} | unknown

function extractMessage(e: AnyError): string {
  if (!e || typeof e !== 'object') return ''
  const err = e as Record<string, any>
  return (
    err._message ||
    err._backendMessage ||
    err?.response?.data?.error?.message ||
    err.message ||
    ''
  )
}

/**
 * 非关键失败：控制台打印便于排查，但不弹用户消息。
 * 用途：后台 telemetry 上报、旁路 chat 补发、guardian 自愈、draft 保存等。
 */
export function silentWarn(err: AnyError, context?: string): void {
  const msg = extractMessage(err)
  if (context) {
    // eslint-disable-next-line no-console
    console.warn(`[${context}]`, msg || err)
  } else {
    // eslint-disable-next-line no-console
    console.warn(err)
  }
}

/**
 * 用户主动操作失败：弹 Message.error，拼 context 让用户知道失败的是哪一步。
 * 用途：保存、发布、执行、fork 等用户直接触发的动作。
 */
export function showError(err: AnyError, context = '操作'): void {
  const msg = extractMessage(err)
  Message.error(msg ? `${context}失败：${msg}` : `${context}失败`)
}
