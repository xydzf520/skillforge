<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import type { PortalSchemaField, PortalParamSchema } from '@/pages/portal/shared'

const props = defineProps<{
  modelValue: Record<string, unknown>
}>()
const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>]
}>()

// ---------- 内部类型 ----------
interface FieldEntry {
  key: string
  field: PortalSchemaField & {
    dependsOn?: { field: string; value: unknown }
  }
  isRequired: boolean
}

// ---------- 状态 ----------
const activeKeys = ref<string[]>([])
const fields = ref<FieldEntry[]>([])

// 类型选项
const typeOptions = [
  { value: 'string', label: '文本' },
  { value: 'number', label: '数字' },
  { value: 'integer', label: '整数' },
  { value: 'boolean', label: '开关' },
  { value: 'array', label: '数组' },
  { value: 'object', label: '对象' },
]

// 格式选项（仅 string 类型可选）
const formatOptions = [
  { value: '', label: '无' },
  { value: 'date', label: '日期' },
  { value: 'textarea', label: '多行文本' },
  { value: 'email', label: '邮箱' },
  { value: 'url', label: 'URL' },
]

// ---------- 从 modelValue 初始化 ----------
function loadFromSchema(schema: Record<string, unknown>) {
  const s = schema as unknown as PortalParamSchema
  const properties = s?.properties || {}
  const required = s?.required || []
  fields.value = Object.entries(properties).map(([key, field]) => ({
    key,
    field: { ...field } as FieldEntry['field'],
    isRequired: required.includes(key),
  }))
}

// 初始加载
loadFromSchema(props.modelValue)

// 监听外部变更（仅在结构变化时重新加载，避免循环）
let emitting = false
watch(() => props.modelValue, (val) => {
  if (emitting) return
  loadFromSchema(val)
}, { deep: true })

// ---------- 同步到 modelValue ----------
function emitUpdate() {
  const properties: Record<string, PortalSchemaField> = {}
  const required: string[] = []
  for (const entry of fields.value) {
    if (!entry.key) continue
    const f: Record<string, unknown> = { type: entry.field.type || 'string' }
    if (entry.field.title) f.title = entry.field.title
    if (entry.field.description) f.description = entry.field.description
    if (entry.field.format) f.format = entry.field.format
    if (entry.field.default !== undefined && entry.field.default !== '') f.default = entry.field.default
    if (entry.field.enum && entry.field.enum.length > 0) f.enum = entry.field.enum
    if (entry.field.minimum !== undefined && entry.field.minimum !== null) f.minimum = Number(entry.field.minimum)
    if (entry.field.maximum !== undefined && entry.field.maximum !== null) f.maximum = Number(entry.field.maximum)
    if (entry.field.items) f.items = entry.field.items
    if (entry.field.dependsOn?.field) f.dependsOn = entry.field.dependsOn
    if (entry.isRequired) required.push(entry.key)
    properties[entry.key] = f as PortalSchemaField
  }
  const schema: Record<string, unknown> = { type: 'object', properties }
  if (required.length > 0) schema.required = required
  emitting = true
  emit('update:modelValue', schema)
  // 在下一个微任务重置标志，防止 watch 循环
  Promise.resolve().then(() => { emitting = false })
}

// ---------- 操作 ----------
function addField() {
  const idx = fields.value.length + 1
  const key = `field_${idx}`
  fields.value.push({
    key,
    field: { type: 'string', title: '' },
    isRequired: false,
  })
  activeKeys.value = [key]
  emitUpdate()
}

function removeField(index: number) {
  fields.value.splice(index, 1)
  emitUpdate()
}

function updateKey(index: number, newKey: string) {
  // 清理：去掉空格和特殊字符
  const cleaned = newKey.replace(/[^a-zA-Z0-9_]/g, '')
  fields.value[index].key = cleaned
  emitUpdate()
}

function onFieldChange() {
  emitUpdate()
}

function onTypeChange(index: number) {
  const entry = fields.value[index]
  // 类型切换时清理不适用的属性
  if (entry.field.type !== 'string') {
    entry.field.format = undefined
    entry.field.enum = undefined
  }
  if (entry.field.type !== 'number' && entry.field.type !== 'integer') {
    entry.field.minimum = undefined
    entry.field.maximum = undefined
  }
  if (entry.field.type !== 'array') {
    entry.field.items = undefined
  }
  emitUpdate()
}

// ---------- 枚举管理 ----------
function addEnumOption(index: number) {
  const entry = fields.value[index]
  if (!entry.field.enum) entry.field.enum = []
  entry.field.enum.push('')
  emitUpdate()
}

function removeEnumOption(fieldIndex: number, enumIndex: number) {
  const entry = fields.value[fieldIndex]
  if (entry.field.enum) {
    entry.field.enum.splice(enumIndex, 1)
    emitUpdate()
  }
}

