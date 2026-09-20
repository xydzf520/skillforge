<template>
  <div class="page-container review-list-page">
    <aside class="review-sidebar ai-sidebar" :class="{ 'review-sidebar--open': sidebarOpen }">
      <div class="review-sidebar-head">
        <span class="review-sidebar-title">审核中心</span>
        <button class="review-sidebar-close" type="button" aria-label="关闭导航" @click="sidebarOpen = false">
          <SfShellIcon name="x" />
        </button>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">Skills 模块</div>
        <router-link class="ai-side-item" to="/skills">
          <SfShellIcon name="cube" class="ic" />
          <span>Skills</span>
        </router-link>
        <router-link class="ai-side-item" to="/portal">
          <SfShellIcon name="grid" class="ic" />
          <span>应用门户</span>
        </router-link>
        <button class="ai-side-item active" type="button" @click="clearReviewFilters">
          <SfShellIcon name="check" class="ic" />
          <span>审核中心</span>
          <span class="count">{{ stats.pending }}</span>
        </button>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">视图</div>
        <button
          v-for="item in reviewStatusOptions"
          :key="item.key || 'all'"
          class="ai-side-item"
          :class="{ active: (filters.status || '') === item.key }"
          type="button"
          @click="setStatus(item.key || undefined)"
        >
          <span class="deptdot" :class="`dot-${item.tone}`" />
          <span>{{ item.label }}</span>
          <span class="count">{{ item.count }}</span>
        </button>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">变更类型</div>
        <button
          v-for="item in changeTypeOptions"
          :key="item.key"
          class="ai-side-item"
          :class="{ active: filters.change_type === item.key }"
          type="button"
          @click="setChangeType(filters.change_type === item.key ? undefined : item.key)"
        >
          <span>{{ item.label }}</span>
          <span class="count">{{ item.count }}</span>
        </button>
      </div>
    </aside>

    <main class="review-main ai-main">
      <button class="review-sidebar-toggle" type="button" @click="sidebarOpen = !sidebarOpen" aria-label="打开导航">
        <SfShellIcon name="list" />
        <span>{{ sidebarLabel }}</span>
      </button>

      <div class="ai-pagehead review-pagehead">
        <div>
          <div class="ai-crumbs">Skills · 审核中心</div>
          <h1 class="ai-title">审核中心</h1>
          <p class="ai-sub">处理 Skill / Playbook 变更发布审核</p>
        </div>
        <div class="review-head-actions">
          <button
            type="button"
            class="ai-btn"
            :disabled="bulkApproving || !selectedPendingReviews.length"
            :aria-busy="bulkApproving"
            @click="openBulkApprove"
          >
            <SfShellIcon name="filter" />
            批量通过<template v-if="selectedPendingReviews.length"> {{ selectedPendingReviews.length }}</template>
          </button>
          <button type="button" class="ai-btn primary" @click="openReviewPolicy">
            <SfShellIcon name="check" />
            审核策略
          </button>
        </div>
      </div>

      <section class="review-shell ai-pagebody">
        <div class="review-toolbar">
          <a-input-search
            v-model="filters.q"
            placeholder="搜索 Skill ID / 摘要"
            allow-clear
            size="small"
            class="review-search"
            style="width: 280px"
            @search="applyFilters"
            @clear="applyFilters"
          />
          <a-input
            v-model="filters.submitter"
            placeholder="提交人"
            allow-clear
            size="small"
            class="review-filter-input"
            @change="applyFilters"
            @clear="applyFilters"
          />
          <a-input
            v-model="filters.reviewer"
            placeholder="审核人"
            allow-clear
            size="small"
            class="review-filter-input"
            @change="applyFilters"
            @clear="applyFilters"
          />
          <a-range-picker
            v-model="dateRange"
            size="small"
            class="date-range-picker"
            @change="onDateChange"
            @clear="onDateChange"
          />
          <div class="review-toolbar-spacer" />
          <button
            v-for="item in statusPillItems"
            :key="item.key"
            class="review-pill"
            :class="[`pill-${item.tone}`, { active: filters.status === item.key }]"
            type="button"
            @click="setStatus(item.key)"
          >
            {{ item.label }} {{ item.count }}
          </button>
        </div>

        <div class="review-table-card ai-card">
          <a-table
            class="page-list-table review-table"
            :scroll="{ x: '100%' }"
            :data="reviews"
            :pagination="false"
            row-key="id"
            :loading="loading"
            v-model:selectedKeys="selectedRowKeys"
            :row-selection="rowSelection"
          >
            <template #empty>
              <SfEmptyState
                title="审核中心"
                description="暂无审核单"
                hint="Skill 变更走发布流程时会生成审核单。当前筛选条件下没有匹配的记录；试试切换视图或清空筛选。"
              />
            </template>
            <template #columns>
              <a-table-column title="ID" :width="56">
                <template #cell="{ record }">
                  <span class="review-id">#{{ record.id }}</span>
                </template>
              </a-table-column>
              <a-table-column title="Skill" data-index="skill_id" :width="200">
                <template #cell="{ record }">
                  <button class="review-skill-link" type="button" @click="openTarget(record.skill_id)">{{ record.skill_id }}</button>
                </template>
              </a-table-column>
              <a-table-column title="变更摘要" data-index="diff_summary" ellipsis>
                <template #cell="{ record }">
                  <span class="diff-summary">{{ record.diff_summary || '-' }}</span>
                </template>
              </a-table-column>
              <a-table-column title="类型" :width="74">
                <template #cell="{ record }">
                  <a-tag size="small" :color="(changeTypeColor as Record<string, string>)[record.change_type]">
                    {{ (changeTypeLabel as Record<string, string>)[record.change_type] || record.change_type }}
                  </a-tag>
                </template>
              </a-table-column>
              <a-table-column title="提交人" data-index="submitter" :width="110" />
              <a-table-column title="审核人" :width="110">
                <template #cell="{ record }">
                  {{ record.reviewer || '—' }}
                </template>
              </a-table-column>
              <a-table-column title="状态" :width="100">
                <template #cell="{ record }">
                  <a-tag :color="(statusColor as Record<string, string>)[record.status]" size="small">{{ (statusLabel as Record<string, string>)[record.status] }}</a-tag>
                </template>
              </a-table-column>
              <a-table-column title="提交时间" :width="150">
                <template #cell="{ record }">{{ formatTime(record.created_at) }}</template>
              </a-table-column>
              <a-table-column title="操作" :width="180" fixed="right">
                <template #cell="{ record }">
                  <a-space size="mini" class="review-actions">
                    <template v-if="record.status === 'pending'">
                      <a-button
                        size="mini"
                        :loading="actingReviewId === String(record.id)"
                        :disabled="!canActReviewRecord(record)"
                        :title="reviewActionDisabledReason(record)"
                        @click.stop="openReject(record)"
                      >
                        驳回
                      </a-button>
                      <a-button size="mini" @click="openReview(record)">查看</a-button>
                      <a-button
                        type="primary"
                        size="mini"
                        :loading="actingReviewId === String(record.id)"
                        :disabled="!canActReviewRecord(record)"
                        :title="reviewActionDisabledReason(record)"
                        @click.stop="confirmApprove(record)"
                      >
                        通过
                      </a-button>
                    </template>
                    <a-button v-else size="mini" @click="openReview(record)">查看</a-button>
                  </a-space>
                </template>
              </a-table-column>
            </template>
          </a-table>
        </div>

        <div class="bottom-bar">
          <span class="review-bottom-text">共 {{ pagination.total }} 条 · 显示 {{ reviews.length }} / {{ pagination.total }}</span>
          <a-pagination :current="pagination.current" :page-size="pagination.pageSize" :total="pagination.total" @change="onPageChange" size="small" />
        </div>
      </section>
    </main>

    <a-modal
      v-model:visible="rejectVisible"
      title="驳回审核"
      ok-text="驳回"
      cancel-text="取消"
      :ok-button-props="{ status: 'danger', disabled: !rejectForm.reason.trim() }"
      :ok-loading="rejecting"
      @ok="confirmReject"
    >
      <a-form :model="rejectForm" layout="vertical">
        <a-form-item label="驳回类型">
          <a-select v-model="rejectForm.reject_reason" placeholder="请选择">
            <a-option value="quality">质量问题</a-option>
            <a-option value="logic">逻辑问题</a-option>
            <a-option value="format">格式问题</a-option>
          </a-select>
        </a-form-item>
        <a-form-item label="驳回说明" required>
          <a-textarea
            v-model="rejectForm.reason"
            placeholder="说明为什么不能通过，方便提交人继续修改"
            :auto-size="{ minRows: 3, maxRows: 6 }"
          />
        </a-form-item>
      </a-form>
    </a-modal>

    <a-modal
      v-model:visible="approveVisible"
      title="审核通过并发布"
      ok-text="确认通过"
      cancel-text="取消"
      :ok-loading="approveSubmitting"
      @ok="submitApprove"
    >
      <a-form :model="approveForm" layout="vertical">
        <div class="approve-intro">
          <div>审核单 #{{ approveTarget?.id || '-' }}</div>
          <div v-if="approveTarget && currentUserId() && approveTarget.submitter === currentUserId()" class="approve-self-tip">
            当前是管理员自审，系统会在审核记录中保留标记。
          </div>
        </div>
        <a-form-item label="反馈类型">
          <a-select v-model="approveForm.feedback_type" placeholder="可选">
            <a-option value="">未指定</a-option>
            <a-option value="ready">可发布</a-option>
            <a-option value="quality">质量稳定</a-option>
            <a-option value="logic">逻辑清晰</a-option>
          </a-select>
        </a-form-item>
        <a-form-item label="评分">
          <a-select v-model="approveForm.rating" placeholder="可选">
            <a-option value="">未评分</a-option>
            <a-option v-for="score in [5, 4, 3, 2, 1]" :key="score" :value="String(score)">
              {{ score }}
            </a-option>
          </a-select>
        </a-form-item>
        <template v-if="!isPlaybookApproveTarget">
          <a-form-item label="运行终端">
            <a-select
              v-model="approveForm.runtime_instance_id"
              allow-clear
              :loading="approveTargetsLoading"
              placeholder="可指定某个 openclaw / aiclaw 终端"
            >
              <a-option v-for="target in approveTargets" :key="target.id" :value="target.id" :disabled="target.selectable === false">
                {{ syncTargetLabel(target) }}
              </a-option>
            </a-select>
          </a-form-item>
          <a-form-item label="定时任务">
            <div class="approve-cron">
              <a-space wrap>
                <a-button size="mini" @click="setApproveCron('0 9 * * *')">每天 09:00</a-button>
                <a-button size="mini" @click="setApproveCron('0 18 * * *')">每天 18:00</a-button>
                <a-button size="mini" @click="setApproveCron('0 * * * *')">每小时</a-button>
                <a-button size="mini" @click="setApproveCron('')">清空</a-button>
              </a-space>
              <a-input
                v-model="approveForm.cron_expression"
                placeholder="留空表示不改定时；例如 0 9 * * *"
              />
            </div>
          </a-form-item>
          <a-form-item>
            <a-checkbox v-model="approveForm.verify_after_sync">
              同步后立即做一次运行验证
            </a-checkbox>
          </a-form-item>
          <a-alert
            v-if="approveForm.static_check_override"
            type="warning"
            title="静态检测命中"
            show-icon
          >
            {{ staticOverrideText }}
          </a-alert>
          <a-form-item v-if="approveForm.static_check_override" label="Override 理由" required>
            <a-textarea
              v-model="approveForm.static_check_override_reason"
              placeholder="说明业务必要性、风险判断和回滚方式"
              :auto-size="{ minRows: 3, maxRows: 6 }"
            />
            <div class="reason-meter">
              {{ approveForm.static_check_override_reason.trim().length }}/20
            </div>
          </a-form-item>
        </template>
        <div v-else class="approve-hint">
          Playbook 审核暂不支持指定运行终端和定时任务，将直接走原有发布链路。
        </div>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Message, Modal } from '@arco-design/web-vue'
