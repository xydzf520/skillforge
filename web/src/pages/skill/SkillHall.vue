<template>
  <div class="page-container skill-hall">
    <div class="page-header">
      <div class="page-heading">
        <h2 class="page-title">Skill 大厅 · 发现</h2>
        <div class="page-subtitle">跨部门浏览全公司的可复用 AI Skill，按热度/最近更新排序，一键 Fork 到你的部门继续迭代</div>
      </div>
      <div class="hall-stats">
        <div class="stat-item">
          <span class="stat-value">{{ stats.total_visible || 0 }}</span>
          <span class="stat-label">可见 Skill</span>
        </div>
        <div class="stat-item">
          <span class="stat-value">{{ departmentCount }}</span>
          <span class="stat-label">覆盖部门</span>
        </div>
        <div class="stat-item">
          <span class="stat-value">{{ recentlyPublishedCount }}</span>
          <span class="stat-label">本周新增</span>
        </div>
        <!-- N2 + E8 + W1-B: 近 7 天 mini 走势；手机隐藏；全 0 时降级文字避免空折线 -->
        <div v-if="dailyLabels.length && !isMobileView" class="stat-item stat-chart">
          <TrendChart v-if="hasNewActivity" :labels="dailyLabels" :values="dailyValues" unit=" 个" />
          <span v-else class="stat-quiet">本周暂无新增</span>
          <span class="stat-label">7 天新增趋势</span>
        </div>
      </div>
    </div>

    <!-- v2.7 大厅 v3：Tab 切换 Skill / 数据 / 团队 -->
    <a-tabs v-model:active-key="activeTab" type="rounded" @change="onTabChange" class="hall-tabs">
      <a-tab-pane key="skill" title="Skill 能力" />
      <a-tab-pane key="data" title="数据能力" />
      <a-tab-pane key="team" title="团队能力" />
    </a-tabs>

    <HallDataTab v-if="activeTab === 'data'" />
    <HallTeamTab v-else-if="activeTab === 'team'" />

    <a-card v-else class="page-list-card">
      <!-- N5: 批量 Fork 入口 -->
      <div v-if="skills.length" class="hall-actions">
        <a-button size="small" type="outline" :disabled="!skills.length" @click="openBatchFork">
          <template #icon><icon-branch /></template>批量 Fork
        </a-button>
      </div>

      <!-- 筛选栏 -->
      <div class="filter-bar">
        <a-space>
          <a-input-search
            v-model="searchQuery"
            placeholder="搜索 Skill 名称 / 描述"
            allow-clear
            style="width: 240px"
            @search="loadSkills"
            @clear="loadSkills"
          />
          <a-select v-model="filters.department" placeholder="部门" allow-clear style="width: 140px" @change="loadSkills">
            <a-option v-for="d in filterOptions.departments" :key="d.department" :value="d.department">
              {{ d.department }}<template v-if="d.count"> ({{ d.count }})</template>
            </a-option>
          </a-select>
          <a-select v-model="filters.category" placeholder="分类" allow-clear style="width: 140px" @change="loadSkills">
            <a-option v-for="c in filterOptions.categories" :key="c.category" :value="c.category">
              {{ c.category }}<template v-if="c.count"> ({{ c.count }})</template>
            </a-option>
          </a-select>
        </a-space>
        <a-space>
          <a-select v-model="filters.sort_by" style="width: 140px" @change="loadSkills">
            <a-option value="popularity">按热度</a-option>
            <a-option value="updated_at">最近更新</a-option>
            <a-option value="name">按名称</a-option>
            <a-option value="health">按健康度</a-option>
          </a-select>
        </a-space>
      </div>

      <!-- 卡片网格 -->
      <SfLoadingState v-if="loading" tip="加载 Skill 大厅..." height="300px" />
      <template v-else>
        <a-row v-if="skills.length" class="skill-grid" :gutter="[16, 16]">
          <a-col v-for="skill in skills" :key="skill.id" :xs="24" :sm="12" :md="8" :lg="6">
            <SkillCard variant="hall" :skill="skill" @click="goToSkill" @fork="handleFork" />
          </a-col>
        </a-row>
        <SfEmptyState
          v-else
          description="没有找到匹配的 Skill"
          :hint="hasActiveFilter ? '试试清除部分筛选条件' : ''"
          :action-label="hasActiveFilter ? '清除筛选' : ''"
          @action="clearAllFilters"
        />
      </template>

      <!-- 分页 -->
      <div v-if="total > pageSize" class="table-footer">
        <a-pagination
          v-model:current="page"
          :total="total"
          :page-size="pageSize"
          size="small"
          show-total
          @change="loadSkills"
        />
      </div>
    </a-card>

    <!-- Fork 对话框 -->
    <a-modal v-model:visible="forkModalVisible" title="Fork Skill" @ok="doFork" :ok-loading="forking">
      <a-form :model="forkForm" layout="vertical">
        <a-form-item label="新 Skill ID" required>
          <a-input v-model="forkForm.skill_id" :placeholder="forkSkillIdPlaceholder" />
        </a-form-item>
        <a-form-item label="目标部门" required>
          <a-input v-model="forkForm.department" :placeholder="userDepartment || '请输入目标部门'" />
        </a-form-item>
      </a-form>
      <div class="fork-source">
        Fork 来源：<strong>{{ forkSource?.display_name || forkSource?.name || forkSource?.id }}</strong>
        <span class="fork-source-dept">({{ forkSource?.department || '-' }})</span>
      </div>
    </a-modal>

    <!-- N5: 批量 Fork 对话框 -->
    <a-modal
      v-model:visible="batchForkModalVisible"
      title="批量 Fork Skill"
      :ok-loading="batchForking"
      :ok-button-props="{ disabled: !batchSelection.length || !batchDept.trim() }"
      :width="560"
      @ok="doBatchFork"
    >
      <a-form layout="vertical">
        <a-form-item label="目标部门" required>
          <a-input v-model="batchDept" :placeholder="userDepartment || '请输入目标部门'" />
        </a-form-item>
        <a-form-item label="ID 前缀（可选）" help="新 Skill ID = 原 ID + 此前缀；留空则 _<dept> 自动拼接">
          <a-input v-model="batchPrefix" placeholder="如: v2 / fork1" />
        </a-form-item>
        <a-form-item :label="`选择要 Fork 的 Skill（共 ${skills.length} 个可选）`" required>
          <a-checkbox-group v-model="batchSelection">
            <a-space direction="vertical" size="mini">
              <a-checkbox v-for="s in skills" :key="s.id" :value="s.id">
                {{ s.display_name || s.name || s.id }}
                <span class="batch-item-dept">（{{ s.department || '-' }}）</span>
              </a-checkbox>
            </a-space>
          </a-checkbox-group>
        </a-form-item>
        <div v-if="batchResults.length" class="batch-results">
          <div v-for="r in batchResults" :key="r.id" class="batch-result-item" :class="r.status">
            <span>{{ r.id }}</span>
            <span class="batch-result-status">{{ r.status === 'ok' ? '✓' : r.status === 'err' ? '✗ ' + r.message : '…' }}</span>
          </div>
        </div>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { IconFire, IconBranch, IconUser, IconTag } from '@arco-design/web-vue/es/icon'
