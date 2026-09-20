<template>
  <div class="page-container portal-home">
    <aside class="portal-sidebar ai-sidebar" :class="{ 'portal-sidebar--open': sidebarOpen }">
      <div class="portal-sidebar-head">
        <span class="portal-sidebar-title">应用门户</span>
        <button class="portal-sidebar-close" type="button" aria-label="关闭导航" @click="sidebarOpen = false">
          <SfShellIcon name="x" />
        </button>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">Skills 模块</div>
        <router-link class="ai-side-item" to="/skills">
          <SfShellIcon name="cube" class="ic" />
          <span>Skills</span>
        </router-link>
        <button class="ai-side-item active" type="button" @click="clearPortalFilters">
          <SfShellIcon name="grid" class="ic" />
          <span>应用门户</span>
          <span class="count">{{ total }}</span>
        </button>
        <router-link class="ai-side-item" to="/reviews">
          <SfShellIcon name="check" class="ic" />
          <span>审核中心</span>
        </router-link>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">按部门</div>
        <button
          v-for="item in departmentOptions"
          :key="item.name"
          class="ai-side-item"
          :class="{ active: department === item.name }"
          type="button"
          @click="setDepartment(department === item.name ? '' : item.name)"
        >
          <SfShellIcon name="dept" class="ic" />
          <span class="ai-side-text">{{ item.name }}</span>
          <span class="count">{{ item.count }}</span>
        </button>
        <button v-if="department" class="ai-side-item" type="button" @click="setDepartment('')">
          <span class="ai-side-text muted-text">清除部门筛选</span>
        </button>
        <div v-if="!departmentOptions.length" class="ai-side-item disabled">
          <span class="ai-side-text muted-text">暂无部门</span>
        </div>
      </div>
    </aside>

    <main class="portal-main">
      <button class="portal-sidebar-toggle" type="button" @click="sidebarOpen = !sidebarOpen" aria-label="打开导航">
        <SfShellIcon name="list" />
        <span>{{ sidebarLabel }}</span>
      </button>

      <div class="ai-pagehead portal-pagehead">
        <div>
          <div class="ai-crumbs">Skills · 应用门户</div>
          <h1 class="ai-title">应用门户 · 执行入口</h1>
          <p class="ai-sub">面向普通员工：浏览本部门已发布、可直接执行的 Skill。新建 / 编辑请到「Skills 工作台」。</p>
        </div>
        <div class="portal-head-actions">
          <button type="button" class="ai-btn" @click="$router.push('/portal/overview')">部门概览</button>
          <button type="button" class="ai-btn primary" @click="$router.push('/hall')">
            <SfShellIcon name="plus" />
            申请新能力
          </button>
        </div>
      </div>

      <section class="portal-shell ai-pagebody">
        <div class="portal-toolbar">
          <a-input-search
            v-model="searchText"
            placeholder="搜索 Skill"
            allow-clear
            size="small"
            class="portal-search"
            style="width: 320px"
            @search="onSearchSubmit"
          />
          <a-select v-model="category" placeholder="分类" allow-clear size="small" class="portal-category-select" style="width: 140px">
            <a-option v-for="item in categoryOptions" :key="item.name" :value="item.name">
              {{ item.name }}<template v-if="item.count"> ({{ item.count }})</template>
            </a-option>
          </a-select>
          <div class="portal-toolbar-spacer" />
          <button class="portal-pill" :class="{ active: !category && !department }" type="button" @click="clearPortalFilters">
            全部 {{ unfilteredTotal }}
          </button>
          <button
            v-for="item in visibleCategoryPills"
            :key="item.name"
            class="portal-pill"
            :class="{ active: category === item.name }"
            type="button"
            @click="setCategory(category === item.name ? '' : item.name)"
          >
            {{ item.name }} {{ item.count }}
          </button>
          <button v-if="department" class="portal-pill active" type="button" @click="setDepartment('')">
            {{ department }} {{ total }}
          </button>
        </div>

        <a-spin :loading="loading">
          <div v-if="skills.length" class="portal-grid">
            <article
              v-for="skill in skills"
              :key="skill.id"
              class="portal-skill-card"
              role="button"
              tabindex="0"
              :aria-label="`Skill ${skill.display_name || skill.name || skill.id}`"
              @click="goDetail(skill.id)"
              @keydown.enter.prevent="goDetail(skill.id)"
              @keydown.space.prevent="goDetail(skill.id)"
            >
              <div class="portal-card-head">
                <span class="portal-card-icon">
                  <SfShellIcon :name="portalShellIconName(skill.icon)" />
                </span>
                <div class="portal-card-headline">
                  <h3 class="portal-card-name">{{ skill.display_name || skill.name || skill.id }}</h3>
                  <div class="portal-card-owner">负责人 · {{ skill.owner_name || '未设置' }}</div>
                </div>
              </div>
              <div class="portal-card-stats">
                <div class="portal-stat">
                  <div class="portal-stat-label">使用次数</div>
                  <span class="portal-stat-value mono">{{ skill.usage_count || 0 }}</span>
                </div>
                <div class="portal-stat">
                  <div class="portal-stat-label">成功率</div>
                  <span class="portal-stat-value mono">{{ formatPercent(skill.success_rate) }}</span>
                </div>
                <div class="portal-stat portal-stat-wide">
                  <div class="portal-stat-label">最近执行</div>
                  <span class="portal-stat-time">{{ skill.last_run_at ? relativeTime(skill.last_run_at) : '暂无执行' }}</span>
                </div>
              </div>
              <div class="portal-card-foot">
                <button
                  class="ai-btn primary portal-run-btn"
                  type="button"
                  @click.stop="goDetail(skill.id)"
                >
                  <SfShellIcon name="play" /> 运行
                </button>
                <button
                  class="ai-btn sm portal-ui-btn"
                  type="button"
                  title="界面"
                  @click.stop="goUi(skill.id)"
                >
                  界面
                </button>
              </div>
            </article>
          </div>
          <SfEmptyState
            v-else
            title="应用门户"
            description="本部门暂无可执行的 Skill"
            hint="这里只展示已审核通过、处于 active 状态的 Skill。如果你是 AIBP，请到「Skills 工作台 → 新建」创建；否则等待本部门 AIBP 发布后刷新。"
          />
        </a-spin>

        <div class="bottom-bar">
          <span class="portal-bottom-text">共 {{ total }} 条 · 显示 {{ skills.length }} / {{ total }}</span>
          <a-pagination
            :current="page"
            :page-size="pageSize"
            :total="total"
            size="small"
            show-total
            @change="onPageChange"
          />
        </div>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { portalApi as rawPortalApi } from '@/api'
