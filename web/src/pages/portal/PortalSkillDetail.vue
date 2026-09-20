<template>
  <div class="page-container portal-detail">
    <div class="page-header page-detail-toolbar">
      <a-button class="portal-back-btn ai-btn sm" type="text" @click="$router.push('/portal')">
        <template #icon><icon-left /></template>
        返回门户
      </a-button>
    </div>

    <section class="report-hero portal-hero">
      <div class="report-hero-main">
        <div class="report-kicker">
          <span>Portal · Skill</span>
          <span v-if="skill?.category">{{ skill.category }}</span>
          <span v-if="skill?.org_unit?.name || skill?.org_unit_name">{{ skill?.org_unit?.name || skill?.org_unit_name }}</span>
        </div>
        <h1 class="report-title">
          {{ skill?.display_name || skill?.name || 'Skill 详情' }}
          <a-tag class="hero-version-pill">{{ uiVersionLabel }}</a-tag>
        </h1>
        <p class="report-summary">{{ skill?.summary || skill?.description || '选择参数并执行 Skill' }}</p>
        <div class="report-meta">
          <span>Skill ID {{ skill?.id || '-' }}</span>
          <span>{{ skill?.org_unit?.name || skill?.org_unit_name || '部门未设置' }}</span>
          <span>最近执行 {{ latestRunLabel }}</span>
        </div>
        <div class="report-tag-row">
          <a-tag
            v-if="skill?.status"
            :color="skillStatusColor[skill?.status as keyof typeof skillStatusColor] || 'gray'"
          >
            {{ skillStatusLabel[skill?.status as keyof typeof skillStatusLabel] || '未知' }}
          </a-tag>
          <a-tag v-if="skill?.category" color="arcoblue">{{ skill.category }}</a-tag>
        </div>
      </div>
      <div class="report-hero-side">
        <div class="hero-side-label">页面打开时间</div>
        <div class="hero-side-time">{{ formatTime(pageOpenedAt) }}</div>
      </div>
    </section>

    <a-result v-if="loadError" status="warning" :title="loadError" class="page-list-card" />

    <!-- B4/D4: Market 承诺"只有 active 的 Skill 才出现"；如果用户直接打开 draft/shadow/deprecated 详情，
         这里挂一条告警 banner + 禁用运行按钮，防止误触发灰度/草稿 Skill -->
    <a-alert
      v-else-if="skill?.status && skill.status !== 'active'"
      class="portal-non-active-banner"
      type="warning"
      show-icon
      :title="`该 Skill 当前状态为「${skillStatusLabel[skill.status as keyof typeof skillStatusLabel] || skill.status}」`"
    >
      非正式运行 Skill 不会出现在企业市场。继续访问只作预览用途，「运行」按钮已禁用。
    </a-alert>

    <a-row v-if="!loadError" :gutter="24">
      <a-col :xs="24" :lg="14">
        <a-card class="page-list-card" title="运行 Skill">
          <template #extra>
            <a-space>
              <a-tag size="small">{{ uiVersionLabel }}</a-tag>
              <a-button v-if="paramEntries.length" type="text" size="small" @click="showPreview = !showPreview">
                {{ showPreview ? '编辑模式' : '预览数据' }}
              </a-button>
            </a-space>
          </template>
          <a-spin :loading="loading">
            <div v-if="showRunUiControlStrip" class="ui-save-strip" :class="{ pending: hasUnsavedUiPreview }">
              <div class="ui-save-copy">
                <strong>{{ runUiControlTitle }}</strong>
                <span>{{ runUiControlHint }}</span>
              </div>
              <div class="ui-save-buttons">
                <a-button
                  size="small"
                  :loading="uiSaving"
                  :disabled="!canSaveUiPreference"
                  @click="handleSaveUi"
                >
                  <template #icon><icon-save /></template>
                  保存运行页面
                </a-button>
                <a-button
                  size="small"
                  :loading="uiResetting"
                  :disabled="!canResetRunUiPreference"
                  @click="handleResetUi"
                >
                  <template #icon><icon-refresh /></template>
                  恢复默认
                </a-button>
              </div>
            </div>

            <!-- 预览模式 -->
            <div v-if="showPreview && paramEntries.length" class="preview-block">
              <pre class="preview-json">{{ previewJson }}</pre>
            </div>

            <!-- 编辑模式 -->
            <a-form v-else-if="paramEntries.length" :model="params" layout="vertical">
              <template v-for="[fieldKey, field] in orderedParamEntries" :key="fieldKey">
                <template v-if="isFieldVisible(fieldKey, paramSchema, params)">
                  <!-- 嵌套对象字段 -->
                  <a-form-item
                    v-if="field.type === 'object' && isRecord(field.properties)"
                    :label="fieldDisplayTitle(fieldKey, field)"
                  >
                    <a-card :title="fieldDisplayTitle(fieldKey, field)" size="small" class="nested-card">
                      <template v-for="[subKey, subField] in orderedSubEntries(fieldKey, field)" :key="`${fieldKey}.${subKey}`">
                        <a-form-item
                          v-if="isSubFieldVisible(fieldKey, subKey, subField, params)"
                          :label="fieldDisplayTitle(`${fieldKey}.${subKey}`, subField)"
                          :required="isNestedRequired(field, subKey)"
                          :help="fieldDisplayDescription(`${fieldKey}.${subKey}`, subField)"
                        >
                          <portal-field-input
                            :field="subField"
                            :model-value="getNestedFieldValue(params, fieldKey, subKey)"
                            :placeholder-prefix="fieldDisplayTitle(`${fieldKey}.${subKey}`, subField)"
                            @update:model-value="setNestedFieldValue(params, fieldKey, subKey, $event)"
                          />
                        </a-form-item>
                      </template>
                    </a-card>
                  </a-form-item>

                  <!-- 普通字段 -->
                  <a-form-item
                    v-else
                    :label="fieldDisplayTitle(fieldKey, field)"
                    :required="isRequiredField(paramSchema, fieldKey)"
                    :help="fieldDisplayDescription(fieldKey, field)"
                  >
                    <portal-field-input
                      :field="field"
                      :model-value="params[fieldKey]"
                      :placeholder-prefix="fieldDisplayTitle(fieldKey, field)"
                      @update:model-value="params[fieldKey] = $event"
                    />
                  </a-form-item>
                </template>
              </template>
            </a-form>
            <a-empty v-else description="暂无可填写参数，直接执行即可" />

            <div class="action-row">
              <a-tooltip :content="runDisabledTip" :disabled="!runDisabledTip">
                <a-button type="primary" :loading="submitting" :disabled="!canRun || hasUnsavedUiPreview" @click="handleSubmit">
                  <template #icon><icon-play-arrow /></template>
                  运行
                </a-button>
              </a-tooltip>
            </div>
          </a-spin>
        </a-card>

        <a-card class="page-list-card detail-card" title="运行历史">
          <template #extra>
            <!-- N8: 导出 CSV（前端生成，不动后端） -->
            <a-button type="text" size="small" :disabled="!recentRuns.length" @click="exportRunsCsv">
              导出 CSV
            </a-button>
          </template>
          <a-table :data="recentRuns" :pagination="false" size="small" row-key="id">
            <template #columns>
              <a-table-column title="时间">
                <template #cell="{ record }">{{ formatTime(record.created_at) }}</template>
              </a-table-column>
              <a-table-column title="状态">
                <template #cell="{ record }">
                  <a-tag :color="submissionStatusColor(record.status)">{{ record.status || '-' }}</a-tag>
                </template>
              </a-table-column>
              <a-table-column title="提交者" data-index="requester_name" />
              <a-table-column title="操作" :width="90">
                <template #cell="{ record }">
                  <a-link @click="openSubmission(record.id)">查看结果</a-link>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-card>
      </a-col>

      <a-col :xs="24" :lg="10">
        <a-card class="page-list-card detail-card" title="Skill 信息">
          <a-descriptions :column="1" bordered size="small">
            <a-descriptions-item label="负责人">{{ skill?.owner?.name || skill?.owner_name || '-' }}</a-descriptions-item>
            <a-descriptions-item label="所属组织">{{ skill?.org_unit?.name || skill?.org_unit_name || '-' }}</a-descriptions-item>
            <a-descriptions-item label="可见范围">{{ visibilityLabel(skill?.visibility) }}</a-descriptions-item>
            <a-descriptions-item label="使用次数">{{ skill?.usage_count ?? 0 }}</a-descriptions-item>
            <a-descriptions-item label="成功率">{{ formatPercent(skill?.success_rate) }}</a-descriptions-item>
          </a-descriptions>
        </a-card>

        <a-card class="page-list-card detail-card" title="标签" style="margin-top: 16px">
          <a-space v-if="skill?.tags?.length" wrap>
            <a-tag v-for="tag in skill?.tags || []" :key="tag">{{ tag }}</a-tag>
          </a-space>
          <a-empty v-else description="暂无标签" />
        </a-card>

        <a-card class="page-list-card detail-card" title="说明" style="margin-top: 16px">
          <div class="description-block">{{ skill?.description || '暂无说明' }}</div>
        </a-card>
      </a-col>
    </a-row>

    <button
      v-if="canOpenUiEntry"
      type="button"
      class="ui-floating-ai"
      :disabled="!canCustomizeUi && !canCustomizeResultUi"
      aria-label="AI 调整页面"
      title="AI 调整页面"
      @click="openDefaultUiDesigner"
    >
      <icon-robot />
    </button>

    <a-modal
      v-model:visible="uiDesignerVisible"
      title="AI 对话调整 Skill 页面"
      :footer="false"
      :width="'min(92vw, 860px)'"
      class="ui-designer-modal"
    >
      <div class="ui-designer">
        <div class="ui-designer-head">
          <div class="ui-surface-switch">
            <button
              type="button"
              class="ui-surface-btn"
              :class="{ active: uiDesignerSurface === 'run_form' }"
              :disabled="!canCustomizeUi"
              @click="setUiDesignerSurface('run_form')"
            >
              运行页面
            </button>
            <button
              type="button"
              class="ui-surface-btn"
              :class="{ active: uiDesignerSurface === 'result' }"
              :disabled="!canCustomizeResultUi"
              @click="setUiDesignerSurface('result')"
            >
              结果页面
            </button>
          </div>
          <div class="ui-meta">
            <a-tag :color="activeUiStatusColor">
              {{ activeUiStatusLabel }}
            </a-tag>
            <a-typography-text v-if="activeUiHash" class="ui-hash">
              {{ activeUiHash }}
            </a-typography-text>
          </div>
        </div>

        <div class="ui-chat-log">
          <div
            v-for="(message, index) in activeUiChatMessages"
            :key="`${message.role}-${index}-${message.content}`"
            class="ui-chat-message"
            :class="`is-${message.role}`"
          >
            <span class="ui-chat-role">{{ message.role === 'user' ? '你' : 'AI' }}</span>
            <div class="ui-chat-bubble">{{ message.content }}</div>
          </div>
        </div>

        <div class="ui-history-panel">
          <div class="ui-history-head">
            <span>保存记录</span>
            <a-button type="text" size="mini" :loading="activeUiHistoryLoading" @click="loadUiHistory(uiDesignerSurface)">
              刷新
            </a-button>
          </div>
          <div v-if="activeUiHistory.length" class="ui-history-list">
            <button
              v-for="item in activeUiHistory"
              :key="item.id"
              type="button"
              class="ui-history-item"
              :class="{ active: item.enabled }"
              :disabled="item.enabled || Boolean(activeUiRestoring[item.id])"
              @click="restoreUiHistory(item)"
            >
              <strong>v{{ item.version }}</strong>
              <span>{{ item.enabled ? '当前使用' : historyMeta(item) }}</span>
              <em>{{ item.prompt_summary || historyFallbackSummary(item) }}</em>
            </button>
          </div>
          <a-empty v-else description="暂无保存记录" />
        </div>

        <div class="ui-chat-compose">
          <a-textarea
            v-model="uiDesignerInput"
            :auto-size="{ minRows: 3, maxRows: 5 }"
            :placeholder="uiDesignerPlaceholder"
            :disabled="!activeCanCustomizeUi"
          />
          <div class="ui-actions">
            <a-button
              type="primary"
              size="small"
              :loading="activeUiPreviewing"
              :disabled="!activeCanCustomizeUi || !uiDesignerInput.trim()"
              @click="sendUiDesignerMessage"
            >
              <template #icon><icon-send /></template>
              发送给 AI
            </a-button>
            <a-button size="small" :loading="activeUiSaving" :disabled="!activeCanSaveUiPreference" @click="saveUiDesigner">
              <template #icon><icon-save /></template>
              保存到我的页面
            </a-button>
            <a-button size="small" :loading="activeUiResetting" :disabled="!activeCanResetUiPreference" @click="resetUiDesigner">
              <template #icon><icon-refresh /></template>
              恢复默认
            </a-button>
          </div>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  DatePicker,
  Input,
  InputNumber,
  Message,
  Option,
  Select,
  Switch,
  Textarea,
} from '@arco-design/web-vue'
import { IconLeft, IconPlayArrow, IconRefresh, IconRobot, IconSave, IconSend } from '@arco-design/web-vue/es/icon'
import { portalApi as rawPortalApi } from '@/api'
import { bjtDateString, formatTime } from '@/utils/format'
import { skillStatusLabel, skillStatusColor } from '@/utils/constants'
import {
  buildInitialParams,
  formatPercent,
  generatePreviewData,
  getNestedValue,
  getSchemaEntries,
  isFieldVisible,
  isRecord,
  isRequiredField,
  normalizeParamsForSubmit,
  normalizeListResponse,
  setNestedValue,
  type PortalParamSchema,
  type PortalResultSchema,
  type PortalSchemaField,
  type PortalSkillUI,
  type PortalUIComponent,
  visibilityLabel,
} from './shared'

