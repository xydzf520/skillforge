/**
 * skill-md 实时校验
 *
 * 在编辑器中标记 SKILL.md 格式错误，使用 Monaco markers 显示。
 * 前端轻量校验（不依赖后端），毫秒级响应。
 */

/**
 * 校验 SKILL.md 内容，返回 Monaco marker 数组
 *
 * @param {string} content - SKILL.md 全文
 * @param {object} monaco - Monaco 命名空间（用于 MarkerSeverity）
 * @returns {Array} Monaco IMarkerData 数组
 */
type MarkerLike = Record<string, any>

export function validateSkillMd(content: string, monaco: any): MarkerLike[] {
  const markers: MarkerLike[] = []
  const lines = content.split('\n')

  // 1. 检查 frontmatter
  validateFrontmatter(lines, markers, monaco)

  // 2. 检查必需章节
  validateRequiredSections(content, lines, markers, monaco)

  // 3. 检查决策树格式
  validateDecisionTree(lines, markers, monaco)

  // 4. 检查 step 引用
  validateStepReferences(lines, markers, monaco)

  // 5. 检查表格格式
  validateTables(lines, markers, monaco)

  return markers
}

// ── Frontmatter 校验 ──

function validateFrontmatter(lines: string[], markers: MarkerLike[], monaco: any): void {
  // 检查是否有 frontmatter
  if (lines[0]?.trim() !== '---') {
    markers.push({
      startLineNumber: 1, startColumn: 1,
      endLineNumber: 1, endColumn: lines[0]?.length + 1 || 1,
      message: '缺少 YAML frontmatter（应以 --- 开头）',
      severity: monaco.MarkerSeverity.Error,
      source: 'skill-md',
    })
    return
  }

  // 找到 frontmatter 结束
  let fmEnd = -1
  for (let i = 1; i < lines.length; i++) {
    if (lines[i].trim() === '---') {
      fmEnd = i
      break
    }
  }

  if (fmEnd === -1) {
    markers.push({
      startLineNumber: 1, startColumn: 1,
      endLineNumber: 1, endColumn: 4,
      message: 'frontmatter 未关闭（缺少结束 ---）',
      severity: monaco.MarkerSeverity.Error,
      source: 'skill-md',
    })
    return
  }

  // 检查必填字段
  const fmContent = lines.slice(1, fmEnd).join('\n')
  const requiredKeys = ['name', 'department', 'trigger_type', 'risk_level']
  requiredKeys.forEach(key => {
    const regex = new RegExp(`^${key}\\s*:`, 'm')
    if (!regex.test(fmContent)) {
      markers.push({
        startLineNumber: 1, startColumn: 1,
        endLineNumber: fmEnd + 1, endColumn: 4,
        message: `frontmatter 缺少必填字段: ${key}`,
        severity: monaco.MarkerSeverity.Warning,
        source: 'skill-md',
      })
    }
  })

  // 检查 trigger_type 枚举值
  const triggerMatch = fmContent.match(/^trigger_type\s*:\s*(.+)$/m)
  if (triggerMatch) {
    const value = triggerMatch[1].trim().replace(/['"]/g, '')
    if (!['manual', 'cron', 'event'].includes(value)) {
      const lineIndex = lines.findIndex(l => l.match(/^trigger_type\s*:/))
      markers.push({
        startLineNumber: lineIndex + 1, startColumn: 1,
        endLineNumber: lineIndex + 1, endColumn: lines[lineIndex].length + 1,
        message: `trigger_type 值无效: "${value}"（应为 manual / cron / event）`,
        severity: monaco.MarkerSeverity.Error,
        source: 'skill-md',
      })
    }
  }

  // 检查 risk_level 枚举值
  const riskMatch = fmContent.match(/^risk_level\s*:\s*(.+)$/m)
  if (riskMatch) {
    const value = riskMatch[1].trim().replace(/['"]/g, '')
    if (!['R1', 'R2', 'R3', 'R4'].includes(value)) {
      const lineIndex = lines.findIndex(l => l.match(/^risk_level\s*:/))
      markers.push({
        startLineNumber: lineIndex + 1, startColumn: 1,
        endLineNumber: lineIndex + 1, endColumn: lines[lineIndex].length + 1,
        message: `risk_level 值无效: "${value}"（应为 R1 / R2 / R3 / R4）`,
        severity: monaco.MarkerSeverity.Error,
        source: 'skill-md',
      })
    }
  }
}

// ── 必需章节校验 ──

function validateRequiredSections(content: string, lines: string[], markers: MarkerLike[], monaco: any): void {
  const requiredSections = ['目的', '执行步骤']
  requiredSections.forEach(section => {
    if (!content.includes(`## ${section}`)) {
      // 在文档末尾标记
      const lastLine = lines.length
      markers.push({
        startLineNumber: lastLine, startColumn: 1,
        endLineNumber: lastLine, endColumn: (lines[lastLine - 1]?.length || 0) + 1,
        message: `缺少必需章节: ## ${section}`,
        severity: monaco.MarkerSeverity.Warning,
        source: 'skill-md',
      })
    }
  })
}

// ── 决策树格式校验 ──

function validateDecisionTree(lines: string[], markers: MarkerLike[], monaco: any): void {
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const trimmed = line.trimStart()

    // 检查分支符号后是否有条件和结论
    if (trimmed.startsWith('├─') || trimmed.startsWith('└─')) {
      const branchContent = trimmed.replace(/^[├└]─\s*/, '')
      if (!branchContent.includes('→') && !branchContent.includes('->')) {
        markers.push({
          startLineNumber: i + 1, startColumn: 1,
          endLineNumber: i + 1, endColumn: line.length + 1,
          message: '决策分支缺少 → 符号（格式: 条件 → 结论）',
          severity: monaco.MarkerSeverity.Info,
          source: 'skill-md',
        })
      }
    }
  }
}

// ── Step 引用校验 ──

function validateStepReferences(lines: string[], markers: MarkerLike[], monaco: any): void {
  // 收集已定义的 step
  const definedSteps = new Set()
  const stepPattern = /^###\s+Step\s+(\w+)/i
  lines.forEach(line => {
    const match = line.match(stepPattern)
    if (match) {
      definedSteps.add(`step_${match[1]}`.toLowerCase())
      definedSteps.add(match[1].toLowerCase())
    }
  })

  // 检查 → 进入 引用的 step 是否存在
  // 支持格式：→ 进入 Step 2 / → 进入 step_2
  const gotoPattern = /→\s*进入\s+(?:Step\s+(\w+)|step_(\w+))/i
  lines.forEach((line, i) => {
    const match = line.match(gotoPattern)
    if (match) {
      const target = (match[1] || match[2] || '').toLowerCase()
      if (target && definedSteps.size > 0 && !definedSteps.has(target)) {
        markers.push({
          startLineNumber: i + 1, startColumn: line.indexOf(match[0]) + 1,
          endLineNumber: i + 1, endColumn: line.indexOf(match[0]) + match[0].length + 1,
          message: `引用的步骤 "${match[1] || match[2]}" 未定义`,
          severity: monaco.MarkerSeverity.Error,
          source: 'skill-md',
        })
      }
    }
  })
}

// ── 表格格式校验 ──

function validateTables(lines: string[], markers: MarkerLike[], monaco: any): void {
  let tableStart = -1
  let headerCols = 0

  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim()
    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      if (tableStart === -1) {
        tableStart = i
        headerCols = trimmed.split('|').length - 2 // 去掉首尾空
      } else {
        const cols = trimmed.split('|').length - 2
        if (cols !== headerCols && !trimmed.match(/^\|[\s\-:]+\|$/)) {
          markers.push({
            startLineNumber: i + 1, startColumn: 1,
            endLineNumber: i + 1, endColumn: lines[i].length + 1,
            message: `表格列数不一致（期望 ${headerCols} 列，实际 ${cols} 列）`,
            severity: monaco.MarkerSeverity.Warning,
            source: 'skill-md',
          })
        }
      }
    } else {
      tableStart = -1
      headerCols = 0
    }
  }
}
