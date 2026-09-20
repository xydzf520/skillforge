<template>
  <div class="block">
    <div class="block-header">
      <div class="block-title">反例</div>
      <a-button size="mini" type="outline" :disabled="disabled" @click="addItem">+ 添加反例</a-button>
    </div>

    <div v-if="!localItems.length" class="block-empty">暂无反例</div>

    <template v-for="(item, i) in localItems" :key="i">
      <a-divider v-if="i > 0" :margin="12" />
      <div class="ap-card">
        <div class="ap-head">
          <span class="ap-index">反例 {{ i + 1 }}</span>
          <a-button size="mini" type="text" status="danger" :disabled="disabled" @click="removeItem(i)">删除</a-button>
        </div>
        <div class="ap-field">
          <div class="ap-label">场景描述</div>
          <a-textarea
            v-model="item.scenario"
            :auto-size="{ minRows: 2, maxRows: 6 }"
            :disabled="disabled"
            placeholder="描述出现反例的场景"
            @input="emitUpdate"
          />
        </div>
        <div class="ap-field">
          <div class="ap-label">正确做法</div>
          <a-textarea
            v-model="item.correct_action"
            :auto-size="{ minRows: 2, maxRows: 6 }"
            :disabled="disabled"
            placeholder="描述正确的处理方式"
            @input="emitUpdate"
          />
        </div>
        <div class="ap-field">
          <div class="ap-label">来源</div>
          <a-input
            v-model="item.source"
            size="mini"
            :disabled="disabled"
            placeholder="来源（如：历史案例、规范文档）"
            @input="emitUpdate"
          />
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import type { PropType } from 'vue'
import type { SkillAntipattern } from '@/types/skill'

const props = defineProps({
  value: { type: Array as PropType<SkillAntipattern[]>, default: () => [] },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update'])

const localItems = ref<{ scenario: string; correct_action: string; source: string }[]>([])

watch(() => props.value, (v) => {
  localItems.value = (v || []).map(o => ({
    scenario: o.scenario || '',
    correct_action: o.correct_action || '',
    source: o.source || '',
  }))
}, { immediate: true, deep: true })

function emitUpdate() {
  emit('update', localItems.value.map(r => ({
    scenario: r.scenario,
    correct_action: r.correct_action,
    source: r.source,
  })))
}

function addItem() {
  localItems.value.push({ scenario: '', correct_action: '', source: '' })
  emitUpdate()
}

function removeItem(i: number) {
  localItems.value.splice(i, 1)
  emitUpdate()
}
</script>

<style scoped>
.block { padding: 4px 0; }
.block-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.block-title { font-size: 15px; font-weight: 700; color: var(--ai-ink-1); }
.block-empty { color: var(--ai-ink-4); font-size: 13px; padding: 20px 0; text-align: center; }

.ap-card { padding: 4px 0; }
.ap-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.ap-index { font-size: 13px; font-weight: 600; color: var(--ai-ink-2); }
.ap-field { margin-bottom: 8px; }
.ap-label { font-size: 11px; font-weight: 600; color: var(--ai-ink-4); margin-bottom: 4px; }
</style>