defineOptions({ name: 'PortalSkillDetail' })

type RecentRun = {
  id: string
  status?: string
  created_at?: string
  requester_name?: string
}

type PortalSkillDetail = {
  id: string
  name?: string
  display_name?: string
  summary?: string
  description?: string
  category?: string
  icon?: string
  status?: string
  visibility?: string
  owner?: { id?: string; name?: string }
  owner_name?: string
  org_unit?: { id?: string; name?: string }
  org_unit_name?: string
  usage_count?: number
  success_rate?: number
  tags?: string[]
  param_ui_schema?: PortalParamSchema
  result_ui_schema?: PortalResultSchema
  recent_runs?: RecentRun[]
  permissions?: {
    read?: boolean
    execute?: boolean
    customize_ui?: boolean
  }
}

type UiSurface = 'run_form' | 'result'
type UiChatMessage = {
  role: 'assistant' | 'user'
  content: string
}
type UiPreferenceHistoryItem = {
  id: string
  version: number
  enabled?: boolean
  generated_by?: string | null
  prompt_summary?: string | null
  component_count?: number
  hidden_component_count?: number
  personal_default_count?: number
  created_at?: string
  updated_at?: string
}
type PreviewUiOutcome = {
  ok: boolean
  fallback?: boolean
  error?: string
}

