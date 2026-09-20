<template>
  <div class="page-container skill-list-page">
    <!-- 设计稿 design/src/skills.jsx 的 ai-sidebar：Skills / 按部门 / 状态 三组 -->
    <aside class="skill-sidebar ai-sidebar" :class="{ 'skill-sidebar--open': sidebarOpen }">
      <div class="skill-sidebar-head">
        <span class="skill-sidebar-title">Skills</span>
        <button class="skill-sidebar-close" type="button" @click="sidebarOpen = false" aria-label="关闭导航">
          <SfShellIcon name="x" />
        </button>
      </div>
      <div class="ai-side-group">
        <div class="ai-side-label">Skills</div>
        <div
          class="ai-side-item"
          :class="{ active: viewFilter === 'all' && !currentDept && !currentStatus && !currentRisk && !currentTrigger && !searchInput }"
          @click="setViewFilter('all')"
        >
          <SfShellIcon name="cube" class="ic" />
          <span>全部</span>
          <span class="count">{{ statusCounts.all }}</span>
        </div>
        <div
          class="ai-side-item"
          :class="{ active: viewFilter === 'mine_created' }"
          @click="setViewFilter('mine_created')"
        >
          <SfShellIcon name="user" class="ic" />
          <span>我创建</span>
          <span class="count">{{ mineCreatedCount }}</span>
        </div>
        <div
          class="ai-side-item"
          :class="{ active: viewFilter === 'mine_owned' }"
          @click="setViewFilter('mine_owned')"
        >
          <SfShellIcon name="users" class="ic" />
          <span>我负责</span>
          <span class="count">{{ mineOwnedCount }}</span>
        </div>
        <div
          class="ai-side-item"
          :class="{ active: viewFilter === 'favorited' }"
          @click="setViewFilter('favorited')"
        >
          <SfShellIcon name="star" class="ic" />
          <span>收藏</span>
          <span class="count">{{ favoritedCount }}</span>
        </div>
        <div
          class="ai-side-item"
          :class="{ active: viewFilter === 'unhealthy' }"
          @click="setViewFilter('unhealthy')"
        >
          <SfShellIcon name="warn" class="ic warn-ic" />
          <span>不健康</span>
          <span class="count warn-count">{{ unhealthyCount }}</span>
        </div>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">按部门</div>
        <div
          v-for="d in departmentOptions"
          :key="d.name"
          class="ai-side-item"
          :class="{ active: currentDept === d.name }"
          @click="setDept(currentDept === d.name ? undefined : d.name)"
        >
          <SfShellIcon name="dept" class="ic" />
          <span class="ai-side-text">{{ d.name }}</span>
          <span class="count">{{ d.skill_count || 0 }}</span>
        </div>
        <div v-if="!departmentOptions.length" class="ai-side-item disabled">
          <span class="ai-side-text muted-text">暂无部门</span>
        </div>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">状态</div>
        <div
          class="ai-side-item"
          :class="{ active: currentStatus === 'active' }"
          @click="setStatus(currentStatus === 'active' ? undefined : 'active')"
        >
          <span class="deptdot dot-ok" />
          <span>正式运行</span>
          <span class="count">{{ statusCounts.active }}</span>
        </div>
        <div
          class="ai-side-item"
          :class="{ active: currentStatus === 'draft' }"
          @click="setStatus(currentStatus === 'draft' ? undefined : 'draft')"
        >
          <span class="deptdot dot-warn" />
          <span>编辑中</span>
          <span class="count">{{ statusCounts.draft }}</span>
        </div>
        <div
          class="ai-side-item"
          :class="{ active: currentStatus === 'deprecated' }"
          @click="setStatus(currentStatus === 'deprecated' ? undefined : 'deprecated')"
        >
          <span class="deptdot dot-muted" />
          <span>已下线</span>
          <span class="count">{{ statusCounts.deprecated }}</span>
        </div>
      </div>
    </aside>

    <main class="skill-main ai-main">
    <button class="skill-sidebar-toggle" type="button" @click="sidebarOpen = !sidebarOpen" aria-label="打开导航">
      <SfShellIcon name="list" />
      <span>{{ sidebarLabel }}</span>
    </button>
    <div class="ai-pagehead skill-pagehead">
      <div>
        <div class="ai-crumbs">Skills 工作台 · 管理员</div>
        <h1 class="ai-title">Skills</h1>
        <p class="ai-sub">编辑、发布、监控部门 Skill；普通员工请前往「能力大厅」运行</p>
      </div>
      <div class="skill-head-actions">
        <button
          v-if="selectedPublishKeys.length > 0"
          type="button"
          class="ai-btn skill-batch-btn"
          :disabled="batchLoading"
          @click="handleBatchPublish"
        >
          批量发布 ({{ selectedPublishKeys.length }})
        </button>
        <!-- W2-C 批量下线 / 批量 tag -->
        <button
          v-if="selectedDeleteKeys.length > 0"
          type="button"
          class="ai-btn skill-batch-btn danger"
          :disabled="batchLoading"
          @click="handleBatchUnpublish"
        >
          批量下线 ({{ selectedDeleteKeys.length }})
        </button>
        <button
          v-if="selectedEditKeys.length > 0"
          type="button"
          class="ai-btn skill-batch-btn"
          :disabled="batchLoading"
          @click="openBatchTagsModal"
        >
          批量 Tag ({{ selectedEditKeys.length }})
        </button>
        <!-- 设计稿 design/src/skills.jsx：审核中心 按钮 + 待审 pill -->
        <button type="button" class="ai-btn header-review-btn" @click="$router.push('/reviews')">
          <SfShellIcon name="check" />审核中心
          <span v-if="pendingReviewCount > 0" class="ai-pill warn header-review-pill">{{ pendingReviewCount }} 待审</span>
        </button>
        <button v-if="userStore.isEngineer" type="button" class="ai-btn" @click="openImportModal">
          <SfShellIcon name="upload" />导入
        </button>
        <button v-if="userStore.isEngineer" type="button" class="ai-btn primary" @click="$router.push('/skills/new')">
          <SfShellIcon name="plus" />新建 Skill
        </button>
      </div>
    </div>

    <!-- 设计稿 design/src/skills.jsx 的 .ai-tabs 顶部 tab 横条：列表 / 监控 / 审核 / 变更日志 -->
    <nav class="skill-section-tabs ai-tabs" aria-label="Skills 工作区导航">
      <span class="skill-section-tab active" aria-current="page">
        列表 <span class="skill-section-tab-count">{{ statusCounts.all }}</span>
      </span>
      <router-link to="/executions" class="skill-section-tab">
        监控
        <span v-if="unhealthyCount > 0" class="ai-pill warn skill-section-tab-pill">{{ unhealthyCount }}</span>
      </router-link>
      <router-link to="/reviews" class="skill-section-tab">
        审核
        <span v-if="pendingReviewCount > 0" class="ai-pill skill-section-tab-pill">{{ pendingReviewCount }}</span>
      </router-link>
      <router-link to="/changelog" class="skill-section-tab">变更日志</router-link>
      <div class="skill-tab-spacer" />
      <div class="skill-tab-tools">
        <a-auto-complete
          v-model="searchInput"
          :data="searchSuggestionsFiltered"
          placeholder="Skill ID / 名称"
          allow-clear
          :default-active-first-option="false"
          class="skill-search-control"
          style="width: 220px"
          @change="onSearchInput"
          @press-enter="doSearch"
          @clear="doSearch"
          @select="(v: string) => { searchInput = v; doSearch() }"
        />
        <a-select v-model="currentTrigger" placeholder="触发" allow-clear class="skill-filter-trigger" style="width: 110px" @change="setTrigger">
          <a-option value="manual">手动</a-option>
          <a-option value="cron">定时</a-option>
          <a-option value="event">事件</a-option>
        </a-select>
        <a-select v-model="currentRisk" placeholder="风险" allow-clear class="skill-filter-risk" style="width: 100px" @change="setRisk">
          <a-option value="R1">R1</a-option>
          <a-option value="R2">R2</a-option>
          <a-option value="R3">R3</a-option>
          <a-option value="R4">R4</a-option>
        </a-select>
        <a-select v-model="currentSort" class="skill-filter-sort" style="width: 140px" @change="setSort">
          <a-option value="updated_at:desc">最近更新</a-option>
          <a-option value="updated_at:asc">最早更新</a-option>
          <a-option value="name:asc">名称 A→Z</a-option>
          <a-option value="name:desc">名称 Z→A</a-option>
          <a-option value="id:asc">ID A→Z</a-option>
        </a-select>
        <a-radio-group v-model="viewMode" type="button" size="small" class="skill-view-toggle" aria-label="视图切换" @change="saveViewMode">
          <a-radio value="table" aria-label="表格视图"><SfShellIcon name="list" /></a-radio>
          <a-radio value="card" aria-label="卡片视图"><SfShellIcon name="grid" /></a-radio>
        </a-radio-group>
      </div>
    </nav>

    <section class="skill-pagebody ai-pagebody">
      <!-- 置顶 + 最近编辑（折叠区域） -->
      <div v-if="pinnedSkills.length || recentSkills.length" class="pre-list-blocks">
        <div v-if="pinnedSkills.length" class="pinned-section">
          <div class="pinned-header" @click="pinnedCollapsed = !pinnedCollapsed">
            <icon-star-fill class="pinned-icon" />
            <span>置顶</span>
            <span class="pinned-count">{{ pinnedSkills.length }}</span>
            <icon-down class="pinned-toggle-icon" :class="{ 'rotate-180': !pinnedCollapsed }" />
          </div>
          <div v-if="!pinnedCollapsed" class="pinned-list">
            <div v-for="s in pinnedSkills" :key="s.id" class="pinned-item" @click="$router.push(`/skills/${s.id}`)">
              <icon-star-fill class="pin-star active" aria-label="取消置顶" tabindex="0" @click.stop="togglePin(s.id, true)" />
              <span class="pinned-id">{{ s.id }}</span>
              <span class="pinned-name">{{ s.name }}</span>
              <a-tag :color="(skillStatusColor as Record<string, string>)[s.status || '']" size="small">{{ (skillStatusLabel as Record<string, string>)[s.status || ''] }}</a-tag>
              <span class="pinned-dept">{{ s.department || '-' }}</span>
            </div>
          </div>
        </div>

        <div v-if="recentSkills.length" class="recent-section">
          <div class="recent-label">
            <icon-clock-circle />
            <span>最近编辑</span>
          </div>
          <div class="recent-list">
            <div
              v-for="r in recentSkills"
              :key="r.id"
              class="recent-chip"
              @click="$router.push(`/skills/${r.id}`)"
            >
              <span class="recent-chip-id">{{ r.id }}</span>
              <span v-if="r.name && r.name !== r.id" class="recent-chip-name">{{ r.name }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 表格视图 — 列序对齐设计稿 design/src/skills.jsx：
           checkbox · Skill · 状态 · 风险/版本 · 负责人/部门 · 触发 · 今日使用 · 最近运行 · 健康度 · 操作 -->
      <a-spin :loading="loading" v-if="viewMode === 'table'">
        <a-table class="page-list-table sf-skill-table" :data="displayedSkills" :pagination="false" row-key="id" v-model:selectedKeys="selectedKeys"
          :row-selection="{ type: 'checkbox', showCheckedAll: true }" @row-click="onRowClick">
          <template #columns>
            <a-table-column title="Skill">
              <template #cell="{ record }">
                <div class="skill-cell">
                  <div class="skill-cell-row">
                    <icon-star-fill v-if="pinnedIds.has(record.id)" class="pin-star active inline-pin" aria-label="取消置顶" tabindex="0" @click.stop="togglePin(record.id, true)" />
                    <icon-star v-else class="pin-star inline-pin" aria-label="置顶 Skill" tabindex="0" @click.stop="togglePin(record.id, false)" />
                    <span class="skill-cell-name">{{ record.name || record.id }}</span>
                  </div>
                  <div class="skill-cell-id mono-id">{{ record.id }}</div>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="状态" :width="90">
              <template #cell="{ record }">
                <span class="ai-pill dot" :class="aiPillClassForStatus(record.status)">
                  {{ (skillStatusLabel as Record<string, string>)[record.status] || record.status || '-' }}
                </span>
              </template>
            </a-table-column>
            <a-table-column title="风险 / 版本" :width="110">
              <template #cell="{ record }">
                <div class="risk-version-cell">
                  <a-tooltip :content="riskTooltip(record.risk_level)">
                    <span class="ai-pill risk-pill" :class="aiPillClassForRisk(record.risk_level)">{{ record.risk_level || '-' }}</span>
                  </a-tooltip>
                  <span class="version-text mono">{{ record.current_version || 'v0.0' }}</span>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="负责人 / 部门" :width="140">
              <template #cell="{ record }">
                <div class="owner-cell">
                  <div class="owner-name">{{ record.owner_name || record.owner || '—' }}</div>
                  <div class="owner-dept tiny muted">{{ record.department || '—' }}</div>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="触发" :width="100">
              <template #cell="{ record }">
                <span class="ai-pill trigger-pill">{{ triggerLabel(record.trigger_type) }}</span>
              </template>
            </a-table-column>
            <a-table-column title="今日使用" :width="120">
              <template #cell="{ record }">
                <div class="uses-cell">
                  <span class="uses-num mono">{{ record.usage_today ?? 0 }}</span>
                  <span class="ai-spark" aria-hidden="true">
                    <i v-for="(h, i) in usageSpark(record)" :key="i" :style="{ height: (2 + h * 1.2) + 'px' }" />
                  </span>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="最近运行" :width="110">
              <template #cell="{ record }">
                <a-tooltip v-if="record.last_run_at" :content="formatTime(record.last_run_at)" mini>
                  <span class="tiny muted">{{ relativeTime(record.last_run_at) }}</span>
                </a-tooltip>
                <a-tooltip v-else :content="record.updated_at ? `暂无运行记录，最近更新：${formatTime(record.updated_at)}` : '暂无运行记录'" mini>
                  <span class="tiny muted">{{ record.updated_at ? relativeTime(record.updated_at) : '—' }}</span>
                </a-tooltip>
              </template>
            </a-table-column>
            <a-table-column title="健康度" :width="130">
              <template #cell="{ record }">
                <HealthBar v-if="record.health_score != null" :value="record.health_score" />
                <span v-else class="health-empty tiny muted">—</span>
              </template>
            </a-table-column>
            <a-table-column title="操作" :width="hasAnyDeletePermission ? 130 : 80" align="right">
              <template #cell="{ record }">
                <span class="row-action" @click.stop>
                  <a-button type="text" size="small" @click.stop="handleExportSkill(record)">
                    <template #icon><icon-download /></template>导出
                  </a-button>
                  <a-button v-if="canDeleteSkill(record)" type="text" size="small" status="danger" @click.stop="handleDeleteSkill(record)" aria-label="删除 Skill">
                    <icon-delete />
                  </a-button>
                </span>
              </template>
            </a-table-column>
          </template>
        </a-table>
        <div class="table-footer">
          <a-pagination v-if="total > 0" :current="currentPage" :page-size="pageSize" :total="total" size="small"
            show-total @change="setPage" />
        </div>
      </a-spin>

      <!-- 卡片视图 -->
      <div v-if="viewMode === 'card'" class="skill-card-view">
        <SfLoadingState v-if="loading" tip="加载 Skill 列表..." height="300px" />
        <template v-else>
          <a-row v-if="displayedSkills.length" :gutter="[16, 16]">
            <a-col v-for="skill in displayedSkills" :key="skill.id" :xs="24" :sm="12" :md="8" :lg="6">
              <SkillListCard
                :skill="skill"
                :show-delete="canDeleteSkill(skill)"
                @click="(s) => $router.push(`/skills/${s.id}`)"
                @delete="handleDeleteSkill"
              />
            </a-col>
          </a-row>
          <SfEmptyState
            v-else
            description="暂无 Skill"
            :hint="hasActiveFilter ? '试试清除部分筛选条件' : ''"
            :action-label="hasActiveFilter ? '清除筛选' : ''"
            @action="clearAllFilters"
          >
            <!-- N4: 空搜索时给 3 个"最近访问"候选，降低"搜什么都没"的挫败感 -->
            <template v-if="hasActiveFilter && searchSuggestions.length" #action>
              <a-space direction="vertical" align="center" size="small">
                <a-button type="primary" size="small" @click="clearAllFilters">清除筛选</a-button>
                <div class="empty-suggest">
                  <span class="suggest-label">或试试：</span>
                  <a-tag
                    v-for="s in searchSuggestions"
                    :key="s.id"
                    size="small"
                    class="suggest-chip"
                    @click="applySuggestion(s)"
                  >{{ s.name || s.id }}</a-tag>
                </div>
              </a-space>
            </template>
          </SfEmptyState>
        </template>
        <div class="table-footer">
          <a-pagination v-if="total > pageSize" :current="currentPage" :page-size="pageSize" :total="total" size="small"
            show-total @change="setPage" />
        </div>
      </div>
    </section>
    </main>

    <a-modal
      :visible="importModalVisible"
      title="导入 Skill 包"
      :mask-closable="false"
      @cancel="closeImportModal"
    >
      <a-alert class="skill-import-alert" type="info">
        直接从 zip 包导入 Skill。导入时可以重新指定目标部门、Skill ID 和版本，落地后默认是 `draft`。
      </a-alert>

      <a-form :model="importForm" layout="vertical">
        <a-form-item label="Zip 包" required>
          <a-upload
            :auto-upload="false"
            accept=".zip"
            :file-list="[]"
            @change="handleImportFileSelect"
          >
            <template #upload-button>
              <a-button class="skill-import-picker" type="outline">
                <template #icon><icon-upload /></template>
                {{ importForm.file ? '重新选择 zip' : '选择 zip 包' }}
              </a-button>
            </template>
          </a-upload>
          <div v-if="importForm.file" class="skill-import-file">
            <a-tag color="arcoblue">{{ importForm.file.name }}</a-tag>
            <span class="skill-import-file-meta">{{ formatImportFileSize(importForm.file.size) }}</span>
          </div>
        </a-form-item>

        <a-form-item label="目标部门" required>
          <a-select
            v-model="importForm.department"
            placeholder="选择导入部门"
            allow-search
            allow-clear
          >
            <a-option v-for="d in importDepartmentOptions" :key="d.name" :value="d.name">
              {{ d.name }}<template v-if="d.skill_count"> ({{ d.skill_count }})</template>
            </a-option>
          </a-select>
        </a-form-item>

        <a-form-item label="导入版本">
          <a-input
            v-model="importForm.currentVersion"
            placeholder="例如 v1.0；不填则沿用包内版本"
            allow-clear
          />
          <div class="skill-import-version-tip">
            {{ importVersionError || '版本会写入 Skill 当前版本，用于列表展示和后续发布起点。' }}
          </div>
        </a-form-item>

        <a-form-item label="目标 Skill ID">
          <a-input
            v-model="importForm.newSkillId"
            placeholder="不填则沿用包内 Skill ID"
            allow-clear
          />
        </a-form-item>
      </a-form>

      <template #footer>
        <a-space>
          <a-button @click="closeImportModal">取消</a-button>
          <a-button type="primary" :loading="importLoading" :disabled="importSubmitDisabled" @click="handleImportSkill">
            开始导入
          </a-button>
        </a-space>
      </template>
    </a-modal>

    <!-- W2-C 批量 tag 弹框 -->
    <a-modal
      v-model:visible="batchTagsModalVisible"
      title="批量设置 Tag"
      :ok-loading="batchLoading"
      :ok-button-props="{ disabled: !batchTagsInput.trim() }"
      @ok="handleBatchSetTags"
    >
      <p class="batch-tags-tip">
        即将为 <strong>{{ selectedKeys.length }}</strong> 个 Skill 替换为以下 tag（会先清空旧 tag 再写入）：
      </p>
      <a-input v-model="batchTagsInput" placeholder="多个 tag 用逗号或空格分隔，例如：自动化, 电商, 周报" />
      <div v-if="batchTagsInput.trim()" class="batch-tags-preview">
        预览：
        <a-tag v-for="t in batchTagsInput.split(/[,，\s]+/).filter(Boolean)" :key="t" size="small" class="batch-tags-chip">{{ t }}</a-tag>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { skillApi as rawSkillApi, skillBatchApi, orgApi, reviewApi as rawReviewApi } from '@/api'
import { useUserStore } from '@/stores/user'
import {
  IconStar, IconStarFill, IconDown, IconDelete, IconClockCircle, IconUpload, IconDownload,
} from '@arco-design/web-vue/es/icon'
import { formatTime, relativeTime } from '@/utils/format'
import { skillStatusLabel, skillStatusColor, riskLabel } from '@/utils/constants'
import { confirmAction, confirmDelete } from '@/utils/confirmDelete'

const RISK_DESC: Record<string, string> = {
  R1: '低风险：自动运行，无需审批',
  R2: '中风险：本部门负责人确认',
  R3: '高风险：双人审批',
  R4: '极高风险：双人审批 + 风控介入',
}
function riskTooltip(level?: string): string {
  if (!level) return '未分类'
  const lbl = (riskLabel as Record<string, string>)[level] || level
  const desc = RISK_DESC[level] || ''
  return desc ? `${lbl} · ${desc}` : lbl
}
import { undoableAction } from '@/utils/undo'
import { SfLoadingState, SfEmptyState } from '@/components/common'
import SkillListCard from './SkillListCard.vue'
import HealthBar from '@/components/skills/HealthBar.vue'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'
import { useSkillStudioRecent } from '@/composables/useSkillStudioRecent'

type SkillListItem = {
  id: string
  name?: string
  status?: string
  department?: string
  trigger_type?: string
  risk_level?: string
  current_version?: string
  git_commit?: string
  updated_at?: string
  usage_count?: number
  usage_today?: number
  usage_trend?: Array<number | { date?: string; day?: string; count?: number; usage_count?: number }>
  last_run_at?: string | null
  owner?: string
  owner_name?: string
  health_score?: number | null
  permissions?: Record<string, boolean>
}

type DepartmentOption = {
  name: string
  skill_count: number
}

const VERSION_PATTERN = /^v?\d+(?:\.\d+){0,2}$/

const route: any = useRoute()
const router: any = useRouter()
const userStore = useUserStore()
const skillApi: any = rawSkillApi
const reviewApi: any = rawReviewApi

// O12: 按用户隔离"最近编辑"列表；key 由 useSkillStudioRecent composable 托管（O13）
const recentStore = useSkillStudioRecent({ userStore })
const RECENT_KEY = computed(() => recentStore.key())
// O9: 不再硬编码部门兜底（原来 [EC / 传统电商 / 即时零售] 换企业就过时）；
// 加载失败时 departmentOptions 为空数组 + 顶部 Message.warning 提示，让用户知道下拉暂不可用
const DEFAULT_DEPARTMENTS: DepartmentOption[] = []

const loading = ref(false)
const skills = ref<SkillListItem[]>([])
const total = ref(0)
const pageSize = 50
const statusCounts = ref({ all: 0, active: 0, shadow: 0, draft: 0, deprecated: 0 })
const viewCounts = ref({ mine_created: 0, mine_owned: 0, favorited: 0, unhealthy: 0 })
const viewMode = ref(localStorage.getItem('sf-skill-view') || 'table')
// 左侧 sidebar 的"视图"维度过滤：all / mine_created / mine_owned / favorited / unhealthy
type SidebarViewFilter = 'all' | 'mine_created' | 'mine_owned' | 'favorited' | 'unhealthy'
function normalizeViewFilter(value: unknown): SidebarViewFilter {
  return ['mine_created', 'mine_owned', 'favorited', 'unhealthy'].includes(String(value))
    ? (String(value) as SidebarViewFilter)
    : 'all'
}
const viewFilter = ref<SidebarViewFilter>(normalizeViewFilter(route.query.view_filter))
// 移动端 sidebar 抽屉开关
const sidebarOpen = ref(false)
const pinnedSkills = ref<SkillListItem[]>([])
const pinnedIds = ref<Set<string>>(new Set())
const pinnedCollapsed = ref(false)
const selectedKeys = ref<string[]>([])
const batchLoading = ref(false)
const recentSkills = ref<Array<{ id: string; name?: string; visited_at?: number }>>([])
const departmentOptions = ref<DepartmentOption[]>(DEFAULT_DEPARTMENTS)
const importModalVisible = ref(false)
const importLoading = ref(false)
const importForm = reactive({
  file: null as File | null,
  department: '',
  currentVersion: '',
  newSkillId: '',
})

function skillPermission(skill: SkillListItem | undefined, action: string): boolean {
  return Boolean(skill?.permissions?.[action])
}

function canDeleteSkill(skill: SkillListItem | undefined): boolean {
  return skillPermission(skill, 'delete')
}

function canPublishSkill(skill: SkillListItem | undefined): boolean {
  return skillPermission(skill, 'publish')
}

function canEditSkill(skill: SkillListItem | undefined): boolean {
  return skillPermission(skill, 'edit')
}

const selectedSkills = computed(() => {
  const selected = new Set(selectedKeys.value)
  return skills.value.filter((s) => selected.has(s.id))
})
const selectedPublishKeys = computed(() => selectedSkills.value.filter(canPublishSkill).map((s) => s.id))
const selectedDeleteKeys = computed(() => selectedSkills.value.filter(canDeleteSkill).map((s) => s.id))
const selectedEditKeys = computed(() => selectedSkills.value.filter(canEditSkill).map((s) => s.id))
const hasAnyDeletePermission = computed(() => skills.value.some(canDeleteSkill))

// 待审核 Skill 数量（顶部 审核中心 按钮 + 4-tab 中的审核 tab 显示）
const pendingReviewCount = ref(0)

// 组织架构部门名称列表（用于导入弹窗下拉）
const orgUnitNames = ref<string[]>([])

async function loadOrgUnits() {
  try {
    const tree = await orgApi.getTree()
    const names: string[] = []
    function walk(nodes: any[]) {
      for (const n of nodes) {
        if (n.name) names.push(n.name)
        if (n.children?.length) walk(n.children)
      }
    }
    walk(Array.isArray(tree) ? tree : [])
    orgUnitNames.value = names
  } catch { /* ignore */ }
}

// 从 URL query 读状态
const currentPage = computed(() => parseInt(route.query.page) || 1)
const currentStatus = computed(() => route.query.status || undefined)
const currentDept = computed({ get: () => route.query.department || undefined, set: () => {} })
const currentRisk = computed({ get: () => route.query.risk_level || undefined, set: () => {} })
const currentTrigger = computed({ get: () => route.query.trigger_type || undefined, set: () => {} })
const currentSort = computed({
  get: () => `${route.query.sort_by || 'updated_at'}:${route.query.sort_order || 'desc'}`,
  set: () => {},
})
const searchInput = ref(route.query.q || '')
const importDepartmentOptions = computed<DepartmentOption[]>(() => {
  const map = new Map<string, DepartmentOption>()
  // 优先用组织架构部门列表
  for (const name of orgUnitNames.value) {
    if (!name) continue
    map.set(name, { name, skill_count: 0 })
  }
  // 补充已有 Skill 的部门（带数量）
  for (const item of departmentOptions.value) {
    const name = String(item?.name || '').trim()
    if (!name) continue
    if (map.has(name)) {
      map.set(name, { name, skill_count: Number(item.skill_count || 0) })
    } else {
      map.set(name, { name, skill_count: Number(item.skill_count || 0) })
    }
  }
  for (const fallback of [String(currentDept.value || '').trim(), String(userStore.department || '').trim()]) {
    if (fallback && !map.has(fallback)) map.set(fallback, { name: fallback, skill_count: 0 })
  }
  return Array.from(map.values()).sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'))
})
const normalizedImportVersion = computed(() => {
  const raw = importForm.currentVersion.trim()
  if (!raw) return ''
  if (raw.startsWith('v') || raw.startsWith('V')) return `v${raw.slice(1)}`
  return /^\d/.test(raw) ? `v${raw}` : raw
})
const importVersionError = computed(() => {
  if (!normalizedImportVersion.value) return ''
  return VERSION_PATTERN.test(normalizedImportVersion.value) ? '' : '版本格式示例：v1.0 / v1.2.3'
})
const importSubmitDisabled = computed(() => {
  return !importForm.file || !importForm.department.trim() || Boolean(importVersionError.value) || importLoading.value
})

// 移动端 toggle 按钮上展示的当前视图标签
const sidebarLabel = computed(() => {
  switch (viewFilter.value) {
    case 'mine_created':
      return '我创建'
    case 'mine_owned':
      return '我负责'
    case 'favorited':
      return '收藏'
    case 'unhealthy':
      return '不健康'
    default: {
      if (currentDept.value) return String(currentDept.value)
      if (currentStatus.value === 'active') return '正式运行'
      if (currentStatus.value === 'draft') return '编辑中'
      if (currentStatus.value === 'deprecated') return '已下线'
      return '全部'
    }
  }
})

// 更新 URL query
function updateQuery(params: Record<string, string | number | undefined | null>) {
  const q = { ...route.query }
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === '' || v === null) delete q[k]
    else q[k] = String(v)
  }
  if (params.page === undefined && !('page' in params)) q.page = '1'
  router.replace({ query: q })
}

