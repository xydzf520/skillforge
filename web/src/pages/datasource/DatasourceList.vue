<template>
  <div class="page-container datasource-list-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">数据 · 数据源</div>
        <h2 class="page-title">数据源</h2>
        <p class="page-subtitle">管理 CSV 上传 / API 拉取类数据源，供 Skill 消费</p>
      </div>
      <a-space>
        <a-button v-if="userStore.isAdmin" @click="$router.push('/admin/connections')">
          <template #icon><icon-link /></template>连接管理
        </a-button>
        <a-button v-if="userStore.isEngineer" type="primary" @click="openCreate">
          <template #icon><icon-plus /></template>新建数据源
        </a-button>
      </a-space>
    </div>

    <a-card class="page-list-card">
      <div class="filter-bar">
        <a-space>
          <a-select v-model="filters.source_type" placeholder="类型" allow-clear style="width: 140px" @change="loadSources">
            <a-option value="csv">CSV 上传</a-option>
            <a-option value="api">API 拉取</a-option>
          </a-select>
          <a-select v-model="filters.is_active" placeholder="状态" allow-clear style="width: 120px" @change="loadSources">
            <a-option :value="true">启用</a-option>
            <a-option :value="false">停用</a-option>
          </a-select>
        </a-space>
      </div>

      <a-table class="page-list-table" :data="nonPlatformSources" :pagination="pagination" @page-change="onPageChange" row-key="id" :loading="loading">
        <template #columns>
          <a-table-column title="名称" data-index="name">
            <template #cell="{ record }">
              <a-link @click="$router.push(`/datasource/${record.id}/upload`)">{{ cleanName(record.name) }}</a-link>
            </template>
          </a-table-column>
          <a-table-column title="类型" data-index="source_type" :width="120">
            <template #cell="{ record }">
              <a-tag size="small" :color="typeColor(record.source_type)">{{ typeLabel(record.source_type) }}</a-tag>
            </template>
          </a-table-column>
          <a-table-column title="部门" data-index="department" :width="120" />
          <a-table-column title="更新频率" data-index="frequency" :width="100" />
          <a-table-column title="状态" :width="100">
            <template #cell="{ record }">
              <a-tag :color="record.is_active ? 'green' : 'gray'">{{ record.is_active ? '启用' : '停用' }}</a-tag>
            </template>
          </a-table-column>
          <a-table-column title="操作" :width="200">
            <template #cell="{ record }">
              <a-space>
                <a-button type="text" size="small" @click="$router.push(`/datasource/${record.id}/upload`)">上传</a-button>
                <a-button type="text" size="small" @click="previewSource(record.id)">预览</a-button>
                <a-button type="text" size="small" @click="openFieldMapping(record)">字段映射</a-button>
                <a-button v-if="record.source_type === 'api'" type="text" size="small" @click="pullSource(record.id)">拉取</a-button>
              </a-space>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </a-card>

    <!-- 新建弹窗 -->
    <a-modal v-model:visible="showCreate" title="新建数据源" @ok="handleCreate" :ok-loading="creating">
      <a-form :model="createForm" layout="vertical">
        <a-form-item label="数据源ID" required><a-input v-model="createForm.source_id" placeholder="如：ds-sales-daily" /></a-form-item>
        <a-form-item label="名称" required><a-input v-model="createForm.name" /></a-form-item>
        <a-form-item label="类型"><a-select v-model="createForm.source_type">
          <a-option value="csv">CSV上传</a-option>
          <a-option value="api">API拉取</a-option>
        </a-select></a-form-item>
        <a-form-item label="部门"><a-input v-model="createForm.department" /></a-form-item>
      </a-form>
    </a-modal>

    <!-- 预览弹窗 -->
    <a-modal v-model:visible="showPreview" title="数据预览" :width="800" :footer="false">
      <a-table v-if="previewData.length" :data="previewData" :pagination="false" size="small" :scroll="{ x: true }" />
      <a-empty v-else description="无数据" />
    </a-modal>

    <!-- 字段映射弹窗 -->
    <a-modal
      v-model:visible="showMapping"
      title="字段映射配置"
      @ok="saveMapping"
      :ok-loading="mappingSaving"
      :on-before-ok="validateMapping"
      :width="'min(90vw, 680px)'"
    >
      <a-alert type="info" style="margin-bottom: 12px">配置数据源字段到目标字段的映射关系，附类型标注供下游工作流消费</a-alert>
      <div class="mapping-head">
        <span class="mapping-head-col">源字段</span>
        <span class="mapping-head-col">目标字段</span>
        <span class="mapping-head-col mapping-head-type">类型</span>
      </div>
      <div
        v-for="(m, i) in mappingEntries"
        :key="i"
        class="mapping-row"
      >
        <a-input
          v-model="m.source"
          placeholder="源字段名"
          class="mapping-input"
          :error="!!mappingErrors[i]?.source"
        />
        <icon-arrow-right class="mapping-arrow" />
        <a-input
          v-model="m.target"
          placeholder="目标字段名"
          class="mapping-input"
          :error="!!mappingErrors[i]?.target"
        />
        <a-select
          v-model="m.type"
          placeholder="类型"
          class="mapping-type"
        >
          <a-option value="string">string</a-option>
          <a-option value="number">number</a-option>
          <a-option value="date">date</a-option>
          <a-option value="bool">bool</a-option>
        </a-select>
        <a-button type="text" status="danger" @click="mappingEntries.splice(i, 1)"><icon-delete /></a-button>
      </div>
      <div v-if="mappingFormError" class="mapping-form-error">
        <icon-exclamation-circle /> {{ mappingFormError }}
      </div>
      <a-button type="dashed" long @click="addMappingRow"><icon-plus /> 添加映射</a-button>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { Message } from '@arco-design/web-vue'
