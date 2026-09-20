<template>
  <div class="hall-data-tab">
    <div class="filter-bar">
      <a-input-search
        v-model="searchQuery"
        placeholder="搜索数据源名称 / 描述 / 用途"
        allow-clear
        class="filter-ctrl filter-ctrl-wide"
      />
      <a-select
        v-model="filters.source_type"
        placeholder="类型"
        allow-clear
        class="filter-ctrl filter-ctrl-narrow"
      >
        <a-option value="csv_upload">CSV 上传</a-option>
        <a-option value="api_pull">API 拉取</a-option>
        <a-option value="crawler">爬虫</a-option>
      </a-select>
      <a-select
        v-model="filters.department"
        placeholder="部门"
        multiple
        allow-clear
        class="filter-ctrl filter-ctrl-mid"
      >
        <a-option v-for="d in availableDepartments" :key="d" :value="d">{{ d }}</a-option>
      </a-select>
      <a-select
        v-model="filters.visibility"
        placeholder="可见性"
        allow-clear
        class="filter-ctrl filter-ctrl-narrow"
      >
        <a-option value="company">全公司</a-option>
        <a-option value="department">部门</a-option>
        <a-option value="private">私有</a-option>
      </a-select>
      <a-select
        v-model="filters.freshness"
        placeholder="新鲜度"
        allow-clear
        class="filter-ctrl filter-ctrl-narrow"
      >
        <a-option value="fresh">新鲜</a-option>
        <a-option value="stale">滞后</a-option>
        <a-option value="unknown">未知</a-option>
      </a-select>
      <a-select v-model="filters.sort_by" class="filter-ctrl filter-ctrl-sort">
        <a-option value="updated_at">最近更新</a-option>
        <a-option value="consumers_desc">被引用数</a-option>
        <a-option value="name_asc">按名称</a-option>
      </a-select>
    </div>

    <a-spin
      :loading="loading"
      tip="加载数据能力..."
      style="width: 100%"
      role="status"
      aria-live="polite"
    >
      <a-row v-if="items.length" :gutter="[16, 16]">
        <a-col
          v-for="item in items"
          :key="item.id"
          :xs="24"
          :sm="12"
          :md="8"
          :lg="6"
        >
          <DataSourceCard
            :source="item"
            @click="goDetail"
            @view="goDetail"
            @request="onRequest"
          />
        </a-col>
      </a-row>
      <a-empty v-else description="没有找到匹配的数据能力" />
    </a-spin>

    <div v-if="total > pageSize" class="table-footer">
      <a-pagination
        v-model:current="page"
        :total="total"
        :page-size="pageSize"
        size="small"
        show-total
        @change="load"
      />
    </div>

    <DataAccessRequestModal
      v-model:visible="requestModalVisible"
      :source-id="requestSourceId"
      :source-name="requestSourceName"
      :owner-contact="requestOwnerContact"
      @submitted="onRequestSubmitted"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { hallApi } from '@/api'
import DataSourceCard from '@/components/hall/DataSourceCard.vue'
import DataAccessRequestModal from '@/components/hall/DataAccessRequestModal.vue'

const router = useRouter()

const loading = ref(false)
const items = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 24
const searchQuery = ref('')
const filters = reactive({
  source_type: undefined as string | undefined,
  department: [] as string[],
  visibility: undefined as string | undefined,
  freshness: undefined as string | undefined,
  sort_by: 'updated_at',
})
const knownDepartments = ref<string[]>([])

const requestModalVisible = ref(false)
const requestSourceId = ref('')
const requestSourceName = ref('')
const requestOwnerContact = ref('')

let searchTimer: ReturnType<typeof setTimeout> | null = null

function normalizeDepartmentName(item: unknown): string {
  if (typeof item === 'string') return item.trim()
  if (!item || typeof item !== 'object') return ''
  const row = item as Record<string, unknown>
  const name = row.name || row.department
  return typeof name === 'string' ? name.trim() : ''
}

async function loadDepartments() {
  try {
    const r: any = await hallApi.departments()
    const list: unknown[] = Array.isArray(r) ? r : Array.isArray(r?.departments) ? r.departments : []
    const departments = Array.from(
      new Set<string>(
        list.map(normalizeDepartmentName).filter((name): name is string => Boolean(name)),
      ),
    ).sort()
    knownDepartments.value = departments
  } catch {
    knownDepartments.value = []
  }
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, unknown> = {
      page: page.value,
      page_size: pageSize,
      sort_by: filters.sort_by,
    }
    if (searchQuery.value.trim()) params.q = searchQuery.value.trim()
    if (filters.source_type) params.source_type = filters.source_type
    if (filters.department.length) params.department = filters.department
    if (filters.visibility) params.visibility = filters.visibility
    if (filters.freshness) params.freshness = filters.freshness

    const r = (await hallApi.data(params)) as { items: any[]; total: number }
    items.value = r.items || []
    total.value = r.total || 0
  } catch {
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

const availableDepartments = computed(() => knownDepartments.value)

function debouncedReload() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    load()
  }, 250)
}

watch(searchQuery, debouncedReload)
watch(
  () => [filters.source_type, filters.department, filters.visibility, filters.freshness, filters.sort_by],
  () => {
    page.value = 1
    load()
  },
  { deep: true },
)
watch(page, load)

function goDetail(source: any) {
  router.push(`/hall/data/${source.id}`)
}

function onRequest(source: any) {
  requestSourceId.value = source.id
  requestSourceName.value = source.name || source.id
  requestOwnerContact.value = source.owner_contact || ''
  requestModalVisible.value = true
}

function onRequestSubmitted(_requestId: number) {
  load()
}

onMounted(() => {
  loadDepartments()
  load()
})
</script>

<style scoped>
.hall-data-tab {
  padding: 4px 0;
}
.filter-bar {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.filter-ctrl {
  width: 100%;
}
.filter-ctrl-wide {
  max-width: 260px;
}
.filter-ctrl-narrow {
  max-width: 110px;
}
.filter-ctrl-mid {
  max-width: 200px;
}
.filter-ctrl-sort {
  max-width: 150px;
}
.hall-data-tab :deep(.arco-empty) {
  border: 1px dashed var(--ai-border);
  border-radius: var(--ai-radius-l);
  background: var(--ai-surface);
  padding: 32px;
}
@media (max-width: 768px) {
  .filter-ctrl {
    max-width: 100%;
  }
}
.table-footer {
  margin-top: 24px;
  display: flex;
  justify-content: flex-end;
}
</style>
