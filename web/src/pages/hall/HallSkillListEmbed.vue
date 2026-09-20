<template>
  <div class="hall-skill-embed">
    <div class="filter-bar">
      <a-input-search
        v-model="searchQuery"
        placeholder="搜索 Skill"
        allow-clear
        class="filter-ctrl filter-ctrl-wide"
        @search="load"
        @clear="load"
      />
      <a-select
        v-model="filters.sort_by"
        class="filter-ctrl filter-ctrl-sort"
        @change="load"
      >
        <a-option value="popularity">按热度</a-option>
        <a-option value="updated_at">最近更新</a-option>
        <a-option value="name">按名称</a-option>
      </a-select>
    </div>

    <a-spin
      :loading="loading"
      tip="加载 Skill..."
      style="width: 100%"
      role="status"
      aria-live="polite"
    >
      <a-row v-if="skills.length" class="skill-grid" :gutter="[16, 16]">
        <a-col v-for="s in skills" :key="s.id" :xs="24" :sm="12" :md="8" :lg="6">
          <SkillCard variant="hall" :skill="s" @click="goSkill" />
        </a-col>
      </a-row>
      <a-empty v-else description="没有匹配的 Skill" />
    </a-spin>

    <div v-if="total > pageSize" class="table-footer">
      <a-pagination
        v-model:current="page"
        :total="total"
        :page-size="pageSize"
        size="small"
        @change="load"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { skillApi } from '@/api'
import { goSkill as routeSkill } from '@/utils/goSkill'
import SkillCard from '@/components/skills/SkillCard.vue'

const router = useRouter()
const loading = ref(false)
const skills = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 24
const searchQuery = ref('')
const filters = reactive({ sort_by: 'popularity' })

const FINETUNED_MODEL_SKILL = {
  id: 'skillforge-finetuned-model-chat',
  name: '全量微调大模型对话',
  display_name: '全量微调大模型对话',
  description: '使用全量历史数据完成三轮 4B 微调后，在大厅直接与已部署模型对话。',
  department: 'AI平台',
  category: '用户对话',
  status: 'active',
  visibility: 'company',
  icon: 'spark',
  owner_name: 'SkillForge',
  risk_level: 'R2',
  usage_count: 0,
  fork_count: 0,
  is_member: true,
  can_fork: false,
  route_path: '/hall/finetuned-model-chat',
  permissions: { read: true, execute: true, edit: false },
  profile: {
    usage_count: 0,
    success_rate: null,
    last_run_at: null,
    adoption_departments: ['AI平台'],
    data_sources: [],
  },
}

function builtinSkillVisible(): boolean {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return true
  return [
    FINETUNED_MODEL_SKILL.id,
    FINETUNED_MODEL_SKILL.name,
    FINETUNED_MODEL_SKILL.description,
    FINETUNED_MODEL_SKILL.department,
    FINETUNED_MODEL_SKILL.category,
  ].some(value => String(value || '').toLowerCase().includes(q))
}

async function load() {
  loading.value = true
  try {
    const params: any = {
      page: page.value,
      page_size: pageSize,
      sort_by: filters.sort_by,
      include: 'profile',
    }
    if (searchQuery.value) params.q = searchQuery.value
    const res: any = await skillApi.hall(params)
    const remoteItems = res?.items || []
    const includeBuiltin = page.value === 1 && builtinSkillVisible()
    skills.value = (includeBuiltin ? [FINETUNED_MODEL_SKILL, ...remoteItems] : remoteItems).slice(0, pageSize)
    total.value = (res?.total || 0) + (builtinSkillVisible() ? 1 : 0)
  } catch {
    const includeBuiltin = page.value === 1 && builtinSkillVisible()
    skills.value = includeBuiltin ? [FINETUNED_MODEL_SKILL] : []
    total.value = builtinSkillVisible() ? 1 : 0
  } finally {
    loading.value = false
  }
}

function goSkill(s: any) {
  routeSkill(router, s.id, s)
}

onMounted(load)
</script>

<style scoped>
.hall-skill-embed {
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
  max-width: 150px;
}
.skill-grid :deep(.arco-col) {
  display: flex;
}
.hall-skill-embed :deep(.arco-empty) {
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
