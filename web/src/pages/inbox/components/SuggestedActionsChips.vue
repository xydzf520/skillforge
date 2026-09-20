<template>
  <ul v-if="visibleActions.length" class="sf-suggest-chips" data-testid="suggested-actions">
    <li
      v-for="(action, idx) in visibleActions"
      :key="idx"
      class="sf-suggest-chip"
      :title="action"
    >
      <span class="sf-suggest-chip-icon">💡</span>
      <span class="sf-suggest-chip-text">{{ action }}</span>
    </li>
  </ul>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'

const props = defineProps({
  actions: { type: Array as PropType<string[] | null | undefined>, default: () => [] },
  max: { type: Number, default: 3 },
})

const visibleActions = computed(() => (props.actions || []).filter(Boolean).slice(0, props.max))
</script>

<style scoped>
.sf-suggest-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.sf-suggest-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(var(--orange-6), 0.12);
  border: 1px solid rgba(var(--orange-6), 0.3);
  color: #8b6914;
  font-size: 12px;
  font-weight: 600;
  max-width: 260px;
}

.sf-suggest-chip-icon {
  flex-shrink: 0;
  font-size: 12px;
  line-height: 1;
}

.sf-suggest-chip-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
