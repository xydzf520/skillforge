import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  knowledgeApi: {
    summary: vi.fn(),
    bases: vi.fn(),
    sync: vi.fn(),
    documents: vi.fn(),
    createDocument: vi.fn(),
    getDocument: vi.fn(),
    updateDocument: vi.fn(),
    deleteDocument: vi.fn(),
    reindexDocument: vi.fn(),
    uploadDocument: vi.fn(),
    importUrl: vi.fn(),
    indexJobs: vi.fn(),
    ragHealth: vi.fn(),
    search: vi.fn(),
    ask: vi.fn(),
    context: vi.fn(),
  },
  copyText: vi.fn(),
  message: {
    success: vi.fn(),
    warning: vi.fn(),
  },
  modal: {
    confirm: vi.fn(),
  },
}))

vi.mock('@/api', () => ({
  knowledgeApi: mocks.knowledgeApi,
}))

vi.mock('@/utils/clipboard', () => ({
  copyText: mocks.copyText,
}))

vi.mock('@arco-design/web-vue', () => ({
  Message: mocks.message,
  Modal: mocks.modal,
}))

vi.mock('@arco-design/web-vue/es/icon', () => ({
  IconBook: { template: '<i />' },
  IconBranch: { template: '<i />' },
  IconCode: { template: '<i />' },
  IconCopy: { template: '<i />' },
  IconDelete: { template: '<i />' },
  IconEdit: { template: '<i />' },
  IconEye: { template: '<i />' },
  IconImage: { template: '<i />' },
  IconLink: { template: '<i />' },
  IconPlus: { template: '<i />' },
  IconRefresh: { template: '<i />' },
  IconRobot: { template: '<i />' },
  IconSearch: { template: '<i />' },
  IconSync: { template: '<i />' },
  IconUndo: { template: '<i />' },
  IconUpload: { template: '<i />' },
}))

import KnowledgeHome from '@/pages/knowledge/KnowledgeHome.vue'