/**
 * 单字段输入组件 -- 根据 field.type / field.format 渲染对应控件。
 * 使用 defineComponent + render 以便在 <script setup> 中直接注册使用。
 */
const PortalFieldInput = defineComponent({
  name: 'PortalFieldInput',
  props: {
    field: { type: Object as () => PortalSchemaField, required: true },
    modelValue: { type: [String, Number, Boolean, Array, Object] as any, default: undefined },
    placeholderPrefix: { type: String, default: '' },
  },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () => {
      const field = props.field
      const value = props.modelValue
      const phPrefix = props.placeholderPrefix
      const onUpdate = (v: unknown) => emit('update:modelValue', v)

      if (field.enum?.length) {
        return h(
          Select,
          {
            modelValue: value,
            'onUpdate:modelValue': onUpdate,
            placeholder: `请选择${phPrefix}`,
          },
          {
            default: () =>
              field.enum!.map((opt) =>
                h(Option, { key: String(opt), value: opt }, { default: () => String(opt) }),
              ),
          },
        )
      }

      if (field.format === 'date') {
        return h(DatePicker, {
          modelValue: value,
          'onUpdate:modelValue': onUpdate,
          valueFormat: 'YYYY-MM-DD',
          style: 'width: 100%',
          placeholder: `请选择${phPrefix}`,
        })
      }

      if (field.type === 'number' || field.type === 'integer') {
        return h(InputNumber, {
          modelValue: value,
          'onUpdate:modelValue': onUpdate,
          style: 'width: 100%',
          min: field.minimum,
          max: field.maximum,
          placeholder: `请输入${phPrefix}`,
        })
      }

      if (field.type === 'boolean') {
        return h(Switch, {
          modelValue: value,
          'onUpdate:modelValue': onUpdate,
        })
      }

      if (field.format === 'textarea' || field.type === 'array') {
        return h(Textarea, {
          modelValue: value,
          'onUpdate:modelValue': onUpdate,
          autoSize: { minRows: 3, maxRows: 8 },
          placeholder: `请输入${phPrefix}`,
        })
      }

      return h(Input, {
        modelValue: value,
        'onUpdate:modelValue': onUpdate,
        placeholder: `请输入${phPrefix}`,
      })
    }
  },
})

const portalApi: any = rawPortalApi
const route = useRoute()
const router = useRouter()
const loading = ref(false)
const submitting = ref(false)
const pageOpenedAt = ref<string>(new Date().toISOString())
const uiPreviewing = ref(false)
const uiSaving = ref(false)
const uiResetting = ref(false)
const loadError = ref('')
const showPreview = ref(false)
const skill = ref<PortalSkillDetail | null>(null)
const runUi = ref<PortalSkillUI | null>(null)
const uiInstruction = ref('')
const uiOverlayDraft = ref<Record<string, unknown> | null>(null)
const resultUi = ref<PortalSkillUI | null>(null)
const resultUiPreviewing = ref(false)
const resultUiSaving = ref(false)
const resultUiResetting = ref(false)
const resultUiInstruction = ref('')
const resultUiOverlayDraft = ref<Record<string, unknown> | null>(null)
const uiDesignerVisible = ref(false)
const uiDesignerSurface = ref<UiSurface>('run_form')
const uiDesignerInput = ref('')
const runUiChatMessages = ref<UiChatMessage[]>([])
const resultUiChatMessages = ref<UiChatMessage[]>([])
const runUiHistory = ref<UiPreferenceHistoryItem[]>([])
const resultUiHistory = ref<UiPreferenceHistoryItem[]>([])
const uiHistoryLoading = ref<Record<UiSurface, boolean>>({ run_form: false, result: false })
const uiRestoring = ref<Record<string, boolean>>({})

// B4/D4: Portal 只应运行 active 状态的 Skill；其他状态直链访问时 banner 提示 + 按钮禁用
const canRun = computed(() => skill.value?.status === 'active' && skill.value?.permissions?.execute !== false)
const canOpenUiEntry = computed(() => Boolean(skill.value && skill.value.permissions?.read !== false))
const canOpenResultUiEntry = computed(() => canOpenUiEntry.value)
const canCustomizeUi = computed(() => Boolean(runUi.value?.permissions?.customize_ui ?? skill.value?.permissions?.execute ?? canRun.value))
const hasUnsavedUiPreview = computed(() => Boolean(uiOverlayDraft.value))
const hasUiPromptChange = computed(() => uiInstruction.value.trim() !== (runUi.value?.saved_prompt || '').trim())
const canSaveUiPreference = computed(() => canCustomizeUi.value && (hasUnsavedUiPreview.value || hasUiPromptChange.value))
const canResetRunUiPreference = computed(() => canCustomizeUi.value && Boolean(runUi.value?.ui_pref_id || uiOverlayDraft.value))
const canCustomizeResultUi = computed(() => Boolean(resultUi.value?.permissions?.customize_ui ?? skill.value?.permissions?.read ?? false))
const hasUnsavedResultUiPreview = computed(() => Boolean(resultUiOverlayDraft.value))
const hasResultUiPromptChange = computed(() => resultUiInstruction.value.trim() !== (resultUi.value?.saved_prompt || '').trim())
const canSaveResultUiPreference = computed(() => canCustomizeResultUi.value && (hasUnsavedResultUiPreview.value || hasResultUiPromptChange.value))
const canResetResultUiPreference = computed(() => canCustomizeResultUi.value && Boolean(resultUi.value?.ui_pref_id || resultUiOverlayDraft.value))
const uiVersionLabel = computed(() => runUi.value?.ui_pref_id ? `界面 v${runUi.value.ui_pref_version || 1}` : '默认界面')
const showRunUiControlStrip = computed(() => Boolean(hasUnsavedUiPreview.value || runUi.value?.ui_pref_id))
const runUiControlTitle = computed(() => hasUnsavedUiPreview.value ? '未保存的运行页面预览' : uiVersionLabel.value)
const runUiControlHint = computed(() => {
  if (hasUnsavedUiPreview.value) return '当前看到的是 AI 预览，保存后才会成为你的默认运行页面；也可以直接恢复默认。'
  return '这是你的个人运行页面，只影响当前账号和当前 Skill。'
})
const shortUiHash = computed(() => {
  const hash = runUi.value?.merged_ui_schema_hash || ''
  return hash.startsWith('sha256:') ? hash.slice(7, 19) : hash.slice(0, 12)
})
const resultUiShortHash = computed(() => {
  const hash = resultUi.value?.merged_ui_schema_hash || ''
  return hash.startsWith('sha256:') ? hash.slice(7, 19) : hash.slice(0, 12)
})
const activeUiChatMessages = computed(() => messagesForSurface(uiDesignerSurface.value))
const activeCanCustomizeUi = computed(() => uiDesignerSurface.value === 'result' ? canCustomizeResultUi.value : canCustomizeUi.value)
const activeUiPreviewing = computed(() => uiDesignerSurface.value === 'result' ? resultUiPreviewing.value : uiPreviewing.value)
const activeUiSaving = computed(() => uiDesignerSurface.value === 'result' ? resultUiSaving.value : uiSaving.value)
const activeUiResetting = computed(() => uiDesignerSurface.value === 'result' ? resultUiResetting.value : uiResetting.value)
const activeUiHistory = computed(() => uiDesignerSurface.value === 'result' ? resultUiHistory.value : runUiHistory.value)
const activeUiHistoryLoading = computed(() => Boolean(uiHistoryLoading.value[uiDesignerSurface.value]))
const activeUiRestoring = computed(() => uiRestoring.value)
const activeCanSaveUiPreference = computed(() => uiDesignerSurface.value === 'result' ? canSaveResultUiPreference.value : canSaveUiPreference.value)
const activeCanResetUiPreference = computed(() => {
  if (uiDesignerSurface.value === 'result') return canResetResultUiPreference.value
  return canResetRunUiPreference.value
})
const activeUiStatusLabel = computed(() => {
  if (uiDesignerSurface.value === 'result') {
    if (hasUnsavedResultUiPreview.value) return '预览中'
    return resultUi.value?.ui_pref_id ? '个人版' : '默认版'
  }
  if (hasUnsavedUiPreview.value) return '预览中'
  return runUi.value?.ui_pref_id ? '个人版' : '默认版'
})
const activeUiStatusColor = computed(() => {
  if (activeUiStatusLabel.value === '预览中') return 'orange'
  if (activeUiStatusLabel.value === '个人版') return 'arcoblue'
  return 'gray'
})
const activeUiHash = computed(() => uiDesignerSurface.value === 'result' ? resultUiShortHash.value : shortUiHash.value)
const uiDesignerPlaceholder = computed(() => (
  uiDesignerSurface.value === 'result'
    ? '例如：把结果改成指标卡加趋势图，表格只保留日期、花费、ROI'
    : '例如：把日期和店铺放第一组，隐藏可选字段，标题改成日常参数'
))
const runDisabledTip = computed(() => {
  if (hasUnsavedUiPreview.value) return '请先保存界面预览'
  if (skill.value?.status !== 'active') return '非 active 状态 Skill 不允许从门户运行'
  if (skill.value?.permissions?.execute === false) return '当前账号无运行权限'
  return ''
})

