<template>
  <div class="page-container">
    <HallDetailHeader
      :crumbs="[
        { label: '能力大厅', to: '/hall' },
        { label: '能力', to: '/hall' },
        { label: `#${category}` },
      ]"
      :title="`#${category}`"
      tooltip="能力分类 —— 按业务场景聚合 Skill"
      back-title="返回能力大厅"
    />

    <a-spin :loading="loading" tip="加载中...">
      <div v-if="detail" class="detail-body">
        <a-card class="summary-card">
          <div class="summary-grid">
            <div class="summary-item">
              <span class="summary-value">{{ detail.summary.skill_count_total }}</span>
              <span class="summary-label">Skill 总数</span>
            </div>
            <div class="summary-item">
              <span class="summary-value">{{ detail.summary.skill_count_active }}</span>
              <span class="summary-label">活跃</span>
            </div>
            <div class="summary-item">
              <span class="summary-value">{{ detail.summary.data_sources_count }}</span>
              <span class="summary-label">关联数据源</span>
            </div>
            <div class="summary-item">
              <span class="summary-value">{{ detail.summary.department_count }}</span>
              <span class="summary-label">覆盖部门</span>
            </div>
          </div>
        </a-card>

        <a-card class="detail-section" title="提供此能力的部门">
          <a-empty v-if="!detail.top_departments.length" description="暂无" />
          <div v-else class="dept-list">
            <div
              v-for="d in detail.top_departments"
              :key="d.department"
              class="dept-item"
              @click="$router.push(`/hall/team/${encodeURIComponent(d.department)}`)"
            >
              <div class="dept-name">
                <icon-idcard />
                <strong>{{ d.department }}</strong>
                <a-tag size="small" color="arcoblue">{{ d.skill_count }} 个 Skill</a-tag>
              </div>
              <div v-if="d.ai_contact" class="dept-contact">
                <icon-user /> {{ d.ai_contact.name || d.ai_contact.user_id }} · {{ roleLabel(d.ai_contact.role) }}
              </div>
              <div v-else class="dept-contact dept-contact-dim">
                <icon-user /> 暂无 AI 联系人
              </div>
            </div>
          </div>
        </a-card>

        <a-card class="detail-section" :title="`Skill 实现（${detail.skills.length}）`">
          <a-empty v-if="!detail.skills.length" description="暂无 Skill" />
          <div v-else class="skill-list">
            <div
              v-for="s in detail.skills"
              :key="s.id"
              class="skill-item"
              @click="goSkill(router, s.id, s)"
            >
              <div class="skill-line-1">
                <strong>{{ s.name || s.id }}</strong>
                <a-tag v-if="s.risk_level" size="small" :color="(riskColor as Record<string, string>)[s.risk_level] || 'gray'">{{ s.risk_level }}</a-tag>
                <a-tag size="small" :color="statusColor(s.status)">{{ statusLabel(s.status) }}</a-tag>
                <span class="skill-dept">{{ s.department }}</span>
              </div>
              <div v-if="s.description" class="skill-desc">{{ s.description }}</div>
              <div class="skill-stats">
                <span><icon-fire /> {{ s.usage_count }}</span>
                <span v-if="typeof s.success_rate === 'number'">成功率 {{ Math.round(s.success_rate * 100) }}%</span>
                <span v-if="s.last_run_at">最近 {{ relativeTime(s.last_run_at) }}</span>
              </div>
            </div>
          </div>
        </a-card>

        <a-card class="detail-section" :title="`依赖数据源（${detail.data_sources.length}）`">
          <a-empty v-if="!detail.data_sources.length" description="暂无依赖的数据源" />
          <div v-else class="ds-list">
            <a-tag
              v-for="d in detail.data_sources"
              :key="d.id"
              size="medium"
              color="arcoblue"
              class="ds-tag"
              @click="$router.push(`/hall/data/${d.id}`)"
            >{{ d.name || d.id }} · {{ visLabel(d.visibility) }}</a-tag>
          </div>
        </a-card>
      </div>
    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { IconFire, IconIdcard, IconUser } from '@arco-design/web-vue/es/icon'
import { relativeTime } from '@/utils/format'
import { riskColor } from '@/utils/constants'
import { goSkill } from '@/utils/goSkill'
import { pushRecent } from '@/utils/hallFavorites'
import { hallApi } from '@/api'
import HallDetailHeader from '@/components/hall/HallDetailHeader.vue'

const route = useRoute()
const router = useRouter()
const category = String(route.params.category)
const loading = ref(false)
const detail = ref<any>(null)

async function load() {
  loading.value = true
  try {
    detail.value = await hallApi.capability(category)
  } catch {
    detail.value = null
  } finally {
    loading.value = false
  }
}

function statusLabel(s: string) {
  const m: Record<string, string> = { active: '活跃', draft: '草稿', shadow: '影子', deprecated: '停用' }
  return m[s] || s
}
function statusColor(s: string) {
  const m: Record<string, string> = { active: 'green', draft: 'gray', shadow: 'arcoblue', deprecated: 'red' }
  return m[s] || 'gray'
}
function visLabel(v: string) {
  const m: Record<string, string> = { company: '全公司', department: '部门', private: '私有' }
  return m[v] || v
}
function roleLabel(r: string) {
  const m: Record<string, string> = {
    ai_engineer: 'AI 工程师',
    biz_owner: '业务 Owner',
    dept_admin: '部门管理员',
    admin: '管理员',
  }
  return m[r] || r
}

onMounted(() => {
  pushRecent(category, 'anon')
  load()
})
</script>

<style scoped>
.detail-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.summary-card {
  border-radius: 8px;
}
.summary-grid {
  display: flex;
  gap: 32px;
  padding: 8px 0;
}
.summary-item {
  display: flex;
  flex-direction: column;
  align-items: center;
}
.summary-value {
  font-size: 26px;
  font-weight: 700;
  color: var(--ai-accent-ink);
}
.summary-label {
  font-size: 12px;
  color: var(--ai-ink-3);
  margin-top: 4px;
}
.dept-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.dept-item {
  padding: 10px 14px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}
.dept-item:hover {
  background: var(--ai-surface-2);
}
.dept-name {
  display: flex;
  align-items: center;
  gap: 8px;
}
.dept-contact {
  margin-top: 4px;
  font-size: 12px;
  color: var(--ai-ink-2);
  display: flex;
  align-items: center;
  gap: 4px;
}
.dept-contact-dim {
  opacity: 0.7;
}
.skill-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.skill-item {
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}
.skill-item:hover {
  background: var(--ai-surface-2);
}
.skill-line-1 {
  display: flex;
  align-items: center;
  gap: 8px;
}
.skill-dept {
  font-size: 12px;
  color: var(--ai-ink-3);
}
.skill-desc {
  font-size: 12px;
  color: var(--ai-ink-2);
  margin: 4px 0;
}
.skill-stats {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.ds-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.ds-tag {
  cursor: pointer;
}
@media (max-width: 768px) {
  .summary-grid {
    flex-wrap: wrap;
    gap: 16px;
  }
}
</style>
