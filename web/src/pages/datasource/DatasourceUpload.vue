<template>
  <div class="page-container datasource-upload-page">
    <div class="page-header">
      <div>
        <a-button type="text" size="small" class="back-btn" @click="$router.push('/datasources')"><icon-left /> 返回</a-button>
        <div class="page-kicker">数据 · 数据源 · 上传</div>
        <h2 class="page-title">数据上传</h2>
        <p class="page-subtitle">上传 CSV、查看质量报告与历史记录</p>
      </div>
    </div>

    <a-row :gutter="16">
      <a-col :xs="24" :md="12">
        <a-card title="上传CSV" class="page-section-card">
          <!-- 第一步：选择文件（无预览时显示） -->
          <div v-if="!previewData">
            <a-upload
              :auto-upload="false"
              accept=".csv"
              :file-list="[]"
              @change="handleFileSelect"
            >
              <template #upload-button>
                <a-button type="primary" :loading="previewing">
                  <icon-upload /> 选择CSV文件
                </a-button>
              </template>
            </a-upload>
          </div>

          <!-- 第二步：预览 + 确认 -->
          <div v-else>
            <a-alert type="info" style="margin-bottom: 12px">
              文件: {{ previewData.file_name }} | 预计 {{ previewData.estimated_total }} 行 | {{ previewData.columns.length }} 列
            </a-alert>

            <a-table
              :data="previewData.preview_rows"
              :pagination="false"
              size="small"
              :scroll="{ x: true }"
            >
              <template #columns>
                <a-table-column
                  v-for="col in previewData.columns"
                  :key="col"
                  :data-index="col"
                  :width="140"
                >
                  <template #title>
                    {{ col }}
                    <a-tag size="small" :color="(typeColor as Record<string, string>)[previewData.col_types[col]]">
                      {{ (typeLabel as Record<string, string>)[previewData.col_types[col]] || previewData.col_types[col] }}
                    </a-tag>
                  </template>
                </a-table-column>
              </template>
            </a-table>

            <div style="margin-top: 16px; display: flex; gap: 8px;">
              <a-button type="primary" @click="confirmUpload" :loading="uploading">确认上传</a-button>
              <a-button @click="resetPreview">重新选择</a-button>
            </div>
          </div>

          <div v-if="uploadResult" style="margin-top: 16px">
            <a-alert :type="uploadResult.success ? 'success' : 'error'">
              {{ uploadResult.message }}
            </a-alert>
          </div>
        </a-card>

        <!-- 质量报告 -->
        <a-card title="数据质量" class="page-section-card" style="margin-top: 16px">
          <a-spin :loading="qualityLoading">
            <div v-if="quality">
              <a-descriptions :column="1" size="small">
                <a-descriptions-item label="总行数">{{ quality.total_rows }}</a-descriptions-item>
                <a-descriptions-item label="有效行">{{ quality.valid_rows }}</a-descriptions-item>
                <a-descriptions-item label="缺失值">{{ quality.missing_count }}</a-descriptions-item>
                <a-descriptions-item label="重复行">{{ quality.duplicate_count }}</a-descriptions-item>
              </a-descriptions>
              <div v-if="quality.issues && quality.issues.length" style="margin-top: 8px">
                <a-alert v-for="(issue, i) in quality.issues" :key="i" type="warning" style="margin-bottom: 4px">
                  {{ issue }}
                </a-alert>
              </div>
            </div>
            <a-empty v-else description="上传后显示质量报告" />
          </a-spin>
        </a-card>
      </a-col>

      <!-- 历史记录 -->
      <a-col :xs="24" :md="12">
        <a-card title="上传历史" class="page-section-card">
          <a-spin :loading="historyLoading">
            <a-table v-if="historyData.length" :data="historyData" :pagination="false" size="small">
              <template #columns>
                <a-table-column title="时间" data-index="created_at" :width="130">
                  <template #cell="{ record }">{{ formatTimeShort(record.created_at) }}</template>
                </a-table-column>
                <a-table-column title="文件" data-index="filename" />
                <a-table-column title="行数" data-index="row_count" :width="70" />
                <a-table-column title="状态" data-index="status" :width="80">
                  <template #cell="{ record }">
                    <a-tag :color="record.status === 'success' ? 'green' : 'red'" size="small">{{ record.status }}</a-tag>
                  </template>
                </a-table-column>
              </template>
            </a-table>
            <a-empty v-else description="暂无历史" />
          </a-spin>
        </a-card>
      </a-col>
    </a-row>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { datasourceApi as rawDatasourceApi } from '@/api'