function warnIfUiPreferenceInvalidated(ui: PortalSkillUI | null, label = '个人界面') {
  if (!ui?.ui_pref_invalidated?.ui_pref_id) return
  Message.warning(`${label}已因 Skill 界面结构变化失效，已恢复默认界面`)
}

function firstQueryValue(value: unknown): string {
  if (Array.isArray(value)) return String(value[0] || '')
  return typeof value === 'string' ? value : ''
}

async function focusSectionFromQuery(): Promise<void> {
  const focus = firstQueryValue(route.query?.focus)
  if (!focus) return
  if (focus === 'ui') {
    openUiDesigner('run_form')
    return
  }
  if (focus === 'result-ui') {
    openUiDesigner('result')
    return
  }
}

function messagesForSurface(surface: UiSurface): UiChatMessage[] {
  return surface === 'result' ? resultUiChatMessages.value : runUiChatMessages.value
}

function seedUiDesignerMessages(surface: UiSurface): void {
  const messages = messagesForSurface(surface)
  if (messages.length) return
  messages.push({
    role: 'assistant',
    content: surface === 'result'
      ? '告诉我结果页面要怎么呈现，我会生成个人预览。确认后保存，只影响你当前账号看到的这个 Skill。'
      : '告诉我运行页面要怎么编排，我会生成个人预览。确认后保存，只影响你当前账号看到的这个 Skill。',
  })
}

function setHistoryForSurface(surface: UiSurface, items: UiPreferenceHistoryItem[]): void {
  if (surface === 'result') {
    resultUiHistory.value = items
  } else {
    runUiHistory.value = items
  }
}

async function loadUiHistory(surface: UiSurface): Promise<void> {
  const id = String(route.params.id || '')
  if (!id) return
  uiHistoryLoading.value = { ...uiHistoryLoading.value, [surface]: true }
  try {
    const res = await portalApi.listSkillUiPreferences(id, { surface, limit: 20 })
    setHistoryForSurface(surface, normalizeListResponse<UiPreferenceHistoryItem>(res))
  } catch {
    setHistoryForSurface(surface, [])
  } finally {
    uiHistoryLoading.value = { ...uiHistoryLoading.value, [surface]: false }
  }
}

function historyMeta(item: UiPreferenceHistoryItem): string {
  const source = item.generated_by === 'ai' ? 'AI 保存' : '手动保存'
  const time = formatTime(item.updated_at || item.created_at || '')
  return time ? `${source} · ${time}` : source
}

function historyFallbackSummary(item: UiPreferenceHistoryItem): string {
  const parts = [
    `${item.component_count || 0} 个组件`,
    item.hidden_component_count ? `隐藏 ${item.hidden_component_count}` : '',
    item.personal_default_count ? `默认值 ${item.personal_default_count}` : '',
  ].filter(Boolean)
  return parts.join(' · ') || '无调整摘要'
}

async function restoreUiHistory(item: UiPreferenceHistoryItem): Promise<void> {
  const id = String(route.params.id || '')
  if (!id || !item.id || item.enabled) return
  const surface = uiDesignerSurface.value
  uiRestoring.value = { ...uiRestoring.value, [item.id]: true }
  try {
    const res = await portalApi.restoreSkillUiPreference(id, item.id, { surface })
    if (surface === 'result') {
      resultUi.value = res || resultUi.value
      resultUiInstruction.value = typeof res?.saved_prompt === 'string' ? res.saved_prompt : resultUiInstruction.value
      resultUiOverlayDraft.value = null
    } else {
      runUi.value = res || runUi.value
      uiInstruction.value = typeof res?.saved_prompt === 'string' ? res.saved_prompt : uiInstruction.value
      uiOverlayDraft.value = null
      resetParamsFromSchema()
    }
    await loadUiHistory(surface)
    messagesForSurface(surface).push({
      role: 'assistant',
      content: surface === 'result' ? `已恢复结果页面 v${item.version}。` : `已恢复运行页面 v${item.version}。`,
    })
    Message.success('已恢复到历史版本')
  } catch (error: any) {
    Message.error(error?._message || '恢复历史版本失败')
  } finally {
    const next = { ...uiRestoring.value }
    delete next[item.id]
    uiRestoring.value = next
  }
}

function setUiDesignerSurface(surface: UiSurface): void {
  uiDesignerSurface.value = surface
  uiDesignerInput.value = ''
  seedUiDesignerMessages(surface)
  void loadUiHistory(surface)
}

function openUiDesigner(surface: UiSurface): void {
  setUiDesignerSurface(surface)
  uiDesignerVisible.value = true
}

function openDefaultUiDesigner(): void {
  if (canCustomizeUi.value) {
    openUiDesigner('run_form')
    return
  }
  if (canCustomizeResultUi.value) {
    openUiDesigner('result')
  }
}

function setUiInstructionForSurface(surface: UiSurface, instruction: string): void {
  if (surface === 'result') {
    resultUiInstruction.value = instruction
  } else {
    uiInstruction.value = instruction
  }
}

async function sendUiDesignerMessage(): Promise<void> {
  const surface = uiDesignerSurface.value
  const instruction = uiDesignerInput.value.trim()
  if (!instruction || !activeCanCustomizeUi.value) return

  const messages = messagesForSurface(surface)
  setUiInstructionForSurface(surface, instruction)
  messages.push({ role: 'user', content: instruction })
  uiDesignerInput.value = ''

  const outcome = await previewUiSurface(surface, instruction, false)
  if (outcome.ok) {
    messages.push({
      role: 'assistant',
      content: outcome.fallback
        ? 'AI 输出没有通过安全校验，已生成安全预览。可以继续补充要求，或保存当前预览。'
        : '已更新页面预览。确认没问题后，可以保存到我的页面。',
    })
  } else {
    messages.push({ role: 'assistant', content: outcome.error || '生成失败，请调整描述后再试。' })
  }
}