// 设计稿表格 cell 辅助：把 Skill 状态映射到 ai-pill 配色（ok / warn / accent / bad）
function aiPillClassForStatus(status?: string): string {
  switch (status) {
    case 'active': return 'ok'
    case 'shadow': return 'info'
    case 'draft': return 'warn'
    case 'deprecated': return 'bad'
    default: return ''
  }
}
// 风险等级 → ai-pill 配色
function aiPillClassForRisk(risk?: string): string {
  switch (risk) {
    case 'R1': return 'ok'
    case 'R2': return 'info'
    case 'R3': return 'warn'
    case 'R4': return 'bad'
    default: return ''
  }
}
const TRIGGER_LABEL_MAP: Record<string, string> = { cron: '定时 cron', manual: '手动', event: '事件' }
function triggerLabel(t?: string): string {
  if (!t) return '手动'
  return TRIGGER_LABEL_MAP[t] || t
}
function trendPointValue(point: number | { count?: number; usage_count?: number } | undefined): number {
  if (typeof point === 'number') return Number.isFinite(point) ? point : 0
  if (!point || typeof point !== 'object') return 0
  const value = point.count ?? point.usage_count ?? 0
  return Number.isFinite(Number(value)) ? Number(value) : 0
}

function usageSpark(skill: SkillListItem): number[] {
  const rawTrend = Array.isArray(skill.usage_trend) ? skill.usage_trend.slice(-7) : []
  const points = rawTrend.map(trendPointValue)
  while (points.length < 7) points.unshift(0)
  if (!rawTrend.length && skill.usage_count) points[6] = Math.max(0, Number(skill.usage_count || 0))
  const max = Math.max(...points, 1)
  return points.map(value => Math.max(0, Math.min(9, Math.round((value / max) * 9))))
}

