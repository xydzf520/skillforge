<template>
  <aside class="gpt-image-param-inspector">
    <header class="inspector-head">
      <div class="title-block">
        <span class="eyebrow">{{ title }}</span>
        <h3>参数 Inspector</h3>
      </div>
      <a-tag size="small">{{ fieldCount }} 项</a-tag>
    </header>

    <div class="head-actions">
      <span>请求 JSON</span>
      <a-switch v-model="showRequestJson" size="small" />
    </div>

    <pre v-if="showRequestJson" class="request-json">{{ requestJson }}</pre>

    <a-collapse
      v-model:active-key="activeSectionKeys"
      class="param-collapse"
      :bordered="false"
    >
      <a-collapse-item
        v-for="group in fieldGroups"
        :key="group.key"
        :header="`${group.title} · ${group.entries.length}`"
      >
        <a-form :model="formModel" layout="vertical" class="param-form">
          <a-form-item
            v-for="entry in group.entries"
            :key="entry.key"
            :field="entry.key"
            :label="fieldTitle(entry.key, entry.field)"
            :required="isRequired(entry.key)"
          >
            <a-select
              v-if="hasEnum(entry.field)"
              :model-value="fieldValue(entry.key)"
              allow-clear
              size="small"
              :placeholder="fieldTitle(entry.key, entry.field)"
              @update:model-value="updateField(entry.key, $event)"
            >
              <a-option
                v-for="item in entry.field.enum"
                :key="enumKey(entry.key, item)"
                :value="item"
              >
                {{ formatEnumValue(item) }}
              </a-option>
            </a-select>

            <a-date-picker
              v-else-if="isDateField(entry.field)"
              :model-value="stringFieldValue(entry.key)"
              :show-time="isDateTimeField(entry.field)"
              :value-format="dateValueFormat(entry.field)"
              allow-clear
              size="small"
              style="width: 100%"
              :placeholder="fieldTitle(entry.key, entry.field)"
              @update:model-value="updateField(entry.key, $event)"
            />

            <div v-else-if="isBooleanField(entry.field)" class="switch-line">
              <a-switch
                :model-value="booleanFieldValue(entry.key)"
                size="small"
                checked-text="开"
                unchecked-text="关"
                @update:model-value="updateField(entry.key, $event)"
              />
              <span>{{ booleanFieldValue(entry.key) ? '已开启' : '已关闭' }}</span>
            </div>

            <a-input-number
              v-else-if="isNumberField(entry.field)"
              :model-value="numberFieldValue(entry.key)"
              :precision="isIntegerField(entry.field) ? 0 : undefined"
              :min="numberLimit(entry.field.minimum)"
              :max="numberLimit(entry.field.maximum)"
              allow-clear
              size="small"
              style="width: 100%"
              :placeholder="fieldTitle(entry.key, entry.field)"
              @update:model-value="updateNumberField(entry.key, entry.field, $event)"
            />

            <a-input-password
              v-else-if="isPasswordField(entry.field, entry.key)"
              :model-value="stringFieldValue(entry.key)"
              allow-clear
              size="small"
              placeholder="sk-..."
              @update:model-value="updateField(entry.key, $event)"
            />

            <a-textarea
              v-else-if="isTextAreaField(entry.field, entry.key)"
              :model-value="textFieldValue(entry.key, entry.field)"
              :auto-size="{ minRows: textAreaRows(entry.key, entry.field), maxRows: 10 }"
              allow-clear
              :placeholder="fieldPlaceholder(entry.key, entry.field)"
              @focus="beginTextEdit(entry.key, entry.field)"
              @blur="endTextEdit(entry.key)"
              @update:model-value="updateTextField(entry.key, entry.field, $event)"
            />

            <a-input
              v-else
              :model-value="stringFieldValue(entry.key)"
              allow-clear
              size="small"
              :placeholder="fieldPlaceholder(entry.key, entry.field)"
              @update:model-value="updateField(entry.key, $event)"
            />

            <div class="field-meta">
              <code>{{ entry.key }}</code>
              <span v-if="entry.field.description">{{ entry.field.description }}</span>
            </div>
          </a-form-item>
        </a-form>
      </a-collapse-item>
    </a-collapse>
  </aside>
</template>

<script setup lang="ts">
import { computed, reactive, ref, type PropType } from 'vue'

type SchemaEnumValue = string | number | boolean | null
type SchemaFieldType = string | string[]

type ParamSchemaField = {
  type?: SchemaFieldType
  format?: string
  title?: string
  description?: string
  default?: unknown
  enum?: SchemaEnumValue[]
  items?: ParamSchemaField
  minimum?: number
  maximum?: number
  properties?: Record<string, ParamSchemaField>
}

type ParamSchema = {
  type?: string
  required?: string[]
  properties?: Record<string, ParamSchemaField>
}

