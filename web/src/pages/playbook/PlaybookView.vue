<template>
  <div class="page-container">
    <a-spin :loading="loading" style="width: 100%">
      <div class="page-header page-detail-toolbar">
          <a-button @click="$router.push('/playbooks')"><icon-left /> 返回</a-button>
          <h2 class="page-title">{{ playbook.name }}</h2>
        <a-space>
          <a-button type="primary" @click="$router.push(`/playbook/${pbName}/edit`)"><icon-edit /> 编辑</a-button>
          <a-button v-if="userStore.isEngineer" @click="handlePublish" :loading="publishing">提交审核</a-button>
          <a-button v-if="userStore.isEngineer" @click="handleRun" :loading="running">执行</a-button>
        </a-space>
      </div>

      <a-card v-if="reviewState" class="page-section-card" style="margin-bottom: 16px" :loading="reviewLoading">
        <template #title>关联审核单</template>
        <div class="review-banner">
          <div class="review-meta">
            <div class="review-title">#{{ reviewState.id }} · {{ reviewState.skill_id }}</div>
            <div class="review-subtitle">
              提交人 {{ reviewState.submitter || '-' }} · 审核人 {{ reviewState.reviewer || '待分配' }} · 状态 {{ reviewState.status || '-' }}
            </div>
          </div>
          <a-space>
            <a-tag :color="reviewState.status === 'approved' ? 'green' : reviewState.status === 'rejected' ? 'red' : 'orange'">
              {{ reviewState.status || 'pending' }}
            </a-tag>
            <a-button
              v-if="canActReview"
              type="primary"
              size="small"
              :loading="reviewActionLoading"
              @click="handleApproveReview"
            >
              通过
            </a-button>
            <a-button
              v-if="canActReview"
              status="danger"
              size="small"
              :loading="reviewActionLoading"
              @click="handleRejectReview"
            >
              驳回
            </a-button>
          </a-space>
        </div>
        <div v-if="reviewState.diff_summary" class="review-copy">{{ reviewState.diff_summary }}</div>
        <div v-if="reviewState.reason" class="review-copy review-reason">说明：{{ reviewState.reason }}</div>
        <pre v-if="reviewState.diff_text" class="review-diff">{{ reviewState.diff_text }}</pre>
      </a-card>

      <a-row :gutter="16">
        <!-- 画布（只读模式） -->
        <a-col :span="16">
          <a-card class="page-section-card flow-card">
            <template #title>
              <div class="section-title-row">
                <span>流程图</span>
                <a-space size="small" wrap>
                  <a-tag size="small" color="arcoblue">只读拓扑</a-tag>
                  <a-tag size="small">{{ playbook.steps?.length || 0 }} 步骤</a-tag>
                </a-space>
              </div>
            </template>
            <div class="flow-summary">
              <div>
                <div class="flow-eyebrow">Visual Canvas</div>
                <div class="flow-copy">流程图样式与主站设计系统对齐，使用暖色纸面、深墨边框和统一的画布信息层。</div>
              </div>
              <div class="flow-summary-chips">
                <span class="flow-chip">触发 {{ playbook.trigger?.schedule || '手动触发' }}</span>
                <span class="flow-chip">部门 {{ playbook.department || '未归属' }}</span>
              </div>
            </div>
            <div class="flow-frame-shell">
              <iframe
                :src="`/playbook-editor/index.html?name=${pbName}&readonly=1`"
                class="flow-iframe"
              />
            </div>
          </a-card>

          <!-- Mermaid 流程图 -->
          <a-card class="page-section-card mermaid-card" style="margin-top: 16px" v-if="mermaidCode">
            <template #title>
              <div class="section-title-row">
                <span>Mermaid 流程图</span>
                <a-tag size="small" color="green">文本拓扑</a-tag>
              </div>
            </template>
            <div class="flow-summary mermaid-summary">
              <div>
                <div class="flow-eyebrow">Mermaid View</div>
                <div class="flow-copy">保留文本级拓扑视图，便于审核结构与条件分支是否一致。</div>
              </div>
            </div>
            <div class="mermaid-shell">
              <div ref="mermaidContainer" class="mermaid-container"></div>
            </div>
          </a-card>

          <!-- 步骤列表 -->
          <a-card title="步骤详情" class="page-section-card" style="margin-top: 16px">
            <a-timeline v-if="playbook.steps && playbook.steps.length">
              <a-timeline-item v-for="(step, i) in playbook.steps" :key="i">
                <div><strong>{{ step.id }}</strong> — {{ step.skill_id || step.name }}</div>
                <div v-if="stepDeps(step)" class="step-meta">依赖: {{ stepDeps(step) }}</div>
                <div v-if="step.timeout" class="step-meta">超时: {{ step.timeout }}s</div>
                <div v-if="step.on_failure && step.on_failure !== 'terminate'" class="step-meta">
                  失败策略: {{ step.on_failure }}
                </div>
                <div v-if="step.params_override && Object.keys(step.params_override).length" class="step-meta mono">
                  参数覆盖: {{ JSON.stringify(step.params_override) }}
                </div>
              </a-timeline-item>
            </a-timeline>
            <a-empty v-else description="暂无步骤" />
          </a-card>
        </a-col>

        <!-- 信息面板 -->
        <a-col :span="8">
          <a-card title="基本信息" class="page-section-card">
            <a-descriptions :column="1">
              <a-descriptions-item label="描述">{{ playbook.description || '-' }}</a-descriptions-item>
              <a-descriptions-item label="部门">{{ playbook.department || '-' }}</a-descriptions-item>
              <a-descriptions-item label="调度">{{ playbook.trigger?.schedule || '手动触发' }}</a-descriptions-item>
              <a-descriptions-item label="步骤数">{{ playbook.steps?.length || 0 }}</a-descriptions-item>
              <a-descriptions-item label="SLA">{{ playbook.sla_minutes ? `${playbook.sla_minutes} 分钟` : '-' }}</a-descriptions-item>
            </a-descriptions>
          </a-card>
        </a-col>
      </a-row>
    </a-spin>

    <a-modal
      v-model:visible="rejectDialogVisible"
      title="驳回审核"
      ok-text="确认驳回"
      cancel-text="取消"
      :ok-button-props="{ status: 'danger' }"
      :on-before-ok="handleConfirmReject"
    >
      <a-form :model="{}" layout="vertical">
        <a-form-item label="驳回原因" required>
          <a-textarea
            v-model="rejectReasonInput"
            placeholder="请说明驳回理由（会通知提交人）"
            :auto-size="{ minRows: 3, maxRows: 6 }"
          />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message, Modal } from '@arco-design/web-vue'