function setStatus(s?: string) { updateQuery({ status: s, page: 1 }) }
function setDept(d?: string) { updateQuery({ department: d, page: 1 }) }
function setRisk(r?: string) { updateQuery({ risk_level: r, page: 1 }) }
function setTrigger(t?: string) { updateQuery({ trigger_type: t, page: 1 }) }
function setPage(p: number) { updateQuery({ page: p }) }
function setSort(v: string) {
  const [by, order] = v.split(':')
  updateQuery({ sort_by: by, sort_order: order, page: 1 })
}
function doSearch() { updateQuery({ q: searchInput.value || undefined, page: 1 }) }

// O7: 空态"清除筛选"按钮
const hasActiveFilter = computed(() => {
  return Boolean(
    currentStatus.value || currentDept.value || currentRisk.value ||
    currentTrigger.value || searchInput.value || viewFilter.value !== 'all',
  )
})
function clearAllFilters() {
  searchInput.value = ''
  viewFilter.value = 'all'
  updateQuery({
    status: undefined,
    department: undefined,
    risk_level: undefined,
    trigger_type: undefined,
    q: undefined,
    view_filter: undefined,
    page: 1,
  })
}

// 左侧 sidebar：视图维度切换，走后端分页筛选，避免只过滤当前页。
function setViewFilter(v: SidebarViewFilter) {
  if (v === 'all') {
    // 同时清掉 URL 上的筛选，回到完全初始态
    clearAllFilters()
    viewFilter.value = 'all'
    sidebarOpen.value = false
    return
  }
  viewFilter.value = v
  updateQuery({ view_filter: v, page: 1 })
  sidebarOpen.value = false
}

