<template>
  <div class="page-container changelog-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">平台 · 更新日志</div>
        <h2 class="page-title">更新日志</h2>
        <p class="page-subtitle">按版本查看变化，重点版本会补充图文导览和入口。</p>
      </div>
      <a-space>
        <a-input-search
          v-model="searchKey"
          placeholder="按版本号或关键词搜索"
          allow-clear
          style="width: 260px"
        />
      </a-space>
    </div>

    <section v-if="featuredSpotlight && !searchKey" class="release-spotlight">
      <div class="spotlight-copy">
        <div class="spotlight-kicker">
          <span>重点导览</span>
          <span>v{{ featuredSpotlight.entry.version }} · {{ featuredSpotlight.entry.date }}</span>
        </div>
        <h3>{{ featuredSpotlight.title }}</h3>
        <p>{{ featuredSpotlight.summary }}</p>
        <div v-if="featuredSpotlight.steps?.length" class="spotlight-steps">
          <span v-for="step in featuredSpotlight.steps" :key="step">{{ step }}</span>
        </div>
        <div class="spotlight-actions">
          <a-button v-if="featuredSpotlight.primaryAction" type="primary" class="ai-btn-like primary" @click="openSpotlightAction(featuredSpotlight.primaryAction)">
            <template #icon><component :is="iconFor(featuredSpotlight.primaryAction.icon || 'image')" /></template>
            {{ featuredSpotlight.primaryAction.label || '去体验' }}
          </a-button>
          <a-button class="ai-btn-like" @click="scrollToVersion(featuredSpotlight.entry.version)">
            <template #icon><icon-arrow-right /></template>
            {{ featuredSpotlight.secondaryAction?.label || '看完整日志' }}
          </a-button>
        </div>
      </div>

      <div v-if="featuredSpotlight.visual" class="spotlight-visual" role="img" :aria-label="featuredSpotlight.visual.title || featuredSpotlight.title">
        <div class="visual-caption">{{ featuredSpotlight.visual.title }}</div>
        <div v-if="featuredSpotlight.visual.flow?.length" class="visual-flow">
          <template v-for="(flow, idx) in featuredSpotlight.visual.flow" :key="`${flow.num || idx}-${flow.title}`">
            <div class="flow-step">
              <span class="flow-num">{{ flow.num || idx + 1 }}</span>
              <div>
                <b>{{ flow.title }}</b>
                <em>{{ flow.desc }}</em>
              </div>
            </div>
            <div v-if="idx < featuredSpotlight.visual.flow.length - 1" class="flow-arrow">→</div>
          </template>
        </div>
        <div v-if="featuredSpotlight.visual.tabs?.length || featuredSpotlight.visual.panels?.length" class="visual-screen">
          <div class="screen-tabs">
            <span
              v-for="(tab, idx) in featuredSpotlight.visual.tabs || []"
              :key="tab"
              class="screen-tab"
              :class="{ active: idx === 0 }"
            >
              {{ tab }}
            </span>
          </div>
          <div class="screen-main">
            <div v-for="panel in featuredSpotlight.visual.panels || []" :key="panel.label" class="screen-box">
              <div class="screen-label">{{ panel.label }}</div>

              <template v-if="panel.kind === 'prompt'">
                <div class="prompt-line wide"></div>
                <div class="prompt-line"></div>
                <div v-if="panel.chips?.length" class="param-row">
                  <span v-for="chip in panel.chips" :key="chip">{{ chip }}</span>
                </div>
              </template>

              <template v-else-if="panel.kind === 'queue'">
                <div v-for="item in panel.items || []" :key="item.label" class="queue-item" :class="item.tone || 'running'">
                  <span></span>{{ item.label }}
                </div>
              </template>

              <template v-else-if="panel.kind === 'result'">
                <div class="image-tile primary"></div>
                <div v-if="panel.actions?.length" class="result-actions">
                  <span v-for="action in panel.actions" :key="action">{{ action }}</span>
                </div>
              </template>

              <div v-else class="screen-text">
                {{ panel.text || '' }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <div v-if="featuredSpotlight?.guides?.length && !searchKey" class="guide-strip">
      <div v-for="guide in featuredSpotlight.guides" :key="guide.title" class="guide-item">
        <component :is="iconFor(guide.icon)" class="guide-icon" />
        <div>
          <div class="guide-title">{{ guide.title }}</div>
          <div class="guide-desc">{{ guide.desc }}</div>
        </div>
      </div>
    </div>

    <div class="changelog-layout">
      <!-- 左侧版本锚点导航 -->
      <aside v-if="filteredVersions.length" class="changelog-toc">
        <div class="toc-title">版本索引</div>
        <a
          v-for="entry in filteredVersions"
          :key="entry.version"
          :href="`#v${entry.version}`"
          class="toc-link"
          :class="{ active: activeVersion === entry.version }"
          @click.prevent="scrollToVersion(entry.version)"
        >
          <span class="toc-version">v{{ entry.version }}</span>
          <span class="toc-date">{{ entry.date }}</span>
        </a>
      </aside>

      <a-card class="page-list-card changelog-body">
        <a-spin :loading="loading" style="width: 100%">
          <div class="changelog-timeline">
            <div v-for="(entry, i) in filteredVersions" :key="entry.version" :id="`v${entry.version}`" class="timeline-entry">
              <div class="timeline-marker">
                <div class="marker-dot" :class="{ latest: i === 0 && !searchKey }"></div>
                <div v-if="i < filteredVersions.length - 1" class="marker-line"></div>
              </div>
              <div class="timeline-card">
                <div class="card-header">
                  <span class="card-version">v{{ entry.version }}</span>
                  <span v-if="i === 0 && !searchKey" class="card-latest">最新</span>
                  <span class="card-date">{{ entry.date }}</span>
                </div>
                <div v-if="getReleaseStories(entry).length" class="story-grid">
                  <article v-for="story in getReleaseStories(entry)" :key="story.title" class="story-card">
                    <div class="story-head">
                        <component :is="iconFor(story.icon)" class="story-icon" />
                      <span>{{ story.tag }}</span>
                    </div>
                    <h4>{{ story.title }}</h4>
                    <p>{{ story.desc }}</p>
                  </article>
                </div>
                <template v-for="(section, sectionIndex) in displaySections(entry)" :key="`${entry.version}-${sectionIndex}-${section.title}`">
                  <div v-if="section.items?.length" class="card-section">
                    <div class="card-tag" :class="'tag-' + categoryFor(section.category)">
                      <component :is="sectionIcons[categoryFor(section.category)]" style="width: 12px; height: 12px" />
                      {{ section.title || sectionLabels[categoryFor(section.category)] }}
                    </div>
                    <ul>
                      <li v-for="(item, j) in section.items" :key="j">{{ item }}</li>
                    </ul>
                  </div>
                </template>
              </div>
            </div>
          </div>
          <a-empty v-if="!loading && !filteredVersions.length" :description="searchKey ? '没有匹配的版本' : '暂无日志'" />
        </a-spin>
      </a-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { changelogApi as rawChangelogApi } from '@/api'
import {
  IconApps,
  IconArrowRight,
  IconBug,
  IconBulb,
  IconCheckCircle,
  IconCopy,
  IconImage,
  IconPlayArrow,
  IconRefresh,
  IconStarFill,
} from '@arco-design/web-vue/es/icon'

const changelogApi: any = rawChangelogApi
const router = useRouter()
const loading = ref(false)
const versions = ref<any[]>([])
const searchKey = ref('')
const activeVersion = ref('')
const sectionIcons: Record<string, any> = { highlights: IconStarFill, features: IconBulb, improvements: IconCheckCircle, fixes: IconBug }
const sectionLabels: Record<string, string> = { highlights: '亮点', features: '新功能', improvements: '优化', fixes: '修复' }

type SpotlightAction = {
  label?: string
  to?: string
  href?: string
  icon?: string
}

type SpotlightCard = {
  icon?: string
  tag?: string
  title: string
  desc: string
}

type SpotlightPanel = {
  kind?: 'prompt' | 'queue' | 'result' | string
  label: string
  chips?: string[]
  items?: Array<{ tone?: string; label: string }>
  actions?: string[]
  text?: string
}

type SpotlightVisual = {
  title?: string
  flow?: Array<{ num?: string; title: string; desc: string }>
  tabs?: string[]
  panels?: SpotlightPanel[]
}

type ChangelogSection = {
  title?: string
  category?: string
  items?: string[]
}

type ChangelogSpotlight = {
  title: string
  summary: string
  primaryAction?: SpotlightAction
  secondaryAction?: SpotlightAction
  steps?: string[]
  visual?: SpotlightVisual
  guides?: SpotlightCard[]
  stories?: SpotlightCard[]
}

type ChangelogEntry = {
  version: string
  date: string
  spotlight?: ChangelogSpotlight
  highlights?: string[]
  features?: string[]
  improvements?: string[]
  fixes?: string[]
  sections?: ChangelogSection[]
}

const iconMap: Record<string, any> = {
  apps: IconApps,
  bug: IconBug,
  bulb: IconBulb,
  check: IconCheckCircle,
  copy: IconCopy,
  image: IconImage,
  play: IconPlayArrow,
  refresh: IconRefresh,
  star: IconStarFill,
}

const filteredVersions = computed(() => {
  const k = searchKey.value.trim().toLowerCase()
  if (!k) return versions.value
  return versions.value.filter((v: ChangelogEntry) => {
    if (String(v.version || '').toLowerCase().includes(k)) return true
    return searchableItems(v).some((item) => String(item).toLowerCase().includes(k))
  })
})

const featuredSpotlight = computed(() => {
  const entry = versions.value.find((v: ChangelogEntry) => v.spotlight) as ChangelogEntry | undefined
  const spotlight = entry?.spotlight
  if (!entry || !spotlight) return null
  return {
    entry,
    ...spotlight,
  }
})

function iconFor(name?: string) {
  return iconMap[name || ''] || IconBulb
}

function categoryFor(category?: string) {
  return ['highlights', 'features', 'improvements', 'fixes'].includes(category || '')
    ? category as keyof typeof sectionIcons
    : 'improvements'
}

function fallbackSections(entry: ChangelogEntry) {
  return (['features', 'improvements', 'fixes'] as const)
    .map((category) => ({
      title: sectionLabels[category],
      category,
      items: entry[category] || [],
    }))
    .filter((section) => section.items.length)
}

function displaySections(entry: ChangelogEntry) {
  const sections: ChangelogSection[] = []
  if (entry.highlights?.length) {
    sections.push({ title: sectionLabels.highlights, category: 'highlights', items: entry.highlights })
  }
  const rawSections = entry.sections?.length ? entry.sections : fallbackSections(entry)
  return sections.concat(rawSections.filter((section) => section.items?.length))
}

function searchableItems(entry: ChangelogEntry) {
  const spotlight = entry.spotlight
  return [
    ...(displaySections(entry).flatMap((section) => [section.title || '', ...(section.items || [])])),
    spotlight?.title || '',
    spotlight?.summary || '',
    ...((spotlight?.stories || []).flatMap((story) => [story.tag || '', story.title, story.desc])),
    ...((spotlight?.guides || []).flatMap((guide) => [guide.title, guide.desc])),
  ].filter(Boolean)
}

function getReleaseStories(entry: ChangelogEntry) {
  return entry?.spotlight?.stories || []
}

function openSpotlightAction(action?: SpotlightAction) {
  if (!action) return
  if (action.to) {
    router.push(action.to)
    return
  }
  if (action.href) {
    window.open(action.href, '_blank', 'noopener,noreferrer')
  }
}

function scrollToVersion(version: string) {
  activeVersion.value = version
  const el = document.getElementById(`v${version}`)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

onMounted(async () => {
  loading.value = true
  try {
    const res = await changelogApi.get(200)
    versions.value = res.versions || []
    if (versions.value.length) activeVersion.value = versions.value[0].version
  } catch { /* ignore */ }
  finally { loading.value = false }
})
</script>

<style scoped>
/* 设计稿 page chrome 覆盖 —— 与 SkillList / AdminUsers 同模式 */
.changelog-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.changelog-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
}
.changelog-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.changelog-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

/* 顶部按钮统一外形 —— 30px / 6px 圆角 / 12.5px 字号 */
.changelog-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary)) {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
  box-shadow: none;
}
.changelog-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.changelog-page :deep(.arco-btn-primary.ai-btn-like) {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
  font-size: 12.5px;
  font-weight: 500;
  box-shadow: none;
}
.changelog-page :deep(.arco-btn-primary.ai-btn-like:hover) {
  background: #000;
  border-color: #000;
}

