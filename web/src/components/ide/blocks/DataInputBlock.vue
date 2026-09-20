<template>
  <div class="block">
    <div class="block-header">
      <div class="block-title">数据输入</div>
      <a-button size="mini" type="outline" :disabled="disabled" @click="addRow">+ 添加</a-button>
    </div>

    <div v-if="!localRows.length" class="block-empty">暂无数据输入定义</div>

    <div class="di-table" v-else>
      <div class="di-head">
        <span class="di-col name">名称</span>
        <span class="di-col src">数据源</span>
        <span class="di-col freq">更新频率</span>
        <span class="di-col act"></span>
      </div>
      <div v-for="(r, i) in localRows" :key="i" class="di-row">
        <a-input v-model="r.name" size="mini" class="di-col name" placeholder="数据名称" :disabled="disabled" @input="emitUpdate" />
        <a-input v-model="r.source" size="mini" class="di-col src" placeholder="数据源" :disabled="disabled" @input="emitUpdate" />
        <a-select v-model="r.frequency" size="mini" class="di-col freq" :disabled="disabled" @change="emitUpdate">
          <a-option value="实时">实时</a-option>
          <a-option value="每日">每日</a-option>
          <a-option value="每周">每周</a-option>
          <a-option value="手动">手动</a-option>
        </a-select>
        <a-button size="mini" type="text" status="danger" class="di-col act" :disabled="disabled" @click="removeRow(i)">
          <icon-delete :size="13" />
        </a-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import type { PropType } from 'vue'
import { IconDelete } from '@arco-design/web-vue/es/icon'
import type { SkillDataInput } from '@/types/skill'

const props = defineProps({
  value: { type: Array as PropType<SkillDataInput[]>, default: () => [] },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update'])

const localRows = ref<{ name: string; source: string; frequency: string }[]>([])

watch(() => props.value, (v) => {
  localRows.value = (v || []).map(o => ({
    name: o.name || '',
    source: o.source || '',
    frequency: o.frequency || '手动',
  }))
}, { immediate: true, deep: true })

function emitUpdate() {
  emit('update', localRows.value.map(r => ({
    name: r.name,
    source: r.source,
    frequency: r.frequency,
  })))
}

function addRow() {
  localRows.value.push({ name: '', source: '', frequency: '手动' })
  emitUpdate()
}

function removeRow(i: number) {
  localRows.value.splice(i, 1)
  emitUpdate()
}
</script>

<style scoped>
.block { padding: 4px 0; }
.block-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.block-title { font-size: 15px; font-weight: 700; color: var(--ai-ink-1); }
.block-empty { color: var(--ai-ink-4); font-size: 13px; padding: 20px 0; text-align: center; }

.di-table { border: 1px solid var(--ai-border); border-radius: 8px; overflow: hidden; }
.di-head { display: flex; gap: 4px; padding: 8px 10px; background: var(--ai-surface-2); font-size: 11px; font-weight: 600; color: var(--ai-ink-3); }
.di-row { display: flex; gap: 4px; padding: 4px 10px; align-items: center; border-top: 1px solid var(--ai-border); }
.di-col.name { flex: 3; }
.di-col.src { flex: 3; }
.di-col.freq { flex: 2; }
.di-col.act { width: 32px; flex-shrink: 0; }
</style>