import { reviewApi as rawReviewApi, aiclawApi as rawAiclawApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { formatTime } from '@/utils/format'
import { reviewStatusLabel, reviewStatusColor } from '@/utils/constants'
import { SfEmptyState } from '@/components/common'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

defineOptions({ name: 'ReviewList' })

const reviewApi: any = rawReviewApi
const aiclawApi: any = rawAiclawApi
const router = useRouter()
const userStore = useUserStore()
const loading = ref(false)
const actingReviewId = ref('')
const bulkApproving = ref(false)
const sidebarOpen = ref(false)
const selectedRowKeys = ref<Array<string | number>>([])
const approveVisible = ref(false)
const approveSubmitting = ref(false)
const approveTarget = ref<Record<string, any> | null>(null)
const approveTargetsLoading = ref(false)
const approveTargets = ref<Array<Record<string, any>>>([])
const rejectVisible = ref(false)
const rejecting = ref(false)
const rejectTarget = ref<Record<string, unknown> | null>(null)
const reviews = ref<any[]>([])
const filters = reactive<{
  status?: string
  q: string
  change_type?: string
  submitter: string
  reviewer: string
  date_from: string
  date_to: string
}>({ status: undefined, q: '', change_type: undefined, submitter: '', reviewer: '', date_from: '', date_to: '' })
const dateRange = ref<any>(undefined)
const pagination = reactive({ current: 1, pageSize: 20, total: 0 })
const stats = reactive({ total: 0, pending: 0, approved: 0, rejected: 0 })
const changeTypeStats = reactive({ new_skill: 0, update: 0, fix: 0 })
const rejectForm = reactive({ reject_reason: 'logic', reason: '' })
const approveForm = reactive({
  rating: '',
  feedback_type: '',
  runtime_instance_id: '',
  cron_expression: '',
  verify_after_sync: true,
  static_check_override: false,
  static_check_override_reason: '',
})

const statusLabel = reviewStatusLabel
const statusColor = reviewStatusColor
const changeTypeLabel = {
  params: '更新',
  logic: '更新',
  code: '修复',
  codex_submit: '更新',
  regression_case: '修复',
  fix: '修复',
  bugfix: '修复',
  new_skill: '新建',
  update: '更新',
}
const changeTypeColor = {
  params: 'cyan',
  logic: 'cyan',
  code: 'orange',
  codex_submit: 'cyan',
  regression_case: 'orange',
  fix: 'orange',
  bugfix: 'orange',
  new_skill: 'green',
  update: 'cyan',
}
const isPlaybookApproveTarget = computed(() => String(approveTarget.value?.skill_id || '').startsWith('playbook:'))
const staticOverrideDetail = ref<any>(null)
const reviewStatusOptions = computed(() => [
  { key: '', label: '全部', count: stats.total, tone: 'all' },
  { key: 'pending', label: '待审核', count: stats.pending, tone: 'warn' },
  { key: 'approved', label: '已通过', count: stats.approved, tone: 'ok' },
  { key: 'rejected', label: '已驳回', count: stats.rejected, tone: 'bad' },
])
const statusPillItems = computed(() => reviewStatusOptions.value.filter((item) => item.key))
const changeTypeOptions = computed(() => [
  { key: 'new_skill', label: '新建', count: changeTypeStats.new_skill },
  { key: 'update', label: '更新', count: changeTypeStats.update },
  { key: 'fix', label: '修复', count: changeTypeStats.fix },
])
const sidebarLabel = computed(() => {
  const status = reviewStatusOptions.value.find((item) => item.key === (filters.status || ''))?.label || '审核中心'
  const type = changeTypeOptions.value.find((item) => item.key === filters.change_type)?.label
  return type ? `${status} · ${type}` : status
})
const selectedPendingReviews = computed(() => {
  const selected = new Set(selectedRowKeys.value.map((item) => String(item)))
  return reviews.value.filter((record) => (
    selected.has(String(record.id))
    && record.status === 'pending'
    && canActReviewRecord(record)
  ))
})
const rowSelection = {
  type: 'checkbox',
  showCheckedAll: true,
}
const staticOverrideText = computed(() => {
  const detail = staticOverrideDetail.value
  if (!detail) return '需要填写 Override 理由后再次确认。'
  if (typeof detail === 'string') return detail
  const issues = Array.isArray(detail.issues) ? detail.issues.length : 0
  const blocked = detail.blocked === true ? '已阻断' : '需人工确认'
  return issues ? `${blocked}，命中 ${issues} 条静态检测。` : `${blocked}，需要填写 Override 理由后再次确认。`
})

function playbookName(skillId: string): string {
  return skillId.startsWith('playbook:') ? skillId.slice('playbook:'.length) : skillId
}

function openTarget(skillId: string) {
  if (skillId.startsWith('playbook:')) {
    router.push(`/playbook/${encodeURIComponent(playbookName(skillId))}`)
    return
  }
  router.push(`/skills/${skillId}`)
}

function openReview(record: Record<string, unknown>) {
  const reviewId = String(record.id || '')
  const skillId = String(record.skill_id || '')
  if (!reviewId) return
  if (skillId.startsWith('playbook:')) {
    router.push(`/playbook/${encodeURIComponent(playbookName(skillId))}?review_id=${reviewId}`)
    return
  }
  router.push(`/skills/${encodeURIComponent(skillId)}?perspective=review&review_id=${reviewId}`)
}

function currentUserId(): string {
  return String(userStore.userInfo?.user_id || userStore.userInfo?.username || '')
}

function canActReviewRecord(record: Record<string, unknown>): boolean {
  return !reviewActionDisabledReason(record)
}

function canSelfApproveReview(): boolean {
  return userStore.isAdmin || userStore.isSystemAdmin || Boolean(userStore.userInfo?.can_view_all)
}

function canReviewAnyRecord(): boolean {
  return canSelfApproveReview() || userStore.isEngineer || userStore.isAibp
}

function reviewActionDisabledReason(record: Record<string, unknown>): string {
  if (record.status !== 'pending') return '审核单已处理'
  const uid = currentUserId()
  const isSelfSubmitted = Boolean(uid && record.submitter === uid)
  if (isSelfSubmitted) {
    return canSelfApproveReview() ? '' : '只有系统管理员可以自审'
  }
  if (canReviewAnyRecord()) return ''
  if (uid && record.reviewer === uid) return ''
  return '只有审核人或管理员可以操作'
}

function refreshReviews() {
  return Promise.all([loadReviews(), loadStats(), loadChangeTypeStats()])
}

function baseReviewParams(options: { includeStatus?: boolean; includeChangeType?: boolean } = {}) {
  return {
    q: filters.q.trim() || undefined,
    change_type: options.includeChangeType ? (filters.change_type || undefined) : undefined,
    status: options.includeStatus ? (filters.status || undefined) : undefined,
    submitter: filters.submitter.trim() || undefined,
    reviewer: filters.reviewer.trim() || undefined,
    date_from: filters.date_from || undefined,
    date_to: filters.date_to || undefined,
  }
}

function applyFilters() {
  pagination.current = 1
  return refreshReviews()
}

function setChangeType(value?: string) {
  filters.change_type = value
  sidebarOpen.value = false
  pagination.current = 1
  refreshReviews()
}

function clearReviewFilters() {
  filters.status = undefined
  filters.q = ''
  filters.change_type = undefined
  filters.submitter = ''
  filters.reviewer = ''
  filters.date_from = ''
  filters.date_to = ''
  dateRange.value = undefined
  selectedRowKeys.value = []
  sidebarOpen.value = false
  pagination.current = 1
  refreshReviews()
}

function openReviewPolicy() {
  router.push('/admin/compliance')
}

function openBulkApprove() {
  const targets = selectedPendingReviews.value
  if (!targets.length) {
    Message.info('先勾选可操作的待审核单')
    return
  }
  Modal.confirm({
    title: '批量通过审核',
    content: `将依次通过 ${targets.length} 个待审核单。需要指定终端或静态检测阻断的审核单会保留待审核。`,
    okText: '批量通过',
    cancelText: '取消',
    onOk: () => bulkApproveReviews(targets),
  })
}

async function bulkApproveReviews(targets: Array<Record<string, any>>) {
  bulkApproving.value = true
  let success = 0
  let failed = 0
  try {
    for (const record of targets) {
      actingReviewId.value = String(record.id)
      try {
        await reviewApi.approve(String(record.id), {
          feedback_type: 'review_list_bulk_approve',
          verify_after_sync: false,
        })
        success += 1
      } catch {
        failed += 1
      }
    }
    selectedRowKeys.value = []
    if (success && failed) {
      Message.warning(`已通过 ${success} 个，${failed} 个需单独处理`)
    } else if (success) {
      Message.success(`已通过 ${success} 个审核单`)
    } else {
      Message.error('批量通过失败')
    }
    await refreshReviews()
  } finally {
    bulkApproving.value = false
    actingReviewId.value = ''
  }
}

async function confirmApprove(record: Record<string, unknown>) {
  approveTarget.value = record as Record<string, any>
  approveVisible.value = true
  approveSubmitting.value = false
  approveForm.rating = ''
  approveForm.feedback_type = ''
  approveForm.runtime_instance_id = ''
  approveForm.cron_expression = ''
  approveForm.verify_after_sync = true
  approveForm.static_check_override = false
  approveForm.static_check_override_reason = ''
  staticOverrideDetail.value = null
  approveTargets.value = []
  if (String(record.skill_id || '').startsWith('playbook:')) return
  approveTargetsLoading.value = true
  try {
    const res = await aiclawApi.listSyncTargets(String(record.skill_id || ''))
    approveTargets.value = Array.isArray(res?.items) ? res.items : []
    const defaultTarget = approveTargets.value.find((item) => item.auto_target && item.selectable !== false && item.bridge_online)
    approveForm.runtime_instance_id = defaultTarget?.id || ''
  } catch (e: any) {
    approveTargets.value = []
    Message.error(e?._message || '加载运行终端失败')
  } finally {
    approveTargetsLoading.value = false
  }
}

function setApproveCron(value: string) {
  approveForm.cron_expression = value
}

function syncTargetLabel(target: Record<string, any>): string {
  const parts = [
    target.name || target.id,
    target.department || '',
    target.gateway_kind || target.agent_type || '',
    target.bridge_online ? '在线' : '离线',
    target.selectable === false ? (target.reason || '不可选') : '',
  ].filter(Boolean)
  return parts.join(' · ')
}

function approvalToast(result: Record<string, any>) {
  const syncResult = result?.sync_result
  const scheduleResult = result?.schedule_result
  const verifyResult = result?.verify_result
  const instances = Array.isArray(syncResult?.instances) ? syncResult.instances : []
  const failed = instances.filter((item: Record<string, unknown>) => item?.ok === false).length
  const scheduleText = scheduleResult?.action ? `；定时已${scheduleResult.action === 'start' ? '启动' : '更新'}` : ''
  const verifyText = verifyResult?.status === 'ok'
    ? '；运行验证成功'
    : verifyResult?.status === 'failed'
      ? '；运行验证失败'
      : verifyResult?.status === 'skipped'
        ? '；运行验证已跳过'
        : ''
  if (!syncResult) {
    Message.success(`审核已通过${scheduleText}${verifyText}`)
    return
  }
  if (!instances.length) {
    Message.warning(`审核已通过，但未匹配到可同步终端${scheduleText}${verifyText}`)
    return
  }
  if (failed) {
    Message.warning(`审核已通过，自动同步 ${instances.length} 台终端，其中 ${failed} 台失败${scheduleText}${verifyText}`)
    return
  }
  Message.success(`审核已通过，并自动同步 ${instances.length} 台终端${scheduleText}${verifyText}`)
}

async function submitApprove() {
  const reviewId = String(approveTarget.value?.id || '')
  if (!reviewId) return
  if (approveForm.static_check_override && approveForm.static_check_override_reason.trim().length < 20) {
    Message.warning('Override 理由至少 20 字')
    return
  }
  approveSubmitting.value = true
  actingReviewId.value = reviewId
  try {
    const payload: Record<string, unknown> = {
      feedback_type: approveForm.feedback_type || 'review_list_approve',
      verify_after_sync: approveForm.verify_after_sync,
    }
    if (approveForm.rating) payload.rating = Number(approveForm.rating)
    if (!isPlaybookApproveTarget.value) {
      if (approveForm.runtime_instance_id) payload.runtime_instance_id = approveForm.runtime_instance_id
      if (approveForm.cron_expression.trim()) payload.cron_expression = approveForm.cron_expression.trim()
      if (approveForm.static_check_override) {
        payload.static_check_override = true
        payload.static_check_override_reason = approveForm.static_check_override_reason.trim()
      }
    }
    const result = await reviewApi.approve(reviewId, payload)
    approvalToast(result || {})
    approveVisible.value = false
    await refreshReviews()
  } catch (e: any) {
    const backendCode = e?._backendCode || e?.response?.data?.error?.code || e?.response?.data?.code
    if (backendCode === 'STATIC_CHECK_BLOCKED') {
      approveForm.static_check_override = true
      staticOverrideDetail.value = e?.response?.data?.error?.detail || e?.response?.data?.detail || null
      Message.warning('静态检测阻断，填写 Override 理由后可再次提交')
      return
    }
    Message.error(e?._message || '审核通过失败')
  } finally {
    approveSubmitting.value = false
    actingReviewId.value = ''
  }
}

function openReject(record: Record<string, unknown>) {
  rejectTarget.value = record
  rejectForm.reject_reason = 'logic'
  rejectForm.reason = ''
  rejectVisible.value = true
}

async function confirmReject() {
  const reviewId = String(rejectTarget.value?.id || '')
  if (!reviewId || !rejectForm.reason.trim()) return
  rejecting.value = true
  actingReviewId.value = reviewId
  try {
    await reviewApi.reject(reviewId, {
      reject_reason: rejectForm.reject_reason,
      reason: rejectForm.reason.trim(),
    })
    Message.success('审核已驳回')
    rejectVisible.value = false
    await refreshReviews()
  } catch (e: any) {
    Message.error(e?._message || '审核驳回失败')
  } finally {
    rejecting.value = false
    actingReviewId.value = ''
  }
}

function onDateChange(val: any) {
  if (val && val.length === 2) {
    filters.date_from = val[0]
    filters.date_to = val[1]
  } else {
    filters.date_from = ''
    filters.date_to = ''
  }
  applyFilters()
}

function setStatus(value?: string) {
  filters.status = value
  sidebarOpen.value = false
  pagination.current = 1
  refreshReviews()
}

async function loadReviews() {
  loading.value = true
  try {
    const res = await reviewApi.list({
      ...baseReviewParams({ includeStatus: true, includeChangeType: true }),
      page: pagination.current,
      page_size: pagination.pageSize,
    })
    reviews.value = Array.isArray(res) ? res : (res.items || [])
    if (res.total !== undefined) pagination.total = res.total
    const visibleKeys = new Set(reviews.value.map((record) => String(record.id)))
    selectedRowKeys.value = selectedRowKeys.value.filter((key) => visibleKeys.has(String(key)))
  } catch (e: any) {
    Message.error(e._message || '加载失败')
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  try {
    const base = baseReviewParams({ includeChangeType: true })
    const [all, pending, approved, rejected] = await Promise.all([
      reviewApi.list({ ...base, page_size: 1 }),
      reviewApi.list({ ...base, status: 'pending', page_size: 1 }),
      reviewApi.list({ ...base, status: 'approved', page_size: 1 }),
      reviewApi.list({ ...base, status: 'rejected', page_size: 1 }),
    ])
    stats.total = all.total || 0
    stats.pending = pending.total || 0
    stats.approved = approved.total || 0
    stats.rejected = rejected.total || 0
  } catch { /* 静默 */ }
}

async function loadChangeTypeStats() {
  try {
    const base = baseReviewParams({ includeStatus: true })
    const [newSkill, update, fix] = await Promise.all([
      reviewApi.list({ ...base, change_type: 'new_skill', page_size: 1 }),
      reviewApi.list({ ...base, change_type: 'update', page_size: 1 }),
      reviewApi.list({ ...base, change_type: 'fix', page_size: 1 }),
    ])
    changeTypeStats.new_skill = newSkill.total || 0
    changeTypeStats.update = update.total || 0
    changeTypeStats.fix = fix.total || 0
  } catch { /* 静默 */ }
}

function onPageChange(page: number) {
  pagination.current = page
  selectedRowKeys.value = []
  loadReviews()
}

onMounted(() => { refreshReviews() })
</script>

<style scoped>
.review-list-page {
  display: flex;
  flex-direction: row;
  align-items: stretch;
  gap: 0;
  min-height: calc(100vh - 52px);
  padding: 0 !important;
  max-width: none !important;
  background: var(--ai-bg);
  color: var(--ai-ink-1);
  font-family: var(--ai-font-sans);
}

.review-sidebar {
  width: 220px;
  flex: 0 0 220px;
  border-right: 1px solid var(--ai-border);
  background: var(--ai-surface);
  padding: 16px 12px;
  overflow-y: auto;
}
.review-sidebar-head {
  display: none;
  align-items: center;
  justify-content: space-between;
  padding: 0 4px 6px;
}
.review-sidebar-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.review-sidebar-close {
  display: none;
  background: transparent;
  border: 0;
  cursor: pointer;
  color: var(--ai-ink-4);
  padding: 4px;
}
.review-sidebar-close svg {
  width: 13px;
  height: 13px;
}
.review-sidebar .ai-side-group {
  margin-bottom: 18px;
}
.review-sidebar .ai-side-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-4);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 0 8px 6px;
}
.review-sidebar .ai-side-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 28px;
  padding: 0 8px;
  border-radius: 5px;
  font-size: 13px;
  color: var(--ai-ink-2);
  cursor: pointer;
  border: 0;
  background: transparent;
  text-decoration: none;
  font-family: var(--ai-font-sans);
  text-align: left;
}
.review-sidebar .ai-side-item:hover,
.review-sidebar .ai-side-item.active {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}
.review-sidebar .ai-side-item.active {
  font-weight: 500;
}
.review-sidebar .ai-side-item .ic {
  width: 13px;
  height: 13px;
  color: var(--ai-ink-4);
}
.review-sidebar .ai-side-item.active .ic {
  color: var(--ai-ink-1);
}
.review-sidebar .count {
  margin-left: auto;
  font-size: 11px;
  color: var(--ai-ink-4);
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
}
.deptdot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--ai-ink-1);
  flex: 0 0 7px;
}
.dot-warn { background: var(--ai-warn); }
.dot-ok { background: var(--ai-ok); }
.dot-bad { background: var(--ai-bad); }
.dot-all { background: var(--ai-ink-1); }

