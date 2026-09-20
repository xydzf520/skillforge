<template>
  <span class="sf-brand" :class="`sf-brand--${size}`">
    <img class="sf-brand__mark" src="/brand/mark.svg" :alt="iconOnly ? 'SkillForge' : ''" width="32" height="32" />
    <span v-if="!iconOnly" class="sf-brand__name">SkillForge</span>
  </span>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ size?: 'sm' | 'md' | 'lg'; iconOnly?: boolean }>(), {
  size: 'md',
  iconOnly: false,
})
</script>

<style scoped>
.sf-brand { display: inline-flex; align-items: center; gap: 10px; flex: 0 0 auto; vertical-align: middle; }
.sf-brand__mark { display: block; width: 32px; height: 32px; flex: 0 0 auto; }
.sf-brand__name { color: var(--color-text-1, inherit); font-family: var(--ai-font-sans, system-ui, sans-serif); font-size: 19px; font-weight: 650; letter-spacing: -0.04em; line-height: 1.2; }
.sf-brand--sm { gap: 8px; }
.sf-brand--sm .sf-brand__mark { width: 28px; height: 28px; }
.sf-brand--sm .sf-brand__name { font-size: 15px; }
.sf-brand--lg .sf-brand__mark { width: 56px; height: 56px; }
.sf-brand--lg .sf-brand__name { font-size: 28px; }
@media (max-width: 768px) { .sf-brand--sm .sf-brand__name { display: none; } }
</style>
