/**
 * 前端 SKILL.md 解析器 — 与后端 parser.py 对等。
 *
 * documentToMarkdown(): IDE skillDocument → SKILL.md 文本
 * markdownToDocument(): SKILL.md 文本 → IDE skillDocument（尽力解析，失败不丢内容）
 */

import type {
  SkillDocument,
  SkillRuleBranch,
  SkillRuleStep,
  SkillTestCase,
  SkillTodoSpec,
} from '@/types/skill'

// ═══ Render: 结构化 → Markdown ═══

const TODO_SECTION_TITLE = '待办'

export function serializeTodoSpecs(todos: SkillTodoSpec[] | null | undefined): string {
  const rows = (todos || []).filter((item) => item && (item.title || item.kind))
  if (!rows.length) return ''
  return [
    '```json',
    JSON.stringify(rows, null, 2),
    '```',
  ].join('\n')
}

export function parseTodoSpecs(content: string | null | undefined): SkillTodoSpec[] {
  if (!content) return []
  const jsonBlock = content.match(/```(?:json)?\s*([\s\S]*?)```/i)
  const raw = (jsonBlock ? jsonBlock[1] : content).trim()
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed)) return parsed.filter((item) => item && typeof item === 'object') as SkillTodoSpec[]
    if (parsed && typeof parsed === 'object') return [parsed as SkillTodoSpec]
  } catch {
    // 兼容手写表格，下面再尝试按表格解析。
  }
  return parseTable(raw, ['kind', 'title', 'summary', 'reviewer_role', 'sla_hours', 'decision_mode']) as SkillTodoSpec[]
}

/**
 * 将 IDE skillDocument 渲染为 SKILL.md 全文。
 * 与后端 SkillParser.render() 逻辑对等。
 */
export function documentToMarkdown(doc: SkillDocument | null | undefined): string {
  if (!doc) return ''
  const parts = []

  // 1. frontmatter
  const fm = doc.meta || {}
  if (fm.name || fm.department) {
    parts.push('---')
    const fmLines = []
    for (const [k, v] of Object.entries(fm)) {
      if (v !== undefined && v !== null && v !== '') {
        fmLines.push(`${k}: ${typeof v === 'string' && v.includes(':') ? `"${v}"` : v}`)
      }
    }
    parts.push(fmLines.join('\n'))
    parts.push('---')
    parts.push('')
  }

  // 2. 标题
  const name = fm.name || 'Untitled Skill'
  parts.push(`# ${name}`)
  parts.push('')

  // 3. 目的
  if (doc.goal) {
    parts.push('## 目的')
    parts.push('')
    parts.push(doc.goal.trim())
    parts.push('')
  }

  // 4. 执行步骤
  if (doc.rules?.length) {
    parts.push('## 执行步骤')
    parts.push('')
    for (const step of doc.rules) {
      parts.push(`### Step ${step.id || '1'}: ${step.name || ''}`)
      parts.push('')
      if (step.description) {
        parts.push(step.description)
        parts.push('')
      }
      const branches = step.branches || []
      for (let i = 0; i < branches.length; i++) {
        const b = branches[i]
        const connector = i === branches.length - 1 ? '└─' : '├─'
        let line = `  ${connector} ${b.condition || ''}`
        if (b.conclusion) line += ` → ${b.conclusion}`
        parts.push(line)
        if (b.action) parts.push(`      动作: ${b.action}`)
        if (b.next_step) parts.push(`      → 进入 Step ${b.next_step}`)
      }
      parts.push('')
    }
  }

  // 5. 反例（从 custom_sections 或 antipatterns）
  const antipatterns = doc.antipatterns || []
  if (antipatterns.length) {
    parts.push('## 反例')
    parts.push('')
    antipatterns.forEach((ap, i) => {
      parts.push(`${i + 1}. **误判场景**: ${ap.scenario || ''}`)
      parts.push(`   **正确做法**: ${ap.correct_action || ''}`)
      if (ap.source) parts.push(`   来源: ${ap.source}`)
      parts.push('')
    })
  }

  // 6. 输出定义
  if (doc.output_table?.length) {
    parts.push('## 输出')
    parts.push('')
    parts.push('| 输出项 | 格式 | 接收人 | 审批级别 |')
    parts.push('|---|---|---|---|')
    for (const item of doc.output_table) {
      parts.push(`| ${item.name || ''} | ${item.format || 'text'} | ${item.recipient || ''} | ${item.approval_level || ''} |`)
    }
    parts.push('')
  }

  // 7. 待办定义
  const todoMarkdown = serializeTodoSpecs(doc.todos)
  if (todoMarkdown) {
    parts.push(`## ${TODO_SECTION_TITLE}`)
    parts.push('')
    parts.push(todoMarkdown)
    parts.push('')
  }

  // 8. 数据输入
  if (doc.data_inputs?.length) {
    parts.push('## 数据输入')
    parts.push('')
    parts.push('| 数据名称 | 来源 | 刷新频率 |')
    parts.push('|---|---|---|')
    for (const di of doc.data_inputs) {
      parts.push(`| ${di.name || ''} | ${di.source || ''} | ${di.frequency || ''} |`)
    }
    parts.push('')
  }

  // 9. 测试用例
  if (doc.test_cases?.length) {
    parts.push('## 测试用例')
    parts.push('')
    for (const tc of doc.test_cases) {
      parts.push(`### ${tc.name || '测试'}`)
      parts.push('')
      if (tc.input_data) {
        parts.push('**输入:**')
        parts.push('```json')
        parts.push(typeof tc.input_data === 'string' ? tc.input_data : JSON.stringify(tc.input_data, null, 2))
        parts.push('```')
        parts.push('')
      }
      if (tc.expected_output) {
        parts.push('**期望输出:**')
        parts.push('```json')
        parts.push(typeof tc.expected_output === 'string' ? tc.expected_output : JSON.stringify(tc.expected_output, null, 2))
        parts.push('```')
        parts.push('')
      }
    }
  }

  // 10. 自定义章节
  if (doc.custom_sections) {
    for (const [title, body] of Object.entries(doc.custom_sections)) {
      if (title === name) continue
      if (title === TODO_SECTION_TITLE) continue
      if (title.startsWith('__')) continue
      parts.push(`## ${title}`)
      parts.push('')
      if (body) {
        parts.push(body)
        parts.push('')
      }
    }
  }

  return parts.join('\n')
}


