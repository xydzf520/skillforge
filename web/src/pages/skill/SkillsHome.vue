<template>
  <div class="page-container skills-hub">
    <div class="page-header">
        <h2 class="page-title">Skills 工作中心</h2>
    </div>

    <!-- O8: 首屏加载骨架，避免"0 个 Skill"闪烁 -->
    <SfLoadingState v-if="loading" tip="加载工作中心..." height="280px" />

    <div v-else class="hub-grid">
      <!-- 入口 1：浏览列表 -->
      <a-card
        class="hub-card"
        :bordered="false"
        hoverable
        @click="$router.push('/skills')"
      >
        <div class="card-icon-wrap card-icon-list">
          <icon-list class="card-icon" />
        </div>
        <div class="card-body">
          <h3 class="card-title">浏览 Skill 列表</h3>
          <p class="card-desc">查看部门内全部 Skill，进入具体 Skill Studio 进行调优、测试、审核</p>
          <div class="card-meta">
            <span class="meta-item">
              <icon-bookmark />{{ stats.totalSkills }} 个 Skill
            </span>
            <span class="meta-item">
              <icon-clock-circle />最近编辑：{{ stats.lastEditAgo || '—' }}
            </span>
          </div>
        </div>
        <div class="card-action">
          进入列表 <icon-arrow-right />
        </div>
      </a-card>

      <!-- 入口 2：通过对话创建 Skill - W3-D: hover 预取 Studio chunk -->
      <a-card
        v-if="canCreate"
        class="hub-card hub-card-primary"
        :bordered="false"
        hoverable
        @click="$router.push('/skills/new')"
        @mouseenter="prefetchStudio"
      >
        <div class="card-icon-wrap card-icon-create">
          <icon-message class="card-icon" />
        </div>
        <div class="card-body">
          <h3 class="card-title">通过对话创建 Skill</h3>
          <p class="card-desc">4 步采访式引导，从你的业务问题描述出发，依次确认目标、规则、输出、测试，自动生成完整 SKILL.md</p>
          <div class="card-meta">
            <span class="meta-item">
              <icon-thunderbolt />AI 一键生成
            </span>
            <span class="meta-item">
              <icon-message />Agent 对话流
            </span>
          </div>
        </div>
        <div class="card-action">
          开始 4 步对话 <icon-arrow-right />
        </div>
      </a-card>

    </div>

    <!-- 最近访问的 Skill 快捷入口 -->
    <div v-if="recentSkills.length" class="recent-section">
      <div class="recent-header">
        <h3 class="recent-title">最近访问</h3>
        <a class="recent-more" @click="$router.push('/skills')">查看全部 →</a>
      </div>
      <div class="recent-grid">
        <SkillCard
          v-for="s in recentSkills.slice(0, 6)"
          :key="s.id"
          variant="recent"
          :skill="s"
          @click="(sk) => $router.push(`/skills/${sk.id}`)"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import {
  IconList, IconArrowRight, IconBookmark,
  IconThunderbolt, IconMessage,
} from '@arco-design/web-vue/es/icon'
import { useUserStore } from '@/stores/user'
import { skillApi as rawSkillApi } from '@/api'
import { relativeTime } from '@/utils/format'
import SkillCard from '@/components/skills/SkillCard.vue'
import { SfLoadingState } from '@/components/common'

type SkillSummary = {
  id: string
  name?: string
  status?: string
  department?: string
  updated_at?: string
}

const skillApi: any = rawSkillApi
const userStore = useUserStore()

const loading = ref(true)
const stats = ref({ totalSkills: 0, lastEditAgo: '' })
const recentSkills = ref<SkillSummary[]>([])

// v2 角色体系：直接复用 userStore.isEngineer（已覆盖 admin / system_admin / ai_engineer / aibp），
// 避免老白名单漏 system_admin 导致 admin 登录后看不到"对话创建"入口（B1）。
const canCreate = computed(() => userStore.isEngineer)

// W3-D: 用户 hover "通过对话创建 Skill" 卡片时预取 Studio chunk（Monaco + 诸多依赖），
// 减少点击后的白屏等待；已预取就 no-op（import() 内部缓存）
let _studioPrefetched = false
function prefetchStudio() {
  if (_studioPrefetched) return
  _studioPrefetched = true
  import('@/pages/skill/SkillStudio.vue').catch(() => { _studioPrefetched = false })
}

