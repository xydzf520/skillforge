<template>
  <div class="block">
    <div class="block-header">
      <div class="block-title">测试样例</div>
      <a-button size="mini" type="outline" :disabled="disabled" @click="addCase">+ 添加样例</a-button>
    </div>

    <div v-if="!localCases.length" class="block-empty">暂无测试样例</div>

    <div v-for="(tc, i) in localCases" :key="i" class="tc-card">
      <div class="tc-head">
        <a-input v-model="tc.name" size="mini" placeholder="样例名称" :disabled="disabled" @input="emitUpdate" style="flex:1" />
        <a-button size="mini" type="text" status="danger" :disabled="disabled" @click="removeCase(i)">删除</a-button>
      </div>
      <div class="tc-fields">
        <div class="tc-field">
          <div class="tc-label">输入数据 (JSON)</div>
          <a-textarea
            v-model="tc.input_str"
            :auto-size="{ minRows: 2, maxRows: 6 }"
            :disabled="disabled"
            class="mono"
            placeholder='{"param1": "value1"}'
            @input="emitUpdate"
          />
        </div>
        <div class="tc-field">
          <div class="tc-label">期望输出 (JSON)</div>
          <a-textarea
            v-model="tc.expected_str"
            :auto-size="{ minRows: 2, maxRows: 6 }"
            :disabled="disabled"
            class="mono"
            placeholder='{"result": "..."}'
            @input="emitUpdate"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import type { PropType } from 'vue'

const props = defineProps({
  value: { type: Array as PropType<any[]>, default: () => [] },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update'])

const localCases = ref<any[]>([])

function stringify(v: unknown) {
  if (typeof v === 'string') return v
  try { return JSON.stringify(v, null, 2) } catch { return '' }
}

watch(() => props.value, (v) => {
  localCases.value = (v || []).map(tc => ({
    name: tc.name || '',
    input_str: stringify(tc.input_data ?? tc.input ?? {}),
    expected_str: stringify(tc.expected_output ?? {}),
  }))
}, { immediate: true, deep: true })

function emitUpdate() {
  emit('update', localCases.value.map(tc => {
    let inp = tc.input_str; try { inp = JSON.parse(inp) } catch {}
    let exp = tc.expected_str; try { exp = JSON.parse(exp) } catch {}
    return { name: tc.name, input_data: inp, expected_output: exp }
  }))
}

function addCase() {
  localCases.value.push({ name: `样例${localCases.value.length + 1}`, input_str: '{}', expected_str: '{}' })
  emitUpdate()
}
function removeCase(i: number) { localCases.value.splice(i, 1); emitUpdate() }
</script>

<style scoped>
.block { padding: 4px 0; }
.block-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.block-title { font-size: 15px; font-weight: 700; color: var(--ai-ink-1); }
.block-empty { color: var(--ai-ink-4); font-size: 13px; padding: 20px 0; text-align: center; }

.tc-card {
  border: 1px solid var(--ai-border); border-radius: 8px;
  padding: 10px 12px; margin-bottom: 10px; background: var(--ai-surface-2);
}
.tc-head { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
.tc-fields { display: flex; gap: 10px; }
.tc-field { flex: 1; }
.tc-label { font-size: 11px; font-weight: 600; color: var(--ai-ink-4); margin-bottom: 4px; }
.mono :deep(.arco-textarea) { font-family: var(--ai-font-mono) !important; font-size: 12px !important; }
</style>