function updateEnumOption(fieldIndex: number, enumIndex: number, value: string) {
  const entry = fields.value[fieldIndex]
  if (entry.field.enum) {
    entry.field.enum[enumIndex] = value
    emitUpdate()
  }
}

// ---------- 条件联动 ----------
// 可用字段名列表（排除自身）
function availableFieldKeys(excludeIndex: number): string[] {
  return fields.value
    .filter((_, i) => i !== excludeIndex)
    .map(e => e.key)
    .filter(Boolean)
}

// ---------- JSON 预览 ----------
const previewJson = computed(() => {
  const properties: Record<string, unknown> = {}
  const required: string[] = []
  for (const entry of fields.value) {
    if (!entry.key) continue
    const f: Record<string, unknown> = { type: entry.field.type || 'string' }
    if (entry.field.title) f.title = entry.field.title
    if (entry.field.description) f.description = entry.field.description
    if (entry.field.format) f.format = entry.field.format
    if (entry.field.default !== undefined && entry.field.default !== '') f.default = entry.field.default
    if (entry.field.enum && entry.field.enum.length > 0) f.enum = entry.field.enum
    if (entry.field.minimum !== undefined && entry.field.minimum !== null) f.minimum = Number(entry.field.minimum)
    if (entry.field.maximum !== undefined && entry.field.maximum !== null) f.maximum = Number(entry.field.maximum)
    if (entry.field.items) f.items = entry.field.items
    if (entry.field.dependsOn?.field) f.dependsOn = entry.field.dependsOn
    if (entry.isRequired) required.push(entry.key)
    properties[entry.key] = f
  }
  const schema: Record<string, unknown> = { type: 'object', properties }
  if (required.length > 0) schema.required = required
  return JSON.stringify(schema, null, 2)
})

// 排序：上移/下移
function moveField(index: number, direction: -1 | 1) {
  const target = index + direction
  if (target < 0 || target >= fields.value.length) return
  const temp = fields.value[index]
  fields.value[index] = fields.value[target]
  fields.value[target] = temp
  emitUpdate()
}
</script>

