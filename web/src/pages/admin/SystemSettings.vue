<template>
  <div class="system-settings-page ai-main">
    <div class="system-settings-pagehead ai-pagehead">
      <div>
        <div class="ai-crumbs">管理后台 · 治理与合规</div>
        <h1 class="ai-title">系统配置</h1>
        <p class="ai-sub">采集、AI 与监控治理参数 · 修改即生效，敏感项需 2 位管理员复签</p>
      </div>
    </div>

    <div class="system-settings-tabs ai-tabs">
      <button
        v-for="tab in settingTabs"
        :key="tab.key"
        class="system-settings-tab ai-tab"
        :class="{ active: activeTab === tab.key }"
        type="button"
        @click="activeTab = tab.key"
      >
        {{ tab.title }}
      </button>
    </div>

    <div class="system-settings-body ai-pagebody">
      <a-spin :loading="loading" class="settings-spin">
        <div class="settings-list">
          <section
            v-for="field in fieldsByTab[activeTab] || []"
            :key="field.key"
            class="setting-row ai-card"
          >
            <div class="setting-main">
              <div class="field-title">{{ field.label }}</div>
              <div class="field-key">{{ field.key }}</div>
              <div class="setting-meta">
                当前 {{ displayCurrentValue(field) }}
                <span>默认 {{ formatValue(field.defaultValue, field) }}</span>
                <span v-if="field.range">{{ field.range }}</span>
                <span>更新 {{ updatedMeta(field.key) }}</span>
              </div>
            </div>
            <div v-if="field.locked" class="locked-field">部署期固定</div>
            <div v-else class="field-editor">
              <a-input-number
                v-if="field.kind === 'number'"
                v-model="editValues[field.key]"
                :min="field.min"
                :max="field.max"
                :step="field.step || 1"
                hide-button
              />
              <a-switch v-else-if="field.kind === 'boolean'" v-model="editValues[field.key]" />
              <a-select v-else-if="field.kind === 'select'" v-model="editValues[field.key]" allow-clear>
                <a-option v-for="option in field.options || []" :key="option.value" :value="option.value">
                  {{ option.label }}
                </a-option>
              </a-select>
              <a-textarea
                v-else-if="field.kind === 'json' || field.kind === 'csv'"
                v-model="editValues[field.key]"
                :auto-size="{ minRows: 2, maxRows: 4 }"
              />
              <a-input-password
                v-else-if="field.kind === 'secret'"
                v-model="editValues[field.key]"
                placeholder="留空不变"
                allow-clear
              />
              <a-input v-else v-model="editValues[field.key]" allow-clear />
              <div class="row-actions">
                <button
                  class="ai-btn sm primary"
                  type="button"
                  :disabled="saving[field.key] || (field.kind === 'secret' ? !editValues[field.key] : !isDirty(field))"
                  @click="saveField(field)"
                >
                  {{ saving[field.key] ? '保存中' : '保存' }}
                </button>
                <button class="ai-btn sm" type="button" :disabled="saving[field.key]" @click="resetField(field)">
                  重置
                </button>
              </div>
            </div>
          </section>
        </div>
      </a-spin>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { systemApi } from '@/api'
import { formatTime } from '@/utils/format'

type FieldKind = 'string' | 'secret' | 'number' | 'boolean' | 'json' | 'csv' | 'select'

type SelectOption = {
  label: string
  value: string
}

type SettingField = {
  key: string
  label: string
  tab: string
  kind: FieldKind
  defaultValue: unknown
  range?: string
  options?: SelectOption[]
  min?: number
  max?: number
  step?: number
  locked?: boolean
}

type ConfigRow = {
  key: string
  value: unknown
  updated_by?: string | null
  updated_at?: string | null
}

const settingTabs = [
  { key: 'security', title: '安全设置' },
  { key: 'connector_keys', title: 'Connector Keys' },
  { key: 'collection', title: 'Collection' },
  { key: 'sycm', title: 'SYCM' },
  { key: 'intelligence', title: 'Intelligence' },
  { key: 'ai', title: 'AI' },
  { key: 'monitoring', title: 'Monitoring' },
]

