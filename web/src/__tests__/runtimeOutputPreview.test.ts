import { describe, expect, it } from 'vitest'

import { normalizeRuntimeOutputPreview } from '@/utils/runtimeOutputPreview'

describe('runtimeOutputPreview', () => {
  it('extracts todos and reports from main.py stdout text', () => {
    const preview = normalizeRuntimeOutputPreview({
      stdout: 'running main.py\n{"output":{"todos":[{"kind":"dispatch","title":"跟进异常链接","summary":"销量连续下滑","reviewer_role":"运营主管","tasks":[{"executor":"店铺运营","content":"核查主图和价格"}]}],"reports":[{"channel":"inbox","title":"日报","summary":"发现 1 个异常"}]}}',
    })

    expect(preview.todos).toHaveLength(1)
    expect(preview.todos[0].title).toBe('跟进异常链接')
    expect(preview.todos[0].tasks?.[0].content).toBe('核查主图和价格')
    expect(preview.reports[0].title).toBe('日报')
  })

  it('extracts tool_result JSON payloads', () => {
    const preview = normalizeRuntimeOutputPreview({
      type: 'tool_result',
      content: JSON.stringify({
        output: {
          todos: [{ kind: 'review', title: '确认销售口径', reviewers: ['zhangsan'] }],
          reports: [{ channel: 'dingtalk_card', title: '日报', summary: '已进入收件-报告' }],
        },
      }),
    })

    expect(preview.todos).toHaveLength(1)
    expect(preview.todos[0].kind).toBe('review')
    expect(preview.todos[0].reviewers).toEqual(['zhangsan'])
    expect(preview.reports).toHaveLength(1)
    expect(preview.reports[0].summary).toContain('收件-报告')
  })

  it('extracts nested output wrappers', () => {
    const preview = normalizeRuntimeOutputPreview({
      result: {
        data: {
          output: {
            todos: [{ kind: 'dispatch', title: '派发整改', tasks: [{ content: '补齐商品图' }] }],
            reports: [{ channel: 'email', title: '周报', summary: '会进入收件-报告' }],
          },
        },
      },
    })

    expect(preview.todos).toHaveLength(1)
    expect(preview.todos[0].tasks?.[0].content).toBe('补齐商品图')
    expect(preview.reports).toHaveLength(1)
    expect(preview.reports[0].title).toBe('周报')
  })
})