const displayedSkills = computed<SkillListItem[]>(() => {
  return skills.value
})

const mineCreatedCount = computed(
  () => viewCounts.value.mine_created,
)
const mineOwnedCount = computed(
  () => viewCounts.value.mine_owned,
)
const favoritedCount = computed(() => viewCounts.value.favorited || pinnedSkills.value.length)
const unhealthyCount = computed(
  () => viewCounts.value.unhealthy,
)

// N4: 空态建议词（取最近访问的 3 条）
const searchSuggestions = computed(() => {
  const arr = recentSkills.value || []
  return arr.slice(0, 3)
})
function applySuggestion(s: { id: string; name?: string }) {
  searchInput.value = s.name || s.id
  updateQuery({ q: s.name || s.id, page: 1 })
}

// W2-D: autocomplete 候选池 = 当前 skills + pinned + recent 的名称和 id 合集，最多 8 条
const searchSuggestionsFiltered = computed<string[]>(() => {
  const q = (searchInput.value || '').toLowerCase().trim()
  const pool = new Set<string>()
  for (const s of skills.value) {
    if (s.name) pool.add(s.name)
    if (s.id) pool.add(s.id)
  }
  for (const s of pinnedSkills.value) {
    if (s.name) pool.add(s.name)
    if (s.id) pool.add(s.id)
  }
  for (const s of recentSkills.value) {
    if (s.name) pool.add(s.name)
    if (s.id) pool.add(s.id)
  }
  const all = Array.from(pool)
  if (!q) return all.slice(0, 8)
  return all.filter((v) => v.toLowerCase().includes(q)).slice(0, 8)
})

