<template>
  <article
    class="cap-card"
    :class="{ 'cap-card-fav': favorite }"
    @click="$emit('click', cap)"
  >
    <button
      class="fav-btn"
      type="button"
      :title="favorite ? '取消收藏' : '收藏此能力'"
      :aria-label="favorite ? '取消收藏' : '收藏此能力'"
      :aria-pressed="favorite"
      @click.stop="$emit('toggle-fav', cap)"
      @keydown.enter.stop.prevent="$emit('toggle-fav', cap)"
      @keydown.space.stop.prevent="$emit('toggle-fav', cap)"
    >
      <SfShellIcon name="star" :class="favorite ? 'fav-on' : 'fav-off'" />
    </button>

    <div class="cap-top">
      <span
        class="cap-icon"
        :class="`cap-icon--${categoryTone}`"
        aria-hidden="true"
      >
        <SfShellIcon :name="categoryIcon" />
      </span>
      <div class="cap-title-block">
        <div class="cap-label">业务能力</div>
        <div class="cap-name" tabindex="0" title="能力分类 —— 按业务场景聚合 Skill">#{{ cap.category }}</div>
      </div>
    </div>

    <div class="cap-stats">
      <span class="stat-big">{{ cap.skill_count_active }}</span>
      <span class="stat-unit">活跃 / 共 {{ cap.skill_count_total }} 个 Skill</span>
    </div>

    <div v-if="cap.top_departments?.length" class="cap-departments">
      <span class="cap-section-label">主要来自</span>
      <button
        v-for="d in cap.top_departments"
        :key="d.department"
        type="button"
        class="cap-chip"
        @click.stop="$emit('go-team', d.department)"
      >{{ d.department }} <span class="cnt">×{{ d.count }}</span></button>
    </div>

    <div v-if="cap.data_sources?.length" class="cap-data">
      <SfShellIcon name="database" /> 依赖 {{ cap.data_sources.length }} 个数据源
    </div>

    <div
      v-if="cap.new_skills_7d || cap.last_run_at || cap.last_updated_at"
      class="cap-fresh"
    >
      <span v-if="cap.new_skills_7d" class="fresh-new">本周新增 {{ cap.new_skills_7d }} 个</span>
      <span v-if="cap.last_run_at" class="fresh-run">
        <SfShellIcon name="clock" /> 最近运行 {{ relativeTime(cap.last_run_at) }}
      </span>
      <span v-else-if="cap.last_updated_at" class="fresh-updated">
        <SfShellIcon name="edit" /> 最近更新 {{ relativeTime(cap.last_updated_at) }}
      </span>
    </div>

    <div v-if="cap.sample_skills?.length" class="cap-samples">
      <div v-for="s in cap.sample_skills" :key="s.id" class="sample-item">
        <span class="sample-name">{{ s.name || s.id }}</span>
        <span class="sample-meta">{{ s.department }} · {{ s.usage_count }}</span>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { relativeTime } from '@/utils/format'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

const props = defineProps<{ cap: any; favorite: boolean }>()
defineEmits<{
  (e: 'click', cap: any): void
  (e: 'toggle-fav', cap: any): void
  (e: 'go-team', department: string): void
}>()

const categoryIcon = computed(() => {
  const raw = String(props.cap?.category || props.cap?.domain || '').toLowerCase()
  if (raw.includes('数据') || raw.includes('分析') || raw.includes('data')) return 'database'
  if (raw.includes('客服') || raw.includes('用户') || raw.includes('customer')) return 'users'
  if (raw.includes('风控') || raw.includes('合规') || raw.includes('risk')) return 'shield'
  if (raw.includes('报表') || raw.includes('文档') || raw.includes('report')) return 'doc'
  if (raw.includes('流程') || raw.includes('工作流') || raw.includes('workflow')) return 'flow'
  return 'cube'
})

const categoryTone = computed<'accent' | 'info' | 'ok' | 'warn' | 'bad'>(() => {
  const raw = String(props.cap?.category || props.cap?.domain || '').toLowerCase()
  if (raw.includes('风控') || raw.includes('合规') || raw.includes('风险') || raw.includes('risk')) return 'bad'
  if (raw.includes('客服') || raw.includes('用户') || raw.includes('customer')) return 'ok'
  if (raw.includes('数据') || raw.includes('分析') || raw.includes('data')) return 'info'
  if (raw.includes('监控') || raw.includes('巡检') || raw.includes('成本')) return 'warn'
  return 'accent'
})
</script>