async function loadStats() {
  loading.value = true
  try {
    const res = await skillApi.list({ page: 1, page_size: 6 })
    stats.value.totalSkills = res?.total || 0
    recentSkills.value = res?.items || []
    if (recentSkills.value[0]?.updated_at) {
      stats.value.lastEditAgo = relativeTime(recentSkills.value[0].updated_at) || '—'
    }
  } catch (e) {
    console.warn('[SkillsHome] 加载统计失败:', e)
  } finally {
    loading.value = false
  }
}

onMounted(loadStats)
</script>

<style scoped>
/* 复用全局 .page-container 的 padding/max-width，
   .skills-hub 只补独立的视觉调整 */
.skills-hub {
}

.hub-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
  gap: 20px;
  margin-bottom: 40px;
}

.hub-card {
  position: relative;
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
  border: 1px solid var(--ai-border);
  border-radius: 12px;
  padding: 24px;
  background: var(--ai-surface);
  /* 让卡片纵向 flex，card-body flex:1 把 card-action 推到底部 → 两张卡等高 */
  display: flex;
  flex-direction: column;
  min-height: 240px;
  /* 覆盖全局 .arco-card + .arco-card { margin-top: 16px } 在 grid 布局下不需要 */
  margin: 0 !important;
}
.hub-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
  border-color: rgb(var(--primary-3));
}

.card-icon-wrap {
  width: 56px;
  height: 56px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 16px;
  flex-shrink: 0;
}
.card-icon-list {
  background: rgb(var(--blue-1));
}
.card-icon-list .card-icon {
  color: rgb(var(--blue-6));
}
.card-icon-create {
  background: rgb(var(--primary-1));
}
.card-icon-create .card-icon {
  color: var(--ai-accent-ink);
}
.card-icon {
  font-size: 28px;
}

.card-body {
  margin-bottom: 16px;
  flex: 1;  /* 撑开剩余空间，让 card-action 锚定到卡片底部 */
}
.card-title {
  font-size: var(--sf-text-h2);
  font-weight: 600;
  color: var(--ai-ink-1);
  margin: 0 0 8px;
}
.card-desc {
  font-size: var(--sf-text-body);
  color: var(--ai-ink-3);
  line-height: 1.5;
  margin: 0 0 12px;
}
.card-meta {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.meta-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--sf-text-caption);
  color: var(--ai-ink-3);
}

.card-action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--ai-accent-ink);
  font-size: var(--sf-text-body);
  font-weight: 500;
}

.hub-card-primary {
  background: linear-gradient(135deg, rgb(var(--primary-1)) 0%, var(--ai-surface) 100%);
}

/* 最近访问 */
.recent-section {
  margin-top: 16px;
}
.recent-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 16px;
}
.recent-title {
  font-size: var(--sf-text-h2);
  font-weight: 600;
  color: var(--ai-ink-1);
  margin: 0;
}
.recent-more {
  font-size: var(--sf-text-body);
  color: var(--ai-accent-ink);
  cursor: pointer;
}
.recent-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}
.recent-item {
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  padding: 12px 14px;
  cursor: pointer;
  transition: all 0.15s;
}
.recent-item:hover {
  border-color: rgb(var(--primary-4));
  background: rgb(var(--primary-1));
}
.recent-name {
  font-size: var(--sf-text-body);
  font-weight: 500;
  color: var(--ai-ink-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 6px;
}
.recent-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}
.recent-dept {
  font-size: var(--sf-text-tiny);
  color: var(--ai-ink-3);
}

/* O11: 响应式断点，平板 md ≤ 1024px + 手机 sm ≤ 768px */
@media (max-width: 1024px) {
  .hub-grid {
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
  }
  .recent-grid {
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  }
}
@media (max-width: 768px) {
  .hub-grid {
    grid-template-columns: 1fr;
    gap: 12px;
  }
  .hub-card {
    min-height: 200px;
    padding: 20px;
  }
  .recent-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 480px) {
  .hub-card {
    min-height: 160px;
    padding: 16px;
  }
  .card-icon-wrap {
    width: 44px;
    height: 44px;
    margin-bottom: 12px;
  }
  .card-icon {
    font-size: 22px;
  }
}
</style>