async function saveUiDesigner(): Promise<void> {
  const surface = uiDesignerSurface.value
  const ok = surface === 'result' ? await handleSaveResultUi() : await handleSaveUi()
  if (!ok) return
  messagesForSurface(surface).push({
    role: 'assistant',
    content: surface === 'result' ? '已保存结果页面结构。' : '已保存运行页面结构。',
  })
}

async function resetUiDesigner(): Promise<void> {
  const surface = uiDesignerSurface.value
  const ok = surface === 'result' ? await handleResetResultUi() : await handleResetUi()
  if (!ok) return
  messagesForSurface(surface).push({
    role: 'assistant',
    content: surface === 'result' ? '结果页面已恢复默认结构。' : '运行页面已恢复默认结构。',
  })
}

async function refreshRunUiAfterConflict(id: string) {
  try {
    runUi.value = await portalApi.getSkillUi(id, { surface: 'run_form' })
    warnIfUiPreferenceInvalidated(runUi.value)
    uiInstruction.value = typeof runUi.value?.saved_prompt === 'string' ? runUi.value.saved_prompt : ''
    if (uiDesignerVisible.value && uiDesignerSurface.value === 'run_form') {
      uiDesignerInput.value = uiInstruction.value
    }
    uiOverlayDraft.value = null
    resetParamsFromSchema()
  } catch {
    uiOverlayDraft.value = null
  }
}

async function loadResultUiForSkill(id: string) {
  try {
    resultUi.value = await portalApi.getSkillUi(id, { surface: 'result' })
    warnIfUiPreferenceInvalidated(resultUi.value, '个人结果界面')
    resultUiInstruction.value = typeof resultUi.value?.saved_prompt === 'string' ? resultUi.value.saved_prompt : ''
    if (uiDesignerVisible.value && uiDesignerSurface.value === 'result') {
      uiDesignerInput.value = resultUiInstruction.value
    }
    resultUiOverlayDraft.value = null
  } catch {
    resultUi.value = null
    resultUiInstruction.value = ''
    resultUiOverlayDraft.value = null
  }
}

