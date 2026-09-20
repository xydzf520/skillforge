<template>
  <button
    v-if="capabilityId"
    type="button"
    class="ui-floating-ai"
    :disabled="!canCustomizeUi"
    aria-label="AI 调整页面"
    title="AI 调整页面"
    @click="openDesigner"
  >
    <icon-robot />
  </button>

  <div v-if="showFloatingState" class="ui-floating-state">
    <div class="ui-state-copy">
      <strong>{{ statusLabel }}</strong>
      <span>{{ floatingStateHint }}</span>
    </div>
    <div class="ui-state-actions">
      <button
        v-if="overlayDraft"
        type="button"
        class="ui-state-action primary"
        :disabled="saving"
        @click="saveDesigner"
      >
        保存
      </button>
      <button
        type="button"
        class="ui-state-action"
        :disabled="resetting"
        @click="resetDesigner"
      >
        恢复默认
      </button>
    </div>
  </div>

  <a-modal
    v-model:visible="visible"
    title="AI 调整我的运行页面"
    :footer="false"
    :width="'min(92vw, 820px)'"
    class="ui-designer-modal"
  >
    <div class="ui-designer">
      <div class="ui-designer-head">
        <div class="ui-surface-label">运行页面</div>
        <div class="ui-meta">
          <a-tag :color="statusColor">{{ statusLabel }}</a-tag>
          <a-typography-text v-if="shortHash" class="ui-hash">{{ shortHash }}</a-typography-text>
        </div>
      </div>

      <div class="ui-chat-log">
        <div
          v-for="(message, index) in messages"
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
          <a-button type="text" size="mini" :loading="historyLoading" @click="loadHistory">刷新</a-button>
        </div>
        <div v-if="history.length" class="ui-history-list">
          <button
            v-for="item in history"
            :key="item.id"
            type="button"
            class="ui-history-item"
            :class="{ 'is-active': item.enabled }"
            :disabled="item.enabled || Boolean(restoring[item.id])"
            @click="restoreHistory(item)"
          >
            <span class="ui-history-main">
              <strong>{{ historyFallbackSummary(item) }}</strong>
              <small>{{ historyMeta(item) }}</small>
            </span>
            <span class="ui-history-action">
              {{ item.enabled ? '当前' : restoring[item.id] ? '恢复中' : '恢复' }}
            </span>
          </button>
        </div>
        <SfEmptyState v-else title="暂无保存记录" description="还没有保存过个人页面版本" hint="发送页面调整指令并保存后会显示在这里。" icon="archive" />
      </div>

      <div class="ui-chat-compose">
        <a-textarea
          v-model="input"
          :auto-size="{ minRows: 3, maxRows: 5 }"
          placeholder="例如：把提示词放到最上面，隐藏不常用参数；或把画幅、分辨率、张数组成常用参数区。"
          :disabled="!canCustomizeUi"
        />
        <div class="ui-actions">
          <a-button
            type="primary"
            size="small"
            :loading="previewing"
            :disabled="!canCustomizeUi || !input.trim()"
            @click="sendMessage"
          >
            <template #icon><icon-send /></template>
            发送给 AI
          </a-button>
          <a-button size="small" :loading="saving" :disabled="!canSave" @click="saveDesigner">
            <template #icon><icon-save /></template>
            保存到我的页面
          </a-button>
          <a-button size="small" :loading="resetting" :disabled="!canReset" @click="resetDesigner">
            <template #icon><icon-refresh /></template>
            恢复默认
          </a-button>
        </div>
      </div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconRefresh, IconRobot, IconSave, IconSend } from '@arco-design/web-vue/es/icon'
import { hallApi } from '@/api'
import SfEmptyState from '@/components/common/SfEmptyState.vue'

type DirectCapabilityUi = {
  skill_id?: string
  capability_id?: string
  surface?: string
  ui_pref_id?: string | null
  ui_pref_version?: number | null
  overlay?: Record<string, unknown>
  merged_schema?: Record<string, any>
  merged_ui_schema_hash?: string
  permissions?: { customize_ui?: boolean }
  saved_prompt?: string | null
  ui_pref_invalidated?: { reason?: string } | null
  ai_status?: string
  ai_reason?: string | null
}