function stubs() {
  return {
    SfKpiCard: { props: ['label', 'value', 'hint'], template: '<section><b>{{ label }}</b><span>{{ value }}</span><small>{{ hint }}</small></section>' },
    SfEmptyState: { props: ['title', 'description', 'hint'], template: '<div>{{ title }}{{ description }}{{ hint }}</div>' },
    LearningFlowMini: { template: '<section data-test="learning-flow-mini" />' },
    'a-button': {
      props: ['disabled', 'loading'],
      emits: ['click'],
      template: '<button :disabled="disabled || loading" @click="$emit(\'click\')"><slot /></button>',
    },
    'a-checkbox': {
      props: ['modelValue'],
      emits: ['update:modelValue'],
      template: '<label><input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" /><slot /></label>',
    },
    'a-col': { template: '<div><slot /></div>' },
    'a-drawer': { props: ['visible', 'title'], template: '<aside v-if="visible"><h3>{{ title }}</h3><slot /></aside>' },
    'a-form': { template: '<form><slot /></form>' },
    'a-form-item': { props: ['label'], template: '<label><span>{{ label }}</span><slot /></label>' },
    'a-input': {
      props: ['modelValue', 'placeholder'],
      emits: ['update:modelValue'],
      template: '<input :placeholder="placeholder" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    },
    'a-input-search': {
      props: ['modelValue', 'placeholder'],
      emits: ['update:modelValue', 'search', 'pressEnter'],
      template: '<input :placeholder="placeholder" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" @keyup.enter="$emit(\'pressEnter\')" />',
    },
    'a-modal': {
      props: ['visible', 'title', 'okLoading'],
      emits: ['ok'],
      template: '<section v-if="visible"><h3>{{ title }}</h3><slot /><button class="modal-ok" :disabled="okLoading" @click="$emit(\'ok\')">确定</button></section>',
    },
    'a-option': { props: ['value'], template: '<option :value="value"><slot /></option>' },
    'a-row': { template: '<div><slot /></div>' },
    'a-select': {
      props: ['modelValue'],
      emits: ['update:modelValue', 'change'],
      template: '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value); $emit(\'change\', $event.target.value)"><slot /></select>',
    },
    'a-space': { template: '<span><slot /></span>' },
    'a-spin': { template: '<div><slot /></div>' },
    'a-table': {
      props: ['data'],
      provide(this: any) {
        return { tableData: this.data || [] }
      },
      template: '<table><slot name="columns" /></table>',
    },
    'a-table-column': {
      props: ['title'],
      inject: { tableData: { default: () => [] } },
      template: '<th>{{ title }}<div v-for="record in tableData" :key="record.id"><slot name="cell" :record="record" /></div></th>',
    },
    'a-tag': { template: '<span><slot /></span>' },
    'a-textarea': {
      props: ['modelValue', 'placeholder'],
      emits: ['update:modelValue'],
      template: '<textarea :placeholder="placeholder" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    },
    'a-tooltip': { template: '<span><slot /></span>' },
  }
}

describe('KnowledgeHome', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.knowledgeApi.bases.mockResolvedValue({
      items: [{
        id: 'kb-ec',
        department: '电商部',
        name: '电商部知识库',
        description: '部门知识',
        storage_backend: 'lightrag',
        stats: { document_count: 1, chunk_count: 2 },
        can_write: true,
      }],
    })
    mocks.knowledgeApi.summary.mockResolvedValue({
      department_count: 1,
      document_count: 1,
      chunk_count: 2,
      storage_backend: 'lightrag',
    })
    mocks.knowledgeApi.documents.mockResolvedValue({
      items: [{
        id: 'doc-1',
        kb_id: 'kb-ec',
        department: '电商部',
        title: '价格力 SOP',
        source_type: 'manual',
        status: 'active',
        tags: ['SOP'],
        chunk_count: 2,
        can_write: true,
      }],
    })
    mocks.knowledgeApi.getDocument.mockResolvedValue({
      id: 'doc-1',
      kb_id: 'kb-ec',
      department: '电商部',
      title: '价格力 SOP',
      content: '处理价格力下降',
      source_type: 'manual',
      status: 'active',
      tags: ['SOP'],
      can_write: true,
    })
    mocks.knowledgeApi.search.mockResolvedValue({
      items: [{ document_id: 'doc-1', document_title: '价格力 SOP', department: '电商部', source_type: 'manual', score: 0.9, snippet: '处理价格力下降' }],
    })
    mocks.knowledgeApi.ask.mockResolvedValue({
      answer: '先检查券后价',
      answer_mode: 'extractive',
      sources: [{ document_id: 'doc-1', document_title: '价格力 SOP', department: '电商部', source_type: 'manual', score: 0.9, snippet: '处理价格力下降' }],
    })
    mocks.knowledgeApi.context.mockResolvedValue({
      prompt_context: '[知识库上下文]\n处理价格力下降',
      context_chars: 18,
      sources: [{ document_id: 'doc-1', document_title: '价格力 SOP', department: '电商部', source_type: 'manual', score: 0.9, snippet: '处理价格力下降' }],
    })
    mocks.knowledgeApi.createDocument.mockResolvedValue({ id: 'doc-new' })
    mocks.knowledgeApi.updateDocument.mockResolvedValue({ id: 'doc-1' })
    mocks.knowledgeApi.deleteDocument.mockResolvedValue({ id: 'doc-1', status: 'deleted' })
    mocks.knowledgeApi.reindexDocument.mockResolvedValue({ id: 'doc-1' })
    mocks.knowledgeApi.uploadDocument.mockResolvedValue({ id: 'doc-upload' })
    mocks.knowledgeApi.importUrl.mockResolvedValue({ id: 'doc-url' })
    mocks.knowledgeApi.ragHealth.mockResolvedValue({ backend: 'lightrag', mode: 'embedded' })
    mocks.knowledgeApi.sync.mockResolvedValue({ total: 1 })
    mocks.copyText.mockResolvedValue(true)
  })

  it('loads department bases and supports search, ask and context calls', async () => {
    const wrapper = mount(KnowledgeHome, { global: { stubs: stubs() } })
    await flushPromises()

    expect(mocks.knowledgeApi.bases).toHaveBeenCalled()
    expect(mocks.knowledgeApi.documents).toHaveBeenCalledWith(expect.objectContaining({ kb_id: 'kb-ec', status: 'active' }))

    await wrapper.find('input[placeholder="搜索 SOP、Skill、数据资产说明"]').setValue('价格力下降')
    await wrapper.findAll('button').find(button => button.text().includes('搜索'))!.trigger('click')
    await flushPromises()
    expect(mocks.knowledgeApi.search).toHaveBeenCalledWith(expect.objectContaining({ query: '价格力下降', kb_id: 'kb-ec' }))

    await wrapper.findAll('button').find(button => button.text().includes('提问'))!.trigger('click')
    await flushPromises()
    expect(mocks.knowledgeApi.ask).toHaveBeenCalledWith(expect.objectContaining({ query: '价格力下降', kb_id: 'kb-ec' }))

    await wrapper.findAll('button').find(button => button.text().includes('上下文'))!.trigger('click')
    await flushPromises()
    expect(mocks.knowledgeApi.context).toHaveBeenCalledWith(expect.objectContaining({ query: '价格力下降', kb_id: 'kb-ec' }))
    expect(wrapper.text()).toContain('知识库上下文')
  })

  it('creates, edits, reindexes and deletes documents from the table workflow', async () => {
    const wrapper = mount(KnowledgeHome, { global: { stubs: stubs() } })
    await flushPromises()

    await wrapper.findAll('button').find(button => button.text().includes('新增文档'))!.trigger('click')
    await wrapper.find('input[placeholder="例如：五星价格力处理 SOP"]').setValue('新 SOP')
    await wrapper.find('textarea[placeholder="粘贴 SOP、复盘结论、业务规则、排障步骤或数据说明"]').setValue('新内容')
    await wrapper.find('.modal-ok').trigger('click')
    await flushPromises()
    expect(mocks.knowledgeApi.createDocument).toHaveBeenCalledWith(expect.objectContaining({
      kb_id: 'kb-ec',
      title: '新 SOP',
      content: '新内容',
      source_type: 'manual',
    }))

    await wrapper.find('button[aria-label="编辑文档"]').trigger('click')
    await flushPromises()
    expect(mocks.knowledgeApi.getDocument).toHaveBeenCalledWith('doc-1')

    await wrapper.find('button[aria-label="重建检索索引"]').trigger('click')
    await flushPromises()
    expect(mocks.knowledgeApi.reindexDocument).toHaveBeenCalledWith('doc-1')

    mocks.modal.confirm.mockImplementationOnce((options: any) => options.onOk())
    await wrapper.find('button[aria-label="删除文档"]').trigger('click')
    await flushPromises()
    expect(mocks.knowledgeApi.deleteDocument).toHaveBeenCalledWith('doc-1')
  })
})
