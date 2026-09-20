/**
 * skill-md 结构化 Snippet 补全
 *
 * 提供 frontmatter 键名、决策树模板、反例模板、输出表格等自动补全
 */

export function registerSkillMdCompletions(monaco: any): void {
  monaco.languages.registerCompletionItemProvider('skill-md', {
    triggerCharacters: ['-', '#', '|', '{', ':'],

    provideCompletionItems(model: any, position: any) {
      const lineContent = model.getLineContent(position.lineNumber)
      const textUntilPosition = model.getValueInRange({
        startLineNumber: 1,
        startColumn: 1,
        endLineNumber: position.lineNumber,
        endColumn: position.column,
      })

      const word = model.getWordUntilPosition(position)
      const range = {
        startLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endLineNumber: position.lineNumber,
        endColumn: word.endColumn,
      }

      const suggestions = []

      // 检测是否在 frontmatter 内
      const inFrontmatter = isInFrontmatter(textUntilPosition)

      if (inFrontmatter) {
        suggestions.push(...getFrontmatterCompletions(monaco, range))
      } else {
        suggestions.push(...getMarkdownCompletions(monaco, range, lineContent))
        suggestions.push(...getTreeCompletions(monaco, range, lineContent))
        suggestions.push(...getTemplateCompletions(monaco, range, lineContent))
      }

      return { suggestions }
    },
  })
}

// ── 判断光标是否在 frontmatter 内 ──

function isInFrontmatter(text: string): boolean {
  const parts = text.split('---')
  // 在第一个 --- 和第二个 --- 之间
  return parts.length === 2 && text.startsWith('---')
}

// ── Frontmatter 键名补全 ──

function getFrontmatterCompletions(monaco: any, range: any): any[] {
  const keys = [
    { key: 'name', desc: 'Skill 名称', value: 'name: ${1:skill_name}' },
    { key: 'department', desc: '所属部门', value: 'department: ${1:部门名}' },
    { key: 'role', desc: '岗位角色', value: 'role: ${1:角色名}' },
    { key: 'trigger_type', desc: '触发类型', value: 'trigger_type: ${1|manual,cron,event|}' },
    { key: 'trigger_expression', desc: 'Cron 表达式', value: 'trigger_expression: "${1:0 9 * * 1-5}"' },
    { key: 'risk_level', desc: '风险等级', value: 'risk_level: ${1|R1,R2,R3,R4|}' },
    { key: 'approval_level', desc: '审批级别', value: 'approval_level: ${1|none,lead,director|}' },
    { key: 'reviewer', desc: '待办接收人', value: 'reviewer:\n  - ${1:user_a}\n  - ${2:user_b}' },
    { key: 'reviewer_role', desc: '按角色派发待办', value: 'reviewer_role: ${1|operator,biz_owner,ai_engineer,director|}' },
    { key: 'decision_mode', desc: '多 reviewer 聚合模式', value: 'decision_mode: ${1|any_of,all_of,independent|}' },
    { key: 'version', desc: '版本号', value: 'version: ${1:1.0}' },
    { key: 'author', desc: '作者', value: 'author: ${1:author_name}' },
    { key: 'description', desc: 'Skill 描述', value: 'description: "${1:一句话描述}"' },
    { key: 'tags', desc: '标签列表', value: 'tags:\n  - ${1:tag1}\n  - ${2:tag2}' },
    { key: 'timeout', desc: '超时秒数', value: 'timeout: ${1:300}' },
    { key: 'retry', desc: '重试次数', value: 'retry: ${1:3}' },
    { key: 'priority', desc: '优先级', value: 'priority: ${1|high,medium,low|}' },
  ]

  return keys.map(k => ({
    label: k.key,
    kind: monaco.languages.CompletionItemKind.Property,
    insertText: k.value,
    insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
    detail: k.desc,
    documentation: `SKILL.md frontmatter 字段: ${k.key}`,
    range,
  }))
}

// ── Markdown 章节补全 ──

function getMarkdownCompletions(monaco: any, range: any, lineContent: string): any[] {
  if (!lineContent.trimStart().startsWith('#')) return []

  const sections = [
    { label: '## 目的', text: '## 目的\n\n${1:描述该 Skill 的核心目的和价值}', desc: 'Purpose 章节' },
    { label: '## 执行步骤', text: '## 执行步骤\n\n### Step 1: ${1:步骤名称}\n${2:步骤描述}\n├─ ${3:条件A} → ${4:结论A}\n│  动作: ${5:执行动作}\n└─ ${6:条件B} → ${7:结论B}', desc: '决策树章节' },
    { label: '## 反例', text: '## 反例\n\n1. **${1:误判场景}**\n   **正确做法**: ${2:应该怎么做}\n   来源: ${3:case-001}', desc: '反例章节' },
    { label: '## 输出', text: '## 输出\n\n| 输出项 | 格式 | 接收人 | 审批级别 |\n|---|---|---|---|\n| ${1:报告名} | ${2:钉钉卡片} | ${3:部门主管} | ${4:none} |', desc: '输出定义章节' },
    { label: '## 数据输入', text: '## 数据输入\n\n| 数据名称 | 来源 | 刷新频率 |\n|---|---|---|\n| ${1:数据名} | ${2:API} | ${3:每日} |', desc: '数据输入章节' },
    { label: '## 测试用例', text: '## 测试用例\n\n### ${1:测试用例名}\n**输入:**\n```json\n{\n  "${2:key}": "${3:value}"\n}\n```\n**期望输出:**\n```json\n{\n  "${4:result}": "${5:expected}"\n}\n```\n**断言:**\n- ${6:断言规则}', desc: '测试用例章节' },
  ]

  return sections.map(s => ({
    label: s.label,
    kind: monaco.languages.CompletionItemKind.Struct,
    insertText: s.text,
    insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
    detail: s.desc,
    documentation: `插入 SKILL.md ${s.desc}模板`,
    range,
  }))
}

