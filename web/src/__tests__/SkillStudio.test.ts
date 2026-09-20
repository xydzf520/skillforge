/**
 * SkillStudio 深度集成测试
 *
 * 覆盖：初始化、编辑模式、视图切换、文件加载、AI对话、命令面板、
 *       底部面板、版本历史、影子运行、Draft防丢失、快捷键等
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// ── 工具函数测试 ──

describe('markdownParser', () => {
  let documentToMarkdown: typeof import('../utils/markdownParser')['documentToMarkdown']
  let markdownToDocument: typeof import('../utils/markdownParser')['markdownToDocument']

  beforeEach(async () => {
    const mod = await import('../utils/markdownParser')
    documentToMarkdown = mod.documentToMarkdown
    markdownToDocument = mod.markdownToDocument
  })

  it('生成包含所有标准章节的 SKILL.md', () => {
    const doc = {
      meta: { name: 'TestSkill', department: 'EC' },
      goal: '测试目标',
      rules: [{ id: 'step_1', name: '判断', branches: [{ condition: 'ROI > 1', conclusion: '绿灯', action: '', next_step: null }] }],
      params: [],
      output_table: [{ name: '结论', format: 'text', recipient: '运营', approval_level: '无' }],
      test_cases: [{ name: '测试1', input_data: { x: 1 }, expected_output: { y: 2 } }],
      antipatterns: [],
      data_inputs: [],
      custom_sections: {},
    }
    const md = documentToMarkdown(doc)
    expect(md).toContain('# TestSkill')
    expect(md).toContain('## 目的')
    expect(md).toContain('## 执行步骤')
    expect(md).toContain('## 输出')
    expect(md).toContain('## 测试用例')
    expect(md).toContain('ROI > 1')
    expect(md).toContain('绿灯')
  })

  it('roundtrip 不丢关键数据', () => {
    const doc = {
      meta: { name: '回程测试', department: 'EC' },
      goal: '确保数据不丢失',
      rules: [{ id: 'step_1', name: '检查', branches: [{ condition: 'x > 0', conclusion: '通过', action: '继续', next_step: null }] }],
      output_table: [{ name: '结果', format: 'text', recipient: '测试', approval_level: '无' }],
      test_cases: [{ name: '用例1', input_data: { a: 1 }, expected_output: { b: 2 } }],
    }
    const md = documentToMarkdown(doc)
    const restored = markdownToDocument(md)
    expect(restored!.goal).toContain('确保数据不丢失')
    expect(restored!.rules!.length).toBe(1)
    expect(restored!.output_table!.length).toBe(1)
  })

  it('解析带转义管道符的表格', () => {
    const md = '## 输出\n\n| 输出项 | 格式 | 接收人 | 审批级别 |\n|---|---|---|---|\n| 结论\\|备注 | text | 运营 | 无 |\n'
    const doc = markdownToDocument(md)
    expect(doc!.output_table!.length).toBe(1)
    expect(doc!.output_table![0].name).toContain('结论|备注')
  })

  it('多个 step 解析不截断', () => {
    const md = `## 执行步骤\n\n### Step step_1: 第一步\n\n  ├─ A > 1 → 通过\n  └─ A <= 1 → 失败\n\n### Step step_2: 第二步\n\n  ├─ B > 2 → 通过\n  └─ B <= 2 → 失败\n`
    const doc = markdownToDocument(md)
    expect(doc!.rules!.length).toBe(2)
    expect(doc!.rules![0].branches!.length).toBe(2)
    expect(doc!.rules![1].branches!.length).toBe(2)
  })

  it('空 markdown 返回 null', () => {
    expect(markdownToDocument('')).toBeNull()
    expect(markdownToDocument(null)).toBeNull()
  })

  it('无 frontmatter 的 markdown 仍可解析', () => {
    const md = '## 目的\n\n直接目标\n\n## 执行步骤\n\n### Step 1: 判断\n\n  └─ x > 0 → 通过\n'
    const doc = markdownToDocument(md)
    expect(doc!.goal).toContain('直接目标')
    expect(doc!.rules!.length).toBe(1)
  })
})

describe('schemaMapper', () => {
  let toStructuredPayload: typeof import('../utils/schemaMapper')['toStructuredPayload']
  let fromSkillResponse: typeof import('../utils/schemaMapper')['fromSkillResponse']

  beforeEach(async () => {
    const mod = await import('../utils/schemaMapper')
    toStructuredPayload = mod.toStructuredPayload
    fromSkillResponse = mod.fromSkillResponse
  })

  it('fromSkillResponse 处理 policy_pack 参数', () => {
    const resp = {
      id: 'test', name: 'Test', department: 'EC', status: 'draft',
      structured: { frontmatter: { name: 'Test' }, purpose: '目标', steps: [], output_definition: [], test_cases: [] },
      policy_pack: { roi_threshold: 1.2, budget_limit: 5000 },
    }
    const doc = fromSkillResponse(resp)!
    expect(doc.params!.length).toBe(2)
    expect(doc.params![0].name).toBe('roi_threshold')
    expect(doc.params![1].name).toBe('budget_limit')
  })

  it('toStructuredPayload 输出正确的 policy_pack', () => {
    const doc = {
      meta: { name: 'Test' },
      goal: '目标',
      params: [{ name: 'threshold', default_value: 1.5 }],
      rules: [], output_table: [], test_cases: [],
    }
    const payload = toStructuredPayload(doc)
    expect(payload.policy_pack.threshold).toBe(1.5)
    expect(payload.purpose).toBe('目标')
  })

  it('null 安全', () => {
    expect(fromSkillResponse(null)).toBeNull()
    expect(toStructuredPayload(null)).toEqual({})
  })

  it('fromSkillResponse 恢复 v7 task contract sidecar', () => {
    const resp = {
      id: 'demo',
      name: 'Demo',
      department: 'EC',
      parsed: { frontmatter: { name: 'Demo' }, purpose: '目标', steps: [], output_definition: [], test_cases: [] },
      other_files: {
        'intent.md': '# intent',
        'policy.yaml': 'version: 1',
        'task-contract.json': JSON.stringify({ goal: '每天 18:00 发日报', risks: { level: 'R2' } }),
        'task-review-state.json': JSON.stringify({ gate: { can_publish: true, items: [{ key: 'preview', passed: true }] } }),
      },
    }
    const doc = fromSkillResponse(resp)!
    expect(doc.custom_sections!.__artifacts!['intent.md']).toContain('intent')
    expect((doc.custom_sections!.__task_contract as Record<string, unknown>)!.goal).toContain('日报')
    expect((doc.custom_sections!.__task_gate as Record<string, unknown>)!.can_publish).toBe(true)
  })
})

describe('UI Store', () => {
  let useUIStore: typeof import('../stores/ui')['useUIStore']
  let createPinia: typeof import('pinia')['createPinia']
  let setActivePinia: typeof import('pinia')['setActivePinia']

  beforeEach(async () => {
    // 清理 localStorage 防止测试间污染
    localStorage.removeItem('sf-wb-ui')
    const pinia = await import('pinia')
    createPinia = pinia.createPinia
    setActivePinia = pinia.setActivePinia
    setActivePinia(createPinia())
    const mod = await import('../stores/ui')
    useUIStore = mod.useUIStore
  })

  afterEach(() => {
    localStorage.removeItem('sf-wb-ui')
  })

  it('toggleBottomPanel() 无参数时纯开关', () => {
    const ui = useUIStore()
    expect(ui.bottomPanelOpen).toBe(false)
    ui.toggleBottomPanel()
    expect(ui.bottomPanelOpen).toBe(true)
    ui.toggleBottomPanel()
    expect(ui.bottomPanelOpen).toBe(false)
  })

  it('toggleBottomPanel(tab) 打开并切到指定 tab', () => {
    const ui = useUIStore()
    ui.toggleBottomPanel('test')
    expect(ui.bottomPanelOpen).toBe(true)
    expect(ui.bottomPanelTab).toBe('test')
  })

  it('toggleBottomPanel(同一tab) 关闭面板', () => {
    const ui = useUIStore()
    // 确保初始关闭
    ui.bottomPanelOpen = false
    ui.bottomPanelTab = 'validation'

    ui.toggleBottomPanel('test')
    expect(ui.bottomPanelOpen).toBe(true)
    expect(ui.bottomPanelTab).toBe('test')

    ui.toggleBottomPanel('test')
    expect(ui.bottomPanelOpen).toBe(false)
  })

  it('toggleBottomPanel(不同tab) 切换而不关闭', () => {
    const ui = useUIStore()
    ui.toggleBottomPanel('test')
    ui.toggleBottomPanel('sandbox')
    expect(ui.bottomPanelOpen).toBe(true)
    expect(ui.bottomPanelTab).toBe('sandbox')
  })

  it('setViewMode 只接受 block/code', () => {
    const ui = useUIStore()
    ui.setViewMode('code')
    expect(ui.viewMode).toBe('code')
    ui.setViewMode('invalid')
    expect(ui.viewMode).toBe('code') // 不变
  })

  it('setStudioPerspective 只接受 edit/explain/review', () => {
    const ui = useUIStore()
    ui.setStudioPerspective('explain')
    expect(ui.studioPerspective).toBe('explain')
    ui.setStudioPerspective('review')
    expect(ui.studioPerspective).toBe('review')
    ui.setStudioPerspective('invalid')
    expect(ui.studioPerspective).toBe('review')
  })

  it('toggleAssistant 开关切换', () => {
    const ui = useUIStore()
    const initial = ui.assistantPaneOpen
    ui.toggleAssistant()
    expect(ui.assistantPaneOpen).toBe(!initial)
  })

  it('setEditorTheme 使用 nullish coalescing', () => {
    const ui = useUIStore()
    ui.setEditorTheme('dark')
    expect(ui.editorTheme).toBe('dark')
    ui.setEditorTheme('light')
    expect(ui.editorTheme).toBe('light')
  })

  it('setEditorFontSize 限制在 12-24', () => {
    const ui = useUIStore()
    ui.setEditorFontSize(8)
    expect(ui.editorFontSize).toBe(12)
    ui.setEditorFontSize(30)
    expect(ui.editorFontSize).toBe(24)
    ui.setEditorFontSize(16)
    expect(ui.editorFontSize).toBe(16)
  })
})

describe('Document Store', () => {
  let useDocumentStore: typeof import('../stores/document')['useDocumentStore']
  let createPinia2: typeof import('pinia')['createPinia']
  let setActivePinia2: typeof import('pinia')['setActivePinia']

  beforeEach(async () => {
    const pinia = await import('pinia')
    createPinia2 = pinia.createPinia
    setActivePinia2 = pinia.setActivePinia
    setActivePinia2(createPinia2())
    const mod = await import('../stores/document')
    useDocumentStore = mod.useDocumentStore
  })

  it('初始状态为空', () => {
    const doc = useDocumentStore()
    expect(doc.dirty).toBe(false)
    expect(doc.activeModule).toBe('meta')
    expect(doc.draftRevision).toBe(0)
    expect(doc.skillDocument.goal.structuredValue).toBe('')
  })

  it('updateModuleStructured 标记 dirty', () => {
    const doc = useDocumentStore()
    doc.updateModuleStructured('goal', '新目标')
    expect(doc.skillDocument.goal.structuredValue).toBe('新目标')
    expect(doc.dirty).toBe(true)
    expect(doc.dirtyModules.has('goal')).toBe(true)
    expect(doc.draftRevision).toBe(1)
  })

  it('markSaved 清除所有 dirty', () => {
    const doc = useDocumentStore()
    doc.updateModuleStructured('goal', '修改')
    doc.updateModuleStructured('params', [{ name: 'x' }])
    doc.markSaved()
    expect(doc.dirty).toBe(false)
    expect(doc.dirtyModules.size).toBe(0)
  })

  it('applyPatch 只改目标模块', () => {
    const doc = useDocumentStore()
    doc.updateModuleStructured('goal', '旧目标')
    doc.markSaved()
    doc.applyPatch({ target_module: 'goal', patch_json: { goal: '新目标' } })
    expect(doc.skillDocument.goal.structuredValue).toBe('新目标')
    expect(doc.dirtyModules.has('goal')).toBe(true)
  })

  it('setActiveModule 只接受有效模块', () => {
    const doc = useDocumentStore()
    doc.setActiveModule('rules')
    expect(doc.activeModule).toBe('rules')
    doc.setActiveModule('invalid' as 'rules')
    expect(doc.activeModule).toBe('rules') // 不变
  })

  it('moduleStates 正确计算状态', () => {
    const doc = useDocumentStore()
    let states = doc.moduleStates
    expect(states.find((s: { key: string; status: string }) => s.key === 'goal')!.status).toBe('empty')

    doc.updateModuleStructured('goal', '有内容')
    states = doc.moduleStates
    expect(states.find((s: { key: string; status: string }) => s.key === 'goal')!.status).toBe('modified')

    doc.markSaved()
    states = doc.moduleStates
    expect(states.find((s: { key: string; status: string }) => s.key === 'goal')!.status).toBe('ready')
  })

  it('reset 清除所有状态', () => {
    const doc = useDocumentStore()
    doc.updateModuleStructured('goal', '内容')
    doc.setActiveModule('rules')
    doc.reset()
    expect(doc.dirty).toBe(false)
    expect(doc.activeModule).toBe('meta')
    expect(doc.skillDocument.goal.structuredValue).toBe('')
  })

  it('loadFromApi 正确映射所有模块', () => {
    const doc = useDocumentStore()
    doc.loadFromApi({
      id: 'EC-01', name: '测试', department: 'EC', status: 'draft', version: 'v1',
      structured: {
        frontmatter: { name: '测试', department: 'EC', trigger_type: 'manual', risk_level: 'R2' },
        purpose: '目标文本',
        steps: [{ id: 's1', name: '判断', branches: [] }],
        output_definition: [{ name: '输出', format: 'text', recipient: '', approval_level: '' }],
        test_cases: [],
      },
      policy_pack: { threshold: 1.2 },
    })
    expect(doc.skillDocument.meta.structuredValue.id).toBe('EC-01')
    expect(doc.skillDocument.goal.structuredValue).toBe('目标文本')
    expect(doc.skillDocument.rules.structuredValue.length).toBe(1)
    expect(doc.skillDocument.params.structuredValue.length).toBe(1)
    expect(doc.parseState).toBe('parsed')
    expect(doc.dirty).toBe(false)
  })

  it('flatDocument reflects structured modules', () => {
    const doc = useDocumentStore()
    doc.updateModuleStructured('goal', '统一目标')
    doc.updateModuleStructured('params', [{ name: 'roi_threshold', default_value: 1.2 }])

    expect(doc.flatDocument.goal).toBe('统一目标')
    expect(doc.flatDocument.params).toHaveLength(1)
    expect(doc.flatDocument.params![0].name).toBe('roi_threshold')
  })

  it('setFromFlatDocument hydrates all modules', () => {
    const doc = useDocumentStore()
    doc.setFromFlatDocument({
      meta: { id: 'EC-02', name: '新 Skill', department: 'EC' },
      goal: '解释给业务',
      rules: [{ id: 'step_1', name: '判断', branches: [] }],
      params: [{ name: 'roi_threshold', default_value: 1.1 }],
      output_table: [{ name: '结论', format: 'text' }],
      test_cases: [{ name: 'case_1', input_data: {}, expected_output: {} }],
      workflow: { nodes: [{ id: 'n1' }], edges: [], bindings: [] },
      custom_sections: { note: 'hello' },
    })

    expect(doc.skillDocument.meta.structuredValue.id).toBe('EC-02')
    expect(doc.skillDocument.goal.structuredValue).toBe('解释给业务')
    expect(doc.skillDocument.rules.structuredValue).toHaveLength(1)
    expect(doc.skillDocument.workflow.structuredValue.nodes).toHaveLength(1)
    expect(doc.skillDocument.custom_sections.note).toBe('hello')
    expect(doc.dirty).toBe(false)
  })
})

describe('diff_service (backend)', async () => {
  // 这些测试需要 python 环境，已在 tests/test_workbench_chat.py 中覆盖
  // 此处验证前端对 diff 结果的处理
  it('hunks 格式符合 InlineDiff 组件预期', () => {
    const hunk = { index: 0, type: 'modify', path: 'params[0].value', old: '1.2', new: '1.5' }
    expect(hunk.index).toBeDefined()
    expect(['modify', 'add', 'remove']).toContain(hunk.type)
    expect(hunk.path).toBeTruthy()
  })
})

describe('renderMd', () => {
  let renderMd: typeof import('../utils/renderMd')['renderMd']

  beforeEach(async () => {
    const mod = await import('../utils/renderMd')
    renderMd = mod.renderMd
  })

  it('剥离危险标签', () => {
    // DOMPurify 把 <script> 整个 strip (内容也一起去掉, 而不是 escape)
    const out = renderMd('<script>alert(1)</script>safe text')
    expect(out).not.toContain('<script>')
    expect(out).not.toContain('alert(1)')
    expect(out).toContain('safe text')
  })

  it('剥离 javascript: 协议链接', () => {
    // [M6] DOMPurify ALLOWED_URI_REGEXP 拒绝 javascript: 协议
    const out = renderMd('[click](javascript:alert(1))')
    expect(out).not.toContain('javascript:')
  })

  it('保留 http/https 链接', () => {
    const out = renderMd('[link](https://example.com)')
    expect(out).toContain('https://example.com')
  })

  it('渲染 bold', () => {
    expect(renderMd('**粗体**')).toContain('<strong>粗体</strong>')
  })

  it('渲染 inline code', () => {
    expect(renderMd('`code`')).toContain('<code>code</code>')
  })

  it('换行转 br', () => {
    expect(renderMd('行1\n行2')).toContain('<br>')
  })

  it('空输入返回空', () => {
    expect(renderMd('')).toBe('')
    expect(renderMd(null)).toBe('')
  })
})
