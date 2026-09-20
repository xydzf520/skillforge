<template>
  <div class="page-container admin-data-hygiene-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">管理后台 · 数据与服务</div>
        <h2 class="page-title">数据卫生</h2>
        <p class="page-subtitle">数据库 Skill 的异常扫描与批量修复</p>
      </div>
    </div>

    <a-tabs v-model:active-key="activeTab" type="rounded" class="hygiene-tabs">
      <a-tab-pane key="department" title="部门字段卫生">
        <a-card class="page-list-card">
          <template #extra>
            <a-button :loading="loadingDept" @click="loadDept">
              <template #icon><icon-refresh /></template>刷新
            </a-button>
          </template>

          <SfLoadingState v-if="loadingDept" tip="扫描 Skill 库..." />
          <SfEmptyState
            v-else-if="!deptItems.length"
            description="没有部门异常的 Skill"
            hint="所有 Skill 都有有效部门（非空、非'未指定'）"
          />
          <a-table
            v-else
            :data="deptItems"
            :pagination="false"
            row-key="id"
            size="small"
          >
            <template #columns>
              <a-table-column title="Skill ID" data-index="id" :width="280" />
              <a-table-column title="名称">
                <template #cell="{ record }">{{ record.display_name || record.name || '-' }}</template>
              </a-table-column>
              <a-table-column title="部门（当前）" :width="140">
                <template #cell="{ record }">
                  <a-tag color="red" size="small">{{ record.department || '(空)' }}</a-tag>
                </template>
              </a-table-column>
              <a-table-column title="负责人" data-index="owner" :width="120" />
              <a-table-column title="更新时间" :width="180">
                <template #cell="{ record }">{{ formatTime(record.updated_at) }}</template>
              </a-table-column>
              <a-table-column title="操作" :width="100">
                <template #cell="{ record }">
                  <router-link :to="`/skills/${record.id}`" class="fix-link">去补录 →</router-link>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-card>
      </a-tab-pane>

      <a-tab-pane key="id" title="Skill ID 卫生">
        <a-card class="page-list-card">
          <template #extra>
            <a-space>
              <a-radio-group
                v-model="idFilter"
                type="button"
                size="small"
                @change="loadIds"
              >
                <a-radio value="">全部</a-radio>
                <a-radio value="e2e_test">e2e 测试残留</a-radio>
                <a-radio value="import">导入</a-radio>
                <a-radio value="fork">Fork</a-radio>
                <a-radio value="manual">手动</a-radio>
              </a-radio-group>
              <a-button :loading="loadingIds" @click="loadIds">
                <template #icon><icon-refresh /></template>刷新
              </a-button>
              <a-button
                v-if="selectedDirty.length"
                type="primary"
                status="danger"
                @click="openBulkDelete"
              >
                批量清理 {{ selectedDirty.length }} 个
              </a-button>
            </a-space>
          </template>

          <div v-if="counts" class="counts-row">
            <span class="count-chip count-e2e">
              e2e 测试残留 <b>{{ counts.e2e_test || 0 }}</b>
            </span>
            <span class="count-chip count-import">
              导入 <b>{{ counts.import || 0 }}</b>
            </span>
            <span class="count-chip count-fork">
              Fork <b>{{ counts.fork || 0 }}</b>
            </span>
            <span class="count-chip count-manual">
              手动 <b>{{ counts.manual || 0 }}</b>
            </span>
            <span class="count-total">总计 {{ total }}</span>
          </div>

          <SfLoadingState v-if="loadingIds" tip="分类扫描..." />
          <SfEmptyState
            v-else-if="!idItems.length"
            :description="idFilter === 'e2e_test' ? '没有 e2e 测试残留 🎉' : '暂无数据'"
          />
          <a-table
            v-else
            :data="idItems"
            :pagination="{ pageSize: 30, showTotal: true }"
            row-key="id"
            size="small"
            :row-selection="rowSelection"
            v-model:selected-keys="selectedIds"
          >
            <template #columns>
              <a-table-column title="Skill ID" data-index="id" :width="260" />
              <a-table-column title="分类" :width="140">
                <template #cell="{ record }">
                  <a-tag size="small" :color="categoryColor(record.category)">
                    {{ categoryLabel(record.category) }}
                  </a-tag>
                </template>
              </a-table-column>
              <a-table-column title="名称">
                <template #cell="{ record }">{{ record.name || '-' }}</template>
              </a-table-column>
              <a-table-column title="部门" data-index="department" :width="120" />
              <a-table-column title="状态" data-index="status" :width="80" />
              <a-table-column title="创建时间" :width="180">
                <template #cell="{ record }">{{ formatTime(record.created_at) }}</template>
              </a-table-column>
              <a-table-column title="操作" :width="100">
                <template #cell="{ record }">
                  <router-link :to="`/skills/${record.id}`" class="fix-link">查看 →</router-link>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </a-card>
      </a-tab-pane>
    </a-tabs>

    <a-modal
      v-model:visible="bulkDeleteVisible"
      title="批量清理确认"
      @ok="doBulkDelete"
      :ok-loading="bulkDeleting"
      :ok-button-props="{ status: 'danger', disabled: confirmText !== 'DELETE' }"
    >
      <p>
        即将删除 <b>{{ selectedDirty.length }}</b> 个被分类为
        <a-tag size="small" color="red">{{ categoryLabel(idFilter || 'e2e_test') }}</a-tag>
        的 Skill。此操作**不可恢复**（git 历史保留，DB 记录消失）。
      </p>
      <p>请在下方输入 <code>DELETE</code> 以确认：</p>
      <a-input v-model="confirmText" placeholder="输入 DELETE" />
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { IconRefresh } from '@arco-design/web-vue/es/icon'
import { Message } from '@arco-design/web-vue'
import { skillApi as rawSkillApi } from '@/api'
import request from '@/api/request'
import { formatTime } from '@/utils/format'
import { SfLoadingState, SfEmptyState } from '@/components/common'