import { playbookApi as rawPlaybookApi, reviewApi as rawReviewApi } from '@/api'
import request from '@/api/request'
import { useUserStore } from '@/stores/user'
import { IconLeft, IconEdit } from '@arco-design/web-vue/es/icon'
import DOMPurify from 'dompurify'
import mermaid from 'mermaid'

const playbookApi: any = rawPlaybookApi
const reviewApi: any = rawReviewApi
const route: any = useRoute()
const router: any = useRouter()
const userStore = useUserStore()
const pbName: string = route.params.name

const loading = ref(false)
const running = ref(false)
const publishing = ref(false)
const playbook = ref<any>({})
const mermaidCode = ref('')
const mermaidContainer = ref<HTMLElement | null>(null)
const reviewState = ref<any | null>(null)
const reviewLoading = ref(false)
const reviewActionLoading = ref(false)
const rejectDialogVisible = ref(false)
const rejectReasonInput = ref('')

mermaid.initialize({
  startOnLoad: false,
  theme: 'base',
  themeVariables: {
    primaryColor: '#fff7ea',
    primaryBorderColor: '#243249',
    primaryTextColor: '#162033',
    secondaryColor: '#edf2ff',
    tertiaryColor: '#eef7f3',
    lineColor: '#8a96a8',
    textColor: '#162033',
    nodeBorder: '#243249',
    mainBkg: '#fff8ef',
    clusterBkg: '#f5ede0',
    clusterBorder: '#cfbea1',
    edgeLabelBackground: '#fffdf8',
    fontFamily: "system-ui, sans-serif",
    fontSize: '13px',
  },
  flowchart: {
    useMaxWidth: false,
    htmlLabels: false,
    curve: 'basis',
    padding: 24,
    nodeSpacing: 48,
    rankSpacing: 56,
  },
  securityLevel: 'loose',
})

