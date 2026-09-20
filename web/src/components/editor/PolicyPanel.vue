<template>
  <div class="policy-panel">
    <div class="pp-tabs">
      <a-tabs v-model:active-key="activeTab" size="small" type="line">
        <a-tab-pane key="form" title="表单视图">
          <!-- v7 B1：YAML schema 表单 + 实时校验 -->
          <div v-if="parsed" class="pp-form">
            <div class="pp-section">
              <div class="pp-section-title">触发</div>
              <div class="pp-row">
                <span>类型</span>
                <a-tag>{{ parsed.trigger?.type || '未设置' }}</a-tag>
              </div>
              <div class="pp-row">
                <span>表达式</span>
                <code>{{ parsed.trigger?.expression || '--' }}</code>
              </div>
            </div>

            <div class="pp-section">
              <div class="pp-section-title">输出</div>
              <div class="pp-row">
                <span>adapter</span>
                <a-tag color="blue">{{ parsed.output?.adapter || '未设置' }}</a-tag>
              </div>
              <div class="pp-row">
                <span>接收方</span>
                <strong>{{ parsed.output?.recipient || '--' }}</strong>
              </div>
            </div>

            <div class="pp-section">
              <div class="pp-section-title">权限（{{ (parsed.permissions || []).length }}）</div>
              <div v-for="(p, i) in parsed.permissions || []" :key="i" class="pp-perm">
                <a-tag :color="p.reversible ? 'green' : 'red'">{{ p.action }}</a-tag>
                <span class="pp-perm-target">{{ p.target }}</span>
                <span v-if="!p.reversible" class="pp-perm-warn">⚠ 不可逆</span>
              </div>
              <div v-if="!(parsed.permissions || []).length" class="pp-empty">无权限项</div>
            </div>

            <div class="pp-section">
              <div class="pp-section-title">风险</div>
              <div class="pp-row">
                <span>等级</span>
                <a-tag :color="parsed.risk?.level === 'R3' ? 'red' : (parsed.risk?.level === 'R2' ? 'orange' : 'green')">
                  {{ parsed.risk?.level || 'R1' }}
                </a-tag>
              </div>
              <div class="pp-row">
                <span>分类</span>
                <span>{{ parsed.risk?.data_classification || 'public' }}</span>
              </div>
              <div class="pp-row">
                <span>部门</span>
                <span>{{ parsed.risk?.department || '--' }}</span>
              </div>
            </div>

            <div class="pp-section">
              <div class="pp-section-title">失败策略</div>
              <div class="pp-row">
                <span>通知</span>
                <span>{{ parsed.failure_policy?.notify || '--' }}</span>
              </div>
              <div class="pp-row">
                <span>自动停用阈值</span>
                <strong>{{ parsed.failure_policy?.auto_disable_after_failures || '--' }} 次</strong>
              </div>
              <div class="pp-row">
                <span>回滚</span>
                <span>{{ parsed.failure_policy?.rollback || '--' }}</span>
              </div>
            </div>

            <div v-if="validationErrors.length" class="pp-errors">
              <div class="pp-errors-title">⚠ 校验错误（{{ validationErrors.length }}）</div>
              <div v-for="(err, i) in validationErrors" :key="i" class="pp-error-item">{{ err }}</div>
            </div>
          </div>
          <div v-else class="pp-empty">YAML 解析失败或为空</div>
        </a-tab-pane>
        <a-tab-pane key="raw" title="YAML 原文">
          <MonacoEditor
            :model-value="modelValue"
            language="yaml"
            :theme="theme"
            :read-only="readOnly"
            :enableAI="enableAI"
            :font-size="fontSize"
            filename="policy.yaml"
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

defineEmits(['update:modelValue', 'save', 'editorReady'])

const activeTab = ref('form')