type ChatMessage = {
  role: 'user' | 'assistant'
  content: string
}

type UiPreferenceHistoryItem = {
  id: string
  skill_id?: string
  surface?: string
  version?: number
  enabled?: boolean
  generated_by?: string | null
  base_skill_commit?: string | null
  prompt_summary?: string | null
  component_count?: number
  hidden_component_count?: number
  personal_default_count?: number
  created_at?: string | null
  updated_at?: string | null
}

const props = defineProps<{
  capabilityId: string
  modelValue?: DirectCapabilityUi | null
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: DirectCapabilityUi | null): void
}>()

const visible = ref(false)
const input = ref('')
const ui = ref<DirectCapabilityUi | null>(props.modelValue || null)
const overlayDraft = ref<Record<string, unknown> | null>(null)
const previewing = ref(false)
const saving = ref(false)
const resetting = ref(false)
const historyLoading = ref(false)
const history = ref<UiPreferenceHistoryItem[]>([])
const restoring = ref<Record<string, boolean>>({})
const messages = ref<ChatMessage[]>([])

const canCustomizeUi = computed(() => ui.value?.permissions?.customize_ui !== false)
const canSave = computed(() => canCustomizeUi.value && Boolean(overlayDraft.value))
const canReset = computed(() => canCustomizeUi.value && Boolean(ui.value?.ui_pref_id || overlayDraft.value))
const showFloatingState = computed(() => canCustomizeUi.value && Boolean(overlayDraft.value || ui.value?.ui_pref_id))
const floatingStateHint = computed(() => {
  if (overlayDraft.value) return '当前只是预览，保存后才会成为你的默认运行页面。'
  return '已保存为你的个人运行页面，可随时恢复默认。'
})
const shortHash = computed(() => {
  const hash = ui.value?.merged_ui_schema_hash || ''
  return hash ? hash.replace(/^sha256:/, '').slice(0, 10) : ''
})
const statusLabel = computed(() => {
  if (!canCustomizeUi.value) return '无调整权限'
  if (overlayDraft.value) return '预览未保存'
  if (ui.value?.ui_pref_id) return `我的页面 v${ui.value.ui_pref_version || 1}`
  return '默认页面'
})
const statusColor = computed(() => {
  if (!canCustomizeUi.value) return 'gray'
  if (overlayDraft.value) return 'orange'
  if (ui.value?.ui_pref_id) return 'green'
  return 'arcoblue'
})

watch(
  () => props.modelValue,
  (value) => {
    ui.value = value || null
  },
)

watch(
  () => props.capabilityId,
  () => {
    overlayDraft.value = null
    history.value = []
    void loadUi()
  },
  { immediate: true },
)

function setUi(value: DirectCapabilityUi | null): void {
  ui.value = value
  emit('update:modelValue', value)
}

function seedMessages(): void {
  if (messages.value.length) return
  messages.value = [{
    role: 'assistant',
    content: '告诉我希望页面怎么调整。我只会保存为你的个人页面，不影响别人，也不会改能力权限。',
  }]
}

async function loadUi(): Promise<void> {
  if (!props.capabilityId) return
  try {
    const result = await hallApi.directCapabilityUi(props.capabilityId, { surface: 'run_form' }) as DirectCapabilityUi
    setUi(result)
    if (result?.ui_pref_invalidated?.reason) {
      Message.warning(`个人页面已恢复默认：${result.ui_pref_invalidated.reason}`)
    }
  } catch (error: any) {
    Message.error(error?.response?.data?.error?.message || '加载个人页面失败')
  }
}

function openDesigner(): void {
  seedMessages()
  visible.value = true
  void loadHistory()
}

