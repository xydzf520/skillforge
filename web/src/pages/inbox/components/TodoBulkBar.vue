<template>
  <transition name="bulkbar-fade">
    <div v-if="selectedCount > 0" class="todo-bulk-bar" data-testid="todo-bulk-bar">
      <div class="todo-bulk-left">
        <a-checkbox
          :model-value="allSelected"
          :indeterminate="someSelected"
          @change="onToggleAll"
        >
          已选 {{ selectedCount }} / {{ totalCount }}
        </a-checkbox>
        <a-button size="small" type="text" @click="$emit('clear')">清除</a-button>
      </div>

      <div class="todo-bulk-actions">
        <a-button
          size="small"
          type="primary"
          status="success"
          :disabled="loading || !canDecide"
          @click="$emit('batch-decide', 'approved')"
        >
          批量通过
        </a-button>
        <a-button
          size="small"
          type="primary"
          status="danger"
          :disabled="loading || !canDecide"
          @click="confirmBatchReject"
        >
          批量驳回
        </a-button>
        <a-button
          size="small"
          type="outline"
          :disabled="loading"
          @click="onExtendClick"
        >
          批量延期
        </a-button>
        <a-button
          size="small"
          type="outline"
          :disabled="loading"
          @click="onReassignClick"
        >
          批量转派
        </a-button>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Modal, Input, InputNumber } from '@arco-design/web-vue'
import { h, ref } from 'vue'

const props = defineProps({
  selectedCount: { type: Number, required: true },
  totalCount: { type: Number, required: true },
  canDecide: { type: Boolean, default: true },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits<{
  (event: 'clear'): void
  (event: 'toggle-all', checked: boolean): void
  (event: 'batch-decide', decision: 'approved' | 'rejected'): void
  (event: 'batch-extend-sla', hours: number): void
  (event: 'batch-reassign', payload: { toUserId: string; reason: string }): void
}>()

const allSelected = computed(() => props.selectedCount > 0 && props.selectedCount === props.totalCount)
const someSelected = computed(() => props.selectedCount > 0 && props.selectedCount < props.totalCount)

function onToggleAll(checked: boolean | string | number) {
  emit('toggle-all', !!checked)
}

function confirmBatchReject() {
  Modal.confirm({
    title: '确认批量驳回',
    content: `即将驳回选中的 ${props.selectedCount} 条待办，是否继续？`,
    onOk: () => emit('batch-decide', 'rejected'),
  })
}

function onExtendClick() {
  const hours = ref<number>(3)
  Modal.open({
    title: '批量延期',
    content: () =>
      h('div', { style: 'padding-top: 4px;' }, [
        h('p', { style: 'margin: 0 0 8px; color: var(--ai-ink-3);' }, '将为选中的待办统一延后 SLA：'),
        h(InputNumber as any, {
          modelValue: hours.value,
          min: 1,
          max: 168,
          step: 1,
          'onUpdate:modelValue': (v: number) => {
            hours.value = Number(v) || 3
          },
        }),
        h('span', { style: 'margin-left: 8px;' }, '小时（1-168）'),
      ]),
    okText: '确认延期',
    cancelText: '取消',
    onOk() {
      emit('batch-extend-sla', Math.max(1, Math.min(168, hours.value)))
    },
  })
}

function onReassignClick() {
  const toUserId = ref('')
  const reason = ref('')
  Modal.open({
    title: '批量转派',
    content: () =>
      h('div', { style: 'padding-top: 4px;' }, [
        h('p', { style: 'margin: 0 0 8px; color: var(--ai-ink-3);' }, '输入接手人用户 ID（需在 Skill 所属部门，且账号为 active）：'),
        h(Input as any, {
          modelValue: toUserId.value,
          placeholder: '例如 bob',
          maxLength: 50,
          'onUpdate:modelValue': (v: string) => {
            toUserId.value = v
          },
        }),
        h('p', { style: 'margin: 12px 0 4px; color: var(--ai-ink-3);' }, '转派原因（会记入审计）：'),
        h(Input.TextArea as any, {
          modelValue: reason.value,
          placeholder: '最多 500 字',
          maxLength: 500,
          autoSize: { minRows: 2, maxRows: 4 },
          'onUpdate:modelValue': (v: string) => {
            reason.value = v
          },
        }),
      ]),
    okText: '确认转派',
    cancelText: '取消',
    onBeforeOk() {
      return !!toUserId.value.trim()
    },
    onOk() {
      emit('batch-reassign', {
        toUserId: toUserId.value.trim(),
        reason: reason.value.trim(),
      })
    },
  })
}
</script>

<style scoped>
.todo-bulk-bar {
  position: sticky;
  top: 8px;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 10px;
  margin-bottom: 10px;
  border-radius: 4px;
  border: 1px solid var(--ai-border);
  background: var(--ai-accent-soft);
  box-shadow: var(--ai-shadow-2);
}

.todo-bulk-left {
  display: flex;
  align-items: center;
  gap: 12px;
  font-weight: 700;
  color: var(--ai-ink-1);
}

.todo-bulk-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.bulkbar-fade-enter-active,
.bulkbar-fade-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.bulkbar-fade-enter-from,
.bulkbar-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
