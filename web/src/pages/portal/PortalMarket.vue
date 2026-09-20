<template>
  <div class="page-container portal-market-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">Skills · 应用门户 · 企业市场</div>
        <h2 class="page-title">企业市场 · 资产库</h2>
        <div class="page-subtitle">按模板 / 已发布资产两个视角统一浏览公司级沉淀；管理员在此维护上架与下架</div>
      </div>
    </div>

    <!-- N7: 简易筛选条 -->
    <div class="market-filter">
      <a-input-search
        v-model="searchQuery"
        placeholder="搜索名称 / 描述"
        allow-clear
        style="width: 240px"
      />
      <a-select v-model="categoryFilter" placeholder="分类" allow-clear style="width: 140px">
        <a-option v-for="c in categoryOptions" :key="c" :value="c">{{ c }}</a-option>
      </a-select>
      <a-tag v-if="filteredTemplates.length + filteredSkills.length !== templates.length + skills.length"
        color="arcoblue" size="small">命中 {{ filteredTemplates.length + filteredSkills.length }} 条 / 总 {{ templates.length + skills.length }}</a-tag>
    </div>

    <a-spin :loading="marketLoading">
      <a-empty v-if="marketError" :description="marketError">
        <a-button type="outline" @click="loadData">重试</a-button>
      </a-empty>
      <a-row v-else :gutter="16">
      <a-col :xs="24" :lg="10">
        <a-card class="page-list-card" title="模板资产">
          <a-list v-if="filteredTemplates.length" :data="filteredTemplates" :bordered="false">
            <template #item="{ item }">
              <a-list-item>
                <a-list-item-meta :title="item.display_name || item.name || item.id" :description="item.description || '暂无说明'" />
              </a-list-item>
            </template>
          </a-list>
          <SfEmptyState
            v-else
            :description="hasFilter ? '没有命中筛选条件的模板' : '暂无模板'"
            :hint="hasFilter ? '试试清除筛选' : '在 Skill 编辑页点「发布为模板」可把当前 Skill 抽成骨架供全公司复用'"
            :action-label="hasFilter ? '清除筛选' : (hasPendingReview ? '去审核中心' : '')"
            @action="hasFilter ? clearFilter() : $router.push('/reviews')"
          />
        </a-card>
      </a-col>
      <a-col :xs="24" :lg="14">
        <a-card class="page-list-card" title="已发布 Skill">
          <a-table v-if="filteredSkills.length" :data="filteredSkills" :pagination="false" row-key="id">
            <template #columns>
              <a-table-column title="名称" data-index="display_name" />
              <a-table-column title="分类" data-index="category" />
              <a-table-column title="负责人" data-index="owner_name" />
            </template>
          </a-table>
          <SfEmptyState
            v-else
            :description="hasFilter ? '没有命中筛选条件的 Skill' : '暂无已发布 Skill'"
            :hint="hasFilter ? '试试清除筛选' : '只有通过审核、状态为 active 的 Skill 才会出现在市场。待审核 Skill 在「审核中心」'"
            :action-label="hasFilter ? '清除筛选' : (hasPendingReview ? '去审核中心' : '')"
            @action="hasFilter ? clearFilter() : $router.push('/reviews')"
          />
        </a-card>
      </a-col>
    </a-row>
    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { portalApi as rawPortalApi, skillApi } from '@/api'
import { SfEmptyState } from '@/components/common'

defineOptions({ name: 'PortalMarket' })

const portalApi: any = rawPortalApi
const templates = ref<any[]>([])
const skills = ref<any[]>([])
const marketLoading = ref(false)
const marketError = ref('')

// N7: 简单前端筛选（没改后端；数据量小够用）
const searchQuery = ref('')
const categoryFilter = ref<string | undefined>(undefined)

const categoryOptions = computed(() => {
  const set = new Set<string>()
  for (const t of templates.value) if (t.category) set.add(t.category)
  for (const s of skills.value) if (s.category) set.add(s.category)
  return Array.from(set).sort()
})

const hasFilter = computed(() => !!(searchQuery.value || categoryFilter.value))

function matchItem(item: any): boolean {
  const q = (searchQuery.value || '').trim().toLowerCase()
  if (q) {
    const hay = `${item.name || ''} ${item.display_name || ''} ${item.description || ''}`.toLowerCase()
    if (!hay.includes(q)) return false
  }
  if (categoryFilter.value && item.category !== categoryFilter.value) return false
  return true
}

const filteredTemplates = computed(() => templates.value.filter(matchItem))
const filteredSkills = computed(() => skills.value.filter(matchItem))

function clearFilter() {
  searchQuery.value = ''
  categoryFilter.value = undefined
}

// W1-C: 检测"待审核" skill（draft/shadow）是否存在，���定空态是否显示"去审核中心"按钮
const hasPendingReview = ref(false)
async function loadPendingCheck() {
  try {
    const res: any = await skillApi.list({ page: 1, page_size: 1, status: 'draft' })
    hasPendingReview.value = (res?.total || 0) > 0
  } catch {
    hasPendingReview.value = false
  }
}

async function loadData() {
  marketLoading.value = true
  marketError.value = ''
  try {
    const res = await portalApi.getMarket()
    templates.value = res?.templates || []
    skills.value = res?.skills || []
  } catch (e: any) {
    marketError.value = e?._message || '加载市场数据失败'
  } finally {
    marketLoading.value = false
  }
}

onMounted(() => {
  loadData()
  loadPendingCheck()
})
</script>

<style scoped>
.portal-market-page {
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}

/* 设计稿 page chrome 覆盖 */
.portal-market-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.portal-market-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
}
.portal-market-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.portal-market-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}
.portal-market-page :deep(.page-list-card .arco-card-header-title) {
  font-weight: 500 !important;
  font-size: 13px !important;
  color: var(--ai-ink-1) !important;
  letter-spacing: -0.005em;
}

.market-filter {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 14px 0;
  padding: 10px 14px;
  border-radius: var(--ai-radius);
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  flex-wrap: wrap;
}

/* arco a-tag → ai-pill */
.portal-market-page :deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.portal-market-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
  border-color: transparent;
}
.portal-market-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}

/* table density */
.portal-market-page :deep(.arco-table-th) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11px !important;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 8px 10px !important;
}
.portal-market-page :deep(.arco-table-td) {
  font-size: 12.5px;
  padding: 10px 10px !important;
  border-bottom: 1px solid var(--ai-border) !important;
}
.portal-market-page :deep(.arco-table-tr:hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

/* a-list 项目密集化 */
.portal-market-page :deep(.arco-list-item) {
  padding: 10px 14px !important;
  border-bottom: 1px solid var(--ai-border) !important;
}
.portal-market-page :deep(.arco-list-item-meta-title) {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.portal-market-page :deep(.arco-list-item-meta-description) {
  font-size: 12px;
  color: var(--ai-ink-3);
}

/* buttons */
.portal-market-page :deep(.arco-btn) {
  border-radius: 6px;
  height: 30px;
  font-weight: 500;
  font-size: 12.5px;
  box-shadow: none;
}
</style>