.review-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow-x: auto;
}
.review-sidebar-toggle {
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
.review-sidebar-toggle svg {
  width: 13px;
  height: 13px;
}

.review-pagehead {
  flex: 0 0 auto;
}
.review-head-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}
.review-head-actions svg {
  width: 12px;
  height: 12px;
  flex: 0 0 12px;
}
.review-head-actions .ai-btn {
  cursor: pointer;
}
.review-head-actions .ai-btn[disabled] {
  cursor: not-allowed;
  color: var(--ai-ink-4);
  background: var(--ai-surface-2);
  border-color: var(--ai-border);
  opacity: 0.72;
}
.review-shell {
  display: flex;
  flex-direction: column;
  gap: 16px;
  background: transparent;
}
.review-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.review-search {
  width: 280px;
}
.review-filter-input {
  width: 130px;
}
.date-range-picker {
  width: 240px;
}
.review-toolbar-spacer {
  flex: 1 1 auto;
  min-width: 8px;
}
.review-pill {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  border: 1px solid transparent;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 11.5px;
  font-weight: 500;
  font-family: var(--ai-font-sans);
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}
.review-pill.active {
  color: var(--ai-surface);
}
.pill-warn {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
}
.pill-warn.active {
  background: var(--ai-warn);
}
.pill-ok {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
}
.pill-ok.active {
  background: var(--ai-ok);
}
.pill-bad {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}
.pill-bad.active {
  background: var(--ai-bad);
}