let searchTimer: ReturnType<typeof setTimeout> | null = null
function onSearchInput() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(doSearch, 300)
}

function saveViewMode(v: string) {
  localStorage.setItem('sf-skill-view', v)
}

function onRowClick(record: SkillListItem) {
  router.push(`/skills/${record.id}`)
}

function preferredImportDepartment(): string {
  return String(currentDept.value || '').trim()
    || String(userStore.department || '').trim()
    || String(importDepartmentOptions.value[0]?.name || '').trim()
}

function openImportModal() {
  importModalVisible.value = true
  importForm.department = preferredImportDepartment()
}

function resetImportForm() {
  importForm.file = null
  importForm.department = preferredImportDepartment()
  importForm.currentVersion = ''
  importForm.newSkillId = ''
}

function closeImportModal() {
  importModalVisible.value = false
  if (!importLoading.value) resetImportForm()
}

function handleImportFileSelect(fileList: unknown, fileItem: any) {
  const file = fileItem?.file as File | undefined
  importForm.file = file || null
}

function formatImportFileSize(size = 0): string {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${size} B`
}

function normalizeExportBlob(payload: unknown): Blob {
  const candidate = (payload as { data?: unknown } | null)?.data ?? payload
  if (candidate instanceof Blob) return candidate
  if (candidate instanceof ArrayBuffer) return new Blob([candidate], { type: 'application/zip' })
  throw new TypeError('导出响应不是有效的文件数据')
}

function skillArchiveName(skill: Pick<SkillListItem, 'id' | 'current_version'>): string {
  const version = String(skill.current_version || '').trim()
  return version ? `${skill.id}-${version}.zip` : `${skill.id}.zip`
}

async function handleExportSkill(skill: SkillListItem) {
  try {
    const blob = normalizeExportBlob(await skillBatchApi.exportSkill(skill.id))
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = skillArchiveName(skill)
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    Message.success(`已下载：${skillArchiveName(skill)}`)
  } catch (e) {
    Message.error(String((e as Record<string, unknown>)?._message || '导出失败'))
  }
}

async function handleImportSkill() {
  if (!importForm.file) {
    Message.warning('请先选择 zip 包')
    return
  }
  if (!importForm.department.trim()) {
    Message.warning('请选择导入部门')
    return
  }
  if (importVersionError.value) {
    Message.warning(importVersionError.value)
    return
  }

  importLoading.value = true
  try {
    const res: any = await skillBatchApi.importSkill(importForm.file, {
      department: importForm.department.trim(),
      newSkillId: importForm.newSkillId.trim() || undefined,
      currentVersion: normalizedImportVersion.value || undefined,
    })
    Message.success(`导入成功：${res.skill_id}${res.current_version ? ` · ${res.current_version}` : ''}`)
    importModalVisible.value = false
    resetImportForm()
    await Promise.all([loadSkills(), loadPinned()])
  } catch (e: any) {
    const baseMsg = String(e?._message || '导入失败')
    const detail = e?._detail
    const detailParts: string[] = []
    if (detail?.errors?.length) detailParts.push(...detail.errors)
    if (detail?.warnings?.length) detailParts.push(...detail.warnings)
    if (detailParts.length) {
      Message.error(`${baseMsg}：${detailParts.join('；')}`)
    } else {
      Message.error(baseMsg)
    }
  } finally {
    importLoading.value = false
  }
}

// 加载数据
async function loadSkills() {
  loading.value = true
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize,
      status: route.query.status || undefined,
      department: route.query.department || undefined,
      q: route.query.q || undefined,
      risk_level: route.query.risk_level || undefined,
      trigger_type: route.query.trigger_type || undefined,
      sort_by: route.query.sort_by || 'updated_at',
      sort_order: route.query.sort_order || 'desc',
      include_health: true,
      view_filter: viewFilter.value === 'all' ? undefined : viewFilter.value,
    }
    const res = await skillApi.list(params)
    skills.value = res.items || []
    total.value = res.total || 0
    statusCounts.value = res.status_counts || { all: 0, active: 0, shadow: 0, draft: 0, deprecated: 0 }
    viewCounts.value = {
      mine_created: Number(res.view_counts?.mine_created || 0),
      mine_owned: Number(res.view_counts?.mine_owned || 0),
      favorited: Number(res.view_counts?.favorited || 0),
      unhealthy: Number(res.view_counts?.unhealthy || 0),
    }
  } catch (e) {
    Message.error(String((e as Record<string, unknown>)?._message || '加载 Skill 列表失败'))
  } finally {
    loading.value = false
  }
}

// 部门列表（从后端拉；失败时空列表 + 弹一次 warning，不再硬编码兜底）
let deptLoadWarned = false
async function loadDepartments() {
  try {
    const r = await skillApi.listDepartments()
    const list = r?.departments || []
    departmentOptions.value = Array.isArray(list) ? list : []
  } catch (e) {
    departmentOptions.value = []
    if (!deptLoadWarned) {
      deptLoadWarned = true
      Message.warning('部门列表加载失败，筛选下拉暂不可用')
    }
  }
}

// 最近编辑：从 localStorage 读取
function loadRecent() {
  try {
    const raw = localStorage.getItem(RECENT_KEY.value)
    if (!raw) {
      recentSkills.value = []
      return
    }
    const list = JSON.parse(raw)
    if (!Array.isArray(list)) {
      recentSkills.value = []
      return
    }
    // 按 visited_at 降序 + 去重 + 限制 10 条
    const seen = new Set()
    const cleaned = []
    for (const item of list.sort((a, b) => (b.visited_at || 0) - (a.visited_at || 0))) {
      if (!item?.id || seen.has(item.id)) continue
      seen.add(item.id)
      cleaned.push(item)
      if (cleaned.length >= 10) break
    }
    recentSkills.value = cleaned
  } catch {
    recentSkills.value = []
  }
}

// 待审核计数（顶部 审核中心 按钮 + 4-tab）
// 失败时静默清零，避免阻塞主列表加载
async function loadPendingReviewCount() {
  try {
    const res: any = await reviewApi.list({ status: 'pending', page_size: 1 })
    pendingReviewCount.value = Number(res?.total || 0)
  } catch {
    pendingReviewCount.value = 0
  }
}

// 置顶
async function loadPinned() {
  try {
    const list = await skillApi.pinned()
    pinnedSkills.value = list
    pinnedIds.value = new Set(list.map((s: Record<string, unknown>) => s.id))
  } catch { /* ignore */ }
}

async function togglePin(skillId: string, isPinned: boolean) {
  try {
    if (isPinned) {
      await skillApi.unpin(skillId)
    } else {
      await skillApi.pin(skillId)
    }
    await loadPinned()
  } catch (e) {
    Message.error(String((e as Record<string, unknown>)?._message || '置顶操作失败'))
  }
}

// 批量发布
async function handleBatchPublish() {
  const ids = selectedPublishKeys.value
  if (!ids.length) return
  batchLoading.value = true
  try {
    const res = await skillApi.batchPublish(ids)
    Message.success(`批量发布完成: ${res.success}成功, ${res.failed}失败`)
    selectedKeys.value = []
    loadSkills()
  } catch (e) { Message.error(String((e as Record<string, unknown>)?._message || '批量发布失败')) }
  finally { batchLoading.value = false }
}

// W2-C 批量下线（active → deprecated）
async function handleBatchUnpublish() {
  const ids = selectedDeleteKeys.value
  if (!ids.length) return
  confirmAction({
    title: '批量下线？',
    content: `将把 ${ids.length} 个 Skill 设为 deprecated，非 active 的会跳过。`,
    okText: '确认下线',
    okStatus: 'danger',
    onOk: async () => {
      batchLoading.value = true
      try {
        const res: any = await skillBatchApi.batchUnpublish(ids)
        Message.success(`批量下线完成：${res.ok_count} 成功，${res.skipped_count} 跳过，${res.err_count} 失败`)
        selectedKeys.value = []
        loadSkills()
      } catch (e) { Message.error(String((e as Record<string, unknown>)?._message || '批量下线失败')) }
      finally { batchLoading.value = false }
    },
  })
}

// W2-C 批量 tag
const batchTagsModalVisible = ref(false)
const batchTagsInput = ref('')
function openBatchTagsModal() {
  batchTagsInput.value = ''
  batchTagsModalVisible.value = true
}
async function handleBatchSetTags() {
  const ids = selectedEditKeys.value
  if (!ids.length || !batchTagsInput.value.trim()) return
  const tags = batchTagsInput.value.split(/[,，\s]+/).filter(Boolean)
  batchLoading.value = true
  try {
    const res: any = await skillBatchApi.batchSetTags(
      ids.map((id) => ({ skill_id: id, tags })),
    )
    Message.success(`批量 tag 完成：${res.ok_count} 成功，${res.err_count} 失败`)
    batchTagsModalVisible.value = false
    selectedKeys.value = []
    loadSkills()
  } catch (e) { Message.error(String((e as Record<string, unknown>)?._message || '批量 tag 失败')) }
  finally { batchLoading.value = false }
}

// 删除（带 Undo）：先乐观移除 + 显示 5 秒撤回 Toast，超时后真正调 API
function handleDeleteSkill(skill: SkillListItem) {
  if (!skill?.id) return
  confirmDelete(skill.name || skill.id, () => {
      // 从列表里乐观移除
      const idx = skills.value.findIndex(s => s.id === skill.id)
      if (idx < 0) return
      const snapshot = skills.value[idx]
      skills.value.splice(idx, 1)

      undoableAction({
        execute: async () => {
          await skillApi.delete(skill.id)
          // 成功后刷新 count
          loadSkills()
          loadPinned()
        },
        undoLabel: `已删除「${skill.name || skill.id}」，5 秒内可撤回`,
        delay: 5000,
        onUndo: () => {
          // 把 snapshot 塞回原位置
          skills.value.splice(Math.min(idx, skills.value.length), 0, snapshot)
        },
        onError: (e: any) => {
          // 失败时恢复列表
          skills.value.splice(Math.min(idx, skills.value.length), 0, snapshot)
          Message.error(e?._message || '删除失败')
        },
      })
  }, {
    content: `确定删除「${skill.name || skill.id}」吗？列表会先移除并保留 5 秒撤回窗口，超时后才会真正删除。`,
    okText: '删除 Skill',
    width: 440,
  })
}

// 监听 URL query 变化自动加载
watch(() => route.query.view_filter, (value) => {
  viewFilter.value = normalizeViewFilter(value)
})
watch(() => route.query, loadSkills, { deep: true })
onMounted(() => {
  loadSkills()
  loadPinned()
  loadDepartments()
  loadRecent()
  loadOrgUnits()
  loadPendingReviewCount()
})
</script>

<style scoped>
/* 设计稿 design/src/skills.jsx 的左侧 ai-sidebar 布局（与 HallHome 共用同一 pattern） */
.skill-list-page {
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

.skill-sidebar {
  width: 220px;
  flex: 0 0 220px;
  border-right: 1px solid var(--ai-border);
  background: var(--ai-surface);
  padding: 16px 12px;
  overflow-y: auto;
}

.skill-sidebar-head {
  display: none;
  align-items: center;
  justify-content: space-between;
  padding: 0 4px 6px;
}
.skill-sidebar-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}
.skill-sidebar-close {
  display: none;
  background: transparent;
  border: 0;
  cursor: pointer;
  color: var(--ai-ink-4);
  padding: 4px;
}
.skill-sidebar-close:hover {
  color: var(--ai-ink-1);
}
.skill-sidebar-close svg {
  width: 13px;
  height: 13px;
}
.skill-sidebar-toggle {
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
.skill-sidebar-toggle svg {
  width: 13px;
  height: 13px;
}

.skill-sidebar .ai-side-group {
  margin-bottom: 18px;
}
.skill-sidebar .ai-side-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-4);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 0 8px 6px;
}
.skill-sidebar .ai-side-item {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 28px;
  padding: 0 8px;
  border-radius: 5px;
  font-size: 13px;
  color: var(--ai-ink-2);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.skill-sidebar .ai-side-item .ic {
  color: var(--ai-ink-4);
  width: 13px;
  height: 13px;
  flex: 0 0 13px;
}
.skill-sidebar .ai-side-item:hover {
  background: var(--ai-surface-2);
}
.skill-sidebar .ai-side-item.active {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
  font-weight: 500;
}
.skill-sidebar .ai-side-item.active .ic {
  color: var(--ai-ink-1);
}
.skill-sidebar .ai-side-item.disabled {
  cursor: default;
  opacity: 0.55;
}
.skill-sidebar .ai-side-item.disabled:hover {
  background: transparent;
}
.skill-sidebar .ai-side-item .ai-side-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.skill-sidebar .ai-side-item .muted-text {
  color: var(--ai-ink-4);
  font-size: 12px;
}
.skill-sidebar .ai-side-item .count {
  margin-left: auto;
  font-size: 11px;
  color: var(--ai-ink-4);
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
}
.skill-sidebar .ai-side-item .warn-ic {
  color: var(--ai-bad, #d9534f);
}
.skill-sidebar .ai-side-item .warn-count {
  color: var(--ai-bad, #d9534f);
}
.skill-sidebar .ai-side-item .deptdot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: 0 0 8px;
  background: var(--ai-ink-4);
}
.skill-sidebar .ai-side-item .dot-ok {
  background: var(--ai-ok, #16a34a);
}
.skill-sidebar .ai-side-item .dot-warn {
  background: var(--ai-warn, #d97706);
}
.skill-sidebar .ai-side-item .dot-muted {
  background: var(--ai-ink-4);
}

.skill-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
  padding: 0;
  overflow-x: auto;
}

.skill-pagehead {
  flex: 0 0 auto;
}
.skill-head-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}
.skill-head-actions svg {
  width: 12px;
  height: 12px;
  flex: 0 0 12px;
}
.skill-head-actions .ai-btn {
  cursor: pointer;
}
.skill-head-actions .ai-btn[disabled] {
  cursor: not-allowed;
  color: var(--ai-ink-4);
  background: var(--ai-surface-2);
  border-color: var(--ai-border);
  opacity: 0.72;
}
.skill-head-actions .skill-batch-btn.danger {
  color: var(--ai-bad);
  background: var(--ai-bad-soft);
  border-color: transparent;
}
.skill-pagebody {
  flex: 1;
  padding: 0;
  background: transparent;
}

@media (max-width: 768px) {
  .skill-list-page {
    flex-direction: column;
  }
  .skill-sidebar {
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
  .skill-sidebar--open {
    transform: translateX(0);
  }
  .skill-sidebar-head {
    display: flex;
  }
  .skill-sidebar-close {
    display: inline-flex;
  }
  .skill-sidebar-toggle {
    display: inline-flex;
  }
  .skill-main {
    padding: 0;
  }
  .skill-sidebar-toggle {
    margin: 12px 16px 0;
  }
  .skill-pagehead {
    flex-direction: column;
    align-items: stretch;
    padding: 16px;
  }
  .skill-head-actions {
    justify-content: flex-start;
  }
  .skill-section-tabs {
    padding: 0 16px;
  }
  .skill-pagebody {
    padding: 16px;
  }
  .pre-list-blocks,
  .skill-card-view {
    margin: 0 0 16px;
    padding: 0;
  }
  .table-footer {
    padding: 14px 0 0;
  }
}

/* 置顶区域 */
.pinned-section {
  margin-bottom: 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  overflow: hidden;
}
.pinned-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 16px;
  font-size: var(--sf-text-body);
  font-weight: 600;
  cursor: pointer;
  color: var(--ai-ink-2);
  transition: background var(--sf-transition);
  user-select: none;
}
.pinned-header:hover { background: var(--ai-surface-2); }
.pinned-icon { color: var(--ai-warn); font-size: 14px; }
.pinned-toggle-icon {
  margin-left: auto;
  font-size: var(--sf-text-caption);
  transition: transform var(--sf-transition-fast);
}
.pinned-count {
  font-size: var(--sf-text-tiny);
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-weight: 400;
}
.rotate-180 { transform: rotate(180deg); }
.pinned-list { border-top: 1px solid var(--ai-border); }
.pinned-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 16px;
  cursor: pointer;
  transition: background var(--sf-transition);
  font-size: var(--sf-text-body);
}
.pinned-item:hover { background: var(--ai-surface-2); }
.pinned-id { font-weight: 600; color: var(--ai-accent-ink); }
.pinned-name { color: var(--ai-ink-3); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pinned-dept { font-size: var(--sf-text-tiny); color: var(--ai-ink-4); }

/* 最近编辑 */
.recent-section {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  padding: 8px 12px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  flex-wrap: wrap;
}
.recent-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  white-space: nowrap;
}
.recent-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  flex: 1;
}
.recent-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: 11.5px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease;
  max-width: 220px;
  border: 1px solid var(--ai-border);
  font-family: var(--ai-font-mono);
}
.recent-chip:hover {
  background: var(--ai-surface-3);
  color: var(--ai-ink-1);
}
.recent-chip-id { font-weight: 500; color: var(--ai-ink-1); }
.recent-chip-name {
  color: var(--ai-ink-4);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 140px;
  font-family: var(--ai-font-sans);
}
.recent-chip:hover .recent-chip-name { color: var(--ai-ink-2); }

/* 星标：hover 和 active 区分色 */
.pin-star {
  font-size: 14px;
  color: var(--ai-ink-4);
  cursor: pointer;
  transition: color var(--sf-transition);
}
.pin-star:hover { color: rgb(var(--orange-3)); }
.pin-star.active { color: var(--ai-warn); }
.pin-star.active:hover { color: rgb(var(--orange-5)); }

/* 可点击 stat 卡片：选中状态 */
.stat-card {
  cursor: pointer;
  transition: all var(--sf-transition);
}
.stat-card.active {
  border-color: var(--ai-accent-ink) !important;
  box-shadow: 0 14px 30px rgba(45, 97, 255, 0.18) !important;
}

/* 列表卡片内的 pre-list 容器（pinned + recent） */
.pre-list-blocks {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 16px 28px;
}

/* 工具栏 */
.status-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.status-tabs .stat-chip {
  cursor: pointer;
}
.filter-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 8px;
}

/* 设计稿 design/src/skills.jsx：page-header 右侧 审核中心 按钮内嵌 待审 pill */
.header-review-btn :deep(.header-review-pill),
.header-review-btn .header-review-pill {
  margin-left: 6px;
  height: 18px;
  padding: 0 6px;
  font-size: 10.5px;
  line-height: 18px;
}

/* 设计稿 .ai-tabs：列表 / 监控 / 审核 / 变更日志 顶部 tab 横条 */
.skill-section-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  border-bottom: 1px solid var(--ai-border);
  margin: 0;
  padding: 0 28px;
  background: var(--ai-surface);
  flex-wrap: wrap;
}
.skill-section-tab {
  height: 32px;
  padding: 0 12px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--ai-ink-3);
  border-bottom: 1.5px solid transparent;
  margin-bottom: -1px;
  cursor: pointer;
  text-decoration: none;
  font-weight: 450;
  transition: color 0.15s ease, border-color 0.15s ease;
}
.skill-section-tab:hover {
  color: var(--ai-ink-1);
}
.skill-section-tab.active,
.skill-section-tab.router-link-active {
  color: var(--ai-ink-1);
  border-bottom-color: var(--ai-ink-1);
  font-weight: 500;
}
.skill-section-tab-count {
  font-size: 11.5px;
  color: var(--ai-ink-4);
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
  margin-left: 2px;
}
.skill-section-tab-pill {
  height: 16px;
  padding: 0 5px;
  font-size: 10px;
  line-height: 16px;
  margin-left: 4px;
}
.skill-tab-spacer {
  flex: 1 1 16px;
  min-width: 16px;
}
.skill-tab-tools {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  padding: 6px 0;
  flex: 0 1 auto;
  min-width: 0;
  flex-wrap: wrap;
}
.skill-search-control {
  width: 220px;
}
.skill-filter-trigger {
  width: 110px;
}
.skill-filter-risk {
  width: 100px;
}
.skill-filter-sort {
  width: 140px;
}
.skill-view-toggle {
  flex: 0 0 auto;
}
.skill-view-toggle :deep(.arco-radio-button) {
  min-width: 34px;
  padding: 0 9px;
}
.skill-view-toggle svg {
  width: 13px;
  height: 13px;
  display: block;
}

/* 设计稿 .ai-table cell：Skill 名 + mono id 副标，pin 内嵌在标题行 */
.skill-cell { cursor: pointer; }
.skill-cell-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.skill-cell-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.skill-cell-id {
  font-size: 11.5px;
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-3);
  margin-top: 1px;
  margin-left: 20px; /* 与 pin-star 宽度对齐 */
}
.inline-pin {
  font-size: 13px;
  flex-shrink: 0;
}

/* 风险 / 版本组合 cell */
.risk-version-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.risk-pill {
  font-size: 10.5px;
  height: 18px;
}
.version-text {
  font-size: 11.5px;
  color: var(--ai-ink-3);
  font-family: var(--ai-font-mono);
}

/* 负责人 / 部门组合 cell */
.owner-cell {
  line-height: 1.35;
}
.owner-name {
  font-size: 13px;
  color: var(--ai-ink-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.owner-dept {
  margin-top: 1px;
}

/* 触发 cell pill */
.trigger-pill {
  font-size: 10.5px;
  height: 18px;
}

/* 今日使用 + sparkline */
.uses-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.uses-num {
  font-family: var(--ai-font-mono);
  font-weight: 500;
  font-size: 12.5px;
  color: var(--ai-ink-1);
  font-variant-numeric: tabular-nums;
}

/* 通用 mono / tiny / muted（与设计稿 tokens 对齐） */
.mono { font-family: var(--ai-font-mono); }
.mono-id { font-family: var(--ai-font-mono); font-size: 11.5px; color: var(--ai-ink-3); }
.tiny { font-size: 11px; }
.muted { color: var(--ai-ink-3); }

/* 表格行操作按钮 */
.row-action {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

/* 表格 hover 行加 surface-2 背景以贴近设计稿 .ai-table */
.sf-skill-table :deep(.arco-table-tr:hover .arco-table-td) {
  background: var(--ai-surface-2);
}
.skill-card-view {
  padding: 20px 28px;
}
.skill-import-alert {
  margin-bottom: 16px;
}
.skill-import-picker {
  width: 100%;
  justify-content: center;
}
.skill-import-file {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}
.skill-import-file-meta {
  font-size: var(--sf-text-tiny);
  color: var(--ai-ink-3);
}
.skill-import-version-tip {
  margin-top: 8px;
  font-size: var(--sf-text-tiny);
  color: var(--ai-ink-3);
}
.table-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 28px 0;
  border-top: 1px solid var(--ai-border);
  margin-top: 0;
}
.stats-inline { display: flex; gap: 6px; flex-wrap: wrap; }

/* 设计稿 .ai-pill 风格的状态 tab — 4px 方角，20px 高，5 档配色 */
.stat-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  font-size: 11.5px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  border: 1px solid var(--ai-border);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0;
}
.stat-chip:hover { background: var(--ai-surface-3); color: var(--ai-ink-2); }
.stat-chip b { font-size: 12px; font-weight: 600; color: var(--ai-ink-2); }
.stat-chip.active {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  border-color: var(--ai-ink-1);
  font-weight: 500;
}
.stat-chip.active b { color: var(--ai-surface); }
.stat-active { background: var(--ai-ok-soft); color: var(--ai-ok); border-color: transparent; }
.stat-active.active { background: var(--ai-ok); color: var(--ai-surface); border-color: var(--ai-ok); }
.stat-active.active b { color: var(--ai-surface); }
.stat-active b { color: var(--ai-ok); }
.stat-shadow { background: var(--ai-info-soft); color: var(--ai-info); border-color: transparent; }
.stat-shadow.active { background: var(--ai-info); color: var(--ai-surface); border-color: var(--ai-info); }
.stat-shadow.active b { color: var(--ai-surface); }
.stat-shadow b { color: var(--ai-info); }
.stat-draft { background: var(--ai-surface-2); color: var(--ai-ink-4); border-color: var(--ai-border); }
.stat-draft b { color: var(--ai-ink-3); }
.git-hash {
  font-family: var(--ai-font-mono);
  font-size: var(--sf-text-tiny);
  color: var(--ai-ink-4);
  margin-left: 4px;
}
.total-text {
  font-size: var(--sf-text-caption);
  color: var(--ai-ink-3);
}

/* 卡片视图 */
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
  margin-bottom: 8px;
}
.card-version {
  font-size: var(--sf-text-tiny);
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
}
.card-id {
  font-size: var(--sf-text-body);
  font-weight: 600;
  color: var(--ai-ink-1);
}
.card-name {
  font-size: var(--sf-text-caption);
  color: var(--ai-ink-3);
  margin: 2px 0 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-dept {
  font-size: var(--sf-text-tiny);
  color: var(--ai-ink-4);
}
.card-time {
  font-size: var(--sf-text-tiny);
  color: var(--ai-ink-4);
  margin-top: 8px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
/* 删除按钮常显但低调，hover 卡片时变明显（触屏友好） */
.card-delete-btn {
  opacity: 0.3;
  transition: opacity var(--sf-transition);
}
.card-delete-btn:hover,
.skill-card:hover .card-delete-btn {
  opacity: 1;
}

/* N4 空态建议 chips */
.empty-suggest {
  margin-top: 4px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: center;
}
.suggest-label { font-size: var(--sf-text-caption); color: var(--ai-ink-3); }
.suggest-chip { cursor: pointer; }
.suggest-chip:hover { background: var(--color-primary-light-1); }

.batch-tags-tip {
  margin-bottom: var(--sf-spacing-md);
  color: var(--ai-ink-3);
}

.batch-tags-preview {
  margin-top: var(--sf-spacing-sm);
}

.batch-tags-chip {
  margin-right: var(--sf-spacing-xs);
}

/* 响应式：平板（md ≤ 1024px）空间偏小，筛选栏自然换行，page-header 自适应 */
@media (max-width: 1024px) {
  .filter-bar {
    flex-wrap: wrap;
    gap: 8px;
  }
}
@media (max-width: 768px) {
  .filter-bar {
    flex-direction: column;
    align-items: stretch;
  }
  .filter-bar :deep(.arco-space) {
    flex-wrap: wrap;
  }
  .status-tabs {
    gap: 6px;
  }
  .pre-list-blocks {
    gap: 8px;
  }
}
@media (max-width: 480px) {
  .filter-bar :deep(.arco-space-item) {
    width: 100%;
  }
  .filter-bar :deep(.arco-select),
  .filter-bar :deep(.arco-auto-complete) {
    width: 100% !important;
  }
  .stat-chip {
    padding: 2px 8px;
    font-size: var(--sf-text-tiny);
  }
}
</style>