type FieldEntry = {
  key: string
  field: ParamSchemaField
}

type FieldGroup = {
  key: string
  title: string
  fields: string[]
}

const COMMON_FIELDS = ['action', 'operation', 'size', 'resolution', 'n', 'reference_images', 'task_id', 'wait', 'download']
const THINKING_FIELDS = ['thinking_mode', 'thinking_model', 'thinking_effort', 'thinking_web_search']
const ADVANCED_FIELDS = [
  'message',
  'prompt',
  'messages',
  'output',
  'response_format',
  'mask',
  'quality',
  'background',
  'output_format',
  'input_fidelity',
  'moderation',
  'poll_interval',
  'timeout',
  'initial_wait',
  'http_timeout',
  'upload_purpose',
  'model',
]
const DEVELOPER_FIELDS = ['image_urls', 'upload_only']

const FIELD_GROUPS: FieldGroup[] = [
  { key: 'common', title: '常用参数', fields: COMMON_FIELDS },
  { key: 'thinking', title: '思考模式', fields: THINKING_FIELDS },
  { key: 'advanced', title: '高级参数', fields: ADVANCED_FIELDS },
  { key: 'developer', title: '开发者设置', fields: DEVELOPER_FIELDS },
]

const HIDDEN_FIELDS = new Set(['base_url', 'api_key', 'config', 'user_agent'])
const FIELD_KEYS = [...COMMON_FIELDS, ...THINKING_FIELDS, ...ADVANCED_FIELDS, ...DEVELOPER_FIELDS]
const VISIBLE_FIELD_KEYS = FIELD_KEYS.filter(key => !HIDDEN_FIELDS.has(key))

const FALLBACK_FIELDS: Record<string, ParamSchemaField> = {
  size: { type: 'string', title: '画幅尺寸' },
  resolution: { type: 'string', title: '分辨率' },
  n: { type: 'integer', title: '生成数量', minimum: 1 },
  reference_images: { type: 'array', title: '参考图', description: '公开 URL；本地图片请先在左侧上传' },
  task_id: { type: 'string', title: '任务 ID' },
  wait: { type: 'boolean', title: '等待完成' },
  download: { type: 'boolean', title: '下载结果' },
  prompt: { type: 'string', format: 'textarea', title: '提示词' },
  thinking_mode: { type: 'boolean', title: 'thinking' },
  thinking_model: { type: 'string', title: '思考模型' },
  thinking_effort: { type: 'string', title: '思考强度', enum: ['low', 'medium', 'high'] },
  thinking_web_search: { type: 'boolean', title: '联网搜索' },
  output: { type: 'string', title: '输出目录' },
  response_format: { type: 'string', title: '响应格式' },
  poll_interval: { type: 'number', title: '轮询间隔', minimum: 0 },
  timeout: { type: 'number', title: '总超时', minimum: 0 },
  initial_wait: { type: 'number', title: '初始等待', minimum: 0 },
  http_timeout: { type: 'number', title: 'HTTP 超时', minimum: 0 },
  upload_purpose: { type: 'string', title: '上传用途' },
  model: { type: 'string', title: '模型' },
  base_url: { type: 'string', format: 'uri', title: 'Base URL' },
  api_key: { type: 'string', format: 'password', title: 'API Key' },
  config: { type: 'object', title: '配置 JSON' },
  user_agent: { type: 'string', title: 'User-Agent' },
  image_urls: { type: 'array', title: '图片 URL' },
  upload_only: { type: 'boolean', title: '仅上传' },
  action: { type: 'string', title: '图片动作' },
  message: { type: 'string', format: 'textarea', title: '本轮消息' },
  messages: { type: 'array', title: '对话消息' },
  operation: { type: 'string', title: '运行模式' },
  mask: { type: 'string', title: '编辑蒙版' },
  quality: { type: 'string', title: '编辑质量' },
  background: { type: 'string', title: '编辑背景' },
  output_format: { type: 'string', title: '编辑输出格式' },
  input_fidelity: { type: 'string', title: '输入保真' },
  moderation: { type: 'string', title: '审核强度' },
}

const props = defineProps({
  title: { type: String, default: 'GPT ImageGen' },
  schema: { type: Object as PropType<ParamSchema | undefined>, default: undefined },
  modelValue: { type: Object as PropType<Record<string, unknown>>, default: () => ({}) },
  requiredFields: { type: Array as PropType<string[]>, default: () => [] },
})

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, unknown>]
}>()

const showRequestJson = ref(false)
const activeSectionKeys = ref(FIELD_GROUPS.map(group => group.key))
const textDrafts = reactive<Record<string, string>>({})

const formModel = computed(() => props.modelValue || {})
const fieldCount = computed(() => VISIBLE_FIELD_KEYS.length)

