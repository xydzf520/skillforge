import { describe, expect, it } from 'vitest'
import {
  buildInitialParams,
  flattenNestedSchema,
  generatePreviewData,
  getNestedValue,
  isFieldVisible,
  normalizeParamsForSubmit,
  setNestedValue,
  type PortalParamSchema,
} from '@/pages/portal/shared'

// ---------------------------------------------------------------------------
// 条件联动 - isFieldVisible
// ---------------------------------------------------------------------------

describe('isFieldVisible (条件联动)', () => {
  const schema: PortalParamSchema = {
    type: 'object',
    properties: {
      mode: { type: 'string', enum: ['simple', 'advanced'] },
      threshold: {
        type: 'number',
        dependsOn: { field: 'mode', value: 'advanced' },
      },
      label: {
        type: 'string',
        dependsOn: { field: 'mode', value: 'simple', operator: 'ne' },
      },
      channel: {
        type: 'string',
        dependsOn: { field: 'mode', values: ['advanced', 'expert'], operator: 'in' },
      },
      notes: {
        type: 'string',
        dependsOn: { field: 'mode', operator: 'notEmpty' },
      },
      always: { type: 'string' },
    },
  }

  it('无 dependsOn 的字段始终可见', () => {
    expect(isFieldVisible('always', schema, { mode: '' })).toBe(true)
    expect(isFieldVisible('always', schema, {})).toBe(true)
  })

  it('eq 操作符：值匹配时可见', () => {
    expect(isFieldVisible('threshold', schema, { mode: 'advanced' })).toBe(true)
    expect(isFieldVisible('threshold', schema, { mode: 'simple' })).toBe(false)
  })

  it('ne 操作符：值不匹配时可见', () => {
    expect(isFieldVisible('label', schema, { mode: 'advanced' })).toBe(true)
    expect(isFieldVisible('label', schema, { mode: 'simple' })).toBe(false)
  })

  it('in 操作符：值在列表中时可见', () => {
    expect(isFieldVisible('channel', schema, { mode: 'advanced' })).toBe(true)
    expect(isFieldVisible('channel', schema, { mode: 'expert' })).toBe(true)
    expect(isFieldVisible('channel', schema, { mode: 'simple' })).toBe(false)
  })

  it('notEmpty 操作符：值非空时可见', () => {
    expect(isFieldVisible('notes', schema, { mode: 'anything' })).toBe(true)
    expect(isFieldVisible('notes', schema, { mode: '' })).toBe(false)
    expect(isFieldVisible('notes', schema, {})).toBe(false)
  })

  it('未知字段名始终返回 true', () => {
    expect(isFieldVisible('nonexistent', schema, {})).toBe(true)
  })

  it('schema 为 undefined 时始终可见', () => {
    expect(isFieldVisible('anything', undefined, {})).toBe(true)
  })

  it('dependsOn 支持嵌套路径', () => {
    const nestedSchema: PortalParamSchema = {
      type: 'object',
      properties: {
        config: {
          type: 'object',
          properties: { level: { type: 'string' } },
        },
        detail: {
          type: 'string',
          dependsOn: { field: 'config.level', value: 'high' },
        },
      },
    }
    expect(isFieldVisible('detail', nestedSchema, { config: { level: 'high' } })).toBe(true)
    expect(isFieldVisible('detail', nestedSchema, { config: { level: 'low' } })).toBe(false)
    expect(isFieldVisible('detail', nestedSchema, {})).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// 嵌套结构 - flattenNestedSchema
// ---------------------------------------------------------------------------

describe('flattenNestedSchema (嵌套展平)', () => {
  it('展平简单扁平 schema', () => {
    const schema: PortalParamSchema = {
      type: 'object',
      properties: {
        name: { type: 'string' },
        age: { type: 'integer' },
      },
    }
    const { flatFields } = flattenNestedSchema(schema)
    expect(flatFields).toHaveLength(2)
    expect(flatFields[0]).toEqual({ path: 'name', field: schema.properties!.name, depth: 0 })
    expect(flatFields[1]).toEqual({ path: 'age', field: schema.properties!.age, depth: 0 })
  })

  it('展平嵌套对象', () => {
    const schema: PortalParamSchema = {
      type: 'object',
      properties: {
        address: {
          type: 'object',
          title: '地址',
          properties: {
            city: { type: 'string', title: '城市' },
            zip: { type: 'string', title: '邮编' },
          },
        },
      },
    }
    const { flatFields } = flattenNestedSchema(schema)
    expect(flatFields).toHaveLength(3)
    expect(flatFields[0].path).toBe('address')
    expect(flatFields[0].depth).toBe(0)
    expect(flatFields[1].path).toBe('address.city')
    expect(flatFields[1].depth).toBe(1)
    expect(flatFields[2].path).toBe('address.zip')
    expect(flatFields[2].depth).toBe(1)
  })

  it('展平数组项中的对象', () => {
    const schema: PortalParamSchema = {
      type: 'object',
      properties: {
        items: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              name: { type: 'string' },
              qty: { type: 'integer' },
            },
          },
        },
      },
    }
    const { flatFields } = flattenNestedSchema(schema)
    expect(flatFields).toHaveLength(3)
    expect(flatFields[0].path).toBe('items')
    expect(flatFields[1].path).toBe('items[].name')
    expect(flatFields[1].depth).toBe(1)
    expect(flatFields[2].path).toBe('items[].qty')
    expect(flatFields[2].depth).toBe(1)
  })

  it('空 properties 返回空列表', () => {
    const { flatFields } = flattenNestedSchema({ type: 'object' })
    expect(flatFields).toEqual([])
  })

  it('malformed properties 不会被展开成前端字段', () => {
    const schema = {
      type: 'object',
      properties: {
        filters: {
          type: 'object',
          properties: [],
        },
        items: {
          type: 'array',
          items: {
            type: 'object',
            properties: [],
          },
        },
      },
    } as any

    const { flatFields } = flattenNestedSchema(schema)
    expect(flatFields.map(field => field.path)).toEqual(['filters', 'items'])
  })
})

// ---------------------------------------------------------------------------
// 嵌套值读写 - getNestedValue / setNestedValue
// ---------------------------------------------------------------------------

describe('getNestedValue / setNestedValue', () => {
  it('读取顶层值', () => {
    expect(getNestedValue({ a: 1 }, 'a')).toBe(1)
  })

  it('读取嵌套值', () => {
    expect(getNestedValue({ a: { b: { c: 42 } } }, 'a.b.c')).toBe(42)
  })

  it('路径不存在返回 undefined', () => {
    expect(getNestedValue({}, 'a.b')).toBeUndefined()
    expect(getNestedValue({ a: 1 }, 'a.b')).toBeUndefined()
  })

  it('设置顶层值', () => {
    const obj: Record<string, unknown> = {}
    setNestedValue(obj, 'x', 10)
    expect(obj.x).toBe(10)
  })

  it('设置嵌套值（自动创建中间节点）', () => {
    const obj: Record<string, unknown> = {}
    setNestedValue(obj, 'a.b.c', 'deep')
    expect((obj as any).a.b.c).toBe('deep')
  })

  it('覆盖已有嵌套值', () => {
    const obj: Record<string, unknown> = { a: { b: 1 } }
    setNestedValue(obj, 'a.b', 2)
    expect((obj as any).a.b).toBe(2)
  })
})

// ---------------------------------------------------------------------------
// 表单预览数据生成 - generatePreviewData
// ---------------------------------------------------------------------------

describe('generatePreviewData (预览数据生成)', () => {
  it('使用 default 值', () => {
    const schema: PortalParamSchema = {
      type: 'object',
      properties: {
        name: { type: 'string', default: 'test' },
        count: { type: 'integer', default: 5 },
      },
    }
    const data = generatePreviewData(schema)
    expect(data.name).toBe('test')
    expect(data.count).toBe(5)
  })

  it('使用 enum 第一个值', () => {
    const schema: PortalParamSchema = {
      type: 'object',
      properties: {
        color: { type: 'string', enum: ['red', 'blue', 'green'] },
      },
    }
    const data = generatePreviewData(schema)
    expect(data.color).toBe('red')
  })

  it('按 type 生成示例值', () => {
    const schema: PortalParamSchema = {
      type: 'object',
      properties: {
        s: { type: 'string', title: '名称' },
        i: { type: 'integer' },
        n: { type: 'number' },
        b: { type: 'boolean' },
        arr: { type: 'array' },
      },
    }
    const data = generatePreviewData(schema)
    expect(data.s).toBe('名称示例')
    expect(data.i).toBe(1)
    expect(data.n).toBe(0.0)
    expect(data.b).toBe(false)
    expect(data.arr).toEqual([])
  })

  it('string + date 格式生成日期', () => {
    const schema: PortalParamSchema = {
      type: 'object',
      properties: {
        date_field: { type: 'string', format: 'date' },
      },
    }
    const data = generatePreviewData(schema)
    expect(typeof data.date_field).toBe('string')
    expect(data.date_field).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('嵌套对象递归生成', () => {
    const schema: PortalParamSchema = {
      type: 'object',
      properties: {
        config: {
          type: 'object',
          properties: {
            level: { type: 'integer', default: 3 },
            name: { type: 'string', title: '配置名' },
          },
        },
      },
    }
    const data = generatePreviewData(schema)
    expect(data.config).toEqual({ level: 3, name: '配置名示例' })
  })

  it('数组+items 生成带示例元素的数组', () => {
    const schema: PortalParamSchema = {
      type: 'object',
      properties: {
        tags: {
          type: 'array',
          items: { type: 'string', title: '标签' },
        },
      },
    }
    const data = generatePreviewData(schema)
    expect(Array.isArray(data.tags)).toBe(true)
    expect((data.tags as string[])).toEqual(['标签示例'])
  })

  it('minimum 值用作数字示例', () => {
    const schema: PortalParamSchema = {
      type: 'object',
      properties: {
        ratio: { type: 'number', minimum: 0.5 },
        count: { type: 'integer', minimum: 10 },
      },
    }
    const data = generatePreviewData(schema)
    expect(data.ratio).toBe(0.5)
    expect(data.count).toBe(10)
  })

  it('空 properties 返回空对象', () => {
    expect(generatePreviewData({ type: 'object' })).toEqual({})
  })
})

// ---------------------------------------------------------------------------
// buildInitialParams 嵌套支持
// ---------------------------------------------------------------------------

describe('buildInitialParams (嵌套支持)', () => {
  it('为嵌套对象递归构建初始值', () => {
    const schema: PortalParamSchema = {
      type: 'object',
      properties: {
        name: { type: 'string', default: 'hello' },
        config: {
          type: 'object',
          properties: {
            enabled: { type: 'boolean' },
            count: { type: 'integer', default: 3 },
          },
        },
      },
    }
    const params = buildInitialParams(schema)
    expect(params.name).toBe('hello')
    expect(params.config).toEqual({ enabled: false, count: 3 })
  })
})

// ---------------------------------------------------------------------------
// normalizeParamsForSubmit 嵌套支持
// ---------------------------------------------------------------------------

describe('normalizeParamsForSubmit (嵌套支持)', () => {
  it('递归 normalize 嵌套对象', () => {
    const schema: PortalParamSchema = {
      type: 'object',
      properties: {
        config: {
          type: 'object',
          properties: {
            retries: { type: 'integer' },
            ratio: { type: 'number' },
          },
        },
      },
    }
    const result = normalizeParamsForSubmit(schema, {
      config: { retries: '5', ratio: '0.75' },
    })
    expect(result.config).toEqual({ retries: 5, ratio: 0.75 })
  })
})