<style scoped>
.cap-card {
  min-height: 220px;
  padding: 14px;
  border-radius: var(--ai-radius);
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  transition: border-color 0.16s ease, box-shadow 0.16s ease, transform 0.16s ease;
  cursor: pointer;
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 10px;
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}
.cap-card:hover {
  border-color: var(--ai-border-2);
  box-shadow: var(--ai-shadow-2);
  transform: translateY(-1px);
}
.cap-card-fav {
  border-color: var(--ai-warn);
  box-shadow: var(--ai-shadow-1);
}
.fav-btn {
  position: absolute;
  top: 12px;
  right: 12px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  width: 28px;
  height: 28px;
  cursor: pointer;
  color: var(--ai-ink-3);
  opacity: 0;
  transition: opacity 0.12s, color 0.12s, transform 0.12s;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border-radius: 6px;
  z-index: 2;
}
.fav-btn:focus-visible {
  outline: 2px solid var(--ai-accent);
  outline-offset: 2px;
}
.cap-card:hover .fav-btn,
.fav-btn:focus-visible,
.fav-btn[aria-pressed="true"] {
  opacity: 1;
}
.fav-btn .fav-on {
  color: var(--ai-warn);
}
.fav-btn:hover .fav-off {
  color: var(--ai-warn);
  transform: scale(1.1);
}
.cap-top {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 0;
  padding-right: 32px;
}
.cap-icon {
  --hall-card-icon-fg: var(--ai-accent-ink);
  --hall-card-icon-bg: var(--ai-accent-soft);
  width: 26px;
  height: 26px;
  flex: 0 0 26px;
  border-radius: 999px;
  display: grid;
  place-items: center;
  font-size: 13px;
  background: var(--hall-card-icon-bg);
  color: var(--hall-card-icon-fg);
  border: 1px solid color-mix(in srgb, var(--hall-card-icon-fg), transparent 82%);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.42);
}
.cap-icon--info {
  --hall-card-icon-fg: var(--ai-info);
  --hall-card-icon-bg: var(--ai-info-soft);
}
.cap-icon--ok {
  --hall-card-icon-fg: var(--ai-ok);
  --hall-card-icon-bg: var(--ai-ok-soft);
}
.cap-icon--warn {
  --hall-card-icon-fg: var(--ai-warn);
  --hall-card-icon-bg: var(--ai-warn-soft);
}
.cap-icon--bad {
  --hall-card-icon-fg: var(--ai-bad);
  --hall-card-icon-bg: var(--ai-bad-soft);
}
.cap-icon svg,
.fav-btn svg,
.cap-data svg,
.cap-fresh svg {
  width: 14px;
  height: 14px;
}
.cap-title-block {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.cap-label,
.cap-section-label {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
}
.cap-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--ai-ink-1);
  outline: none;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cap-name:focus-visible {
  outline: 2px solid var(--ai-accent);
  outline-offset: 2px;
  border-radius: 3px;
}
.cap-stats {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 0;
}
.stat-big {
  font-family: var(--ai-font-mono);
  font-size: 28px;
  font-weight: 650;
  color: var(--ai-ink-1);
}
.stat-unit {
  font-size: 12px;
  color: var(--ai-ink-3);
}
.cap-departments {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.cap-departments .cnt {
  opacity: 0.6;
  margin-left: 2px;
}
.cap-chip {
  height: 22px;
  padding: 0 7px;
  border-radius: 999px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: 11px;
  font-weight: 500;
  font-family: var(--ai-font-sans);
  cursor: pointer;
  transition: transform 0.12s, border-color 0.12s, background 0.12s;
}
.cap-chip:hover {
  transform: translateY(-1px);
  border-color: var(--ai-border-2);
  background: var(--ai-surface-3);
}
.cap-data {
  font-size: 12px;
  color: var(--ai-accent);
  display: flex;
  align-items: center;
  gap: 4px;
}
.cap-fresh {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 11px;
  color: var(--ai-ink-3);
}
.cap-fresh .fresh-new {
  color: var(--ai-ok);
  font-weight: 600;
  background: var(--ai-ok-soft);
  padding: 1px 6px;
  border-radius: 9px;
}
.cap-fresh .fresh-run,
.cap-fresh .fresh-updated {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.cap-samples {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  background: var(--ai-surface-2);
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  margin-top: auto;
}
.sample-item {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
}
.sample-name {
  color: var(--ai-ink-1);
  font-weight: 500;
}
.sample-meta {
  color: var(--ai-ink-3);
}
</style>
