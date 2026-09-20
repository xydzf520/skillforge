<template>
  <div class="review-view">
    <section class="rv-hero">
      <div>
        <div class="rv-kicker">审批视图</div>
        <h2>{{ meta.name || skillId }}</h2>
        <p>当前页面聚焦于变化、风险、验证与发布门禁，不暴露编辑入口。</p>
      </div>
      <div class="rv-pill-group">
        <a-tag size="small" :color="readiness?.can_publish ? 'green' : 'orange'">
          {{ readiness?.can_publish ? '可进入发布流程' : `${readiness?.blocker_count || 0} 个阻断` }}
        </a-tag>
        <a-tag size="small" color="arcoblue">健康 {{ Math.round(healthScore?.score || 0) }}</a-tag>
        <a-tag size="small" :color="guardianSeverity">{{ guardianItems.length }} 个运行告警</a-tag>
      </div>
    </section>

    <section class="rv-grid">
      <article class="rv-card">
        <div class="rv-card-title">审批摘要</div>
        <ul class="rv-list">
          <li>规则 {{ rules.length }} 条</li>
          <li>参数 {{ params.length }} 个</li>
          <li>输出 {{ outputs.length }} 个字段</li>
          <li>测试 {{ tests.length }} 个样例</li>
        </ul>
      </article>

      <article class="rv-card">
        <div class="rv-card-title">门禁状态</div>
        <div v-if="readiness" class="rv-gates">
          <div class="rv-gate-line">阻断项：{{ readiness.blocker_count || 0 }}</div>
          <div class="rv-gate-line">警告项：{{ readiness.warning_count || 0 }}</div>
          <div v-if="readiness.review_gate" class="rv-gate-line">提交 gate：{{ readiness.review_gate.score ?? 0 }}/100</div>
          <div class="rv-gate-line">历史回放：{{ replayLabel }}</div>
          <div v-if="reviewGateMissing.length" class="rv-gate-missing">
            <div v-for="item in reviewGateMissing" :key="item.key || item.label" class="rv-risk-copy">
              {{ item.label }}：{{ item.detail }}；建议：{{ item.suggestion }}
            </div>
          </div>
        </div>
        <div v-else class="rv-empty">尚未运行发布门禁。</div>
      </article>
    </section>

    <section class="rv-card">
      <div class="rv-card-title">当前待审单</div>
      <div v-if="reviewLoading" class="rv-empty">正在加载审核上下文...</div>
      <template v-else-if="reviewContext?.review">
        <div class="rv-review-head">
          <div>
            <div class="rv-review-id">#{{ reviewContext.review.id }} · {{ reviewContext.review.change_type }}</div>
            <div class="rv-review-meta">提交人 {{ reviewContext.review.submitter }} · 审核人 {{ reviewContext.review.reviewer || '待分配' }}</div>
          </div>
          <button class="rv-link-btn" type="button" @click="$emit('open-review-detail', reviewContext.review.id)">查看详情</button>
        </div>
        <div v-if="reviewContext.review.diff_summary" class="rv-copy" style="margin-top: 10px">{{ reviewContext.review.diff_summary }}</div>
      </template>
      <div v-else class="rv-empty">当前没有待审批的审核单。</div>
    </section>

    <section class="rv-card">
      <div class="rv-card-title">风险与告警</div>
      <div v-if="riskItems.length" class="rv-stack">
        <div v-for="(item, index) in riskItems" :key="index" class="rv-risk">
          <div class="rv-risk-title">{{ item.title }}</div>
          <div class="rv-risk-copy">{{ item.detail }}</div>
        </div>
      </div>
      <div v-else class="rv-empty">当前没有额外风险提示。</div>
    </section>

    <section class="rv-card rv-analysis-card">
      <div class="rv-card-title">AI 变更分析</div>

      <!-- Reviewer 一句话摘要 -->
      <div v-if="reviewContext?.reviewer_report?.one_line" class="rv-analysis-summary">
        {{ reviewContext.reviewer_report.one_line }}
      </div>

      <!-- 摘要指标 -->
      <div v-if="reviewContext?.reviewer_report?.summary" class="rv-summary-grid">
        <div v-if="reviewContext.reviewer_report.summary.logic_change" class="rv-summary-item">
          <span class="rv-summary-label">逻辑变化</span>
          <span class="rv-summary-value">{{ reviewContext.reviewer_report.summary.logic_change }}</span>
        </div>
        <div v-if="reviewContext.reviewer_report.summary.direction" class="rv-summary-item">
          <span class="rv-summary-label">方向</span>
          <span class="rv-summary-value">{{ reviewContext.reviewer_report.summary.direction }}</span>
        </div>
        <div v-if="reviewContext.reviewer_report.summary.estimated_impact" class="rv-summary-item">
          <span class="rv-summary-label">影响</span>
          <span class="rv-summary-value">{{ reviewContext.reviewer_report.summary.estimated_impact }}</span>
        </div>
      </div>

      <!-- 风险维度标签 -->
      <div v-if="reviewContext?.reviewer_report?.risk" class="rv-chip-list" style="margin-top: 12px">
        <span v-for="(label, key) in riskDimLabels" :key="key" class="rv-chip">
          {{ label }}：{{ riskLevelLabel[reviewContext.reviewer_report.risk[key]] || reviewContext.reviewer_report.risk[key] }}
        </span>
      </div>

      <!-- 变更条目 + 内联风险标注 -->
      <div v-if="reviewContext?.semantic_diff?.changes?.length" class="rv-stack" style="margin-top: 14px">
        <div v-for="(change, index) in visibleChanges" :key="index" class="rv-change-item">
          <div class="rv-change-main">
            <div class="rv-risk-title">{{ change.description }}</div>
            <div v-if="change.old_value || change.new_value" class="rv-risk-copy">
              <span v-if="change.old_value">旧：{{ truncate(change.old_value) }}</span>
              <span v-if="change.old_value && change.new_value"> → </span>
              <span v-if="change.new_value">新：{{ truncate(change.new_value) }}</span>
            </div>
          </div>
        </div>
        <div v-if="hasMoreChanges" class="rv-actions" style="margin-top: 10px">
          <a-button size="small" @click="showAllChanges = !showAllChanges">
            {{ showAllChanges ? '收起变化' : `查看更多变化 (${reviewContext.semantic_diff.changes.length})` }}
          </a-button>
        </div>
      </div>
      <div v-else class="rv-empty" style="margin-top: 12px">暂无结构化变更摘要。</div>

      <!-- AI 风险意见（嵌入在同一卡片中） -->
      <div v-if="reviewContext?.ai_review?.opinions?.length" style="margin-top: 14px">
        <div class="rv-analysis-subtitle">风险意见</div>
        <div class="rv-stack">
          <div v-for="(opinion, index) in reviewContext.ai_review.opinions.slice(0, 4)" :key="index" class="rv-risk">
            <div class="rv-risk-title">{{ opinion.title }}</div>
            <div class="rv-risk-copy">{{ opinion.detail }}</div>
          </div>
        </div>
      </div>

      <!-- Reviewer 风险原因 -->
      <div v-if="reviewContext?.reviewer_report?.risk?.reasons?.length" style="margin-top: 14px">
        <div class="rv-analysis-subtitle">风险原因</div>
        <div class="rv-stack">
          <div v-for="(reason, index) in reviewContext.reviewer_report.risk.reasons" :key="index" class="rv-risk">
            <div class="rv-risk-copy">{{ reason }}</div>
          </div>
        </div>
      </div>

      <!-- 结果分布变化 -->
      <div v-if="hasResultDiff" class="rv-result-diff">
        <div class="rv-analysis-subtitle">结果分布变化</div>
        <div class="rv-stack">
          <div v-for="label in resultDiffLabels" :key="label" class="rv-risk">
            <div class="rv-risk-title">{{ label }}</div>
            <div class="rv-risk-copy">
              前 {{ resultDiffBefore(label) }}% → 后 {{ resultDiffAfter(label) }}%（{{ percentDeltaText(label) }}）
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="rv-card" v-if="canActReview && reviewContext?.review?.status === 'pending'">
      <div class="rv-card-title">审批建议</div>
      <div class="rv-copy">
        <template v-if="readiness?.can_publish">
          当前门禁通过，可以进入正式审批或发布动作；建议重点复核测试覆盖和最近一次回放结果。
        </template>
        <template v-else>
          当前仍有发布阻断，建议先查看验证面板与灰度对比，再决定是否继续审批。
        </template>
      </div>
      <div class="rv-actions">
        <a-button size="small" @click="$emit('open-tab', 'validation')">查看验证</a-button>
        <a-button size="small" @click="$emit('open-tab', 'shadow')">查看灰度</a-button>
        <a-button size="small" @click="$emit('open-tab', 'history')">查看历史</a-button>
      </div>
      <div class="rv-form-grid">
        <div class="rv-form-card">
          <div class="rv-form-title">通过</div>
          <label class="rv-label">反馈类型</label>
          <select v-model="approveForm.feedback_type" class="rv-input">
            <option value="">未指定</option>
            <option value="ready">可发布</option>
            <option value="quality">质量稳定</option>
            <option value="logic">逻辑清晰</option>
          </select>
          <label class="rv-label">评分</label>
          <select v-model="approveForm.rating" class="rv-input">
            <option value="">未评分</option>
            <option v-for="score in [5,4,3,2,1]" :key="score" :value="String(score)">{{ score }}</option>
          </select>
          <template v-if="!isPlaybookTarget">
            <label class="rv-label">运行终端</label>
            <select v-model="approveForm.runtime_instance_id" class="rv-input">
              <option value="">保持默认</option>
              <option v-for="target in runtimeTargets" :key="target.id" :value="target.id">
                {{ syncTargetLabel(target) }}
              </option>
            </select>
            <div v-if="syncTargetsLoading" class="rv-inline-hint">运行终端加载中...</div>
            <label class="rv-label">定时任务</label>
            <div class="rv-preset-row">
              <button class="rv-chip-btn" type="button" @click="setApproveCron('0 9 * * *')">每天 09:00</button>
              <button class="rv-chip-btn" type="button" @click="setApproveCron('0 18 * * *')">每天 18:00</button>
              <button class="rv-chip-btn" type="button" @click="setApproveCron('0 * * * *')">每小时</button>
              <button class="rv-chip-btn" type="button" @click="setApproveCron('')">清空</button>
            </div>
            <input v-model="approveForm.cron_expression" class="rv-input" placeholder="留空表示不改定时；例如 0 9 * * *">
            <label class="rv-check">
              <input v-model="approveForm.verify_after_sync" type="checkbox">
              <span>同步后立即做一次运行验证</span>
            </label>
          </template>
          <div v-if="approveForm.static_check_override" class="rv-warning-box">
            {{ staticOverrideText }}
          </div>
          <template v-if="approveForm.static_check_override">
            <label class="rv-label">Override 理由</label>
            <textarea
              v-model="approveForm.static_check_override_reason"
              class="rv-textarea"
              placeholder="说明业务必要性、风险判断和回滚方式"
            />
            <div class="rv-reason-meter">
              {{ approveForm.static_check_override_reason.trim().length }}/20
            </div>
          </template>
          <button class="rv-primary-btn" type="button" :disabled="reviewActing" @click="emitApprove">批准当前审核</button>
        </div>
        <div class="rv-form-card">
          <div class="rv-form-title">驳回</div>
          <label class="rv-label">驳回类型</label>
          <select v-model="rejectForm.reject_reason" class="rv-input">
            <option value="">未指定</option>
            <option value="quality">质量问题</option>
            <option value="logic">逻辑问题</option>
            <option value="format">格式问题</option>
          </select>
          <label class="rv-label">驳回说明</label>
          <textarea v-model="rejectForm.reason" class="rv-textarea" placeholder="说明为什么不能通过" />
          <button class="rv-danger-btn" type="button" :disabled="reviewActing || !rejectForm.reason.trim()" @click="emitReject">驳回当前审核</button>
          <a-button v-if="rejectForm.reason.trim()" type="outline" size="small" style="margin-top: 8px" @click="emitAskAIFix">
            <template #icon><icon-robot /></template>
            让 AI 修复
          </a-button>
        </div>
      </div>
    </section>

    <section class="rv-card" v-else>
      <div class="rv-card-title">审批建议</div>
      <div class="rv-copy">
        <template v-if="reviewContext?.review?.status === 'pending'">
          当前审批单不属于你，或当前角色不具备直接审批权限。
        </template>
        <template v-else>
          当前没有可直接执行的审批动作。
        </template>
      </div>
      <div class="rv-actions">
        <a-button size="small" @click="$emit('open-tab', 'validation')">查看验证</a-button>
        <a-button size="small" @click="$emit('open-tab', 'history')">查看历史</a-button>
      </div>
    </section>

    <section class="rv-card">
      <div class="rv-card-title">评论时间线</div>
      <div v-if="comments.length" class="rv-stack">
        <div v-for="comment in comments" :key="comment.id" class="rv-comment" :class="{ resolved: comment.resolved }">
          <div class="rv-comment-head">
            <div class="rv-comment-meta">
              <strong>{{ comment.author }}</strong>
              <span v-if="comment.file_path" class="rv-comment-line">{{ comment.file_path }}:{{ comment.line_number }}</span>
              <span class="rv-comment-time">{{ comment.created_at || '' }}</span>
            </div>
            <button
              v-if="comment.line_number"
              class="rv-link-btn"
              type="button"
              @click="selectedDiffLine = comment.line_number"
            >
              跳到 L{{ comment.line_number }}
            </button>
            <button
              v-if="canCommentReview && !comment.resolved"
              class="rv-link-btn"
              type="button"
              @click="$emit('resolve-review-comment', comment.id)"
            >
              标记已解决
            </button>
          </div>
          <div class="rv-risk-copy">{{ comment.content }}</div>
          <div v-if="comment.resolved" class="rv-comment-status">已解决</div>
        </div>
      </div>
      <div v-else class="rv-empty">当前还没有评论。</div>

      <div v-if="canCommentReview && reviewContext?.review" class="rv-comment-form">
        <label class="rv-label">新增评论</label>
        <textarea v-model="commentDraft" class="rv-textarea" placeholder="补充审批意见或需要进一步说明的问题" />
        <div class="rv-actions">
          <a-button size="small" @click="$emit('open-review-detail', reviewContext.review.id)">查看完整审核页</a-button>
          <button class="rv-primary-btn" type="button" :disabled="reviewActing || !commentDraft.trim()" @click="emitComment">发送评论</button>
        </div>
      </div>
    </section>

    <section class="rv-card">
      <div class="rv-card-title">Diff Drilldown</div>
      <WorkbenchReviewDiffPanel
        :diff-text="reviewContext?.review?.diff_text || ''"
        :comments="comments"
        :selected-line="selectedDiffLine"
        @line-select="selectedDiffLine = $event"
      />
      <div class="rv-copy" style="margin-top: 10px">
        <template v-if="selectedDiffLine">当前选中 L{{ selectedDiffLine }}，新增评论会默认定位到这一行。</template>
        <template v-else>点击评论中的行号，或在 diff 中点击行号来定位。</template>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch, type PropType } from 'vue'