import { formatTimeShort } from '@/utils/format'
import { IconLeft, IconUpload } from '@arco-design/web-vue/es/icon'

const datasourceApi: any = rawDatasourceApi
const route: any = useRoute()
const sourceId: string = route.params.id

// 预览/上传状态
const selectedFile = ref<File | null>(null)
const previewData = ref<any>(null)
const previewing = ref(false)
const uploading = ref(false)
const uploadResult = ref<any>(null)

// 列类型颜色映射
const typeColor = { number: 'arcoblue', date: 'purple', text: '' }
const typeLabel = { number: '数值', date: '日期', text: '文本' }

// 质量报告 & 历史
const quality = ref<any>(null)
const qualityLoading = ref(false)
const historyData = ref<any[]>([])
const historyLoading = ref(false)

async function handleFileSelect(fileList: any, fileItem: any) {
  const file = fileItem.file as File
  if (!file) return
  selectedFile.value = file
  uploadResult.value = null
  previewing.value = true
  try {
    const formData = new FormData()
    formData.append('file', file)
    previewData.value = await datasourceApi.previewUpload(sourceId, formData)
  } catch (e: any) {
    Message.error(e?._message || '文件预览失败')
    selectedFile.value = null
  } finally {
    previewing.value = false
  }
}

async function confirmUpload() {
  if (!selectedFile.value) return
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    await datasourceApi.upload(sourceId, formData)
    uploadResult.value = { success: true, message: '上传成功' }
    Message.success('上传成功')
    resetPreview()
    loadQuality()
    loadHistory()
  } catch (e: any) {
    uploadResult.value = { success: false, message: '上传失败：' + ((e instanceof Error ? e.message : String(e)) || '未知错误') }
  } finally {
    uploading.value = false
  }
}

function resetPreview() {
  selectedFile.value = null
  previewData.value = null
}

async function loadQuality() {
  qualityLoading.value = true
  try { quality.value = await datasourceApi.quality(sourceId) } catch (e: any) { /* 质量数据非关键 */ } finally { qualityLoading.value = false }
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const res = await datasourceApi.history(sourceId)
    historyData.value = Array.isArray(res) ? res : (res.items || [])
  } finally { historyLoading.value = false }
}

onMounted(() => { loadQuality(); loadHistory() })
</script>

<style scoped>
.datasource-upload-page {
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}
.datasource-upload-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.datasource-upload-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
}
.datasource-upload-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.datasource-upload-page :deep(.page-section-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}
.datasource-upload-page :deep(.page-section-card .arco-card-header-title) {
  font-weight: 500 !important;
  font-size: 13px !important;
  color: var(--ai-ink-1) !important;
}
.back-btn {
  color: var(--ai-ink-4);
  font-size: 12px;
  margin-bottom: 4px;
  padding: 0;
}
.back-btn:hover {
  color: var(--ai-ink-1);
  background: transparent;
}

/* a-tag → ai-pill */
.datasource-upload-page :deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.datasource-upload-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}
.datasource-upload-page :deep(.arco-tag-color-purple) {
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
  border-color: transparent;
}
.datasource-upload-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
.datasource-upload-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}

/* table 密集化 */
.datasource-upload-page :deep(.arco-table-th) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11px !important;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 8px 10px !important;
}
.datasource-upload-page :deep(.arco-table-td) {
  font-size: 12.5px;
  padding: 10px 10px !important;
  border-bottom: 1px solid var(--ai-border) !important;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

/* descriptions */
.datasource-upload-page :deep(.arco-descriptions-item-label) {
  color: var(--ai-ink-4) !important;
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.datasource-upload-page :deep(.arco-descriptions-item-value) {
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

/* alerts */
.datasource-upload-page :deep(.arco-alert) {
  border-radius: var(--ai-radius);
  border: 1px solid var(--ai-border);
}
.datasource-upload-page :deep(.arco-alert-info) {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}
.datasource-upload-page :deep(.arco-alert-warning) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.datasource-upload-page :deep(.arco-alert-success) {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
.datasource-upload-page :deep(.arco-alert-error) {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}

/* buttons */
.datasource-upload-page :deep(.arco-btn) {
  border-radius: 6px;
  font-weight: 500;
  font-size: 12.5px;
  box-shadow: none;
}
.datasource-upload-page :deep(.arco-btn-primary) {
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
}
.datasource-upload-page :deep(.arco-btn-primary:hover) {
  background: #000;
  border-color: #000;
}
</style>
