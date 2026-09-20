<template>
  <div class="block">
    <div class="block-header">
      <div>
        <div class="block-title">待办输出</div>
        <div class="block-subtitle"><code>output.todos[]</code> 会进入收件中心待办</div>
      </div>
      <div class="block-header-actions">
        <a-button size="mini" type="outline" :disabled="disabled" @click="addTodo('dispatch')">+ 派发待办</a-button>
        <a-button size="mini" type="outline" :disabled="disabled" @click="addTodo('review')">+ 审核待办</a-button>
      </div>
    </div>

    <div v-if="!localRows.length" class="block-empty">未配置待办输出</div>

    <div v-else class="todo-list">
      <div v-for="(todo, index) in localRows" :key="index" class="todo-card">
        <div class="todo-card-head">
          <a-select v-model="todo.kind" size="mini" class="todo-kind" :disabled="disabled" @change="emitUpdate">
            <a-option value="dispatch">派发</a-option>
            <a-option value="review">审核</a-option>
          </a-select>
          <a-input v-model="todo.title" size="mini" class="todo-title-input" placeholder="标题模板，如 {item_id} 链接下滑整改" :disabled="disabled" @input="emitUpdate" />
          <a-button size="mini" type="text" status="danger" :disabled="disabled" @click="removeTodo(index)">
            <icon-delete :size="13" />
          </a-button>
        </div>

        <a-textarea
          v-model="todo.summary"
          size="mini"
          :auto-size="{ minRows: 2, maxRows: 4 }"
          placeholder="摘要模板：问题 + 建议动作"
          :disabled="disabled"
          @input="emitUpdate"
        />

        <div class="todo-grid">
          <a-select v-model="todo.reviewer_role" size="mini" :disabled="disabled" placeholder="接收角色" @change="emitUpdate">
            <a-option value="biz_owner">biz_owner</a-option>
            <a-option value="operator">operator</a-option>
            <a-option value="ai_engineer">ai_engineer</a-option>
            <a-option value="admin">admin</a-option>
            <a-option value="director">director</a-option>
          </a-select>
          <a-input v-model="todo.reviewers_text" size="mini" placeholder="接收人 user_id，逗号分隔" :disabled="disabled" @input="emitUpdate" />
          <a-input-number v-model="todo.sla_hours" size="mini" :min="1" :max="168" placeholder="SLA小时" :disabled="disabled" @change="emitUpdate" />
          <a-select v-model="todo.decision_mode" size="mini" :disabled="disabled" @change="emitUpdate">
            <a-option value="any_of">任一处理</a-option>
            <a-option value="all_of">全部处理</a-option>
            <a-option value="independent">独立处理</a-option>
          </a-select>
        </div>

        <div class="todo-grid todo-grid-wide">
          <a-input v-model="todo.payload_fields_text" size="mini" placeholder="payload 字段，如 item_id,metric,reason,suggestion" :disabled="disabled" @input="emitUpdate" />
          <a-input
            v-if="todo.kind === 'dispatch'"
            v-model="todo.task_content"
            size="mini"
            placeholder="派发任务内容模板"
            :disabled="disabled"
            @input="emitUpdate"
          />
        </div>
      </div>
    </div>

    <div class="contract-preview">
      <span class="preview-label">返回结构</span>
      <code>return {"{ ...业务字段, todos: [{ kind, title, summary, reviewer_role, tasks }] }"}</code>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import type { PropType } from 'vue'
import { IconDelete } from '@arco-design/web-vue/es/icon'
import type { SkillTodoSpec } from '@/types/skill'

type LocalTodo = {
  kind: string
  title: string
  summary: string
  reviewer_role: string
  reviewers_text: string
  sla_hours: number | null
  decision_mode: string
  payload_fields_text: string
  task_content: string
}

const props = defineProps({
  value: { type: Array as PropType<SkillTodoSpec[]>, default: () => [] },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['update'])
const localRows = ref<LocalTodo[]>([])

watch(() => props.value, (value) => {
  localRows.value = (value || []).map(toLocal)
}, { immediate: true, deep: true })

function toLocal(todo: SkillTodoSpec): LocalTodo {
  return {
    kind: todo.kind || 'dispatch',
    title: todo.title || '',
    summary: todo.summary || '',
    reviewer_role: todo.reviewer_role || 'biz_owner',
    reviewers_text: (todo.reviewers || []).join(', '),
    sla_hours: typeof todo.sla_hours === 'number' ? todo.sla_hours : 24,
    decision_mode: todo.decision_mode || 'any_of',
    payload_fields_text: (todo.payload_fields || []).join(', '),
    task_content: todo.tasks?.[0]?.content || '',
  }
}

function fromLocal(todo: LocalTodo): SkillTodoSpec {
  const reviewers = todo.reviewers_text.split(',').map(item => item.trim()).filter(Boolean)
  const payloadFields = todo.payload_fields_text.split(',').map(item => item.trim()).filter(Boolean)
  const out: SkillTodoSpec = {
    kind: todo.kind || 'dispatch',
    title: todo.title,
    summary: todo.summary,
    reviewer_role: todo.reviewer_role || undefined,
    reviewers: reviewers.length ? reviewers : undefined,
    sla_hours: todo.sla_hours || undefined,
    decision_mode: todo.decision_mode || 'any_of',
    payload_fields: payloadFields.length ? payloadFields : undefined,
  }
  if (out.kind === 'dispatch') {
    out.tasks = [{ content: todo.task_content || todo.summary || todo.title }]
  }
  return out
}

function emitUpdate() {
  emit('update', localRows.value.map(fromLocal))
}

function addTodo(kind: 'dispatch' | 'review') {
  localRows.value.push({
    kind,
    title: kind === 'dispatch' ? '{item_id} 整改待办' : '{title} 审核确认',
    summary: '',
    reviewer_role: 'biz_owner',
    reviewers_text: '',
    sla_hours: 24,
    decision_mode: 'any_of',
    payload_fields_text: 'item_id, metric, reason, suggestion',
    task_content: kind === 'dispatch' ? '{suggestion}' : '',
  })
  emitUpdate()
}

function removeTodo(index: number) {
  localRows.value.splice(index, 1)
  emitUpdate()
}
</script>

<style scoped>
.block { padding: 4px 0; }
.block-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.block-header-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.block-title { font-size: 15px; font-weight: 700; color: var(--ai-ink-1); }
.block-subtitle { margin-top: 3px; font-size: 12px; color: var(--ai-ink-3); font-weight: 600; }
.block-subtitle code { font-family: var(--ai-font-mono); color: var(--ai-info); }
.block-empty { color: var(--ai-ink-4); font-size: 13px; padding: 20px 0; text-align: center; }
.todo-list { display: flex; flex-direction: column; gap: 10px; }
.todo-card {
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  padding: 10px;
  background: var(--ai-surface);
}
.todo-card-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.todo-kind { width: 92px; flex-shrink: 0; }
.todo-title-input { flex: 1; min-width: 0; }
.todo-grid { display: grid; grid-template-columns: 1fr 1.4fr 110px 120px; gap: 8px; margin-top: 8px; }
.todo-grid-wide { grid-template-columns: 1fr 1fr; }
.contract-preview {
  margin-top: 12px;
  padding: 8px 10px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
}
.preview-label { flex-shrink: 0; font-size: 12px; color: var(--ai-ink-3); font-weight: 700; }
.contract-preview code {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--ai-font-mono);
  font-size: 12px;
  color: var(--ai-ink-2);
}
@media (max-width: 860px) {
  .block-header { flex-direction: column; }
  .todo-grid, .todo-grid-wide { grid-template-columns: 1fr; }
}
</style>
