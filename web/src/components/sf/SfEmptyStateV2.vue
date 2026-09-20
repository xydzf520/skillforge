<template>
  <div class="sf-empty-state-v2">
    <div class="sf-empty-illust">
      <component :is="resolvedIcon" v-if="resolvedIcon" />
    </div>
    <div class="sf-empty-title">{{ title }}</div>
    <p v-if="description" class="sf-empty-desc">{{ description }}</p>
    <div v-if="$slots.action || actionText" class="sf-empty-action">
      <slot name="action">
        <a-button v-if="actionText" type="primary" @click="$emit('action')">{{ actionText }}</a-button>
      </slot>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * SfEmptyStateV2 — 完整版空状态（图标 + 标题 + 说明 + CTA）
 *
 * 解决前端 UX 审计（2026-04-24）G4 — 空状态无规范：
 *   - playbooks (09)：图标 + title + 说明 + CTA 大按钮（最完整，对标此组件）
 *   - admin-org (15)：图标 + "暂无数据"（太简）
 *   - dashboard "业务影响"：图标 + "暂无数据"（太简）
 *
 * 与已有 SfEmptyState（@/components/common）的区别：
 * - 老组件锁死 IconBook 图标，只有 title/description/hint/actionLabel
 * - 本组件允许自定义图标（字符串名称 或 component），props 对齐新规范
 *   （action → actionText，触发 @action emit）
 * - 先不替换老组件，避免影响其他 agent 正在迁移的页面
 *
 * Props:
 * - icon       — 可选图标组件（如 IconApps）或字符串名（暂仅支持组件）；不传则默认 IconFolder
 * - title      — 主标题（必填，如 "请选择左侧组织"）
 * - description— 副说明（可选，讲清"为什么空"+"下一步"）
 * - actionText — CTA 文案（传了就渲染主按钮，点击 emit('action')）
 *
 * Slots:
 * - action — 自定义 CTA（多按钮 / 自定义样式）
 *
 * Emits:
 * - action — 主 CTA 点击（仅 actionText 模式触发；slot 模式由调用方自己处理）
 */
import { computed, type Component } from 'vue'
import { IconFolder } from '@arco-design/web-vue/es/icon'

const props = withDefaults(
  defineProps<{
    icon?: string | Component | null
    title: string
    description?: string
    actionText?: string
  }>(),
  {
    icon: null,
    description: '',
    actionText: '',
  },
)

defineEmits<{ (e: 'action'): void }>()

// 字符串名目前不做动态解析（避免拉 icon 全量 import 包膨胀）；
// 只接受直接传 component 引用。字符串参数保留向后扩展空间。
const resolvedIcon = computed<Component | null>(() => {
  if (!props.icon) return IconFolder
  if (typeof props.icon === 'string') return IconFolder
  return props.icon
})
</script>

<style scoped>
.sf-empty-state-v2 {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: var(--sf-spacing-xxl, 40px) 16px;
  gap: 6px;
}

.sf-empty-illust {
  font-size: 48px;
  color: var(--ai-ink-4);
  opacity: 0.72;
  margin-bottom: 6px;
  line-height: 1;
}

.sf-empty-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--ai-ink-1);
  letter-spacing: -0.01em;
  line-height: 1.4;
}

.sf-empty-desc {
  margin: 0;
  max-width: 420px;
  font-size: 13px;
  color: var(--ai-ink-3);
  line-height: 1.6;
  font-weight: 500;
}

.sf-empty-action {
  margin-top: 14px;
}
</style>