import { formatPercent, normalizePagedResponse } from './shared'
import { relativeTime } from '@/utils/format'
import { SfEmptyState } from '@/components/common'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

defineOptions({ name: 'PortalHome' })

type PortalSkill = {
  id: string
  name?: string
  display_name?: string
  summary?: string
  category?: string
  icon?: string
  owner_name?: string
  org_unit_name?: string
  usage_count?: number
  success_rate?: number
  last_run_at?: string
  tags?: string[]
  has_params?: boolean
  status?: string
}

type CountOption = {
  name: string
  count: number
}

const portalApi: any = rawPortalApi
const router = useRouter()
const loading = ref(false)
const skills = ref<PortalSkill[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 12
const searchText = ref('')
const category = ref('')
const department = ref('')
const sidebarOpen = ref(false)
const categoryStats = ref<CountOption[]>([])
const departmentOptions = ref<CountOption[]>([])
let searchTimer: ReturnType<typeof setTimeout> | null = null
let requestSeq = 0

function normalizeCountOptions(value: unknown): CountOption[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item: any) => ({
      name: String(item?.name || '').trim(),
      count: Number(item?.count || 0),
    }))
    .filter((item) => item.name)
}

const categoryOptions = computed<CountOption[]>(() => {
  if (categoryStats.value.length) return categoryStats.value
  const seen = new Map<string, number>()
  for (const skill of skills.value) {
    const name = skill.category || '通用'
    seen.set(name, (seen.get(name) || 0) + 1)
  }
  if (category.value && !seen.has(category.value)) seen.set(category.value, total.value)
  return Array.from(seen.entries()).map(([name, count]) => ({ name, count }))
})

const visibleCategoryPills = computed(() => categoryOptions.value.slice(0, 3))
const unfilteredTotal = computed(() => {
  const sum = categoryOptions.value.reduce((acc, item) => acc + item.count, 0)
  return sum || total.value
})
const sidebarLabel = computed(() => department.value || '应用门户')