const reviewId = computed(() => {
  const raw = route.query.review_id
  return typeof raw === 'string' ? raw : ''
})

const canActReview = computed(() => {
  const review = reviewState.value
  if (!review || review.status !== 'pending') return false
  if (userStore.isEngineer || userStore.isAdmin || userStore.canViewAll) return true
  return review.reviewer && review.reviewer === userStore.userInfo?.user_id
})

function stepDeps(step: any) {
  const deps = step.depends_on
  if (!deps) return ''
  if (typeof deps === 'string') return deps
  if (Array.isArray(deps)) {
    return deps.map(d => typeof d === 'string' ? d : d.step_id).join(', ')
  }
  return ''
}

async function loadPlaybook() {
  loading.value = true
  try {
    playbook.value = await playbookApi.get(pbName)
    loadMermaid()
  } finally { loading.value = false }
}

async function loadMermaid() {
  try {
    const res: any = await request.get(`/playbooks/${pbName}/mermaid`)
    mermaidCode.value = res.mermaid || ''
    if (mermaidCode.value) {
      nextTick(() => renderMermaid())
    }
  } catch { /* mermaid 是辅助功能，失败不阻塞 */ }
}

async function renderMermaid() {
  if (!mermaidContainer.value || !mermaidCode.value) return
  try {
    const id = 'playbook-mermaid-' + Date.now()
    const { svg } = await mermaid.render(id, mermaidCode.value)
    mermaidContainer.value.innerHTML = DOMPurify.sanitize(svg, {
      USE_PROFILES: { svg: true, svgFilters: true },
      ADD_TAGS: ['style'],
      ADD_ATTR: ['id', 'class', 'style', 'xmlns'],
    })
  } catch { /* ignore */ }
}

function handlePublish() {
  Modal.confirm({
    title: '确认提交审核',
    content: '提交后将生成审核单，审核通过后才会正式发布。',
    okText: '确认提交',
    cancelText: '取消',
    onOk: async () => {
      publishing.value = true
      try {
        const result = await playbookApi.publish(pbName)
        Message.success(`已提交审核 (ID: ${result.review_id})`)
        await router.replace({
          path: `/playbook/${pbName}`,
          query: { ...route.query, review_id: String(result.review_id) },
        })
      } catch (e: any) { Message.error(e._message || '提交失败') }
      finally { publishing.value = false }
    },
  })
}

async function handleRun() {
  running.value = true
  try {
    const result = await playbookApi.run(pbName, {})
    Message.success(`执行完成: ${result.status}, ${result.completed_steps}/${result.total_steps} 步骤完成`)
  } catch (e: any) { Message.error(e._message || '执行失败') }
  finally { running.value = false }
}

async function loadReview() {
  if (!reviewId.value) {
    reviewState.value = null
    return
  }
  reviewLoading.value = true
  try {
    reviewState.value = await reviewApi.get(reviewId.value)
  } catch (e: any) {
    reviewState.value = null
    Message.error(e?._message || '加载审核单失败')
  } finally {
    reviewLoading.value = false
  }
}

async function handleApproveReview() {
  if (!reviewState.value?.id) return
  reviewActionLoading.value = true
  try {
    await reviewApi.approve(String(reviewState.value.id), {})
    Message.success('审核已通过')
    await loadReview()
  } catch (e: any) {
    Message.error(e?._message || '审核通过失败')
  } finally {
    reviewActionLoading.value = false
  }
}

function handleRejectReview() {
  if (!reviewState.value?.id) return
  rejectReasonInput.value = ''
  rejectDialogVisible.value = true
}

async function handleConfirmReject(): Promise<boolean> {
  const reason = rejectReasonInput.value.trim()
  if (!reason) {
    Message.warning('请填写驳回原因')
    return false
  }
  if (!reviewState.value?.id) return true
  reviewActionLoading.value = true
  try {
    await reviewApi.reject(String(reviewState.value.id), { reason, reject_reason: 'logic' })
    Message.success('审核已驳回')
    await loadReview()
    return true
  } catch (e: any) {
    Message.error(e?._message || '审核驳回失败')
    return false
  } finally {
    reviewActionLoading.value = false
  }
}

