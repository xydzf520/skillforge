<template>
  <a-modal
    :visible="visible"
    :title="`申请访问：${sourceName || sourceId}`"
    :ok-loading="submitting"
    :ok-button-props="{ disabled: !canSubmit }"
    ok-text="提交申请"
    cancel-text="取消"
    @update:visible="v => emit('update:visible', v)"
    @ok="submit"
    @cancel="reset"
  >
    <div v-if="ownerContact" class="owner-note">
      审批人：<strong>{{ ownerContact }}</strong>（数据源负责人）
    </div>
    <a-form :model="form" layout="vertical">
      <a-form-item
        label="申请理由"
        :help="reasonHelp"
        :validate-status="reasonInvalid ? 'error' : ''"
        required
      >
        <a-textarea
          v-model="form.reason"
          :max-length="500"
          show-word-limit
          placeholder="请说明使用场景、数据用途和覆盖范围（至少 20 字）"
          :auto-size="{ minRows: 3, maxRows: 6 }"
        />
      </a-form-item>
      <a-form-item label="期望授权有效期（可选）" help="不填：默认 90 天；审批人可改">
        <a-date-picker v-model="form.expires_at" format="YYYY-MM-DD" style="width: 200px" />
      </a-form-item>
    </a-form>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { datasourceApi } from '@/api'

const props = defineProps<{
  visible: boolean
  sourceId: string
  sourceName?: string
  ownerContact?: string
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'submitted', requestId: number): void
}>()

const form = reactive({ reason: '', expires_at: '' as string })
const submitting = ref(false)

const MIN_REASON = 20
const reasonInvalid = computed(() => form.reason.length > 0 && form.reason.trim().length < MIN_REASON)
const reasonHelp = computed(() => {
  const len = form.reason.trim().length
  if (len === 0) return `必填，至少 ${MIN_REASON} 字`
  if (len < MIN_REASON) return `还差 ${MIN_REASON - len} 字`
  return '已达最低字数要求'
})
const canSubmit = computed(() => form.reason.trim().length >= MIN_REASON && !submitting.value)

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  try {
    const r = (await datasourceApi.requestAccess(props.sourceId, {
      reason: form.reason.trim(),
    })) as { id: number; status: string; idempotent?: boolean; message?: string }
    if (r.status === 'granted') {
      Message.info(r.message || '你已拥有访问权限，无需申请')
    } else if (r.idempotent) {
      Message.info('你已提交过申请，审批人正在处理中')
    } else {
      Message.success('申请已提交，审批人将在 1-2 个工作日内处理')
    }
    emit('submitted', r.id)
    reset()
    emit('update:visible', false)
  } catch (e: any) {
    const code = e?._backendCode || e?.response?.data?.code
    if (code === 'REASON_TOO_SHORT') {
      Message.error('理由太短，至少 20 字')
    } else {
      Message.error(e?._message || '申请提交失败')
    }
  } finally {
    submitting.value = false
  }
}

function reset() {
  form.reason = ''
  form.expires_at = ''
}

watch(
  () => props.visible,
  (v) => {
    if (!v) reset()
  }
)
</script>

<style scoped>
.owner-note {
  padding: 10px 12px;
  background: var(--ai-surface-2);
  border-radius: 6px;
  margin-bottom: 14px;
  font-size: 13px;
  color: var(--ai-ink-2);
}
</style>
