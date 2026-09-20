<template>
  <div class="grid-editor">
    <a-card
      v-for="mod in GRID_MODULES"
      :key="mod.key"
      class="grid-cell"
      :class="{ 'grid-cell--dirty': dirtyModules.has(mod.key) }"
      hoverable
      :bordered="true"
      @click="$emit('select-module', mod.key)"
    >
      <div class="grid-cell__header">
        <span class="grid-cell__icon" :style="{ color: mod.color, background: mod.color + '14' }">
          <component :is="iconMap[mod.key]" :size="16" />
        </span>
        <span class="grid-cell__label">{{ mod.label }}</span>
      </div>
      <div class="grid-cell__summary">{{ getSummary(mod.key) }}</div>
      <div class="grid-cell__footer">
        <span class="grid-cell__stat">{{ getStat(mod.key) }}</span>
      </div>
    </a-card>

    <!-- 第 9 格：概览 -->
    <a-card class="grid-cell grid-cell--overview" hoverable :bordered="true" @click="$emit('select-module', 'overview')">
      <div class="grid-cell__header">
        <span class="grid-cell__icon" :style="{ color: '#86909C', background: 'rgba(134,144,156,0.08)' }">
          <IconDashboard :size="16" />
        </span>
        <span class="grid-cell__label">概览</span>
      </div>
      <div class="grid-cell__summary">
        <template v-if="doc?.meta?.status">
          状态: {{ doc.meta.status }}
        </template>
        <template v-else>Skill 总览</template>
      </div>
      <div class="grid-cell__footer">
        <span class="grid-cell__stat">{{ overviewStat }}</span>
      </div>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { SkillDocument } from '@/types/skill'
import {
  IconIdcard,
  IconBulb,
  IconBranch,
  IconSettings,
  IconFile,
  IconExperiment,
  IconClose,
  IconStorage,
  IconDashboard,
  IconCheckCircle,
} from '@arco-design/web-vue/es/icon'

const props = defineProps<{
  doc: SkillDocument
  skillId: string
  isCreate: boolean
  disabled: boolean
  dirtyModules: Set<string>
}>()

defineEmits<{
  (e: 'select-module', moduleKey: string): void
}>()

const GRID_MODULES = [
  { key: 'meta', label: '基础信息', icon: 'IconIdcard', color: '#165DFF' },
  { key: 'goal', label: '目标', icon: 'IconBulb', color: '#0FC6C2' },
  { key: 'rules', label: '决策规则', icon: 'IconBranch', color: '#722ED1' },
  { key: 'params', label: '参数', icon: 'IconSettings', color: '#F77234' },
  { key: 'output_table', label: '输出定义', icon: 'IconFile', color: '#0FC6C2' },
  { key: 'todos', label: '待办输出', icon: 'IconCheckCircle', color: '#14C9C9' },
  { key: 'test_cases', label: '测试用例', icon: 'IconExperiment', color: '#EB0AA4' },
  { key: 'antipatterns', label: '反例', icon: 'IconClose', color: '#F53F3F' },
  { key: 'data_inputs', label: '数据输入', icon: 'IconStorage', color: '#3491FA' },
] as const

// 图标组件映射
const iconMap: Record<string, typeof IconIdcard> = {
  meta: IconIdcard,
  goal: IconBulb,
  rules: IconBranch,
  params: IconSettings,
  output_table: IconFile,
  todos: IconCheckCircle,
  test_cases: IconExperiment,
  antipatterns: IconClose,
  data_inputs: IconStorage,
}

function getSummary(key: string): string {
  const doc = props.doc
  switch (key) {
    case 'meta': return doc?.meta?.name || '未命名'
    case 'goal': return doc?.goal ? `${doc.goal.slice(0, 40)}...` : '未设置'
    case 'rules': return `${doc?.rules?.length || 0} 条规则`
    case 'params': return `${doc?.params?.length || 0} 个参数`
    case 'output_table': return `${doc?.output_table?.length || 0} 个字段`
    case 'todos': return `${doc?.todos?.length || 0} 个待办模板`
    case 'test_cases': return `${doc?.test_cases?.length || 0} 个用例`
    case 'antipatterns': return `${doc?.antipatterns?.length || 0} 个反例`
    case 'data_inputs': return `${doc?.data_inputs?.length || 0} 个数据源`
    default: return ''
  }
}

function getStat(key: string): string {
  const doc = props.doc
  switch (key) {
    case 'meta': {
      const fields = ['name', 'department', 'owner', 'trigger_type'] as const
      const filled = fields.filter(f => doc?.meta?.[f]).length
      return `${filled}/${fields.length} 字段`
    }
    case 'goal': return doc?.goal ? `${doc.goal.length} 字符` : '0 字符'
    case 'rules': {
      const branchCount = (doc?.rules || []).reduce((sum, r) => sum + (r.branches?.length || 0), 0)
      return `${branchCount} 个分支`
    }
    case 'params': return `${doc?.params?.length || 0} 项`
    case 'output_table': return `${doc?.output_table?.length || 0} 项`
    case 'todos': return `${doc?.todos?.length || 0} 项`
    case 'test_cases': return `${doc?.test_cases?.length || 0} 项`
    case 'antipatterns': return `${doc?.antipatterns?.length || 0} 项`
    case 'data_inputs': return `${doc?.data_inputs?.length || 0} 项`
    default: return ''
  }
}

const overviewStat = computed(() => {
  const doc = props.doc
  const totalModules = GRID_MODULES.length
  let filledModules = 0
  if (doc?.meta?.name) filledModules++
  if (doc?.goal) filledModules++
  if (doc?.rules?.length) filledModules++
  if (doc?.params?.length) filledModules++
  if (doc?.output_table?.length) filledModules++
  if (doc?.todos?.length) filledModules++
  if (doc?.test_cases?.length) filledModules++
  if (doc?.antipatterns?.length) filledModules++
  if (doc?.data_inputs?.length) filledModules++
  return `${filledModules}/${totalModules} 模块已填`
})
</script>

<style scoped>
.grid-editor {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--sf-spacing-md, 12px);
  padding: var(--sf-spacing-md, 12px);
  height: 100%;
  overflow-y: auto;
  align-content: start;
}

.grid-cell {
  cursor: pointer;
  transition: transform var(--sf-transition-fast, 0.15s ease), box-shadow var(--sf-transition-fast, 0.15s ease);
  border-radius: 8px;
  min-height: 120px;
  position: relative;
  display: flex;
  flex-direction: column;
}

.grid-cell:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.grid-cell--dirty::after {
  content: '';
  position: absolute;
  top: 8px;
  right: 8px;
  width: 8px;
  height: 8px;
  background: var(--color-primary-6, #165DFF);
  border-radius: 50%;
}

.grid-cell :deep(.arco-card-body) {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 16px;
  gap: 8px;
}

.grid-cell__header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.grid-cell__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  flex-shrink: 0;
}

.grid-cell__label {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
}

.grid-cell__summary {
  flex: 1;
  font-size: 13px;
  color: var(--ai-ink-2);
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.grid-cell__footer {
  margin-top: auto;
}

.grid-cell__stat {
  font-size: 11px;
  color: var(--ai-ink-3);
}
</style>