import { datasourceApi as rawDatasourceApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { IconPlus, IconArrowRight, IconDelete, IconLink, IconExclamationCircle } from '@arco-design/web-vue/es/icon'

type DatasourceRecord = {
  id: string
  name?: string
  source_type?: string
  department?: string
  frequency?: string
  is_active?: boolean
  config?: Record<string, any>
}

const datasourceApi: any = rawDatasourceApi
const userStore = useUserStore()

const loading = ref(false)
const sources = ref<DatasourceRecord[]>([])
const filters = reactive<{ source_type?: string; is_active?: boolean }>({ source_type: undefined, is_active: undefined })
const pagination = reactive({ current: 1, pageSize: 20, total: 0 })
const showCreate = ref(false)
const creating = ref(false)
const showPreview = ref(false)
const previewData = ref<any[]>([])
const createForm = reactive({ source_id: '', name: '', source_type: 'csv', department: '', config: {} })

// 平台连接数据源现在由 /connections 页独立管理；本页过滤掉
const nonPlatformSources = computed(() => sources.value.filter(s => s.source_type !== 'platform_cookies'))

function cleanName(name: string | undefined): string {
  if (!name) return '-'
  return name.replace(/（自动创建）|\(自动创建\)|（auto-created）|\(auto-created\)/gi, '').trim()
}
function typeLabel(t: string | undefined) {
  return ({ csv_upload: 'CSV', csv: 'CSV', api_pull: 'API', api: 'API' } as Record<string, string>)[t || ''] || t
}
function typeColor(t: string | undefined) {
  return ({ csv_upload: 'blue', csv: 'blue', api_pull: 'cyan', api: 'cyan' } as Record<string, string>)[t || ''] || 'gray'
}

function openCreate() {
  Object.assign(createForm, { source_id: '', name: '', source_type: 'csv', department: '', config: {} })
  showCreate.value = true
}

async function loadSources() {
  loading.value = true
  try {
    const res = await datasourceApi.list({
      source_type: filters.source_type || undefined,
      is_active: filters.is_active !== undefined ? filters.is_active : undefined,
      page: pagination.current,
      page_size: pagination.pageSize,
    })
    sources.value = Array.isArray(res) ? res : (res.items || [])
    if (res.total !== undefined) pagination.total = res.total
  } catch (e: any) {
    Message.error(e._message || '加载失败')
  } finally {
    loading.value = false
  }
}

function onPageChange(page: number) {
  pagination.current = page
  loadSources()
}

async function handleCreate() {
  creating.value = true
  try {
    await datasourceApi.create(createForm)
    showCreate.value = false
    Message.success('创建成功')
    await loadSources()
  } catch (e: any) {
    Message.error(e._message || '创建失败')
  } finally { creating.value = false }
}

async function previewSource(id: string) {
  try {
    const res = await datasourceApi.preview(id, { limit: 50 })
    previewData.value = Array.isArray(res) ? res : (res.rows || [])
    showPreview.value = true
  } catch (e: any) { Message.error(e._message || '预览失败') }
}

async function pullSource(id: string) {
  try {
    await datasourceApi.pull(id)
    Message.success('拉取成功')
  } catch (e: any) { Message.error(e._message || '拉取失败') }
}

// ── 字段映射 ──
type MappingEntry = { source: string; target: string; type?: string }
const showMapping = ref(false)
const mappingSaving = ref(false)
const mappingEntries = ref<MappingEntry[]>([])
const mappingErrors = ref<Array<{ source?: boolean; target?: boolean }>>([])
const mappingFormError = ref('')
const mappingSourceId = ref('')

function openFieldMapping(record: DatasourceRecord) {
  mappingSourceId.value = record.id
  const existing: any = record.config?.field_mapping || {}
  // 兼容老结构 {src: 'tgt'} 和新结构 {src: {target: 'tgt', type: 'string'}}
  mappingEntries.value = Object.entries(existing).map(([source, v]: [string, any]) => {
    if (typeof v === 'object' && v !== null) return { source, target: String(v.target || ''), type: v.type || 'string' }
    return { source, target: String(v), type: 'string' }
  })
  if (!mappingEntries.value.length) mappingEntries.value.push({ source: '', target: '', type: 'string' })
  mappingErrors.value = []
  mappingFormError.value = ''
  showMapping.value = true
}

function addMappingRow() {
  mappingEntries.value.push({ source: '', target: '', type: 'string' })
}

/** 必填校验：返回 true = 可提交 */
function validateMapping(): boolean {
  const errors: Array<{ source?: boolean; target?: boolean }> = []
  let hasErr = false
  mappingEntries.value.forEach((m) => {
    const e: { source?: boolean; target?: boolean } = {}
    if (!m.source?.trim()) { e.source = true; hasErr = true }
    if (!m.target?.trim()) { e.target = true; hasErr = true }
    errors.push(e)
  })
  mappingErrors.value = errors
  mappingFormError.value = hasErr ? '有字段未填写：源字段和目标字段都必填' : ''
  return !hasErr
}

async function saveMapping() {
  // 保存前再次校验（@ok 不会自动触发 on-before-ok 的返回值）
  if (!validateMapping()) return
  const mapping: Record<string, { target: string; type: string }> = {}
  mappingEntries.value.forEach(m => {
    if (m.source.trim() && m.target.trim()) {
      mapping[m.source.trim()] = { target: m.target.trim(), type: m.type || 'string' }
    }
  })
  mappingSaving.value = true
  try {
    await datasourceApi.update(mappingSourceId.value, { config: { field_mapping: mapping } })
    Message.success('字段映射已保存')
    showMapping.value = false
    loadSources()
  } catch (e: any) { Message.error(e._message || '保存失败') }
  finally { mappingSaving.value = false }
}

onMounted(() => {
  loadSources()
})
</script>

<style scoped>
/* 设计稿 page chrome 覆盖 */
.datasource-list-page {
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}
.datasource-list-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.datasource-list-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
}
.datasource-list-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.datasource-list-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

