import { beforeEach, describe, expect, it, vi } from 'vitest'
import { shallowMount } from '@vue/test-utils'

const push = vi.fn()
const replace = vi.fn()
const getReport = vi.fn()
const getUnreadCount = vi.fn()
const overviewMock = vi.fn()
const listReports = vi.fn()
const listReportDesignTemplates = vi.fn()
const markReportsRead = vi.fn()
const listTodos = vi.fn()
const listDriftAlerts = vi.fn()
const trends = vi.fn()
const routeState = {
  params: { id: '10001-0' },
  query: {},
}
const userStore = {
  canViewAll: true,
  isAdmin: true,
  userInfo: { user_id: 'u1' },
}

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => ({ push, replace }),
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => userStore,
}))

vi.mock('@/api', () => ({
  inboxApi: {
    getReport,
    getUnreadCount,
    overview: overviewMock,
    listReports,
    listReportDesignTemplates,
    markReportsRead,
  },
  todoApi: {
    list: listTodos,
    trends,
  },
  driftAlertApi: {
    list: listDriftAlerts,
  },
  authApi: {},
}))

const ButtonStub = {
  template: '<button v-bind="$attrs" @click="$emit(\'click\')"><slot /></button>',
}

const SlotStub = {
  template: '<div v-bind="$attrs"><slot /></div>',
}

const baseReport = {
  id: '10001-0',
  decision_log_id: 10001,
  report_index: 0,
  run_id: 'run-1',
  skill_id: 'EC-投放-01',
  skill_name: '电商投放ROI诊断',
  skill_department: 'EC',
  channel: 'dingtalk_card',
  title: '华南区 ROI 跌破红线',
  summary: '华南区本周 ROI 1.2，建议下调预算。',
  content_markdown: '# 详细分析\n\n- ROI 下滑',
  metrics: [{ label: 'ROI', value: '1.2', trend: 'down', delta: '-15%' }],
  tags: ['告警'],
  trigger_type: 'cron',
  payload: { roi_raw: 1.2 },
  related_todos: [],
  related_todo_count: 1,
  related_pending_request_count: 1,
  created_at: '2026-04-17T09:00:00Z',
}

async function flushAsync() {
  await Promise.resolve()
  await Promise.resolve()
}

