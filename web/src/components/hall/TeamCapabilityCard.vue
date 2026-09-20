<template>
  <div
    class="team-card-wrap"
    role="button"
    tabindex="0"
    :aria-label="`团队 ${team.department}，${team.skill_count_active || 0} 个活跃 Skill`"
    @keydown.enter.prevent="$emit('view', team)"
    @keydown.space.prevent="$emit('view', team)"
  >
  <a-card class="team-card" hoverable @click="$emit('view', team)">
    <div class="card-top">
      <icon-idcard class="dept-icon" />
      <div class="dept-name">{{ team.department }}</div>
    </div>
    <div class="skill-stats">
      <span class="stat-big">{{ team.skill_count_total }}</span>
      <span class="stat-unit">个 Skill</span>
      <span v-if="team.skill_count_active" class="stat-active">
        （{{ team.skill_count_active }} 活跃）
      </span>
    </div>

    <div v-if="team.top_categories && team.top_categories.length" class="card-categories">
      <a-tag
        v-for="c in team.top_categories"
        :key="c.category"
        size="small"
        color="arcoblue"
      >#{{ c.category }} <span class="cat-cnt">{{ c.count }}</span></a-tag>
    </div>

    <div class="card-meta">
      <span v-if="team.ai_contact" class="meta-item">
        <icon-user /> AI 联系人：{{ team.ai_contact.name || team.ai_contact.user_id }}
        <a-tag size="small" style="margin-left:4px">{{ roleLabel }}</a-tag>
      </span>
      <span v-else class="meta-item meta-dim">
        <icon-user /> 暂无 AI 联系人
      </span>
      <span class="meta-item">
        <icon-storage /> {{ team.data_sources_count || 0 }} 个数据源
      </span>
    </div>
  </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { IconIdcard, IconStorage, IconUser } from '@arco-design/web-vue/es/icon'

type Team = {
  department: string
  skill_count_total: number
  skill_count_active: number
  top_categories: { category: string; count: number }[]
  data_sources_count: number
  ai_contact?: { user_id: string; name?: string; role?: string } | null
}

const props = defineProps<{ team: Team }>()
defineEmits<{ (e: 'view', t: Team): void }>()

const roleLabel = computed(() => {
  const r = props.team.ai_contact?.role || ''
  const map: Record<string, string> = {
    ai_engineer: 'AI 工程师',
    biz_owner: '业务 Owner',
    dept_admin: '部门管理员',
  }
  return map[r] || r || ''
})
</script>

<style scoped>
.team-card-wrap {
  outline: none;
  border-radius: 8px;
}
.team-card-wrap:focus-visible {
  outline: 2px solid var(--ai-accent);
  outline-offset: 2px;
}
.team-card {
  cursor: pointer;
  border-radius: 8px;
  transition: box-shadow 0.2s;
}
.team-card:hover {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
}
.card-top {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.dept-icon {
  font-size: 20px;
  color: var(--ai-accent-ink);
}
.dept-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.skill-stats {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 10px;
}
.stat-big {
  font-size: 24px;
  font-weight: 700;
  color: var(--ai-accent-ink);
}
.stat-unit {
  font-size: 13px;
  color: var(--ai-ink-2);
}
.stat-active {
  font-size: 12px;
  color: var(--ai-ok);
  margin-left: 4px;
}
.card-categories {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 10px;
}
.cat-cnt {
  opacity: 0.7;
  margin-left: 2px;
  font-size: 10px;
}
.card-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.meta-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.meta-dim {
  opacity: 0.6;
}
</style>