// N8: 运行历史导出 CSV（前端 Blob，不占后端）
function exportRunsCsv() {
  const runs = recentRuns.value || []
  if (!runs.length) return
  const header = ['时间', '状态', '提交者', 'submission_id']
  const rows = runs.map((r: any) => [
    formatTime(r.created_at) || '',
    r.status || '',
    r.requester_name || r.requester_id || '',
    r.id || '',
  ])
  const escape = (v: any) => `"${String(v).replace(/"/g, '""')}"`
  const csv = '\uFEFF' + [header, ...rows].map((row) => row.map(escape).join(',')).join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  const skillId = String(route.params.id || 'skill')
  a.download = `portal-runs-${skillId}-${bjtDateString(Date.now())}.csv`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

const params = ref<Record<string, any>>({})
const paramSchema = computed<PortalParamSchema | undefined>(() => skill.value?.param_ui_schema)
const paramEntries = computed(() => getSchemaEntries(paramSchema.value))
const recentRuns = computed(() => normalizeListResponse<RecentRun>(skill.value?.recent_runs))
const latestRunLabel = computed(() => {
  const latest = recentRuns.value[0]
  if (!latest?.created_at) return '暂无执行'
  return formatTime(latest.created_at) || '暂无执行'
})
const uiComponents = computed<PortalUIComponent[]>(() => runUi.value?.merged_schema?.components || [])
const uiFieldComponents = computed<PortalUIComponent[]>(() => {
  const grouped: PortalUIComponent[] = []
  const direct: PortalUIComponent[] = []
  const visit = (component: PortalUIComponent, bucket: PortalUIComponent[]) => {
    if (component.type === 'field') bucket.push(component)
    for (const child of component.children || []) visit(child, bucket)
  }
  for (const component of uiComponents.value) {
    if (['form_group', 'tabs'].includes(component.type)) {
      for (const child of component.children || []) visit(child, grouped)
    } else {
      visit(component, direct)
    }
  }
  if (!grouped.length) return direct
  const groupedIds = new Set(grouped.map(component => component.id))
  return [...grouped, ...direct.filter(component => !groupedIds.has(component.id))]
})
const orderedParamEntries = computed(() => {
  const order = new Map<string, number>()
  const hidden = new Set<string>()
  for (const [index, component] of uiFieldComponents.value.entries()) {
    const paramPath = componentParamPath(component)
    if (!paramPath) continue
    const fieldKey = paramPath.split('.', 1)[0]
    if (!order.has(fieldKey)) order.set(fieldKey, index)
    if (component.visible === false && paramPath === fieldKey) hidden.add(fieldKey)
  }
  return [...paramEntries.value]
    .filter(([fieldKey]) => !hidden.has(fieldKey))
    .sort(([left], [right]) => (order.get(left) ?? 9999) - (order.get(right) ?? 9999))
})

const previewJson = computed(() => {
  if (!paramSchema.value) return '{}'
  const data = generatePreviewData(paramSchema.value)
  return JSON.stringify(data, null, 2)
})

// ---------------------------------------------------------------------------
// 嵌套字段访问辅助
// ---------------------------------------------------------------------------

function getNestedFieldValue(
  obj: Record<string, any>,
  parentKey: string,
  subKey: string,
): unknown {
  const parent = obj[parentKey]
  if (isRecord(parent)) return parent[subKey]
  return undefined
}

function setNestedFieldValue(
  obj: Record<string, any>,
  parentKey: string,
  subKey: string,
  value: unknown,
): void {
  if (!isRecord(obj[parentKey])) {
    obj[parentKey] = {}
  }
  ;(obj[parentKey] as Record<string, unknown>)[subKey] = value
}

function isNestedRequired(parentField: PortalSchemaField, subKey: string): boolean {
  return Boolean(parentField.required?.includes(subKey))
}

function componentParamPath(component: PortalUIComponent): string {
  const binding = component.binding || ''
  if (binding.startsWith('params.')) return binding.slice('params.'.length)
  return component.field || ''
}

function fieldComponent(fieldPath: string): PortalUIComponent | undefined {
  return uiFieldComponents.value.find((component) => {
    return componentParamPath(component) === fieldPath
  })
}

function fieldDisplayTitle(fieldPath: string, field: PortalSchemaField): string {
  const component = fieldComponent(fieldPath)
  return String(component?.title || field.title || fieldPath.split('.').pop() || fieldPath)
}

function fieldDisplayDescription(fieldPath: string, field: PortalSchemaField): string | undefined {
  const component = fieldComponent(fieldPath)
  return component?.description || field.description
}

function isUiFieldHidden(fieldPath: string): boolean {
  return fieldComponent(fieldPath)?.visible === false
}

function orderedSubEntries(parentKey: string, parentField: PortalSchemaField): Array<[string, PortalSchemaField]> {
  const properties = isRecord(parentField.properties) ? parentField.properties : {}
  const entries = Object.entries(properties)
  const order = new Map<string, number>()
  for (const [index, component] of uiFieldComponents.value.entries()) {
    const path = componentParamPath(component)
    if (path.startsWith(`${parentKey}.`)) {
      order.set(path.slice(parentKey.length + 1), index)
    }
  }
  return entries.sort(([left], [right]) => (order.get(left) ?? 9999) - (order.get(right) ?? 9999))
}

function applyPersonalDefaults(target: Record<string, unknown>, defaults?: Record<string, unknown>): void {
  if (!defaults) return
  for (const [path, value] of Object.entries(defaults)) {
    setNestedValue(target, path, value)
  }
}

function resetParamsFromSchema(): void {
  const nextParams = buildInitialParams(skill.value?.param_ui_schema)
  applyPersonalDefaults(nextParams, runUi.value?.merged_schema?.personal_defaults)
  params.value = nextParams
}

/**
 * 子字段条件可见性：将子字段的 dependsOn 解析到父对象的值空间中。
 */
function isSubFieldVisible(
  parentKey: string,
  subKey: string,
  subField: PortalSchemaField,
  currentValues: Record<string, any>,
): boolean {
  if (isUiFieldHidden(`${parentKey}.${subKey}`)) return false
  if (!subField.dependsOn) return true
  // 子字段 dependsOn.field 相对于父对象
  const parentObj = isRecord(currentValues[parentKey]) ? currentValues[parentKey] as Record<string, unknown> : {}
  return isFieldVisible(subKey, {
    type: 'object',
    properties: { [subKey]: subField },
  }, parentObj)
}

// ---------------------------------------------------------------------------
// 数据加载 / 提交
// ---------------------------------------------------------------------------

async function loadSkill() {
  const id = String(route.params.id || '')
  if (!id) return
  loading.value = true
  loadError.value = ''
  try {
    const res = await portalApi.getSkill(id)
    skill.value = res || null
    uiOverlayDraft.value = null
    try {
      runUi.value = await portalApi.getSkillUi(id, { surface: 'run_form' })
      warnIfUiPreferenceInvalidated(runUi.value)
      uiInstruction.value = typeof runUi.value?.saved_prompt === 'string' ? runUi.value.saved_prompt : ''
    } catch {
      runUi.value = null
      uiInstruction.value = ''
    }
    await loadResultUiForSkill(id)
    resetParamsFromSchema()
  } catch (error: any) {
    skill.value = null
    runUi.value = null
    uiInstruction.value = ''
    resultUi.value = null
    resultUiInstruction.value = ''
    resultUiOverlayDraft.value = null
    loadError.value = error?._message || '加载 Skill 失败'
  } finally {
    loading.value = false
    await focusSectionFromQuery()
  }
}

async function previewUiSurface(surface: UiSurface, instruction: string, notify = true): Promise<PreviewUiOutcome> {
  const id = String(route.params.id || '')
  if (!id || !instruction) return { ok: false }
  const isResult = surface === 'result'
  if (isResult) {
    resultUiPreviewing.value = true
  } else {
    uiPreviewing.value = true
  }
  try {
    const res = await portalApi.previewSkillUi(id, {
      surface,
      instruction,
      current_overlay: isResult
        ? resultUiOverlayDraft.value || resultUi.value?.overlay || {}
        : uiOverlayDraft.value || runUi.value?.overlay || {},
    })
    if (isResult) {
      resultUi.value = res || resultUi.value
      resultUiOverlayDraft.value = (res?.overlay || {}) as Record<string, unknown>
    } else {
      runUi.value = res || runUi.value
      uiOverlayDraft.value = (res?.overlay || {}) as Record<string, unknown>
      resetParamsFromSchema()
    }
    if (res?.ai_status === 'fallback') {
      if (notify) Message.warning('AI 输出未通过安全校验，已使用安全预览')
    } else {
      if (notify) Message.success(isResult ? '已生成结果界面预览' : '已生成界面预览')
    }
    return { ok: true, fallback: res?.ai_status === 'fallback' }
  } catch (error: any) {
    const message = error?._message || '生成失败'
    if (notify) Message.error(message)
    return { ok: false, error: message }
  } finally {
    if (isResult) {
      resultUiPreviewing.value = false
    } else {
      uiPreviewing.value = false
    }
  }
}

async function handlePreviewUi(): Promise<PreviewUiOutcome> {
  return previewUiSurface('run_form', uiInstruction.value.trim())
}

async function handleSaveUi(): Promise<boolean> {
  const id = String(route.params.id || '')
  if (!id || !canSaveUiPreference.value) return false
  uiSaving.value = true
  try {
    const res = await portalApi.saveSkillUiPreference(id, {
      surface: 'run_form',
      overlay: uiOverlayDraft.value || runUi.value?.overlay || {},
      generated_by: uiOverlayDraft.value ? 'ai' : runUi.value?.generated_by || 'manual',
      prompt_summary: uiInstruction.value.trim() || undefined,
    })
    runUi.value = res || runUi.value
    uiInstruction.value = typeof res?.saved_prompt === 'string' ? res.saved_prompt : uiInstruction.value
    uiOverlayDraft.value = null
    resetParamsFromSchema()
    await loadUiHistory('run_form')
    Message.success('已保存个人界面')
    return true
  } catch (error: any) {
    if (error?._backendCode === 'UI_PREF_CONFLICT') {
      Message.warning(error?._backendMessage || '个人界面已被其他请求更新，请重新确认后保存')
      await refreshRunUiAfterConflict(id)
      return false
    }
    Message.error(error?._message || '保存失败')
    return false
  } finally {
    uiSaving.value = false
  }
}

async function handleResetUi(): Promise<boolean> {
  const id = String(route.params.id || '')
  if (!id) return false
  uiResetting.value = true
  try {
    const res = await portalApi.deleteSkillUiPreference(id, { surface: 'run_form' })
    runUi.value = res || null
    uiInstruction.value = typeof res?.saved_prompt === 'string' ? res.saved_prompt : ''
    uiOverlayDraft.value = null
    resetParamsFromSchema()
    await loadUiHistory('run_form')
    Message.success('已恢复默认界面')
    return true
  } catch (error: any) {
    Message.error(error?._message || '重置失败')
    return false
  } finally {
    uiResetting.value = false
  }
}

async function handlePreviewResultUi(): Promise<PreviewUiOutcome> {
  return previewUiSurface('result', resultUiInstruction.value.trim())
}

async function handleSaveResultUi(): Promise<boolean> {
  const id = String(route.params.id || '')
  if (!id || !canSaveResultUiPreference.value) return false
  resultUiSaving.value = true
  try {
    const res = await portalApi.saveSkillUiPreference(id, {
      surface: 'result',
      overlay: resultUiOverlayDraft.value || resultUi.value?.overlay || {},
      generated_by: resultUiOverlayDraft.value ? 'ai' : resultUi.value?.generated_by || 'manual',
      prompt_summary: resultUiInstruction.value.trim() || undefined,
    })
    resultUi.value = res || resultUi.value
    resultUiInstruction.value = typeof res?.saved_prompt === 'string' ? res.saved_prompt : resultUiInstruction.value
    resultUiOverlayDraft.value = null
    await loadUiHistory('result')
    Message.success('已保存个人结果界面')
    return true
  } catch (error: any) {
    if (error?._backendCode === 'UI_PREF_CONFLICT') {
      Message.warning(error?._backendMessage || '个人结果界面已被其他请求更新，请重新确认后保存')
      await loadResultUiForSkill(id)
      return false
    }
    Message.error(error?._message || '保存失败')
    return false
  } finally {
    resultUiSaving.value = false
  }
}

async function handleResetResultUi(): Promise<boolean> {
  const id = String(route.params.id || '')
  if (!id) return false
  resultUiResetting.value = true
  try {
    const res = await portalApi.deleteSkillUiPreference(id, { surface: 'result' })
    resultUi.value = res || null
    resultUiInstruction.value = typeof res?.saved_prompt === 'string' ? res.saved_prompt : ''
    resultUiOverlayDraft.value = null
    await loadUiHistory('result')
    Message.success('已恢复默认结果界面')
    return true
  } catch (error: any) {
    Message.error(error?._message || '重置失败')
    return false
  } finally {
    resultUiResetting.value = false
  }
}

async function handleSubmit() {
  if (hasUnsavedUiPreview.value) {
    Message.warning('请先保存界面预览')
    return
  }
  const currentSchema = skill.value?.param_ui_schema
  // 检查顶层必填字段（跳过不可见字段）
  for (const [fieldKey, field] of getSchemaEntries(currentSchema)) {
    if (!isRequiredField(currentSchema, fieldKey)) continue
    if (!isFieldVisible(fieldKey, currentSchema, params.value)) continue
    const value = params.value[fieldKey]
    if (value === '' || value === null || value === undefined) {
      Message.warning(`请填写${field.title || fieldKey}`)
      return
    }
  }

  submitting.value = true
  try {
    const payload = normalizeParamsForSubmit(currentSchema, params.value)
    const uiContext = runUi.value
    const res = await portalApi.submitSkill(String(route.params.id || ''), {
      params: payload,
      ui_pref_id: uiContext?.ui_pref_id || undefined,
      ui_pref_version: uiContext?.ui_pref_version || undefined,
      ui_surface: uiContext?.surface || 'run_form',
      merged_ui_schema_hash: uiContext?.merged_ui_schema_hash || undefined,
    })
    Message.success('已提交执行')
    const nextId = res?.id
    if (nextId) {
      await router.push(`/portal/submissions/${nextId}`)
    }
  } catch (error: any) {
    Message.error(error?._message || '提交失败')
  } finally {
    submitting.value = false
  }
}

function openSubmission(id: string) {
  router.push(`/portal/submissions/${id}`)
}

function submissionStatusColor(status?: string): string {
  if (status === 'completed') return 'green'
  if (status === 'running') return 'arcoblue'
  if (status === 'failed') return 'red'
  return 'gray'
}

watch(() => route.params.id, loadSkill)
watch(() => route.query?.focus, () => {
  void focusSectionFromQuery()
})

onMounted(loadSkill)
</script>

<style scoped>
/* ───────── 页面壳 (Crisp Mono · 设计稿对齐) ───────── */
.portal-detail {
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
  background: var(--ai-bg);
}

/* page-detail-toolbar: 顶部返回栏，flat ai-surface + ai-border */
.portal-detail :deep(.page-detail-toolbar) {
  display: flex;
  align-items: center;
  margin: 0 0 16px;
  padding: 8px 12px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  box-shadow: none;
}

/* 返回按钮：ai-btn sm 语义 */
.portal-back-btn {
  margin: 0;
  font-family: var(--ai-font-sans);
  font-size: 12px;
  color: var(--ai-ink-3);
}
.portal-back-btn:hover {
  color: var(--ai-ink-1);
}

/* ───────── Hero: flat ai-surface + ai-border ───────── */
.report-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) 220px;
  gap: 16px;
  margin: 0 0 16px;
  padding: 22px 28px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  border: 1px solid var(--ai-border);
  box-shadow: none;
}
.report-hero-main {
  min-width: 0;
}
.report-kicker {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 12px;
  color: var(--ai-ink-4);
  font-weight: 500;
}
.report-kicker > span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.report-kicker > span + span::before {
  content: '·';
  margin-right: 6px;
  color: var(--ai-ink-5);
}
.report-title {
  margin: 6px 0 6px;
  font-size: 22px;
  line-height: 1.25;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.hero-version-pill {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.report-summary {
  margin: 0;
  max-width: 920px;
  color: var(--ai-ink-3);
  font-size: 13px;
  line-height: 1.6;
}
.report-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 500;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.report-meta > span {
  display: inline-flex;
  align-items: center;
}
.report-meta > span + span::before {
  content: '·';
  margin-right: 8px;
  color: var(--ai-ink-5);
}
.report-tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}
.report-hero-side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  justify-content: flex-start;
  gap: 6px;
}
.hero-side-label {
  font-size: 10.5px;
  letter-spacing: 0;
  text-transform: uppercase;
  color: var(--ai-ink-4);
  font-weight: 500;
}
.hero-side-time {
  font-size: 14px;
  font-weight: 500;
  color: var(--ai-ink-1);
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
}