import { Message } from '@arco-design/web-vue'
import { useUserStore } from '@/stores/user'
import { skillApi as rawSkillApi, skillBatchApi } from '@/api'
import { SfLoadingState, SfEmptyState } from '@/components/common'
import SkillCard from '@/components/skills/SkillCard.vue'
import HallDataTab from '@/pages/hall/HallDataTab.vue'
import HallTeamTab from '@/pages/hall/HallTeamTab.vue'
import { defineAsyncComponent, computed as vueComputed } from 'vue'
import { useResponsive } from '@/composables/useResponsive'
const TrendChart = defineAsyncComponent(() => import('@/components/charts/TrendChart.vue'))

// E8: useResponsive 首个真实调用点；窄屏不渲染 mini chart 省空间
const { width: viewportWidth } = useResponsive()
const isMobileView = vueComputed(() => viewportWidth.value <= 768)
import { riskColor, riskLabel } from '@/utils/constants'

defineOptions({ name: 'SkillHall' })

type HallSkill = {
  id: string
  name?: string
  display_name?: string
  description?: string
  department?: string
  owner?: string
  owner_name?: string
  category?: string
  risk_level?: string
  usage_count?: number
  fork_count?: number
  can_fork?: boolean
  is_member?: boolean
}

const skillApi: any = rawSkillApi
const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