watch(() => reviewId.value, () => { loadReview() }, { immediate: true })

onMounted(loadPlaybook)
</script>

<style scoped>
.section-title-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; width: 100%; }
.flow-card :deep(.arco-card-body),
.mermaid-card :deep(.arco-card-body) { display: flex; flex-direction: column; gap: 16px; }
.flow-summary { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.flow-eyebrow {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--ai-warn);
}
.flow-copy { margin-top: 6px; color: var(--ai-ink-2); font-weight: 600; line-height: 1.7; }
.flow-summary-chips { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.flow-chip {
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.78);
  border: 1px solid var(--ai-border);
  color: var(--ai-ink-2);
  font-size: 12px;
  font-weight: 700;
  box-shadow: 0 8px 18px rgba(61, 46, 28, 0.08);
}
.flow-frame-shell,
.mermaid-shell {
  position: relative;
  overflow: hidden;
  border-radius: 18px;
  border: 1px solid var(--ai-border);
  background:
    radial-gradient(circle at top left, rgba(198, 106, 20, 0.12), transparent 28%),
    radial-gradient(circle at bottom right, rgba(22, 93, 255, 0.09), transparent 28%),
    linear-gradient(180deg, rgba(255, 253, 248, 0.96), rgba(245, 238, 226, 0.94));
  box-shadow: 0 16px 36px rgba(61, 46, 28, 0.12);
}
.flow-frame-shell::before,
.mermaid-shell::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.18), transparent 18%);
}
.flow-iframe {
  position: relative;
  z-index: 1;
  width: 100%;
  height: min(620px, 60vh);
  border: none;
  display: block;
  background: transparent;
}
.mermaid-summary { padding-bottom: 0; }
.step-meta { font-size: 12px; color: var(--ai-ink-3); margin-top: 2px; font-weight: 600; }
.mono { font-family: monospace; }
.mermaid-container {
  min-height: 180px;
  overflow: auto;
  padding: 18px;
  border-radius: 18px;
  background:
    linear-gradient(180deg, rgba(255,255,255,0.46), rgba(255,255,255,0.18)),
    var(--ai-surface-2);
}
.mermaid-container :deep(svg) {
  max-width: none;
  min-width: 100%;
  height: auto;
}
.mermaid-container :deep(.node rect),
.mermaid-container :deep(.node polygon),
.mermaid-container :deep(.node path),
.mermaid-container :deep(.cluster rect) {
  filter: drop-shadow(0 10px 18px rgba(61, 46, 28, 0.12));
}
.mermaid-container :deep(.flowchart-link) {
  stroke: #8a96a8 !important;
  stroke-width: 1.8px !important;
}
.mermaid-container :deep(.marker) {
  fill: #8a96a8 !important;
  stroke: #8a96a8 !important;
}
.mermaid-container :deep(.edgeLabel .label) {
  background: #fffdf8 !important;
  border-radius: 999px !important;
  box-shadow: 0 8px 16px rgba(198, 106, 20, 0.1);
}
.mermaid-container :deep(.label text),
.mermaid-container :deep(.edgeLabel text) {
  font-weight: 700;
}
.review-banner { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.review-title { font-size: 14px; font-weight: 700; color: var(--ai-ink-1); }
.review-subtitle { margin-top: 4px; font-size: 12px; color: var(--ai-ink-3); }
.review-copy { margin-top: 12px; line-height: 1.7; color: var(--ai-ink-1); }
.review-reason { color: var(--ai-ink-2); }
.review-diff { margin-top: 12px; padding: 12px; border-radius: 10px; background: var(--ai-surface-2); overflow: auto; white-space: pre-wrap; font-size: 12px; }

@media (max-width: 1024px) {
  .flow-summary { flex-direction: column; }
  .flow-summary-chips { justify-content: flex-start; }
  .flow-iframe { height: 540px; }
}
</style>
