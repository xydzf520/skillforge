import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const push = vi.fn()
const routeState: any = { params: { id: '8500' }, query: {} }

const todoGet = vi.fn()
const listAssignableUsers = vi.fn()
const decide = vi.fn()
const extendSla = vi.fn()
const updateDraft = vi.fn()
const assignDispatchTask = vi.fn()

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => ({ push }),
}))

vi.mock('@/api', () => ({
  todoApi: {
    get: todoGet,
    listAssignableUsers,
    decide,
    extendSla,
    updateDraft,
    assignDispatchTask,
  },
}))

const SlotStub = {
  template: '<div><slot name="title" /><slot name="icon" /><slot /><slot name="extra" /></div>',
}

const ButtonStub = {
  template: '<button v-bind="$attrs" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>',
}

const commonStubs = {
  'a-spin': SlotStub,
  'a-button': ButtonStub,
  'a-tag': { template: '<span><slot /></span>' },
  'a-select': SlotStub,
  'a-option': SlotStub,
  'a-empty': { template: '<div />' },
  'a-collapse': SlotStub,
  'a-collapse-item': SlotStub,
  'icon-left': { template: '<i />' },
}

describe('TodoDetailV2', () => {
  beforeEach(() => {
    push.mockReset()
    todoGet.mockReset()
    listAssignableUsers.mockReset()
    decide.mockReset()
    extendSla.mockReset()
    updateDraft.mockReset()
    assignDispatchTask.mockReset()
    listAssignableUsers.mockResolvedValue([
      { id: 'u1', name: '运营A', department: '运营部', role: 'operator', dingtalk_bound: true },
    ])
  })

  it('keeps the new management view separated into conclusion, dispatch, diagnosis and evidence blocks', async () => {
    todoGet.mockResolvedValue({
      todo: { id: 8500, status: 'pending', decided_by: null },
      request: { id: 'req-8500', kind: 'dispatch', title: '测试待办', summary: '测试摘要', aggregate_decision: null },
      skill_meta: { id: 'skill-1', name: 'tmall-link-decline-operator-v1' },
      structured: null,
      related_report: null,
      dispatch_tasks: [
        {
          id: 10,
          request_id: 'req-8500',
          content: '检查付费计划：暂停低ROI计划；复核免费搜索：补采核心词排名',
          status: 'awaiting_dispatch',
          executor: null,
          default_executor: null,
          deadline: '2026-05-29T12:00:00Z',
          extra: { item_id: '800729240226', priority: 'P1', data_time: '2026-05-29 07:50', evidence: '付费ROI下滑' },
        },
      ],
      payload: {
        card_type: 'product_decline_decision_card',
        item_id: '800729240226',
        item_title: '示例品牌测试商品',
        data_time: '2026-05-29 07:50',
        priority: 'P1',
        confidence: '中',
        business_action_allowed: true,
        recommended_decision: '建议派发运营处理',
        metric_sections: [
          {
            title: '标准口径：当前24小时 vs 前24小时',
            metrics: [
              { name: '—当前24h—', note: '支付¥2507.9｜件数37｜转化7.28%｜访客481｜加购59' },
              { name: '—对比24h—', note: '支付¥5000.45｜件数67｜转化11.25%｜访客560｜加购88' },
              { name: '—变化率—', status: '下降', note: '支付-49.85%｜件数-44.78%｜转化-35.32%｜访客-14.11%｜加购-32.95%' },
              { name: '免费搜索-访客数', value: '98', status: '下降', delta: '当前24h 98 / 前24h 104' },
              { name: '免费搜索-支付转化率', value: '5.1%', status: '下降', delta: '当前24h 5.1% / 前24h 8.65%' },
              { name: '付费商品汇总实时', value: '商品级付费待补采', status: '昨日同刻已对比', delta: '后台返回聚合值：实时花费金额¥549.88 / ROI 0.741 / CPC 5.9127' },
            ],
          },
        ],
        paid_realtime_compare: {
          period: {
            current_label: '2026-05-28 07:50~2026-05-29 07:50',
            compare_label: '2026-05-27 07:50~2026-05-28 07:50',
          },
          metrics: [
            { key: 'charge', label: '实时花费金额', current_text: '¥549.88', previous_text: '¥837.66', change_text: '-34.35%' },
            { key: 'roi', label: 'ROI', current_text: '0.741', previous_text: '0.9061', change_text: '-18.22%' },
            { key: 'cpc', label: 'CPC', current_text: '¥5.9127', previous_text: '¥7.5465', change_text: '-21.65%' },
          ],
        },
        free_flow_analysis_basis: [
          { label: '搜索访客当前24h环比', value: '-5.77%', status: '下降' },
        ],
        paid_flow_analysis_basis: [
          { label: '关键词推广-ROI环比', value: '-18.22%', status: '下降' },
        ],
        analysis_basis: [
          { dimension: '市场Top300状态', basis: '当前第84，昨日第84', confidence: '中' },
        ],
      },
    })

    const TodoDetailV2 = (await import('@/pages/inbox/TodoDetailV2.vue')).default
    const wrapper = mount(TodoDetailV2, {
      global: {
        stubs: commonStubs,
      },
    })

    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('新版结构')
    expect(wrapper.text()).toContain('管理结论')
    expect(wrapper.text()).toContain('问题在哪')
    expect(wrapper.text()).toContain('待派发任务')
    expect(wrapper.text()).toContain('证据详情')
    expect(wrapper.text()).toContain('整体成交')
    expect(wrapper.text()).toContain('免费搜索')
    expect(wrapper.text()).toContain('付费推广')
    expect(wrapper.text()).toContain('市场/商品侧')
    expect(wrapper.text()).toContain('¥2507.9')
    expect(wrapper.text()).toContain('-49.85%')
    expect(wrapper.text()).toContain('¥549.88')
    expect(wrapper.text()).toContain('¥837.66 / -34.35%')

    await new Promise(resolve => setTimeout(resolve, 0))
    await flushPromises()
    expect(wrapper.text()).toContain('运营A')
  }, 15000)
})