async function sendMessage(): Promise<void> {
  const instruction = input.value.trim()
  if (!instruction || !props.capabilityId) return
  messages.value.push({ role: 'user', content: instruction })
  input.value = ''
  previewing.value = true
  try {
    const result = await hallApi.previewDirectCapabilityUi(props.capabilityId, {
      surface: 'run_form',
      instruction,
      current_overlay: overlayDraft.value || ui.value?.overlay || {},
    }) as DirectCapabilityUi
    overlayDraft.value = result.overlay || {}
    setUi(result)
    const suffix = result.ai_status === 'llm' ? '' : '（已使用规则兜底）'
    messages.value.push({ role: 'assistant', content: `已生成页面结构预览${suffix}，确认后保存到我的页面。` })
  } catch (error: any) {
    messages.value.push({ role: 'assistant', content: '这次页面调整没有生成成功，请换一种说法。' })
    Message.error(error?.response?.data?.error?.message || 'AI 调整失败')
  } finally {
    previewing.value = false
  }
}

async function saveDesigner(): Promise<void> {
  if (!props.capabilityId || !overlayDraft.value) return
  saving.value = true
  try {
    const result = await hallApi.saveDirectCapabilityUiPreference(props.capabilityId, {
      surface: 'run_form',
      overlay: overlayDraft.value,
      generated_by: 'ai',
      prompt_summary: messages.value.filter(item => item.role === 'user').slice(-1)[0]?.content || undefined,
    }) as DirectCapabilityUi
    overlayDraft.value = null
    setUi(result)
    messages.value.push({ role: 'assistant', content: `已保存为我的页面 v${result.ui_pref_version || 1}。` })
    Message.success('已保存到我的页面')
    await loadHistory()
  } catch (error: any) {
    Message.error(error?.response?.data?.error?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function resetDesigner(): Promise<void> {
  if (!props.capabilityId) return
  resetting.value = true
  try {
    const result = await hallApi.deleteDirectCapabilityUiPreference(props.capabilityId, { surface: 'run_form' }) as DirectCapabilityUi
    overlayDraft.value = null
    setUi(result)
    messages.value.push({ role: 'assistant', content: '已恢复默认页面。' })
    Message.success('已恢复默认页面')
    await loadHistory()
  } catch (error: any) {
    Message.error(error?.response?.data?.error?.message || '恢复失败')
  } finally {
    resetting.value = false
  }
}

async function loadHistory(): Promise<void> {
  if (!props.capabilityId) return
  historyLoading.value = true
  try {
    const result = await hallApi.listDirectCapabilityUiPreferences(props.capabilityId, {
      surface: 'run_form',
      limit: 20,
    }) as { items?: UiPreferenceHistoryItem[] }
    history.value = Array.isArray(result?.items) ? result.items : []
  } catch (error: any) {
    Message.error(error?.response?.data?.error?.message || '加载保存记录失败')
  } finally {
    historyLoading.value = false
  }
}

async function restoreHistory(item: UiPreferenceHistoryItem): Promise<void> {
  if (!props.capabilityId || !item.id || item.enabled) return
  restoring.value = { ...restoring.value, [item.id]: true }
  try {
    const result = await hallApi.restoreDirectCapabilityUiPreference(props.capabilityId, item.id, {
      surface: 'run_form',
    }) as DirectCapabilityUi
    overlayDraft.value = null
    setUi(result)
    messages.value.push({ role: 'assistant', content: `已恢复页面 v${item.version || 1}，并保存为我的页面 v${result.ui_pref_version || 1}。` })
    Message.success('已恢复保存记录')
    await loadHistory()
  } catch (error: any) {
    Message.error(error?.response?.data?.error?.message || '恢复保存记录失败')
  } finally {
    const next = { ...restoring.value }
    delete next[item.id]
    restoring.value = next
  }
}

function historyMeta(item: UiPreferenceHistoryItem): string {
  const parts = [`v${item.version || 1}`]
  parts.push(item.generated_by === 'ai' ? 'AI 调整' : '手动保存')
  if (item.component_count) parts.push(`${item.component_count} 个组件`)
  if (item.hidden_component_count) parts.push(`隐藏 ${item.hidden_component_count}`)
  if (item.personal_default_count) parts.push(`默认值 ${item.personal_default_count}`)
  const time = formatHistoryTime(item.created_at || item.updated_at || null)
  if (time) parts.push(time)
  return parts.join(' · ')
}

function historyFallbackSummary(item: UiPreferenceHistoryItem): string {
  if (item.prompt_summary) return item.prompt_summary
  return item.enabled ? '当前使用中的页面' : '未命名页面调整'
}

function formatHistoryTime(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}
</script>

<style scoped>
.ui-floating-ai {
  position: fixed;
  right: 22px;
  bottom: 24px;
  z-index: 40;
  width: 44px;
  height: 44px;
  border: 1px solid var(--ai-border-2);
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--ai-surface);
  background: var(--ai-ink-1);
  box-shadow: var(--ai-shadow-2);
  cursor: pointer;
}

.ui-floating-ai:disabled {
  cursor: not-allowed;
  color: var(--ai-ink-4);
  background: var(--ai-surface-3);
  box-shadow: none;
}

.ui-floating-state {
  position: fixed;
  right: 22px;
  bottom: 78px;
  z-index: 39;
  width: min(320px, calc(100vw - 44px));
  padding: 10px;
  border: 1px solid var(--ai-info-soft);
  border-radius: 8px;
  background: var(--ai-surface);
  box-shadow: var(--ai-shadow-2);
}

.ui-state-copy {
  display: grid;
  gap: 2px;
  margin-bottom: 8px;
}

.ui-state-copy strong {
  color: var(--ai-ink-1);
  font-size: 13px;
  line-height: 1.3;
}

.ui-state-copy span {
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.45;
}

.ui-state-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.ui-state-action {
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--ai-border-2);
  border-radius: 6px;
  color: var(--ai-ink-2);
  background: var(--ai-surface);
  font-size: 12px;
  cursor: pointer;
}

.ui-state-action.primary {
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
  background: var(--ai-ink-1);
}

.ui-state-action:disabled {
  cursor: not-allowed;
  opacity: 0.58;
}

.ui-designer {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.ui-designer-head,
.ui-actions,
.ui-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ui-designer-head {
  justify-content: space-between;
}

.ui-surface-label {
  font-weight: 700;
  color: var(--ai-ink-1);
}

.ui-hash {
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-3);
}

.ui-chat-log {
  min-height: 220px;
  max-height: 300px;
  overflow: auto;
  padding: 14px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
}

.ui-chat-message {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}

.ui-chat-message.is-user {
  flex-direction: row-reverse;
}

.ui-chat-role {
  width: 34px;
  height: 24px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--ai-ink-3);
  background: var(--ai-surface-3);
  flex: 0 0 auto;
}

