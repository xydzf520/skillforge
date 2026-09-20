<template>
  <div class="tt-filter-bar" data-testid="tasktree-filter">
    <div class="tt-filter-stack">
      <div class="tt-filter-row">
        <a-input-search
          v-model="nameQueryModel"
          size="small"
          placeholder="搜索节点名 / ID（空格分多词）"
          allow-clear
          data-testid="tasktree-filter-search"
        />
      </div>

      <div class="tt-filter-row tt-filter-row--wrap">
        <span class="tt-filter-label">时间窗</span>
        <button
          v-for="option in windowOptions"
          :key="option.value"
          type="button"
          class="tt-filter-pill"
          :class="{ active: windowModel === option.value }"
          @click="windowModel = option.value"
        >{{ option.label }}</button>

        <span class="tt-filter-gap" />
        <span class="tt-filter-label">密度</span>
        <button
          type="button"
          class="tt-filter-pill"
          :class="{ active: compactModel }"
          @click="compactModel = true"
        >紧凑</button>
        <button
          type="button"
          class="tt-filter-pill"
          :class="{ active: !compactModel }"
          @click="compactModel = false"
        >宽松</button>

        <span class="tt-filter-spacer" />
        <div v-if="canSwitchDepartment" class="tt-filter-department">
          <span class="tt-filter-label">部门</span>
          <a-select v-model="departmentModel" size="small" class="tt-department-select">
            <a-option value="__all_lv1__">全部</a-option>
            <a-option
              v-for="option in departmentOptions"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </a-option>
          </a-select>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { TaskTreeTimeWindow, type TaskTreeTimeWindowValue } from './types'

type DepartmentOption = {
  label: string
  value: string
}

const props = defineProps<{
  showAnomalyOnly: boolean
  window: TaskTreeTimeWindowValue
  department: string
  canSwitchDepartment: boolean
  departmentOptions: DepartmentOption[]
  nameQuery: string
  compactView: boolean
}>()

const emit = defineEmits<{
  'update:showAnomalyOnly': [value: boolean]
  'update:window': [value: TaskTreeTimeWindowValue]
  'update:department': [value: string]
  'update:nameQuery': [value: string]
  'update:compactView': [value: boolean]
}>()

const windowOptions = [
  { label: '1h', value: TaskTreeTimeWindow.ONE_HOUR },
  { label: '24h', value: TaskTreeTimeWindow.ONE_DAY },
  { label: '7d', value: TaskTreeTimeWindow.SEVEN_DAYS },
]

const windowModel = computed({
  get: () => props.window,
  set: (value: TaskTreeTimeWindowValue) => emit('update:window', value),
})

const departmentModel = computed({
  get: () => props.department,
  set: (value: string) => emit('update:department', value || '__all_lv1__'),
})

const nameQueryModel = computed({
  get: () => props.nameQuery,
  set: (value: string) => emit('update:nameQuery', value || ''),
})

const compactModel = computed({
  get: () => props.compactView,
  set: (value: boolean | string | number) => emit('update:compactView', Boolean(value)),
})
</script>

<style scoped>
.tt-filter-bar {
  padding: 14px 16px;
  background: var(--ai-surface);
  border: 0;
  border-bottom: 1px solid var(--ai-border);
  border-radius: 0;
  box-shadow: none;
  font-family: var(--ai-font-sans);
}

.tt-filter-stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tt-filter-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.tt-filter-row--wrap {
  flex-wrap: wrap;
  gap: 6px;
}

.tt-filter-row :deep(.arco-input-wrapper) {
  width: 100%;
  background: var(--ai-surface);
  border-color: var(--ai-border);
  border-radius: 4px;
}

.tt-filter-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 24px;
  padding: 0 9px;
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  background: var(--ai-surface);
  color: var(--ai-ink-2);
  font-size: 11.5px;
  font-weight: 500;
  cursor: pointer;
  font-family: var(--ai-font-sans);
}
.tt-filter-pill:hover {
  background: var(--ai-surface-2);
}
.tt-filter-pill.active {
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
}

.tt-filter-label {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  margin-right: 2px;
  white-space: nowrap;
}

.tt-filter-gap {
  width: 12px;
  flex: 0 0 12px;
}

.tt-filter-spacer {
  flex: 1 1 auto;
  min-width: 12px;
}

.tt-filter-department {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  margin-left: auto;
}

.tt-department-select {
  width: 120px;
}
.tt-department-select :deep(.arco-select-view-single) {
  border-radius: 4px;
  border-color: var(--ai-border);
}

@media (max-width: 640px) {
  .tt-filter-bar { padding: 12px; }
  .tt-filter-label {
    letter-spacing: 0;
  }
  .tt-filter-spacer {
    flex-basis: 100%;
  }
  .tt-department-select {
    width: 150px;
  }
}
</style>
