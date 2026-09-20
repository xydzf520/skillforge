<template>
  <div class="page-container page-wide admin-platform-apis-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">管理后台 · 数据与服务</div>
        <h2 class="page-title">平台 API 注册表</h2>
        <p class="page-subtitle">AI 助手首次抓包时自动登记，之后所有 Skill 共享使用。</p>
      </div>
      <a-space>
        <a-button class="ai-btn-like" @click="exportPlatformApis"><icon-download /> 导出</a-button>
        <a-button class="ai-btn-like" @click="triggerImport"><icon-upload /> 导入</a-button>
        <a-button class="ai-btn-like" @click="loadPlatformApis"><icon-refresh /> 刷新</a-button>
      </a-space>
      <input ref="importFileRef" type="file" accept=".json" class="visually-hidden-file" @change="handleImportFile" />
    </div>

    <a-card class="page-list-card">
      <div class="section-head">
        <h3>已发现</h3>
        <a-tag size="small" color="gray">{{ platformApis.length }}</a-tag>
      </div>

      <div v-if="platformApis.length === 0" class="empty-hint">
        暂无平台 API 缓存。AI 助手首次抓包时会自动注册。
      </div>

      <a-table
        v-else
        :data="platformApis"
        :pagination="{ pageSize: 20, showTotal: true }"
        size="small"
        :bordered="false"
        row-key="id"
      >
        <template #columns>
          <a-table-column title="平台" :width="200">
            <template #cell="{ record }">
              <span class="api-domain">{{ record.domain }}</span>
            </template>
          </a-table-column>
          <a-table-column title="页面">
            <template #cell="{ record }">
              <div class="page-cell">
                <span class="page-name">{{ record.page_title || record.page_path }}</span>
                <div v-if="record.page_title" class="api-page-path">{{ record.page_path }}</div>
              </div>
            </template>
          </a-table-column>
          <a-table-column title="API数" :width="80" align="center">
            <template #cell="{ record }">
              <a-tag size="small" color="arcoblue">{{ record.api_count }}</a-tag>
            </template>
          </a-table-column>
          <a-table-column title="更新时间" :width="120">
            <template #cell="{ record }">
              <span class="muted-cell">{{ record.updated_at ? record.updated_at.slice(0, 10) : '-' }}</span>
            </template>
          </a-table-column>
          <a-table-column title="操作" :width="80" align="center">
            <template #cell="{ record }">
              <a-button type="text" size="mini" @click="viewPlatformApi(record)">详情</a-button>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </a-card>

    <a-modal v-model:visible="showPlatformApiModal" :title="`${platformApiDetail.domain}${platformApiDetail.page_path}`" :width="'min(90vw, 720px)'" :footer="false">
      <div class="modal-meta-block">
        <a-descriptions :column="2" size="small" bordered>
          <a-descriptions-item label="页面标题">{{ platformApiDetail.page_title || '-' }}</a-descriptions-item>
          <a-descriptions-item label="API 数量">{{ platformApiDetail.api_count }}</a-descriptions-item>
          <a-descriptions-item label="发现方式">{{ platformApiDetail.discovery_method || '-' }}</a-descriptions-item>
          <a-descriptions-item label="更新时间">{{ platformApiDetail.updated_at || '-' }}</a-descriptions-item>
        </a-descriptions>
      </div>
      <div class="modal-section-title">数据 API 列表</div>
      <a-table
        :data="platformApiDetail._apis || []"
        :pagination="{ pageSize: 10, showTotal: true }"
        size="mini"
        row-key="url"
      >
        <template #columns>
          <a-table-column title="方法" :width="62">
            <template #cell="{ record }">
              <a-tag size="small" :color="methodColor(record.method)">{{ record.method || '?' }}</a-tag>
            </template>
          </a-table-column>
          <a-table-column title="URL">
            <template #cell="{ record }">
              <span class="api-url">{{ (record.url || '').split('?')[0] }}</span>
            </template>
          </a-table-column>
          <a-table-column title="来源" :width="65">
            <template #cell="{ record }">
              <a-tag size="small" :color="record.source === 'intercepted' ? 'green' : 'gray'">{{ record.source === 'intercepted' ? '拦截' : 'perf' }}</a-tag>
            </template>
          </a-table-column>
          <a-table-column title="响应字段" :width="200">
            <template #cell="{ record }">
              <span class="response-keys">{{ (record.response_keys || record.data_keys || []).slice(0, 4).join(', ') }}</span>
            </template>
          </a-table-column>
        </template>
      </a-table>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconRefresh, IconDownload, IconUpload } from '@arco-design/web-vue/es/icon'