import { Message } from '@arco-design/web-vue'
import { IconRobot } from '@arco-design/web-vue/es/icon'
import { aiclawApi as rawAiclawApi } from '@/api'
import WorkbenchReviewDiffPanel from './WorkbenchReviewDiffPanel.vue'

const aiclawApi: any = rawAiclawApi
const props = defineProps({
  doc: { type: Object as PropType<any>, default: () => ({}) },
  skillId: { type: String, default: '' },
  readiness: { type: Object as PropType<any>, default: null },
  healthScore: { type: Object as PropType<any>, default: null },
  guardianItems: { type: Array as PropType<any[]>, default: () => [] },
  reviewContext: { type: Object as PropType<any>, default: null },
  staticCheckOverrideDetail: { type: [Object, String] as PropType<any>, default: null },
  reviewLoading: { type: Boolean, default: false },
  reviewActing: { type: Boolean, default: false },
  canActReview: { type: Boolean, default: false },
  canCommentReview: { type: Boolean, default: false },
})

const emit = defineEmits(['open-tab', 'open-review-detail', 'approve-review', 'reject-review', 'comment-review', 'resolve-review-comment', 'ask-ai-fix'])

const meta = computed(() => props.doc?.meta || {})
const rules = computed(() => props.doc?.rules || [])
const params = computed(() => props.doc?.params || [])
const outputs = computed(() => props.doc?.output_table || [])
const tests = computed(() => props.doc?.test_cases || [])
const reviewGateMissing = computed(() => {
  const items = props.readiness?.review_gate?.items
  return Array.isArray(items)
    ? items.filter((item: any) => item?.severity === 'block' && !item?.passed)
    : []
})
const approveForm = reactive({
  rating: '',
  feedback_type: '',
  runtime_instance_id: '',
  cron_expression: '',
  verify_after_sync: true,
  static_check_override: false,
  static_check_override_reason: '',
})
const rejectForm = reactive({
  reject_reason: '',
  reason: '',
})
const commentDraft = ref('')
const showAllChanges = ref(false)
const selectedDiffLine = ref<number | null>(null)
const syncTargets = ref<any[]>([])
const syncTargetsLoading = ref(false)
const isPlaybookTarget = computed(() => props.skillId.startsWith('playbook:'))
const runtimeTargets = computed(() => syncTargets.value.filter((item: any) => item?.selectable !== false))

