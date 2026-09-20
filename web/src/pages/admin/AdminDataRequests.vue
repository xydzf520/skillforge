<template>
  <div class="page-container admin-data-requests-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">管理后台 · 数据与服务</div>
        <h2 class="page-title">数据访问审批</h2>
        <p class="page-subtitle">我作为数据源负责人（或管理员）需要审批的访问申请</p>
      </div>
      <a-space>
        <a-button class="ai-btn-like" @click="load" :loading="loading">
          <icon-refresh /> 刷新
        </a-button>
      </a-space>
    </div>

    <a-card class="page-list-card">
      <a-spin :loading="loading" class="full-spin">
        <a-table class="page-list-table" :data="requests" :pagination="false" row-key="id" size="medium">
          <template #empty>
            <SfEmptyState title="数据访问审批" description="暂无待审批的申请" hint="新申请会在数据源负责人或管理员视图中显示。" icon="inbox" />
          </template>
          <template #columns>
            <a-table-column title="申请 ID" data-index="id" :width="90">
              <template #cell="{ record }">
                <span class="request-id">#{{ record.id }}</span>
              </template>
            </a-table-column>
            <a-table-column title="数据源" :width="220">
              <template #cell="{ record }">
                <div class="cell-stack">
                  <span class="cell-strong">{{ record.source_name || record.source_id }}</span>
                  <span class="cell-sub mono-id">{{ record.source_id }}</span>
                  <span v-if="record.source_department" class="cell-sub">{{ record.source_department }}</span>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="申请人" :width="180">
              <template #cell="{ record }">
                <div class="cell-stack">
                  <span class="cell-strong">{{ record.requester_name || record.requester_id }}</span>
                  <span class="cell-sub mono-id">{{ record.requester_id }}</span>
                </div>
              </template>
            </a-table-column>
            <a-table-column title="理由">
              <template #cell="{ record }">
                <div class="reason-text">{{ record.reason }}</div>
              </template>
            </a-table-column>
            <a-table-column title="申请时间" :width="170">
              <template #cell="{ record }">
                <span class="mono-time">{{ formatTime(record.created_at) }}</span>
              </template>
            </a-table-column>
            <a-table-column title="状态" :width="100">
              <template #cell="{ record }">
                <a-tag size="small" :color="statusColor(record.status)">{{ statusLabel(record.status) }}</a-tag>
              </template>
            </a-table-column>
            <a-table-column title="操作" :width="190">
              <template #cell="{ record }">
                <a-space v-if="record.status === 'pending'">
                  <a-button type="text" size="small" status="success" @click="openApprove(record)">
                    通过
                  </a-button>
                  <a-button type="text" size="small" status="danger" @click="openReject(record)">
                    驳回
                  </a-button>
                </a-space>
                <span v-else class="decided-note">{{ record.decision_comment || '-' }}</span>
              </template>
            </a-table-column>
          </template>
        </a-table>
      </a-spin>
    </a-card>

    <!-- 批准弹窗 -->
    <a-modal
      v-model:visible="approveVisible"
      title="批准访问申请"
      :ok-loading="acting"
      ok-text="通过"
      @ok="doApprove"
      @cancel="closeApprove"
    >
      <a-form :model="approveForm" layout="vertical">
        <a-form-item label="授权有效期（可选）" help="不填默认 90 天">
          <a-date-picker v-model="approveForm.expires_at" format="YYYY-MM-DD" class="expires-picker" />
        </a-form-item>
        <a-form-item label="备注（可选）">
          <a-textarea v-model="approveForm.comment" :max-length="200" :auto-size="{ minRows: 2 }" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 驳回弹窗 -->
    <a-modal
      v-model:visible="rejectVisible"
      title="驳回访问申请"
      :ok-loading="acting"
      ok-text="驳回"
      :ok-button-props="{ status: 'danger', disabled: !rejectForm.comment.trim() }"
      @ok="doReject"
      @cancel="closeReject"
    >
      <a-form :model="rejectForm" layout="vertical">
        <a-form-item label="驳回理由" required>
          <a-textarea
            v-model="rejectForm.comment"
            :max-length="200"
            :auto-size="{ minRows: 3 }"
            placeholder="请说明驳回原因（必填）"
          />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconRefresh } from '@arco-design/web-vue/es/icon'
import { datasourceApi } from '@/api'
import { formatTime as formatBjtTime } from '@/utils/format'
import { SfEmptyState } from '@/components/common'

const loading = ref(false)
const acting = ref(false)
const requests = ref<any[]>([])

const approveVisible = ref(false)
const rejectVisible = ref(false)
const currentRequest = ref<any>(null)

const approveForm = reactive({ expires_at: '', comment: '' })
const rejectForm = reactive({ comment: '' })

async function load() {
  loading.value = true
  try {
    requests.value = (await datasourceApi.listPendingRequests()) as any[]
  } catch {
    requests.value = []
  } finally {
    loading.value = false
  }
}

function openApprove(req: any) {
  currentRequest.value = req
  approveForm.expires_at = ''
  approveForm.comment = ''
  approveVisible.value = true
}
function closeApprove() {
  approveVisible.value = false
  currentRequest.value = null
}