/* ───────── 非 active banner ───────── */
.portal-detail :deep(.portal-non-active-banner) {
  margin: 0 0 16px;
  border-radius: var(--ai-radius);
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border: 1px solid transparent;
}

/* ───────── 卡片：flat ai-surface ───────── */
.portal-detail :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}
.portal-detail :deep(.page-list-card .arco-card-body) {
  padding: 16px !important;
}
.portal-detail :deep(.page-list-card .arco-card-header) {
  border-bottom: 1px solid var(--ai-border);
  padding: 12px 16px;
}
.portal-detail :deep(.page-list-card .arco-card-header-title) {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.portal-detail :deep(.arco-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

.detail-card + .detail-card {
  margin-top: 16px;
}
.action-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
.action-row :deep(.arco-btn-primary) {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  border-color: var(--ai-ink-1);
  border-radius: 6px;
  height: 30px;
  padding: 0 14px;
  font-size: 12.5px;
  font-weight: 500;
  font-family: var(--ai-font-sans);
}
.action-row :deep(.arco-btn-primary:hover) {
  background: #000;
  color: var(--ai-surface);
  border-color: #000;
}
.action-row :deep(.arco-btn-primary[disabled]) {
  background: var(--ai-surface-3);
  border-color: var(--ai-border);
  color: var(--ai-ink-4);
}
.description-block {
  color: var(--ai-ink-3);
  line-height: 1.75;
  white-space: pre-wrap;
  font-size: 13px;
}

/* ───────── 嵌套子卡 ───────── */
.nested-card {
  margin-bottom: 8px;
  background: var(--ai-surface-2) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}
.nested-card :deep(.arco-card-body) {
  padding: 12px 16px 4px !important;
}
.nested-card :deep(.arco-card-header) {
  padding: 10px 16px;
  border-bottom: 1px solid var(--ai-border);
}
.nested-card :deep(.arco-card-header-title) {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ai-ink-1);
}

/* ───────── 预览 JSON / 输出面板 ───────── */
.preview-block {
  margin-bottom: 8px;
}
.preview-json {
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  padding: 12px 14px;
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  line-height: 1.6;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--ai-ink-2);
  font-variant-numeric: tabular-nums;
}

/* ───────── 表单 input → ai-input 语义 ───────── */
.portal-detail :deep(.arco-input-wrapper),
.portal-detail :deep(.arco-input-number),
.portal-detail :deep(.arco-textarea-wrapper),
.portal-detail :deep(.arco-select-view),
.portal-detail :deep(.arco-picker) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: 6px !important;
  font-size: 12.5px;
  color: var(--ai-ink-1);
  box-shadow: none !important;
}
.portal-detail :deep(.arco-input-wrapper:hover),
.portal-detail :deep(.arco-input-number:hover),
.portal-detail :deep(.arco-textarea-wrapper:hover),
.portal-detail :deep(.arco-select-view:hover),
.portal-detail :deep(.arco-picker:hover) {
  border-color: var(--ai-border-2) !important;
  background: var(--ai-surface) !important;
}
.portal-detail :deep(.arco-input-focus),
.portal-detail :deep(.arco-input-wrapper:focus-within),
.portal-detail :deep(.arco-textarea-wrapper:focus-within),
.portal-detail :deep(.arco-picker-focused),
.portal-detail :deep(.arco-select-view-focus) {
  border-color: var(--ai-ink-1) !important;
}
.portal-detail :deep(.arco-form-item-label-col label) {
  color: var(--ai-ink-2);
  font-size: 12.5px;
  font-weight: 500;
}
.portal-detail :deep(.arco-form-item-message-help) {
  color: var(--ai-ink-4);
  font-size: 11.5px;
}