/* 顶部搜索框 —— 30px 高 / 6px 圆角 */
.changelog-page :deep(.arco-input-wrapper) {
  height: 30px;
  border-radius: 6px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}
.changelog-page :deep(.arco-input-wrapper:hover),
.changelog-page :deep(.arco-input-wrapper.arco-input-focus) {
  border-color: var(--ai-border-2);
  background: var(--ai-surface);
}
.changelog-page :deep(.arco-input-wrapper .arco-input) {
  font-size: 12.5px;
  color: var(--ai-ink-1);
}

/* a-tag → ai-pill: 20px / 4px / 11px / 500 */
.changelog-page :deep(.arco-tag.arco-tag-size-small),
.changelog-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
}
.changelog-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.changelog-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.changelog-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.changelog-page :deep(.arco-tag-color-orange),
.changelog-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.changelog-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-2) !important;
  border-color: transparent !important;
}

/* ─────────── Release spotlight（重点导览） ─────────── */
.release-spotlight {
  display: grid;
  grid-template-columns: minmax(260px, 0.9fr) minmax(360px, 1.1fr);
  gap: 24px;
  align-items: stretch;
  margin-bottom: 16px;
  padding: 22px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  transition: border-color 0.15s ease;
}
.release-spotlight:hover {
  border-color: var(--ai-border-2);
}
.spotlight-copy {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
}
.spotlight-kicker {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
  font-size: 11px;
  font-weight: 500;
}
.spotlight-kicker span {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 7px;
  color: var(--ai-ink-2);
  background: var(--ai-surface-2);
  border-radius: 4px;
  font-family: var(--ai-font-mono);
  letter-spacing: 0;
}
.spotlight-kicker span:first-child {
  color: var(--ai-ink-1);
  background: var(--ai-surface-3);
  font-family: var(--ai-font-sans);
}
.spotlight-copy h3 {
  margin: 0;
  color: var(--ai-ink-1);
  font-size: 22px;
  line-height: 1.25;
  font-weight: 600;
  letter-spacing: -0.02em;
}
.spotlight-copy p {
  margin: 10px 0 0;
  color: var(--ai-ink-3);
  font-size: 13px;
  line-height: 1.6;
  font-weight: 400;
}
.spotlight-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 14px;
}
.spotlight-steps span {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  color: var(--ai-ink-2);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  font-size: 11.5px;
  font-weight: 500;
}
.spotlight-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
}
.spotlight-visual {
  min-height: 300px;
  padding: 14px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
}
.visual-caption {
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
}
.visual-flow {
  display: grid;
  grid-template-columns: 1fr auto 1fr auto 1fr auto 1fr;
  gap: 8px;
  align-items: stretch;
  margin-top: 10px;
}
.flow-step {
  display: flex;
  gap: 8px;
  min-width: 0;
  padding: 10px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
}
.flow-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  color: var(--ai-surface);
  background: var(--ai-ink-1);
  border-radius: 50%;
  font-size: 11px;
  font-weight: 600;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.flow-step b {
  display: block;
  color: var(--ai-ink-1);
  font-size: 12px;
  line-height: 1.35;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.flow-step em {
  display: block;
  margin-top: 3px;
  color: var(--ai-ink-4);
  font-size: 11px;
  line-height: 1.4;
  font-style: normal;
  font-weight: 400;
  overflow-wrap: anywhere;
}
.flow-arrow {
  display: flex;
  align-items: center;
  color: var(--ai-ink-4);
  font-size: 14px;
  font-weight: 500;
}
.visual-screen {
  margin-top: 12px;
  padding: 12px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
}
.screen-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}
.screen-tab {
  padding: 4px 8px;
  color: var(--ai-ink-3);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  font-size: 11.5px;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.screen-tab.active {
  color: var(--ai-surface);
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
}
.screen-main {
  display: grid;
  grid-template-columns: 0.9fr 0.9fr 1.1fr;
  gap: 10px;
}
.screen-box {
  min-width: 0;
  padding: 12px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
}
.screen-label {
  margin-bottom: 10px;
  color: var(--ai-ink-3);
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.screen-text {
  color: var(--ai-ink-2);
  font-size: 12px;
  line-height: 1.6;
  font-weight: 400;
  overflow-wrap: anywhere;
}
.param-row,
.queue-item {
  display: flex;
  align-items: center;
}
.prompt-line {
  width: 78%;
  height: 8px;
  margin-bottom: 8px;
  background: var(--ai-border);
  border-radius: 4px;
}
.prompt-line.wide { width: 100%; }
.prompt-line.short { width: 54%; }
.param-row {
  justify-content: space-between;
  gap: 6px;
  margin-top: 14px;
}
.param-row span {
  padding: 4px 7px;
  color: var(--ai-ink-2);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  font-family: var(--ai-font-mono);
}
.image-tile {
  min-height: 120px;
  border-radius: var(--ai-radius-s);
  background:
    linear-gradient(135deg, var(--ai-surface-3), var(--ai-surface-2)),
    repeating-linear-gradient(45deg, rgba(255,255,255,0.5) 0 8px, transparent 8px 16px);
}
.image-tile.primary {
  background:
    linear-gradient(145deg, var(--ai-ink-5), var(--ai-surface-3)),
    repeating-linear-gradient(135deg, rgba(255,255,255,0.6) 0 10px, transparent 10px 20px);
}
.queue-item {
  justify-content: center;
  gap: 6px;
  min-width: 0;
  height: 30px;
  margin-bottom: 8px;
  color: var(--ai-ink-2);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  font-size: 11.5px;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.queue-item:last-child { margin-bottom: 0; }
.queue-item span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.queue-item.running span { background: var(--ai-info); }
.queue-item.done span { background: var(--ai-ok); }
.queue-item.retry span { background: var(--ai-warn); }
.result-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}
.result-actions span {
  padding: 4px 7px;
  color: var(--ai-ink-2);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
}

/* ─────────── Guide strip ─────────── */
.guide-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.guide-item {
  display: flex;
  gap: 10px;
  min-width: 0;
  padding: 14px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  transition: border-color 0.15s ease;
}
.guide-item:hover {
  border-color: var(--ai-border-2);
}
.guide-icon {
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  margin-top: 2px;
  color: var(--ai-ink-3);
}
.guide-title {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 500;
  letter-spacing: -0.005em;
  overflow-wrap: anywhere;
}
.guide-desc {
  margin-top: 4px;
  color: var(--ai-ink-4);
  font-size: 12px;
  line-height: 1.55;
  font-weight: 400;
  overflow-wrap: anywhere;
}

/* ─────────── Timeline layout ─────────── */
.changelog-layout {
  display: grid;
  grid-template-columns: 180px 1fr;
  gap: 20px;
  align-items: start;
}
.changelog-toc {
  position: sticky;
  top: 12px;
  max-height: calc(100vh - 140px);
  overflow-y: auto;
  padding: 8px 0;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
}
.toc-title {
  font-size: 11px;
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 6px 14px 8px;
  border-bottom: 1px solid var(--ai-border);
}
.toc-link {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 14px;
  font-size: 12px;
  color: var(--ai-ink-2);
  text-decoration: none;
  border-left: 2px solid transparent;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}
.toc-link:hover { background: var(--ai-surface-2); color: var(--ai-ink-1); }
.toc-link.active {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
  border-left-color: var(--ai-ink-1);
}
.toc-version {
  font-weight: 500;
  font-family: var(--ai-font-mono);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0;
}
.toc-date {
  font-size: 11px;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
@media (max-width: 768px) {
  .changelog-layout { grid-template-columns: 1fr; }
  .changelog-toc { display: none; }
}
.changelog-timeline { margin: 0 auto; }
.timeline-entry { display: flex; gap: 16px; }
.timeline-marker {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 14px;
  flex-shrink: 0;
}
.marker-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ai-ink-5);
  border: 2px solid var(--ai-surface);
  outline: 1px solid var(--ai-border);
  flex-shrink: 0;
  margin-top: 14px;
}
.marker-dot.latest {
  background: var(--ai-ink-1);
  outline-color: var(--ai-ink-1);
}
.marker-line {
  flex: 1;
  width: 1px;
  background: var(--ai-border);
  margin-top: 4px;
}
.timeline-card {
  flex: 1;
  margin-bottom: 16px;
  padding: 16px 18px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  transition: border-color 0.15s ease;
}
.timeline-card:hover {
  border-color: var(--ai-border-2);
}
.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.card-version {
  font-family: var(--ai-font-mono);
  font-size: 16px;
  font-weight: 600;
  color: var(--ai-ink-1);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0;
}
.card-latest {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  font-size: 11px;
  font-weight: 500;
}
.card-date {
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-4);
  margin-left: auto;
  font-weight: 400;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0;
}