const settingFields: SettingField[] = [
  {
    tab: 'security',
    key: 'security.bypass_review_direct_publish',
    label: '免审核直接发布',
    kind: 'boolean',
    defaultValue: false,
    range: '关闭=提交后进入审核；开启=提交后自动发布',
  },
  {
    tab: 'security',
    key: 'security.platform_node_fallback_enabled',
    label: '平台节点兜底运行',
    kind: 'boolean',
    defaultValue: true,
    range: '部门无运行节点时使用平台默认节点',
  },
  {
    tab: 'connector_keys',
    key: 'connector_keys.legacy_owner_user_id',
    label: 'Legacy Owner',
    kind: 'string',
    defaultValue: '',
  },
  {
    tab: 'connector_keys',
    key: 'connector_keys.legacy_grace_until',
    label: 'Legacy Grace Until',
    kind: 'string',
    defaultValue: '',
    range: 'ISO 时间',
  },
  {
    tab: 'collection',
    key: 'collection.rate_max_calls',
    label: 'Rate Max Calls',
    kind: 'number',
    defaultValue: 120,
    min: 1,
    max: 10000,
    range: '1-10000',
  },
  {
    tab: 'collection',
    key: 'collection.rate_window_seconds',
    label: 'Rate Window Seconds',
    kind: 'number',
    defaultValue: 60,
    min: 1,
    max: 3600,
    range: '1-3600',
  },
  {
    tab: 'collection',
    key: 'collection.circuit_failures',
    label: 'Circuit Failures',
    kind: 'number',
    defaultValue: 3,
    min: 1,
    max: 50,
    range: '1-50',
  },
  {
    tab: 'collection',
    key: 'collection.circuit_cooldown_seconds',
    label: 'Circuit Cooldown Seconds',
    kind: 'number',
    defaultValue: 300,
    min: 1,
    max: 86400,
    range: '1-86400',
  },
  {
    tab: 'sycm',
    key: 'sycm.max_inflight_per_cookie',
    label: 'Max Inflight Per Cookie',
    kind: 'number',
    defaultValue: 1,
    range: '部署期固定',
    locked: true,
  },
  {
    tab: 'sycm',
    key: 'sycm.max_attempts_per_data_scope',
    label: 'Max Attempts Per Data Scope',
    kind: 'number',
    defaultValue: 2,
    range: '部署期固定',
    locked: true,
  },
  {
    tab: 'sycm',
    key: 'sycm.warning_hold_seconds',
    label: 'Warning Hold Seconds',
    kind: 'number',
    defaultValue: 300,
    min: 30,
    max: 86400,
    range: '30-86400',
  },
  {
    tab: 'intelligence',
    key: 'intelligence.allowed_models',
    label: 'Allowed Models',
    kind: 'csv',
    defaultValue: ['deepseek-v4-pro', 'deepseek-v4-flash', 'deepseek-chat'],
  },
  {
    tab: 'intelligence',
    key: 'intelligence.default_model',
    label: 'Default Model',
    kind: 'string',
    defaultValue: 'deepseek-v4-pro',
  },
  {
    tab: 'intelligence',
    key: 'intelligence.fallback_model',
    label: 'Fallback Model',
    kind: 'string',
    defaultValue: 'deepseek-chat',
  },
  {
    tab: 'intelligence',
    key: 'intelligence.max_input_tokens_per_run',
    label: 'Max Input Tokens',
    kind: 'number',
    defaultValue: 1000000,
    min: 50000,
    max: 1000000,
    range: '50000-1000000',
    step: 1000,
  },
  {
    tab: 'intelligence',
    key: 'intelligence.max_output_tokens_per_run',
    label: 'Max Output Tokens',
    kind: 'number',
    defaultValue: 16384,
    min: 512,
    max: 32768,
    range: '512-32768',
    step: 256,
  },
  {
    tab: 'intelligence',
    key: 'intelligence.max_cost_usd_per_run',
    label: 'Max Cost Per Run',
    kind: 'number',
    defaultValue: 1,
    min: 0.01,
    max: 50,
    range: '0.01-50',
    step: 0.01,
  },
  {
    tab: 'intelligence',
    key: 'intelligence.daily_budget_usd',
    label: 'Daily Budget',
    kind: 'number',
    defaultValue: 50,
    min: 0.1,
    max: 1000,
    range: '0.1-1000',
    step: 0.1,
  },
  {
    tab: 'ai',
    key: 'ai.provider',
    label: 'AI Provider',
    kind: 'select',
    options: [
      { label: 'Custom', value: 'custom' },
      { label: 'SiliconFlow', value: 'siliconflow' },
    ],
    defaultValue: 'custom',
    range: 'SiliconFlow 可自动填充 api_base/model 默认值',
  },
  {
    tab: 'ai',
    key: 'ai.api_base',
    label: 'API Base',
    kind: 'string',
    defaultValue: '',
  },
  {
    tab: 'ai',
    key: 'ai.api_key',
    label: 'API Key',
    kind: 'secret',
    defaultValue: '',
  },
  {
    tab: 'ai',
    key: 'ai.model',
    label: 'Model',
    kind: 'string',
    defaultValue: 'deepseek-chat',
  },
  {
    tab: 'ai',
    key: 'ai.max_tokens',
    label: 'Max Tokens',
    kind: 'number',
    defaultValue: 4096,
    min: 256,
    max: 65536,
    range: '256-65536',
  },
  {
    tab: 'ai',
    key: 'ai.temperature',
    label: 'Temperature',
    kind: 'number',
    defaultValue: 0.2,
    min: 0,
    max: 2,
    step: 0.1,
    range: '0-2',
  },
  {
    tab: 'ai',
    key: 'ai.timeout',
    label: 'Timeout Seconds',
    kind: 'number',
    defaultValue: 60,
    min: 1,
    max: 600,
    range: '1-600',
  },
  {
    tab: 'ai',
    key: 'ai.cheap.api_base',
    label: 'Cheap API Base',
    kind: 'string',
    defaultValue: '',
    range: '留空沿用 ai.api_base',
  },
  {
    tab: 'ai',
    key: 'ai.cheap.api_key',
    label: 'Cheap API Key',
    kind: 'secret',
    defaultValue: '',
    range: '留空沿用全局/已保存 Key',
  },
  {
    tab: 'ai',
    key: 'ai.cheap.model',
    label: 'Cheap Model',
    kind: 'string',
    defaultValue: 'deepseek-chat',
    range: '低成本模型，用于项目转服务和 ai.cheap.* 能力',
  },
  {
    tab: 'ai',
    key: 'ai.speech.enabled',
    label: '素材工作台独立配音',
    kind: 'boolean',
    defaultValue: false,
    range: '启用后只在服务端调用 SiliconFlow 官方语音接口，并保留配音与混音 artifact 血缘',
  },
  {
    tab: 'ai',
    key: 'ai.speech.api_base',
    label: 'Speech API Base',
    kind: 'string',
    defaultValue: 'https://api.siliconflow.cn/v1',
    range: '固定白名单端点：https://api.siliconflow.cn/v1',
  },
  {
    tab: 'ai',
    key: 'ai.speech.api_key',
    label: 'Speech API Key',
    kind: 'secret',
    defaultValue: '',
    range: '独立语音 Key；不返回浏览器、不下发 Bridge、不写日志',
  },
  {
    tab: 'ai',
    key: 'ai.speech.model',
    label: 'Speech Model',
    kind: 'string',
    defaultValue: 'FunAudioLLM/CosyVoice2-0.5B',
    range: '第一版固定白名单模型 FunAudioLLM/CosyVoice2-0.5B',
  },
  {
    tab: 'ai',
    key: 'project.service_conversion.ai_enabled',
    label: 'Project Service AI',
    kind: 'boolean',
    defaultValue: false,
    range: '上传网页后用便宜模型优化服务契约；关闭时仍做确定性转服务',
  },
  {
    tab: 'monitoring',
    key: 'monitoring.cost_budget_warn_ratio',
    label: 'Cost Warn Ratio',
    kind: 'number',
    defaultValue: 0.8,
    min: 0,
    max: 1,
    step: 0.05,
    range: '0-1',
  },
  {
    tab: 'monitoring',
    key: 'monitoring.data_health_ratio_low',
    label: 'Data Health Low',
    kind: 'number',
    defaultValue: 0.8,
    min: 0,
    max: 1,
    step: 0.05,
    range: '0-1',
  },
  {
    tab: 'monitoring',
    key: 'monitoring.mcp_runtime_drift_enabled',
    label: 'MCP Runtime Drift',
    kind: 'boolean',
    defaultValue: true,
  },
  {
    tab: 'monitoring',
    key: 'monitoring.mcp_direct_cookie_blocked_enabled',
    label: 'Direct Cookie Blocked',
    kind: 'boolean',
    defaultValue: true,
  },
]