const replayLabel = computed(() => {
  const status = props.readiness?.replay?.status
  if (!status) return '未运行'
  return ({
    ok: '正常',
    insufficient: '样本不足',
    degraded: '存在偏差',
    skipped: '跳过',
  } as Record<string, string>)[status] || status
})

const guardianSeverity = computed(() =>
  props.guardianItems.some(item => item.severity === 'high' || item.severity === 'critical') ? 'red' : 'orange',
)
const comments = computed(() => props.reviewContext?.review?.comments || [])
const riskDimLabels = {
  behavior_expansion: '行为扩张',
  false_positive: '误伤',
  drift: '漂移',
  dependency: '依赖',
  test_coverage: '测试覆盖',
  cross_skill_conflict: '跨 Skill 冲突',
}
const riskLevelLabel: Record<string, string> = { low: '低', medium: '中', high: '高', none: '无', adequate: '充分', insufficient: '不足' }
const visibleChanges = computed(() => {
  const changes = props.reviewContext?.semantic_diff?.changes || []
  return showAllChanges.value ? changes : changes.slice(0, 6)
})
const hasMoreChanges = computed(() => (props.reviewContext?.semantic_diff?.changes?.length || 0) > 6)
const hasResultDiff = computed(() => {
  const rd = props.reviewContext?.reviewer_report?.result_diff
  if (!rd) return false
  return (rd.sample_size_before || 0) + (rd.sample_size_after || 0) > 0
})
const resultDiffLabels = computed(() => {
  const rd = props.reviewContext?.reviewer_report?.result_diff
  if (!rd) return []
  return Array.from(new Set([...Object.keys(rd.before || {}), ...Object.keys(rd.after || {})]))
})
const staticOverrideText = computed(() => {
  const detail = props.staticCheckOverrideDetail
  if (!detail) return '需要填写 Override 理由后再次确认。'
  if (typeof detail === 'string') return detail
  const issues = Array.isArray(detail.issues) ? detail.issues.length : 0
  const blocked = detail.blocked === true ? '已阻断' : '需人工确认'
  return issues ? `${blocked}，命中 ${issues} 条静态检测。` : `${blocked}，需要填写 Override 理由后再次确认。`
})

