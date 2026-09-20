import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi, beforeEach } from 'vitest'

const push = vi.fn()
const replace = vi.fn()
const routeState: any = { params: { id: '3750' }, query: {} }

const todoGet = vi.fn()
const relatedTimeline = vi.fn()
const listAssignableUsers = vi.fn()

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => ({ push, replace }),
}))

vi.mock('@/stores/user', () => ({
  useUserStore: () => ({
    isAdmin: true,
    userInfo: { user_id: 'u1' },
  }),
}))

vi.mock('@/api', () => ({
  todoApi: {
    get: todoGet,
    relatedTimeline,
    listAssignableUsers,
  },
}))

vi.mock('@/pages/inbox/components/RelatedReportLink.vue', () => ({
  default: { template: '<div />' },
}))

const SlotStub = {
  template: '<div><slot name="title" /><slot /><slot name="extra" /></div>',
}

const ButtonStub = {
  template: '<button v-bind="$attrs" @click="$emit(\'click\')"><slot /></button>',
}

const commonStubs = {
  'a-spin': SlotStub,
  'a-card': SlotStub,
  'a-button': ButtonStub,
  'a-tag': { template: '<span><slot /></span>' },
  'a-space': SlotStub,
  'a-collapse': SlotStub,
  'a-collapse-item': SlotStub,
  'a-descriptions': SlotStub,
  'a-descriptions-item': SlotStub,
  'a-textarea': { template: '<textarea />' },
  'a-input': { template: '<input />' },
  'a-table': SlotStub,
  'a-table-column': SlotStub,
  'a-select': SlotStub,
  'a-option': SlotStub,
  'icon-left': { template: '<i />' },
}