// ═══ Parse: Markdown → 结构化 ═══

/**
 * 将 SKILL.md 文本解析为 IDE skillDocument 格式。
 * 尽力解析，解析失败的章节保留为 custom_sections，不丢内容。
 */
export function markdownToDocument(markdown: string | null | undefined): SkillDocument | null {
  if (!markdown) return null

  const doc: SkillDocument & { meta: Record<string, string> } = {
    meta: {},
    goal: '',
    rules: [],
    params: [],
    output_table: [],
    test_cases: [],
    antipatterns: [],
    data_inputs: [],
    todos: [],
    workflow: {},
    custom_sections: {},
  }

  // 1. 提取 frontmatter
  const fmMatch = markdown.match(/^---\s*\n([\s\S]*?)\n---\s*\n/)
  if (fmMatch) {
    try {
      const lines = fmMatch[1].split('\n')
      for (const line of lines) {
        const idx = line.indexOf(':')
        if (idx > 0) {
          const key = line.slice(0, idx).trim()
          let val = line.slice(idx + 1).trim()
          // 去掉引号
          if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
            val = val.slice(1, -1)
          }
          doc.meta[key] = val
        }
      }
    } catch { /* 保持空 meta */ }
  }

  // 2. 按 ## 拆分章节
  const bodyStart = fmMatch ? fmMatch[0].length : 0
  const body = markdown.slice(bodyStart)
  const sections: Record<string, string> = {}
  const sectionRegex = /^## (.+)$/gm
  let lastTitle = null
  let lastStart = 0
  let match

  while ((match = sectionRegex.exec(body)) !== null) {
    if (lastTitle !== null) {
      sections[lastTitle] = body.slice(lastStart, match.index).trim()
    }
    lastTitle = match[1].trim()
    lastStart = match.index + match[0].length
  }
  if (lastTitle !== null) {
    sections[lastTitle] = body.slice(lastStart).trim()
  }

  // 3. 映射各章节
  const sectionAliases: Record<string, string> = {
    '目的': 'purpose',
    '执行步骤': 'steps', '判断逻辑': 'steps', '决策步骤': 'steps', '决策逻辑': 'steps',
    '反例': 'antipatterns', '误判': 'antipatterns',
    '输出': 'output',
    [TODO_SECTION_TITLE]: 'todos', 'Todo': 'todos', 'Todos': 'todos', '待办输出': 'todos',
    '数据输入': 'data_inputs',
    '测试用例': 'test_cases', '测试': 'test_cases',
  }

  const knownKeys = new Set()

  for (const [title, content] of Object.entries(sections)) {
    const alias = sectionAliases[title]
    if (!alias) continue
    knownKeys.add(title)

    switch (alias) {
      case 'purpose':
        doc.goal = content
        break
      case 'steps':
        doc.rules = parseSteps(content)
        break
      case 'antipatterns':
        doc.antipatterns = parseAntipatterns(content)
        break
      case 'output':
        doc.output_table = parseTable(content, ['name', 'format', 'recipient', 'approval_level'])
        break
      case 'todos':
        doc.todos = parseTodoSpecs(content)
        break
      case 'data_inputs':
        doc.data_inputs = parseTable(content, ['name', 'source', 'frequency'])
        break
      case 'test_cases':
        doc.test_cases = parseTestCases(content)
        break
    }
  }

  // 4. 未识别章节保留为 custom_sections
  for (const [title, content] of Object.entries(sections)) {
    if (!knownKeys.has(title) && title !== (doc.meta as Record<string, any>).name) {
      ;(doc.custom_sections as Record<string, any>)[title] = content
    }
  }

  return doc
}


// ── 辅助解析函数 ──

