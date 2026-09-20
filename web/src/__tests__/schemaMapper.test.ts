/**
 * schemaMapper + markdownParser 双向映射测试
 */
import { describe, it, expect } from 'vitest'
import type { SkillDocument, StructuredSkillResponse } from '@/types/skill'
import {
  toStructuredPayload,
  fromSkillResponse,
  inferOutputCapabilitiesFromContract,
} from '@/utils/schemaMapper'
import { documentToMarkdown, markdownToDocument } from '@/utils/markdownParser'

const sampleApiResponse = {
  id: 'EC-投放-01',
  name: '投放决策',
  department: 'EC',
  status: 'active',
  version: 'v1.0',
  structured: {
    frontmatter: { name: '投放决策', department: 'EC', trigger_type: 'schedule', risk_level: 'medium' },
    purpose: '根据ROI判断投放动作',
    steps: [{ id: 'step_1', name: '判断ROI', branches: [{ condition: 'ROI > 1.2', conclusion: '绿灯', action: '加预算', next_step: null }] }],
    output_definition: [{ name: '结论', format: 'text', recipient: '运营组', approval_level: '无' }],
    test_cases: [{ name: '标准测试', input_data: { roi: 1.5 }, expected_output: { conclusion: '绿灯' } }],
    custom_sections: {},
  },
  policy_pack: { roi_threshold: 1.2 },
} as StructuredSkillResponse

describe('schemaMapper', () => {
  describe('fromSkillResponse', () => {
    it('完整映射所有模块', () => {
      const doc = fromSkillResponse(sampleApiResponse)!
      expect(doc).not.toBeNull()
      expect(doc.meta!.name).toBe('投放决策')
      expect(doc.meta!.department).toBe('EC')
      expect(doc.goal).toBe('根据ROI判断投放动作')
      expect(doc.rules).toHaveLength(1)
      expect(doc.rules![0].id).toBe('step_1')
      expect(doc.output_table).toHaveLength(1)
      expect(doc.test_cases).toHaveLength(1)
      expect(doc.params).toHaveLength(1)
      expect(doc.params![0].name).toBe('roi_threshold')
    })

    it('null 输入返回 null', () => {
      expect(fromSkillResponse(null)).toBeNull()
    })

    it('从 contract.json 声明恢复待办模块', () => {
      const doc = fromSkillResponse({
        ...sampleApiResponse,
        other_files: {
          'contract.json': JSON.stringify({
            output_schema: {
              type: 'object',
              required: ['summary', 'reports', 'todos'],
              properties: {
                summary: { type: 'string' },
                reports: { type: 'array' },
                todos: { type: 'array' },
              },
            },
          }),
        },
      })!

      expect(doc.todos).toHaveLength(1)
      expect(doc.todos?.[0].title).toContain('output.todos')
      expect(doc.todos?.[0].tasks?.[0].content).toContain('todos[]')
      expect((doc.custom_sections?.__output_capabilities as Record<string, unknown>)?.reports).toBe(true)
      expect((doc.custom_sections?.__output_capabilities as Record<string, unknown>)?.todos).toBe(true)
    })

    it('从 contract.json 识别 reports 默认能力与 todos 声明', () => {
      const caps = inferOutputCapabilitiesFromContract({
        output_schema: {
          type: 'object',
          required: ['reports'],
          properties: {
            reports: { type: 'array' },
            todos: { type: 'array' },
          },
        },
      })

      expect(caps.reports).toBe(true)
      expect(caps.todos).toBe(true)
    })
  })

  describe('toStructuredPayload', () => {
    it('反向映射正确', () => {
      const doc = fromSkillResponse(sampleApiResponse)
      const payload = toStructuredPayload(doc)
      expect(payload.purpose).toBe('根据ROI判断投放动作')
      expect(payload.steps).toHaveLength(1)
      expect(payload.steps[0].id).toBe('step_1')
      expect(payload.output_definition).toHaveLength(1)
      expect(payload.test_cases).toHaveLength(1)
      expect(payload.frontmatter.name).toBe('投放决策')
    })

    it('null 输入返回空对象', () => {
      expect(toStructuredPayload(null)).toEqual({})
    })
  })
})

describe('markdownParser', () => {
  const sampleDoc: SkillDocument = {
    meta: { name: '测试Skill', department: 'EC', trigger_type: 'schedule', risk_level: 'R2' },
    goal: '判断投放效果',
    rules: [
      { id: 'step_1', name: '判断ROI', branches: [{ condition: 'ROI > 1.2', conclusion: '绿灯', action: '', next_step: null }] },
    ],
    params: [{ name: 'roi_threshold', default_value: 1.2 }],
    output_table: [{ name: '结论', format: 'text', recipient: '运营', approval_level: '无' }],
    test_cases: [{ name: '基本测试', input_data: { roi: 1.5 }, expected_output: { result: 'pass' } }],
    antipatterns: [],
    data_inputs: [],
    custom_sections: {},
  }

  describe('documentToMarkdown', () => {
    it('生成有效 markdown', () => {
      const md = documentToMarkdown(sampleDoc)
      expect(md).toContain('# 测试Skill')
      expect(md).toContain('## 目的')
      expect(md).toContain('判断投放效果')
      expect(md).toContain('## 执行步骤')
      expect(md).toContain('ROI > 1.2')
      expect(md).toContain('## 输出')
      expect(md).toContain('## 测试用例')
    })

    it('空文档不崩溃', () => {
      expect(documentToMarkdown(null)).toBe('')
      // 空对象仍生成标题行（符合 render 逻辑）
      expect(documentToMarkdown({})).toContain('Untitled Skill')
    })
  })

  describe('markdownToDocument', () => {
    it('解析 markdown 回结构化数据', () => {
      const md = documentToMarkdown(sampleDoc)
      const restored = markdownToDocument(md)
      expect(restored).not.toBeNull()
      expect(restored?.goal).toContain('判断投放效果')
      expect(restored?.rules?.length ?? 0).toBeGreaterThanOrEqual(1)
      expect(restored?.output_table?.length ?? 0).toBeGreaterThanOrEqual(1)
    })

    it('空输入返回 null', () => {
      expect(markdownToDocument('')).toBeNull()
      expect(markdownToDocument(null)).toBeNull()
    })
  })

  describe('roundtrip', () => {
    it('双向转换不丢关键数据', () => {
      const md = documentToMarkdown(sampleDoc)
      const restored = markdownToDocument(md)
      expect(restored?.goal).toContain('判断投放效果')
      // rules 的 step 名保留
      if (restored?.rules?.length) {
        expect(restored.rules[0]?.name || '').toContain('判断ROI')
      }
      // output_table 行数保留
      expect(restored?.output_table?.length).toBe(sampleDoc.output_table?.length)
    })
  })
})
