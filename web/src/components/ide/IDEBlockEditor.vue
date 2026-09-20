<template>
  <div class="block-editor">
    <!-- 返回概览面包屑 -->
    <div class="be-breadcrumb">
      <button class="be-back" @click="emit('goOverview')">
        <icon-left :size="12" /> 概览
      </button>
      <span class="be-sep">/</span>
      <span class="be-current">{{ moduleLabel }}</span>
    </div>
    <MetaBlock v-if="activeModule === 'meta'" :value="doc.meta" :is-create="isCreate" @update="onUpdate('meta', $event)" />
    <GoalBlock v-else-if="activeModule === 'goal'" :value="doc.goal" @update="onUpdate('goal', $event)" />
    <ParamsBlock v-else-if="activeModule === 'params'" :value="doc.params" :skill-id="skillId" @update="onUpdate('params', $event)" />
    <OutputTableBlock v-else-if="activeModule === 'output_table'" :value="doc.output_table" @update="onUpdate('output_table', $event)" @preview-output="emit('preview-output')" />
    <TodoBlock v-else-if="activeModule === 'todos'" :value="doc.todos" @update="onUpdate('todos', $event)" />
    <TestCasesBlock v-else-if="activeModule === 'test_cases'" :value="doc.test_cases" @update="onUpdate('test_cases', $event)" />
    <AntipatternBlock v-else-if="activeModule === 'antipatterns'" :value="doc.antipatterns" :disabled="false" @update="onUpdate('antipatterns', $event)" />
    <DataInputBlock v-else-if="activeModule === 'data_inputs'" :value="doc.data_inputs" :disabled="false" @update="onUpdate('data_inputs', $event)" />
    <RulesBlock v-else-if="activeModule === 'rules'" :value="doc.rules" :disabled="false" @update="onUpdate('rules', $event)" />
    <WorkflowBlock v-else-if="activeModule === 'workflow'" :value="doc.workflow" @openCanvas="emit('openCanvas')" />
    <CustomSectionBlock v-else-if="activeModule === 'custom_sections'" :value="doc.custom_sections" :disabled="false" @update="onUpdate('custom_sections', $event)" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import { IconLeft } from '@arco-design/web-vue/es/icon'
import MetaBlock from './blocks/MetaBlock.vue'
import GoalBlock from './blocks/GoalBlock.vue'
import ParamsBlock from './blocks/ParamsBlock.vue'
import OutputTableBlock from './blocks/OutputTableBlock.vue'
import TodoBlock from './blocks/TodoBlock.vue'
import TestCasesBlock from './blocks/TestCasesBlock.vue'
import WorkflowBlock from './blocks/WorkflowBlock.vue'
import AntipatternBlock from './blocks/AntipatternBlock.vue'
import DataInputBlock from './blocks/DataInputBlock.vue'
import RulesBlock from './blocks/RulesBlock.vue'
import CustomSectionBlock from './blocks/CustomSectionBlock.vue'

const props = defineProps({
  activeModule: { type: String, default: 'meta' },
  doc: { type: Object as PropType<any>, default: () => ({}) },
  isCreate: { type: Boolean, default: false },
  skillId: { type: String, default: '' },
})
const emit = defineEmits(['update', 'openCanvas', 'goOverview', 'preview-output'])

const MODULE_LABELS: Record<string, string> = { meta: '基础信息', goal: '目标', params: '参数', output_table: '输出', todos: '待办', test_cases: '测试', antipatterns: '反例', data_inputs: '数据输入', rules: '决策规则', workflow: '工作流', custom_sections: '自定义章节' }
const moduleLabel = computed(() => MODULE_LABELS[props.activeModule] || props.activeModule)

function onUpdate(key: string, value: any) {
  emit('update', key, value)
}
</script>

<style scoped>
.block-editor { padding: 20px 24px; height: 100%; overflow-y: auto; }
.be-breadcrumb { display: flex; align-items: center; gap: 6px; margin-bottom: 16px; font-size: 13px; }
.be-back {
  display: inline-flex; align-items: center; gap: 3px;
  border: none; background: none; color: var(--ai-ink-4); cursor: pointer;
  padding: 2px 6px; border-radius: 4px; transition: all .1s;
}
.be-back:hover { color: var(--ai-accent-ink); background: var(--ai-surface-2); }
.be-sep { color: var(--ai-ink-4); }
.be-current { color: var(--ai-ink-1); font-weight: 500; }
</style>
