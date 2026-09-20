<template>
  <a-modal v-model:visible="visible" title="条件编辑器" :width="640" @ok="handleOk" @cancel="handleCancel">
    <div class="condition-editor">
      <div v-for="(rule, idx) in rules" :key="idx" class="condition-rule">
        <!-- 逻辑连接符（第2条开始显示） -->
        <a-select v-if="idx > 0" v-model="rule.logic" size="small" style="width: 60px; margin-right: 8px">
          <a-option value="AND">AND</a-option>
          <a-option value="OR">OR</a-option>
        </a-select>
        <span v-else style="width: 60px; display: inline-block; margin-right: 8px; text-align: center; color: var(--ai-ink-3)">IF</span>

        <!-- 数据源类型 -->
        <a-select v-model="rule.source" size="small" style="width: 110px" @change="onSourceChange(idx)">
          <a-option value="step_output">步骤输出</a-option>
          <a-option value="step_status">步骤状态</a-option>
          <a-option value="param">参数</a-option>
          <a-option value="env">环境变量</a-option>
        </a-select>

        <!-- 引用字段 -->
        <a-select v-if="rule.source === 'step_output'" v-model="rule.ref" size="small" style="width: 140px" allow-search placeholder="步骤.字段">
          <a-option v-for="opt in stepOutputOptions" :key="opt" :value="opt">{{ opt }}</a-option>
        </a-select>
        <a-select v-else-if="rule.source === 'step_status'" v-model="rule.ref" size="small" style="width: 140px" placeholder="步骤">
          <a-option v-for="opt in stepIdOptions" :key="opt" :value="opt + '.status'">{{ opt }}</a-option>
        </a-select>
        <a-input v-else v-model="rule.ref" size="small" style="width: 140px" placeholder="字段名" />

        <!-- 操作符 -->
        <a-select v-model="rule.op" size="small" style="width: 80px">
          <a-option value="==">==</a-option>
          <a-option value="!=">!=</a-option>
          <a-option value=">">></a-option>
          <a-option value="<"><</a-option>
          <a-option value=">=">>=</a-option>
          <a-option value="<="><=</a-option>
          <a-option value="contains">包含</a-option>
        </a-select>

        <!-- 值 -->
        <a-input v-if="rule.source !== 'step_status'" v-model="rule.value" size="small" style="width: 120px" placeholder="值" />
        <a-select v-else v-model="rule.value" size="small" style="width: 120px">
          <a-option value="success">success</a-option>
          <a-option value="failed">failed</a-option>
          <a-option value="skipped">skipped</a-option>
        </a-select>

        <!-- 删除按钮 -->
        <a-button v-if="rules.length > 1" size="small" type="text" status="danger" @click="removeRule(idx)">
          <template #icon><icon-delete /></template>
        </a-button>
      </div>

      <a-space style="margin-top: 12px">
        <a-button size="small" @click="addRule('AND')">+ AND</a-button>
        <a-button size="small" @click="addRule('OR')">+ OR</a-button>
      </a-space>

      <!-- 预览 -->
      <div class="condition-preview">
        <div class="preview-label">预览:</div>
        <code class="preview-text">{{ previewText }}</code>
      </div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { PropType } from 'vue'
import { IconDelete } from '@arco-design/web-vue/es/icon'

const props = defineProps({
  modelValue: { type: [Object, String] as PropType<any>, default: null },
  stepIds: { type: Array as PropType<string[]>, default: () => [] },
  stepOutputs: { type: Object as PropType<Record<string, string[]>>, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue', 'save'])

const visible = ref(false)
const rules = ref<any[]>([{ logic: 'AND', source: 'step_output', ref: '', op: '==', value: '' }])

// 可选的步骤输出字段
const stepOutputOptions = computed(() => {
  const opts: string[] = []
  for (const [stepId, outputs] of Object.entries(props.stepOutputs)) {
    for (const field of (outputs || [])) {
      opts.push(`${stepId}.${field}`)
    }
  }
  // 如果没有具体输出定义，至少提供 stepId.result
  if (opts.length === 0) {
    for (const sid of props.stepIds) {
      opts.push(`${sid}.result`)
    }
  }
  return opts
})

const stepIdOptions = computed(() => props.stepIds)

const previewText = computed(() => {
  return rules.value.map((r, i) => {
    const prefix = i === 0 ? '' : ` ${r.logic} `
    return `${prefix}${r.ref} ${r.op} "${r.value}"`
  }).join('')
})

function addRule(logic: string) {
  rules.value.push({ logic, source: 'step_output', ref: '', op: '==', value: '' })
}

function removeRule(idx: number) {
  rules.value.splice(idx, 1)
}

function onSourceChange(idx: number) {
  rules.value[idx].ref = ''
  rules.value[idx].value = ''
}

function open(condition: any) {
  if (condition && typeof condition === 'object' && condition.children) {
    rules.value = condition.children.map((c: Record<string, string>, i: number) => ({
      logic: i === 0 ? 'AND' : (condition.type || 'AND'),
      source: c.source || 'step_output',
      ref: c.ref || '',
      op: c.op || '==',
      value: c.value || '',
    }))
  } else if (typeof condition === 'string' && condition.trim()) {
    rules.value = [{ logic: 'AND', source: 'step_output', ref: '', op: '==', value: '', _raw: condition }]
  } else {
    rules.value = [{ logic: 'AND', source: 'step_output', ref: '', op: '==', value: '' }]
  }
  visible.value = true
}

function handleOk() {
  const topLogic = rules.value.length > 1 ? rules.value[1]?.logic || 'AND' : 'AND'
  const result = {
    type: topLogic,
    children: rules.value.map(r => ({
      source: r.source,
      ref: r.ref,
      op: r.op,
      value: r.value,
    })),
    _preview: previewText.value,
  }
  emit('save', result)
  visible.value = false
}

function handleCancel() {
  visible.value = false
}

defineExpose({ open })
</script>

<style scoped>
.condition-editor { padding: 8px 0; }
.condition-rule {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.condition-preview {
  margin-top: 16px;
  padding: 10px 12px;
  background: var(--ai-surface-2);
  border-radius: 6px;
}
.preview-label { font-size: 12px; color: var(--ai-ink-3); margin-bottom: 4px; }
.preview-text { font-size: 13px; word-break: break-all; }
</style>
