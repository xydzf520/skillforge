<template>
  <div class="skill-not-found">
    <a-result status="warning" :title="title" :subtitle="subtitle">
      <template #extra>
        <a-space>
          <a-button type="primary" @click="$router.push('/skills/list')">返回 Skill 列表</a-button>
          <a-button @click="$router.push('/skills/new')">新建 Skill</a-button>
        </a-space>
      </template>
    </a-result>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    skillId?: string
    title?: string
    subtitle?: string
  }>(),
  {
    skillId: '',
    title: 'Skill 不存在或已删除',
    subtitle: '',
  },
)

const subtitle = computed(() => {
  if (props.subtitle) return props.subtitle
  return props.skillId
    ? `找不到 ID 为「${props.skillId}」的 Skill。可能已被删除，或链接有误。`
    : '请检查链接是否正确。'
})
</script>

<style scoped>
.skill-not-found {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
  background: var(--ai-surface);
}
</style>
