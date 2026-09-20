/**
 * SchemaEditor 组件测试
 * - 添加字段 / 删除字段
 * - 类型切换清理逻辑
 * - JSON 输出正确性
 *
 * 由于 Arco Design Vue 组件 stub 会产生嵌套 DOM 结构，
 * 测试使用 vm 暴露的方法 + emitted 事件验证，而非 DOM 查询。
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import SchemaEditor from '@/components/SchemaEditor.vue'

// 使用 shallowMount 级别的 stub —— 所有 Arco 组件变成 <a-xxx-stub>
const globalConfig = {
  stubs: {
    'a-row': true,
    'a-col': true,
    'a-button': true,
    'a-collapse': true,
    'a-collapse-item': true,
    'a-form': true,
    'a-form-item': true,
    'a-input': true,
    'a-input-number': true,
    'a-textarea': true,
    'a-select': true,
    'a-option': true,
    'a-checkbox': true,
    'a-switch': true,
    'a-card': true,
    'a-empty': true,
    'a-divider': true,
    'a-space': true,
  },
}

function mountEditor(modelValue: Record<string, unknown> = {}) {
  return mount(SchemaEditor, {
    props: { modelValue },
    global: globalConfig,
  })
}

/** 提取最后一次 emitted schema */
function lastEmittedSchema(wrapper: ReturnType<typeof mountEditor>): Record<string, unknown> | null {
  const emitted = wrapper.emitted('update:modelValue')
  if (!emitted || emitted.length === 0) return null
  return emitted[emitted.length - 1][0] as Record<string, unknown>
}