// 简单 YAML 解析（不引入新 dep，覆盖 90% 场景）
function parseSimpleYaml(text: string) {
  if (!text || !text.trim()) return null
  const lines = text.split('\n')
  const root: Record<string, any> = {}
  const stack: Array<{ obj: any; indent: number }> = [{ obj: root, indent: -1 }]
  for (const rawLine of lines) {
    if (!rawLine.trim() || rawLine.trim().startsWith('#')) continue
    const indent = rawLine.length - rawLine.trimStart().length
    const line = rawLine.trim()
    while (stack.length > 1 && indent <= stack[stack.length - 1].indent) stack.pop()
    const parent = stack[stack.length - 1].obj
    if (line.startsWith('-')) {
      // list item
      if (!Array.isArray(parent.__list)) parent.__list = []
      const item: Record<string, any> = {}
      parent.__list.push(item)
      const after = line.slice(1).trim()
      if (after.includes(':')) {
        const [k, v] = after.split(/:(.*)/s).map((s) => s.trim())
        item[k] = parseValue(v)
      }
      stack.push({ obj: item, indent })
    } else if (line.includes(':')) {
      const [k, vRaw] = line.split(/:(.*)/s).map((s) => s.trim())
      const v = parseValue(vRaw)
      if (v === null && vRaw === '') {
      const child: Record<string, any> = {}
        parent[k] = child
        stack.push({ obj: child, indent })
      } else {
        parent[k] = v
      }
    }
  }
  // 把 __list 改回数组形式
  const fixLists = (obj: any): any => {
    if (!obj || typeof obj !== 'object') return obj
    for (const key of Object.keys(obj)) {
      const val = obj[key]
      if (val && typeof val === 'object' && Array.isArray(val.__list)) {
        obj[key] = val.__list
      }
      if (Array.isArray(obj[key])) obj[key].forEach(fixLists)
      else fixLists(obj[key])
    }
    return obj
  }
  return fixLists(root)
}

function parseValue(v: any) {
  if (v === undefined || v === null) return null
  if (v === 'true') return true
  if (v === 'false') return false
  if (/^-?\d+$/.test(v)) return parseInt(v, 10)
  if (/^-?\d+\.\d+$/.test(v)) return parseFloat(v)
  return v.replace(/^["']|["']$/g, '')
}

const parsed = computed(() => {
  try {
    return parseSimpleYaml(props.modelValue)
  } catch (e) {
    return null
  }
})

const validationErrors = computed(() => {
  const errors: string[] = []
  if (!parsed.value) {
    errors.push('YAML 解析失败')
    return errors
  }
  if (!parsed.value.trigger) errors.push('缺少 trigger 字段')
  if (!parsed.value.output?.adapter) errors.push('缺少 output.adapter 字段')
  if (!parsed.value.risk?.level) errors.push('缺少 risk.level 字段')
  if (!parsed.value.failure_policy) errors.push('缺少 failure_policy（v7 必填）')
  return errors
})
</script>

<style scoped>
.policy-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.pp-form {
  padding: 16px;
  overflow-y: auto;
  max-height: 75vh;
}
.pp-section {
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  padding: 12px 14px;
  margin-bottom: 12px;
  background: var(--ai-surface-2);
}
.pp-section-title {
  font-size: 12px;
  color: var(--ai-ink-2);
  font-weight: 600;
  margin-bottom: 8px;
}
.pp-row {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
  padding: 3px 0;
}
.pp-row > span:first-child {
  color: var(--ai-ink-3);
  min-width: 100px;
}
.pp-perm {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
}
.pp-perm-target {
  color: var(--ai-ink-1);
}
.pp-perm-warn {
  color: var(--ai-bad);
  font-size: 11px;
}
.pp-empty {
  color: var(--ai-ink-3);
  font-size: 12px;
}
.pp-errors {
  background: var(--ai-bad-soft);
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  padding: 12px;
  margin-top: 12px;
}
.pp-errors-title {
  color: var(--ai-bad);
  font-weight: 600;
  margin-bottom: 6px;
}
.pp-error-item {
  font-size: 12px;
  color: var(--ai-bad);
  padding: 2px 0;
}
</style>
