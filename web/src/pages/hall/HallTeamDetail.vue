<template>
  <div class="page-container">
    <HallDetailHeader
      :crumbs="[
        { label: '能力大厅', to: '/hall' },
        { label: '团队', to: '/hall?view=type&tab=team' },
        { label: department },
      ]"
      :title="`${department} · 团队能力`"
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
              <span class="summary-label">数据源</span>
            </div>
            <div class="summary-item">
              <span class="summary-value">{{ detail.summary.member_count }}</span>
              <span class="summary-label">成员</span>
            </div>
          </div>
        </a-card>

        <a-card class="detail-section" title="Skill 列表">
          <a-empty v-if="!detail.skills.length" description="暂无" />
          <div v-else class="skill-list">
            <div
              v-for="s in detail.skills"
              :key="s.id"
              class="skill-item"
              @click="goSkill(router, s.id, s)"
            >
              <div class="skill-line-1">
                <strong>{{ s.name || s.id }}</strong>
                <a-tag v-if="s.risk_level" size="small" :color="(riskColor as Record<string, string>)[s.risk_level] || 'gray'">
                  {{ s.risk_level }}
                </a-tag>
                <a-tag size="small" :color="statusColor(s.status)">{{ statusLabel(s.status) }}</a-tag>
                <span v-if="s.category" class="skill-cat">#{{ s.category }}</span>
              </div>
              <div class="skill-line-2">
                <span v-if="s.description" class="skill-desc">{{ s.description }}</span>
              </div>
              <div class="skill-line-3">
                <span><icon-fire /> {{ s.usage_count || 0 }}</span>
                <span v-if="typeof s.success_rate === 'number'">
                  成功率 {{ Math.round(s.success_rate * 100) }}%
                </span>
                <span v-if="s.last_run_at">最近 {{ relativeTime(s.last_run_at) }}</span>
              </div>
            </div>
          </div>
        </a-card>

        <a-card class="detail-section" title="数据源">
          <a-empty v-if="!detail.data_sources.length" description="暂无" />
          <div v-else class="ds-list">
            <a-tag
              v-for="d in detail.data_sources"
              :key="d.id"
              color="arcoblue"
              size="medium"
              class="ds-tag"
              @click="$router.push(`/hall/data/${d.id}`)"
            >{{ d.name || d.id }} · {{ visLabel(d.visibility) }}</a-tag>
          </div>
        </a-card>

        <a-card class="detail-section" title="成员">
          <a-empty v-if="!detail.members.length" description="暂无" />
          <div v-else class="member-list">
            <div v-for="m in detail.members" :key="m.user_id" class="member-item">
              <icon-user />
              <span class="member-name">{{ m.name || m.user_id }}</span>
              <a-tag size="small">{{ roleLabel(m.role) }}</a-tag>
            </div>
          </div>
        </a-card>
      </div>
    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { IconFire, IconUser } from '@arco-design/web-vue/es/icon'
import { relativeTime } from '@/utils/format'
import { riskColor } from '@/utils/constants'
import { goSkill } from '@/utils/goSkill'
import { hallApi } from '@/api'
import HallDetailHeader from '@/components/hall/HallDetailHeader.vue'

const route = useRoute()
const router = useRouter()
const department = String(route.params.department)
const loading = ref(false)
const detail = ref<any>(null)

async function load() {
  loading.value = true
  try {
    detail.value = await hallApi.teamDetail(department)
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
    admin: '管理员',
    ai_engineer: 'AI 工程师',
    biz_owner: '业务 Owner',
    dept_admin: '部门管理员',
    operator: '运营',
    director: '总监',
    aibp: 'AI BP',
  }
  return m[r] || r
}

onMounted(load)
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
.skill-line-2 {
  font-size: 12px;
  color: var(--ai-ink-2);
  margin: 4px 0;
}
.skill-line-3 {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.skill-cat {
  font-size: 12px;
  color: var(--ai-accent-ink);
}
.ds-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.ds-tag {
  cursor: pointer;
}
.member-list {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.member-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  font-size: 13px;
}
.member-name {
  font-weight: 600;
}
@media (max-width: 768px) {
  .summary-grid {
    flex-wrap: wrap;
    gap: 16px;
  }
}
</style>