import request from '@/api/request'
import { bjtDateString } from '@/utils/format'

defineOptions({ name: 'AdminPlatformApis' })

interface PlatformApiRow {
  id: string
  domain: string
  page_path: string
  page_title?: string
  api_count: number
  updated_at?: string
}

interface PlatformApiDetail {
  domain: string
  page_path: string
  page_title: string
  api_count: number
  discovery_method: string
  updated_at: string
  _apis: Array<{ method?: string; url?: string; source?: string; response_keys?: string[]; data_keys?: string[] }>
}

const platformApis = ref<PlatformApiRow[]>([])
const showPlatformApiModal = ref(false)

const platformApiDetail = reactive<PlatformApiDetail>({
  domain: '',
  page_path: '',
  page_title: '',
  api_count: 0,
  discovery_method: '',
  updated_at: '',
  _apis: [],
})

// 按 HTTP 方法分配 ai-pill 颜色：GET→green(ok)，POST→arcoblue(info)，DELETE→red(bad)，PUT/PATCH→orange(warn)，其它→gray
function methodColor(method?: string): string {
  const m = (method || '').toUpperCase()
  if (m === 'GET') return 'green'
  if (m === 'POST') return 'arcoblue'
  if (m === 'DELETE') return 'red'
  if (m === 'PUT' || m === 'PATCH') return 'orange'
  return 'gray'
}

async function loadPlatformApis() {
  try {
    const res = await request.get<{ platforms?: PlatformApiRow[] }>('/browser/platform-apis')
    platformApis.value = res.platforms || []
  } catch (e) {
    console.warn('[platform-apis] 加载失败', e)
  }
}

async function viewPlatformApi(record: PlatformApiRow) {
  try {
    const res = await request.get<{
      domain?: string
      page_path?: string
      page_title?: string
      api_count?: number
      discovery_method?: string
      updated_at?: string
      apis_json?: { data_apis?: PlatformApiDetail['_apis'] }
    }>(`/browser/platform-apis/${record.id}`)
    platformApiDetail.domain = res.domain || ''
    platformApiDetail.page_path = res.page_path || ''
    platformApiDetail.page_title = res.page_title || ''
    platformApiDetail.api_count = res.api_count || 0
    platformApiDetail.discovery_method = res.discovery_method || ''
    platformApiDetail.updated_at = res.updated_at || ''
    platformApiDetail._apis = (res.apis_json || {}).data_apis || []
    showPlatformApiModal.value = true
  } catch (e) {
    Message.error(String((e as { _message?: string })?._message || '加载详情失败'))
  }
}

const importFileRef = ref<HTMLInputElement>()

function triggerImport() {
  importFileRef.value?.click()
}

async function handleImportFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    const text = await file.text()
    const json = JSON.parse(text)
    if (json.export_type !== 'platform_apis') {
      Message.error('文件格式不匹配，需要 platform_apis 导出文件')
      return
    }
    const res = await request.post<{ message: string; total: number }>(
      '/browser/platform-apis/import', json,
    )
    Message.success(res.message)
    await loadPlatformApis()
  } catch (e) {
    Message.error(String((e as { _message?: string })?._message || '导入失败'))
  } finally {
    if (importFileRef.value) importFileRef.value.value = ''
  }
}

