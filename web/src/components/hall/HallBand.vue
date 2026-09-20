<template>
  <!--
    HallBand — 能力大厅首页的横向 band。

    使用场景：能力大厅首屏上方的"最近更新 / 热门推荐 / 部门推荐"三条横向带。
    即使传入的 items 为空，也会展示带骨架占位（"暂无 xx"），保证页面不空旷。

    Props:
      - title: 标题
      - subtitle: 副标题（可选）
      - icon: 左侧图标组件（可选）
      - tone: chip 的 tone（用于数量徽章）
      - items: 数据列表（任意对象，只要能传给 SkillCard 使用即可）
      - loading: 正在加载骨架
      - emptyText: 无数据时的占位文案
      - moreTo: 查看更多的路由 location（传给 router-link）

    触发事件：
      - itemClick(item): 点击某卡片时触发（由父组件决定跳转）
  -->
  <section
    class="hall-band"
    :aria-label="title"
    :class="[`hall-band-${tone}`]"
  >
    <header class="hall-band-header">
      <div class="hall-band-title-wrap">
        <component :is="icon" v-if="icon" class="hall-band-icon" />
        <h3 class="hall-band-title">{{ title }}</h3>
        <SfStatChip
          v-if="!loading && items.length"
          :value="items.length"
          :tone="tone"
          class="hall-band-count"
        />
        <p v-if="subtitle" class="hall-band-subtitle">{{ subtitle }}</p>
      </div>
      <router-link v-if="moreTo" :to="moreTo" class="hall-band-more">
        查看更多 <icon-right />
      </router-link>
    </header>

    <!-- 骨架占位：3 个淡色卡片，营造加载感 -->
    <div v-if="loading" class="hall-band-scroll hall-band-skeleton">
      <div v-for="i in 4" :key="i" class="hall-band-skel-card" />
    </div>

    <!-- 有数据：横向 4+ 列滚动 -->
    <div v-else-if="items.length" class="hall-band-scroll" role="list">
      <div
        v-for="s in items"
        :key="s.id"
        class="hall-band-cell"
        role="listitem"
      >
        <SkillCard variant="hall" :skill="s" @click="onClick(s)" />
      </div>
    </div>

    <!-- 空态：也要占屏，不让 band 塌成 0 高度 -->
    <div v-else class="hall-band-empty">
      <icon-empty class="hall-band-empty-icon" />
      <p class="hall-band-empty-text">{{ emptyText }}</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import type { Component } from 'vue'
import { IconRight, IconEmpty } from '@arco-design/web-vue/es/icon'
import type { RouteLocationRaw } from 'vue-router'
import type { Tone } from '@/utils/tone'
import SkillCard from '@/components/skills/SkillCard.vue'
import { SfStatChip } from '@/components/sf'

withDefaults(
  defineProps<{
    title: string
    subtitle?: string
    icon?: Component | null
    tone?: Tone
    items: any[]
    loading?: boolean
    emptyText?: string
    moreTo?: RouteLocationRaw | null
  }>(),
  {
    subtitle: '',
    icon: null,
    tone: 'info',
    loading: false,
    emptyText: '暂无数据',
    moreTo: null,
  },
)

const emit = defineEmits<{
  (e: 'item-click', item: any): void
}>()

function onClick(item: any) {
  emit('item-click', item)
}
</script>

<style scoped>
.hall-band {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px 16px;
  border-radius: var(--ai-radius-l);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  box-shadow: var(--ai-shadow-1);
}

/* 左边细色带，用于区分 band 主题 —— 走 tone 映射 */
.hall-band {
  position: relative;
  overflow: hidden;
}
.hall-band::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  bottom: 0;
  width: 3px;
  background: var(--sf-tone-fg, var(--ai-accent));
  opacity: 0.72;
}
.hall-band-info {
  --sf-tone-fg: var(--ai-info);
}
.hall-band-brand {
  --sf-tone-fg: var(--ai-accent);
}
.hall-band-success {
  --sf-tone-fg: var(--ai-ok);
}

.hall-band-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.hall-band-title-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
}

