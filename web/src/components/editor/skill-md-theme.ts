/**
 * skill-md 编辑器主题定义
 * 基于 VS Dark+ 风格，为 SKILL.md 所有元素定制高对比度颜色
 */

export function registerSkillMdTheme(monaco: any): void {
  monaco.editor.defineTheme('skill-dark', {
    base: 'vs-dark',
    inherit: true,
    rules: [
      // ── YAML frontmatter ──
      { token: 'meta.separator', foreground: '6A9955', fontStyle: 'bold' },
      { token: 'type.yaml-key', foreground: '9CDCFE' },
      { token: 'delimiter', foreground: 'D4D4D4' },
      { token: 'string.yaml', foreground: 'CE9178' },
      { token: 'string.yaml-value', foreground: 'CE9178' },
      { token: 'number.yaml', foreground: 'B5CEA8' },
      { token: 'constant.yaml', foreground: '569CD6', fontStyle: 'bold' },
      { token: 'constant.enum', foreground: '4EC9B0', fontStyle: 'bold' },
      { token: 'comment.yaml', foreground: '6A9955' },

      // ── Markdown 标题（高亮度） ──
      { token: 'keyword.heading1', foreground: '569CD6', fontStyle: 'bold' },
      { token: 'markup.heading1', foreground: 'DCDCAA', fontStyle: 'bold' },
      { token: 'keyword.heading2', foreground: '4FC1FF', fontStyle: 'bold' },
      { token: 'markup.heading2', foreground: 'DCDCAA', fontStyle: 'bold' },
      { token: 'keyword.heading3', foreground: '4EC9B0' },
      { token: 'markup.heading3', foreground: 'C8C8C8', fontStyle: 'bold' },
      { token: 'keyword.heading4', foreground: '6A9955' },
      { token: 'markup.heading4', foreground: 'C8C8C8' },

      // ── Markdown 文本格式 ──
      { token: 'markup.bold', foreground: 'E8E8E8', fontStyle: 'bold' },
      { token: 'markup.italic', foreground: 'C8C8C8', fontStyle: 'italic' },
      { token: 'markup.list', foreground: '569CD6' },
      { token: 'markup.list.ordered', foreground: '569CD6' },
      { token: 'markup.table', foreground: 'B5CEA8' },
      { token: 'markup.table.separator', foreground: '4E5969' },

      // ── 决策树 ──
      { token: 'tree.branch', foreground: 'C586C0', fontStyle: 'bold' },
      { token: 'tree.pipe', foreground: '6A6A6A' },
      { token: 'tree.arrow', foreground: 'C586C0', fontStyle: 'bold' },
      { token: 'tree.goto', foreground: '4EC9B0', fontStyle: 'bold' },
      { token: 'keyword.tree', foreground: 'C586C0', fontStyle: 'bold' },

      // ── 风险等级 ──
      { token: 'constant.risk', foreground: 'F48771', fontStyle: 'bold' },

      // ── 行内元素 ──
      { token: 'string.code-fence', foreground: '6A9955', fontStyle: 'bold' },
      { token: 'string.inline-code', foreground: 'CE9178' },
      { token: 'variable.ref', foreground: 'DCDCAA', fontStyle: 'italic' },
      { token: 'number', foreground: 'B5CEA8' },
      { token: 'string.link', foreground: '4FC1FF', fontStyle: 'underline' },

      // ── 代码块内 ──
      { token: 'comment.code', foreground: '6A9955', fontStyle: 'italic' },
      { token: 'string.code', foreground: 'CE9178' },
      { token: 'number.code', foreground: 'B5CEA8' },
      { token: 'constant.code', foreground: '569CD6', fontStyle: 'bold' },
      { token: 'keyword.code', foreground: 'C586C0' },
      { token: 'bracket.code', foreground: 'FFD700' },
      { token: 'delimiter.code', foreground: 'D4D4D4' },
      { token: 'identifier.code', foreground: '9CDCFE' },
    ],
    colors: {
      'editor.background': '#1E1E1E',
      'editor.foreground': '#D4D4D4',
      'editorLineNumber.foreground': '#858585',
      'editorLineNumber.activeForeground': '#C6C6C6',
      'editor.selectionBackground': '#264F78',
      'editor.lineHighlightBackground': '#2A2D2E',
      'editorCursor.foreground': '#AEAFAD',
      'editorSuggestWidget.background': '#252526',
      'editorSuggestWidget.border': '#454545',
      'editorSuggestWidget.selectedBackground': '#04395E',
    },
  })

  monaco.editor.defineTheme('skill-light', {
    base: 'vs',
    inherit: true,
    rules: [
      { token: 'meta.separator', foreground: '008000', fontStyle: 'bold' },
      { token: 'type.yaml-key', foreground: '0451A5' },
      { token: 'string.yaml', foreground: 'A31515' },
      { token: 'string.yaml-value', foreground: 'A31515' },
      { token: 'constant.yaml', foreground: '0000FF', fontStyle: 'bold' },
      { token: 'constant.enum', foreground: '267F99', fontStyle: 'bold' },
      { token: 'keyword.heading1', foreground: '0000FF', fontStyle: 'bold' },
      { token: 'markup.heading1', foreground: '795E26', fontStyle: 'bold' },
      { token: 'keyword.heading2', foreground: '0070C1', fontStyle: 'bold' },
      { token: 'markup.heading2', foreground: '795E26', fontStyle: 'bold' },
      { token: 'keyword.heading3', foreground: '267F99' },
      { token: 'markup.heading3', foreground: '333333', fontStyle: 'bold' },
      { token: 'markup.bold', foreground: '000000', fontStyle: 'bold' },
      { token: 'markup.list', foreground: '0451A5' },
      { token: 'markup.list.ordered', foreground: '0451A5' },
      { token: 'markup.table', foreground: '008000' },
      { token: 'tree.branch', foreground: 'AF00DB', fontStyle: 'bold' },
      { token: 'tree.arrow', foreground: 'AF00DB', fontStyle: 'bold' },
      { token: 'tree.goto', foreground: '267F99', fontStyle: 'bold' },
      { token: 'keyword.tree', foreground: 'AF00DB', fontStyle: 'bold' },
      { token: 'constant.risk', foreground: 'CD3131', fontStyle: 'bold' },
      { token: 'variable.ref', foreground: '795E26', fontStyle: 'italic' },
      { token: 'string.code-fence', foreground: '008000', fontStyle: 'bold' },
      { token: 'string.inline-code', foreground: 'A31515' },
      { token: 'string.link', foreground: '0070C1', fontStyle: 'underline' },
      { token: 'comment.code', foreground: '008000', fontStyle: 'italic' },
      { token: 'string.code', foreground: 'A31515' },
      { token: 'number.code', foreground: '098658' },
      { token: 'constant.code', foreground: '0000FF', fontStyle: 'bold' },
      { token: 'keyword.code', foreground: 'AF00DB' },
      { token: 'bracket.code', foreground: '795E26' },
      { token: 'identifier.code', foreground: '001080' },
    ],
    colors: {
      'editor.background': '#FFFFFF',
      'editor.foreground': '#333333',
    },
  })
}