.review-table-card {
  padding: 0;
  overflow: hidden;
}
.review-table-card :deep(.arco-table-container),
.review-table-card :deep(.arco-table-content) {
  border-radius: var(--ai-radius);
}
.review-id {
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-3);
  font-size: 12px;
  font-weight: 500;
}
.diff-summary {
  font-size: 12.5px;
  color: var(--ai-ink-2);
  font-weight: 450;
}
.review-skill-link {
  border: 0;
  background: transparent;
  padding: 0;
  cursor: pointer;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 12px;
  font-weight: 500;
  max-width: 176px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
}
.review-skill-link:hover {
  color: var(--ai-accent);
}
.review-actions {
  justify-content: flex-end;
  width: 100%;
}

.bottom-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 12px;
  border-top: 1px solid var(--ai-border);
  gap: 12px;
  flex-wrap: wrap;
}
.review-bottom-text {
  color: var(--ai-ink-4);
  font-size: 12px;
}

.review-list-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.review-list-page :deep(.arco-tag-color-gray),
.review-list-page :deep(.arco-tag-color-grey) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  border: 1px solid var(--ai-border);
}
.review-list-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
  border-color: transparent;
}
.review-list-page :deep(.arco-tag-color-blue),
.review-list-page :deep(.arco-tag-color-cyan) {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}
.review-list-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
.review-list-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}
.review-list-page :deep(.arco-tag-color-orange),
.review-list-page :deep(.arco-tag-color-orangered),
.review-list-page :deep(.arco-tag-color-gold) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.review-list-page :deep(.arco-tag-color-purple),
.review-list-page :deep(.arco-tag-color-magenta),
.review-list-page :deep(.arco-tag-color-pinkpurple) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  border-color: var(--ai-border);
}