const riskItems = computed(() => {
  const items = []
  if (props.reviewContext?.ai_review?.risk_score != null) {
    items.push({
      title: 'AI 风险分',
      detail: `当前 AI 审核风险分为 ${props.reviewContext.ai_review.risk_score}/10。`,
    })
  }
  if (props.readiness && !props.readiness.can_publish) {
    items.push({
      title: '发布门禁未通过',
      detail: `当前仍有 ${props.readiness.blocker_count || 0} 个阻断项，需要先处理。`,
    })
  }
  if (props.guardianItems.length) {
    for (const item of props.guardianItems.slice(0, 3)) {
      items.push({
        title: item.description || item.metric || '运行告警',
        detail: `${item.dimension || item.category || 'runtime'} · ${item.segment || item.severity || 'info'}`,
      })
    }
  }
  return items
})

function truncate(value: unknown): string {
  const text = String(value ?? '')
  return text.length > 80 ? `${text.slice(0, 80)}...` : text
}

async function loadSyncTargets(): Promise<void> {
  syncTargets.value = []
  if (!props.skillId || isPlaybookTarget.value) return
  syncTargetsLoading.value = true
  try {
    const res = await aiclawApi.listSyncTargets(props.skillId)
    syncTargets.value = Array.isArray(res?.items) ? res.items : []
    const autoTarget = syncTargets.value.find((item: any) => item?.auto_target && item?.selectable !== false && item?.bridge_online)
    approveForm.runtime_instance_id = autoTarget?.id || approveForm.runtime_instance_id
  } catch (error: any) {
    syncTargets.value = []
    Message.error(error?._message || '加载运行终端失败')
  } finally {
    syncTargetsLoading.value = false
  }
}