.ui-chat-message.is-user .ui-chat-role {
  color: var(--ai-surface);
  background: var(--ai-ink-1);
}

.ui-chat-bubble {
  max-width: min(640px, 78%);
  padding: 9px 11px;
  border-radius: 8px;
  background: var(--ai-surface);
  color: var(--ai-ink-2);
  line-height: 1.55;
  white-space: pre-wrap;
}

.ui-chat-message.is-user .ui-chat-bubble {
  color: var(--ai-surface);
  background: var(--ai-ink-1);
}

.ui-history-panel {
  display: grid;
  gap: 8px;
  padding: 10px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
}

.ui-history-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 700;
}

.ui-history-list {
  display: grid;
  gap: 8px;
  max-height: 170px;
  overflow: auto;
}

.ui-history-item {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 10px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
  text-align: left;
  cursor: pointer;
}

.ui-history-item.is-active {
  border-color: var(--ai-ok);
  background: var(--ai-ok-soft);
  cursor: default;
}

.ui-history-item:disabled {
  opacity: 0.78;
  cursor: not-allowed;
}

.ui-history-main {
  min-width: 0;
  display: grid;
  gap: 2px;
}

.ui-history-main strong,
.ui-history-main small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ui-history-main strong {
  color: var(--ai-ink-1);
  font-size: 13px;
  line-height: 1.35;
}

.ui-history-main small {
  color: var(--ai-ink-3);
  font-size: 12px;
}

.ui-history-action {
  flex: 0 0 auto;
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 700;
}

.ui-history-item.is-active .ui-history-action {
  color: var(--ai-ok);
}

.ui-chat-compose {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.ui-actions {
  justify-content: flex-end;
  flex-wrap: wrap;
}
</style>