describe('TodoDetail standard compare tiles', () => {
  beforeEach(() => {
    push.mockReset()
    replace.mockReset()
    todoGet.mockReset()
    relatedTimeline.mockReset()
    listAssignableUsers.mockReset()
    relatedTimeline.mockResolvedValue({ items: [] })
    listAssignableUsers.mockResolvedValue([])
  })

  it('promotes free search facts into a fact strip and paid compare rows into tiles', async () => {
    todoGet.mockResolvedValue({
      todo: { id: 3750, status: 'approved', decided_by: null },
      request: { id: 99, kind: 'review', title: '测试待办', summary: '测试摘要', aggregate_decision: 'approved' },
      skill_meta: null,
      structured: null,
      dispatch_tasks: [],
      related_report: null,
      payload: {
        card_type: 'product_decline_decision_card',
        item_id: '835103629624',
        item_title: '示例品牌测试商品',
        data_time: '2026-04-29 10:52',
        recommended_decision: '建议继续观察',
        metric_sections: [
          {
            title: '标准口径：当前24小时 vs 前24小时',
            metrics: [
              { name: '—当前24h—', note: '支付¥100｜件数10｜转化5.0%｜访客200｜加购20' },
              { name: '—对比24h—', note: '支付¥120｜件数12｜转化5.5%｜访客210｜加购24' },
              { name: '—变化率—', status: '下降', note: '支付-16.7%｜件数-16.7%｜转化-9.1%｜访客-4.8%｜加购-16.7%' },
              {
                name: '免费搜索/免费访客环比',
                value: '-25.0%',
                status: '下降',
                delta: '当前24h 访客数90 / 支付买家数5 / 支付转化率5.0% / 支付金额¥456.78；前24h 访客数120 / 支付买家数12 / 支付转化率10.0% / 支付金额¥900',
              },
              {
                name: '搜索/免费转化环比',
                value: '-50.0%',
                status: '下降',
                delta: '当前24h 访客数90 / 支付买家数5 / 支付转化率5.0% / 支付金额¥456.78；前24h 访客数120 / 支付买家数12 / 支付转化率10.0% / 支付金额¥900',
              },
              { name: '免费搜索-访客数', value: '90', status: '下降', delta: '当前24h 90 / 前24h 120' },
              { name: '免费搜索-支付买家数', value: '5', status: '下降', delta: '当前24h 5 / 前24h 12' },
              { name: '免费搜索-支付转化率', value: '5.0%', status: '下降', delta: '当前24h 5.0% / 前24h 10.0%' },
              { name: '免费搜索-支付金额', value: '¥456.78', status: '下降', delta: '当前24h ¥456.78 / 前24h ¥900' },
              { name: '付费访客环比', value: '0.0%', status: '正常', delta: '2026-05-28 07:50~2026-05-29 07:50 100 / 2026-05-27 07:50~2026-05-28 07:50 100' },
              { name: '付费转化环比', value: '0.0%', status: '正常', delta: '2026-05-28 07:50~2026-05-29 07:50 2.0% / 2026-05-27 07:50~2026-05-28 07:50 2.0%' },
              { name: '市场Top300状态', value: '未进Top300', status: 'Top300覆盖不足', delta: '今日Top300覆盖不足，不能判定是否在榜' },
              {
                name: '付费商品汇总实时',
                value: '商品级付费待补采',
                status: '付费数据疑似聚合口径，商品级待补采',
                delta: '后台返回聚合值：实时花费金额¥1596.14 / ROI 1.019 / CPC 7.7483',
              },
            ],
          },
        ],
        paid_realtime_compare: {
          source: 'local_snapshot',
          period: {
            current_label: '2026-05-25 07:50~2026-05-26 07:50',
            compare_label: '2026-05-24 07:50~2026-05-25 07:50',
          },
          metrics: [
            { key: 'charge', label: '实时花费金额', current_text: '¥1596.14', previous_text: '¥1922.57', change_text: '-16.98%' },
            { key: 'roi', label: 'ROI', current_text: '1.019', previous_text: '0.4685', change_text: '+117.50%' },
            { key: 'cpc', label: 'CPC', current_text: '¥7.7483', previous_text: '¥8.1811', change_text: '-5.29%' },
          ],
        },
      },
    })

    const TodoDetail = (await import('@/pages/inbox/TodoDetail.vue')).default
    const wrapper = mount(TodoDetail, {
      global: {
        stubs: commonStubs,
      },
    })

    await flushPromises()
    await flushPromises()

    const promotedTiles = wrapper.findAll('.promoted-metric-tile')
    expect(promotedTiles).toHaveLength(4)
    expect(promotedTiles[0].text()).toContain('免费搜索/免费访客环比')
    expect(promotedTiles[0].text()).toContain('-25.0%')
    expect(promotedTiles[0].text()).toContain('当前24h')
    expect(promotedTiles[0].text()).toContain('前24h')
    expect(promotedTiles[0].text()).toContain('买家5')
    expect(promotedTiles[0].text()).toContain('转化5.0%')
    expect(promotedTiles[0].text()).toContain('金额¥456.78')
    expect(promotedTiles[0].text()).not.toContain('访客数90 / 支付买家数5')
    expect(promotedTiles[1].text()).toContain('搜索/免费转化环比')
    expect(promotedTiles[1].text()).toContain('-50.0%')
    expect(promotedTiles[2].text()).toContain('付费访客环比')
    expect(promotedTiles[2].text()).toContain('0.0%')
    expect(promotedTiles[2].text()).toContain('当前24h')
    expect(promotedTiles[2].text()).toContain('前24h')
    expect(promotedTiles[2].text()).toContain('访客100')
    expect(promotedTiles[3].text()).toContain('付费转化环比')
    expect(promotedTiles[3].text()).toContain('0.0%')
    expect(promotedTiles[3].text()).toContain('转化2.0%')

    const factStrip = wrapper.find('.free-search-fact-strip')
    expect(factStrip.exists()).toBe(true)
    expect(factStrip.text()).toContain('免费搜索实时事实')
    expect(factStrip.text()).toContain('访客90')
    expect(factStrip.text()).toContain('买家5')
    expect(factStrip.text()).toContain('转化率5.0%')
    expect(factStrip.text()).toContain('支付金额¥456.78')
    expect(factStrip.text()).toContain('前24h 已采集')

    const extraRows = wrapper.findAll('.row-extra')
    const extraText = extraRows.map((row) => row.text()).join(' | ')
    expect(extraText).toContain('市场Top300状态')
    expect(extraText).toContain('付费商品汇总实时')
    expect(extraText).toContain('2026-05-25 07:50~2026-05-26 07:50 / 2026-05-24 07:50~2026-05-25 07:50')
    expect(extraText).toContain('实时花费金额¥1596.14 / ¥1922.57-16.98%')
    expect(extraText).toContain('ROI1.019 / 0.4685+117.50%')
    expect(extraText).not.toContain('后台返回聚合值')
    expect(extraText).not.toContain('免费搜索/免费访客环比')
    expect(extraText).not.toContain('搜索/免费转化环比')
    expect(extraText).not.toContain('免费搜索-支付金额')
    expect(extraText).not.toContain('付费访客环比')
    expect(extraText).not.toContain('付费转化环比')
  }, 15000)

  it('renders SampleBrand low-consumption todo as a deduplicated manager view', async () => {
    todoGet.mockResolvedValue({
      todo: { id: 11562, status: 'approved', decided_by: null },
      request: {
        id: 'req-samplebrand-video',
        kind: 'dispatch',
        skill_id: 'samplebrand-video-low-consumption-operator-v1',
        title: 'P1｜云视频账号 梁稳丽 2026-06-14 同主题低消耗视频改进',
        summary: '云视频账号 梁稳丽 有 1 条低消耗视频通过视觉证据门槛。',
        aggregate_status: 'completed',
        aggregate_decision: 'approved',
      },
      skill_meta: { id: 'samplebrand-video-low-consumption-operator-v1', name: '低消耗视频分析' },
      structured: null,
      related_report: null,
      dispatch_tasks: [
        {
          id: 11340,
          request_id: 'req-samplebrand-video',
          content: '# 云视频账号 梁稳丽 低消耗视频详细分析\n- 低消耗画面证据：这段完整 Markdown 不应直接展示。',
          status: 'sent',
          executor: 'exec-1',
          deadline: null,
          extra: { account_name: '梁稳丽', required_output: '提交改版视频方向、复用的高质量样本、复测指标。' },
        },
      ],
      payload: {
        card_type: 'video_low_consumption_decision_card',
        analysis_schema: 'samplebrand_video_low_consumption_daily_operator_v1',
        priority: 'P1',
        account_name: '梁稳丽',
        analysis_date: '2026-06-14',
        data_quality: '有完整视频证据',
        business_action_allowed: true,
        recommended_decision: '梁稳丽账号有1条低消耗视频建议关注，Agent 分型：成交承接优化1条，建议补价格锚点。',
        key_metrics: [
          { name: '低消耗视频数', value: '1', status: '建议关注', delta: '账号最多 3 条' },
          { name: 'Agent 分型', value: '成交承接优化1条', status: '已结构化' },
        ],
        detail_markdown: '# 云视频账号 梁稳丽 低消耗视频详细分析\n- 低消耗画面证据：这段不应铺开。',
        operation_actions: [
          {
            section: '建议视频 1',
            scenario: '示例企业一组_20260612-LWL-LWL_战甲AA_测试视频_02',
            priority: 'P1',
            dimension: '成交承接优化',
            topic_key: '战甲三合一｜其他',
            theme_name: '测试视频',
            editor_code: 'LWL',
            product_code: '战甲AA',
            benchmark_label: '同主题高消耗参考',
            benchmark_editor_code: 'MJL',
            video_id: '101571379',
            root_causes: ['分型：成交承接优化。当前素材基础信号不差，优先补价格锚点。'],
            actions: [
              '建议补成交承接：在可见画面里加价格锚点、优惠理由、适用场景和明确下单引导。',
              '按视觉模型建议微调：优化首帧视觉。',
              '复盘输出建议写清楚：这是放量问题还是承接问题。',
            ],
            qianchuan_lifecycle_value_points: {
              manager_value: '高消耗参考整体点击 43719、峰值 01s，可判断当前是成交承接弱。',
              consumer_value: '消费者在01s附近被适用场景和价格理由触发点击。',
              optimization_focus: '围绕适用场景、价格理由和CTA做轻改。',
            },
            low_visual_evidence: '商品露出晚。',
            benchmark_visual_evidence: '卖点清晰。',
          },
        ],
        chart_sections: [
          {
            type: 'segmented_bar',
            key: 'issue_mix',
            title: '问题分型分布',
            description: '管理者先看问题集中在哪类。',
            items: [{ key: 'offer_conversion', label: '成交承接优化', value: 1 }],
          },
          {
            type: 'segmented_bar',
            key: 'benchmark_mix',
            title: '对标质量',
            description: '同主题高消耗对标可直接解释低消耗原因。',
            items: [{ key: 'same_theme_high_cost', label: '同主题高消耗对标', value: 1 }],
          },
          {
            type: 'bar_compare',
            key: 'video_metric_compare_1',
            title: '1. 低消耗 vs 高消耗指标',
            description: '当前视频对比高消耗参考。',
            items: [
              { key: 'cost', label: '消耗', unit: '¥', low: 320, benchmark: 12000 },
              { key: 'roi', label: 'ROI', low: 1.1, benchmark: 1.8 },
            ],
          },
        ],
        data_quality_items: [
          { dimension: '投放承接数据', issue: '预算、出价、人群包未完整返回。' },
        ],
        forbidden_actions: ['不要跨主题复用高消耗视频脚本。'],
      },
    })

    const TodoDetail = (await import('@/pages/inbox/TodoDetail.vue')).default
    const wrapper = mount(TodoDetail, {
      global: {
        stubs: commonStubs,
      },
    })

    await flushPromises()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('处理结论')
    expect(text).toContain('视频与对标')
    expect(text).toContain('数据图表')
    expect(text).toContain('问题分型分布')
    expect(text).toContain('成交承接优化')
    expect(text).toContain('对标质量')
    expect(text).toContain('同主题高消耗对标')
    expect(text).toContain('低消耗 vs 高消耗指标')
    expect(text).toContain('¥320')
    expect(text).toContain('¥12,000')
    expect(text).toContain('为什么这样处理')
    expect(text).toContain('高点击依据')
    expect(text).toContain('建议动作')
    expect(text).toContain('视觉证据')
    expect(text).toContain('复测与风险边界')
    expect(text).toContain('高消耗参考整体点击 43719')
    expect(text).toContain('消费者在01s附近被适用场景和价格理由触发点击')
    expect(text).toContain('提交改版视频方向、复用的高质量样本、复测指标')
    expect(text).not.toContain('详细分析')
    expect(text).not.toContain('逐视频建议')
    expect(text).not.toContain('完整 Markdown 不应直接展示')
    expect(text).not.toContain('复盘输出建议')
    expect(text.match(/补价格锚点/g)?.length || 0).toBe(1)
    expect(text).toContain('主题测试视频')
    expect(text).toContain('剪辑人LWL')
    expect(text).toContain('参考等级同主题高消耗参考')
    expect(text.match(/商品露出晚。/g)?.length || 0).toBe(1)
    expect(text.match(/卖点清晰。/g)?.length || 0).toBe(1)
  }, 15000)

  it('does not render all-zero paid plan rows as valid plan detail', async () => {
    todoGet.mockResolvedValue({
      todo: { id: 3751, status: 'approved', decided_by: null },
      request: { id: 100, kind: 'dispatch', title: '测试待办', summary: '测试摘要', aggregate_decision: 'approved' },
      skill_meta: null,
      structured: null,
      dispatch_tasks: [],
      related_report: null,
      payload: {
        card_type: 'product_decline_decision_card',
        item_id: '800494291181',
        item_title: '示例品牌测试商品',
        paid_flow_analysis_basis: [
          {
            label: '关键词推广-商品汇总实时花费/直接ROI/CPC',
            value: '实时花费金额 ¥13356.2 / 实时直接ROI 1.9286 / 直接ROI环比 +13.74%；实时CPC ¥10.25 / CPC环比 +6.46%',
            status: '已采集计划列表，计划级花费/ROI/CPC待补采；关键词/创意级明细待补采',
            detail: '实时花费金额 ¥13356.2 / 实时直接ROI 1.9286 / 直接ROI环比 +13.74%；实时CPC ¥10.25 / CPC环比 +6.46%',
          },
          {
            label: '关键词推广-计划明细底部合计',
            value: '商品级计划待补采',
            status: '不能按0值判定低ROI/高PPC为0',
            detail: '已采集计划列表，计划级实时花费/ROI/CPC明细待补采',
          },
        ],
        decision_context: {
          paid_detail: {
            paid_detail_status: '已采集计划列表，计划级花费/ROI/CPC待补采；关键词/创意级明细待补采',
            budget_status: '未发现计划状态异常；预算字段需后台复核',
            plan_details: [
              { plan_id: 1, plan_name: '持久三合一', charge: null, roi: null, ppc: null, status: 1, diagnosis: '' },
              { plan_id: 2, plan_name: '趋势明星', charge: 0, roi: 0, ppc: 0, status: null, diagnosis: '' },
            ],
          },
        },
      },
    })

    const TodoDetail = (await import('@/pages/inbox/TodoDetail.vue')).default
    const wrapper = mount(TodoDetail, {
      global: {
        stubs: commonStubs,
      },
    })

    await flushPromises()
    await flushPromises()

    expect(wrapper.find('.mini-table').exists()).toBe(false)
    const gapNote = wrapper.find('.gap-note')
    expect(gapNote.exists()).toBe(true)
    expect(gapNote.text()).toContain('计划级花费/ROI/CPC待补采')
    expect(wrapper.text()).not.toContain('持久三合一¥00¥01')
  })

  it('renders local free search and paid realtime snapshot compares', async () => {
    todoGet.mockResolvedValue({
      todo: { id: 3753, status: 'approved', decided_by: null },
      request: { id: 102, kind: 'dispatch', title: '测试待办', summary: '测试摘要', aggregate_decision: 'approved' },
      skill_meta: null,
      structured: null,
      dispatch_tasks: [],
      related_report: null,
      payload: {
        card_type: 'product_decline_decision_card',
        item_id: '800559674590',
        item_title: '示例品牌测试商品',
        free_search_compare: {
          source: 'local_snapshot',
          period: {
            current_label: '2026-05-25 07:50~2026-05-26 07:50',
            compare_label: '2026-05-24 07:50~2026-05-25 07:50',
          },
          metrics: [
            { key: 'visitor', label: '免费搜索访客', current_text: '34', previous_text: '39', change_text: '-12.82%' },
            { key: 'conversion', label: '免费搜索转化率', current_text: '5.88%', previous_text: '7.69%', change_text: '-23.53%' },
          ],
        },
        paid_realtime_compare: {
          source: 'local_snapshot',
          period: {
            current_label: '2026-05-25 07:50~2026-05-26 07:50',
            compare_label: '2026-05-24 07:50~2026-05-25 07:50',
          },
          metrics: [
            { key: 'charge', label: '实时花费金额', current_text: '¥1596.14', previous_text: '¥1922.57', change_text: '-16.98%' },
            { key: 'roi', label: 'ROI', current_text: '1.019', previous_text: '0.4685', change_text: '+117.50%' },
            { key: 'cpc', label: 'CPC', current_text: '¥7.7483', previous_text: '¥8.1811', change_text: '-5.29%' },
          ],
        },
      },
    })

    const TodoDetail = (await import('@/pages/inbox/TodoDetail.vue')).default
    const wrapper = mount(TodoDetail, {
      global: {
        stubs: commonStubs,
      },
    })

    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('免费搜索昨日同刻对比')
    expect(wrapper.text()).toContain('免费搜索访客')
    expect(wrapper.text()).toContain('34 vs 39')
    expect(wrapper.text()).toContain('-12.82%')
    expect(wrapper.text()).toContain('免费搜索转化率')
    expect(wrapper.text()).toContain('5.88% vs 7.69%')
    expect(wrapper.text()).toContain('付费商品汇总实时')
    expect(wrapper.text()).toContain('¥1596.14')
    expect(wrapper.text()).toContain('¥1922.57')
    expect(wrapper.text()).toContain('1.019')
    expect(wrapper.text()).toContain('0.4685')
    expect(wrapper.text()).toContain('¥7.7483')
    expect(wrapper.text()).toContain('¥8.1811')
  })

  it('builds paid realtime compare rows from paid detail summary when explicit compare is absent', async () => {
    todoGet.mockResolvedValue({
      todo: { id: 8439, status: 'pending', decided_by: null },
      request: { id: 103, kind: 'dispatch', title: '测试待办', summary: '测试摘要', aggregate_decision: null },
      skill_meta: null,
      structured: null,
      dispatch_tasks: [],
      related_report: null,
      payload: {
        card_type: 'product_decline_decision_card',
        item_id: '800729240226',
        item_title: '示例品牌测试商品',
        paid_flow_analysis_basis: [
          {
            label: '关键词推广-商品汇总实时花费/直接ROI/CPC',
            value: '商品级付费待补采',
            detail: '后台返回聚合值：实时花费金额¥325.38 / ROI 1.1052 / CPC 5.8104',
            period: {
              current_label: '2026-05-28 07:50~2026-05-29 07:50',
              compare_label: '2026-05-27 07:50~2026-05-28 07:50',
            },
            source: 'tmall_item_promotion_required_metrics',
            status: '付费数据疑似聚合口径，商品级待补采',
          },
        ],
        decision_context: {
          paid_detail: {
            product_summary: {
              charge: 325.38,
              charge_prev: 2662.15,
              roi: 1.1052,
              roi_delta: 0.1772,
              cpc: 5.8104,
              cpc_delta: -2.3557,
              charge_change_pct: -87.78,
              roi_change_pct: 19.09,
              cpc_change_pct: -28.85,
            },
          },
        },
      },
    })

    const TodoDetail = (await import('@/pages/inbox/TodoDetail.vue')).default
    const wrapper = mount(TodoDetail, {
      global: {
        stubs: commonStubs,
      },
    })

    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('付费商品汇总实时')
    expect(wrapper.text()).toContain('2026-05-28 07:50~2026-05-29 07:50 / 2026-05-27 07:50~2026-05-28 07:50')
    expect(wrapper.text()).toContain('实时花费金额')
    expect(wrapper.text()).toContain('¥325.38')
    expect(wrapper.text()).toContain('¥2,662.15')
    expect(wrapper.text()).toContain('-87.78%')
    expect(wrapper.text()).toContain('ROI')
    expect(wrapper.text()).toContain('1.1052')
    expect(wrapper.text()).toContain('0.928')
    expect(wrapper.text()).toContain('+19.09%')
    expect(wrapper.text()).toContain('CPC')
    expect(wrapper.text()).toContain('¥5.81')
    expect(wrapper.text()).toContain('¥8.17')
    expect(wrapper.text()).toContain('-28.85%')
  })

  it('renders operator playbook actions grouped by traffic section', async () => {
    todoGet.mockResolvedValue({
      todo: { id: 3752, status: 'approved', decided_by: null },
      request: { id: 101, kind: 'dispatch', title: '测试待办', summary: '测试摘要', aggregate_decision: 'approved' },
      skill_meta: null,
      structured: null,
      dispatch_tasks: [],
      related_report: null,
      payload: {
        card_type: 'product_decline_decision_card',
        item_id: '8001',
        item_title: '示例品牌测试商品',
        operation_actions: [
          {
            source: 'operator_playbook',
            playbook_id: 'free_search_visitor_decline',
            section: '免费流量端',
            scenario: '免费搜索访客持续下滑',
            priority: '高',
            dimension: '运营专用-免费搜索访客',
            evidence: '命中指标：免费搜索/免费访客环比',
            root_causes: ['关键词排名下跌', '竞品卡位抢流'],
            actions: ['实时核对核心引流词、大词、长尾词搜索排名，掉位词优先补单补人气稳住坑位'],
            ai_action: '补采核心引流词排名证据。',
          },
          {
            source: 'operator_playbook',
            playbook_id: 'paid_keyword_conversion_decline',
            section: '付费推广端',
            scenario: '直通车关键词推广转化率下滑',
            priority: '高',
            dimension: '运营专用-付费推广转化',
            evidence: '命中指标：付费转化环比',
            root_causes: ['付费流量人群不精准', '落地页承接差'],
            actions: ['精准筛选高转化关键词，暂停低转化、高点击无下单垃圾词，精简词库'],
            ai_action: '补采关键词转化证据。',
          },
        ],
      },
    })

    const TodoDetail = (await import('@/pages/inbox/TodoDetail.vue')).default
    const wrapper = mount(TodoDetail, {
      global: {
        stubs: commonStubs,
      },
    })

    await flushPromises()
    await flushPromises()

    const sections = wrapper.findAll('.operator-action-section')
    expect(sections).toHaveLength(2)
    expect(sections[0].text()).toContain('免费流量端')
    expect(sections[0].text()).toContain('免费搜索访客持续下滑')
    expect(sections[0].text()).toContain('关键词排名下跌')
    expect(sections[0].text()).toContain('实时核对核心引流词')
    expect(sections[1].text()).toContain('付费推广端')
    expect(sections[1].text()).toContain('直通车关键词推广转化率下滑')
    expect(sections[1].text()).toContain('精准筛选高转化关键词')
  })

  it('renders video low-consumption agent cards without legacy ecommerce sections', async () => {
    todoGet.mockResolvedValue({
      todo: { id: 10836, status: 'pending', decided_by: null },
      request: {
        id: 104,
        kind: 'dispatch',
        title: 'P1｜云视频账号 党涛 2026-06-10 同主题低消耗视频改进',
        summary: '云视频账号 党涛 有 3 条低消耗视频通过视觉证据门槛。',
        aggregate_decision: null,
      },
      skill_meta: { name: '示例品牌低消耗视频日诊断', department: '示例品牌内容电商运营部' },
      structured: null,
      dispatch_tasks: [],
      related_report: null,
      payload: {
        card_type: 'video_low_consumption_decision_card',
        analysis_schema: 'samplebrand_video_low_consumption_daily_operator_v1',
        account_name: '党涛',
        analysis_date: '2026-06-10',
        recommended_decision: '每条视频按自己的分型参考处理，素材问题建议只改视觉弱项。',
        detail_markdown: [
          '# 云视频账号 党涛 低消耗视频详细分析 2026-06-10',
          '',
          '## 结论摘要',
          '- 优先级：**P1**',
          '',
          '## 逐视频详细结论',
          '1. 《战甲BB_有活动你不买》',
          '   - 低消耗画面证据：开头铺垫慢，商品露出晚。',
          '   - 参考视频画面证据：首屏直接露出商品和核心利益点。',
          '   - 改进：重剪前3秒，直接给出同主题核心利益点。',
        ].join('\n'),
        key_metrics: [
          { name: '低消耗视频数', value: '3', status: '建议关注', delta: '账号最多 3 条' },
          { name: '主题范围', value: '战甲三合一', status: '同主题参考', delta: '不跨主题混用好视频' },
        ],
        metric_sections: [
          {
            title: '建议关注视频',
            metrics: [
              { name: '1. 战甲BB_有活动你不买', value: '素材改版优先', status: '消耗 250.34 / ROI 2.4', note: '战甲三合一' },
              { name: '1. 对标', value: '同主题高质量视频A', status: '消耗 60534.86 / ROI 1.11', note: '同主题高质量视频' },
            ],
          },
        ],
        operation_actions: [
          {
            rank: 1,
            source: 'operator_playbook',
            section: '建议视频 1',
            scenario: '战甲BB_有活动你不买',
            video_id: '98279339',
            topic_key: '战甲三合一',
            theme_name: '有活动你不买',
            editor_code: 'PJX',
            product_code: '战甲BB',
            benchmark_label: '同主题高消耗参考',
            benchmark_title: '同主题高质量视频A',
            benchmark_editor_code: 'MJL',
            dimension: '素材改版优先',
            priority: 'P1',
            evidence: '消耗 250.34，ROI 2.40；同主题参照高质量视频A。',
            low_visual_evidence: '开头铺垫慢，商品露出晚。',
            benchmark_visual_evidence: '首屏直接露出商品和核心利益点。',
            root_causes: ['当前视频弱于同主题样本，不能跨主题混用好视频。'],
            actions: ['重剪前3秒，直接给出同主题核心利益点。'],
            keep_points: ['保留商品露出节奏。'],
            data_checks: ['补查预算、出价、人群包和学习期。'],
          },
        ],
        analysis_basis: [
          { dimension: '筛选口径', basis: '只纳入 statCost > 200 的有消耗视频。', confidence: '高' },
        ],
        data_quality_items: [
          { dimension: '投放承接数据', issue: '预算、出价、人群包未返回，需要运营补查。' },
        ],
        forbidden_actions: ['不要跨主题套用好视频。'],
      },
    })

    const TodoDetail = (await import('@/pages/inbox/TodoDetail.vue')).default
    const wrapper = mount(TodoDetail, {
      global: {
        stubs: commonStubs,
      },
    })

    await flushPromises()
    await flushPromises()

    expect(wrapper.find('.agent-video-action-card').exists()).toBe(true)
    expect(wrapper.find('.operator-action-section').exists()).toBe(false)
    expect(wrapper.text()).toContain('待办结论')
    expect(wrapper.text()).toContain('账号 党涛')
    expect(wrapper.text()).toContain('视频诊断')
    expect(wrapper.text()).toContain('详细分析')
    expect(wrapper.text()).toContain('低消耗视频详细分析')
    expect(wrapper.text()).toContain('低消耗画面证据：开头铺垫慢')
    expect(wrapper.find('.agent-video-detail-markdown h1').text()).toContain('云视频账号 党涛')
    expect(wrapper.find('.agent-video-detail-markdown h2').text()).toContain('结论摘要')
    expect(wrapper.find('.agent-video-detail-markdown strong').text()).toBe('P1')
    expect(wrapper.text()).toContain('逐视频建议')
    expect(wrapper.text()).toContain('主题：有活动你不买')
    expect(wrapper.text()).toContain('剪辑人：PJX')
    expect(wrapper.text()).toContain('产品：战甲BB')
    expect(wrapper.text()).toContain('视频 ID：98279339')
    expect(wrapper.text()).toContain('参考等级：同主题高消耗参考')
    expect(wrapper.text()).toContain('参考样本：同主题高质量视频A')
    expect(wrapper.text()).toContain('参考剪辑人：MJL')
    expect(wrapper.text()).toContain('画面证据')
    expect(wrapper.text()).toContain('低消耗视频：开头铺垫慢，商品露出晚')
    expect(wrapper.text()).toContain('参考视频：首屏直接露出商品和核心利益点')
    expect(wrapper.text()).toContain('原因判断')
    expect(wrapper.text()).toContain('不能跨主题混用好视频')
    expect(wrapper.text()).toContain('重剪前3秒')
    expect(wrapper.text()).toContain('补查预算、出价、人群包和学习期')
    expect(wrapper.text()).toContain('指标与参考建议')
    expect(wrapper.text()).toContain('补查数据')
    expect(wrapper.text()).toContain('不要跨主题套用好视频')
    expect(wrapper.text()).not.toContain('SKU')
    expect(wrapper.text()).not.toContain('付费实时数据')
    expect(wrapper.text()).not.toContain('免费流分析依据')
  })
})