/* 表格密度 + 颜色：12.5px，ink-2，无渐变，hover surface-2 */
.review-list-page :deep(.arco-table-th) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  border-bottom: 1px solid var(--ai-border);
}
.review-list-page :deep(.arco-table-td) {
  font-size: 12.5px;
  color: var(--ai-ink-1);
  border-bottom: 1px solid var(--ai-border);
}
.review-list-page :deep(.arco-table-tr:hover .arco-table-td) {
  background: var(--ai-surface-2);
}
.review-list-page :deep(.arco-table-checkbox) {
  padding-left: 12px;
}

.review-list-page :deep(.arco-btn) {
  font-size: 12px;
  font-weight: 500;
}
.review-list-page :deep(.arco-btn-primary) {
  box-shadow: none;
  font-weight: 500;
  font-size: 12px;
}
.review-list-page :deep(.arco-btn-mini),
.review-list-page :deep(.arco-btn-size-mini) {
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  font-size: 11.5px;
}

.review-list-page :deep(.arco-input-wrapper),
.review-list-page :deep(.arco-select-view),
.review-list-page :deep(.arco-picker) {
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  box-shadow: none;
}
.review-list-page :deep(.arco-input-wrapper:hover),
.review-list-page :deep(.arco-select-view:hover),
.review-list-page :deep(.arco-picker:hover) {
  border-color: var(--ai-border-2);
}
.review-list-page :deep(.arco-input-wrapper.arco-input-focus),
.review-list-page :deep(.arco-select-view-focus),
.review-list-page :deep(.arco-picker-focused) {
  border-color: var(--ai-ink-3);
  box-shadow: none;
}

