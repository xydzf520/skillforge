import { beforeEach, describe, expect, it, vi } from 'vitest'

const dialogMocks = vi.hoisted(() => ({
  confirm: vi.fn(),
}))

vi.mock('@arco-design/web-vue', () => ({
  Modal: {
    confirm: dialogMocks.confirm,
  },
}))

import { confirmAction, confirmDelete, confirmSensitiveReveal } from '@/utils/confirmDelete'

describe('confirm helpers', () => {
  beforeEach(() => {
    dialogMocks.confirm.mockReset()
  })

  it('uses danger styling for destructive deletes', () => {
    confirmDelete('测试 Skill', vi.fn(), { okText: '删除 Skill', width: 440 })

    expect(dialogMocks.confirm).toHaveBeenCalledTimes(1)
    expect(dialogMocks.confirm).toHaveBeenCalledWith(expect.objectContaining({
      title: '删除确认',
      content: '确定删除「测试 Skill」吗？此操作无法撤销。',
      okText: '删除 Skill',
      cancelText: '取消',
      width: 440,
      okButtonProps: { status: 'danger' },
    }))
  })

  it('uses warning styling for sensitive reveal', () => {
    confirmSensitiveReveal(vi.fn())

    expect(dialogMocks.confirm).toHaveBeenCalledTimes(1)
    expect(dialogMocks.confirm).toHaveBeenCalledWith(expect.objectContaining({
      title: '显示敏感字段',
      okText: '确认显示',
      cancelText: '取消',
      width: 480,
      okButtonProps: { status: 'warning' },
    }))
  })

  it('passes through generic action options', () => {
    confirmAction({
      title: '确认批量下线',
      content: '会跳过非 active Skill。',
      okText: '继续',
      cancelText: '返回',
      okStatus: 'danger',
      width: 520,
      onOk: vi.fn(),
    })

    expect(dialogMocks.confirm).toHaveBeenCalledTimes(1)
    expect(dialogMocks.confirm).toHaveBeenCalledWith(expect.objectContaining({
      title: '确认批量下线',
      content: '会跳过非 active Skill。',
      okText: '继续',
      cancelText: '返回',
      width: 520,
      okButtonProps: { status: 'danger' },
    }))
  })
})