async function doApprove() {
  if (!currentRequest.value) return
  acting.value = true
  try {
    const body: any = { comment: approveForm.comment || undefined }
    if (approveForm.expires_at) {
      body.expires_at = approveForm.expires_at
    }
    await datasourceApi.approveRequest(currentRequest.value.id, body)
    Message.success('已批准')
    approveVisible.value = false
    await load()
  } catch (e: any) {
    Message.error(e?._message || '批准失败')
  } finally {
    acting.value = false
  }
}

function openReject(req: any) {
  currentRequest.value = req
  rejectForm.comment = ''
  rejectVisible.value = true
}
function closeReject() {
  rejectVisible.value = false
  currentRequest.value = null
}

async function doReject() {
  if (!currentRequest.value || !rejectForm.comment.trim()) return
  acting.value = true
  try {
    await datasourceApi.rejectRequest(currentRequest.value.id, {
      comment: rejectForm.comment.trim(),
    })
    Message.success('已驳回')
    rejectVisible.value = false
    await load()
  } catch (e: any) {
    Message.error(e?._message || '驳回失败')
  } finally {
    acting.value = false
  }
}

function statusLabel(s: string) {
  const m: Record<string, string> = {
    pending: '待审批',
    approved: '已通过',
    rejected: '已驳回',
    expired: '已过期',
  }
  return m[s] || s
}

// 状态 → arco color：通过 :deep(.arco-tag-color-xxx) 映射到 ai-pill 调色板
function statusColor(s: string): string {
  switch (s) {
    case 'pending': return 'orange'
    case 'approved': return 'green'
    case 'rejected': return 'red'
    case 'expired': return 'gray'
    default: return 'arcoblue'
  }
}

function formatTime(iso: string) {
  if (!iso) return '-'
  const formatted = formatBjtTime(iso)
  return formatted === '-' ? iso : formatted
}

onMounted(load)
</script>

<style scoped>
/* 设计稿 page chrome 覆盖 —— 与 AdminUsers / ReviewList 同模式 */
.admin-data-requests-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.admin-data-requests-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
}
.admin-data-requests-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.admin-data-requests-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

/* 顶部按钮统一外形 —— 30px / 6px 圆角 / 12.5px 字号 */
.admin-data-requests-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary)) {
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
.admin-data-requests-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}

/* 表头 —— 11.5px / uppercase / ink-4 */
.admin-data-requests-page :deep(.arco-table-th) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 8px 12px !important;
}
.admin-data-requests-page :deep(.arco-table-th .arco-table-th-title) {
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* 单元格 —— 12.5px / ink-1 / ai-border 底分隔线 */
.admin-data-requests-page :deep(.arco-table-td) {
  font-size: 12.5px !important;
  color: var(--ai-ink-1) !important;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 10px 12px !important;
  background: var(--ai-surface) !important;
}

/* 行 hover：surface-2 */
.admin-data-requests-page :deep(.arco-table-tr:hover .arco-table-td),
.admin-data-requests-page :deep(.arco-table-tr-hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

/* tag pills —— 20px 高 / 4px 方角 / 11px/500 —— 按 arcoblue / green / red / orange / gray 五档 */
.admin-data-requests-page :deep(.arco-tag.arco-tag-size-small),
.admin-data-requests-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
}
.admin-data-requests-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.admin-data-requests-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.admin-data-requests-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.admin-data-requests-page :deep(.arco-tag-color-orange),
.admin-data-requests-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.admin-data-requests-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-2) !important;
  border-color: transparent !important;
}

/* 文本按钮：扁平化，hover surface-2 */
.admin-data-requests-page :deep(.arco-btn-text) {
  color: var(--ai-ink-2);
  font-size: 12px;
  font-weight: 500;
}
.admin-data-requests-page :deep(.arco-btn-text:hover) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}
.admin-data-requests-page :deep(.arco-btn-text.arco-btn-status-success) {
  color: var(--ai-ok);
}
.admin-data-requests-page :deep(.arco-btn-text.arco-btn-status-success:hover) {
  color: var(--ai-ok);
  background: var(--ai-ok-soft);
}
.admin-data-requests-page :deep(.arco-btn-text.arco-btn-status-danger) {
  color: var(--ai-bad);
}
.admin-data-requests-page :deep(.arco-btn-text.arco-btn-status-danger:hover) {
  color: var(--ai-bad);
  background: var(--ai-bad-soft);
}

/* 申请 ID 用 mono */
.request-id {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--ai-ink-3);
  font-size: 12px;
  font-weight: 500;
}

/* 数据源 / 申请人 单元格 —— ID 用 mono，name 普通 */
.cell-stack {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.cell-strong {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.cell-sub {
  font-size: 11.5px;
  color: var(--ai-ink-4);
}
.mono-id {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

/* 申请时间 mono */
.mono-time {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--ai-ink-2);
  font-size: 12px;
}

/* 理由文本：限宽，可换行 */
.reason-text {
  max-width: 420px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12.5px;
  color: var(--ai-ink-2);
  line-height: 1.55;
}

/* 已决定单元格的备注 */
.decided-note {
  color: var(--ai-ink-4);
  font-size: 12px;
}

/* Admin sweep utilities */
.full-spin {
  width: 100%;
}
.expires-picker {
  width: 220px;
}
@media (max-width: 900px) {
  .expires-picker,
  .admin-data-requests-page :deep(.arco-modal) {
    width: 100%;
  }
  .reason-text {
    max-width: none;
  }
}

</style>