function syncTargetLabel(target: any): string {
  const parts = [
    target?.name || target?.id,
    target?.department || '',
    target?.gateway_kind || target?.agent_type || '',
    agentPurposeLabel(target?.agent_purpose),
    target?.bridge_online ? '在线' : '离线',
  ].filter(Boolean)
  return parts.join(' · ')
}

function agentPurposeLabel(value: any): string {
  const purpose = String(value || 'skill_runtime').toLowerCase()
  const labels: Record<string, string> = {
    skill_runtime: '部门执行',
    analysis: '分析',
    training: '训练',
    media: '媒体生成',
    mixed: '混合',
  }
  return labels[purpose] || '部门执行'
}

function setApproveCron(value: string): void {
  approveForm.cron_expression = value
}

function emitApprove(): void {
  if (approveForm.static_check_override && approveForm.static_check_override_reason.trim().length < 20) {
    Message.warning('Override 理由至少 20 字')
    return
  }
  const payload: Record<string, unknown> = {
    rating: approveForm.rating ? Number(approveForm.rating) : null,
    feedback_type: approveForm.feedback_type,
    verify_after_sync: approveForm.verify_after_sync,
  }
  if (!isPlaybookTarget.value && approveForm.runtime_instance_id) {
    payload.runtime_instance_id = approveForm.runtime_instance_id
  }
  if (!isPlaybookTarget.value && approveForm.cron_expression.trim()) {
    payload.cron_expression = approveForm.cron_expression.trim()
  }
  if (approveForm.static_check_override) {
    payload.static_check_override = true
    payload.static_check_override_reason = approveForm.static_check_override_reason.trim()
  }
  emit('approve-review', payload)
}