.hall-band-icon {
  font-size: 18px;
  color: var(--sf-tone-fg, var(--ai-accent));
}

.hall-band-title {
  font-size: 16px;
  font-weight: 700;
  margin: 0;
  color: var(--ai-ink-1);
  letter-spacing: 0.01em;
}

.hall-band-count {
  margin-left: 2px;
}

.hall-band-subtitle {
  font-size: 12px;
  color: var(--ai-ink-3);
  margin: 0 0 0 4px;
}

.hall-band-more {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: 13px;
  color: var(--ai-accent-ink);
  text-decoration: none;
  white-space: nowrap;
  flex-shrink: 0;
}
.hall-band-more:hover {
  text-decoration: underline;
}

/* 横向滚动容器：始终保持可滚动；
   每张卡片固定宽度，让 band 即使只有 1-2 张卡也"整齐" */
.hall-band-scroll {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: minmax(260px, 1fr);
  gap: 14px;
  overflow-x: auto;
  overflow-y: hidden;
  padding-bottom: 4px;
  scrollbar-width: thin;
}

.hall-band-scroll::-webkit-scrollbar {
  height: 6px;
}
.hall-band-scroll::-webkit-scrollbar-thumb {
  background: var(--ai-border-2);
  border-radius: 3px;
}
.hall-band-scroll::-webkit-scrollbar-track {
  background: transparent;
}

/* 大屏固定 4 列，单行可见（多余的数据溢出后用 "查看更多" 跳转全量列表）。
   小屏走默认 grid-auto-flow column + 横向滚动。 */
@media (min-width: 1280px) {
  .hall-band-scroll {
    grid-template-columns: repeat(4, minmax(0, 1fr));
    grid-auto-flow: row;
    grid-auto-columns: unset;
    overflow: hidden;
  }
  /* 隐藏第 5 张之后的卡片（用 n+5 选择器），只保留一行 4 张 */
  .hall-band-scroll > .hall-band-cell:nth-child(n + 5) {
    display: none;
  }
  /* 骨架屏同理：只展示一行 4 张 */
  .hall-band-scroll.hall-band-skeleton > .hall-band-skel-card:nth-child(n + 5) {
    display: none;
  }
}

.hall-band-cell {
  min-width: 0;
  display: flex;
}

/* 在 band 上下文里把 hall 卡片高度收紧（整体 min-height 下调到 180，避免大片空白） */
.hall-band-cell :deep(.skill-card.variant-hall) {
  width: 100%;
  min-height: 188px;
}
.hall-band-cell :deep(.skill-card.variant-hall .arco-card-body) {
  padding: 14px 16px;
}
/* profile 行在列表项里不展示（卡片本身已经瘦身） */
.hall-band-cell :deep(.skill-card.variant-hall .card-profile) {
  display: none;
}

/* 骨架屏：4 个淡卡片，让 band 即使没数据回来也不塌 */
.hall-band-skeleton {
  pointer-events: none;
}
.hall-band-skel-card {
  min-height: 180px;
  border-radius: 10px;
  background: var(--ai-surface-2);
  background-size: 200% 100%;
  animation: hall-band-skel 1.4s linear infinite;
}
@keyframes hall-band-skel {
  from { background-position: 200% 0; }
  to { background-position: -200% 0; }
}

/* 空态：也要占一定高度，保证视觉饱满 */
.hall-band-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-height: 140px;
  color: var(--ai-ink-3);
  /* webkit 4 边 1px dashed 全部渲染成 solid，用 4 个 background gradient 拼出虚线框 */
  background-color: var(--ai-surface-2);
  background-image:
    linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to bottom, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to bottom, var(--ai-border-2) 50%, transparent 0%);
  background-position: top, bottom, left, right;
  background-size: 6px 1px, 6px 1px, 1px 6px, 1px 6px;
  background-repeat: repeat-x, repeat-x, repeat-y, repeat-y;
  border-radius: 10px;
}
.hall-band-empty-icon {
  font-size: 28px;
  color: var(--ai-ink-4);
  opacity: 0.5;
}
.hall-band-empty-text {
  font-size: 13px;
  margin: 0;
}
</style>
