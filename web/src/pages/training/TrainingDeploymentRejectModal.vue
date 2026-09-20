<template>
  <a-modal
    :visible="visible"
    title="驳回模型部署"
    ok-text="驳回"
    cancel-text="取消"
    :ok-loading="loading"
    :ok-button-props="{ status: 'danger' }"
    @ok="submit"
    @cancel="close"
  >
    <div class="reject-modal-body">
      <p>驳回后该部署请求结束，可基于同一训练任务重新提交部署审批。</p>
      <a-textarea
        v-model="reason"
        placeholder="填写驳回原因，至少 10 个字符"
        :max-length="1000"
        :auto-size="{ minRows: 3, maxRows: 6 }"
        show-word-limit
      />
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { Message } from '@arco-design/web-vue'

const props = defineProps<{
  visible: boolean
  loading?: boolean
}>()

const emit = defineEmits<{
  (event: 'update:visible', value: boolean): void
  (event: 'submit', reason: string): void
}>()

const reason = ref('')

watch(
  () => props.visible,
  (visible) => {
    if (visible) reason.value = ''
  },
)

function close() {
  emit('update:visible', false)
}

function submit() {
  const value = reason.value.trim()
  if (value.length < 10) {
    Message.warning('请填写至少 10 个字符的驳回原因')
    return
  }
  emit('submit', value)
}
</script>

<style scoped>
.reject-modal-body {
  display: grid;
  gap: 10px;
}
.reject-modal-body p {
  margin: 0;
  color: var(--ai-ink-2);
  line-height: 1.6;
}
</style>