function emitReject(): void {
  emit('reject-review', {
    reject_reason: rejectForm.reject_reason,
    reason: rejectForm.reason.trim(),
  })
}

function emitAskAIFix(): void {
  const reasons: string[] = []
  if (rejectForm.reason.trim()) reasons.push(rejectForm.reason.trim())
  const riskReasons = props.reviewContext?.reviewer_report?.risk?.reasons
  if (Array.isArray(riskReasons)) reasons.push(...riskReasons)
  emit('ask-ai-fix', { reasons })
}

function emitComment(): void {
  emit('comment-review', {
    content: commentDraft.value.trim(),
    file_path: selectedDiffLine.value ? 'SKILL.md' : undefined,
    line_number: selectedDiffLine.value || undefined,
    side: selectedDiffLine.value ? 'right' : undefined,
  })
  commentDraft.value = ''
}

function resultDiffBefore(label: string): number {
  const rd = props.reviewContext?.reviewer_report?.result_diff
  if (!rd?.sample_size_before) return 0
  return Math.round(((rd.before?.[label] || 0) / rd.sample_size_before) * 100)
}

function resultDiffAfter(label: string): number {
  const rd = props.reviewContext?.reviewer_report?.result_diff
  if (!rd?.sample_size_after) return 0
  return Math.round(((rd.after?.[label] || 0) / rd.sample_size_after) * 100)
}