const activeTab = ref('security')
const loading = ref(false)
const configs = ref<ConfigRow[]>([])
const editValues = reactive<Record<string, any>>({})
const saving = reactive<Record<string, boolean>>({})

const configMap = computed(() => new Map(configs.value.map((item) => [item.key, item])))
const fieldsByTab = computed(() => settingFields.reduce<Record<string, SettingField[]>>((acc, field) => {
  if (!acc[field.tab]) acc[field.tab] = []
  acc[field.tab].push(field)
  return acc
}, {}))

function normalizeEditValue(field: SettingField, value: unknown): any {
  if (field.kind === 'number') return Number(value ?? field.defaultValue ?? 0)
  if (field.kind === 'boolean') return Boolean(value ?? field.defaultValue)
  if (field.kind === 'json') return typeof value === 'string' ? value : JSON.stringify(value ?? field.defaultValue ?? {}, null, 2)
  if (field.kind === 'csv') return Array.isArray(value) ? value.join('\n') : String(value ?? '')
  if (field.kind === 'secret') return ''
  return String(value ?? '')
}

function actualValue(field: SettingField): unknown {
  return configMap.value.get(field.key)?.value ?? field.defaultValue
}

function parseEditValue(field: SettingField): unknown {
  const raw = editValues[field.key]
  if (field.kind === 'number') return Number(raw)
  if (field.kind === 'boolean') return Boolean(raw)
  if (field.kind === 'json') return JSON.parse(String(raw || '{}'))
  if (field.kind === 'csv') {
    return String(raw || '')
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean)
  }
  return String(raw ?? '').trim()
}

