<template>
  <div class="hall-team-tab">
    <div class="filter-bar">
      <a-input-search
        v-model="searchQuery"
        placeholder="搜索部门 / AI 联系人"
        allow-clear
        class="filter-ctrl filter-ctrl-wide"
      />
      <a-select v-model="sortBy" class="filter-ctrl filter-ctrl-sort">
        <a-option value="active_desc">按活跃 Skill 数</a-option>
        <a-option value="total_desc">按 Skill 总数</a-option>
        <a-option value="data_desc">按数据源数量</a-option>
        <a-option value="name_asc">按部门名</a-option>
      </a-select>
    </div>

    <a-spin
      :loading="loading"
      tip="加载团队能力..."
      style="width: 100%"
      role="status"
      aria-live="polite"
    >
      <a-row v-if="teams.length" :gutter="[16, 16]">
        <a-col v-for="t in teams" :key="t.department" :xs="24" :sm="12" :md="8" :lg="6">
          <TeamCapabilityCard :team="t" @view="goDetail" />
        </a-col>
      </a-row>
      <a-empty v-else :description="searchQuery ? '没有匹配的团队' : '暂无团队能力数据'" />
    </a-spin>

    <div v-if="total > pageSize" class="table-footer">
      <a-pagination
        v-model:current="page"
        :total="total"
        :page-size="pageSize"
        size="small"
        show-total
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { hallApi } from '@/api'
import TeamCapabilityCard from '@/components/hall/TeamCapabilityCard.vue'

const router = useRouter()
const loading = ref(false)
const teams = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 60
const searchQuery = ref('')
const sortBy = ref('active_desc')

let searchTimer: ReturnType<typeof setTimeout> | null = null

async function load() {
  loading.value = true
  try {
    const params: Record<string, unknown> = {
      page: page.value,
      page_size: pageSize,
      sort_by: sortBy.value,
    }
    if (searchQuery.value.trim()) params.q = searchQuery.value.trim()
    const r: any = await hallApi.teamList(params)
    if (Array.isArray(r)) {
      teams.value = r
      total.value = r.length
    } else {
      teams.value = r?.items || []
      total.value = r?.total || 0
    }
  } catch {
    teams.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function debouncedReload() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    load()
  }, 250)
}

watch(searchQuery, debouncedReload)
watch(sortBy, () => {
  page.value = 1
  load()
})
watch(page, load)

function goDetail(team: any) {
  router.push(`/hall/team/${encodeURIComponent(team.department)}`)
}

onMounted(load)
</script>

<style scoped>
.hall-team-tab {
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
.filter-ctrl-sort {
  max-width: 180px;
}
.hall-team-tab :deep(.arco-empty) {
  border: 1px dashed var(--ai-border);
  border-radius: var(--ai-radius-l);
  background: var(--ai-surface);
  padding: 32px;
}
.table-footer {
  margin-top: 24px;
  display: flex;
  justify-content: flex-end;
}
</style>
