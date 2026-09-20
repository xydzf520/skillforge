<template>
  <div class="block">
    <div class="block-header">
      <div class="block-title">自定义章节</div>
      <a-button size="mini" type="outline" :disabled="disabled" @click="addSection">+ 添加章节</a-button>
    </div>

    <div v-if="!sectionEntries.length" class="block-empty">暂无自定义章节</div>

    <template v-for="(entry, i) in sectionEntries" :key="i">
      <a-divider v-if="i > 0" :margin="12" />
      <div class="cs-card">
        <div class="cs-head">
          <a-input
            v-model="entry.title"
            size="mini"
            :disabled="disabled"
            placeholder="章节标题"
            class="cs-title-input"
            @input="emitUpdate"
          />
          <a-button size="mini" type="text" status="danger" :disabled="disabled" @click="removeSection(i)">删除</a-button>
        </div>
        <a-textarea
          v-model="entry.content"
          :auto-size="{ minRows: 3, maxRows: 12 }"
          :disabled="disabled"
          placeholder="章节内容..."
          @input="emitUpdate"
        />
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import type { PropType } from 'vue'

const props = defineProps({
  value: { type: Object as PropType<Record<string, string>>, default: () => ({}) },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update'])

const sectionEntries = ref<{ title: string; content: string }[]>([])

watch(() => props.value, (v) => {
  const raw = v || {}
  sectionEntries.value = Object.entries(raw)
    .filter(([key]) => !key.startsWith('__'))
    .map(([title, content]) => ({ title, content: String(content) }))
}, { immediate: true, deep: true })

function emitUpdate() {
  const result: Record<string, string> = {}
  // 保留内部元数据字段（以 __ 开头的）
  const raw = props.value || {}
  for (const key of Object.keys(raw)) {
    if (key.startsWith('__')) {
      result[key] = raw[key] as string
    }
  }
  // 合并用户编辑的章节
  for (const entry of sectionEntries.value) {
    const title = entry.title.trim()
    if (title) {
      result[title] = entry.content
    }
  }
  emit('update', result)
}

function addSection() {
  sectionEntries.value.push({ title: '', content: '' })
}

function removeSection(i: number) {
  sectionEntries.value.splice(i, 1)
  emitUpdate()
}
</script>

<style scoped>
.block { padding: 4px 0; }
.block-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.block-title { font-size: 15px; font-weight: 700; color: var(--ai-ink-1); }
.block-empty { color: var(--ai-ink-4); font-size: 13px; padding: 20px 0; text-align: center; }

.cs-card { padding: 4px 0; }
.cs-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.cs-title-input { flex: 1; font-weight: 600; }
</style>