/* ───────── 表格 ───────── */
.portal-detail :deep(.arco-table-th) {
  background: var(--ai-surface-2) !important;
  border-bottom: 1px solid var(--ai-border) !important;
  font-size: 11.5px !important;
  text-transform: uppercase !important;
  letter-spacing: 0.04em !important;
  color: var(--ai-ink-4) !important;
  font-weight: 500 !important;
  padding: 8px 12px !important;
}
.portal-detail :deep(.arco-table-td) {
  font-size: 12.5px !important;
  color: var(--ai-ink-1) !important;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 10px 12px !important;
  background: var(--ai-surface) !important;
}
.portal-detail :deep(.arco-table-tr:hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}
.portal-detail :deep(.arco-link) {
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 500;
}
.portal-detail :deep(.arco-link:hover) {
  color: var(--ai-accent-ink);
  background: transparent;
}

/* ───────── descriptions ───────── */
.portal-detail :deep(.arco-descriptions-item-label-block) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  letter-spacing: 0.02em;
  padding: 8px 12px !important;
  border-color: var(--ai-border) !important;
}
.portal-detail :deep(.arco-descriptions-item-value-block) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-1) !important;
  font-size: 12.5px !important;
  padding: 8px 12px !important;
  border-color: var(--ai-border) !important;
}
.portal-detail :deep(.arco-descriptions-table-layout-fixed),
.portal-detail :deep(.arco-descriptions-body) {
  border-color: var(--ai-border) !important;
}

/* ───────── a-tag → ai-pill 映射（20/4/11/500） ───────── */
.portal-detail :deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-family: var(--ai-font-sans);
}
.portal-detail :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  border-color: var(--ai-border);
}
.portal-detail :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}
.portal-detail :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}
.portal-detail :deep(.arco-tag-color-orange) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.portal-detail :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.portal-detail :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}

/* ───────── UI Save Strip ───────── */
.ui-save-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
}
.ui-save-strip.pending {
  border-color: transparent;
  background: var(--ai-warn-soft);
}
.ui-save-copy {
  display: grid;
  min-width: 0;
  gap: 2px;
}
.ui-save-copy strong {
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.35;
}
.ui-save-copy span {
  color: var(--ai-ink-3);
  font-size: 11.5px;
  line-height: 1.45;
}
.ui-save-buttons {
  display: flex;
  flex: 0 0 auto;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.ui-save-buttons :deep(.arco-btn) {
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  color: var(--ai-ink-1);
  border-radius: 6px;
  height: 26px;
  padding: 0 10px;
  font-size: 12px;
  font-weight: 500;
}
.ui-save-buttons :deep(.arco-btn:hover) {
  background: var(--ai-surface-2);
}
.ui-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.ui-actions :deep(.arco-btn) {
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  color: var(--ai-ink-1);
  border-radius: 6px;
  height: 28px;
  font-size: 12px;
  font-weight: 500;
}
.ui-actions :deep(.arco-btn-primary) {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  border-color: var(--ai-ink-1);
}
.ui-actions :deep(.arco-btn-primary:hover) {
  background: #000;
}
.ui-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.ui-hash {
  max-width: 140px;
  overflow: hidden;
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ───────── 悬浮 AI 入口（设计稿 .ai-iconbtn 半语义） ───────── */
.ui-floating-ai {
  position: fixed;
  right: 24px;
  bottom: 28px;
  z-index: 20;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  border: 1px solid var(--ai-ink-1);
  border-radius: 50%;
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  box-shadow: var(--ai-shadow-2);
  cursor: pointer;
}
.ui-floating-ai:disabled {
  border-color: var(--ai-border);
  background: var(--ai-surface-3);
  color: var(--ai-ink-4);
  cursor: not-allowed;
  box-shadow: none;
}
.ui-floating-ai:not(:disabled):hover {
  background: #000;
  border-color: #000;
}

/* ───────── UI Designer Modal ───────── */
.ui-designer {
  display: flex;
  flex-direction: column;
  gap: 14px;
  font-family: var(--ai-font-sans);
}
.ui-designer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.ui-surface-switch {
  display: inline-flex;
  gap: 4px;
  padding: 3px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-2);
}
.ui-surface-btn {
  height: 28px;
  padding: 0 12px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--ai-ink-3);
  font-size: 12.5px;
  font-weight: 500;
  font-family: var(--ai-font-sans);
  cursor: pointer;
}
.ui-surface-btn.active {
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  box-shadow: var(--ai-shadow-1);
}
.ui-surface-btn:hover:not(.active):not(:disabled) {
  color: var(--ai-ink-2);
}
.ui-surface-btn:disabled {
  color: var(--ai-ink-5);
  cursor: not-allowed;
}
.ui-chat-log {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 220px;
  max-height: 380px;
  padding: 14px;
  overflow-y: auto;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
}
.ui-chat-message {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.ui-chat-message.is-user {
  flex-direction: row-reverse;
}
.ui-chat-role {
  flex: 0 0 auto;
  min-width: 28px;
  padding-top: 7px;
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  text-align: center;
}
.ui-chat-bubble {
  max-width: min(620px, 82%);
  padding: 9px 11px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-size: 12.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.ui-chat-message.is-user .ui-chat-bubble {
  border-color: transparent;
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
}
.ui-history-panel {
  display: grid;
  gap: 8px;
  padding: 10px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
}
.ui-history-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.ui-history-head :deep(.arco-btn) {
  color: var(--ai-ink-3);
  font-size: 11.5px;
  font-weight: 500;
}
.ui-history-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px;
}
.ui-history-item {
  display: grid;
  min-width: 0;
  gap: 3px;
  padding: 8px 10px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-family: var(--ai-font-sans);
  cursor: pointer;
  text-align: left;
}
.ui-history-item:hover:not(:disabled):not(.active) {
  border-color: var(--ai-border-2);
  background: var(--ai-surface);
}
.ui-history-item.active {
  border-color: transparent;
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
  cursor: default;
}
.ui-history-item:disabled:not(.active) {
  cursor: wait;
  opacity: 0.7;
}
.ui-history-item strong {
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 12.5px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.ui-history-item.active strong {
  color: var(--ai-accent-ink);
}
.ui-history-item span,
.ui-history-item em {
  overflow: hidden;
  font-size: 11.5px;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ui-history-item em {
  color: var(--ai-ink-4);
}
.ui-chat-compose {
  display: flex;
  flex-direction: column;
}

/* O11: 响应式 */
@media (max-width: 1024px) {
  .report-hero {
    grid-template-columns: 1fr;
  }
  .report-hero-side {
    align-items: flex-start;
  }
}
@media (max-width: 768px) {
  .report-hero {
    margin: 0 0 12px;
    padding: 18px 16px;
  }
  .action-row {
    justify-content: stretch;
  }
  .action-row :deep(.arco-btn) {
    width: 100%;
  }
  .ui-save-strip {
    align-items: stretch;
    flex-direction: column;
  }
  .ui-save-buttons {
    justify-content: stretch;
  }
  .ui-save-buttons :deep(.arco-btn) {
    flex: 1 1 120px;
  }
  .nested-card :deep(.arco-card-body) {
    padding: 8px 10px 4px !important;
  }
  .ui-designer-head {
    align-items: stretch;
    flex-direction: column;
  }
  .ui-surface-switch {
    width: 100%;
  }
  .ui-surface-btn {
    flex: 1;
  }
  .ui-chat-bubble {
    max-width: 88%;
  }
  .ui-floating-ai {
    right: 16px;
    bottom: 18px;
  }
}
</style>