async function exportPlatformApis() {
  try {
    const res = await request.get<{ export_type: string; exported_at: string; total: number; data: unknown[] }>(
      '/browser/platform-apis/export',
    )
    const blob = new Blob([JSON.stringify(res, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `platform-apis-${bjtDateString(Date.now())}.json`
    a.click()
    URL.revokeObjectURL(url)
    Message.success(`已导出 ${res.total} 条`)
  } catch (e) {
    Message.error(String((e as { _message?: string })?._message || '导出失败'))
  }
}

onMounted(loadPlatformApis)
</script>

<style scoped>
/* 设计稿 page chrome 覆盖 —— 与 AdminUsers 同模式 */
.admin-platform-apis-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.admin-platform-apis-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
}
.admin-platform-apis-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.admin-platform-apis-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

/* 顶部按钮统一外形 */
.admin-platform-apis-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary)) {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
  box-shadow: none;
}
.admin-platform-apis-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}

/* 卡片内 section 头 */
.section-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.section-head h3 {
  margin: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}

.empty-hint {
  color: var(--ai-ink-4);
  font-size: 12px;
  padding: 8px 0;
}

/* 表头 —— 11.5px / uppercase / ink-4 */
.admin-platform-apis-page :deep(.arco-table-th) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 8px 12px !important;
}
.admin-platform-apis-page :deep(.arco-table-th .arco-table-th-title) {
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* 单元格 —— 12.5px / ink-1 / ai-border 底分隔线 */
.admin-platform-apis-page :deep(.arco-table-td) {
  font-size: 12.5px !important;
  color: var(--ai-ink-1) !important;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 10px 12px !important;
  background: var(--ai-surface) !important;
}

/* 行 hover：surface-2 */
.admin-platform-apis-page :deep(.arco-table-tr:hover .arco-table-td),
.admin-platform-apis-page :deep(.arco-table-tr-hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

/* a-tag → ai-pill 风格映射 */
.admin-platform-apis-page :deep(.arco-tag.arco-tag-size-small),
.admin-platform-apis-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
  letter-spacing: 0;
}
.admin-platform-apis-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.admin-platform-apis-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.admin-platform-apis-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.admin-platform-apis-page :deep(.arco-tag-color-orange),
.admin-platform-apis-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.admin-platform-apis-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-3) !important;
  border-color: transparent !important;
}

/* HTTP 方法 tag —— 强制 mono 字体增强识别度 */
.admin-platform-apis-page :deep(.arco-tag-color-arcoblue),
.admin-platform-apis-page :deep(.arco-tag-color-green),
.admin-platform-apis-page :deep(.arco-tag-color-red),
.admin-platform-apis-page :deep(.arco-tag-color-orange) {
  font-family: var(--ai-font-mono);
}

/* 平台域名 / 页面路径 / API URL —— 全部 mono */
.api-domain {
  font-family: var(--ai-font-mono);
  font-weight: 500;
  font-size: 12.5px;
  color: var(--ai-ink-1);
}
.page-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.page-name {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.api-page-path {
  font-size: 11px;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
}
.api-url {
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-1);
  word-break: break-all;
}
.response-keys {
  font-size: 11px;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
}
.muted-cell {
  font-size: 12px;
  color: var(--ai-ink-4);
}

/* Modal 区块 */
.modal-meta-block {
  margin-bottom: 12px;
}
.modal-section-title {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ai-ink-1);
  margin-bottom: 8px;
}

/* Descriptions 内字号 / 颜色对齐 */
.admin-platform-apis-page :deep(.arco-descriptions-item-label) {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 500;
}
.admin-platform-apis-page :deep(.arco-descriptions-item-value) {
  color: var(--ai-ink-1);
  font-size: 12.5px;
}

/* Admin sweep utilities */
.visually-hidden-file {
  display: none;
}
@media (max-width: 900px) {
  .admin-platform-apis-page :deep(.arco-space) {
    row-gap: 8px;
  }
}

</style>
