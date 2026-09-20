<template>
  <div class="intent-panel">
    <div class="ip-tabs">
      <a-tabs v-model:active-key="activeTab" size="small" type="line">
        <a-tab-pane key="form" title="结构化编辑">
          <!-- v7 B1：可视化 intent.md 字段编辑（goal / trigger / permissions） -->
          <div v-if="parsedFields" class="ip-form">
            <div class="ip-field">
              <label>目标</label>
              <a-textarea
                :model-value="parsedFields.goal"
                @update:model-value="updateField('goal', $event)"
                :auto-size="{ minRows: 1, maxRows: 3 }"
                placeholder="一句业务目标"
              />
            </div>
            <div class="ip-field">
              <label>触发</label>
              <a-input
                :model-value="parsedFields.trigger"
                @update:model-value="updateField('trigger', $event)"
                placeholder="例如：每天 18:00 / webhook / 手动"
              />
            </div>
            <div class="ip-field">
              <label>输出</label>
              <a-input
                :model-value="parsedFields.output"
                @update:model-value="updateField('output', $event)"
                placeholder="adapter → 接收方"
              />
            </div>
            <div class="ip-field">
              <label>风险等级</label>
              <a-select
                :model-value="parsedFields.risk"
                @update:model-value="updateField('risk', $event)"
                style="width: 120px"
              >
                <a-option value="R1">R1（低）</a-option>
                <a-option value="R2">R2（中）</a-option>
                <a-option value="R3">R3（高）</a-option>
              </a-select>
            </div>
            <div class="ip-field">
              <label>所需权限</label>
              <div class="ip-perms">
                <a-tag
                  v-for="(perm, i) in parsedFields.permissions"
                  :key="i"
                  :color="perm.includes('不可逆') ? 'red' : 'blue'"
                >{{ perm }}</a-tag>
                <span v-if="!parsedFields.permissions.length" class="ip-empty">未抽取到权限项</span>
              </div>
            </div>
          </div>
          <div v-else class="ip-empty">无法从当前 intent.md 解析出结构化字段</div>
        </a-tab-pane>
        <a-tab-pane key="raw" title="原始 Markdown">
          <MonacoEditor
            :model-value="modelValue"
            language="markdown"
            :theme="theme"
            :read-only="readOnly"
            :enableAI="enableAI"
            :font-size="fontSize"
            filename="intent.md"
            :height="height"
            @update:modelValue="$emit('update:modelValue', $event)"
            @save="$emit('save')"
            @editorReady="$emit('editorReady', $event)"
          />
        </a-tab-pane>
      </a-tabs>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import MonacoEditor from '@/components/editor/MonacoEditor.vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  readOnly: { type: Boolean, default: false },
  theme: { type: String, default: 'vs' },
  fontSize: { type: Number, default: 14 },
  height: { type: String, default: '100%' },
  enableAI: { type: Boolean, default: true },
})

const emit = defineEmits(['update:modelValue', 'save', 'editorReady'])

const activeTab = ref('form')

// 把 intent.md 文本解析成结构化字段
const parsedFields = computed(() => {
  const text = props.modelValue || ''
  if (!text.trim()) return null
  const lines = text.split('\n')
  const fields: { goal: string; trigger: string; output: string; risk: string; permissions: string[] } = { goal: '', trigger: '', output: '', risk: 'R2', permissions: [] }
  let inPermissions = false
  for (const line of lines) {
    const lt = line.trim()
    if (lt.startsWith('- 目标')) {
      fields.goal = lt.replace(/^-\s*目标\s*[：:]/, '').trim()
    } else if (lt.startsWith('- 触发')) {
      fields.trigger = lt.replace(/^-\s*触发\s*[：:]/, '').trim()
    } else if (lt.startsWith('- 输出')) {
      fields.output = lt.replace(/^-\s*输出\s*[：:]/, '').trim()
    } else if (lt.startsWith('- 风险')) {
      const risk = lt.replace(/^-\s*风险\s*[：:]/, '').trim()
      if (risk) fields.risk = risk.split(/\s/)[0]
    } else if (lt.startsWith('## 所需权限')) {
      inPermissions = true
      continue
    } else if (lt.startsWith('## ')) {
      inPermissions = false
    } else if (inPermissions && lt.startsWith('-')) {
      fields.permissions.push(lt.replace(/^-\s*/, ''))
    }
  }
  return fields
})

function updateField(key: string, value: string) {
  // 简单回写：替换原文中对应的行
  const lines = (props.modelValue || '').split('\n')
  const updated = lines.map((line) => {
    const lt = line.trim()
    if (key === 'goal' && lt.startsWith('- 目标')) return `- 目标：${value}`
    if (key === 'trigger' && lt.startsWith('- 触发')) return `- 触发：${value}`
    if (key === 'output' && lt.startsWith('- 输出')) return `- 输出：${value}`
    if (key === 'risk' && lt.startsWith('- 风险')) return `- 风险：${value}`
    return line
  })
  emit('update:modelValue', updated.join('\n'))
}
</script>

<style scoped>
.intent-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.ip-tabs {
  height: 100%;
}
.ip-form {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.ip-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ip-field label {
  font-size: 12px;
  color: var(--ai-ink-2);
  font-weight: 500;
}
.ip-perms {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.ip-empty {
  color: var(--ai-ink-3);
  font-size: 12px;
}
</style>