defineOptions({ name: 'AdminDataHygiene' })

const skillApi: any = rawSkillApi

interface SkillRow {
  id: string
  name?: string
  display_name?: string
  department?: string | null
  owner?: string
  updated_at?: string
  created_at?: string
  status?: string
  category?: string
}

const activeTab = ref<'department' | 'id'>('department')

// ────────── 部门卫生（原有） ──────────
const loadingDept = ref(false)
const deptItems = ref<SkillRow[]>([])
const PLACEHOLDER = new Set(['', '未指定'])

async function loadDept() {
  loadingDept.value = true
  try {
    const res = await skillApi.list({ page: 1, page_size: 500 })
    const all: SkillRow[] = res?.items || []
    deptItems.value = all.filter((s) => !s.department || PLACEHOLDER.has(s.department as string))
  } catch {
    deptItems.value = []
  } finally {
    loadingDept.value = false
  }
}

// ────────── ID 卫生（v2.8.0 新） ──────────
const loadingIds = ref(false)
const idItems = ref<SkillRow[]>([])
const counts = ref<Record<string, number> | null>(null)
const total = ref(0)
const idFilter = ref<string>('e2e_test')
const selectedIds = ref<string[]>([])

const rowSelection = { type: 'checkbox', showCheckedAll: true }

const selectedDirty = computed(() =>
  selectedIds.value.filter((sid) => idItems.value.find((it) => it.id === sid)),
)

async function loadIds() {
  loadingIds.value = true
  selectedIds.value = []
  try {
    const r: any = await request.get('/skills/admin/hygiene', {
      params: idFilter.value ? { filter_class: idFilter.value } : {},
    })
    idItems.value = r?.items || []
    counts.value = r?.counts || null
    total.value = r?.total || 0
  } catch (e: any) {
    Message.error(e?.message || '加载失败')
    idItems.value = []
  } finally {
    loadingIds.value = false
  }
}

const bulkDeleteVisible = ref(false)
const bulkDeleting = ref(false)
const confirmText = ref('')

function openBulkDelete() {
  confirmText.value = ''
  bulkDeleteVisible.value = true
}

