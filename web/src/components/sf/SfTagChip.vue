<template>
  <span
    class="sf-tag-chip"
    :class="[toneCls, `sf-tag-chip-${size}`, { 'is-outline': outline }]"
    :data-tone="tone"
  >
    <span v-if="$slots.icon" class="sf-tag-chip-icon">
      <slot name="icon" />
    </span>
    <span class="sf-tag-chip-label">
      <slot>{{ label }}</slot>
    </span>
  </span>
</template>

<script setup lang="ts">
/**
 * SfTagChip — 语义化 tag chip（替代 a-tag）
 *
 * 解决前端 UX 审计（2026-04-24）V3 — admin-users 角色徽章颜色失控：
 *   同语义不同色 / 不同语义同色。原因是调用方直接给 a-tag 传硬编码颜色。
 *
 * 本组件强制走 `Tone` 语义（见 utils/tone.ts），配合 `.sf-tone-*` CSS 变量
 * （见 styles/tone.css）统一着色。外观近似 a-tag，但颜色不可自由指定。
 *
 * Props:
 * - label   — chip 文案（也可用默认 slot 覆盖）
 * - tone    — 语义 tone（danger/warning/success/info/brand/neutral）
 * - size    — 'sm' | 'md'（默认 md，与 a-tag size="small" 大致一致）
 * - outline — 描边样式（默认实心浅底）
 *
 * Slots:
 * - default — 自定义内容（传了则 label prop 忽略）
 * - icon    — 左侧图标
 *
 * 典型：
 *   <SfTagChip :label="roleLabel[u.role]" :tone="roleTone(u.role)" />
 *   <SfTagChip label="失败" tone="danger" size="sm" outline />
 */
import { computed } from 'vue'
import type { Tone } from '@/utils/tone'
import { toneClass as toneClassFn } from '@/utils/tone'

const props = withDefaults(
  defineProps<{
    label?: string
    tone?: Tone
    size?: 'sm' | 'md'
    outline?: boolean
  }>(),
  {
    label: '',
    tone: 'neutral',
    size: 'md',
    outline: false,
  },
)

const toneCls = computed(() => toneClassFn(props.tone))
</script>

<style scoped>
.sf-tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border-radius: 4px;
  font-weight: 600;
  line-height: 1.4;
  white-space: nowrap;
  background: var(--sf-tone-bg);
  color: var(--sf-tone-fg);
  border: 1px solid transparent;
  transition: background-color 0.15s ease, color 0.15s ease;
}

/* neutral 时补一层浅灰底，避免 tone.css 的 bg 在白底看不出来 */
.sf-tag-chip[data-tone='neutral'] {
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
}

/* size 变体 */
.sf-tag-chip-sm {
  padding: 1px 8px;
  font-size: 11px;
}

.sf-tag-chip-md {
  padding: 2px 10px;
  font-size: 12px;
}

/* 描边样式：透明底 + tone 边框 + tone 文本 */
.sf-tag-chip.is-outline {
  background: transparent;
  border-color: var(--sf-tone-border);
}

.sf-tag-chip.is-outline[data-tone='neutral'] {
  background: transparent;
  border-color: var(--ai-border-2);
}

.sf-tag-chip-icon {
  display: inline-flex;
  align-items: center;
  line-height: 0;
  font-size: 12px;
}

.sf-tag-chip-label {
  color: inherit;
}
</style>