describe('inbox helpers', () => {
  it('maps labels and unread state', async () => {
    const { hasUnreadReports, reportChannelLabel, reportTriggerLabel, sanitizeSummaryText } = await import('@/pages/inbox/presentation')

    expect(reportChannelLabel('dingtalk_card')).toBe('钉钉卡片')
    expect(reportTriggerLabel('cron')).toBe('定时触发')
    expect(hasUnreadReports('2026-04-17T09:00:00Z', '2026-04-17T08:00:00Z')).toBe(true)
    expect(hasUnreadReports('2026-04-17T09:00:00Z', '2026-04-17T10:00:00Z')).toBe(false)
    expect(sanitizeSummaryText("{'下滑系数排名': [], '免费流分析结果': [], '评价检查结果': []}")).toBe(
      '执行输出包含 下滑系数排名、免费流分析结果、评价检查结果 等字段，暂无可读摘要',
    )
    expect(sanitizeSummaryText("{'metadata': {'run_mode': 'sandbox_test', 'skill_name': '天猫店铺链接下滑分析'}, '整改建议清单': []}")).toBe(
      '天猫店铺链接下滑分析 · 沙箱运行 · 整改建议清单',
    )
  })

  it('keeps default inbox date range stable within a Beijing calendar day', async () => {
    vi.useFakeTimers()
    try {
      const { defaultInboxDateRange } = await import('@/pages/inbox/presentation')
      vi.setSystemTime(new Date('2026-06-04T01:23:45+08:00'))
      expect(defaultInboxDateRange(7)).toEqual(['2026-05-29T00:00:00', '2026-06-04T23:59:59'])

      vi.setSystemTime(new Date('2026-06-04T23:58:59+08:00'))
      expect(defaultInboxDateRange(7)).toEqual(['2026-05-29T00:00:00', '2026-06-04T23:59:59'])
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('reports tab cacheable default window', () => {
  beforeEach(() => {
    push.mockReset()
    replace.mockReset()
    listReports.mockReset().mockResolvedValue({ total: 0, items: [] })
    routeState.query = {}
  })

  it('requests reports with day-boundary dates instead of current seconds', async () => {
    vi.useFakeTimers()
    try {
      vi.setSystemTime(new Date('2026-06-04T12:34:56+08:00'))
      const ReportsTab = (await import('@/pages/inbox/ReportsTab.vue')).default
      const wrapper = shallowMount(ReportsTab, {
        props: { active: true },
        global: {
          stubs: {
            'a-input': true,
            'a-select': true,
            'a-option': true,
            'a-range-picker': true,
            'a-button': ButtonStub,
            'a-spin': SlotStub,
            'a-pagination': true,
            ReportCard: true,
            SfEmptyState: true,
          },
        },
      })

      await flushAsync()
      expect(listReports).toHaveBeenCalledWith(expect.objectContaining({
        date_from: '2026-05-29T00:00:00',
        date_to: '2026-06-04T23:59:59',
      }))
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('report red dot per-user isolation', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('reads & writes last-seen namespaced by userId', async () => {
    const {
      markReportsLastSeen,
      readReportsLastSeen,
      reportsLastSeenKey,
    } = await import('@/pages/inbox/presentation')

    markReportsLastSeen('user-a', '2026-04-17T09:00:00Z')
    markReportsLastSeen('user-b', '2026-04-17T10:30:00Z')

    expect(readReportsLastSeen('user-a')).toBe('2026-04-17T09:00:00Z')
    expect(readReportsLastSeen('user-b')).toBe('2026-04-17T10:30:00Z')
    expect(readReportsLastSeen('user-a')).not.toBe(readReportsLastSeen('user-b'))

    // key 必须带 user_id 后缀，防止同浏览器多账号串号
    expect(reportsLastSeenKey('user-a')).toBe('sf-inbox-reports-last-seen:user-a')
    expect(reportsLastSeenKey('user-b')).toBe('sf-inbox-reports-last-seen:user-b')
  })

  it('mark user-a does not leak into user-b', async () => {
    const {
      markReportsLastSeen,
      readReportsLastSeen,
    } = await import('@/pages/inbox/presentation')

    markReportsLastSeen('user-b', '2026-04-16T08:00:00Z')
    const beforeB = readReportsLastSeen('user-b')

    markReportsLastSeen('user-a', '2026-04-17T12:00:00Z')

    expect(readReportsLastSeen('user-b')).toBe(beforeB)
    expect(readReportsLastSeen('user-a')).toBe('2026-04-17T12:00:00Z')
  })

  it('falls back to anonymous bucket when userId is empty', async () => {
    const {
      markReportsLastSeen,
      readReportsLastSeen,
      reportsLastSeenKey,
    } = await import('@/pages/inbox/presentation')

    markReportsLastSeen('', '2026-04-17T13:00:00Z')
    expect(reportsLastSeenKey('')).toBe('sf-inbox-reports-last-seen:anonymous')
    expect(readReportsLastSeen('')).toBe('2026-04-17T13:00:00Z')
    // anonymous 桶与具名用户互不干扰
    expect(readReportsLastSeen('user-a')).toBe('')
  })
})

describe('cardId encode/decode', () => {
  it('roundtrips the normal {logId}-{idx} shape', () => {
    const logId = 10001
    const idx = 0
    const raw = `${logId}-${idx}`
    const encoded = encodeURIComponent(raw)
    expect(decodeURIComponent(encoded)).toBe(raw)

    const [decodedLogId, decodedIdx] = decodeURIComponent(encoded).split('-')
    expect(Number(decodedLogId)).toBe(logId)
    expect(Number(decodedIdx)).toBe(idx)
  })

  it('survives a multi-digit idx', () => {
    const raw = '987654321-12'
    expect(decodeURIComponent(encodeURIComponent(raw))).toBe(raw)
  })

  it('defends against accidental special chars in ids', () => {
    // 当前后端以 `{decision_log_id}-{report_index}` 返回 cardId，均为数字；
    // 但若以后切到 UUID 或其他 opaque id，encode 链路应当仍然无损。
    const raw = 'abc def/xyz-0'
    const encoded = encodeURIComponent(raw)
    // 空格与斜杠必须被编码
    expect(encoded).not.toContain(' ')
    expect(encoded).not.toContain('/')
    expect(decodeURIComponent(encoded)).toBe(raw)
  })
})

describe('related report link', () => {
  it('renders and emits open', async () => {
    const RelatedReportLink = (await import('@/pages/inbox/components/RelatedReportLink.vue')).default
    const wrapper = shallowMount(RelatedReportLink, {
      props: {
        report: {
          id: '10001-0',
          decision_log_id: 10001,
          title: '华南区 ROI 跌破红线',
          created_at: '2026-04-17T09:00:00Z',
          summary: '华南区本周 ROI 1.2，建议下调预算。',
        },
      },
      global: {
        stubs: {
          'a-button': ButtonStub,
        },
      },
    })

    expect(wrapper.text()).toContain('上下文报告')
    await wrapper.get('[data-testid="related-report-open"]').trigger('click')
    expect(wrapper.emitted('open')?.[0]).toEqual(['10001-0'])
  })
})

describe('todo card primary metric', () => {
  it('prefers paid amount change over legacy realtime decline indicator', async () => {
    const TodoCard = (await import('@/pages/inbox/TodoCard.vue')).default
    const wrapper = shallowMount(TodoCard, {
      props: {
        item: {
          id: 4455,
          request_id: 'req-4455',
          kind: 'dispatch',
          status: 'pending',
          title: '商品实时下滑待办',
          skill_id: 'tmall-link-decline-analysis-v2',
          decision_mode: 'any_of',
          aggregate_status: 'pending',
          created_at: '2026-05-06T08:00:00Z',
          sla_at: '2026-05-06T10:00:00Z',
          metrics_preview: [],
          payment_change_pct: -12.34,
          payment_change_text: '-12.34%',
          primary_indicator: {
            label: '实时下滑系数',
            value: '-1.1036',
            severity: 'medium',
          },
        } as any,
      },
      global: {
        stubs: {
          'a-tag': SlotStub,
          'a-button': ButtonStub,
          IconBarChart: true,
        },
      },
    })

    const metric = wrapper.get('[data-testid="todo-primary-metric"]')
    expect(metric.text()).toContain('支付金额变化率')
    expect(metric.text()).toContain('-12.34%')
    expect(metric.text()).not.toContain('实时下滑系数')
    expect(metric.text()).not.toContain('-1.1036')
    expect(metric.classes()).toContain('tone-down')
  })


  it('renders all key metrics in dense and regular cards without collapsed count', async () => {
    const TodoCard = (await import('@/pages/inbox/TodoCard.vue')).default
    const item = {
      id: 9100,
      request_id: 'req-9100',
      kind: 'dispatch',
      status: 'pending',
      title: '完整关键指标待办',
      skill_id: 'tmall-link-decline-analysis-v2',
      decision_mode: 'any_of',
      aggregate_status: 'pending',
      created_at: '2026-06-05T07:50:00Z',
      sla_at: '2026-06-05T10:00:00Z',
      payment_change_pct: -12.34,
      payment_change_text: '-12.34%',
      metrics_preview: [
        { label: '免费访客环比', value: '-18.5%', trend: 'down', delta: '下降' },
        { label: '免费转化环比', value: '-14.58%', trend: 'down' },
        { label: '付费访客环比', value: '+6.2%', trend: 'up' },
        { label: '付费转化环比', value: '-3.1%', trend: 'down' },
      ],
    } as any
    const global = {
      stubs: {
        'a-tag': SlotStub,
        'a-button': ButtonStub,
        IconBarChart: true,
      },
    }

    const dense = shallowMount(TodoCard, { props: { item, dense: true }, global })
    const denseText = dense.get('[data-testid="todo-dense-metric-list"]').text()
    for (const expected of ['免费访客环比', '-18.5%', '免费转化环比', '-14.58%', '付费访客环比', '+6.2%', '付费转化环比', '-3.1%']) {
      expect(denseText).toContain(expected)
    }
    expect(dense.text()).not.toContain('+4 指标')
    expect(dense.text()).not.toContain('进入详情查看')

    const regular = shallowMount(TodoCard, { props: { item, dense: false }, global })
    const regularText = regular.get('[data-testid="todo-metric-list"]').text()
    for (const expected of ['免费访客环比', '-18.5%', '免费转化环比', '-14.58%', '付费访客环比', '+6.2%', '付费转化环比', '-3.1%']) {
      expect(regularText).toContain(expected)
    }
    expect(regular.text()).not.toContain('+4 指标')
    expect(regular.text()).not.toContain('进入详情查看')
  })


  it('keeps long metric evidence out of todo list body', async () => {
    const TodoCard = (await import('@/pages/inbox/TodoCard.vue')).default
    const longEvidence = '2026-06-04 12:50~2026-06-05 12:50 访客数2 / 支付买家数0 / 支付转化率0.0% / 支付金额0；2026-06-03 12:50~2026-06-04 12:50 访客数1'
    const item = {
      id: 9101,
      request_id: 'req-9101',
      kind: 'dispatch',
      status: 'pending',
      title: '下滑待办',
      skill_id: 'tmall-link-decline-analysis-v2',
      decision_mode: 'any_of',
      aggregate_status: 'pending',
      created_at: '2026-06-05T07:50:00Z',
      sla_at: '2026-06-05T10:00:00Z',
      metrics_preview: [
        { label: '免费搜索/免费访客环比', value: '+100.0%', trend: 'up', delta: longEvidence },
        { label: '搜索/免费转化环比', value: '空', trend: 'flat', delta: '2026-06-04 0.0% / 2026-06-03 0.0%' },
        { label: '付费访客环比', value: '不可判定', trend: 'flat', delta: '2026-06-04 0 / 2026-06-03 0' },
      ],
    } as any
    const global = {
      stubs: {
        'a-tag': SlotStub,
        'a-button': ButtonStub,
        IconBarChart: true,
      },
    }

    const dense = shallowMount(TodoCard, { props: { item, dense: true }, global })
    expect(dense.get('[data-testid="todo-primary-metric"]').text()).toContain('+100.0%')
    const denseText = dense.get('[data-testid="todo-dense-metric-list"]').text()
    expect(denseText).toContain('搜索/免费转化环比')
    expect(denseText).toContain('-')
    expect(denseText).toContain('付费访客环比')
    expect(denseText).toContain('不可判定')
    expect(dense.text()).not.toContain('访客数2')
    expect(dense.text()).not.toContain('支付买家数0')
    expect(dense.text()).not.toContain('2026-06-04')
    expect(dense.get('[data-testid="todo-primary-metric"]').attributes('title')).toContain('访客数2')

    const regular = shallowMount(TodoCard, { props: { item, dense: false }, global })
    const regularText = regular.get('[data-testid="todo-metric-list"]').text()
    expect(regular.get('[data-testid="todo-primary-metric"]').text()).toContain('+100.0%')
    expect(regularText).toContain('不可判定')
    expect(regular.text()).not.toContain('支付转化率0.0%')
  })

  it('keeps dense key metric values compact without ellipsis CSS', async () => {
    const todoCardCss = (await import('@/pages/inbox/TodoCard.vue?raw')).default as string
    const todosTabCss = (await import('@/pages/inbox/TodosTab.vue?raw')).default as string

    expect(todoCardCss).toContain('.todo-dense-main {\n  flex: 0 1 clamp(180px, 24vw, 360px);')
    expect(todoCardCss).toContain('.todo-dense-metrics {\n  flex: 1 0 360px;')
    expect(todosTabCss).toContain('overflow-x: auto')
    expect(todosTabCss).toContain('.todo-grid-header .col-main {\n  flex: 0 1 clamp(180px, 24vw, 360px);')
    expect(todosTabCss).toContain('.todo-grid-header .col-metric {\n  width: 360px;\n  min-width: 340px;')

    const metricValueBlock = todoCardCss.match(/\.todo-metric-value,[\s\S]*?\.todo-dense-metric-value \{([\s\S]*?)\n\}/)?.[1] || ''
    const metricDeltaBlock = todoCardCss.match(/\.todo-metric-delta,[\s\S]*?\.todo-dense-metric-delta \{([\s\S]*?)\n\}/)?.[1] || ''
    const denseChipBlock = todoCardCss.match(/\.todo-dense-metric-chip \{([\s\S]*?)\n\}/)?.[1] || ''

    expect(metricValueBlock).toContain('white-space: nowrap')
    expect(metricValueBlock).toContain('text-overflow: clip')
    expect(metricValueBlock).not.toContain('text-overflow: ellipsis')

    expect(metricDeltaBlock).toContain('white-space: nowrap')
    expect(metricDeltaBlock).toContain('text-overflow: clip')
    expect(metricDeltaBlock).not.toContain('text-overflow: ellipsis')

    expect(denseChipBlock).toContain('flex-wrap: wrap')
    expect(denseChipBlock).toContain('overflow: visible')
    expect(denseChipBlock).not.toContain('text-overflow: ellipsis')
  })

  it('shows a clear V2 entry without changing the default detail action', async () => {
    const TodoCard = (await import('@/pages/inbox/TodoCard.vue')).default
    const wrapper = shallowMount(TodoCard, {
      props: {
        item: {
          id: 8500,
          request_id: 'req-8500',
          kind: 'dispatch',
          status: 'pending',
          title: '商品实时下滑待办',
          skill_id: 'tmall-link-decline-analysis-v2',
          decision_mode: 'any_of',
          aggregate_status: 'pending',
          created_at: '2026-05-29T07:50:00Z',
          sla_at: '2026-05-29T10:00:00Z',
          metrics_preview: [],
        } as any,
      },
      global: {
        stubs: {
          'a-tag': SlotStub,
          'a-button': ButtonStub,
          IconBarChart: true,
        },
      },
    })

    const buttons = wrapper.findAll('button')
    const v2Button = buttons.find((button) => button.text().includes('新版 V2'))
    const detailButton = buttons.find((button) => button.text() === '详情')
    expect(v2Button?.exists()).toBe(true)
    expect(detailButton?.exists()).toBe(true)

    await v2Button!.trigger('click')
    await detailButton!.trigger('click')

    expect(wrapper.emitted('open-v2')?.[0]).toEqual([8500])
    expect(wrapper.emitted('open')?.[0]).toEqual([8500])
  })
})

describe('InboxCenter unread state (no localStorage fallback)', () => {
  beforeEach(() => {
    getUnreadCount.mockReset()
    overviewMock.mockReset().mockResolvedValue({})
    listReports.mockReset().mockResolvedValue({ items: [] })
    listReportDesignTemplates.mockReset().mockResolvedValue({ items: [] })
    markReportsRead.mockReset().mockResolvedValue({})
    listTodos.mockReset().mockResolvedValue({ items: [], total: 0 })
    listDriftAlerts.mockReset().mockResolvedValue({ items: [], total: 0 })
    trends.mockReset().mockResolvedValue({ points: [] })
    window.localStorage.clear()
    routeState.query = {}
  })

  it('uses server unread count when fetch succeeds, no localStorage read', async () => {
    getUnreadCount.mockResolvedValue({ unread: 3 })
    const lsGet = vi.spyOn(Storage.prototype, 'getItem')

    const InboxCenter = (await import('@/pages/inbox/InboxCenter.vue')).default
    const wrapper = shallowMount(InboxCenter, {
      global: {
        stubs: {
          InboxHeader: true,
          ReportsTab: true,
          TodosTab: true,
          'a-tabs': true,
          'a-tab-pane': true,
        },
      },
    })

    await flushAsync()
    await flushAsync()

    expect(getUnreadCount).toHaveBeenCalled()
    // 不应触碰 localStorage 读取报告未读
    const ranInboxRead = lsGet.mock.calls.some(([k]) => String(k).includes('reports-last-seen'))
    expect(ranInboxRead).toBe(false)

    wrapper.unmount()
    lsGet.mockRestore()
  })

  it('shows unknown state on fetch failure, never reads localStorage', async () => {
    getUnreadCount.mockRejectedValue(new Error('500'))
    const lsGet = vi.spyOn(Storage.prototype, 'getItem')

    const InboxCenter = (await import('@/pages/inbox/InboxCenter.vue')).default
    const wrapper = shallowMount(InboxCenter, {
      global: {
        stubs: {
          InboxHeader: true,
          ReportsTab: true,
          TodosTab: true,
          'a-tabs': true,
          'a-tab-pane': true,
        },
      },
    })

    await flushAsync()
    await flushAsync()

    expect(getUnreadCount).toHaveBeenCalled()
    const ranInboxRead = lsGet.mock.calls.some(([k]) => String(k).includes('reports-last-seen'))
    expect(ranInboxRead).toBe(false)

    wrapper.unmount()
    lsGet.mockRestore()
  })
})

describe('InboxCenter sidebar priority filters', () => {
  beforeEach(() => {
    getUnreadCount.mockReset().mockResolvedValue({ unread: 0 })
    overviewMock.mockReset().mockResolvedValue({})
    listReports.mockReset().mockResolvedValue({ items: [] })
    listReportDesignTemplates.mockReset().mockResolvedValue({ items: [] })
    markReportsRead.mockReset().mockResolvedValue({})
    listTodos.mockReset().mockResolvedValue({ items: [], total: 0 })
    listDriftAlerts.mockReset().mockResolvedValue({ items: [], total: 0 })
    trends.mockReset().mockResolvedValue({ points: [] })
    replace.mockReset()
    routeState.query = {}
  })

  it('renders the P0 sidebar view and writes priority=P0 when selected', async () => {
    const InboxCenter = (await import('@/pages/inbox/InboxCenter.vue')).default
    const wrapper = shallowMount(InboxCenter, {
      global: {
        stubs: {
          InboxHeader: true,
          ReportsTab: true,
          TodosTab: true,
          DriftAlertsTab: true,
          'a-tabs': true,
          'a-tab-pane': true,
        },
      },
    })

    await flushAsync()

    const p0Item = wrapper.findAll('.ai-side-item').find((item) => item.text().includes('P0 紧急'))
    expect(p0Item?.exists()).toBe(true)

    await p0Item!.trigger('click')

    expect(replace).toHaveBeenLastCalledWith({
      path: '/inbox',
      query: {
        tab: 'pending',
        priority: 'P0',
        status: 'pending',
      },
    })

    wrapper.unmount()
  })
})

describe('report detail', () => {
  beforeEach(() => {
    push.mockReset()
    replace.mockReset()
    getReport.mockReset()
    listReportDesignTemplates.mockReset().mockResolvedValue({ items: [] })
    userStore.canViewAll = true
  })

  it('requests debug payload for privileged users and renders the section', async () => {
    getReport.mockResolvedValue({
      ...baseReport,
      debug_context: {
        input_snapshot: { region: '华南' },
        raw_output_result: { reports: [{ title: '华南区 ROI 跌破红线' }] },
      },
    })

    const ReportDetail = (await import('@/pages/inbox/ReportDetail.vue')).default
    const wrapper = shallowMount(ReportDetail, {
      global: {
        stubs: {
          'a-spin': SlotStub,
          'a-card': SlotStub,
          'a-button': ButtonStub,
          'a-tag': SlotStub,
          MetricChip: SlotStub,
          RelatedTodosList: SlotStub,
          IconLeft: true,
        },
      },
    })

    await flushAsync()
    await flushAsync()

    expect(getReport).toHaveBeenCalledWith('10001-0', { include_debug: 1 })
    expect(wrapper.find('[data-testid="report-debug"]').exists()).toBe(true)
  })



  it('lets users choose Open Design report templates and uses persisted/report defaults', async () => {
    listReportDesignTemplates
      .mockResolvedValueOnce({
        items: [
          { id: 'od-design-meta', source_type: 'design_system', name: 'Meta', preview: { colors: ['#0064E0'] } },
        ],
      })
      .mockResolvedValueOnce({
        items: [
          { id: 'od-skill-ui-skills', source_type: 'skill', name: 'ui-skills', preview: { colors: [] } },
        ],
      })
    getReport.mockResolvedValue({
      ...baseReport,
      payload: {
        _report_design: {
          template_id: 'od-design-meta',
          name: 'Meta',
          source_type: 'design_system',
          design_standard: { colors: ['#0064E0'] },
        },
      },
      report_design: { template_id: 'od-design-meta', name: 'Meta' },
    })

    const ReportDetail = (await import('@/pages/inbox/ReportDetail.vue')).default
    const wrapper = shallowMount(ReportDetail, {
      global: {
        stubs: {
          'a-spin': SlotStub,
          'a-card': SlotStub,
          'a-button': ButtonStub,
          'a-tag': SlotStub,
          MetricChip: SlotStub,
          RelatedTodosList: SlotStub,
          IconLeft: true,
        },
      },
    })

    await flushAsync()
    await flushAsync()

    expect(listReportDesignTemplates).toHaveBeenCalledWith({ source_type: 'design_system', page_size: 200 })
    expect(listReportDesignTemplates).toHaveBeenCalledWith({ source_type: 'skill', page_size: 200 })
    expect(wrapper.get('[data-testid="report-design-picker"]').text()).toContain('报告模板')
    expect(wrapper.text()).toContain('设计模板 · Meta')
    expect((wrapper.get('select').element as HTMLSelectElement).value).toBe('od-design-meta')
  })

  it('hides debug context for normal readers', async () => {
    userStore.canViewAll = false
    getReport.mockResolvedValue({
      ...baseReport,
      debug_context: null,
    })

    const ReportDetail = (await import('@/pages/inbox/ReportDetail.vue')).default
    const wrapper = shallowMount(ReportDetail, {
      global: {
        stubs: {
          'a-spin': SlotStub,
          'a-card': SlotStub,
          'a-button': ButtonStub,
          'a-tag': SlotStub,
          MetricChip: SlotStub,
          RelatedTodosList: SlotStub,
          IconLeft: true,
        },
      },
    })

    await flushAsync()
    await flushAsync()

    expect(getReport).toHaveBeenCalledWith('10001-0', undefined)
    expect(wrapper.find('[data-testid="report-debug"]').exists()).toBe(false)
  })
})