// ── 决策树补全 ──

function getTreeCompletions(monaco: any, range: any, lineContent: string): any[] {
  const trimmed = lineContent.trimStart()
  // 在行首或 ├ └ 开头时提供补全
  if (trimmed && !trimmed.startsWith('├') && !trimmed.startsWith('└') && !trimmed.startsWith('│') && trimmed.length > 3) {
    return []
  }

  return [
    {
      label: '├─ 条件分支',
      kind: monaco.languages.CompletionItemKind.Snippet,
      insertText: '├─ ${1:条件表达式} → ${2:结论}\n│  动作: ${3:执行动作}',
      insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
      detail: '添加一个条件分支（非末尾）',
      range,
    },
    {
      label: '└─ 条件分支（末尾）',
      kind: monaco.languages.CompletionItemKind.Snippet,
      insertText: '└─ ${1:条件表达式} → ${2:结论}\n   动作: ${3:执行动作}',
      insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
      detail: '添加最后一个条件分支',
      range,
    },
    {
      label: '→ 进入下一步',
      kind: monaco.languages.CompletionItemKind.Snippet,
      insertText: '→ 进入 Step ${1:2}',
      insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
      detail: '跳转到下一步骤',
      range,
    },
    {
      label: '### Step N: 新步骤',
      kind: monaco.languages.CompletionItemKind.Snippet,
      insertText: '### Step ${1:N}: ${2:步骤名称}\n${3:步骤描述}\n├─ ${4:条件A} → ${5:绿灯}\n│  动作: ${6:可考虑加预算}\n└─ ${7:条件B} → ${8:红灯}\n   动作: ${9:暂停投放}',
      insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
      detail: '插入完整的决策步骤',
      range,
    },
  ]
}

// ── 模板补全 ──

function getTemplateCompletions(monaco: any, range: any, lineContent: string): any[] {
  const suggestions: any[] = []

  // 变量引用补全
  if (lineContent.includes('{')) {
    const commonVars = [
      'roi_green_ratio', 'roi_red_ratio', 'budget_max', 'budget_min',
      'cpa_threshold', 'roas_target', 'click_rate_min', 'conversion_rate_min',
      'spend_daily_limit', 'impression_min',
    ]
    commonVars.forEach(v => {
      suggestions.push({
        label: `{${v}}`,
        kind: monaco.languages.CompletionItemKind.Variable,
        insertText: `{${v}}`,
        detail: '参数变量引用',
        range,
      })
    })
  }

  // Markdown 表格行补全
  if (lineContent.startsWith('|')) {
    suggestions.push({
      label: '| 新表格行 |',
      kind: monaco.languages.CompletionItemKind.Snippet,
      insertText: '| ${1:列1} | ${2:列2} | ${3:列3} | ${4:列4} |',
      insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
      detail: '添加表格行',
      range,
    })
  }

  // 反例条目补全
  suggestions.push({
    label: '反例条目',
    kind: monaco.languages.CompletionItemKind.Snippet,
    insertText: '${1:N}. **${2:误判场景描述}**\n   **正确做法**: ${3:应该执行的操作}\n   来源: ${4:case-id}',
    insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
    detail: '插入一条反例',
    range,
  })

  // 完整 frontmatter 模板
  suggestions.push({
    label: '--- frontmatter 模板 ---',
    kind: monaco.languages.CompletionItemKind.Snippet,
    insertText: [
      '---',
      'name: ${1:skill_name}',
      'department: ${2:部门名}',
      'role: ${3:岗位角色}',
      'trigger_type: ${4|manual,cron,event|}',
      'trigger_expression: "${5:0 9 * * 1-5}"',
      'risk_level: ${6|R1,R2,R3,R4|}',
      'approval_level: ${7|none,lead,director|}',
      'reviewer:',
      '  - ${8:user_a}',
      '  - ${9:user_b}',
      'decision_mode: ${10|any_of,all_of,independent|}',
      '---',
    ].join('\n'),
    insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
    detail: '插入完整的 YAML frontmatter',
    range,
  })

  return suggestions
}