async function loadSkills() {
  const seq = ++requestSeq
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  loading.value = true
  try {
    const res = await portalApi.listSkills({
      search: searchText.value.trim() || undefined,
      category: category.value || undefined,
      department: department.value || undefined,
      page: page.value,
      page_size: pageSize,
    })
    if (seq !== requestSeq) return
    const next = normalizePagedResponse<PortalSkill>(res)
    skills.value = next.items
    total.value = next.total
    categoryStats.value = normalizeCountOptions((res as Record<string, unknown>)?.categories)
    departmentOptions.value = normalizeCountOptions((res as Record<string, unknown>)?.departments)
  } catch {
    if (seq === requestSeq) {
      skills.value = []
      total.value = 0
    }
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

function scheduleSearch() {
  if (searchTimer) clearTimeout(searchTimer)
  page.value = 1
  searchTimer = setTimeout(() => {
    loadSkills()
  }, 300)
}

function onPageChange(nextPage: number) {
  page.value = nextPage
  loadSkills()
}

function onSearchSubmit() {
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  page.value = 1
  loadSkills()
}

function setCategory(value: string) {
  category.value = value
}

function setDepartment(value: string) {
  department.value = value
  sidebarOpen.value = false
}

function clearPortalFilters() {
  searchText.value = ''
  category.value = ''
  department.value = ''
  page.value = 1
  sidebarOpen.value = false
  loadSkills()
}

function goDetail(id: string) {
  router.push(`/portal/skills/${id}`)
}

function goUi(id: string) {
  router.push({ path: `/portal/skills/${id}`, query: { focus: 'ui' } })
}

function portalShellIconName(name?: string): string {
  const iconMap: Record<string, string> = {
    apps: 'grid',
    'bar-chart': 'trend',
    calendar: 'clock',
    cloud: 'database',
    code: 'flow',
    dashboard: 'grid',
    file: 'doc',
    safe: 'shield',
    search: 'search',
    settings: 'cube',
    thunderbolt: 'bolt',
  }
  return iconMap[name || ''] || 'cube'
}

watch(searchText, scheduleSearch)
watch(category, () => {
  page.value = 1
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  loadSkills()
})
watch(department, () => {
  page.value = 1
  if (searchTimer) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  loadSkills()
})

onMounted(loadSkills)

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
})
</script>

<style scoped>
.portal-home {
  display: flex;
  flex-direction: row;
  gap: 0;
  align-items: stretch;
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
  background: var(--ai-bg);
  min-height: calc(100vh - 52px);
  padding: 0 !important;
  max-width: none !important;
}

.portal-sidebar {
  width: 220px;
  flex: 0 0 220px;
  border-right: 1px solid var(--ai-border);
  background: var(--ai-surface);
  padding: 16px 12px;
  overflow-y: auto;
}
.portal-sidebar-head {
  display: none;
  align-items: center;
  justify-content: space-between;
  padding: 0 4px 6px;
}
.portal-sidebar-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.portal-sidebar-close {
  display: none;
  background: transparent;
  border: 0;
  cursor: pointer;
  color: var(--ai-ink-4);
  padding: 4px;
}
.portal-sidebar .ai-side-group {
  margin-bottom: 18px;
}
.portal-sidebar .ai-side-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-4);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 0 8px 6px;
}
.portal-sidebar .ai-side-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 28px;
  padding: 0 8px;
  border-radius: 5px;
  font-size: 13px;
  color: var(--ai-ink-2);
  cursor: pointer;
  border: 0;
  background: transparent;
  text-decoration: none;
  font-family: var(--ai-font-sans);
  text-align: left;
}
.portal-sidebar .ai-side-item:hover,
.portal-sidebar .ai-side-item.active {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}
.portal-sidebar .ai-side-item.active {
  font-weight: 500;
}
.portal-sidebar .ai-side-item.disabled {
  cursor: default;
  opacity: 0.55;
}
.portal-sidebar .ai-side-item .ic {
  color: var(--ai-ink-4);
  font-size: 13px;
  width: 13px;
  height: 13px;
}
.portal-sidebar .ai-side-item.active .ic {
  color: var(--ai-ink-1);
}
.portal-sidebar .ai-side-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.portal-sidebar .muted-text {
  color: var(--ai-ink-4);
  font-size: 12px;
}
.portal-sidebar .count {
  margin-left: auto;
  font-size: 11px;
  color: var(--ai-ink-4);
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
}

.portal-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow-x: auto;
  padding: 0;
}
.portal-sidebar-toggle {
  display: none;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  padding: 0 12px;
  height: 30px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ai-ink-1);
  cursor: pointer;
  font-family: var(--ai-font-sans);
  align-self: flex-start;
}

.portal-pagehead {
  flex: 0 0 auto;
}
.portal-head-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}
.portal-head-actions svg {
  width: 12px;
  height: 12px;
  flex: 0 0 12px;
}

.portal-shell {
  display: flex;
  flex-direction: column;
  gap: 16px;
  background: transparent;
}
.portal-shell :deep(.arco-spin),
.portal-shell :deep(.arco-spin-children) {
  width: 100%;
}