const requiredSet = computed(() => {
  return new Set([...(props.schema?.required || []), ...props.requiredFields])
})

const fieldGroups = computed(() =>
  FIELD_GROUPS.map(group => ({
    ...group,
    entries: group.fields
      .filter(key => !HIDDEN_FIELDS.has(key))
      .map(key => ({
        key,
        field: resolveField(key),
      })),
  })).filter(group => group.entries.length > 0),
)

const requestPayload = computed(() => {
  const payload: Record<string, unknown> = { ...(props.modelValue || {}) }
  for (const key of VISIBLE_FIELD_KEYS) {
    const value = (props.modelValue || {})[key]
    if (value !== undefined) payload[key] = normalizeFieldForRequest(key, resolveField(key), value)
  }
  HIDDEN_FIELDS.forEach(key => delete payload[key])
  return payload
})

const requestJson = computed(() => JSON.stringify(redactSecrets(requestPayload.value), null, 2))

function resolveField(key: string): ParamSchemaField {
  return {
    ...(FALLBACK_FIELDS[key] || { type: 'string', title: key }),
    ...(props.schema?.properties?.[key] || {}),
  }
}

function fieldValue(key: string) {
  return (props.modelValue || {})[key]
}

function updateField(key: string, value: unknown) {
  emit('update:modelValue', {
    ...(props.modelValue || {}),
    [key]: value,
  })
}

function updateNumberField(key: string, field: ParamSchemaField, value: unknown) {
  if (value === '' || value === undefined || value === null) {
    updateField(key, undefined)
    return
  }

  const num = Number(value)
  if (!Number.isFinite(num)) {
    updateField(key, value)
    return
  }

  updateField(key, isIntegerField(field) ? Math.trunc(num) : num)
}

function beginTextEdit(key: string, field: ParamSchemaField) {
  textDrafts[key] = stringifyTextValue(fieldValue(key), field)
}

function endTextEdit(key: string) {
  delete textDrafts[key]
}

function updateTextField(key: string, field: ParamSchemaField, value: unknown) {
  const text = String(value ?? '')
  textDrafts[key] = text
  updateField(key, coerceTextValue(field, text))
}

function fieldTitle(key: string, field: ParamSchemaField) {
  return field.title || FALLBACK_FIELDS[key]?.title || key
}

function fieldPlaceholder(key: string, field: ParamSchemaField) {
  if (normalizedType(field) === 'array') return '每行一个值，也支持 JSON 数组'
  if (normalizedType(field) === 'object') return '输入 JSON 对象'
  return fieldTitle(key, field)
}

function textAreaRows(key: string, field: ParamSchemaField) {
  if (key === 'prompt') return 5
  if (normalizedType(field) === 'object') return 4
  if (normalizedType(field) === 'array') return 3
  return 2
}

function stringFieldValue(key: string) {
  const value = fieldValue(key)
  if (value === undefined || value === null) return ''
  return String(value)
}

function numberFieldValue(key: string) {
  const value = fieldValue(key)
  if (value === undefined || value === null || value === '') return undefined
  const num = Number(value)
  return Number.isFinite(num) ? num : undefined
}

function booleanFieldValue(key: string) {
  const value = fieldValue(key)
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    return !['', 'false', '0', 'no', 'off'].includes(normalized)
  }
  return Boolean(value)
}

function textFieldValue(key: string, field: ParamSchemaField) {
  if (Object.prototype.hasOwnProperty.call(textDrafts, key)) return textDrafts[key]
  return stringifyTextValue(fieldValue(key), field)
}

function stringifyTextValue(value: unknown, field: ParamSchemaField) {
  if (value === undefined || value === null) return ''
  if (typeof value === 'string') return value
  if (normalizedType(field) === 'array' && Array.isArray(value)) {
    return value.map(item => (typeof item === 'string' ? item : JSON.stringify(item))).join('\n')
  }
  if (normalizedType(field) === 'object' || typeof value === 'object') {
    return JSON.stringify(value, null, 2)
  }
  return String(value)
}

function coerceTextValue(field: ParamSchemaField, text: string) {
  const type = normalizedType(field)
  if (!text.trim()) return ''

  if (type === 'array') {
    const parsed = parseJson(text)
    if (Array.isArray(parsed)) return parsed
    if (parsed !== undefined) return [parsed]
    return text
      .split(/\n|,/)
      .map(item => item.trim())
      .filter(Boolean)
  }

  if (type === 'object') {
    const parsed = parseJson(text)
    return isPlainObject(parsed) ? parsed : text
  }

  return text
}

