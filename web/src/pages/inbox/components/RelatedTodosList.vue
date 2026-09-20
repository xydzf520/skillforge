<template>
  <div class="related-todos">
    <div v-if="items.length" class="related-todo-list">
      <button
        v-for="item in items"
        :key="item.id"
        type="button"
        class="related-todo-item"
        @click="$emit('open', item.id)"
      >
        <div class="related-todo-topline">
          <div class="related-todo-tags">
            <a-tag size="small" :color="todoKindColor(item.kind)">{{ todoKindLabel(item.kind) }}</a-tag>
            <a-tag size="small" :color="todoStatusColor(item.aggregate_status)">{{ todoStatusLabel(item.aggregate_status) }}</a-tag>
          </div>
          <span class="related-todo-time">{{ formatTime(item.sla_at) }}</span>
        </div>
        <strong class="related-todo-title">{{ item.title }}</strong>
        <div v-if="item.assignees?.length" class="related-todo-assignees">
          处理人：{{ item.assignees.join(' / ') }}
        </div>
      </button>
    </div>
    <SfEmptyState
      v-else
      icon="inbox"
      title="暂无关联待办"
      description="这份报告还没有生成待办"
    />
  </div>
</template>

<script setup lang="ts">
import type { PropType } from 'vue'
import { formatTime } from '@/utils/format'
import type { RelatedTodoSummary } from '@/types/inbox'
import { todoKindColor, todoKindLabel, todoStatusColor, todoStatusLabel } from '../presentation'
import { SfEmptyState } from '@/components/common'

defineProps({
  items: { type: Array as PropType<RelatedTodoSummary[]>, default: () => [] },
})

defineEmits<{
  (event: 'open', id: number): void
}>()
</script>

<style scoped>
.related-todo-list {
  display: grid;
  gap: 12px;
}

.related-todo-item {
  width: 100%;
  padding: 14px 16px;
  border: 1px solid var(--ai-border);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.94);
  text-align: left;
  cursor: pointer;
  transition: border-color 0.16s ease, box-shadow 0.16s ease, transform 0.16s ease;
}

.related-todo-item:hover {
  border-color: var(--ai-border-2);
  box-shadow: var(--ai-shadow-2);
  transform: translateY(-1px);
}

.related-todo-topline {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.related-todo-tags {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.related-todo-title {
  display: block;
  margin-top: 10px;
  color: var(--ai-ink-1);
  font-size: 14px;
  line-height: 1.45;
}

.related-todo-time,
.related-todo-assignees {
  color: var(--ai-ink-2);
  font-size: 12px;
  font-weight: 700;
}

.related-todo-assignees {
  margin-top: 10px;
}

@media (max-width: 768px) {
  .related-todo-topline {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
