<!--
  @deprecated 已替换为各页面内联 .page-header > .page-kicker / .page-title / .page-subtitle 模式。
  见 web/src/pages/skill/SkillList.vue 等参考。
  保留文件本身不删除以免破坏可能的旧引用，但不再 export。
-->
<template>
  <header class="sf-page-header" :class="{ 'has-kicker': !!kicker, 'has-subtitle': !!subtitle }">
    <div class="sf-page-heading">
      <span v-if="kicker" class="sf-page-kicker">{{ kicker }}</span>
      <h1 class="sf-page-title">{{ title }}</h1>
      <p v-if="subtitle" class="sf-page-subtitle">{{ subtitle }}</p>
      <div v-if="$slots.extra" class="sf-page-extra">
        <slot name="extra" />
      </div>
    </div>
    <div v-if="$slots.actions" class="sf-page-actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup lang="ts">
/**
 * SfPageHeader — 统一页面头部
 *
 * 解决前端 UX 审计（2026-04-24）G1 — 页面头 4 种结构并存：
 *   A（完整）：[kicker][title][subtitle] ... [actions]   hall / inbox / playbooks / portal
 *   B（简化）：[title] ........................ [actions]  skills / reviews / executions / dashboard
 *   C（密集）：[title][timestamps][status-bar] ... [actions]  tasktree
 *
 * 本组件合并 A/B 为一种：subtitle、kicker 都可选，结构保持稳定。
 * C 型（tasktree）保留原实现；D 型（IDE/Chat 全屏）不使用此组件。
 *
 * Props:
 * - kicker   — 标题上方的小 eyebrow 文字（如 "SKILL 市场"），可选
 * - title    — 主标题（必填）
 * - subtitle — 标题下方的副标题（如 "每日观察一键推送 10 条建议"），可选
 *
 * Slots:
 * - actions — 右侧按钮区（<a-button /> 等），通常与 <a-space /> 组合
 * - extra   — subtitle 下方的自定义区域（KPI / status chips 等）
 *
 * 用法：
 *   <SfPageHeader title="执行监控">
 *     <template #actions>
 *       <a-button>导出</a-button>
 *     </template>
 *   </SfPageHeader>
 *
 *   <SfPageHeader kicker="审核中心" title="待审核列表" subtitle="处理 Skill 变更发布">
 *     <template #actions>...</template>
 *   </SfPageHeader>
 */
defineProps<{
  kicker?: string
  title: string
  subtitle?: string
}>()
</script>

<style scoped>
.sf-page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.sf-page-heading {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  flex: 1 1 auto;
}

.sf-page-kicker {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
  color: var(--ai-ink-3);
  text-transform: uppercase;
  line-height: 1;
}

.sf-page-title {
  font-size: var(--sf-text-h1);
  font-weight: 800;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
  line-height: 1.2;
}

.sf-page-subtitle {
  margin: 2px 0 0;
  color: var(--ai-ink-2);
  font-weight: 600;
  font-size: 13px;
  line-height: 1.5;
}

.sf-page-extra {
  margin-top: 10px;
  min-width: 0;
}

.sf-page-actions {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

@media (max-width: 640px) {
  .sf-page-header {
    flex-wrap: wrap;
    gap: 12px;
  }
  .sf-page-title {
    font-size: 18px;
  }
}
</style>