function percentDeltaText(label: string): string {
  const delta = resultDiffAfter(label) - resultDiffBefore(label)
  return `${delta > 0 ? '+' : ''}${delta}%`
}

watch(
  () => props.skillId,
  () => {
    approveForm.runtime_instance_id = ''
    approveForm.cron_expression = ''
    approveForm.verify_after_sync = true
    approveForm.static_check_override = Boolean(props.staticCheckOverrideDetail)
    if (!props.staticCheckOverrideDetail) approveForm.static_check_override_reason = ''
    loadSyncTargets()
  },
  { immediate: true },
)

watch(
  () => props.staticCheckOverrideDetail,
  (detail) => {
    approveForm.static_check_override = Boolean(detail)
    if (!detail) approveForm.static_check_override_reason = ''
  },
  { immediate: true },
)
</script>

<style scoped>
.review-view {
  height: 100%;
  overflow-y: auto;
  padding: 28px 32px 36px;
  background: var(--ai-surface);
}
.rv-hero, .rv-card {
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--sf-panel-radius);
  box-shadow: var(--ai-shadow-2);
}
.rv-hero {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  padding: 24px 26px;
  margin-bottom: 18px;
}
.rv-kicker {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ai-warn);
  margin-bottom: 10px;
}
.rv-hero h2 {
  margin: 0 0 8px;
  font-size: 24px;
  color: var(--ai-ink-1);
}
.rv-hero p {
  margin: 0;
  color: var(--ai-ink-3);
  line-height: 1.7;
}
.rv-pill-group {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: flex-end;
  gap: 6px;
  max-width: 280px;
}
.rv-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
  margin-bottom: 18px;
}
.rv-card {
  padding: 18px 20px;
  margin-bottom: 18px;
}
.rv-card-title {
  font-size: 13px;
  font-weight: 800;
  color: var(--ai-ink-1);
  margin-bottom: 12px;
}
.rv-list {
  margin: 0;
  padding-left: 18px;
  color: var(--ai-ink-3);
  line-height: 1.8;
}
.rv-gates {
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: var(--ai-ink-3);
}
.rv-gate-line {
  font-size: 13px;
}
.rv-stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.rv-risk {
  padding: 12px 14px;
  border-radius: var(--sf-panel-radius);
  background: var(--ai-surface-2);
}
.rv-risk-title {
  font-size: 13px;
  font-weight: 800;
  color: var(--ai-ink-1);
  margin-bottom: 6px;
}
.rv-risk-copy, .rv-copy, .rv-empty {
  font-size: 13px;
  line-height: 1.7;
  color: var(--ai-ink-3);
}
.rv-comment {
  padding: 12px 14px;
  border-radius: var(--sf-panel-radius);
  background: var(--ai-surface-2);
}
.rv-comment.resolved {
  opacity: 0.7;
}
.rv-comment-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 6px;
}
.rv-comment-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.rv-comment-line {
  padding: 2px 6px;
  border-radius: 999px;
  background: rgba(49,86,163,0.08);
  color: var(--ai-info);
  font-weight: 700;
}
.rv-comment-time {
  color: var(--ai-ink-4);
}
.rv-comment-status {
  margin-top: 6px;
  font-size: 12px;
  font-weight: 700;
  color: var(--ai-ok);
}
.rv-comment-form {
  margin-top: 18px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rv-summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}