/* ─────────── Story grid ─────────── */
.story-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}
.story-card {
  min-width: 0;
  padding: 12px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
}
.story-head {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--ai-ink-3);
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.story-icon {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
}
.story-card h4 {
  margin: 8px 0 0;
  color: var(--ai-ink-1);
  font-size: 13px;
  line-height: 1.4;
  font-weight: 500;
  letter-spacing: -0.005em;
  overflow-wrap: anywhere;
}
.story-card p {
  margin: 6px 0 0;
  color: var(--ai-ink-4);
  font-size: 12px;
  line-height: 1.55;
  font-weight: 400;
  overflow-wrap: anywhere;
}

/* ─────────── Card sections (change type badges + items) ─────────── */
.card-section { margin-bottom: 12px; }
.card-section:last-child { margin-bottom: 0; }
.card-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  margin-bottom: 8px;
  border: 1px solid transparent;
}
/* highlights → accent (ai-pill accent palette) */
.tag-highlights {
  color: var(--ai-accent-ink);
  background: var(--ai-accent-soft);
}
/* features → info (blue, ai-pill info) */
.tag-features {
  color: var(--ai-info);
  background: var(--ai-info-soft);
}
/* improvements → ok (green, ai-pill ok) */
.tag-improvements {
  color: var(--ai-ok);
  background: var(--ai-ok-soft);
}
/* fixes → warn (orange/amber, ai-pill warn) */
.tag-fixes {
  color: var(--ai-warn);
  background: var(--ai-warn-soft);
}
.card-section ul { list-style: none; margin: 0; padding: 0; }
.card-section li {
  position: relative;
  padding-left: 14px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--ai-ink-2);
  font-weight: 400;
  overflow-wrap: anywhere;
}
.card-section li::before {
  content: '';
  position: absolute;
  left: 0;
  top: 9px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--ai-ink-5);
}
/* inline code（如果未来 markdown 渲染出来） → ai-surface-2 + ai-border + mono 11.5px */
.card-section :deep(code),
.timeline-card :deep(code) {
  padding: 1px 5px;
  font-size: 11.5px;
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-1);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: 3px;
}
/* 版本号 / commit hash / 文件路径 在内容中可能出现 —— 用 mono */
.timeline-card :deep(.mono),
.timeline-card :deep(.commit-hash),
.timeline-card :deep(.file-path) {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0;
}

@media (max-width: 480px) {
  .release-spotlight { padding: 14px; }
  .spotlight-copy h3 { font-size: 18px; }
  .spotlight-visual { min-height: 260px; padding: 10px; }
  .visual-flow,
  .screen-main {
    grid-template-columns: 1fr;
  }
  .flow-arrow { display: none; }
  .image-tile { min-height: 110px; }
  .timeline-card { padding: 12px 14px; margin-bottom: 12px; }
  .timeline-entry { gap: 10px; }
  .card-version { font-size: 14px; }
  .card-header { margin-bottom: 8px; }
  .card-section li { font-size: 12px; line-height: 1.65; }
}
@media (max-width: 900px) {
  .release-spotlight {
    grid-template-columns: 1fr;
  }
  .visual-flow,
  .screen-main {
    grid-template-columns: 1fr;
  }
  .flow-arrow { display: none; }
  .guide-strip,
  .story-grid {
    grid-template-columns: 1fr;
  }
}
</style>
