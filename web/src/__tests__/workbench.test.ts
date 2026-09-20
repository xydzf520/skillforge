import { mount, shallowMount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import WorkbenchModuleNav from '@/components/workbench/WorkbenchModuleNav.vue'
import WorkbenchPatchPreview from '@/components/workbench/WorkbenchPatchPreview.vue'
import WorkbenchAssistantPane from '@/components/workbench/WorkbenchAssistantPane.vue'
import WorkbenchTopBar from '@/components/workbench/WorkbenchTopBar.vue'
import WorkbenchEditorSurface from '@/components/workbench/WorkbenchEditorSurface.vue'
import WorkbenchExplainView from '@/components/workbench/WorkbenchExplainView.vue'
import WorkbenchReviewView from '@/components/workbench/WorkbenchReviewView.vue'
import WorkbenchReviewDiffPanel from '@/components/workbench/WorkbenchReviewDiffPanel.vue'

describe('workbench skeleton', () => {
  it('renders module nav items', () => {
    const wrapper = mount(WorkbenchModuleNav, {
      props: {
        activeModule: 'goal',
        modules: [
          { key: 'goal', label: '目标', status: '草稿' },
          { key: 'rules', label: '规则', status: '空' },
        ],
      },
    })

    expect(wrapper.text()).toContain('目标')
    expect(wrapper.text()).toContain('规则')
  })

  it('renders patch preview payload', () => {
    const wrapper = mount(WorkbenchPatchPreview, {
      props: {
        patch: {
          target_module: 'params',
          intent: 'tune_params',
          summary: '调整参数',
          diff_preview: { changed_keys: ['params'] },
        },
      },
    })

    expect(wrapper.text()).toContain('调整参数')
    expect(wrapper.text()).toContain('params')
  })

  it('renders coding session runtime metadata in assistant header', () => {
    const wrapper = shallowMount(WorkbenchAssistantPane, {
      props: {
        messages: [],
        references: [],
        selectedReferenceIds: [],
        conversations: [],
        doc: {},
        coachSuggestions: [],
        sessionMeta: {
          model: 'glm-5',
          prompt_hash: 'abc123def456',
          tools_count: 2,
          runtime_profile: { provider: 'glm', permission_strategy: 'balanced', bare_mode: true },
        },
      },
      global: {
        stubs: {
          WorkbenchReferencePicker: true,
          CompactBoundaryChip: true,
          BudgetProgress: true,
        },
      },
    })

    expect(wrapper.text()).toContain('glm-5')
    expect(wrapper.text()).toContain('prompt abc123def456')
    expect(wrapper.text()).toContain('2 tools')
    expect(wrapper.text()).toContain('balanced')
    expect(wrapper.text()).toContain('bare')
  })

  it('disables assistant input in review mode', () => {
    const wrapper = shallowMount(WorkbenchAssistantPane, {
      props: {
        messages: [],
        references: [],
        selectedReferenceIds: [],
        conversations: [],
        doc: {},
        coachSuggestions: [],
        studioMode: 'review',
      },
      global: {
        stubs: {
          WorkbenchReferencePicker: true,
          CompactBoundaryChip: true,
          BudgetProgress: true,
          'a-textarea': {
            props: ['disabled', 'placeholder'],
            template: '<textarea :disabled="disabled" :placeholder="placeholder"></textarea>',
          },
        },
      },
    })
    expect(wrapper.text()).toContain('审批模式')
    expect(wrapper.find('textarea').attributes('disabled')).toBeDefined()
  })

  it('hides edit and save entry points in review mode top bar', () => {
    const wrapper = mount(WorkbenchTopBar, {
      props: {
        title: 'Skill A',
        studioMode: 'review',
        isCreate: false,
        editMode: false,
        dirty: true,
      },
      global: {
        mocks: {
          $router: { back: () => {}, push: () => {} },
        },
        stubs: {
          'a-button': { template: '<button v-bind="$attrs"><slot /></button>' },
          'a-tag': { template: '<span><slot /></span>' },
          'a-radio-group': { template: '<div><slot /></div>' },
          'a-radio': { template: '<label><slot /></label>' },
          'a-dropdown': { template: '<div><slot /></div>' },
          'a-doption': { template: '<div><slot /></div>' },
          'a-dsubmenu': { template: '<div><slot /></div>' },
          'a-tooltip': { template: '<div><slot /></div>' },
          'a-popover': { template: '<div><slot /><slot name="content" /></div>' },
          'icon-left': true,
          'icon-save': true,
          'icon-apps': true,
          'icon-code': true,
          'icon-check-circle': true,
          'icon-edit': true,
          'icon-robot': true,
          'icon-menu-fold': true,
          'icon-layers': true,
          'icon-search': true,
          'icon-play-arrow': true,
          'icon-thunderbolt': true,
          'icon-history': true,
          'icon-eye': true,
          'icon-trophy': true,
          'icon-exclamation-circle-fill': true,
          'icon-down': true,
          'icon-lock': true,
          'icon-heart': true,
          'icon-upload': true,
          'icon-user-group': true,
        },
      },
    })

    expect(wrapper.text()).toContain('审批模式')
    expect(wrapper.find('[data-testid="enter-edit-btn"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="save-menu-btn"]').exists()).toBe(false)
  })

  it('exposes personal UI adjustment from Skill Studio top bar', async () => {
    const wrapper = mount(WorkbenchTopBar, {
      props: {
        title: 'Skill A',
        studioMode: 'edit',
        isCreate: false,
        hasContent: true,
      },
      global: {
        mocks: {
          $router: { back: () => {}, push: () => {} },
        },
        stubs: {
          'a-button': { emits: ['click'], template: '<button @click="$emit(\'click\')"><slot /></button>' },
          'a-tag': { template: '<span><slot /></span>' },
          'a-progress': { template: '<div />' },
          'a-radio-group': { template: '<div><slot /></div>' },
          'a-radio': { template: '<label><slot /></label>' },
          'a-dropdown': { template: '<div><slot /></div>' },
          'a-doption': { template: '<div><slot /></div>' },
          'a-dsubmenu': { template: '<div><slot /></div>' },
          'a-tooltip': { template: '<div><slot /></div>' },
          'a-popover': { template: '<div><slot /><slot name="content" /></div>' },
          'icon-left': true,
          'icon-save': true,
          'icon-apps': true,
          'icon-code': true,
          'icon-check-circle': true,
          'icon-edit': true,
          'icon-robot': true,
          'icon-menu-fold': true,
          'icon-layers': true,
          'icon-search': true,
          'icon-play-arrow': true,
          'icon-thunderbolt': true,
          'icon-history': true,
          'icon-eye': true,
          'icon-trophy': true,
          'icon-exclamation-circle-fill': true,
          'icon-down': true,
          'icon-lock': true,
          'icon-heart': true,
          'icon-upload': true,
          'icon-user-group': true,
        },
      },
    })

    const button = wrapper.findAll('button').find(item => item.text().includes('AI 调整界面'))
    expect(button).toBeTruthy()
    await button!.trigger('click')

    expect(wrapper.emitted('open-portal-ui')).toHaveLength(1)
  })

  it('shows explain and review central views in editor surface', () => {
    const explain = shallowMount(WorkbenchEditorSurface, {
      props: {
        studioMode: 'explain',
        doc: { meta: { name: 'Skill A' }, rules: [], params: [], output_table: [], test_cases: [] },
        skillId: 'Skill-A',
      },
      global: {
        stubs: {
          WorkbenchExplainView: { template: '<div class="explain-stub">讲解视图</div>' },
          WorkbenchReviewView: { template: '<div />' },
          IDEPatchOverlay: true,
        },
      },
    })
    expect(explain.text()).toContain('讲解视图')

    const review = shallowMount(WorkbenchEditorSurface, {
      props: {
        studioMode: 'review',
        doc: { meta: { name: 'Skill A' }, rules: [], params: [], output_table: [], test_cases: [] },
        skillId: 'Skill-A',
      },
      global: {
        stubs: {
          WorkbenchExplainView: { template: '<div />' },
          WorkbenchReviewView: { template: '<div class="review-stub">审批视图</div>' },
          IDEPatchOverlay: true,
        },
      },
    })
    expect(review.text()).toContain('审批视图')
  })

  it('renders L3-C focused rule editor rail', async () => {
    const wrapper = mount(WorkbenchEditorSurface, {
      props: {
        viewMode: 'block',
        activeModule: 'rules',
        moduleFocusMode: true,
        doc: {
          meta: { name: 'Skill A' },
          rules: [],
          params: [],
          output_table: [],
          todos: [],
          test_cases: [],
        },
      },
      global: {
        stubs: {
          'a-spin': { template: '<div><slot /></div>' },
          'icon-left': true,
          'icon-list': true,
          'icon-code': true,
          'icon-experiment': true,
          'icon-upload': true,
          'icon-plus': true,
          'icon-drag-dot-vertical': true,
        },
      },
    })

    expect(wrapper.find('.surface-rule-editor.focus-mode').exists()).toBe(true)
    expect(wrapper.find('.rule-mini-nav').exists()).toBe(true)
    expect(wrapper.text()).toContain('预览影响')

    await wrapper.find('.rule-mini-nav-item').trigger('click')
    expect(wrapper.emitted('go-module')?.[0]).toEqual(['overview'])
  })

  it('renders explain pack sections', () => {
    const wrapper = mount(WorkbenchExplainView, {
      props: {
        skillId: 'EC-Explain-01',
        explainPack: {
          executive_summary: 'AI 解释总结',
          trigger_summary: '按计划触发',
          decision_ladder: [{ id: 'step_1', name: '判断 ROI', summary: 'ROI > 1.2 时，绿灯' }],
          parameter_impacts: [{ name: 'roi_threshold', value: 1.2, impact: '决定绿灯阈值' }],
          output_contract: [{ name: '结论', recipient: '运营', format: 'text' }],
          test_confidence: { total_cases: 1, covered_rules: 1, uncovered_rules: 0, summary: '测试覆盖充足' },
        },
        doc: {
          meta: { name: '解释 Skill', trigger_type: 'manual', risk_level: 'R2' },
          goal: '根据 ROI 判断是否加预算',
          rules: [{ id: 'step_1', name: '判断 ROI', branches: [{ condition: 'ROI > 1.2', conclusion: '绿灯' }] }],
          params: [{ name: 'roi_threshold', default_value: 1.2 }],
          output_table: [{ name: '结论', recipient: '运营', format: 'text' }],
          test_cases: [{ name: 'case_1', input_data: { roi: 1.5 }, expected_output: { result: 'green' } }],
        },
        readiness: { can_publish: true, blocker_count: 0 },
      },
    })

    expect(wrapper.text()).toContain('AI 解释总结')
    expect(wrapper.text()).toContain('按计划触发')
    expect(wrapper.text()).toContain('决策阶梯')
    expect(wrapper.text()).toContain('参数影响')
    expect(wrapper.text()).toContain('输出契约')
    expect(wrapper.text()).toContain('测试信心')
  })

  it('renders review context and structured review actions', () => {
    const wrapper = mount(WorkbenchReviewView, {
      props: {
        skillId: 'EC-Review-01',
        doc: {
          meta: { name: '审批 Skill' },
          rules: [{ id: 'step_1' }],
          params: [{ name: 'roi_threshold' }],
          output_table: [{ name: '结论' }],
          test_cases: [{ name: 'case_1' }],
        },
        readiness: { can_publish: false, blocker_count: 2, warning_count: 1, replay: { status: 'degraded' } },
        healthScore: { score: 72 },
        guardianItems: [{ severity: 'high', description: '运行波动' }],
        canActReview: true,
        reviewContext: {
          review: {
            id: 9,
            change_type: 'params',
            submitter: 'alice',
            reviewer: 'bob',
            status: 'pending',
            diff_summary: '调高 ROI 阈值',
            diff_text: '@@ -1,3 +1,3 @@\n- old\n+ new',
            comments: [
              { id: 1, author: 'alice', content: '请重点看 ROI 边界', resolved: false, created_at: '2026-04-11T10:00:00', file_path: 'SKILL.md', line_number: 3 },
            ],
          },
          semantic_diff: {
            changes: [
              { description: '阈值从 1.2 改为 1.4', old_value: '1.2', new_value: '1.4' },
              { description: '增加兜底分支' },
              { description: '补充输出字段' },
              { description: '新增测试样例 A' },
              { description: '新增测试样例 B' },
              { description: '调整提示文案' },
              { description: '第七条变化' },
            ],
          },
          ai_review: { risk_score: 7, opinions: [{ title: '风险偏高', detail: '边界覆盖不足' }] },
          reviewer_report: {
            one_line: '该变更会放宽 ROI 判定。',
            summary: { logic_change: '放宽阈值', direction: '放宽', estimated_impact: '绿灯样本增加' },
            risk: { behavior_expansion: 'medium', reasons: ['阈值放宽可能扩大触发范围'] },
            result_diff: {
              sample_size_before: 10,
              sample_size_after: 10,
              before: { 绿灯: 2 },
              after: { 绿灯: 4 },
            },
          },
        },
      },
      global: {
        stubs: {
          'a-button': { template: '<button v-bind="$attrs" @click="$emit(\'click\')"><slot /></button>' },
          'a-tag': { template: '<span><slot /></span>' },
          WorkbenchReviewDiffPanel: { template: '<div class="diff-stub">Diff Drilldown</div>' },
        },
      },
    })

    expect(wrapper.text()).toContain('当前待审单')
    expect(wrapper.text()).toContain('#9')
    expect(wrapper.text()).toContain('AI 变更分析')
    expect(wrapper.text()).toContain('风险意见')
    expect(wrapper.text()).toContain('风险原因')
    expect(wrapper.text()).toContain('结果分布变化')
    expect(wrapper.text()).toContain('评论时间线')
    expect(wrapper.text()).toContain('请重点看 ROI 边界')
    expect(wrapper.text()).toContain('查看更多变化')
    expect(wrapper.text()).toContain('Diff Drilldown')
    expect(wrapper.text()).toContain('批准当前审核')
    expect(wrapper.text()).toContain('驳回当前审核')
  })

  it('review diff panel exposes diff text and selected line meta', () => {
    const wrapper = mount(WorkbenchReviewDiffPanel, {
      props: {
        diffText: '@@ -1,2 +1,2 @@\n-old\n+new',
        comments: [{ id: 1, line_number: 2, author: 'alice', content: '看这里' }],
        selectedLine: 2,
      },
      global: {
        stubs: {
          MonacoEditor: { template: '<div class="monaco-stub">monaco</div>' },
        },
      },
    })

    expect(wrapper.text()).toContain('变更详情')
    expect(wrapper.text()).toContain('当前定位 L2')
  })
})