.rv-summary-item {
  padding: 10px 12px;
  border-radius: 12px;
  background: var(--ai-surface-2);
}
.rv-summary-label {
  display: block;
  font-size: 11px;
  color: var(--ai-ink-4);
  margin-bottom: 4px;
}
.rv-summary-value {
  font-size: 13px;
  color: var(--ai-ink-1);
  font-weight: 700;
}
.rv-inline-hint {
  font-size: 12px;
  color: var(--ai-ink-4);
  margin: -2px 0 6px;
}
.rv-chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.rv-chip {
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  border-radius: 999px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: 12px;
  font-weight: 700;
}
.rv-analysis-card {
  border-left: 3px solid rgba(49,86,163,0.4);
}
.rv-analysis-summary {
  font-size: 14px;
  font-weight: 700;
  color: var(--ai-ink-1);
  line-height: 1.7;
  margin-bottom: 12px;
}
.rv-analysis-subtitle {
  font-size: 12px;
  font-weight: 800;
  color: var(--ai-ink-3);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 8px;
}
.rv-change-item {
  padding: 12px 14px;
  border-radius: var(--sf-panel-radius);
  background: var(--ai-surface-2);
}
.rv-change-main {
  margin-bottom: 0;
}
.rv-change-annotation {
  margin-top: 8px;
  padding: 8px 10px;
  border-radius: var(--sf-control-radius);
  background: rgba(49,86,163,0.06);
  border-left: 2px solid rgba(49,86,163,0.3);
  font-size: 12px;
  line-height: 1.6;
  color: var(--ai-info);
}
.rv-result-diff {
  margin-top: 16px;
}
.rv-review-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.rv-review-id {
  font-size: 13px;
  font-weight: 800;
  color: var(--ai-ink-1);
}
.rv-review-meta {
  margin-top: 4px;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.rv-link-btn {
  border: none;
  background: transparent;
  color: var(--ai-info);
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
}
.rv-actions {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}
.rv-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 18px;
}
.rv-form-card {
  padding: 14px;
  border-radius: var(--sf-panel-radius);
  background: var(--ai-surface-2);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rv-form-title {
  font-size: 13px;
  font-weight: 800;
  color: var(--ai-ink-1);
}
.rv-label {
  font-size: 12px;
  font-weight: 700;
  color: var(--ai-ink-3);
}
.rv-input, .rv-textarea {
  width: 100%;
  border: 1px solid var(--ai-border);
  border-radius: var(--sf-control-radius);
  background: var(--ai-surface);
  padding: 8px 10px;
  font-size: 12px;
  color: var(--ai-ink-1);
}
.rv-textarea {
  min-height: 90px;
  resize: vertical;
}
.rv-preset-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
.rv-chip-btn {
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-2);
  border-radius: 999px;
  padding: 6px 10px;
  font-size: 12px;
  cursor: pointer;
}
.rv-chip-btn:hover {
  border-color: rgba(47,105,236,0.28);
  color: var(--ai-accent);
}
.rv-check {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--ai-ink-2);
}
.rv-warning-box {
  border: 1px solid rgba(190, 116, 0, 0.2);
  border-radius: var(--sf-control-radius);
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  font-size: 12px;
  line-height: 1.6;
  padding: 9px 10px;
}
.rv-reason-meter {
  text-align: right;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.rv-primary-btn, .rv-danger-btn {
  border: none;
  border-radius: var(--sf-control-radius);
  padding: 9px 12px;
  color: #fff;
  font-size: 12px;
  font-weight: 800;
  cursor: pointer;
}
.rv-primary-btn {
  background: var(--sf-brand-action);
  color: var(--sf-on-action);
}
.rv-danger-btn {
  background: var(--sf-danger-solid);
}
.rv-primary-btn:disabled, .rv-danger-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
@media (max-width: 960px) {
  .rv-hero, .rv-grid {
    display: grid;
    grid-template-columns: 1fr;
  }
  .rv-form-grid {
    grid-template-columns: 1fr;
  }
  .rv-summary-grid {
    grid-template-columns: 1fr;
  }
}
</style>
