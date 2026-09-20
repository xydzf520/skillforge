<template>
  <a-modal v-model:visible="visible" :footer="false" :closable="false" class="command-palette-modal"
    :mask-style="{ background: 'rgba(0,0,0,0.4)' }" unmount-on-close>
    <div class="command-palette">
      <a-input
        ref="searchInput"
        v-model="query"
        placeholder="搜索 Skill、页面、操作..."
        size="large"
        allow-clear
        @keydown.up.prevent="moveSelection(-1)"
        @keydown.down.prevent="moveSelection(1)"
        @keydown.enter.prevent="executeSelected"
        @keydown.escape="visible = false"
      >
        <template #prefix><icon-search /></template>
      </a-input>

      <div class="results" v-if="groupedResults.length">
        <div v-for="group in groupedResults" :key="group.type" class="result-group">
          <div class="group-title">{{ group.title }}</div>
          <div
            v-for="(item, idx) in group.items"
            :key="item.id"
            class="result-item"
            :class="{ active: flatIndex(group, Number(idx)) === selectedIndex }"
            @click="execute(item)"
            @mouseenter="selectedIndex = flatIndex(group, Number(idx))"
          >
            <span class="item-label">{{ item.label }}</span>
            <span v-if="item.hint" class="item-hint">{{ item.hint }}</span>
            <span v-if="item.shortcut" class="item-shortcut">{{ item.shortcut }}</span>
          </div>
        </div>
      </div>
      <div v-else-if="query" class="results-empty">未找到匹配项</div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { skillApi as rawSkillApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { IconSearch } from '@arco-design/web-vue/es/icon'

type PaletteItem = {
  id: string
  label: string
  path?: string
  hint?: string
  shortcut?: string
  department?: string
  adminOnly?: boolean
}

type PaletteGroup = {
  type: string
  title: string
  items: PaletteItem[]
}

const skillApi = rawSkillApi
const router = useRouter()
const userStore = useUserStore()
const visible = ref(false)
const query = ref('')
const selectedIndex = ref(0)
const searchInput = ref<{ focus: () => void } | null>(null)
const skillResults = ref<PaletteItem[]>([])

// 页面导航
const pages = [
  { id: 'p-home', label: 'Skill 列表', path: '/', hint: '首页' },
  { id: 'p-reviews', label: '审核中心', path: '/reviews' },
  { id: 'p-exec', label: '执行监控', path: '/executions' },
  { id: 'p-dash', label: '效果看板', path: '/dashboard' },
  { id: 'p-ds', label: '数据源管理', path: '/datasources' },
  { id: 'p-prj', label: '项目宿主', path: '/projects' },
  { id: 'p-pb', label: 'Playbook', path: '/playbooks' },
  { id: 'p-users', label: '用户管理', path: '/admin/users', hint: '管理员', adminOnly: true },
  { id: 'p-audit', label: '审计日志', path: '/admin/audit', hint: '管理员', adminOnly: true },
  { id: 'p-new', label: '新建 Skill', path: '/skill/new', shortcut: '' },
]

// 最近访问
function getRecent() {
  try {
    return JSON.parse(localStorage.getItem('sf_recent_skills') || '[]').slice(0, 5)
  } catch { return [] }
}

// 搜索 Skill
let _searchTimer: ReturnType<typeof setTimeout> | null = null
watch(query, (q) => {
  if (_searchTimer) clearTimeout(_searchTimer)
  if (!q || q.length < 1) { skillResults.value = []; return }
  _searchTimer = setTimeout(async () => {
    try {
      const res = await skillApi.list({ q, page_size: 5 })
      const items = (res as { data?: { items?: Array<Record<string, string>> } }).data?.items || []
      skillResults.value = items.map((s: Record<string, string>) => ({
        id: `s-${s.id}`, label: s.name || s.id, hint: s.department,
        path: `/skills/${s.id}`,
      }))
    } catch { skillResults.value = [] }
  }, 200)
})

const groupedResults = computed((): PaletteGroup[] => {
  const groups: PaletteGroup[] = []
  const q = query.value.toLowerCase()

  // Skill 搜索结果
  if (skillResults.value.length) {
    groups.push({ type: 'skill', title: 'Skill', items: skillResults.value })
  }

  // 页面
  const visiblePages = pages.filter((p) => !p.adminOnly || userStore.isAdmin)
  const matchedPages = q
    ? visiblePages.filter(p => p.label.toLowerCase().includes(q) || (p.hint || '').toLowerCase().includes(q))
    : visiblePages.slice(0, 5)
  if (matchedPages.length) {
    groups.push({ type: 'page', title: '页面', items: matchedPages })
  }

  // 最近访问
  if (!q) {
    const recent = getRecent()
    if (recent.length) {
      groups.push({ type: 'recent', title: '最近访问', items: recent })
    }
  }

  return groups
})

// 键盘导航
const totalItems = computed(() => groupedResults.value.reduce((s: number, g: PaletteGroup) => s + g.items.length, 0))

function flatIndex(group: PaletteGroup, idx: number) {
  let offset = 0
  for (const g of groupedResults.value) {
    if (g === group) return offset + idx
    offset += g.items.length
  }
  return 0
}

function getItemByFlatIndex(fi: number) {
  let offset = 0
  for (const g of groupedResults.value) {
    if (fi < offset + g.items.length) return g.items[fi - offset]
    offset += g.items.length
  }
  return null
}

function moveSelection(delta: number) {
  selectedIndex.value = Math.max(0, Math.min(totalItems.value - 1, selectedIndex.value + delta))
}

function executeSelected() {
  const item = getItemByFlatIndex(selectedIndex.value)
  if (item) execute(item)
}

function execute(item: PaletteItem) {
  visible.value = false
  query.value = ''
  if (item.path) {
    // 记录最近访问
    if (item.path.startsWith('/skill/') && item.path !== '/skill/new') {
      try {
        const recent = (getRecent() as PaletteItem[]).filter((r: PaletteItem) => r.id !== item.id)
        recent.unshift({ id: item.id, label: item.label, hint: item.hint, path: item.path })
        localStorage.setItem('sf_recent_skills', JSON.stringify(recent.slice(0, 10)))
      } catch { /* ignore */ }
    }
    router.push(item.path)
  }
}

watch(visible, async (v) => {
  if (v) {
    selectedIndex.value = 0
    query.value = ''
    await nextTick()
    searchInput.value?.focus()
  }
})

// 全局快捷键
function onKeydown(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault()
    visible.value = !visible.value
  }
}
onMounted(() => document.addEventListener('keydown', onKeydown))
onUnmounted(() => document.removeEventListener('keydown', onKeydown))

defineExpose({ open: () => { visible.value = true } })
</script>

<style scoped>
.command-palette { max-width: 560px; }
.result-group { margin-top: 8px; }
.group-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--ai-ink-3);
  text-transform: uppercase;
  padding: 4px 8px;
}
.result-item {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  border-radius: 6px;
  cursor: pointer;
  gap: 8px;
}
.result-item.active { background: var(--ai-surface-2); }
.item-label { flex: 1; font-size: 14px; }
.item-hint { font-size: 12px; color: var(--ai-ink-3); }
.item-shortcut {
  font-size: 11px;
  color: var(--ai-ink-4);
  background: var(--ai-surface-2);
  padding: 2px 6px;
  border-radius: 3px;
}
.results-empty { text-align: center; color: var(--ai-ink-3); padding: 20px; }
</style>

<style>
.command-palette-modal .arco-modal { max-width: 580px; }
.command-palette-modal .arco-modal-body { padding: 8px; }
</style>
