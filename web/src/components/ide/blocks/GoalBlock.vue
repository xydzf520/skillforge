<template>
  <div class="block">
    <div class="block-title">目标</div>
    <div class="block-desc">描述该 Skill 的目的和业务价值</div>
    <a-textarea
      :model-value="value"
      :auto-size="{ minRows: 3, maxRows: 12 }"
      :disabled="disabled"
      placeholder="例如：识别高流失风险的客户，通过预警机制帮助业务团队及时干预..."
      @input="emit('update', $event)"
    />
    <div class="block-footer">{{ (value || '').length }} 字</div>
  </div>
</template>

<script setup lang="ts">
defineProps({
  value: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update'])
</script>

<style scoped>
.block { padding: 4px 0; }
.block-title { font-size: 15px; font-weight: 700; color: var(--ai-ink-1); margin-bottom: 4px; }
.block-desc { font-size: 12px; color: var(--ai-ink-4); margin-bottom: 10px; }
.block-footer { font-size: 11px; color: var(--ai-ink-4); margin-top: 6px; text-align: right; }
</style>