describe('SchemaEditor', () => {
  describe('添加字段', () => {
    it('调用 addField 后创建新字段并 emit 更新', async () => {
      const wrapper = mountEditor()
      const vm = wrapper.vm as any

      // 初始无字段
      expect(vm.fields).toHaveLength(0)

      // 调用 addField
      vm.addField()
      await nextTick()

      expect(vm.fields).toHaveLength(1)
      expect(vm.fields[0].key).toBe('field_1')
      expect(vm.fields[0].field.type).toBe('string')

      const schema = lastEmittedSchema(wrapper)
      expect(schema).not.toBeNull()
      expect(schema!.type).toBe('object')
      const props = schema!.properties as Record<string, unknown>
      expect(Object.keys(props)).toContain('field_1')
    })

    it('连续添加多个字段，key 不重复', async () => {
      const wrapper = mountEditor()
      const vm = wrapper.vm as any

      vm.addField()
      vm.addField()
      vm.addField()
      await nextTick()

      expect(vm.fields).toHaveLength(3)

      const schema = lastEmittedSchema(wrapper)!
      const keys = Object.keys(schema.properties as Record<string, unknown>)
      expect(new Set(keys).size).toBe(3)
    })
  })

  describe('删除字段', () => {
    it('删除字段后 emit 更新且字段消失', async () => {
      const initial = {
        type: 'object',
        properties: {
          name: { type: 'string', title: '名称' },
          age: { type: 'integer', title: '年龄' },
        },
        required: ['name'],
      }
      const wrapper = mountEditor(initial)
      const vm = wrapper.vm as any

      expect(vm.fields).toHaveLength(2)

      // 删除第一个字段 (name)
      vm.removeField(0)
      await nextTick()

      expect(vm.fields).toHaveLength(1)
      expect(vm.fields[0].key).toBe('age')

      const schema = lastEmittedSchema(wrapper)!
      const keys = Object.keys(schema.properties as Record<string, unknown>)
      expect(keys).toHaveLength(1)
      expect(keys).toContain('age')
      // name 被删除后不应在 required 中
      expect(schema.required).toBeUndefined()
    })
  })

  describe('类型切换', () => {
    it('从 string 切换到 number 时清理 format 和 enum', async () => {
      const initial = {
        type: 'object',
        properties: {
          status: {
            type: 'string',
            title: '状态',
            format: 'textarea',
            enum: ['active', 'inactive'],
          },
        },
      }
      const wrapper = mountEditor(initial)
      const vm = wrapper.vm as any

      // 修改类型
      vm.fields[0].field.type = 'number'
      vm.onTypeChange(0)
      await nextTick()

      const schema = lastEmittedSchema(wrapper)!
      const statusField = (schema.properties as Record<string, Record<string, unknown>>).status
      expect(statusField.type).toBe('number')
      // format 和 enum 应被清除
      expect(statusField.format).toBeUndefined()
      expect(statusField.enum).toBeUndefined()
    })

    it('从 number 切换到 string 时清理 minimum/maximum', async () => {
      const initial = {
        type: 'object',
        properties: {
          score: {
            type: 'number',
            title: '分数',
            minimum: 0,
            maximum: 100,
          },
        },
      }
      const wrapper = mountEditor(initial)
      const vm = wrapper.vm as any

      vm.fields[0].field.type = 'string'
      vm.onTypeChange(0)
      await nextTick()

      const schema = lastEmittedSchema(wrapper)!
      const scoreField = (schema.properties as Record<string, Record<string, unknown>>).score
      expect(scoreField.type).toBe('string')
      expect(scoreField.minimum).toBeUndefined()
      expect(scoreField.maximum).toBeUndefined()
    })

    it('从 string 切换到 array 时清理 format/enum 但保留 items', async () => {
      const initial = {
        type: 'object',
        properties: {
          tags: { type: 'string', title: '标签', format: 'textarea' },
        },
      }
      const wrapper = mountEditor(initial)
      const vm = wrapper.vm as any

      vm.fields[0].field.type = 'array'
      vm.onTypeChange(0)
      await nextTick()

      const schema = lastEmittedSchema(wrapper)!
      const tagsField = (schema.properties as Record<string, Record<string, unknown>>).tags
      expect(tagsField.type).toBe('array')
      expect(tagsField.format).toBeUndefined()
      expect(tagsField.enum).toBeUndefined()
    })
  })

  describe('JSON 输出正确性', () => {
    it('从已有 schema 初始化并输出完整 JSON', () => {
      const initial = {
        type: 'object',
        properties: {
          campaign: { type: 'string', title: '活动名称', description: '投放活动名' },
          budget: { type: 'number', title: '预算', minimum: 0, maximum: 100000 },
          enabled: { type: 'boolean', title: '是否启用', default: true },
        },
        required: ['campaign', 'budget'],
      }
      const wrapper = mountEditor(initial)
      const vm = wrapper.vm as any

      // 通过 computed 属性验证 JSON 预览内容
      const json = JSON.parse(vm.previewJson)
      expect(json.type).toBe('object')
      expect(Object.keys(json.properties)).toHaveLength(3)
      expect(json.properties.campaign.type).toBe('string')
      expect(json.properties.campaign.title).toBe('活动名称')
      expect(json.properties.campaign.description).toBe('投放活动名')
      expect(json.properties.budget.minimum).toBe(0)
      expect(json.properties.budget.maximum).toBe(100000)
      expect(json.properties.enabled.default).toBe(true)
      expect(json.required).toEqual(['campaign', 'budget'])
    })

    it('空 schema 输出正确基础结构', () => {
      const wrapper = mountEditor({})
      const vm = wrapper.vm as any

      const json = JSON.parse(vm.previewJson)
      expect(json.type).toBe('object')
      expect(json.properties).toEqual({})
      expect(json.required).toBeUndefined()
    })

    it('required 勾选状态正确反映在输出中', async () => {
      const initial = {
        type: 'object',
        properties: {
          name: { type: 'string', title: '名称' },
        },
      }
      const wrapper = mountEditor(initial)
      const vm = wrapper.vm as any

      // 模拟勾选 required
      vm.fields[0].isRequired = true
      vm.onFieldChange()
      await nextTick()

      const schema = lastEmittedSchema(wrapper)!
      expect(schema.required).toEqual(['name'])
    })

    it('取消 required 后从数组中移除', async () => {
      const initial = {
        type: 'object',
        properties: {
          name: { type: 'string', title: '名称' },
        },
        required: ['name'],
      }
      const wrapper = mountEditor(initial)
      const vm = wrapper.vm as any

      expect(vm.fields[0].isRequired).toBe(true)

      vm.fields[0].isRequired = false
      vm.onFieldChange()
      await nextTick()

      const schema = lastEmittedSchema(wrapper)!
      expect(schema.required).toBeUndefined()
    })
  })

  describe('字段排序', () => {
    it('下移第一个字段后顺序反转', async () => {
      const initial = {
        type: 'object',
        properties: {
          first: { type: 'string', title: '第一个' },
          second: { type: 'string', title: '第二个' },
        },
      }
      const wrapper = mountEditor(initial)
      const vm = wrapper.vm as any

      expect(vm.fields[0].key).toBe('first')
      expect(vm.fields[1].key).toBe('second')

      vm.moveField(0, 1) // 下移
      await nextTick()

      expect(vm.fields[0].key).toBe('second')
      expect(vm.fields[1].key).toBe('first')

      const schema = lastEmittedSchema(wrapper)!
      const keys = Object.keys(schema.properties as Record<string, unknown>)
      expect(keys[0]).toBe('second')
      expect(keys[1]).toBe('first')
    })

    it('上移第二个字段后顺序反转', async () => {
      const initial = {
        type: 'object',
        properties: {
          alpha: { type: 'string', title: 'A' },
          beta: { type: 'string', title: 'B' },
        },
      }
      const wrapper = mountEditor(initial)
      const vm = wrapper.vm as any

      vm.moveField(1, -1) // 上移
      await nextTick()

      expect(vm.fields[0].key).toBe('beta')
      expect(vm.fields[1].key).toBe('alpha')
    })

    it('边界：上移第一个字段不变', async () => {
      const initial = {
        type: 'object',
        properties: {
          only: { type: 'string', title: '唯一' },
        },
      }
      const wrapper = mountEditor(initial)
      const vm = wrapper.vm as any

      vm.moveField(0, -1)
      await nextTick()

      // 不应该 emit（没有变化）
      const emitted = wrapper.emitted('update:modelValue')
      expect(emitted).toBeUndefined()
    })
  })

  describe('枚举管理', () => {
    it('添加和删除枚举选项', async () => {
      const initial = {
        type: 'object',
        properties: {
          color: { type: 'string', title: '颜色' },
        },
      }
      const wrapper = mountEditor(initial)
      const vm = wrapper.vm as any

      // 添加枚举选项
      vm.addEnumOption(0)
      vm.updateEnumOption(0, 0, '红色')
      vm.addEnumOption(0)
      vm.updateEnumOption(0, 1, '绿色')
      await nextTick()

      const schema1 = lastEmittedSchema(wrapper)!
      const colorField1 = (schema1.properties as Record<string, Record<string, unknown>>).color
      expect(colorField1.enum).toEqual(['红色', '绿色'])

      // 删除第一个枚举选项
      vm.removeEnumOption(0, 0)
      await nextTick()

      const schema2 = lastEmittedSchema(wrapper)!
      const colorField2 = (schema2.properties as Record<string, Record<string, unknown>>).color
      expect(colorField2.enum).toEqual(['绿色'])
    })
  })

  describe('字段名更新', () => {
    it('updateKey 清理特殊字符', async () => {
      const wrapper = mountEditor()
      const vm = wrapper.vm as any

      vm.addField()
      await nextTick()

      vm.updateKey(0, 'my field!')
      await nextTick()

      expect(vm.fields[0].key).toBe('myfield')

      const schema = lastEmittedSchema(wrapper)!
      const keys = Object.keys(schema.properties as Record<string, unknown>)
      expect(keys).toContain('myfield')
    })
  })
})