// v2.7 大厅 v3：tab 切换 ?tab=skill|data，默认 skill
const activeTab = ref<string>((route.query.tab as string) || 'skill')
function onTabChange(key: string | number) {
  activeTab.value = String(key)
  router.replace({ query: { ...route.query, tab: key === 'skill' ? undefined : String(key) } })
}
watch(() => route.query.tab, (v) => {
  activeTab.value = (v as string) || 'skill'
})

const loading = ref(false)
const skills = ref<HallSkill[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 12
const searchQuery = ref('')

const stats = ref<any>({})
const filterOptions = ref<any>({ departments: [], categories: [] })

// B5: 优先用后端返的 dept_count（排除"未指定"），缺省时兜底前端计算保持兼容
const departmentCount = computed(() => {
  if (typeof stats.value?.dept_count === 'number') return stats.value.dept_count
  const arr = (stats.value?.by_department || []) as Array<{ department?: string; count?: number }>
  return arr.filter((d) => d?.department && d.department !== '未指定').length
})

// O10: 本周新增优先用后端 weekly_new；兜底 recently_published.length（旧后端兼容）
const recentlyPublishedCount = computed(() => {
  if (typeof stats.value?.weekly_new === 'number') return stats.value.weekly_new
  const arr = (stats.value?.recently_published || []) as any[]
  return Array.isArray(arr) ? arr.length : 0
})

// N2: 近 7 天新增 mini trend chart 数据
const dailyLabels = computed(() => {
  const arr = (stats.value?.daily_new || []) as Array<{ date: string; count: number }>
  return arr.map((d) => d.date)
})
const dailyValues = computed(() => {
  const arr = (stats.value?.daily_new || []) as Array<{ date: string; count: number }>
  return arr.map((d) => d.count || 0)
})
// W1-B: 全 0 时降级成文字
const hasNewActivity = computed(() => dailyValues.value.some((v) => v > 0))

const filters = ref({
  department: undefined as string | undefined,
  category: undefined as string | undefined,
  sort_by: 'popularity',
})

// O7: 空态"清除筛选"按钮
const hasActiveFilter = computed(() => {
  return Boolean(
    searchQuery.value || filters.value.department || filters.value.category,
  )
})
function clearAllFilters() {
  searchQuery.value = ''
  filters.value.department = undefined
  filters.value.category = undefined
  loadSkills()
}

const forkModalVisible = ref(false)
const forking = ref(false)
const forkSource = ref<HallSkill | null>(null)
const forkForm = ref({ skill_id: '', department: '' })
const userDepartment = userStore.department || ''
// v2.6.3: Fork 对话框 skill_id placeholder 根据当前部门动态生成（原硬编码 "EC-ROI-检查-v2" 换企业就过时）
const forkSkillIdPlaceholder = computed(() => {
  const src = forkSource.value?.name || forkSource.value?.id || ''
  const dept = userDepartment.trim() || '部门'
  return src ? `如: ${dept}-${src}-v2` : `如: ${dept}-关键动作-v2`
})

function riskColorValue(level: string) {
  return (riskColor as Record<string, string>)[level] || 'gray'
}

// 风险等级说明：R1 低 / R2 中 / R3 高 / R4 极高，用于 tooltip
const riskDescription: Record<string, string> = {
  R1: '低风险：可自动运行，无需审批',
  R2: '中风险：需本部门负责人确认',
  R3: '高风险：需双人审批',
  R4: '极高风险：涉及对外资金/用户数据，需双人审批+风控介入',
}
function riskTooltip(level: string) {
  const label = (riskLabel as Record<string, string>)[level] || level
  const desc = riskDescription[level] || ''
  return desc ? `${label} · ${desc}` : label
}

function goToSkill(skill: HallSkill) {
  router.push(`/skills/${skill.id}`)
}

function handleFork(skill: HallSkill) {
  forkSource.value = skill
  forkForm.value = {
    skill_id: `${skill.name || skill.id}-fork`,
    department: userDepartment,
  }
  forkModalVisible.value = true
}

async function doFork() {
  if (!forkSource.value) return
  if (!forkForm.value.skill_id || !forkForm.value.department) {
    Message.warning('请填写完整的 Fork 信息')
    return
  }
  forking.value = true
  try {
    await skillApi.forkSkill(forkSource.value.id, {
      new_skill_id: forkForm.value.skill_id,
      department: forkForm.value.department,
    })
    Message.success('Fork 成功')
    forkModalVisible.value = false
    loadSkills()
  } catch (e: any) {
    Message.error(e?.message || 'Fork 失败')
  } finally {
    forking.value = false
  }
}

// N5: 批量 Fork — 前端并发 N 次单 Fork（后端无 batch endpoint，一旦有就直接切过去）
const batchForkModalVisible = ref(false)
const batchForking = ref(false)
const batchDept = ref('')
const batchPrefix = ref('')
const batchSelection = ref<string[]>([])
type BatchResult = { id: string; status: 'pending' | 'ok' | 'err'; message?: string }
const batchResults = ref<BatchResult[]>([])

function openBatchFork() {
  batchDept.value = userDepartment || ''
  batchPrefix.value = ''
  batchSelection.value = []
  batchResults.value = []
  batchForkModalVisible.value = true
}

async function doBatchFork() {
  if (!batchSelection.value.length || !batchDept.value.trim()) return
  batchForking.value = true
  batchResults.value = batchSelection.value.map((id) => ({ id, status: 'pending' }))
  const prefix = batchPrefix.value.trim()
  const suffix = prefix || `_${batchDept.value.trim()}`
  try {
    // v2.2.0 W7b: 改走后端 batch-fork（单条失败不阻断其他，由后端遍历）
    const res: any = await skillBatchApi.batchFork({
      department: batchDept.value.trim(),
      items: batchSelection.value.map((id) => ({
        source_skill_id: id,
        new_skill_id: `${id}${suffix}`,
      })),
    })
    for (const r of (res?.results || [])) {
      const idx = batchSelection.value.indexOf(r.source)
      if (idx >= 0) {
        batchResults.value[idx] = {
          id: r.source,
          status: r.status === 'ok' ? 'ok' : 'err',
          message: r.status === 'ok' ? undefined : (r.message || r.code || 'Fork 失败'),
        }
      }
    }
    const okCount = res?.ok_count ?? 0
    const errCount = res?.err_count ?? 0
    if (errCount === 0) {
      Message.success(`批量 Fork 成功：${okCount} 个`)
      batchForkModalVisible.value = false
      loadSkills()
    } else {
      Message.warning(`批量 Fork 部分成功：${okCount} / ${batchSelection.value.length}，${errCount} 失败（见弹窗底部）`)
      loadSkills()
    }
  } catch (e: any) {
    // 整个请求挂（网络错 / 401 等）— 把所有条目标 err
    batchResults.value = batchSelection.value.map((id) => ({
      id, status: 'err', message: e?._message || e?.message || '批量 Fork 请求失败',
    }))
    Message.error(e?._message || e?.message || '批量 Fork 失败')
  } finally {
    batchForking.value = false
  }
}

async function loadSkills() {
  loading.value = true
  try {
    const params: any = {
      page: page.value,
      page_size: pageSize,
      sort_by: filters.value.sort_by,
      include: 'profile', // v2.7 大厅 v3：附加 adoption_departments / data_sources 画像
    }
    if (searchQuery.value) params.q = searchQuery.value
    if (filters.value.department) params.department = filters.value.department
    if (filters.value.category) params.category = filters.value.category

    const res = await skillApi.hall(params)
    skills.value = res?.items || []
    total.value = res?.total || 0
  } catch (e) {
    console.warn('[SkillHall] 加载 Skill 列表失败:', e)
    skills.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  try {
    const res = await skillApi.hallStats()
    stats.value = res || {}
  } catch (e) {
    console.warn('[SkillHall] 加载统计失败:', e)
  }
}

async function loadFilters() {
  try {
    const res = await skillApi.hallFilters()
    filterOptions.value = {
      departments: res?.departments || [],
      categories: res?.categories || [],
    }
  } catch (e) {
    console.warn('[SkillHall] 加载筛选项失败:', e)
  }
}

onMounted(() => {
  loadSkills()
  loadStats()
  loadFilters()
})
</script>

<style scoped>
/* 顶部紧凑统计，对齐 page-header 右侧 */
.hall-stats {
  display: flex;
  gap: 24px;
  align-items: center;
}
.stat-item {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  line-height: 1.1;
}
.stat-value {
  font-size: var(--sf-text-h2);
  font-weight: 700;
  color: var(--ai-ink-1);
  letter-spacing: -0.01em;
  font-variant-numeric: tabular-nums;
}
.stat-label {
  margin-top: 2px;
  font-size: var(--sf-text-tiny);
  color: var(--ai-ink-3);
}
/* N5: 批量 Fork 入口区 */
.hall-actions {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 8px;
}
.batch-item-dept {
  color: var(--ai-ink-3);
  font-size: 12px;
  margin-left: 4px;
}
.batch-results {
  margin-top: 12px;
  max-height: 180px;
  overflow-y: auto;
  border-top: 1px solid var(--ai-border);
  padding-top: 8px;
}
.batch-result-item {
  display: flex;
  justify-content: space-between;
  padding: 4px 6px;
  font-size: 12px;
  border-radius: var(--sf-radius-xs);
}
.batch-result-item.ok { color: var(--ai-ok); }
.batch-result-item.err { color: var(--ai-bad); }
.batch-result-item.pending { color: var(--ai-ink-3); }

/* N2: mini trend chart 在 stats 最右 */
.stat-chart {
  align-items: stretch;
  min-width: 140px;
}
.stat-chart :deep(.trend-chart-wrap) {
  height: 48px;
  width: 140px;
}
/* W1-B: 空态文字替代折线 */
.stat-quiet {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 48px;
  width: 140px;
  font-size: 12px;
  color: var(--ai-ink-4);
  background: var(--ai-surface-2);
  border-radius: 4px;
  border: 1px dashed var(--ai-border);
  font-style: italic;
}

/* 筛选栏：复用全局 .filter-bar，追加 flex 布局 */
.filter-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.skill-grid :deep(.arco-col) {
  display: flex;
}

/* 卡片视图 —— 对齐 SkillList.vue 的 .skill-card */
.skill-card {
  cursor: pointer;
  transition: transform var(--sf-transition), box-shadow var(--sf-transition);
  height: 100%;
}
.skill-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--sf-shadow-hover) !important;
}
.card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.card-id {
  font-size: var(--sf-text-body);
  font-weight: 600;
  color: var(--ai-ink-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-desc {
  font-size: var(--sf-text-caption);
  color: var(--ai-ink-3);
  line-height: 1.5;
  margin: 6px 0 10px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 36px;
}
.card-meta {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.card-meta-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--sf-text-tiny);
  color: var(--ai-ink-3);
}
.card-footer {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--ai-border);
}
.card-stat {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--sf-text-tiny);
  color: var(--ai-ink-4);
}
.card-spacer {
  flex: 1;
}

/* 分页尾部，对齐 SkillList */
.table-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  padding-top: 14px;
  border-top: 1px solid var(--ai-border);
  margin-top: 12px;
}

/* Fork 弹窗中的来源提示 */
.fork-source {
  margin-top: 12px;
  padding: 8px 12px;
  background: var(--ai-surface-2);
  border-radius: 4px;
  font-size: var(--sf-text-caption);
  color: var(--ai-ink-2);
}
.fork-source-dept {
  margin-left: 6px;
  color: var(--ai-ink-3);
}

/* 响应式断点（O11/E8）：sm ≤ 768px（手机）/ md ≤ 1024px（平板）/ lg > 1024px（桌面） */
@media (max-width: 1024px) {
  .page-header {
    flex-wrap: wrap;
  }
  .hall-stats {
    gap: 20px;
    margin-top: 12px;
  }
}
@media (max-width: 768px) {
  .hall-stats {
    gap: 16px;
    margin-top: 8px;
  }
  .stat-item {
    align-items: flex-start;
  }
}
</style>
