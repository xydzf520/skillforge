/**
 * skill-md 自定义语言注册
 *
 * SKILL.md 是混合格式：YAML frontmatter + Markdown + 决策树 + 代码围栏
 * 使用 Monarch tokenizer 实现分层高亮
 */

export function registerSkillMdLanguage(monaco: any): void {
  monaco.languages.register({
    id: 'skill-md',
    extensions: ['.skill.md'],
    aliases: ['Skill Markdown', 'skill-md'],
  })

  monaco.languages.setLanguageConfiguration('skill-md', {
    brackets: [
      ['{', '}'],
      ['[', ']'],
      ['(', ')'],
    ],
    autoClosingPairs: [
      { open: '{', close: '}' },
      { open: '[', close: ']' },
      { open: '(', close: ')' },
      { open: '"', close: '"' },
      { open: "'", close: "'" },
      { open: '`', close: '`' },
    ],
    surroundingPairs: [
      { open: '{', close: '}' },
      { open: '[', close: ']' },
      { open: '(', close: ')' },
      { open: '"', close: '"' },
      { open: "'", close: "'" },
      { open: '`', close: '`' },
      { open: '*', close: '*' },
    ],
    folding: {
      markers: {
        start: /^#{1,6}\s/,
        end: /^(?=#{1,6}\s)/,
      },
    },
  })

  monaco.languages.setMonarchTokensProvider('skill-md', {
    defaultToken: '',

    tokenizer: {
      root: [
        // ── YAML frontmatter ──
        [/^---\s*$/, { token: 'meta.separator', next: '@frontmatter' }],

        // ── 代码围栏（必须在标题之前匹配）──
        [/^```\s*\w*\s*$/, { token: 'string.code-fence', next: '@codeblock' }],

        // ── Markdown 标题（最长匹配优先，### 在 ## 之前）──
        [/^(####\s+)(.+)$/, ['keyword.heading4', 'markup.heading4']],
        [/^(###\s+)(.+)$/, ['keyword.heading3', 'markup.heading3']],
        [/^(##\s+)(.+)$/, ['keyword.heading2', 'markup.heading2']],
        [/^(#\s+)(.+)$/, ['keyword.heading1', 'markup.heading1']],

        // ── Markdown 表格分隔行 ──
        [/^\|[\s\-:|]+\|$/, 'markup.table.separator'],
        // ── Markdown 表格内容行 ──
        [/^\|.+\|$/, 'markup.table'],

        // ── 决策树符号 ──
        [/[├└]─/, 'tree.branch'],
        [/│/, 'tree.pipe'],
        [/→\s*进入\s+(?:Step\s+\w+|step_\w+)/i, 'tree.goto'],
        [/→/, 'tree.arrow'],

        // ── 决策树关键词 ──
        [/(条件|动作|结论)\s*[:：]/, 'keyword.tree'],

        // ── Markdown 无序列表 ──
        [/^\s*[-*+]\s/, 'markup.list'],
        // ── Markdown 有序列表 ──
        [/^\s*\d+\.\s/, 'markup.list.ordered'],

        // ── 行内元素 ──
        [/\*\*[^*]+\*\*/, 'markup.bold'],
        [/\*[^*]+\*/, 'markup.italic'],
        [/`[^`]+`/, 'string.inline-code'],

        // ── 变量引用 {var_name} ──
        [/\{[\w.]+\}/, 'variable.ref'],

        // ── 风险等级 ──
        [/\bR[1-4]\b/, 'constant.risk'],

        // ── 数字 ──
        [/\b\d+(\.\d+)?\b/, 'number'],

        // ── URL ──
        [/https?:\/\/[^\s)]+/, 'string.link'],
      ],

      // ── YAML frontmatter 状态 ──
      frontmatter: [
        [/^---\s*$/, { token: 'meta.separator', next: '@pop' }],
        [/^(\w[\w_-]*)(\s*:\s*)/, ['type.yaml-key', 'delimiter']],
        [/"[^"]*"/, 'string.yaml'],
        [/'[^']*'/, 'string.yaml'],
        [/\b(true|false|null)\b/, 'constant.yaml'],
        [/\b\d+(\.\d+)?\b/, 'number.yaml'],
        [/\b(manual|cron|event)\b/, 'constant.enum'],
        [/\bR[1-4]\b/, 'constant.risk'],
        [/#.*$/, 'comment.yaml'],
        [/./, 'string.yaml-value'],
      ],

      // ── 代码块状态 ──
      // 关键：closing ``` 可能有前导空格，不能用 ^ 严格匹配
      codeblock: [
        [/^\s*```\s*$/, { token: 'string.code-fence', next: '@pop' }],
        // 代码块内的基本高亮
        [/#.*$/, 'comment.code'],               // 注释
        [/"[^"]*"/, 'string.code'],              // 字符串
        [/'[^']*'/, 'string.code'],              // 字符串
        [/\b\d+(\.\d+)?\b/, 'number.code'],     // 数字
        [/\b(true|false|null|None|True|False)\b/, 'constant.code'], // 常量
        [/\b(def|class|import|from|return|if|else|elif|for|while|in|not|and|or|try|except|with|as|async|await|SELECT|FROM|WHERE|JOIN|ON|ORDER|BY|INSERT|UPDATE|DELETE|POST|GET|PUT|PATCH|DELETE|Body|Header)\b/, 'keyword.code'], // 关键字
        [/[{}[\]()]/, 'bracket.code'],           // 括号
        [/[,;:]/, 'delimiter.code'],             // 分隔符
        [/\w+/, 'identifier.code'],              // 标识符
      ],
    },
  })
}
