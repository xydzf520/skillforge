<template>
  <section
    class="tt-dept-section"
    :class="{ collapsed, virtual: department.is_virtual, compact }"
    :data-testid="`tasktree-department-${department.department_id}`"
  >
    <header
      class="tt-dept-header"
      role="button"
      tabindex="0"
      :aria-expanded="!collapsed"
      :aria-label="`${department.department_name}，${collapsed ? '展开' : '折叠'}`"
      @click="$emit('toggle', departmentKey)"
      @keydown.enter.prevent="$emit('toggle', departmentKey)"
      @keydown.space.prevent="$emit('toggle', departmentKey)"
    >
      <button
        type="button"
        class="tt-dept-toggle"
        :aria-label="collapsed ? '展开' : '折叠'"
        tabindex="-1"
        @click.stop="$emit('toggle', departmentKey)"
      >
        <SfShellIcon name="chevr" class="tt-chevron" :class="{ open: !collapsed }" />
      </button>

      <div class="tt-dept-copy">
        <div class="tt-dept-title-row">
          <SfShellIcon name="dept" class="tt-dept-icon" />
          <h3>{{ department.department_name }}</h3>
          <span v-if="anomalyCount > 0" class="tt-dept-pill bad">异常 {{ anomalyCount }}</span>
          <a-tooltip v-if="department.is_virtual" :content="isPlatformPool ? '平台统一调度池，下面可挂载多台训练、推理或执行机器' : '不在组织架构但有节点归属的临时分组'">
            <span class="tt-dept-pill">临时分组</span>
          </a-tooltip>
          <span class="tt-dept-pill">{{ isPlatformPool ? '机器' : '节点' }} {{ department.node_count || 0 }}</span>
        </div>
      </div>

      <div class="tt-dept-stats">
        <span><span class="tt-dot online" />{{ department.online_count || 0 }}</span>
        <span><span class="tt-dot offline" />{{ department.offline_count || 0 }}</span>
      </div>
    </header>

    <div v-show="!collapsed" class="tt-instance-grid" :class="{ 'tt-instance-grid--few': (department.instances || []).length <= 2 }">
      <TaskTreeInstanceCard
        v-for="instance in department.instances || []"
        :key="instance.instance_id"
        :instance="instance"
        :window="window"
        :compact="compact"
        :selected="selectedInstanceId === instance.instance_id"
        :selected-range="selectedInstanceId === instance.instance_id ? selectedRange : null"
        @select="$emit('select-instance', $event)"
        @open-diagnosis="(inst, run) => $emit('open-diagnosis', inst, run)"
        @focus-bucket="(inst, bucket) => $emit('focus-bucket', inst, bucket)"
      />
    </div>

    <a-empty
      v-if="!collapsed && !(department.instances || []).length"
      description="该部门暂无节点接入"
    />
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'
import TaskTreeInstanceCard from './TaskTreeInstanceCard.vue'
import { computeDepartmentAnomalyCount } from './helpers'
import type {
  TaskTreeDepartmentNode,
  TaskTreeInstanceNode,
  TaskTreeSkillRunItem,
  TaskTreeTimelineBucket,
  TaskTreeTimeWindowValue,
} from './types'

const props = defineProps<{
  department: TaskTreeDepartmentNode
  window: TaskTreeTimeWindowValue
  collapsed: boolean
  selectedInstanceId: string
  selectedRange?: { start: string; end: string } | null
  compact?: boolean
}>()

defineEmits<{
  toggle: [departmentKey: string]
  'select-instance': [instance: TaskTreeInstanceNode]
  'open-diagnosis': [instance: TaskTreeInstanceNode, run: TaskTreeSkillRunItem]
  'focus-bucket': [instance: TaskTreeInstanceNode, bucket: TaskTreeTimelineBucket]
}>()

const departmentKey = computed(() => props.department.department_name)
const isPlatformPool = computed(() => (
  props.department.department_id === '__virtual_unassigned__' &&
  props.department.department_name === '平台主节点'
))
const anomalyCount = computed(() => {
  return props.department.anomaly_count ?? computeDepartmentAnomalyCount(props.department, props.window)
})
</script>

<style scoped>
/* 设计稿 .ai-card：surface + border + radius 8px，无 gradient/大阴影 */
.tt-dept-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 0 0 10px;
  margin-bottom: 8px;
  background: var(--ai-surface);
  border: 0;
  border-bottom: 1px dashed var(--ai-border);
  border-radius: 0;
  box-shadow: none;
  font-family: var(--ai-font-sans);
}

.tt-dept-section.virtual {
  background: var(--ai-surface);
}

.tt-dept-header {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  padding: 0 0 2px;
  background: none;
}

.tt-dept-toggle {
  width: 18px;
  height: 18px;
  padding: 0;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--ai-ink-4);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  cursor: pointer;
}
.tt-dept-toggle:hover {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}

.tt-chevron {
  width: 12px;
  height: 12px;
  transition: transform 0.18s ease;
}

.tt-chevron.open {
  transform: rotate(90deg);
}

.tt-dept-copy {
  min-width: 0;
  flex: 1;
}

.tt-dept-title-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.tt-dept-icon {
  width: 12px;
  height: 12px;
  color: var(--ai-ink-3);
  flex: 0 0 12px;
}

.tt-dept-title-row h3 {
  margin: 0;
  color: var(--ai-ink-2);
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.tt-dept-pill {
  display: inline-flex;
  align-items: center;
  height: 16px;
  padding: 0 5px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  color: var(--ai-ink-3);
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
  font-family: var(--ai-font-mono);
}
.tt-dept-pill.bad {
  background: var(--ai-bad-soft);
  border-color: transparent;
  color: var(--ai-bad);
}

.tt-dept-stats {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  color: var(--ai-ink-3);
  font-size: 11.5px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
}

.tt-dept-stats span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.tt-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.tt-dot.online { background: var(--ai-ok); }
.tt-dot.offline { background: var(--ai-bad); }

.tt-instance-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tt-instance-grid--few {
  display: flex;
}

.tt-dept-section.compact {
  padding: 0 0 8px;
  gap: 7px;
}

.tt-dept-section.compact .tt-instance-grid {
  gap: 8px;
}

.tt-dept-header:focus-visible {
  outline: 1.5px solid var(--ai-ink-1);
  outline-offset: 2px;
  border-radius: var(--ai-radius-s);
}

@media (max-width: 760px) {
  .tt-dept-header {
    align-items: flex-start;
  }

  .tt-dept-stats {
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
  }
}
</style>