function parseSteps(content: string): SkillRuleStep[] {
  if (!content) return []
  const steps: SkillRuleStep[] = []
  const stepRegex = /^### (?:Step\s+)?(\w+)(?::\s*|\s*[:：]\s*)(.*)$/gm
  let match: RegExpExecArray | null
  const stepMatches: Array<{ id: string; name: string; index: number }> = []

  while ((match = stepRegex.exec(content)) !== null) {
    stepMatches.push({ id: match[1], name: match[2].trim(), index: match.index + match[0].length })
  }

  for (let i = 0; i < stepMatches.length; i++) {
    const sm = stepMatches[i]
    // 下一个 step 的 ### 位置作为截止点（精确匹配，不用 magic number）
    const end = i + 1 < stepMatches.length ? content.lastIndexOf('\n', stepMatches[i + 1].index) : content.length
    const block = content.slice(sm.index, end > sm.index ? end : content.length).trim()
    const branches = parseBranches(block)
    steps.push({ id: sm.id, name: sm.name, description: '', branches })
  }

  // 如果没有 ### 子标题，尝试直接解析分支
  if (!steps.length && content.trim()) {
    const branches = parseBranches(content)
    if (branches.length) {
      steps.push({ id: 'step_1', name: '判断', description: '', branches })
    }
  }

  return steps
}

function parseBranches(block: string): SkillRuleBranch[] {
  const branches: SkillRuleBranch[] = []
  const lines = block.split('\n')
  for (const line of lines) {
    const m = line.match(/[├└]─\s*(.+?)(?:\s*→\s*(.+))?$/)
    if (m) {
      branches.push({
        condition: m[1].trim(),
        conclusion: m[2]?.trim() || '',
        action: '',
        next_step: null,
      })
    }
  }
  // 检查后续行中的动作和 next_step
  for (let i = 0; i < lines.length; i++) {
    const actionMatch = lines[i].match(/^\s+动作:\s*(.+)/)
    if (actionMatch && branches.length) {
      branches[branches.length - 1].action = actionMatch[1].trim()
    }
    const nextMatch = lines[i].match(/→\s*进入\s*Step\s*(\w+)/)
    if (nextMatch && branches.length) {
      branches[branches.length - 1].next_step = nextMatch[1]
    }
  }
  return branches
}

function parseAntipatterns(content: string): Array<Record<string, string>> {
  if (!content) return []
  const items: Array<Record<string, string>> = []
  const regex = /\*\*误判场景\*\*:\s*(.+)/g
  let m: RegExpExecArray | null
  while ((m = regex.exec(content)) !== null) {
    const scenario = m[1].trim()
    // 查找后续的"正确做法"
    const afterIdx = m.index + m[0].length
    const rest = content.slice(afterIdx, afterIdx + 200)
    const actionMatch = rest.match(/\*\*正确做法\*\*:\s*(.+)/)
    items.push({
      scenario,
      correct_action: actionMatch ? actionMatch[1].trim() : '',
      source: '',
    })
  }
  return items
}

function parseTable(content: string, columns: string[]): Array<Record<string, string>> {
  if (!content) return []
  const rows: Array<Record<string, string>> = []
  const lines = content.split('\n').filter(l => l.includes('|'))
  // 跳过表头和分隔行
  const dataLines = lines.filter(l => !l.match(/^\s*\|?\s*[-:]+\s*\|/) && !l.match(/^\s*\|?\s*(?:输出项|数据名称)/))
  for (const line of dataLines) {
    // 处理转义管道符 \|，先替换再拆分再还原
    const cells = line.replace(/\\\|/g, '\x00').split('|').map(c => c.replace(/\x00/g, '|').trim()).filter(Boolean)
    if (cells.length >= columns.length - 1) {
      const row: Record<string, string> = {}
      columns.forEach((col, i) => { row[col] = cells[i] || '' })
      if (row[columns[0]]) rows.push(row)
    }
  }
  return rows
}

function parseTestCases(content: string): SkillTestCase[] {
  if (!content) return []
  const cases: SkillTestCase[] = []
  // 按 ### 拆分
  const blocks = content.split(/^### /m).filter(Boolean)
  for (const block of blocks) {
    const nameEnd = block.indexOf('\n')
    const name = block.slice(0, nameEnd > 0 ? nameEnd : undefined).trim()
    const body = nameEnd > 0 ? block.slice(nameEnd) : ''

    let input_data = {}
    let expected_output = {}

    // 提取 JSON 代码块
    const jsonBlocks: any[] = []
    const jsonRegex = /```json\s*\n([\s\S]*?)```/g
    let jm: RegExpExecArray | null
    while ((jm = jsonRegex.exec(body)) !== null) {
      try { jsonBlocks.push(JSON.parse(jm[1])) } catch { /* skip */ }
    }

    if (jsonBlocks.length >= 2) {
      input_data = jsonBlocks[0]
      expected_output = jsonBlocks[1]
    } else if (jsonBlocks.length === 1) {
      // 尝试根据上下文判断
      if (body.indexOf('输入') < body.indexOf('期望')) {
        input_data = jsonBlocks[0]
      } else {
        expected_output = jsonBlocks[0]
      }
    }

    if (name) {
      cases.push({ name, input_data, expected_output, assert_rules: [] })
    }
  }
  return cases
}