function normalizeFieldForRequest(key: string, field: ParamSchemaField, value: unknown) {
  const type = normalizedType(field)

  if (type === 'integer') {
    const num = Number(value)
    return Number.isFinite(num) ? Math.trunc(num) : value
  }

  if (type === 'number') {
    const num = Number(value)
    return Number.isFinite(num) ? num : value
  }

  if (type === 'boolean') {
    if (typeof value === 'string') {
      const normalized = value.trim().toLowerCase()
      return !['', 'false', '0', 'no', 'off'].includes(normalized)
    }
    return Boolean(value)
  }

  if (type === 'array' && typeof value === 'string') return coerceTextValue(field, value)
  if (type === 'object' && typeof value === 'string') return coerceTextValue(field, value)

  if (key === 'api_key' && typeof value === 'string' && !value.trim()) return ''
  return value
}

function redactSecrets(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactSecrets)
  if (!isPlainObject(value)) return value
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => {
      if (/api[_-]?key|token|secret|authorization/i.test(key) && item) {
        return [key, '[redacted]']
      }
      return [key, redactSecrets(item)]
    }),
  )
}

function normalizedType(field: ParamSchemaField) {
  if (Array.isArray(field.type)) return field.type.find(type => type !== 'null') || field.type[0]
  return field.type
}

function hasEnum(field: ParamSchemaField) {
  return Array.isArray(field.enum) && field.enum.length > 0
}

function isRequired(key: string) {
  return requiredSet.value.has(key)
}

function isBooleanField(field: ParamSchemaField) {
  return normalizedType(field) === 'boolean'
}

function isNumberField(field: ParamSchemaField) {
  const type = normalizedType(field)
  return type === 'number' || type === 'integer'
}

function isIntegerField(field: ParamSchemaField) {
  return normalizedType(field) === 'integer'
}

function isDateField(field: ParamSchemaField) {
  return field.format === 'date' || field.format === 'date-time' || field.format === 'datetime'
}

function isDateTimeField(field: ParamSchemaField) {
  return field.format === 'date-time' || field.format === 'datetime'
}

function dateValueFormat(field: ParamSchemaField) {
  return isDateTimeField(field) ? 'YYYY-MM-DD HH:mm:ss' : 'YYYY-MM-DD'
}

function isPasswordField(field: ParamSchemaField, key: string) {
  return field.format === 'password' || key.toLowerCase().includes('api_key')
}

function isTextAreaField(field: ParamSchemaField, key: string) {
  const type = normalizedType(field)
  return field.format === 'textarea' || type === 'array' || type === 'object' || key === 'prompt'
}

function numberLimit(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function parseJson(text: string) {
  try {
    return JSON.parse(text)
  } catch {
    return undefined
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function enumKey(key: string, value: SchemaEnumValue) {
  return `${key}:${String(value)}`
}

function formatEnumValue(value: SchemaEnumValue) {
  if (value === null) return 'null'
  if (value === true) return 'true'
  if (value === false) return 'false'
  return String(value)
}
</script>

<style scoped>
.gpt-image-param-inspector {
  width: 100%;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
  box-shadow: 0 8px 24px var(--ai-surface-2);
}

.inspector-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 14px 12px;
  border-bottom: 1px solid var(--ai-border);
}

.title-block {
  min-width: 0;
}

.eyebrow {
  display: block;
  margin-bottom: 3px;
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.2;
}

.title-block h3 {
  margin: 0;
  color: var(--ai-ink-1);
  font-size: 16px;
  line-height: 1.35;
}

.head-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--ai-border);
  color: var(--ai-ink-2);
  font-size: 13px;
}

.request-json {
  margin: 12px 14px 0;
  padding: 12px;
  max-height: 280px;
  overflow: auto;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
  font-size: 12px;
  line-height: 1.6;
}

.param-collapse {
  padding: 4px 10px 10px;
}

.param-collapse :deep(.arco-collapse-item-header) {
  padding: 10px 4px;
  color: var(--ai-ink-1);
  font-weight: 600;
}

.param-collapse :deep(.arco-collapse-item-content-box) {
  padding: 6px 0 2px;
}

.param-form {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.param-form :deep(.arco-form-item) {
  margin-bottom: 14px;
}

.param-form :deep(.arco-form-item-label) {
  color: var(--ai-ink-2);
  font-size: 12px;
}

.param-form :deep(.arco-input-wrapper),
.param-form :deep(.arco-select-view-single),
.param-form :deep(.arco-input-number),
.param-form :deep(.arco-picker) {
  border-radius: 8px;
}

.param-form :deep(textarea) {
  line-height: 1.6;
}

.switch-line {
  min-height: 28px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.switch-line span {
  color: var(--ai-ink-3);
  font-size: 12px;
}

.field-meta {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-top: 6px;
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.5;
}

.field-meta code {
  width: fit-content;
  max-width: 100%;
  padding: 1px 6px;
  overflow-wrap: anywhere;
  border-radius: 6px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: 12px;
}

</style>