/* a-tag → ai-pill */
.datasource-list-page :deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.datasource-list-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
.datasource-list-page :deep(.arco-tag-color-arcoblue),
.datasource-list-page :deep(.arco-tag-color-blue) {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}
.datasource-list-page :deep(.arco-tag-color-orange) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.datasource-list-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}

/* 表格密集化 */
.datasource-list-page :deep(.arco-table-th) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11px !important;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 8px 10px !important;
}
.datasource-list-page :deep(.arco-table-td) {
  font-size: 12.5px;
  padding: 10px 10px !important;
  border-bottom: 1px solid var(--ai-border) !important;
}
.datasource-list-page :deep(.arco-table-tr:hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

.datasource-list-page :deep(.arco-btn) {
  border-radius: 6px;
  font-weight: 500;
  font-size: 12.5px;
  box-shadow: none;
}
.datasource-list-page :deep(.arco-btn-primary) {
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
}
.datasource-list-page :deep(.arco-btn-primary:hover) {
  background: #000;
  border-color: #000;
}

/* 映射编辑器 (Modal 内) */
.mapping-head {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--ai-ink-4);
  margin-bottom: 4px;
  padding-left: 2px;
}
.mapping-head-col { flex: 1; }
.mapping-head-type { flex: 0 0 120px; }

.mapping-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
  padding: 4px;
  border-radius: var(--ai-radius-s);
  cursor: pointer;
  transition: background 0.15s ease;
}
.mapping-row:hover {
  background: var(--ai-surface-2);
}
.mapping-input { flex: 1; font-family: var(--ai-font-mono); }
.mapping-arrow { color: var(--ai-ink-4); flex: 0 0 auto; }
.mapping-type { flex: 0 0 120px; }

.mapping-form-error {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 8px 0;
  padding: 8px 10px;
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-radius: var(--ai-radius-s);
  font-size: 12px;
  font-weight: 500;
}
</style>