<template>
  <div class="schema-editor">
    <a-row :gutter="16">
      <!-- 左侧：字段编辑列表 -->
      <a-col :span="14">
        <div class="schema-editor-header">
          <span class="schema-editor-title">参数定义</span>
          <a-button type="primary" size="small" @click="addField">
            添加字段
          </a-button>
        </div>

        <a-empty v-if="fields.length === 0" description="暂无参数字段，点击「添加字段」开始" />

        <a-collapse v-model:active-key="activeKeys" accordion>
          <a-collapse-item
            v-for="(entry, index) in fields"
            :key="entry.key || `field-${index}`"
            :header="entry.field.title || entry.key || '未命名字段'"
          >
            <template #extra>
              <a-space size="mini" @click.stop>
                <a-button
                  size="mini"
                  type="text"
                  :disabled="index === 0"
                  @click="moveField(index, -1)"
                >
                  上移
                </a-button>
                <a-button
                  size="mini"
                  type="text"
                  :disabled="index === fields.length - 1"
                  @click="moveField(index, 1)"
                >
                  下移
                </a-button>
              </a-space>
            </template>

            <a-form layout="vertical" size="small" :model="entry">
              <!-- 字段名 -->
              <a-form-item label="字段名（key）">
                <a-input
                  :model-value="entry.key"
                  placeholder="如 roi_threshold"
                  @input="updateKey(index, $event)"
                />
              </a-form-item>

              <!-- 显示名称 -->
              <a-form-item label="显示名称">
                <a-input
                  v-model="entry.field.title"
                  placeholder="如 ROI 阈值"
                  @change="onFieldChange"
                />
              </a-form-item>

              <!-- 类型 -->
              <a-form-item label="类型">
                <a-select
                  v-model="entry.field.type"
                  @change="onTypeChange(index)"
                >
                  <a-option
                    v-for="opt in typeOptions"
                    :key="opt.value"
                    :value="opt.value"
                  >
                    {{ opt.label }}
                  </a-option>
                </a-select>
              </a-form-item>

              <!-- 格式（仅 string 类型） -->
              <a-form-item v-if="entry.field.type === 'string'" label="格式">
                <a-select
                  v-model="entry.field.format"
                  allow-clear
                  placeholder="选择格式"
                  @change="onFieldChange"
                >
                  <a-option
                    v-for="opt in formatOptions"
                    :key="opt.value"
                    :value="opt.value"
                  >
                    {{ opt.label }}
                  </a-option>
                </a-select>
              </a-form-item>

              <!-- 是否必填 -->
              <a-form-item label="必填">
                <a-checkbox
                  v-model="entry.isRequired"
                  @change="onFieldChange"
                >
                  该字段为必填项
                </a-checkbox>
              </a-form-item>

              <!-- 默认值 -->
              <a-form-item label="默认值">
                <a-input-number
                  v-if="entry.field.type === 'number' || entry.field.type === 'integer'"
                  v-model="entry.field.default"
                  placeholder="默认值"
                  @change="onFieldChange"
                />
                <a-switch
                  v-else-if="entry.field.type === 'boolean'"
                  :model-value="Boolean(entry.field.default)"
                  @update:model-value="(value) => { entry.field.default = value; onFieldChange() }"
                />
                <a-input
                  v-else
                  v-model="entry.field.default"
                  placeholder="默认值"
                  @change="onFieldChange"
                />
              </a-form-item>

              <!-- 枚举选项（仅 string 类型） -->
              <a-form-item v-if="entry.field.type === 'string'" label="枚举选项">
                <div class="enum-list">
                  <div v-for="(opt, ei) in (entry.field.enum || [])" :key="ei" class="enum-item">
                    <a-input
                      :model-value="String(opt)"
                      size="mini"
                      placeholder="选项值"
                      @input="updateEnumOption(index, ei, $event)"
                    />
                    <a-button
                      size="mini"
                      type="text"
                      status="danger"
                      @click="removeEnumOption(index, ei)"
                    >
                      删除
                    </a-button>
                  </div>
                  <a-button size="mini" type="dashed" long @click="addEnumOption(index)">
                    添加选项
                  </a-button>
                </div>
              </a-form-item>

              <!-- 最小值/最大值（number/integer 类型） -->
              <a-row
                v-if="entry.field.type === 'number' || entry.field.type === 'integer'"
                :gutter="8"
              >
                <a-col :span="12">
                  <a-form-item label="最小值">
                    <a-input-number
                      v-model="entry.field.minimum"
                      placeholder="最小值"
                      @change="onFieldChange"
                    />
                  </a-form-item>
                </a-col>
                <a-col :span="12">
                  <a-form-item label="最大值">
                    <a-input-number
                      v-model="entry.field.maximum"
                      placeholder="最大值"
                      @change="onFieldChange"
                    />
                  </a-form-item>
                </a-col>
              </a-row>

              <!-- 描述 -->
              <a-form-item label="描述">
                <a-textarea
                  v-model="entry.field.description"
                  placeholder="字段说明"
                  :auto-size="{ minRows: 2, maxRows: 4 }"
                  @change="onFieldChange"
                />
              </a-form-item>

              <!-- 条件联动 -->
              <a-form-item label="条件联动（dependsOn）">
                <a-space direction="vertical" fill>
                  <a-select
                    :model-value="entry.field.dependsOn?.field"
                    allow-clear
                    placeholder="依赖字段"
                    @change="(val: string) => {
                      if (!entry.field.dependsOn) entry.field.dependsOn = { field: '', value: '' }
                      entry.field.dependsOn.field = val || ''
                      onFieldChange()
                    }"
                  >
                    <a-option
                      v-for="k in availableFieldKeys(index)"
                      :key="k"
                      :value="k"
                    >
                      {{ k }}
                    </a-option>
                  </a-select>
                  <a-input
                    v-if="entry.field.dependsOn?.field"
                    :model-value="String(entry.field.dependsOn?.value ?? '')"
                    placeholder="当依赖字段等于此值时显示"
                    @input="(val: string) => {
                      if (!entry.field.dependsOn) return
                      entry.field.dependsOn.value = val
                      onFieldChange()
                    }"
                  />
                </a-space>
              </a-form-item>

              <!-- 删除字段 -->
              <a-divider />
              <a-button status="danger" size="mini" @click="removeField(index)">
                删除此字段
              </a-button>
            </a-form>
          </a-collapse-item>
        </a-collapse>
      </a-col>

      <!-- 右侧：JSON 预览 -->
      <a-col :span="10">
        <a-card title="Schema 预览" class="schema-preview-card">
          <pre class="schema-preview-json">{{ previewJson }}</pre>
        </a-card>
      </a-col>
    </a-row>
  </div>
</template>

<style scoped>
.schema-editor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.schema-editor-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--ai-ink-1);
}

.enum-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.enum-item {
  display: flex;
  align-items: center;
  gap: 6px;
}

.enum-item .arco-input-wrapper {
  flex: 1;
}

.schema-preview-card {
  position: sticky;
  top: 16px;
}

.schema-preview-json {
  margin: 0;
  padding: 8px;
  font-size: 12px;
  line-height: 1.5;
  background: var(--ai-surface-2);
  border-radius: 4px;
  overflow-x: auto;
  max-height: 600px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