.approve-intro {
  margin-bottom: 12px;
  color: var(--ai-ink-2);
  line-height: 1.6;
  font-size: 12.5px;
}
.approve-self-tip {
  color: var(--ai-warn);
  font-weight: 500;
  margin-top: 4px;
}
.approve-hint {
  padding: 10px 12px;
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  border: 1px solid var(--ai-border);
  font-size: 12.5px;
}
.approve-cron { display: grid; gap: 8px; }
.reason-meter {
  margin-top: 4px;
  text-align: right;
  font-size: 11px;
  color: var(--ai-ink-4);
  font-variant-numeric: tabular-nums;
}

@media (max-width: 768px) {
  .review-list-page {
    flex-direction: column;
  }
  .review-sidebar {
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
  .review-sidebar.review-sidebar--open {
    transform: translateX(0) !important;
  }
  .review-sidebar-head {
    display: flex;
  }
  .review-sidebar-close,
  .review-sidebar-toggle {
    display: inline-flex;
  }
  .review-main {
    padding: 0;
  }
  .review-sidebar-toggle {
    margin: 12px 16px 0;
  }
  .review-pagehead {
    flex-direction: column;
    align-items: stretch;
    padding: 16px;
  }
  .review-head-actions {
    justify-content: flex-start;
  }
  .review-shell {
    padding: 16px;
  }
  .review-search,
  .review-filter-input,
  .date-range-picker {
    width: 100%;
  }
  .review-toolbar-spacer {
    display: none;
  }
}
</style>
