import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SkillDependencyGraph from '@/components/studio/SkillDependencyGraph.vue'
import { skillApi } from '@/api'

vi.mock('@/api', () => ({
  skillApi: {
    dependencyGraph: vi.fn(),
  },
}))

vi.mock('cytoscape', () => ({
  default: vi.fn(() => ({
    destroy: vi.fn(),
    fit: vi.fn(),
    on: vi.fn(),
  })),
}))

const dependencyGraphMock = vi.mocked(skillApi.dependencyGraph)

function mountGraph() {
  return mount(SkillDependencyGraph, {
    props: { skillId: 'skill-self' },
    global: {
      stubs: {
        'a-input-search': {
          props: ['modelValue', 'size'],
          emits: ['update:modelValue'],
          template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
        },
        'a-checkbox-group': { template: '<div><slot /></div>' },
        'a-checkbox': { template: '<span><slot /></span>' },
        'a-switch': {
          props: ['modelValue', 'size'],
          emits: ['update:modelValue'],
          template: '<input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
        },
        'a-button': {
          emits: ['click'],
          template: '<button @click="$emit(\'click\')"><slot /></button>',
        },
        SfLoadingState: { template: '<div>loading</div>' },
        SfEmptyState: { props: ['description'], template: '<div>{{ description }}</div>' },
        IconApps: true,
        IconRefresh: true,
        IconZoomIn: true,
      },
    },
  })
}

describe('SkillDependencyGraph', () => {
  beforeEach(() => {
    dependencyGraphMock.mockResolvedValue({
      nodes: [
        { id: 'skill-self', type: 'skill', label: '当前 Skill', is_self: true, can_view: true },
        { id: 'skill-parent', type: 'skill', label: '父 Skill', can_view: true },
        { id: 'ds-1', type: 'datasource', label: '数据表', can_view: true },
      ],
      edges: [
        { from: 'skill-parent', to: 'skill-self', kind: 'forked-into' },
        { from: 'skill-self', to: 'ds-1', kind: 'consumes' },
      ],
      summary: {
        skill_count: 2,
        datasource_count: 1,
        playbook_count: 0,
        total_edges: 2,
      },
    })
  })

  it('loads dependency graph summary', async () => {
    const wrapper = mountGraph()
    await flushPromises()

    expect(dependencyGraphMock).toHaveBeenCalledWith('skill-self')
    expect(wrapper.text()).toContain('Skill 2')
    expect(wrapper.text()).toContain('数据源 1')
  })

  it('shows empty state when search + onlyMatched hides all non-self nodes', async () => {
    const wrapper = mountGraph()
    await flushPromises()

    const [searchInput] = wrapper.findAll('input')
    await searchInput.setValue('不存在的节点')
    const toggle = wrapper.find('input[type="checkbox"]')
    await toggle.setValue(true)
    await flushPromises()

    expect(wrapper.text()).toContain('当前搜索仅命中当前 Skill')
  })
})
