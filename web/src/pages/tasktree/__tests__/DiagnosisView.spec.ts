/**
 * DiagnosisView 组件测试。
 *
 * 覆盖 AI 诊断 fallback UI：
 * - ai_available !== false → 蓝色 "AI 诊断" Tag
 * - ai_available === false → 灰色 "规则引擎" Tag + 提示文字
 * - 没有 diagnosis 文本时不渲染 Tag
 * - run 为 null / 非 failed 状态时不渲染诊断操作
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import DiagnosisView from '../components/DiagnosisView.vue'
import { SkillRunStatus, type TaskTreeSkillRunItem } from '../types'

function makeRun(overrides: Partial<TaskTreeSkillRunItem> = {}): TaskTreeSkillRunItem {
  return {
    run_id: 'run-1',
    skill_id: 'skill-1',
    skill_name: 'Skill One',
    status: SkillRunStatus.FAILED,
    ...overrides,
  }
}

// Arco 组件在 vitest jsdom 下未 autoload，用轻量 stub 保留 slot / props。
const globalStubs = {
  'a-card': {
    template: '<div class="a-card"><div class="a-card-title"><slot name="title" /></div><div class="a-card-body"><slot /></div></div>',
  },
  'a-tag': {
    props: ['size', 'color'],
    template: '<span class="a-tag" :data-color="color"><slot /></span>',
  },
  'a-button': {
    props: ['size', 'type', 'loading'],
    template: '<button class="a-btn" :disabled="loading" @click="$emit(\'click\')"><slot /></button>',
  },
  'a-spin': {
    template: '<div class="a-spin"><slot /></div>',
  },
  // common/SfEmptyState 内部也是 Arco 组件，为避免递归 stub，直接 stub 这个组件
  SfEmptyState: {
    props: ['description'],
    template: '<div class="sf-empty" :data-description="description" />',
  },
}

describe('DiagnosisView', () => {
  it('ai_available !== false 时渲染 AI 诊断 Tag（蓝色）', () => {
    const wrapper = mount(DiagnosisView, {
      global: { stubs: globalStubs },
      props: {
        run: makeRun(),
        diagnosis: '网络超时，建议重试',
        aiAvailable: true,
        loading: false,
      },
    })
    const tag = wrapper.find('[data-testid="tasktree-diagnosis-source-tag"]')
    expect(tag.exists()).toBe(true)
    expect(tag.text()).toContain('AI 诊断')
    expect(wrapper.find('[data-testid="tasktree-diagnosis-fallback-tip"]').exists()).toBe(false)
  })

  it('ai_available === false 时渲染规则引擎 Tag + 兜底提示', () => {
    const wrapper = mount(DiagnosisView, {
      global: { stubs: globalStubs },
      props: {
        run: makeRun(),
        diagnosis: '规则引擎生成：检查错误码 503',
        aiAvailable: false,
        loading: false,
      },
    })
    const tag = wrapper.find('[data-testid="tasktree-diagnosis-source-tag"]')
    expect(tag.exists()).toBe(true)
    expect(tag.text()).toContain('规则引擎')

    const tip = wrapper.find('[data-testid="tasktree-diagnosis-fallback-tip"]')
    expect(tip.exists()).toBe(true)
    expect(tip.text()).toContain('AI 服务不可用')
    expect(tip.text()).toContain('规则引擎')
  })

  it('diagnosis 为空时不渲染 Tag（避免误导）', () => {
    const wrapper = mount(DiagnosisView, {
      global: { stubs: globalStubs },
      props: {
        run: makeRun(),
        diagnosis: '',
        aiAvailable: true,
        loading: false,
      },
    })
    expect(wrapper.find('[data-testid="tasktree-diagnosis-source-tag"]').exists()).toBe(false)
  })

  it('run 为 null 时不渲染诊断按钮', () => {
    const wrapper = mount(DiagnosisView, {
      global: { stubs: globalStubs },
      props: {
        run: null,
        diagnosis: '',
        aiAvailable: true,
        loading: false,
      },
    })
    expect(wrapper.find('[data-testid="tasktree-diagnose-btn"]').exists()).toBe(false)
  })

  it('run 不是 failed 状态时不渲染诊断按钮', () => {
    const wrapper = mount(DiagnosisView, {
      global: { stubs: globalStubs },
      props: {
        run: makeRun({ status: SkillRunStatus.COMPLETED }),
        diagnosis: '',
        aiAvailable: true,
        loading: false,
      },
    })
    expect(wrapper.find('[data-testid="tasktree-diagnose-btn"]').exists()).toBe(false)
  })

  it('点击诊断按钮触发 diagnose-run 事件', async () => {
    const wrapper = mount(DiagnosisView, {
      global: { stubs: globalStubs },
      props: {
        run: makeRun(),
        diagnosis: '',
        aiAvailable: true,
        loading: false,
      },
    })
    await wrapper.find('[data-testid="tasktree-diagnose-btn"]').trigger('click')
    const emits = wrapper.emitted('diagnose-run')
    expect(emits).toBeTruthy()
    expect(emits![0][0]).toBe('run-1')
  })
})