async function doBulkDelete() {
  if (confirmText.value !== 'DELETE') return
  bulkDeleting.value = true
  try {
    const r: any = await request.post('/skills/admin/hygiene/bulk-cleanup', {
      skill_ids: selectedDirty.value,
      expected_category: idFilter.value || 'e2e_test',
      confirm_text: 'DELETE',
    })
    const deleted = (r?.deleted || []).length
    const skipped = (r?.skipped || []).length
    const errors = (r?.errors || []).length
    Message.success(`清理完成：删除 ${deleted} 个，跳过 ${skipped}，错误 ${errors}`)
    bulkDeleteVisible.value = false
    await loadIds()
  } catch (e: any) {
    Message.error(e?.message || '批量清理失败')
  } finally {
    bulkDeleting.value = false
  }
}

function categoryLabel(c: string): string {
  const m: Record<string, string> = {
    e2e_test: 'e2e 测试',
    import: '导入',
    fork: 'Fork',
    manual: '手动',
  }
  return m[c] || c
}

function categoryColor(c: string): string {
  const m: Record<string, string> = {
    e2e_test: 'red',
    import: 'blue',
    fork: 'orange',
    manual: 'gray',
  }
  return m[c] || 'gray'
}

onMounted(() => {
  loadDept()
  loadIds()
})
</script>

<style scoped>
/* 设计稿 page chrome 覆盖 */
.admin-data-hygiene-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.admin-data-hygiene-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
}
.admin-data-hygiene-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.admin-data-hygiene-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

/* arco tabs → ai-tabs 风格 */
.hygiene-tabs :deep(.arco-tabs-nav) {
  padding: 0 0 0;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--ai-border);
}
.hygiene-tabs :deep(.arco-tabs-tab) {
  padding: 0 12px;
  height: 36px;
  font-weight: 450;
  font-size: 13px;
  color: var(--ai-ink-3);
  letter-spacing: -0.005em;
}
.hygiene-tabs :deep(.arco-tabs-tab-active) {
  color: var(--ai-ink-1);
  font-weight: 500;
}
.hygiene-tabs :deep(.arco-tabs-nav-ink) {
  background: var(--ai-ink-1);
  height: 1.5px;
}

/* 跳转链接 */
.fix-link {
  color: var(--ai-accent-ink);
  text-decoration: none;
  font-size: 12.5px;
  font-weight: 500;
}
.fix-link:hover {
  text-decoration: underline;
}

/* 状态计数器条 */
.counts-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.count-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  font-size: 11.5px;
  font-weight: 500;
  color: var(--ai-ink-3);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  font-variant-numeric: tabular-nums;
}
.count-chip b {
  font-weight: 600;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
}
.count-chip.count-e2e {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}
.count-chip.count-e2e b {
  color: var(--ai-bad);
}
.count-chip.count-import {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}
.count-chip.count-import b {
  color: var(--ai-info);
}
.count-chip.count-fork {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.count-chip.count-fork b {
  color: var(--ai-warn);
}
.count-total {
  font-size: 11.5px;
  color: var(--ai-ink-4);
  margin-left: 4px;
  font-variant-numeric: tabular-nums;
}

/* inline code */
code {
  background: var(--ai-surface-2);
  padding: 1px 5px;
  border-radius: 3px;
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-1);
}

/* a-tag → ai-pill 映射 */
.admin-data-hygiene-page :deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.admin-data-hygiene-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}
.admin-data-hygiene-page :deep(.arco-tag-color-orange) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.admin-data-hygiene-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
.admin-data-hygiene-page :deep(.arco-tag-color-arcoblue),
.admin-data-hygiene-page :deep(.arco-tag-color-blue) {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}

/* table 密集化 */
.admin-data-hygiene-page :deep(.arco-table-th) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11px !important;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 8px 10px !important;
}
.admin-data-hygiene-page :deep(.arco-table-td) {
  font-size: 12.5px;
  padding: 10px 10px !important;
  border-bottom: 1px solid var(--ai-border) !important;
}
.admin-data-hygiene-page :deep(.arco-table-tr:hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}
</style>
