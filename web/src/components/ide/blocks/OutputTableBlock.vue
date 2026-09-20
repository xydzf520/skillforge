<template>
  <div class="block">
    <div class="block-header">
      <div class="block-title">输出定义</div>
      <div class="block-header-actions">
        <a-button size="mini" type="outline" @click="emit('preview-output')">
          <template #icon><icon-eye :size="13" /></template>
          预览
        </a-button>
        <a-button size="mini" type="outline" :disabled="disabled" @click="addRow">+ 添加字段</a-button>
      </div>
    </div>

    <div v-if="!localRows.length" class="block-empty">暂无输出字段定义</div>

    <div class="out-table" v-else>
      <div class="ot-head">
        <span class="ot-col name">字段名</span>
        <span class="ot-col fmt">格式</span>
        <span class="ot-col rcp">接收人</span>
        <span class="ot-col act"></span>
      </div>
      <div v-for="(r, i) in localRows" :key="i" class="ot-row">
        <a-input v-model="r.name" size="mini" class="ot-col name" placeholder="字段名" :disabled="disabled" @input="emitUpdate" />
        <a-select v-model="r.format" size="mini" class="ot-col fmt" :disabled="disabled" @change="emitUpdate">
          <a-option value="text">文本</a-option>
          <a-option value="table">表格</a-option>
          <a-option value="number">数值</a-option>
          <a-option value="chart">图表</a-option>
        </a-select>
        <a-input v-model="r.recipient" size="mini" class="ot-col rcp" placeholder="接收人（角色或部门）" :disabled="disabled" @input="emitUpdate" />
        <a-button size="mini" type="text" status="danger" class="ot-col act" :disabled="disabled" @click="removeRow(i)">
          <icon-delete :size="13" />
        </a-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import type { PropType } from 'vue'
import { IconDelete, IconEye } from '@arco-design/web-vue/es/icon'

const props = defineProps({
  value: { type: Array as PropType<any[]>, default: () => [] },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update', 'preview-output'])

const localRows = ref<any[]>([])

watch(() => props.value, (v) => {
  localRows.value = (v || []).map(o => ({ name: o.name || '', format: o.format || 'text', recipient: o.recipient || '' }))
}, { immediate: true, deep: true })

function emitUpdate() {
  emit('update', localRows.value.map(r => ({ name: r.name, format: r.format, recipient: r.recipient })))
}
function addRow() { localRows.value.push({ name: '', format: 'text', recipient: '' }); emitUpdate() }
function removeRow(i: number) { localRows.value.splice(i, 1); emitUpdate() }
</script>

<style scoped>
.block { padding: 4px 0; }
.block-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.block-header-actions { display: flex; align-items: center; gap: 8px; }
.block-title { font-size: 15px; font-weight: 700; color: var(--ai-ink-1); }
.block-empty { color: var(--ai-ink-4); font-size: 13px; padding: 20px 0; text-align: center; }

.out-table { border: 1px solid var(--ai-border); border-radius: 8px; overflow: hidden; }
.ot-head { display: flex; gap: 4px; padding: 8px 10px; background: var(--ai-surface-2); font-size: 11px; font-weight: 600; color: var(--ai-ink-3); }
.ot-row { display: flex; gap: 4px; padding: 4px 10px; align-items: center; border-top: 1px solid var(--ai-border); }
.ot-col.name { flex: 3; }
.ot-col.fmt { flex: 2; }
.ot-col.rcp { flex: 2; }
.ot-col.act { width: 32px; flex-shrink: 0; }
</style>