.portal-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.portal-search {
  width: 320px;
}
.portal-category-select {
  width: 140px;
}
.portal-toolbar-spacer {
  flex: 1 1 auto;
  min-width: 8px;
}
.portal-pill {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 11.5px;
  font-weight: 500;
  cursor: pointer;
  font-family: var(--ai-font-sans);
}
.portal-pill.active {
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
}

.portal-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

/* Skill 卡片：对齐设计稿 direct-capability-card 风格 */
.portal-skill-card {
  height: auto;
  min-height: 170px;
  padding: 14px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 10px;
  position: relative;
  transition: border-color 0.15s, box-shadow 0.15s;
  font-family: var(--ai-font-sans);
}
.portal-skill-card:hover {
  border-color: var(--ai-border-2);
  box-shadow: var(--ai-shadow-2);
}
.portal-skill-card:focus-visible {
  outline: 2px solid var(--ai-accent);
  outline-offset: 2px;
}

.portal-card-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.portal-card-icon {
  width: 30px;
  height: 30px;
  border-radius: 7px;
  display: grid;
  place-items: center;
  color: var(--ai-ink-1);
  background: var(--ai-surface-2);
  flex: 0 0 30px;
  font-size: 14px;
}
.portal-card-icon :deep(svg) {
  width: 14px;
  height: 14px;
}
.portal-card-headline {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.portal-card-name {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.01em;
  line-height: 1.3;
  color: var(--ai-ink-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.portal-card-owner {
  color: var(--ai-ink-3);
  font-size: 11.5px;
  margin-top: 3px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 中段统计区：设计稿 dashed 上下边框（webkit 1px dashed 渲染成 solid，用 background gradient pattern 代替） */
.portal-card-stats {
  display: flex;
  gap: 0;
  padding: 8px 0;
  background-image:
    linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%);
  background-position: top, bottom;
  background-size: 6px 1px;
  background-repeat: repeat-x;
}
.portal-stat {
  flex: 1;
  min-width: 0;
}
.portal-stat-wide {
  flex: 1.4;
}
.portal-stat-label {
  font-size: 10px;
  color: var(--ai-ink-4);
  font-weight: 500;
  margin-bottom: 2px;
}
.portal-stat-value {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
}
.portal-stat-time {
  font-size: 11.5px;
  color: var(--ai-ink-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: block;
}
.mono {
  font-family: var(--ai-font-mono);
}

.portal-card-foot {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: auto;
}
.portal-run-btn {
  flex: 1;
  justify-content: center;
  height: 30px;
}
.portal-ui-btn {
  min-width: 54px;
  height: 30px;
  justify-content: center;
}
.portal-card-foot svg,
.portal-sidebar .ai-side-item .ic,
.portal-sidebar-toggle svg,
.portal-sidebar-close svg {
  width: 13px;
  height: 13px;
  flex: 0 0 13px;
}

.bottom-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--ai-border);
  gap: 12px;
  flex-wrap: wrap;
}
.portal-bottom-text {
  color: var(--ai-ink-4);
  font-size: 12px;
}

/* Arco tag → ai-pill 映射（保留以防未来 a-tag 复用） */
.portal-home :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  border: 1px solid var(--ai-border);
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
}
.portal-home :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
}
.portal-home :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
}
.portal-home :deep(.arco-tag-color-orange) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
}
.portal-home :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
}

@media (max-width: 768px) {
  .portal-home {
    flex-direction: column;
  }
  .portal-sidebar {
    position: fixed;
    top: 0;
    left: 0;
    height: 100vh;
    width: min(280px, 80vw);
    max-height: none;
    z-index: 200;
    flex: 0 0 auto;
    border-right: 1px solid var(--ai-border);
    border-bottom: 0;
    transform: translateX(-110%);
    transition: transform 0.2s ease;
    box-shadow: var(--ai-shadow-2);
  }
  .portal-sidebar--open {
    transform: translateX(0);
  }
  .portal-sidebar-head {
    display: flex;
  }
  .portal-sidebar-close,
  .portal-sidebar-toggle {
    display: inline-flex;
  }
  .portal-main {
    padding: 0;
  }
  .portal-sidebar-toggle {
    margin: 16px 16px 0;
  }
  .portal-pagehead {
    flex-direction: column;
    align-items: stretch;
    padding: 16px;
  }
  .portal-shell {
    padding: 16px;
  }
  .portal-grid {
    grid-template-columns: 1fr;
  }
  .portal-search,
  .portal-category-select {
    width: 100%;
  }
}

@media (min-width: 769px) and (max-width: 1180px) {
  .portal-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