function formatValue(value: unknown, field?: SettingField): string {
  if (field?.kind === 'secret') return configMap.value.has(field.key) ? '已配置' : '未配置'
  if (Array.isArray(value)) return value.join(', ')
  if (value && typeof value === 'object') return JSON.stringify(value)
  if (value === '' || value === null || value === undefined) return '-'
  return String(value)
}

function displayCurrentValue(field: SettingField): string {
  return formatValue(actualValue(field), field)
}

function updatedMeta(key: string): string {
  const row = configMap.value.get(key)
  if (!row) return '-'
  const author = row.updated_by || '-'
  const time = row.updated_at ? formatTime(row.updated_at) : '-'
  return `${author} / ${time}`
}

function isDirty(field: SettingField): boolean {
  if (field.kind === 'secret') return Boolean(editValues[field.key])
  return JSON.stringify(parseEditValue(field)) !== JSON.stringify(actualValue(field))
}

function resetField(field: SettingField) {
  editValues[field.key] = normalizeEditValue(field, actualValue(field))
}

async function saveField(field: SettingField) {
  if (field.locked) return
  try {
    const value = parseEditValue(field)
    if (field.kind === 'number' && Number.isNaN(Number(value))) {
      Message.warning('数值无效')
      return
    }
    saving[field.key] = true
    await systemApi.set(field.key, value)
    Message.success('已保存')
    await loadConfigs()
  } catch (e: any) {
    Message.error(e?._message || e?.message || '保存失败')
  } finally {
    saving[field.key] = false
  }
}

async function loadConfigs() {
  loading.value = true
  try {
    const rows = await systemApi.list()
    configs.value = Array.isArray(rows) ? rows : []
    for (const field of settingFields) {
      editValues[field.key] = normalizeEditValue(field, actualValue(field))
    }
  } catch (e: any) {
    configs.value = []
    Message.error(e?._message || '加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(loadConfigs)
</script>

<style scoped>
.system-settings-page {
  gap: 0;
  padding: 0;
  max-width: none;
  margin: 0;
  min-height: calc(100vh - 92px);
  overflow-x: auto;
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}
.system-settings-pagehead {
  flex: 0 0 auto;
}
.system-settings-tabs {
  flex: 0 0 auto;
}
.system-settings-tab {
  border: 0;
  background: transparent;
  cursor: pointer;
  font-family: var(--ai-font-sans);
}
.system-settings-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.settings-spin {
  display: block;
  width: 100%;
}

.settings-list {
  display: grid;
  gap: 12px;
}

/* 设计稿 AdminConfig: 单行配置卡片 ai-card 风格 */
.setting-row {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) minmax(300px, 420px);
  align-items: center;
  gap: 16px;
  padding: 16px 20px;
  border-radius: var(--ai-radius) !important;
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  box-shadow: none !important;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.setting-row:hover {
  border-color: var(--ai-border-2);
  box-shadow: var(--ai-shadow-1);
}

.setting-main {
  min-width: 0;
}

.field-title {
  font-weight: 600;
  font-size: 13.5px;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}

.field-key {
  margin-top: 2px;
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-4);
  word-break: break-all;
}

.setting-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  margin-top: 8px;
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.5;
  font-variant-numeric: tabular-nums;
}

.current-value,
.muted,
.locked-field {
  color: var(--ai-ink-3);
  word-break: break-word;
}

.locked-field {
  font-size: 12px;
  color: var(--ai-ink-4);
  font-weight: 500;
  line-height: 32px;
  text-align: center;
}

.field-editor {
  display: grid;
  grid-template-columns: minmax(160px, 1fr) auto;
  align-items: center;
  gap: 8px;
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

@media (max-width: 760px) {
  .system-settings-pagehead {
    flex-direction: column;
    align-items: flex-start;
    padding: 16px;
  }
  .system-settings-tabs {
    overflow-x: auto;
  }
  .system-settings-body {
    padding: 16px;
  }
  .setting-row {
    grid-template-columns: 1fr;
    padding: 14px;
  }

  .field-editor {
    grid-template-columns: 1fr;
  }
}
</style>
