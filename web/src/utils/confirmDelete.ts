import { Modal } from '@arco-design/web-vue'

/**
 * 统一的二次确认工具。
 * - confirmDelete：破坏性操作（红色删除按钮）
 * - confirmAction：其他需要用户二次确认的重要操作（自定义 okText/按钮样式）
 * - confirmSensitiveReveal：敏感字段显隐等需审计的 warning 操作
 */

type ConfirmStatus = 'normal' | 'success' | 'warning' | 'danger'

type ConfirmDialogParams = {
  title: string
  content: string
  okText?: string
  cancelText?: string
  okStatus?: ConfirmStatus
  width?: number
  onOk: () => void | Promise<void>
  onCancel?: () => void
}

function openConfirm(params: ConfirmDialogParams) {
  Modal.confirm({
    title: params.title,
    content: params.content,
    okText: params.okText ?? '确定',
    cancelText: params.cancelText ?? '取消',
    width: params.width,
    okButtonProps: params.okStatus ? { status: params.okStatus } : undefined,
    onOk: async () => {
      await params.onOk()
    },
    onCancel: params.onCancel,
  })
}

export function confirmDelete(
  name: string,
  onOk: () => void | Promise<void>,
  options: { content?: string; okText?: string; cancelText?: string; width?: number } = {},
) {
  openConfirm({
    title: '删除确认',
    content: options.content ?? `确定删除「${name}」吗？此操作无法撤销。`,
    okText: options.okText ?? '删除',
    cancelText: options.cancelText ?? '取消',
    okStatus: 'danger',
    width: options.width,
    onOk,
  })
}

export function confirmAction(params: ConfirmDialogParams) {
  openConfirm(params)
}

export function confirmSensitiveReveal(
  onOk: () => void | Promise<void>,
  options: { title?: string; content?: string; okText?: string; cancelText?: string; width?: number } = {},
) {
  openConfirm({
    title: options.title ?? '显示敏感字段',
    content: options.content ?? '将按已授权身份读取未脱敏 schema，并写入审计日志。仅在明确需要核对敏感字段时继续。',
    okText: options.okText ?? '确认显示',
    cancelText: options.cancelText ?? '取消',
    okStatus: 'warning',
    width: options.width ?? 480,
    onOk,
  })
}
